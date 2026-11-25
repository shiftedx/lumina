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

#if os(iOS)
@MainActor
final class NativePhoneMusicHomeTests: NativeProofJourney {
    func testHomeLayoutAndBrowse() async throws { try await journeyPhoneMusicHome() }
    func testMiniPlayerPreservesMusicAcrossBackground() async throws { try await journeyMusicHomeMiniPlayerAcrossBackground() }
    func testLegacyMusicBrowse() async throws { try await journeyPhoneMusicLegacy() }
    func testContextualMix() async throws { try await journeyPhoneContextualMix() }
    func testArtistInstantMixDismissalPreservesQueue() async throws { try await journeyPhoneArtistInstantMixDismissal() }
    func testNowPlayingTrackRadioDismissalPreservesQueue() async throws { try await journeyPhoneNowPlayingTrackRadioDismissal() }
    func testOfflineHomeResume() async throws { try await journeyPhoneOfflineMusicHome() }
    func testOfflineReconnectKeepsDeviceQueue() async throws {
        try await journeyPhoneOfflineMusicHome(reconnect: true)
    }
    func testExplicitHomeResume() async throws { try await journeyPhoneExplicitHomeResume() }
}
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
final class NativeActorLibraryTests: NativeProofJourney {
    func testCastOpensActorTitlesAndBackReturnsToTheTitle() throws {
        try journeyActorLibrary()
    }
}
#endif

@MainActor
final class NativeStreamingTests: NativeProofJourney {
    func testSavedStreamingUsesLibraryWhenRemoteStreamingIsDisabled() async throws {
        try await journeyNativeStreaming(remoteEnabled: false)
    }
    func testConfiguredStreamingShowsRecommendationsLiveAndServerPlayback() async throws {
        try await journeyNativeStreaming(remoteEnabled: true)
    }
}

@MainActor
final class NativeTVPhoneEnrollmentTests: NativeProofJourney {
    #if os(tvOS)
    func testTVSignsInAfterAnEnrolledPhoneApprovesItsCode() async throws {
        try await journeyTVPhoneEnrollment()
    }
    #else
    func testEnrolledPhoneSuppliesItsServerAndOpensTVApproval() async throws {
        try await journeyPhoneApprovesTV(useDeepLink: true, nearby: true)
    }
    func testEnrolledPhoneApprovesTVFromAccountWithoutSigningOut() async throws {
        try await journeyPhoneApprovesTV(useDeepLink: false)
    }
    func testEnrolledPhoneOpensTVQRLinkAndCanDenyIt() async throws {
        try await journeyPhoneApprovesTV(useDeepLink: true)
    }
    #endif
}

