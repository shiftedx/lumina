# Books and manga performance audit — 2026-10-18

## Scope and release provenance

EPUB, PDF, CBZ and CBR catalog/series browsing, cold reader entry, resume, page/chapter turns, progress writes, cover delivery, archive parsing, and browser/server resource use. Work is isolated on `codex/books-manga-performance`. Performance commit `0bd0584` was reconciled with deployed schema-31 release `06a4bb96`, then with the concurrently deployed branded-dropdown release `41f91734b65d7f9b0be09ee2ffa9f4ff367f3d25` in `3822006`. The merge retains both bounded reader delivery and the branded controls. Backend source is unchanged by the latter integration. The original checkout's additional uncommitted work was not copied.

The reader-entry fix is `b91faa5`; `845ec64` corrects the malformed legacy real-stack PNG fixture; `576dfd6` updates three stale native-select tests to use visible branded controls; `4dccf56` separates document-loading time from decoded resume content in the measurement harness. None of these test-only corrections relax runtime validation. Commit `550f317` separately fixes the public shell/hashed-asset cache policy without changing Books service behavior.

Retained benchmark fixtures and databases are synthetic. Live measurements retain only action names and elapsed times; no private media identifiers, paths, hostnames, credentials or content are committed. `output/books-performance/` contains ignored local evidence. Synthetic observations establish behavior under the stated workload, not universal latency or process-memory guarantees.

## Findings resolved

| Finding | Change and observable contract |
| --- | --- |
| Import inspection waits for native parsing on the ASGI event loop. | DB-free inspection runs in the thread pool. An HTTP regression holds inspection open and proves an unrelated health request completes. |
| Page turns wait for progress writes and accumulate saves. | Content changes immediately; checkpoints use a 500 ms debounce, one active write and one latest pending write. Account/book changes reject stale responses; explicit Finish and Read again retain their semantics. Initial resume still refreshes reading recency. |
| Covers serve full scan images and compete with reader parsing. | One background cover lane creates static JPEGs of at most 600 px and 1 MiB. It leaves one of the two native parser slots available for pages. Up to 32 identities can be queued/in flight; duplicate work shares a future. Requests wait at most 50 ms before returning a private, non-cacheable 202 with Retry-After. Cards defer offscreen work and retry while mounted. |
| Encoded file size alone does not bound decoded raster work. | Shared validation bounds canvas to 40 MP, animation to 256 frames and 80 MP aggregate, checks frame extents, and bounds expanded PNG text/profile metadata to 1 MiB. GIF, APNG and animated WebP covers use a static first frame. |
| Repeated page requests restart parsing/rendering. | An exact-file-identity raster LRU holds at most 64 MiB/128 entries, shares identical in-flight work and allows four uncached raster readers. HTTP authorization is still checked for every delivery. |
| Every CBR page revalidates all members. | A 128-entry in-process exact-identity validation registry permits trusted unchanged reads to stop at the target. First reads validate the whole archive; persisted manifests alone never grant this shortcut. Solid RAR may still decode preceding members. |
| Native page turns repeat network transfer and decode speculative images. | A reader-scoped encoded Blob window reuses current/previous/one-next pages, at most four entries and 48 MiB including fetch reservations. Only visible images decode, at most two DOM images. Lookahead waits for visible decode, aborts on abandoned jumps/hidden tabs/unmount, respects data saver, and revokes object URLs. |
| EPUB turns wait for the next chapter, and promotion can start the following request twice. | One abortable structured chapter lookahead scoped to loaded book/unit content. A promotion regression changes request sequence `[0,1,2,2]` to `[0,1,2]`; aborted current-chapter results are ignored. Failed chapter loads retain the last readable checkpoint. |
| Direct reader entry loads unrelated surfaces and large font files. | Home and Books route modules load on demand; direct books routes suppress speculative Watch/Settings/Gallery/command-palette imports. The common Newsreader regular+italic pair is 261,884 bytes instead of 454,724 (42.4% smaller). Common punctuation stays in that tier; original supported glyphs remain available through the complementary font range. Variable axes and license are preserved. |
| Catalog/series pagination hydrates whole collections and probes unused files. | SQL counts/pages the common catalog order. Exact normalized filters use scalar metadata and aggregate state, then hydrate the returned page. Series summaries use scalar grouping without BookItem hydration; details scope by series. Availability probes occur only where returned content requires them. |
| Overview hydrates/probes every book, and unused views request it. | Overview uses scalar totals and bounded card hydration; Continue-reading availability checks stop after enough cards, including timestamp ties. Manga/catalog/details do not request overview unless shelf choices are needed. Shelves use two bulk queries and scalar cover metadata. |
| Short asset freshness and a five-minute shell cache cause avoidable revalidation and deployment staleness. | The shell requires revalidation (`no-cache`); only content-hashed build assets receive one-year immutable caching. Unhashed scripts, fonts and service workers keep their existing freshness behavior. |

