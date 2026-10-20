#if os(macOS)
import AVFoundation
import Network
import XCTest
@testable import LuminaCore

@MainActor
final class NativeMusicTransportTests: XCTestCase {
    func testDecodedQueueAdvancesDuplicateOccurrencesOnTheAudioClock() async throws {
        let fixture = try audioFixture(duration: 0.8)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let tracks = [fixture.track, fixture.track]
        var occurrences: [Int] = []
        let transport = transport(tracks: tracks, url: fixture.url)
        defer { transport.stop() }
        transport.onTrack = { _, index in occurrences.append(index) }
        await transport.start()
        XCTAssertNil(transport.state.error)
        XCTAssertEqual(transport.mode, .decoded)
        let confirmed = await transport.confirmPlaying()
        XCTAssertTrue(confirmed)
        try await observe(timeout: 5) { transport.state.index == 1 }
        XCTAssertEqual(occurrences, [0, 1], "The repeated recording is a distinct queue occurrence")
        XCTAssertTrue(transport.state.playing)
        try await observe(timeout: 5) { transport.state.finished }
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(transport.state.position, 0.8, accuracy: 0.05)
    }

    func testCrossfadeAdvancesBeforeTheOutgoingTrackEndsAndRetainsTheSuccessor() async throws {
        let fixture = try audioFixture(duration: 1.6)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let tracks = [fixture.track, fixture.track]
        let transport = transport(tracks: tracks, url: fixture.url, crossfade: 3)
        defer { transport.stop() }
        var maximumFirstPosition = 0.0
        transport.onChange = { state in
            if state.index == 0 { maximumFirstPosition = max(maximumFirstPosition, state.position) }
        }
        await transport.start()
        XCTAssertNil(transport.state.error)
        XCTAssertEqual(transport.mode, .decoded)
        try await observe(timeout: 5) { transport.state.index == 1 }
        // Short sources cap a requested three-second fade at half a track.
        // Observe the real running engine rather than depending on exact timer delivery.
        XCTAssertGreaterThan(maximumFirstPosition, 0.3)
        XCTAssertLessThan(maximumFirstPosition, 1.4)
        try await observe(timeout: 5) { transport.state.index == 1 && transport.state.position > 1 }
        XCTAssertNil(transport.state.error)
        try await observe(timeout: 5) { transport.state.finished }
        XCTAssertEqual(transport.state.position, 1.6, accuracy: 0.05)
    }

    func testPausedRestoreSeekAndResumeKeepTheRequestedPosition() async throws {
        let fixture = try audioFixture(duration: 2)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let transport = transport(tracks: [fixture.track], url: fixture.url)
        defer { transport.stop() }
        await transport.start(playing: false)
        let pausedConfirmation = await transport.confirmPlaying(timeout: 0.2)
        XCTAssertFalse(pausedConfirmation)
        await transport.seek(0.5)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(transport.state.position, 0.5, accuracy: 0.01)
        await transport.resume()
        XCTAssertNil(transport.state.error)
        XCTAssertEqual(transport.mode, .decoded)
        try await observe(timeout: 5) { transport.state.position > 0.75 }
        transport.pause()
        let paused = transport.state.position
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(transport.state.position, paused, accuracy: 0.01)
        await transport.resume()
        try await observe(timeout: 5) { transport.state.position > paused + 0.15 }
        XCTAssertNil(transport.state.error)
    }

    func testPendingWaitBoundaryFinishesCurrentWithoutPrefetchingOrStartingTheLaterTrack() async throws {
        let fixture = try audioFixture(duration: 0.6)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let transport = transport(tracks: [fixture.track, fixture.track], url: fixture.url)
        defer { transport.stop() }
        var played: [Int] = []
        var waiting = false
        transport.onTrack = { _, index in played.append(index) }
        transport.allowsAdvance = { _, next in next != 1 }
        transport.onBlockedAdvance = { from, next in
            XCTAssertEqual(from, 0); XCTAssertEqual(next, 1)
            waiting = true
        }
        await transport.start()
        let confirmed = await transport.confirmPlaying()
        XCTAssertTrue(confirmed)
        try await observe(timeout: 5) { waiting }
        XCTAssertEqual(played, [0])
        XCTAssertEqual(transport.state.index, 0)
        XCTAssertFalse(transport.state.playing)
        XCTAssertFalse(transport.state.finished, "A pending acquisition is a paused boundary, not a completed queue")
        XCTAssertEqual(transport.state.position, 0.6, accuracy: 0.05)
        // Choosing Skip and Play is explicit; refreshing a pending request never starts audio.
        transport.allowsAdvance = nil
        await transport.select(1)
        let resumed = await transport.confirmPlaying()
        XCTAssertTrue(resumed)
        XCTAssertEqual(played, [0, 1])
    }

