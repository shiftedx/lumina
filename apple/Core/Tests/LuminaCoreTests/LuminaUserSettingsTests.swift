import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaUserSettingsTests: XCTestCase {
    override func setUp() {
        super.setUp()
        UserSettingsProtocol.handler = nil
    }

    func testAuthenticatedLoadUpdateActionAndFailureRetainTheReturnedDocument() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UserSettingsProtocol.self]
        let settings = LuminaUserSettings(
            session: LuminaSession(
                serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
                username: "Member", serverName: "Lumina", deviceID: "settings-device"
            ),
            client: LuminaClient(configuration: configuration)
        )
        let recorder = UserSettingsRequestRecorder()
        let replies = UserSettingsReplies()
        UserSettingsProtocol.handler = { request in
            let body = recorder.record(request)
            return replies.response(for: request, body: body)
        }

        await settings.load()
        XCTAssertTrue(settings.hasLoaded)
        XCTAssertNil(settings.errorMessage)
        XCTAssertEqual(settings.theme, "dark")
        XCTAssertTrue(settings.autoplayUpNext)
        XCTAssertEqual(settings.autoplayPresencePolicy, .init(enabled: false, episodeLimit: 6, timeoutSeconds: 900))
        XCTAssertEqual(settings.document["ui_prefs"]?["density"]?.stringValue, "compact")
        XCTAssertEqual(settings.document["remote_playback_cache"]?["enabled"]?.boolValue, true)
        XCTAssertTrue(settings.streamingEnabled)
        XCTAssertTrue(settings.youtubeSessionEnabled)

        let patch: [String: SettingsValue] = [
            "ui_prefs": .object(["theme": .string("light"), "autoplay_up_next": .bool(false)]),
        ]
        let didUpdate = await settings.update(patch)
        XCTAssertTrue(didUpdate)
        XCTAssertEqual(settings.theme, "light")
        XCTAssertFalse(settings.autoplayUpNext)
        XCTAssertEqual(settings.document["ui_prefs"]?["density"]?.stringValue, "compact")

        let action = try await settings.action("connected-apps/refresh", method: "POST", body: ["enabled": .bool(true)], reauth: "one-use-proof")
        XCTAssertEqual(action["state"]?.stringValue, "ready")
        let emptyAction = try await settings.action("discovery/search", method: "DELETE")
        XCTAssertEqual(emptyAction, .null)
        do {
            _ = try await settings.action("../admin?token=member-token")
            XCTFail("Unsafe settings action path was accepted.")
        } catch UserSettingsError.invalidActionPath { }

        let beforeFailure = settings.document
        let didFail = await settings.update(patch)
        XCTAssertFalse(didFail)
        XCTAssertEqual(settings.document, beforeFailure)
        XCTAssertNotNil(settings.errorMessage)

        let requests = recorder.requests
        XCTAssertEqual(requests.map { $0.request.httpMethod }, ["GET", "GET", "PUT", "POST", "DELETE", "PUT"])
        XCTAssertEqual(requests.map { $0.request.url?.path }, [
            "/Lumina/Settings", "/Lumina/Settings/capabilities", "/Lumina/Settings", "/Lumina/Settings/connected-apps/refresh", "/Lumina/Settings/discovery/search", "/Lumina/Settings",
        ])
        for recorded in requests {
            XCTAssertTrue(recorded.request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true)
            XCTAssertFalse(recorded.request.url?.query?.lowercased().contains("token") == true)
        }
        XCTAssertEqual(try settingsBody(requests[2].body), patch)
        XCTAssertEqual(try settingsBody(requests[3].body), ["enabled": .bool(true)])
        XCTAssertEqual(requests[3].request.value(forHTTPHeaderField: "X-Lumina-Reauth"), "one-use-proof")

        let roundTrip = try JSONDecoder().decode(SettingsValue.self, from: JSONEncoder().encode(SettingsValue.object([
            "answer": .number(42), "items": .array([.null, .string("kept")]),
        ])))
        XCTAssertEqual(roundTrip["answer"]?.numberValue, 42)
        XCTAssertEqual(roundTrip["items"]?.arrayValue?.first, .null)
    }

    func testStreamingCapabilityFailsClosedForDisabledLegacyAndMalformedResponses() async throws {
        enum CapabilityResponse: Equatable {
            case disabled
            case twitchOnly
            case missingYouTubePermission
            case missing
            case malformed
        }

        for capabilityResponse in [
            CapabilityResponse.disabled, .twitchOnly, .missingYouTubePermission, .missing, .malformed,
        ] {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [UserSettingsProtocol.self]
            let settings = LuminaUserSettings(
                session: settingsSession(token: "member-token"),
                client: LuminaClient(configuration: configuration)
            )
            UserSettingsProtocol.handler = { request in
                let value: Any
                let status: Int
                switch request.url?.path {
                case "/Lumina/Settings":
                    status = 200
                    value = settingsDocument(theme: "dark")
                case "/Lumina/Settings/capabilities":
                    switch capabilityResponse {
                    case .disabled:
                        status = 200
                        value = ["streaming_enabled": false, "youtube_session_enabled": true]
                    case .twitchOnly:
                        status = 200
                        value = ["streaming_enabled": true, "youtube_session_enabled": false]
                    case .missingYouTubePermission:
                        status = 200
                        value = ["streaming_enabled": true]
                    case .missing:
                        status = 404
                        value = ["detail": "not_found"]
                    case .malformed:
                        status = 200
                        value = ["streaming_enabled": "yes"]
                    }
                default:
                    status = 404
                    value = ["detail": "not_found"]
                }
                let data = try JSONSerialization.data(withJSONObject: value)
                let response = try XCTUnwrap(HTTPURLResponse(
                    url: try XCTUnwrap(request.url), statusCode: status, httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                ))
                return (response, data)
            }

            await settings.load()

            XCTAssertTrue(settings.hasLoaded)
            XCTAssertEqual(settings.theme, "dark")
            XCTAssertEqual(settings.streamingEnabled, capabilityResponse == .twitchOnly || capabilityResponse == .missingYouTubePermission)
            XCTAssertFalse(settings.youtubeSessionEnabled)
            XCTAssertNil(settings.errorMessage)
        }
    }

    func testStreamingCapabilityIsClearedWhenTheSessionChanges() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UserSettingsProtocol.self]
        let settings = LuminaUserSettings(
            session: settingsSession(token: "first-token"),
            client: LuminaClient(configuration: configuration)
        )
        UserSettingsProtocol.handler = { request in
            let value: Any
            switch (request.url?.path, request.value(forHTTPHeaderField: "Authorization")) {
            case ("/Lumina/Settings", let authorization) where authorization?.contains("first-token") == true:
                value = settingsDocument(theme: "dark")
            case ("/Lumina/Settings/capabilities", let authorization) where authorization?.contains("first-token") == true:
                value = ["streaming_enabled": true, "youtube_session_enabled": true]
            case ("/Lumina/Settings", let authorization) where authorization?.contains("second-token") == true:
                value = settingsDocument(theme: "light")
            case ("/Lumina/Settings/capabilities", let authorization) where authorization?.contains("second-token") == true:
                value = ["streaming_enabled": false, "youtube_session_enabled": true]
            default:
                let response = try XCTUnwrap(HTTPURLResponse(
                    url: try XCTUnwrap(request.url), statusCode: 404, httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                ))
                return (response, try JSONSerialization.data(withJSONObject: ["detail": "not_found"]))
            }
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url), statusCode: 200, httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, try JSONSerialization.data(withJSONObject: value))
        }

        await settings.load()
        XCTAssertTrue(settings.streamingEnabled)
        XCTAssertTrue(settings.youtubeSessionEnabled)

        settings.configure(session: settingsSession(token: "second-token"))
        XCTAssertFalse(settings.streamingEnabled)
        XCTAssertFalse(settings.youtubeSessionEnabled)
        XCTAssertFalse(settings.hasLoaded)

        await settings.load()
        XCTAssertFalse(settings.streamingEnabled)
        XCTAssertFalse(settings.youtubeSessionEnabled)
        XCTAssertEqual(settings.theme, "light")
    }
}

