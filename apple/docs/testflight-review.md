# Native TestFlight distribution review

Reviewed 2026-10-19, before packaging changes. Scope: internal testing on the owner's iPhone, iPad, and Apple TV, using one app record. This supplements the broader [App Store review](app-store-readiness.md); it does not authorize a public release.

The user subsequently requested an email invitation for an external tester. Build 2 is an external-eligible candidate for each platform, with a private external group. The owner provided review contact details and clarified the review model: reviewers install their own companion server and create their own member account; there is no developer-hosted instance. The reviewer credential fields remain disabled as requested, and the review notes include companion server setup instructions. The broader policy/privacy and reviewability findings remain relevant to external review. No public Store submission is part of this task.

## 1) Executive summary

- Lumina is a native SwiftUI/AVKit client for an existing household server.
- Sign-in, visual browsing/search, and playback/progress sync are the three core flows.
- The developer team is signed into Xcode and has a valid development signing identity.
- Packaging blockers are missing icon/top shelf assets, separate proof bundle IDs, and absent distribution configuration.
- Use one stable bundle ID for iOS and tvOS, as requested, before the first upload fixes that identity.
- Internal TestFlight is appropriate for this device feedback round; external beta review and public Store metadata remain separate work.
- Native build and targeted workflow evidence exists in [verification](verification.md). A known tvOS Home/return playback issue remains a device-testing target.

## 2) Risk register

| Priority | Area | Finding | Why review/distribution might fail | Evidence | Recommendation | Effort | Confidence |
|---|---|---|---|---|---|---|---|
| P0 blocker | Technical | Icon assets absent | Archive validation requires platform icons | `project.yml`, missing asset catalogs | Add iOS icon and layered tvOS icon/top shelf assets | S | High |
| P0 blocker | Technical | Separate proof identities, no team configuration | Cannot associate both builds with one app record | `project.yml` | Share a stable ID; supply team at archive time | S | High |
| P0 blocker | Technical | No App Store Connect record for Lumina | Upload must resolve to an app | Signed-in Apps inventory | Create one record for iOS/tvOS | S | High |
| P1 high | Technical | Versions hard-coded in Info.plist | Later uploads can accidentally repeat build numbers | `Config/Info.plist` | Expand Xcode version/build settings; validate archived values | S | High |
| P2 medium | UX | tvOS playback can resume after Home/return | Unexpected playback on physical TV | `docs/verification.md` | Explicitly include in beta test notes; retain known-issue status | M | High |
| P1 public-release gate | Privacy | Policy, labels, review access outstanding | Public/external review needs accurate disclosure and access | `docs/app-store-readiness.md` | Complete before external review/public submission | M | High |

## 3) Detailed findings

### Privacy & data handling

The client exchanges a password for a revocable session, stores the session/device ID in Keychain, and sends progress to the selected household server (`LuminaStore`, `LuminaClient`). No analytics, ads, tracking, or custom cryptographic implementation was found. Preserve the privacy manifest and exclude disposable test credentials. Verify the archive contains the manifest and contains no `Fixture.json` or test bundle. Public App Privacy answers still require the deployment assessment described in the broader review.

### Permissions & entitlements

`Info.plist` describes local-network use and narrows HTTP exceptions to private/local servers. No unrelated entitlement is required. Verify signed application entitlements, local-network access on devices, and remote HTTPS independently.

### Monetization

There is no StoreKit, paywall, subscription, or external purchase flow in the native client. This internal beta does not choose or change Store pricing. Verify the test notes do not promise purchases or access to supplied content.

### Account & authentication

An existing server member account and enabled connected-app API are required. There is no native signup/social login. Verify sign-in, restoration after relaunch, and Account > Sign Out against the owner's server. Do not put server credentials in source, release notes, or build artifacts.

### Content / external links

The app displays the selected server's library; it does not include a bundled media catalog. Use the existing Lumina brand glyph for release assets. Verify no synthetic proof media, private library data, or third-party poster artwork is shipped in the app.

### Technical stability & performance

Generic simulator builds and targeted real-server UI workflows passed previously; physical-device codec, focus, networking, and lifecycle qualification remains the purpose of this beta. Archive Release for both actual device platforms, validate signatures/assets/versions, and keep debug symbols. Full repository checks have unrelated backend failures recorded in `verification.md`.

### UX & reviewability

Test the artwork-first feature, poster density, rotation, short-pull search/deep-pull refresh, account controls, TV remote focus, playback, and progress sync. The known TV Home/return issue must remain visible in beta notes. External beta/public review will additionally need a reachable review server and current legal/support metadata.

## 4) Reviewer experience checklist

- [ ] Install and launch signed iOS and tvOS builds through TestFlight.
- [ ] Understand the existing household-server/API prerequisite.
- [ ] Allow local-network access when connecting to a LAN server.
- [ ] Sign in, browse, search, play, seek, dismiss, and verify synced resume.
- [ ] Exercise offline, empty-library, and invalid-credentials states.
- [ ] Confirm there is no purchase/restore path in this beta.
- [ ] Complete policy/support/reviewer-access requirements before external review.

## 5) Suggested reviewer notes

For internal testing, use [the beta test notes](testflight-what-to-test.txt). For later external review: provide `<review-server-https-url>`, `<review-member>`, and `<review-password>` through App Store Connect's protected review fields, with authorized media and the connected-app API enabled. Explain the LAN permission and server prerequisite. No demo mode or purchase path exists.

## 6) Next pass

The packaging authorization above applied to the recorded 2026-10-19 release work. Subsequent source merges and UI tweaks do not authorize another TestFlight build or upload; wait for an explicit release request. Revisit the broader review before inviting external testers or submitting publicly.

Sources: [Apple: add platforms](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-platforms), [upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/), [internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/), [icon catalogs](https://developer.apple.com/documentation/xcode/configuring-your-app-icon).
