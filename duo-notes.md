# iPhone Duo validation notes
Journey: Home → Account appearance → selected feature → detail → Back, then Closed → Book → Open → Closed.
Stack: SwiftUI, iOS 18 minimum, Xcode/iOS 27.1 SDK; native Device Hub review.
Stage: 5 selected native navigation verified at the merge stopping point; live Duo opening remains unqualified because of Device Hub discovery.

## Current findings
- The old iPhone Account toolbar reserved 44 pt above Welcome home; the account action now lives in native navigation.
- iPhone and iPad share Home, Video, Audio, and Account destinations. Web and TV retain their top-right account control.
- The current detail pane uses a stable TabView with one NavigationStack per destination.
- The first adaptive-tab sidebar overlaid iPad Home. A balanced native NavigationSplitView now gives leading navigation and trailing content separate panes.
- No custom per-pose layouts, screen coordinates, hinge animation, or display-transition anchor API is introduced.

## Decisions — user-authorized PM pass
- Use a balanced native split view with a stable TabView in its detail pane; compact iPhone navigation remains native tabs, and expanded navigation is a sidebar.
- Keep selected destination, Home carousel, navigation history, and the root audio coordinator alive through resizing.
- Hide the unused navigation bar only on Home and Account; media destinations explicitly restore the native Back bar.
- Respect the system's asymmetric safe areas and default placement of Duo controls.
- Keep the Home artwork, Newsreader typography, and paper/ink/gold palette; reclaim space with tighter content spacing.

## Current evidence
- Final iPhone Home/carousel/video journey passed (136.025 s), including Details, Back, resumed playback and deliberate Account sign-out.
- Rendered iPhone Home starts 44 pt higher; actual captures are retained in the local home-navigation gallery.
- Final Account appearance/state checks passed on iPhone (102.733 s) and Duo Closed (101.026 s).
- Refined iPad navigation passed (102.525 s): separate trailing content, no duplicate tabs, Account appearance, selected feature and Back. Persistent audio passed on iPad (182.918 s) and iPhone (178.561 s), including Browse, destination changes, queue/repeat and Stop.
- Device Hub keeps a stale inventory even after safe UI creation/new-window attempts, blocking a genuine live Duo opening/closing check; no shared simulator services were restarted.

## Historical limits retained
- XCTest orientation requests do not reliably rotate Duo; use Device Hub and record the actual viewport.
- Physical hinge comfort, inner-camera avoidance, multitasking/PiP, RTL, Reduce Transparency and spoken VoiceOver still need device qualification.
- Format/output and Sonos hardware qualification remain separate from simulator navigation evidence.
- Never stop another app's simulator. Lumina tests and manual review use only one Lumina-owned simulator at a time.
