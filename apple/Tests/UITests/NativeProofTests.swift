import Foundation
import XCTest
#if os(iOS)
import UIKit
#endif

#if os(iOS)
@MainActor
final class NativeHomeHeroMotionTests: NativeProofJourney {
    func testHomeArtworkMovesWhileDetailArtworkStaysStill() async throws {
        try await journeyNativeHomeHeroMotion()
    }
}
@MainActor
final class NativeWatchlistEpisodeHeroTests: NativeProofJourney {
    func testSavedEpisodeShowsSeriesArtworkAndEpisodeDetails() async throws {
        try await journeyWatchlistEpisodeHero()
    }
}
@MainActor
final class NativeAlbumArtworkColorTests: NativeProofJourney {
    func testAlbumPageUsesItsCoverColor() async throws {
        try await journeyAlbumArtworkColor()
    }
}
@MainActor
final class NativeMobileWelcomeTests: NativeProofJourney {
    func testMinimalWelcomeHelpAndSignIn() async throws {
        try await journeyMobileWelcome()
    }
}
@MainActor
final class NativeRequestDetailLayoutTests: NativeProofJourney {
    func testRequestHeroExtendsBehindDuoNavigationRail() async throws {
        try await journeyRequestDetailLayout()
    }
}
@MainActor
final class NativeSearchPresentationTests: NativeProofJourney {
    func testSearchIsHiddenUntilOpenedAndRemovedOnCancel() async throws {
        try await journeyNativeSearchPresentation()
    }
}
#endif

#if os(iOS)
@MainActor
final class NativeNativeBooksReadTurnAndRestoreAllFormatsTests: NativeProofJourney {
    func testNativeBooksReadTurnAndRestoreAllFormats() async throws { try await journeyNativeBooksReadTurnAndRestoreAllFormats() }
}
@MainActor
final class NativeBookCurlTests: NativeProofJourney {
    func testCompactReaderDefaultsToOnePage() async throws {
        try await journeyArtworkCurlHonorsReadingOrderAndCommitsOnlyCompletedTurns(compactLayoutOnly: true)
    }
    func testArtworkCurlHonorsReadingOrderAndCommitsOnlyCompletedTurns() async throws {
        try await journeyArtworkCurlHonorsReadingOrderAndCommitsOnlyCompletedTurns()
    }
}
#endif

@MainActor
final class NativeAudioAlbumTrackPlaybackReportsProgressTests: NativeProofJourney {
    func testAudioAlbumTrackPlaybackReportsProgress() async throws { try await journeyAudioAlbumTrackPlaybackReportsProgress() }
    func testAlbumPlayStartsFromIdle() async throws { try await journeyAlbumPlayStartsFromIdle() }
}

@MainActor
final class NativeAudioFormatRecoveryTests: NativeProofJourney {
    func testRejectedSourceRecoversWithCompatibleAudioAndShowsLyrics() async throws {
        try await journeyAlbumPlayStartsFromIdle(requiresCompatibleAudio: true)
    }
}

#if os(tvOS)
@MainActor
final class NativeAudioAlbumPlayAfterUnavailableRestoreTests: NativeProofJourney {
    func testAlbumPlayAfterUnavailableRestore() async throws {
        try await journeyAlbumPlayStartsFromIdle(restoredQueueUnavailable: true)
    }
}
#endif

@MainActor
final class NativeDownloadedAudioPlaysFromLocalFilesTests: NativeProofJourney {
    func testDownloadedAudioPlaysFromLocalFiles() async throws { try await journeyDownloadedAudioPlaysFromLocalFiles() }
}

@MainActor
final class NativeMusicAdvancedControlsPausedRestoreAndQueueConflictTests: NativeProofJourney {
    func testMusicAdvancedControlsPausedRestoreAndQueueConflict() async throws { try await journeyMusicAdvancedControlsPausedRestoreAndQueueConflict() }
}

@MainActor
final class NativeNativePlaybackReportsResumeAndConversionProgressTests: NativeProofJourney {
    func testNativePlaybackReportsResumeAndConversionProgress() async throws { try await journeyNativePlaybackReportsResumeAndConversionProgress() }
}

@MainActor
final class NativeOriginalMKVReportsPeriodicProgressAndResumesTests: NativeProofJourney {
    func testOriginalMKVReportsPeriodicProgressAndResumes() async throws { try await journeyOriginalMKVReportsPeriodicProgressAndResumes() }
}

#if os(iOS)
@MainActor
final class NativeCompatibleHLSReplayRequestsFreshSessionTests: NativeProofJourney {
    func testCompatibleHLSReplayRequestsFreshSession() async throws { try await journeyCompatibleHLSReplayRequestsFreshSession() }
}
#endif


#if os(tvOS)
@MainActor
final class NativeTVWelcomeShowcaseAndConnectionHelpTests: NativeProofJourney {
    func testTVWelcomeShowcaseAndConnectionHelp() throws { try journeyTVWelcomeShowcaseAndConnectionHelp() }
}
@MainActor
final class NativeTVNavigationArtworkAndVideoCategoriesTests: NativeProofJourney {
    func testTVNavigationArtworkAndVideoCategories() async throws { try await journeyTVNavigationArtworkAndVideoCategories() }
}
@MainActor
final class NativeTVHomeNavigationPreservesSelectionTests: NativeProofJourney {
    func testTVHomeNavigationPreservesSelection() async throws { try await journeyTVHomeNavigationPreservesSelection() }
}
@MainActor
final class NativeTVLibraryAlphabetTests: NativeProofJourney {
    func testTVLibraryAlphabetSortAndJump() throws { try journeyTVLibraryAlphabetSortAndJump() }
}
@MainActor
final class NativeTVBackHistoryTests: NativeProofJourney {
    func testTVBackRestoresChronologicalDestinations() throws { try journeyTVBackRestoresChronologicalDestinations() }
}
@MainActor
final class NativeTVMusicBrowseAndAutoHideBarTests: NativeProofJourney {
    func testTVMusicBrowseAndAutoHideBar() async throws { try await journeyTVMusicBrowseAndAutoHideBar() }
}
@MainActor
final class NativeTVBackDuringPlaybackPreparationTests: NativeProofJourney {
    func testTVBackDuringPlaybackPreparation() async throws { try await journeyTVBackDuringPlaybackPreparation() }
}
@MainActor
final class NativeTVEpisodeFocusedCollectionTests: NativeProofJourney {
    func testTVEpisodeFocusedCollection() async throws { try await journeyTVEpisodeFocusedCollection() }
}
#endif
#if os(iOS)
/// Samples Account surfaces that are safe to observe without changing account,
/// provider, job, or credential state.  It emits the same marker shape as the
/// main performance tour so the external sampler can correlate idle windows.
@MainActor
final class NativeAccountPerformanceTourTests: NativeProofJourney {
    func testNativeAccountPerformanceTour() async throws { try await journeyNativeAccountPerformanceTour() }
}
#endif

@MainActor
final class NativeAccountSettingsSearchSaveAndPersistTests: NativeProofJourney { func testNativeAccountSettingsSearchSaveAndPersist() async throws { try await journeyNativeAccountSettingsSearchSaveAndPersist() } }
#if os(iOS)
@MainActor
final class NativeAdminQuickActionsTests: NativeProofJourney {
    func testAdminCanScanInviteAndRevokeFromSettings() async throws { try await journeyNativeAdminQuickActions() }
}
#endif
@MainActor
final class NativeDeviceSettingsRouteTests: NativeProofJourney {
    func testDeviceSettingsUsesNativeLocalPreferences() throws { try journeyNativeDeviceSettingsRoute() }
}
@MainActor
final class NativeHomeSettingsOpensEditorTests: NativeProofJourney { func testHomeSettingsOpensEditor() async throws { try await journeyHomeSettingsOpensEditor() } }
#if os(iOS)
@MainActor
final class NativeLibraryEdgeTests: NativeProofJourney {
    func testLibraryPostersExtendBehindGlassNavigation() async throws { try await journeyLibraryEdges() }
}
@MainActor
final class NativeHomeCarouselEdgeTests: NativeProofJourney {
    func testCarouselReachesScreenEdgeBehindNavigation() async throws { try await journeyHomeCarouselEdges() }
}
@MainActor
final class NativeHomeEmptyWatchlistLayoutTests: NativeProofJourney {
    func testEmptyWatchlistKeepsEmptyStateBelowNavigation() async throws { try await journeyHomeEmptyWatchlistLayout() }
}
#endif

#if os(iOS)
@MainActor
final class NativeHomeBrowseFiltersSearchAndHeroWatchlistTests: NativeProofJourney {
    func testHomeBrowseFiltersSearchAndHeroWatchlist() async throws {
        try await journeyNativeHomeBrowseFiltersSearchAndHeroWatchlist()
    }
}
#endif
@MainActor
final class NativeHomeLayoutReordersHidesAndPersistsTests: NativeProofJourney { func testNativeHomeLayoutReordersHidesAndPersists() async throws { try await journeyNativeHomeLayoutReordersHidesAndPersists() } }
@MainActor
final class NativeRequestsDiscoverSelectAndSubmitThroughTheServerTests: NativeProofJourney { func testNativeRequestsDiscoverSelectAndSubmitThroughTheServer() async throws { try await journeyNativeRequestsDiscoverSelectAndSubmitThroughTheServer() } }
@MainActor
final class NativeTitleLogoArtworkAndTextFallbackTests: NativeProofJourney { func testNativeTitleLogoArtworkAndTextFallback() async throws { try await journeyNativeTitleLogoArtworkAndTextFallback() } }
@MainActor
final class NativeHomeDetailWatchlistPersistsThroughServerTests: NativeProofJourney { func testNativeHomeDetailWatchlistPersistsThroughServer() async throws { try await journeyNativeHomeDetailWatchlistPersistsThroughServer() } }
@MainActor
final class NativeEpisodeSkipActionsAndNextEpisodeTests: NativeProofJourney { func testNativeEpisodeSkipActionsAndNextEpisode() async throws { try await journeyNativeEpisodeSkipActionsAndNextEpisode() } }
@MainActor
final class NativeVideoOverlayTests: NativeProofJourney {
    func testVideoOverlayCentersAndDismisses() async throws { try await journeyVideoOverlayCentersAndDismisses() }
}
@MainActor
final class NativeVideoSettingsTests: NativeProofJourney { func testNativeVideoDoesNotKeepDuplicateControlsOverPlayback() async throws { try await journeyNativeVideoDoesNotKeepDuplicateControlsOverPlayback() } }
@MainActor
final class NativePlaybackQualityAndSubtitleTimingTests: NativeProofJourney { func testNativePlaybackQualityAndSubtitleTimingRepackagePausedMKV() async throws { try await journeyNativePlaybackQualityAndSubtitleTimingRepackagePausedMKV() } }
@MainActor
final class NativePlaybackSpeedAndChaptersTests: NativeProofJourney { func testNativePlaybackSpeedAndChaptersRepackagePausedMKV() async throws { try await journeyNativePlaybackSpeedAndChaptersRepackagePausedMKV() } }

@MainActor
final class NativeMediaDetailsTests: NativeProofJourney { func testNativeMediaDetails() async throws { try await journeyNativeMediaDetails() } }

/// A bounded, real-server navigation tour for external sampling tools.  The
/// emitted markers deliberately contain only stable phase names and elapsed
/// time: the fixture itself remains private to the test process.
@MainActor
final class NativePerformanceTourTests: NativeProofJourney {
    func testNativePerformanceTour() async throws { try await journeyNativePerformanceTour() }
}

@MainActor
class NativeProofJourney: XCTestCase {
    private var application: XCUIApplication?
    private let isolationID = UUID().uuidString
    private var brokerFixtureURL: URL?
    private var cachedFixture: ProofFixture?

    override func tearDown() async throws {
        try finishJourney()
        try await super.tearDown()
    }

    private func finishJourney() throws {
        application?.terminate()
        if let url = brokerFixtureURL {
            var request = URLRequest(url: url)
            request.httpMethod = "DELETE"
            _ = try brokerRequest(request)
        }
    }

    private func brokerRequest(_ request: URLRequest) throws -> Data {
        let semaphore = DispatchSemaphore(value: 0)
        let response = BrokerResponse()
        let session = URLSession(configuration: .ephemeral)
        defer { session.invalidateAndCancel() }
        session.dataTask(with: request) { data, reply, error in
            response.data = data
            response.succeeded = error == nil && (reply as? HTTPURLResponse)?.statusCode == 200
            semaphore.signal()
        }.resume()
        guard semaphore.wait(timeout: .now() + 300) == .success, response.succeeded,
              let data = response.data else { throw ProofFailure.invalidFixture }
        return data
    }


    override func setUpWithError() throws {
        try super.setUpWithError()
        continueAfterFailure = false
    }

    #if os(iOS)
    func journeyNativeBooksReadTurnAndRestoreAllFormats() async throws {
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
        let books = [epub, pdf, cbz, cbr]
        try launchAndResetSession()
        for book in books { try await api.seedBookProgress(identity: identity, bookID: book) }
        try signIn(using: fixture)
        try selectDestination("Books", screen: "booksScreen")
        try revealBookItem(element("bookCatalogButton"))
        try activate(element("bookCatalogButton"))
        for book in books {
            let card = element("book-\(book)")
            try revealBookItem(card)
            try waitForExistence(card, timeout: 20, named: "native book catalog entry")
            try activate(card)
            try activate(element("bookControlsButton"))
            let position = element("bookPosition")
            try waitForExistence(position, timeout: 20, named: "native reader position")
            let next = element("bookNextButton")
            try waitForHittable(next, timeout: 30, named: "prepared native page")
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
            try activate(element("bookCloseButton"))
            try revealBookItem(card)
            try activate(card)
            try activate(element("bookControlsButton"))
            try waitForExistence(element("bookPosition"), timeout: 20, named: "restored native book position")
            guard waitUntil(timeout: 30, condition: { self.element("bookPosition").label == selected }) else {
                throw ProofFailure.ui("Native book did not restore the saved page.")
            }
            try activate(element("bookCloseButton"))
        }
        completed = true
    }

