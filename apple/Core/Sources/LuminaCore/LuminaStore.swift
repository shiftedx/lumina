import Foundation
import Observation

let tickScale = 10_000_000.0

public enum NavigationLoadState: Equatable, Sendable {
    case loading
    case loaded
    case failed
}

@MainActor @Observable public final class LuminaStore {
    public private(set) var session: LuminaSession? {
        didSet {
            if oldValue?.serverURL != session?.serverURL || oldValue?.accessToken != session?.accessToken || oldValue?.userID != session?.userID {
                ArtworkImageCache.shared.removeAll(includingDisk: oldValue != nil)
                if oldValue != nil { metadataCache?.clear() }
                readyArtworkFramings.removeAll()
                readyArtworkFramingOrder.removeAll()
                availableLibraryCategories = []
                requestsAvailable = false
                streamingAvailable = false
                navigationLoadState = .loading
                hasLoadedNavigationCategories = false
                lastNavigationLoadState = nil
                nativeStreamingCapabilities = nil
                navigationGeneration += 1
                librarySnapshots.removeAll()
                loadedLibraryQuery = nil
                if oldValue != nil {
                    cancelCatalogPreload()
                    libraryTask?.cancel()
                    libraryTask = nil
                    libraryTaskQuery = nil
                    libraryAlphabetTask?.cancel()
                    libraryAlphabetTask = nil
                    libraryGeneration += 1
                    items = []
                    householdCollections = []
                    householdCollectionsError = nil
                    recentItems = []
                    resumeItems = []
                    nextUpItems = []
                    audioItems = []
                    libraryAlphabet = []
                    hasMore = false
                    nextIndex = 0
                }
            }
            metadata.configure(session: session)
            music.configure(session: session)
            offline.configure(session: session)
        }
    }
    public let music = LuminaMusicLibrary()
    public let metadata = LuminaMetadataEditor()
    public let offline: LuminaOfflineLibrary
    /// Reachability includes both the OS network path and failed server requests.
    public private(set) var connectivityStatus: LuminaConnectivity.Status = .unknown
    public var isOffline: Bool { connectivityStatus == .offline }
    /// Only completed, locally playable video downloads are safe to show offline.
    public var offlineVideoItems: [MediaItem] {
        offline.downloadedItems.filter { !$0.isAudio && $0.isPlayable && offline.localURL(for: $0.id) != nil }
    }
    /// Device-local choices applied to each native video preparation. They never mutate member settings.
    public let playbackPreferences: NativePlaybackPreferences
    public private(set) var items = [MediaItem]()
    /// Member-visible manual and smart collections. These remain distinct from
    /// TMDB movie box sets, which use the ordinary paginated `items` wall.
    public private(set) var householdCollections = [MediaItem]()
    public private(set) var householdCollectionsError: String?
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
    public private(set) var isCatalogLoading = false
    public private(set) var libraryAlphabet = [LibraryAlphabetAnchor]()
    public private(set) var availableLibraryCategories = Set<String>()
    public private(set) var requestsAvailable = false
    public private(set) var streamingAvailable = false
    public private(set) var navigationLoadState: NavigationLoadState = .loading
    /// Remote-provider readiness. Saved streaming downloads do not depend on this opt-in.
    public private(set) var nativeStreamingCapabilities: NativeStreamingCapabilities?
    private var navigationGeneration = 0
    private var hasLoadedNavigationCategories = false
    private var lastNavigationLoadState: NavigationLoadState?

    /// Alphabet seeking is available only where a server index can describe the
    /// current complete video wall. Search, recency/year ordering, episodes and
    /// saved-provider channels intentionally keep ordinary pagination.
    public var supportsLibraryAlphabet: Bool {
        guard session != nil,
              currentSearch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              videoSort == .name else { return false }
        return switch videoKind {
        case .all, .movies, .shows, .anime: true
        case .collections, .youtube, .streaming, .episodes: false
        }
    }

    public var featuredItems: [MediaItem] {
        var seen = Set<String>()
        return Array((resumeItems + recentItems).filter { seen.insert($0.id).inserted }.prefix(8))
    }

    let client: LuminaClient
    let metadataCache: MediaMetadataCache?
    private let credentials: CredentialStore
    private let connectivity: LuminaConnectivity
    private struct LibraryQuery: Equatable {
        let search: String
        let kind: VideoBrowseKind
        let sort: VideoSort
        let filters: VideoFilters
        var preloadCatalog = false
    }

    private struct LibrarySnapshot {
        let query: LibraryQuery
        let items: [MediaItem]
        let householdCollections: [MediaItem]
        let householdCollectionsError: String?
        let nextIndex: Int
        let hasMore: Bool
        let parentID: String?
        let sourceProvider: String?
        let category: String?
        let alphabet: [LibraryAlphabetAnchor]
    }

    private var loadedLibraryQuery: LibraryQuery?
    private var librarySnapshots: [LibrarySnapshot] = []
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
    private var homeTaskCoverage = Set<HomeShelfID>()
    private var libraryTask: Task<Void, Never>?
    private var libraryTaskQuery: LibraryQuery?
    private var libraryAlphabetTask: Task<Void, Never>?
    private var catalogTask: Task<Void, Never>?
    private var catalogBackgrounded = false
    private var catalogEnabled = false
    private var isConnectivityRefreshInFlight = false
    private var nextIndex = 0
    private var libraryGeneration = 0
    private var readyArtworkFramings = [URL: HeroFraming]()
    private var readyArtworkFramingOrder = [URL]()
    private let maximumReadyArtworkFramings = 80
    /// Tickets are scoped to this native session only. They are never persisted or shared with
    /// other app instances, so signing out cannot revoke another member's Cast session.
    private var issuedChromecastTicketIDs = Set<UUID>()
    private var issuedMusicReceiverTicketIDs = Set<UUID>()

