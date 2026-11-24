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
    func journeyTVWelcomeShowcaseAndConnectionHelp() throws {
        #if os(tvOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        let ready = element("connectionShowcaseReady")
        try activate(element("connectionHelpButton"))
        try waitForExistence(element("connectionHelpSheet"), timeout: 8, named: "connection help")
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("connectionHelpDoneButton").hasFocus })
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("connectionHelpSheet"), timeout: 8))
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("connectionHelpButton").hasFocus },
                      "Back from help must restore its trigger.")
        try revealTVManualConnection()
        XCTAssertTrue(waitUntil(timeout: 15) { ready.exists && ((ready.value as? String)?.hasPrefix("asset") == true) },
                      "The welcome screen must begin with bundled offline artwork.")
        try enter(fixture.serverURL, into: element("serverURLField"))
        XCTAssertTrue(waitUntil(timeout: 25) {
            guard let value = ready.value as? String else { return false }
            return !value.hasPrefix("asset") && value != "unavailable"
        }, "The public release showcase should replace bundled artwork before sign-in.")
        XCTAssertFalse(element("connectionArtworkPauseButton").exists)
        try attachFrame(named: "TV welcome — public upcoming poster and compact sign-in")
        let previousArtwork = ready.value as? String
        XCTAssertTrue(waitUntil(timeout: 15) { (ready.value as? String) != previousArtwork },
                      "Welcome artwork must continue rotating without a pause control.")
        try enter(fixture.username, into: element("usernameField"))
        try enter(fixture.password, into: element("passwordField"))
        try activate(element("connectButton"))
        try waitForExistence(element("homeScreen"), timeout: 40, named: "Home after cinematic sign-in")
        completed = true
        #else
        throw XCTSkip("The cinematic welcome workflow applies to tvOS.")
        #endif
    }

