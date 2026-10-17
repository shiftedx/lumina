# Household resources: implementation handoff

## Mission and authority

Act as project manager and integration owner. Orchestrate a swarm of bounded subagents to implement household resource defaults, per-member overrides, fair download/AI admission, and bandwidth-aware playback. Deliver tested working code and operator documentation, not another proposal. The user explicitly requested swarm orchestration for this feature.

The user wants remote playback to transcode a technically playable 100 Mbps Blu-ray when necessary to preserve household upload headroom for competitive gaming. Keep request-frequency protections internal; expose bandwidth, playback, download, and AI controls to the owner.

This document is the feature's acceptance contract. Numeric defaults below are proposed starting values; the PM may refine them with repository evidence and record the reason. Preserve the behavioral requirements. Ask the user only for decisions that block implementation; implement configurable upload capacity without requiring their actual connection speed.

Read repository `AGENTS.md`, `CONTRIBUTING.md`, and applicable skills first. Follow the session's supported model routing; if its requested worker model is unavailable, report that and use an available supported worker rather than repeatedly attempting invalid spawns. Keep planning, shared contracts, integration, and final acceptance with the PM.

## Execution sequence

1. **Establish baseline.** Inspect Git status, branch, and current source before assigning work. Preserve existing edits and distinguish pre-existing check failures. Read ADRs 0001, 0004, 0006, 0012, 0013, 0014, 0019, and 0020 when touching their respective deployment, jobs, persistence, playback, AI, access, or background-work seams. Complete when the PM records baseline revision, checks, route inventory, and exclusive file ownership.
2. **Freeze contracts.** Record settings fields, inheritance semantics, connection classification, admission errors, bandwidth units, session lifecycle, and task ownership. Write an ADR using the next available number. Complete when workers can implement against concrete data shapes without independently editing shared models or inventing conflicting policy.
3. **Build foundation.** Implement durable settings, member overrides, effective-policy resolution, validation, and migration/rollback. Complete when inheritance, authorization, upgrade, and backward compatibility tests pass.
4. **Run parallel implementation.** Dispatch playback admission, outbound pacing, download fairness, AI fairness, and frontend packets as capacity permits. Use the ownership and dependency rules below. Complete each packet only with its required evidence.
5. **Integrate vertical journeys.** Connect web and Jellyfin playback from policy resolution through actual bytes; connect settings through effective backend enforcement; verify work admission and cancellation. Complete when the acceptance matrix passes across all supported routes.
6. **Qualify and deliver.** Run focused suites and `mise exec -- make check`, exercise real media delivery through the supported proxy topology, and record bandwidth measurements. Report final diff, commands/results, limitations, and any remaining blockers. A missing supported media path or unmeasured pacing claim is incomplete work.

## Product policy

| Setting | Starting value | Scope |
| --- | --- | --- |
| Remote upload budget | Unconfigured until owner supplies Mbps | Household aggregate |
| Remote stream quality ceiling | 8 Mbps | Each remote stream |
| Remote member bandwidth | 10 Mbps | All remote connections for one member |
| Simultaneous playback | 2 | Member, across devices |
| Simultaneous video encodes | 1 | Member, also bounded by household capacity |
| Running acquisitions | 1 | Member |
| Queued acquisitions | 25 | Member |
| Running AI jobs | 1 | Member, also bounded by resource-specific workers |
| Queued AI jobs | 10 | Member, shared across included AI job kinds |

- Preserve existing configured household worker capacities. Explicitly document migration behavior and distinguish queued jobs from queued-plus-running counts in the old download cap.
- An absent member override inherits the household default. Define explicit unlimited semantics independently from inheritance; validate positive finite values and cross-field relationships. Display Mbps; use one documented integer unit internally.
- Household capacity is a hard ceiling for everyone, including owners. Owners may raise member allowances within it.
- An unconfigured total upload budget must be shown as unconfigured, with no claim that household upload is protected. Preserve existing playback compatibility until the owner enables the policy; offer the proposed defaults when enabling it. Record the chosen enable/migration semantics in the ADR.
- Enforce one member allowance across browser sessions, connected apps, app passwords, and Jellyfin clients. Resolve identity from server authentication or a verified playback grant, never a client-selected user ID.
- Keep member access restrictions and client capability limits in force. The effective playback ceiling is the strictest applicable limit.
- Ordinary API activity retains internal safeguards. Correct authenticated shared-IP coupling without weakening unauthenticated login protections or making request-rate controls part of the owner-facing feature.

