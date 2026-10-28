import CoreGraphics
import Foundation

/// A private, process-local cache of decoded artwork and compressed renditions for library returns.
///
/// Keys intentionally include every URL component. Lumina's signed image URLs carry the
/// current member credential and artwork tag, so a new session or refreshed rendition cannot
/// read a prior member's decoded artwork. Successful compressed renditions also have a bounded,
/// hashed disk tier so process-local decoded cache eviction does not require another download.
@MainActor
public final class ArtworkImageCache {
    public static let shared = ArtworkImageCache()

    public static let defaultDecodedByteLimit = 128 * 1024 * 1024
    public static let defaultImageCountLimit = 256
    public static let defaultEncodedByteLimit = 32 * 1024 * 1024

    private final class CachedImage: NSObject {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    private typealias PreparedImage = (image: CGImage, encoded: Data)

    private final class InFlight {
        let identifier = UUID()
        let generation: Int
        let priority: TaskPriority
        var task: Task<PreparedImage?, Never>?
        var consumers = [UUID: CheckedContinuation<CGImage?, Never>]()

        init(generation: Int, priority: TaskPriority) {
            self.generation = generation
            self.priority = priority
        }
    }

    private let transport: BoundedArtworkTransport
    private let maximumDecodedBytes: Int
    private let maximumEncodedBytes: Int
    private let decode: @Sendable (Data) -> CGImage?
    private let diskCache: ArtworkDiskCache?
    private var diskGeneration: Int?
    private let images = NSCache<NSURL, CachedImage>()
    private let encodedImages = NSCache<NSURL, NSData>()
    private var inFlight = [URL: InFlight]()
    private var queuedLoads: [URL] = []
    private var activeLoads = 0
    private var generation = 0
    private var pendingPrefetch: [URL] = []
    private var prefetchTask: Task<Void, Never>?

    public init(
        session: URLSession = .shared,
        maximumDecodedBytes: Int = ArtworkImageCache.defaultDecodedByteLimit,
        imageCountLimit: Int = ArtworkImageCache.defaultImageCountLimit,
        maximumEncodedBytes: Int = ArtworkImageCache.defaultEncodedByteLimit,
        diskCache: ArtworkDiskCache? = .shared
    ) {
        self.transport = BoundedArtworkTransport(configuration: session.configuration)
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.maximumEncodedBytes = max(0, maximumEncodedBytes)
        self.diskCache = diskCache
        self.diskGeneration = diskCache?.currentGeneration
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
        decode: @escaping @Sendable (Data) -> CGImage?,
        diskCache: ArtworkDiskCache? = nil
    ) {
        self.transport = BoundedArtworkTransport(configuration: session.configuration)
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.maximumEncodedBytes = max(0, maximumEncodedBytes)
        self.diskCache = diskCache
        self.diskGeneration = diskCache?.currentGeneration
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

    /// Drops resident artwork and pending loads. Include disk data for an account change or
    /// explicit cache clear; a restored session can retain its private signed renditions.
    public func removeAll(includingDisk: Bool = true) {
        generation &+= 1
        prefetchTask?.cancel()
        prefetchTask = nil
        pendingPrefetch.removeAll()
        for entry in inFlight.values {
            entry.task?.cancel()
            entry.consumers.values.forEach { $0.resume(returning: nil) }
        }
        queuedLoads.removeAll()
        inFlight.removeAll()
        images.removeAllObjects()
        encodedImages.removeAllObjects()
        if includingDisk, let diskCache {
            diskGeneration = diskCache.invalidate()
            diskCache.scheduleRemoval()
        }
    }

    /// Removes every artwork tier and waits until the persistent cache directory is empty.
    public func clearAllArtwork() async {
        removeAll(includingDisk: false)
        guard let diskCache else { return }
        diskGeneration = diskCache.invalidate()
        await diskCache.removeAll()
    }

    /// Returns the compressed artwork currently retained on device.
    public func diskUsageBytes() async -> Int {
        await diskCache?.usageBytes() ?? 0
    }

    /// Visible consumers share one rendition. Work is cancelled when its last consumer leaves;
    /// prefetch uses the same admission limit as mounted artwork instead of a second load pool.
    public func image(for url: URL) async -> CGImage? {
        guard !Task.isCancelled else { return nil }
        if let image = cachedImage(for: url) { return image }
        let consumer = UUID()
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard !Task.isCancelled else { continuation.resume(returning: nil); return }
                let entry: InFlight
                if let existing = inFlight[url] { entry = existing }
                else {
                    entry = InFlight(generation: generation, priority: Task.currentPriority)
                    inFlight[url] = entry
                    queuedLoads.append(url)
                }
                entry.consumers[consumer] = continuation
                startQueuedLoads()
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.cancelConsumer(consumer, for: url) }
        }
    }

