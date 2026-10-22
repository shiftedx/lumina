# iPhone Duo redesign notes
Journey: Books library → native reader → contents/appearance → resume.
Stack: SwiftUI + UIKit; iOS 18 deployment; Xcode 27.1 installed.
Stage: 5  Next: Qualify the visual library and device-specific defaults, then finish Device Hub poses.

## Findings
- Books has no native entry point or renderer; current reader is React. [HIG]
- Existing native shell uses NavigationSplitView, TabView and NavigationStack. [HIG]
- New reader must retain book/file ID, chapter/page and offset across resizing. [HIG]
- System bars and sheets adapt; custom centered controls need fold review. [HIG]
- Native credentials need a Books extension of the existing Connected-app boundary.
- Ready: shell size classes, landscape support, safe-area-managed containers.
- N/A: camera, hinge effects, scene accessories, multiwindow, table-pose extras.

## Decisions
- Books entry: recommended fifth native tab with a searchable paginated catalog.
- Reader: recommended full-screen native navigation, with system toolbar actions.
- Expanded layout: automatic spreads chosen by user (2026-10-21); outer active page stays physically right inside, including RTL.
- Prose: adaptive native pagination, Dynamic Type, text-size controls and native selection; retain content anchor across reflow.
- Contents/appearance: recommended standard sheets; avoid an additional permanent sidebar.
- Performance: recommended bounded encoded lookahead, off-main downsampling and ordered progress writes.
- Native rendering: recommended UIKit text/images within SwiftUI, using safe structured API nodes.

## Done / verified
- Repository and API assessment completed; native reader absent.
- Final iOS/tvOS builds, Core 78 tests and make check pass; final Duo LTR/RTL workflows pass (2 tests).
- PDF selected page remains right through open/closed, rotation and vertical fold; horizontal fold retains it below.
- Prose anchor remains in the right pane after reflow; largest Dynamic Type and outer keyboard checked.
- RTL canvas fixed and selected page verified physically right; native fill/pan and Fit swipe workflows pass.
- Edge-fold compact/expanded bounds fixed; final Core geometry/RTL/fold contracts pass.
- Actual Split View verified on both sides, including Next and fold/unfold continuity; rerun immersive build pending.
- User chose Fit on standard iPhone and Fill on Duo; guarded hinge capability sets the default; Appearance overrides it.
- Visual cover grid, real shelf/series stacks, shared bounded thumbnail cache and concurrent catalog metadata added.
- Library backend/Core/live-cover checks pass; native compile passes; latest UI workflows queued after full gate.
- External Safari PiP did not expose a control or floating window in this simulator; unverified.
