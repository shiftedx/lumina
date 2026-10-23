import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaShowcaseTests: XCTestCase {
    override func setUp() {
        super.setUp()
        ShowcaseURLProtocol.handler = nil
    }

    func testPublicShowcaseUsesAnAnonymousBoundedRequestAndAcceptsOnlySignedRootRelativeArtwork() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ShowcaseURLProtocol.self]
        let recorder = ShowcaseRequestRecorder()
        ShowcaseURLProtocol.handler = { @Sendable request in
            recorder.record(request)
            return showcaseResponse(request, json: ["slides": showcaseSlides()])
        }

        let store = LuminaStore(client: LuminaClient(configuration: configuration), credentials: ShowcaseCredentials())
        let slides = try await store.publicShowcase(server: "https://lumina.example")

        XCTAssertEqual(slides.count, 12, "The public response is bounded after invalid and duplicate artwork is removed.")
        XCTAssertEqual(slides.first?.title, "Welcome 0")
        XCTAssertEqual(slides.first?.kind, "movie")
        XCTAssertEqual(slides.first?.section, "upcoming")
        XCTAssertEqual(slides.first?.backdropURL.absoluteString, "https://lumina.example/api/public/showcase/art/AAAAAAAAAAAAAAAAAAAAA0")
        XCTAssertEqual(slides.first?.posterURL?.absoluteString, "https://lumina.example/api/public/showcase/art/BBBBBBBBBBBBBBBBBBBBB0")
        XCTAssertEqual(slides[1].posterURL, nil, "Unsafe optional poster artwork is dropped without dropping its valid slide.")
        XCTAssertEqual(Set(slides.map(\.backdropURL)).count, slides.count)
        XCTAssertFalse(slides.contains { ["Duplicate", "Cross origin", "Malformed token", "Query artwork", "Fragment artwork"].contains($0.title) })

        let request = try XCTUnwrap(recorder.request)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/api/public/showcase")
        XCTAssertNil(request.url?.query)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Emby-Authorization"))
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertLessThanOrEqual(request.timeoutInterval, 10)
    }

    func testPublicShowcaseOriginRejectsPathsCredentialsAndFragments() {
        XCTAssertNoThrow(try ServerOrigin("https://lumina.example"))
        for invalid in [
            "https://lumina.example/lumina",
            "https://member@lumina.example",
            "https://lumina.example?member=hidden",
            "https://lumina.example#private",
        ] {
            XCTAssertThrowsError(try ServerOrigin(invalid))
        }
    }
}

private final class ShowcaseURLProtocol: URLProtocol {
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

    override func stopLoading() { }
}

private final class ShowcaseCredentials: CredentialStore {
    func deviceID() throws -> String { "unused-public-showcase-device" }
    func save(session: LuminaSession) throws { }
    func load() throws -> LuminaSession? { nil }
    func deleteSession() { }
}

private final class ShowcaseRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedRequest: URLRequest?

    var request: URLRequest? { lock.withLock { storedRequest } }
    func record(_ request: URLRequest) { lock.withLock { storedRequest = request } }
}

private func showcaseSlides() -> [[String: Any]] {
    let prefix = "/api/public/showcase/art/"
    var slides: [[String: Any]] = [
        [
            "title": "Welcome 0", "kind": "movie", "section": "upcoming",
            "backdrop_url": prefix + showcaseToken("A", 0),
            "poster_url": prefix + showcaseToken("B", 0),
        ],
        [
            "title": "Unsafe poster", "kind": "show", "section": "on_the_air",
            "backdrop_url": prefix + showcaseToken("A", 1),
            "poster_url": "https://elsewhere.example/api/public/showcase/art/" + showcaseToken("B", 1),
        ],
        [
            "title": "Duplicate", "kind": "movie",
            "backdrop_url": prefix + showcaseToken("A", 0),
        ],
        [
            "title": "Cross origin", "kind": "anime",
            "backdrop_url": "https://elsewhere.example/api/public/showcase/art/" + showcaseToken("A", 2),
        ],
        [
            "title": "Malformed token", "kind": "anime",
            "backdrop_url": prefix + "too-short",
        ],
        [
            "title": "Query artwork", "kind": "movie",
            "backdrop_url": prefix + showcaseToken("A", 2) + "?household=private",
        ],
        [
            "title": "Fragment artwork", "kind": "movie",
            "backdrop_url": prefix + showcaseToken("A", 3) + "#private",
        ],
    ]
    for index in 2...13 {
        slides.append([
            "title": "Welcome \(index)", "kind": "movie", "section": "now_playing",
            "backdrop_url": prefix + showcaseToken("A", index),
        ])
    }
    return slides
}

private func showcaseToken(_ character: Character, _ index: Int) -> String {
    let suffixes = Array("0123456789abcdefghijklmn")
    return String(repeating: String(character), count: 21) + String(suffixes[index])
}

private func showcaseResponse(_ request: URLRequest, json object: Any) -> (HTTPURLResponse, Data) {
    let data = try! JSONSerialization.data(withJSONObject: object)
    let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
    return (response, data)
}
