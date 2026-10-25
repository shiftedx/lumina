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

    func testHomeRefinementUsesLoadedCategoryGenreAndWatchlistFactsWithoutMutatingRows() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HomeLayoutProtocol.self]
        let payload: [String: Any] = ["rows": [
            [
                "id": "watchlist", "shelf_id": "watchlist", "title": "Your watchlist", "shape": "still",
                "items": [["Id": "episode-1", "Name": "Episode", "Type": "Episode", "SeriesId": "show-1", "LuminaCategory": "shows", "Genres": ["Drama"]]],
                "external_items": [["id": "saved-source", "title": "Saved source", "subtitle": "", "url": "https://example.com/watch"]],
            ],
            [
                "id": "recommended", "shelf_id": "recommended", "title": "Recommended", "shape": "poster",
                "items": [
                    ["Id": "movie-1", "Name": "Movie", "Type": "Movie", "LuminaCategory": "movies", "Genres": ["drama"]],
                    ["Id": "show-1", "Name": "Show", "Type": "Series", "LuminaCategory": "shows", "Genres": ["Drama"]],
                    ["Id": "anime-1", "Name": "Anime", "Type": "Series", "LuminaCategory": "anime", "Genres": ["Fantasy"]],
                ],
            ],
        ]]
        let data = try JSONSerialization.data(withJSONObject: payload)
        HomeLayoutProtocol.handler = { @Sendable request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, data)
        }
        let feed = LuminaHomeFeed(
            session: LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member", username: "Member", serverName: "Lumina", deviceID: "home-refinement-device"),
            client: LuminaClient(configuration: configuration)
        )
        await feed.load()
        let heroes = try JSONDecoder().decode([MediaItem].self, from: JSONSerialization.data(withJSONObject: [
            ["Id": "show-1", "Name": "Show", "Type": "Series"],
            ["Id": "movie-1", "Name": "Movie", "Type": "Movie"],
            ["Id": "anime-1", "Name": "Anime", "Type": "Series"],
        ]))

        let watchlist = feed.refined(HomeRefinement(category: .watchlist), heroItems: [heroes[1]])
        XCTAssertEqual(watchlist.rows.map(\.id), ["watchlist", "recommended"])
        XCTAssertEqual(watchlist.rows[1].items.map(\.id), ["show-1"])
        XCTAssertEqual(watchlist.items.map(\.id), ["episode-1"], "Watchlist heroes must preserve saved entries without duplicating their related series cards.")
        XCTAssertEqual(watchlist.rows[0].externalItems?.map(\.id), ["saved-source"])

        let movies = feed.refined(HomeRefinement(category: .movies, genre: "Drama"), heroItems: heroes)
        XCTAssertEqual(movies.rows.map(\.id), ["recommended"])
        XCTAssertEqual(movies.rows[0].items.map(\.id), ["movie-1"])
        XCTAssertEqual(movies.items.map(\.id), ["movie-1"])
        XCTAssertNil(movies.rows[0].externalItems)

        let anime = feed.refined(HomeRefinement(category: .anime), heroItems: heroes)
        XCTAssertEqual(anime.rows.map(\.id), ["recommended"])
        XCTAssertEqual(anime.rows[0].items.map(\.id), ["anime-1"])
        XCTAssertEqual(anime.items.map(\.id), ["anime-1"])
        XCTAssertEqual(feed.availableGenres, ["Drama", "Fantasy"])
        XCTAssertEqual(feed.rows.flatMap(\.items).map(\.id), ["episode-1", "movie-1", "show-1", "anime-1"])
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
        XCTAssertEqual(Array(HomeShelfID.allCases.prefix(3)), [.continueWatching, .nextUp, .live])
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

    func testHomeFeedRetainsLoadedRowsDuringRefreshAndClearsForAnotherMember() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HomeLayoutProtocol.self]
        let replies = HomeFeedRefreshReplies()
        HomeLayoutProtocol.handler = { request in
            let response = replies.response(for: request)
            return response
        }
        let session = LuminaSession(
            serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member-one",
            username: "Member one", serverName: "Lumina", deviceID: "home-refresh-device"
        )
        let feed = LuminaHomeFeed(session: session, client: LuminaClient(configuration: configuration), includesExternalSources: true)

        await feed.load()
        let displayedRows = feed.rows
        XCTAssertTrue(feed.hasLoaded)
        XCTAssertEqual(displayedRows.map(\.id), ["continue"])

        await feed.load()
        XCTAssertTrue(feed.hasLoaded)
        XCTAssertEqual(feed.rows.map(\.id), displayedRows.map(\.id))
        XCTAssertEqual(feed.errorMessage, "Home could not be loaded.")

        feed.configure(session: LuminaSession(
            serverURL: session.serverURL, accessToken: session.accessToken, userID: "member-two",
            username: "Member two", serverName: session.serverName, deviceID: session.deviceID
        ))
        XCTAssertFalse(feed.hasLoaded)
        XCTAssertTrue(feed.rows.isEmpty)
        XCTAssertNil(feed.errorMessage)
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

private final class HomeFeedRefreshReplies: @unchecked Sendable {
    private let lock = NSLock()
    private var attempts = 0

    func response(for request: URLRequest) -> (HTTPURLResponse, Data) {
        let attempt = lock.withLock {
            attempts += 1
            return attempts
        }
        let status = attempt == 1 ? 200 : 503
        let payload: [String: Any] = status == 200
            ? ["rows": [[
                "id": "continue", "shelf_id": "continue", "title": "Continue watching",
                "shape": "poster", "items": []
            ]]]
            : ["detail": "temporarily_unavailable"]
        let response = HTTPURLResponse(
            url: request.url!, statusCode: status, httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
        )!
        return (response, try! JSONSerialization.data(withJSONObject: payload))
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
