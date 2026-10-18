# Backend and all-media performance audit

Status: available native audit completed. Docker/Linux workloads were blocked before container creation. Requested 2026-10-17. User authorized continuing under other workloads without another review. Contended results are observations, not quiet-host acceptance baselines.

## Prioritized findings

1. **Profile and reduce transcript candidate SQL.** Six-member search reached 415 ms median / 1,136 ms p95. In the separate instrumented route profile, `moment_hits` accounted for about 70% of cumulative route time; the plan drives broad visibility indexes before bounded transcript hits. A read-only paired variant reduced representative viewer query medians from 69 to 22 ms with identical ordered results for the six synthetic users. Preserve access rules and deterministic ordering in any optimization; restricted policies/Linux remain unqualified.
2. **Choose starting video quality using available bandwidth and capacity.** On the 2.5 Mbit/s synthetic link, 1080p remux started in a median 16.6 seconds and stalled about 13.1 seconds in a 20-second window. Explicit 480p conversion started in 2.0 seconds with no stalls. This is a measured opportunity for one source/link, not a qualified universal automatic ladder.
3. **Investigate title and scrolling budget misses.** Offline title/recommendation checks still exceeded existing budgets under contention. Home/Live long-task misses and a small warm-All readiness miss remain, even though the extended gallery scroll checks passed and the instrumented Home repeat improved.
4. **Fix concrete UI geometry and loading behavior.** Chromecast's preview disclosure is 18 px high against existing 36/44 px target contracts; phone watch tabs overflow by 8 px. Watch tools can reset to Details when title metadata arrives. The single overlay CLS miss is intermittent and its cause remains unresolved.
5. **Profile large distinct queue hydration if it matters to usage.** A 1,979-distinct-track queue GET+PUT took 324 ms median (N=3). The 5,000-entry repeated-track case cannot stand in for unique-track scale.
6. **Close the unprobed long-audio allocation gap.** Under load, background probing deferred metadata. Source inspection shows the early decoded-size guard needs those fields and otherwise checks decoded size after allocation; native allocation size was not measured.

No production optimization was applied during the baseline audit below. The subsequent authorized remediation and its independent verification are recorded in [the remediation report](audit-remediation-2026-10-17.md). Existing range chunking already measured substantially better than the legacy chunk in the warm loopback comparison. These priorities describe observed costs and correctness findings, with contention and coverage limits below.

## Scope and method

Audit current backend workflows and product playback/viewer branches, including video, remote streams, live/DVR, browser music, images and book readers. The coverage matrix records fixture and infrastructure gaps; this is not an exhaustive codec/device compatibility certification. Preserve raw results and unsuccessful outcomes. Identify measured bottlenecks before proposing production changes. Synthetic fixtures and disposable data roots keep household services and private media outside the workload.

The current implementation is based on commit `a4e01dad62fadc6186ca27ea0fbda8e3ea0d3674` with uncommitted integration/playback changes. Each run records a production-source fingerprint and benchmark-source fingerprint rather than treating that commit alone as its source identity.

Reference host: Apple M4 Max, 16 physical/logical CPUs, 64 GiB RAM, macOS 27.0.1; pinned Python 3.12.14 and Node 22.17.1. FFmpeg, SQLite and run-specific source versions accompany the raw evidence. Final installed-browser metadata (`browser-versions.json`) records Chrome 155.0.8059.84, Playwright 1.55.1 and bundled WebKit 26.0/revision 2203. This is a final installed-version observation, not a per-run `browser.version()` capture.

Deployment targets requested: native macOS and local Docker/Linux. The local Docker VM is Linux/aarch64 with 16 CPUs and about 7.75 GiB available RAM; each audit container is limited to 4 CPUs and 4 GiB. Browser clients remain macOS Chrome against the Linux backend. This does not qualify a Linux browser or hardware video encoder. The dedicated image uses the production Dockerfile with only build-time llama compilation parallelism reduced to four jobs in a temporary Dockerfile; runtime code and build options are unchanged.

- Run host-sensitive cases sequentially under `/tmp/lumina-perf.lock`. The lock excludes cooperating performance jobs, so also record external CPU activity before and during each run.
- Browser cold means a new context/profile without its HTTP cache/service worker. Application cold means a fresh process and declared application caches. The OS filesystem cache is **not flushed**; its state is warm/unknown.
- Retain raw wall times, statuses, query counts, bytes and correctness outcomes. Three browser runs establish a small-sample median, not a tail-latency guarantee. Household JSON reports suppress p95 below 20 samples and p99 below 100; even those counts remain observational estimates.
- Sample total descendant RSS and process roles at one-second intervals. This includes benchmark drivers and servers; it is an observed peak, not an exact instantaneous maximum. JS heap excludes important native audio/decoder/GPU allocations. Process CPU percentages are OS observations rather than integrated CPU seconds.
- Distinguish actual decoded frames/audio-clock advancement, fetched media bytes, scheduled audio boundaries and physical output. A mock or advancing browser clock does not certify an external receiver or audible speaker output.

### Fixture and measurement controls

Backend workflow fixtures use 20k library items, 300 follows, 5k notes, 500 transcripts with 1,000 cues each, 200 collections, 50k progress rows and 5k jobs. The title budget harness uses the production-scale title seed (default 1,735 movies, 850 series and 37,500 episodes, plus anime/music). Browser gallery budgets use the separate medium seed: 1,735 movies, 60 series, 2,000 episodes, 10 anime series, 40 albums and 400 tracks, with 7,473 ready rendition rows. These synthetic sizes are benchmark inputs, not household inventory facts.

Browser image measurements cover delivery/rendering of **prepared renditions**, not cold generation of thousands of images. That fixture renders each sample/type once and hard-links the output. It drops its deliberately unsupported AVIF entries; native FFmpeg uses JPEG because libwebp is unavailable. Consequently those budgets cannot qualify AVIF/WebP/logo support or production cold-art generation. The separate rendition component measurements cover the supported JPEG work.

