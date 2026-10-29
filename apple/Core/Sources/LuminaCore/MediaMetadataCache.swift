import CryptoKit
import Foundation

/// Small, disposable member-scoped snapshots. The shared disk byte store provides hashed names,
/// LRU pruning, file protection, off-main I/O and generation-safe clearing in a separate budget.
@MainActor
final class MediaMetadataCache {
    private struct Snapshot: Codable, Sendable {
        let version: Int
        let expires: Date
        let items: [MediaItem]
    }

    private let disk: ArtworkDiskCache
    private let memory = NSCache<NSURL, NSData>()
    private var generation: Int
    private let now: @Sendable () -> Date
    private static let recordLimit = 1024 * 1024

    init(directory: URL? = nil, maximumBytes: Int = 32 * 1024 * 1024,
         now: @escaping @Sendable () -> Date = { Date() }) {
        let directory = directory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("LuminaMetadata", isDirectory: true)
        disk = ArtworkDiskCache(directory: directory, maximumBytes: maximumBytes)
        generation = disk.currentGeneration
        memory.totalCostLimit = min(maximumBytes, 8 * 1024 * 1024)
        memory.countLimit = 128
        self.now = now
    }

    static func key(_ resource: String, session: LuminaSession) -> URL {
        let scope = [session.serverURL.absoluteString, session.userID, session.accessToken, resource]
        let digest = SHA256.hash(data: Data(scope.joined(separator: "\u{0}").utf8))
            .map { String(format: "%02x", $0) }.joined()
        return URL(string: "https://metadata.invalid/\(digest)")!
    }

    func items(for key: URL) async -> [MediaItem]? {
        let owner = generation
        let resident = memory.object(forKey: key as NSURL).map { $0 as Data }
        let data: Data?
        if let resident { data = resident }
        else { data = await disk.data(for: key, generation: owner) }
        guard let data, data.count <= Self.recordLimit else { return nil }
        let snapshot = await Task.detached(priority: .userInitiated) {
            try? JSONDecoder().decode(Snapshot.self, from: data)
        }.value
        guard owner == generation, let snapshot, snapshot.version == 1,
              snapshot.expires > now(), snapshot.items.count <= 2_000 else { return nil }
        memory.setObject(data as NSData, forKey: key as NSURL, cost: data.count)
        return snapshot.items
    }

    func store(_ items: [MediaItem], for key: URL, lifetime: TimeInterval = 120) async {
        guard items.count <= 2_000 else { return }
        let owner = generation
        let snapshot = Snapshot(version: 1, expires: now().addingTimeInterval(lifetime), items: items)
        let data = await Task.detached(priority: .utility) { try? JSONEncoder().encode(snapshot) }.value
        guard owner == generation, let data, data.count <= Self.recordLimit else { return }
        memory.setObject(data as NSData, forKey: key as NSURL, cost: data.count)
        await disk.store(data, for: key, generation: owner)
    }

    func clear() {
        memory.removeAllObjects()
        generation = disk.invalidate()
        disk.scheduleRemoval()
    }
}
