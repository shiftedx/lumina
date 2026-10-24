import CoreGraphics
import Foundation

/// A private, process-local cache of decoded artwork and compressed renditions for library returns.
///
/// Keys intentionally include every URL component. Lumina's signed image URLs carry the
/// current member credential and artwork tag, so a new session or refreshed rendition cannot
/// read a prior member's decoded artwork. The cache never writes image data to disk.
@MainActor
public final class ArtworkImageCache {
    public static let shared = ArtworkImageCache()

    public static let defaultDecodedByteLimit = 64 * 1024 * 1024
    public static let defaultImageCountLimit = 80
    public static let defaultEncodedByteLimit = 32 * 1024 * 1024

    private final class CachedImage: NSObject {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    private struct InFlight {
        let identifier = UUID()
        let generation: Int
        let task: Task<(image: CGImage, encoded: Data)?, Never>
    }

    private let transport: BoundedArtworkTransport
    private let maximumDecodedBytes: Int
    private let maximumEncodedBytes: Int
    private let decode: @Sendable (Data) -> CGImage?
    private let images = NSCache<NSURL, CachedImage>()
    private let encodedImages = NSCache<NSURL, NSData>()
    private var inFlight = [URL: InFlight]()
    private var generation = 0
    private var pendingPrefetch: [URL] = []
    private var prefetchTask: Task<Void, Never>?

    public init(
        session: URLSession = .shared,
        maximumDecodedBytes: Int = ArtworkImageCache.defaultDecodedByteLimit,
        imageCountLimit: Int = ArtworkImageCache.defaultImageCountLimit,
        maximumEncodedBytes: Int = ArtworkImageCache.defaultEncodedByteLimit
    ) {
        self.transport = BoundedArtworkTransport(configuration: session.configuration)
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.maximumEncodedBytes = max(0, maximumEncodedBytes)
        encodedImages.totalCostLimit = max(1, maximumEncodedBytes)
        encodedImages.countLimit = 2_000
        self.decode = BillboardArtworkPreparation.image(from:)
        images.totalCostLimit = max(1, maximumDecodedBytes)
        images.countLimit = max(1, imageCountLimit)
    }

    init(
        session: URLSession,
        maximumDecodedBytes: Int,
        imageCountLimit: Int,
        maximumEncodedBytes: Int = ArtworkImageCache.defaultEncodedByteLimit,
        decode: @escaping @Sendable (Data) -> CGImage?
    ) {
        self.transport = BoundedArtworkTransport(configuration: session.configuration)
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.maximumEncodedBytes = max(0, maximumEncodedBytes)
        encodedImages.totalCostLimit = max(1, maximumEncodedBytes)
        encodedImages.countLimit = 2_000
        self.decode = decode
        images.totalCostLimit = max(1, maximumDecodedBytes)
        images.countLimit = max(1, imageCountLimit)
    }

    /// Returns a previously decoded rendition without scheduling network work.
    public func cachedImage(for url: URL) -> CGImage? {
        images.object(forKey: url as NSURL)?.image
    }

    /// Drops both resident tiers and pending artwork when the signed-in member changes.
    public func removeAll() {
        generation &+= 1
        prefetchTask?.cancel()
        prefetchTask = nil
        pendingPrefetch.removeAll()
        inFlight.values.forEach { $0.task.cancel() }
        inFlight.removeAll()
        images.removeAllObjects()
        encodedImages.removeAllObjects()
    }

    /// Coalesces view lifetimes that ask for the same signed rendition. A cancelled caller does
    /// not cancel a shared fetch; it simply declines to use the result in its own view task.
    public func image(for url: URL) async -> CGImage? {
        if let image = cachedImage(for: url) { return image }
        if let entry = inFlight[url] {
            let image = await entry.task.value
            return generation == entry.generation ? image?.image : nil
        }

        let transport = transport
        let decode = decode
        let retained = encodedImages.object(forKey: url as NSURL).map { $0 as Data }
        let task = Task.detached(priority: Task.currentPriority) { [transport, decode] () -> (image: CGImage, encoded: Data)? in
            do {
                let data: Data
                if let retained {
                    data = retained
                } else if url.isFileURL {
                    data = try Data(contentsOf: url, options: .mappedIfSafe)
                } else {
                    data = try await transport.data(from: url, maximumBytes: BillboardArtworkPreparation.maximumEncodedBytes)
                }
                guard !Task.isCancelled,
                      data.count <= BillboardArtworkPreparation.maximumEncodedBytes
                else { return nil }
                guard let image = decode(data) else { return nil }
                return (image, data)
            } catch {
                return nil
            }
        }
        let entry = InFlight(generation: generation, task: task)
        inFlight[url] = entry
        let image = await task.value
        guard inFlight[url]?.identifier == entry.identifier else { return nil }
        inFlight[url] = nil
        guard generation == entry.generation else { return nil }
        if let image {
            if image.encoded.count <= maximumEncodedBytes {
                encodedImages.setObject(image.encoded as NSData, forKey: url as NSURL, cost: image.encoded.count)
            }
            if let cost = decodedByteCost(of: image.image) {
                images.setObject(CachedImage(image.image), forKey: url as NSURL, cost: cost)
            }
        }
        return image?.image
    }

