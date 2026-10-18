# Native tvOS feasibility — 2026-10-17

## Decision summary

A fully native Swift/SwiftUI Apple TV app is feasible without changing the
product's catalog identity: Lumina's cinematic art, paper-and-ink palette,
editorial type hierarchy, gold rules, Home hero, and horizontally organised
catalog rails translate well to the living room. It is not feasible as a
pixel-for-pixel port of the responsive web app. tvOS is a directional,
focus-based interface operated primarily with the Siri Remote, and Apple asks
developers to make every on-screen control reachable by remote or keyboard.
The appropriate product is therefore **Lumina's visual identity and content
architecture, redesigned around a TV focus graph and system playback UI**.

This is a source-and-documentation feasibility assessment only. No Xcode
project, server, Apple TV, physical display, or media fixture was run. Time
estimates are planning ranges for one experienced Apple developer working
sequentially, include physical-device QA and release stabilization, and do not
include an unknown App Review queue. They assume a deployment target of
**tvOS 18** as a planning baseline to be confirmed against the project's
Xcode 27.1 toolchain and product support policy. It is not a claim about the
newest tvOS release.

## Lumina surfaces reviewed

The current web shell groups Home, Video, Audio, Books, Streaming, Requests,
Downloads, My Settings, and owner-only Manage Server navigation
([Navigation.tsx](../../frontend/src/app/Navigation.tsx)). The review examined
the implementation, rather than treating the prior design research as the
product:

| Current surface | Evidence in the repository | Apple TV implication |
| --- | --- | --- |
| Home | `HomeSurface` composes a hero, Continue Watching, fetched title shelves, Live, follows, recommendations, recent saves, and watchlist; shelves lazy-load near the viewport. | Strong fit. Preserve the hero and rails, but make one predictable initial focus destination and a directional graph between hero actions and each rail. Do not carry the web carousel's hover and touch behavior over. |
| Video and catalog | `LibraryBrowser`, `GalleryTitlePage`, collections, lenses, seasons/episodes, cast and extras provide the local catalogue and title detail. | Strong fit. Use an inset, large-art detail page with Play/Resume as the default focused action, followed by seasons, extras, and related rails. Grid density, action menus, and filtering need TV-specific layouts. |
| Watch | `WatchSurface` combines video/local audio, continuation, Up Next, related, versions, captions, track preferences, playback menus, a mini player, chat, transcript, moments, notes, AI summary/idea graph, provenance, and tools. | Split the experience. Native system playback owns primary transport, PiP, subtitles, audio selection, and seeking. Keep a small immediate action set; put continuation/Up Next on a post-playback or detail surface. Treat each analysis/editorial panel as an explicit later TV feature, not a free port. |
| Audio/music | `AudioSurface` has a featured-artist soundstage, music catalogue, playlists, curation; `MusicPlayer` has queue, lyrics, crossfade, ReplayGain, sleep timer, offline saves, and Sonos output transfer. | Music discovery, album/artist detail, basic queue, and Now Playing fit TV. Inline text editing, playlist construction/reorder, offline downloads, and Sonos management are poor first-release TV targets. The browser's Web Audio graph is not reusable on tvOS. |
| Other web areas | Streaming providers/live, Books, Requests, Downloads, member/settings/owner tools, metadata editing, and administration appear in routes and navigation. | These are material scope additions, not incidental screens. Basic member requests can follow the consumer v1; provider streaming/live, Books, downloads, and administration should each be separately estimated. |

The implementation also confirms that playback is not merely a file URL: it
uses visibility-scoped playback decisions, HLS, continuation, item/version
state, subtitle/track preferences, and server music-session revisions. The
Jellyfin-compatible surface already supports non-browser device credentials
(ADR 0010), while ordinary `/api` routes are browser/cookie and CSRF oriented.
The native app therefore needs a deliberately supported device-auth/API
contract. Reusing the compatibility API is a possible bootstrap path, but it
does not automatically expose Lumina Home, recommendations, requests, music
session, or the product-specific watch panels.

## Current Apple platform constraints

