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
    func journeyNativePerformanceTour() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        if let selection = ProcessInfo.processInfo.environment["LUMINA_PERF_PHASES"], !selection.isEmpty {
            let phases = selection.split(separator: ",").map(String.init)
            guard phases.allSatisfy(Self.performanceTourPhases.contains) else {
                throw ProofFailure.ui("LUMINA_PERF_PHASES contains an unknown phase.")
            }
        }
        let startAt = try performanceTourStartPhase()

        try await performancePhase("login_form") {
            try launchAndResetSession()
            #if os(tvOS)
            try waitForExistence(element("tvManualSignInButton"), timeout: 20, named: "connection welcome")
            #else
            try waitForExistence(element("qrPairingLanding"), timeout: 20, named: "QR pairing landing")
            #endif
        }
        try await performanceQuietPhase("login_form_idle")
        try await performancePhase("sign_in_home") {
            #if os(tvOS)
            try revealTVManualConnection()
            let serverField = element("serverURLField")
            try enter(fixture.serverURL, into: serverField)
            let showcase = element("connectionShowcaseReady")
            XCTAssertTrue(waitUntil(timeout: 25) {
                (showcase.value as? String)?.hasPrefix("remote:") == true
            }, "The TV performance tour needs a decoded public welcome release before sign-in.")
            try await performanceQuietPhase("tv_remote_showcase_idle")
            try enter(fixture.username, into: element("usernameField"))
            try enter(fixture.password, into: element("passwordField"))
            try activate(element("connectButton"))
            try waitForPerformanceContainer(element("homeScreen"), timeout: 40, named: "Home after sign in")
            #else
            try signIn(using: fixture)
            try waitForPerformanceContainer(element("homeScreen"), timeout: 20, named: "Home after sign in")
            #endif
        }
        try await performanceQuietPhase("home_idle")

        if shouldRunPerformancePhase("home_feature_detail", startingAt: startAt) {
            try await performancePhase("home_feature_detail") {
            let details = element("featuredTitleButton")
            #if os(tvOS)
            try selectTVFeaturedTitle(named: fixture.directItemTitle)
            #endif
            let readyDetails = try waitForPerformanceControl(details, timeout: 20, named: "Home feature details")
            try activate(readyDetails)
            try waitForPerformanceContainer(element("detailScreen"), timeout: 20, named: "Home feature detail")
            try await performanceQuietPhase("home_feature_detail_idle")
            try returnToLibrary()
            }
        }

        if shouldRunPerformancePhase("library_catalog", startingAt: startAt) {
            try await performancePhase("library_catalog") {
            try selectTourVideoCategory("movies")
            try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "movie catalog item")
            #if os(iOS)
            try scrollLibrary(element("libraryScreen"), up: true)
            try scrollLibrary(element("libraryScreen"), up: false)
            #else
            let rawSteps = ProcessInfo.processInfo.environment["LUMINA_PERF_TV_SCROLL_STEPS"] ?? "8"
            guard let scrollSteps = Int(rawSteps), (1...80).contains(scrollSteps) else {
                throw ProofFailure.ui("LUMINA_PERF_TV_SCROLL_STEPS must be from 1 through 80.")
            }
            if let expected = ProcessInfo.processInfo.environment["LUMINA_PERF_EXPECTED_CATALOG_COUNT"] {
                guard let count = Int(expected), count > 0 else { throw ProofFailure.invalidFixture }
                guard waitUntil(timeout: 25, condition: {
                    (self.element("libraryScreen").value as? String)?.contains("catalog=\(count) ") == true
                }) else { throw ProofFailure.ui("The complete stress catalog did not load before scrolling.") }
            }
            for cycle in 1...6 {
                try await performancePhase("tv_library_focus_sweep_\(cycle)") {
                    let fixtureMovie = element("libraryItem-\(fixture.directItemID)")
                    guard focus(fixtureMovie, timeout: 15) else {
                        throw ProofFailure.ui("The TV movie grid could not focus the fixture title before the focus sweep.")
                    }
                    for _ in 0..<scrollSteps { XCUIRemote.shared.press(.down) }
                    for _ in 0..<8 { XCUIRemote.shared.press(.right) }
                    for _ in 0..<8 { XCUIRemote.shared.press(.up) }
                    for _ in 0..<8 { XCUIRemote.shared.press(.left) }
                    try waitForPerformanceContainer(element("libraryScreen"), timeout: 10, named: "TV movie catalog after focus sweep")
                    // The sweep deliberately scrolls the initial fixture out of the lazy grid.
                    // Return through the public alphabet instead of steering toward an absent AX cell.
                    let alphabet = element("libraryAlphabetIndex")
                    guard focus(alphabet, timeout: 15) else {
                        throw ProofFailure.ui("The TV alphabet was unreachable after the focus sweep.")
                    }
                    // Up at the first letter moves into Search; select the current letter directly.
                    try activate(alphabet)
                    try waitForExistence(fixtureMovie, timeout: 20, named: "first-page fixture after alphabet jump")
                    guard focus(fixtureMovie, timeout: 20) else {
                        throw ProofFailure.ui("The TV movie grid could not focus the fixture after an alphabet jump.")
                    }
                }
                try await performanceQuietPhase("library_catalog_cycle_\(cycle)_idle")
            }
            #endif
            }
            try await performanceQuietPhase("library_idle")
        }

        if shouldRunPerformancePhase("movie_detail_cast_related", startingAt: startAt) {
            try await performancePhase("movie_detail_cast_related") {
            try openTourVideoItem(id: fixture.directItemID)
            #if os(iOS)
            let cast = element("detailCastSection")
            let related = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "related-")).firstMatch
            #else
            let cast = element("detailCastRail")
            let related = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "relatedTitle-")).firstMatch
            #endif
            try waitForPerformanceContainer(cast, timeout: 20, named: "movie cast")
            try waitForExistence(related, timeout: 20, named: "related movie")
            try await performanceQuietPhase("movie_detail_idle")
            #if os(iOS)
            try revealDetailControl(related, named: "related movie")
            #else
            guard focus(related, timeout: 20) else { throw ProofFailure.ui("Related movie was unreachable with the remote.") }
            #endif
            try activate(related)
            try waitForPerformanceContainer(element("detailScreen"), timeout: 20, named: "related movie detail")
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
            #endif
            try waitForExistence(element("playButton"), timeout: 20, named: "original movie detail")
            try returnTourToLibrary()
            }
        }

        if shouldRunPerformancePhase("show_and_season", startingAt: startAt) {
            try await performancePhase("show_and_season") {
            try selectTourVideoCategory("tv")
            try activate(element("libraryItem-\(fixture.showID)"))
            try waitForPerformanceContainer(element("videoCollectionScreen"), timeout: 25, named: "show seasons")
            try await performanceQuietPhase("show_detail_idle")
            #if os(iOS)
            try selectCollectionSeason(fixture.seasonID)
            let episode = element("videoEpisode-\(fixture.episodeID)")
            try revealDetailElement(
                episode,
                in: element("videoCollectionScreen"),
                named: "season episode",
                requiresHittable: true
            )
            #else
            try waitForPerformanceContainer(
                element("videoCollectionItem-\(fixture.seasonID)"),
                timeout: 20,
                named: "inline show season selector"
            )
            let episode = element("videoCollectionItem-\(fixture.episodeID)")
            #endif
            let readyEpisode = try waitForPerformanceControl(episode, timeout: 20, named: "season episode")
            try await performanceQuietPhase("show_season_idle")
            try activate(readyEpisode)
            try waitForPerformanceContainer(element("videoCollectionScreen"), timeout: 20, named: "show after episode selection")
            try returnTourToLibrary()
            }
        }

        if shouldRunPerformancePhase("video_play_pause_settings_close", startingAt: startAt) {
            try await performancePhase("video_play_pause_settings_close") {
            try openTourVideoItem(id: fixture.directItemID)
            let time = try startPlayback(using: "playButton")
            let playingAt = try waitForPlaybackValue(time, timeout: 20) { _ in true }
            _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= playingAt + 1 }
            try await performanceQuietPhase("video_playing_idle")
            #if os(tvOS)
            let beforeSettingsShortcut = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            try openTVPlaybackSettings()
            XCUIRemote.shared.press(.menu)
            XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 10))
            _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= beforeSettingsShortcut + 1 }

            XCUIRemote.shared.press(.playPause)
            _ = try waitForPerformanceControl(element("Media Controls"), timeout: 10, named: "paused video transport")
            try await performanceQuietPhase("video_paused_idle")
            try openTVPlaybackSettings()
            #else
            try tapVideoTransportAfterChromeHides(named: "performance video pause control")
            try await performanceQuietPhase("video_paused_idle")
            try activate(element("videoSettingsButton"))
            #endif
            try waitForPerformanceContainer(element("videoSettingsPanel"), timeout: 15, named: "video settings")
            try activate(element("videoSettingsTab-video"))
            try await performanceQuietPhase("video_settings_video_idle")
            try activate(element("videoSettingsTab-audio"))
            try await performanceQuietPhase("video_settings_audio_idle")
            #if os(iOS)
            if !element("videoSettingsTab-subtitles").isHittable {
                element("videoSettingsTab-audio").swipeLeft()
            }
            #endif
            try activate(element("videoSettingsTab-subtitles"))
            try await performanceQuietPhase("video_settings_subtitles_idle")
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(element("videoSettingsDoneButton"))
            #endif
            try closePlayer()
            try waitForPerformanceContainer(element("detailScreen"), timeout: 15, named: "movie detail after player close")
            try returnTourToLibrary()
            }
        }

        if shouldRunPerformancePhase("mkv_client_demux_play_pause_settings_close", startingAt: startAt) {
            try await performancePhase("mkv_client_demux_play_pause_settings_close") {
            try openTourVideoItem(id: fixture.convertedItemID)
            let time = try startPlayback(using: "playButton")
            let playingAt = try waitForPlaybackValue(time, timeout: 25) { _ in true }
            _ = try waitForPlaybackValue(time, timeout: 25) { $0 >= playingAt + 1 }
            try await performanceQuietPhase("mkv_client_demux_playing_idle")
            #if os(tvOS)
            XCUIRemote.shared.press(.playPause)
            _ = try waitForPerformanceControl(
                element("Media Controls"), timeout: 10, named: "paused MKV video transport"
            )
            #else
            try tapVideoTransportAfterChromeHides(named: "performance MKV pause control")
            #endif
            let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCTAssertFalse(waitUntil(timeout: 3) {
                (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5
            }, "The MKV client-demux checkpoint must remain paused before settings.")
            #if os(tvOS)
            try openTVPlaybackSettings()
            #else
            try activate(element("videoSettingsButton"))
            #endif
            try waitForPerformanceContainer(element("videoSettingsPanel"), timeout: 15, named: "MKV video settings")
            try activate(element("videoSettingsTab-video"))
            try await performanceQuietPhase("mkv_client_demux_settings_idle")
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(element("videoSettingsDoneButton"))
            #endif
            try closePlayer()
            try waitForPerformanceContainer(element("detailScreen"), timeout: 15, named: "MKV movie detail after player close")
            try returnTourToLibrary()
            }
            try await performanceQuietPhase("mkv_client_demux_post_close_idle")
        }

        if shouldRunPerformancePhase("audio_album_player_settings_queue", startingAt: startAt) {
            try await performancePhase("audio_album_player_settings_queue") {
            try selectTourAudioRoot()
            try await performanceQuietPhase("audio_catalog_idle")
            let album = try revealMusicAlbum(fixture, named: "performance-tour audio album")
            try activate(album)
            try waitForPerformanceContainer(element("musicCollectionScreen"), timeout: 25, named: "audio album")
            try await performanceQuietPhase("audio_album_idle")
            try activate(element("musicItem-\(fixture.audioTrackID)"))
            #if os(tvOS)
            let bar = element("nowPlayingBar")
            try waitForPerformanceContainer(bar, timeout: 30, named: "inline audio now-playing bar")
            XCTAssertFalse(hasVisibleFrame(element("playbackScreen")), "TV album selection must remain inline.")
            try activate(bar)
            try waitForPerformanceContainer(element("audioPlayerStage"), timeout: 20, named: "expanded TV audio player")
            let time = element("playbackTime")
            try waitForExistence(time, timeout: 20, named: "TV audio playback time")
            #else
            try waitForPerformanceContainer(element("audioPlayerStage"), timeout: 30, named: "direct audio player")
            let time = element("playbackTime")
            try waitForExistence(time, timeout: 20, named: "audio playback time")
            #endif
            let playingAt = try waitForPlaybackValue(time, timeout: 20) { _ in true }
            _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= playingAt + 1 }
            try await performanceQuietPhase("audio_playing_idle")
            #if os(iOS)
            let inlineCompanion = hasVisibleFrame(element("closePlayerButton"))
            #else
            let inlineCompanion = false
            #endif
            if !inlineCompanion {
                try revealMusicControl(element("audioListeningOptions"))
                try activate(element("audioListeningOptions"))
            }
            try revealMusicControl(element("audioQueueButton"))
            try activate(element("audioQueueButton"))
            try waitForPerformanceContainer(element("audioQueueScreen"), timeout: 20, named: "audio queue")
            try await performanceQuietPhase("audio_queue_idle")
            try activate(element("audioQueueDoneButton"))
            try stopAudioPlayer()
            #if os(iOS)
            let sharedPlaylistID = try XCTUnwrap(
                fixture.sharedMusicPlaylistID,
                "Generate the shared music playlist proof fixture."
            )
            try musicBack()
            try waitForExistence(element("audioScreen"), timeout: 15, named: "audio catalog after playback")
            try activate(element("musicOptionsMenu"))
            try activate(element("musicLibrary-playlists"))
            try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "music playlists")
            try await performanceQuietPhase("audio_playlists_idle")
            let playlist = element("musicPlaylist-\(sharedPlaylistID)")
            try revealMusicControl(playlist)
            try activate(playlist)
            try waitForExistence(element("musicPlaylistDetailScreen"), timeout: 20, named: "shared playlist")
            try await performanceQuietPhase("audio_playlist_detail_idle")
            try musicBack()
            try musicBack()
            try waitForExistence(element("audioScreen"), timeout: 15, named: "audio catalog before downloads")
            try activate(element("musicOptionsMenu"))
            try activate(element("musicLibrary-downloads"))
            try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "music downloads")
            try await performanceQuietPhase("audio_downloads_idle")
            try musicBack()
            #else
            try unwindTVAudioToRoot()
            try waitForPerformanceContainer(element("audioScreen"), timeout: 15, named: "audio catalog after playback")
            #endif
            }
        }

        if shouldRunPerformancePhase("requests_detail_history", startingAt: startAt) {
            try await performancePhase("requests_detail_history") {
            let movieKey = try XCTUnwrap(fixture.requestMovieKey, "Generate the Requests movie fixture.")
            let movieTitle = try XCTUnwrap(fixture.requestMovieTitle, "Generate the Requests movie fixture.")
            try selectDestination("Requests", screen: "requestsScreen")
            try await performanceQuietPhase("requests_idle")
            #if os(iOS)
            try waitForExistence(application!.buttons["Watch trailer on YouTube"], timeout: 15, named: "featured trailer")
            #endif
            #if os(tvOS)
            let search = try openTVRequestsSearch()
            let readySearch = try waitForPerformanceControl(search, timeout: 20, named: "Requests search")
            try enter(movieTitle, into: readySearch)
            let searchResult = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "requestPoster-")
            ).firstMatch
            _ = try waitForPerformanceControl(searchResult, timeout: 30, named: "TV Requests search result")
            try await performanceQuietPhase("requests_search_idle")
            try activate(element("requestsDiscoverButton"))
            let browse = element("requestsBrowseMenu")
            _ = try waitForPerformanceControl(browse, timeout: 20, named: "TV Requests browse controls")
            try await performanceQuietPhase("requests_browse_controls_idle")
            let detail = element("requestFeatured-\(movieKey)")
            #else
            let detail = element("requestDetails-\(movieKey)")
            #endif
            let readyDetail = try waitForPerformanceControl(detail, timeout: 30, named: "Requests featured movie")
            try activate(readyDetail)
            try waitForPerformanceContainer(element("requestDetailScreen"), timeout: 20, named: "Requests detail")
            try await performanceQuietPhase("requests_detail_idle")
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(element("requestDetailsCloseButton"))
            #endif
            try waitForPerformanceContainer(element("requestsScreen"), timeout: 15, named: "Requests after detail")
            #if os(iOS)
            try activate(element("requestsOptionsMenu"))
            #endif
            try activate(element("requestsMyListButton"))
            try waitForPerformanceContainer(element("requestHistoryScreen"), timeout: 20, named: "Requests history")
            try await performanceQuietPhase("requests_history_idle")
            #if os(tvOS)
            XCUIRemote.shared.press(.menu)
            #else
            try activate(menuAction("Done"))
            #endif
            try waitForPerformanceContainer(element("requestsScreen"), timeout: 15, named: "Requests after history")
            #if os(iOS)
            let usesIPadCategories = element("iPadUnifiedCategories").exists
            try activate(element("requestsSearchButton"))
            let search = application!.searchFields.firstMatch
            try waitForHittable(search, timeout: 10, named: "Requests search")
            try enter(movieTitle, into: search)
            let searchResult = try revealRequestPoster(movieKey, named: "Requests movie search result")
            try waitForHittable(searchResult, timeout: 20, named: "Requests movie search result")
            try await performanceQuietPhase("requests_search_idle")
            // The iPad drawer owns an explicit content Cancel; compact
            // navigation uses the system Close control.
            let closeSearch = usesIPadCategories
                ? element("nativeSearchDismissButton")
                : application!.navigationBars.buttons["Close"]
            try waitForHittable(closeSearch, timeout: 10, named: "Requests search close")
            try activate(closeSearch)
            XCTAssertTrue(disappears(search, timeout: 10), "Requests search drawer must close before Account navigation.")
            try waitForExistence(application!.buttons["Watch trailer on YouTube"], timeout: 15, named: "featured trailer after search cancellation")
            try waitForHittable(destinationControl("Account"), timeout: 10, named: "Account after Requests search")
            #endif
            }
        }

        if shouldRunPerformancePhase("account", startingAt: startAt) {
            try await performancePhase("account") {
            #if os(iOS)
            try selectDestination("Account", screen: "accountScreen")
            try revealInLibraryViewport([element("homeEditButton")], in: element("accountScreen"))
            try waitForHittable(element("homeEditButton"), timeout: 20, named: "Home settings in Account")
            try await performanceQuietPhase("account_idle")
            try activate(element("homeEditButton"))
            try waitForHittable(element("homeLayoutSaveButton"), timeout: 20, named: "Home editor")
            try await performanceQuietPhase("account_home_editor_idle")
            try activate(element("homeLayoutCancelButton"))
            try waitForHittable(element("homeEditButton"), timeout: 15, named: "Account after Home editor")
            #else
            try selectDestination("Account", screen: "accountScreen")
            try waitForPerformanceContainer(element("deviceSettingsScreen"), timeout: 15, named: "TV Device settings")
            try await performanceQuietPhase("account_device_settings_idle")
            XCUIRemote.shared.press(.menu)
            #endif
            }
        }

        #if os(iOS)
        if shouldRunPerformancePhase("books_reader", startingAt: startAt) {
            try await performancePhase("books_reader") {
            let bookID = try XCTUnwrap(fixture.proseBookID, "Generate the prose book proof fixture.")
            try selectTourBooksRoot()
            do {
                try revealBookItem(element("bookCatalogButton"))
            } catch {
                #if LUMINA_UI_TESTING
                await emitBooksReaderFailureEvidence(fixture: fixture, proseBookID: bookID)
                #endif
                throw error
            }
            try activate(element("bookCatalogButton"))
            let book = element("book-\(bookID)")
            do {
                try revealBookItem(book)
            } catch {
                #if LUMINA_UI_TESTING
                await emitBooksReaderFailureEvidence(fixture: fixture, proseBookID: bookID)
                #endif
                throw error
            }
            try await performanceQuietPhase("books_catalog_idle")
            // Tap the visible artwork rather than the cell center under Duo’s side rail.
            book.coordinate(withNormalizedOffset: CGVector(dx: 0.1, dy: 0.2)).tap()
            try waitForExistence(element("bookControlsButton"), timeout: 25, named: "book reader controls")
            try await performanceQuietPhase("books_reader_idle")
            try showBookControls()
            try activate(element("bookCloseButton"))
            try waitForExistence(element("booksScreen"), timeout: 15, named: "books catalog after prose reader")
            let comicID = try XCTUnwrap(fixture.comicBookID, "Generate the comic book proof fixture.")
            let comic = element("book-\(comicID)")
            try revealBookItem(comic)
            try await performanceQuietPhase("books_comic_catalog_idle")
            try activate(comic)
            try waitForExistence(element("bookPrimaryPage"), timeout: 25, named: "comic reader page")
            try await performanceQuietPhase("books_comic_reader_idle")
            try showBookControls()
            try activate(element("bookCloseButton"))
            try waitForExistence(element("booksScreen"), timeout: 15, named: "books catalog after comic reader")
            }
        }
        #endif

        try attachFrame(named: "Native performance tour representative destination")
        completed = true
    }

    private func performanceTourStartPhase() throws -> String? {
        let value = ProcessInfo.processInfo.environment["LUMINA_PERF_START_AT"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let value, !value.isEmpty else { return nil }
        guard Self.performanceTourPhases.contains(value) else {
            throw ProofFailure.ui("Unknown LUMINA_PERF_START_AT phase: \(value).")
        }
        #if os(tvOS)
        if value == "books_reader" {
            throw ProofFailure.ui("The books_reader performance phase is available only on iOS.")
        }
        #endif
        return value
    }

    private func shouldRunPerformancePhase(_ phase: String, startingAt startAt: String?) -> Bool {
        if let selection = ProcessInfo.processInfo.environment["LUMINA_PERF_PHASES"], !selection.isEmpty {
            return selection.split(separator: ",").map(String.init).contains(phase)
        }
        guard let startAt,
              let selectedIndex = Self.performanceTourPhases.firstIndex(of: startAt),
              let phaseIndex = Self.performanceTourPhases.firstIndex(of: phase) else {
            return true
        }
        return phaseIndex >= selectedIndex
    }

    func performanceQuietPhase(_ phase: String) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", started: started)
        try await Task.sleep(for: .seconds(2))
        emitPerformanceMarker(phase: phase, event: "ready", started: started)
        // Keep the final app process alive long enough for an app-scoped leak
        // scan and delayed decoder/session teardown measurement.
        try await Task.sleep(for: .seconds(8))
        emitPerformanceMarker(phase: phase, event: "end", started: started)
    }

    func performancePhase(_ phase: String,
                                  operation: @MainActor () async throws -> Void) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", started: started)
        try await operation()
        emitPerformanceMarker(phase: phase, event: "ready", started: started)
        emitPerformanceMarker(phase: phase, event: "end", started: started)
    }

    /// tvOS reports an unfocused remote control as non-hittable even when it is
    /// visibly ready. `activate` below still must route actual remote focus.
    func waitForPerformanceControl(_ control: XCUIElement, timeout: TimeInterval,
                                           named name: String) throws -> XCUIElement {
        #if os(iOS)
        return try waitForHittable(control, timeout: timeout, named: name)
        #else
        var visibleControl: XCUIElement?
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let identifier = control.identifier
            let candidates = identifier.isEmpty
                ? [control]
                : application!.descendants(matching: .any).matching(identifier: identifier).allElementsBoundByIndex
            if let candidate = (candidates + [control]).first(where: {
                $0.exists && hasVisibleFrame($0) && $0.isEnabled
            }) {
                visibleControl = candidate
                break
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        guard let visibleControl else {
            throw ProofFailure.timeout("Timed out waiting for visible \(name).")
        }
        return visibleControl
        #endif
    }

    private func waitForPerformanceContainer(_ container: XCUIElement, timeout: TimeInterval,
                                             named name: String) throws {
        #if os(iOS)
        try waitForExistence(container, timeout: timeout, named: name)
        #else
        guard waitUntil(timeout: timeout, condition: { container.exists && self.hasVisibleFrame(container) }) else {
            throw ProofFailure.timeout("Timed out waiting for visible \(name).")
        }
        #endif
    }

    private func emitPerformanceSkip(_ phase: String, reason: String) {
        let marker: [String: Any] = [
            "event": "skipped",
            "phase": phase,
            "reason": reason,
            "timestamp": Date().timeIntervalSince1970,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return }
        print("LUMINA_PERF \(text)")
    }

    func emitPerformanceMarker(phase: String, event: String, started: ContinuousClock.Instant) {
        let duration = started.duration(to: .now).components
        let elapsed = Double(duration.seconds) * 1_000 + Double(duration.attoseconds) / 1e15
        var marker: [String: Any] = [
            "event": event,
            "elapsed_ms": Int(elapsed.rounded()),
            "phase": phase,
            "timestamp": Date().timeIntervalSince1970,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return }
        print("LUMINA_PERF \(text)")
    }

    #if os(iOS)
    func journeyStandardPhoneEpisodeChromeAndBack() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        guard application!.windows.firstMatch.frame.width < 450 else {
            throw XCTSkip("This workflow targets a standard portrait iPhone.")
        }

        let browse = element("compactBrowseMenu")
        try waitForHittable(browse, timeout: 20, named: "compact Browse menu")
        try activate(browse)
        try activate(element("compactCategory-tv"))
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "TV library from Browse")
        let show = element("libraryItem-\(fixture.showID)")
        try activate(show)
        let collection = element("videoCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "show collection")
        try selectCollectionSeason(fixture.seasonID)
        let selectedEpisode = element("videoEpisode-\(fixture.secondEpisodeID)")
        try revealDetailElement(selectedEpisode, in: collection, named: "direct episode", requiresHittable: true)
        try activate(selectedEpisode)

        try waitForExistence(element("playbackScreen"), timeout: 30, named: "tapped episode playback")
        try closePlayer()
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.hasVisibleFrame(selectedEpisode) && selectedEpisode.isHittable
                && (self.element("videoEpisode-\(fixture.secondEpisodeID)").value as? String) == "Selected"
        }, "The direct episode card must be selected and visible without manual scrolling.")
        try assertStandardPhoneCollectionHeader(in: collection, phase: "selected episode", requiresVisibleHeader: true)
        try attachFrame(named: "Standard iPhone safe episode collection header")

        // A rail drag starts well away from the physical leading edge and must
        // not pop the show collection.
        let railStart = selectedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.82, dy: 0.5))
        let railEnd = selectedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.18, dy: 0.5))
        railStart.press(forDuration: 0.05, thenDragTo: railEnd)
        try waitForExistence(collection, timeout: 15, named: "collection after horizontal episode rail drag")

        collection.swipeUp()
        try assertStandardPhoneCollectionHeader(in: collection, phase: "scrolled episode collection", requiresVisibleHeader: false)

        let cancelledStart = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24)
        )
        let cancelledEnd = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.07, dy: 0.24)
        )
        cancelledStart.press(forDuration: 0.05, thenDragTo: cancelledEnd)
        try waitForExistence(collection, timeout: 15, named: "collection after cancelled leading-edge Back")

        try performStandardPhoneLeadingEdgeBack(
            from: collection,
            to: element("libraryScreen"),
            named: "episode collection"
        )
        XCTAssertFalse(element("detailScreen").exists,
                       "Leading-edge Back from the show collection must return to Library, not a standalone episode detail.")
        completed = true
    }

    func journeyStandardPhoneHomeSearchLeadingEdgeBack() throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForHittable(element("homeSearchButton"), timeout: 20, named: "Home Search")
        try activate(element("homeSearchButton"))

        let search = element("homeSearchScreen")
        try assertStandardPhonePushedChrome(search, named: "Home Search")
        let query = element("homeSearchField")
        try replaceText(query, with: fixture.directItemTitle)
        XCTAssertTrue(waitUntil(timeout: 10) {
            (query.value as? String)?.localizedCaseInsensitiveContains(fixture.directItemTitle) == true
        }, "The standard-phone Home Search field must visibly retain the typed query.")
        let clear = element("homeSearchClearButton")
        try waitForHittable(clear, timeout: 10, named: "Home Search clear")
        XCTAssertGreaterThanOrEqual(clear.frame.width, 44, "Home Search clear needs a 44-point-wide hit target")
        XCTAssertGreaterThanOrEqual(clear.frame.height, 44, "Home Search clear needs a 44-point-tall hit target")
        let result = element("homeSearchResult-\(fixture.directItemID)")
        try waitForHittable(result, timeout: 20, named: "visible Home Search result")
        XCTAssertTrue(hasVisibleFrame(result), "The selected Home Search result must render in the current search screen.")
        try activate(result)
        let detail = element("detailScreen")
        try waitForExistence(detail, timeout: 20, named: "Home Search result detail")
        try performStandardPhoneLeadingEdgeBack(from: detail, to: search, named: "Home Search result")
        XCTAssertTrue(waitUntil(timeout: 10) {
            (query.value as? String)?.localizedCaseInsensitiveContains(fixture.directItemTitle) == true
        }, "Leading-edge Back must restore the typed Home Search query.")
        try waitForHittable(result, timeout: 15, named: "Home Search result restored after Back")
        XCTAssertTrue(hasVisibleFrame(result),
                      "Leading-edge Back must restore the populated Home Search results, not an empty search shell.")

        let window = try applicationWindow()
        let shortStart = window.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24))
        let shortEnd = window.coordinate(withNormalizedOffset: CGVector(dx: 0.07, dy: 0.24))
        shortStart.press(forDuration: 0.05, thenDragTo: shortEnd)
        try waitForExistence(search, timeout: 10, named: "Home Search after short leading-edge drag")

        let verticalStart = window.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24))
        let verticalEnd = window.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.42))
        verticalStart.press(forDuration: 0.05, thenDragTo: verticalEnd)
        try waitForExistence(search, timeout: 10, named: "Home Search after vertical edge drag")

        try performStandardPhoneLeadingEdgeBack(from: search, to: element("homeScreen"), named: "Home Search")
        try performStandardPhoneEdgeGesture(named: "Home root")
        try waitForExistence(element("homeScreen"), timeout: 10, named: "Home after root leading-edge drag")
    }

    func journeyStandardPhoneAppearanceChrome() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)

        try selectDestination("Account", screen: "accountScreen")
        let appearancePicker = element("deviceAppearancePicker")
        try waitForExistence(appearancePicker, timeout: 15, named: "device appearance picker")
        let originalAppearance = try XCTUnwrap(
            ["Default", "Light", "Dark", "OLED", "System"].first {
                accessibilityText(appearancePicker).localizedCaseInsensitiveContains($0)
            },
            "The device appearance picker did not expose its current selection."
        )
        defer {
            if !completed {
                try? self.selectDestination("Account", screen: "accountScreen")
                try? self.chooseDeviceSetting("deviceAppearancePicker", option: originalAppearance)
            }
        }

        // Caption appearance is an ordinary pushed Account route.  It must
        // use the same standard-phone edge Back without disturbing Account.
        try activateDeviceControl("deviceSubtitleAppearanceButton")
        try performStandardPhoneLeadingEdgeBack(from: element("deviceSubtitleAppearanceScreen"),
                                                 to: element("accountScreen"),
                                                 named: "caption appearance")

        func openFocusedEpisode(appearance: String) throws {
            try selectDestination("Home", screen: "homeScreen")
            let browse = element("compactBrowseMenu")
            try waitForHittable(browse, timeout: 15, named: "Home Browse in \(appearance) appearance")
            try activate(browse)
            try activate(element("compactCategory-tv"))
            try waitForExistence(element("libraryScreen"), timeout: 20, named: "TV library in \(appearance) appearance")
            try activate(try revealLibraryItem(id: fixture.showID, named: "show in \(appearance) appearance"))
            let collection = element("videoCollectionScreen")
            try assertStandardPhonePushedChrome(collection, named: "show collection in \(appearance) appearance")
            try attachFrame(named: "Standard iPhone \(appearance) show hero")

            try selectCollectionSeason(fixture.seasonID)
            let episode = element("videoEpisode-\(fixture.secondEpisodeID)")
            try revealDetailElement(episode, in: collection, named: "selected episode in \(appearance) appearance", requiresHittable: true)
            try activate(episode)
            try waitForExistence(element("playbackScreen"), timeout: 30, named: "episode playback in \(appearance) appearance")
            try closePlayer()
            XCTAssertTrue(waitUntil(timeout: 10) {
                self.hasVisibleFrame(episode) && episode.isHittable
                    && (self.element("videoEpisode-\(fixture.secondEpisodeID)").value as? String) == "Selected"
            }, "The selected episode must remain visible in \(appearance) appearance.")
            try assertStandardPhoneCollectionHeader(in: collection,
                                                     phase: "auto-positioned episode in \(appearance) appearance",
                                                     requiresVisibleHeader: true)
            try attachFrame(named: "Standard iPhone \(appearance) selected episode")
        }

        try chooseDeviceSetting("deviceAppearancePicker", option: "Light")
        try openFocusedEpisode(appearance: "Light")
        try selectDestination("Account", screen: "accountScreen")
        try chooseDeviceSetting("deviceAppearancePicker", option: "Dark")
        try openFocusedEpisode(appearance: "Dark")

        guard let requestMovieKey = fixture.requestMovieKey else {
            throw XCTSkip("Generate the linked Requests movie fixture.")
        }
        try selectDestination("Requests", screen: "requestsScreen")
        let requestMovie = element("requestDetails-\(requestMovieKey)")
        try waitForHittable(requestMovie, timeout: 25, named: "linked Requests movie")
        try activate(requestMovie)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "linked Request detail")
        try openRequestVault(expectedScreen: "detailScreen", named: "linked Request movie")
        try performStandardPhoneLeadingEdgeBack(from: element("detailScreen"),
                                                 to: element("requestDetailScreen"),
                                                 named: "Requests vault movie")
        try closeRequestDetailToRequests()

        try selectDestination("Account", screen: "accountScreen")
        try chooseDeviceSetting("deviceAppearancePicker", option: originalAppearance)
        completed = true
    }

    func journeyEpisodeControlsAndPlayback() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.secondEpisodeID, seconds: 8)
        let otherEpisode = try XCTUnwrap(fixture.secondSeasonEpisodeID)
        try await api.seedProgress(identity: identity, itemID: otherEpisode, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        let resume = element("resumeItem-\(fixture.secondEpisodeID)")
        try revealDetailElement(resume, in: element("homeScreen"), named: "saved episode on Home", requiresHittable: false)
        resume.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.3)).tap()
        let collection = element("videoCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "show collection")
        let selector = element("videoSeasonSelector")
        try revealDetailElement(selector, in: collection, named: "season controls", requiresHittable: false)
        let viewport = try applicationViewport()
        let rightEdge = viewport.maxX - (UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome ? 20 : 96)
        for control in [selector, element("downloadEpisodesButton")] {
            XCTAssertGreaterThanOrEqual(control.frame.minX, viewport.minX + 19)
            XCTAssertLessThanOrEqual(control.frame.maxX, rightEdge + 1)
        }
        XCTAssertFalse(selector.frame.intersects(element("downloadEpisodesButton").frame))
        XCTAssertFalse(element("selectedEpisodePlayButton").exists)
        XCTAssertFalse(element("collectionEpisodeTitle").exists)
        let savedEpisode = element("videoEpisode-\(fixture.secondEpisodeID)")
        try revealDetailElement(savedEpisode, in: collection, named: "saved episode thumbnail", requiresHittable: false)
        XCTAssertTrue(accessibilityText(savedEpisode).contains("in progress"))
        XCTAssertFalse(accessibilityText(savedEpisode).contains("Unwatched"))
        let aboutHeading = element("episodeAboutHeading")
        try waitForExistence(aboutHeading, timeout: 10, named: "episode About heading")
        let episodesHeading = application!.staticTexts["Episodes"].firstMatch
        for text in [episodesHeading, selector, aboutHeading] {
            XCTAssertEqual(savedEpisode.frame.minX, text.frame.minX, accuracy: 2,
                           "The artwork card's outer edge must align with text above and below it.")
        }
        try attachFrame(named: "Episode thumbnail aligned with About and no duplicate title or Resume block")
        savedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let resumeChoice = element("resumePlaybackChoice")
        try waitForExistence(resumeChoice, timeout: 15, named: "saved episode resume choice")
        try activate(resumeChoice.buttons["Resume"])
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "thumbnail resumes saved episode")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 25) { $0 >= 8 }
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.secondEpisodeID, minimum: 8)
        try closePlayer()
        try revealDetailElement(selector, in: collection, named: "season controls after playback", requiresHittable: false)
        selector.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try activate(menuAction("Season 2"))
        let thumbnail = element("videoEpisode-\(otherEpisode)")
        try revealDetailElement(thumbnail, in: collection, named: "other season episode thumbnail", requiresHittable: false)
        XCTAssertTrue(accessibilityText(thumbnail).contains("Unwatched"))
        try attachFrame(named: "Unwatched episode with gold thumbnail corner")
        XCTAssertTrue(element("collectionHero").frame.minY < viewport.minY)
        thumbnail.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "thumbnail starts tapped season episode")
        _ = try await waitForServerProgress(api, identity: identity, itemID: otherEpisode, minimum: 1)
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 2 }
        try attachPlaybackFrame(named: "Tapped thumbnail plays the exact episode")
        try closePlayer()
        completed = true
    }

    func journeyEpisodeSelectionReturnsToShowContext() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 0)
        try await api.seedProgress(identity: identity, itemID: fixture.secondEpisodeID, seconds: 0)

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectTourLibraryCategory("tv", screen: "libraryScreen")
        try activate(element("libraryItem-\(fixture.showID)"))
        let collection = element("videoCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "show collection")

        let seriesAboutFirstParagraph = try XCTUnwrap(
            fixture.seriesAboutFirstParagraph,
            "Generate the rich synthetic series About fixture."
        )
        let seriesAboutFinalParagraph = try XCTUnwrap(
            fixture.seriesAboutFinalParagraph,
            "Generate the rich synthetic series About fixture."
        )
        let seriesAboutSource = try XCTUnwrap(
            fixture.seriesAboutSource,
            "Generate the rich synthetic series About fixture."
        )
        let secondEpisodeAbout = try XCTUnwrap(
            fixture.secondEpisodeAbout,
            "Generate the synthetic second-episode About fixture."
        )
        let secondEpisodeAboutSource = try XCTUnwrap(
            fixture.secondEpisodeAboutSource,
            "Generate the synthetic second-episode About fixture."
        )
        let secondEpisodeAboutNote = try XCTUnwrap(
            fixture.secondEpisodeAboutNote,
            "Generate the synthetic second-episode About fixture."
        )
        let episodeAbout = try XCTUnwrap(
            fixture.episodeAbout,
            "Generate the synthetic first-episode About fixture."
        )
        let episodeAboutSource = try XCTUnwrap(
            fixture.episodeAboutSource,
            "Generate the synthetic first-episode About fixture."
        )

        func aboutElement(_ identifier: String, in about: XCUIElement) -> XCUIElement {
            about.descendants(matching: .any).matching(identifier: identifier).firstMatch
        }

        func aboutText(_ text: String, in about: XCUIElement) -> XCUIElement {
            about.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label == %@", text))
                .firstMatch
        }

        try selectCollectionSeason(fixture.seasonID)
        let selectedEpisode = element("videoEpisode-\(fixture.secondEpisodeID)")
        try revealDetailElement(selectedEpisode, in: collection, named: "episode to open in its show", requiresHittable: true)
        try activate(selectedEpisode)
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "selected episode playback")
        try closePlayer()

        try waitForExistence(collection, timeout: 20, named: "parent show after selecting an episode")
        XCTAssertFalse(element("detailScreen").exists,
                       "Selecting an episode must keep browsing in the parent show rather than open a standalone detail.")
        XCTAssertFalse(element("collectionEpisodeTitle").exists)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.hasVisibleFrame(selectedEpisode) && selectedEpisode.isHittable
        }, "Playback return must retain the selected episode card.")
        XCTAssertTrue(waitUntil(timeout: 10) { (self.element("videoSeasonSelector").value as? String) == "Season 1" },
                      "The episode's season must remain selected in the show.")
        XCTAssertTrue(waitUntil(timeout: 10) { (self.element("videoEpisode-\(fixture.secondEpisodeID)").value as? String) == "Selected" },
                      "The episode selected before navigation must remain selected in its show.")
        if usesStandardPhonePushedChrome {
            try assertStandardPhoneCollectionHeader(in: collection, phase: "auto-positioned selected episode", requiresVisibleHeader: true)
        }
        try attachFrame(named: "iOS selected episode in parent show")

        let selectedEpisodeAbout = element("episodeAbout")
        try revealDetailElement(selectedEpisodeAbout, in: collection,
                                named: "selected episode About", requiresHittable: false)
        let selectedEpisodeSource = aboutElement("mediaAboutSource", in: selectedEpisodeAbout)
        try waitForExistence(selectedEpisodeSource, timeout: 15, named: "selected episode About source")
        XCTAssertEqual(selectedEpisodeSource.label, "Source: \(secondEpisodeAboutSource)",
                       "The selected episode must expose its own source as a plain caption.")
        let selectedEpisodeText = aboutText(secondEpisodeAbout, in: selectedEpisodeAbout)
        try waitForExistence(selectedEpisodeText, timeout: 15, named: "selected episode About text")
        let selectedEpisodeNotes = aboutElement("mediaAboutNotes", in: selectedEpisodeAbout)
        try revealDetailElement(selectedEpisodeNotes, in: collection,
                                named: "selected episode About notes", requiresHittable: true)
        let selectedEpisodeNote = aboutText(secondEpisodeAboutNote, in: selectedEpisodeAbout)
        XCTAssertFalse(hasVisibleFrame(selectedEpisodeNote),
                       "Episode About notes must start collapsed.")
        try activate(selectedEpisodeNotes)
        try waitForExistence(selectedEpisodeNote, timeout: 15, named: "expanded selected episode About note")

        let seriesAbout = element("seriesAbout")
        XCTAssertFalse(hasVisibleFrame(seriesAbout),
                       "The series About section must start collapsed behind its disclosure.")
        let seriesAboutToggle = element("seriesAboutToggle")
        try revealDetailElement(seriesAboutToggle, in: collection,
                                named: "series About disclosure", requiresHittable: true)
        try activate(seriesAboutToggle)
        try waitForExistence(seriesAbout, timeout: 15, named: "expanded series About")
        let seriesSource = aboutElement("mediaAboutSource", in: seriesAbout)
        try waitForExistence(seriesSource, timeout: 15, named: "series About source")
        XCTAssertEqual(seriesSource.label, "Source: \(seriesAboutSource)")
        let showMore = aboutElement("mediaAboutShowMore", in: seriesAbout)
        try revealDetailElement(showMore, in: collection,
                                named: "series About Show more", requiresHittable: true)
        try activate(showMore)
        let seriesFirstText = aboutText(seriesAboutFirstParagraph, in: seriesAbout)
        try waitForExistence(seriesFirstText, timeout: 15, named: "expanded series About opening synopsis")
        let seriesFinalText = aboutText(seriesAboutFinalParagraph, in: seriesAbout)
        try waitForExistence(seriesFinalText, timeout: 15, named: "expanded series About synopsis")
        let showLess = aboutElement("mediaAboutShowLess", in: seriesAbout)
        try revealDetailElement(showLess, in: collection,
                                named: "series About Show less", requiresHittable: true)
        try activate(showLess)
        try waitForExistence(aboutElement("mediaAboutShowMore", in: seriesAbout), timeout: 15,
                             named: "collapsed series About synopsis")

        let otherSeasonID = try XCTUnwrap(fixture.secondSeasonID, "Generate the second synthetic show season.")
        XCTAssertFalse(element("seasonArtwork-\(fixture.seasonID)").exists,
                       "The season dropdown must replace season poster cards.")
        try selectCollectionSeason(otherSeasonID, named: "Season 2")
        try attachFrame(named: "Season dropdown showing second season episodes")
        let otherEpisodeID = try XCTUnwrap(fixture.secondSeasonEpisodeID, "Generate the second synthetic season episode.")
        let otherEpisode = element("videoEpisode-\(otherEpisodeID)")
        try revealDetailElement(otherEpisode, in: collection, named: "episode in another show season", requiresHittable: true)
        XCTAssertTrue(waitUntil(timeout: 10) {
            (self.element("videoSeasonSelector").value as? String) == "Season 2"
                && (self.element("videoEpisode-\(otherEpisodeID)").value as? String) == "Selected"
        }, "Switching seasons must retain the show and expose that season's selected episode.")

        try selectCollectionSeason(fixture.seasonID)

        let siblingEpisode = element("videoEpisode-\(fixture.episodeID)")
        try revealDetailElement(siblingEpisode, in: collection, named: "another episode in the selected season", requiresHittable: true)
        try activate(siblingEpisode)
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "sibling episode playback")
        try closePlayer()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (self.element("videoEpisode-\(fixture.episodeID)").value as? String) == "Selected"
        }, "Selecting another episode must stay in the show and update its selected playback target.")

        let siblingEpisodeAbout = element("episodeAbout")
        try revealDetailElement(siblingEpisodeAbout, in: collection,
                                named: "newly selected episode About", requiresHittable: false)
        let siblingEpisodeText = aboutText(episodeAbout, in: siblingEpisodeAbout)
        try waitForExistence(siblingEpisodeText, timeout: 15, named: "newly selected episode About text")
        XCTAssertEqual(aboutElement("mediaAboutSource", in: siblingEpisodeAbout).label,
                       "Source: \(episodeAboutSource)",
                       "Selecting a different episode must replace its source instead of using the series overview.")
        XCTAssertFalse(aboutText(secondEpisodeAbout, in: siblingEpisodeAbout).exists,
                       "Selecting a different episode must replace the prior episode overview.")

        let play = element("playButton")
        try revealDetailElement(play, in: collection, named: "selected episode play", requiresHittable: true)
        try attachFrame(named: "Standard iPhone visible selected episode Play")
        try activate(play)
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "selected episode playback")
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.episodeID, minimum: 1)
        try activate(element("videoControlsSurface"))
        try closePlayer()

        // A horizontal drag that begins within the episode rail must stay in
        // the collection. This distinguishes rail interaction from the native
        // leading-edge Back gesture exercised below.
        let railStart = selectedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.82, dy: 0.5))
        let railEnd = selectedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.18, dy: 0.5))
        railStart.press(forDuration: 0.05, thenDragTo: railEnd)
        try waitForExistence(collection, timeout: 15, named: "show collection after horizontal episode-rail drag")

        if usesStandardPhonePushedChrome {
            collection.swipeUp()
            try assertStandardPhoneCollectionHeader(in: collection, phase: "scrolled selected episode", requiresVisibleHeader: false)

            // Start at the physical leading edge, in the hero gap above either
            // horizontal rail. This verifies native interactive-pop rather than a
            // generic swipe that could scroll season or episode content instead.
            // First cancel a short drag: it must leave the collection in place.
            let cancelledEdgeStart = application!.windows.firstMatch.coordinate(
                withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24)
            )
            let cancelledEdgeEnd = application!.windows.firstMatch.coordinate(
                withNormalizedOffset: CGVector(dx: 0.07, dy: 0.24)
            )
            cancelledEdgeStart.press(forDuration: 0.05, thenDragTo: cancelledEdgeEnd)
            try waitForExistence(collection, timeout: 15, named: "collection after cancelled leading-edge Back")

            try performStandardPhoneLeadingEdgeBack(
                from: collection,
                to: element("libraryScreen"),
                named: "focused show collection"
            )
        } else {
            try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
            try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library after show Back")
        }
        XCTAssertFalse(element("detailScreen").exists,
                       "Leading-edge Back from the focused show collection must not open an episode detail.")
        completed = true
    }

    func selectCollectionSeason(_ seasonID: String, named seasonName: String = "Season 1") throws {
        let selector = element("videoSeasonSelector")
        try revealDetailElement(selector, in: element("videoCollectionScreen"),
                                named: "season dropdown", requiresHittable: true)
        try activate(selector)
        let identifiedOption = element("videoSeason-\(seasonID)")
        let option = identifiedOption.exists ? identifiedOption : application!.buttons[seasonName]
        try waitForHittable(option, timeout: 10, named: "season menu option")
        try attachFrame(named: "Season dropdown options")
        try activate(option)
        XCTAssertTrue(waitUntil(timeout: 15) { selector.isEnabled },
                      "Selecting a season must finish loading its episode carousel.")
    }

    private func assertStandardPhoneCollectionHeader(
        in collection: XCUIElement,
        phase: String,
        requiresVisibleHeader: Bool
    ) throws {
        try waitForExistence(collection, timeout: 15, named: "show collection during \(phase)")

        let legacyBack = collection.descendants(matching: .button)
            .matching(identifier: "BackButton")
            .allElementsBoundByIndex
        XCTAssertFalse(legacyBack.contains(where: hasVisibleFrame),
                       "Standard-phone \(phase) must use native leading-edge Back instead of a visible custom Back button.")
        XCTAssertFalse(hasVisibleFrame(element("homeMasthead")),
                       "Standard-phone \(phase) must not layer the Home masthead over collection content.")

        let protectedTop = usesStandardPhonePushedChrome
            ? try standardPhoneNavigationViewport().minY
            : try protectedStatusBarBottom()
        let headerItems = [element("playButton"), element("videoWatchlistButton")]
        for item in headerItems {
            try waitForExistence(item, timeout: 15, named: "collection header item during \(phase)")
        }
        if requiresVisibleHeader {
            XCTAssertTrue(element("videoSeasonSelector").exists, "The show must retain season navigation after playback.")
        }
        let visibleHeaderItems = headerItems.filter {
            hasVisibleFrame($0) && $0.frame.minY >= protectedTop - 1
        }
        for (index, item) in visibleHeaderItems.enumerated() {
            for peer in visibleHeaderItems.dropFirst(index + 1) {
                XCTAssertTrue(item.frame.intersection(peer.frame).isEmpty,
                              "Collection hero controls must not overlap during \(phase): \(item.identifier) and \(peer.identifier).")
            }
        }
    }

    func protectedStatusBarBottom() throws -> CGFloat {
        let statusBar = application!.statusBars.firstMatch
        return statusBar.exists && !statusBar.frame.isEmpty
            ? statusBar.frame.maxY
            : try applicationViewport().minY
    }

    var usesStandardPhonePushedChrome: Bool {
        UIDevice.current.userInterfaceIdiom == .phone
            && min(UIScreen.main.bounds.width, UIScreen.main.bounds.height) < 450
    }

    func standardPhoneNavigationViewport() throws -> CGRect {
        let window = try applicationViewport()
        let customHeader = element("standardPhoneNavigationHeader")
        var header: XCUIElement?
        _ = waitUntil(timeout: 10) {
            if self.hasVisibleFrame(customHeader) {
                header = customHeader
            } else {
                header = self.application!.navigationBars.allElementsBoundByIndex
                    .first(where: self.hasVisibleFrame)
            }
            return header != nil
        }
        guard let header else {
            throw ProofFailure.ui("The standard-phone navigation chrome had no usable visible frame.")
        }

        let top = max(window.minY + 2, header.frame.maxY + 2)
        let visibleTabs = ["Home", "Library", "Requests", "Account"]
            .map { destinationControl($0) }
            .filter { hasVisibleFrame($0) }
        let tabTop = visibleTabs.map(\.frame.minY).min() ?? window.maxY
        let bottom = min(window.maxY - 2, tabTop - 2)
        guard bottom - top >= 44 else {
            throw ProofFailure.ui("The standard-phone content viewport is empty: header=\(header.frame), tabs=\(visibleTabs.map(\.frame)).")
        }
        return CGRect(x: window.minX + 2, y: top, width: window.width - 4, height: bottom - top)
    }

    func assertStandardPhonePushedChrome(_ screen: XCUIElement, named name: String) throws {
        try waitForExistence(screen, timeout: 20, named: name)
        let legacyBack = screen.descendants(matching: .button)
            .matching(identifier: "BackButton")
            .allElementsBoundByIndex
        XCTAssertFalse(legacyBack.contains(where: hasVisibleFrame),
                       "\(name) must use native leading-edge Back instead of a visible custom Back button.")
        XCTAssertFalse(hasVisibleFrame(element("homeMasthead")),
                       "\(name) must not layer the Home masthead over pushed content.")
    }

    func performStandardPhoneEdgeGesture(named name: String) throws {
        let edgeStart = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24)
        )
        let edgeEnd = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.62, dy: 0.24)
        )
        edgeStart.press(forDuration: 0.05, thenDragTo: edgeEnd)
        _ = try applicationViewport()
    }

    func performStandardPhoneLeadingEdgeBack(
        from screen: XCUIElement,
        to destination: XCUIElement,
        named name: String
    ) throws {
        try assertStandardPhonePushedChrome(screen, named: name)
        try performStandardPhoneEdgeGesture(named: name)
        XCTAssertTrue(waitUntil(timeout: 15) { !self.hasVisibleFrame(screen) },
                      "A completed leading-edge swipe must pop \(name) exactly one level.")
        try waitForExistence(destination, timeout: 15, named: "destination after \(name) leading-edge Back")
    }

    #endif

    func journeyNativeEpisodeSkipActionsAndNextEpisode() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 0)
        try await api.seedProgress(identity: identity, itemID: fixture.secondEpisodeID, seconds: 0)

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        #if os(iOS)
        try selectTourLibraryCategory("tv", screen: "libraryScreen")
        #else
        try selectTVLibraryCategory("tv", screen: "libraryScreen")
        #endif
        let show = element("libraryItem-\(fixture.showID)")
        #if os(tvOS)
        try activate(try waitForPerformanceControl(show, timeout: 20, named: "show for playback actions"))
        #else
        try activate(show)
        #endif
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons for playback actions")
        #if os(tvOS)
        try waitForExistence(element("videoCollectionItem-\(fixture.seasonID)"), timeout: 20,
                             named: "inline show season selector")
        let firstEpisode = element("videoCollectionItem-\(fixture.episodeID)")
        #else
        try selectCollectionSeason(fixture.seasonID)
        let firstEpisode = element("videoEpisode-\(fixture.episodeID)")
        #endif
        try waitForHittable(firstEpisode, timeout: 20, named: "first episode for playback actions")
        try activate(firstEpisode)
        #if os(iOS)
        let initialResumeChoice = element("resumePlaybackChoice")
        try waitForExistence(initialResumeChoice, timeout: 15, named: "initial saved episode choice")
        try activate(initialResumeChoice.buttons.matching(NSPredicate(format: "label == %@", "Resume")).firstMatch)
        #endif
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "episode thumbnail playback")
        let time = element("playbackTime")
        let intro = element("skipIntroButton")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 1 }
        XCTAssertFalse(element("nextEpisodeButton").exists,
                       "The contextual Next episode action should wait for credits or completion.")

        #if os(tvOS)
        let transport = element("videoPlayPauseButton")
        // The Remote play/pause command is the public pause affordance and
        // always reveals the TV playback chrome for this already-playing episode.
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(transport, timeout: 8, named: "paused episode transport")
        XCTAssertEqual(transport.label, "Play", "The episode transport must be paused before contextual skip actions are exercised.")
        XCTAssertFalse(element("closePlayerButton").exists,
                       "tvOS video controls must rely on the Remote Back action instead of an on-screen close button.")
        let settings = element("videoSettingsButton")
        let mute = element("videoMuteButton")
        try waitForHittable(settings, timeout: 8, named: "TV video settings control")
        try waitForHittable(mute, timeout: 8, named: "TV video mute control")
        let settingsFrame = settings.frame
        let muteFrame = mute.frame
        let viewport = application!.windows.firstMatch.frame
        XCTAssertEqual(settingsFrame.midY, muteFrame.midY, accuracy: 2)
        XCTAssertGreaterThanOrEqual(settingsFrame.minX, viewport.minX)
        XCTAssertLessThanOrEqual(muteFrame.maxX, viewport.maxX,
                                 "The remaining TV playback controls must stay inside the video viewport.")
        XCTAssertGreaterThan(intro.frame.minY, viewport.midY,
                             "Contextual episode actions belong near the playback timeline.")
        try attachPlaybackFrame(named: "TV playback — aligned edge toolbar and contextual action")
        #else
        try tapVideoTransportAfterChromeHides(named: "episode pause control")
        try waitForExistence(element("nextEpisodeTransportButton"), timeout: 8, named: "manual next episode transport")
        #endif
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5 },
                       "Skip actions must be available without resuming the episode.")

        try waitForHittable(intro, timeout: 20, named: "authoritative intro skip action")
        try activate(intro)
        XCTAssertTrue(waitUntil(timeout: 15) {
            let currentTime = Double(time.value as? String ?? "") ?? 0
            return currentTime >= fixture.episodeIntroEndSeconds && currentTime < fixture.episodeIntroEndSeconds + 5
        }, "Skip intro must seek to the authoritative segment endpoint.")
        let afterIntro = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? afterIntro) > afterIntro + 0.5 },
                       "Skip intro must preserve the paused state.")

        let forward = element("videoSeekForwardButton")
        for expectedTime in stride(from: fixture.episodeIntroEndSeconds + 10,
                                   through: fixture.episodeCreditsStartSeconds + 5, by: 10) {
            try waitForHittable(forward, timeout: 8, named: "forward seek to credits")
            try activate(forward)
            _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= expectedTime - 1 }
        }
        XCTAssertTrue(waitUntil(timeout: 10) {
            let currentTime = Double(time.value as? String ?? "") ?? 0
            return currentTime >= fixture.episodeCreditsStartSeconds && currentTime < fixture.episodeCreditsStartSeconds + 8
        }, "The paused seek controls must enter the authoritative credits range; actual=\(time.value ?? "unavailable").")
        let credits = element("skipCreditsButton")
        try waitForHittable(credits, timeout: 15, named: "authoritative credits skip action")
        #if os(iOS)
        let next = element("nextEpisodeTransportButton")
        XCTAssertFalse(hasVisibleFrame(element("nextEpisodeButton")), "Visible transport controls already provide Next episode.")
        #else
        let next = element("nextEpisodeButton")
        #endif
        try waitForHittable(next, timeout: 15, named: "next episode action")
        try attachPlaybackFrame(named: "Episode intro, credits, and next actions")
        try activate(credits)
        XCTAssertTrue(disappears(credits, timeout: 15), "Skipping credits must leave the recorded credits range.")
        try waitForHittable(next, timeout: 15, named: "next episode action after completion")
        try activate(next)
        let playerChrome = element("videoPlaybackChrome")
        let nextTitle = playerChrome.staticTexts[fixture.secondEpisodeTitle]
        let nextTitleVisible = waitUntil(timeout: 20) { self.hasVisibleFrame(nextTitle) }
        #if LUMINA_UI_TESTING
        if !nextTitleVisible {
            print("EPISODE_NEXT_AFTER_TAP \(element("videoGestureDiagnostics").value ?? "absent")")
        }
        #endif
        XCTAssertTrue(nextTitleVisible,
                      "Next episode playback must expose its new title in visible player chrome.")
        XCTAssertTrue(waitUntil(timeout: 15) { (Double(time.value as? String ?? "") ?? 90) < 5 },
                      "Next episode must begin at its own start position.")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 1 }
        XCTAssertFalse(element("videoActionError").exists, "The next episode action must keep native playback usable.")
        try closePlayer()
        completed = true
    }

}
