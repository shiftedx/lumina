# iOS playback gestures

Implementation and qualification, 2026-10-25. The supplied gesture handoff defines the
interaction rules; it is guidance for the existing player, not a new engine.

- `App/Shared/PlaybackScreen.swift`: `PlaybackModel` owns one `AVPlayer`, playback
  intent, normal video speed, stream replacement, seek/progress reporting and
  continuous episodes. `NativeVideoSource` prepares compatible AVPlayer items,
  including the existing client-demux route. Audio retains `NativeAudioSession`.
- The video presentation is app-owned SwiftUI chrome over `AVPlayerLayer` in
  `PlayerLayerView`; the existing PiP controller, AirPlay picker and media-track
  selection stay intact. A transparent UIKit touch collector sits below controls,
  including letterboxing. The lower tabletop transport retains its own touches.
- Existing seek buttons and timelines call `PlaybackModel.seek(to:)`. Gestures
  join that authority; previews do not seek until release. Outstanding requests
  coalesce and relative skips use the optimistic destination.
- A right swipe drives the system-owned `MPVolumeView` slider, sharing output
  volume with hardware buttons. Chromecast retains its receiver volume. The
  redundant video volume sliders are removed.
- Brightness uses UIKit's supported main-screen brightness control while the
  player belongs to an application scene. Duo inner-window screens are tracked
  for attachment/transition changes but do not provide a separate supported
  brightness-write endpoint. External presentation scenes are excluded. A shared
  lease restores the original level on exit/inactivity/PiP only while Lumina still
  owns the last applied value. External changes revoke ownership.
- `Core/.../NativePlaybackPreferences.swift` stores device preferences in
  `UserDefaults`. New gesture choices use that model and default on; temporary
  speed and brightness never persist. Device Settings and player settings share
  the same settings component.
- HUDs use `LuminaTheme`, approved `LuminaIcon` assets where semantics exist,
  neutral material and timestamps. VoiceOver retains persistent visible controls;
  custom multi-touch interpretation is disabled. System edge regions are excluded.
- Scene, PiP, modal, item, route, interruption, geometry and accessibility changes
  cancel contacts and temporary rate overrides. Cancellation never requests Play.

Validation uses focused `apple/Core` Swift tests and the existing iOS UI-test
target. No full repository suite or distribution build is requested. Physical
volume routing, screen brightness and simultaneous multi-touch require device
qualification; simulator compilation alone does not establish those behaviors.

Apple references: [player-local volume](https://developer.apple.com/documentation/avfoundation/avplayer/volume),
[system volume](https://developer.apple.com/documentation/avfaudio/avaudiosession/outputvolume),
[screen brightness](https://developer.apple.com/documentation/uikit/uiscreen/brightness).

## Interaction map

| Input | Action |
| --- | --- |
| Single tap | Show/hide controls; remains available with advanced gestures off |
| Two-finger tap | Toggle play/pause once |
| Left/right vertical swipe | System screen brightness / device output volume |
| Left/right double tap | Skip backward / forward; further same-side taps accumulate |
| Horizontal swipe | Preview relative position; seek once on release; cancel discards preview |
| Hold | Temporary absolute 2× while eligible playback is already running |
| Timeline drag, then move upward | Absolute seek, then incremental 0.5× / 0.25× / 0.1× fine scrubbing |

Device Settings and the player's Video settings share enablement, individual
gesture switches, haptics, separate forward/back intervals and an on-demand map.
Defaults are enabled and 10 seconds in each direction. Disabling double-tap skip
removes the single-tap delay. Temporary speed and brightness are never persisted.
Brightness restoration uses the specified 0.01 comparison tolerance; UIKit does
not identify the writer, so external-change attribution remains a heuristic.

## Retained verification

- 29 focused Core checks passed: gesture classification (17), seek/rate state
  (6), brightness ownership (3), and preference persistence/defaults (3).
- `NativeVideoGestureTests` passed on iPad mini (A17 Pro), iPadOS 27.0, in
  portrait and landscape: control revelation/dismissal, double-tap seeking,
  paused-hold safety, timeline dragging and rotation.
- The same workflow passed on standard iPhone 17, iOS 27.0, in both orientations,
  including forward and backward surface scrubbing. These runs use the isolated
  proof server's direct synthetic video through the existing public API.
- The native icon adapter preserves semantic image labels, fixing empty
  icon-only Play/Pause names without changing the SVG pixels or native menu shape.
- UI-test window selection now handles the inactive zero-sized first window
  exposed during full-screen playback. This is test infrastructure, not a player
  layout workaround.

Core command: `mise exec -- swift test --package-path apple/Core --filter
'VideoGestureClassifierTests|VideoPlaybackGestureStateTests|VideoBrightnessOwnershipTests|NativePlaybackPreferencesTests'`.

Physical Duo/open/closed-screen gesture feel, real brightness/system-volume
behavior, route changes, interruptions and PiP during an active hold remain device
qualification. Two-finger chords and live-window coalescing have deterministic
state tests; they are not claimed as hardware-tested. XCTest's default full-screen
two-finger tap places contacts inside excluded system-edge bands, so it is not
used to claim a valid centered chord. No new thumbnail provider, server endpoint,
full repository suite, archive, upload or release was added for this task.
