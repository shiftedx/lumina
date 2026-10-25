import Foundation

public struct MobilePairingPayload: Sendable {
    public let serverURL: URL
    let token: String

    public init(scannedCode: String) throws {
        guard scannedCode.utf8.count <= 8_192,
              let data = scannedCode.data(using: .utf8) else { throw LuminaError.malformedResponse }
        let raw = try JSONDecoder().decode(RawPayload.self, from: data)
        guard raw.type == "lumina-pair", raw.version == 1,
              !raw.token.isEmpty, raw.token.utf8.count <= 512 else { throw LuminaError.malformedResponse }
        serverURL = try ServerOrigin(raw.serverURL).url
        token = raw.token
    }

    public init(deepLink: URL) throws {
        guard let components = URLComponents(url: deepLink, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "lumina",
              components.host?.lowercased() == "pair",
              components.path.isEmpty || components.path == "/",
              components.user == nil, components.password == nil, components.port == nil,
              components.query == nil,
              let fragment = components.fragment else { throw LuminaError.malformedResponse }
        try self.init(scannedCode: fragment)
    }

    private struct RawPayload: Decodable {
        let type: String
        let version: Int
        let serverURL: String
        let token: String

        enum CodingKeys: String, CodingKey {
            case type, version, token
            case serverURL = "server_url"
        }
    }
}

struct MobileConnectClaim: Decodable {
    let username: String
    let password: String
    let serverURL: String

    enum CodingKeys: String, CodingKey {
        case username, password
        case serverURL = "server_url"
    }
}
