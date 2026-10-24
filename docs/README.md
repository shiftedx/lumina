# Documentation

## Choosing and using Lumina

- [Shared brand identity](../apple/docs/brand-assets.md): approved sunrise assets, adaptive control roles, and cross-platform change/verification procedure.
- [Photo library](photos.md): uploads, synced NAS folders, albums, local people filtering, and original-file backups.

- [Lumina vs Plex vs Jellyfin](lumina-vs-plex-vs-jellyfin.md): compare media servers by household needs, client support and operating model.
- [Jellyfin clients and migration](jellyfin-clients.md): connect Infuse, Swiftfin, Roku and Android TV; understand compatibility limits and import watch history.
- [Frequently asked questions](faq.md): what Lumina does, Docker requirements, licensing, online sources and local AI.

## Operating and developing Lumina

- [Maintaining public discoverability](discoverability.md): source-backed descriptions, website follow-through and search measurement.
- [`../docker/README.md`](../docker/README.md): operator guide for the Docker deployment (startup, remote access, volumes, backup and restore, upgrades and rollbacks).
- [`../RELEASE_NOTES.md`](../RELEASE_NOTES.md): changelog.
- [`../SECURITY.md`](../SECURITY.md): supported versions and how to report a vulnerability.
- [`BUILDING.md`](BUILDING.md): toolchain setup and the clean-clone build.
- [`VERIFICATION.md`](VERIFICATION.md): which command verifies which kind of change.
- [`adr/`](adr/): architecture decision records.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md): how to contribute.
- [`../AGENTS.md`](../AGENTS.md): conventions for coding agents.
- [`design/household-resources-handoff.md`](design/household-resources-handoff.md): implementation contract and swarm ownership for household defaults, member overrides, fair jobs, and bandwidth-aware playback. Read before implementing that feature.
- [`research/soulsync-competitive-analysis-2026-10-16.md`](research/soulsync-competitive-analysis-2026-10-16.md): source-backed playback comparison, acquisition boundaries, and recommendations for broader arr integration.
- [`design/arr-integration-playback-plan.md`](design/arr-integration-playback-plan.md): phased implementation scope for broader arr integration, playback continuity, and optional later native acquisition.
- [`design/arr-integration-playback-handoff.md`](design/arr-integration-playback-handoff.md): read before coordinating parallel implementation of the arr/playback plan; defines packet ownership, dependencies, integration gates and delivery evidence.
- [`design/arr-integration-playback-implementation.md`](design/arr-integration-playback-implementation.md): current implementation status, frozen contracts, validation and provider/device qualification gaps.

- [`../CONTEXT.md`](../CONTEXT.md): media/acquisition identity and listening terminology.
- [`adr/0027-record-external-acquisition-before-dispatch.md`](adr/0027-record-external-acquisition-before-dispatch.md): durable external operation ownership and uncertain outcomes.
- [`adr/0028-member-owned-listening-export-ledger.md`](adr/0028-member-owned-listening-export-ledger.md): opt-in export, play identity and provider replay policy.
- [`adr/0029-check-published-releases-without-installing-updates.md`](adr/0029-check-published-releases-without-installing-updates.md): stable release checks, cached admin notices and optional best-effort email without automatic installation.
- [Photo pipeline optimization](performance/photo-pipeline-optimization-2026-10-24.md): full HEIC import/preview/face-processing comparison and reproduction commands.
- [Photo library validation](performance/photo-library-2026-10-24.md): synthetic 100,000-photo catalog timings and photo workflow coverage.
- [`performance/comprehensive-audit-2026-10-17.md`](performance/comprehensive-audit-2026-10-17.md): current backend/all-media benchmark coverage, methodology, results and qualification gaps.
- [`performance/audit-remediation-2026-10-17.md`](performance/audit-remediation-2026-10-17.md): search, playback, UI and long-audio fixes with independent follow-up measurements.
- [`design/media-discovery-reliability-2026-10-17.md`](design/media-discovery-reliability-2026-10-17.md): missing-season diagnosis, guarded production recovery, automatic discovery settings and registered-title reconciliation regression.
- [`design/metadata-enrichment-reliability-2026-10-17.md`](design/metadata-enrichment-reliability-2026-10-17.md): delayed episode names/artwork diagnosis, production metadata recovery and incremental-import queue/race fixes.

- [Personal YouTube sessions are playback-only](adr/0029-personal-youtube-sessions-are-playback-only.md)