    func journeyArtworkCurlHonorsReadingOrderAndCommitsOnlyCompletedTurns(compactLayoutOnly: Bool = false) async throws {
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
        try signIn(using: fixture)
        let booksCategory = element("homeBrowse-books")
        revealHomeMenuControl(booksCategory)
        try activate(booksCategory)
        try waitForExistence(element("booksScreen"), timeout: 20, named: "Books library")
        try revealBookItem(element("bookCatalogButton"))
        try activate(element("bookCatalogButton"))
        for book in [comic, pdf] {
            let card = element("book-\(book)")
            try revealBookItem(card); try activate(card)
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
                try activate(element("bookControlsButton"))
                try activate(element("bookCloseButton"))
                continue
            }
            let before = pager.value as? String
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
            try activate(element("bookControlsButton"))
            try activate(element("bookCloseButton"))
            try revealBookItem(card); try activate(card)
            try waitForExistence(pager, timeout: 30, named: "restored curled page")
            XCTAssertTrue(waitUntil(timeout: 20) { pager.value as? String == selected })
            try activate(element("bookControlsButton"))
            try activate(element("bookCloseButton"))
        }
    }

    private func revealBookItem(_ item: XCUIElement) throws {
        let viewport = try applicationViewport()
        for _ in 0..<8 {
            if hasVisibleFrame(item) && item.isHittable { return }
            // The outer Duo display can place navigation over a trailing catalog action.
            try dragVertically(in: viewport, anchorX: viewport.midX, up: true)
        }
        throw ProofFailure.ui("The Books catalog item could not be revealed.")
    }
    #endif

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
            try activate(element("audioStopButton"))
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
        let back = application!.buttons.matching(identifier: "BackButton").firstMatch
        XCTAssertTrue(back.exists, "Music must use the app's standard navigation back button.")
        XCTAssertFalse(element("closePlayerButton").exists, "Music must not invent a second back control.")
        XCTAssertTrue(element("audioQueueButton").exists)
        XCTAssertTrue(element("audioStopButton").exists)
        let transport = element("audioPlayPauseButton")
        try waitForHittable(transport, timeout: 15, named: "Navigation Play/Pause")
        XCTAssertFalse(element("audioPreviousButton").exists)
        XCTAssertFalse(element("audioNextButton").exists)
        let queue = element("audioQueueButton")
        let window = application!.windows.firstMatch.frame
        if queue.frame.minY > back.frame.maxY {
            XCTAssertLessThan(queue.frame.midY, transport.frame.midY,
                              "Queue must be the first action in the navigation rail.")
            XCTAssertEqual(transport.frame.midX, queue.frame.midX, accuracy: 3,
                           "Play/Pause must share the navigation rail with Queue.")
        } else {
            XCTAssertEqual(transport.frame.midY, queue.frame.midY, accuracy: 3,
                           "Play/Pause must share the navigation bar with Queue.")
        }
        let timeline = element("audioTimeline")
        let artwork = element("audioAlbumArtwork")
        try waitForExistence(artwork, timeout: 15, named: "Full-screen album artwork")
        XCTAssertTrue(waitUntil(timeout: 3) { !timeline.exists }, "Track progress must fade while artwork is idle.")
        artwork.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        XCTAssertTrue(timeline.exists, "Tapping artwork must reveal track progress immediately.")
        let timelineFrame = timeline.frame
        XCTAssertGreaterThan(timelineFrame.minY, window.minY + window.height * 0.7,
                             "Only the track timeline should occupy the bottom of the artwork stage.")
        XCTAssertLessThanOrEqual(timelineFrame.maxY, window.maxY)
        XCTAssertLessThanOrEqual(timelineFrame.minX, window.minX + 26)
        XCTAssertGreaterThanOrEqual(timelineFrame.maxX, window.maxX - 26,
                                    "Track progress must span the normal navigation area.")
        XCTAssertTrue(waitUntil(timeout: 3) { !timeline.exists }, "Revealed progress must fade again after inactivity.")
        artwork.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        let slider = application!.sliders["Track position"]
        XCTAssertTrue(slider.exists, "The revealed timeline must support seeking.")
        slider.adjust(toNormalizedSliderPosition: 0.6)
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

        #endif
        if requiresCompatibleAudio {
            try waitForExistence(element("audioPlaybackMethod"), timeout: 15, named: "compatible audio playback method")
            XCTAssertEqual(element("audioPlaybackMethod").label, "Compatible audio")
            XCTAssertFalse(element("replayButton").exists, "A rejected source must not be reported as completed.")
            try revealMusicControl(element("audioLyrics"))
            XCTAssertEqual(element("audioLyrics").value as? String, "Collapsed")
            try activate(element("audioLyrics"))
            let lyric = try XCTUnwrap(fixture.audioLyric)
            try waitForExistence(menuAction(lyric), timeout: 20, named: "Lyrics opened on demand")
        }
        try attachFrame(named: "Audio player with expandable lyrics")
        try activate(element("audioStopButton"))
        completed = true
    }

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
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "imported synthetic album")
        XCTAssertTrue(album.label.contains(fixture.audioAlbumTitle))
        try attachFrame(named: "Audio albums with synthetic artwork")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album tracks")
        let track = element("musicItem-\(fixture.audioTrackID)")
        #if os(iOS)
        let albumScreen = element("musicCollectionScreen")
        for _ in 0..<4 where !track.exists { albumScreen.swipeUp() }
        try revealInLibraryViewport([track], in: albumScreen)
        #endif
        try waitForExistence(track, timeout: 20, named: "synthetic album track")
        XCTAssertTrue(track.label.contains(fixture.audioTrackTitle))
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
        try waitForExistence(element("detailScreen"), timeout: 20, named: "audio track detail")
        let time = try startPlayback(using: "playButton")
        #endif
        let initial = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 5 }
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
        XCTAssertTrue(element("nowPlayingBar").label.contains(fixture.audioTrackTitle))
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioTrackID, minimum: 3)
        try unwindTVAudioToRoot()
        #else
        try waitForExistence(element("detailScreen"), timeout: 15, named: "track detail after browsing from audio")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "persistent now-playing bar")
        XCTAssertTrue(element("nowPlayingBar").label.contains(fixture.audioTrackTitle))
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioTrackID, minimum: 3)
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album after track detail")
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Audio catalog while music continues")
        #endif
        try selectDestination("Video", screen: "libraryScreen")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "now-playing bar while browsing Video")
        try await Task.sleep(for: .seconds(3))
        try selectDestination("Audio", screen: "audioScreen")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "reopened persistent audio player")
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        let reopened = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(reopened, beforeBrowse + 2, "Browsing destinations restarted or paused the audio session.")
        try attachFrame(named: "Persistent audio reopened after browsing Video and Audio")
        #if os(iOS)
        // The compact bar belongs to active playback only. Pausing from the
        // expanded player must keep that player available while removing the
        // background compact control.
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
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(self.element("nowPlayingBar")) }) else {
            throw ProofFailure.ui("Explicit Stop did not dismiss playback and clear the persistent now-playing bar.")
        }
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioTrackID, minimum: 3)

        completed = true
    }

    #if os(tvOS)
    func journeyTVMusicBrowseAndAutoHideBar() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }

        let fixture = try loadFixture()
        try await performancePhase("login_form") {
            try launchAndResetSession()
            try waitForExistence(element("serverURLField"), timeout: 20, named: "TV music connection form")
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
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(bar) }) else {
            throw ProofFailure.ui("Stop in the TV full player must dismiss it and remove the compact bar.")
        }
        try await performanceQuietPhase("tv_music_post_stop_idle")
        completed = true
    }
    #endif

    func journeyDownloadedAudioPlaysFromLocalFiles() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let sharedID = try XCTUnwrap(fixture.sharedMusicPlaylistID, "Regenerate the enriched music fixture first")
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try openMusicLibrarySection("downloads")
        let clearDownloads = application!.buttons.matching(NSPredicate(format: "label == %@", "Remove all downloads")).firstMatch
        if clearDownloads.exists && clearDownloads.isEnabled {
            try revealMusicControl(clearDownloads)
            try activate(clearDownloads)
            try activate(menuAction("Remove downloads"))
        }
        try musicBack()
        try openMusicLibrarySection("playlists")
        try activate(element("musicPlaylist-\(sharedID)"))
        try waitForExistence(element("musicPlaylistDetailScreen"), timeout: 20, named: "shared playlist")
        try revealMusicControl(element("downloadAudioButton"))
        try activate(element("downloadAudioButton"))
        try musicBack()
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "music library after playlist")
        try activate(element("musicLibrarySectionPicker"))
        try activate(menuAction("Downloads"))
        try waitForExistence(element("downloadedTrack-\(fixture.audioTrackID)"), timeout: 60, named: "downloaded first track")
        try waitForExistence(element("downloadedTrack-\(fixture.audioSecondTrackID)"), timeout: 20, named: "downloaded second track")
        try activate(element("playDownloadsButton"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "local downloaded playback")
        try waitForExistence(element("audioPlaybackMethod"), timeout: 15, named: "actual local playback method")
        XCTAssertEqual(element("audioPlaybackMethod").label, "Downloaded audio")
        let initial = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= initial + 3 }
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 15),
              disappears(element("nowPlayingBar"), timeout: 15) else {
            throw ProofFailure.ui("Explicit Stop did not dismiss downloaded playback.")
        }
        if clearDownloads.exists && clearDownloads.isEnabled {
            try revealMusicControl(clearDownloads)
            try activate(clearDownloads)
            try activate(menuAction("Remove downloads"))
        }
        guard disappears(element("downloadedTrack-\(fixture.audioTrackID)"), timeout: 20),
              disappears(element("downloadedTrack-\(fixture.audioSecondTrackID)"), timeout: 20) else {
            throw ProofFailure.ui("Removing downloads did not clear the downloaded tracks.")
        }
        completed = true
    }

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
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "album queue before restoration")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album queue before restoration")
        try startMusicCollectionPlayback()
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "album's advanced player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 4 }
        try revealMusicControl(element("audioPlayPauseButton"))
        try activate(element("audioPlayPauseButton"))
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
        try revealMusicControl(element("audioLyrics"))
        XCTAssertEqual(element("audioLyrics").value as? String, "Collapsed",
                       "Lyrics must remain collapsed until explicitly opened, including after loading.")
        try activate(element("audioLyrics"))
        XCTAssertEqual(element("audioLyrics").value as? String, "Expanded")
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
        guard waitUntil(timeout: 20, condition: { self.hasVisibleFrame(restoredBar) }) else {
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
        try revealMusicControl(element("audioUseServerButton"))
        try waitForExistence(element("audioKeepDeviceButton"), timeout: 20, named: "explicit device/server conflict choices")
        try attachFrame(named: "Queue revision conflict requires a deliberate choice")
        try activate(element("audioUseServerButton"))
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
        // TV places the menu label on its image rather than the containing button.
        let queueActions = element("audioQueueScreen").buttons.containing(.image, identifier: "ellipsis")
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
        let artwork = element("audioAlbumArtwork")
        let stage = application!.scrollViews.matching(identifier: "audioPlayerStage").firstMatch
        for _ in 0..<8 where !artwork.exists || artwork.frame.minY < -2 {
            stage.swipeDown()
        }
        try waitForHittable(artwork, timeout: 15, named: "Artwork after returning from listening options")
        application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.4))
            .press(forDuration: 0.1, thenDragTo: application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.4)))
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
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 20) else {
            throw ProofFailure.ui("Stop did not dismiss the restored player")
        }
        // Relaunch restored the queue on Home, without restoring a navigation stack.
        try selectDestination("Audio", screen: "audioScreen")
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
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        completed = true
    }

    private func currentAudioTitle() -> String {
        #if os(iOS)
        let artwork = element("audioAlbumArtwork")
        if artwork.exists, let title = artwork.value as? String { return title }
        #endif
        let title = element("nowPlayingTitle")
        return title.exists ? title.label : ""
    }

    private func revealMusicControl(_ control: XCUIElement) throws {
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

    private func musicBack() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        #endif
    }

    private func revealMusicAlbum(_ fixture: ProofFixture, named name: String) throws -> XCUIElement {
        let album = element("audioItem-\(fixture.audioAlbumID)")
        #if os(tvOS)
        let featured = element("audioFeaturedAlbum")
        try waitForExistence(featured, timeout: 25, named: "featured \(name)")
        XCTAssertTrue(featured.label.contains(fixture.audioAlbumTitle),
                      "The featured Music album must match the fixture album before navigating to its grid card.")
        for _ in 0..<10 {
            if hasVisibleFrame(album), album.isEnabled, album.isHittable, album.hasFocus {
                XCTAssertTrue(album.label.contains(fixture.audioAlbumTitle))
                return album
            }
            XCUIRemote.shared.press(.down)
        }
        throw ProofFailure.ui("Siri Remote Down did not reveal and focus the \(name) grid card.")
        #else
        try waitForExistence(album, timeout: 25, named: name)
        return album
        #endif
    }

    private func startMusicCollectionPlayback() throws {
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
    private func openMusicLibrarySection(_ section: String) throws {
        #if os(iOS)
        try activate(element("musicOptionsMenu"))
        #endif
        try activate(element("musicLibrary-\(section)"))
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "\(section) music library")
        try waitForExistence(element("musicLibrarySectionPicker"), timeout: 10, named: "\(section) music library picker")
    }

    #if os(tvOS)
    /// Album selection stays on its collection screen. Unwind only this proof
    /// journey's audio path rather than assuming two fixed Menu presses.
    private func unwindTVAudioToRoot() throws {
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

    func journeyTVWelcomeShowcaseAndConnectionHelp() throws {
        #if os(tvOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        let ready = element("connectionShowcaseReady")
        XCTAssertTrue(waitUntil(timeout: 15) { ready.exists && ((ready.value as? String)?.hasPrefix("asset") == true) },
                      "The welcome screen must begin with bundled offline artwork.")
        try enter(fixture.serverURL, into: element("serverURLField"))
        XCTAssertTrue(waitUntil(timeout: 25) {
            guard let value = ready.value as? String else { return false }
            return !value.hasPrefix("asset") && value != "unavailable"
        }, "The public release showcase should replace bundled artwork before sign-in.")
        XCTAssertFalse(element("connectionArtworkPauseButton").exists)
        try attachFrame(named: "TV welcome — public upcoming poster and compact sign-in")
        XCTAssertFalse(element("connectionHelpSheet").exists)
        try activate(element("connectionHelpButton"))
        try waitForExistence(element("connectionHelpSheet"), timeout: 8, named: "connection help")
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("connectionHelpDoneButton").hasFocus })
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("connectionHelpSheet"), timeout: 8))
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("connectionHelpButton").hasFocus },
                      "Back from help must restore its trigger.")
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
    func journeyTVHomeNavigationPreservesSelection() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: true)

        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("homeScreen"), timeout: 30, named: "TV Home browse screen")
        let movies = element("homeBrowse-movies")
        try waitForExistence(movies, timeout: 20, named: "TV Home Movies destination")
        XCTAssertFalse(accessibilityText(movies).contains("Selected"), "Home must not select a media category.")
        XCTAssertFalse(element("homeBrowse-all").exists)
        try activate(movies)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Movies library")
        XCTAssertTrue(accessibilityText(element("homeBrowse-movies")).contains("Selected"),
                      "Movies must open the matching Library category.")

        try activate(element("homeMasthead"))
        XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(self.element("homeScreen")) },
                      "The Lumina logo must route directly from Movies back to Home.")
        XCTAssertFalse(accessibilityText(element("homeBrowse-movies")).contains("Selected"),
                       "Returning Home must clear the media category selection.")

        try activate(element("homeBrowse-movies"))
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Movies library before direct Watchlist")
        try activate(element("homeBrowse-watchlist"))
        XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(self.element("homeScreen")) },
                      "Watchlist must route directly from Movies back to Home.")
        XCTAssertTrue(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"),
                      "Watchlist must become selected after routing directly from Movies.")

        try activate(element("homeMasthead"))
        try activate(element("homeBrowse-movies"))
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Movies library before Back")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "TV Home after Video Back")
        XCTAssertTrue(waitUntil(timeout: 12) { !self.accessibilityText(self.element("homeBrowse-watchlist")).contains("Selected") },
                      "A Library destination must not replace the selected local Home state.")

        let shows = element("homeBrowse-tv")
        try activate(shows)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Shows library")
        XCTAssertTrue(accessibilityText(element("homeBrowse-tv")).contains("Selected"),
                      "TV must open the matching Library category.")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "TV Home after Shows Back")
        let episodeResume = element("resumeItem-\(fixture.episodeID)")
        try waitForExistence(episodeResume, timeout: 20, named: "episode resume on TV Home")
        XCTAssertTrue(episodeResume.label.contains(fixture.showTitle) && episodeResume.label.contains("S1 · E1"),
                      "The TV episode resume must identify its series and episode number.")
        try activate(element("homeBrowse-music"))
        try waitForExistence(element("audioScreen"), timeout: 15, named: "TV Music library")
        let musicHero = element("audioFeaturedAlbum")
        try waitForExistence(musicHero, timeout: 20, named: "TV Music hero")
        XCTAssertTrue(focus(musicHero, timeout: 20), "The TV Music hero must accept remote focus.")
        XCUIRemote.shared.press(.up)
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("homeBrowse-music").hasFocus },
                      "Up from the focused Music hero must return to the persistent Music category.")
        XCUIRemote.shared.press(.left)
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("homeBrowse-tv").hasFocus },
                      "Left from Music must skip the unconfigured Anime library and reach TV.")
        XCTAssertFalse(element("homeBrowse-anime").exists)
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "TV Home after Music Back")

        try activate(element("homeBrowse-watchlist"))
        XCTAssertTrue(waitUntil(timeout: 12) { self.accessibilityText(self.element("homeBrowse-watchlist")).contains("Selected") },
                      "Watchlist remains a local TV Home refinement.")
        try waitForExistence(element("featuredWatchlistButton"), timeout: 15, named: "TV Home hero watchlist")
        XCTAssertFalse(element("featuredDetailsButton").exists,
                       "The TV Home hero must keep one title route instead of a duplicate Details action.")
        try attachFrame(named: "TV Home destinations preserve its local selection")
        try activate(element("homeSearchButton"))
        try waitForExistence(element("homeSearchScreen"), timeout: 15, named: "TV Home search")
        try waitForExistence(element("homeSearchField"), timeout: 15, named: "TV Home search field")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("homeSearchScreen"), timeout: 15),
                      "Back must dismiss Home search before leaving the browsing context.")
        XCTAssertTrue(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"),
                      "Closing Home search must retain the local Home selection.")
        completed = true
    }

    func journeyTVNavigationArtworkAndVideoCategories() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let requestMovieKey = try XCTUnwrap(fixture.requestMovieKey, "Generate the Requests movie fixture.")
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
        let homeViewport = try applicationViewport()
        let firstShelfHeading = application!.staticTexts["Continue watching"].firstMatch
        try waitForExistence(firstShelfHeading, timeout: 15, named: "first shelf below Home hero")
        XCTAssertGreaterThanOrEqual(firstShelfHeading.frame.minY, homeViewport.height * 0.75,
                                    "The TV Home artwork must dominate the first viewport before shelves begin.")
        let firstCategory = element("homeBrowse-movies")
        let lastCategory = element("homeBrowse-watchlist")
        let categoryMidpoint = (firstCategory.frame.minX + lastCategory.frame.maxX) / 2
        XCTAssertEqual(categoryMidpoint, homeViewport.midX, accuracy: 8,
                       "Primary TV categories must remain geometrically centered regardless of edge utilities.")
        XCTAssertFalse(element("homeTab").exists)
        XCTAssertFalse(element("libraryTab").exists)
        XCTAssertFalse(element("requestsTab").exists)
        XCTAssertEqual(element("homeMasthead").frame.midY, firstCategory.frame.midY, accuracy: 8,
                       "The masthead and primary categories must share one header line.")
        XCTAssertFalse(element("homeVideoButton").exists)
        XCTAssertFalse(element("homeAudioButton").exists)
        let featuredTitle = element("featuredTitleButton")
        try waitForExistence(featuredTitle, timeout: 20, named: "Home featured title")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "The Home featured title must be remote-focusable.")
        let featureBeforeBrowse = accessibilityText(featuredTitle)
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("featuredTitleButton")) != featureBeforeBrowse
        }, "Right on a focused Home title must choose an adjacent featured release.")
        XCTAssertTrue(waitUntil(timeout: 3) { self.element("featuredTitleButton").hasFocus },
                      "Browsing a featured title must retain remote focus on that title.")
        XCUIRemote.shared.press(.left)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("featuredTitleButton")) == featureBeforeBrowse
        }, "Left on a focused Home title must restore the preceding featured release.")
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("featuredTitleButton")) != featureBeforeBrowse
        }, "A subsequent right move must remain available after returning to the original feature.")
        let featureAfterBrowse = accessibilityText(featuredTitle)
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "featured title selected after remote browse")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after featured title Back")
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("featuredTitleButton")) == featureAfterBrowse
        }, "Back from a browsed featured title must restore its Home selection.")
        try attachFrame(named: "TV Home with pinned categories and immersive artwork")
        try activate(featuredTitle)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "TV film details")
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
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Return to Home")
        try selectDestination("Video", screen: "libraryScreen")
        try selectDestination("Audio", screen: "audioScreen")
        try selectDestination("Video", screen: "libraryScreen")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Back returns unified Library to Home")
        try selectDestination("Video", screen: "libraryScreen")
        try exerciseTVLibraryAlphabet(using: fixture)
        try attachFrame(named: "TV Video Movies category")
        try activate(element("libraryItem-\(fixture.directItemID)"))
        try waitForExistence(element("detailScreen"), timeout: 15, named: "Video title details")
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("detailScreen"), timeout: 15), "Back must pop the Video title detail.")
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Detail Back returns to Video, not Home")
        try activate(element("homeBrowse-tv"))
        try waitForExistence(element("libraryItem-\(fixture.showID)"), timeout: 25, named: "Shows library")
        try attachFrame(named: "TV Library Shows category")
        XCTAssertFalse(element("homeBrowse-anime").exists, "Unconfigured Anime must be absent on TV too.")
        XCTAssertFalse(element("homeBrowse-books").exists,
                       "Books remain intentionally unavailable on tvOS.")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Library Back returns to Home")
        try waitForExistence(featuredTitle, timeout: 15, named: "restored Home content after Library Back")
        XCTAssertTrue(focus(featuredTitle, timeout: 15), "Back to Home must restore a focusable featured title.")
        try selectDestination("Account", screen: "accountScreen")
        try waitForExistence(element("deviceSettingsScreen"), timeout: 15, named: "TV Account settings")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Account Back returns to Home")
        XCTAssertFalse(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"),
                       "Closing Account settings must preserve the selected primary category.")
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
        let requestFeature = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "requestFeatured-")).firstMatch
        try waitForExistence(requestFeature, timeout: 25, named: "Requests featured artwork")
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
        XCTAssertTrue(requests.contains { $0.key == requestMovieKey },
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
        try waitForExistence(element("audioFeaturedAlbum"), timeout: 20, named: "Audio album artwork")
        try attachFrame(named: "TV Audio library")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Audio Back returns to Home")
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
        try exerciseTVLibraryAlphabet(using: fixture)
        completed = true
    }

    private func exerciseTVLibraryAlphabet(using fixture: ProofFixture) throws {
        try waitForExistence(element("homeBrowse-movies"), timeout: 15, named: "Movies category")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 20, named: "Movies artwork")
        let sortPicker = element("videoSortPicker")
        let alphabet = element("libraryAlphabetIndex")
        try waitForExistence(sortPicker, timeout: 15, named: "Title A–Z sort control")
        XCTAssertTrue(application!.staticTexts["Title A–Z"].exists,
                      "The name-sorted Video toolbar must present a plain Title A–Z control.")
        try waitForExistence(alphabet, timeout: 20, named: "library alphabet index")
        let viewport = try applicationViewport()
        let filters = element("videoFiltersButton")
        let search = element("searchField")
        try waitForExistence(filters, timeout: 10, named: "inline Video filters")
        let categoryMidpoint = (element("homeBrowse-movies").frame.minX + element("homeBrowse-watchlist").frame.maxX) / 2
        XCTAssertEqual(categoryMidpoint, viewport.midX, accuracy: 8,
                       "The six primary categories must remain centered in the TV viewport.")
        XCTAssertLessThan(element("homeBrowse-movies").frame.maxY, sortPicker.frame.minY,
                          "Library categories must remain pinned above its options row.")
        XCTAssertEqual(filters.frame.midY, sortPicker.frame.midY, accuracy: 3,
                       "Sort and filters must share the Library options baseline.")
        XCTAssertLessThan(sortPicker.frame.maxX, filters.frame.minX)
        XCTAssertGreaterThan(search.frame.minX, filters.frame.maxX,
                             "Search must remain reachable at the trailing edge of Library options.")
        XCTAssertGreaterThan(alphabet.frame.minX, viewport.maxX - 200,
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
        }, "Selecting an alphabet letter must replace the page and focus its first matching title.")

        try activate(sortPicker)
        try activate(menuAction("Recently added"))
        XCTAssertTrue(disappears(alphabet, timeout: 15),
                      "A non-name sort must disable the alphabet index.")
        try activate(sortPicker)
        try activate(menuAction("Name"))
        try waitForExistence(alphabet, timeout: 20, named: "alphabet index restored by name sort")
    }

    func journeyTVBackRestoresChronologicalDestinations() throws {
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

        try selectDestination("Requests", screen: "requestsScreen")
        try selectDestination("Account", screen: "accountScreen")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("requestsScreen"), timeout: 15, named: "chronological Back returns Account to Requests")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "chronological Back returns Requests to Library")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("homeScreen"), timeout: 15, named: "chronological Back returns Library to Home")
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

        try activate(element("homeBrowse-tv"))
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
        try activate(element("homeBrowse-tv"))
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
        XCTAssertTrue(accessibilityText(heroTitle).contains(fixture.episodeTitle),
                      "The initial hero must select the first current or next unwatched episode.")
        XCTAssertTrue(accessibilityText(heroTitle).contains("S1") && accessibilityText(heroTitle).contains("E1"),
                      "The initial hero title must expose season and episode numbers.")
        XCTAssertTrue(waitUntil(timeout: 10) {
            !self.accessibilityText(self.element("collectionEpisodeMetadata"))
                .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }, "The initial hero must expose the selected episode's runtime or source facts.")
        XCTAssertTrue(accessibilityText(heroSynopsis).contains("Synthetic episode."),
                      "The initial hero must expose the selected episode synopsis.")
        let play = element("playButton")
        try waitForHittable(play, timeout: 15, named: "episode collection play action")
        let actionToSeasonGap = seasonSelector.frame.minY - play.frame.maxY
        XCTAssertGreaterThan(actionToSeasonGap, 0,
                             "The season selector must sit below the collection action row.")
        XCTAssertLessThanOrEqual(actionToSeasonGap, 90,
                                 "Collection actions must sit at the bottom of the hero, close to the season selector.")

        try waitForHittable(firstEpisode, timeout: 20, named: "first season episode card")
        try waitForHittable(secondEpisode, timeout: 20, named: "second season episode card")
        let firstFrame = firstEpisode.frame
        let secondFrame = secondEpisode.frame
        XCTAssertEqual(firstFrame.width, secondFrame.width, accuracy: 2,
                       "Episode cards in one rail must have equal widths.")
        XCTAssertEqual(firstFrame.height, secondFrame.height, accuracy: 2,
                       "Episode cards in one rail must have equal heights.")
        XCTAssertGreaterThan(firstFrame.height, 0, "Episode cards must have a measurable height.")
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
        try waitForExistence(element("detailScreen"), timeout: 20, named: "selected episode detail")
        try waitForExistence(application!.staticTexts[secondSeasonEpisodeTitle], timeout: 10,
                             named: "selected episode detail title")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("videoCollectionScreen"), timeout: 15, named: "collection after episode detail Back")
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(secondSeasonEpisodeTitle)
        }, "Returning from an episode detail must retain the selected episode hero.")
        try waitForHittable(play, timeout: 15, named: "selected episode collection play")
        try activate(play)
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "selected episode playback")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { _ in true }
        try closePlayer()

        // A fresh local session must prefer the server's saved resume episode over
        // season-one's first unwatched item.
        try await api.seedProgress(identity: identity, itemID: secondSeasonEpisodeID, seconds: 30)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        try activate(element("homeBrowse-tv"))
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
        completed = true
    }
