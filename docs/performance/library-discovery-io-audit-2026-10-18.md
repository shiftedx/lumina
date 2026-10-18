# Library discovery I/O audit — 2026-10-18

## Scope and result

Audit of external-library discovery: scan admission, traversal, stability tracking, indexing, completion hooks, persistent watcher state and header status. Three independent agents audited scheduling, per-file I/O and the status workflow; the coordinating agent sampled production read-only. No scan, filesystem write, configuration change, deployment or public release was initiated on production.

The recurring activity has real causes: the manager observer repeatedly reconciles registered folders, scans enumerate candidates before indexing, and unchanged runs still wake global maintenance. A pending/unreadable watcher file can also keep the header active indefinitely. This is avoidable work, but the screenshot alone does not establish physical drive wear.

Source baseline: `532553be078adc852633427487bb7aae5e3633de`. SHA-256 comparisons confirmed production matches the audited importer, watcher, automation, manager observer/sync, authentication, probe warming, artwork and caption-hook source. Production remains on its earlier application image; the newly merged release-updates feature is not deployed.

## Measurements and limitations

Private raw evidence is retained under ignored `output/library-discovery-audit/`; no household titles, paths, root IDs, hostnames or library inventory counts belong in this report.

| Passive production sample | Logical read bytes | Block-layer read bytes | Accounted storage writes |
| --- | ---: | ---: | ---: |
| 20 seconds overlapping rapid unchanged scoped scans | 18,848,715,584 | 65,536 | 1,982,464 |
| Separate quieter 20 seconds | 1,322,434,089 | 0 | 0 |

