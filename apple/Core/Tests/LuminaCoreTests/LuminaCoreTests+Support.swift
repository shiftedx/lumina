import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

func syntheticKeychainSession(deviceID: String = "test-device") -> LuminaSession {
    LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: deviceID)
}

/// A synthetic Security API boundary: no process, simulator or signed app Keychain is touched.
final class SyntheticKeychain {
    var items = [String: [String: Any]]()
    var readFailure: OSStatus?
    var updateFailure: OSStatus?
    var updates = 0
    var additions = 0
    var deletions = 0

    init() {}
    convenience init(legacySession: LuminaSession) throws {
        self.init()
        for (key, data) in [("session", try JSONEncoder().encode(legacySession)), ("device-id", Data(legacySession.deviceID.utf8))] {
            items[key] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock]
        }
    }

    var operations: KeychainCredentials.Operations {
        var operations = KeychainCredentials.Operations()
        operations.copyMatching = { [self] query, result in
            if let readFailure { return readFailure }
            let query = query as! [String: Any]
            guard let item = items[query[kSecAttrAccount as String] as! String] else { return errSecItemNotFound }
            XCTAssertEqual(query[kSecReturnData as String] as? Bool, true)
            XCTAssertEqual(query[kSecReturnAttributes as String] as? Bool, true)
            result?.pointee = item as CFDictionary
            return errSecSuccess
        }
        operations.update = { [self] query, attributes in
            updates += 1
            if let updateFailure { return updateFailure }
            let key = (query as! [String: Any])[kSecAttrAccount as String] as! String
            guard let old = items[key] else { return errSecItemNotFound }
            items[key] = old.merging(attributes as! [String: Any]) { _, new in new }
            return errSecSuccess
        }
        operations.add = { [self] query, _ in
            additions += 1
            let item = query as! [String: Any]
            let key = item[kSecAttrAccount as String] as! String
            guard items[key] == nil else { return errSecDuplicateItem }
            items[key] = item
            return errSecSuccess
        }
        operations.delete = { [self] query in
            deletions += 1
            let key = (query as! [String: Any])[kSecAttrAccount as String] as! String
            return items.removeValue(forKey: key) == nil ? errSecItemNotFound : errSecSuccess
        }
        return operations
    }
}

final class TestCredentials: CredentialStore {
    var stored: LuminaSession?
    init(session: LuminaSession? = nil) { stored = session }
    func deviceID() throws -> String { "test-device" }
    func save(session: LuminaSession) throws { stored = session }
    func load() throws -> LuminaSession? { stored }
    func deleteSession() { stored = nil }
}

final class FailingCredentials: CredentialStore {
    func deviceID() throws -> String { throw LuminaError.keychain(errSecMissingEntitlement) }
    func save(session: LuminaSession) throws {}
    func load() throws -> LuminaSession? { nil }
    func deleteSession() {}
}

final class RequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()
    func append(_ request: URLRequest) { lock.withLock { requests.append(request) } }
    var all: [URLRequest] { lock.withLock { requests } }
}

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func increment() -> Int { lock.withLock { value += 1; return value } }
}

final class HomeRequestTimeline: @unchecked Sendable {
    struct Measurement {
        let elapsedMilliseconds: Int
        let firstCompletionMilliseconds: Int
        let maxConcurrent: Int
    }

    private let lock = NSLock()
    private var startedAt: Date?
    private var firstCompletedAt: Date?
    private var activeRequests = 0
    private var peakRequests = 0

    var isMeasuring: Bool { lock.withLock { startedAt != nil } }

    func start() {
        lock.withLock {
            startedAt = Date()
            firstCompletedAt = nil
            activeRequests = 0
            peakRequests = 0
        }
    }

    func begin(_ path: String) {
        lock.withLock {
            guard Self.homePaths.contains(path) else { return }
            guard startedAt != nil else { return }
            activeRequests += 1
            peakRequests = max(peakRequests, activeRequests)
        }
    }

    func end(_ path: String) {
        lock.withLock {
            guard Self.homePaths.contains(path) else { return }
            guard activeRequests > 0 else { return }
            activeRequests -= 1
            if firstCompletedAt == nil { firstCompletedAt = Date() }
        }
    }

    private static let homePaths: Set<String> = ["/Items", "/UserItems/Resume", "/Shows/NextUp"]

    var measurement: Measurement {
        lock.withLock {
            Measurement(
                elapsedMilliseconds: Int(Date().timeIntervalSince(startedAt ?? Date()) * 1_000),
                firstCompletionMilliseconds: Int((firstCompletedAt ?? Date()).timeIntervalSince(startedAt ?? Date()) * 1_000),
                maxConcurrent: peakRequests
            )
        }
    }
}

