# ADR 0020: Optional media backfills yield to app activity

- Status: Accepted
- Date: 2026-10-12

## Context

Full-file loudness measurement and automatic caption ingestion can keep a large library's server busy for days without a viewer requesting those results. Niceness yields under contention but does not make an otherwise idle server quiet. Background work must also yield during browsing and direct playback, rather than only during video encoding.

## Decision

- Automatic loudness and caption backfills are disabled by default. Existing caption jobs remain durable and pending. Operators can opt into a daily maintenance window through `LUMINA_BACKGROUND_BACKFILL_START_HOUR` and `LUMINA_BACKGROUND_BACKFILL_END_HOUR`, using the server's local timezone. The start is inclusive and the end exclusive; overnight windows are supported.
- Successful authenticated HTTP requests mark app activity, except the passive `GET /api/library/activity` progress read. Registered optional media subprocesses receive SIGSTOP when activity begins, remain paused while requests are in flight, and resume only after 30 seconds quiet. A media request's entire streamed response counts as activity. Passive GET server-sent event streams release their request count after response headers and retain only the quiet grace period. Authentication failures and unauthenticated health probes do not extend the activity deadline.
- Requested-item loudness work and explicitly requested caption bulk work can run outside a maintenance window once the app is quiet. Explicit interactive caption generation and subtitle playback extraction retain their immediate path. A request racing caption admission records demand durably and promotes the same child; promoted children retain shutdown ownership.
- Registered background media children share one running slot, in registration order among eligible children. Interactive subtitle extraction does not use that slot.
- Automatic workers recheck the window and activity between stages and while children run. Timeout accounting excludes paused time; cancellation and shutdown resume a paused child before terminating it. Graceful shutdown reaps registered children and joins background caption workers. Cached results and durable restart recovery are preserved.
- The activity gate is in-memory in the existing single-process server. It stores no user, token, URL, or media metadata. Scheduled recordings and backups are outside this optional-work policy.

## Consequences

The default server does less optional analysis without changing the media playback decision or schema. Fresh normalization measurements and automatically ingested captions may wait until a requested-item quiet period or an operator-enabled maintenance window. Authenticated polling conservatively counts as activity except the identity-free library progress endpoint. This passive endpoint must let the idle grace period elapse; browsing, playback and all writes retain activity protection. Empty queues and disabled backfills must avoid repeated broad database selection.

Amendment (2026-10-22): finite POST household-transfer streams retain authenticated activity until the response finishes or disconnects. They perform user-requested work, so optional analysis must remain paused while histories are read and committed. Passive GET subscriptions retain the header-time release.
