# Lumina tvOS remote navigation audit

The initial audit found that the custom tvOS transport had no interactive video timeline and changed directional behavior when controls appeared. The repair below replaces it with native AVKit transport while retaining Lumina’s playback settings. Findings and line references in the historical audit describe the original source at `ae2d56e7`; the final section records the repair and qualification.

## Scope and installation

Audited source: main at `ae2d56e7`, in the isolated `codex/tvos-axiom-audit` worktree. This is current-source qualification, not identification of the owner's installed TestFlight binary. At that initial audit stage, product source was unchanged; a characterization probe and Resume-dialog adaptation extended an existing UI journey. The subsequent repair is isolated in the same worktree, now retained at `/Users/kmcdowell/.codex/worktrees/lumina-tvos-native-avkit` on branch `codex/tvos-native-avkit`.

Axiom revision `58ae15ac93b1fa59db628f634a6c257e86e794b2` supplied the tvOS Focus Engine, Siri Remote, AVPlayer, Menu state-machine, and SwiftUI navigation checks. The six relevant Codex skills were installed under the local Codex skills directory: `axiom-swift`, `axiom-media`, `axiom-build`, `axiom-tools`, `axiom-swiftui`, and `axiom-audit-swiftui-nav`. This installs skill content; it does not enable plugin hooks or MCP services. The skills become discoverable on the next turn and were read directly for this audit.

The Debug app was built with Xcode 27.1 and installed on a dedicated Apple TV 4K third-generation Simulator running tvOS 27.0. UI execution uses `LUMINA_UI_TESTING`, disposable synthetic media, and local simulator signing. These runs do not qualify Release performance or physical remote feel. An initial unsigned attempt was interrupted because Keychain prevented sign-in; it supplies no playback or navigation result.

## Navigation architecture

- The app scene restores the shared session and presents `TVRootView`.
- A typed `[MediaItem]` path drives the main navigation stack.
- One matching destination registration handles movie, episode, collection, and music routes.
- The persistent header changes Home, library categories, and Requests destinations.
- A separate destination history handles Back between those root destinations.
- Playback preparation resolves resume choices before presenting a full-screen player.
- Account settings have a second typed stack and matching route registration.
- Search and supporting dialogs use separate modal presentations.
- Navigation paths live in memory; neither typed path has persisted scene restoration.
- The common plist registers `lumina`, but pairing URL handling is confined to iOS.

Both programmatically pushed route types have registrations. Static modal stacks and destination-based links are not missing-path defects. No deprecated `NavigationLink(isActive:)` or `NavigationLink(tag:)` use was found in the audited shared/tvOS files. Relaunch restoration and the unused tvOS URL registration are follow-up product decisions, not established causes of the reported remote friction. Axiom's navigation-restoration criterion remains incomplete; this is not a blanket navigation-compliance pass.

## Findings

### High priority missing video scrubbing

`PlaybackScreen.swift:1924` renders a plain `ProgressView` for tvOS. The interactive `VideoGestureTimeline` is iOS-only (`1327`). The video renderer is `NativePlayerLayer` (`1458`), backed by `AVPlayerLayer` (`6192`); there is no system transport supplying the missing interaction.

The hidden-controls handler (`803`) immediately seeks ten seconds on Left or Right, then calls `revealVideoChrome`. Once controls are visible, that handler no longer seeks; the interface instead relies on focusable transport buttons (`1943–1999`). The viewer must select an explicit seek button or hide the controls to use the hidden shortcut again. There is no tvOS scrub-position preview, commit/cancel interaction, or clickpad-ring handling in this path.

