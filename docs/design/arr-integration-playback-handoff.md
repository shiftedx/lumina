# Arr integration and playback implementation handoff

## Mission and scope

Act as project manager and integration owner. Coordinate bounded subagents in parallel to deliver working broader arr integration and playback improvements from the [implementation plan](arr-integration-playback-plan.md). The user explicitly requested this handoff for a next agent managing parallel subagents. Own decomposition, shared contracts, integration, acceptance and final reporting; workers own clearly assigned implementation slices.

Implement Phases 1–5 in focused increments, honoring each conditional slice. Phase 6 is a later native-acquisition option: deliver its decision record and prerequisites; implement a native pilot only after explicit user selection and an accepted replacement for ADR 0018's affected boundary. Continue useful integration/playback work while optional provider or device qualification is blocked. Record every slice as implemented, conditionally deferred with evidence, or blocked with the exact missing input.

An opt-in user setting is still an implementation slice: crossfade and listening export should not disappear merely because they default off. Development deferrals require an explicit plan decision gate or concrete unavailable dependency; missing hardware/account access is a qualification gap, not proof of interoperability.

This handoff governs execution. The implementation plan owns feature scope and phase exit checks. Repository `AGENTS.md` owns coding/testing rules. Inspect current code before treating research findings or proposed file names as current facts.

## Execution sequence

1. **Establish baseline.** Read `AGENTS.md`, `CONTRIBUTING.md`, the implementation plan and applicable skills. Inspect branch, Git status, current source and prior implementation records. Establish the repository's issue/PR context under the current session's authorization. Preserve pre-existing edits. Complete when revision, baseline checks, slice statuses and exclusive file ownership are recorded in the delivery record below.
2. **Freeze contracts.** Before workers write dependent code, agree endpoint/data shapes, external identity mapping, visibility, connector capabilities, operation reconciliation, session revisions/conflicts, offline migration and errors. PM owns shared schema/route changes and required ADRs. Complete when each active packet names a concrete contract and every shared file has one writer.
3. **Deliver Phase 1.** Dispatch arr observation, integration UI and connector discovery concurrently. Retain existing request behavior; observations must work with Requests disabled. Complete only when the Phase 1 exit checks pass through backend and UI, including upstream-added inventory, pagination, stale-state handling and visibility.
4. **Run parallel workstreams.** After Phase 1, run acquisition controls/connectors alongside shared music-session/playback work. Keep prerequisites local to a slice rather than waiting for every connector. Dispatch only ready packets from the table below. Complete a packet only after its code, focused checks and evidence meet the named plan exit checks.
5. **Integrate journeys.** PM joins independently tested slices: external title → status → permitted action → import → Play; album request → mixed-availability queue → visible tracks; browser A → browser B → existing Sonos output → recovery. Complete when these workflows pass against the integrated tree, including conflict, timeout, outage and revocation paths.
6. **Qualify conditional features.** Verify provider contracts before exposing connector actions; qualify the selected additional output on real hardware; measure local-conversion behavior before selecting an adaptive ladder. Complete each conditional slice with either passing evidence or a specific evidence-backed deferral. Keep unsupported controls absent or clearly unavailable.
7. **Review and deliver.** Obtain independent scope/correctness review, resolve feature regressions, run the required integrated gate and update operator documentation. Complete when the delivery record accounts for every slice, check, qualification and unresolved blocker; a passing worker report alone is insufficient.

## Reading by branch

