import Foundation
import Observation

public enum RequestCatalogKind: String, Codable, Sendable, CaseIterable {
    case movie
    case show
    case anime
}

public enum RequestMediaType: String, Codable, Sendable {
    case movie
    case tv
}

public enum RequestState: String, Codable, Sendable {
    case none
    case pending
    case approved
    case processing
    case partiallyAvailable = "partially_available"
    case available
    case declined
    case failed

    public var label: String {
        switch self {
        case .none: ""
        case .pending: "Requested"
        case .approved: "Approved"
        case .processing: "Downloading"
        case .partiallyAvailable: "Partly in your vault"
        case .available: "In your vault"
        case .declined: "Declined"
        case .failed: "Needs attention"
        }
    }
}

public enum RequestLanguage: String, Codable, Sendable, CaseIterable {
    case dub
    case sub
}

public enum RequestSeasonSelection: Codable, Sendable, Equatable {
    case all
    case selected([Int])

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let all = try? value.decode(String.self), all == "all" {
            self = .all
        } else {
            self = .selected(try value.decode([Int].self))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .all: try value.encode("all")
        case .selected(let seasons): try value.encode(Array(Set(seasons)).sorted())
        }
    }
}

public struct RequestCatalogStatus: Codable, Hashable, Sendable {
    public let state: RequestState
    public let requestID: String?
    public let progress: Double?
    public let libraryTitleID: String?

    public var label: String {
        guard state == .processing, let progress else { return state.label }
        let percent = min(100, max(0, Int((progress * 100).rounded())))
        return percent >= 100 ? "Downloaded — adding to your vault" : "Downloading \(percent)%"
    }

    public var canCreateRequest: Bool {
        [.none, .declined, .partiallyAvailable].contains(state)
    }

    enum CodingKeys: String, CodingKey {
        case state
        case requestID = "request_id"
        case progress
        case libraryTitleID = "library_title_id"
    }
}

public struct RequestAnimeInfo: Codable, Hashable, Sendable {
    public let season: String?
    public let seasonYear: Int?
    public let format: String?
    public let episodes: Int?
    public let studios: [String]
    public let score: Double?
    public let popularity: Int?
    public let color: String?

    enum CodingKeys: String, CodingKey {
        case season, format, episodes, studios, score, popularity, color
        case seasonYear = "season_year"
    }
}

public struct RequestCatalogItem: Identifiable, Codable, Hashable, Sendable {
    public let id: String
    public let kind: RequestCatalogKind
    public let mediaType: RequestMediaType
    public let tmdbID: Int?
    public let tvdbID: Int?
    public let anilistID: Int?
    public let title: String
    public let originalTitle: String?
    public let year: Int?
    public let overview: String?
    public let releaseDate: String?
    public let posterURL: URL?
    public let backdropURL: URL?
    public let logoURL: URL?
    public let rating: Double?
    public let genres: [String]
    public let anime: RequestAnimeInfo?
    public let status: RequestCatalogStatus
    public let canRequest: Bool
    public let unrequestableReason: String?

    public var catalogID: Int? { kind == .anime ? anilistID : tmdbID }