private final class UserSettingsProtocol: URLProtocol {
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

private struct UserSettingsRecordedRequest {
    let request: URLRequest
    let body: Data?
}

private final class UserSettingsRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = [UserSettingsRecordedRequest]()

    func record(_ request: URLRequest) -> Data? {
        let body = try? userSettingsBody(request)
        lock.withLock { stored.append(UserSettingsRecordedRequest(request: request, body: body)) }
        return body
    }

    var requests: [UserSettingsRecordedRequest] { lock.withLock { stored } }
}

private final class UserSettingsReplies: @unchecked Sendable {
    private let lock = NSLock()
    private var putCount = 0

    func response(for request: URLRequest, body: Data?) -> (HTTPURLResponse, Data) {
        let status: Int
        let value: Any
        lock.lock()
        defer { lock.unlock() }
        switch (request.httpMethod, request.url?.path) {
        case ("GET", "/Lumina/Settings"):
            status = 200
            value = settingsDocument(theme: "dark")
        case ("GET", "/Lumina/Settings/capabilities"):
            status = 200
            value = ["streaming_enabled": true, "youtube_session_enabled": true,
                     "autoplay_presence": ["enabled": false, "episode_limit": 6, "timeout_seconds": 900]]
        case ("PUT", "/Lumina/Settings"):
            putCount += 1
            if putCount == 1 {
                status = 200
                var updated = settingsDocument(theme: "light")
                updated["ui_prefs"] = ["theme": "light", "density": "compact", "autoplay_up_next": false]
                value = updated
            } else {
                status = 503
                value = ["detail": "temporarily_unavailable"]
            }
        case ("POST", "/Lumina/Settings/connected-apps/refresh"):
            status = 200
            value = ["state": "ready"]
        case ("DELETE", "/Lumina/Settings/discovery/search"):
            let response = HTTPURLResponse(url: request.url!, statusCode: 204, httpVersion: nil, headerFields: nil)!
            return (response, Data())
        default:
            status = 404
            value = ["detail": "not_found"]
        }
        let data = try! JSONSerialization.data(withJSONObject: value)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        return (response, data)
    }
}

