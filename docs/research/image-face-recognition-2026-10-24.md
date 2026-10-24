# Local face detection, recognition, and person clustering

Status: primary-source research and implementation recommendation, 2026-10-24. This is not a benchmark or legal opinion.

## Decision

Use an **off-by-default, local-only, owner-scoped** pipeline:

- OpenCV Zoo YuNet 2023mar for face detection and five landmarks.
- OpenCV Zoo SFace 2021dec for aligned 128-float embeddings.
- OpenCV's `FaceDetectorYN` and `FaceRecognizerSF` APIs on CPU, one job at a time.
- Lumina's pinned, size-capped, SHA-256-verified model catalog for SFace; reuse the already-bundled and verified YuNet.
- Conservative, reversible grouping. Machine-created people are unnamed suggestions. Manual naming, assignment, merge, split, hide, and deletion are durable corrections.

Do **not** redistribute InsightFace `buffalo_*` or `antelopev2` weights as Lumina's default. InsightFace code is MIT, but the project separately says its supplied pretrained models are for non-commercial research and directs users to obtain licensing. Immich says it received permission in 2023 and explicitly warns that its permission does not extend to third-party redistribution or commercial use. Open-source code is not evidence that weights have the same grant. [InsightFace license](https://github.com/deepinsight/insightface/blob/c6c3af7bd2d0a0bf4b78c78437357f3521aa5d52/README.md#license), [InsightFace model-zoo terms](https://github.com/deepinsight/insightface/blob/c6c3af7bd2d0a0bf4b78c78437357f3521aa5d52/python-package/docs/model_zoo.md#model-licenses), [Immich ML acknowledgement](https://github.com/immich-app/immich/blob/cf719e733e6250e0fcd8f45d2e2c46f166d3557d/machine-learning/README.md#license-and-use-restrictions)

This choice adds about 38.7 MB of model bytes and no new Python runtime. Lumina already locks `opencv-python-headless==4.12.0.88` and NumPy, and already ships YuNet for hero framing. [Lumina dependencies](../../backend/pyproject.toml), [bundled YuNet record](../../backend/app/data/hero_models/README.md)

## Model and runtime assessment

| Candidate | Verified terms | Payload / operational fit | Outcome |
| --- | --- | --- | --- |
| YuNet 2023mar | Its OpenCV Zoo directory says all files, including ONNX, are MIT | Actual upstream artifact: 232,589 bytes; SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`; already bundled | Use |
| SFace 2021dec | Its OpenCV Zoo directory says all files, including ONNX, are Apache-2.0 | Actual upstream artifact: 38,696,353 bytes; SHA-256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` | Use; preserve license/notice |
| InsightFace Python + public packs | Code MIT; supplied pretrained packs restricted to non-commercial research absent separate authorization | Technically attractive SCRFD/ArcFace family, but not a generally redistributable Lumina default | Reject as default; future bring-your-own licensed bundle only |
| ONNX Runtime CPU | Runtime MIT; it does not license model weights | 2026-10-24 PyPI snapshot: compressed CPython 3.12 Linux wheels about 21.4 MB arm64 / 23.8 MB x86-64 before unpacking | Defer; OpenCV already supplies the required high-level APIs |
| ONNX Runtime CUDA / OpenVINO | Runtime MIT; accelerator and driver terms separate | CUDA wheels about 212–256 MB; OpenVINO about 84 MB, before drivers and device plumbing | Optional deployment profile only after measured need |

