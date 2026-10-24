# Million-photo ingestion: getting all work to completion

Status: primary-source research, 2026-10-24.

Scope: roughly 1,000,000 iPhone HEIC photos averaging 4–12 MB, or roughly
4–12 TB of source bytes before filesystem and derivative overhead. “Complete” in
this note means that every discovered file has an accountable terminal result for
metadata, all configured 400/1200/2400 renditions, face detection and embedding,
and face grouping. A gallery becoming browseable while those queues continue is a
useful intermediate milestone, but is not completion.

## Decision summary

The most defensible sequence for Lumina is:

1. define durable, source-versioned stage completion and retain every promised
   rendition;
2. fuse the existing Pillow inspect and render paths so a clean import decodes each
   original once, not twice;
3. keep YuNet and SFace resident in the existing supervised-process architecture;
4. feed the global decoder limit from a bounded per-file queue, so one large NAS
   source can use the available two decode slots;
5. keep the database, queue state, models, and active derivative writes on local
   storage while originals can remain on the NAS;
6. fix face-candidate SQL/indexing and design a rebuildable vector/grouping layer
   before considering more inference workers or a GPU; and
7. measure the complete pipeline on representative HEIC files before choosing a
   decoder, preview codec, provider, or completion-date estimate.

These are architecture conclusions, not speedup promises. In particular, the
earlier 6–23 day figure came from an arbitrary 0.5–2 photos/second assumption.
There is no measured Lumina HEIC throughput, so this report does not preserve that
figure as a forecast.

## Release boundary and evidence policy

This report uses current official documentation plus source pinned to releases:

