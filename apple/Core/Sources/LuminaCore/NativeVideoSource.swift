import AVFoundation
import Foundation
@preconcurrency import PrismCore

/// Owns on-demand, stream-copy MKV packaging; AVPlayer still owns decoding and transport.
@MainActor
public final class NativeVideoSource {
    public enum SelectionError: Error, Equatable, Sendable {
        /// Native client delivery can only carry a stream-copyable selected audio track.
        /// Callers retain the existing item and use their server fallback instead.
        case unsupportedAudioStream(Int)
        case externalSubtitleMustBeLocal
        case externalSubtitleMustBeWebVTT
        case externalSubtitleDurationRequired
        case externalSubtitleRequiresHLS
        case externalSubtitleTooLarge
        case subtitleDelayUnavailable
        case externalSubtitleUnsupportedTiming
        /// A Profile 7 conversion emitted an RPU or packet that cannot match
        /// the advertised single-layer Profile 8.1 route.
        case invalidDolbyVisionConversion
    }

    /// Host-owned conversion evidence, without exposing PrismCore types.
    public struct DolbyVisionConversionSnapshot: Sendable, Equatable {
        public let convertedRPUs: Int
        public let failedRPUs: Int
        public let droppedEnhancementLayerNALs: Int
        public let staleUnconvertedPackets: Int
        /// Prism's verdict for the segments produced so far: at least one RPU
        /// was rewritten, no RPU or packet was refused, and an enhancement-layer
        /// NAL was actually removed. A terminal pairing failure also makes
        /// this false, even before a first segment exists. Counters describe
        /// observed conversion work, not an all-file preflight.
        public let isClean: Bool

        /// The P8.1 route is unsafe until the complete conversion evidence is
        /// clean. This deliberately includes zero successful conversions and
        /// zero removed EL NALs, which otherwise leave the master lying about
        /// a single-layer conversion.
        public var hasFailure: Bool { !isClean }
    }

    public struct ExternalSubtitle: Sendable, Equatable {
        public let fileURL: URL
        public let language: String?
        public let name: String?
        public let isForced: Bool
        /// Required when wrapping an existing HLS stream without PrismCore.
        public let durationSeconds: Double?

        public init(
            fileURL: URL,
            language: String? = nil,
            name: String? = nil,
            isForced: Bool = false,
            durationSeconds: Double? = nil
        ) {
            self.fileURL = fileURL
            self.language = language
            self.name = name
            self.isForced = isForced
            self.durationSeconds = durationSeconds
        }
    }

    private struct SubtitleMaster {
        let server: LoopbackHTTPServer
        let directory: URL
        let playlistURL: URL
    }

    private var demux: PrismCoreSession?
    private var terminalDolbyVisionConversion: DolbyVisionConversionSnapshot?
    // Tests can require a metadata-only HDR/DV master without claiming that the
    // local Mac display can present it. Production always samples the current
    // output route when it creates a new Prism session.
    private let injectedDisplay: DisplayCapabilities?
    /// Compressed bytes read from the source, excluding local HLS delivery and container framing.
    public var sourceBytesRead: Int64? { demux?.sourceBytesRead }
    /// `nil` until PrismCore has produced conversion statistics or rejected a
    /// separate-track pair. Later packets can still invalidate this evidence.
    public var dolbyVisionConversion: DolbyVisionConversionSnapshot? {
        get async {
            guard let session = demux else { return terminalDolbyVisionConversion }
            let snapshot = await Self.conversionSnapshot(for: session)
            guard demux === session else { return nil }
            return snapshot
        }
    }

