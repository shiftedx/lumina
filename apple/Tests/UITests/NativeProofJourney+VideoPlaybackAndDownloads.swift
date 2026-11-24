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

    func journeyLibraryMetadata(refreshOnly: Bool = false) async throws {
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
        let library = application!.buttons["Library"]
        try waitForExistence(library, timeout: 15, named: "Library tab")
        library.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try waitForExistence(element("libraryScreen"), timeout: 15, named: "Library posters")
        let poster = try revealLibraryItem(id: fixture.directItemID, named: "metadata poster")
        if refreshOnly {
            try await Task.sleep(for: .milliseconds(500))
            try await api.resetMetadataRefreshMetrics()
        }
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
        if refreshOnly {
            let beforeSave = try await api.metadataRefreshMetrics()
            XCTAssertEqual(beforeSave.nextUp, 0,
                           "Opening and editing metadata must not refresh the Home catalog.")
        }
        try attachFrame(named: "Native metadata editor from poster long press")
        try activate(element("metadataEditorSave"))
        XCTAssertTrue(disappears(form, timeout: 10), "Saving valid metadata must return to the library.")
        let doc = try await api.metadataDocument(identity: identity, id: fixture.directItemID)
        let fields = try XCTUnwrap(doc["fields"] as? [String: [String: Any]])
        XCTAssertEqual(fields["name"]?["value"] as? String, correctedTitle)
        XCTAssertEqual(fields["year"]?["value"] as? Int, 2023)
        XCTAssertTrue(waitUntil(timeout: 12) { poster.exists && poster.label.contains(correctedTitle) },
                      "The saved title must replace the old poster label without restarting the app.")
        if refreshOnly {
            let refreshed = try await waitForMetadataRefreshMetrics(api) { $0.nextUp >= 1 }
            XCTAssertEqual(refreshed.nextUp, 1, "A saved metadata revision must refresh Home exactly once.")
            try await Task.sleep(for: .milliseconds(500))
            let settled = try await api.metadataRefreshMetrics()
            XCTAssertEqual(settled.nextUp, 1, "The completed revision must not refresh again while unchanged.")
            return
        }
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

    private func waitForMetadataRefreshMetrics(_ api: ProofAPI, timeout: TimeInterval = 12,
                                                where condition: (MetadataRefreshMetrics) -> Bool) async throws -> MetadataRefreshMetrics {
        let deadline = Date().addingTimeInterval(timeout)
        var latest = try await api.metadataRefreshMetrics()
        while Date() < deadline {
            if condition(latest) { return latest }
            try await Task.sleep(for: .milliseconds(200))
            latest = try await api.metadataRefreshMetrics()
        }
        throw ProofFailure.timeout("Metadata refresh metrics did not settle: \(latest)")
    }

    func journeyMetadataSheetAppearance(themes: [String]) async throws {
        var completed = false
        defer { if !completed { captureScreenshot(succeeded: false) } }
        let fixture = try loadFixture()
        let origin = try localOrigin(from: fixture.serverURL)
        let session = makeTestAPISession(origin: origin)
        defer { session.invalidateAndCancel() }
        let api = ProofAPI(origin: origin, session: session)
        let identity = try await api.authenticate(username: fixture.username, password: fixture.password)
        let before = try await api.metadataDocument(identity: identity, id: fixture.directItemID)
        let originalFields = try XCTUnwrap(before["fields"] as? [String: [String: Any]])
        let originalTitle = originalFields["name"]?["value"] as? String
        func tap(_ control: XCUIElement, named name: String) throws {
            try waitForExistence(control, timeout: 15, named: name)
            control.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        for (index, theme) in themes.enumerated() {
            _ = try await api.settingsJSON(identity: identity, method: "PUT", body: ["ui_prefs": ["theme": theme]])
            if index == 0 {
                try launchAndResetSession(extraLaunchArguments: ["--ui-testing-static-hero-artwork"])
                try signIn(using: fixture, waitsForAccount: false)
            } else {
                application!.terminate()
                application!.launch()
                try waitForExistence(element("homeScreen"), timeout: 30, named: "restored Home")
            }
            try selectTourLibraryCategory("movies", screen: "libraryScreen")
            try waitForExistence(element("libraryScreen"), timeout: 20, named: "Library before themed metadata")
            let poster = try revealLibraryItem(id: fixture.directItemID, named: "metadata appearance poster")
            poster.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(forDuration: 1.1)
            try tap(element("editMetadataAction"), named: "Edit metadata action")
            let form = element("metadataEditorForm")
            let title = element("metadataField-name")
            try waitForExistence(title, timeout: 15, named: "themed metadata title")
            XCTAssertFalse(element("metadataEditorSave").isEnabled, "An unchanged editor must not offer Save.")
            try attachFrame(named: "Metadata \(theme) appearance")
            try tap(title, named: "metadata title field")
            try waitForExistence(application!.keyboards.firstMatch, timeout: 10, named: "metadata keyboard")
            try attachFrame(named: "Metadata \(theme) focused title and keyboard")
            try tap(element("metadataEditorCancel"), named: "metadata Cancel")
            XCTAssertTrue(disappears(form, timeout: 10), "Cancel must dismiss the themed editor.")
            poster.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(forDuration: 1.1)
            try tap(element("fixMatchAction"), named: "Fix match action")
            try tap(element("metadataMatchQuery"), named: "Fix match search field")
            try waitForExistence(application!.keyboards.firstMatch, timeout: 10, named: "Fix match keyboard")
            try attachFrame(named: "Fix match \(theme) focused search and keyboard")
            try tap(element("metadataEditorCancel"), named: "Fix match Cancel")
            XCTAssertTrue(disappears(form, timeout: 10), "Cancel must dismiss Fix match.")
        }
        let after = try await api.metadataDocument(identity: identity, id: fixture.directItemID)
        let finalFields = try XCTUnwrap(after["fields"] as? [String: [String: Any]])
        XCTAssertEqual(finalFields["name"]?["value"] as? String, originalTitle,
                       "Appearance changes and cancelling either sheet must preserve metadata.")
        completed = true
    }

    private func replaceMetadataText(_ field: XCUIElement, with text: String) throws {
        let visible = try waitForHittable(field, timeout: 10, named: "metadata field")
        visible.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        if let current = visible.value as? String, !current.isEmpty, current != visible.placeholderValue {
            visible.press(forDuration: 1.1)
            let selectAll = menuAction("Select All")
            try waitForExistence(selectAll, timeout: 10, named: "Select All metadata text")
            selectAll.tap()
        }
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

    func journeyInitialVideoFormatRecovery(stopsAfterPlaybackStarts: Bool = false) async throws {
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
        if usesStandardPhonePushedChrome {
            let viewport = try standardPhoneNavigationViewport()
            XCTAssertTrue(isFullyVerticallyContained(play.frame, in: viewport),
                          "The initial detail Play action must clear the floating tab bar.")
            application!.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: play.frame.midX, dy: play.frame.midY)).tap()
        } else {
            try revealDetailElement(play, in: element("detailScreen"), named: "playButton", requiresHittable: false)
            play.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        let resumeChoice = element("resumePlaybackChoice")
        XCTAssertTrue(waitUntil(timeout: 15) { resumeChoice.exists || self.element("playbackScreen").exists },
                      "Detail Play must present playback or the resume choice.")
        if resumeChoice.exists {
            try activate(resumeChoice.buttons.matching(NSPredicate(format: "label == %@", "Resume")).firstMatch)
        }
        try waitForExistence(element("playbackScreen"), timeout: 35, named: "native playback screen")
        let time = element("playbackTime")
        try waitForExistence(time, timeout: 20, named: "playback time")
        #else
        let time = try startPlayback(using: "playButton")
        #endif
        let initial = try waitForPlaybackValue(time, timeout: 25) { $0 >= 10 }
        _ = try waitForPlaybackValue(time, timeout: 20) { $0 >= initial + 2 }

        if stopsAfterPlaybackStarts {
            completed = true
            return
        }

        #if os(tvOS)
        XCUIRemote.shared.press(.playPause)
        try waitForExistence(element("Media Controls"), timeout: 8, named: "paused recovered video transport")
        try openTVPlaybackSettings()
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
            try selectTourVideoCategory("movies")
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
        try selectTourVideoCategory("tv")
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
        try selectTourVideoCategory("tv")
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

}
