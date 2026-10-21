import AVFoundation
import Darwin

/// Two file-backed engine slots at most. Unknown or large files use AVQueuePlayer instead.
/// Only the decoded path schedules its successor against the same audio clock.
@MainActor
public final class NativeMusicTransport {
    public struct State: Sendable {
        public var index: Int
        public var position: Double
        public var playing: Bool
        public var finished: Bool
        public var error: String?
        public init(index: Int, position: Double, playing: Bool, finished: Bool = false, error: String? = nil) {
            self.index = index; self.position = position; self.playing = playing; self.finished = finished; self.error = error
        }
    }
    private final class PreparedAudio: @unchecked Sendable {
        let file: AVAudioFile
        let temporary: URL?
        var duration: Double { Double(file.length) / file.processingFormat.sampleRate }
        init(file: AVAudioFile, temporary: URL?) { self.file = file; self.temporary = temporary }
        deinit { if let temporary { try? FileManager.default.removeItem(at: temporary) } }
    }
    private struct Slot {
        let audio: PreparedAudio
        let index: Int
        let node: Int
        var hostStart: UInt64
        let offset: Double
        let length: Double
        var end: UInt64 { hostStart + AVAudioTime.hostTime(forSeconds: length) }
    }
    public enum Mode: Sendable { case decoded, streaming }
    public private(set) var mode = Mode.streaming
    public private(set) var state: State
    public var onChange: ((State) -> Void)?
    public var onTrack: ((PlaybackRequest, Int) -> Void)?
    public var onMode: ((Mode) -> Void)?
    public var allowsAdvance: ((Int, Int?) -> Bool)?
    public var onBlockedAdvance: ((Int, Int?) -> Void)?
    public var source: (Int) async throws -> PlaybackRequest
    private var tracks: [MusicTrack]
    private var gains: [Float]
    private var lastTickState: State?
    private var requests: [Int: PlaybackRequest] = [:]
    private let engine = AVAudioEngine()
    private let nodes = [AVAudioPlayerNode(), AVAudioPlayerNode()]
    private let gainStages = (0..<2).map { _ in [AVAudioUnitEQ(numberOfBands: 0), AVAudioUnitEQ(numberOfBands: 0)] }
    private var current: Slot?
    private var next: Slot?
    private var retiring: Slot?
    private var streaming: AVQueuePlayer?
    private var streamItems: [ObjectIdentifier: Int] = [:]
    private var streamObserver: Any?
    private var timer: Task<Void, Never>?
    private var loading: Task<Void, Never>?
    private var generation = 0
    private var lookaheadGeneration = 0
    private var repeatMode = "off"
    private var crossfade = 0.0
    private var normalize = true
    private var shuffle = false
    private var volume: Float = 0.8
    private var streamIndex: Int?
    private var streamCurrentItem: ObjectIdentifier?
    private var fallbackNext: Int?
    private var preparingNext = false
    private var blockedAdvance: (from: Int, to: Int?)?
    private var wantsPlayback = false
    private var pausedAt: UInt64?
    private var playbackStartsAt: UInt64 = 0

