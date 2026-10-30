#if os(macOS)
import AVFoundation
import CryptoKit
import CoreVideo
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativeVideoSourceTests: XCTestCase {
    func testRepeatedHTTPSeeksDoNotWaitForObsoleteTargets() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = directory.appendingPathComponent("synthetic.mkv")
        let generate = Process()
        generate.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        generate.arguments = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=90",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=90", "-c:v", "libx264", "-preset", "ultrafast",
            "-g", "300", "-keyint_min", "300", "-sc_threshold", "0", "-c:a", "aac", source.path]
        try generate.run()
        generate.waitUntilExit()
        XCTAssertEqual(generate.terminationStatus, 0)
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: source), mime: "video/x-matroska", delay: 100)
        let remote = try await server.start().deletingLastPathComponent().appendingPathComponent("synthetic.mkv")
        defer { server.stop() }
        for input in [source, remote] {
            let owner = NativeVideoSource()
            let item = try await owner.prepare(url: input, usesClientDemux: true)
            let frames = AVPlayerItemVideoOutput(pixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
            item.add(frames)
            let player = AVPlayer(playerItem: item)
            player.volume = 0
            defer { player.replaceCurrentItem(with: nil); owner.stop() }
            try await NativeVideoSource.waitUntilReady(player)
            player.play()
            try await Task.sleep(for: .milliseconds(500))
            if !input.isFileURL {
                var authority = VideoSeekAuthority()
                let range = VideoSeekRange(0, 89)!
                let first = try XCTUnwrap(authority.request(target: 72.3, ranges: [range], direction: .forward))
                let olderSeek = Task { @MainActor in
                    await player.seek(to: CMTime(seconds: first.target, preferredTimescale: 600),
                                      toleranceBefore: .zero, toleranceAfter: .zero)
                }
                try await Task.sleep(for: .milliseconds(50))
                let latestRequested = ContinuousClock.now
                let latest = try XCTUnwrap(authority.request(target: 8.7, ranges: [range], direction: .backward))
                item.cancelPendingSeeks()
                let finished = await player.seek(to: CMTime(seconds: latest.target, preferredTimescale: 600),
                                                 toleranceBefore: .zero, toleranceAfter: .zero)
                XCTAssertTrue(finished)
                _ = await olderSeek.value
                let frameDeadline = latestRequested + .seconds(1)
                var decodedLatest = false
                while ContinuousClock.now < frameDeadline {
                    if abs(item.currentTime().seconds - latest.target) < 1,
                       frames.copyPixelBuffer(forItemTime: item.currentTime(), itemTimeForDisplay: nil) != nil {
                        decodedLatest = true
                        break
                    }
                    try await Task.sleep(for: .milliseconds(10))
                }
                XCTAssertTrue(decodedLatest, "The superseding seek must render its destination promptly")
                let latency = latestRequested.duration(to: .now)
                print("Superseding cached seek latency=\(latency)")
                XCTAssertLessThan(latency, .seconds(1),
                                  "A new scrub to cached media must not wait for an obsolete HTTP seek")
            }
            for target in [8.7, 72.3, 16.7, 51.2, 82.8, 12.3] {
                let start = ContinuousClock.now
                let finished = await player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                                                 toleranceBefore: .zero, toleranceAfter: .zero)
                XCTAssertTrue(finished)
                let deadline = start + .seconds(5)
                var decoded = false
                while ContinuousClock.now < deadline {
                    let time = item.currentTime()
                    if abs(time.seconds - target) < 1,
                       frames.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil {
                        decoded = true
                        break
                    }
                    try await Task.sleep(for: .milliseconds(10))
                }
                print("Seek benchmark remote=\(!input.isFileURL) target=\(target) decoded=\(decoded) elapsed=\(start.duration(to: .now))")
                XCTAssertTrue(decoded, "A long-GOP seek must produce a frame without a multi-second stall")
            }
        }
    }

    func testExternalSubtitleMustBeLocal() async {
        let source = NativeVideoSource()
        do {
            _ = try await source.prepare(
                url: URL(fileURLWithPath: "/unused.mp4"),
                usesClientDemux: false,
                externalSubtitles: [.init(fileURL: URL(string: "https://example.invalid/subtitles.vtt")!)]
            )
            XCTFail("Remote sidecars must be fetched and validated by the client before registration")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .externalSubtitleMustBeLocal)
        } catch {
            XCTFail("Unexpected sidecar validation error: \(error)")
        }
    }

    func testSubtitleDelayRejectsDirectAssetsWithoutASelectableSidecar() async {
        let source = NativeVideoSource()
        do {
            _ = try await source.prepare(
                url: URL(fileURLWithPath: "/unused.mp4"),
                usesClientDemux: false,
                subtitleDelaySeconds: 1
            )
            XCTFail("A direct asset has no native timing adjustment path")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .subtitleDelayUnavailable)
        } catch {
            XCTFail("Unexpected direct timing error: \(error)")
        }
        XCTAssertEqual(NativeVideoSource.normalizedSubtitleDelay(99), 10)
        XCTAssertEqual(NativeVideoSource.normalizedSubtitleDelay(-99), -10)
        XCTAssertEqual(NativeVideoSource.normalizedSubtitleDelay(.nan), 0)
    }

    func testServerHLSExternalVTTUsesAPrivateNativeSubtitleMaster() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let stream = directory.appendingPathComponent("stream.m3u8")
        try "#EXTM3U\n#EXT-X-VERSION:3\n".write(to: stream, atomically: true, encoding: .utf8)
        let subtitles = directory.appendingPathComponent("selected.vtt")
        try "WEBVTT\r\n\r\nNOTE\r\n00:01.000 --> 00:02.000\r\nUnshifted note\r\n\r\n00:01.000\t-->\t00:02.000 line:80%\r\nA private sidecar\r\n\r\n00:00.250 --> 00:00.500\r\nClipped away\r\n".write(
            to: subtitles, atomically: true, encoding: .utf8
        )

        let owner = NativeVideoSource()
        let item = try await owner.prepare(
            url: stream,
            usesClientDemux: false,
            externalSubtitles: [
                .init(
                    fileURL: subtitles,
                    language: "en",
                    name: "English\"\n#EXT-X-MEDIA:TYPE=AUDIO",
                    durationSeconds: 12
                )
            ],
            bandwidth: 1_234_567,
            subtitleDelaySeconds: -1
        )
        let masterURL = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
        XCTAssertEqual(masterURL.host, "127.0.0.1")
        XCTAssertTrue(owner.hasSelectableExternalSubtitles)
        let (masterData, masterResponse) = try await URLSession.shared.data(from: masterURL)
        XCTAssertEqual((masterResponse as? HTTPURLResponse)?.statusCode, 200)
        let master = String(decoding: masterData, as: UTF8.self)
        XCTAssertTrue(master.contains("TYPE=SUBTITLES"))
        XCTAssertTrue(master.contains("URI=\"sidecar-0.m3u8\""))
        XCTAssertTrue(master.contains("BANDWIDTH=1234567"))
        XCTAssertEqual(master.components(separatedBy: "#EXT-X-STREAM-INF:").count - 1, 1)
        XCTAssertFalse(
            master.split(separator: "\n").contains { $0.hasPrefix("#EXT-X-MEDIA:TYPE=AUDIO") },
            "Sidecar wrapper must not expose audio renditions or a bridge"
        )
        XCTAssertFalse(master.contains("NAME=\"English\"\n"), "Untrusted subtitle metadata must not add a manifest line")
        let sidecarURL = masterURL.deletingLastPathComponent().appendingPathComponent("sidecar-0.m3u8")
        let (playlistData, _) = try await URLSession.shared.data(from: sidecarURL)
        XCTAssertTrue(String(decoding: playlistData, as: UTF8.self).contains("sidecar-0.vtt"))
        let vttURL = masterURL.deletingLastPathComponent().appendingPathComponent("sidecar-0.vtt")
        let (vttData, _) = try await URLSession.shared.data(from: vttURL)
        let vtt = String(decoding: vttData, as: UTF8.self)
        XCTAssertTrue(vtt.contains("X-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000"))
        XCTAssertTrue(vtt.contains("00:00:00.000 --> 00:00:01.000 line:80%"),
                      "Two-field WebVTT timing and settings shift with a negative delay")
        XCTAssertTrue(vtt.contains("NOTE\n00:01.000 --> 00:02.000"), "NOTE blocks are not cue timing")
        XCTAssertFalse(vtt.contains("Clipped away"), "A cue ending before zero is not emitted")
        await owner.stop().value
        XCTAssertFalse(owner.hasSelectableExternalSubtitles)
    }

    func testSubtitleDelayRejectsUnshiftableExternalWebVTT() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let stream = directory.appendingPathComponent("stream.m3u8")
        try "#EXTM3U\n#EXT-X-VERSION:3\n".write(to: stream, atomically: true, encoding: .utf8)
        let subtitles = directory.appendingPathComponent("selected.vtt")
        try "WEBVTT\n\n999999999999999999999:00:00.000 --> 999999999999999999999:00:01.000\nOut of range\n".write(
            to: subtitles, atomically: true, encoding: .utf8
        )
        let source = NativeVideoSource()
        do {
            _ = try await source.prepare(
                url: stream,
                usesClientDemux: false,
                externalSubtitles: [.init(fileURL: subtitles, durationSeconds: 12)],
                subtitleDelaySeconds: 1
            )
            XCTFail("Out-of-range timing must fail without converting it to Int")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .externalSubtitleUnsupportedTiming)
        }

        try "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nKaraoke <00:01.500>timestamp\n".write(
            to: subtitles, atomically: true, encoding: .utf8
        )
        do {
            _ = try await source.prepare(
                url: stream,
                usesClientDemux: false,
                externalSubtitles: [.init(fileURL: subtitles, durationSeconds: 12)],
                subtitleDelaySeconds: 1
            )
            XCTFail("Inline karaoke timestamps must not remain unshifted")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .externalSubtitleUnsupportedTiming)
        }
    }

    func testReadinessPropagatesFailureAndCancellation() async throws {
        let empty = AVPlayer()
        do { try await NativeVideoSource.waitUntilReady(empty); XCTFail("Missing items cannot become ready") }
        catch is CancellationError { }
        let cancelled = Task { try await NativeVideoSource.waitUntilReady(empty) }
        cancelled.cancel()
        do { try await cancelled.value; XCTFail("Cancelled preparation must terminate") }
        catch is CancellationError { }
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mp4")
        let failed = AVPlayer(url: missing)
        do { try await NativeVideoSource.waitUntilReady(failed, timeout: .seconds(2)); XCTFail("Missing files must fail") }
        catch {
            XCTAssertEqual(failed.currentItem?.status, .failed, "Propagate asset failure rather than waiting for the deadline")
        }
        failed.replaceCurrentItem(with: nil)
    }

    func testOriginalMKVDecodesSeeksAndReleasesItsLoopbackSession() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = directory.appendingPathComponent("synthetic.mkv")
        let captions = directory.appendingPathComponent("synthetic.srt")
        try "1\n00:00:00,100 --> 00:00:01,000\nSynthetic opening caption\n\n2\n00:00:06,000 --> 00:00:07,000\nSynthetic seek caption\n".write(to: captions, atomically: true, encoding: .utf8)
        let generate = Process()
        generate.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        generate.arguments = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=12",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-f", "lavfi", "-i", "sine=frequency=880:duration=12",
            "-i", captions.path, "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:0",
            "-c:s", "srt", "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-c:a", "aac",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=Primary",
            "-metadata:s:a:1", "language=spa", "-metadata:s:a:1", "title=Second", source.path]
        try generate.run()
        generate.waitUntilExit()
        XCTAssertEqual(generate.terminationStatus, 0)
        let owner = NativeVideoSource()
        let item = try await owner.prepare(
            url: source,
            usesClientDemux: true,
            preferredAudioStreamIndex: 2,
            preferredSubtitleStreamIndex: 3,
            externalSubtitles: [.init(fileURL: captions, language: "en", name: "Downloaded sidecar")],
            subtitleDelaySeconds: 99
        )
        try FileManager.default.removeItem(at: captions)
        XCTAssertEqual(owner.activeAudioStreamIndex, 2)
        XCTAssertEqual(owner.subtitleDelaySeconds, 10)
        XCTAssertTrue(owner.hasSelectableExternalSubtitles, "Native source owns copied sidecars after prepare returns")
        let playlist = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
        XCTAssertEqual(playlist.host, "127.0.0.1")
        let frames = AVPlayerItemVideoOutput(pixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
        item.add(frames)
        let player = AVPlayer(playerItem: item)
        player.volume = 0
        try await NativeVideoSource.waitUntilReady(player)
        XCTAssertEqual(item.status, .readyToPlay)
        // An already-ready item must also complete; no future KVO change is required.
        try await NativeVideoSource.waitUntilReady(player)
        player.play()
        var decoded = false
        let deadline = ContinuousClock.now.advanced(by: .seconds(15))
        while ContinuousClock.now < deadline {
            let time = item.currentTime()
            if frames.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil { decoded = true }
            if decoded && time.seconds > 0.5 { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(decoded)
        XCTAssertGreaterThan(item.currentTime().seconds, 0.5)
        XCTAssertTrue(item.tracks.contains { $0.isEnabled && $0.assetTrack?.mediaType == .audio })
        let audibleGroup = try XCTUnwrap(item.asset.mediaSelectionGroup(forMediaCharacteristic: .audible))
        XCTAssertEqual(
            item.currentMediaSelection.selectedMediaOption(in: audibleGroup)?.displayName,
            "Spanish",
            "The authored master must retain the requested stream-copied audio as its default rendition."
        )
        let legibleGroup = try XCTUnwrap(item.asset.mediaSelectionGroup(forMediaCharacteristic: .legible))
        let subtitleOption = try XCTUnwrap(legibleGroup.options.first)
        item.select(subtitleOption, in: legibleGroup)
        XCTAssertTrue(item.currentMediaSelection.selectedMediaOption(in: legibleGroup) === subtitleOption)
        item.select(nil, in: legibleGroup)
        XCTAssertNil(item.currentMediaSelection.selectedMediaOption(in: legibleGroup))
        XCTAssertEqual(owner.retainedSubtitleHistoryBytes, 0, "AVKit does not consume the host cue replay cache")
        let (masterData, masterResponse) = try await URLSession.shared.data(from: playlist)
        XCTAssertEqual((masterResponse as? HTTPURLResponse)?.statusCode, 200)
        let master = String(decoding: masterData, as: UTF8.self)
        XCTAssertTrue(master.contains("#EXT-X-INDEPENDENT-SEGMENTS"))
        XCTAssertTrue(master.contains("RESOLUTION=160x90"),
                      "A video-bearing subtitle master must retain Prism's declared resolution.")
        XCTAssertTrue(master.contains("CODECS=\""),
                      "A video-bearing subtitle master must retain Prism's declared codecs.")
        XCTAssertTrue(master.contains("CLOSED-CAPTIONS=NONE"),
                      "The master must retain Prism's explicit closed-caption signaling.")
        let masterLines = master.split(separator: "\n")
        let streamOffset = try XCTUnwrap(masterLines.firstIndex(where: { $0.hasPrefix("#EXT-X-STREAM-INF:") }))
        let videoVariantName = try XCTUnwrap(
            masterLines.dropFirst(streamOffset + 1).first { !$0.isEmpty && !$0.hasPrefix("#") }
        )
        let videoVariant = try XCTUnwrap(URL(string: String(videoVariantName), relativeTo: playlist))
        let (videoVariantData, videoVariantResponse) = try await URLSession.shared.data(from: videoVariant)
        XCTAssertEqual((videoVariantResponse as? HTTPURLResponse)?.statusCode, 200)
        let videoVariantText = String(decoding: videoVariantData, as: UTF8.self)
        XCTAssertFalse(videoVariantText.contains("#EXT-X-STREAM-INF:"),
                       "The authored master must target Prism's media playlist, never another master")
        XCTAssertTrue(videoVariantText.contains("#EXT-X-TARGETDURATION:"))
        let subtitleLine = try XCTUnwrap(master.split(separator: "\n").first { $0.contains("TYPE=SUBTITLES") })
        let uriPrefix = try XCTUnwrap(subtitleLine.range(of: "URI=\""))
        let uriSuffix = subtitleLine[uriPrefix.upperBound...]
        let subtitleURI = uriSuffix.prefix { $0 != "\"" }
        let subtitlePlaylist = try XCTUnwrap(URL(string: String(subtitleURI), relativeTo: playlist))
        let (subtitleData, subtitleResponse) = try await URLSession.shared.data(from: subtitlePlaylist)
        XCTAssertEqual((subtitleResponse as? HTTPURLResponse)?.statusCode, 200)
        let subtitleText = String(decoding: subtitleData, as: UTF8.self)
        let segmentName = try XCTUnwrap(subtitleText.split(separator: "\n").first { !$0.hasPrefix("#") })
        let segment = try XCTUnwrap(URL(string: String(segmentName), relativeTo: subtitlePlaylist))
        let (cueData, cueResponse) = try await URLSession.shared.data(from: segment)
        XCTAssertEqual((cueResponse as? HTTPURLResponse)?.statusCode, 200)
        let cueText = String(decoding: cueData, as: UTF8.self)
        XCTAssertTrue(cueText.contains("X-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000"),
                      "Positive Prism delay writes later subtitle timing before the item is created")
        XCTAssertTrue(cueText.contains("Synthetic opening caption"),
                      "Bounding host replay must preserve the packaged subtitle cues")
        let sought = await player.seek(to: CMTime(seconds: 6, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        XCTAssertTrue(sought)
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertGreaterThan(item.currentTime().seconds, 6.1)
        player.pause()
        player.replaceCurrentItem(with: nil)
        await owner.stop().value
        XCTAssertNil(owner.activeAudioStreamIndex)
        var check = URLRequest(url: playlist)
        check.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: check)
            XCTAssertNotEqual((response as? HTTPURLResponse)?.statusCode, 200)
        } catch { /* The listener is closed. */ }
        var subtitleCheck = URLRequest(url: subtitlePlaylist)
        subtitleCheck.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: subtitleCheck)
            XCTAssertNotEqual((response as? HTTPURLResponse)?.statusCode, 200)
        } catch { /* The Prism listener is closed. */ }

        // A force-muxed Prism item is a real HLS media playlist, not the empty
        // file fixture above. Wrap it with a selected local WebVTT sidecar to
        // exercise AVFoundation's direct-HLS media-selection path end to end.
        let fixtureOwner = NativeVideoSource()
        let fixtureItem = try await fixtureOwner.prepare(
            url: source,
            usesClientDemux: true,
            enableSubtitleRenditions: false
        )
        let fixtureURL = try XCTUnwrap((fixtureItem.asset as? AVURLAsset)?.url)
        XCTAssertEqual(fixtureURL.pathExtension.lowercased(), "m3u8")
        let directSidecar = directory.appendingPathComponent("direct-sidecar.vtt")
        try "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nDirect HLS subtitle\n".write(
            to: directSidecar, atomically: true, encoding: .utf8
        )
        let sidecarOwner = NativeVideoSource()
        let sidecarItem = try await sidecarOwner.prepare(
            url: fixtureURL,
            usesClientDemux: false,
            externalSubtitles: [.init(fileURL: directSidecar, language: "en", name: "Direct HLS", durationSeconds: 12)],
            bandwidth: 1_234_567
        )
        try FileManager.default.removeItem(at: directSidecar)
        let sidecarURL = try XCTUnwrap((sidecarItem.asset as? AVURLAsset)?.url)
        let sidecarPlayer = AVPlayer(playerItem: sidecarItem)
        sidecarPlayer.volume = 0
        try await NativeVideoSource.waitUntilReady(sidecarPlayer)
        XCTAssertTrue(sidecarItem.tracks.contains { $0.isEnabled && $0.assetTrack?.mediaType == .audio })
        XCTAssertNotNil(sidecarItem.asset.mediaSelectionGroup(forMediaCharacteristic: .audible))
        let directLegibleGroup = try XCTUnwrap(sidecarItem.asset.mediaSelectionGroup(forMediaCharacteristic: .legible))
        let directOption = try XCTUnwrap(directLegibleGroup.options.first)
        sidecarItem.select(directOption, in: directLegibleGroup)
        XCTAssertTrue(sidecarItem.currentMediaSelection.selectedMediaOption(in: directLegibleGroup) === directOption)
        sidecarItem.select(nil, in: directLegibleGroup)
        XCTAssertNil(sidecarItem.currentMediaSelection.selectedMediaOption(in: directLegibleGroup))
        sidecarPlayer.replaceCurrentItem(with: nil)
        await sidecarOwner.stop().value
        var sidecarCheck = URLRequest(url: sidecarURL)
        sidecarCheck.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: sidecarCheck)
            XCTAssertNotEqual((response as? HTTPURLResponse)?.statusCode, 200)
        } catch { /* The sidecar wrapper listener is closed. */ }
        await fixtureOwner.stop().value
        var fixtureCheck = URLRequest(url: fixtureURL)
        fixtureCheck.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: fixtureCheck)
            XCTAssertNotEqual((response as? HTTPURLResponse)?.statusCode, 200)
        } catch { /* The Prism fixture listener is closed. */ }

        let cancelled = Task { try await owner.prepare(url: source, usesClientDemux: true) }
        cancelled.cancel()
        do { _ = try await cancelled.value; XCTFail("Cancelled startup must not publish a player item") }
        catch { }
        await owner.stop().value
        do {
            _ = try await owner.prepare(url: source, usesClientDemux: true, preferredAudioStreamIndex: 3)
            XCTFail("An unavailable audio stream must not silently select another track")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .unsupportedAudioStream(3))
        }
        XCTAssertNil(owner.activeAudioStreamIndex)
        // Replay owns a new session; closing a predecessor must not stop it.
        let replay = try await owner.prepare(url: source, usesClientDemux: true)
        XCTAssertNotEqual((replay.asset as? AVURLAsset)?.url, playlist)
        await owner.stop().value
    }

    /// Opt-in evidence for the exact MKV -> Prism -> AVPlayer master route.
    /// The public Apple source carries a real Profile 5 RPU, which ffmpeg cannot
    /// synthesize. This only inspects the bytes and independent seek fragments;
    /// it deliberately does not claim that this Mac's display presents Dolby
    /// Vision. See `apple/scripts/qualify_dv5_fixture.py`.
    func testPublicDV5MKVRetainsMasterInitAndRPUsThroughSubtitleMaster() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV5_MKV_FIXTURE"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV5_MKV_FIXTURE to the bounded public Apple DV5 MKV fixture")
        }
        try await assertDolbyVisionFixture(
            URL(fileURLWithPath: value), expectedPrimaryCodec: "dvh1.05.", expectedRange: "PQ",
            expectedSupplementalCodec: nil, expectedCompatibility: "0", withExternalSubtitle: false
        )
        try await assertDolbyVisionFixture(
            URL(fileURLWithPath: value), expectedPrimaryCodec: "dvh1.05.", expectedRange: "PQ",
            expectedSupplementalCodec: nil, expectedCompatibility: "0", withExternalSubtitle: true
        )
    }

    /// These fixtures are generated with genuine Profile 8 RPU data by the
    /// local dovi_tool qualifier. They exercise metadata delivery only, not
    /// graded Dolby Vision output or display presentation.
    func testSyntheticDV8MKVsRetainMasterInitAndRPUsThroughSubtitleMaster() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV8_MKV_FIXTURES"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV8_MKV_FIXTURES to the generated dv8-1.mkv:dv8-4.mkv files")
        }
        let fixtures = value.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        guard fixtures.count == 2 else {
            XCTFail("LUMINA_DV8_MKV_FIXTURES must contain dv8-1 then dv8-4")
            return
        }
        try await assertDolbyVisionFixture(
            fixtures[0], expectedPrimaryCodec: "hvc1.", expectedRange: "PQ",
            expectedSupplementalCodec: "dvh1.08.01/db1p", expectedCompatibility: "1", withExternalSubtitle: false
        )
        try await assertDolbyVisionFixture(
            fixtures[0], expectedPrimaryCodec: "hvc1.", expectedRange: "PQ",
            expectedSupplementalCodec: "dvh1.08.01/db1p", expectedCompatibility: "1", withExternalSubtitle: true
        )
        try await assertDolbyVisionFixture(
            fixtures[1], expectedPrimaryCodec: "hvc1.", expectedRange: "HLG",
            expectedSupplementalCodec: "dvh1.08.01/db4h", expectedCompatibility: "4", withExternalSubtitle: false
        )
        try await assertDolbyVisionFixture(
            fixtures[1], expectedPrimaryCodec: "hvc1.", expectedRange: "HLG",
            expectedSupplementalCodec: "dvh1.08.01/db4h", expectedCompatibility: "4", withExternalSubtitle: true
        )
    }

    /// Profile 7 is remuxed to Profile 8.1 structurally. This does not claim
    /// Dolby Vision display presentation or retention of a FEL contribution.
    func testProfile7MKVsConvertToProfile81WithoutEnhancementLayer() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_MKV_FIXTURES"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_MKV_FIXTURES to generated p7-mel.mkv:p7-fel.mkv files")
        }
        let fixtures = value.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        guard fixtures.count == 2 else {
            XCTFail("LUMINA_DV7_MKV_FIXTURES must contain p7-mel then p7-fel")
            return
        }
        XCTAssertTrue(NativeVideoSource.supportsDolbyVisionProfile7Conversion,
                      "A successful P7 conversion requires the host's Libdovi build")
        let fixtureDirectory = fixtures[0].deletingLastPathComponent()
        for (fixture, expectedName) in zip(fixtures, ["p81-mel.rpu", "p81-fel.rpu"]) {
            let expected = fixtureDirectory.appendingPathComponent(expectedName)
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: false)
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: true)
        }
    }

    func testM2TSProfile7PairsConvertToProfile81() async throws {
        guard let path = ProcessInfo.processInfo.environment["LUMINA_DV7_M2TS_DIRECTORY"], !path.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_M2TS_DIRECTORY to the generated M2TS corpus")
        }
        let directory = URL(fileURLWithPath: path)
        for kind in ["mel", "fel"] {
            let fixture = directory.appendingPathComponent("p7-m2ts-\(kind).m2ts")
            let expected = directory.appendingPathComponent("p81-m2ts-\(kind).rpu")
            for subtitles in [false, true] {
                try await assertProfile7Conversion(fixture, expectedRPU: expected,
                                                  withExternalSubtitle: subtitles, separateTracks: true,
                                                  transportPairing: true)
            }
        }
    }

    func testDolbyVisionConversionSnapshotRejectsIncompleteConversionEvidence() {
        let noConvertedRPUs = NativeVideoSource.DolbyVisionConversionSnapshot(
            convertedRPUs: 0, failedRPUs: 0, droppedEnhancementLayerNALs: 1,
            staleUnconvertedPackets: 0, isClean: false
        )
        XCTAssertTrue(noConvertedRPUs.hasFailure,
                      "Published conversion statistics with no converted RPU cannot certify P8.1")

        let noDroppedEnhancementLayer = NativeVideoSource.DolbyVisionConversionSnapshot(
            convertedRPUs: 1, failedRPUs: 0, droppedEnhancementLayerNALs: 0,
            staleUnconvertedPackets: 0, isClean: false
        )
        XCTAssertTrue(noDroppedEnhancementLayer.hasFailure,
                      "A P7 conversion that retained its EL cannot certify a single-layer master")
    }

    func testSeparateTrackProfile7MKVsConvertToProfile81() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_SEPARATE_TRACK_FIXTURES"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_SEPARATE_TRACK_FIXTURES to generated separate-track MEL:FEL MKVs")
        }
        let fixtures = value.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        XCTAssertEqual(fixtures.count, 2)
        for (fixture, expectedName) in zip(fixtures, ["p81-separate-mel.rpu", "p81-separate-fel.rpu"]) {
            let expected = fixture.deletingLastPathComponent().appendingPathComponent(expectedName)
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: false, separateTracks: true)
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: true, separateTracks: true)
        }
        if let path = ProcessInfo.processInfo.environment["LUMINA_DV7_SEPARATE_TRACK_REORDERED_FIXTURE"], !path.isEmpty {
            let fixture = URL(fileURLWithPath: path)
            let expected = fixture.deletingLastPathComponent().appendingPathComponent("p81-separate-reordered-mel.rpu")
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: false, separateTracks: true)
            try await assertProfile7Conversion(fixture, expectedRPU: expected, withExternalSubtitle: true, separateTracks: true)
        }
    }

    func testSeparateTrackProfile7RejectsUnpairedOrInvalidEnhancement() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_SEPARATE_TRACK_INVALID_FIXTURES"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_SEPARATE_TRACK_INVALID_FIXTURES to generated invalid separate-track MKVs")
        }
        for path in value.split(separator: ":") {
            let fixture = URL(fileURLWithPath: String(path))
            XCTAssertTrue(FileManager.default.isReadableFile(atPath: fixture.path))
            let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
            defer { owner.stop() }
            var prepared = false
            do {
                let item = try await owner.prepare(url: fixture, usesClientDemux: true)
                prepared = true
                let masterURL = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
                let master = try await text(at: masterURL)
                let mediaURL = try hlsVariant(master, relativeTo: masterURL)
                let media = try await text(at: mediaURL)
                for segment in try hlsSegments(media, relativeTo: mediaURL) {
                    try await owner.validateDolbyVisionConversion()
                    do {
                        _ = try await URLSession.shared.data(for: URLRequest(url: segment, timeoutInterval: 1))
                    }
                    catch {
                        // A producer can refuse a pair before serving the
                        // requested fragment. Preserve its typed diagnosis.
                        try await owner.validateDolbyVisionConversion()
                        throw error
                    }
                }
                try await owner.validateDolbyVisionConversion()
                XCTFail("Invalid separate-track input must reject P8.1: \(fixture.lastPathComponent)")
            } catch let error as NativeVideoSource.SelectionError {
                XCTAssertEqual(error, .invalidDolbyVisionConversion, fixture.lastPathComponent)
                if prepared {
                    let evidence = await owner.dolbyVisionConversion
                    XCTAssertEqual(evidence?.hasFailure, true, "Terminal evidence must survive until explicit stop")
                    let generic = NSError(domain: AVFoundationErrorDomain, code: AVError.unknown.rawValue)
                    let mapped = await owner.playbackError(for: generic)
                    XCTAssertEqual(mapped as? NativeVideoSource.SelectionError, .invalidDolbyVisionConversion)
                    let cancelled = await owner.playbackError(for: CancellationError())
                    XCTAssertTrue(cancelled is CancellationError)
                    let transport = URLError(.notConnectedToInternet)
                    let retainedTransport = await owner.playbackError(for: transport)
                    XCTAssertEqual((retainedTransport as NSError).code, transport.errorCode)
                    XCTAssertEqual((retainedTransport as NSError).domain, NSURLErrorDomain)
                }
            }
            await owner.stop().value
            let stoppedConversion = await owner.dolbyVisionConversion
            XCTAssertNil(stoppedConversion, "Explicit stop must clear evidence before a replacement source")
        }
    }

    func testSeparateTrackProfile7BasePlaybackAdvancesAndSeeks() async throws {
        guard let paths = ProcessInfo.processInfo.environment["LUMINA_DV7_SEPARATE_TRACK_FIXTURES"], !paths.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_SEPARATE_TRACK_FIXTURES for local native base-picture playback")
        }
        var fixtures = paths.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        if let path = ProcessInfo.processInfo.environment["LUMINA_DV7_SEPARATE_TRACK_REORDERED_FIXTURE"], !path.isEmpty {
            fixtures.append(URL(fileURLWithPath: path))
        }
        try await assertBasePlaybackAdvancesAndSeeks(fixtures)
    }

    func testM2TSRequiredPairingRejectsInvalidTransportOrPackets() async throws {
        guard let path = ProcessInfo.processInfo.environment["LUMINA_DV7_M2TS_DIRECTORY"], !path.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_M2TS_DIRECTORY to the generated M2TS corpus")
        }
        let directory = URL(fileURLWithPath: path)
        for name in ["p7-m2ts-invalid-no-dovi.m2ts", "p7-m2ts-invalid-dependency.m2ts",
                     "p7-m2ts-invalid-ambiguous-el.m2ts", "p7-m2ts-invalid-missing-el-au.m2ts",
                     "p7-m2ts-invalid-shifted-el-pts.m2ts", "p7-m2ts-invalid-final-rpu.m2ts"] {
            let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
            defer { owner.stop() }
            do {
                _ = try await owner.prepare(url: directory.appendingPathComponent(name), usesClientDemux: true,
                                            requireDolbyVisionTransportPairing: true)
                // Packet-level corruption can arrive after the initial item.
                // These bounded fixtures finish inside the producer lead window.
                let deadline = ContinuousClock.now + .seconds(5)
                while ContinuousClock.now < deadline {
                    if await owner.dolbyVisionConversion?.hasFailure == true { break }
                    try await Task.sleep(for: .milliseconds(20))
                }
                try await owner.validateDolbyVisionConversion()
                XCTFail("Required pairing must reject invalid transport or packets: \(name)")
            } catch let error as NativeVideoSource.SelectionError {
                XCTAssertEqual(error, .invalidDolbyVisionConversion, name)
            }
        }
    }

    func testM2TSProfile7BasePlaybackAdvancesAndSeeks() async throws {
        guard let path = ProcessInfo.processInfo.environment["LUMINA_DV7_M2TS_DIRECTORY"], !path.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_M2TS_DIRECTORY for local M2TS base-picture playback")
        }
        let directory = URL(fileURLWithPath: path)
        let fixtures = ["p7-m2ts-mel.m2ts", "p7-m2ts-fel.m2ts", "p7-m2ts-nonconventional-mel.m2ts"]
            .map { directory.appendingPathComponent($0) }
        try await assertBasePlaybackAdvancesAndSeeks(fixtures, transportPairing: true)
        let server = try ProgressiveAudioHTTPFixture(bytes: Data(contentsOf: fixtures[0]), mime: "video/mp2t", delay: 0)
        let remote = try await server.start().deletingLastPathComponent().appendingPathComponent("paired.m2ts")
        defer { server.stop() }
        try await assertBasePlaybackAdvancesAndSeeks([remote], transportPairing: true)
    }

    /// An opt-in public workflow for a cold, late M2TS seek. The engine suite
    /// owns exact RPU/VCL association and its private keyframe-cache evidence;
    /// this test keeps the native AVPlayer route, source identity, and range
    /// behaviour observable without depending on engine internals.
    func testM2TSProfile7ColdSeekAvoidsPrefixPlayback() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_M2TS_COLD_SEEK_FIXTURE"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_M2TS_COLD_SEEK_FIXTURE to the generated 96-second M2TS fixture")
        }
        let fixture = URL(fileURLWithPath: value)
        let manifestURL = fixture.deletingLastPathComponent().appendingPathComponent("manifest.json")
        let manifestData = try Data(contentsOf: manifestURL)
        let manifest = try XCTUnwrap(try JSONSerialization.jsonObject(with: manifestData) as? [String: Any])
        let coldSeek = try XCTUnwrap(manifest["coldSeek"] as? [String: Any])
        let fixtureSummary = try XCTUnwrap(coldSeek["fixture"] as? [String: Any])
        let anchor = try XCTUnwrap(fixtureSummary["coldSeek"] as? [String: Any])
        let expectedHash = try XCTUnwrap(fixtureSummary["sha256"] as? String)
        XCTAssertEqual(fixtureSummary["container"] as? String, fixture.lastPathComponent)
        let targetSeconds = try XCTUnwrap(anchor["targetSeconds"] as? NSNumber).doubleValue
        let targetByteOffset = try XCTUnwrap(anchor["basePESByteOffset"] as? NSNumber).intValue
        let sourceBytes = try Data(contentsOf: fixture)
        XCTAssertEqual(SHA256.hash(data: sourceBytes).map { String(format: "%02x", $0) }.joined(), expectedHash,
                       "The cold-seek manifest must describe the exact source served to Prism")

        // Pace this long source so a producer cannot race to EOF while the
        // first user-visible seek is meant to exercise a cold late target.
        let server = try ProgressiveAudioHTTPFixture(bytes: sourceBytes, mime: "video/mp2t", delay: 2)
        let remote = try await server.start().deletingLastPathComponent()
            .appendingPathComponent("cold-seek-\(UUID().uuidString).m2ts")
        defer { server.stop() }

        let rejected = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
        defer { rejected.stop() }
        do {
            _ = try await rejected.prepare(url: remote, usesClientDemux: false,
                                           requireDolbyVisionTransportPairing: true)
            XCTFail("A paired M2TS source must not bypass the client-demux route")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .invalidDolbyVisionConversion)
        }

        let owner = NativeVideoSource()
        let item = try await owner.prepare(url: remote, usesClientDemux: true,
                                           requireDolbyVisionTransportPairing: true)
        let frames = AVPlayerItemVideoOutput(pixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ])
        item.add(frames)
        let player = AVPlayer(playerItem: item)
        player.volume = 0
        defer { player.replaceCurrentItem(with: nil); owner.stop() }
        try await NativeVideoSource.waitUntilReady(player)
        let duration = item.duration.seconds
        let seekableEnd = item.seekableTimeRanges
            .map { CMTimeRangeGetEnd($0.timeRangeValue).seconds }
            .filter(\.isFinite)
            .max() ?? 0
        guard duration.isFinite, duration > 95, seekableEnd >= 95 else {
            XCTFail("The first playable item must expose a finite full cold-seek scrubber range; duration=\(duration), seekableEnd=\(seekableEnd)")
            return
        }

        let baselineRanges = server.requestedRangeStarts.count
        let baselineBytes = try XCTUnwrap(owner.sourceBytesRead,
                                          "Coordinated HTTP must report origin bytes before the cold seek")
        XCTAssertLessThan(baselineBytes, Int64(sourceBytes.count / 2),
                          "Startup must not preflight more than half the cold source")
        func seekAndDecode(_ target: Double, play: Bool = false) async throws -> Duration {
            let started = ContinuousClock.now
            let finished = await player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                                             toleranceBefore: .zero, toleranceAfter: .zero)
            XCTAssertTrue(finished, "Cold seek must complete at \(target)s")
            if play { player.play() }
            let deadline = started + .seconds(8)
            var decoded = false
            while ContinuousClock.now < deadline {
                let time = item.currentTime()
                if abs(time.seconds - target) < 0.75,
                   frames.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil {
                    decoded = true
                    break
                }
                try await Task.sleep(for: .milliseconds(20))
            }
            XCTAssertTrue(decoded, "Cold paired M2TS seek must render \(target)s")
            return started.duration(to: .now)
        }

        let firstSeekElapsed = try await seekAndDecode(targetSeconds, play: true)
        let postReadyRanges = Array(server.requestedRangeStarts.dropFirst(baselineRanges))
        let minimumLateRange = targetByteOffset / 2
        XCTAssertTrue(postReadyRanges.contains { $0 >= minimumLateRange },
                      "The first late seek must request a range near its verified transport anchor")
        let bytesAfterFirstLateSeek = try XCTUnwrap(owner.sourceBytesRead,
                                                     "Coordinated HTTP must report origin bytes after the cold seek")
        XCTAssertLessThan(bytesAfterFirstLateSeek - baselineBytes, Int64(targetByteOffset / 2),
                          "The initial late seek must not linearly walk the source prefix after startup probing")
        XCTAssertLessThanOrEqual(firstSeekElapsed, .seconds(8),
                                 "The first cold seek must include both seek completion and frame delivery in its budget")
        print("Cold M2TS first seek elapsed=\(firstSeekElapsed) sourceBytes=\(bytesAfterFirstLateSeek - baselineBytes)")
        for target in [88.0, 68.0, targetSeconds] {
            try await seekAndDecode(target)
        }
        try await owner.validateDolbyVisionConversion()
    }

    private func assertBasePlaybackAdvancesAndSeeks(_ fixtures: [URL], transportPairing: Bool = false) async throws {
        for fixture in fixtures {
            let owner = NativeVideoSource()
            let item = try await owner.prepare(url: fixture, usesClientDemux: true,
                                               requireDolbyVisionTransportPairing: transportPairing)
            let frames = AVPlayerItemVideoOutput(pixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
            ])
            item.add(frames)
            let player = AVPlayer(playerItem: item)
            player.volume = 0
            defer { player.replaceCurrentItem(with: nil); owner.stop() }
            try await NativeVideoSource.waitUntilReady(player)
            player.play()
            var decoded = false
            let deadline = ContinuousClock.now + .seconds(10)
            while ContinuousClock.now < deadline {
                let time = item.currentTime()
                if frames.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil { decoded = true }
                if decoded && time.seconds >= 1 { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            XCTAssertTrue(decoded, fixture.lastPathComponent)
            XCTAssertGreaterThanOrEqual(item.currentTime().seconds, 1)
            player.pause()
            for target in [6.2, 0.5] {
                let finished = await player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                                                 toleranceBefore: .zero, toleranceAfter: .zero)
                XCTAssertTrue(finished)
                let seekDeadline = ContinuousClock.now + .seconds(5)
                var decodedTarget = false
                while ContinuousClock.now < seekDeadline {
                    let time = item.currentTime()
                    if abs(time.seconds - target) < 0.5,
                       frames.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil {
                        decodedTarget = true
                        break
                    }
                    try await Task.sleep(for: .milliseconds(20))
                }
                XCTAssertTrue(decodedTarget, "Paired-stream seek must render \(target)s: \(fixture.lastPathComponent)")
            }
            try await owner.validateDolbyVisionConversion()
        }
    }

    func testProfile7InvalidRPURefusesSingleLayerDeclaration() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_INVALID_RPU_FIXTURE"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_INVALID_RPU_FIXTURE to generated p7-invalid-rpu.mkv")
        }
        let fixture = URL(fileURLWithPath: value)
        guard FileManager.default.isReadableFile(atPath: fixture.path) else {
            throw XCTSkip("Missing generated invalid P7 fixture")
        }
        let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
        defer { owner.stop() }
        do {
            _ = try await owner.prepare(url: fixture, usesClientDemux: true)
            XCTFail("A P7 stream with a malformed RPU must reject its P8.1 declaration before publishing an item")
        } catch let error as NativeVideoSource.SelectionError {
            XCTAssertEqual(error, .invalidDolbyVisionConversion)
        }
    }

    func testProfile7FinalPartialSegmentRejectsInvalidConversion() async throws {
        guard let value = ProcessInfo.processInfo.environment["LUMINA_DV7_INVALID_TAIL_FIXTURES"], !value.isEmpty else {
            throw XCTSkip("Set LUMINA_DV7_INVALID_TAIL_FIXTURES to long-final-bad:two-frame-final-bad MKVs")
        }
        let fixtures = value.split(separator: ":").map { URL(fileURLWithPath: String($0)) }
        XCTAssertEqual(fixtures.count, 2, "Exercise both a regular-cut tail and a tail-only source")
        for fixture in fixtures {
            XCTAssertTrue(FileManager.default.isReadableFile(atPath: fixture.path))
            let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
            defer { owner.stop() }
            do {
                let item = try await owner.prepare(url: fixture, usesClientDemux: true)
                let masterURL = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
                let master = try await text(at: masterURL)
                let mediaURL = try hlsVariant(master, relativeTo: masterURL)
                let media = try await text(at: mediaURL)
                let segments = try hlsSegments(media, relativeTo: mediaURL)
                XCTAssertFalse(segments.isEmpty)
                // Demand every fragment: planned playlists can name a tail before
                // its packets have been read and its conversion verdict exists.
                for segment in segments { _ = try await bytes(at: segment) }
                let conversion = await owner.dolbyVisionConversion
                let snapshot = try XCTUnwrap(conversion, fixture.lastPathComponent)
                XCTAssertGreaterThan(snapshot.failedRPUs + snapshot.staleUnconvertedPackets, 0,
                                     "The malformed final RPU must reach the published verdict")
                XCTAssertTrue(snapshot.hasFailure)
                try await owner.validateDolbyVisionConversion()
                XCTFail("A malformed final partial segment must reject its P8.1 declaration")
            } catch let error as NativeVideoSource.SelectionError {
                // Short sources can reach EOF before prepare returns; longer
                // sources can be rejected by the same check during playback.
                XCTAssertEqual(error, .invalidDolbyVisionConversion)
            }
            await owner.stop().value
        }
    }

    private func assertProfile7Conversion(
        _ fixture: URL, expectedRPU: URL, withExternalSubtitle: Bool, separateTracks: Bool = false,
        transportPairing: Bool = false
    ) async throws {
        guard FileManager.default.isReadableFile(atPath: fixture.path),
              FileManager.default.isReadableFile(atPath: expectedRPU.path) else {
            throw XCTSkip("Missing generated Profile 7 conversion fixture")
        }
        // A transport BL correctly has no DOVI configuration; its role comes
        // from the explicit PMT dependency, not an invented BL record.
        if !transportPairing {
            XCTAssertEqual(try doviConfiguration(in: fixture), [
                "dv_profile": "7", "dv_level": "6", "rpu_present_flag": separateTracks ? "0" : "1",
                "el_present_flag": separateTracks ? "0" : "1", "bl_present_flag": "1",
                "dv_bl_signal_compatibility_id": "6"
            ])
        }

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let subtitle = directory.appendingPathComponent("p7.vtt")
        try "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nP7 fixture\n".write(
            to: subtitle, atomically: true, encoding: .utf8
        )
        let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
        defer { owner.stop() }
        let item = try await owner.prepare(
            url: fixture,
            usesClientDemux: true,
            requireDolbyVisionTransportPairing: transportPairing,
            externalSubtitles: withExternalSubtitle
                ? [.init(fileURL: subtitle, language: "en", name: "P7 fixture")]
                : []
        )
        let masterURL = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
        let master = try await text(at: masterURL)
        XCTAssertTrue(master.contains("CODECS=\"hvc1."))
        XCTAssertTrue(master.contains("SUPPLEMENTAL-CODECS=\"dvh1.08.06/db1p\""))
        XCTAssertTrue(master.contains("VIDEO-RANGE=PQ"))
        if withExternalSubtitle { XCTAssertTrue(master.contains("SUBTITLES=")) }

        let mediaURL = try hlsVariant(master, relativeTo: masterURL)
        let media = try await completeMediaPlaylist(at: mediaURL)
        let initialization = try await bytes(at: hlsMap(media, relativeTo: mediaURL))
        XCTAssertTrue(contains(initialization, Data("hvc1".utf8)))
        XCTAssertTrue(contains(initialization, Data("dvvC".utf8)))

        let expectedPayloads = try expectedRPUPayloads(at: expectedRPU)
        let sourceNals = try hevcNALs(in: fixture, directory: directory, name: "p7-source")
        if separateTracks {
            XCTAssertGreaterThan(Set(expectedPayloads).count, 1,
                                 "Separate-track timing evidence needs distinguishable RPUs")
            XCTAssertEqual(try doviConfiguration(in: fixture, videoTrack: 1), [
                "dv_profile": "7", "dv_level": "6", "rpu_present_flag": "1",
                "el_present_flag": "1", "bl_present_flag": "0",
                "dv_bl_signal_compatibility_id": "6"
            ])
            XCTAssertFalse(sourceNals.contains { hevcNALType($0) == 62 || hevcNALType($0) == 63 })
            let enhancement = try hevcNALs(in: fixture, directory: directory, name: "p7-enhancement", videoTrack: 1)
            XCTAssertEqual(enhancement.filter { hevcNALType($0) == 62 }.count, expectedPayloads.count)
            XCTAssertTrue(enhancement.contains { hevcNALType($0) <= 31 })
        } else {
            XCTAssertTrue(sourceNals.contains { hevcNALType($0) == 63 }, "P7 fixture must contain an EL NAL")
        }
        let sourceBaseVCL = sourceNals.filter { hevcNALType($0) <= 31 && hevcNALLayer($0) == 0 }
        XCTAssertFalse(sourceBaseVCL.isEmpty)

        var deliveredBaseVCL: [Data] = []
        var deliveredRPUPayloads: [Data] = []
        var deliveredAudioDTS: [Int64] = []
        for (index, segment) in try hlsSegments(media, relativeTo: mediaURL).enumerated() {
            let fragment = try await bytes(at: segment)
            let seekTarget = directory.appendingPathComponent("p7-seek-\(index).mp4")
            try (initialization + fragment).write(to: seekTarget, options: .atomic)
            try runFFmpeg(["-v", "error", "-nostdin", "-i", seekTarget.path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"])
            XCTAssertEqual(try doviConfiguration(in: seekTarget), [
                "dv_profile": "8", "dv_level": "6", "rpu_present_flag": "1",
                "el_present_flag": "0", "bl_present_flag": "1",
                "dv_bl_signal_compatibility_id": "1"
            ], "P7 seek fragment \(index) must declare its converted P8.1 payload")
            let nals = try hevcNALs(in: seekTarget, directory: directory, name: "p7-seek-\(index)")
            XCTAssertFalse(nals.contains { hevcNALType($0) == 63 || hevcNALLayer($0) != 0 },
                           "P7 seek fragment \(index) retained an enhancement layer")
            deliveredBaseVCL.append(contentsOf: nals.filter { hevcNALType($0) <= 31 && hevcNALLayer($0) == 0 })
            deliveredRPUPayloads.append(contentsOf: nals.filter { hevcNALType($0) == 62 }.map { Data($0.dropFirst(2)) })
            if separateTracks { deliveredAudioDTS.append(contentsOf: try audioPacketDTS(in: seekTarget)) }
        }
        let conversion = await owner.dolbyVisionConversion
        XCTAssertEqual(deliveredBaseVCL, sourceBaseVCL, "P7 conversion must leave base-layer picture NALs unchanged")
        XCTAssertEqual(deliveredRPUPayloads.count, sourceBaseVCL.count, "Every P7 video packet needs a converted RPU")
        XCTAssertEqual(deliveredRPUPayloads, expectedPayloads,
                       "P7 RPUs must be rewritten to the ordered generated P8.1 vectors, not merely retagged")
        if separateTracks {
            if deliveredAudioDTS.isEmpty {
                let audioURL = try hlsAudioPlaylist(master, relativeTo: masterURL)
                let audio = try await completeMediaPlaylist(at: audioURL)
                let audioInitialization = try await bytes(at: hlsMap(audio, relativeTo: audioURL))
                for (index, segment) in try hlsSegments(audio, relativeTo: audioURL).enumerated() {
                    let fragment = try await bytes(at: segment)
                    let audioTarget = directory.appendingPathComponent("p7-audio-\(index).mp4")
                    try (audioInitialization + fragment).write(to: audioTarget, options: .atomic)
                    deliveredAudioDTS.append(contentsOf: try audioPacketDTS(in: audioTarget))
                }
            }
            XCTAssertFalse(deliveredAudioDTS.isEmpty, "Separate-track packaging must retain the selected AAC stream")
            XCTAssertTrue(zip(deliveredAudioDTS, deliveredAudioDTS.dropFirst()).allSatisfy { pair in pair.0 < pair.1 },
                          "Waiting for EL metadata must not reverse audio decode timestamps")
        }

        let snapshot = try XCTUnwrap(conversion)
        XCTAssertEqual(snapshot.convertedRPUs, sourceBaseVCL.count,
                       "Successful final partial segments must contribute their converted RPUs too")
        XCTAssertEqual(snapshot.failedRPUs, 0)
        XCTAssertGreaterThan(snapshot.droppedEnhancementLayerNALs, 0)
        XCTAssertEqual(snapshot.staleUnconvertedPackets, 0)
        XCTAssertTrue(snapshot.isClean)
        XCTAssertFalse(snapshot.hasFailure)
        try await owner.validateDolbyVisionConversion()
    }

    private func assertDolbyVisionFixture(
        _ fixture: URL, expectedPrimaryCodec: String, expectedRange: String,
        expectedSupplementalCodec: String?, expectedCompatibility: String, withExternalSubtitle: Bool
    ) async throws {
        guard FileManager.default.isReadableFile(atPath: fixture.path) else {
            throw XCTSkip("Missing local Dolby Vision MKV fixture: \(fixture.lastPathComponent)")
        }
        let sourceConfiguration = try doviConfiguration(in: fixture)
        XCTAssertEqual(sourceConfiguration["dv_bl_signal_compatibility_id"], expectedCompatibility)

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let subtitle = directory.appendingPathComponent("dv.vtt")
        try "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nDV fixture\n".write(
            to: subtitle, atomically: true, encoding: .utf8
        )
        let owner = NativeVideoSource(display: .init(isHDRReady: true, isDolbyVisionCapable: true))
        defer { owner.stop() }
        let item = try await owner.prepare(
            url: fixture,
            usesClientDemux: true,
            externalSubtitles: withExternalSubtitle
                ? [.init(fileURL: subtitle, language: "en", name: "DV fixture")]
                : []
        )
        let masterURL = try XCTUnwrap((item.asset as? AVURLAsset)?.url)
        let master = try await text(at: masterURL)
        XCTAssertTrue(master.contains("CODECS=\"\(expectedPrimaryCodec)"), "The Dolby Vision primary must remain in the master")
        XCTAssertTrue(master.contains("VIDEO-RANGE=\(expectedRange)"), "The master must retain its dynamic-range claim")
        if let expectedSupplementalCodec {
            XCTAssertTrue(master.contains("SUPPLEMENTAL-CODECS=\"\(expectedSupplementalCodec)\""),
                          "The Profile 8 supplemental declaration must retain its compatibility brand")
        }
        if withExternalSubtitle {
            XCTAssertTrue(master.contains("SUBTITLES="), "Adding an external subtitle must not replace Prism's master")
        } else {
            XCTAssertFalse(master.contains("SUBTITLES="), "The normal Prism master must not require the sidecar wrapper")
        }

        let mediaURL = try hlsVariant(master, relativeTo: masterURL)
        let media = try await text(at: mediaURL)
        let initURL = try hlsMap(media, relativeTo: mediaURL)
        let segments = try hlsSegments(media, relativeTo: mediaURL)
        let initialization = try await bytes(at: initURL)
        XCTAssertTrue(contains(initialization, Data(expectedPrimaryCodec.prefix(4).utf8)))
        XCTAssertTrue(contains(initialization, Data("dvcC".utf8)) || contains(initialization, Data("dvvC".utf8)))

        let sourceRPUs = try rpuHashes(in: fixture, directory: directory, name: "source")
        XCTAssertFalse(sourceRPUs.isEmpty, "The Dolby Vision source must carry RPU NAL units")
        XCTAssertEqual(sourceRPUs.count, try videoPacketCount(in: fixture),
                       "The bounded fixture must carry one RPU per video packet")
        var deliveredRPUs: [Data] = []
        for (index, segment) in segments.enumerated() {
            let fragment = try await bytes(at: segment)
            let seekTarget = directory.appendingPathComponent("seek-\(index).mp4")
            try (initialization + fragment).write(to: seekTarget, options: .atomic)
            // Every independent HLS fragment is the byte-level seek target. A
            // fresh demux verifies it can start without an earlier fragment.
            try runFFmpeg(["-v", "error", "-nostdin", "-i", seekTarget.path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"])
            let fragmentRPUs = try rpuHashes(in: seekTarget, directory: directory, name: "segment-\(index)")
            XCTAssertFalse(fragmentRPUs.isEmpty, "Seek fragment \(index) lost its Dolby Vision RPU")
            XCTAssertEqual(fragmentRPUs.count, try videoPacketCount(in: seekTarget),
                           "Seek fragment \(index) must retain one RPU per video packet")
            deliveredRPUs.append(contentsOf: fragmentRPUs)
            XCTAssertEqual(try doviConfiguration(in: seekTarget), sourceConfiguration,
                           "Seek fragment \(index) changed the Dolby Vision configuration record")
        }
        XCTAssertEqual(deliveredRPUs, sourceRPUs,
                       "The complete HLS delivery changed, dropped, or reordered a Dolby Vision RPU")
    }

    private func completeMediaPlaylist(at url: URL) async throws -> String {
        let deadline = ContinuousClock.now + .seconds(20)
        while ContinuousClock.now < deadline {
            let playlist = try await text(at: url)
            if playlist.contains("#EXT-X-ENDLIST") { return playlist }
            try await Task.sleep(for: .milliseconds(50))
        }
        throw NSError(domain: "NativeVideoSourceTests", code: 11,
                      userInfo: [NSLocalizedDescriptionKey: "The bounded fixture did not publish its complete playlist"])
    }

    private func text(at url: URL) async throws -> String {
        String(decoding: try await bytes(at: url), as: UTF8.self)
    }

    private func bytes(at url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        let (data, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200, "Expected a Prism loopback response")
        return data
    }

    private func hlsVariant(_ master: String, relativeTo masterURL: URL) throws -> URL {
        let lines = master.split(whereSeparator: \.isNewline).map(String.init)
        guard let index = lines.firstIndex(where: { $0.hasPrefix("#EXT-X-STREAM-INF:") }),
              let name = lines.dropFirst(index + 1).first(where: { !$0.isEmpty && !$0.hasPrefix("#") }),
              let url = URL(string: name, relativeTo: masterURL)?.absoluteURL else {
            throw NSError(domain: "NativeVideoSourceTests", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing Prism media variant"])
        }
        return url
    }

    private func hlsMap(_ media: String, relativeTo mediaURL: URL) throws -> URL {
        guard let line = media.split(whereSeparator: \.isNewline).map(String.init).first(where: { $0.hasPrefix("#EXT-X-MAP:") }),
              let range = line.range(of: "URI=\"") else {
            throw NSError(domain: "NativeVideoSourceTests", code: 2, userInfo: [NSLocalizedDescriptionKey: "Missing HLS init map"])
        }
        let raw = line[range.upperBound...].prefix { $0 != "\"" }
        guard let url = URL(string: String(raw), relativeTo: mediaURL)?.absoluteURL else {
            throw NSError(domain: "NativeVideoSourceTests", code: 3, userInfo: [NSLocalizedDescriptionKey: "Invalid HLS init map"])
        }
        return url
    }

    private func hlsSegments(_ media: String, relativeTo mediaURL: URL) throws -> [URL] {
        let names = media.split(whereSeparator: \.isNewline).map(String.init).filter { !$0.isEmpty && !$0.hasPrefix("#") }
        guard !names.isEmpty else {
            throw NSError(domain: "NativeVideoSourceTests", code: 4, userInfo: [NSLocalizedDescriptionKey: "No independent HLS segments"])
        }
        return try names.map {
            guard let url = URL(string: $0, relativeTo: mediaURL)?.absoluteURL else {
                throw NSError(domain: "NativeVideoSourceTests", code: 5, userInfo: [NSLocalizedDescriptionKey: "Invalid HLS segment"])
            }
            return url
        }
    }

    private func hlsAudioPlaylist(_ master: String, relativeTo masterURL: URL) throws -> URL {
        let declarations = master.split(whereSeparator: \.isNewline).filter { $0.hasPrefix("#EXT-X-MEDIA:TYPE=AUDIO,") }
        XCTAssertEqual(declarations.count, 1, "The fixture has one stream-copyable AAC source")
        guard let declaration = declarations.first,
              let start = declaration.range(of: "URI=\""),
              let end = declaration[start.upperBound...].firstIndex(of: "\""),
              let url = URL(string: String(declaration[start.upperBound..<end]), relativeTo: masterURL)?.absoluteURL else {
            throw NSError(domain: "NativeVideoSourceTests", code: 13,
                          userInfo: [NSLocalizedDescriptionKey: "Missing declared AAC rendition"])
        }
        return url
    }

    private func rpuHashes(in source: URL, directory: URL, name: String) throws -> [Data] {
        try hevcNALs(in: source, directory: directory, name: name)
            .filter { hevcNALType($0) == 62 }
            .map { Data(SHA256.hash(data: $0)) }
    }

    private func hevcNALs(in source: URL, directory: URL, name: String, videoTrack: Int = 0) throws -> [Data] {
        let elementary = directory.appendingPathComponent("\(name).hevc")
        try runFFmpeg(["-v", "error", "-nostdin", "-y", "-i", source.path, "-map", "0:v:\(videoTrack)", "-c", "copy", "-bsf:v", "hevc_mp4toannexb", elementary.path])
        return annexBNALs(try Data(contentsOf: elementary))
    }

    private func expectedRPUPayloads(at source: URL) throws -> [Data] {
        let bytes = try Data(contentsOf: source)
        let nals = annexBNALs(bytes)
        guard !nals.isEmpty else {
            throw NSError(domain: "NativeVideoSourceTests", code: 10,
                          userInfo: [NSLocalizedDescriptionKey: "Expected generated Annex-B RPU payloads"])
        }
        return nals
    }

    private func runFFmpeg(_ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["ffmpeg"] + arguments
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            XCTFail("ffmpeg must inspect a stream-copy fragment (exit \(process.terminationStatus))")
            throw NSError(domain: "NativeVideoSourceTests", code: 6,
                          userInfo: [NSLocalizedDescriptionKey: "ffmpeg inspection failed"])
        }
    }

    private func contains(_ data: Data, _ needle: Data) -> Bool {
        data.range(of: needle) != nil
    }

    private func doviConfiguration(in source: URL, videoTrack: Int = 0) throws -> [String: String] {
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["ffprobe", "-v", "error", "-select_streams", "v:\(videoTrack)", "-show_entries", "stream_side_data", "-of", "json", source.path]
        process.standardOutput = output
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0,
              let root = try JSONSerialization.jsonObject(with: output.fileHandleForReading.readDataToEndOfFile()) as? [String: Any],
              let stream = (root["streams"] as? [[String: Any]])?.first,
              let dovi = (stream["side_data_list"] as? [[String: Any]])?.first(where: { $0["side_data_type"] as? String == "DOVI configuration record" }) else {
            throw NSError(domain: "NativeVideoSourceTests", code: 7,
                          userInfo: [NSLocalizedDescriptionKey: "Missing Dolby Vision configuration record"])
        }
        let keys = ["dv_profile", "dv_level", "rpu_present_flag", "el_present_flag", "bl_present_flag", "dv_bl_signal_compatibility_id"]
        var result: [String: String] = [:]
        for key in keys {
            guard let value = dovi[key] else {
                throw NSError(domain: "NativeVideoSourceTests", code: 8,
                              userInfo: [NSLocalizedDescriptionKey: "Dolby Vision configuration lacks \(key)"])
            }
            result[key] = String(describing: value)
        }
        return result
    }

    private func videoPacketCount(in source: URL) throws -> Int {
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                             "-show_entries", "stream=nb_read_packets", "-of", "default=nw=1:nk=1", source.path]
        process.standardOutput = output
        try process.run()
        process.waitUntilExit()
        let value = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard process.terminationStatus == 0, let count = Int(value) else {
            throw NSError(domain: "NativeVideoSourceTests", code: 9,
                          userInfo: [NSLocalizedDescriptionKey: "Unable to count video packets"])
        }
        return count
    }

    private func audioPacketDTS(in source: URL) throws -> [Int64] {
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=dts", "-of", "json", source.path]
        process.standardOutput = output
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let root = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard process.terminationStatus == 0, let packets = root?["packets"] as? [[String: Any]] else {
            throw NSError(domain: "NativeVideoSourceTests", code: 11,
                          userInfo: [NSLocalizedDescriptionKey: "Unable to inspect packaged audio timestamps"])
        }
        return try packets.map { packet in
            guard let dts = packet["dts"] as? NSNumber else {
                throw NSError(domain: "NativeVideoSourceTests", code: 12,
                              userInfo: [NSLocalizedDescriptionKey: "Packaged AAC packet lacks a decode timestamp"])
            }
            return dts.int64Value
        }
    }

    private func annexBNALs(_ data: Data) -> [Data] {
        let bytes = Array(data)
        var starts: [(offset: Int, length: Int)] = []
        var index = 0
        while index + 3 < bytes.count {
            if bytes[index] == 0, bytes[index + 1] == 0, bytes[index + 2] == 1 {
                starts.append((index, 3)); index += 3
            } else if index + 4 < bytes.count, bytes[index] == 0, bytes[index + 1] == 0, bytes[index + 2] == 0, bytes[index + 3] == 1 {
                starts.append((index, 4)); index += 4
            } else {
                index += 1
            }
        }
        var result: [Data] = []
        for (ordinal, start) in starts.enumerated() {
            let lower = start.offset + start.length
            let upper = ordinal + 1 < starts.count ? starts[ordinal + 1].offset : bytes.count
            guard lower + 1 < upper else { continue }
            result.append(Data(bytes[lower..<upper]))
        }
        return result
    }

    private func hevcNALType(_ nal: Data) -> UInt8 {
        guard let first = nal.first else { return 255 }
        return (first >> 1) & 0x3f
    }

    private func hevcNALLayer(_ nal: Data) -> UInt8 {
        guard nal.count >= 2 else { return 255 }
        return ((nal[nal.startIndex] & 1) << 5) | (nal[nal.index(after: nal.startIndex)] >> 3)
    }
}
#endif
