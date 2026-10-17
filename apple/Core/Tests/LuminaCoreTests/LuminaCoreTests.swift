import Foundation
import Security
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaCoreTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MockURLProtocol.handler = nil
    }

    func testConnectLoadsLibraryAndSendsMediaBrowserCredentials() async throws {
        let credentials = TestCredentials()
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: credentials)
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/System/Info/Public":
                return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName":
                return response(request, json: ["AccessToken": "test-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me":
                return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items":
                return response(request, json: page(items: [movie()]))
            case "/UserItems/Resume":
                return response(request, json: page(items: []))
            default:
                return response(request, status: 404)
            }
        }

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(store.session?.serverURL.absoluteString, "http://127.0.0.1:8765")
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])
        let calls = requests.all
        XCTAssertEqual(calls.compactMap(\.url?.path), ["/System/Info/Public", "/Users/AuthenticateByName", "/Users/Me", "/Items", "/UserItems/Resume"])
        XCTAssertEqual(calls[1].httpMethod, "POST")
        XCTAssertTrue(calls[1].value(forHTTPHeaderField: "Authorization")!.contains("DeviceId=\"test-device\""))
        XCTAssertEqual(try body(calls[1])["Username"] as? String, "member")
        XCTAssertTrue(calls[2].value(forHTTPHeaderField: "Authorization")!.contains("Token=\"test-token\""))
        XCTAssertEqual(URLComponents(url: calls[3].url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "IncludeItemTypes" })?.value, "Movie,Episode,Video")
        XCTAssertNil(store.errorMessage)
    }

    func testPlaybackInfoUsesOnlySameOriginHLSAndReportsPositions() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "session-1", "MediaSources": [[
                    "Id": "source-1", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-1/master.m3u8?ApiKey=test-token",
                ]]])
            case "/Sessions/Playing/Progress":
                return response(request, status: 204)
            default:
                return response(request, status: 404)
            }
        }

        let info = try await client.playbackInfo(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", deviceID: "test-device", item: item, forceTranscode: true)
        let request = try client.playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)
        try await client.report(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", deviceID: "test-device", event: .progress, request: request, positionSeconds: 12.5, isPaused: true)

        XCTAssertEqual(request.url.host, "127.0.0.1")
        XCTAssertTrue(request.isTranscoding)
        let playbackBody = try body(requests.all[0])
        XCTAssertEqual(playbackBody["EnableDirectPlay"] as? Bool, false)
        let profile = playbackBody["DeviceProfile"] as? [String: Any]
        let direct = (profile?["DirectPlayProfiles"] as? [[String: Any]])?.first?["Container"] as? String
        XCTAssertEqual(direct, "mp4,m4v,mov")
        XCTAssertEqual(try body(requests.all[1])["PositionTicks"] as? Int64, 125_000_000)
        XCTAssertEqual(try body(requests.all[1])["IsPaused"] as? Bool, true)
    }

    func testPlaybackRefreshesAStaleDetailItemBeforePreparing() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie(positionTicks: 250_000_000))
            case "/Items/movie-1/PlaybackInfo": return response(request, json: ["PlaySessionId": "session-1", "MediaSources": [["Id": "source-1", "SupportsDirectPlay": true, "SupportsTranscoding": false]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let stale = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: stale)

        XCTAssertEqual(request.startPositionSeconds, 25)
        let infoRequest = requests.all.first(where: { $0.url?.path == "/Items/movie-1/PlaybackInfo" })!
        XCTAssertEqual(try body(infoRequest)["StartTimeTicks"] as? Int64, 250_000_000)
    }

    func testRefusesCrossOriginPlaybackURL() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json(["PlaySessionId": "session-1", "MediaSources": [[
            "Id": "source-1", "SupportsDirectPlay": false, "SupportsTranscoding": true, "TranscodingUrl": "https://elsewhere.invalid/master.m3u8",
        ]]]))
        XCTAssertThrowsError(try LuminaClient().playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)) {
            XCTAssertEqual($0 as? LuminaError, .unsafeURL)
        }
    }

    func testUnauthorizedRestoreClearsStoredSessionAndExplainsAppPassword() async throws {
        let credentials = TestCredentials(session: LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "expired-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device"))
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in response(request, status: 401) }

        await store.restore()

        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertEqual(store.errorMessage, "Your session expired. Sign in again.")
    }

    func testOfflineRestoreKeepsStoredSessionForRetry() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "saved-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let credentials = TestCredentials(session: saved)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }

        await store.restore()

        XCTAssertEqual(store.session?.accessToken, "saved-token")
        XCTAssertEqual(credentials.stored?.accessToken, "saved-token")
        XCTAssertEqual(store.errorMessage, "Could not reach the Lumina server.")
    }

    func testUnavailableKeychainDoesNotAttemptAuthentication() async throws {
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: ["ServerName": "Lumina"])
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: FailingCredentials())

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(requests.all.compactMap(\.url?.path), ["/System/Info/Public"])
        XCTAssertEqual(store.errorMessage, "Secure credential storage is unavailable. Use a signed app build and try again.")
    }

    func testSignOutClearsLocalSessionBeforeDelayedFailedRevocation() async throws {
        let credentials = TestCredentials(session: LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device"))
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }
        await store.restore()

        let requests = RequestRecorder()
        let logoutStarted = expectation(description: "logout request started")
        let releaseLogout = DispatchSemaphore(value: 0)
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Sessions/Logout":
                logoutStarted.fulfill()
                releaseLogout.wait()
                throw URLError(.notConnectedToInternet)
            case "/System/Info/Public": return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return response(request, json: ["AccessToken": "new-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        let signOut = Task { await store.signOut() }
        await fulfillment(of: [logoutStarted], timeout: 1)

        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertNil(store.errorMessage)
        releaseLogout.signal()
        await signOut.value
        XCTAssertNil(store.errorMessage, "A failed revocation must not replace the signed-out connection form with an old session error.")

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(store.session?.accessToken, "new-token")
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(requests.all.first?.url?.path, "/Sessions/Logout")
    }
}

private final class TestCredentials: CredentialStore {
    var stored: LuminaSession?
    init(session: LuminaSession? = nil) { stored = session }
    func deviceID() throws -> String { "test-device" }
    func save(session: LuminaSession) throws { stored = session }
    func load() throws -> LuminaSession? { stored }
    func deleteSession() { stored = nil }
}

private final class FailingCredentials: CredentialStore {
    func deviceID() throws -> String { throw LuminaError.keychain(errSecMissingEntitlement) }
    func save(session: LuminaSession) throws {}
    func load() throws -> LuminaSession? { nil }
    func deleteSession() {}
}

private final class RequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()
    func append(_ request: URLRequest) { lock.withLock { requests.append(request) } }
    var all: [URLRequest] { lock.withLock { requests } }
}

private final class MockURLProtocol: URLProtocol {
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

private func mockConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [MockURLProtocol.self]
    return configuration
}

private func response(_ request: URLRequest, status: Int = 200, json object: Any = [:]) -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, json(object))
}

private func body(_ request: URLRequest) throws -> [String: Any] {
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

private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }
private func page(items: [[String: Any]]) -> [String: Any] { ["Items": items, "TotalRecordCount": items.count, "StartIndex": 0] }
private func movie(positionTicks: Int64? = nil) -> [String: Any] {
    var item: [String: Any] = ["Id": "movie-1", "Name": "Example Movie", "Type": "Movie", "RunTimeTicks": 120_000_000]
    if let positionTicks { item["UserData"] = ["PlaybackPositionTicks": positionTicks, "Played": false] }
    return item
}
