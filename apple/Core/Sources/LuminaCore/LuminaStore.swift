import Foundation
import Observation
import Security

let tickScale = 10_000_000.0

@MainActor @Observable public final class LuminaStore {
    public private(set) var session: LuminaSession? {
        didSet {
            if oldValue?.serverURL != session?.serverURL || oldValue?.accessToken != session?.accessToken || oldValue?.userID != session?.userID {
                ArtworkImageCache.shared.removeAll()
                readyArtworkFramings.removeAll()
                readyArtworkFramingOrder.removeAll()
                availableLibraryCategories = []
            }
            music.configure(session: session)
            offline.configure(session: session)
        }
    }
    public let music = LuminaMusicLibrary()
    public let offline = LuminaOfflineLibrary()
    /// Device-local choices applied to each native video preparation. They never mutate member settings.
    public let playbackPreferences: NativePlaybackPreferences
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
    public private(set) var libraryAlphabet = [LibraryAlphabetAnchor]()
    public private(set) var availableLibraryCategories = Set<String>()

    /// Alphabet seeking is available only where a server index can describe the
    /// current complete video wall. Search, recency/year ordering, episodes and
    /// saved-provider channels intentionally keep ordinary pagination.
    public var supportsLibraryAlphabet: Bool {
        guard session != nil,
              currentSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              videoSort == .name else { return false }
        return switch videoKind {
        case .all, .movies, .shows, .anime: true
        case .youtube, .episodes: false
        }
    }

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
    private var videoSourceProvider: String?
    private var videoLuminaCategory: String?
    private var musicKind = MusicBrowseKind.albums
    private var audioSearch = ""
    private var audioIndex = 0
    private var audioGeneration = 0
    private var homeGeneration = 0
    private var homeTask: Task<Void, Never>?
    private var libraryTask: Task<Void, Never>?
    private var libraryAlphabetTask: Task<Void, Never>?
    private var nextIndex = 0
    private var libraryGeneration = 0
    private var readyArtworkFramings = [URL: HeroFraming]()
    private var readyArtworkFramingOrder = [URL]()
    private let maximumReadyArtworkFramings = 80

    public init(playbackPreferences: NativePlaybackPreferences = NativePlaybackPreferences()) {
        credentials = KeychainCredentials()
        client = LuminaClient()
        self.playbackPreferences = playbackPreferences
    }

    init(client: LuminaClient, credentials: CredentialStore,
         playbackPreferences: NativePlaybackPreferences = NativePlaybackPreferences()) {
        self.client = client
        self.credentials = credentials
        self.playbackPreferences = playbackPreferences
    }

    /// Loads public release artwork for the signed-out welcome screen without sending member credentials or a device ID.
    public func publicShowcase(server: String) async throws -> [PublicShowcaseSlide] {
        let origin = try ServerOrigin(server)
        return try await client.publicShowcase(origin: origin.url)
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
        libraryTask?.cancel()
        libraryTask = nil
        libraryAlphabetTask?.cancel()
        libraryAlphabetTask = nil
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
        videoSourceProvider = nil
        videoLuminaCategory = nil
        hasMore = false
        libraryAlphabet = []
    }

    public func loadLibrary(search: String = "", kind: VideoBrowseKind = .all, sort: VideoSort = .name, filters: VideoFilters = .init()) async {
        await loadLibrary(search: search, kind: kind, sort: sort, filters: filters, clearsError: true)
    }

