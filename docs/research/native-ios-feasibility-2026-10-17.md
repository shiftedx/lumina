# Native iOS viewing client: feasibility and sizing

**Date:** 2026-10-17  
**Status:** research only; no runtime validation was performed.

## Decision framing

A fully native Swift/SwiftUI Lumina viewing client is feasible without a web wrapper. The current launch definition is deliberately much smaller than the web app: **browse, search, profile selection, AI discovery, video playback, and shared progress** on iPhone, iPad, and iPhone Duo. Owner administration remains on the web. Books, a dedicated music-library client, Sonos, device downloads, acquisition management, and server administration are excluded.

The thin native path is a **5–10 person-week** technical spike. A production viewing beta is **21–32 person-weeks** including the small shared-server allowance. The requested “Infuse-equivalent playback capability” changes the total to **33–54 person-weeks**, chiefly because the server must make and verify format-specific playback decisions; `AVPlayer` alone does not establish that claim.

All estimates assume one experienced, full-time Apple developer, five working days per week, an otherwise stable Lumina API, and timely access to representative non-personal test media. They are effort estimates, not elapsed calendar time or a shipping commitment. The minimum OS is an **iOS 18 assumption for planning**, not a product mandate. iPhone Duo-specific validation naturally occurs on its iOS 27 environment; the layouts must adapt when the device opens, closes, resizes, and uses split view.

## What current code already supplies

The backend already has a useful first-party seam for a native client:

* authenticated library, search, title, progress, artwork, home/recommendation, and profile APIs live in [`backend/app/main.py`](../../backend/app/main.py), [`backend/app/routers/discovery.py`](../../backend/app/routers/discovery.py), and [`backend/app/routers/titles.py`](../../backend/app/routers/titles.py);
* local video has playback-options, session creation, HLS segment delivery, subtitle tracks, and progress endpoints in [`backend/app/routers/local_playback.py`](../../backend/app/routers/local_playback.py);
* the existing web client’s concrete request and response shapes are in [`frontend/src/api.ts`](../../frontend/src/api.ts), [`frontend/src/types.ts`](../../frontend/src/types.ts), and [`frontend/src/localPlayer.tsx`](../../frontend/src/localPlayer.tsx);
* route evidence for the viewing surfaces is [`frontend/src/app/routes.ts`](../../frontend/src/app/routes.ts), while the current application composition is [`frontend/src/LuminaApp.tsx`](../../frontend/src/LuminaApp.tsx).

This is not a drop-in mobile SDK. The current local endpoint parses a browser-profile query parameter (`caps_from_browser_profiles`) before calling the decision engine. The native app needs a typed, versioned capability profile rather than pretending to be a browser. The server already makes one decisive constraint visible: converted local sessions are H.264/AAC, while direct/remux paths preserve more of the source. See [`backend/app/services/local_playback_sessions.py`](../../backend/app/services/local_playback_sessions.py) and [`backend/app/routers/local_playback.py`](../../backend/app/routers/local_playback.py). Therefore server conversion cannot preserve HDR, Dolby Vision, Atmos, or lossless audio unless the playback engine is extended and qualified to do so.

The visual identity also translates well to native components. [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css) defines the dark/light paper-and-ink palette, gold hairlines, restrained corner radius, editorial Newsreader/serif hierarchy, and reduced-motion/high-contrast behavior. [`frontend/src/ui/BrandMark.tsx`](../../frontend/src/ui/BrandMark.tsx) defines the half-sun mark, and [`frontend/src/ui/Masthead.tsx`](../../frontend/src/ui/Masthead.tsx) the kicker/title/rule pattern. Existing phone references are [`docs/design/evidence/p2-library-phone-after.png`](../design/evidence/p2-library-phone-after.png), [`docs/design/evidence/p2-title-phone-after.png`](../design/evidence/p2-title-phone-after.png), and [`docs/design/evidence/watch-populated-390-after.png`](../design/evidence/watch-populated-390-after.png).

## Native UI mapping

| Web character and organization | Native SwiftUI expression | Scope implication |
| --- | --- | --- |
| Paper/ink, serif editorial mastheads, gold rules, half-sun wordmark | A small token layer (`Color`, `Font`, spacing, rules) plus custom `Masthead`, `BrandMark`, card, shelf, and artwork views | Preserve the hierarchy and art-led feel; do not reproduce CSS geometry literally. |
| Home rails and cinematic hero | `ScrollView` with horizontal shelves; immersive artwork header and compact card actions | Shared iPhone/iPad data source, with different density and art treatment. |
| Home, Video, Search, Profile | iPhone tab bar; iPad `NavigationSplitView`; a Duo adaptive composition that keeps the same navigation state as width changes | Avoid a separate “foldable app.” Test state preservation through resize/open/close and split view. |
| Title detail, seasons, resume, play | Native scroll detail, season picker, contextual menu, then an `AVPlayerViewController`-backed player | The system player supplies familiar transport behavior; Lumina owns the editorial detail and surrounding information. |
| Watch view overlays | Native controls for chapters, subtitles/audio selection, resume, up-next, and errors | Start with the viewing actions. Transcript/chat/notes/tools are not launch requirements. |
| Profile and AI discovery | Member picker/profile switcher and recommendation shelves/feedback | Keep the household language and source provenance; no owner configuration screens. |

