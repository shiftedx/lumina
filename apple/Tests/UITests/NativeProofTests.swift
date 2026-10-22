import Foundation
import XCTest
#if os(iOS)
import UIKit
#endif

#if os(iOS)
@MainActor
final class NativeNativeBooksLibraryBrowsesShelvesAndMangaStacksTests: NativeProofJourney {
    func testNativeBooksLibraryBrowsesShelvesAndMangaStacks() async throws { try await journeyNativeBooksLibraryBrowsesShelvesAndMangaStacks() }
}
#endif

#if os(iOS)
@MainActor
final class NativeNativeBooksReadTurnResizeAndRestoreAllFormatsTests: NativeProofJourney {
    func testNativeBooksReadTurnResizeAndRestoreAllFormats() async throws { try await journeyNativeBooksReadTurnResizeAndRestoreAllFormats() }
}
#endif

#if os(iOS)
@MainActor
final class NativeNativeBooksKeepPhysicalRightInRTLLocaleTests: NativeProofJourney {
    func testNativeBooksKeepPhysicalRightInRTLLocale() async throws { try await journeyNativeBooksKeepPhysicalRightInRTLLocale() }
}
#endif

@MainActor
final class NativeFeaturedTitlePreservesDetailsAndResumesPlaybackTests: NativeProofJourney {
    func testFeaturedTitlePreservesDetailsAndResumesPlayback() async throws { try await journeyFeaturedTitlePreservesDetailsAndResumesPlayback() }
}

#if os(iOS)
@MainActor
final class NativeAccountTabAppearancePreservesHomeSelectionAndCompactHeaderTests: NativeProofJourney {
    func testAccountTabAppearancePreservesHomeSelectionAndCompactHeader() async throws { try await journeyAccountTabAppearancePreservesHomeSelectionAndCompactHeader() }
}
#endif

@MainActor
final class NativeLibraryPullRevealsSearchAndRefreshesProgressTests: NativeProofJourney {
    func testLibraryPullRevealsSearchAndRefreshesProgress() async throws { try await journeyLibraryPullRevealsSearchAndRefreshesProgress() }
}

@MainActor
final class NativeAudioAlbumTrackPlaybackReportsProgressTests: NativeProofJourney {
    func testAudioAlbumTrackPlaybackReportsProgress() async throws { try await journeyAudioAlbumTrackPlaybackReportsProgress() }
}

@MainActor
final class NativeMusicPlaylistsSmartSelectionAndDownloadedPlaybackTests: NativeProofJourney {
    func testMusicPlaylistsSmartSelectionAndDownloadedPlayback() async throws { try await journeyMusicPlaylistsSmartSelectionAndDownloadedPlayback() }
}

@MainActor
final class NativeMusicAdvancedControlsPausedRestoreAndQueueConflictTests: NativeProofJourney {
    func testMusicAdvancedControlsPausedRestoreAndQueueConflict() async throws { try await journeyMusicAdvancedControlsPausedRestoreAndQueueConflict() }
}

@MainActor
final class NativeShowSeasonEpisodeBrowsingAndPlaybackTests: NativeProofJourney {
    func testShowSeasonEpisodeBrowsingAndPlayback() async throws { try await journeyShowSeasonEpisodeBrowsingAndPlayback() }
}

@MainActor
final class NativeVideoWatchedFavoriteAndActivityFiltersTests: NativeProofJourney {
    func testVideoWatchedFavoriteAndActivityFilters() async throws { try await journeyVideoWatchedFavoriteAndActivityFilters() }
}

@MainActor
final class NativeNativePlaybackReportsResumeAndConversionProgressTests: NativeProofJourney {
    func testNativePlaybackReportsResumeAndConversionProgress() async throws { try await journeyNativePlaybackReportsResumeAndConversionProgress() }
}

@MainActor
final class NativeCurrentDuoLayoutKeepsControlsAndPlaybackReachableTests: NativeProofJourney {
    func testCurrentDuoLayoutKeepsControlsAndPlaybackReachable() async throws { try await journeyCurrentDuoLayoutKeepsControlsAndPlaybackReachable() }
}

@MainActor
final class NativeOriginalMKVReportsPeriodicProgressAndResumesTests: NativeProofJourney {
    func testOriginalMKVReportsPeriodicProgressAndResumes() async throws { try await journeyOriginalMKVReportsPeriodicProgressAndResumes() }
}

@MainActor
final class NativeCompatibleHLSReplayRequestsFreshSessionTests: NativeProofJourney {
    func testCompatibleHLSReplayRequestsFreshSession() async throws { try await journeyCompatibleHLSReplayRequestsFreshSession() }
}

@MainActor
class NativeProofJourney: XCTestCase {
    private var application: XCUIApplication?
    private let isolationID = UUID().uuidString
    private var brokerFixtureURL: URL?
    private var cachedFixture: ProofFixture?
    private var expectedBookFill = true

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
    func journeyNativeBooksLibraryBrowsesShelvesAndMangaStacks() async throws {
        let fixture = try loadFixture()
        guard let shelf = fixture.bookShelfID, let series = fixture.comicSeriesID, let comic = fixture.comicBookID else {
            throw XCTSkip("Generate the current visual Books fixture.")
        }
        try launchAndResetSession()
        try signIn(using: fixture)
        try activate(destinationControl("Books"))
        try revealLibraryItem(element("bookShelf-\(shelf)"))
        try activate(element("bookShelf-\(shelf)"))
        try activate(element("book-\(comic)"))
        try activate(element("bookControlsButton"))
        try waitForHittable(element("bookNextButton"), timeout: 30, named: "shelf book ready to read")
        try activate(element("bookCloseButton"))
        let back = element("BackButton")
        try activate(back.exists ? back : application!.navigationBars.buttons["Books"].firstMatch)
        let stack = element("bookSeries-\(series)")
        try revealLibraryItem(stack)
        try attachFrame(named: "Native Books cover grid, shelves and manga stacks")
        try activate(stack)
        try waitForExistence(element("book-\(comic)"), timeout: 20, named: "volume belonging to the selected manga stack")
    }

    func journeyNativeBooksReadTurnResizeAndRestoreAllFormats() async throws {
        try await readTurnAndRestoreBooks(rightToLeft: false, resizing: true, fillByDefault: false)
    }

    func journeyNativeBooksKeepPhysicalRightInRTLLocale() async throws {
        try await readTurnAndRestoreBooks(rightToLeft: true)
    }

    private func revealLibraryItem(_ item: XCUIElement) throws {
        let viewport = try applicationViewport()
        for _ in 0..<8 {
            if hasVisibleFrame(item) && item.isHittable { return }
            // Duo's split shell can put the application's default swipe point in the sidebar.
            // Anchor the gesture in the requested catalog column, including RTL.
            let anchor = item.exists && !item.frame.isEmpty ? item.frame.midX : viewport.midX
            try dragVertically(in: viewport, anchorX: anchor, up: true)
        }
    }

