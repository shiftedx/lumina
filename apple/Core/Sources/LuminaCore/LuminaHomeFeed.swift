import Foundation
import Observation

public enum HomeFeedShape: String, Codable, Sendable {
    case poster
    case still
    case square
}

public struct HomeRecommendation: Codable, Hashable, Sendable {
    public let listID: String
    public let key: String
    public let position: Int
    public let slot: String
    public let reasonCode: String
    public let reason: String

    private enum CodingKeys: String, CodingKey {
        case key, position, slot, reason
        case listID = "list_id"
        case reasonCode = "reason_code"
    }
}

/// The locally available Home refinements. They operate on the loaded Home
/// response and therefore never turn a Home interaction into a catalog scan.
public enum HomeBrowseCategory: String, CaseIterable, Hashable, Sendable {
    case all
    case movies
    case shows
    case anime
    case watchlist

    public var label: String { rawValue.capitalized }
}

public struct HomeRefinement: Hashable, Sendable {
    public var category: HomeBrowseCategory
    public var genre: String?

    public init(category: HomeBrowseCategory = .all, genre: String? = nil) {
        self.category = category
        let normalized = genre?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.genre = normalized?.isEmpty == true ? nil : normalized
    }
}

/// A presentation-safe filtered snapshot. Refining Home does not alter the
/// shared feed, its server-defined row order, or Library pagination state.
public struct HomeRefinementResult: Sendable {
    public let genres: [String]
    public let items: [MediaItem]
    public let rows: [HomeFeedRow]

    public init(genres: [String], items: [MediaItem], rows: [HomeFeedRow]) {
        self.genres = genres
        self.items = items
        self.rows = rows
    }
}

/// One populated, member-scoped Home row. Its items use the ordinary Jellyfin
/// DTO so callers can open and play them through the existing library store.
public struct HomeFeedRow: Codable, Identifiable, Sendable {
    public let id: String
    public let shelfID: HomeShelfID
    public let title: String
    public let shape: HomeFeedShape
    public let items: [MediaItem]
    public let externalItems: [HomeExternalItem]?
    public var hasItems: Bool { !items.isEmpty || !(externalItems ?? []).isEmpty }

    private enum CodingKeys: String, CodingKey {
        case id, title, shape, items
        case externalItems = "external_items"
        case shelfID = "shelf_id"
    }

    fileprivate func replacing(items: [MediaItem], externalItems: [HomeExternalItem]?) -> HomeFeedRow {
        HomeFeedRow(id: id, shelfID: shelfID, title: title, shape: shape, items: items, externalItems: externalItems)
    }
}

private struct HomeSuppression: Decodable {
    let id: String
}

/// Metadata-only handoff; the native session is never sent to a provider.
public struct HomeExternalItem: Codable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let subtitle: String
    private let url: String
    private let artwork: String?

    public var sourceURL: URL? { Self.httpsURL(url) }
    public var artworkURL: URL? { artwork.flatMap(Self.httpsURL) }

    private enum CodingKeys: String, CodingKey {
        case id, title, subtitle, url
        case artwork = "artwork_url"
    }

    private static func httpsURL(_ value: String) -> URL? {
        guard let parts = URLComponents(string: value), parts.scheme == "https",
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil else { return nil }
        return parts.url
    }
}

/// Loads the server's local, playable subset of the website Home surface.
/// Layout stays in ``LuminaHomeLayout`` so a member can share one preference
/// across the web, iPhone, and Apple TV without coupling presentation to data.
@MainActor @Observable public final class LuminaHomeFeed {
    public private(set) var rows = [HomeFeedRow]()
    public private(set) var isLoading = false
    public private(set) var hasLoaded = false
    public private(set) var errorMessage: String?

    private var session: LuminaSession?
    private let client: LuminaClient
    private let includesExternalSources: Bool
    private var sessionGeneration = 0
    private var operationGeneration = 0
    private var seenRecommendations = Set<String>()
    private var hiddenTitleIDs = [String: String]()

    public init(session: LuminaSession? = nil, includesExternalSources: Bool = false) {
        self.includesExternalSources = includesExternalSources
        self.session = session
        client = LuminaClient()
    }

    init(session: LuminaSession?, client: LuminaClient, includesExternalSources: Bool = false) {
        self.includesExternalSources = includesExternalSources
        self.session = session
        self.client = client
    }