    public init(playbackPreferences: NativePlaybackPreferences = NativePlaybackPreferences(),
                connectivity: LuminaConnectivity = LuminaConnectivity(),
                offline: LuminaOfflineLibrary = LuminaOfflineLibrary()) {
        metadataCache = MediaMetadataCache()
        credentials = KeychainCredentials()
        client = LuminaClient()
        self.connectivity = connectivity
        self.offline = offline
        self.playbackPreferences = playbackPreferences
        configureConnectivity()
    }

    init(client: LuminaClient, credentials: CredentialStore,
         metadataCache: MediaMetadataCache? = nil,
         playbackPreferences: NativePlaybackPreferences = NativePlaybackPreferences(),
         connectivity: LuminaConnectivity = LuminaConnectivity(),
         offline: LuminaOfflineLibrary = LuminaOfflineLibrary()) {
        self.client = client
        self.credentials = credentials
        self.metadataCache = metadataCache
        self.connectivity = connectivity
        self.offline = offline
        self.playbackPreferences = playbackPreferences
        configureConnectivity()
    }

    private func configureConnectivity() {
        connectivityStatus = connectivity.status
        connectivity.onChange = { [weak self] status in
            self?.didChangeConnectivity(status)
        }
    }

    public func connect(pairingCode: String, appName: String) async {
        guard canStartPairing() else { return }
        do {
            try await connect(pairing: MobilePairingPayload(scannedCode: pairingCode), appName: appName)
        } catch {
            errorMessage = Self.pairingMessage(for: error)
        }
    }

    public func connect(pairingLink: URL, appName: String) async {
        guard canStartPairing() else { return }
        do {
            try await connect(pairing: MobilePairingPayload(deepLink: pairingLink), appName: appName)
        } catch {
            errorMessage = Self.pairingMessage(for: error)
        }
    }

    private func connect(pairing payload: MobilePairingPayload, appName: String) async throws {
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        let claimed = try await client.claimMobileConnect(origin: payload.serverURL, token: payload.token, name: appName)
        let returnedOrigin = try ServerOrigin(claimed.serverURL).url
        guard sameOrigin(payload.serverURL, returnedOrigin) else { throw LuminaError.unsafeURL }
        _ = try await client.publicInfo(origin: returnedOrigin)
        let deviceID = try credentials.deviceID()
        let signedIn = try await client.authenticate(origin: returnedOrigin, username: claimed.username, password: claimed.password, deviceID: deviceID)
        let checked = try await client.me(origin: returnedOrigin, token: signedIn.token, deviceID: deviceID)
        let saved = LuminaSession(serverURL: returnedOrigin, accessToken: signedIn.token, userID: checked.id,
                                  username: checked.name, serverName: signedIn.serverName, deviceID: deviceID)
        try credentials.save(session: saved)
        session = saved
        await refresh()
    }

    /// Begins an unauthenticated, short-lived pairing exchange for a TV. The device secret is
    /// deliberately retained only by the TV caller and is never stored in this phone's session.
    public func beginTVPairing(server: String, deviceName: String) async throws -> TVPairingRequest {
        guard session == nil else { throw LuminaError.notSignedIn }
        let origin = try ServerOrigin(server).url
        let deviceID = try credentials.deviceID()
        return try await client.startTVPairing(origin: origin, deviceID: deviceID, deviceName: deviceName)
    }

    public func tvPairingStatus(server: String, request: TVPairingRequest) async throws -> TVPairingStatus {
        let origin = try ServerOrigin(server).url
        let deviceID = try credentials.deviceID()
        return try await client.tvPairingStatus(origin: origin, deviceID: deviceID, request: request)
    }

    /// Claims approved TV credentials only after normal server validation. A failed claim is not
    /// retried here: claims are single-use and the TV must begin a fresh exchange when consumed.
    public func completeTVPairing(server: String, request: TVPairingRequest) async throws {
        guard session == nil else { throw LuminaError.notSignedIn }
        try Task.checkCancellation()
        let origin = try ServerOrigin(server).url
        let deviceID = try credentials.deviceID()
        let claim = try await client.claimTVPairing(origin: origin, deviceID: deviceID, request: request)
        try Task.checkCancellation()
        _ = try await client.publicInfo(origin: origin)
        try Task.checkCancellation()
        let signedIn = try await client.authenticate(origin: origin, username: claim.username,
                                                     password: claim.password, deviceID: deviceID)
        try Task.checkCancellation()
        let checked = try await client.me(origin: origin, token: signedIn.token, deviceID: deviceID)
        try Task.checkCancellation()
        guard session == nil else { throw LuminaError.notSignedIn }
        let saved = LuminaSession(serverURL: origin, accessToken: signedIn.token, userID: checked.id,
                                  username: checked.name, serverName: signedIn.serverName, deviceID: deviceID)
        try credentials.save(session: saved)
        session = saved
        await refresh()
    }

    public func inspectTVPairing(code: String) async throws -> TVPairingInspection {
        guard let active = session else { throw LuminaError.notSignedIn }
        let inspection = try await client.inspectTVPairing(origin: active.serverURL, token: active.accessToken,
                                                           deviceID: active.deviceID, code: code)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return inspection
    }

    public func approveTVPairing(code: String) async throws {
        guard let active = session else { throw LuminaError.notSignedIn }
        try await client.approveTVPairing(origin: active.serverURL, token: active.accessToken,
                                          deviceID: active.deviceID, code: code)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
    }

    public func denyTVPairing(code: String) async throws {
        guard let active = session else { throw LuminaError.notSignedIn }
        try await client.denyTVPairing(origin: active.serverURL, token: active.accessToken,
                                       deviceID: active.deviceID, code: code)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
    }

    /// Starts the platform monitor once. `restore()` also calls this so a cold offline
    /// launch obtains a durable local library without relying on a view lifetime.
    public func startConnectivityMonitoring() {
        connectivity.start()
    }

