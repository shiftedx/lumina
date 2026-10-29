import Foundation
import Observation

/// Stable Home shelf identifiers shared with the website's member preference contract.
public enum HomeShelfID: String, Codable, CaseIterable, Sendable {
    case continueWatching = "continue"
    case nextUp = "next_up"
    case live
    case watchlist
    case newInLibrary = "new_in_library"
    case becauseYouWatched = "because_you_watched"
    case recommended
    case pickedForYou = "picked_for_you"
    case collections
    case fromFollows = "from_follows"
    case recentlySaved = "recently_saved"
    case newAnime = "new_anime"
    case recentMusic = "recent_music"
    case continueListening = "continue_listening"

    public var title: String {
        switch self {
        case .continueWatching: "Continue watching"
        case .live: "Live now"
        case .nextUp: "Next up"
        case .watchlist: "Your watchlist"
        case .newInLibrary: "New in your library"
        case .becauseYouWatched: "More for you"
        case .recommended: "Recommended for you"
        case .pickedForYou: "Picked for you"
        case .collections: "Your smart collections"
        case .fromFollows: "New from your follows"
        case .recentlySaved: "Recently saved"
        case .newAnime: "New in Anime"
        case .recentMusic: "Recently added music"
        case .continueListening: "Continue listening"
        }
    }
}

/// A persisted Home shelf entry. The server preserves this full list even where a client only edits a subset.
public struct HomeShelfPreference: Codable, Identifiable, Equatable, Sendable {
    public let id: HomeShelfID
    public let visible: Bool

    public init(id: HomeShelfID, visible: Bool) {
        self.id = id
        self.visible = visible
    }
}

/// Member-scoped Home shelf layout. Requests use the connected-app session and never persist its credentials.
@MainActor @Observable public final class LuminaHomeLayout {
    public private(set) var shelves: [HomeShelfPreference] = LuminaHomeLayout.defaultShelves
    public private(set) var isLoading = false
    public private(set) var isSaving = false
    public private(set) var hasLoaded = false
    public private(set) var errorMessage: String?

    private var session: LuminaSession?
    private let client: LuminaClient
    private var sessionGeneration = 0
    private var operationGeneration = 0

    public init(session: LuminaSession? = nil) {
        self.session = session
        client = LuminaClient()
    }

    init(session: LuminaSession?, client: LuminaClient) {
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
        shelves = Self.defaultShelves
        isLoading = false
        isSaving = false
        hasLoaded = false
        errorMessage = nil
    }

    public func load() async {
        guard !isSaving, !isLoading else { return }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = "Sign in to customize Home." }
            return
        }
        let owner = sessionGeneration
        isLoading = true
        errorMessage = nil
        defer { if owns(operation), owner == sessionGeneration { isLoading = false } }
        do {
            let layout: HomeLayoutResponse = try await request(session: session, method: "GET")
            guard owns(operation), owner == sessionGeneration else { return }
            shelves = Self.normalized(layout.shelves)
            hasLoaded = true
        } catch {
            guard owns(operation), owner == sessionGeneration else { return }
            errorMessage = Self.message(for: error)
        }
    }

    /// Persists a complete website-compatible layout. Passing nil removes the stored override and restores defaults.
    @discardableResult public func save(_ shelves: [HomeShelfPreference]?) async -> Bool {
        guard !isSaving else { return false }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = "Sign in to customize Home." }
            return false
        }
        let owner = sessionGeneration
        // A save supersedes a foreground refresh. The older load's deferred cleanup
        // intentionally no-ops once its operation token is stale.
        isLoading = false
        isSaving = true
        errorMessage = nil
        defer { if owns(operation), owner == sessionGeneration { isSaving = false } }
        do {
            let layout: HomeLayoutResponse = try await request(session: session, method: "PUT", body: HomeLayoutResponse(shelves: shelves))
            guard owns(operation), owner == sessionGeneration else { return false }
            self.shelves = Self.normalized(layout.shelves)
            hasLoaded = true
            return true
        } catch {
            guard owns(operation), owner == sessionGeneration else { return false }
            errorMessage = Self.message(for: error)
            return false
        }
    }

    private static let defaultShelves = HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }

    private static func normalized(_ stored: [HomeShelfPreference]?) -> [HomeShelfPreference] {
        guard let stored else { return defaultShelves }
        var seen = Set<HomeShelfID>()
        var result = [HomeShelfPreference]()
        for preference in stored where seen.insert(preference.id).inserted {
            result.append(preference)
        }
        for id in HomeShelfID.allCases where seen.insert(id).inserted {
            result.append(HomeShelfPreference(id: id, visible: true))
        }
        return result
    }

    private func beginOperation() -> Int {
        operationGeneration += 1
        return operationGeneration
    }

    private func owns(_ operation: Int) -> Bool { operation == operationGeneration }

    private func request<Value: Decodable>(session: LuminaSession, method: String, body: HomeLayoutResponse? = nil) async throws -> Value {
        var request = client.request(
            origin: session.serverURL,
            path: "/Lumina/Home/layout",
            method: method,
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
            guard (200..<300).contains(response.statusCode) else { throw LuminaError.http(response.statusCode) }
            return try JSONDecoder().decode(Value.self, from: data)
        } catch let error as LuminaError {
            throw error
        } catch {
            throw LuminaError.malformedResponse
        }
    }

    private static func message(for error: Error) -> String {
        if case LuminaError.notSignedIn = error { return "Sign in to customize Home." }
        return "Home layout could not be updated."
    }
}

private struct HomeLayoutResponse: Codable {
    let shelves: [HomeShelfPreference]?

    init(shelves: [HomeShelfPreference]?) {
        self.shelves = shelves
    }

    private enum CodingKeys: String, CodingKey { case shelves }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        shelves = try values.decodeIfPresent([HomeShelfPreference].self, forKey: .shelves)
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        if let shelves {
            try values.encode(shelves, forKey: .shelves)
        } else {
            try values.encodeNil(forKey: .shelves)
        }
    }
}