    private func cancelConsumer(_ consumer: UUID, for url: URL) {
        guard let entry = inFlight[url], let waiter = entry.consumers.removeValue(forKey: consumer) else { return }
        waiter.resume(returning: nil)
        guard entry.consumers.isEmpty else { return }
        entry.task?.cancel()
        inFlight[url] = nil
        queuedLoads.removeAll { $0 == url }
    }

    private func startQueuedLoads() {
        while activeLoads < 4, !queuedLoads.isEmpty {
            let url = queuedLoads.removeFirst()
            guard let entry = inFlight[url], !entry.consumers.isEmpty else { continue }
            activeLoads += 1
            let transport = transport, decode = decode
            let retained = encodedImages.object(forKey: url as NSURL).map { $0 as Data }
            let diskCache = diskCache, diskGeneration = diskGeneration
            let task = Task.detached(priority: entry.priority) { [transport, decode, diskCache, diskGeneration] () -> PreparedImage? in
                do {
                    let data: Data
                    if let retained { data = retained }
                    else if let diskCache, let diskGeneration,
                            let cached = await diskCache.data(for: url, generation: diskGeneration) {
                        guard !Task.isCancelled else { return nil }
                        if cached.count <= BillboardArtworkPreparation.maximumEncodedBytes,
                           let image = decode(cached) {
                            guard !Task.isCancelled else { return nil }
                            return (image, cached)
                        }
                        guard !Task.isCancelled else { return nil }
                        await diskCache.remove(for: url, generation: diskGeneration)
                        guard !Task.isCancelled else { return nil }
                        if url.isFileURL { data = try Data(contentsOf: url, options: .mappedIfSafe) }
                        else { data = try await transport.data(from: url, maximumBytes: BillboardArtworkPreparation.maximumEncodedBytes) }
                    }
                    else if url.isFileURL { data = try Data(contentsOf: url, options: .mappedIfSafe) }
                    else { data = try await transport.data(from: url, maximumBytes: BillboardArtworkPreparation.maximumEncodedBytes) }
                    guard !Task.isCancelled, data.count <= BillboardArtworkPreparation.maximumEncodedBytes,
                          let image = decode(data), !Task.isCancelled else { return nil }
                    return (image, data)
                } catch { return nil }
            }
            entry.task = task
            let identifier = entry.identifier, scope = entry.generation
            Task { [weak self] in
                let result = await task.value
                self?.finishLoad(for: url, identifier: identifier, generation: scope, result: result)
            }
        }
    }

    private func finishLoad(for url: URL, identifier: UUID, generation scope: Int, result: PreparedImage?) {
        // A cancelled predecessor still owns its slot until transport/decode actually finishes.
        activeLoads -= 1
        defer { startQueuedLoads() }
        guard let entry = inFlight[url], entry.identifier == identifier, generation == scope else { return }
        inFlight[url] = nil
        if let result {
            if result.encoded.count <= maximumEncodedBytes {
                encodedImages.setObject(result.encoded as NSData, forKey: url as NSURL, cost: result.encoded.count)
            }
            if let diskCache, let diskGeneration,
               result.encoded.count <= BillboardArtworkPreparation.maximumEncodedBytes {
                Task.detached { await diskCache.store(result.encoded, for: url, generation: diskGeneration) }
            }
            if let cost = decodedByteCost(of: result.image) {
                images.setObject(CachedImage(result.image), forKey: url as NSURL, cost: cost)
            }
        }
        entry.consumers.values.forEach { $0.resume(returning: result?.image) }
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
