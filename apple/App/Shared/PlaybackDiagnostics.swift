import AVFoundation
import Foundation
import LuminaCore
import Observation
import ObjectiveC
import SwiftUI

/// A user-started, process-wide capture survives replacement of the playback presentation.
@MainActor @Observable
final class PlaybackDiagnostics {
    static let shared = PlaybackDiagnostics()
    private(set) var isRecording = false
    private(set) var isFinishing = false
    private(set) var exportURL: URL?
    private(set) var errorMessage: String?
    @ObservationIgnored private var capture: PlaybackDiagnosticCapture?
    @ObservationIgnored private var deadline: TimeInterval?
    private static var identityKey: UInt8 = 0
    private static var nextIdentity: UInt64 = 0
    @ObservationIgnored private var timeoutTask: Task<Void, Never>?

    private var fileURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PlaybackDiagnostics", isDirectory: true)
            .appendingPathComponent("Lumina-playback-diagnostics.jsonl")
    }

    // Read by capture hooks without adding SwiftUI observation dependencies.
    var isCapturing: Bool { capture?.isRecording == true }

    private init() {
        if FileManager.default.fileExists(atPath: fileURL.path) { exportURL = fileURL }
    }

    func start() {
        guard !isRecording, !isFinishing else { return }
        errorMessage = nil
        do {
            let directory = fileURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            var resourceValues = URLResourceValues()
            resourceValues.isExcludedFromBackup = true
            var protectedDirectory = directory
            try protectedDirectory.setResourceValues(resourceValues)
            capture = try PlaybackDiagnosticCapture.start(
                fileURL: fileURL,
                version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0",
                build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0",
                osVersion: ProcessInfo.processInfo.operatingSystemVersion
            )
            exportURL = nil
            isRecording = true
            deadline = ProcessInfo.processInfo.systemUptime + 300
            timeoutTask = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(300)) } catch { return }
                self?.finish(reason: .timeout)
            }
        } catch {
            errorMessage = "Playback diagnostics could not start."
        }
    }

    func record(_ event: PlaybackDiagnosticEvent, sample: PlaybackDiagnosticSample) {
        guard let capture, capture.isRecording else { return }
        if let deadline, ProcessInfo.processInfo.systemUptime >= deadline { finish(reason: .timeout); return }
        if !capture.record(event, sample: sample) { finish(reason: .limit) }
    }

    func finish(reason: PlaybackDiagnosticStopReason = .user) {
        guard isRecording, let capture else { return }
        isRecording = false
        deadline = nil
        isFinishing = true
        timeoutTask?.cancel()
        timeoutTask = nil
        Task {
            defer { self.capture = nil; isFinishing = false }
            do { exportURL = try await capture.finish(reason: reason) }
            catch {
                if FileManager.default.fileExists(atPath: fileURL.path) {
                    exportURL = fileURL
                    errorMessage = "Recording was interrupted. You can share the saved portion."
                } else { errorMessage = "Playback diagnostics could not be saved." }
            }
        }
    }

    static func identity(_ object: AnyObject?) -> UInt64? {
        guard let object else { return nil }
        if let value = objc_getAssociatedObject(object, &identityKey) as? NSNumber { return value.uint64Value }
        nextIdentity += 1
        objc_setAssociatedObject(object, &identityKey, NSNumber(value: nextIdentity), .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
        return nextIdentity
    }

    func delete() {
        guard !isRecording, !isFinishing else { return }
        do {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                try FileManager.default.removeItem(at: fileURL)
            }
            exportURL = nil
            errorMessage = nil
        } catch { errorMessage = "Playback diagnostics could not be deleted." }
    }
}