- Immich v3.3.5, released 2026-10-21
  ([release](https://github.com/immich-app/immich/releases/tag/v3.3.5));
- PhotoPrism `261007-65faaae5d`, released 2026-10-07
  ([release](https://github.com/photoprism/photoprism/releases/tag/261007-65faaae5d));
  and
- LibrePhotos 1.4.0, released 2026-10-17
  ([release](https://github.com/LibrePhotos/librephotos/releases/tag/1.4.0)).

No recommendation depends on a main/develop-only implementation. When a project’s
website is rolling documentation, the source link beside the material claim points
to a released tag. Claims about these products are descriptions of their shipped
architecture, not benchmark evidence for Lumina.

## What the released systems do

### Comparison at the full-completion boundary

| Concern | Immich v3.3.5 | PhotoPrism `261007-65faaae5d` | LibrePhotos 1.4.0 | Transferable lesson |
| --- | --- | --- | --- | --- |
| Work graph and recovery | Redis/BullMQ queues; successful thumbnail work enqueues smart search, face detection, OCR, and video work; queue state exposes active/completed/failed/delayed/waiting/paused counts. | An indexing run feeds worker goroutines; a normal rescan skips unchanged indexed files. I found no released primary source describing a durable per-file, per-stage queue equivalent to BullMQ. | Django-Q2 ORM broker plus durable `LongRunningJob` rows; queue failure, progress, cancellation, terminal state, stuck-job cleanup, and chained follow-up jobs are represented in the database. | Lumina can keep SQLite, but completion must be a durable predicate over versioned stages rather than an in-memory loop ending. |
| Decode/preview reuse | A generated preview is the input to face detection; thumbnail completion is the predecessor of downstream ML. | Static thumbnails are pregenerated and retained; a 720px thumbnail is used for classification/face work. | One original decode produces a large WebP and smaller renditions; the large normalized rendition is reused by pHash and ML. | Decode the untrusted HEIC once per source generation, persist every promised rendition, and hand a bounded derivative to ML. |
| Resident ML and execution | ONNX models are cached, can be preloaded, have a configurable idle TTL, and use selectable execution providers. Face recognition has a configurable small batch limit. | Released ONNX detector/embedder sessions are created once and reused. CPU/CUDA providers and thread counts are configurable; the shared embedder serializes `Run` with a mutex. | Long-lived supervised Flask sidecars load models on demand and can unload them when idle; provider selection prefers CUDA then CPU. Face detection returns boxes and embeddings from one request. | Keep model ownership out of the per-photo path. Scale providers, workers, and batches only from measured queue/service time and memory. |
| Face indexing/grouping | Face embeddings are indexed in PostgreSQL/VectorChord. The clusterer is incremental/DBSCAN-derived, uses small nearest-neighbor searches, and revisits unassigned faces. | Face recognition starts after the library scan is complete and is optimized in later background passes. | Face clustering loads all embeddings and fits HDBSCAN; semantic retrieval uses a persisted exact `IndexFlatIP` FAISS index. | A named vector dependency is not enough. At Lumina’s scale, candidate completeness, index maintenance, memory, and resumability need explicit designs. |
| Storage | PostgreSQL should be on local SSD, not a network share; thumbnail storage can be separated from original storage. | Database/cache paths are separable; local SSD is recommended for the database; static thumbnails avoid repeated on-demand CPU work. | The job table and broker live in the database; normalized derivatives are reused by later stages. | Keep coordination and hot write-heavy data local. NAS source access should be sequential/bounded, and configured outputs need capacity-backed retention. |

The cells above are supported in detail below.

### Immich: a durable job DAG, resident batched models, and a real vector index

Immich documents a dependency chain in which upload work leads through metadata and
thumbnail generation, then fans out to smart search, face detection, OCR, and video
jobs; face recognition follows face detection. Its v3.3.5 service source enqueues
those downstream jobs only after `AssetGenerateThumbnails` completes
([job documentation](https://docs.immich.app/administration/jobs-workers/),
[v3.3.5 `job.service.ts`](https://github.com/immich-app/immich/blob/v3.3.5/server/src/services/job.service.ts)).
The released repository creates BullMQ workers per queue with configurable
concurrency, bulk enqueue support, and persisted queue counts for waiting, active,
completed, failed, delayed, and paused states
([v3.3.5 `job.repository.ts`](https://github.com/immich-app/immich/blob/v3.3.5/server/src/repositories/job.repository.ts)).
The scaling guide says jobs wait if an appropriate worker is absent and that shared
state is held in PostgreSQL, Redis, and the filesystem
([scaling guide](https://docs.immich.app/guides/scaling-immich/)). This supports
restartable separation of scheduling from execution; it is not a claim of
exactly-once execution.

The machine-learning service holds instantiated models in a cache and removes idle
entries by TTL; preloaded entries remain resident
([v3.3.5 model cache](https://github.com/immich-app/immich/blob/v3.3.5/machine-learning/immich_ml/models/cache.py)).
The released configuration defaults to one ML worker, exposes inter/intra-op thread
counts and preload options, and sets a face-recognition maximum batch size of four
faces—not four source photos
([v3.3.5 ML environment variables](https://github.com/immich-app/immich/blob/v3.3.5/docs/docs/install/environment-variables.md),
[v3.3.5 ML configuration](https://github.com/immich-app/immich/blob/v3.3.5/machine-learning/immich_ml/config.py)).
Immich ships experimental ONNX Runtime provider choices including CUDA, ROCm,
OpenVINO, ARM NN, and RKNN. Its documentation also warns that each additional ML
worker has its own model memory and that multiple GPUs require multiple workers
([hardware acceleration](https://docs.immich.app/features/ml-hardware-acceleration/)).
Those facts justify provider/batch experiments after profiling; they do not show
that a GPU will shorten Lumina’s end-to-end completion time.

For faces, Immich sends the generated preview to detection, stores embeddings in a
database vector index, performs incremental DBSCAN-derived clustering using small
nearest-neighbor queries, and revisits unassigned faces nightly. It also notes that
upload/processing order can affect incremental grouping quality and that a full
rerun produces the best baseline grouping
([facial-recognition design](https://docs.immich.app/features/facial-recognition/)).
Its supported PostgreSQL setup uses `face_index` and `clip_index` vector indexes;
current documentation recommends VectorChord rather than the older pgvecto.rs
([PostgreSQL/vector extension guide](https://docs.immich.app/administration/postgres-standalone/)).
This is the clearest comparator for Lumina’s fixed candidate bounds: the important
property is an indexed neighbor query with an explicit regrouping path, not the
specific PostgreSQL extension.

Immich’s job-concurrency setting is per task. Its documentation says more
concurrency raises throughput rather than single-job latency, warns against setting
thumbnail concurrency above available cores, and fixes facial-recognition
clustering concurrency because that DBSCAN implementation is sequential
([system settings](https://docs.immich.app/administration/system-settings/)). This
is evidence for separate resource budgets, not “turn every worker count up.”

Storage guidance keeps PostgreSQL on local SSD and explicitly allows thumbnail
storage to be located separately from originals
([requirements](https://docs.immich.app/install/requirements/),
[custom locations](https://docs.immich.app/guides/custom-locations/)). Immich treats
thumbnail and preview directories as regenerated data for backup purposes, but
omitting them means rerunning the associated jobs
([backup and restore](https://docs.immich.app/administration/backup-and-restore/)).
For a one-million-photo settle, “rebuildable” must not be confused with “free to
evict before completion.”

### PhotoPrism: host-sized workers and shared resident ONNX sessions

PhotoPrism derives its default index-worker limit from available CPU and exposes
separate detector/embedder thread counts, CPU or CUDA ONNX providers, static
thumbnail sizing, and filesystem locations for storage/cache
([configuration reference](https://docs.photoprism.app/getting-started/config-options/)).
In the released tag, the face engine creates its ONNX session once and reuses it
([released `engine_onnx.go`](https://github.com/photoprism/photoprism/blob/261007-65faaae5d/internal/ai/face/engine_onnx.go)).
Indexing workers share one embedder; a mutex serializes calls into that session
([released `embedder_onnx.go`](https://github.com/photoprism/photoprism/blob/261007-65faaae5d/internal/ai/face/embedder_onnx.go)).
The released indexing implementation uses a bounded worker/channel pattern
([released `index.go`](https://github.com/photoprism/photoprism/blob/261007-65faaae5d/internal/photoprism/index.go)).

Static thumbnails are generated during indexing and retained in the thumbnail
cache. PhotoPrism’s configuration includes a 1920px static size default, a 4096px
face-source limit, and a disabled-by-default dynamic rendering mode because
on-demand generation consumes CPU and memory
([configuration reference](https://docs.photoprism.app/getting-started/config-options/)).
Its face README says the detector consumes a 720p thumbnail, while YuNet and SFace
are the default detector and embedding models
([released face README](https://github.com/photoprism/photoprism/blob/261007-65faaae5d/internal/ai/face/README.md)).
The reuse pattern transfers directly; the exact 720/1920/4096 dimensions do not.

PhotoPrism does not start face recognition until the library scan is complete and
continues optimizing clusters in the background as faces, files, and corrections
arrive
([people guide](https://docs.photoprism.app/user-guide/organize/people/)). A normal
rescan skips files that are already indexed and unchanged, while a complete rescan
reprocesses them; overlapping index processes are discouraged
([originals/indexing guide](https://docs.photoprism.app/user-guide/library/originals/)).
That supplies an idempotent recovery story, but I found no released primary source
for a durable per-file, per-stage queue comparable to Immich or LibrePhotos. Lumina
should therefore copy PhotoPrism’s resident-session and bounded-worker patterns,
not infer queue guarantees that are not documented.

PhotoPrism recommends local SSD for the database rather than a shared/unreliable
network location
([performance guidance](https://docs.photoprism.app/getting-started/troubleshooting/performance/)).
Its released CUDA support accelerates built-in AI, while image decoding and
thumbnail generation remain CPU work
([GPU acceleration](https://docs.photoprism.app/user-guide/ai/gpu-acceleration/)).
That is another reason to establish the actual bottleneck before buying or enabling
an accelerator.

### LibrePhotos: durable chained jobs, one decode, and completion-triggered follow-ups

LibrePhotos 1.4.0 centralizes enqueueing through Django-Q2’s ORM broker and writes a
`LongRunningJob` row before submitting work. It records queue submission failure,
prevents duplicate jobs of a type for a user, and supports follow-up chains
([released `queue.py`](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/jobs/queue.py)).
The released job model persists queued/start/finish/fail/cancel state, progress,
and last-progress time; it includes conditional claims and stuck-job cleanup, with
comments acknowledging that large scans can run for days
([released `long_running_job.py`](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/models/long_running_job.py)).
Production settings make worker concurrency explicit and set queue, timeout,
recycle, memory, retry, and polling limits
([released production settings](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/librephotos/settings/production.py)).

The released scanner groups discovered files and bounds database lookup batches
instead of issuing a lookup per file. Most importantly for full completion, only
the last completed scan worker submits the post-scan repair/probe/tag/geolocation,
CLIP, and face follow-ups, so early worker completion cannot omit later-discovered
photos
([released `scan_jobs.py`](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/directory_watcher/scan_jobs.py)).
Per-photo work queues primary keys rather than whole object payloads, and missed
items are eligible for later repair
([released `processing_jobs.py`](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/directory_watcher/processing_jobs.py)).
That “last producer closes the generation, then enqueue dependents” rule is directly
useful for a million-file source.

LibrePhotos’ thumbnail renderer opens the original once, produces a large WebP,
then derives smaller sizes from that loaded image. The large rendition is also used
for pHash and ML consumers
([released thumbnail renderer](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/thumbnails/render.py)).
Its 1.2.0 release notes explicitly list one metadata request, one thumbnail decode
instead of three, one hash per file, and retaining embeddings already produced by
face detection as shipped work
([1.2.0 release](https://github.com/LibrePhotos/librephotos/releases/tag/1.2.0)).
The same release reports metadata extraction falling from about 390 ms to 30 ms
per photo after combining ExifTool requests. This is an upstream metadata-stage
measurement, not a Lumina benchmark or an end-to-end speedup ratio. Its 1.3.0
release also records one OpenCLIP pass for both tags and embedding
([1.3.0 release](https://github.com/LibrePhotos/librephotos/releases/tag/1.3.0)).
These are evidence for coalescing shared input/model work, not transferable timing
ratios.

Its small ML services are long-lived supervised processes with health/busy/loaded
state and idle model unloading without killing the service
([released sidecar support](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/service/_common.py)).
The ONNX helper selects CUDA then CPU and exposes CPU thread controls
([released ONNX session helper](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/service/onnx_session.py)).
The face service returns detection boxes and embeddings from one loaded
`FaceAnalysis` instance
([released face service](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/service/face_recognition/main.py)).

Two LibrePhotos choices are cautions rather than recommendations. Its face
classifier loads all face embeddings and fits HDBSCAN
([released face classifier](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/api/faces/classify.py)),
and its persisted semantic-search index is FAISS `IndexFlatIP`, an exact exhaustive
index
([released retrieval index](https://github.com/LibrePhotos/librephotos/blob/1.4.0/apps/backend/image_similarity/retrieval_index.py)).
Neither establishes acceptable memory or regrouping time for Lumina’s as-yet
unmeasured face count.

Finally, LibrePhotos’ face service uses InsightFace models. Lumina must **not** adopt
or redistribute those restricted weights. Immich’s own ML README is explicit that
its permission to use InsightFace models does not grant third parties a general
redistribution or commercial-use right
([Immich v3.3.5 ML README](https://github.com/immich-app/immich/blob/v3.3.5/machine-learning/README.md)).
The transferable features are resident supervision, provider selection, bounded
requests, and combined outputs—not the weights.

## Lumina’s current work amplification

The following points come from the local implementation audit for this research.
They describe deterministic repeated work; they still do not establish which stage
dominates wall-clock time.

| Current behavior | Consequence at one million clean imports | Required invariant while changing it |
| --- | --- | --- |
| `inspect()` and `render()` each start a Pillow child, call the facts path, and perform `ImageOps.exif_transpose`; the renderer already computes facts but returns only products. | One extra HEIC decode and one extra Python process start per photo: one million of each. This is not a claim of a 50% total speedup, because hashing, NAS reads, encoding, SQLite, and faces remain. | Keep the untrusted-image subprocess, pixel/dimension/resource limits, orientation semantics, and atomic publication. Return facts and all configured renditions from one child result. |
| Four source lanes parallelize sources, but each source prepares files serially. | A single million-photo NAS root cannot feed both available decoder slots through source concurrency alone. | Add a bounded per-file producer/consumer queue with one global decoder admission of two; retain source fairness, backpressure, cancellation, and ordered completion accounting. |
| Import makes a full private snapshot/hash and performs a second full source validation immediately before commit. | Source bytes can traverse the NAS twice even before later face work. | Do **not** substitute size/mtime: same-size, restored-mtime content changes are possible. Keep the second full validation until an explicitly equivalent immutable snapshot, server generation/version, or filesystem snapshot protocol is proven. Optimize overlap/local reuse without weakening this guarantee. |
| `_child_main` constructs `OpenCvFaceAnalyzer` for every photo. Its constructor validates and SHA-256 hashes YuNet (232,589 bytes) and SFace (38,696,353 bytes), then loads SFace. | Across one million photos, model digesting alone has about 38.9 TB of logical hash input. OS caching may prevent 38.9 TB of physical disk reads; this figure deliberately makes no disk-throughput claim. Interpreter and model setup also repeat. | Verify model digests once per worker lifetime, then preserve request limits, timeout, crash isolation, output validation, cancellation, and restart/backoff. ADR 0014 already provides long-lived supervised model children with idle shutdown. |
| A 20 GiB LRU governs derivatives, while full settle requires 400/1200/2400 products for every photo. | Valid outputs can be evicted and later recomputed, so a cache hit/miss policy cannot be the source of truth for “all previews complete.” | Persist all configured/promised renditions or explicitly change the product contract. Size storage from measured output distributions. At minimum, keep the analysis rendition referenced until every dependent ML job is terminal. |
| Face grouping asks for at most 16,000 assigned candidates, then unique prototypes capped at 4,096 plus 4,096 outliers, ordered by `updated_at`; the access path lacks a matching composite index. | Candidate quality and query cost can degrade as the table grows; more inference workers do not solve grouping lookup or fixed-neighborhood coverage. | Add the matching relational indexes and profile the query first. Then define a complete/rebuildable neighbor strategy, clustering generation, quality check, and migration path before raising inference concurrency. |

Lumina’s single-process FastAPI + SQLite boundary is not itself disqualified. Immich’s
Redis/PostgreSQL split is evidence for durable queues and indexed vectors, not a
requirement to import its deployment topology. LibrePhotos shows that database-
backed job state and supervised local services can provide the same important
boundaries. SQLite should remain until measured write contention, queue claim cost,
or vector requirements justify a migration.

## Ranked changes for Lumina

### 1. Make “fully settled” a database predicate

Create an ingestion generation tied to a source identity/version and pipeline
signature. Persist per-item stage state for metadata, each promised rendition,
face detection/embedding, and grouping, with attempt count, lease/heartbeat,
dependency signature, retry eligibility, terminal error/skip reason, and artifact
integrity. A run is fully settled only when discovery is closed and every item is
either successful for every enabled stage or terminally accounted for. Report
counts for discovered, unchanged, ready-by-stage, retrying, terminal failed, and
grouped. Reclaim expired leases after restart.

This copies Immich’s explicit dependency/count model and LibrePhotos’ durable job
rows without adding Redis. Stage handlers must be idempotent because durable queues
are generally at-least-once, not proof of exactly-once work.

### 2. Fuse inspect and render, then hand off a retained preview

Change the existing bounded Pillow child contract to return metadata/facts plus all
400/1200/2400 products from the renderer’s single decoded, EXIF-transposed image.
Publish those products and their records atomically for the source generation.
Pass the retained 1200px product (or a separately measured analysis size) directly
to the face queue rather than reopening the HEIC or taking another NAS snapshot.

This removes exactly one full HEIC decode and one Python start from the clean path
per imported photo. It does not justify a percentage or elapsed-time claim. Retain
all three configured sizes if they are product promises; sharing the 1200px product
with ML is about avoiding rework, not deferring the 2400px viewer asset.

### 3. Put face inference in a long-lived supervised child

Adapt the existing ADR 0014 supervisor instead of introducing an external model
container. Start one YuNet/SFace child, validate and hash the model files once,
load both models, accept bounded framed requests, and idle-shutdown/restart under
the existing supervisor policy. Keep the same output validation and untrusted-input
boundary. Record model-load time, service time, queue delay, RSS, timeout, crash,
and restart counts.

Start with one resident worker. Then benchmark a small pool and any supported CPU,
Core ML/OpenVINO/CUDA-style execution provider that is actually available to the
chosen runtime. If recognition can batch face crops safely, benchmark small bounded
batches separately from detection. Immich’s default maximum of four faces shows
that batching exists; it is not evidence that four is Lumina’s optimum.

### 4. Feed the decoder with a bounded per-file queue

Keep the source walker/cursor as producer, but let one large source place a small
window of validated work onto a global queue. Two decoder consumers can then use
the existing admission budget even when there is only one source. Give NAS
copy/hash, HEIC decode, encoding/write, ML IPC, and grouping separate admissions so
the product of several worker settings cannot accidentally create unbounded work.
Queue bounds should be expressed in files and bytes because 4–12 MB is only the
compressed source size; decoded pixels determine memory pressure.

### 5. Separate durable rendition storage from the browsing LRU

Keep SQLite, queue state, model files, and active derivative writes on local SSD
when possible. Originals may remain on the NAS. Store every promised rendition in
a durable, content/pipeline-addressed location and reserve/measure capacity for
roughly three million outputs plus temporary atomic writes. If a 20 GiB browsing
LRU remains, it should cache access to durable artifacts, not decide whether the
import is complete. A manifest or database coverage query should find missing,
wrong-signature, or corrupt products without reading every original again.

### 6. Fix relational access, then choose a vector/grouping design

Add and verify indexes matching the existing owner/person/assignment/order filters
before introducing ANN complexity. Instrument candidate query time, candidates
examined, faces per photo, total embedding count, unassigned rate, and regrouping
time. Remove the fixed 4,096/4,096 candidate ceilings only with a replacement that
has defined recall and resource bounds.

The next design should keep authoritative embeddings in SQLite while treating any
ANN structure as disposable and rebuildable. It needs a single-writer/update rule,
checkpointed build generation, crash-safe swap, deletion handling, an exact-query
sample for recall checking, and a full regroup command. Options such as an on-disk
HNSW sidecar or a database migration should be evaluated against the observed face
count; this report does not select one. Avoid LibrePhotos’ all-embeddings HDBSCAN
fit unless measured memory and restart time are acceptable.

### 7. Preserve safe source validation while reducing NAS amplification

Keep the second full pre-commit hash today. Measure whether the NAS supports an
immutable snapshot handle, file ID/change token, or server-side snapshot generation
that is cryptographically/equivalently tied to the bytes copied for processing.
Only such a protocol can replace full revalidation without weakening the current
same-path mutation defense. Independent of that decision, schedule large reads
sequentially, avoid later face reads through derivative handoff, and pipeline local
decode/encode against bounded NAS reads rather than opening many HEIC files at once.

### 8. Benchmark libvips and preview encoding as candidates, not conclusions

libvips recommends its `thumbnail()` path because it combines load and shrink,
uses sequential access, and can reuse intermediates in a long pipeline
([libvips developer checklist](https://www.libvips.org/API/current/developer-checklist.html)).
Its shrinking guide says an embedded HEIC thumbnail is used only when large enough
for the requested result
([image shrinking guide](https://github.com/libvips/libvips/wiki/HOWTO----Image-shrinking)).
An iPhone’s embedded thumbnail therefore cannot be assumed sufficient for Lumina’s
2400px viewer output or face input. The published JPEG example is not a Lumina HEIC
benchmark.

Benchmark one-decode Pillow against one-pipeline libvips on representative files,
including metadata/orientation parity, corrupt inputs, memory, all three outputs,
and subprocess isolation. Separately benchmark JPEG for the 1200/2400 previews at
matched visual quality: Immich supports JPEG and WebP and states that WebP is
smaller but slower to encode
([Immich image settings](https://docs.immich.app/administration/system-settings/)).
Lumina currently encodes all three at WebP quality 82/method 4, but neither storage
size nor encode CPU can be presumed to win without its own corpus. Do not change
the promised quality/viewer behavior merely to improve first-gallery time.

## Completion-rate targets, not forecasts

For one million photos, continuously sustained *completed-pipeline* throughput must
reach these rates. The source-read column assumes an 8 MB average original and the
current two full NAS reads, before protocol, metadata, retry, or regeneration costs.

| Completion target | Fully completed photos/s | Source bandwidth alone |
| --- | ---: | ---: |
| 24 hours | 11.57 | 185.2 MB/s |
| 48 hours | 5.79 | 92.6 MB/s |
| 72 hours | 3.86 | 61.7 MB/s |

At 100 MB/s, the resulting 16 TB of source reads alone takes roughly 44.4 hours.
Processing can overlap those reads, but faster inference cannot beat this source
bandwidth floor. These numbers express required capacity; no reviewed benchmark
shows that Lumina currently sustains these completed-pipeline rates.

Discovery also needs a memory qualification: the current walker materializes and
sorts each directory's entries. A single directory containing a million files can
consume substantial memory before decoder admission applies. A disk-backed discovery
manifest or bounded enumeration strategy should be tested alongside typical nested
libraries. NAS file watchers can provide hints, but periodic reconciliation remains
necessary; Immich explicitly warns that network-drive watching may not work
([external-library watching](https://docs.immich.app/features/libraries/#automatic-watching-experimental)).

## Measurement and acceptance plan

Before estimating a completion date, run a consented, stratified HEIC corpus through
the complete path on both local storage and the actual NAS. Include the common
iPhone dimensions/bit depths/orientations, Live Photo companions where supported,
bursts, unusually large files, corrupt inputs, and files that change during import.
Run cold-cache and warm-cache trials, but label them; a million-file first settle
will contain both regimes.

Measure, per stage and end to end:

- source enumeration, first snapshot/hash, and second pre-commit validation bytes
  and latency;
- inspect/decode, each rendition encode/write, and peak child RSS;
- face model cold start, resident request latency, faces per image, crop batch size,
  provider utilization, RSS, and queue delay;
- SQLite claim/publication time, busy retries, WAL growth, and checkpoint behavior;
- candidate-query and grouping generation time versus accumulated face count;
- artifact bytes by size/codec, temporary-write headroom, and cache/retention
  pressure; and
- retries, terminal failures, restart recovery, and final coverage-query time.

Report throughput distributions and resource saturation, not only an average
photos/second. Perform a kill/restart test during every stage and prove that the
generation reaches the same terminal counts without losing or silently skipping
work. Verify all configured renditions by manifest/signature sampling and compare
face-grouping quality against the current exact/bounded behavior on an approved
sample.

Only after that measurement should Lumina calculate a range from observed stage
service demands and resource concurrency. The range should include retries,
grouping, and final coverage verification; extrapolating decoder throughput alone
would again answer time-to-first-gallery rather than time-to-full-completion.

## Explicit non-recommendations

- Do not use the prior 6–23 day estimate; its 0.5–2 photos/second premise was not
  measured.
- Do not claim a 50% overall gain from eliminating the second Pillow decode. The
  exact eliminated work is known, but its share of wall time is not.
- Do not defer 2400px previews, face inference, or grouping and call that a faster
  full completion. Prioritization may improve interactivity, but the outstanding
  work still belongs in the settle time.
- Do not replace the second NAS hash with size/mtime-only validation.
- Do not adopt Redis, PostgreSQL, multiple containers, or a GPU merely because
  Immich uses them. Adopt the durable-stage, resident-model, vector-query, and
  locality properties when Lumina measurements require them.
- Do not adopt restricted InsightFace weights. YuNet/SFace remain the licensed
  model path unless a separate model review changes it.
- Do not raise decoder/model worker counts independently. Memory, NAS locality,
  SQLite write serialization, and grouping can simply move the bottleneck.
- Do not treat PhotoPrism’s background optimization or LibrePhotos’ full HDBSCAN
  fit as proof of acceptable million-photo grouping time.

## Remaining blockers to a calendar estimate

The actual host CPU/GPU/RAM, NAS protocol and locality, source mutation rate, HEIC
dimensions/bit depth, derivative-size distribution, detected face count, and warm
versus cold filesystem-cache behavior are unknown. One million photos is not one
million face vectors. Until those inputs and stage measurements exist, no primary
source reviewed here supports a days-to-completion promise or a hardware purchase.