Household perf mode disables per-member request throttling and uses canned provider extraction with a loopback-only network guard. Controlled title computation measurements retain limiter bookkeeping but raise ceilings and replace background external search with an empty offline response; this prevents a 429 or attempted provider request from being mistaken for title computation cost. Security/rate-limit correctness remains in the functional suite, not inferred from these throughput runs.

Network profiles are distinct: real-stack gallery/TTFF budget “LAN” means 2 ms latency with unlimited throughput, while its “slow” profile means 20 Mbit/s down, 5 Mbit/s up and 40 ms latency, normally with 4× CDP CPU throttling. The separate local-conversion experiment uses 2.5 Mbit/s down/50 ms latency without that CPU throttle. Extended video/reader audits use unshaped loopback. These profiles must not be combined into a single “slow network” result.

## Coverage and execution record

| Family | Executed coverage | Qualification |
| --- | --- | --- |
| Household backend | One/six-member real HTTP read stress; 20k items | 6,659 successful requests; synthetic offline providers |
| Titles/recommendations | Lists, filters, facets, details, Home, cold/warm recommendations, event/suppression budgets | Offline repeat: 5 passed / 3 failed under contention |
| Integrations/acquisition/import | 10k Arr/Lidarr loopback snapshots, local reads, operation/reconciliation, 32-file terminal import | Real application HTTP; installed household services excluded |
| Auth/policy/writes/music backend | Login, visibility, CAS conflicts, logical/distinct queues, export/replay | Fake provider; no recorded SQLite busy timeouts |
| Backup/admin | Backup creation, offline restore/integrity | Disposable synthetic database; N=1 |
| Books/art/files | EPUB API, JPEG rendition, 64 MiB range correctness/performance | WebP/logo unsupported on native FFmpeg; warm/unknown OS cache |
| Live/jobs/resources | HLS/DASH rolling-map components, threaded admission/cancellation | Component/fake source coverage; relay/DVR capture throughput unqualified |
| Local video | Chrome and WebKit decode, far seek, resume, queues; extended codec/track matrix | 27 trials retained; MPEG-4 exact-target frame unqualified; no hardware encoder certification |
| Remote/live frontend | Synthetic DASH, HLS VOD and rolling current-edge HLS | Page-intercepted upstream; real relay/auth/provider unqualified |
| Browser music | Four codecs, decoded/WAV/native fallback/offline, scheduled transitions | Clock/event proxies; metadata-ready oversized track auto-stream/seek passed |
| Images/gallery | Real-stack first-screen/sharp/hero, full-wall scroll, slow network, long tasks | Prepared JPEG renditions; full-wall extended checks passed |
| EPUB/PDF/CBZ/CBR | Real rendered content, turns and bounded navigation soak | Three contexts/format; CBR stored RAR4 only |
| Audiobooks | Product inventory checked | Dedicated surface absent; no invented benchmark |
| Sonos/Chromecast/Apple TV | Existing contract suite and documented integration boundary | No physical output qualification; user has no Chromecast |
| Docker/Linux | Image built; all three families retried | Docker Desktop HTTP 503 before container creation; no Linux runtime result |

## Harnesses and evidence

Existing entry points: `make perf`, `make perf-titles`, `make e2e-realstack`, `make e2e-browsers`, and `make playperf`. See [verification](../VERIFICATION.md) for their current scope and historical reference values. Historical numbers belong to their original host/workload; they are not this audit's baseline.

`scripts/perf/audit_run.py` records run environment, quietness and sampled process resources. It wraps commands inside the existing lock. `scripts/perf/load.py` now supports one to six members and `--json` raw-sample retention; transport failures remain measured failures instead of disappearing with a worker thread.

Isolated backend, reader/music and video harnesses execute the workflows in the matrix. Raw artifacts belong under `output/performance-audit/`; run commands, validity, sample counts and findings are retained with the evidence below.

### Environment obstruction and smoke runs

The initial guarded probe (`output/performance-audit/baseline-probe.json`) refused execution: process sampling timed out and the Mac reported load averages around 765/849/809 on 16 CPUs. A repeated probe after the Docker build also refused execution. The shared performance lock was occupied at subsequent checks; it later became available and the user authorized contended execution. No baseline latency is inferred from those refusals. `host-obstruction.json` retains interval CPU/disk, load and memory observations. Functional smoke runs during image preparation are explicitly contended diagnostics, not isolated performance evidence. No unrelated process or container is stopped by this audit.

`audit_run.py` bounds a blocked process-sampling call and records failure rather than inventing memory/CPU values. In Docker mode it retains the VM CPU observation separately from external-process contention, records the other running container inventory, and pairs host observations with per-container cgroup counters from `audit_docker.py`.

Linux execution specs are retained in `output/performance-audit/{backend,readers,video}-linux-spec.json`. They mount only benchmark helpers, synthetic test helpers, disposable data and output. Browser ports publish to host loopback; the finite backend workload has no external Docker network.

The earlier [local-conversion experiment](../research/local-conversion-network-2026-10-17.md) is provisional because unrelated tests ran concurrently. Its nine-trial repeat is recorded below; it also ran under contention and remains observational rather than a quiet-host acceptance baseline.

## Qualification limits

Local fakes and loopback media measure Lumina's own cost. They do not establish latency or compatibility for real arr/downloader installations, external media providers, physical Sonos/Chromecast outputs, household Wi-Fi, mobile hardware, or hardware transcoding. Unsupported metrics and unavailable fixtures are recorded as gaps, never as zero cost or a passing result. The bounded video corpus does not qualify 4K, HDR/Dolby Vision, AV1, all multichannel/passthrough combinations, bitmap/ASS subtitle rendering, every container or hardware encoding. Reader fixtures do not qualify DRM or every PDF/archive variant. Full model/ASR inference, cold mass-art generation, storage/NAS saturation and long-duration concurrent encoder/capture saturation remain outside measured coverage. qBittorrent/NZBGet/Prowlarr protocol behavior has functional fixture coverage, but this audit has no separate timed baseline for those installed services or their complete refresh paths; the timed connector workload is explicitly Lidarr.