#if os(iOS)
struct PlaybackDiagnosticsControls: View {
    let playback: PlaybackModel
    private var diagnostics: PlaybackDiagnostics { .shared }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Playback diagnostics").font(.headline)
            Text("Record playback timing and display changes to help investigate playback issues. Media titles, addresses, and account details are excluded. Nothing is sent automatically.")
                .font(.caption).foregroundStyle(.secondary)
            if diagnostics.isRecording {
                Text("Recording — stops automatically after five minutes.")
                    .font(.caption).accessibilityIdentifier("playbackDiagnosticsRecording")
                Button("Finish recording") {
                    diagnostics.finish()
                }
                .accessibilityIdentifier("playbackDiagnosticsStopButton")
            } else if diagnostics.isFinishing {
                ProgressView("Saving diagnostics…")
            } else {
                Button(diagnostics.exportURL == nil ? "Start recording" : "Start new recording") {
                    diagnostics.start()
                    playback.recordPlaybackDiagnostic(.sample, includeNegotiation: true)
                }
                .accessibilityIdentifier("playbackDiagnosticsStartButton")
                if let url = diagnostics.exportURL {
                    ShareLink("Share diagnostics", item: url)
                        .accessibilityIdentifier("playbackDiagnosticsShareButton")
                    Button("Delete recording", role: .destructive) { diagnostics.delete() }
                        .accessibilityIdentifier("playbackDiagnosticsDeleteButton")
                    Text("Starting a new recording replaces the previous recording.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let message = diagnostics.errorMessage {
                Text(message).font(.caption).accessibilityIdentifier("playbackDiagnosticsError")
            }
        }
        .buttonStyle(LuminaQuietActionStyle())
        .controlSize(.large)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackDiagnosticsControls")
    }
}
#endif

#if os(iOS)
/// Samples only while the Info tab is visible and the app is active. State lives
/// here so a one-second metrics update does not invalidate the video stage.
struct PlaybackInfoView: View {
    let playback: PlaybackModel
    let audioGroup: AVMediaSelectionGroup?
    @Environment(\.scenePhase) private var scenePhase
    @State private var sections: [StatsSection] = []
    @State private var formatSignature = ""
    @State private var formats: [String: String] = [:]
    @State private var transferSample: TransferSample?

    private struct RefreshID: Equatable {
        let active: Bool
        let audioGroup: ObjectIdentifier?
    }
    private struct TransferSample {
        let item: ObjectIdentifier
        let events: Int
        let bytes: Int64
        let time: TimeInterval
    }
    #if LUMINA_UI_TESTING
    @State private var sampleCount = 0
    #endif

