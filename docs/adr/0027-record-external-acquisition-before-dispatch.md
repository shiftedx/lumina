# ADR 0027: Record external acquisition before dispatch

- Status: Accepted
- Date: 2026-10-17

Sonarr, Radarr and Lidarr remain the owners of release selection, transfer and import under ADR 0018. Lumina records an allowed operation before its external write, admits one dispatcher for that intent/resource, and retains an uncertain outcome when acceptance cannot be proved. Successful dispatch receipts are durable before optional rereads; reconciliation only reads the original connection and known identities. An unresolved timeout blocks a competing acquisition rather than risking duplicate work. This deliberately accepts a need for upstream confirmation when the provider offers no idempotency or sufficient correlation. Music requests use artist/release-group identities and their own approval records; they never become movie/show requests or proof of a playable file.