## Findings

### Runtime validity and Linux infrastructure

Native workloads executed sequentially with external workload activity recorded. Production source stayed frozen at SHA-256 `b65bd28c34562ab0c9f14e23d0506debf764a9875bf709c070065b6068511ae5`; the baseline commit plus uncommitted integration/playback changes define the audited build. Benchmark corrections changed only fixtures, selectors, instrumentation and documentation, retaining failed attempts and unchanged product budgets. Final review verified the same frozen production fingerprint in all 44 wrapper attempt records, including unsuccessful and instrumented attempts; every executed wrapper recorded external workload activity. These are not 44 independent accepted benchmarks. There is no quiet-host or cross-platform comparison.

All three Linux families retried and failed before container creation. Direct Unix-socket API probes returned **HTTP 503: Docker Desktop is unable to start**; normal Desktop start reports it is already running. This is an infrastructure failure, not measured Lumina performance. Evidence: `{backend,readers,video}-linux/retry-resources.json`, `docker-direct-api-probe.json` and `docker-start.json`. No unrelated job/container was stopped and no destructive Docker recovery was attempted.

Backend composite workflow wall times include every subcall; response SQL/bytes/status refer to the returned response. C6 samples count requests rather than independent concurrent rounds. The 8 MiB safety check occurs after httpx buffering. Import terminal follow-up below supersedes the original admission-only scope. Model/ASR inference throughput has no qualified weights/runtime fixture and remains unmeasured.

The Linux fixture runner uses the runtime image with a custom benchmark entrypoint, an unprivileged UID/GID matching the synthetic bind mounts, and disabled periodic healthchecks. It excludes operator entrypoint/chown startup cost. Docker infrastructure currently prevents execution, so these settings are declared preparation rather than a measured deployment result.

### Household read stress: completed, contended

The same disposable 20k-item household fixture ran for 60 seconds at one and six members, with a deliberately aggressive mean think time of 20 ms. This is a stress mix, not a claim about six people issuing normal interactive traffic. All **1,568 C1 and 5,091 C6 requests returned 2xx**. Backend endpoint results were written before the later browser UI harness failed on an outdated transcript selector; that failure does not erase or convert the HTTP results into a successful browser run.

| Endpoint | C1 median / p95 ms | C6 median / p95 ms | C1 / C6 samples |
| --- | ---: | ---: | ---: |
| Local search | 146 / 210 | 415 / 1,136 | 50 / 193 |
| Library FTS page | 28 / 37 | 67 / 206 | 41 / 194 |
| Library title | 27 / 33 | 48 / 114 | 65 / 200 |
| Collections | 22 / 65 | 46 / 103 | 52 / 189 |
| Imported library | 24 / 54 | 53 / 99 | 52 / 184 |

Backend end RSS was 253 MiB after C1 and 507 MiB after C6, in separate process runs. Those two observations do not demonstrate a leak. Raw endpoint latency/status/query-count/body-byte samples are in `household-native/{c1,c6}.json`; process contention is in `household-native/run.json`. Search is the strongest profiling candidate in this mix. The direct-route CPU profile below identifies transcript candidate SQL as the dominant measured cost; it excludes HTTP/auth/threadpool overhead.

### Local search CPU profile: completed, contended/instrumented

`search-profile/{raw.json,cumulative.txt}` profiles 63 direct calls to the ordinary search route over the 20k household seed (three queries, first call plus 20 repeats). This excludes HTTP/auth/threadpool cost and includes cProfile/event-listener overhead; its wall times must not replace the HTTP baseline. The returned mode was `hybrid`, using the deterministic offline token/intent encoder and ranking implementation rather than qualified model inference.

The route accumulated 8.95 seconds; `_candidate_documents` accumulated 8.41 seconds, and `LibrarySearchService.moment_hits` 6.31 seconds (**about 70% of route cumulative time**). SQLite cursor execution accumulated 7.09 seconds across the route. Semantic ranking and synchronization accumulated only 0.245 and 0.191 seconds respectively. These cumulative categories overlap and must not be added together. This identifies transcript candidate SQL as the primary optimization target rather than treating semantic inference as the presumed bottleneck.

Repeat medians with instrumentation were 168 ms wall / 156 ms process CPU for `river`, 171/166 ms for `river forest`, and 36/36 ms for a no-match query. SQL execute medians were 131, 141 and 33 ms respectively; fetching and ORM hydration are outside that SQL timer. The final transcript query samples alone took 105, 110 and 34 ms. Its CTE ranks transcript FTS hits using BM25 and `rowid`, limits candidates, then joins/filter-checks visible present items and orders the result. A read-only structural EXPLAIN plan (`search-query-plan/plan.json`) shows a MULTI-INDEX OR driving visibility/owner library indexes before joining the bounded FTS hits. The unrestricted visibility predicate therefore remains the query-plan target. A separate paired query variant experiment is recorded below; restricted-access policies and Linux SQLite remain unqualified. Any query change must retain member visibility/access rules, deterministic tie order and bounded candidate/result limits.

### Search query variant: completed, contended, read-only experiment

`search-variant/{run.json,raw.json}` compares the retained production SQL with a predicate-only variant wrapping the unrestricted visibility/owner columns in `coalesce`. SQLite 3.53.1 then scans bounded transcript hits and probes `library_items` by primary key instead of driving broad visibility indexes. The database was opened read-only; production source and rows were unchanged. Each of 18 user/query cases alternated original/variant order for 20 samples per variant, fetching and comparing exact ordered rows on every execution. All comparisons matched (six users: one admin, five viewers; three queries; candidate cap 500/result cap 200).

