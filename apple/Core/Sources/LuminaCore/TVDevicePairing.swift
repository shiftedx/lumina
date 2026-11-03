import Foundation
import CryptoKit
import Network

public struct TVPairingRequest: Decodable, Sendable, Hashable {
    public let id: String
    public let deviceSecret: String
    public let code: String
    public let expiresAt: String
    public let pollIntervalSeconds: Int
    public let serverID: String

    enum CodingKeys: String, CodingKey {
        case id, code
        case deviceSecret = "device_secret"
        case expiresAt = "expires_at"
        case pollIntervalSeconds = "poll_interval_seconds"
        case serverID = "server_id"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        deviceSecret = try values.decode(String.self, forKey: .deviceSecret)
        code = try values.decode(String.self, forKey: .code)
        expiresAt = try values.decode(String.self, forKey: .expiresAt)
        pollIntervalSeconds = try values.decode(Int.self, forKey: .pollIntervalSeconds)
        serverID = try values.decode(String.self, forKey: .serverID)
        guard Self.validIdentifier(id), Self.validSecret(deviceSecret), Self.validCode(code),
              Self.validExpiry(expiresAt), (1...60).contains(pollIntervalSeconds), Self.validIdentifier(serverID) else {
            throw LuminaError.malformedResponse
        }
    }

    public func qrLink(serverURL: URL) throws -> URL {
        let origin = try ServerOrigin(serverURL.absoluteString).url
        let payload = TVPairingApprovalLink.RawPayload(type: "lumina-tv-pair", version: 1,
                                                       serverURL: origin.absoluteString, serverID: serverID, code: code)
        let data = try JSONEncoder().encode(payload)
        guard let fragment = String(data: data, encoding: .utf8), fragment.utf8.count <= 8_192 else {
            throw LuminaError.malformedResponse
        }
        var components = URLComponents()
        components.scheme = "lumina"
        components.host = "approve-tv"
        components.fragment = fragment
        guard let link = components.url else { throw LuminaError.malformedResponse }
        return link
    }
}

public enum TVPairingStatus: String, Decodable, Sendable, Hashable {
    case pending, approved, denied, expired, consumed
}

public struct TVPairingApprovalLink: Identifiable, Sendable, Hashable {
    public let serverURL: URL
    public let serverID: String
    public let code: String
    public var id: String { "\(serverID):\(code)" }

    public init(scannedCode: String) throws {
        guard scannedCode.utf8.count <= 8_192,
              let data = scannedCode.data(using: .utf8) else { throw LuminaError.malformedResponse }
        let raw = try JSONDecoder().decode(RawPayload.self, from: data)
        guard raw.type == "lumina-tv-pair", raw.version == 1,
              TVPairingRequest.validIdentifier(raw.serverID), TVPairingRequest.validCode(raw.code) else {
            throw LuminaError.malformedResponse
        }
        serverURL = try ServerOrigin(raw.serverURL).url
        serverID = raw.serverID
        code = raw.code
    }

    public init(deepLink: URL) throws {
        guard let components = URLComponents(url: deepLink, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "lumina",
              components.host?.lowercased() == "approve-tv",
              components.path.isEmpty || components.path == "/",
              components.user == nil, components.password == nil, components.port == nil,
              components.query == nil, let fragment = components.fragment else {
            throw LuminaError.malformedResponse
        }
        try self.init(scannedCode: fragment)
    }

    struct RawPayload: Codable {
        let type: String
        let version: Int
        let serverURL: String
        let serverID: String
        let code: String

        enum CodingKeys: String, CodingKey {
            case type, version, code
            case serverURL = "server_url"
            case serverID = "server_id"
        }
    }
}

public struct TVPairingInspection: Decodable, Sendable, Hashable {
    public let code: String
    public let deviceName: String
    public let expiresAt: String
    public let serverID: String
    public let status: TVPairingStatus

