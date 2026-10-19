# Books and manga performance audit — 2026-10-18

## Scope and source

Audit of EPUB, PDF, CBZ and CBR catalog, series, reader startup, resume, page/chapter turns, progress writes, archive parsing, and server/browser resource bounds. Changes live on `codex/books-manga-performance` in a dedicated worktree based on `a4e01dad62fadc6186ca27ea0fbda8e3ea0d3674`. The original checkout's uncommitted integration/performance work is excluded.

Synthetic archives and disposable test databases keep private books, hostnames, credentials and reading history out of retained evidence. This work does not deploy changes or establish the cause of the specific reported comic's production delay. The deployed file, its compression method, production server load, storage latency and visitor network have not been profiled.

## Findings and remediation

| Priority | Finding | Result |
| --- | --- | --- |
| High | Native import inspection blocks the ASGI event loop while waiting for a subprocess/semaphore. | Moved DB-free inspection to the thread pool. A public HTTP regression holds inspection open and requires an unrelated health request to finish before release. It failed before the fix and passes afterward. Upload disk writes/fsync and transaction work remain synchronous. |
| High | Reader navigation awaits serialized progress PATCH requests before changing content. Every turn adds more saves. | Turns update immediately. A 500 ms debounce and one active plus one latest pending checkpoint bound queued requests. Opening or resuming still refreshes Continue-reading recency; that initial write is debounced and may coalesce with a turn. Failed saves remain visible/retryable, failed EPUB loads retain the last readable checkpoint, and stale prior-book responses cannot overwrite the current reader. |
| High | Each page/cover request reopens ZIPs or starts PDF/RAR workers, including identical concurrent requests and repeated reads. | A server LRU retains encoded raster responses, bounded to 64 MiB and 128 entries; identical in-flight reads share work, with at most four uncached raster readers. Authorization and `private, no-store` remain on every HTTP delivery. File identity changes invalidate reuse. This saves server parsing/rendering, **not repeated network bytes or browser decode memory**. |
| Medium | Every CBR read validates all archive members, including members after the requested page. | Exact-identity in-process validation reuse permits unchanged, already validated archives to stop after the requested member. First read after restart still validates the full archive. Persisted manifests alone do not grant that optimization. Solid archives can still require decoding preceding members. |
| Medium | A series-detail request first aggregates the complete manga collection. | The service scopes summary aggregation by series ID before loading member states. Visibility and bundle-state semantics are retained. |
| Medium | Catalog/series status classification probes physical files it does not use. | State-only classification avoids those probes; availability is still checked for actual serialized file choices. |
| Medium | Shelves issue membership and book queries individually. | Shelf metadata and visible joined memberships/books use two bulk queries. Private books and empty shared shelves remain hidden appropriately. |
| Medium | Manga, series and book-detail views request the complete overview despite not displaying it. | Overview loads only for Overview/Shelves or when shelf choices are requested. Existing shelf workflows remain covered. |
| Medium | EPUB turns wait for chapter transfer and structured parsing. | One next structured chapter is prefetched and cancelled on leave. Native image prefetch is deliberately absent: under `no-store`, speculative image requests could duplicate transfers and expensive rendering. |

## Measured server effects

The archive harness uses 120 units, valid 900 × 1300 PNGs of 113,943 encoded bytes, roughly 13 MiB CBZ/CBR files, a 120-page synthetic PDF and long EPUB chapters. Images are repeated deterministic artwork with an ancillary payload; they are not representative scans or a codec-complexity benchmark. RAR4 members are stored and non-solid. Each value below is the median of three samples, with warm/unknown OS filesystem cache. These are component observations under host contention, not user-perceived startup latency or tail guarantees.

