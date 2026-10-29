# TestFlight: native iOS and tvOS

Source merges and small UI changes do not trigger TestFlight releases. Archive, export, and upload only when the owner explicitly requests another beta build. The distribution entries below record the 2026-10-19 release work; they are not a request to repeat it.

The two native targets share `ai.cozylabs.lumina` and one App Store Connect record, **Lumina: Household Cinema**. On devices, the display name is **Lumina**. The iOS build supports iPhone and iPad; tvOS has its own build under the same record. Deployment minimums remain iOS/tvOS 18.

Initial beta: version **0.1.0 (1)**, uploaded on 2026-10-19 for both platforms. Open [Lumina in App Store Connect](https://appstoreconnect.apple.com/apps/6824763750) and choose TestFlight. The private **Device Testing** group contains the owner's existing App Store Connect user. Signed IPAs and archives are retained locally in `apple/Distribution/0.1.0-1/`.

## Build the next beta

Use Xcode with an Apple Developer Program team signed in under Settings > Apple Accounts and access to Certificates, Identifiers & Profiles. No signing secrets are stored in this repository. Supply your team ID locally and choose a build number that has not already been uploaded for that version/platform.

From the repository root:

```sh
export LUMINA_DEVELOPMENT_TEAM='<team-id>'
export LUMINA_BUILD_NUMBER=2
mise exec -- apple/scripts/testflight.sh archive
mise exec -- apple/scripts/testflight.sh export
mise exec -- apple/scripts/testflight.sh upload
```

`LUMINA_VERSION` defaults to `0.1.0`. Output defaults to ignored `apple/Distribution/<version>-<build>/`; set `LUMINA_DISTRIBUTION_DIR` to an absolute path to override it. DerivedData stays in the temporary directory, outside the local package. `LUMINA_PLATFORMS=iOS` or `LUMINA_PLATFORMS=tvOS` limits a retry to one platform. Archive refuses to overwrite an existing archive; export/upload reuse the selected archive without rebuilding.

Archive creates unsigned Release device builds so an unregistered physical Apple TV is not a prerequisite. Before export, `scripts/prepare_archive.py` clears inherited framework signatures in the unsigned archive so Xcode creates signing requirements from the current bundle identifiers. A vendor requirement can otherwise name a different identifier even after a valid distribution re-sign. The helper refuses signed app archives. Export and upload use automatic App Store distribution signing from the supplied team; Xcode can use its managed signing service. The exported IPA validation requires a valid signature, disabled debugging, and the TestFlight entitlement. Archive validation checks identity, versions, device platform, icons/top shelf, privacy manifest, symbols, and absence of test fixtures/test bundles. Each embedded framework is also verified independently against its designated requirement; a valid parent-app resource seal alone does not establish that requirement. Run `mise exec -- python3 apple/scripts/test_archive_signing.py` for the synthetic rejected-requirement → unsigned preparation → corrected-signing regression. Keep both `.xcarchive` bundles for crash symbolication.

`Config/TestFlightExport.plist` marks these builds **TestFlight Internal Only**, preserves the chosen build number, and uploads symbols. It cannot be used for external beta testing or a public Store submission. Do not retry a successful upload with the same build number; check App Store Connect for processing first.

For an explicitly requested external beta, choose a new build number and set `LUMINA_TESTFLIGHT_AUDIENCE=external` for export/upload. This clears the internal-only export flag and allows Beta App Review. It does not submit the app publicly. The **External Device Testing** group uses email invitations without a public link. Its first eligible build on each platform needs Apple's beta approval and complete review contact information before external testers can install it. Review notes explain that reviewers run the companion server and create their own account; there is no developer-hosted instance, and the reviewer credential fields remain disabled. Build **0.1.0 (2)** is the first external candidate; build **0.1.0 (1)** remains the owner's internal beta.

Both external build 2 candidates were submitted on 2026-10-19 and show **Waiting for Review** in External Device Testing. **Automatically notify testers** was enabled for both submissions, so the email tester added at the owner's request will be notified after approval. The contact details and companion-server instructions are saved in App Store Connect.

The encryption declaration is `ITSAppUsesNonExemptEncryption = NO`: this client uses Apple's HTTPS/Keychain facilities and contains no custom encryption library. Reassess that declaration if dependencies or cryptographic behavior change.

## Install and report feedback

In App Store Connect > Lumina: Household Cinema > TestFlight, select **Device Testing**, add the processed iOS and tvOS builds, and use [the beta notes](testflight-what-to-test.txt) for What to Test. This group uses manual build assignment. Automatic distribution is a choice made when creating a group and cannot be changed afterward.

On iPhone/iPad, install Apple's TestFlight app, accept the invitation, and install Lumina. On Apple TV, install TestFlight and use the invitation's redemption code when prompted. Connect each app to your own Lumina server with a member account and the connected-app API enabled. The app does not bundle a server, media, or login.

Send TestFlight feedback with the device/OS, steps, screenshot, expected/actual result, and direct versus compatible playback. The known tvOS Home/return playback issue is included in the beta notes. See [verification](verification.md) for the current evidence and limitations.

## Artwork

The iOS icon and tvOS layered icons/top shelf use Lumina's existing sunrise glyph and palette from `frontend/public/favicon.svg`. PNGs and asset catalog metadata are checked in. To regenerate them on macOS, run `mise exec -- swift scripts/generate_brand_assets.swift` from `apple/`, then regenerate the project with XcodeGen. These are app branding assets; no household posters or proof fixture artwork are included.

Before external beta/public review, complete the remaining policy, App Privacy, support metadata, reviewer server, and hardware qualification items in [the App Store review](app-store-readiness.md). This internal feedback round does not publish the app or set Store pricing.

Apple references: [upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/), [internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/), [multiple platforms](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-platforms).

### Upload through Xcode Organizer

After `export`, open `Lumina-<platform>-Organizer.xcarchive` in the release directory. The original archive is intentionally unsigned and cannot be distributed directly through Organizer (Xcode reports “No Team Found in Archive”). The Organizer copy contains the distribution-signed application from the validated IPA and records its actual signing team and certificate. `prepare_organizer_archive.py` checks bundle identity, version, signature, and matching executable UUIDs before preserving the original archive’s debug symbols. It refuses to overwrite an existing destination.

Select **Distribute App → App Store Connect → Upload** in Xcode. Creating or opening this archive does not upload it.

### Playback framework symbol warnings

The tvOS 0.1.0 (4) upload completed with missing-dSYM warnings for 27 framework placeholders, including Libass, Libavcodec, and Libavdevice. Investigation showed Xcode embedded empty dynamic-library stubs for static framework dependencies: every stub has zero section content, zero symbols, and minimum OS 100.0. The app executable has no runtime load commands for these placeholders; the actual playback library code is statically linked into the app. This is separate from the vendor static libraries lacking source-level debug information.

`prepare_archive.py` removes only unreferenced, resource-free Xcode placeholders before signing. The guard preserves libraries containing code/data, referenced libraries, and framework resources (including privacy manifests). Archive and IPA validation reject leftover unused placeholders. `mise exec -- python3 apple/scripts/test_archive_stubs.py` exercises this boundary using real Mach-O files. No fabricated dSYMs are generated. Apple DTS explains the expected static-linking symbol layout in [this framework-symbol discussion](https://developer.apple.com/forums/thread/761589).

The command-line export options keep `manageAppVersionAndBuildNumber=false`. Xcode Organizer’s recommended upload flow can independently advance the build number; select the archive by its version/build and check the final upload summary. The first upload of the 0.1.0 (4) archive reported build number 5, so the corrected replacement is packaged as build 6.

The corrected tvOS **0.1.0 (6)** signed archive and IPA passed local distribution validation, and Xcode Organizer’s App Store Connect validation completed successfully with no warnings. The executable and app dSYM UUIDs match. The replacement was prepared and validated without uploading it.

### Internal dogfood package, 2026-10-23

Version **0.1.0 (11)** is prepared for both iOS and tvOS in ignored
`apple/Distribution/0.1.0-11/`. Each platform has an unsigned source archive,
a validated distribution-signed IPA, and a signed `Lumina-<platform>-Organizer.xcarchive`
with matching executable/debug-symbol UUIDs. Open the Organizer copy to upload;
the original unsigned archive is retained for symbolication.

`ReleaseManifest.json` records the source revision, native source-input hashes,
and IPA hashes; `SHA256SUMS`, `README.txt`, and `What-to-Test.txt` accompany the
package. These are **TestFlight Internal Only** exports. Packaging and opening an
archive do not upload it; build 11 has not been uploaded by this work.

The bundled offline welcome scenes are original generated Lumina artwork with
hashes and provenance in [WelcomeArtwork.md](../Assets/WelcomeArtwork.md).
Use the matching updated server for the native Home, Requests and Account APIs.
Comprehensive device performance and physical HDR/HDMI/audio qualification remain
incomplete; see [the native performance record](native-view-performance-2026-10-23.md).

### Internal dogfood package 12, 2026-10-23

Version **0.1.0 (12)** is packaged for iOS and tvOS in ignored
`apple/Distribution/0.1.0-12/`. Both Release device archives and their
distribution-signed IPAs passed local identity, artwork, privacy, fixture-exclusion
and signature checks. The signed Organizer copies preserve matching executable
and debug-symbol UUIDs and are staged in Xcode Organizer with the signing team.

Command-line export reported “No Accounts”; Xcode's existing account successfully
signed both packages using its cloud-managed distribution certificate. Export was
local, retained build 12, and used **TestFlight Internal Only**. No upload or
production deployment was performed for build 12. Select the signed build 12
archive in Organizer when ready to upload.

The release package includes source-input and IPA hashes, release provenance and
What to Test notes. Native settings persistence and nested TV Back passed simulator
workflows on TV and a standard iPhone. Duo and physical 4K/HDR/HDMI/audio
qualification remain incomplete; see [verification](verification.md). Use the
matching updated server for the native library index and playback policy changes.

### Internal dogfood package 13, 2026-10-24

Version **0.1.0 (13)** is packaged for iOS and tvOS in ignored
`apple/Distribution/0.1.0-13/`. Both signed IPAs and signed Organizer archives
passed distribution validation, including matching executable/debug-symbol UUIDs.
The packages use **TestFlight Internal Only** and retain build 13.

Xcode Organizer confirmed **iOS 0.1.0 (13) uploaded to Apple**. Processing and
Device Testing group assignment have not been verified. **tvOS is staged locally
and has not been uploaded**; the user will distribute it. Select the signed
`Lumina-tvOS-Organizer.xcarchive` to upload. Do not upload the iOS package again.

This package includes the unified native masthead, expanded Home hero, video
control positioning/dismissal, music playback recovery, lyrics and native music
navigation/layout fixes. The iOS reader defaults to consecutive two-page spreads
on open/wide displays and one page on the closed screen, with native artwork page
curl. Focused Core and native simulator workflows passed, including reader
turn/cancel/zoom/restore and music format recovery. The full repository check
retains nine existing web test failures in `LuminaKeyboard.test.tsx` and
`v1_home.test.tsx`; backend, web typecheck, build and style passed. Physical-device
household formats/output remain to be qualified.

The release directory contains source-input hashes, the included working-tree
patch, per-platform upload status, signed IPA hashes and What to Test notes.

### Internal dogfood package 14, 2026-10-24

Version **0.1.0 (14)** is packaged for **iOS only** in ignored
`apple/Distribution/0.1.0-14/`. tvOS is deferred at the user's request for
UI/alignment dogfooding. The signed IPA and `Lumina-iOS-Organizer.xcarchive`
passed local distribution-signature, identity, privacy, artwork and matching
executable/debug-symbol checks. Xcode's Apple validation succeeded.

The packaging interaction exported locally and staged the signed archive.
Subsequent Xcode logs independently confirm **upload succeeded** for iOS build 14;
processing and tester/group assignment have not been verified. The unsigned
source archive is labeled “UNSIGNED source — do not distribute” in Organizer.

This build includes the expanded Home photo hero, Home category routing to the
full Library, wider category scrolling, genre header clearance, and poster
carousels/catalogs that extend beneath Liquid Glass navigation. Focused native
workflows passed on open and closed Duo displays. The full repository gate
retains nine existing web failures; backend and frontend typecheck/build/style
passed. Physical device formats/output require dogfooding. The package retains
source-input hashes, the working-tree patch, IPA hashes and What to Test notes.

### Internal dogfood package 15, 2026-10-24

Version **0.1.0 (15)** was built for **iOS only** from merged revision
`01be59b587b4fbd75d3a8885c80c2eaf0345cbde` (PR #150), with a clean source tree.
The package in ignored `apple/Distribution/0.1.0-15/` contains source-input and
IPA hashes, an unsigned source archive, a distribution-signed IPA, a signed
Organizer reference archive, matching executable/debug symbols and What to Test.
Identity, artwork, privacy, fixture exclusion and distribution signatures passed.

Xcode cloud-managed signing exported and uploaded **iOS 0.1.0 (15)** with
**TestFlight Internal Only** enabled and automatic build-number changes disabled.
Organizer confirmed **Uploaded to Apple**, and the package retains that screenshot.
Do not upload build 15 again. Processing and tester-group assignment have not
been verified. The unsigned source archive was re-signed by Xcode during upload;
the signed Organizer copy is retained as a local reference.

This build includes artwork-first browsing/player layouts, the Lumina detail
masthead, search clearance, neutral navigation with gold selected destinations,
Home category routing, bounded session-private artwork caching, and the staged
reader/audio recovery fixes. The full repository gate passed (5,305 backend tests,
11 skipped; 2,660 frontend tests; typecheck/build/style; 650 browser cases).
Core ran 140 cases with 5 platform skips and no failures. Four iOS release
workflows passed separately on clean iPad simulators: format recovery, downloaded
audio, advanced listening/queue controls, and direct resume/compatible video
progress. Native fixture refresh and UI navigation repairs preserve those
behavior assertions. Physical-device formats and output remain for dogfooding.


### Internal dogfood package 29, 2026-10-28

**iOS 0.1.0 (29) uploaded to Apple**, confirmed in Xcode Organizer. The package
was built from clean `main` revision `d165cf8e2c658867b3fad1b1fe4802a632019ef8`,
including the build-28 feature stack and all merged dogfood fixes through PR #183.
It uses TestFlight Internal Only, retains build 29, and uploads symbols. Do not
submit build 29 again. Apple processing and tester assignment are not verified.
tvOS remains deferred.

Ignored `apple/Distribution/0.1.0-29/` retains the exact uploaded IPA, signed export
and Organizer reference, source archive/snapshot, input and artifact hashes,
matching app dSYM, upload logs and confirmation. XcodeGen refreshed only project
object identifiers/group ordering; the derived project and patch are retained,
and the checkout was restored to its committed project after archiving.

Release qualification was focused at the owner's request: 18 background/cache
Core checks, five native workflows, normal simulator and Release device builds,
two signing/framework packaging checks, and archive/IPA distribution validation.
**No full suite was run.** Prior issue-specific screenshot/workflow evidence is
retained locally. Physical energy/PiP and actual receiver maintenance are still
unverified. The iOS upload does not deploy the server-side hero-framing warm-up.

### Internal dogfood package 30, 2026-10-28

**iOS 0.1.0 (30) uploaded to Apple**, confirmed in Xcode Organizer. Built from clean
`main` revision `f04761f6c481da68393159a395b7b5e507a0e603`, including all build-29
features and merged dogfood fixes through PR #189: steady Home hero swipes,
transparent portrait categories, system volume/brightness gestures, playback
performance info, and permission-aware native metadata editing/matching.

Xcode used cloud-managed distribution signing, preserved build 30, included
symbols, and selected TestFlight Internal Only. Do not upload build 30 again.
Apple processing and tester assignment are not verified; tvOS remains deferred.

Ignored `apple/Distribution/0.1.0-30/` retains the exact signed uploaded IPA,
Organizer-ready archive, matching app symbols, source snapshot/input hashes,
release manifest, What to Test, validation output, and successful upload logs.
The committed Xcode project was used directly; no source regeneration or build
number changes were made in the checkout. Qualification used the existing focused
dogfood checks plus Release archive and signed-package validation. **No full suite
was run.** Physical volume/brightness and energy/PiP verification remain device
checks. Native metadata actions require the matching updated Lumina server;
this iOS upload does not deploy the server.

### Dogfood package 31, 2026-10-28

**iOS 0.1.0 (31) uploaded to Apple**, confirmed in Xcode Organizer with uploaded
build number 31. Built from clean `main` revision
`c1111788aa6696eeca602873ed50f23a31ecf25c`, retaining build-30 features and merged
fixes through PR #198: critic ratings on heroes, removal of recommendation reason
badges, open-display brightness routing, quiet transient Home refresh failures,
category artwork beneath floating navigation, and immediate supersession of
obsolete committed video seeks.

Ignored `apple/Distribution/0.1.0-31/` retains the uploaded signed IPA, source
snapshot/input hashes, matching symbols, signed Organizer archive, validation
results, release manifest, What to Test and successful upload logs. The committed
Xcode project was used directly with build-number overrides. No source regeneration,
server deployment or full suite was performed. Archive and signed-package
validation passed; the retained issue-specific playback evidence includes 13 Core
checks, the iPhone scrub/pause workflow and a shared tvOS Simulator build.

Do not upload build 31 again. Apple processing and tester assignment are not
verified; tvOS remains deferred. Physical Duo brightness, device energy/output
and real household-media latency still require dogfooding. Uncached remote seeks
still need media bytes. The separately identified standard-phone portrait detail
Play-button/tab-bar overlap is not fixed in this build.


### Internal dogfood package 33, 2026-10-29 — upload pending

Version **0.1.0 (33)** has validated unsigned Release device archives for iOS
and tvOS in ignored `apple/Distribution/0.1.0-33/`, built from clean main
`06faf929684b79b4e70df2228a781e43c19b8e63` (PR #206). The package preserves
tracked native source hashes and snapshot, archive/preparation/validation logs,
matching executable/dSYM UUIDs, export options and focused verification records.
Identity, physical-device platform, artwork, privacy and test-fixture exclusion
checks pass. Neither archive contains the UI-testing compilation condition.

This rollup includes source-specific IMDb, Rotten Tomatoes and Metacritic
ratings (PR #205), full lightweight catalogs and bounded poster prefetch,
scene-display brightness targeting, artwork-first TV details, and the shared
compositor/decoder improvements. Focused iPhone motion/layout, TV catalog,
ratings, Remote Play/resume and direct/HLS/MKV workflows pass. The full TV
performance tour and 503-title stress workload pass; see the
[measurement record](tvos-performance-2026-10-29.md). The owner requested focused
checks, so no full repository/Core/native UI suite was run.

**No build-33 IPA has been signed or uploaded.** CLI iOS export failed with
“No Accounts” and no iOS Distribution certificate. The Mac remained locked,
preventing access to Xcode's existing cloud-signing account. The requested iOS
upload remains pending; tvOS is staged for qualification and has no upload
authorization. Preserve the earlier unuploaded build-32 package unchanged.

After unlock, recheck live build inventory, sign/export the existing iOS archive
with internal-only distribution and automatic build-number changes disabled,
validate the actual signed IPA, and prepare its signed Organizer copy. Retain the
actual uploaded artifact, symbols, logs and confirmation before updating upload
status. The latest confirmed iOS upload remains build 31; processing and tester
assignment are unverified. Named ratings require the matching server update;
no household deployment or physical-TV/HDMI/HDR/audio qualification occurred.