    private static func conversionSnapshot(for session: PrismCoreSession) async -> DolbyVisionConversionSnapshot? {
        let conversion = await session.dolbyVisionConversion
        let failure = await session.remuxFailure
        let pairingFailed: Bool
        if case .invalidDolbyVisionConversion = failure { pairingFailed = true }
        else { pairingFailed = false }
        guard conversion != nil || pairingFailed else { return nil }
        return .init(
            convertedRPUs: conversion?.convertedRPUs ?? 0,
            failedRPUs: conversion?.failedRPUs ?? 0,
            droppedEnhancementLayerNALs: conversion?.droppedEnhancementLayerNALs ?? 0,
            staleUnconvertedPackets: conversion?.staleUnconvertedPackets ?? 0,
            isClean: conversion?.isClean == true && !pairingFailed
        )
    }
    private var subtitleMaster: SubtitleMaster?
    private var externalSubtitleDirectory: URL?
    private var statusObservation: NSKeyValueObservation?
    public private(set) var activeAudioStreamIndex: Int?
    public private(set) var requestedSubtitleStreamIndex: Int?
    /// Positive values render subtitles later than the picture, bounded to +/-10 seconds.
    public private(set) var subtitleDelaySeconds = 0.0
    /// `true` only while this source owns a master that declares sidecar tracks.
    public private(set) var hasSelectableExternalSubtitles = false
    public private(set) var externalSubtitleDisplayName: String?

    // Server reporting cadence; the app-owned timeline can update more frequently.
    public static let progressObservationInterval = CMTime(seconds: 5, preferredTimescale: 600)
    private static let maximumExternalSubtitleBytes = 10 << 20
    private static let maximumPrismPlaylistBytes = 128 << 10
    private static let maximumWebVTTSeconds = Double(Int.max / 1_000)

    /// Mirrors the pinned PrismCore engine's build-time Libdovi guard. The capability says
    /// this binary can convert P7; per-session evidence still comes from
    /// `dolbyVisionConversion` after packets have been produced.
    nonisolated static var supportsDolbyVisionProfile7Conversion: Bool {
        #if canImport(Libdovi)
        true
        #else
        false
        #endif
    }

    public init() { injectedDisplay = nil }

    init(display: DisplayCapabilities) { injectedDisplay = display }

    // Internal accounting for the host cue replay cache, independent of HLS subtitle files.
    var retainedSubtitleHistoryBytes: Int { demux?.subtitleCueHistoryStats.retainedBytes ?? 0 }

