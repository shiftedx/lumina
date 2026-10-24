# Durable photo previews and million-face indexing

The photo-scale branch changes preview retention and removes the recency ceiling
from automatic face matching. It does not accelerate HEIC decoding or face-model
inference. Originals and their durable previews still require sufficient storage.

## What changed

- Preview storage reserves capacity before decoding and retains catalog-backed
  renditions across pressure and restart. Folder scans commit their prepared
  prefix and resume from the committed cursor when capacity returns. Uploads
  report a retryable 503 instead of marking valid images corrupt.
- A persisted keyset cursor bounds each preview cleanup pass to 256 assets.
  Publication and restart do not walk the derivative directory tree.
- FAISS HNSW searches all indexed people and automatic outliers, using separate
  populations to prevent a prolific person from hiding competing identities.
  SQLite rechecks and exact float32 scoring protect current ownership, live
  photo state and manual corrections.
- Transactional vector versions and a change journal support incremental updates.
  Private checksummed checkpoints accelerate restart; invalid checkpoints rebuild
  from SQLite. Idle settling captures the final committed face, and erasure is
  serialized with checkpoint publication.

## Reproduce

The previous implementation is main commit `944fdbe12cf3b324aff0afb41b6cd8e6582f6e28`.
A local baseline can be created without checking out the shared working tree:

```sh
mkdir -p output/photo-scale-baseline
git archive 944fdbe backend scripts/perf/photo_pipeline.py | tar -x -C output/photo-scale-baseline
```

Run the preview, face-index and paired real-pipeline harnesses documented in
`scripts/perf/photo_preview_storage.py`, `scripts/perf/photo_scale.py`, and
`scripts/perf/photo_pipeline.py`. They use synthetic data and disposable storage.
The HEIC fixture also requires the existing public-domain image/model downloads;
its manifest records the exact fixture and rendition hashes.

## Interpretation

These are separate measurements. A million-row registry benchmark measures
accounting and reclamation, not writing millions of full-size files. Synthetic
face vectors qualify indexing, recall against exact vector search and restart,
not household recognition accuracy. The real HEIC pipeline measures a small
local-SSD workload with 16 generated 12/24 MP images, not a million-original NAS
import. There is no measured million-original completion-time guarantee here.

## Measurements

Measured on the shared Apple Silicon development host (64 GB RAM), using fixed
synthetic fixtures. Raw observations and caveats are retained in the linked files.

| Measurement | Result |
| --- | --- |
| Three-million-asset preview registry: reserve / publish / release medians | 0.723 / 1.069 / 1.185 ms |
| Registry restart recovery | 184.103 ms median; no derivative-tree enumeration |
| Indexed cleanup | 256 assets examined; 32.742 ms median |
| Referenced previews missing after pressure/restart | Previous LRU: 500/500; durable storage: 0/500, with retryable capacity rejection |
| Archived matching service, million-face fixture, legacy face indexes | 2,524.237 ms median over three trials |
| Full-history candidate service, million-face fixture | 2.382 / 2.649 ms p50 / p95 over 64 queries |
| Million-face graph build and durable checkpoint | 167.355 s, excluding fixture seeding and model inference |
| Checkpoint restart | 4.401 s |
| Exact top-32 recall for 64 synthetic old-person queries | 1.0 before and after restart; prior recent-only candidate coverage: 0.0 |
| Process high-water RSS in service-only benchmark | 3,473,653,760 bytes; includes construction/checkpoint buffers |

The archived candidate trial runs the prior service on a private consistent
backup of the same fixture, with the two new photo-face indexes removed. It
includes SQL, ORM materialization, stacking, float32 scoring and sorting. The
approximately thousandfold candidate-time difference is specific to this
all-unassigned synthetic workload. The methods have different coverage: the
prior query returns only 4,096 recent outliers, whereas the new index searches
complete history. This is not an end-to-end photo-import speedup.

The million-face service result records its exact source hash. The final change
adds only cache-budget accounting to that measured service version; the report
explains the verified hash reconstruction and the single-owner condition.
Current-source prototype qualification and multi-owner regressions cover the
additional paths separately.

- [Preview report and reproduction](photo-preview-storage-2026-10-24.md)
- [Three-million-row preview observations](photo-preview-storage-3m-2026-10-24.json)
- [Face-index report and reproduction](photo-face-index-2026-10-24.md)
- [Million-face observations](measurements/photo-face-index-1m-2026-10-24.json)
- [Archived candidate observations](measurements/photo-face-legacy-candidates-1m-2026-10-24.json)
- [Complete resident-vector exact-search reference](measurements/photo-face-index-resident-exact-1m-2026-10-24.json)

## Complete HEIC pipeline

Three alternating paired trials against `944fdbe` used the same 16 generated
12/24 MP HEICs and the real metadata/rendition/model/grouping pipeline. Median
completion was **18.0148 s before and 17.9779 s after** (1.002×, effectively
unchanged at this scale). All 48 rendition hashes matched exactly. Every run
finished with 16 successful face jobs, no queued/running/failed jobs, 16 detected
faces and one person. The final implementation additionally verifies zero
publication pins, zero reserved preview bytes and zero uncheckpointed index
changes after idle settling.

The change removes scale and retention costs; it does not claim faster native
HEIC decoding. These small-batch timings must not be projected as a measured
million-original completion time.

[Paired observations](measurements/photo-scale-pipeline-2026-10-24.json) retain every
trial, fixture dimensions/sizes and rendition digests.

## Verification contracts

Parallel import tests use file-backed SQLite connections, matching production;
a single in-memory `StaticPool` connection is not safe for concurrent sessions.
The gallery pagination test waits for observer registration before simulating
intersection. Home's image budget checks the actual viewport plus one vertical
viewport and two horizontal cards, retaining its concurrency, response-size and
no-duplicate-transfer limits. A count proportional to currently visible images
cannot represent that spatial budget when an empty shelf disappears and nearby
rails have different shapes and densities.
