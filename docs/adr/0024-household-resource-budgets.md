# ADR 0024: Household resource budgets at admission and delivery

Accepted, 2026-10-16. Keep ADR 0006's single-process server and existing resource-specific workers. Persist household policy in `app_settings.resource_policy` and member overrides in `users.resource_overrides`; admission uses the existing serialized writer or an in-process lock, never a network wait inside a transaction.

## Frozen settings contract

`GET/PUT /api/admin/resources` reads/replaces the household object. Fields are `upload_bps` (null = disabled/unconfigured, otherwise positive integer bits/second), `lan_networks` (explicit CIDRs, default empty), and `defaults`. New defaults contain `remote_stream_bps=0`, `remote_member_bps=0`, `playback_sessions=2`, `video_encodes=1`, `download_running=1`, `download_queued=25`, `ai_running=1`, `ai_queued=10`. Allowances use zero for unlimited, subject always to household worker/upload ceilings. An upload budget therefore protects aggregate remote delivery without adding a source-quality ceiling; administrators opt into stream or member caps explicitly. Persisted nonzero allowances retain their values. Counts and rates are bounded integers. A stream ceiling cannot exceed a finite member bandwidth allowance.

`GET/PUT /api/admin/members/{user_id}/resources` reads/replaces overrides and returns `{overrides, effective}`; omitted/null override keys inherit. `GET /api/me/resources` returns the same shape for the authenticated member. Owners have the same policy and hard household ceilings. Owner settings authentication follows other admin settings. Member identity always comes from authentication or a verified playback grant.

Python contract: `resource_policy.household(db) -> HouseholdResources`, `resource_policy.effective(db, user_id) -> ResourceAllowances`. Pydantic models expose the field names above as attributes. `resource_policy.remote(request, policy) -> bool` defaults ambiguous connections to remote. `request.state.resource_user_id` is set only after server authentication. Delivery reads fresh policy for active transfers. Upload limits are independent of acquisition ingress limits.

## Enablement and migration

Upgrade leaves upload protection unconfigured and existing playback compatibility intact until `upload_bps` is supplied. Proposed playback and bandwidth defaults take effect together on enablement. Work queue defaults take effect immediately, preserving configured household `concurrency`, `ai_max_concurrency` and `max_playback_sessions`. The old download `max_active_jobs_per_user` cap remains a queued-plus-running compatibility ceiling; the new queued allowance counts waiting work only. Lower limits retain work and existing stream allocations; additional admission/dispatch waits or refuses until capacity exists.

## Playback and delivery

Reserve stable allocations before selecting quality, with aggregate member and household accounting across devices. Reuse logical sessions on seeks/retries; replace/release on supersession, stop, startup failure, cancellation and ten minutes without activity. Restart drops ephemeral sessions and reservations together. Paused streams expire on the same idle deadline. Budget the whole media stream including audio and mux overhead, leaving delivery headroom. Unknown bitrate under a cap forces bounded conversion or a clear unsupported outcome. Quality/audio/subtitle choices re-enter policy. A conversion failure never returns an unrestricted original.

Pace actual ASGI response bodies in bounded chunks with shared monotonic token accounting and cancellation-aware waits. A persistent household/member bucket grants its initial burst once, not once per request; parallel full/range/segment/download transfers share it. Disable pathsend/sendfile and proxy buffering/cache bypasses for protected delivery. Application bytes exclude TLS/IP overhead; only Lumina's traffic is controlled, and router SQM remains useful for whole-household latency. External URLs and oversized live/non-transcodable sources must be refused under capped policy unless an enforceable suitable rendition is available.

LAN exemption requires an explicitly configured network and verified provenance. The direct peer must be outside the trusted proxy set with no forwarding ambiguity, or a trusted proxy must supply the validated supported canonical client chain. Host names and private proxy peers alone give no exemption. Tunnel visitor headers never prove locality; tunneled and ambiguous traffic stays remote. VPNs are remote unless their verified network is explicitly exempted.

## Work ownership

Admission checks duplicate/cache hits before charging, then atomically admits durable work. Dispatch is round robin by member and FIFO within member, without preemption, within each existing resource-specific worker pool. AI member accounting spans expensive member-requested job kinds; child stages keep the parent's charge. Owner automation is charged to that owner; unattributed maintenance retains its existing bounded worker lane and background yielding (ADR 0020). Recovery rebuilds durable queue state; terminal transitions release charges exactly once.
