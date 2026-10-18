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
            #if os(tvOS)
            VStack(alignment: .leading, spacing: 8) {
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
            #endif
        }
        #if !os(tvOS)
        .safeAreaInset(edge: .top, alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        playbackActions
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        playbackActions
                    }
                }
                .padding(8)
                .background(.black.opacity(0.75), in: .rect(cornerRadius: 12))
                if let error = playback.errorMessage {
                    Text(error)
                        .padding()
                        .background(.black.opacity(0.8), in: .rect(cornerRadius: 12))
                }
                if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                    Color.clear
                        .frame(width: 1, height: 1)
                        .accessibilityElement()
                        .accessibilityLabel("Playback seconds")
                        .accessibilityIdentifier("playbackTime")
                        .accessibilityValue(String(Int(playback.elapsedSeconds)))
                }
                if let syncError = playback.syncError {
                    Text(syncError)
                        .font(.caption)
                        .padding(8)
                        .background(.black.opacity(0.8), in: .rect(cornerRadius: 8))
                }
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.top, 8)
        }
        #endif
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
        .task { await playback.start() }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { playback.pause() }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            playback.pause()
        }
        #if os(tvOS)
        .onChange(of: playback.hasFinished) { _, finished in
            if finished { finish() }
        }
        .onExitCommand { finish() }
        #endif
    }

    private func finish() {
        playback.close()
        dismiss()
    }

    private func replay() {
        Task { await playback.replay() }
    }

    @ViewBuilder private var playbackActions: some View {
        Button("Done", systemImage: "xmark.circle.fill") { finish() }
            .buttonStyle(.borderedProminent)
            .tint(LuminaTheme.gold)
            .foregroundStyle(LuminaTheme.paper)
            .controlSize(.large)
            .accessibilityIdentifier("closePlayerButton")
        if playback.hasFinished {
            Button("Replay", systemImage: "arrow.counterclockwise") { replay() }
                .buttonStyle(.bordered)
                .tint(.white)
                .controlSize(.large)
                .disabled(playback.isReplaying)
                .accessibilityIdentifier("replayButton")
        }
    }
}

@MainActor @Observable
private final class PlaybackModel {
    let player: AVPlayer
    private(set) var request: PlaybackRequest
    private let store: LuminaStore
    var elapsedSeconds: Double
    var hasFinished = false
    var isReplaying = false
    var errorMessage: String?
    var syncError: String?
    private var timeObserver: Any?
    private var controlObservation: NSKeyValueObservation?
    private var endObserver: NSObjectProtocol?
    private var reportQueue: Task<Void, Never>?
    private var pendingReports: [(event: PlaybackEvent, request: PlaybackRequest, seconds: Double, paused: Bool)] = []
    private var didSeek = false
    private var started = false
    private var autoplayWhenReady = true
    private var closed = false
    private var didReportCompletion = false
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
                guard let self, !self.closed, !self.hasFinished else { return }
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
                guard let self, !self.closed, !self.hasFinished else { return }
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        installEndObserver()
        if autoplayWhenReady { player.play() }
    }

    func pause() {
        guard !closed else { return }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        player.pause()
        guard !hasFinished else { return }
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func replay() async {
        guard hasFinished, !closed, !isReplaying else { return }
        isReplaying = true
        defer { isReplaying = false }
        autoplayWhenReady = true
        errorMessage = nil
        let newRequest: PlaybackRequest
        do {
            newRequest = try await store.preparePlayback(for: request.item, forceTranscode: request.isTranscoding)
        } catch {
            errorMessage = "Replay could not start. Check the server connection and try again."
            return
        }
        guard !closed, !Task.isCancelled else {
            try? await store.reportPlayback(event: .stop, request: newRequest,
                                            positionSeconds: 0, isPaused: true)
            return
        }
        request = newRequest
        elapsedSeconds = 0
        didReportCompletion = false
        player.replaceCurrentItem(with: AVPlayerItem(url: newRequest.url))
        installEndObserver()
        for _ in 0..<300 {
            guard !Task.isCancelled, !closed else { return }
            guard let item = player.currentItem else { return }
            if item.status == .failed {
                failReplay("Replay could not start. Check the server connection and try again.")
                return
            }
            if item.status == .readyToPlay { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        guard !closed, !Task.isCancelled else { return }
        guard player.currentItem?.status == .readyToPlay else {
            failReplay("Replay did not become ready. Check the server connection.")
            return
        }
        await player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, !Task.isCancelled else { return }
        updatePosition()
        hasFinished = false
        enqueue(.start, paused: !autoplayWhenReady)
        if autoplayWhenReady { player.play() }
    }

    func close() {
        guard !closed else { return }
        closed = true
        updatePosition()
        player.pause()
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        if !didReportCompletion {
            enqueue(.stop, paused: true)
        }
        player.replaceCurrentItem(with: nil)
    }

    private func finishPlayback() {
        guard !closed, !hasFinished else { return }
        updatePosition()
        hasFinished = true
        autoplayWhenReady = false
        player.pause()
        // The stopped server session is no longer a valid replay source (especially for HLS).
        // Remove it so AVKit can't restart the obsolete item behind the fresh Replay action.
        player.replaceCurrentItem(with: nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
    }

    private func failReplay(_ message: String) {
        errorMessage = message
        player.replaceCurrentItem(with: nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
    }

    private func installEndObserver() {
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        guard let item = player.currentItem else {
            endObserver = nil
            return
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.finishPlayback()
            }
        }
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
        pendingReports.append((event, request, elapsedSeconds, paused))
        guard reportQueue == nil else { return }
        reportQueue = Task { [self] in
            while !pendingReports.isEmpty {
                let report = pendingReports.removeFirst()
                do {
                    try await store.reportPlayback(event: report.event, request: report.request,
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
