import CoreGraphics
import Foundation

/// A session-local cache of decoded book-cover thumbnails.
public actor BookCoverCache {
    private final class Entry {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    private final class Pending: @unchecked Sendable {
        struct Consumer {
            let token: ConsumerToken
            let continuation: CheckedContinuation<CGImage?, Never>
        }

        let task: Task<CGImage?, Never>
        var consumers = [UUID: Consumer]()

        init(task: Task<CGImage?, Never>) {
            self.task = task
        }
    }

    private final class ConsumerToken: @unchecked Sendable {
        let id = UUID()
        private let lock = NSLock()
        private var cancelled = false

        var isCancelled: Bool { lock.withLock { cancelled } }
        func cancel() { lock.withLock { cancelled = true } }
    }

    private let api: LuminaBooks
    private let cache = NSCache<NSString, Entry>()
    private var pending = [String: Pending]()

    public init(api: LuminaBooks) {
        self.api = api
        cache.countLimit = 32
        cache.totalCostLimit = 24 * 1024 * 1024
    }

    public func image(_ id: String) async -> CGImage? {
        let token = ConsumerToken()
        return await withTaskCancellationHandler {
            guard !Task.isCancelled, !token.isCancelled else { return nil }
            if let image = cache.object(forKey: id as NSString)?.image {
                return Task.isCancelled || token.isCancelled ? nil : image
            }

            let image = await withCheckedContinuation { (continuation: CheckedContinuation<CGImage?, Never>) in
                guard !Task.isCancelled, !token.isCancelled else {
                    continuation.resume(returning: nil)
                    return
                }

                let request: Pending
                if let existing = pending[id] {
                    removeCancelledConsumers(from: existing)
                    if existing.consumers.isEmpty {
                        pending[id] = nil
                        existing.task.cancel()
                        guard !Task.isCancelled, !token.isCancelled else {
                            continuation.resume(returning: nil)
                            return
                        }
                        request = createRequest(id: id)
                    } else {
                        request = existing
                    }
                } else {
                    request = createRequest(id: id)
                }
                request.consumers[token.id] = Pending.Consumer(token: token, continuation: continuation)
            }
            return Task.isCancelled || token.isCancelled ? nil : image
        } onCancel: {
            // Mark it before hopping to the actor so a racing completion cannot cache abandoned work.
            token.cancel()
            Task { await self.cancel(id: id, token: token) }
        }
    }

    private func createRequest(id: String) -> Pending {
        let api = api
        let task = Task<CGImage?, Never> {
            do {
                let data = try await api.cover(id: id)
                guard !Task.isCancelled else { return nil }
                return BookPagePreparation.image(from: data, maximumPixelSize: 600)
            } catch {
                return nil
            }
        }
        let request = Pending(task: task)
        pending[id] = request
        Task { [weak self, request] in
            let image = await request.task.value
            await self?.finish(id: id, request: request, image: image)
        }
        return request
    }

    private func cancel(id: String, token: ConsumerToken) {
        guard let request = pending[id], let consumer = request.consumers.removeValue(forKey: token.id) else { return }
        consumer.continuation.resume(returning: nil)
        guard request.consumers.isEmpty else { return }
        pending[id] = nil
        request.task.cancel()
    }

    private func removeCancelledConsumers(from request: Pending) {
        let cancelled = request.consumers.filter { $0.value.token.isCancelled }
        for (id, consumer) in cancelled {
            request.consumers[id] = nil
            consumer.continuation.resume(returning: nil)
        }
    }

    private func finish(id: String, request: Pending, image: CGImage?) {
        // A replacement request may already own this key when an old task resumes.
        guard pending[id] === request else { return }
        removeCancelledConsumers(from: request)
        guard !request.consumers.isEmpty else {
            pending[id] = nil
            request.task.cancel()
            return
        }
        pending[id] = nil
        if let image {
            cache.setObject(Entry(image), forKey: id as NSString, cost: image.bytesPerRow * image.height)
        }
        let consumers = request.consumers.values
        request.consumers.removeAll()
        consumers.forEach { $0.continuation.resume(returning: image) }
    }
}
