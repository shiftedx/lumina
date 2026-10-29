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
  are superseded by the latest committed target; obsolete native seeks are
  cancelled rather than blocking the new target. Relative skips use the
  optimistic destination. Drag previews still commit only once on release.
- A right swipe drives the system-owned `MPVolumeView` slider, sharing output
  volume with hardware buttons. Chromecast retains its receiver volume. The
  redundant video volume sliders are removed.
- Brightness targets the screen of the player's application scene. On Duo,
  `UIScreen.main` can identify the other built-in panel after unfolding; using it
  can move the HUD while leaving the visible panel unchanged. Window attachment
  and layout reports recheck the scene's display, releasing the old display's
  lease when it changes. External presentation scenes remain excluded. A shared
  per-display lease restores the original level on exit/inactivity/PiP only while
  Lumina still owns the last applied value. External changes revoke ownership.
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
qualification. Two-finger chords and live-window seek bounds have deterministic
state tests; they are not claimed as hardware-tested. XCTest's default full-screen
two-finger tap places contacts inside excluded system-edge bands, so it is not
used to claim a valid centered chord. No new thumbnail provider, server endpoint,
full repository suite, archive, upload or release was added for this task.


## Duo brightness targeting follow-up (2026-10-28)

The owner reported that the physical outer panel responded to brightness swipes,
while the inner panel showed a moving HUD without a visible brightness change.
The previous native check asserted only the requested level. A strengthened check
failed on the unfolded 951×669-point player because its brightness target was not
the screen supplied by the player's window scene. The global main-screen fallback
has been removed; brightness writes now use that application-scene display.

The existing device-controls journey now asserts display identity and retains an
attachment containing requested brightness and active-display readback. On the
final source it passed both the unfolded 951×669 and folded 466×678 viewports,
including brightness/volume dispatch, pause intent and public Close. The focused
Core gesture, playback-state and brightness-ownership filters passed 27 tests.

Simulator readback remained 0.5 after writes in both postures, including the outer
posture where the owner reports that physical control works. These results prove
target selection and dispatch, not physical panel dimming. Physical brightness
and restoration still need confirmation in the next requested device build;
uploaded build 31 contains the old routing. No archive or upload was made.