| Role / query | Original median ms | Variant median ms |
| --- | ---: | ---: |
| Admin / river | 84.2 | 22.8 |
| Admin / river forest | 96.0 | 34.4 |
| Admin / no match | 26.9 | 0.12 |
| Viewers / river | 68.9 | 22.0 |
| Viewers / river forest | 81.8 | 32.3 |
| Viewers / no match | 21.1 | 0.11 |

Viewer rows summarize the median of five per-user medians, not a pooled distribution; raw samples and per-user p95 remain in JSON. This warm-cache SQL execute+fetch microbenchmark excludes HTTP, auth, ORM and semantic ranking and includes external host contention. It establishes a promising query-plan change on this fixture, not an application-wide speedup. Restricted member policies, null/legacy ownership and visibility edge cases, deterministic ties, broader FTS distributions and Linux SQLite 3.46.1 need meaningful contract/plan checks before a separate production change.

### Offline title/recommendation repeat: completed, contended

`titles-native-offline/` retained request samples, XML and logs: **5 passed, 3 failed** in 82.6 seconds. The entire benchmark process uses an empty offline extractor, including background refresh threads, and keeps rate-limiter bookkeeping with raised ceilings. This corrects the earlier fixture's 429 and blocked background-provider attempts without changing production behavior or budgets. Warm budget measurements use 20 samples after two warmups and the existing 19th order statistic for p95; first/cold observations are reported separately. Host load was approximately 103–110 on 16 CPUs; these are observed budget misses, not quiet-host acceptance verdicts.

| Operation | Observed ms | Existing budget ms |
| --- | ---: | ---: |
| Series + 4K, p95 | 465 | 400 |
| Home Next Up, p95 | 215 | 150 |
| Similar titles, p95 | 217 | 120 |
| Diagnostics recommendation added work | 130 | 80 |
| 10k-title similarity repeat, p95 | 139 | 120 |

The separate 10k title-row check passed with median 145 ms / p95 234 ms against 250 ms. The earlier controlled attempt (4 failed / 4 passed) and initial rate-limited attempt remain preserved rather than replaced. Existing live/channel/cache annotation budgets passed in this offline repeat. Profiling these title paths is the next step before choosing SQL/cache changes; no production fix is included.

### Native workflow baseline: completed, contended

The successful first backend run used real HTTP against the ordinary application, with loopback Arr/Lidarr fixtures and a fake export provider. Every measured workflow had zero unexpected errors. The CAS conflict is an expected 409 contract, and replay was verified not to dispatch again. These are current-build observations, not before/after improvements.

| Workflow | Median ms | Samples / scope |
| --- | ---: | --- |
| Login | 58.8 | 20 |
| Visibility-filtered listing | 5.9 | 20 |
| Music manifest | 3.5 | 20 |
| Listening session replace | 8.5 / 38.8 | C1 20 / C6 120 requests |
| Expected stale-revision conflict | 14.2 | 20 |
| Export fresh / replay | 5.4 / 5.3 | C1 20 each |
| Export fresh / replay | 21.7 / 21.9 | C6 120 requests each |
| Arr paged observation read | 10.5 / 36.7 | C1 20 / C6 120 requests |
| Lidarr paged observation read | 7.3 / 34.4 | C1 20 / C6 120 requests |
| Arr refresh, 10k items + queue | 1,793 | 3 |
| Lidarr refresh, 10k queue | 128 | 3 |
| Arr operation + reconcile | 34.4 | 1 composite |
| EPUB reader / chapter / progress | 4.2 / 3.3 / 4.7 | 20 each |
| Backup create + verify | 1,334 | 1 |
| Offline restore + SQLite integrity | 3,166 | 1 |

The initial queue case used 5,000 logical entries repeating one track ID (84 ms for GET+PUT, N=1). It must not be presented as a 5,000-distinct-track hydration benchmark. A separate run adds the seed's available distinct tracks and records that exact count. Backup/restore operates on the disposable synthetic database and fixture media, not a large household archive. Full paths, raw samples, statuses, decoded body bytes, SQL counts and cumulative write-admission telemetry are retained under `backend-native/`; no database busy-timeout was reported in that run. Expected CAS conflicts appear in writer error counters and must not be counted as unexpected HTTP failures.

### Distinct queue and terminal import follow-up: completed, contended

`backend-native-distinct/backend-audit-20261017T232516Z.json` completed with zero unexpected errors and no recorded SQLite busy timeout. It adds a **1,979-distinct-track** queue GET-revision+PUT workflow: median 324 ms across three trials (maximum 504 ms). The repeated-one-track 5,000-entry case was 164 ms in this separate run, N=1. Neither number establishes a 5,000-unique-track result or a tail percentile. Large distinct queue hydration/serialization is a secondary profiling candidate, with scale declared rather than inferred from logical entry count.

The scanner indexed all 32 new one-second PCM WAV fixtures with zero failed files and complete scan coverage. Start to observed terminal state was 246 ms (100 ms polling; N=1), ending at `needs_confirmation` because the existing synthetic root has 9,000 missing-file candidates. That is the expected missing-item safety boundary; the audit does not confirm removal. Admission alone was 8.3 ms. This measures small-file scan/index/reconciliation through its public terminal policy state, not completion of all background probing/enrichment or large-file/remote-storage throughput. First-run admission-only evidence remains preserved separately.

### Resource policy components: completed, contended