Primary sources: [YuNet model and license](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet), [SFace model and license](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface), [ONNX Runtime MIT license](https://github.com/microsoft/onnxruntime/blob/36f07f760dccb74a96d0a3c851163d6448bdfd21/LICENSE), [CPU release](https://pypi.org/project/onnxruntime/1.31.0/), [CUDA release](https://pypi.org/project/onnxruntime-gpu/1.31.0/), [OpenVINO release](https://pypi.org/project/onnxruntime-openvino/1.24.1/).

The byte counts and hashes were computed from the actual Git-LFS media, not the small pointer files. The catalog pins revision, size, and digest. A moving `main` URL without a digest is not an acceptable model boundary.

### Why not add ONNX Runtime now?

ONNX Runtime is useful when several hardware targets justify it. Execution providers are explicitly ordered, so CUDA can fall back to CPU. It supports CUDA, TensorRT, OpenVINO, CoreML, and others. Adding it now would duplicate an installed inference runtime and require Lumina to own YuNet post-processing and SFace alignment instead of using OpenCV's reviewed face APIs. [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html#load-and-run-a-model), [execution providers](https://onnxruntime.ai/docs/execution-providers/)

Acceleration is not one portable Docker switch:

- CUDA needs a compatible CUDA/cuDNN stack and Docker GPU access.
- OpenVINO targets Intel CPU/GPU/NPU and has its own package and configuration. [OpenVINO provider](https://onnxruntime.ai/docs/execution-providers/OpenVINO-ExecutionProvider.html)
- CoreML is included in official macOS wheels, but a Linux container on a Mac does not thereby gain CoreML. [CoreML requirements](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html#requirements)
- OpenCV pip wheels are CPU-oriented; CUDA OpenCV is a separate build and release commitment.

The supported baseline should therefore be CPU. A later accelerator image must publish provider/operator coverage, CPU fallback, cold start, peak RSS, image-size cost, throughput, and output-consistency evidence.

## Accuracy evidence and its limits

OpenCV reports YuNet WIDER Face validation results and SFace verification accuracy of 99.60% on LFW, 93.95% CALFW, 91.05% CPLFW, 94.90% AgeDB-30, and 94.80% CFP-FP. YuNet's model card says its training scheme targets faces roughly 10×10 through 300×300 pixels. [OpenCV face tutorial](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html#models), [YuNet card](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/README.md), [SFace card](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/README.md)

Those scores do not establish household clustering quality:

- They are mainly one-to-one verification results at dataset-specific thresholds. A people page performs repeated one-to-many comparisons, increasing false-match opportunities.
- Household photos contain aging children, twins and siblings, profiles, masks, low light, scans, blur, collages, reflections, and faces on screens or posters.
- Detection AP is not end-to-end identity accuracy; missed or misaligned faces can dominate recognition.
- Published runtime claims do not predict a 4-vCPU/8-GiB shared Lumina container.
- NIST reports that false-positive and false-negative behavior varies by algorithm, photography, demographic group, and threshold. This is not an SFace evaluation, but it rules out treating a headline accuracy as universal. [NIST demographic summaries](https://pages.nist.gov/frvt/reports/demographics/nistir_8429.pdf), [NIST biometric error explanation](https://pages.nist.gov/800-63-4/sp800-63b.html#use-of-biometrics)

OpenCV publishes a cosine threshold of `0.363` for one LFW verification operating point. Lumina must not reuse it blindly for clustering. Tune a stricter threshold and runner-up margin on a sanitized, consented household-like set. Measure detection recall, false joins, duplicate groups, unassigned faces, and runtime separately. Optimize for precision: a duplicate that can be merged is safer than two people silently collapsed.

## Useful Immich architecture, without copying its topology

Immich documents this flow:

1. Its server sends a generated preview to its machine-learning service.
2. The service loads models, decodes and preprocesses the preview, detects faces, aligns crops, embeds them, and returns boxes, scores, and embeddings.
3. The server persists/indexes embeddings; server-side jobs perform clustering.
4. Its incremental algorithm is DBSCAN-derived: a core point needs enough nearby faces, non-core faces are deferred, outliers remain unassigned, and a later job revisits them.
5. Its UI supports naming, hiding, feature-photo choice, merges, and manual assignment. Its docs warn that permissive thresholds can produce results harder to repair than strict thresholds that create duplicates.

[Immich detection and clustering](https://github.com/immich-app/immich/blob/cf719e733e6250e0fcd8f45d2e2c46f166d3557d/docs/docs/features/facial-recognition.md), [service architecture](https://github.com/immich-app/immich/blob/cf719e733e6250e0fcd8f45d2e2c46f166d3557d/docs/docs/developer/architecture.mdx), [person job source](https://github.com/immich-app/immich/blob/cf719e733e6250e0fcd8f45d2e2c46f166d3557d/server/src/services/person.service.ts)

Lumina should borrow the generated-preview boundary, durable jobs, outliers, and correction tools. It should not add a second container for these two small models or rely on Immich's non-transferable InsightFace permission.

## Privacy and consent contract

- Default disabled. First enable requires a specific acknowledgement that processing is local, embeddings are biometric-derived data, grouping can be wrong, and pictured people may not have consented.
- Consent and identity namespace are per photo owner. A shared photo does not permit cross-member identity linking.
- Initial face boxes, people pages, labels, corrections, counts, and crops are owner-only. Shared viewers do not learn identities or the existence of faces from private photos.
- Skip trashed photos and unsupported/animated inputs. Do not scan videos in this phase.
- Turning the feature off stops admission. A separate delete action removes boxes, embeddings, groups, labels, edit receipts, and queued work for that owner.
- Never return embeddings through an API, log them, put them in URLs/browser state, or export them by default.
- Never use a match for authentication, authorization, moderation, or an adverse decision. It is an organizing suggestion.

The UK's regulator says biometric recognition used for unique identification is special-category processing; consent, where relied on, must be specific, informed, freely given, withdrawable, and have a real alternative. Lumina cannot claim an installer's toggle establishes a lawful basis for every pictured person or jurisdiction. [ICO biometric guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/biometric-data-guidance-biometric-recognition/how-do-we-process-biometric-data-lawfully/)

## Input and output trust boundary

Analyze Lumina-generated, correctly oriented 1,200-pixel WebP derivatives rather than arbitrary originals. This makes box coordinates agree with the UI and keeps hostile source parsing in the existing supervised derivative child.

Required checks:

- At most the derivative pipeline's encoded-byte and pixel limits.
- One CPU thread, OpenCL disabled, one inference globally. Native OpenCV and model tensors live in a per-photo supervised child with CPU, address-space, output, and active-runtime limits; a crash or hang cannot take down the API process.
- Detection score at least the configured threshold and at most 100 faces.
- Deterministic score/coordinate ordering and truncation.
- Finite detector values; boxes clipped then validated as `0 <= x1 < x2 <= 1`, `0 <= y1 < y2 <= 1`.
- Five-landmark alignment through `FaceRecognizerSF.alignCrop`.
- Exactly 128 finite floats, nonzero norm, L2 normalization, stored as a 512-byte little-endian float32 BLOB.
- Fixed public error codes; never log private filenames, storage paths, pixels, vectors, names, or model URLs.

Persist a complete pipeline signature containing detector and recognizer hashes plus preprocessing revision. Never compare embeddings across signatures. A model or alignment change creates a rebuild generation rather than silently mixing vector spaces.

## Durable jobs and grouping

One job is unique by `(photo_id, pipeline_signature)` and moves through `queued | running | succeeded | failed | canceled`. It snapshots the source SHA-256 when queued and again when claimed. Publication re-reads the photo in the write transaction; if an external/NAS file changed during inference, Lumina discards the stale output and requeues the new hash. The selected 1,200-pixel derivative must also have the hash-derived key for that snapshot. Queue creation keyset-pages at most 500 photo ids per transaction rather than materializing or locking the whole library. A restart requeues interrupted work unless its three-attempt bound is exhausted. Inference occurs in the supervised child outside a transaction; the parent validates all output; then one short transaction replaces that photo's face generation and completes the job. This matches [ADR 0006](../adr/0006-keep-single-process-sqlite-persistence.md).

Automatic post-ingest jobs run only in the configured maintenance window and when the activity gate is quiet. Explicit Enable, Scan, and retry actions are requested work: they may claim while the web UI is active and their supervised child is promoted through the existing interactive-work gate, so status polling cannot starve the user's request. Shutdown still cancels admission, and one worker plus `cv2.setNumThreads(1)` prevents multiplicative CPU concurrency. This follows [ADR 0020](../adr/0020-optional-media-backfills-yield-to-app-activity.md) and the durable admission intent of [ADR 0024](../adr/0024-household-resource-budgets.md).

Grouping rules:

1. Cosine similarity is the dot product of normalized embeddings.
2. An auto-created person needs a dense seed: at least three compatible faces from distinct photos.
3. Auto-attach only when the best person clears the calibrated threshold and beats the runner-up by a calibrated margin.
4. Ambiguous and singleton faces remain unassigned.
5. Faces from the same photo cannot auto-seed one identity.
6. Manual assignments, merges, and splits change assignment source to `manual`; reanalysis preserves a manual face via box overlap and automatic regrouping never moves it.
7. Merge leaves a redirect/tombstone. Split creates a new durable person and manual assignments, so the next clustering pass does not recreate the mistake.

Exact all-pairs DBSCAN is quadratic. The dependency-free first release may compare a bounded unassigned pool and existing exemplars, accepting more duplicates/outliers. If measured libraries require approximate nearest-neighbor search, add a separate ADR; SQLite remains authoritative and the index must be rebuildable.

## API and visibility

All routes authenticate. Face and people routes are owner-only even when a photo is shared:

```text
GET/PUT /api/photo-faces/settings
POST    /api/photo-faces/scan
POST    /api/photo-faces/retry
DELETE  /api/photo-faces/data

GET     /api/photo-people
POST    /api/photo-people
PATCH   /api/photo-people/{id}
POST    /api/photo-people/{source}/merge
POST    /api/photo-people/{id}/split

GET     /api/photos/{photo_id}/faces
POST    /api/photo-faces/{face_id}/assign
GET     /api/photo-faces/{face_id}/image
GET     /api/photos?person_id={id}
```

Person counts use distinct, live photos owned by the caller; no private-photo count leaks through a shared item. Boxes and detector confidence are diagnostic facts, not identity confidence. The API never reports “92% Alice.”

## Alignment with Lumina decisions

- [ADR 0013](../adr/0013-ai-output-is-untrusted-and-validated-against-library-data.md): every box, score, vector, and assignment is untrusted; numeric shape/range/finite validation precedes persistence; manual organization is the no-AI path.
- [ADR 0014](../adr/0014-on-device-models-as-supervised-child-processes.md): only pinned, hash-verified catalog models load. Face inference consumes derivatives produced by the photo child, then runs native OpenCV in its own supervised, resource-limited child so a native crash or hang cannot terminate the API.
- [ADR 0020](../adr/0020-optional-media-backfills-yield-to-app-activity.md): bulk face work is optional maintenance and yields to app activity.
- [ADR 0024](../adr/0024-household-resource-budgets.md): admission is durable, duplicate checks precede charging, and one bounded worker protects household resources.

## Evidence required before calling clustering tuned

- Public API tests for cross-owner/shared-photo privacy, consent, deletion, and embeddings never serialized.
- Model-unavailable, digest mismatch, corrupt image, non-finite output, invalid dimensions, restart recovery, retry exhaustion, and idempotent queue tests.
- Reanalysis preserving manual assignment; merge/split staying durable across a rebuild.
- A real-model smoke test with the pinned YuNet/SFace pair on Linux x86-64 and arm64 images.
- A consented evaluation set reporting false joins, duplicate groups, unassigned faces, detection misses, photos/minute, peak RSS, and interactive latency during backfill.

OpenCV YuNet/SFace is the only evaluated path that meets the present combination of local CPU operation, existing runtime, modest payload, clear terms on the actual weights, and single-image deployment. The main risk is not loading ONNX; it is turning probabilistic comparisons into hidden, irreversible identity claims. Lumina's contract must keep them private, optional, reversible, and visibly uncertain.

A 2026-10-24 macOS development smoke exercised the production catalog downloader, digest verification, and supervised child from a non-backend working directory. The pinned 38,696,353-byte SFace artifact and bundled YuNet processed [OpenCV Zoo's pinned `largest_selfie.jpg` example](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/example_outputs/largest_selfie.jpg) after conversion to the 1,200-pixel WebP boundary, returning 61 bounded 128-float embeddings. This proves the wired inference path, not accuracy, throughput, Linux-container behavior, or threshold quality; those release qualifications remain required above.
