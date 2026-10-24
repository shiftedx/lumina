# Synthetic million-face index qualification

This measurement uses one million fixed-seed, synthetic normalized 128-float
vectors. It never opens household media or calls a face model. The direct-core
phases do not exercise SQLite, NAS, photo import, or person/grouping accuracy;
the optional service phase does exercise a disposable SQLite fixture and the real
candidate/checkpoint/replay adapter.

Run from the repository root after the pinned `faiss-cpu` environment is installed:

```sh
mise exec -- backend/.venv/bin/python scripts/perf/photo_scale.py \
  --count 1000000 --queries 64 --threads 1 --service \
  --output docs/performance/measurements/photo-face-index-1m-2026-10-24.json
```

The harness constructs a released Faiss CPU `IndexIDMap2(IndexHNSWFlat)` in
single-threaded fixed-seed chunks, adds a bounded incremental batch, writes/loads a
temporary native artifact, and builds a fresh replacement. The artifact stays in a
temporary directory and is deleted on exit.

It reports build/update/restart/rebuild time, process peak RSS, serialized artifact
size, HNSW p50/p95 search, a bounded exact-representative p50/p95 reference, and
streamed exact global top-k recall for 64 new (non-self) query vectors. Global exact
truth regenerates chunks in memory and scans all one million vectors; it does not
retain the corpus. The recency comparison searches only the final 16,000 entries
against deliberately old-person queries, exposing coverage bias without making a
claim about household identity accuracy.

The checked-in measurement JSON records a runtime source SHA-256 with its service
result. If the service SQL changes after direct-core metrics have been recorded,
refresh just the adapter metrics without rebuilding HNSW or replacing core results:

```sh
mise exec -- backend/.venv/bin/python scripts/perf/photo_scale.py \
  --count 1000000 --queries 64 --threads 1 --service-only \
  --output docs/performance/measurements/photo-face-index-1m-2026-10-24.json
```

With `--service`, it additionally writes a disposable one-million-row SQLite fixture
through the real face and index triggers, measures the first
`prepare_candidates` build/checkpoint, warm candidate p50/p95, a
`reset_for_tests()` checkpoint restart, and journal replay after 32 assignment
updates plus 32 deletions. It calls `settle_pending()` after that replay, before
the simulated restart, so restart measures a fully checkpointed index rather than
an intentionally pending journal tail. It does not create a person/prototype
fixture, so that mode qualifies the unassigned candidate path rather than
person-grouping accuracy.

To measure a fair full-corpus exact latency reference without rebuilding the ANN
graph or SQLite fixture, run this separately after the qualification finishes:

```sh
mise exec -- backend/.venv/bin/python scripts/perf/photo_scale.py \
  --count 1000000 --queries 64 --threads 1 --resident-exact-only \
  --output docs/performance/measurements/photo-face-index-resident-exact-1m-2026-10-24.json
```

It holds the full 1,000,000 × 128 float32 corpus (about 512 MB) in RAM and reports
per-query exact top-k p50/p95. The output records the BLAS thread environment;
interpret it as a native-compute comparison only, not an end-to-end service-latency
comparison.

## Recorded synthetic results

The raw JSON files are the source of truth. On the recorded arm64 macOS host with
Faiss 1.15.1, M=16, efConstruction=96, efSearch=96, and one Faiss thread:

- Direct one-million-vector HNSW build took 163.238 s; the serialized graph was
  670,901,799 bytes and reloaded in 0.063 s. For 64 deliberately old-person
  queries, direct ANN search was p50/p95 0.144/0.191 ms with measured exact
  recall@32 and older-person coverage both 1.0. The 16,000-row recency comparison
  had recall and older-person coverage 0.0 by construction.
- The separate full resident exact reference held 512,000,000 bytes of vectors and
  scanned all one million per query in p50/p95 11.307/17.030 ms under the recorded
  one-thread BLAS environment. This is a compute reference, not a service claim.
- The service fixture contains one million unassigned synthetic faces. Its recorded
  runtime source hash is `c99949da…`: seed 113.848 s, first build/checkpoint
  167.355 s, warm candidate p50/p95 2.382/2.649 ms, settled-checkpoint restart
  4.401 s, and exact recall@32 1.0. Peak process RSS was 3,473,653,760 bytes and
  the disposable SQLite database was 4,348,391,424 bytes. The current source hash
  `3b37ec6f…` differs only by the later multi-owner LRU count/byte-budget trim;
  reconstructing the current source without that patch produces the recorded hash
  exactly. The recorded one-owner graph is below the default 2 GiB estimate and
  is not evicted, so the run remains functional evidence for current build,
  recall, journal, and restart behavior. The latency figures still describe the
  recorded hash; rerun `--service-only` before treating them as measurements of
  the cache-trim revision itself.
- The archived recency-capped candidate service baseline on the same synthetic
  unassigned fixture recorded median/p95 2,524.237/2,693.802 ms per query and
  old-person coverage 0.0. Its run overlapped the direct HNSW build on this shared
  host and is an archived-implementation comparison, not a production SLA.
- A separate 20,000-face, 10,000-person assigned/prototype qualification against
  runtime source hash `3b37ec6f…` recorded build/checkpoint 0.940 s, warm prototype
  p50/p95 2.514/2.699 ms, restart 0.219 s, and person recall@32 1.0 both initially
  and across two restart reads. It excludes 32 deliberately reassigned source and
  target people from its query set; it does not measure grouping accuracy.

The result is a native-index qualification, not a promise of full photo-import
throughput or face-grouping quality. See the JSON result for the precise package,
platform, HNSW settings, seed, and caveats.
