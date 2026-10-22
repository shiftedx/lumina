# App Store readiness: native Apple proof

**Reviewed:** 2026-10-17

**2026-10-19 update:** The [internal TestFlight packaging pass](testflight.md) adds icon/top shelf assets, a shared iOS/tvOS bundle ID and app record, version settings, and distribution tooling. The findings below retain the original pre-packaging review; those asset/identity blockers have been addressed for the internal beta. Public-release privacy, metadata, reviewer access, and hardware qualification remain outstanding.

For the subsequently requested external beta, the owner selected the companion-server review model: reviewers install Lumina's self-hosted server and create their own member account. Review notes include server source/setup instructions, and developer-managed reviewer credentials are disabled. A hosted demo instance is not part of the requested distribution workflow; the original hosted-demo recommendations below describe the earlier review assumption.
**Scope:** paid-upfront native iPhone, iPad, and Apple TV client for a user-selected, self-hosted Lumina household server. The proof supports member sign-in, browse/search, direct or compatible-HLS playback, and progress sync. It has no in-app signup, social login, advertising, analytics SDK, WebView, StoreKit/IAP, third-party acquisition, or YouTube/Twitch/Kick integration.

This is a read-only review of the proof, not an approval guarantee. A passed simulator journey is evidence for the proof; it does not make the project submission-ready.

## 2026-10-20 extended-audio implementation update

The review below is historical. Native music now includes saved/smart playlists and mixes, read-only household sharing, member queue synchronization with revision conflicts and paused restore, owner-scoped offline audio/artwork/lyrics, timed lyrics, buffered normalization/crossfade, persistent sleep deadlines, and acknowledged Sonos output controls. See [current usage](../README.md) and [verification](verification.md) for limits and actual evidence; extended platform builds and four additional UI workflows remain pending in that record until results are entered.

Update the signed candidate's reviewer steps, screenshots, privacy policy and App Privacy assessment for owner-scoped on-device downloads/checkpoints/lyrics and server-mediated playlist, queue and speaker operations. Explain explicit sign-out cleanup and tvOS cache purging. Offline downloading is limited to the member's authorized household media; it adds no third-party acquisition service. Test speaker handoff, interruptions, storage recovery and offline errors on real hardware. No new upload, approval, distribution entitlement or all-format playback qualification follows from these code changes. The original checklist and recommendations below remain historical review evidence, subject to the later selected companion-server review model above.

## 1) Executive Summary

