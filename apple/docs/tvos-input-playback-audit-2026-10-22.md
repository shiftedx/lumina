# tvOS input and playback audit — 2026-10-22

## Scope and evidence

This is a static source audit of the native tvOS application and shared player
as they stand in this worktree. It covers remote navigation and focus, not
general visual design. It is **not** a certification of Human Interface
Guidelines compliance and it does not prove physical Siri Remote behavior.

No simulator or hardware run, app build, archive, export, upload, or deployment
was performed for this audit. The user has requested that builds and uploads
remain on hold. Rows marked **source-confirmed** mean the cited source contains
the intended hierarchy, focusable control, or command handler; they still need
the planned interactive workflow before release. **Fixed-source, pending sim**
means an implementation exists in the worktree but event ordering or focus
behavior cannot be established by reading SwiftUI source. **Hardware unknown**
means that a real Apple TV is needed because the simulator cannot establish the
remote, output-device, overscan, or HDR behavior.

Apple's operative tvOS rule is: “In almost all cases, open the parent of the
current screen when people press the Back button.” The same guidance says that
at the top level the parent is the Apple TV Home Screen, whereas an app's parent
is defined by its hierarchy and isn't necessarily the previous screen. Pressing
and holding Back goes to the Home Screen. [Apple HIG — Remotes](https://developer.apple.com/design/human-interface-guidelines/remotes?changes=_9)

That rule makes the desired behavior explicit: Menu/Back from a pushed title,
season, request detail, filter sheet, history sheet, or player returns to that
presentation's parent. At an app-level Video or Audio destination, Lumina
returns to its in-app Home destination; it does not replace the system's
press-and-hold route to Apple TV Home.

## Product evidence and supported Apple APIs

Infuse is useful product evidence, not an Apple platform requirement. Firecore
documents “Access playback options: Swipe down while a video is playing” on
Apple TV, while on iPhone and iPad it documents direct-screen playback gestures.
Its subtitle documentation specifies the Apple TV down-swipe and an iOS/macOS
gear/options entry point. [Infuse playback controls](https://support.firecore.com/hc/en-us/articles/215090987-Playback-Controls)
[Infuse subtitles](https://support.firecore.com/hc/en-us/articles/215090967-Using-Subtitles)

Apple's current AVKit contract has an important distinction:

- `AVPlayerViewController.playbackControlsIncludeInfoViews` presents native
  metadata, navigation-marker, and playback-settings views when requested; it
  is enabled by default on tvOS.
- `customInfoViewControllers` adds native player content tabs on tvOS 15+.
  These are a good place for compact, truthful playback information.
- `transportBarCustomMenuItems` adds `UIAction` or one-level `UIMenu` controls
  above the tvOS transport bar on tvOS 15+.
- `customOverlayViewController` is the Apple-supported **swipe-up** extension
  point when transport is hidden. Apple explicitly says to use it instead of
  installing a custom swipe gesture recognizer. It is a different integration point from the user-requested down-swipe panel;
  that panel is a Lumina product choice rather than an AVKit requirement.

[Apple — Customizing the tvOS playback experience](https://developer.apple.com/documentation/avkit/customizing-the-tvos-playback-experience)
[Apple — AVPlayerViewController custom overlay](https://developer.apple.com/documentation/avkit/avplayerviewcontroller/customoverlayviewcontroller)

The tvOS tab, transport-menu, and custom-overlay properties are unavailable on
iOS. Thus iOS should use a compact, discoverable overflow/settings surface
attached to its own chrome; it should not emulate an Apple TV down-swipe panel.

Plex is consistent with separating per-playback choices from global policy. Its
current Apple TV settings document app-wide streaming quality, Direct Play,
audio boost, and subtitle-burn choices. [Plex — Settings for Apple TV](https://support.plex.tv/articles/settings-plex-for-apple-tv/)
Plex's current mobile settings documentation covers version 2025.10 and later,
separating global Video & Audio policy from subtitle presentation preferences.
Its audio-track enhancement guide covers iOS and tvOS from 2025.28 and documents
an on-screen gear leading to Playback Settings and Playback Options.
These support the discoverable-settings pattern without implying Lumina supports
Plex's transcoding features. [Plex — Mobile settings](https://support.plex.tv/articles/205671068-settings-plex-mobile/)
[Plex — Audio track enhancements](https://support.plex.tv/articles/audio-track-enhancements-for-video/)
The older iOS playback article is explicitly limited to version 8.45 and earlier
and is not used as current UI evidence.

For selectable audio and captions, AVFoundation is the source of truth. The
asset exposes audible, visual, and legible media-selection groups; a custom
picker selects an `AVMediaSelectionOption` with
`AVPlayerItem.select(_:in:)`. AVKit already supports presentation and selection
of captions and alternate tracks, and AVPlayer defaults to system media
selection preferences. [Apple — Selecting subtitles and alternative audio tracks](https://developer.apple.com/documentation/avfoundation/selecting-subtitles-and-alternative-audio-tracks)

## Static audit inventory

| Area | Source finding | Status | Required workflow |
| --- | --- | --- | --- |
| Sign-in | `TVConnectionView` uses focusable server, account, password, and Connect fields, with default focus on the server field. Connection failures are visible labels. | Source-confirmed | Move through every field, submit and recover from a failed connection, then press Menu. |
| Root destinations | `TVLibraryView` presents Home, Video, Audio, and Requests as focusable buttons. It moves focus into content after a down movement. | Source-confirmed | Traverse header-to-content and each destination in both directions; verify no unreachable edge focus. |
| Root Back behavior | The Exit handler exists only for non-Home destinations at the media navigation root. Home leaves Back to tvOS. Pushed media disables the root handler; Requests details dismiss one level explicitly. Event ordering remains pending simulator validation. | Fixed-source, pending sim | Open Video → title → season → episode; one Menu at each depth must pop exactly one parent. From top-level Video, Audio, and Requests, one Menu must select Home; hold Menu must leave the app. |
| Home hero and rails | `TVHomeView`, `TVFeaturedView`, and `TVRail` provide focusable Play/Details and media cards with identifiers used by native UI tests. | Source-confirmed | Move across a full rail, between rails, hero Play/Details, and back to the header without focus loss. |
| Video categories, search, and filters | `TVVideoView` exposes category controls, search, a filter sheet, result cards, and clear-filters action. The filter sheet has its own `NavigationStack`. | Source-confirmed | Open/close filters with Menu; complete a filter, clear it, open a result, and return to Video then Home. |
| Title detail and video collections | `TVDetailView` and `TVVideoCollectionView` use the root navigation path for collection/title/season/episode movement; their visible actions expose Play, watchlist, overflow, conversion, and state errors. | Source-confirmed | Validate a 3-level series path plus collection error/retry and every overflow action; Menu must preserve the expected previous focused card. |
| Audio browse, collection, queue, and player | Audio browse and music collection have focusable album/track actions. Audio playback uses app-owned `PlaybackScreen`, `NativeAudioSession`, and queue controls; Play/Pause is explicitly bound on tvOS. | Source-confirmed for browse controls; hardware unknown for output | Test track/album playback, queue opening/dismissal, repeated Menu, Play/Pause, interruption, receiver route changes, and recovery from unavailable tracks. |
| Video playback transport | `PlaybackScreen` currently uses `AVPlayerViewController` as the renderer. The player must retain the system's remote semantics while custom Lumina chrome replaces the generic appearance requested by dogfood. | Fixed-source, pending sim | Confirm Menu closes settings, then transport, then playback one level at a time; Select reveals transport, Play/Pause toggles playback, and left/right seeking works, custom controls neither duplicate nor obscure system transport, and focus returns to the initiating detail. |
| Playback information, audio, subtitles, and stream choice | Source audio inventory and external text-subtitle metadata are decoded. Audio changes prepare independently, retain position and caption choice, and restore the old stream on failure. Appearance uses AVFoundation text rules. | Fixed-source, pending sim | Exercise multiple audio codecs, native and external captions, failures, replay, and cleanup. Exercise quality limits and subtitle timing as well; output format remains automatic. |
| Requests search, featured rails, detail, forms, history | `TVRequestsView` has focusable search, browse controls, featured/poster rows, detail, season/language pickers, confirmation, error/retry, and a history sheet with its own navigation stack. | Source-confirmed | Exercise search → detail → request; cancel every picker/sheet with Menu; test quota/error retry and history opening/closing. |
| Account, licenses, messages, and errors | Header account uses a `confirmationDialog`; licenses use a sheet. Library, collection, and request error states use explicit retry actions. | Source-confirmed | Open account and licenses from every root destination, cancel with Menu, force/recover one error per main surface, and confirm focus stays intelligible. |

## Playback capability boundary

ADR 0031 retains one muxed audio stream per on-device Matroska session. The
follow-up patch selects that source stream explicitly and exposes text subtitle
renditions through a local HLS master. Audio bridges remain disabled; unsupported
audio asks the server for a compatible route. HDR/Dolby Vision and physical audio
output still require separate qualification. [ADR 0031](../../docs/adr/0031-client-side-matroska-packaging.md)

The implemented source keeps `AVPlayerViewController` as the renderer and
AVPlayer as decoder/transport owner, but hides AVKit's built-in chrome in favor
of the custom appearance explicitly requested for dogfood. Apple's preference
is to use the system player and follow its interactions closely when custom UI
is necessary. The intended replacement is auto-hiding Lumina transport, a tvOS
down-command panel with Info/Video/Audio/Subtitles, and an iOS settings sheet.
Video sizing changes Fit/Fill. Audio choices use source stream metadata, falling
back to the active asset's audible group when metadata is absent. Subtitle
selection uses the actual legible group plus validated external text files.
Auto and supported Off choices remain available. Stream method/container are
information rather than output-format switches.

Codec/output format, alternative media versions, and 3D mode remain outside
this patch. Quality now offers Automatic and ceilings of 1080p/8 Mbps,
720p/4 Mbps, and 480p/1.5 Mbps through the server playback decision. These are
ceilings, not promises to upscale a lower-quality source. Subtitle timing offers
half-second adjustments within ±10 seconds for packaged MKV text tracks and
attached WebVTT files; positive values show text later. Persisted appearance now supports font,
size, color, regular/bold weight, edge, background, opacity, and vertical position
for WebVTT. Disabling customization defers to platform preferences. Authored
markup and system caption preferences can override fallback text rules; bitmap
and burned-in subtitles cannot be restyled.

The Apple HIG also prefers standard gestures for standard actions and expects
Play/Pause to control media playback. Its remote button table expects Back to return to the previous screen
or exit to Apple TV Home, touch-surface swipes to navigate/change focus, and
Play/Pause to play or pause media. [Apple HIG — Remotes](https://developer.apple.com/design/human-interface-guidelines/remotes?changes=_9)

For custom controls, relevant routed HIG guidance includes: “Use progressive
disclosure to reduce initial choices/content”; “Use Liquid Glass effects
sparingly on custom controls; system components provide the appearance and
behavior automatically”; and, on tvOS, “Inset primary content 60 pt top/bottom
and 80 pt sides for overscan/compatibility.” Custom tvOS controls have a 66×66
pt recommended target and a 56×56 pt minimum. These are design constraints for
the custom chrome, not proof that the current source satisfies them.

## Required validation after the player/navigation change

Run these workflows in an Apple TV Simulator, then repeat the remote/output
items on physical Apple TV before any release candidate:

1. Root, detail, collection, sheet, full-screen player, and error-state Menu
   behavior. Verify one press produces one hierarchy transition; verify
   press-and-hold reaches Apple TV Home.
2. Header/rail/grid focus traversal at normal and increased text sizes, with
   Reduce Motion and Increase Contrast enabled independently and together.
3. Video transport: Select, Play/Pause, scrub, Menu close, replay/error, and
   return focus. Ensure custom controls do not create a second visible transport
   stack.
4. Down-swipe playback information: metadata, active route, no unavailable
   choices, and any actual audible/legible selections. Verify state survives
   pause, seek, background/foreground, and completion.
5. Physical-device audio output, captions, AirPlay/PiP policy, HDR/dynamic
   range, receiver behavior, overscan, and remote variants. These remain
   hardware qualification, especially for the ADR 0031 client-packaged path.

## Guidance corpus consulted

The Apple HIG routing pass for this audit read all selected files: the 16
foundations (accessibility, branding, color, dark mode, design principles,
icons, images, inclusion, layout, materials, motion, privacy, right-to-left,
SF Symbols, typography, writing); iOS/tvOS platform guidance; and the routed
input/playback topics: AirPlay, context menus, drag and drop, edit menus,
entering data, eyes, file management, focus and selection, gestures, full
screen, keyboards, live viewing, menus, multitasking, onboarding, pickers,
playing audio/video, pop-up and pull-down buttons, remotes, settings, spatial
layout, text fields, menu bar, and Top Shelf. The audit applies only the clauses
quoted above to this scope; it does not claim unrelated guidance is satisfied.


## Implemented patch and static checks

- Root Video/Audio/Requests Back selects in-app Home. The root handler is absent
  at Home and while the media navigation path is pushed. Request detail handles
  its own one-level dismissal. Title and series/season detail respond to the
  remote Play/Pause button by preparing their playable title/next episode.
- The shared player retains its AVPlayer/AVPlayerViewController renderer and
  session lifecycle, with built-in chrome hidden. Lumina transport hides after
  four seconds of playing inactivity, stays visible for paused/error/completed
  states, and remains visible during slider scrubbing. Remote focus changes and
  seek actions reset the hide timer. TV controls respect overscan insets.
- From unobstructed tvOS playback, Down opens the Info/Video/Audio/Subtitles
  panel. While controls or settings are visible, directional focus remains for
  their buttons. Select reveals transport; Play/Pause toggles playback. Back
  dismisses the open settings panel, then visible transport, then playback.
  Errors/completion exit after dismissing any open panel. The audio queue closes
  before its player. iOS has a dark, scrollable settings sheet and the Duo
  tabletop retains separate controls with settings access.
- Fit/Fill changes renderer gravity; audio/subtitle selection uses the active
  item's playable groups. Auto retains system criteria, and subtitle Off is
  available only when empty selection is supported. Selection groups are
  reloaded after player-item replacement and checked against item identity.
  The visible timeline updates each second while server progress cadence stays
  at five seconds.

The retained native UI workflows now assert Video detail → Video → Home,
Requests detail → Requests → Home, Audio → Home, and Home → Apple TV Home Screen.
They also cover remote Play on a title, auto-hide, opening settings, Fit/Fill,
closing settings without leaving playback, Play/Pause, and return to detail.
Existing resume/resize/background and server-progress contracts remain.

Swift frontend syntax parsing passed for iOS and tvOS with their platform SDKs,
and `git diff --check` passed. Parsing does not type-check SwiftUI expressions,
compile the app, run these tests, or prove focus/gesture behavior. All builds,
UI test execution, simulator/device validation, archives and uploads remain on
hold until the user explicitly resumes them. The earlier build-9 archive
predates this patch and is not the completed dogfood candidate.


## Playback follow-up: source audio and subtitle appearance

- Audio switches prepare and validate a separate candidate before replacing the
  active item. A failed candidate leaves the old stream selected; successful
  changes retain position, pause intent, and an explicit caption choice. Session
  reports stay serialized, including cleanup for failed candidates. Replay
  retains audio and caption choices.
- External WebVTT is downloaded with authenticated, same-origin requests, a
  10 MiB limit, and no credential-bearing query in local subtitle playlists.
  Converted SubRip is accepted only through a VTT delivery URL. Temporary files
  and local listeners belong to their playback source and are removed on stop.
- MKV playback retains native AVPlayer decoding and muxed audio. External text
  subtitles on progressive MP4 request compatible server HLS, because AVPlayer
  cannot directly attach a sidecar to that progressive item.
- The tvOS Subtitles panel presents tracks and appearance in two columns; iOS
  presents the same settings vertically. Changes persist on this device and
  apply to new player items.

Regression coverage was authored for source-track requests, stream inventory,
subtitle download boundaries, style persistence/rules, real synthetic MKV/HLS
audio/subtitle selection and listener teardown, and the settings UI workflow.
It has **not been executed** under the build hold. Swift syntax parsing and
whitespace checks do not establish type correctness, playable media, caption
layout, focus behavior, or decoder/output compatibility. Those checks remain
required when builds and simulator/device validation are authorized again.


## Quality and timing follow-up

The Video panel now requests a quality ceiling through PlaybackInfo, retaining
the existing codec constraints, selected media source, audio/subtitle indexes,
and position. Explicitly returning to Automatic removes the ceiling and permits
direct playback again when compatible. The server/member playback policy still
applies. Quality remains selected through audio changes and replay.

Timing is local to this playback model, retained through stream replacement and
replay, and reset for a new video. Each change prepares a fresh candidate before
commit, so buffered subtitle segments cannot silently retain the old offset.
Prism receives timing before packaging starts; a direct HLS sidecar is staged
with adjusted WebVTT timing. The prior stream and offset remain selected if
preparation fails. Direct embedded MP4 captions and burned-in captions cannot be
shifted by this renderer. When a nonzero native offset cannot be carried into a
converted stream, the transition fails safely and asks the viewer to reset
timing first.

Request contracts, caption timing/cleanup contracts, and native UI workflows
have been extended. The existing synthetic MKV proof fixture gains a synthetic
embedded subtitle without changing its title, identity, or duration. All
executable validation remains pending under the user’s build hold; syntax and
whitespace checks alone do not prove server encoding, visible subtitle timing,
remote focus, or transactional playback behavior.


## Chapters and playback speed follow-up

A Chapters tab lists actual embedded chapter titles and start times, highlights
the current chapter, and seeks without changing paused/playing intent. A video
that has completed opens a fresh paused session at the chosen chapter instead
of reusing the stopped source. Empty chapter lists show a clear empty state.

The existing ffprobe cache now records bounded, normalized chapter metadata.
Jellyfin item details and each selected media source carry markers through the
existing playback API; a different cut must not inherit the preferred version’s
chapters. Chapter zero remains a valid target. Legacy successful probe caches
refresh chapter metadata lazily without discarding usable playback facts when
the optional refresh cannot run. No schema migration or new route is introduced.

The Video tab offers a native speed picker for 0.5×, 0.75×, 1×, 1.25×, 1.5×,
and 2×. Slow rates require the ready item’s slow-play capability. The stable
AVPlayer retains its selected default rate through pause/resume, chapter seeks,
stream changes, and replay; video items use pitch-preserving spectral audio.
Changing speed while paused does not start playback. A new stream without slow
play support returns the visible selection to 1×. These controls apply to video;
the audio-library queue keeps its existing transport behavior.

Focused Python tests passed: 41 across chapter normalization, probe/cache
behavior, Jellyfin DTO compatibility, and query budgets. Native regression coverage now includes the 0/30/60-second
synthetic MKV chapters, speed selection while paused, chapter seek, quality
replacement, retained speed/position, and resume. Native execution remains
pending: source parsing cannot establish remote focus, visible playback rate,
pitch, seek precision, or simulator/device media behavior. No native build,
simulator run, archive, export, upload, or deployment was performed.


## Skip actions and next episode follow-up

Skip Intro and Skip Credits use recorded `/MediaSegments` ranges for the actual
selected media source. They appear only inside the matching range, seek exactly
to its endpoint, and retain pause state. Missing analysis offers no guessed
segment. Skip buttons can remain visible independently of hidden transport;
settings suppress the contextual overlay. These are manual controls; this
patch does not add automatic skipping or an autoplay countdown.

Next Episode is available in transport and during credits/completion. Its target
is the immediate visible successor in the server’s series order, including
season rollover. Preparing the target leaves the current picture/session intact
until readiness and seek validation complete. Failure retains the old episode
and a retryable action. Success stops the old session before reporting the new
one, starts the next episode at zero, preserves quality/speed/video fit and
caption appearance, and resets source-specific tracks, external subtitle files,
and subtitle timing. Back/close cancels preparation and stops the owned candidate.
Play/Pause and backgrounding can suppress pending autoplay. Replay cannot race
an outstanding episode handoff. No automatic episode transition is introduced.

Metadata loads separately from playback. Independent marker/episode failures
remain nonfatal and expose Retry Playback Actions in Info. Request identity and
load-generation checks prevent stale metadata from attaching to another item.

The synthetic episode fixture records real user segments through the existing
analysis service, then verifies the authenticated segment DTO. Native regression
coverage exercises paused intro skipping, credits skipping, and the next episode
starting at its own origin. Focused backend checks passed: 26 passed, one skipped
for segments and series ordering. Swift/Python syntax and whitespace checks
passed. Native regression execution, focus/render validation, builds, archives,
simulator/device runs, uploads, and deployments remain on hold.
