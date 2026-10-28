import CryptoKit
import Foundation

/// A bounded, private cache for compressed artwork. File names are hashes of complete rendition
/// URLs so signed URL credentials never reach disk as path names or cache metadata.
public final class ArtworkDiskCache: @unchecked Sendable {
    public static let defaultByteLimit = 2 * 1024 * 1024 * 1024
    public static let shared = ArtworkDiskCache()

    private struct Entry {
        var size: Int
        var modified: Date
    }

    private let directory: URL
    private let maximumBytes: Int
    private let queue = DispatchQueue(label: "com.lumina.artwork-disk-cache", qos: .utility)
    private let stateLock = NSLock()
    private var generation = 0
    private var indexLoaded = false
    private var entries = [URL: Entry]() // Accessed only on queue.
    private var totalBytes = 0 // Accessed only on queue.

    public init(directory: URL? = nil, maximumBytes: Int = ArtworkDiskCache.defaultByteLimit) {
        let cachesDirectory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        self.directory = directory ?? cachesDirectory.appendingPathComponent("LuminaArtwork", isDirectory: true)
        self.maximumBytes = max(0, maximumBytes)
    }

    /// A synchronous invalidation gate. Existing reads and writes check this token before using
    /// disk, so callers can make a security-sensitive clear take effect before deletion finishes.
    func invalidate() -> Int {
        stateLock.withLock {
            generation &+= 1
            return generation
        }
    }

    var currentGeneration: Int { stateLock.withLock { generation } }

    func data(for url: URL, generation: Int) async -> Data? {
        guard isCurrent(generation) else { return nil }
        return await withCheckedContinuation { continuation in
            queue.async { [weak self] in
                guard let self, self.isCurrent(generation) else {
                    continuation.resume(returning: nil)
                    return
                }
                let manager = FileManager.default
                self.loadIndex(using: manager)
                let file = self.fileURL(for: url)
                guard let data = try? Data(contentsOf: file, options: .mappedIfSafe) else {
                    self.removeEntry(for: file)
                    continuation.resume(returning: nil)
                    return
                }
                guard self.isCurrent(generation) else {
                    continuation.resume(returning: nil)
                    return
                }
                let now = Date()
                try? manager.setAttributes([.modificationDate: now], ofItemAtPath: file.path)
                if var entry = self.entries[file] {
                    entry.modified = now
                    self.entries[file] = entry
                }
                continuation.resume(returning: data)
            }
        }
    }

    func store(_ data: Data, for url: URL, generation: Int) async {
        guard !data.isEmpty, data.count <= maximumBytes, isCurrent(generation) else { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [weak self] in
                defer { continuation.resume() }
                guard let self, self.isCurrent(generation) else { return }
                let manager = FileManager.default
                self.loadIndex(using: manager)
                guard self.isCurrent(generation) else { return }
                let file = self.fileURL(for: url)
                do {
                    try data.write(to: file, options: .atomic)
                    self.applyFileProtection(to: file, using: manager)
                    guard self.isCurrent(generation) else { return }
                    if let previous = self.entries[file] { self.totalBytes -= previous.size }
                    self.entries[file] = Entry(size: data.count, modified: Date())
                    self.totalBytes += data.count
                    self.prune(using: manager)
                } catch {
                    // Caches are best effort. Failed writes must never affect artwork delivery.
                }
            }
        }
    }

    func remove(for url: URL, generation: Int) async {
        guard isCurrent(generation) else { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [weak self] in
                defer { continuation.resume() }
                guard let self, self.isCurrent(generation) else { return }
                let manager = FileManager.default
                self.loadIndex(using: manager)
                let file = self.fileURL(for: url)
                try? manager.removeItem(at: file)
                self.removeEntry(for: file)
            }
        }
    }

    /// Queues deletion immediately, so subsequent reads on this cache's serial queue cannot see
    /// data from the generation that was invalidated by the caller.
    func scheduleRemoval() {
        queue.async { [weak self] in self?.removeAllOnQueue() }
    }

    func removeAll() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [weak self] in
                defer { continuation.resume() }
                guard let self else { return }
                self.removeAllOnQueue()
            }
        }
    }

    public func usageBytes() async -> Int {
        await withCheckedContinuation { (continuation: CheckedContinuation<Int, Never>) in
            queue.async { [weak self] in
                guard let self else { continuation.resume(returning: 0); return }
                self.loadIndex(using: FileManager.default)
                continuation.resume(returning: self.totalBytes)
            }
        }
    }

    private func isCurrent(_ expectedGeneration: Int) -> Bool {
        stateLock.withLock { generation == expectedGeneration }
    }

    private func fileURL(for url: URL) -> URL {
        let digest = SHA256.hash(data: Data(url.absoluteString.utf8))
        let name = digest.map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent(name).appendingPathExtension("artwork")
    }

    private func loadIndex(using manager: FileManager) {
        guard !indexLoaded else { return }
        indexLoaded = true
        entries.removeAll()
        totalBytes = 0
        prepareDirectory(using: manager)
        let keys: [URLResourceKey] = [.fileSizeKey, .contentModificationDateKey, .isRegularFileKey]
        guard let files = try? manager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]
        ) else { return }
        for file in files where file.pathExtension == "artwork" {
            guard let values = try? file.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true,
                  let size = values.fileSize
            else { continue }
            entries[file] = Entry(size: size, modified: values.contentModificationDate ?? .distantPast)
            totalBytes += size
        }
        prune(using: manager)
    }

    private func prepareDirectory(using manager: FileManager) {
        try? manager.createDirectory(at: directory, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutableDirectory = directory
        try? mutableDirectory.setResourceValues(values)
    }

    private func removeAllOnQueue() {
        let manager = FileManager.default
        try? manager.removeItem(at: directory)
        entries.removeAll()
        totalBytes = 0
        indexLoaded = true
        prepareDirectory(using: manager)
    }

    private func applyFileProtection(to file: URL, using manager: FileManager) {
        #if os(iOS) || os(tvOS)
        try? manager.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: file.path
        )
        #endif
    }

    private func removeEntry(for file: URL) {
        if let entry = entries.removeValue(forKey: file) { totalBytes -= entry.size }
    }

    private func prune(using manager: FileManager) {
        guard maximumBytes > 0 else {
            for file in entries.keys { try? manager.removeItem(at: file) }
            entries.removeAll()
            totalBytes = 0
            return
        }
        guard totalBytes > maximumBytes else { return }
        for (file, entry) in entries.sorted(by: { $0.value.modified < $1.value.modified }) where totalBytes > maximumBytes {
            if (try? manager.removeItem(at: file)) != nil {
                entries.removeValue(forKey: file)
                totalBytes -= entry.size
            }
        }
    }
}