The cover cache is disposable, private on disk, atomically written, identity-versioned, and capped at 256 MiB/2,048 files. Failures have a 30-second retry cooldown. Native parsing retains two workers, 30-second wall/25-second CPU/24-MiB output limits and the Linux 1-GiB address-space limit. The shared raster validator is stdlib-only so short-lived native workers do not import ORM/metadata services.

## Measured catalog improvements

Paired 10,000-book runs compare the original starting commit with the completed implementation. Three-run wall-time medians, synthetic SQLite databases, same host. Exact differential checks cover catalog filters, NFKC/casefold search, aggregate bundle state, shelves, series and overview identities/totals.

| Operation | Before ms | After ms | Other measured work |
| --- | ---: | ---: | --- |
| Added-order catalog page | 246.7 | 14.5 | Hydrated books 10,000 → 40; availability probes 10,120 → 120 |
| Favorite catalog page | 251.6 | 33.7 | Hydrated books 10,000 → 40 |
| Search + author page | 299.0 | 64.5 | Hydrated books 10,000 → 40 |
| Series list | 136.1 | 42.2 | Hydrated books 5,100 → 0; availability probes 5,100 → 0 |
| Filtered series list | 150.7 | 57.1 | Scalar grouping preserves exact semantics |
| Series detail | 157.1 | 12.8 | Hydrated books 5,300 → 200 |
| Overview | 326.9 | 106.7 | SELECTs 214 → 10; probes 10,108 → 90; hydrated books 10,000 → 26 |

Evidence: `round2-catalog-head-baseline.json`, `round2-catalog-after.json` and differential JSON. These are local benchmark observations, not service-level guarantees.

## Cover and archive qualification

A synthetic 2,700 × 3,900 grayscale-noise scan (10.53 MP, 10,537,168 encoded bytes) becomes a 415 × 600 JPEG of 47,107 bytes: 99.55% fewer bytes and 42.3 times fewer pixels. Observed cold generation was 40.1 ms; warm disk retrieval 0.038 ms. This is a stress fixture, not a comic image-quality evaluation or peak RSS measurement. Four-format cover tests also verify static output and dimensions.

A genuinely compressed solid RAR5 fixture contains 120 identical valid PNGs. Qualification observed 75 ms inspection, 77 ms cold read, 68 ms next read, 75 ms late read, and 0.07 ms repeated cached read. The small 9,689-byte fixture exercises solid/RAR5 parsing rather than representative scan entropy. It is checked in with a service-boundary regression. The optional official RAR CLI was used only to author the fixture and removed afterward; no runtime dependency was added.

An intermediate native-worker regression from importing image services was caught and corrected by extracting the shared validator. The intermediate 572 → 69 ms CBR cold result demonstrates that correction; it is **not** claimed as an improvement over the original release.

Evidence: `cover-renditions.json`, `cover-scan-qualification.json`, `solid-rar5-qualification.json`, `rar-fixture-generator.json`, `raster-import-before.json`, `raster-import-after.json`.

## Browser and live verification

The real-stack harness imports 120-unit archives, uses three fresh contexts per format, records startup/actual first content, rapid and settled turns, jump, persisted resume, duplicate HTTP work, response timings and JS/DOM snapshots. It checks for page errors, Books HTTP 5xx responses and more than two reader page image elements. A separate series-entry test clicks while cover work remains outstanding and records actual DOM click-to-image-load independently of test polling. The constrained profile uses 2.5 Mbit/s down, 1 Mbit/s up, 40 ms latency and 4× CPU slowdown.

The final runtime build (`371e98c`, including the deployed dropdown update and integration fixes) improved default-network startup from 460.8 to 227.7 ms (50.6%). Actual first content appeared at a median 153.3 ms. Settled-turn observations were effectively flat at 131.4 ms on loopback and include assertion polling. Legacy resume increased 3.7% to 149.7 ms, while actual resumed content appeared at a median 69.6 ms; a general resume improvement is not claimed.

