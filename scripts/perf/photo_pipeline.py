"""Opt-in real photo-ingestion benchmark; all data stays in disposable local folders.

Generate deterministic HEIC fixtures, then benchmark a selected checkout's actual
NAS-source and face workers. No household files, HTTP mocks, or fake ML results.
Use the same corpus/interpreter for baseline and optimized checkouts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ASTRONAUT_URL = "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png"
ASTRONAUT_SHA256 = "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def generate(folder: Path, count: int, repo: Path) -> None:
    import numpy as np
    from PIL import Image
    from pillow_heif import register_heif_opener

    if folder.exists():
        raise SystemExit("Refusing to replace an existing corpus directory")
    folder.mkdir(parents=True)
    register_heif_opener()
    asset = folder / "astronaut.png"
    urllib.request.urlretrieve(ASTRONAUT_URL, asset)
    if digest(asset) != ASTRONAUT_SHA256:
        raise RuntimeError("Public-domain fixture digest mismatch")
    portrait = Image.open(asset).convert("RGB")
    originals = folder / "originals"
    originals.mkdir()
    templates = []
    rng = np.random.default_rng(20261024)
    # Detailed synthetic backgrounds exercise HEIC compression/decode. The NASA
    # portrait supplies actual detections/embeddings; it is not an accuracy corpus.
    for index, (width, height) in enumerate(((4032, 3024), (3024, 4032), (6000, 4000), (4000, 6000))):
        pixels = rng.integers(24, 232, (height, width, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
        side = min(width, height) * 2 // 3
        head = portrait.resize((side, side), Image.Resampling.LANCZOS)
        image.paste(head, ((width - side) // 2, (height - side) // 2))
        path = folder / f"template-{index}.heic"
        image.save(path, format="HEIF", quality=65 if width * height < 20_000_000 else 45)
        templates.append(path.read_bytes())
    entries = []
    for index in range(count):
        path = originals / f"fixture-{index:04d}.heic"
        # ISO BMFF free boxes make source hashes distinct without claiming the
        # repeated four decoded scenes form a diverse household corpus.
        payload = f"lumina synthetic fixture {index:08d}".encode()
        path.write_bytes(templates[index % len(templates)] + struct.pack(">I4s", 8 + len(payload), b"free") + payload)
        entries.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)})
    for path in folder.glob("template-*.heic"):
        path.unlink()
    models = json.loads((repo / "backend/app/model_catalog.json").read_text())["models"]
    model = next(model for model in models if model["role"] == "face" and model.get("default"))
    weights = folder / "models" / model["id"]
    weights.mkdir(parents=True)
    for record in model["files"]:
        path = weights / record["name"]
        urllib.request.urlretrieve(record["url"], path)
        if path.stat().st_size != record["size"] or digest(path) != record["sha256"]:
            raise RuntimeError("Pinned model verification failed")
    (weights / ".ready").write_text(json.dumps({record["name"]: record["sha256"] for record in model["files"]}))
    (folder / "manifest.json").write_text(json.dumps({
        "fixture": "four synthetic 12/24 MP HEIC scenes with public-domain NASA astronaut",
        "face_source": ASTRONAUT_URL, "face_source_sha256": ASTRONAUT_SHA256,
        "rights": "https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.astronaut",
        "quality": "65 for 12MP, 45 for 24MP synthetic noise", "files": entries,
    }, indent=2) + "\n")
    print(json.dumps({"files": count, "min_bytes": min(row["bytes"] for row in entries), "max_bytes": max(row["bytes"] for row in entries)}))


def trial(repo: Path, corpus: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="lumina-photo-pipeline-") as temporary:
        data = Path(temporary).resolve()
        os.environ["LUMINA_DATA_DIR"] = str(data)
        sys.path.insert(0, str(repo / "backend"))
        from sqlalchemy import func, inspect as inspect_sql, select, text
        from app.db import init_db, session_scope, engine
        from app.models import StorageRoot, User, utcnow
        from app.photo_face_models import PhotoFace, PhotoFaceJob, PhotoFacePreference, PhotoPerson
        from app.photo_models import PhotoDerivative, PhotoItem
        from app.photo_root_models import PhotoImportRun
        from app.services import photo_derivatives, photo_faces, photo_imports
        from app.services.background_work import background_work
        from app.services.storage_roots import StorageRootService

        manifest = json.loads((corpus / "manifest.json").read_text())
        for row in manifest["files"]:
            if digest(corpus / "originals" / row["name"]) != row["sha256"]:
                raise RuntimeError("Corpus changed")
        shutil.copytree(corpus / "models", data / "models")
        init_db()
        with session_scope() as db:
            owner = User(id="pipeline-benchmark", username="pipeline-benchmark", display_name="Synthetic benchmark", role="admin", is_active=True)
            db.add(owner)
            root = StorageRoot(id="pipeline-source", label="Synthetic local source", path=str((corpus / "originals").resolve()), mode="external", enabled=True)
            db.add(root)
            StorageRootService(db).probe(root)
            db.flush()
            source = photo_imports.PhotoImportService(db).create(root.id, "", owner, "private")
            source_id = source.id
        starts: dict[str, int] = {}
        counter_lock = threading.Lock()
        original_popen = subprocess.Popen
        def counted_popen(argv, *args, **kwargs):
            if isinstance(argv, list) and len(argv) > 3 and argv[1] == "-m":
                key = f"{argv[2]}:{argv[3]}"
                with counter_lock:
                    starts[key] = starts.get(key, 0) + 1
            return original_popen(argv, *args, **kwargs)
        subprocess.Popen = counted_popen
        background_work.start()
        importer = photo_imports.PhotoImportWorker()
        face_times = []
        started = time.perf_counter()
        try:
            if not importer.run_once():
                raise RuntimeError("Import did not run")
            imported = time.perf_counter()
            with session_scope() as db:
                run = db.scalar(select(PhotoImportRun).where(PhotoImportRun.source_id == source_id))
                if run.state != "succeeded" or run.error_count:
                    raise RuntimeError(f"Source failed: {run.state}/{run.error_count}")
                db.add(PhotoFacePreference(user_id=owner.id, enabled=True, auto_group=True, consented_at=utcnow()))
            with session_scope() as db:
                photo_faces.queue_all(db, db.get(User, owner.id), requested=True)
            while True:
                one = time.perf_counter()
                if not photo_faces.worker.run_once():
                    break
                face_times.append(time.perf_counter() - one)
            with session_scope() as db:
                counts = {
                    "photos": db.scalar(select(func.count()).select_from(PhotoItem)),
                    "ready_renditions": db.scalar(select(func.count()).select_from(PhotoDerivative).where(PhotoDerivative.state == "ready")),
                    "face_jobs_succeeded": db.scalar(select(func.count()).select_from(PhotoFaceJob).where(PhotoFaceJob.state == "succeeded")),
                    "face_jobs_remaining": db.scalar(select(func.count()).select_from(PhotoFaceJob).where(PhotoFaceJob.state.in_(("queued", "running")))),
                    "face_jobs_failed": db.scalar(select(func.count()).select_from(PhotoFaceJob).where(PhotoFaceJob.state == "failed")),
                    "faces": db.scalar(select(func.count()).select_from(PhotoFace)),
                    "people": db.scalar(select(func.count()).select_from(PhotoPerson)),
                }
                if inspect_sql(engine).has_table("photo_preview_assets"):
                    counts["preview_publication_pins"] = db.scalar(text(
                        "SELECT count(*) FROM photo_preview_assets WHERE reservation_id IS NOT NULL"
                    ))
                    counts["preview_reserved_bytes"] = db.scalar(text(
                        "SELECT coalesce(sum(reserved_bytes),0) FROM photo_preview_storage"
                    ))
                    counts["index_uncheckpointed_changes"] = db.scalar(text("""
                        SELECT count(*) FROM photo_face_index_changes c
                        WHERE c.id > coalesce((SELECT s.watermark FROM photo_face_index_snapshots s
                          WHERE s.owner_user_id=:owner AND s.pipeline_signature=:pipeline),0)
                          AND ((c.old_owner_user_id=:owner AND c.old_pipeline_signature=:pipeline)
                            OR (c.new_owner_user_id=:owner AND c.new_pipeline_signature=:pipeline))
                    """), {"owner": owner.id, "pipeline": photo_faces.PIPELINE_SIGNATURE})
                    if any(counts[key] for key in (
                        "preview_publication_pins", "preview_reserved_bytes", "index_uncheckpointed_changes",
                    )):
                        raise RuntimeError(f"Derived storage did not fully settle: {counts}")
                artifacts = list(db.scalars(select(PhotoDerivative)))
                if not all(photo_derivatives.derivative_path(row.source_key).is_file() for row in artifacts):
                    raise RuntimeError("A promised rendition is missing")
                rendition_digests = {row.source_key: digest(photo_derivatives.derivative_path(row.source_key)) for row in artifacts}
            finished = time.perf_counter()
            count = len(manifest["files"])
            if counts["photos"] != count or counts["ready_renditions"] != count * 3 or counts["face_jobs_succeeded"] != count or counts["face_jobs_remaining"] or counts["face_jobs_failed"] or counts["faces"] < count or not counts["people"]:
                raise RuntimeError(f"Pipeline did not fully settle with real faces: {counts}")
            return {"seconds": round(finished - started, 4), "ingestion_seconds": round(imported - started, 4),
                    "face_and_completion_seconds": round(finished - imported, 4), "photos_per_second": round(count / (finished - started), 4),
                    "first_face_job_seconds": round(face_times[0], 4), "subsequent_face_job_median_seconds": round(statistics.median(face_times[1:]), 4),
                    "child_process_starts": starts, "counts": counts,
                    "rendition_bytes": sum(photo_derivatives.derivative_path(row.source_key).stat().st_size for row in artifacts),
                    "rendition_sha256": rendition_digests}
        finally:
            importer.close()
            photo_faces.worker.close()
            background_work.close()
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline-root", type=Path, help="Alternate paired trials against this checkout")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--one-trial", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo, corpus = args.repo_root.resolve(), args.corpus.resolve()
    if args.generate:
        if not 4 <= args.count <= 1000:
            parser.error("count must be 4–1000")
        generate(corpus, args.count, repo)
    elif args.one_trial:
        print(json.dumps(trial(repo, corpus)))
    else:
        if not 1 <= args.trials <= 10:
            parser.error("trials must be 1–10")
        roots = {"optimized": repo}
        if args.baseline_root:
            roots = {"baseline": args.baseline_root.resolve(), **roots}
        observations: dict[str, list[dict]] = {label: [] for label in roots}
        order = []
        for ordinal in range(args.trials):
            labels = list(roots) if ordinal % 2 == 0 else list(reversed(roots))
            for label in labels:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--repo-root", str(roots[label]), "--corpus", str(corpus), "--one-trial"], capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError(f"{label} trial failed: {result.stderr}")
                row = json.loads(result.stdout.splitlines()[-1])
                observations[label].append(row)
                order.append(label)
                print(f"{label} trial {ordinal + 1}: {row['seconds']}s", file=sys.stderr, flush=True)
        results = {label: {"trials": runs,
                          "median_seconds": round(statistics.median(row["seconds"] for row in runs), 4),
                          "median_photos_per_second": round(statistics.median(row["photos_per_second"] for row in runs), 4)}
                   for label, runs in observations.items()}
        report = {"fixture": json.loads((corpus / "manifest.json").read_text()), "results": results, "execution_order": order,
                  "limitations": "Local SSD, warm OS caches, four repeated synthetic scenes, 12/24MP HEIC, real single-face inference/grouping. Not an iPhone accuracy corpus, real NAS, Linux qualification, or million-face grouping benchmark. Downloads and fixture encoding excluded."}
        if args.baseline_root:
            reference = observations["baseline"][0]["rendition_sha256"]
            if not all(row["rendition_sha256"] == reference for runs in observations.values() for row in runs):
                raise RuntimeError("Baseline/optimized rendition bytes differ")
            report["identical_rendition_bytes"] = True
            report["speedup"] = round(results["baseline"]["median_seconds"] / results["optimized"]["median_seconds"], 3)
        encoded = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded)
        else:
            print(encoded)


if __name__ == "__main__":
    main()
