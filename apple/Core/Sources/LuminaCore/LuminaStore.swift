import Foundation
import Observation
import Security

let tickScale = 10_000_000.0

@MainActor @Observable public final class LuminaStore {
    public private(set) var session: LuminaSession? {
        didSet {
            music.configure(session: session)
            offline.configure(session: session)
        }
    }
    public let music = LuminaMusicLibrary()
    public let offline = LuminaOfflineLibrary()
    public private(set) var items = [MediaItem]()
    public private(set) var recentItems = [MediaItem]()
    public private(set) var audioItems = [MediaItem]()
    public private(set) var audioHasMore = false
    public private(set) var isAudioBusy = false
    public private(set) var isHomeBusy = false
    public private(set) var nextUpItems = [MediaItem]()
    public private(set) var resumeItems = [MediaItem]()
    public private(set) var isBusy = false
    public private(set) var errorMessage: String?
    public private(set) var hasMore = false

    public var featuredItems: [MediaItem] {
        var seen = Set<String>()
        return Array((resumeItems + recentItems).filter { seen.insert($0.id).inserted }.prefix(8))
    }

    private let client: LuminaClient
    private let credentials: CredentialStore
    private var currentSearch = ""
    private var videoKind = VideoBrowseKind.all
    private var videoSort = VideoSort.name
    private var videoFilters = VideoFilters()
    private var videoParentID: String?
    private var musicKind = MusicBrowseKind.albums
    private var audioSearch = ""
    private var audioIndex = 0
    private var audioGeneration = 0
    private var homeGeneration = 0
    private var homeTask: Task<Void, Never>?
    private var nextIndex = 0
    private var libraryGeneration = 0

    public init() {
        credentials = KeychainCredentials()
        client = LuminaClient()
    }

    init(client: LuminaClient, credentials: CredentialStore) {
        self.client = client
        self.credentials = credentials
    }

