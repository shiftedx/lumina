import AVKit
import LuminaCore
import SwiftUI

/// AVKit owns remote gestures, scrubbing, transport focus, and control visibility.
struct TVNativeVideoPlayer: UIViewControllerRepresentable {
    let playback: PlaybackModel
    let videoGravity: AVLayerVideoGravity
    let onSettings: () -> Void
    let onDismiss: () -> Void
    let onMediaSelection: (AVMediaSelectionOption?, AVMediaSelectionGroup) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        controller.player = playback.player
        controller.delegate = context.coordinator
        controller.allowsPictureInPicturePlayback = false
        controller.appliesPreferredDisplayCriteriaAutomatically = true
        // Lumina's speed choices retain their capability checks and stream-change policy.
        controller.speeds = []
        context.coordinator.settingsAction = UIAction(
            title: "Playback settings", image: UIImage(systemName: "slider.horizontal.3"),
            identifier: UIAction.Identifier("videoSettingsButton")
        ) { [weak coordinator = context.coordinator] _ in
            coordinator?.parent.onSettings()
        }
        context.coordinator.nextEpisodeAction = UIAction(
            title: "Next episode", image: UIImage(systemName: "forward.end.fill"),
            identifier: UIAction.Identifier("nextEpisodeTransportButton")
        ) { [weak coordinator = context.coordinator] _ in
            guard let playback = coordinator?.parent.playback else { return }
            Task { await playback.playNextEpisode() }
        }
        updateUIViewController(controller, context: context)
        return controller
    }

    func updateUIViewController(_ controller: AVPlayerViewController, context: Context) {
        context.coordinator.parent = self
        if controller.videoGravity != videoGravity { controller.videoGravity = videoGravity }
        let requiresLinearPlayback = playback.isSwitchingVideoStream || playback.isSeekingChapter || playback.isReplaying
        if controller.requiresLinearPlayback != requiresLinearPlayback {
            controller.requiresLinearPlayback = requiresLinearPlayback
        }
        if context.coordinator.metadataItem !== playback.player.currentItem {
            context.coordinator.metadataItem = playback.player.currentItem
            let title = AVMutableMetadataItem()
            title.identifier = .commonIdentifierTitle
            title.value = playback.request.item.name as NSString
            title.extendedLanguageTag = "und"
            playback.player.currentItem?.externalMetadata = [title]
        }
        let state = ActionState(intro: playback.activeIntro, credits: playback.activeCredits,
                                nextID: playback.nextEpisode?.id, finished: playback.hasFinished,
                                enabled: playback.canUseVideoActions)
        guard state != context.coordinator.actionState else { return }
        if state.nextID != context.coordinator.actionState?.nextID
            || state.enabled != context.coordinator.actionState?.enabled {
            context.coordinator.nextEpisodeAction?.attributes = state.enabled ? [] : [.disabled]
            controller.transportBarCustomMenuItems = [context.coordinator.settingsAction,
                state.nextID == nil ? nil : context.coordinator.nextEpisodeAction].compactMap { $0 }
        }
        context.coordinator.actionState = state
        var actions: [UIAction] = []
        if let intro = state.intro {
            actions.append(UIAction(title: "Skip intro", identifier: UIAction.Identifier("skipIntroButton")) { _ in
                Task { await playback.skip(intro) }
            })
        }
        if let credits = state.credits {
            actions.append(UIAction(title: "Skip credits", identifier: UIAction.Identifier("skipCreditsButton")) { _ in
                Task { await playback.skip(credits) }
            })
        }
        if state.nextID != nil, state.credits != nil || state.finished {
            actions.append(UIAction(title: "Next episode", identifier: UIAction.Identifier("nextEpisodeButton")) { _ in
                Task { await playback.playNextEpisode() }
            })
        }
        if state.finished {
            actions.append(UIAction(title: "Replay", identifier: UIAction.Identifier("replayButton")) { _ in
                Task { await playback.replay() }
            })
        }
        for action in actions { action.attributes = state.enabled ? [] : [.disabled] }
        controller.contextualActions = actions
    }

    static func dismantleUIViewController(_ controller: AVPlayerViewController, coordinator: Coordinator) {
        controller.delegate = nil
        controller.player = nil
    }

    struct ActionState: Equatable {
        let intro: PlaybackMarker?
        let credits: PlaybackMarker?
        let nextID: String?
        let finished: Bool
        let enabled: Bool
    }

    final class Coordinator: NSObject, AVPlayerViewControllerDelegate {
        var parent: TVNativeVideoPlayer
        weak var metadataItem: AVPlayerItem?
        var actionState: ActionState?
        var settingsAction: UIAction?
        var nextEpisodeAction: UIAction?

        init(_ parent: TVNativeVideoPlayer) { self.parent = parent }

        func playerViewControllerShouldDismiss(_ playerViewController: AVPlayerViewController) -> Bool {
            // The controller is embedded in SwiftUI's full-screen presentation.
            parent.onDismiss()
            return false
        }

        func playerViewController(_ playerViewController: AVPlayerViewController,
                                  didSelect mediaSelectionOption: AVMediaSelectionOption?,
                                  in mediaSelectionGroup: AVMediaSelectionGroup) {
            parent.onMediaSelection(mediaSelectionOption, mediaSelectionGroup)
        }
    }
}
