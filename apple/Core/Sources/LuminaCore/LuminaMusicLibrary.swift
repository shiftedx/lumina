import CryptoKit
import Foundation
import Observation

/// Member-scoped music contracts. Native tokens stay inside the opt-in connected-app boundary.
@MainActor @Observable public final class LuminaMusicLibrary {
    public private(set) var playlists: [MusicPlaylist] = []
    public private(set) var cachedSession: MusicListeningSnapshot?
    public private(set) var sessionIdentity: String?
    public private(set) var isBusy = false
    public private(set) var errorMessage: String?
    private var session: LuminaSession?
    private let client: LuminaClient
    private let root: URL
    private var metadata: [String: MusicTrack] = [:]
    private var metadataOrder: [String] = []
    private var lyricCache: [String: MusicLyrics] = [:]
    private var generation = 0
    private var playlistGeneration = 0
    private let checkpointWriter = CheckpointWriter()
    private var cacheLoad: Task<Void, Never>?
    private var checkpointGeneration = 0

    public convenience init() { self.init(client: LuminaClient()) }

    init(client: LuminaClient, root: URL? = nil) {
        self.client = client
        #if os(tvOS)
        let directory: FileManager.SearchPathDirectory = .cachesDirectory
        #else
        let directory: FileManager.SearchPathDirectory = .applicationSupportDirectory
        #endif
        self.root = root ?? FileManager.default.urls(for: directory, in: .userDomainMask)[0]
            .appending(path: "LuminaMusic", directoryHint: .isDirectory)
    }

    public func configure(session: LuminaSession?) {
        guard self.session?.accessToken != session?.accessToken || self.session?.serverURL != session?.serverURL || self.session?.userID != session?.userID else { return }
        generation += 1
        checkpointGeneration += 1
        cacheLoad?.cancel()
        cacheLoad = nil
        self.session = session
        playlists = []; metadata = [:]; metadataOrder = []; lyricCache = [:]; cachedSession = nil
        isBusy = false; errorMessage = nil
        sessionIdentity = session.map {
            SHA256.hash(data: Data(($0.serverURL.absoluteString.lowercased() + "|" + $0.userID).utf8))
                .map { String(format: "%02x", $0) }.joined()
        }
        guard let directory = ownerDirectory else { return }
        let owner = sessionIdentity
        let loadGeneration = generation
        let checkpointGeneration = checkpointGeneration
        let writer = checkpointWriter
        cacheLoad = Task { [weak self, writer] in
            let persisted = await writer.load(directory: directory)
            guard !Task.isCancelled, let self,
                  self.generation == loadGeneration,
                  self.sessionIdentity == owner
            else { return }
            if self.checkpointGeneration == checkpointGeneration {
                self.cachedSession = persisted.snapshot
                self.rememberPersisted(persisted.snapshot?.queue ?? [])
            }
            for (id, lyrics) in persisted.lyrics where self.lyricCache.count < 100 && self.lyricCache[id] == nil {
                self.lyricCache[id] = lyrics
            }
        }
    }

    /// Waits for the configured member's bounded persisted snapshot before restoring playback.
    public func waitForPersistentCache() async { await cacheLoad?.value }

    public func refresh() async {
        let current = generation
        playlistGeneration += 1
        let requestGeneration = playlistGeneration
        isBusy = true; errorMessage = nil
        defer { if current == generation && requestGeneration == playlistGeneration { isBusy = false } }
        do {
            let result: [MusicPlaylist] = try await request("playlists")
            guard current == generation, requestGeneration == playlistGeneration else { return }
            playlists = result
        } catch {
            guard current == generation, requestGeneration == playlistGeneration else { return }
            errorMessage = error.localizedDescription
        }
    }

    public func tracks(in playlist: MusicPlaylist) async throws -> [MusicTrack] {
        let tracks: [MusicTrack] = try await request("playlists/\(try safeID(playlist.id))/tracks")
        remember(tracks)
        return tracks
    }

