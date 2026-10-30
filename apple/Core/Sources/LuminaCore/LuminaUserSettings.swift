import Foundation
import Observation

/// A lossless JSON value for member settings. It keeps unknown server fields intact when clients read nested preferences.
public indirect enum SettingsValue: Codable, Equatable, Sendable {
    case object([String: SettingsValue])
    case string(String)
    case bool(Bool)
    case number(Double)
    case array([SettingsValue])
    case null

    public init(object: [String: SettingsValue]) { self = .object(object) }
    public init(string: String) { self = .string(string) }
    public init(bool: Bool) { self = .bool(bool) }
    public init(number: Double) { self = .number(number) }
    public init(array: [SettingsValue]) { self = .array(array) }
    public init(_ object: [String: SettingsValue]) { self = .object(object) }
    public init(_ string: String) { self = .string(string) }
    public init(_ bool: Bool) { self = .bool(bool) }
    public init(_ number: Double) { self = .number(number) }
    public init(_ array: [SettingsValue]) { self = .array(array) }

    public var objectValue: [String: SettingsValue]? {
        guard case let .object(value) = self else { return nil }
        return value
    }

    public var stringValue: String? {
        guard case let .string(value) = self else { return nil }
        return value
    }

    public var boolValue: Bool? {
        guard case let .bool(value) = self else { return nil }
        return value
    }

    public var numberValue: Double? {
        guard case let .number(value) = self else { return nil }
        return value
    }

    public var arrayValue: [SettingsValue]? {
        guard case let .array(value) = self else { return nil }
        return value
    }

    public subscript(key: String) -> SettingsValue? { objectValue?[key] }

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let bool = try? value.decode(Bool.self) { self = .bool(bool) }
        else if let number = try? value.decode(Double.self) { self = .number(number) }
        else if let string = try? value.decode(String.self) { self = .string(string) }
        else if let array = try? value.decode([SettingsValue].self) { self = .array(array) }
        else { self = .object(try value.decode([String: SettingsValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .object(let object): try value.encode(object)
        case .string(let string): try value.encode(string)
        case .bool(let bool): try value.encode(bool)
        case .number(let number): try value.encode(number)
        case .array(let array): try value.encode(array)
        case .null: try value.encodeNil()
        }
    }
}

private struct UserSettingsCapabilities: Decodable {
    let streamingEnabled: Bool
    let youtubeSessionEnabled: Bool?
    let autoplayPresence: SettingsValue?

    private enum CodingKeys: String, CodingKey {
        case streamingEnabled = "streaming_enabled"
        case youtubeSessionEnabled = "youtube_session_enabled"
        case autoplayPresence = "autoplay_presence"
    }
}

public enum UserSettingsError: Error, Equatable, LocalizedError, Sendable {
    case notSignedIn
    case invalidActionPath
    case invalidMethod
    case server(Int)
    case malformedResponse

    public var errorDescription: String? {
        switch self {
        case .notSignedIn: "Sign in to manage settings."
        case .invalidActionPath, .invalidMethod: "That settings action is unavailable."
        case .server(401), .server(403): "Your session no longer has access to these settings."
        case .server: "Lumina could not complete that settings action."
        case .malformedResponse: "Lumina sent an unexpected settings response."
        }
    }
}

/// Connected-app member settings. Document fields are retained verbatim so a focused native editor never drops web preferences.
@MainActor @Observable public final class LuminaUserSettings {
    public private(set) var document = [String: SettingsValue]()
    public private(set) var isLoading = false
    public private(set) var isSaving = false
    public private(set) var hasLoaded = false
    public private(set) var errorMessage: String?
    /// Whether this connected app may start media streams for the current signed-in member.
    public private(set) var streamingEnabled = false
    /// Whether the current member is allowed to use their YouTube session in this connected app.
    public private(set) var youtubeSessionEnabled = false
    public private(set) var autoplayPresencePolicy = EpisodeAutoplayPolicy()

    /// The theme stored under `ui_prefs.theme`, with the website's system default for an absent or malformed value.
    public var theme: String {
        guard let theme = document["ui_prefs"]?["theme"]?.stringValue,
              ["system", "light", "dark", "oled"].contains(theme) else { return "system" }
        return theme
    }

    /// Shared with the website; an absent preference keeps next-episode autoplay enabled.
    public var autoplayUpNext: Bool { document["ui_prefs"]?["autoplay_up_next"]?.boolValue ?? true }

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
        guard !Self.sameSession(self.session, session) else { return }
        sessionGeneration += 1
        operationGeneration += 1
        self.session = session
        document = [:]
        isLoading = false
        isSaving = false
        hasLoaded = false
        errorMessage = nil
        streamingEnabled = false
        youtubeSessionEnabled = false
        autoplayPresencePolicy = EpisodeAutoplayPolicy()
    }

    public func load() async {
        guard !isSaving else { return }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = UserSettingsError.notSignedIn.localizedDescription }
            return
        }
        let owner = sessionGeneration
        isLoading = true
        errorMessage = nil
        streamingEnabled = false
        youtubeSessionEnabled = false
        defer { if owns(operation), owner == sessionGeneration { isLoading = false } }
        do {
            let response: [String: SettingsValue] = try await request(session: session, path: "", method: "GET")
            guard owns(operation), owner == sessionGeneration else { return }
            document = response
            hasLoaded = true

            let capabilities: UserSettingsCapabilities?
            do {
                capabilities = try await request(session: session, path: "/capabilities", method: "GET")
            } catch {
                capabilities = nil
            }
            guard owns(operation), owner == sessionGeneration else { return }
            streamingEnabled = capabilities?.streamingEnabled ?? false
            youtubeSessionEnabled = streamingEnabled && capabilities?.youtubeSessionEnabled == true
            if let capabilities { autoplayPresencePolicy = EpisodeAutoplayPolicy(settingsValue: capabilities.autoplayPresence) }
        } catch {
            guard owns(operation), owner == sessionGeneration else { return }
            errorMessage = Self.message(for: error)
        }
    }

    /// Sends only the caller's top-level settings fields. The returned complete document becomes the local source of truth.
    @discardableResult public func update(_ patch: [String: SettingsValue]) async -> Bool {
        guard !isSaving else { return false }
        let operation = beginOperation()
        guard let session else {
            if owns(operation) { errorMessage = UserSettingsError.notSignedIn.localizedDescription }
            return false
        }
        let owner = sessionGeneration
        isLoading = false
        isSaving = true
        errorMessage = nil
        defer { if owns(operation), owner == sessionGeneration { isSaving = false } }
        do {
            let response: [String: SettingsValue] = try await request(session: session, path: "", method: "PUT", body: patch)
            guard owns(operation), owner == sessionGeneration else { return false }
            document = response
            hasLoaded = true
            return true
        } catch {
            guard owns(operation), owner == sessionGeneration else { return false }
            errorMessage = Self.message(for: error)
            return false
        }
    }

    /// Performs a deliberately scoped settings sub-action. Paths are slash-separated safe identifiers, never URLs or queries.
    public func action(_ path: String, method: String = "GET", body: [String: SettingsValue]? = nil,
                       reauth: String? = nil) async throws -> SettingsValue {
        let path = try Self.validActionPath(path)
        let method = try Self.validMethod(method)
        guard let session else { throw UserSettingsError.notSignedIn }
        let owner = sessionGeneration
        let response: SettingsValue = try await request(session: session, path: "/\(path)", method: method, body: body,
                                                         reauth: reauth, emptyValue: .null)
        guard owner == sessionGeneration else { throw CancellationError() }
        return response
    }

    private func beginOperation() -> Int {
        operationGeneration += 1
        return operationGeneration
    }

    private func owns(_ operation: Int) -> Bool { operation == operationGeneration }

    private func request<Value: Decodable>(session: LuminaSession, path: String, method: String, body: [String: SettingsValue]? = nil,
                                            reauth: String? = nil, emptyValue: Value? = nil) async throws -> Value {
        var request = client.request(
            origin: session.serverURL,
            path: "/Lumina/Settings" + path,
            method: method,
            token: session.accessToken,
            deviceID: session.deviceID
        )
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let reauth = reauth?.trimmingCharacters(in: .whitespacesAndNewlines), !reauth.isEmpty {
            request.setValue(reauth, forHTTPHeaderField: "X-Lumina-Reauth")
        }
        do {
            let (data, response) = try await client.response(request)
            guard (200..<300).contains(response.statusCode) else { throw UserSettingsError.server(response.statusCode) }
            if data.isEmpty, let emptyValue { return emptyValue }
            return try JSONDecoder().decode(Value.self, from: data)
        } catch let error as UserSettingsError {
            throw error
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw UserSettingsError.malformedResponse
        }
    }

    private static func sameSession(_ left: LuminaSession?, _ right: LuminaSession?) -> Bool {
        left?.serverURL == right?.serverURL && left?.accessToken == right?.accessToken && left?.userID == right?.userID
    }

    private static func validActionPath(_ value: String) throws -> String {
        guard !value.isEmpty, value.count <= 160, !value.hasPrefix("/"), !value.contains("?"), !value.contains("#"), !value.contains(":") else {
            throw UserSettingsError.invalidActionPath
        }
        let pieces = value.split(separator: "/", omittingEmptySubsequences: false)
        guard !pieces.isEmpty else { throw UserSettingsError.invalidActionPath }
        for piece in pieces {
            guard !piece.isEmpty, piece != ".", piece != "..",
                  piece.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" }) else {
                throw UserSettingsError.invalidActionPath
            }
        }
        return pieces.joined(separator: "/")
    }

    private static func validMethod(_ value: String) throws -> String {
        let method = value.uppercased()
        guard ["GET", "POST", "PUT", "PATCH", "DELETE"].contains(method) else { throw UserSettingsError.invalidMethod }
        return method
    }

    private static func message(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? "Lumina could not update settings."
    }
}

private extension EpisodeAutoplayPolicy {
    init(settingsValue: SettingsValue?) {
        let limit = settingsValue?["episode_limit"]?.numberValue ?? 3
        let timeout = settingsValue?["timeout_seconds"]?.numberValue ?? 300
        self.init(enabled: settingsValue?["enabled"]?.boolValue ?? true,
                  episodeLimit: (1.0...100.0).contains(limit) && limit.rounded() == limit ? Int(limit) : 3,
                  timeoutSeconds: (60.0...86_400.0).contains(timeout) && timeout.rounded() == timeout ? Int(timeout) : 300)
    }
}
