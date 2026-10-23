import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaHomeLayoutTests: XCTestCase {
    override func setUp() {
        super.setUp()
        HomeLayoutProtocol.handler = nil
        HomeLayoutProtocol.responseDelay = 0
    }

    func testHomeFeedLoadsAllShelfKindsAndExplicitSourceHandoffs() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HomeLayoutProtocol.self]
        let rows = HomeShelfID.allCases.map { id -> [String: Any] in
            ["id": id.rawValue, "shelf_id": id.rawValue, "title": id.title, "shape": "still", "items": [],
             "external_items": [["id": "source", "title": "Example video", "subtitle": "Example channel",
                                 "url": "https://www.youtube.com/watch?v=example", "artwork_url": "https://i.ytimg.com/vi/example/default.jpg"]]]
        }
        let payload = try JSONSerialization.data(withJSONObject: ["rows": rows])
        HomeLayoutProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/Lumina/Home/rows")
            XCTAssertEqual(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems,
                           [URLQueryItem(name: "include_external", value: "true")])
            XCTAssertNotNil(request.value(forHTTPHeaderField: "Authorization"))
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, payload)
        }
        let feed = LuminaHomeFeed(session: LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member", username: "Member", serverName: "Lumina", deviceID: "home-feed-device"), client: LuminaClient(configuration: configuration), includesExternalSources: true)
        await feed.load()
        XCTAssertNil(feed.errorMessage)
        XCTAssertTrue(feed.hasLoaded)
        XCTAssertEqual(feed.rows.map(\.shelfID), HomeShelfID.allCases)
        XCTAssertTrue(feed.rows.allSatisfy(\.hasItems))
        XCTAssertEqual(feed.rows.first?.externalItems?.first?.sourceURL?.host, "www.youtube.com")
    }

    func testAuthenticatedLoadSaveResetAndFailuresRetainTheCurrentLayout() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HomeLayoutProtocol.self]
        let layout = LuminaHomeLayout(
            session: LuminaSession(
                serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
                username: "Member", serverName: "Lumina", deviceID: "home-layout-device"
            ),
            client: LuminaClient(configuration: configuration)
        )
        let recorder = HomeLayoutRequestRecorder()
        let replies = HomeLayoutReplies()
        HomeLayoutProtocol.handler = { request in
            let body = recorder.record(request)
            return replies.response(for: request, body: body)
        }

        XCTAssertEqual(HomeShelfID.allCases.count, 14)
        XCTAssertEqual(HomeShelfID.becauseYouWatched.title, "Because you watched…")
        XCTAssertFalse(layout.hasLoaded)
        XCTAssertTrue(layout.shelves.allSatisfy(\.visible))

        await layout.load()
        XCTAssertTrue(layout.hasLoaded)
        XCTAssertNil(layout.errorMessage)
        XCTAssertEqual(Array(layout.shelves.prefix(2)), [
            HomeShelfPreference(id: .newAnime, visible: false),
            HomeShelfPreference(id: .continueWatching, visible: true),
        ])
        XCTAssertEqual(layout.shelves.count, HomeShelfID.allCases.count)

        let custom = [
            HomeShelfPreference(id: .watchlist, visible: false),
            HomeShelfPreference(id: .recentMusic, visible: true),
        ]
        let didSaveCustom = await layout.save(custom)
        XCTAssertTrue(didSaveCustom)
        XCTAssertEqual(Array(layout.shelves.prefix(2)), custom)
        XCTAssertEqual(layout.shelves.count, HomeShelfID.allCases.count)
        XCTAssertTrue(layout.hasLoaded)

        let didReset = await layout.save(nil)
        XCTAssertTrue(didReset)
        XCTAssertEqual(layout.shelves.map(\.id), HomeShelfID.allCases)
        XCTAssertTrue(layout.shelves.allSatisfy(\.visible))

        let beforeFailure = layout.shelves
        await layout.load()
        XCTAssertEqual(layout.shelves, beforeFailure)
        XCTAssertTrue(layout.hasLoaded)
        XCTAssertNotNil(layout.errorMessage)

        let didSaveAfterFailure = await layout.save(custom)
        XCTAssertFalse(didSaveAfterFailure)
        XCTAssertEqual(layout.shelves, beforeFailure)
        XCTAssertNotNil(layout.errorMessage)

        let requests = recorder.requests
        XCTAssertEqual(requests.map { $0.request.httpMethod }, ["GET", "PUT", "PUT", "GET", "PUT"])
        for recorded in requests {
            XCTAssertEqual(recorded.request.url?.path, "/Lumina/Home/layout")
            XCTAssertTrue(recorded.request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true)
            XCTAssertFalse(recorded.request.url?.query?.lowercased().contains("token") == true)
        }
        XCTAssertEqual(try shelves(in: requests[1].body), custom)
        XCTAssertNil(try shelves(in: requests[2].body))
        let resetBody = try XCTUnwrap(requests[2].body)
        let resetObject = try XCTUnwrap(try JSONSerialization.jsonObject(with: resetBody) as? [String: Any])
        XCTAssertTrue(resetObject["shelves"] is NSNull)
    }

    func testOverlappingHomeLoadsCoalesceToOneRequestPerEndpoint() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HomeLayoutProtocol.self]
        let recorder = HomeLayoutRequestRecorder()
        HomeLayoutProtocol.responseDelay = 0.05
        HomeLayoutProtocol.handler = { request in
            _ = recorder.record(request)
            let payload: [String: Any]
            switch request.url?.path {
            case "/Lumina/Home/layout":
                payload = ["shelves": []]
            case "/Lumina/Home/rows":
                payload = ["rows": []]
            default:
                payload = ["detail": "not_found"]
            }
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: request.url?.path == "/Lumina/Home/layout" || request.url?.path == "/Lumina/Home/rows" ? 200 : 404,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!
            return (response, try JSONSerialization.data(withJSONObject: payload))
        }
        let session = LuminaSession(
            serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
            username: "Member", serverName: "Lumina", deviceID: "home-overlap-device"
        )
        let layout = LuminaHomeLayout(session: session, client: LuminaClient(configuration: configuration))
        let feed = LuminaHomeFeed(session: session, client: LuminaClient(configuration: configuration), includesExternalSources: true)

        async let firstLayout: Void = layout.load()
        async let secondLayout: Void = layout.load()
        async let firstFeed: Void = feed.load()
        async let secondFeed: Void = feed.load()
        _ = await (firstLayout, secondLayout, firstFeed, secondFeed)

        let paths = recorder.requests.compactMap { $0.request.url?.path }
        XCTAssertEqual(paths.filter { $0 == "/Lumina/Home/layout" }.count, 1)
        XCTAssertEqual(paths.filter { $0 == "/Lumina/Home/rows" }.count, 1)
        XCTAssertTrue(layout.hasLoaded)
        XCTAssertTrue(feed.hasLoaded)
    }
}