    public func savePlaylist(id: String? = nil, name: String, shared: Bool, itemIDs: [String], rules: MusicPlaylistRules?, revision: Int?) async throws -> MusicPlaylist {
        let owner = generation
        var body: [String: Any] = ["name": name, "shared": shared, "item_ids": itemIDs]
        if let rules { body["rules"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(rules)) }
        if let revision { body["revision"] = revision }
        let path = try id.map { "playlists/\(try safeID($0))" } ?? "playlists"
        let value: MusicPlaylist = try await request(path, method: id == nil ? "POST" : "PUT", body: body)
        await refresh()
        guard owner == generation else { throw CancellationError() }
        return value
    }

    public func deletePlaylist(_ playlist: MusicPlaylist) async throws {
        let owner = generation
        let _: Deleted = try await request("playlists/\(try safeID(playlist.id))", method: "DELETE")
        await refresh()
        guard owner == generation else { throw CancellationError() }
    }

    public func mix(_ kind: String, seedID: String? = nil) async throws -> [MusicTrack] {
        var query: [URLQueryItem] = []
        if let seedID { query.append(URLQueryItem(name: "seed_id", value: seedID)) }
        let tracks: [MusicTrack] = try await request("mixes/\(try safeID(kind))", query: query)
        remember(tracks)
        return tracks
    }

    public func resolve(items: [MediaItem]) async throws -> [MusicTrack] {
        let tracks: [MusicTrack] = try await request("queue", method: "POST", body: ["item_ids": items.map(\.id)])
        remember(tracks)
        return tracks
    }

    public func track(for item: MediaItem) async throws -> MusicTrack {
        if let track = metadata[item.id] { return track }
        let track: MusicTrack = try await request("tracks/\(try safeID(item.id))")
        remember([track])
        return track
    }

    public func cachedTrack(for id: String) -> MusicTrack? { metadata[id] }

    public func lyrics(for item: MediaItem, retry: Bool = false) async throws -> MusicLyrics {
        let owner = generation
        do {
            let value: MusicLyrics = try await request("tracks/\(try safeID(item.id))/lyrics", query: [URLQueryItem(name: "retry", value: retry ? "true" : "false")])
            lyricCache[item.id] = value
            if lyricCache.count > 100, let key = lyricCache.keys.first(where: { $0 != item.id }) { lyricCache.removeValue(forKey: key) }
            if let directory = ownerDirectory { checkpointWriter.enqueueLyrics(lyricCache, directory: directory) }
            return value
        } catch {
            guard owner == generation else { throw CancellationError() }
            if let cached = lyricCache[item.id], !retry { return cached }
            throw error
        }
    }

    public func cachedLyrics(for id: String) -> MusicLyrics? { lyricCache[id] }

    public func loadSession() async throws -> MusicListeningSnapshot {
        let value: MusicListeningSnapshot = try await request("session")
        remember(value.queue)
        return value
    }

    public func saveSession(itemIDs: [String], currentIndex: Int?, positionSeconds: Double, repeatMode: String, shuffle: Bool, baseRevision: Int, acknowledgedOutputTicket: String? = nil, entries: [MusicQueueEntry]? = nil, entryIndex: Int? = nil, unavailablePolicy: String = "skip") async throws -> MusicListeningSnapshot {
        var body: [String: Any] = [
            "base_revision": baseRevision, "item_ids": itemIDs,
            "current_index": currentIndex.map { $0 as Any } ?? NSNull(),
            "position_seconds": min(604800, max(0, positionSeconds.isFinite ? positionSeconds : 0)),
            "repeat": repeatMode, "shuffle": shuffle, "active_output": ["kind": "browser"],
            "unavailable_policy": unavailablePolicy,
        ]
        if let acknowledgedOutputTicket { body["acknowledged_output_ticket"] = acknowledgedOutputTicket }
        if let entries {
            body["entries"] = entries.map { entry -> [String: String] in
                if entry.kind == "acquisition", let id = entry.requestID { return ["kind": "acquisition", "request_id": id] }
                return ["kind": "track", "item_id": entry.itemID ?? ""]
            }
            body["entry_index"] = entryIndex.map { $0 as Any } ?? NSNull()
        }
        let value: MusicListeningSnapshot = try await request("session", method: "PUT", body: body)
        remember(value.queue)
        return value
    }