    func testInvalidStreamingAssetNeverAcknowledgesAnOutputHandoff() async throws {
        var fixture = try audioFixture(duration: 0.5)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        try Data("This is not an audio recording".utf8).write(to: fixture.url)
        fixture.track.duration = nil // Unprobed sources deliberately use AVQueuePlayer.
        let transport = transport(tracks: [fixture.track], url: fixture.url)
        defer { transport.stop() }
        await transport.start()
        XCTAssertEqual(transport.mode, .streaming)
        let confirmed = await transport.confirmPlaying(timeout: 1)
        XCTAssertFalse(confirmed, "Play intent must not authorize stopping the previous output")
        XCTAssertFalse(transport.state.playing)
    }

    func testSteadyPlaybackPublishesWholeSecondProgressAndImmediatePause() async throws {
        let fixture = try audioFixture(duration: 4)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let transport = transport(tracks: [fixture.track], url: fixture.url)
        defer { transport.stop() }
        var updates = 0
        transport.onChange = { _ in updates += 1 }
        await transport.start()
        let initial = updates
        try await observe(timeout: 5) { transport.state.position > 2 }
        print("PERF steady progress callbacks over 2 playback seconds: \(updates - initial)")
        XCTAssertLessThanOrEqual(updates - initial, 4, "Audio envelopes must not drive ten UI/Now Playing updates per second")
        XCTAssertGreaterThanOrEqual(updates - initial, 2)
        let beforePause = updates
        transport.pause()
        XCTAssertFalse(transport.state.playing)
        XCTAssertGreaterThan(updates, beforePause, "Pause must publish immediately")
    }