| Format | Default startup before ms | Integrated startup ms | Actual first content ms |
| --- | ---: | ---: | ---: |
| EPUB | 463.4 | 220.5 | 153.4 |
| PDF | 457.1 | 229.9 | 153.1 |
| CBZ | 458.8 | 234.6 | 145.7 |
| CBR | 460.2 | 232.3 | 154.5 |

In instrumented pre/post reader-entry runs, the reader request moved from approximately 302.5 ms after click to 2.0 ms. The final default run measured actual click-to-image-load at 41.0 ms. The paired older test-level click-to-observed measure fell from 403.2 to 150.1 ms (62.8%), but includes automation/polling delays. Eight covers started for the initial viewport instead of twenty; generated covers were 415 × 600 px. Fonts were loaded at click. No Books non-2xx responses or page errors occurred; default state writes remained two per workflow. Following-chapter response counts are `[1,1,1]` across the three EPUB trials, compared with `[2,2,2]` before the loaded-chapter guard.

The final constrained profile improved overall startup from 4,223.0 to 3,158.9 ms (25.2%) and settled-turn observations from 651.4 to 65.0 ms (90.0%). Native format turns improved 78.7–93.7%; the EPUB chapter-turn measure increased 5.95% and is not claimed as a gain.

| Format | Constrained startup before → after ms | Settled turn before → after ms | Actual resumed content ms, after |
| --- | ---: | ---: | ---: |
| EPUB | 3,967.6 → 2,930.0 | 118.7 → 125.8 | 491.2 |
| PDF | 4,049.1 → 3,061.1 | 262.7 → 55.9 | 541.5 |
| CBZ | 4,303.1 → 3,265.9 | 902.7 → 56.7 | 816.2 |
| CBR | 4,301.3 → 3,275.4 | 902.2 → 65.4 | 821.5 |

Constrained legacy resume/ready observations increased 18.6% overall (765.5 → 908.3 ms), while the four per-format medians increased 1.3–4.1%. The pooled observations combine distinct format distributions and scheduling variation; the aggregate does not describe a typical per-format change. Actual visible-content times above differ from the prior same-path diagnostic by −4.5%/−3.1%/−2.6%/+0.4% for EPUB/PDF/CBZ/CBR. An earlier apparent CBR regression did not reproduce once document loading and visible decode were recorded separately. A general resume improvement is not claimed. Full reloads still transfer the current page and rebuild the reader-scoped window.

Final constrained workflows made three state writes for EPUB/PDF and four for CBZ/CBR, versus a baseline pooled median of 4.5. All Books responses were 2xx; the maximum native page request count per index was two across initial entry plus a fresh reload. EPUB following-chapter counts are `[1,1,1]` across trials.

Evidence: `final-integrated-default/{summary,source-assets}.json` and `final-integrated-slow/{summary,source-assets}.json`, with their raw results. Both profiles use identical built assets from runtime source `371e98c`; the shaped run revision `cfb0716` adds only a test-readiness correction. Fresh browser contexts, three trials per format, warm server caches after first access, and uncontrolled OS caches are recorded. The private HTTP cache policy has separate public HTTP contracts and deployment verification. Earlier resume phase diagnostics remain in `resume-diagnosis-after-dropdown/`.

The authenticated live baseline found 857 ms opening from Continue reading, 521/548 ms forward turns, and a 20,020 ms opening from series detail with covers outstanding. The original reading position was restored after the baseline. Paired post-deployment observations and rollback/health evidence are retained with the release PR; synthetic results alone do not establish that the live issue is fixed.

WebKit real-reader workflows passed for all four formats. The pinned Firefox executable fails to launch an isolated blank page on this host (OS sandbox/graphics initialization), before Lumina loads. Security protections were retained; Firefox automation is unqualified on this host. The authenticated live browser provides a separate real-device path, without implying broad Firefox certification.

## Transport and cache delivery

The existing backend already compresses text/JSON with gzip level 3 and bypasses compressed images, media, WOFF fonts, partial responses and event streams. The real-stack harness uses that backend compression. Public-edge observations also confirm negotiated compression for HTML, JavaScript and CSS. No extra compression middleware or uncompressed-network latency adjustment is needed. The separate cache-policy change gives the shell revalidation and only content-hashed assets one-year immutable freshness; public HTTP tests and image smoke checks verify that policy. Evidence: `edge-compression-audit.json` and the ignored deployment packet.

