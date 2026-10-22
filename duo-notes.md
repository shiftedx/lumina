# iPhone Duo reader handoff
Journey: Books library → native reader → contents/appearance → resume.
Stack: SwiftUI + UIKit; iOS 18 deployment; Xcode 27.1 SDK.
Stage: Functional reader and visual library; UI redesign is a separate follow-up.

## Preserve during redesign
- Native Connected-app credentials use the same member visibility as the web Books service.
- The selected outer-screen page/content stays physically right when unfolded, including RTL.
- LTR companion precedes the selected page; RTL companion follows it. Horizontal fold puts selected content below.
- EPUB stores a UTF-16 content anchor through reflow; artwork stores its selected page.
- Standard iPhone defaults to Fit entire page; Duo defaults to Fill display. Explicit Appearance choice overrides.
- Layout follows safe areas, actual size classes and reserved fold/camera regions, without device-name/angle branches.
- EPUB/Fit retain safe areas; immersive artwork fills the viewport. Native pan/zoom exposes cropped pixels.
- Progress writes are ordered/coalesced and failures offer retry. Each file retains its own position.
- Covers, shelf memberships and manga-series stacks use real service data and bounded shared thumbnails.
- Only visible pages are decoded, off the main actor; encoded lookahead is bounded and abandoned work cancelled.

## Evidence and limits
- Full make check passes; shared native Core 78 tests pass with no skips/failures.
- Four focused Books Core contracts pass, including failed-prefetch recovery.
- Full-to-bounded decoded bitmap storage: 96,000,000 → 25,165,824 bytes (73.8% smaller).
- Preparation/materialization median: 65.341 → 116.671 ms; no latency gain claimed.
- Actual Device Hub pose checks covered outer/inner, both rotations, both fold directions, keyboard, accessibility and both Split View sides.
- Pose checks preceded the final library/default/toolbar refinements; latest native workflow results are in the reader report.
- XCTest orientation does not resize Duo here; actual automated resizing uses standard iPhone.
- External Safari PiP was not exposed in this simulator and remains unverified.
- No tvOS reader was added; the existing tvOS app builds against shared Core changes.
- Details: apple/docs/native-book-reader-2026-10-21.md; local artifacts: output/native-book-reader/.
