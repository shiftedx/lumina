import AVFoundation
import Darwin

/// Two decoded slots at most. Unknown or large files use AVQueuePlayer instead.
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
    private final class PCM: @unchecked Sendable {
        let buffer: AVAudioPCMBuffer
        init(_ buffer: AVAudioPCMBuffer) { self.buffer = buffer }
    }
    private struct Slot {
        let pcm: PCM
        let index: Int
        let node: Int
        let hostStart: UInt64
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

    public init(tracks: [MusicTrack], index: Int, position: Double, source: @escaping (Int) async throws -> PlaybackRequest) {
        self.tracks = tracks
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
        self.normalize = normalize
        self.shuffle = shuffle
        self.volume = min(1, max(0, volume))
        engine.mainMixerNode.outputVolume = self.volume
        streaming?.volume = self.volume
        if changed, state.playing { invalidateLookahead() }
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
               let pcm = try await Self.prepare(request.url) {
                guard token == generation, !Task.isCancelled else { return }
                mode = .decoded; onMode?(mode)
                if playing { try beginDecoded(pcm, index: state.index, offset: state.position) }
            } else {
                guard token == generation, !Task.isCancelled else { return }
                mode = .streaming; onMode?(mode)
                await beginStreaming(request, playing: playing)
            }
            state.playing = playing && (mode == .decoded || streaming?.timeControlStatus == .playing); state.finished = false; state.error = nil
            onChange?(state)
            if playing { startClock(); prepareNext() }
        } catch {
            guard token == generation, !Task.isCancelled else { return }
            wantsPlayback = false; state.playing = false
            state.error = error.localizedDescription
            onChange?(state)
        }
    }

    /// A handoff must acknowledge rendered playback, not merely a request to play.
    public func confirmPlaying(timeout: Double = 10) async -> Bool {
        let token = generation
        let deadline = ContinuousClock.now.advanced(by: .seconds(min(10, max(0, timeout))))
        repeat {
            guard token == generation, wantsPlayback, !Task.isCancelled, state.error == nil else { return false }
            if mode == .decoded, let current, mach_absolute_time() >= current.hostStart,
               engine.isRunning, nodes[current.node].isPlaying, nodes[current.node].lastRenderTime != nil { return true }
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
        state.playing = false
        stopRuntime()
        onChange?(state)
    }
    public func resume() async { await start() }
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
        invalidateLookahead()
    }
    public func stop() { pause(); state.finished = true; onChange?(state) }

    private func stopRuntime() {
        generation += 1
        wantsPlayback = false; blockedAdvance = nil
        loading?.cancel(); loading = nil
        preparingNext = false; fallbackNext = nil
        timer?.cancel(); timer = nil
        nodes.forEach { $0.stop() }
        engine.stop()
        if let streamObserver { streaming?.removeTimeObserver(streamObserver) }
        streamObserver = nil
        streaming?.pause(); streaming?.removeAllItems(); streaming = nil
        streamItems.removeAll(); streamIndex = nil
        current = nil; next = nil; retiring = nil
    }

    private func gain(at index: Int) -> Float {
        guard tracks.indices.contains(index) else { return 1 }
        let track = tracks[index]
        let sameAlbum = track.albumID != nil && tracks.allSatisfy { $0.albumID == track.albumID }
        let ordered = zip(tracks, tracks.dropFirst()).allSatisfy { a, b in
            (a.disc ?? 1) < (b.disc ?? 1) || (a.disc ?? 1) == (b.disc ?? 1) && (a.number ?? Int.max) <= (b.number ?? Int.max)
        }
        return MusicPlaybackPolicy.gain(enabled: normalize, manual: track.gainIsManual, trackGain: track.gainDB,
            albumGain: track.albumGainDB, peak: track.peak, albumPeak: track.albumPeak, sequentialAlbum: sameAlbum && ordered && !shuffle)
    }

    private func applyGain(index: Int, node: Int, envelope: Float) {
        // AVAudioMixing.volume is limited to 0...1. EQ stages carry positive
        // ReplayGain as decibels; the player volume remains only a fade envelope.
        let linear = gain(at: index)
        let db = linear > 0 ? 20 * log10(linear) : -192
        let first = min(24, max(-96, db))
        gainStages[node][0].globalGain = first
        gainStages[node][1].globalGain = min(24, max(-96, db - first))
        nodes[node].volume = linear > 0 ? min(1, max(0, envelope)) : 0
    }

    private func beginDecoded(_ pcm: PCM, index: Int, offset: Double) throws {
        let sliced = try Self.sliced(pcm, offset: offset)
        let start = mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.08)
        current = Slot(pcm: sliced, index: index, node: 0, hostStart: start, offset: offset,
            length: Double(sliced.buffer.frameLength) / 48_000)
        engine.prepare(); try engine.start()
        applyGain(index: index, node: 0, envelope: 1)
        nodes[0].scheduleBuffer(sliced.buffer, completionHandler: nil)
        nodes[0].play(at: AVAudioTime(hostTime: start))
    }

    private func prepareNext() {
        guard state.playing, !preparingNext, next == nil, retiring == nil else { return }
        let candidate = MusicPlaybackPolicy.nextIndex(current: state.index, count: tracks.count, repeatMode: repeatMode)
        guard allowsAdvance?(state.index, candidate) != false else { blockedAdvance = (state.index, candidate); return }
        blockedAdvance = nil
        guard let index = candidate else { return }
        if mode == .streaming { prepareStreamingNext(index); return }
        guard let playing = current else { return }
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
                      let pcm = try await Self.prepare(request.url) else {
                    fallbackNext = index
                    return
                }
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled, current?.hostStart == playing.hostStart else { return }
                let overlap = min(crossfade, playing.length / 2, Double(pcm.buffer.frameLength) / 48_000 / 2)
                let wanted = playing.end - AVAudioTime.hostTime(forSeconds: overlap)
                let host = max(wanted, mach_absolute_time() + AVAudioTime.hostTime(forSeconds: 0.04))
                let slot = Slot(pcm: pcm, index: index, node: 1 - playing.node, hostStart: host, offset: 0,
                    length: Double(pcm.buffer.frameLength) / 48_000)
                next = slot
                applyGain(index: index, node: slot.node, envelope: overlap > 0 ? 0 : 1)
                nodes[slot.node].scheduleBuffer(pcm.buffer, completionHandler: nil)
                nodes[slot.node].play(at: AVAudioTime(hostTime: host))
            } catch {
                guard token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled else { return }
                if (error as? URLError)?.code == .userAuthenticationRequired {
                    pause()
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
                if let request = requests[index] { onTrack?(request, index) }
                prepareNext()
            }
            if streaming.currentItem == nil { ended() }
            else if streaming.currentItem?.status == .failed {
                state.error = "This audio format could not stream. Try a compatible copy."
                state.playing = false; wantsPlayback = false
            }
            if state.playing { prepareNext() }
            streaming.volume = volume
            onChange?(state)
            return
        }
        let now = mach_absolute_time()
        if let queued = next, now >= queued.hostStart {
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
                Task { [weak self] in await self?.select(index) }
            } else if !preparingNext && MusicPlaybackPolicy.nextIndex(current: state.index, count: tracks.count, repeatMode: repeatMode) == nil { ended() }
        }
        onChange?(state)
    }

    private func ended() {
        if let blocked = blockedAdvance {
            state.playing = false
            stopRuntime(); onChange?(state)
            onBlockedAdvance?(blocked.from, blocked.to)
            return
        }
        nodes.forEach { $0.stop() }; engine.stop(); wantsPlayback = false
        state.finished = true; state.playing = false
        timer?.cancel(); timer = nil
        onChange?(state)
    }

    private func beginStreaming(_ request: PlaybackRequest, playing: Bool) async {
        let item = AVPlayerItem(url: request.url)
        item.preferredForwardBufferDuration = 8
        let queue = AVQueuePlayer(items: [item])
        streamItems[ObjectIdentifier(item)] = state.index; streamIndex = state.index; streamCurrentItem = ObjectIdentifier(item)
        streaming = queue
        queue.volume = volume
        if state.position > 0 { await queue.seek(to: CMTime(seconds: state.position, preferredTimescale: 600)) }
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
                streamItems[ObjectIdentifier(item)] = index
                if streaming.canInsert(item, after: nil) { streaming.insert(item, after: nil) }
            } catch {
                if token == generation, lookaheadToken == lookaheadGeneration, !Task.isCancelled { state.error = "The next track is unavailable."; onChange?(state) }
            }
        }
    }

    nonisolated private static func prepare(_ url: URL) async throws -> PCM? {
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
        return await Task.detached(priority: .userInitiated) {
            do {
                let file = try AVAudioFile(forReading: local)
                let frames = file.length
                let input = file.processingFormat
                guard frames > 0, frames <= Int64(UInt32.max),
                      Double(frames) * Double(input.channelCount) * 4 <= Double(MusicPlaybackPolicy.maximumTrackBytes) else { return nil as PCM? }
                let output = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 2)!
                let count = ceil(Double(frames) * 48_000 / input.sampleRate) + 1
                guard count * 8 + Double(frames) * Double(input.channelCount) * 4 <= Double(MusicPlaybackPolicy.maximumTrackBytes),
                      let source = AVAudioPCMBuffer(pcmFormat: input, frameCapacity: AVAudioFrameCount(frames)),
                      let target = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: AVAudioFrameCount(count)),
                      let converter = AVAudioConverter(from: input, to: output) else { return nil }
                try file.read(into: source)
                var consumed = false
                var error: NSError?
                let status = converter.convert(to: target, error: &error) { _, result in
                    if consumed { result.pointee = .endOfStream; return nil }
                    consumed = true; result.pointee = .haveData; return source
                }
                guard status != .error, target.frameLength > 0 else { return nil }
                return PCM(target)
            } catch { return nil }
        }.value
    }

    private static func sliced(_ pcm: PCM, offset: Double) throws -> PCM {
        guard offset > 0 else { return pcm }
        let start = min(Int(pcm.buffer.frameLength) - 1, max(0, Int(offset * 48_000)))
        let count = Int(pcm.buffer.frameLength) - start
        guard let buffer = AVAudioPCMBuffer(pcmFormat: pcm.buffer.format, frameCapacity: AVAudioFrameCount(count)),
              let source = pcm.buffer.floatChannelData, let target = buffer.floatChannelData else { throw URLError(.cannotDecodeContentData) }
        buffer.frameLength = AVAudioFrameCount(count)
        for channel in 0..<Int(buffer.format.channelCount) {
            target[channel].update(from: source[channel].advanced(by: start), count: count)
        }
        return PCM(buffer)
    }
}
