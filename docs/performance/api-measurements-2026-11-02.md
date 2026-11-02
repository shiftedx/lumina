# API read measurements — 2026-11-02

Source: `a5e9329f32b9c6f1fb91f7a42b023dcd035a9be4`. These are diagnostic observations, not production or app-screen latency guarantees.

## Controls

- Real loopback HTTP against the application on a disposable SQLite fixture; no production service/database was contacted.
- Existing `seed_titles.py` defaults except 30 albums / 300 tracks: 1,735 movies, 850 series, 2,529 seasons, 37,500 show episodes, 20 anime movies, 60 anime series / 720 episodes, 27 artists, 30 albums, 300 tracks and 10,698 progress records.
- One admin principal via browser cookie and native token; concurrency six means six simultaneous requests, not six independent members.
- 20 samples at C1 and 20 samples through a six-worker pool per route. First observation is separate, not a certified cold-cache sample. Server process persists across cases; no OS cache flush.
- App lifespan/background workers off to isolate route computation. Existing `scripts/perf/serve.py` supplies canned extraction, loopback-only sockets, disabled `main.enforce_rate_limit`, and SQL-count headers. Service-local rate limits may remain. No provider latency, live media decode, artwork generation, WAN loss, or import contention is qualified.
- Both runs failed the quiet-host observation (load averages in raw data). C1/C6 differences include host contention, scheduling, shared in-process work and endpoint admission, not only database time. Do not convert these into promised savings, capacity numbers, or service-level objectives.
- SQL counts cover statements before response headers. JSON routes complete their database reads first; these counts do not describe streaming-body work. Byte counts are decoded response bytes, not compressed wire bytes.
- Empirical p95 uses nearest rank with N=20 and is intentionally only a small-sample diagnostic. No p99 claim.

## Results

All **902 recorded GET observations returned 200** (22 cases × one first observation plus 20 C1 and 20 C6). Health/auth setup calls are outside that total. The raw files retain every sample.

| Case | C1 median / p95 ms | C6 median / p95 ms | Median SQL | Median decoded bytes |
| --- | ---: | ---: | ---: | ---: |
| `web_navigation` | 11.7 / 15.0 | 34.2 / 63.1 | 11 | 93 |
| `web_settings` | 3.2 / 3.5 | 12.0 / 15.6 | 3 | 2,430 |
| `web_library_50` | 89.6 / 94.1 | 733.7 / 834.1 | 6 | 39,709 |
| `web_library_500` | 89.7 / 93.6 | 650.8 / 678.7 | 6 | 79,331 |
| `web_groups` | 46.5 / 73.3 | 202.9 / 240.1 | 3 | 117 |
| `web_titles_50` | 16.2 / 17.0 | 214.9 / 302.7 | 8 | 85,763 |
| `web_continue` | 9.6 / 11.1 | 94.9 / 104.0 | 10 | 62,210 |
| `web_new_library` | 11.4 / 12.8 | 72.9 / 165.9 | 9 | 33,211 |
| `web_title_rows` | 8.8 / 10.0 | 53.9 / 66.7 | 11 | 37,529 |
| `web_movie_detail` | 6.1 / 7.2 | 34.4 / 43.0 | 10 | 2,730 |
| `web_series_detail` | 13.3 / 14.8 | 81.1 / 105.2 | 13 | 14,472 |
| `native_navigation` | 11.4 / 14.4 | 44.2 / 53.5 | 11 | 94 |
| `native_capabilities` | 3.3 / 3.7 | 11.5 / 15.6 | 3 | 292 |
| `native_movies_50` | 20.4 / 35.3 | 107.5 / 130.6 | 6 | 46,767 |
| `native_movies_500` | 116.1 / 286.5 | 795.0 / 1247.7 | 6 | 465,222 |
| `native_resume` | 25.3 / 35.2 | 177.8 / 232.1 | 10 | 22,949 |
| `native_home` | 273.9 / 512.9 | 780.9 / 922.2 | 72 | 129,958 |
| `native_catalog_movies_50` | 16.6 / 17.5 | 62.2 / 71.6 | 6 | 22,307 |
| `native_catalog_movies_500` | 40.9 / 98.9 | 282.0 / 293.8 | 6 | 219,801 |
| `native_home_recent` | 200.7 / 320.1 | 1734.2 / 1902.9 | 7 | 54,569 |
| `native_home_resume` | 23.0 / 32.3 | 101.7 / 135.1 | 10 | 26,861 |
| `native_home_nextup` | 82.5 / 158.6 | 443.1 / 478.2 | 10 | 54,820 |


`web_library_500` is the *requested* limit; the web endpoint caps it at **100** (`backend/app/services/library.py:51`). It is not a 500-record response. The two broad `native_movies_*` rows use default DTO fields; they are **not** the native wall’s actual catalog projection. The five `native_catalog_*` / `native_home_*` cases in the second raw file reproduce the native caller query fields, sort and offsets. Different host-load windows and query shapes prevent treating broad-vs-exact timings as an isolated A/B experiment.

The native catalog projection was already substantially reduced before this audit: the exact 500-item continuation returns 219,801 decoded bytes here. Its six SQL statements are batch work, not 500 per-item queries. Preserve that projection and the server’s two-worker Items admission rather than responding by removing limits. Native Home Recent and rich Home rows are more compelling work-reduction targets.

## Artifacts

- [Broad route raw observations](measurements/api-reads-2026-11-02.json).
- [Exact native query raw observations](measurements/api-native-exact-2026-11-02.json).
- [Sanitized host/resource metadata](measurements/api-read-environment-2026-11-02.json); baseline process names/paths are intentionally omitted. Peak sampled RSS is the benchmark process tree, not an app/device memory figure.
- Frozen [broad driver](measurements/api-read-driver-2026-11-02.py), [native-query driver](measurements/api-native-exact-driver-2026-11-02.py), and [server wrapper](measurements/api-read-server-2026-11-02.py). These are audit artifacts, not application modules. They operate on the fixed disposable output fixture only.

## Reproduce

Run from the repository root in a fresh checkout/output directory. `seed_titles.py` refuses a nonempty fixture; retain earlier evidence rather than deleting it. Use the pinned toolchain and existing performance lock. The drivers correct the legacy seed’s `complete` status spelling to `completed` and enable the native API **only in that disposable fixture**, with native remote streaming disabled.

```sh
mkdir -p output/api-performance-2026-11-02
cp docs/performance/measurements/api-read-server-2026-11-02.py output/api-performance-2026-11-02/server.py
cp docs/performance/measurements/api-read-driver-2026-11-02.py output/api-performance-2026-11-02/measure.py
cp docs/performance/measurements/api-native-exact-driver-2026-11-02.py output/api-performance-2026-11-02/measure-native-exact.py
mise exec -- backend/.venv/bin/python scripts/perf/seed_titles.py output/api-performance-2026-11-02/fixture --albums 30 --tracks 300
mise exec -- backend/.venv/bin/python scripts/perf/audit_run.py --name api-read-audit-2026-11-02 --out output/api-performance-2026-11-02/resources.json -- backend/.venv/bin/python output/api-performance-2026-11-02/measure.py
mise exec -- backend/.venv/bin/python scripts/perf/audit_run.py --name api-native-exact-2026-11-02 --out output/api-performance-2026-11-02/resources-native-exact.json -- backend/.venv/bin/python output/api-performance-2026-11-02/measure-native-exact.py
```

A quiet repeat, non-admin/member-scoped cohorts, multi-member read/write contention and provider/media workloads are required before setting production budgets.
