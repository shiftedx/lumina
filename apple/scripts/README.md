# Native proof fixture server

Run `mise exec -- backend/.venv/bin/python apple/scripts/proof_server.py` before the native simulator UI tests. It creates a fresh temporary Lumina data directory and media root, starts the production FastAPI app at `http://127.0.0.1:18743`, and writes the ignored `apple/Tests/UITests/Fixture.json` after proving the opt-in Jellyfin-compatible sign-in, items, playback negotiation, and progress APIs.

The fixture contains a random per-run Lumina username and password, three imported item IDs, and the loopback server URL. It generates long titles, portrait/landscape artwork, direct/compatible video, and a shorter completion/replay video. It is mode `0600`, is removed when the process stops, and must not be committed. The server accepts only loopback network calls and removes its generated temporary state after Ctrl-C.


After the server prints “ready”, run the shared client/media test and UI suites sequentially (they update the same synthetic watch positions):

```sh
cd apple
LUMINA_NATIVE_FIXTURE="$PWD/Tests/UITests/Fixture.json" swift test --package-path Core --filter NativeServerTests
xcodebuild -project Lumina.xcodeproj -scheme Lumina-iOS \
  -destination 'platform=iOS Simulator,id=<your-simulator-UDID>' \
  -derivedDataPath /tmp/lumina-native-ios test
xcodebuild -project Lumina.xcodeproj -scheme Lumina-tvOS \
  -destination 'platform=tvOS Simulator,id=<your-simulator-UDID>' \
  -derivedDataPath /tmp/lumina-native-tv test
```

Use `xcrun simctl list devices` to find device IDs. Do not disable simulator code signing for runtime tests: Keychain needs the generated signing entitlements. Unsigned generic builds check compilation only. Keep DerivedData and result bundles outside `apple/` to avoid local-package file-watcher rebuilds. Normal Xcode defaults already do this.

The Xcode project optionally copies the generated fixture into the test bundle using a test-only build phase. Without a fixture, a clean checkout still builds and the UI test explicitly skips. This build phase has script sandboxing disabled only for the UI-test targets so its optional input can be absent; application build scripts remain sandboxed. There is no alternate test login path in the app.

The XCTest UI journey signs in through native fields, opens the actual player, observes its time advance, verifies the shared server position advances beyond its initial value, reopens to check resume, and repeats with compatibility playback. It attaches a screenshot to the result. The macOS AVFoundation test provides additional real-media evidence, rather than iOS/tvOS hardware qualification. None of these tests establish HDR/HDMI/audio output quality.

For Duo, select a disposable simulator in Device Hub and set each of Closed, Book, and Open in portrait and landscape. Run the following once per state, using a distinct result-bundle path:

```sh
xcodebuild -project Lumina.xcodeproj -scheme Lumina-iOS \
  -destination 'platform=iOS Simulator,id=<your-disposable-simulator-UDID>' \
  -parallel-testing-enabled NO -collect-test-diagnostics never \
  -derivedDataPath /tmp/lumina-duo-matrix \
  -resultBundlePath /tmp/lumina-duo-<pose>-<orientation>.xcresult \
  -only-testing:Lumina-iOS-UITests/NativeProofTests/testCurrentDuoLayoutKeepsControlsAndPlaybackReachable test
```

The test preserves the current Device Hub layout, records actual viewport dimensions, and exercises sign-in, library, detail, player bounds, verified background pause, explicit resume, completion, Replay, and Done. XCTest's orientation/Home requests do not reliably change this Duo runtime; choose layout in Device Hub and verify visual results there. The background check explicitly activates SpringBoard and verifies application state. The test signs out any existing session, so never use a simulator holding a production login.

`testCompatibleHLSReplayRequestsFreshSession` separately verifies completion/replay of a converted stream and its persisted progress. Keep it sequential with the other native workflows.
