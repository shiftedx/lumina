import Foundation
import XCTest
#if os(iOS)
import UIKit
#endif

@MainActor
final class NativeProofTests: XCTestCase {
    private var application: XCUIApplication?

    override func setUpWithError() throws {
        try super.setUpWithError()
        continueAfterFailure = false
    }

    func testNativePlaybackReportsResumeAndConversionProgress() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }

        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)

        let apiSession = makeTestAPISession(origin: origin)
        defer { apiSession.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: apiSession)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 0)
        let directBefore = try await api.playbackSeconds(identity: identity, itemID: fixture.directItemID)
        let convertedBefore = try await api.playbackSeconds(identity: identity, itemID: fixture.convertedItemID)

        #if os(iOS)
        XCUIDevice.shared.orientation = UIDevice.current.userInterfaceIdiom == .pad ? .landscapeLeft : .portrait
        #endif
        try launchAndResetSession()
        try signIn(using: fixture)

        #if os(iOS)
        try await assertLibraryCardsStayInsideTheVisibleGrid(using: fixture)
        #endif

        try openLibraryItem(id: fixture.directItemID)
        let directPlaybackTime = try startPlayback(using: "playButton")
        let directInitial = try waitForPlaybackValue(directPlaybackTime, timeout: 10) { _ in true }
        let directAdvanced = try waitForPlaybackValue(directPlaybackTime, timeout: 25) {
            $0 >= directInitial + 7
        }
        XCTAssertGreaterThan(directAdvanced, directInitial + 6, "The direct player's accessibility value did not advance for seven seconds.")

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try await Task.sleep(for: .seconds(4))
        let pausedTime = try waitForPlaybackValue(directPlaybackTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(pausedTime, directAdvanced + 2, "The Siri Remote did not pause the native player.")
        XCUIRemote.shared.press(.playPause)
        let beforeHome = try waitForPlaybackValue(directPlaybackTime, timeout: 15) { $0 >= pausedTime + 2 }
        XCUIRemote.shared.press(.home)
        try await Task.sleep(for: .seconds(4))
        application!.activate()
        try waitForExistence(element("playbackScreen"), timeout: 20, named: "player after returning from Home")
        let afterHome = try waitForPlaybackValue(directPlaybackTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(afterHome, beforeHome + 2, "Playback continued while the app was in the background.")
        try await Task.sleep(for: .seconds(3))
        let stillPaused = try waitForPlaybackValue(directPlaybackTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, afterHome + 1, "Returning to the app resumed playback without an explicit action.")
        XCUIRemote.shared.press(.playPause)
        _ = try waitForPlaybackValue(directPlaybackTime, timeout: 15) { $0 >= stillPaused + 2 }
        #endif

        try attachPlaybackFrame(named: "Direct video frame")
        try closePlayer()
        let directSaved = try await waitForServerProgress(api, identity: identity, itemID: fixture.directItemID, minimum: max(2, directBefore + 5))
        XCTAssertGreaterThan(directSaved, 2, "Direct playback did not report more than two seconds of watch progress.")

        try returnToLibrary()
        try refreshLibrary(waitingForResumeOf: fixture.directItemID)
        try openLibraryItem(id: fixture.directItemID)
        let resumedPlaybackTime = try startPlayback(using: "playButton")
        let resumedInitial = try waitForPlaybackValue(resumedPlaybackTime, timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(
            resumedInitial,
            directSaved - 2,
            "Reopening the direct fixture did not seek to the saved watch position."
        )
        let resumedAdvanced = try waitForPlaybackValue(resumedPlaybackTime, timeout: 10) {
            $0 >= resumedInitial + 1
        }
        XCTAssertGreaterThan(resumedAdvanced, resumedInitial, "The resumed player did not continue advancing.")

        try closePlayer()
        try returnToLibrary()
        try refreshLibrary(waitingForResumeOf: fixture.directItemID)
        try openLibraryItem(id: fixture.convertedItemID)
        let convertedPlaybackTime = try startPlayback(using: "convertButton")
        let convertedInitial = try waitForPlaybackValue(convertedPlaybackTime, timeout: 10) { _ in true }
        let convertedAdvanced = try waitForPlaybackValue(convertedPlaybackTime, timeout: 25) {
            $0 >= convertedInitial + 7
        }
        XCTAssertGreaterThan(convertedAdvanced, convertedInitial + 6, "The converted player's accessibility value did not advance for seven seconds.")

        try attachPlaybackFrame(named: "Compatible HLS video frame")
        try closePlayer()
        let convertedSaved = try await waitForServerProgress(api, identity: identity, itemID: fixture.convertedItemID, minimum: max(2, convertedBefore + 5))
        XCTAssertGreaterThan(convertedSaved, 2, "Converted playback did not report more than two seconds of watch progress.")

        completed = true
    }

    func testCurrentDuoLayoutKeepsControlsAndPlaybackReachable() async throws {
        #if os(iOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }

        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let apiSession = makeTestAPISession(origin: origin)
        defer { apiSession.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: apiSession)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.matrixItemID, seconds: 0)
        try launchAndResetSession()
        try assertConnectionFieldsFitViewport(named: "Current-layout sign-in")
        try signIn(using: fixture)
        try await assertMatrixLibraryItem(fixture, named: "Current-layout library")

        try openLibraryItem(id: fixture.matrixItemID)
        try assertMatrixDetail(fixture, named: "Current-layout detail")

        let playbackTime = try startPlayback(using: "playButton")
        let initial = try waitForPlaybackValue(playbackTime, timeout: 10) { _ in true }
        _ = try waitForPlaybackValue(playbackTime, timeout: 10) { $0 >= initial + 2 }
        try assertPlayerControlsFitViewport(named: "Current-layout player")
        let beforeHome = try waitForPlaybackValue(playbackTime, timeout: 5) { _ in true }

        try backgroundApplication()
        try await Task.sleep(for: .seconds(3))
        application!.activate()
        guard waitUntil(timeout: 10, condition: { self.application!.state == .runningForeground }) else {
            throw ProofFailure.timeout("Lumina did not return to the foreground after activation.")
        }
        try waitForExistence(element("playbackScreen"), timeout: 15, named: "player after returning from Home")
        let afterHome = try waitForPlaybackValue(playbackTime, timeout: 5) { _ in true }
        try await Task.sleep(for: .seconds(2))
        let stillPaused = try waitForPlaybackValue(playbackTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(afterHome, beforeHome + 2, "Playback continued while the app was in the background.")
        XCTAssertLessThanOrEqual(stillPaused, afterHome + 1, "Returning to the app resumed playback without a user action.")
        _ = try resumeNativePlayer(playbackTime, pausedAt: stillPaused)

        let replay = element("replayButton")
        try waitForExistence(replay, timeout: 50, named: "replay button at end of media")
        try waitForExistence(element("playbackScreen"), timeout: 2, named: "player retained at end of media")
        try assertPlayerControlFitsViewport(replay, named: "Replay")
        try assertPlayerControlsFitViewport(named: "Current-layout player at end")

        try activate(replay)
        guard disappears(replay, timeout: 5) else {
            throw ProofFailure.ui("Replay did not leave the finished playback state.")
        }
        let replayedFrom = try waitForPlaybackValue(playbackTime, timeout: 8) { $0 <= 2 }
        _ = try waitForPlaybackValue(playbackTime, timeout: 10) { $0 >= replayedFrom + 1 }
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after Done")

        completed = true
        #else
        throw XCTSkip("The Duo orientation matrix applies only to iOS and iPadOS.")
        #endif
    }

    func testCompatibleHLSReplayRequestsFreshSession() async throws {
        #if os(iOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }

        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let apiSession = makeTestAPISession(origin: origin)
        defer { apiSession.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: apiSession)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 84)

        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.convertedItemID)
        let playbackTime = try startPlayback(using: "convertButton")
        let resumed = try waitForPlaybackValue(playbackTime, timeout: 15) { $0 >= 82 }
        XCTAssertGreaterThanOrEqual(resumed, 82, "Compatible playback did not resume near the seeded end position.")

        let replay = element("replayButton")
        try waitForExistence(replay, timeout: 25, named: "compatible replay button at end of media")
        try waitForExistence(element("playbackScreen"), timeout: 2, named: "compatible player retained at end of media")
        try assertPlayerControlFitsViewport(replay, named: "Compatible Replay")
        try assertPlayerControlFitsViewport(element("closePlayerButton"), named: "Compatible Done")
        try attachFrame(named: "Compatible HLS at end")

        try activate(replay)
        guard disappears(replay, timeout: 35) else {
            throw ProofFailure.ui("Compatible Replay did not prepare a fresh playback session.")
        }
        let replayedFrom = try waitForPlaybackValue(playbackTime, timeout: 10) { $0 <= 2 }
        let replayed = try waitForPlaybackValue(playbackTime, timeout: 15) { $0 >= replayedFrom + 3 }
        XCTAssertGreaterThanOrEqual(replayed, replayedFrom + 3, "Compatible replay did not advance from its reset position.")
        try attachFrame(named: "Compatible HLS replaying")

        try closePlayer()
        let saved = try await waitForServerProgress(
            api,
            identity: identity,
            itemID: fixture.convertedItemID,
            minimum: 2,
            maximum: 20
        )
        XCTAssertGreaterThan(saved, 2, "Compatible replay did not report its new watch position to the server.")
        XCTAssertLessThan(saved, 20, "Compatible replay retained the stale near-end server position.")

        completed = true
        #else
        throw XCTSkip("The compatible replay control regression applies only to iOS and iPadOS.")
        #endif
    }

    private func attachPlaybackFrame(named name: String) throws {
        let attachment = XCTAttachment(screenshot: try applicationWindow().screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func attachFrame(named name: String) throws {
        let window = try applicationWindow()
        let attachment = XCTAttachment(screenshot: window.screenshot())
        let frame = window.frame
        attachment.name = "\(name) — \(Int(frame.width))×\(Int(frame.height)) pt"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func captureScreenshot(succeeded: Bool) {
        guard let application else { return }
        let window = application.windows.firstMatch
        let screenshot = window.exists && !window.frame.isEmpty ? window.screenshot() : application.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = succeeded ? "Lumina native proof - success" : "Lumina native proof - failure"
        attachment.lifetime = .keepAlways
        add(attachment)
        if !succeeded {
            let hierarchy = XCTAttachment(string: application.debugDescription)
            hierarchy.name = "Native accessibility hierarchy"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
        }
    }

    private func applicationWindow() throws -> XCUIElement {
        let window = application!.windows.firstMatch
        try waitForExistence(window, timeout: 10, named: "main application window")
        guard !window.frame.isEmpty else {
            throw ProofFailure.ui("The main application window had an empty viewport frame.")
        }
        return window
    }

    private func applicationViewport() throws -> CGRect {
        try applicationWindow().frame
    }

    private func loadFixture() throws -> ProofFixture {
        let bundle = Bundle(for: Self.self)
        guard let url = bundle.url(forResource: "Fixture", withExtension: "json") else {
            throw XCTSkip(
                "Fixture.json is absent from the UI-test bundle. The proof harness must generate it with local serverURL, username, password, item ID, and item title strings."
            )
        }

        do {
            return try JSONDecoder().decode(ProofFixture.self, from: Data(contentsOf: url))
        } catch {
            throw ProofFailure.invalidFixture
        }
    }

    private func localOrigin(from value: String) throws -> URL {
        guard var components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              let host = components.host?.lowercased(),
              ["localhost", "127.0.0.1", "::1"].contains(host),
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              components.path.isEmpty || components.path == "/" else {
            throw ProofFailure.nonLocalServer
        }

        components.scheme = scheme
        components.path = ""
        guard let origin = components.url else { throw ProofFailure.nonLocalServer }
        return origin
    }

    private func makeTestAPISession(origin: URL) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 30
        return URLSession(configuration: configuration, delegate: ProofRedirectGuard(origin: origin), delegateQueue: nil)
    }

    private func launchAndResetSession() throws {
        let app = XCUIApplication()
        application = app
        app.launchArguments.append("--ui-testing")
        app.launch()

        let loginField = element("serverURLField")
        let library = element("libraryScreen")
        guard appears(loginField, timeout: 10) || appears(library, timeout: 30) else {
            throw ProofFailure.ui("The app did not reach either the connection form or the library screen after launch.")
        }

        if library.exists {
            let signOut = element("signOutButton")
            try waitForExistence(signOut, timeout: 10, named: "sign-out button")
            try activate(signOut)
            try waitForExistence(loginField, timeout: 20, named: "connection form after sign out")
        }
    }

    private func signIn(using fixture: ProofFixture) throws {
        let serverField = element("serverURLField")
        let usernameField = element("usernameField")
        let passwordField = element("passwordField")

        try waitForExistence(serverField, timeout: 20, named: "server URL field")
        #if os(iOS)
        try enter(fixture.serverURL, into: serverField)
        serverField.typeText("\n")
        try waitForExistence(usernameField, timeout: 10, named: "username field after Next")
        usernameField.typeText(fixture.username)
        usernameField.typeText("\n")
        try waitForExistence(passwordField, timeout: 10, named: "password field after Next")
        passwordField.typeText(fixture.password)
        // Exercise the keyboard's Go action, which stays reachable in Duo landscape.
        passwordField.typeText("\n")
        #else
        try enter(fixture.serverURL, into: serverField)
        try enter(fixture.username, into: usernameField)
        try enter(fixture.password, into: passwordField)
        try activate(element("connectButton"))
        #endif

        let library = element("libraryScreen")
        guard appears(library, timeout: 40) else {
            if element("connectionError").exists {
                throw ProofFailure.ui(element("connectionError").label)
            }
            throw ProofFailure.ui("Real form login did not reach the library screen.")
        }
    }

    private func openLibraryItem(id: String) throws {
        guard !id.isEmpty else { throw ProofFailure.invalidFixture }
        #if os(iOS)
        let item = try revealLibraryItem(id: id, named: "library item")
        #else
        let item = element("libraryItem-\(id)")
        try waitForExistence(item, timeout: 35, named: "library item")
        #endif
        try activate(item)
        try waitForExistence(element("detailScreen"), timeout: 25, named: "detail screen")
    }

    #if os(iOS)
    private func assertLibraryCardsStayInsideTheVisibleGrid(using fixture: ProofFixture) async throws {
        let direct = element("libraryItem-\(fixture.directItemID)")
        let converted = element("libraryItem-\(fixture.convertedItemID)")
        try waitForExistence(direct, timeout: 35, named: "long-title direct library card")
        try waitForExistence(converted, timeout: 35, named: "landscape-artwork library card")
        XCTAssertEqual(direct.elementType, .button, "A library card lost its native button role.")
        XCTAssertEqual(converted.elementType, .button, "A library card lost its native button role.")

        XCTAssertTrue(
            direct.label.contains(fixture.directItemTitle),
            "The complete direct-item title was not available from its library card."
        )
        XCTAssertTrue(
            converted.label.contains(fixture.convertedItemTitle),
            "The complete converted-item title was not available from its library card."
        )

        // The loopback artwork route is already verified by the fixture server.
        // Its generated rendition is warm before launch; allow the app's real
        // AsyncImage request to render, then require consecutive stable layouts.
        try await Task.sleep(for: .seconds(2))
        let (frames, isSingleColumn) = try await visibleStableFrames(of: [direct, converted], timeout: 12)
        let first = frames[0]
        let second = frames[1]
        let visibleLibrary = element("libraryScreen").frame.intersection(try applicationViewport())
        let tolerance: CGFloat = 1

        for frame in frames {
            XCTAssertFalse(frame.isEmpty, "A library card had no visible frame.")
            XCTAssertGreaterThanOrEqual(
                frame.minX,
                visibleLibrary.minX - tolerance,
                "A library card escaped the leading visible-library edge: card=\(frame), library=\(visibleLibrary)."
            )
            XCTAssertLessThanOrEqual(
                frame.maxX,
                visibleLibrary.maxX + tolerance,
                "A library card escaped the trailing visible-library edge: card=\(frame), library=\(visibleLibrary)."
            )
            let copyHeight = frame.height - (frame.width * 1.5)
            XCTAssertGreaterThan(
                copyHeight,
                max(32, frame.width * 0.15),
                "A library card did not reserve the expected portrait artwork and title area."
            )
            XCTAssertLessThan(
                copyHeight,
                max(100, frame.width * 0.6),
                "A library card grew beyond its portrait artwork and two-line title area."
            )
        }

        XCTAssertEqual(first.width, second.width, accuracy: tolerance, "Library columns rendered at different widths.")
        XCTAssertEqual(first.height, second.height, accuracy: tolerance, "Long-title library cards rendered at different heights.")
        if !isSingleColumn {
            let leading = first.minX <= second.minX ? first : second
            let trailing = first.minX <= second.minX ? second : first
            XCTAssertGreaterThanOrEqual(
                trailing.minX - leading.maxX,
                8,
                "Adjacent library cards overlapped instead of remaining in separate grid columns."
            )
        } else {
            XCTAssertEqual(first.minX, second.minX, accuracy: tolerance, "A single-column library was not leading-aligned.")
        }

        let attachment = XCTAttachment(screenshot: try applicationWindow().screenshot())
        attachment.name = "Long-title portrait and landscape artwork cards"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func visibleStableFrames(
        of elements: [XCUIElement],
        timeout: TimeInterval
    ) async throws -> ([CGRect], Bool) {
        let initial = try await stableFrames(of: elements, timeout: timeout)
        let isSingleColumn = abs(initial[0].minY - initial[1].minY) > 1
        if !isSingleColumn {
            try revealInLibraryViewport(elements)
            return (initial, false)
        }

        for element in elements {
            try revealInLibraryViewport([element])
        }
        return (initial, true)
    }

    private func revealInLibraryViewport(_ elements: [XCUIElement]) throws {
        let library = element("libraryScreen")
        let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        for _ in 0..<8 {
            let frames = elements.map(\.frame)
            let hasUsefulVisibleArea = frames.allSatisfy { frame in
                let visible = frame.intersection(viewport)
                return !visible.isNull && visible.height >= min(44, frame.height)
            }
            if hasUsefulVisibleArea { return }
            guard let leastVisible = frames.min(by: {
                $0.intersection(viewport).height < $1.intersection(viewport).height
            }) else { return }
            if leastVisible.midY >= viewport.midY {
                try scrollLibrary(library, up: true)
            } else {
                try scrollLibrary(library, up: false)
            }
        }
        throw ProofFailure.ui(
            "The long-title grid cards could not be scrolled into the visible library: "
                + "cards=\(elements.map(\.frame)), viewport=\(viewport)."
        )
    }

    private func stableFrames(of elements: [XCUIElement], timeout: TimeInterval) async throws -> [CGRect] {
        let deadline = Date().addingTimeInterval(timeout)
        var previous: [CGRect]?
        var stableSamples = 0
        while Date() < deadline {
            let current = elements.map(\.frame)
            if current.allSatisfy({ !$0.isEmpty }), framesEqual(current, previous, accuracy: 0.5) {
                stableSamples += 1
                if stableSamples >= 3 { return current }
            } else {
                stableSamples = 0
            }
            previous = current
            try await Task.sleep(for: .milliseconds(400))
        }
        throw ProofFailure.timeout("The long-title library cards did not settle into stable visible frames after artwork loaded.")
    }

    private func framesEqual(_ left: [CGRect], _ right: [CGRect]?, accuracy: CGFloat) -> Bool {
        guard let right, left.count == right.count else { return false }
        return zip(left, right).allSatisfy { lhs, rhs in
            abs(lhs.minX - rhs.minX) <= accuracy
                && abs(lhs.minY - rhs.minY) <= accuracy
                && abs(lhs.width - rhs.width) <= accuracy
                && abs(lhs.height - rhs.height) <= accuracy
        }
    }

    private func assertConnectionFieldsFitViewport(named name: String) throws {
        let fields = [element("serverURLField"), element("usernameField"), element("passwordField")]
        for field in fields {
            try waitForExistence(field, timeout: 10, named: "\(name) connection field")
            try assertHorizontalContainment(field.frame, in: applicationViewport(), named: "\(name) connection field")
        }
        try attachFrame(named: name)
    }

    private func assertMatrixLibraryItem(_ fixture: ProofFixture, named name: String) async throws {
        let item = try revealLibraryItem(id: fixture.matrixItemID, named: "\(name) matrix library item")
        XCTAssertEqual(item.elementType, .button, "\(name) library item lost its button role.")
        XCTAssertTrue(item.label.contains(fixture.matrixItemTitle), "\(name) library item lost its complete title.")
        try revealInLibraryViewport([item])
        let frame = try await stableFrames(of: [item], timeout: 8)[0]
        let viewport = element("libraryScreen").frame.intersection(try applicationViewport())
        try assertHorizontalContainment(frame, in: viewport, named: "\(name) library item")
        try attachFrame(named: name)
    }

    private func assertMatrixDetail(_ fixture: ProofFixture, named name: String) throws {
        let detail = element("detailScreen")
        let title = application!.staticTexts[fixture.matrixItemTitle].firstMatch
        let play = element("playButton")
        let convert = element("convertButton")
        try waitForExistence(title, timeout: 10, named: "\(name) complete title")
        try waitForExistence(play, timeout: 10, named: "\(name) play button")
        try waitForExistence(convert, timeout: 10, named: "\(name) convert button")

        try revealDetailElement(title, in: detail, named: "\(name) title", requiresHittable: false)
        try revealDetailElement(play, in: detail, named: "\(name) Play", requiresHittable: true)
        try revealDetailElement(convert, in: detail, named: "\(name) Convert", requiresHittable: true)
        let viewport = detail.frame.intersection(try applicationViewport())
        for (element, label) in [(title, "title"), (play, "Play"), (convert, "Convert")] {
            try assertHorizontalContainment(element.frame, in: viewport, named: "\(name) \(label)")
        }
        XCTAssertFalse(play.frame.intersects(convert.frame), "\(name) Play and Convert controls overlapped.")
        try attachFrame(named: name)
    }

    private func assertPlayerControlsFitViewport(named name: String) throws {
        let playback = element("playbackScreen")
        try waitForExistence(playback, timeout: 10, named: name)
        try assertPlayerControlFitsViewport(element("closePlayerButton"), named: "\(name) Done")
        try attachFrame(named: name)
    }

    private func assertPlayerControlFitsViewport(_ control: XCUIElement, named name: String) throws {
        try waitForExistence(control, timeout: 10, named: name)
        let viewport = element("playbackScreen").frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 1)
        try assertHorizontalContainment(control.frame, in: viewport, named: name)
        guard control.isHittable, control.isEnabled,
              isFullyVerticallyContained(control.frame, in: viewport) else {
            throw ProofFailure.ui("\(name) was not a usable control inside the player viewport: control=\(control.frame), viewport=\(viewport).")
        }
    }

    private func assertHorizontalContainment(_ frame: CGRect, in viewport: CGRect, named name: String) throws {
        let tolerance: CGFloat = 1
        guard !frame.isEmpty,
              frame.minX >= viewport.minX - tolerance,
              frame.maxX <= viewport.maxX + tolerance else {
            throw ProofFailure.ui("\(name) escaped horizontal viewport bounds: element=\(frame), viewport=\(viewport).")
        }
    }

    private func isFullyVerticallyContained(_ frame: CGRect, in viewport: CGRect) -> Bool {
        let tolerance: CGFloat = 1
        return !frame.isEmpty
            && frame.minY >= viewport.minY - tolerance
            && frame.maxY <= viewport.maxY + tolerance
    }

    private func revealDetailElement(
        _ element: XCUIElement,
        in detail: XCUIElement,
        named name: String,
        requiresHittable: Bool
    ) throws {
        let viewport = detail.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        for _ in 0..<8 {
            let frame = element.frame
            let visible = frame.intersection(viewport)
            let hasRequiredVisibility = requiresHittable
                ? isFullyVerticallyContained(frame, in: viewport)
                : !visible.isNull && visible.height >= min(44, frame.height)
            if (!requiresHittable || element.isHittable), element.isEnabled,
               hasRequiredVisibility {
                return
            }
            let anchor = self.element("playButton")
            let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : frame.midX
            try dragVertically(in: viewport, anchorX: anchorX, up: frame.midY >= viewport.midY)
        }
        throw ProofFailure.ui("\(name) could not be revealed: element=\(element.frame), viewport=\(viewport).")
    }

    private func revealLibraryItem(id: String, named name: String) throws -> XCUIElement {
        let item = element("libraryItem-\(id)")
        let library = element("libraryScreen")
        try waitForExistence(library, timeout: 15, named: "library containing \(name)")
        for _ in 0..<8 {
            if item.exists {
                try revealInLibraryViewport([item])
                return item
            }
            try scrollLibrary(library, up: true)
        }
        throw ProofFailure.timeout("The \(name) did not materialize after bounded library scrolling.")
    }

    private func scrollLibrary(_ library: XCUIElement, up: Bool) throws {
        // A deliberate vertical drag lets the outer scroll view win direction
        // locking even when it begins over the horizontal Continue Watching rail.
        let anchor = element("searchField")
        try waitForExistence(anchor, timeout: 5, named: "library search field scroll anchor")
        let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        try dragVertically(in: viewport, anchorX: anchor.frame.midX, up: up)
    }

    private func dragVertically(in viewport: CGRect, anchorX: CGFloat, up: Bool) throws {
        let window = try applicationWindow()
        let visibleViewport = viewport.intersection(window.frame)
        guard !visibleViewport.isEmpty, visibleViewport.height >= 44 else {
            throw ProofFailure.ui("The vertical scroll viewport was empty: viewport=\(viewport), window=\(window.frame).")
        }

        let insetX = min(max(anchorX, visibleViewport.minX + 2), visibleViewport.maxX - 2)
        let startY = visibleViewport.minY + visibleViewport.height * (up ? 0.82 : 0.18)
        let endY = visibleViewport.minY + visibleViewport.height * (up ? 0.18 : 0.82)
        let normalizedX = (insetX - window.frame.minX) / window.frame.width
        let normalizedStartY = (startY - window.frame.minY) / window.frame.height
        let normalizedEndY = (endY - window.frame.minY) / window.frame.height
        let start = window.coordinate(withNormalizedOffset: CGVector(dx: normalizedX, dy: normalizedStartY))
        let end = window.coordinate(withNormalizedOffset: CGVector(dx: normalizedX, dy: normalizedEndY))
        start.press(forDuration: 0.05, thenDragTo: end)
    }

    private func resumeNativePlayer(_ playbackTime: XCUIElement, pausedAt: Double) throws -> Double {
        let player = element("nativeVideoPlayer")
        try waitForExistence(player, timeout: 10, named: "native player after returning from Home")
        player.tap()
        if let advanced = try? waitForPlaybackValue(playbackTime, timeout: 4, where: { $0 >= pausedAt + 1 }) {
            return advanced
        }

        let play = player.descendants(matching: .button).matching(
            NSPredicate(format: "label == %@ OR label == %@", "Play", "Resume")
        ).firstMatch
        try waitForExistence(play, timeout: 8, named: "native Play control")
        try activate(play)
        return try waitForPlaybackValue(playbackTime, timeout: 10) { $0 >= pausedAt + 1 }
    }

    private func backgroundApplication() throws {
        let app = application!
        XCUIApplication(bundleIdentifier: "com.apple.springboard").activate()
        guard waitUntil(timeout: 8, condition: {
            app.state == .runningBackground || app.state == .runningBackgroundSuspended
        }) else {
            throw ProofFailure.ui("Lumina remained in the foreground after SpringBoard activation: state=\(app.state.rawValue).")
        }
    }
    #endif

    private func startPlayback(using buttonIdentifier: String) throws -> XCUIElement {
        let button = element(buttonIdentifier)
        try waitForExistence(button, timeout: 20, named: buttonIdentifier)
        #if os(iOS)
        try revealDetailControl(button, named: buttonIdentifier)
        #endif
        try activate(button)
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "native playback screen")
        let playbackTime = element("playbackTime")
        try waitForExistence(playbackTime, timeout: 20, named: "playback time")
        return playbackTime
    }

    #if os(iOS)
    private func revealDetailControl(_ control: XCUIElement, named name: String) throws {
        let detail = element("detailScreen")
        try waitForExistence(detail, timeout: 10, named: "detail screen containing \(name)")
        let viewport = detail.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)

        for _ in 0..<8 {
            let frame = control.frame
            if control.isHittable,
               control.isEnabled,
               isFullyVerticallyContained(frame, in: viewport) {
                return
            }
            let anchor = element("playButton")
            let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : frame.midX
            try dragVertically(in: viewport, anchorX: anchorX, up: frame.midY >= viewport.midY)
        }

        throw ProofFailure.ui(
            "The \(name) control could not be revealed in its detail screen: "
                + "control=\(control.frame), viewport=\(viewport), hittable=\(control.isHittable)."
        )
    }

    private func revealConnectionField(_ field: XCUIElement) throws {
        let form = application!.scrollViews.firstMatch
        try waitForExistence(form, timeout: 10, named: "connection form scroll view")
        let viewport = form.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        for _ in 0..<8 {
            if field.isHittable { return }
            guard !field.frame.isEmpty else {
                throw ProofFailure.ui("A connection field had no frame to anchor its reveal gesture.")
            }
            try dragVertically(in: viewport, anchorX: field.frame.midX, up: field.frame.midY >= viewport.midY)
        }
        throw ProofFailure.ui("A connection field could not be revealed: field=\(field.frame), viewport=\(viewport).")
    }
    #endif

    private func closePlayer() throws {
        #if os(tvOS)
        let playbackScreen = element("playbackScreen")
        try waitForExistence(playbackScreen, timeout: 10, named: "playback screen before close")
        XCUIRemote.shared.press(.menu)
        if !disappears(playbackScreen, timeout: 8) {
            XCUIRemote.shared.press(.menu)
        }
        guard disappears(playbackScreen, timeout: 8) else {
            throw ProofFailure.ui("The tvOS menu button did not close the native player.")
        }
        #else
        let closeButton = element("closePlayerButton")
        try waitForExistence(closeButton, timeout: 10, named: "iOS close player button")
        try activate(closeButton)
        guard disappears(element("playbackScreen"), timeout: 10) else {
            throw ProofFailure.ui("The iOS close player button did not dismiss the native player.")
        }
        #endif
    }

    private func returnToLibrary() throws {
        #if os(tvOS)
        let detail = element("detailScreen")
        try waitForExistence(detail, timeout: 15, named: "detail screen before returning to library")
        XCUIRemote.shared.press(.menu)
        #else
        let backButton = application!.buttons.matching(identifier: "BackButton").firstMatch
        try waitForExistence(backButton, timeout: 15, named: "navigation back button")
        try activate(backButton)
        #endif
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "library screen")
    }

    private func refreshLibrary(waitingForResumeOf itemID: String? = nil) throws {
        try activate(element("refreshButton"))
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "refreshed library screen")
        guard let itemID else { return }

        #if os(tvOS)
        let resumeItem = application!.descendants(matching: .any).matching(identifier: "libraryItem-\(itemID)")
        guard waitUntil(timeout: 20, condition: { resumeItem.count >= 2 }) else {
            throw ProofFailure.timeout("The refreshed tvOS library did not expose the direct fixture in Continue Watching.")
        }
        #else
        try waitForExistence(element("resumeItem-\(itemID)"), timeout: 20, named: "resumed library item")
        #endif
    }

    private func enter(_ text: String, into field: XCUIElement) throws {
        try waitForExistence(field, timeout: 15, named: "text field")
        #if os(tvOS)
        guard focus(field, timeout: 15) else {
            throw ProofFailure.ui("The tvOS remote could not focus a connection field.")
        }
        #else
        try revealConnectionField(field)
        try waitForHittable(field, timeout: 10, named: "text field")
        field.tap()
        #endif
        #if os(tvOS)
        XCUIRemote.shared.press(.select)
        #endif
        field.typeText(text)
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #endif
    }

    private func activate(_ control: XCUIElement) throws {
        #if os(tvOS)
        guard focus(control, timeout: 20) else {
            throw ProofFailure.ui("The tvOS remote could not focus a control.")
        }
        XCUIRemote.shared.press(.select)
        #else
        try waitForHittable(control, timeout: 15, named: "control")
        control.tap()
        #endif
    }

    private func waitForExistence(_ element: XCUIElement, timeout: TimeInterval, named name: String) throws {
        guard appears(element, timeout: timeout) else {
            throw ProofFailure.timeout("Timed out waiting for \(name).")
        }
    }

    private func waitForHittable(_ element: XCUIElement, timeout: TimeInterval, named name: String) throws {
        let predicate = NSPredicate { [self] _, _ in
            #if os(iOS)
            // AutoFill can present after navigation or after this wait starts.
            // Never save the disposable fixture password in the system vault.
            let prompt = application!.sheets["Save Password?"]
            let decline = application!.buttons["Not Now"]
            if (prompt.exists || application!.staticTexts["Save Password?"].exists), decline.exists, decline.isHittable {
                decline.tap()
            }
            #endif
            return element.isHittable && element.isEnabled
        }
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: nil)
        guard XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed else {
            throw ProofFailure.timeout("Timed out waiting for hittable \(name).")
        }
    }

    private func appears(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == true")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }

    private func disappears(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }

    private func waitForPlaybackValue(
        _ element: XCUIElement,
        timeout: TimeInterval,
        where condition: @escaping (Double) -> Bool
    ) throws -> Double {
        var latestValue: Double?
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            guard let value = Self.numericAccessibilityValue(of: element) else { return false }
            guard condition(value) else { return false }
            latestValue = value
            return true
        }, object: nil)

        guard XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed,
              let latestValue else {
            throw ProofFailure.timeout("The playback time accessibility value did not reach the expected advancing value.")
        }
        return latestValue
    }

    private static func numericAccessibilityValue(of element: XCUIElement) -> Double? {
        if let number = element.value as? NSNumber { return number.doubleValue }
        if let string = element.value as? String, let number = Double(string.trimmingCharacters(in: .whitespacesAndNewlines)) {
            return number
        }
        if let number = Double(element.label.trimmingCharacters(in: .whitespacesAndNewlines)) {
            return number
        }
        return nil
    }

    private func element(_ identifier: String) -> XCUIElement {
        application!.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    #if os(tvOS)
    private func focus(_ target: XCUIElement, timeout: TimeInterval) -> Bool {
        if waitUntil(timeout: min(2, timeout)) { return true }

        let directions: [XCUIRemote.Button] = [.up, .down, .left, .right]
        let deadline = Date().addingTimeInterval(timeout)
        var step = 0
        while Date() < deadline {
            if target.hasFocus { return true }
            let direction: XCUIRemote.Button
            if let current = focusedElement() {
                direction = directionToward(target, from: current, fallback: step, directions: directions)
            } else {
                direction = directions[step % directions.count]
            }
            XCUIRemote.shared.press(direction)
            step += 1
            if waitUntil(timeout: min(1.5, max(0, deadline.timeIntervalSinceNow))) { return true }
        }
        return target.hasFocus

        func waitUntil(timeout: TimeInterval) -> Bool {
            guard timeout > 0 else { return target.hasFocus }
            return self.waitUntil(timeout: timeout) { target.hasFocus }
        }
    }

    private func focusedElement() -> XCUIElement? {
        let query = application!.descendants(matching: .any).matching(NSPredicate(format: "hasFocus == true"))
        let focused = query.firstMatch
        return focused.exists ? focused : nil
    }

    private func directionToward(
        _ target: XCUIElement,
        from current: XCUIElement,
        fallback step: Int,
        directions: [XCUIRemote.Button]
    ) -> XCUIRemote.Button {
        let targetFrame = target.frame
        let currentFrame = current.frame
        if !targetFrame.isEmpty && !currentFrame.isEmpty {
            if targetFrame.minY > currentFrame.maxY { return .down }
            if targetFrame.maxY < currentFrame.minY { return .up }
            if targetFrame.minX > currentFrame.maxX { return .right }
            if targetFrame.maxX < currentFrame.minX { return .left }
        }
        return directions[step % directions.count]
    }
    #endif
}

