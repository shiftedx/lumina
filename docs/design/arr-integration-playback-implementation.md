# Arr integration and playback delivery record

Execution started 2026-10-17 from [handoff](arr-integration-playback-handoff.md) and [plan](arr-integration-playback-plan.md). This is an active delivery record, not a completion claim.

## Baseline

- Revision: `a4e01dad62fadc6186ca27ea0fbda8e3ea0d3674`; branch `codex/household-resources`.
- Pre-existing changes preserved: `docs/README.md`, untracked plan/handoff and `docs/research/`.
- Read-only GitHub context: no open issues and no PR for this branch at kickoff. No publication or merge performed.
- Initial `mise exec -- make check` overlapped edits and ended with three mixed-source failures; see validation below. A fresh integrated full gate remains required.
- Runtime model availability checked against tool metadata: configured Terra is not advertised; workers use supported inherited default. No unsupported Luna/Terra spawn attempted.

## Contracts and ownership

Phase 1 contract v1 is frozen (ADR 0025). Existing request dispatch remains separate from inventory observation. Upstream-only inventory is admin-only; member explanations require current visible registered media. No external data or stored client URL establishes playable availability.

| Packet | Owner | Exclusive files | Status |
| --- | --- | --- | --- |
| A backend/review/measurement | arr_observer_design | Observer, operations, Lidarr backend; assigned local-network harness | Backend slices complete; independent queue review and conversion measurement |
| B integrations UI/review | integrations_ui | `frontend/src/features/integrations/**`, `MusicExtras.tsx`, assigned integration browser spec/mock | Phase 1–3 UI complete; focused review of listening export |
| C music | connector_discovery | Music session service/resolver, `musicSession.ts`, `musicSharedSession.ts`, `MusicPlayer.tsx`, `musicEngine.ts`, focused tests | Shared session, output transfer and controls implemented; mixed queue review fixes active |
| Integration | PM | Shared models, migrations, main/route registries, shared types, ADRs, operator docs, this record | Active |

### Phase 1 contract v1

- Schema 23: `ArrObservationState` keyed by server; `ArrObservedItem` keyed by server/upstream identity; `ArrObservedRecord` keyed by server/dataset/record identity. Only curated fields are stored. No fake request rows.
- `GET /api/admin/arr/observations`: paginated inventory and per-server version/capability/sync/count summaries; `server_id`, `page`, `page_size` filters.
- `GET /api/admin/arr/servers/{id}/{dataset}`: paginated queue/history/health/calendar/missing/cutoff records. `POST .../{id}/refresh`: single-flight explicit read refresh, no command dispatch.
- `GET /api/arr/titles/{id}`: current visibility check; only `{title_id, integrations:[{state,stale}]}`. Hidden titles return 404. Inventory never creates Play.
- Complete successful snapshot replacement is atomic; timeout/malformed paging retains previous state and marks stale. Changed/deleted/disabled connection invalidates in-flight results. Polling starts at process startup independently of Requests.
- Import states: unmapped, root_unavailable, awaiting_first_import, pending_import, registered. Existing scoped import/throttle is reused; the first root import remains an explicit owner action.
- New operational write commands are outside this observation contract and require Phase 2's ledger/reconciliation freeze.

## Slice status