## Scope tiers and bottom-up arithmetic

### 1. Thin native technical spike — 5–10 total person-weeks

This is a decision-quality implementation, not a TestFlight beta. It proves that a completely native client can connect to a representative Lumina server, sign in, browse a small library, play one local item, and save progress. It deliberately omits premium-format commitments, Duo polish, and broad failure-state coverage.

| iOS work item | Complexity | Effort |
| --- | ---:| ---:|
| Minimal SwiftUI target, manually entered server address, keychain/session handling | M | 1–1.5 weeks (5–7.5 days) |
| Native HTTP transport, cookie/CSRF behavior, small typed API surface | M | 1–1.5 weeks (5–7.5 days) |
| Home/library/search/title browse path | M | 1–1.5 weeks (5–7.5 days) |
| `AVPlayer` direct/HLS start, pause/seek, and periodic progress write | L | 1.5–2.5 weeks (7.5–12.5 days) |
| Lumina visual shell, error states, real-device walkthrough | M | 0.5–1.5 weeks (2.5–7.5 days) |
| **iOS subtotal** |  | **5–8.5 weeks** |
| Shared server clarification only if discovered necessary | S | **0–1 week** |
| **Total: 5–8.5 + 0–1** |  | **5–10 weeks** |

### 2. Production viewing beta, standard playback — 21–32 total person-weeks

This is the recommended baseline launch: household profiles, browse/search/title details, AI recommendation shelves, shared continuation/progress, local video playback, subtitles and alternate audio, and robust layouts for iPhone, iPad, and Duo. “Standard” means the supported direct/HLS paths are qualified for an agreed H.264/AAC test set. It does **not** promise parity with Infuse across source formats.

| iOS work item | Complexity | Effort |
| --- | ---:| ---:|
| Secure server setup, login/profile switching, transport/cache/error architecture | L | 2.5–3.5 weeks (12.5–17.5 days) |
| Lumina token system, components, accessibility, dark/light detail | L | 3–4 weeks (15–20 days) |
| Home, video library, search, title/season detail, artwork and empty/loading states | L | 4–5 weeks (20–25 days) |
| Native player controls, HLS/direct routing, subtitle/audio selection, quality/error UX, progress/resume/up-next | XL | 4–5 weeks (20–25 days) |
| Profiles and AI discovery/recommendation feedback | M | 1.5–2.5 weeks (7.5–12.5 days) |
| iPhone/iPad/Duo adaptation, resize/split-view state preservation | L | 2–4 weeks (10–20 days) |
| Device matrix, VoiceOver/Dynamic Type/reduced motion, TestFlight packaging and review materials | L | 2–4 weeks (10–20 days) |
| **iOS subtotal** |  | **19–28 weeks** |
| Shared capability identity and API-contract tests | M | **2–4 weeks** |
| **Total: 19–28 + 2–4** |  | **21–32 weeks** |

### 3. Production viewing beta with the premium playback benchmark — 33–54 total person-weeks

The requested benchmark is: allow server conversion where needed and be comparable to Infuse in playback capability. That requires a concrete device-and-format acceptance matrix before it can be estimated more narrowly. It is primarily playback-engine work, not more SwiftUI screens.

| Delta from tier 2 | Ownership | Complexity | Effort |
| --- | --- | ---:| ---:|
| Capability-driven player choices, clear format outcomes, playback telemetry | iOS | L | +2–4 weeks |
| Typed native client capability profile and decision contract | Shared server/playback | L | +2–3 weeks |
| Direct/remux/transcode policy and HLS packaging changes | Shared server/playback | XL | +3–5 weeks |
| HDR/Dolby Vision, multichannel/Atmos, lossless policy and server-output qualification | Shared server/playback | XL | +3–6 weeks |
| Real-media/device regression matrix and fallback/error contracts | Shared server/playback | L | +2–4 weeks |
| **Premium delta: iOS 2–4 + shared 10–18** |  |  | **+12–22 weeks** |
| **Total: (19–28 + 2–4) iOS + (2–4 + 10–18) shared** |  |  | **33–54 weeks** |

The arithmetic keeps shared playback work explicit: tier 3 is **21–32** plus **12–22**, hence **33–54**. It does not treat a future iOS/tvOS shared core as free. A future tvOS client might reuse selected networking, models, and playback contracts, but it still needs its own focus-driven UI, platform behavior, device qualification, and release testing.

The 10–18 shared premium-engine estimate has **low confidence** until the format/device spike closes. It is intentionally separated so server/playback work is counted once alongside related backend planning, rather than once per Apple client. Duo is included in the iPad/adaptive work above; it is not estimated as a third app.

## Premium playback: what is known and what must be proved

