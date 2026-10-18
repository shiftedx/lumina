# Native Lumina for iPhone, iPad, iPhone Duo, and Apple TV

Research and planning estimate, 2026-10-17. Estimates are engineering judgment, not Apple or Firecore delivery promises. This document synthesizes three delegated investigations and the current Lumina working tree; it does not authorize implementation.

## Recommendation

Build one native iOS/iPadOS application, including an adaptive iPhone Duo experience, and a separate native tvOS application. Share Swift models, network/authentication code, image caching, member preferences, playback/progress logic, and the qualified media engine. Use platform-specific navigation and input behavior.

For the clarified viewing scope, plan a **usable TestFlight beta in 12–16 weeks**, followed by a **production release in approximately 24–36 weeks (6–9 months)** with the recommended team. The production range assumes an agreed, representative Infuse-style playback matrix and reuse of an existing native playback engine where AVPlayer is insufficient. It does not establish exhaustive parity with every format in Infuse's advertised catalog. If “parity” includes that entire catalog, including disc images and long-tail codecs, use **9–15 months as a preliminary budget envelope**, with low confidence until the media-engine evaluation passes.

Because the user explicitly requested Infuse playback parity, carry the wider **9–15 month envelope** for budgeting until the format matrix is agreed. The 6–9 month target is a qualified premium release, not a substitute for that full requirement. The first 2–3 calendar weeks should resolve the playback architecture and turn the range into a committed scope and schedule. There is no responsible unconditional Infuse-parity date before that evidence exists.

## Confirmed scope and remaining assumptions

The user selected household-library browse, search, playback, watch progress, profiles, and AI discovery, with administration remaining on the web. YouTube/Twitch/Kick viewing and live chat are excluded from v1. The apps will be paid upfront in the App Store. The device scope is iPhone, iPad, iPhone Duo, and Apple TV. Premium playback includes 4K HDR10/Dolby Vision, supported Atmos, subtitles, and reliable remote streaming; conversion is permitted, and Infuse is the requested playback benchmark.

The estimate assumes:

- Fully native Swift/SwiftUI interfaces and native media playback; existing React screens, CSS, browser audio graphs, and browser storage are rewritten for Apple platforms.
- The existing self-hosted Lumina server remains the catalog, permissions, recommendation, metadata, and progress authority. AI discovery consumes Lumina's existing results; it does not add a new on-device model or assistant product.
- Profile means Lumina member identity, visibility, preferences, and progress. Automatic mapping to Apple TV system users is a later option.
- Remote viewing uses the existing deployment model and a configured HTTPS endpoint. A managed relay, VPN, or automatic router configuration is outside this estimate.
- Dedicated music-library functionality, books, native offline downloads, requests management, and server operations are outside the selected first release.
- The baseline covers household-library video over LAN and remote HTTPS; online providers and live chat stay outside the release.
- A provisional support floor is iOS/iPadOS 18 and tvOS 18, with OS-specific APIs gated. Final support depends on the selected engine and hardware matrix; iPhone Duo exercises iOS 27 paths. These floors are planning assumptions, not statements of the latest OS.

## What Lumina already provides

The current app has real catalog/detail/search, household visibility, favorites, progress, subtitle/audio enumeration, playback decisions, direct byte ranges, and HLS conversion. Its opt-in Jellyfin facade already provides device credentials and HLS child URLs that carry authentication for AVPlayer. That is a strong starting point for a native playback spike.

The facade does not represent all Lumina Home, profile, and AI experiences. Production needs a small, explicit first-party client contract that reuses current services and authorization. A complete API redesign, a new backend, or generation of a client from every administrative endpoint is unnecessary.

The premium gap is material: the existing safe conversion path uses H.264/AAC. It cannot be treated as a quality-preserving substitute for HDR, lossless multichannel audio, or Atmos. The browser's DASH player, custom subtitle presentation, and Web Audio processing also do not transfer to AVPlayer automatically. See the [API readiness investigation](native-apple-api-readiness-2026-10-17.md), particularly its source references to playback decisions, local sessions, authentication, and current working-tree changes.

## What “Infuse playback parity” means

Firecore advertises substantially more than 4K playback: MKV and disc/container support, Dolby Vision profiles 5 and 8, HDR10/HDR10+/HLG, numerous text and image subtitle formats, and broad audio decoding. Its audio documentation describes lossless output through multichannel PCM, with E-AC3 Atmos support and limitations for TrueHD Atmos and DTS:X. Those distinctions belong in Lumina's acceptance tests. [Infuse specifications](https://firecore.com/infuse), [Firecore audio capabilities](https://support.firecore.com/hc/en-us/articles/217735707-Audio-Options-Capabilities).