    /// Searches the authenticated catalog without replacing the current Library
    /// wall, its filters, pagination cursor, or alphabet anchors.
    public func searchHome(query: String) async throws -> [MediaItem] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            let page = try await client.items(
                origin: active.serverURL,
                token: active.accessToken,
                deviceID: active.deviceID,
                search: trimmed,
                start: 0,
                types: "Movie,Series,Episode,MusicAlbum,MusicArtist,Audio"
            )
            guard !Task.isCancelled, isCurrent(active) else { throw LuminaError.notSignedIn }
            return page.items.prefix(50).map { $0 }
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) {
                clearSession()
            }
            throw error
        }
    }

    private func loadLibrary(search: String, kind: VideoBrowseKind, sort: VideoSort, filters: VideoFilters, clearsError: Bool) async {
        guard !Task.isCancelled, session != nil else { return }
        libraryTask?.cancel()
        libraryAlphabetTask?.cancel()
        libraryAlphabetTask = nil
        libraryGeneration += 1
        let generation = libraryGeneration
        isBusy = true
        if clearsError { errorMessage = nil }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performLibraryLoad(search: search, kind: kind, sort: sort, filters: filters, generation: generation)
        }
        libraryTask = task
        defer {
            if generation == libraryGeneration { isBusy = false; libraryTask = nil }
        }
        await withTaskCancellationHandler(operation: { await task.value }, onCancel: { task.cancel() })
    }

    private func performLibraryLoad(search: String, kind: VideoBrowseKind, sort: VideoSort, filters: VideoFilters, generation: Int) async {
        guard !Task.isCancelled, generation == libraryGeneration, let active = session else { return }
        do {
            currentSearch = search
            videoKind = kind
            videoSort = sort
            videoFilters = filters
            videoParentID = nil
            videoSourceProvider = nil
            videoLuminaCategory = kind.luminaCategory
            libraryAlphabet = []
            if kind == .shows || kind == .youtube {
                let views = try await client.views(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
                guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
                videoParentID = switch kind {
                case .shows:
                    views.items.first { $0.name == "Shows" && $0.collectionType == "tvshows" }?.id
                case .youtube:
                    views.items.first { $0.name == "Channels" }?.id
                default:
                    nil
                }
                if videoParentID == nil { items = []; hasMore = false; nextIndex = 0; return }
                if kind == .youtube { videoSourceProvider = "youtube" }
            }
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: search, start: 0, types: kind.itemTypes, sort: sort, parentID: videoParentID, sourceProvider: videoSourceProvider, luminaCategory: videoLuminaCategory, filters: filters)
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
            items = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            nextIndex = (page.startIndex ?? 0) + page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
            if supportsLibraryAlphabet {
                let types = kind.itemTypes
                let parentID = videoParentID
                let category = videoLuminaCategory
                let alphabetClient = client
                libraryAlphabetTask = Task { [weak self] in
                    guard !Task.isCancelled else { return }
                    let alphabet = try? await alphabetClient.libraryAlphabet(
                        origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                        types: types, parentID: parentID, luminaCategory: category, filters: filters
                    )
                    guard !Task.isCancelled,
                          let self,
                          generation == self.libraryGeneration,
                          self.isCurrent(active) else { return }
                    self.libraryAlphabet = alphabet?.anchors ?? []
                    self.libraryAlphabetTask = nil
                }
            }
        } catch {
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadMore() async {
        guard let active = session, hasMore, !isBusy else { return }
        let generation = libraryGeneration
        let start = nextIndex
        isBusy = true
        defer { if generation == libraryGeneration { isBusy = false } }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: currentSearch, start: start, types: videoKind.itemTypes, sort: videoSort, parentID: videoParentID, sourceProvider: videoSourceProvider, luminaCategory: videoLuminaCategory, filters: videoFilters)
            guard generation == libraryGeneration, isCurrent(active) else { return }
            let playable = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            items.append(contentsOf: playable)
            nextIndex = (page.startIndex ?? start) + page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
        } catch {
            guard generation == libraryGeneration, isCurrent(active) else { return }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    /// Replaces the visible page with the first server page at an alphabet
    /// anchor. The returned id lets a platform focus its first newly-loaded card.
    @discardableResult
    public func jumpLibrary(to anchor: LibraryAlphabetAnchor) async -> String? {
        guard let active = session,
              supportsLibraryAlphabet,
              anchor.index >= 0,
              libraryAlphabet.contains(anchor) else { return nil }
        libraryGeneration += 1
        let generation = libraryGeneration
        isBusy = true
        errorMessage = nil
        defer { if generation == libraryGeneration { isBusy = false } }
        do {
            let page = try await client.items(
                origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                search: currentSearch, start: anchor.index, types: videoKind.itemTypes, sort: videoSort,
                parentID: videoParentID, sourceProvider: videoSourceProvider,
                luminaCategory: videoLuminaCategory, filters: videoFilters
            )
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return nil }
            items = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            nextIndex = (page.startIndex ?? anchor.index) + page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
            return items.first?.id
        } catch {
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return nil }
            errorMessage = Self.message(for: error, signingIn: false)
            return nil
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
        async let navigation: Void = loadNavigationCategories(active, generation: generation)
        _ = await (recent, resume, nextUp, navigation)
    }

    private func loadNavigationCategories(_ active: LuminaSession, generation: Int) async {
        do {
            let categories = try await client.navigationCategories(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            availableLibraryCategories = categories
        } catch {
            // Retain this server's last successful menu through a transient failure.
        }
    }

    public func refreshNavigationCategories() async {
        guard let active = session else { return }
        await loadNavigationCategories(active, generation: homeGeneration)
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

    /// Resolves a concrete music pivot without changing the active browse page.
    /// TV uses this for album and artist tiles that were sourced from a related
    /// media item rather than the current audio catalogue.
    public func musicCollection(id: String, expectedType: String) async throws -> MediaItem {
        guard !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              ["MusicAlbum", "MusicArtist"].contains(expectedType) else {
            throw LuminaError.malformedResponse
        }
        guard let active = session else { throw LuminaError.notSignedIn }
        let requestedID = canonicalMusicCollectionID(id)
        do {
            let fresh = try await client.item(
                origin: active.serverURL,
                token: active.accessToken,
                deviceID: active.deviceID,
                id: requestedID,
                userID: active.userID
            )
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            guard canonicalMusicCollectionID(fresh.id) == requestedID,
                  fresh.type == expectedType else { throw LuminaError.malformedResponse }
            replaceCachedItem(fresh)
            return fresh
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) {
                clearSession()
            }
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

    /// Jellyfin commonly represents a UUID link in music metadata with hyphens,
    /// while its item endpoint returns the same value in compact lowercase form.
    /// Non-UUID fixture and provider identifiers deliberately remain byte-for-byte
    /// unchanged.
    private func canonicalMusicCollectionID(_ id: String) -> String {
        let compact = id.replacingOccurrences(of: "-", with: "")
        guard compact.utf8.count == 32,
              compact.unicodeScalars.allSatisfy({ CharacterSet(charactersIn: "0123456789abcdefABCDEF").contains($0) }) else {
            return id
        }
        let parts = [
            String(compact.prefix(8)),
            String(compact.dropFirst(8).prefix(4)),
            String(compact.dropFirst(12).prefix(4)),
            String(compact.dropFirst(16).prefix(4)),
            String(compact.dropFirst(20)),
        ]
        guard let uuid = UUID(uuidString: parts.joined(separator: "-")) else { return id }
        return uuid.uuidString.replacingOccurrences(of: "-", with: "").lowercased()
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

    public func relatedItems(for item: MediaItem) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        let loaded = try await client.relatedItems(
            origin: active.serverURL,
            token: active.accessToken,
            deviceID: active.deviceID,
            itemID: item.id
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        var seen = Set<String>()
        return loaded.filter { related in
            related.id != item.id && seen.insert(related.id).inserted
        }.prefix(12).map { $0 }
    }

    public func clearError() { errorMessage = nil }

    public func personImageURL(for person: MediaPerson, maxWidth: Int = 240) -> URL? {
        guard let active = session,
              let id = person.id, !id.isEmpty,
              let tag = person.primaryImageTag, !tag.isEmpty else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(id)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func posterURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        guard ["Episode", "Season"].contains(item.type),
              let seriesID = item.seriesID, let tag = item.seriesPrimaryImageTag,
              let active = session else { return imageURL(for: item, maxWidth: maxWidth) }
        return client.url(origin: active.serverURL, path: "/Items/\(seriesID)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func imageURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        if let artwork = offline.artworkURL(for: item.id) { return artwork }
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if item.isAudio, let albumID = item.albumID, let albumTag = item.albumPrimaryImageTag {
            imageID = albumID
            tag = albumTag
        } else if let parentID = item.parentPrimaryImageItemID, let parentTag = item.parentPrimaryImageTag {
            imageID = parentID
            tag = parentTag
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

    /// Warms the next visible slice without making scrolling views own fetch tasks per cell.
    public func prefetchArtwork(after itemID: String?, in items: [MediaItem], maxWidth: Int = 400) async {
        guard !Task.isCancelled, let itemID, let index = items.firstIndex(where: { $0.id == itemID }) else { return }
        let urls = items.dropFirst(index + 1).prefix(16).compactMap {
            imageURL(for: $0, maxWidth: maxWidth)
        }
        await ArtworkImageCache.shared.prefetch(urls)
    }

    public func artworkFraming(for source: URL) async -> HeroFraming? {
        guard let active = session, source.path.hasSuffix("/Images/Backdrop/0") else { return nil }
        if let cached = readyArtworkFramings[source] { return cached }
        let result = try? await client.heroFraming(origin: active.serverURL, token: active.accessToken,
                                                  deviceID: active.deviceID, backdrop: source)
        guard isCurrent(active), let result else { return nil }
        guard result.state == "ready" else { return result }
        cacheReadyArtworkFraming(result, for: source)
        return result
    }

    private func cacheReadyArtworkFraming(_ framing: HeroFraming, for source: URL) {
        if readyArtworkFramings[source] == nil {
            readyArtworkFramingOrder.append(source)
            if readyArtworkFramingOrder.count > maximumReadyArtworkFramings {
                readyArtworkFramings.removeValue(forKey: readyArtworkFramingOrder.removeFirst())
            }
        }
        readyArtworkFramings[source] = framing
    }

    public func backdropFraming(for item: MediaItem) async -> HeroFraming? {
        guard let source = backdropURL(for: item) else { return nil }
        return await artworkFraming(for: source)
    }

    public func logoURL(for item: MediaItem, maxWidth: Int = 600) -> URL? {
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if let ownTag = item.imageTags?["Logo"] {
            imageID = item.id; tag = ownTag
        } else if let parentID = item.parentLogoItemID, let parentTag = item.parentLogoImageTag {
            imageID = parentID; tag = parentTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Logo", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
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

    public func preparePlayback(for item: MediaItem, forceTranscode: Bool = false,
                                quality: PlaybackQuality? = nil,
                                audioStreamIndex: Int? = nil, subtitleStreamIndex: Int? = nil,
                                mediaSourceID: String? = nil, startPositionSeconds: Double? = nil) async throws -> PlaybackRequest {
        guard let active = session else { throw LuminaError.notSignedIn }
        if item.isAudio, let local = offline.localURL(for: item.id) {
            return PlaybackRequest(id: UUID().uuidString, item: item, url: local, startPositionSeconds: 0,
                                   mediaSourceID: item.id, isTranscoding: false, methodLabel: "Downloaded audio")
        }
        let current = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        if current.isAudio { return try client.audioRequest(origin: active.serverURL, token: active.accessToken, item: current, forceCompatible: forceTranscode) }
        let resolvedQuality = quality ?? playbackPreferences.quality
        let result = try await client.playbackInfo(origin: active.serverURL, token: active.accessToken,
                                                    deviceID: active.deviceID, item: current,
                                                    forceTranscode: forceTranscode,
                                                    quality: resolvedQuality,
                                                    audioStreamIndex: audioStreamIndex,
                                                    subtitleStreamIndex: subtitleStreamIndex,
                                                    mediaSourceID: mediaSourceID,
                                                    startPositionSeconds: startPositionSeconds)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let source = try client.preferredSource(in: result, mediaSourceID: mediaSourceID)
        let streams = try client.playbackStreams(for: source, origin: active.serverURL)
        let preferredAudio = audioStreamIndex ?? playbackPreferences.preferredAudioStreamIndex(in: streams)
        let preferredSubtitle = subtitleStreamIndex ?? playbackPreferences.preferredSubtitleStreamIndex(in: streams)
        let selectedExternalSubtitle = preferredSubtitle.flatMap { index in
            index >= 0 ? streams.first { $0.index == index && $0.hasExternalSubtitle } : nil
        }
        // AVFoundation cannot append a sidecar legible group to a progressive MP4. Preserve
        // Matroska's local packaging route, but ask the server for its compatible HLS decision
        // before handing a direct MP4 sidecar to the playback layer.
        let isMatroska = ["mkv", "matroska"].contains((source.container ?? current.container ?? "").lowercased())
        let requiresServerHLS = selectedExternalSubtitle != nil && source.supportsDirectPlay && !isMatroska
        let needsSelectionRequest = (audioStreamIndex == nil && preferredAudio != nil && preferredAudio != source.defaultAudioStreamIndex)
            || (subtitleStreamIndex == nil && preferredSubtitle != nil && preferredSubtitle != source.defaultSubtitleStreamIndex)
            || requiresServerHLS

        if needsSelectionRequest {
            let selected = try await client.playbackInfo(origin: active.serverURL, token: active.accessToken,
                                                          deviceID: active.deviceID, item: current,
                                                          forceTranscode: forceTranscode || requiresServerHLS,
                                                          quality: resolvedQuality,
                                                          audioStreamIndex: preferredAudio,
                                                          subtitleStreamIndex: preferredSubtitle,
                                                          mediaSourceID: source.id,
                                                          startPositionSeconds: startPositionSeconds)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            let request = try client.playbackRequest(origin: active.serverURL, token: active.accessToken, item: current,
                                                      result: selected, quality: resolvedQuality,
                                                      audioStreamIndex: preferredAudio, subtitleStreamIndex: preferredSubtitle,
                                                      mediaSourceID: source.id, startPositionSeconds: startPositionSeconds)
            guard !requiresServerHLS || request.isTranscoding else { throw LuminaError.malformedResponse }
            return request
        }
        return try client.playbackRequest(origin: active.serverURL, token: active.accessToken, item: current,
                                          result: result, quality: resolvedQuality,
                                          audioStreamIndex: preferredAudio, subtitleStreamIndex: preferredSubtitle,
                                          mediaSourceID: source.id, startPositionSeconds: startPositionSeconds)
    }

    /// Downloads an origin-validated WebVTT sidecar to a private temporary file. The caller owns
    /// the returned file and must remove it after its player item is discarded.
    public func downloadSubtitle(for stream: PlaybackStream) async throws -> URL {
        guard let active = session, stream.hasExternalSubtitle,
              let deliveryURL = stream.subtitleDeliveryURL else { throw LuminaError.malformedResponse }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let data = try await client.subtitle(origin: active.serverURL, token: active.accessToken,
                                             deviceID: active.deviceID, url: deliveryURL)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("lumina-subtitle-\(UUID().uuidString)")
            .appendingPathExtension("vtt")
        do {
            try data.write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            guard isCurrent(active) else {
                try? FileManager.default.removeItem(at: file)
                throw LuminaError.notSignedIn
            }
            return file
        } catch {
            try? FileManager.default.removeItem(at: file)
            throw error
        }
    }

    /// Resolve Lumina's one-variant server HLS master before a local subtitle wrapper references
    /// it. AVPlayer fetches the returned URL itself, so its existing playback query is retained.
    public func hlsMediaVariant(for request: PlaybackRequest) async throws -> URL {
        guard let active = session, request.isTranscoding,
              request.url.pathExtension.lowercased() == "m3u8" else { throw LuminaError.malformedResponse }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let variant = try await client.hlsMediaVariant(origin: active.serverURL, token: active.accessToken,
                                                       deviceID: active.deviceID, masterURL: request.url)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return variant
    }

    /// Reads recorded skip ranges for this concrete playback source. The caller keeps this
    /// optional metadata separate from playback preparation and may offer a retry on failure.
    public func playbackMarkers(for request: PlaybackRequest) async throws -> [PlaybackMarker] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard !request.item.isAudio else { return [] }
        let markers = try await client.playbackMarkers(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
            mediaSourceID: request.mediaSourceID, durationSeconds: request.item.durationSeconds,
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return markers
    }

    /// Returns only the immediate following episode from Lumina's established series order.
    /// The optional lookup is intentionally independent of playback preparation and reporting.
    public func nextEpisode(after item: MediaItem) async throws -> MediaItem? {
        guard let active = session else { throw LuminaError.notSignedIn }
        let episode = try await client.nextEpisode(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, after: item,
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return episode
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
        session?.serverURL == candidate.serverURL && session?.accessToken == candidate.accessToken && session?.userID == candidate.userID
    }
}

public enum LuminaError: Error, Equatable {
    case invalidOrigin, unsafeURL, notSignedIn, malformedResponse, http(Int), keychain(OSStatus)
}

struct ServerOrigin {
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
        #if DEBUG || LUMINA_UI_TESTING
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