    /// When attaching sidecars without client demux, `url` must be a resolved muxed
    /// HLS media playlist. Resolve server masters through LuminaStore first.
    /// A server-qualified M2TS Dolby Vision route must require transport pairing;
    /// failure to prove that pair must not silently publish base-only HDR playback.
    public func prepare(
        url: URL,
        usesClientDemux: Bool,
        requireDolbyVisionTransportPairing: Bool = false,
        preferredAudioStreamIndex: Int? = nil,
        preferredSubtitleStreamIndex: Int? = nil,
        externalSubtitles: [ExternalSubtitle] = [],
        bandwidth: Int? = nil,
        enableSubtitleRenditions: Bool = true,
        subtitleDelaySeconds: Double = 0
    ) async throws -> AVPlayerItem {
        stop()
        guard usesClientDemux || !requireDolbyVisionTransportPairing else {
            throw SelectionError.invalidDolbyVisionConversion
        }
        self.subtitleDelaySeconds = Self.normalizedSubtitleDelay(subtitleDelaySeconds)
        try validate(externalSubtitles)
        guard usesClientDemux || !externalSubtitles.isEmpty || self.subtitleDelaySeconds == 0 else {
            throw SelectionError.subtitleDelayUnavailable
        }
        guard usesClientDemux else {
            return try await prepareServerHLSWithSidecars(
                url: url,
                externalSubtitles: externalSubtitles,
                bandwidth: bandwidth
            )
        }
        let session = try PrismCoreSession(url: url, display: injectedDisplay ?? .current(),
                                          segmentCacheBytes: 64 << 20,
                                          preferredAudioStreamIndex: preferredAudioStreamIndex,
                                          preferredSubtitleStreamIndex: preferredSubtitleStreamIndex,
                                          coordinatedHTTP: true,
                                          // No host overlay needs cue replay; packaged subtitle files are independent.
                                          subtitleCueHistory: .bounded(maxCues: 1, maxBytes: 1),
                                          requireDolbyVisionTransportPairing: requireDolbyVisionTransportPairing)
        do {
            let retainedSubtitles = try retainExternalSubtitles(externalSubtitles)
            externalSubtitleDirectory = retainedSubtitles.directory
            for subtitle in retainedSubtitles.subtitles {
                try await session.addExternalSubtitle(url: subtitle.fileURL, language: subtitle.language,
                                                name: subtitle.name, isForced: subtitle.isForced)
            }
            _ = await session.setSubtitleDelaySeconds(self.subtitleDelaySeconds)
            try Task.checkCancellation()
            demux = session
            let playlist = try await session.start()
            try Task.checkCancellation()
            guard demux === session else { throw CancellationError() }
            try await validateDolbyVisionConversion()
            let deliveredAudio = session.audioTrackDeliveries
            if let preferredAudioStreamIndex {
                guard deliveredAudio.contains(where: {
                    $0.streamIndex == preferredAudioStreamIndex && $0.delivery == .streamCopy
                }) else {
                    throw SelectionError.unsupportedAudioStream(preferredAudioStreamIndex)
                }
            } else if let selected = deliveredAudio.first(where: { $0.delivery != .unavailable }) {
                guard selected.delivery == .streamCopy else {
                    throw SelectionError.unsupportedAudioStream(selected.streamIndex)
                }
                activeAudioStreamIndex = selected.streamIndex
            }
            activeAudioStreamIndex = preferredAudioStreamIndex ?? activeAudioStreamIndex
            requestedSubtitleStreamIndex = preferredSubtitleStreamIndex
            let renditions = enableSubtitleRenditions ? await session.subtitleRenditions : []
            let playableURL: URL
            if renditions.isEmpty {
                playableURL = playlist
            } else {
                // Prism's ordinary session owns a complete master, including video
                // metadata and its subtitle rendition paths. Only a muxed fallback
                // lacks that master and needs Lumina's local wrapper.
                let mediaPlaylist = try await prismMediaVariant(for: playlist)
                if mediaPlaylist == playlist {
                    let master = try await makeSubtitleMaster(
                        variantURL: mediaPlaylist,
                        prismRenditions: renditions,
                        externalSubtitles: [],
                        bandwidth: bandwidth
                    )
                    guard demux === session else {
                        await Self.stop(master)
                        throw CancellationError()
                    }
                    subtitleMaster = master
                    playableURL = master.playlistURL
                } else {
                    playableURL = playlist
                }
                hasSelectableExternalSubtitles = !externalSubtitles.isEmpty
                externalSubtitleDisplayName = externalSubtitles.first.map { hlsValue($0.name ?? $0.language ?? "Subtitle 1") }
            }
            let item = AVPlayerItem(url: playableURL)
            if let preferredAudioStreamIndex {
                guard let audioGroup = try await item.asset.loadMediaSelectionGroup(for: .audible),
                      let selectedAudio = audioGroup.defaultOption else {
                    throw SelectionError.unsupportedAudioStream(preferredAudioStreamIndex)
                }
                item.select(selectedAudio, in: audioGroup)
            }
            item.preferredForwardBufferDuration = 4
            statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
                guard item.status == .failed else { return }
                Task { @MainActor [weak self] in
                    guard self?.demux === session else { return }
                    let conversion = await Self.conversionSnapshot(for: session)
                    guard let self, self.demux === session else { return }
                    self.stop()
                    // The app samples conversion evidence after AVPlayer's
                    // failure notification too; teardown must not erase it.
                    self.terminalDolbyVisionConversion = conversion
                }
            }
            return item
        } catch {
            let master = subtitleMaster
            let retainedDirectory = externalSubtitleDirectory
            subtitleMaster = nil
            self.externalSubtitleDirectory = nil
            if demux === session { demux = nil }
            activeAudioStreamIndex = nil
            requestedSubtitleStreamIndex = nil
            hasSelectableExternalSubtitles = false
            externalSubtitleDisplayName = nil
            if let master { await Self.stop(master) }
            await session.stop()
            if let retainedDirectory { try? FileManager.default.removeItem(at: retainedDirectory) }
            if case .invalidDolbyVisionConversion = PrismCoreError.classify(error) {
                throw SelectionError.invalidDolbyVisionConversion
            }
            throw error
        }
    }

    /// Fail only once a producer observed an unsafe P7 conversion. Missing
    /// statistics are inconclusive; callers may poll this while playback proceeds.
    public func validateDolbyVisionConversion() async throws {
        guard let conversion = await dolbyVisionConversion else { return }
        guard conversion.isClean else {
            throw SelectionError.invalidDolbyVisionConversion
        }
    }

    /// AVPlayer can report a generic loopback delivery error after the producer
    /// rejected a pair. Keep that known conversion cause for compatible recovery.
    public func playbackError(for error: Error) async -> Error {
        guard !(error is CancellationError), (error as NSError).domain != NSURLErrorDomain else { return error }
        let conversion = await dolbyVisionConversion
        guard !Task.isCancelled else { return CancellationError() }
        return conversion?.hasFailure == true ? SelectionError.invalidDolbyVisionConversion : error
    }

    private struct SubtitleRendition {
        let name: String
        let language: String
        let uri: String
        let isForced: Bool
        let isDefault: Bool
    }

    private func prepareServerHLSWithSidecars(
        url: URL,
        externalSubtitles: [ExternalSubtitle],
        bandwidth: Int?
    ) async throws -> AVPlayerItem {
        guard !externalSubtitles.isEmpty else { return AVPlayerItem(url: url) }
        guard url.pathExtension.lowercased() == "m3u8" else {
            throw SelectionError.externalSubtitleRequiresHLS
        }
        for subtitle in externalSubtitles {
            guard subtitle.fileURL.pathExtension.lowercased() == "vtt" else {
                throw SelectionError.externalSubtitleMustBeWebVTT
            }
            guard let duration = subtitle.durationSeconds, duration.isFinite,
                  duration > 0, duration <= Double(Int32.max) else {
                throw SelectionError.externalSubtitleDurationRequired
            }
        }

        let master = try await makeSubtitleMaster(
            variantURL: url,
            prismRenditions: [],
            externalSubtitles: externalSubtitles,
            bandwidth: bandwidth
        )
        subtitleMaster = master
        hasSelectableExternalSubtitles = true
        externalSubtitleDisplayName = externalSubtitles.first.map { hlsValue($0.name ?? $0.language ?? "Subtitle 1") }
        let item = AVPlayerItem(url: master.playlistURL)
        item.preferredForwardBufferDuration = 4
        statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            guard item.status == .failed else { return }
            Task { @MainActor [weak self] in
                guard self?.subtitleMaster?.playlistURL == master.playlistURL else { return }
                self?.stop()
            }
        }
        return item
    }

    private func validate(_ externalSubtitles: [ExternalSubtitle]) throws {
        for subtitle in externalSubtitles {
            guard subtitle.fileURL.isFileURL else {
                throw SelectionError.externalSubtitleMustBeLocal
            }
            let values = try subtitle.fileURL.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
            guard values.isRegularFile == true,
                  let size = values.fileSize,
                  size <= Self.maximumExternalSubtitleBytes else {
                throw SelectionError.externalSubtitleTooLarge
            }
        }
    }

    private func retainExternalSubtitles(
        _ subtitles: [ExternalSubtitle]
    ) throws -> (directory: URL?, subtitles: [ExternalSubtitle]) {
        guard !subtitles.isEmpty else { return (nil, []) }
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaExternalSubtitles-\(UUID().uuidString)", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let retained = try subtitles.enumerated().map { ordinal, subtitle in
                let data = try Data(contentsOf: subtitle.fileURL, options: .mappedIfSafe)
                guard data.count <= Self.maximumExternalSubtitleBytes else {
                    throw SelectionError.externalSubtitleTooLarge
                }
                let ext = subtitle.fileURL.pathExtension.lowercased()
                let url = directory.appendingPathComponent("subtitle-\(ordinal).\(ext)")
                try data.write(to: url, options: .atomic)
                return ExternalSubtitle(
                    fileURL: url,
                    language: subtitle.language,
                    name: subtitle.name,
                    isForced: subtitle.isForced,
                    durationSeconds: subtitle.durationSeconds
                )
            }
            return (directory, retained)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    private func makeSubtitleMaster(
        variantURL: URL,
        prismRenditions: [MasterPlaylistBuilder.SubtitleRendition],
        externalSubtitles: [ExternalSubtitle],
        bandwidth: Int?
    ) async throws -> SubtitleMaster {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaSubtitleMaster-\(UUID().uuidString)", isDirectory: true)
        var server: LoopbackHTTPServer?
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            var renditions = try prismRenditions.enumerated().map { ordinal, rendition in
                guard let uri = URL(string: rendition.uri, relativeTo: variantURL)?.absoluteURL else {
                    throw URLError(.badURL)
                }
                return SubtitleRendition(
                    name: rendition.name,
                    language: rendition.language ?? "und",
                    uri: uri.absoluteString,
                    isForced: rendition.isForced,
                    isDefault: rendition.isDefault
                )
            }
            renditions.append(contentsOf: try copyExternalSubtitles(externalSubtitles, into: directory))
            guard !renditions.isEmpty else { throw URLError(.resourceUnavailable) }
            try Data(subtitleMasterPlaylist(
                variantURL: variantURL, renditions: renditions, bandwidth: bandwidth
            ).utf8).write(
                to: directory.appendingPathComponent("master.m3u8"), options: .atomic
            )
            let newServer = LoopbackHTTPServer(root: directory)
            server = newServer
            let baseURL = try await newServer.start()
            return SubtitleMaster(
                server: newServer,
                directory: directory,
                playlistURL: baseURL.appendingPathComponent("master.m3u8")
            )
        } catch {
            if let server { await server.stop() }
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    /// Resolves the one video variant produced by Lumina's own Prism loopback session before
    /// a second master wraps its subtitle renditions. This never follows a server URL or accepts
    /// a non-loopback redirect: Prism owns the input listener and the wrapper only needs its
    /// generated HLS manifest.
    private func prismMediaVariant(for playlistURL: URL) async throws -> URL {
        guard isPrismLoopbackURL(playlistURL) else { throw URLError(.badURL) }
        let guardDelegate = PrismLoopbackRedirectGuard(origin: playlistURL)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        let session = URLSession(configuration: configuration, delegate: guardDelegate, delegateQueue: nil)
        defer { session.invalidateAndCancel() }

        var request = URLRequest(url: playlistURL)
        request.timeoutInterval = 5
        request.setValue("application/vnd.apple.mpegurl, application/x-mpegURL", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse,
              response.statusCode == 200,
              let responseURL = response.url,
              samePrismLoopbackOrigin(playlistURL, responseURL) else {
            throw URLError(.cannotParseResponse)
        }
        var data = Data()
        for try await byte in bytes {
            guard data.count < Self.maximumPrismPlaylistBytes else {
                throw URLError(.cannotParseResponse)
            }
            data.append(byte)
        }
        try Task.checkCancellation()
        return try prismMediaVariantURL(masterURL: responseURL, playlist: data)
    }

    private func prismMediaVariantURL(masterURL: URL, playlist: Data) throws -> URL {
        guard let source = String(data: playlist, encoding: .utf8) else {
            throw URLError(.cannotParseResponse)
        }
        let lines = source.split(whereSeparator: { $0.isNewline }).map(String.init)
        guard lines.first?.trimmingCharacters(in: .whitespacesAndNewlines) == "#EXTM3U" else {
            throw URLError(.cannotParseResponse)
        }
        let streamInfos = lines.indices.filter { lines[$0].hasPrefix("#EXT-X-STREAM-INF:") }
        if streamInfos.isEmpty {
            guard lines.contains(where: { $0.hasPrefix("#EXTINF:") || $0.hasPrefix("#EXT-X-TARGETDURATION:") }) else {
                throw URLError(.cannotParseResponse)
            }
            return masterURL
        }
        guard streamInfos.count == 1,
              let variantLine = lines[(streamInfos[0] + 1)...].first(where: { !$0.isEmpty && !$0.hasPrefix("#") }),
              let variantURL = URL(string: variantLine, relativeTo: masterURL)?.absoluteURL,
              variantURL.pathExtension.lowercased() == "m3u8",
              samePrismLoopbackOrigin(masterURL, variantURL) else {
            throw URLError(.cannotParseResponse)
        }
        return variantURL
    }

    private func copyExternalSubtitles(
        _ subtitles: [ExternalSubtitle],
        into directory: URL
    ) throws -> [SubtitleRendition] {
        try subtitles.enumerated().map { ordinal, subtitle in
            let data = try Data(contentsOf: subtitle.fileURL, options: .mappedIfSafe)
            guard data.count <= Self.maximumExternalSubtitleBytes else {
                throw SelectionError.externalSubtitleTooLarge
            }
            let fileName = "sidecar-\(ordinal).vtt"
            try normalizedWebVTT(data, delaySeconds: subtitleDelaySeconds).write(
                to: directory.appendingPathComponent(fileName), options: .atomic
            )
            let duration = subtitle.durationSeconds ?? 1
            try Data(sidecarPlaylist(fileName: fileName, duration: duration).utf8).write(
                to: directory.appendingPathComponent("sidecar-\(ordinal).m3u8"), options: .atomic
            )
            return SubtitleRendition(
                name: subtitle.name ?? subtitle.language ?? "Subtitle \(ordinal + 1)",
                language: subtitle.language ?? "und",
                uri: "sidecar-\(ordinal).m3u8",
                isForced: subtitle.isForced,
                isDefault: false
            )
        }
    }

    private func normalizedWebVTT(_ data: Data, delaySeconds: Double) throws -> Data {
        guard var text = String(data: data, encoding: .utf8) else {
            throw SelectionError.externalSubtitleMustBeWebVTT
        }
        if text.unicodeScalars.first == "\u{FEFF}" { text.removeFirst() }
        text = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        guard let newline = text.firstIndex(of: "\n") else {
            throw SelectionError.externalSubtitleMustBeWebVTT
        }
        let header = text[..<newline]
        guard header == "WEBVTT" || header == "WEBVTT\r" || header.hasPrefix("WEBVTT ") || header.hasPrefix("WEBVTT\t") else {
            throw SelectionError.externalSubtitleMustBeWebVTT
        }
        if !text.contains("X-TIMESTAMP-MAP=") {
            text.insert(contentsOf: "\nX-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000", at: newline)
        }
        if delaySeconds != 0,
           text.range(of: #"<(?:(?:\d{2}:)?\d{2}:\d{2}\.\d{3})>"#, options: .regularExpression) != nil {
            // Karaoke timestamp tags need the same offset as their containing
            // cue. Do not silently leave them behind the shifted cue timing.
            throw SelectionError.externalSubtitleUnsupportedTiming
        }
        return Data(try shiftWebVTTCues(in: text, by: delaySeconds).utf8)
    }

    private func shiftWebVTTCues(in text: String, by delay: Double) throws -> String {
        guard delay != 0 else { return text }
        let blocks = text.components(separatedBy: "\n\n")
        return try blocks.compactMap { block in
            let lines = block.components(separatedBy: "\n")
            guard let first = lines.first?.trimmingCharacters(in: .whitespacesAndNewlines),
                  first != "NOTE", !first.hasPrefix("NOTE "), !first.hasPrefix("NOTE\t"),
                  first != "STYLE", first != "REGION" else {
                return block
            }
            let timingIndices = lines.indices.filter { lines[$0].contains("-->") }
            guard !timingIndices.isEmpty else { return block }
            guard timingIndices.count == 1, let timingIndex = timingIndices.first,
                  let timing = parseWebVTTTiming(lines[timingIndex]) else {
                throw SelectionError.externalSubtitleUnsupportedTiming
            }
            let start = timing.start + delay
            let end = timing.end + delay
            guard end > 0 else { return nil }
            var shifted = lines
            shifted[timingIndex] = "\(webVTTTimestamp(max(0, start))) --> \(webVTTTimestamp(end))\(timing.settings)"
            return shifted.joined(separator: "\n")
        }.joined(separator: "\n\n")
    }

    private func parseWebVTTTiming(_ line: String) -> (start: Double, end: Double, settings: String)? {
        let parts = line.components(separatedBy: "-->")
        guard parts.count == 2,
              let start = webVTTSeconds(parts[0].trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
        let endAndSettings = parts[1].split(whereSeparator: { $0.isWhitespace })
        guard let endPart = endAndSettings.first,
              let end = webVTTSeconds(String(endPart)), end > start else { return nil }
        let settings = endAndSettings.dropFirst().isEmpty ? "" : " " + endAndSettings.dropFirst().joined(separator: " ")
        return (start, end, settings)
    }

    private func webVTTSeconds(_ timestamp: String) -> Double? {
        let fields = timestamp.split(separator: ":")
        guard (fields.count == 2 || fields.count == 3),
              let seconds = Double(fields[fields.count - 1]),
              let minutes = Double(fields[fields.count - 2]),
              seconds.isFinite, minutes.isFinite,
              seconds >= 0, seconds < 60, minutes >= 0 else { return nil }
        let hours: Double
        if fields.count == 3 {
            guard let parsedHours = Double(fields[0]), parsedHours.isFinite, parsedHours >= 0 else { return nil }
            hours = parsedHours
        } else {
            hours = 0
        }
        let total = hours * 3_600 + minutes * 60 + seconds
        guard total.isFinite, total <= Self.maximumWebVTTSeconds else { return nil }
        return total
    }

    private func webVTTTimestamp(_ seconds: Double) -> String {
        let bounded = seconds.isFinite ? min(Self.maximumWebVTTSeconds, max(0, seconds)) : 0
        let milliseconds = Int((bounded * 1_000).rounded())
        let hours = milliseconds / 3_600_000
        let minutes = (milliseconds % 3_600_000) / 60_000
        let remainder = milliseconds % 60_000
        return String(format: "%02d:%02d:%02d.%03d", locale: Locale(identifier: "en_US_POSIX"),
                      hours, minutes, remainder / 1_000, remainder % 1_000)
    }

    /// Positive values render text later. The same bound is used by PrismCore.
    public static func normalizedSubtitleDelay(_ seconds: Double) -> Double {
        guard seconds.isFinite else { return 0 }
        return min(10, max(-10, seconds))
    }

    private func sidecarPlaylist(fileName: String, duration: Double) -> String {
        let targetDuration = max(1, Int(ceil(duration)))
        return """
        #EXTM3U
        #EXT-X-VERSION:3
        #EXT-X-TARGETDURATION:\(targetDuration)
        #EXT-X-PLAYLIST-TYPE:VOD
        #EXTINF:\(String(format: "%.3f", locale: Locale(identifier: "en_US_POSIX"), duration)),
        \(fileName)
        #EXT-X-ENDLIST
        """
    }

    private func subtitleMasterPlaylist(
        variantURL: URL,
        renditions: [SubtitleRendition],
        bandwidth: Int?
    ) -> String {
        let groupID = "lumina-subtitles"
        var lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
        for rendition in renditions {
            lines.append(
                "#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID=\(hlsQuoted(groupID)),NAME=\(hlsQuoted(rendition.name)),LANGUAGE=\(hlsQuoted(rendition.language)),DEFAULT=\(rendition.isDefault ? "YES" : "NO"),AUTOSELECT=\(rendition.isDefault ? "YES" : "NO"),FORCED=\(rendition.isForced ? "YES" : "NO"),URI=\(hlsQuoted(rendition.uri))"
            )
        }
        // PrismCore intentionally keeps codec/display signalling private when
        // `forceMuxedShape` is active, so this wrapper must not invent it. The
        // server's source bitrate is preferred; the fallback only overstates a
        // fixed variant's scheduling budget rather than claiming a false codec.
        let declaredBandwidth = bandwidth.flatMap { $0 > 0 ? $0 : nil } ?? 50_000_000
        lines.append("#EXT-X-STREAM-INF:BANDWIDTH=\(declaredBandwidth),SUBTITLES=\(hlsQuoted(groupID))")
        lines.append(variantURL.absoluteString)
        return lines.joined(separator: "\n") + "\n"
    }

    private func hlsQuoted(_ value: String) -> String { "\"" + hlsValue(value) + "\"" }

    private func hlsValue(_ value: String) -> String {
        String(value.unicodeScalars.map { scalar in
            switch scalar.value {
            case 0...31, 34, 92, 127:
                return " "
            default:
                return Character(String(scalar))
            }
        })
    }

    private static func stop(_ master: SubtitleMaster) async {
        await master.server.stop()
        try? FileManager.default.removeItem(at: master.directory)
    }

    /// Resume on readiness, failure, item replacement or cancellation without a recurring timer.
    public static func waitUntilReady(_ player: AVPlayer, timeout: Duration = .seconds(30)) async throws {
        try Task.checkCancellation()
        guard let item = player.currentItem else { throw CancellationError() }
        let (statuses, continuation) = AsyncStream.makeStream(of: AVPlayerItem.Status.self, bufferingPolicy: .bufferingNewest(1))
        let observation = item.observe(\.status, options: [.initial, .new]) { item, _ in
            continuation.yield(item.status)
        }
        let replacement = player.observe(\.currentItem, options: [.new]) { player, _ in
            if player.currentItem !== item { continuation.finish() }
        }
        defer { observation.invalidate(); replacement.invalidate(); continuation.finish() }
        let status = try await withThrowingTaskGroup(of: AVPlayerItem.Status.self) { group in
            group.addTask {
                for await status in statuses {
                    try Task.checkCancellation()
                    if status == .readyToPlay || status == .failed { return status }
                }
                throw CancellationError()
            }
            group.addTask {
                try await Task.sleep(for: timeout)
                throw POSIXError(.ETIMEDOUT)
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
        try Task.checkCancellation()
        guard player.currentItem === item else { throw CancellationError() }
        if status == .failed { throw item.error ?? POSIXError(.EIO) }
    }

    @discardableResult
    public func stop() -> Task<Void, Never> {
        let session = demux
        let master = subtitleMaster
        let retainedDirectory = externalSubtitleDirectory
        demux = nil
        terminalDolbyVisionConversion = nil
        subtitleMaster = nil
        self.externalSubtitleDirectory = nil
        statusObservation = nil
        activeAudioStreamIndex = nil
        requestedSubtitleStreamIndex = nil
        hasSelectableExternalSubtitles = false
        externalSubtitleDisplayName = nil
        return Task {
            if let master { await Self.stop(master) }
            if let session { await session.stop() }
            if let retainedDirectory { try? FileManager.default.removeItem(at: retainedDirectory) }
        }
    }

    isolated deinit {
        let session = demux
        let master = subtitleMaster
        let retainedDirectory = externalSubtitleDirectory
        Task {
            if let master { await Self.stop(master) }
            if let session { await session.stop() }
            if let retainedDirectory { try? FileManager.default.removeItem(at: retainedDirectory) }
        }
    }
}

private final class PrismLoopbackRedirectGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let origin: URL

    init(origin: URL) { self.origin = origin }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        guard let target = request.url, samePrismLoopbackOrigin(origin, target) else {
            completionHandler(nil)
            task.cancel()
            return
        }
        completionHandler(request)
    }
}

private func isPrismLoopbackURL(_ url: URL) -> Bool {
    guard url.scheme?.lowercased() == "http",
          url.host?.lowercased() == "127.0.0.1",
          url.port != nil,
          url.user == nil,
          url.password == nil else {
        return false
    }
    return true
}

private func samePrismLoopbackOrigin(_ left: URL, _ right: URL) -> Bool {
    guard isPrismLoopbackURL(left), isPrismLoopbackURL(right) else { return false }
    return left.port == right.port
}
