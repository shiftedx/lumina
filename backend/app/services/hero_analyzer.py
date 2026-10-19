"""One bounded, offline backdrop analysis child. Native runtime memory dies with the child."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from app.services.raster_images import MAX_PIXELS, image_size

MAX_BYTES = 8 * 1024 * 1024
SUBJECT_SIDE = 416
SUBJECT_MIN_CONFIDENCE = .35
SUBJECT_REQUIRED_CONFIDENCE = .4
MAX_SUBJECTS = 8
MODELS = {
    "yunet.onnx": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "nanodet.onnx": "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186",
}


def analyze(data: bytes) -> dict:
    """Normalized subject regions, never image edits, identities, or network requests."""
    size = image_size(data)
    if not size or min(size) < 32 or max(size) > 16000 or size[0] * size[1] > MAX_PIXELS or len(data) > MAX_BYTES:
        raise ValueError("unsupported_image")
    root = Path(__file__).resolve().parents[1] / "data" / "hero_models"
    for name, checksum in MODELS.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("invalid_model")

    # Imported only in the supervised child: the API process never retains OpenCV or model tensors.
    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    flags = cv2.IMREAD_REDUCED_COLOR_2 if size[0] * size[1] > 2_000_000 else cv2.IMREAD_COLOR
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags)
    if image is None:
        raise ValueError("invalid_image")
    height, width = image.shape[:2]
    scale = min(640 / width, 640 / height, 1)
    small = cv2.resize(image, (max(32, round(width * scale)), max(32, round(height * scale))))
    detector = cv2.FaceDetectorYN.create(str(root / "yunet.onnx"), "", (small.shape[1], small.shape[0]), .75, .3, 100)
    _, faces = detector.detect(small)
    regions = []

    def region(x: float, y: float, w: float, h: float, kind: str, weight: float) -> dict:
        left, top = max(0., min(1., float(x))), max(0., min(1., float(y)))
        right, bottom = max(left, min(1., float(x + w))), max(top, min(1., float(y + h)))
        return {"x": left, "y": top, "width": right - left, "height": bottom - top, "kind": kind, "weight": float(weight)}

    if faces is not None:
        largest = max(float(face[2] * face[3]) for face in faces)
        for face in sorted(faces, key=lambda item: -float(item[2] * item[3]))[:8]:
            if float(face[2] * face[3]) >= largest * .15:
                regions.append(region(face[0] / small.shape[1], face[1] / small.shape[0], face[2] / small.shape[1],
                                      face[3] / small.shape[0], "face", face[-1]))

    # NanoDet's COCO classes are intentionally discarded.  Its well-localized boxes
    # are generic subject proposals only; Lumina does not identify artwork subjects.
    scale = min(SUBJECT_SIDE / width, SUBJECT_SIDE / height)
    resized_width, resized_height = round(width * scale), round(height * scale)
    left, top = (SUBJECT_SIDE - resized_width) // 2, (SUBJECT_SIDE - resized_height) // 2
    subject_input = cv2.copyMakeBorder(
        cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (resized_width, resized_height), interpolation=cv2.INTER_AREA),
        top, SUBJECT_SIDE - resized_height - top, left, SUBJECT_SIDE - resized_width - left, cv2.BORDER_CONSTANT, value=0,
    ).astype(np.float32)
    subject_input = (subject_input - np.array([103.53, 116.28, 123.675], dtype=np.float32)) / np.array([57.375, 57.12, 58.395], dtype=np.float32)
    network = cv2.dnn.readNetFromONNX(str(root / "nanodet.onnx"))
    network.setInput(cv2.dnn.blobFromImage(subject_input))
    outputs = network.forward(network.getUnconnectedOutLayersNames())
    classifications, regressions = outputs[::2], outputs[1::2]
    boxes, confidences = [], []
    for stride, classification, regression in zip((8, 16, 32, 64), classifications, regressions):
        classification, regression = classification.squeeze(0), regression.squeeze(0)
        axis_x, axis_y = np.meshgrid(np.arange(SUBJECT_SIDE // stride), np.arange(SUBJECT_SIDE // stride))
        anchors = np.column_stack((axis_x.ravel() * stride + .5 * (stride - 1), axis_y.ravel() * stride + .5 * (stride - 1)))
        raw_distances = np.clip(np.nan_to_num(regression.reshape(-1, 8).astype(np.float64), nan=-1e4, posinf=1e4, neginf=-1e4), -1e4, 1e4)
        distribution = np.exp(raw_distances - raw_distances.max(axis=1, keepdims=True))
        distribution /= distribution.sum(axis=1, keepdims=True)
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            distances = np.nan_to_num(distribution @ np.arange(8, dtype=np.float64).reshape(8, 1))
        distances = distances.reshape(-1, 4) * stride
        proposed = np.column_stack((anchors[:, 0] - distances[:, 0], anchors[:, 1] - distances[:, 1], anchors[:, 0] + distances[:, 2], anchors[:, 1] + distances[:, 3]))
        confidence = classification.max(axis=1)
        accepted = np.flatnonzero(confidence >= SUBJECT_MIN_CONFIDENCE)
        for index in accepted:
            x1, y1, x2, y2 = np.clip(proposed[index], 0, SUBJECT_SIDE)
            if (x2 - x1) * (y2 - y1) >= SUBJECT_SIDE * SUBJECT_SIDE * .01:
                boxes.append([float(x1), float(y1), float(x2 - x1), float(y2 - y1)])
                confidences.append(float(confidence[index]))
    if boxes:
        selected = cv2.dnn.NMSBoxes(boxes, confidences, SUBJECT_MIN_CONFIDENCE, .5)
        proposed_subjects = sorted(((boxes[int(index)], confidences[int(index)]) for index in selected), key=lambda item: -item[1])
        strongest = proposed_subjects[0][1] if proposed_subjects else 0.
        kept: list[tuple[list[float], float]] = []
        for box, confidence in proposed_subjects:
            if confidence < max(SUBJECT_MIN_CONFIDENCE, strongest * .55) or len(kept) >= MAX_SUBJECTS:
                continue
            x, y, box_width, box_height = box
            area = box_width * box_height
            # Boxes entirely inside a materially larger proposal add no framing information.
            if any(x >= outer[0] and y >= outer[1] and x + box_width <= outer[0] + outer[2] and y + box_height <= outer[1] + outer[3] and area < outer[2] * outer[3] * .6 for outer, _ in kept):
                continue
            kept.append((box, confidence))
        if strongest >= SUBJECT_REQUIRED_CONFIDENCE:
            for (x, y, box_width, box_height), confidence in kept:
                regions.append(region((x - left) / resized_width, (y - top) / resized_height, box_width / resized_width, box_height / resized_height, "subject", confidence))
    return {"state": "ready", "width": size[0], "height": size[1], "regions": regions}


def main() -> None:
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    try:
        result = analyze(sys.stdin.buffer.read(MAX_BYTES + 1))
    except Exception:
        raise SystemExit(1) from None
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