| When working on | Read before editing |
| --- | --- |
| Arr ownership, requests or operations | Plan Phases 1–3; ADR 0018; `services/requests/{arr,engine,sync,status}.py`; existing request API/service tests and arr fake |
| New persistence or external dispatch | ADRs 0004 and 0006; current `MIGRATIONS`; migration/rollback conventions in `docker/README.md` |
| Music sessions, offline or output transfer | Plan Phase 4; ADR 0023; `musicSession.ts`, `musicCastSession.ts`, `musicEngine.ts`, `MusicPlayer.tsx`; current music and Sonos services/tests |
| Playback defaults, video output or local adaptation | Plan Phase 5; ADRs 0010, 0012 and 0024; current local/Jellyfin decisions and sessions; existing remote-streaming qualification |
| Member visibility or generated media | ADRs 0019 and 0013; existing visibility/provenance predicates |
| Native release acquisition or retention | Plan Phase 6; ADRs 0018, 0004 and 0005; make the native decision before dispatching implementation |
| Verification or release preparation | `docs/VERIFICATION.md`, current Makefile and `CONTRIBUTING.md` |

## Parallel packets and ownership

Use available supported workers according to current session/repository routing. Check runtime model availability once; if a configured worker model is unavailable, record that and choose an allowed supported alternative. Use in-task subagents for these packets; create separate user-visible chats only when the user explicitly requests them. Keep at most the available worker slots active; logical packets outnumber simultaneous workers.

Every dispatch must say: **You are not alone in the codebase. Preserve others' edits and adapt to shared changes.** Replace the candidate paths below with exact ownership before dispatch. Proposed new files are starting points; surrounding code determines the final seam.

| Packet | Exclusive responsibility and candidate paths | Ready when / completion evidence |
| --- | --- | --- |
| A Arr backend | Existing bounded arr client; new observation service/router; external item correlation, paginated reads, then operation ledger/controls; focused arr tests | PM contracts ready. Phase 1 and then Phase 2 exit checks pass; provide timeout/reconciliation and permission evidence. |
| B Integrations UI | `frontend/src/features/integrations/**`, assigned request-settings extraction and assigned browser specs | Read/write shapes frozen. Admin operations and member-scoped title explanations match real backend behavior, including disabled Requests and stale data. |
| C Connector discovery and adapters | Per-provider new modules and dedicated contract tests for Lidarr, Bazarr, Prowlarr and a selected downloader | Discovery can start immediately; writes wait for PM contracts and provider evidence. Return version/auth/capability matrix, fixtures, supported behavior and qualification gaps. |
| D Music session backend | New music-session service/schema/router; exclusively assigned existing music/Sonos route or service files | Shared session contract ready after Phase 1. Two-client revision/conflict checks and acknowledged Sonos recovery pass; old local queue IDs are revalidated through visibility. |
| E Music frontend | Assigned files under `frontend/src/features/music/`, session/cast/engine tests and music browser journeys | Session shapes frozen. Shared restoration, mixed-availability state, offline reconciliation, crossfade/timer capability behavior and handoff recovery pass. Bind music acquisitions after Lidarr correlation exists. |
| F Playback defaults and local resilience | Assigned local/Jellyfin playback files, caption preference files and video tests | Track policy contract ready; shared-session output contract before new output control. Preserve overrides/resource budgets; provide real-decode and shaped-network evidence for conversion changes. |
| G Independent review | Read-only integrated diff and selected user workflows | A vertical slice is integrated. Return actionable findings with file/line and reproduction, or a bounded no-findings result; review ownership, policy bypass, duplicate dispatch and overstated capability claims. |

PM retains `backend/app/{db,models,main}.py`, shared schemas/authentication, `frontend/src/{api,types}.ts`, route/settings registries, ADR numbering and shared operator docs unless ownership is explicitly transferred. Workers return required shared-file edits to PM; they do not independently append migrations or edit the same integration seam. Transfer ownership only after the previous owner has finished. PM serializes these edits and publishes the updated contract.

Suggested waves with three worker slots:

