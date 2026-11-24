import AVKit
import AVFoundation
import Combine
import LuminaCore
import Observation
import SwiftUI
import MediaPlayer
#if canImport(UIKit)
import UIKit
#endif
struct NativePlayerLayer: UIViewRepresentable {
    let player: AVPlayer
    let videoGravity: AVLayerVideoGravity
    let onVideoTap: @MainActor () -> Void
    let pictureInPictureStartRequest: Int
    let onPictureInPicturePossibleChange: @MainActor (Bool) -> Void
    let onPictureInPictureActiveChange: @MainActor (Bool) -> Void
    let onPictureInPictureContinuationChange: @MainActor (Bool) -> Void
    let onPictureInPictureRestoreUI: @MainActor () -> Void

    init(player: AVPlayer, videoGravity: AVLayerVideoGravity,
         onVideoTap: @escaping @MainActor () -> Void,
         pictureInPictureStartRequest: Int = 0,
         onPictureInPicturePossibleChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureActiveChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureContinuationChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureRestoreUI: @escaping @MainActor () -> Void = {}) {
        self.player = player
        self.videoGravity = videoGravity
        self.onVideoTap = onVideoTap
        self.pictureInPictureStartRequest = pictureInPictureStartRequest
        self.onPictureInPicturePossibleChange = onPictureInPicturePossibleChange
        self.onPictureInPictureActiveChange = onPictureInPictureActiveChange
        self.onPictureInPictureContinuationChange = onPictureInPictureContinuationChange
        self.onPictureInPictureRestoreUI = onPictureInPictureRestoreUI
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onVideoTap: onVideoTap,
                    onPictureInPicturePossibleChange: onPictureInPicturePossibleChange,
                    onPictureInPictureActiveChange: onPictureInPictureActiveChange,
                    onPictureInPictureContinuationChange: onPictureInPictureContinuationChange,
                    onPictureInPictureRestoreUI: onPictureInPictureRestoreUI)
    }

    func makeUIView(context: Context) -> PlayerLayerView {
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE native-player-layer make \(ObjectIdentifier(player).hashValue)")
        #endif
        let view = PlayerLayerView()
        view.traceRendering("view-create")
        if view.playerLayer.player !== player {
            view.traceRendering("layer-player-write")
            view.playerLayer.player = player
        }
        if view.playerLayer.videoGravity != videoGravity {
            view.traceRendering("layer-gravity-write")
            view.playerLayer.videoGravity = videoGravity
        }
        context.coordinator.attach(view: view, player: player)
        view.traceRendering("view-attached")
        context.coordinator.handlePictureInPictureStartRequest(pictureInPictureStartRequest)
        return view
    }

    func updateUIView(_ view: PlayerLayerView, context: Context) {
        view.traceRendering("view-update")
        context.coordinator.onVideoTap = onVideoTap
        context.coordinator.updatePictureInPictureCallbacks(
            possible: onPictureInPicturePossibleChange,
            active: onPictureInPictureActiveChange,
            continuation: onPictureInPictureContinuationChange,
            restoreUI: onPictureInPictureRestoreUI
        )
        if view.playerLayer.player !== player {
            view.traceRendering("layer-player-write")
            view.playerLayer.player = player
        }
        if view.playerLayer.videoGravity != videoGravity {
            view.traceRendering("layer-gravity-write")
            view.playerLayer.videoGravity = videoGravity
        }
        context.coordinator.attach(view: view, player: player)
        context.coordinator.handlePictureInPictureStartRequest(pictureInPictureStartRequest)
    }

    static func dismantleUIView(_ view: PlayerLayerView, coordinator: Coordinator) {
        view.traceRendering("view-dismantle")
        coordinator.detach(from: view)
        view.playerLayer.player = nil
    }

    @MainActor final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var onVideoTap: @MainActor () -> Void
        private weak var view: PlayerLayerView?
        private weak var player: AVPlayer?

        #if os(iOS)
        private var onPictureInPicturePossibleChange: @MainActor (Bool) -> Void
        private var onPictureInPictureActiveChange: @MainActor (Bool) -> Void
        private var onPictureInPictureContinuationChange: @MainActor (Bool) -> Void
        private var onPictureInPictureRestoreUI: @MainActor () -> Void
        private var pictureInPictureController: AVPictureInPictureController?
        private weak var pictureInPictureLayer: AVPlayerLayer?
        private var pictureInPicturePossibleObservation: NSKeyValueObservation?
        private var pendingPictureInPictureStartRequest = 0
        private var handledPictureInPictureStartRequest = 0
        private var reportedPictureInPicturePossible: Bool?
        private var reportedPictureInPictureActive: Bool?
        private var reportedPictureInPictureContinuation: Bool?
        #endif

        #if os(tvOS)
        private weak var displayWindow: UIWindow?
        private var videoTapRecognizer: UITapGestureRecognizer?
        private var currentItemObservation: NSKeyValueObservation?
        private var displayCriteriaTask: Task<Void, Never>?
        private var criteriaItem: AVPlayerItem?
        private var previousDisplayCriteria: AVDisplayCriteria?
        private var installedDisplayCriteria: AVDisplayCriteria?
        private var displayCriteriaLeaseLost = false
        #endif

        init(onVideoTap: @escaping @MainActor () -> Void,
             onPictureInPicturePossibleChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureActiveChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureContinuationChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureRestoreUI: @escaping @MainActor () -> Void) {
            self.onVideoTap = onVideoTap
            #if os(iOS)
            self.onPictureInPicturePossibleChange = onPictureInPicturePossibleChange
            self.onPictureInPictureActiveChange = onPictureInPictureActiveChange
            self.onPictureInPictureContinuationChange = onPictureInPictureContinuationChange
            self.onPictureInPictureRestoreUI = onPictureInPictureRestoreUI
            #endif
            super.init()
        }

        func attach(view: PlayerLayerView, player: AVPlayer) {
            #if os(tvOS)
            let playerChanged = self.player !== player
            #endif
            self.view = view
            self.player = player
            #if os(iOS)
            configurePictureInPicture(for: view.playerLayer)
            #endif
            #if os(tvOS)
            view.didMoveToWindowHandler = { [weak self] in
                Task { @MainActor [weak self] in
                    self?.rendererWindowDidChange()
                }
            }
            if playerChanged {
                displayCriteriaTask?.cancel()
                displayCriteriaTask = nil
                releaseDisplayCriteria()
                criteriaItem = nil
                displayCriteriaLeaseLost = false
                currentItemObservation = player.observe(\.currentItem, options: [.initial, .new]) { [weak self] observedPlayer, _ in
                    Task { @MainActor [weak self, weak observedPlayer] in
                        guard let self, let observedPlayer, self.player === observedPlayer else { return }
                        self.setDisplayCriteriaItem(observedPlayer.currentItem)
                    }
                }
            }
            rendererWindowDidChange()
            #endif
        }

        func detach(from view: PlayerLayerView) {
            guard self.view === view else { return }
            view.didMoveToWindowHandler = nil
            #if os(iOS)
            releasePictureInPicture()
            #endif
            #if os(tvOS)
            if let recognizer = videoTapRecognizer { recognizer.view?.removeGestureRecognizer(recognizer) }
            videoTapRecognizer = nil
            displayCriteriaTask?.cancel()
            displayCriteriaTask = nil
            currentItemObservation = nil
            releaseDisplayCriteria()
            criteriaItem = nil
            displayWindow = nil
            #endif
            self.view = nil
            player = nil
        }

        #if os(iOS)
        func updatePictureInPictureCallbacks(
            possible: @escaping @MainActor (Bool) -> Void,
            active: @escaping @MainActor (Bool) -> Void,
            continuation: @escaping @MainActor (Bool) -> Void,
            restoreUI: @escaping @MainActor () -> Void
        ) {
            onPictureInPicturePossibleChange = possible
            onPictureInPictureActiveChange = active
            onPictureInPictureContinuationChange = continuation
            onPictureInPictureRestoreUI = restoreUI
        }

        func handlePictureInPictureStartRequest(_ request: Int) {
            guard request != 0, request != handledPictureInPictureStartRequest else { return }
            pendingPictureInPictureStartRequest = request
            startPictureInPictureIfPossible()
        }

        private func configurePictureInPicture(for layer: AVPlayerLayer) {
            guard pictureInPictureLayer !== layer else { return }
            releasePictureInPicture()
            #if LUMINA_UI_TESTING
            if ProcessInfo.processInfo.arguments.contains("--ui-testing-force-video-system-pause") {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            #endif
            guard AVPictureInPictureController.isPictureInPictureSupported() else {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            guard let controller = AVPictureInPictureController(playerLayer: layer) else {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            controller.delegate = self
            controller.canStartPictureInPictureAutomaticallyFromInline = true
            pictureInPictureLayer = layer
            pictureInPictureController = controller
            let controllerID = ObjectIdentifier(controller)
            pictureInPicturePossibleObservation = controller.observe(
                \.isPictureInPicturePossible, options: [.initial, .new]
            ) { [weak self] _, change in
                let possible = change.newValue
                Task { @MainActor [weak self] in
                    guard let self, let current = self.pictureInPictureController,
                          ObjectIdentifier(current) == controllerID else { return }
                    self.reportPictureInPicturePossible(possible ?? current.isPictureInPicturePossible)
                    self.startPictureInPictureIfPossible()
                }
            }
            reportPictureInPictureActive(controller.isPictureInPictureActive)
        }

        private func startPictureInPictureIfPossible() {
            guard pendingPictureInPictureStartRequest != 0,
                  pendingPictureInPictureStartRequest != handledPictureInPictureStartRequest,
                  let controller = pictureInPictureController,
                  controller.isPictureInPicturePossible else { return }
            handledPictureInPictureStartRequest = pendingPictureInPictureStartRequest
            controller.startPictureInPicture()
        }

        private func releasePictureInPicture() {
            pictureInPicturePossibleObservation = nil
            if pictureInPictureController?.isPictureInPictureActive == true {
                pictureInPictureController?.stopPictureInPicture()
            }
            pictureInPictureController?.delegate = nil
            pictureInPictureController = nil
            pictureInPictureLayer = nil
            pendingPictureInPictureStartRequest = 0
            reportPictureInPicturePossible(false)
            reportPictureInPictureActive(false)
            reportPictureInPictureContinuation(false)
        }

        private func reportPictureInPicturePossible(_ possible: Bool) {
            guard reportedPictureInPicturePossible != possible else { return }
            reportedPictureInPicturePossible = possible
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPicturePossible == possible else { return }
                self.onPictureInPicturePossibleChange(possible)
            }
        }

        private func reportPictureInPictureActive(_ active: Bool) {
            guard reportedPictureInPictureActive != active else { return }
            reportedPictureInPictureActive = active
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPictureActive == active else { return }
                self.onPictureInPictureActiveChange(active)
            }
        }

        private func reportPictureInPictureContinuation(_ continuing: Bool, notifyImmediately: Bool = false) {
            guard reportedPictureInPictureContinuation != continuing else { return }
            reportedPictureInPictureContinuation = continuing
            if notifyImmediately {
                onPictureInPictureContinuationChange(continuing)
                return
            }
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPictureContinuation == continuing else { return }
                self.onPictureInPictureContinuationChange(continuing)
            }
        }

        nonisolated func pictureInPictureControllerWillStartPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureContinuation(true, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureController(
            _ pictureInPictureController: AVPictureInPictureController,
            failedToStartPictureInPictureWithError error: any Error
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureActive(false)
                self.reportPictureInPictureContinuation(false, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureControllerDidStartPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureContinuation(true, notifyImmediately: true)
                self.reportPictureInPictureActive(true)
            }
        }

        nonisolated func pictureInPictureControllerDidStopPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureActive(false)
                self.reportPictureInPictureContinuation(false, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureController(
            _ pictureInPictureController: AVPictureInPictureController,
            restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            let completion = PictureInPictureRestoreCompletion(completionHandler)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else {
                    completion(false)
                    return
                }
                self.onPictureInPictureRestoreUI()
                completion(self.view != nil)
            }
        }
        #else
        func updatePictureInPictureCallbacks(
            possible: @escaping @MainActor (Bool) -> Void,
            active: @escaping @MainActor (Bool) -> Void,
            continuation: @escaping @MainActor (Bool) -> Void,
            restoreUI: @escaping @MainActor () -> Void
        ) { }

        func handlePictureInPictureStartRequest(_ request: Int) { }
        #endif

        #if os(tvOS)
        private func rendererWindowDidChange() {
            guard let view else { return }
            if displayWindow !== view.window {
                if let recognizer = videoTapRecognizer { recognizer.view?.removeGestureRecognizer(recognizer) }
                videoTapRecognizer = nil
                if let window = view.window {
                    let recognizer = UITapGestureRecognizer(target: self, action: #selector(videoTapped))
                    // A light touchpad tap toggles chrome; a Select press still activates the focused control.
                    recognizer.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.indirect.rawValue)]
                    recognizer.allowedPressTypes = []
                    recognizer.cancelsTouchesInView = false
                    recognizer.delegate = self
                    window.addGestureRecognizer(recognizer)
                    videoTapRecognizer = recognizer
                }
                displayCriteriaTask?.cancel()
                displayCriteriaTask = nil
                releaseDisplayCriteria()
                displayWindow = view.window
                displayCriteriaLeaseLost = false
                setDisplayCriteriaItem(player?.currentItem, force: true)
            }
        }

        @objc private func videoTapped() { onVideoTap() }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }

        private func setDisplayCriteriaItem(_ item: AVPlayerItem?, force: Bool = false) {
            guard force || criteriaItem !== item else { return }
            criteriaItem = item
            displayCriteriaTask?.cancel()
            displayCriteriaTask = nil
            guard let item else {
                releaseDisplayCriteria()
                displayCriteriaLeaseLost = false
                return
            }
            guard !displayCriteriaLeaseLost, let player, player.currentItem === item,
                  let view, let window = view.window else { return }
            displayWindow = window
            displayCriteriaTask = Task { [weak self, weak player, weak window] in
                do {
                    let criteria = try await item.asset.load(.preferredDisplayCriteria)
                    guard !Task.isCancelled, let self, let player, let window,
                          self.player === player, player.currentItem === item,
                          self.criteriaItem === item, self.view?.window === window else { return }
                    self.installDisplayCriteria(criteria, in: window)
                } catch is CancellationError {
                    return
                } catch {
                    guard !Task.isCancelled, let self, let player, let window,
                          self.player === player, player.currentItem === item,
                          self.criteriaItem === item, self.view?.window === window else { return }
                    self.releaseDisplayCriteria()
                    return
                }
            }
        }

        private func installDisplayCriteria(_ criteria: AVDisplayCriteria, in window: UIWindow) {
            let manager = window.avDisplayManager
            if displayWindow !== window {
                releaseDisplayCriteria()
                displayWindow = window
            }
            if let installedDisplayCriteria {
                guard let currentCriteria = manager.preferredDisplayCriteria,
                      currentCriteria === installedDisplayCriteria else {
                    previousDisplayCriteria = nil
                    self.installedDisplayCriteria = nil
                    displayCriteriaLeaseLost = true
                    return
                }
            } else {
                previousDisplayCriteria = manager.preferredDisplayCriteria
            }
            manager.preferredDisplayCriteria = criteria
            // Read back the manager-owned copy so cleanup can leave any newer
            // display request alone rather than overwriting it with a stale value.
            guard let installed = manager.preferredDisplayCriteria else {
                previousDisplayCriteria = nil
                displayCriteriaLeaseLost = true
                return
            }
            installedDisplayCriteria = installed
        }

        private func releaseDisplayCriteria() {
            defer {
                previousDisplayCriteria = nil
                installedDisplayCriteria = nil
            }
            guard let window = displayWindow, let installedDisplayCriteria,
                  let currentCriteria = window.avDisplayManager.preferredDisplayCriteria,
                  currentCriteria === installedDisplayCriteria else { return }
            window.avDisplayManager.preferredDisplayCriteria = previousDisplayCriteria
        }
        #endif
    }
}