private func settingsSession(token: String) -> LuminaSession {
    LuminaSession(
        serverURL: URL(string: "https://lumina.example")!, accessToken: token, userID: "member",
        username: "Member", serverName: "Lumina", deviceID: "settings-device"
    )
}

private func settingsDocument(theme: String) -> [String: Any] {
    [
        "id": "settings-1", "user_id": "member",
        "download_defaults": ["format": "best"], "automation_defaults": [:],
        "ui_prefs": ["theme": theme, "density": "compact"],
        "notification_prefs": ["email": true],
        "remote_playback_cache": ["enabled": true, "recent_video_limit": 5],
        "resolved_download_defaults": [:], "resolved_automation_defaults": [:],
        "created_at": "2026-10-22T00:00:00Z", "updated_at": "2026-10-22T00:00:00Z",
    ]
}

private func settingsBody(_ data: Data?) throws -> [String: SettingsValue] {
    try JSONDecoder().decode([String: SettingsValue].self, from: try XCTUnwrap(data))
}

private func userSettingsBody(_ request: URLRequest) throws -> Data? {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { return nil }
    stream.open()
    defer { stream.close() }
    var data = Data()
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4_096)
    defer { buffer.deallocate() }
    while stream.hasBytesAvailable {
        let count = stream.read(buffer, maxLength: 4_096)
        guard count >= 0 else { throw stream.streamError ?? URLError(.badServerResponse) }
        if count == 0 { break }
        data.append(buffer, count: count)
    }
    return data
}