Apple documents repeated ten-second skips, held rewind/fast-forward, and pause followed by a horizontal swipe to choose a position, with resume or Back to commit/cancel. This is the expected Apple TV app workflow, not a requirement to reproduce every optional Apple TV app feature. [Apple playback guide](https://support.apple.com/guide/tv/control-video-playback-atvb7944597f/tvos)

### High priority transport focus does not reach Forward after reveal

The simulator probe paused the actual video clock, hid transport with Back, and sent Right twice. The first Right passed the check for at least eight seconds of advancement, consistent with the ten-second handler. The second Right passed a two-second observation with no additional advancement greater than two seconds, then failed the assertion that `videoSeekForwardButton` held focus. The failure screenshot shows paused video at approximately 28 seconds with transport visible. The first post-reveal Right therefore neither continued seeking nor reached the expected Forward target.

Inspect focus handoff in `revealVideoChrome` (`1754`) and directional handlers on the transport row (`1996`) and enclosing playback screen (`803`). Axiom cautions that assigning SwiftUI focus does not establish actual Focus Engine state. The failed target assertion establishes the workflow defect; it does not by itself identify which handler or layout decision owns the failure.

### Medium priority center Select only reveals controls

The hidden video surface (`1363`) handles Select by revealing chrome without toggling playback. Focus then moves to Play/Pause (`1754`). Pausing with center Select therefore takes a reveal action and a second activation. The separate physical Play/Pause command works directly (`798`). This adds interaction cost compared with the center-click play/pause behavior documented in Apple's playback guide.

### Medium priority tvOS controls can disappear during VoiceOver use

The four-second hide timer (`1754–1772`) and `hideVideoChrome` (`1744`) protect VoiceOver only inside iOS compilation guards. tvOS does not read the corresponding environment value (`416–419`). Focus movement restarts the timer (`672`), but remaining on a control still permits automatic hiding and focus transfer to the invisible reveal surface. This is a source-confirmed accessibility risk; spoken feedback and actual focus behavior still need a physical Apple TV VoiceOver workflow.

## Recommended repair

Use `AVPlayerViewController` for tvOS transport while retaining the existing playback model and stable `AVPlayer`. Expose Lumina-specific quality/settings through supported transport menus or info controllers, and map intro/credits/next-episode actions to contextual actions. Keep the iOS transport separate. Apple documents these customization surfaces in [Customizing the tvOS playback experience](https://developer.apple.com/documentation/avkit/customizing-the-tvos-playback-experience); Axiom's [tvOS guide](https://github.com/CharlesWiltgen/Axiom/blob/58ae15ac93b1fa59db628f634a6c257e86e794b2/axiom-codex/skills/axiom-swift/skills/tvos.md) also favors the player's supported extension points over intercepting its gestures.

Acceptance should cover paused swipe seeking, repeated skips, held seeking, center Select, cancellation back to the original position, pause-intent retention, settings dismissal, and focus returning to the originating episode. Physical qualification must cover both touch-surface and clickpad remotes. Simulator directional presses cannot establish analog swipe or ring feel.

## Validation

The tvOS build-for-testing passed. Signed simulator workflows passed for `NativeTVBackHistoryTests/testTVBackRestoresChronologicalDestinations` (87.888 seconds) and `NativeTVEpisodeRemoteNavigationTests/testEpisodeCardSelectStartsPlayback` (68.514 seconds), including return focus to the originating episode. Their result bundle is `/tmp/lumina-axiom-signed-navigation.xcresult`.

The final `NativeTVRemoteVideoStartTests/testRemoteStartsVideoFromDetails` probe reached real paused playback and reproduced the transport-focus assertion failure described above. This is retained failing audit evidence, not a passing regression suite. Teardown stalled after the assertion; the owned app was terminated and the runner interrupted, so the bundle must not be treated as a normally completed workflow. The result bundle is `/tmp/lumina-axiom-seek-qualified.xcresult`; the captured screen is `/tmp/lumina-axiom-failure-screen-small.png`. Earlier diagnostic attempts stopped at an unhandled Resume dialog, an ambiguous Resume selector, or overlapping test-runner startup; none establishes a product defect. The final run uses the dialog-scoped selector and a restarted, exclusively owned simulator.

`mise exec -- make check` failed on unchanged product source. Backend result: 5,843 passed, 23 skipped, 2 failed, with 6 subtests passed. The two failures are in `test_jellyfin_settings_native.py`: `test_native_streaming_capability_requires_admin_opt_in_and_member_provider_access` and `test_native_streaming_can_remain_available_when_youtube_is_member_blocked`. Their exact-response expectations omit the returned `autoplay_presence` object. Frontend typechecking and production build/style budgets passed before the backend gate stopped the command; later gate stages did not run.

No archive, upload, deployment, or product fix is part of this audit.


## Native AVKit repair and qualification

The tvOS video presentation now embeds stock `AVPlayerViewController` through `TVNativeVideoPlayer.swift`, using the existing stable `PlaybackModel.player`. AVKit owns remote input, focus, control visibility, repeated skips and timeline scrub preview/commit/cancel. The custom tvOS overlay and its global directional/Play-Pause handlers have been removed. No AVKit subclass or replacement gesture recognizer is involved.

A native transport action opens Lumina’s existing Info, Video, Audio, Subtitles and Chapters settings. Quality, Fit/Fill, capability-checked speed, source audio, subtitle appearance/timing and chapter selection retain their existing model and service paths. Native subtitle selections feed the same selection policy. Native contextual actions expose intro/credits skip, next episode and replay. A focus section makes Done reachable from the settings tabs. AVKit rate changes synchronize the model’s playback intent, and periodic callbacks no longer reset AVKit scanning rates. Settings presentation preserves the playback session and paused state. iOS continues to use its existing transport.

Simulator verification uses synthetic fixtures on the dedicated tvOS 27.0 Apple TV 4K simulator. The following user workflows passed:

| Workflow | Evidence |
| --- | --- |
| Detail navigation, remote start/pause, settings Fit/Fill, Done and Back, repeated Right skips, paused scrub preview and Select commit, Back cancellation retaining the original position | `lumina-avkit-workflows-r5.xcresult`; repeated successfully in `lumina-avkit-remote-final.xcresult` |
| Hide native controls during the intro, reopen with Select after the hide transition, navigate into settings, Back returns to playback | `lumina-avkit-hidden-ready.xcresult` |
| Episode card Select starts video; native pause and return focus | `lumina-avkit-workflows-r5.xcresult` |
| Capability-checked speed, chapters and quality repackaging of paused MKV; playback resumes after settings | `lumina-avkit-workflows-r5.xcresult` |
| Source audio selection and subtitle appearance settings | `lumina-avkit-settings-r7.xcresult` (NativeVideoSettingsTests passed; its separate caption test was subsequently corrected and rerun below) |
| Actual rendered subtitle cue verified with OCR, subtitle timing adjustment/reset, 480p HLS handoff and selected subtitle retention | `lumina-avkit-subtitles-r8.xcresult` |

Raw result bundles and compact Axiom summaries are under `/tmp/lumina-avkit-*`. Selected screenshots and result summaries are retained in ignored `output/tvos-native-avkit-2026-11-01/` in this worktree. Earlier unsuccessful runs are diagnostic evidence, not passing qualification. The additional intro check initially sent inputs during the hide animation and encountered recycled duplicate accessibility cells; waiting for the transition and selecting the first matching cell passed without a product change. In particular, native transport actions are accessibility cells, settings transitions require a readiness wait, and AVKit retains hidden transport entries in its accessibility hierarchy; the tests now check observable playback state and rendered captions instead of assuming the deleted custom overlay’s structure.

Both tvOS and iOS simulator builds passed. `git diff --check` passed. The required `mise exec -- make check` was attempted: frontend typecheck, build and style budgets passed; backend finished with 5,842 passed, 23 skipped and three failures, stopping the remaining gate. Two native Jellyfin capability tests expect a payload without the existing `autoplay_presence` field; the third is a native book file-change-time validation assertion. No backend source was changed by this repair. The full gate is therefore **not green**. The retained gate log is `/tmp/lumina-avkit-make-check-r2.log`.

Qualification limits: XCTest remote button events prove simulator navigation and scrub preview/commit/cancel. They do not qualify physical Siri Remote touch swipes, clickpad-ring feel, HDR/display matching, release performance or all media/provider combinations. AVKit supplies the standard touch transport, but physical-device validation remains necessary. No archive, upload or deployment was performed.
