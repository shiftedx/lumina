import Foundation

public enum PlaybackDiagnosticEvent: String, Codable, Sendable {
    case captureStarted, captureStopped, sample, modelCreated, start, initialSeek, pause, play
    case seekRequested, seekCompleted, timeJump, itemReplaced
    case sceneActive, sceneInactive, sceneBackground, appResignActive, appBecomeActive
    case audioInterruptionBegan, audioInterruptionEnded, audioRouteChanged
    case pictureInPictureChanged, rateChanged, timeControlChanged
    case rendererCreated, rendererDetached, rendererWindowChanged, rendererLayout, rendererReadyChanged
    case reportQueued, reportAcknowledged, reportFailed, accessLog, errorLog, close
}

public enum PlaybackDiagnosticErrorDomain: String, Codable, Sendable {
    case avFoundation, url, osStatus, other
}

public enum PlaybackDiagnosticRateChangeReason: String, Codable, Sendable {
    case appBackgrounded, setRateCalled, audioSessionInterrupted, setRateFailed, other, unknown
}

public enum PlaybackDiagnosticWaitingReason: String, Codable, Sendable {
    case minimizeStalls, evaluateBuffering, noItem, other
}

public enum PlaybackDiagnosticStopReason: String, Codable, Sendable {
    case user, timeout, limit
}

public struct PlaybackDiagnosticSample: Codable, Sendable {
    public var modelID: UUID?
    public var playerID: UInt64?
    public var viewID: UInt64?
    public var layerID: UInt64?
    public var seekFinished: Bool?
    public var itemID: UInt64?
    public var positionSeconds: Double?
    public var rate: Double?
    public var rateChangeReason: PlaybackDiagnosticRateChangeReason?
    public var waitingReason: PlaybackDiagnosticWaitingReason?
    public var nativeReason: Int?
    public var requestedPositionSeconds: Double?
    public var timeControlStatus: Int?
    public var itemStatus: Int?
    public var playbackRequested: Bool?
    public var bufferEmpty: Bool?
    public var likelyToKeepUp: Bool?
    public var bufferedUntilSeconds: Double?
    public var seekTargetSeconds: Double?
    public var width: Double?
    public var height: Double?
    public var displayReady: Bool?
    public var windowID: UInt64?
    public var sceneState: Int?
    public var usesClientDemux: Bool?
    public var errorCode: Int?
    public var errorDomain: PlaybackDiagnosticErrorDomain?
    public var accessStalls: Int?
    public var accessObservedBitrate: Double?
    public var accessIndicatedBitrate: Double?

    public init() {}

    fileprivate var sanitized: Self {
        var result = self
        result.positionSeconds = positionSeconds.flatMap { $0.isFinite ? $0 : nil }
        result.rate = rate.flatMap { $0.isFinite ? $0 : nil }
        result.requestedPositionSeconds = requestedPositionSeconds.flatMap { $0.isFinite ? $0 : nil }
        result.bufferedUntilSeconds = bufferedUntilSeconds.flatMap { $0.isFinite ? $0 : nil }
        result.seekTargetSeconds = seekTargetSeconds.flatMap { $0.isFinite ? $0 : nil }
        result.width = width.flatMap { $0.isFinite ? $0 : nil }
        result.height = height.flatMap { $0.isFinite ? $0 : nil }
        result.accessObservedBitrate = accessObservedBitrate.flatMap { $0.isFinite ? $0 : nil }
        result.accessIndicatedBitrate = accessIndicatedBitrate.flatMap { $0.isFinite ? $0 : nil }
        return result
    }
}

/// A bounded, explicitly enabled capture. Only typed transport state reaches disk.
@MainActor
public final class PlaybackDiagnosticCapture {
    private enum CaptureError: Error { case invalidMetadata }
    public static let maximumBytes = 1 << 20
    public static let maximumRecords = 2_000
    public private(set) var isRecording = true

    private struct Record: Encodable {
        let schema: Int = 1
        let event: PlaybackDiagnosticEvent
        let elapsedSeconds: Double
        var sample: PlaybackDiagnosticSample?
        var version: String?
        var build: String?
        var osMajor: Int?
        var osMinor: Int?
        var osPatch: Int?
        var suppressedEvents: Int?
        var records: Int?
        var stopReason: PlaybackDiagnosticStopReason?
    }

    private let fileURL: URL
    private let writer: Writer
    private let startedAt = ProcessInfo.processInfo.systemUptime
    private var bytes = 0
    private var records = 0
    private var suppressed = 0
    private var lastElapsed = 0.0
    private var rateWindow = 0.0
    private var windowRecords = 0
    private var windowHotRecords = 0
    private var finalization: Task<URL, Error>?
    private let encoder = JSONEncoder()

    private init(fileURL: URL) {
        self.fileURL = fileURL
        writer = Writer(fileURL: fileURL)
    }