## Practical limits

- Exact normalized catalog search, series grouping and affinity totals still scan scalar collection metadata. Shelf responses intentionally retain their complete ordered membership IDs. These costs are measured and substantially reduced, not constant-time at arbitrary scale.
- The 48-MiB reader and 64-MiB server bounds account for encoded cached data. They do not bound total process RSS, transient fetch/decoder allocations, GPU memory or full-resolution visible-page decode.
- If fewer than twelve recent books are available, overview may probe all active candidates. Solid archives and slow storage can still require substantial cold decoding.
- Abrupt browser/process termination cannot guarantee delivery of a newer checkpoint queued behind an active write. Ordinary navigation remains responsive; failed saves remain visible.
- Missing or permanently invalid cover inputs retain a placeholder. Failed jobs cool down rather than launching repeated background work.

## Reproduce

```bash
mise exec -- backend/.venv/bin/python scripts/perf/audit_books.py --pages 120 --runs 3 --output output/books-performance/archive-current.json
mise exec -- backend/.venv/bin/python scripts/perf/audit_books_catalog.py --ref a4e01dad62fadc6186ca27ea0fbda8e3ea0d3674 --sizes 1000 10000 --shelves 100 --target-series 200 --output output/books-performance/catalog-baseline.json
mise exec -- backend/.venv/bin/python scripts/perf/audit_books_catalog.py --sizes 1000 10000 --shelves 100 --target-series 200 --output output/books-performance/catalog-current.json
mise exec -- make frontend-build
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 sh -c 'cd frontend && npx playwright test -c playwright.realstack.config.ts books-performance.spec.ts books-browsers.spec.ts --project=journeys'
# Repeat with BOOKS_PERF_SLOW=1 for the shaped profile.
mise exec -- make check
```

The optional `--solid-rar-tool /absolute/path/to/rar` fixture-authoring option is documented by `audit_books.py --help`. It is not needed to run the checked-in solid-RAR regression.

## Integration regressions

The broad gate also checks routes outside Books. Lazy Home loading exposed a lost cross-route Edit home command; one pending intent now survives until the Home listener mounts and coalesces repeated requests. The complete 13-workflow Home-edit browser file passes, including both themes at phone and desktop widths.

The concurrent dropdown release exposed a mobile people-editor wrapper sizing regression and closed-choice interception of Settings D-pad row navigation. Those behaviors retain the existing layout and keyboard contracts. Mobile gallery filter triggers also fill their shrinking flex wrappers, eliminating measured 1–19 px horizontal overflow at 390/320 px without clipping content. Browser checks now measure visible branded triggers and popup options instead of hidden native backing fields or an obsolete direct text node; touch-size, axe and overflow assertions remain. The short Watch fixture also requires accounting for visible progress actions when checking focus order.

## Final release evidence

Final backend source (`550f317`, unchanged by later frontend fixes): **5,143 passed, 11 skipped** in 1,245.57 seconds. The public cache-policy HTTP contracts also pass (six tests). Final runtime source is `371e98c`; frontend/e2e typecheck, production build/style budgets and **2,585 tests in 296 files** pass. Focused browser checks pass all 13 Home-edit workflows, 20 playback/People/Settings/Watch workflows, eight direct gallery overflow checks and all four full mobile axe/touch/overflow route matrices. The real-backend integrated gate passes **50 tests/5 skips**. Final default and constrained paired measurements pass; the constrained targeted run records **4 passes/1 Firefox skip**. The final complete mocked suite passes **622 tests/2 skips**. Its earlier overlay-readiness race was corrected by explicitly waiting for fixture data; the unchanged 0.01 shift threshold also passes three consecutive focused repeats. The final frontend gate exits zero with typecheck, production build/style budgets and all unit/browser tests passing. Backend and real-stack stages were retained from their passing runs because their exact inputs are unchanged; `gate-input-equivalence.json` records that comparison, and all 71 rebuilt assets match both final measured profiles.

The malformed legacy real-stack PNG fixture was replaced with an existing valid fixture; its workflow now requires actual image decoding. Firefox is unqualified on this host and remains an explicit skip; Chromium and WebKit are exercised. Deployment, rollback retention and authenticated live paired observations are recorded with the release PR after cutover; the local ignored deployment packet retains the machine-readable evidence without private identifiers.