| Operation | Before ms | After ms | Interpretation |
| --- | ---: | ---: | --- |
| Repeat PDF page | 190.261 | 0.114 | Avoids worker spawn and rasterization. |
| Repeat CBR page | 89.753 | 0.092 | Avoids worker spawn and archive validation/decompression. |
| Repeat CBZ page | 0.654 | 0.018 | Avoids ZIP index/read work. |
| Cold PDF page | 139.497 | 144.015 | No cold improvement established. |
| Cold CBR page | 77.152 | 86.388 | No cold improvement established. |
| EPUB chapter | 2.136 | 3.573 | Server parser unchanged; browser lookahead is separate. |

Raw `baseline.json` and `after.json` contain wall time, parent plus terminated-child CPU time, returned bytes, per-run samples, fixture facts and cache bounds under `output/books-performance/`. These are the locked final server-harness repeat; other host workloads and the full backend tests may still contend.

The catalog harness uses SQLite in memory and substitutes a counted successful availability check. It measures ORM/query/classification work and probe counts; it does **not** measure real filesystem latency. One observation per workflow/scale is not a latency distribution.

| 10,000-book case, 100 shelves, 200-volume target series | Before | After |
| --- | ---: | ---: |
| Catalog page of 40, availability probes | 10,120 | 120 |
| Catalog page, ms | 292.5 | 269.1 |
| Series-list availability probes | 5,100 | 0 |
| Target series detail, ms | 187.8 | 15.0 |
| Target series detail, availability probes | 5,700 | 600 |
| Overview SELECT queries | 214 | 15 |
| Overview, ms | 395.6 | 371.1 |

Query/probe counts establish eliminated work. The small timing difference for the overall catalog/overview does not establish a broad scalability fix. Raw 1,000/10,000-book observations are in `catalog-before.json` and `catalog-after.json`.

## Browser measurements

`frontend/e2e/realstack/books-performance.spec.ts` imports real 120-unit fixtures through the authenticated API and uses three fresh browser contexts for every format. It verifies rendered prose or genuinely decoded image dimensions, four turns, a jump to unit 111, persisted progress and reload/resume. It checks at most two page image elements, page errors and 5xx failures. Attachments retain startup/turn/resume wall times, request statuses and declared bytes, state-write counts, and CDP heap/node/task/layout metrics. CDP heap excludes browser native image/decoder/GPU allocations; a two-image DOM bound is not a process-memory bound.

A second workflow imports twenty independent 28-page CBR volumes into one synthetic series, opens the series with `domcontentloaded` rather than waiting for images, and immediately clicks its read action. It records pending cover requests at click and click-to-decoded-page time across three fresh contexts. This checks cover/reader competition that a direct reader URL misses. Its profile is always unshaped loopback.

The default main-reader profile is unshaped loopback. `BOOKS_PERF_SLOW=1` selects 2.5 Mbit/s download, 1 Mbit/s upload, 40 ms latency and 4× CPU slowdown. Fresh browser contexts do not clear server caches or OS page cache. Measurements include app/bootstrap and Playwright observation costs. There is no before/after browser speedup claim without a paired baseline.

Accepted full-gate Chromium 140.0.7339.186 observations (three contexts per format, unshaped loopback):

| Format | Startup ms | Turn ms | Jump to unit 111 ms | Reload/resume ms |
| --- | ---: | ---: | ---: | ---: |
| EPUB | 580.1 | 65.4 | 60.4 | 149.1 |
| PDF | 852.6 | 146.7 | 153.2 | 156.1 |
| CBZ | 496.5 | 141.1 | 154.5 | 157.7 |
| CBR | 511.6 | 148.2 | 169.0 | 162.9 |

Startup/jump/resume columns are medians of three observations. Turns are the median of twelve correlated turns across those three contexts. There is no p95 claim. Maximum final CDP JS heap observations were approximately 15.1 / 8.3 / 8.6 / 8.7 MB respectively; these are **final snapshots, not peak process or decoded-image memory**. No reader showed more than two page image elements. Actual state-write counts were one or two across the open/turn/jump/reload workflow, including recency updates rather than isolated per-turn writes.

