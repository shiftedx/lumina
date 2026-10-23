import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaWatchlistTests: XCTestCase {
    override func setUp() {
        super.setUp()
        WatchlistProtocol.handler = nil
    }

    func testLoadAndSetUseTheConnectedAppWatchlistContract() async throws {
        let recorder = WatchlistRequestRecorder()
        let watchlist = library()
        WatchlistProtocol.handler = { request in
            recorder.record(request)
            switch (request.httpMethod, request.url?.path) {
            case ("GET", "/Lumina/Watchlist/0123456789abcdef"):
                return response(request, json: ["included": false])
            case ("PUT", "/Lumina/Watchlist/0123456789abcdef"):
                return response(request, json: ["included": true])
            default:
                return response(request, status: 404, json: ["detail": "not_found"])
            }
        }

        await watchlist.load(itemID: "0123456789abcdef")
        XCTAssertFalse(watchlist.isIncluded)
        XCTAssertFalse(watchlist.contains(itemID: "0123456789abcdef"))
        XCTAssertNil(watchlist.errorMessage)

        let saved = await watchlist.set(itemID: "0123456789abcdef", included: true)
        XCTAssertTrue(saved)
        XCTAssertTrue(watchlist.isIncluded)
        XCTAssertTrue(watchlist.contains(itemID: "0123456789abcdef"))
        XCTAssertFalse(watchlist.isSaving)

        let requests = recorder.requests
        XCTAssertEqual(requests.map { $0.httpMethod }, ["GET", "PUT"])
        XCTAssertEqual(requests.map { $0.url?.path }, ["/Lumina/Watchlist/0123456789abcdef", "/Lumina/Watchlist/0123456789abcdef"])
        XCTAssertTrue(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"member-token\"") == true })
        XCTAssertEqual(try body(requests[1]), ["included": true])
    }

    func testFailureRetainsKnownMembershipAndRejectsUnsafeIDs() async throws {
        let watchlist = library()
        var calls = 0
        WatchlistProtocol.handler = { request in
            calls += 1
            return calls == 1 ? response(request, json: ["included": true]) : response(request, status: 422, json: ["detail": "watchlist_full"])
        }

        await watchlist.load(itemID: "item-one")
        XCTAssertTrue(watchlist.isIncluded)
        let saved = await watchlist.set(itemID: "item-one", included: false)
        XCTAssertFalse(saved)
        XCTAssertTrue(watchlist.isIncluded)
        XCTAssertEqual(watchlist.errorMessage, WatchlistError.server(422).localizedDescription)

        await watchlist.load(itemID: "../unsafe")
        XCTAssertEqual(watchlist.errorMessage, WatchlistError.invalidItemID.localizedDescription)
    }

    func testChangingSessionClearsAFormerMembersWatchlistState() async throws {
        let watchlist = library()
        WatchlistProtocol.handler = { request in response(request, json: ["included": true]) }
        await watchlist.load(itemID: "item-one")
        XCTAssertTrue(watchlist.isIncluded)

        watchlist.configure(session: LuminaSession(
            serverURL: URL(string: "https://lumina.example")!, accessToken: "other-token", userID: "other",
            username: "Other", serverName: "Lumina", deviceID: "watchlist-device"
        ))
        XCTAssertFalse(watchlist.isIncluded)
        XCTAssertFalse(watchlist.contains(itemID: "item-one"))
        XCTAssertNil(watchlist.errorMessage)
    }

    private func library() -> LuminaWatchlist {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WatchlistProtocol.self]
        return LuminaWatchlist(
            session: LuminaSession(
                serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
                username: "Member", serverName: "Lumina", deviceID: "watchlist-device"
            ),
            client: LuminaClient(configuration: configuration)
        )
    }
}

private final class WatchlistProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.badServerResponse) }
            let (reply, data) = try handler(request)
            client?.urlProtocol(self, didReceive: reply, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class WatchlistRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = [URLRequest]()

    func record(_ request: URLRequest) { lock.withLock { stored.append(request) } }
    var requests: [URLRequest] { lock.withLock { stored } }
}

private func response(_ request: URLRequest, status: Int = 200, json: Any) -> (HTTPURLResponse, Data) {
    let data = try! JSONSerialization.data(withJSONObject: json)
    return (
        HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!,
        data
    )
}

private func body(_ request: URLRequest) throws -> [String: Bool] {
    let data: Data
    if let direct = request.httpBody {
        data = direct
    } else {
        let stream = try XCTUnwrap(request.httpBodyStream)
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
    }
    return try JSONDecoder().decode([String: Bool].self, from: data)
}
