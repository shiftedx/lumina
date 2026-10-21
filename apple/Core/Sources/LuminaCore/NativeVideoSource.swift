import AVFoundation
import Foundation
@preconcurrency import PrismCore

/// Owns on-demand, stream-copy MKV packaging; AVPlayer still owns decoding and transport.
@MainActor
public final class NativeVideoSource {
    private var demux: PrismCoreSession?
    private var statusObservation: NSKeyValueObservation?

    public init() {}

    public func prepare(url: URL, usesClientDemux: Bool) async throws -> AVPlayerItem {
        stop()
        guard usesClientDemux else { return AVPlayerItem(url: url) }
        let session = try PrismCoreSession(url: url, display: .current(),
                                          segmentCacheBytes: 64 << 20, forceMuxedShape: true, coordinatedHTTP: true)
        demux = session
        do {
            let playlist = try await session.start()
            try Task.checkCancellation()
            guard demux === session else { throw CancellationError() }
            let item = AVPlayerItem(url: playlist)
            item.preferredForwardBufferDuration = 4
            statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
                guard item.status == .failed else { return }
                Task { @MainActor [weak self] in
                    guard self?.demux === session else { return }
                    self?.stop()
                }
            }
            return item
        } catch {
            if demux === session { demux = nil }
            await session.stop()
            throw error
        }
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
        demux = nil
        statusObservation = nil
        return Task { if let session { await session.stop() } }
    }

    isolated deinit {
        let session = demux
        if let session { Task { await session.stop() } }
    }
}
