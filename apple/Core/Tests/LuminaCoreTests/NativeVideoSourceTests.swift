#if os(macOS)
import AVFoundation
import CoreVideo
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativeVideoSourceTests: XCTestCase {
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
}
#endif
