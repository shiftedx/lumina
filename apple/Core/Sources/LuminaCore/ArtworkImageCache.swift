import CoreGraphics
import Foundation

/// A private, process-local cache for the bounded artwork rendition used by native views.
///
/// Keys intentionally include every URL component. Lumina's signed image URLs carry the
/// current member credential and artwork tag, so a new session or refreshed rendition cannot
/// read a prior member's decoded artwork. The cache never writes image data to disk.
@MainActor
public final class ArtworkImageCache {
    public static let shared = ArtworkImageCache()

    public static let defaultDecodedByteLimit = 64 * 1024 * 1024
    public static let defaultImageCountLimit = 80

    private final class CachedImage: NSObject {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    private struct InFlight {
        let identifier = UUID()
        let generation: Int
        let task: Task<CGImage?, Never>
    }

    private let session: URLSession
    private let maximumDecodedBytes: Int
    private let decode: @Sendable (Data) -> CGImage?
    private let images = NSCache<NSURL, CachedImage>()
    private var inFlight = [URL: InFlight]()
    private var generation = 0

    public init(
        session: URLSession = .shared,
        maximumDecodedBytes: Int = ArtworkImageCache.defaultDecodedByteLimit,
        imageCountLimit: Int = ArtworkImageCache.defaultImageCountLimit
    ) {
        self.session = session
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.decode = BillboardArtworkPreparation.image(from:)
        images.totalCostLimit = max(1, maximumDecodedBytes)
        images.countLimit = max(1, imageCountLimit)
    }

    init(
        session: URLSession,
        maximumDecodedBytes: Int,
        imageCountLimit: Int,
        decode: @escaping @Sendable (Data) -> CGImage?
    ) {
        self.session = session
        self.maximumDecodedBytes = max(1, maximumDecodedBytes)
        self.decode = decode
        images.totalCostLimit = max(1, maximumDecodedBytes)
        images.countLimit = max(1, imageCountLimit)
    }

    /// Returns a previously decoded rendition without scheduling network work.
    public func cachedImage(for url: URL) -> CGImage? {
        images.object(forKey: url as NSURL)?.image
    }

    /// Drops decoded and pending artwork when the signed-in member changes.
    public func removeAll() {
        generation &+= 1
        inFlight.values.forEach { $0.task.cancel() }
        inFlight.removeAll()
        images.removeAllObjects()
    }

    /// Coalesces view lifetimes that ask for the same signed rendition. A cancelled caller does
    /// not cancel a shared fetch; it simply declines to use the result in its own view task.
    public func image(for url: URL) async -> CGImage? {
        if let image = cachedImage(for: url) { return image }
        if let entry = inFlight[url] {
            let image = await entry.task.value
            return generation == entry.generation ? image : nil
        }

        let session = session
        let decode = decode
        let task = Task.detached(priority: .userInitiated) { [session, decode] () -> CGImage? in
            do {
                let data: Data
                if url.isFileURL {
                    data = try Data(contentsOf: url, options: .mappedIfSafe)
                } else {
                    let (loaded, response) = try await session.data(from: url)
                    guard let http = response as? HTTPURLResponse,
                          (200..<300).contains(http.statusCode)
                    else { return nil }
                    data = loaded
                }
                guard !Task.isCancelled,
                      data.count <= BillboardArtworkPreparation.maximumEncodedBytes
                else { return nil }
                return decode(data)
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
        if let image, let cost = decodedByteCost(of: image) {
            images.setObject(CachedImage(image), forKey: url as NSURL, cost: cost)
        }
        return image
    }

    /// NSCache uses this as an eviction target rather than a hard memory limit. Oversized images
    /// remain usable by their current view but are not retained for later Home returns.
    private func decodedByteCost(of image: CGImage) -> Int? {
        let (bytes, overflow) = image.bytesPerRow.multipliedReportingOverflow(by: image.height)
        guard !overflow, bytes > 0, bytes <= maximumDecodedBytes else { return nil }
        return bytes
    }
}
