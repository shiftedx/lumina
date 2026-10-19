# Native Apple proof

SwiftUI applications for iPhone, iPad, and Apple TV, sharing a Foundation API client and an AVKit player. These are native apps; they contain no web view. The proof connects to an existing Lumina server, browses/searches household videos, plays direct or compatible HLS video, and synchronizes watch progress with the web UI.

This is an engineering proof, not an App Store release or an Infuse playback replacement. AI discovery, profile switching, offline downloads, PiP, acquisition, administration, and third-party streaming services are outside this proof. Native AVKit audio/subtitle controls expose the tracks present in the selected stream; full library subtitle delivery and premium codec parity still need implementation and device qualification.

## Build and install

Requires Xcode 26 or newer with iOS/tvOS SDKs, a Mac, and iOS/iPadOS/tvOS 18 or newer. The checked-in Xcode project was generated with XcodeGen; ordinary Xcode builds do not require XcodeGen.

1. Open `apple/Lumina.xcodeproj`.
2. Select **Lumina-iOS** or **Lumina-tvOS**, then a simulator or paired physical device.
3. For a physical device, select your Apple development team under **Signing & Capabilities**. Change the bundle identifier if your team requires it. No developer credentials are stored in this repository.
4. Build and run. Allow local-network access when iOS asks.
5. Enable **Manage Server → Connections → Metadata → Apps like Infuse** in Lumina's web UI. Enter the server origin shown there, with no `/jellyfin` or `/emby` suffix. Use the member's own Lumina account; accounts with two-step verification must use an app password.

Use the existing server's remote HTTPS or explicitly configured LAN HTTP address. Do not expose the internal Docker service port for remote use. The app uses Apple’s [local-network ATS exceptions](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking) for local names, `.home.arpa`/`.lan` names, and private IP ranges and keeps normal TLS validation; remote servers should use trusted HTTPS. Server addresses must be origins without a path, query, or embedded credentials. Session tokens and a stable device ID are stored in Keychain; the password is used only to exchange for a revocable connected-app token. Sign-out clears local credentials immediately and attempts server revocation; an offline token can also be revoked through Lumina's Connected apps page.

```sh
cd apple
swift test --package-path Core
xcodebuild -project Lumina.xcodeproj -scheme Lumina-iOS \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project Lumina.xcodeproj -scheme Lumina-tvOS \
  -destination 'generic/platform=tvOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

After changing `project.yml`, run `xcodegen generate` from `apple/` and commit the regenerated project. `LuminaCore` is a local Swift package with no external dependencies.

## Playback behavior

Direct playback is negotiated for native MP4/M4V/MOV H.264/HEVC and AAC/AC-3/E-AC-3. Other sources use the server's HLS path when supported. **Convert to compatible video** disables direct playback and asks the server for a compatible stream; the server may retain compatible video while converting audio. Conversion does not promise source HDR, Dolby Vision, lossless audio, or Atmos preservation.

The player seeks to the saved whole-file position before reporting start, sends progress about every five seconds and on transport state changes, and sends a final stopped event on dismissal. Reports are serialized so a delayed progress request cannot overwrite the stopped position. Leaving the app pauses playback. Stopping also releases the server's HLS session. A network failure displays a progress-sync warning; there is no offline queue in this proof.

Apple TV uses the native focus engine, remote transport controls, a readable poster grid, and native playback controls, following Apple's [tvOS design](https://developer.apple.com/design/human-interface-guidelines/designing-for-tvos), [focus](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection), and [video playback](https://developer.apple.com/design/human-interface-guidelines/playing-video) guidance. AVKit's preferred display criteria are enabled for the system's Match Content behavior. Actual HDR, refresh-rate switching, HDMI output, and multichannel sound must be checked on hardware; simulator playback cannot establish them.

Before App Store distribution, add required icon/top-shelf assets, qualify codecs and any engine licenses, complete App Privacy and store metadata, and provide a reachable reviewer demo server. See the [App Store readiness review](docs/app-store-readiness.md) for the paid-release requirements and reviewer-notes draft. The proof has not been submitted to App Review.

## Validation

See [the physical device checklist](docs/device-validation.md) and [recorded verification](docs/verification.md). The real-server simulator harness generates lawful synthetic media and disposable credentials; see [scripts](scripts/README.md). Generated credentials, application data, build products, and test results are ignored by Git. Never attach stream URLs or tokens to issues.