`resources-native-rerun/raw.json` passed 20 C1 rounds and 20 C6 rounds (120 C6 admission/release attempts). Median round wall/process CPU were 0.38/0.38 ms at C1 and 5.97/6.62 ms at C6; C6 p95 round wall was 8.48 ms and maximum 35.44 ms. Expected member/bandwidth ceilings returned 429; direct admission followed by cancellation released the reservation. This invokes the production policy/admission logic with isolated SQLite and real worker threads, but it does not spawn encoders, transfer media, measure HTTP disconnects or establish saturation throughput. The first attempt's denial fixture incorrectly expected LAN-compatible MP4 to transcode; that harness assumption was corrected, with the unsuccessful evidence retained in `components-native/`.

### Existing range-delivery configuration: measured, contended

A 64 MiB body was read in 20 trials per case, with byte correctness checked by SHA-256. The actual HTTP range case used median wall/process CPU of 30.3/43.6 ms with the configured chunk versus 106.9/102.8 ms with the legacy 64 KiB chunk. The file/cache and HLS-range component cases also favored the configured chunk. The HTTP harness runs its server and client in the same process, so CPU includes both and SHA-256 response verification. It discards one warmup per configuration. This supports the existing implementation; no production optimization was applied by this audit. These are warm-cache loopback results, not isolated backend CPU or WAN throughput.

JPEG rendition components completed 20 trials per supported size. Poster mean 91 ms, backdrop 71 ms and still 61 ms include encoder/component work under contention. Logo/WebP was skipped because the native FFmpeg build lacks libwebp; its absence is not a zero-cost result. Synthetic watcher bookkeeping completed: relist of 20k entries/500 changes had median 7.9 ms, and removal of 500 subtrees 0.73 ms (five trials each). This excludes actual filesystem traversal and network-mounted storage.

### Completed: production artifact footprint

The dedicated Linux image built successfully, including its production frontend typecheck, asset/license build and stylesheet budgets. `output/performance-audit/bundle.json` retains every JS/CSS/HTML/WASM artifact's byte size, independent gzip size and SHA-256, extracted from image `sha256:75dfcda133a2bd3c2a5478946cb96ebcadaa0f20e7ab0c3ae90a0d4a5a3addc5`. Gzip uses level 9 and mtime 0. This deterministic measurement does not need an idle host.

| Artifact | Uncompressed bytes | Gzip bytes |
| --- | ---: | ---: |
| Main application entry | 803,332 | 245,130 |
| Shared vendor | 223,110 | 70,583 |
| DASH library | 855,189 | 254,731 |
| HLS library | 523,075 | 161,251 |
| Watch surface | 108,208 | 34,873 |
| Book surface | 41,321 | 11,994 |

The entry HTML explicitly references four JS artifacts totaling **332,892 gzip bytes (325.1 KiB)**. This is the sum of independently compressed artifact sizes, not an observed page transfer total, parse cost or loaded-route weight. Fonts, CSS, API bodies, headers and later dynamic imports are separate. Source inspection confirms dynamic HLS/DASH imports in `localPlayer.tsx`/`remotePlayer.tsx`; their full sizes must not be added to every page's initial payload. The production build reports CSS total 66.4/68 KiB gzip and book surface 11.7/15 KiB gzip, both within current budgets.

The entry's download/parse contribution remains a profiling candidate alongside the measured browser results. No bundle split or playback optimization is justified by byte counts alone.

### Household browser workflow: completed, contended

`household-ui-native/rerun-2/{run.json,ui.json}` completed without browser errors. Cold Home FCP/LCP were 132/132 ms and shelves ready 167 ms. Twelve single route observations ranged 55–79 ms except music at 308 ms; these readiness markers are workflow proxies, not repeated tail distributions. The transcript rendered its first 100 cues in 39 ms. The supported `/library/youtube` scroll surface accumulated 1,840 cards / 30,207 DOM nodes / 39.4 MB JS heap; average page readiness was 138 ms and measured main-thread work 2.55 seconds across the scripted sequence. The scripted traversal back to the top took 3.35 seconds at 600 pixels per animation frame; this is a traversal duration, not latency for a user scroll-to-top action. These are bounded synthetic observations with no media playback/provider work or multi-hour leak qualification.

The initial UI harness used an outdated transcript selector; its first repair then waited for the old root-library grid after the product moved the grid to `/library/youtube`. Both unsuccessful attempts retain partial route/transcript metrics and fatal phase evidence. The corrected workflow uses the actual current surface; no product source or threshold was changed to make it pass.

### Browser budgets: first broad pass, contended

The 54-case real-stack run completed **39 passed, 11 failed, 4 skipped** in 13 minutes. Eight functional playback failures used a stale broad Play selector: saved traces show the target was the persistent music dock's “Play music” control. A corrected selector and fresh sequence including prior music playback subsequently passed the affected playback cases. These eight outcomes are harness failures, not proof that video decode failed. Harness edits made while this runner was active did not replace the already compiled test modules; the saved trace selectors are authoritative for that attempt.

Three actual budget misses remain recorded without raising thresholds:

- Warm All landing's first two chapters: 1,238 / 834 / 1,012 ms; median 1,012 ms versus 1,000 ms budget.
- Home scroll long tasks: 4 / 5 / 6; median 5 versus 2 budget.
- Live rail fling long tasks: 11 / 0 / 4; median 4 versus 2 budget. Live/channel API data is deterministic frontend fixture data, not a real upstream provider.

Direct first-frame medians passed (LAN 454 ms, shaped slow 447 ms, three samples each). The 160×90 conversion pipeline first-segment median was 567 ms; this is segment delivery, not H.264 decode or a full-resolution transcode benchmark. Movie wall LAN cold first-screen/sharp medians were 718/841 ms, warm 360/466 ms, and shaped slow cold 1,051/1,377 ms. Movie-wall 4× CPU scroll had 4/1/1 long tasks, median 1 within budget; its individual outlier is retained. Raw logs/JSON and failed traces are under `realstack-native/`. Extended gallery scrolls subsequently passed all four cases (plus setup): no failed images across both walls, warm and slow-cold; All landing scroll long tasks were 0/0/0 in both profiles. The separate instrumented Home repeat passed with 0/1/1 long tasks, without erasing the original miss. Its CPU profile includes substantial Playwright DOM snapshot work (`visitNode`/`visitChild`) and idle/program samples; it does not establish an application hotspot. The profile and self-time summary are retained in `home-cpu-profile/`.