    private struct StatsRow: Identifiable {
        let id: String
        let label: String
        let value: String
    }
    private struct StatsSection: Identifiable {
        let id: String
        let title: String
        let rows: [StatsRow]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(scenePhase == .active ? "Stats for nerds · updates every second" : "Stats for nerds · updates paused")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(sections) { section in
                VStack(alignment: .leading, spacing: 12) {
                    Text(section.title).font(.headline).accessibilityAddTraits(.isHeader)
                    ForEach(section.rows) { row in
                        LabeledContent(row.label) {
                            Text(row.value).monospacedDigit().multilineTextAlignment(.trailing)
                                #if os(iOS)
                                .textSelection(.enabled)
                                #endif
                        }
                        .font(.subheadline)
                        .accessibilityIdentifier("playbackStat-\(row.id)")
                    }
                }
                Divider()
            }
            if playback.request.usesClientDemux && !playback.isCasting {
                Text("Device remux: source totals and recent rate measure compressed bytes read before remuxing, excluding container overhead. AVPlayer throughput describes local HLS delivery.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(playback.isCasting
                 ? "Live receiver metrics are not reported by Chromecast. Source details describe the original file."
                 : "AVPlayer counters describe the current access-log event and may update after segment downloads. Track HDR describes the stream metadata, not the display’s HDR output. Not reported means the player has not supplied that measurement.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoPlaybackStats")
        #if LUMINA_UI_TESTING
        .accessibilityValue("samples=\(sampleCount)")
        #endif
        .task(id: RefreshID(active: scenePhase == .active, audioGroup: audioGroup.map(ObjectIdentifier.init))) {
            guard scenePhase == .active else { return }
            transferSample = nil
            while !Task.isCancelled {
                await refresh()
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }

    @MainActor private func refresh() async {
        let item = playback.player.currentItem
        let remote = playback.isCasting
        let events = remote ? [] : item?.accessLog()?.events ?? []
        let event = events.last
        let enabledTracks = remote ? [] : item?.tracks.filter(\.isEnabled) ?? []
        let size = item?.presentationSize ?? .zero
        let signature = "\(item.map { ObjectIdentifier($0).hashValue } ?? 0)-\(enabledTracks.map { ObjectIdentifier($0).hashValue })-\(size)-\(event?.indicatedBitrate ?? -1)"
        if signature != formatSignature {
            var next: [String: String] = [:]
            for playerTrack in enabledTracks {
                guard let track = playerTrack.assetTrack,
                      let descriptions = try? await track.load(.formatDescriptions) else { continue }
                guard !Task.isCancelled else { return }
                let kind = track.mediaType == .video ? "video" : track.mediaType == .audio ? "audio" : "other"
                next[kind + "Codec"] = descriptions.map(Self.codec).uniquedStatsText
                if kind == "video" {
                    next["hdr"] = descriptions.map(Self.dynamicRange).uniquedStatsText
                    if let fps = try? await track.load(.nominalFrameRate), fps.isFinite, fps > 0 {
                        next["fps"] = Double(fps).formatted(.number.precision(.fractionLength(0...3))) + " fps (nominal)"
                    }
                } else if kind == "audio" {
                    next["audioFormat"] = descriptions.compactMap { description in
                        guard let audio = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee,
                              audio.mSampleRate.isFinite, audio.mSampleRate > 0, audio.mChannelsPerFrame > 0 else { return nil }
                        return "\(audio.mChannelsPerFrame) ch · \((audio.mSampleRate / 1_000).formatted(.number.precision(.fractionLength(0...3)))) kHz"
                    }.uniquedStatsText
                }
            }
            guard !Task.isCancelled else { return }
            formats = next
            formatSignature = signature
        }
        guard !Task.isCancelled else { return }
        let sourceBytes = remote ? nil : playback.videoSourceBytesRead
        let transferred = sourceBytes ?? event?.numberOfBytesTransferred
        let counterEvents = sourceBytes == nil ? events.count : 0
        let now = ProcessInfo.processInfo.systemUptime
        var recentBitrate: Double?
        if let item, let transferred, transferred >= 0 {
            if let previous = transferSample, previous.item == ObjectIdentifier(item), previous.events == counterEvents,
               transferred >= previous.bytes, now > previous.time {
                recentBitrate = Double(transferred - previous.bytes) * 8 / (now - previous.time)
            }
            transferSample = TransferSample(item: ObjectIdentifier(item), events: counterEvents,
                                            bytes: transferred, time: now)
        } else { transferSample = nil }
        let current = playback.player.currentTime().seconds
        let buffered = remote ? nil : item?.loadedTimeRanges.compactMap { value -> Double? in
            let range = value.timeRangeValue
            let start = range.start.seconds, end = range.end.seconds
            guard current.isFinite, start.isFinite, end.isFinite, start <= current + 0.05, end >= current else { return nil }
            return end - current
        }.max()
        let currentFPS = enabledTracks.filter { $0.assetTrack?.mediaType == .video }
            .map(\.currentVideoFrameRate).filter { $0.isFinite && $0 >= 0 }.max()
        let unavailable = "Not reported"
        let selection = remote ? nil : audioGroup.flatMap { item?.currentMediaSelection.selectedMediaOption(in: $0)?.displayName }
        let delivery = (item?.asset as? AVURLAsset)?.url.pathExtension.lowercased()
        let state: String
        if remote { state = "On Chromecast" }
        else if item == nil { state = "No active item" }
        else if item?.status == .failed { state = "Failed" }
        else {
            switch playback.player.timeControlStatus {
            case .playing: state = "Playing"
            case .waitingToPlayAtSpecifiedRate: state = "Buffering"
            default: state = "Paused"
            }
        }
        sections = [
            StatsSection(id: "playback", title: "Playing stream", rows: [
                StatsRow(id: "state", label: "State", value: state),
                StatsRow(id: "route", label: "Route", value: remote ? "Chromecast" : playback.player.isExternalPlaybackActive ? "AirPlay" : "This device"),
                StatsRow(id: "resolution", label: "Resolution", value: !remote && size.width.isFinite && size.height.isFinite && size.width > 0 && size.height > 0 && size.width < CGFloat(Int.max) && size.height < CGFloat(Int.max) ? "\(Int(size.width)) × \(Int(size.height))" : unavailable),
                StatsRow(id: "videoCodec", label: "Video codec", value: formats["videoCodec"] ?? unavailable),
                StatsRow(id: "hdr", label: "Track HDR format", value: formats["hdr"] ?? unavailable),
                StatsRow(id: "fps", label: "Track frame rate", value: formats["fps"] ?? unavailable),
                StatsRow(id: "currentFPS", label: "Current frame rate", value: currentFPS.map { Double($0).formatted(.number.precision(.fractionLength(0...2))) + " fps" } ?? unavailable),
                StatsRow(id: "delivery", label: "Delivery container", value: remote ? unavailable : delivery == "m3u8" ? "HLS" : delivery.flatMap { $0.isEmpty ? nil : $0.uppercased() }
                    ?? (!playback.request.isTranscoding && !playback.request.usesClientDemux ? playback.request.sourceContainer?.uppercased() : nil) ?? unavailable)
            ]),
            StatsSection(id: "network", title: "Network & performance", rows: [
                StatsRow(id: "recentDownload", label: sourceBytes == nil ? "Recent download" : "Recent source read", value: Self.bitrate(recentBitrate)),
                StatsRow(id: "throughput", label: sourceBytes == nil ? "Observed throughput" : "Local delivery throughput", value: Self.bitrate(event?.observedBitrate)),
                StatsRow(id: "streamBitrate", label: "Advertised stream bitrate", value: Self.bitrate(event?.indicatedBitrate)),
                StatsRow(id: "transferred", label: sourceBytes == nil ? "Transferred" : "Source data read", value: transferred.flatMap { $0 >= 0 ? ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) : nil } ?? unavailable),
                StatsRow(id: "buffer", label: "Buffer ahead", value: buffered.map { $0.formatted(.number.precision(.fractionLength(1))) + " s" } ?? unavailable),
                StatsRow(id: "stalls", label: "Stalls", value: event.flatMap { $0.numberOfStalls >= 0 ? String($0.numberOfStalls) : nil } ?? unavailable),
                StatsRow(id: "dropped", label: "Dropped frames", value: event.flatMap { $0.numberOfDroppedVideoFrames >= 0 ? String($0.numberOfDroppedVideoFrames) : nil } ?? unavailable)
            ]),
            StatsSection(id: "audio", title: "Playing audio", rows: [
                StatsRow(id: "audioCodec", label: "Audio codec", value: formats["audioCodec"] ?? unavailable),
                StatsRow(id: "audioFormat", label: "Track channels / rate", value: formats["audioFormat"] ?? unavailable),
                StatsRow(id: "audioTrack", label: "Selected track", value: selection ?? unavailable)
            ]),
            StatsSection(id: "source", title: "Source file", rows: [
                StatsRow(id: "sourceContainer", label: "Source container", value: playback.request.sourceContainer?.uppercased() ?? unavailable),
                StatsRow(id: "sourceBitrate", label: "Source bitrate", value: Self.bitrate(playback.request.bitRate.map(Double.init)))
            ])
        ]
        #if LUMINA_UI_TESTING
        sampleCount += 1
        #endif
    }

    private static func bitrate(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0 else { return "Not reported" }
        return (value / 1_000_000).formatted(.number.precision(.fractionLength(2))) + " Mbps"
    }

    private static func codec(_ description: CMFormatDescription) -> String {
        let code = CMFormatDescriptionGetMediaSubType(description)
        let bytes = [24, 16, 8, 0].map { UInt8(truncatingIfNeeded: code >> $0) }
        let raw = bytes.allSatisfy { (32...126).contains($0) }
            ? String(decoding: bytes, as: UTF8.self).trimmingCharacters(in: .whitespaces)
            : "0x" + String(code, radix: 16)
        let name = ["avc1": "H.264", "hvc1": "HEVC", "hev1": "HEVC", "dvh1": "Dolby Vision / HEVC", "dvhe": "Dolby Vision / HEVC", "aac": "AAC", "mp4a": "AAC", "ac-3": "AC-3", "ec-3": "E-AC-3", "alac": "ALAC", "flac": "FLAC", "lpcm": "PCM"][raw]
        return name.map { "\($0) (\(raw))" } ?? raw
    }

    private static func dynamicRange(_ description: CMFormatDescription) -> String {
        let code = CMFormatDescriptionGetMediaSubType(description)
        // Dolby Vision uses dvh1 or the older dvhe sample entry.
        if code == kCMVideoCodecType_DolbyVisionHEVC || code == 0x64766865 { return "Dolby Vision" }
        let transfer = CMFormatDescriptionGetExtension(description, extensionKey: kCMFormatDescriptionExtension_TransferFunction) as? String
        if transfer == (kCMFormatDescriptionTransferFunction_SMPTE_ST_2084_PQ as String) { return "PQ / HDR" }
        if transfer == (kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG as String) { return "HLG" }
        if [kCMFormatDescriptionTransferFunction_ITU_R_709_2 as String,
            kCMFormatDescriptionTransferFunction_ITU_R_2020 as String,
            kCMFormatDescriptionTransferFunction_sRGB as String].contains(where: { $0 == transfer }) { return "SDR" }
        return "Not reported"
    }
}

private extension [String] {
    var uniquedStatsText: String? {
        let values = Array(Set(self)).sorted()
        return values.isEmpty ? nil : values.joined(separator: " / ")
    }
}
#endif