In the twenty-volume CBR series, click-to-decoded-page was 924.5 / 421.5 / 419.2 ms, with 20 / 10 / 10 cover requests still outstanding at click. Each volume was a 3,191,551-byte stored RAR4 archive. The first context has cold resources; later contexts reuse server work. This shows the real entry path while covers are outstanding, not a paired causal attribution to one subsystem or a production cold/solid-RAR guarantee.

Durable raw JSON is copied under `output/books-performance/browser-accepted/`; `browser-summary.json` derives the table. The full gate’s reader and cover-grid tests both passed. The shaped run also passed (setup plus the four-format workflow):

| Format | Cold-context startup ms | Turn ms | Reload/resume ms |
| --- | ---: | ---: | ---: |
| EPUB | 4,092.6 | 176.1 | 616.0 |
| PDF | 4,238.8 | 359.8 | 690.7 |
| CBZ | 4,330.8 | 552.7 | 907.9 |
| CBR | 4,313.9 | 903.6 | 895.7 |

Profile: 2.5 Mbit/s down, 1 Mbit/s up, 40 ms latency, 4× CPU slowdown; same three-context sample size and correlated-turn definition. Raw data lives in `output/books-performance/slow-results/`, with `browser-slow-summary.json` derived from it. Four-second cold startup and roughly 0.9-second CBR turns on this profile are **remaining responsiveness findings**, not a pass against a snappiness budget. Common cold app/bootstrap resources, network bytes, decoding and native worker work need a phase breakdown before attributing one cause. Large real scan images can be more demanding than these synthetic 114 KiB pages. Whole-gate results and final-source checks are recorded below. Initial attempts are excluded: test API writes initially lacked Origin and then the session CSRF token; another attempt overlapped a frontend rebuild and loaded a stale hashed JS URL (404). The harness was corrected and assets frozen before the accepted repeat.

## Remaining work, ranked

1. **Full-resolution covers and image decode bounds.** Cards lazily load original images; they have no bounded static cover rendition. The 24 MiB encoded-member limit and signatures do not limit pixel dimensions, animated GIF/WebP frame counts, or total browser-native allocations. Measure representative scan dimensions and cover bytes, then add import-time pixel/frame validation plus bounded static cover generation. The cover-grid observation also motivates deferring cover work and prioritizing visible reader pages over background cover parsing; cancellation/queue behavior needs its own controlled test. Keep reader legibility policy separate from thumbnail sizing. The new encoded-response cache cannot solve this.
2. **Cold app startup on constrained links.** Every format took roughly 4.1–4.3 seconds in the shaped fresh-context run, versus 0.6–0.9-second reload/resume. Profile critical JS/CSS/font transfers, parse/layout and reader resource phases separately. Do not infer that warm server caching fixes common cold app/network startup. Native-page lookahead could help turns only with bounded encoded-blob reuse, cancellation and pixel/decode policy; detached image prefetch under `no-store` would risk duplicate HTTP work.
3. **Catalog/series pagination is still O(collection).** Catalog loads roots, bundle members and states, performs NFKC normalization/filtering/sorting in Python, then slices. Series listing groups all manga before router pagination. At 10k, full hydration dominates. A SQL change needs regression coverage for aggregate bundle checkpoints/favorites/completion, exact normalization, stable ordering, shelf ordering and visibility; do not replace it with a semantically different shortcut.
4. **Overview still hydrates/probes the full collection.** At 10k, 10,108 availability probes remain, and visible sections are serialized through multiple queries. It also carries full shelf memberships. Request frequency is lower, but the endpoint needs state aggregation/availability profiling and bounded shelf summaries for larger collections.
5. **Native cold/solid archive work.** Native parsers retain their existing two-worker, 30 s wall / 25 s CPU / 24 MiB output limits and Linux 1 GiB address-space bound. Concurrent distinct requests can still occupy shared request threads while awaiting these workers. No admission/fairness redesign is included. Compressed solid RAR, RAR5, corrupt/encrypted production files, slow external storage and process RSS peaks need dedicated qualification.
6. **Checkpoint delivery on abrupt termination.** Normal navigation writes are coalesced, active requests use keepalive from dispatch, and pagehide requests a flush. A newer pending checkpoint still waits behind an active write, so abrupt browser/process termination cannot guarantee its delivery without server-side ordering tokens or a durable client queue. This is separate from keeping ordinary page turns responsive.
7. **Deployment/device qualification.** Synthetic loopback measurements do not prove the reported production issue is fixed. Docker/Linux runtime, physical phones, Safari/WebKit and real network loss/bandwidth variation are not inferred from Chromium/macOS observations. Preserve a rollback image before deployment using the operator guide.

