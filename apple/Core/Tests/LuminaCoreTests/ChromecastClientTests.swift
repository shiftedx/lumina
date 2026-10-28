import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class ChromecastClientTests: XCTestCase {
    override func setUp() {
        super.setUp()
        ChromecastURLProtocol.handler = nil
    }

    override func tearDown() {
        ChromecastURLProtocol.handler = nil
        super.tearDown()
    }

    func testMintUsesConnectedAppAuthenticationAndReturnsValidatedReceiverTicket() async throws {
        let ticketID = UUID()
        let client = LuminaClient(configuration: configuration())
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            return response(request, json: ticketPayload(ticketID: ticketID))
        }

        let ticket = try await client.prepareChromecast(
            origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
            itemID: "movie-1", positionSeconds: 12.5, audioIndex: 2, subtitle: "e:4"
        )

        XCTAssertEqual(ticket.ticketID, ticketID)
        XCTAssertEqual(ticket.mediaURL.host, "livingroom.lan")
        XCTAssertEqual(ticket.durationSeconds, 7_200)
        XCTAssertEqual(ticket.positionSeconds, 12.5)
        XCTAssertEqual(ticket.subtitle?.language, "en")
        XCTAssertEqual(ticket.qualification, "unverified_hardware")
        let request = try XCTUnwrap(observed.last)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/Lumina/Cast/tickets")
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true)
        XCTAssertNil(request.url?.query)
        let body = try XCTUnwrap(try requestBody(request).jsonObject() as? [String: Any])
        XCTAssertEqual(body["item_id"] as? String, "movie-1")
        XCTAssertEqual(body["position_seconds"] as? Double, 12.5)
        XCTAssertEqual(body["audio_index"] as? Int, 2)
        XCTAssertEqual(body["subtitle"] as? String, "e:4")

        let receiver = try XCTUnwrap(URLComponents(url: ticket.mediaURL, resolvingAgainstBaseURL: false))
        XCTAssertEqual(receiver.queryItems, [URLQueryItem(name: "token", value: "receiver-capability")])
        XCTAssertNil(ticket.mediaURL.user)
        XCTAssertNil(ticket.mediaURL.password)
        XCTAssertNotEqual(ticket.mediaURL.host, "native.example")
    }

    func testConvertedTicketAcceptsTextCaptionsWithoutLanguageMetadata() async throws {
        let ticketID = UUID()
        let client = LuminaClient(configuration: configuration())
        ChromecastURLProtocol.handler = { request in
            var payload = ticketPayload(ticketID: ticketID)
            payload["content_type"] = "application/vnd.apple.mpegurl"
            var subtitle = try XCTUnwrap(payload["subtitle"] as? [String: Any])
            subtitle["language"] = NSNull()
            payload["subtitle"] = subtitle
            return response(request, json: payload)
        }
        let ticket = try await client.prepareChromecast(
            origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
            itemID: "source-version", positionSeconds: 12.5, audioIndex: nil, subtitle: "j:4"
        )
        XCTAssertEqual(ticket.contentType, "application/vnd.apple.mpegurl")
        XCTAssertNil(ticket.subtitle?.language)
        XCTAssertEqual(ticket.subtitle?.label, "English")
    }

    func testRenewAndRevokeUseTicketSpecificCompatibleRoutes() async throws {
        let ticketID = UUID()
        let client = LuminaClient(configuration: configuration())
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            if request.url?.path.hasSuffix("/renew") == true {
                return response(request, json: ["ticket_id": ticketID.uuidString, "expires_at": "2026-10-27T18:00:00Z"])
            }
            return response(request, status: 204, data: Data())
        }

        try await client.renewChromecast(origin: URL(string: "https://native.example")!, token: "member-token",
                                         deviceID: "fixture-device", ticketID: ticketID)
        try await client.revokeChromecast(origin: URL(string: "https://native.example")!, token: "member-token",
                                          deviceID: "fixture-device", ticketID: ticketID)

        let requests = observed.all
        XCTAssertEqual(requests.map(\.httpMethod), ["POST", "DELETE"])
        XCTAssertEqual(requests.map { $0.url?.path }, [
            "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())/renew",
            "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())",
        ])
        XCTAssertTrue(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true })
    }

    func testRejectsInvalidInputsAndUnsafeReceiverURLs() async throws {
        let client = LuminaClient(configuration: configuration())
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            if request.url?.path == "/Lumina/Music/Receiver/tickets" {
                let ticketID = UUID()
                var payload = musicTicketPayload(ticketID: ticketID)
                payload["media_url"] = "https://livingroom.lan/api/music/receivers/\(ticketID.uuidString)/not-audio?token=receiver-capability"
                return response(request, json: payload)
            }
            let payload = ticketPayload(ticketID: UUID(), mediaURL: "http://127.0.0.1/api/chromecast/ignored/media?token=receiver-capability")
            return response(request, json: payload)
        }

        do {
            _ = try await client.prepareChromecast(origin: URL(string: "https://native.example")!, token: "member-token",
                                                   deviceID: "fixture-device", itemID: "movie-1", positionSeconds: .infinity,
                                                   audioIndex: nil, subtitle: nil)
            XCTFail("Infinite positions must never reach the server")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        XCTAssertTrue(observed.all.isEmpty)

        do {
            _ = try await client.prepareChromecast(origin: URL(string: "https://native.example")!, token: "member-token",
                                                   deviceID: "fixture-device", itemID: "movie-1", positionSeconds: 0,
                                                   audioIndex: nil, subtitle: nil)
            XCTFail("Loopback receiver URLs must be rejected")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        XCTAssertEqual(observed.all.count, 1)

        do {
            _ = try await client.prepareMusicReceiver(origin: URL(string: "https://native.example")!, token: "member-token",
                                                      deviceID: "fixture-device", itemIDs: ["music-track-1"],
                                                      currentIndex: 0, positionSeconds: 0)
            XCTFail("Music receiver URLs must be limited to the ticketed audio route")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        XCTAssertEqual(observed.all.count, 2)
    }

    func testRejectsReceiverURLsThatEchoTheConnectedAppCredential() async throws {
        let videoTicketID = UUID()
        let musicTicketID = UUID()
        let client = LuminaClient(configuration: configuration())
        ChromecastURLProtocol.handler = { request in
            switch request.url?.path {
            case "/Lumina/Cast/tickets":
                let body = try requestBody(request).jsonObject() as? [String: Any]
                let itemID = body?["item_id"] as? String
                let base = "https://livingroom.lan/api/chromecast/\(videoTicketID.uuidString.lowercased())"
                if itemID == "movie-subtitle" {
                    var payload = ticketPayload(ticketID: videoTicketID, mediaURL: "\(base)/media?token=receiver-capability")
                    payload["subtitle"] = ["url": "\(base)/subtitle.vtt?token=member-token", "label": "English"]
                    return response(request, json: payload)
                }
                return response(request, json: ticketPayload(ticketID: videoTicketID,
                                                             mediaURL: "\(base)/media?token=member-token"))
            case "/Lumina/Music/Receiver/tickets":
                var payload = musicTicketPayload(ticketID: musicTicketID)
                let body = try requestBody(request).jsonObject() as? [String: Any]
                let firstItemID = ((body?["item_ids"] as? [String]) ?? []).first
                let base = "https://livingroom.lan/api/music/receivers/\(musicTicketID.uuidString.lowercased())"
                if firstItemID == "music-queue" {
                    let currentID = "01234567-89ab-cdef-0123-456789abcdef"
                    let extraID = "fedcba98-7654-3210-fedc-ba9876543210"
                    payload["queue"] = [
                        ["item_id": currentID, "media_url": "\(base)/\(currentID)/audio?token=receiver-capability",
                         "content_type": "audio/mp4", "title": "Example track", "duration": 240.0],
                        ["item_id": extraID, "media_url": "\(base)/\(extraID)/audio?token=member-token",
                         "content_type": "audio/mp4", "title": "Queued track", "duration": 240.0],
                    ]
                } else {
                    let itemID = "01234567-89ab-cdef-0123-456789abcdef"
                    let url = "\(base)/\(itemID)/audio?token=member-token"
                    payload["media_url"] = url
                    payload["queue"] = [[
                        "item_id": itemID, "media_url": url, "content_type": "audio/mp4",
                        "title": "Example track", "duration": 240.0,
                    ]]
                }
                return response(request, json: payload)
            default:
                return response(request, status: 404, data: Data())
            }
        }

        do {
            _ = try await client.prepareChromecast(
                origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
                itemID: "movie-1", positionSeconds: 0, audioIndex: nil, subtitle: nil
            )
            XCTFail("A receiver must never receive the connected-app credential")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }

        do {
            _ = try await client.prepareChromecast(
                origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
                itemID: "movie-subtitle", positionSeconds: 0, audioIndex: nil, subtitle: nil
            )
            XCTFail("A Cast subtitle must never receive the connected-app credential")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }

        do {
            _ = try await client.prepareMusicReceiver(
                origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
                itemIDs: ["music-track-1"], currentIndex: 0, positionSeconds: 0
            )
            XCTFail("A music receiver must never receive the connected-app credential")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }

        do {
            _ = try await client.prepareMusicReceiver(
                origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
                itemIDs: ["music-queue"], currentIndex: 0, positionSeconds: 0
            )
            XCTFail("Every queued music receiver URL must be capability-only")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
    }

    func testPropagatesServerFailureAndStoreRequiresASession() async throws {
        let client = LuminaClient(configuration: configuration())
        ChromecastURLProtocol.handler = { request in response(request, status: 409, json: ["detail": "conversion_needed"]) }
        do {
            _ = try await client.prepareChromecast(origin: URL(string: "https://native.example")!, token: "member-token",
                                                   deviceID: "fixture-device", itemID: "movie-1", positionSeconds: 0,
                                                   audioIndex: nil, subtitle: nil)
            XCTFail("Server failures must remain observable")
        } catch {
            XCTAssertEqual(error as? LuminaError, .http(409))
        }

        let store = LuminaStore(client: LuminaClient(configuration: configuration()), credentials: ChromecastCredentials())
        do {
            _ = try await store.prepareChromecast(itemID: "movie-1", positionSeconds: 0)
            XCTFail("Cast minting requires a current member session")
        } catch {
            XCTAssertEqual(error as? LuminaError, .notSignedIn)
        }
    }

    func testStoreSignOutRevokesIssuedChromecastTicketBeforeLogout() async throws {
        let ticketID = UUID()
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            switch request.url?.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Lumina/Cast/tickets": return response(request, json: ticketPayload(ticketID: ticketID))
            case "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())": return response(request, status: 204, data: Data())
            case "/Sessions/Logout": return response(request, status: 204, data: Data())
            default: return response(request, status: 404, data: Data())
            }
        }
        let credentials = ChromecastCredentials(session: testSession())
        let store = LuminaStore(client: LuminaClient(configuration: configuration()), credentials: credentials)
        await store.restore()

        _ = try await store.prepareChromecast(itemID: "movie-1", positionSeconds: 12.5)
        await store.signOut()

        XCTAssertNil(store.session)
        XCTAssertNil(try credentials.load())
        let relevant = observed.all.filter { $0.url?.path.hasPrefix("/Lumina/Cast/tickets") == true || $0.url?.path == "/Sessions/Logout" }
        XCTAssertEqual(relevant.map(\.httpMethod), ["POST", "DELETE", "POST"])
        XCTAssertEqual(relevant.map { $0.url?.path }, [
            "/Lumina/Cast/tickets",
            "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())",
            "/Sessions/Logout",
        ])
    }

    func testStoreSignOutRevokesIssuedMusicReceiverTicketBeforeLogout() async throws {
        let ticketID = UUID()
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            switch request.url?.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Lumina/Music/Receiver/tickets": return response(request, json: musicTicketPayload(ticketID: ticketID))
            case "/Lumina/Music/Receiver/tickets/\(ticketID.uuidString.lowercased())":
                return response(request, status: 204, data: Data())
            case "/Sessions/Logout": return response(request, status: 204, data: Data())
            default: return response(request, status: 404, data: Data())
            }
        }
        let credentials = ChromecastCredentials(session: testSession())
        let store = LuminaStore(client: LuminaClient(configuration: configuration()), credentials: credentials)
        await store.restore()

        _ = try await store.prepareMusicReceiver(itemIDs: ["music-track-1"], currentIndex: 0, positionSeconds: 12.5)
        await store.signOut()

        let relevant = observed.all.filter {
            $0.url?.path.hasPrefix("/Lumina/Music/Receiver/tickets") == true || $0.url?.path == "/Sessions/Logout"
        }
        XCTAssertEqual(relevant.map(\.httpMethod), ["POST", "DELETE", "POST"])
        XCTAssertEqual(relevant.map { $0.url?.path }, [
            "/Lumina/Music/Receiver/tickets",
            "/Lumina/Music/Receiver/tickets/\(ticketID.uuidString.lowercased())",
            "/Sessions/Logout",
        ])
    }

    func testStaleChromecastMintAfterSignOutRevokesTicketWithoutRegisteringIt() async throws {
        let ticketID = UUID()
        let observed = ChromecastRequestRecorder()
        let mintStarted = expectation(description: "Cast mint started")
        let releaseMint = DispatchSemaphore(value: 0)
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            switch request.url?.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Lumina/Cast/tickets":
                mintStarted.fulfill()
                releaseMint.wait()
                return response(request, json: ticketPayload(ticketID: ticketID))
            case "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())": return response(request, status: 204, data: Data())
            case "/Sessions/Logout": return response(request, status: 204, data: Data())
            default: return response(request, status: 404, data: Data())
            }
        }
        let credentials = ChromecastCredentials(session: testSession())
        let store = LuminaStore(client: LuminaClient(configuration: configuration()), credentials: credentials)
        await store.restore()

        let mint = Task { () -> Error? in
            do {
                _ = try await store.prepareChromecast(itemID: "movie-1", positionSeconds: 12.5)
                return nil
            } catch {
                return error
            }
        }
        await fulfillment(of: [mintStarted], timeout: 1)

        let signOut = Task { await store.signOut() }
        await Task.yield()
        XCTAssertNil(store.session, "Cast ownership must be invalidated before sign-out awaits network I/O.")
        XCTAssertNil(try credentials.load())

        releaseMint.signal()
        let mintError = await mint.value
        XCTAssertEqual(mintError as? LuminaError, .notSignedIn)
        await signOut.value
        await store.signOut()

        let castRequests = observed.all.filter { $0.url?.path.hasPrefix("/Lumina/Cast/tickets") == true }
        XCTAssertEqual(castRequests.map(\.httpMethod), ["POST", "DELETE"])
        XCTAssertEqual(castRequests.map { $0.url?.path }, [
            "/Lumina/Cast/tickets",
            "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())",
        ])
    }

    func testMusicReceiverPreservesSelectedQueueOccurrence() async throws {
        let ticketID = UUID()
        let firstID = "01234567-89ab-cdef-0123-456789abcdef"
        let secondID = "fedcba98-7654-3210-fedc-ba9876543210"
        ChromecastURLProtocol.handler = { request in
            var payload = musicTicketPayload(ticketID: ticketID)
            var queue = try XCTUnwrap(payload["queue"] as? [[String: Any]])
            var second = queue[0]
            second["item_id"] = secondID
            second["media_url"] = (try XCTUnwrap(second["media_url"] as? String))
                .replacingOccurrences(of: firstID, with: secondID)
            queue.append(second)
            payload["queue"] = queue
            payload["media_url"] = second["media_url"]
            return response(request, json: payload)
        }
        let ticket = try await LuminaClient(configuration: configuration()).prepareMusicReceiver(
            origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
            itemIDs: [firstID, secondID], currentIndex: 1, positionSeconds: 42.5
        )
        XCTAssertEqual(ticket.queue.map(\.itemID), [firstID, secondID])
        XCTAssertEqual(ticket.ticket.mediaURL, ticket.queue[1].mediaURL)
        XCTAssertTrue(ticket.ownsMediaURL(ticket.queue[1].mediaURL))
        XCTAssertTrue(ticket.ownsMediaURL(ticket.queue[0].mediaURL))
        XCTAssertFalse(ticket.ownsMediaURL(URL(string: "https://livingroom.lan/api/music/receivers/\(UUID())/\(firstID)/audio?token=receiver-capability")))
    }

    func testMusicReceiverUsesScopedMusicRouteAndAcceptsOnlyMusicReceiverURL() async throws {
        let ticketID = UUID()
        let observed = ChromecastRequestRecorder()
        ChromecastURLProtocol.handler = { request in
            observed.append(request)
            return response(request, json: musicTicketPayload(ticketID: ticketID))
        }
        let client = LuminaClient(configuration: configuration())
        let ticket = try await client.prepareMusicReceiver(
            origin: URL(string: "https://native.example")!, token: "member-token", deviceID: "fixture-device",
            itemIDs: ["music-track-1"], currentIndex: 0, positionSeconds: 42.5
        )
        XCTAssertEqual(ticket.ticket.ticketID, ticketID)
        XCTAssertEqual(ticket.ticket.contentType, "audio/mp4")
        XCTAssertEqual(ticket.queue.count, 1)
        XCTAssertEqual(ticket.queueLimit, 100)
        let request = try XCTUnwrap(observed.last)
        XCTAssertEqual(request.url?.path, "/Lumina/Music/Receiver/tickets")
        let body = try XCTUnwrap(try requestBody(request).jsonObject() as? [String: Any])
        XCTAssertEqual(body["item_ids"] as? [String], ["music-track-1"])
        XCTAssertEqual(body["current_index"] as? Int, 0)
        XCTAssertEqual(body["position_seconds"] as? Double, 42.5)
    }
}

