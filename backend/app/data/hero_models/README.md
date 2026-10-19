# Backdrop analysis models

These small, CPU-only models ship with Lumina for offline face and generic subject-region proposals. They do not identify people, retain model classes, or alter the source artwork. The supervised analyzer verifies their SHA-256 before loading either file. There are no runtime downloads.

| File | Upstream artifact | SHA-256 | License |
| --- | --- | --- | --- |
| `yunet.onnx` | [OpenCV Zoo YuNet 2023mar](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), original `face_detection_yunet_2023mar.onnx` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | MIT, Shiqi Yu; `YUNET-LICENSE` |
| `nanodet.onnx` | [OpenCV Zoo NanoDet 2022nov](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet), original `object_detection_nanodet_2022nov.onnx` | `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186` | Apache-2.0; `NANODET-LICENSE` |

The combined model payload is approximately 4.0 MB. To upgrade, replace the reviewed artifact and license, update the analyzer's digest and analysis cache version, and rerun the focused inference/framing checks. Region detections are suggestions: uncertain or over-broad results must retain conservative full-bleed framing rather than trust a detected label.