#if os(iOS)
private final class PictureInPictureRestoreCompletion: @unchecked Sendable {
    private let completion: (Bool) -> Void

    init(_ completion: @escaping (Bool) -> Void) {
        self.completion = completion
    }

    func callAsFunction(_ restored: Bool) {
        completion(restored)
    }
}

extension NativePlayerLayer.Coordinator: AVPictureInPictureControllerDelegate { }
#endif

#if LUMINA_UI_TESTING
// Aggregate hot callbacks and cap detailed output so diagnosis cannot become a log storm.
@MainActor
private final class PlaybackContinuityTrace {
    static let enabled = ProcessInfo.processInfo.environment["LUMINA_PLAYBACK_TRACE"] == "1"
    private var counts: [String: Int] = [:]
    private var windowStart = ProcessInfo.processInfo.systemUptime
    private var detailedEvents = 0

    func record(_ event: String, state: @autoclosure () -> String) {
        guard Self.enabled else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if now - windowStart >= 1 {
            let totals = counts.keys.sorted().map { "\($0):\(counts[$0]!)" }.joined(separator: ",")
            write("wall=\(now) interval=\(now - windowStart) counts=\(totals) \(state())")
            counts.removeAll(keepingCapacity: true)
            detailedEvents = 0
            windowStart = now
        }
        let key = String(event.split(separator: "=", maxSplits: 1)[0])
        counts[key, default: 0] += 1
        if detailedEvents < 20 {
            write("wall=\(now) event=\(event) \(state())")
            detailedEvents += 1
        }
    }

