import AVKit
import AVFoundation
import LuminaCore
import Observation
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// AVKit owns transport controls, audio/subtitle selection, seeking, and the remote.
struct PlaybackScreen: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var playback: PlaybackModel

    init(request: PlaybackRequest, store: LuminaStore) {
        _playback = State(initialValue: PlaybackModel(request: request, store: store))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            NativePlayer(player: playback.player) { finish() }
                .ignoresSafeArea()
                .accessibilityIdentifier("nativeVideoPlayer")
            VStack(alignment: .leading, spacing: 8) {
                #if !os(tvOS)
                Button("Done", systemImage: "xmark.circle.fill") { finish() }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("closePlayerButton")
                #endif
                if let error = playback.errorMessage {
                    Text(error).padding().background(.black.opacity(0.8))
                    Button("Close player") { finish() }
                        .accessibilityIdentifier("closePlayerButton")
                }
                Text(playback.request.methodLabel)
                    .font(.caption)
                Text(playback.elapsedSeconds.formatted(.number.precision(.fractionLength(0))))
                    .font(.caption.monospacedDigit())
                    .accessibilityLabel("Playback seconds")
                    .accessibilityIdentifier("playbackTime")
                    .accessibilityValue(String(Int(playback.elapsedSeconds)))
                if let syncError = playback.syncError {
                    Text(syncError).font(.caption)
                }
            }
            .foregroundStyle(.white)
            .padding()
            .background(.black.opacity(0.45))
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
        .task { await playback.start() }
        .onChange(of: playback.hasFinished) { _, finished in
            if finished { finish() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { playback.pause() }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            playback.pause()
        }
        #if os(tvOS)
        .onExitCommand { finish() }
        #endif
    }

    private func finish() {
        playback.close()
        dismiss()
    }
}

@MainActor @Observable
private final class PlaybackModel {
    let player: AVPlayer
    let request: PlaybackRequest
    private let store: LuminaStore
    var elapsedSeconds: Double
    var hasFinished = false
    var errorMessage: String?
    var syncError: String?
    private var timeObserver: Any?
    private var controlObservation: NSKeyValueObservation?
    private var endObserver: NSObjectProtocol?
    private var reportQueue: Task<Void, Never>?
    private var pendingReports: [(event: PlaybackEvent, seconds: Double, paused: Bool)] = []
    private var didSeek = false
    private var started = false
    private var autoplayWhenReady = true
    private var closed = false
    private var lastReport: Double = -1

    init(request: PlaybackRequest, store: LuminaStore) {
        self.request = request
        self.store = store
        elapsedSeconds = request.startPositionSeconds
        player = AVPlayer(url: request.url)
        player.preventsDisplaySleepDuringVideoPlayback = true
    }

    func start() async {
        guard !started, !closed else { return }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
        } catch {
            errorMessage = "Audio playback could not be configured."
            return
        }
        started = true
        defer {
            if !didSeek { started = false }
        }
        // Do not report a zero position while the item is loading or seeking to resume.
        for _ in 0..<300 {
            guard !Task.isCancelled, !closed else { return }
            guard let item = player.currentItem else { return }
            if item.status == .failed {
                errorMessage = "This video could not be played. Try Convert to compatible video."
                return
            }
            if item.status == .readyToPlay { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        guard !closed, !Task.isCancelled else { return }
        guard player.currentItem?.status == .readyToPlay else {
            errorMessage = "The video did not become ready. Check the server connection."
            return
        }
        if request.startPositionSeconds > 0 {
            await player.seek(to: CMTime(seconds: request.startPositionSeconds, preferredTimescale: 600),
                              toleranceBefore: .zero, toleranceAfter: .zero)
        }
        guard !closed, !Task.isCancelled else { return }
        didSeek = true
        updatePosition()
        enqueue(.start, paused: !autoplayWhenReady)
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 1, preferredTimescale: 600), queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed else { return }
                self.updatePosition()
                if abs(self.elapsedSeconds - self.lastReport) >= 5 {
                    self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
                }
                if self.player.currentItem?.status == .failed {
                    self.errorMessage = "Playback stopped. Check your connection or try compatible video."
                }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed else { return }
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        endObserver = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: player.currentItem, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.hasFinished = true
                self?.close()
            }
        }
        if autoplayWhenReady { player.play() }
    }

    func pause() {
        guard !closed else { return }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        player.pause()
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func close() {
        guard !closed else { return }
        closed = true
        updatePosition()
        player.pause()
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        enqueue(.stop, paused: true)
        player.replaceCurrentItem(with: nil)
    }

    private func updatePosition() {
        let seconds = player.currentTime().seconds
        if didSeek, seconds.isFinite, seconds >= 0, player.currentItem?.status == .readyToPlay {
            elapsedSeconds = seconds
        }
    }

    private func enqueue(_ event: PlaybackEvent, paused: Bool) {
        lastReport = elapsedSeconds
        // Keep one pending progress snapshot. Stop follows the in-flight request,
        // so an old progress response cannot overwrite the final saved position.
        if case .progress = event, let last = pendingReports.last, case .progress = last.event {
            pendingReports.removeLast()
        }
        if case .stop = event {
            pendingReports.removeAll { if case .progress = $0.event { return true }; return false }
        }
        pendingReports.append((event, elapsedSeconds, paused))
        guard reportQueue == nil else { return }
        reportQueue = Task { [self] in
            while !pendingReports.isEmpty {
                let report = pendingReports.removeFirst()
                do {
                    try await store.reportPlayback(event: report.event, request: request,
                                                   positionSeconds: report.seconds, isPaused: report.paused)
                    syncError = nil
                    if case .stop = report.event { await store.refresh() }
                } catch {
                    syncError = "Watch progress could not sync. Check the server connection."
                }
            }
            reportQueue = nil
        }
    }

}

private struct NativePlayer: UIViewControllerRepresentable {
    let player: AVPlayer
    let onDismiss: @MainActor () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onDismiss: onDismiss) }

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        controller.player = player
        controller.view.accessibilityIdentifier = "nativeVideoPlayer"
        controller.delegate = context.coordinator
        controller.allowsPictureInPicturePlayback = false
        #if os(tvOS)
        controller.appliesPreferredDisplayCriteriaAutomatically = true
        #endif
        return controller
    }

    func updateUIViewController(_ controller: AVPlayerViewController, context: Context) {}

    @MainActor final class Coordinator: NSObject, @MainActor AVPlayerViewControllerDelegate {
        let onDismiss: @MainActor () -> Void
        init(onDismiss: @escaping @MainActor () -> Void) { self.onDismiss = onDismiss }
        #if os(tvOS)
        func playerViewControllerShouldDismiss(_ playerViewController: AVPlayerViewController) -> Bool {
            onDismiss()
            return true
        }
        #endif
    }
}