    enum CodingKeys: String, CodingKey {
        case code, status
        case deviceName = "device_name"
        case expiresAt = "expires_at"
        case serverID = "server_id"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        code = try values.decode(String.self, forKey: .code)
        deviceName = try values.decode(String.self, forKey: .deviceName)
        expiresAt = try values.decode(String.self, forKey: .expiresAt)
        serverID = try values.decode(String.self, forKey: .serverID)
        status = try values.decode(TVPairingStatus.self, forKey: .status)
        guard TVPairingRequest.validCode(code), TVPairingRequest.validName(deviceName),
              TVPairingRequest.validExpiry(expiresAt), TVPairingRequest.validIdentifier(serverID) else {
            throw LuminaError.malformedResponse
        }
    }
}

struct TVPairingClaim: Decodable {
    let username: String
    let password: String

    func validates() -> Bool {
        !username.isEmpty && username.utf8.count <= 256 && !password.isEmpty && password.utf8.count <= 512
    }
}

struct TVPairingStatusResponse: Decodable {
    let status: TVPairingStatus
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case status
        case expiresAt = "expires_at"
    }
}

struct TVPairingActionResponse: Decodable {
    let status: TVPairingStatus
}

extension TVPairingRequest {
    static func validCode(_ value: String) -> Bool {
        value.utf8.count == 8 && value.unicodeScalars.allSatisfy {
            "23456789ABCDEFGHJKLMNPQRSTUVWXYZ".unicodeScalars.contains($0)
        }
    }

    static func validIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 128 && value.unicodeScalars.allSatisfy { !CharacterSet.controlCharacters.contains($0) }
    }

    static func validSecret(_ value: String) -> Bool {
        value.utf8.count >= 32 && value.utf8.count <= 512 && value.unicodeScalars.allSatisfy { !CharacterSet.controlCharacters.contains($0) }
    }

    static func validName(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && value.utf8.count <= 128 &&
            value.unicodeScalars.allSatisfy { !CharacterSet.controlCharacters.contains($0) }
    }

    static func validExpiry(_ value: String) -> Bool {
        guard value.utf8.count <= 64 else { return false }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) != nil || ISO8601DateFormatter().date(from: value) != nil
    }
}

// The QR is the out-of-band key. Bonjour advertises only a random service name.
public struct TVPairingHandoff: Hashable, Sendable {
    private static let serviceType = "_lumina-pair._tcp"
    private static let limit = 8_192
    private let serviceName: String
    private let keyData: Data

    public init() {
        serviceName = UUID().uuidString.lowercased()
        keyData = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
    }

    public init(link: URL) throws {
        guard let parts = URLComponents(url: link, resolvingAgainstBaseURL: false),
              parts.scheme == "lumina", parts.host == "connect-tv",
              parts.user == nil, parts.password == nil, parts.port == nil, parts.query == nil,
              parts.path.count == 37, let id = UUID(uuidString: String(parts.path.dropFirst())),
              let secret = parts.fragment, secret.utf8.count == 44,
              let key = Data(base64Encoded: secret), key.count == 32 else {
            throw LuminaError.malformedResponse
        }
        serviceName = id.uuidString.lowercased()
        keyData = key
    }

    public var link: URL {
        // Both components have fixed alphabets and are generated or validated above.
        URL(string: "lumina://connect-tv/\(serviceName)#\(keyData.base64EncodedString())")!
    }

    public func requestApproval(serverURL: URL) async throws -> TVPairingApprovalLink {
        let origin = try ServerOrigin(serverURL.absoluteString).url
        let endpoint = NWEndpoint.service(name: serviceName, type: Self.serviceType, domain: "local.", interface: nil)
        let connection = NWConnection(to: endpoint, using: Self.parameters())
        return try await Self.exchange(connection, timeout: .seconds(20)) {
            try await send(origin.absoluteString, on: connection, response: false)
            let raw = try await receive(on: connection, response: true)
            guard let url = URL(string: raw) else { throw LuminaError.malformedResponse }
            let approval = try TVPairingApprovalLink(deepLink: url)
            guard approval.serverURL == origin else { throw LuminaError.malformedResponse }
            return approval
        }
    }

