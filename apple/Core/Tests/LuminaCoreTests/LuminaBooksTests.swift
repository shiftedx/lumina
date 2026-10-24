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
        XCTAssertNil(BookPagePreparation.image(from: Data("not an image".utf8), maximumPixelSize: 1024))
    }

    func testPageWindowDiscardsFailedLookaheadForRetryWithinEncodedBudget() async throws {
        let firstPage = Data(repeating: 0, count: BookPagePreparation.maximumEncodedBytes)
        let loader = RetryingPageLoader(firstPage: firstPage)
        let window = BookPageWindow(load: { index in try await loader.load(index) })

        let visible = try await window.bytes(0)
        XCTAssertEqual(visible.count, BookPagePreparation.maximumEncodedBytes)
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
