# Native proof verification

Validation performed on 2026-10-17 using Xcode 27.1 (27A9269), Swift 6.4, and the checked-in project. Minimum supported deployment targets are iOS/iPadOS/tvOS 18; older OS versions have not yet been exercised.

## Completed checks

| Check | Result |
| --- | --- |
| Shared Swift package unit contracts | 8 passed; optional real-server test skips without its fixture |
| Real-server AVFoundation integration | Passed direct MP4 and compatible HLS playback, seeking to saved positions, and persisted progress |
| iPhone 18 Pro, iOS 27.0 simulator | Passed native sign-in, browse, direct playback with advancing time, persisted watch progress, resume, and compatible HLS playback |
| iPad Pro 11-inch (M5), iPadOS 27.0 simulator, landscape | Passed the same journey, including keyboard Go submission; captured direct and HLS video frames were visually inspected |
| iPhone Duo, iOS 27.1 simulator | Passed native sign-in, browse, direct playback, persisted progress, resume, and compatible HLS playback on the active display |
| Apple TV 4K (3rd generation), tvOS 27.0 simulator | Passed the same journey using Siri Remote focus/select/Menu, Play/Pause and resumed transport, including return to the fixed library header |
| Generic iOS and tvOS simulator builds | Passed |
| Generic iOS and tvOS device Release builds | Passed compilation without distribution signing |
| Clean tvOS UI-test build without disposable credentials | Passed `build-for-testing`; verified the test bundle contains no `Fixture.json` |
| `apple/scripts/check.sh` | Passed the shared package tests and both generic simulator builds |

The UI tests use the production Lumina backend, its opt-in Jellyfin-compatible endpoints, a temporary database, disposable member credentials, and generated H.264/AAC MP4 and MKV media. They use the app's actual sign-in fields and AVKit player. Each journey verifies both advancing playback time and the server's saved watch position. Direct playback is reopened to verify resume. The shared media integration additionally seeks and plays the HLS stream from a saved position.

Simulator runtime tests retain Xcode's local code signing so Keychain works. Disabling signing is suitable for compilation checks only. Test fixture files, generated media, credentials, build products, and result bundles are deliberately excluded from Git.

The successful UI journeys above precede the final player lifecycle and audio-category changes. An additional Apple TV Home/return check exposed disposal of the player on disappearance. The final implementation retains the player across backgrounding, pauses on `willResignActive`, suppresses delayed autoplay, and allows cancelled initialization to retry. Its final UI reruns stalled in XCTest accessibility requests before the Home scenario; a passing final Home/return regression could not be recorded. Both device Release configurations compile with these changes. Verify background pause, explicit resume, and audio on physical devices before relying on this build for everyday viewing.

## Duo layout audit, 2026-10-18

The Apple HIG audit repaired bounded poster/card layout, long-title wrapping, accessibility button bounds, accessibility-size detail stacking, explicit sign-in labels/content types, and prominent-button contrast. Custom colors now respond to Increase Contrast. iOS keeps the player open at completion and offers Done/Replay; Replay negotiates a fresh Direct/HLS session because stopping releases the previous conversion session. Queued playback reports retain their original request identity.

The converted-video completion/replay workflow passed on the closed Duo in landscape: resume near completion, retained player, fresh compatible session, reset/advancing time, exit, and persisted progress. Shared package contracts passed (9 tests), as did the real-server AVFoundation integration for authentication, direct/HLS playback, seeking, and persisted progress. The final shared-player source builds for iOS and tvOS simulators.

All six Device Hub layouts passed the native sign-in, library/detail bounds and reachability, playback, verified background pause, explicit resume, completion, Replay, and Done workflow. Upright and 90° rotation refer to physical device orientation; an unfolded window's aspect ratio differs from the outer display.

| Pose | Physical orientation | Main window (pt) | Result bundle under `/tmp/` |
| --- | --- | --- | --- |
| Closed | Upright | 466 × 678 | `lumina-duo-closed-portrait-final.xcresult` |
| Closed | 90° rotation | 678 × 466 | `lumina-duo-closed-landscape-final.xcresult` |
| Book | Upright | 951 × 669 | `lumina-duo-book-portrait-content.xcresult` |
| Book | 90° rotation | 669 × 951 | `lumina-duo-book-landscape-final.xcresult` |
| Open | Upright | 951 × 669 | `lumina-duo-open-portrait-final.xcresult` |
| Open | 90° rotation | 669 × 951 | `lumina-duo-open-landscape-final.xcresult` |