private struct ProofFixture: Decodable {
    let serverURL: String
    let username: String
    let password: String
    let directItemID: String
    let directItemTitle: String
    let convertedItemID: String
    let convertedItemTitle: String
    let matrixItemID: String
    let matrixItemTitle: String
}

private struct ProofAPI {
    let origin: URL
    let session: URLSession

    func authenticate(username: String, password: String) async throws -> APIIdentity {
        var request = URLRequest(url: origin.appendingPathComponent("Users").appendingPathComponent("AuthenticateByName"))
        request.httpMethod = "POST"
        request.setValue("MediaBrowser Client=\"Lumina proof tests\",Device=\"Simulator\",DeviceId=\"native-proof-verifier\",Version=\"0.1\"", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["Username": username, "Pw": password])

        let data = try await send(request)
        do {
            let response = try JSONDecoder().decode(AuthenticationResponse.self, from: data)
            guard !response.token.isEmpty, !response.user.id.isEmpty else { throw ProofAPIError.malformedResponse }
            return APIIdentity(token: response.token, userID: response.user.id)
        } catch let error as ProofAPIError {
            throw error
        } catch {
            throw ProofAPIError.malformedResponse
        }
    }

    // Each platform journey starts from the same disposable fixture state,
    // including a nonzero resume point. Only the public server API is used.
    func seedProgress(identity: APIIdentity, itemID: String, seconds: Int) async throws {
        var clear = URLRequest(url: origin.appendingPathComponent("Users/\(identity.userID)/Items/\(itemID)/UserData"))
        clear.httpMethod = "POST"
        clear.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        clear.setValue("application/json", forHTTPHeaderField: "Content-Type")
        clear.httpBody = try JSONSerialization.data(withJSONObject: ["Played": false])
        _ = try await send(clear)
        guard seconds > 0 else { return }
        var progress = URLRequest(url: origin.appendingPathComponent("Sessions/Playing/Progress"))
        progress.httpMethod = "POST"
        progress.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        progress.setValue("application/json", forHTTPHeaderField: "Content-Type")
        progress.httpBody = try JSONSerialization.data(withJSONObject: ["ItemId": itemID, "PositionTicks": seconds * 10_000_000, "IsPaused": true])
        _ = try await send(progress)
    }

