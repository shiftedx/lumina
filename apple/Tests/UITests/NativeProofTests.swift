import Foundation
import XCTest
#if os(iOS)
import AVFoundation
#endif
#if canImport(UIKit)
import UIKit
#endif
#if canImport(Vision)
import Vision
#endif

#if os(iOS)
@MainActor
final class NativeMobileWelcomeTests: NativeProofJourney {
    func testMinimalWelcomeHelpAndSignIn() async throws {
        try await journeyMobileWelcome()
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
    func testReadingCheckpointSurvivesBackground() async throws {
        try await journeyNativeBooksReadTurnAndRestoreAllFormats(backgroundCheckpoint: true)
    }
}
@MainActor
final class NativeVisualConsistencyTests: NativeProofJourney {
    func testMediaCatalogGuttersAndNavigation() throws { try journeyMediaCatalogGuttersAndNavigation() }
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
    #if os(iOS)
    func testLockScreenMusicArtworkPersists() async throws { try await journeyLockScreenMusicArtworkPersists() }
    func testCompactMusicToolbarReopensAndResumes() async throws { try await journeyCompactMusicToolbarReopensAndResumes() }
    #endif
}

@MainActor
final class NativeAudioFormatRecoveryTests: NativeProofJourney {
    func testRejectedSourceRecoversWithCompatibleAudioAndShowsLyrics() async throws {
        try await journeyAlbumPlayStartsFromIdle(requiresCompatibleAudio: true)
    }
}

@MainActor
final class NativeVideoFormatRecoveryTests: NativeProofJourney {
    func testInitialFormatFailureRetriesOneCompatibleVideo() async throws {
        try await journeyInitialVideoFormatRecovery()
    }

    #if os(iOS)
    func testStandardPhoneInitialFormatFailureRetriesOneCompatibleVideo() async throws {
        try await journeyInitialVideoFormatRecovery()
    }
    #endif
}

@MainActor
final class NativeClientDemuxVideoFormatRecoveryTests: NativeProofJourney {
    func testClientDemuxFormatFailureRetainsNormalServerProfile() async throws {
        try await journeyClientDemuxVideoFormatRecovery(failServerCandidate: false)
    }

    func testClientDemuxServerMasterFailureUsesItsSelectedMediaPlaylist() async throws {
        try await journeyClientDemuxVideoFormatRecovery(failServerCandidate: true)
    }

