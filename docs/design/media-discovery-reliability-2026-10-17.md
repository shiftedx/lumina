# External media discovery repair

## Confirmed failure

A read-only comparison of the manager API, Lumina's mounted files and its catalog confirmed that files in two additional seasons were present on both services but absent from registered media. The mounted series held 126 media files; 94 were registered and 32 were not. This was an import/discovery failure before playback, rather than an unavailable source mount or merely a cached season list. Private titles, paths, hostnames and credentials are excluded from this record.

The affected production external root had folder watching disabled and its full-scan schedule set to `off`. Its last complete full scan predated the additions. Other manager/request-driven scoped scans continued, but those do not constitute discovery of every new season in the root. Manual library refresh starts administrative scans asynchronously; member refresh reconciles already registered files. The available historical records do not establish the exact skip reason or account used for each reported refresh, so no claim is made about that detail.

## Production recovery

The installed production version's existing `start_run_default` and `drive` importer were used for one deep scoped scan of the already imported series folder, inheriting baseline visibility and using the existing administrator. The normal first-import, active-run, mount and missing-file guards remained in place. The run finished successfully with complete coverage: **126 inspected, 94 unchanged, 32 indexed, zero missing**. A subsequent catalog join confirmed all seven seasons and all 126 episodes registered with available file artifacts, including all 32 recovered episodes.

The affected root now has **watching enabled at a 60-second interval** and a **six-hour full-scan safety net**. The live poller picked up the persistent settings without a restart and automatically started the overdue full scan. No application image was deployed, and the other roots' settings were not changed. Previous settings were watch off, interval 300 seconds and schedule off.

Watching uses bounded directory polling and requires stable file observations before import (ADR 0017). A 60-second setting is a polling interval, not an instant-discovery or end-to-end deadline. Full scans cover changes that directory mtimes cannot reliably reveal. Mount/identity and confirmation failures remain visible blockers rather than permission to erase or import an unverified library.

## Working-source correction

The pending independent Sonarr/Radarr observer also contained a gap: it requested scoped imports only for `pending_import` inventory. A series with any playable episode was `registered`, which incorrectly ended its reconciliation despite later seasons. The same condition missed replacement files for registered movies. Deploying the observer as it stood would therefore not fully resolve this report.

The observer now also offers mapped, file-bearing **registered** inventory to the existing scoped importer. A public service regression covers an already-playable series gaining another season and a registered movie remaining eligible for replacement reconciliation, with Requests disabled and without synthesizing a Request. It failed for both managers before the correction and passed after it.

The shared `rescan_folder` helper retains its ten-minute per-folder cooldown, root single-flight behavior and retry on subsequent polls when a root is busy. It still requires an enabled mapped external root with a successful full baseline and refuses a held confirmation. Availability remains grounded in registered visible media; upstream completion does not grant playback. This periodic folder reconciliation is intentionally independent of episode counts, which cannot reveal same-count upgrades or file replacements. It is a safety layer alongside watching, not a new full-root scan on each observer poll.

Bulk observer reconciliation now deduplicates folders and offers those with the oldest successful scan first. This matters when many titles share one busy root: a fixed ordering could repeatedly rescan the first title after its cooldown while later titles never get admitted. The service regression models one accepted folder per poll, advances beyond the cooldown between polls and requires all three folders to progress. A fixed-order replay reproduced `A, A, A`; the corrected service admits `A, B, C`. Busy attempts do not consume a folder's cooldown, and each folder still uses the same guarded importer. Unexpected failure of one folder cannot stop the rest.

## Verification

The deterministic failing command was `mise exec -- backend/.venv/bin/python -m pytest -q backend/tests/test_arr_observations.py -k registered_titles`: **2 failed** before the correction. The initial focused observer, request-sync, incremental-scan and watcher suite then passed **43 tests**; after the fair-order follow-up the same suite passed **44 tests**. The existing request-sync contract continues to cover cooldown, missing first baseline and held confirmation.

Full project check output is retained under `output/media-discovery-2026-10-17/check.log`. Gate and live safety-scan outcomes are recorded below.

The full gate began with the registered-title eligibility correction. The fair-order follow-up was made during its backend phase and received the separate final 44-test run, recorded in `output/media-discovery-2026-10-17/focused-final.log`; the initial backend gate process had already loaded its earlier snapshot. Frontend source stayed unchanged. Source identities and the red/focused logs are retained in that output directory.

The live overdue safety scan completed with **38,981 inspected, 38,020 unchanged, 14 indexed, 947 updated and zero missing**, complete coverage and no error. Watching then persisted its baseline of **19,487 directories**. A fresh read-only catalog verification still found all 126 episodes available. Sanitized evidence is `output/media-discovery-2026-10-17/production-verification.json`; no private title or path is included.

The `mise exec -- make check` invocation passed bootstrap, source/e2e TypeScript checks, the production build and stylesheet budgets. Its backend stage completed **5,060 passed / 11 skipped / 1 failed** in 753.2 seconds, stopping the gate before frontend unit/browser phases. The failure is the same existing 5,000-directory watch-baseline writer-duration contract observed in the preceding performance work: **558.7 ms against an unchanged 500 ms limit**. The isolated repeat passed in 2.49 seconds (`writer-repeat.log`). No writer implementation or threshold changed. The timing failure remains a failed full-gate observation; a passing isolated repeat does not replace it. This shared-host gate also overlapped brief focused follow-ups, and no competing process is singled out as its cause. Post-check host state is retained separately and is not an assertion-time measurement.

Frontend source is unchanged from the preceding remediation, whose completed frontend unit, real-stack and mocked runs remain recorded in that report. Those phases were not repeated after this backend-only gate stopped. The final changed observer/scanner boundaries are covered by the separate fresh 44-test suite. `git diff --check` passed. No clean full-gate or new application deployment is claimed.


A later full-project verification for the metadata-enrichment correction includes the final observer/scanner source. Its backend stage passed **5,068 tests / 11 existing skips**, including the unchanged writer-duration contract and final fair-order regression. Its frontend unit, real-stack and mocked browser phases also passed, completing the full gate with exit status 0; see [the metadata-enrichment record](metadata-enrichment-reliability-2026-10-17.md) for the completed gate outcome. This subsequent run does not erase the earlier failed timing observation.
