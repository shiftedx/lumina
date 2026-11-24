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
        #if os(tvOS)
        try selectDeviceSettingsCategory("library")
        XCTAssertTrue(element("deviceSettingsCategory-library").hasFocus)
        XCUIRemote.shared.press(.right)
        XCTAssertTrue(waitUntil(timeout: 5) { self.element("deviceDefaultLibraryPicker").hasFocus },
                      "Right from Library must focus the Default library row.")
        XCUIRemote.shared.press(.select)
        try activate(element("deviceLibraryOption-tv"))
        try assertDeviceSetting("deviceDefaultLibraryPicker", contains: "Shows")
        try selectDeviceSettingsCategory("playback")
        XCTAssertTrue(focus(element("devicePlaybackQualityPicker"), timeout: 10))
        XCTAssertEqual(element("deviceSettingsCategory-playback").value as? String, "Selected")
        try attachFrame(named: "Device settings focused row and selected Playback category")
        #endif
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
    #if os(iOS)
    func journeyMusicLoadsNextPageWhileScrolling() throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let lastAlbumID = try XCTUnwrap(fixture.audioPaginationAlbumID,
                                       "The music scrolling fixture must exceed the 50-album page.")
        try launchAndResetSession()
        try signIn(using: fixture)
        try selectDestination("Audio", screen: "audioScreen")
        let library = element("audioScreen")
        let lastAlbum = element("audioItem-\(lastAlbumID)")
        try waitForExistence(element("audioItem-\(fixture.audioAlbumID)"), timeout: 20, named: "first album page")
        for _ in 0..<24 where !hasVisibleFrame(lastAlbum) {
            library.swipeUp(velocity: .fast)
        }
        try waitForHittable(lastAlbum, timeout: 15, named: "album beyond the first 50 results")
        XCTAssertFalse(element("audioLoadMoreButton").exists,
                       "Scrolling must load the final music page without pressing Load more.")
        try attachFrame(named: "Music automatically loads albums beyond the first page")
        try activate(lastAlbum)
        try waitForExistence(element("musicCollectionScreen"), timeout: 15, named: "automatically loaded album")
        completed = true
    }
    #endif

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
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-ios-lifecycle"])
        try signIn(using: fixture)
        guard UIDevice.current.userInterfaceIdiom != .pad else {
            throw XCTSkip("iPad uses its unified category navigation instead of the native Library tab.")
        }

        try selectTourVideoCategory("movies")
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
        let lifecycle = element("iosLifecycleProbe")
        try waitForExistence(lifecycle, timeout: 10, named: "iOS lifecycle probe")
        let beforePressure = accessibilityText(lifecycle)
        try activate(lifecycle)
        XCTAssertTrue(waitUntil(timeout: 10) { self.accessibilityText(lifecycle) != beforePressure },
                      "A system memory warning must reclaim mounted artwork without changing its URL")
        try waitForExistence(element("detailScreen"), timeout: 10, named: "detail retained after memory pressure")
        try attachFrame(named: "Movie detail before Library tab return")

        let viewport = try applicationViewport()
        let libraryTab = try XCTUnwrap(application!.buttons.matching(NSPredicate(format: "label == %@", "Library"))
            .allElementsBoundByIndex.first { self.hasVisibleFrame($0) && $0.frame.midY > viewport.midY },
            "The visible Library tab must remain available from detail")
        libraryTab.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Movies Library after tab return")
        let returnedMovie = element(movieIdentifier)
        try waitForExistence(returnedMovie, timeout: 15, named: "returned deeply scrolled Movies fixture")
        XCTAssertFalse(hasVisibleFrame(element("detailScreen")),
                       "Selecting Library from a movie detail must return to the catalog root, not leave the detail active.")
        XCTAssertEqual(returnedMovie.frame.midY, movieRootFrame.midY, accuracy: 8,
                       "Library tab return must retain the visible Movies catalog position.")
        try waitForPreparedLibraryArtwork()
        try attachFrame(named: "Movies Library root after tab return")
        let beforeInterruption = accessibilityText(lifecycle)
        let window = try applicationWindow()
        window.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.01))
            .press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.72)))
        XCTAssertTrue(waitUntil(timeout: 10) { self.accessibilityText(lifecycle).contains("phase=inactive") },
                      "Control Center must make the scene inactive before dismissing it")
        let springBoard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        springBoard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.95))
            .press(forDuration: 0.05, thenDragTo: springBoard.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)))
        XCTAssertTrue(waitUntil(timeout: 15) { self.accessibilityText(lifecycle) == beforeInterruption },
                      "A brief interruption must not start another refresh or reclaim artwork")

        // Re-enter the same category through the visible Library chooser (or
        // Home's category row on wider phones). The retained Movies root and
        // its visible item must remain usable.
        try selectTourVideoCategory("movies")
        let reenteredMovie = element(movieIdentifier)
        try waitForExistence(reenteredMovie, timeout: 15, named: "re-entered deeply scrolled Movies fixture")
        XCTAssertEqual(reenteredMovie.frame.midY, movieRootFrame.midY, accuracy: 8,
                       "Reselecting a category must not reconstruct the retained Movies root.")
        try waitForPreparedLibraryArtwork()
        try attachFrame(named: "Movies Library retained through category selection")

        try selectTourVideoCategory("tv")
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
        let beforeBackground = accessibilityText(lifecycle)
        try backgroundApplication()
        application!.activate()
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "detail retained after background")
        XCTAssertTrue(waitUntil(timeout: 15) {
            let value = self.accessibilityText(lifecycle)
            return value.contains("phase=active") && value != beforeBackground && value.contains("refreshes=1")
        }, "A real background return must refresh once and reclaim hidden artwork")
        try attachFrame(named: "TV show detail before Library tab return")

        libraryTab.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
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

    func selectLibraryCategory(_ category: String, screen: String) throws {
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
            let title = category == "tv" ? "Shows" : category.capitalized
            if let menu = visibleMenu,
               !waitUntil(timeout: 3, condition: { self.hasVisibleFrame(identified) || self.hasVisibleFrame(self.menuAction(title)) }) {
                // The native toolbar can settle after the tab's first visible frame.
                // Retry the public menu once before declaring its category absent.
                menu.tap()
            }
            let choice = identified.exists ? identified : menuAction(title)
            guard waitUntil(timeout: 15, condition: { self.hasVisibleFrame(choice) }) else {
                throw ProofFailure.timeout("The Library \(title) menu choice did not become visible.")
            }
            choice.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        } else {
            try selectTourLibraryCategory(category, screen: screen)
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

    func openDeviceSettings() throws {
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

    func chooseDeviceSetting(_ identifier: String, option: String) throws {
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

    func activateDeviceControl(_ identifier: String) throws {
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
    func devicePickerOption(_ title: String) throws -> XCUIElement {
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
    func selectDeviceSettingsCategory(_ category: String) throws {
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

    func assertDeviceSetting(_ identifier: String, contains expected: String) throws {
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
        try selectTourAudioRoot()
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
        try selectTourVideoCategory("tv")
        let show = element("libraryItem-\(fixture.showID)")
        try waitForHittable(show, timeout: 20, named: "TV show from Home Browse")
        try activate(show)
        try waitForExistence(element("videoCollectionScreen"), timeout: 20, named: "TV show collection")
        #if os(iOS)
        try performStandardPhoneLeadingEdgeBack(from: element("videoCollectionScreen"), to: element("libraryScreen"),
                                                 named: "TV show collection")
        #endif

        try selectDestination("Home", screen: "homeScreen")
        try selectTourVideoCategory("tv")
        try waitForExistence(element("libraryScreen"), timeout: 20, named: "TV library root after Home selection")
        try waitForHittable(show, timeout: 20, named: "TV show at its library root")
        XCTAssertFalse(element("videoCollectionScreen").exists,
                       "Choosing TV from Home must not retain the previous show collection.")
        XCTAssertFalse(element("detailScreen").exists,
                       "Choosing TV from Home must not retain a previous media detail.")

        try selectDestination("Home", screen: "homeScreen")
        try selectTourVideoCategory("movies")
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
    func journeyClosedDisplayCategoryWidth(checksStatusTap: Bool = false) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        try launchAndResetSession()
        try signIn(using: fixture)
        let viewport = try applicationViewport()
        guard viewport.width >= 450, viewport.width < 500, viewport.height > viewport.width else {
            throw XCTSkip("This layout contract targets the closed Duo portrait display.")
        }
        XCTAssertFalse(element("homeBrowsingRow").exists)
        try assertClosedNavigationClearsCamera([element("homeMasthead"), element("compactBrowseMenu")])
        let heroArtwork = application!.descendants(matching: .any).matching(identifier: "homeHeroArtworkBounds").firstMatch
        try waitForExistence(heroArtwork, timeout: 15, named: "Closed Duo hero artwork bounds")
        XCTAssertLessThanOrEqual(heroArtwork.frame.minY, viewport.minY + 2)
        XCTAssertGreaterThanOrEqual(heroArtwork.frame.maxY, viewport.maxY - 0.5)
        try attachFrame(named: "Closed Duo Home without category tabs")
        if checksStatusTap {
            let window = try applicationWindow()
            let content = window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.75))
            content.press(forDuration: 0.1,
                          thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.25)))
            XCTAssertTrue(waitUntil(timeout: 5) { !heroArtwork.exists || heroArtwork.frame.minY < viewport.minY - 60 },
                          "Home must scroll away from the hero before testing the status tap.")
            window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.015)).tap()
            let reachesTop = waitUntil(timeout: 8) {
                heroArtwork.exists && abs(heroArtwork.frame.minY - viewport.minY) <= 2
                    && heroArtwork.frame.maxY >= viewport.maxY - 0.5
            }
            XCTAssertTrue(reachesTop, "After the status tap, Home artwork must cover the top edge. Artwork: \(heroArtwork.exists ? heroArtwork.frame : .null).")
            try attachFrame(named: "Closed Duo Home after status tap")
            XCTAssertEqual(try homeRefreshCount(), 0, "The status tap must not refresh Home.")
            window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.25))
                .press(forDuration: 0.1,
                       thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.35, dy: 0.8)),
                       withVelocity: .slow, thenHoldForDuration: 0.2)
            XCTAssertTrue(waitUntil(timeout: 5) { (try? self.homeRefreshCount()) == 1 },
                          "An intentional pull from the top must still refresh Home.")
            completed = true
            return
        }
        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        let row = element("libraryCategories")
        let third = element("libraryCategory-music")
        try waitForHittable(third, timeout: 15, named: "third Library category without swiping")
        try assertClosedDuoLibraryHeader()
        let libraryWidth = row.frame.width
        try assertClosedNavigationClearsCamera([element("homeMasthead"), row])
        try activate(third)
        try waitForExistence(element("audioScreen"), timeout: 20, named: "Music from Library categories")
        try activate(revealMusicAlbum(fixture, named: "closed navigation album"))
        try startMusicCollectionPlayback()
        try activate(element("audioPlayPauseButton"))
        try closePlayer()
        try selectDestination("Home", screen: "homeScreen")
        XCTAssertFalse(element("homeBrowsingRow").exists)
        try assertClosedNavigationClearsCamera([element("homeMasthead"), element("compactBrowseMenu"),
                                               element("nowPlayingBar"), element("nowPlayingPauseButton")])
        try attachFrame(named: "Closed Home keeps Now Playing without category tabs")
        try selectTourLibraryCategory("movies", screen: "libraryScreen")
        try assertClosedDuoLibraryHeader()
        XCTAssertEqual(element("libraryCategories").frame.width, libraryWidth, accuracy: 1)
        try assertClosedNavigationClearsCamera([element("homeMasthead"), element("libraryCategories"),
                                               element("nowPlayingBar"), element("nowPlayingPauseButton")])
        try attachFrame(named: "Closed Library categories retain width with Now Playing")
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
        try openMobileHomeBrowseMenu()
        try tapVisibleControl(element("homeMenu-watchlist"))
        let title = application!.staticTexts["No matches on Home"]
        let message = application!.staticTexts["Choose Home or add something to your watchlist."]
        try waitForExistence(title, timeout: 20, named: "empty Watchlist title")
        try waitForExistence(message, timeout: 10, named: "empty Watchlist guidance")
        let categories = element("homeMasthead").frame
        let group = title.frame.union(message.frame)
        XCTAssertGreaterThan(group.minY, categories.maxY + 80,
                             "The empty Watchlist must clear Home navigation.")
        XCTAssertEqual(group.midY, viewport.midY, accuracy: viewport.height * 0.15,
                       "Empty Watchlist guidance belongs in the middle of the page.")
        try attachFrame(named: "Open Duo portrait empty Watchlist centered below navigation")

        try await api.setWatchlistIncluded(identity: identity, itemID: fixture.directItemID, included: true)
        application!.terminate()
        application!.launch()
        try waitForExistence(element("homeScreen"), timeout: 25, named: "Home after saved Watchlist relaunch")
        try openMobileHomeBrowseMenu()
        try tapVisibleControl(element("homeMenu-watchlist"))
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
        let first = categoryID == "homeBrowsingRow" ? mark : element(firstCategory)
        let search = element(searchID)
        let artwork = element("nowPlayingBar")
        let toggle = element("nowPlayingPauseButton")
        if categoryID != "homeBrowsingRow" { try waitForExistence(categories, timeout: 15, named: "portrait category row") }
        else { XCTAssertFalse(categories.exists) }
        for (control, name) in [(mark, "logo"), (first, "first category"), (search, "Search"),
                                (artwork, "Now Playing artwork"), (toggle, "play/pause")] {
            try waitForHittable(control, timeout: 15, named: "portrait navigation \(name)")
        }
        guard mark.frame.width <= 60, mark.frame.minY <= 32, abs(first.frame.midY - mark.frame.midY) <= 6,
              (categoryID == "homeBrowsingRow" || categories.frame.maxX <= search.frame.minX - 8) else {
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
        let mark = element("homeMasthead")
        let menu = element("homeBrowseMenu")
        try waitForExistence(mark, timeout: 15, named: "Home mark in \(posture)")
        try waitForExistence(menu, timeout: 15, named: "Home Browse menu in \(posture)")
        XCTAssertFalse(element("homeBrowsingRow").exists, "Home categories belong in Library in \(posture).")
        XCTAssertFalse(element("homeBrowse-movies").exists)
        let viewport = try applicationViewport()
        XCTAssertTrue(hasVisibleFrame(mark))
        XCTAssertTrue(hasVisibleFrame(menu))
        XCTAssertLessThanOrEqual(mark.frame.width, 60)
        XCTAssertGreaterThanOrEqual(menu.frame.minY, 72, "Browse stays below the system status corner.")
        XCTAssertLessThanOrEqual(menu.frame.maxX, viewport.maxX - 12)
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

    func homeRefreshCount() throws -> Int {
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

    func revealMenuControl(_ category: XCUIElement, in menu: XCUIElement) {
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


    func heroPixels() throws -> Data {
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

}
