import CryptoKit
import Foundation
import Observation

/// Member-owned downloads. Requests and their credentials exist only while downloading.
@MainActor @Observable public final class LuminaOfflineLibrary {
    public enum DownloadState: Equatable, Sendable {
        case queued
        case downloading(receivedBytes: Int64, totalBytes: Int64?)
        case downloaded
        case failed(String)
        case cancelled
    }

    public nonisolated static let maximumTrackBytes: Int64 = 128 * 1_024 * 1_024
    /// Direct-playable films and episodes only. HLS and converted streams are intentionally never persisted.
    public nonisolated static let maximumVideoBytes: Int64 = 32 * 1_024 * 1_024 * 1_024
    public nonisolated static let maximumArtworkBytes: Int64 = 2 * 1_024 * 1_024

    /// Downloading is limited to audio and concrete playable video files. Collections, playlists and HLS manifests
    /// cannot become durable single-file playback entries.
    public nonisolated static func supportsDownload(_ item: MediaItem) -> Bool {
        item.isAudio || supportsVideoDownload(item)
    }

    public nonisolated static func supportsVideoDownload(_ item: MediaItem) -> Bool {
        item.isPlayable && !item.isAudio
    }
    public let limitBytes: Int64
    public private(set) var usedBytes: Int64 = 0
    public private(set) var downloadStates = [String: DownloadState]()
    public private(set) var errorMessage: String?
    /// Metadata snapshot reconciled on refresh; opening media still revalidates the actual file.
    public var downloadedItems: [MediaItem] { entries.map(\.item) }
    public var pendingItems: [MediaItem] { knownItems.filter { downloadStates[$0.id] != .downloaded } }

    private struct Entry: Codable {
        let item: MediaItem
        let filename: String
        let bytes: Int64
        var artworkFilename: String? = nil
        /// Stored separately so older manifests remain decodable and no credentials or server state are retained.
        var resumeSeconds: Double? = nil
    }
    private struct Job {
        let key: UUID
        let owner: String
        let generation: UUID
        let item: MediaItem
        let artwork: Bool
        let request: URLRequest
        let origin: URL
        let continuation: CheckedContinuation<Void, Never>
    }

    private var entries = [Entry]()
    private var knownItems = [MediaItem]()
    @ObservationIgnored private let root: URL
    @ObservationIgnored private let configuration: URLSessionConfiguration
    @ObservationIgnored private var owner: String?
    @ObservationIgnored private var origin: URL?
    @ObservationIgnored private var accessToken: String?
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var jobs = [Job]()
    @ObservationIgnored private var active: Job?
    @ObservationIgnored private var transfer: OfflineTransfer?
    @ObservationIgnored private var cancelled = Set<UUID>()

    public init(root: URL? = nil, limitBytes: Int64 = 64 * 1_024 * 1_024 * 1_024,
                configuration: URLSessionConfiguration = .ephemeral) {
        #if os(tvOS)
        let directory: FileManager.SearchPathDirectory = .cachesDirectory
        #else
        let directory: FileManager.SearchPathDirectory = .applicationSupportDirectory
        #endif
        self.root = root ?? FileManager.default.urls(for: directory, in: .userDomainMask)[0]
            .appendingPathComponent("LuminaOffline", isDirectory: true)
        self.limitBytes = max(0, limitBytes)
        let config = configuration.copy() as! URLSessionConfiguration
        config.urlCache = nil
        config.urlCredentialStorage = nil
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.configuration = config
    }