private final class ChromecastURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.badServerResponse) }
            let (http, data) = try handler(request)
            client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class ChromecastRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()

    func append(_ request: URLRequest) { lock.withLock { requests.append(request) } }
    var all: [URLRequest] { lock.withLock { requests } }
    var last: URLRequest? { lock.withLock { requests.last } }
}

private final class ChromecastCredentials: CredentialStore {
    var stored: LuminaSession?
    init(session: LuminaSession? = nil) { stored = session }
    func deviceID() throws -> String { "fixture-device" }
    func save(session: LuminaSession) throws {}
    func load() throws -> LuminaSession? { stored }
    func deleteSession() { stored = nil }
}

private func testSession() -> LuminaSession {
    LuminaSession(serverURL: URL(string: "https://native.example")!, accessToken: "member-token",
                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "fixture-device")
}

private func configuration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [ChromecastURLProtocol.self]
    return configuration
}

private func response(_ request: URLRequest, status: Int = 200, json: Any) -> (HTTPURLResponse, Data) {
    response(request, status: status, data: try! JSONSerialization.data(withJSONObject: json))
}

private func response(_ request: URLRequest, status: Int, data: Data) -> (HTTPURLResponse, Data) {
    let http = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
                               headerFields: ["Content-Type": "application/json"])!
    return (http, data)
}