Closed upright additionally passed both workflows at the largest accessibility Dynamic Type size with Dark Mode and Increase Contrast enabled (`lumina-duo-ax5-dark-contrast.xcresult`). Player buttons and hittable detail actions were checked for full vertical visibility. HLS replay required fresh saved progress strictly above 2 and below 20 seconds, so the old seeded 84-second position could not satisfy the check. The simulator's normal text size, light appearance and standard contrast were restored afterward.

After GUI access was restored, a single direct-play session continued through Open → Closed → Book → Closed → Open, rotation, then Book → Closed. The synthetic video time advanced from 15.208 seconds through 31.208, 43.792, 48.125, 56.667 and beyond 67 seconds without restarting or dismissing playback. Done returned to the same media detail; restoring upright orientation retained the title and Resume action. This live check supplements the six fixed-layout workflow runs. Device Hub was returned to the production validation simulator, where the installed app retained its authenticated library session; keyboard capture was disabled.

Device Hub is required for pose/orientation selection on this runtime: XCTest reported a requested orientation while the physical Duo display stayed unchanged. Tests use the main window's coordinate space because `XCUIApplication.frame` can be transposed after a pose change. Scroll gestures anchor to the visible content column rather than the fold or sidebar. Some XCTest screenshot attachments contain inconsistent orientation/capture metadata; Device Hub observations are required for visual conclusions.

The complete `mise exec -- make check` invocation exited successfully: 5,011 backend tests (11 skipped), 2,478 frontend unit tests, 47 real-stack/performance tests (4 skipped), and 592 mocked browser tests (2 skipped), plus bootstrap, typecheck, build, size and style gates.

## PR merge validation, 2026-10-18

After rebasing the native fixes onto the newer main branch, `mise exec -- make check` passed bootstrap, typecheck, build/size/style and 5,119 backend tests (11 skipped), then exposed an invalid AppShell activity mock. A separate one-line test fix uses the valid loading state. The full frontend suite passed all 2,547 tests with two workers; two default-concurrency timeouts passed without changing their assertions or timeouts.

The real-stack/performance gate passed 46 cases (4 skipped) and exposed an existing Diagnostics bookmark regression after Updates became the section's first page. A minimal default-page fix retains Diagnostics bookmarks and explicit Updates navigation, covered by 46 focused navigation/route tests. The unchanged recommendation/Diagnostics journey then passed with fixture setup (2 tests). The rebuilt frontend passed size/style checks and all 605 mocked browser tests (2 skipped). This is combined coverage of all gate targets after the two small web repairs, rather than a second single successful `make check` invocation. The native app sources are unchanged from the six-layout and live-transition validation above.

## Repository regression checks