    public func awaitPhone(
        startPairing: @MainActor @Sendable (URL) async throws -> TVPairingRequest
    ) async throws {
        let listener = try NWListener(using: Self.parameters())
        listener.service = NWListener.Service(name: serviceName, type: Self.serviceType)
        let (connections, input) = AsyncThrowingStream<NWConnection, Error>.makeStream(bufferingPolicy: .bufferingOldest(1))
        listener.newConnectionHandler = { connection in
            switch input.yield(connection) {
            case .dropped(let dropped): dropped.cancel()
            case .terminated: connection.cancel()
            default: break
            }
        }
        listener.stateUpdateHandler = { state in
            if case .failed(let error) = state { input.finish(throwing: error) }
        }
        let expiry = Task {
            try await Task.sleep(for: .seconds(300))
            input.finish(throwing: URLError(.timedOut))
        }
        listener.start(queue: .global(qos: .userInitiated))
        defer {
            expiry.cancel()
            listener.cancel()
            input.finish()
        }
        for try await connection in connections {
            // Ignore unauthenticated local probes. Only a valid QR holder may
            // select a server and cause the listener to stop accepting requests.
            let accepted = try await Self.exchange(connection, timeout: .seconds(20)) {
                let origin: URL
                do {
                    origin = try ServerOrigin(try await receive(on: connection, response: false)).url
                } catch {
                    try Task.checkCancellation()
                    return false
                }
                try Task.checkCancellation()
                let request = try await startPairing(origin)
                try await send(try request.qrLink(serverURL: origin).absoluteString, on: connection, response: true)
                return true
            }
            if accepted { return }
        }
        try Task.checkCancellation()
    }

    private static func parameters() -> NWParameters {
        let parameters = NWParameters.tcp
        let webSocket = NWProtocolWebSocket.Options()
        webSocket.maximumMessageSize = limit
        parameters.defaultProtocolStack.applicationProtocols.insert(webSocket, at: 0)
        return parameters
    }

    private static func exchange<Result>(
        _ connection: NWConnection, timeout: Duration, operation: () async throws -> Result
    ) async throws -> Result {
        let expiry = Task {
            try await Task.sleep(for: timeout)
            connection.cancel()
        }
        defer {
            expiry.cancel()
            connection.cancel()
        }
        return try await withTaskCancellationHandler {
            try Task.checkCancellation()
            connection.start(queue: .global(qos: .userInitiated))
            return try await operation()
        } onCancel: {
            connection.cancel()
        }
    }

    private func authenticationContext(response: Bool) -> Data {
        Data("lumina-tv-handoff/1/\(serviceName)/\(response ? "approval" : "origin")".utf8)
    }

    private func send(_ message: String, on connection: NWConnection, response: Bool) async throws {
        let sealed = try ChaChaPoly.seal(Data(message.utf8), using: SymmetricKey(data: keyData),
                                         authenticating: authenticationContext(response: response))
        guard sealed.combined.count <= Self.limit else { throw LuminaError.malformedResponse }
        let context = NWConnection.ContentContext(identifier: "handoff", metadata: [NWProtocolWebSocket.Metadata(opcode: .binary)])
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.send(content: sealed.combined, contentContext: context, isComplete: true, completion: .contentProcessed { error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume() }
            })
        }
    }

    private func receive(on connection: NWConnection, response: Bool) async throws -> String {
        let data: Data = try await withCheckedThrowingContinuation { continuation in
            connection.receiveMessage { data, _, complete, error in
                if let error { continuation.resume(throwing: error) }
                else if let data, complete, data.count <= Self.limit { continuation.resume(returning: data) }
                else { continuation.resume(throwing: LuminaError.malformedResponse) }
            }
        }
        let plain = try ChaChaPoly.open(ChaChaPoly.SealedBox(combined: data), using: SymmetricKey(data: keyData),
                                        authenticating: authenticationContext(response: response))
        guard let message = String(data: plain, encoding: .utf8) else { throw LuminaError.malformedResponse }
        return message
    }
}