@MainActor
final class NativeCollectionsTests: NativeProofJourney {
    #if os(iOS)
    func testCollectionsFilterTheExistingMovieLibrary() throws {
        try journeyCollectionFilter()
    }
    #else
    func testCollectionsFilterTheExistingMovieLibrary() throws {
        try journeyTVCollectionFilter()
    }
    #endif
}

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
@MainActor
final class NativeBookTextLimitsTests: NativeProofJourney {
    func testOversizedProseChapterShowsReaderError() async throws {
        try await journeyOversizedProseChapterShowsReaderError()
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
final class NativeMusicBaselineTimingTests: NativeProofJourney {
    func testMusicBrowsePlayAndNextTiming() async throws { try await journeyMusicBaselineTiming() }
    #if os(iOS)
    func testMusicHomeReadyTiming() throws { try journeyMusicHomeReadyTiming() }
    #endif
}

@MainActor
final class NativeMusicFileQualityTests: NativeProofJourney {
    func testAlbumAndArtistExposeSourceFileQuality() throws { try journeyMusicFileQuality() }
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

    func testStandardPhoneDetailPlayClearsFloatingTabBar() async throws {
        try await journeyInitialVideoFormatRecovery(stopsAfterPlaybackStarts: true)
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

    func testMetadataSaveRefreshesCatalogOnce() async throws {
        try await journeyLibraryMetadata(refreshOnly: true)
    }
    func testMetadataSheetsFollowLightAndDarkAppearance() async throws {
        try await journeyMetadataSheetAppearance(themes: ["light", "dark"])
    }
    func testMetadataSheetsRemainUsableWithIncreasedContrast() async throws {
        try await journeyMetadataSheetAppearance(themes: ["light"])
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
    func testHeaderEntersHeroBeforeShelves() throws { try journeyTVHeaderHeroFocus() }
    func testReducedMotionKeepsRemoteFocusAtRestingSize() throws { try journeyTVHeaderHeroFocus(reducedMotion: true) }
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
    func testTVLargeMovieAndSeriesCatalogs() throws { try journeyTVLargeMovieAndSeriesCatalogs() }
    func testTVLargeLibraryEntryTiming() throws { try journeyTVLargeLibraryEntryTiming() }
}
@MainActor
final class NativeTVBackHistoryTests: NativeProofJourney {
    func testTVBackRestoresLibraryHubFocus() throws { try journeyTVBackRestoresLibraryHubFocus() }
}
@MainActor
final class NativeTVMusicBrowseAndAutoHideBarTests: NativeProofJourney {
    func testTVMusicBrowseAndAutoHideBar() async throws { try await journeyTVMusicBrowseAndAutoHideBar() }
}
@MainActor
final class NativeTVMusicHomeTests: NativeProofJourney {
    func testHomeAndLegacyBrowsePlayback() async throws { try await journeyTVMusicHomeAndLegacyBrowse() }
    func testOfflineMusicHomeRestoresDownloadedQueue() async throws { try await journeyTVOfflineMusicHome() }
    func testContextualMixReturnsFocus() async throws { try await journeyTVContextualMixFocus() }
    func testContinuationOptInSurvivesHomeAndNaturalEnd() async throws { try await journeyTVMusicContinuationAcrossHomeAndNaturalEnd() }
    func testBrowseShortcutReturnsFocus() throws { try journeyTVBrowseShortcutFocus() }
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
    func testHiddenNativeControlsCanReturnToSettingsDuringIntro() async throws {
        try await journeyTVHiddenNativeControlsReturnToSettings()
    }
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
    func testHomeKeepsCategoriesInLibrary() throws { try journeyMobileHomeLibraryNavigation() }
    func testIPadHomeHeaderScrollsAwayAndLibraryCategoriesStayPinned() throws { try journeyHomeHeaderScrolling() }
    func testIPadHomeRefreshRequiresIntentionalPull() throws {
        try journeyHomeHeaderScrolling(checksRefresh: true)
    }
    func testCompleteCatalogSupportsFastPosterScrolling() throws {
        try journeyCompleteCatalogSupportsFastPosterScrolling()
    }
}

@MainActor
final class NativeMusicCatalogScrollingTests: NativeProofJourney {
    func testMusicLoadsNextPageWhileScrolling() throws {
        try journeyMusicLoadsNextPageWhileScrolling()
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
    func testHeroFlicksWithNormalMotion() async throws {
        try await journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle(motionOnly: true)
    }
    func testHeroFlicksWithReducedMotion() async throws {
        try await journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle(reducedMotion: true)
    }
    func testCategoryOverflowWithReducedMotion() throws { try journeyCategoryOverflowWithReducedMotion() }
    func testCategoryOverflowWithNormalMotion() throws { try journeyCategoryOverflowWithReducedMotion(reducedMotion: false) }
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
    func testClosedDuoStatusTapReturnsHomeHeroToTop() async throws {
        try await journeyClosedDisplayCategoryWidth(checksStatusTap: true)
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
#if os(tvOS)
@MainActor
final class NativeTVRequestQuotaRetryTests: NativeProofJourney {
    func testQuotaFailureBlocksSubmissionUntilRemoteRetry() async throws {
        try await journeyTVRequestQuotaRetry()
    }
}
#endif
@MainActor
final class NativeServerNavigationTests: NativeProofJourney {
    func testServerFeaturesAndRemovedLibraryUpdateNavigation() async throws {
        try await journeyServerNavigation()
    }
}
@MainActor
final class NativeServerMediaLanguageTests: NativeProofJourney {
    func testExistingJapaneseAudioCanRequestEnglish() async throws {
        try await journeyServerMediaLanguage()
    }
}
@MainActor
final class NativeRequestsVaultViewTests: NativeProofJourney {
    func testAvailableTitlesOpenTheirVaultDestinationsAndReturnToRequests() async throws {
        try await journeyNativeRequestsVaultView()
    }
}
#if os(iOS)
@MainActor
final class NativeRequestsCatalogEfficiencyTests: NativeProofJourney {
    func testBrowseCachesGenresAndDefersHeroDetailsUntilVisible() async throws {
        try await journeyNativeRequestsCatalogEfficiency()
    }
}
#endif
@MainActor
final class NativeHomeDetailWatchlistPersistsThroughServerTests: NativeProofJourney { func testNativeHomeDetailWatchlistPersistsThroughServer() async throws { try await journeyNativeHomeDetailWatchlistPersistsThroughServer() } }
@MainActor
final class NativeEpisodeSkipActionsAndNextEpisodeTests: NativeProofJourney { func testNativeEpisodeSkipActionsAndNextEpisode() async throws { try await journeyNativeEpisodeSkipActionsAndNextEpisode() } }
@MainActor
final class NativeEpisodeAutoplayPresenceTests: NativeProofJourney {
    func testNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes() async throws {
        try await journeyNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes()
    }
    #if os(tvOS)
    func testUnansweredPresencePromptEntersLibraryArtworkStandbyAfterFiveMinutes() async throws {
        executionTimeAllowance = 900
        try await journeyNativeEpisodeAutoplayRequiresPresenceAfterThreeEpisodes(waitForStandby: true)
    }
    #endif
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
final class NativeHomeForegroundRefreshTests: NativeProofJourney {
    func testHomeRefreshesOnlyAfterBackgroundAndCoalescesActivation() async throws {
        try await journeyHomeForegroundRefresh()
    }
}

@MainActor
class NativeProofJourney: XCTestCase {
    var application: XCUIApplication?
    let isolationID = UUID().uuidString
    var brokerFixtureURL: URL?
    var cachedFixture: ProofFixture?

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

    func brokerRequest(_ request: URLRequest) throws -> Data {
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

    static let performanceTourPhases = [
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
}

struct ProofFixture: Decodable {
    let largeVideoCatalog: Bool?
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
    let savedYouTubeID: String?
    let savedTwitchID: String?
    let streamingRecommendationID: String?
    let streamingLiveID: String?
    let directItemTitle: String
    let franchiseCollectionID: String?
    let franchiseCollectionTitle: String?
    let manualCollectionID: String?
    let manualCollectionTitle: String?
    let smartCollectionID: String?
    let smartCollectionTitle: String?
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
    let audioPaginationAlbumID: String?
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
    let requestQuotaOutageAvailable: Bool?
    let requestAnimeKey: String?
    let requestAnimeTitle: String?
    let requestFreshAnimeKey: String?
    let requestFreshAnimeTitle: String?
}

struct ProofHomeShelf: Codable, Equatable {
    let id: String
    let visible: Bool
}

struct ProofHomeRow: Decodable {
    struct Item: Decodable { let Id: String }
    let id: String
    let shelf_id: String
    let items: [Item]
}

struct ProofHomeLayout: Decodable {
    let shelves: [ProofHomeShelf]?
}

struct APIWatchlistState: Decodable {
    let included: Bool
}

struct APIRequestPage: Decodable {
    let items: [APIRequestRecord]
}

struct RequestsCatalogMetrics: Decodable, CustomStringConvertible {
    let genresMovie: Int
    let genresShow: Int
    let genresAnime: Int
    let details: Int

    enum CodingKeys: String, CodingKey {
        case genresMovie = "genres_movie"
        case genresShow = "genres_show"
        case genresAnime = "genres_anime"
        case details
    }

    var description: String {
        "genres_movie=\(genresMovie) genres_show=\(genresShow) genres_anime=\(genresAnime) details=\(details)"
    }
}

struct MetadataRefreshMetrics: Decodable, CustomStringConvertible {
    let nextUp: Int

    enum CodingKeys: String, CodingKey {
        case nextUp = "next_up"
    }

    var description: String { "next_up=\(nextUp)" }
}

struct APIRequestRecord: Decodable {
    let id: String
    let key: String
    let language: String?
    let audioLanguage: String?
    let seasons: APIRequestSeasons?

    enum CodingKeys: String, CodingKey {
        case id, key, language, seasons
        case audioLanguage = "audio_language"
    }

    var seasonNumbers: [Int] {
        guard let seasons, case let .selected(values) = seasons else { return [] }
        return values
    }
}

enum APIRequestSeasons: Decodable {
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

struct ProofAPI {
    let origin: URL
    let session: URLSession

    @MainActor func devicePairing(_ action: String, identity: APIIdentity? = nil, body: [String: Any]) async throws -> [String: Any] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/DevicePairing/\(action)"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let deviceID = identity?.deviceID ?? "proof-pending-apple-tv"
        var authorization = "MediaBrowser Client=\"Lumina enrollment proof\",Device=\"Proof Apple TV\",DeviceId=\"\(deviceID)\",Version=\"0.1\""
        if let identity { authorization += ",Token=\"\(identity.token)\"" }
        request.setValue(authorization, forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        guard let object = try JSONSerialization.jsonObject(with: await send(request)) as? [String: Any] else { throw ProofAPIError.malformedResponse }
        return object
    }

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

    func streamingMetrics() async throws -> [String: Int] {
        let request = URLRequest(url: origin.appendingPathComponent("proof/streaming-metrics"))
        return try JSONDecoder().decode([String: Int].self, from: await send(request))
    }

    func setStreamingFixture(_ mode: String) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("proof/streaming/\(mode)"))
        request.httpMethod = "POST"
        _ = try await send(request)
    }

    func setNavigationFixture(_ mode: String) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("proof/navigation/\(mode)"))
        request.httpMethod = "POST"
        _ = try await send(request)
    }

    func requestRecords(identity: APIIdentity) async throws -> [APIRequestRecord] {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Requests"))
        request.setValue(mediaBrowserAuthorization(identity.token), forHTTPHeaderField: "Authorization")
        let response = try JSONDecoder().decode(APIRequestPage.self, from: await send(request))
        return response.items
    }

    func setRequestQuotaOutage(_ failing: Bool) async throws -> [String: Int] {
        var request = URLRequest(url: origin.appendingPathComponent("proof/request-quota-outage/\(failing)"))
        request.httpMethod = "POST"
        return try JSONDecoder().decode([String: Int].self, from: await send(request))
    }

    func requestsMetrics() async throws -> RequestsCatalogMetrics {
        let request = URLRequest(url: origin.appendingPathComponent("proof/requests-metrics"))
        return try JSONDecoder().decode(RequestsCatalogMetrics.self, from: await send(request))
    }

    func resetRequestsMetrics() async throws {
        var request = URLRequest(url: origin.appendingPathComponent("proof/requests-metrics/reset"))
        request.httpMethod = "POST"
        _ = try JSONDecoder().decode(RequestsCatalogMetrics.self, from: await send(request))
    }

    func metadataRefreshMetrics() async throws -> MetadataRefreshMetrics {
        let request = URLRequest(url: origin.appendingPathComponent("proof/metadata-refresh-metrics"))
        return try JSONDecoder().decode(MetadataRefreshMetrics.self, from: await send(request))
    }

    func resetMetadataRefreshMetrics() async throws {
        var request = URLRequest(url: origin.appendingPathComponent("proof/metadata-refresh-metrics/reset"))
        request.httpMethod = "POST"
        _ = try JSONDecoder().decode(MetadataRefreshMetrics.self, from: await send(request))
    }

    func authenticate(username: String, password: String, deviceID: String = "native-proof-verifier") async throws -> APIIdentity {
        var request = URLRequest(url: origin.appendingPathComponent("Users").appendingPathComponent("AuthenticateByName"))
        request.httpMethod = "POST"
        request.setValue("MediaBrowser Client=\"Lumina proof tests\",Device=\"Simulator\",DeviceId=\"\(deviceID)\",Version=\"0.1\"", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["Username": username, "Pw": password])

        let data = try await send(request)
        do {
            let response = try JSONDecoder().decode(AuthenticationResponse.self, from: data)
            guard !response.token.isEmpty, !response.user.id.isEmpty else { throw ProofAPIError.malformedResponse }
            return APIIdentity(token: response.token, userID: response.user.id, deviceID: deviceID)
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

struct APIIdentity {
    let token: String
    let userID: String
    let deviceID: String

    init(token: String, userID: String, deviceID: String = "native-proof-verifier") {
        self.token = token
        self.userID = userID
        self.deviceID = deviceID
    }
}

struct VideoFormatRecoveryEvidence: Decodable {
    let faultedDirectStream: Bool
    let compatibilityRequests: Int
    let compatibilityProfile: Bool
    let normalRecoveryProfile: Bool
    let sameMediaSource: Bool
    let preservedOptions: Bool
}

struct AuthenticationResponse: Decodable {
    let token: String
    let user: AuthenticatedUser

    enum CodingKeys: String, CodingKey {
        case token = "AccessToken"
        case user = "User"
    }
}

struct AuthenticatedUser: Decodable {
    let id: String

    enum CodingKeys: String, CodingKey {
        case id = "Id"
    }
}

struct ItemResponse: Decodable {
    let userData: APIUserData?

    enum CodingKeys: String, CodingKey {
        case userData = "UserData"
    }
}

struct APIUserData: Decodable {
    let played: Bool
    let isFavorite: Bool
    let playbackPositionTicks: Int64

    enum CodingKeys: String, CodingKey {
        case played = "Played"
        case isFavorite = "IsFavorite"
        case playbackPositionTicks = "PlaybackPositionTicks"
    }
}

enum ProofFailure: Error {
    case invalidFixture
    case nonLocalServer
    case ui(String)
    case timeout(String)
}

enum ProofAPIError: Error {
    case httpStatus(Int)
    case malformedResponse
    case transport
}

final class ProofRedirectGuard: NSObject, URLSessionTaskDelegate {
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

extension NativeProofJourney {
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