    enum CodingKeys: String, CodingKey {
        case id = "key"
        case kind
        case mediaType = "media_type"
        case tmdbID = "tmdb_id"
        case tvdbID = "tvdb_id"
        case anilistID = "anilist_id"
        case title
        case originalTitle = "original_title"
        case year
        case overview
        case releaseDate = "release_date"
        case posterURL = "poster_url"
        case backdropURL = "backdrop_url"
        case logoURL = "logo_url"
        case rating
        case genres
        case anime
        case status
        case canRequest = "requestable"
        case unrequestableReason = "unrequestable_reason"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        kind = try values.decode(RequestCatalogKind.self, forKey: .kind)
        mediaType = try values.decode(RequestMediaType.self, forKey: .mediaType)
        tmdbID = try values.decodeIfPresent(Int.self, forKey: .tmdbID)
        tvdbID = try values.decodeIfPresent(Int.self, forKey: .tvdbID)
        anilistID = try values.decodeIfPresent(Int.self, forKey: .anilistID)
        title = try values.decode(String.self, forKey: .title)
        originalTitle = try values.decodeIfPresent(String.self, forKey: .originalTitle)
        year = try values.decodeIfPresent(Int.self, forKey: .year)
        overview = try values.decodeIfPresent(String.self, forKey: .overview)
        releaseDate = try values.decodeIfPresent(String.self, forKey: .releaseDate)
        posterURL = try values.decodeIfPresent(URL.self, forKey: .posterURL)
        backdropURL = try values.decodeIfPresent(URL.self, forKey: .backdropURL)
        logoURL = try values.decodeIfPresent(URL.self, forKey: .logoURL)
        rating = try values.decodeIfPresent(Double.self, forKey: .rating)
        genres = try values.decodeIfPresent([String].self, forKey: .genres) ?? []
        anime = try values.decodeIfPresent(RequestAnimeInfo.self, forKey: .anime)
        status = try values.decode(RequestCatalogStatus.self, forKey: .status)
        canRequest = try values.decodeIfPresent(Bool.self, forKey: .canRequest) ?? true
        unrequestableReason = try values.decodeIfPresent(String.self, forKey: .unrequestableReason)
    }
}

public struct RequestCatalogRail: Codable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let kind: RequestCatalogKind?
    public let seeAll: String?
    public let items: [RequestCatalogItem]

    enum CodingKeys: String, CodingKey {
        case id = "key"
        case title, kind, items
        case seeAll = "see_all"
    }
}

public struct RequestCatalogHome: Codable, Sendable {
    public let hero: [RequestCatalogItem]
    public let rails: [RequestCatalogRail]
}

public struct RequestCatalogPage: Codable, Sendable {
    public let items: [RequestCatalogItem]
    public let page: Int
    public let totalPages: Int

    enum CodingKeys: String, CodingKey { case items, page; case totalPages = "total_pages" }
}

public struct RequestCatalogGenre: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
}

public struct RequestSeason: Codable, Identifiable, Hashable, Sendable {
    public let number: Int
    public let name: String
    public let episodeCount: Int
    public let airDate: String?
    public let posterURL: URL?
    public var id: Int { number }

    enum CodingKeys: String, CodingKey {
        case number, name
        case episodeCount = "episode_count"
        case airDate = "air_date"
        case posterURL = "poster_url"
    }
}

public struct RequestTrailer: Codable, Identifiable, Hashable, Sendable {
    public let youtubeID: String
    public let name: String
    public let type: String
    public let official: Bool
    public var id: String { youtubeID }

    public init(youtubeID: String, name: String, type: String, official: Bool) {
        self.youtubeID = youtubeID
        self.name = name
        self.type = type
        self.official = official
    }

    /// A validated public watch URL suitable for the system's external link
    /// handler. Catalog metadata must never supply a provider URL directly.
    public var youtubeURL: URL? {
        let scalars = youtubeID.unicodeScalars
        guard (6...20).contains(youtubeID.utf8.count),
              scalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" }) else {
            return nil
        }
        var components = URLComponents()
        components.scheme = "https"
        components.host = "www.youtube.com"
        components.path = "/watch"
        components.queryItems = [URLQueryItem(name: "v", value: youtubeID)]
        return components.url
    }

    enum CodingKeys: String, CodingKey {
        case youtubeID = "youtube_id"
        case name, type, official
    }
}

public struct RequestCatalogCredit: Codable, Hashable, Sendable {
    public let id: Int?
    public let name: String
    public let character: String?
    public let profileURL: URL?
    public let job: String?

    enum CodingKeys: String, CodingKey {
        case id, name, character, job
        case profileURL = "profile_url"
    }
}

