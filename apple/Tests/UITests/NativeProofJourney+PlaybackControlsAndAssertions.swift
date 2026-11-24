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
    func journeyNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes(waitForStandby: Bool = false) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        guard let thirdEpisodeID = fixture.thirdEpisodeID,
              let thirdEpisodeTitle = fixture.thirdEpisodeTitle,
              let fourthEpisodeID = fixture.fourthEpisodeID,
              let fourthEpisodeTitle = fixture.fourthEpisodeTitle else {
            throw XCTSkip("The autoplay journey requires the opt-in four-episode proof fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        for episodeID in [fixture.episodeID, fixture.secondEpisodeID, thirdEpisodeID, fourthEpisodeID] {
            try await api.seedProgress(identity: identity, itemID: episodeID, seconds: 0)
        }

        #if os(iOS)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 8)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-animations"])
        #else
        try launchAndResetSession()
        #endif
        try signIn(using: fixture, waitsForAccount: false)
        #if os(iOS)
        let resume = element("resumeItem-\(fixture.episodeID)")
        try revealDetailElement(resume, in: element("homeScreen"), named: "episode on Home", requiresHittable: false)
        resume.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.3)).tap()
        #else
        try selectTourVideoCategory("tv")
        let show = element("libraryItem-\(fixture.showID)")
        try activate(try waitForPerformanceControl(show, timeout: 20, named: "autoplay fixture show"))
        #endif
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "autoplay fixture show")
        #if os(tvOS)
        try waitForExistence(element("videoCollectionItem-\(fixture.seasonID)"), timeout: 20,
                             named: "autoplay fixture season")
        let firstEpisode = element("videoCollectionItem-\(fixture.episodeID)")
        #else
        let firstEpisode = element("videoEpisode-\(fixture.episodeID)")
        #endif
        #if os(iOS)
        try revealDetailElement(firstEpisode, in: element("videoCollectionScreen"), named: "first episode thumbnail", requiresHittable: false)
        firstEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        #else
        try activate(firstEpisode)
        #endif
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "episode thumbnail playback")
        let time = element("playbackTime")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 1 }

        func finishCurrentEpisode(creditsStartSeconds: Double) throws {
            #if os(tvOS)
            _ = try waitForPlaybackValue(time, timeout: creditsStartSeconds + 15) { $0 >= creditsStartSeconds }
            #else
            XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 8))
            let surface = element("videoControlsSurface")
            try waitForExistence(surface, timeout: 8, named: "video controls reveal surface")
            surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let transport = element("videoPlayPauseButton")
            XCTAssertTrue(waitUntil(timeout: 8) { self.hasVisibleFrame(transport) })
            transport.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Play" },
                          "Autoplay proof must seek while the current episode is paused.")
            let forward = element("videoSeekForwardButton")
            let position = try waitForPlaybackValue(time, timeout: 5) { $0 >= 0 }
            let steps = max(0, Int(ceil((creditsStartSeconds - position) / 10)))
            for _ in 0..<steps {
                #if os(iOS)
                XCTAssertTrue(waitUntil(timeout: 10) { self.hasVisibleFrame(forward) && forward.isEnabled })
                forward.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                #else
                try activate(forward)
                #endif
            }
            let skipCredits = element("skipCreditsButton")
            #if os(iOS)
            XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(skipCredits) && skipCredits.isEnabled })
            skipCredits.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            #else
            try activate(skipCredits)
            #endif
            #endif
        }

        let automaticallyStarted = [
            (title: fixture.secondEpisodeTitle, id: fixture.secondEpisodeID),
            (title: thirdEpisodeTitle, id: thirdEpisodeID),
        ]
        for (index, next) in automaticallyStarted.enumerated() {
            try finishCurrentEpisode(creditsStartSeconds: index == 0 ? fixture.episodeCreditsStartSeconds : 10)
            #if os(tvOS)
            let title = element("playbackScreen").staticTexts
                .matching(NSPredicate(format: "label CONTAINS %@", next.title)).firstMatch
            #else
            let title = element("videoPlaybackChrome").staticTexts
                .matching(NSPredicate(format: "label CONTAINS %@", next.title)).firstMatch
            #endif
            guard waitUntil(timeout: 120, condition: { self.hasVisibleFrame(title) }) else {
                throw ProofFailure.ui("Completing episode \(index + 1) did not reveal the next episode title.")
            }
            _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 3 }
            _ = try await waitForServerProgress(api, identity: identity, itemID: next.id, minimum: 1)
        }

        try finishCurrentEpisode(creditsStartSeconds: 10)
        let stillWatching = application!.alerts["Still watching?"]
        try waitForExistence(stillWatching, timeout: 120, named: "Still watching presence confirmation")
        let fourthTitle = element("videoPlaybackChrome").staticTexts
            .matching(NSPredicate(format: "label CONTAINS %@", fourthEpisodeTitle)).firstMatch
        XCTAssertFalse(hasVisibleFrame(fourthTitle),
                       "The fourth episode must not start until presence is confirmed.")
        let fourthProgress = try await api.playbackSeconds(identity: identity, itemID: fourthEpisodeID)
        XCTAssertEqual(fourthProgress, 0, accuracy: 0.1, "An unconfirmed fourth episode must not report playback.")
        try attachFrame(named: "Still watching after three episodes")
        #if os(tvOS)
        if waitForStandby {
            let standby = element("tvStandbyArtwork")
            XCTAssertFalse(standby.exists, "Standby must wait for the presence deadline.")
            try waitForExistence(standby, timeout: 330, named: "library artwork standby after five unanswered minutes")
            XCTAssertFalse(element("playbackScreen").exists, "Standby must release the video player.")
            XCTAssertFalse(stillWatching.exists)
            let standbyProgress = try await api.playbackSeconds(identity: identity, itemID: fourthEpisodeID)
            XCTAssertEqual(standbyProgress, 0, accuracy: 0.1,
                           "Standby must not start the unconfirmed fourth episode.")
            XCTAssertTrue(waitUntil(timeout: 20) { !(standby.value as? String ?? "").isEmpty },
                          "Standby must display artwork from the signed-in library.")
            try attachFrame(named: "Unattended Apple TV displays library artwork in standby")
            let firstArtworkTitle = standby.value as? String
            XCTAssertTrue(waitUntil(timeout: 30) { (standby.value as? String) != firstArtworkTitle },
                          "Standby must cycle through library artwork.")
            XCUIRemote.shared.press(.right)
            XCTAssertTrue(disappears(standby, timeout: 10), "Remote input must leave standby.")
            try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after leaving standby")
            XCTAssertFalse(element("playbackScreen").exists, "Waking standby must not resume playback.")
            completed = true
            return
        }
        #endif
        let goHome = application!.buttons.matching(NSPredicate(format: "label == %@", "Go Home")).firstMatch
        try activate(try waitForHittable(goHome, timeout: 10, named: "Go Home presence action"))
        XCTAssertTrue(waitUntil(timeout: 15) {
            !self.element("playbackScreen").exists && self.hasVisibleFrame(self.element("homeScreen"))
        }, "Go Home must dismiss the player and return the unattended session to Home.")
        completed = true
    }

    #if os(iOS)
    func journeyVideoPictureInPictureContinuesInBackground() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try relaunchAuthenticatedSession()
        try openLibraryItem(id: fixture.directItemID)
        let time = try startPlayback(using: "playButton")
        let state = element("pictureInPictureState")
        try waitForExistence(state, timeout: 15, named: "native PiP state")
        if state.value as? String == "Unsupported" {
            application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            XCTAssertFalse(element("videoPictureInPictureButton").exists, "Unsupported devices must not offer PiP.")
            try closePlayer()
            completed = true
            return
        }
        guard waitUntil(timeout: 25, condition: { state.value as? String == "Ready" }) else {
            throw ProofFailure.ui("The native video layer never became eligible for Picture in Picture.")
        }
        // Pause to hold chrome still while the test selects PiP; ordinary playback
        // auto-hides controls after inactivity.
        try tapVideoTransportAfterChromeHides(named: "PiP pause transport")
        let transport = application!.buttons["videoPlayPauseButton"]
        XCTAssertEqual(transport.label, "Play", "PiP must begin from an explicitly paused player.")
        application!.buttons["videoPictureInPictureButton"].tap()
        guard waitUntil(timeout: 15, condition: { state.value as? String == "Active" }) else {
            throw ProofFailure.ui("The native Picture in Picture controller did not start.")
        }
        transport.tap()
        let before = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        let playerWindow = try applicationWindow().frame
        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        XCUIDevice.shared.press(.home)
        guard waitUntil(timeout: 10, condition: {
            let hasPiPWindow = self.application!.windows.allElementsBoundByIndex.contains { window in
                let frame = window.frame
                return !frame.isEmpty && frame.width < playerWindow.width && frame.height < playerWindow.height
            }
            return springBoard.state == .runningForeground && state.value as? String == "Active" && hasPiPWindow
        }) else {
            throw ProofFailure.ui("Home must expose SpringBoard while the active Picture in Picture window remains visible.")
        }
        try await Task.sleep(for: .seconds(3))
        application!.activate()
        try waitForExistence(element("playbackScreen"), timeout: 15, named: "video after returning from PiP")
        let after = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(after, before + 2, "PiP must continue video while Lumina is backgrounded.")
        try attachPlaybackFrame(named: "Native Picture in Picture after background playback")
        let pipView = element("PIPUIView")
        let pipState = state.value as? String ?? "absent"
        let pipViewExists = pipView.exists
        let pipViewFrame = pipView.frame
        let pipViewVisible = hasVisibleFrame(pipView)
        let pipViewHittable = pipView.isHittable
        let hadVisibleDescendantControl = pipViewExists && hasVisiblePIPControl(in: pipView)
        attachPIPReturnGuard(named: "PiP return-surface guard before observation",
                             state: pipState,
                             frame: pipViewFrame,
                             exists: pipViewExists,
                             visible: pipViewVisible,
                             hittable: pipViewHittable,
                             hadVisibleDescendantControl: hadVisibleDescendantControl)
        let viewport = try applicationViewport()
        let pipWindow = application!.windows.allElementsBoundByIndex.first { window in
            let frame = window.frame
            let matchesPIPView = abs(frame.minX - pipViewFrame.minX) < 0.5
                && abs(frame.minY - pipViewFrame.minY) < 0.5
                && abs(frame.width - pipViewFrame.width) < 0.5
                && abs(frame.height - pipViewFrame.height) < 0.5
            let finite = [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite)
            return !frame.isEmpty && finite && matchesPIPView && viewport.contains(frame)
        }
        var restoredFromPictureInPicture = false
        if pipState == "Active", let pipWindow {
            pipWindow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            attachPIPReturnObservation(named: "PiP system controls after return-surface tap",
                                       pipWindow: pipWindow,
                                       hadVisibleDescendantControl: hadVisibleDescendantControl)
            var restoreFrame: CGRect?
            for (name, delay) in [("0.5s", 0.5), ("1s", 0.5), ("2s", 1.0)] {
                try await Task.sleep(for: .seconds(delay))
                if let observedFrame = attachSpringBoardPIPObservation(
                    named: "PiP SpringBoard controls \(name) after return-surface tap",
                    springBoard: springBoard,
                    controlNamed: "Restore fullscreen"
                ) {
                    restoreFrame = observedFrame
                }
            }
            guard let restoreFrame else {
                throw ProofFailure.ui("The public PiP Restore fullscreen control did not appear after showing PiP controls.")
            }
            let finiteRestoreFrame = [restoreFrame.minX, restoreFrame.minY, restoreFrame.width, restoreFrame.height]
                .allSatisfy(\.isFinite)
            guard finiteRestoreFrame, !restoreFrame.isEmpty, viewport.contains(restoreFrame) else {
                throw ProofFailure.ui("The public PiP Restore fullscreen control was outside the usable viewport.")
            }
            let normalizedRestore = CGVector(
                dx: (restoreFrame.midX - viewport.minX) / viewport.width,
                dy: (restoreFrame.midY - viewport.minY) / viewport.height
            )
            guard normalizedRestore.dx.isFinite, normalizedRestore.dy.isFinite,
                  (0...1).contains(normalizedRestore.dx), (0...1).contains(normalizedRestore.dy) else {
                throw ProofFailure.ui("The public PiP Restore fullscreen control had no valid physical tap point.")
            }
            springBoard.coordinate(withNormalizedOffset: normalizedRestore).tap()
            guard waitUntil(timeout: 15, condition: { state.value as? String != "Active" }) else {
                throw ProofFailure.ui("The public PiP Restore control did not return playback to the app.")
            }
            try waitForExistence(element("playbackScreen"), timeout: 15, named: "full player after restoring Picture in Picture")
            restoredFromPictureInPicture = true
        }
        if restoredFromPictureInPicture {
            try closePlayerAfterPictureInPictureReturn()
        } else {
            try closePlayer()
        }
        XCTAssertTrue(disappears(element("playbackScreen"), timeout: 15))
        completed = true
    }
    #endif

    #if os(iOS)
    func journeyVideoCastPickerOpensAndReturnsToLocalPlayback() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.directItemID)
        _ = try startPlayback(using: "playButton")

        // Pause through the public transport before requesting Cast. This holds the
        // same player chrome a person uses while selecting an output and avoids
        // racing its intentional four-second auto-hide interval.
        try tapVideoTransportAfterChromeHides(named: "video pause before Chromecast")
        let app = try XCTUnwrap(application)
        let transport = app.buttons["videoPlayPauseButton"]
        XCTAssertEqual(transport.label, "Play", "Chromecast begins from a visibly paused local player.")
        // Address the wrapped UIKit control directly. The SwiftUI accessibility
        // container has the same identifier but is not the SDK-owned button that
        // receives the Cast default action.
        let cast = app.buttons["videoChromecastButton"]
        try waitForHittable(cast, timeout: 10, named: "Chromecast")
        try activate(cast)

        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let nearbyDevicePrompt = app.staticTexts["Connect to nearby device"]
        let localNetworkExplainer = app.staticTexts["LNAPermissionTitleLabel"]
        let castToTitle = app.staticTexts["Cast to"]
        let castToNavigation = app.navigationBars["Cast to"]
        let noDevicesNavigation = app.navigationBars["No devices available"]
        let localNetworkAlerts = [springBoard.alerts.firstMatch, app.alerts.firstMatch]
        XCTAssertTrue(waitUntil(timeout: 45, condition: {
            self.hasVisibleFrame(nearbyDevicePrompt)
                || self.hasVisibleFrame(localNetworkExplainer)
                || self.hasVisibleFrame(castToTitle)
                || self.hasVisibleFrame(castToNavigation)
                || self.hasVisibleFrame(noDevicesNavigation)
                || localNetworkAlerts.contains { $0.exists }
        }), "Chromecast must respond with its SDK local-network help, system permission, or device chooser.")

        if hasVisibleFrame(localNetworkExplainer) {
            XCTAssertTrue(localNetworkExplainer.isHittable,
                          "Chromecast local-network help must present an actionable SDK confirmation.")
            try attachPlaybackFrame(named: "Chromecast local-network help")
            try activate(app.buttons["LNAOKButton"])
        } else if hasVisibleFrame(nearbyDevicePrompt) {
            XCTAssertTrue(nearbyDevicePrompt.isHittable,
                          "Chromecast's local-network explainer must render a usable Connect action.")
            try activate(app.buttons["Connect"])
        }

        if waitUntil(timeout: 5, condition: { localNetworkAlerts.contains { $0.exists } }) {
            let localNetworkAlert = try XCTUnwrap(localNetworkAlerts.first { $0.exists })
            XCTAssertTrue(localNetworkAlert.label.localizedCaseInsensitiveContains("local network"),
                          "The system permission shown after Chromecast Connect must request local-network access.")
            try activate(localNetworkAlert.buttons["Allow"])
        }

        if waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(castToTitle)
                || self.hasVisibleFrame(castToNavigation)
                || self.hasVisibleFrame(noDevicesNavigation)
        }) {
            try attachPlaybackFrame(named: "Chromecast device chooser")
            let dismiss = [app.buttons["Close"], app.buttons["Cancel"]]
                .first { self.hasVisibleFrame($0) && $0.isHittable }
            let dismissButton = try XCTUnwrap(dismiss, "The Chromecast chooser must expose a user-facing dismissal control.")
            try activate(dismissButton)
            XCTAssertTrue(waitUntil(timeout: 10, condition: {
                !self.hasVisibleFrame(castToTitle) && !self.hasVisibleFrame(castToNavigation)
            }), "Dismissing the Chromecast chooser must return to local playback.")
        }
        try waitForHittable(element("videoPlayPauseButton"), timeout: 10, named: "local video transport after Chromecast picker")
        XCTAssertTrue(element("playbackScreen").exists)
        completed = true
    }
    #endif

    func journeyVideoOverlayCentersAndDismisses() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        #if os(iOS)
        try selectDestination("Video", screen: "libraryScreen")
        try selectTourLibraryCategory("tv", screen: "libraryScreen")
        try activate(element("libraryItem-\(fixture.showID)"))
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons")
        let collection = element("videoCollectionScreen")
        let hero = element("collectionHero")
        try waitForExistence(hero, timeout: 15, named: "full-page show artwork")
        let detailViewport = try applicationViewport()
        XCTAssertGreaterThanOrEqual(hero.frame.height, detailViewport.height - 2,
                                    "The detail hero must fill the first viewport before seasons.")
        try attachFrame(named: "Full-page show artwork before revealing seasons")
        let revealSeasons = element("collectionSeasonsButton")
        try activate(revealSeasons)
        try selectCollectionSeason(fixture.seasonID)
        let episode = element("videoEpisode-\(fixture.episodeID)")
        try revealDetailElement(episode, in: collection, named: "episode", requiresHittable: true)
        try activate(episode)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show after selecting episode")
        XCTAssertFalse(element("detailScreen").exists,
                       "Video overlay playback must begin from the retained show context after selecting an episode.")
        #else
        try openLibraryItem(id: fixture.directItemID)
        #endif
        _ = try startPlayback(using: "playButton")
        #if os(iOS)
        if element("videoTabletopControls").exists {
            let time = element("playbackTime")
            let initial = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            _ = try waitForPlaybackValue(time, timeout: 12) { $0 >= initial + 5 }
            try attachPlaybackFrame(named: "Tabletop video after system status auto-hides")
            let transport = element("videoPlayPauseButton")
            try activate(transport)
            XCTAssertEqual(transport.frame.midX, try applicationViewport().midX, accuracy: 2,
                           "The tabletop transport must remain centered with Next Episode present.")
            XCTAssertTrue(element("nextEpisodeTransportButton").exists)
            try attachPlaybackFrame(named: "Centered tabletop episode transport")
            completed = true
            return
        }
        try attachPlaybackFrame(named: "Video after controls and system status auto-hide")
        try tapVideoTransportAfterChromeHides(named: "revealed video transport")
        let transport = element("videoPlayPauseButton")
        XCTAssertEqual(transport.label, "Play", "The dismiss check must run while playback is paused.")
        #else
        XCUIRemote.shared.press(.playPause)
        let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 15, named: "video transport")
        XCTAssertEqual(transport.label, "Play", "The tvOS play/pause command must pause video while revealing chrome.")
        #endif
        let viewport = try applicationViewport()
        XCTAssertEqual(transport.frame.midX, viewport.midX, accuracy: 2,
                       "The video transport must be centered over the full video viewport.")
        try attachPlaybackFrame(named: "Video overlay before layout checks")
        let chrome = element("videoPlaybackChrome").frame
        XCTAssertEqual(chrome.midX, viewport.midX, accuracy: 2,
                       "Video chrome must share the video's horizontal center.")
        #if os(iOS)
        XCTAssertTrue(element("nextEpisodeTransportButton").exists,
                      "Centering must hold when an episode has a next action.")
        let close = element("closePlayerButton").frame
        let mute = element("videoMuteButton").frame
        let settings = element("videoSettingsButton").frame
        if viewport.width < 600, viewport.height > viewport.width {
            let timeline = element("videoTimeline").frame
            XCTAssertLessThanOrEqual(timeline.minX - viewport.minX, 24,
                                     "Portrait playback must not mirror the system rail's inset on the left.")
            XCTAssertLessThanOrEqual(viewport.maxX - timeline.maxX, 24,
                                     "The timeline must use the portrait screen's full width below the system rail.")
            let backward = element("videoSeekBackwardButton").frame
            let forward = element("videoSeekForwardButton").frame
            XCTAssertLessThan(backward.midX, viewport.minX + viewport.width * 0.35)
            XCTAssertGreaterThan(forward.midX, viewport.minX + viewport.width * 0.65)
            XCTAssertFalse(forward.intersects(element("nextEpisodeTransportButton").frame),
                           "Centered transport must keep Forward clear of Next Episode.")
            XCTAssertEqual(close.minX, settings.minX, accuracy: 2,
                           "Top and bottom utility icons must share the same leading edge.")
        }
        XCTAssertLessThan(close.maxY, transport.frame.minY,
                          "Close belongs above the centered transport.")
        XCTAssertGreaterThan(settings.minY, transport.frame.maxY,
                             "Playback utilities belong below the centered transport.")
        XCTAssertEqual(close.midY, mute.midY, accuracy: 2,
                       "Close and audio share the top toolbar baseline.")
        let airPlay = element("videoAirPlayButton").frame
        let cast = element("videoChromecastButton").frame
        let next = element("nextEpisodeTransportButton").frame
        XCTAssertEqual(settings.midY, airPlay.midY, accuracy: 2)
        XCTAssertEqual(airPlay.midY, cast.midY, accuracy: 2)
        XCTAssertEqual(cast.midY, next.midY, accuracy: 2)
        XCTAssertEqual((airPlay.midX + cast.midX) / 2, chrome.midX, accuracy: 2,
                       "Output routes form one centered group between settings and next.")
        #endif
        try attachPlaybackFrame(named: "Centered video overlay")
        #if os(iOS)
        try applicationWindow().coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.32)).tap()
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 3),
                      "Tapping the video background must dismiss paused controls immediately.")
        try activate(element("videoControlsSurface"))
        try waitForHittable(element("videoPlayPauseButton"), timeout: 3, named: "revealed video transport")
        #else
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 3))
        XCUIRemote.shared.press(.select)
        try waitForHittable(element("videoPlayPauseButton"), timeout: 3, named: "revealed video transport")
        #endif
        completed = true
    }

    func journeyNativeVideoDoesNotKeepDuplicateControlsOverPlayback() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.directItemID)
        let time = try startPlayback(using: "playButton")
        let initial = try waitForPlaybackValue(time, timeout: 10) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= initial + 2 }
        XCTAssertFalse(element("videoAppearanceButton").exists, "Fit/Fill belongs in playback settings.")
        if !element("videoTabletopControls").exists {
            #if os(tvOS)
            XCTAssertFalse(element("videoPlaybackChrome").exists,
                           "AVKit must be the only TV transport; Lumina's old overlay must be absent.")
            #else
            XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 10),
                          "Playing video must hide its transport controls after inactivity.")
            #endif
            XCTAssertFalse(element("closePlayerButton").exists, "Close must hide with the transport controls.")
            try attachPlaybackFrame(named: "Unobstructed video after controls auto-hide")
            #if os(tvOS)
            try openTVPlaybackSettings()
            #else
            try tapVideoTransportAfterChromeHides(named: "duplicate-control pause transport")
            try activate(element("videoSettingsButton"))
            #endif
            try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "playback settings")
            #if os(tvOS)
            XCUIRemote.shared.press(.playPause)
            XCTAssertTrue(waitUntil(timeout: 3) { self.element("videoSettingsTab-info").hasFocus },
                          "Opening playback settings must land on its initial category.")
            #endif
            try activate(element("videoSettingsTab-video"))
            #if os(tvOS)
            let fitPicker = element("videoFitPicker")
            let fit = element("videoFitFitButton")
            let fill = element("videoFitFillButton")
            try waitForHittable(fit, timeout: 10, named: "Fit video choice")
            try activate(fit)
            XCTAssertEqual(fit.value as? String, "Selected",
                           "The explicit Fit choice must expose its initial selected state.")
            XCUIRemote.shared.press(.right)
            XCTAssertTrue(waitUntil(timeout: 3) { fill.hasFocus },
                          "Remote Right must move focus from Fit to Fill.")
            XCUIRemote.shared.press(.select)
            XCTAssertTrue(waitUntil(timeout: 10) { self.accessibilityText(fitPicker).contains("Fill") },
                          "Remote Right/Select must choose Fill through the focusable Video fit choice.")
            XCTAssertEqual(fill.value as? String, "Selected",
                           "The explicit Fill choice must expose its selected state.")
            XCUIRemote.shared.press(.left)
            XCUIRemote.shared.press(.select)
            XCTAssertTrue(waitUntil(timeout: 10) { self.accessibilityText(fitPicker).contains("Fit") },
                          "Remote Left/Select must restore Fit through the focusable Video fit choice.")
            XCTAssertEqual(fit.value as? String, "Selected",
                           "The explicit Fit choice must expose its restored selected state.")
            XCTAssertEqual(fill.value as? String, "Not selected",
                           "Remote Left/Select must clear Fill's selected state.")
            XCTAssertTrue(waitUntil(timeout: 3) { fit.hasFocus },
                          "Remote Left/Select must leave Fit as the active focusable choice.")
            #else
            try activate(element("videoFitPicker"))
            try activate(menuAction("Fill"))
            try activate(element("videoFitPicker"))
            try activate(menuAction("Fit"))
            #endif
            try activate(element("videoSettingsTab-audio"))
            let sourceAudio = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "videoSourceAudioTrack-")
            ).firstMatch
            try waitForExistence(sourceAudio, timeout: 10, named: "source audio stream inventory")
            let beforeSwitch = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            try activate(sourceAudio)
            XCTAssertTrue(waitUntil(timeout: 45) { (sourceAudio.value as? String) == "Selected" },
                          "Selecting an audio stream must commit the requested source track.")
            XCTAssertFalse(element("videoStreamError").exists, "The valid source audio track must open successfully.")
            _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= beforeSwitch }
            #if os(iOS)
            if !element("videoSettingsTab-subtitles").isHittable {
                element("videoSettingsTab-audio").swipeLeft()
            }
            #endif
            try activate(element("videoSettingsTab-subtitles"))
            #if os(iOS)
            let automaticSubtitle = element("videoSubtitleAutoButton")
            let subtitlesOff = element("videoSubtitleOffButton")
            try waitForHittable(automaticSubtitle, timeout: 8, named: "horizontal subtitle choices")
            if subtitlesOff.exists {
                XCTAssertEqual(automaticSubtitle.frame.midY, subtitlesOff.frame.midY, accuracy: 1,
                               "Auto and Off should share one horizontal row on iOS.")
            }
            let firstSubtitle = element("videoSubtitleTrack-0")
            if firstSubtitle.exists {
                XCTAssertEqual(automaticSubtitle.frame.midY, firstSubtitle.frame.midY, accuracy: 1,
                               "Subtitle tracks should align with Auto and Off on iOS.")
            }
            #endif
            try setNativeSwitch("videoSubtitleCustomizeToggle", to: true)
            let appearance = element("videoSubtitleAppearanceDisclosure")
            for _ in 0..<8 where !appearance.isHittable {
                #if os(tvOS)
                XCUIRemote.shared.press(.down)
                #else
                element("videoSettingsPanel").swipeUp()
                #endif
            }
            try waitForHittable(appearance, timeout: 10, named: "caption appearance disclosure")
            XCTAssertFalse(element("videoSubtitleStyle-Size").exists,
                           "Detailed appearance options stay out of the way until requested.")
            try activate(appearance)
            let size = element("videoSubtitleStyle-Size")
            for _ in 0..<8 where !size.isHittable {
                #if os(tvOS)
                XCUIRemote.shared.press(.down)
                #else
                element("videoSettingsPanel").swipeUp()
                #endif
            }
            #if os(tvOS)
            let mediumSubtitleSize = element("videoSubtitleStyle-Size-medium")
            let largeSubtitleSize = element("videoSubtitleStyle-Size-large")
            try waitForHittable(mediumSubtitleSize, timeout: 10, named: "Medium subtitle size choice")
            try activate(mediumSubtitleSize)
            XCTAssertEqual(mediumSubtitleSize.value as? String, "Selected",
                           "The explicit Medium subtitle size choice must expose its initial selected state.")
            XCUIRemote.shared.press(.right)
            XCUIRemote.shared.press(.select)
            XCTAssertEqual(largeSubtitleSize.value as? String, "Selected",
                           "Remote Right/Select must select the explicit Large subtitle size choice.")
            #else
            try activate(size)
            try activate(menuAction("Large"))
            #endif
            XCTAssertTrue(waitUntil(timeout: 5) { (size.value as? String)?.contains("Large") == true },
                          "Subtitle size must retain the viewer's choice.")
            try attachPlaybackFrame(named: "Lumina subtitle tracks and custom appearance")
            let reset = element("videoSubtitleResetButton")
            for _ in 0..<10 where !reset.isHittable {
                #if os(tvOS)
                XCUIRemote.shared.press(.down)
                #else
                element("videoSettingsPanel").swipeUp()
                #endif
            }
            try activate(reset)
            for _ in 0..<10 where !element("videoSubtitleCustomizeToggle").isHittable {
                #if os(tvOS)
                XCUIRemote.shared.press(.up)
                #else
                element("videoSettingsPanel").swipeDown()
                #endif
            }
            try setNativeSwitch("videoSubtitleCustomizeToggle", to: false)
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(element("videoSettingsDoneButton"))
            #endif
            XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 8))
            XCTAssertTrue(element("playbackScreen").exists, "Closing settings must keep the player open.")
            #if os(tvOS)
            try waitForExistence(element("Media Controls"), timeout: 8, named: "native transport after settings")
            #endif
            let afterSettings = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            // Pause the short fixture during the long settings workflow so the
            // final transport assertion cannot race the movie's natural ending.
            #if os(tvOS)
            XCUIRemote.shared.press(.playPause)
            #else
            try activate(element("videoPlayPauseButton"))
            #endif
            _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= afterSettings + 1 }
            #if os(tvOS)
            XCUIRemote.shared.press(.playPause)
            try waitForExistence(element("Media Controls"), timeout: 8, named: "paused video transport")
            let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 1 },
                           "Remote Play/Pause must pause the video.")
            XCUIRemote.shared.press(.playPause)
            _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= pausedAt + 1 }
            #endif
        }
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after closing video")
        completed = true
    }

    func journeyNativePlaybackQualityAndSubtitleTimingRepackagePausedMKV() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        // The shared fixture normally seeds a long resume point. This cue is
        // authored for 2–7 seconds, so begin this public playback journey at
        // the actual beginning rather than treating end-of-video as “after 3”.
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.convertedItemID)
        let time = try startPlayback(using: "playButton")
        let startedAt = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= startedAt + 1 }

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("Media Controls"), timeout: 8, named: "paused native MKV transport")
        #else
        try tapVideoTransportAfterChromeHides(named: "native MKV pause control")
        #endif
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5 },
                       "Playback must stay paused while reconfiguring quality and subtitle timing.")

        #if os(tvOS)
        try openTVPlaybackSettings()
        #else
        try activate(element("videoSettingsButton"))
        #endif
        try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "paused playback settings")
        try activate(element("videoSettingsTab-subtitles"))
        let subtitle = element("videoSubtitleTrack-0")
        try waitForHittable(subtitle, timeout: 20, named: "embedded MKV subtitle track")
        try activate(subtitle)
        XCTAssertTrue(waitUntil(timeout: 8) { (subtitle.value as? String) == "Selected" },
                      "The synthetic embedded subtitle track must be selectable on packaged MKV playback.")

        // This fixture authors a cue from 2–7 seconds. Selection state alone
        // is not evidence that AVFoundation delivered or displayed it.
        let cueTime: Double
        #if os(tvOS)
        // Native scrubbing is covered by the remote workflow. The public Start
        // chapter gives the caption-rendering proof a precise paused origin.
        try activate(element("videoSettingsTab-chapters"))
        try activate(element("videoChapter-0"))
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 <= 1 }
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 8))
        try waitForExistence(element("Media Controls"), timeout: 8, named: "native transport before subtitle cue")
        XCUIRemote.shared.press(.playPause)
        cueTime = try waitForPlaybackValue(time, timeout: 15) { (2...7).contains($0) }
        #else
        try activate(element("videoSettingsDoneButton"))
        let resume = element("videoPlayPauseButton")
        if !resume.isHittable {
            try activate(element("videoControlsSurface"))
        }
        try waitForHittable(resume, timeout: 10, named: "resume for synthetic subtitle cue")
        XCTAssertEqual(resume.label, "Play")
        let pauseCueCoordinate = resume.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        let pausedBeforeCue = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(time.value as? String ?? "") ?? pausedBeforeCue) > pausedBeforeCue + 0.5
        }, "Closing settings must preserve the requested paused state before cue playback resumes.")
        let timeline = element("videoTimeline")
        try waitForHittable(timeline, timeout: 10, named: "paused timeline for synthetic subtitle cue")
        // This generated fixture is 90 seconds. Four percent lands near three
        // seconds; confirm the actual clock rather than trusting the tap.
        timeline.coordinate(withNormalizedOffset: CGVector(dx: 0.035, dy: 0.5)).tap()
        let soughtCueTime = try waitForPlaybackValue(time, timeout: 10) { (2...7).contains($0) }
        XCTAssertEqual(resume.label, "Play", "Seeking to the subtitle cue must retain pause intent.")
        try activate(resume)
        cueTime = try waitForPlaybackValue(time, timeout: 10) {
            $0 >= soughtCueTime + 0.8 && $0 <= 5
        }
        #endif
        XCTAssertTrue((2...7).contains(cueTime), "Subtitle rendering must be evaluated inside its authored 2–7 second cue window.")

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        let pausedCueTime = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(time.value as? String ?? "") ?? pausedCueTime) > pausedCueTime + 0.5
        }, "Pausing for the subtitle proof must stop the real video clock.")
        XCTAssertTrue((2...7).contains(pausedCueTime),
                      "The paused subtitle capture must remain inside its authored cue window.")
        // Verify rendered captions with AVKit's own transport. Native Back
        // cancellation and dismissal are covered by the remote workflow.
        XCTAssertTrue(element("playbackScreen").exists,
                      "The native transport must retain the paused subtitle player.")
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(time.value as? String ?? "") ?? pausedCueTime) > pausedCueTime + 0.5
        }, "The subtitle proof must remain paused while native controls are visible.")
        let subtitleScreenshot = try attachPlaybackFrame(named: "Synthetic subtitle cue while paused")
        try assertSyntheticSubtitleVisible(in: subtitleScreenshot)
        try openTVPlaybackSettings()
        #else
        let surface = element("videoControlsSurface")
        XCTAssertEqual(resume.label, "Pause", "Playback must still expose Pause before the authored cue can auto-hide transport chrome.")
        pauseCueCoordinate.tap()
        let pausedCueTime = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(time.value as? String ?? "") ?? pausedCueTime) > pausedCueTime + 0.5
        }, "Pausing for the subtitle proof must stop the real video clock.")
        XCTAssertTrue((2...7).contains(pausedCueTime),
                      "The paused subtitle capture must remain inside its authored cue window.")
        try activate(surface)
        XCTAssertTrue(disappears(resume, timeout: 5), "Hiding transport chrome must not resume the paused subtitle proof.")
        let subtitleScreenshot = try attachPlaybackFrame(named: "Synthetic subtitle cue while paused")
        try assertSyntheticSubtitleVisible(in: subtitleScreenshot)
        try activate(surface)
        try waitForHittable(resume, timeout: 10, named: "paused controls after subtitle proof")
        XCTAssertEqual(resume.label, "Play")
        try activate(element("videoSettingsButton"))
        #endif
        try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "settings after synthetic subtitle cue")
        try activate(element("videoSettingsTab-subtitles"))

        let earlier = element("videoSubtitleEarlierButton")
        for _ in 0..<8 where !earlier.isHittable {
            #if os(tvOS)
            XCUIRemote.shared.press(.down)
            #else
            element("videoSettingsPanel").swipeUp()
            #endif
        }
        try waitForHittable(earlier, timeout: 10, named: "subtitle timing earlier control")
        let timing = element("videoSubtitleTimingValue")
        try waitForExistence(timing, timeout: 10, named: "subtitle timing value")
        let timingValue: () -> String = {
            (timing.value as? String) ?? timing.label
        }
        let initialTiming = timingValue()
        try activate(earlier)
        XCTAssertTrue(waitUntil(timeout: 45) {
            let currentTiming = timingValue()
            return (currentTiming == "-0.5" || currentTiming == "−0.5")
                && !self.element("videoStreamChanging").exists
        }, "Earlier must shift the packaged MKV subtitle timing by 0.5 seconds.")
        try activate(element("videoSubtitleLaterButton"))
        XCTAssertTrue(waitUntil(timeout: 45) {
            timingValue() == initialTiming && !self.element("videoStreamChanging").exists
        }, "Later must undo the prior 0.5-second timing adjustment.")
        try activate(earlier)
        XCTAssertTrue(waitUntil(timeout: 45) {
            let currentTiming = timingValue()
            return (currentTiming == "-0.5" || currentTiming == "−0.5")
                && !self.element("videoStreamChanging").exists
        }, "Earlier must remain available after a timing repackaging.")
        try activate(element("videoSubtitleTimingResetButton"))
        XCTAssertTrue(waitUntil(timeout: 45) {
            timingValue() == initialTiming && !self.element("videoStreamChanging").exists
        }, "Reset must restore the original subtitle timing before changing delivery paths.")
        XCTAssertFalse(element("videoStreamError").exists, "Subtitle timing changes must keep the paused MKV playable.")

        #if os(iOS)
        if !element("videoSettingsTab-video").isHittable {
            element("videoSettingsTab-subtitles").swipeRight()
        }
        #endif
        try activate(element("videoSettingsTab-video"))
        let quality = element("videoQualityPicker")
        try waitForHittable(quality, timeout: 10, named: "480p stream quality")
        try activate(quality)
        try activate(menuAction("Up to 480p"))
        XCTAssertTrue(waitUntil(timeout: 45) {
            (quality.value as? String)?.contains("480p") == true && !self.element("videoStreamChanging").exists
        }, "Selecting 480p must repackage the paused MKV and retain the setting.")
        XCTAssertTrue(element("videoSettingsPanel").exists, "Quality selection must keep playback settings open.")
        XCTAssertFalse(element("videoStreamError").exists, "The supported 480p request must not discard the paused MKV.")

        let info = element("videoSettingsTab-info")
        #if os(iOS)
        if !info.isHittable {
            element("videoSettingsTab-video").swipeRight()
        }
        #endif
        try activate(info)
        let method = element("videoPlaybackMethod")
        try waitForExistence(method, timeout: 10, named: "actual post-quality stream method")
        XCTAssertTrue(accessibilityText(method).contains("HLS"),
                      "The 480p cap must actually switch the 540p fixture to server HLS before HLS-only caption assertions run.")

        #if os(iOS)
        if !element("videoSettingsTab-subtitles").isHittable {
            info.swipeLeft()
        }
        #endif
        try activate(element("videoSettingsTab-subtitles"))
        try waitForHittable(subtitle, timeout: 20, named: "embedded MKV subtitle track after quality repackaging")
        try activate(subtitle)
        XCTAssertTrue(waitUntil(timeout: 8) { (subtitle.value as? String) == "Selected" },
                      "The synthetic embedded subtitle track must remain selectable after quality repackaging.")
        XCTAssertFalse(earlier.exists, "Server HLS captions must not expose a timing adjustment the native source cannot apply.")
        XCTAssertFalse(timing.exists, "Server HLS captions must not show a stale subtitle timing value.")
        try attachPlaybackFrame(named: "Paused MKV quality and subtitle timing controls")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(element("videoSettingsDoneButton"))
        #endif
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after paused MKV playback")
        completed = true
    }

    func journeyNativePlaybackSpeedAndChaptersRepackagePausedMKV() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.convertedItemID)
        let time = try startPlayback(using: "playButton")
        let startedAt = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= startedAt + 1 }

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("Media Controls"), timeout: 8, named: "paused chapter fixture transport")
        #else
        try tapVideoTransportAfterChromeHides(named: "chapter fixture pause control")
        #endif
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5 },
                       "Playback must stay paused while changing speed, chapters, and quality.")

        #if os(tvOS)
        try openTVPlaybackSettings()
        #else
        try activate(element("videoSettingsButton"))
        #endif
        try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "paused chapter playback settings")
        try activate(element("videoSettingsTab-video"))
        let speed = element("videoSpeedPicker")
        for _ in 0..<8 where !speed.isHittable {
            #if os(tvOS)
            XCUIRemote.shared.press(.down)
            #else
            element("videoSettingsPanel").swipeUp()
            #endif
        }
        try waitForHittable(speed, timeout: 10, named: "playback speed picker")
        try activate(speed)
        try activate(menuAction("1.5×"))
        XCTAssertTrue(waitUntil(timeout: 8) { (speed.value as? String)?.contains("1.5") == true },
                      "Selecting 1.5× must update the native playback speed.")

        #if os(iOS)
        if !element("videoSettingsTab-chapters").isHittable {
            element("videoSettingsTab-video").swipeLeft()
        }
        #endif
        try activate(element("videoSettingsTab-chapters"))
        let middle = element("videoChapter-300000000")
        try waitForHittable(middle, timeout: 15, named: "Middle chapter")
        try activate(middle)
        XCTAssertTrue(waitUntil(timeout: 20) {
            (middle.value as? String) == "Selected"
                && (Double(time.value as? String ?? "") ?? 0) >= 30
                && (Double(time.value as? String ?? "") ?? 90) < 35
        }, "Selecting Middle must seek the paused fixture to its 30-second chapter.")
        let chapterTime = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? chapterTime) > chapterTime + 0.5 },
                       "Chapter selection must retain the paused playback state.")

        #if os(iOS)
        if !element("videoSettingsTab-video").isHittable {
            element("videoSettingsTab-chapters").swipeRight()
        }
        #endif
        try activate(element("videoSettingsTab-video"))
        let quality = element("videoQualityPicker")
        try waitForHittable(quality, timeout: 10, named: "720p quality after chapter seek")
        try activate(quality)
        try activate(menuAction("Up to 720p"))
        XCTAssertTrue(waitUntil(timeout: 45) {
            (quality.value as? String)?.contains("720p") == true && !self.element("videoStreamChanging").exists
        }, "Quality replacement must finish while retaining the paused chapter position.")
        XCTAssertTrue(waitUntil(timeout: 20) {
            let currentTime = Double(time.value as? String ?? "") ?? 0
            return currentTime >= 30 && currentTime < 35
        }, "Quality replacement must retain the selected chapter position.")
        let replacementTime = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? replacementTime) > replacementTime + 0.5 },
                       "Quality replacement must retain the paused playback state.")
        XCTAssertTrue((speed.value as? String)?.contains("1.5") == true,
                      "A quality replacement must retain the selected playback speed.")
        XCTAssertTrue(element("videoSettingsPanel").exists, "Changing quality must keep the settings panel open.")
        XCTAssertFalse(element("videoStreamError").exists, "The paused chapter fixture must remain playable after quality replacement.")
        try attachPlaybackFrame(named: "Paused MKV speed, chapters, and quality controls")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 8))
        try waitForExistence(element("Media Controls"), timeout: 8, named: "native transport after settings")
        XCUIRemote.shared.press(.playPause)
        #else
        try activate(element("videoSettingsDoneButton"))
        if !element("videoPlayPauseButton").isHittable {
            try activate(element("videoControlsSurface"))
        }
        try activate(element("videoPlayPauseButton"))
        #endif
        #if os(iOS)
        let resumedControl = element("videoPlayPauseButton")
        try waitForExistence(resumedControl, timeout: 8, named: "resumed chapter fixture transport")
        XCTAssertTrue(waitUntil(timeout: 8) { resumedControl.label == "Pause" },
                      "Resuming after a paused quality replacement must restore the Pause transport state.")
        #endif
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= replacementTime + 1 }
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after paused chapter playback")
        completed = true
    }

    func openRequestVault(expectedScreen: String, named name: String) throws {
        let detail = element("requestDetailScreen")
        let view = element("requestViewVaultButton")
        try revealDetailElement(view, in: detail, named: "\(name) View action", requiresHittable: true)
        try activate(view)
        try waitForExistence(element(expectedScreen), timeout: 25, named: "\(name) vault destination")
        XCTAssertFalse(element("requestVaultLookupError").exists,
                       "Viewing \(name) from Requests could not resolve its authenticated vault title.")
    }

    func assertRequestUsesVaultOnly(named name: String) {
        XCTAssertTrue(element("requestViewVaultButton").exists, "\(name) must expose View.")
        XCTAssertFalse(element("requestConfirmButton").exists || element("requestSeasonPicker").exists || element("requestLanguagePicker").exists,
                       "\(name) must expose View only, without duplicate Request controls.")
    }

    func returnToRequestDetailFromVault() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        let back = application!.buttons.matching(identifier: "BackButton").firstMatch
        if usesStandardPhonePushedChrome {
            XCTAssertFalse(hasVisibleFrame(back),
                           "A standard-phone Requests vault destination must use native leading-edge Back.")
            try performStandardPhoneEdgeGesture(named: "Requests vault destination")
        } else {
            try waitForHittable(back, timeout: 15, named: "vault destination Back")
            try activate(back)
        }
        #endif
        try waitForHittable(element("requestViewVaultButton"), timeout: 20, named: "Requests detail after vault Back")
    }

    func closeRequestDetailToRequests() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(element("requestDetailsCloseButton"))
        #endif
        try waitForHittable(element("requestsScreen"), timeout: 20, named: "Requests after detail Back")
    }

    func revealRequestPoster(_ key: String, named name: String) throws -> XCUIElement {
        let poster = element("requestPoster-\(key)")
        #if os(iOS)
        let requests = element("requestsScreen")
        try waitForExistence(requests, timeout: 15, named: "Requests browse catalog")
        // Native search deliberately debounces remote catalog loading. Establish that
        // the requested card has reached the observable UI before the viewport
        // helper starts scrolling; otherwise it can complete all bounded drags
        // against the pre-search grid on a compact Duo display.
        try waitForExistence(poster, timeout: 20, named: name)
        for _ in 0..<8 {
            if poster.exists {
                try revealInLibraryViewport([poster], in: requests)
                if hasVisibleFrame(poster) && poster.isHittable { return poster }
            }
            try scrollLibrary(requests, up: true)
        }
        throw ProofFailure.timeout("Requests browse did not reveal the \(name) poster.")
        #else
        try waitForHittable(poster, timeout: 20, named: name)
        return poster
        #endif
    }

    func replaceText(_ field: XCUIElement, with text: String) throws {
        try waitForHittable(field, timeout: 10, named: "search field")
        try activate(field)
        let clear = application!.buttons.matching(NSPredicate(format: "label == %@", "Clear text"))
            .allElementsBoundByIndex.first(where: { hasVisibleFrame($0) && $0.isHittable })
        if let clear {
            try activate(clear)
        } else if let current = field.value as? String, !current.isEmpty {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
        }
        field.typeText(text)
        if field.identifier == "settingsSearchField" { field.typeText("\n") }
    }

    private func revealVideoBrowseControl(_ control: XCUIElement, named name: String) throws {
        let options = element("videoOptionsMenu")
        try waitForHittable(options, timeout: 10, named: "Video options")
        try activate(options)
        try waitForHittable(control, timeout: 10, named: name)
    }

    func revealDetailElement(
        _ element: XCUIElement,
        in detail: XCUIElement,
        named name: String,
        requiresHittable: Bool
    ) throws {
        #if os(iOS)
        let applicationViewport = try applicationViewport()
        var viewport = detail.frame.intersection(applicationViewport).insetBy(dx: 0, dy: 2)
        if usesStandardPhonePushedChrome,
           ["detailScreen", "videoCollectionScreen"].contains(detail.identifier) {
            // The compact pushed-screen header is deliberately opaque. A
            // control beneath it or the tab bar is not a visible target, even
            // if accessibility reports it as hittable through the view tree.
            viewport = try standardPhoneNavigationViewport()
        }
        for _ in 0..<8 {
            guard element.exists else {
                // Lazy stacks don't expose an offscreen child to accessibility
                // until a real scroll materializes it.
                try dragVertically(in: viewport, anchorX: viewport.midX, up: true)
                continue
            }
            let frame = element.frame
            let visible = frame.intersection(viewport)
            let hasRequiredVisibility = requiresHittable
                ? isFullyVerticallyContained(frame, in: viewport)
                : !visible.isNull && visible.height >= min(44, frame.height)
            if element.identifier.hasPrefix("videoEpisode-"), hasRequiredVisibility,
               frame.minX < viewport.minX || frame.maxX > viewport.maxX {
                let swipeLeft = frame.midX > viewport.midX
                let start = detail.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
                    dx: viewport.width * (swipeLeft ? 0.8 : 0.2) + viewport.minX - detail.frame.minX,
                    dy: visible.midY - detail.frame.minY
                ))
                let end = detail.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(
                    dx: viewport.width * (swipeLeft ? 0.2 : 0.8) + viewport.minX - detail.frame.minX,
                    dy: visible.midY - detail.frame.minY
                ))
                start.press(forDuration: 0.05, thenDragTo: end)
                continue
            }
            if hasRequiredVisibility, element.isEnabled,
               (!requiresHittable || (hasVisibleFrame(element) && element.isHittable)) {
                return
            }
            let anchor = self.element("playButton")
            let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : frame.midX
            try dragVertically(in: viewport, anchorX: anchorX, up: frame.midY >= viewport.midY)
        }
        let finalFrame = element.exists ? String(describing: element.frame) : "not materialized"
        throw ProofFailure.ui("\(name) could not be revealed: element=\(finalFrame), viewport=\(viewport).")
        #else
        try waitForHittable(element, timeout: 15, named: name)
        #endif
    }

    private func waitForSettings(_ api: ProofAPI, identity: APIIdentity,
                         until predicate: ([String: Any]) -> Bool) async throws -> [String: Any] {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            let values = try await api.settingsJSON(identity: identity)
            if predicate(values) { return values }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw ProofFailure.timeout("The Account setting did not persist through the native Settings API.")
    }

    func waitForNativeRequest(_ api: ProofAPI, identity: APIIdentity, key: String, excluding: Set<String> = []) async throws -> APIRequestRecord {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if let record = try await api.requestRecords(identity: identity).first(where: { $0.key == key && !excluding.contains($0.id) }) {
                return record
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw ProofFailure.timeout("The submitted request did not persist through the native Requests API.")
    }

    func waitForWatchlist(_ api: ProofAPI, identity: APIIdentity, itemID: String, included: Bool) async throws {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if try await api.watchlistIncluded(identity: identity, itemID: itemID) == included { return }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw ProofFailure.timeout("The detail watchlist action did not persist through the native Watchlist API.")
    }

    func attachPlaybackFrame(named name: String) throws -> XCUIScreenshot {
        let screenshot = try applicationWindow().screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        return screenshot
    }

    private func assertSyntheticSubtitleVisible(in screenshot: XCUIScreenshot) throws {
        #if canImport(UIKit) && canImport(Vision)
        let image = screenshot.image
        let cgImage = try XCTUnwrap(image.cgImage, "The playback screenshot must expose pixels for subtitle OCR.")
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["en-US"]
        try VNImageRequestHandler(cgImage: cgImage, orientation: .up).perform([request])
        let recognized = request.results?
            .flatMap { $0.topCandidates(3).map(\.string) }
            .joined(separator: " ") ?? ""
        let normalized = recognized.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        XCTAssertTrue(normalized.contains("synthetic subtitle timing proof"),
                      "The selected synthetic subtitle must visibly render its authored cue inside the paused 2–7 second frame. OCR saw: \(recognized)")
        #else
        throw ProofFailure.ui("This native UI target must support Vision OCR for the subtitle rendering contract.")
        #endif
    }

    func attachFrame(named name: String) throws {
        let window = try applicationWindow()
        let attachment = XCTAttachment(screenshot: window.screenshot())
        let frame = window.frame
        attachment.name = "\(name) — \(Int(frame.width))×\(Int(frame.height)) pt"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func captureScreenshot(succeeded: Bool) {
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

    func applicationWindow() throws -> XCUIElement {
        let window = visibleApplicationWindow ?? application!.windows.firstMatch
        try waitForExistence(window, timeout: 10, named: "main application window")
        guard !window.frame.isEmpty else {
            throw ProofFailure.ui("The main application window had an empty viewport frame.")
        }
        return window
    }

    func applicationViewport() throws -> CGRect {
        try applicationWindow().frame
    }

    func loadFixture() throws -> ProofFixture {
        if let cachedFixture { return cachedFixture }
        let bundle = Bundle(for: Self.self)
        if let config = bundle.url(forResource: "FixtureBroker", withExtension: "json") {
            let settings = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: config))
            guard let value = settings["brokerURL"], let broker = URL(string: value) else { throw ProofFailure.invalidFixture }
            _ = try localOrigin(from: "http://\(broker.host ?? ""):\(broker.port ?? 80)")
            // Full-tier shards can run two methods of the same XCTestCase
            // concurrently. Give each test instance its own disposable server
            // so one teardown cannot stop a sibling method's fixture.
            let fixtureKey = "\(String(describing: type(of: self)))-\(isolationID)"
            let url = broker.appendingPathComponent(fixtureKey)
            brokerFixtureURL = url
            var request = URLRequest(url: url)
            request.timeoutInterval = 295
            let fixture = try JSONDecoder().decode(ProofFixture.self, from: brokerRequest(request))
            cachedFixture = fixture
            return fixture
        }
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

    func localOrigin(from value: String) throws -> URL {
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

    func makeTestAPISession(origin: URL) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 30
        return URLSession(configuration: configuration, delegate: ProofRedirectGuard(origin: origin), delegateQueue: nil)
    }

    func launchAndResetSession(extraLaunchArguments: [String] = [],
                                       extraLaunchEnvironment: [String: String] = [:]) throws {
        let app = XCUIApplication()
        application = app
        app.launchArguments.append("--ui-testing")
        app.launchArguments.append(contentsOf: extraLaunchArguments)
        app.launchEnvironment["LUMINA_UI_TEST_NAMESPACE"] = isolationID
        extraLaunchEnvironment.forEach { key, value in app.launchEnvironment[key] = value }
        app.launch()

        #if os(tvOS)
        let loginField = element("tvManualSignInButton")
        #else
        let loginField = element("serverURLField")
        #endif
        #if os(iOS)
        let qrOption = element("advancedConnectionButton")
        #endif
        #if os(tvOS)
        let destinations = ["homeScreen", "libraryScreen", "audioScreen", "deviceSettingsScreen"]
        #else
        let destinations = ["homeScreen", "libraryScreen", "audioScreen", "accountScreen"]
        #endif
        guard waitUntil(timeout: 30, condition: {
            #if os(iOS)
            if self.hasVisibleFrame(qrOption) { return true }
            #endif
            return self.hasVisibleFrame(loginField) || destinations.contains { self.hasVisibleFrame(self.element($0)) }
        }) else {
            throw ProofFailure.ui("The app did not reach the connection form or a visible destination after launch.")
        }

        #if os(iOS)
        if hasVisibleFrame(qrOption) { return }
        #endif
        if !hasVisibleFrame(loginField) {
            try signOutThroughAccount()
            #if os(iOS)
            try revealManualConnection()
            #endif
            try waitForExistence(loginField, timeout: 20, named: "connection form after sign out")
        }
    }

    #if os(iOS)
    func revealManualConnection() throws {
        guard !hasVisibleFrame(element("serverURLField")) else { return }
        try waitForExistence(element("qrPairingLanding"), timeout: 20, named: "QR pairing before Advanced")
        let advanced = element("advancedConnectionButton")
        try waitForHittable(advanced, timeout: 20, named: "Advanced manual connection")
        XCTAssertFalse(element("passwordField").exists, "Manual credentials stay hidden while scanning.")
        try activate(advanced)
    }
    #endif

    #if os(tvOS)
    func revealTVManualConnection() throws {
        if !hasVisibleFrame(element("serverURLField")) {
            try activate(element("tvManualSignInButton"))
        }
    }
    #endif

    func signIn(using fixture: ProofFixture, waitsForAccount: Bool = true) throws {
        #if os(iOS)
        try revealManualConnection()
        #elseif os(tvOS)
        try revealTVManualConnection()
        #endif
        let serverField = element("serverURLField")
        let usernameField = element("usernameField")
        let passwordField = element("passwordField")

        try waitForExistence(serverField, timeout: 20, named: "server URL field")
        #if os(iOS)
        try enter(fixture.serverURL, into: serverField)
        serverField.typeText("\n")
        try waitForExistence(usernameField, timeout: 10, named: "username field after Next")
        usernameField.typeText(fixture.username)
        try revealConnectionField(passwordField)
        try waitForHittable(passwordField, timeout: 10, named: "password field after Username")
        passwordField.tap()
        passwordField.typeText(fixture.password)
        let connect = element("connectButton")
        try revealConnectionField(connect)
        try activate(connect)
        #else
        try enter(fixture.serverURL, into: serverField)
        try enter(fixture.username, into: usernameField)
        try enter(fixture.password, into: passwordField)
        try activate(element("connectButton"))
        #endif

        let library = element("homeScreen")
        guard waitUntil(timeout: 40, condition: { self.hasVisibleFrame(library) }) else {
            if element("connectionError").exists {
                throw ProofFailure.ui(element("connectionError").label)
            }
            throw ProofFailure.ui("Real form login did not reach Home.")
        }
        #if os(iOS)
        try waitForPostLoginControls(waitsForAccount: waitsForAccount)
        #endif
    }

}