#if os(tvOS)
    func journeyTVEpisodeCardActivation() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try launchAndResetSession()
        try signIn(using: fixture)
        let resume = element("resumeItem-\(fixture.episodeID)")
        for _ in 0..<8 where !resume.exists { XCUIRemote.shared.press(.down) }
        try activate(resume)
        try waitForExistence(element("collectionEpisodeRail"), timeout: 25, named: "show episode rail")
        let inProgress = element("videoCollectionItem-\(fixture.episodeID)")
        try waitForExistence(inProgress, timeout: 15, named: "in-progress episode card")
        XCTAssertTrue(inProgress.label.contains("remaining"), "The in-progress episode card should announce time remaining.")
        let episode = element("videoCollectionItem-\(fixture.secondEpisodeID)")
        XCTAssertTrue(focus(episode, timeout: 20))
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(fixture.secondEpisodeTitle)
        }, "Focus must preview the episode before Select.")
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "playback directly from episode Select")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 3 }
        XCUIRemote.shared.press(.playPause)
        let chrome = element("Media Controls")
        try waitForExistence(chrome, timeout: 10, named: "native TV video transport")
        let pausedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(self.element("playbackTime").value as? String ?? "") ?? pausedAt) > pausedAt + 1
        }, "The remote must pause the selected episode through native AVKit transport.")
        XCTAssertFalse(element("closePlayerButton").exists, "TV video controls must use remote Back instead of a close icon.")
        try attachPlaybackFrame(named: "TV remote-only playback controls without a close icon")
        try closePlayer()
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.secondEpisodeID)
        try waitForExistence(episode, timeout: 15, named: "episode after playback Back")
        XCTAssertTrue(waitUntil(timeout: 10) { episode.hasFocus }, "Playback Back must restore the episode card.")
        completed = true
    }

    private func tvHeroOrigin() -> Double {
        Double(element("tvHeroBounds").value as? String ?? "") ?? .infinity
    }

    func journeyTVHeroReturn() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try launchAndResetSession()
        try signIn(using: fixture)
        let resume = element("resumeItem-\(fixture.episodeID)")
        for _ in 0..<8 where !resume.exists { XCUIRemote.shared.press(.down) }
        try activate(resume)
        try waitForExistence(element("collectionEpisodeRail"), timeout: 25, named: "show episode rail")
        let seasons = element("collectionSeasonSelector").buttons
        XCTAssertEqual(seasons.count, 27, "The reproduction needs a horizontally scrolled season selector.")
        try activate(seasons.element(boundBy: 0))
        for _ in 0..<26 { XCUIRemote.shared.press(.right) }
        XCTAssertTrue(waitUntil(timeout: 8) { seasons.element(boundBy: 26).hasFocus })
        XCUIRemote.shared.press(.select)
        let episode = element("collectionEpisodeRail").buttons.element(boundBy: 4)
        try waitForExistence(episode, timeout: 20, named: "late episode in season 27")
        XCTAssertTrue(focus(episode, timeout: 30))
        XCTAssertLessThan(tvHeroOrigin(), -1, "The episode rail must actually scroll the hero away.")
        for _ in 0..<5 { XCUIRemote.shared.press(.up) }
        try attachFrame(named: "TV show after Up from late season")
        print("TV_HERO_RETURN origin=\(tvHeroOrigin()) focus=\(focusedElement()?.identifier ?? "none")")
        XCTAssertTrue(waitUntil(timeout: 8) {
            return abs(self.tvHeroOrigin()) < 2
        }, "Up from the episode rail must restore the whole hero, including its title.")
        try attachFrame(named: "TV Up returns the complete show hero")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after show Back")
        XCTAssertTrue(focus(resume, timeout: 20))
        for _ in 0..<5 { XCUIRemote.shared.press(.up) }
        XCTAssertTrue(waitUntil(timeout: 8) {
            abs(self.tvHeroOrigin()) < 2
        }, "Up from Home shelves must restore the full hero artwork, not only its bottom controls.")
        try attachFrame(named: "TV Up returns the complete Home hero")
        completed = true
    }

    func journeyTVBrowseHeroReturns() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try launchAndResetSession()
        try signIn(using: fixture)
        let movie = element("resumeItem-\(fixture.directItemID)")
        for _ in 0..<8 where !movie.exists { XCUIRemote.shared.press(.down) }
        try activate(movie)
        try waitForExistence(element("tvHeroBounds"), timeout: 20, named: "movie hero")
        XCTAssertTrue(waitUntil(timeout: 8) { abs(self.tvHeroOrigin()) < 2 })
        try attachFrame(named: "TV movie opens with its full hero")
        try activate(element("playButton"))
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "movie playback from restored hero")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 12 }
        try attachPlaybackFrame(named: "TV movie hero retains decoded playback")
        try closePlayer()
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home before Music")
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "Music library album")
        XCTAssertTrue(album.hasFocus)
        try attachFrame(named: "TV Music opens directly to the album grid")
        try selectDestination("Requests", screen: "requestsScreen")
        let heading = element("requestsScreen").staticTexts["Requests"].firstMatch
        try waitForExistence(heading, timeout: 20, named: "Requests heading")
        let initialHeadingY = heading.frame.minY
        let poster = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "requestPoster-")).firstMatch
        for _ in 0..<8 where !poster.exists { XCUIRemote.shared.press(.down) }
        try waitForExistence(poster, timeout: 20, named: "Requests shelf poster")
        XCTAssertTrue(focus(poster, timeout: 25))
        XCTAssertTrue(!heading.exists || heading.frame.minY < initialHeadingY - 1)
        for _ in 0..<8 { XCUIRemote.shared.press(.up) }
        XCTAssertTrue(waitUntil(timeout: 8) { heading.exists && abs(heading.frame.minY - initialHeadingY) < 2 },
                      "Requests Up must restore the top controls and hero.")
        try attachFrame(named: "TV Requests top restored from shelves")
        completed = true
    }

    func journeyTVHomeArtworkAndCachedReturns(nextUp: Bool) async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID,
                                   seconds: nextUp ? 0 : 12, completed: nextUp)
        _ = try await api.saveHomeLayout(identity: identity, shelves: nil)
        try launchAndResetSession()
        try await performanceQuietPhase("login_form")
        try signIn(using: fixture)
        let targetID = nextUp ? fixture.secondEpisodeID : fixture.episodeID
        let resume = element("\(nextUp ? "nextUpItem" : "resumeItem")-\(targetID)")
        for _ in 0..<10 where !resume.exists { XCUIRemote.shared.press(.down) }
        try waitForExistence(resume, timeout: 25, named: "Continue Watching episode")
        XCTAssertTrue(focus(resume, timeout: 25), "Continue Watching must accept remote focus.")
        let artwork = resume.descendants(matching: .any).matching(identifier: "tvArtwork-\(targetID)").firstMatch
        try waitForExistence(artwork, timeout: 10, named: "Home episode artwork")
        let source = try XCTUnwrap(artwork.value as? String)
        if nextUp {
            XCTAssertTrue(source.hasSuffix("/Images/Primary"))
            XCTAssertNotEqual(source, "/Items/\(targetID)/Images/Primary",
                              "Next Up uses series or season poster artwork.")
            XCTAssertLessThan(artwork.frame.width, artwork.frame.height)
        } else {
            XCTAssertTrue(source == "/Items/\(targetID)/Images/Primary" || source.hasSuffix("/Images/Backdrop/0"),
                          "Continue Watching uses the episode still or a backdrop.")
            XCTAssertGreaterThan(artwork.frame.width, artwork.frame.height)
        }
        try attachFrame(named: nextUp ? "TV Next Up uses series poster artwork" : "TV Continue Watching uses wide episode artwork")
        for cycle in 0..<(nextUp ? 1 : 3) {
            let phase = cycle == 0 ? "tv_cold_show_detail" : "tv_warm_show_detail_\(cycle)"
            let started = ContinuousClock.now
            emitPerformanceMarker(phase: phase, event: "begin", started: started)
            try activate(resume)
            try waitForExistence(element("videoCollectionScreen"), timeout: 25, named: "show from Continue Watching")
            try waitForExistence(element("collectionSeasonSelector"), timeout: 20, named: "cached show seasons")
            XCTAssertTrue(waitUntil(timeout: 15) { self.element("playButton").isEnabled })
            emitPerformanceMarker(phase: phase, event: "ready", started: started)
            try await Task.sleep(for: .seconds(4))
            emitPerformanceMarker(phase: phase, event: "end", started: started)
            if cycle == 1 { try attachFrame(named: "TV warm detail return keeps episodes and playback ready") }
            XCUIRemote.shared.press(.menu)
            try waitForExistence(element("homeScreen"), timeout: 20, named: "Home after cached show Back")
        }
        let next = resume
        XCTAssertTrue(focus(next, timeout: 25), "The shelf must remain focusable after detail returns.")
        let navigationStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "tv_shelf_remote_navigation", event: "begin", started: navigationStarted)
        emitPerformanceMarker(phase: "tv_shelf_remote_navigation", event: "ready", started: navigationStarted)
        for _ in 0..<8 { XCUIRemote.shared.press(.right); XCUIRemote.shared.press(.left) }
        emitPerformanceMarker(phase: "tv_shelf_remote_navigation", event: "end", started: navigationStarted)
        XCTAssertTrue(focus(next, timeout: 20))
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show from the Home shelf")
        let play = element("playButton")
        XCTAssertTrue(focus(play, timeout: 20))
        try attachFrame(named: "Episode detail with focused playback action")
        XCUIRemote.shared.press(.select)
        if element("resumePlaybackChoice").waitForExistence(timeout: 3) {
            try activate(element("resumePlaybackChoice").buttons["Resume"])
        }
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "episode playback after cached navigation")
        let startedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= startedAt + 2 }
        try attachPlaybackFrame(named: "Cached TV browsing retains decoded episode playback")
        try closePlayer()
        if !nextUp {
            try waitForExistence(play, timeout: 15, named: "episode detail after playback")
            XCUIRemote.shared.press(.menu)
            XCTAssertTrue(waitUntil(timeout: 12) { resume.hasFocus },
                          "Back must restore the original Continue Watching card.")
        }
        completed = true
    }

    func journeyTVLeadingCarouselEntry() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        for id in [fixture.directItemID, fixture.convertedItemID, fixture.matrixItemID] {
            try await api.seedProgress(identity: identity, itemID: id, seconds: 12)
        }
        _ = try await api.saveHomeLayout(identity: identity, shelves: [
            ProofHomeShelf(id: "continue", visible: true),
            ProofHomeShelf(id: "new_in_library", visible: true),
            ProofHomeShelf(id: "next_up", visible: false),
        ])
        let rows = try await api.homeRows(identity: identity)
        let resumeRow = try XCTUnwrap(rows.first { $0.shelf_id == "continue" })
        let recentRow = try XCTUnwrap(rows.first { $0.shelf_id == "new_in_library" })
        XCTAssertGreaterThanOrEqual(resumeRow.items.count, 3)
        XCTAssertGreaterThanOrEqual(recentRow.items.count, 3)
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        try waitForExistence(element("featuredWatchlistButton"), timeout: 25, named: "hero's right-hand action")
        XCTAssertTrue(focus(element("featuredWatchlistButton"), timeout: 20))
        let firstResume = element("resumeItem-\(resumeRow.items[0].Id)")
        try waitForExistence(firstResume, timeout: 20, named: "first Continue Watching card")
        try attachFrame(named: "Compact TV Home with Continue Watching visible")
        XCUIRemote.shared.press(.down)
        guard waitUntil(timeout: 8, condition: { firstResume.hasFocus }) else {
            throw ProofFailure.ui("Down from the hero must enter the leftmost Continue watching card, regardless of the hero action's horizontal position.")
        }
        XCUIRemote.shared.press(.right)
        XCUIRemote.shared.press(.right)
        let visited = element("resumeItem-\(resumeRow.items[2].Id)")
        XCTAssertTrue(waitUntil(timeout: 8) { visited.hasFocus }, "Horizontal navigation must remain within the row.")
        let recentPrefix = "homeRow-\(recentRow.id)"
        let firstRecent = element("\(recentPrefix)-\(recentRow.items[0].Id)")
        for _ in 0..<4 {
            XCUIRemote.shared.press(.down)
            if recentRow.items.contains(where: { element("\(recentPrefix)-\($0.Id)").hasFocus }) { break }
        }
        XCTAssertTrue(waitUntil(timeout: 8) { firstRecent.hasFocus }, "Down from a right-hand card must enter a new carousel at its leftmost card.")
        try attachFrame(named: "New Home carousel enters at its left edge")
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 8) { visited.hasFocus }, "Returning to a visited row must restore the member's last card.")
        try activate(visited)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "movie from the visited carousel")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 12) { visited.hasFocus }, "Back from detail must restore the same carousel card.")
        let movie = element("\(recentPrefix)-\(fixture.directItemID)")
        try activate(movie)
        let play = element("playButton")
        try waitForExistence(play, timeout: 20, named: "Play on the Home movie's details")
        let facts = element("movieDetailFacts")
        XCTAssertTrue(waitUntil(timeout: 15) {
            facts.label.contains("180p") && facts.label.localizedCaseInsensitiveContains("AAC")
        }, "Movie detail should show the fixture's video resolution and audio format.")
        XCTAssertTrue(focus(play, timeout: 10))
        try attachFrame(named: "Movie detail shows source formats and focused Resume")
        XCUIRemote.shared.press(.select)
        if element("resumePlaybackChoice").waitForExistence(timeout: 3) {
            try activate(element("resumePlaybackChoice").buttons["Resume"])
        }
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "Home movie playback")
        let startedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= startedAt + 2 }
        try closePlayer()
        try waitForExistence(play, timeout: 15, named: "movie details after leaving playback")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 12) { movie.hasFocus },
                      "Back from playback and detail must restore the exact Home poster, even when the movie also appears in Continue Watching.")
        try attachFrame(named: "Home restores the focused movie poster after playback")
        completed = true
    }

    func journeyTVHomeNavigationPreservesSelection() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setNavigationFixture("on")
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: true)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.convertedItemID, included: false)

        try launchAndResetSession()
        try activate(element("tvPairingStartButton"))
        let qr = element("tvPairingQRCode")
        try waitForExistence(qr, timeout: 15, named: "TV pairing QR")
        let image = try XCTUnwrap(qr.screenshot().image.cgImage)
        let barcode = try readTVPairingBarcode(image)
        let link = try XCTUnwrap(barcode.messageString.flatMap { URL(string: $0) })
        let approval = try await TVPairingHandoff(link: link).requestApproval(serverURL: origin)
        let approved = try await api.devicePairing("Approve", identity: identity, body: ["code": approval.code])
        XCTAssertEqual(approved["status"] as? String, "approved")
        try waitForExistence(element("homeScreen"), timeout: 30, named: "TV Home browse screen")
        XCTAssertFalse(element("homeBrowse-movies").exists,
                       "Home must leave media categories in the Library hub.")
        XCTAssertFalse(element("homeBrowse-watchlist").exists)
        XCTAssertFalse(element("homeBrowse-all").exists)
        XCTAssertTrue(application!.buttons["homeNavigationButton"].exists,
                      "The sunrise must be a remote-focusable Home button.")
        let home = element("homeNavigationButton")
        XCTAssertEqual(home.value as? String, "Selected")
        let library = element("libraryNavigationButton")
        let moviesStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "tv_movies_entry", event: "begin", started: moviesStarted)
        try activate(library)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library opens directly to Movies")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "Movies poster wall")
        emitPerformanceMarker(phase: "tv_movies_entry", event: "ready", started: moviesStarted)
        XCTAssertFalse(element("libraryHubScreen").exists)
        XCTAssertNotEqual(home.value as? String, "Selected")
        XCTAssertEqual(library.value as? String, "Selected")
        try attachFrame(named: "Library opens directly to Movies")
        let unsaved = element("libraryItem-\(fixture.convertedItemID)")
        try waitForExistence(unsaved, timeout: 25, named: "unsaved movie in the unfiltered library")
        try openTVLibraryBrowse()
        try waitForExistence(element("videoCollectionFilterButton"), timeout: 15, named: "Movies Collections shortcut")
        try activate(element("videoLibraryWatchlistButton"))
        try dismissTVLibraryBrowse()
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "saved movie")
        XCTAssertTrue(disappears(unsaved, timeout: 20), "Watchlist filters out unsaved movies.")

        let requestsStarted = ContinuousClock.now
        emitPerformanceMarker(phase: "tv_requests_entry", event: "begin", started: requestsStarted)
        try activate(element("requestsButton"))
        try waitForExistence(element("requestsScreen"), timeout: 20, named: "Requests from the Movies library")
        emitPerformanceMarker(phase: "tv_requests_entry", event: "ready", started: requestsStarted)
        let requestHero = application!.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "requestFeatured-")).firstMatch
        try waitForExistence(requestHero, timeout: 15, named: "Requests featured title")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Requests Back restores Movies")
        try openTVLibraryBrowse()
        XCTAssertEqual(element("videoLibraryWatchlistButton").value as? String, "Selected",
                       "Returning from Requests must preserve the Watchlist scope.")
        try dismissTVLibraryBrowse()
        let savedMovie = element("libraryItem-\(fixture.directItemID)")
        try activate(savedMovie)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "saved movie detail")
        let bookmark = element("videoWatchlistButton")
        XCTAssertTrue(waitUntil(timeout: 15) { bookmark.value as? String == "In watchlist" })
        try activate(bookmark)
        XCTAssertTrue(waitUntil(timeout: 15) { bookmark.value as? String == "Not in watchlist" })
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("Browse all titles"), timeout: 20, named: "empty Movies Watchlist")
        try attachFrame(named: "Empty Movies Watchlist with a way back to all titles")
        try activate(element("Browse all titles"))
        try waitForExistence(unsaved, timeout: 25, named: "All titles after exiting empty Watchlist")
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: true)
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "TV Home after Library Back")
        try waitForExistence(element("featuredPlayButton"), timeout: 15, named: "warm Home hero")
        try activate(library)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Movies before switching categories")
        let shows = element("homeBrowse-tv")
        XCTAssertEqual(shows.label, "Shows")
        try activate(shows)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Shows library")
        try openTVLibraryBrowse()
        try waitForExistence(element("videoLibraryWatchlistButton"), timeout: 15, named: "Shows Watchlist shortcut")
        try activate(element("videoCollectionFilterButton"))
        try waitForExistence(element("collectionFilter-any"), timeout: 15, named: "Shows Collections picker")
        XCUIRemote.shared.press(.menu)
        guard disappears(element("collectionFilter-any"), timeout: 10) else {
            throw ProofFailure.ui("Back must dismiss Collections before leaving the category.")
        }
        try dismissTVLibraryBrowse()
        try activate(element("homeNavigationButton"))
        let episodeResume = element("resumeItem-\(fixture.episodeID)")
        try waitForExistence(episodeResume, timeout: 20, named: "episode resume on TV Home")
        XCTAssertTrue(episodeResume.label.contains(fixture.showTitle) && episodeResume.label.contains("S1 · E1"))

        try activate(element("requestsButton"))
        try waitForExistence(requestHero, timeout: 15, named: "featured title on returning to Requests")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after returning from Requests")

        try openDeviceSettings()
        try selectDeviceSettingsCategory("library")
        try assertDeviceSetting("deviceDefaultLibraryPicker", contains: "Movies")
        try activateDeviceControl("deviceDefaultLibraryPicker")
        try activate(element("deviceLibraryOption-tv"))
        try assertDeviceSetting("deviceDefaultLibraryPicker", contains: "Shows")
        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "Home after app relaunch")
        try activate(library)
        try waitForExistence(element("libraryItem-\(fixture.showID)"), timeout: 25, named: "saved TV Shows landing preference")
        try attachFrame(named: "Library opens directly to the saved TV Shows category")
        try selectTVLibraryCategory("music", screen: "audioScreen")
        _ = try revealMusicAlbum(fixture, named: "Music library album")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "TV Home after Music Back")

        try activate(element("homeSearchButton"))
        try waitForExistence(element("homeSearchScreen"), timeout: 15, named: "TV Home search")
        try waitForExistence(element("homeSearchField"), timeout: 15, named: "TV Home search field")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("homeSearchScreen"), timeout: 15),
                      "Back must dismiss Home search before leaving the browsing context.")
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("homeSearchButton").hasFocus },
                      "Closing Home search must return focus to Search.")
        completed = true
    }

    func journeyTVHeaderHeroFocus(reducedMotion: Bool = false) throws {
        #if os(tvOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession(extraLaunchArguments: reducedMotion ? ["--ui-testing-reduce-motion"] : [])
        try signIn(using: fixture)
        let play = element("featuredPlayButton")
        let home = element("homeNavigationButton")
        try waitForExistence(play, timeout: 30, named: "Home hero action")
        XCTAssertTrue(focus(home, timeout: 20), "The Home tab must be reachable from the header.")
        if reducedMotion {
            XCTAssertEqual(home.frame.width, 64, accuracy: 1, "Reduce Motion must keep focused navigation at its resting size.")
            try attachFrame(named: "Reduced Motion header focus without zoom")
        }
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { play.hasFocus }, "Down from Home must enter the hero before the shelves.")
        try attachFrame(named: "Home tab enters the hero")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 5) { self.element("featuredWatchlistButton").hasFocus },
                      "Right from Play must focus the Home hero's Watchlist action.")
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 5) { home.hasFocus }, "Up from the hero must restore the Home tab.")
        XCTAssertTrue(application!.buttons["homeNavigationButton"].exists, "The sunrise mark is the Home control.")
        XCTAssertFalse(application!.staticTexts["LUMINA"].exists, "The TV header uses the sunrise mark without a wordmark.")
        let requests = element("requestsButton")
        try waitForExistence(requests, timeout: 20, named: "Requests in the Home header")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 5) { self.element("libraryNavigationButton").hasFocus },
                      "Right from Home must focus Library.")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 5) { requests.hasFocus },
                      "Right from Library on Home must focus Requests.")
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("requestsScreen"), timeout: 15, named: "Requests after Select from Home")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 5) { self.element("requestsDiscoverButton").hasFocus },
                      "Down from the Requests header must enter Discover.")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after Requests Back")

        try activate(element("homeSearchButton"))
        try waitForExistence(element("homeSearchField"), timeout: 15, named: "Home search field")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("homeSearchButton").hasFocus },
                      "Closing Home search must restore focus to Search.")
        if reducedMotion {
            XCTAssertEqual(element("homeSearchButton").frame.width, 64, accuracy: 1,
                           "Reduce Motion must keep the focused Search control at its resting size.")
            try attachFrame(named: "Reduced Motion Search focus outline without zoom")
        }

        try openDeviceSettings()
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after Settings Back")

        let library = element("libraryNavigationButton")
        try activate(library)
        try waitForExistence(element("libraryScreen"), timeout: 25, named: "Movies Library")
        if reducedMotion {
            let card = element("libraryItem-\(fixture.directItemID)")
            try waitForExistence(card, timeout: 20, named: "Library artwork")
            XCTAssertTrue(focus(library, timeout: 15))
            let restingFrame = card.frame
            XCTAssertTrue(focus(card, timeout: 20))
            XCTAssertEqual(card.frame.width, restingFrame.width, accuracy: 1, "Reduce Motion must keep focused artwork at its resting width.")
            XCTAssertEqual(card.frame.height, restingFrame.height, accuracy: 1, "Reduce Motion must keep focused artwork at its resting height.")
            try attachFrame(named: "Reduced Motion artwork focus outline without zoom")
        }
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after Movies Back")
        completed = true
        #endif
    }

    func journeyTVNavigationArtworkAndVideoCategories() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let requestShowKey = try XCTUnwrap(fixture.requestUnavailableShowKey, "Generate the unlinked Requests show fixture.")
        let requestShowTitle = try XCTUnwrap(fixture.requestUnavailableShowTitle, "Generate the unlinked Requests show fixture.")
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 0)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("featuredPlayButton"), timeout: 25, named: "Home hero")
        let firstShelfHeading = application!.staticTexts["Continue watching"].firstMatch
        try waitForExistence(firstShelfHeading, timeout: 15, named: "first shelf below Home hero")
        let library = element("libraryNavigationButton")
        try waitForExistence(library, timeout: 15, named: "Library tab")
        XCTAssertFalse(element("homeBrowse-movies").exists,
                       "The Home header must not duplicate media category navigation.")
        XCTAssertFalse(element("homeTab").exists)
        XCTAssertFalse(element("libraryTab").exists)
        XCTAssertFalse(element("requestsTab").exists)
        XCTAssertFalse(element("homeVideoButton").exists)
        XCTAssertFalse(element("homeAudioButton").exists)
        let featuredTitle = element("featuredTitleButton")
        try waitForExistence(featuredTitle, timeout: 20, named: "Home featured title")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "The Home featured title must be remote-focusable.")
        let featuredChoices = application!.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "featuredChoice-")
        )
        let featuredCounter = application!.descendants(matching: .any).matching(
            NSPredicate(format: "label BEGINSWITH %@", "Featured titles,")
        ).firstMatch
        try waitForExistence(featuredCounter, timeout: 10, named: "Home featured page counter")
        func selectedFeaturedChoiceID() -> String? {
            for index in 0..<featuredChoices.count {
                let choice = featuredChoices.element(boundBy: index)
                if choice.exists, (choice.value as? String) == "Selected" { return choice.identifier }
            }
            return nil
        }
        func featuredPage() -> (current: Int, total: Int)? {
            let values = featuredCounter.label.components(separatedBy: CharacterSet.decimalDigits.inverted)
                .compactMap(Int.init)
            guard values.count == 2, values[0] > 0, values[1] > 0 else { return nil }
            return (values[0], values[1])
        }
        guard let featureBeforeBrowseID = selectedFeaturedChoiceID(),
              let featureBeforeBrowsePage = featuredPage() else {
            throw ProofFailure.ui("The focused Home feature did not expose a selected choice and page counter.")
        }
        XCTAssertGreaterThan(featureBeforeBrowsePage.total, 1,
                             "The TV Home fixture must provide adjacent featured titles to test remote browsing.")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 10) {
            guard let selectedID = selectedFeaturedChoiceID(), let page = featuredPage() else { return false }
            return selectedID != featureBeforeBrowseID
                && page.total == featureBeforeBrowsePage.total
                && page.current == (featureBeforeBrowsePage.current % featureBeforeBrowsePage.total) + 1
        }, "Right on a focused Home title must choose the next featured release.")
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("featuredTitleButton").hasFocus },
                      "Browsing a featured title must retain remote focus on that title.")
        let featureAfterBrowseID = try XCTUnwrap(selectedFeaturedChoiceID(), "Right must select a featured choice.")
        let featureAfterBrowsePage = try XCTUnwrap(featuredPage(), "Right must update the featured page counter.")
        XCUIRemote.shared.press(.left)
        XCTAssertTrue(waitUntil(timeout: 10) {
            guard let page = featuredPage() else { return false }
            return selectedFeaturedChoiceID() == featureBeforeBrowseID && page == featureBeforeBrowsePage
        }, "Left on a focused Home title must restore the preceding featured release.")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 10) {
            guard let page = featuredPage() else { return false }
            return selectedFeaturedChoiceID() == featureAfterBrowseID && page == featureAfterBrowsePage
        }, "A subsequent right move must return to the same next featured release.")
        let featureAfterBrowseItemID = String(featureAfterBrowseID.dropFirst("featuredChoice-".count))
        let featuredMovieIDs = Set([fixture.directItemID, fixture.convertedItemID, fixture.matrixItemID])
        var featuredEpisodeIDs = Set([fixture.episodeID, fixture.secondEpisodeID])
        if let secondSeasonEpisodeID = fixture.secondSeasonEpisodeID {
            featuredEpisodeIDs.insert(secondSeasonEpisodeID)
        }
        func assertFeaturedDestination() throws {
            if featuredEpisodeIDs.contains(featureAfterBrowseItemID) {
                try waitForExistence(element("videoCollectionScreen"), timeout: 20,
                                     named: "featured episode collection")
                let selectedEpisode = element("videoCollectionItem-\(featureAfterBrowseItemID)")
                try waitForExistence(selectedEpisode, timeout: 15, named: "selected featured episode")
                XCTAssertEqual(selectedEpisode.value as? String, "Selected",
                               "Opening a featured episode must preserve its selected collection item.")
            } else if featuredMovieIDs.contains(featureAfterBrowseItemID) {
                try waitForExistence(element("detailScreen"), timeout: 20, named: "featured film details")
            } else {
                throw ProofFailure.ui("The selected TV featured choice did not map to a known fixture destination: \(featureAfterBrowseID).")
            }
        }
        XCUIRemote.shared.press(.select)
        try assertFeaturedDestination()
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after featured title Back")
        XCTAssertTrue(waitUntil(timeout: 10) {
            guard let page = featuredPage() else { return false }
            return selectedFeaturedChoiceID() == featureAfterBrowseID && page == featureAfterBrowsePage
        }, "Back from a browsed featured title must restore its Home selection.")
        try attachFrame(named: "TV Home with stable primary navigation and immersive artwork")
        try activate(featuredTitle)
        try assertFeaturedDestination()
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Return to Home")
        try selectDestination("Video", screen: "libraryScreen")
        try selectDestination("Audio", screen: "audioScreen")
        try selectDestination("Video", screen: "libraryScreen")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Back from focused Library tab returns Home")
        try selectDestination("Video", screen: "libraryScreen")
        try exerciseTVLibraryAlphabet(using: fixture)
        try attachFrame(named: "TV Video Movies category")
        try activate(element("libraryItem-\(fixture.directItemID)"))
        try waitForExistence(element("detailScreen"), timeout: 15, named: "Video title details")
        let detailPlayButton = element("playButton")
        try waitForExistence(detailPlayButton, timeout: 15, named: "TV detail playback action")
        XCTAssertTrue(waitUntil(timeout: 10) { detailPlayButton.isEnabled },
                      "The TV detail Play action must be enabled before the remote Play command is sent.")
        XCTAssertTrue(focus(detailPlayButton, timeout: 15),
                      "The TV detail Play action must receive remote focus before the remote Play command is sent.")
        try attachFrame(named: "TV film artwork and metadata")
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("playbackScreen"), timeout: 20, named: "Remote Play starts the title")
        let remoteStartedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= remoteStartedAt + 1 }
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 15, named: "Player Back returns to its title")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("detailScreen"), timeout: 15), "Back must pop the Video title detail.")
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Detail Back returns to Video, not Home")
        try selectTVLibraryCategory("tv", screen: "libraryScreen")
        try waitForExistence(element("libraryItem-\(fixture.showID)"), timeout: 25, named: "Shows library")
        try attachFrame(named: "TV Library Shows category")
        XCTAssertFalse(element("homeBrowse-anime").exists, "Unconfigured Anime must be absent from the Library category bar.")
        XCTAssertFalse(element("homeBrowse-books").exists,
                       "Books remain intentionally unavailable on tvOS.")
        try activate(element("homeNavigationButton"))
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Library Home tab returns to Home")
        try waitForExistence(featuredTitle, timeout: 15, named: "restored Home content after Library Back")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "Back to Home must restore a focusable featured title.")
        try selectDestination("Account", screen: "accountScreen")
        try waitForExistence(element("deviceSettingsScreen"), timeout: 15, named: "TV Account settings")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Account Back returns to Home")
        XCTAssertFalse(element("homeBrowse-watchlist").exists,
                       "Watchlist must stay out of the Home masthead.")
        try selectDestination("Account", screen: "accountScreen")
        try activateDeviceControl("devicePlaybackQualityPicker")
        _ = try devicePickerOption("Original (best available)")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("devicePlaybackQualityPicker"), timeout: 15,
                             named: "Device Settings after nested Back")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15,
                             named: "Device Settings root Back returns to Home")
        try selectDestination("Requests", screen: "requestsScreen")
        XCTAssertEqual(element("requestsButton").label, "Requests",
                       "Requests must be a labeled primary navigation tab, not an unlabeled utility icon.")
        try enter(requestShowTitle, into: openTVRequestsSearch())
        let requestFeature = try revealRequestPoster(requestShowKey, named: "unlinked Requests show")
        try attachFrame(named: "TV Requests discovery")
        try activate(requestFeature)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "TV request details")
        try attachFrame(named: "TV Requests title details")
        let requestConfirm = try waitForPerformanceControl(
            element("requestConfirmButton"),
            timeout: 20,
            named: "TV request confirmation"
        )
        try activate(requestConfirm)
        try waitForExistence(element("requestSubmittedStatus"), timeout: 15, named: "TV submitted request status")
        let requests = try await api.requestRecords(identity: identity)
        XCTAssertTrue(requests.contains { $0.key == requestShowKey },
                      "The confirmed TV request did not reach the server.")
        try attachFrame(named: "TV Requests confirmed through the server")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("requestDetailScreen"), timeout: 15), "Back must pop the request detail.")
        try waitForExistence(element("requestsScreen"), timeout: 15, named: "Request detail Back returns to Requests")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Requests Back returns to Home")
        try waitForExistence(featuredTitle, timeout: 15, named: "restored Home content after Requests Back")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "Requests Back must restore a focusable featured title.")
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("audioSearchField"), timeout: 15, named: "Audio search")
        try waitForExistence(element("audioItem-\(fixture.audioAlbumID)"), timeout: 20, named: "Audio album artwork")
        try attachFrame(named: "TV Audio library")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Audio hub Back returns Home")
        try waitForExistence(featuredTitle, timeout: 15, named: "restored Home content after Audio Back")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "Audio Back must restore a focusable featured title.")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 8) {
            self.application!.state == .runningBackground || self.application!.state == .runningBackgroundSuspended
        }, "Back at Home must return to the Apple TV Home Screen.")
        completed = true
    }
    func journeyTVLibraryAlphabetSortAndJump() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        XCTAssertTrue(waitUntil(timeout: 25) {
            (self.element("libraryScreen").value as? String)?.contains("catalog=123 ") == true
        }, "TV must finish loading the full lightweight catalog without paging through its posters.")
        XCTAssertFalse(element("videoLoadMoreButton").exists)
        try exerciseTVLibraryAlphabet(using: fixture)
        XCTAssertTrue(waitUntil(timeout: 25) {
            (self.element("libraryScreen").value as? String)?.contains("catalog=123 ") == true
        }, "Returning to name sort must reload the full catalog.")
        completed = true
    }

    func journeyTVLargeMovieAndSeriesCatalogs() throws {
        let fixture = try loadFixture()
        guard fixture.largeVideoCatalog == true else {
            throw XCTSkip("Generate the 1,000-movie and 2,000-series proof fixture.")
        }
        try launchAndResetSession()
        try signIn(using: fixture)
        for (category, count) in [("movies", 1_000), ("tv", 2_000)] {
            let started = ContinuousClock.now
            emitPerformanceMarker(phase: "tv_scale_\(category)_load", event: "begin", started: started)
            try selectTVLibraryCategory(category, screen: "libraryScreen")
            let screen = element("libraryScreen")
            guard waitUntil(timeout: 60, condition: {
                (screen.value as? String)?.contains("catalog=\(count) loading=false") == true
            }) else {
                throw ProofFailure.ui("Expected \(count) \(category) posters, got \(screen.value ?? "none").")
            }
            emitPerformanceMarker(phase: "tv_scale_\(category)_load", event: "ready", started: started)
            let first = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-")).firstMatch
            try focusVideoGridItemFromCategories(first, named: "leading \(category) poster")
            try sweepTVPosterWall(phase: "tv_scale_\(category)_scroll")
        }

        let firstPoster = "libraryItem-\(fixture.showID)"
        for _ in 0..<12 { XCUIRemote.shared.press(.down) }
        guard let deepPoster = focusedElement()?.identifier, deepPoster.hasPrefix("libraryItem-"), deepPoster != firstPoster else {
            throw ProofFailure.ui("Expected a focused poster further down the series wall.")
        }
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "series details from the poster wall")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(waitUntil(timeout: 15) { self.focusedElement()?.identifier == deepPoster },
                      "Back must restore the exact series poster.")

        let alphabet = element("libraryAlphabetIndex")
        XCTAssertTrue(focus(alphabet, timeout: 15), "A–Z must be reachable from the scrolled wall.")
        XCTAssertEqual(alphabet.value as? String, "T", "The alphabet starts at the first series letter.")
        XCUIRemote.shared.press(.select)
        guard waitUntil(timeout: 15, condition: { self.focusedElement()?.identifier == firstPoster }) else {
            let focus = focusedElement()
            throw ProofFailure.ui("A–Z jump expected the first series, got \(focus?.identifier ?? "no focus") (\(focus?.label ?? "")), letter \(alphabet.value ?? "none").")
        }
    }

    func journeyTVLargeLibraryEntryTiming() throws {
        let fixture = try loadFixture()
        guard fixture.largeVideoCatalog == true else {
            throw XCTSkip("Generate the 1,000-movie and 2,000-series proof fixture.")
        }
        try launchAndResetSession(extraLaunchEnvironment: [
            "LUMINA_TV_ARTWORK_TIMING": "\(fixture.directItemID),\(fixture.showID)",
        ])
        try signIn(using: fixture)
        let loginSettled = ContinuousClock.now
        emitPerformanceMarker(phase: "login_form", event: "ready", started: loginSettled)
        try waitForExistence(element("homeScreen"), timeout: 30, named: "Home before Library entry timing")

        try measureTVLibraryEntry(phase: "tv_library_cold", itemID: fixture.directItemID)
        try measureTVLibraryCategory(phase: "tv_library_cold_tv_category", category: "tv",
                                     itemID: fixture.showID, expectedCatalogCount: 2_000)
        try measureTVLibraryCategory(phase: "tv_library_warm_movies_round_trip", category: "movies",
                                     itemID: fixture.directItemID, expectedCatalogCount: 1_000)

        try activate(element("homeNavigationButton"))
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home before warm Library entry")
        try measureTVLibraryEntry(phase: "tv_library_warm", itemID: fixture.directItemID)
        try sweepTVPosterWall(phase: "tv_library_warm_scroll")
    }

    private func measureTVLibraryEntry(phase: String, itemID: String) throws {
        let library = element("libraryNavigationButton")
        XCTAssertTrue(focus(library, timeout: 15), "The Library action must accept remote focus before entry timing.")

        let poster = element("libraryItem-\(itemID)")
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: "\(phase)_first_poster", event: "begin", started: started)
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library screen")
        try waitForExistence(poster, timeout: 30, named: "first Movie poster")
        emitPerformanceMarker(phase: "\(phase)_first_poster", event: "ready", started: started)

        let screen = element("libraryScreen")
        emitPerformanceMarker(phase: "\(phase)_full_catalog", event: "begin", started: started)
        guard waitUntil(timeout: 60, condition: {
            (screen.value as? String)?.contains("catalog=1000 loading=false") == true
        }) else {
            throw ProofFailure.ui("Expected the full Movie catalog after Library entry, got \(screen.value ?? "none").")
        }
        emitPerformanceMarker(phase: "\(phase)_full_catalog", event: "ready", started: started)

        emitPerformanceMarker(phase: "\(phase)_first_focus", event: "begin", started: started)
        try focusVideoGridItemFromCategories(poster, named: "first Movie poster")
        emitPerformanceMarker(phase: "\(phase)_first_focus", event: "ready", started: started)
    }

    private func measureTVLibraryCategory(phase: String, category: String, itemID: String,
                                          expectedCatalogCount: Int) throws {
        let categoryControl = element("homeBrowse-\(category)")
        XCTAssertTrue(focus(categoryControl, timeout: 15), "The \(category) category must accept remote focus before entry timing.")

        let poster = element("libraryItem-\(itemID)")
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: "\(phase)_first_poster", event: "begin", started: started)
        XCUIRemote.shared.press(.select)
        try waitForExistence(poster, timeout: 30, named: "first \(category) poster")
        emitPerformanceMarker(phase: "\(phase)_first_poster", event: "ready", started: started)

        let screen = element("libraryScreen")
        emitPerformanceMarker(phase: "\(phase)_full_catalog", event: "begin", started: started)
        guard waitUntil(timeout: 60, condition: {
            (screen.value as? String)?.contains("catalog=\(expectedCatalogCount) loading=false") == true
        }) else {
            throw ProofFailure.ui("Expected the full \(category) catalog after category entry, got \(screen.value ?? "none").")
        }
        emitPerformanceMarker(phase: "\(phase)_full_catalog", event: "ready", started: started)

        emitPerformanceMarker(phase: "\(phase)_first_focus", event: "begin", started: started)
        try focusVideoGridItemFromCategories(poster, named: "first \(category) poster")
        emitPerformanceMarker(phase: "\(phase)_first_focus", event: "ready", started: started)
    }

    private func sweepTVPosterWall(phase: String) throws {
        guard let firstFocusedPoster = focusedElement()?.identifier,
              firstFocusedPoster.hasPrefix("libraryItem-") else {
            throw ProofFailure.ui("The poster wall did not have focus before scrolling.")
        }
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", started: started)
        for _ in 0..<8 { XCUIRemote.shared.press(.down) }
        XCTAssertTrue(waitUntil(timeout: 8) {
            guard let focused = self.focusedElement()?.identifier else { return false }
            return focused.hasPrefix("libraryItem-") && focused != firstFocusedPoster
        }, "Repeated Down presses must move focus through the poster wall.")
        for _ in 0..<8 { XCUIRemote.shared.press(.up) }
        XCTAssertTrue(waitUntil(timeout: 8) { self.focusedElement()?.identifier == firstFocusedPoster },
                      "The reverse sweep must restore the same poster.")
        emitPerformanceMarker(phase: phase, event: "ready", started: started)
    }

    private func exerciseTVLibraryAlphabet(using fixture: ProofFixture) throws {
        let firstPoster = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(firstPoster, timeout: 20, named: "Movies artwork")
        let movies = element("homeBrowse-movies")
        let browse = element("videoBrowseButton")
        try waitForExistence(movies, timeout: 15, named: "Movies category")
        try waitForExistence(browse, timeout: 15, named: "Filters action")
        XCTAssertEqual(browse.label, "Filters")
        XCTAssertEqual(movies.frame.midY, browse.frame.midY, accuracy: 4,
                       "The compact Library category and Browse action must share one pinned bar.")
        let viewport = try applicationViewport()
        XCTAssertLessThan(firstPoster.frame.minY, viewport.minY + viewport.height * 0.25,
                          "The first poster must begin in the upper quarter of the compact Library screen.")

        XCTAssertTrue(focus(movies, timeout: 15), "Movies must accept remote focus before entering the wall.")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 8) {
            self.focusedElement()?.identifier.hasPrefix("libraryItem-") == true
        }, "Down from Movies must enter the poster wall immediately.")
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 8) { self.focusedElement()?.identifier == movies.identifier },
                      "Up from the first poster must restore Movies.")

        XCTAssertTrue(focus(browse, timeout: 15), "Browse must accept remote focus before entering the wall.")
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 8) {
            self.focusedElement()?.identifier.hasPrefix("libraryItem-") == true
        }, "Down from Browse must enter the poster wall immediately.")
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 8) { self.focusedElement()?.identifier == browse.identifier },
                      "Up from the first poster must restore Browse.")

        try openTVLibraryBrowse()
        let sortPicker = element("videoSortPicker")
        let alphabet = element("libraryAlphabetIndex")
        try waitForExistence(sortPicker, timeout: 15, named: "Title A–Z sort control")
        let filters = element("videoFiltersButton")
        let search = element("searchField")
        let allTitles = element("videoAllTitlesButton")
        let collections = element("videoCollectionFilterButton")
        try waitForExistence(allTitles, timeout: 10, named: "Browse all titles choice")
        try waitForExistence(filters, timeout: 10, named: "Browse filters choice")
        try waitForExistence(collections, timeout: 10, named: "Browse collections choice")
        try waitForExistence(search, timeout: 10, named: "Browse search field")
        for choice in [allTitles, element("videoLibraryWatchlistButton"), collections, sortPicker, filters, search] {
            XCTAssertTrue(focus(choice, timeout: 15), "\(choice.identifier) must be reachable inside Browse.")
        }
        XCTAssertTrue(element("videoBrowseSheet").exists, "Browse controls must be grouped in the Browse sheet.")
        try activate(filters)
        let unwatched = element("videoUnwatchedToggle")
        try waitForExistence(unwatched, timeout: 15, named: "Library filters")
        XCTAssertTrue(focus(unwatched, timeout: 15), "Filter options must accept remote focus.")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(unwatched, timeout: 10), "Back must close Filters before Browse.")
        try waitForExistence(sortPicker, timeout: 15, named: "Browse after Filters Back")
        XCTAssertTrue(focus(sortPicker, timeout: 15), "Title A–Z sort must be reachable in Browse.")
        try attachFrame(named: "TV compact Library Browse choices")
        try activate(sortPicker)
        try activate(menuAction("Recently added"))
        try dismissTVLibraryBrowse()
        XCTAssertTrue(disappears(alphabet, timeout: 15),
                      "A non-name sort chosen from Browse must disable the alphabet index.")

        try openTVLibraryBrowse()
        try activate(sortPicker)
        try activate(menuAction("Name"))
        try dismissTVLibraryBrowse()
        try waitForExistence(alphabet, timeout: 20, named: "alphabet index restored by name sort")
        XCTAssertGreaterThan(alphabet.frame.minX, try applicationViewport().maxX - 200,
                             "The alphabet index must stay pinned to the physical right edge of the TV viewport.")
        XCTAssertTrue(focus(alphabet, timeout: 15), "The alphabet index must be remote-focusable.")
        try attachFrame(named: "TV title sort and right alphabet rail")
        let firstAlphabetLetter = accessibilityText(alphabet)
        XCUIRemote.shared.press(.down)
        XCTAssertTrue(waitUntil(timeout: 8) {
            self.accessibilityText(self.element("libraryAlphabetIndex")) != firstAlphabetLetter
        }, "Down on the alphabet index must preview its next available letter.")
        XCUIRemote.shared.press(.left)
        XCTAssertTrue(waitUntil(timeout: 8) {
            self.focusedElement()?.identifier.hasPrefix("libraryItem-") == true
        }, "Left from the alphabet index must return focus to the visible library grid.")
        XCTAssertTrue(focus(alphabet, timeout: 15), "The alphabet index must be reachable again from the grid.")
        XCUIRemote.shared.press(.select)
        XCTAssertTrue(waitUntil(timeout: 20) {
            self.focusedElement()?.identifier.hasPrefix("libraryItem-") == true
        }, "Selecting an alphabet letter must jump within the wall and focus its first matching title.")
        XCTAssertTrue((element("libraryScreen").value as? String)?.contains("catalog=123 ") == true,
                      "An alphabet jump must preserve all preloaded titles.")
    }

    func journeyTVBackRestoresLibraryHubFocus() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        let movie = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(movie, timeout: 25, named: "Video title before nested Back")
        try activate(movie)
        try waitForExistence(element("detailScreen"), timeout: 15, named: "Video title detail")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "title Back returns to Video")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Library tab Back returns Home")
        completed = true
    }

    func journeyTVBackDuringPlaybackPreparation() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")

        let directTitle = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(directTitle, timeout: 25, named: "direct-video title")
        try activate(directTitle)
        try waitForExistence(element("detailScreen"), timeout: 15, named: "direct-video detail")
        try activate(element("playButton"))
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("playButton").label.contains("Preparing") },
                      "Detail playback must enter preparation before Back is exercised.")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("detailScreen"), timeout: 15),
                      "Back during detail preparation must return to the Video library.")
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Video library after cancelling detail preparation")
        try await Task.sleep(for: .seconds(4))
        XCTAssertTrue(hasVisibleFrame(element("libraryScreen")))
        XCTAssertFalse(element("playbackScreen").exists,
                       "A delayed detail preparation must not present playback after its screen has been dismissed.")

        try selectTVLibraryCategory("tv", screen: "libraryScreen")
        let show = element("libraryItem-\(fixture.showID)")
        try waitForExistence(show, timeout: 25, named: "show collection")
        try activate(show)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show collection detail")
        try activate(element("playButton"))
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("playButton").label.contains("Preparing") },
                      "Collection Play next must enter preparation before Back is exercised.")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoCollectionScreen"), timeout: 15),
                      "Back during collection preparation must return to the Video library.")
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Video library after cancelling collection preparation")
        try await Task.sleep(for: .seconds(4))
        XCTAssertTrue(hasVisibleFrame(element("libraryScreen")))
        XCTAssertFalse(element("playbackScreen").exists,
                       "A delayed collection Play next preparation must not present playback after its collection has been dismissed.")
        completed = true
    }

    func openTVPlaybackSettings() throws {
        let settings = application!.cells.matching(identifier: "Playback settings").firstMatch
        if !settings.exists { XCUIRemote.shared.press(.up) }
        XCUIRemote.shared.press(.up)
        try waitForExistence(settings, timeout: 8, named: "Lumina settings in native AVKit transport")
        try activate(settings)
        try waitForExistence(element("videoSettingsPanel"), timeout: 8, named: "Lumina playback settings")
    }

    func journeyTVHiddenNativeControlsReturnToSettings() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
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
        let time = try startPlayback(using: "playButton")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 2 }
        XCUIRemote.shared.press(.playPause)
        let paused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertLessThan(paused, 10, "The navigation check must remain inside the recorded intro.")
        XCUIRemote.shared.press(.menu)
        try await Task.sleep(for: .seconds(1)) // Let AVKit finish hiding its transport.
        XCUIRemote.shared.press(.select)
        let settings = application!.cells.matching(identifier: "Playback settings").firstMatch
        XCTAssertTrue(waitUntil(timeout: 8) { settings.isHittable },
                      "Select must reveal usable native controls before settings navigation.")
        try openTVPlaybackSettings()
        try waitForExistence(element("videoSettingsPanel"), timeout: 8, named: "settings after hiding native controls during intro")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 8))
        try closePlayer()
        completed = true
    }

    func journeyTVRemoteStartsVideoFromDetails() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("featuredTitleButton"), timeout: 25, named: "Home featured title")
        try activate(element("featuredTitleButton"))
        guard waitUntil(timeout: 25, condition: {
            self.element("detailScreen").exists || self.element("videoCollectionScreen").exists
        }) else {
            throw ProofFailure.ui("Clicking the focused Home title must open its details.")
        }
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after title selection")
        try openLibraryItem(id: fixture.directItemID)
        try waitForExistence(element("playButton"), timeout: 20, named: "video detail Play action")
        try waitForExistence(element("heroRottenTomatoesRating-\(fixture.directItemID)"), timeout: 15, named: "movie source ratings")
        XCTAssertEqual(element("heroIMDbRating-\(fixture.directItemID)").label, "IMDb: 8.2 out of 10")
        XCTAssertEqual(element("heroRottenTomatoesRating-\(fixture.directItemID)").label, "Rotten Tomatoes: 91.5 percent, Fresh")
        XCTAssertEqual(element("heroMetacriticRating-\(fixture.directItemID)").label, "Metacritic: 73 out of 100, Favorable")
        let play = element("playButton")
        guard waitUntil(timeout: 10, condition: { play.isEnabled }), focus(play, timeout: 20) else {
            throw ProofFailure.ui("The movie Play action must be enabled and reachable with directional remote input.")
        }
        try attachFrame(named: "TV artwork-first movie detail with source ratings")
        XCUIRemote.shared.press(.select)
        if element("resumePlaybackChoice").waitForExistence(timeout: 3) {
            try activate(element("resumePlaybackChoice").buttons["Resume"])
        }
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "video started by clicking focused Play")
        let startedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= startedAt + 3 }
        try attachPlaybackFrame(named: "Video started from detail with Siri Remote")

        XCUIRemote.shared.press(.playPause)
        let paused = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(self.element("playbackTime").value as? String ?? "") ?? paused) > paused + 1
        }, "Native AVKit pause must stop the real video clock.")
        let hierarchy = XCTAttachment(string: application!.debugDescription)
        hierarchy.name = "Native AVKit paused transport hierarchy"
        hierarchy.lifetime = .keepAlways
        add(hierarchy)
        try attachPlaybackFrame(named: "Native AVKit paused transport")
        try openTVPlaybackSettings()
        try waitForExistence(element("videoSettingsPanel"), timeout: 8, named: "Lumina settings from AVKit")
        try activate(element("videoSettingsTab-video"))
        try activate(element("videoFitFillButton"))
        XCTAssertEqual(element("videoFitFillButton").value as? String, "Selected")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 8))
        XCTAssertTrue(element("playbackScreen").exists, "Back from settings must retain the player.")
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(self.element("playbackTime").value as? String ?? "") ?? paused) > paused + 1
        }, "Closing settings must preserve native pause intent.")
        // Native transport must keep handling repeated skips after it becomes visible.
        XCUIRemote.shared.press(.playPause)
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= paused + 2 }
        try openTVPlaybackSettings()
        try activate(element("videoSettingsDoneButton"))
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= paused + 4 }
        if element("Media Controls").exists { XCUIRemote.shared.press(.menu) }
        let beforeSkip = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCUIRemote.shared.press(.right)
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 8) { $0 >= beforeSkip + 8 }
        XCUIRemote.shared.press(.right)
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 8) { $0 >= beforeSkip + 18 }
        XCUIRemote.shared.press(.playPause)
        let beforeScrub = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCUIRemote.shared.press(.right)
        XCUIRemote.shared.press(.right)
        try attachPlaybackFrame(named: "Native AVKit scrub preview after repeated Right")
        XCUIRemote.shared.press(.select)
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 10) { $0 >= beforeScrub + 15 }
        XCUIRemote.shared.press(.playPause)
        let cancelPosition = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCUIRemote.shared.press(.left)
        XCUIRemote.shared.press(.left)
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(element("playbackScreen").exists, "Back must cancel scrubbing before leaving playback.")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 8) { abs($0 - cancelPosition) <= 2 }
        try attachPlaybackFrame(named: "Native AVKit scrub cancellation retains original position")
        try closePlayer()
        completed = true
    }

    func journeyTVEpisodeFocusedCollection() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        guard let secondSeasonID = fixture.secondSeasonID,
              let secondSeasonEpisodeID = fixture.secondSeasonEpisodeID,
              let secondSeasonEpisodeTitle = fixture.secondSeasonEpisodeTitle else {
            throw XCTSkip("The episode-focused collection journey requires a broker fixture with a second synthetic season.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        for episodeID in [fixture.episodeID, fixture.secondEpisodeID, secondSeasonEpisodeID] {
            try await api.seedProgress(identity: identity, itemID: episodeID, seconds: 0)
        }

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        try selectTVLibraryCategory("tv", screen: "libraryScreen")
        let show = element("libraryItem-\(fixture.showID)")
        try focusVideoGridItemFromCategories(show, named: "episode-focused collection")
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("videoCollectionScreen"), timeout: 25, named: "episode-focused collection")

        let heroTitle = element("collectionEpisodeTitle")
        let heroMetadata = element("collectionEpisodeMetadata")
        let heroSynopsis = element("collectionEpisodeSynopsis")
        let seasonSelector = element("collectionSeasonSelector")
        let firstEpisode = element("videoCollectionItem-\(fixture.episodeID)")
        let secondEpisode = element("videoCollectionItem-\(fixture.secondEpisodeID)")
        let secondSeason = element("videoCollectionItem-\(secondSeasonID)")

        try waitForExistence(heroTitle, timeout: 25, named: "selected episode hero title")
        try waitForExistence(heroMetadata, timeout: 15, named: "selected episode hero metadata")
        try waitForExistence(heroSynopsis, timeout: 15, named: "selected episode hero synopsis")
        try waitForExistence(seasonSelector, timeout: 15, named: "inline season selector")
        try waitForExistence(element("heroRottenTomatoesRating-\(fixture.showID)"), timeout: 15, named: "series source ratings")
        XCTAssertEqual(element("heroIMDbRating-\(fixture.showID)").label, "IMDb: 6.4 out of 10")
        XCTAssertEqual(element("heroRottenTomatoesRating-\(fixture.showID)").label, "Rotten Tomatoes: 0 percent, Rotten")
        XCTAssertEqual(element("heroMetacriticRating-\(fixture.showID)").label, "Metacritic: 39.9 out of 100, Unfavorable")
        XCTAssertTrue(accessibilityText(heroTitle).contains(fixture.episodeTitle),
                      "The initial hero must select the first current or next unwatched episode.")
        XCTAssertTrue(accessibilityText(heroTitle).contains("S1") && accessibilityText(heroTitle).contains("E1"),
                      "The initial hero title must expose season and episode numbers.")
        XCTAssertTrue(waitUntil(timeout: 10) {
            !self.accessibilityText(self.element("collectionEpisodeMetadata"))
                .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }, "The initial hero must expose the selected episode's runtime or source facts.")
        let expectedSynopsis = try XCTUnwrap(fixture.episodeAbout, "The fixture must provide its episode synopsis.")
        guard waitUntil(timeout: 10, condition: {
            self.accessibilityText(heroSynopsis).contains(expectedSynopsis)
        }) else {
            throw ProofFailure.ui("The initial hero must expose the selected episode synopsis.")
        }
        let play = element("playButton")
        try waitForHittable(play, timeout: 15, named: "episode collection play action")
        let actionToSeasonGap = seasonSelector.frame.minY - play.frame.maxY
        XCTAssertGreaterThan(actionToSeasonGap, 0,
                             "The season selector must sit below the collection action row.")
        XCTAssertLessThanOrEqual(actionToSeasonGap, 90,
                                 "Collection actions must sit at the bottom of the hero, close to the season selector.")

        try waitForHittable(firstEpisode, timeout: 20, named: "first season episode card")
        try waitForHittable(secondEpisode, timeout: 20, named: "second season episode card")
        XCTAssertTrue(accessibilityText(firstEpisode).contains("Unwatched"))
        XCTAssertTrue(accessibilityText(secondEpisode).contains("Unwatched"))
        // Labels and the selected focus outline affect a card's outer
        // accessibility frame. Compare the poster surfaces, which retain the
        // equal media-card geometry users see in the rail.
        let firstArtwork = firstEpisode.descendants(matching: .any).matching(identifier: "tvArtwork-\(fixture.episodeID)").firstMatch
        let secondArtwork = secondEpisode.descendants(matching: .any).matching(identifier: "tvArtwork-\(fixture.secondEpisodeID)").firstMatch
        try waitForExistence(firstArtwork, timeout: 15, named: "first episode poster")
        try waitForExistence(secondArtwork, timeout: 15, named: "second episode poster")
        XCTAssertEqual(firstArtwork.frame.width, secondArtwork.frame.width, accuracy: 2,
                       "Episode posters in one rail must have equal widths.")
        XCTAssertEqual(firstArtwork.frame.height, secondArtwork.frame.height, accuracy: 2,
                       "Episode posters in one rail must have equal heights.")
        XCTAssertGreaterThan(firstArtwork.frame.height, 0, "Episode posters must have a measurable height.")
        try attachFrame(named: "TV episode collection initial hero")

        XCTAssertTrue(focus(secondEpisode, timeout: 20), "The second episode card must be remote-focusable.")
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(fixture.secondEpisodeTitle)
        }, "Remote focus must update the selected-episode hero without opening the card.")
        XCTAssertTrue(accessibilityText(heroTitle).contains("S1") && accessibilityText(heroTitle).contains("E2"),
                      "Focused episode title must update its season and episode numbers.")
        try attachFrame(named: "TV episode collection focused second episode")

        try waitForHittable(secondSeason, timeout: 20, named: "second inline season selector")
        try activate(secondSeason)
        let secondSeasonEpisode = element("videoCollectionItem-\(secondSeasonEpisodeID)")
        try waitForHittable(secondSeasonEpisode, timeout: 25, named: "second season episode card")
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(secondSeasonEpisodeTitle)
        }, "Selecting a season must update the hero to that season's current or next unwatched episode.")
        XCTAssertTrue(accessibilityText(heroTitle).contains("S2") && accessibilityText(heroTitle).contains("E1"),
                      "Season switching must update the episode hero title numbers.")
        try attachFrame(named: "TV episode-focused collection with inline season selector")

        try activate(secondSeasonEpisode)
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "selected episode playback")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { _ in true }
        try closePlayer()

        // A fresh app process must prefer the server's saved resume episode over
        // season-one's first unwatched item.
        try await api.seedProgress(identity: identity, itemID: secondSeasonEpisodeID, seconds: 30)
        let relaunched = try XCTUnwrap(application)
        relaunched.terminate()
        relaunched.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "Home after episode process relaunch")
        try selectDestination("Video", screen: "libraryScreen")
        try selectTVLibraryCategory("tv", screen: "libraryScreen")
        let resumedShow = element("libraryItem-\(fixture.showID)")
        try focusVideoGridItemFromCategories(resumedShow, named: "episode-focused collection with saved progress")
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("videoCollectionScreen"), timeout: 25, named: "resumed episode-focused collection")
        try waitForExistence(element("collectionEpisodeTitle"), timeout: 20, named: "resumed episode hero")
        XCTAssertTrue(waitUntil(timeout: 15) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(secondSeasonEpisodeTitle)
        }, "A saved episode resume point must take priority over the first unwatched episode.")
        let resume = element("playButton")
        try waitForHittable(resume, timeout: 15, named: "resumed episode collection play")
        XCTAssertTrue(accessibilityText(resume).contains("Resume"),
                      "The selected resumed episode must present a Resume action.")
        let resumedEpisode = element("videoCollectionItem-\(secondSeasonEpisodeID)")
        XCTAssertTrue(accessibilityText(resumedEpisode).contains("In progress"))
        XCTAssertFalse(accessibilityText(resumedEpisode).contains("Unwatched"))
        XCTAssertTrue(focus(resumedEpisode, timeout: 20), "The resumed episode must remain remote-focusable.")
        try attachFrame(named: "In-progress episode retains its progress bar without an unwatched corner")
        XCUIRemote.shared.press(.playPause)
        let resumeChoice = element("resumePlaybackChoice")
        try waitForExistence(resumeChoice, timeout: 15, named: "saved episode resume choice")
        try activate(resumeChoice.buttons["Resume"])
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "episode resumed with Siri Remote")
        let resumedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 28 }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= resumedAt + 2 }
        try attachPlaybackFrame(named: "Episode resumed from collection with Siri Remote")
        try closePlayer()
        completed = true
    }
