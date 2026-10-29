# Continue Lumina app dogfooding

Current continuation handoff, October 28, 2026. Use this for the owner's next native-app screenshot, recording, bug or small feature request. Older handoffs describe historical batches; this document supplies the current workflow.

**Focused testing only. Test the specific change. A full repository, Core or native UI suite requires the owner's direct request.** This explicit owner instruction overrides older `make check` requirements in repository docs and skills. Land accepted fixes on main; all current features must stay together.

## Work the next issue

1. **Verify the base.** Read applicable `AGENTS.md`, inspect changes/worktrees, then fetch upstream. Account for existing edits before updating the checkout. All implementation through [PR #198](https://github.com/shiftedx/lumina/pull/198) and the build-31 upload record [#199](https://github.com/shiftedx/lumina/pull/199) are merged. Use live Git state for HEAD and preserve other tasks' work. Done: the base and ownership of existing edits are known.
2. **Define the result.** Turn the owner's report into an observable acceptance criterion. Closed/folded Duo means the outer screen; open/unfolded means the larger inner screen. Inspect relevant code/context before asking for missing information. Continue independent work while an optional question is pending. Done: the affected path, posture and expected result are clear.
3. **Reproduce and fix.** Trace the control through its shared authority and service boundary. For latency, measure submission, completion and rendered output separately before tuning. Prefer the existing component and the smallest root-cause change. Use applicable skills and the live delegation policy. Done: the reported behavior is corrected without losing adjacent behavior.
4. **Prove the change.** Extend an existing public workflow or boundary regression; inspect comparable screenshots for visual changes. Select individual Core filters or native UI methods. Prefix pinned commands with `mise exec --`. After relevant checks pass, broaden only for a new failure or unresolved concern. Done: the acceptance criterion has direct evidence, with remaining failures and untested hardware explicitly identified.
5. **Merge and clean.** Commit a focused `codex/` branch, push, create a PR using a body file, and attach every created PR with the Codex artifact tool. Merge to main, remove the issue branch and owned disposable resources, then verify a clean checkout synchronized with origin/main. Preserve release packages and useful evidence. Done: the PR is merged and local state is accounted for.

Give concise progress updates while working. Carry the request through implementation and verification; ask only for information or access that actually blocks the next action. The final report states the result, merged PR, focused checks and material limits. If no new issue accompanies this handoff, report readiness and wait: the previous batch is complete.

## Preserve the current behavior

- **Navigation/artwork:** sunrise mark and categories without the Lumina wordmark or opaque category pill. Content extends beneath floating navigation; interactive controls clear the camera/status area and rail. Preserve discoverable category scrolling and the stable Home hero viewport during swipes.
- **Music:** wide artwork aligns with the lyrics top; AirPlay/Cast align with Lyrics/Queue at the bottom. Artwork opens Now Playing, with a separate Play/Pause control beneath it in the rail. Track entry preserves source queue/index.
- **Video:** device volume gestures/hardware buttons and screen brightness, with no overlay volume slider. Keep playback info available. Scrub previews commit on release; new committed targets supersede obsolete native seeks. Preserve pause intent, live-window bounds, stale-callback rejection, durable player ownership through posture/view changes, and Close/Stop teardown.
- **Library/Home:** permission-gated metadata editing and Fix match on poster long press/detail overflow; hero critic ratings; recommendation reason badges removed; quiet loaded-Home recovery from transient network failures. First-load, authentication and persistent failures remain actionable.
- **Lifecycle/reader/casting:** bounded background work, account-scoped artwork, reader position/settings and explicit casting session ownership. Simulator checks establish neither physical receiver playback nor device energy behavior. The reported full-device restart was not proven to be caused by Lumina.

Before logo/color/button/navigation changes, read the [brand contract](../../apple/docs/brand-assets.md). Keep established native styles and bespoke icons; this is iterative dogfooding, not an unsolicited redesign.

## Known follow-ups and evidence limits

- **Portrait detail Play CTA:** confirmed on standard iPhone: the floating tab bar can cover the hero's Play button. Not fixed. The focused gesture test enters playback in landscape, then tests the portrait/landscape player. That setup workaround does not establish that the detail-page obstruction is resolved.
- **Category artwork under the rail, PR #197:** iOS build passed; the closed-display baseline passed before the change. Open portrait visual verification was blocked by a locked Mac. Recheck the actual open display when this layout is next touched.
- **Brightness:** both Duo postures passed dispatch/availability checks. Actual physical backlight behavior remains a device check; a HUD alone is insufficient.
- **Seek performance, PR #198:** the paced-HTTP cached-target reproduction improved from 2.645 seconds to about 50 ms through decoded-frame delivery. Uncached jumps still took approximately 2.5–3.1 seconds. These are synthetic macOS AVFoundation measurements, not physical Duo/4K/HDR/WAN guarantees. See the [measurement record](../../apple/docs/playback-seek-performance-2026-10-28.md).

## Read when the issue needs it

| Issue | Entry points / reference |
| --- | --- |
| Categories, floating navigation, Home | `apple/App/iOS/MobileRootView.swift`; shared `MediaPresentation.swift` |
| Scrubbing, player lifetime, playback info | `apple/App/Shared/PlaybackScreen.swift`; `apple/Core/Sources/LuminaCore/VideoPlaybackGestureState.swift`; [gesture contract](../../apple/docs/playback-gestures.md) |
| Gesture recognition, brightness, timeline | `apple/App/iOS/VideoGesturePresentation.swift`, `VideoGestureTimeline.swift` |
| Delivery, music, artwork, books | `NativeVideoSource.swift`, `NativeMusicTransport.swift` in Core; `NativeAudioSession.swift`, `SubjectAwareArtwork.swift` in Shared; `NativeBooksView.swift` in iOS |
| Native regression coverage | `apple/Tests/UITests/NativeProofTests.swift`; `apple/Core/Tests/LuminaCoreTests/` |
| Older integration details | [Historical native handoff](../../apple/docs/dogfood-handoff.md); [build-26 validation](dogfood-validation-handoff.md) |

## Testing and local cleanup gotchas

- Create a dedicated proof simulator and fixture runtime; preserve other tasks' devices/processes. Device Hub controls Duo posture; XCTest orientation can target its inactive panel. Duo requires the compatible iOS 27.1 runtime on this host. Verify observed viewport geometry before claiming a posture check.
- Use CUA for Xcode/Device Hub UI interaction. CLI builds/tests and simulator lifecycle commands remain useful. When the Mac is locked, request unlock and continue independent work; report blocked UI/hardware checks accurately.
- Give concurrent Xcode builds/tests separate DerivedData directories. Wait for the process to exit before reusing its directory or simulator. A passed/failed log line is not process completion. Wait on returned session IDs and bound log output.
- Current navigation has a Library tab; old tests can still look for the removed Home Browse menu. Poster context menus can give misleading AX hittable results. Repair test entry through visible public controls and observed frames, retaining the actual behavior assertions.
- Cleanup removed this chat's stale dogfood worktree and 11 merged branches. Older release packages were moved into this checkout's ignored `apple/Distribution/`. Recovery bundle, manifest and historical output evidence are at `/Users/kmcdowell/Documents/Codex/worktree-recovery/lumina-final-cleanup-20261028/`. Other worktrees/stashes belong to separate work. Recent focused evidence remains in ignored `output/` and retained XCResults; disposable build caches were removed.

## Only when the owner requests another build

Latest confirmed upload is **iOS 0.1.0 (31)** from clean source `c1111788aa6696eeca602873ed50f23a31ecf25c`, confirmed by Xcode Organizer on October 28. It contains the fixes above and all build-30 features. Preserve ignored `apple/Distribution/0.1.0-31/`: exact uploaded IPA, source snapshot/hashes, symbols, signed Organizer archive, validation and upload logs. Build 31 is consumed; Apple processing and tester assignment are unverified. tvOS remains deferred.

Use the [TestFlight procedure](../../apple/docs/testflight.md) for a newly requested release. Recheck build inventory; 32 is only a candidate. Archive from clean main with recorded provenance; validate identity, privacy, fixture exclusion, signatures and matching symbols. Direct Xcode build-setting overrides avoided unnecessary XcodeGen project churn for builds 30/31. Xcode's existing account can sign/upload when CLI export reports No Accounts. Verify the final version/build and requested audience in Xcode, preserve its actual upload confirmation, and record unresolved processing/tester availability honestly. Staging and uploading are separate actions; a successful prior upload does not authorize the next one.
