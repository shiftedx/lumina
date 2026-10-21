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
