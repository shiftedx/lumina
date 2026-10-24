# Photo ingestion optimization and full-pipeline measurement

This follow-up implements fused initial decoding, resident face models, and bounded
per-file preparation within one registered source. The measured baseline was commit `c11a85d` (the original gallery implementation
plus research). Its backend tree is byte-identical to rebased commit `47567cb`,
retained in [PR 151](https://github.com/shiftedx/lumina/pull/151) history. The
optimized implementation is the change accompanying this report. No decoder/model dependencies or image
quality settings changed.

## What changed

- Initial uploads and external imports extract metadata and generate all
  400/1200/2400 WebPs from one decoded image in one bounded child. Cache repair
  retains its independent renderer. Container/facts/output checks, EXIF orientation,
  ICC conversion, metadata stripping, and source hash-before/hash-after checks remain.
- One supervised face child verifies and loads YuNet/SFace, then serves bounded
  versioned requests. YuNet input dimensions change between requests without loading
  a new detector. Each request retains CPU, memory, active-time and output bounds;
  cancellation, idle shutdown, crash backoff/circuit breaking, model replacement and
  explicit Retry are covered. Parent output validation includes unit-norm embeddings.
- Each source has two ordered preparation futures. Four global preparation slots and
  two decoder slots cap concurrency, including across retired pool generations.
  Publication/cursors remain ordered. Automatic work rechecks maintenance admission
  at execution; cancellation stops admission waiters. Lifecycle retirement is
  linearized with short writes, while source I/O stays outside the writer lock.
- The second complete external-source hash before publication is preserved. The
  improvement does not substitute size/mtime for content verification.

## Workload and method

Host: macOS arm64, Apple M4 Max, 64 GiB RAM; local SSD, no GPU inference.

The fixture has 16 distinct HEIC files, 12 or 24 megapixels, approximately
7.6–9.5 MB each. Four generated high-entropy scenes contain a resized public-domain
NASA astronaut photograph. Extra ISO BMFF `free` boxes give repeated scenes distinct
source hashes, so every file actually decodes/renders. The manifest records every
source SHA-256. The portrait provenance and pinned digest are in the benchmark and
result JSON. This is a decoder/encoder workload with real face detection, embeddings,
and grouping; it is not a diverse iPhone or identity-accuracy corpus.

Three baseline and three optimized trials alternate order. Every trial runs in a
fresh Python process and disposable SQLite/data directory, with already downloaded
verified model files. Each timed trial invokes the production registered-source
scanner, secure snapshots, both source validations, metadata extraction, all three
renditions, database publication, explicit face job queueing, production face-worker
claims/inference/grouping, and final counts plus rendition checksum verification.
Face work follows ingestion in this measurement. It is explicitly requested and runs
continuously rather than waiting for a maintenance window.

The final predicate requires 16 catalog entries, 48 ready and present renditions,
16 successful face jobs, zero queued/running/failed face jobs, at least 16 detected
faces, and a formed person group. All six trials must have exactly identical
rendition SHA-256 maps. Fixture generation, model download/copy, schema setup, source
registration, HTTP and browser rendering are excluded. OS caches are warm/uncontrolled;
the benchmark does not flush the host cache or claim cold-NAS measurements.

## Results

Three paired trials per version, alternating baseline/optimized order:

| Measure | Baseline | Optimized |
| --- | ---: | ---: |
| Total completion, median | 59.46 s | 20.61 s |
| Total completion, observed range | 56.17–60.90 s | 19.31–27.30 s |
| Completed photos/s, median | 0.2691 | 0.7762 |
| Ingestion stage, median | 48.12 s | 18.90 s |
| Face jobs + completion checks, median | 8.53 s | 1.72 s |
| Subsequent face job, median of trial medians | 522.7 ms | 84.6 ms |
| Child-process starts per trial | 48 | 17 |
| Ready and present renditions | 48 | 48 |
| Successful face jobs / pending / failed | 16 / 0 / 0 | 16 / 0 / 0 |
| Detected faces / formed groups | 16 / 1 | 16 / 1 |

The final comparison is **2.885× faster**, or about **65.3% less elapsed time**.
All 48 rendition SHA-256 values match exactly across all six trials; each trial
retains 39,258,848 rendition bytes. Stage medians are calculated independently and
need not sum to the median total. Timing variation on this shared development host
is included rather than selecting the fastest sample.

The 48 baseline children are 16 inspectors + 16 renderers + 16 face analyzers;
the optimized run starts 16 fused preparers + one resident face analyzer.

[Full machine-readable observations](measurements/photo-pipeline-paired-2026-10-24.json).

## Reproduce

From the repository root, with the locked backend environment installed:

```sh
mkdir -p output/photo-pipeline-baseline
git fetch origin pull/151/head
git archive 47567cb backend | tar -x -C output/photo-pipeline-baseline
mise exec -- backend/.venv/bin/python scripts/perf/photo_pipeline.py \
  --corpus output/photo-pipeline-benchmark/heic-corpus --generate --count 16
mise exec -- backend/.venv/bin/python scripts/perf/photo_pipeline.py \
  --baseline-root output/photo-pipeline-baseline \
  --corpus output/photo-pipeline-benchmark/heic-corpus --trials 3 \
  --output output/photo-pipeline-benchmark/paired.json
```

Fetching the PR head makes the recorded baseline available even after a squash
merge. Generation refuses to overwrite an existing corpus. Both variants use the same
interpreter, dependencies, files, model weights and settings. The fixture generator
downloads only the pinned public-domain portrait and catalog-pinned face weights;
all generated media/model/data folders stay outside version control.

## Validation

`mise exec -- make check` passed against the final implementation:

- Backend: **5,364 passed**, 11 documented environment/performance-fixture skips.
- Frontend: **309 files / 2,679 tests passed**.
- Full browser tier: **655 passed**, zero skipped/unexpected/flaky cases;
  1,060.398 seconds against its 2,700-second budget. Includes real photo upload/NAS
  indexing, household workflows, accessibility, existing media performance budgets,
  and WebKit remux/transcode/seek playback.
- Typecheck, production build, dependency licenses and stylesheet budgets passed.

The existing navigation-resilience browser test now waits for the same width/height
limits after a viewport resize, preserving its contract while allowing the responsive
popover layout to settle. Three focused repeats passed before the full gate.
Existing request-promotion, portrait-geometry and child-working-directory contracts
were extended in the renamed face-worker tests; they were not removed.

## Qualification limits

These measurements establish gains for this complete small processing workload.
They do not qualify a million-photo library, a million-face grouping index, NAS
bandwidth/latency, Linux containers, mixed-format iPhone HDR/color behavior, concurrent
household workloads, or long-duration memory growth. The synthetic noisy backgrounds
stress WebP encoding and should not be treated as natural-photo throughput.

The existing 20 GiB derivative LRU policy is unchanged; this fixture fits entirely
within it. Capacity-backed rendition retention and large-scale face-index work remain
separate follow-ups before promising that every million-photo preview stays prepared.
The benchmark checks every fixture output exists at completion; ready database rows
alone would not meet that condition.
