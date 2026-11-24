import Foundation
import Security

public enum LuminaError: Error, Equatable {
    case invalidOrigin, unsafeURL, notSignedIn, malformedResponse, http(Int), keychain(OSStatus)
}

struct ServerOrigin {
    let url: URL
    init(_ value: String) throws {
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let authority = value.range(of: "://").map { range in
            value[range.upperBound...].prefix { $0 != "/" && $0 != "?" && $0 != "#" }
        }
        guard authority?.last != ":",
              var parts = URLComponents(string: value),
              let scheme = parts.scheme?.lowercased(), ["http", "https"].contains(scheme), parts.host != nil,
              parts.port.map({ (1...65_535).contains($0) }) != false,
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

final class KeychainCredentials: CredentialStore {
    // Keep Security calls injectable so credential migration can be checked without a signed app's Keychain.
    struct Operations {
        var copyMatching: (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus = SecItemCopyMatching
        var add: (CFDictionary, UnsafeMutablePointer<CFTypeRef?>?) -> OSStatus = SecItemAdd
        var update: (CFDictionary, CFDictionary) -> OSStatus = SecItemUpdate
        var delete: (CFDictionary) -> OSStatus = SecItemDelete
    }
    private let operations: Operations
    init(operations: Operations = Operations()) { self.operations = operations }

    private let service: String = {
        #if DEBUG || LUMINA_UI_TESTING
        if let namespace = ProcessInfo.processInfo.environment["LUMINA_UI_TEST_NAMESPACE"],
           UUID(uuidString: namespace) != nil { return "dev.lumina.apple.ui-test.\(namespace)" }
        #endif
        return "dev.lumina.apple"
    }()
    private let deviceKey = "device-id"
    private let sessionKey = "session"

    func deviceID() throws -> String {
        if let data = try read(deviceKey) {
            guard let id = String(data: data, encoding: .utf8), !id.isEmpty else { throw LuminaError.keychain(errSecDecode) }
            return id
        }
        let id = UUID().uuidString.lowercased()
        try write(Data(id.utf8), key: deviceKey)
        return id
    }
    func save(session: LuminaSession) throws { try write(JSONEncoder().encode(session), key: sessionKey) }
    func load() throws -> LuminaSession? { guard let data = try read(sessionKey) else { return nil }; return try JSONDecoder().decode(LuminaSession.self, from: data) }
    func deleteSession() { delete(sessionKey) }

    private func query(_ key: String) -> [CFString: Any] { [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: key] }
    private func read(_ key: String) throws -> Data? {
        var values = query(key)
        values[kSecReturnData] = true; values[kSecReturnAttributes] = true; values[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = operations.copyMatching(values as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw LuminaError.keychain(status) }
        guard let item = result as? [String: Any], let data = item[kSecValueData as String] as? Data else {
            throw LuminaError.keychain(errSecDecode)
        }
        if item[kSecAttrAccessible as String] as? String != kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String {
            // Preserve background access, but stop credentials migrating to a different device via backup.
            let status = operations.update(query(key) as CFDictionary, [kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly] as CFDictionary)
            guard status == errSecSuccess else { throw LuminaError.keychain(status) }
        }
        return data
    }
    private func write(_ data: Data, key: String) throws {
        let attributes: [CFString: Any] = [kSecValueData: data, kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        // Updating in place preserves the previous session/device identity if Keychain is unavailable.
        var status = operations.update(query(key) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = operations.add(query(key).merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw LuminaError.keychain(status) }
    }
    private func delete(_ key: String) { _ = operations.delete(query(key) as CFDictionary) }
}

extension LuminaError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Sign in to access your library."
        case .http(401): return "Your session expired. Sign in again."
        case .http(403): return "Your account cannot access this media."
        case .http(404): return "This item is no longer available."
        case .http(let code): return "The server returned HTTP \(code). Try again."
        default: return nil
        }
    }
}
