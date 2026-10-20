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
    private var lyricCache: [String: MusicLyrics] = [:]
    private var generation = 0
    private var playlistGeneration = 0

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
        guard self.session?.accessToken != session?.accessToken || self.session?.serverURL != session?.serverURL else { return }
        generation += 1
        self.session = session
        playlists = []; metadata = [:]; lyricCache = [:]; cachedSession = nil
        isBusy = false; errorMessage = nil
        sessionIdentity = session.map {
            SHA256.hash(data: Data(($0.serverURL.absoluteString.lowercased() + "|" + $0.userID).utf8))
                .map { String(format: "%02x", $0) }.joined()
        }
        guard let directory = ownerDirectory else { return }
        if let data = try? Data(contentsOf: directory.appending(path: "queue.json")), data.count <= 8 * 1024 * 1024 {
            cachedSession = try? JSONDecoder().decode(MusicListeningSnapshot.self, from: data)
            remember(cachedSession?.queue ?? [])
        }
        if let data = try? Data(contentsOf: directory.appending(path: "lyrics.json")), data.count <= 4 * 1024 * 1024 {
            lyricCache = (try? JSONDecoder().decode([String: MusicLyrics].self, from: data)) ?? [:]
        }
    }

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
            if let directory = ownerDirectory, let data = try? JSONEncoder().encode(lyricCache), data.count <= 4 * 1024 * 1024 {
                try? prepareDirectory(directory)
                try? data.write(to: directory.appending(path: "lyrics.json"), options: .atomic)
            }
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
        cachedSession = snapshot
        guard let directory = ownerDirectory else { return }
        var safe = snapshot
        func sanitized(_ track: MusicTrack) -> MusicTrack {
            var value = track
            value.audioURL = ""
            value.artworkURL = nil
            return value
        }
        safe.queue = snapshot.queue.map(sanitized)
        safe.entries = snapshot.entries.map { entry in
            var value = entry
            value.tracks = entry.tracks?.map(sanitized)
            return value
        }
        guard let data = try? JSONEncoder().encode(safe), data.count <= 8 * 1024 * 1024 else { return }
        try? prepareDirectory(directory)
        try? data.write(to: directory.appending(path: "queue.json"), options: .atomic)
    }

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

    private func remember(_ tracks: [MusicTrack]) {
        for track in tracks { metadata[track.id] = track; metadata[track.nativeID] = track }
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