    func testClientDemuxServerMasterAndMediaFailureUsesFinalCompatibilityProfile() async throws {
        try await journeyClientDemuxVideoFormatRecovery(failServerCandidate: true, failMediaCandidate: true)
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

#if os(iOS)
@MainActor
final class NativeLibraryMetadataTests: NativeProofJourney {
    func testPosterEditAndDetailFixMatchUseAuthorizedServerWorkflows() async throws {
        try await journeyLibraryMetadata()
    }
}

@MainActor
final class NativePlaybackInfoTests: NativeProofJourney {
    func testInfoShowsLivePlaybackMetricsAndReturnsToVideo() async throws {
        try await journeyPlaybackInfo()
    }
}

@MainActor
final class NativeVideoGestureTests: NativeProofJourney {
    func testVerticalSwipesAdjustDeviceControlsWithoutPlayerVolumeSlider() async throws {
        try await journeyVideoDeviceControls()
    }

    func testSurfaceGesturesSeekAndKeepPausedPlaybackPaused() async throws {
        try await journeyVideoSurfaceGestures()
    }
}



@MainActor
final class NativeVideoRotationContinuityTests: NativeProofJourney {
    func testPlayingVideoContinuesAcrossOrientationChanges() async throws {
        try await journeyVideoPlaybackSurvivesOrientationChanges()
    }
}

@MainActor
final class NativePlaybackDiagnosticsTests: NativeProofJourney {
    func testRecordsAndSharesPlaybackDiagnostics() async throws {
        try await journeyPlaybackDiagnostics()
    }
}

@MainActor
final class NativeVideoDuoContinuityTests: NativeProofJourney {
    func testPlaybackRetainsSessionWhenPresentationIsRebuilt() async throws {
        try await journeyVideoPresentationRebuild()
    }
    func testClientDemuxPlaybackRetainsSessionWhenPresentationIsRebuilt() async throws {
        try await journeyVideoPresentationRebuild(clientDemux: true)
    }

    func testStablePlaybackDoesNotContinuouslyRewritePlayerRate() async throws {
        try await journeyVideoRateSettlesDuringPlayback()
    }

    func testDirectPlaybackRetainsSessionAcrossDuoPoses() async throws {
        try await journeyVideoDuoContinuity()
    }
}

@MainActor
final class NativeVideoForegroundInactiveContinuityTests: NativeProofJourney {
    func testPlayingVideoContinuesAcrossControlCenter() async throws {
        try await journeyVideoPlaybackSurvivesForegroundInactiveTransition()
    }
}

@MainActor
final class NativeVideoForegroundRestoreContinuityTests: NativeProofJourney {
    func testVideoPlaybackIntentRestoresAfterHomeAndKeepsExplicitPause() async throws {
        try await journeyVideoPlaybackSurvivesHomeForegroundTransition()
    }
}

@MainActor
final class NativeVideoSettingsLayoutTests: NativeProofJourney {
    func testClosedDuoSettingsPanelKeepsControlsUsable() async throws {
        try await journeyClosedDuoSettingsPanelKeepsControlsUsable()
    }
}

@MainActor
final class NativeDownloadedVideoTests: NativeProofJourney {
    func testMovieDownloadSurvivesOfflineRelaunchAndPlaysLocally() async throws {
        try await journeyDownloadedVideoPlaysOffline(mkv: false)
    }
    func testMKVDownloadPlaysLocally() async throws {
        try await journeyDownloadedVideoPlaysOffline(mkv: true)
    }
    func testShowsDownloadSelectedEpisodesAndWholeSeasonsForOfflinePlayback() async throws {
        try await journeyShowEpisodeDownloads()
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
    func testNewHomeCarouselsEnterAtLeadingCard() async throws { try await journeyTVLeadingCarouselEntry() }
    func testHomeArtworkAndCachedDetailReturns() async throws { try await journeyTVHomeArtworkAndCachedReturns(nextUp: false) }
    func testNextUpArtworkAndCachedPlayback() async throws { try await journeyTVHomeArtworkAndCachedReturns(nextUp: true) }
    func testTVHomeNavigationPreservesSelection() async throws { try await journeyTVHomeNavigationPreservesSelection() }
}
@MainActor
final class NativeTVEpisodeRemoteNavigationTests: NativeProofJourney {
    func testEpisodeCardSelectStartsPlayback() async throws { try await journeyTVEpisodeCardActivation() }
    func testHeroReturnsFullyIntoView() async throws { try await journeyTVHeroReturn() }
    func testHeroReturnAcrossBrowseDestinations() async throws { try await journeyTVBrowseHeroReturns() }
}
@MainActor
final class NativeTVLibraryAlphabetTests: NativeProofJourney {
    func testHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned() throws { try journeyHomeHeaderScrolling() }
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
@MainActor
final class NativeTVRemoteVideoStartTests: NativeProofJourney {
    func testRemoteStartsVideoFromDetails() async throws { try await journeyTVRemoteStartsVideoFromDetails() }
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
#if os(iOS)
@MainActor
final class NativeLibraryCatalogScrollingTests: NativeProofJourney {
    func testIPadHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned() throws { try journeyHomeHeaderScrolling() }
    func testCompleteCatalogSupportsFastPosterScrolling() throws {
        try journeyCompleteCatalogSupportsFastPosterScrolling()
    }
}

@MainActor
final class NativeLibraryWarmReturnTests: NativeProofJourney {
    func testLibraryTabReturnsToRetainedRootsAndClearsArtworkCache() throws {
        try journeyNativeLibraryWarmReturn()
    }
}
@MainActor
final class NativeEpisodeShowContextTests: NativeProofJourney {
    func testEpisodeControlsStayAlignedAndPlayWithoutReturningToHero() async throws {
        try await journeyEpisodeControlsAndPlayback()
    }

    func testSelectingEpisodeReturnsToItsShowWithTheEpisodeSelected() async throws {
        try await journeyEpisodeSelectionReturnsToShowContext()
    }
}
@MainActor
final class NativeStandardPhoneEpisodeChromeTests: NativeProofJourney {
    func testEpisodeCollectionUsesSafeHeaderAndNativeLeadingEdgeBack() async throws {
        try await journeyStandardPhoneEpisodeChromeAndBack()
    }
}
@MainActor
final class NativeStandardPhonePushedChromeNavigationTests: NativeProofJourney {
    func testHomeSearchUsesLeadingEdgeBackWithoutPoppingRoot() throws {
        try journeyStandardPhoneHomeSearchLeadingEdgeBack()
    }
}
@MainActor
final class NativeStandardPhoneAppearanceChromeTests: NativeProofJourney {
    func testPushedChromeStaysClearInLightAndDarkAppearances() throws {
        try journeyStandardPhoneAppearanceChrome()
    }
}
@MainActor
final class NativeOpenDuoEmptyWatchlistTests: NativeProofJourney {
    func testEmptyWatchlistCentersBelowCategoriesAndPopulates() async throws { try await journeyEmptyWatchlistCentering() }
}
@MainActor
final class NativeOpenDuoRequestsHeaderTests: NativeProofJourney {
    func testRequestsArtworkAndActionsClearPortraitStatus() throws { try journeyRequestsPortraitHeader() }
}
@MainActor
final class NativeOpenDuoLibraryHeaderTests: NativeProofJourney {
    func testCategoryArtworkExtendsUnderFloatingNavigation() throws {
        try journeyCategoryArtworkUnderNavigation()
    }
    func testOpenPortraitPlacesNavigationOverContentAndUtilitiesBelowStatus() async throws {
        try await journeyPortraitNavigationWithMusic()
    }
    func testOpenDuoLibraryHeaderAndSearchKeepCategoriesClearOfChrome() throws {
        try journeyOpenDuoLibraryHeaderAndSearch()
    }
}
#endif

#if os(iOS)
#if os(iOS)
@MainActor
final class NativeIdleBackgroundTests: NativeProofJourney {
    func testIdleHomeReturnsAfterUsingAnotherApp() async throws {
        try await journeyIdleHomeReturnsAfterUsingAnotherApp()
    }
}
#endif

@MainActor
final class NativeHomeBrowseFiltersSearchAndHeroWatchlistTests: NativeProofJourney {
    func testHomeHeroArtworkSwipesAndPlaysSelectedTitle() async throws {
        try await journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle()
    }
    func testHomeHeroSwipeKeepsWideLayoutStable() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }
        try await journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle(checksLayout: true)
    }
    func testStandardPhoneUsesBottomNavigationAndCompactBrowseMenu() async throws {
        try await journeyStandardPhoneNavigation()
    }
    func testClosedDisplayFitsThreeCategoriesBesideLogo() async throws {
        try await journeyClosedDisplayCategoryWidth()
    }
    func testHomeBrowseFiltersSearchAndHeroWatchlist() async throws {
        try await journeyNativeHomeBrowseFiltersSearchAndHeroWatchlist()
    }
}
#endif
#if os(iOS)
@MainActor
final class NativeHomeLayoutReordersHidesAndPersistsTests: NativeProofJourney { func testNativeHomeLayoutReordersHidesAndPersists() async throws { try await journeyNativeHomeLayoutReordersHidesAndPersists() } }
#endif
@MainActor
final class NativeRequestsDiscoverSelectAndSubmitThroughTheServerTests: NativeProofJourney { func testNativeRequestsDiscoverSelectAndSubmitThroughTheServer() async throws { try await journeyNativeRequestsDiscoverSelectAndSubmitThroughTheServer() } }
@MainActor
final class NativeRequestsVaultViewTests: NativeProofJourney {
    func testAvailableTitlesOpenTheirVaultDestinationsAndReturnToRequests() async throws {
        try await journeyNativeRequestsVaultView()
    }
}
@MainActor
final class NativeHomeDetailWatchlistPersistsThroughServerTests: NativeProofJourney { func testNativeHomeDetailWatchlistPersistsThroughServer() async throws { try await journeyNativeHomeDetailWatchlistPersistsThroughServer() } }
@MainActor
final class NativeEpisodeSkipActionsAndNextEpisodeTests: NativeProofJourney { func testNativeEpisodeSkipActionsAndNextEpisode() async throws { try await journeyNativeEpisodeSkipActionsAndNextEpisode() } }
@MainActor
final class NativeEpisodeAutoplayPresenceTests: NativeProofJourney {
    func testNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes() async throws {
        try await journeyNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes()
    }
}
@MainActor
final class NativeVideoOverlayTests: NativeProofJourney {
    func testVideoOverlayCentersAndDismisses() async throws { try await journeyVideoOverlayCentersAndDismisses() }
    #if os(iOS)
    func testVideoPictureInPictureContinuesInBackground() async throws { try await journeyVideoPictureInPictureContinuesInBackground() }
    #endif
}
#if os(iOS)
@MainActor
final class NativeVideoCastPickerSmokeTests: NativeProofJourney {
    func testVideoCastPickerOpensAndReturnsToLocalPlayback() async throws {
        try await journeyVideoCastPickerOpensAndReturnsToLocalPlayback()
    }
}
#endif
#if os(iOS)
@MainActor
final class NativeConnectionFormCenteringTests: NativeProofJourney {
    func testConnectionFormCentersInViewport() throws { try journeyConnectionFormCentering() }
}
#endif
@MainActor
final class NativePlaybackResumeChoiceTests: NativeProofJourney {
    func testMovieResumeStartOverAndCancel() async throws { try await journeyPlaybackResumeChoice(episode: false) }
    func testEpisodeResumeStartOverAndCancel() async throws { try await journeyPlaybackResumeChoice(episode: true) }
}
@MainActor
final class NativeVideoSettingsTests: NativeProofJourney { func testNativeVideoDoesNotKeepDuplicateControlsOverPlayback() async throws { try await journeyNativeVideoDoesNotKeepDuplicateControlsOverPlayback() } }
@MainActor
final class NativePlaybackQualityAndSubtitleTimingTests: NativeProofJourney {
    func testNativePlaybackQualityAndSubtitleTimingRepackagePausedMKV() async throws {
        try await journeyNativePlaybackQualityAndSubtitleTimingRepackagePausedMKV()
    }
}
@MainActor
final class NativePlaybackSpeedAndChaptersTests: NativeProofJourney { func testNativePlaybackSpeedAndChaptersRepackagePausedMKV() async throws { try await journeyNativePlaybackSpeedAndChaptersRepackagePausedMKV() } }
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
        try revealBookItem(element("bookCatalogButton"))
        try activate(element("bookCatalogButton"))
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
        // Standard phones expose Books from Home's compact Browse menu rather
        // than the wide Home category rail. The shared tour helper follows
        // that public route while retaining the iPad behavior.
        try selectTourBooksRoot()
        if compactLayoutOnly {
            try revealBookItem(element("bookCatalogButton"))
            try activate(element("bookCatalogButton"))
        }
        for book in [comic, pdf] {
            let card = element("book-\(book)")
            try waitForExistence(card, timeout: 25, named: "book cover")
            if compactLayoutOnly { try revealBookItem(card); try activate(card) }
            else { card.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.3)).tap() }
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

    private func showBookControls() throws {
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

    private func revealBookItem(_ item: XCUIElement) throws {
        let viewport = try applicationViewport()
        for _ in 0..<8 {
            if hasVisibleFrame(item) && item.isHittable { return }
            // The outer Duo display can place navigation over a trailing catalog action.
            try dragVertically(in: viewport, anchorX: viewport.midX, up: true)
        }
        throw ProofFailure.ui("The Books catalog item could not be revealed.")
    }

    #if LUMINA_UI_TESTING
    private func emitBooksReaderFailureEvidence(fixture: ProofFixture, proseBookID: String) async {
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
        } else {
            let back = application!.buttons.matching(identifier: "BackButton").firstMatch
            XCTAssertFalse(element("closePlayerButton").exists, "Music must not invent a second back control.")
            XCTAssertTrue(queue.exists)
            XCTAssertTrue(element("audioStopButton").exists)
            try waitForHittable(transport, timeout: 15, named: "Navigation Play/Pause")
            XCTAssertFalse(element("audioPreviousButton").exists)
            XCTAssertFalse(element("audioNextButton").exists)
            if usesStandardPhonePushedChrome {
                XCTAssertFalse(hasVisibleFrame(back),
                               "The standard-phone player must use its leading-edge Back gesture instead of a visible Back button.")
                let header = element("standardPhoneNavigationHeader")
                try waitForExistence(header, timeout: 15, named: "standard-phone music navigation header")
                XCTAssertTrue(hasVisibleFrame(header))
                XCTAssertEqual(transport.frame.midY, queue.frame.midY, accuracy: 3,
                               "Queue and Play/Pause must share the standard-phone navigation header.")
            } else {
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
        let usesRail = usesDuo && viewport.width > viewport.height
        if !usesDuo { try selectDestination("Home", screen: "homeScreen") }
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
        if usesRail {
            // Duo rail layouts stack controls; ordinary top bars remain horizontal.
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
        completed = true
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
        try stopAudioPlayer()
        guard disappears(element("playbackScreen"), timeout: 15),
              waitUntil(timeout: 15, condition: { !self.hasVisibleFrame(bar) }) else {
            throw ProofFailure.ui("Stop in the TV full player must dismiss it and remove the compact bar.")
        }
        try await performanceQuietPhase("tv_music_post_stop_idle")
        completed = true
    }
    #endif

    #if os(iOS)
    func journeyClosedDuoSettingsPanelKeepsControlsUsable() async throws {
        XCUIDevice.shared.orientation = .portrait
        let fixture = try loadFixture()
        try launchAndResetSession()
        let viewport = try applicationViewport()
        guard viewport.width >= 450, viewport.width < 500, viewport.height > viewport.width else {
            throw ProofFailure.ui("This layout contract requires the closed Duo portrait display (450..<500pt).")
        }
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.directItemID)
        _ = try startPlayback(using: "playButton")
        try tapVideoTransportAfterChromeHides(named: "closed-Duo settings pause control")
        try activate(element("videoSettingsButton"))
        let panel = element("videoSettingsPanel")
        try waitForExistence(panel, timeout: 10, named: "portrait playback settings")
        try activate(element("videoSettingsTab-video"))
        let done = element("videoSettingsDoneButton")
        let fit = element("videoFitPicker")
        let quality = element("videoQualityPicker")
        let controls = [
            done,
            element("videoSettingsTab-video"),
            element("videoSettingsTab-chapters"),
            fit,
            quality,
        ]
        for control in controls {
            _ = try waitForHittable(control, timeout: 10, named: "portrait playback setting \(control.identifier)")
            XCTAssertTrue(panel.frame.contains(control.frame),
                          "The closed-Duo settings panel must contain \(control.identifier).")
        }
        for (index, control) in controls.enumerated() {
            for peer in controls.dropFirst(index + 1) {
                XCTAssertTrue(control.frame.intersection(peer.frame).isEmpty,
                              "Closed-Duo settings controls must not overlap: \(control.identifier) and \(peer.identifier).")
            }
        }
        try attachFrame(named: "Portrait playback settings remain usable in the system sheet")
        try activate(done)
        try closePlayer()
    }

    func journeyLibraryMetadata() async throws {
        XCUIDevice.shared.orientation = .portrait
        let correctedTitle = "Corrected synthetic title \(UUID().uuidString.prefix(4))"
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-hero-artwork"])
        try signIn(using: fixture, waitsForAccount: false)
        try activate(application!.buttons["Library"])
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library posters")
        let poster = try revealLibraryItem(id: fixture.directItemID, named: "metadata poster")
        try attachFrame(named: "Library before poster context menu")
        poster.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(forDuration: 1.1)
        try activate(element("editMetadataAction"))
        let form = element("metadataEditorForm")
        try waitForExistence(form, timeout: 8, named: "metadata editor")
        let title = element("metadataField-name")
        try replaceMetadataText(title, with: correctedTitle)
        try activate(element("metadataKeyboardDone"))
        let yearField = element("metadataField-year")
        try revealDetailElement(yearField, in: form, named: "Year field", requiresHittable: true)
        try replaceMetadataText(yearField, with: "2023")
        try activate(element("metadataKeyboardDone"))
        try attachFrame(named: "Native metadata editor from poster long press")
        try activate(element("metadataEditorSave"))
        XCTAssertTrue(disappears(form, timeout: 10), "Saving valid metadata must return to the library.")
        let doc = try await api.metadataDocument(identity: identity, id: fixture.directItemID)
        let fields = try XCTUnwrap(doc["fields"] as? [String: [String: Any]])
        XCTAssertEqual(fields["name"]?["value"] as? String, correctedTitle)
        XCTAssertEqual(fields["year"]?["value"] as? Int, 2023)
        XCTAssertTrue(waitUntil(timeout: 12) { poster.exists && poster.label.contains(correctedTitle) },
                      "The saved title must replace the old poster label without restarting the app.")
        poster.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 12, named: "updated title detail")
        try attachFrame(named: "Updated title detail overflow entry")
        let menus = application!.descendants(matching: .any).matching(identifier: "titleActionsMenu").allElementsBoundByIndex
        let menu = try XCTUnwrap(menus.first(where: { hasVisibleFrame($0) }))
        menu.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try activate(element("fixMatchAction"))
        try waitForExistence(element("metadataMatchQuery"), timeout: 10, named: "Fix match search")
        try replaceMetadataText(element("metadataMatchQuery"), with: try XCTUnwrap(fixture.requestMovieTitle))
        try activate(element("metadataKeyboardDone"))
        try replaceMetadataText(element("metadataMatchYear"), with: "1999")
        try activate(element("metadataKeyboardDone"))
        try activate(element("metadataMatchSearch"))
        let candidate = element("metadataMatch-603")
        try revealDetailElement(candidate, in: form, named: "correct provider match", requiresHittable: true)
        try attachFrame(named: "Native Fix match search results")
        try activate(candidate)
        try activate(element("metadataMatchConfirm"))
        XCTAssertTrue(disappears(form, timeout: 10), "Confirming the provider must finish the matching workflow.")
        let matched = try await api.metadataDocument(identity: identity, id: fixture.directItemID)
        let matchedFields = try XCTUnwrap(matched["fields"] as? [String: [String: Any]])
        XCTAssertEqual((matchedFields["provider_ids"]?["value"] as? [String: String])?["Tmdb"], "603")
        XCTAssertEqual(matchedFields["name"]?["value"] as? String, correctedTitle,
                       "Fix match must preserve user-authored edits.")
    }

    private func replaceMetadataText(_ field: XCUIElement, with text: String) throws {
        let visible = try waitForHittable(field, timeout: 10, named: "metadata field")
        visible.tap()
        visible.typeKey("a", modifierFlags: .command)
        visible.typeText(text)
    }


    func journeyPlaybackInfo() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-hero-artwork"])
        try signIn(using: fixture, waitsForAccount: false)
        let play = element("featuredPlayButton")
        try waitForExistence(play, timeout: 20, named: "Home hero Play")
        XCTAssertTrue(hasVisibleFrame(play))
        play.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "video player")
        try tapVideoTransportAfterChromeHides(named: "pause before inspecting playback info")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5)
        try activate(element("videoInfoButton"))
        let panel = element("videoSettingsPanel")
        let stats = element("videoPlaybackStats")
        try waitForExistence(stats, timeout: 10, named: "stats for nerds")
        func samples() -> Int {
            Int((stats.value as? String ?? "").replacingOccurrences(of: "samples=", with: "")) ?? 0
        }
        XCTAssertTrue(waitUntil(timeout: 8) { samples() >= 2 }, "Info must refresh live metrics while visible.")
        XCTAssertTrue(accessibilityText(element("playbackStat-resolution")).contains("×"))
        XCTAssertTrue(accessibilityText(element("playbackStat-videoCodec")).contains("H.264"),
                      "Video format must come from the playing track.")
        try attachFrame(named: "Playback info live video metrics")
        for id in ["hdr", "fps", "currentFPS", "delivery", "recentDownload", "throughput", "transferred", "buffer", "stalls", "dropped", "audioCodec", "audioFormat", "sourceContainer"] {
            let row = element("playbackStat-\(id)")
            try revealDetailElement(row, in: panel, named: "playback metric \(id)", requiresHittable: false)
            XCTAssertTrue(row.exists)
        }
        XCTAssertTrue(accessibilityText(element("playbackStat-audioCodec")).contains("AAC"))
        XCTAssertTrue(accessibilityText(element("playbackStat-audioFormat")).contains("kHz"))
        XCTAssertFalse(accessibilityText(element("playbackStat-sourceContainer")).contains("Not reported"),
                       "PlaybackInfo must retain the selected source container even when Home's card omits it.")
        XCTAssertFalse(element("playbackDiagnosticsRecording").exists, "Opening Info must not start a recording.")
        try attachFrame(named: "Playback info source and live performance metrics")
        try activate(element("videoSettingsDoneButton"))
        XCTAssertTrue(disappears(stats, timeout: 5))
        // Capture the visible Info target before resuming so auto-hide cannot race
        // the second tap. Opening Info must leave active playback running.
        let info = element("videoInfoButton")
        let target = try XCTUnwrap(visibleApplicationWindow).coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: info.frame.midX, dy: info.frame.midY))
        try activate(element("videoPlayPauseButton"))
        target.tap()
        try waitForExistence(stats, timeout: 8, named: "live playback info")
        let time = element("playbackTime")
        let before = try waitForPlaybackValue(time, timeout: 8) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 8) { $0 >= before + 2 }
        XCTAssertTrue(waitUntil(timeout: 5) { self.accessibilityText(self.element("playbackStat-state")).contains("Playing") })
        try activate(element("videoSettingsTab-video"))
        XCTAssertTrue(disappears(stats, timeout: 5), "Leaving Info must remove its live sampler.")
        try activate(element("videoSettingsDoneButton"))
        try tapVideoTransportAfterChromeHides(named: "pause after Info")
        try closePlayer()
    }

    #endif

    func journeyInitialVideoFormatRecovery() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        #if os(iOS)
        // Password storage is owned by iOS and may arrive after Home has
        // rendered. Install the monitor before authentication so XCTest can
        // handle it at the next user interaction without a direct tap waiting
        // for the app to become idle behind the sheet.
        let passwordMonitor = addUIInterruptionMonitor(withDescription: "Discard disposable proof password") { alert in
            guard alert.staticTexts["Save Password?"].exists else { return false }
            // The system sheet can expose this under a non-Button AX trait.
            let decline = alert.descendants(matching: .any)
                .matching(NSPredicate(format: "label == %@", "Not Now"))
                .firstMatch
            guard decline.exists else { return false }
            decline.tap()
            return true
        }
        defer { removeUIInterruptionMonitor(passwordMonitor) }
        #endif
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)

        #if os(iOS)
        let recoveryLaunchArguments = UIDevice.current.userInterfaceIdiom == .pad
            ? ["--ui-testing-static-animations"] : []
        try launchAndResetSession(extraLaunchArguments: recoveryLaunchArguments)
        #else
        try launchAndResetSession()
        #endif
        try signIn(using: fixture, waitsForAccount: false)
        let resume = element("resumeItem-\(fixture.directItemID)")
        do {
            #if os(iOS)
            try waitForExistence(resume, timeout: 25, named: "seeded direct-video resume item")
            // Interruption monitors run on a user interaction, not an AX
            // query. This ordinary Home scroll is restored below before
            // selecting the seeded Continue Watching card.
            element("homeScreen").swipeUp()
            try revealInLibraryViewport([resume], in: element("homeScreen"))
            guard waitUntil(timeout: 15, condition: {
                guard let viewport = try? self.applicationViewport() else { return false }
                return self.hasVisibleFrame(resume)
                    && self.isFullyVerticallyContained(resume.frame, in: viewport)
            }) else {
                throw ProofFailure.timeout("Timed out waiting for visible seeded direct-video resume item.")
            }
            // The SwiftUI rail reports this rendered card as non-hittable to
            // XCTest. Its observed visible frame is the user-facing tap target.
            resume.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.3)).tap()
            #else
            #if os(tvOS)
            try waitForExistence(resume, timeout: 25, named: "seeded direct-video resume item")
            #else
            try waitForHittable(resume, timeout: 25, named: "seeded direct-video resume item")
            #endif
            try activate(resume)
            #endif
        } catch {
            let evidence = await api.recoveryHomeEvidence(identity: identity, itemID: fixture.directItemID,
                                                          title: fixture.directItemTitle)
            let attachment = XCTAttachment(string: "expected=\(fixture.directItemID) title=\(fixture.directItemTitle) ax=\(resume.exists ? resume.identifier : "absent") \(evidence)")
            attachment.name = "Compatible-video recovery Home evidence"
            attachment.lifetime = .keepAlways
            add(attachment)
            throw error
        }
        try waitForExistence(element("detailScreen"), timeout: 25, named: "direct-video detail screen")
        #if os(iOS)
        let play = element("playButton")
        try waitForExistence(play, timeout: 20, named: "playButton")
        try revealDetailElement(play, in: element("detailScreen"), named: "playButton", requiresHittable: false)
        // The rendered Resume control can be visible without XCTest reporting
        // it hittable; tap its observed public button frame directly.
        if usesStandardPhonePushedChrome {
            let visible = play.frame.intersection(try standardPhoneNavigationViewport())
            application!.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: visible.midX, dy: visible.midY)).tap()
        } else {
            play.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "native playback screen")
        let time = element("playbackTime")
        try waitForExistence(time, timeout: 20, named: "playback time")
        #else
        let time = try startPlayback(using: "playButton")
        #endif
        let initial = try waitForPlaybackValue(time, timeout: 25) { $0 >= 10 }
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 2 }

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused recovered video transport")
        XCUIRemote.shared.press(.menu)
        XCUIRemote.shared.press(.down)
        try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "recovered video settings")
        #else
        #if os(iOS)
        try tapVideoTransportAfterChromeHides(named: "pause recovered video before settings")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5)
        let info = try waitForHittable(element("videoInfoButton"), timeout: 8, named: "recovered playback info control")
        info.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("videoSettingsPanel"), timeout: 10, named: "recovered playback info")
        #else
        try activate(element("videoSettingsButton"))
        #endif
        #endif
        #if !os(iOS)
        try activate(element("videoSettingsTab-info"))
        #endif
        let method = element("videoPlaybackMethod")
        try waitForExistence(method, timeout: 10, named: "compatible video playback method")
        XCTAssertTrue(accessibilityText(method).contains("HLS"),
                      "The format retry must use the server's compatible HLS rendition.")

        let evidence = try await api.initialVideoFormatRecovery()
        XCTAssertTrue(evidence.faultedDirectStream, "The proof fixture must reject the initial direct MP4 with an HTTP 200 format fault.")
        XCTAssertEqual(evidence.compatibilityRequests, 1, "A format failure permits exactly one compatible-video retry.")
        XCTAssertTrue(evidence.compatibilityProfile, "The retry must negotiate only H.264/SDR-compatible playback.")
        XCTAssertTrue(evidence.sameMediaSource, "The compatible retry must retain the selected source version.")
        XCTAssertTrue(evidence.preservedOptions, "The compatible retry must retain quality, selected tracks, and its playback position.")

        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        guard disappears(element("videoSettingsPanel"), timeout: 10) else {
            throw ProofFailure.timeout("Recovered video settings did not dismiss through the remote Back action.")
        }
        #else
        try activate(element("videoSettingsDoneButton"))
        #endif
        try attachPlaybackFrame(named: "Initial direct format failure recovered through compatible HLS")
        try closePlayer()
        completed = true
    }

    func journeyClientDemuxVideoFormatRecovery(failServerCandidate: Bool, failMediaCandidate: Bool = false) async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        try await api.resetInitialVideoFormatRecovery()
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 6)

        var arguments = ["--ui-testing-client-demux-format-failure"]
        if failServerCandidate { arguments.append("--ui-testing-server-video-recovery-format-failure") }
        if failMediaCandidate { arguments.append("--ui-testing-server-media-recovery-format-failure") }
        try launchAndResetSession(extraLaunchArguments: arguments)
        try signIn(using: fixture, waitsForAccount: false)
        #if os(iOS)
        // Authenticate in the fixture helper's portrait viewport, then enter
        // playback in landscape to avoid the known portrait detail Play overlap.
        // This proves recovery without claiming that separate layout issue fixed.
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        if usesStandardPhonePushedChrome {
            // Use the standard-phone Library selector, which taps the visible
            // tab frame before choosing Movies without returning through the
            // stale Home Browse tour entry.
            try selectLibraryCategory("movies", screen: "libraryScreen")
        } else {
            try selectDestination("Video", screen: "libraryScreen")
        }
        let item = try revealLibraryItem(id: fixture.convertedItemID, named: "recovery item")
        item.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 25, named: "detail screen")
        let play = element("playButton")
        try waitForExistence(play, timeout: 20, named: "play button")
        play.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let resumeChoice = element("resumePlaybackChoice")
        XCTAssertTrue(waitUntil(timeout: 35) { resumeChoice.exists || self.element("playbackScreen").exists })
        if resumeChoice.exists { try activate(resumeChoice.buttons.matching(NSPredicate(format: "label == %@", "Resume")).firstMatch) }
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "native playback screen")
        let playbackTime = element("playbackTime")
        try waitForExistence(playbackTime, timeout: 20, named: "playback time")
        #else
        try openLibraryItem(id: fixture.convertedItemID)
        let playbackTime = try startPlayback(using: "playButton")
        #endif
        let startedAt = try waitForPlaybackValue(playbackTime, timeout: 25) { $0 >= 5 }
        _ = try waitForPlaybackValue(playbackTime, timeout: 20) { $0 >= startedAt + 2 }

        let evidence = try await api.initialVideoFormatRecovery()
        XCTAssertFalse(evidence.faultedDirectStream, "The client-demux fault must not replace the source bytes.")
        XCTAssertEqual(evidence.compatibilityRequests, failMediaCandidate ? 2 : 1,
                       "The selected media playlist must reuse the normal server session; only its failure may request final compatibility.")
        XCTAssertTrue(evidence.normalRecoveryProfile,
                      "The first client-demux recovery must retain the normal server capability profile.")
        XCTAssertEqual(evidence.compatibilityProfile, failMediaCandidate,
                       "A master rejection alone must retain normal HEVC/HDR capability; only media failure may request H.264/SDR compatibility.")
        XCTAssertTrue(evidence.sameMediaSource, "Recovery must retain the selected media source.")
        XCTAssertTrue(evidence.preservedOptions, "Recovery must retain the selected tracks and resume position.")
        try closePlayer()
    }

    #if os(iOS)
    func journeyVideoDeviceControls() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }
        let fixture = try loadFixture()
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-hero-artwork"])
        try signIn(using: fixture, waitsForAccount: false)
        let play = element("featuredPlayButton")
        try waitForExistence(play, timeout: 20, named: "Home hero Play")
        XCTAssertTrue(hasVisibleFrame(play))
        play.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "video player")
        let surface = element("videoControlsSurface")
        XCTAssertTrue(waitUntil(timeout: 15) { surface.frame.width > 200 && surface.frame.height > 200 })
        try tapVideoTransportAfterChromeHides(named: "pause for device control checks")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5)
        let diagnostics = element("videoGestureDiagnostics")
        func level(_ key: String) -> Double {
            let value = diagnostics.value as? String ?? ""
            return value.split(separator: " ").first { $0.hasPrefix(key + "=") }
                .flatMap { Double($0.dropFirst(key.count + 1)) } ?? -1
        }
        let initial = level("brightness")
        XCTAssertGreaterThanOrEqual(initial, 0, "The video must attach to its display's brightness control.")
        XCTAssertEqual(level("brightnessTargetMatchesDisplay"), 1,
                       "Brightness writes must target the display containing the player, including Duo's inner display. \(diagnostics.value ?? "absent")")
        // Start in the middle of the surface, clear of transport and timeline controls.
        let upward = initial < 0.7
        let startY = upward ? 0.6 : 0.35
        let endY = upward ? 0.35 : 0.6
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: startY))
            .press(forDuration: 0.05, thenDragTo: surface.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: endY)))
        XCTAssertTrue(waitUntil(timeout: 3) { abs(level("brightnessRequested") - initial) > 0.1 && level("brightnessRequested") >= 0 },
                      "A left-side swipe must keep sending brightness changes to UIScreen beyond its first event. \(diagnostics.value ?? "absent")")
        let brightnessReadback = XCTAttachment(string: diagnostics.value as? String ?? "absent")
        brightnessReadback.name = "Brightness target and display readback"
        brightnessReadback.lifetime = .keepAlways
        add(brightnessReadback)
        XCTAssertTrue((diagnostics.value as? String ?? "").contains("last=endAdjustment(LuminaCore.VideoGestureAdjustment.brightness)"),
                      "The brightness swipe must finish without being cancelled by its own writes.")
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.35))
            .press(forDuration: 0.05, thenDragTo: surface.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.6)))
        XCTAssertTrue((diagnostics.value as? String ?? "").contains("last=endAdjustment(LuminaCore.VideoGestureAdjustment.volume)"),
                      "The volume swipe must reach its adjustment handler.")
        XCTAssertEqual(level("playerVolume"), 1, accuracy: 0.001,
                       "Volume swipes must leave AVPlayer at unity and adjust the device output instead.")
        try waitForHittable(element("closePlayerButton"), timeout: 5, named: "visible video controls")
        XCTAssertFalse(element("videoSystemVolume").exists)
        XCTAssertFalse(element("videoCastVolume").exists)
        try attachFrame(named: "Video controls use device volume and brightness")
        let close = element("closePlayerButton")
        if !close.exists {
            surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        }
        try waitForExistence(close, timeout: 5, named: "device-controls player close")
        // Use the rendered control rather than a semantic hit point that can
        // target the inactive panel after Duo posture changes.
        close.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(disappears(element("playbackScreen"), timeout: 10),
                      "Closing the device-control proof must dismiss the player")
    }

    func journeyVideoSurfaceGestures() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Library", screen: "libraryScreen")
        let movie = element("libraryItem-\(fixture.directItemID)")
        try waitForExistence(movie, timeout: 25, named: "gesture proof movie")
        try revealInLibraryViewport([movie], in: element("libraryScreen"))
        // Poster context menus can suppress AX's hittable trait; use the
        // observed, visible poster frame to open its native detail link.
        movie.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 25, named: "gesture proof detail")
        // The phone's floating tab bar covers this fixture's portrait hero CTA.
        // Enter playback from landscape, then test the portrait player itself.
        XCUIDevice.shared.orientation = .landscapeLeft
        let time = try startPlayback(using: "playButton")
        XCUIDevice.shared.orientation = .portrait
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= 2 }
        let surface = element("videoControlsSurface")
        let diagnostics = element("videoGestureDiagnostics")
        print("GESTURE_DIAGNOSTICS \(diagnostics.value ?? "absent")")
        if surface.exists {
            print("GESTURE_SURFACE frame=\(surface.frame) enabled=\(surface.isEnabled) hit=\(surface.isHittable) value=\(surface.value ?? "nil") window=\(application!.windows.firstMatch.frame)")
        }
        try waitForHittable(surface, timeout: 10, named: "video gesture surface")
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        let pause = element("videoPlayPauseButton")
        try waitForHittable(pause, timeout: 10, named: "video pause")
        pause.tap()
        print("GESTURE_AFTER_PAUSE \(element("videoGestureDiagnostics").value ?? "absent") label=\(pause.label)")
        XCTAssertTrue(waitUntil(timeout: 10) { pause.label == "Play" })
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertFalse(waitUntil(timeout: 3) {
            (Double(time.value as? String ?? "") ?? pausedAt) > pausedAt + 0.5
        }, "Pause must stop the real playback clock, not merely change the transport label.")
        pause.tap()
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 >= pausedAt + 1 }
        pause.tap()
        XCTAssertTrue(waitUntil(timeout: 10) { pause.label == "Play" },
                      "A resumed video must be pausable again before gesture seeking.")
        // Paused playback deliberately keeps chrome visible. Hide that known
        // public state through its center, then use a real surface
        // tap to prove the user-facing reveal route without resuming playback.
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        XCTAssertTrue(disappears(pause, timeout: 3),
                      "The visible paused playback chrome must hide through its public surface action.")
        let diagnosticsBeforeReveal = diagnostics.value as? String ?? "absent"
        let actionsBeforeReveal = diagnosticsBeforeReveal
            .split(separator: " ")
            .first(where: { $0.hasPrefix("actions=") })
            .flatMap { Int($0.dropFirst("actions=".count)) }
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        try waitForHittable(pause, timeout: 10, named: "revealed paused controls")
        let revealRecorded = waitUntil(timeout: 2) {
            let value = diagnostics.value as? String ?? ""
            guard let actionToken = value.split(separator: " ").first(where: { $0.hasPrefix("actions=") }),
                  let actionsAfterReveal = Int(actionToken.dropFirst("actions=".count)) else {
                return false
            }
            return actionsAfterReveal > (actionsBeforeReveal ?? -1) && value.contains("last=toggleControls")
        }
        print("GESTURE_HUD_REVEAL before=\(diagnosticsBeforeReveal) after=\(diagnostics.value ?? "absent")")
        XCTAssertTrue(revealRecorded,
                      "A physical surface reveal after hiding paused chrome must record toggleControls and reveal Pause.")
        let initial = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        let right = surface.coordinate(withNormalizedOffset: CGVector(dx: 0.75, dy: 0.4))
        right.doubleTap()
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 9 }
        XCTAssertEqual(pause.label, "Play", "Skipping must retain the current pause intent.")
        let afterSkip = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        right.press(forDuration: 0.7)
        XCTAssertEqual(pause.label, "Play", "Holding and releasing while paused must not start playback.")
        XCTAssertEqual(try waitForPlaybackValue(time, timeout: 5) { _ in true }, afterSkip, accuracy: 1)
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.4))
            .press(forDuration: 0.05, thenDragTo: surface.coordinate(withNormalizedOffset: CGVector(dx: 0.7, dy: 0.4)))
        let afterForwardScrub = try waitForPlaybackValue(time, timeout: 15) { $0 >= afterSkip + 2 }
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.65, dy: 0.4))
            .press(forDuration: 0.05, thenDragTo: surface.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.4)))
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 <= afterForwardScrub - 2 }
        XCTAssertEqual(pause.label, "Play", "Surface scrubbing must retain pause intent.")
        let timeline = element("videoTimeline")
        try waitForHittable(timeline, timeout: 10, named: "app-owned video timeline")
        timeline.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.5))
            .press(forDuration: 0.05, thenDragTo: timeline.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.5)))
        let timelineClock = try XCTUnwrap(timeline.value as? String)
        let timelineTarget = timelineClock.split(separator: ":").compactMap { Double($0) }.reduce(0) { $0 * 60 + $1 }
        _ = try waitForPlaybackValue(time, timeout: 15) { abs($0 - timelineTarget) <= 1 }
        XCTAssertEqual(pause.label, "Play", "Timeline dragging must retain pause intent.")
        let portraitFrame = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        portraitFrame.name = "Video gesture controls and app-owned timeline"
        portraitFrame.lifetime = .keepAlways
        add(portraitFrame)
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(waitUntil(timeout: 15) { surface.frame.width > surface.frame.height })
        let landscapeTime = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.75, dy: 0.4)).doubleTap()
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= landscapeTime + 9 }
        XCTAssertEqual(pause.label, "Play", "Landscape gesture seeking must retain pause intent.")
        let landscapeFrame = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        landscapeFrame.name = "Landscape surface gesture seeking"
        landscapeFrame.lifetime = .keepAlways
        add(landscapeFrame)
        try closePlayer()
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail after closing video")
        let savedAtClose = try await api.playbackSeconds(identity: identity, itemID: fixture.directItemID)
        try await Task.sleep(nanoseconds: 3_000_000_000)
        let savedAfterQuietPeriod = try await api.playbackSeconds(identity: identity, itemID: fixture.directItemID)
        XCTAssertEqual(savedAfterQuietPeriod, savedAtClose, accuracy: 1,
                       "Closing video must stop server progress updates rather than leave an orphaned background player.")
    }



    func journeyVideoPlaybackSurvivesOrientationChanges() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

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
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= 2 }
        try waitForVideoPlaybackDiagnostics(
            requested: true,
            playing: true,
            requiredTokens: ["policy=\(AVPlayerAudiovisualBackgroundPlaybackPolicy.automatic.rawValue)"],
            timeout: 10
        )

        let surface = element("videoControlsSurface")
        try waitForHittable(surface, timeout: 10, named: "video controls before rotation")
        let playingInPortrait = try waitForPlaybackValue(time, timeout: 5) { _ in true }

        XCUIDevice.shared.orientation = .landscapeLeft
        guard waitUntil(timeout: 15, condition: { surface.frame.width > surface.frame.height }) else {
            throw ProofFailure.ui("The player did not reflow to landscape during active playback.")
        }
        try waitForVideoPlaybackDiagnostics(requested: true, playing: true, timeout: 10)
        let playingInLandscape = try waitForPlaybackValue(time, timeout: 15) { $0 >= playingInPortrait + 1 }

        XCUIDevice.shared.orientation = .portrait
        guard waitUntil(timeout: 15, condition: { surface.frame.height > surface.frame.width }) else {
            throw ProofFailure.ui("The player did not reflow to portrait during active playback.")
        }
        try waitForVideoPlaybackDiagnostics(requested: true, playing: true, timeout: 10)
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= playingInLandscape + 1 }

        try tapVideoTransportAfterChromeHides(named: "video pause after orientation changes")
        let transport = element("videoPlayPauseButton")
        XCTAssertTrue(waitUntil(timeout: 10) { transport.label == "Play" },
                      "Pausing after orientation changes must update the real transport state.")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 10)
        let pausedAt = try waitForPlaybackValue(time, timeout: 5) { _ in true }

        XCUIDevice.shared.orientation = .landscapeRight
        guard waitUntil(timeout: 15, condition: { surface.frame.width > surface.frame.height }) else {
            throw ProofFailure.ui("The player did not reflow to landscape while paused.")
        }
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 10)
        try await Task.sleep(for: .seconds(2))
        let stillPaused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, pausedAt + 0.5,
                                 "An explicitly paused video must remain paused through an orientation change.")
        try closePlayer()
    }

    func journeyVideoPresentationRebuild(clientDemux: Bool = false) async throws {
        let fixture = try loadFixture()
        let itemID = clientDemux ? fixture.convertedItemID : fixture.directItemID
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: itemID, seconds: 12)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-rebuild-video-presentation"])
        try signIn(using: fixture)
        try openLibraryItem(id: itemID)
        _ = try startPlayback(using: "playButton")
        let time = element("playbackTime")
        let before = try waitForPlaybackValue(time, timeout: 15) { $0 >= 15 }
        func playerIdentity() -> String? {
            (element("videoGestureDiagnostics").value as? String ?? "")
                .split(separator: " ").first { $0.hasPrefix("player=") }.map(String.init)
        }
        let player = try XCTUnwrap(playerIdentity())
        try activate(element("rebuildVideoPresentationButton"))
        XCTAssertTrue(waitUntil(timeout: 8) { playerIdentity() == player }, "A new presentation must retain the active player.")
        let after = try waitForPlaybackValue(time, timeout: 10) { $0 >= before + 2 }
        XCTAssertGreaterThan(after, before, "Rebuilding the playback screen must advance from the live position, not the original resume position.")
        XCTAssertEqual(playerIdentity(), player)
        try tapVideoTransportAfterChromeHides(named: "pause before rebuilding presentation")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5)
        let paused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        try activate(element("rebuildVideoPresentationButton"))
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5)
        try await Task.sleep(for: .seconds(2))
        let retainedPause = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertEqual(retainedPause, paused, accuracy: 0.5)
        XCTAssertEqual(playerIdentity(), player)
        try closePlayer()
        _ = try startPlayback(using: "playButton")
        XCTAssertNotEqual(playerIdentity(), player, "Explicit Close must end the old session; a subsequent Play must create a fresh player.")
        try waitForVideoPlaybackDiagnostics(requested: true, playing: true, timeout: 15)
        try tapVideoTransportAfterChromeHides(named: "pause fresh session before closing")
        try closePlayer()
    }

    func journeyPlaybackDiagnostics() async throws {
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
        try activate(element("playButton"))
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "playing video")
        let settings = element("videoSettingsButton")
        if !settings.isHittable { try activate(element("videoControlsSurface")) }
        try activate(settings)
        try activate(element("videoSettingsTab-info"))
        let start = element("playbackDiagnosticsStartButton")
        try revealDetailElement(start, in: element("videoSettingsPanel"), named: "start diagnostics", requiresHittable: true)
        try activate(start)
        try waitForExistence(element("playbackDiagnosticsRecording"), timeout: 5, named: "recording diagnostics")
        try activate(element("videoSettingsDoneButton"))
        if ProcessInfo.processInfo.environment["LUMINA_DIAGNOSTICS_FOLD"] == "1" {
            print("LUMINA_DIAGNOSTICS fold capture window started")
            try await Task.sleep(for: .seconds(25))
        } else { try await Task.sleep(for: .seconds(5)) }
        try tapVideoTransportAfterChromeHides(named: "pause while recording diagnostics")
        try activate(element("videoSeekForwardButton"))
        try await Task.sleep(for: .seconds(2))
        try activate(element("videoSettingsButton"))
        try activate(element("videoSettingsTab-info"))
        let stop = element("playbackDiagnosticsStopButton")
        try revealDetailElement(stop, in: element("videoSettingsPanel"), named: "finish diagnostics", requiresHittable: true)
        try activate(stop)
        let share = element("playbackDiagnosticsShareButton")
        try waitForHittable(share, timeout: 10, named: "completed diagnostic export")
        XCTAssertFalse(element("playbackDiagnosticsError").exists)
        try activate(share)
        let app = try XCTUnwrap(application)
        XCTAssertTrue(waitUntil(timeout: 10) {
            app.otherElements["ActivityListView"].exists
                || app.buttons["Save to Files"].exists
                || app.buttons["Copy"].exists
        }, "Sharing the completed on-device capture must open the native share sheet.")
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = "Playback diagnostics native export"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func journeyVideoRateSettlesDuringPlayback() async throws {
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
        let started = try waitForPlaybackValue(time, timeout: 20) { $0 >= 2 }
        func rateEvents() throws -> Int {
            let value = element("videoGestureDiagnostics").value as? String ?? ""
            guard let field = value.split(separator: " ").first(where: { $0.hasPrefix("rateEvents=") }),
                  let count = Int(field.dropFirst("rateEvents=".count).filter(\.isNumber)) else {
                throw ProofFailure.ui("Missing native rate-change diagnostics.")
            }
            return count
        }
        let before = try rateEvents()
        _ = try waitForPlaybackValue(time, timeout: 12) { $0 >= started + 5 }
        let changes = try rateEvents() - before
        XCTAssertLessThanOrEqual(changes, 20,
                                 "Steady playback must settle, not recursively rewrite the AVPlayer rate (\(changes) events in five seconds).")
        try closePlayer()
    }

    func journeyVideoDuoContinuity() async throws {
        guard ProcessInfo.processInfo.environment["LUMINA_DUO_TRANSITIONS"] == "1" else {
            throw XCTSkip("Opt-in live pose test: switch Closed and Open in Device Hub during the transition window.")
        }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 12)
        XCUIDevice.shared.orientation = .portrait
        try launchAndResetSession()
        try signIn(using: fixture)
        if ProcessInfo.processInfo.environment["LUMINA_DUO_ENTRY"] == "home" {
            let home = element("homeScreen")
            try waitForExistence(home, timeout: 30, named: "Home")
            let episode = element("resumeItem-\(fixture.episodeID)")
            try revealInLibraryViewport([episode], in: home)
            try activate(episode)
            _ = try startPlayback(using: "playButton")
        } else {
            try selectDestination("Video", screen: "libraryScreen")
            try activate(element("libraryCategory-tv"))
            try activate(element("libraryItem-\(fixture.showID)"))
            try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons")
            let collection = element("videoCollectionScreen")
            try selectCollectionSeason(fixture.seasonID)
            let play = element("playButton")
            try revealDetailElement(play, in: collection, named: "season play", requiresHittable: true)
            try activate(play)
            try waitForExistence(element("playbackScreen"), timeout: 35, named: "episode player")
        }
        let time = element("playbackTime")
        var previous = try waitForPlaybackValue(time, timeout: 15) { $0 >= 13 }
        let startedAt = Date()
        let initialPosition = previous
        let diagnostics = element("videoGestureDiagnostics")
        let baseline = ProcessInfo.processInfo.environment["LUMINA_DUO_BASELINE"] == "1"
        let initial = baseline ? "" : diagnostics.value as? String ?? ""
        let player = initial.split(separator: " ").first { $0.hasPrefix("player=") }
        if !baseline { XCTAssertNotNil(player) }
        var sizes = Set<String>()
        // This checks transport continuity, not uninterrupted rendered frames.
        // Device Hub recordings can black out inactive displays during a fold.
        let deadline = Date().addingTimeInterval(45)
        print("LUMINA_DUO playing transition window started")
        while Date() < deadline {
            let next = try waitForPlaybackValue(time, timeout: 3) { $0 >= previous + 1 }
            XCTAssertLessThanOrEqual(next - previous, 4, "Changing a Duo pose must not skip the playhead forward.")
            XCTAssertGreaterThanOrEqual(next - initialPosition, Date().timeIntervalSince(startedAt) - 1.5,
                                         "A Duo handoff must not lose playback time or stall while the clock keeps running.")
            previous = next
            let current = baseline ? "" : diagnostics.value as? String ?? ""
            XCTAssertEqual(current.split(separator: " ").first { $0.hasPrefix("player=") }, player,
                           "Changing a Duo pose must retain the playing session.")
            sizes.insert(String(describing: element("videoControlsSurface").frame.size))
            print("LUMINA_DUO time=\(next) \(current)")
        }
        XCTAssertGreaterThanOrEqual(sizes.count, 2, "Exercise Closed and Open Duo layouts during this live transition test.")
        let transport = element("videoPlayPauseButton")
        if !transport.isHittable { try activate(element("videoControlsSurface")) }
        try activate(transport)
        if !baseline { try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 5) }
        let paused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        print("LUMINA_DUO paused transition window started")
        let pausedDeadline = Date().addingTimeInterval(20)
        while Date() < pausedDeadline {
            let value = try waitForPlaybackValue(time, timeout: 3) { _ in true }
            XCTAssertEqual(value, paused, accuracy: 0.5, "Changing a Duo pose must preserve explicit Pause.")
            if !baseline { XCTAssertEqual((diagnostics.value as? String ?? "").split(separator: " ").first { $0.hasPrefix("player=") }, player) }
            try await Task.sleep(for: .seconds(1))
        }
        try closePlayer()
    }

    func journeyVideoPlaybackSurvivesForegroundInactiveTransition() async throws {
        XCUIDevice.shared.orientation = .portrait
        defer { XCUIDevice.shared.orientation = .portrait }

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
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= 2 }
        try waitForVideoPlaybackDiagnostics(requested: true, playing: true, timeout: 10)
        let playingBeforeInterruption = try waitForPlaybackValue(time, timeout: 5) { _ in true }

        // Control Center changes the foreground scene to inactive without sending
        // the app to the background. This is the lifecycle transition that must
        // preserve an explicitly requested video playback session.
        let app = try applicationWindow()
        let controlCenterStart = app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.01))
        let controlCenterEnd = app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.72))
        controlCenterStart.press(forDuration: 0.05, thenDragTo: controlCenterEnd)
        try waitForVideoPlaybackDiagnostics(
            requested: true,
            playing: true,
            scenePhase: "inactive",
            timeout: 10
        )

        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        // Control Center occupies the trailing panel; a physical tap in the exposed leading backdrop
        // dismisses it without issuing the bottom-edge Home gesture.
        springBoard.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.5)).tap()
        let postDismissFrame = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        postDismissFrame.name = "Control Center dismissed before active-phase verification"
        postDismissFrame.lifetime = .keepAlways
        add(postDismissFrame)
        guard waitUntil(timeout: 15, condition: { self.application!.state == .runningForeground }) else {
            throw ProofFailure.ui("Lumina did not return to the foreground after dismissing Control Center.")
        }
        try waitForVideoPlaybackDiagnostics(
            requested: true,
            playing: true,
            scenePhase: "active",
            requiredTokens: ["appState=0", "gestureEnabled=true"],
            timeout: 10
        )
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= playingBeforeInterruption + 1 }
        try closePlayer()
    }

    func journeyVideoPlaybackSurvivesHomeForegroundTransition() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 0)

        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-force-video-system-pause"])
        try signIn(using: fixture)
        try openLibraryItem(id: fixture.directItemID)

        let time = try startPlayback(using: "playButton")
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= 2 }
        try waitForVideoPlaybackDiagnostics(
            requested: true,
            playing: true,
            requiredTokens: ["policy=\(AVPlayerAudiovisualBackgroundPlaybackPolicy.pauses.rawValue)"],
            timeout: 10
        )
        let playingBeforeHome = try waitForPlaybackValue(time, timeout: 5) { _ in true }

        // This exercises the real Home → foreground lifecycle boundary with a
        // controlled native AVPlayer system-pause fault, not a fake player.
        try backgroundApplication()
        application!.activate()
        try waitForExistence(element("playbackScreen"), timeout: 20, named: "video player after returning from Home")
        try waitForVideoPlaybackDiagnostics(
            requested: true,
            playing: true,
            scenePhase: "active",
            requiredTokens: ["systemPauseInjections=1"],
            timeout: 15
        )
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= playingBeforeHome + 1 }

        try tapVideoTransportAfterChromeHides(named: "video pause before Home")
        let transport = element("videoPlayPauseButton")
        XCTAssertTrue(waitUntil(timeout: 10) { transport.label == "Play" },
                      "The explicit pause must be visible before testing foreground restoration.")
        try waitForVideoPlaybackDiagnostics(requested: false, playing: false, timeout: 10)
        let pausedBeforeHome = try waitForPlaybackValue(time, timeout: 5) { _ in true }

        try backgroundApplication()
        application!.activate()
        try waitForExistence(element("playbackScreen"), timeout: 20, named: "paused video player after returning from Home")
        try waitForVideoPlaybackDiagnostics(
            requested: false,
            playing: false,
            scenePhase: "active",
            requiredTokens: ["systemPauseInjections=1"],
            timeout: 15
        )
        try await Task.sleep(for: .seconds(2))
        let stillPaused = try waitForPlaybackValue(time, timeout: 5) { _ in true }
        XCTAssertLessThanOrEqual(stillPaused, pausedBeforeHome + 0.5,
                                 "Returning from Home must not resume explicitly paused video playback.")
        try closePlayer()
    }

    func journeyShowEpisodeDownloads() async throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        let secondSeasonEpisode = try XCTUnwrap(fixture.secondSeasonEpisodeID)
        func chooseAppearance(_ option: String) throws {
            let picker = element("deviceAppearancePicker")
            try waitForExistence(picker, timeout: 15, named: "Appearance setting")
            picker.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            let choice = element("deviceAppearancePicker-choices").buttons
                .matching(NSPredicate(format: "label == %@", option)).firstMatch
            try waitForExistence(choice, timeout: 10, named: "Appearance choice \(option)")
            choice.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            try assertDeviceSetting("deviceAppearancePicker", contains: option)
        }
        try launchAndResetSession()
        try signIn(using: fixture, waitsForAccount: false)
        destinationControl("Account").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("accountScreen"), timeout: 20, named: "Account appearance settings")
        try chooseAppearance("Light")
        try selectLibraryCategory("tv", screen: "libraryScreen")
        let show = try revealLibraryItem(id: fixture.showID, named: "show to download")
        show.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let collection = element("videoCollectionScreen")
        try waitForExistence(collection, timeout: 20, named: "show with seasons and episodes")
        let download = element("downloadEpisodesButton")
        func openDownloadPicker() throws {
            try revealDetailElement(download, in: collection, named: "show download action", requiresHittable: false)
            let visible = download.frame.intersection(try standardPhoneNavigationViewport())
            application!.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: visible.midX, dy: visible.midY)).tap()
            try waitForExistence(element("episodeDownloadPicker"), timeout: 15, named: "episode download picker")
        }
        try openDownloadPicker()
        let first = element("episodeDownload-\(fixture.episodeID)")
        try waitForHittable(first, timeout: 20, named: "episode download choice")
        try activate(first)
        XCTAssertEqual(element("episodeDownloadSelectionCount").label, "1 selected")
        try attachFrame(named: "Lumina episode downloads — Light appearance")
        try activate(element("cancelEpisodeDownloadsButton"))
        let back = application!.buttons.matching(identifier: "BackButton").firstMatch
        try waitForExistence(back, timeout: 10, named: "show Back button")
        back.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "Library after leaving show")
        destinationControl("Account").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("accountScreen"), timeout: 20, named: "Account appearance settings")
        try chooseAppearance("Dark")
        try selectLibraryCategory("tv", screen: "libraryScreen")
        let darkShow = try revealLibraryItem(id: fixture.showID, named: "show in Dark appearance")
        darkShow.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(collection, timeout: 20, named: "show in Dark appearance")
        try openDownloadPicker()
        try waitForHittable(first, timeout: 20, named: "new episode selection")
        XCTAssertFalse(element("confirmEpisodeDownloadsButton").isEnabled, "Cancel must discard the selection without downloading.")
        try activate(element("selectDownloadSeasonButton"))
        XCTAssertEqual(element("episodeDownloadSelectionCount").label, "2 selected")
        try activate(element("selectDownloadSeasonButton"))
        XCTAssertFalse(element("confirmEpisodeDownloadsButton").isEnabled)
        try activate(first)
        try activate(element("episodeDownloadSeasonPicker"))
        try activate(menuAction("Season 2"))
        try waitForHittable(element("episodeDownload-\(secondSeasonEpisode)"), timeout: 20, named: "second season download choice")
        XCTAssertEqual(element("episodeDownloadSelectionCount").label, "1 selected",
                       "Selections must survive changing seasons.")
        try activate(element("selectDownloadSeasonButton"))
        XCTAssertEqual(element("episodeDownloadSelectionCount").label, "2 selected")
        try attachFrame(named: "Lumina episode downloads — Dark appearance")
        try activate(element("confirmEpisodeDownloadsButton"))
        XCTAssertTrue(disappears(element("episodeDownloadPicker"), timeout: 10))
        try openDownloadPicker()
        try waitForExistence(first, timeout: 20, named: "first episode download state")
        XCTAssertTrue(waitUntil(timeout: 60) { first.label.contains("On this device") }, "The individually selected episode must finish downloading.")
        try activate(element("episodeDownloadSeasonPicker"))
        try activate(menuAction("Season 2"))
        let secondSeason = element("episodeDownload-\(secondSeasonEpisode)")
        try waitForExistence(secondSeason, timeout: 20, named: "downloaded season")
        XCTAssertTrue(waitUntil(timeout: 60) { secondSeason.label.contains("On this device") }, "The selected whole season must finish downloading.")
        try activate(element("cancelEpisodeDownloadsButton"))
        let app = application!
        app.terminate()
        app.launchArguments.append("--ui-testing-offline")
        app.launch()
        try waitForExistence(element("offlineLibraryScreen"), timeout: 20, named: "device downloads after offline relaunch")
        try waitForExistence(element("offlineItem-\(fixture.episodeID)"), timeout: 15, named: "individually downloaded episode")
        let savedSeasonEpisode = element("offlineItem-\(secondSeasonEpisode)")
        try waitForExistence(savedSeasonEpisode, timeout: 15, named: "downloaded season episode")
        XCTAssertFalse(element("offlineItem-\(fixture.secondEpisodeID)").exists, "An unselected episode must not be downloaded.")
        try activate(savedSeasonEpisode)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "downloaded episode playing offline")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 2 }
        try attachPlaybackFrame(named: "Selected season episode playing from device storage offline")
        try closePlayer()
        completed = true
    }

    func journeyDownloadedVideoPlaysOffline(mkv: Bool) async throws {
        let fixture = try loadFixture()
        let itemID = mkv ? fixture.convertedItemID : fixture.directItemID
        try launchAndResetSession()
        try signIn(using: fixture)
        try openLibraryItem(id: itemID)
        let actions = element("titleActionsMenu")
        try revealDetailControl(actions, named: "title actions menu")
        try activate(actions)
        XCTAssertFalse(element("convertButton").exists)
        try activate(menuAction("Download to Device"))
        try waitForExistence(element("videoDownloadedStatus"), timeout: 60, named: "completed video download")

        try returnToLibrary()
        try selectDestination("Account", screen: "accountScreen")
        let downloads = element("deviceDownloadsButton")
        XCTAssertFalse(hasVisibleFrame(element("homeSearchButton")), "Account owns settings search, not global media search.")
        XCTAssertFalse(hasVisibleFrame(element("homeDownloadsButton")), "Account already exposes Downloads under Storage.")
        try revealInLibraryViewport([downloads], in: element("accountScreen"))
        try activate(downloads)
        try waitForExistence(element("offlineLibraryScreen"), timeout: 15, named: "all device downloads from Account")
        try waitForExistence(element("offlineItem-\(itemID)"), timeout: 10, named: "video in Account downloads")
        XCTAssertEqual(application!.buttons.matching(identifier: "offlineSearchButton").count, 1)

        let app = application!
        app.terminate()
        app.launchArguments.append("--ui-testing-offline")
        app.launch()
        try waitForExistence(element("offlineLibraryScreen"), timeout: 15, named: "offline library after cold launch")
        XCTAssertFalse(element("homeScreen").exists, "Remote Home must be hidden while disconnected.")
        let saved = element("offlineItem-\(itemID)")
        try waitForExistence(saved, timeout: 10, named: "persisted downloaded video")
        try activate(saved)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "offline video player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 25) { $0 >= 2 }
        try attachPlaybackFrame(named: mkv ? "Downloaded MKV playing offline" : "Downloaded movie playing offline")
        try closePlayer()
        app.terminate()
        app.launch()
        try waitForExistence(element("offlineItem-\(itemID)"), timeout: 15, named: "download retained after second offline launch")
        try attachFrame(named: "Only device downloads shown offline")
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
        try stopAudioPlayer()
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
        if usesStandardPhonePushedChrome {
            try performStandardPhoneEdgeGesture(named: "music destination")
        } else {
            try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        }
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
        let episode = element("videoCollectionItem-\(fixture.secondEpisodeID)")
        XCTAssertTrue(focus(episode, timeout: 20))
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("collectionEpisodeTitle")).contains(fixture.secondEpisodeTitle)
        }, "Focus must preview the episode before Select.")
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "playback directly from episode Select")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 3 }
        XCUIRemote.shared.press(.playPause)
        let chrome = element("videoPlaybackChrome")
        if !hasVisibleFrame(chrome) { XCUIRemote.shared.press(.select) }
        try waitForExistence(chrome, timeout: 10, named: "TV video controls overlay")
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("videoPlayPauseButton").label == "Play" })
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
        try activate(element("homeBrowse-music"))
        let albumHero = element("audioFeaturedAlbum")
        try waitForExistence(albumHero, timeout: 20, named: "Music hero")
        let album = element("audioItem-\(fixture.audioAlbumID)")
        for _ in 0..<8 where !album.exists { XCUIRemote.shared.press(.down) }
        XCTAssertTrue(focus(album, timeout: 25))
        XCTAssertLessThan(albumHero.frame.minY, -1)
        for _ in 0..<6 { XCUIRemote.shared.press(.up) }
        XCTAssertTrue(waitUntil(timeout: 8) { abs(albumHero.frame.minY) < 2 },
                      "Music Up must restore its artwork and header.")
        try attachFrame(named: "TV Music hero restored from albums")
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
        try launchAndResetSession()
        try await performanceQuietPhase("login_form")
        try signIn(using: fixture)
        let targetID = nextUp ? fixture.secondEpisodeID : fixture.episodeID
        let resume = element("\(nextUp ? "nextUpItem" : "resumeItem")-\(targetID)")
        for _ in 0..<10 where !resume.exists { XCUIRemote.shared.press(.down) }
        try waitForExistence(resume, timeout: 25, named: "Continue Watching episode")
        XCTAssertTrue(focus(resume, timeout: 25), "Continue Watching must accept remote focus.")
        let artwork = resume.descendants(matching: .any).matching(identifier: "tvArtwork-\(targetID)").firstMatch
        try waitForExistence(artwork, timeout: 10, named: "Continue Watching poster source")
        let source = try XCTUnwrap(artwork.value as? String)
        XCTAssertTrue(source.hasSuffix("/Images/Primary"))
        XCTAssertNotEqual(source, "/Items/\(targetID)/Images/Primary",
                          "A portrait episode tile must load series/catalog poster artwork, not its landscape still.")
        try attachFrame(named: nextUp ? "TV Next Up uses series poster artwork" : "TV Continue Watching uses series poster artwork")
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
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "Next Up show")
        let play = element("playButton")
        XCTAssertTrue(focus(play, timeout: 20))
        XCUIRemote.shared.press(.select)
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "Next Up playback after cached navigation")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 2 }
        try attachPlaybackFrame(named: "Cached TV browsing retains decoded episode playback")
        try closePlayer()
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
        for _ in 0..<4 {
            XCUIRemote.shared.press(.down)
            if resumeRow.items.contains(where: { element("resumeItem-\($0.Id)").hasFocus }) { break }
        }
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
        try attachFrame(named: "TV Home with pinned categories and immersive artwork")
        try activate(featuredTitle)
        try assertFeaturedDestination()
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
        try enter(requestShowTitle, into: element("requestsSearchField"))
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
        }, "Selecting an alphabet letter must jump within the wall and focus its first matching title.")
        XCTAssertTrue((element("libraryScreen").value as? String)?.contains("catalog=123 ") == true,
                      "An alphabet jump must preserve all preloaded titles.")

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
        try waitForExistence(element("playbackScreen"), timeout: 25, named: "video started by clicking focused Play")
        let startedAt = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { $0 >= startedAt + 3 }
        try attachPlaybackFrame(named: "Video started from detail with Siri Remote")
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
        XCUIRemote.shared.press(.playPause)
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

    #if os(iOS)
    func journeyCompleteCatalogSupportsFastPosterScrolling() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Library", screen: "libraryScreen")
        let readiness = element("libraryArtworkReadiness")
        try waitForExistence(readiness, timeout: 25, named: "catalog wall")
        XCTAssertTrue(waitUntil(timeout: 30) {
            guard let progress = self.libraryArtworkReadiness(readiness) else { return false }
            let catalog = self.accessibilityText(readiness).range(of: #"catalog=(\d+)"#, options: .regularExpression)
            let count = catalog.flatMap { Int(self.accessibilityText(readiness)[$0].dropFirst("catalog=".count)) } ?? 0
            return count >= 120 && progress.ready == progress.total
        }, "The complete catalog must be scrollable before reaching the first page boundary")
        let library = element("libraryScreen")
        let cards = application!.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-"))
        let first = cards.allElementsBoundByIndex.first { self.hasVisibleFrame($0) }
        let firstIdentifier = try XCTUnwrap(first).identifier
        try attachFrame(named: "Complete lightweight catalog before flick")
        library.swipeUp(velocity: .fast)
        XCTAssertTrue(waitUntil(timeout: 15) {
            !self.hasVisibleFrame(self.element(firstIdentifier))
        }, "A fast flick must move through the complete wall")
        try attachFrame(named: "Poster wall after fast flick")
        let viewport = try applicationViewport().insetBy(dx: 0, dy: 110)
        let poster = try XCTUnwrap(cards.allElementsBoundByIndex.first {
            self.isFullyVerticallyContained($0.frame, in: viewport)
        }, "The landing viewport must contain a complete poster")
        let landingIdentifier = poster.identifier
        let landingFrame = poster.frame
        // Layered native controls can report false AX hittability. A real tap
        // and a detail destination prove that the visible poster is usable.
        poster.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 20, named: "poster detail after fast flick")
        try activate(destinationControl("Library"))
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "retained full catalog")
        XCTAssertEqual(element(landingIdentifier).frame.midY, landingFrame.midY, accuracy: 8,
                       "Returning from a landing poster must preserve the scroll position")
        library.swipeDown(velocity: .fast)
        XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(self.element(firstIdentifier)) },
                      "Reverse scrolling must retain the beginning of the wall")
        try attachFrame(named: "Poster wall after reverse flick")
        completed = true
    }

    /// Verifies the tab-selection path rather than the Back path: returning to
    /// Library must reveal its retained catalog immediately, while its category
    /// chooser can still deliberately select a different catalog.
    func journeyNativeLibraryWarmReturn() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        guard UIDevice.current.userInterfaceIdiom != .pad else {
            throw XCTSkip("iPad uses its unified category navigation instead of the native Library tab.")
        }

        try selectLibraryCategory("movies", screen: "libraryScreen")
        let moviesRoot = element("libraryScreen")
        let movieCards = application!.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-"))
        try waitForExistence(movieCards.firstMatch, timeout: 25, named: "prepared Movies cards before deep scroll")
        for _ in 0..<4 {
            try scrollLibrary(moviesRoot, up: true)
        }
        let movieViewport = moviesRoot.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        let movie = try XCTUnwrap(
            application!.descendants(matching: .any)
                .matching(NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-"))
                .allElementsBoundByIndex.first { candidate in
                    self.isFullyVerticallyContained(candidate.frame, in: movieViewport)
                },
            "A deeply scrolled Movies root must expose a fully visible public title card."
        )
        let movieIdentifier = movie.identifier
        let movieRootFrame = movie.frame
        try attachFrame(named: "Movies Library root before detail")
        movie.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("detailScreen"), timeout: 20, named: "movie detail")
        try attachFrame(named: "Movie detail before Library tab return")

        let libraryTab = destinationControl("Library")
        try waitForHittable(libraryTab, timeout: 15, named: "Library tab from movie detail")
        try activate(libraryTab)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Movies Library after tab return")
        let returnedMovie = element(movieIdentifier)
        try waitForExistence(returnedMovie, timeout: 15, named: "returned deeply scrolled Movies fixture")
        XCTAssertFalse(hasVisibleFrame(element("detailScreen")),
                       "Selecting Library from a movie detail must return to the catalog root, not leave the detail active.")
        XCTAssertEqual(returnedMovie.frame.midY, movieRootFrame.midY, accuracy: 8,
                       "Library tab return must retain the visible Movies catalog position.")
        try waitForPreparedLibraryArtwork()
        try attachFrame(named: "Movies Library root after tab return")

        // Re-enter the same category through the visible Library chooser (or
        // Home's category row on wider phones). The retained Movies root and
        // its visible item must remain usable.
        try selectLibraryCategory("movies", screen: "libraryScreen")
        let reenteredMovie = element(movieIdentifier)
        try waitForExistence(reenteredMovie, timeout: 15, named: "re-entered deeply scrolled Movies fixture")
        XCTAssertEqual(reenteredMovie.frame.midY, movieRootFrame.midY, accuracy: 8,
                       "Reselecting a category must not reconstruct the retained Movies root.")
        try waitForPreparedLibraryArtwork()
        try attachFrame(named: "Movies Library retained through category selection")

        try selectLibraryCategory("tv", screen: "libraryScreen")
        let videoOptions = element("videoOptionsMenu")
        try waitForExistence(videoOptions, timeout: 15, named: "TV library options")
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.accessibilityText(self.element("videoOptionsMenu")).localizedCaseInsensitiveContains("shows")
        }, "Selecting TV must configure the visible Library query for Shows before revealing a card.")
        try waitForPreparedLibraryArtwork()
        let show = element("libraryItem-\(fixture.showID)")
        try waitForExistence(show, timeout: 15, named: "visible TV show fixture")
        XCTAssertTrue(hasVisibleFrame(show))
        let showRootFrame = show.frame
        try attachFrame(named: "TV Library root before show detail")
        show.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "TV show detail")
        try attachFrame(named: "TV show detail before Library tab return")

        try waitForHittable(libraryTab, timeout: 15, named: "Library tab from TV show detail")
        try activate(libraryTab)
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "TV Library after tab return")
        let returnedShow = element("libraryItem-\(fixture.showID)")
        try waitForExistence(returnedShow, timeout: 15, named: "returned TV show fixture")
        XCTAssertFalse(hasVisibleFrame(element("videoCollectionScreen")),
                       "Selecting Library from a TV show must return to the TV catalog root, not leave the collection active.")
        XCTAssertEqual(returnedShow.frame.midY, showRootFrame.midY, accuracy: 8,
                       "Library tab return must retain the visible TV catalog position.")
        try waitForPreparedLibraryArtwork()
        try attachFrame(named: "TV Library root after tab return")

        // The native tab's semantic tap can resolve to an adjacent tab on this
        // simulator. Use its observed center and verify the actual destination.
        destinationControl("Account").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("accountScreen"), timeout: 20, named: "Account settings")
        let search = element("settingsSearchField")
        try waitForExistence(search, timeout: 15, named: "settings search")
        search.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        search.typeText("artwork cache\n")
        let usage = element("deviceArtworkCacheUsage")
        let clear = element("deviceClearArtworkCacheButton")
        try waitForExistence(usage, timeout: 15, named: "Artwork cache usage")
        try waitForExistence(clear, timeout: 15, named: "Artwork cache clear control")
        try revealInLibraryViewport([usage, clear], in: element("accountScreen"))
        XCTAssertTrue(hasVisibleFrame(clear), "The public cache-clear control must be visible")
        XCTAssertTrue(waitUntil(timeout: 15) {
            !self.artworkCacheUsageReportsZero(self.element("deviceArtworkCacheUsage"))
        }, "Browsing the prepared Library roots must retain artwork on disk before clearing it.")
        try attachFrame(named: "Artwork cache before clearing")
        clear.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(waitUntil(timeout: 2) {
            !self.element("deviceClearArtworkCacheButton").isEnabled
                || self.artworkCacheUsageReportsZero(self.element("deviceArtworkCacheUsage"))
        }, "Artwork cache clear must either disable while deleting or finish with zero usage.")
        XCTAssertTrue(waitUntil(timeout: 15) {
            self.artworkCacheUsageReportsZero(self.element("deviceArtworkCacheUsage"))
        }, "Clearing artwork cache must report zero retained artwork bytes.")
        XCTAssertTrue(waitUntil(timeout: 10) { self.element("deviceClearArtworkCacheButton").isEnabled },
                      "Artwork cache clear control must re-enable after its asynchronous deletion completes.")
        try attachFrame(named: "Artwork cache cleared")
        completed = true
    }

    private func artworkCacheUsageReportsZero(_ usage: XCUIElement) -> Bool {
        let text = accessibilityText(usage).lowercased()
        return text.range(of: #"\b0\s*bytes?\b"#, options: .regularExpression) != nil
    }

    private func selectLibraryCategory(_ category: String, screen: String) throws {
        if usesStandardPhonePushedChrome {
            destinationControl("Library").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            try waitForExistence(element("libraryScreen"), timeout: 20, named: "Library category root")
            var visibleMenu: XCUIElement?
            _ = waitUntil(timeout: 10) {
                visibleMenu = self.application!.buttons.matching(identifier: "compactBrowseMenu")
                    .allElementsBoundByIndex.first { self.hasVisibleFrame($0) }
                return visibleMenu != nil
            }
            if let menu = visibleMenu {
                // Use the observed public button frame; native floating toolbar
                // controls can be visible while XCTest reports them unhittable.
                menu.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            } else {
                let overflow = application!.buttons.matching(identifier: "OverflowBarButtonItem").firstMatch
                try waitForHittable(overflow, timeout: 15, named: "Library toolbar overflow")
                try activate(overflow)
                try activate(menuAction("Libraries"))
            }
            let identified = element("compactCategory-\(category)")
            let title = category == "tv" ? "TV" : category.capitalized
            let choice = identified.exists ? identified : menuAction(title)
            guard waitUntil(timeout: 15, condition: { self.hasVisibleFrame(choice) }) else {
                throw ProofFailure.timeout("The Library \(title) menu choice did not become visible.")
            }
            choice.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        } else {
            try selectDestination("Home", screen: "homeScreen")
            let categoryRow = element("homeBrowse-\(category)")
            try waitForHittable(categoryRow, timeout: 20, named: "Home \(category) category")
            try activate(categoryRow)
        }
        try waitForExistence(element(screen), timeout: 20, named: "\(category) Library root")
    }

    private func waitForPreparedLibraryArtwork() throws {
        let readiness = element("libraryArtworkReadiness")
        try waitForExistence(readiness, timeout: 20, named: "prepared Library artwork")
        XCTAssertTrue(waitUntil(timeout: 30) {
            guard let progress = self.libraryArtworkReadiness(readiness) else { return false }
            return progress.ready == progress.total && progress.total > 0
        }, "A retained Library return must expose fully prepared artwork before its root is presented.")
    }

    private func libraryArtworkReadiness(_ readiness: XCUIElement) -> (ready: Int, total: Int)? {
        let text = accessibilityText(readiness)
        let expression = #"ready=(\d+)\s+total=(\d+)"#
        guard let match = text.range(of: expression, options: .regularExpression) else { return nil }
        let values = text[match].split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
        guard values.count == 2 else { return nil }
        return (values[0], values[1])
    }
    #endif

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

    func journeyStandardPhoneNavigation() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        guard application!.windows.firstMatch.frame.width < 450 else {
            throw XCTSkip("This workflow targets a standard portrait iPhone.")
        }

        #if os(iOS)
        // Home Search is a normal pushed route on standard phones. Its search
        // field lives in the safe in-content header, then native edge Back
        // unwinds one destination at a time.
        try waitForHittable(element("homeSearchButton"), timeout: 20, named: "Home Search")
        try activate(element("homeSearchButton"))
        let searchScreen = element("homeSearchScreen")
        try assertStandardPhonePushedChrome(searchScreen, named: "Home Search")
        let homeSearch = element("homeSearchField")
        try waitForHittable(homeSearch, timeout: 15, named: "standard-phone Home search field")
        XCTAssertGreaterThanOrEqual(homeSearch.frame.minY, try protectedStatusBarBottom() - 1,
                                    "The in-content Home search field must not overlap the status bar.")
        try activate(homeSearch)
        homeSearch.typeText(fixture.directItemTitle)
        let searchResult = element("homeSearchResult-\(fixture.directItemID)")
        try waitForHittable(searchResult, timeout: 20, named: "standard-phone Home search result")
        try activate(searchResult)
        let searchedDetail = element("detailScreen")
        try performStandardPhoneLeadingEdgeBack(from: searchedDetail, to: searchScreen,
                                                 named: "Home search result")
        try performStandardPhoneLeadingEdgeBack(from: searchScreen, to: element("homeScreen"),
                                                 named: "Home Search")
        #endif

        try waitForHittable(element("homeSearchButton"), timeout: 20, named: "Home Search")
        XCTAssertFalse(element("homeBrowsingRow").exists)
        XCTAssertFalse(element("homeBrowse-movies").exists)
        let menu = element("compactBrowseMenu")
        try waitForHittable(menu, timeout: 15, named: "Compact Browse menu")
        XCTAssertGreaterThanOrEqual(menu.frame.minX, element("homeMasthead").frame.maxX + 8)
        try attachFrame(named: "Standard iPhone Home with bottom navigation")
        try activate(menu)
        try activate(element("compactCategory-music"))
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Music from Browse")
        #if os(iOS)
        // Keep a real current track while navigating to a different pushed
        // destination below. This proves the shared compact header retains
        // the existing Now Playing affordances instead of replacing them.
        try activate(revealMusicAlbum(fixture, named: "standard-phone album"))
        let album = element("musicCollectionScreen")
        try waitForExistence(album, timeout: 25, named: "standard-phone album tracks")
        let track = element("musicItem-\(fixture.audioTrackID)")
        try revealInLibraryViewport([track], in: album)
        try activate(track)
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "standard-phone direct audio player")
        let audioTime = element("playbackTime")
        try waitForExistence(audioTime, timeout: 20, named: "standard-phone audio playback time")
        let beforePushedBrowse = try waitForPlaybackValue(audioTime, timeout: 15) { $0 >= 1 }
        try closePlayer()
        #endif
        XCTAssertFalse(element("libraryCategories").exists)
        try selectDestination("Home", screen: "homeScreen")
        try waitForHittable(element("homeSearchButton"), timeout: 15, named: "Search after returning Home")
        XCTAssertFalse(element("homeBrowsingRow").exists)

        // Home category choices must reset the retained Library navigation
        // stack, even when the user returns to the same category.
        try activate(menu)
        try activate(element("compactCategory-tv"))
        let show = element("libraryItem-\(fixture.showID)")
        try waitForHittable(show, timeout: 20, named: "TV show from Home Browse")
        try activate(show)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "TV show collection")
        #if os(iOS)
        try performStandardPhoneLeadingEdgeBack(from: element("videoCollectionScreen"), to: element("libraryScreen"),
                                                 named: "TV show collection")
        #endif

        try selectDestination("Home", screen: "homeScreen")
        try activate(menu)
        try activate(element("compactCategory-tv"))
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "TV library root after Home selection")
        try waitForHittable(show, timeout: 20, named: "TV show at its library root")
        XCTAssertFalse(element("videoCollectionScreen").exists,
                       "Choosing TV from Home must not retain the previous show collection.")
        XCTAssertFalse(element("detailScreen").exists,
                       "Choosing TV from Home must not retain a previous media detail.")

        try selectDestination("Home", screen: "homeScreen")
        try activate(menu)
        try activate(element("compactCategory-movies"))
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "Movies library root after Home selection")
        let movieOptions = element("videoOptionsMenu")
        var openedMoviesOverflow = false
        if !movieOptions.exists {
            // With compact Browse and an active Now Playing bar, UIKit moves
            // the public Video options action into its visible More overflow.
            // The first-level row exposes the active category; its submenu
            // does not retain the app's accessibility identifier.
            let overflow = application!.buttons.matching(identifier: "OverflowBarButtonItem").firstMatch
            try waitForHittable(overflow, timeout: 15, named: "Movies toolbar More overflow")
            try activate(overflow)
            openedMoviesOverflow = true
            let overflowedOptions = application!.buttons.matching(
                NSPredicate(format: "label CONTAINS[c] %@", "Video options")
            ).firstMatch
            try waitForHittable(overflowedOptions, timeout: 15, named: "Movies library options in overflow")
            XCTAssertTrue(accessibilityText(overflowedOptions).localizedCaseInsensitiveContains("movies"),
                          "Movies chosen from Home must configure the Movies library root.")
        } else {
            try waitForHittable(movieOptions, timeout: 20, named: "Movies library options")
            XCTAssertTrue(accessibilityText(movieOptions).localizedCaseInsensitiveContains("movies"),
                          "Movies chosen from Home must configure the Movies library root.")
        }
        #if os(iOS)
        if openedMoviesOverflow {
            // Dismiss the system popover through the noninteractive leading
            // header margin. The selected tab remains intentionally covered
            // while UIKit owns the popover's hit testing.
            let window = application!.windows.firstMatch
            window.coordinate(withNormalizedOffset: CGVector(dx: 0.06, dy: 0.14)).tap()
            let overflowedOptions = application!.buttons.matching(
                NSPredicate(format: "label CONTAINS[c] %@", "Video options")
            ).firstMatch
            XCTAssertTrue(disappears(overflowedOptions, timeout: 10),
                          "Dismissing Movies toolbar overflow must restore the underlying catalog.")
        }
        #endif
        #if os(iOS)
        _ = try revealLibraryItem(id: fixture.directItemID, named: "movie in its requested library root")
        #else
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 20,
                              named: "movie in its requested library root")
        #endif
        XCTAssertFalse(element("videoCollectionScreen").exists,
                       "Choosing Movies from Home must not retain the previous TV collection.")
        XCTAssertFalse(element("detailScreen").exists,
                       "Choosing Movies from Home must not retain a previous media detail.")

        #if os(iOS)
        let movie = element("libraryItem-\(fixture.directItemID)")
        try waitForHittable(movie, timeout: 20, named: "movie to test standard-phone chrome")
        try activate(movie)
        let movieDetail = element("detailScreen")
        try assertStandardPhonePushedChrome(movieDetail, named: "movie detail with current audio")
        let nowPlaying = element("nowPlayingBar")
        let toggle = element("nowPlayingPauseButton")
        try waitForHittable(nowPlaying, timeout: 15, named: "Now Playing while viewing movie detail")
        try waitForHittable(toggle, timeout: 15, named: "Now Playing pause while viewing movie detail")
        XCTAssertTrue(nowPlaying.label.contains(fixture.audioTrackTitle),
                      "The pushed movie header must retain the active track destination.")
        try activate(toggle)
        XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Play" },
                      "The compact playback action must pause the current track from a pushed movie.")
        try activate(toggle)
        XCTAssertTrue(waitUntil(timeout: 10) { toggle.label == "Pause" },
                      "The compact playback action must resume the same track from a pushed movie.")
        try activate(nowPlaying)
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "full player from pushed movie")
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle,
                       "Opening Now Playing from a pushed movie must resume the existing track, not restart browsing.")
        XCTAssertGreaterThanOrEqual(try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }, beforePushedBrowse)
        try stopAudioPlayer()
        XCTAssertTrue(disappears(element("playbackScreen"), timeout: 15), "Stopping the current track must close the full player.")
        try waitForExistence(movieDetail, timeout: 15, named: "movie detail after closing full player")
        try performStandardPhoneLeadingEdgeBack(from: movieDetail, to: element("libraryScreen"),
                                                 named: "movie detail")

        try selectDestination("Account", screen: "accountScreen")
        try activateDeviceControl("deviceSubtitleAppearanceButton")
        try performStandardPhoneLeadingEdgeBack(from: element("deviceSubtitleAppearanceScreen"), to: element("accountScreen"),
                                                 named: "caption appearance")

        try selectTourBooksRoot()
        try revealBookItem(element("bookCatalogButton"))
        try activate(element("bookCatalogButton"))
        XCTAssertTrue(waitUntil(timeout: 15) { !self.element("bookCatalogButton").exists },
                      "Opening the full Books catalog must replace the shelf's Browse all route.")
        try assertStandardPhonePushedChrome(element("booksScreen"), named: "Books catalog")
        try performStandardPhoneEdgeGesture(named: "Books catalog")
        try waitForHittable(element("bookCatalogButton"), timeout: 15, named: "Books shelf after leading-edge Back")

        guard let requestMovieKey = fixture.requestMovieKey else {
            throw XCTSkip("Generate the linked Requests movie fixture.")
        }
        try selectDestination("Requests", screen: "requestsScreen")
        let requestMovie = element("requestDetails-\(requestMovieKey)")
        try waitForHittable(requestMovie, timeout: 25, named: "linked Requests movie")
        try activate(requestMovie)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "linked Request detail")
        try openRequestVault(expectedScreen: "detailScreen", named: "linked Request movie")
        try performStandardPhoneLeadingEdgeBack(from: element("detailScreen"), to: element("requestDetailScreen"),
                                                 named: "Requests vault movie")
        try closeRequestDetailToRequests()

        try selectDestination("Home", screen: "homeScreen")
        try performStandardPhoneEdgeGesture(named: "Home root")
        try waitForExistence(element("homeScreen"), timeout: 10, named: "Home after root leading-edge gesture")
        #endif

        try attachFrame(named: "Standard iPhone Home category returns to requested library root")
    }

    #if os(iOS)
    func journeyClosedDisplayCategoryWidth() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        let viewport = application!.windows.firstMatch.frame
        guard viewport.width >= 450, viewport.width < 500, viewport.height > viewport.width else {
            throw XCTSkip("This layout contract targets the closed Duo portrait display.")
        }
        let row = element("homeBrowsingRow")
        let masthead = element("homeMasthead")
        let movies = element("homeBrowse-movies")
        // This fixture has no Anime library; Music is its third, equally wide label.
        let third = element("homeBrowse-music")
        let watchlist = element("homeBrowse-watchlist")
        try waitForHittable(third, timeout: 20, named: "Third category")
        XCTAssertGreaterThanOrEqual(movies.frame.minX, masthead.frame.maxX + 8,
                                    "Categories must clear the Lumina logo.")
        XCTAssertGreaterThanOrEqual(movies.frame.minX, row.frame.minX)
        XCTAssertLessThanOrEqual(third.frame.maxX, row.frame.maxX - 8,
                                 "The third category must fit completely before the scroll edge.")
        XCTAssertEqual(movies.frame.midY, masthead.frame.midY, accuracy: 6)

        let heroArtwork = application!.descendants(matching: .any)
            .matching(identifier: "homeHeroArtworkBounds").firstMatch
        try waitForExistence(heroArtwork, timeout: 15, named: "Closed Duo hero artwork bounds")
        XCTAssertTrue(waitUntil(timeout: 15) { self.hasVisibleFrame(heroArtwork) },
                      "Closed Duo Home must expose a visible hero artwork bounds marker.")
        XCTAssertLessThanOrEqual(heroArtwork.frame.minY, viewport.minY + 2,
                                 "Closed Duo hero artwork must reach the top edge of Home.")
        XCTAssertGreaterThanOrEqual(heroArtwork.frame.maxY, viewport.maxY - 0.5,
                                    "Closed Duo hero artwork must reach the bottom edge without a black band.")
        try attachFrame(named: "Closed Duo three complete categories")
        let homeCategoryWidth = row.frame.width
        try assertClosedNavigationClearsCamera([masthead, row])
        let moreCategories = element("categoryScrollForward")
        try waitForHittable(moreCategories, timeout: 10, named: "More categories affordance")
        XCTAssertLessThanOrEqual(third.frame.maxX, moreCategories.frame.minX - 4,
                                 "Three complete categories must remain clear of the overflow control.")
        try activate(moreCategories)
        try waitForHittable(watchlist, timeout: 10, named: "Watchlist revealed by More categories")
        try activate(element("categoryScrollBack"))
        try waitForHittable(movies, timeout: 10, named: "Movies revealed by Previous categories")

        let initialRefreshes = try homeRefreshCount()
        try revealHomeMenuControl(watchlist)
        try waitForHittable(watchlist, timeout: 10, named: "Horizontally revealed Watchlist category")
        XCTAssertEqual(try homeRefreshCount(), initialRefreshes,
                       "Horizontally scrolling Home categories must not begin Home refresh.")

        let toolbarDragStart = row.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        toolbarDragStart.press(forDuration: 0.1,
                               thenDragTo: toolbarDragStart.withOffset(CGVector(dx: 0, dy: 260)),
                               withVelocity: .slow,
                               thenHoldForDuration: 0.5)
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(try homeRefreshCount(), initialRefreshes,
                       "A downward gesture beginning in the horizontal category toolbar must not refresh Home.")
        XCTAssertLessThanOrEqual(try categoryVerticalPull(), 2,
                                 "The horizontal category toolbar must not vertically rubber-band.")

        let contentDragStart = element("homeScreen").coordinate(
            withNormalizedOffset: CGVector(dx: 0.25, dy: 0.5)
        )
        contentDragStart.press(forDuration: 0.1,
                               thenDragTo: contentDragStart.withOffset(CGVector(dx: 0, dy: 250)),
                               withVelocity: .slow,
                               thenHoldForDuration: 0.5)
        XCTAssertTrue(waitUntil(timeout: 10) {
            (try? self.homeRefreshCount()).map { $0 > initialRefreshes } ?? false
        }, "A downward gesture in Home content must retain pull-to-refresh.")

        try revealHomeMenuControl(third)
        try activate(third)
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Third category destination")
        try attachFrame(named: "Closed Library categories after selection from Home")

        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        try assertClosedDuoLibraryHeader()
        try attachFrame(named: "Closed Duo Library header keeps logo categories and actions separate")
        let libraryCategories = element("libraryCategories")
        let libraryThird = element("libraryCategory-music")
        try waitForHittable(libraryThird, timeout: 10, named: "third Library category without swiping")
        XCTAssertGreaterThanOrEqual(libraryCategories.frame.width, homeCategoryWidth - 4,
                                    "Closed Library must use the same available category width as Home.")
        try assertClosedNavigationClearsCamera([element("homeMasthead"), libraryCategories])
        try activate(libraryThird)
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Music selected from the wide closed Library row")
        try activate(revealMusicAlbum(fixture, named: "closed navigation album"))
        try startMusicCollectionPlayback()
        try activate(element("audioPlayPauseButton"))
        try closePlayer()
        try selectDestination("Home", screen: "homeScreen")
        let activeRow = element("homeBrowsingRow")
        try waitForHittable(element("homeBrowse-music"), timeout: 15, named: "third category with Now Playing")
        XCTAssertEqual(activeRow.frame.width, homeCategoryWidth, accuracy: 1,
                       "Now Playing must not shrink the closed category row when actions stack vertically.")
        try assertClosedNavigationClearsCamera([element("homeMasthead"), activeRow,
                                               element("nowPlayingBar"), element("nowPlayingPauseButton")])
        try attachFrame(named: "Closed Home categories retain their width with Now Playing")
        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        try assertClosedDuoLibraryHeader()
        try assertClosedNavigationClearsCamera([element("homeMasthead"), element("libraryCategories"),
                                               element("nowPlayingBar"), element("nowPlayingPauseButton")])
        try attachFrame(named: "Closed Library categories retain their width with Now Playing")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "player from closed navigation")
        try stopAudioPlayer()
        completed = true
    }

    private func assertClosedNavigationClearsCamera(_ controls: [XCUIElement]) throws {
        let raw = try XCTUnwrap(element("navigationOcclusions").value as? String)
        let regions = raw.split(separator: ";").compactMap { record -> CGRect? in
            let values = record.split(separator: ",").compactMap { Double($0) }
            guard values.count == 4 else { return nil }
            return CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
        }
        let attachment = XCTAttachment(string: "reserved=\(raw) controls=\(controls.map(\.frame))")
        attachment.name = "Closed navigation hardware clearance"
        attachment.lifetime = .keepAlways
        add(attachment)
        let cameraRegions = regions.filter { $0.minY < 80 && $0.width < 80 && $0.height < 80 }
        XCTAssertFalse(cameraRegions.isEmpty, "The closed-display proof must expose its camera occlusion.")
        for region in regions {
            for control in controls {
                XCTAssertFalse(control.frame.intersects(region), "Navigation must clear the camera punch hole: \(region).")
            }
        }
    }

    func journeyEmptyWatchlistCentering() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: false)
        try launchAndResetSession()
        let viewport = try applicationViewport()
        guard UIDevice.current.userInterfaceIdiom == .phone,
              viewport.width >= 600, viewport.height > viewport.width else {
            throw XCTSkip("Use the actual open Duo portrait display for empty Watchlist verification.")
        }
        func tapVisibleControl(_ control: XCUIElement) throws {
            try waitForExistence(control, timeout: 15, named: "Watchlist navigation control")
            guard hasVisibleFrame(control) else { throw ProofFailure.ui("Watchlist control must be on screen.") }
            let frame = control.frame
            try applicationWindow().coordinate(withNormalizedOffset: CGVector(
                dx: (frame.midX - viewport.minX) / viewport.width,
                dy: (frame.midY - viewport.minY) / viewport.height)).tap()
        }
        try signIn(using: fixture)
        try waitForExistence(element("featuredTitleButton"), timeout: 20, named: "loaded Home hero")
        try tapVisibleControl(element("homeBrowse-watchlist"))
        let title = application!.staticTexts["No matches on Home"]
        let message = application!.staticTexts["Choose Home or add something to your watchlist."]
        try waitForExistence(title, timeout: 20, named: "empty Watchlist title")
        try waitForExistence(message, timeout: 10, named: "empty Watchlist guidance")
        let categories = element("homeBrowsingRow").frame
        let group = title.frame.union(message.frame)
        XCTAssertGreaterThan(group.minY, categories.maxY + 80,
                             "The empty Watchlist must clear the category row.")
        XCTAssertEqual(group.midY, viewport.midY, accuracy: viewport.height * 0.15,
                       "Empty Watchlist guidance belongs in the middle of the page.")
        try attachFrame(named: "Open Duo portrait empty Watchlist centered below categories")

        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: true)
        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 25, named: "Home after saved Watchlist relaunch")
        try tapVisibleControl(element("homeBrowse-watchlist"))
        let populated = element("featuredTitleButton")
        try waitForExistence(populated, timeout: 25, named: "populated Watchlist hero after relaunch")
        XCTAssertEqual(populated.label, fixture.directItemTitle)
        XCTAssertFalse(title.exists, "A populated Watchlist must replace the empty state.")
        XCTAssertGreaterThan(populated.frame.minY, categories.maxY,
                             "Populated Watchlist content must retain normal hero placement.")
        try attachFrame(named: "Open Duo portrait populated Watchlist retains its hero")
    }

    func journeyRequestsPortraitHeader() throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        let viewport = try applicationViewport()
        guard UIDevice.current.userInterfaceIdiom == .phone,
              viewport.width >= 600, viewport.height > viewport.width else {
            throw XCTSkip("Use the actual open Duo portrait display for Requests header verification.")
        }
        func tapVisibleControl(_ control: XCUIElement) throws {
            guard hasVisibleFrame(control) else { throw ProofFailure.ui("Requests control must be visibly on screen before tapping.") }
            let frame = control.frame
            let viewport = try applicationViewport()
            try applicationWindow().coordinate(withNormalizedOffset: CGVector(
                dx: (frame.midX - viewport.minX) / viewport.width,
                dy: (frame.midY - viewport.minY) / viewport.height)).tap()
        }
        try signIn(using: fixture)
        let tab = try waitForHittable(destinationControl("Requests"), timeout: 15, named: "visible Requests tab")
        let tabFrame = tab.frame
        let tabEvidence = XCTAttachment(string: "viewport=\(viewport) Requests=\(tabFrame)")
        tabEvidence.name = "Open Duo Requests tab geometry"
        tabEvidence.lifetime = .keepAlways
        add(tabEvidence)
        try tapVisibleControl(tab)
        try waitForExistence(element("requestsScreen"), timeout: 20, named: "Requests from its visible tab")
        let movieKey = try XCTUnwrap(fixture.requestMovieKey)
        try waitForExistence(element("requestDetails-\(movieKey)"), timeout: 30, named: "Requests featured title")
        try attachFrame(named: "Open Duo portrait Requests header")

        let search = element("requestsSearchButton")
        let options = element("requestsOptionsMenu")
        for control in [search, options] {
            XCTAssertTrue(hasVisibleFrame(control))
            XCTAssertGreaterThan(control.frame.minX, viewport.maxX - 90, "Requests actions belong on the right utility rail.")
            XCTAssertGreaterThanOrEqual(control.frame.minY, viewport.minY + 88, "Requests actions must clear the physical status corner.")
            XCTAssertGreaterThanOrEqual(control.frame.width, 44)
            XCTAssertGreaterThanOrEqual(control.frame.height, 44)
        }
        XCTAssertFalse(search.frame.intersects(options.frame))

        let hero = element("requestsFeaturedHero")
        try waitForExistence(hero, timeout: 10, named: "Requests hero artwork")
        XCTAssertLessThanOrEqual(hero.frame.minY, viewport.minY + 1,
                                 "Requests artwork must extend to the top edge beneath navigation.")

        try tapVisibleControl(search)
        let field = element("searchField")
        try waitForExistence(field, timeout: 10, named: "Requests search on the open display")
        try tapVisibleControl(field)
        field.typeText(try XCTUnwrap(fixture.requestUnavailableShowTitle) + "\n")
        let result = try revealRequestPoster(try XCTUnwrap(fixture.requestUnavailableShowKey), named: "Requests search result")
        XCTAssertLessThanOrEqual(result.frame.maxX, options.frame.minX - 8,
                                 "Requests results must leave room for the utility rail.")
        try attachFrame(named: "Open Duo portrait Requests search")
        try tapVisibleControl(application!.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close"])).firstMatch)
        XCTAssertTrue(disappears(field, timeout: 10))
        try tapVisibleControl(options)
        try activate(element("requestsMyListButton"))
        try waitForExistence(element("requestHistoryScreen"), timeout: 15, named: "Requests history from right overflow")
        try activate(menuAction("Done"))
        XCTAssertTrue(disappears(element("requestHistoryScreen"), timeout: 10))
        try attachFrame(named: "Open Duo portrait Requests after Search and history")
    }

    func journeyPortraitNavigationWithMusic() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        let viewport = try applicationViewport()
        guard UIDevice.current.userInterfaceIdiom == .phone,
              viewport.width >= 600, viewport.height > viewport.width else {
            throw XCTSkip("Use the open Duo portrait display for this navigation workflow.")
        }
        try signIn(using: fixture)
        try selectTourAudioRoot()
        try activate(revealMusicAlbum(fixture, named: "portrait navigation album"))
        try startMusicCollectionPlayback()
        try activate(element("audioPlayPauseButton"))
        try closePlayer()
        try selectDestination("Home", screen: "homeScreen")
        try attachFrame(named: "Open portrait Home with paused Now Playing")
        try assertPortraitNavigation(categories: "homeBrowsingRow", firstCategory: "homeBrowse-movies", search: "homeSearchButton")
        let hero = application!.descendants(matching: .any).matching(
            NSPredicate(format: "label BEGINSWITH %@", "Featured titles,")
        ).firstMatch
        guard hero.frame.minY <= viewport.minY + 4 else {
            throw ProofFailure.ui("Home artwork must extend behind the portrait header instead of beginning below a full-width bar: \(hero.frame).")
        }
        let pause = element("nowPlayingPauseButton")
        try activate(pause)
        guard waitUntil(timeout: 10, condition: { pause.label == "Pause" }) else {
            throw ProofFailure.ui("Portrait rail play must resume the current track.")
        }
        try activate(pause)
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "player from portrait artwork control")
        XCTAssertEqual(currentAudioTitle(), fixture.audioTrackTitle)
        XCTAssertEqual(element("audioPlayPauseButton").label, "Play")
        try closePlayer()
        try activate(element("homeSearchButton"))
        try waitForExistence(element("homeSearchScreen"), timeout: 15, named: "portrait Home search")
        try performStandardPhoneEdgeGesture(named: "portrait Home search")
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after portrait search")
        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        try assertPortraitNavigation(categories: "libraryCategories", firstCategory: "libraryCategory-movies", search: "videoSearchButton")
        try attachFrame(named: "Open portrait Library with categories and status-side actions")
        try activate(element("videoSearchButton"))
        try waitForHittable(element("searchField"), timeout: 15, named: "portrait Library search field")
        try activate(element("videoSearchCancelButton"))
        try selectTourLibraryCategory("music", screen: "audioScreen")
        try assertPortraitNavigation(categories: "libraryCategories", firstCategory: "libraryCategory-movies", search: "musicSearchButton")
        try attachFrame(named: "Open portrait Music uses the shared navigation positions")
        try activate(element("musicSearchButton"))
        let musicSearch = application!.searchFields.firstMatch
        try waitForHittable(musicSearch, timeout: 10, named: "portrait Music search")
        XCTAssertGreaterThanOrEqual(musicSearch.frame.minY, element("libraryCategories").frame.maxY + 4)
        try attachFrame(named: "Open portrait Music search below categories")
        try activate(musicSearch)
        musicSearch.typeText("portrait-nav-no-match\n")
        try waitForExistence(application!.staticTexts["No music found"], timeout: 15, named: "portrait Music search results")
        try activate(application!.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close", "close"])).firstMatch)
        XCTAssertTrue(disappears(musicSearch, timeout: 10))
        try selectTourLibraryCategory("books", screen: "booksScreen")
        try attachFrame(named: "Open portrait Books on category entry")
        try assertPortraitNavigation(categories: "libraryCategories", firstCategory: "libraryCategory-movies", search: "booksSearchButton")
        try attachFrame(named: "Open portrait Books uses the shared navigation positions")
        let comicID = try XCTUnwrap(fixture.comicBookID)
        let shelves = application!.scrollViews.containing(.button, identifier: "book-\(comicID)").allElementsBoundByIndex
        let recentBooks = try XCTUnwrap(shelves.min { $0.frame.height < $1.frame.height })
        let lastBook = recentBooks.buttons.matching(identifier: "book-\(comicID)").firstMatch
        try waitForExistence(lastBook, timeout: 15, named: "last cover in portrait Books shelf")
        let initialBookX = lastBook.frame.minX
        recentBooks.swipeLeft()
        XCTAssertLessThan(lastBook.frame.minX, initialBookX - 10, "The final cover must remain reachable by swiping beside the utility rail.")
        XCTAssertLessThanOrEqual(lastBook.frame.maxX, element("booksSearchButton").frame.minX - 8,
                                 "The revealed cover must stay clear of the utility rail.")
        try attachFrame(named: "Open portrait Books reveals the last cover clear of navigation")
        try activate(element("booksSearchButton"))
        let booksSearch = application!.searchFields.firstMatch
        try waitForHittable(booksSearch, timeout: 10, named: "portrait Books search")
        XCTAssertGreaterThanOrEqual(booksSearch.frame.minY, element("libraryCategories").frame.maxY + 4)
        try attachFrame(named: "Open portrait Books search below categories")
        try activate(booksSearch)
        booksSearch.typeText("portrait-nav-no-match\n")
        try waitForExistence(application!.staticTexts["No matching books"], timeout: 15, named: "portrait Books search results")
        try activate(application!.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close", "close"])).firstMatch)
        XCTAssertTrue(disappears(booksSearch, timeout: 10))
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 15, named: "player from portrait Books")
        try stopAudioPlayer()
        completed = true
    }

    private func assertPortraitNavigation(categories categoryID: String, firstCategory: String, search searchID: String) throws {
        let viewport = try applicationViewport()
        let mark = element("homeMasthead")
        let categories = element(categoryID)
        let first = element(firstCategory)
        let search = element(searchID)
        let artwork = element("nowPlayingBar")
        let toggle = element("nowPlayingPauseButton")
        try waitForExistence(categories, timeout: 15, named: "portrait category row")
        for (control, name) in [(mark, "logo"), (first, "first category"), (search, "Search"),
                                (artwork, "Now Playing artwork"), (toggle, "play/pause")] {
            try waitForHittable(control, timeout: 15, named: "portrait navigation \(name)")
        }
        guard mark.frame.width <= 60, mark.frame.minY <= 32, abs(first.frame.midY - mark.frame.midY) <= 6,
              categories.frame.maxX <= search.frame.minX - 8 else {
            throw ProofFailure.ui("Portrait navigation must align mark-only branding and categories in one row clear of the utility rail: mark=\(mark.frame), categories=\(categories.frame).")
        }
        guard search.frame.minX >= viewport.maxX - 80, search.frame.minY >= 72, search.frame.maxY <= 144,
              artwork.frame.minY >= search.frame.maxY + 4,
              toggle.frame.minY >= artwork.frame.maxY + 4,
              abs(artwork.frame.midX - search.frame.midX) <= 4,
              abs(toggle.frame.midX - search.frame.midX) <= 4 else {
            throw ProofFailure.ui("Portrait Search and Now Playing must stack below the system status corner: search=\(search.frame), artwork=\(artwork.frame), toggle=\(toggle.frame).")
        }
    }

    func journeyCategoryArtworkUnderNavigation() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        let display = try applicationViewport()
        guard UIDevice.current.userInterfaceIdiom == .phone,
              display.width >= 600, display.height > display.width else {
            throw XCTSkip("This layout contract targets the open Duo portrait display.")
        }
        try signIn(using: fixture, waitsForAccount: false)
        let library = application!.buttons["Library"].firstMatch
        try waitForHittable(library, timeout: 20, named: "Library tab")
        try activate(library)
        try waitForExistence(element("libraryScreen"), timeout: 25, named: "Movies library")
        let artwork = element("libraryArtworkReadiness")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 25, named: "loaded movie artwork")
        let viewport = try applicationViewport()
        XCTAssertEqual(artwork.frame.maxX, viewport.maxX - 20, accuracy: 2,
                       "Category artwork must extend to the normal page gutter beneath the floating rail.")
        try attachFrame(named: "Category artwork beneath floating navigation")

        let search = element("videoSearchButton")
        try waitForHittable(search, timeout: 15, named: "Library search")
        try activate(search)
        let cancel = element("videoSearchCancelButton")
        try waitForHittable(cancel, timeout: 15, named: "Library search cancel")
        let options = element("videoOptionsMenu")
        XCTAssertLessThanOrEqual(cancel.frame.maxX, options.frame.minX - 8,
                                 "Search controls must remain clear of the floating utility rail.")
        try attachFrame(named: "Category search clear of floating navigation")
        completed = true
    }

    func journeyOpenDuoLibraryHeaderAndSearch() throws {
        defer { XCUIDevice.shared.orientation = .portrait }
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }

        let fixture = try loadFixture()
        try launchAndResetSession()
        if application!.windows.firstMatch.frame.width > application!.windows.firstMatch.frame.height {
            XCUIDevice.shared.orientation = .portrait
        }
        guard waitUntil(timeout: 15, condition: {
            let frame = self.application!.windows.firstMatch.frame
            return frame.height > frame.width
        }) else {
            throw ProofFailure.ui("The Duo navigation proof could not establish portrait orientation.")
        }
        let portrait = try applicationViewport()
        guard UIDevice.current.userInterfaceIdiom == .phone, portrait.width >= 600 else {
            throw XCTSkip("This layout contract targets the open Duo portrait display.")
        }
        try signIn(using: fixture)
        try assertDuoHomeCategoryRail(named: "open Duo portrait Home")
        try attachFrame(named: "Open Duo portrait Home category rail")

        try selectDestination("Library", screen: "libraryScreen")
        try assertDuoLibraryHeaderLayout(named: "open Duo portrait", expectsSearchButton: true)
        try attachFrame(named: "Open Duo portrait Library category header")

        let categories = element("libraryCategories")
        let music = element("libraryCategory-music")
        try revealMenuControl(music, in: categories)
        try waitForHittable(music, timeout: 15, named: "horizontally revealed Music category")
        try activate(music)
        try waitForExistence(element("audioScreen"), timeout: 25, named: "Music category destination")

        let books = element("libraryCategory-books")
        try revealMenuControl(books, in: categories)
        try waitForHittable(books, timeout: 15, named: "horizontally revealed Books category")
        try activate(books)
        try waitForExistence(element("booksScreen"), timeout: 25, named: "Books category destination")

        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        let search = element("videoSearchButton")
        try waitForHittable(search, timeout: 15, named: "Library search action")
        try activate(search)
        try waitForHittable(element("videoSearchCancelButton"), timeout: 15, named: "Library search cancel")
        XCTAssertFalse(hasVisibleFrame(search),
                       "Opening Library search must replace its toolbar search action rather than overlap it.")
        try assertDuoLibraryHeaderLayout(named: "open Duo portrait search", expectsSearchButton: false)
        try attachFrame(named: "Open Duo portrait Library search clear of header")

        XCUIDevice.shared.orientation = .landscapeLeft
        // Device Hub controls the active Duo inner display; XCTest's orientation
        // request can target the inactive display, so leave room for the observed
        // geometry change before asserting the landscape layout.
        guard waitUntil(timeout: 60, condition: {
            let frame = self.application!.windows.firstMatch.frame
            return frame.width > frame.height && frame.width >= 900
        }) else {
            throw ProofFailure.ui("The open Duo navigation proof did not reflow to landscape.")
        }
        try assertDuoLibraryHeaderLayout(named: "open Duo landscape search", expectsSearchButton: false)
        try attachFrame(named: "Open Duo landscape Library search clear of header")
        completed = true
    }

    private func assertDuoLibraryHeaderLayout(named posture: String, expectsSearchButton: Bool) throws {
        let masthead = element("homeMasthead")
        let categories = element("libraryCategories")
        let movies = element("libraryCategory-movies")
        let alphabet = element("videoAlphabetButton")
        let options = element("videoOptionsMenu")
        let search = element("videoSearchButton")
        try waitForHittable(masthead, timeout: 15, named: "Lumina masthead in \(posture)")
        try waitForExistence(categories, timeout: 15, named: "Library categories in \(posture)")
        XCTAssertTrue(hasVisibleFrame(categories), "Library categories must remain visible in \(posture).")
        try waitForHittable(movies, timeout: 15, named: "Movies category in \(posture)")
        try waitForHittable(alphabet, timeout: 15, named: "Library alphabet action in \(posture)")
        try waitForHittable(options, timeout: 15, named: "Library options action in \(posture)")
        if expectsSearchButton {
            try waitForHittable(search, timeout: 15, named: "Library search action in \(posture)")
        } else {
            XCTAssertFalse(hasVisibleFrame(search), "\(posture) must not retain a search action behind the open search field.")
            let searchField = element("searchField")
            try waitForHittable(searchField, timeout: 15, named: "Library search field in \(posture)")
        }

        let frames = XCTAttachment(string: """
        viewport=\(try applicationViewport())
        masthead=\(masthead.frame)
        categories=\(categories.frame)
        movies=\(movies.frame)
        search=\(search.exists ? String(describing: search.frame) : "absent")
        alphabet=\(alphabet.frame)
        options=\(options.frame)
        """)
        frames.name = "Open Duo Library header frames — \(posture)"
        frames.lifetime = .keepAlways
        add(frames)

        let viewport = try applicationViewport()
        if viewport.height > viewport.width {
            XCTAssertEqual(movies.frame.midY, masthead.frame.midY, accuracy: 6,
                           "Portrait categories must share the mark's baseline in \(posture).")
            XCTAssertLessThanOrEqual(categories.frame.maxX, options.frame.minX - 8,
                                     "Portrait categories must clear the status-side utility rail in \(posture).")
            XCTAssertGreaterThanOrEqual(categories.frame.minX, viewport.minX,
                                        "The open Duo portrait category rail must stay inside the leading window edge in \(posture).")
            XCTAssertLessThanOrEqual(categories.frame.maxX, viewport.maxX,
                                     "The open Duo portrait category rail must stay inside the trailing window edge in \(posture).")
            XCTAssertGreaterThanOrEqual(movies.frame.minY, categories.frame.minY,
                                        "Movies must remain in the open Duo portrait category rail in \(posture).")
            XCTAssertGreaterThanOrEqual(movies.frame.minX, viewport.minX + 20,
                                        "Movies must remain inside the leading category-rail gutter in \(posture).")
            if !expectsSearchButton {
                let searchField = element("searchField")
                XCTAssertGreaterThanOrEqual(searchField.frame.minY, categories.frame.maxY + 4,
                                            "The Library search field must begin below the portrait category rail in \(posture).")
            }
        } else {
            let firstTrailingEdge = min(alphabet.frame.minX, options.frame.minX,
                                        expectsSearchButton ? search.frame.minX : CGFloat.greatestFiniteMagnitude)
            XCTAssertGreaterThanOrEqual(categories.frame.minX, masthead.frame.maxX + 8,
                                        "The Library category viewport must start after the Lumina masthead in \(posture).")
            XCTAssertGreaterThanOrEqual(movies.frame.minX, masthead.frame.maxX + 8,
                                        "The first visible Movies category must clear the Lumina masthead in \(posture).")
            XCTAssertLessThanOrEqual(categories.frame.maxX, firstTrailingEdge - 8,
                                     "The Library category viewport must end before navigation actions in \(posture).")
        }
    }

    private func assertDuoHomeCategoryRail(named posture: String) throws {
        let masthead = element("homeMasthead")
        let movies = element("homeBrowse-movies")
        let tv = element("homeBrowse-tv")
        let music = element("homeBrowse-music")
        let books = element("homeBrowse-books")
        let watchlist = element("homeBrowse-watchlist")
        try waitForHittable(masthead, timeout: 15, named: "Lumina masthead in \(posture)")
        try waitForHittable(movies, timeout: 15, named: "Home Movies category in \(posture)")
        [tv, music, books, watchlist].forEach {
            XCTAssertTrue(hasVisibleFrame($0), "Home category controls must remain visible in \(posture).")
        }

        let viewport = try applicationViewport()
        XCTAssertGreaterThan(viewport.height, viewport.width,
                             "The Home category rail contract applies to open Duo portrait only.")
        let controls = [movies, tv, music, books, watchlist]

        let railMinX = controls.map(\.frame.minX).min() ?? 0
        let railMaxX = controls.map(\.frame.maxX).max() ?? 0
        XCTAssertEqual(movies.frame.midY, masthead.frame.midY, accuracy: 6,
                       "Portrait Home categories must share the mark's baseline in \(posture).")
        XCTAssertGreaterThanOrEqual(railMinX, viewport.minX + 20,
                                    "The open Duo portrait Home category rail must stay inside the leading window edge in \(posture).")
        XCTAssertLessThanOrEqual(railMaxX, viewport.maxX - 20,
                                 "The open Duo portrait Home category rail must stay inside the trailing window edge in \(posture).")
    }

    private func assertClosedDuoLibraryHeader() throws {
        let masthead = element("homeMasthead")
        let categories = element("libraryCategories")
        let movies = element("libraryCategory-movies")
        let alphabet = element("videoAlphabetButton")
        let options = element("videoOptionsMenu")
        let search = element("videoSearchButton")
        try waitForHittable(masthead, timeout: 15, named: "Lumina masthead in closed Duo Library")
        try waitForExistence(categories, timeout: 15, named: "Library categories in closed Duo")
        try waitForHittable(movies, timeout: 15, named: "Movies category in closed Duo")
        try waitForHittable(alphabet, timeout: 15, named: "Library alphabet action in closed Duo")
        try waitForHittable(options, timeout: 15, named: "Library options action in closed Duo")
        try waitForHittable(search, timeout: 15, named: "Library search action in closed Duo")

        XCTAssertGreaterThanOrEqual(categories.frame.minX, masthead.frame.maxX + 8,
                                    "Closed Duo Library categories must clear the Lumina masthead.")
        XCTAssertGreaterThanOrEqual(movies.frame.minX, masthead.frame.maxX + 8,
                                    "Closed Duo Library Movies must clear the Lumina masthead.")
        for action in [alphabet, options, search] {
            XCTAssertFalse(categories.frame.intersects(action.frame),
                           "Closed Duo Library categories must not overlap navigation actions.")
        }
        XCTAssertEqual(movies.frame.midY, masthead.frame.midY, accuracy: 6,
                       "Closed Duo Library Movies must share the masthead's toolbar row.")
    }


    private func revealHomeMenuControl(_ category: XCUIElement) {
        revealMenuControl(category, in: element("homeBrowsingRow"))
    }

    private func homeRefreshCount() throws -> Int {
        let diagnostics = try XCTUnwrap(element("homeScreen").value as? String,
                                        "UI-testing Home diagnostics must expose a refresh count.")
        let prefix = "refreshes="
        guard let refreshToken = diagnostics.split(separator: " ").first(where: { $0.hasPrefix(prefix) }),
              let refreshes = Int(refreshToken.dropFirst(prefix.count)) else {
            throw ProofFailure.ui("UI-testing Home diagnostics did not include a numeric refresh count: \(diagnostics)")
        }
        return refreshes
    }

    private func categoryVerticalPull() throws -> Double {
        let diagnostics = try XCTUnwrap(element("homeBrowsingRow").value as? String,
                                        "UI-testing category diagnostics must expose vertical pull distance.")
        let prefix = "verticalPull="
        guard let pullToken = diagnostics.split(separator: " ").first(where: { $0.hasPrefix(prefix) }),
              let pull = Double(pullToken.dropFirst(prefix.count)) else {
            throw ProofFailure.ui("UI-testing category diagnostics did not include a numeric vertical pull: \(diagnostics)")
        }
        return pull
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


    private func assertVideoDetailChrome(screen identifier: String) throws {
        let masthead = element("homeMasthead")
        try waitForExistence(masthead, timeout: 15, named: "Lumina detail masthead")
        XCTAssertEqual(masthead.label, "Lumina")
        let viewport = application!.scrollViews.matching(identifier: identifier).firstMatch
        try waitForExistence(viewport, timeout: 15, named: "Detail viewport")
        XCTAssertLessThanOrEqual(viewport.frame.minY, application!.windows.firstMatch.frame.minY + 2,
                                 "Detail artwork must begin at the top bezel, behind the navigation header.")
    }


    #endif

    #if os(iOS)


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

    #if os(iOS)
    func journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle(checksLayout: Bool = false) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try launchAndResetSession(extraLaunchArguments: checksLayout ? ["--ui-testing-manual-hero-selection"] : [])
        try signIn(using: fixture, waitsForAccount: !checksLayout)
        if !checksLayout {
            try selectDestination("Account", screen: "accountScreen")
            try chooseDeviceSetting("deviceAppearancePicker", option: "Dark")
            try activate(application!.buttons.matching(NSPredicate(format: "label == %@", "Home")).firstMatch)
        }
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home hero")
        if checksLayout {
            XCUIDevice.shared.orientation = .landscapeLeft
            XCTAssertTrue(waitUntil(timeout: 8) { (try? self.applicationWindow().frame.width) ?? 0 > 700 })
        }
        let choice = element("featuredChoice-\(fixture.directItemID)")
        let title = element("featuredTitleButton")
        if !checksLayout || title.label != fixture.directItemTitle {
            try revealInLibraryViewport([choice])
            try activate(choice)
        }
        guard waitUntil(timeout: 5, condition: { title.label == fixture.directItemTitle }) else {
            throw ProofFailure.ui("Home did not select the seeded hero before swiping.")
        }
        let criticRating = element("heroRottenTomatoesRating-\(fixture.directItemID)")
        try waitForExistence(criticRating, timeout: 5, named: "server critic rating")
        XCTAssertEqual(criticRating.label, "Rotten Tomatoes: 91.5 percent, Fresh")
        XCTAssertEqual(element("heroIMDbRating-\(fixture.directItemID)").label, "IMDb: 8.2 out of 10")
        XCTAssertEqual(element("heroMetacriticRating-\(fixture.directItemID)").label, "Metacritic: 73 out of 100, Favorable")
        try attachFrame(named: "Home hero Resume button before artwork swipe")
        if checksLayout {
            let initialPixels = try heroPixels()
            try await Task.sleep(for: .seconds(3))
            let movedPixels = try heroPixels()
            guard title.label == fixture.directItemTitle, initialPixels != movedPixels else {
                throw ProofFailure.ui("Ambient artwork must keep moving while the selected hero and its layout stay fixed.")
            }
            try attachFrame(named: "Home ambient artwork after three seconds")
        }
        let window = try applicationWindow()
        let artwork = application!.descendants(matching: .any).matching(
            NSPredicate(format: "label BEGINSWITH %@", "Featured titles,")
        ).firstMatch
        try waitForExistence(artwork, timeout: 10, named: "Home hero artwork")
        let visibleArt = artwork.frame.intersection(window.frame)
        let y = visibleArt.minY + visibleArt.height * 0.3 - window.frame.minY
        let right = window.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: window.frame.width * 0.75, dy: y))
        let left = window.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: window.frame.width * 0.25, dy: y))
        let initialHeroFrame = artwork.frame
        var initialHeroY = Double(initialHeroFrame.minY)
        var initialHeroHeight = Double(initialHeroFrame.height)
        if checksLayout {
            XCTAssertGreaterThan(initialHeroFrame.width, 700, "Exercise the wide hero layout")
            let diagnostics = try XCTUnwrap(artwork.value as? String)
            let token = try XCTUnwrap(diagnostics.split(separator: " ").first { $0.hasPrefix("frames=") })
                .replacingOccurrences(of: "frames=", with: "")
            let baseline = try XCTUnwrap(token.split(separator: "|").last).split(separator: ",")
            guard baseline.count == 3 else { throw ProofFailure.ui("Hero layout measurements were incomplete.") }
            initialHeroY = try XCTUnwrap(Double(baseline[1]))
            initialHeroHeight = try XCTUnwrap(Double(baseline[2]))
        }
        right.press(forDuration: 0.05, thenDragTo: left)
        guard waitUntil(timeout: 3, condition: { title.label != fixture.directItemTitle && !title.label.isEmpty }),
              !element("detailScreen").exists else {
            throw ProofFailure.ui("Swiping left on Home artwork must select the next hero without opening details.")
        }
        try attachFrame(named: "Home artwork swipe selects the next hero")
        XCTAssertTrue(disappears(criticRating, timeout: 5), "A different hero must not retain the previous title’s critic score.")
        if checksLayout {
            let diagnostics = try XCTUnwrap(artwork.value as? String)
            let transition = try XCTUnwrap(diagnostics.split(separator: " ").first { $0.hasPrefix("transition=") })
                .replacingOccurrences(of: "transition=", with: "")
            let frames = try XCTUnwrap(diagnostics.split(separator: " ").first { $0.hasPrefix("frames=") })
                .replacingOccurrences(of: "frames=", with: "").split(separator: "|")
            for frame in frames {
                let values = frame.split(separator: ",")
                guard values.count == 3, values[0] == transition else { continue }
                XCTAssertEqual(try XCTUnwrap(Double(values[1])), initialHeroY, accuracy: 1,
                               "The hero and following shelves must not jump vertically during the swipe")
                XCTAssertEqual(try XCTUnwrap(Double(values[2])), initialHeroHeight, accuracy: 1,
                               "The hero must retain its height throughout the swipe, including artwork loading")
            }
        }
        left.press(forDuration: 0.05, thenDragTo: right)
        guard waitUntil(timeout: 3, condition: { title.label == fixture.directItemTitle }) else {
            throw ProofFailure.ui("Swiping right on Home artwork must return to the previous hero.")
        }
        try waitForExistence(criticRating, timeout: 5, named: "restored critic rating")
        if checksLayout {
            try attachFrame(named: "Home hero after swiping in both directions")
            XCUIDevice.shared.orientation = .portrait
            XCTAssertTrue(waitUntil(timeout: 8) { artwork.frame.width < artwork.frame.height })
            try attachFrame(named: "Portrait hero with critic rating")
            completed = true
            return
        }
        let time = try startPlayback(using: "featuredPlayButton")
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= 14 }
        try attachFrame(named: "Play starts the title selected by artwork swipes")
        try closePlayer()
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.directItemID, minimum: 14)
        completed = true
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
        if application!.windows.firstMatch.frame.width < 700 {
            let compactHeroWindow = application!.windows.firstMatch.frame
            let compactHeroCandidates = application!.buttons.matching(identifier: "featuredPlayButton")
            XCTAssertTrue(waitUntil(timeout: 10) {
                compactHeroCandidates.allElementsBoundByIndex.filter {
                    self.hasVisibleFrame($0) && $0.isHittable
                }.count == 1
            }, "Closed Duo Home must settle one visible hero action before its alignment contract.")
            let compactHeroPlay = try XCTUnwrap(compactHeroCandidates.allElementsBoundByIndex.first {
                hasVisibleFrame($0) && $0.isHittable
            }, "Closed Duo Home did not retain a settled hero action after the readiness wait.")
            let compactHeroPlayFrame = compactHeroPlay.frame
            let compactHeroPlayLabel = compactHeroPlay.label
            let compactHeroPlayFrames = compactHeroCandidates.allElementsBoundByIndex.map { "\($0.label) \($0.frame)" }
            let featuredChoices = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "featuredChoice-")
            )
            let selectedFeaturedID = featuredChoices.allElementsBoundByIndex.first {
                ($0.value as? String) == "Selected"
            }?.identifier ?? "none"
            let featuredPage = application!.descendants(matching: .any).matching(
                NSPredicate(format: "label BEGINSWITH %@", "Featured titles,")
            ).firstMatch.label
            print("DUO_HERO_PRE_ASSERT selected=\(selectedFeaturedID) page=\(featuredPage) candidate=\(compactHeroPlayLabel) \(compactHeroPlayFrame) matches=\(compactHeroPlayFrames)")
            let compactHeroFrame = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
            compactHeroFrame.name = "Closed Duo Home hero before Play alignment assertion"
            compactHeroFrame.lifetime = .keepAlways
            add(compactHeroFrame)
            let compactHeroHierarchy = XCTAttachment(string: application!.debugDescription)
            compactHeroHierarchy.name = "Closed Duo Home hero accessibility hierarchy before Play alignment assertion"
            compactHeroHierarchy.lifetime = .keepAlways
            add(compactHeroHierarchy)
            XCTAssertLessThan(compactHeroPlayFrame.minX, compactHeroWindow.width * 0.2)
        } else {
            XCTAssertLessThan(play.frame.minX, application!.windows.firstMatch.frame.width * 0.2)
        }
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

    #if os(iOS)
    func journeyNativeHomeLayoutReordersHidesAndPersists() async throws {
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
    }
    #endif

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
            if !category.isEmpty {
                try selectTourLibraryCategory(category, screen: screen)
            } else {
                try selectDestination(destination, screen: screen)
            }
            try waitForHittable(element(screen), timeout: 20, named: "Browse surface before Search")
            XCTAssertFalse(application!.searchFields.firstMatch.exists, "\(screen) must open without a search drawer.")
            let usesIPadCategories = element("iPadUnifiedCategories").exists
            try activate(element(searchButton))
            let search = application!.searchFields.firstMatch
            let cancel = usesIPadCategories
                ? element("nativeSearchDismissButton")
                : application!.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close"])).firstMatch
            try waitForHittable(search, timeout: 10, named: "Search opened from navigation")
            if usesIPadCategories {
                try waitForHittable(cancel, timeout: 10, named: "iPad Search Cancel")
                XCTAssertFalse(cancel.frame.isEmpty, "iPad Search Cancel must have a visible frame.")
            }
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

    func journeyMobileWelcome() async throws {
        let fixture = try loadFixture()
        try launchAndResetSession()
        let qrLanding = element("qrPairingLanding")
        try waitForExistence(qrLanding, timeout: 20, named: "QR pairing landing")
        let cameraStatus = element("connectionCameraStatus")
        try waitForExistence(cameraStatus, timeout: 10, named: "Camera availability")
        XCTAssertEqual(cameraStatus.label, "Camera unavailable")
        let manualAction = element("advancedConnectionButton")
        XCTAssertEqual(manualAction.label, "Sign in manually")
        XCTAssertTrue(qrLanding.label.contains("Mobile connect"),
                      "QR setup must identify the web action needed to obtain a code.")
        XCTAssertFalse(element("serverURLField").exists,
                       "Manual credentials must stay hidden until manual sign-in is explicitly chosen.")
        XCTAssertFalse(element("usernameField").exists,
                       "QR pairing must remain the primary mobile connection path.")
        XCTAssertFalse(element("passwordField").exists,
                       "QR pairing must not pre-expose a password field.")
        try waitForExistence(element("scanPairingCodeButton"), timeout: 10, named: "QR scan action")
        try attachFrame(named: "Compact mobile connection actions")
        try revealManualConnection()
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

    #endif

    func journeyNativeRequestsDiscoverSelectAndSubmitThroughTheServer() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard let movieKey = fixture.requestMovieKey, let movieTitle = fixture.requestMovieTitle,
              let unavailableShowKey = fixture.requestUnavailableShowKey,
              let unavailableShowTitle = fixture.requestUnavailableShowTitle,
              let freshAnimeKey = fixture.requestFreshAnimeKey,
              let freshAnimeTitle = fixture.requestFreshAnimeTitle else {
            throw XCTSkip("Generate the Requests catalog proof fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = try await api.requestRecords(identity: identity)
        let showBefore = before.filter { $0.key == unavailableShowKey }.map(\.id)
        let freshAnimeBefore = Set(before.filter { $0.key == freshAnimeKey }.map(\.id))
        XCTAssertTrue(freshAnimeBefore.isEmpty,
                      "The fresh anime fixture must begin without an existing request to prove this submission.")

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Requests", screen: "requestsScreen")
        let requests = element("requestsScreen")
        try waitForHittable(requests, timeout: 30, named: "Requests discovery catalog")
        #if os(tvOS)
        let movie = element("requestFeatured-\(movieKey)")
        #else
        let movie = element("requestDetails-\(movieKey)")
        #endif
        try waitForHittable(movie, timeout: 30, named: "Requests hero title")
        #if os(tvOS)
        XCTAssertTrue(movie.label.contains(movieTitle), "The Requests hero did not preserve its catalog title.")
        let search = element("requestsSearchField")
        try enter(unavailableShowTitle, into: search)
        #else
        XCTAssertEqual(movie.label, movieTitle, "The Requests hero did not preserve its catalog title.")
        try attachFrame(named: "Requests featured hero")
        try activate(element("requestsSearchButton"))
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "Requests search")
        try activate(search)
        search.typeText(unavailableShowTitle)
        search.typeText("\n")
        #endif
        let show = try revealRequestPoster(unavailableShowKey, named: "Requests show poster")
        #if os(tvOS)
        XCTAssertTrue(show.label.contains(unavailableShowTitle),
                      "Requests browse did not preserve the show poster title.")
        #else
        XCTAssertEqual(show.label, unavailableShowTitle, "Requests browse did not preserve the show poster title.")
        #endif
        try attachFrame(named: "Requests search with an unlinked series")
        try activate(show)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "show request details")
        try waitForHittable(element("requestSeasonPicker"), timeout: 10, named: "show season selector")
        XCTAssertTrue(element("requestConfirmButton").exists,
                      "An unlinked Requests title must retain its request confirmation control.")
        XCTAssertFalse(element("requestViewVaultButton").exists,
                       "An unlinked Requests title must retain Request controls, not expose View.")
        try closeRequestDetailToRequests()
        guard disappears(element("requestDetailScreen"), timeout: 10) else {
            throw ProofFailure.ui("Cancelling a Requests detail did not dismiss it.")
        }
        let afterCancel = try await api.requestRecords(identity: identity)
        XCTAssertEqual(afterCancel.filter { $0.key == unavailableShowKey }.map(\.id), showBefore,
                       "Cancelling a Requests detail submitted a server request.")

        #if os(tvOS)
        try replaceText(search, with: freshAnimeTitle)
        XCUIRemote.shared.press(.menu)
        #else
        // Closing the request detail dismisses the system search drawer, so
        // its original field reference is no longer a live user control.
        try activate(element("requestsSearchButton"))
        let freshSearch = application!.searchFields.firstMatch
        try waitForHittable(freshSearch, timeout: 10, named: "fresh Requests search")
        try replaceText(freshSearch, with: freshAnimeTitle)
        freshSearch.typeText("\n")
        #endif
        let anime = try revealRequestPoster(freshAnimeKey, named: "searched fresh anime poster")
        try activate(anime)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "anime request details")
        #if os(tvOS)
        let chooseSeasons = element("requestSeasonPicker").buttons
            .matching(NSPredicate(format: "label == %@", "Choose seasons"))
            .firstMatch
        try waitForExistence(chooseSeasons, timeout: 20, named: "anime season choice")
        try activate(chooseSeasons)
        #else
        try waitForHittable(element("requestSeasonPicker"), timeout: 20, named: "anime season selector")
        try activate(element("requestSeasonPicker"))
        try activate(menuAction("Choose seasons"))
        #endif
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
        #if os(tvOS)
        let englishDub = element("requestLanguagePicker").buttons
            .matching(NSPredicate(format: "label == %@", "English dub"))
            .firstMatch
        try waitForExistence(englishDub, timeout: 10, named: "anime English dub choice")
        try activate(englishDub)
        XCTAssertTrue(waitUntil(timeout: 10) { englishDub.isSelected },
                      "Selecting English dub must visibly select that language before the request is submitted.")
        #else
        try waitForHittable(element("requestLanguagePicker"), timeout: 10, named: "anime audio language selector")
        try activate(element("requestLanguagePicker"))
        try activate(menuAction("English dub"))
        #endif
        try revealDetailElement(
            element("requestConfirmButton"),
            in: element("requestDetailScreen"),
            named: "request confirmation",
            requiresHittable: true
        )
        try waitForHittable(element("requestConfirmButton"), timeout: 10, named: "request confirmation")
        try activate(element("requestConfirmButton"))
        let submitted = try await waitForNativeRequest(api, identity: identity, key: freshAnimeKey)
        XCTAssertFalse(freshAnimeBefore.contains(submitted.id),
                       "The fresh anime submission must create a new request rather than reuse a fixture record.")
        XCTAssertEqual(submitted.language, "dub", "The selected request language did not reach the server.")
        XCTAssertTrue(submitted.seasonNumbers.contains(1), "The selected season did not reach the server.")
        try waitForExistence(element("requestSubmittedStatus"), timeout: 15, named: "submitted request status")
        try closeRequestDetailToRequests()
        guard disappears(element("requestDetailScreen"), timeout: 10) else {
            throw ProofFailure.ui("Closing submitted request details did not return to Requests.")
        }

        #if os(iOS)
        try activate(element("requestsOptionsMenu"))
        #endif
        try activate(element("requestsMyListButton"))
        try waitForExistence(element("requestHistoryScreen"), timeout: 20, named: "My Requests history")
        let history = element("requestHistory-\(freshAnimeKey)")
        try waitForExistence(history, timeout: 20, named: "submitted request in My Requests")
        XCTAssertTrue(hasVisibleFrame(history), "The submitted request was outside the visible history.")
        XCTAssertTrue(history.staticTexts[freshAnimeTitle].exists || history.label.contains(freshAnimeTitle),
                      "My Requests did not show the submitted catalog title.")
        try attachFrame(named: "Native Requests browse, request controls, and submitted history")
        completed = true
    }

    func journeyNativeRequestsVaultView() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard let movieKey = fixture.requestMovieKey,
              let showKey = fixture.requestShowKey,
              let showTitle = fixture.requestShowTitle,
              let animeKey = fixture.requestAnimeKey,
              let animeTitle = fixture.requestAnimeTitle,
              let unavailableShowKey = fixture.requestUnavailableShowKey,
              let unavailableShowTitle = fixture.requestUnavailableShowTitle else {
            throw XCTSkip("Generate the Requests vault-link proof fixture.")
        }

        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Requests", screen: "requestsScreen")
        try waitForHittable(element("requestsScreen"), timeout: 30, named: "Requests discovery catalog")

        #if os(tvOS)
        let movie = element("requestFeatured-\(movieKey)")
        try waitForHittable(movie, timeout: 20, named: "available movie Requests feature")
        #else
        let movie = element("requestDetails-\(movieKey)")
        try waitForHittable(movie, timeout: 20, named: "available movie Requests detail")
        #endif
        try activate(movie)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "available movie request details")
        assertRequestUsesVaultOnly(named: "available movie")
        try openRequestVault(expectedScreen: "detailScreen", named: "available movie")
        XCTAssertFalse(element("videoCollectionScreen").exists,
                       "Viewing an available movie from Requests must open its movie detail, not a collection.")
        try returnToRequestDetailFromVault()
        try closeRequestDetailToRequests()

        #if os(tvOS)
        let search = element("requestsSearchField")
        try enter(showTitle, into: search)
        #endif
        let show = try revealRequestPoster(showKey, named: "available show")
        try activate(show)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "available show request details")
        assertRequestUsesVaultOnly(named: "available show")
        try openRequestVault(expectedScreen: "videoCollectionScreen", named: "available show")
        XCTAssertFalse(element("detailScreen").exists,
                       "Viewing an available show from Requests must open its series collection, not a movie detail.")
        try returnToRequestDetailFromVault()
        try closeRequestDetailToRequests()

        #if os(tvOS)
        try replaceText(search, with: animeTitle)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(element("requestsSearchButton"))
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "Requests search")
        try activate(search)
        search.typeText(animeTitle)
        #endif
        let anime = element("requestPoster-\(animeKey)")
        try waitForHittable(anime, timeout: 20, named: "partly available anime")
        try activate(anime)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "partly available anime request details")
        #if os(tvOS)
        try waitForExistence(element("requestLanguagePicker"), timeout: 10,
                              named: "partly available anime audio language selector")
        let englishDub = element("requestLanguagePicker").buttons
            .matching(NSPredicate(format: "label == %@", "English dub"))
            .firstMatch
        try waitForExistence(englishDub, timeout: 10,
                              named: "partly available anime English dub choice")
        try activate(englishDub)
        #else
        try revealDetailElement(element("requestLanguagePicker"), in: element("requestDetailScreen"),
                                named: "partly available anime audio language selector", requiresHittable: true)
        try activate(element("requestLanguagePicker"))
        try activate(menuAction("English dub"))
        #endif
        #if os(tvOS)
        try waitForExistence(element("requestConfirmButton"), timeout: 10,
                              named: "enabled partly available anime Request-more action")
        #else
        try revealDetailElement(element("requestConfirmButton"), in: element("requestDetailScreen"),
                                named: "enabled partly available anime Request-more action", requiresHittable: true)
        #endif
        XCTAssertTrue(element("requestViewVaultButton").exists &&
                      waitUntil(timeout: 10, condition: { self.element("requestConfirmButton").isEnabled }),
                      "A partly available anime series must retain Request-more alongside View.")
        try openRequestVault(expectedScreen: "videoCollectionScreen", named: "partly available anime")
        try attachFrame(named: "Requests View opens the matching vault series")
        try returnToRequestDetailFromVault()
        try closeRequestDetailToRequests()

        #if os(tvOS)
        try replaceText(search, with: unavailableShowTitle)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(element("requestsSearchButton"))
        let unlinkedSearch = application!.searchFields.firstMatch
        try waitForHittable(unlinkedSearch, timeout: 10, named: "Requests search for unlinked title")
        try activate(unlinkedSearch)
        unlinkedSearch.typeText(unavailableShowTitle)
        unlinkedSearch.typeText("\n")
        #endif
        let unlinkedShow = try revealRequestPoster(unavailableShowKey, named: "unlinked Requests series")
        try activate(unlinkedShow)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "unlinked series request details")
        #if os(tvOS)
        try waitForExistence(element("requestSeasonPicker"), timeout: 10, named: "unlinked series Request controls")
        #else
        try waitForHittable(element("requestSeasonPicker"), timeout: 10, named: "unlinked series Request controls")
        #endif
        XCTAssertFalse(element("requestViewVaultButton").exists,
                       "An unlinked title must retain Request controls without View.")
        try closeRequestDetailToRequests()
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
        #if os(tvOS)
        try selectTVFeaturedTitle(named: fixture.directItemTitle)
        #else
        let choice = element("featuredChoice-\(fixture.directItemID)")
        try revealInLibraryViewport([choice])
        try activate(choice)
        #endif
        guard waitUntil(timeout: 5, condition: { self.featuredTitleElement.label == fixture.directItemTitle }) else {
            throw ProofFailure.ui("The selected Home feature did not update before opening Details.")
        }
        let details = element("featuredTitleButton")
        #if os(iOS)
        try revealInLibraryViewport([details])
        #endif
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


    /// Exercises the currently shipped destinations in one app process so a
    /// sampler can correlate settled windows and repeated navigation without
    /// treating XCTest polling or screenshots as product work.
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
            try waitForExistence(element("serverURLField"), timeout: 20, named: "connection form")
            #else
            try waitForExistence(element("qrPairingLanding"), timeout: 20, named: "QR pairing landing")
            #endif
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
                element("videoPlayPauseButton"), timeout: 10, named: "paused MKV video transport"
            )
            #else
            try tapVideoTransportAfterChromeHides(named: "performance MKV pause control")
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
            let search = element("requestsSearchField")
            let readySearch = try waitForPerformanceControl(search, timeout: 20, named: "Requests search")
            try enter(movieTitle, into: readySearch)
            let searchResult = application!.buttons.matching(
                NSPredicate(format: "identifier BEGINSWITH %@", "requestPoster-")
            ).firstMatch
            _ = try waitForPerformanceControl(searchResult, timeout: 30, named: "TV Requests search result")
            try await performanceQuietPhase("requests_search_idle")
            try replaceText(readySearch, with: "")
            // replaceText opens the TV keyboard but intentionally remains
            // cross-platform; dismiss it here before remote focus returns to
            // the featured Requests action.
            XCUIRemote.shared.press(.menu)
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
    ]

    private func performanceQuietPhase(_ phase: String) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", started: started)
        try await Task.sleep(for: .seconds(2))
        emitPerformanceMarker(phase: phase, event: "ready", started: started)
        // Keep the final app process alive long enough for an app-scoped leak
        // scan and delayed decoder/session teardown measurement.
        try await Task.sleep(for: .seconds(8))
        emitPerformanceMarker(phase: phase, event: "end", started: started)
    }

    private func performancePhase(_ phase: String,
                                  operation: @MainActor () async throws -> Void) async throws {
        let started = ContinuousClock.now
        emitPerformanceMarker(phase: phase, event: "begin", started: started)
        try await operation()
        emitPerformanceMarker(phase: phase, event: "ready", started: started)
        emitPerformanceMarker(phase: phase, event: "end", started: started)
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

    private func emitPerformanceMarker(phase: String, event: String, started: ContinuousClock.Instant) {
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
        let aboutHeading = element("episodeAboutHeading")
        try waitForExistence(aboutHeading, timeout: 10, named: "episode About heading")
        let episodesHeading = application!.staticTexts["Episodes"].firstMatch
        for text in [episodesHeading, selector, aboutHeading] {
            XCTAssertEqual(savedEpisode.frame.minX, text.frame.minX, accuracy: 2,
                           "The artwork card's outer edge must align with text above and below it.")
        }
        try attachFrame(named: "Episode thumbnail aligned with About and no duplicate title or Resume block")
        savedEpisode.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("playbackScreen"), timeout: 30, named: "thumbnail resumes saved episode")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 25) { $0 >= 8 }
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.secondEpisodeID, minimum: 8)
        try closePlayer()
        try revealDetailElement(selector, in: collection, named: "season controls after playback", requiresHittable: false)
        selector.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try activate(menuAction("Season 2"))
        let thumbnail = element("videoEpisode-\(otherEpisode)")
        try revealDetailElement(thumbnail, in: collection, named: "other season episode thumbnail", requiresHittable: false)
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

    private func selectCollectionSeason(_ seasonID: String, named seasonName: String = "Season 1") throws {
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

        let legacyBack = application!.buttons.matching(identifier: "BackButton").firstMatch
        XCTAssertFalse(hasVisibleFrame(legacyBack),
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

    private func protectedStatusBarBottom() throws -> CGFloat {
        let statusBar = application!.statusBars.firstMatch
        return statusBar.exists && !statusBar.frame.isEmpty
            ? statusBar.frame.maxY
            : try applicationViewport().minY
    }

    private var usesStandardPhonePushedChrome: Bool {
        UIDevice.current.userInterfaceIdiom == .phone
            && min(UIScreen.main.bounds.width, UIScreen.main.bounds.height) < 450
    }

    private func standardPhoneNavigationViewport() throws -> CGRect {
        let window = try applicationViewport()
        let header = element("standardPhoneNavigationHeader")
        try waitForExistence(header, timeout: 10, named: "standard-phone navigation header")
        guard hasVisibleFrame(header) else {
            throw ProofFailure.ui("The standard-phone navigation header has no usable frame.")
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

    private func assertStandardPhonePushedChrome(_ screen: XCUIElement, named name: String) throws {
        try waitForExistence(screen, timeout: 20, named: name)
        let legacyBack = application!.buttons.matching(identifier: "BackButton").firstMatch
        XCTAssertFalse(hasVisibleFrame(legacyBack),
                       "\(name) must use native leading-edge Back instead of a visible custom Back button.")
        XCTAssertFalse(hasVisibleFrame(element("homeMasthead")),
                       "\(name) must not layer the Home masthead over pushed content.")
    }

    private func performStandardPhoneEdgeGesture(named name: String) throws {
        let edgeStart = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.01, dy: 0.24)
        )
        let edgeEnd = application!.windows.firstMatch.coordinate(
            withNormalizedOffset: CGVector(dx: 0.62, dy: 0.24)
        )
        edgeStart.press(forDuration: 0.05, thenDragTo: edgeEnd)
        _ = try applicationViewport()
    }

    private func performStandardPhoneLeadingEdgeBack(
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
        try activate(element("homeBrowse-tv"))
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

    func journeyNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes() async throws {
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
            XCUIRemote.shared.press(.playPause)
            let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 10,
                                                 named: "paused autoplay transport")
            XCTAssertEqual(transport.label, "Play", "Autoplay proof must seek while the current episode is paused.")
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
            #endif
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
        }

        let automaticallyStarted = [
            (title: fixture.secondEpisodeTitle, id: fixture.secondEpisodeID),
            (title: thirdEpisodeTitle, id: thirdEpisodeID),
        ]
        for (index, next) in automaticallyStarted.enumerated() {
            try finishCurrentEpisode(creditsStartSeconds: index == 0 ? fixture.episodeCreditsStartSeconds : 10)
            let title = element("videoPlaybackChrome").staticTexts
                .matching(NSPredicate(format: "label CONTAINS %@", next.title)).firstMatch
            guard waitUntil(timeout: 30, condition: { self.hasVisibleFrame(title) }) else {
                throw ProofFailure.ui("Completing episode \(index + 1) did not reveal the next episode title.")
            }
            _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= 3 }
            _ = try await waitForServerProgress(api, identity: identity, itemID: next.id, minimum: 1)
        }

        try finishCurrentEpisode(creditsStartSeconds: 10)
        let stillWatching = application!.alerts["Still watching?"]
        try waitForExistence(stillWatching, timeout: 20, named: "Still watching presence confirmation")
        let fourthTitle = element("videoPlaybackChrome").staticTexts
            .matching(NSPredicate(format: "label CONTAINS %@", fourthEpisodeTitle)).firstMatch
        XCTAssertFalse(hasVisibleFrame(fourthTitle),
                       "The fourth episode must not start until presence is confirmed.")
        let fourthProgress = try await api.playbackSeconds(identity: identity, itemID: fourthEpisodeID)
        XCTAssertEqual(fourthProgress, 0, accuracy: 0.1, "An unconfirmed fourth episode must not report playback.")
        try attachFrame(named: "Still watching after three episodes")
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
            XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 10),
                          "Playing video must hide its transport controls after inactivity.")
            XCTAssertFalse(element("closePlayerButton").exists, "Close must hide with the transport controls.")
            try attachPlaybackFrame(named: "Unobstructed video after controls auto-hide")
            #if os(tvOS)
            XCUIRemote.shared.press(.down)
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
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused native MKV transport")
        #else
        try tapVideoTransportAfterChromeHides(named: "native MKV pause control")
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

        // This fixture authors a cue from 2–7 seconds. Selection state alone
        // is not evidence that AVFoundation delivered or displayed it.
        let cueTime: Double
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        // The settings sequence is longer than this generated cue. Rewind via
        // the explicit public transport while paused, then enter the authored
        // window. A bare Left is focus navigation on this playback surface;
        // it does not invoke the visible Back 10 seconds control.
        try activate(element("videoSeekBackwardButton"))
        _ = try waitForPlaybackValue(time, timeout: 10) { $0 <= 1 }
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
        // Menu hides the transport chrome on this native playback surface;
        // it must not dismiss the player or resume playback. Capture the cue
        // unobscured rather than OCRing it beneath the Back/Play controls.
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(disappears(element("videoPlayPauseButton"), timeout: 5),
                      "Hiding tvOS transport chrome must leave the paused subtitle cue visible.")
        XCTAssertFalse(waitUntil(timeout: 2) {
            (Double(time.value as? String ?? "") ?? pausedCueTime) > pausedCueTime + 0.5
        }, "Hiding tvOS transport chrome must not resume the paused subtitle proof.")
        let subtitleScreenshot = try attachPlaybackFrame(named: "Synthetic subtitle cue while paused")
        try assertSyntheticSubtitleVisible(in: subtitleScreenshot)
        XCUIRemote.shared.press(.down)
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
        try waitForExistence(element("videoPlayPauseButton"), timeout: 8, named: "paused chapter fixture transport")
        #else
        try tapVideoTransportAfterChromeHides(named: "chapter fixture pause control")
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

    private func openRequestVault(expectedScreen: String, named name: String) throws {
        let detail = element("requestDetailScreen")
        let view = element("requestViewVaultButton")
        try revealDetailElement(view, in: detail, named: "\(name) View action", requiresHittable: true)
        try activate(view)
        try waitForExistence(element(expectedScreen), timeout: 25, named: "\(name) vault destination")
        XCTAssertFalse(element("requestVaultLookupError").exists,
                       "Viewing \(name) from Requests could not resolve its authenticated vault title.")
    }

    private func assertRequestUsesVaultOnly(named name: String) {
        XCTAssertTrue(element("requestViewVaultButton").exists, "\(name) must expose View.")
        XCTAssertFalse(element("requestConfirmButton").exists || element("requestSeasonPicker").exists || element("requestLanguagePicker").exists,
                       "\(name) must expose View only, without duplicate Request controls.")
    }

    private func returnToRequestDetailFromVault() throws {
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

    private func closeRequestDetailToRequests() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(element("requestDetailsCloseButton"))
        #endif
        try waitForHittable(element("requestsScreen"), timeout: 20, named: "Requests after detail Back")
    }

    private func revealRequestPoster(_ key: String, named name: String) throws -> XCUIElement {
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

    private func attachPlaybackFrame(named name: String) throws -> XCUIScreenshot {
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
        let window = visibleApplicationWindow ?? application!.windows.firstMatch
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

    private func launchAndResetSession(extraLaunchArguments: [String] = []) throws {
        let app = XCUIApplication()
        application = app
        app.launchArguments.append("--ui-testing")
        app.launchArguments.append(contentsOf: extraLaunchArguments)
        app.launchEnvironment["LUMINA_UI_TEST_NAMESPACE"] = isolationID
        app.launch()

        let loginField = element("serverURLField")
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
    private func revealManualConnection() throws {
        guard !hasVisibleFrame(element("serverURLField")) else { return }
        try waitForExistence(element("qrPairingLanding"), timeout: 20, named: "QR pairing before Advanced")
        let advanced = element("advancedConnectionButton")
        try waitForHittable(advanced, timeout: 20, named: "Advanced manual connection")
        XCTAssertFalse(element("passwordField").exists, "Manual credentials stay hidden while scanning.")
        try activate(advanced)
    }
    #endif

    private func signIn(using fixture: ProofFixture, waitsForAccount: Bool = true) throws {
        #if os(iOS)
        try revealManualConnection()
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

    #if os(iOS)
    private func waitForPostLoginControls(waitsForAccount: Bool) throws {
        let prompt = application!.sheets["Save Password?"]
        let promptTitle = application!.staticTexts["Save Password?"]
        // The OS-owned password sheet can expose its decline action under a
        // non-Button AX trait. Its public label remains the stable contract.
        let decline = application!.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", "Not Now"))
            .firstMatch

        // Saving a password is an OS-owned interruption. A direct tap can wait
        // for the sheet's dismissal before returning, so it must not share the
        // same predicate deadline as the destination assertion.
        guard waitUntil(timeout: 25, condition: {
            if prompt.exists || promptTitle.exists { return true }
            let control = waitsForAccount ? self.destinationControl("Account") : self.element("homeScreen")
            return self.hasVisibleFrame(control) && control.isEnabled && (!waitsForAccount || control.isHittable)
        }) else {
            throw ProofFailure.timeout("Timed out waiting for password storage or post-login navigation.")
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
            let control = waitsForAccount ? self.destinationControl("Account") : self.element("homeScreen")
            return self.hasVisibleFrame(control) && control.isEnabled && (!waitsForAccount || control.isHittable)
        }) else {
            throw ProofFailure.timeout("Timed out waiting for post-login navigation after declining password storage.")
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
        if hasVisibleFrame(element("iPadUnifiedCategories")) {
            let iPadCategory = switch requestedTitle {
            case "Home": "iPadCategory-all"
            case "Account": "iPadCategory-account"
            case "Library", "Video": "iPadCategory-movies"
            default: ""
            }
            if !iPadCategory.isEmpty {
                let control = element(iPadCategory)
                if hasVisibleFrame(control), control.isHittable { return control }
            }
        }
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
            if hasVisibleFrame(element(screen)) { return }
            try activate(element("homeMasthead"))
            guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
                throw ProofFailure.timeout("Timed out waiting for the visible Home destination.")
            }
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
        case "Requests":
            if element("iPadUnifiedCategories").exists {
                let account = element("iPadCategory-account")
                try waitForExistence(account, timeout: 15, named: "iPad Account before Requests")
                revealMenuControl(account, in: element("iPadUnifiedCategories"))
                try activate(account)
                try waitForExistence(element("accountScreen"), timeout: 15, named: "iPad Account before Requests")
                let requests = element("accountRequestsButton")
                try waitForExistence(requests, timeout: 15, named: "iPad Requests action")
                try revealInLibraryViewport([requests], in: element("accountScreen"))
                try activate(requests)
                guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
                    throw ProofFailure.timeout("Timed out waiting for the visible Requests destination from iPad Account.")
                }
                return
            }
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
        if usesStandardPhonePushedChrome {
            // Standard phones intentionally have no persistent Library category
            // rail. Home's Browse menu is the public way to both choose a
            // category and reset the retained Library stack for a fresh tour.
            try selectDestination("Home", screen: "homeScreen")
            let browse = element("compactBrowseMenu")
            try waitForHittable(browse, timeout: 15, named: "Home Browse for \(category)")
            try activate(browse)
            try activate(element("compactCategory-\(category)"))
            guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
                throw ProofFailure.timeout("Home Browse \(category) did not open \(screen).")
            }
            return
        }
        if try selectUnifiedIPadLibraryCategoryIfVisible(category, screen: screen) { return }
        try unwindTourLibraryNavigationStack()
        if try selectUnifiedIPadLibraryCategoryIfVisible(category, screen: screen) { return }
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
        if usesStandardPhonePushedChrome {
            // This is tour setup, not an assertion about the Back gesture. A
            // Home-origin Browse choice is the real standard-phone root reset.
            try selectDestination("Home", screen: "homeScreen")
            let browse = element("compactBrowseMenu")
            try waitForHittable(browse, timeout: 15, named: "Home Browse for Library reset")
            try activate(browse)
            try activate(element("compactCategory-movies"))
            try waitForExistence(element("libraryScreen"), timeout: 25, named: "Movies library root after Home Browse")
            return
        }
        let categories = element("libraryCategories")
        let unifiedCategories = element("iPadUnifiedCategories")
        if !hasVisibleFrame(categories) {
            // Books is a pushed Library destination on iPad. Its catalog
            // intentionally has a public Back control but no category rail or
            // standalone Library destination, so unwind that real stack first.
            let back = application!.buttons.matching(identifier: "BackButton").firstMatch
            if back.exists {
                try activate(back)
            } else {
                try activate(destinationControl("Library"))
            }
        }
        for _ in 0..<4 {
            if hasVisibleFrame(categories) || hasVisibleFrame(unifiedCategories) { return }
            if usesStandardPhonePushedChrome {
                try performStandardPhoneEdgeGesture(named: "Library navigation unwind")
            } else {
                let back = application!.buttons.matching(identifier: "BackButton").firstMatch
                guard back.exists else { break }
                try activate(back)
            }
        }
        guard waitUntil(timeout: 15, condition: {
            self.hasVisibleFrame(categories) || self.hasVisibleFrame(unifiedCategories)
        }) else {
            throw ProofFailure.ui("The Library navigation stack did not return to its category root.")
        }
    }

    private func selectUnifiedIPadLibraryCategoryIfVisible(_ category: String, screen: String) throws -> Bool {
        let categories = element("iPadUnifiedCategories")
        guard hasVisibleFrame(categories) else { return false }
        let control = element("iPadCategory-\(category)")
        try waitForExistence(control, timeout: 15, named: "unified iPad \(category) category")
        revealMenuControl(control, in: categories)
        let visible = control.frame.intersection(categories.frame).intersection(try applicationViewport())
        guard visible.width >= min(44, control.frame.width), visible.height >= min(44, control.frame.height) else {
            throw ProofFailure.ui("The iPad \(category) category is outside the visible rail.")
        }
        // Native floating category buttons can have a visible frame while AX reports them unhittable.
        application!.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: visible.midX, dy: visible.midY)).tap()
        for _ in 0..<4 {
            if hasVisibleFrame(element(screen)) { return true }
            let back = application!.buttons.matching(identifier: "BackButton").firstMatch
            guard back.exists else { break }
            try activate(back)
        }
        try waitForExistence(element(screen), timeout: 25, named: "unified iPad \(category) content")
        return true
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
            let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
            if item.exists, isFullyVerticallyContained(item.frame, in: viewport) {
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
        // Explicitly paused video must stay paused; playing video may enter PiP.
        try tapVideoTransportAfterChromeHides(named: "video pause before backgrounding")
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
        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        XCUIDevice.shared.press(.home)
        if !waitUntil(timeout: 3, condition: {
            app.state == .runningBackground || app.state == .runningBackgroundSuspended
        }) {
            springBoard.activate()
        }
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = "Home background probe — Lumina \(app.state.rawValue), SpringBoard \(springBoard.state.rawValue)"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        guard waitUntil(timeout: 8, condition: {
            app.state == .runningBackground || app.state == .runningBackgroundSuspended
        }) else {
            throw ProofFailure.ui("Lumina remained in the foreground after SpringBoard activation: state=\(app.state.rawValue).")
        }
    }
    #endif

    private func startPlayback(using buttonIdentifier: String, freezeInitialTransport: Bool = false) throws -> XCUIElement {
        #if os(iOS)
        if buttonIdentifier == "convertButton" {
            // Compatibility playback remains covered through playback quality; the title
            // overflow now saves a local copy instead of exposing server conversion.
            let time = try startPlayback(using: "playButton", freezeInitialTransport: true)
            let transport = element("videoPlayPauseButton")
            try activate(element("videoSettingsButton"))
            try activate(element("videoSettingsTab-video"))
            let quality = element("videoQualityPicker")
            try activate(quality)
            try activate(menuAction("Up to 720p"))
            XCTAssertTrue(waitUntil(timeout: 45) {
                (quality.value as? String)?.contains("720p") == true && !self.element("videoStreamChanging").exists
            })
            XCTAssertFalse(element("videoStreamError").exists)
            try activate(element("videoSettingsDoneButton"))
            let revealedTransport = try waitForHittable(transport, timeout: 8, named: "video transport after settings")
            revealedTransport.tap()
            return time
        }
        #endif
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
            if element("detailScreen").exists {
                try revealDetailControl(button, named: buttonIdentifier)
            } else if element("videoCollectionScreen").exists {
                try revealDetailElement(button, in: element("videoCollectionScreen"), named: buttonIdentifier, requiresHittable: true)
            }
        }
        #endif
        try activate(button)
        // Existing playback journeys explicitly keep their saved position; choice semantics have their own workflow.
        let resumeChoice = element("resumePlaybackChoice")
        XCTAssertTrue(waitUntil(timeout: 35) { resumeChoice.exists || self.element("playbackScreen").exists })
        if resumeChoice.exists { try activate(resumeChoice.buttons.matching(NSPredicate(format: "label == %@", "Resume")).firstMatch) }
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "native playback screen")
        #if os(iOS)
        if freezeInitialTransport {
            // Converted playback resumes close to its end. Freeze the initially
            // visible running transport before any later accessibility round trip
            // can cross the normal chrome-hide deadline.
            let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 8, named: "initial video transport")
            XCTAssertEqual(transport.label, "Pause",
                           "Compatible playback must be running before the near-end resume is frozen for quality selection.")
            transport.tap()
        }
        #endif
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

    func journeyHomeHeaderScrolling() throws {
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        #if os(iOS)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-animations"])
        #else
        try launchAndResetSession()
        #endif
        try signIn(using: fixture, waitsForAccount: false)
        let home = element("homeScreen")
        try waitForExistence(home, timeout: 30, named: "Home before scrolling")
        #if os(tvOS)
        let movies = element("homeBrowse-movies")
        #else
        guard UIDevice.current.userInterfaceIdiom == .pad else { throw XCTSkip("This workflow targets iPad navigation.") }
        let movies = element("iPadCategory-movies")
        #endif
        try waitForExistence(movies, timeout: 20, named: "Home categories at the top")
        XCTAssertTrue(hasVisibleFrame(movies))
        try attachFrame(named: "Home categories over bright hero artwork")
        #if os(tvOS)
        let resume = element("resumeItem-\(fixture.directItemID)")
        for _ in 0..<12 where !hasVisibleFrame(resume) { XCUIRemote.shared.press(.down) }
        XCTAssertTrue(focus(resume, timeout: 20), "The remote must reach a shelf below the Home hero.")
        #else
        home.swipeUp()
        home.swipeUp()
        #endif
        XCTAssertTrue(waitUntil(timeout: 10) { !self.hasVisibleFrame(movies) },
                      "Home categories must scroll away rather than cover the shelves.")
        XCTAssertFalse(hasVisibleFrame(element("homeMasthead")))
        try attachFrame(named: "Home shelves without an overlapping navigation header")
        #if os(tvOS)
        for _ in 0..<12 where !hasVisibleFrame(movies) { XCUIRemote.shared.press(.up) }
        try activate(movies)
        #else
        for _ in 0..<8 where !hasVisibleFrame(movies) { home.swipeDown() }
        movies.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        #endif
        let library = element("libraryScreen")
        try waitForExistence(library, timeout: 25, named: "Movies Library")
        try waitForExistence(movies, timeout: 10, named: "pinned Library categories")
        let headerY = movies.frame.minY
        let first = application!.descendants(matching: .any).matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "libraryItem-")
        ).firstMatch
        try waitForExistence(first, timeout: 20, named: "first Library poster")
        let firstID = first.identifier
        #if os(tvOS)
        XCTAssertTrue(focus(first, timeout: 20))
        for _ in 0..<8 { XCUIRemote.shared.press(.down) }
        #else
        library.swipeUp()
        library.swipeUp()
        #endif
        XCTAssertFalse(hasVisibleFrame(element(firstID)), "The Library must actually scroll through its posters.")
        XCTAssertTrue(hasVisibleFrame(movies), "Library categories must remain pinned while browsing.")
        XCTAssertEqual(movies.frame.minY, headerY, accuracy: 2)
        try attachFrame(named: "Library posters beneath pinned categories")
        completed = true
    }

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
        let audioPlayer = element("audioPlayerStage")
        let wideAudioClose = element("closePlayerButton")
        let isAudioPlayer = audioPlayer.exists
        let closeButton = isAudioPlayer
            ? application!.buttons.matching(identifier: "BackButton").firstMatch
            : wideAudioClose
        if isAudioPlayer {
            if hasVisibleFrame(wideAudioClose) {
                try waitForHittable(wideAudioClose, timeout: 10, named: "wide audio player close")
                try activate(wideAudioClose)
            } else if usesStandardPhonePushedChrome {
                XCTAssertFalse(hasVisibleFrame(closeButton),
                               "The standard-phone audio player must use native leading-edge Back instead of a visible custom Back button.")
                try performStandardPhoneEdgeGesture(named: "audio player")
            } else {
                try waitForExistence(closeButton, timeout: 10, named: "iOS player back button")
                try activate(closeButton)
            }
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

    private func stopAudioPlayer() throws {
        #if os(iOS)
        let audioStage = element("audioPlayerStage")
        let wideClose = element("closePlayerButton")
        if hasVisibleFrame(audioStage), hasVisibleFrame(wideClose) {
            let options = element("audioOptionsButton")
            try waitForHittable(options, timeout: 10, named: "wide audio playback menu")
            try activate(options)
            try activate(menuAction("Stop"))
            return
        }
        #endif
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
    }

    #if os(iOS)
    private func closePlayerAfterPictureInPictureReturn() throws {
        let chrome = element("videoPlaybackChrome")
        let viewport = try applicationViewport()
        guard disappears(chrome, timeout: 8) else {
            throw ProofFailure.ui("The restored Picture in Picture player did not hide playback chrome before its public reveal.")
        }
        let surface = try waitForHittable(element("videoControlsSurface"), timeout: 10, named: "restored Picture in Picture controls reveal surface")
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()

        var frame: CGRect?
        guard waitUntil(timeout: 10, condition: {
            guard let snapshot = try? self.application!.snapshot() else { return false }
            let nodes = [snapshot] + self.pipSnapshotDescendants(snapshot)
            guard let chromeNode = nodes.first(where: { $0.identifier == "videoPlaybackChrome" }),
                  let closeNode = nodes.first(where: { $0.identifier == "closePlayerButton" }),
                  !chromeNode.frame.isEmpty,
                  !closeNode.frame.isEmpty else {
                return false
            }
            let candidate = closeNode.frame
            let finite = [candidate.minX, candidate.minY, candidate.width, candidate.height]
                .allSatisfy(\.isFinite)
            guard finite, viewport.contains(candidate) else { return false }
            frame = candidate
            return true
        }), let frame else {
            throw ProofFailure.ui("The restored Picture in Picture player did not render playback chrome.")
        }
        let finite = [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite)
        guard finite, !frame.isEmpty, viewport.contains(frame) else {
            throw ProofFailure.ui("The rendered Picture in Picture close button had no valid physical tap point.")
        }
        let point = CGVector(
            dx: (frame.midX - viewport.minX) / viewport.width,
            dy: (frame.midY - viewport.minY) / viewport.height
        )
        guard point.dx.isFinite, point.dy.isFinite, (0...1).contains(point.dx), (0...1).contains(point.dy) else {
            throw ProofFailure.ui("The rendered Picture in Picture close button was outside the usable viewport.")
        }
        try applicationWindow().coordinate(withNormalizedOffset: point).tap()
        guard disappears(element("playbackScreen"), timeout: 10) else {
            throw ProofFailure.ui("The restored Picture in Picture close button did not dismiss the native player.")
        }
    }

    private func relaunchAuthenticatedSession() throws {
        guard let application else {
            throw ProofFailure.ui("The authenticated application was unavailable for relaunch.")
        }
        application.terminate()
        application.launch()
        let home = element("homeScreen")
        let connectionForm = element("serverURLField")
        guard waitUntil(timeout: 20, condition: {
            self.hasVisibleFrame(home) && !self.hasVisibleFrame(connectionForm)
        }) else {
            throw ProofFailure.ui("The authenticated session did not restore Home after relaunch.")
        }
    }
    #endif

    private func returnToLibrary() throws {
        #if os(tvOS)
        let detail = element("detailScreen")
        try waitForExistence(detail, timeout: 15, named: "detail screen before returning to library")
        XCUIRemote.shared.press(.menu)
        #else
        if usesStandardPhonePushedChrome {
            try assertStandardPhonePushedChrome(element("detailScreen"), named: "detail")
            try performStandardPhoneEdgeGesture(named: "detail")
            XCTAssertTrue(waitUntil(timeout: 15) { !self.hasVisibleFrame(self.element("detailScreen")) },
                          "A completed leading-edge swipe must pop the detail exactly one level.")
        } else {
            let backButton = application!.buttons.matching(identifier: "BackButton").firstMatch
            try waitForExistence(backButton, timeout: 15, named: "navigation back button")
            try activate(backButton)
        }
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
        if UIDevice.current.userInterfaceIdiom == .pad {
            let categories = element("iPadUnifiedCategories")
            for _ in 0..<4 {
                if hasVisibleFrame(categories) { return }
                let back = application!.buttons.matching(identifier: "BackButton").firstMatch
                try waitForExistence(back, timeout: 10, named: "iPad tour navigation back button")
                try activate(back)
                if waitUntil(timeout: 5, condition: { self.hasVisibleFrame(categories) }) { return }
            }
            guard hasVisibleFrame(categories) else {
                throw ProofFailure.ui("iPad Back did not unwind the tour to the unified Video library root.")
            }
        } else {
            try unwindTourLibraryNavigationStack()
        }
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

    #if os(iOS)
    /// The player deliberately hides its chrome during active playback. Waiting for
    /// that public hidden state makes a single physical surface tap unambiguously
    /// reveal it, without accessibility activation's toggle semantics or a stale
    /// pre-check crossing the auto-hide deadline.
    private func tapVideoTransportAfterChromeHides(named name: String) throws {
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 8),
                      "Playback chrome must auto-hide before using its public reveal surface.")
        let surface = try waitForHittable(element("videoControlsSurface"), timeout: 8, named: "video controls reveal surface")
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 8, named: name)
        transport.tap()
    }
    #endif

    private func waitForExistence(_ element: XCUIElement, timeout: TimeInterval, named name: String) throws {
        guard appears(element, timeout: timeout) else {
            throw ProofFailure.timeout("Timed out waiting for \(name).")
        }
    }

    private func hasVisibleFrame(_ control: XCUIElement) -> Bool {
        guard control.exists, application != nil else { return false }
        let frame = control.frame
        guard !frame.isEmpty,
              [frame.minX, frame.minY, frame.width, frame.height].allSatisfy({ $0.isFinite }) else {
            return false
        }
        // Full-screen playback can expose an inactive zero-sized first window.
        // UIKit's hit-test is authoritative when no usable viewport is exposed.
        guard let window = visibleApplicationWindow else { return control.isHittable }
        let viewport = window.frame
        return !frame.intersection(viewport).isEmpty
    }

    private var visibleApplicationWindow: XCUIElement? {
        application?.windows.allElementsBoundByIndex.filter { window in
            let frame = window.frame
            return !frame.isEmpty && [frame.minX, frame.minY, frame.width, frame.height].allSatisfy { $0.isFinite }
        }.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
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

    #if os(iOS)
    private func waitForVideoPlaybackDiagnostics(
        requested: Bool,
        playing: Bool,
        scenePhase: String? = nil,
        requiredTokens: [String] = [],
        timeout: TimeInterval
    ) throws {
        let diagnostics = element("videoGestureDiagnostics")
        try waitForExistence(diagnostics, timeout: timeout, named: "video playback diagnostics")
        let expectedStates = [
            "playing=\(playing) requested=\(requested)",
            scenePhase.map { "phase=\($0)" }
        ].compactMap { $0 } + requiredTokens
        guard waitUntil(timeout: timeout, condition: {
            guard let value = diagnostics.value as? String else { return false }
            return expectedStates.allSatisfy(value.contains)
        }) else {
            throw ProofFailure.ui(
                "Video playback state did not become \(expectedStates.joined(separator: ", ")): \(diagnostics.value ?? "absent")."
            )
        }
    }
    #endif

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

    #if os(iOS)
    private func hasVisiblePIPControl(in pipView: XCUIElement) -> Bool {
        pipView.descendants(matching: .button).allElementsBoundByIndex.contains { control in
            hasVisibleFrame(control) && control.isHittable
        }
    }

    private func attachPIPReturnObservation(named name: String,
                                            pipWindow: XCUIElement,
                                            hadVisibleDescendantControl: Bool) {
        let screen = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screen.name = name
        screen.lifetime = .keepAlways
        add(screen)

        let evidence: String
        if let snapshot = try? pipWindow.snapshot() {
            let records = ([snapshot] + pipSnapshotDescendants(snapshot)).map { node in
                let visibleWithinPIP = !node.frame.isEmpty && node.frame.intersects(snapshot.frame)
                return "id=\(node.identifier.debugDescription) label=\(node.label.debugDescription) type=\(node.elementType.rawValue) frame=\(node.frame) visibleWithinPIP=\(visibleWithinPIP) enabled=\(node.isEnabled)"
            }
            evidence = "hadVisibleDescendantControl=\(hadVisibleDescendantControl)\n\(records.joined(separator: "\n"))"
        } else {
            evidence = "hadVisibleDescendantControl=\(hadVisibleDescendantControl)\nsnapshot unavailable"
        }
        print("PIP_RETURN_SURFACE \(name)\n\(evidence)")
        let attachment = XCTAttachment(string: evidence)
        attachment.name = "\(name) AX"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @discardableResult
    private func attachSpringBoardPIPObservation(named name: String,
                                                 springBoard: XCUIApplication,
                                                 controlNamed: String) -> CGRect? {
        let screen = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screen.name = name
        screen.lifetime = .keepAlways
        add(screen)

        let evidence: String
        if let snapshot = try? springBoard.snapshot() {
            let nodes = [snapshot] + pipSnapshotDescendants(snapshot)
            let records = nodes.map { node in
                let visible = !node.frame.isEmpty && node.frame.intersects(snapshot.frame)
                return "id=\(node.identifier.debugDescription) label=\(node.label.debugDescription) type=\(node.elementType.rawValue) frame=\(node.frame) visible=\(visible) enabled=\(node.isEnabled)"
            }
            evidence = records.joined(separator: "\n")
            let control = nodes.first { $0.identifier == controlNamed && $0.label == controlNamed }
            let controlFrame = control?.frame
            print("PIP_SPRINGBOARD_SURFACE \(name)\n\(evidence)")
            let attachment = XCTAttachment(string: evidence)
            attachment.name = "\(name) AX"
            attachment.lifetime = .keepAlways
            add(attachment)
            return controlFrame
        } else {
            evidence = "snapshot unavailable"
        }
        print("PIP_SPRINGBOARD_SURFACE \(name)\n\(evidence)")
        let attachment = XCTAttachment(string: evidence)
        attachment.name = "\(name) AX"
        attachment.lifetime = .keepAlways
        add(attachment)
        return nil
    }

    private func attachPIPReturnGuard(named name: String,
                                      state: String,
                                      frame: CGRect,
                                      exists: Bool,
                                      visible: Bool,
                                      hittable: Bool,
                                      hadVisibleDescendantControl: Bool) {
        let screen = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screen.name = name
        screen.lifetime = .keepAlways
        add(screen)
        let evidence = "state=\(state) exists=\(exists) frame=\(frame) visible=\(visible) hittable=\(hittable) hadVisibleDescendantControl=\(hadVisibleDescendantControl)"
        print("PIP_RETURN_GUARD \(evidence)")
        let attachment = XCTAttachment(string: evidence)
        attachment.name = "\(name) state"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func pipSnapshotDescendants(_ snapshot: XCUIElementSnapshot) -> [XCUIElementSnapshot] {
        snapshot.children.flatMap { [$0] + pipSnapshotDescendants($0) }
    }
    #endif

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
            if (videoContainerTarget || targetIdentifier.hasPrefix("requestFeatured-")),
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
    let seriesAboutFirstParagraph: String?
    let seriesAboutFinalParagraph: String?
    let seriesAboutSource: String?
    let seriesAboutFirstNote: String?
    let seriesAboutSecondNote: String?
    let seasonID: String
    let secondSeasonID: String?
    let episodeID: String
    let episodeTitle: String
    let episodeAbout: String?
    let episodeAboutSource: String?
    let episodeAboutNote: String?
    let secondEpisodeID: String
    let secondEpisodeTitle: String
    let secondEpisodeAbout: String?
    let secondEpisodeAboutSource: String?
    let secondEpisodeAboutNote: String?
    let thirdEpisodeID: String?
    let thirdEpisodeTitle: String?
    let fourthEpisodeID: String?
    let fourthEpisodeTitle: String?
    let secondSeasonEpisodeID: String?
    let secondSeasonEpisodeTitle: String?
    let episodeIntroEndSeconds: Double
    let episodeCreditsStartSeconds: Double
    let audioAlbumID: String
    let audioAlbumTitle: String
    let audioArtistID: String
    let audioTrackID: String
    let audioTrackTitle: String
    let audioTrackDurationSeconds: Double
    let audioSecondTrackID: String
    let audioSecondTrackTitle: String
    let sharedMusicPlaylistID: String?
    let sharedMusicPlaylistTitle: String?
    let audioLyric: String?
    let requestMovieKey: String?
    let requestMovieTitle: String?
    let requestShowKey: String?
    let requestShowTitle: String?
    let requestUnavailableShowKey: String?
    let requestUnavailableShowTitle: String?
    let requestAnimeKey: String?
    let requestAnimeTitle: String?
    let requestFreshAnimeKey: String?
    let requestFreshAnimeTitle: String?
}

private struct ProofHomeShelf: Codable, Equatable {
    let id: String
    let visible: Bool
}

private struct ProofHomeRow: Decodable {
    struct Item: Decodable { let Id: String }
    let id: String
    let shelf_id: String
    let items: [Item]
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

    @MainActor func metadataDocument(identity: APIIdentity, id: String) async throws -> [String: Any] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Metadata/titles").appendingPathComponent(id))
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        guard let doc = try JSONSerialization.jsonObject(with: await send(request)) as? [String: Any] else {
            throw ProofAPIError.malformedResponse
        }
        return doc
    }

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

    func homeRows(identity: APIIdentity) async throws -> [ProofHomeRow] {
        struct Response: Decodable { let rows: [ProofHomeRow] }
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Home/rows"))
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        return try JSONDecoder().decode(Response.self, from: await send(request)).rows
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
    func seedProgress(identity: APIIdentity, itemID: String, seconds: Int, completed: Bool = false) async throws {
        var clear = URLRequest(url: origin.appendingPathComponent("Users/\(identity.userID)/Items/\(itemID)/UserData"))
        clear.httpMethod = "POST"
        clear.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        clear.setValue("application/json", forHTTPHeaderField: "Content-Type")
        clear.httpBody = try JSONSerialization.data(withJSONObject: ["Played": completed])
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

    func booksCatalogEvidence(identity: APIIdentity, proseBookID: String) async -> String {
        var components = URLComponents(url: origin.appendingPathComponent("Lumina/Books"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "q", value: ""), URLQueryItem(name: "sort", value: "added")]
        guard let url = components?.url else { return "outcome=unavailable" }
        var request = URLRequest(url: url)
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        do {
            let (data, response) = try await session.data(for: request)
            guard let reply = response as? HTTPURLResponse else { return "outcome=unavailable" }
            guard (200..<300).contains(reply.statusCode) else { return "status=\(reply.statusCode)" }
            guard let page = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let items = page["items"] as? [[String: Any]] else {
                return "status=\(reply.statusCode) outcome=malformed"
            }
            return "status=\(reply.statusCode) items=\(items.count) prosePresent=\(items.contains { $0["id"] as? String == proseBookID })"
        } catch {
            return "outcome=unavailable"
        }
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

    func initialVideoFormatRecovery() async throws -> VideoFormatRecoveryEvidence {
        let request = URLRequest(url: origin.appendingPathComponent("Lumina/Proof/initial-video-format-recovery"))
        return try JSONDecoder().decode(VideoFormatRecoveryEvidence.self, from: await send(request))
    }

    func resetInitialVideoFormatRecovery() async throws {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Proof/initial-video-format-recovery/reset"))
        request.httpMethod = "POST"
        _ = try await send(request)
    }

    func recoveryHomeEvidence(identity: APIIdentity, itemID: String, title: String) async -> String {
        func matches(_ data: Data, key: String) -> String {
            guard let document = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return "\(key)=malformed"
            }
            let rows = (document["rows"] as? [[String: Any]]) ?? []
            let items = (document["Items"] as? [[String: Any]]) ?? []
            let entries = rows.flatMap { row in
                ((row["items"] as? [[String: Any]]) ?? []).map { item in
                    "\(row["shelf_id"] as? String ?? "unknown"):\(item["Id"] as? String ?? "missing"):\(item["Name"] as? String ?? "missing")"
                }
            } + items.map { item in
                "resume:\(item["Id"] as? String ?? "missing"):\(item["Name"] as? String ?? "missing")"
            }
            let expected = entries.filter { $0.contains(itemID) || $0.contains(title) }
            return "\(key)=\(expected.isEmpty ? "absent" : expected.joined(separator: ","))"
        }
        do {
            var home = URLRequest(url: origin.appendingPathComponent("Lumina/Home/rows"))
            home.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
            var components = URLComponents(url: origin.appendingPathComponent("UserItems/Resume"), resolvingAgainstBaseURL: false)
            components?.queryItems = [
                URLQueryItem(name: "IncludeItemTypes", value: "Movie,Episode,Video"),
                URLQueryItem(name: "Recursive", value: "true"),
                URLQueryItem(name: "Limit", value: "50"),
            ]
            guard let resumeURL = components?.url else { return "evidence=unavailable" }
            var resume = URLRequest(url: resumeURL)
            resume.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
            return "\(matches(try await send(home), key: "home")) \(matches(try await send(resume), key: "resume"))"
        } catch {
            return "evidence=unavailable"
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

private struct VideoFormatRecoveryEvidence: Decodable {
    let faultedDirectStream: Bool
    let compatibilityRequests: Int
    let compatibilityProfile: Bool
    let normalRecoveryProfile: Bool
    let sameMediaSource: Bool
    let preservedOptions: Bool
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