    private func readTurnAndRestoreBooks(rightToLeft: Bool, resizing: Bool = false, fillByDefault expectedFill: Bool? = nil) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let fillByDefault = expectedFill ?? expectedBookFill
        guard let comic = fixture.comicBookID, let prose = fixture.proseBookID,
              let pdf = fixture.pdfBookID, let cbr = fixture.cbrBookID else { throw XCTSkip("Generate the current native Books fixture.") }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        XCUIDevice.shared.orientation = .portrait
        try launchAndResetSession()
        for id in [comic, prose, pdf, cbr] { try await api.seedBookProgress(identity: identity, bookID: id) }
        try signIn(using: fixture)
        if rightToLeft {
            // Qualify the reader's RTL layout using the real session established through the connection form.
            application!.terminate()
            application!.launchArguments += ["-AppleLanguages", "(ar)", "-AppleLocale", "ar", "-AppleTextDirection", "YES", "-NSForceRightToLeftWritingDirection", "YES"]
            application!.launch()
            try waitForExistence(element("homeScreen"), timeout: 30, named: "Home after relaunch in an RTL locale")
        }
        try activate(destinationControl("Books"))
        try revealLibraryItem(element("bookCatalogButton"))
        try activate(element("bookCatalogButton"))
        for id in [comic, prose, pdf, cbr] {
            let card = element("book-\(id)")
            try revealLibraryItem(card)
            try waitForExistence(card, timeout: 20, named: "native book catalog entry")
            try activate(card)
            try activate(element("bookControlsButton"))
            let position = element("bookPosition")
            try waitForExistence(position, timeout: 20, named: "native reader position")
            let next = element("bookNextButton")
            try waitForHittable(next, timeout: 30, named: "prepared native page")
            guard waitUntil(timeout: 30, condition: { next.isEnabled }) else { throw ProofFailure.ui("Native book page did not become ready.") }
            let before = position.label
            try activate(next)
            guard waitUntil(timeout: 20, condition: { position.label != before }) else { throw ProofFailure.ui("Native page turn did not advance.") }
            guard waitUntil(timeout: 30, condition: { next.isEnabled }) else { throw ProofFailure.ui("Second native page did not become ready.") }
            let selected = position.label
            if rightToLeft, id == comic, !element("bookCompanionPage").exists {
                throw XCTSkip("Run the RTL pane contract with iPhone Duo fully opened (or another regular-width viewport).")
            }
            if id != prose, element("bookCompanionPage").exists {
                let selectedImage = application!.images.matching(NSPredicate(format: "label == %@", selected)).firstMatch
                try waitForExistence(selectedImage, timeout: 20, named: "selected image in expanded reader")
                XCTAssertGreaterThan(selectedImage.frame.midX, application!.windows.firstMatch.frame.midX,
                                     "The selected outer-screen page must remain physically right, including RTL manga.")
            }
            if id == comic {
                let selectedArt = application!.images.matching(NSPredicate(format: "label == %@", selected)).firstMatch
                XCTAssertEqual(selectedArt.value as? String, fillByDefault ? "Fill display" : "Fit entire page", "The default should suit the device; explicit appearance choices still override it.")
                let controlledViewport = application!.scrollViews.containing(.image, identifier: selected).firstMatch
                try waitForExistence(controlledViewport, timeout: 20, named: "reading viewport with controls")
                let controlledArea = controlledViewport.frame.width * controlledViewport.frame.height
                try activate(element("bookImmersiveButton"))
                let selectedImage = application!.images.matching(NSPredicate(format: "label == %@", selected)).firstMatch
                try waitForExistence(selectedImage, timeout: 20, named: "immersive manga page")
                let immersiveViewport = application!.scrollViews.containing(.image, identifier: selected).firstMatch
                guard waitUntil(timeout: 20, condition: { immersiveViewport.frame.width * immersiveViewport.frame.height > controlledArea }) else {
                    throw ProofFailure.ui("Hiding controls did not enlarge the reading canvas.")
                }
                print("PERF native reading canvas: controlled area=\(controlledArea) pt², immersive area=\(immersiveViewport.frame.width * immersiveViewport.frame.height) pt²")
                try attachFrame(named: "Immersive manga fills the available reading canvas")
                // Pan the cropped axis of this original 2:3 fixture. The other axis remains available for navigation.
                if fillByDefault {
                    if immersiveViewport.frame.width / immersiveViewport.frame.height > 2.0 / 3.0 { selectedImage.swipeUp() }
                    else { selectedImage.swipeLeft() }
                }
                try activate(element("bookControlsButton"))
                XCTAssertEqual(position.label, selected, "Panning cropped artwork must retain the selected page.")
                try activate(element("bookAppearanceButton"))
                try activate(element("bookSizingPicker"))
                try activate(application!.buttons["Fit entire page"].firstMatch)
                try activate(element("bookAppearanceDoneButton"))
                try activate(element("bookImmersiveButton"))
                let zoomPoint = immersiveViewport.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
                zoomPoint.doubleTap()
                guard waitUntil(timeout: 10, condition: { (selectedImage.value as? String)?.contains("200% zoom") == true }) else {
                    throw ProofFailure.ui("Double-tap did not magnify the native page.")
                }
                try attachFrame(named: "Native artwork magnified without changing reading position")
                zoomPoint.doubleTap()
                guard waitUntil(timeout: 10, condition: { selectedImage.value as? String == "Fit entire page" }) else {
                    throw ProofFailure.ui("Double-tap did not return to the fitted page.")
                }
                selectedImage.swipeLeft() // This original fixture reads right-to-left: left returns to the preceding spread.
                let precedingImage = application!.images.matching(NSPredicate(format: "label == %@", before)).firstMatch
                try waitForExistence(precedingImage, timeout: 20, named: "previous page through native immersive gesture")
                precedingImage.swipeRight()
                try waitForExistence(selectedImage, timeout: 20, named: "selected page through native immersive gesture")
                try activate(element("bookControlsButton"))
                XCTAssertEqual(position.label, selected)
            }
            try attachFrame(named: "Native book \(id == prose ? "EPUB" : id == pdf ? "PDF" : id == cbr ? "CBR" : "manga")")
            if resizing {
                let originalSize = application!.windows.firstMatch.frame.size
                XCUIDevice.shared.orientation = .landscapeLeft
                guard waitUntil(timeout: 15, condition: { self.application!.windows.firstMatch.frame.size != originalSize }) else {
                    throw ProofFailure.ui("The simulator did not resize the reader after rotation.")
                }
                guard waitUntil(timeout: 15, condition: { next.isEnabled }) else { throw ProofFailure.ui("Reader did not recover after resizing.") }
                if id != prose { XCTAssertEqual(position.label, selected, "Resizing must preserve the selected page.") }
                try attachFrame(named: "Native reader resized with selected page retained")
                XCUIDevice.shared.orientation = .portrait
                guard waitUntil(timeout: 15, condition: { self.application!.windows.firstMatch.frame.size == originalSize && next.isEnabled }) else {
                    throw ProofFailure.ui("The reader did not return to its original viewport.")
                }
            }
            try activate(element("bookCloseButton"))
            try activate(element("book-\(id)"))
            try activate(element("bookControlsButton"))
            try waitForExistence(element("bookPosition"), timeout: 20, named: "restored native book position")
            guard waitUntil(timeout: 30, condition: { self.element("bookPosition").label == selected }) else { throw ProofFailure.ui("Native book did not restore the saved page.") }
            try activate(element("bookCloseButton"))
        }
        completed = true
    }
    #endif

    func journeyFeaturedTitlePreservesDetailsAndResumesPlayback() async throws {
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
        try await api.seedProgress(identity: identity, itemID: fixture.matrixItemID, seconds: 0)

        try launchAndResetSession()
        try signIn(using: fixture)
        #if os(iOS)
        let carouselTitle = element("featuredTitle")
        try waitForExistence(carouselTitle, timeout: 15, named: "initial Home carousel title")
        let idleTitle = carouselTitle.label
        XCTAssertFalse(idleTitle.isEmpty)
        try attachFrame(named: "Home idle carousel initial feature")
        guard waitUntil(timeout: 14, condition: {
            !carouselTitle.label.isEmpty && carouselTitle.label != idleTitle
        }) else { throw ProofFailure.ui("The idle Home carousel did not advance to a different title.") }
        try attachFrame(named: "Home carousel advanced automatically")
        #endif
        #if os(tvOS)
        let focusedHeroPlay = element("featuredPlayButton")
        try waitForExistence(focusedHeroPlay, timeout: 15, named: "focusable Home hero play action")
        guard focus(focusedHeroPlay, timeout: 20) else {
            throw ProofFailure.ui("The Siri Remote could not focus the Home hero Play button.")
        }
        let focusedHeroTitle = element("featuredTitle").label
        XCTAssertFalse(focusedHeroTitle.isEmpty)
        try await Task.sleep(for: .seconds(9))
        XCTAssertEqual(element("featuredTitle").label, focusedHeroTitle,
                       "Hero rotation must pause while the Siri Remote focuses Play.")
        XCTAssertFalse(element("playbackScreen").exists, "Focusing Play must not start playback.")
        #endif
        try activate(element("featuredPauseButton"))
        XCTAssertEqual(element("featuredPauseButton").label, "Resume featured rotation")
        #if os(iOS)
        try revealInLibraryViewport([carouselTitle])
        let beforeSwipe = carouselTitle.label
        carouselTitle.swipeLeft()
        guard waitUntil(timeout: 5, condition: {
            !carouselTitle.label.isEmpty && carouselTitle.label != beforeSwipe
        }) else { throw ProofFailure.ui("A horizontal swipe did not change the paused Home carousel title.") }
        try attachFrame(named: "Paused Home carousel changed by horizontal swipe")
        let directChoice = element("featuredChoice-\(fixture.directItemID)")
        try revealInLibraryViewport([directChoice])
        try activate(directChoice)
        XCTAssertEqual(carouselTitle.label, fixture.directItemTitle)
        #endif
        XCTAssertFalse(element("refreshButton").exists)
        XCTAssertFalse(menuAction("Sign out").exists)
        #if os(iOS)
        XCTAssertFalse(hasVisibleFrame(application!.searchFields.firstMatch) && application!.searchFields.firstMatch.isHittable,
                       "Search should leave room for artwork until the user pulls down.")
        #endif
        try waitForExistence(element("homeScreen"), timeout: 15, named: "Home after sign-in")
        try waitForExistence(element("featuredPlayButton"), timeout: 15, named: "Home hero play action")
        try attachFrame(named: "Home cinematic hero")
        #if os(tvOS)
        let recentCaption = application!.staticTexts["New in your library"]
        #else
        let recentCaption = application!.staticTexts["Recently added"]
        #endif
        try waitForExistence(recentCaption, timeout: 15, named: "Home recent-video shelf caption")
        let knownPosters = [
            "recentItem-\(fixture.directItemID)": fixture.directItemTitle,
            "recentItem-\(fixture.convertedItemID)": fixture.convertedItemTitle,
            "recentItem-\(fixture.matrixItemID)": fixture.matrixItemTitle,
            "recentItem-\(fixture.episodeID)": fixture.episodeTitle,
        ]
        let recentPosters = application!.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "recentItem-"))
        var leadingPoster: XCUIElement?
        for _ in 0..<8 {
            let viewport = try applicationViewport()
            leadingPoster = recentPosters.allElementsBoundByIndex.first(where: {
                let frame = $0.frame
                return knownPosters[$0.identifier] != nil && !frame.isEmpty
                    && frame.minX >= viewport.minX && frame.maxX <= viewport.maxX
                    && frame.minY >= viewport.minY && frame.maxY <= viewport.maxY
            })
            if leadingPoster != nil { break }
            #if os(tvOS)
            XCUIRemote.shared.press(.down)
            #else
            try scrollLibrary(element("homeScreen"), up: true)
            #endif
        }
        guard let leadingPoster, let expectedTitle = knownPosters[leadingPoster.identifier] else {
            throw ProofFailure.ui("Home did not expose a known fixture poster in the visible recent-shelf column.")
        }
        let posterIdentifier = leadingPoster.identifier
        let knownPoster = element(posterIdentifier)
        XCTAssertTrue(knownPoster.label.contains(expectedTitle), "The recent shelf lost the known media title caption.")
        #if os(tvOS)
        guard focus(knownPoster, timeout: 20) else {
            throw ProofFailure.ui("The Siri Remote could not reveal the known recent-shelf poster.")
        }
        #else
        try revealInLibraryViewport([knownPoster])
        #endif
        try attachFrame(named: "Home recent video rail")
        try selectDestination("Video", screen: "libraryScreen")
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 15, named: "Video catalog")
        try attachFrame(named: "Video destination")
        try selectDestination("Audio", screen: "audioScreen")
        try waitForExistence(element("audioItem-\(fixture.audioAlbumID)"), timeout: 15, named: "Audio album")
        try attachFrame(named: "Audio destination")
        try selectDestination("Home", screen: "homeScreen")
        if element("featuredPauseButton").label == "Pause featured rotation" {
            try activate(element("featuredPauseButton"))
        }
        #if os(iOS)
        try revealInLibraryViewport([element("featuredChoice-\(fixture.convertedItemID)")])
        #endif
        try activate(element("featuredChoice-\(fixture.convertedItemID)"))
        XCTAssertEqual(element("featuredTitle").label, fixture.convertedItemTitle)
        try attachFrame(named: "Home panoramic artwork hero")
        #if os(iOS)
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertEqual(element("featuredTitle").label, fixture.convertedItemTitle)
        try attachFrame(named: "Home panoramic hero in landscape")
        XCUIDevice.shared.orientation = .portrait
        #endif
        try activate(element("featuredChoice-\(fixture.directItemID)"))
        XCTAssertEqual(element("featuredTitle").label, fixture.directItemTitle)
        let details = element("featuredDetailsButton")
        #if os(iOS)
        for _ in 0..<4 {
            if details.exists { break }
            try scrollLibrary(element("homeScreen"), up: true)
        }
        #endif
        try waitForExistence(details, timeout: 20, named: "featured title details")
        #if os(iOS)
        try revealInLibraryViewport([details])
        #endif
        let libraryCapture = XCTAttachment(screenshot: application!.screenshot())
        libraryCapture.name = "Home selected feature"
        libraryCapture.lifetime = .keepAlways
        add(libraryCapture)
        try activate(details)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "featured detail")
        XCTAssertTrue(application!.staticTexts[fixture.directItemTitle].exists)
        try returnToLibrary()

        let play = element("featuredPlayButton")
        try waitForExistence(play, timeout: 20, named: "featured play action")
        #if os(iOS)
        try revealInLibraryViewport([play])
        #endif
        try activate(play)
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "featured playback")
        let time = element("playbackTime")
        let initial = try waitForPlaybackValue(time, timeout: 15) { $0 >= 10 }
        _ = try waitForPlaybackValue(time, timeout: 15) { $0 >= initial + 3 }
        try closePlayer()
        try waitForExistence(element("featuredDetailsButton"), timeout: 20, named: "library after featured playback")
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.directItemID, minimum: 14)

        try signOutThroughAccount()
        try waitForExistence(element("serverURLField"), timeout: 15, named: "connection form after deliberate sign out")
        completed = true
    }

    #if os(iOS)
    func journeyAccountTabAppearancePreservesHomeSelectionAndCompactHeader() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)

        let home = element("homeScreen")
        let welcome = application!.staticTexts["Welcome home"]
        try waitForExistence(welcome, timeout: 20, named: "Home welcome heading after fresh login")
        let viewport = try applicationViewport()
        let homeFrame = home.frame.intersection(viewport)
        let headingFrame = welcome.frame
        try attachFrame(named: "Home initial native navigation and welcome position")
        XCTAssertFalse(homeFrame.isNull)
        XCTAssertGreaterThan(headingFrame.height, 0)
        let statusBar = application!.statusBars.firstMatch
        let safeTop = hasVisibleFrame(statusBar) ? max(viewport.minY, statusBar.frame.maxY) : viewport.minY
        // Scroll-view AX bounds may extend under the status bar; measure content from its effective safe top.
        let contentTop = max(homeFrame.minY, safeTop)
        XCTAssertGreaterThanOrEqual(headingFrame.minY, contentTop - 1)
        if hasVisibleFrame(statusBar) {
            XCTAssertLessThanOrEqual(headingFrame.minY - contentTop, headingFrame.height,
                                     "Home's first heading must follow its scrolling viewport without an empty navigation-title gap.")
        } else {
            // On runtimes that omit status-bar AX, scroll bounds include the unsafe top.
            // Bound the first heading against the actual viewport: this rejects the old empty title bar.
            XCTAssertLessThanOrEqual(headingFrame.minY - viewport.minY, viewport.height * 0.12,
                                     "Home's first heading must stay near the viewport top when safe-area AX is unavailable.")
        }
        let topAllowance = headingFrame.height * (hasVisibleFrame(statusBar) ? 1 : 2)
        XCTAssertLessThanOrEqual(homeFrame.minY - safeTop, topAllowance,
                                 "The Home viewport must begin near the safe top rather than below an unused title bar.")
        let sidebar = element("librarySidebar")
        let sidebarHome = sidebar.descendants(matching: .any).matching(identifier: "homeTab").firstMatch
        if hasVisibleFrame(sidebar), hasVisibleFrame(sidebarHome), sidebarHome.isHittable {
            // SwiftUI exposes the scroll container across the window; its visible
            // heading locates the content within the native split-view pane.
            XCTAssertGreaterThanOrEqual(headingFrame.minX, sidebar.frame.maxX - 1,
                                        "Home content must occupy the trailing pane rather than sit beneath its expanded sidebar.")
            let duplicateHomeTab = application!.tabBars.buttons["Home"]
            XCTAssertFalse(hasVisibleFrame(duplicateHomeTab) && duplicateHomeTab.isHittable,
                           "An expanded sidebar must not duplicate the destination controls in a tab bar.")
            try attachFrame(named: "Expanded native sidebar reserves a separate Home pane without duplicate tabs")
        }
        XCTAssertFalse(element("accountMenuButton").exists, "iOS Account belongs in its native tab.")
        try attachFrame(named: "Home welcome begins directly below the safe top")

        let pause = element("featuredPauseButton")
        try waitForExistence(pause, timeout: 20, named: "Home carousel pause action")
        if pause.label == "Pause featured rotation" { try activate(pause) }
        let choice = element("featuredChoice-\(fixture.directItemID)")
        try waitForExistence(choice, timeout: 20, named: "known Home feature selector")
        try revealInLibraryViewport([choice])
        try activate(choice)
        guard waitUntil(timeout: 5, condition: { self.element("featuredTitle").label == fixture.directItemTitle }) else {
            throw ProofFailure.ui("Selecting the known feature did not update Home.")
        }

        try selectDestination("Account", screen: "accountScreen")
        let picker = element("appearancePicker")
        try waitForExistence(picker, timeout: 15, named: "native Account appearance picker")
        let initialAppearance = (picker.value as? String) ?? ""
        XCTAssertTrue(["Dark", "Light", "System"].contains(initialAppearance),
                      "The native appearance picker must expose its selected appearance, received: \(initialAppearance).")
        for appearance in ["Light", "System", "Dark"] {
            try activate(picker)
            try activate(menuAction(appearance))
            guard waitUntil(timeout: 5, condition: { (self.element("appearancePicker").value as? String) == appearance }) else {
                throw ProofFailure.ui("The Account appearance picker did not select \(appearance).")
            }
            if appearance == "Light" { try attachFrame(named: "Account tab selects the Light appearance") }
        }
        try attachFrame(named: "Account tab restores the Dark appearance")
        try selectDestination("Home", screen: "homeScreen")
        XCTAssertEqual(element("featuredTitle").label, fixture.directItemTitle,
                       "Account appearance changes must preserve Home's selected feature.")
        let details = element("featuredDetailsButton")
        try revealInLibraryViewport([details])
        try activate(details)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "selected feature detail after Account visit")
        XCTAssertTrue(application!.staticTexts[fixture.directItemTitle].exists)
        try returnToLibrary()
        XCTAssertEqual(element("featuredTitle").label, fixture.directItemTitle,
                       "Back must return to the same Home feature after visiting Account.")
        try attachFrame(named: "Account tab and appearance preserve Home selection and detail back navigation")
        completed = true
    }
    #endif

    func journeyLibraryPullRevealsSearchAndRefreshesProgress() async throws {
        #if os(iOS)
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try await api.seedProgress(identity: identity, itemID: fixture.convertedItemID, seconds: 0)
        try await api.seedProgress(identity: identity, itemID: fixture.matrixItemID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        XCTAssertFalse(hasVisibleFrame(application!.searchFields.firstMatch) && application!.searchFields.firstMatch.isHittable)

        // Anchor inside artwork; the scroll view's accessibility frame can include the toolbar.
        let start = element("libraryItem-\(fixture.directItemID)").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        let end = start.withOffset(CGVector(dx: 0, dy: 72))
        start.press(forDuration: 0.05, thenDragTo: end)
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "search revealed by a short pull")
        try activate(search)
        search.typeText("Cartographer\n")
        guard waitUntil(timeout: 15, condition: {
            self.element("libraryItem-\(fixture.convertedItemID)").exists
                && !self.element("libraryItem-\(fixture.directItemID)").exists
        }) else { throw ProofFailure.ui("Native search did not filter the catalog.") }
        try activate(application!.buttons["Cancel"])
        try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 15, named: "Video catalog after search cancellation")

        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 0)
        try refreshLibrary()
        guard waitUntil(timeout: 15, condition: {
            !self.element("libraryItem-\(fixture.directItemID)").label.contains("in progress")
        }) else { throw ProofFailure.ui("Pull-to-refresh did not reload the changed watch position.") }
        #else
        throw XCTSkip("Discovery gestures use iOS touch input.")
        #endif
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
        let album = element("audioItem-\(fixture.audioAlbumID)")
        try waitForExistence(album, timeout: 25, named: "imported synthetic album")
        XCTAssertTrue(album.label.contains(fixture.audioAlbumTitle))
        try attachFrame(named: "Audio albums with synthetic artwork")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 25, named: "album tracks")
        let track = element("musicItem-\(fixture.audioTrackID)")
        try waitForExistence(track, timeout: 20, named: "synthetic album track")
        XCTAssertTrue(track.label.contains(fixture.audioTrackTitle))
        try attachFrame(named: "Album track list")
        try activate(track)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "audio track detail")
        let time = try startPlayback(using: "playButton")
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
        try waitForExistence(element("detailScreen"), timeout: 15, named: "track detail after browsing from audio")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "persistent now-playing bar")
        XCTAssertTrue(element("nowPlayingBar").label.contains(fixture.audioTrackTitle))
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioTrackID, minimum: 3)
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        #endif
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album after track detail")
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        #endif
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Audio catalog while music continues")
        try selectDestination("Video", screen: "libraryScreen")
        try waitForExistence(element("nowPlayingBar"), timeout: 15, named: "now-playing bar while browsing Video")
        try await Task.sleep(for: .seconds(3))
        try selectDestination("Audio", screen: "audioScreen")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "reopened persistent audio player")
        XCTAssertEqual(element("nowPlayingTitle").label, fixture.audioTrackTitle)
        let reopened = try waitForPlaybackValue(element("playbackTime"), timeout: 5) { _ in true }
        XCTAssertGreaterThanOrEqual(reopened, beforeBrowse + 2, "Browsing destinations restarted or paused the audio session.")
        try attachFrame(named: "Persistent audio reopened after browsing Video and Audio")
        try closePlayer()
        try waitForExistence(element("audioScreen"), timeout: 15, named: "Audio catalog after browsing from reopened player")
        try activate(album)
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album before queued playback")
        try activate(element("musicPlayButton"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "album audio player")
        guard waitUntil(timeout: 15, condition: {
            self.element("nowPlayingTitle").label == fixture.audioTrackTitle
        }) else { throw ProofFailure.ui("Play album did not start with the first numbered track.") }
        try activate(element("audioNextButton"))
        guard waitUntil(timeout: 20, condition: {
            self.element("nowPlayingTitle").label == fixture.audioSecondTrackTitle
        }) else { throw ProofFailure.ui("Next did not advance the album queue to its second track.") }
        try activate(element("audioQueueButton"))
        try waitForExistence(element("audioQueueScreen"), timeout: 15, named: "album play queue")
        let queuedFirst = element("audioQueueItem-\(fixture.audioTrackID)")
        let queuedSecond = element("audioQueueItem-\(fixture.audioSecondTrackID)")
        try waitForExistence(queuedFirst, timeout: 15, named: "first queued track")
        try waitForExistence(queuedSecond, timeout: 15, named: "second queued track")
        XCTAssertTrue(queuedFirst.label.contains(fixture.audioTrackTitle))
        XCTAssertTrue(queuedSecond.label.contains(fixture.audioSecondTrackTitle))
        try attachFrame(named: "Album queue with both imported tracks")
        try activate(queuedFirst)
        do {
            let queueDone = element("audioQueueDoneButton")
            try waitForExistence(queueDone, timeout: 15, named: "queue Done action after selecting a track")
            try activate(queueDone)
        } catch {
            captureScreenshot(succeeded: false) // Include the queue's live screenshot and accessibility hierarchy.
            throw error
        }
        guard waitUntil(timeout: 20, condition: {
            self.element("nowPlayingTitle").label == fixture.audioTrackTitle
        }) else { throw ProofFailure.ui("Choosing a queued track did not switch playback to that track.") }
        try activate(element("audioNextButton"))
        guard waitUntil(timeout: 20, condition: {
            self.element("nowPlayingTitle").label == fixture.audioSecondTrackTitle
        }) else { throw ProofFailure.ui("Next did not continue the selected album queue.") }
        let repeatControl = element("audioRepeatButton")
        XCTAssertEqual(repeatControl.value as? String, "Repeat off")
        for mode in ["Repeat queue", "Repeat track", "Repeat off"] {
            try activate(repeatControl)
            guard waitUntil(timeout: 5, condition: { repeatControl.value as? String == mode }) else {
                throw ProofFailure.ui("The repeat control did not expose \(mode).")
            }
        }
        let queuedTime = element("playbackTime")
        let queuedInitial = try waitForPlaybackValue(queuedTime, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(queuedTime, timeout: 20) { $0 >= queuedInitial + 5 }
        try activate(element("audioQueueButton"))
        try waitForExistence(element("audioQueueScreen"), timeout: 15, named: "queue after progress updates")
        XCTAssertTrue(element("audioQueueItem-\(fixture.audioTrackID)").label.contains(fixture.audioTrackTitle))
        XCTAssertTrue(element("audioQueueItem-\(fixture.audioSecondTrackID)").label.contains(fixture.audioSecondTrackTitle))
        try activate(element("audioQueueDoneButton"))
        try attachFrame(named: "Album queue advanced to second track")
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 15), disappears(element("nowPlayingBar"), timeout: 15) else {
            throw ProofFailure.ui("Explicit Stop did not dismiss playback and clear the persistent now-playing bar.")
        }
        try waitForExistence(element("musicCollectionScreen"), timeout: 15, named: "album after stopping queue")
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.audioSecondTrackID, minimum: 3)

        completed = true
    }

    func journeyMusicPlaylistsSmartSelectionAndDownloadedPlayback() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let regularName = "Native proof saved queue"
        let smartName = "Native proof Ambient selection"
        try await api.removeProofPlaylists(identity: identity, names: [regularName, smartName])
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        try activate(element("musicLibrary-downloads"))
        let clearDownloads = application!.buttons.matching(NSPredicate(format: "label == %@", "Remove all downloads")).firstMatch
        try waitForExistence(clearDownloads, timeout: 15, named: "download cleanup action")
        if clearDownloads.exists && clearDownloads.isEnabled {
            try revealMusicControl(clearDownloads)
            try activate(clearDownloads)
            try activate(menuAction("Remove downloads"))
        }
        try waitForExistence(application!.staticTexts["Save tracks, albums, or your listening queue from their playback controls."],
                             timeout: 15, named: "empty downloads before the playlist journey")
        XCTAssertFalse(element("playDownloadsButton").exists)
        try musicBack()
        try activate(element("musicLibrary-playlists"))
        let sharedID = try XCTUnwrap(fixture.sharedMusicPlaylistID, "Regenerate the enriched music fixture first")
        try activate(element("musicPlaylist-\(sharedID)"))
        try waitForExistence(element("musicPlaylistDetailScreen"), timeout: 20, named: "shared read-only playlist")
        XCTAssertFalse(element("editMusicPlaylistButton").exists)
        try activate(menuAction("Refresh playlist"))
        try waitForExistence(element("playlistTrack-\(fixture.audioTrackID)"), timeout: 20, named: "shared playlist's visible first track")
        XCTAssertFalse(element("editMusicPlaylistButton").exists)
        try attachFrame(named: "Shared household playlist is playable and read only")
        try activate(element("playlistTrack-\(fixture.audioTrackID)"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "shared playlist playback")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 3 }
        try activate(element("audioPlayPauseButton"))
        try closePlayer()
        try musicBack()
        try revealMusicControl(menuAction("Save listening queue as a playlist"))
        try activate(menuAction("Save listening queue as a playlist"))
        try fillMusicField("musicPlaylistNameField", text: regularName)
        let savePlaylistButton = application!.navigationBars["New playlist"].buttons
            .matching(NSPredicate(format: "label == %@", "Save")).firstMatch
        try revealMusicControl(savePlaylistButton)
        try activate(savePlaylistButton)
        guard waitUntil(timeout: 20, condition: { !self.element("musicPlaylistNameField").exists }) else {
            throw ProofFailure.ui("Saving the regular playlist did not dismiss its editor")
        }
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "saved playlist list")
        let regular = try await api.proofPlaylist(identity: identity, name: regularName)
        XCTAssertEqual(regular["count"] as? Int, 2)
        let regularID = try XCTUnwrap(regular["id"] as? String)
        try waitForExistence(element("musicPlaylist-\(regularID)"), timeout: 15, named: "new regular playlist")
        try activate(element("createMusicPlaylistButton"))
        try fillMusicField("musicPlaylistNameField", text: smartName)
        try setNativeSwitch("musicPlaylistSmartToggle", to: true)
        try fillMusicField("musicPlaylistGenresField", text: "Ambient")
        try revealMusicControl(savePlaylistButton)
        try activate(savePlaylistButton)
        guard waitUntil(timeout: 20, condition: { !self.element("musicPlaylistNameField").exists }) else {
            throw ProofFailure.ui("Saving the smart playlist did not dismiss its editor")
        }
        try waitForExistence(element("musicLibraryScreen"), timeout: 20, named: "smart playlist saved")
        let smart = try await api.proofPlaylist(identity: identity, name: smartName)
        XCTAssertEqual((smart["rules"] as? [String: Any])?["genres"] as? [String], ["Ambient"])
        let smartID = try XCTUnwrap(smart["id"] as? String)
        try activate(element("musicPlaylist-\(smartID)"))
        try waitForExistence(element("playlistTrack-\(fixture.audioTrackID)"), timeout: 20, named: "smart selection first track")
        try waitForExistence(element("playlistTrack-\(fixture.audioSecondTrackID)"), timeout: 20, named: "smart selection second track")
        try attachFrame(named: "Smart playlist resolves the imported Ambient album")
        try revealMusicControl(element("downloadAudioButton"))
        try activate(element("downloadAudioButton"))
        try waitForExistence(application!.staticTexts["2 tracks available offline."], timeout: 60, named: "both completed native downloads")
        try musicBack()
        try activate(element("musicLibrarySectionPicker"))
        try activate(menuAction("Downloads"))
        try waitForExistence(element("downloadedTrack-\(fixture.audioTrackID)"), timeout: 20, named: "downloaded first track")
        try waitForExistence(element("downloadedTrack-\(fixture.audioSecondTrackID)"), timeout: 20, named: "downloaded second track")
        try activate(element("playDownloadsButton"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "local downloaded playback")
        try waitForExistence(element("audioPlaybackMethod"), timeout: 15, named: "actual local playback method")
        XCTAssertEqual(element("audioPlaybackMethod").label, "Downloaded audio")
        let initial = try waitForPlaybackValue(element("playbackTime"), timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= initial + 3 }
        try attachFrame(named: "Downloaded music plays from the local file with an advancing clock")
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        if clearDownloads.exists && clearDownloads.isEnabled {
            try revealMusicControl(clearDownloads)
            try activate(clearDownloads)
            try activate(menuAction("Remove downloads"))
        }
        try waitForExistence(application!.staticTexts["Save tracks, albums, or your listening queue from their playback controls."],
                             timeout: 15, named: "empty downloads after cleanup")
        XCTAssertFalse(element("playDownloadsButton").exists)
        try await api.removeProofPlaylists(identity: identity, names: [regularName, smartName])
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
        try activate(element("audioItem-\(fixture.audioAlbumID)"))
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "album queue before restoration")
        try activate(menuAction("Play album"))
        try waitForExistence(element("audioPlayerStage"), timeout: 30, named: "album's advanced player")
        _ = try waitForPlaybackValue(element("playbackTime"), timeout: 20) { $0 >= 4 }
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
        try activate(element("audioLyrics"))
        XCTAssertTrue(waitUntil(timeout: 5, condition: {
            let header = self.element("audioLyrics")
            return header.exists && header.value as? String == "Expanded"
        }), "Lyrics must expand before its content is queried")
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
        XCTAssertEqual(element("nowPlayingTitle").label, fixture.audioTrackTitle)
        // Pause is a user action; relaunch must restore that intention without starting audio.
        application!.terminate()
        application!.launch()
        try waitForExistence(element("nowPlayingBar"), timeout: 40, named: "paused queue restored after app relaunch")
        try activate(element("nowPlayingBar"))
        try waitForExistence(element("audioPlayerStage"), timeout: 20, named: "restored audio stage")
        XCTAssertEqual(element("audioPlayPauseButton").label, "Play")
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
        guard waitUntil(timeout: 20, condition: { self.element("nowPlayingTitle").label == fixture.audioSecondTrackTitle }) else {
            throw ProofFailure.ui("Choosing the server queue did not install its selected track")
        }
        XCTAssertEqual(element("audioPlayPauseButton").label, "Play")
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
        try activate(element("audioNextButton"))
        guard waitUntil(timeout: 20, condition: { self.element("nowPlayingTitle").label == fixture.audioTrackTitle }) else {
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
        XCTAssertEqual(element("nowPlayingTitle").label, fixture.audioTrackTitle)
        try activate(element("audioQueueDoneButton"))
        try revealMusicControl(element("audioStopButton"))
        try activate(element("audioStopButton"))
        guard disappears(element("playbackScreen"), timeout: 20) else {
            throw ProofFailure.ui("Stop did not dismiss the restored player")
        }
        // Relaunch restored the queue on Home, without restoring a navigation stack.
        try selectDestination("Audio", screen: "audioScreen")
        try activate(element("audioItem-\(fixture.audioAlbumID)"))
        try waitForExistence(element("musicCollectionScreen"), timeout: 20, named: "fresh album in the same app process")
        try activate(menuAction("Play album"))
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

    private func revealMusicControl(_ control: XCUIElement) throws {
        #if os(tvOS)
        try waitForExistence(control, timeout: 15, named: "music control \(control.identifier)")
        guard focus(control, timeout: 15) else {
            throw ProofFailure.ui("The Siri Remote could not focus the music control \(control.identifier.isEmpty ? control.label : control.identifier).")
        }
        #else
        for _ in 0..<8 {
            if control.exists && control.isHittable { return }
            let viewport = try applicationViewport()
            if control.exists && !control.frame.isEmpty && control.frame.midY < viewport.midY {
                application!.swipeDown()
            } else { application!.swipeUp() }
        }
        try waitForHittable(control, timeout: 10, named: "requested music control")
        #endif
    }

    private func fillMusicField(_ identifier: String, text: String) throws {
        let field = element(identifier)
        #if os(tvOS)
        try enter(text, into: field)
        #else
        try revealMusicControl(field)
        field.tap()
        field.typeText(text)
        field.typeText("\n")
        #endif
    }

    private func musicBack() throws {
        #if os(tvOS)
        XCUIRemote.shared.press(.menu)
        #else
        try activate(application!.buttons.matching(identifier: "BackButton").firstMatch)
        #endif
    }

    func journeyShowSeasonEpisodeBrowsingAndPlayback() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.episodeID, seconds: 0)
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Video", screen: "libraryScreen")
        try activate(element("videoKindPicker"))
        try activate(menuAction("Shows"))
        let show = element("libraryItem-\(fixture.showID)")
        try waitForExistence(show, timeout: 25, named: "imported synthetic show")
        XCTAssertTrue(show.label.contains(fixture.showTitle))
        try activate(show)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "show seasons")
        #if os(tvOS)
        let season = element("videoCollectionItem-\(fixture.seasonID)")
        #else
        let season = element("videoSeason-\(fixture.seasonID)")
        #endif
        try activate(season)
        #if os(tvOS)
        let episode = element("videoCollectionItem-\(fixture.episodeID)")
        let second = element("videoCollectionItem-\(fixture.secondEpisodeID)")
        #else
        let episode = element("videoEpisode-\(fixture.episodeID)")
        let second = element("videoEpisode-\(fixture.secondEpisodeID)")
        #endif
        try waitForExistence(episode, timeout: 20, named: "first season episode")
        try waitForExistence(second, timeout: 20, named: "second season episode")
        XCTAssertTrue(episode.label.contains(fixture.episodeTitle))
        try attachFrame(named: "Synthetic show season and episode list")
        try activate(episode)
        try waitForExistence(element("detailScreen"), timeout: 20, named: "episode detail")
        let time = try startPlayback(using: "playButton")
        let initial = try waitForPlaybackValue(time, timeout: 15) { _ in true }
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 4 }
        try closePlayer()
        _ = try await waitForServerProgress(api, identity: identity, itemID: fixture.episodeID, minimum: 2)
    }

    func journeyVideoWatchedFavoriteAndActivityFilters() async throws {
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.setItemState(identity: identity, itemID: fixture.directItemID, played: false, favorite: false)
        try await api.setItemState(identity: identity, itemID: fixture.convertedItemID, played: false, favorite: false)
        do {
            try launchAndResetSession()
            try signIn(using: fixture)
            try openLibraryItem(id: fixture.directItemID)
            let watched = element("videoWatchedButton")
            let favorite = element("videoFavoriteButton")
            #if os(iOS)
            try revealDetailControl(watched, named: "mark watched")
            #endif
            try activate(watched)
            guard waitUntil(timeout: 15, condition: { watched.value as? String == "Watched" }) else {
                throw ProofFailure.ui("Mark watched did not update the detail action.")
            }
            #if os(iOS)
            try revealDetailControl(favorite, named: "add favorite")
            #endif
            try activate(favorite)
            guard waitUntil(timeout: 15, condition: { favorite.value as? String == "Favorite" }) else {
                throw ProofFailure.ui("Add favorite did not update the detail action.")
            }
            let saved = try await api.itemState(identity: identity, itemID: fixture.directItemID)
            XCTAssertTrue(saved.played)
            XCTAssertTrue(saved.isFavorite)
            try returnToLibrary()
            try applyActivityFilters(favorites: true, unwatched: false)
            try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 20, named: "favorite movie in filtered catalog")
            guard disappears(element("libraryItem-\(fixture.convertedItemID)"), timeout: 15) else {
                throw ProofFailure.ui("Favorites only retained a non-favorite movie.")
            }
            try applyActivityFilters(favorites: true, unwatched: true)
            guard disappears(element("libraryItem-\(fixture.directItemID)"), timeout: 20) else {
                throw ProofFailure.ui("Unwatched only retained the watched favorite movie.")
            }
            try attachFrame(named: "Watched favorite excluded by Unwatched filter")
            try applyActivityFilters(favorites: true, unwatched: false)
            try openLibraryItem(id: fixture.directItemID)
            #if os(iOS)
            try revealDetailControl(watched, named: "mark unwatched")
            #endif
            try activate(watched)
            guard waitUntil(timeout: 15, condition: { watched.value as? String == "Unwatched" }) else {
                throw ProofFailure.ui("Mark unwatched did not restore the detail state.")
            }
            #if os(iOS)
            try revealDetailControl(favorite, named: "remove favorite")
            #endif
            try activate(favorite)
            guard waitUntil(timeout: 15, condition: { favorite.value as? String == "Not a favorite" }) else {
                throw ProofFailure.ui("Remove favorite did not restore the detail state.")
            }
            let reset = try await api.itemState(identity: identity, itemID: fixture.directItemID)
            XCTAssertFalse(reset.played)
            XCTAssertFalse(reset.isFavorite)
            try returnToLibrary()
            try applyActivityFilters(favorites: true, unwatched: false)
            guard disappears(element("libraryItem-\(fixture.directItemID)"), timeout: 20) else {
                throw ProofFailure.ui("Favorites only retained the movie after removing its favorite state.")
            }
            try applyActivityFilters(favorites: false, unwatched: false)
            try waitForExistence(element("libraryItem-\(fixture.directItemID)"), timeout: 20, named: "movie after clearing activity filters")
        } catch {
            try? await api.setItemState(identity: identity, itemID: fixture.directItemID, played: false, favorite: false)
            throw error
        }
        try await api.setItemState(identity: identity, itemID: fixture.directItemID, played: false, favorite: false)
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

    func journeyCurrentDuoLayoutKeepsControlsAndPlaybackReachable() async throws {
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
        _ = try resumeNativePlayer(pausedAt: stillPaused)

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

    func journeyCompatibleHLSReplayRequestsFreshSession() async throws {
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
        if let cachedFixture { return cachedFixture }
        let bundle = Bundle(for: Self.self)
        if let config = bundle.url(forResource: "FixtureBroker", withExtension: "json") {
            let settings = try JSONDecoder().decode([String: String].self, from: Data(contentsOf: config))
            // The tier runner owns an unhinged iPad; legacy Duo fixture runs retain Fill.
            expectedBookFill = false
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
        let destinations = ["homeScreen", "libraryScreen", "audioScreen", "accountScreen"]
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
        // Exercise the keyboard's Go action, which stays reachable in Duo landscape.
        passwordField.typeText("\n")
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
        try waitForHittable(destinationControl("Account"), timeout: 10, named: "Account tab after declining password storage")
        #endif
    }

    private func applyActivityFilters(favorites: Bool, unwatched: Bool) throws {
        try activate(element("videoFiltersButton"))
        try setNativeSwitch("videoFavoritesToggle", to: favorites)
        try setNativeSwitch("videoUnwatchedToggle", to: unwatched)
        // Check both bindings immediately before Apply, including retained sheet state.
        XCTAssertEqual(nativeSwitchValue(element("videoFavoritesToggle")), favorites)
        XCTAssertEqual(nativeSwitchValue(element("videoUnwatchedToggle")), unwatched)
        try activate(element("videoApplyFiltersButton"))
        guard disappears(element("videoApplyFiltersButton"), timeout: 10) else {
            throw ProofFailure.ui("Applying activity filters did not dismiss the filter sheet.")
        }
        let summary = [unwatched ? "Unwatched" : nil, favorites ? "Favorites" : nil].compactMap { $0 }.joined(separator: " · ")
        guard waitUntil(timeout: 10, condition: {
            let clear = self.element("videoClearFiltersButton")
            if summary.isEmpty {
                return self.hasVisibleFrame(self.element("videoFiltersButton")) && !clear.exists
                    && !self.application!.staticTexts["Favorites"].exists
                    && !self.application!.staticTexts["Unwatched"].exists
                    && !self.application!.staticTexts["Unwatched · Favorites"].exists
            }
            return self.hasVisibleFrame(self.application!.staticTexts[summary]) && self.hasVisibleFrame(clear)
        }) else {
            try attachFrame(named: "Activity-filter summary mismatch")
            throw ProofFailure.ui("Applied activity filters did not expose the expected summary: \(summary.isEmpty ? "No filters" : summary).")
        }
    }

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

    private func destinationControl(_ title: String) -> XCUIElement {
        let identifier = "\(title.lowercased())Tab"
        #if os(iOS)
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
        #else
        return element(identifier)
        #endif
    }

    private func signOutThroughAccount() throws {
        #if os(iOS)
        try selectDestination("Account", screen: "accountScreen")
        let signOut = element("signOutButton")
        #else
        try activate(element("accountMenuButton"))
        let signOut = menuAction("Sign out")
        #endif
        try waitForExistence(signOut, timeout: 10, named: "account sign-out action")
        try activate(signOut)
    }

    private func selectDestination(_ title: String, screen: String) throws {
        if hasVisibleFrame(element(screen)) { return }
        try activate(destinationControl(title))
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the visible \(title) destination.")
        }
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
    private func assertLibraryCardsStayInsideTheVisibleGrid(using fixture: ProofFixture) async throws {
        try selectDestination("Video", screen: "libraryScreen")
        let direct = try revealLibraryItem(id: fixture.directItemID, named: "long-title direct library card")
        let converted = try revealLibraryItem(id: fixture.convertedItemID, named: "landscape-artwork library card")
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
        let library = element("homeScreen").exists ? element("homeScreen") : element("libraryScreen")
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
        try selectDestination("Video", screen: "libraryScreen")
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
        guard isFullyVerticallyContained(control.frame, in: viewport),
              hasVisibleFrame(control), control.isEnabled, control.isHittable else {
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
            if hasRequiredVisibility, element.isEnabled,
               (!requiresHittable || (hasVisibleFrame(element) && element.isHittable)) {
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
        let player = element("nativeVideoPlayer")
        try waitForExistence(player, timeout: 10, named: "native player after returning from Home")
        player.tap()
        let play = player.descendants(matching: .button).matching(
            NSPredicate(format: "label == %@ OR label == %@", "Play", "Resume")
        ).firstMatch
        let deadline = Date().addingTimeInterval(15)
        var activatedPlay = false
        while Date() < deadline {
            // AVKit replaces accessibility nodes when its controls appear. Re-query
            // the clock, and observe playback while Play is still visible.
            let clock = element("playbackTime")
            if clock.exists, let value = Self.numericAccessibilityValue(of: clock), value >= pausedAt + 1 {
                return value
            }
            if !activatedPlay, play.exists, play.isHittable {
                try activate(play)
                activatedPlay = true
            }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        throw ProofFailure.timeout("The native player's clock did not advance after resume.")
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
        guard waitUntil(timeout: 20, condition: {
            self.hasVisibleFrame(self.element("homeScreen")) || self.hasVisibleFrame(self.element("libraryScreen"))
        }) else {
            throw ProofFailure.ui("Back did not return to the originating Home or Video destination.")
        }
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
        try activate(element("accountMenuButton"))
        try activate(menuAction("Refresh library"))
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
        guard focus(control, timeout: 20) else {
            let current = focusedElement()
            throw ProofFailure.ui("The tvOS remote could not focus \(control.identifier) at \(control.frame); focused=\(current?.identifier ?? "none") at \(current?.frame ?? .zero).")
        }
        XCUIRemote.shared.press(.select)
        #else
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
        // Native picker menus expose focusable cells with Other labels. Scope to
        // the focused popup so the picker's underlying value cannot win firstMatch.
        let popupCells = application!.collectionViews
            .containing(NSPredicate(format: "hasFocus == true"))
            .cells.containing(NSPredicate(
                format: "elementType == %d AND label == %@",
                XCUIElement.ElementType.other.rawValue, title
            ))
        if popupCells.count == 1 { return popupCells.firstMatch }
        #elseif os(iOS)
        let button = application!.buttons.matching(NSPredicate(format: "label == %@", title)).firstMatch
        if button.exists { return button }
        #endif
        return application!.descendants(matching: .any).matching(NSPredicate(format: "label == %@", title)).firstMatch
    }

    #if os(tvOS)
    private func focus(_ target: XCUIElement, timeout: TimeInterval) -> Bool {
        guard let initialTarget = try? target.snapshot() else { return false }
        let targetIdentifier = initialTarget.identifier
        let targetLabel = initialTarget.label
        let musicIdentifiers = ["createMusicPlaylistButton", "saveMusicPlaylistButton", "editMusicPlaylistButton",
                                "downloadAudioButton", "playDownloadsButton", "audioListeningOptions", "audioLyrics",
                                "audioNormalizeToggle", "audioCrossfadePicker", "audioSleepTimerButton",
                                "audioUseServerButton", "audioKeepDeviceButton", "audioPlayPauseButton",
                                "audioRepeatButton", "audioStopButton"]
        let musicLabels = ["Refresh playlist", "Remove all downloads", "Remove downloads",
                                "Save listening queue as a playlist", "Downloads", "3 seconds", "15 minutes", "Play album"]
        let musicTarget = targetIdentifier.hasPrefix("musicLibrary") || targetIdentifier.hasPrefix("musicPlaylist")
            || targetIdentifier.hasPrefix("playlistTrack-") || targetIdentifier.hasPrefix("downloadedTrack-")
            || musicIdentifiers.contains(targetIdentifier)
            || targetIdentifier.isEmpty && musicLabels.contains(targetLabel)
        let videoContainerTarget = ["videoKindPicker", "videoFavoritesToggle", "videoUnwatchedToggle"].contains(targetIdentifier)
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
    let episodeID: String
    let episodeTitle: String
    let secondEpisodeID: String
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

    func seedBookProgress(identity: APIIdentity, bookID: String) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("Lumina/Books/\(bookID)/state"))
        request.httpMethod = "PATCH"
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["unit_index": 0, "offset": 0, "completed": false])
        _ = try await send(request)
    }

    func setItemState(identity: APIIdentity, itemID: String, played: Bool, favorite: Bool) async throws {
        var request = URLRequest(url: origin.appendingPathComponent("Users/\(identity.userID)/Items/\(itemID)/UserData"))
        request.httpMethod = "POST"
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["Played": played, "IsFavorite": favorite])
        _ = try await send(request)
    }

    func itemState(identity: APIIdentity, itemID: String) async throws -> APIUserData {
        var request = URLRequest(url: origin.appendingPathComponent("Users/\(identity.userID)/Items/\(itemID)/UserData"))
        request.setValue(identity.token, forHTTPHeaderField: "X-Emby-Token")
        return try JSONDecoder().decode(APIUserData.self, from: await send(request))
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

    @MainActor func proofPlaylist(identity: APIIdentity, name: String) async throws -> [String: Any] {
        let list = try await musicJSON(identity: identity, path: "playlists") as? [[String: Any]]
        guard let value = list?.first(where: { $0["name"] as? String == name }) else { throw ProofAPIError.malformedResponse }
        return value
    }

    @MainActor func removeProofPlaylists(identity: APIIdentity, names: [String]) async throws {
        let list = try await musicJSON(identity: identity, path: "playlists") as? [[String: Any]] ?? []
        for entry in list where names.contains(entry["name"] as? String ?? "") {
            guard let id = entry["id"] as? String else { throw ProofAPIError.malformedResponse }
            _ = try await musicJSON(identity: identity, path: "playlists/" + id, method: "DELETE")
        }
    }

    @MainActor func replaceProofMusicSession(identity: APIIdentity, itemIDs: [String], position: Int) async throws {
        guard let current = try await musicJSON(identity: identity, path: "session") as? [String: Any],
              let revision = current["revision"] as? Int else { throw ProofAPIError.malformedResponse }
        _ = try await musicJSON(identity: identity, path: "session", method: "PUT", body: [
            "base_revision": revision, "item_ids": itemIDs, "current_index": 0, "position_seconds": position,
            "repeat": "off", "shuffle": false, "active_output": ["kind": "browser"],
        ])
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