    public func persistCheckpoint(snapshot: MusicListeningSnapshot, owner: String? = nil) {
        guard sessionIdentity != nil, owner == nil || owner == sessionIdentity else { return }
        checkpointGeneration += 1
        cachedSession = snapshot
        guard let directory = ownerDirectory else { return }
        checkpointWriter.enqueue(snapshot: snapshot, directory: directory)
    }

    /// Waits for the latest accepted checkpoint before the app is suspended or tests read it back.
    public func flushCheckpoint() async { await checkpointWriter.flush() }

    public func speakers() async throws -> [MusicSpeaker] { try await request("sonos") }

    public func cast(speakerID: String, itemIDs: [String], baseRevision: Int, currentIndex: Int, positionSeconds: Double, compatibility: Bool) async throws -> MusicCastResult {
        try await request("cast", method: "POST", body: [
            "speaker_id": speakerID, "item_ids": itemIDs, "base_revision": baseRevision,
            "current_index": currentIndex, "position_seconds": positionSeconds, "compatibility": compatibility,
        ])
    }

    public func control(ticketID: String, action: String, value: Int? = nil) async throws -> MusicOutputStatus {
        var body: [String: Any] = ["action": action]
        if let value { body["value"] = value }
        return try await request("cast/\(try safeID(ticketID))/control", method: "POST", body: body)
    }

    public func status(ticketID: String) async throws -> MusicOutputStatus {
        try await request("cast/\(try safeID(ticketID))/status")
    }

    public func downloadRequest(for item: MediaItem, format: String = "native") throws -> URLRequest {
        guard let session else { throw LuminaError.notSignedIn }
        return client.request(origin: session.serverURL, path: "/Lumina/Music/tracks/\(try safeID(item.id))/download",
                              query: [URLQueryItem(name: "format", value: format)], token: session.accessToken, deviceID: session.deviceID)
    }

    private var ownerDirectory: URL? { sessionIdentity.map { root.appending(path: $0, directoryHint: .isDirectory) } }

    private func remember(_ tracks: [MusicTrack]) {
        for track in tracks {
            if let previous = metadata[track.id] {
                if previous.nativeID != track.nativeID { metadata[previous.nativeID] = nil }
            } else { metadataOrder.append(track.id) }
            metadata[track.id] = track
            metadata[track.nativeID] = track
        }
        // Queue/snapshot owners retain their complete order; this is only lookup history.
        let excess = max(0, metadataOrder.count - 512)
        for id in metadataOrder.prefix(excess) {
            if let previous = metadata.removeValue(forKey: id), metadata[previous.nativeID]?.id == id {
                metadata[previous.nativeID] = nil
            }
        }
        metadataOrder.removeFirst(excess)
    }

    private func rememberPersisted(_ tracks: [MusicTrack]) {
        let capacity = max(0, 512 - metadataOrder.count)
        guard capacity > 0 else { return }
        remember(Array(tracks.lazy.filter { self.metadata[$0.id] == nil && self.metadata[$0.nativeID] == nil }.prefix(capacity)))
    }

    private func safeID(_ value: String) throws -> String {
        guard !value.isEmpty, value.utf8.count <= 128,
              value.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "-" || $0 == "_" }) else { throw LuminaError.unsafeURL }
        return value
    }

    private func request<Value: Decodable>(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: [String: Any]? = nil) async throws -> Value {
        guard let session else { throw LuminaError.notSignedIn }
        let current = generation
        let request = client.request(origin: session.serverURL, path: "/Lumina/Music/" + path, method: method, query: query,
                                     token: session.accessToken, deviceID: session.deviceID, body: body)
        do {
            let data = try await client.send(request)
            guard current == generation else { throw CancellationError() }
            return try JSONDecoder().decode(Value.self, from: data)
        } catch LuminaError.http(let code) {
            guard current == generation else { throw CancellationError() }
            throw MusicAPIError.http(code)
        } catch {
            guard current == generation else { throw CancellationError() }
            throw error
        }
    }
}

private struct Deleted: Decodable { var deleted: Bool }

/// Keeps checkpoint work off the main actor while retaining only an active write and its newest replacement.
private final class CheckpointWriter: @unchecked Sendable {
    private struct Work: Sendable {
        var snapshot: MusicListeningSnapshot?
        var lyrics: [String: MusicLyrics]?
        let directory: URL
    }