### Additional functional and layout findings

The full mocked workflow gate finished 585 passed, 16 failed and 2 skipped in 18.8 minutes. Thirteen failures share the same concrete cause: `frontend/src/features/watch/ChromecastPanel.tsx` renders its “Chromecast preview” `<summary>` with an 18 px height. The project's existing target-size contract is 36 px for fine pointers and 44 px for touch/remotes. Saved failure geometry identifies this disclosure at narrow, zoom-equivalent and desktop surfaces; this is a control-size finding rather than proof of horizontal overflow. Production source remains unchanged during measurement; the finding is retained for a subsequent fix.

The overlay CLS gate observed 0.0536 versus a 0.01 budget while opening the recommendation menu. This is an associated layout shift, not established causation by the menu itself. A short diagnostic repeat passed with CLS zero for every overlay and no shifted-element entries. The original miss is intermittent; this repeat does not establish its cause or erase it. The original failed result remains under `mocked-e2e/`.



The remaining mocked failures are one overlay CLS miss above, one phone watch-tab overflow (342 px scroll width versus 334 px client width), and one stale host-launch assertion. The latter matches every button beginning “Open” or “Reveal”; its snapshot shows browser-only “Open listening room” and “Open queue” controls in the persistent music dock. That failure does not demonstrate a restored OS-launch API. The original negative removed-route mock still rejects calls to those endpoints. The two geometry findings and CLS miss are retained as actionable audit findings; the broad name assertion is classified as a harness false positive. No production fix is mixed into the frozen performance baseline.

The corrected real-stack playback sequence completed ten passing cases, including real WebKit remux/conversion frames, far seek, direct WebM, watch-queue autoplay and prior music playback. One notes workflow failed after reload while the snapshot showed the Details tab selected and “1 note you can read”; that is evidence the note persisted, with a tool-selection readiness race. The targeted repeat passed (setup plus workflow) after waiting for real title metadata before selecting Notes. Source inspection shows the fallback tool tree remounts when metadata arrives and resets selection to Details; this transient UX behavior remains a finding, while note persistence and seek are verified. The original eight broad-Play-selector failures are covered by the passing corrected playback cases.

### Browser music and readers: completed, contended

The retained corrected run `readers-native/run-rerun-5.json` passed all three Playwright cases in 95.8 seconds (setup plus music and readers). Four distinct synthetic 12-second files traverse FLAC, MP3, AAC-in-M4A and Opus. The browser's “Source audio” label is not used as codec proof; fixture/probe metadata identifies the codecs. Music observations are event/clock proxies, not audible onset:

| Music operation | Observed time | Scope |
| --- | ---: | --- |
| Decoded cold startup | 134 ms median | 3 contexts; first scheduled/clocked playback proxy |
| Server WAV fallback | 383 ms | One induced decode failure; actual `audio/wav` response |
| Explicit native stream | 43 ms | Detached media element's `playing` after explicit action; two induced decode failures |
| Saved offline playback | 55 ms | Scheduled playback proxy |

Gapless/crossfade checks verify that a successor buffer is scheduled for a future AudioContext boundary. They do not prove acoustic continuity, physical output latency or a perceptual crossfade. Native-stream error/fallback paths above deliberately induce decode failures; a separate long-form fixture checks the natural memory-budget path. JS heap excludes native AudioBuffer/decoder allocations, and neither short audio nor short navigation soaks establish multi-hour leak freedom.

| Reader fixture | First usable content median ms | Warm turn median ms |
| --- | ---: | ---: |
| EPUB, 36 chapters | 593 | 137 |
| PDF, 72 pages | 995 | 140 |
| CBZ, 28 pages | 978 | 143 |
| CBR, 16 pages, stored RAR4 | 590 | 139 |

Each format uses three cold browser contexts, eight warm turns per context and a bounded 20-navigation soak. Readiness requires real prose or decoded page images. Turn times include browser-driver actionability/polling overhead. p95 is suppressed for three-sample startup distributions; raw turn/heap/request observations remain in `rerun-5-results/`. Content-Length observations are declared representation sizes, not wire-transfer accounting. CBR uses a stored-member RAR4 fixture, so compressed/solid RAR extraction cost is unqualified. These bounded synthetic books do not qualify very large scanned books or large solid-archive throughput. Earlier incomplete attempts remain preserved separately and do not replace this run.

### Long audio metadata and allocation boundary

The natural long-form fixture is 12:48 FLAC at 48 kHz stereo: its expected Float32 decoded representation is 294,912,000 bytes (281.25 MiB), above the per-track 128 MiB decoded budget. The first import had no probe metadata, and a bounded 90-second readiness retry confirmed that background warming deferred work under host contention. This is expected background scheduling behavior, not corrupt media: ffprobe verified the generated file.

Source inspection of `musicSession.ts` and `musicEngine.ts` shows the automatic streaming decision and early decode estimate need the same probe facts. `expectedDecodedBytes()` returns null when any duration/sample-rate/channel input is absent. `decodeSource()` only rejects before fetch/decode when that estimate exists; absent metadata follows the fetch/decode path until input-size and post-decode limits apply. The post-decode check cannot prove a pre-allocation memory ceiling. This is a meaningful cold/unprobed long-media risk, **not a measured native allocation peak or a demonstrated leak**. Evaluate a conservative streaming path or synchronous metadata readiness before a separate product fix. The metadata-ready natural-guard/stream follow-up is recorded independently; its setup must not be described as cold-import user behavior.