    public func configure(session: LuminaSession?) {
        guard self.session?.accessToken != session?.accessToken ||
              self.session?.serverURL != session?.serverURL ||
              self.session?.userID != session?.userID else { return }
        sessionGeneration += 1
        operationGeneration += 1
        self.session = session
        rows = []
        isLoading = false
        hasLoaded = false
        errorMessage = nil
        seenRecommendations = []
        hiddenTitleIDs = [:]
    }

    /// Member-visible genres represented by the currently loaded local Home rows.
    /// Values retain the server's first display spelling and are sorted for a compact picker.
    public var availableGenres: [String] {
        var displayByKey = [String: String]()
        for genre in rows.flatMap(\.items).flatMap({ $0.genres ?? [] }) {
            let trimmed = genre.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            let key = trimmed.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            if displayByKey[key] == nil { displayByKey[key] = trimmed }
        }
        return displayByKey.values.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    /// Filters only the existing member-scoped Home snapshot. A watchlist row
    /// is the source of truth: queued episodes also match their loaded series
    /// cards, while an absent row deliberately yields no watchlist results.
    public func refined(_ refinement: HomeRefinement, heroItems: [MediaItem]) -> HomeRefinementResult {
        let context = HomeRefinementContext(rows: rows)
        let filteredRows = rows.compactMap { row -> HomeFeedRow? in
            let localItems = row.items.filter { context.includes($0, refinement: refinement) }
            let canKeepExternal = refinement.genre == nil && (refinement.category == .all || (refinement.category == .watchlist && row.shelfID == .watchlist))
            let externalItems = canKeepExternal ? row.externalItems : nil
            guard !localItems.isEmpty || !(externalItems ?? []).isEmpty else { return nil }
            return row.replacing(items: localItems, externalItems: externalItems)
        }
        // Watchlist owns its saved versions and their enriched artwork. A related
        // Series card from another Home shelf must not replace or duplicate them.
        let heroCandidates = refinement.category == .watchlist
            ? rows.first(where: { $0.shelfID == .watchlist })?.items ?? [] : heroItems
        let matchingHeroes = heroCandidates.filter { context.includes($0, refinement: refinement) }
        let fallbackHeroes: [MediaItem]
        if refinement.category != .all, refinement.category != .watchlist, matchingHeroes.isEmpty {
            var seen = Set<String>()
            fallbackHeroes = Array(filteredRows.flatMap(\.items).filter { seen.insert($0.id).inserted }.prefix(8))
        } else {
            fallbackHeroes = matchingHeroes
        }
        return HomeRefinementResult(genres: availableGenres, items: fallbackHeroes, rows: filteredRows)
    }

    public func load() async {
        guard !isLoading else { return }
        operationGeneration += 1
        let operation = operationGeneration
        let currentSession = sessionGeneration
        guard let session else {
            if operation == operationGeneration { errorMessage = "Sign in to load Home." }
            return
        }
        isLoading = true
        errorMessage = nil
        defer { if operation == operationGeneration, currentSession == sessionGeneration { isLoading = false } }
        do {
            let response: Response = try await request(session: session)
            guard operation == operationGeneration, currentSession == sessionGeneration else { return }
            rows = withoutHiddenTitles(response.rows)
            hasLoaded = true
        } catch {
            guard operation == operationGeneration, currentSession == sessionGeneration else { return }
            errorMessage = "Home could not be loaded."
        }
    }

    /// Called only after a card is actually visible. The served-list cache
    /// validates both member and key before the event is stored.
    public func record(_ kind: String, recommendation: HomeRecommendation) async {
        guard let session, kind == "impression" || kind == "open" else { return }
        let generation = sessionGeneration
        let identity = "\(recommendation.listID):\(recommendation.key)"
        if kind == "impression" && seenRecommendations.contains(identity) { return }
        if kind == "impression" { seenRecommendations.insert(identity) }
        let body: [String: Any] = ["events": [["kind": kind, "list_id": recommendation.listID,
                                                "key": recommendation.key, "age_ms": 0]]]
        let request = client.request(origin: session.serverURL, path: "/Lumina/Home/reco/events",
                                     method: "POST", token: session.accessToken, deviceID: session.deviceID, body: body)
        do { _ = try await client.send(request) }
        catch {
            if kind == "impression", generation == sessionGeneration { seenRecommendations.remove(identity) }
        }
    }

    @discardableResult public func hide(_ item: MediaItem) async -> String? {
        guard let session, let reco = item.luminaReco else { return nil }
        let generation = sessionGeneration
        let body: [String: Any] = ["scope": "title", "title_id": reco.key,
                                   "key": reco.key, "list_id": reco.listID]
        let request = client.request(origin: session.serverURL, path: "/Lumina/Home/reco/suppressions",
                                     method: "POST", token: session.accessToken, deviceID: session.deviceID, body: body)
        guard let data = try? await client.send(request),
              let response = try? JSONDecoder().decode(HomeSuppression.self, from: data) else { return nil }
        guard generation == sessionGeneration else { return nil }
        hiddenTitleIDs[response.id] = item.id
        rows = withoutHiddenTitles(rows)
        return response.id
    }

    public func undoHide(_ suppressionID: String) async {
        guard let session, hiddenTitleIDs[suppressionID] != nil else { return }
        let generation = sessionGeneration
        let request = client.request(origin: session.serverURL,
                                     path: "/Lumina/Settings/suppressions/\(suppressionID)",
                                     method: "DELETE", token: session.accessToken, deviceID: session.deviceID)
        guard (try? await client.send(request)) != nil else { return }
        guard generation == sessionGeneration else { return }
        hiddenTitleIDs.removeValue(forKey: suppressionID)
        await load()
    }

    private func withoutHiddenTitles(_ rows: [HomeFeedRow]) -> [HomeFeedRow] {
        let hidden = Set(hiddenTitleIDs.values)
        guard !hidden.isEmpty else { return rows }
        return rows.compactMap { row in
            let remaining = row.items.filter { !hidden.contains($0.id) }
            return remaining.isEmpty && (row.externalItems ?? []).isEmpty ? nil
                : row.replacing(items: remaining, externalItems: row.externalItems)
        }
    }

    private func request<Value: Decodable>(session: LuminaSession) async throws -> Value {
        var request = client.request(
            origin: session.serverURL,
            path: "/Lumina/Home/rows",
            query: includesExternalSources ? [URLQueryItem(name: "include_external", value: "true")] : [],
            token: session.accessToken,
            deviceID: session.deviceID
        )
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, response) = try await client.response(request)
            guard (200..<300).contains(response.statusCode) else { throw LuminaError.http(response.statusCode) }
            return try JSONDecoder().decode(Value.self, from: data)
        } catch let error as LuminaError {
            throw error
        } catch {
            throw LuminaError.malformedResponse
        }
    }

    private struct Response: Decodable {
        let rows: [HomeFeedRow]
    }
}


