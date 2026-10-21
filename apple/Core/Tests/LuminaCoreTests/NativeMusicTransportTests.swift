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
        var fixture = try audioFixture(duration: 0.6)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url), delay: 0)
        let remote = try await server.start()
        defer { server.stop() }
        for url in [fixture.url, remote] {
            let transport = transport(tracks: [fixture.track, fixture.track], url: url)
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
        let existingInputs = try privateAudioInputs()
        let server = try AudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let remote = try await server.start()
        defer { server.stop() }
        for decoded in [true, false] {
            var track = fixture.track
            if !decoded { track.duration = nil }
            var revoked = false, resolutions = 0
            let transport = NativeMusicTransport(tracks: [track, track], index: 0, position: 0) { index in
                resolutions += 1
                if revoked || index == 1 { revoked = true; throw URLError(.userAuthenticationRequired) }
                return PlaybackRequest(id: "synthetic", item: track.mediaItem, url: decoded ? remote : fixture.url,
                    startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
            }
            transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
            await transport.start()
            try await observe(timeout: 5) { revoked && transport.state.error != nil }
            XCTAssertFalse(transport.state.playing)
            XCTAssertTrue(transport.state.finished)
            XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty,
                "Authorization failure must release the downloaded input")
            await transport.resume()
            let confirmed = await transport.confirmPlaying(timeout: 0.2)
            XCTAssertFalse(confirmed, "Revoked audio must not resume from retained resources")
            XCTAssertEqual(resolutions, 3, "A fresh source check must reject the revoked current track")
            transport.stop()
        }
    }

    func testFileSegmentsResampleMixedRateQueueAndSeekWithoutDeletingLocalInputs() async throws {
        var first = try audioFixture(duration: 2.4, sampleRate: 44_100, channelCount: 1, aac: true)
        let second = try audioFixture(duration: 1.6, sampleRate: 96_000, channelCount: 2)
        defer {
            try? FileManager.default.removeItem(at: first.url)
            try? FileManager.default.removeItem(at: second.url)
        }
        first.track.gainIsManual = true
        first.track.gainDB = 6
        let fixtures = [first, second]
        var played = [Int]()
        let transport = NativeMusicTransport(tracks: fixtures.map(\.track), index: 0, position: 0) { index in
            PlaybackRequest(id: "occurrence-\(index)", item: fixtures[index].track.mediaItem, url: fixtures[index].url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Mixed-rate audio")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0.6, normalize: true, volume: 0)
        transport.onTrack = { _, index in played.append(index) }
        await transport.start()
        let initial = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(initial)
        XCTAssertEqual(transport.mode, .decoded)
        try await observe(timeout: 5) { transport.state.index == 1 && transport.state.position > 0.1 }
        transport.pause()
        await transport.resume()
        let resumed = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(resumed)
        await transport.seek(0.4)
        let sought = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(sought)
        try await observe(timeout: 5) { transport.state.finished }
        XCTAssertEqual(played, [0, 1, 1], "Only the explicit seek restarts the current occurrence")
        XCTAssertEqual(transport.state.position, 1.6, accuracy: 0.05)
        XCTAssertNil(transport.state.error)
        transport.stop()
        XCTAssertTrue(FileManager.default.fileExists(atPath: first.url.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: second.url.path))
    }

    func testTenDecodedPauseResumeCyclesReuseTheDownloadedAudio() async throws {
        let fixture = try audioFixture(duration: 8)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let existingInputs = try privateAudioInputs()
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
        let preparedInputs = try privateAudioInputs().subtracting(existingInputs)
        XCTAssertEqual(preparedInputs.count, 1, "Only the active private input may be retained")
        for input in preparedInputs {
            let attributes = try FileManager.default.attributesOfItem(atPath: input.path)
            XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        }
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
        XCTAssertEqual(try privateAudioInputs().subtracting(existingInputs), preparedInputs,
            "Pause/resume must reuse exactly the same bounded private input")
        transport.stop()
        XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty, "Stop must remove retained input files")
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

    func testRemoteInputsAreBoundedDuringCrossfadeAndRemovedOnCompletion() async throws {
        let fixture = try audioFixture(duration: 2.4)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let existingInputs = try privateAudioInputs()
        let server = try AudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let remote = try await server.start()
        defer { server.stop() }
        let transport = transport(tracks: [fixture.track, fixture.track], url: remote, crossfade: 0.6)
        defer { transport.stop() }
        await transport.start()
        try await observe(timeout: 5) { (try? privateAudioInputs().subtracting(existingInputs).count) == 2 }
        XCTAssertEqual(server.requests, 2)
        try await observe(timeout: 5) { transport.state.index == 1 && transport.state.position > 0.8 }
        XCTAssertEqual(try privateAudioInputs().subtracting(existingInputs).count, 1,
            "The retired occurrence must release its private input")
        try await observe(timeout: 5) { transport.state.finished }
        XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty)
        XCTAssertNil(transport.state.error)
    }

    func testStopReleasesInputEvenWhenLookaheadIgnoresCancellation() async throws {
        let fixture = try audioFixture(duration: 2.4)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let existingInputs = try privateAudioInputs()
        let server = try AudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let remote = try await server.start()
        defer { server.stop() }
        var pending: CheckedContinuation<Void, Never>?
        var returned = false
        let transport = NativeMusicTransport(tracks: [fixture.track, fixture.track], index: 0, position: 0) { index in
            if index == 1 {
                await withCheckedContinuation { pending = $0 }
                returned = true
            }
            return PlaybackRequest(id: "occurrence-\(index)", item: fixture.track.mediaItem, url: remote,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "HTTP WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        await transport.start()
        try await observe(timeout: 2) { pending != nil }
        XCTAssertEqual(try privateAudioInputs().subtracting(existingInputs).count, 1)
        transport.stop()
        XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty,
            "A canceled lookahead must not retain the stopped occurrence's file")
        pending?.resume(); pending = nil
        try await observe(timeout: 2) { returned }
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(server.requests, 1, "A late source must not begin another download")
    }

    func testPauseDuringDownloadCannotRetainOrStartTheLateInput() async throws {
        let fixture = try audioFixture(duration: 2)
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let existingInputs = try privateAudioInputs()
        let server = try AudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let remote = try await server.start()
        defer { server.stop() }
        let transport = transport(tracks: [fixture.track], url: remote)
        defer { transport.stop() }
        let starting = Task { await transport.start() }
        try await observe(timeout: 2) { server.requests == 1 }
        transport.pause()
        await starting.value
        XCTAssertFalse(transport.state.playing)
        XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty)
        await transport.resume()
        let confirmed = await transport.confirmPlaying(timeout: 2)
        XCTAssertTrue(confirmed)
        XCTAssertEqual(server.requests, 2)
        transport.stop()
        XCTAssertTrue(try privateAudioInputs().subtracting(existingInputs).isEmpty)
    }

    func testProgressiveRemoteStartsBeforeFullDeliveryAndKeepsPauseAndSeekInTheSameOccurrence() async throws {
        var fixture = try audioFixture(duration: 60)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let existing = try privateAudioInputs()
        let server = try ProgressiveAudioHTTPFixture(bytes: bytes)
        let url = try await server.start()
        defer { server.stop() }
        var resolutions = 0, played = [Int]()
        let transport = NativeMusicTransport(tracks: [fixture.track], index: 0, position: 0) { _ in
            resolutions += 1
            return PlaybackRequest(id: "progressive", item: fixture.track.mediaItem, url: url,
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        transport.onTrack = { _, index in played.append(index) }
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 5)
        XCTAssertTrue(confirmed)
        XCTAssertEqual(transport.mode, .progressive)
        XCTAssertLessThan(server.deliveredBytes, bytes.count, "Playback must start before whole-track delivery")
        try await observe(timeout: 3) { transport.state.position > 0.1 }
        transport.pause()
        // Allow already-buffered TCP data to drain, then require read-ahead to stop.
        try await Task.sleep(for: .seconds(1))
        let settledBytes = server.deliveredBytes
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(server.deliveredBytes, settledBytes, "Paused playback must suspend HTTP read-ahead")
        XCTAssertLessThan(settledBytes, bytes.count, "Pause must not download the remaining track")
        await transport.resume()
        for _ in 0..<2 {
            transport.pause()
            let paused = transport.state.position
            try await Task.sleep(for: .milliseconds(100))
            XCTAssertEqual(transport.state.position, paused)
            await transport.resume()
            let resumed = await transport.confirmPlaying(timeout: 2)
            XCTAssertTrue(resumed)
        }
        transport.pause()
        await transport.seek(40)
        XCTAssertFalse(transport.state.playing, "A paused seek must not resume audio")
        await transport.resume()
        try await observe(timeout: 3) { transport.state.position > 40.05 }
        XCTAssertEqual(resolutions, 1, "Progressive seek and pause reuse the current asset")
        XCTAssertEqual(played, [0])
        XCTAssertTrue(try privateAudioInputs().subtracting(existing).isEmpty)
        transport.pause()
        transport.stop()
        try await observe(timeout: 3) { server.activeConnections == 0 }
        let stoppedBytes = server.deliveredBytes
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertEqual(server.deliveredBytes, stoppedBytes, "Stop must cancel network read-ahead")
        XCTAssertNil(transport.state.error)
    }

    func testPausedProgressiveQueueRetainsBothInputsWithoutContinuingReadAhead() async throws {
        var fixture = try audioFixture(duration: 60)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let servers = try (0..<2).map { _ in try ProgressiveAudioHTTPFixture(bytes: bytes) }
        defer { servers.forEach { $0.stop() } }
        var urls = [URL]()
        for server in servers { urls.append(try await server.start()) }
        var resolutions = [Int]()
        let transport = NativeMusicTransport(tracks: [fixture.track, fixture.track], index: 0, position: 0) { index in
            resolutions.append(index)
            return PlaybackRequest(id: "paused-\(index)", item: fixture.track.mediaItem, url: urls[index],
                startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 5)
        XCTAssertTrue(confirmed)
        try await observe(timeout: 3) { servers[1].requestCount > 0 && transport.state.position > 0.1 }
        transport.pause()
        try await Task.sleep(for: .seconds(1))
        let settled = servers.map(\.deliveredBytes)
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(servers.map(\.deliveredBytes), settled)
        XCTAssertTrue(settled.allSatisfy { $0 < bytes.count })
        let earlierSeek = Task { await transport.seek(20) }
        try await observe(timeout: 3) { transport.state.position == 20 }
        await transport.seek(40)
        await earlierSeek.value
        XCTAssertFalse(transport.state.playing)
        await transport.resume()
        let resumed = await transport.confirmPlaying(timeout: 5)
        XCTAssertTrue(resumed)
        try await observe(timeout: 3) { transport.state.position > 40.05 }
        XCTAssertEqual(resolutions, [0, 1], "Both prepared occurrences must survive pause and overlapping seeks")
        transport.pause()
        transport.stop()
        try await observe(timeout: 3) { servers.allSatisfy { $0.activeConnections == 0 } }
    }

    func testEnablingCrossfadeUpgradesProgressivePlaybackWithoutAnotherTrackEvent() async throws {
        var fixture = try audioFixture(duration: 6)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url), delay: 0)
        let url = try await server.start()
        defer { server.stop() }
        let transport = transport(tracks: [fixture.track], url: url)
        defer { transport.stop() }
        var events = 0
        transport.onTrack = { _, _ in events += 1 }
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 3)
        XCTAssertTrue(confirmed)
        XCTAssertEqual(transport.mode, .progressive)
        try await observe(timeout: 3) { transport.state.position > 0.2 }
        let position = transport.state.position
        transport.configure(repeatMode: "off", crossfade: 3, normalize: true, volume: 0)
        try await observe(timeout: 3) { transport.mode == .decoded && transport.state.playing }
        XCTAssertGreaterThanOrEqual(transport.state.position, position - 0.05)
        XCTAssertEqual(events, 1)
        XCTAssertNil(transport.state.error)
    }

    func testProgressiveDeliveryRejectsCrossOriginRedirectsDenialsAndOversizedInput() async throws {
        var fixture = try audioFixture(duration: 2)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let destination = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0)
        let destinationURL = try await destination.start()
        defer { destination.stop() }
        let existing = try privateAudioInputs()
        let servers = try [
            ProgressiveAudioHTTPFixture(bytes: bytes, redirect: destinationURL),
            ProgressiveAudioHTTPFixture(bytes: bytes, status: 401),
            ProgressiveAudioHTTPFixture(bytes: bytes, status: 403),
            ProgressiveAudioHTTPFixture(bytes: bytes, advertisedLength: MusicPlaybackPolicy.maximumTrackBytes + 1),
        ]
        for (index, server) in servers.enumerated() {
            let url = try await server.start()
            let transport = transport(tracks: [fixture.track], url: url)
            await transport.start()
            try await observe(timeout: 4) { transport.state.error != nil }
            XCTAssertFalse(transport.state.playing)
            let confirmed = await transport.confirmPlaying(timeout: 0.1)
            XCTAssertFalse(confirmed)
            if index == 1 || index == 2 {
                XCTAssertTrue(transport.state.finished, "Authorization denial tears down the active owner")
                XCTAssertTrue(transport.state.error?.contains("Access to this track was denied") == true)
            }
            transport.stop()
            server.stop()
        }
        XCTAssertEqual(destination.requestCount, 0, "A redirect must not forward private playback to another origin")
        XCTAssertTrue(try privateAudioInputs().subtracting(existing).isEmpty)
    }

    func testProgressiveSameOriginRedirectAndRangeIgnoringServerKeepSeekAndPrivacy() async throws {
        var fixture = try audioFixture(duration: 6)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let cookie = try XCTUnwrap(HTTPCookie(properties: [.domain: "127.0.0.1", .path: "/", .name: "lumina-synthetic", .value: "test-only"]))
        HTTPCookieStorage.shared.setCookie(cookie)
        defer { HTTPCookieStorage.shared.deleteCookie(cookie) }
        for ranges in [true, false] {
            let server = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0, ranges: ranges, relativeRedirect: true)
            let url = try await server.start().appendingPathComponent("redirect")
            let transport = transport(tracks: [fixture.track], url: url)
            await transport.start()
            let confirmed = await transport.confirmPlaying(timeout: 4)
            XCTAssertTrue(confirmed)
            XCTAssertEqual(transport.mode, .progressive)
            await transport.seek(3)
            try await observe(timeout: 3) { transport.state.position > 3.05 }
            XCTAssertFalse(server.receivedCredentials, "The shared cookie jar must not enter progressive audio requests")
            XCTAssertNil(transport.state.error)
            transport.stop()
            try await observe(timeout: 3) { server.activeConnections == 0 }
            server.stop()
        }
    }

    func testProgressiveDuplicateQueueAndProcessingQueueBothKeepTheirOccurrences() async throws {
        var fixture = try audioFixture(duration: 0.8)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url), delay: 0)
        let url = try await server.start()
        defer { server.stop() }
        for (processing, localNext) in [(false, false), (false, true), (true, false)] {
            var successor = fixture.track
            if processing { successor.gainDB = -6 }
            let transport = NativeMusicTransport(tracks: [fixture.track, successor], index: 0, position: 0) { index in
                PlaybackRequest(id: "occurrence-\(index)", item: fixture.track.mediaItem,
                    url: localNext && index == 1 ? fixture.url : url, startPositionSeconds: 0,
                    mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Mixed source queue")
            }
            transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
            defer { transport.stop() }
            var played = [Int]()
            transport.onTrack = { _, index in played.append(index) }
            await transport.start()
            let expected: NativeMusicTransport.Mode = processing ? .decoded : .progressive
            XCTAssertEqual(transport.mode, expected, "A queue needing processing keeps engine preloading from the start")
            try await observe(timeout: 5) { transport.state.index == 1 }
            XCTAssertEqual(transport.mode, expected)
            try await observe(timeout: 5) { transport.state.finished }
            XCTAssertEqual(played, [0, 1])
            XCTAssertNil(transport.state.error)
            try await observe(timeout: 3) { server.activeConnections == 0 }
        }
    }

    func testEnablingNormalizationWhilePausedRetainsPositionAndRequiresTheEngine() async throws {
        var fixture = try audioFixture(duration: 6)
        fixture.track.codec = "pcm_f32le"; fixture.track.gainDB = 6
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url), delay: 0)
        let url = try await server.start()
        defer { server.stop() }
        let transport = transport(tracks: [fixture.track], url: url)
        defer { transport.stop() }
        var trackEvents = 0
        transport.onTrack = { _, _ in trackEvents += 1 }
        transport.configure(repeatMode: "off", crossfade: 0, normalize: false, volume: 0)
        await transport.start()
        XCTAssertEqual(transport.mode, .progressive)
        try await observe(timeout: 3) { transport.state.position > 0.2 }
        transport.pause()
        let paused = transport.state.position
        transport.configure(repeatMode: "off", crossfade: 0, normalize: true, volume: 0)
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertFalse(transport.state.playing)
        XCTAssertEqual(transport.state.position, paused)
        await transport.resume()
        let confirmed = await transport.confirmPlaying(timeout: 3)
        XCTAssertTrue(confirmed)
        XCTAssertEqual(transport.mode, .decoded, "Positive normalization must never be clamped to AVPlayer volume")
        XCTAssertEqual(trackEvents, 1, "Changing processing while paused must retain the occurrence on Resume")
        XCTAssertNil(transport.state.error)
    }

    func testProgressiveAuthorizationDenialAfterPauseAndSeekStillTearsDownTheOwner() async throws {
        var fixture = try audioFixture(duration: 60)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let url = try await server.start()
        defer { server.stop() }
        let transport = transport(tracks: [fixture.track], url: url)
        defer { transport.stop() }
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 5)
        XCTAssertTrue(confirmed)
        transport.pause()
        await transport.resume()
        server.reject(403)
        await transport.seek(50)
        try await observe(timeout: 4) { transport.state.finished }
        XCTAssertFalse(transport.state.playing)
        XCTAssertTrue(transport.state.error?.contains("Access to this track was denied") == true)
        try await observe(timeout: 3) { server.activeConnections == 0 }
    }

    func testProgressiveFailedLookaheadKeepsCurrentAudioAndRetriesOnlyAtTheBoundary() async throws {
        var fixture = try audioFixture(duration: 4)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let bytes = try Data(contentsOf: fixture.url)
        let server = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0)
        let failing = try ProgressiveAudioHTTPFixture(bytes: bytes, status: 500)
        let url = try await server.start(), failureURL = try await failing.start()
        defer { server.stop(); failing.stop() }
        for repeatMode in ["off", "one"] {
            var resolutions = [Int]()
            let transport = NativeMusicTransport(tracks: [fixture.track, fixture.track], index: 0, position: 0) { index in
                let remote = resolutions.isEmpty ? url : failureURL
                resolutions.append(index)
                return PlaybackRequest(id: "occurrence-\(index)", item: fixture.track.mediaItem, url: remote,
                    startPositionSeconds: 0, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Test WAV")
            }
            defer { transport.stop() }
            transport.configure(repeatMode: repeatMode, crossfade: 0, normalize: true, volume: 0)
            await transport.start()
            try await observe(timeout: 3) { transport.state.error?.contains("next track") == true }
            XCTAssertTrue(transport.state.playing)
            XCTAssertEqual(transport.state.index, 0)
            try await Task.sleep(for: .milliseconds(300))
            XCTAssertEqual(resolutions, [0, repeatMode == "one" ? 0 : 1], "A failed progressive preload must not retry on every clock tick")
            try await observe(timeout: 6) { transport.state.finished }
            XCTAssertEqual(resolutions, repeatMode == "one" ? [0, 0, 0] : [0, 1, 1], "Only the actual boundary may retry the failed occurrence")
            XCTAssertFalse(transport.state.playing)
        }
    }

    func testDroppingTheProgressiveOwnerCancelsItsReadAhead() async throws {
        var fixture = try audioFixture(duration: 60)
        fixture.track.codec = "pcm_f32le"
        defer { try? FileManager.default.removeItem(at: fixture.url) }
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixture.url))
        let url = try await server.start()
        defer { server.stop() }
        var owner: NativeMusicTransport? = transport(tracks: [fixture.track], url: url)
        weak var released = owner
        await owner?.start()
        let confirmed = await owner?.confirmPlaying(timeout: 5)
        XCTAssertEqual(confirmed, true)
        owner?.pause()
        owner = nil
        try await observe(timeout: 3) { released == nil && server.activeConnections == 0 }
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

    private func privateAudioInputs() throws -> Set<URL> {
        Set(try FileManager.default.contentsOfDirectory(at: FileManager.default.temporaryDirectory,
            includingPropertiesForKeys: nil).filter { $0.lastPathComponent.hasPrefix("lumina-audio-") })
    }

    private func audioFixture(duration: Double, sampleRate: Double = 48_000, channelCount: UInt32 = 2, aac: Bool = false) throws -> (url: URL, track: LuminaCore.MusicTrack) {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-engine-\(UUID()).\(aac ? "m4a" : "wav")")
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: channelCount))
        let frames = AVAudioFrameCount(duration * sampleRate)
        let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames))
        buffer.frameLength = frames
        let channels = try XCTUnwrap(buffer.floatChannelData)
        for channel in 0..<Int(channelCount) {
            for frame in 0..<Int(frames) {
                channels[channel][frame] = Float(sin(Double(frame) * 2 * .pi * 440 / sampleRate)) * 0.01
            }
        }
        let settings: [String: Any] = aac ? [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: channelCount, AVEncoderBitRateKey: 128_000] : format.settings
        let file = try AVAudioFile(forWriting: url, settings: settings)
        try file.write(from: buffer)
        let data = try JSONSerialization.data(withJSONObject: [
            "id": "00000000-0000-0000-0000-000000000001", "title": "Synthetic tone", "artist": "Test",
            "album_artist": "Test", "album": "Engine proof", "audio_url": "", "duration": duration,
            "sample_rate": Int(sampleRate), "channels": Int(channelCount), "gain_is_manual": false, "genres": [],
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