## Playback contract

### Classification and admission

Classify local versus remote from verified connection provenance and configured LAN networks. A private reverse-proxy peer, Host header, or client-supplied forwarded address alone does not prove a local viewer. Cover tunnel, IPv4/IPv6, trusted proxy chains, VPN, and ambiguous cases; ambiguous traffic receives remote policy. Document the supported classification and LAN exemption configuration.

At playback admission, atomically reserve the member's session/encode slots and an allocation within both member and household remote bandwidth budgets. Pick a compatible quality within that allocation. Account for video, selected audio, mux overhead, and delivery margin; video `maxrate` is not a total-stream cap. Unknown source bitrate gets conservative handling.

Direct play is allowed when the source fits the applicable policy. Otherwise choose a lower-bitrate conversion even when the client supports the original. Quality selections and new audio/subtitle choices re-enter policy. A failed or unavailable transcode returns a useful error; it never falls back to an over-budget original.

Keep existing stream allocations stable in the first version. Admit a new stream at a lower viable quality or refuse it clearly. Defer automatic redistribution and repeated midstream quality restarts. Release reservations on stop, expiration, failed startup, supersession, and cancellation. Preserve allocations across seeks and retries of the same logical session without double charging. Define paused-stream expiry and crash/restart recovery explicitly.

### Byte delivery

Quality selection and byte pacing are separate requirements. A small, bounded burst is allowed; repeatedly opening requests must not replenish it. Use monotonic time, cancellation-aware async waiting, bounded chunks, and shared accounting across simultaneous connections. Avoid blocking the event loop or holding database transactions/locks while waiting for network allowance.

Enforce household and member aggregate caps on actual remote response bodies, including full files, byte ranges/multipart ranges, HLS/DASH segments, cached responses, relays, and remote member file downloads. Preserve HEAD, range, conditional, seek, pinned-file cleanup, and disconnect semantics. Review sendfile/pathsend and proxy buffering/cache paths for bypasses. Count a response once even if it crosses multiple wrappers.

Inventory local web playback, Jellyfin direct/transcode routes, on-demand remote media, live relay, and export/download paths. For each, record identity, classification, quality control, pacing, and tests. An external provider URL bypassing Lumina cannot be advertised as bandwidth protected: use an enforceable relay/rendition or clearly refuse capped playback when no such path exists. Never silently return an unrestricted URL.

Define the pacing envelope as bytes sent <= rate * elapsed + burst allowance, including the initial burst, under parallel requests. Reserve enough delivery headroom for the chosen media rate. Document that application-byte accounting excludes TLS/IP overhead and that Lumina controls its own traffic; router SQM remains relevant to whole-household latency.

For live/non-transcodable paths that cannot satisfy the quality ceiling, select an enforceable lower rendition or return a clear unsupported-policy outcome. Pacing an oversized stream into permanent buffering is not successful playback.

## Download and AI contract

- Reuse existing job managers and durable jobs. Keep separate resource-specific worker capacities; avoid building a speculative universal scheduling framework.
- Make check-and-admit atomic. Check duplicate/cached work before allocating another queue or execution slot. Charge owner-attributed automation work to its owner; explicitly define unattributed maintenance work.
- Select the next eligible member in round-robin order, FIFO within that member. New work from a prolific member cannot perpetually bypass another eligible member. This is dispatch fairness, not preemption of already-running jobs.
- Apply AI member allowances across summaries, ASR, subtitle generation/translation/sync, idea graphs, and other expensive member-started AI jobs identified by inventory. Preserve model/process concurrency and ADR 0020 background yielding.
- Define retries, chained child jobs, failures, restart recovery, and cancellation so slots are neither leaked nor double charged. Cached results consume no new execution slot.
- Limit reductions allow running work to finish and retain queued work, while preventing additional admission/dispatch above the new limit. Cancellation and owner recovery remain available at capacity.
- Distinguish acquisition traffic entering the server from remote member file downloads leaving it. Remote file downloads share the upload cap. An optional acquisition bandwidth cap must be aggregate across workers if exposed; per-process limits alone cannot implement that promise.

## Swarm ownership and dependencies

Assign explicit files before each dispatch. The paths below are orientation, not permission for overlapping edits. Tell every worker: **You are not alone in the codebase. Preserve others' edits and adapt to shared changes.**