    /// A retained authenticated session can open its downloads without contacting the server.
    public func configure(session: LuminaSession?) {
        let nextOwner = session.map(Self.ownerKey)
        accessToken = session?.accessToken
        guard nextOwner != owner else { refresh(); return }
        cancelAll()
        generation = UUID()
        owner = nextOwner
        origin = session?.serverURL
        entries = []
        knownItems = []
        downloadStates = [:]
        usedBytes = 0
        errorMessage = nil
        guard let directory = ownerDirectory else { return }
        do {
            try prepare(directory)
            let manifest = directory.appendingPathComponent("manifest.json")
            if FileManager.default.fileExists(atPath: manifest.path) {
                let facts = try manifest.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
                let size = facts.fileSize ?? 0
                guard facts.isRegularFile == true, facts.isSymbolicLink != true, size <= 8 * 1_024 * 1_024 else { throw OfflineFailure.storage }
                entries = try JSONDecoder().decode([Entry].self, from: Data(contentsOf: manifest))
                knownItems = entries.map(\.item)
            }
            // Interrupted transfers are never presented as playable downloads.
            for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
                where file.lastPathComponent.hasPrefix(".partial-") {
                try? FileManager.default.removeItem(at: file)
            }
            refresh()
        } catch {
            entries = []
            knownItems = []
            errorMessage = "Saved downloads could not be read. Clear downloads to recover storage."
            updateUsedBytes()
        }
    }

    /// Downloads are sequential; failures remain visible and another call retries the item.
    public func download(item: MediaItem, request: URLRequest) async {
        await enqueue(item: item, request: request, artwork: false)
    }

    public func downloadVideo(item: MediaItem, request: URLRequest) async {
        guard Self.supportsVideoDownload(item) else {
            downloadStates[item.id] = .failed("Only playable movies and episodes can be downloaded to this device.")
            return
        }
        await enqueue(item: item, request: request, artwork: false)
    }

    public func downloadArtwork(item: MediaItem, request: URLRequest) async {
        guard localURL(for: item.id) != nil, artworkURL(for: item.id) == nil else { return }
        await enqueue(item: item, request: request, artwork: true)
    }

    public func artworkURL(for id: String) -> URL? {
        guard let entry = entries.first(where: { $0.item.id == id }) else { return nil }
        return existingArtworkURL(entry)
    }