    struct PersistedCache: Sendable {
        let snapshot: MusicListeningSnapshot?
        let lyrics: [String: MusicLyrics]
    }

    private let queue = DispatchQueue(label: "app.lumina.music-checkpoint", qos: .utility)
    private let lock = NSLock()
    private var pending = [URL: Work]()
    private var pendingOrder = [URL]()
    private var draining = false

    func enqueue(snapshot: MusicListeningSnapshot, directory: URL) {
        lock.lock()
        var work = pendingWork(for: directory)
        work.snapshot = snapshot
        pending[directory] = work
        if !draining {
            draining = true
            queue.async { self.drain() }
        }
        lock.unlock()
    }

    func enqueueLyrics(_ lyrics: [String: MusicLyrics], directory: URL) {
        lock.lock()
        var work = pendingWork(for: directory)
        work.lyrics = lyrics
        pending[directory] = work
        if !draining {
            draining = true
            queue.async { self.drain() }
        }
        lock.unlock()
    }

    func load(directory: URL) async -> PersistedCache {
        await withCheckedContinuation { continuation in
            queue.async { [weak self] in
                continuation.resume(returning: self?.read(directory: directory) ?? .init(snapshot: nil, lyrics: [:]))
            }
        }
    }

    func flush() async {
        await withCheckedContinuation { continuation in
            queue.async { continuation.resume() }
        }
    }

    private func drain() {
        while let work = next() { write(work) }
    }

    private func next() -> Work? {
        lock.lock()
        defer { lock.unlock() }
        guard let directory = pendingOrder.first, let work = pending.removeValue(forKey: directory) else {
            draining = false
            return nil
        }
        pendingOrder.removeFirst()
        return work
    }

    private func pendingWork(for directory: URL) -> Work {
        if let work = pending[directory] { return work }
        pendingOrder.append(directory)
        return Work(snapshot: nil, lyrics: nil, directory: directory)
    }

    private func write(_ work: Work) {
        if let snapshot = work.snapshot { writeSnapshot(snapshot, directory: work.directory) }
        if let lyrics = work.lyrics { writeLyrics(lyrics, directory: work.directory) }
    }

    private func writeSnapshot(_ snapshot: MusicListeningSnapshot, directory: URL) {
        var safe = snapshot
        func sanitized(_ track: MusicTrack) -> MusicTrack {
            var value = track
            value.audioURL = ""
            value.artworkURL = nil
            return value
        }
        safe.queue = safe.queue.map(sanitized)
        safe.entries = safe.entries.map { entry in
            var value = entry
            value.tracks = entry.tracks?.map(sanitized)
            return value
        }
        guard let data = try? JSONEncoder().encode(safe), data.count <= 8 * 1024 * 1024 else { return }
        do {
            try prepareDirectory(directory)
            try data.write(to: directory.appending(path: "queue.json"), options: .atomic)
        } catch {}
    }

    private func writeLyrics(_ lyrics: [String: MusicLyrics], directory: URL) {
        guard let data = try? JSONEncoder().encode(lyrics), data.count <= 4 * 1024 * 1024 else { return }
        do {
            try prepareDirectory(directory)
            try data.write(to: directory.appending(path: "lyrics.json"), options: .atomic)
        } catch {}
    }

    private func read(directory: URL) -> PersistedCache {
        let snapshot: MusicListeningSnapshot?
        if let data = try? Data(contentsOf: directory.appending(path: "queue.json"), options: .mappedIfSafe), data.count <= 8 * 1024 * 1024 {
            snapshot = try? JSONDecoder().decode(MusicListeningSnapshot.self, from: data)
        } else { snapshot = nil }
        let lyrics: [String: MusicLyrics]
        if let data = try? Data(contentsOf: directory.appending(path: "lyrics.json"), options: .mappedIfSafe), data.count <= 4 * 1024 * 1024 {
            lyrics = (try? JSONDecoder().decode([String: MusicLyrics].self, from: data)) ?? [:]
        } else { lyrics = [:] }
        return .init(snapshot: snapshot, lyrics: lyrics)
    }

    private func prepareDirectory(_ directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var resource = directory
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try resource.setResourceValues(values)
        #if os(iOS)
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: directory.path)
        #endif
    }
}
