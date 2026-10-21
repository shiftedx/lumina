import AVKit
import AVFoundation
import LuminaCore
import Observation
import SwiftUI
import MediaPlayer
#if canImport(UIKit)
import UIKit
#endif

extension QueuePresentationRow where Item == MediaItem {
    var accessibilityID: String { "audioQueueItem-\(item.id)" + (duplicated ? "-\(id.uuidString)" : "") }
}

/// AVKit owns video transport; the listening stage shares its audio model with the app shell.
struct PlaybackScreen: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var requestedPlayback: PlaybackModel
    @State private var startedPresentation = false
    @State private var attemptedAudioAdoption = false
    @State private var presentationError: String?
    @State private var queuePresented = false
    private let store: LuminaStore

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem] = [], queueIndex: Int? = nil) {
        self.store = store
        _requestedPlayback = State(initialValue: PlaybackModel(request: request, store: store, queue: queue, queueIndex: queueIndex))
    }

    init(playback: PlaybackModel, store: LuminaStore) {
        self.store = store
        _requestedPlayback = State(initialValue: playback)
    }

    private var playback: PlaybackModel {
        if requestedPlayback.request.item.isAudio, attemptedAudioAdoption {
            return audioSession.playback ?? requestedPlayback
        }
        return requestedPlayback
    }
    private var displayedError: String? { presentationError ?? playback.errorMessage }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            if requestedPlayback.request.item.isAudio {
                if attemptedAudioAdoption, audioSession.playback == nil {
                    ContentUnavailableView {
                        Label("No playable track", systemImage: "music.note")
                    } description: {
                        Text(audioSession.message ?? "Your shared queue is paused. Browse Audio to manage requested albums.")
                    } actions: {
                        Button("Refresh shared queue") { Task { await audioSession.refreshSharedQueue() } }
                        Button("Browse Audio") { finish() }
                    }
                } else {
                    audioStage
                        #if os(tvOS)
                        .onPlayPauseCommand { playback.togglePlayback() }
                        #endif
                }
            } else {
                NativePlayer(player: playback.player) { finish() }
                    .ignoresSafeArea()
                    .accessibilityIdentifier("nativeVideoPlayer")
            }
            #if os(tvOS)
            if displayedError != nil || playback.syncError != nil {
                VStack(alignment: .leading, spacing: 8) {
                    if let error = displayedError {
                        Text(error).padding().background(.black.opacity(0.8))
                        Button("Close player") { finish() }
                            .accessibilityIdentifier("closePlayerButton")
                    }
                    if let syncError = playback.syncError {
                        Text(syncError).font(.caption)
                    }
                }
                .foregroundStyle(.white)
                .padding()
                .background(.black.opacity(0.45))
            }
            if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                Color.clear
                    .frame(width: 1, height: 1)
                    .accessibilityElement()
                    .accessibilityLabel("Playback seconds")
                    .accessibilityIdentifier("playbackTime")
                    .accessibilityValue(String(Int(playback.elapsedSeconds)))
            }
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
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(.black.opacity(playback.request.item.isAudio ? 0 : 0.75), in: .rect(cornerRadius: 12))
                if let error = displayedError {
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
            .background {
                if playback.request.item.isAudio {
                    LuminaTheme.theater.ignoresSafeArea(edges: .top)
                }
            }
        }
        #endif
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
        .sheet(isPresented: $queuePresented) { audioQueue }
        .task {
            guard !startedPresentation else { return }
            startedPresentation = true
            let initial = requestedPlayback
            initial.setActive(scenePhase == .active)
            if initial.request.item.isAudio {
                do { try await audioSession.adoptForPlayback(initial) }
                catch { presentationError = error.localizedDescription }
                attemptedAudioAdoption = true
            } else {
                audioSession.stop()
                await initial.start()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            playback.setActive(phase == .active)
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            playback.setActive(false)
        }
        #if os(tvOS)
        .onChange(of: playback.hasFinished) { _, finished in
            if finished && !playback.request.item.isAudio { finish() }
        }
        .onExitCommand { finish() }
        #endif
    }

    private var audioStage: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 36) {
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .center, spacing: 64) {
                            audioArtwork.frame(width: min(geometry.size.width * 0.38, 460))
                            audioControls(compact: false).frame(minWidth: 360, maxWidth: 560)
                        }
                        VStack(alignment: .leading, spacing: 20) {
                            audioArtwork.frame(width: min(320, max(160, geometry.size.height * 0.30)))
                                .frame(maxWidth: .infinity)
                            audioControls(compact: true)
                        }
                    }
                    NativeAudioOptions(session: audioSession, playback: playback)
                        .frame(maxWidth: 1120, alignment: .leading)
                }
                .frame(maxWidth: 1120)
                .padding(.horizontal, audioHorizontalInset)
                .padding(.vertical, 32)
                .frame(maxWidth: .infinity, minHeight: geometry.size.height, alignment: .top)
            }
            .background {
                MediaBackdrop(item: playback.request.item, store: store).opacity(0.3)
                    .overlay {
                        LinearGradient(colors: [LuminaTheme.theater.opacity(0.35), LuminaTheme.theater],
                                       startPoint: .top, endPoint: .bottom)
                    }
                    .ignoresSafeArea()
            }
        }
        .foregroundStyle(.white)
        .accessibilityIdentifier("audioPlayerStage")
    }

    private var audioHorizontalInset: CGFloat {
        #if os(tvOS)
        80
        #else
        24
        #endif
    }
    private var audioTitleSize: CGFloat {
        #if os(tvOS)
        52
        #else
        36
        #endif
    }

    private var audioArtwork: some View {
        Rectangle().fill(.white.opacity(0.08))
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                if let url = store.imageURL(for: playback.request.item, maxWidth: 1000) {
                    AsyncImage(url: url) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        Image(systemName: "music.note").font(.largeTitle)
                    }
                } else {
                    Image(systemName: "music.note").font(.largeTitle)
                }
            }
            .clipShape(.rect(cornerRadius: 4))
            .overlay { RoundedRectangle(cornerRadius: 4).strokeBorder(.white.opacity(0.1), lineWidth: 1) }
            .shadow(color: .black.opacity(0.35), radius: 24, y: 16)
            .accessibilityHidden(true)
    }

    private func audioControls(compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: compact ? 14 : 22) {
            Text("NOW PLAYING").font(.caption.weight(.semibold)).tracking(2)
                .foregroundStyle(LuminaTheme.spotlight)
            Text(playback.request.item.name)
                .font(LuminaTheme.editorial(compact ? 30 : audioTitleSize, relativeTo: .largeTitle))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("nowPlayingTitle")
            Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                .font(LuminaTheme.editorial(compact ? 20 : audioTitleSize * 0.62, relativeTo: .title3))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .accessibilityLabel(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
            if let album = playback.request.item.album {
                Text(album).font(LuminaTheme.editorial(compact ? 17 : audioTitleSize * 0.5, relativeTo: .body))
                    .foregroundStyle(.white.opacity(0.65))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .accessibilityLabel(album)
            }
            VStack(spacing: 8) {
                #if os(tvOS)
                ProgressView(value: min(playback.elapsedSeconds, max(1, playback.request.item.durationSeconds)), total: max(1, playback.request.item.durationSeconds))
                    .tint(LuminaTheme.spotlight)
                    .accessibilityLabel("Track position")
                #else
                Slider(value: Binding(get: { playback.elapsedSeconds }, set: { playback.seek(to: $0) }), in: 0...max(1, playback.request.item.durationSeconds))
                    .tint(LuminaTheme.spotlight)
                    .accessibilityLabel("Track position")
                #endif
                HStack {
                    Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                    Spacer()
                    Text(Duration.seconds(playback.request.item.durationSeconds).formatted(.time(pattern: .minuteSecond)))
                }
                .font(.caption.monospacedDigit()).foregroundStyle(.white.opacity(0.8))
            }
            HStack(spacing: 28) {
                Spacer(minLength: 0)
                Button("Previous track", systemImage: "backward.end.fill") { Task { await playback.playQueued(offset: -1) } }
                    .labelStyle(.iconOnly).disabled(!playback.hasPrevious || playback.isReplaying)
                Button(playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play", systemImage: playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
                    if playback.hasFinished { replay() } else { playback.togglePlayback() }
                }
                .labelStyle(.iconOnly)
                .font(.title)
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.circle)
                .tint(LuminaTheme.spotlight)
                .foregroundStyle(LuminaTheme.theater)
                .accessibilityIdentifier("audioPlayPauseButton")
                .disabled(playback.isReplaying)
                Button("Next track", systemImage: "forward.end.fill") { Task { await playback.playQueued(offset: 1) } }
                    .labelStyle(.iconOnly).disabled(!playback.hasNext || playback.isReplaying)
                    .accessibilityIdentifier("audioNextButton")
                Spacer(minLength: 0)
            }
            .font(.title3)
            #if os(tvOS)
            .buttonStyle(.bordered)
            // Include the row's empty space when navigating up from the seek buttons.
            .focusSection()
            #else
            .buttonStyle(.plain)
            #endif
            .controlSize(.large)
            #if os(tvOS)
            HStack(spacing: 24) {
                Button("Back 15 seconds", systemImage: "gobackward.15") { playback.seek(to: playback.elapsedSeconds - 15) }
                    .accessibilityIdentifier("audioSeekBackwardButton")
                Button("Forward 15 seconds", systemImage: "goforward.15") { playback.seek(to: playback.elapsedSeconds + 15) }
                    .accessibilityIdentifier("audioSeekForwardButton")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .buttonStyle(.bordered)
            .focusSection()
            #endif
            if !playback.queue.isEmpty {
                HStack(spacing: 24) {
                    #if os(tvOS)
                    Button("Queue · \(playback.queueIndex + 1)/\(playback.queue.count)", systemImage: "list.bullet") { queuePresented = true }
                        .accessibilityIdentifier("audioQueueButton")
                    Spacer(minLength: 8)
                    #endif
                    Button(playback.repeatMode.label, systemImage: playback.repeatMode.symbol) { playback.cycleRepeat() }
                        .accessibilityValue(playback.repeatMode.label)
                        .accessibilityIdentifier("audioRepeatButton")
                }
                .font(.callout)
                #if os(tvOS)
                .buttonStyle(.bordered)
                .focusSection()
                #else
                .buttonStyle(.plain)
                .frame(minHeight: 44)
                #endif
            }
            Text(playback.request.methodLabel).font(.caption).foregroundStyle(.white.opacity(0.6))
                .accessibilityIdentifier("audioPlaybackMethod")
            #if os(tvOS)
            HStack(spacing: 24) {
                Button { finish() } label: {
                    Label("Browse", systemImage: "chevron.down").frame(maxWidth: .infinity)
                }
                .accessibilityIdentifier("audioDoneButton")
                Button { stopAudio() } label: {
                    Label("Stop", systemImage: "stop.fill").frame(maxWidth: .infinity)
                }
                .accessibilityIdentifier("audioStopButton")
            }
            .buttonStyle(.bordered)
            .focusSection()
            #endif
        }
    }

    private var audioQueue: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Play queue").font(.title2.bold())
                Spacer()
                Button("Done") { queuePresented = false }
                    .accessibilityIdentifier("audioQueueDoneButton")
            }
            .padding(.horizontal)
            #if os(tvOS)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    audioQueueRows
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal)
                .padding(.vertical, 24)
            }
            .buttonStyle(.bordered)
            .focusSection()
            #else
            List {
                audioQueueRows
            }
            .listStyle(.plain)
            #endif
        }
        #if os(tvOS)
        .frame(width: 1000, height: 650)
        #endif
        .padding(.vertical)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioQueueScreen")
    }

    @ViewBuilder private var audioQueueRows: some View {
        ForEach(playback.queueRows) { row in
            let index = row.index
            let item = row.item
            HStack(spacing: 16) {
                Button {
                    Task { await playback.playOccurrence(row.id) }
                } label: {
                    HStack(spacing: 16) {
                        Image(systemName: index == playback.queueIndex ? "speaker.wave.2.fill" : "music.note")
                        Text(item.name).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .disabled(playback.isReplaying || audioSession.isSonos)
                .accessibilityIdentifier(row.accessibilityID)
                if index > playback.queueIndex {
                    Menu("Track actions", systemImage: "ellipsis") {
                        Button("Play next") { playback.moveOccurrence(row.id) }
                        Button("Move up") { playback.moveOccurrence(row.id, offset: -1) }
                            .disabled(index == playback.queueIndex + 1)
                        Button("Move down") { playback.moveOccurrence(row.id, offset: 1) }
                            .disabled(index == playback.queue.count - 1)
                        Button("Remove from queue", role: .destructive) { playback.removeOccurrence(row.id) }
                    }
                    .labelStyle(.iconOnly)
                    .disabled(audioSession.isSonos)
                }
            }
        }
    }

    private func finish() {
        if !playback.request.item.isAudio { playback.close() }
        dismiss()
    }

    private func stopAudio() {
        audioSession.stop()
        dismiss()
    }

    private func replay() {
        Task { await playback.replay() }
    }

    @ViewBuilder private var playbackActions: some View {
        if playback.request.item.isAudio {
            Button { finish() } label: {
                Label("Browse", systemImage: "chevron.down").frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("closePlayerButton")
            Button { queuePresented = true } label: {
                Label("Queue", systemImage: "list.bullet").frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("audioQueueButton")
            Button { stopAudio() } label: {
                Label("Stop", systemImage: "stop.fill").frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("audioStopButton")
        } else {
            Button("Done", systemImage: "xmark.circle.fill") { finish() }
                .buttonStyle(.borderedProminent)
                .tint(LuminaTheme.gold)
                .foregroundStyle(LuminaTheme.paper)
                .controlSize(.large)
                .accessibilityIdentifier("closePlayerButton")
        }
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
final class PlaybackModel {
    enum RepeatMode: CaseIterable {
        case off, all, one
        var label: String { switch self { case .off: "Repeat off"; case .all: "Repeat queue"; case .one: "Repeat track" } }
        var symbol: String { self == .one ? "repeat.1" : "repeat" }
    }
    let player: AVPlayer
    private(set) var request: PlaybackRequest
    let store: LuminaStore
    weak var audioOwner: NativeAudioSession?
    var audioTransport: NativeMusicTransport?
    private var reportedAudioStart = false
    let initiallyPaused: Bool
    var elapsedSeconds: Double
    var hasFinished = false
    var isReplaying = false
    var errorMessage: String?
    var syncError: String?
    var isPlaying = false
    private(set) var queue: [MediaItem]
    private var occurrenceIDs: [UUID]
    typealias QueueRow = QueuePresentationRow<MediaItem>
    private(set) var queueRows: [QueueRow]

    private static func makeQueueRows(items: [MediaItem], occurrenceIDs: [UUID]) -> [QueueRow] {
        QueuePresentation.rows(items: items, occurrenceIDs: occurrenceIDs, itemID: { $0.id })
    }
    private func rebuildQueueRows() { queueRows = Self.makeQueueRows(items: queue, occurrenceIDs: occurrenceIDs) }
    func occurrenceIndex(_ id: UUID) -> Int? { occurrenceIDs.firstIndex(of: id) }
    func playOccurrence(_ id: UUID) async {
        guard let index = occurrenceIndex(id) else { return }
        await playQueued(offset: index - queueIndex)
    }
    func moveOccurrence(_ id: UUID, offset: Int? = nil) {
        guard let index = occurrenceIndex(id) else { return }
        moveQueued(at: index, to: offset.map { index + $0 } ?? queueIndex + 1)
    }
    func removeOccurrence(_ id: UUID) {
        guard let index = occurrenceIndex(id) else { return }
        removeQueued(at: index)
    }
    private(set) var repeatMode = RepeatMode.off
    private(set) var queueIndex: Int
    var hasNext: Bool { queueIndex + 1 < queue.count }
    var hasPrevious: Bool { !queue.isEmpty && queueIndex > 0 }
    private var timeObserver: Any?
    private var controlObservation: NSKeyValueObservation?
    private var endObserver: NSObjectProtocol?
    private let videoSource = NativeVideoSource()
    private var reportQueue: Task<Void, Never>?
    private var pendingReports: [(event: PlaybackEvent, request: PlaybackRequest, seconds: Double, paused: Bool)] = []
    private var didSeek = false
    private var started = false
    private var autoplayWhenReady = true
    private var isActive = true
    private var closed = false
    private var didReportCompletion = false
    private var lastReport: Double = -1
    private var remoteTargets: [(command: MPRemoteCommand, target: Any)] = []

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem], queueIndex: Int? = nil, initiallyPaused: Bool = false) {
        self.request = request
        self.store = store
        let resolvedQueue = request.item.isAudio && queue.isEmpty ? [request.item] : queue
        let resolvedOccurrences = resolvedQueue.map { _ in UUID() }
        self.queue = resolvedQueue
        self.occurrenceIDs = resolvedOccurrences
        self.queueRows = Self.makeQueueRows(items: resolvedQueue, occurrenceIDs: resolvedOccurrences)
        self.queueIndex = min(max(0, queueIndex ?? resolvedQueue.firstIndex(where: { $0.id == request.item.id }) ?? 0), max(0, resolvedQueue.count - 1))
        self.initiallyPaused = initiallyPaused
        elapsedSeconds = request.startPositionSeconds
        player = request.item.isAudio || request.usesClientDemux ? AVPlayer() : AVPlayer(url: request.url)
        player.preventsDisplaySleepDuringVideoPlayback = true
        if !request.item.isAudio { player.audiovisualBackgroundPlaybackPolicy = .pauses }
    }

    func start() async {
        guard !started, !closed else { return }
        if request.item.isAudio, let audioOwner {
            started = true
            player.replaceCurrentItem(with: nil)
            installRemoteControls()
            await audioOwner.start(self)
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: request.item.isAudio ? .default : .moviePlayback)
        } catch {
            errorMessage = "Audio playback could not be configured."
            return
        }
        started = true
        if request.item.isAudio { installRemoteControls() }
        defer {
            if !didSeek { started = false; videoSource.stop() }
        }
        if request.usesClientDemux {
            do {
                let item = try await videoSource.prepare(url: request.url, usesClientDemux: request.usesClientDemux)
                guard !closed, !Task.isCancelled else { videoSource.stop(); return }
                player.replaceCurrentItem(with: item)
            } catch {
                videoSource.stop()
                errorMessage = "This video could not be played. Try Convert to compatible video."
                return
            }
        }
        // Do not report a zero position while the item is loading or seeking to resume.
        guard let item = player.currentItem else { return }
        do {
            try await NativeVideoSource.waitUntilReady(player)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = item.status == .failed
                ? (request.item.isAudio ? "This track could not be played. Check its format and try again." : "This video could not be played. Try Convert to compatible video.")
                : "The media did not become ready. Check the server connection."
            return
        }
        guard !closed, !Task.isCancelled else { return }
        if request.startPositionSeconds > 0 {
            await player.seek(to: CMTime(seconds: request.startPositionSeconds, preferredTimescale: 600),
                              toleranceBefore: .zero, toleranceAfter: .zero)
        }
        guard !closed, !Task.isCancelled else { return }
        didSeek = true
        updatePosition()
        enqueue(.start, paused: !autoplayWhenReady)
        let progressInterval = request.item.isAudio
            ? CMTime(seconds: 1, preferredTimescale: 600)
            : NativeVideoSource.progressObservationInterval
        timeObserver = player.addPeriodicTimeObserver(forInterval: progressInterval, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed, !self.hasFinished else { return }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.updatePosition()
                let shouldReport = self.request.item.isAudio
                    ? abs(self.elapsedSeconds - self.lastReport) >= 5
                    : self.elapsedSeconds != self.lastReport
                if shouldReport {
                    self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
                }
                if self.player.currentItem?.status == .failed {
                    self.errorMessage = "Playback stopped. Check your connection and try again."
                }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed, !self.hasFinished else { return }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        installEndObserver()
        if autoplayWhenReady { player.play() }
    }

    func pause() {
        guard !closed else { return }
        if let audioOwner { audioOwner.pause(); return }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        player.pause()
        isPlaying = false
        guard !hasFinished else { return }
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func togglePlayback() {
        if let audioOwner { Task { await audioOwner.toggle() }; return }
        guard didSeek, !closed, !hasFinished else { return }
        if player.timeControlStatus == .playing { pause() }
        else { autoplayWhenReady = true; player.play(); isPlaying = true }
    }

    func setActive(_ active: Bool) {
        guard isActive != active else { return }
        isActive = active
        // Video remains paused when returning; AVKit must wait for a new Play action.
        if !request.item.isAudio { pause() }
    }

    func seek(to seconds: Double) {
        if let audioOwner { Task { await audioOwner.seek(seconds) }; return }
        guard didSeek, !closed, !hasFinished else { return }
        elapsedSeconds = min(max(0, seconds), request.item.durationSeconds)
        player.seek(to: CMTime(seconds: elapsedSeconds, preferredTimescale: 600))
    }

    func cycleRepeat() {
        if let audioOwner { audioOwner.cycleRepeat(); return }
        switch repeatMode { case .off: repeatMode = .all; case .all: repeatMode = .one; case .one: repeatMode = .off }
    }

    func moveQueued(at source: Int, to target: Int) {
        guard source > queueIndex, target > queueIndex, queue.indices.contains(source), queue.indices.contains(target) else { return }
        if let audioOwner { audioOwner.move(from: source, to: target); return }
        reflectQueueMove(from: source, to: target)
    }

    func reflectQueueMove(from source: Int, to target: Int) {
        guard queue.indices.contains(source), queue.indices.contains(target) else { return }
        let item = queue.remove(at: source), occurrence = occurrenceIDs.remove(at: source)
        queue.insert(item, at: target); occurrenceIDs.insert(occurrence, at: target)
        rebuildQueueRows()
    }
    func reflectQueueRemoval(at index: Int) {
        guard queue.indices.contains(index) else { return }
        queue.remove(at: index); occurrenceIDs.remove(at: index)
        rebuildQueueRows()
    }

    func removeQueued(at index: Int) {
        guard queue.indices.contains(index), index > queueIndex else { return }
        if let audioOwner { audioOwner.remove(at: index); return }
        reflectQueueRemoval(at: index)
    }

    func configureAudioQueue(_ items: [MediaItem], index: Int, repeatMode: String) {
        if queue != items {
            if queue.map(\.id) != items.map(\.id) { occurrenceIDs = items.map { _ in UUID() } }
            queue = items
            rebuildQueueRows()
        }
        if queueIndex != index { queueIndex = index }
        let nextRepeatMode: RepeatMode = repeatMode == "one" ? .one : repeatMode == "all" ? .all : .off
        if self.repeatMode != nextRepeatMode { self.repeatMode = nextRepeatMode }
    }

    func receiveAudioTrack(_ request: PlaybackRequest, index: Int) {
        if reportedAudioStart { enqueue(.stop, paused: true) }
        self.request = request
        queueIndex = index
        elapsedSeconds = 0
        reportedAudioStart = false
        didReportCompletion = false
        errorMessage = nil
        updateNowPlaying()
    }

    func receiveAudioState(_ state: NativeMusicTransport.State) {
        let wasPlaying = isPlaying
        elapsedSeconds = state.position
        isPlaying = state.playing
        hasFinished = state.finished
        errorMessage = state.error
        if state.playing && !reportedAudioStart {
            reportedAudioStart = true
            enqueue(.start, paused: false)
        } else if reportedAudioStart && (abs(elapsedSeconds - lastReport) >= 5 || wasPlaying != isPlaying) {
            enqueue(.progress, paused: !isPlaying)
        }
        if state.finished && !didReportCompletion && reportedAudioStart {
            didReportCompletion = true
            enqueue(.stop, paused: true)
        }
        updateNowPlaying()
    }

    func playQueued(offset: Int) async {
        let nextIndex = queueIndex + offset
        if let audioOwner { await audioOwner.step(offset); return }
        guard queue.indices.contains(nextIndex), !closed, !isReplaying else { return }
        isReplaying = true
        defer { isReplaying = false }
        do {
            let next = try await store.preparePlayback(for: queue[nextIndex])
            guard !closed, !Task.isCancelled else { return }
            updatePosition()
            if !didReportCompletion { enqueue(.stop, paused: true) }
            player.pause()
            isPlaying = false
            if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
            controlObservation = nil
            if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
            request = next
            queueIndex = nextIndex
            elapsedSeconds = next.startPositionSeconds
            didSeek = false
            started = false
            hasFinished = false
            didReportCompletion = false
            lastReport = -1
            autoplayWhenReady = next.item.isAudio || isActive
            errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(url: next.url))
            await start()
        } catch {
            errorMessage = "The next track could not start. Check your connection and try again."
        }
    }

    func replay() async {
        if let audioOwner { await audioOwner.select(queueIndex); return }
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
        do {
            let item = try await videoSource.prepare(url: newRequest.url, usesClientDemux: newRequest.usesClientDemux)
            guard !closed, !Task.isCancelled else { videoSource.stop(); return }
            player.replaceCurrentItem(with: item)
        } catch {
            failReplay("Replay could not start. Try Convert to compatible video.")
            return
        }
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
        videoSource.stop()
        updatePosition()
        audioTransport?.stop()
        audioTransport = nil
        player.pause()
        isPlaying = false
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        if !didReportCompletion && (!request.item.isAudio || reportedAudioStart) {
            enqueue(.stop, paused: true)
        }
        player.replaceCurrentItem(with: nil)
        if request.item.isAudio {
            for entry in remoteTargets { entry.command.removeTarget(entry.target) }
            remoteTargets.removeAll()
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        }
    }

    private func finishPlayback() {
        guard !closed, !hasFinished else { return }
        updatePosition()
        hasFinished = true
        videoSource.stop()
        autoplayWhenReady = false
        player.pause()
        isPlaying = false
        // The stopped server session is no longer a valid replay source (especially for HLS).
        // Remove it so AVKit can't restart the obsolete item behind the fresh Replay action.
        player.replaceCurrentItem(with: nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
        updateNowPlaying()
        if request.item.isAudio {
            if repeatMode == .one { Task { await replay() } }
            else if hasNext { Task { await playQueued(offset: 1) } }
            else if repeatMode == .all && !queue.isEmpty { Task { await playQueued(offset: -queueIndex) } }
        }
    }

    private func failReplay(_ message: String) {
        videoSource.stop()
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
        updateNowPlaying()
    }

    private func updateNowPlaying() {
        guard request.item.isAudio, !closed else { return }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: request.item.name,
            MPMediaItemPropertyArtist: request.item.artists?.joined(separator: ", ") ?? request.item.albumArtist ?? "",
            MPMediaItemPropertyAlbumTitle: request.item.album ?? "",
            MPMediaItemPropertyPlaybackDuration: request.item.durationSeconds,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsedSeconds,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        let commands = MPRemoteCommandCenter.shared()
        commands.nextTrackCommand.isEnabled = hasNext
        commands.previousTrackCommand.isEnabled = hasPrevious
    }

    private func installRemoteControls() {
        guard remoteTargets.isEmpty else { return }
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.isEnabled = true
        commands.pauseCommand.isEnabled = true
        commands.togglePlayPauseCommand.isEnabled = true
        commands.changePlaybackPositionCommand.isEnabled = true
        remoteTargets = [
            (commands.playCommand, commands.playCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() }
                    else if !self.isPlaying { self.togglePlayback() }
                }
                return .success
            }),
            (commands.pauseCommand, commands.pauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in self?.pause() }
                return .success
            }),
            (commands.togglePlayPauseCommand, commands.togglePlayPauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() } else { self.togglePlayback() }
                }
                return .success
            }),
            (commands.nextTrackCommand, commands.nextTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: 1) }
                return .success
            }),
            (commands.previousTrackCommand, commands.previousTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: -1) }
                return .success
            }),
            (commands.changePlaybackPositionCommand, commands.changePlaybackPositionCommand.addTarget { [weak self] event in
                guard let position = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
                let seconds = position.positionTime
                Task { @MainActor in self?.seek(to: seconds) }
                return .success
            }),
        ]
        updateNowPlaying()
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
                    syncError = report.request.item.isAudio ? "Listening progress could not sync. Check the server connection." : "Watch progress could not sync. Check the server connection."
                }
            }
            reportQueue = nil
        }
    }

}

