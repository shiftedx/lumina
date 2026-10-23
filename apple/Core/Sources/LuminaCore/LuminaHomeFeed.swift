import Foundation
import Observation

public enum HomeFeedShape: String, Codable, Sendable {
    case poster
    case still
    case square
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
        guard self.session?.accessToken != session?.accessToken || self.session?.serverURL != session?.serverURL else { return }
        sessionGeneration += 1
        operationGeneration += 1
        self.session = session
        rows = []
        isLoading = false
        hasLoaded = false
        errorMessage = nil
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
            rows = response.rows
            hasLoaded = true
        } catch {
            guard operation == operationGeneration, currentSession == sessionGeneration else { return }
            errorMessage = "Home could not be loaded."
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
