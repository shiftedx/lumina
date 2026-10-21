#if os(macOS)
import AVFoundation
import CoreVideo
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativeVideoSourceTests: XCTestCase {
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
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-i", captions.path, "-c:s", "srt", "-c:v", "libx264", "-preset", "ultrafast",
            "-g", "48", "-c:a", "aac", source.path]
        try generate.run()
        generate.waitUntilExit()
        XCTAssertEqual(generate.terminationStatus, 0)
        let owner = NativeVideoSource()
        let item = try await owner.prepare(url: source, usesClientDemux: true)
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
        XCTAssertEqual(owner.retainedSubtitleHistoryBytes, 0, "AVKit does not consume the host cue replay cache")
        let subtitlePlaylist = playlist.deletingLastPathComponent().appendingPathComponent("subs0/index.m3u8")
        let (subtitleData, subtitleResponse) = try await URLSession.shared.data(from: subtitlePlaylist)
        XCTAssertEqual((subtitleResponse as? HTTPURLResponse)?.statusCode, 200)
        let subtitleText = String(decoding: subtitleData, as: UTF8.self)
        let segmentName = try XCTUnwrap(subtitleText.split(separator: "\n").first { !$0.hasPrefix("#") })
        let segment = try XCTUnwrap(URL(string: String(segmentName), relativeTo: subtitlePlaylist))
        let (cueData, cueResponse) = try await URLSession.shared.data(from: segment)
        XCTAssertEqual((cueResponse as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertTrue(String(decoding: cueData, as: UTF8.self).contains("Synthetic opening caption"),
                      "Bounding host replay must preserve the packaged subtitle cues")
        let sought = await player.seek(to: CMTime(seconds: 6, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        XCTAssertTrue(sought)
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertGreaterThan(item.currentTime().seconds, 6.1)
        player.pause()
        player.replaceCurrentItem(with: nil)
        await owner.stop().value
        var check = URLRequest(url: playlist)
        check.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: check)
            XCTAssertNotEqual((response as? HTTPURLResponse)?.statusCode, 200)
        } catch { /* The listener is closed. */ }
        let cancelled = Task { try await owner.prepare(url: source, usesClientDemux: true) }
        cancelled.cancel()
        do { _ = try await cancelled.value; XCTFail("Cancelled startup must not publish a player item") }
        catch { }
        await owner.stop().value
        // Replay owns a new session; closing a predecessor must not stop it.
        let replay = try await owner.prepare(url: source, usesClientDemux: true)
        XCTAssertNotEqual((replay.asset as? AVURLAsset)?.url, playlist)
        await owner.stop().value
    }
}
#endif