private struct HomeRefinementContext {
    private let watchlistItemIDs: Set<String>
    private let watchlistSeriesIDs: Set<String>
    private let categoryByItemID: [String: String]
    private let genresByItemID: [String: [String]]

    init(rows: [HomeFeedRow]) {
        let queued = rows.first(where: { $0.shelfID == .watchlist })?.items ?? []
        watchlistItemIDs = Set(queued.map(\.id))
        watchlistSeriesIDs = Set(queued.compactMap(\.seriesID))
        var categories = [String: String]()
        var genres = [String: [String]]()
        for item in rows.flatMap(\.items) {
            if let category = item.luminaCategory { categories[item.id] = category }
            if let itemGenres = item.genres, !itemGenres.isEmpty { genres[item.id] = itemGenres }
        }
        categoryByItemID = categories
        genresByItemID = genres
    }

    func includes(_ item: MediaItem, refinement: HomeRefinement) -> Bool {
        guard matchesCategory(item, category: refinement.category), matchesGenre(item, genre: refinement.genre) else { return false }
        return true
    }

    private func matchesCategory(_ item: MediaItem, category: HomeBrowseCategory) -> Bool {
        switch category {
        case .all:
            true
        case .watchlist:
            watchlistItemIDs.contains(item.id) || (item.type == "Series" && watchlistSeriesIDs.contains(item.id))
        case .anime:
            resolvedCategory(for: item) == HomeBrowseCategory.anime.rawValue
        case .movies:
            resolvedCategory(for: item) == HomeBrowseCategory.movies.rawValue
                || (resolvedCategory(for: item) == nil && item.type == "Movie")
        case .shows:
            resolvedCategory(for: item) == HomeBrowseCategory.shows.rawValue
                || (resolvedCategory(for: item) == nil && ["Episode", "Season", "Series"].contains(item.type))
        }
    }

    private func resolvedCategory(for item: MediaItem) -> String? {
        categoryByItemID[item.id] ?? item.luminaCategory
    }

    private func matchesGenre(_ item: MediaItem, genre: String?) -> Bool {
        guard let genre else { return true }
        let itemGenres = genresByItemID[item.id] ?? item.genres ?? []
        return itemGenres.contains { $0.compare(genre, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }
    }
}
