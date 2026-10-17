# Python candidate loading and DTO construction against 0.3.4

This change keeps the Python runtime and existing service contracts. Mixed search reads item link fields first, then hydrates full ORM rows only for standalone library results and transcript moments. Title-backed file hits retain version/summary scoring text without unused provider JSON. Moment-only searches keep their single hydration query. Search ancestors project only names and parent links; title records use the fixed projection order rather than constructing keyword mappings. Jellyfin DTOs reuse a bounded 4,096-entry cache of pure UUID-to-hex conversions. No member decisions, image grants, DTOs or mutable metadata enter that cache.

The harness in `scripts/perf/python_hotpaths.py` compares eight alternating fresh processes (ABBA then BAAB). Every response must equal its warm-up result and the response hashes must agree across revisions. It measures a 60-movie page with MediaSources and an untyped semantic search returning up to 200 results, rotating eight queries. CPU includes equality checking; RSS is measured after both service workloads and includes the interpreter, ORM and caches. This does not measure HTTP transport, decoding, transcoding, AI inference or Jellyfin's own server.

The fictional, indexed fixture contains 1,735 movies, 80 series and 2,000 episodes. It was generated with `seed_titles.py` and its index rebuilt with `ensure_search_index`. Native comparison uses an archive of tag `v0.3.4`. Linux comparison uses the published image pinned to `sha256:23876fbffa0ad94e4dfae303268c40604efed567aa1f6b46d0a01f4657d978ab`, with the candidate backend mounted read-only, identical image dependencies, two CPUs and a 512 MiB memory ceiling. Copy the database into the container's writable temporary directory before running: SQLite WAL cannot open this fixture from its read-only mount.

## Results

Linux medians of eight block metrics per revision, across two complete comparisons (4,800 measured calls per workload per revision):

| Service | 0.3.4 p50 | Candidate p50 | Change | CPU/request before → after |
| --- | ---: | ---: | ---: | ---: |
| 60-movie DTO page with MediaSources | 12.472 ms | 12.253 ms | 1.8% faster | 13.886 → 13.649 ms (1.7% less) |
| Untyped search, rotating eight queries | 10.982 ms | 10.720 ms | 2.4% faster | 12.020 → 11.760 ms (2.2% less) |

Page p95 was 13.963 → 13.719 ms; search p95 was 13.123 → 12.923 ms. Post-workload RSS was 118.008 → 117.826 MiB: **no meaningful memory gain measured**. Native results were 3.3% faster pages and 3.9% faster search, also with essentially unchanged RSS. All response hashes matched.

These are small improvements. Both Linux comparisons favored the candidate, but the host was shared and `make check` was active; background noise remains. This is evidence for modest service savings from a small Python change, not a broad speedup, energy-efficiency result, or comparison against Jellyfin. The fictional fixture has relatively small file metadata; do not extrapolate a large memory saving from avoiding unused provider JSON. Raw samples, source hashes and image identity are in [the measurement record](measurements/python-hotpaths-0.3.4-2026-10-14.json).

Reproduce locally after seeding and indexing a fixture:

```sh
mkdir -p /tmp/lumina-baseline
git archive v0.3.4 backend | tar -x -C /tmp/lumina-baseline
mise exec -- backend/.venv/bin/python scripts/perf/python_hotpaths.py \
  /path/to/fixture/data/app.db /tmp/lumina-baseline/backend \
  --samples 600 --output /tmp/python-hotpaths.json
```

The retained service tests cover typed/untyped title materialization, full standalone/moment payloads, visibility, mutable metadata and DTO stream/sidecar projections. The DTO workflow also verifies repeated nested UUIDs are converted once within the bounded cache. The complete gate additionally covers browser and real-media behavior. No Rust extension or new runtime dependency is part of this change.

Full verification: `mise exec -- make check` passed with 4,825 backend cases (11 skipped), 2,454 frontend cases across 286 files, 47 real-backend browser cases (4 skipped), and 588 mocked browser cases (2 skipped), plus bootstrap, typecheck, build and style/bundle gates. The backend gate took 505.99 seconds; the prior run took 530.25 seconds under different host conditions.
