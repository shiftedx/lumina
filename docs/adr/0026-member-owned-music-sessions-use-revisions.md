# ADR 0026: Member-owned music sessions use revisions

- Status: Accepted
- Date: 2026-10-17

## Context

Per-item playback progress does not describe the member's deliberate listening queue. Browser-local queues cannot restore across browsers and can overwrite intentional rewinds if reconnect merges choose the furthest position. Output capabilities and offline copies have different authority from the current shared queue.

## Decision

Keep one server listening-session row per member, with an ordered list of item IDs (including duplicates), selected index, finite position, repeat/shuffle state, a credential-free output descriptor and a revision. A whole-session update requires the current base revision; creation requires revision zero. Concurrent edits return a conflict with the current server snapshot. Queue revisions remain separate from per-item progress.

The server revalidates IDs using current member visibility and rebuilds manifests from registered music data. It prunes stale/hidden items without returning their old metadata, adjusts cursor/position and reports a removal count. Browser storage supplies only provisional offline state and IDs for initial migration; its URLs, manifests and metadata never establish server authority.

Logical queue entries may name either a registered track or the member's own approved/pending music request. They remain distinct from playable track manifests. A request resolves only through a matching music release group and visible registered files in an enabled root; upstream download flags cannot resolve it. Keep logical order/cursor beside the resolved playback queue. Skip advances to the next playable entry; Wait places a hard playback/prefetch boundary at the unresolved entry. Restoration stays paused, and only an already active explicit wait intention may continue when import resolves the entry.

On reconnect, unchanged revisions can accept the local snapshot, including a lower intentional position. A diverged revision requires an explicit choice to use the server session or keep this device's snapshot against the new revision; a second race returns another conflict. There is no furthest-position merge. Restoring a queue does not automatically start audio or recreate a remote output capability.

Remote output transfer must acknowledge the target queue/item/position before stopping or revoking the source. Sonos/Chromecast credentials and media capability lifecycles remain separate from the session row. A credential-free output descriptor alone never claims an output is still playing.

An acknowledged transfer performs one serialized session transition and adopts its returned revision before ordinary checkpoints resume. Exact queue-index mappings preserve duplicate tracks and filtered output queues. A remote descriptor requires a live owner-bound ticket/status; visibility pruning also invalidates an output whose granted items were removed.

## Consequences

Schema 25 adds `music_listening_sessions`. Rollback preserves the table and stamps schema 24; older releases ignore shared queue state while retaining per-item progress. Two-client API/browser workflow checks accompany persistence. Offline saved audio remains member-scoped. Current visibility, capability revocation, decoding memory limits and household resource admission still apply at playback time.

## Native preparation lifetime

The native audio engine schedules file segments instead of materializing whole source/converted PCM buffers. Each prepared slot owns its `AVAudioFile` and, for remote input, the private bounded download backing it. Retain at most the current and scheduled/retiring inputs; the existing conservative admission estimate and 128 MiB input-file limit remain. A suspended source lookup retains only scheduling scalars, so cancellation cannot pin a stopped slot's input.

Keep remote input files mode 0600 with the existing iOS data protection. Pause retains the prepared input without another download; retirement, completion, Stop, owner teardown, authorization failure and canceled preparation release it. Original/offline local inputs stay owned by their existing library. This is active-session temporary storage, not a durable or shared member cache. Full remote delivery still precedes engine scheduling when gain/crossfade processing is required; native read-ahead removes whole-track PCM preparation, not network admission or delivery policy.

For queues of admitted, qualified music files with crossfade off and unity effective gains, start remote files using AVQueuePlayer through a private incremental AVAsset resource loader. Honor requested byte ranges without accumulating a whole input or creating a shared/durable cache. Each active/lookahead asset owns an ephemeral URLSession with no cookie/credential/cache storage; redirects remain same-origin and declared resource length must be positive and at most 128 MiB. Stop, owner teardown and retirement cancel the asset's network tasks. Authorization failure is scoped to an asset still owned by the runtime, including after pause/resume, and tears down playback. A failed lookahead is dropped without a clock-driven retry loop.

This first progressive slice preserves the existing metadata/admission ceiling. A two-second AVFoundation forward-buffer preference is a target, not a hard memory bound. Unknown/large inputs retain the prior streaming route. Queues needing gain processing or an unqualified format retain engine preloading from the first occurrence; local successors can join a source-playback queue without another download. Crossfade retains the file-backed engine route; enabling them during progressive playback reuses the current occurrence/position without publishing another track-start event. Progressive seek and pause reuse the current asset. Pause suspends all owned progressive HTTP tasks, including lookahead, and new requests remain suspended. Resume reactivates those tasks without resolving another source. A paused seek temporarily permits reads on the current asset; overlapping seeks retain that permission until all complete, after which paused transfers suspend again. Stop and teardown cancel suspended tasks as well as running tasks. No output format or server encoding policy changes.