    public func connect(server: String, username: String, password: String) async {
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            let origin = try ServerOrigin(server)
            _ = try await client.publicInfo(origin: origin.url)
            let deviceID = try credentials.deviceID()
            let signedIn = try await client.authenticate(origin: origin.url, username: username, password: password, deviceID: deviceID)
            let checked = try await client.me(origin: origin.url, token: signedIn.token, deviceID: deviceID)
            let saved = LuminaSession(serverURL: origin.url, accessToken: signedIn.token, userID: checked.id, username: checked.name, serverName: signedIn.serverName, deviceID: deviceID)
            try credentials.save(session: saved)
            session = saved
            await refresh()
        } catch {
            errorMessage = Self.message(for: error, signingIn: true)
        }
    }

    public func restore() async {
        guard let saved = try? credentials.load() else { return }
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            let checked = try await client.me(origin: saved.serverURL, token: saved.accessToken, deviceID: saved.deviceID)
            session = LuminaSession(serverURL: saved.serverURL, accessToken: saved.accessToken, userID: checked.id, username: checked.name, serverName: saved.serverName, deviceID: saved.deviceID)
            await refresh()
        } catch {
            if case LuminaError.http(401) = error {
                offline.clear(session: saved)
                credentials.deleteSession()
                session = nil
            } else if case LuminaError.http(403) = error {
                offline.clear(session: saved)
                credentials.deleteSession()
                session = nil
            } else {
                session = saved
            }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func signOut() async {
        let active = session
        if let active { offline.clear(session: active) }
        clearSession()
        guard let active else { return }
        try? await client.logout(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
    }

    private func clearSession() {
        homeTask?.cancel()
        homeTask = nil
        if let session { offline.clear(session: session) }
        credentials.deleteSession()
        session = nil
        libraryGeneration += 1
        audioGeneration += 1
        homeGeneration += 1
        items = []
        recentItems = []
        audioItems = []
        audioHasMore = false
        isAudioBusy = false
        isHomeBusy = false
        isBusy = false
        nextUpItems = []
        resumeItems = []
        videoParentID = nil
        hasMore = false
    }

    public func loadLibrary(search: String = "", kind: VideoBrowseKind = .all, sort: VideoSort = .name, filters: VideoFilters = .init()) async {
        await loadLibrary(search: search, kind: kind, sort: sort, filters: filters, clearsError: true)
    }

    private func loadLibrary(search: String, kind: VideoBrowseKind, sort: VideoSort, filters: VideoFilters, clearsError: Bool) async {
        guard let active = session else { return }
        libraryGeneration += 1
        let generation = libraryGeneration
        isBusy = true
        if clearsError { errorMessage = nil }
        defer { if generation == libraryGeneration { isBusy = false } }
        do {
            currentSearch = search
            videoKind = kind
            videoSort = sort
            videoFilters = filters
            videoParentID = nil
            if kind == .shows || kind == .anime {
                let views = try await client.views(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
                guard generation == libraryGeneration, isCurrent(active) else { return }
                videoParentID = views.items.first { $0.collectionType == "tvshows" && (kind == .anime ? $0.name == "Anime" : $0.name != "Anime") }?.id
                if videoParentID == nil { items = []; hasMore = false; nextIndex = 0; return }
            }
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: search, start: 0, types: kind.itemTypes, sort: sort, parentID: videoParentID, filters: filters)
            guard generation == libraryGeneration, isCurrent(active) else { return }
            items = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            nextIndex = page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
        } catch {
            guard generation == libraryGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadMore() async {
        guard let active = session, hasMore, !isBusy else { return }
        let generation = libraryGeneration
        isBusy = true
        defer { if generation == libraryGeneration { isBusy = false } }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: currentSearch, start: nextIndex, types: videoKind.itemTypes, sort: videoSort, parentID: videoParentID, filters: videoFilters)
            guard generation == libraryGeneration, isCurrent(active) else { return }
            let playable = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            items.append(contentsOf: playable)
            nextIndex += page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
        } catch {
            guard generation == libraryGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadResume() async {
        guard let active = session else { return }
        do {
            let loaded = try await client.resume(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard isCurrent(active) else { return }
            resumeItems = loaded.filter { $0.isPlayable && !$0.isAudio }
        } catch {
            guard isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func refresh() async {
        errorMessage = nil
        async let home: Void = loadHome(clearsError: false)
        await loadLibrary(search: currentSearch, kind: videoKind, sort: videoSort, filters: videoFilters, clearsError: false)
        await home
    }

    public func loadHome() async {
        await loadHome(clearsError: true)
    }

    private func loadHome(clearsError: Bool) async {
        guard !Task.isCancelled else { return }
        homeGeneration += 1
        let generation = homeGeneration
        homeTask?.cancel()
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performHomeLoad(generation: generation, clearsError: clearsError)
        }
        homeTask = task
        defer {
            if generation == homeGeneration {
                isHomeBusy = false
                homeTask = nil
            }
        }
        await withTaskCancellationHandler(operation: {
            await task.value
        }, onCancel: {
            task.cancel()
        })
    }

    private func performHomeLoad(generation: Int, clearsError: Bool) async {
        guard !Task.isCancelled, let active = session, generation == homeGeneration else { return }
        isHomeBusy = true
        if clearsError { errorMessage = nil }
        async let recent: Void = loadHomeRecent(active, generation: generation)
        async let resume: Void = loadHomeResume(active, generation: generation)
        async let nextUp: Void = loadHomeNextUp(active, generation: generation)
        _ = await (recent, resume, nextUp)
    }

    private func loadHomeRecent(_ active: LuminaSession, generation: Int) async {
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: "", start: 0, sort: .recentlyAdded)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            recentItems = page.items.filter { $0.isPlayable && !$0.isAudio }
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    private func loadHomeResume(_ active: LuminaSession, generation: Int) async {
        do {
            let items = try await client.resume(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            resumeItems = items.filter { $0.isPlayable && !$0.isAudio }
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    private func loadHomeNextUp(_ active: LuminaSession, generation: Int) async {
        do {
            let items = try await client.nextUp(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            nextUpItems = items.filter { $0.type == "Episode" }
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadNextUp() async {
        guard let active = session else { return }
        do {
            let loaded = try await client.nextUp(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard isCurrent(active) else { return }
            nextUpItems = loaded.filter { $0.type == "Episode" }
        } catch {
            guard isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadChildren(of item: MediaItem) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard item.isCollection else { return [] }
        var result = [MediaItem]()
        var start = 0
        while true {
            let page = try await client.children(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, item: item, start: start)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            result.append(contentsOf: page.items.filter { item.type == "Series" ? $0.type == "Season" : $0.type == "Episode" })
            start += page.items.count
            if item.type == "Series" || page.items.isEmpty || start >= page.total {
                return result.sorted { ($0.indexNumber ?? Int.max, $0.id) < ($1.indexNumber ?? Int.max, $1.id) }
            }
        }
    }

    public func loadItem(_ item: MediaItem) async throws -> MediaItem {
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            let fresh = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            replaceCachedItem(fresh)
            return fresh
        } catch {
            if isCurrent(active), let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    public func updateItemState(item: MediaItem, played: Bool? = nil, favorite: Bool? = nil) async throws -> MediaItem {
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            if played != nil || favorite != nil {
                try await client.updateItemState(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, userID: active.userID, id: item.id, played: played, favorite: favorite)
            }
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            return try await loadItem(item)
        } catch {
            if isCurrent(active), let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    private func replaceCachedItem(_ fresh: MediaItem) {
        items = items.compactMap { cached in
            guard cached.id == fresh.id else { return cached }
            if videoFilters.favoritesOnly && fresh.userData?.isFavorite != true { return nil }
            if videoFilters.unwatchedOnly && !fresh.isCollection && fresh.userData?.played == true { return nil }
            return fresh
        }
        recentItems = recentItems.map { $0.id == fresh.id ? fresh : $0 }
        audioItems = audioItems.map { $0.id == fresh.id ? fresh : $0 }
        resumeItems = resumeItems.map { $0.id == fresh.id ? fresh : $0 }.filter { $0.resumeSeconds > 0 && $0.userData?.played != true }
        nextUpItems = nextUpItems.map { $0.id == fresh.id ? fresh : $0 }.filter { $0.userData?.played != true }
    }

    public func loadAudio(kind: MusicBrowseKind = .albums, search: String = "") async {
        audioGeneration += 1
        errorMessage = nil
        musicKind = kind
        audioSearch = search
        audioIndex = 0
        audioItems = []
        audioHasMore = true
        await fetchAudioPage()
    }

    public func loadMoreAudio() async {
        guard audioHasMore, !isAudioBusy else { return }
        await fetchAudioPage()
    }

    private func fetchAudioPage() async {
        guard let active = session else { return }
        let generation = audioGeneration
        isAudioBusy = true
        defer { if generation == audioGeneration { isAudioBusy = false } }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: audioSearch, start: audioIndex, types: musicKind.itemTypes)
            guard generation == audioGeneration, isCurrent(active) else { return }
            audioItems.append(contentsOf: page.items.filter { ["Audio", "MusicAlbum", "MusicArtist"].contains($0.type) })
            audioIndex += page.items.count
            audioHasMore = !page.items.isEmpty && audioIndex < page.total
        } catch {
            guard generation == audioGeneration, isCurrent(active) else { return }
            audioHasMore = false
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func musicChildren(of item: MediaItem) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard ["MusicAlbum", "MusicArtist"].contains(item.type) else { return [] }
        var result = [MediaItem]()
        var start = 0
        while true {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: "", start: start, types: item.type == "MusicArtist" ? "MusicAlbum" : "Audio", parentID: item.id)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            result.append(contentsOf: page.items)
            start += page.items.count
            if page.items.isEmpty || start >= page.total { return result }
        }
    }

    public func clearError() { errorMessage = nil }

    public func imageURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        if let artwork = offline.artworkURL(for: item.id) { return artwork }
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if item.isAudio, let albumID = item.albumID, let albumTag = item.albumPrimaryImageTag {
            imageID = albumID
            tag = albumTag
        } else if let ownTag = item.imageTags?["Primary"] {
            imageID = item.id
            tag = ownTag
        } else if ["Episode", "Season"].contains(item.type), let seriesID = item.seriesID, let seriesTag = item.seriesPrimaryImageTag {
            imageID = seriesID
            tag = seriesTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func backdropFraming(for item: MediaItem) async -> HeroFraming? {
        guard let active = session, let backdrop = backdropURL(for: item) else { return nil }
        let result = try? await client.heroFraming(origin: active.serverURL, token: active.accessToken,
                                                  deviceID: active.deviceID, backdrop: backdrop)
        return isCurrent(active) ? result : nil
    }

    public func backdropURL(for item: MediaItem, maxWidth: Int = 1920) -> URL? {
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if let ownTag = item.backdropImageTags?.first {
            imageID = item.id
            tag = ownTag
        } else if let parentID = item.parentBackdropItemID, let parentTag = item.parentBackdropImageTags?.first {
            imageID = parentID
            tag = parentTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Backdrop/0", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func preparePlayback(for item: MediaItem, forceTranscode: Bool = false) async throws -> PlaybackRequest {
        guard let active = session else { throw LuminaError.notSignedIn }
        if item.isAudio, let local = offline.localURL(for: item.id) {
            return PlaybackRequest(id: UUID().uuidString, item: item, url: local, startPositionSeconds: 0,
                                   mediaSourceID: item.id, isTranscoding: false, methodLabel: "Downloaded audio")
        }
        let current = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        if current.isAudio { return try client.audioRequest(origin: active.serverURL, token: active.accessToken, item: current) }
        let result = try await client.playbackInfo(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, item: current, forceTranscode: forceTranscode)
        return try client.playbackRequest(origin: active.serverURL, token: active.accessToken, item: current, result: result)
    }

    public func reportPlayback(event: PlaybackEvent, request: PlaybackRequest, positionSeconds: Double, isPaused: Bool) async throws {
        guard let active = session else { throw LuminaError.notSignedIn }
        try await client.report(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, event: event, request: request, positionSeconds: positionSeconds, isPaused: isPaused)
    }

    private static func message(for error: Error, signingIn: Bool) -> String {
        if let error = error as? LuminaError {
            switch error {
            case .http(404): return "The server returned HTTP 404 (not found). Check the server address and that Apps like Infuse is enabled in Lumina Settings."
            case .http(401): return signingIn ? "Sign-in failed. If this account uses two-factor authentication, use an app password from Lumina Settings → Connected apps." : "Your session expired. Sign in again."
            case .invalidOrigin: return "Enter a complete http:// or https:// server origin."
            case .unsafeURL: return "The server returned a URL outside the configured server origin."
            case .keychain: return "Secure credential storage is unavailable. Use a signed app build and try again."
            default: break
            }
        }
        return "Could not reach the Lumina server."
    }

    private func isCurrent(_ candidate: LuminaSession) -> Bool {
        session?.serverURL == candidate.serverURL && session?.accessToken == candidate.accessToken
    }
}

public enum LuminaError: Error, Equatable {
    case invalidOrigin, unsafeURL, notSignedIn, malformedResponse, http(Int), keychain(OSStatus)
}

private struct ServerOrigin {
    let url: URL
    init(_ value: String) throws {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), ["http", "https"].contains(scheme), parts.host != nil,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { throw LuminaError.invalidOrigin }
        parts.scheme = scheme
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        guard let url = parts.url else { throw LuminaError.invalidOrigin }
        self.url = url
    }
}

protocol CredentialStore: AnyObject {
    func deviceID() throws -> String
    func save(session: LuminaSession) throws
    func load() throws -> LuminaSession?
    func deleteSession()
}

private final class KeychainCredentials: CredentialStore {
    private let service: String = {
        #if DEBUG
        if let namespace = ProcessInfo.processInfo.environment["LUMINA_UI_TEST_NAMESPACE"],
           UUID(uuidString: namespace) != nil { return "dev.lumina.apple.ui-test.\(namespace)" }
        #endif
        return "dev.lumina.apple"
    }()
    private let deviceKey = "device-id"
    private let sessionKey = "session"

    func deviceID() throws -> String {
        if let data = read(deviceKey), let id = String(data: data, encoding: .utf8) { return id }
        let id = UUID().uuidString.lowercased()
        try write(Data(id.utf8), key: deviceKey)
        return id
    }
    func save(session: LuminaSession) throws { try write(JSONEncoder().encode(session), key: sessionKey) }
    func load() throws -> LuminaSession? { guard let data = read(sessionKey) else { return nil }; return try JSONDecoder().decode(LuminaSession.self, from: data) }
    func deleteSession() { delete(sessionKey) }

    private func query(_ key: String) -> [CFString: Any] { [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: key] }
    private func read(_ key: String) -> Data? {
        var values = query(key); values[kSecReturnData] = true; values[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        return SecItemCopyMatching(values as CFDictionary, &result) == errSecSuccess ? result as? Data : nil
    }
    private func write(_ data: Data, key: String) throws {
        delete(key)
        var values = query(key); values[kSecValueData] = data; values[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(values as CFDictionary, nil)
        guard status == errSecSuccess else { throw LuminaError.keychain(status) }
    }
    private func delete(_ key: String) { SecItemDelete(query(key) as CFDictionary) }
}

extension LuminaError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Sign in to access your library."
        case .http(401): return "Your session expired. Sign in again."
        case .http(403): return "Your account cannot access this media."
        case .http(404): return "This item is no longer available."
        case .http(let code): return "The server returned HTTP \(code). Try again."
        default: return nil
        }
    }
}