private final class HomeLayoutProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var responseDelay: TimeInterval = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let complete = { [weak self] in
            guard let self else { return }
            do {
                guard let handler = Self.handler else { throw URLError(.badServerResponse) }
                let (response, data) = try handler(self.request)
                self.client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
                self.client?.urlProtocol(self, didLoad: data)
                self.client?.urlProtocolDidFinishLoading(self)
            } catch {
                self.client?.urlProtocol(self, didFailWithError: error)
            }
        }
        if Self.responseDelay > 0 {
            DispatchQueue.global().asyncAfter(deadline: .now() + Self.responseDelay, execute: complete)
        } else {
            complete()
        }
    }

    override func stopLoading() {}
}

private final class HomeLayoutRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = [HomeLayoutRecordedRequest]()

    func record(_ request: URLRequest) -> Data? {
        let body = try? homeLayoutBody(request)
        lock.withLock { stored.append(HomeLayoutRecordedRequest(request: request, body: body)) }
        return body
    }

    var requests: [HomeLayoutRecordedRequest] { lock.withLock { stored } }
}

private struct HomeLayoutRecordedRequest {
    let request: URLRequest
    let body: Data?
}

private final class HomeLayoutReplies: @unchecked Sendable {
    private let lock = NSLock()
    private var getCount = 0
    private var putCount = 0

    func response(for request: URLRequest, body: Data?) -> (HTTPURLResponse, Data) {
        let status: Int
        var object: [String: Any]
        lock.lock()
        defer { lock.unlock() }
        switch request.httpMethod {
        case "GET":
            getCount += 1
            if getCount == 1 {
                status = 200
                object = ["shelves": [["id": "new_anime", "visible": false], ["id": "continue", "visible": true]]]
            } else {
                status = 503
                object = ["detail": "temporarily_unavailable"]
            }
        case "PUT":
            putCount += 1
            status = putCount <= 2 ? 200 : 500
            if let body,
               let payload = try? JSONSerialization.jsonObject(with: body),
               let requestLayout = payload as? [String: Any] {
                object = requestLayout
            } else {
                object = ["shelves": NSNull()]
            }
        default:
            status = 405
            object = ["detail": "method_not_allowed"]
        }
        let data = try! JSONSerialization.data(withJSONObject: object)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        return (response, data)
    }
}

private func shelves(in data: Data?) throws -> [HomeShelfPreference]? {
    let data = try XCTUnwrap(data)
    return try JSONDecoder().decode(HomeLayoutPayload.self, from: data).shelves
}

private func homeLayoutBody(_ request: URLRequest) throws -> Data? {
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

private struct HomeLayoutPayload: Decodable {
    let shelves: [HomeShelfPreference]?
}