    /// Replace stale lookahead with the latest window. A single worker keeps speculative
    /// loading at four requests even when scrolling rapidly replaces many view tasks.
    public func prefetch(_ urls: [URL]) async {
        guard !Task.isCancelled else { return }
        var seen = Set<URL>()
        pendingPrefetch = urls.prefix(16).filter { seen.insert($0).inserted && cachedImage(for: $0) == nil }
        if let prefetchTask {
            await prefetchTask.value
            return
        }
        let scope = generation
        let worker = Task(priority: .utility) {
            defer { if self.generation == scope { self.prefetchTask = nil } }
            while !Task.isCancelled, self.generation == scope, !self.pendingPrefetch.isEmpty {
                let batch = Array(self.pendingPrefetch.prefix(4))
                self.pendingPrefetch.removeFirst(batch.count)
                await withTaskGroup(of: Void.self) { group in
                    for url in batch {
                        group.addTask(priority: .utility) { _ = await self.image(for: url) }
                    }
                }
            }
        }
        prefetchTask = worker
        await worker.value
    }

    /// NSCache uses this as an eviction target rather than a hard memory limit. Oversized images
    /// remain usable by their current view but are not retained for later Home returns.
    private func decodedByteCost(of image: CGImage) -> Int? {
        let (bytes, overflow) = image.bytesPerRow.multipliedReportingOverflow(by: image.height)
        guard !overflow, bytes > 0, bytes <= maximumDecodedBytes else { return nil }
        return bytes
    }
}

/// A cache-owned connection pool that receives private artwork incrementally and never uses
/// URLCache. Its delegate owns only bounded request state, avoiding a session/delegate cycle.
private final class BoundedArtworkTransport: @unchecked Sendable {
    private let delegate: Delegate
    private let session: URLSession

    init(configuration: URLSessionConfiguration) {
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        let delegate = Delegate()
        self.delegate = delegate
        self.session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    deinit {
        session.invalidateAndCancel()
    }

    func data(from url: URL, maximumBytes: Int) async throws -> Data {
        let reference = RequestReference()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let task = session.dataTask(with: URLRequest(url: url))
                delegate.register(task, maximumBytes: max(0, maximumBytes), continuation: continuation)
                guard reference.install(task: task, delegate: delegate), !Task.isCancelled else {
                    delegate.cancel(task)
                    return
                }
                task.resume()
            }
        } onCancel: {
            reference.cancel()
        }
    }

    private final class RequestReference: @unchecked Sendable {
        private let lock = NSLock()
        private weak var task: URLSessionDataTask?
        private weak var delegate: Delegate?
        private var isCancelled = false

        func install(task: URLSessionDataTask, delegate: Delegate) -> Bool {
            lock.withLock {
                guard !isCancelled else { return false }
                self.task = task
                self.delegate = delegate
                return true
            }
        }

        func cancel() {
            let target = lock.withLock { () -> (URLSessionDataTask, Delegate)? in
                isCancelled = true
                guard let task, let delegate else { return nil }
                return (task, delegate)
            }
            if let (task, delegate) = target {
                delegate.cancel(task)
            }
        }
    }

    private final class Delegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
        private enum Failure: Error {
            case invalidResponse
            case responseTooLarge
        }

        private final class Pending {
            let maximumBytes: Int
            let continuation: CheckedContinuation<Data, Error>
            var data = Data()
            var acceptedResponse = false

            init(maximumBytes: Int, continuation: CheckedContinuation<Data, Error>) {
                self.maximumBytes = maximumBytes
                self.continuation = continuation
            }
        }

        private let lock = NSLock()
        private var pending = [Int: Pending]()

        func register(
            _ task: URLSessionDataTask,
            maximumBytes: Int,
            continuation: CheckedContinuation<Data, Error>
        ) {
            lock.withLock {
                pending[task.taskIdentifier] = Pending(maximumBytes: maximumBytes, continuation: continuation)
            }
        }

        func cancel(_ task: URLSessionDataTask) {
            reject(task, with: CancellationError())
        }

        func urlSession(
            _ session: URLSession,
            dataTask: URLSessionDataTask,
            didReceive response: URLResponse,
            completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void
        ) {
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode)
            else {
                reject(dataTask, with: Failure.invalidResponse)
                completionHandler(.cancel)
                return
            }

            let accepted = lock.withLock {
                guard let state = pending[dataTask.taskIdentifier] else { return false }
                let expected = response.expectedContentLength
                guard expected < 0 || expected <= Int64(state.maximumBytes) else { return false }
                state.acceptedResponse = true
                if expected > 0 { state.data.reserveCapacity(Int(expected)) }
                return true
            }
            guard accepted else {
                reject(dataTask, with: Failure.responseTooLarge)
                completionHandler(.cancel)
                return
            }
            completionHandler(.allow)
        }

        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            let oversized = lock.withLock {
                guard let state = pending[dataTask.taskIdentifier] else { return false }
                let (nextCount, overflow) = state.data.count.addingReportingOverflow(data.count)
                guard !overflow, nextCount <= state.maximumBytes else { return true }
                state.data.append(data)
                return false
            }
            if oversized {
                reject(dataTask, with: Failure.responseTooLarge)
            }
        }

        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            let state = lock.withLock { pending.removeValue(forKey: task.taskIdentifier) }
            guard let state else { return }
            if let error {
                state.continuation.resume(throwing: error)
            } else if state.acceptedResponse {
                state.continuation.resume(returning: state.data)
            } else {
                state.continuation.resume(throwing: Failure.invalidResponse)
            }
        }

        private func reject(_ task: URLSessionDataTask, with error: Error) {
            let state = lock.withLock { pending.removeValue(forKey: task.taskIdentifier) }
            task.cancel()
            state?.continuation.resume(throwing: error)
        }
    }
}