## Reproduce

```bash
mise exec -- backend/.venv/bin/python scripts/perf/audit_books.py --pages 120 --runs 3 --output output/books-performance/archive-current.json
mise exec -- backend/.venv/bin/python scripts/perf/audit_books_catalog.py --ref HEAD --sizes 1000 10000 --shelves 100 --target-series 200 --output output/books-performance/catalog-before.json
mise exec -- backend/.venv/bin/python scripts/perf/audit_books_catalog.py --sizes 1000 10000 --shelves 100 --target-series 200 --output output/books-performance/catalog-after.json
mise exec -- make frontend-build
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 sh -c 'cd frontend && npx playwright test -c playwright.realstack.config.ts books-performance.spec.ts --project=journeys'
# Optional network/CPU profile; use the same command with BOOKS_PERF_SLOW=1.
mise exec -- make check
```

`--ref HEAD` means the baseline commit only while changes remain uncommitted; use the recorded baseline SHA after committing. Detailed API/service regressions remain in the existing Books suites, with an added concurrent-import responsiveness workflow. No schema migration, new runtime dependency or production deployment is included.

## Verification record

- Full backend stage: **5,018 passed / 11 skipped** in 994.68 s under contention.
- Final focused Books backend suites: **66 passed** in 44.05 s, including independently exercised byte and entry cache caps. The full gate started before the last reader/cache review refinements, so this final focused check and the frozen frontend rebuild supplement it.
- Final refreshed frontend stage: **2,486 passed across 287 files**. Source/e2e TypeScript, production build, and stylesheet/bundle budgets passed, including the refreshed frozen-reader build.
- Initial successful focused real-stack run: **3 passed**, including setup and both new reading workflows. It establishes correctness; the full-gate repeat writes the durable raw JSON evidence after the harness persistence correction.
- Docker server-version readiness probe timed out at 10 s (`output/books-performance/docker-readiness.json`). No container was created or deployed; Linux runtime remains unqualified.
- Whole-gate real-stack browser stage: **48 passed / 4 existing skips / 1 failed** in 10.5 minutes. Both new Books workflows passed and raw evidence was retained. `make check` exited 2 at this stage, so this is not a clean gate pass. The failure is the unchanged video Notes workflow (`watch.spec.ts`): after reload, the trace shows Details selected and the test times out waiting for its note seek control. No Watch implementation or threshold was changed. Failure trace/screenshot are preserved under `output/books-performance/check-failures/`.
- Shaped-network run: **2 passed** in 2.9 minutes, including setup. Cold app startup remains about four seconds on the declared constrained profile.
- Browser measurements used the static assets inventoried in `measured-frontend-assets.json`. Two subsequent completion-intent corrections preserve queued Finish commands across pagehide and keep Read again busy until its page-zero reset is accepted; these do not change the measured startup/ordinary-turn path. They passed **25 focused reader tests** and TypeScript checks. The final source refresh passed the production build, stylesheet/bundle budgets, source/e2e TypeScript, all 2,486 frontend unit tests and **592 mocked browser tests / 2 existing skips** in 8.1 minutes. This continuation used `make check -o bootstrap-test -o backend-test -o e2e-realstack`; it does not rerun or erase the initial real-stack failure. Final source hashes are recorded in `output/books-performance/source-environment.json`; measured browser asset hashes are recorded separately.
