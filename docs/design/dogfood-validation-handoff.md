# Build 26 validation handoff

Use this when continuing the accumulated dogfood batch or investigating its physical-device results. Code is in [PR #169](https://github.com/shiftedx/lumina/pull/169). The release is iOS 0.1.0 (26); iPhone and iPad share the package.

## Resume

1. Inspect the local release manifest in ignored `apple/Distribution/0.1.0-26/`. It records the exact source revision, signing/validation, hashes and upload result. If it says upload succeeded, continue with processing/device validation rather than uploading 26 again. If the manifest is unavailable, inspect Xcode Organizer's submission status before retrying this number.
2. For packaging or signing, follow [the TestFlight workflow](../../apple/docs/testflight.md). Completion means distribution-signature validation, matching app/debug-symbol UUIDs, production source matching the merged code, and Xcode's confirmed upload result. Keep the source/signed archives and symbols. Browser login to App Store Connect is outside this task's authorization.
3. For physical validation, use the package's `What-to-Test.txt`. Reproduce closed→open and open→closed while playing and paused; exercise actual AirPlay/Cast receivers. Share on-device playback diagnostics if playback fails. A simulator chooser is not receiver playback qualification.
4. Record new failures by view, build, posture, action and observed result. Preserve the existing session ownership and reader-position contracts while fixing them.

## Included views

| View | Changes / retained behavior | Verification |
| --- | --- | --- |
| Home | Horizontal category gestures; full compact viewport artwork; shared gutter; compact missing-art fallback; bold art with only a minimal logo/nav corner scrim | Closed-Duo category and Home browse/search/watchlist workflows ; 15 web Home cases |
| Video/library | Shared native gutter and metadata gaps; library tab scrolling/focus retained | Native catalog/gutter workflow; web category/overflow workflows |
| Details | Full-page artwork, seasons below first viewport, branding removed from detail focus, neutral actions; shared gutters; compact missing-backdrop web field | Native detail/show captures; responsive web title and Play workflows |
| Playback | Durable root session across adaptive rebuilds; aligned bespoke SVG controls; AirPlay/Cast; 44px web player/mini-player targets | Direct/client-demux rebuild checks; native overlay; web keyboard/mini-player workflows |
| Music | AirPlay/Cast and vendor-neutral native output wording; shared spotlight gutter; web focus clearance | Audio catalog capture; web music workflows; shared SDK picker path |
| Books/reader | Shared page gutter, paired shelf headings/card gaps, narrow missing-cover title; plain chapter/page count | EPUB/PDF/CBZ/CBR page-turn/reopen restoration; web book/reader evidence |
| Requests | Shared native hero gutter; shared web overflow controls | Native discover→select→submit→history; Requests light/dark phone/desktop and Library overflow |
| Search/account | Real 44pt label targets for Clear/Retry; supported result layout retained | Native Home/account flows; web search/Requests evidence |
| Settings | Active category stays visible through resize while form/focus/location survive; existing hierarchy retained | Native search/save; web 14-area alignment review and keyboard/resize workflows |
| Connection/brand | Concise QR actions; mark/wordmark baseline aligned; onboarding keeps its distinct layout | Existing native setup work; web Mobile Connect at 320/1440px |
| Photos | Reviewed; existing media proportions and contained viewer controls retained | Web timeline/viewer workflows |

Local screenshots/measurements: ignored `output/visual-polish-2026-10-27/README.md`. Post-outage logs/results: ignored `output/dogfood-build-26/`. Earlier temporary completeness captures were lost in the restart; retained comparisons and new validation evidence are on disk. Measurement overlays are absent from app source.

## Cast integration

The initial dynamic package and intermediate packages were never uploaded. Preserve their `*-superseded` / `*-pre-*` directories as evidence; distribute only the current 26 package.

The corrected target uses GoogleCastStatic 4.8.6, matching vendor UI bundles and its privacy manifest. The archive preparer removes Xcode's unreferenced, zero-code/zero-symbol Cast wrapper only after proving its resources are duplicated byte-for-byte in the app. Unique resources and loaded/code-bearing frameworks remain intact. The archive validator rejects dynamic embedding and missing Cast resources.

Plain Lumina SwiftUI buttons explicitly open the official SDK picker; discovery starts on that user action. This avoids the hidden UIKit button/duplicate accessibility-container bridge. Session/ticket handoff ownership is retained.

When changing Cast SDK version or linkage, read [resource provenance](../../apple/Resources/GoogleCast/README.md), refresh matched resources/hashes, and run the archive contracts plus real picker workflow before distribution.

## Evidence

- Full frontend: 2,721 passed across 314 files; typecheck, production build and style passed. Final Requests/shared-scroller units 17/17; Requests/Library overflow 5/5; Home 15/15.
- Native direct and client-demux presentation-rebuild checks: 2/2 passed after restart, including player identity, live position, paused retention and Close→fresh Play.
- Native Requests completed in 143.133s. Search submission dismisses the keyboard before the test scrolls; the former full-viewport test drag began inside the keyboard.
- Native Cast smoke passed in 108.43s against `da6281d`: real SDK `No devices available` picker with network guidance, Cancel, then local playback transport. Result `native/cast-picker-r5.xcresult`; screenshots in `native/attachments-cast-picker-r5/` beneath the post-outage output directory.
- Native Account/Home/category checks passed. Standard-phone-only navigation skipped on the 466pt-wide Duo by its explicit device guard. Reader restoration, overlay and catalog checks passed before the final Home/Cast changes; those reader/ownership paths were retained.
- Package/resource, placeholder-retention, plist/hash and unsigned archive checks passed. Signed package and Apple upload status belong to the release manifest, avoiding duplicate state here.

## Limits / remaining validation

The complete repository gate is not green. Post-outage backend: 5,543 passed, 20 skipped, two existing concurrency tests failed; both passed a focused rerun. Pre-outage full browser: 620 passed, one previously reproduced standalone offline-reload failure, 33 dependency-blocked cases. Do not call those blocked cases qualified.

Physical Duo decoder/display continuity, actual Cast/AirPlay receivers, native iPad and fresh native light-reader appearance remain device validation. The captured SDK zero-device/network-help screen proves UI/resource loading and local return, not remote receiver discovery or playback. The IPA ships native iOS changes; web/backend changes in the same PR use their server deployment path.