Apple's supported HLS codecs, packaging, Dolby Vision profiles, audio renditions, and device capabilities are separate constraints. A source file's badge is not proof that its actual output retains HDR or Atmos. [Apple HLS authoring specification](https://developer.apple.com/streaming/hls-authoring-specification-for-apple-devices.html), [Apple TV 4K specifications](https://www.apple.com/apple-tv-4k/specs/).

Start the engine evaluation with AVPlayer and the current server transport. Compare an established native engine for gaps; VLCKit supports iOS/tvOS and media outside AVFoundation's usual support, while KSPlayer is another candidate with published format claims. Neither project's claims prove Lumina compatibility. Verify the exact release, build configuration, license, hardware decoding, subtitle rendering, output quality, and system integrations before selecting one. Building new decoders is outside this plan. [VLCKit](https://github.com/VideoLAN/VLCKit), [KSPlayer](https://github.com/kingslay/KSPlayer).

The first qualification set should cover:

- H.264/AAC MP4 and ordinary direct/HLS playback; HEVC Main10 HDR10 in MKV; supported Dolby Vision profiles and E-AC3 Atmos.
- TrueHD/DTS-HD MA to correctly mapped multichannel lossless PCM, within the platform's documented output limits.
- SRT/VTT, styled ASS/SSA, and PGS/VobSub, with forced/default/SDH and language preferences.
- High-bitrate 4K, repeated seeks, subtitle/audio changes, A/V synchronization, completion and resume, interrupted networks, and resource-constrained server fallback.
- Real display/receiver/headphone output, frame-rate/dynamic-range switching where supported, plus AirPlay/PiP behavior for each qualified engine path.

Disc images, VVC/H.266, unusual legacy codecs, damaged files, and every casting/output combination require their own rows if literal catalog-wide parity is required. Server conversion may make a file playable without meeting the original quality target; record those outcomes separately.

## Apple-aligned release contract

The [App Store readiness review](native-apple-app-store-readiness-2026-10-17.md) turns policy into a risk register, verification checklist, and draft reviewer notes. These are planned release requirements; no native binary currently exists to certify.

| Area | Required condition or proposed launch decision |
| --- | --- |
| Sale model | Paid upfront through the App Store. Recommend one universal purchase for the iOS/iPadOS and tvOS targets; a fully unlocked app has no separate subscription/feature-unlock flow. Complete the Paid Apps Agreement and required business details. [Universal purchase](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-platforms), [Paid app setup](https://developer.apple.com/help/app-store-connect/get-started/app-store-connect-workflow). |
| Honest product page | State clearly before purchase that Lumina requires a separately installed server and the user's own authorized media. Include support and setup information; do not imply films or subscriptions come with the app. [Complete review information](https://developer.apple.com/help/app-review/before-submitting-for-review/complete-review/). |
| Content boundary | Household media viewer. Exclude provider acquisition, arbitrary URL extraction/download controls, online providers and live chat in both review and production builds. This is the selected scope, not a claim that Apple bans all third-party streaming. [App Review Guidelines, 5.2](https://developer.apple.com/app-store/review/guidelines/). |
| Account experience | Sign in to an existing Lumina household member account; provide logout/device revocation. Own-account authentication does not automatically require social login. If app-supported registration, automatic accounts, or registration links are later added, include account-deletion initiation. [Account deletion guidance](https://developer.apple.com/support/offering-account-deletion-in-your-app/). |
| Privacy and AI | Inventory actual server, provider, telemetry and SDK data flows; publish matching privacy disclosures, iOS policy URL and tvOS policy text. If personal data goes to third-party AI, disclose and obtain explicit permission first; local-only AI is a different flow. [App privacy](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/), [Privacy manifests](https://developer.apple.com/documentation/bundleresources/privacy-manifest-files), [App Review Guidelines, 5.1.2](https://developer.apple.com/app-store/review/guidelines/). |
| Networking | Use system networking and narrowly justified transport settings. On iOS/iPadOS, provide local-network purpose text and intentional permission timing; tvOS does not implement that privacy prompt. Test IPv6-only/DNS64/NAT64 access; this does not automatically require rewriting the public server for IPv6. [Local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy), [Apple IPv6 guidance](https://developer.apple.com/support/ipv6/). |
| Native media engine | Use public APIs and a bundled, appropriately licensed engine. No downloaded executable codec/plugins to introduce new features. Prove HDR/audio/subtitle claims for the selected builds and output routes. [App Review Guidelines, 2.5](https://developer.apple.com/app-store/review/guidelines/). |
| TV input | All required flows work using the Siri Remote, including sign-in fallback, search, playback and exit. A paired phone is optional. Top Shelf and other enhancements remain product options. [Remotes](https://developer.apple.com/design/human-interface-guidelines/remotes). |
| Review access | Maintain a reachable demo server/account with authorized sample video and artwork, stable credentials, a tested-device list, and complete review notes. [Reviewer access](https://developer.apple.com/help/app-review/before-submitting-for-review/complete-review/). |
| Duo and store assets | Include folding/resizing state preservation and current device screenshots. Apple's notice makes Duo screenshots mandatory for submissions starting April 2027, within the projected production window. iOS 27 SDK uploads also need a launch-screen configuration; keep the new SwiftUI project's generated configuration. [Duo submission guidance](https://developer.apple.com/news/?id=kkphp5qo), [Launch screen requirement](https://developer.apple.com/documentation/technotes/tn3208-preparing-your-apps-launch-screen-to-meet-app-store-requirements). |

Draft product-page prerequisite copy: **“Requires a separately installed Lumina server and your own media library. No movies, TV shows, or streaming subscriptions are included.”**

Policy and submission work is allocated to the foundation and release rows below; it is not an extra uncounted phase. Monetization changes, external AI processing, or provider access would reopen the relevant requirements.

## Effort and complexity

One person-week is five dedicated engineering days, including normal implementation and focused tests. These are normalized workstreams: shared work is counted once, and platform/device qualification is allocated to the final row instead of repeated in every row.

| Workstream | Complexity | Person-weeks | Concrete result |
| --- | --- | ---: | --- |
| Shared Swift client foundation | Medium–high | 7–10 | API/authentication, member scope, cache, navigation data, preferences, progress, retries and error states |
| iPhone/iPad/Duo application | High | 12–18 | Lumina visual identity, Home/library/search/detail/profile/AI screens, touch navigation, adaptive layouts and native player integration |
| tvOS application | High | 12–18 | TV shell and rails, details/search/profile/AI, focus restoration, Remote/Back behavior and TV player integration |
| Shared first-party backend contracts | High | 6–10 | Narrow native authentication/data/playback boundary, revocation and media URL behavior, compatibility tests |
| Premium playback across both apps and server | Very high | 14–24 | Qualified engine, format negotiation, HDR/audio/subtitle preservation and fallback behavior; includes the engine spike and premium server changes |
| Combined hardware qualification and release | High | 8–12 | Representative files/devices/output chains, network and lifecycle checks, accessibility, TestFlight stabilization and submission assets |
| **Engineering total** | | **59–92** | |
| **Budget with approximately 15% contingency, rounded** | | **70–110** | |

The contingency is additional to ordinary testing, covering failed engine assumptions, new-device issues, and integration rework. It is not a promise that every possible Infuse format fits this budget. Schedule confidence is medium for UI/API work and low-to-medium for premium playback until the spike passes.

## Team and delivery

Recommend three experienced full-time Apple engineers: one owns iPhone/iPad/Duo, one owns tvOS/focus/input, and one owns the shared media engine and playback qualification. Add approximately 0.25–0.5 backend engineer and 0.5 QA engineer during integration, plus periodic design review. At least one engineer needs actual AVFoundation/VideoToolbox/FFmpeg or comparable media-stack experience.

| Milestone from kickoff | Delivery | Acceptance |
| --- | --- | --- |
| Weeks 1–3 | Technical and visual proof | Native apps connect and play; representative premium files expose engine/transport gaps; compare Lumina screens and approve format matrix |
| Weeks 4–10 | Internal vertical slices | Home → browse/search → detail → playback → progress on mobile and TV; working profile/visibility and AI fallbacks |
| Weeks 12–16 | TestFlight viewing beta | Useful end-to-end viewing on all target device types; validated baseline playback, with premium gaps explicitly tracked |
| Weeks 18–26 | Premium qualification beta | Agreed HDR/audio/subtitle cases pass on real devices; remote playback and fallback behavior are measurable and stable |
| Weeks 24–36 | Production release target | Required matrix passes, unresolved high-severity defects closed, accessibility/lifecycle/review preparation complete |

These windows overlap because the team works in parallel. The recommended team has roughly 3–4 full-time equivalents during different phases; its full capacity is not available for feature coding every week. With two Apple engineers and part-time backend/QA, budget roughly **9–13 months** for the defined production scope. With one engineer doing Apple, server, media and QA work, budget roughly **18–30 months**. Adding an inexperienced engineer does not divide the critical media work proportionally.

Actual App Store approval remains external. Reserve approximately 1–3 calendar weeks for submission/rework within the release window, and revise if review raises a material product issue. External TestFlight distribution can also require review. [Apple TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/).

## Preserve Lumina's UI while adapting input

Preserve the current paper/ink and dark themes, editorial serif headings, gold accents/rules, cinematic artwork, hero panels, poster/still rails, title hierarchy, Continue Watching and discovery concepts. Current route/component source is authoritative; existing evidence screenshots are useful visual references but have some older navigation labels. See [iOS investigation](native-ios-feasibility-2026-10-17.md) and [tvOS investigation](native-tvos-feasibility-2026-10-17.md).

For TV, enlarge content and type for viewing across the room, provide a clear primary Play/Resume action, use standard focus effects and predictable directional movement, retain a card's focus when returning from detail/playback, and move secondary actions into discoverable menus. Respect system Back and Play/Pause behavior, support system text entry, and keep every required flow usable with the Remote. TV focus and search need substantive interaction work even when the visual design stays close to the web. Use AVKit's supported customization points for native player actions and information panels. [Designing for tvOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-tvos), [Focus and selection](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection), [Remotes](https://developer.apple.com/design/human-interface-guidelines/remotes), [AVPlayerViewController](https://developer.apple.com/documentation/avkit/avplayerviewcontroller).

iPhone Duo is part of the mobile app. Include compact/expanded layouts, continuity of navigation and playback while folding/resizing, reserved-region/safe-area handling, and Split View. Native system navigation reduces custom adaptation work. Apple's October 5 notice confirms October 23, 2026 availability and Xcode 27.1 development support; this host has Xcode and iPhoneOS SDK 27.1 installed. Hardware qualification is still outstanding. [Apple Duo development notice](https://developer.apple.com/news/?id=kkphp5qo), [Designing for iPhone Duo](https://developer.apple.com/design/human-interface-guidelines/designing-for-iphone-duo).

## Scope sensitivities

- **Literal Infuse format-catalog parity:** preliminarily budget 9–15 months with the recommended team; only the engine spike can establish a defensible inclusion list and revised estimate.
- **Online provider viewing:** allow an additional planning envelope of 4–8 person-weeks for source/transport integration and qualification; live chat adds roughly 2–4. These are lower-confidence allowances, not commitments. Acquisition controls are a separate scope.
- **Offline video:** roughly 6–10 additional person-weeks across client/server for transfers, durable media, storage management, metadata and visibility lifecycle.
- **Music, books, owner administration:** separate products/workstreams relative to this launch; the existing web's breadth should not silently enter the viewing estimate.

If online-provider access/acquisition ships, Apple requires appropriate authorization for third-party services and downloads; server-side execution does not by itself settle the review question. Supply a reviewer-accessible demo server or full demo mode with authorized sample media for this self-hosted app. [App Review Guidelines, completeness and 5.2.2–5.2.3](https://developer.apple.com/app-store/review/guidelines/).

## Research evidence and validation limits

- [Native iOS feasibility](native-ios-feasibility-2026-10-17.md)
- [Native tvOS feasibility](native-tvos-feasibility-2026-10-17.md)
- [Native Apple API readiness](native-apple-api-readiness-2026-10-17.md)
- [Apple App Store readiness](native-apple-app-store-readiness-2026-10-17.md)

This work changes research documents only. The application already had extensive unrelated working-tree changes, which were preserved. No native app, decoder, device playback, HDR output, or App Store submission was built or validated.

The required mise exec -- make check ran. Bootstrap tests, frontend type checking, and the production build passed. The backend suite reported 5,030 passed, 11 skipped, and one failed: tests/test_reco_pool.py::test_the_maintenance_cycle_schedules_the_refresher_and_the_lifespan_closes_it. The gate stopped there, so its later frontend unit/browser stages did not run. This research did not modify application code or attempt to repair that separate failure. git diff --check passed.
