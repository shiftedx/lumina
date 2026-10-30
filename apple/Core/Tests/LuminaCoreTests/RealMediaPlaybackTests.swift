#if os(macOS)
import AudioToolbox
import AVFoundation
import CoreVideo
import Foundation
import XCTest
@testable import LuminaCore

private final class QualificationRedirectGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
        task.cancel()
    }
}

/// Opt-in qualification against a locally proxied, operator-provided media file.
/// The manifest deliberately contains an alias rather than a library title, path, or credential.
@MainActor
final class RealMediaPlaybackTests: XCTestCase {
    func testConfiguredAssetRendersAndSeeks() async throws {
        let configuration = try configuredAsset()
        try await assertSourceBinding(configuration)

        do {
            let defaultDelivery = try await exercise(
                configuration,
                preferredAudioStreamIndex: nil,
                initialPosition: configuration.startSeconds,
                initialLabel: "unselected-audio",
                requirePlaybackMetadata: true,
                followUp: []
            )
            _ = try await exercise(
                configuration,
                preferredAudioStreamIndex: configuration.audioStreamIndex,
                initialPosition: configuration.startSeconds,
                initialLabel: "startup",
                requirePlaybackMetadata: false,
                followUp: []
            )
            try await assertSelectedSubtitleSelection(
                configuration,
                mediaDirect: defaultDelivery == .mediaDirect
            )
            // This is deliberately a new item: app resume calls
            // awaitInitialPlayback after it has prepared a fresh player, rather
            // than after playing the beginning of a cached item.
            _ = try await exercise(
                configuration,
                preferredAudioStreamIndex: configuration.audioStreamIndex,
                initialPosition: configuration.deepSeekSeconds,
                initialLabel: "initial-resume",
                requirePlaybackMetadata: false,
                followUp: [("near-end", configuration.nearEndSeconds)]
            )
        } catch let error as QualificationFailure {
            throw error
        } catch {
            throw QualificationFailure(chain: sanitizedErrorChain(error))
        }
    }

    func testConfiguredAssetFreshPlayerColdNearEndRenders() async throws {
        let configuration = try configuredAsset()
        try await assertSourceBinding(configuration)
        _ = try await exercise(
            configuration,
            preferredAudioStreamIndex: configuration.audioStreamIndex,
            initialPosition: configuration.nearEndSeconds,
            initialLabel: "fresh-player-cold-near-end",
            requirePlaybackMetadata: true,
            followUp: []
        )
    }

    func testSourceBindingPreflightRejectsChangedOriginBeforePlayback() async throws {
        let bytes = Data([0x01, 0x02])
        let validator = "\"fixture-v1\""
        let accepted = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0, etag: validator)
        let acceptedURL = try await accepted.start()
        defer { accepted.stop() }
        try await assertSourceBinding(url: acceptedURL, sourceSize: bytes.count, sourceValidator: validator)
        XCTAssertEqual(accepted.requestedRangeStarts, [0])