private func ticketPayload(ticketID: UUID, mediaURL: String? = nil) -> [String: Any] {
    let base = "https://livingroom.lan/gateway/api/chromecast/\(ticketID.uuidString.lowercased())"
    return [
        "ticket_id": ticketID.uuidString,
        "media_url": mediaURL ?? "\(base)/media?token=receiver-capability",
        "content_type": "video/mp4",
        "title": "Example",
        "duration": 7_200.0,
        "position_seconds": 12.5,
        "expires_at": "2026-10-27T18:00:00Z",
        "subtitle": ["url": "\(base)/subtitle.vtt?token=receiver-capability", "label": "English", "language": "en"],
        "qualification": "unverified_hardware",
    ]
}

private func musicTicketPayload(ticketID: UUID) -> [String: Any] {
    let base = "https://livingroom.lan/gateway/api/music/receivers/\(ticketID.uuidString.lowercased())"
    let itemID = "01234567-89ab-cdef-0123-456789abcdef"
    return [
        "ticket_id": ticketID.uuidString,
        "media_url": "\(base)/\(itemID)/audio?token=receiver-capability",
        "content_type": "audio/mp4",
        "title": "Example track",
        "duration": 240.0,
        "position_seconds": 42.5,
        "expires_at": "2026-10-27T18:00:00Z",
        "subtitle": NSNull(),
        "qualification": "unverified_hardware",
        "queue_limit": 100,
        "queue": [[
            "item_id": itemID,
            "media_url": "\(base)/\(itemID)/audio?token=receiver-capability",
            "content_type": "audio/mp4",
            "title": "Example track",
            "duration": 240.0,
        ]],
    ]
}

private func requestBody(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { throw URLError(.badServerResponse) }
    stream.open()
    defer { stream.close() }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 4_096)
    while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        guard count >= 0 else { throw stream.streamError ?? URLError(.badServerResponse) }
        if count == 0 { break }
        data.append(buffer, count: count)
    }
    return data
}

private extension Data {
    func jsonObject() throws -> Any { try JSONSerialization.jsonObject(with: self) }
}