    private func enqueue(item: MediaItem, request: URLRequest, artwork: Bool) async {
        guard Self.supportsDownload(item), let owner, let origin,
              let url = request.url, offlineSameOrigin(origin, url), url.user == nil, url.password == nil,
              hasCurrentCredentials(request), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
            if !artwork { downloadStates[item.id] = .failed("Sign in to the same server before downloading this media.") }
            return
        }
        if !artwork, !knownItems.contains(where: { $0.id == item.id }) { knownItems.append(item) }
        if !artwork, localURL(for: item.id) != nil { downloadStates[item.id] = .downloaded; return }
        guard !jobs.contains(where: { $0.owner == owner && $0.item.id == item.id && $0.artwork == artwork }),
              !(active?.owner == owner && active?.generation == generation && active?.item.id == item.id && active?.artwork == artwork) else { return }
        let key = UUID()
        await withTaskCancellationHandler {
            guard !Task.isCancelled else { if !artwork { downloadStates[item.id] = .cancelled }; return }
            await withCheckedContinuation { continuation in
                jobs.append(Job(key: key, owner: owner, generation: generation, item: item, artwork: artwork,
                                request: request, origin: origin, continuation: continuation))
                if !artwork { downloadStates[item.id] = .queued }
                startNext()
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.cancel(key: key) }
        }
    }

    public func cancel(id: String) {
        if let job = active, job.owner == owner, job.item.id == id { cancel(key: job.key) }
        for job in jobs.filter({ $0.owner == owner && $0.item.id == id }) { cancel(key: job.key) }
    }

    public func remove(id: String) {
        cancel(id: id)
        if let entry = entries.first(where: { $0.item.id == id }), let url = safeURL(entry.filename) {
            do { try FileManager.default.removeItem(at: url) }
            catch where !FileManager.default.fileExists(atPath: url.path) { }
            catch { errorMessage = "The saved track could not be removed."; return }
        }
        if let filename = entries.first(where: { $0.item.id == id })?.artworkFilename, let url = safeURL(filename) {
            try? FileManager.default.removeItem(at: url)
        }
        entries.removeAll { $0.item.id == id }
        knownItems.removeAll { $0.id == id }
        downloadStates.removeValue(forKey: id)
        saveOrReport()
        updateUsedBytes()
    }

    public func clear() {
        cancelAll()
        generation = UUID()
        guard let directory = ownerDirectory else { return }
        do {
            if FileManager.default.fileExists(atPath: directory.path) { try FileManager.default.removeItem(at: directory) }
            entries = []
            knownItems = []
            downloadStates = [:]
            errorMessage = nil
            usedBytes = 0
        } catch { errorMessage = "Downloads could not be cleared."; updateUsedBytes() }
    }

    /// Explicit sign-out cleanup for this member, including when another member is configured.
    public func clear(session: LuminaSession) {
        let key = Self.ownerKey(session)
        if key == owner { clear(); return }
        let directory = root.appendingPathComponent(key, isDirectory: true)
        do { if FileManager.default.fileExists(atPath: directory.path) { try FileManager.default.removeItem(at: directory) } }
        catch { errorMessage = "This member's downloads could not be cleared." }
    }

    public func localURL(for id: String) -> URL? {
        guard let entry = entries.first(where: { $0.item.id == id }) else { return nil }
        guard let url = existingURL(entry) else { refresh(); return nil }
        return url
    }

    public func resumeSeconds(for id: String) -> Double {
        guard let entry = entries.first(where: { $0.item.id == id }) else { return 0 }
        let seconds = entry.resumeSeconds ?? entry.item.resumeSeconds
        guard seconds.isFinite, seconds >= 0 else { return 0 }
        return seconds
    }

    /// Offline positions are durable locally and may be reconciled with the server when connectivity returns.
    public func recordPlaybackPosition(id: String, seconds: Double) {
        guard seconds.isFinite, seconds >= 0,
              let index = entries.firstIndex(where: { $0.item.id == id }) else { return }
        entries[index].resumeSeconds = seconds
        saveOrReport()
    }

    /// tvOS may purge caches at any time; callers can reconcile when opening the download list.
    public func refresh() {
        let missingEntries = entries.filter { existingURL($0) == nil }
        let missing = missingEntries.map(\.item.id)
        // The system already purged these audio files; remove their now-unaddressable artwork.
        for entry in missingEntries {
            if let filename = entry.artworkFilename, let url = safeURL(filename) { try? FileManager.default.removeItem(at: url) }
        }
        entries.removeAll { missing.contains($0.item.id) }
        for id in missing { downloadStates[id] = .failed("This download is no longer on the device. Download it again.") }
        var missingArtwork = false
        for index in entries.indices {
            downloadStates[entries[index].item.id] = .downloaded
            if entries[index].artworkFilename != nil, existingArtworkURL(entries[index]) == nil {
                entries[index].artworkFilename = nil
                missingArtwork = true
            }
        }
        if !missing.isEmpty || missingArtwork { saveOrReport() }
        updateUsedBytes()
    }

    private func hasCurrentCredentials(_ request: URLRequest) -> Bool {
        guard let accessToken, !accessToken.isEmpty else { return false }
        var supplied = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
            .filter { $0.name.lowercased() == "apikey" }.compactMap(\.value) ?? []
        if let header = request.value(forHTTPHeaderField: "X-Emby-Token") { supplied.append(header) }
        if let header = request.value(forHTTPHeaderField: "Authorization") {
            if header.lowercased().hasPrefix("bearer ") { supplied.append(String(header.dropFirst(7))) }
            else if let range = header.range(of: "Token=\"", options: .caseInsensitive),
                    let end = header[range.upperBound...].firstIndex(of: "\"") {
                supplied.append(String(header[range.upperBound..<end]))
            }
        }
        return !supplied.isEmpty && supplied.allSatisfy { $0 == accessToken }
    }

    private var ownerDirectory: URL? { owner.map { root.appendingPathComponent($0, isDirectory: true) } }

    private static func ownerKey(_ session: LuminaSession) -> String {
        let url = session.serverURL
        let port = url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80)
        let origin = "\(url.scheme?.lowercased() ?? "")://\(url.host?.lowercased() ?? ""):\(port)"
        return SHA256.hash(data: Data("\(origin)\n\(session.userID)".utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private func safeURL(_ filename: String) -> URL? {
        let name = URL(fileURLWithPath: filename)
        guard filename == name.lastPathComponent,
              UUID(uuidString: name.deletingPathExtension().lastPathComponent) != nil,
              OfflineTransfer.extensions.contains(name.pathExtension), let directory = ownerDirectory else { return nil }
        return directory.appendingPathComponent(filename)
    }

    private func existingURL(_ entry: Entry) -> URL? {
        let types = entry.item.isAudio ? OfflineTransfer.audioTypes : OfflineTransfer.videoTypes
        let maximumBytes = entry.item.isAudio ? Self.maximumTrackBytes : Self.maximumVideoBytes
        guard Self.supportsDownload(entry.item),
              entry.bytes > 0, entry.bytes <= maximumBytes,
              let url = safeURL(entry.filename), types.values.contains(url.pathExtension),
              let facts = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              facts.isRegularFile == true, facts.isSymbolicLink != true,
              Int64(facts.fileSize ?? 0) == entry.bytes else { return nil }
        return url
    }

    private func existingArtworkURL(_ entry: Entry) -> URL? {
        guard let filename = entry.artworkFilename, let url = safeURL(filename),
              OfflineTransfer.artworkTypes.values.contains(url.pathExtension),
              let facts = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              facts.isRegularFile == true, facts.isSymbolicLink != true,
              let size = facts.fileSize, size > 0, size <= Self.maximumArtworkBytes else { return nil }
        return url
    }

    private func prepare(_ directory: URL) throws {
        if FileManager.default.fileExists(atPath: directory.path),
           try directory.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true { throw OfflineFailure.storage }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try protect(directory)
    }

    private func protect(_ url: URL) throws {
        var target = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try target.setResourceValues(values)
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }

    private func save() throws {
        guard let directory = ownerDirectory else { return }
        try prepare(directory)
        let url = directory.appendingPathComponent("manifest.json")
        let data = try JSONEncoder().encode(entries)
        guard data.count <= 8 * 1_024 * 1_024 else { throw OfflineFailure.storage }
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        try protect(url)
    }

    private func saveOrReport() {
        do { try save() }
        catch { errorMessage = "Download changes could not be saved." }
    }

    private func updateUsedBytes() {
        guard let directory = ownerDirectory else { usedBytes = 0; return }
        let files = (try? FileManager.default.contentsOfDirectory(at: directory,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])) ?? []
        usedBytes = files.filter { $0.lastPathComponent != "manifest.json" }.reduce(0) { total, file in
            guard let facts = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
                  facts.isRegularFile == true, facts.isSymbolicLink != true else { return total }
            return total + Int64(facts.fileSize ?? 0)
        }
    }

    private func cancel(key: UUID) {
        if let index = jobs.firstIndex(where: { $0.key == key }) {
            let job = jobs.remove(at: index)
            if job.owner == owner, !job.artwork { downloadStates[job.item.id] = .cancelled }
            job.continuation.resume()
        } else if let job = active, job.key == key {
            cancelled.insert(key)
            if job.owner == owner, !job.artwork { downloadStates[job.item.id] = .cancelled }
            transfer?.cancel()
        }
    }

    private func cancelAll() {
        for job in jobs { job.continuation.resume() }
        jobs.removeAll()
        if let active { cancel(key: active.key) }
    }

    private func startNext() {
        guard active == nil, !jobs.isEmpty else { return }
        let job = jobs.removeFirst()
        active = job
        guard job.owner == owner, job.generation == generation, let directory = ownerDirectory else {
            finish(key: job.key, result: .failure(CancellationError())); return
        }
        updateUsedBytes()
        let available = max(0, limitBytes - usedBytes)
        guard available > 0 else { finish(key: job.key, result: .failure(OfflineFailure.budget)); return }
        do { try prepare(directory) }
        catch { finish(key: job.key, result: .failure(OfflineFailure.storage)); return }
        if !job.artwork { downloadStates[job.item.id] = .downloading(receivedBytes: 0, totalBytes: nil) }
        let mediaMaximum = job.item.isAudio ? Self.maximumTrackBytes : Self.maximumVideoBytes
        let types = job.artwork ? OfflineTransfer.artworkTypes : (job.item.isAudio ? OfflineTransfer.audioTypes : OfflineTransfer.videoTypes)
        let next = OfflineTransfer(origin: job.origin, destination: directory.appendingPathComponent(".partial-\(job.key)"),
                                   maximumBytes: min(job.artwork ? Self.maximumArtworkBytes : mediaMaximum, available),
                                   configuration: configuration, types: types) { [weak self] received, total in
            Task { @MainActor in
                guard let self, self.active?.key == job.key, self.owner == job.owner,
                      self.generation == job.generation, !job.artwork, !self.cancelled.contains(job.key) else { return }
                self.downloadStates[job.item.id] = .downloading(receivedBytes: received, totalBytes: total)
            }
        } completion: { [weak self] result in
            Task { @MainActor in self?.finish(key: job.key, result: result) }
        }
        transfer = next
        next.start(job.request)
    }

    private func finish(key: UUID, result: Result<OfflineTransfer.File, Error>) {
        guard let job = active, job.key == key else { return }
        let wasCancelled = cancelled.remove(key) != nil
        defer {
            active = nil
            transfer = nil
            job.continuation.resume()
            startNext()
        }
        guard job.owner == owner, job.generation == generation, !wasCancelled else {
            if case .success(let file) = result { try? FileManager.default.removeItem(at: file.url) }
            return
        }
        do {
            let file = try result.get()
            defer { try? FileManager.default.removeItem(at: file.url) }
            updateUsedBytes()
            // The staging file is already included in disk usage here.
            guard usedBytes <= limitBytes else { throw OfflineFailure.budget }
            let filename = "\(UUID()).\(file.fileExtension)"
            guard let target = safeURL(filename) else { throw OfflineFailure.storage }
            try FileManager.default.moveItem(at: file.url, to: target)
            let previousEntries = entries
            let previousArtwork = entries.first(where: { $0.item.id == job.item.id })?.artworkFilename
            do {
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
                try protect(target)
                if job.artwork {
                    guard let index = entries.firstIndex(where: { $0.item.id == job.item.id }) else { throw CancellationError() }
                    entries[index].artworkFilename = filename
                } else { entries.append(Entry(item: job.item, filename: filename, bytes: file.bytes)) }
                try save()
                if job.artwork, let previousArtwork, let old = safeURL(previousArtwork) { try? FileManager.default.removeItem(at: old) }
            } catch {
                entries = previousEntries
                try? FileManager.default.removeItem(at: target)
                throw error
            }
            if !job.artwork { downloadStates[job.item.id] = .downloaded }
            errorMessage = nil
        } catch is CancellationError {
            if !job.artwork { downloadStates[job.item.id] = .cancelled }
        } catch {
            let message = (error as? OfflineFailure)?.message ?? "The download could not be saved. Try downloading it again."
            if job.artwork { errorMessage = "Artwork could not be saved. \(message)" }
            else { downloadStates[job.item.id] = .failed(message) }
        }
        updateUsedBytes()
    }
}

