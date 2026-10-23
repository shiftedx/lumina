import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaRequestsTests: XCTestCase {
    override func setUp() {
        super.setUp()
        RequestsProtocol.handler = nil
    }

    func testCatalogQuotaAndRequestUseConnectedAppAuthentication() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RequestsProtocol.self]
        let session = LuminaSession(
            serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
            username: "Member", serverName: "Lumina", deviceID: "request-device"
        )
        let requests = LuminaRequests(session: session, client: LuminaClient(configuration: configuration))
        let createBody = RequestsBodyRecorder()
        let observedRequests = RequestsRequestRecorder()
        do {
            let data = try JSONSerialization.data(withJSONObject: requestRecord())
            _ = try JSONDecoder().decode(RequestRecord.self, from: data)
        } catch {
            XCTFail("Request fixture cannot decode: \(error)")
            return
        }
        RequestsProtocol.handler = { request in
            observedRequests.record(request)
            switch request.url?.path {
            case "/Lumina/Requests/catalog/home":
                return requestsResponse(request, json: ["hero": [catalogItem()], "rails": []])
            case "/Lumina/Requests/catalog/framing":
                return requestsResponse(request, json: ["state": "ready", "width": 1920, "height": 1080,
                    "regions": [["kind": "face", "x": 0.8, "y": 0.1, "width": 0.1, "height": 0.2, "weight": 0.9]]])
            case "/Lumina/Requests/catalog/search":
                return requestsResponse(request, json: ["items": [catalogItem()], "page": 3, "total_pages": 8])
            case "/Lumina/Requests/catalog/list/anime":
                return requestsResponse(request, json: ["items": [catalogItem()], "page": 2, "total_pages": 4])
            case "/Lumina/Requests/catalog/genres/movie":
                return requestsResponse(request, json: ["genres": [["id": "28", "name": "Action"], ["id": "878", "name": "Science Fiction"]]])
            case "/Lumina/Requests/quota":
                return requestsResponse(request, json: ["quotas": [["kind": "movie", "can_request": true, "auto_approve": true, "limit": 4, "days": 7, "used": 1, "remaining": 3]]])
            case "/Lumina/Requests":
                createBody.body = try requestsBody(request)
                return requestsResponse(request, status: 201, json: requestRecord())
            default:
                return requestsResponse(request, status: 404, json: ["detail": "not_found"])
            }
        }

        await requests.loadHome()
        let item = try XCTUnwrap(requests.home?.hero.first)
        XCTAssertEqual(item.catalogID, 42)
        XCTAssertEqual(item.status.state, .none)
        XCTAssertNil(requests.error)

        let source = URL(string: "https://image.tmdb.org/t/p/w1280/example.jpg")!
        let framing = await requests.artworkFraming(for: source)
        XCTAssertEqual(framing?.state, "ready")
        let lookup = try XCTUnwrap(observedRequests.request(path: "/Lumina/Requests/catalog/framing"))
        XCTAssertEqual(lookup.timeoutInterval, 0.15)
        XCTAssertEqual(observedRequests.query(path: lookup.url!.path, named: "src"), source.absoluteString)
        let crop = HeroFraming.crop(framing, source: CGSize(width: 1920, height: 1080), viewport: CGSize(width: 390, height: 500))
        XCTAssertLessThanOrEqual(crop.minX, 0.79)
        XCTAssertGreaterThanOrEqual(crop.maxX, 0.91)

        let search = try await requests.search("Example", page: 3)
        XCTAssertEqual(search.page, 3)
        XCTAssertEqual(search.totalPages, 8)
        XCTAssertEqual(search.items.first?.id, "movie:42")
        let anime = try await requests.list(.anime, section: "this_season", genre: "Fantasy", page: 2)
        XCTAssertEqual(anime.page, 2)
        XCTAssertEqual(anime.totalPages, 4)
        XCTAssertEqual(anime.items.first?.id, "movie:42")
        let genres = try await requests.genres(.movie)
        XCTAssertEqual(genres, [
            RequestCatalogGenre(id: "28", name: "Action"),
            RequestCatalogGenre(id: "878", name: "Science Fiction"),
        ])
        XCTAssertEqual(observedRequests.query(path: "/Lumina/Requests/catalog/search", named: "q"), "Example")
        XCTAssertEqual(observedRequests.query(path: "/Lumina/Requests/catalog/search", named: "page"), "3")
        XCTAssertEqual(observedRequests.query(path: "/Lumina/Requests/catalog/list/anime", named: "section"), "this_season")
        XCTAssertEqual(observedRequests.query(path: "/Lumina/Requests/catalog/list/anime", named: "genre"), "Fantasy")
        XCTAssertEqual(observedRequests.query(path: "/Lumina/Requests/catalog/list/anime", named: "page"), "2")

        await requests.loadQuotas()
        XCTAssertEqual(requests.quotas.first?.remaining, 3)

        let created: RequestRecord
        do {
            created = try await requests.create(item)
        } catch {
            XCTFail("Creation failed: \(error)")
            return
        }
        XCTAssertEqual(created.status, .approved)
        XCTAssertEqual(createBody.body?["tmdb_id"] as? Int, 42)
        XCTAssertNil(createBody.body?["media_type"])
        for path in [
            "/Lumina/Requests/catalog/home",
            "/Lumina/Requests/catalog/framing",
            "/Lumina/Requests/catalog/search",
            "/Lumina/Requests/catalog/list/anime",
            "/Lumina/Requests/catalog/genres/movie",
            "/Lumina/Requests/quota",
            "/Lumina/Requests",
        ] {
            XCTAssertTrue(observedRequests.request(path: path)?.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true)
        }

    }

    func testRequestErrorRetainsStructuredServerCode() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RequestsProtocol.self]
        let session = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member", username: "Member", serverName: "Lumina", deviceID: "request-device")
        let requests = LuminaRequests(session: session, client: LuminaClient(configuration: configuration))
        RequestsProtocol.handler = { request in
            requestsResponse(request, status: 429, json: ["detail": "quota_exceeded"])
        }

        await requests.loadHome()
        XCTAssertEqual(requests.error, .server(status: 429, code: "quota_exceeded"))
    }

    func testTrailerMetadataBuildsOnlyValidatedYouTubeWatchURLs() throws {
        let trailer = RequestTrailer(youtubeID: "official111", name: "Trailer", type: "Trailer", official: true)
        let url = try XCTUnwrap(trailer.youtubeURL)
        let parts = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(parts.scheme, "https")
        XCTAssertEqual(parts.host, "www.youtube.com")
        XCTAssertEqual(parts.path, "/watch")
        XCTAssertEqual(parts.queryItems, [URLQueryItem(name: "v", value: "official111")])
        XCTAssertNil(parts.user)
        XCTAssertNil(parts.password)
        XCTAssertNil(parts.fragment)

        for malformedID in ["short", "contains/slash", "contains?query", String(repeating: "a", count: 21)] {
            let data = try JSONSerialization.data(withJSONObject: [
                "youtube_id": malformedID, "name": "Trailer", "type": "Trailer", "official": true,
            ])
            let malformed = try JSONDecoder().decode(RequestTrailer.self, from: data)
            XCTAssertNil(malformed.youtubeURL, "Malformed catalog metadata must not produce a provider URL: \(malformedID)")
        }
    }

}