    public init(tracks: [MusicTrack], index: Int, position: Double, source: @escaping (Int) async throws -> PlaybackRequest) {
        self.tracks = tracks
        self.gains = MusicPlaybackPolicy.queueGains(tracks, enabled: true, shuffle: false)
        self.state = State(index: index, position: max(0, position), playing: false)
        self.source = source
        let format = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 2)!
        for index in nodes.indices {
            let node = nodes[index], stages = gainStages[index]
            engine.attach(node)
            stages.forEach { engine.attach($0) }
            engine.connect(node, to: stages[0], format: format)
            engine.connect(stages[0], to: stages[1], format: format)
            engine.connect(stages[1], to: engine.mainMixerNode, format: format)
        }
    }

    public func configure(repeatMode: String, crossfade: Double, normalize: Bool, volume: Float, shuffle: Bool = false) {
        let changed = self.repeatMode != repeatMode || self.crossfade != crossfade
        self.repeatMode = repeatMode
        self.crossfade = min(12, max(0, crossfade))
        if self.normalize != normalize || self.shuffle != shuffle {
            gains = MusicPlaybackPolicy.queueGains(tracks, enabled: normalize, shuffle: shuffle)
        }
        self.normalize = normalize
        self.shuffle = shuffle
        self.volume = min(1, max(0, volume))
        engine.mainMixerNode.outputVolume = self.volume
        streaming?.volume = self.volume
        if changed { invalidateLookahead() }
        tick()
    }

    public func start(playing: Bool = true) async {
        stopRuntime()
        wantsPlayback = playing
        let token = generation
        guard tracks.indices.contains(state.index) else { return }
        do {
            let request = try await source(state.index)
            guard token == generation, !Task.isCancelled else { return }
            requests = [state.index: request]
            onTrack?(request, state.index)
            let track = tracks[state.index]
            if !playing {
                state.playing = false; state.finished = false; state.error = nil
                onChange?(state)
                return
            }
            if MusicPlaybackPolicy.canBuffer(duration: track.duration, sampleRate: track.sampleRate, channels: track.channels),
               let audio = try await Self.prepare(request.url) {
                guard token == generation, !Task.isCancelled else { return }
                mode = .decoded; onMode?(mode)
                if playing { try beginDecoded(audio, index: state.index, offset: state.position) }
            } else {
                guard token == generation, !Task.isCancelled else { return }
                mode = .streaming; onMode?(mode)
                await beginStreaming(request, playing: playing)
                guard token == generation, !Task.isCancelled else { return }
            }
            state.playing = playing && (mode == .decoded || streaming?.timeControlStatus == .playing); state.finished = false; state.error = nil
            onChange?(state)
            if playing { startClock(); prepareNext() }
        } catch {
            guard token == generation, !Task.isCancelled else { return }
            stopRuntime(); state.playing = false
            state.error = error.localizedDescription
            onChange?(state)
        }
    }

    /// A handoff must acknowledge rendered playback, not merely a request to play.
    public func confirmPlaying(timeout: Double = 10) async -> Bool {
        let token = generation
        let deadline = ContinuousClock.now.advanced(by: .seconds(min(10, max(0, timeout))))
        repeat {
            guard token == generation, wantsPlayback, !Task.isCancelled else { return false }
            if mode == .decoded, let current, mach_absolute_time() >= playbackStartsAt,
               engine.isRunning, nodes[current.node].isPlaying,
               let rendered = nodes[current.node].lastRenderTime, rendered.hostTime >= playbackStartsAt { return true }
            if mode == .streaming, streaming?.currentItem?.status == .readyToPlay,
               streaming?.timeControlStatus == .playing {
                state.playing = true
                return true
            }
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return false }
        } while ContinuousClock.now < deadline
        return false
    }

    public func pause() {
        tick()
        if wantsPlayback, current != nil || streaming?.currentItem != nil {
            generation += 1
            lookaheadGeneration += 1
            loading?.cancel(); loading = nil; preparingNext = false
            timer?.cancel(); timer = nil
            pausedAt = mach_absolute_time()
            wantsPlayback = false
            nodes.forEach { $0.pause() }
            engine.pause()
            streaming?.pause()
            lastTickState = nil
        } else if pausedAt == nil { stopRuntime() }
        state.playing = false
        onChange?(state)
    }

    public func resume() async {
        guard !wantsPlayback else { return }
        guard let pausedAt, !state.finished else { await start(); return }
        do {
            if mode == .decoded, current != nil {
                try engine.start()
                let resumeAt = mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.08)
                playbackStartsAt = resumeAt
                let shift = resumeAt - pausedAt
                // The sample timelines stay paused; move host-clock boundaries by
                // the same interval so queued starts and overlapping fades stay aligned.
                current?.hostStart += shift
                next?.hostStart += shift
                retiring?.hostStart += shift
                for slot in [current, next, retiring].compactMap({ $0 }) {
                    nodes[slot.node].play(at: AVAudioTime(hostTime: max(resumeAt, slot.hostStart)))
                }
                state.playing = true
            } else if let streaming, let item = streaming.currentItem, item.status != .failed {
                streaming.play()
                state.playing = streaming.timeControlStatus == .playing
            } else { await start(); return }
            self.pausedAt = nil
            wantsPlayback = true
            tick()
            if wantsPlayback { startClock(); prepareNext() }
        } catch {
            stopRuntime()
            state.playing = false; state.error = error.localizedDescription
            onChange?(state)
        }
    }
    public func seek(_ seconds: Double) async {
        let wasPlaying = wantsPlayback
        state.position = min(max(0, seconds), tracks[state.index].duration ?? max(0, seconds))
        await start(playing: wasPlaying)
    }
    public func select(_ index: Int, position: Double = 0, playing: Bool = true) async {
        guard tracks.indices.contains(index) else { return }
        state.index = index; state.position = max(0, position)
        await start(playing: playing)
    }
    public func replaceUpcoming(_ tracks: [MusicTrack]) {
        self.tracks = tracks
        gains = MusicPlaybackPolicy.queueGains(tracks, enabled: normalize, shuffle: shuffle)
        invalidateLookahead()
    }
    public func stop() { pause(); stopRuntime(); state.finished = true; onChange?(state) }

    private func stopRuntime() {
        generation += 1
        wantsPlayback = false; blockedAdvance = nil; pausedAt = nil; playbackStartsAt = 0
        lastTickState = nil
        loading?.cancel(); loading = nil
        preparingNext = false; fallbackNext = nil
        timer?.cancel(); timer = nil
        nodes.forEach { $0.stop() }
        engine.stop()
        if let streamObserver { streaming?.removeTimeObserver(streamObserver) }
        streamObserver = nil
        streaming?.pause(); streaming?.removeAllItems(); streaming = nil
        streamItems.removeAll(); streamIndex = nil; streamCurrentItem = nil
        requests.removeAll()
        current = nil; next = nil; retiring = nil
    }

    private func applyGain(index: Int, node: Int, envelope: Float) {
        // AVAudioMixing.volume is limited to 0...1. EQ stages carry positive
        // ReplayGain as decibels; the player volume remains only a fade envelope.
        let linear = gains.indices.contains(index) ? gains[index] : 1
        let db = linear > 0 ? 20 * log10(linear) : -192
        let first = min(24, max(-96, db))
        let second = min(24, max(-96, db - first))
        let level = linear > 0 ? min(1, max(0, envelope)) : 0
        if gainStages[node][0].globalGain != first { gainStages[node][0].globalGain = first }
        if gainStages[node][1].globalGain != second { gainStages[node][1].globalGain = second }
        if nodes[node].volume != level { nodes[node].volume = level }
    }

    private func beginDecoded(_ audio: PreparedAudio, index: Int, offset: Double) throws {
        let file = audio.file
        let rate = file.processingFormat.sampleRate
        let frame = AVAudioFramePosition(min(Double(file.length - 1), max(0, offset) * rate))
        let count = AVAudioFrameCount(file.length - frame)
        nodes[0].scheduleSegment(file, startingFrame: frame, frameCount: count, at: nil, completionHandler: nil)
        nodes[0].prepare(withFrameCount: 4_096)
        engine.prepare(); try engine.start()
        let start = mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.08)
        playbackStartsAt = start
        current = Slot(audio: audio, index: index, node: 0, hostStart: start, offset: Double(frame) / rate,
            length: Double(count) / rate)
        applyGain(index: index, node: 0, envelope: 1)
        nodes[0].play(at: AVAudioTime(hostTime: start))
    }

    private func prepareNext() {
        guard state.playing, !preparingNext, fallbackNext == nil, next == nil, retiring == nil else { return }
        let candidate = MusicPlaybackPolicy.nextIndex(current: state.index, count: tracks.count, repeatMode: repeatMode)
        guard allowsAdvance?(state.index, candidate) != false else { blockedAdvance = (state.index, candidate); return }
        blockedAdvance = nil
        guard let index = candidate else { return }
        if mode == .streaming { prepareStreamingNext(index); return }
        guard let slot = current else { return }
        // A suspended source must not retain the outgoing file after Stop.
        let playing = (hostStart: slot.hostStart, length: slot.length, end: slot.end, node: slot.node)
        preparingNext = true
        let token = generation
        let lookaheadToken = lookaheadGeneration
        loading = Task { [weak self] in
            guard let self else { return }
            defer { if token == generation, lookaheadToken == lookaheadGeneration { preparingNext = false } }
            do {
                let request = try await source(index)
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled else { return }
                requests = requests.filter { $0.key == state.index }
                requests[index] = request
                let track = tracks[index]
                guard MusicPlaybackPolicy.canBuffer(duration: track.duration, sampleRate: track.sampleRate, channels: track.channels),
                      let audio = try await Self.prepare(request.url) else {
                    fallbackNext = index
                    return
                }
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled, current?.hostStart == playing.hostStart else { return }
                let overlap = min(crossfade, playing.length / 2, audio.duration / 2)
                let node = 1 - playing.node
                nodes[node].scheduleSegment(audio.file, startingFrame: 0, frameCount: AVAudioFrameCount(audio.file.length),
                    at: nil, completionHandler: nil)
                nodes[node].prepare(withFrameCount: 4_096)
                let wanted = playing.end - AVAudioTime.hostTime(forSeconds: overlap)
                let host = max(wanted, mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.04))
                let slot = Slot(audio: audio, index: index, node: node, hostStart: host, offset: 0, length: audio.duration)
                next = slot
                applyGain(index: index, node: slot.node, envelope: overlap > 0 ? 0 : 1)
                nodes[slot.node].play(at: AVAudioTime(hostTime: host))
            } catch {
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled else { return }
                if (error as? URLError)?.code == .userAuthenticationRequired {
                    stop()
                    state.error = "Access to this track was denied. Sign in again before continuing."
                    onChange?(state)
                    return
                }
                fallbackNext = index
                state.error = "The next track could not preload. Streaming will be tried at the boundary."
                onChange?(state)
            }
        }
    }

    private func invalidateLookahead() {
        lookaheadGeneration += 1
        blockedAdvance = nil
        loading?.cancel(); loading = nil; preparingNext = false; fallbackNext = nil
        if let next { nodes[next.node].stop(); self.next = nil }
        if let streaming {
            for item in streaming.items().dropFirst() { streaming.remove(item) }
            streamItems = streamItems.filter { $0.key == streamCurrentItem }
        }
        if state.playing { prepareNext() }
    }

    private func startClock() {
        timer?.cancel()
        timer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self else { return }
                tick()
            }
        }
    }

    private func tick() {
        guard wantsPlayback else { return }
        if mode == .streaming {
            guard let streaming else { return }
            state.playing = streaming.timeControlStatus == .playing
            let seconds = streaming.currentTime().seconds
            if seconds.isFinite { state.position = max(0, seconds) }
            if let item = streaming.currentItem, let index = streamItems[ObjectIdentifier(item)], ObjectIdentifier(item) != streamCurrentItem {
                streamCurrentItem = ObjectIdentifier(item); streamIndex = index; state.index = index
                let retained = Set(streaming.items().map { ObjectIdentifier($0) })
                streamItems = streamItems.filter { retained.contains($0.key) }
                if let request = requests[index] { onTrack?(request, index) }
                prepareNext()
            }
            if streaming.currentItem == nil { ended() }
            else if streaming.currentItem?.status == .failed {
                state.error = "This audio format could not stream. Try a compatible copy."
                state.playing = false
                stopRuntime()
            }
            if state.playing { prepareNext() }
            publishTick()
            return
        }
        let now = max(mach_absolute_time(), playbackStartsAt)
        if let queued = next, now >= queued.hostStart {
            if let outgoing = current {
                state.position = outgoing.offset + min(AVAudioTime.seconds(forHostTime: now - outgoing.hostStart), outgoing.length)
                onChange?(state)
            }
            retiring = current; current = queued; next = nil
            state.index = queued.index; state.position = 0
            if let request = requests[queued.index] { onTrack?(request, queued.index) }
        }
        guard let current else { return }
        let since = now >= current.hostStart ? AVAudioTime.seconds(forHostTime: now - current.hostStart) : 0
        state.position = current.offset + min(since, current.length)
        if let previous = retiring {
            let overlap = previous.end > current.hostStart ? AVAudioTime.seconds(forHostTime: previous.end - current.hostStart) : 0
            let fraction = overlap > 0 ? min(1, since / overlap) : 1
            applyGain(index: previous.index, node: previous.node, envelope: Float(cos(fraction * .pi / 2)))
            applyGain(index: current.index, node: current.node, envelope: Float(sin(fraction * .pi / 2)))
            if now >= previous.end { nodes[previous.node].stop(); retiring = nil; prepareNext() }
        } else { applyGain(index: current.index, node: current.node, envelope: 1) }
        if now >= current.end && next == nil {
            if let blocked = blockedAdvance {
                state.playing = false
                stopRuntime()
                onChange?(state)
                onBlockedAdvance?(blocked.from, blocked.to)
                return
            }
            if let index = fallbackNext {
                fallbackNext = nil
                let token = generation
                Task { [weak self] in
                    guard let self, token == generation, wantsPlayback else { return }
                    await select(index)
                }
            } else if !preparingNext && MusicPlaybackPolicy.nextIndex(current: state.index, count: tracks.count, repeatMode: repeatMode) == nil { ended() }
        }
        publishTick()
    }

    private func publishTick() {
        // Crossfade envelopes need the audio clock's 100 ms tick. Browsing,
        // checkpoints and system Now Playing only need whole-second progress.
        if let previous = lastTickState,
           previous.index == state.index, previous.playing == state.playing,
           previous.finished == state.finished, previous.error == state.error,
           abs(previous.position - state.position) < 1 { return }
        lastTickState = state
        onChange?(state)
    }

    private func ended() {
        if let blocked = blockedAdvance {
            state.playing = false
            stopRuntime(); onChange?(state)
            onBlockedAdvance?(blocked.from, blocked.to)
            return
        }
        stopRuntime()
        state.finished = true; state.playing = false
        onChange?(state)
    }

    private func beginStreaming(_ request: PlaybackRequest, playing: Bool) async {
        let token = generation
        let item = AVPlayerItem(url: request.url)
        item.preferredForwardBufferDuration = 8
        let queue = AVQueuePlayer(items: [item])
        streamItems[ObjectIdentifier(item)] = state.index; streamIndex = state.index; streamCurrentItem = ObjectIdentifier(item)
        streaming = queue
        queue.volume = volume
        if state.position > 0 { await queue.seek(to: CMTime(seconds: state.position, preferredTimescale: 600)) }
        guard token == generation, !Task.isCancelled else { return }
        if playing { queue.play() }
    }
    private func prepareStreamingNext(_ index: Int) {
        guard let streaming, streaming.items().count < 2 else { return }
        preparingNext = true
        let token = generation
        let lookaheadToken = lookaheadGeneration
        loading = Task { [weak self] in
            guard let self else { return }
            defer { if token == generation, lookaheadToken == lookaheadGeneration { preparingNext = false } }
            do {
                let request = try await source(index)
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled else { return }
                requests = requests.filter { $0.key == state.index }
                requests[index] = request
                let item = AVPlayerItem(url: request.url); item.preferredForwardBufferDuration = 8
                if streaming.canInsert(item, after: nil) {
                    streaming.insert(item, after: nil)
                    streamItems[ObjectIdentifier(item)] = index
                } else { fallbackNext = index }
            } catch {
                if token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled {
                    if (error as? URLError)?.code == .userAuthenticationRequired {
                        stop()
                        state.error = "Access to this track was denied. Sign in again before continuing."
                        onChange?(state)
                        return
                    }
                    fallbackNext = index
                    state.error = "The next track is unavailable."; onChange?(state)
                }
            }
        }
    }

    nonisolated private static func prepare(_ url: URL) async throws -> PreparedAudio? {
        var temporary: URL?
        let local: URL
        if url.isFileURL { local = url }
        else {
            do {
                let file = try await BoundedMusicDownload(origin: url).download()
                temporary = file; local = file
            } catch let error as URLError where error.code == .dataLengthExceedsMaximum {
                return nil
            }
        }
        defer { if let temporary { try? FileManager.default.removeItem(at: temporary) } }
        try Task.checkCancellation()
        let size = try local.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size <= MusicPlaybackPolicy.maximumTrackBytes else { return nil }
        let downloaded = temporary
        let audio = await Task.detached(priority: .userInitiated) {
            do {
                let file = try AVAudioFile(forReading: local)
                let format = file.processingFormat
                guard file.length > 0, file.length <= Int64(UInt32.max),
                      format.sampleRate.isFinite, format.sampleRate > 0, (1...2).contains(format.channelCount) else { return nil as PreparedAudio? }
                // Keep the existing admission ceiling; no whole-track PCM is allocated.
                let convertedFrames = ceil(Double(file.length) * 48_000 / format.sampleRate) + 1
                guard convertedFrames * 8 + Double(file.length) * Double(format.channelCount) * 4
                    <= Double(MusicPlaybackPolicy.maximumTrackBytes) else { return nil }
                return PreparedAudio(file: file, temporary: downloaded)
            } catch { return nil }
        }.value
        try Task.checkCancellation()
        // Transfer the private download's lifetime to its active/scheduled slot.
        if audio != nil { temporary = nil }
        return audio
    }
}