These are whole application-process counters, not isolated scanner attribution. The first sample includes millions of read syscalls; most logical bytes were not block-device reads. Do not extrapolate either short window into a daily drive-wear estimate. Linux documents `rchar` as read syscall bytes independent of page-cache hits; `read_bytes` accounts at the block layer and does not qualify NFS/CIFS accounting. See [kernel proc I/O documentation](https://docs.kernel.org/filesystems/proc.html#proc-pid-io-display-the-io-accounting-fields).

A separate 20-second shared NFS-client sample recorded 28,032 GETATTR calls, 50 READ calls and zero WRITE calls. Bind mounts exposed duplicate counters, so only one shared set was sampled. This is not per-process attribution and is not a NAS physical-drive measurement. No NAS controller/SMART/device wear qualification was performed. Read-only media mounts and the importer design prevent application media writes; catalog/WAL/cache writes remain separate.

Production run history confirmed bursts of successful, unchanged-only scoped imports on a root with Watch disabled and full-scan scheduling off. No active import remained at the end of the first sampling period. Thus the screenshot does not prove a single stuck full scan.

## Ranked findings

### 1. High: manager reconciliation continuously admits unchanged folders

`arr_observer.py:27,136-156` polls each minute and offers both pending and already registered file-bearing titles. `requests/sync.py:29-32,64-102` deduplicates exact folders, applies an in-memory ten-minute cooldown, and submits deep scoped runs. It checks an enabled external root and successful full baseline, but not the root's Watch flag or full-scan schedule. These runs are labelled `watch`, obscuring their actual trigger.

The enabled Lidarr infrastructure observer also runs each minute and offers every mapped artist with existing track files to the same rescan helper (`infrastructure_connectors.py:150-177,235-262,282-295`). Manual manager refresh invokes the same observation/rescan path (`routers/arr_observations.py:67-74`). qBittorrent, NZBGet and Prowlarr do not populate this Lidarr scan-path list.

The loop has no per-root admission budget or stop-after-success. Since drivers run asynchronously, several short scans can finish and admit later folders during one observer pass. Busy attempts do not consume cooldown. A large registered inventory therefore causes recurring reconciliation without an upstream change; restarting resets cooldown. Existing fairness coverage deliberately preserves rotation rather than suppressing redundant work.

Correction: retain discovery of new seasons and same-count replacements, but reconcile changed upstream file inventories preferentially, with an explicit slower bounded safety reconciliation and durable admission state. Label manager-driven activity separately. Do not simply disable registered-title reconciliation: that reintroduces the previously fixed missing-season/replacement defect. Qualify unchanged snapshots, new seasons, same-count replacements, busy-root fairness and restart behavior.

### 2. High: unchanged imports fan out into catalog-wide maintenance

`library_import.py:589-592` invokes all completion hooks even for unchanged-only runs. `probe_warming.py:142-146` invalidates its drained generation and queued IDs, waking the global artifact JSON eligibility query (`:225-246`). `renditions.py:582-604` wakes artwork planning; an empty plan scans title artwork and title images across the catalog (`:410-447`). Events may coalesce, so this is not necessarily one sweep per completed run.

The captions hook (`enrichment.py:751-765`) also queries every seen item by `MediaArtifact.last_seen_run_id`, which lacks an index. Production EXPLAIN chose `SCAN library_items`, then item-artifact and artifact primary-key lookups. A deliberately nonexistent run lookup returned zero rows in 161 ms in one warm sample. This demonstrates work unrelated to the scoped folder; it is not a latency distribution.

Correction: pass relevant changed artifact/title IDs and invalidate only affected work, retaining restart/periodic catch-up. File `unchanged` alone is insufficient: sidecar/art metadata can change independently. Qualify indexed/replaced/recovered files and sidecar-only edits. Any proposed index must improve actual EXPLAIN plans enough to justify its write cost and use an additive migration.

### 3. High for wide folders: counting and indexing repeatedly enumerate directories

`library_import.py:201-237,469-479,595-617` restarts sorted DFS from a cursor for every 200 candidates, materializing and sorting all entries of each visited directory before filtering. Discovery does this once, then indexing repeats it. Ancestor entries and the current wide directory are re-enumerated on each batch.

A deterministic synthetic 1,000-file flat folder required six discovery batches plus six indexing batches: 12 directory listings and 12,000 entries enumerated for 1,000 candidates. A 1,000-directory/one-file layout enumerated 14,010 entries with 2,022 listings. These are application enumeration counts, not physical disk reads. Wide-folder work grows superlinearly as batches increase.

Correction: remove the count-only traversal or retain bounded traversal state within the driver, with safe restart fallback. Progress may show an unknown total until enumeration ends. Keep cancellation, restart, sorted-cursor semantics, scope handling, disappearing files, symlink and mount safeguards. Measure enumeration counts at increasing synthetic sizes rather than relying on warm wall-clock timings.

### 4. Medium: status polling prevents background idleness

`frontend/src/app/useLibraryActivity.ts` polls every five seconds for each visible signed-in shell. The backend endpoint only reads in-memory automation status (`routers/admin_library_automation.py:57-58`); it does not start scans or aggregate the catalog. Automation refreshes its database snapshot every 15 seconds (`library_automation.py:306-324,497-514`). Most steady polls cannot reveal fresher persisted progress.

Authentication marks every request as activity (`security.py:509-512`). `background_work.py:14,31-33,78-99` retains a 30-second quiet threshold, so five-second polling keeps `busy()` true. A fake-clock probe confirmed it remains busy until 30 seconds after the final poll. Probe warming and optional captions consult this gate. Consequently merely leaving the app visible can delay background completion.

Correction: classify passive status reads separately from member activity, and align idle polling with status freshness or back off. Retain pause/abort for hidden tabs and genuine playback/request protection. A regression must show passive polling allows the idle gate to open while real user work still closes it. Authentication itself uses session/user PK lookups; its last-seen write is throttled to five minutes.

### 5. Medium: waiting/error files can keep the activity indicator alive

`library_automation.py:682-726` projects scan progress and independently sums waiting files from all watchers. `TopBar.tsx:43-52,114` keeps its ring active when waiting files exist. Three equal size/mtime observations across at least 90 seconds establish stability (`library_watch.py:35-38,289-311`); this is metadata checking, not repeated media-content hashing.

An OSError leaves the file pending, without advancing its last-attempt timestamp (`:302-304`). It can be retried on successive batches and displayed as copying/stabilizing forever. This audit did not establish that the screenshot's specific six files are unreadable; that remains unproven. Discovery counts come from the cached persisted batch counters and may lag the live driver.

Correction: separate active discovery, waiting for stability and unreadable/error states; timestamp failed attempts and use bounded retry/backoff. Qualify transient errors, recovery, deletion and continuously changing files. A waiting cohort should not imply an active full-library traversal.

### 6. High: scoped scans perform catalog-wide missing-file queries

`library_import.py:299-312,1078-1109` filters scoped paths with `substr`/`instr`, then counts all available artifacts and flags missing items even when the confirmed gone list is empty. An unchanged one-file scoped run still incurs this reconciliation cost.

On a synthetic 50,000-artifact fixture after ANALYZE, EXPLAIN selected `SCAN media_artifacts`. Retaining exact folder semantics while adding indexable path bounds changed it to a SEARCH on the existing unique root/path index. Across 100 warm queries for a synthetic 500-row folder, timings were 464.6 ms versus 65.4 ms. These are isolated query measurements, not production speedup claims. A no-gone early return also avoids two broad statements without weakening missing-file protection.

The new/moved-file lookup (`:631-645`) similarly lacks an index for root/size and selected a full scan in the synthetic fixture. Treat a composite index as secondary: qualify its benefit against migration and update costs.

Correction: indexable scope range predicates retaining exact subtree boundaries, and skip empty missing reconciliation. Reuse scoped removal/mount-loss contracts, including similarly prefixed sibling folders, deep versus shallow scopes and the mass-missing guard.

### 7. Medium: progress cohorts can accumulate behind a stuck run

`library_automation.py:662-723` retains completed runs until no active run remains. Scheduling can continue other roots while an unresponsive run stays active. Consequently both memory and the count labelled discovered can accumulate completed runs indefinitely. A synthetic one-active-discoverer/200-candidate fixture followed by 100 completed 40-file runs reported one discovering root but 4,200 discovered candidates and retained 101 records. This reproduces misleading progress, not the exact historical screenshot state.

Correction: bound cohorts and count active discovery separately from completed overlapping work. Expose stalled age/errors instead of retaining an apparently progressing phase indefinitely. Extend the existing concurrent-cohort contract with a stale run and many sequential terminal runs.

### 8. Medium: watcher persistence and repeated safety probes add write/metadata cost

`library_automation.py:434-472,754-769` persists baseline rows in 500-row transactions and refreshes every directory's timestamp hourly. A stale row can force rebaselining. This causes catalog writes even with no directory changes. Existing watcher microbenchmarks explicitly exclude actual traversal/network-storage cost.

A one-file scoped run also performs three online checks: discovery, indexing, and terminal missing-item protection (`library_import.py:449-470,519,619-622`). Each root probe performs root metadata/listing and filesystem capacity/identity checks (`storage_roots.py:34-65,181-253`). These are correct safety boundaries but expensive when redundant runs multiply them.

Correction: first reduce redundant admission and completion fan-out. Then consider a root-level baseline heartbeat and persist changed directory mtimes only. Preserve mount identity and terminal missing-file safety; do not cache away the final online check without proving removal protection.

## Recommended vertical slices and acceptance

1. Separate passive status polling from background activity; clarify pending versus active/error UI. Fake-clock/API and focused hook tests.
2. Bound manager reconciliation and persist change/cooldown state, preserving new-season/replacement discovery and fairness. Public observer/import contract tests.
3. Make scoped missing-file queries use the existing root/path index and skip empty removal work. Query-plan and scoped-removal/mount-loss contracts.
4. Target completion hooks and caption lookup to relevant changes. No-change scans must not trigger global probe/art plans; sidecar-only edits must still work.
5. Eliminate count-first/repeated traversal amplification. Synthetic enumeration budgets plus cancellation/resume/scope contracts.
6. Reduce unchanged watcher-baseline writes and add failed-stability backoff. Writer/call-count budgets and mount/disappearance regressions.

Do not combine these into a scheduler rewrite or disable discovery to make the counter disappear. Focused checks are appropriate for each slice. Per the user's instruction, defer the complete gate until production deployment or a public release is imminent. This audit applies no runtime fixes and makes no deployment or release claim.

## Verification

Scheduling/watch audit: 11 focused tests passed across registered inventory reconciliation, scoped admission/cooldown/fairness, watcher performance, offline-root protection and baseline writer duration. Status audit: four backend public-activity tests and five frontend poller tests passed. These passing tests characterize current behavior and safeguards; they do not certify the findings fixed. The status agent also passed two watcher-stability tests, one background-request-gate test and one activity-route contract. Per-file audit subset: three passed, 40 deselected.

A deterministic unchanged one-candidate shallow scope used 22 SQL statements and affected five rows: one artifact update for `last_seen_run_id`, three run updates and one run insertion. It made two lstat calls, three directory listings and one metadata description, with no media hash/tag/probe calls. A synthetic unchanged 1,000-file/100-folder full scan made 322 directory listings, 1,000 metadata descriptions and 89 SQL statements; no hash/tag calls occurred. Unchanged imports still update artifact last-seen state. Existing fingerprint/tag caches therefore work; eliminating all safety scans or assuming every file count is a content read would be incorrect.

The original audit used a zero-I/O traversal harness, subsequently replaced at `scripts/perf/audit_library_discovery.py` by a real-service temporary-fixture harness during remediation. That original baseline exercised the real walk against in-memory directory entries and counted enumeration across discovery/indexing. Its optimization target was:

```sh
mise exec -- backend/.venv/bin/python scripts/perf/audit_library_discovery.py --max-enumeration-factor 4
```

The pre-fix baseline exited 1 with 12 listings / 12,000 enumerated entries / factor 12 at 1,000 candidates. This intentional red result establishes a repeatable optimization target; it is not a failed product test being concealed. The budget allows two passes plus bounded overhead and is an audit target, not an existing project gate. No broad suite was run for this audit.


Remediation is recorded in [the focused fixes report](library-discovery-io-fixes-2026-10-18.md). The retained harness now measures the real import service with isolated temporary filesystem/SQLite fixtures; it creates no production work. The measurements above remain the original audit evidence.