| Slice | Status / evidence |
| --- | --- |
| 1 Whole library visibility | Implemented; focused backend/UI/browser checks and independent review passed |
| 2 Acquisition controls | Implemented; focused backend/UI/browser checks passed; independent review collision finding fixed and regression added |
| 3A Lidarr | Backend, release-group import correlation, periodic observations and member/admin UI implemented; 41 focused UI tests and five integration browser workflows passed; installed-service qualification pending |
| 3B Bazarr | Conditional: official contract discovered, no household instance/identity mapping available; connector/actions absent pending qualification |
| 3C Prowlarr | Read-only backend/UI implemented and fixture/browser-tested; real installation qualification pending |
| 3D Downloader | qBittorrent/NZBGet read-only backend/UI implemented and fixture/browser-tested; real versions/auth/correlation qualification pending |
| 4A Shared music session | Backend/frontend implemented; focused checks passed; independent review refinements integrated with 4C |
| 4B Sonos continuity | Acknowledged transfer/recovery implemented; independent review refinements and API/component regressions integrated; real-device qualification pending |
| 4C Acquisition-aware queue | Durable resolver, mixed queue, explicit skip/wait and paused import refresh implemented; pending→import→explicit Play browser journey passed; large-queue ordering/descriptor bounds, cursor and owner-race review accepted |
| 4D Language defaults | Implemented for browser/Jellyfin; ordered languages/forced/SDH and explicit overrides tested |
| 4E Crossfade and sleep timer | Implemented and focused-tested, including cancellation of scheduled fades when changing mode |
| 4F Listening export | Last.fm/ListenBrainz member authorization, browser tracker and replay-safe ledger implemented; focused API/unit/browser checks passed; real-account qualification pending |
| 5 Additional output | Chromecast direct-video Web Sender/ticket preview implemented against Google docs; focused backend/SDK/component/browser checks passed; user has no receiver, so hardware qualification unavailable |
| 5 Local conversion | Browser automatic starts use a constrained, non-saturated downlink hint with manual/saved quality and server ceilings preserved. The final nine-trial follow-up passed under recorded contention: shaped automatic 480p median first frame 1.763 s with no stalls. Continuous local ABR and hardware qualification remain conditional. |
| 6 Native acquisition | [Decision/prerequisites recorded](native-acquisition-decision.md); no pilot selected; ADR 0018 retained |

## Validation and qualification

Focused implementation checks and bounded independent reviews below pass. The final integrated gate and isolated measurement repeat remain pending. Real-service/device interoperability is qualified separately and is not implied by fixtures.

Initial focused baseline: the previously failing scheduling waiter check passed (`1 passed in 0.61s`). Typecheck/build passed before source edits. The long full run overlaps implementation and is not a pristine complete baseline; an integrated full gate is still required.

Household selections (2026-10-17): Chromecast is the requested additional output; qBittorrent and NZBGet are the downloader targets. Existing Infuse/Jellyfin-compatible Apple TV support remains the established path. User authorized reading the local homelab repository for service information. Only sanitized product/version/capability evidence may enter Lumina artifacts.

Focused validation so far:

- Schema creation, upgrade, schema snapshot and documented rollback contracts: `18 passed, 6 warnings in 2.55s` (`/tmp/lumina-arr-schema-check.log`). Initial snapshot generation omitted separately registered Books tables; corrected before this passing run.
- Existing request API/engine/sync with connection snapshot lifecycle regression: `57 passed, 1 warning in 14.31s` (`/tmp/lumina-arr-requests-check.log`). Initial added test omitted its seed commit; corrected before this passing run.
- [Connector contract discovery](../research/arr-connector-contracts-2026-10-17.md) complete using official source contracts; installed-service behavior remains separately unqualified.

The initial overlapping full run ended with `3 failed, 5007 passed, 11 skipped, 49 warnings in 515.86s` (exit 2; backend stopped the gate). Failures were the music rollback/schema snapshot contracts and a source-inspection lifecycle contract, all reading files changed after their modules were loaded. A current-process focused rerun of all three failed areas passed: `20 passed, 6 warnings in 3.07s` (`/tmp/lumina-arr-baseline-recheck.log`). This does not substitute for the required final full gate.

Observer packet focused check: `test_arr_observations.py` plus `test_requests_sync.py`: `12 passed`; typecheck passed. UI packet focused check: integrations/navigation/request-settings Vitest `26 passed`; typecheck passed; focused mocked browser journey passed. PM inspected desktop and 320px screenshots; warning text contrast and mock contract mismatches were sent back for correction before acceptance.

### Additional frozen contracts and evidence

