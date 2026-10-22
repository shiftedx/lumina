# Backend pytest: strict regression-history evidence

- **Base reviewed:** `b3c5ff7`
- **Scope:** AST inventory of **3,860 authored `test_*` functions** in **378** `backend/tests/` files. Parameter expansion is deliberately deferred to the runner's supplied nodeid inventory; this report is at source-template level.
- **Rule:** A historical catcher needs a test that existed before the corrective change, evidence it failed, and an app-code fix responding to that failure. A test added with a feature/fix, or a test-only timing/flake/drift repair, is not qualifying evidence.

## Confirmed historical regression catcher — protect

| Test nodeid prefix | Evidence | App-code response | Verdict |
| --- | --- | --- | --- |
| `backend/tests/test_live_recording_scheduling_manager.py::test_scheduling_starts_in_waiting_and_runs_the_waiter` | [Issue #65](https://github.com/shiftedx/lumina/issues/65) names this existing test and reports **two focused failures in eight retries** on `ce8023c`; it identifies `wait_all()` joining a published-but-unstarted worker. The test's line history shows it predates the fix (introduced in `caa0a302`). | [PR #66](https://github.com/shiftedx/lumina/pull/66), merge `4a887c4773689711106ab1e4ec336bdcb37dd961`, changes `backend/app/services/live_recording_manager.py`: it starts the worker under the same lock that publishes it, preventing `join()` before `start()`. | **Keep: historical catcher.** |

The deterministic `backend/tests/test_live_recording_scheduling_manager.py::test_wait_all_joins_scheduled_work_while_it_starts` was added in PR #66. It may be valuable coverage, but is **not** a historical catcher under this rule.

## Broad scan and what it establishes

1. Ran an exact `git -S` function-title query for every template:

   ```sh
   git log --all -S "def <test function>" --format=%H%x09%s -- <test file>
   ```

   The machine-readable result contains **7,546 creation hits** and **one removal/rename hit**, for all 3,860 templates. These establish title provenance only. They do not prove a regression: a test title can remain unchanged while an app fix lands.

2. Reviewed the Git candidate pool:

   ```sh
   git log --all --grep='fix\|regress\|race\|security\|migrat\|concurr\|data loss\|corrupt' -i
   ```

   This returned **146** candidate commits; **67** changed both `backend/app/` and `backend/tests/`. The raw screening artifacts intentionally over-include functions merely retained in a touched test file and are not verdict evidence.

3. Queried first-party GitHub issues/PRs, including:

   ```sh
   gh api -X GET search/issues -f 'q=repo:shiftedx/lumina is:issue (regression OR failed OR failure OR broke OR broken)' -f per_page=100
   gh api -X GET search/issues -f 'q=repo:shiftedx/lumina is:pr (regression OR failed OR failure OR broke OR broken)' -f per_page=100
   gh api -X GET search/issues -f 'q=repo:shiftedx/lumina is:pr "backend/tests/"' -f per_page=100
   ```

   The broad searches and **49** PRs returned by the `backend/tests/` query yielded the #65/#66 direct chain above. Generic `"test_"` issue/PR searches returned no results.

## Explicit non-qualifying / uncertain example

[PR #72](https://github.com/shiftedx/lumina/pull/72) says both regressions failed before their fixes and extends the pre-existing `test_rescan_removes_stale_shared_folder_cover_but_preserves_manual_art`. Its patch adds the cached-lookup and maintenance assertions that exercise the stated defect; therefore the old test alone is not shown to have failed before the app fix. It is **not** recorded as a historical catcher from this evidence. It needs a candidate-specific failure record before any strict evidence claim.

## Limits and next step

- `git -L` across `--all` is not usable in this repository because multiple snapshot roots make line tracking ambiguous. The exact selected-file `-L` command succeeded for the confirmed catcher. For any proposed deletion, run `git -L` on its fixed path/line or function and inspect the associated patch/PR.
- The all-template `-S` sweep, broad commit scan, and broad GitHub searches are complete; they are not an exhaustive proof of the absence of an undocumented historical failure for every test. Do **not** use “no hit” as a deletion verdict.
- Candidate-specific exact-title/static-prefix GitHub searches and selected `-L`/patch reviews were completed below before assigning deletions.

## Artifacts

- `/tmp/lumina-pytest-history-inventory.json` — source AST inventory.
- `/tmp/lumina-pytest-history-records.json` — all-template pickaxe rows plus the confirmed evidence mapping.
- `/tmp/lumina-pytest-fix-commits.tsv` — 146 commit candidate subjects.
- `/tmp/lumina-pytest-mixed-fix-commits.json` — 67 app-and-test candidate commits, screening only.
- `/tmp/lumina-pytest-issue-65.json`, `/tmp/lumina-pytest-pr-66.json` — first-party source snapshots.

## Candidate-specific history review (parent-supplied deletion candidates)

Exact `-S`, selected-function `-L`, and exact-title GitHub issue and PR searches were completed for these five candidates. **None has qualifying existing-test-failure-to-app-fix evidence.** This is a history conclusion only; it does not override the separate retained-contract and mutation-proof requirements.

| Candidate | Git evidence | Exact GitHub issue/PR title search | Strict history conclusion |
| --- | --- | --- | --- |
| `test_jellyfin_dto_compat.py::test_an_unavailable_decision_offers_no_transcode_and_keeps_direct_play` | `-S` and `-L` show creation in `caa0a302`; later file commits #47/#49 leave this function unchanged. | 0 / 0 | No qualifying evidence. |
| `test_jellyfin_clients.py::test_an_unknown_route_is_an_empty_bodied_404` | `-S` and `-L` show creation in `caa0a302`; no later test-file history. | 0 / 0 | No qualifying evidence. |
| `test_jellyfin_integration.py::test_trace_logs_route_templates_and_parameter_names_only` | `-S` and `-L` show creation in `caa0a302`; later #47/#70 file changes do not alter the function. | 0 / 0 | No qualifying historical-catcher evidence. It is a token/value secrecy check, so retain-or-delete still requires the separately named retained privacy contracts to fail on mutation. |
| `test_library_gallery_contract.py::test_the_gallery_fixture_holds_an_album_of_three_tracks` | `-S` and `-L` show creation in `caa0a302`; no later test-file history. | 0 / 0 | No qualifying evidence. |
| `test_jellyfin_clients.py::test_api_errors_keep_their_json_body` | `-S` and `-L` show creation in `caa0a302`; no later test-file history. | 0 / 0 | No qualifying evidence. |

Exact searches used both `is:issue` and `is:pr` with each quoted function name. Raw results: `/tmp/lumina-pytest-candidate-github.json` and `/tmp/lumina-pytest-candidate-prs.json`.

## Additional candidate-specific history review

| Candidate | Git/first-party source | Strict history conclusion |
| --- | --- | --- |
| `test_library_automation_safety.py::test_no_automation_code_path_calls_confirm` | `-S` and `-L` show creation in `caa0a302`; later file commits [#74](https://github.com/shiftedx/lumina/pull/74) and [#77](https://github.com/shiftedx/lumina/pull/77) do not modify it. Exact quoted issue/PR-title searches: 0 / 0. | No qualifying existing-test failure-to-app-fix evidence. Retained behavior coverage and its selective fault control are verified below; the source recipe is removed. |
| `test_library_automation_snapshot.py::test_facade_method_names_and_signatures_match_s3` | It originated in `caa0a302`; `8794d059` / [#43](https://github.com/shiftedx/lumina/pull/43) extends its expected facade signatures for the new activity feature. Exact quoted issue/PR-title searches: 0 / 0. | Feature-time expansion is not an existing failing test followed by an app fix; no qualifying historical evidence. |

Raw exact-search results: `/tmp/lumina-pytest-automation-candidate-github.json`.

## Retained-coverage negative controls

The following focused runtime controls are intentionally loaded from `/tmp/lumina_pytest_negative_controls.py`; no app or test source changes were made. Each first runs with `LUMINA_NEGATIVE_CONTROL=<name>`, `PYTHONPATH=/tmp`, and `-p lumina_pytest_negative_controls`, then reruns the identical retained node without the plugin. Detailed private logs and machine-readable results are in `/tmp/lumina-pytest-negative-controls-run3/`.

| Removed candidate behavior | Selective runtime fault | Retained public contract | Red / green evidence |
| --- | --- | --- | --- |
| Unavailable decisions do not offer transcoding | Central `transcoding_fields` unavailable branch returns `SupportsTranscoding=True`; direct imports still call the same mutated function object. | `test_jellyfin_integration.py::test_transcoding_fields_direct_and_hls` | exit **1** (assertion failure) / exit **0** |
| Unknown Jellyfin routes return an empty 404 | `main.jellyfin_error` returns a nonempty 418 only for Jellyfin paths. | `test_jellyfin_api.py::test_unknown_route_is_a_logged_empty_404` | exit **1** (assertion failure) / exit **0** |
| Trace logs emit names, never values or tokens | Middleware records the actual normalized raw query only while tracing; request/response behavior is unchanged and no test secret is embedded in the plugin. | `test_jellyfin_api.py::test_trace_logs_names_never_values_or_tokens` | exit **1** (assertion failure) / exit **0** |
| Gallery seed's physical track order feeds the album workflow | The consuming album-detail module's imported `seed_gallery` changes the pending third track to disc 0 before fixture commit. | `test_album_detail.py::test_an_album_page_lists_its_tracks_with_the_members_progress` | exit **1** (assertion failure) / exit **0** |

These controls establish the named replacement coverage only. They do not establish the distinct ordinary-API `Content-Type` contract in `test_api_errors_keep_their_json_body`, which remains a **keep** because no replacement for its header-specific contract was established.
## Additional retained-coverage mutation controls

No repository source or test file was changed. Faults live in private pytest plugins under `/tmp` and were loaded only by the focused fault process.

## 1. Remove the source-substring confirmation recipe

Candidate: `backend/tests/test_library_automation_safety.py::test_no_automation_code_path_calls_confirm`

Retained detector: `backend/tests/test_library_automation_safety.py::test_mass_missing_is_never_auto_confirmed_9_4_M`

Fault plugin: `/tmp/lumina_fault_auto_confirm.py`. It wraps the real `LibraryAutomation.tick` runner path. After the normal tick it selects only an `ImportRun` that is `needs_confirmation` with `missing_candidates > 0`, then attempts `LibraryImportService.confirm(run)`. This is the forbidden automatic behavior; it does not alter ordinary or non-missing runs.

RED command (from `backend/`):

```sh
PYTHONPATH=/tmp:. .venv/bin/python -m pytest -q -p lumina_fault_auto_confirm tests/test_library_automation_safety.py::test_mass_missing_is_never_auto_confirmed_9_4_M
```

Result: exit 1, `AssertionError: automation must never confirm a held run`, raised by the retained file-wide `no_confirm` fixture when the injected runner behavior reaches the held missing run. Log: `/tmp/lumina-pytest-auto-confirm-red.log`.

GREEN command:

```sh
.venv/bin/python -m pytest -q tests/test_library_automation_safety.py::test_mass_missing_is_never_auto_confirmed_9_4_M
```

Result: exit 0, `1 passed in 0.79s`. Log: `/tmp/lumina-pytest-auto-confirm-green.log`.

Conclusion: the behavioral retained test detects the consequential forbidden action directly. The deleted `inspect.getsource` substring check adds only spelling/layout coverage.

## 2. Remove the facade signature reflection recipe

Candidate: `backend/tests/test_library_automation_snapshot.py::test_facade_method_names_and_signatures_match_s3`

Representative retained detector: `backend/tests/test_v1_scan.py::test_external_scan_zero_mutation`

Fault plugin: `/tmp/lumina_fault_facade_activity.py`. It changes the real `LibraryAutomation.activity_started` signature to accept no run arguments. The production `POST /api/admin/imports` consumer still calls `automation.activity_started(run.id, run.counters)`.

RED command (from `backend/`):

```sh
PYTHONPATH=/tmp:. .venv/bin/python -m pytest -q -p lumina_fault_facade_activity tests/test_v1_scan.py::test_external_scan_zero_mutation
```

Result: exit 1 at the production route, `TypeError: ...activity_started() takes 1 positional argument but 3 were given`. Log: `/tmp/lumina-pytest-facade-red.log`.

GREEN command:

```sh
.venv/bin/python -m pytest -q tests/test_v1_scan.py::test_external_scan_zero_mutation
```

Result: exit 0, `1 passed, 1 warning in 1.69s`. Log: `/tmp/lumina-pytest-facade-green.log`.

### Coverage for all nine reflected methods

| Method | Retained consequential coverage |
| --- | --- |
| `snapshot` | `test_snapshot_matches_the_6_1_shape_and_never_includes_paths`; admin GET contract also consumes the shape. |
| `public_activity` | `test_public_activity_is_a_bounded_identity_free_aggregate`; member activity HTTP contract checks its projection. |
| `root_entry` | `test_root_entry_matches_snapshot_roots_item_and_is_none_for_unknown`; root PATCH consumer calls it. |
| `roots_changed` | `test_roots_changed_starts_a_baseline_when_watch_turns_on_and_off_keeps_rows`; root PATCH consumer calls it after commit. |
| `scan_now` | `test_scan_now_all_roots_in_label_order_first_starts_rest_queued`, `test_scan_now_probes_a_never_probed_root_with_a_bound`, and focused-root cases call both supported argument forms; admin scan HTTP contract calls it. |
| `submit` | `test_submit_runs_drive_on_the_manual_lane_tracked` and `test_submit_routes_manager_runs_to_the_roots_automatic_lane_and_ignores_missing_runs`; request sync is the production caller. |
| `activity_started` | Real import HTTP workflow `test_external_scan_zero_mutation`; mutation control above proves it detects a broken signature. |
| `activity_finished` | The same real import HTTP workflow waits for `_drive_reported`, whose `finally` invokes `activity_finished(run_id)` before the TestClient response completes. |
| `diagnostics` | `test_diagnostics_matches_6_3` and `test_diagnostics_returns_the_raw_poller_error`; admin diagnostics HTTP contract consumes/redacts the returned block. |

The HTTP tests for the dedicated automation router use `FakeAutomation`, so they are not by themselves proof of real-singleton wiring. The retained direct service contracts call the real class with the same method names/argument forms; the import route additionally exercises the real singleton for the two activity methods that lack direct calls. No important reflected method lacks a retained consumer or behavior contract.

## Isolation failure found while qualifying retained performance cases

The ordinary parallel tier skips the eight seeded performance measurements.
Running their normal serial lane (`mise exec -- make perf-titles`, without the
audit benchmark plugin) exposed a 429 in `test_reco_api_budgets` after
`test_title_api_budgets`: direct `TestClient` users shared the process-wide
popular-request bucket. Only the optional `api_client` fixture previously reset
it. An isolated recommendation run did not reproduce that 429.

A private two-test public-API control used the real
`rate_limit_dependency("popular")`: the first case exhausted its 60-request
allowance and verified request 61 returned 429; the next case's first request
incorrectly returned 429. Before repair: **1 failed, 1 passed**. With autouse
`fresh_rate_limits` clearing the real singleton before and after every case:
**2 passed**. Production limits are unchanged. Ten unchanged rate-limit and
lockout contracts also passed. Private evidence:
`/tmp/lumina-rate-limit-isolation-{red,green}.log` and
`/tmp/lumina-rate-limit-existing-green.log`. Temporary control source was removed.

The loaded-host performance attempt had **5 passed, 3 failed** in 66.40 s
(75 s including seed/setup). Besides that 429, timing budgets exceeded their
unchanged thresholds; the tests explicitly reported load 41.8 on 16 cores as
invalid for a timing verdict. This run is not presented as qualification.

## Chromaprint regression found on the production media toolchain

The existing `test_media_segments.py::test_fingerprint_finds_a_shared_intro_at_different_offsets[chromaprint]`
was skipped on macOS because its FFmpeg lacks the muxer. Executing it in Linux
first with Debian FFmpeg and then with the exact production Jellyfin FFmpeg
**8.1.2-5 arm64** reproduced empty detected segments. The same test's bandpass
variant passed. This is an existing-test failure leading to an **app-code fix**,
so the chromaprint case is a protected regression catcher from this audit onward.

The 32-bit matcher accepted up to ten different bits per word and smoothed
unrelated frames into 32–50-second runs, hiding agreement on the actual shared
20-second intro. Tightening this local binary matcher to two bits restores
agreement. The bandpass threshold is unchanged. This is a conservative tradeoff:
it rejects broad approximate chromaprint matches rather than treating them as
shared content. Both two and three bits passed the unchanged fixture when timing
was corrected; two also passed the unrelated-noise/no-agreement control.

The old floating hop also differed from Chromaprint's integer hop, and raw-word
positions were anchored at the input edge. The repair uses the analysis-window
midpoint. [Chromaprint's configuration](https://github.com/acoustid/chromaprint/blob/v1.5.1/src/fingerprinter_configuration.cpp)
uses 4,096-sample frames at 11,025 Hz, an integer 1,365-sample hop, five chroma-filter
taps and a maximum classifier width of 16.
Its [delay formula](https://github.com/acoustid/chromaprint/blob/v1.5.1/src/fingerprinter_configuration.h)
gives `((5-1 + 16-1)*1365 + 2731)/11025` seconds; half is approximately 1.300 s.
Using that midpoint for our segment-boundary estimate avoids a fixture-fitted
offset. The existing one-second boundary tolerance and every test input remain
unchanged. This local matching scheme is not the upstream gradient matcher.

RED: unchanged production-toolchain case failed with no segments. GREEN:
unchanged case plus a private unrelated-noise control **2 passed in 8.95 s**.
Private pinned-image evidence and Dockerfile:
`/tmp/lumina-chromaprint-pinned-8014/`; `final_test.log` records the passing run.

## HDR regression found on the production media toolchain

The retained `test_v1_transcode.py::test_hdr10_tone_maps_in_software` failed
repeatedly on production Jellyfin FFmpeg 8.1.2-5 arm64: output remained tagged
`smpte2084`. A command capture disproved the initial metadata-only hypothesis:
the app selected `scale,format`, with no tone-map filter. Stream-header probing
reported the BT.2020 matrix but omitted transfer metadata; decoding one frame
reported `smpte2084`. That missing fact incorrectly selected the SDR path.

The app now performs a bounded, protocol-restricted one-frame probe for the
first video when its BT.2020 header omits transfer metadata. Missing, malformed,
over-limit, timed-out or failed enrichment fails the probe instead of claiming
SDR. Other streams retain the ordinary probe path. A private cache-version
marker makes old successful facts re-probe once, preserving cached-error policy
and excluding the marker from returned/public facts.

The existing real-media journey was already red before the app fix; its final
assertion now requires exact BT.709 matrix, transfer and primaries as well as
H.264/yuv420p. Existing cache contracts were extended to check one-time recovery
and normal subsequent caching. Their names became
`test_legacy_successful_caches_are_stale_but_cached_errors_are_not` and
`test_old_successful_caches_are_reprobed_once`; each original ledger row maps its
rename, with no case-count change. This HDR test is a protected regression catcher.

Evidence: `/tmp/lumina-chromaprint-pinned-8014/hdr-candidates/` contains
`command-probe-3/inputs.json`, `raw-probe/video-frame.json`,
`strict-real-media-red/run.log` and `strict-real-media-defensive-green/run.log`.
The original chromaprint, Linux address-space and repaired HDR cases all passed
with zero skips in two production-toolchain runs. Final qualification timings
and paths are recorded in readiness after the last review correction.


## Merge-review corrections

Review found that an attached cover image before the playable video could hide
HDR frame enrichment. The probe now skips attached pictures and selects the
playable video's validated absolute stream index. Its new service contract
failed with the previous one-call behavior and passes with the repair. Malformed
disposition metadata is treated consistently with stream normalization. This is
a new contract accompanying its fix, not historical regression evidence.
Evidence: `/tmp/lumina-probe-cover-{red,green}.log`,
`/tmp/lumina-probe-focused-green.log` (12 passed),
`/tmp/lumina-probe-hdr-docker.log` (real HDR passed), and
`/tmp/lumina-probe-disposition-green.log` (5 passed). This adds one case and one
authored template: 5,285 retained cases / 3,855 templates.

The existing 10,000-title similarity budget exposed SQLAlchemy result buffering:
iterating an ORM result still materialized every row before the loop. Fetching
with the existing 500-row chunk size preserves SQL, filtering, order, ranking
and cache behavior while bounding transient rows. Three measurements with
bounded fetching had p95 65/65/69 ms. Reverting only that option failed at
185 ms p95 / 81 ms median; restoring it passed at 73 ms / 66 ms. Neither GC nor
test budgets changed. The unchanged eight visibility, query-flatness, annotation
and privacy contracts passed. This existing case is now a protected catcher.
A full serial attempt then passed seven cases but retained a seeded similarity
p95 failure (127.2/120 ms); diagnostics passed at 39.5/80 ms. This attempt is
recorded as failed, not qualified. Logs:
`/tmp/lumina-reco-pool-streaming-{before,after,after-2,after-3,reverted,restored}.log`
and `/tmp/lumina-pytest-merge-perf-1.log`.


The seeded similarity API still exceeded 120 ms at load 8.6/16 after bounded
fetching. In-app-thread profiling attributed 87% of candidate-building CPU to
cosine calls: candidates took 72.0 ms median, base loading 24.3 ms and ranking
12.7 ms. The policy knew the requested media types but filtered them only after
nomination. It now restricts eligibility before every nomination source, so
wrong-type series neither consume scoring work nor crowd movies out of quotas.
The existing like-with-like service contract was extended with 101 competing
series: reverting the filter lost a valid matching movie; restoring it returns
the movie and only movies. This extension accompanies its fix and is not
claimed as historical regression evidence. The focused policy, visibility,
flat-query, annotation, privacy and pool cohort passed 56 cases. Candidate
median fell to 65.0 ms, but fresh seeded API attempts still failed at 121.9 and
125.1 ms. Those are failed attempts, not qualification. Artifacts:
`/tmp/lumina-reco-seeded-diagnosis-20261022T164630/`.


With the type filter in place, vector arithmetic remained the dominant cost.
The existing NumPy runtime dependency performs the same unit-vector dot product
with float64 inputs, avoiding Python float boxing while iterating float32 arrays.
A private 7 × 8,192-call, 768-dimension comparison measured 65.408 ms versus
12.438 ms median (5.26×), maximum numerical delta 5.33e-15, and no retained
allocation. Each call uses two temporary 6,144-byte float64 arrays. The initial
NumPy import measured 45–51 ms. Length mismatch still returns 0.0, empty vectors
still return 0.0, and ranking's separate cosine function is unchanged.
Embedding, policy, ranking, replay and search contracts passed 140 cases.
A diagnostic full serial run passed all seven other cases but failed title API
home next-up/live snapshot p95 timing; that failed attempt is retained in
`/tmp/lumina-embeddings-numpy-perf.log` (61.54 s pytest / 68.79 s wall).
Numerical and focused evidence: `/tmp/lumina-numpy-cosine-bench/results.json` and
`/tmp/lumina-embeddings-numpy-focused.log`. Final qualification below supersedes
these diagnostic attempts only when both independent runs are green.


At `9e14deb`, explicit serial qualification passed eight cases in 67.057 s on
its first run, then failed only diagnostics p95 (99.1/80 ms) in 64.012 s on its
second run, at load 6.1/16. This pair is not qualified. Evidence:
`/tmp/lumina-pytest-merge-perf-qualified/report.json`.

The earlier home/live timing failure had a separate fixture race: reading an
empty, due snapshot started four yt-dlp provider workers during measurement.
The retained title API case now seeds a real, nonempty fresh snapshot through
normal normalization/persistence, and binds a real live-service reader to its
private path. This binding matters because another perf module imports the app
during collection, before the seeded DB fixture changes settings. The test
asserts its live item appears and that no provider refresh occurred; it closes
the reader and joins its executor before checking the refresh guard. The real
HTTP route and all 20 timing samples remain, with the same 100 ms budget.
The existing live snapshot/refresh service contracts and
`test_live_with_50_follows_and_a_96_item_snapshot` retain refresh coverage.
All four perf modules collect eight cases; the full eight-case cohort passed
with this fixture in 56.62 s pytest / 63.61 s wall, and the guarded title case
passed independently in 30.73 s. Evidence:
`/tmp/lumina-title-live-fixture-{collection,perf8,guard-focused}.log`.


Diagnostics' remaining p95 spike was reproduced at load 3.8/16 and correlated
exactly with generation-2 GC: two of 20 measured calls took 89.865/90.538 ms
while ordinary calls had 35.66 ms median. Profiling identified ORM buffering of
the existing distinct event queries (5,435 opens and 3,809 plays). Applying the
existing 500-row chunk size to those two results preserves SQL, filters, totals
and caching. A selective unstreamed control failed at 88.2/80 ms, with gen2 tails
on calls 8/20; restoring bounded fetching passed with raw p95 36.669 ms and no
gen2 collections in the measured calls. GC and all test budgets stay enabled.
The focused metrics/privacy/diagnostics cohort passed 36 cases. The existing
`test_reco_api_budgets` is therefore a protected failure-to-app-fix catcher.
Evidence: `/tmp/lumina-diag-gc-{profile,old-control,new}.{log,json}`,
`/tmp/lumina-diag-gc-profile.pstats`, and
`/tmp/lumina-reco-metrics-yield-focused.log`.


## Final backend qualification at b8d52f7

Smoke passed twice with four workers (50/50 each, zero skips), 5.860/5.814 s.
Ordinary full passed twice with four workers (5,274 passed plus exactly the
11 declared prerequisite skips), 139.950/138.549 s including collection,
validation and cleanup. Linux/production FFmpeg prerequisites passed twice
with three xdist workers (3/3 each, zero skips), 16.670/16.816 s including
container setup and cleanup. The eight seeded measurements passed twice
serially with fresh seeds and unchanged guards (8/8 each, zero skips),
63.441/63.386 s including setup and cleanup. All 5,285 retained cases have
therefore executed and passed twice. The only serial exceptions are the eight
seeded p95 measurements documented in the ledger.

Reports, executable commands, source version and exact reconciliation are in
[readiness](../BACKEND_READINESS.md). The final repository gate is still running
its browser phase; backend, bootstrap, TypeScript, bundle and all 2,659 Vitest
tests have passed. The earlier failure logs above remain part of the evidence.
