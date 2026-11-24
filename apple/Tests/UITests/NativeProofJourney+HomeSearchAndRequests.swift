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
    #if os(iOS)
    func journeyCategoryOverflowWithReducedMotion(reducedMotion: Bool = true) throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let app = XCUIApplication()
        application = app
        app.launchArguments = ["--ui-testing", "--ui-testing-category-motion-proof"]
            + (reducedMotion ? ["--ui-testing-reduce-motion"] : [])
        app.launchEnvironment["LUMINA_UI_TEST_NAMESPACE"] = isolationID
        app.launch()
        let categories = element("motionProofCategories")
        try waitForExistence(categories, timeout: 15, named: "Unified category rail")
        let forward = element("categoryScrollForward")
        try waitForHittable(forward, timeout: 10, named: "More categories")
        try activate(forward)
        let back = element("categoryScrollBack")
        try waitForHittable(back, timeout: 10, named: "Previous categories after scrolling")
        let diagnostics = try XCTUnwrap(categories.value as? String)
        XCTAssertTrue(diagnostics.contains("scrollAnimated=\(!reducedMotion)"), "Category scrolling must honor Reduce Motion: \(diagnostics)")
        try activate(app.buttons["Account"])
        XCTAssertEqual(element("motionProofSelection").label, "Selected: Account")
        try attachFrame(named: "Category navigation reaches trailing categories — Reduce Motion \(reducedMotion)")
        try activate(back)
        try waitForHittable(forward, timeout: 10, named: "More categories after returning")
        completed = true
    }

    func journeyHomeHeroArtworkSwipesAndPlaysSelectedTitle(checksLayout: Bool = false, reducedMotion: Bool = false, motionOnly: Bool = false) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        try await api.seedProgress(identity: identity, itemID: fixture.directItemID, seconds: 12)
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-manual-hero-selection"]
            + (motionOnly || reducedMotion ? ["--ui-testing-static-hero-artwork"] : [])
            + (reducedMotion ? ["--ui-testing-reduce-motion"] : []))
        try signIn(using: fixture, waitsForAccount: !checksLayout && !reducedMotion && !motionOnly)
        if !checksLayout && !reducedMotion && !motionOnly {
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
        try waitForExistence(title, timeout: 15, named: "Home hero title")
        if title.label != fixture.directItemTitle {
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
        let shortLeft = right.withOffset(CGVector(dx: -36, dy: 0))
        right.press(forDuration: 0.05, thenDragTo: shortLeft, withVelocity: XCUIGestureVelocity(rawValue: 40), thenHoldForDuration: 0.3)
        XCTAssertEqual(title.label, fixture.directItemTitle, "A short slow drag must not advance the hero.")
        right.press(forDuration: 0.05, thenDragTo: right.withOffset(CGVector(dx: 0, dy: 36)), withVelocity: .fast, thenHoldForDuration: 0)
        XCTAssertEqual(title.label, fixture.directItemTitle, "Vertical scrolling must not advance the hero.")
        right.press(forDuration: 0.05, thenDragTo: shortLeft, withVelocity: .fast, thenHoldForDuration: 0)
        XCTAssertTrue(waitUntil(timeout: 3) { title.label != fixture.directItemTitle && !title.label.isEmpty },
                      "A short fast flick must advance the hero.")
        XCTAssertFalse(element("detailScreen").exists, "Flicking artwork must not open details.")
        try attachFrame(named: "Short fast flick advances the hero")
        shortLeft.press(forDuration: 0.05, thenDragTo: right, withVelocity: .fast, thenHoldForDuration: 0)
        XCTAssertTrue(waitUntil(timeout: 3) { title.label == fixture.directItemTitle }, "The reverse flick must restore the hero.")
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
        if reducedMotion || motionOnly {
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
        let categories = element("homeMasthead")
        XCTAssertFalse(element("homeBrowsingRow").exists)
        XCTAssertFalse(element("homeBrowse-movies").exists)
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
        XCTAssertFalse(element("homeBrowse-movies").exists)
        XCTAssertFalse(element("homeBrowse-watchlist").exists)
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

        for (id, screen) in [("movies", "libraryScreen"), ("tv", "libraryScreen"), ("music", "audioScreen"), ("books", "booksScreen")] {
            try selectTourLibraryCategory(id, screen: screen)
            try waitForExistence(element(screen), timeout: 20, named: "Library \(id) destination")
            try selectDestination("Home", screen: "homeScreen")
            XCTAssertFalse(element("homeBrowsingRow").exists)
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

    #if os(tvOS)
    func journeyTVRequestQuotaRetry() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard fixture.requestQuotaOutageAvailable == true else {
            throw XCTSkip("Generate a NativeTVRequestQuotaRetryTests fixture.")
        }
        let showKey = try XCTUnwrap(fixture.requestUnavailableShowKey)
        let showTitle = try XCTUnwrap(fixture.requestUnavailableShowTitle)
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = try await api.requestRecords(identity: identity)
        XCTAssertFalse(before.contains { $0.key == showKey }, "The request target must start unrequested.")

        try launchAndResetSession()
        try signIn(using: fixture)
        _ = try await api.setRequestQuotaOutage(true)
        try selectDestination("Requests", screen: "requestsScreen")
        let search = try openTVRequestsSearch()
        try enter(showTitle, into: search)
        let poster = try revealRequestPoster(showKey, named: "quota retry show poster")
        try activate(poster)

        let retry = element("requestRetryQuotaButton")
        let confirm = element("requestConfirmButton")
        try waitForExistence(retry, timeout: 20, named: "quota retry after a failed allowance check")
        try waitForExistence(confirm, timeout: 10, named: "disabled request confirmation")
        XCTAssertFalse(confirm.isEnabled, "A failed allowance check must disable submission.")
        guard focus(retry, timeout: 20), hasVisibleFrame(retry) else {
            throw ProofFailure.ui("The remote could not focus the visible quota retry button.")
        }
        XCTAssertFalse(confirm.hasFocus, "The disabled request must be skipped by remote focus.")
        try attachFrame(named: "Quota check failed with Try again focused")

        let outage = try await api.setRequestQuotaOutage(false)
        XCTAssertGreaterThan(try XCTUnwrap(outage["failed_checks"]), 0)
        XCTAssertEqual(outage["submit_attempts"], 0, "No request POST is allowed during the quota outage.")
        XCTAssertFalse(confirm.isEnabled, "Restoring the server must not enable submission before a successful check.")
        XCUIRemote.shared.press(.select)
        guard disappears(retry, timeout: 10), waitUntil(timeout: 20, condition: { confirm.isEnabled }) else {
            throw ProofFailure.ui("Remote quota retry did not enable the request.")
        }
        try activate(confirm)
        let submitted = try await waitForNativeRequest(api, identity: identity, key: showKey)
        XCTAssertFalse(before.contains { $0.id == submitted.id })
        let recovered = try await api.setRequestQuotaOutage(false)
        XCTAssertEqual(recovered["submit_attempts"], 1, "Retry should permit exactly one new request POST.")
        try waitForExistence(element("requestSubmittedStatus"), timeout: 15, named: "submitted request status")
        try attachFrame(named: "Request submitted after remote quota retry")
        completed = true
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
        XCTAssertFalse(element("requestsSearchField").exists, "Requests should open on Discover.")
        XCTAssertTrue(element("requestsMyListButton").exists)
        try attachFrame(named: "Requests Discover with Search and My Requests")
        _ = try openTVRequestsSearch()
        XCUIRemote.shared.press(.menu)
        try waitForExistence(movie, timeout: 15, named: "Discover restored by Back from Requests Search")
        let search = try openTVRequestsSearch()
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
        #if os(tvOS)
        try waitForExistence(element("requestConfirmButton"), timeout: 10, named: "request confirmation")
        #else
        try revealDetailElement(
            element("requestConfirmButton"),
            in: element("requestDetailScreen"),
            named: "request confirmation",
            requiresHittable: true
        )
        try waitForHittable(element("requestConfirmButton"), timeout: 10, named: "request confirmation")
        #endif
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

    func journeyServerNavigation() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        try await api.setNavigationFixture("off")
        try launchAndResetSession()
        try signIn(using: fixture)
        try waitForExistence(element("homeScreen"), timeout: 25, named: "Home")
        #if os(iOS)
        XCTAssertFalse(application!.tabBars.buttons["Requests"].exists)
        #else
        XCTAssertFalse(element("requestsButton").exists)
        #endif
        XCTAssertEqual(application!.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "homeExternal-")).count, 0)
        try await api.setNavigationFixture("on")
        #if os(iOS)
        try backgroundApplication()
        #else
        XCUIRemote.shared.press(.home)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.application!.state == .runningBackground || self.application!.state == .runningBackgroundSuspended
        }, "Home must background the app before returning.")
        #endif
        application!.activate()
        #if os(iOS)
        try waitForExistence(application!.tabBars.buttons["Requests"], timeout: 30, named: "enabled Requests")
        #else
        try waitForExistence(element("requestsButton"), timeout: 30, named: "enabled Requests")
        XCTAssertEqual(element("requestsButton").label, "Requests",
                       "Enabled Requests must be presented as a labeled primary tab.")
        try selectDestination("Requests", screen: "requestsScreen")
        try activate(element("requestsMyListButton"))
        try waitForExistence(element("requestHistoryScreen"), timeout: 20, named: "TV My Requests")
        XCUIRemote.shared.press(.menu)
        try waitForExistence(element("requestsScreen"), timeout: 15, named: "Requests after My Requests Back")
        #endif
        try selectDestination("Audio", screen: "audioScreen")
        try await api.setNavigationFixture("remove-music")
        #if os(iOS)
        try backgroundApplication()
        #else
        XCUIRemote.shared.press(.home)
        XCTAssertTrue(waitUntil(timeout: 10) {
            self.application!.state == .runningBackground || self.application!.state == .runningBackgroundSuspended
        }, "Home must background the app before returning.")
        #endif
        application!.activate()
        #if os(tvOS)
        try waitForExistence(element("libraryScreen"), timeout: 30, named: "Movie catalog after music removal")
        XCTAssertFalse(element("homeBrowse-music").exists)
        #else
        XCTAssertTrue(waitUntil(timeout: 30) { !self.element("audioScreen").exists }, "A removed selected category must leave its old library.")
        #endif
        try await api.setNavigationFixture("on")
        try attachFrame(named: "Server-driven navigation after category removal")
        completed = true
    }

    func journeyServerMediaLanguage() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let animeKey = try XCTUnwrap(fixture.requestAnimeKey)
        let animeTitle = try XCTUnwrap(fixture.requestAnimeTitle)
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = Set(try await api.requestRecords(identity: identity).map(\.id))
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Requests", screen: "requestsScreen")
        #if os(tvOS)
        let search = try openTVRequestsSearch()
        try enter(animeTitle, into: search)
        #else
        try activate(element("requestsSearchButton"))
        let search = application!.searchFields.firstMatch
        try waitForHittable(search, timeout: 10, named: "Requests search")
        try activate(search)
        search.typeText(animeTitle)
        #endif
        let anime = try revealRequestPoster(animeKey, named: "available anime")
        try activate(anime)
        try waitForExistence(element("requestDetailScreen"), timeout: 20, named: "anime detail")
        let evidence = element("requestLanguageAvailability")
        #if os(iOS)
        try revealDetailElement(evidence, in: element("requestDetailScreen"), named: "track availability", requiresHittable: false)
        #else
        try waitForExistence(evidence, timeout: 15, named: "track availability")
        #endif
        XCTAssertTrue(application!.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "Japanese", "English")).firstMatch.exists,
                      "One file must show its Japanese audio and English subtitles together.")
        XCTAssertTrue(element("requestViewVaultButton").exists)
        #if os(tvOS)
        let dub = element("requestLanguagePicker").buttons.matching(NSPredicate(format: "label == %@", "English dub")).firstMatch
        try activate(dub)
        #else
        try revealDetailElement(element("requestLanguagePicker"), in: element("requestDetailScreen"), named: "anime preset", requiresHittable: true)
        try activate(element("requestLanguagePicker"))
        try activate(menuAction("English dub"))
        try revealDetailElement(element("requestConfirmButton"), in: element("requestDetailScreen"), named: "request confirmation", requiresHittable: true)
        #endif
        try activate(element("requestConfirmButton"))
        let submitted = try await waitForNativeRequest(api, identity: identity, key: animeKey, excluding: before)
        XCTAssertEqual(submitted.language, "dub")
        XCTAssertEqual(submitted.audioLanguage, "en")
        try attachFrame(named: "Anime language evidence and English request")
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
        let search = try openTVRequestsSearch()
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

    #if os(iOS)
    func journeyNativeRequestsCatalogEfficiency() async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        guard let movieKey = fixture.requestMovieKey,
              let showKey = fixture.requestShowKey,
              let showTitle = fixture.requestShowTitle else {
            throw XCTSkip("Generate the Requests catalog proof fixture.")
        }
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)

        try launchAndResetSession()
        try signIn(using: fixture)
        if usesStandardPhonePushedChrome {
            let requestsTab = application!.tabBars.buttons["Requests"]
            try waitForExistence(requestsTab, timeout: 15, named: "Requests tab")
            requestsTab.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        } else {
            try selectDestination("Requests", screen: "requestsScreen")
        }
        try waitForExistence(element("requestsScreen"), timeout: 25, named: "Requests catalog")
        try waitForExistence(element("requestsFeaturedHero"), timeout: 30, named: "Requests featured hero")
        try waitForExistence(application!.buttons["Watch trailer on YouTube"], timeout: 15, named: "featured trailer")

        func tapVisible(_ control: XCUIElement, named name: String) throws {
            try waitForExistence(control, timeout: 15, named: name)
            guard waitUntil(timeout: 10, condition: { self.hasVisibleFrame(control) }) else {
                throw ProofFailure.timeout("Timed out waiting for visible \(name).")
            }
            control.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        func selectKind(_ title: String, named name: String) throws {
            try tapVisible(element("requestsOptionsMenu"), named: "Requests options")
            try tapVisible(application!.buttons[title], named: name)
        }

        try await api.resetRequestsMetrics()
        try selectKind("Films", named: "Requests Films filter")
        try waitForExistence(element("requestPoster-\(movieKey)"), timeout: 20, named: "Requests Films result")
        try await Task.sleep(for: .milliseconds(500))
        let movies = try await waitForRequestsMetrics(api) {
            $0.genresMovie == 1 && $0.genresShow == 0 && $0.details == 0
        }
        XCTAssertEqual(movies.genresAnime, 0)

        try selectKind("Series", named: "Requests Series filter")
        try waitForExistence(element("requestPoster-\(showKey)"), timeout: 20, named: "Requests Series result")
        try await Task.sleep(for: .milliseconds(500))
        let shows = try await waitForRequestsMetrics(api) {
            $0.genresMovie == 1 && $0.genresShow == 1 && $0.details == 0
        }
        XCTAssertEqual(shows.genresAnime, 0)

        try selectKind("Films", named: "Requests cached Films filter")
        try waitForExistence(element("requestPoster-\(movieKey)"), timeout: 20, named: "Requests cached Films result")
        try await Task.sleep(for: .milliseconds(500))
        let cachedMovies = try await api.requestsMetrics()
        XCTAssertEqual(cachedMovies.genresMovie, 1, "Returning to Films must reuse the current Requests screen's genres.")
        XCTAssertEqual(cachedMovies.genresShow, 1)
        XCTAssertEqual(cachedMovies.details, 0, "Browse results must not fetch an invisible hero's details.")

        try scrollLibrary(element("requestsScreen"), up: false)
        let refreshedMovies = try await waitForRequestsMetrics(api) {
            $0.genresMovie == 2 && $0.genresShow == 1 && $0.details == 0
        }
        XCTAssertEqual(refreshedMovies.genresAnime, 0,
                       "Pull-to-refresh must revalidate only the current Requests genre list.")

        try await api.resetRequestsMetrics()
        try selectKind("Everything", named: "Requests Everything filter")
        try waitForExistence(element("requestsFeaturedHero"), timeout: 20, named: "restored Requests hero")
        try waitForExistence(application!.buttons["Watch trailer on YouTube"], timeout: 15, named: "restored featured trailer")
        let restoredHero = try await waitForRequestsMetrics(api) { $0.details == 1 }
        XCTAssertEqual(restoredHero.genresMovie, 0)
        XCTAssertEqual(restoredHero.genresShow, 0)

        try await api.resetRequestsMetrics()
        try tapVisible(element("requestsSearchButton"), named: "Requests search")
        let search = application!.searchFields.firstMatch
        try tapVisible(search, named: "Requests efficiency search")
        search.typeText(showTitle)
        try waitForExistence(element("requestPoster-\(showKey)"), timeout: 20, named: "Requests search result")
        try await Task.sleep(for: .milliseconds(500))
        let searched = try await api.requestsMetrics()
        XCTAssertEqual(searched.details, 0,
                       "Search results must not fetch an invisible hero's details.")
        completed = true
    }

    private func waitForRequestsMetrics(_ api: ProofAPI, timeout: TimeInterval = 10,
                                        where condition: (RequestsCatalogMetrics) -> Bool) async throws -> RequestsCatalogMetrics {
        let deadline = Date().addingTimeInterval(timeout)
        var latest = try await api.requestsMetrics()
        while Date() < deadline {
            if condition(latest) { return latest }
            try await Task.sleep(for: .milliseconds(200))
            latest = try await api.requestsMetrics()
        }
        throw ProofFailure.timeout("Requests efficiency metrics did not settle: \(latest)")
    }
    #endif


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
}