public struct RequestCatalogDetail: Decodable, Sendable {
    public let item: RequestCatalogItem
    public let tagline: String?
    public let runtime: Int?
    public let certification: String?
    public let statusText: String?
    public let seasons: [RequestSeason]
    public let trailers: [RequestTrailer]
    public let cast: [RequestCatalogCredit]
    public let crew: [RequestCatalogCredit]
    public let networks: [String]
    public let studios: [String]
    public let recommendations: [RequestCatalogItem]
    public let similar: [RequestCatalogItem]
    public let animeRelations: [RequestCatalogItem]

    enum CodingKeys: String, CodingKey {
        case tagline, runtime, certification, seasons, trailers, cast, crew, networks, studios, recommendations, similar
        case statusText = "status_text"
        case animeRelations = "anime_relations"
    }

    public init(from decoder: Decoder) throws {
        item = try RequestCatalogItem(from: decoder)
        let values = try decoder.container(keyedBy: CodingKeys.self)
        tagline = try values.decodeIfPresent(String.self, forKey: .tagline)
        runtime = try values.decodeIfPresent(Int.self, forKey: .runtime)
        certification = try values.decodeIfPresent(String.self, forKey: .certification)
        statusText = try values.decodeIfPresent(String.self, forKey: .statusText)
        seasons = try values.decodeIfPresent([RequestSeason].self, forKey: .seasons) ?? []
        trailers = try values.decodeIfPresent([RequestTrailer].self, forKey: .trailers) ?? []
        cast = try values.decodeIfPresent([RequestCatalogCredit].self, forKey: .cast) ?? []
        crew = try values.decodeIfPresent([RequestCatalogCredit].self, forKey: .crew) ?? []
        networks = try values.decodeIfPresent([String].self, forKey: .networks) ?? []
        studios = try values.decodeIfPresent([String].self, forKey: .studios) ?? []
        recommendations = try values.decodeIfPresent([RequestCatalogItem].self, forKey: .recommendations) ?? []
        similar = try values.decodeIfPresent([RequestCatalogItem].self, forKey: .similar) ?? []
        animeRelations = try values.decodeIfPresent([RequestCatalogItem].self, forKey: .animeRelations) ?? []
    }
}

public struct RequestQuota: Codable, Hashable, Sendable {
    public let kind: RequestCatalogKind
    public let canRequest: Bool
    public let autoApprove: Bool
    public let limit: Int?
    public let days: Int?
    public let used: Int
    public let remaining: Int?

    enum CodingKeys: String, CodingKey {
        case kind, limit, days, used
        case canRequest = "can_request"
        case autoApprove = "auto_approve"
        case remaining
    }
}

public struct RequestMember: Codable, Hashable, Sendable, Identifiable {
    public let id: String
    public let name: String
}

public struct RequestRecord: Codable, Identifiable, Sendable {
    public let id: String
    public let kind: RequestCatalogKind
    public let mediaType: RequestMediaType
    public let key: String
    public let tmdbID: Int?
    public let tvdbID: Int?
    public let anilistID: Int?
    public let title: String
    public let year: Int?
    public let posterURL: URL?
    public let seasons: RequestSeasonSelection?
    public let language: RequestLanguage?
    public let status: RequestState
    public let progress: Double?
    public let requestedBy: RequestMember
    public let followers: [RequestMember]
    public let libraryTitleID: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, key, title, year, seasons, language, status, progress, followers
        case mediaType = "media_type"
        case tmdbID = "tmdb_id"
        case tvdbID = "tvdb_id"
        case anilistID = "anilist_id"
        case posterURL = "poster_url"
        case requestedBy = "requested_by"
        case libraryTitleID = "library_title_id"
    }
}

public struct RequestPage: Codable, Sendable {
    public let items: [RequestRecord]
    public let page: Int
    public let totalPages: Int
    public let counts: [String: Int]

