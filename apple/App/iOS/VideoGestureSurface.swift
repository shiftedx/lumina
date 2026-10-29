import LuminaCore
import SwiftUI
import UIKit

/// Transparent, app-owned touch collection surface. Place it below SwiftUI player
/// controls: controls above it retain their normal hit-testing ownership.
struct VideoGestureSurface: UIViewRepresentable {
    struct Configuration {
        var gestures: VideoGestureConfiguration
        var safeArea: UIEdgeInsets
        /// Regions occupied by app-owned controls in the surface's local coordinates.
        /// The UIKit surface passes these through and the adapter also rejects a
        /// contact there before the Core reducer sees it.
        var excludedRegions: [CGRect]

        init(gestures: VideoGestureConfiguration = .init(), safeArea: UIEdgeInsets = .zero, excludedRegions: [CGRect] = []) {
            self.gestures = gestures
            self.safeArea = safeArea
            self.excludedRegions = excludedRegions
        }
    }

    var configuration: Configuration
    var isInteractionEnabled: Bool
    var isVoiceOverRunning: Bool
    /// Change this for modal ownership, route/item changes and presentation teardown.
    var cancellationID: UUID
    var onWindow: @MainActor (UIWindow?) -> Void
    var action: @MainActor (VideoGestureAction) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(configuration: configuration, isInteractionEnabled: isInteractionEnabled,
                    isVoiceOverRunning: isVoiceOverRunning, cancellationID: cancellationID,
                    onWindow: onWindow, action: action)
    }

    func makeUIView(context: Context) -> TouchView {
        let view = TouchView()
        view.coordinator = context.coordinator
        view.excludedRegions = configuration.excludedRegions
        view.backgroundColor = .clear
        view.isMultipleTouchEnabled = true
        view.isExclusiveTouch = false
        context.coordinator.view = view
        context.coordinator.updateGeometry(for: view)
        return view
    }

    func updateUIView(_ view: TouchView, context: Context) {
        let wasInteractive = context.coordinator.isInteractionEnabled
        let wasVoiceOverRunning = context.coordinator.isVoiceOverRunning
        context.coordinator.configuration = configuration
        view.excludedRegions = configuration.excludedRegions
        context.coordinator.isInteractionEnabled = isInteractionEnabled
        context.coordinator.isVoiceOverRunning = isVoiceOverRunning
        context.coordinator.onWindow = onWindow
        context.coordinator.action = action
        if context.coordinator.cancellationID != cancellationID {
            context.coordinator.cancellationID = cancellationID
            context.coordinator.cancelActiveInteractions(deferred: true)
        }
        if (wasInteractive && !isInteractionEnabled) || (!wasVoiceOverRunning && isVoiceOverRunning) {
            context.coordinator.cancelActiveInteractions(deferred: true)
        }
        context.coordinator.updateGeometry(for: view)
        view.updateAccessibility(isVoiceOverRunning: isVoiceOverRunning, isInteractionEnabled: isInteractionEnabled)
    }

    static func dismantleUIView(_ view: TouchView, coordinator: Coordinator) {
        coordinator.cancelActiveInteractions(deferred: true)
        coordinator.cancelTimer()
        coordinator.onWindow(nil)
    }

    @MainActor
    final class Coordinator: NSObject {
        var configuration: Configuration
        var isInteractionEnabled: Bool
        var isVoiceOverRunning: Bool
        var cancellationID: UUID
        var onWindow: @MainActor (UIWindow?) -> Void
        var action: @MainActor (VideoGestureAction) -> Void
        var classifier: VideoGestureClassifier
        weak var view: TouchView?
        private var timer: DispatchWorkItem?
        private var scheduledDeadline: TimeInterval?
        private var activeTouchIDs = Set<Int>()
        private var suppressUntilAllTouchesLift = false

        init(configuration: Configuration, isInteractionEnabled: Bool, isVoiceOverRunning: Bool,
             cancellationID: UUID, onWindow: @escaping @MainActor (UIWindow?) -> Void,
             action: @escaping @MainActor (VideoGestureAction) -> Void) {
            self.configuration = configuration
            self.isInteractionEnabled = isInteractionEnabled
            self.isVoiceOverRunning = isVoiceOverRunning
            self.cancellationID = cancellationID
            self.onWindow = onWindow
            self.action = action
            var gestures = configuration.gestures
            gestures.voiceOverEnabled = isVoiceOverRunning
            self.classifier = VideoGestureClassifier(configuration: gestures, geometry: .init(size: .init(width: 0, height: 0)))
        }

        func updateGeometry(for view: UIView) {
            var gestures = configuration.gestures
            gestures.voiceOverEnabled = isVoiceOverRunning
            let geometry = VideoGestureGeometry(
                size: .init(width: Double(view.bounds.width), height: Double(view.bounds.height)),
                safeArea: .init(top: Double(configuration.safeArea.top), leading: Double(configuration.safeArea.left),
                                bottom: Double(configuration.safeArea.bottom), trailing: Double(configuration.safeArea.right))
            )
            let didChange = classifier.configuration != gestures || classifier.geometry != geometry
            let cancellations = classifier.reconfigure(gestures, geometry: geometry)
            if didChange, !activeTouchIDs.isEmpty { suppressUntilAllTouchesLift = true }
            emitDeferred(cancellations)
            scheduleDeadline()
        }

        func began(_ touches: Set<UITouch>, in view: UIView) {
            let identifiers = touches.map { ObjectIdentifier($0).hashValue }
            activeTouchIDs.formUnion(identifiers)
            guard isInteractionEnabled, !isVoiceOverRunning, !suppressUntilAllTouchesLift else {
                suppressUntilAllTouchesLift = true
                return
            }
            for touch in touches {
                let point = local(touch, in: view)
                let excluded = configuration.excludedRegions.contains { $0.contains(CGPoint(x: point.x, y: point.y)) }
                emit(classifier.touchBegan(id: ObjectIdentifier(touch).hashValue, at: point, timestamp: touch.timestamp, eligible: !excluded))
            }
            scheduleDeadline()
        }

        func moved(_ touches: Set<UITouch>, in view: UIView) {
            guard isInteractionEnabled, !isVoiceOverRunning, !suppressUntilAllTouchesLift else { return }
            for touch in touches {
                emit(classifier.touchMoved(id: ObjectIdentifier(touch).hashValue, to: local(touch, in: view), timestamp: touch.timestamp))
            }
            scheduleDeadline()
        }

        func ended(_ touches: Set<UITouch>, in view: UIView) {
            let identifiers = touches.map { ObjectIdentifier($0).hashValue }
            activeTouchIDs.subtract(identifiers)
            if suppressUntilAllTouchesLift {
                if activeTouchIDs.isEmpty { suppressUntilAllTouchesLift = false }
                return
            }
            guard isInteractionEnabled, !isVoiceOverRunning else { return }
            for touch in touches {
                emit(classifier.touchEnded(id: ObjectIdentifier(touch).hashValue, at: local(touch, in: view), timestamp: touch.timestamp))
            }
            scheduleDeadline()
        }

        func cancelled(_ touches: Set<UITouch>, in view: UIView) {
            let identifiers = touches.map { ObjectIdentifier($0).hashValue }
            activeTouchIDs.subtract(identifiers)
            if suppressUntilAllTouchesLift {
                if activeTouchIDs.isEmpty { suppressUntilAllTouchesLift = false }
                return
            }
            for touch in touches {
                emit(classifier.touchCancelled(id: ObjectIdentifier(touch).hashValue, timestamp: touch.timestamp))
            }
            scheduleDeadline()
        }

        func emit(_ actions: [VideoGestureAction]) {
            actions.forEach(action)
        }

        /// `updateUIView` is part of SwiftUI's render transaction. Gesture cleanup
        /// can mutate observable playback presentation state, so defer it one main
        /// turn rather than publishing from inside that transaction.
        func emitDeferred(_ actions: [VideoGestureAction]) {
            guard !actions.isEmpty else { return }
            let action = action
            DispatchQueue.main.async { actions.forEach(action) }
        }

        func cancelActiveInteractions(deferred: Bool) {
            cancelTimer()
            if !activeTouchIDs.isEmpty { suppressUntilAllTouchesLift = true }
            let actions = classifier.cancelAll()
            if deferred { emitDeferred(actions) } else { emit(actions) }
        }

        func scheduleDeadline() {
            cancelTimer()
            guard let deadline = classifier.nextDeadline else { return }
            scheduledDeadline = deadline
            let timer = DispatchWorkItem { [weak self] in
                guard let self, self.scheduledDeadline == deadline else { return }
                self.scheduledDeadline = nil
                self.emit(self.classifier.advance(to: ProcessInfo.processInfo.systemUptime))
                self.scheduleDeadline()
            }
            self.timer = timer
            DispatchQueue.main.asyncAfter(deadline: .now() + max(0, deadline - ProcessInfo.processInfo.systemUptime), execute: timer)
        }

        func cancelTimer() {
            timer?.cancel(); timer = nil; scheduledDeadline = nil
        }

        private func local(_ touch: UITouch, in view: UIView) -> VideoGesturePoint {
            let point = touch.location(in: view)
            return .init(x: Double(point.x), y: Double(point.y))
        }
    }

    final class TouchView: UIView {
        weak var coordinator: Coordinator?
        var excludedRegions: [CGRect] = []

        override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
            guard super.point(inside: point, with: event) else { return false }
            return !excludedRegions.contains { $0.contains(point) }
        }

        override func didMoveToWindow() {
            super.didMoveToWindow()
            coordinator?.onWindow(window)
        }

        override func layoutSubviews() {
            super.layoutSubviews()
            coordinator?.updateGeometry(for: self)
            #if LUMINA_UI_TESTING
            accessibilityValue = "bounds=\(bounds) enabled=\(coordinator?.isInteractionEnabled == true) appState=\(UIApplication.shared.applicationState.rawValue)"
            #endif
            // External-display and scene changes can retain the same UIView. The
            // presentation owner deduplicates this cheap report by screen identity.
            coordinator?.onWindow(window)
        }

        override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) { coordinator?.began(touches, in: self) }
        override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) { coordinator?.moved(touches, in: self) }
        override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { coordinator?.ended(touches, in: self) }
        override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { coordinator?.cancelled(touches, in: self) }

        func updateAccessibility(isVoiceOverRunning: Bool, isInteractionEnabled: Bool) {
            isAccessibilityElement = isInteractionEnabled
            accessibilityIdentifier = "videoControlsSurface"
            accessibilityLabel = "Playback controls"
            accessibilityHint = "Double-tap to show or hide playback controls"
            accessibilityTraits = .button
            if accessibilityCustomActions == nil {
                accessibilityCustomActions = [
                    accessibleAction("Play or pause", action: .togglePlayback),
                    accessibleAction("Skip forward", action: .relativeSkip(.forward, count: 1)),
                    accessibleAction("Skip backward", action: .relativeSkip(.backward, count: 1))
                ]
            }
        }

        private func accessibleAction(_ name: String, action: VideoGestureAction) -> UIAccessibilityCustomAction {
            UIAccessibilityCustomAction(name: name) { [weak self] _ in
                guard let coordinator = self?.coordinator, coordinator.isInteractionEnabled else { return false }
                coordinator.action(action)
                return true
            }
        }

        override func accessibilityActivate() -> Bool {
            guard let coordinator, coordinator.isInteractionEnabled else { return false }
            coordinator.action(.toggleControls)
            return true
        }
    }
}