struct NativeNowPlayingBar: View {
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var expanded = false

    var body: some View {
        if let playback = audioSession.playback {
            HStack(spacing: 16) {
                Button { expanded = true } label: {
                    HStack(spacing: 14) {
                        AsyncImage(url: store.imageURL(for: playback.request.item, maxWidth: 160)) { image in
                            image.resizable().scaledToFill()
                        } placeholder: {
                            Image(systemName: "music.note").frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        .frame(width: artworkSize, height: artworkSize)
                        .clipShape(.rect(cornerRadius: 8))
                        .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(playback.request.item.name).font(.headline).lineLimit(1)
                            Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .contentShape(.rect)
                }
                .accessibilityLabel("Now playing: \(playback.request.item.name)")
                .accessibilityHint("Open playback and queue controls")
                .accessibilityIdentifier("nowPlayingBar")

                Button(playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play", systemImage: playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
                    if playback.hasFinished { Task { await playback.replay() } }
                    else { playback.togglePlayback() }
                }
                .labelStyle(.iconOnly)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(playback.isReplaying)
                .accessibilityIdentifier("nowPlayingPauseButton")
            }
            #if os(tvOS)
            .buttonStyle(.bordered)
            .padding(.horizontal, 80)
            .padding(.vertical, 12)
            .focusSection()
            #else
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            #endif
            .foregroundStyle(LuminaTheme.ink)
            .background(.regularMaterial)
            .fullScreenCover(isPresented: $expanded) {
                PlaybackScreen(playback: playback, store: store)
            }
        }
    }

    private var artworkSize: CGFloat {
        #if os(tvOS)
        64
        #else
        44
        #endif
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
