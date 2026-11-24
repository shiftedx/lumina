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
    func journeyMusicAdvancedControlsPausedRestoreAndQueueConflict() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try launchAndResetSession()
        try signIn(using: fixture)
        #if os(iOS)
        if UIDevice.current.userInterfaceIdiom == .pad {
            XCUIDevice.shared.orientation = .landscapeLeft
            XCTAssertTrue(waitUntil(timeout: 10) {
                let frame = self.application!.windows.firstMatch.frame
                return frame.width > frame.height
            }, "The iPad listening-options proof must use its landscape player viewport.")
        }
        #endif
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "album queue before restoration")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album queue before restoration")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "album's advanced player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 4 }
        #if os(iOS)
        let advancedViewport = try applicationViewport()
        let usesWideAudioLayout = !UIApplication.shared.preferredContentSizeCategory.isAccessibilityCategory
            && advancedViewport.width >= 640 && advancedViewport.height >= 560
        #endif
        try revealMusicControl(element("audioPlayPauseButton"))
        try activate(element("audioPlayPauseButton"))
        #if os(iOS)
        if usesWideAudioLayout {
            let close = element("closePlayerButton")
            try waitForHittable(close, timeout: 15, named: "wide player close")
            let options = element("audioOptionsButton")
            try waitForHittable(options, timeout: 15, named: "wide listening options menu")
            try activate(options)
            if !waitUntil(timeout: 3, condition: { self.element("audioListeningOptions").exists }) {
                try activate(menuAction("Listening options"))
            }
            try waitForExistence(element("audioOptionsSheet"), timeout: 15, named: "wide listening options sheet")
        }
        #endif
        try revealMusicControl(element("audioListeningOptions"))
        try activate(element("audioListeningOptions"))
        XCTAssertTrue(waitUntil(timeout: 5, condition: {
            let header = self.element("audioListeningOptions")
            return header.exists && header.value as? String == "Expanded"
        }), "Listening options must expand before its controls are queried")
        try revealMusicControl(element("audioNormalizeToggle"))
        XCTAssertTrue(element("audioNormalizeToggle").isEnabled, "Imported AAC must support buffered normalization")
        try setNativeSwitch("audioNormalizeToggle", to: false)
        try setNativeSwitch("audioNormalizeToggle", to: true)
        try revealMusicControl(element("audioCrossfadePicker"))
        try activate(element("audioCrossfadePicker"))
        try activate(menuAction("3 seconds"))
        try revealMusicControl(element("audioSleepTimerButton"))
        try activate(element("audioSleepTimerButton"))
        try activate(menuAction("15 minutes"))
        XCTAssertFalse(element("audioSleepTimerButton").label.contains("Off"))
        #if os(iOS)
        XCTAssertFalse(element("audioOutput").exists,
                       "iOS listening options must not hide output routing in a disclosure.")
        if usesWideAudioLayout {
            try activate(application!.buttons.matching(NSPredicate(format: "label == %@", "Done")).firstMatch)
            XCTAssertTrue(waitUntil(timeout: 10) { !self.hasVisibleFrame(self.element("audioOptionsSheet")) },
                          "Closing listening options must return to the persistent wide player controls.")
        }
        let airPlay = element("audioAirPlayButton")
        let chromecast = element("musicChromecastButton")
        try waitForHittable(airPlay, timeout: 15, named: "persistent AirPlay control")
        try waitForHittable(chromecast, timeout: 15, named: "persistent Chromecast control")
        XCTAssertGreaterThanOrEqual(airPlay.frame.width, 44, "AirPlay needs a 44 pt touch target.")
        XCTAssertGreaterThanOrEqual(airPlay.frame.height, 44, "AirPlay needs a 44 pt touch target.")
        XCTAssertGreaterThanOrEqual(chromecast.frame.width, 44, "Chromecast needs a 44 pt touch target.")
        XCTAssertGreaterThanOrEqual(chromecast.frame.height, 44, "Chromecast needs a 44 pt touch target.")
        XCTAssertTrue(airPlay.isEnabled && chromecast.isEnabled,
                      "A local session must expose, but not open, its native output pickers.")
        XCTAssertFalse(element("audioRefreshSpeakersButton").exists)
        XCTAssertFalse(application!.switches["Compatibility mode for Sonos S1"].exists)
        XCTAssertFalse(application!.buttons["Refresh Sonos speakers"].exists)
        XCTAssertFalse(application!.staticTexts["Sonos receives at most the next 100 tracks. The local player pauses after the speaker acknowledges the transfer."].exists)
        XCTAssertFalse(application!.sliders["Music volume"].exists,
                       "Music must use device volume rather than an app volume slider.")
        #endif
        try revealMusicControl(element("audioLyrics"))
        #if os(iOS)
        let lyricsInitiallyCollapsed = !usesWideAudioLayout
        #else
        let lyricsInitiallyCollapsed = true
        #endif
        if lyricsInitiallyCollapsed {
            XCTAssertEqual(element("audioLyrics").value as? String, "Collapsed",
                           "Lyrics must remain collapsed until explicitly opened, including after loading.")
            try activate(element("audioLyrics"))
            XCTAssertEqual(element("audioLyrics").value as? String, "Expanded")
        } else {
            try activate(element("audioLyrics"))
        }
        let lyric = try XCTUnwrap(fixture.audioLyric)
        try waitForExistence(menuAction(lyric), timeout: 20, named: "timed synthetic lyrics")
        try attachFrame(named: "Native listening options and imported timed lyrics")
        let pausedBeforeBackground = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        #if os(iOS)
        try backgroundApplication()
        #else
        XCUIRemote.shared.press(.home)
        #endif
        try await Task.sleep(for: .seconds(3))
        application!.activate()
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "paused queue after background flush")
        let pausedAfterBackground = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(pausedAfterBackground, pausedBeforeBackground + 0.5,
                                 "Backgrounding resumed the paused queue before it could be restored.")
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        // Pause is a user action. Its saved queue survives relaunch, but the
        // compact control is intentionally absent until the user resumes.
        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 40, named: "Home after paused queue relaunch")
        let restoredSession = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        let restoredQueue = restoredSession?["queue"] as? [[String: Any]]
        let normalizedTrackID = fixture.audioTrackID.replacingOccurrences(of: "-", with: "").lowercased()
        XCTAssertTrue(restoredQueue?.contains(where: {
            ($0["id"] as? String)?.replacingOccurrences(of: "-", with: "").lowercased() == normalizedTrackID
        }) == true, "Relaunch must retain the paused queue on the shared session.")
        XCTAssertNotNil(restoredSession?["current_index"] as? Int,
                         "Relaunch must retain the paused queue's selected entry.")
        guard waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(self.element("nowPlayingBar")) }) else {
            throw ProofFailure.ui("A paused queue must not leave a visible compact now-playing bar after relaunch.")
        }
        #if os(tvOS)
        // The Siri Remote is the deliberate resume affordance while browsing.
        XCUIRemote.shared.press(.playPause)
        let restoredBar = element("nowPlayingBar")
        #if LUMINA_UI_TESTING
        print("PAUSED_RESTORE_AFTER_REMOTE \(element("homeScreen").value ?? "absent")")
        #endif
        let restoredAfterRemote = waitUntil(timeout: 20, condition: { self.hasVisibleFrame(restoredBar) })
        #if LUMINA_UI_TESTING
        print("PAUSED_RESTORE_AFTER_WAIT \(element("homeScreen").value ?? "absent")")
        #endif
        guard restoredAfterRemote else {
            throw ProofFailure.ui("The Siri Remote did not resume the paused shared queue after relaunch.")
        }
        try activate(restoredBar)
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "resumed audio stage")
        XCUIRemote.shared.press(.playPause)
        #else
        // iPhone requires an explicit album action after relaunch; this starts
        // the next user-owned listening session without auto-resuming audio.
        try selectDestination("Audio", screen: "audioScreen")
        let freshAlbum = try revealMusicAlbum(fixture, named: "fresh album in the same app process")
        try activate(freshAlbum)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album after paused queue relaunch")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "explicitly resumed iPhone audio stage")
        try activate(element("audioPlayPauseButton"))
        #endif
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(self.element("audioPlayerStage"))
                && !self.hasVisibleFrame(self.element("nowPlayingBar"))
                && self.element("audioPlayPauseButton").label == "Play"
        }) else {
            throw ProofFailure.ui("The explicitly paused restored session did not keep its player open without a compact bar.")
        }
        let paused = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        try await Task.sleep(for: .seconds(3))
        let stillPaused = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, paused + 0.5)
        // A second real API client changes the queue, then this device's repeat edit must conflict.
        try await api.replaceProofMusicSession(identity: identity,
                                                itemIDs: [fixture.audioSecondTrackID, fixture.audioSecondTrackID, fixture.audioTrackID],
                                                position: 12)
        try revealMusicControl(element("audioRepeatButton"))
        try activate(element("audioRepeatButton"))
        #if os(iOS)
        if usesWideAudioLayout {
            try activate(element("audioOptionsButton"))
            try activate(menuAction("Listening options"))
            try waitForExistence(element("audioOptionsSheet"), timeout: 15, named: "wide conflict listening options sheet")
        }
        #endif
        try revealMusicControl(element("audioUseServerButton"))
        try waitForExistence(element("audioKeepDeviceButton"), timeout: 20, named: "explicit device/server conflict choices")
        try attachFrame(named: "Queue revision conflict requires a deliberate choice")
        try activate(element("audioUseServerButton"))
        #if os(iOS)
        if usesWideAudioLayout {
            try activate(application!.buttons.matching(NSPredicate(format: "label == %@", "Done")).firstMatch)
            XCTAssertTrue(waitUntil(timeout: 10) { !self.hasVisibleFrame(self.element("audioOptionsSheet")) },
                          "The wide conflict choice must return to the inline player.")
        }
        #endif
        guard waitUntil(timeout: 20, condition: {
            self.hasVisibleFrame(self.element("audioPlayerStage"))
                && self.currentAudioTitle() == fixture.audioSecondTrackTitle
                && self.element("audioPlayPauseButton").label == "Play"
        }) else {
            throw ProofFailure.ui("Choosing the server queue did not retain the paused player with its selected track")
        }
        try revealMusicControl(element("audioQueueButton"))
        try activate(element("audioQueueButton"))
        try waitForExistence(element("audioQueueScreen"), timeout: 15, named: "server queue with duplicate tracks")
        let duplicateRows = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@",
                                                                         "audioQueueItem-\(fixture.audioSecondTrackID)-"))
        guard waitUntil(timeout: 10, condition: { duplicateRows.count == 2 }) else {
            throw ProofFailure.ui("Duplicate queue entries did not keep distinct stable identities.")
        }
        let duplicateIDs = Set(duplicateRows.allElementsBoundByIndex.map(\.identifier))
        #if os(tvOS)
        let queueActions = element("audioQueueScreen").buttons.containing(
            NSPredicate(format: "elementType == %d AND label == %@", XCUIElement.ElementType.image.rawValue, "Track actions")
        )
        #else
        let queueActions = application!.buttons.matching(NSPredicate(format: "label == %@", "Track actions"))
        #endif
        try waitForExistence(queueActions.element(boundBy: 0), timeout: 10, named: "first duplicate queue action")
        try activate(queueActions.element(boundBy: 0))
        try activate(menuAction("Move down"))
        XCTAssertEqual(Set(duplicateRows.allElementsBoundByIndex.map(\.identifier)), duplicateIDs,
                       "Reordering must preserve both duplicate occurrence identities.")
        try activate(element("audioQueueDoneButton"))
        #if os(iOS)
        if usesWideAudioLayout {
            try activate(element("audioNextButton"))
        } else {
            let artwork = element("audioAlbumArtwork")
            let stage = application!.scrollViews.matching(identifier: "audioPlayerStage").firstMatch
            for _ in 0..<8 where !artwork.exists || artwork.frame.minY < -2 {
                stage.swipeDown()
            }
            try waitForHittable(artwork, timeout: 15, named: "Artwork after returning from listening options")
            application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.4))
                .press(forDuration: 0.1, thenDragTo: application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.4)))
        }
        #else
        let artwork = element("audioAlbumArtwork")
        try revealMusicControl(artwork)
        XCTAssertTrue(artwork.hasFocus, "Queue skipping must return focus to the artwork surface.")
        XCUIRemote.shared.press(.right)
        #endif
        guard waitUntil(timeout: 20, condition: { self.currentAudioTitle() == fixture.audioTrackTitle }) else {
            throw ProofFailure.ui("Moving a duplicate queue entry did not preserve the next occurrence.")
        }
        try revealMusicControl(element("audioQueueButton"))
        try activate(element("audioQueueButton"))
        try waitForExistence(queueActions.element(boundBy: 0), timeout: 10, named: "remaining duplicate queue action")
        try activate(queueActions.element(boundBy: 0))
        try activate(menuAction("Remove from queue"))
        guard waitUntil(timeout: 10, condition: {
            duplicateRows.count == 0 && self.element("audioQueueItem-\(fixture.audioSecondTrackID)").exists
                && self.element("audioQueueItem-\(fixture.audioTrackID)").exists
        }) else {
            throw ProofFailure.ui("Removing a duplicate queue entry did not preserve the currently selected occurrence.")
        }
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        try activate(element("audioQueueDoneButton"))
        #if os(iOS)
        if usesWideAudioLayout {
            try activate(element("audioOptionsButton"))
            try activate(menuAction("Stop"))
        } else {
            try stopAudioPlayer()
        }
        #else
        try stopAudioPlayer()
        #endif
        guard disappears(element("playbackScreen"), timeout: 20) else {
            throw ProofFailure.ui("Stop did not dismiss the restored player")
        }
        // Relaunch restored the queue on Home, without restoring a navigation stack.
        #if os(iOS)
        // Stop returns to the pushed album collection on iPad. Pop that public
        // detail before choosing Music again from the unified category shell;
        // standard phone keeps its own established destination reset.
        if usesStandardPhonePushedChrome {
            try selectDestination("Audio", screen: "audioScreen")
        } else {
            try musicBack()
            try waitForExistence(element("audioScreen"), timeout: 20, named: "Music root after stopping the restored player")
        }
        #else
        try selectDestination("Audio", screen: "audioScreen")
        #endif
        let postStopAlbum = try revealMusicAlbum(fixture, named: "fresh album in the same app process")
        try activate(postStopAlbum)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "fresh album in the same app process")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "new playback after Stop")
        try revealMusicControl(element("audioRepeatButton"))
        try activate(element("audioRepeatButton"))
        // A new queue must keep publishing changes after the old debounce was cancelled.
        let expectedIDs = [fixture.audioTrackID, fixture.audioSecondTrackID].map {
            $0.replacingOccurrences(of: "-", with: "").lowercased()
        }
        var publishedNewQueue = false
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if let saved = try await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let queue = saved["queue"] as? [[String: Any]] {
                let IDs = queue.compactMap { $0["id"] as? String }.map {
                    $0.replacingOccurrences(of: "-", with: "").lowercased()
                }
                if IDs == expectedIDs, saved["current_index"] as? Int == 0, saved["repeat"] as? String == "all" {
                    publishedNewQueue = true
                    break
                }
            }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertTrue(publishedNewQueue, "The next queue's selected track and Repeat change must reach the real server after Stop without relaunching.")
        #if os(iOS)
        if usesWideAudioLayout {
            let close = element("closePlayerButton")
            try waitForHittable(close, timeout: 15, named: "wide close after resumed playback")
            try activate(close)
            guard disappears(element("playbackScreen"), timeout: 15),
                  waitUntil(timeout: 15, condition: { self.hasVisibleFrame(self.element("nowPlayingBar")) }) else {
                throw ProofFailure.ui("Wide Close must return to the active now-playing bar without stopping audio.")
            }
            try activate(element("nowPlayingBar"))
            try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "wide player reopened after Close")
            try activate(element("audioOptionsButton"))
            try activate(menuAction("Stop"))
        } else {
            try stopAudioPlayer()
        }
        #else
        try stopAudioPlayer()
        #endif
        completed = true
    }

    func currentAudioTitle() -> String {
        #if os(iOS)
        let artwork = element("audioAlbumArtwork")
        if artwork.exists, let title = artwork.value as? String { return title }
        #endif
        let title = element("nowPlayingTitle")
        return title.exists ? title.label : ""
    }

    func journeyMusicFileQuality() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        #if os(iOS)
        try activate(element("musicOptionsMenu"))
        try activate(application!.buttons["Artists"].firstMatch)
        let artist = element("audioItem-\(fixture.audioArtistID)")
        try waitForExistence(artist, timeout: 20, named: "artist in music catalog")
        try activate(artist)
        let artistAlbum = element("musicItem-\(fixture.audioAlbumID)")
        try waitForExistence(artistAlbum, timeout: 20, named: "artist album")
        application!.swipeUp()
        XCTAssertTrue(waitUntil(timeout: 20) { (artistAlbum.value as? String)?.contains("kbps avg") == true })
        XCTAssertTrue((artistAlbum.value as? String)?.contains("AAC") == true)
        try attachFrame(named: "Artist albums with quiet source-file captions")
        try activate(artistAlbum)
        #else
        try activate(revealMusicAlbum(fixture, named: "file-quality album"))
        #endif
        let summary = element("musicAlbumSourceQuality")
        try waitForExistence(summary, timeout: 25, named: "album source-file summary")
        XCTAssertTrue((summary.value as? String)?.contains("AAC") == true)
        XCTAssertTrue((summary.value as? String)?.contains("kHz") == true)
        XCTAssertTrue((summary.value as? String)?.contains("kbps avg") == true)
        let details = element("musicFileDetails")
        try revealMusicControl(details)
        try activate(details)
        XCTAssertTrue(waitUntil(timeout: 10) { (details.value as? String) == "Expanded" })
        try waitForExistence(application!.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Bit depth, Not reported")).firstMatch, timeout: 10, named: "truthful lossy bit-depth readout")
        #if os(iOS)
        application!.swipeUp()
        #endif
        try attachFrame(named: "Album source-file details expanded")
        try revealMusicControl(details)
        try activate(details)
        let track = element("musicItem-\(fixture.audioTrackID)")
        try waitForExistence(track, timeout: 15, named: "track source-file facts")
        #if os(tvOS)
        XCTAssertTrue(track.label.contains("AAC"))
        XCTAssertTrue(track.label.contains("kbps"))
        try activate(track)
        XCTAssertTrue(waitUntil(timeout: 25) { (track.value as? String) == "Playing" })
        try activate(element("musicArtistButton"))
        let artistAlbum = element("musicItem-\(fixture.audioAlbumID)")
        try waitForExistence(artistAlbum, timeout: 20, named: "artist album")
        XCTAssertTrue(waitUntil(timeout: 20) { (artistAlbum.value as? String)?.contains("kbps avg") == true })
        XCTAssertTrue((artistAlbum.value as? String)?.contains("AAC") == true)
        try attachFrame(named: "Artist albums with quiet source-file captions")
        try activate(artistAlbum)
        try waitForExistence(element("musicAlbumSourceQuality"), timeout: 20, named: "album quality after artist navigation")
        #else
        XCTAssertTrue((track.value as? String)?.contains("AAC") == true)
        XCTAssertTrue((track.value as? String)?.contains("kbps") == true)
        #endif
        completed = true
    }

    func revealMusicControl(_ control: XCUIElement) throws {
        #if os(tvOS)
        try waitForExistence(control, timeout: 15, named: "music control \(control.identifier)")
        let railOrder = ["audioQueueButton", "audioPlayPauseButton", "audioRepeatButton", "audioStopButton"]
        if let railIndex = railOrder.firstIndex(of: control.identifier) {
            let artwork = element("audioAlbumArtwork")
            try waitForExistence(artwork, timeout: 15, named: "music artwork focus anchor")
            guard focus(artwork, timeout: 15) else {
                throw ProofFailure.ui("The Siri Remote could not focus the music artwork before entering its control rail.")
            }
            let queueOffset = element("audioQueueButton").isEnabled ? 0 : 1
            let presses = railIndex + 1 - min(railIndex, queueOffset)
            for _ in 0..<presses { XCUIRemote.shared.press(.down) }
            guard waitUntil(timeout: 8, condition: { control.hasFocus }) else {
                throw ProofFailure.ui("The Siri Remote could not reach the music control \(control.identifier) through its ordered rail.")
            }
            return
        }
        guard focus(control, timeout: 15) else {
            throw ProofFailure.ui("The Siri Remote could not focus the music control \(control.identifier.isEmpty ? control.label : control.identifier).")
        }
        #else
        for _ in 0..<8 {
            if control.exists && control.isHittable { return }
            // The full-player controls live in their own ScrollView. The
            // application gesture can instead target the covered detail screen,
            // leaving an offscreen listening control unreachable.
            let player = application!.scrollViews.matching(identifier: "audioPlayerStage").firstMatch
            if hasVisibleFrame(element("playbackScreen")), player.exists, hasVisibleFrame(player) {
                if control.exists, !control.frame.isEmpty, control.frame.midY < player.frame.midY {
                    player.swipeDown()
                } else {
                    player.swipeUp()
                }
            } else {
                let viewport = try applicationViewport()
                if control.exists && !control.frame.isEmpty && control.frame.midY < viewport.midY {
                    application!.swipeDown()
                } else { application!.swipeUp() }
            }
        }
        try waitForHittable(control, timeout: 10, named: "requested music control")
        #endif
    }

    func musicBack() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        if usesStandardPhonePushedChrome {
            try performStandardPhoneEdgeGesture(named: "music destination")
        } else {
            try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        }
        #endif
    }

    func revealMusicAlbum(_ fixture: ProofFixture, named name: String) throws -> XCUIElement {
        let album = element("audioItem-\(fixture.audioAlbumID)")
        #if os(tvOS)
        if !album.exists, element("tvMusicHomeBrowse").exists {
            try activate(element("tvMusicHomeBrowse"))
        } else if !album.exists, element("audioCategory-albums").exists {
            XCTAssertTrue(focus(element("audioCategory-albums"), timeout: 30), "The remote must reach Music browse below Home.")
        }
        try waitForExistence(album, timeout: 25, named: name)
        XCTAssertTrue(focus(album, timeout: 20), "The album must be focusable in the Music grid.")
        XCTAssertTrue(album.label.contains(fixture.audioAlbumTitle))
        return album
        #else
        if element("musicHomeBrowseButton").waitForExistence(timeout: 3) {
            try activate(element("musicHomeBrowseButton"))
        }
        try waitForExistence(album, timeout: 25, named: name)
        return album
        #endif
    }

    #if os(iOS)
    /// Music Home's Browse row is a full-width List button. iOS 26.5 can
    /// report that visible row as unhittable even though a physical center tap
    /// reaches the catalog, so retain the platform exception in one verified
    /// transition instead of spreading coordinate workarounds through journeys.
    func browseFromMusicHome() throws {
        let browse = element("musicHomeBrowseButton")
        guard browse.waitForExistence(timeout: 3) else {
            return
        }
        guard hasVisibleFrame(browse), browse.isEnabled else {
            throw ProofFailure.ui("Music Home Browse must be visible and enabled before selection.")
        }
        if browse.isHittable {
            browse.tap()
        } else {
            let frame = browse.frame
            let window = try applicationWindow()
            window.coordinate(withNormalizedOffset: CGVector(
                dx: (frame.midX - window.frame.minX) / window.frame.width,
                dy: (frame.midY - window.frame.minY) / window.frame.height
            )).tap()
        }
        try waitForExistence(element("musicOptionsMenu"), timeout: 20,
                              named: "music catalog after Music Home Browse")
    }
    #endif

    func startMusicCollectionPlayback() throws {
        let play = element("musicPlayButton")
        try waitForExistence(play, timeout: 20, named: "loaded album play action")
        guard waitUntil(timeout: 20, condition: { play.isEnabled }) else {
            throw ProofFailure.timeout("Timed out waiting for the loaded album play action to become enabled.")
        }
        try activate(play)
        #if os(tvOS)
        let nowPlaying = element("nowPlayingBar")
        guard waitUntil(timeout: 30, condition: { self.hasVisibleFrame(nowPlaying) }) else {
            throw ProofFailure.timeout("Timed out waiting for the inline now-playing bar after starting the album.")
        }
        try activate(nowPlaying)
        #endif
    }

    /// iOS exposes music-library sections inside its options menu, while tvOS
    /// exposes the same destinations directly. Wait for the pushed library
    /// before continuing so a TV Menu press cannot leave it immediately.
    func openMusicLibrarySection(_ section: String) throws {
        #if os(iOS)
        try browseFromMusicHome()
        // iOS 26.5 can expose the visible toolbar menu without AX hit-testing.
        // Keep its one physical fallback localized to the real options control;
        // the destination link below still proves the public Queue route.
        let options = element("musicOptionsMenu")
        try waitForExistence(options, timeout: 20, named: "Music options")
        if options.isHittable {
            options.tap()
        } else {
            let frame = options.frame
            let window = try applicationWindow()
            window.coordinate(withNormalizedOffset: CGVector(
                dx: (frame.midX - window.frame.minX) / window.frame.width,
                dy: (frame.midY - window.frame.minY) / window.frame.height
            )).tap()
        }
        #endif
        try activate(element("musicLibrary-\(section)"))
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "\(section) music library")
        try waitForExistence(element("musicLibrarySectionPicker"), timeout: 10, named: "\(section) music library picker")
    }

    #if os(tvOS)
    /// Album selection stays on its collection screen. Unwind only this proof
    /// journey's audio path rather than assuming two fixed Menu presses.
    func unwindTVAudioToRoot() throws {
        for _ in 0..<4 {
            if hasVisibleFrame(element("audioScreen")) { return }
            XCUIRemote.shared.press(.menu)
            if waitUntil(timeout: 5, condition: { self.hasVisibleFrame(self.element("audioScreen")) }) {
                return
            }
        }
        throw ProofFailure.ui("TV Back did not return the music proof journey to the Audio catalog.")
    }
    #endif

    #if os(iOS)
    func journeyConnectionFormCentering() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        guard UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome else {
            throw XCTSkip("Duo retains its existing connection layout.")
        }
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-animations"])
        let form = element("connectionForm")
        try waitForExistence(form, timeout: 15, named: "centered connection form")
        try waitForExistence(element("connectionCameraStatus"), timeout: 15, named: "resolved QR camera state")
        let viewport = try applicationViewport()
        XCTAssertEqual(form.frame.midX, viewport.midX, accuracy: 2, "Connect must center horizontally in the full viewport.")
        XCTAssertEqual(form.frame.midY, viewport.midY, accuracy: 24, "Connect must center vertically within system safe areas.")
        XCTAssertTrue(hasVisibleFrame(element("advancedConnectionButton")))
        XCTAssertTrue(hasVisibleFrame(element("connectionHelpButton")))
        try attachFrame(named: "Connection form centered for this device")
        completed = true
    }
    #endif

    func journeyPlaybackResumeChoice(episode: Bool) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let itemID = episode ? fixture.episodeID : fixture.directItemID
        try await api.seedProgress(identity: identity, itemID: itemID, seconds: 24)
        #if os(iOS)
        XCUIDevice.shared.orientation = .portrait
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-animations"])
        #else
        try launchAndResetSession()
        #endif
        try signIn(using: fixture, waitsForAccount: false)
        #if os(iOS)
        // Keep the detail Play action clear of the known portrait tab-bar obstruction.
        if !episode { XCUIDevice.shared.orientation = .landscapeLeft }
        #endif

        func press(_ control: XCUIElement) throws {
            #if os(iOS)
            XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(control) && control.isEnabled })
            control.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            #else
            try activate(control)
            #endif
        }
        func closeRequestedPlayback() throws {
            #if os(iOS)
            // Start from a known hidden state: an existence probe can race the auto-hide timer.
            guard disappears(element("videoPlaybackChrome"), timeout: 8) else {
                throw ProofFailure.ui("Playing video must auto-hide its controls before the close gesture.")
            }
            let surface = element("videoControlsSurface")
            surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
            let close = element("closePlayerButton")
            try waitForExistence(close, timeout: 3, named: "revealed player close control")
            close.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            guard disappears(element("playbackScreen"), timeout: 10) else {
                throw ProofFailure.ui("The visible close control must dismiss requested playback.")
            }
            #else
            try closePlayer()
            #endif
        }
        let resume = element("resumeItem-\(itemID)")
        #if os(iOS)
        try revealDetailElement(resume, in: element("homeScreen"), named: "saved title on Home", requiresHittable: false)
        #else
        for _ in 0..<8 where !resume.exists { XCUIRemote.shared.press(.down) }
        #endif
        try press(resume)
        let action: XCUIElement
        if episode {
            try waitForExistence(element("videoCollectionScreen"), timeout: 25, named: "show detail")
            #if os(tvOS)
            action = element("videoCollectionItem-\(itemID)")
            #else
            action = element("videoEpisode-\(itemID)")
            try revealDetailElement(action, in: element("videoCollectionScreen"), named: "saved episode", requiresHittable: false)
            #endif
        } else {
            try waitForExistence(element("detailScreen"), timeout: 25, named: "movie detail")
            action = element("playButton")
        }
        let choice = element("resumePlaybackChoice")
        try press(action)
        try waitForExistence(choice, timeout: 15, named: "Resume / Start Over choice")
        XCTAssertFalse(element("playbackScreen").exists, "Saved playback must wait for a choice.")
        try attachFrame(named: episode ? "Episode Resume and Start Over choice" : "Movie Resume and Start Over choice")
        let startOver = choice.buttons.matching(NSPredicate(format: "label == %@", "Start Over")).firstMatch
        #if os(tvOS)
        guard focus(startOver, timeout: 20) else { throw ProofFailure.ui("The remote must focus Start Over before timing selection.") }
        #endif
        let restartSubmitted = ProcessInfo.processInfo.systemUptime
        #if os(tvOS)
        XCUIRemote.shared.press(.select)
        #else
        try press(startOver)
        #endif
        XCTAssertTrue(disappears(choice, timeout: 2), "Start Over must promptly leave the chooser for an already prepared original.")
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - restartSubmitted, 4,
                          "A slow playback-info service must not delay Start Over for a prepared original.")
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "restarted player")
        let restarted = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= 1 }
        XCTAssertLessThan(restarted, 10, "Start Over must play from zero, not the old resume offset.")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= restarted + 2 }
        let deadline = Date().addingTimeInterval(10)
        var saved = try await api.playbackSeconds(identity: identity, itemID: itemID)
        while saved >= 10, Date() < deadline {
            try await Task.sleep(for: .milliseconds(250))
            saved = try await api.playbackSeconds(identity: identity, itemID: itemID)
        }
        XCTAssertLessThan(saved, 10, "The server must receive the restarted timeline.")
        try closeRequestedPlayback()
        try await api.seedProgress(identity: identity, itemID: itemID, seconds: 24)
        try press(action)
        try waitForExistence(choice, timeout: 15, named: "choice before cancellation")
        try press(choice.buttons.matching(NSPredicate(format: "label == %@", "Cancel")).firstMatch)
        XCTAssertTrue(disappears(choice, timeout: 8))
        XCTAssertFalse(element("playbackScreen").exists)
        let untouched = try await api.playbackSeconds(identity: identity, itemID: itemID)
        XCTAssertEqual(untouched, 24, accuracy: 0.1, "Cancelling must preserve saved progress.")

        try press(action)
        try waitForExistence(choice, timeout: 15, named: "resume choice after cancellation")
        try press(choice.buttons.matching(NSPredicate(format: "label == %@", "Resume")).firstMatch)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "resumed player")
        let initial = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= 24 }
        XCTAssertLessThan(initial, 35, "Resume must use the saved position.")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= initial + 2 }
        try closeRequestedPlayback()

        try await api.seedProgress(identity: identity, itemID: itemID, seconds: 0)
        try press(action)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "unplayed title starts directly")
        XCTAssertFalse(choice.exists, "A fresh title must not need a resume choice.")
        try closeRequestedPlayback()
        completed = true
    }

    func journeyNativePlaybackReportsResumeAndConversionProgress() async throws {
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

        try launchAndResetSession()
        try signIn(using: fixture)

        try openLibraryItem(id: fixture.directItemID)
        let directPlaybackTime = try startPlayback(using: "playButton")
        let directInitial = try waitForPlaybackValue(directPlaybackTime, timeout: 10) { _ in true }
        let directAdvanced = try waitForPlaybackValue(directPlaybackTime, timeout: 25) {
            $0 >= directInitial + 7
        }
        XCTAssertGreaterThan(directAdvanced, directInitial + 6, "The direct player's accessibility value did not advance for seven seconds.")

        #if os(iOS)
        try await pauseAndResumeNativePlayer(after: directAdvanced)
        #endif

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

    func journeyOriginalMKVReportsPeriodicProgressAndResumes() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.convertedItemID)
        let playbackTime = try startPlayback(using: "playButton")
        // Verify periodic reporting while still playing, before pause/close can flush progress.
        let periodic = try await waitForServerProgress(
            api, identity: identity, itemID: fixture.convertedItemID, minimum: 4
        )
        XCTAssertGreaterThan(periodic, 4)
        #if os(tvOS)
        _ = try waitForPlaybackValue(playbackTime, timeout: 15) { $0 >= periodic + 2 }
        XCUIRemote.shared.press(.playPause)
        try await Task.sleep(for: .seconds(1))
        let paused = try waitForPlaybackValue(playbackTime, timeout: 5) { _ in true }
        _ = try await waitForServerProgress(
            api, identity: identity, itemID: fixture.convertedItemID,
            minimum: max(0, paused - 1), maximum: paused + 2
        )
        try await Task.sleep(for: .seconds(2))
        let stillPaused = try waitForPlaybackValue(playbackTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, paused + 1)
        #endif
        try attachPlaybackFrame(named: "Original MKV video frame")
        try closePlayer()
        let saved = try await waitForServerProgress(
            api, identity: identity, itemID: fixture.convertedItemID, minimum: 4
        )
        try returnToLibrary()
        try refreshLibrary(waitingForResumeOf: fixture.convertedItemID)
        try openLibraryItem(id: fixture.convertedItemID)
        let resumed = try startPlayback(using: "playButton")
        _ = try waitForPlaybackValue(resumed, timeout: 15) { $0 >= saved - 1 }
        _ = try waitForPlaybackValue(resumed, timeout: 15) { $0 >= saved + 4 }
        try closePlayer()
    }

    #if os(iOS)
    func journeyCompatibleHLSReplayRequestsFreshSession() async throws {
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
    }
    #endif

}
