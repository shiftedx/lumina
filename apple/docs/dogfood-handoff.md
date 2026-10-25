# Lumina native dogfood handoff

Help the owner fix small iOS and tvOS issues reported during device testing. Apply and verify each requested fix, accumulate the changes, and stage another release only after the owner explicitly asks. No new dogfood defects have been supplied at this handoff.

## Start here

1. Read the applicable `AGENTS.md`, inspect the branch and working tree, and preserve existing work. The last packaged source is `dbdf1a58a29e25b831752f2a8af73d8724d3bcb3` on `main`, merged through PR #154. Check for newer commits before choosing a base.
2. Before changing appearance, read the [shared brand contract](brand-assets.md). It is the authority for colors and control roles; use its existing tokens and styles.
3. Take the owner's next report as the work queue. For each issue, identify platform, screen, trigger, and expected result from the report or screenshot. Ask only for information that blocks reproduction or a correct fix. Completion means the reported case works and adjacent navigation, focus, and playback behavior still work.

## Preserve these product decisions

- Artwork leads and reaches the bezel. Detail headers show Lumina branding; search Cancel stays clear of navigation.
- Ordinary controls are muted neutrals. Selected accents, progress, and enabled switches use the shared brand roles. The canonical warm accent is `#DBB688`; accessible text and focus follow the brand contract.
- tvOS has one centered category menu, quiet utility icons, and no Books. iOS retains Books. Home categories match the libraries, with Watchlist last; genre filtering belongs in Library.
- Music opens with large artwork and a bottom timeline. Queue is the top transport action, followed by Play/Pause, Repeat, and Stop. iOS artwork swipes change tracks; tvOS Left/Right changes tracks while artwork owns focus. Lyrics/options start collapsed. Preserve timeline seeking and remote focus/Back behavior.
- Keep the bounded, session-scoped artwork cache and prefetch behavior intact. Confirm account changes cannot retain another member's artwork.

## Work and verification

Use the native UI skills appropriate to the change. Reuse the existing public workflow coverage in `apple/Tests/UITests/NativeProofTests.swift`; add regression coverage for changed behavior at the workflow boundary. Inspect actual screenshots for visual changes, including relevant appearance modes and tvOS focus. Follow repository test requirements before handing back changes or opening a PR.

Likely entry points, relative to `apple/`:

| Area | Files |
| --- | --- |
| Shared appearance and actions | `App/Shared/LuminaTheme.swift`, `App/Shared/MediaPresentation.swift` |
| iOS browsing and navigation | `App/iOS/MobileRootView.swift` |
| tvOS browsing and navigation | `App/tvOS/TVRootView.swift` |
| Music browsing and controls | `App/Shared/NativeMusicLibraryView.swift`, `App/Shared/PlaybackScreen.swift`, `App/Shared/NativeAudioOptions.swift`, `App/tvOS/TVMusicDetailView.swift` |
| Artwork framing | `App/Shared/SubjectAwareArtwork.swift` |

Use subagents for independently bounded implementation or test packets when useful, following the current routing policy. Assign ownership and require changed-file and verification evidence. Keep final integration and acceptance review with the primary agent.

The packaged source passed the full repository gate: 5,365 backend tests with 11 platform skips, 2,681 frontend tests, and 655 browser checks. Both native builds and six focused public UI methods passed. These results describe that source, not future edits. Broader exploratory tvOS Requests and iOS Account harnesses hit focus failures; those cases are not claimed green. The supported tvOS Navigation workflow verifies Requests submission through the UI and server record. Physical codec/HDR/HDMI/audio qualification remains device work.

## Current distribution state

Last verified on 2026-10-24: **iOS and tvOS 0.1.0 (16)** are distribution-signed, locally validated, and staged in Xcode Organizer. This work uploaded neither build; the owner may distribute them manually afterward. Apple server validation was not performed.

The ignored package is `apple/Distribution/0.1.0-16/`. Its `ReleaseManifest.json`, `NativeSourceInputs.json`, `SHA256SUMS`, `README.txt`, and `What-to-Test.txt` are the release evidence. Both signed IPAs passed local validation, and the signed Organizer copies preserve matching executable/debug-symbol UUIDs. Select the entries labeled **Signed for Organizer**; retain the unsigned source archives for symbols.

## When the owner asks for the next build

Until that instruction arrives, finish fixes and verification without archiving, exporting, staging, or uploading another release. Prior authorization for build 16 does not authorize a later package.

After an explicit staging request:

1. Finish the agreed fixes and required checks. Resolve the intended clean source revision and record its provenance. Use the current [TestFlight procedure](testflight.md) and [release script](../scripts/testflight.sh).
2. Choose a fresh build number from current local and App Store Connect evidence. Build 17 is only a candidate after build 16; recheck before using it. Preserve existing packages and successful uploads.
3. Archive each requested platform, sign/export with the established internal-only configuration unless the owner requests another audience, and validate the IPAs, Organizer copies, privacy/artwork/fixture exclusions, and symbol UUIDs. Record source-input and package hashes.
4. Stage the signed Organizer copies and visibly verify platform, version, build, and signing team in Xcode. Completion means both requested signed entries are available for the owner to distribute, with truthful package records and test notes.