The metadata-ready follow-up invoked the ordinary authenticated `playback-options` endpoint to cache real probe facts, excluding that setup from any playback timing. Its initial AlbumPage test incorrectly expected a manual “Stream track” button. The bounded failure-state capture (`long-form-diagnostic-results/.../long-form-stream-control-failure.json`) instead showed the intended track, “Browser streaming”, a native `playing` event, **zero decodeAudioData calls and no AudioBufferSourceNode starts**, with no page errors. Source inspection confirms `musicSession.startMusicQueue()` deliberately chooses native streaming when predicted decoded size exceeds the budget. Thus the absent button was a harness contract error, not an observed guard failure. The event's 418 ms value is a page-clock timestamp, not a measured click-to-start duration. All earlier metadata/selector/manual-control failures remain preserved, with the accepted corrected contract below.


The corrected automatic-streaming test passed (setup plus one long-form case; wrapper 10.96 seconds) in `readers-native/run-long-form-auto-stream.json`. Accepted per-track evidence is `long-form-auto-stream-results/.../long-form-music.json`: **224 ms from primary Play to native `playing`**, correct 768-second/48 kHz/stereo track active, zero decode calls and buffer starts, and a genuine native-media `seeked` event at **60 seconds**. This is N=1, unshaped loopback with populated metadata, a native event proxy rather than acoustic onset. No tail percentile, unprobed-track allocation ceiling, multi-hour memory claim or crossfade guarantee is inferred. The product's automatic choice is working on this fixture; the original manual recovery-control assertion was corrected to match that behavior.

### Starting video quality on constrained bandwidth: completed, contended

The new nine-trial repeat passed all correctness checks, with sampled process contention recorded in `network-native/run.json`. The fixture is 1080p H.264/AAC in MKV, encoded with an 8 Mbit/s video target. Chrome CDP caps delivery to 2.5 Mbit/s with 50 ms latency in the shaped cases. Each observation lasts 20 seconds after the first decoded frame, using a fresh browser context and cleared saved progress. This is delivery shaping on loopback, not measured Wi-Fi.

| Scenario | First decoded frame, three trials ms | Media-clock advance, three trials s | Stalled time, three trials ms |
| --- | --- | --- | --- |
| Unshaped 1080p remux | 780 / 279 / 226 | 20.292 / 20.003 / 20.001 | 0 / 0 / 0 |
| Shaped 1080p remux | 16,589 / 16,989 / 16,534 | 7.877 / 6.944 / 6.919 | 12,147 / 13,124 / 13,106 |
| Shaped 480p software conversion | 1,974 / 2,212 / 1,876 | 20.011 / 20.016 / 20.011 | 0 / 0 / 0 |

All shaped 1080p trials had two stalls and retained 1080p; all 480p trials retained 480p with no stall. The existing manual ceiling demonstrably helps this source/link combination. **Bandwidth-aware starting quality is a worthwhile optimization candidate**, even though unshaped playback is already fast. This does not establish an automatic ladder or universal threshold: there is one synthetic source, one bandwidth condition, three trials, no network loss, no simultaneous conversion saturation, and no quiet-host baseline. Preserve user preferences, resume position, selected audio/subtitles and conversion-capacity limits in any separate implementation. Complete evidence is in `network-native/playwright.json` and attached scenario JSON files.

### Local codec matrix: completed, contended

`video-native/attempt-4/` completed **10 passed / 1 failed** in 16.9 minutes (setup, dedicated audio/subtitle control case and nine three-context codec cases). All 27 startup/quality rows are retained, including the three failed MPEG-4 seeks. The table reports actual initial modes rather than fixture expectations:

| Input fixture / initial mode | First frame median ms | Seek median ms | 480p switch median ms | Seek outcomes |
| --- | ---: | ---: | ---: | --- |
| direct-h264-aac / direct | 79 | 79 | 371 | 3/3 passed |
| remux-h264-aac-mkv / remux | 261 | 96 | 302 | 3/3 passed |
| transcode-mpeg4 / transcode | 493 | — | 271 | 0/3 passed |
| hevc10-eac3 / transcode | 362 | 340 | 537 | 3/3 passed |
| long-gop-h264-ac3 / transcode | 695 | 1848 | 372 | 3/3 passed |
| vp9-opus-webm / direct | 113 | 345 | 356 | 3/3 passed |
| long-h264-resume / direct | 79 | 90 | 191 | 3/3 passed |
| tail-moov-h264 / direct | 80 | 91 | 240 | 3/3 passed |
| tracks-h264-two-audio-text-subtitle / remux | 164 | 58 | 191 | 3/3 passed |

Each fresh context visits Library and resets state before the synthetic Play click. TTFF starts at that click and excludes initial application bootstrap and fixture generation; the browser cache is empty at context creation, not necessarily at the later Play action. Each trial clears saved progress and the maximum-height preference, observes source quality for 10 seconds after the seek check, then selects 480p and observes it for 20 seconds. The long fixture seeks to 300 seconds, the 45-second multi-track fixture to 15 seconds, and the other fixtures to 60 seconds. Every 480p change received a successful session acknowledgment and a decoded 480p frame; all quality outcomes passed. H.264/AC-3 selected `transcode` because audio was unsupported, despite the fixture's earlier expected-mode label. The production decision copies H.264 video and encodes AC-3 audio; this is an audio-only conversion, not a full video re-encode or an observed remux result.

All 27 source windows and all 27 post-480p windows recorded zero stalls. HEVC source windows each recorded one dropped frame; other source windows and all post-480p windows recorded zero. Counter resets retain before/after counters and null deltas instead of becoming zero drops. These are bounded decode observations, not multi-hour or physical-display smoothness certification. The wrapper observed 2,785,067,008 bytes (2.59 GiB) peak process-tree RSS, including drivers, server, browser and encoder processes; GPU/native allocation scope and exact peak remain unqualified.

