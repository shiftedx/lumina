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
    func waitForPostLoginControls(waitsForAccount: Bool) throws {
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

    func nativeSwitchValue(_ control: XCUIElement) -> Bool? {
        if let value = control.value as? NSNumber { return value.boolValue }
        switch (control.value as? String)?.lowercased() {
        case "1", "on", "true": return true
        case "0", "off", "false": return false
        default: return nil
        }
    }

    func setNativeSwitch(_ identifier: String, to enabled: Bool) throws {
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

    var featuredTitleElement: XCUIElement { element("featuredTitleButton") }

    #if os(tvOS)
    func selectTVFeaturedTitle(named expectedTitle: String) throws {
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

    func destinationControl(_ requestedTitle: String) -> XCUIElement {
        #if os(tvOS)
        let identifier = switch requestedTitle {
        case "Home": "homeNavigationButton"
        case "Library": "libraryNavigationButton"
        case "Video": "homeBrowse-movies"
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

    func signOutThroughAccount() throws {
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

    func selectDestination(_ title: String, screen: String) throws {
        #if os(tvOS)
        switch title {
        case "Home":
            if hasVisibleFrame(element(screen)) { return }
            try activate(element("homeNavigationButton"))
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
        if UIDevice.current.userInterfaceIdiom == .pad, hasVisibleFrame(element("homeBrowseMenu")), title == "Library" || title == "Account" {
            try openMobileHomeBrowseMenu()
            try activate(element("homeMenu-\(title.lowercased())"))
            try waitForExistence(element(screen), timeout: 25, named: "\(title) from Home Browse")
            return
        }
        if UIDevice.current.userInterfaceIdiom == .pad, hasVisibleFrame(element("iPadUnifiedCategories")), title == "Home" || title == "Account" {
            let control = element(title == "Home" ? "iPadCategory-all" : "iPadCategory-account")
            revealMenuControl(control, in: element("iPadUnifiedCategories"))
            guard hasVisibleFrame(control) else { throw ProofFailure.ui("The iPad destination must be visible before selection.") }
            control.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            try waitForExistence(element(screen), timeout: 25, named: "\(title) from Library navigation")
            return
        }
        // iPhone groups media kinds under Library rather than giving Audio and
        // Video independent tabs. Keep legacy proof journeys on the real
        // category rail instead of searching for a nonexistent tab.
        switch title {
        case "Requests":
            if UIDevice.current.userInterfaceIdiom == .pad {
                try selectDestination("Account", screen: "accountScreen")
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
        #if os(iOS)
        let viewport = try applicationViewport()
        if UIDevice.current.userInterfaceIdiom == .phone, viewport.width >= 450 {
            let control = destinationControl(title)
            try waitForExistence(control, timeout: 15, named: "\(title) navigation")
            guard hasVisibleFrame(control), control.isEnabled else { throw ProofFailure.ui("The destination must be visibly available.") }
            let window = try applicationWindow()
            window.coordinate(withNormalizedOffset: CGVector(dx: (control.frame.midX - viewport.minX) / viewport.width,
                                                             dy: (control.frame.midY - viewport.minY) / viewport.height)).tap()
        } else { try activate(destinationControl(title)) }
        #else
        try activate(destinationControl(title))
        #endif
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the visible \(title) destination.")
        }
    }

    #if os(tvOS)
    func selectTVLibraryCategory(_ category: String, screen: String) throws {
        let categories = element("libraryCategoriesScreen")
        if !hasVisibleFrame(categories) {
            try activate(element("libraryNavigationButton"))
            try waitForExistence(categories, timeout: 15, named: "TV Library categories")
        }
        let identifier = category == "savedStreaming" ? "savedStreaming" : category
        let control = element("homeBrowse-\(identifier)")
        try waitForExistence(control, timeout: 15, named: "TV \(category) category")
        try activate(control)
        guard waitUntil(timeout: 25, condition: { self.hasVisibleFrame(self.element(screen)) }) else {
            throw ProofFailure.timeout("Timed out waiting for the TV Library \(category) category.")
        }
    }
    #endif

    /// The performance tour must start each iOS Library category at the shell
    /// root.  The production shell intentionally keeps its own NavigationStack
    /// per tab, so a plain tab selection doesn't pop a prior episode or album.
    func selectTourVideoCategory(_ category: String) throws {
        #if os(iOS)
        try selectTourLibraryCategory(category, screen: "libraryScreen")
        #else
        try selectTVLibraryCategory(category, screen: "libraryScreen")
        #endif
    }

    func selectTourAudioRoot() throws {
        #if os(iOS)
        try selectTourLibraryCategory("music", screen: "audioScreen")
        #else
        try selectDestination("Audio", screen: "audioScreen")
        #endif
    }

    #if os(iOS)
    func selectTourBooksRoot() throws {
        try selectTourLibraryCategory("books", screen: "booksScreen")
    }

    func selectTourLibraryCategory(_ category: String, screen: String) throws {
        if usesStandardPhonePushedChrome {
            try selectLibraryCategory(category, screen: screen)
            return
        }
        if UIDevice.current.userInterfaceIdiom == .pad, hasVisibleFrame(element("homeBrowseMenu")) {
            try selectDestination("Library", screen: "libraryScreen")
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

    func openTourVideoItem(id: String) throws {
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

    func openLibraryItem(id: String) throws {
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
    func revealInLibraryViewport(_ elements: [XCUIElement], in container: XCUIElement? = nil) throws {
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
    func revealInLibraryViewport(_ elements: [XCUIElement], in container: XCUIElement? = nil) throws {
        for element in elements {
            try waitForHittable(element, timeout: 15, named: element.identifier)
        }
    }
    #endif

    #if os(iOS)
    func isFullyVerticallyContained(_ frame: CGRect, in viewport: CGRect) -> Bool {
        let tolerance: CGFloat = 1
        return !frame.isEmpty
            && frame.minY >= viewport.minY - tolerance
            && frame.maxY <= viewport.maxY + tolerance
    }

    func revealLibraryItem(id: String, named name: String) throws -> XCUIElement {
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

    func scrollLibrary(_ library: XCUIElement, up: Bool) throws {
        // A deliberate vertical drag lets the outer scroll view win direction
        // locking even when it begins over the horizontal Continue Watching rail.
        let viewport = library.frame.intersection(try applicationViewport()).insetBy(dx: 0, dy: 2)
        let anchor = library.buttons.firstMatch
        let anchorX = anchor.exists && !anchor.frame.isEmpty ? anchor.frame.midX : viewport.midX
        try dragVertically(in: viewport, anchorX: anchorX, up: up)
    }

    func dragVertically(in viewport: CGRect, anchorX: CGFloat, up: Bool) throws {
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

    func pauseAndResumeNativePlayer(after directAdvanced: Double) async throws {
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

    func backgroundApplication() throws {
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

    func startPlayback(using buttonIdentifier: String, freezeInitialTransport: Bool = false) throws -> XCUIElement {
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
    func revealDetailControl(_ control: XCUIElement, named name: String) throws {
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

    func revealConnectionField(_ field: XCUIElement) throws {
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

    #if os(iOS)
    func openMobileHomeBrowseMenu() throws {
        let menu = element("homeBrowseMenu").exists ? element("homeBrowseMenu") : element("compactBrowseMenu")
        try waitForExistence(menu, timeout: 15, named: "Home Browse menu")
        guard hasVisibleFrame(menu), menu.isEnabled else { throw ProofFailure.ui("Home Browse must be visibly available.") }
        // Floating SwiftUI header controls can report unhittable despite a visible, tappable frame.
        let center = menu.frame
        let window = try applicationWindow()
        let viewport = window.frame
        window.coordinate(withNormalizedOffset: CGVector(dx: (center.midX - viewport.minX) / viewport.width,
                                                         dy: (center.midY - viewport.minY) / viewport.height)).tap()
        guard waitUntil(timeout: 10, condition: {
            self.element("homeMenu-watchlist").exists || self.element("compactCategory-watchlist").exists
        }) else { throw ProofFailure.ui("Browse must actually reveal Watchlist before continuing.") }
    }
    #endif

    func journeyMobileHomeLibraryNavigation() throws {
        #if os(iOS)
        var completed = false
        defer { captureScreenshot(succeeded: completed) }
        let fixture = try loadFixture()
        try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-animations"])
        try signIn(using: fixture, waitsForAccount: false)
        try waitForExistence(element("featuredPlayButton"), timeout: 30, named: "Home hero")
        for id in ["homeBrowsingRow", "iPadUnifiedCategories", "homeBrowse-movies", "homeBrowse-tv", "homeBrowse-music", "homeBrowse-books"] {
            XCTAssertFalse(element(id).exists, "Home must leave categories in Library: \(id)")
        }
        try attachFrame(named: "Home hero without category tabs")
        try openMobileHomeBrowseMenu()
        let watchlist = element("homeMenu-watchlist").exists ? element("homeMenu-watchlist") : element("compactCategory-watchlist")
        try activate(watchlist)
        try waitForExistence(element("homeScreen"), timeout: 20, named: "Watchlist remains reachable")
        try selectDestination("Library", screen: "libraryScreen")
        let categories = element("iPadUnifiedCategories").exists ? element("iPadUnifiedCategories") : element("libraryCategories")
        try waitForExistence(categories, timeout: 20, named: "dynamic Library categories")
        let movies = element("iPadCategory-movies").exists ? element("iPadCategory-movies") : element("libraryCategory-movies")
        try waitForExistence(movies, timeout: 15, named: "Movies in Library")
        XCTAssertTrue(hasVisibleFrame(movies))
        try attachFrame(named: "Library dynamic category navigation")
        try selectTourLibraryCategory("music", screen: "audioScreen")
        try selectTourLibraryCategory("books", screen: "booksScreen")
        try selectDestination("Home", screen: "homeScreen")
        XCTAssertFalse(element("homeBrowsingRow").exists)
        XCTAssertFalse(element("iPadUnifiedCategories").exists)
        completed = true
        #endif
    }

    func journeyHomeHeaderScrolling(checksRefresh: Bool = false) throws {
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
        let homeHeader = element("homeNavigationButton")
        #else
        guard UIDevice.current.userInterfaceIdiom == .pad else { throw XCTSkip("This workflow targets iPad navigation.") }
        let homeMark = element("homeMasthead")
        let homeHeader = homeMark
        #endif
        try waitForExistence(homeHeader, timeout: 20, named: "Home navigation at the top")
        XCTAssertTrue(hasVisibleFrame(homeHeader))
        try attachFrame(named: "Home navigation over bright hero artwork")
        #if os(tvOS)
        let resume = element("resumeItem-\(fixture.directItemID)")
        for _ in 0..<12 where !hasVisibleFrame(resume) { XCUIRemote.shared.press(.down) }
        XCTAssertTrue(focus(resume, timeout: 20), "The remote must reach a shelf below the Home hero.")
        for _ in 0..<6 where hasVisibleFrame(homeHeader) { XCUIRemote.shared.press(.down) }
        #else
        home.swipeUp()
        home.swipeUp()
        #endif
        XCTAssertTrue(waitUntil(timeout: 10) { !self.hasVisibleFrame(homeHeader) },
                      "Home navigation must scroll away rather than cover the shelves.")
        try attachFrame(named: "Home shelves without an overlapping navigation header")
        #if os(tvOS)
        for _ in 0..<12 where !hasVisibleFrame(homeHeader) { XCUIRemote.shared.press(.up) }
        try activate(homeHeader)
        #else
        for _ in 0..<8 where !hasVisibleFrame(homeHeader) { home.swipeDown() }
        if checksRefresh {
            XCTAssertEqual(try homeRefreshCount(), 0, "Scrolling back to the hero must not refresh Home.")
            let window = application!.windows.firstMatch
            window.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.25))
                .press(forDuration: 0.1,
                       thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.4, dy: 0.8)),
                       withVelocity: .slow, thenHoldForDuration: 0.2)
            XCTAssertTrue(waitUntil(timeout: 5) { (try? self.homeRefreshCount()) == 1 },
                          "An intentional pull from the top must still refresh Home.")
            completed = true
            return
        }
        try selectDestination("Library", screen: "libraryScreen")
        #endif
        let library = element("libraryScreen")
        try waitForExistence(library, timeout: 25, named: "Movies Library")
        #if os(iOS)
        let movies = element("iPadCategory-movies")
        #else
        let movies = homeHeader
        #endif
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

    func closePlayer() throws {
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

    func stopAudioPlayer() throws {
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
    func closePlayerAfterPictureInPictureReturn() throws {
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

    func relaunchAuthenticatedSession() throws {
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

    func returnToLibrary() throws {
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
    func returnTourToLibrary() throws {
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

    func refreshLibrary(waitingForResumeOf itemID: String? = nil) throws {
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

    func enter(_ text: String, into field: XCUIElement) throws {
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

    func activate(_ control: XCUIElement) throws {
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

    /// Streaming's underline tabs can expose a correct visible accessibility
    /// frame while XCTest still reports them non-hittable inside the scroll
    /// container. Keep the coordinate fallback scoped to this proof surface.
    func activateStreamingTab(_ identifier: String) throws {
        #if os(tvOS)
        try activate(element(identifier))
        #else
        try activateVisibleStreamingControl(identifier, named: "Streaming tab")
        #endif
    }

    /// Cards share the same scroll-container activation defect as its tabs;
    /// constrain the fallback to the two Streaming card identifier families.
    func activateStreamingCard(_ identifier: String) throws {
        #if os(tvOS)
        try activate(element(identifier))
        #else
        guard identifier.hasPrefix("streamingRemote-") || identifier.hasPrefix("streamingSaved-") else {
            throw ProofFailure.ui("Streaming card fallback was asked to activate an unrelated control.")
        }
        try activateVisibleStreamingControl(identifier, named: "Streaming card")
        #endif
    }

    #if os(iOS)
    private func activateVisibleStreamingControl(_ identifier: String, named: String) throws {
        let buttons = application!.buttons.matching(identifier: identifier).allElementsBoundByIndex
        var visibleTab: XCUIElement?
        guard waitUntil(timeout: 15, condition: {
            guard let candidate = buttons.first(where: {
                self.hasVisibleFrame($0) && $0.isEnabled
            }) else { return false }
            visibleTab = candidate
            return true
        }), let visibleTab else {
            throw ProofFailure.timeout("Timed out waiting for visible \(named) \(identifier).")
        }
        if visibleTab.isHittable {
            visibleTab.tap()
            return
        }
        guard let window = visibleApplicationWindow else {
            throw ProofFailure.ui("\(named) \(identifier) is visible but has no active window for a physical tap.")
        }
        let viewport = window.frame
        let frame = visibleTab.frame
        guard viewport.contains(frame) else {
            throw ProofFailure.ui("\(named) \(identifier) is not fully inside the active viewport.")
        }
        window.coordinate(withNormalizedOffset: CGVector(
            dx: (frame.midX - viewport.minX) / viewport.width,
            dy: (frame.midY - viewport.minY) / viewport.height
        )).tap()
    }
    #endif

    #if os(iOS)
    /// The player deliberately hides its chrome during active playback. Waiting for
    /// that public hidden state makes a single physical surface tap unambiguously
    /// reveal it, without accessibility activation's toggle semantics or a stale
    /// pre-check crossing the auto-hide deadline.
    func tapVideoTransportAfterChromeHides(named name: String) throws {
        XCTAssertTrue(disappears(element("videoPlaybackChrome"), timeout: 8),
                      "Playback chrome must auto-hide before using its public reveal surface.")
        let surface = try waitForHittable(element("videoControlsSurface"), timeout: 8, named: "video controls reveal surface")
        surface.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)).tap()
        let transport = try waitForHittable(element("videoPlayPauseButton"), timeout: 8, named: name)
        transport.tap()
    }
    #endif

    func waitForExistence(_ element: XCUIElement, timeout: TimeInterval, named name: String) throws {
        guard appears(element, timeout: timeout) else {
            throw ProofFailure.timeout("Timed out waiting for \(name).")
        }
    }

    func hasVisibleFrame(_ control: XCUIElement) -> Bool {
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

    var visibleApplicationWindow: XCUIElement? {
        application?.windows.allElementsBoundByIndex.filter { window in
            let frame = window.frame
            return !frame.isEmpty && [frame.minX, frame.minY, frame.width, frame.height].allSatisfy { $0.isFinite }
        }.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
    }

    @discardableResult
    func waitForHittable(_ control: XCUIElement, timeout: TimeInterval, named name: String) throws -> XCUIElement {
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

    func disappears(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
        return XCTWaiter().wait(for: [expectation], timeout: timeout) == .completed
    }

    func waitForPlaybackValue(
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
    func waitForVideoPlaybackDiagnostics(
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

    func accessibilityText(_ element: XCUIElement) -> String {
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

    func element(_ identifier: String) -> XCUIElement {
        application!.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    func menuAction(_ title: String) -> XCUIElement {
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
    func hasVisiblePIPControl(in pipView: XCUIElement) -> Bool {
        pipView.descendants(matching: .button).allElementsBoundByIndex.contains { control in
            hasVisibleFrame(control) && control.isHittable
        }
    }

    func attachPIPReturnObservation(named name: String,
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
    func attachSpringBoardPIPObservation(named name: String,
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

    func attachPIPReturnGuard(named name: String,
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

}