- Schema 24 adds the durable admin-only `arr_operations` ledger. Commands use a client UUID and fresh resource revision, preserve upstream fields, persist dispatch receipts before rereads and never redispatch uncertain work. Reconciliation is read-only and bound to a hash of the original server identity.
- Schema 25 adds member-owned `music_listening_sessions`; GET/PUT `/api/me/music-session` uses revision CAS, rebuilds current manifests by visible item ID, prunes removed items, and preserves intentional rewinds. ADR 0026 records the policy.
- Schema 26 adds `service_connectors` and separate `music_acquisitions`. Connector secrets are write-only and nulled in backups. Saves require re-probing; endpoint changes cannot reuse old credentials. Infrastructure snapshots are admin-only, read-only and complete-or-stale. Download correlation needs an explicit manager/client-name binding plus exact upstream queue/native identity; unmatched/ambiguous jobs remain labeled.
- Phase 1 integrated backend checks: `82 passed, 7 warnings in 19.79s`. UI focused checks included 28 integration/settings tests plus 30 gallery tests, typecheck, desktop/320px light/dark browser checks and contrast correction.
- Phase 2 backend checks: `85 passed` across controls/observation/request/schema contracts before independent review. UI integration/settings checks: 32 passed; browser checks: 3 passed. Independent review found ambiguous release-ID concatenation; fixed with structured tuple hashing and a selection regression.
- Phase 4A backend: 5 API tests passed, including two-client conflicts, visibility pruning, duplicate queue entries and normalization.
- Phase 4D compatibility: `272 passed, 1 warning in 28.16s` across Jellyfin API/client/integration and pure playback decisions; focused browser resolver/preferences tests passed, frontend typecheck passed. Settings Playwright passed with alias normalization, autosave, desktop/320px, both themes and axe checks. Screenshot visual inspection remains to be repeated after the next browser batch replaced artifacts.
- Infrastructure/backup workflow checks: `5 passed, 1 warning in 3.51s`; schema checks: 18 passed. Fixtures cover qBittorrent cookie auth/full sync, NZBGet read-only RPC, Prowlarr >1,000 history records, sanitization, admin denial, changed-endpoint credentials and byte-level backup credential removal. Real services have not been contacted.

### Wave 3/4 integration

