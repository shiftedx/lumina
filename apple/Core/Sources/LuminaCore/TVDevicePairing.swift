import Foundation

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
