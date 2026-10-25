import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import LuminaCore

final class BookCoverCacheTests: XCTestCase {
    func testCoalescedConsumersKeepTheCoverAliveWhenOneIsCancelled() async throws {
        let fixture = BookCoverURLProtocolFixture(
            responses: [.init(statusCode: 200, data: try coverData(width: 1_200, height: 1_800))],
            gatedRequests: [1]
        )
        BookCoverURLProtocol.fixture = fixture
        defer { BookCoverURLProtocol.fixture = nil }
        let cache = makeCache()

        let first = Task { await cache.image(bookCoverID) }
        let second = Task { await cache.image(bookCoverID) }
        let didStart = await fixture.waitForRequest(atLeast: 1)
        XCTAssertTrue(didStart)
        try await Task.sleep(for: .milliseconds(20))

        first.cancel()
        let firstFinishedBeforeRelease = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                _ = await first.value
                return true
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(1))
                return false
            }
            let finished = await group.next() ?? false
            group.cancelAll()
            if !finished { fixture.release(1) }
            return finished
        }
        XCTAssertTrue(firstFinishedBeforeRelease)
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertFalse(fixture.wasStopped(1), "Cancelling one visible consumer must not stop the shared download")
        fixture.release(1)

        let firstImage = await first.value
        let secondImage = await second.value
        XCTAssertNil(firstImage)
        XCTAssertEqual(secondImage?.width, 400)
        XCTAssertEqual(secondImage?.height, 600)
        XCTAssertEqual(fixture.requestCount, 1)
    }

    func testCancellingTheFinalConsumerRemovesWorkAndAllowsAFreshRetry() async throws {
        let fixture = BookCoverURLProtocolFixture(responses: [
            .init(statusCode: 202, data: Data()),
            .init(statusCode: 200, data: try coverData(width: 1_600, height: 2_400)),
        ])
        BookCoverURLProtocol.fixture = fixture
        defer { BookCoverURLProtocol.fixture = nil }
        let cache = makeCache()

        let cancelled = Task { await cache.image(bookCoverID) }
        let didStart = await fixture.waitForRequest(atLeast: 1)
        XCTAssertTrue(didStart)
        let didRespond = await fixture.waitForResponse(atLeast: 1)
        XCTAssertTrue(didRespond)
        cancelled.cancel()
        let cancelledImage = await cancelled.value
        XCTAssertNil(cancelledImage)
        XCTAssertEqual(fixture.requestCount, 1)

        let fresh = await cache.image(bookCoverID)
        XCTAssertEqual(fresh?.width, 400)
        XCTAssertEqual(fresh?.height, 600)
        XCTAssertEqual(fixture.requestCount, 2, "A cancelled retry must not be reused as a completed cover")
    }

    func testLateCompletionFromCancelledWorkCannotReplaceAFreshRequest() async throws {
        let fixture = BookCoverURLProtocolFixture(
            responses: [
                .init(statusCode: 200, data: try coverData(width: 1_200, height: 1_200)),
                .init(statusCode: 200, data: try coverData(width: 1_200, height: 1_800)),
            ],
            gatedRequests: [1],
            lateRequests: [1]
        )
        BookCoverURLProtocol.fixture = fixture
        defer { BookCoverURLProtocol.fixture = nil }
        let cache = makeCache()

        let cancelled = Task { await cache.image(bookCoverID) }
        let didStart = await fixture.waitForRequest(atLeast: 1)
        XCTAssertTrue(didStart)
        cancelled.cancel()
        let didStop = await fixture.waitForStop(1)
        XCTAssertTrue(didStop)

        let fresh = await cache.image(bookCoverID)
        XCTAssertEqual(fresh?.width, 400)
        XCTAssertEqual(fresh?.height, 600)
        fixture.release(1)
        let cancelledImage = await cancelled.value
        XCTAssertNil(cancelledImage)

        let cached = await cache.image(bookCoverID)
        XCTAssertEqual(cached?.width, 400)
        XCTAssertEqual(cached?.height, 600)
        XCTAssertEqual(fixture.requestCount, 2)
    }

    func testCacheReusePreservesTheSixHundredPixelThumbnail() async throws {
        let fixture = BookCoverURLProtocolFixture(
            responses: [.init(statusCode: 200, data: try coverData(width: 1_200, height: 1_800))]
        )
        BookCoverURLProtocol.fixture = fixture
        defer { BookCoverURLProtocol.fixture = nil }
        let cache = makeCache()

        let first = await cache.image(bookCoverID)
        let cached = await cache.image(bookCoverID)
        XCTAssertEqual(first?.width, 400)
        XCTAssertEqual(first?.height, 600)
        XCTAssertEqual(cached?.width, 400)
        XCTAssertEqual(cached?.height, 600)
        XCTAssertEqual(fixture.requestCount, 1)
    }

    func testAlreadyCancelledCallerDoesNotRegisterOrFetch() async throws {
        let fixture = BookCoverURLProtocolFixture(
            responses: [.init(statusCode: 200, data: try coverData(width: 1_200, height: 1_800))]
        )
        BookCoverURLProtocol.fixture = fixture
        defer { BookCoverURLProtocol.fixture = nil }
        let cache = makeCache()

        let request = Task { () -> CGImage? in
            try? await Task.sleep(for: .milliseconds(50))
            return await cache.image(bookCoverID)
        }
        request.cancel()

        let image = await request.value
        XCTAssertNil(image)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(fixture.requestCount, 0)
    }

    private func makeCache() -> BookCoverCache {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BookCoverURLProtocol.self]
        let session = LuminaSession(
            serverURL: URL(string: "https://lumina.example")!, accessToken: "member-token", userID: "member",
            username: "Member", serverName: "Lumina", deviceID: "book-cover-device"
        )
        let api = LuminaBooks(session: session, client: LuminaClient(configuration: configuration))
        return BookCoverCache(api: api)
    }

    private func coverData(width: Int, height: Int) throws -> Data {
        let context = try XCTUnwrap(CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(red: 0.12, green: 0.32, blue: 0.68, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let encoded = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(encoded, "public.jpeg" as CFString, 1, nil))
        CGImageDestinationAddImage(destination, try XCTUnwrap(context.makeImage()), nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return encoded as Data
    }
}

private let bookCoverID = "00000000-0000-0000-0000-000000000001"

private struct BookCoverHTTPResponse: Sendable {
    let statusCode: Int
    let data: Data
}

private final class BookCoverURLProtocolFixture: @unchecked Sendable {
    private let lock = NSLock()
    private let responses: [BookCoverHTTPResponse]
    private let gatedRequests: Set<Int>
    private let lateRequests: Set<Int>
    private var gates = [Int: DispatchSemaphore]()
    private var protocols = [Int: BookCoverURLProtocol]()
    private var responsesDelivered = 0
    private var requestsStarted = 0

    var requestCount: Int { lock.withLock { requestsStarted } }

    init(responses: [BookCoverHTTPResponse], gatedRequests: Set<Int> = [], lateRequests: Set<Int> = []) {
        self.responses = responses
        self.gatedRequests = gatedRequests
        self.lateRequests = lateRequests
    }

    func start(_ loader: BookCoverURLProtocol) {
        let index = lock.withLock { () -> Int in
            requestsStarted += 1
            protocols[requestsStarted] = loader
            if gatedRequests.contains(requestsStarted) { gates[requestsStarted] = DispatchSemaphore(value: 0) }
            return requestsStarted
        }
        let gate = lock.withLock { gates[index] }
        DispatchQueue.global().async {
            if let gate { _ = gate.wait(timeout: .now() + 5) }
            Thread.sleep(forTimeInterval: 0.01)
            let response = self.responses[min(index - 1, self.responses.count - 1)]
            loader.deliver(response, afterCancellation: self.lateRequests.contains(index))
            self.lock.withLock { self.responsesDelivered = max(self.responsesDelivered, index) }
        }
    }

    func release(_ index: Int) {
        let gate = lock.withLock { gates[index] }
        gate?.signal()
    }

    func waitForRequest(atLeast target: Int) async -> Bool {
        for _ in 0..<1_000 {
            if lock.withLock({ requestsStarted >= target }) { return true }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return lock.withLock { requestsStarted >= target }
    }

    func waitForResponse(atLeast target: Int) async -> Bool {
        for _ in 0..<1_000 {
            if lock.withLock({ responsesDelivered >= target }) { return true }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return lock.withLock { responsesDelivered >= target }
    }

    func wasStopped(_ index: Int) -> Bool {
        let loader = lock.withLock { protocols[index] }
        return loader?.wasStopped ?? false
    }

    func waitForStop(_ index: Int) async -> Bool {
        for _ in 0..<1_000 {
            if wasStopped(index) { return true }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return wasStopped(index)
    }
}

private final class BookCoverURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var fixture: BookCoverURLProtocolFixture?

    private let lock = NSLock()
    private var stopped = false

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let fixture = Self.fixture else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        fixture.start(self)
    }

    override func stopLoading() {
        lock.withLock { stopped = true }
    }

    var wasStopped: Bool { lock.withLock { stopped } }

    func deliver(_ response: BookCoverHTTPResponse, afterCancellation: Bool) {
        guard lock.withLock({ !stopped || afterCancellation }) else { return }
        let httpResponse = HTTPURLResponse(
            url: request.url!, statusCode: response.statusCode, httpVersion: nil,
            headerFields: ["Content-Type": "image/jpeg"]
        )!
        client?.urlProtocol(self, didReceive: httpResponse, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: response.data)
        client?.urlProtocolDidFinishLoading(self)
    }
}