    enum CodingKeys: String, CodingKey { case items, page, counts; case totalPages = "total_pages" }
}

public enum RequestAPIError: LocalizedError, Sendable, Equatable {
    case notSignedIn
    case invalidRequest
    case server(status: Int, code: String?)
    case malformedResponse

    public var code: String? {
        if case let .server(_, code) = self { return code }
        return nil
    }

    public var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Sign in to use requests."
        case .invalidRequest: return "This title cannot be requested yet."
        case .server(_, let code): return code ?? "Lumina could not complete that request."
        case .malformedResponse: return "Lumina sent an unexpected response."
        }
    }
}

@MainActor @Observable public final class LuminaRequests {
    public private(set) var home: RequestCatalogHome?
    public private(set) var quotas: [RequestQuota] = []
    public private(set) var myRequests: RequestPage?
    public private(set) var isLoading = false
    public private(set) var error: RequestAPIError?

    private var session: LuminaSession?
    private let client: LuminaClient
    private var generation = 0

    public init(session: LuminaSession? = nil) {
        self.session = session
        client = LuminaClient()
    }

    init(session: LuminaSession?, client: LuminaClient) {
        self.session = session
        self.client = client
    }

    public func configure(session: LuminaSession?) {
        guard self.session?.accessToken != session?.accessToken || self.session?.serverURL != session?.serverURL else { return }
        generation += 1
        self.session = session
        home = nil
        quotas = []
        myRequests = nil
        isLoading = false
        error = nil
    }

    public func artworkFraming(for source: URL) async -> HeroFraming? {
        guard let session, source.scheme == "https", source.host == "image.tmdb.org",
              source.path.hasPrefix("/t/p/w1280/") else { return nil }
        let current = generation
        var lookup = client.request(origin: session.serverURL, path: "/Lumina/Requests/catalog/framing",
                                    query: [URLQueryItem(name: "src", value: source.absoluteString)],
                                    token: session.accessToken, deviceID: session.deviceID)
        lookup.timeoutInterval = 0.15
        do {
            let (data, response) = try await client.response(lookup)
            guard current == generation, response.statusCode == 200 else { return nil }
            return try JSONDecoder().decode(HeroFraming.self, from: data)
        } catch { return nil }
    }

    public func loadHome() async {
        await load { try await self.request("/catalog/home") as RequestCatalogHome } assign: { self.home = $0 }
    }

    public func loadQuotas() async {
        struct Response: Decodable { let quotas: [RequestQuota] }
        await load { try await self.request("/quota") as Response } assign: { self.quotas = $0.quotas }
    }

    public func loadMyRequests(status: RequestState? = nil, page: Int = 1) async {
        var query = [URLQueryItem(name: "page", value: String(max(1, page)))]
        if let status { query.append(URLQueryItem(name: "status", value: status.rawValue)) }
        await load { try await self.request("", query: query) as RequestPage } assign: { self.myRequests = $0 }
    }

    public func search(_ text: String, page: Int = 1) async throws -> RequestCatalogPage {
        try await request("/catalog/search", query: [URLQueryItem(name: "q", value: String(text.prefix(200))), URLQueryItem(name: "page", value: String(max(1, page)))])
    }

    public func list(_ kind: RequestCatalogKind, section: String? = nil, genre: String? = nil, page: Int = 1) async throws -> RequestCatalogPage {
        var query = [URLQueryItem(name: "page", value: String(max(1, page)))]
        if let section, !section.isEmpty { query.append(URLQueryItem(name: "section", value: String(section.prefix(32)))) }
        if let genre, !genre.isEmpty { query.append(URLQueryItem(name: "genre", value: String(genre.prefix(64)))) }
        return try await request("/catalog/list/\(kind.rawValue)", query: query)
    }

    public func genres(_ kind: RequestCatalogKind) async throws -> [RequestCatalogGenre] {
        struct Response: Decodable { let genres: [RequestCatalogGenre] }
        let response: Response = try await request("/catalog/genres/\(kind.rawValue)")
        return response.genres
    }

