#if os(macOS)
import AVFoundation
import CoreVideo
import Foundation
import XCTest
@testable import LuminaCore

/// Opt-in qualification against a locally proxied, operator-provided media file.
/// The manifest deliberately contains an alias rather than a library title, path, or credential.
@MainActor
final class RealMediaPlaybackTests: XCTestCase {
    func testConfiguredAssetRendersAndSeeks() async throws {
        guard let path = ProcessInfo.processInfo.environment["LUMINA_REAL_MEDIA_MANIFEST"], !path.isEmpty else {
            throw XCTSkip("Set LUMINA_REAL_MEDIA_MANIFEST to an opt-in local qualification manifest")
        }
        let configuration = try JSONDecoder().decode(
            Configuration.self,
            from: Data(contentsOf: URL(fileURLWithPath: path))
        )
        try configuration.validate()

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
        do {
            let owner = NativeVideoSource()
            defer { owner.stop() }
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
            let player = AVPlayer(playerItem: item)
            player.volume = 0
            defer { player.replaceCurrentItem(with: nil) }

            phase = "ready"
            try await NativeVideoSource.waitUntilReady(player, timeout: .seconds(30))
            phase = "render"
            try await render(initialPosition, item: item, player: player, frames: frames, label: initialLabel)
            if requirePlaybackMetadata {
                phase = "playback-metadata"
                delivery = try await assertPlaybackMetadata(item: item, expected: configuration.expected)
            }
            XCTAssertLessThanOrEqual(startupStarted.duration(to: .now), .seconds(30),
                                      "Real-media \(initialLabel) exceeded its 30-second qualification budget")
            for (label, target) in followUp {
                let started = ContinuousClock.now
                phase = "\(label)-render"
                try await render(target, item: item, player: player, frames: frames, label: label)
                XCTAssertLessThanOrEqual(started.duration(to: .now), .seconds(30),
                                          "Real-media \(label) seek exceeded its independent 30-second qualification budget")
            }
            try await owner.validateDolbyVisionConversion()
            return delivery
        } catch {
            throw QualificationFailure(chain: "\(initialLabel)/\(phase): \(sanitizedErrorChain(error))")
        }
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
                let (data, response) = try await URLSession.shared.data(from: url)
                XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
                XCTAssertTrue(String(decoding: data, as: UTF8.self).contains("#EXT-X-STREAM-INF:"))
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
        label: String
    ) async throws {
        let seekStarted = ContinuousClock.now
        let sought = await boundedSeek(player: player, item: item, target: target)
        let seekElapsed = seekStarted.duration(to: .now)
        guard sought else {
            print("Real-media \(label) seek completed=false elapsed=\(seekElapsed)")
            throw QualificationFailure(chain: "\(label) seek timed out")
        }
        player.play()
        defer { player.pause() }

        let frameStarted = ContinuousClock.now
        let deadline = ContinuousClock.now + .seconds(30)
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

    private func boundedSeek(player: AVPlayer, item: AVPlayerItem, target: Double) async -> Bool {
        let gate = SeekGate()
        return await withCheckedContinuation { continuation in
            gate.install(continuation)
            let timeout = Task { @MainActor in
                do { try await Task.sleep(for: .seconds(30)) }
                catch { return }
                item.cancelPendingSeeks()
                gate.resolve(false)
            }
            player.seek(
                to: CMTime(seconds: target, preferredTimescale: 600),
                toleranceBefore: .zero,
                toleranceAfter: .zero
            ) { finished in
                timeout.cancel()
                gate.resolve(finished)
            }
        }
    }

    private func assertPlaybackMetadata(item: AVPlayerItem, expected: Expected) async throws -> DeliveryShape {
        guard let masterURL = (item.asset as? AVURLAsset)?.url else {
            XCTFail("Client-demux qualification must publish an AVURLAsset")
            return .mediaDirect
        }
        let (data, response) = try await URLSession.shared.data(from: masterURL)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let master = String(decoding: data, as: UTF8.self).lowercased()
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
        XCTAssertTrue(audioDescriptions.contains { CMFormatDescriptionGetMediaSubType($0) == fourCC(expected.audio) },
                      "Media-direct item must retain the expected audio codec")
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
        let expected: Expected
        let selectedSubtitleStreamIndex: Int?
        let audioStreamIndex: Int?

        func validate() throws {
            guard !alias.isEmpty,
                  let scheme = proxyURL.scheme?.lowercased(), scheme == "http" || scheme == "https",
                  let host = proxyURL.host?.lowercased(), ["127.0.0.1", "::1", "localhost"].contains(host),
                  proxyURL.user == nil, proxyURL.password == nil,
                  usesClientDemux,
                  [startSeconds, deepSeekSeconds, nearEndSeconds].allSatisfy({ $0.isFinite && $0 >= 0 }),
                  startSeconds <= deepSeekSeconds, deepSeekSeconds < nearEndSeconds,
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