    func playbackSeconds(identity: APIIdentity, itemID: String) async throws -> Double {
        var request = URLRequest(
            url: origin
                .appendingPathComponent("Users")
                .appendingPathComponent(identity.userID)
                .appendingPathComponent("Items")
                .appendingPathComponent(itemID)
        )
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")

        let data = try await send(request)
        do {
            let response = try JSONDecoder().decode(ItemResponse.self, from: data)
            return Double(response.userData?.playbackPositionTicks ?? 0) / 10_000_000
        } catch {
            throw ProofAPIError.malformedResponse
        }
    }

    private func send(_ request: URLRequest) async throws -> Data {
        do {
            let (data, response) = try await session.data(for: request)
            guard let response = response as? HTTPURLResponse else { throw ProofAPIError.malformedResponse }
            guard (200..<300).contains(response.statusCode) else { throw ProofAPIError.httpStatus(response.statusCode) }
            return data
        } catch let error as ProofAPIError {
            throw error
        } catch {
            throw ProofAPIError.transport
        }
    }
}

private struct APIIdentity {
    let token: String
    let userID: String
}

private struct AuthenticationResponse: Decodable {
    let token: String
    let user: AuthenticatedUser

    enum CodingKeys: String, CodingKey {
        case token = "AccessToken"
        case user = "User"
    }
}

