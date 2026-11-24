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
    #if os(tvOS)
    /// Library categories are pinned above the options row; continue downward
    /// through that hierarchy until the first matching poster owns focus.
    func openTVRequestsSearch() throws -> XCUIElement {
        let field = element("requestsSearchField")
        if !field.exists { try activate(element("requestsCatalogSearchButton")) }
        try waitForExistence(field, timeout: 10, named: "Requests search field")
        return field
    }

    func openTVLibraryBrowse() throws {
        let sheet = element("videoBrowseSheet")
        if sheet.exists { return }
        let browse = element("videoBrowseButton")
        try waitForExistence(browse, timeout: 15, named: "Library Browse action")
        try activate(browse)
        try waitForExistence(sheet, timeout: 15, named: "Library Browse sheet")
        try waitForExistence(element("videoAllTitlesButton"), timeout: 15, named: "Browse all titles choice")
    }

    func dismissTVLibraryBrowse() throws {
        let sheet = element("videoBrowseSheet")
        guard sheet.exists else { return }
        XCUIRemote.shared.press(.menu)
        guard disappears(sheet, timeout: 10) else {
            throw ProofFailure.ui("Menu must dismiss the Library Browse sheet before changing destinations.")
        }
        XCTAssertTrue(waitUntil(timeout: 5) { self.element("videoBrowseButton").hasFocus },
                      "Closing Browse must restore remote focus to its header button.")
    }

    func focusVideoGridItemFromCategories(_ item: XCUIElement, named name: String) throws {
        try waitForExistence(item, timeout: 25, named: name)
        XCTAssertTrue(focus(item, timeout: 20),
                      "Real remote navigation through Library categories and options must focus the \(name) card.")
    }

    func focus(_ target: XCUIElement, timeout: TimeInterval) -> Bool {
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
        let videoContainerTarget = ["videoKindPicker", "videoFavoritesToggle", "videoUnwatchedToggle", "videoWatchlistFilterToggle",
                                    "videoFitPicker", "videoQualityPicker", "videoSpeedPicker"].contains(targetIdentifier)
            || targetIdentifier.hasPrefix("videoSubtitleStyle-")
        let queueTarget = targetIdentifier.hasPrefix("audioQueueItem-") || targetIdentifier == "videoApplyFiltersButton"
        let videoSwitchTarget = ["videoFavoritesToggle", "videoUnwatchedToggle", "videoWatchlistFilterToggle"].contains(targetIdentifier)
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
            if ["videoCollectionFilterButton", "videoApplyFiltersButton", "videoCancelFiltersButton"].contains(targetIdentifier), let owner = focusedElement() {
                // tvOS Form focuses the navigation row around its labeled action.
                let actions = snapshotDescendants(owner).filter { $0.elementType == .button || $0.elementType == .link }
                if !actions.isEmpty && actions.allSatisfy({ $0.identifier == targetIdentifier }) { return true }
            }
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

    func focusedElement() -> XCUIElementSnapshot? {
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