    private func write(_ line: String) {
        FileHandle.standardError.write(Data("[LUMINA_CONTINUITY] \(line)\n".utf8))
    }
}
#endif

final class PlayerLayerView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    var didMoveToWindowHandler: (() -> Void)?
    #if LUMINA_UI_TESTING
    private let continuityTrace = PlaybackContinuityTrace()
    #endif
    private var readyObservation: NSKeyValueObservation?

    override init(frame: CGRect) {
        super.init(frame: frame)
        observeReadiness()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        observeReadiness()
    }

    private func observeReadiness() {
        readyObservation = playerLayer.observe(\.isReadyForDisplay, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.traceRendering("render-ready-change") }
        }
    }

    func traceRendering(_ event: String) {
        if PlaybackDiagnostics.shared.isCapturing {
            let kind: PlaybackDiagnosticEvent?
            switch event {
            case "view-attached": kind = .rendererCreated
            case "view-dismantle": kind = .rendererDetached
            case "layer-window-change": kind = .rendererWindowChanged
            case "layer-layout", "view-update": kind = .rendererLayout
            case "render-ready-change": kind = .rendererReadyChanged
            default: kind = nil
            }
            if let kind {
                var sample = PlaybackDiagnosticSample()
                let player = playerLayer.player
                sample.playerID = PlaybackDiagnostics.identity(player)
                sample.itemID = PlaybackDiagnostics.identity(player?.currentItem)
                sample.positionSeconds = player?.currentTime().seconds
                sample.rate = player.map { Double($0.rate) }
                sample.timeControlStatus = player?.timeControlStatus.rawValue
                sample.width = Double(bounds.width)
                sample.height = Double(bounds.height)
                sample.displayReady = playerLayer.isReadyForDisplay
                sample.windowID = PlaybackDiagnostics.identity(window)
                sample.viewID = PlaybackDiagnostics.identity(self)
                sample.layerID = PlaybackDiagnostics.identity(playerLayer)
                PlaybackDiagnostics.shared.record(kind, sample: sample)
            }
        }
        #if LUMINA_UI_TESTING
        guard PlaybackContinuityTrace.enabled else { return }
        continuityTrace.record(event, state: "view=\(ObjectIdentifier(self)) layer=\(ObjectIdentifier(playerLayer)) player=\(playerLayer.player.map { String(describing: ObjectIdentifier($0)) } ?? "nil") window=\(window.map { String(describing: ObjectIdentifier($0)) } ?? "nil") bounds=\(bounds) ready=\(playerLayer.isReadyForDisplay) hidden=\(isHidden) opacity=\(layer.opacity)")
        #endif
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        traceRendering("layer-layout")
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        traceRendering("layer-window-change")
        didMoveToWindowHandler?()
    }
}

#if os(iOS)
struct VideoRoutePicker: View {
    var active = false

    var body: some View {
        ZStack {
            NativeAirPlayRoutePicker()
            LuminaIcon(glyph: .airplay, size: 24)
                .foregroundStyle(active ? LuminaTheme.brand : .white)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
    }
}

private struct NativeAirPlayRoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.tintColor = .clear
        picker.activeTintColor = .clear
        picker.prioritizesVideoDevices = true
        picker.accessibilityLabel = "AirPlay"
        picker.accessibilityIdentifier = "videoAirPlayButton"
        return picker
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) { }
}

#endif