Apple's Human Interface Guidelines make the interaction redesign a product
requirement, not styling polish:

* The Siri Remote is the primary Apple TV input. Standard remote gestures and
  button meanings should keep their standard purpose; a paired phone may be a
  convenience for text entry or sign-in but cannot be required to navigate or
  recover the app. [Remotes](https://developer.apple.com/design/human-interface-guidelines/remotes/)
* tvOS uses directional focus. Every control must be reachable with the remote
  or a connected keyboard, focus should use the system effect where possible,
  and focus should not jump unexpectedly. Full-screen media gestures affect
  the content rather than a hidden cursor.
  [Focus and selection](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/)
* Primary content should respect Apple TV's 60-point vertical and 80-point
  horizontal safe-area guidance. Focused controls grow, so art rails need
  breathing room; layout must also cope with text size and localisation.
  [Layout](https://developer.apple.com/design/human-interface-guidelines/layout/)
* Search is a destination, not an always-open desktop field. A search landing
  page can orient people with genres/categories before they enter text.
  [Search fields](https://developer.apple.com/design/human-interface-guidelines/search-fields/)
* A focus guide can deliberately bridge non-geometric focus movements, and
  UIKit supplies a restoration preference after a controller transition. These
  are tools for the hard cases, not substitutes for a coherent rail/detail
  focus design. [UIFocusGuide](https://developer.apple.com/documentation/uikit/uifocusguide),
  [UIViewController focus restoration](https://developer.apple.com/documentation/uikit/uiviewcontroller)

### Interaction translations

| Web convention | TV-native decision |
| --- | --- |
| Hover prefetch, hover/focus carousel pause, mouse/scroll position restore | Preserve data prefetching, but drive it from focus/selection and explicit appearance state. Restore focus to the launching card after Back if it still exists; fall back to its rail heading or adjacent card if it was removed. Never depend on hover or a pointer. |
| Browser top bar, popover navigation, command palette | Use a small persistent top-level navigation model (Home, Video, Search, Profile) and a remote-friendly sidebar/tab structure. Settings/admin live below a member profile/menu, not beside daily viewing destinations. The Menu/Back behavior must unwind transient UI, then page depth, predictably. |
| Responsive cards, dense grids, inline filters and inputs | Keep poster/backdrop art and rails, but lower density, increase focus-safe gaps, turn filters/actions into short menus or a dedicated filter screen, and avoid forms during normal TV consumption. |
| Home hero plus many personalized/fetched shelves | Keep the shelf order and catalogue voice. Limit the first screen to an obvious Play/Resume and a handful of high-confidence rails; lazy fetch remains useful, but its completion must not steal focus. |
| Title actions, contextual buttons, ellipsis menus | Put Play/Resume first. Use `Menu`/system actions for infrequent commands (watchlist, version, mark watched, refresh). Avoid a row of tiny icon controls. |
| Keyboard search and editable playlist/metadata forms | Provide Search as a first-class screen using the tvOS text-entry system, with recent/suggested results and voice/keyboard as optional accelerators. Defer broad metadata curation, rule builders, batch selection, and server configuration to web/iPad. |
| Watch page's concurrent side panels | Keep the viewing canvas clear. Put only genuinely in-session actions in native playback UI; launch transcript, notes, queue, and AI panels as separate focused screens only when their TV value is proven. Live chat needs a separate remote-friendly read-first design. |

## Playback: system UI is an advantage and a boundary

`AVPlayerViewController` is the default recommendation for video. On tvOS it
provides the familiar Apple TV transport experience, Siri Remote voice-command
support and Picture in Picture. Apple documents supported customization: title
and subtitle metadata, content information, custom transport-bar actions and
menus, and content tabs; system controls continue to provide common settings
such as subtitle selection and PiP.
[Customizing the tvOS playback experience](https://developer.apple.com/documentation/avkit/customizing-the-tvos-playback-experience)

That yields a defensible first player:

1. Create the HLS/direct-play asset from a Lumina playback decision with a
   registered native-tvOS capability profile and device credential.
2. Feed title, episode/subtitle, artwork and live-state metadata into the
   system player; report start, heartbeat, seek, selected tracks, end, and
   error to Lumina's existing progress/session contracts.
3. Let system UI select normal audio and subtitle choices when HLS exposes
   them; map deliberate Lumina defaults to AVFoundation media selections and
   save the resulting user preference. `AVPlayerItem` supports selecting one
   media option from a selection group (or none when allowed).
   [Media selection](https://developer.apple.com/documentation/avfoundation/avplayeritem/select%28_%3Ain%3A%29)
4. Add only a short curated action menu such as watched/watchlist, version,
   and a link to the native Up Next screen. Validate each action with a real
   remote in the system player.

The system player cannot promise web-player pixel parity. The web's custom
overlay layout, mini-player presentation, Web Audio loudness/profanity graph,
custom caption styling, every watch tool panel, and arbitrary chat/transcript
widgets are not automatically represented by AVKit. A custom overlay is
possible, but it introduces its own focus, remote, accessibility and
transport-conflict work; it should be funded feature by feature. Native
parity means preserving user outcomes and Lumina's visual identity, not
replacing Apple's playback transport with a screenshot of the web player.

### Video and audio compatibility risks

* **Profiles and fallback.** Lumina's pure playback decision currently favors
  direct play, remux, audio-only encode, then video encode (ADR 0012). A tvOS
  client profile must exercise each route and report actionable failure/retry
  behavior; do not assume a browser HLS capability profile is an Apple TV
  profile.
* **HDR and colour.** `preferredMaximumResolution` can express an HLS maximum,
  but does not make an incompatible rendition playable.
  [AVPlayerItem resolution preference](https://developer.apple.com/documentation/avfoundation/avplayeritem/preferredmaximumresolution)
  Apple's HLS authoring spec requires the appropriate codec/container choices,
  calls out HDR10/HLG/Dolby Vision for HDR HEVC, tvOS subtitle WebVTT, SDR
  trick-play, and codec-consistent variants. The transcode/manifest output and
  display/AVR configuration must be tested as a system.
  [HLS authoring specification for Apple devices](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices/)
* **Audio.** Test stereo AAC, Dolby Digital/Plus where offered, and Atmos only
  where the media, device, receiver, and server output all support it. The web
  loudness-normalization and profanity-mute behavior in ADR 0012 is explicitly
  browser/Web-Audio behavior; it needs a product decision (native processing,
  server rendition, or unavailable) rather than a port assumption.
* **Subtitles and accessibility.** Validate forced/default/off selection,
  language labels, WebVTT styling, burned-in fallback, audio-description
  tracks, and the system's caption/accessibility settings. Apple evaluates
  audio-description support separately for tvOS.
  [Audio Descriptions evaluation criteria](https://developer.apple.com/help/app-store-connect/manage-app-accessibility/audio-descriptions-evaluation-criteria/)
* **Progress correctness.** App termination, network loss, refreshed HLS
  credentials, version switching, delayed playback decisions, and Back from
  system playback must not duplicate starts/completions or lose an intentional
  rewind. This is cross-client contract work, not just an AVPlayer callback.

## Playback parity benchmark: Infuse outcomes, not an unbounded clone

The selected benchmark is **Infuse-like user-visible playback coverage for
Lumina media**, with server conversion/remuxing allowed. It is not a commitment
to clone Infuse's NAS, cloud, network-protocol, metadata-sync, transfer, or
entire codec-engine product. Firecore currently advertises a broad direct-play
list (including MKV, ISO, BDMV, H.266, DTS-HD and TrueHD), Dolby Vision
profiles 5 and 8, HDR10/HDR10+/HLG, and broad text/image subtitle support.
[Infuse formats and claims](https://firecore.com/infuse)

Firecore documents Atmos for E-AC3 tracks on compatible hardware. Its tvOS
path sends up-to-7.1, 24-bit, 48 kHz LPCM under the normal Auto setting and
decodes Dolby/DTS, including lossless families, to LPCM; it does not claim
TrueHD-Atmos or DTS:X passthrough on Apple TV.
[Firecore audio capabilities](https://support.firecore.com/hc/en-us/articles/217735707-Audio-Options-Capabilities)

Lumina's acceptance target should be: a tested file either direct plays or
gets a server remux/transcode rendition that preserves the best verified TV
outcome (4K HDR10/Dolby Vision where supported, E-AC3 Atmos where supported,
or documented high-quality LPCM/stereo fallback). This is not literal
direct-play parity for every Infuse input, or a promise that a receiver will
show a TrueHD/DTS-HD/Atmos badge when the correct output is LPCM.

Do a **2–3.5 person-week playback spike before production commitment**. Compare
AVFoundation plus Lumina conversion with established engines using the agreed
fixtures. Official VideoLAN [VLCKit](https://github.com/VideoLAN/VLCKit)
supports tvOS and explicitly targets media/subtitles beyond AVFoundation, but
its LGPL obligations and build/support costs need review. KSPlayer claims
FFmpeg-backed HDR/Dolby Vision/Atmos and image/text subtitles, but defaults to
GPL and describes paid LGPL/commercial terms; it is candidate evidence, not
validated Lumina capability. [KSPlayer](https://github.com/kingslay/KSPlayer)
The spike must cover license/App Store review, build size/startup,
HDR10/Dolby-Vision output, E-AC3 Atmos and TrueHD/DTS-HD-to-LPCM through an
AVR, subtitle treatment, fallback, remote transport, progress, and PiP.
Do not build a decoder.

## Scope tiers and bottom-up delivery ranges

These ranges include normal engineering, reviewable tests, physical-hardware
QA, and stabilization. They assume that a product owner makes timely calls on
authentication, unsupported media behavior, and which web-only tools are
deferred. They exclude a future iPad/iPhone client; shared-core work is called
out so that a later universal Apple programme does not re-estimate it as
entirely duplicated work.

| Scope | Delivered behaviour | Bottom-up arithmetic (person-weeks) | One-developer planning range |
| --- | --- | --- | --- |
| **Technical spike** | No release. Proves the native device/API seam and chooses AVFoundation-plus-server-conversion versus an established licensed engine against premium fixtures. | auth/playback contract **0.5–1** + fixtures/hardware **0.5–1** + engine/build/license/PiP/progress comparison **1–1.5** = **2–3.5** | **2–3.5 weeks** |
| **Video-first beta** | Device sign-in/configuration; profile selection; Home's Continue/selected rails; local Video browse/search/detail/season; Play/Resume, progress, normal audio/subtitle selection, Up Next; remote-first navigation; focused errors. No music, Books, Requests, provider streaming/live, offline downloads, admin, or broad watch tools. | app shell/auth/API contract **3–4** + shared models/repositories/cache **2–3** + Home/catalog/search/detail **3–4** + AVKit/playback/progress **3–4** + TV focus/layout/accessibility **2–3** + tests/device matrix/stabilization **5–6** = **18–24** | **18–24 weeks** (P50 about 20; ~4.5–6 calendar months) |
| **Everyday viewing v1** | Beta plus profiles, watchlist/collections, basic AI discovery/recaps and playback settings. This remains a viewing app: no music-library UI, Books, Requests, downloads, editing, or administration. | video beta **18–24** + profiles/AI discovery/collections/settings **3–5** + cross-surface focus, regression and wider hardware/release stabilization **3–5** = **24–34** | **24–34 weeks** (P50 about 29; ~6–8.5 calendar months) |
| **Premium Infuse-outcome qualification** | Everyday viewing v1 plus proven 4K HDR10/Dolby Vision, E-AC3 Atmos, robust subtitles, and server remux/transcode/LPCM fallback for the agreed fixture catalogue. | playback spike **2–3.5** + client/server capability and rendition work **4–7** + HDR/AVR/subtitle qualification and recovery hardening **4–8** = **10–18.5 incremental** | Add **10–18.5 weeks** unless the spike proves most fixtures already work. |
| **Broad web feature parity** | Everyday v1 plus streaming/live, Books, downloads/offline, music, dense watch panels, subscriptions, metadata editing, settings and owner/admin operations. | everyday viewing **24–34** + streaming/live **4–6** + Books **4–6** + downloads/offline **3–5** + music **5–8** + watch panels **5–8** + creator/admin/settings **6–9** + hardening **5–7** = **56–83** | **56–83 weeks** (P50 about 70; ~14–21 calendar months) |

Separate, additive planning increments:

* **Provider streaming/live:** 4–6 weeks after consumer v1, assuming the
  server has an authorized, AVFoundation-playable path and clear platform
  content rights. This is not part of ordinary local-video playback.
* **Books:** 4–6 weeks for a TV-appropriate catalogue/detail and a carefully
  defined reader/listener outcome; longer if it means a full remote-controlled
  long-form reader rather than discovery and handoff.
* **Top Shelf:** 1–2 weeks plus QA, only after Home and deep links are stable.
  TV Services supports a full-screen Top Shelf extension and warns that its
  extension has lower memory limits, so it should receive prepared server data
  rather than execute expensive local discovery.
  [TV Services](https://developer.apple.com/documentation/tvservices),
  [Top Shelf sample](https://developer.apple.com/documentation/tvservices/building-a-full-screen-top-shelf-extension)
* **Pairing / second-screen control:** 2–4 weeks after device auth and remote
  navigation are complete. It is optional product polish, never a substitute
  for usable Siri Remote input.

### Shared Apple core versus TV-only work

For the selected main release, keep costs distinct so a combined mobile + TV
plan does not count shared work twice. The 10–18.5 week premium qualification
increment above is distributed across the shared playback contract and the
TV adapter/physical qualification below; it is not a third addition. Thus the
selected TV release is **15–23 shared + 19–30 TV-only = 34–53 person-weeks**,
the same range as everyday viewing v1 plus premium qualification.

| Work owned once for the Apple programme | Person-weeks | Incremental tvOS work only | Person-weeks |
| --- | ---: | --- | ---: |
| Device auth/API repositories, profiles, Home/AI-domain models, cache/artwork policy, and progress contracts | **8–12** | TV information architecture, Home/catalog/detail presentation, and TV-scale identity system | **5–8** |
| Premium playback spike and shared capability/rendition contract | **7–11** | Focus graph/restoration, Menu/Back, remote/keyboard, and TV accessibility | **4–6** |
|  |  | TV player adapter plus the TV share of track/subtitle and fallback integration | **2–4** |
|  |  | Physical Remote/display/AVR/subtitle matrix, TV regression and stabilization | **8–12** |
| **Shared core total** | **15–23** | **TV-only total (Top Shelf excluded)** | **19–30** |

The TV-only figure is not a reskin: focus/input and physical output
qualification are material. Top Shelf remains a 1–2 week option after deep
links and Home are stable. A later iPad/iPhone client can reuse the shared
core while retaining separate UI and playback adapters.

## High-risk work and acceptance evidence

| Risk | Why it is hard | Evidence before scope exit |
| --- | --- | --- |
| Device auth and API seam | Browser session/CSRF routes are not a native-device protocol; the existing Jellyfin API is deliberately compatibility-scoped. | Threat-modelled native device registration/token revocation; long-lived device credentials never logged or URL-encoded; item/session-scoped playback capabilities for media URLs; relogin/revocation test; and an explicitly versioned native API or documented compatibility subset. |
| Focus restoration | Async shelves, disappeared cards, modal menus, player exit, and detail navigation all create ambiguous return targets. | UI tests plus physical remote scripts prove initial focus, four-direction movement, Menu/Back unwind order, return to the originating item, and deterministic fallback after deletion/reload. |
| Media matrix | Server direct/remux/transcode, HLS expiry, codecs, HDR, audio sinks, captions, and network conditions combine multiplicatively. | Fixture-backed results record route chosen, startup, seek, track switch, exit, resume, and recoverable error for every required media/device/display combination. |
| System-player boundary | AVKit has supported customization but does not contain every browser tool panel. | Product has a signed list of system-player actions/tabs and deferred panels; no acceptance criterion calls for web overlay pixel parity. |
| Music output and parity | Web Audio, offline cache, Sonos capability tickets, and revisioned shared queues do not map directly to Apple TV. | Out of the selected viewing release; if added later, start with a separately tested server-session/basic-queue scope, not output transfer/offline/crossfade parity. |
| Reviewability of a self-hosted server | Reviewers need a reachable, stable account-based demonstration without private-network assumptions. | Submission pack includes a working demo account, content, setup/reachability steps, external services, tested device/OS list, and reviewer notes. Apple asks for those details and current credentials for account-based features. [Complete review information](https://developer.apple.com/help/app-review/before-submitting-for-review/complete-review/) |

## Required test matrix and release handoff

Use automated unit and UI coverage for route/focus state, fixture-backed API
integration for auth/progress/playback decision mapping, and a physical
release matrix. Simulator coverage is useful for navigation regression but
cannot establish remote feel, HDMI/display modes, HDR, receiver audio, or
real network behavior.

Minimum release candidates should cover:

| Dimension | Minimum evidence |
| --- | --- |
| Apple TV hardware | One supported 1080p/SDR baseline when the selected deployment target supports it; two Apple TV 4K generations/configurations, including the intended shipping baseline. Record exact model, tvOS build, storage/network configuration. |
| Displays and output | 1080p SDR television with overscan check; 4K HDR10 television; Dolby Vision display if claimed; TV speakers/stereo; AVR or soundbar path for each claimed multichannel/Atmos outcome. |
| Inputs/accessibility | Siri Remote clickpad and Menu/Back/Play-Pause behavior; connected keyboard; VoiceOver traversal; Larger Text; Increase Contrast/Reduce Motion; system captions/subtitles and audio descriptions. A paired iPhone remote/keyboard may be tested as optional help, never as required navigation hardware. |
| Media fixtures | Direct H.264/AAC; direct HEVC/4K SDR; each claimed HDR format; multichannel and stereo; external/embedded and forced subtitles; alternate audio/audio description; remux and video/audio transcode; live HLS if shipped; expired/refreshed HLS URL; unavailable/unauthorized item. |
| Network/lifecycle | Wired and Wi-Fi; constrained/interrupt/reconnect; server restart; app background/termination; token revoke/sign-out; progress after seek and version switch; return from player; deep link/Top Shelf if shipped. |

The review handoff should be a short operator and App Review runbook: exact
build and server version, deployment target, tested matrix/results, known
unsupported formats/features, demo account and non-sensitive sample content,
server reachability/setup steps, screenshots/video, and an explicit statement
that remote navigation works with no paired phone. Apple recommends supplying
tested models/OSs, external services, and valid credentials up front; a
required-hardware app needs a physical-device pairing video.
[Provide information for a complete review](https://developer.apple.com/help/app-review/before-submitting-for-review/complete-review/)

## Planning sensitivities and blockers

The ranges move materially with unresolved choices:

1. **Native API and sign-in.** The largest early schedule variable is whether
   the app receives a purpose-built native device API and secure pairing flow,
   or begins from the Jellyfin-compatible API with a deliberately limited
   Lumina feature set. Budget the higher end until this is decided.
2. **Supported media promise.** 4K HDR/Dolby Vision, multichannel/Atmos,
   live playback, rich subtitle styling, and server transcode fallback require
   fixture and hardware evidence. A beta that promises only tested local HLS
   video can ship sooner than a beta advertised as format-transparent.
3. **Product boundary.** The selected everyday viewing v1 is local Video,
   profiles, browse/search/playback/watch progress, and AI discovery. Music,
   Books, Requests, streaming/live, offline, owner/admin, editing, and every
   dense watch panel remain explicit increments.
4. **Distribution model.** A household-only sideload/TestFlight route and a
   public App Store release have different packaging, reviewer-access, content
   rights, and stabilization needs. The estimates include release preparation,
   not a guaranteed review duration.

There are no implementation blockers in the inspected source. The planning
blockers are product decisions about distribution, the native authentication
contract, exact minimum hardware/OS support, claimed media formats, and the
consumer-v1 boundary.