    public func detail(kind: RequestCatalogKind, id: Int) async throws -> RequestCatalogDetail {
        guard id > 0 else { throw RequestAPIError.invalidRequest }
        return try await request("/catalog/title/\(kind.rawValue)/\(id)")
    }

    public func create(_ item: RequestCatalogItem, seasons: RequestSeasonSelection? = nil, language: RequestLanguage? = nil) async throws -> RequestRecord {
        guard item.canRequest, item.status.canCreateRequest else { throw RequestAPIError.invalidRequest }
        let submission = try RequestSubmission(item: item, seasons: seasons, language: language)
        return try await request("", method: "POST", body: submission)
    }

    private func load<Value>(_ operation: () async throws -> Value, assign: (Value) -> Void) async {
        let current = generation
        isLoading = true
        error = nil
        defer { if current == generation { isLoading = false } }
        do {
            let value = try await operation()
            guard current == generation else { return }
            assign(value)
        } catch let failure as RequestAPIError {
            guard current == generation else { return }
            error = failure
        } catch {
            guard current == generation else { return }
            self.error = .malformedResponse
        }
    }

    private func request<Value: Decodable>(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: RequestSubmission? = nil) async throws -> Value {
        guard let session else { throw RequestAPIError.notSignedIn }
        let current = generation
        var request = client.request(
            origin: session.serverURL,
            path: "/Lumina/Requests" + path,
            method: method,
            query: query,
            token: session.accessToken,
            deviceID: session.deviceID
        )
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        do {
            let (data, response) = try await client.response(request)
            guard current == generation else { throw CancellationError() }
            guard (200..<300).contains(response.statusCode) else {
                throw RequestAPIError.server(status: response.statusCode, code: RequestErrorEnvelope.code(from: data))
            }
            do { return try JSONDecoder().decode(Value.self, from: data) }
            catch { throw RequestAPIError.malformedResponse }
        } catch let error as RequestAPIError {
            throw error
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw RequestAPIError.malformedResponse
        }
    }
}

private struct RequestSubmission: Encodable {
    let kind: RequestCatalogKind
    let tmdbID: Int?
    let tvdbID: Int?
    let anilistID: Int?
    let mediaType: RequestMediaType?
    let seasons: RequestSeasonSelection?
    let language: RequestLanguage?

    init(item: RequestCatalogItem, seasons: RequestSeasonSelection?, language: RequestLanguage?) throws {
        guard item.catalogID != nil else { throw RequestAPIError.invalidRequest }
        kind = item.kind
        tmdbID = item.tmdbID
        tvdbID = item.tvdbID
        anilistID = item.anilistID
        mediaType = item.kind == .anime && item.mediaType == .movie ? .movie : nil
        self.seasons = item.mediaType == .tv ? seasons ?? .all : nil
        self.language = item.kind == .anime && item.mediaType == .tv ? language : nil
    }

    enum CodingKeys: String, CodingKey {
        case kind, seasons, language
        case tmdbID = "tmdb_id"
        case tvdbID = "tvdb_id"
        case anilistID = "anilist_id"
        case mediaType = "media_type"
    }
}

private struct RequestErrorEnvelope: Decodable {
    private struct Detail: Decodable {
        let code: String?

        init(from decoder: Decoder) throws {
            let value = try decoder.singleValueContainer()
            if let string = try? value.decode(String.self) {
                code = string
            } else if let nested = try? decoder.container(keyedBy: CodingKeys.self) {
                code = try nested.decodeIfPresent(String.self, forKey: .code)
            } else {
                code = nil
            }
        }

        private enum CodingKeys: String, CodingKey { case code }
    }

    private let detail: Detail?

    static func code(from data: Data) -> String? {
        (try? JSONDecoder().decode(Self.self, from: data))?.detail?.code
    }
}