- Phase 3A import identity was corrected during PM integration: Lidarr album IDs are MusicBrainz release groups, while local `MusicBrainz` provider IDs may identify editions. Matching now requires `release_group_id`/explicit release-group identity plus visible linked files in enabled storage roots. An enabled connector poll reads full bounded artist/album/queue/history data and reuses the existing mapped scoped scanner gate. Fixture workflow confirms downloading observations cannot establish Play.
- Phase 3 infrastructure UI: 33 focused Vitest tests, e2e typecheck, four mocked browser workflows, production build; CSS budget 66.3/68 KiB at that snapshot. PM inspected retained 320px light/dark captures. Performance measurements and final integrated build remain separate.
- Phase 4F provider contracts: [Last.fm authentication](https://www.last.fm/api/authspec), [scrobbling eligibility](https://www.last.fm/api/scrobbling), [ListenBrainz core API](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html). Provider credentials stay server-only; a failed replacement retains a working account, authorization generation protects concurrent disconnects, and Last.fm stages pending authorization separately. Schema27 adds connections/events and backup redaction. Source metadata/manual edits qualify; filename guesses do not. Uncertain results only reconcile against exact history/time.
- Phase 5 follows Google's current [Web Sender integration](https://developers.google.com/cast/docs/web_sender/integrate) and [supported media](https://developers.google.com/cast/docs/media). Source inspection of the current public sender bootstrap identified its specific gstatic/extension script dependencies for the CSP; no broad script or media-origin permission was added. Schema28 adds item-scoped receiver tickets, 15-minute expiry and authenticated 60-second sender renewal. Default Media Receiver; narrow direct MP4 H.264 SDR/stereo AAC profile, selected text subtitles, no alternate audio or conversion. Existing pure decisions/resource admission are reused.
- User clarified there is **no household Chromecast device** and requested current Google developer docs as the reference. This is a hardware-qualification gap, not an installed-device compatibility claim. Apple TV remains served by the existing Infuse/Jellyfin path.
- Parent focused validation after independent review: listening export + app-password boundary `7 passed`; SDK/component/export/settings frontend `10 passed`. Chromecast backend plus existing music API `20 passed`; parent source-return and late-owner cleanup regressions added. Mocked browser batch `3 passed in 11.9s`: Cast load/pause/seek/resume/return/failure, export connect/reconcile/disconnect, language aliases/defaults; all include responsive/theme/accessibility checks. No physical receiver or external listening account contacted.
- Schema29 adds logical queue references/cursor/skip-wait policy alongside resolved IDs. ADR0026 is amended; ADR0027 records durable external acquisition and ADR0028 records export replay/ownership policy. All rollback guidance is additive and retains uncertain operation/export history.
- Listening export now binds offline events to the provider account generations verified when listening starts. Replacement accounts cannot inherit older cached plays. Independent review found an overlapping refresh race; a separate request sequence now rejects superseded responses, with a deferred-response regression. Five focused tracker tests passed.
- Pending acquisition browser workflow and existing music journeys passed together (`9 passed`): no playable placeholder, pending-only queue remains reachable, Wait is visible, import refresh stays paused, and only explicit Play fetches audio. The music browser mock now persists shared GET/PUT sessions with CAS rather than returning an empty queue after every write.
- Latest focused closure batch: frontend typecheck passed, music Vitest `66 passed`, requests/session API `17 passed`. Parent export/schema/security contracts `34 passed, 7 warnings in 17.34s`; SDK/receiver/export browser checks `2 passed in 32.7s`; language-default browser passed separately. Final integrated gate remains required.
- Final integration review found an intra-album queue cursor reset and an album-resolution materialization bound; fixes and workflow regressions are being verified before the full gate.
- Final bounded queue/session acceptance passed after corrections: logical-order resolution preserves the first 5,000 playable tracks, response descriptors share that bound, later logical references remain durable, resolved acquisitions expose their track mappings, snapshot adoption disposes the old controller, clearing serializes durable writes, and owner changes invalidate pending remote restores. Additional API/engine mapping regressions passed (`20` session frontend tests and `8` session API tests); the preceding complete focused music/request batches passed `67`/`18` tests.
- Latest frozen-source gate: backend `5054 passed, 11 skipped, 49 warnings` in 954.96 seconds. Frontend initially had one 5-second channel-navigation timeout (`2518 passed`); that test passed alone in 574 ms, and a complete rerun with four workers passed all `2519` tests. The subsequent full mocked browser run finished `585 passed, 16 failed, 2 skipped`; thirteen failures share the 18 px Chromecast disclosure target, with phone watch-tab overflow, intermittent overlay CLS and one stale broad host-launch assertion accounting for the rest. The broad real-stack pass finished `39 passed, 11 failed, 4 skipped`; corrected video selectors subsequently passed all ten playback cases, and a targeted notes-readiness repeat passed. Extended gallery checks passed separately. These findings remain open; this is not a clean complete `make check` claim.
- [Local-conversion measurements](../research/local-conversion-network-2026-10-17.md): setup plus three scenarios passed (`4 passed`, nine cold trials, 5.3 minutes). At 2.5 Mbps/50 ms, median first frame/stalled time changed from 16.414 s/12.466 s at 1080p remux to 3.178 s/0.263 s at 480p software conversion; media-clock advance changed from 7.541 s to 19.188 s in the 20-second window. These observations are provisional because unrelated tests ran concurrently; they suggest a quiet-window repeat of the existing manual ceiling. No automatic ladder or additional concurrent encoders were introduced.

## Connector capability and qualification boundary

All configuration below is owner-only with write-only credentials. Protocol contracts and fixtures establish the implemented surface; they do not certify an installed product version. No live service or provider credentials were used during this implementation.

| Integration | Implemented surface | Writes and exclusions | Qualification |
| --- | --- | --- | --- |
| Sonarr/Radarr | Complete bounded inventory/queue/history/health/calendar/missing/cutoff observations and current item state | Explicit admin item/release commands through the owning manager, durable receipts and read-only uncertainty reconciliation; no arbitrary API forwarding | Existing integration plus new fake-server/API/UI checks; household versions unverified |
| Lidarr | Artists/albums/queue/history, member release-group requests, visible-file import correlation | Admin approval performs add/monitor/search once; global release-group admission prevents competing acquisitions, even across connectors; no native downloader ownership | Official v1 API contract and fake-server/member/admin workflows; installed version/auth/import mapping unverified |
| Prowlarr | Curated health/indexer/status/statistics/history and upstream link | Read-only; indexer configuration and searches/grabs remain upstream | Official v1 API contract, bounded fixtures and UI checks; installed version/auth unverified |
| qBittorrent | Cookie login, app/WebAPI versions, complete sync transfer state, exact configured arr correlation | Read-only; no file listing, pause/delete, seeding or retention controls | Official WebAPI v2 contract and fixtures; installed version/auth/identity join unverified |
| NZBGet | Basic-auth JSON-RPC version/status/listgroups/history and exact configured arr correlation | Read-only; no file listing, queue editing or retention controls | Official JSON-RPC contract and fixtures; installed version/auth/identity join unverified |
| Bazarr | Discovery only | Conditional connector/actions absent until an actual instance and media identity mapping are qualified | No household instance established |
| Last.fm/ListenBrainz | Member authorization, eligible browser listens, durable per-provider delivery/reconciliation | Opt-in; unknown outcomes never blindly resend; no remote-output listen accounting | Official API contracts, fixture/component/browser checks; real accounts unqualified |
| Chromecast | Web Sender, Default Media Receiver, direct MP4 H.264 SDR ≤1080p/stereo AAC and selected text subtitles | Preview; no alternate audio/conversion; item-scoped expiring/renewable receiver access | Current Google docs and mocked SDK/browser/API controls; user has no receiver, so hardware qualification deferred |

The authorized local homelab repository is the service inventory reference for future household setup work. Its private hostnames, media and credentials are excluded from these artifacts. Installed version/auth/path/correlation checks remain setup qualification tasks rather than compatibility claims.


## Comprehensive performance audit

The [2026-10-17 audit](../performance/comprehensive-audit-2026-10-17.md) records backend household stress, integration/workflow costs, range/rendition components, browser playback/readers and explicit qualification gaps. Production source was frozen during measurement; benchmark fixes do not change product budgets. All native runtime results were collected with unrelated workload activity and labeled accordingly. The nine-trial constrained-network repeat observed median first frame 16.589 s at 1080p remux versus 1.974 s at 480p conversion, with stalled time 13.106 s versus zero. Docker/Linux execution was retried for all three families but stopped at Docker Desktop HTTP 503 before container creation. No household services, physical receiver or external listening account was contacted.


The available native audit and final static checks completed. The extended video matrix retained 27 trial rows (10 cases passed, one strict MPEG-4 target-frame qualification failed); a narrow diagnostic confirmed a 60-second session restart and continuing decoded playback, while exact landing latency remains unqualified. Known oversized long-form audio automatically streamed with zero Web Audio decode/buffer starts, reached native `playing` in 224 ms (N=1) and emitted a real 60-second seek event. Search profiling identified transcript candidate SQL; a read-only paired variant preserved ordered results for six synthetic users while reducing representative viewer SQL medians from 69 to 22 ms. Production source stayed frozen; the report retains workload contention, failed attempts, open UI budget/geometry findings and Linux infrastructure limits.

The authorized [remediation follow-up](../performance/audit-remediation-2026-10-17.md) applies candidate-driven search SQL, shared Next Up SQL aliases, advisory initial video bandwidth, stable watch-tool placement/target geometry and safe streaming for audio with unknown probe facts. Six-member search p95 fell from 1,136 to 347 ms in contended HTTP repeats; the constrained automatic-video case reached the useful 480p rendition without the baseline stalls. Watch Notes drafts survive late metadata. Unprobed long audio streamed and sought without Web Audio decoding; known-short music and all four reader formats passed their follow-ups. Existing title-budget misses under very high host load and Docker HTTP 503 remain explicit limits, not waived checks.
