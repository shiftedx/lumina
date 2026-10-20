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

Archive creates unsigned Release device builds so an unregistered physical Apple TV is not a prerequisite. Export and upload use automatic App Store distribution signing from the supplied team; Xcode can use its managed signing service. The exported IPA validation requires a valid signature, disabled debugging, and the TestFlight entitlement. Archive validation checks identity, versions, device platform, icons/top shelf, privacy manifest, symbols, and absence of test fixtures/test bundles. Keep both `.xcarchive` bundles for crash symbolication.

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