    func testUnavailableStreamingSuccessorIsRequestedOnceUntilQueueChanges() async throws {
        var fixture = try audioFixture(duration: 4)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        fixture.track.duration = nil
        let track = fixture.track
        var attempts = 0
        let transport = NativeMusicTransport(tracks: [track, track], index: 0, position: 0) { index in
            if index == 1 { attempts += 1; throw URLError(.cannotConnectToHost) }
            return PlaybackRequest(id: "synthetic", item: track.mediaItem, url: fixture.url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        await transport.start()
        try await observe(timeout: 5) { transport.state.position > 0 && attempts > 0 }
        let confirmed = await transport.confirmPlaying()
        XCTAssertTrue(confirmed, "An unavailable successor must not reject an already playing output handoff")
        try await Task.sleep(for: .milliseconds(700))
        print("PERF failed successor requests over 700 ms: \(attempts)")
        XCTAssertEqual(attempts, 1, "A server failure must not be retried on every playback tick")
        transport.replaceUpcoming([track, track])
        try await observe(timeout: 5) { attempts > 1 }
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(attempts, 2, "An explicit queue refresh may retry once")
    }

    func testPausePreservesScheduledSuccessorAndInProgressCrossfade() async throws {
        let fixture = try audioFixture(duration: 2.4)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        var resolutions = [Int](), played = [Int]()
        let transport = NativeMusicTransport(tracks: [fixture.track, fixture.track], index: 0, position: 0) { index in
            resolutions.append(index)
            return PlaybackRequest(id: "occurrence-\(index)", item: fixture.track.mediaItem, url: fixture.url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 3, normalize: true, volume: 0)
        transport.onTrack = { _, index in played.append(index) }
        await transport.start()
        try await observe(timeout: 5) { transport.state.position > 0.15 && resolutions.count == 2 }
        transport.pause()
        let paused = transport.state.position
        // Outlast the successor's original host start; pausing must move that boundary.
        try await Task.sleep(for: .milliseconds(1_400))
        transport.pause()
        XCTAssertEqual(transport.state.position, paused)
        await transport.resume()
        await transport.resume() // Repeated Play must not start another source or move the clock.
        try await observe(timeout: 5) { transport.state.index == 1 && transport.state.position > 0.15 }
        XCTAssertLessThan(transport.state.position, 0.6)
        transport.pause()
        let crossfadePosition = transport.state.position
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(transport.state.position, crossfadePosition)
        await transport.resume()
        try await observe(timeout: 5) { transport.state.finished }
        XCTAssertEqual(played, [0, 1])
        XCTAssertEqual(resolutions, [0, 1], "Both sides of a fade must survive pause without preparing again")
        XCTAssertEqual(transport.state.position, 2.4, accuracy: 0.05)
        XCTAssertNil(transport.state.error)
    }

    func testRepeatChangeWhilePausedReplacesTheScheduledSuccessor() async throws {
        let fixture = try audioFixture(duration: 1.6)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        var resolutions = [Int](), played = [Int]()
        let transport = NativeMusicTransport(tracks: [fixture.track, fixture.track], index: 0, position: 0) { index in
            resolutions.append(index)
            return PlaybackRequest(id: "occurrence-\(index)", item: fixture.track.mediaItem, url: fixture.url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        transport.onTrack = { _, index in played.append(index) }
        await transport.start()
        try await observe(timeout: 5) { transport.state.position > 0.15 && resolutions.count == 2 }
        transport.pause()
        transport.configure(repeatMode: "one", crossfade: 0, normalize: true, volume: 0)
        XCTAssertFalse(transport.state.playing)
        await transport.resume()
        try await observe(timeout: 5) { played.count == 2 }
        XCTAssertEqual(played, [0, 0], "The paused Repeat choice must replace the previously scheduled occurrence")
        XCTAssertNil(transport.state.error)
    }

    func testPauseDuringSourceResolutionCannotStartLateAudio() async throws {
        let fixture = try audioFixture(duration: 2)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        var resolutions = 0, trackEvents = 0
        let transport = NativeMusicTransport(tracks: [fixture.track], index: 0, position: 0) { _ in
            resolutions += 1
            try await Task.sleep(for: .milliseconds(150))
            return PlaybackRequest(id: "synthetic", item: fixture.track.mediaItem, url: fixture.url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        transport.onTrack = { _, _ in trackEvents += 1 }
        let starting = Task { await transport.start() }
        try await observe(timeout: 2) { resolutions == 1 }
        transport.pause()
        await starting.value
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(trackEvents, 0)
        await transport.resume()
        let confirmed = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(confirmed)
        XCTAssertEqual(resolutions, 2)
        XCTAssertEqual(trackEvents, 1)
    }

    func testAuthorizationFailureDiscardsPreparedPlaybackInBothModes() async throws {
        let fixture = try audioFixture(duration: 2)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        for decoded in [true, false] {
            var track = fixture.track
            if !decoded { track.duration = nil }
            var revoked = false, resolutions = 0
            let transport = NativeMusicTransport(tracks: [track, track], index: 0, position: 0) { index in
                resolutions += 1
                if revoked || index == 1 { revoked = true; throw URLError(.userAuthenticationRequired) }
                return PlaybackRequest(id: "synthetic", item: track.mediaItem, url: fixture.url,
                    startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
            }
            transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
            await transport.start()
            try await observe(timeout: 5) { revoked && transport.state.error != nil }
            XCTAssertFalse(transport.state.playing)
            XCTAssertTrue(transport.state.finished)
            await transport.resume()
            let confirmed = await transport.confirmPlaying(timeout: 0.2)
            XCTAssertFalse(confirmed, "Revoked audio must not resume from retained resources")
            XCTAssertEqual(resolutions, 3, "A fresh source check must reject the revoked current track")
            transport.stop()
        }
    }

    func testTenDecodedPauseResumeCyclesReuseTheDownloadedAudio() async throws {
        let fixture = try audioFixture(duration: 8)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let server = try AudioHTTPFixture(bytes: bytes)
        let url = try await server.start()
        defer { server.stop() }
        var resolutions = 0, trackEvents = 0, stateEvents = 0
        let transport = NativeMusicTransport(tracks: [fixture.track], index: 0, position: 0) { _ in
            resolutions += 1
            return PlaybackRequest(id: "synthetic", item: fixture.track.mediaItem, url: url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        transport.onTrack = { _, _ in trackEvents += 1 }
        transport.onChange = { _ in stateEvents += 1 }
        await transport.start()
        XCTAssertEqual(transport.mode, .decoded)
        try await observe(timeout: 5) { transport.state.position > 0.1 }
        var latencies = [Double]()
        for _ in 0..<10 {
            transport.pause()
            let paused = transport.state.position
            try await Task.sleep(for: .milliseconds(40))
            XCTAssertEqual(transport.state.position, paused)
            let beforeResume = stateEvents
            let duration = await ContinuousClock().measure {
                await transport.resume()
                XCTAssertEqual(stateEvents, beforeResume + 1, "Resume must publish the new state once, immediately")
                let confirmed = await transport.confirmPlaying(timeout: 2)
                XCTAssertTrue(confirmed)
            }
            latencies.append(Double(duration.components.seconds) * 1_000 + Double(duration.components.attoseconds) / 1e15)
            try await observe(timeout: 5) { transport.state.position > paused + 0.05 }
        }
        print("PERF ten decoded pause/resumes: source resolutions=\(resolutions), HTTP downloads=\(server.requests), delivered bytes=\(server.requests * bytes.count), resume-to-render median ms=\(latencies.sorted()[5])")
        XCTAssertEqual(server.requests, 1, "Resuming prepared audio must not download it again")
        XCTAssertEqual(resolutions, 1)
        XCTAssertEqual(trackEvents, 1, "Pause/resume must not create another track/start event")
        transport.stop()
        await transport.resume()
        let restarted = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(restarted)
        XCTAssertEqual(server.requests, 2, "Stop must discard prepared playback resources")
    }

    func testTenStreamingPauseResumeCyclesKeepTheCurrentPlayer() async throws {
        var fixture = try audioFixture(duration: 8)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        fixture.track.duration = nil
        var resolutions = 0
        let transport = NativeMusicTransport(tracks: [fixture.track], index: 0, position: 0) { _ in
            resolutions += 1
            return PlaybackRequest(id: "synthetic", item: fixture.track.mediaItem, url: fixture.url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        await transport.start()
        XCTAssertEqual(transport.mode, .streaming)
        try await observe(timeout: 5) { transport.state.position > 0.1 }
        for _ in 0..<10 {
            transport.pause()
            let paused = transport.state.position
            try await Task.sleep(for: .milliseconds(40))
            XCTAssertEqual(transport.state.position, paused)
            await transport.resume()
            let confirmed = await transport.confirmPlaying(timeout: 2)
            XCTAssertTrue(confirmed)
            try await observe(timeout: 5) { transport.state.position > paused + 0.05 }
        }
        print("PERF ten streaming pause/resumes: source resolutions=\(resolutions)")
        XCTAssertEqual(resolutions, 1, "The current AVQueuePlayer item must survive a pause")
    }

    private func transport(tracks: [LuminaCore.MusicTrack], url: URL, crossfade: Double = 0) -> NativeMusicTransport {
        let transport = NativeMusicTransport(tracks: tracks, index: 0, position: 0) { index in
            PlaybackRequest(id: "occurrence-\(index)", item: tracks[index].mediaItem, url: url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        transport.configure(repeatMode: "off", crossfade: crossfade, normalize: true, volume: 0)
        return transport
    }

    private func observe(timeout: Double, until predicate: () -> Bool) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(timeout))
        while !predicate(), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(predicate(), "The live audio engine did not reach the expected playback state")
    }

    private func audioFixture(duration: Double) throws -> (url: URL, track: LuminaCore.MusicTrack) {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-engine-\(UUID()).wav")
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 2))
        let frames = AVAudioFrameCount(duration * 48_000)
        let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames))
        buffer.frameLength = frames
        let channels = try XCTUnwrap(buffer.floatChannelData)
        for channel in 0..<2 {
            for frame in 0..<Int(frames) {
                channels[channel][frame] = Float(sin(Double(frame) * 2 * .pi * 440 / 48_000)) * 0.01
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buffer)
        let data = try JSONSerialization.data(withJSONObject: [
            "id": "00000000-0000-0000-0000-000000000001", "title": "Synthetic tone", "artist": "Test",
            "album_artist": "Test", "album": "Engine proof", "audio_url": "", "duration": duration,
            "sample_rate": 48_000, "channels": 2, "gain_is_manual": false, "genres": [],
        ])
        return (url, try JSONDecoder().decode(LuminaCore.MusicTrack.self, from: data))
    }
}
/// A real download boundary with a fixed 100 ms response delay, no shared cache.
private final class AudioHTTPFixture: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "lumina.test.audio-http")
    private let lock = NSLock()
    private var count = 0
    private let response: Data
    var requests: Int { lock.withLock { count } }

    init(bytes: Data) throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        response = Data("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: \(bytes.count)\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".utf8) + bytes
    }

    func start() async throws -> URL {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            connection.start(queue: queue)
            connection.receive(minimumIncompleteLength: 1, maximumLength: 8_192) { [weak self] data, _, _, _ in
                guard let self, data != nil else { connection.cancel(); return }
                lock.withLock { count += 1 }
                queue.asyncAfter(deadline: .now() + .milliseconds(100)) { [self] in
                    connection.send(content: response, completion: .contentProcessed { _ in connection.cancel() })
                }
            }
        }
        return try await withCheckedThrowingContinuation { continuation in
            listener.stateUpdateHandler = { [self] state in
                switch state {
                case .ready:
                    listener.stateUpdateHandler = nil
                    continuation.resume(returning: URL(string: "http://127.0.0.1:\(listener.port!.rawValue)/tone.wav")!)
                case .failed(let error):
                    listener.stateUpdateHandler = nil
                    continuation.resume(throwing: error)
                default: break
                }
            }
            listener.start(queue: queue)
        }
    }

    func stop() { listener.cancel() }
}
#endif