#endif

    #if os(iOS)
    func journeyNativeAccountPerformanceTour() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try await performancePhase("login_form") {
            try launchAndResetSession()
            try waitForExistence(element("serverURLField"), timeout: 20, named: "connection form")
        }
        try await performanceQuietPhase("login_form_idle")
        try await performancePhase("account_sign_in") {
            try signIn(using: fixture)
            try selectDestination("Account", screen: "accountScreen")
        }
        let search = element("settingsSearchField")
        try waitForHittable(search, timeout: 20, named: "Settings search")
        try await performancePhase("account_device_preferences") {
            try replaceText(search, with: "video quality")
            try waitForHittable(element("devicePlaybackQualityPicker"), timeout: 15, named: "Device video quality")
        }
        try await performanceQuietPhase("account_preferences_idle")
        try await performancePhase("account_licenses") {
            try replaceText(search, with: "licenses")
            try activate(element("accountLicensesButton"))
            try waitForExistence(element("accountLicensesSheet"), timeout: 15, named: "Open-source licenses")
            try await performanceQuietPhase("account_licenses_idle")
            try activate(application!.buttons.matching(NSPredicate(format: "label == %@", "Done")).firstMatch)
        }
        try replaceText(search, with: "")
        completed = true
    }
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
        completed = true
    }

    #if os(iOS)
    func journeyNativeAdminQuickActions() async throws {
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
        try selectDestination("Account", screen: "accountScreen")
        try chooseDeviceSetting("deviceAppearancePicker", option: "Light")
        try replaceText(element("settingsSearchField"), with: "server")
        try waitForExistence(element("nativeAdminSettings"), timeout: 15, named: "Verified admin quick actions")
        try activate(element("adminScanLibrariesButton"))
        try waitForExistence(element("adminScanResult"), timeout: 15, named: "Library scan acknowledgment")
        try attachFrame(named: "Compact native admin quick actions")
        try activate(element("adminInvitationsButton"))
        try waitForExistence(element("nativeInvitationsScreen"), timeout: 15, named: "Native invitations")
        let email = "native-settings-\(UUID().uuidString.lowercased())@example.test"
        try activate(element("adminInviteEmail"))
        element("adminInviteEmail").typeText(email + "\n")
        try activate(element("adminInviteLibrary-movies"))
        let password = element("adminVerificationPassword")
        try revealInLibraryViewport([password], in: element("nativeInvitationsScreen"))
        try activate(password)
        password.typeText(fixture.password + "\n")
        try activate(element("adminCreateInvitation"))
        try waitForExistence(element("adminShareInvitation"), timeout: 15, named: "Created one-time invitation")
        let invitations = try await api.nativeInvitations(identity: identity)
        let created = try XCTUnwrap(invitations.first { $0["email"] as? String == email })
        let id = try XCTUnwrap(created["id"] as? String)
        XCTAssertEqual(created["status"] as? String, "pending")
        XCTAssertNil(created["invitation_url"], "Listing invitations must not reveal their secret links.")
        try activate(element("adminRevokeInvite-\(id)"))
        try activate(password)
        password.typeText(fixture.password + "\n")
        try activate(element("adminRevokeInviteConfirm"))
        XCTAssertTrue(waitUntil(timeout: 15) { !self.element("adminRevokeInvite-\(id)").exists })
        let revoked = try await api.nativeInvitations(identity: identity)
        XCTAssertEqual(revoked.first { $0["id"] as? String == id }?["status"] as? String, "revoked")
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try replaceText(element("settingsSearchField"), with: "")
        try attachFrame(named: "Lumina settings in light appearance")
        try chooseDeviceSetting("deviceAppearancePicker", option: "Default")
        completed = true
    }
    #endif

    func journeyNativeDeviceSettingsRoute() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try openDeviceSettings()
        try attachFrame(named: "Device settings initial layout")
        try chooseDeviceSetting("devicePlaybackQualityPicker", option: "Up to 720p")
        #if os(tvOS)
        try selectDeviceSettingsCategory("audio")
        #endif
        try chooseDeviceSetting("devicePreferredAudioLanguagePicker", option: "Japanese")
        #if os(tvOS)
        try selectDeviceSettingsCategory("subtitles")
        #endif
        try chooseDeviceSetting("devicePreferredSubtitleLanguagePicker", option: "Spanish")
        try chooseDeviceSetting("deviceSubtitleModePicker", option: "Always")
        try activateDeviceControl("deviceSubtitleAppearanceButton")
        try waitForExistence(element("deviceSubtitleAppearanceScreen"), timeout: 15, named: "device caption appearance")
        try setDeviceSwitch("deviceCustomizeSubtitlesToggle", to: true)

        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "Home after Device Settings restart")
        try openDeviceSettings()
        try assertDeviceSetting("devicePlaybackQualityPicker", contains: "Up to 720p")
        #if os(tvOS)
        try selectDeviceSettingsCategory("audio")
        #endif
        try assertDeviceSetting("devicePreferredAudioLanguagePicker", contains: "Japanese")
        #if os(tvOS)
        try selectDeviceSettingsCategory("subtitles")
        #endif
        try assertDeviceSetting("devicePreferredSubtitleLanguagePicker", contains: "Spanish")
        try assertDeviceSetting("deviceSubtitleModePicker", contains: "Always")
        try attachFrame(named: "Device settings local playback preferences")
        try activateDeviceControl("deviceSubtitleAppearanceButton")
        try waitForExistence(element("deviceSubtitleAppearanceScreen"), timeout: 15, named: "persisted device caption appearance")
        XCTAssertEqual(nativeSwitchValue(element("deviceCustomizeSubtitlesToggle")), true,
                       "Caption appearance customization must persist as a device preference.")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("deviceSettingsScreen"), timeout: 15, named: "Device Settings after caption Back")
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try waitForExistence(element("deviceSettingsScreen"), timeout: 15, named: "Device Settings after caption Back")
        #endif
        #if os(tvOS)
        try selectDeviceSettingsCategory("playback")
        #endif
        #if os(tvOS)
        try chooseDeviceSetting("devicePlaybackQualityPicker", option: "Original (best available)")
        #else
        try chooseDeviceSetting("devicePlaybackQualityPicker", option: "Original")
        #endif
        #if os(tvOS)
        try selectDeviceSettingsCategory("audio")
        #endif
        try chooseDeviceSetting("devicePreferredAudioLanguagePicker", option: "Automatic")
        #if os(tvOS)
        try selectDeviceSettingsCategory("subtitles")
        #endif
        try chooseDeviceSetting("devicePreferredSubtitleLanguagePicker", option: "Automatic")
        try chooseDeviceSetting("deviceSubtitleModePicker", option: "Automatic")
        try activateDeviceControl("deviceSubtitleAppearanceButton")
        try waitForExistence(element("deviceSubtitleAppearanceScreen"), timeout: 15, named: "caption appearance before reset")
        try setDeviceSwitch("deviceCustomizeSubtitlesToggle", to: false)

        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("deviceSettingsScreen"), timeout: 15, named: "Device Settings after restoring defaults")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("accountButton"), timeout: 15, named: "Account utility after Device Settings Back")
        #endif
        completed = true
    }

    private func openDeviceSettings() throws {
        #if os(iOS)
        try selectDestination("Account", screen: "accountScreen")
        #else
        try selectDestination("Account", screen: "accountScreen")
        #endif
        for identifier in ["deviceSettingsScreen", "devicePlaybackQualityPicker"] {
            guard waitUntil(timeout: 20, condition: { self.element(identifier).exists }) else {
                throw ProofFailure.timeout("Timed out waiting for \(identifier).")
            }
        }
    }

    private func chooseDeviceSetting(_ identifier: String, option: String) throws {
        try activateDeviceControl(identifier)
        #if os(tvOS)
        // Resolve the destination only after its custom choice list is visible.
        try activate(try devicePickerOption(option))
        #else
        let choices = element("\(identifier)-choices")
        let choice = choices.buttons.matching(NSPredicate(format: "label == %@", option)).firstMatch
        try revealInLibraryViewport([choice], in: element("accountScreen").exists ? element("accountScreen") : element("deviceSettingsScreen"))
        try activate(choice)
        #endif
        try assertDeviceSetting(identifier, contains: option)
    }

    private func activateDeviceControl(_ identifier: String) throws {
        #if os(tvOS)
        let control = element(identifier)
        try waitForExistence(control, timeout: 15, named: "Device Settings \(identifier)")
        try activate(control)
        #else
        let control = element(identifier)
        let screen = hasVisibleFrame(element("deviceSubtitleAppearanceScreen"))
            ? element("deviceSubtitleAppearanceScreen") : element("accountScreen")
        try revealInLibraryViewport([control], in: screen)
        try activate(control)
        #endif
    }

    #if os(tvOS)
    private func devicePickerOption(_ title: String) throws -> XCUIElement {
        let label = NSPredicate(format: "label == %@", title)
        let tableChoice = application!.tables.cells.matching(label).firstMatch
        let focusedPopupChoice = application!.collectionViews
            .containing(NSPredicate(format: "hasFocus == true"))
            .cells.containing(.other, identifier: title).firstMatch
        let collectionChoice = application!.collectionViews.cells.matching(label).firstMatch
        let buttonChoice = application!.buttons.matching(label).firstMatch
        let fallbackChoice = application!.descendants(matching: .any).matching(label).firstMatch
        let choices = [tableChoice, focusedPopupChoice, collectionChoice, buttonChoice, fallbackChoice]

        guard waitUntil(timeout: 10, condition: {
            choices.contains { $0.exists && self.hasVisibleFrame($0) }
        }) else {
            throw ProofFailure.ui("The tvOS native picker did not present the \(title) choice.")
        }
        guard let choice = choices.first(where: { $0.exists && hasVisibleFrame($0) }) else {
            throw ProofFailure.ui("The tvOS native picker presented no visible \(title) choice.")
        }
        return choice
    }
    #endif

    #if os(tvOS)
    private func selectDeviceSettingsCategory(_ category: String) throws {
        let control = element("deviceSettingsCategory-\(category)")
        try waitForExistence(control, timeout: 15, named: "Device Settings \(category) category")
        try activate(control)
        XCTAssertTrue(waitUntil(timeout: 5) {
            self.accessibilityText(self.element("deviceSettingsCategory-\(category)")).contains("Selected")
        }, "The \(category) Device Settings category did not become selected.")
    }
    #endif

    private func setDeviceSwitch(_ identifier: String, to enabled: Bool) throws {
        #if os(tvOS)
        let control = element(identifier)
        try waitForExistence(control, timeout: 15, named: identifier)
        if nativeSwitchValue(control) != enabled {
            try activate(control)
        }
        XCTAssertEqual(nativeSwitchValue(control), enabled, "\(identifier) did not change.")
        #else
        let row = element(identifier)
        try revealInLibraryViewport([row], in: element("deviceSubtitleAppearanceScreen"))
        try activate(element("\(identifier)-\(enabled ? "on" : "off")"))
        XCTAssertEqual(nativeSwitchValue(row), enabled)
        #endif
    }

    private func assertDeviceSetting(_ identifier: String, contains expected: String) throws {
        XCTAssertTrue(waitUntil(timeout: 10) {
            let current = self.element(identifier)
            return current.exists && self.accessibilityText(current).localizedCaseInsensitiveContains(expected)
        }, "\(identifier) did not retain \(expected).")
    }

    func journeyHomeSettingsOpensEditor() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        XCTAssertFalse(element("homeOptionsMenu").exists, "Home customization belongs in Account settings.")
        let masthead = element("homeMasthead")
        try waitForExistence(masthead, timeout: 20, named: "Home masthead")
        XCTAssertGreaterThan(masthead.frame.width, 80, "The full Lumina wordmark must fit in the header.")
        XCTAssertTrue(application!.navigationBars.descendants(matching: .any)
            .matching(identifier: "homeMasthead").firstMatch.exists,
            "Lumina must remain in the top navigation header, outside the scrolling hero.")
        let homeCategories = element("homeBrowse-movies")
        try waitForHittable(element("homeBrowse-movies"), timeout: 15, named: "Top navigation Home categories")
        let homeCategoryY = homeCategories.frame.minY
        XCTAssertEqual(homeCategories.frame.midY, masthead.frame.midY, accuracy: 6,
                       "Categories must share the top navigation line with the Lumina logo.")
        let homeMenuCenter = (element("homeBrowse-movies").frame.minX + element("homeBrowse-watchlist").frame.maxX) / 2
        if application!.windows.firstMatch.frame.width > 700 {
            XCTAssertLessThan(abs(homeMenuCenter - application!.windows.firstMatch.frame.midX), 48,
                              "Home categories must occupy the center of the top navigation.")
        }
        try attachFrame(named: "Home with categories beside the logo")
        try selectDestination("Library", screen: "libraryScreen")
        let tiles = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-"))
        try waitForExistence(tiles.firstMatch, timeout: 20, named: "Library poster grid")
        let visibleTiles = tiles.allElementsBoundByIndex.filter { hasVisibleFrame($0) }
        XCTAssertGreaterThanOrEqual(visibleTiles.count, 2)
        if visibleTiles.count >= 2 {
            XCTAssertEqual(visibleTiles[0].frame.width, visibleTiles[1].frame.width, accuracy: 1)
            XCTAssertEqual(visibleTiles[0].frame.height, visibleTiles[1].frame.height, accuracy: 1)
            XCTAssertEqual(visibleTiles[0].frame.minY, visibleTiles[1].frame.minY, accuracy: 1)
        }
        let libraryCategories = element("libraryCategory-movies")
        XCTAssertEqual(libraryCategories.frame.minY, homeCategoryY, accuracy: 1,
                       "Home and Library must use the same category header position.")
        if application!.windows.firstMatch.frame.width > 700 {
            let menuCenter = (element("libraryCategory-movies").frame.minX + element("libraryCategory-books").frame.maxX) / 2
            XCTAssertEqual(menuCenter, homeMenuCenter, accuracy: 3,
                           "Library categories must be centered when the complete menu fits.")
        }
        try attachFrame(named: "Library with centered categories and uniform posters")
        try selectDestination("Account", screen: "accountScreen")
        try activate(element("homeEditButton"))
        try waitForHittable(element("homeLayoutSaveButton"), timeout: 20, named: "Home editor from Account settings")
        try activate(element("homeLayoutCancelButton"))
        try waitForHittable(element("homeEditButton"), timeout: 15, named: "Account settings after cancelling editor")
        completed = true
    }

    #if os(iOS)
    private func revealHomeMenuControl(_ category: XCUIElement) {
        revealMenuControl(category, in: element("homeBrowsingRow"))
    }

    private func revealMenuControl(_ category: XCUIElement, in menu: XCUIElement) {
        for _ in 0..<8 where !menu.frame.insetBy(dx: 8, dy: -1).contains(CGPoint(x: category.frame.midX, y: category.frame.midY)) {
            let delta = min(menu.frame.width * 0.6,
                            max(-menu.frame.width * 0.6, category.frame.midX - menu.frame.midX))
            let start = menu.coordinate(withNormalizedOffset: CGVector(dx: delta > 0 ? 0.8 : 0.2, dy: 0.5))
            start.press(forDuration: 0.1, thenDragTo: start.withOffset(CGVector(dx: -delta, dy: 0)),
                        withVelocity: .slow, thenHoldForDuration: 0.3)
        }
    }

    func journeyLibraryEdges() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Library", screen: "libraryScreen")
        let window = application!.windows.firstMatch.frame
        try activate(element("videoSearchButton"))
        let cancel = element("videoSearchCancelButton")
        try waitForHittable(cancel, timeout: 15, named: "Search Cancel clear of navigation")
        let railControl = element("videoSearchButton")
        if railControl.frame.midX > window.maxX - 100 && railControl.frame.minY > cancel.frame.minY {
            XCTAssertLessThanOrEqual(cancel.frame.maxX + 8, railControl.frame.minX,
                                     "Search Cancel must leave space before the vertical navigation rail.")
        }
        try attachFrame(named: "Video search leaves Cancel clear of navigation")
        try activate(cancel)
        let library = element("libraryScreen")
        let posters = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-"))
        try waitForHittable(posters.firstMatch, timeout: 20, named: "Library posters")
        XCTAssertTrue(waitUntil(timeout: 15) {
            posters.allElementsBoundByIndex.contains { $0.frame.maxX >= window.maxX - 24 && $0.isHittable }
        }, "Real poster artwork must reach beyond the navigation rail, not stop at its safe-area boundary.")
        try attachFrame(named: "Library poster artwork continues beneath Liquid Glass to the bezel")
        let edgePoster = posters.allElementsBoundByIndex.first { $0.frame.maxX >= window.maxX - 24 && $0.isHittable }!
        let returnedPosterID = edgePoster.identifier
        var traversedPosterIDs = Set(posters.allElementsBoundByIndex.filter { hasVisibleFrame($0) }.map(\.identifier))
        let decodedCacheTarget = 80
        for _ in 0..<48 where traversedPosterIDs.count <= decodedCacheTarget {
            try scrollLibrary(library, up: true)
            try await Task.sleep(for: .milliseconds(200))
            traversedPosterIDs.formUnion(
                posters.allElementsBoundByIndex.filter { hasVisibleFrame($0) }.map(\.identifier)
            )
        }
        XCTAssertGreaterThan(traversedPosterIDs.count, decodedCacheTarget,
                             "The proof journey must traverse more posters than the decoded cache retains.")
        XCTAssertFalse(hasVisibleFrame(element(returnedPosterID)),
                       "Large-library scrolling must move the opening edge card out of the viewport.")
        for _ in 0..<48 where !hasVisibleFrame(element(returnedPosterID)) || !element(returnedPosterID).isHittable {
            try scrollLibrary(library, up: false)
        }
        try waitForHittable(element(returnedPosterID), timeout: 20, named: "opening poster after large-library return")
        XCTAssertTrue(waitUntil(timeout: 15) {
            posters.allElementsBoundByIndex.contains { $0.frame.maxX >= window.maxX - 24 && $0.isHittable }
        }, "Returning through a large library must leave its bezel-edge poster cards reachable.")
        try await Task.sleep(for: .milliseconds(500))
        try attachFrame(named: "Large poster wall returns with rendered artwork and reachable edge cards")
        let returnedEdgePoster = posters.allElementsBoundByIndex.first {
            $0.frame.maxX >= window.maxX - 24 && $0.isHittable
        }!
        // The glass controls correctly own touches above the poster; use its exposed portion.
        returnedEdgePoster.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 15, named: "Edge poster opens details")
        try assertVideoDetailChrome(screen: "detailScreen")
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library after native Back")
        revealMenuControl(element("libraryCategory-tv"), in: element("libraryCategories"))
        try activate(element("libraryCategory-tv"))
        let show = element("libraryItem-\(fixture.showID)")
        try waitForHittable(show, timeout: 20, named: "Show collection")
        try activate(show)
        try waitForExistence(element("videoCollectionScreen"), timeout: 15, named: "Series details")
        try assertVideoDetailChrome(screen: "videoCollectionScreen")
        try attachFrame(named: "Series details with Lumina branding and artwork to the bezel")
        let season = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "videoSeason-")).firstMatch
        try revealInLibraryViewport([season], in: element("videoCollectionScreen"))
        try activate(season)
        try waitForHittable(element("playButton"), timeout: 15, named: "Season details")
        try assertVideoDetailChrome(screen: "videoCollectionScreen")
        try attachFrame(named: "Season details with artwork to the bezel")
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library after collection Back")
        for (category, screen) in [("music", "audioScreen"), ("books", "booksScreen")] {
            revealMenuControl(element("libraryCategory-\(category)"), in: element("libraryCategories"))
            try activate(element("libraryCategory-\(category)"))
            try waitForExistence(element(screen), timeout: 15, named: "\(category) library")
            XCTAssertGreaterThanOrEqual(element(screen).frame.maxX, window.maxX - 2,
                                        "The \(category) library must extend beneath the right navigation.")
            if category == "music" {
                let featured = element("audioFeaturedAlbum")
                try waitForHittable(featured, timeout: 20, named: "Artwork-first featured album")
                XCTAssertTrue(featured.label.contains(fixture.audioAlbumTitle),
                              "Music must lead with a real album, not introductory copy.")
                XCTAssertLessThanOrEqual(featured.frame.minX, window.minX + 2)
                XCTAssertGreaterThanOrEqual(featured.frame.maxX, window.maxX - 2)
                XCTAssertLessThanOrEqual(featured.frame.minY, window.minY + 2,
                                         "Featured album artwork must extend to the top bezel behind navigation.")
                try attachFrame(named: "Music opens with bezel-to-bezel artwork and a compact album caption")
                try activate(featured)
                try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "Featured artwork opens its album")
                try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
                try waitForExistence(element("audioScreen"), timeout: 15, named: "Music after album Back")
            }
        }
    }

    private func assertVideoDetailChrome(screen identifier: String) throws {
        let masthead = element("homeMasthead")
        try waitForExistence(masthead, timeout: 15, named: "Lumina detail masthead")
        XCTAssertEqual(masthead.label, "Lumina")
        let viewport = application!.scrollViews.matching(identifier: identifier).firstMatch
        try waitForExistence(viewport, timeout: 15, named: "Detail viewport")
        XCTAssertLessThanOrEqual(viewport.frame.minY, application!.windows.firstMatch.frame.minY + 2,
                                 "Detail artwork must begin at the top bezel, behind the navigation header.")
    }

    func journeyHomeCarouselEdges() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        let home = element("homeScreen")
        let card = element("recentItem-\(fixture.directItemID)")
        try revealInLibraryViewport([card], in: home)
        let rail = element("mediaRail-recentItem")
        try waitForExistence(rail, timeout: 10, named: "Recently added carousel")
        let window = application!.windows.firstMatch.frame
        XCTAssertLessThanOrEqual(rail.frame.minX, window.minX + 2,
                                 "Carousels must enter from the bezel, beyond the heading inset.")
        XCTAssertGreaterThanOrEqual(rail.frame.maxX, window.maxX - 2,
                                    "Carousels must extend behind the right Liquid Glass navigation to the bezel.")
        try attachFrame(named: "Carousel extends behind navigation to screen edges")
        if window.width < 700 {
            rail.swipeLeft(velocity: .slow)
            let cards = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "recentItem-"))
            XCTAssertTrue(cards.allElementsBoundByIndex.contains { $0.frame.minX < window.minX && $0.frame.maxX > window.minX + 2 },
                          "Scrolling posters must remain visible as they roll across the left bezel.")
            try attachFrame(named: "Posters roll across the bezel while scrolling")
        }
        try activate(card)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "Carousel card remains reachable")
        try selectDestination("Library", screen: "libraryScreen")
        XCTAssertGreaterThanOrEqual(element("libraryScreen").frame.maxX, window.maxX - 2,
                                    "The poster wall must also extend beneath the right navigation.")
    }

    func journeyHomeEmptyWatchlistLayout() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: false)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.episodeID, included: false)
        try launchAndResetSession()
        try signIn(using: fixture)
        XCTAssertFalse(element("homeBrowse-all").exists, "Home must not appear as a category.")
        XCTAssertFalse(application!.buttons["All"].exists)
        XCTAssertFalse(element("homeBrowse-anime").exists, "A server without an Anime library must not advertise Anime.")
        XCTAssertTrue(element("homeBrowse-music").exists, "Available Music must remain in the server menu.")
        XCTAssertTrue(element("homeBrowse-books").exists, "Available Books must remain in the server menu.")
        let watchlist = element("homeBrowse-watchlist")
        try waitForExistence(watchlist, timeout: 20, named: "Home Watchlist")
        revealHomeMenuControl(watchlist)
        try activate(watchlist)
        let emptyState = application!.staticTexts["No matches on Home"].firstMatch
        try waitForExistence(emptyState, timeout: 15, named: "empty Watchlist state")
        XCTAssertFalse(element("featuredTitleButton").exists,
                       "An empty Watchlist must exercise Home without a hero.")
        XCTAssertGreaterThan(emptyState.frame.minY, element("homeMasthead").frame.maxY + 12,
                             "The empty state must start below the logo.")
        XCTAssertGreaterThan(emptyState.frame.minY, watchlist.frame.maxY + 12,
                             "The empty state must start below the Home navigation.")
        try attachFrame(named: "Empty Watchlist clears top navigation")
        try activate(destinationControl("Home"))
        try waitForHittable(element("featuredPlayButton"), timeout: 15, named: "Hero restored after choosing Home")
        XCTAssertFalse(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"))
        XCTAssertGreaterThan(element("featuredTitleButton").frame.minY, element("homeBrowse-movies").frame.maxY + 20)

        revealHomeMenuControl(watchlist)
        try activate(watchlist)
        try waitForExistence(emptyState, timeout: 15, named: "Watchlist before logo navigation")
        try activate(element("homeMasthead"))
        try waitForHittable(element("featuredPlayButton"), timeout: 15, named: "Logo restores full Home feed")
        XCTAssertFalse(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"))

        revealHomeMenuControl(element("homeBrowse-movies"))
        try activate(element("homeBrowse-movies"))
        try waitForHittable(element("libraryCategory-movies"), timeout: 15, named: "Movies before logo navigation")
        XCTAssertFalse(element("libraryCategory-anime").exists, "Library must use the same server categories as Home.")
        try activate(element("homeMasthead"))
        try waitForHittable(element("featuredPlayButton"), timeout: 15, named: "Logo returns from Library to Home")

        try activate(element("featuredTitleButton"))
        try waitForHittable(element("playButton"), timeout: 15, named: "Detail before logo navigation")
        try activate(element("homeMasthead"))
        try waitForHittable(element("featuredPlayButton"), timeout: 15, named: "Logo pops detail back to Home")
        XCTAssertFalse(hasVisibleFrame(element("detailScreen")))
    }
    #endif

    #if os(iOS)
    func journeyWatchlistEpisodeHero() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: false)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.episodeID, included: true)
        try launchAndResetSession()
        try signIn(using: fixture)
        let watchlist = element("homeBrowse-watchlist")
        revealHomeMenuControl(watchlist)
        try activate(watchlist)
        try waitForHittable(element("featuredTitleButton"), timeout: 20, named: "Saved episode hero")
        XCTAssertEqual(element("featuredTitleButton").label, fixture.showTitle)
        let subtitle = element("featuredEpisodeSubtitle")
        try waitForExistence(subtitle, timeout: 15, named: "Episode information below series title")
        XCTAssertTrue(subtitle.label.contains("S1 · E1") && subtitle.label.contains(fixture.episodeTitle))
        try waitForExistence(element("featuredTitleButton").images.firstMatch, timeout: 20, named: "Loaded series logo")
        XCTAssertEqual(element("featuredWatchlistButton").value as? String, "In watchlist")
        try attachFrame(named: "Watchlist episode with series artwork and episode details")
        try activate(element("featuredWatchlistButton"))
        try waitForExistence(application!.staticTexts["No matches on Home"].firstMatch, timeout: 20, named: "Removed saved episode leaves empty Watchlist")
        let stillIncluded = try await api.watchlistIncluded(identity: identity, itemID: fixture.episodeID)
        XCTAssertFalse(stillIncluded)
        completed = true
    }

    func journeyNativeHomeHeroMotion() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        let play = element("featuredPlayButton")
        try waitForHittable(play, timeout: 20, named: "Home hero")
        // Choosing a feature gives the artwork time to move without advancing the carousel.
        let choice = element("featuredChoice-\(fixture.convertedItemID)")
        try activate(choice)
        try await Task.sleep(for: .seconds(2))
        let controls = play.frame
        if application!.windows.firstMatch.frame.width > 700 {
            let carousel = element("featuredControls").frame
            XCTAssertEqual(carousel.maxY - play.frame.maxY, 16, accuracy: 3,
                           "Home actions must share the hero's bottom row with its carousel controls.")
            XCTAssertEqual(play.frame.minX, element("featuredTitleButton").frame.minX, accuracy: 2)
            XCTAssertEqual(play.frame.maxY, element("featuredWatchlistButton").frame.maxY, accuracy: 2)
        }
        let homeBefore = try heroPixels()
        try await Task.sleep(for: .seconds(2))
        XCTAssertNotEqual(try heroPixels(), homeBefore, "Home hero artwork must pan and zoom.")
        XCTAssertEqual(play.frame, controls, "Artwork motion must leave the hero controls stationary.")
        try attachFrame(named: "Home hero during slow artwork motion")

        try activate(element("featuredTitleButton"))
        try waitForHittable(element("playButton"), timeout: 20, named: "Expanded media detail")
        if application!.windows.firstMatch.frame.width > 700 {
            XCTAssertGreaterThan(element("playButton").frame.minY, application!.windows.firstMatch.frame.height * 0.65,
                                 "Detail actions must remain near the bottom of the hero.")
            XCTAssertEqual(element("playButton").frame.minX, element("detailTitle").frame.minX, accuracy: 2)
        }
        try await Task.sleep(for: .seconds(2))
        let detailBefore = try heroPixels()
        try await Task.sleep(for: .seconds(2))
        XCTAssertEqual(try heroPixels(), detailBefore, "Expanded detail artwork must stay static.")
        try attachFrame(named: "Static expanded detail artwork")
        completed = true
    }

    private func heroPixels() throws -> Data {
        let window = try applicationWindow()
        let screenshot = window.screenshot().image
        let viewport = window.frame
        // Duo snapshots can carry a rotated UIImage orientation. Normalize it
        // before sampling in the same coordinates used by the visible controls.
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let normalized = UIGraphicsImageRenderer(size: viewport.size, format: format).image { _ in
            screenshot.draw(in: CGRect(origin: .zero, size: viewport.size))
        }
        let source = try XCTUnwrap(normalized.cgImage)
        let scale = CGFloat(source.width) / viewport.width
        // Sample artwork below the Home header and outside the system rail and title controls.
        let viewportWidth = application!.windows.firstMatch.frame.width
        let sampleX = viewportWidth > 700 ? viewportWidth * 0.70 : 40
        let region = CGRect(x: sampleX * scale, y: 165 * scale, width: 160 * scale, height: 90 * scale)
        let sample = try XCTUnwrap(source.cropping(to: region))
        return try XCTUnwrap(UIImage(cgImage: sample).pngData())
    }
    #endif

    func journeyNativeHomeBrowseFiltersSearchAndHeroWatchlist() async throws {
        #if os(iOS)
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: false)

        try launchAndResetSession()
        try signIn(using: fixture)
        let home = element("homeScreen")
        try waitForExistence(home, timeout: 30, named: "Home browse screen")
        let title = element("featuredTitleButton")
        let play = element("featuredPlayButton")
        try waitForHittable(play, timeout: 20, named: "Home hero controls")
        let categories = element("homeBrowse-movies")
        if application!.windows.firstMatch.frame.width < 700 {
            XCTAssertGreaterThan(element("homeBrowsingRow").frame.width, 180,
                                 "The closed display must use the available width for multiple categories.")
        }
        XCTAssertGreaterThan(title.frame.minY, categories.frame.maxY + 20,
                             "Hero title must clear the category menu.")
        XCTAssertLessThan(title.frame.maxY, play.frame.minY,
                          "Title information must sit above the bottom-left controls.")
        XCTAssertLessThan(play.frame.minX, application!.windows.firstMatch.frame.width * 0.2)
        if application!.windows.firstMatch.frame.width > 700 {
            XCTAssertGreaterThan(play.frame.minY, application!.windows.firstMatch.frame.height * 0.4,
                                 "Expanded Home hero must leave cinematic space above its bottom-left controls.")
        }
        try attachFrame(named: "Home initial browse and hero")

        XCTAssertFalse(element("homeBrowse-all").exists, "Home must not appear as a category.")
        XCTAssertFalse(accessibilityText(element("homeBrowse-movies")).contains("Selected"))
        XCTAssertFalse(accessibilityText(element("homeBrowse-watchlist")).contains("Selected"))
        XCTAssertFalse(element("homeGenresMenu").exists, "Home must leave genre refinement to Library.")

        let episodeResume = element("resumeItem-\(fixture.episodeID)")
        try revealInLibraryViewport([episodeResume], in: home)
        try waitForHittable(episodeResume, timeout: 15, named: "episode Continue Watching card")
        XCTAssertTrue(episodeResume.label.contains(fixture.showTitle),
                      "Episode resumes must lead with their series name.")
        XCTAssertTrue(episodeResume.label.contains("S1 · E1") && episodeResume.label.contains(fixture.episodeTitle),
                      "Episode resumes must identify their season, episode, and title.")
        XCTAssertTrue(episodeResume.label.localizedCaseInsensitiveContains("remaining"),
                      "A resumed episode must report its remaining progress.")
        try attachFrame(named: "Home compact header and series-first resume shelf")

        // Duo's closed display scrolls its top chrome away with the hero.
        for _ in 0..<4 where !element("homeBrowse-movies").exists {
            try scrollLibrary(home, up: false)
        }
        for (homeID, libraryID) in [("movies", "movies"), ("tv", "tv"), ("anime", "anime"), ("music", "music"), ("books", "books")] {
            let category = element("homeBrowse-\(homeID)")
            if homeID == "anime" {
                XCTAssertFalse(category.exists, "This server has no Anime library.")
                continue
            }
            revealHomeMenuControl(category)
            try activate(category)
            let selected = element("libraryCategory-\(libraryID)")
            try waitForHittable(selected, timeout: 15, named: "Home category opens full Library")
            XCTAssertTrue(accessibilityText(selected).contains("Selected"),
                          "Home \(homeID) must open the same category as Library.")
            try selectDestination("Home", screen: "homeScreen")
        }

        let choice = element("featuredChoice-\(fixture.directItemID)")
        try revealInLibraryViewport([choice], in: home)
        try activate(choice)
        let heroTitle = element("featuredTitleButton")
        try waitForHittable(heroTitle, timeout: 15, named: "selected Home hero title")
        XCTAssertEqual(heroTitle.label, fixture.directItemTitle)
        let watchlist = element("featuredWatchlistButton")
        try revealInLibraryViewport([watchlist], in: home)
        try waitForHittable(watchlist, timeout: 15, named: "Home hero watchlist action")
        XCTAssertEqual(watchlist.value as? String, "Not in watchlist")
        XCTAssertFalse(element("featuredDetailsButton").exists,
                       "The compact Home hero must use its title as the details route instead of duplicating a Details button.")
        try activate(watchlist)
        try await waitForWatchlist(api, identity: identity, itemID: fixture.directItemID, included: true)
        XCTAssertTrue(waitUntil(timeout: 15) { (self.element("featuredWatchlistButton").value as? String) == "In watchlist" },
                      "Saving from the Home hero must update its watchlist action.")
        try activate(element("featuredWatchlistButton"))
        try await waitForWatchlist(api, identity: identity, itemID: fixture.directItemID, included: false)
        XCTAssertTrue(waitUntil(timeout: 15) { (self.element("featuredWatchlistButton").value as? String) == "Not in watchlist" },
                      "Removing from the Home hero must restore its watchlist action.")

        try revealInLibraryViewport([element("homeSearchButton")], in: home)
        try activate(element("homeSearchButton"))
        let searchScreen = element("homeSearchScreen")
        try waitForExistence(searchScreen, timeout: 15, named: "Home search")
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "Home search field")
        search.tap()
        search.typeText(fixture.directItemTitle)
        let result = element("homeSearchResult-\(fixture.directItemID)")
        try waitForHittable(result, timeout: 20, named: "Home search result")
        XCTAssertTrue(result.label.hasPrefix(fixture.directItemTitle),
                      "Home search must retain the matching media title before its subtitle metadata.")
        try attachFrame(named: "Home browse filters, concise episode resume, hero watchlist, and search")
        completed = true
        #else
        throw XCTSkip("The compact Home browse and Home search workflow applies to iOS.")
        #endif
    }

    func journeyNativeHomeLayoutReordersHidesAndPersists() async throws {
        #if os(iOS)
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let initial = [ProofHomeShelf(id: "continue", visible: true), ProofHomeShelf(id: "live", visible: false),
                       ProofHomeShelf(id: "next_up", visible: true), ProofHomeShelf(id: "new_in_library", visible: true)]
        _ = try await api.saveHomeLayout(identity: identity, shelves: initial)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Account", screen: "accountScreen")
        try activate(element("homeEditButton"))
        try waitForHittable(element("homeLayoutSaveButton"), timeout: 20, named: "loaded Home editor")
        try waitForHittable(element("homeShelfVisibility-next_up"), timeout: 15, named: "Home visibility choices")
        try setNativeSwitch("homeShelfVisibility-next_up", to: false)
        guard nativeSwitchValue(element("homeShelfVisibility-next_up")) == false else { throw ProofFailure.ui("The visibility switch did not turn off.") }
        try activate(element("homeLayoutCancelButton"))
        let cancelled = try await api.homeLayout(identity: identity)
        XCTAssertEqual(cancelled?.first { $0.id == "next_up" }?.visible, true, "Cancel saved an uncommitted Home edit.")

        try selectDestination("Account", screen: "accountScreen")
        try activate(element("homeEditButton"))
        try waitForHittable(element("homeLayoutSaveButton"), timeout: 20, named: "Home editor ready for reordering")
        XCTAssertEqual(nativeSwitchValue(element("homeShelfVisibility-next_up")), true, "Reopening the editor retained a cancelled visibility edit.")
        let handles = application!.buttons.matching(NSPredicate(format: "label BEGINSWITH[c] %@", "Reorder"))
            .allElementsBoundByIndex.filter { hasVisibleFrame($0) }
        XCTAssertGreaterThanOrEqual(handles.count, 4, "The full Home shelf editor is missing its first four row handles.")
        guard handles.count >= 4, let first = handles.first else { throw ProofFailure.ui("Missing Home row reorder handles.") }
        handles[3].press(forDuration: 0.8, thenDragTo: first)
        try waitForHittable(element("homeShelfVisibility-next_up"), timeout: 15, named: "Home visibility choices")
        try setNativeSwitch("homeShelfVisibility-next_up", to: false)
        guard nativeSwitchValue(element("homeShelfVisibility-next_up")) == false else { throw ProofFailure.ui("The visibility switch did not turn off after dragging.") }
        try attachFrame(named: "Home editor with reordered shelves and visibility controls")
        try activate(element("homeLayoutSaveButton"))
        guard disappears(element("homeLayoutEditor"), timeout: 20) else { throw ProofFailure.ui("Saving Home layout did not finish.") }
        let saved = try await api.homeLayout(identity: identity)
        let nativeIDs = Set(["continue", "next_up", "new_in_library"])
        XCTAssertEqual(saved?.filter { nativeIDs.contains($0.id) }.map(\.id), ["new_in_library", "continue", "next_up"])
        XCTAssertEqual(saved?.first { $0.id == "next_up" }?.visible, false)
        XCTAssertEqual(saved?.first { $0.id == "live" }?.visible, false, "Editing native rows changed another website row.")

        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 30, named: "Home after relaunch")
        try selectDestination("Account", screen: "accountScreen")
        try activate(element("homeEditButton"))
        try waitForHittable(element("homeLayoutSaveButton"), timeout: 20, named: "persisted Home editor")
        XCTAssertEqual(nativeSwitchValue(element("homeShelfVisibility-next_up")), false)
        let picked = element("homeShelfVisibility-picked_for_you")
        for _ in 0..<6 where !picked.isHittable { application!.swipeUp() }
        try waitForHittable(picked, timeout: 10, named: "Picked for you in the full Home editor")
        try setNativeSwitch("homeShelfVisibility-picked_for_you", to: false)
        try activate(element("homeLayoutSaveButton"))
        guard disappears(element("homeLayoutEditor"), timeout: 20) else { throw ProofFailure.ui("Saving a discovery shelf failed.") }
        let discoverySaved = try await api.homeLayout(identity: identity)
        XCTAssertEqual(discoverySaved?.count, 14)
        XCTAssertEqual(discoverySaved?.first { $0.id == "picked_for_you" }?.visible, false)
        try selectDestination("Account", screen: "accountScreen")
        try activate(element("homeEditButton"))
        let resetControl = element("homeLayoutResetButton")
        for _ in 0..<8 where !resetControl.isHittable { application!.swipeUp() }
        try activate(resetControl)
        try activate(element("homeLayoutSaveButton"))
        guard disappears(element("homeLayoutEditor"), timeout: 20) else { throw ProofFailure.ui("Resetting Home layout did not finish.") }
        let reset = try await api.homeLayout(identity: identity)
        XCTAssertNil(reset, "Reset did not restore the shared default layout.")
        completed = true
        #else
        throw XCTSkip("Home shelf reordering uses iOS drag gestures.")
        #endif
    }

    #if os(iOS)
    func journeyNativeSearchPresentation() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        for (destination, screen, category, searchButton) in [
            ("Requests", "requestsScreen", "", "requestsSearchButton"),
            ("Library", "audioScreen", "music", "musicSearchButton"),
            ("Library", "booksScreen", "books", "booksSearchButton"),
        ] {
            try activate(destinationControl(destination))
            if !category.isEmpty {
                try waitForHittable(element("libraryCategory-\(category)"), timeout: 15, named: "Library category")
                try activate(element("libraryCategory-\(category)"))
            }
            try waitForHittable(element(screen), timeout: 20, named: "Browse surface before Search")
            XCTAssertFalse(application!.searchFields.firstMatch.exists, "\(screen) must open without a search drawer.")
            try activate(element(searchButton))
            let search = application!.searchFields.firstMatch
            let cancel = application!.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close"])).firstMatch
            try waitForHittable(search, timeout: 10, named: "Search opened from navigation")
            search.typeText("Synthetic")
            try activate(cancel)
            XCTAssertTrue(disappears(search, timeout: 10), "Cancel must remove the search drawer on \(screen).")
            try attachFrame(named: "\(screen) after cancelling Search")
            try activate(element(searchButton))
            try waitForHittable(search, timeout: 10, named: "Reopened Search")
            XCTAssertFalse((search.value as? String ?? "").contains("Synthetic"), "Cancelled search text must be cleared.")
            try activate(cancel)
            XCTAssertTrue(disappears(search, timeout: 10))
        }
        completed = true
    }
    #endif

    #if os(iOS)
    func journeyAlbumArtworkColor() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let album = try revealMusicAlbum(fixture, named: "Album with generated cover art")
        try activate(album)
        try waitForHittable(element("musicPlayButton"), timeout: 25, named: "Album actions")
        try await Task.sleep(for: .seconds(1))
        let window = try applicationWindow()
        let image = window.screenshot().image
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.preferredRange = .standard
        let sample = UIGraphicsImageRenderer(size: CGSize(width: 1, height: 1), format: format).image { _ in
            image.draw(in: CGRect(x: -window.frame.width * 0.025, y: -window.frame.height * 0.90,
                                  width: window.frame.width, height: window.frame.height))
        }
        let data = try XCTUnwrap(sample.cgImage?.dataProvider?.data)
        let bytes = try XCTUnwrap(CFDataGetBytePtr(data))
        let channels = [Int(bytes[0]), Int(bytes[1]), Int(bytes[2])]
        XCTAssertGreaterThan(channels.max()! - channels.min()!, 12,
                             "The page outside the cover must inherit its color rather than neutral charcoal.")
        XCTAssertTrue(element("musicShuffleButton").isEnabled)
        try attachFrame(named: "Album page with dominant cover color")
    }

    func journeyMobileWelcome() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try waitForHittable(element("serverURLField"), timeout: 20, named: "Server address")
        XCTAssertFalse(element("connectButton").isEnabled, "Empty credentials must not submit.")
        XCTAssertFalse(element("connectionHelpSheet").exists)
        try attachFrame(named: "Mobile welcome with shared offline artwork")
        try activate(element("connectionHelpButton"))
        try waitForExistence(element("connectionHelpSheet"), timeout: 10, named: "Connection help")
        try activate(element("connectionHelpDoneButton"))
        XCTAssertTrue(disappears(element("connectionHelpSheet"), timeout: 10))
        try signIn(using: fixture)
        try waitForExistence(element("homeScreen"), timeout: 20, named: "Home after minimal sign-in")
    }

    func journeyRequestDetailLayout() async throws {
        let fixture = try loadFixture()
        let key = try XCTUnwrap(fixture.requestMovieKey)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Requests", screen: "requestsScreen")
        let movie = element("requestDetails-\(key)")
        try waitForHittable(movie, timeout: 30, named: "Requests hero")
        try activate(movie)
        try waitForHittable(element("requestDetailsCloseButton"), timeout: 20, named: "Request detail close")
        try await Task.sleep(for: .seconds(2))
        let window = try applicationWindow()
        let image = window.screenshot().image
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let railSample = UIGraphicsImageRenderer(size: CGSize(width: 1, height: 1), format: format).image { _ in
            image.draw(in: CGRect(x: -window.frame.width * 0.96, y: -window.frame.height * 0.10,
                                  width: window.frame.width, height: window.frame.height))
        }
        let pixels = try XCTUnwrap(railSample.cgImage?.dataProvider?.data)
        let bytes = try XCTUnwrap(CFDataGetBytePtr(pixels))
        let channels = [Int(bytes[0]), Int(bytes[1]), Int(bytes[2])]
        XCTAssertGreaterThan(channels.max()! - channels.min()!, 40,
                             "Generated colored artwork must reach behind the rail instead of a black safe-area strip.")
        try attachFrame(named: "Closed Duo request hero behind navigation rail")
        try activate(element("requestDetailsCloseButton"))
        XCTAssertTrue(disappears(element("requestDetailScreen"), timeout: 10))
    }
    #endif

    func journeyNativeRequestsDiscoverSelectAndSubmitThroughTheServer() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard let movieKey = fixture.requestMovieKey, let movieTitle = fixture.requestMovieTitle,
              let showKey = fixture.requestShowKey, let showTitle = fixture.requestShowTitle,
              let animeKey = fixture.requestAnimeKey, let animeTitle = fixture.requestAnimeTitle else {
            throw XCTSkip("Generate the Requests catalog proof fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = try await api.requestRecords(identity: identity)
        let showBefore = before.filter { $0.key == showKey }.map(\.id)

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Requests", screen: "requestsScreen")
        let requests = element("requestsScreen")
        try waitForHittable(requests, timeout: 30, named: "Requests discovery catalog")
        let movie = element("requestDetails-\(movieKey)")
        try waitForHittable(movie, timeout: 30, named: "Requests hero title")
        XCTAssertEqual(movie.label, movieTitle, "The Requests hero did not preserve its catalog title.")

        let show = try revealRequestPoster(showKey, named: showTitle)
        try waitForHittable(show, timeout: 15, named: "Requests show poster")
        XCTAssertEqual(show.label, showTitle, "Requests browse did not preserve the show poster title.")
        let shelfPeers = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "requestPoster-"))
            .allElementsBoundByIndex.filter { hasVisibleFrame($0) && $0.frame.intersects(show.frame) == false
                && abs($0.frame.midY - show.frame.midY) < show.frame.height / 2 }
        XCTAssertFalse(shelfPeers.isEmpty, "Requests must expose adjacent posters in a shelf.")
        if let peer = shelfPeers.first {
            XCTAssertEqual(show.frame.width, peer.frame.width, accuracy: 1)
            XCTAssertEqual(show.frame.height, peer.frame.height, accuracy: 1)
            XCTAssertEqual(show.frame.minY, peer.frame.minY, accuracy: 1)
        }
        try attachFrame(named: "Requests shelf with equal aligned posters")
        try activate(show)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "show request details")
        try waitForHittable(element("requestSeasonPicker"), timeout: 10, named: "show season selector")
        try activate(element("requestDetailsCloseButton"))
        guard disappears(element("requestDetailScreen"), timeout: 10) else {
            throw ProofFailure.ui("Cancelling a Requests detail did not dismiss it.")
        }
        let afterCancel = try await api.requestRecords(identity: identity)
        XCTAssertEqual(afterCancel.filter { $0.key == showKey }.map(\.id), showBefore,
                       "Cancelling a Requests detail submitted a server request.")

        try activate(element("requestsSearchButton"))
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "Requests search")
        try activate(search)
        search.typeText(animeTitle)
        let anime = element("requestPoster-\(animeKey)")
        try waitForHittable(anime, timeout: 20, named: "searched anime poster")
        try activate(anime)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "anime request details")
        try waitForHittable(element("requestSeasonPicker"), timeout: 20, named: "anime season selector")
        try activate(element("requestSeasonPicker"))
        try activate(menuAction("Choose seasons"))
        try revealDetailElement(
            element("requestSeason-1"),
            in: element("requestDetailScreen"),
            named: "season one request choice",
            requiresHittable: true
        )
        try activate(element("requestSeason-1"))
        try revealDetailElement(
            element("requestLanguagePicker"),
            in: element("requestDetailScreen"),
            named: "anime audio language selector",
            requiresHittable: true
        )
        try waitForHittable(element("requestLanguagePicker"), timeout: 10, named: "anime audio language selector")
        try activate(element("requestLanguagePicker"))
        try activate(menuAction("English dub"))
        try revealDetailElement(
            element("requestConfirmButton"),
            in: element("requestDetailScreen"),
            named: "request confirmation",
            requiresHittable: true
        )
        try waitForHittable(element("requestConfirmButton"), timeout: 10, named: "request confirmation")
        try activate(element("requestConfirmButton"))
        let submitted = try await waitForNativeRequest(api, identity: identity, key: animeKey)
        XCTAssertEqual(submitted.language, "dub", "The selected request language did not reach the server.")
        XCTAssertTrue(submitted.seasonNumbers.contains(1), "The selected season did not reach the server.")
        try waitForExistence(element("requestSubmittedStatus"), timeout: 15, named: "submitted request status")
        try activate(element("requestDetailsCloseButton"))
        guard disappears(element("requestDetailScreen"), timeout: 10) else {
            throw ProofFailure.ui("Closing submitted request details did not return to Requests.")
        }

        try activate(element("requestsOptionsMenu"))
        try activate(element("requestsMyListButton"))
        try waitForExistence(element("requestHistoryScreen"), timeout: 20, named: "My Requests history")
        let history = element("requestHistory-\(animeKey)")
        try waitForExistence(history, timeout: 20, named: "submitted request in My Requests")
        XCTAssertTrue(hasVisibleFrame(history), "The submitted request was outside the visible history.")
        XCTAssertTrue(history.staticTexts[animeTitle].exists || history.label.contains(animeTitle),
                      "My Requests did not show the submitted catalog title.")
        try attachFrame(named: "Native Requests browse, request controls, and submitted history")
        completed = true
    }

    func journeyNativeTitleLogoArtworkAndTextFallback() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 0)

        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("featuredControls"), timeout: 20, named: "Home carousel controls")
        XCTAssertFalse(element("featuredPauseButton").exists)

        #if os(tvOS)
        try selectTVFeaturedTitle(named: fixture.directItemTitle)
        #else
        let directChoice = element("featuredChoice-\(fixture.directItemID)")
        try revealInLibraryViewport([directChoice])
        try activate(directChoice)
        #endif
        let homeTitle = element("featuredTitleButton")
        #if os(tvOS)
        _ = try waitForPerformanceControl(homeTitle, timeout: 15, named: "logo-backed Home title")
        #else
        try waitForHittable(homeTitle, timeout: 15, named: "logo-backed Home title")
        #endif
        XCTAssertEqual(homeTitle.label, fixture.directItemTitle,
                       "A logo-backed title must retain the media name for accessibility.")
        let footer = element("featuredControls")
        #if os(tvOS)
        let homeAction = element("featuredWatchlistButton")
        _ = try waitForPerformanceControl(homeAction, timeout: 15, named: "logo-backed Home action")
        try waitForPerformanceContainer(footer, timeout: 15, named: "Home carousel footer")
        XCTAssertGreaterThan(footer.frame.minY, homeAction.frame.maxY,
                             "Home footer controls must remain below its actions, not over title artwork.")
        try activate(homeTitle)
        #else
        let details = element("featuredDetailsButton")
        try revealInLibraryViewport([details, footer])
        XCTAssertGreaterThan(footer.frame.minY, details.frame.maxY,
                             "Home footer controls must remain below Details, not over title artwork.")
        try activate(details)
        #endif
        try waitForExistence(element("detailScreen"), timeout: 20, named: "logo-backed title details")
        let detailLogo = element("titleLogoArtwork")
        try waitForExistence(detailLogo, timeout: 20, named: "detail title logo")
        XCTAssertEqual(detailLogo.label, fixture.directItemTitle,
                       "Detail title-logo artwork must expose the media name to accessibility.")
        try attachFrame(named: "Detail title-logo artwork with accessible title")
        try returnToLibrary()
        #if os(tvOS)
        _ = try waitForPerformanceControl(homeTitle, timeout: 15, named: "cached logo-backed Home title")
        #else
        try waitForHittable(homeTitle, timeout: 15, named: "cached logo-backed Home title")
        #endif
        // The Home logo is deliberately inside its title button and may merge into
        // that accessibility element. Detail exposes the image leaf above; by
        // returning only after it loads, the Home screenshot exercises the same
        // cached sidecar without depending on a private AX tree shape.
        try attachFrame(named: "Home title-logo artwork with accessible title")

        #if os(tvOS)
        try selectTVFeaturedTitle(named: fixture.convertedItemTitle)
        #else
        let convertedChoice = element("featuredChoice-\(fixture.convertedItemID)")
        try revealInLibraryViewport([convertedChoice])
        try activate(convertedChoice)
        #endif
        let fallbackHomeTitle = element("featuredTitleButton")
        #if os(tvOS)
        _ = try waitForPerformanceControl(fallbackHomeTitle, timeout: 20, named: "text-fallback Home title")
        #else
        try waitForHittable(fallbackHomeTitle, timeout: 20, named: "text-fallback Home title")
        #endif
        XCTAssertEqual(fallbackHomeTitle.label, fixture.convertedItemTitle)
        #if os(tvOS)
        try activate(fallbackHomeTitle)
        #else
        let fallbackDetails = element("featuredDetailsButton")
        try revealInLibraryViewport([fallbackDetails])
        try activate(fallbackDetails)
        #endif
        try waitForExistence(element("detailScreen"), timeout: 20, named: "text-fallback title details")
        let fallback = element("detailTitle")
        try waitForExistence(fallback, timeout: 20, named: "detail text fallback for title without logo")
        XCTAssertEqual(fallback.label, fixture.convertedItemTitle)
        XCTAssertTrue(disappears(element("titleLogoArtwork"), timeout: 10),
                      "A title without a logo sidecar must use the text fallback instead of stale artwork.")
        completed = true
    }

    func journeyNativeHomeDetailWatchlistPersistsThroughServer() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: false)

        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("featuredControls"), timeout: 20, named: "Home carousel controls")
        XCTAssertFalse(element("featuredPauseButton").exists)
        let choice = element("featuredChoice-\(fixture.directItemID)")
        try revealInLibraryViewport([choice])
        try activate(choice)
        guard waitUntil(timeout: 5, condition: { self.featuredTitleElement.label == fixture.directItemTitle }) else {
            throw ProofFailure.ui("The selected Home feature did not update before opening Details.")
        }
        let details = element("featuredDetailsButton")
        try revealInLibraryViewport([details])
        let controls = element("featuredControls")
        XCTAssertGreaterThan(controls.frame.minY, details.frame.maxY,
                             "The hero indicators belong below the actions, away from the artwork subject.")
        try attachFrame(named: "Home compact Lumina actions and footer indicators")
        try activate(details)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "Home feature details")

        let watchlist = element("videoWatchlistButton")
        #if os(iOS)
        try revealDetailControl(watchlist, named: "watchlist")
        #else
        try waitForHittable(watchlist, timeout: 15, named: "watchlist")
        #endif
        XCTAssertEqual(watchlist.value as? String, "Not in watchlist")
        try activate(watchlist)
        try await waitForWatchlist(api, identity: identity, itemID: fixture.directItemID, included: true)
        guard waitUntil(timeout: 15, condition: { (watchlist.value as? String) == "In watchlist" }) else {
            throw ProofFailure.ui("Adding the detail item did not update the native watchlist action.")
        }
        try attachFrame(named: "Movie detail Play and saved watchlist action")
        try activate(watchlist)
        try await waitForWatchlist(api, identity: identity, itemID: fixture.directItemID, included: false)
        guard waitUntil(timeout: 15, condition: { (watchlist.value as? String) == "Not in watchlist" }) else {
            throw ProofFailure.ui("Removing the detail item did not update the native watchlist action.")
        }
        try returnToLibrary()
        XCTAssertEqual(featuredTitleElement.label, fixture.directItemTitle,
                       "Changing the watchlist must preserve the selected Home feature.")
        completed = true
    }

    func journeyNativeMediaDetails() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.directItemID)
        let play = element("playButton")
        let watchlist = element("videoWatchlistButton")
        try waitForHittable(play, timeout: 20, named: "detail playback action")
        try waitForHittable(watchlist, timeout: 20, named: "detail watchlist action")
        XCTAssertEqual(play.frame.midY, watchlist.frame.midY, accuracy: 2,
                       "The primary actions must share a row with aligned centers, including TV focus enlargement.")
        try attachFrame(named: "Title detail hierarchy and aligned actions")
        #if os(iOS)
        let cast = element("detailCastSection")
        let related = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "related-")).firstMatch
        #else
        let cast = element("detailCastRail")
        let related = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "relatedTitle-")).firstMatch
        #endif
        try waitForExistence(cast, timeout: 20, named: "title cast credits")
        XCTAssertTrue(application!.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", "Synthetic Performer")).firstMatch.exists,
            "The details page must render server-provided cast credits.")
        #if os(iOS)
        try revealDetailControl(related, named: "related title")
        #else
        try waitForExistence(related, timeout: 20, named: "related title")
        guard focus(related, timeout: 20) else { throw ProofFailure.ui("Related titles must be reachable with the remote.") }
        #endif
        try attachFrame(named: "Detail cast and related titles")
        let relatedTitle = related.label
        try activate(related)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "related title details")
        XCTAssertTrue(waitUntil(timeout: 15) {
            self.application!.descendants(matching: .staticText).matching(identifier: "detailTitle")
                .allElementsBoundByIndex.contains { title in
                    title.exists && title.isHittable && !title.label.isEmpty
                        && title.label != fixture.directItemTitle && relatedTitle.contains(title.label)
                }
        }, "Selecting a related title must open that title’s details.")
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        #endif
        try waitForExistence(element("playButton"), timeout: 15, named: "original detail after Back")
        try returnToLibrary()
        completed = true
    }

    /// Exercises the currently shipped destinations in one app process so a
    /// sampler can correlate settled windows and repeated navigation without
    /// treating XCTest polling or screenshots as product work.
    func journeyNativePerformanceTour() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let startAt = try performanceTourStartPhase()

        try await performancePhase("login_form") {
            try launchAndResetSession()
            try waitForExistence(element("serverURLField"), timeout: 20, named: "connection form")
        }
        try await performanceQuietPhase("login_form_idle")
        try await performancePhase("sign_in_home") {
            #if os(tvOS)
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
            let details = element("featuredDetailsButton")
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
            try await performancePhase("tv_library_focus_sweep") {
                let fixtureMovie = element("libraryItem-\(fixture.directItemID)")
                guard focus(fixtureMovie, timeout: 15) else {
                    throw ProofFailure.ui("The TV movie grid could not focus the fixture title before the focus sweep.")
                }
                for _ in 0..<8 { XCUIRemote.shared.press(.down) }
                for _ in 0..<8 { XCUIRemote.shared.press(.right) }
                for _ in 0..<8 { XCUIRemote.shared.press(.up) }
                for _ in 0..<8 { XCUIRemote.shared.press(.left) }
                try waitForPerformanceContainer(element("libraryScreen"), timeout: 10, named: "TV movie catalog after focus sweep")
                guard focus(fixtureMovie, timeout: 20) else {
                    throw ProofFailure.ui("The TV movie grid could not restore focus to the fixture title after the focus sweep.")
                }
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
            let season = element("videoSeason-\(fixture.seasonID)")
            try revealDetailElement(
                season,
                in: element("videoCollectionScreen"),
                named: "show season",
                requiresHittable: true
            )
            try activate(season)
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
            try waitForPerformanceContainer(element("detailScreen"), timeout: 20, named: "episode detail")
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
            // With the chrome hidden, Down is the player-level shortcut for
            // settings. Once transport is visible, Settings is an explicitly
            // focusable control rather than a second meaning for Down.
            let beforeSettingsShortcut = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCUIRemote.shared.press(.down)
            try waitForPerformanceContainer(element("videoSettingsPanel"), timeout: 15, named: "video settings from Down")
            XCUIRemote.shared.press(.menu)
            XCTAssertTrue(disappears(element("videoSettingsPanel"), timeout: 10),
                          "Menu must close the Down-opened settings panel without closing playback.")
            _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= beforeSettingsShortcut + 1 }

            XCUIRemote.shared.press(.playPause)
            _ = try waitForPerformanceControl(element("videoPlayPauseButton"), timeout: 10, named: "paused video transport")
            try await performanceQuietPhase("video_paused_idle")
            try activate(element("videoSettingsButton"))
            #else
            try activate(element("videoControlsSurface"))
            try activate(element("videoPlayPauseButton"))
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
                element("videoPlayPauseButton"), timeout: 10, named: "paused MKV video transport"
            )
            #else
            try activate(element("videoControlsSurface"))
            try activate(element("videoPlayPauseButton"))
            #endif
            let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
            XCTAssertFalse(waitUntil(timeout: 3) {
                (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5
            }, "The MKV client-demux checkpoint must remain paused before settings.")
            try activate(element("videoSettingsButton"))
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
            try waitForPerformanceContainer(element("detailScreen"), timeout: 20, named: "audio track detail")
            try await performanceQuietPhase("audio_track_detail_idle")
            let time = try startPlayback(using: "playButton")
            #endif
            let playingAt = try waitForPlaybackValue(time, timeout: 20) { _ in true }
            _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= playingAt + 1 }
            try await performanceQuietPhase("audio_playing_idle")
            try revealMusicControl(element("audioListeningOptions"))
            try activate(element("audioListeningOptions"))
            try revealMusicControl(element("audioQueueButton"))
            try activate(element("audioQueueButton"))
            try waitForPerformanceContainer(element("audioQueueScreen"), timeout: 20, named: "audio queue")
            try await performanceQuietPhase("audio_queue_idle")
            try activate(element("audioQueueDoneButton"))
            try revealMusicControl(element("audioStopButton"))
            try activate(element("audioStopButton"))
            #if os(iOS)
            let sharedPlaylistID = try XCTUnwrap(
                fixture.sharedMusicPlaylistID,
                "Generate the shared music playlist proof fixture."
            )
            try musicBack()
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
            #if os(tvOS)
            let search = element("requestsSearchField")
            let readySearch = try waitForPerformanceControl(search, timeout: 20, named: "Requests search")
            try enter(movieTitle, into: readySearch)
            let searchResult = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "requestPoster-")
            ).firstMatch
            _ = try waitForPerformanceControl(searchResult, timeout: 30, named: "TV Requests search result")
            try await performanceQuietPhase("requests_search_idle")
            try replaceText(readySearch, with: "")
            let browse = element("requestsBrowseMenu")
            _ = try waitForPerformanceControl(browse, timeout: 20, named: "TV Requests browse controls")
            try await performanceQuietPhase("requests_browse_controls_idle")
            let detail = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "requestFeatured-")
            ).firstMatch
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
            try activate(element("requestsSearchButton"))
            let search = application!.searchFields.firstMatch
            try waitForHittable(search, timeout: 10, named: "Requests search")
            try enter(movieTitle, into: search)
            let searchResult = try revealRequestPoster(movieKey, named: "Requests movie search result")
            try waitForHittable(searchResult, timeout: 20, named: "Requests movie search result")
            try await performanceQuietPhase("requests_search_idle")
            // On OS 27 the navigation search field exposes a visible Close
            // button (rather than Cancel).  End the search before the next
            // coarse phase so the keyboard cannot cover the Account tab.
            let closeSearch = application!.navigationBars.buttons["Close"]
            try waitForHittable(closeSearch, timeout: 10, named: "Requests search close")
            try activate(closeSearch)
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
            try revealBookItem(element("bookCatalogButton"))
            try activate(element("bookCatalogButton"))
            let book = element("book-\(bookID)")
            try revealBookItem(book)
            try await performanceQuietPhase("books_catalog_idle")
            try activate(book)
            try waitForExistence(element("bookControlsButton"), timeout: 25, named: "book reader controls")
            try await performanceQuietPhase("books_reader_idle")
            try activate(element("bookControlsButton"))
            try activate(element("bookCloseButton"))
            try waitForExistence(element("booksScreen"), timeout: 15, named: "books catalog after prose reader")
            let comicID = try XCTUnwrap(fixture.comicBookID, "Generate the comic book proof fixture.")
            let comic = element("book-\(comicID)")
            try revealBookItem(comic)
            try await performanceQuietPhase("books_comic_catalog_idle")
            try activate(comic)
            try waitForExistence(element("bookPrimaryPage"), timeout: 25, named: "comic reader page")
            try await performanceQuietPhase("books_comic_reader_idle")
            try activate(element("bookControlsButton"))
            try activate(element("bookCloseButton"))
            try waitForExistence(element("booksScreen"), timeout: 15, named: "books catalog after comic reader")
            }
        }
        #endif

        if shouldRunPerformancePhase("destination_detail_back", startingAt: startAt) {
            for cycle in 1...5 {
                try await performancePhase("destination_detail_back", cycle: cycle) {
                    if cycle == 1 {
                        try openTourVideoItem(id: fixture.directItemID)
                    } else {
                        try openTourMovieFromLibraryRoot(id: fixture.directItemID)
                    }
                    _ = try waitForPerformanceControl(element("playButton"), timeout: 20, named: "movie detail in cycle \(cycle)")
                    let time = try startPlayback(using: "playButton")
                    let playingAt = try waitForPlaybackValue(time, timeout: 20) { _ in true }
                    _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= playingAt + 1 }
                    #if os(tvOS)
                    XCUIRemote.shared.press(.playPause)
                    _ = try waitForPerformanceControl(element("videoPlayPauseButton"), timeout: 10, named: "paused video transport in cycle \(cycle)")
                    try activate(element("videoSettingsButton"))
                    #else
                    try activate(element("videoControlsSurface"))
                    try activate(element("videoPlayPauseButton"))
                    try activate(element("videoSettingsButton"))
                    #endif
                    try waitForPerformanceContainer(element("videoSettingsPanel"), timeout: 15, named: "video settings in cycle \(cycle)")
                    try activate(element("videoSettingsTab-video"))
                    #if os(tvOS)
                    XCUIRemote.shared.press(.menu)
                    #else
                    try activate(element("videoSettingsDoneButton"))
                    #endif
                    try closePlayer()
                    try waitForPerformanceContainer(element("detailScreen"), timeout: 15, named: "movie detail after cycle \(cycle) player close")
                    try returnTourToLibrary()
                }
                try await performanceQuietPhase("library_cycle_idle", cycle: cycle)
            }
        }
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
        guard let startAt,
              let selectedIndex = Self.performanceTourPhases.firstIndex(of: startAt),
              let phaseIndex = Self.performanceTourPhases.firstIndex(of: phase) else {
            return true
        }
        return phaseIndex >= selectedIndex
    }

    private static let performanceTourPhases = [
        "login_form",
        "sign_in_home",
        "home_feature_detail",
        "library_catalog",
        "movie_detail_cast_related",
        "show_and_season",
        "video_play_pause_settings_close",
        "mkv_client_demux_play_pause_settings_close",
        "audio_album_player_settings_queue",
        "requests_detail_history",
        "account",
        "books_reader",
        "destination_detail_back",
    ]

    private func performanceQuietPhase(_ phase: String, cycle: Int? = nil) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", cycle: cycle, started: started)
        try await Task.sleep(for: .seconds(2))
        emitPerformanceMarker(phase: phase, event: "ready", cycle: cycle, started: started)
        // Keep the final app process alive long enough for an app-scoped leak
        // scan and delayed decoder/session teardown measurement.
        let quietSeconds = phase == "library_cycle_idle" && cycle == 5 ? 45 : 8
        try await Task.sleep(for: .seconds(quietSeconds))
        emitPerformanceMarker(phase: phase, event: "end", cycle: cycle, started: started)
    }

    private func performancePhase(_ phase: String, cycle: Int? = nil,
                                  operation: @MainActor () async throws -> Void) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", cycle: cycle, started: started)
        try await operation()
        emitPerformanceMarker(phase: phase, event: "ready", cycle: cycle, started: started)
        emitPerformanceMarker(phase: phase, event: "end", cycle: cycle, started: started)
    }

    /// tvOS reports an unfocused remote control as non-hittable even when it is
    /// visibly ready. `activate` below still must route actual remote focus.
    private func waitForPerformanceControl(_ control: XCUIElement, timeout: TimeInterval,
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

    private func emitPerformanceMarker(phase: String, event: String, cycle: Int?, started: ContinuousClock.Instant) {
        let duration = started.duration(to: .now).components
        let elapsed = Double(duration.seconds) * 1_000 + Double(duration.attoseconds) / 1e15
        var marker: [String: Any] = [
            "event": event,
            "elapsed_ms": Int(elapsed.rounded()),
            "phase": phase,
            "timestamp": Date().timeIntervalSince1970,
        ]
        if let cycle { marker["cycle"] = cycle }
        guard let data = try? JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else { return }
        print("LUMINA_PERF \(text)")
    }

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
        try activate(element("libraryCategory-tv"))
        #else
        try activate(element("homeBrowse-tv"))
        #endif
        try activate(element("libraryItem-\(fixture.showID)"))
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons for playback actions")
        #if os(tvOS)
        try waitForExistence(element("videoCollectionItem-\(fixture.seasonID)"), timeout: 20,
                             named: "inline show season selector")
        let firstEpisode = element("videoCollectionItem-\(fixture.episodeID)")
        #else
        try activate(element("videoSeason-\(fixture.seasonID)"))
        let firstEpisode = element("videoEpisode-\(fixture.episodeID)")
        #endif
        try waitForHittable(firstEpisode, timeout: 20, named: "first episode for playback actions")
        try activate(firstEpisode)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "first episode details for playback actions")
        let time = try startPlayback(using: "playButton")
        let intro = element("skipIntroButton")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 1 }
        XCTAssertFalse(element("nextEpisodeButton").exists,
                       "The contextual Next episode action should wait for credits or completion.")
        try waitForExistence(element("nextEpisodeTransportButton"), timeout: 8, named: "manual next episode transport")

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused episode transport")
        let closeFrame = element("closePlayerButton").frame
        let settingsFrame = element("videoSettingsButton").frame
        let muteFrame = element("videoMuteButton").frame
        let viewport = application!.windows.firstMatch.frame
        XCTAssertLessThan(closeFrame.minX - viewport.minX, viewport.width * 0.06,
                          "The player toolbar must stay at the screen edge, without compounded safe-area insets.")
        XCTAssertLessThan(closeFrame.minY - viewport.minY, viewport.height * 0.06)
        XCTAssertEqual(closeFrame.midY, settingsFrame.midY, accuracy: 2)
        XCTAssertEqual(settingsFrame.midY, muteFrame.midY, accuracy: 2)
        XCTAssertEqual(closeFrame.minX - viewport.minX, viewport.maxX - muteFrame.maxX, accuracy: 2,
                       "The toolbar must have balanced leading and trailing margins.")
        XCTAssertGreaterThan(intro.frame.minY, viewport.midY,
                             "Contextual episode actions belong near the playback timeline.")
        try attachPlaybackFrame(named: "TV playback — aligned edge toolbar and contextual action")
        #else
        let pause = element("videoPlayPauseButton")
        if !pause.isHittable { try activate(element("videoControlsSurface")) }
        try waitForHittable(pause, timeout: 8, named: "episode pause control")
        try activate(pause)
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
        let next = element("nextEpisodeButton")
        try waitForHittable(next, timeout: 15, named: "next episode action")
        try attachPlaybackFrame(named: "Episode intro, credits, and next actions")
        try activate(credits)
        XCTAssertTrue(disappears(credits, timeout: 15), "Skipping credits must leave the recorded credits range.")
        try waitForHittable(next, timeout: 15, named: "next episode action after completion")
        try activate(next)
        try waitForExistence(application!.staticTexts[fixture.secondEpisodeTitle], timeout: 20, named: "next episode title")
        XCTAssertTrue(waitUntil(timeout: 15) { (Double(time.value as? String ?? "") ?? 90) < 5 },
                      "Next episode must begin at its own start position.")
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 1 }
        XCTAssertFalse(element("videoActionError").exists, "The next episode action must keep native playback usable.")
        try closePlayer()
        completed = true
    }

    func journeyVideoOverlayCentersAndDismisses() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        #if os(iOS)
        try selectDestination("Video", screen: "libraryScreen")
        try activate(element("libraryCategory-tv"))
        try activate(element("libraryItem-\(fixture.showID)"))
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons")
        try activate(element("videoSeason-\(fixture.seasonID)"))
        try activate(element("videoEpisode-\(fixture.episodeID)"))
        try waitForExistence(element("detailScreen"), timeout: 20, named: "episode details")
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
            XCTAssertEqual(transport.frame.midX, application!.windows.firstMatch.frame.midX, accuracy: 2,
                           "The tabletop transport must remain centered with Next Episode present.")
            XCTAssertTrue(element("nextEpisodeTransportButton").exists)
            try attachPlaybackFrame(named: "Centered tabletop episode transport")
            completed = true
            return
        }
        let background = application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 8))
        try attachPlaybackFrame(named: "Video after controls and system status auto-hide")
        background.tap()
        let transport = application!.buttons["videoPlayPauseButton"]
        transport.tap()
        XCTAssertEqual(transport.label, "Play", "The dismiss check must run while playback is paused.")
        #else
        if !element("videoPlayPauseButton").exists { try activate(element("videoControlsSurface")) }
        let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 15, named: "video transport")
        try activate(transport)
        #endif
        let viewport = application!.windows.firstMatch.frame
        XCTAssertEqual(transport.frame.midX, viewport.midX, accuracy: 2,
                       "The video transport must be centered over the full video viewport.")
        let chrome = element("videoPlaybackChrome").frame
        XCTAssertEqual(chrome.midX, viewport.midX, accuracy: 2,
                       "Video chrome must share the video's horizontal center.")
        #if os(iOS)
        XCTAssertTrue(element("nextEpisodeTransportButton").exists,
                      "Centering must hold when an episode has a next action.")
        let close = element("closePlayerButton").frame
        let mute = element("videoMuteButton").frame
        let settings = element("videoSettingsButton").frame
        XCTAssertEqual(close.minX - viewport.minX, viewport.maxX - mute.maxX, accuracy: 3,
                       "The top controls must have balanced margins around system UI.")
        XCTAssertEqual(close.midY, settings.midY, accuracy: 2)
        XCTAssertEqual(settings.midY, mute.midY, accuracy: 2)
        #endif
        try attachPlaybackFrame(named: "Centered video overlay")
        #if os(iOS)
        application!.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 3),
                      "Tapping the video background must dismiss paused controls immediately.")
        try activate(element("videoControlsSurface"))
        try waitForHittable(element("videoPlayPauseButton"), timeout: 3, named: "revealed video transport")
        #else
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 3))
        try activate(element("videoControlsSurface"))
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
            XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 10),
                          "Playing video must hide its transport controls after inactivity.")
            XCTAssertFalse(element("closePlayerButton").exists, "Close must hide with the transport controls.")
            try attachPlaybackFrame(named: "Unobstructed video after controls auto-hide")
            #if os(tvOS)
            XCUIRemote.shared.press(.down)
            #else
            try activate(element("videoControlsSurface"))
            try activate(element("videoPlayPauseButton"))
            try activate(element("videoSettingsButton"))
            #endif
            try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "playback settings")
            #if os(tvOS)
            XCUIRemote.shared.press(.playPause)
            XCTAssertTrue(waitUntil(timeout: 3) { self.element("videoSettingsTab-info").hasFocus },
                          "Opening playback settings must land on its initial category.")
            #endif
            try activate(element("videoSettingsTab-video"))
            try activate(element("videoFitPicker"))
            try activate(menuAction("Fill"))
            try activate(element("videoFitPicker"))
            try activate(menuAction("Fit"))
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
            try activate(size)
            try activate(menuAction("Large"))
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
            XCTAssertTrue(waitUntil(timeout: 3) { self.element("videoSettingsButton").hasFocus },
                          "Closing settings must return TV focus to its trigger.")
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
            try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused video transport")
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
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.convertedItemID)
        let time = try startPlayback(using: "playButton")
        let startedAt = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= startedAt + 1 }

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused native MKV transport")
        #else
        try activate(element("videoControlsSurface"))
        let pause = element("videoPlayPauseButton")
        try waitForHittable(pause, timeout: 8, named: "native MKV pause control")
        try activate(pause)
        #endif
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5 },
                       "Playback must stay paused while reconfiguring quality and subtitle timing.")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu) // Down opens settings from unobstructed playback.
        XCUIRemote.shared.press(.down)
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
        try waitForHittable(quality, timeout: 10, named: "720p stream quality")
        try activate(quality)
        try activate(menuAction("Up to 720p"))
        XCTAssertTrue(waitUntil(timeout: 45) {
            (quality.value as? String)?.contains("720p") == true && !self.element("videoStreamChanging").exists
        }, "Selecting 720p must repackage the paused item and retain the setting.")
        XCTAssertTrue(element("videoSettingsPanel").exists, "Quality selection must keep playback settings open.")
        XCTAssertFalse(element("videoStreamError").exists, "The supported 720p request must not discard the paused MKV.")

        #if os(iOS)
        if !element("videoSettingsTab-subtitles").isHittable {
            element("videoSettingsTab-video").swipeLeft()
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
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused chapter fixture transport")
        #else
        try activate(element("videoControlsSurface"))
        let pause = element("videoPlayPauseButton")
        try waitForHittable(pause, timeout: 8, named: "chapter fixture pause control")
        try activate(pause)
        #endif
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) { (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5 },
                       "Playback must stay paused while changing speed, chapters, and quality.")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu) // Hide the paused transport before opening the settings panel.
        XCUIRemote.shared.press(.down)
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
        XCUIRemote.shared.press(.playPause)
        #else
        try activate(element("videoSettingsDoneButton"))
        if !element("videoPlayPauseButton").isHittable {
            try activate(element("videoControlsSurface"))
        }
        try activate(element("videoPlayPauseButton"))
        #endif
        let resumedControl = element("videoPlayPauseButton")
        try waitForExistence(resumedControl, timeout: 8, named: "resumed chapter fixture transport")
        XCTAssertTrue(waitUntil(timeout: 8) { resumedControl.label == "Pause" },
                      "Resuming after a paused quality replacement must restore the Pause transport state.")
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= replacementTime + 1 }
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after paused chapter playback")
        completed = true
    }

    private func revealRequestPoster(_ key: String, named name: String) throws -> XCUIElement {
        let poster = element("requestPoster-\(key)")
        #if os(iOS)
        let requests = element("requestsScreen")
        try waitForExistence(requests, timeout: 15, named: "Requests browse catalog")
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

    private func replaceText(_ field: XCUIElement, with text: String) throws {
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

    private func revealDetailElement(
        _ element: XCUIElement,
        in detail: XCUIElement,
        named name: String,
        requiresHittable: Bool
    ) throws {
        #if os(iOS)
        let viewport = detail.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
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

    private func waitForNativeRequest(_ api: ProofAPI, identity: APIIdentity, key: String) async throws -> APIRequestRecord {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if let record = try await api.requestRecords(identity: identity).first(where: { $0.key == key }) {
                return record
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw ProofFailure.timeout("The submitted request did not persist through the native Requests API.")
    }

    private func waitForWatchlist(_ api: ProofAPI, identity: APIIdentity, itemID: String, included: Bool) async throws {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            if try await api.watchlistIncluded(identity: identity, itemID: itemID) == included { return }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw ProofFailure.timeout("The detail watchlist action did not persist through the native Watchlist API.")
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
        if let cachedFixture { return cachedFixture }
        let bundle = Bundle(for: Self.self)
        if let config = bundle.url(forResource: "FixtureBroker", withExtension: "json") {
            let settings = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: config))
            guard let value = settings["brokerURL"], let broker = URL(string: value) else { throw ProofFailure.invalidFixture }
            _ = try localOrigin(from: "http://\(broker.host ?? ""):\(broker.port ?? 80)")
            let url = broker.appendingPathComponent(String(describing: type(of: self)))
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
        app.launchEnvironment["LUMINA_UI_TEST_NAMESPACE"] = isolationID
        app.launch()

        let loginField = element("serverURLField")
        #if os(tvOS)
        let destinations = ["homeScreen", "libraryScreen", "audioScreen", "deviceSettingsScreen"]
        #else
        let destinations = ["homeScreen", "libraryScreen", "audioScreen", "accountScreen"]
        #endif
        guard appears(loginField, timeout: 10) || waitUntil(timeout: 30, condition: {
            destinations.contains { self.hasVisibleFrame(self.element($0)) }
        }) else {
            throw ProofFailure.ui("The app did not reach the connection form or a visible destination after launch.")
        }

        if !hasVisibleFrame(loginField) {
            try signOutThroughAccount()
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
        passwordField.typeText("\n") // Use Go when the software keyboard covers Connect on closed Duo.
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
        try waitForPostLoginAccountDestination()
        #endif
    }

    #if os(iOS)
    private func waitForPostLoginAccountDestination() throws {
        let prompt = application!.sheets["Save Password?"]
        let promptTitle = application!.staticTexts["Save Password?"]
        let decline = application!.buttons["Not Now"]

        // Saving a password is an OS-owned interruption. A direct tap can wait
        // for the sheet's dismissal before returning, so it must not share the
        // same predicate deadline as the destination assertion.
        guard waitUntil(timeout: 25, condition: {
            if prompt.exists || promptTitle.exists { return true }
            let account = self.destinationControl("Account")
            return self.hasVisibleFrame(account) && account.isEnabled && account.isHittable
        }) else {
            throw ProofFailure.timeout("Timed out waiting for password storage or the Account tab after login.")
        }
        if prompt.exists || promptTitle.exists {
            guard waitUntil(timeout: 10, condition: {
                self.hasVisibleFrame(decline) && decline.isEnabled && decline.isHittable
            }) else {
                throw ProofFailure.timeout("Timed out waiting for the password-storage decline action.")
            }
            decline.tap()
            guard waitUntil(timeout: 25, condition: { !prompt.exists && !promptTitle.exists }) else {
                throw ProofFailure.timeout("Password storage did not dismiss after declining it.")
            }
        }

        // The system sheet replaces the accessibility snapshot. Resolve the
        // destination only after it is gone instead of retaining a stale row.
        guard waitUntil(timeout: 25, condition: {
            let account = self.destinationControl("Account")
            return self.hasVisibleFrame(account) && account.isEnabled && account.isHittable
        }) else {
            throw ProofFailure.timeout("Timed out waiting for hittable Account tab after declining password storage.")
        }
    }
    #endif

    private func nativeSwitchValue(_ control: XCUIElement) -> Bool? {
        if let value = control.value as? NSNumber { return value.boolValue }
        switch (control.value as? String)?.lowercased() {
        case "1", "on", "true": return true
        case "0", "off", "false": return false
        default: return nil
        }
    }

    private func setNativeSwitch(_ identifier: String, to enabled: Bool) throws {
        #if os(iOS)
        if identifier.hasPrefix("homeShelfVisibility-") {
            try activate(element("\(identifier)-\(enabled ? "on" : "off")"))
            XCTAssertEqual(nativeSwitchValue(element(identifier)), enabled)
            return
        }
        let native = application!.switches.matching(identifier: identifier).firstMatch
        #else
        let native = element(identifier)
        #endif
        try waitForExistence(native, timeout: 10, named: "native \(identifier) switch")
        guard nativeSwitchValue(native) != nil else {
            throw ProofFailure.ui("The native \(identifier) switch did not expose an on/off accessibility value.")
        }
        if nativeSwitchValue(native) != enabled {
            #if os(iOS)
            try waitForHittable(native, timeout: 10, named: identifier)
            // A SwiftUI Form switch's frame may include its label. Hit the trailing
            // physical switch instead of the row's central activation point.
            let inset = min(26, native.frame.width / 2)
            native.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 0.5))
                .withOffset(CGVector(dx: -inset, dy: 0)).tap()
            #else
            try activate(native)
            #endif
        }
        guard waitUntil(timeout: 5, condition: { self.nativeSwitchValue(native) == enabled }) else {
            try attachFrame(named: "Native filter switch did not change")
            throw ProofFailure.ui("The \(identifier) switch did not change to \(enabled ? "on" : "off"); value=\(native.value ?? "missing").")
        }
    }

    private var featuredTitleElement: XCUIElement { element("featuredTitleButton") }

    #if os(tvOS)
    private func selectTVFeaturedTitle(named expectedTitle: String) throws {
        let title = featuredTitleElement
        _ = try waitForPerformanceControl(title, timeout: 20, named: "featured Home title")
        guard focus(title, timeout: 20) else {
            throw ProofFailure.ui("The Siri Remote could not focus the featured Home title.")
        }
        for _ in 0..<8 {
            if title.label == expectedTitle { return }
            let previousTitle = title.label
            XCUIRemote.shared.press(.right)
            _ = waitUntil(timeout: 5) { title.label != previousTitle }
        }
        guard title.label == expectedTitle else {
            throw ProofFailure.ui("The Home carousel did not reach the expected title \(expectedTitle).")
        }
    }
    #endif

    private func destinationControl(_ requestedTitle: String) -> XCUIElement {
        #if os(tvOS)
        let identifier = switch requestedTitle {
        case "Home": "homeMasthead"
        case "Library", "Video": "homeBrowse-movies"
        case "Audio": "homeBrowse-music"
        case "Requests": "requestsButton"
        case "Account": "accountButton"
        default: requestedTitle
        }
        return element(identifier)
        #else
        let title = requestedTitle == "Video" ? "Library" : requestedTitle
        let identifier = "\(title.lowercased())Tab"
        // A compact shell can retain offscreen sidebar rows in accessibility.
        // Prefer only visible destinations, including native sidebar cells on iPad.
        if let identified = application!.descendants(matching: .any).matching(identifier: identifier)
            .allElementsBoundByIndex.first(where: { hasVisibleFrame($0) && $0.isHittable }) { return identified }
        let nativeTab = application!.tabBars.buttons[title]
        if hasVisibleFrame(nativeTab) && nativeTab.isHittable { return nativeTab }
        let label = NSPredicate(format: "label == %@", title)
        let sidebarCells = application!.collectionViews.cells.containing(.staticText, identifier: identifier).allElementsBoundByIndex
            + application!.tables.cells.containing(.staticText, identifier: identifier).allElementsBoundByIndex
            + application!.collectionViews.cells.matching(label).allElementsBoundByIndex
            + application!.tables.cells.matching(label).allElementsBoundByIndex
        if let row = sidebarCells.first(where: { hasVisibleFrame($0) && $0.isHittable }) { return row }
        return application!.descendants(matching: .any).matching(label).firstMatch
        #endif
    }

    private func signOutThroughAccount() throws {
        #if os(iOS)
        try selectDestination("Account", screen: "accountScreen")
        let signOut = element("signOutButton")
        #else
        try selectDestination("Account", screen: "accountScreen")
        try waitForExistence(element("deviceSettingsScreen"), timeout: 10, named: "Device Settings before sign out")
        try selectDeviceSettingsCategory("account")
        let signOut = element("deviceSignOutButton")
        #endif
        try waitForExistence(signOut, timeout: 10, named: "account sign-out action")
        #if os(iOS)
        try revealInLibraryViewport([signOut], in: element("accountScreen"))
        #endif
        try activate(signOut)
    }

    private func selectDestination(_ title: String, screen: String) throws {
        #if os(tvOS)
        switch title {
        case "Home":
            try selectTVLibraryCategory("all", screen: screen)
            return
        case "Library", "Video":
            try selectTVLibraryCategory("movies", screen: screen)
            return
        case "Audio":
            try selectTVLibraryCategory("music", screen: screen)
            return
        case "Books":
            throw ProofFailure.ui("Books are intentionally unavailable on tvOS.")
        default:
            break
        }
        let destinationScreen = title == "Account" ? "deviceSettingsScreen" : screen
        if hasVisibleFrame(element(destinationScreen)) { return }
        try activate(destinationControl(title))
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(destinationScreen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the visible \(title) destination.")
        }
        return
        #endif
        if hasVisibleFrame(element(screen)) { return }
        #if os(iOS)
        // iPhone groups media kinds under Library rather than giving Audio and
        // Video independent tabs. Keep legacy proof journeys on the real
        // category rail instead of searching for a nonexistent tab.
        switch title {
        case "Video":
            try selectTourLibraryCategory("movies", screen: screen)
            return
        case "Audio":
            try selectTourLibraryCategory("music", screen: screen)
            return
        case "Books":
            try selectTourLibraryCategory("books", screen: screen)
            return
        default:
            break
        }
        #endif
        try activate(destinationControl(title))
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the visible \(title) destination.")
        }
    }

    #if os(tvOS)
    private func selectTVLibraryCategory(_ category: String, screen: String) throws {
        let control = element("homeBrowse-\(category)")
        try waitForExistence(control, timeout: 15, named: "TV \(category) category")
        if !accessibilityText(control).contains("Selected") || !hasVisibleFrame(element(screen)) {
            try activate(control)
        }
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the TV Library \(category) category.")
        }
    }
    #endif

    /// The performance tour must start each iOS Library category at the shell
    /// root.  The production shell intentionally keeps its own NavigationStack
    /// per tab, so a plain tab selection doesn't pop a prior episode or album.
    private func selectTourVideoCategory(_ category: String) throws {
        #if os(iOS)
        try selectTourLibraryCategory(category, screen: "libraryScreen")
        #else
        try selectTVLibraryCategory(category, screen: "libraryScreen")
        #endif
    }

    private func selectTourAudioRoot() throws {
        #if os(iOS)
        try selectTourLibraryCategory("music", screen: "audioScreen")
        #else
        try selectDestination("Audio", screen: "audioScreen")
        #endif
    }

    #if os(iOS)
    private func selectTourBooksRoot() throws {
        try selectTourLibraryCategory("books", screen: "booksScreen")
    }

    private func selectTourLibraryCategory(_ category: String, screen: String) throws {
        try unwindTourLibraryNavigationStack()
        let categories = element("libraryCategories")
        try waitForExistence(categories, timeout: 15, named: "Library categories")
        let control = element("libraryCategory-\(category)")
        for _ in 0..<8 {
            if control.exists, hasVisibleFrame(control), control.isHittable {
                try activate(control)
                guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
                    throw ProofFailure.timeout("The Library \(category) category did not open \(screen).")
                }
                return
            }
            // The rail keeps its horizontal offset when returning from Books.
            // Earlier categories must move back into view; later ones move
            // forward. This remains bounded to the five known categories.
            if category == "movies" || category == "tv" {
                categories.swipeRight()
            } else {
                categories.swipeLeft()
            }
        }
        throw ProofFailure.ui("The Library \(category) category could not be revealed in its horizontal category rail.")
    }

    private func unwindTourLibraryNavigationStack() throws {
        let categories = element("libraryCategories")
        if !hasVisibleFrame(categories) {
            try activate(destinationControl("Library"))
        }
        for _ in 0..<4 {
            if hasVisibleFrame(categories) { return }
            let back = application!.buttons.matching(identifier: "BackButton").firstMatch
            guard back.exists else { break }
            try activate(back)
        }
        guard waitUntil(timeout: 15, condition: { self.hasVisibleFrame(categories) }) else {
            throw ProofFailure.ui("The Library navigation stack did not return to its category root.")
        }
    }
    #endif

    private func openTourVideoItem(id: String) throws {
        try selectTourVideoCategory("movies")
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

    /// Repeated close cycles deliberately begin at the verified Library root left
    /// by the prior cycle, avoiding unrelated tab and category navigation.
    private func openTourMovieFromLibraryRoot(id: String) throws {
        guard !id.isEmpty else { throw ProofFailure.invalidFixture }
        #if os(iOS)
        guard hasVisibleFrame(element("libraryCategories")) else {
            throw ProofFailure.ui("A repeated video cycle did not return to the Library root.")
        }
        let item = try revealLibraryItem(id: id, named: "library movie at root")
        #else
        guard hasVisibleFrame(element("libraryScreen")) else {
            throw ProofFailure.ui("A repeated video cycle did not return to the Video library root.")
        }
        let item = element("libraryItem-\(id)")
        try waitForExistence(item, timeout: 25, named: "library movie at root")
        #endif
        try activate(item)
        try waitForExistence(element("detailScreen"), timeout: 25, named: "detail screen")
    }

    private func openLibraryItem(id: String) throws {
        try selectDestination("Video", screen: "libraryScreen")
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
    private func revealInLibraryViewport(_ elements: [XCUIElement], in container: XCUIElement? = nil) throws {
        let library = container ?? (element("homeScreen").exists ? element("homeScreen") : element("libraryScreen"))
        let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        for _ in 0..<8 {
            let frames = elements.map(\.frame)
            if frames.allSatisfy({ frame in
                let visible = frame.intersection(viewport)
                return !visible.isNull && visible.height >= min(44, frame.height)
            }) { return }
            guard let leastVisible = frames.min(by: {
                $0.intersection(viewport).height < $1.intersection(viewport).height
            }) else { return }
            try scrollLibrary(library, up: leastVisible.midY >= viewport.midY)
        }
        throw ProofFailure.ui("The library item could not be scrolled into the visible library.")
    }
    #elseif os(tvOS)
    private func revealInLibraryViewport(_ elements: [XCUIElement], in container: XCUIElement? = nil) throws {
        for element in elements {
            try waitForHittable(element, timeout: 15, named: element.identifier)
        }
    }
    #endif

    #if os(iOS)
    private func isFullyVerticallyContained(_ frame: CGRect, in viewport: CGRect) -> Bool {
        let tolerance: CGFloat = 1
        return !frame.isEmpty
            && frame.minY >= viewport.minY - tolerance
            && frame.maxY <= viewport.maxY + tolerance
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
        let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        let anchor = library.buttons.firstMatch
        let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : viewport.midX
        try dragVertically(in: viewport, anchorX: anchorX, up: up)
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

    private func resumeNativePlayer(pausedAt: Double) throws -> Double {
        // The native AV layer is intentionally hidden from accessibility and does
        // not accept test input. Reveal Lumina's transport, then resume through
        // its real user-facing action.
        try waitForExistence(element("nativeVideoPlayer"), timeout: 10, named: "native player after returning from Home")
        let controlsSurface = application!.buttons.matching(identifier: "videoControlsSurface").firstMatch
        let play = application!.buttons.matching(identifier: "videoPlayPauseButton").firstMatch
        if !play.isHittable {
            try activate(controlsSurface)
        }
        try waitForHittable(play, timeout: 10, named: "resumed native video transport")
        XCTAssertEqual(play.label, "Play", "Returning from Home must leave native playback paused until an explicit resume.")
        try activate(play)
        return try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= pausedAt + 1 }
    }

    private func pauseAndResumeNativePlayer(after directAdvanced: Double) async throws {
        let beforeHome = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { $0 >= directAdvanced }
        try backgroundApplication()
        try await Task.sleep(for: .seconds(3))
        application!.activate()
        try waitForExistence(element("playbackScreen"), timeout: 20, named: "player after returning from Home")
        let afterHome = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(afterHome, beforeHome + 2, "Playback continued while the app was in the background.")
        try await Task.sleep(for: .seconds(3))
        let stillPaused = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, afterHome + 1, "Returning to the app resumed playback without an explicit action.")
        _ = try resumeNativePlayer(pausedAt: stillPaused)
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
        if buttonIdentifier == "convertButton" {
            let actions = element("titleActionsMenu")
            try waitForExistence(actions, timeout: 20, named: "title actions menu")
            #if os(iOS)
            try revealDetailControl(actions, named: "title actions menu")
            #endif
            try activate(actions)
        }
        let button = element(buttonIdentifier)
        try waitForExistence(button, timeout: 20, named: buttonIdentifier)
        #if os(iOS)
        if buttonIdentifier != "convertButton" {
            try revealDetailControl(button, named: buttonIdentifier)
        }
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
            if isFullyVerticallyContained(frame, in: viewport),
               hasVisibleFrame(control), control.isEnabled, control.isHittable {
                return
            }
            let anchor = element("playButton")
            let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : frame.midX
            try dragVertically(in: viewport, anchorX: anchorX, up: frame.midY >= viewport.midY)
        }

        throw ProofFailure.ui(
            "The \(name) control could not be revealed in its detail screen: "
                + "control=\(control.frame), viewport=\(viewport), hittable=\(hasVisibleFrame(control) && control.isHittable)."
        )
    }

    private func revealConnectionField(_ field: XCUIElement) throws {
        let form = application!.scrollViews.firstMatch
        try waitForExistence(form, timeout: 10, named: "connection form scroll view")
        let viewport = form.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        for _ in 0..<8 {
            if hasVisibleFrame(field), field.isHittable { return }
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
        let audioPlayer = element("audioPlayerStage").exists
        let closeButton = audioPlayer
            ? application!.buttons.matching(identifier: "BackButton").firstMatch
            : application!.buttons.matching(identifier: "closePlayerButton").firstMatch
        if audioPlayer {
            try waitForExistence(closeButton, timeout: 10, named: "iOS player back button")
            try activate(closeButton)
        } else {
            let controlsSurface = application!.buttons.matching(identifier: "videoControlsSurface").firstMatch
            if controlsSurface.label == "Show playback controls" || !closeButton.exists {
                try activate(controlsSurface)
            }
            if let visibleClose = try? waitForHittable(closeButton, timeout: 2, named: "visible iOS player close button") {
                visibleClose.tap()
            } else {
                // The running video can auto-hide between the first probe and
                // its tap. Reveal exactly once through the user-facing surface.
                try activate(controlsSurface)
                let revealedClose = try waitForHittable(closeButton, timeout: 2, named: "revealed iOS player close button")
                revealedClose.tap()
            }
        }
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
        guard waitUntil(timeout: 20, condition: {
            self.hasVisibleFrame(self.element("homeScreen")) || self.hasVisibleFrame(self.element("libraryScreen"))
        }) else {
            throw ProofFailure.ui("Back did not return to the originating Home or Video destination.")
        }
    }

    /// Bounded tour-only unwind for nested seasons, episodes, and details.
    /// It deliberately does not alter the product's ordinary Back contract.
    private func returnTourToLibrary() throws {
        #if os(tvOS)
        for _ in 0..<4 {
            if hasVisibleFrame(element("libraryScreen")) { return }
            XCUIRemote.shared.press(.menu)
            if waitUntil(timeout: 5, condition: { self.hasVisibleFrame(self.element("libraryScreen")) }) {
                return
            }
        }
        guard waitUntil(timeout: 20, condition: { self.hasVisibleFrame(self.element("libraryScreen")) }) else {
            throw ProofFailure.ui("TV Back did not unwind the tour to the Video library root.")
        }
        #else
        try unwindTourLibraryNavigationStack()
        #endif
    }

    private func refreshLibrary(waitingForResumeOf itemID: String? = nil) throws {
        #if os(iOS)
        let library = element("libraryScreen")
        for _ in 0..<6 {
            if hasVisibleFrame(library.buttons.firstMatch), library.buttons.firstMatch.isHittable { break }
            try scrollLibrary(library, up: false)
        }
        try scrollLibrary(library, up: false)
        #else
        // Device Settings intentionally contains no server-library refresh action.
        // Re-entering Video exercises the same session-backed reload path as a
        // viewer changing sections with the Siri Remote.
        try selectDestination("Home", screen: "homeScreen")
        try selectDestination("Video", screen: "libraryScreen")
        #endif
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "refreshed library screen")
        guard let itemID else { return }

        let resumed = element("libraryItem-\(itemID)")
        try waitForExistence(resumed, timeout: 20, named: "refreshed Video item")
        #if os(tvOS)
        // The Home journey proves its Resume action. This journey verifies the
        // saved position through the API and the reopened native player's clock.
        #else
        XCTAssertTrue(resumed.label.contains("in progress"))
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
        application!.typeText(text)
        XCUIRemote.shared.press(.menu)
        #else
        field.typeText(text)
        #endif
    }

    private func activate(_ control: XCUIElement) throws {
        #if os(tvOS)
        guard control.exists else {
            throw ProofFailure.ui("The requested tvOS control is not present in the current navigation destination.")
        }
        guard focus(control, timeout: 20) else {
            let current = focusedElement()
            throw ProofFailure.ui("The tvOS remote could not focus \(control.identifier) at \(control.frame); focused=\(current?.identifier ?? "none") at \(current?.frame ?? .zero).")
        }
        XCUIRemote.shared.press(.select)
        #else
        if control.exists, control.identifier == "homeEditButton" {
            try revealInLibraryViewport([control], in: element("accountScreen"))
        }
        let visibleControl = try waitForHittable(control, timeout: 15, named: "control")
        visibleControl.tap()
        #endif
    }

    private func waitForExistence(_ element: XCUIElement, timeout: TimeInterval, named name: String) throws {
        guard appears(element, timeout: timeout) else {
            throw ProofFailure.timeout("Timed out waiting for \(name).")
        }
    }

    private func hasVisibleFrame(_ control: XCUIElement) -> Bool {
        guard control.exists, let application else { return false }
        let window = application.windows.firstMatch
        guard window.exists else { return false }
        let frame = control.frame
        let viewport = window.frame
        guard !frame.isEmpty, !viewport.isEmpty,
              [frame.minX, frame.minY, frame.width, frame.height,
               viewport.minX, viewport.minY, viewport.width, viewport.height].allSatisfy({ $0.isFinite }) else {
            return false
        }
        return !frame.intersection(viewport).isEmpty
    }

    @discardableResult
    private func waitForHittable(_ control: XCUIElement, timeout: TimeInterval, named name: String) throws -> XCUIElement {
        var readyControl: XCUIElement?
        let predicate = NSPredicate { [self] _, _ in
            #if os(iOS)
            // AutoFill can present after navigation or after this wait starts.
            // Never save the disposable fixture password in the system vault.
            let prompt = application!.sheets["Save Password?"]
            let decline = application!.buttons["Not Now"]
            if (prompt.exists || application!.staticTexts["Save Password?"].exists),
               hasVisibleFrame(decline), decline.isEnabled, decline.isHittable {
                decline.tap()
                return false // Re-query after the sheet changes the accessibility snapshot.
            }
            #endif
            // The requested control can disappear while a sheet updates. Reading
            // identifier requires a matching snapshot just as frame/hittability do.
            guard control.exists else { return false }
            // Inactive SwiftUI navigation stacks can expose zero-frame matches.
            // XCTest can throw while computing their activation point, even for isHittable.
            let identifier = control.identifier
            let candidates = identifier.isEmpty
                ? [control]
                : application!.descendants(matching: .any).matching(identifier: identifier).allElementsBoundByIndex
            // iPad sidebar rows can retain a tab identifier that re-queries as
            // an inactive zero-frame tab. Keep the requested visible row as a fallback.
            guard let visible = (candidates + [control]).first(where: {
                hasVisibleFrame($0) && $0.isEnabled && $0.isHittable
            }) else { return false }
            readyControl = visible
            return true
        }
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: nil)
        guard XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed,
              let readyControl else {
            throw ProofFailure.timeout("Timed out waiting for hittable \(name).")
        }
        return readyControl
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

    private func accessibilityText(_ element: XCUIElement) -> String {
        let value: String
        if let string = element.value as? String {
            value = string
        } else if let number = element.value as? NSNumber {
            value = number.stringValue
        } else {
            value = ""
        }
        return [element.label, value].joined(separator: " ")
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

    private func menuAction(_ title: String) -> XCUIElement {
        #if os(tvOS)
        // Native picker menus expose focusable cells with Other labels. Their
        // focus settles after presentation; the underlying value isn't a cell.
        let popupCells = application!.collectionViews
            .cells.containing(NSPredicate(
                format: "elementType == %d AND label == %@",
                XCUIElement.ElementType.other.rawValue, title
            ))
        if waitUntil(timeout: 5, condition: { popupCells.count == 1 }) { return popupCells.firstMatch }
        #elseif os(iOS)
        let button = application!.buttons.matching(NSPredicate(format: "label == %@", title)).firstMatch
        if button.exists { return button }
        #endif
        return application!.descendants(matching: .any).matching(NSPredicate(format: "label == %@", title)).firstMatch
    }

    #if os(tvOS)
    /// Library categories are pinned above the options row; continue downward
    /// through that hierarchy until the first matching poster owns focus.
    private func focusVideoGridItemFromCategories(_ item: XCUIElement, named name: String) throws {
        try waitForExistence(item, timeout: 25, named: name)
        XCTAssertTrue(focus(item, timeout: 20),
                      "Real remote navigation through Library categories and options must focus the \(name) card.")
    }

    private func focus(_ target: XCUIElement, timeout: TimeInterval) -> Bool {
        guard let initialTarget = try? target.snapshot() else { return false }
        let targetIdentifier = initialTarget.identifier
        let targetLabel = initialTarget.label
        let musicIdentifiers = ["createMusicPlaylistButton", "saveMusicPlaylistButton", "editMusicPlaylistButton",
                                "downloadAudioButton", "playDownloadsButton", "audioListeningOptions", "audioLyrics",
                                "audioNormalizeToggle", "audioCrossfadePicker", "audioSleepTimerButton",
                                "audioUseServerButton", "audioKeepDeviceButton", "audioPlayPauseButton",
                                "audioRepeatButton", "audioStopButton", "musicPlayButton"]
        let musicLabels = ["Refresh playlist", "Remove all downloads", "Remove downloads",
                                "Save listening queue as a playlist", "Downloads", "3 seconds", "15 minutes", "Play album"]
        let musicTarget = targetIdentifier.hasPrefix("musicLibrary") || targetIdentifier.hasPrefix("musicPlaylist")
            || targetIdentifier.hasPrefix("playlistTrack-") || targetIdentifier.hasPrefix("downloadedTrack-")
            || musicIdentifiers.contains(targetIdentifier)
            || targetIdentifier.isEmpty && musicLabels.contains(targetLabel)
        let videoContainerTarget = ["videoKindPicker", "videoFavoritesToggle", "videoUnwatchedToggle",
                                    "videoFitPicker", "videoQualityPicker", "videoSpeedPicker"].contains(targetIdentifier)
            || targetIdentifier.hasPrefix("videoSubtitleStyle-")
        let queueTarget = targetIdentifier.hasPrefix("audioQueueItem-") || targetIdentifier == "videoApplyFiltersButton"
        let videoSwitchTarget = ["videoFavoritesToggle", "videoUnwatchedToggle"].contains(targetIdentifier)
        if waitUntil(timeout: min(2, timeout)) { return true }

        let directions: [XCUIRemote.Button] = [.up, .down, .left, .right]
        // A featured row can require traversing eight choices plus navigation
        // controls, each through a real remote event and accessibility snapshot.
        let routeTimeout = targetIdentifier.hasPrefix("featured") ? max(timeout, 45) : timeout
        let deadline = Date().addingTimeInterval(routeTimeout)
        var step = 0
        var blockedPrimary: (direction: XCUIRemote.Button, key: String)?
        while Date() < deadline {
            if targetIsFocused() { return true }
            let previousFocusKey = focusKey()
            if blockedPrimary?.key != previousFocusKey { blockedPrimary = nil }
            var direction: XCUIRemote.Button
            if let current = focusedElement(), let currentTarget = try? target.snapshot() {
                direction = directionToward(currentTarget, from: current, fallback: step, directions: directions)
                if let blockedPrimary, blockedPrimary.key == previousFocusKey, blockedPrimary.direction == direction {
                    let dx = currentTarget.frame.midX - current.frame.midX
                    let dy = currentTarget.frame.midY - current.frame.midY
                    if (direction == .left || direction == .right), abs(dy) > 2 {
                        direction = dy > 0 ? .down : .up
                    } else if (direction == .up || direction == .down), abs(dx) > 2 {
                        direction = dx > 0 ? .right : .left
                    }
                }
            } else {
                direction = directions[step % directions.count]
            }
            XCUIRemote.shared.press(direction)
            step += 1
            _ = self.waitUntil(timeout: min(1.5, max(0, deadline.timeIntervalSinceNow))) {
                if targetIsFocused() { return true }
                guard let currentFocusKey = focusKey() else { return false }
                return currentFocusKey != previousFocusKey
            }
            if focusKey() != previousFocusKey {
                blockedPrimary = nil
            } else if let previousFocusKey {
                blockedPrimary = (direction, previousFocusKey)
            }
        }
        return targetIsFocused()

        func focusKey() -> String? {
            guard let current = focusedElement() else { return nil }
            let identifier = current.identifier
            if !identifier.isEmpty { return identifier }
            let frame = current.frame
            return "\(current.elementType.rawValue)|\(current.label)|\(frame.midX.rounded())|\(frame.midY.rounded())"
        }

        func waitUntil(timeout: TimeInterval) -> Bool {
            guard timeout > 0 else { return targetIsFocused() }
            return self.waitUntil(timeout: timeout) { targetIsFocused() }
        }

        func targetIsFocused() -> Bool {
            guard let targetSnapshot = try? target.snapshot() else { return false }
            if targetSnapshot.hasFocus { return true }
            if musicTarget, let owner = focusedElement(), owner.elementType == .cell {
                let controls = snapshotDescendants(owner).filter {
                    [.button, .switch, .textField, .secureTextField].contains($0.elementType)
                }
                // A native List cell can proxy its sole action more than once. Every
                // actionable descendant must identify that same logical music control.
                if !controls.isEmpty {
                    let sameID = !targetIdentifier.isEmpty && controls.allSatisfy { $0.identifier == targetIdentifier }
                    let sameLabel = !targetLabel.isEmpty && controls.allSatisfy { $0.label == targetLabel }
                    if sameID || sameLabel { return true }
                }
            }
            if videoContainerTarget,
               snapshotDescendants(targetSnapshot).contains(where: \.hasFocus) {
                return true
            }
            if queueTarget,
               let owner = focusedElement(), owner.elementType == .cell, owner.identifier.isEmpty {
                let ownedButtons = snapshotDescendants(owner).filter { $0.elementType == .button }
                if !ownedButtons.isEmpty && ownedButtons.allSatisfy({ $0.identifier == targetIdentifier }) {
                    return true
                }
            }
            if videoSwitchTarget,
               let owner = focusedElement(), owner.elementType == .cell, owner.identifier.isEmpty {
                let ownedSwitches = snapshotDescendants(owner).filter { $0.elementType == .switch }
                if !ownedSwitches.isEmpty && ownedSwitches.allSatisfy({ $0.identifier == targetIdentifier }) {
                    return true
                }
            }
            guard let current = focusedElement(), current.identifier.isEmpty, current.label.isEmpty else { return false }
            // System actions focus an anonymous wrapper; its bounds have subpoint rounding differences.
            let expected = targetSnapshot.frame
            let actual = current.frame
            return !expected.isEmpty && abs(expected.minX - actual.minX) < 0.5
                && abs(expected.minY - actual.minY) < 0.5
                && abs(expected.width - actual.width) < 0.5
                && abs(expected.height - actual.height) < 0.5
        }
    }

    private func focusedElement() -> XCUIElementSnapshot? {
        guard let snapshot = try? application!.snapshot() else { return nil }
        return snapshotDescendants(snapshot).first(where: \.hasFocus)
    }

    private func snapshotDescendants(_ snapshot: XCUIElementSnapshot) -> [XCUIElementSnapshot] {
        snapshot.children.flatMap { [$0] + snapshotDescendants($0) }
    }

    private func directionToward(
        _ target: XCUIElementSnapshot,
        from current: XCUIElementSnapshot,
        fallback step: Int,
        directions: [XCUIRemote.Button]
    ) -> XCUIRemote.Button {
        let targetFrame = target.frame
        let currentFrame = current.frame
        if !targetFrame.isEmpty && !currentFrame.isEmpty {
            // A wide inset can overlap a scaled card while its focus lane is below it.
            if targetFrame.maxX > currentFrame.minX && targetFrame.minX < currentFrame.maxX {
                if targetFrame.midY > currentFrame.maxY { return .down }
                if targetFrame.midY < currentFrame.minY { return .up }
            }
            // Native card focus scales the current view; adjacent focus bounds can overlap.
            if targetFrame.intersects(currentFrame) {
                let dx = targetFrame.midX - currentFrame.midX
                let dy = targetFrame.midY - currentFrame.midY
                if abs(dx) > abs(dy) { return dx > 0 ? .right : .left }
                if abs(dy) > 1 { return dy > 0 ? .down : .up }
            }
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
    let comicBookID: String?
    let bookShelfID: String?
    let comicSeriesID: String?
    let proseBookID: String?
    let pdfBookID: String?
    let cbrBookID: String?
    let serverURL: String
    let username: String
    let password: String
    let directItemID: String
    let directItemTitle: String
    let convertedItemID: String
    let convertedItemTitle: String
    let matrixItemID: String
    let matrixItemTitle: String
    let showID: String
    let showTitle: String
    let seasonID: String
    let secondSeasonID: String?
    let episodeID: String
    let episodeTitle: String
    let secondEpisodeID: String
    let secondEpisodeTitle: String
    let secondSeasonEpisodeID: String?
    let secondSeasonEpisodeTitle: String?
    let episodeIntroEndSeconds: Double
    let episodeCreditsStartSeconds: Double
    let audioAlbumID: String
    let audioAlbumTitle: String
    let audioArtistID: String
    let audioTrackID: String
    let audioTrackTitle: String
    let audioSecondTrackID: String
    let audioSecondTrackTitle: String
    let sharedMusicPlaylistID: String?
    let sharedMusicPlaylistTitle: String?
    let audioLyric: String?
    let requestMovieKey: String?
    let requestMovieTitle: String?
    let requestShowKey: String?
    let requestShowTitle: String?
    let requestAnimeKey: String?
    let requestAnimeTitle: String?
}

private struct ProofHomeShelf: Codable, Equatable {
    let id: String
    let visible: Bool
}

private struct ProofHomeLayout: Decodable {
    let shelves: [ProofHomeShelf]?
}

private struct APIWatchlistState: Decodable {
    let included: Bool
}

private struct APIRequestPage: Decodable {
    let items: [APIRequestRecord]
}

private struct APIRequestRecord: Decodable {
    let id: String
    let key: String
    let language: String?
    let seasons: APIRequestSeasons?

    var seasonNumbers: [Int] {
        guard let seasons, case let .selected(values) = seasons else { return [] }
        return values
    }
}

private enum APIRequestSeasons: Decodable {
    case all
    case selected([Int])

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let all = try? value.decode(String.self), all == "all" {
            self = .all
        } else {
            self = .selected(try value.decode([Int].self))
        }
    }
}

private struct ProofAPI {
    let origin: URL
    let session: URLSession

    @MainActor func nativeInvitations(identity: APIIdentity) async throws -> [[String: Any]] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Settings/admin/invites"))
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        guard let rows = try JSONSerialization.jsonObject(with: await send(request)) as? [[String: Any]] else {
            throw ProofAPIError.malformedResponse
        }
        return rows
    }

    @MainActor func settingsJSON(identity: APIIdentity, method: String = "GET", body: [String: Any]? = nil) async throws -> [String: Any] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Settings"))
        request.httpMethod = method
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        guard let object = try JSONSerialization.jsonObject(with: await send(request)) as? [String: Any] else {
            throw ProofAPIError.malformedResponse
        }
        return object
    }

    func homeLayout(identity: APIIdentity) async throws -> [ProofHomeShelf]? {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Home/layout"))
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        return try JSONDecoder().decode(ProofHomeLayout.self, from: await send(request)).shelves
    }

    func saveHomeLayout(identity: APIIdentity, shelves: [ProofHomeShelf]?) async throws -> [ProofHomeShelf]? {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Home/layout"))
        request.httpMethod = "PUT"
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let value: Any
        if let shelves { value = shelves.map { ["id": $0.id, "visible": $0.visible] as [String: Any] } }
        else { value = NSNull() }
        request.httpBody = try JSONSerialization.data(withJSONObject: ["shelves": value])
        return try JSONDecoder().decode(ProofHomeLayout.self, from: await send(request)).shelves
    }

    func watchlistIncluded(identity: APIIdentity, itemID: String) async throws -> Bool {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Watchlist/\(itemID)"))
        request.setValue(mediaBrowserAuthorization(identity.token), forHTTPHeaderField: "Authorization")
        return try JSONDecoder().decode(APIWatchlistState.self, from: await send(request)).included
    }

    func setWatchlistIncluded(identity: APIIdentity, itemID: String, included: Bool) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Watchlist/\(itemID)"))
        request.httpMethod = "PUT"
        request.setValue(mediaBrowserAuthorization(identity.token), forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["included": included])
        let saved = try JSONDecoder().decode(APIWatchlistState.self, from: await send(request))
        guard saved.included == included else { throw ProofAPIError.malformedResponse }
    }

    func requestRecords(identity: APIIdentity) async throws -> [APIRequestRecord] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Requests"))
        request.setValue(mediaBrowserAuthorization(identity.token), forHTTPHeaderField: "Authorization")
        let response = try JSONDecoder().decode(APIRequestPage.self, from: await send(request))
        return response.items
    }

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

    func seedBookProgress(identity: APIIdentity, bookID: String) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Books/\(bookID)/state"))
        request.httpMethod = "PATCH"
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["unit_index": 0, "offset": 0, "completed": false])
        _ = try await send(request)
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

    @MainActor func musicJSON(identity: APIIdentity, path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Any {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Music/" + path))
        request.httpMethod = method
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let data = try await send(request)
        if data.isEmpty { return [String: Any]() }
        return try JSONSerialization.jsonObject(with: data)
    }

    @MainActor func replaceProofMusicSession(identity: APIIdentity, itemIDs: [String], position: Int) async throws {
        guard let current = try await musicJSON(identity: identity, path: "session") as? [String: Any],
              let revision = current["revision"] as? Int else { throw ProofAPIError.malformedResponse }
        _ = try await musicJSON(identity: identity, path: "session", method: "PUT", body: [
            "base_revision": revision, "item_ids": itemIDs, "current_index": 0, "position_seconds": position,
            "repeat": "off", "shuffle": false, "active_output": ["kind": "browser"],
        ])
    }

    private func mediaBrowserAuthorization(_ token: String) -> String {
        "MediaBrowser Client=\"Lumina proof tests\",Device=\"Simulator\",DeviceId=\"native-proof-verifier\",Version=\"0.1\",Token=\"\(token)\""
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
    let played: Bool
    let isFavorite: Bool
    let playbackPositionTicks: Int64

    enum CodingKeys: String, CodingKey {
        case played = "Played"
        case isFavorite = "IsFavorite"
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

private extension NativeProofJourney {
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

// The completion runs off the blocked test actor; the semaphore publishes these values.
private final class BrokerResponse: @unchecked Sendable {
    var data: Data?
    var succeeded = false
}