        let stale = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0, etag: "\"fixture-v2\"")
        let staleURL = try await stale.start()
        defer { stale.stop() }
        await assertPreflightRejects(url: staleURL, sourceSize: bytes.count, sourceValidator: validator)
        XCTAssertEqual(stale.requestedRangeStarts, [0])

        let wrongSize = try ProgressiveAudioHTTPFixture(
            bytes: bytes, delay: 0, advertisedLength: bytes.count + 1, etag: validator
        )
        let wrongSizeURL = try await wrongSize.start()
        defer { wrongSize.stop() }
        await assertPreflightRejects(url: wrongSizeURL, sourceSize: bytes.count, sourceValidator: validator)
        XCTAssertEqual(wrongSize.requestedRangeStarts, [0])

        let ignoresRange = try ProgressiveAudioHTTPFixture(bytes: bytes, delay: 0, ranges: false, etag: validator)
        let ignoresRangeURL = try await ignoresRange.start()
        defer { ignoresRange.stop() }
        await assertPreflightRejects(url: ignoresRangeURL, sourceSize: bytes.count, sourceValidator: validator)
        XCTAssertEqual(ignoresRange.requestedRangeStarts, [0])
    }

    private func configuredAsset() throws -> Configuration {
        guard let path = ProcessInfo.processInfo.environment["LUMINA_REAL_MEDIA_MANIFEST"], !path.isEmpty else {
            throw XCTSkip("Set LUMINA_REAL_MEDIA_MANIFEST to an opt-in local qualification manifest")
        }
        let configuration = try JSONDecoder().decode(
            Configuration.self,
            from: Data(contentsOf: URL(fileURLWithPath: path))
        )
        try configuration.validate()
        return configuration
    }

    private func assertPreflightRejects(url: URL, sourceSize: Int, sourceValidator: String) async {
        do {
            try await assertSourceBinding(url: url, sourceSize: sourceSize, sourceValidator: sourceValidator)
            XCTFail("A changed source binding must fail before playback")
        } catch {}
    }

    private func assertSourceBinding(_ configuration: Configuration) async throws {
        try await assertSourceBinding(
            url: configuration.proxyURL,
            sourceSize: configuration.sourceSize,
            sourceValidator: configuration.sourceValidator
        )
    }

    private func assertSourceBinding(url: URL, sourceSize: Int, sourceValidator: String) async throws {
        do {
            try await assertSourceBindingUnchecked(url: url, sourceSize: sourceSize, sourceValidator: sourceValidator)
        } catch let error as QualificationFailure {
            throw error
        } catch {
            throw QualificationFailure(chain: "source provenance preflight: \(sanitizedErrorChain(error))")
        }
    }

    private func assertSourceBindingUnchecked(url: URL, sourceSize: Int, sourceValidator: String) async throws {
        var request = URLRequest(url: url)
        request.setValue("bytes=0-0", forHTTPHeaderField: "Range")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 30
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30
        let session = URLSession(
            configuration: configuration,
            delegate: QualificationRedirectGuard(),
            delegateQueue: nil
        )
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 206,
              http.value(forHTTPHeaderField: "Content-Range") == "bytes 0-0/\(sourceSize)",
              http.value(forHTTPHeaderField: "ETag") == sourceValidator
        else { throw QualificationFailure(chain: "source provenance preflight rejected") }
        var iterator = bytes.makeAsyncIterator()
        guard try await iterator.next() != nil else {
            throw QualificationFailure(chain: "source provenance preflight body missing")
        }
        guard try await iterator.next() == nil else {
            throw QualificationFailure(chain: "source provenance preflight exceeded one byte")
        }
    }

    private func exercise(
        _ configuration: Configuration,
        preferredAudioStreamIndex: Int?,
        initialPosition: Double,
        initialLabel: String,
        requirePlaybackMetadata: Bool,
        followUp: [(String, Double)]
    ) async throws -> DeliveryShape? {
        var phase = "prepare"
        var delivery: DeliveryShape?
        let owner = NativeVideoSource()
        var player: AVPlayer?
        do {
            let startupStarted = ContinuousClock.now
            let item = try await owner.prepare(
                url: configuration.proxyURL,
                usesClientDemux: configuration.usesClientDemux,
                requireDolbyVisionTransportPairing: configuration.strictPair,
                preferredAudioStreamIndex: preferredAudioStreamIndex
            )
            let frames = AVPlayerItemVideoOutput(pixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
            ])
            item.add(frames)
            let preparedPlayer = AVPlayer(playerItem: item)
            preparedPlayer.volume = 0
            player = preparedPlayer

            let initialDeadline = startupStarted + .seconds(30)
            phase = "ready"
            try await NativeVideoSource.waitUntilReady(preparedPlayer, timeout: remaining(until: initialDeadline))
            print("Real-media \(initialLabel) prepareReadyElapsed=\(startupStarted.duration(to: .now))")
            if initialPosition > 0 {
                phase = "timeline"
                let timelineStarted = ContinuousClock.now
                try await NativeVideoSource.waitUntilReady(
                    preparedPlayer, seekingTo: initialPosition, timeout: remaining(until: initialDeadline)
                )
                print("Real-media \(initialLabel) timelineWaitElapsed=\(timelineStarted.duration(to: .now))")
            }
            phase = "render"
            try await render(
                initialPosition, item: item, player: preparedPlayer, frames: frames,
                label: initialLabel, deadline: initialDeadline
            )
            if requirePlaybackMetadata {
                phase = "playback-metadata"
                delivery = try await assertPlaybackMetadata(
                    item: item,
                    expected: configuration.expected,
                    usesClientDemux: configuration.usesClientDemux
                )
            }
            XCTAssertLessThanOrEqual(startupStarted.duration(to: .now), .seconds(30),
                                      "Real-media \(initialLabel) exceeded its 30-second qualification budget")
            for (label, target) in followUp {
                let started = ContinuousClock.now
                phase = "\(label)-render"
                let deadline = started + .seconds(30)
                try await render(target, item: item, player: preparedPlayer, frames: frames, label: label, deadline: deadline)
                XCTAssertLessThanOrEqual(started.duration(to: .now), .seconds(30),
                                          "Real-media \(label) seek exceeded its independent 30-second qualification budget")
            }
            try await owner.validateDolbyVisionConversion()
            preparedPlayer.replaceCurrentItem(with: nil)
            await owner.stop().value
            return delivery
        } catch {
            player?.replaceCurrentItem(with: nil)
            await owner.stop().value
            if let qualification = error as? QualificationFailure {
                throw qualification
            }
            throw QualificationFailure(chain: "\(initialLabel)/\(phase): \(sanitizedErrorChain(error))")
        }
    }

    private func remaining(until deadline: ContinuousClock.Instant) throws -> Duration {
        let remaining = ContinuousClock.now.duration(to: deadline)
        guard remaining > .zero else {
            throw QualificationFailure(chain: "initial playback budget exhausted")
        }
        return remaining
    }

    private func loadOwnedLoopbackPlaylist(_ url: URL) async throws -> String {
        guard isOwnedLoopbackPlaylist(url) else {
            throw QualificationFailure(chain: "client-demux playlist is not an owned loopback m3u8")
        }
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 5
        request.setValue("application/vnd.apple.mpegurl, application/x-mpegURL", forHTTPHeaderField: "Accept")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 5
        let session = URLSession(configuration: configuration, delegate: QualificationRedirectGuard(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 200,
              let responseURL = http.url,
              isOwnedLoopbackPlaylist(responseURL),
              sameLoopbackOrigin(url, responseURL) else {
            throw QualificationFailure(chain: "client-demux playlist request rejected")
        }
        var data = Data()
        for try await byte in bytes {
            guard data.count < 1024 * 1024 else {
                throw QualificationFailure(chain: "client-demux playlist exceeded qualification bound")
            }
            data.append(byte)
        }
        guard let playlist = String(data: data, encoding: .utf8) else {
            throw QualificationFailure(chain: "client-demux playlist is not text")
        }
        return playlist
    }

    private func isOwnedLoopbackPlaylist(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "http", url.pathExtension.lowercased() == "m3u8",
              url.user == nil, url.password == nil, url.fragment == nil,
              let host = url.host?.lowercased() else { return false }
        return ["127.0.0.1", "::1", "localhost"].contains(host)
    }

    private func sameLoopbackOrigin(_ left: URL, _ right: URL) -> Bool {
        left.scheme?.lowercased() == right.scheme?.lowercased()
            && left.host?.lowercased() == right.host?.lowercased()
            && left.port == right.port
    }

    private func assertSelectedSubtitleSelection(
        _ configuration: Configuration,
        mediaDirect: Bool
    ) async throws {
        guard let selectedSubtitleStreamIndex = configuration.selectedSubtitleStreamIndex else { return }
        let modes = mediaDirect ? [true, false] : [true]
        for enableSubtitleRenditions in modes {
            let owner = NativeVideoSource()
            defer { owner.stop() }
            do {
                let item = try await owner.prepare(
                    url: configuration.proxyURL,
                    usesClientDemux: configuration.usesClientDemux,
                    requireDolbyVisionTransportPairing: configuration.strictPair,
                    preferredAudioStreamIndex: configuration.audioStreamIndex,
                    preferredSubtitleStreamIndex: selectedSubtitleStreamIndex,
                    enableSubtitleRenditions: enableSubtitleRenditions
                )
                guard !mediaDirect else {
                    XCTFail("A media-direct source with an explicitly selected subtitle must request compatible fallback")
                    continue
                }
                guard let url = (item.asset as? AVURLAsset)?.url else {
                    XCTFail("A selected subtitle needs a genuine HLS master")
                    continue
                }
                let master = try await loadOwnedLoopbackPlaylist(url)
                XCTAssertTrue(master.contains("#EXT-X-STREAM-INF:"))
                let legible = try await item.asset.loadMediaSelectionGroup(for: .legible)
                XCTAssertNotNil(legible,
                                "A selected subtitle accepted by a genuine master must remain selectable")
            } catch let error as NativeVideoSource.SelectionError {
                if mediaDirect {
                    XCTAssertEqual(error, .subtitleMasterUnavailable)
                } else {
                    XCTFail("A genuine master must not silently drop a selected subtitle: \(error)")
                }
            }
        }
    }

    private func render(
        _ target: Double,
        item: AVPlayerItem,
        player: AVPlayer,
        frames: AVPlayerItemVideoOutput,
        label: String,
        deadline: ContinuousClock.Instant
    ) async throws {
        let seekStarted = ContinuousClock.now
        let sought = await boundedSeek(player: player, item: item, target: target, timeout: try remaining(until: deadline))
        let seekElapsed = seekStarted.duration(to: .now)
        guard sought else {
            let seekableEnd = item.seekableTimeRanges
                .map { CMTimeRangeGetEnd($0.timeRangeValue).seconds }
                .filter(\.isFinite)
                .max()
                .map { String($0) } ?? "none"
            let duration = item.duration.seconds.isFinite ? String(item.duration.seconds) : "unknown"
            let itemError = item.error.map(sanitizedErrorChain) ?? "none"
            print("Real-media \(label) seek completed=false elapsed=\(seekElapsed) itemStatus=\(item.status.rawValue) playerStatus=\(player.status.rawValue) duration=\(duration) seekableEnd=\(seekableEnd) itemError=\(itemError)")
            throw QualificationFailure(chain: "\(label) seek completion false")
        }
        player.play()
        defer { player.pause() }

        let frameStarted = ContinuousClock.now
        var displayedTime: Double?
        while ContinuousClock.now < deadline {
            let current = item.currentTime().seconds
            var itemTimeForDisplay = CMTime.invalid
            if frames.copyPixelBuffer(forItemTime: item.currentTime(), itemTimeForDisplay: &itemTimeForDisplay) != nil {
                displayedTime = itemTimeForDisplay.seconds
            }
            if abs(current - target) < 1,
               let displayedTime, displayedTime.isFinite,
               abs(displayedTime - target) < 1 {
                print("Real-media \(label) seekElapsed=\(seekElapsed) frameElapsed=\(frameStarted.duration(to: .now))")
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        let rendered = displayedTime.map { String($0) } ?? "no-frame"
        XCTFail("Real-media \(label) must display the requested presentation time; rendered=\(rendered)")
    }

    private func boundedSeek(player: AVPlayer, item: AVPlayerItem, target: Double, timeout: Duration) async -> Bool {
        let gate = SeekGate()
        return await withCheckedContinuation { continuation in
            gate.install(continuation)
            let timeoutTask = Task { @MainActor in
                do { try await Task.sleep(for: timeout) }
                catch { return }
                item.cancelPendingSeeks()
                gate.resolve(false)
            }
            player.seek(
                to: CMTime(seconds: target, preferredTimescale: 600),
                toleranceBefore: .zero,
                toleranceAfter: .zero
            ) { finished in
                timeoutTask.cancel()
                gate.resolve(finished)
            }
        }
    }

    private func assertPlaybackMetadata(
        item: AVPlayerItem,
        expected: Expected,
        usesClientDemux: Bool
    ) async throws -> DeliveryShape {
        guard usesClientDemux else {
            try await assertMediaFormatDescriptions(item, expected: expected)
            return .mediaDirect
        }
        guard let masterURL = (item.asset as? AVURLAsset)?.url else {
            XCTFail("Client-demux qualification must publish an AVURLAsset")
            return .mediaDirect
        }
        let master = try await loadOwnedLoopbackPlaylist(masterURL).lowercased()
        guard master.contains("#ext-x-stream-inf:") else {
            try await assertMediaFormatDescriptions(item, expected: expected)
            return .mediaDirect
        }
        XCTAssertTrue(master.contains("resolution=\(expected.width)x\(expected.height)"), "Master must retain source resolution")
        XCTAssertTrue(masterContainsCodec(master, expected: expected), "Master must retain expected codec signaling")
        XCTAssertTrue(masterContainsAudio(master, expected: expected.audio), "Master must retain expected audio signaling")
        switch expected.hdr.lowercased() {
        case "dolbyvision":
            XCTAssertTrue(master.contains("supplemental-codecs") || master.contains("dvh1") || master.contains("dvhe"),
                          "Master must carry Dolby Vision signaling")
            XCTAssertTrue(master.contains("video-range=pq"), "Dolby Vision Profile 8 PQ base must retain HDR signaling")
        case "hdr10", "pq":
            XCTAssertTrue(master.contains("video-range=pq"), "Master must retain PQ signaling")
        case "hlg":
            XCTAssertTrue(master.contains("video-range=hlg"), "Master must retain HLG signaling")
        case "sdr":
            XCTAssertFalse(master.contains("video-range="), "SDR master must not claim HDR")
        default:
            throw QualificationFailure(chain: "unsupported expected HDR label")
        }
        return .master
    }

    private func assertMediaFormatDescriptions(_ item: AVPlayerItem, expected: Expected) async throws {
        guard let videoTrack = item.tracks.compactMap(\.assetTrack).first(where: { $0.mediaType == .video }) else {
            XCTFail("Media-direct item must expose a video track")
            return
        }
        let videoDescriptions = try await videoTrack.load(.formatDescriptions)
        guard let video = videoDescriptions.first else {
            XCTFail("Media-direct video track must expose a format description")
            return
        }
        let deliveredCodec = CMFormatDescriptionGetMediaSubType(video)
        if expected.acceptsHEVCFamily {
            XCTAssertTrue([fourCC("hvc1"), fourCC("hev1")].contains(deliveredCodec),
                          "Media-direct item must retain an HEVC sample entry")
        } else {
            XCTAssertEqual(deliveredCodec, fourCC(expected.codec),
                           "Media-direct item must retain the expected video codec")
        }
        let dimensions = CMVideoFormatDescriptionGetDimensions(video)
        XCTAssertEqual(Int(dimensions.width), expected.width)
        XCTAssertEqual(Int(dimensions.height), expected.height)
        let transfer = CMFormatDescriptionGetExtension(
            video, extensionKey: kCMFormatDescriptionExtension_TransferFunction
        ) as? String
        switch expected.hdr.lowercased() {
        case "dolbyvision", "hdr10", "pq":
            XCTAssertEqual(transfer, kCMFormatDescriptionTransferFunction_SMPTE_ST_2084_PQ as String,
                           "Media-direct item must retain PQ transfer characteristics")
        case "hlg":
            XCTAssertEqual(transfer, kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG as String,
                           "Media-direct item must retain HLG transfer characteristics")
        case "sdr":
            XCTAssertNotEqual(transfer, kCMFormatDescriptionTransferFunction_SMPTE_ST_2084_PQ as String)
            XCTAssertNotEqual(transfer, kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG as String)
        default:
            throw QualificationFailure(chain: "unsupported expected HDR label")
        }
        let audioTracks = item.tracks.compactMap(\.assetTrack).filter { $0.mediaType == .audio }
        var audioDescriptions: [CMFormatDescription] = []
        for track in audioTracks {
            audioDescriptions.append(contentsOf: try await track.load(.formatDescriptions))
        }
        XCTAssertTrue(audioDescriptions.contains {
            matchesMediaDirectAudioFormat(CMFormatDescriptionGetMediaSubType($0), expected: expected.audio)
        },
                      "Media-direct item must retain the expected audio codec")
    }

    private func matchesMediaDirectAudioFormat(_ delivered: UInt32, expected: String) -> Bool {
        switch expected {
        case "mp4a":
            delivered == kAudioFormatMPEG4AAC
        case "fLaC":
            delivered == kAudioFormatFLAC
        default:
            delivered == fourCC(expected)
        }
    }

    private func fourCC(_ value: String) -> UInt32 {
        value.utf8.reduce(0) { ($0 << 8) | UInt32($1) }
    }

    private enum DeliveryShape { case master, mediaDirect }

    private final class SeekGate: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<Bool, Never>?
        private var resolved = false

        func install(_ continuation: CheckedContinuation<Bool, Never>) {
            lock.withLock { self.continuation = continuation }
        }

        func resolve(_ value: Bool) {
            let continuation = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
                guard !resolved else { return nil }
                resolved = true
                return self.continuation
            }
            continuation?.resume(returning: value)
        }
    }

    private func masterContainsCodec(_ master: String, expected: Expected) -> Bool {
        if expected.acceptsHEVCFamily {
            master.contains("hvc1") || master.contains("hev1")
        } else {
            master.contains(expected.codec)
        }
    }

    private func masterContainsAudio(_ master: String, expected: String) -> Bool {
        switch expected.lowercased() {
        case "eac3", "eac3atmos", "atmos":
            master.contains("ec-3")
        case "ac3":
            master.contains("ac-3")
        default:
            master.contains(expected.lowercased())
        }
    }

    private func sanitizedErrorChain(_ error: Error) -> String {
        var result: [String] = []
        var current: NSError? = error as NSError
        while let value = current, result.count < 8 {
            result.append("\(value.domain)(\(value.code))")
            current = value.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return result.joined(separator: " -> ")
    }

    private struct Configuration: Decodable {
        let alias: String
        let proxyURL: URL
        let usesClientDemux: Bool
        let strictPair: Bool
        let startSeconds: Double
        let deepSeekSeconds: Double
        let nearEndSeconds: Double
        let sourceSize: Int
        let sourceValidator: String
        let expected: Expected
        let selectedSubtitleStreamIndex: Int?
        let audioStreamIndex: Int?

        func validate() throws {
            guard !alias.isEmpty,
                  let scheme = proxyURL.scheme?.lowercased(), scheme == "http" || scheme == "https",
                  let host = proxyURL.host?.lowercased(), ["127.0.0.1", "::1", "localhost"].contains(host),
                  proxyURL.user == nil, proxyURL.password == nil,
                  [startSeconds, deepSeekSeconds, nearEndSeconds].allSatisfy({ $0.isFinite && $0 >= 0 }),
                  startSeconds <= deepSeekSeconds, deepSeekSeconds < nearEndSeconds,
                  sourceSize > 0,
                  sourceValidator.count > 2,
                  sourceValidator.hasPrefix("\"") && sourceValidator.hasSuffix("\""),
                  selectedSubtitleStreamIndex.map({ $0 >= 0 }) ?? true,
                  audioStreamIndex.map({ $0 >= 0 }) ?? true,
                  expected.width > 0, expected.height > 0,
                  !expected.codec.isEmpty, !expected.hdr.isEmpty, !expected.audio.isEmpty
            else { throw QualificationFailure(chain: "invalid local real-media qualification manifest") }
        }
    }

    private struct Expected: Decodable {
        let width: Int
        let height: Int
        let codec: String
        let hdr: String
        let audio: String
        let acceptsHEVCFamily: Bool

        enum CodingKeys: String, CodingKey {
            case width, height, codec, videoCodec, hdr, audio, audioCodecs
        }

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            width = try values.decode(Int.self, forKey: .width)
            height = try values.decode(Int.self, forKey: .height)
            let rawCodec = try values.decodeIfPresent(String.self, forKey: .codec)
                ?? values.decode(String.self, forKey: .videoCodec)
            acceptsHEVCFamily = rawCodec.lowercased() == "hevc"
            codec = Self.videoFourCC(rawCodec)
            hdr = try values.decode(String.self, forKey: .hdr)
            let rawAudio = try values.decodeIfPresent(String.self, forKey: .audio)
                ?? values.decode([String].self, forKey: .audioCodecs).first
                ?? ""
            audio = Self.audioFourCC(rawAudio)
        }

        private static func videoFourCC(_ value: String) -> String {
            switch value.lowercased() {
            case "h264", "avc": "avc1"
            case "hevc": "hvc1"
            case "av1": "av01"
            default: value.lowercased()
            }
        }

        private static func audioFourCC(_ value: String) -> String {
            switch value.lowercased() {
            case "aac": "mp4a"
            case "eac3", "eac3atmos", "atmos": "ec-3"
            case "ac3": "ac-3"
            case "flac": "fLaC"
            default: value.lowercased()
            }
        }
    }

    private struct QualificationFailure: LocalizedError {
        let chain: String
        var errorDescription: String? { "Real-media qualification failed: \(chain)" }
    }
}
#endif
