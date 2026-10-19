# ADR 0028: Member-owned listening exports use a play ledger

- Status: Accepted
- Date: 2026-10-17

Listening export defaults off and uses each member's own provider authorization. A logical play has one stable identity across retries, with separate durable provider claims and account-generation bindings. Count elapsed playback, excluding seeks and previews; require structured track metadata, duration over 30 seconds and half the duration or four minutes heard. A lost acknowledgement remains uncertain and is reconciled by exact provider-history identity/time before it can be considered sent; it is never blindly retried. This favors avoiding duplicate scrobbles over guaranteeing delivery after an unknowable timeout. Replacing an account validates/stages its authorization before replacing working credentials, while disconnecting invalidates future pending dispatch. Browser caches contain IDs/counters, and provider credentials stay server-side and are removed from backups. Per-item progress, queue revisions and listening plays remain distinct.