private final class RequestsProtocol: URLProtocol {
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

private final class RequestsBodyRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [String: Any]?

    var body: [String: Any]? {
        get { lock.withLock { stored } }
        set { lock.withLock { stored = newValue } }
    }
}

private final class RequestsRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()

    func record(_ request: URLRequest) {
        lock.withLock { requests.append(request) }
    }

    func request(path: String) -> URLRequest? {
        lock.withLock { requests.last(where: { $0.url?.path == path }) }
    }

    func query(path: String, named name: String) -> String? {
        guard let url = request(path: path)?.url else { return nil }
        return URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == name })?.value
    }
}

private func catalogItem() -> [String: Any] {
    [
        "key": "movie:42", "kind": "movie", "media_type": "movie", "tmdb_id": 42, "title": "The Example", "year": 2026,
        "genres": ["Drama"], "status": ["state": "none"], "requestable": true,
    ]
}

private func requestRecord() -> [String: Any] {
    [
        "id": "request-1", "kind": "movie", "media_type": "movie", "key": "movie:42", "tmdb_id": 42, "title": "The Example",
        "seasons": NSNull(), "language": NSNull(), "status": "approved", "requested_by": ["id": "member", "name": "Member"],
        "followers": [], "created_at": "2026-10-21T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z",
    ]
}

private func requestsResponse(_ request: URLRequest, status: Int = 200, json object: Any) -> (HTTPURLResponse, Data) {
    let data = try! JSONSerialization.data(withJSONObject: object)
    let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
    return (response, data)
}

private func requestsBody(_ request: URLRequest) throws -> [String: Any] {
    let data: Data
    if let body = request.httpBody {
        data = body
    } else if let stream = request.httpBodyStream {
        stream.open()
        defer { stream.close() }
        var result = Data()
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4_096)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let count = stream.read(buffer, maxLength: 4_096)
            guard count >= 0 else { throw stream.streamError ?? URLError(.badServerResponse) }
            if count == 0 { break }
            result.append(buffer, count: count)
        }
        data = result
    } else {
        throw URLError(.badServerResponse)
    }
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}
