#if os(macOS)
import AVFoundation
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
#endif