    public static func start(fileURL: URL, version: String, build: String,
                             osVersion: OperatingSystemVersion) throws -> PlaybackDiagnosticCapture {
        guard fileURL.isFileURL, validVersion(version), validVersion(build) else {
            throw CaptureError.invalidMetadata
        }
        let capture = PlaybackDiagnosticCapture(fileURL: fileURL)
        let header = Record(event: .captureStarted, elapsedSeconds: 0, version: version, build: build,
                            osMajor: osVersion.majorVersion, osMinor: osVersion.minorVersion,
                            osPatch: osVersion.patchVersion)
        let data = try capture.line(header)
        capture.writer.begin(data)
        capture.bytes = data.count
        capture.records = 1
        return capture
    }

    private static func validVersion(_ value: String) -> Bool {
        // Numeric dotted app versions/build numbers only; never arbitrary metadata.
        !value.isEmpty && value.utf8.count <= 32
            && value.split(separator: ".", omittingEmptySubsequences: false).allSatisfy {
                !$0.isEmpty && $0.utf8.allSatisfy { (48...57).contains($0) }
            }
    }

    @discardableResult
    public func record(_ event: PlaybackDiagnosticEvent, sample: PlaybackDiagnosticSample) -> Bool {
        record(event, sample: sample, uptime: ProcessInfo.processInfo.systemUptime)
    }

    // Explicit time seam keeps burst/limit tests independent of wall-clock sleeps.
    @discardableResult
    func record(_ event: PlaybackDiagnosticEvent, sample: PlaybackDiagnosticSample, uptime: Double) -> Bool {
        guard isRecording else { return false }
        if records >= Self.maximumRecords - 1 || bytes >= Self.maximumBytes - 1_024 {
            isRecording = false
            return false
        }
        let elapsed = uptime.isFinite ? max(lastElapsed, uptime - startedAt) : lastElapsed
        lastElapsed = elapsed
        if elapsed - rateWindow >= 1 {
            rateWindow = elapsed
            windowRecords = 0
            windowHotRecords = 0
        }
        let isHot = event == .sample || event == .rendererLayout
        // Preserve half the window for transport/lifecycle evidence after layout bursts.
        guard windowRecords < 16, !isHot || windowHotRecords < 8 else {
            suppressed += 1
            return true
        }
        let record = Record(event: event, elapsedSeconds: elapsed, sample: sample.sanitized)
        guard let data = try? line(record), bytes + data.count <= Self.maximumBytes - 1_024 else {
            isRecording = false
            return false
        }
        writer.append(data)
        bytes += data.count
        records += 1
        windowRecords += 1
        if isHot { windowHotRecords += 1 }
        return true
    }

    public func finish(reason: PlaybackDiagnosticStopReason = .user) async throws -> URL {
        if let finalization { return try await finalization.value }
        isRecording = false
        let summary = Record(event: .captureStopped,
                             elapsedSeconds: max(lastElapsed, ProcessInfo.processInfo.systemUptime - startedAt),
                             suppressedEvents: suppressed, records: records + 1, stopReason: reason)
        let data = try line(summary)
        let writer = self.writer
        let fileURL = self.fileURL
        let task = Task<URL, Error> {
            try await writer.finish(data)
            return fileURL
        }
        finalization = task
        return try await task.value
    }

    private func line(_ record: Record) throws -> Data {
        var data = try encoder.encode(record)
        data.append(0x0A)
        return data
    }
}

/// Every handle operation and error mutation is confined to this serial queue.
private final class Writer: @unchecked Sendable {
    private let queue = DispatchQueue(label: "dev.lumina.playback-diagnostics", qos: .utility)
    private let fileURL: URL
    private var handle: FileHandle?
    private var failure: Error?

    init(fileURL: URL) { self.fileURL = fileURL }

    func begin(_ header: Data) {
        queue.async { [self] in
            do {
                #if os(iOS) || os(tvOS) || os(visionOS)
                let options: Data.WritingOptions = [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
                #else
                let options: Data.WritingOptions = .atomic
                #endif
                try Data().write(to: fileURL, options: options)
                handle = try FileHandle(forWritingTo: fileURL)
                try handle?.write(contentsOf: header)
            } catch { failure = error }
        }
    }

    func append(_ data: Data) {
        queue.async { [self] in
            guard failure == nil else { return }
            do { try handle?.write(contentsOf: data) }
            catch { failure = error }
        }
    }

    func finish(_ summary: Data) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                do {
                    if let failure { throw failure }
                    try handle?.write(contentsOf: summary)
                    try handle?.synchronize()
                    try handle?.close()
                    handle = nil
                    continuation.resume()
                } catch {
                    try? handle?.close()
                    handle = nil
                    continuation.resume(throwing: error)
                }
            }
        }
    }
}