    private func didChangeConnectivity(_ status: LuminaConnectivity.Status) {
        guard status == .online else {
            connectivityStatus = .offline
            presentOfflineItems()
            return
        }
        guard session != nil else {
            connectivityStatus = .online
            return
        }
        // Keep the local-only surface in place until the server itself answers.
        // A usable Wi-Fi/cellular route does not necessarily reach a household server.
        Task { [weak self] in await self?.refreshConnectivity() }
    }

    /// Revalidates the retained session when the app returns or a route becomes usable.
    /// An unreachable server becomes offline; authentication failures still clear the session.
    public func refreshConnectivity() async {
        guard connectivity.status != .offline,
              !isConnectivityRefreshInFlight,
              let active = session else { return }
        isConnectivityRefreshInFlight = true
        defer { isConnectivityRefreshInFlight = false }
        do {
            let checked = try await client.me(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard isCurrent(active) else { return }
            guard connectivity.status != .offline else {
                connectivityStatus = .offline
                presentOfflineItems()
                return
            }
            session = LuminaSession(serverURL: active.serverURL, accessToken: active.accessToken,
                                    userID: checked.id, username: checked.name, serverName: active.serverName,
                                    deviceID: active.deviceID)
            connectivityStatus = .online
            await refresh()
        } catch {
            guard isCurrent(active) else { return }
            if let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) {
                clearSession()
                errorMessage = Self.message(for: error, signingIn: false)
            } else {
                connectivityStatus = .offline
                presentOfflineItems()
            }
        }
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
        // Make retained credentials and their local downloads available before an
        // initial path callback or server validation completes.
        session = saved
        startConnectivityMonitoring()
        guard connectivity.status != .offline else {
            connectivityStatus = .offline
            presentOfflineItems()
            return
        }
        // Do not launch a second `/Users/Me` validation if the initial path
        // callback arrives while this retained-session validation is pending.
        isConnectivityRefreshInFlight = true
        isBusy = true
        errorMessage = nil
        defer {
            isConnectivityRefreshInFlight = false
            isBusy = false
        }
        do {
            let checked = try await client.me(origin: saved.serverURL, token: saved.accessToken, deviceID: saved.deviceID)
            guard isCurrent(saved) else { return }
            guard connectivity.status != .offline else {
                connectivityStatus = .offline
                presentOfflineItems()
                return
            }
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
                connectivityStatus = .offline
                presentOfflineItems()
            }
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func signOut() async {
        let active = session
        let issuedTickets = issuedChromecastTicketIDs
        let issuedMusicTickets = issuedMusicReceiverTicketIDs
        issuedChromecastTicketIDs.removeAll()
        issuedMusicReceiverTicketIDs.removeAll()
        // A pending ticket mint resumes after its network await. Clear ownership
        // before the first revoke so that completion cannot register a new ticket
        // after sign-out; it will revoke its own stale capability instead.
        clearSession()
        if let active {
            for ticketID in issuedTickets {
                try? await client.revokeChromecast(origin: active.serverURL, token: active.accessToken,
                                                    deviceID: active.deviceID, ticketID: ticketID)
            }
            for ticketID in issuedMusicTickets {
                try? await client.revokeMusicReceiver(origin: active.serverURL, token: active.accessToken,
                                                       deviceID: active.deviceID, ticketID: ticketID)
            }
        }
        guard let active else { return }
        try? await client.logout(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
    }

    func clearSession() {
        homeTask?.cancel()
        homeTask = nil
        homeTaskCoverage = []
        cancelCatalogPreload()
        libraryTask?.cancel()
        libraryTask = nil
        libraryTaskQuery = nil
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

    /// Reenter a warm wall without refetching page one or discarding its pagination.
    /// Pull-to-refresh and account/activity refreshes explicitly request fresh server state.
    public func loadLibrary(search: String = "", kind: VideoBrowseKind = .all, sort: VideoSort = .name,
                            filters: VideoFilters = .init(), forceRefresh: Bool = false, preloadCatalog: Bool = false) async {
        guard !Task.isCancelled, session != nil else { return }
        let query = LibraryQuery(search: search, kind: kind, sort: sort, filters: filters,
                                 preloadCatalog: preloadCatalog && search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && [.all, .movies, .shows, .anime].contains(kind))
        if !forceRefresh, let loading = libraryTask, libraryTaskQuery == query {
            await loading.value
            return
        }
        if !forceRefresh, !isOffline, loadedLibraryQuery == query, errorMessage == nil {
            startCatalogPreload()
            return
        }
        if !isOffline { retainLibrarySnapshot() }
        if !forceRefresh, !isOffline, errorMessage == nil,
           let snapshot = librarySnapshots.last(where: { $0.query == query }) {
            cancelCatalogPreload()
            libraryTask?.cancel()
            libraryTask = nil
            libraryTaskQuery = nil
            libraryAlphabetTask?.cancel()
            libraryAlphabetTask = nil
            libraryGeneration += 1
            currentSearch = search
            videoKind = kind
            videoSort = sort
            videoFilters = filters
            videoParentID = snapshot.parentID
            videoSourceProvider = snapshot.sourceProvider
            videoLuminaCategory = snapshot.category
            nextIndex = snapshot.nextIndex
            hasMore = snapshot.hasMore
            libraryAlphabet = snapshot.alphabet
            items = snapshot.items
            householdCollections = snapshot.householdCollections
            householdCollectionsError = snapshot.householdCollectionsError
            loadedLibraryQuery = query
            catalogEnabled = query.preloadCatalog
            errorMessage = nil
            isBusy = false
            startCatalogPreload()
            return
        }
        await loadLibrary(search: search, kind: kind, sort: sort, filters: filters, clearsError: true, preloadCatalog: query.preloadCatalog)
    }

    private func retainLibrarySnapshot() {
        guard let query = loadedLibraryQuery else { return }
        librarySnapshots.removeAll { $0.query == query }
        librarySnapshots.append(LibrarySnapshot(query: query, items: items, householdCollections: householdCollections,
            householdCollectionsError: householdCollectionsError, nextIndex: nextIndex,
            hasMore: hasMore, parentID: videoParentID, sourceProvider: videoSourceProvider,
            category: videoLuminaCategory, alphabet: libraryAlphabet))
        while librarySnapshots.count > 8 || librarySnapshots.reduce(0, { $0 + $1.items.count }) > 10_000 {
            librarySnapshots.removeFirst()
        }
    }

    /// Feature-detects the server-side technical movie index only when the UI is
    /// about to need it. It is deliberately rechecked on an explicit retry, so a
    /// server upgraded in place can enable the control without restarting the app.
    public func supportsTechnicalVideoFilters() async throws -> Bool {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard !isOffline else { return false }
        do {
            let supported = try await client.supportsTechnicalVideoFilters(
                origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID
            )
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            return supported
        } catch {
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            if let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) {
                clearSession()
            }
            if error as? LuminaError == .http(404) { return false }
            throw error
        }
    }

    /// Refreshes the server's remote-streaming readiness without changing the current library wall.
    @discardableResult public func loadNativeStreamingCapabilities() async -> NativeStreamingCapabilities? {
        guard let active = session, !isOffline else { return nil }
        do {
            let capabilities = try await client.nativeStreamingCapabilities(
                origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID
            )
            guard isCurrent(active) else { return nil }
            nativeStreamingCapabilities = capabilities
            return capabilities
        } catch {
            guard isCurrent(active) else { return nil }
            if Self.isAuthenticationFailure(error) { clearSession() }
            nativeStreamingCapabilities = nil
            return nil
        }
    }

    public func prepareNativeRemotePlayback(sourceURL: URL, supportedProfiles: [String] = []) async throws -> NativeRemotePlayback {
        guard let active = session else { throw LuminaError.notSignedIn }
        let playback = try await client.startNativeRemotePlayback(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
            sourceURL: sourceURL, supportedProfiles: supportedProfiles
        )
        guard isCurrent(active) else {
            try? await client.stopNativeRemotePlayback(
                origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, sessionID: playback.sessionID
            )
            throw LuminaError.notSignedIn
        }
        return playback
    }

    /// The owning session is captured so a logout cannot try to release it using another member's token.
    public func stopNativeRemotePlayback(_ playback: NativeRemotePlayback, session owningSession: LuminaSession? = nil) async {
        let active = owningSession ?? session
        guard let active else { return }
        try? await client.stopNativeRemotePlayback(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, sessionID: playback.sessionID
        )
    }

    /// A bounded, independent sample that leaves the current Library wall intact.
    public func loadStandbyArtwork() async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            try Task.checkCancellation()
            let page = try await client.items(origin: active.serverURL, token: active.accessToken,
                deviceID: active.deviceID, search: "", start: 0, types: "Movie,Series", sort: .recentlyAdded, limit: 50)
            try Task.checkCancellation()
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            return page.items.filter {
                ["Movie", "Series"].contains($0.type) && (backdropURL(for: $0) != nil || imageURL(for: $0) != nil)
            }
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    /// Actor-scoped catalog reads keep the current Library wall and its navigation state intact.
    public func titles(featuring person: MediaPerson) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        var result: [MediaItem] = []
        var start = 0
        do {
            while true {
                try Task.checkCancellation()
                let page = try await client.items(origin: active.serverURL, token: active.accessToken,
                    deviceID: active.deviceID, search: "", start: start, types: "Movie,Series", person: person)
                guard isCurrent(active) else { throw LuminaError.notSignedIn }
                result.append(contentsOf: page.items)
                start += page.items.count
                if page.items.isEmpty || start >= page.total { return result }
            }
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    /// Searches without replacing the current Library wall, filters, or alphabet anchors.
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

    private func loadLibrary(search: String, kind: VideoBrowseKind, sort: VideoSort, filters: VideoFilters, clearsError: Bool, preloadCatalog: Bool = false) async {
        guard !Task.isCancelled, session != nil else { return }
        cancelCatalogPreload()
        libraryTask?.cancel()
        libraryAlphabetTask?.cancel()
        libraryAlphabetTask = nil
        libraryGeneration += 1
        let generation = libraryGeneration
        catalogEnabled = preloadCatalog
        libraryTaskQuery = LibraryQuery(search: search, kind: kind, sort: sort, filters: filters, preloadCatalog: preloadCatalog)
        isBusy = true
        if clearsError { errorMessage = nil }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performLibraryLoad(search: search, kind: kind, sort: sort, filters: filters, generation: generation)
        }
        libraryTask = task
        defer {
            if generation == libraryGeneration {
                isBusy = false
                libraryTask = nil
                libraryTaskQuery = nil
                startCatalogPreload()
            }
        }
        // This request belongs to the current catalog query, not an appearance task
        // that NavigationStack may cancel while revealing its retained root.
        await task.value
    }

    private func performLibraryLoad(search: String, kind: VideoBrowseKind, sort: VideoSort, filters: VideoFilters, generation: Int) async {
        guard !Task.isCancelled, generation == libraryGeneration, let active = session else { return }
        guard !isOffline else { presentOfflineItems(); return }
        loadedLibraryQuery = nil
        do {
            currentSearch = search
            videoKind = kind
            videoSort = sort
            videoFilters = filters
            videoParentID = nil
            videoSourceProvider = nil
            videoLuminaCategory = kind.luminaCategory
            libraryAlphabet = []
            if kind == .shows || kind == .youtube || kind == .streaming {
                let views = try await client.views(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
                guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
                videoParentID = switch kind {
                case .shows:
                    views.items.first { $0.name == "Shows" && $0.collectionType == "tvshows" }?.id
                case .youtube:
                    views.items.first { $0.name == "Channels" }?.id
                case .streaming:
                    views.items.first { $0.name == "Channels" }?.id
                default:
                    nil
                }
                if videoParentID == nil { items = []; hasMore = false; nextIndex = 0; return }
                if kind == .youtube { videoSourceProvider = "youtube" }
                if kind == .streaming { videoSourceProvider = "youtube,twitch,kick" }
            }
            async let householdTask: HouseholdCollectionLoad = kind == .collections
                ? loadHouseholdCollections(active, search: search)
                : .empty
            if kind == .collections {
                async let franchiseTask = loadFranchiseCollections(active, search: search, sort: sort, filters: filters)
                let (franchises, household) = await (franchiseTask, householdTask)
                guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
                if case let .failure(error) = franchises, Self.isAuthenticationFailure(error) {
                    handleRequestFailure(error, for: active)
                    return
                }
                if let failure = household.failure, Self.isAuthenticationFailure(failure) {
                    handleRequestFailure(failure, for: active)
                    return
                }
                householdCollections = household.items
                householdCollectionsError = household.errorMessage
                switch franchises {
                case let .success(page):
                    items = page.items.filter { $0.type == "BoxSet" }
                    nextIndex = (page.startIndex ?? 0) + page.items.count
                    hasMore = !page.items.isEmpty && nextIndex < page.total
                case let .failure(error):
                    items = []
                    nextIndex = 0
                    hasMore = false
                    errorMessage = Self.message(for: error, signingIn: false)
                }
                loadedLibraryQuery = LibraryQuery(search: search, kind: kind, sort: sort, filters: filters, preloadCatalog: false)
                return
            }
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: search, start: 0, types: kind.itemTypes, sort: sort, parentID: videoParentID, sourceProvider: videoSourceProvider, luminaCategory: videoLuminaCategory, filters: filters, catalog: catalogEnabled)
            let household = await householdTask
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
            items = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            householdCollections = household.items
            householdCollectionsError = household.errorMessage
            loadedLibraryQuery = LibraryQuery(search: search, kind: kind, sort: sort, filters: filters, preloadCatalog: catalogEnabled)
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
            handleRequestFailure(error, for: active)
        }
    }

    private struct HouseholdCollectionLoad {
        let items: [MediaItem]
        let errorMessage: String?
        let failure: Error?
        static let empty = Self(items: [], errorMessage: nil, failure: nil)
    }

    private func loadFranchiseCollections(_ active: LuminaSession, search: String, sort: VideoSort, filters: VideoFilters) async -> Result<ItemPage, Error> {
        do {
            return .success(try await client.items(
                origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                search: search, start: 0, types: VideoBrowseKind.collections.itemTypes, sort: sort, filters: filters
            ))
        } catch {
            return .failure(error)
        }
    }

    private func loadHouseholdCollections(_ active: LuminaSession, search: String) async -> HouseholdCollectionLoad {
        do {
            let views = try await client.views(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard let playlists = views.items.first(where: { $0.collectionType == "playlists" }) else { return .empty }
            var loaded = [MediaItem]()
            var start = 0
            while !Task.isCancelled {
                let page = try await client.items(
                    origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                    search: "", start: start, types: "Playlist", sort: .name, parentID: playlists.id, limit: 500
                )
                loaded.append(contentsOf: page.items.filter { $0.type == "Playlist" })
                start = (page.startIndex ?? start) + page.items.count
                if page.items.isEmpty || start >= page.total { break }
            }
            let term = search.trimmingCharacters(in: .whitespacesAndNewlines)
            return HouseholdCollectionLoad(items: term.isEmpty ? loaded : loaded.filter { $0.name.localizedCaseInsensitiveContains(term) }, errorMessage: nil, failure: nil)
        } catch {
            return HouseholdCollectionLoad(items: [], errorMessage: Self.message(for: error, signingIn: false), failure: error)
        }
    }

    /// Independent picker data: loading collection choices never replaces the active movie/show wall.
    public func collectionFilterOptions() async throws -> [MediaItem] {
        guard let active = session, !isOffline else { return [] }
        async let household = loadHouseholdCollections(active, search: "")
        var franchises = [MediaItem]()
        var start = 0
        while !Task.isCancelled {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                                              search: "", start: start, types: "BoxSet", limit: 500)
            guard isCurrent(active) else { throw CancellationError() }
            franchises.append(contentsOf: page.items)
            start = (page.startIndex ?? start) + page.items.count
            if page.items.isEmpty || start >= page.total { break }
        }
        let lists = await household
        guard !Task.isCancelled, isCurrent(active) else { throw CancellationError() }
        if let failure = lists.failure { throw failure }
        return (franchises + lists.items).sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    public func loadMore() async {
        if let catalogTask { await catalogTask.value; return }
        if catalogEnabled { errorMessage = nil }
        await loadMoreLibraryPage()
        startCatalogPreload()
    }

    private func cancelCatalogPreload() {
        catalogTask?.cancel()
        catalogTask = nil
        isCatalogLoading = false
    }

    /// Catalog work belongs to the query, survives pushing a detail, and stops at suspension.
    public func setCatalogBackgrounded(_ backgrounded: Bool) {
        catalogBackgrounded = backgrounded
        if backgrounded { cancelCatalogPreload() }
        else { startCatalogPreload() }
    }

    private func startCatalogPreload() {
        guard catalogEnabled, loadedLibraryQuery?.preloadCatalog == true, !catalogBackgrounded, !isOffline, hasMore,
              !isBusy, catalogTask == nil, errorMessage == nil else { return }
        let generation = libraryGeneration
        isCatalogLoading = true
        catalogTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if generation == self.libraryGeneration, !Task.isCancelled {
                    self.catalogTask = nil
                    self.isCatalogLoading = false
                }
            }
            while !Task.isCancelled, generation == self.libraryGeneration, self.hasMore {
                let start = self.nextIndex
                await self.loadMoreLibraryPage()
                guard self.nextIndex > start else { return }
            }
        }
    }

    private func loadMoreLibraryPage() async {
        guard !Task.isCancelled, let active = session, !isOffline, hasMore, !isBusy else { return }
        let generation = libraryGeneration
        let start = nextIndex
        isBusy = true
        defer {
            if generation == libraryGeneration {
                isBusy = false
                if Task.isCancelled { startCatalogPreload() }
            }
        }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: currentSearch, start: start, types: videoKind.itemTypes, sort: videoSort, parentID: videoParentID, sourceProvider: videoSourceProvider, luminaCategory: videoLuminaCategory, filters: videoFilters, catalog: catalogEnabled, limit: 500)
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
            let playable = page.items.filter { ($0.isPlayable || $0.isCollection) && !$0.isAudio }
            items.append(contentsOf: playable)
            nextIndex = (page.startIndex ?? start) + page.items.count
            hasMore = !page.items.isEmpty && nextIndex < page.total
        } catch {
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active) else { return }
            handleRequestFailure(error, for: active)
        }
    }

    /// Full catalogs seek within the retained wall; paged clients fetch an anchor page.
    /// The returned id lets a platform focus or scroll to the requested card.
    @discardableResult
    public func jumpLibrary(to anchor: LibraryAlphabetAnchor) async -> String? {
        guard let active = session,
              supportsLibraryAlphabet,
              anchor.index >= 0,
              libraryAlphabet.contains(anchor) else { return nil }
        if catalogEnabled {
            guard !Task.isCancelled else { return nil }
            if anchor.index < items.count { return items[anchor.index].id }
            let generation = libraryGeneration
            if let catalogTask { await catalogTask.value }
            guard !Task.isCancelled, generation == libraryGeneration, isCurrent(active),
                  anchor.index < items.count else { return nil }
            return items[anchor.index].id
        }
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
        librarySnapshots.removeAll()
        guard !isOffline else { presentOfflineItems(); return }
        errorMessage = nil
        async let home: Void = loadHome(clearsError: false)
        await loadLibrary(search: currentSearch, kind: videoKind, sort: videoSort, filters: videoFilters, clearsError: false, preloadCatalog: catalogEnabled)
        await home
    }

    public func loadHome() async {
        await loadHome(clearsError: true)
    }

    /// Returns compatibility shelves that still need their legacy endpoint after
    /// a rich Home request. Retained rich rows are intentionally not treated as
    /// fresh completion facts when that request failed.
    @discardableResult public func reconcileRichHomeRows(_ rows: [HomeFeedRow], completedShelfIDs: Set<HomeShelfID>?,
                                                         loadSucceeded: Bool, expectedCompatibilityShelves: Set<HomeShelfID>) async -> Set<HomeShelfID> {
        let expected = expectedCompatibilityShelves.intersection(Self.compatibilityHomeShelves)
        guard loadSucceeded else { return expected }
        return await adoptRichHomeRows(rows, completedShelfIDs: completedShelfIDs,
                                       expectedCompatibilityShelves: expected)
    }

    /// Accepts compatibility shelves the rich, member-scoped Home response has
    /// explicitly completed. The balanced rich `new_in_library` title row is
    /// deliberately not substituted for legacy `recentItems`.
    @discardableResult public func adoptRichHomeRows(_ rows: [HomeFeedRow], completedShelfIDs: Set<HomeShelfID>?,
                                                     expectedCompatibilityShelves: Set<HomeShelfID>) async -> Set<HomeShelfID> {
        let expected = expectedCompatibilityShelves.intersection(Self.compatibilityHomeShelves)
        guard !Task.isCancelled, let active = session, !isOffline else { return expected }
        homeGeneration += 1
        let generation = homeGeneration
        homeTask?.cancel()
        homeTask = nil
        homeTaskCoverage = []
        isHomeBusy = false

        // Servers without completion facts retain legacy fallback rather than
        // guessing whether an omitted row is empty or incomplete.
        let completed = (completedShelfIDs ?? []).intersection(expected)
        let rowsByShelf = Dictionary(grouping: rows, by: \.shelfID)
        var missing = expected.subtracting(completed)

        if completed.contains(.continueWatching) {
            resumeItems = (rowsByShelf[.continueWatching]?.first?.items ?? []).filter { $0.isPlayable && !$0.isAudio }
            missing.remove(.continueWatching)
            await metadataCache?.store(resumeItems, for: MediaMetadataCache.key("home/resumeItems", session: active), lifetime: 24 * 60 * 60)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return expected }
        }
        if completed.contains(.nextUp) {
            nextUpItems = (rowsByShelf[.nextUp]?.first?.items ?? []).filter { $0.type == "Episode" }
            missing.remove(.nextUp)
            await metadataCache?.store(nextUpItems, for: MediaMetadataCache.key("home/nextUpItems", session: active), lifetime: 24 * 60 * 60)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return expected }
        }
        if completed.contains(.newInLibrary) { missing.remove(.newInLibrary) }

        guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return expected }
        return missing
    }

    /// Fetches only legacy shelves not supplied by a completed rich Home response.
    /// Navigation remains independently fresh through the Home presentation.
    public func loadHomeFallback(for missingShelves: Set<HomeShelfID>) async {
        let compatible = missingShelves.intersection(Self.compatibilityHomeShelves)
        guard !compatible.isEmpty else { return }
        // Store restoration and connectivity refresh already load every
        // compatibility shelf. Joining that in-flight work prevents a Home view
        // admission race from cancelling and reissuing the same three requests.
        if let homeTask {
            let covered = homeTaskCoverage
            await homeTask.value
            guard !Task.isCancelled else { return }
            let remaining = compatible.subtracting(covered)
            guard !remaining.isEmpty else { return }
            await loadHome(clearsError: false, compatibilityShelves: remaining, includesNavigation: false)
            return
        }
        await loadHome(clearsError: false, compatibilityShelves: compatible, includesNavigation: false)
    }

    private func presentOfflineItems() {
        librarySnapshots.removeAll()
        loadedLibraryQuery = nil
        let downloads = offlineVideoItems
        items = downloads
        recentItems = downloads
        resumeItems = downloads.filter { offline.resumeSeconds(for: $0.id) > 0 }
        nextUpItems = downloads.filter { $0.type == "Episode" }
        hasMore = false
        nextIndex = downloads.count
        libraryAlphabet = []
        isBusy = false
        isHomeBusy = false
        errorMessage = nil
    }

    private static let compatibilityHomeShelves: Set<HomeShelfID> = [.continueWatching, .nextUp, .newInLibrary]

    private func loadHome(clearsError: Bool, compatibilityShelves: Set<HomeShelfID> = [.continueWatching, .nextUp, .newInLibrary],
                          includesNavigation: Bool = true) async {
        guard !Task.isCancelled else { return }
        if homeGeneration > 0 { metadataCache?.clear() }
        homeGeneration += 1
        let generation = homeGeneration
        homeTask?.cancel()
        homeTaskCoverage = compatibilityShelves.intersection(Self.compatibilityHomeShelves)
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performHomeLoad(generation: generation, clearsError: clearsError,
                                       compatibilityShelves: compatibilityShelves, includesNavigation: includesNavigation)
        }
        homeTask = task
        defer {
            if generation == homeGeneration {
                isHomeBusy = false
                homeTask = nil
                homeTaskCoverage = []
            }
        }
        await withTaskCancellationHandler(operation: {
            await task.value
        }, onCancel: {
            task.cancel()
        })
    }

    private func performHomeLoad(generation: Int, clearsError: Bool, compatibilityShelves: Set<HomeShelfID>,
                                 includesNavigation: Bool) async {
        guard !Task.isCancelled, let active = session, generation == homeGeneration else { return }
        guard !isOffline else { presentOfflineItems(); return }
        isHomeBusy = true
        if clearsError { errorMessage = nil }
        async let recent: Void = loadHomeRecent(active, generation: generation,
                                                 isNeeded: compatibilityShelves.contains(.newInLibrary))
        async let resume: Void = loadHomeResume(active, generation: generation,
                                                 isNeeded: compatibilityShelves.contains(.continueWatching))
        async let nextUp: Void = loadHomeNextUp(active, generation: generation,
                                                 isNeeded: compatibilityShelves.contains(.nextUp))
        async let navigation: Void = loadNavigationCategories(active, isNeeded: includesNavigation)
        _ = await (recent, resume, nextUp, navigation)
    }

    private func loadNavigationCategories(_ active: LuminaSession, isNeeded: Bool = true) async {
        guard isNeeded else { return }
        navigationGeneration += 1
        let requestGeneration = navigationGeneration
        navigationLoadState = .loading
        do {
            let navigation = try await client.navigationCategories(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            // Navigation is an independent member/session resource. A concurrent
            // compatibility-shelf load can advance homeGeneration while this
            // request is in flight; that must not discard a still-current
            // navigation response and leave the app without Library destinations.
            guard requestGeneration == navigationGeneration, isCurrent(active) else { return }
            guard !Task.isCancelled else {
                restoreNavigationLoadStateAfterCancellation()
                return
            }
            availableLibraryCategories = Set(navigation.categories)
            requestsAvailable = navigation.features?.requests ?? false
            streamingAvailable = navigation.features?.streaming ?? false
            navigationLoadState = .loaded
            hasLoadedNavigationCategories = true
            lastNavigationLoadState = .loaded
        } catch {
            guard requestGeneration == navigationGeneration, isCurrent(active) else { return }
            guard !Task.isCancelled else {
                restoreNavigationLoadStateAfterCancellation()
                return
            }
            if navigationAuthenticationFailed(error) || !hasLoadedNavigationCategories {
                availableLibraryCategories = []
                requestsAvailable = false
                streamingAvailable = false
                hasLoadedNavigationCategories = false
            }
            navigationLoadState = .failed
            lastNavigationLoadState = .failed
        }
    }

    public func refreshNavigationCategories() async {
        guard let active = session else { return }
        await loadNavigationCategories(active)
    }

    private func navigationAuthenticationFailed(_ error: Error) -> Bool {
        switch error as? LuminaError {
        case .http(401), .http(403), .notSignedIn:
            true
        default:
            false
        }
    }

    private func restoreNavigationLoadStateAfterCancellation() {
        navigationLoadState = lastNavigationLoadState ?? .failed
    }

    private func loadHomeRecent(_ active: LuminaSession, generation: Int, isNeeded: Bool = true) async {
        guard isNeeded else { return }
        let cacheKey = MediaMetadataCache.key("home/recentItems", session: active)
        if recentItems.isEmpty, let cached = await metadataCache?.items(for: cacheKey),
           !Task.isCancelled, generation == homeGeneration, isCurrent(active) { recentItems = cached }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: "", start: 0, sort: .recentlyAdded)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            recentItems = page.items.filter { $0.isPlayable && !$0.isAudio }
            await metadataCache?.store(recentItems, for: cacheKey, lifetime: 24 * 60 * 60)
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            handleRequestFailure(error, for: active)
        }
    }

    private func loadHomeResume(_ active: LuminaSession, generation: Int, isNeeded: Bool = true) async {
        guard isNeeded else { return }
        let cacheKey = MediaMetadataCache.key("home/resumeItems", session: active)
        if resumeItems.isEmpty, let cached = await metadataCache?.items(for: cacheKey),
           !Task.isCancelled, generation == homeGeneration, isCurrent(active) { resumeItems = cached }
        do {
            let items = try await client.resume(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            resumeItems = items.filter { $0.isPlayable && !$0.isAudio }
            await metadataCache?.store(resumeItems, for: cacheKey, lifetime: 24 * 60 * 60)
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            handleRequestFailure(error, for: active)
        }
    }

    private func loadHomeNextUp(_ active: LuminaSession, generation: Int, isNeeded: Bool = true) async {
        guard isNeeded else { return }
        let cacheKey = MediaMetadataCache.key("home/nextUpItems", session: active)
        if nextUpItems.isEmpty, let cached = await metadataCache?.items(for: cacheKey),
           !Task.isCancelled, generation == homeGeneration, isCurrent(active) { nextUpItems = cached }
        do {
            let items = try await client.nextUp(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            nextUpItems = items.filter { $0.type == "Episode" }
            await metadataCache?.store(nextUpItems, for: cacheKey, lifetime: 24 * 60 * 60)
        } catch {
            guard !Task.isCancelled, generation == homeGeneration, isCurrent(active) else { return }
            handleRequestFailure(error, for: active)
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

    func replaceCachedItem(_ fresh: MediaItem) {
        librarySnapshots.removeAll()
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

    public func clearError() { errorMessage = nil }

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

    public func prepareChromecast(itemID: String, positionSeconds: Double, audioIndex: Int? = nil,
                                  subtitle: String? = nil) async throws -> ChromecastTicket {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        let ticket = try await client.prepareChromecast(origin: active.serverURL, token: active.accessToken,
                                                        deviceID: active.deviceID, itemID: itemID,
                                                        positionSeconds: positionSeconds, audioIndex: audioIndex,
                                                        subtitle: subtitle)
        guard isCurrent(active) else {
            try? await client.revokeChromecast(origin: active.serverURL, token: active.accessToken,
                                                deviceID: active.deviceID, ticketID: ticket.ticketID)
            throw LuminaError.notSignedIn
        }
        issuedChromecastTicketIDs.insert(ticket.ticketID)
        return ticket
    }

    public func renewChromecast(ticketID: UUID) async throws {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        try await client.renewChromecast(origin: active.serverURL, token: active.accessToken,
                                         deviceID: active.deviceID, ticketID: ticketID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
    }

    public func revokeChromecast(ticketID: UUID) async throws {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        try await client.revokeChromecast(origin: active.serverURL, token: active.accessToken,
                                          deviceID: active.deviceID, ticketID: ticketID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        issuedChromecastTicketIDs.remove(ticketID)
    }

    /// Mints a short-lived receiver capability for an AAC-compatible music track.
    public func prepareMusicReceiver(itemIDs: [String], currentIndex: Int,
                                     positionSeconds: Double) async throws -> MusicReceiverTicket {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        let ticket = try await client.prepareMusicReceiver(origin: active.serverURL, token: active.accessToken,
                                                           deviceID: active.deviceID, itemIDs: itemIDs, currentIndex: currentIndex,
                                                           positionSeconds: positionSeconds)
        guard isCurrent(active) else {
            try? await client.revokeMusicReceiver(origin: active.serverURL, token: active.accessToken,
                                                   deviceID: active.deviceID, ticketID: ticket.ticket.ticketID)
            throw LuminaError.notSignedIn
        }
        issuedMusicReceiverTicketIDs.insert(ticket.ticket.ticketID)
        return ticket
    }

    public func renewMusicReceiver(ticketID: UUID) async throws {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        try await client.renewMusicReceiver(origin: active.serverURL, token: active.accessToken,
                                            deviceID: active.deviceID, ticketID: ticketID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
    }

    public func revokeMusicReceiver(ticketID: UUID) async throws {
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        try await client.revokeMusicReceiver(origin: active.serverURL, token: active.accessToken,
                                             deviceID: active.deviceID, ticketID: ticketID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        issuedMusicReceiverTicketIDs.remove(ticketID)
    }

    func handleRequestFailure(_ error: Error, for active: LuminaSession) {
        guard isCurrent(active) else { return }
        if Self.isAuthenticationFailure(error) {
            clearSession()
            errorMessage = Self.message(for: error, signingIn: false)
        } else if Self.isConnectionFailure(error) {
            connectivityStatus = .offline
            presentOfflineItems()
        } else {
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    private static func isAuthenticationFailure(_ error: Error) -> Bool {
        guard let failure = error as? LuminaError else { return false }
        return failure == .http(401) || failure == .http(403)
    }

    static func isConnectionFailure(_ error: Error) -> Bool {
        let nsError = error as NSError
        guard nsError.domain == NSURLErrorDomain else { return false }
        switch URLError.Code(rawValue: nsError.code) {
        case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost,
             .cannotFindHost, .dnsLookupFailed, .timedOut, .internationalRoamingOff,
             .dataNotAllowed, .callIsActive:
            return true
        default:
            return false
        }
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

    private static func pairingMessage(for error: Error) -> String {
        if let error = error as? LuminaError, case .unsafeURL = error {
            return "This code belongs to a different server."
        }
        if error is URLError { return "Cannot reach this server. Check Wi-Fi or VPN." }
        return "That code is unavailable. Scan a new one."
    }

    private func canStartPairing() -> Bool {
        guard session == nil else {
            errorMessage = "Sign out before connecting another server."
            return false
        }
        guard !isBusy else {
            errorMessage = "Connection already in progress."
            return false
        }
        return true
    }

    func isCurrent(_ candidate: LuminaSession) -> Bool {
        session?.serverURL == candidate.serverURL && session?.accessToken == candidate.accessToken && session?.userID == candidate.userID
    }
}