- **Wave 1:** A read observer + B integration UI + C connector discovery; PM integrates schema/contracts and closes Phase 1.
- **Wave 2:** A acquisition controls + B controls UI + D shared-session backend; connector implementation waits for its verified contract and a free slot.
- **Wave 3:** C Lidarr/verified adapters + E music frontend + F language/default improvements; PM connects acquisition/session boundaries.
- **Wave 4:** Schedule remaining verified connectors, optional listening export, additional output and evidence-gated local adaptation in independent packets. Give one worker all files in a coupled output implementation.
- **Review:** G examines each integrated slice; other ready workers continue disjoint work. PM runs one integrated full gate after merging, rather than concurrent full suites from every worker.

## Contract boundaries to enforce

- **Authority:** Sonarr/Radarr/Lidarr own managed release selection, import, naming and upgrades. Prowlarr owns indexer configuration. Arr-owned transfers receive commands through their owning manager. Native ownership changes are a separate later decision.
- **Visibility:** upstream inventory and registered playable media are different objects. Keep unmatched inventory admin-only under the plan policy; Play requires a member-visible registered file. Client URLs/manifests and raw upstream diagnostics never become authoritative server queue entries.
- **Uncertain writes:** persist the operation before dispatch; reconcile upstream identity/history after timeout. If success cannot be established, retain an explicit uncertain state and prevent blind redispatch. The existing yt-dlp outbox does not prove arr command idempotency.
- **Session continuity:** retain per-item progress and existing browser↔Sonos transfer; extend them with shared session revisions and output acknowledgement. Preserve intentional rewinds, define offline conflicts and keep failed transfers recoverable.
- **Capabilities:** keep crossfade/gapless/output capabilities separate. Existing remote HLS/DASH already adapts; measure additional adaptation only for local conversion. ADR 0024 budgets apply to every new encode/output path.
- **Scope:** reuse current requests, policies, import, lyrics, normalization, offline music, Sonos and playback decisions. Broader integration means supported household workflows, not arbitrary API forwarding or duplicating every upstream settings screen.

## Dispatch template

```text
Task: One bounded implementation objective; plan phase/slice and contract revision.
Inputs: Exact plan section, relevant ADRs, baseline and agreed API/data shapes.
Ownership: Exact allowed files; shared edits to return to PM; named dependencies.
Constraints: You are not alone in the codebase. Preserve others' edits and adapt to shared changes.
Acceptance: Observable workflow checks and error/recovery cases from the plan.
Done evidence: Files/diff summary; commands, exit results and relevant output; contract changes; remaining qualification/blockers.
Escalation: Return ambiguity, ownership conflicts or a failed refined retry to PM instead of expanding scope.
```

## Acceptance and delivery record

Create `docs/design/arr-integration-playback-implementation.md` at kickoff and maintain:

- Baseline revision/dirty files, existing issue context and current check results.
- Frozen contracts/ADRs and packet owner, exact files, dependencies and status.
- Slice checklist keyed to plan Phases 1–6, with evidence links and explicit conditional decisions.
- Integrated workflow results, real-service/device versions and measured media results where applicable.
- Connector capability matrix: configuration, reads, writes, unsupported actions and verified versions.
- Migration/rollback coverage, operator setup, permissions and remaining qualification requirements.

The previous planning run recorded `5009 passed, 11 skipped, 1 failed` in the backend suite; the failure was `test_live_recording_scheduling_manager.py::test_scheduling_starts_in_waiting_and_runs_the_waiter`. Recheck the current baseline rather than assuming that result persists. Fix feature regressions; characterize any independent baseline failure without masking it. A full gate with a known failure is not a clean pass.

Extend existing behavior contracts and use focused checks during implementation. Run `mise exec -- make check` before implementation handback/PR; local remux/transcode changes also require the real-decode checks specified in `docs/VERIFICATION.md`. Keep performance measurements isolated from other heavy checks. Mocks establish contracts; real provider/device results establish interoperability. Record both distinctly.

Final handback: concise additions → user benefits, completed slices and conditional deferrals, exact validation results, remaining blockers, and links to the implementation record and any authorized PRs. Follow the current user's authorization for external publication and merge actions.