private enum OfflineFailure: Error {
    case budget, response, storage, redirect
    var message: String {
        switch self {
        case .budget: "This download exceeds the available offline budget or per-file limit (128 MiB audio, 32 GiB video, 2 MiB artwork). Remove a download and retry."
        case .response: "The server did not return a complete supported media file."
        case .storage: "The device could not save this download. Check available storage."
        case .redirect: "The media download redirected to another server and was blocked."
        }
    }
}

private func offlineSameOrigin(_ left: URL, _ right: URL) -> Bool {
    func port(_ url: URL) -> Int { url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80) }
    return left.scheme?.lowercased() == right.scheme?.lowercased()
        && left.host?.lowercased() == right.host?.lowercased() && port(left) == port(right)
}

/// URLSession writes media to disk; delegate state is serialized and cancellation is lock-protected.
private final class OfflineTransfer: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    struct File: Sendable { let url: URL; let bytes: Int64; let fileExtension: String }
    static let audioTypes = ["audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/flac": "flac", "audio/x-flac": "flac",
                             "audio/aac": "aac", "audio/wav": "wav", "audio/x-wav": "wav", "audio/aiff": "aiff",
                             "audio/ogg": "ogg", "audio/opus": "opus"]
    /// These are complete, direct-playable file responses. Playlist/HLS and generic binary responses are rejected.
    static let videoTypes = ["video/mp4": "mp4", "application/mp4": "mp4", "video/quicktime": "mov", "video/x-m4v": "m4v",
                             "video/x-matroska": "mkv", "application/x-matroska": "mkv"]
    static let artworkTypes = ["image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"]
    static let extensions = Set(Array(audioTypes.values) + Array(videoTypes.values) + Array(artworkTypes.values))
    private let origin: URL
    private let destination: URL
    private let maximumBytes: Int64
    private let configuration: URLSessionConfiguration
    private let types: [String: String]
    private let progress: @Sendable (Int64, Int64?) -> Void
    private let completion: @Sendable (Result<File, Error>) -> Void
    private let lock = NSLock()
    private var task: URLSessionDownloadTask?
    private var session: URLSession?
    private var failure: Error?
    private var file: File?
    private var completed = false
    private var lastProgressNanoseconds: UInt64 = 0

    init(origin: URL, destination: URL, maximumBytes: Int64, configuration: URLSessionConfiguration,
         types: [String: String], progress: @escaping @Sendable (Int64, Int64?) -> Void,
         completion: @escaping @Sendable (Result<File, Error>) -> Void) {
        self.origin = origin; self.destination = destination; self.maximumBytes = maximumBytes
        self.configuration = configuration; self.types = types; self.progress = progress; self.completion = completion
    }

    func start(_ request: URLRequest) {
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        let task = session.downloadTask(with: request)
        lock.withLock { self.session = session; self.task = task }
        task.resume()
    }

    func cancel() { lock.withLock { task }?.cancel() }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let url = request.url, offlineSameOrigin(origin, url), url.user == nil, url.password == nil else {
            lock.withLock { failure = OfflineFailure.redirect }
            completionHandler(nil)
            task.cancel()
            return
        }
        completionHandler(request)
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > maximumBytes || totalBytesExpectedToWrite > maximumBytes {
            lock.withLock { failure = OfflineFailure.budget }
            downloadTask.cancel()
        } else {
            // Large video transfers write in very small chunks. Keep the observable UI responsive without
            // scheduling a main-actor state change for every network callback.
            let shouldReport = lock.withLock { () -> Bool in
                let now = DispatchTime.now().uptimeNanoseconds
                let complete = totalBytesExpectedToWrite > 0 && totalBytesWritten >= totalBytesExpectedToWrite
                guard complete || now &- lastProgressNanoseconds >= 125_000_000 else { return false }
                lastProgressNanoseconds = now
                return true
            }
            if shouldReport { progress(totalBytesWritten, totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : nil) }
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        do {
            guard let response = downloadTask.response as? HTTPURLResponse, response.statusCode == 200,
                  let fileExtension = types[response.mimeType?.lowercased() ?? ""] else { throw OfflineFailure.response }
            let size = Int64(try location.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
            guard size > 0, size <= maximumBytes else { throw OfflineFailure.budget }
            guard response.expectedContentLength < 0 || response.expectedContentLength == size else { throw OfflineFailure.response }
            if let failure = lock.withLock({ failure }) { throw failure }
            try FileManager.default.moveItem(at: location, to: destination)
            lock.withLock { file = File(url: destination, bytes: size, fileExtension: fileExtension) }
        } catch { lock.withLock { failure = error } }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<File, Error>? = lock.withLock {
            guard !completed else { return nil }
            completed = true
            if let failure { return .failure(failure) }
            if let error { return .failure(error is URLError && (error as? URLError)?.code == .cancelled ? CancellationError() : error) }
            if let file { return .success(file) }
            return .failure(OfflineFailure.response)
        }
        session.finishTasksAndInvalidate()
        if case .failure = result { try? FileManager.default.removeItem(at: destination) }
        if let result { completion(result) }
    }
}