The dedicated track case acknowledged selection of audio stream 2, loaded/showed a subtitle cue and decoded a frame near the subsequent 12-second seek. MPEG-4 startup and quality passed, but all three strict 60-second seek checks found no qualifying decoded frame within 30 seconds. Those null seek results remain failed qualification checks. The narrow diagnostic below distinguishes that missing observation from a demonstrated playback failure.

The one-context supplemental `attempt-4/seek-diagnostic/` run completed successfully as a diagnostic, without replacing the failed matrix rows. It recorded an initial 201 transcode session at start 0 and a second 201 session with requested/returned start 60. `playing` resumed about 288 ms after the seek event; after 30 seconds the player was unpaused/readyState 4 at 89.693 seconds, with a decoded frame at 89.711 seconds and seekable range 0–180.023. This is consistent with playback continuing after a 60-second restart. The polling probe did not retain a frame inside the strict ±2-second target window, so exact landing and decoded-target latency remain **unqualified**, not a demonstrated “seek stayed at 80 seconds” product bug. The single diagnostic is not a new baseline or a successful substitute for the three missing seek samples.

Earlier local attempts remain preserved: attempt 1 used stale quality state and a permissive minimum-time seek condition and was invalidated; attempt 2 expired waiting for the lock; attempt 3 exposed an unbounded optional initial session wait and was stopped after retaining its failure. Attempt 4 bounds that wait and records all acknowledged session IDs for owned-session cleanup. Remote frontend evidence from attempt 1 is independent and retained below.

### Synthetic remote HLS/DASH frontend: completed, contended

The three remote cases from `video-native/attempt-1/audit-video-remote-*.json` remain accepted independently of that attempt's later invalid local case. Each used three fresh contexts and page-intercepted local media:

| Frontend path | First decoded frame median ms | Additional observation |
| --- | ---: | --- |
| HLS VOD, 640×360 | 154 | Seek to 12 s: median 89 ms, landed 12.042 s |
| Rolling HLS current edge, 640×360 | 166 | 11 playlist reads/trial; no DVR archive/seek supplied |
| DASH adaptive start | 268 | Initial height varied 180/360; pin to 180p median 117 ms |

Both HLS cases advanced approximately 20 seconds in each 20-second window with zero recorded stalls or dropped frames. DASH only qualifies startup and an explicit pin, not sustained adaptation/recovery under changing bandwidth; its startup waiting events must not be compared with HLS steady-state stalls. No fixed startup resolution is implied for adaptive DASH.

The backend never registered these synthetic remote tokens. Progress/metrics/delete calls therefore received expected fixture-related 403 responses, and these cases do not certify real relay authorization, cleanup or external transport. Separate production HLS/live/DASH component checks passed using in-memory fake upstream responses and a bounded eight-resource rolling map; those sub-millisecond algorithms are not HTTP throughput, full upstream playback or capture/DVR performance. Remote resolution, authenticated relay throughput, network loss/discontinuities and recording/archive saturation remain unmeasured.

The recording gap was checked against the existing capture tests and production boundary. Finite end-list, segment deduplication, stop/cancel, disk/runtime ceilings, chat persistence and publication have component/API contracts in `test_live_recording_{edge_follow,chat_capture,api,media_recorder}.py`, covered by the backend functional suite. Their recorder/fetcher/sink seams use fake or staged media; they do not measure real guarded fetch plus FFmpeg capture/remux throughput. The actual fetcher intentionally rejects loopback/private sources, and no policy-preserving local valid-segment capture corpus is wired into that path. Consequently no synthetic relay test is promoted to end-to-end recording certification and the production public-source policy was not weakened for a benchmark.

## Verification and reproduction

The frozen integration source's `mise exec -- make check` run reached backend **5,054 passed / 11 skipped**, then a single frontend 5-second timeout. That case passed in isolation and the complete frontend rerun passed **2,519 tests** with four workers. Production build/typechecking and stylesheet budgets passed. The subsequent full mocked and real-stack browser gates retain the failures and corrected repeats described above; this is **not a clean complete gate**. Audit helper Python compilation, Node syntax checking, frontend/e2e typechecking and diff whitespace checks passed in `audit-final-checks/`; `audit-static-checks/` retains the preceding check. Narrow diagnostic edits receive their own focused static checks.

Finite benchmark entry points are `scripts/perf/audit_household.py`, `audit_backend.py`, `audit_components.py`, `audit_resources.py`, `audit_search_profile.py` and `audit_search_variant.py`. Browser suites use `frontend/playwright.audit-readers.config.ts`, `playwright.audit-video.config.ts`, `playwright.local-network.config.ts` and the existing `playwright.realstack.config.ts`. Opt-in audit fixture generation is deliberate; inspect the config/header before running. Use disposable output/data roots, the pinned toolchain and the shared lock:

```sh
mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 7200 \
  backend/.venv/bin/python scripts/perf/audit_run.py \
  --name household-repeat --out output/performance-audit/household-repeat/run.json -- \
  backend/.venv/bin/python scripts/perf/audit_household.py \
  --out output/performance-audit/household-repeat
```

This audit intentionally omitted `--require-quiet` at the user's request and labeled external activity. `run*.json` stores source/environment identity, elapsed time, sampled process roles/RSS and contention. `resource-index.json` inventories wrapper attempts, including unsuccessful and instrumented runs, rather than treating every file as an independent accepted result. The current fingerprint scheme covers production separately from benchmark helpers/configs; the Playwright JSON/traces and current test diff retain selector/instrumentation corrections. Raw artifacts are ignored local outputs under `output/performance-audit/`, not committed household data.

The Linux specs are retained as `{backend,readers,video}-linux-spec.json` and use the production image with an unprivileged matching bind-mount UID, custom audit entrypoint and disabled healthcheck. Their fail-fast infrastructure results must not be interpreted as a native-versus-Linux performance comparison.
