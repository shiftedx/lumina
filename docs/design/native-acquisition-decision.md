# Native movie and TV acquisition decision

- Date: 2026-10-17
- Decision: retain external arr ownership under ADR 0018. No native pilot selected.
- Scope: Phase 6 decision and prerequisites from the [integration plan](arr-integration-playback-plan.md).

The current implementation request prioritizes broader external integrations and playback. No remaining household need has been established that requires a native release manager, and the user has not selected a pilot. This record does not supersede ADR 0018 or authorize native release dispatch.

Before implementation, select one media kind and a separate managed root/edition; document the unmet need and accept an ADR replacing the affected ownership boundary. Select and verify Prowlarr and one downloader contract. Define exact release/episode identity, deduplication across search/RSS, durable dispatch and uncertain-outcome reconciliation, failure/blocklist retry, mapped paths, archive/sample/file validation, atomic upgrades retaining the previous valid copy, hardlink/seeding rules, and backup/migration/rollback.

The cutover must pause the old owner and reconcile transfers/files/history before enabling the new owner. Acceptance must prove one acquisition/import/rename/upgrade authority, no duplicate work after crash/restart/timeout, no wrong or sample files becoming playable, and rollback to one owner with usable media. Unattended retention remains gated by ADR 0005's provenance/lifecycle prerequisites.
