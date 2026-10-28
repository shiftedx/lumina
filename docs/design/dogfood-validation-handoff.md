# Dogfood validation handoff

Use this document when continuing the current polish batch or validating its next iOS TestFlight build. The batch includes the retained Duo playback fix and all subsequent dogfood commits; Books is one part of the scope.

## Current task

Qualify the accumulated changes, merge the complete dogfood branch upstream through a pull request, then package and upload iOS 0.1.0 (26) through Xcode. The owner explicitly authorized upstream merge, signing and upload after the final change; the subsequent power outage stopped running checks but retained the source edits. Build 25 is already uploaded; preserve it as the comparison build.

Completion requires a distribution-signed package that passes the repository archive validator, an exact clean-source revision in the release manifest, and Xcode's confirmed upload result. Upload confirmation does not establish TestFlight processing or physical-device validation.

## Resume in order

1. Inspect the working tree and the release manifest in ignored `apple/Distribution/0.1.0-26/`. Use its recorded source revision, package hashes and upload state. If upload succeeded, continue with processing/validation; reuse that upload rather than submitting build 26 twice.
2. Finish any checks marked pending below. Resolve regressions in changed behavior; retain separate evidence for unrelated baseline failures.
3. Follow [the TestFlight workflow](../../apple/docs/testflight.md) for archive, signing, validation and Xcode distribution. Keep both source and signed Organizer archives plus symbols. Use the existing signed-in team; signing credentials remain outside the repository.
4. After upload, update this state record and the local release manifest with observed results. Hand the owner a validation list covering each changed view and the folded-to-open playback transition.

## View coverage

| View family | Included changes or review | Evidence |
| --- | --- | --- |
| Home | Horizontal category gestures; full compact viewport artwork; shared gutter; compact missing-art fallback; final artwork without broad dark/bottom fades, only a minimal logo/navigation corner scrim | Native Home/detail/watchlist journey; 15 web Home workflows; final native render rerunning after the outage |
| Video/library | Shared native gutter; consistent metadata gaps; library tab scrolling and focus retained | Native catalog navigation/gutter workflow; web Library category/navigation workflows |
| Media details | Full-page artwork, seasons below the first viewport; detail branding removed; neutral primary actions; shared gutters; shorter web missing-backdrop color field | Native detail/show captures; web title Play and responsive workflows |
| Playback | Retained root video session across adaptive presentation rebuilds; bespoke SVG overlay, alignment, AirPlay and Cast controls; 44px web player and mini-player targets | Existing direct/client-demux rebuild regressions; native overlay journey; web keyboard/mini-player workflows |
| Music | Receiver controls and vendor-neutral wording; shared spotlight gutter; keyboard focus clearance above fixed web navigation | Native Audio catalog capture; web music workflows |
| Books/reader | Shared page gutter, paired shelf headings, consistent card gaps; narrow missing-cover title; plain chapter/page count | Native EPUB/PDF/CBZ/CBR turn/reopen restoration; web book workflow and reader captures |
| Requests | Shared native hero gutter; shared web overflow navigation pending final check | Native discover/select/submit journey pending; Requests phone/desktop × light/dark evidence |
| Search | Native clear action receives a real 44pt label target; result rows already contain full artwork/touch targets | Native search journey pending; web search/request evidence |
| Settings/account | Native Retry label target; web selected category stays visible across resize while preserving form/focus/location; existing form hierarchy retained | Native account journey pending; web 14-area alignment review, Settings keyboard/resize workflows |
| Connection/branding | Concise QR actions, corrected mark/wordmark baseline; onboarding retains its distinct layout | Existing native setup work; web Mobile Connect at 320/1440px |
| Photos | Reviewed; existing artwork proportions and contained phone/desktop viewer controls retained | Web Photos timeline/viewer workflows |

The source of each implemented change is its code/commit, rather than a duplicate specification here. Local before/after captures, overlays and detailed check records live in ignored `output/visual-polish-2026-10-27/README.md`; completeness evidence lives in `/tmp/lumina-completeness/` on this host. Temporary overlays are evidence only and are absent from app source.

## Qualification state

- Polish checkpoint: `3e27092`; final Home/navigation changes follow it.
- Four native Release UI workflows passed: Home/detail/watchlist, four-format reading-position restoration, video overlay, catalog gutters. Generic tvOS Release build passed before the final Home scrim adjustment.
- Backend: 5,545 passed, 20 skipped, six subtests passed. Frontend: broad run 2,720 passed with one intermittent existing live-recording pagination failure; focused recheck of that file plus style contracts passed all 38.
- Web typecheck, production build and style checks passed. Full browser run: 620 passed, one known standalone offline-reload failure, 33 dependency-blocked cases not run. That offline failure was also reproduced before this polish. The full gate is therefore not green.
- Extra completeness review: seven Requests/Photos, three Settings/Mobile Connect, four Library and one missing-cover workflow passed. Final Home web suite passed all 15 after removing its broad scrim.
- Final native Home/Search/Account/Requests run, final web Requests overflow check, signed archive validation and upload: pending.

## Validation boundaries

Keep playback session/engine ownership, reader preferences/position/fit/zoom, existing navigation semantics and bespoke SVG assets intact. Actual Cast/AirPlay receivers and the physical folded-to-open Duo transition require the owner's hardware validation. The Mac currently qualifies simulator behavior, not physical decoder/display continuity. Native closed-phone/iPad and light-mode reader appearance were not freshly captured in this pass. Dependency-blocked browser perf/WebKit cases remain unqualified by the broad run.

The IPA ships native iOS changes. Web/backend changes are retained on the same source branch and require their server deployment path; do not describe them as code embedded in the IPA.
