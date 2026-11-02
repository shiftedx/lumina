# Server navigation and language-specific requests

Implementation branch: `codex/server-driven-media-requests`. This integrates the approved web work and native Home/navigation changes onto the current application base without changing the prototype branches. No deployment or native release is part of this revision.

## Packet ownership and status

| Packet | Owned files / boundary | Status |
| --- | --- | --- |
| Primary integration | App wiring, shared hook lifecycle, settings preference integration, final review and delivery | Integrated and verified |
| Backend contracts | Navigation endpoints/service, request engine/catalog/sync, additive schema and rollback contracts | Integrated; focused regression checks pass |
| Web surfaces | Drawer/tabs, Home, requests, palette/providers, focused browser fixtures | Integrated; desktop/phone workflows pass |
| Native surfaces | Core response/DTO consumption, iOS/tvOS navigation and request views, isolated native proof fixtures | Builds and focused Core checks pass; UI evidence limitations below |

## Capability matrix

| Capability | Server contract | Web | iOS / tvOS |
| --- | --- | --- | --- |
| Movies, Shows, Anime | Enabled sources, usable catalog items and member visibility | Drawer, mobile, library tabs, Home, search and direct browse routes | Supported library categories filtered by shared response |
| Music | Usable member-visible audio | Dynamic destination, Home/search; browsing preference retained | Dynamic library category |
| Books | Available member-visible books | Dynamic destination, Home/search; active reader preserved | Dynamic supported library category |
| Saved YouTube and recordings | Independently populated saved catalog; no dependency on online Streaming switch | Separate library lenses and Home content | No unsupported destination introduced |
| Photos | Available visible managed photos or enabled external source/root | Dynamic destination/search | No unsupported destination introduced |
| Streaming | Explicit server opt-in intersected with member provider grants | Destination, live/remote Home shelves, provider context and search gated | Existing supported external destination/feed gated |
| Requests | Enabled server feature and existing member policy | Destination and direct route gated | Destination gated |
| Member/server changes | Authenticated no-store response; member isolation | Fail closed during initial load/member switch, refresh on navigation/foreground and visible 30-second interval | Fail closed, refresh on foreground/settings/account changes |
| Existing tracks | Stored current artifact probe plus recorded sidecars, per file | Detail shows audio/subtitles and unknown/partial/truncated evidence | Same-file evidence on request details |
| Alternate language | Exact normalized audio/subtitle tuple; regional tags retained | View existing title and request another language | Same alternate-language workflow |
| Approval | Manual review unless an explicit compatible anime profile exists | Owner amendments retain omitted fields; explicit null clears | Typed DTOs preserve legacy and exact fields |
| Availability | Matching actual file tracks and proven episode coverage | Reflects truthful request status | Same server-owned status |
| Playback, offline downloads, settings | Existing permissions and real providers | Existing player/reader ownership retained; manual URL flow remains separate | Offline entry and playback remain available |

No transient storage-root online status is used to flicker known catalog destinations. Disabled roots and missing artifacts do remove their contribution. A null subtitle preference means no subtitle requirement, not a requirement that subtitles be absent. Evidence is bounded to 500 file rows and does not run probes or read media during detail requests.

## Validation

Focused checks only, following the owner's dogfood testing instructions; no full gate was run.

- Backend final navigation/request contracts: 102 passed. Final language/catalog/schema and documented rollback/re-upgrade checks after metadata hardening: 53 passed. These runs overlap and must not be summed.
- Final integrated schema/language/navigation rerun: 34 passed (11 existing SQLAlchemy/dependency warnings).
- Web navigation, shell, library tabs, request UI, Home visibility, palette and provider context: 69 passed across 7 files.
- Retained Home, shell, accessibility and recommendation suppression contracts: 52 passed across 4 files.
- Desktop and phone Playwright workflows: 2 passed. Each hides unavailable navigation, shows Japanese audio with English subtitles, and submits a separate English-audio request.
- Frontend typecheck passed. Production build passed including licenses and CSS budgets (81.9 KB gzip / 82 KB budget); existing large-chunk warnings remain.
- Native focused Core navigation: 1 passed; Requests DTO/contracts: 5 passed.
- Generic iOS and tvOS simulator builds and signed build-for-testing targets passed. TV navigation workflow passed (1/1, 53.696 seconds): Requests off/on, populated Music, and removal of the selected Music library returning Home.

Browser captures are in `output/server-navigation-language/`: `navigation-desktop.png`, `navigation-phone.png`, `language-desktop.png`, `language-phone.png`. Native logs, xcresults and captures are in `output/server-media-native/`.

## Operational and evidence limits

Schema 43 adds nullable audio/subtitle preference columns. Upgrade revalidates old language-specific available/partial requests instead of trusting historic manager state; existing manager bindings are retained without redispatch. The operator guide records additive rollback/re-upgrade handling. Older rollback images do not understand exact language requests.

Unprobed or malformed metadata stays unknown. Automatic searches are conditional on the configured anime profile; a manager may replace an existing version and cannot promise parallel language editions. Other exact combinations use owner/manual fulfillment. Manual TV fulfillment remains approved until authoritative season/episode coverage can be observed; track evidence from one episode does not establish the whole series.

Native UI evidence limits: iOS XCTest did not start because CoreSimulator failed with Mach error -308. The TV language workflow reached every body assertion, displayed Japanese audio with English subtitles, and verified a newly persisted English-audio request, then stalled during app-termination teardown; it is not a formal XCTest pass. `tv-navigation-retry.xcresult` is the passing navigation result, `tv-retry.xcresult` is incomplete language evidence, and `tv-live.png` shows the same-file language row. The external-source fixture was unpopulated, so its absence does not independently prove hiding of an already-populated native external rail. Owned disposable simulators and broker processes were cleaned up. No physical-device or release claim is made.
