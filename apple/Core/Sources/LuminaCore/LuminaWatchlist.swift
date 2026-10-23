import Foundation
import Observation

public enum WatchlistError: Error, Equatable, LocalizedError, Sendable {
    case notSignedIn
    case invalidItemID
    case server(Int)
    case malformedResponse

    public var errorDescription: String? {
        switch self {
        case .notSignedIn: "Sign in to update your watchlist."
        case .invalidItemID: "This item cannot be added to your watchlist."
        case .server(401), .server(403): "Your session no longer has access to this watchlist."
        case .server(404): "This item is no longer available."
        case .server(422): "Your watchlist is full."
        case .server: "Lumina could not update your watchlist."
        case .malformedResponse: "Lumina sent an unexpected watchlist response."
        }
    }
}

/// The member's existing server-side WatchQueue, presented as a detail-level watchlist toggle.
/// Each instance owns one detail item's current membership state; it never aliases Favorites.
@MainActor @Observable public final class LuminaWatchlist {
    public private(set) var isIncluded = false
    public private(set) var isLoading = false
    public private(set) var isSaving = false
    public private(set) var errorMessage: String?

    private var session: LuminaSession?
    private let client: LuminaClient
    private var loadedItemID: String?
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
        guard !Self.sameSession(self.session, session) else { return }
        sessionGeneration += 1
        operationGeneration += 1
        self.session = session
        loadedItemID = nil
        isIncluded = false
        isLoading = false
        isSaving = false
        errorMessage = nil
    }

    public func contains(_ item: MediaItem) -> Bool { contains(itemID: item.id) }

    public func contains(itemID: String) -> Bool {
        loadedItemID == itemID && isIncluded
    }

    public func load(itemID: String) async {
        guard !isSaving else { return }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = WatchlistError.notSignedIn.localizedDescription }
            return
        }
        let owner = sessionGeneration
        isLoading = true
        errorMessage = nil
        defer { if owns(operation), owner == sessionGeneration { isLoading = false } }
        do {
            let state = try await request(session: session, itemID: itemID, method: "GET", included: nil)
            guard owns(operation), owner == sessionGeneration else { return }
            loadedItemID = itemID
            isIncluded = state.included
        } catch {
            guard owns(operation), owner == sessionGeneration else { return }
            errorMessage = Self.message(for: error)
        }
    }

    /// Writes the member's intended state. The server resolves a title to its current concrete playable item.
    @discardableResult public func set(itemID: String, included: Bool) async -> Bool {
        guard !isSaving else { return false }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = WatchlistError.notSignedIn.localizedDescription }
            return false
        }
        let owner = sessionGeneration
        isLoading = false
        isSaving = true
        errorMessage = nil
        defer { if owns(operation), owner == sessionGeneration { isSaving = false } }
        do {
            let state = try await request(session: session, itemID: itemID, method: "PUT", included: included)
            guard owns(operation), owner == sessionGeneration else { return false }
            loadedItemID = itemID
            isIncluded = state.included
            return true
        } catch {
            guard owns(operation), owner == sessionGeneration else { return false }
            errorMessage = Self.message(for: error)
            return false
        }
    }

    private func beginOperation() -> Int {
        operationGeneration += 1
        return operationGeneration
    }

    private func owns(_ operation: Int) -> Bool { operation == operationGeneration }

    private func request(session: LuminaSession, itemID: String, method: String, included: Bool?) async throws -> WatchlistState {
        let itemID = try Self.safeID(itemID)
        var request = client.request(
            origin: session.serverURL,
            path: "/Lumina/Watchlist/\(itemID)",
            method: method,
            token: session.accessToken,
            deviceID: session.deviceID
        )
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let included {
            request.httpBody = try JSONEncoder().encode(WatchlistState(included: included))
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        do {
            let (data, response) = try await client.response(request)
            guard (200..<300).contains(response.statusCode) else { throw WatchlistError.server(response.statusCode) }
            return try JSONDecoder().decode(WatchlistState.self, from: data)
        } catch let error as WatchlistError {
            throw error
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw WatchlistError.malformedResponse
        }
    }

    private static func safeID(_ value: String) throws -> String {
        guard !value.isEmpty, value.utf8.count <= 128,
              value.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "-" || $0 == "_" }) else {
            throw WatchlistError.invalidItemID
        }
        return value
    }

    private static func sameSession(_ left: LuminaSession?, _ right: LuminaSession?) -> Bool {
        left?.serverURL == right?.serverURL && left?.accessToken == right?.accessToken && left?.userID == right?.userID
    }

    private static func message(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? "Lumina could not update your watchlist."
    }
}

private struct WatchlistState: Codable {
    let included: Bool
}
