import Foundation
import XCTest
import LuminaCore
#if os(tvOS)
import CoreImage
#endif
#if os(iOS)
import AVFoundation
#endif
#if canImport(UIKit)
import UIKit
#endif
#if canImport(Vision)
import Vision
#endif

extension NativeProofJourney {
    func journeyNativeStreaming(remoteEnabled: Bool) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        guard let youtubeID = fixture.savedYouTubeID, let twitchID = fixture.savedTwitchID else {
            throw ProofFailure.ui("The streaming workflow requires its isolated saved-provider fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let api = ProofAPI(origin: origin, session: makeTestAPISession(origin: origin))
        try await api.setStreamingFixture(remoteEnabled ? "on" : "off")
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        #if os(iOS)
        let streaming = destinationControl("Streaming")
        if !remoteEnabled {
            try waitForExistence(destinationControl("Home"), timeout: 20, named: "Home destination")
            XCTAssertFalse(streaming.exists, "The dedicated Streaming tab requires server opt-in.")
            XCTAssertEqual(application!.descendants(matching: .any).matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "homeExternal-")
            ).count, 0, "Disabled streaming must not appear on Home.")
            try selectLibraryCategory("streaming", screen: "savedStreamingLibrary")
        } else {
            try waitForExistence(streaming, timeout: 20, named: "Streaming destination")
            guard hasVisibleFrame(streaming) else { throw ProofFailure.ui("Streaming destination must be visible.") }
            try activate(streaming)
            let screen = element("streamingScreen")
            if !waitUntil(timeout: 4, condition: { screen.exists }) {
                let viewport = try applicationViewport()
                let frame = streaming.frame
                try applicationWindow().coordinate(withNormalizedOffset: CGVector(
                    dx: (frame.midX - viewport.minX) / viewport.width,
                    dy: (frame.midY - viewport.minY) / viewport.height
                )).tap()
            }
            try waitForExistence(screen, timeout: 25, named: "Streaming destination")
        }
        #else
        if remoteEnabled {
            try selectTVLibraryCategory("streaming", screen: "streamingScreen")
        } else {
            XCTAssertFalse(element("homeBrowse-streaming").exists)
            try selectTVLibraryCategory("savedStreaming", screen: "savedStreamingLibrary")
        }
        #endif
        if remoteEnabled {
            try waitForExistence(element("streamingRecommendationsTab"), timeout: 25, named: "configured recommendations")
            try waitForExistence(element("streamingLiveTab"), timeout: 20, named: "configured Live")
            try activateStreamingTab("streamingLiveTab")
            guard let liveID = fixture.streamingLiveID else { throw ProofAPIError.malformedResponse }
            try waitForExistence(element("streamingRemote-\(liveID)"), timeout: 25, named: "live source")
            try attachFrame(named: "Configured Streaming live sources")
            try activateStreamingTab("streamingRecommendationsTab")
            guard let recommendationID = fixture.streamingRecommendationID else { throw ProofAPIError.malformedResponse }
            try waitForExistence(element("streamingRemote-\(recommendationID)"), timeout: 25, named: "recommended source")
            try attachFrame(named: "Configured Streaming recommendations")
            try activateStreamingCard("streamingRemote-\(recommendationID)")
            try waitForExistence(element("nativeRemotePlaybackScreen"), timeout: 30, named: "server-owned remote player")
            var receivedBytes = false
            for _ in 0..<20 {
                if try await api.streamingMetrics()["bytes_served", default: 0] > 0 { receivedBytes = true; break }
                try await Task.sleep(for: .milliseconds(500))
            }
            XCTAssertTrue(receivedBytes, "AVPlayer must request the server-relayed media bytes.")
            try attachFrame(named: "Native server streaming playback")
            #if os(iOS)
            let remoteTime = element("nativeRemotePlaybackTime")
            var position = try waitForPlaybackValue(remoteTime, timeout: 15) { $0 >= 3 }
            defer { XCUIDevice.shared.orientation = .portrait }
            for orientation: UIDeviceOrientation in [.landscapeLeft, .portrait] {
                XCUIDevice.shared.orientation = orientation
                let window = try applicationWindow()
                XCTAssertTrue(waitUntil(timeout: 12) {
                    (window.frame.width > window.frame.height) == orientation.isLandscape
                }, "The player window must adopt the new orientation.")
                let metrics = try await api.streamingMetrics()
                XCTAssertEqual(metrics["sessions_released", default: 0], 0, "Rotation must keep the same server stream.")
                position = try waitForPlaybackValue(remoteTime, timeout: 10) { $0 >= position + 1 }
            }
            try activate(element("nativeRemoteCloseButton"))
            #else
            XCUIRemote.shared.press(.menu)
            if !disappears(element("nativeRemotePlaybackScreen"), timeout: 8) { XCUIRemote.shared.press(.menu) }
            guard disappears(element("nativeRemotePlaybackScreen"), timeout: 8) else {
                throw ProofFailure.ui("Remote Back did not close streaming playback.")
            }
            #endif
            try waitForExistence(element("streamingScreen"), timeout: 20, named: "return from remote playback")
            var released = false
            for _ in 0..<20 {
                if try await api.streamingMetrics()["sessions_released", default: 0] > 0 { released = true; break }
                try await Task.sleep(for: .milliseconds(500))
            }
            XCTAssertTrue(released, "Closing the remote player must release its server session.")
        } else {
            XCTAssertFalse(element("streamingRecommendationsTab").exists)
            XCTAssertFalse(element("streamingLiveTab").exists)
        }
        if remoteEnabled { try activateStreamingTab("streamingDownloadedTab") }
        try waitForExistence(element("streamingSaved-\(youtubeID)"), timeout: 25, named: "saved YouTube video")
        try waitForExistence(element("streamingSaved-\(twitchID)"), timeout: 25, named: "saved Twitch video")
        try attachFrame(named: remoteEnabled ? "Configured Streaming saved library" : "Saved Streaming without external opt-in")
        try activateStreamingCard("streamingSaved-\(youtubeID)")
        guard waitUntil(timeout: 25, condition: {
            self.element("playbackScreen").exists || self.element("playButton").exists || self.element("resumePlaybackChoice").exists
        }) else { throw ProofFailure.timeout("Saved video did not open its playback action or player.") }
        if !element("playbackScreen").exists && !element("resumePlaybackChoice").exists { try activate(element("playButton")) }
        guard waitUntil(timeout: 30, condition: {
            self.element("playbackScreen").exists || self.element("resumePlaybackChoice").exists
        }) else { throw ProofFailure.timeout("Saved video did not prepare playback.") }
        if element("resumePlaybackChoice").exists { try activate(element("resumePlaybackChoice").buttons["Resume"]) }
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "saved provider video playback")
        try closePlayer()
        completed = true
    }

    #if os(tvOS)
    func readTVPairingBarcode(_ image: CGImage) throws -> CIQRCodeFeature {
        let scan = try XCTUnwrap(CIDetector(ofType: CIDetectorTypeQRCode, context: nil,
                                          options: [CIDetectorAccuracy: CIDetectorAccuracyHigh]))
        return try XCTUnwrap(scan.features(in: CIImage(cgImage: image)).first as? CIQRCodeFeature,
                                    "The rendered TV code must be scannable.")
    }

    func journeyTVPhoneEnrollment() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let api = ProofAPI(origin: origin, session: makeTestAPISession(origin: origin))
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password,
                                                  deviceID: "proof-enrolled-phone-\(isolationID)")
        try launchAndResetSession()
        XCTAssertFalse(element("serverURLField").exists)
        XCTAssertFalse(element("passwordField").exists)
        try attachFrame(named: "Apple TV welcome")
        try activate(element("tvPairingStartButton"))
        let qr = element("tvPairingQRCode")
        try waitForExistence(qr, timeout: 15, named: "Apple TV pairing QR before server entry")
        XCTAssertFalse(element("serverURLField").exists)
        try attachFrame(named: "Apple TV phone sign-in QR")
        let image = try XCTUnwrap(qr.screenshot().image.cgImage)
        let barcode = try readTVPairingBarcode(image)
        XCTAssertEqual(barcode.bounds.midX / CGFloat(image.width), 0.5, accuracy: 0.025,
                       "The QR must be centered horizontally.")
        XCTAssertEqual(barcode.bounds.midY / CGFloat(image.height), 0.5, accuracy: 0.025,
                       "The QR must be centered vertically.")
        let link = try XCTUnwrap(barcode.messageString.flatMap { URL(string: $0) })
        let approval = try await TVPairingHandoff(link: link).requestApproval(serverURL: origin)
        let code = approval.code
        try waitForExistence(element("tvPairingCode"), timeout: 15, named: "Apple TV confirmation code")
        XCTAssertEqual(element("tvPairingCode").label, code)
        try attachFrame(named: "Apple TV awaits phone approval")
        let preview = try await api.devicePairing("Inspect", identity: identity, body: ["code": code])
        XCTAssertEqual(preview["code"] as? String, code)
        XCTAssertEqual(preview["status"] as? String, "pending")
        let approved = try await api.devicePairing("Approve", identity: identity, body: ["code": code])
        XCTAssertEqual(approved["status"] as? String, "approved")
        try waitForExistence(element("homeScreen"), timeout: 40, named: "TV automatically signed in")
        let stillPhone = try await api.settingsJSON(identity: identity)
        XCTAssertFalse(stillPhone.isEmpty, "Approving the TV must leave the enrolled phone credential usable.")
        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "TV enrollment persists after relaunch")
        completed = true
    }
    #else
    func journeyPhoneApprovesTV(useDeepLink: Bool, nearby: Bool = false) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let api = ProofAPI(origin: origin, session: makeTestAPISession(origin: origin))
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        let pending = try await api.devicePairing("Start", body: ["device_name": "Proof Living Room Apple TV"])
        let request = try JSONDecoder().decode(TVPairingRequest.self, from: JSONSerialization.data(withJSONObject: pending))
        let code = request.code
        let id = request.id
        let secret = request.deviceSecret
        let serverID = request.serverID
        if nearby {
            let handoff = TVPairingHandoff()
            let tv = Task {
                try await handoff.awaitPhone { receivedOrigin in
                    XCTAssertEqual(receivedOrigin, origin, "The phone supplies its authenticated server origin.")
                    return request
                }
            }
            defer { tv.cancel() }
            application!.open(handoff.link)
            let approval = element("tvPairingApprovalScreen")
            let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
            let allowLocalNetwork = springboard.alerts.buttons["Allow"]
            for _ in 0..<80 {
                if approval.exists { break }
                if allowLocalNetwork.exists { allowLocalNetwork.tap() }
                try await Task.sleep(for: .milliseconds(250))
            }
            guard approval.exists else { throw ProofFailure.timeout("The nearby QR did not open the approval screen.") }
            try await tv.value
        } else if useDeepLink {
            let payload: [String: Any] = ["type": "lumina-tv-pair", "version": 1,
                                        "server_url": origin.absoluteString, "server_id": serverID, "code": code]
            var components = URLComponents()
            components.scheme = "lumina"
            components.host = "approve-tv"
            components.fragment = String(data: try JSONSerialization.data(withJSONObject: payload), encoding: .utf8)
            guard let link = components.url else { throw ProofAPIError.malformedResponse }
            application!.open(link)
        } else {
            let account = destinationControl("Account")
            try waitForExistence(account, timeout: 20, named: "Account destination")
            guard hasVisibleFrame(account) else { throw ProofFailure.ui("Account must be visible before navigating.") }
            account.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            try waitForExistence(element("accountScreen"), timeout: 20, named: "Account settings")
            let search = element("settingsSearchField")
            try waitForExistence(search, timeout: 15, named: "Account settings search")
            let searchFrame = search.frame
            let window = try applicationWindow()
            guard hasVisibleFrame(search), !searchFrame.isEmpty else {
                throw ProofFailure.ui("Account settings search must be visible before filtering for TV.")
            }
            window.coordinate(withNormalizedOffset: CGVector(
                dx: (searchFrame.midX - window.frame.minX) / window.frame.width,
                dy: (searchFrame.midY - window.frame.minY) / window.frame.height
            )).tap()
            application!.typeText("tv\n")
            let connectTV = element("connectTVButton")
            try waitForExistence(connectTV, timeout: 15, named: "Connect a TV action")
            let connectTVFrame = connectTV.frame
            guard hasVisibleFrame(connectTV), !connectTVFrame.isEmpty, connectTV.isEnabled else {
                throw ProofFailure.ui("Connect a TV must be visible after filtering Account settings.")
            }
            window.coordinate(withNormalizedOffset: CGVector(
                dx: (connectTVFrame.midX - window.frame.minX) / window.frame.width,
                dy: (connectTVFrame.midY - window.frame.minY) / window.frame.height
            )).tap()
            try waitForExistence(element("tvPairingCodeField"), timeout: 15, named: "manual TV pairing code")
            try enter(code, into: element("tvPairingCodeField"))
            element("tvPairingCodeField").typeText("\n")
        }
        try waitForExistence(element("tvPairingApprovalScreen"), timeout: 25, named: "explicit TV approval")
        try waitForExistence(application!.staticTexts["Proof Living Room Apple TV"], timeout: 15, named: "TV identity before approval")
        try attachFrame(named: useDeepLink ? "Signed-in phone opens TV QR approval link" : "Signed-in phone approves Apple TV")
        try activate(element(useDeepLink ? "tvPairingDenyButton" : "tvPairingApproveButton"))
        guard waitUntil(timeout: 20, condition: {
            self.element("tvPairingApprovalStatus").label == (useDeepLink ? "Denied" : "Approved")
        }) else { throw ProofFailure.timeout("The explicit phone decision did not complete.") }
        try waitForExistence(element("tvPairingDoneButton"), timeout: 20, named: "pairing decision complete")
        let result = try await api.devicePairing("Status", body: ["id": id, "device_secret": secret])
        XCTAssertEqual(result["status"] as? String, useDeepLink ? "denied" : "approved")
        try activate(element("tvPairingDoneButton"))
        try selectDestination("Home", screen: "homeScreen")
        XCTAssertFalse(element("qrPairingLanding").exists, "The existing phone enrollment must stay signed in.")
        completed = true
    }
    #endif

    #if os(iOS)
    func journeyActorLibrary() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        // The simulator may report visible poster links as non-hittable; verify their
        // bounds and exercise the same on-screen tap rather than bypassing navigation.
        func tapVisible(_ control: XCUIElement) throws {
            try waitForExistence(control, timeout: 20, named: "visible navigation control")
            let visible = control.frame.intersection(try applicationViewport())
            guard visible.width >= 44, visible.height >= 44 else { throw ProofFailure.ui("Navigation target is outside the viewport") }
            application!.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: visible.midX, dy: visible.midY)).tap()
        }
        try selectTourVideoCategory("movies")
        let original = try revealLibraryItem(id: fixture.directItemID, named: "original movie")
        try tapVisible(original)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "original title detail")
        let actor = element("detailActor-Synthetic Performer")
        try revealDetailElement(actor, in: element("detailScreen"), named: "actor link", requiresHittable: false)
        try tapVisible(actor)
        try waitForExistence(element("actorLibraryScreen"), timeout: 20, named: "actor library")
        let film = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(film, timeout: 25, named: "actor's movie")
        let attachment = XCTAttachment(screenshot: application!.screenshot())
        attachment.name = "actor-library-grid"
        attachment.lifetime = .keepAlways
        add(attachment)
        try tapVisible(film)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "filtered title detail")
        if usesStandardPhonePushedChrome { try musicBack() }
        else { try tapVisible(application!.buttons.matching(identifier: "BackButton").firstMatch) }
        try waitForExistence(element("actorLibraryScreen"), timeout: 20, named: "retained actor results")
        if usesStandardPhonePushedChrome { try musicBack() }
        else { try tapVisible(application!.buttons.matching(identifier: "BackButton").firstMatch) }
        try waitForExistence(element("detailScreen"), timeout: 20, named: "original title detail")
        completed = true
    }

    func journeyCollectionFilter() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        guard let franchiseID = fixture.franchiseCollectionID,
              let manualID = fixture.manualCollectionID,
              let smartID = fixture.smartCollectionID else { throw XCTSkip("Collections fixture required") }
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        XCTAssertFalse(element("libraryCategory-collections").exists)
        let screen = element("libraryScreen")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "Unfiltered movie library")
        try activate(element("videoOptionsMenu"))
        try activate(element("videoFiltersButton"))
        try activate(element("videoCollectionFilterButton"))
        try waitForExistence(element("collectionFilterPicker"), timeout: 15, named: "Collection filter picker")
        for id in [franchiseID, manualID, smartID] {
            try waitForExistence(element("collectionFilter-\(id)"), timeout: 25, named: "Collection filter option")
        }
        XCTAssertGreaterThan(element("collectionFilterPicker").frame.height, try applicationViewport().height * 0.7,
                             "The visual picker must have enough space for artwork and labels.")
        try attachFrame(named: "Artwork-based collection filter within Movies")
        try activate(element("collectionFilter-\(franchiseID)"))
        try activate(element("videoApplyFiltersButton"))
        try waitForExistence(element("videoActiveCollectionFilter"), timeout: 15, named: "Active franchise filter")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "Movie in selected franchise")
        XCTAssertTrue(screen.exists)
        XCTAssertFalse(element("movieCollectionScreen").exists)
        try attachFrame(named: "Selected collection retains the ordinary movie grid")
        try activate(element("videoClearCollectionFilterButton"))
        try waitForExistence(element("libraryItem-\(fixture.convertedItemID)"), timeout: 25, named: "All movies restored after clearing collection")
        completed = true
    }
    #endif

    #if os(tvOS)
    func journeyTVCollectionFilter() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        guard let franchiseID = fixture.franchiseCollectionID,
              let manualID = fixture.manualCollectionID,
              let smartID = fixture.smartCollectionID else {
            throw ProofFailure.ui("The TV collection-filter fixture must be enabled.")
        }
        try launchAndResetSession()
        try signIn(using: fixture)
        XCTAssertFalse(element("homeBrowse-collections").exists, "Collections belong in Movies filters, not primary navigation.")
        try selectTVLibraryCategory("movies", screen: "libraryScreen")
        try openTVLibraryBrowse()
        try activate(element("videoCollectionFilterButton"))
        try waitForExistence(element("collectionFilter-any"), timeout: 20, named: "TV collection filter picker")
        for id in [franchiseID, manualID, smartID] {
            try waitForExistence(element("collectionFilter-\(id)"), timeout: 25, named: "visible collection filter option")
        }
        try attachFrame(named: "TV collection choices from the Movies toolbar")
        try activate(element("collectionFilter-\(franchiseID)"))
        XCTAssertTrue(waitUntil(timeout: 20) {
            self.element("videoCollectionFilterButton").value as? String == fixture.franchiseCollectionTitle
        }, "Choosing a collection should immediately select its scope.")
        try dismissTVLibraryBrowse()
        let movie = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(movie, timeout: 25, named: "ordinary Movies grid filtered to a collection")
        XCTAssertTrue(element("libraryScreen").exists)
        XCTAssertFalse(element("movieCollectionScreen").exists)
        XCTAssertFalse(element("libraryItem-\(fixture.convertedItemID)").exists)
        try attachFrame(named: "TV collection filter retains the Movies poster wall")
        try openTVLibraryBrowse()
        try activate(element("videoAllTitlesButton"))
        try dismissTVLibraryBrowse()
        try waitForExistence(element("libraryItem-\(fixture.convertedItemID)"), timeout: 25, named: "full Movies restored after clearing collection")
        try focusVideoGridItemFromCategories(movie, named: "movie after collection filter")
        try activate(movie)
        try waitForExistence(element("detailScreen"), timeout: 25, named: "movie detail from normal filtered library")
        completed = true
    }
    #endif

    func journeyHomeForegroundRefresh() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-ios-lifecycle"])
        try signIn(using: fixture, waitsForAccount: false)
        let home = element("homeScreen")
        try waitForExistence(home, timeout: 25, named: "Home before lifecycle transitions")
        try waitForExistence(element("featuredPlayButton"), timeout: 25, named: "loaded Home content")
        func lifecycleRefreshes() -> Int? {
            accessibilityText(home).split(separator: " ").first { $0.hasPrefix("lifecycleRefreshes=") }
                .flatMap { Int($0.dropFirst("lifecycleRefreshes=".count)) }
        }
        XCTAssertTrue(waitUntil(timeout: 15) { lifecycleRefreshes() == 0 },
                      "Initial Home loading must not count as a foreground refresh.")
        #if os(iOS)
        let lifecycle = element("iosLifecycleProbe")
        let window = try applicationWindow()
        window.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.01))
            .press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.72)))
        XCTAssertTrue(waitUntil(timeout: 10) { self.accessibilityText(lifecycle).contains("phase=inactive") })
        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        springBoard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.95))
            .press(forDuration: 0.05, thenDragTo: springBoard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)))
        XCTAssertTrue(waitUntil(timeout: 15) { self.accessibilityText(lifecycle).contains("phase=active") })
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(lifecycleRefreshes(), 0, "Control Center must not refresh Home.")
        #endif
        for expected in 1...2 {
            #if os(iOS)
            try backgroundApplication()
            #else
            XCUIRemote.shared.press(.home)
            XCTAssertTrue(waitUntil(timeout: 10) {
                self.application!.state == .runningBackground || self.application!.state == .runningBackgroundSuspended
            }, "The remote Home action must background Lumina.")
            #endif
            application!.activate()
            try waitForExistence(home, timeout: 20, named: "Home after background return")
            XCTAssertTrue(waitUntil(timeout: 15) { lifecycleRefreshes() == expected },
                          "Each real background return must admit exactly one Home refresh.")
            application!.activate()
            try await Task.sleep(for: .milliseconds(500))
            XCTAssertEqual(lifecycleRefreshes(), expected, "Activating an already active scene must not refresh again.")
            try waitForExistence(element("featuredPlayButton"), timeout: 20, named: "retained Home content")
        }
        completed = true
    }

    #if os(iOS)
    func journeyIdleHomeReturnsAfterUsingAnotherApp() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("homeScreen"), timeout: 20, named: "idle Home")
        let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
        defer { settings.terminate() }
        for _ in 0..<3 {
            try backgroundApplication()
            settings.activate()
            XCTAssertTrue(settings.wait(for: .runningForeground, timeout: 10))
            try await Task.sleep(for: .seconds(5))
            application!.activate()
            try waitForExistence(element("homeScreen"), timeout: 20, named: "Home after using another app")
            XCTAssertFalse(element("playbackScreen").exists, "Returning from idle background must not open a player")
            XCTAssertFalse(element("audioPlayerStage").exists, "Idle backgrounding must not present music playback")
        }
    }

    func journeyNativeBooksReadTurnAndRestoreAllFormats(backgroundCheckpoint: Bool = false) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard let epub = fixture.proseBookID, let pdf = fixture.pdfBookID,
              let cbz = fixture.comicBookID, let cbr = fixture.cbrBookID else {
            throw XCTSkip("Generate the current native Books fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let books = backgroundCheckpoint ? [epub] : [epub, pdf, cbz, cbr]
        try launchAndResetSession()
        for book in books { try await api.seedBookProgress(identity: identity, bookID: book) }
        try signIn(using: fixture)
        try selectDestination("Books", screen: "booksScreen")
        let catalog = try revealBookItem(element("bookCatalogButton"))
        catalog.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try attachFrame(named: "Books catalog before reading")
        for book in books {
            let format = book == epub ? "EPUB" : book == pdf ? "PDF" : book == cbz ? "CBZ" : "CBR"
            let card = element("book-\(book)")
            try revealBookItem(card)
            try waitForExistence(card, timeout: 20, named: "native book catalog entry")
            if backgroundCheckpoint {
                // Keep the tap inside the card's leading artwork, clear of the Duo utility rail.
                card.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.3)).tap()
            } else { try activate(card) }
            try showBookControls()
            let position = element("bookPosition")
            try waitForExistence(position, timeout: 20, named: "native reader position")
            let next = element("bookNextButton")
            try waitForHittable(next, timeout: 30, named: "prepared native page")
            try attachFrame(named: "\(format) reader with navigation controls")
            let before = position.label
            try activate(next)
            guard waitUntil(timeout: 20, condition: { position.label != before }) else {
                throw ProofFailure.ui("Native page turn did not advance.")
            }
            let selected = position.label
            if book != epub {
                let page = selected.replacingOccurrences(of: "Pages ", with: "Page ")
                    .replacingOccurrences(of: "–[0-9]+", with: "", options: .regularExpression)
                try waitForExistence(application!.images.matching(NSPredicate(format: "label == %@", page)).firstMatch,
                                     timeout: 20, named: "selected native image page")
            }
            if backgroundCheckpoint {
                try backgroundApplication()
                try await Task.sleep(for: .seconds(2))
                application!.activate()
                XCTAssertEqual(element("bookPosition").label, selected, "Background checkpointing must preserve the current reading position")
            }
            try activate(element("bookCloseButton"))
            try revealBookItem(card)
            if backgroundCheckpoint {
                // Keep the tap inside the card's leading artwork, clear of the Duo utility rail.
                card.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.3)).tap()
            } else { try activate(card) }
            try showBookControls()
            try waitForExistence(element("bookPosition"), timeout: 20, named: "restored native book position")
            guard waitUntil(timeout: 30, condition: { self.element("bookPosition").label == selected }) else {
                throw ProofFailure.ui("Native book did not restore the saved page.")
            }
            try activate(element("bookCloseButton"))
        }
        completed = true
    }

    func journeyOversizedProseChapterShowsReaderError() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let prose = try XCTUnwrap(fixture.proseBookID, "Generate the oversized EPUB proof fixture.")
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Books", screen: "booksScreen")
        let catalog = try revealBookItem(element("bookCatalogButton"))
        catalog.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let card = try revealBookItem(element("book-\(prose)"))
        card.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.3)).tap()
        try waitForExistence(application!.staticTexts["Unable to load page"], timeout: 30, named: "oversized EPUB reader error")
        XCTAssertFalse(element("bookProsePage").exists, "The oversized EPUB must not reach the TextKit page view.")
        completed = true
    }

    func journeyMediaCatalogGuttersAndNavigation() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try attachFrame(named: "Visual consistency Home")
        try selectDestination("Video", screen: "libraryScreen")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 20, named: "movie catalog")
        try attachFrame(named: "Visual consistency movie library")
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("audioItem-\(fixture.audioAlbumID)"), timeout: 20, named: "music catalog")
        try attachFrame(named: "Visual consistency music library")
        try selectDestination("Books", screen: "booksScreen")
        let books = element("booksScreen")
        let cards = books.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "book-"))
        try waitForExistence(cards.firstMatch, timeout: 25, named: "Books overview covers")
        let heading = books.staticTexts.matching(NSPredicate(format: "label IN %@", ["Continue reading", "Recently added"]))
            .allElementsBoundByIndex.first(where: { hasVisibleFrame($0) })
        let firstHeading = try XCTUnwrap(heading, "Books overview must identify its first shelf.")
        let firstCard = try XCTUnwrap(cards.allElementsBoundByIndex.filter { hasVisibleFrame($0) }
            .min(by: { $0.frame.minX < $1.frame.minX }), "The first Books shelf must show a cover.")
        try attachFrame(named: "Visual consistency Books overview gutters")
        let measurements = XCTAttachment(string: "headingX=\(firstHeading.frame.minX), firstCoverX=\(firstCard.frame.minX), viewport=\(try applicationViewport())")
        measurements.name = "Books heading and first cover gutter measurements"
        measurements.lifetime = .keepAlways
        add(measurements)
        XCTAssertEqual(firstHeading.frame.minX, try applicationViewport().minX + 20, accuracy: 1,
                       "Books must use the same content gutter as the other media catalogs.")
        // The accessible card bounds include its 4-point cover shadow.
        XCTAssertEqual(firstCard.frame.minX, firstHeading.frame.minX, accuracy: 4,
                       "The first Books cover must remain aligned with its shelf heading.")
        completed = true
    }

    func journeyArtworkCurlHonorsReadingOrderAndCommitsOnlyCompletedTurns(compactLayoutOnly: Bool = false) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let comic = try XCTUnwrap(fixture.comicBookID)
        let pdf = try XCTUnwrap(fixture.pdfBookID)
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        for book in [comic, pdf] { try await api.seedBookProgress(identity: identity, bookID: book) }
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        // Follow the public Library chooser on phones and the category rail on iPad.
        try selectTourBooksRoot()
        let catalog = try revealBookItem(element("bookCatalogButton"))
        catalog.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        for book in [comic, pdf] {
            let card = element("book-\(book)")
            let visibleCard = try revealBookItem(card)
            visibleCard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let pager = element("bookCurlPager")
            try waitForExistence(pager, timeout: 30, named: "native paper curl reader")
            try waitForExistence(application!.images.matching(NSPredicate(format: "label BEGINSWITH %@", "Page 1 of")).firstMatch,
                timeout: 30, named: "prepared artwork")
            let window = application!.windows.firstMatch.frame
            try attachFrame(named: "Immersive artwork reaches the display edges")
            XCTAssertGreaterThanOrEqual(pager.frame.width, window.width * 0.95)
            XCTAssertLessThanOrEqual(pager.frame.minY, window.minY + 1,
                "Immersive artwork must reach the top of the display without a reserved black strip.")
            XCTAssertGreaterThanOrEqual(pager.frame.maxY, window.maxY * 0.95)
            if compactLayoutOnly {
                guard window.width < 700 else { throw XCTSkip("Requires the closed or compact reading viewport.") }
                XCTAssertEqual(pager.value as? String, "Page 1 of \(book == comic ? 6 : 4)", "The closed screen defaults to one page.")
                XCTAssertGreaterThanOrEqual(element("bookPrimaryPage").frame.width, window.width * 0.95)
                try showBookControls()
                try activate(element("bookCloseButton"))
                continue
            }
            let before = pager.value as? String
            XCTAssertFalse(element("bookControlsButton").exists, "Immersive artwork must not have a floating menu button.")
            XCTAssertFalse(element("bookCloseButton").exists)
            try showBookControls()
            XCTAssertEqual(pager.value as? String, before, "Revealing menus must preserve the selected page.")
            try attachFrame(named: "Reader menus restored with a center tap")
            element("bookPrimaryPage").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            XCTAssertTrue(disappears(element("bookCloseButton"), timeout: 5), "A second center tap must return to unobstructed reading.")
            XCTAssertEqual(pager.value as? String, before)
            let rtl = book == comic
            let edge = rtl ? 0.08 : 0.92
            let start = pager.coordinate(withNormalizedOffset: CGVector(dx: edge, dy: 0.8))
            let partial = pager.coordinate(withNormalizedOffset: CGVector(dx: rtl ? 0.12 : 0.88, dy: 0.8))
            start.press(forDuration: 0.1, thenDragTo: partial, withVelocity: .slow, thenHoldForDuration: 0.5)
            XCTAssertEqual(pager.value as? String, before, "An abandoned fold must not save a different page.")
            let primaryPage = window.width >= 700 && !rtl ? "Page 2 of" : "Page 1 of"
            let artwork = application!.images.matching(NSPredicate(format: "label BEGINSWITH %@", primaryPage)).firstMatch
            let center = pager.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.5))
            center.doubleTap()
            XCTAssertTrue(waitUntil(timeout: 5) { (artwork.value as? String)?.contains("zoom") == true })
            XCTAssertFalse(element("bookCloseButton").exists, "Double-tap zoom must not reveal the menus.")
            let panEnd = pager.coordinate(withNormalizedOffset: CGVector(dx: 0.45, dy: 0.5))
            center.press(forDuration: 0.1, thenDragTo: panEnd)
            XCTAssertEqual(pager.value as? String, before, "Panning magnified artwork must not turn a page.")
            center.doubleTap()
            XCTAssertTrue(waitUntil(timeout: 5) { (artwork.value as? String)?.contains("zoom") == false })
            let end = pager.coordinate(withNormalizedOffset: CGVector(dx: rtl ? 0.92 : 0.08, dy: 0.75))
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
            XCTAssertTrue(waitUntil(timeout: 20) { pager.value as? String != before }, "The completed native curl must advance in the book's reading direction.")
            try attachFrame(named: rtl ? "Immersive manga after native RTL curl" : "Immersive PDF after native page curl")
            let last = window.width >= 700 ? (rtl ? "Pages 5–6 of 6" : "Pages 3–4 of 4") : (rtl ? "Page 6 of 6" : "Page 4 of 4")
            for _ in 0..<5 {
                if pager.value as? String == last { break }
                start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
            }
            XCTAssertEqual(pager.value as? String, last, "The final spread must remain reachable.")
            if rtl, window.width >= 700 {
                let fifth = application!.images.matching(NSPredicate(format: "label == %@", "Page 5 of 6")).firstMatch
                let sixth = application!.images.matching(NSPredicate(format: "label == %@", "Page 6 of 6")).firstMatch
                XCTAssertTrue(fifth.isHittable && sixth.isHittable, "Pages 5–6 must be visible together.")
                XCTAssertLessThan(sixth.frame.midX, fifth.frame.midX, "Manga reads right to left.")
            }
            let finalPage = application!.images.matching(NSPredicate(format: "label == %@", rtl ? "Page 6 of 6" : "Page 4 of 4")).firstMatch
            XCTAssertTrue(finalPage.isHittable, "The final real page must be visible.")
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.2)
            XCTAssertEqual(pager.value as? String, last, "Do not advance into a blank bookend.")
            let reverseStart = pager.coordinate(withNormalizedOffset: CGVector(dx: rtl ? 0.92 : 0.08, dy: 0.8))
            let reverseEnd = pager.coordinate(withNormalizedOffset: CGVector(dx: rtl ? 0.08 : 0.92, dy: 0.75))
            reverseStart.press(forDuration: 0.1, thenDragTo: reverseEnd, withVelocity: .slow, thenHoldForDuration: 0.2)
            XCTAssertTrue(waitUntil(timeout: 10) { pager.value as? String != last }, "Reverse curling must return to earlier pages.")
            let selected = pager.value as? String
            try showBookControls()
            try activate(element("bookCloseButton"))
            card.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.3)).tap()
            try waitForExistence(pager, timeout: 30, named: "restored curled page")
            XCTAssertTrue(waitUntil(timeout: 20) { pager.value as? String == selected })
            try showBookControls()
            try activate(element("bookCloseButton"))
        }
        completed = true
    }

    func showBookControls() throws {
        let proseControls = element("bookControlsButton")
        let artwork = application!.images.matching(NSPredicate(format: "label BEGINSWITH %@", "Page ")).firstMatch
        guard waitUntil(timeout: 30, condition: { proseControls.exists || artwork.exists }) else {
            throw ProofFailure.ui("The reader did not prepare its page or prose controls.")
        }
        if proseControls.exists {
            try activate(proseControls)
        } else {
            let page = element("bookPrimaryPage")
            try waitForExistence(page, timeout: 15, named: "reading page")
            page.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        try waitForHittable(element("bookCloseButton"), timeout: 15, named: "reading controls restored")
    }

    @discardableResult
    func revealBookItem(_ item: XCUIElement) throws -> XCUIElement {
        let viewport = try applicationViewport()
        for _ in 0..<8 {
            let candidates = application!.descendants(matching: .any).matching(identifier: item.identifier).allElementsBoundByIndex
            if let visible = candidates.first(where: { hasVisibleFrame($0) && $0.isEnabled }) { return visible }
            // The outer Duo display can place navigation over a trailing catalog action.
            try dragVertically(in: viewport, anchorX: viewport.midX, up: !item.exists || item.frame.midY >= viewport.midY)
        }
        throw ProofFailure.ui("The Books catalog item could not be revealed.")
    }

    #if LUMINA_UI_TESTING
    func emitBooksReaderFailureEvidence(fixture: ProofFixture, proseBookID: String) async {
        let viewState = element("booksScreen").value as? String ?? "absent"
        let outcome: String
        do {
            let origin = try localOrigin(from: fixture.serverURL)
            let session = makeTestAPISession(origin: origin)
            defer { session.invalidateAndCancel() }
            let api = ProofAPI(origin: origin, session: session)
            let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
            outcome = await api.booksCatalogEvidence(identity: identity, proseBookID: proseBookID)
        } catch {
            outcome = "outcome=unavailable"
        }
        print("PERF_BOOKS_PRE_TEARDOWN ui=\(viewState) api=\(outcome)")
    }
    #endif
    #endif

    #if os(iOS)
    func journeyPhoneExplicitHomeResume() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let resume = element("musicHomeResumeButton")
        try waitForExistence(resume, timeout: 25, named: "saved Resume")
        XCTAssertFalse(element("audioPlayerStage").exists, "Music Home must not start the saved queue on its own.")
        try activate(resume)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "explicitly resumed playback")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 20 }
    }

    func journeyPhoneOfflineMusicHome(reconnect: Bool = false) async throws {
        let fixture = try loadFixture()
        let sharedID = try XCTUnwrap(fixture.sharedMusicPlaylistID)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("musicHomeResumeButton"), timeout: 25, named: "saved Resume")
        let playlist = element("musicHomePlaylist-\(sharedID)")
        try waitForExistence(playlist, timeout: 20, named: "Home playlist")
        element("audioScreen").swipeUp()
        playlist.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("musicPlaylistDetailScreen"), timeout: 20, named: "playlist to download")
        let download = element("downloadAudioButton")
        try waitForExistence(download, timeout: 20, named: "playlist download")
        guard waitUntil(timeout: 20, condition: { download.isEnabled }) else { throw ProofFailure.timeout("Playlist did not load.") }
        download.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(application!.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "tracks available offline")).firstMatch,
                             timeout: 90, named: "playlist download completion")
        let app = application!
        app.terminate()
        app.launchArguments.append("--ui-testing-offline")
        app.launch()
        try waitForExistence(element("offlineLibraryScreen"), timeout: 25, named: "offline library")
        try waitForExistence(element("offlineMusicHomeButton"), timeout: 20, named: "offline Music Home")
        try activate(element("offlineMusicHomeButton"))
        let resume = element("musicHomeResumeButton")
        try waitForExistence(resume, timeout: 20, named: "offline saved Resume")
        XCTAssertFalse(element("audioPlayerStage").exists, "Opening offline Home must not begin playback.")
        try activate(resume)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "resumed offline playback")
        XCTAssertEqual(element("audioPlaybackMethod").label, "Downloaded audio")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 20 }
        guard reconnect else { return }

        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)

        // Pause after actual downloaded playback so the on-device checkpoint is dirty
        // while the connection is deliberately unavailable.
        try revealMusicControl(element("audioPlayPauseButton"))
        try activate(element("audioPlayPauseButton"))
        guard waitUntil(timeout: 10, condition: {
            self.element("audioPlayPauseButton").label == "Play"
        }) else {
            throw ProofFailure.ui("The offline queue did not retain its local paused checkpoint.")
        }
        // A different authenticated client now owns a competing server revision.
        try await api.replaceProofMusicSession(identity: identity,
                                                itemIDs: [fixture.audioSecondTrackID, fixture.audioSecondTrackID, fixture.audioTrackID],
                                                position: 12)
        // Exercise the production background checkpoint before simulating a
        // terminated process; a direct foreground termination can interrupt
        // the asynchronous checkpoint writer before it receives its flush.
        try backgroundApplication()
        try await Task.sleep(for: .seconds(3))
        app.terminate()
        app.launchArguments = ["--ui-testing"]
        app.launchEnvironment["LUMINA_UI_TEST_NAMESPACE"] = isolationID
        app.launch()

        // Reconnect returns to the library shell rather than presenting playback.
        // Surface the deliberately paused conflict through the shared-queue section.
        try waitForExistence(element("homeScreen"), timeout: 30, named: "connected Home after offline relaunch")
        let competing = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        XCTAssertEqual((competing?["queue"] as? [[String: Any]])?.compactMap { ($0["id"] as? String)?.replacingOccurrences(of: "-", with: "").lowercased() },
                       [fixture.audioSecondTrackID, fixture.audioSecondTrackID, fixture.audioTrackID].map { $0.replacingOccurrences(of: "-", with: "").lowercased() },
                       "The server revision must remain competing until the device resolution is chosen.")
        XCTAssertEqual(competing?["position_seconds"] as? Int, 12)
        // Home becomes visible before its reconnect navigation refresh has finished.
        // The conflict must be reachable from the real Library destination, not a
        // test-only deep link or a stale offline view.
        try waitForExistence(destinationControl("Library"), timeout: 45, named: "connected Library navigation")
        try selectDestination("Audio", screen: "audioScreen")
        try openMusicLibrarySection("queue")
        let useServer = element("libraryUseServerQueueButton")
        let keepDevice = element("libraryKeepDeviceQueueButton")
        try waitForExistence(useServer, timeout: 20, named: "visible reconnect server choice")
        try waitForExistence(keepDevice, timeout: 20, named: "visible reconnect Keep this device choice")
        XCTAssertEqual(keepDevice.label, "Keep this device's queue")
        XCTAssertTrue(hasVisibleFrame(keepDevice) && keepDevice.isEnabled,
                      "Keep this device must be visibly actionable in the Queue screen.")
        if keepDevice.isHittable {
            try activate(keepDevice)
        } else {
            // iOS 26.5 can leave the just-pushed Queue screen's real button
            // visibly enabled while XCTest reports its AX element unhittable.
            // Keep this physical fallback limited to the asserted Queue action.
            let frame = keepDevice.frame
            let window = try applicationWindow()
            window.coordinate(withNormalizedOffset: CGVector(
                dx: (frame.midX - window.frame.minX) / window.frame.width,
                dy: (frame.midY - window.frame.minY) / window.frame.height
            )).tap()
        }
        guard waitUntil(timeout: 20, condition: {
            !self.element("libraryUseServerQueueButton").exists && !self.element("libraryKeepDeviceQueueButton").exists
        }) else {
            throw ProofFailure.ui("Keeping this device did not resolve the reconnect conflict.")
        }

        try await waitForDeviceQueueToRepublish(api: api, identity: identity, fixture: fixture)
    }

    private func waitForDeviceQueueToRepublish(api: ProofAPI, identity: APIIdentity, fixture: ProofFixture) async throws {
        let deadline = Date().addingTimeInterval(20)
        repeat {
            if let saved = try? await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let entries = saved["entries"] as? [[String: Any]] {
                let normalizedTrackID = fixture.audioTrackID.replacingOccurrences(of: "-", with: "").lowercased()
                let duplicates = entries.filter { entry in
                    entry["kind"] as? String == "track"
                        && (entry["item_id"] as? String)?.replacingOccurrences(of: "-", with: "").lowercased() == normalizedTrackID
                }
                if duplicates.count == 2,
                   entries.contains(where: { $0["kind"] as? String == "acquisition" }),
                   saved["unavailable_policy"] as? String == "wait" {
                    return
                }
            }
            try await Task.sleep(for: .milliseconds(500))
        } while Date() < deadline
        throw ProofFailure.ui("Keeping this device did not republish duplicate occurrences and the Wait queue boundary.")
    }

    func journeyPhoneContextualMix() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try activate(revealMusicAlbum(fixture, named: "album for Instant Mix"))
        let instant = element("nativeInstantMixButton")
        try waitForExistence(instant, timeout: 20, named: "Instant Mix")
        element("musicCollectionScreen").swipeUp()
        try revealInLibraryViewport([instant], in: element("musicCollectionScreen"))
        try activate(instant)
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "mix preview")
        try waitForExistence(element("nativeMixSave"), timeout: 20, named: "mix actions")
        let beforeSave = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try activate(element("nativeMixSave"))
        try waitForExistence(application!.textFields["Playlist name"], timeout: 10, named: "playlist name")
        try activate(application!.buttons["Save Playlist"])
        try waitForExistence(element("nativeMixNotice"), timeout: 20, named: "playlist saved")
        let afterSave = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        XCTAssertEqual(beforeSave?["revision"] as? Int, afterSave?["revision"] as? Int, "Saving a mix must not change the queue.")
        XCUIDevice.shared.press(.home)
        application!.activate()
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "mix preview after foreground")
        try waitForExistence(element("nativeMixPlay"), timeout: 10, named: "Play Mix after foreground")
        try waitForExistence(element("nativeMixAppend"), timeout: 10, named: "Add to Queue after foreground")
        try activate(application!.buttons["Close"])
        let collection = element("musicCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "album after closing mix")
        let track = element("musicItem-\(fixture.audioTrackID)")
        try waitForExistence(track, timeout: 20, named: "track radio seed")
        try revealInLibraryViewport([track], in: collection)
        track.press(forDuration: 1)
        try activate(application!.buttons["Track Radio"])
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "track radio preview")
        try activate(application!.buttons["Close"])
    }

    func journeyPhoneArtistInstantMixDismissal() async throws {
        let fixture = try loadFixture()
        let (api, identity) = try await proofMusicAPI(fixture)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try browseFromMusicHome()
        try openMusicOptionsForProof()
        try activate(application!.buttons["Artists"].firstMatch)

        let artist = element("audioItem-\(fixture.audioArtistID)")
        try waitForExistence(artist, timeout: 20, named: "fixture artist")
        let artistSeed = artist.label
        try activateVisibleCenterWhenNeeded(artist, named: "fixture artist")
        let collection = element("musicCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "artist detail")
        let instant = element("nativeInstantMixButton")
        try revealInLibraryViewport([instant], in: collection)
        let before = try await queueState(api: api, identity: identity, source: "Artist Instant Mix before preview")
        try activateVisibleCenterWhenNeeded(instant, named: "artist Instant Mix")
        let preview = element("nativeMixPreview")
        try waitForExistence(preview, timeout: 20, named: "artist Instant Mix preview")
        XCTAssertEqual(preview.label, artistSeed, "Artist Instant Mix preview must retain the fixture artist seed.")
        try activate(application!.buttons["Close"])
        try waitForExistence(collection, timeout: 20, named: "artist detail after dismissal")
        let after = try await queueState(api: api, identity: identity, source: "Artist Instant Mix after dismissal")
        XCTAssertEqual(after.ids, before.ids, "Dismissing Artist Instant Mix must preserve queue order.")
        XCTAssertEqual(after.currentIndex, before.currentIndex, "Dismissing Artist Instant Mix must preserve the selected queue entry.")
    }

    func journeyPhoneNowPlayingTrackRadioDismissal() async throws {
        let fixture = try loadFixture()
        let (api, identity) = try await proofMusicAPI(fixture)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let resume = element("musicHomeResumeButton")
        try waitForExistence(resume, timeout: 25, named: "fixture Music Home Resume")
        XCTAssertFalse(element("audioPlayerStage").exists, "Music Home must not begin playback before explicit Resume.")
        try activateVisibleCenterWhenNeeded(resume, named: "fixture Music Home Resume")
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "fixture Now Playing")
        XCTAssertTrue(currentAudioTitle().contains(fixture.audioTrackTitle), "Explicit Resume must open the seeded #343 track.")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 1 }

        let optionsMenu = element("audioOptionsButton")
        try activateVisibleCenterWhenNeeded(optionsMenu, named: "Now Playing actions")
        try activateVisibleCenterWhenNeeded(application!.buttons["Listening options"].firstMatch, named: "Listening options action")
        try waitForExistence(element("audioOptionsSheet"), timeout: 20, named: "Listening options sheet")
        let options = element("audioListeningOptions")
        try activateVisibleCenterWhenNeeded(options, named: "Now Playing listening options")
        XCTAssertTrue(waitUntil(timeout: 10) { options.value as? String == "Expanded" },
                      "Now Playing listening options must expand before Track Radio.")
        let radio = element("nativeNowPlayingTrackRadio")
        try revealMusicControl(radio)
        let before = try await queueState(api: api, identity: identity, source: "Now Playing Track Radio before preview")
        try activate(radio)
        let preview = element("nativeMixPreview")
        try waitForExistence(preview, timeout: 20, named: "Now Playing Track Radio preview")
        XCTAssertTrue(preview.label.contains(fixture.audioTrackTitle), "Track Radio preview must retain the Now Playing fixture track seed.")
        try activate(application!.buttons["Close"])
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "Now Playing after Track Radio dismissal")
        let after = try await queueState(api: api, identity: identity, source: "Now Playing Track Radio after dismissal")
        XCTAssertEqual(after.ids, before.ids, "Dismissing Now Playing Track Radio must preserve queue order.")
        XCTAssertEqual(after.currentIndex, before.currentIndex, "Dismissing Now Playing Track Radio must preserve the selected queue entry.")
        try activateVisibleCenterWhenNeeded(application!.buttons["Done"], named: "Listening options Done")
        XCTAssertTrue(disappears(element("audioOptionsSheet"), timeout: 10), "Listening options must close before stopping playback.")
    }

    private func proofMusicAPI(_ fixture: ProofFixture) async throws -> (ProofAPI, APIIdentity) {
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        return (api, identity)
    }

    private func queueState(api: ProofAPI, identity: APIIdentity, source: String) async throws -> (ids: [String], currentIndex: Int) {
        let response = try await api.musicJSON(identity: identity, path: "session")
        let session = try XCTUnwrap(response as? [String: Any],
                                    "\(source) requires a server session.")
        let ids = try XCTUnwrap((session["queue"] as? [[String: Any]])?.compactMap { $0["id"] as? String },
                                 "\(source) requires a concrete queue.")
        XCTAssertFalse(ids.isEmpty, "\(source) requires a nonempty queue.")
        return (ids, try XCTUnwrap(session["current_index"] as? Int, "\(source) requires a selected queue entry."))
    }

    private func openMusicOptionsForProof() throws {
        let options = element("musicOptionsMenu")
        try waitForExistence(options, timeout: 20, named: "Music options")
        try activateVisibleCenterWhenNeeded(options, named: "Music options")
    }

    private func activateVisibleCenterWhenNeeded(_ control: XCUIElement, named name: String) throws {
        try waitForExistence(control, timeout: 20, named: name)
        guard hasVisibleFrame(control), control.isEnabled else {
            throw ProofFailure.ui("\(name) must be visible and enabled before selection.")
        }
        if control.isHittable {
            control.tap()
            return
        }
        let frame = control.frame
        let window = try applicationWindow()
        window.coordinate(withNormalizedOffset: CGVector(
            dx: (frame.midX - window.frame.minX) / window.frame.width,
            dy: (frame.midY - window.frame.minY) / window.frame.height
        )).tap()
    }

    func journeyPhoneMusicLegacy() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "legacy music album")
        XCTAssertFalse(element("musicHomeBrowseButton").exists, "Disabled and old servers must retain the existing browse surface.")
        let afterBrowse = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        let beforeIDs = (before?["queue"] as? [[String: Any]])?.compactMap { $0["id"] as? String }
        let afterIDs = (afterBrowse?["queue"] as? [[String: Any]])?.compactMap { $0["id"] as? String }
        XCTAssertEqual(beforeIDs, afterIDs, "Browsing must not replace the saved queue.")
        XCTAssertEqual(before?["current_index"] as? Int, afterBrowse?["current_index"] as? Int)
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "legacy album")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "legacy playback")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 4 }
        try revealMusicControl(element("audioPlayPauseButton"))
        try activate(element("audioPlayPauseButton"))
        var savedPosition = 0.0
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if let saved = try await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let position = saved["position_seconds"] as? Double, position >= 3 { savedPosition = position; break }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertGreaterThanOrEqual(savedPosition, 3, "Legacy playback must checkpoint its position.")
    }

    func journeyPhoneMusicHome() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("musicHomeBrowseButton"), timeout: 25, named: "Music Home")
        try waitForExistence(element("musicHomeEditButton"), timeout: 20, named: "shelf editor")
        let edit = element("musicHomeEditButton")
        if UIDevice.current.userInterfaceIdiom == .pad {
            edit.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        } else { try activate(edit) }
        try waitForExistence(element("musicHomeToggle-downloads"), timeout: 20, named: "Downloads shelf setting")
        let downloads = element("musicHomeToggle-downloads")
        downloads.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        XCTAssertTrue(waitUntil(timeout: 20) { downloads.value as? String == "0" }, "Downloads must be hidden after saving.")
        let reorder = element("musicHomeReorder-downloads")
        try activate(reorder)
        try activate(application!.buttons["Move earlier"])
        let reset = element("musicHomeResetButton")
        guard waitUntil(timeout: 20, condition: { reset.isEnabled }) else { throw ProofFailure.timeout("Shelf order did not save.") }
        try activate(reset)
        XCTAssertTrue(waitUntil(timeout: 20) { downloads.value as? String == "1" }, "Reset must show Downloads again.")
        try activate(application!.buttons["Done"])
        try waitForExistence(element("musicHomeShelf-downloads"), timeout: 20, named: "reset Downloads shelf")
        try activate(element("musicHomeBrowseButton"))
        _ = try revealMusicAlbum(fixture, named: "album after Music Home")
    }

    func journeyMusicHomeMiniPlayerAcrossBackground() async throws {
        let fixture = try loadFixture()
        let (api, identity) = try await proofMusicAPI(fixture)
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        try selectDestination("Audio", screen: "audioScreen")
        try activateVisibleCenterWhenNeeded(element("musicHomeResumeButton"), named: "Music Home Resume")
        try waitForExistence(element("audioPlayerStage"), timeout: 25, named: "resumed music player")
        let time = element("playbackTime")
        let beforePosition = try waitForPlaybackValue(time, timeout: 20) { $0 >= 24 }
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        let beforeQueue = try await queueState(api: api, identity: identity, source: "Music Home before background")

        let expandedClose = element("closePlayerButton")
        if hasVisibleFrame(expandedClose) {
            try activateVisibleCenterWhenNeeded(expandedClose, named: "expanded music player close")
        } else {
            try closePlayer()
        }
        let miniPlayer = element("nowPlayingBar")
        try waitForExistence(miniPlayer, timeout: 15, named: "Music Home mini-player")
        XCTAssertTrue(miniPlayer.label.contains(fixture.audioTrackTitle))
        try backgroundApplication()
        application!.activate()
        try waitForExistence(element("audioScreen"), timeout: 15, named: "Music Home after foreground")
        try activateVisibleCenterWhenNeeded(miniPlayer, named: "Music Home mini-player after foreground")
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "reopened music player")

        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 > beforePosition }
        let afterQueue = try await queueState(api: api, identity: identity, source: "Music Home after foreground")
        XCTAssertEqual(afterQueue.ids, beforeQueue.ids, "Returning to Music Home must preserve queue order.")
        XCTAssertEqual(afterQueue.currentIndex, beforeQueue.currentIndex, "Returning to Music Home must preserve the selected track.")
    }
    #endif

}