- The proof is a native SwiftUI/AVKit app, with native iOS/iPadOS and tvOS shells over a Foundation client; it is not a repackaged website. This supports [Guideline 4.2](https://developer.apple.com/app-store/review/guidelines/).
- No urgent runtime correctness blocker was found in this review. The reported real-server simulator full-flow results are consistent with the code paths inspected.
- **Release blockers:** final app icons/store assets, an accessible Privacy Policy and accurate App Privacy answers, a reachable reviewer server/member, and complete paid-app metadata/agreements.
- The connection screens clearly explain the household-server prerequisite and the opt-in connected-app API: [iOS](../App/iOS/MobileRootView.swift), [tvOS](../App/tvOS/TVRootView.swift).
- Local-network access has a focused purpose string and narrow local/private ATS exceptions; remote connections retain normal TLS: [Info.plist](../Config/Info.plist).
- Keychain stores a revocable session and stable device ID; password entry is exchanged for a token and cleared from the view: [LuminaStore](../Core/Sources/LuminaCore/LuminaStore.swift).
- No Sign in with Apple requirement is triggered: the proof has no third-party or social login. See [Guideline 4.8](https://developer.apple.com/app-store/review/guidelines/).
- The intended pricing is paid upfront. There is no IAP restore path because the proof has no IAP. Do not add external paid feature unlocks without revisiting [Guideline 3.1](https://developer.apple.com/app-store/review/guidelines/).

## 2) Risk Register

| Priority | Area | Finding | Why Review Might Reject | Evidence | Recommendation | Effort | Confidence |
|---|---|---|---|---|---|---|---|
| P0 blocker | Review access | No reviewer demo is provisioned. | A login app must offer working backend/demo access under [2.1](https://developer.apple.com/app-store/review/guidelines/). | [README](../README.md) calls reviewer setup future work. | Provide HTTPS server, non-admin member, API enablement, authorized media, and steps. | M | High |
| P0 blocker | Privacy | No accessible in-app Privacy Policy link. | [5.1.1](https://developer.apple.com/app-store/review/guidelines/) requires policy in app and App Store Connect. | No policy `Link` or `openURL` in `apple/`. | Publish policy, add in-app link, configure ASC policy URL. | M | High |
| P0 blocker | Assets/metadata | No checked-in asset catalog/images. | Store submission needs final platform assets and complete metadata. | [project.pbxproj](../Lumina.xcodeproj/project.pbxproj) names missing `AppIcon`/top-shelf assets. | Add icons/top shelf, screenshots, support URL, age rating, category. | M | High |
| P0 blocker | Paid distribution | No App Store Connect paid-app record/configuration. | Review cannot assess the declared business model or submit the app. | Development identifiers/version in [project.yml](../project.yml). | Configure pricing, agreements, banking/tax, metadata, truthful description. | S/M | High |
| P1 gate | App Privacy | Manifest says no tracking/SDK data, while app sends credentials, device/token, and progress to selected server. | Labels/policy must match actual retention and access. | [Privacy manifest](../Config/PrivacyInfo.xcprivacy), [client](../Core/Sources/LuminaCore/LuminaClient.swift), [store](../Core/Sources/LuminaCore/LuminaStore.swift). | Assess developer/third-party access to retained household-server data; do not automatically classify all self-hosted data either way. | M | High |
| P1 decision | Universal purchase | iOS/tvOS IDs differ. | Only relevant if promising one purchase across platforms. | [project.yml](../project.yml). | Choose universal or separate purchases; universal needs matching IDs/one record per [Apple](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-platforms). | S | High |
| P2 conditional | Accounts | No native account creation/registration links; sign-out revokes local session. | Deletion applies if account creation/onboarding enters scope. | [LuminaStore](../Core/Sources/LuminaCore/LuminaStore.swift). | Add applicable deletion path if adding account creation; no present defect. | M | High |
| P2 gate | Rights | No provider acquisition/downloader code; users select Lumina server. | Reviewer may request authorization for served media/services. | [Playback request](../Core/Sources/LuminaCore/LuminaClient.swift). | Use authorized demo media and rights statement; see [5.2.2/5.2.3](https://developer.apple.com/app-store/review/guidelines/). | S | High |
| P3 | Networking | Native IPv6 ranges/session handling are present. | Final build must operate on IPv6-only networks. | [Info.plist](../Config/Info.plist). | Qualify signed candidate on NAT64/IPv6 and remote HTTPS. | S | Medium |

## 3) Detailed Findings

### Privacy & Data Handling

The client uses Keychain for the session/device ID and rejects cross-origin redirect/playback URLs: [LuminaStore](../Core/Sources/LuminaCore/LuminaStore.swift), [LuminaClient](../Core/Sources/LuminaCore/LuminaClient.swift). This is appropriate security hygiene.

App Privacy is a deployment decision, not an inference from the empty privacy manifest. Apple defines collection around data sent off-device and retained longer than the request, and requires disclosures for developer and third-party practices. Determine whether the app developer can access a household server's retained account/device/progress data, then make the label and policy match that answer. See [Apple’s App Privacy details](https://developer.apple.com/app-store/app-privacy-details/) and [Guideline 5.1.1](https://developer.apple.com/app-store/review/guidelines/).

### Permissions & Entitlements

`NSLocalNetworkUsageDescription` explains the iOS/iPadOS LAN use, and ATS is limited to local/private ranges rather than `NSAllowsArbitraryLoads`: [Info.plist](../Config/Info.plist). This is a sound proof configuration. Record the prompt’s purpose in review notes and run final IPv6/NAT64 qualification. No unrelated permission or tracking entitlement was found.

### Monetization

There is no StoreKit, paywall, subscription, IAP, or external purchase call to review. A paid-upfront listing is compatible with that scope; no restore-purchase UI is required for an app with no IAP. Product-page copy must explain the app is a one-time native client purchase and what household-server setup it requires. Do not claim the server must always be free; the release scope only establishes that the native app has no external paid unlock flow.

### Account & Authentication

The app signs into the member account on the selected household server and can sign out: [mobile shell](../App/iOS/MobileRootView.swift), [TV shell](../App/tvOS/TVRootView.swift). That is directly relevant to the service’s personalized library/progress function. No social login means no equivalent Sign in with Apple option is required. Account deletion is conditional on future account-creation/onboarding scope, not a present proof defect.

### Content / External Links

The code is a native client over a configured server and AVKit playback path, not a third-party content catalog or downloader: [playback request](../Core/Sources/LuminaCore/LuminaClient.swift), [player](../App/Shared/PlaybackScreen.swift). Submission metadata and demo media must not imply rights to content that the developer lacks. Avoid unlicensed third-party marks in the public listing.

### Technical Stability & Performance

The app handles invalid origins, same-origin redirects, connection failures, sign-out, and progress reporting: [client](../Core/Sources/LuminaCore/LuminaClient.swift), [store](../Core/Sources/LuminaCore/LuminaStore.swift). The proof also has native tvOS focus and remote controls: [TVRootView](../App/tvOS/TVRootView.swift). Hardware HDR/audio qualification remains a release quality task, not proof evidence.

### UX & Reviewability

The native connection, library, error, empty, and sign-out states make the core flow reviewable once demo access exists. The current README accurately labels the result as an engineering proof and lists remaining release work: [README](../README.md).

## 4) Reviewer Experience Checklist

- [ ] Install/launch final signed iOS/iPadOS and tvOS builds with production icons.
- [ ] Explain that the app connects to a user-selected household Lumina server.
- [ ] Provide a live HTTPS reviewer server and non-admin credentials; pre-enable the connected-app API.
- [ ] Demonstrate browse, search, direct playback, compatibility HLS playback, seek, dismiss, and synced resume.
- [ ] Show the iOS/iPadOS local-network prompt only for LAN use; test remote HTTPS separately.
- [ ] Verify privacy-policy link, support URL, policy/label accuracy, rights-safe demo media, and paid-upfront listing copy.
- [ ] Verify empty/offline/error states and NAT64/IPv6 operation on the final signed candidate.

## 5) Suggested Reviewer Notes (Draft)

> Lumina is a paid, native household-media client for iPhone, iPad, and Apple TV. It connects only to a user-selected Lumina server. It does not sell, acquire, download, or aggregate third-party streaming content.
>
> **Review access**
> Server: `<review-server-https-url>`
> Member: `<review-member>`
> Password/app password: `<review-password>`
> The server's connected-app API is enabled and the supplied library contains media authorized for this review.
>
> **Steps**
> 1. Enter the server URL and supplied member credentials.
> 2. Open **Video**, browse or search, and select a title.
> 3. Use **Play**, then use **Convert to compatible video** for the HLS path.
> 4. Pause/seek/dismiss playback and reopen the title or **Continue Watching** to observe progress sync.
>
> The local-network prompt is used only for the user-selected LAN server. Remote connections use HTTPS. The app is a one-time App Store purchase with no subscriptions, IAP, advertisements, social login, or external purchase links.

## 6) Next Pass

Before submission, perform a release-candidate pass covering the final signed archive, assets, policy/labels, App Store Connect record, reviewer demo, universal-versus-separate purchase decision, NAT64/IPv6, and physical-device video/audio qualification.


## Native UI signal gate (2026-10-21, qualification pending)

The audit ledger is [native-ui-ledger.md](../../docs/agents/native-ui-ledger.md);
its linked history report distinguishes existing-test regression catches from
feature creation and test drift. A new UI test needs a journey reason; logic
and copy go lower, using an existing Core/API or hosted contract first.
Tests that previously caused an app-code fix are never deleted.

| Tier | Intended use | Selected journeys | Budget |
| --- | --- | --- | --- |
| smoke | Merge bar for native UI changes | Audio/now-playing/background/queue and direct/conversion/resume/background video on both platforms (4 broad cases) | <10 minutes wall per complete two-platform run |
| full | Pre-release | All 15 iOS and 8 executable tvOS cases | <45 minutes wall per complete two-platform run |

The suite has fewer than 50 critical journeys, so smoke retains the relevant
critical playback paths without adding tests to meet a count. Home carousel,
hierarchy navigation, Books and advanced music remain in full. iOS runs on an isolated
regular-width iPad to exercise RTL spread geometry; hardware Duo pose selection
remains an additional manual device qualification. No automatic serial exception
is needed for the current-viewport check on that iPad.

Run from the repository root with locked dependencies installed:

```sh
mise exec -- python3 apple/scripts/run_ui_tiers.py --tier smoke --repeat 2 --output /tmp/lumina-ui-smoke-<unique-run>
mise exec -- python3 apple/scripts/run_ui_tiers.py --tier full --repeat 2 --output /tmp/lumina-ui-full-<unique-run>
```

Use a fresh output directory. The runner reports build time separately and sums
iOS plus tvOS test invocation wall times for each repeat, including simulator
startup and fixture provisioning.
The platforms run sequentially. Each platform warms two owned simulators serially,
then runs two explicit XCTest shards concurrently against those devices. Each shard
disables Xcode's internal clone scheduling; the overall cohort remains parallel.
Fresh iOS devices disable only the stock widget renderer, empty Photos analysis and
CoreSpotlight indexing jobs, which are outside Lumina's tested integrations. A full pass requires
exact expected executed/passed counts and zero failures or skips, twice. A green
empty selection cannot qualify. The report contains total invocation wall time
as well as per-repeat test wall time; build/bootstrap is recorded separately
from test execution qualification.

Each journey is a separate XCTest scheduling class. Its fixture broker allocates
an exclusive loopback port, database, media root and disposable member; teardown
stops only that owned server. A per-test UUID defaults suite and Keychain service
survive the test's intentional app relaunch and do not share state across cases.
There is no application App Group entitlement; Xcode workers use separate app
sandboxes, and persisted downloads/queue files are scoped by the unique member
and origin. Only Debug builds accept the test preference/credential namespace.

The user explicitly authorized running alongside other agents' Xcode jobs and
removed the host-load ceiling on 2026-10-21. The runner records peak load for
measurement context without waiting or cancelling because of load. It creates
its own simulators and removes only its recorded devices/clones and child
processes. It never stops another agent's Xcode run or simulator.

**Qualification is pending.** No before/after wall-time claim, two-pass parallel
qualification or budget claim is established until actual result bundles and
counts are recorded in the ledger. Passing this suite does not replace the
physical-device format/output and App Store requirements above.
