# iPhone Duo validation notes
Journey: Sign in → Video library and Continue Watching → Title detail → Player
Stack: SwiftUI, iOS 18 minimum, Xcode/iOS 27.1 SDK; Device Hub available.
Stage: 5 complete  Next: Retain release regressions and qualify physical-device media behavior.

## Findings
- Library: Change — loaded artwork and long titles exceed card bounds; reproduced in simulator.
- Library: Change — foreground must respect asymmetric side controls [HIG: designing-for-iphone-duo].
- Navigation: Ready — native NavigationSplitView/NavigationStack with labeled system toolbar items [HIG].
- Detail: Audit constrained artwork and text under resizing; maintain selection and playback state [HIG].
- Camera, multiwindow extras and custom hinge effects: N/A to the existing journey.

## Decisions
- Keep existing library/detail hierarchy and visual identity; fix sizing within proposed space (agent, user-authorized repair).
- Use native safe areas and adaptive layout; no display coordinates or custom per-pose layouts [HIG].
- Verify with synthetic long titles and artwork, then inspect the existing production session.

## Done / verified
- Production sign-in succeeded, as confirmed by the user.
- Misleading HTTP 404 explanation corrected; regression test and nine core tests passed.
- Native iOS build passed before layout fixes.
- Fresh Duo simulator boots and launches after shared simulator service recovery.
- Bounded artwork/card layout, clipped rail and safe player controls build successfully.
- Closed, book and open library observed with native navigation and contained content.
- Synthetic long-title/mixed-artwork regression fails against original UI (poster/card geometry).
- Accessibility categories use one grid column; rail width is capped by its scroll container.
- Increased-contrast palette, prominent-button contrast and large-text detail stacking repaired.
- Sign-in fields expose explicit labels and native URL/username/password content types.
- iOS EOF retains the player with Done/Replay; Replay negotiates a fresh Direct/HLS session.
- Completed player items are detached so native transport cannot restart a stopped stream.
- Independent playback review passed; request identity is retained by queued progress reports.
- XCTest orientation reports do not rotate this Duo; use Device Hub and record actual viewport dimensions.
- Native simulator journey passed: geometry, button roles, direct playback, resume, HLS conversion and progress.
- Standard, AX3 and AX5 library layouts inspected; normal text size restored.
- Portrait/landscape and closed/book/open library inspected; production session preserved.
- Backend: 5011 passed; frontend unit: 2478 passed; mocked browser: 592 passed, 2 skipped.
- Full make check passed on the expanded audit (47 real-stack/perf passed, 4 skipped).
- Closed/book/open in both physical orientations passed the full layout/background/resume/EOF/replay/Done journey.
- Converted HLS replay passed with a fresh session and persisted server progress.
- AX5 + dark + increased contrast passed the full journey and strict HLS replay progress/full-button visibility checks.
- Live folding/rotation retained direct playback (15s → 67s+) and returned to the same detail; production login retained.
- Physical hinge/camera, multitasking/PiP, RTL, Reduce Transparency and spoken VoiceOver not run.