Command-line export previously reported **No Accounts**, while Xcode's existing account successfully used cloud-managed distribution signing. If this recurs, use Xcode's Custom → App Store Connect → Export flow, retain the build number, enable TestFlight internal testing only, and follow the existing signed Organizer preparation procedure. Use CUA for Xcode UI interactions. Credentials stay in Apple's UI and local signing tools.

Staging and uploading are separate actions. A staging request ends with local packages and Organizer entries; upload only when the owner explicitly requests upload. Report the exact version/build, platforms, source revision, validation results, package location, and upload status.

## QR-first authentication integration — 2026-10-25

The current `codex/ios-dogfood-followups` branch contains the native QR-first sign-in integration alongside the retained dogfood fixes. It incorporates the earlier `codex/mobile-connect` work without modifying that source worktree. Scanning is the default; Advanced reveals manual entry, and unavailable/denied camera access falls back to the same manual form. Personal codes expire after five minutes; owner-shared codes expire after fifteen minutes. Deep links use `lumina://pair`.

Server/web pairing is merged in PR #160 and production was independently verified at revision `bffe58d90f58c12f37753c6d14e527bea0c76c86`, schema 38, with healthy service and protected QR endpoints. The separate unmerged security batch was excluded at the owner's request. A consistent database snapshot and stopped rollback image are retained in private deployment storage; private paths and credentials are not recorded here.

Focused qualification passed: four native Core pairing tests, 36 backend checks, 21 frontend tests, frontend typecheck/build, the real-stack create/claim/authenticate/revoke workflow, and the iOS welcome/manual-fallback workflow. The final iOS test build passed. Physical camera/mail-client behavior still needs device qualification. No full suite, native archive, upload, build-number change, or native release was performed for this task.

## iOS video continuity — 2026-10-25

The owner confirmed the affected TestFlight build is 0.1.0 (18); the earlier build-17 lifecycle explanation does not establish the cause of this report. Added narrowly scoped recovery when AVPlayer reports an app-backgrounded pause while playback is still requested. Recovery resumes the same ready item only after the app is active and seeks/stream changes have settled. A weak item reference avoids retaining replaced assets; deliberate pause and close clear recovery intent. The normal iOS background policy remains automatic. Audio and tvOS behavior are unchanged.

Ordinary simulator rotation, Control Center, foreground return, and Duo Closed/Book/Open transitions did not naturally reproduce the physical report. A controlled real AVPlayer pause at the background boundary reproduced the stuck state (active, playback requested, engine paused). That regression now passes on the isolated Duo simulator, including deliberate pause remaining paused. Normal portrait/landscape continuity and explicit-pause rotation pass on iPhone 17. The normal Release simulator build passed, and its executable excludes the test fault-injection argument and diagnostic token. Focused result bundles are retained locally under `/tmp/lumina-video-recovery-final-20261025.xcresult` and `/tmp/lumina-video-rotation-final-20261025.xcresult`.

Physical Duo confirmation of the OS-generated orientation/posture handoff remains necessary in the next native build. No full suite, native archive, staging, upload, release, or build-number change was performed.

## Closed Duo Home categories — 2026-10-25

Compact Home headers anchor the category scroller beside the full wordmark and reclaim 40 points of trailing space. Compact overflowing menus use leading alignment so the first category is not clipped behind the logo; wider menus retain their centered presentation and existing selection behavior. Text sizes and touch targets are unchanged.

The focused closed-Duo workflow passed: three complete categories clear the wordmark and scroll edge, share the header baseline, and the third opens its matching library. The fixture has Movies, TV, and Music (no Anime); Music provides an equally wide third-label check. Screenshot inspected at 466×678 points. Result: `/tmp/lumina-category-width-r4-20261025.xcresult`. No full suite or native release was staged.

## Standard iPhone navigation — 2026-10-25

Standard iPhones hide Home and Library's top category rows and retain system bottom tabs for primary navigation. Home keeps Search and one Browse overflow; available library types and Watchlist remain reachable there. On Library the same category chooser uses the library glyph, distinct from its existing media-options overflow. Downloads move inside Home's Browse menu on standard phones to avoid another toolbar button during music playback. The size gate uses both full display dimensions so rotating an ordinary phone does not enable the category header. Duo keeps its category row; iPad keeps its unified header.

Focused standard-phone workflow verifies the hidden rows, clean header, Music access through Browse, and Account/Home navigation via bottom tabs. Its Home screenshot was inspected at 402×874 points. Final standard-phone result: `/tmp/lumina-standard-phone-nav-final-20261025.xcresult`. The closed-Duo three-category workflow also passed after adding the responsive gate (`/tmp/lumina-phone-duo-nav-20261025.xcresult`). No full suite, native release, or build staging was performed.