private struct AuthenticatedUser: Decodable {
    let id: String

    enum CodingKeys: String, CodingKey {
        case id = "Id"
    }
}

private struct ItemResponse: Decodable {
    let userData: APIUserData?

    enum CodingKeys: String, CodingKey {
        case userData = "UserData"
    }
}

private struct APIUserData: Decodable {
    let playbackPositionTicks: Int64

    enum CodingKeys: String, CodingKey {
        case playbackPositionTicks = "PlaybackPositionTicks"
    }
}

private enum ProofFailure: Error {
    case invalidFixture
    case nonLocalServer
    case ui(String)
    case timeout(String)
}

private enum ProofAPIError: Error {
    case httpStatus(Int)
    case malformedResponse
    case transport
}

private final class ProofRedirectGuard: NSObject, URLSessionTaskDelegate {
    private let origin: URL

    init(origin: URL) {
        self.origin = origin
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        guard let from = task.currentRequest?.url,
              let to = request.url,
              sameOrigin(from, to),
              sameOrigin(origin, to) else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}

private func sameOrigin(_ left: URL, _ right: URL) -> Bool {
    let a = URLComponents(url: left, resolvingAgainstBaseURL: false)
    let b = URLComponents(url: right, resolvingAgainstBaseURL: false)
    return a?.scheme?.lowercased() == b?.scheme?.lowercased()
        && a?.host?.lowercased() == b?.host?.lowercased()
        && effectivePort(a) == effectivePort(b)
}

private func effectivePort(_ components: URLComponents?) -> Int? {
    guard let components else { return nil }
    if let port = components.port { return port }
    switch components.scheme?.lowercased() {
    case "http": return 80
    case "https": return 443
    default: return nil
    }
}

private extension NativeProofTests {
    func waitForServerProgress(
        _ api: ProofAPI,
        identity: APIIdentity,
        itemID: String,
        minimum: Double = 2,
        maximum: Double? = nil
    ) async throws -> Double {
        let deadline = Date().addingTimeInterval(20)
        var latest = 0.0
        while Date() < deadline {
            latest = try await api.playbackSeconds(identity: identity, itemID: itemID)
            if latest > minimum, maximum.map({ latest < $0 }) ?? true { return latest }
            try await Task.sleep(for: .milliseconds(250))
        }
        let range = maximum.map { " above \(minimum) and below \($0) seconds" } ?? " above \(minimum) seconds"
        throw ProofFailure.timeout("The server did not report watch progress\(range); latest=\(latest).")
    }

    func waitUntil(timeout: TimeInterval, condition: @escaping () -> Bool) -> Bool {
        guard timeout > 0 else { return condition() }
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: nil)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }
}