Apple documents the native primitives that make the standard path attractive: `AVPlayerViewController` supports system playback UI, Picture in Picture, and AirPlay; AVFoundation exposes alternate audio/subtitle selection; background audio/PiP requires the Audio, AirPlay, and Picture in Picture background mode plus an appropriate audio session. See Apple’s [media playback configuration](https://developer.apple.com/documentation/avfoundation/configuring-your-app-for-media-playback), [AVPlayerViewController](https://developer.apple.com/documentation/avkit/avplayerviewcontroller), and [media-selection guide](https://developer.apple.com/documentation/avfoundation/selecting-subtitles-and-alternative-audio-tracks).

Those APIs do not establish support for every container, HDR/Dolby Vision profile, immersive audio format, lossless route, or conversion path. Lumina’s current browser-oriented capability string and its H.264/AAC conversion output are direct evidence that an equivalence claim needs server work. The premium promise should be stated only after each combination has a tested result: **direct play preserved**, **remux preserved**, **conversion with specified loss**, or **unsupported with a useful alternative**. “HDR/Atmos versus lossless” must be separate rows: they are different source, output, route, and conversion constraints.

## Risks, decisions, and bounded spikes

| Unknown or delivery risk | Consequence | Time-boxed spike / decision |
| --- | --- | --- |
| Native credentials, local server addresses, ATS, and household LAN privacy | A web session may work differently in `URLSession`; background access cannot be assumed | 2 days: connect a signed-in physical device to a representative TLS and LAN endpoint; define keychain/session renewal and the local-network prompt. Apple requires an `NSLocalNetworkUsageDescription` for direct local-host use, and documents ATS local-network rules in [NSLocalNetworkUsageDescription](https://developer.apple.com/documentation/bundleresources/information-property-list/nslocalnetworkusagedescription) and [NSAllowsLocalNetworking](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking). |
| Direct/remux/transcode behavior versus native decoder capabilities | Incorrect profiles either cause needless conversion or unsupported playback | 3 days: send a typed native capability profile through `decide`, then prove direct and HLS starts against a small H.264/AAC set. |
| “Infuse-equivalent” premium format scope | Largest range driver; an unqualified claim will produce late failures | 5 days to define the acceptance matrix and play representative H.264/HEVC SDR/HDR/Dolby Vision, AAC/E-AC-3/Atmos, and lossless cases on supported physical routes. Convert the result into server contract and backlog rows before committing tier 3. |
| iPhone Duo continuity and iPad multitasking | Layout polish can become a separate code path if state is not shared | 2 days: test the primary browse-to-player path in Xcode 27.1 Device Hub and confirm navigation/player state across resize, open/close, and split view. Apple’s [Duo design guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-iphone-duo) calls for a continuous experience across both displays; Apple’s [iPhone Duo announcement](https://www.apple.com/newsroom/2026/09/apple-unveils-iphone-duo/) says the inner and outer displays have the same aspect ratio. |
| TestFlight/App Review access to a household server and media rights | External testing or review can pause while a reviewer cannot reach the backend or assess streamed content | 1–2 days before external distribution: prepare non-personal demo account/content, review notes, and accessible backend. Apple says the first external TestFlight build may receive review, and App Review asks for working account credentials/backend access and may ask for rights information for third-party audio/video content: [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/), [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/uk/), and [complete-review information](https://developer.apple.com/help/app-review/before-submitting-for-review/complete-review/). This is a conditional review risk, not a prediction of rejection. |

Device downloads are intentionally absent from launch sizing. If later requested, `URLSession` background downloads and `AVAssetDownloadURLSession` supply system-managed transfer mechanisms, but they introduce device storage, visibility revocation, encrypted media/file protection, resume, and support contracts. Apple documents the mechanics in [Downloading files in the background](https://developer.apple.com/documentation/foundation/downloading-files-in-the-background) and [AVAssetDownloadURLSession](https://developer.apple.com/documentation/avfoundation/avassetdownloadurlsession). Server-side acquisition jobs are a different capability and must not be presented as native offline viewing.

Likewise, the current native web EPUB reader is not launch scope. It sanitizes server-produced prose into DOM elements in [`frontend/src/features/books/BookReader.tsx`](../../frontend/src/features/books/BookReader.tsx); that implementation cannot be carried into a no-web-wrapper app. A native EPUB reader is a separate renderer/library evaluation, not incidental work in this estimate.

## Short appendix: full web parity is a separate program

Current web parity is explicitly **not** a launch tier and has not been added to the totals. Beyond the viewing beta it would add, at minimum, native books/EPUB rendering, dedicated music library and queue behavior, Sonos, native offline storage, downloads and acquisition controls, live/channels, requests, collections, command palette, all settings, metadata editing, and owner administration. Each has a different trust and playback boundary. Re-plan those feature packs after the viewing beta has established the mobile API and playback contracts; do not use the viewing estimates as a proxy for all-web parity.

## Evidence and limits

Repository inspection covered the current route map, app shell, visual tokens, player paths, API client/types, local playback router/decision/session code, and the existing phone visual evidence listed above. The inspected web app remains much broader than this launch scope: books, music, downloads, requests/acquisition, channels/live streaming, collections, settings, and owner administration remain web-owned for this plan.

No iOS project was created; no server was started; no media was played; and no physical iPhone, iPad, Duo, AirPlay target, HDR display, or audio route was tested. The time-boxed spikes are the work needed to turn those unknowns into an implementation commitment.
