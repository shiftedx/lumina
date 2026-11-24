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
    func journeyAlbumPlayStartsFromIdle(restoredQueueUnavailable: Bool = false, requiresCompatibleAudio: Bool = false) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        if restoredQueueUnavailable {
            let origin = try localOrigin(from: fixture.serverURL)
            let session = makeTestAPISession(origin: origin)
            defer { session.invalidateAndCancel() }
            let api = ProofAPI(origin: origin, session: session)
            let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
            try await api.replaceProofMusicSession(identity: identity,
                itemIDs: [fixture.audioTrackID, fixture.audioSecondTrackID], position: 0)
        }
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "album")
        #if os(tvOS)
        try attachFrame(named: "TV single-header Music library hero and focused album grid")
        #endif
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album tracks")
        #if os(tvOS)
        try waitForExistence(element("musicItem-\(fixture.audioTrackID)"), timeout: 25, named: "album tracks")
        if !restoredQueueUnavailable && !requiresCompatibleAudio {
            XCUIRemote.shared.press(.playPause)
            try waitForExistence(element("nowPlayingBar"), timeout: 30, named: "remote Play starts idle album")
            try activate(element("nowPlayingBar"))
            try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "remote-started album player")
            _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 3 }
            try stopAudioPlayer()
        }
        let play = application!.buttons.matching(NSPredicate(format: "label == %@", "Play album")).firstMatch
        #else
        let play = element("musicPlayButton")
        try waitForExistence(play, timeout: 20, named: "Play album")
        try revealInLibraryViewport([play], in: element("musicCollectionScreen"))
        #endif
        try waitForHittable(play, timeout: 20, named: "Play album")
        #if os(tvOS)
        XCTAssertGreaterThanOrEqual(play.frame.width, 300, "TV album actions need readable full-width labels.")
        XCTAssertGreaterThanOrEqual(play.frame.height, 64)
        try attachFrame(named: "TV album with full-width actions")
        #endif
        try activate(play)
        #if os(tvOS)
        try waitForExistence(element("nowPlayingBar"), timeout: 30, named: "on-screen Play starts idle album")
        try activate(element("nowPlayingBar"))
        #endif
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "album player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 30) { $0 >= 3 }
        #if os(tvOS)
        let window = application!.windows.firstMatch.frame
        let artwork = element("audioAlbumArtwork")
        try waitForExistence(artwork, timeout: 15, named: "TV full-screen album artwork")
        XCTAssertTrue(waitUntil(timeout: 10) { artwork.hasFocus },
                      "The artwork surface must initially own Siri Remote movement.")
        XCTAssertLessThanOrEqual(artwork.frame.minX, window.minX + 3)
        XCTAssertGreaterThanOrEqual(artwork.frame.maxX, window.maxX - 3)
        XCTAssertLessThanOrEqual(artwork.frame.minY, window.minY + 3)
        XCTAssertGreaterThanOrEqual(artwork.frame.maxY, window.maxY - 3,
                                    "Focused album artwork must cover the first TV viewport bezel to bezel without gaps.")
        let timeline = element("audioTimeline")
        try waitForExistence(timeline, timeout: 15, named: "TV bottom artwork timeline")
        XCTAssertGreaterThan(timeline.frame.minY, window.minY + window.height * 0.72)
        XCTAssertLessThanOrEqual(timeline.frame.maxY, window.maxY - 50)
        XCTAssertLessThanOrEqual(timeline.frame.minX, window.minX + 82)
        XCTAssertGreaterThanOrEqual(timeline.frame.maxX, window.maxX - 82,
                                    "TV progress must span the available artwork width inside safe margins.")
        XCTAssertFalse(hasVisibleFrame(element("nowPlayingTitle")),
                       "Track metadata must begin below the first artwork viewport.")
        let lyrics = element("audioLyrics")
        let listeningOptions = element("audioListeningOptions")
        try waitForExistence(lyrics, timeout: 10, named: "collapsed TV lyrics")
        try waitForExistence(listeningOptions, timeout: 10, named: "collapsed TV listening options")
        XCTAssertFalse(hasVisibleFrame(lyrics),
                       "Lyrics must begin below the first artwork viewport.")
        XCTAssertEqual(lyrics.value as? String, "Collapsed")
        XCTAssertEqual(listeningOptions.value as? String, "Collapsed")
        XCTAssertFalse(element("audioCurrentLyric").exists, "Lyrics must have one collapsed presentation.")
        XCTAssertFalse(element("audioPreviousButton").exists)
        XCTAssertFalse(element("audioNextButton").exists)
        XCTAssertFalse(element("audioSeekBackwardButton").exists)
        XCTAssertFalse(element("audioSeekForwardButton").exists)

        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioSecondTrackTitle },
                      "Right on focused artwork must advance to the next queued track.")
        XCUIRemote.shared.press(.left)
        XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioTrackTitle },
                      "Left on focused artwork must return to the previous queued track.")

        XCUIRemote.shared.press(.down)
        let queue = element("audioQueueButton")
        let transport = element("audioPlayPauseButton")
        let repeatButton = element("audioRepeatButton")
        let stop = element("audioStopButton")
        XCTAssertTrue(waitUntil(timeout: 10) { queue.hasFocus },
                      "Down from artwork must enter the listening controls at Queue.")
        for control in [queue, transport, repeatButton, stop] {
            try waitForExistence(control, timeout: 10, named: "TV listening control \(control.identifier)")
            XCTAssertTrue(hasVisibleFrame(control), "The persistent TV listening rail must stay visible.")
        }
        XCTAssertEqual(queue.frame.midX, transport.frame.midX, accuracy: 4)
        XCTAssertEqual(transport.frame.midX, repeatButton.frame.midX, accuracy: 4)
        XCTAssertEqual(repeatButton.frame.midX, stop.frame.midX, accuracy: 4)
        XCTAssertLessThan(queue.frame.midY, transport.frame.midY)
        XCTAssertLessThan(transport.frame.midY, repeatButton.frame.midY)
        XCTAssertLessThan(repeatButton.frame.midY, stop.frame.midY,
                          "TV controls must read Queue, Play/Pause, Repeat, Stop.")
        try activate(queue)
        try waitForExistence(element("audioQueueScreen"), timeout: 15, named: "TV play queue")
        XCUIRemote.shared.press(.menu)
        guard disappears(element("audioQueueScreen"), timeout: 10),
              hasVisibleFrame(element("playbackScreen")),
              waitUntil(timeout: 10, condition: { queue.hasFocus }) else {
            throw ProofFailure.ui("Back from the TV queue must keep the player open and restore Queue focus.")
        }
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 5) { artwork.hasFocus },
                      "Up from Queue must return to the artwork-owned listening surface.")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { queue.hasFocus })
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { transport.hasFocus })
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle,
                       "Directional focus inside controls must not skip the queue.")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { repeatButton.hasFocus })
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { stop.hasFocus })
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { timeline.hasFocus },
                      "Remote Down from Stop must reach the full-width track timeline.")

        XCUIRemote.shared.press(.playPause)
        XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Play" },
                      "The remote must pause before validating timeline seeking.")
        let pausedBeforeSeek = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCUIRemote.shared.press(.right)
        let sought = try waitForPlaybackValue(element("playbackTime"), timeout: 8) { $0 >= pausedBeforeSeek + 14 }
        try await Task.sleep(for: .seconds(2))
        let stillPausedAfterSeek = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPausedAfterSeek, sought + 0.5,
                                 "Seeking on the focused timeline must not resume paused audio.")
        XCUIRemote.shared.press(.playPause)
        XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Pause" })

        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 5) { artwork.hasFocus },
                      "Remote Up from the timeline must return to the artwork surface.")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { queue.hasFocus })
        for control in [transport, repeatButton, stop, timeline] {
            XCUIRemote.shared.press(.down)
            XCTAssertTrue(waitUntil(timeout: 5) { control.hasFocus })
        }
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 8) { lyrics.hasFocus },
                      "Remote Down from the timeline must reach Lyrics without a focus trap.")
        for control in [queue, transport, repeatButton, stop] {
            XCTAssertTrue(hasVisibleFrame(control), "The TV listening rail must persist while options scroll.")
        }
        try attachFrame(named: "TV artwork-first player with ordered listening controls")
        #endif
        #if os(iOS)
        let transport = element("audioPlayPauseButton")
        let queue = element("audioQueueButton")
        let window = application!.windows.firstMatch.frame
        let timeline = element("audioTimeline")
        let artwork = element("audioAlbumArtwork")
        try waitForExistence(artwork, timeout: 15, named: "Full-screen album artwork")
        let usesWideAudioLayout = !UIApplication.shared.preferredContentSizeCategory.isAccessibilityCategory
            && window.width >= 640 && window.height >= 560
        if usesWideAudioLayout {
            try waitForExistence(timeline, timeout: 15, named: "wide album timeline")
            XCTAssertEqual(artwork.frame.width, artwork.frame.height,
                           accuracy: max(6, artwork.frame.width * 0.03),
                           "Wide album artwork must remain square.")
            for control in [queue, transport, timeline, element("audioPreviousButton"), element("audioNextButton")] {
                try waitForExistence(control, timeout: 15, named: "wide player \(control.identifier)")
                XCTAssertTrue(hasVisibleFrame(control), "Wide player must retain \(control.identifier) on screen.")
            }
            try await Task.sleep(for: .seconds(1.2))
            for control in [timeline, transport, queue, element("audioPreviousButton"), element("audioNextButton")] {
                XCTAssertTrue(hasVisibleFrame(control), "Wide player controls must persist after the compact timeline timeout.")
            }
            let slider = application!.sliders["Track position"]
            try waitForHittable(slider, timeout: 15, named: "wide track position")
            slider.adjust(toNormalizedSliderPosition: 0.6)
            _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= 45 }
            try activate(element("audioNextButton"))
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioSecondTrackTitle },
                          "Wide Next must advance the album queue.")
            try activate(element("audioPreviousButton"))
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioTrackTitle },
                          "Wide Previous must return to the prior album track.")
            try attachFrame(named: "Wide player keeps square artwork and persistent transport")
        } else if usesStandardPhonePushedChrome {
            XCTAssertEqual(artwork.frame.width, artwork.frame.height, accuracy: 2,
                           "Phone artwork must show the whole square cover.")
            XCTAssertGreaterThanOrEqual(artwork.frame.minX, window.minX + 20)
            XCTAssertLessThanOrEqual(artwork.frame.maxX, window.maxX - 20)
            let title = element("nowPlayingTitle")
            let previous = element("audioPreviousButton")
            let next = element("audioNextButton")
            let close = element("closePlayerButton")
            let airPlay = element("audioAirPlayButton")
            let cast = element("musicChromecastButton")
            let quality = element("audioQualityButton")
            XCTAssertGreaterThanOrEqual(close.frame.minY, window.minY + 44,
                                       "The phone player header must clear the status area.")
            for control in [title, timeline, transport, previous, next, queue, close, airPlay, cast, quality] {
                try waitForExistence(control, timeout: 15, named: "phone player \(control.identifier)")
                XCTAssertTrue(hasVisibleFrame(control), "Phone player must show \(control.identifier) without scrolling.")
                XCTAssertGreaterThanOrEqual(control.frame.minY, window.minY)
                XCTAssertLessThanOrEqual(control.frame.maxY, window.maxY - 20,
                                         "Phone controls must clear the home indicator.")
            }
            XCTAssertGreaterThanOrEqual(title.frame.minY, artwork.frame.maxY)
            XCTAssertGreaterThanOrEqual(transport.frame.minY, timeline.frame.maxY)
            XCTAssertGreaterThanOrEqual(queue.frame.minY, transport.frame.maxY + 28,
                                       "Listening utilities belong below transport with clear separation.")
            XCTAssertGreaterThan(queue.frame.midY, window.minY + window.height * 0.84,
                                 "The utility row should use the bottom of the phone viewport.")
            XCTAssertTrue(waitUntil(timeout: 10) { (quality.value as? String)?.contains("kbps") == true },
                          "Playback quality must come from the active native stream.")
            try activate(quality)
            try waitForExistence(element("audioPlaybackFormat"), timeout: 10, named: "active playback format")
            XCTAssertTrue(element("audioSourceQuality").exists)
            try activate(menuAction("Done"))
            try await Task.sleep(for: .seconds(1.2))
            XCTAssertTrue(hasVisibleFrame(timeline), "Phone track progress must remain visible while idle.")
            try activate(transport)
            XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Play" })
            let slider = application!.sliders["Track position"]
            try waitForHittable(slider, timeout: 10, named: "phone track position")
            slider.adjust(toNormalizedSliderPosition: 0.6)
            _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= 45 }
            try activate(transport)
            XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Pause" })
            try activate(next)
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioSecondTrackTitle })
            try activate(previous)
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioTrackTitle })
            try activate(queue)
            try waitForExistence(element("audioQueueScreen"), timeout: 10, named: "phone play queue")
            try activate(element("audioQueueDoneButton"))
            XCTAssertTrue(disappears(element("audioQueueScreen"), timeout: 10))
            artwork.swipeLeft()
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioSecondTrackTitle })
            artwork.swipeRight()
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioTrackTitle })
            let beforeClose = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
            try closePlayer()
            try waitForHittable(element("nowPlayingBar"), timeout: 15, named: "current track after closing player")
            try activate(element("nowPlayingBar"))
            try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "reopened phone player")
            XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
            _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= beforeClose }
            XCTAssertTrue(hasVisibleFrame(timeline), "Reopening the player must retain visible progress.")
            try attachFrame(named: "Phone player with whole cover and visible track details and transport")
        } else {
            let back = application!.buttons.matching(identifier: "BackButton").firstMatch
            XCTAssertFalse(element("closePlayerButton").exists, "Music must not invent a second back control.")
            XCTAssertTrue(queue.exists)
            XCTAssertTrue(element("audioStopButton").exists)
            try waitForHittable(transport, timeout: 15, named: "Navigation Play/Pause")
            XCTAssertFalse(element("audioPreviousButton").exists)
            XCTAssertFalse(element("audioNextButton").exists)
            XCTAssertTrue(back.exists, "Music must use the app's standard navigation back button.")
            if queue.frame.minY > back.frame.maxY {
                XCTAssertLessThan(queue.frame.midY, transport.frame.midY,
                                  "Queue must be the first action in the navigation rail.")
                XCTAssertEqual(transport.frame.midX, queue.frame.midX, accuracy: 3,
                               "Play/Pause must share the navigation rail with Queue.")
            } else {
                XCTAssertEqual(transport.frame.midY, queue.frame.midY, accuracy: 3,
                               "Play/Pause must share the navigation bar with Queue.")
            }
            XCTAssertTrue(waitUntil(timeout: 3) { !timeline.exists }, "Track progress must fade while artwork is idle.")
            artwork.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
            XCTAssertTrue(timeline.exists, "Tapping artwork must reveal track progress immediately.")
            let slider = application!.sliders["Track position"]
            XCTAssertTrue(slider.exists, "The revealed timeline must support seeking.")
            let sliderFrame = slider.frame
            let timelineFrame = timeline.frame
            XCTAssertGreaterThan(timelineFrame.minY, window.minY + window.height * 0.7,
                                 "Only the track timeline should occupy the bottom of the artwork stage.")
            XCTAssertLessThanOrEqual(timelineFrame.maxY, window.maxY)
            XCTAssertLessThanOrEqual(timelineFrame.minX, window.minX + 26)
            XCTAssertGreaterThanOrEqual(timelineFrame.maxX, window.maxX - 26,
                                        "Track progress must span the normal navigation area.")
            XCTAssertTrue(waitUntil(timeout: 3) { !timeline.exists }, "Revealed progress must fade again after inactivity.")

            try activate(transport)
            XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Play" }, "Play/Pause must pause before the seek gesture.")
            let pausedStart = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
            try await Task.sleep(for: .seconds(1))
            let pausedSeconds = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
            XCTAssertLessThanOrEqual(pausedSeconds, pausedStart + 0.5, "Pausing must freeze the seek thumb before dragging.")
            let seekStartFraction = pausedSeconds / fixture.audioTrackDurationSeconds
            guard (0...1).contains(seekStartFraction), seekStartFraction < 0.6 else {
                throw ProofFailure.ui("The synthetic audio timeline was not at a seekable live position.")
            }
            let seekStart = application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(
                dx: (sliderFrame.minX + sliderFrame.width * seekStartFraction - window.minX) / window.width,
                dy: (sliderFrame.midY - window.minY) / window.height
            ))
            let seekEnd = application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(
                dx: (sliderFrame.minX + sliderFrame.width * 0.6 - window.minX) / window.width,
                dy: (sliderFrame.midY - window.minY) / window.height
            ))
            // No accessibility query may sit between this reveal and the physical drag:
            // the public timeline intentionally auto-hides after one second.
            artwork.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
            seekStart.press(forDuration: 0.1, thenDragTo: seekEnd)
            try activate(transport)
            XCTAssertTrue(waitUntil(timeout: 8) { transport.label == "Pause" }, "Play/Pause must resume after seeking.")
            _ = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { $0 >= 45 }
            XCTAssertTrue(waitUntil(timeout: 3) { !timeline.exists }, "Progress must fade after seeking ends.")
            try waitForHittable(element("audioRepeatButton"), timeout: 15, named: "Repeat in navigation")
            XCTAssertGreaterThanOrEqual(artwork.frame.height, window.height * 0.85)
            XCTAssertLessThanOrEqual(artwork.frame.minY, window.minY + 2)
            XCTAssertFalse(element("audioCurrentLyric").exists, "Lyrics must have one expandable presentation.")
            XCTAssertFalse(hasVisibleFrame(element("nowPlayingTitle")),
                           "Track details should begin below the initial artwork screen.")
            XCTAssertFalse(hasVisibleFrame(element("audioListeningOptions")),
                           "Listening sections should begin below the initial artwork screen.")
            try attachFrame(named: "Artwork-first player with navigation transport and bottom timeline")
            let swipeStart = application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.4))
            let swipeEnd = application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.4))
            swipeStart.press(forDuration: 0.1, thenDragTo: swipeEnd)
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioSecondTrackTitle },
                          "Swiping left on artwork must advance to the next queued track.")
            swipeEnd.press(forDuration: 0.1, thenDragTo: swipeStart)
            XCTAssertTrue(waitUntil(timeout: 20) { self.currentAudioTitle() == fixture.audioTrackTitle },
                          "Swiping right on artwork must return to the previous queued track.")
        }

        #endif
        if requiresCompatibleAudio {
            try waitForExistence(element("audioPlaybackMethod"), timeout: 15, named: "compatible audio playback method")
            XCTAssertEqual(element("audioPlaybackMethod").label, "Compatible audio")
            XCTAssertFalse(element("replayButton").exists, "A rejected source must not be reported as completed.")
            try revealMusicControl(element("audioLyrics"))
            #if os(iOS)
            let lyricsInitiallyCollapsed = !usesWideAudioLayout
            #else
            let lyricsInitiallyCollapsed = true
            #endif
            if lyricsInitiallyCollapsed {
                XCTAssertEqual(element("audioLyrics").value as? String, "Collapsed")
                try activate(element("audioLyrics"))
            }
            let lyric = try XCTUnwrap(fixture.audioLyric)
            try waitForExistence(menuAction(lyric), timeout: 20, named: "Lyrics opened on demand")
        }
        try attachFrame(named: "Audio player with expandable lyrics")
        try stopAudioPlayer()
        completed = true
    }

    #if os(iOS)
    func journeyLockScreenMusicArtworkPersists() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectTourAudioRoot()
        try activate(revealMusicAlbum(fixture, named: "synthetic album"))
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album tracks")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "music player")
        let artwork = element("nowPlayingArtwork")
        try waitForExistence(artwork, timeout: 10, named: "system artwork metadata")
        guard waitUntil(timeout: 15, condition: { artwork.value as? String == fixture.audioTrackTitle }) else {
            throw ProofFailure.ui("System Now Playing did not receive renderable album artwork for the current track.")
        }
        let time = element("playbackTime")
        let initial = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= initial + 3 }
        XCTAssertEqual(artwork.value as? String, fixture.audioTrackTitle, "Progress updates must retain lock-screen artwork.")
        for _ in 0..<3 {
            let beforeBackground = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            try backgroundApplication()
            try await Task.sleep(for: .seconds(3))
            application!.activate()
            let afterBackground = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCTAssertGreaterThanOrEqual(afterBackground, beforeBackground + 2, "Music must continue while other apps are in the foreground.")
            XCTAssertEqual(artwork.value as? String, fixture.audioTrackTitle, "Background playback must retain audio metadata and artwork for system surfaces.")
        }
        try activate(element("audioPlayPauseButton"))
        XCTAssertEqual(artwork.value as? String, fixture.audioTrackTitle, "Pausing must retain lock-screen artwork.")
        let albumArt = element("audioAlbumArtwork")
        albumArt.swipeLeft()
        guard waitUntil(timeout: 20, condition: { artwork.value as? String == fixture.audioSecondTrackTitle }) else {
            throw ProofFailure.ui("The next track did not publish its lock-screen artwork and title.")
        }
        try attachFrame(named: "System artwork follows the current audio track")
        try stopAudioPlayer()
        XCTAssertTrue(disappears(element("playbackScreen"), timeout: 15))
        completed = true
    }

    func journeyCompactMusicToolbarReopensAndResumes() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectTourAudioRoot()
        try activate(revealMusicAlbum(fixture, named: "synthetic album"))
        let albumScreen = element("musicCollectionScreen")
        try waitForExistence(albumScreen, timeout: 25, named: "album tracks")
        let track = element("musicItem-\(fixture.audioTrackID)")
        for _ in 0..<4 where !track.exists { albumScreen.swipeUp() }
        try revealInLibraryViewport([track], in: albumScreen)
        try activate(track)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "direct track player")
        let time = element("playbackTime")
        try waitForExistence(time, timeout: 20, named: "audio playback time")
        let beforeBrowse = try waitForPlaybackValue(time, timeout: 15) { $0 >= 2 }
        try closePlayer()
        let viewport = try applicationViewport()
        let usesDuo = UIDevice.current.userInterfaceIdiom == .phone && min(viewport.width, viewport.height) >= 450
        let usesLandscapeHomeHeader = usesDuo && viewport.height >= 600 && viewport.width > viewport.height
        if ProcessInfo.processInfo.environment["LUMINA_EXPECT_DUO_LANDSCAPE_HOME"] == "1" {
            XCTAssertTrue(usesLandscapeHomeHeader, "Expected the open Duo landscape viewport, got \(viewport).")
        }
        if !usesDuo || usesLandscapeHomeHeader { try selectDestination("Home", screen: "homeScreen") }
        let artwork = element("nowPlayingBar")
        let toggle = element("nowPlayingPauseButton")
        let search = element("homeSearchButton")
        try waitForExistence(artwork, timeout: 15, named: "compact music artwork")
        try waitForExistence(toggle, timeout: 15, named: "compact music play/pause")
        XCTAssertEqual(application!.buttons.matching(identifier: "nowPlayingBar").count, 1,
                       "Browsing must expose one current-track destination.")
        XCTAssertEqual(application!.buttons.matching(identifier: "nowPlayingPauseButton").count, 1,
                       "Browsing must expose one compact playback toggle.")
        XCTAssertTrue(artwork.label.contains(fixture.audioTrackTitle))
        XCTAssertLessThanOrEqual(artwork.frame.width, 70, "Music controls must not form a full-width footer.")
        XCTAssertLessThanOrEqual(artwork.frame.height, 70)
        if usesLandscapeHomeHeader {
            let browse = element("homeBrowseMenu")
            for control in [search, browse, artwork, toggle] {
                try waitForHittable(control, timeout: 15, named: control.identifier)
                XCTAssertTrue(viewport.contains(control.frame))
                XCTAssertLessThan(control.frame.midX, viewport.midX,
                                  "Home controls must clear the trailing system chrome.")
                XCTAssertEqual(control.frame.midY, search.frame.midY, accuracy: 4,
                               "Home actions and Now Playing must share the masthead row.")
            }
            try activate(browse)
            try waitForExistence(element("homeMenu-watchlist"), timeout: 10, named: "Home Browse Watchlist")
            try activate(element("homeMenu-home"))
        } else if usesDuo && viewport.width > viewport.height {
            guard toggle.frame.minY >= artwork.frame.maxY - 1,
                  abs(toggle.frame.midX - artwork.frame.midX) <= 4,
                  artwork.isHittable, toggle.isHittable else {
                throw ProofFailure.ui("Duo Now Playing must expose separate, centered artwork and play/pause targets: artwork=\(artwork.frame), toggle=\(toggle.frame).")
            }
            if search.exists {
                XCTAssertGreaterThanOrEqual(artwork.frame.minY, search.frame.maxY - 1, "Artwork belongs below Search on the Duo rail.")
                XCTAssertEqual(artwork.frame.midX, search.frame.midX, accuracy: 4)
            }
        }
        try attachFrame(named: "Compact music artwork and separate play pause control")
        try activate(toggle)
        XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Play" })
        XCTAssertTrue(hasVisibleFrame(artwork), "Paused music must remain accessible.")
        try activate(toggle)
        XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Pause" })

        // The shared standard-phone pushed header must keep the existing
        // Now Playing destination and pause/resume control usable while a
        // non-audio title is open.
        if !usesDuo {
            let browse = element("compactBrowseMenu")
            try waitForHittable(browse, timeout: 15, named: "Home Browse before pushed movie")
            try activate(browse)
            try activate(element("compactCategory-movies"))
            try waitForExistence(element("libraryScreen"), timeout: 20, named: "Movies library root while music is active")
            let movie = try revealLibraryItem(id: fixture.directItemID, named: "movie while music is active")
            try activate(movie)
            let movieDetail = element("detailScreen")
            try assertStandardPhonePushedChrome(movieDetail, named: "movie detail with current audio")
            try waitForHittable(artwork, timeout: 15, named: "Now Playing on pushed movie detail")
            try waitForHittable(toggle, timeout: 15, named: "Now Playing pause on pushed movie detail")
            XCTAssertTrue(artwork.label.contains(fixture.audioTrackTitle),
                          "A pushed movie must retain the active track destination.")
            try activate(toggle)
            XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Play" },
                          "The compact action must pause the active track from a pushed movie.")
            try activate(toggle)
            XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Pause" },
                          "The compact action must resume the same track from a pushed movie.")
            try attachFrame(named: "Standard iPhone movie retains Now Playing")
            try activate(artwork)
            try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "full player from pushed movie")
            XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle,
                           "Opening Now Playing from a pushed movie must not restart browsing or playback.")
            XCTAssertGreaterThanOrEqual(try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }, beforeBrowse)
            try closePlayer()
            try waitForExistence(movieDetail, timeout: 15, named: "movie after closing full player")
            try performStandardPhoneLeadingEdgeBack(from: movieDetail, to: element("libraryScreen"),
                                                     named: "movie detail with current audio")
            try selectDestination("Home", screen: "homeScreen")
        }

        try activate(toggle)
        try activate(artwork)
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "reopened full music player")
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        XCTAssertGreaterThanOrEqual(try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }, beforeBrowse)
        XCTAssertEqual(element("audioPlayPauseButton").label, "Play")
        XCTAssertFalse(hasVisibleFrame(element("nowPlayingBar")), "Compact browsing controls must not crowd the full player.")
        try attachFrame(named: "Artwork reopens the paused full music player")
        try stopAudioPlayer()
        XCTAssertTrue(disappears(element("playbackScreen"), timeout: 15))
        XCTAssertFalse(hasVisibleFrame(element("nowPlayingBar")))
        if usesLandscapeHomeHeader {
            try activate(search)
            try waitForExistence(element("homeSearchScreen"), timeout: 15, named: "Duo landscape Home search")
        }
        completed = true
    }
    #endif

    /// UI readiness proxies for the Music Rework baseline. These timings begin
    /// at a user action and finish when the next actionable UI state is visible
    /// or its playback clock advances. They intentionally do not claim output
    /// route or acoustic latency.
    func journeyMusicBaselineTiming() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)

        let browseStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "music_baseline_browse", event: "begin", started: browseStarted)
        try selectTourAudioRoot()
        _ = try revealMusicAlbum(fixture, named: "baseline music album")
        emitPerformanceMarker(phase: "music_baseline_browse", event: "ready", started: browseStarted)

        try activate(element("audioItem-\(fixture.audioAlbumID)"))
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "baseline album tracks")
        let firstTrack = element("musicItem-\(fixture.audioTrackID)")
        try waitForExistence(firstTrack, timeout: 20, named: "baseline first album track")
        #if os(iOS)
        let app = try XCTUnwrap(application)
        let albumPlay = app.buttons.matching(identifier: "musicPlayButton").firstMatch
        try waitForExistence(albumPlay, timeout: 20, named: "baseline album play action")
        guard waitUntil(timeout: 20, condition: { albumPlay.isEnabled }) else {
            throw ProofFailure.timeout("Timed out waiting for the baseline album play action to become enabled.")
        }
        #endif
        let firstAudioStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "music_baseline_first_audio", event: "begin", started: firstAudioStarted)
        #if os(iOS)
        try activateTimingButton(albumPlay)
        #else
        try activate(firstTrack)
        #endif
        #if os(tvOS)
        let nowPlaying = element("nowPlayingBar")
        try waitForExistence(nowPlaying, timeout: 30, named: "baseline inline now playing")
        try activate(nowPlaying)
        #endif
        let clock = element("playbackTime")
        try waitForExistence(clock, timeout: 20, named: "baseline playback clock")
        _ = try waitForPlaybackValue(clock, timeout: 20) { $0 >= 1 }
        emitPerformanceMarker(phase: "music_baseline_first_audio", event: "ready", started: firstAudioStarted)

        #if os(tvOS)
        // The full-player control is intentionally not exposed on tvOS. Return
        // to the inline bar, whose Next action is the TV queue-transition
        // contract, before starting the timed intent interval.
        XCUIRemote.shared.press(.menu)
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(nowPlaying) && !self.hasVisibleFrame(self.element("playbackScreen"))
        }) else {
            throw ProofFailure.ui("Timed out returning to the TV inline now-playing bar.")
        }
        #endif

        let nextStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "music_baseline_manual_next", event: "begin", started: nextStarted)
        #if os(tvOS)
        let next = element("nowPlayingNextButton")
        try revealMusicControl(next)
        try activate(next)
        #else
        let next = application!.buttons.matching(identifier: "audioNextButton").firstMatch
        try activateTimingButton(next)
        #endif
        #if os(tvOS)
        guard waitUntil(timeout: 20, condition: {
            nowPlaying.label.contains(fixture.audioSecondTrackTitle)
                && (self.element("musicItem-\(fixture.audioSecondTrackID)").value as? String) == "Playing"
        }) else {
            throw ProofFailure.timeout("Timed out waiting for the TV inline successor after manual Next.")
        }
        #else
        guard waitUntil(timeout: 20, condition: { self.currentAudioTitle() == fixture.audioSecondTrackTitle }) else {
            throw ProofFailure.timeout("Timed out waiting for the successor track after manual Next.")
        }
        _ = try waitForPlaybackValue(clock, timeout: 10) { $0 >= 1 }
        #endif
        emitPerformanceMarker(phase: "music_baseline_manual_next", event: "ready", started: nextStarted)

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        #else
        try stopAudioPlayer()
        #endif
        completed = true
    }

    #if os(iOS)
    func journeyMusicHomeReadyTiming() throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)

        let started = ContinuousClock.now
        emitPerformanceMarker(phase: "music_home_ready", event: "begin", started: started)
        try selectTourAudioRoot()
        let resume = application!.buttons.matching(identifier: "musicHomeResumeButton").firstMatch
        guard waitUntil(timeout: 20, condition: { self.hasVisibleFrame(resume) && resume.isEnabled }) else {
            throw ProofFailure.ui("The Music Home checkpoint did not become actionable.")
        }
        emitPerformanceMarker(phase: "music_home_ready", event: "ready", started: started)
        XCTAssertFalse(element("audioPlayerStage").exists, "Music Home must remain paused before explicit Resume.")
    }

    private func activateTimingButton(_ button: XCUIElement) throws {
        guard waitUntil(timeout: 15, condition: { self.hasVisibleFrame(button) && button.isEnabled }) else {
            throw ProofFailure.ui("The timing control is not visible and enabled.")
        }
        if button.isHittable { button.tap() }
        else { button.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap() }
    }
    #endif

    func journeyAudioAlbumTrackPlaybackReportsProgress() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.audioTrackID, seconds: 0)
        try await api.seedProgress(identity: identity, itemID: fixture.audioSecondTrackID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        #if os(iOS)
        if UIDevice.current.userInterfaceIdiom == .pad {
            XCUIDevice.shared.orientation = .landscapeLeft
            XCTAssertTrue(waitUntil(timeout: 10) {
                let frame = self.application!.windows.firstMatch.frame
                return frame.width > frame.height && frame.width >= 640 && frame.height >= 560
            }, "The iPad audio proof must use its landscape wide-player viewport.")
        }
        #endif
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "imported synthetic album")
        XCTAssertTrue(album.label.contains(fixture.audioAlbumTitle))
        try attachFrame(named: "Audio albums with synthetic artwork")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album tracks")
        let track = element("musicItem-\(fixture.audioSecondTrackID)")
        #if os(iOS)
        let albumScreen = element("musicCollectionScreen")
        for _ in 0..<4 where !track.exists { albumScreen.swipeUp() }
        try revealInLibraryViewport([track], in: albumScreen)
        #endif
        try waitForExistence(track, timeout: 20, named: "second synthetic album track")
        XCTAssertTrue(track.label.contains(fixture.audioSecondTrackTitle))
        try attachFrame(named: "Album track list")
        try activate(track)
        #if os(tvOS)
        let nowPlaying = element("nowPlayingBar")
        try waitForExistence(nowPlaying, timeout: 30, named: "inline now-playing bar after selecting an album track")
        XCTAssertFalse(hasVisibleFrame(element("playbackScreen")), "Selecting a TV album track must keep the album list visible.")
        try activate(nowPlaying)
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "expanded audio player")
        let time = element("playbackTime")
        try waitForExistence(time, timeout: 20, named: "audio playback time")
        #else
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "direct audio player")
        let time = element("playbackTime")
        try waitForExistence(time, timeout: 20, named: "audio playback time")
        #endif
        XCTAssertEqual(currentAudioTitle(), fixture.audioSecondTrackTitle,
                       "Selecting a non-first album track must begin playback at that track.")
        var selectedSecondTrackPublished = false
        let selectionDeadline = Date().addingTimeInterval(20)
        while Date() < selectionDeadline {
            if let saved = try await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let queue = saved["queue"] as? [[String: Any]] {
                let ids = queue.compactMap { $0["id"] as? String }.map {
                    $0.replacingOccurrences(of: "-", with: "").lowercased()
                }
                let expected = [fixture.audioTrackID, fixture.audioSecondTrackID].map {
                    $0.replacingOccurrences(of: "-", with: "").lowercased()
                }
                if ids == expected, saved["current_index"] as? Int == 1 {
                    selectedSecondTrackPublished = true
                    break
                }
            }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertTrue(selectedSecondTrackPublished,
                      "Direct album-track playback must preserve the full queue and publish the selected second index.")
        let initial = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 5 }
        #if os(iOS)
        let viewport = try applicationViewport()
        let usesWideAudioLayout = !UIApplication.shared.preferredContentSizeCategory.isAccessibilityCategory
            && viewport.width >= 640 && viewport.height >= 560
        if usesWideAudioLayout {
            let artwork = element("audioAlbumArtwork")
            let title = element("nowPlayingTitle")
            let timeline = element("audioTimeline")
            let transport = element("audioPlayPauseButton")
            let queue = element("audioQueueButton")
            let lyrics = element("audioLyrics")
            try waitForExistence(artwork, timeout: 15, named: "wide album artwork")
            XCTAssertEqual(artwork.frame.width, artwork.frame.height,
                           accuracy: max(6, artwork.frame.width * 0.03),
                           "Wide album artwork must remain square.")
            for control in [artwork, title, timeline, transport] {
                try waitForExistence(control, timeout: 15, named: "wide primary \(control.identifier)")
                XCTAssertTrue(hasVisibleFrame(control), "Wide primary content must remain on screen: \(control.identifier).")
            }

            let fixtureLyric = try XCTUnwrap(fixture.audioLyric)
            let lyricCue = menuAction(fixtureLyric)
            try waitForExistence(lyricCue, timeout: 20, named: "prominent fixture lyric")
            XCTAssertTrue(hasVisibleFrame(lyricCue))
            try attachFrame(named: "Wide album artwork aligns with the first lyric")
            let lyricScreenshot = try applicationWindow().screenshot().image
            let lyricFormat = UIGraphicsImageRendererFormat()
            lyricFormat.scale = 1
            let lyricImage = try XCTUnwrap(UIGraphicsImageRenderer(size: viewport.size, format: lyricFormat).image { _ in
                lyricScreenshot.draw(in: CGRect(origin: .zero, size: viewport.size))
            }.cgImage)
            let recognition = VNRecognizeTextRequest()
            recognition.recognitionLevel = .accurate
            try VNImageRequestHandler(cgImage: lyricImage, orientation: .up).perform([recognition])
            let firstLine = try XCTUnwrap(recognition.results?.first { observation in
                guard let text = observation.topCandidates(1).first?.string else { return false }
                return fixtureLyric.hasPrefix(text)
            }, "The first lyric must be readable beside the artwork.")
            let lyricTop = viewport.minY + (1 - firstLine.boundingBox.maxY) * viewport.height
            XCTAssertEqual(artwork.frame.minY, lyricTop, accuracy: 8,
                           "Wide artwork and the first lyric must share a top edge while the transport stays visible.")
            for output in [element("audioAirPlayButton"), element("musicChromecastButton")] {
                try waitForExistence(output, timeout: 5, named: "wide audio output \(output.identifier)")
                XCTAssertTrue(hasVisibleFrame(output))
                for companion in [lyrics, queue] {
                    XCTAssertEqual(output.frame.midY, companion.frame.midY, accuracy: 2,
                                   "Wide output and companion controls must share the bottom row.")
                }
            }
            let primaryRight = [artwork.frame.maxX, title.frame.maxX, timeline.frame.maxX, transport.frame.maxX].max()!
            XCTAssertGreaterThan(lyricCue.frame.minX, primaryRight,
                                 "The prominent fixture lyric must occupy the right companion pane beside the primary controls.")

            let beforePaneSwitch = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            try waitForHittable(queue, timeout: 15, named: "wide Queue")
            try activate(queue)
            let queueScreen = element("audioQueueScreen")
            try waitForExistence(queueScreen, timeout: 15, named: "inline wide audio queue")
            XCTAssertTrue(hasVisibleFrame(queueScreen))
            XCTAssertGreaterThan(queueScreen.frame.midX, primaryRight,
                                 "Wide Queue must replace the right companion content inline.")
            XCTAssertFalse(hasVisibleFrame(element("audioOptionsSheet")),
                           "Wide Queue must not present listening options over the player.")

            try waitForHittable(lyrics, timeout: 15, named: "wide Lyrics return")
            try activate(lyrics)
            try waitForExistence(lyricCue, timeout: 15, named: "fixture lyric after returning from Queue")
            XCTAssertGreaterThan(lyricCue.frame.minX, primaryRight,
                                 "Lyrics must return to the right companion pane after closing the inline queue.")
            let afterPaneSwitch = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCTAssertGreaterThanOrEqual(afterPaneSwitch, beforePaneSwitch,
                                        "Changing wide companion content must not rewind playback.")
            try attachFrame(named: "Wide audio player keeps playback beside inline Lyrics and Queue")
        }
        #endif
        #if os(tvOS)
        let beforePause = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCUIRemote.shared.press(.playPause)
        try await Task.sleep(for: .seconds(3))
        let paused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(paused, beforePause + 2, "The Siri Remote did not pause audio playback.")
        try await Task.sleep(for: .seconds(2))
        let stillPaused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, paused + 1, "Audio advanced while paused by the Siri Remote.")
        XCUIRemote.shared.press(.playPause)
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= stillPaused + 2 }
        #endif
        #if os(iOS)
        let beforeBackground = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        try backgroundApplication()
        try await Task.sleep(for: .seconds(3))
        application!.activate()
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "audio player after returning from Home")
        let afterBackground = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(afterBackground, beforeBackground + 2, "Audio stopped advancing while the app was in the background.")
        #endif
        try attachFrame(named: "Native audio playback")
        let beforeBrowse = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        try closePlayer()
        #if os(tvOS)
        try waitForExistence(element("musicCollectionScreen"), timeout: 15, named: "album after browsing from audio")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "persistent now-playing bar")
        XCTAssertTrue(element("nowPlayingBar").label.contains(fixture.audioSecondTrackTitle))
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioSecondTrackID, minimum: 3)
        try unwindTVAudioToRoot()
        #else
        try waitForExistence(element("musicCollectionScreen"), timeout: 15, named: "album after closing audio")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "persistent now-playing bar")
        XCTAssertTrue(element("nowPlayingBar").label.contains(fixture.audioSecondTrackTitle))
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioSecondTrackID, minimum: 3)
        try musicBack()
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Audio catalog while music continues")
        #endif
        try selectDestination("Video", screen: "libraryScreen")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "now-playing bar while browsing Video")
        try await Task.sleep(for: .seconds(3))
        try selectDestination("Audio", screen: "audioScreen")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "reopened persistent audio player")
        XCTAssertEqual(currentAudioTitle(), fixture.audioSecondTrackTitle)
        let reopened = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(reopened, beforeBrowse + 2, "Browsing destinations restarted or paused the audio session.")
        try attachFrame(named: "Persistent audio reopened after browsing Video and Audio")
        #if os(iOS)
        // The expanded player owns its controls; browsing controls must not
        // appear over it, including when playback is paused.
        try revealInLibraryViewport([element("audioPlayPauseButton")], in: element("audioPlayerStage"))
        try activate(element("audioPlayPauseButton"))
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(self.element("audioPlayerStage"))
                && !self.hasVisibleFrame(self.element("nowPlayingBar"))
        }) else {
            throw ProofFailure.ui("Pausing on iPhone did not retain the expanded player and hide the compact now-playing bar.")
        }
        try attachFrame(named: "Paused iPhone audio keeps its expanded player without a compact bar")
        #endif
        try stopAudioPlayer()
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(self.element("nowPlayingBar")) }) else {
            throw ProofFailure.ui("Explicit Stop did not dismiss playback and clear the persistent now-playing bar.")
        }
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioSecondTrackID, minimum: 3)

        completed = true
    }

    #if os(tvOS)
    func journeyTVMusicHomeAndLegacyBrowse() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let capability = try? await api.musicJSON(identity: identity, path: "home/capability") as? [String: Any]
        let enabled = capability?["enabled"] as? Bool == true
        if enabled { _ = try await api.musicJSON(identity: identity, path: "home/layout", method: "PUT", body: ["order": [], "hidden": []]) }
        try await api.replaceProofMusicSession(identity: identity, itemIDs: [fixture.audioSecondTrackID, fixture.audioTrackID], position: 12)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        if enabled {
            let edit = element("tvMusicHomeEdit")
            try waitForExistence(edit, timeout: 25, named: "Music Home")
            let resume = element("tvMusicHomeResume")
            try waitForExistence(resume, timeout: 15, named: "saved Music Home queue")
            try activate(edit)
            try waitForExistence(element("tvMusicHomeEditor"), timeout: 10, named: "Music Home editor")
            XCTAssertTrue(waitUntil(timeout: 8) { self.element("tvMusicHomeDone").hasFocus }, "Editor must start with visible focus.")
            try activate(element("tvMusicHomeVisibility-downloads"))
            XCTAssertTrue(waitUntil(timeout: 10) { self.element("tvMusicHomeVisibility-downloads").label == "Show" })
            try activate(element("tvMusicHomeMoveUp-downloads"))
            XCTAssertTrue(waitUntil(timeout: 10) { !self.element("tvMusicHomeSaving").exists })
            let moved = try await api.musicJSON(identity: identity, path: "home") as? [String: Any]
            let movedLayout = moved?["layout"] as? [String: Any]
            XCTAssertEqual((movedLayout?["order"] as? [String])?.firstIndex(of: "downloads"), 2)
            try activate(element("tvMusicHomeReset"))
            XCTAssertTrue(waitUntil(timeout: 10) { self.element("tvMusicHomeVisibility-downloads").label == "Hide" })
            try activate(element("tvMusicHomeDone"))
            XCTAssertTrue(waitUntil(timeout: 10) { edit.hasFocus }, "Closing the editor restores focus to Edit Home.")
            try activate(resume)
            try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "resumed Music Home player")
            let clock = element("playbackTime")
            _ = try waitForPlaybackValue(clock, timeout: 20) { $0 >= 12 }
            XCUIRemote.shared.press(.menu)
            XCTAssertTrue(waitUntil(timeout: 10) { resume.hasFocus }, "Back from the player restores focus to Resume.")
        } else {
            XCTAssertFalse(element("tvMusicHome").exists, "Unavailable Home must retain legacy navigation.")
        }
        let album = try revealMusicAlbum(fixture, named: "Music browse album")
        try activate(album)
        let track = element("musicItem-\(fixture.audioTrackID)")
        try waitForExistence(track, timeout: 25, named: "Music browse track")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 15) { album.exists && album.hasFocus }, "Back from album detail must restore the browse card.")
        try activate(album)
        try waitForExistence(track, timeout: 20, named: "Music browse track after return")
        try activate(track)
        try waitForExistence(element("nowPlayingBar"), timeout: 30, named: "Music playback")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "Music player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 3 }
        XCUIRemote.shared.press(.playPause)
        let deadline = Date().addingTimeInterval(20)
        var checkpoint = 0.0
        while Date() < deadline {
            if let saved = try await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let position = saved["position_seconds"] as? Double, position >= 3,
               let queue = saved["queue"] as? [[String: Any]],
               let current = saved["current_index"] as? Int, queue.indices.contains(current),
               let id = queue[current]["id"] as? String,
               id.replacingOccurrences(of: "-", with: "").lowercased() == fixture.audioTrackID.replacingOccurrences(of: "-", with: "").lowercased() {
                checkpoint = position
                break
            }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertGreaterThanOrEqual(checkpoint, 3, "Playback must persist a shared queue checkpoint.")
    }

    func journeyTVBrowseShortcutFocus() throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let shortcut = element("tvMusicHomeBrowse")
        try waitForExistence(shortcut, timeout: 25, named: "Music Home browse shortcut")
        try activate(shortcut)
        let album = try revealMusicAlbum(fixture, named: "shortcut album")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album detail")
        XCUIRemote.shared.press(.menu)
        let focusAfterMenu = focusedElement()?.identifier ?? "none"
        let restored = waitUntil(timeout: 15) { album.exists && album.hasFocus }
        if !restored {
            let hierarchy = XCTAttachment(string: application!.debugDescription)
            hierarchy.name = "TV Music Home Browse back-focus hierarchy"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
        }
        XCTAssertTrue(restored, "Back must restore the originating album focus. afterMenu=\(focusAfterMenu) afterWait=\(focusedElement()?.identifier ?? "none") audio=\(element("audioScreen").exists) detail=\(element("musicCollectionScreen").exists) album=\(album.exists) hittable=\(album.isHittable)")
    }

    func journeyTVContextualMixFocus() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "album for Instant Mix")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album detail")

        let albumMix = element("tvInstantMix-current")
        try waitForExistence(albumMix, timeout: 20, named: "album Instant Mix")
        func activateAlbumInstantMix() throws {
            // The real remote path from the track/file-detail column enters the
            // album action rail through Play, then moves down to Instant Mix.
            let play = element("musicPlayButton")
            XCUIRemote.shared.press(.left)
            XCTAssertTrue(waitUntil(timeout: 10) { play.hasFocus }, "Left must enter the album action rail at Play.")
            XCUIRemote.shared.press(.down)
            XCTAssertTrue(waitUntil(timeout: 10) { albumMix.hasFocus }, "Down from Play must reach album Instant Mix.")
            XCUIRemote.shared.press(.select)
        }
        let before = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try activateAlbumInstantMix()
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "album Instant Mix preview")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 10) { albumMix.hasFocus }, "Closing an album preview returns focus to Instant Mix.")
        let after = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        XCTAssertEqual(before?["revision"] as? Int, after?["revision"] as? Int, "Dismissing a preview must leave the queue unchanged.")

        let artist = element("musicArtistButton")
        try activate(artist)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "artist detail")
        let artistMix = element("tvInstantMix-current")
        try waitForExistence(artistMix, timeout: 20, named: "artist Instant Mix")
        func activateArtistInstantMix() throws {
            XCUIRemote.shared.press(.left)
            XCTAssertTrue(waitUntil(timeout: 10) { artistMix.hasFocus }, "Left from an artist album must enter the artist Instant Mix action.")
            XCUIRemote.shared.press(.select)
        }
        let beforeArtist = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try activateArtistInstantMix()
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "artist Instant Mix preview")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 10) { artistMix.hasFocus }, "Closing an artist preview returns focus to Instant Mix.")
        let afterArtist = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        XCTAssertEqual(beforeArtist?["revision"] as? Int, afterArtist?["revision"] as? Int, "Dismissing an artist preview must leave the queue unchanged.")

        XCUIRemote.shared.press(.menu)
        try waitForExistence(artist, timeout: 15, named: "album after artist detail")
        let radio = element("tvTrackRadio-\(fixture.audioTrackID)")
        try waitForExistence(radio, timeout: 20, named: "track radio")
        let beforeTrack = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try activate(radio)
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "album track radio preview")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 10) { radio.hasFocus }, "Closing a detail Track Radio preview returns focus to the initiating control.")
        let afterTrack = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        XCTAssertEqual(beforeTrack?["revision"] as? Int, afterTrack?["revision"] as? Int, "Dismissing a detail Track Radio preview must leave the queue unchanged.")

        let track = element("musicItem-\(fixture.audioTrackID)")
        try activate(track)
        let bar = element("nowPlayingBar")
        try waitForExistence(bar, timeout: 30, named: "Now Playing bar")
        try activate(bar)
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "Now Playing")
        let listeningOptions = element("audioListeningOptions")
        try revealMusicControl(listeningOptions)
        try activate(listeningOptions)
        XCTAssertTrue(waitUntil(timeout: 10) { listeningOptions.value as? String == "Expanded" }, "Listening options must expand for Now Playing Track Radio.")
        let nowPlayingRadio = element("nativeNowPlayingTrackRadio")
        try revealMusicControl(nowPlayingRadio)
        let beforeNowPlaying = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        try activate(nowPlayingRadio)
        try waitForExistence(element("nativeMixPreview"), timeout: 20, named: "Now Playing Track Radio preview")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 10) { nowPlayingRadio.hasFocus }, "Closing Now Playing Track Radio returns focus to its initiating control.")
        let afterNowPlaying = try await api.musicJSON(identity: identity, path: "session") as? [String: Any]
        let beforeNowPlayingQueue = (beforeNowPlaying?["queue"] as? [[String: Any]])?.compactMap { $0["id"] as? String }
        let afterNowPlayingQueue = (afterNowPlaying?["queue"] as? [[String: Any]])?.compactMap { $0["id"] as? String }
        XCTAssertEqual(beforeNowPlayingQueue, afterNowPlayingQueue, "Dismissing Now Playing Track Radio must leave the queue unchanged.")
        XCTAssertEqual(beforeNowPlaying?["current_index"] as? Int, afterNowPlaying?["current_index"] as? Int, "Dismissing Now Playing Track Radio must not change the selected queue entry.")
    }

    func journeyTVMusicContinuationAcrossHomeAndNaturalEnd() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        // Pause promptly after resuming, then opt in without letting focus
        // traversal consume the remainder of this single-track queue.
        try await api.replaceProofMusicSession(identity: identity, itemIDs: [fixture.audioTrackID], position: 65)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let resume = element("tvMusicHomeResume")
        try waitForExistence(resume, timeout: 25, named: "single-track Music Home resume")
        try activate(resume)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "resumed one-track player")
        let clock = element("playbackTime")
        _ = try waitForPlaybackValue(clock, timeout: 20) { $0 >= 65 }
        let playPause = element("audioPlayPauseButton")
        XCUIRemote.shared.press(.playPause)
        XCTAssertTrue(waitUntil(timeout: 12) { playPause.label == "Play" },
                      "Remote Play/Pause must pause before the continuation option traversal.")

        let listeningOptions = element("audioListeningOptions")
        try revealMusicControl(listeningOptions)
        try activate(listeningOptions)
        XCTAssertTrue(waitUntil(timeout: 10) { listeningOptions.value as? String == "Expanded" },
                      "Listening options must expand before continuation can be opted into.")
        try revealMusicControl(element("nativeMusicContinuation"))
        try setNativeSwitch("nativeMusicContinuation", to: true)

        XCUIRemote.shared.press(.playPause)
        XCTAssertTrue(waitUntil(timeout: 12) { playPause.label == "Pause" },
                      "Remote Play/Pause must resume the opted-in track toward its natural end.")
        let resumedAt = try waitForPlaybackValue(clock, timeout: 8) { _ in true }
        _ = try waitForPlaybackValue(clock, timeout: 12) { $0 >= resumedAt + 1 }

        XCUIRemote.shared.press(.home)
        try await Task.sleep(for: .seconds(2))
        let app = try XCTUnwrap(application)
        app.activate()
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "player after tvOS Home foreground")
        let afterForeground = try waitForPlaybackValue(clock, timeout: 8) { _ in true }
        _ = try waitForPlaybackValue(clock, timeout: 12) { $0 >= afterForeground + 1 }

        let deadline = Date().addingTimeInterval(35)
        var advanced = false
        while Date() < deadline {
            if let saved = try await api.musicJSON(identity: identity, path: "session") as? [String: Any],
               let entries = saved["entries"] as? [[String: Any]],
               let current = saved["current_index"] as? Int,
               current > 0,
               entries.indices.contains(current),
               let provenance = entries[current]["provenance"] as? [String: Any],
               provenance["source"] as? String == "contextual_mix" {
                advanced = true
                break
            }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTAssertTrue(advanced,
                      "Opted-in natural completion must append a contextual recommendation and begin it after Home foregrounding.")
    }

    func journeyTVOfflineMusicHome() async throws {
        let fixture = try loadFixture()
        let sharedID = try XCTUnwrap(fixture.sharedMusicPlaylistID)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("tvMusicHomeEdit"), timeout: 25, named: "online Music Home")
        let playlist = element("tvMusicHomePlaylist-\(sharedID)")
        try waitForExistence(playlist, timeout: 20, named: "Music Home playlist")
        try activate(playlist)
        try waitForExistence(element("musicPlaylistDetailScreen"), timeout: 20, named: "shared playlist")
        try revealMusicControl(element("downloadAudioButton"))
        try activate(element("downloadAudioButton"))
        try musicBack()
        try activate(element("tvMusicHomeDownloads"))
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "Music Home downloads")
        try waitForExistence(element("downloadedTrack-\(fixture.audioTrackID)"), timeout: 60, named: "downloaded track")
        try waitForExistence(element("downloadedTrack-\(fixture.audioSecondTrackID)"), timeout: 30, named: "second downloaded track")
        try activate(element("playDownloadsButton"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "downloaded player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 4 }
        XCUIRemote.shared.press(.playPause)
        let paused = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= 4 }
        try await Task.sleep(for: .seconds(2))
        let app = try XCTUnwrap(application)
        app.terminate()
        app.launchArguments.append("--ui-testing-offline")
        app.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "offline cold launch")
        try selectDestination("Audio", screen: "audioScreen")
        let resume = element("tvMusicHomeResume")
        try waitForExistence(resume, timeout: 30, named: "offline paused Music Home queue")
        XCTAssertFalse(element("nowPlayingBar").exists, "Cold restore must remain paused until an explicit action.")
        try activate(resume)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "explicit offline resume")
        XCTAssertEqual(element("audioPlaybackMethod").label, "Downloaded audio")
        let resumed = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= max(0, paused - 2) }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= resumed + 2 }
        XCUIRemote.shared.press(.playPause)
        try await Task.sleep(for: .seconds(2))
        app.terminate()
        app.launchArguments.append("--ui-testing-purge-current-audio")
        app.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "offline launch after cache purge")
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("tvMusicHomeUnavailable"), timeout: 30, named: "recoverable missing download")
        XCTAssertFalse(element("tvMusicHomeResume").exists, "A purged file cannot offer offline Resume.")
    }

    func journeyTVMusicBrowseAndAutoHideBar() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }

        let fixture = try loadFixture()
        try await performancePhase("login_form") {
            try launchAndResetSession()
            try waitForExistence(element("tvManualSignInButton"), timeout: 20, named: "TV music connection welcome")
        }
        try await performanceQuietPhase("tv_music_login_form_idle")
        let firstTrack = element("musicItem-\(fixture.audioTrackID)")
        let secondTrack = element("musicItem-\(fixture.audioSecondTrackID)")
        try await performancePhase("tv_music_sign_in_album") {
            try signIn(using: fixture)
            try selectDestination("Audio", screen: "audioScreen")
            let album = try revealMusicAlbum(fixture, named: "TV music album")
            try activate(album)
            try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "TV album track listing")
            try waitForExistence(firstTrack, timeout: 20, named: "first TV album track")
            try waitForExistence(secondTrack, timeout: 20, named: "second TV album track")
        }
        try await performanceQuietPhase("tv_music_album_ready_idle")
        try attachFrame(named: "TV album track layout")

        try activate(firstTrack)
        let bar = element("nowPlayingBar")
        try waitForExistence(bar, timeout: 30, named: "TV inline now-playing bar")
        XCTAssertTrue(bar.label.contains(fixture.audioTrackTitle))
        XCTAssertFalse(hasVisibleFrame(element("playbackScreen")), "Selecting a TV track must not force the full player.")
        try await performanceQuietPhase("tv_music_playing_inline_idle")
        try attachFrame(named: "TV album playing inline")

        let next = element("nowPlayingNextButton")
        try revealMusicControl(next)
        try activate(next)
        XCTAssertTrue(waitUntil(timeout: 20) {
            self.element("nowPlayingBar").label.contains(fixture.audioSecondTrackTitle)
                && (self.element("musicItem-\(fixture.audioSecondTrackID)").value as? String) == "Playing"
        }, "The now-playing bar's Next control must select the second album track.")

        XCUIRemote.shared.press(.playPause)
        guard waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(bar) }) else {
            throw ProofFailure.ui("Pausing a TV album track must hide the compact now-playing bar.")
        }
        try waitForExistence(element("musicCollectionScreen"), timeout: 10, named: "album while paused")
        try await performanceQuietPhase("tv_music_paused_bar_hidden_idle")
        try attachFrame(named: "TV album paused with compact bar hidden")

        XCUIRemote.shared.press(.playPause)
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(bar) && bar.label.contains(fixture.audioSecondTrackTitle)
        }) else {
            throw ProofFailure.ui("Resuming a TV album track did not restore the visible compact now-playing bar.")
        }

        let artist = element("musicArtistButton")
        try waitForExistence(artist, timeout: 25, named: "album artist pivot")
        try activate(artist)
        let artistAlbum = element("musicItem-\(fixture.audioAlbumID)")
        try waitForExistence(artistAlbum, timeout: 25, named: "artist album navigation")
        try await performanceQuietPhase("tv_music_artist_albums_idle")
        try attachFrame(named: "TV artist album grid")
        try activate(artistAlbum)
        try waitForExistence(firstTrack, timeout: 25, named: "album reopened from artist")
        XCUIRemote.shared.press(.menu)
        guard waitUntil(timeout: 20, condition: {
            self.hasVisibleFrame(self.element("musicItem-\(fixture.audioAlbumID)"))
                && self.element("nowPlayingBar").label.contains(fixture.audioSecondTrackTitle)
        }) else {
            throw ProofFailure.ui("Back from an artist album must return to the artist without interrupting music.")
        }

        try activate(bar)
        let audioStage = element("audioPlayerStage")
        try waitForExistence(audioStage, timeout: 20, named: "TV expanded player from compact bar")
        let fullPlayerTime = element("playbackTime")
        try waitForExistence(fullPlayerTime, timeout: 15, named: "TV expanded audio playback time")
        XCUIRemote.shared.press(.playPause)
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(audioStage) && !self.hasVisibleFrame(self.element("nowPlayingBar"))
        }) else {
            throw ProofFailure.ui("Pausing in the TV full player must keep the player open while hiding the compact bar.")
        }
        let pausedAt = try waitForPlaybackValue(fullPlayerTime, timeout: 5) { _ in true }
        try await performanceQuietPhase("tv_music_full_player_paused_idle")
        let stillPaused = try waitForPlaybackValue(fullPlayerTime, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, pausedAt + 0.5,
                                 "Audio advanced during the TV full-player paused idle window.")
        XCUIRemote.shared.press(.playPause)
        let resumedAt = try waitForPlaybackValue(fullPlayerTime, timeout: 10) { _ in true }
        guard hasVisibleFrame(audioStage),
              (try waitForPlaybackValue(fullPlayerTime, timeout: 15) { $0 >= resumedAt + 2 }) >= resumedAt + 2 else {
            throw ProofFailure.ui("Resuming in the TV full player did not retain the player and advance audio playback.")
        }
        XCUIRemote.shared.press(.menu)
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { self.hasVisibleFrame(bar) }) else {
            throw ProofFailure.ui("Back from the TV full player must return to the active compact player.")
        }
        try activate(bar)
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "reopened TV full player")
        try stopAudioPlayer()
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(bar) }) else {
            throw ProofFailure.ui("Stop in the TV full player must dismiss it and remove the compact bar.")
        }
        try await performanceQuietPhase("tv_music_post_stop_idle")
        completed = true
    }
    #endif

}