| Packet | Responsibility and likely files | Dependency / done evidence |
| --- | --- | --- |
| PM: foundation | `models.py`, `schemas.py`, `db.py`, configuration/settings contracts, new policy resolver, ADR and rollback | Migration, authorization, inheritance tests; published contract |
| Playback worker | `routers/local_playback.py`, `services/playback_decision.py`, `services/jellyfin_playback.py`, session lifecycle | Foundation and reservation interface; forced-conversion and lifecycle tests |
| Delivery worker | New bandwidth implementation, `services/media_response.py`, relay response integration | Foundation and authenticated delivery context; pacing/cancellation/range tests plus measurements |
| Downloads worker | `services/job_manager.py`, acquisition admission and selection | Foundation; atomic caps, fairness, recovery tests |
| AI worker | Identified AI job managers and member-job admission | Foundation; cross-kind queue/running accounting, fairness and cancellation tests |
| UI worker | Settings/member controls, player explanations, `api.ts`, `types.ts`, frontend tests | Frozen endpoint/error shapes; inherited/override/reset and playback journeys |

The PM owns `main.py`, `security.py`, shared HTTP glue, and shared model/schema edits unless explicitly transferred to one worker. Split playback session reservation hooks from delivery pacing through an agreed small interface. Schedule work in waves according to available slots; logical packets need not all run simultaneously. PM integrates worker requests for shared-file changes serially.

Use this dispatch format:

```text
Task: One bounded objective from the packet table.
Inputs: This handoff, frozen contracts, relevant ADRs, baseline revision.
Ownership: Exact allowed files; identify shared-file changes to return to PM.
Constraints: You are not alone in the codebase. Preserve others' edits. Stay within scope.
Acceptance: Specific rows of the acceptance matrix plus focused tests.
Done evidence: Files changed, behavior/diff summary, commands and results, limitations/blockers.
Escalation: Report ambiguity or a failed refined retry to PM instead of guessing.
```

## Acceptance matrix

| Area | Required observable result |
| --- | --- |
| Policy | Household default, explicit override, reset-to-inherit, owner authorization, invalid values, and additive upgrade are tested |
| Identity | Same-IP members have independent activity budgets; one member's devices/tokens share resource budgets |
| Quality | A compatible 100 Mbps source under an 8 Mbps remote ceiling converts; client Original and forged bitrate arguments cannot bypass it |
| Aggregate admission | Simultaneous starts cannot reserve beyond member or household bandwidth/session/encode capacity |
| Transport | Concurrent full/range/segment/download requests obey the documented rate-plus-burst envelope; request churn cannot renew bursts |
| Compatibility | Web and Jellyfin direct/conversion paths enforce the same policy; existing seek/audio/subtitle/HEAD/range behavior passes |
| Classification | Local exemption is verified; private proxy peers, forged forwarding headers, tunnel/VPN and IPv6 cases cannot accidentally gain exemption |
| Unsupported media | Missing bitrate, unavailable encoder, source redirects and live/remote paths produce conservative enforceable behavior |
| Lifecycle | Stop, cancellation, failed startup, retry, seek, stale session and restart leave no leaked/double-counted reservations |
| Work queues | Concurrent admission respects caps; two members receive fair dispatch; duplicate/cached jobs, retries and automations are attributed correctly |
| Settings changes | Lower limits preserve existing work with documented application timing; pacing changes apply to active delivery without unrestricted bursts |
| UI | Owner can inspect defaults/overrides and reset them; members see understandable capacity/forced-conversion reasons; cancellation remains usable |
| Performance | Measured aggregate bytes over short and sustained windows pass through the supported proxy; local startup/seek and event-loop responsiveness remain acceptable |

Use deterministic clocks for counter/pacing tests and real delivery for final qualification. Record numeric rate, burst, duration, parallelism, proxy buffering configuration, measured bytes, and any tolerance justified by buffering. Distinguish server send measurements from client-observed wire measurements. Synthetic fixtures use documentation hosts/addresses and invented media only.

## Delivery record

Maintain concise progress and evidence here or in a linked implementation record: baseline, frozen contracts, packet owners/status, route coverage, check results, measurements, and unresolved risks. Consult `docs/VERIFICATION.md` and current Makefile for commands. Full `mise exec -- make check` is required before handback/PR; distinguish baseline failures from feature regressions and fix feature regressions.

Follow repository PR conventions and user authorization for publishing/merging. Do not declare bandwidth protection complete on encoder arguments or advertised playlist bandwidth alone. The delivered feature must enforce actual byte delivery and account for aggregate concurrent activity.
