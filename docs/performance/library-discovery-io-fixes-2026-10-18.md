# Library discovery I/O fixes — 2026-10-18

The eight findings in [the audit](library-discovery-io-audit-2026-10-18.md) are addressed in focused slices. Production is unchanged and no public release was created. The complete project gate remains required before production deployment or a public release, as requested; it was not run for this landing.

## Changes and retained discovery contracts

| Finding | Implemented behavior | Focused evidence |
| --- | --- | --- |
| 1. Repeated manager scans | Source-scoped durable fingerprints, successful-run acknowledgement, changed-first admission, priority for pending imports during bootstrap, one admitted folder/root/observation, six-hour safety eligibility, ten-minute failed-attempt backoff; distinct `manager` trigger and automatic lane | New seasons/replacements, unchanged snapshots without cursor writes, restart, busy/fairness, same-folder sources, safety failures, newest history beyond 64 records, Arr/Lidarr observation success despite scanner errors |
| 2. Completion fan-out | Changed artifact/item/title subjects target probe/caption work; unchanged runs do not wake broad maintenance; changed art already queues exact titles | Unchanged completion context, missing-probe recovery, same-name subtitle edit producing a transcript revision, existing probe/art/caption/search suites |
| 3. Repeated traversal | One indexing walk retained across batches, no preliminary count pass, fresh lstat before indexing, durable-cursor recovery after failure/resume; unknown total until completion | Enumeration budget, cancellation/resume, failed-batch replay, legacy resume, disappearing future entries and successful next-scan reconciliation |
| 4. Polling blocks idle | Exact passive GET exception in authentication; 15-second active / 60-second idle polling, hidden-tab pause | Authenticated HTTP fake-clock workflow and existing background-child protection; frontend timer and lifecycle tests |
| 5. Waiting/error confusion | Failed attempts respect retry cadence, reset the stability chain, and report unreadable counts separately; waiting alone has no active spinner | Error/recovery requiring a fresh 90-second span, deletion, phone waiting/error presentation |
| 6. Broad scoped queries | Indexable exact folder bounds, no-op removal short circuits, root/size rename index | Actual service EXPLAIN seeks for shallow/deep Unicode/wildcard-like prefixes and sibling/case exclusion; rename identity/root isolation; migration/rollback |
| 7. Unbounded progress cohort | Current/newest run per root, active discovery counts separated from completed work, explicit stalled roots | Stalled peer plus sequential completions remains bounded; normal concurrent roots retain useful progress |
| 8. Watch persistence writes | Hourly root-marker heartbeat replaces updates to every directory timestamp; fresh marker restores old child rows | Marker-only update, stale/missing marker fallback, chunked writer and watcher safety suites |

Manager identities include stable file IDs/counts/sizes and recent canonical history. Newest events are retained before the 64-record bound. If an upstream API provides no in-place change signal, safety reconciliation remains the fallback; its six-hour eligibility is not a deadline under the per-root budget. Root Watch continues to detect directory changes independently.

Retained directory listings intentionally discover additions after a directory snapshot on the next scan. A vanished future entry makes the current coverage partial, preventing premature missing marks. The next scan finds additions and reconciles genuine removals. Online/mount identity, symlink, cancellation and mass-missing confirmation safeguards remain in place.

Completion contexts are in memory. Restart/resume uses conservative broad catch-up; normal unchanged runs avoid it. Subtitle metadata now carries a stat fingerprint so unchanged sidecars are not reopened, while same-name edits remain discoverable. Existing periodic and restart maintenance paths remain available.

## Enumeration measurements

The original zero-I/O traversal baseline enumerated 12,000 directory entries for a flat 1,000-file root across discovery and indexing. The retained harness now exercises the real importer and its online guards against isolated temporary media/SQLite fixtures:

| Candidates | Import steps | Root listings, including online guards | Entries enumerated | Entries/candidate |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 6 | 8 | 1,007 | 1.007 |
| 2,000 | 11 | 13 | 2,012 | 1.006 |

This is application enumeration, not physical storage I/O or a drive-wear estimate. The baseline and final instruments differ: the final count includes safety-guard enumeration that the traversal-only baseline excluded. The final fixture demonstrates linear growth and remains below a 1.1 budget. It creates bounded temporary files and SQLite state; it never scans production or contacts providers.

```sh
mise exec -- backend/.venv/bin/python scripts/perf/audit_library_discovery.py --files 1000 --max-enumeration-factor 1.1
mise exec -- backend/.venv/bin/python scripts/perf/audit_library_discovery.py --files 2000 --max-enumeration-factor 1.1
```

## Verification and rollout

Focused results, with some overlapping follow-ups rather than a unique-test total:

- Importer, titles, loudness, renditions, subtitles, search and scope: **274 passed**.
- Automation, API, watcher, driver and safety: **154 passed**; final activity snapshot follow-up **26 passed**.
- Manager observation/reconciliation/acquisition: final follow-up **22 passed**, including reviewer regressions. Independent reviewer repeated the four affected observation/history cases successfully.
- Background activity and passive HTTP contract: **25 passed**.
- Schema/version contracts: **19 passed**, plus executable rollback/re-upgrade regression **1 passed**.
- Changed import query plans: **3 passed**.
- Frontend header, activity poller and trigger copy: **22 passed**; application and e2e TypeScript checks passed.
- Focused library-activity browser workflows: **6 passed**, covering desktop/phone, known/unknown activity, downloads, waiting, stalled/unreadable state and accessibility/containment. The privacy assertion retains an exact field allowlist and numeric-only values; the legitimate numeric `waiting_error_files` key is permitted.
- Python compilation and `git diff --check` passed. No full `make check`, production benchmark, image deployment or release publication is claimed.

Schema 31 adds only `ix_media_artifacts_root_size`. Upgrade and rollback/re-upgrade preserve registered media. The [operator rollback guide](../../docker/README.md#rollback-after-a-schema-upgrade) normalizes `manager` history to `watch` before an older API reads it, retains the additive index and ignores reconciliation cursors. Deployment must still follow the rollback-image preservation procedure and run the complete project gate before cutover.
