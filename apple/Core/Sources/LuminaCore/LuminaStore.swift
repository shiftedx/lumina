import Foundation
import Observation
import Security

let tickScale = 10_000_000.0

@MainActor @Observable public final class LuminaStore {
    public private(set) var session: LuminaSession?
    public private(set) var items = [MediaItem]()
    public private(set) var resumeItems = [MediaItem]()
    public private(set) var isBusy = false
    public private(set) var errorMessage: String?
    public private(set) var hasMore = false

    private let client: LuminaClient
    private let credentials: CredentialStore
    private var currentSearch = ""
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
                credentials.deleteSession()
                session = nil
            } else if case LuminaError.http(403) = error {
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
        credentials.deleteSession()
        session = nil
        items = []
        resumeItems = []
        hasMore = false
        guard let active else { return }
        try? await client.logout(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
    }

    public func loadLibrary(search: String = "") async {
        guard let active = session else { return }
        libraryGeneration += 1
        let generation = libraryGeneration
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            currentSearch = search
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: search, start: 0)
            guard generation == libraryGeneration, isCurrent(active) else { return }
            items = page.items.filter(\.isPlayable)
            nextIndex = items.count
            hasMore = nextIndex < page.total
        } catch {
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadMore() async {
        guard let active = session, hasMore, !isBusy else { return }
        let generation = libraryGeneration
        isBusy = true
        defer { isBusy = false }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: currentSearch, start: nextIndex)
            guard generation == libraryGeneration, isCurrent(active) else { return }
            let playable = page.items.filter(\.isPlayable)
            items.append(contentsOf: playable)
            nextIndex += page.items.count
            hasMore = nextIndex < page.total
        } catch {
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func loadResume() async {
        guard let active = session else { return }
        do {
            let loaded = try await client.resume(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            guard isCurrent(active) else { return }
            resumeItems = loaded.filter(\.isPlayable)
        } catch {
            errorMessage = Self.message(for: error, signingIn: false)
        }
    }

    public func refresh() async {
        await loadLibrary(search: currentSearch)
        await loadResume()
    }

    public func clearError() { errorMessage = nil }

    public func imageURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        guard let active = session, item.imageTags?["Primary"] != nil else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(item.id)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func preparePlayback(for item: MediaItem, forceTranscode: Bool = false) async throws -> PlaybackRequest {
        guard let active = session else { throw LuminaError.notSignedIn }
        let current = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
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

enum LuminaError: Error, Equatable {
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
    private let service = "dev.lumina.apple"
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