#endif

    #if os(iOS)
    #endif

    func journeyNativeAccountSettingsSearchSaveAndPersist() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        _ = try await api.settingsJSON(identity: identity, method: "PUT", body: ["ui_prefs": [
            "theme": "dark", "playback_max_height": 1080,
            "auto_skip": ["intro": false, "recap": true, "credits": false],
        ]])
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Account", screen: "accountScreen")
        #if os(tvOS)
        // tvOS groups the local settings in explicit focusable categories;
        // unlike iOS it intentionally has no free-text settings search.
        try selectDeviceSettingsCategory("account")
        XCTAssertFalse(element("accountConnectionsImportSection").exists)
        XCTAssertFalse(element("accountImportJellyfinButton").exists,
                       "Watch-history import belongs on the website.")
        try selectDeviceSettingsCategory("playback")
        try chooseDeviceSetting("devicePlaybackQualityPicker", option: "Up to 720p")
        try attachFrame(named: "tvOS Device Settings playback preferences")
        let saved = try await api.settingsJSON(identity: identity, method: "GET")
        let prefs = saved["ui_prefs"] as? [String: Any]
        XCTAssertEqual(prefs?["playback_max_height"] as? Int, 1080,
                       "Device quality must not overwrite the website preference.")
        XCTAssertEqual((prefs?["auto_skip"] as? [String: Any])?["intro"] as? Bool, false)
        XCTAssertEqual((prefs?["auto_skip"] as? [String: Any])?["recap"] as? Bool, true)
        application!.terminate()
        application!.launch()
        try selectDestination("Account", screen: "accountScreen")
        try selectDeviceSettingsCategory("playback")
        try assertDeviceSetting("devicePlaybackQualityPicker", contains: "Up to 720p")
        try attachFrame(named: "Persisted tvOS Device Settings playback preference")
        #else
        let search = element("settingsSearchField")
        try waitForHittable(search, timeout: 20, named: "Device-focused settings search")
        try replaceText(search, with: "import")
        XCTAssertFalse(element("accountConnectionsImportSection").exists)
        XCTAssertFalse(element("accountImportJellyfinButton").exists,
                       "Watch-history import belongs on the website.")
        try replaceText(search, with: "video quality")
        try chooseDeviceSetting("devicePlaybackQualityPicker", option: "Up to 720p")
        try attachFrame(named: "Lumina settings with inline underline selections")
        let saved = try await api.settingsJSON(identity: identity, method: "GET")
        let prefs = saved["ui_prefs"] as? [String: Any]
        XCTAssertEqual(prefs?["playback_max_height"] as? Int, 1080,
                       "Device quality must not overwrite the website preference.")
        XCTAssertEqual((prefs?["auto_skip"] as? [String: Any])?["intro"] as? Bool, false)
        XCTAssertEqual((prefs?["auto_skip"] as? [String: Any])?["recap"] as? Bool, true)
        application!.terminate()
        application!.launch()
        try selectDestination("Account", screen: "accountScreen")
        let restoredSearch = element("settingsSearchField")
        try waitForHittable(restoredSearch, timeout: 20, named: "Settings after relaunch")
        try replaceText(restoredSearch, with: "video quality")
        try assertDeviceSetting("devicePlaybackQualityPicker", contains: "Up to 720p")
        try replaceText(restoredSearch, with: "")
        try attachFrame(named: "Device-focused Lumina settings")
        #endif
        completed = true
    }

}