Running `make check` exposed two existing regressions, fixed separately in [worker shutdown #66](https://github.com/shiftedx/lumina/pull/66) and [watch-tool selection #68](https://github.com/shiftedx/lumina/pull/68). The full backend suite passed 5,011 tests with 11 skips. After the web fix, the remaining check targets passed bootstrap, typecheck, production build, 2,478 frontend tests, and 47 real-stack/performance tests with 4 skips.

The complete mocked-browser run passed 582 tests with 2 skips; six screenshot/artifact writes failed when the shared Mac ran out of disk space, and four catalogue sweeps hit development-server navigation timeouts. After cleanup, all ten affected cases passed across focused reruns: two library-channel cases, two accessibility route sweeps, two resource/activity cases, and all four catalogue sweeps against the same compiled production bundle. The catalogue assertions and timeouts were unchanged. That earlier run provided combined coverage; the 2026-10-18 audit above subsequently recorded one complete passing `make check` invocation.

## Cinematic native library, 2026-10-19

The iOS and tvOS library and detail surfaces now share an adaptive artwork stage, selected-poster strip, and artwork-derived ambient color. iOS removes the redundant large page header and single-section sidebar, uses native navigation/search, and fits three posters across the normal phone width. A short pull reveals search; a deeper pull refreshes. Both actions commit on release, keeping the artwork layout stable during the gesture. Sign-out is inside Account. tvOS keeps system safe areas, tightens the featured composition, uses native card focus, and moves refresh/sign-out inside Account. Poster and landscape sources stay bounded by their card frames. The library requests another page near the end of the visible catalog, retaining the manual retry action.

`apple/scripts/check.sh` passed 10 shared package contracts, skipped the optional real-server test without its fixture environment, and built both generic simulators. The contracts cover authenticated same-origin backdrop URLs, artwork cache tags, catalog pagination, and deduplicated featured titles. The real-server iPhone journey passed direct playback, persisted progress, resume, and compatible HLS playback (`lumina-native-gestures-ios-reveal.xcresult`, playback case). The featured journey passed first-screen catalog visibility, thumbnail selection, selection retained through portrait/landscape rotation, Details navigation, resumed video, and persisted progress (`lumina-native-cinema-ios-bounds.xcresult`). The final iOS discovery workflow passed hidden initial search, a short pull to reveal it, a real filtered query, Cancel, and a deeper pull that reloaded a changed watch position from the server (`lumina-native-gestures-ios-pulls.xcresult`). Account sign-out was exercised during test setup.

The final tvOS featured journey passed first-screen catalog visibility, Siri Remote thumbnail selection, Details, resumed playback with advancing time, persisted progress, and sign-out through the native Account dialog (`lumina-native-account-tv-dialog.xcresult`). The TV uses a system dialog for its account actions. XCTest can expose anonymous focus wrappers with subpoint differences from their labelled action bounds; the test helper recognizes that focused wrapper while still activating with the real remote.

Visual captures use the disposable synthetic catalog and Lumina-owned onboarding illustrations in its temporary artwork cache. No household media or credentials are included. Artwork reflection uses the selected image; this pass does not implement hinge/light sensor effects or video-frame reflections. The six Duo pose checks recorded above precede this library redesign; this pass verifies iPhone rotation rather than repeating that pose matrix.

The broader tvOS Home/return regression did not pass: playback advanced after reactivation without an explicit Play command. The player lifecycle implementation is unchanged in this UI pass; background pause on the current TV runtime remains unqualified (`lumina-native-cinema-tv-final.xcresult`).

`mise exec -- make check` passed the initial build/typecheck/style gates, then stopped with 8 backend failures, 5,210 passes and 11 skips. Failures concern episode still generation, the bundled hero analyzer, idle activity, the ArtworkProgress TypeScript contract, and recommendation reason grammar. Backend and web sources are unchanged in this pass; the remaining repository targets did not run after that failure.

## TestFlight packaging, 2026-10-19

iOS/iPadOS and tvOS share the registered `ai.cozylabs.lumina` identity and the **Lumina: Household Cinema** App Store Connect record. Version 0.1.0 (1) passed Release device archives, automatic distribution signing, and upload for both platforms. Apple processed both builds; both show **Testing** in the private Device Testing group with one owner tester invited. Platform icons and TV top shelf branding use the existing Lumina sunrise glyph.

The user additionally requested an external tester. Version 0.1.0 (2) passed unsigned Release device archives, signed IPA exports, and standard App Store Connect uploads for both platforms with the internal-only export flag disabled. Both candidates were submitted for Beta App Review and show **Waiting for Review** in the private External Device Testing group, which contains the requested email tester. Automatic tester notification is enabled after approval. Review contact details are saved; review notes use the owner's companion-server model: reviewers install their own server and create their own member account, with no hosted instance or developer-managed credentials supplied. The reviewer sign-in-required checkbox is disabled as requested.

`scripts/validate_archive.py` verified bundle ID/version/build, physical-device platform, icon/top shelf metadata, compiled artwork, privacy manifest, archive debug symbols, absence of disposable fixtures/test bundles, and code signatures. IPA checks additionally verified disabled debugging, the TestFlight entitlement, and matching signing identity. Final `scripts/testflight.sh export` completed successfully for both platforms and both build numbers. The script defaults to internal distribution and takes an explicit external-audience option. All 16 asset catalog JSON files referenced existing resources; plist lint, shell syntax, and whitespace checks passed. Shared package tests passed 10 contracts, with the optional server integration test skipped without a fixture.

The current `mise exec -- make check` rerun passed bootstrap, frontend typecheck/build/size/style gates, then stopped at the same 8 backend failures: 5,210 passed, 11 skipped, 49 warnings, 890.20 seconds. The failures concern episode stills (4), bundled hero analysis, idle activity, the ArtworkProgress TypeScript contract, and recommendation reason grammar. No backend or web source was modified in this packaging pass; later gate targets did not run.

Archives and signed IPAs remain in ignored `apple/Distribution/0.1.0-1/` and `apple/Distribution/0.1.0-2/`. Packaging/upload logs are retained locally under `/tmp/lumina-testflight-*.log`. This is distribution evidence, not a new physical-device playback qualification or an App Review approval.

## Limits of this evidence

The simulator does not establish HDR10 or Dolby Vision output, HDMI Match Content behavior, frame-rate switching, surround sound, Atmos, remote-network reliability, or App Store readiness. Use the [physical device checklist](device-validation.md) to record those results. The proof does not claim Infuse format parity.

The tests cover generated SDR H.264/AAC sources. HEVC/HDR, additional audio codecs, subtitle delivery, large libraries, oldest supported OS releases and iPad multitasking need separate qualification. The 2026-10-18 Duo audit above adds display-transition and selected accessibility-setting coverage; spoken VoiceOver, RTL and Reduce Transparency remain untested.

Latest passing result bundles were retained locally outside the source tree under `/tmp/lumina-native-proof-validation`, with validation logs in `/tmp/lumina-native-*.log`; these are temporary evidence, not release artifacts. Reproduce the native checks using [the fixture-server instructions](../scripts/README.md).


## Home, Video, and Audio workflow coverage, 2026-10-20

The native clients open on Home, with separate Video and Audio destinations. Home uses a cinematic artwork hero with an eight-second idle rotation, manual previous/next and title selection, horizontal swiping on iOS, and an explicit Pause control. Automatic rotation stops for Reduce Motion, VoiceOver, an inactive scene, or TV focus interaction. Continue watching, Next up, and recent-video shelves use the authenticated server catalog. The recent shelf can contain movies or episodes; its first visible card is not a fixed fixture item. Authenticated artwork requests support inherited series backdrops while preserving own-art precedence.

Video's All catalog groups shows as Series alongside movies and other video rather than flattening every episode into the first screen. Shows and Anime have separate browse scopes; show → season → episode navigation leads to the video detail and player. Search, sorting, genre/year filters, Unwatched/Favorites filters, and watched/favorite detail actions use the production API and member state. The activity workflow verifies server user data, native switch state, visible filter summaries, and filtered catalog membership, then resets its disposable state. TV category/filter menus are readable native controls, and Account remains available while the catalog is busy.

Audio browses albums, artists, and tracks. A session-owned player retains music when Browse dismisses the full player: the now-playing bar remains available while navigating Video and Audio, and reopening it keeps the same playback position. Stop explicitly ends the session and removes the bar. iOS background audio, Siri Remote Play/Pause on TV, ordered album playback, queue inspection and track selection, Next/Previous, and repeat off/queue/track controls have passing selected workflow coverage below. Browse and Stop share an aligned action row; the TV queue modal is bounded at 1,000 × 650 points with an accessible Done action.

Video uses the player pause policy on backgrounding and stays paused after foregrounding until the user explicitly resumes. Its native AVKit transport owns TV remote handling; the custom Play/Pause handler applies only to audio. This prevents competing video transport actions while keeping music available during browsing and backgrounding.

Audio uses authenticated direct delivery when supported and a bounded, cached server conversion to FLAC for compatibility playback. The shared response MIME resolver serves AAC M4A audio as `audio/mp4`, correcting the `audio/mp4a-latm` response that prevented native asset playback. The fixture checks the exact M4A response type and authenticated stream bytes. Its generated media includes three movies, a show/season/two-episode hierarchy copied from the original synthetic video, and two numbered AAC tracks with album/artist artwork. Public fixture IDs use Jellyfin's 32-hex UUID form; credentials and generated media stay outside version control.

### Shared, server, and repository checks

| Check | Verified result |
| --- | --- |
| Shared Core unit contracts | 14 passed, including inherited series backdrop URLs and own-art precedence |
| Real-server AVFoundation integration | All 3 `NativeServerTests` passed: direct audio, compatible FLAC audio, and direct/HLS video with progress |
| Final generic simulator builds | iOS and tvOS succeeded; `/tmp/lumina-parity-ios-final-build.log` and `/tmp/lumina-parity-tv-final-build.log` |
| Full backend suite | 5,227 passed, 11 skipped |
| Full frontend unit suite | 2,622 passed |
| Final web build, types, and style gates | Passed; strict CSS size 70.5 KiB |
| Retained read-only checks | 20 passed |
| Real-stack checks | Original run: 50 passed, 5 skipped, 2 failed; repaired focused rerun: 4 passed |

These are separate verified runs and retained focused repairs, not one wholly green final `make check` invocation. In particular, the focused real-stack repair result does not turn the original full run into a passing full rerun.

**Final mocked browser checks:** all 659 collected cases across 100 files have retained passing or skipped outcomes across the file batches and focused repairs: 657 passed, 2 skipped. The first batch retained 333 cases (332 passed, 1 skipped). The remaining 58 files ran 326 cases (312 passed, 13 failed, 1 skipped); all 13 failures were covered by the final seven-file rerun, which passed all 120 cases in 10.7 minutes with two workers. The final log is `/tmp/lumina-parity-browser-final-repairs.log`. These are combined coverage results, not a single green full-suite invocation.

### Final UI workflow results

All 11 selected native UI workflows passed across six iOS cases and five TV cases. They use the real disposable server, native sign-in and transport controls, advancing playback clocks, and independent server-state/progress checks. The Home case includes the cinematic hero and shelves; iOS also proves idle rotation, Pause, and a real horizontal swipe. Audio covers persistent browsing/reopening, background or remote transport behavior, queue selection, repeat, and explicit Stop. The table records each retained passing case, since a result bundle may also contain older failures in other cases.

All result-bundle paths below are under `/tmp/`.

| Platform | Passing workflow | Seconds | Result bundle |
| --- | --- | --- | --- |
| iOS | Home/carousel, destinations, featured detail and resumed playback | 146.401 | `lumina-parity-ios-qualified.xcresult` |
| iOS | Direct/HLS video, resume and persisted progress | 138.191 | `lumina-parity-ios-qualified.xcresult` |
| iOS | Persistent audio, background playback, queue/repeat and Stop | 174.337 | `lumina-parity-ios-final-controls.xcresult` |
| iOS | Watched/favorite actions and activity filters | 156.728 | `lumina-parity-ios-final-controls.xcresult` |
| iOS | Show → season → episode and playback progress | 79.355 | `lumina-parity-ios-complete.xcresult` |
| iOS | Pull/search and refresh | 64.605 | `lumina-parity-ios-video-search-final.xcresult` |
| tvOS | Home, destinations, featured selection/detail and resumed playback | 167.715 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Direct/HLS video, remote transport, background pause and progress | 249.683 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Show → season → episode and playback progress | 95.916 | `lumina-parity-tv-qualified.xcresult` |
| tvOS | Persistent audio, Siri Remote pause/resume, queue/repeat and Stop | 217.234 | `lumina-parity-tv-final-controls.xcresult` |
| tvOS | Watched/favorite actions and activity filters | 259.249 | `lumina-parity-tv-final-controls.xcresult` |

The older failed video case in `lumina-parity-ios-video-search-final.xcresult` is superseded by the passing iOS qualified video case. Older Audio/Filters failures in `lumina-parity-tv-qualified.xcresult` are superseded by the fully passing TV final-controls bundle. Earlier diagnostic failures remain historical evidence, rather than being counted as additional passing cases. The historical chapters above retain their original scope; the selected results here qualify the Home/Video/Audio snapshot tested in those bundles, not the later extended-audio changes, every native feature or physical device. Reproduction commands and screenshot behavior are in [the fixture README](../scripts/README.md).

All test simulators were shut down after the runs. After confirming that no devices were booted and no native test runners remained, residual orphaned simulator-runtime helpers were also terminated to release host resources. The disposable native media fixture was stopped and its generated credentials file removed. This pass performed no distribution upload or deployment.

### Historical limits before the extended-audio implementation

The following paragraph records the earlier Home/Video/Audio qualification boundary. Its music-feature exclusions are superseded by the implementation update below; its hardware qualification limits still apply.

The native audio queue is in-memory. Navigation and backgrounding preserve it within the current app process, but process termination does not restore the queue, and the native client does not synchronize the member-owned listening queue across devices. Saved/smart playlists and mixes, lyrics, loudness normalization, crossfade, sleep timer, offline downloads, and Sonos output remain web-only. Queue playback does not establish gapless transitions. Direct and FLAC fixture results do not qualify every hardware codec, file type, HDR/HDMI mode, surround/Atmos output, or network condition. Physical-device playback and accessibility checks remain necessary; this is not a claim of complete web-feature parity.


### Extended native audio implementation, 2026-10-20

The implementation now includes saved/smart playlist CRUD, read-only household-shared playlists, and server mixes. Listening queues are member-owned, revision-checked server sessions with explicit conflict resolution and owner-scoped checkpoints; restoration is paused and never automatically begins audible playback. This is synchronization between one member's devices, not a multi-member listening party.

Offline storage downloads sequentially to disk with cancel/retry/remove/clear and explicit sign-out cleanup. Its default owner budget is 1 GiB; each audio file is capped at 128 MiB and artwork at 2 MiB. Metadata manifests exclude credentials and stream URLs. Saved native bytes receive a local playability probe, then WAV fallback if needed; failed authorization never triggers that fallback. Retained sessions can use local audio and cached artwork/lyrics without network access. Apple TV uses purgeable Caches; absence of a cached file is recoverable, not evidence that a download remains available.

Lyrics expose source attribution and timed-line seeking. Manual gain, sequential-album gain or track normalization use peak limiting on the buffered path. Bounded current/next-track 48 kHz stereo PCM schedules transitions on the native audio clock, with crossfade Off/3/6/12 seconds. Unsupported or oversized buffering falls back to source-loudness streaming and labels that limitation. The sleep deadline persists per owner; system interruption/headphone removal pauses playback. Sonos handoff requires acknowledgement, polls output status, supports transport and debounced volume, and requires acknowledged return to local output; local audio effects and sleep controls are disabled while Sonos owns playback.

The enriched lawful fixture adds timed lyrics, gain metadata and a read-only shared playlist. Two additional public UI workflows are enabled on both iOS and tvOS: `testMusicPlaylistsSmartSelectionAndDownloadedPlayback` and `testMusicAdvancedControlsPausedRestoreAndQueueConflict`. Earlier eleven-case results above remain evidence for their original snapshot, not qualification of these additions.

| Extended-audio gate | Current evidence |
| --- | --- |
| Backend full suite | 5,233 passed, 11 skipped |
| Focused native API contracts | 35 passed, then 6 passed in the subsequent focused run |
| Offline Core contracts | 5 passed: disk persistence, budgets, cancel/owner switch, credentials and redirects |
| Music library Core contracts | 5 passed |
| Final Core suite with live fixture | 35 passed, including all 3 live `NativeServerTests` and Episode/Season inherited series-artwork fallback; `/tmp/lumina-audio-parity-core-art-final.log` |
| Real native transport and playback policy | 5 engine + 3 policy cases passed (8 focused cases); `/tmp/lumina-native-music-engine-final.log` |
| Frontend unit suite | Final full run: 2,622 passed across 299 files; the earlier recording-control async fixture repair passed all 14 focused cases |
| Bootstrap, typecheck, web build/style gates | Passed in the final qualification invocation |
| Final real-stack cohort | 51 passed, 5 skipped, 1 Home artwork baseline failure; focused baseline repair: 2/2 passed |
| Final mocked-browser cohort | 656 passed, 2 skipped, 1 catalogue interaction failure; focused dark-desktop Undo repair: 1 passed |
| Final repository qualification | Full cohorts plus retained focused repairs, as described below; **not one green `make check` invocation** |
| Local signed iOS/tvOS test builds | Runtime test builds compiled and ran on both platforms; no distribution archive or upload |
| Current native UI qualification | All eight selected journeys have passing case-level evidence below |
| Responsive Newsreader/layout visual acceptance | Current iOS and TV captures reviewed and accepted; TV album uses the retained accepted capture |


The eight selected native journeys below distinguish current passes from retained case-level evidence. A passing case does not make every other case in its result bundle pass. Older failed or interrupted cases are superseded only by the corresponding later passing journey. Paths below are under `/tmp/`.

| Platform | Journey | Seconds | Qualification/result bundle |
| --- | --- | --- | --- |
| tvOS | Home/featured | 193.410 | Passed; `lumina-audio-parity-tv-final-design.xcresult` |
| tvOS | Audio/remote transport | 211.004 | Retained pass; `lumina-audio-parity-tv-art-ui.xcresult` |
| tvOS | Advanced listening/restore/conflict | 284.025 | Retained pass; `lumina-audio-parity-tv-extended-qualified.xcresult` |
| tvOS | Playlist/download | 229.279 | Passed; `lumina-audio-parity-tv-final-design.xcresult` |
| iOS | Home/featured | 142.445 | Passed; `lumina-audio-parity-ios-final-design.xcresult` |
| iOS | Audio/background playback | 183.975 | Retained pass; `lumina-audio-parity-ios-final-ui.xcresult` |
| iOS | Advanced listening/restore/conflict | 144.629 | Passed; `lumina-audio-parity-ios-advanced-final-design.xcresult` |
| iOS | Playlist/download | 147.760 | Passed; `lumina-audio-parity-ios-final-design.xcresult` |

The TV final-design bundle passed both selected cases. The iOS final-design bundle retained passing Home and Playlist cases; its Advanced menu lookup failed, then passed as the sole case in the focused advanced-final-design bundle after an XCTest-only exact-button lookup repair. No product change occurred for that focused rerun. The retained TV Audio and Advanced cases likewise qualify their individual journeys, not their entire containing bundles.

The latest source removes duplicate Home/Video/Audio root navigation titles on iOS, leaves generic Album/Artist navigation titles blank, and removes redundant TV Video/Audio body headings while retaining actual destination labels. An opaque pinned iOS audio toolbar prevents scrolling content from showing through controls. Crossfade has an explicit caption on both platforms. Playlist creation keeps its queue snapshot in an item-based sheet; track state initializes before metadata awaits, with native editor Save/Cancel toolbar actions. `NativeMusicList` retains inset-grouped lists on iOS and uses a paper/ink `ScrollView`/`LazyVStack` with native focusable controls on tvOS, where `scrollContentBackground` is unavailable. Signed local runtime compilation is confirmed on both platforms; this does not establish a distribution archive or upload.

Local artwork evidence is collected in [the review gallery](/Users/kmcdowell/.codex/visualizations/2026/10/20/01a15766-1f7c-7d21-b521-0604bea3306b/native-parity/extended-audio/review.md). The final iOS Home, Audio, Video, smart-playlist, downloaded-player and focused Advanced-options captures have been personally reviewed and accepted: duplicate root titles are absent, paper/ink surfaces are consistent, the opaque toolbar has no content overlap, and Crossfade is labeled with one active playback channel. Current TV Home, Audio, Video, smart-playlist and player captures are accepted; TV Album acceptance uses the retained earlier capture, not a fresh album image from the final-design bundle. Current iPhone Home/Audio gallery images are present. This closes the selected simulator workflow and visual acceptance scope; physical hardware, codec and Sonos limits below remain. No distribution archive, upload or release was requested or performed by this pass.


The final `make check` invocation in `/tmp/lumina-audio-parity-make-check-qualified.log` passed backend, frontend unit, bootstrap, typecheck and web build/style gates, then failed the Home image performance test's first-viewport baseline. The real-stack cohort retained 51 passing cases and 5 skips. The repaired fixture explicitly disables browser scroll restoration and returns to the top after real scroll/focus checks; it preserves every artwork budget. The focused rerun in `/tmp/lumina-audio-parity-home-images-qualified.log` passed setup and the performance case (2/2). All six cold/warm phases decoded 8 offscreen images against a budget of 10, with concurrency 4 and no image failures or duplicate concurrent/body transfers.

The subsequent full mocked-browser run in `/tmp/lumina-audio-parity-mocked.log` retained 656 passes and 2 skips, with one failure opening the feedback menu for the final dark-desktop catalogue Undo screenshot. A test-only interaction repair hovers the populated card and verifies its expanded, controlled menu before selecting feedback; the Undo assertion remains. The focused `VISUAL_ONLY=home-undo-row` dark-desktop case passed in `/tmp/lumina-audio-parity-catalogue-undo-qualified.log`. These are composed qualification results from full cohorts and focused repairs; neither failed full invocation is reported as a single wholly green command. The selected native UI and visual scope is qualified separately in the case table above.

No new distribution upload or deployment is established by this update. Buffered-engine checks and synthetic media do not establish all codecs, lossless/bit-perfect output, arbitrary gapless/crossfade cases, Sonos hardware behavior, HDR/HDMI/Atmos, accessibility on physical devices, or complete web-feature parity. Retain hardware acceptance checks and record actual output rather than inferring it from a source label.

### Native Account navigation and Home spacing, 2026-10-20

Account is a native navigation destination on iPhone and iPad. Web and Apple TV keep their existing top-right account control. Home no longer reserves an empty navigation toolbar above Welcome home, reclaiming its 44-point strip; its content spacing is also tighter. Account retains native appearance selection and explicit Sign out. Root Home and Account suppress redundant navigation titles; pushed media details restore the system Back bar.

A balanced `NavigationSplitView` supplies separate leading navigation and trailing content at expanded widths. The detail pane retains one `TabView` with stable destination stacks; compact layouts expose native tabs. Expanded panes hide duplicate tabs while the sidebar is present and restore them when the sidebar is hidden. Hero, collection spotlight and album layouts use their actual available content width, including Dynamic Type scaling, instead of assuming that a regular window always provides a wide detail pane. The shared audio coordinator remains outside destination selection.

The iPad audio regression caught during this change was caused by placing the persistent bar inside the detail navigation host: after Browse returned to a pushed track detail, the bar was absent. Expanded layouts now host the bar on the outer library shell; compact layouts attach it to the root and pushed screen content inside the native tabs so its hit area does not overlap destination controls. Only the visible destination exposes the compact bar, and every host shares the same session-owned player. The existing public audio journey verifies background progress, Browse, destination switching, reopening without resetting position, album queue selection/repeat, explicit Stop and independently persisted server progress. The two earlier failing iPad audio bundles are retained as diagnostic evidence and superseded by the passing focused case below.

| Platform | Current selected journey | Result / bundle under `/tmp/` |
| --- | --- | --- |
| iPhone | Account, native appearance values, selected feature, detail and Back; compact Home heading position | Passed, 102.733 s; `lumina-home-nav-iphone-final.xcresult` |
| iPhone | Home hero idle/manual/swipe, destinations, featured detail and resumed video | Passed, 136.025 s; `lumina-home-nav-iphone-final.xcresult` |
| Duo Closed | Account appearance/state and Home/detail/Back | Passed, 101.026 s; `lumina-home-nav-duo-final.xcresult` |
| iPad | Separate trailing content, no duplicate navigation tabs, Account appearance/state and detail/Back | Passed, 102.525 s; `lumina-home-account-ipad-qualified.xcresult` (Account case only) |
| iPad | Persistent audio/background/queue/repeat/Stop after the outer-shell bar fix | Passed, 182.918 s; `lumina-home-nav-ipad-audio-shell.xcresult` |
| iPhone | Persistent audio/background/queue/repeat/Stop with compact insets on root and pushed screens | Passed, 178.561 s; `lumina-home-nav-iphone-audio-screens.xcresult` |

The first three navigation bundles predate the final bar-host correction; the focused audio bundles qualify that correction separately. No full all-feature native rerun is claimed. Original runtime captures and their provenance are in the local [Home navigation gallery](/Users/kmcdowell/.codex/visualizations/2026/10/20/01a15766-1f7c-7d21-b521-0604bea3306b/native-parity/home-navigation/review.md).

Duo Closed is qualified only for its observed viewport. Genuine Closed → Book → Open → Closed motion remains unqualified: Device Hub did not discover the current review devices even after bounded safe UI attempts. No custom screen pose, hinge animation, placement coordinates, or simulated fold screenshot was substituted. Other apps' simulators and shared simulator services were left intact. Every Lumina runtime check used the machine-wide performance lock and at most one Lumina-owned simulator, shut down after its run.

This is a source merge. It does not bump the version/build number or create a TestFlight archive, upload or release. Small UI changes do not trigger distribution; a future distribution candidate requires an explicit request.

The latest required repository invocation, `VITEST_MAX_WORKERS=2 mise exec -- make check E2E_WORKERS=2`, is retained in `/tmp/lumina-home-account-make-check.log`. Bootstrap (13), application/E2E typechecks, production build/license/style gates, backend (5,233 passed, 11 skipped) and frontend units (2,622 tests across 299 files) passed. Real-stack checks finished with 51 passed, 5 skipped and one watch-queue fixture timing failure; all 30 executed performance cases passed. The four-second test video had completed while queue ordering was being inspected, consuming an entry before the reload assertion. The repaired test pauses through editing/reload, then retains its actual autoplay assertion. Its focused rerun passed both setup and queue cases (2 passed; `/tmp/lumina-home-account-watch-repair.log`), and the changed E2E TypeScript check passed.

This is composed qualification, **not a wholly green `make check` invocation**. Make stopped at the original real-stack failure, before the mocked cohort. The previously recorded full mocked cohort (656 passed, 2 skipped, with its one failure covered by the passing focused catalogue repair) remains retained evidence: subsequent product changes are native-only; the latest web change is the queue-test pause repair. No redundant mocked cohort was started for this stopping-point merge. The five current real-stack skips are one unavailable Firefox executable and four `PERF_STRICT=1` reference-host-only cases.
