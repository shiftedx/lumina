import Foundation
import XCTest
import ImageIO
import CoreGraphics
@testable import LuminaCore

final class LuminaBooksTests: XCTestCase {
    func testNativeCatalogReaderAndRewindUseMemberHeaderAndStructuredEndpoints() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BooksProtocol.self]
        let api = LuminaBooks(session: LuminaSession(serverURL: URL(string: "https://example.com")!, accessToken: "synthetic-token", userID: "member", username: "reader", serverName: "Test", deviceID: "device"), client: LuminaClient(configuration: configuration))
        let catalog = try await api.catalog(search: "Night")
        XCTAssertEqual(catalog.items.first?.readingFileID, bookID)
        XCTAssertEqual(catalog.items.first?.seriesID, bookID)
        _ = try await api.catalog(search: "Night", kind: .manga, status: .reading, sort: .author)
        let overview = try await api.overview()
        XCTAssertEqual(overview.shelves.first?.bookIDs, [bookID])
        let series = try await api.series()
        XCTAssertEqual(series.items.first?.coverBookID, bookID)
        let volumes = try await api.series(id: bookID)
        XCTAssertEqual(volumes.books.first?.id, bookID)
        let manifest = try await api.manifest(id: bookID)
        XCTAssertEqual(manifest.count, 4)
        XCTAssertEqual(manifest.readingDirection, "rtl")
        let nodes = try await api.chapter(id: bookID, index: 0)
        XCTAssertEqual(nodes.first?.children?.first?.text, "A safe chapter.")
        let saved = try await api.save(id: bookID, unit: 1, offset: 0.2)
        XCTAssertEqual(saved.progress.unitIndex, 1)
        XCTAssertEqual(saved.progress.offset, 0.2)
        do { _ = try await api.manifest(id: "../another-origin"); XCTFail("Unsafe ID accepted") }
        catch LuminaError.unsafeURL { }
    }

    func testReadingLayoutPairsConsecutivePagesAcrossExpansionFoldsAndRTL() {
        let small = BookReaderLayout(size: CGSize(width: 400, height: 720), expanded: false)
        XCTAssertNil(small.companion)
        let artwork = BookReaderLayout(size: CGSize(width: 400, height: 720), expanded: false, artwork: true)
        XCTAssertEqual(artwork.primary, CGRect(x: 0, y: 0, width: 400, height: 720))
        for size in [CGSize(width: 840, height: 720), CGSize(width: 720, height: 840)] {
            let spread = BookReaderLayout(size: size, expanded: true)
            XCTAssertGreaterThan(spread.primary.minX, spread.companion!.maxX)
            XCTAssertEqual(spread.primary.size, spread.companion!.size)
            XCTAssertLessThanOrEqual(spread.primary.maxX, size.width)
        }
        for fold in [CGRect(x: 400, y: 0, width: 40, height: 720), CGRect(x: 0, y: 340, width: 840, height: 40)] {
            let spread = BookReaderLayout(size: CGSize(width: 840, height: 720), expanded: true, divisions: [fold])
            XCTAssertFalse(spread.primary.intersects(fold)); XCTAssertFalse(spread.companion!.intersects(fold))
        }
        let viewport = CGRect(x: 0, y: 0, width: 400, height: 720)
        for expanded in [false, true] {
            for fold in [CGRect(x: -20, y: 0, width: 40, height: 720), CGRect(x: 380, y: 0, width: 40, height: 720), CGRect(x: 0, y: -20, width: 400, height: 40), CGRect(x: 0, y: 700, width: 400, height: 40)] {
                let split = BookReaderLayout(size: viewport.size, expanded: expanded, divisions: [fold])
                XCTAssertNil(split.companion)
                XCTAssertTrue(viewport.contains(split.primary))
                XCTAssertFalse(split.primary.isEmpty)
                XCTAssertFalse(split.primary.intersects(fold))
            }
        }
        let camera = CGRect(x: 780, y: 0, width: 40, height: 40)
        let safe = BookReaderLayout(size: CGSize(width: 840, height: 720), expanded: true, occlusions: [camera])
        XCTAssertFalse(safe.primary.intersects(camera))
        let closedDisplay = CGSize(width: 466, height: 678)
        let outerCamera = CGRect(x: 390, y: 0, width: 76, height: 82)
        let immersive = BookReaderLayout(size: closedDisplay, expanded: false, occlusions: [outerCamera], artwork: true)
        XCTAssertEqual(immersive.primary, CGRect(origin: .zero, size: closedDisplay),
                       "Artwork must not reserve a full-width strip for a corner camera.")
        XCTAssertEqual(BookSpread.companion(current: 3, count: 8, rightToLeft: false), 2)
        XCTAssertEqual(BookSpread.companion(current: 3, count: 8, rightToLeft: true), 3)
        for count in 1...8 {
            for current in 0..<count {
                for rtl in [false, true] {
                    let left = BookSpread.companion(current: current, count: count, rightToLeft: rtl)
                    let right = BookSpread.primary(current: current, count: count, rightToLeft: rtl)
                    let first = current / 2 * 2
                    XCTAssertEqual([left, right].compactMap { $0 }.sorted(), Array(first..<min(count, first + 2)))
                    if let left, let right { XCTAssertEqual(right - left, rtl ? -1 : 1) }
                }
            }
        }
        XCTAssertEqual(BookSpread.companion(current: 4, count: 8, rightToLeft: false), 4)
        XCTAssertEqual(BookSpread.primary(current: 4, count: 8, rightToLeft: false), 5)
        XCTAssertEqual(BookSpread.companion(current: 5, count: 8, rightToLeft: true), 5)
        XCTAssertEqual(BookSpread.primary(current: 5, count: 8, rightToLeft: true), 4)
    }

    func testPagePreparationBoundsDecodedPixelsAndRejectsNonImages() throws {
        let context = try XCTUnwrap(CGContext(data: nil, width: 4096, height: 2048, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        context.setFillColor(CGColor(gray: 0.5, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: 4096, height: 2048))
        let image = try XCTUnwrap(context.makeImage())
        let encoded = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(encoded, "public.jpeg" as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, nil); XCTAssertTrue(CGImageDestinationFinalize(destination))
        let prepared = try XCTUnwrap(BookPagePreparation.image(from: encoded as Data, maximumPixelSize: 1024))
        XCTAssertEqual(prepared.width, 1024); XCTAssertEqual(prepared.height, 512)
        XCTAssertLessThan(prepared.bytesPerRow * prepared.height, image.bytesPerRow * image.height / 8)
        let twoScale = try XCTUnwrap(BookPagePreparation.image(from: encoded as Data, maximumPixelSize: 2048))
        let threeScale = try XCTUnwrap(BookPagePreparation.image(from: encoded as Data, maximumPixelSize: 3072))
        XCTAssertEqual(twoScale.width, 2048)
        XCTAssertEqual(twoScale.height, 1024)
        XCTAssertLessThan(twoScale.bytesPerRow * twoScale.height, threeScale.bytesPerRow * threeScale.height / 2,
                          "A 2x display must not pay for a 3x page's decoded backing")
        print("LUMINA_READER_DECODE twoScaleBytes=\(twoScale.bytesPerRow * twoScale.height) threeScaleBytes=\(threeScale.bytesPerRow * threeScale.height)")
        XCTAssertNil(BookPagePreparation.image(from: Data("not an image".utf8), maximumPixelSize: 1024))
    }

    func testPageWindowDiscardsFailedLookaheadForRetryWithinEncodedBudget() async throws {
        let firstPage = Data(repeating: 0, count: BookPagePreparation.maximumEncodedBytes / 2)
        let loader = RetryingPageLoader(firstPage: firstPage)
        let window = BookPageWindow(load: { index in try await loader.load(index) })

        let visible = try await window.bytes(0)
        XCTAssertEqual(visible.count, firstPage.count)
        await window.retain([0], next: 1)
        do {
            _ = try await window.bytes(1)
            XCTFail("The first speculative page request should fail.")
        } catch LuminaError.malformedResponse { }

        let failedAttempts = await loader.callCount(for: 1)
        XCTAssertEqual(failedAttempts, 1)
        let retriedPage = try await window.bytes(1)
        XCTAssertEqual(retriedPage, Data([1]))
        let retryAttempts = await loader.callCount(for: 1)
        XCTAssertEqual(retryAttempts, 2)

        await window.retain([0], next: 2)
        let prefetchedPage = try await window.bytes(2)
        XCTAssertEqual(prefetchedPage, Data([2]))
        let prefetchAttempts = await loader.callCount(for: 2)
        XCTAssertEqual(prefetchAttempts, 1, "A prefetched page within the remaining budget must be reused.")
    }

    func testPageWindowLimitsVisibleCacheAndDoesNotReuseRetiredLoads() async throws {
        let fullPage = Data(repeating: 0, count: BookPagePreparation.maximumEncodedBytes)
        let loader = RetiredPageLoader(firstPage: fullPage)
        let window = BookPageWindow(load: { index in await loader.load(index) })

        let firstPage = try await window.bytes(0)
        XCTAssertEqual(firstPage, fullPage)
        await window.retain([0, 1], next: 2)
        let speculativeCalls = await loader.callCount(for: 2)
        XCTAssertEqual(speculativeCalls, 0, "A full visible cache must leave no room for speculative work.")
        let firstUncachedPage = try await window.bytes(1)
        let secondUncachedPage = try await window.bytes(1)
        XCTAssertEqual(firstUncachedPage, Data([1]))
        XCTAssertEqual(secondUncachedPage, Data([1]))
        let uncachedCalls = await loader.callCount(for: 1)
        XCTAssertEqual(uncachedCalls, 2, "A visible page remains usable even when it cannot be retained.")

        let partialPage = Data(repeating: 0, count: BookPagePreparation.maximumEncodedBytes / 2)
        let oversizedLookahead = Data(repeating: 2, count: BookPagePreparation.maximumEncodedBytes)
        let fallbackLoader = RetiredPageLoader(firstPage: partialPage, oversizedLookahead: oversizedLookahead)
        let fallbackWindow = BookPageWindow(load: { index in await fallbackLoader.load(index) })
        _ = try await fallbackWindow.bytes(0)
        await fallbackWindow.retain([0], next: 2)
        let fallbackPage = try await fallbackWindow.bytes(2)
        XCTAssertEqual(fallbackPage, Data([2]))
        let fallbackCalls = await fallbackLoader.callCount(for: 2)
        XCTAssertEqual(fallbackCalls, 2, "An oversized prefetched page must be released and fetched explicitly when requested.")

        await window.retain([], next: nil)
        let retired = Task { try await window.bytes(3) }
        let retiredStarted = await loader.waitForCall(to: 3)
        XCTAssertTrue(retiredStarted)
        await window.retain([0], next: nil)
        await loader.release(3)
        let retiredPage = try await retired.value
        XCTAssertEqual(retiredPage, Data([3]))
        _ = try await window.bytes(3)
        let retiredCalls = await loader.callCount(for: 3)
        XCTAssertEqual(retiredCalls, 2, "A load retired by a new visible window must not repopulate the cache.")

        let stopped = Task { try await window.bytes(4) }
        let stoppedStarted = await loader.waitForCall(to: 4)
        XCTAssertTrue(stoppedStarted)
        await window.stop()
        await loader.release(4)
        let stoppedPage = try await stopped.value
        XCTAssertEqual(stoppedPage, Data([4]))
        _ = try await window.bytes(4)
        let stoppedCalls = await loader.callCount(for: 4)
        XCTAssertEqual(stoppedCalls, 2, "Stopping the reader must prevent an in-flight page from being retained later.")
    }
}

private actor RetryingPageLoader {
    private let firstPage: Data
    private var calls = [Int: Int]()

    init(firstPage: Data) { self.firstPage = firstPage }

    func load(_ index: Int) throws -> Data {
        calls[index, default: 0] += 1
        if index == 0 { return firstPage }
        if index == 1, calls[index] == 1 { throw LuminaError.malformedResponse }
        return Data([UInt8(index)])
    }

    func callCount(for index: Int) -> Int { calls[index, default: 0] }
}

private actor RetiredPageLoader {
    private let firstPage: Data
    private let oversizedLookahead: Data?
    private var calls = [Int: Int]()
    private var continuations = [Int: [CheckedContinuation<Void, Never>]]()

    init(firstPage: Data, oversizedLookahead: Data? = nil) {
        self.firstPage = firstPage; self.oversizedLookahead = oversizedLookahead
    }

    func load(_ index: Int) async -> Data {
        calls[index, default: 0] += 1
        if (index == 3 || index == 4), calls[index] == 1 {
            await withCheckedContinuation { continuation in
                continuations[index, default: []].append(continuation)
            }
        }
        if index == 0 { return firstPage }
        if index == 2, calls[index] == 1, let oversizedLookahead { return oversizedLookahead }
        return Data([UInt8(index)])
    }

    func callCount(for index: Int) -> Int { calls[index, default: 0] }

    func waitForCall(to index: Int) async -> Bool {
        for _ in 0..<1_000 {
            if calls[index, default: 0] > 0 { return true }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return calls[index, default: 0] > 0
    }

    func release(_ index: Int) {
        let waiting = continuations.removeValue(forKey: index) ?? []
        waiting.forEach { $0.resume() }
    }
}

private let bookID = "00000000-0000-0000-0000-000000000001"
private final class BooksProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"synthetic-token\"") == true)
        XCTAssertFalse(request.url?.query?.lowercased().contains("token") == true)
        var book: [String: Any] = ["id": bookID, "reading_file_id": bookID, "title": "Night", "author": "Synthetic", "format": "cbz", "kind": "manga", "series_id": bookID, "available": true, "files": [], "progress": ["unit_index": 0, "offset": 0, "completed": false]]
        let result: [String: Any]
        if request.url?.path == "/Lumina/Books", let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false) {
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).compactMap { item in
                item.value.map { (item.name, $0) }
            })
            if query["kind"] != nil {
                XCTAssertEqual(query["kind"], "manga")
                XCTAssertEqual(query["status"], "reading")
                XCTAssertEqual(query["sort"], "author")
            }
        }
        if request.httpMethod == "PATCH" {
            var body = request.httpBody ?? Data()
            if body.isEmpty, let stream = request.httpBodyStream {
                stream.open(); defer { stream.close() }
                var buffer = [UInt8](repeating: 0, count: 1024)
                while stream.hasBytesAvailable {
                    let read = stream.read(&buffer, maxLength: buffer.count)
                    if read <= 0 { break }
                    body.append(contentsOf: buffer.prefix(read))
                }
            }
            let fields = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            XCTAssertEqual(fields?["unit_index"] as? Int, 1)
            XCTAssertEqual(fields?["offset"] as? Double, 0.2)
            XCTAssertEqual(request.url?.path, "/Lumina/Books/\(bookID)/state")
            book["progress"] = ["unit_index": 1, "offset": 0.2, "completed": false]
            result = book
        } else if request.url!.path.hasSuffix("/overview") {
            result = ["continue_reading": [book], "shelves": [["id": bookID, "name": "Night shelf", "count": 1, "book_ids": [bookID]]]]
        } else if request.url!.path.hasSuffix("/series") {
            result = ["items": [["id": bookID, "title": "Night", "author": "Synthetic", "volume_count": 1, "cover_url": "/api/books/\(bookID)/cover"]], "next_cursor": NSNull()]
        } else if request.url!.path.contains("/series/") {
            result = ["books": [book]]
        } else if request.url!.path.hasSuffix("/reader") {
            result = ["book": book, "chapters": [], "page_count": 4, "reading_direction": "rtl"]
        } else if request.url!.path.contains("/chapters/") {
            result = ["nodes": [["tag": "p", "children": [["tag": "span", "text": "A safe chapter."]]]]]
        } else { result = ["items": [book], "next_cursor": NSNull()] }
        let data = try! JSONSerialization.data(withJSONObject: result)
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }
}
