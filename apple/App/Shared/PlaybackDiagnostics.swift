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
        guard isRecording, let capture else { return }
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
                    playback.recordPlaybackDiagnostic(.sample)
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