final class PathCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var counts = [String: Int]()

    func next(_ path: String) -> Int {
        lock.withLock {
            counts[path, default: 0] += 1
            return counts[path]!
        }
    }

    func count(for path: String) -> Int {
        lock.withLock { counts[path, default: 0] }
    }
}

final class RequestStartRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    private var waiter: CheckedContinuation<Void, Never>?

    func record(_ request: URLRequest) {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            count += 1
            guard let waiter, count >= 3 else { return nil }
            self.waiter = nil
            return waiter
        }
        waiter?.resume()
    }

    func waitForThree() async {
        await wait(for: 3)
    }

    func wait(for target: Int) async {
        await withCheckedContinuation { continuation in
            let resume = lock.withLock { () -> Bool in
                guard count < target else { return true }
                waiter = continuation
                return false
            }
            if resume { continuation.resume() }
        }
    }
}

final class MockURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.badServerResponse) }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
    override func stopLoading() {}
}

func mockConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [MockURLProtocol.self]
    return configuration
}

func homeConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [HomeDelayedURLProtocol.self]
    return configuration
}

final class HomeDelayedURLProtocol: URLProtocol {
    private struct Pending {
        let `protocol`: HomeDelayedURLProtocol
        let timeline: HomeRequestTimeline?
        let path: String
    }

    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var delay: @Sendable (URLRequest) -> TimeInterval = { _ in 0 }
    nonisolated(unsafe) static var onStart: (@Sendable (URLRequest) -> Void)?
    nonisolated(unsafe) static weak var timeline: HomeRequestTimeline?
    private static let lock = NSLock()
    nonisolated(unsafe) private static var pending = [UUID: Pending]()
    nonisolated(unsafe) private static var cancellationCount = 0

    private var identifier: UUID?

    static var cancelledRequests: Int { lock.withLock { cancellationCount } }

    static func reset() {
        let pending = lock.withLock { () -> [Pending] in
            let pending = Array(Self.pending.values)
            Self.pending.removeAll()
            Self.handler = nil
            Self.delay = { _ in 0 }
            Self.onStart = nil
            Self.timeline = nil
            Self.cancellationCount = 0
            return pending
        }
        pending.forEach { $0.timeline?.end($0.path) }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let request = request
        let timeline = Self.timeline
        let path = request.url?.path ?? ""
        let identifier = UUID()
        self.identifier = identifier
        let handler = Self.handler
        let delay = Self.delay(request)
        timeline?.begin(path)
        Self.lock.withLock {
            Self.pending[identifier] = Pending(protocol: self, timeline: timeline, path: path)
        }
        Self.onStart?(request)
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) {
            Self.complete(identifier: identifier, request: request, handler: handler)
        }
    }

    override func stopLoading() {
        guard let identifier else { return }
        self.identifier = nil
        let pending = Self.lock.withLock { () -> Pending? in
            guard let pending = Self.pending.removeValue(forKey: identifier) else { return nil }
            Self.cancellationCount += 1
            return pending
        }
        if let pending { pending.timeline?.end(pending.path) }
    }

    private static func complete(identifier: UUID, request: URLRequest, handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?) {
        guard let pending = lock.withLock({ Self.pending.removeValue(forKey: identifier) }) else { return }
        defer { pending.timeline?.end(pending.path) }
        do {
            guard let handler else { throw URLError(.badServerResponse) }
            let (response, data) = try handler(request)
            pending.protocol.client?.urlProtocol(pending.protocol, didReceive: response, cacheStoragePolicy: .notAllowed)
            pending.protocol.client?.urlProtocol(pending.protocol, didLoad: data)
            pending.protocol.client?.urlProtocolDidFinishLoading(pending.protocol)
        } catch {
            pending.protocol.client?.urlProtocol(pending.protocol, didFailWithError: error)
        }
    }
}

func coreResponse(_ request: URLRequest, status: Int = 200, json object: Any = [:]) -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, json(object))
}

func body(_ request: URLRequest) throws -> [String: Any] {
    let data: Data
    if let body = request.httpBody {
        data = body
    } else if let stream = request.httpBodyStream {
        stream.open()
        defer { stream.close() }
        var bytes = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count > 0 else { break }
            bytes.append(buffer, count: count)
        }
        data = bytes
    } else {
        data = Data()
    }
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }
func page(items: [[String: Any]]) -> [String: Any] { ["Items": items, "TotalRecordCount": items.count, "StartIndex": 0] }
func movie(positionTicks: Int64? = nil) -> [String: Any] {
    var item: [String: Any] = ["Id": "movie-1", "Name": "Example Movie", "Type": "Movie", "RunTimeTicks": 120_000_000]
    if let positionTicks { item["UserData"] = ["PlaybackPositionTicks": positionTicks, "Played": false] }
    return item
}
