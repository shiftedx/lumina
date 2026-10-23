import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import LuminaCore

final class BillboardArtworkPreparationTests: XCTestCase {
    func testDownsamplesLandscapePortraitAndRotatedArtwork() throws {
        let landscape = try XCTUnwrap(BillboardArtworkPreparation.image(from: jpeg(width: 4_000, height: 3_000)))
        XCTAssertEqual(landscape.width, 1_920)
        XCTAssertEqual(landscape.height, 1_440)

        let portrait = try XCTUnwrap(BillboardArtworkPreparation.image(from: jpeg(width: 2_000, height: 4_000)))
        XCTAssertEqual(portrait.width, 960)
        XCTAssertEqual(portrait.height, 1_920)

        let rotated = try XCTUnwrap(BillboardArtworkPreparation.image(from: jpeg(width: 4_000, height: 2_000, orientation: .right)))
        XCTAssertEqual(rotated.width, 960)
        XCTAssertEqual(rotated.height, 1_920)

        let small = try XCTUnwrap(BillboardArtworkPreparation.image(from: jpeg(width: 320, height: 180)))
        XCTAssertEqual(small.width, 320)
        XCTAssertEqual(small.height, 180)
    }

    func testRejectsMalformedAndOversizedArtworkBeforeDecode() {
        XCTAssertNil(BillboardArtworkPreparation.image(from: Data("not an image".utf8)))
        XCTAssertNil(BillboardArtworkPreparation.image(from: Data(repeating: 0, count: BillboardArtworkPreparation.maximumEncodedBytes + 1)))
    }

    private func jpeg(width: Int, height: Int, orientation: CGImagePropertyOrientation? = nil) -> Data {
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 0.1, green: 0.3, blue: 0.6, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil)!
        var properties: [CFString: Any] = [:]
        if let orientation { properties[kCGImagePropertyOrientation] = orientation.rawValue }
        CGImageDestinationAddImage(destination, context.makeImage()!, properties as CFDictionary)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }
}

extension BillboardArtworkPreparationTests {
    @MainActor
    func testArtworkCacheCoalescesRepeatReturnsAndKeepsSignedRenditionsSeparate() async throws {
        ArtworkImageCache.shared.removeAll()
        let recorder = ArtworkRequestRecorder()
        let blocker = ArtworkResponseBlocker()
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            if request.url?.query?.contains("tag=pending") == true { blocker.arriveAndWait() }
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            return (response, self.jpeg(width: 320, height: 180))
        }
        defer { ArtworkCacheURLProtocol.handler = nil }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let decoder = ArtworkDecodeRecorder()
        let cache = ArtworkImageCache(
            session: URLSession(configuration: configuration),
            maximumDecodedBytes: 2 * 1024 * 1024,
            imageCountLimit: 4,
            decode: { data in
                decoder.record()
                return BillboardArtworkPreparation.image(from: data)
            }
        )
        let currentRendition = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=member-one"))
        let refreshedRendition = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=changed&ApiKey=member-one"))
        let anotherMemberRendition = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=member-two"))

        async let first = cache.image(for: currentRendition)
        async let returningHome = cache.image(for: currentRendition)
        let firstImage = await first
        let returningImage = await returningHome
        XCTAssertNotNil(firstImage)
        XCTAssertNotNil(returningImage)
        XCTAssertEqual(recorder.requestCount, 1, "Two Home card lifetimes for one rendition share one fetch")
        XCTAssertEqual(decoder.decodeCount, 1, "Two Home card lifetimes for one rendition share one decode")

        let cachedReturn = await cache.image(for: currentRendition)
        XCTAssertNotNil(cachedReturn)
        XCTAssertEqual(recorder.requestCount, 1, "Returning Home after the first render reuses the decoded rendition")
        XCTAssertEqual(decoder.decodeCount, 1)

        let refreshed = await cache.image(for: refreshedRendition)
        XCTAssertNotNil(refreshed)
        XCTAssertEqual(recorder.requestCount, 2, "A changed artwork tag or signed session URL must not reuse an earlier rendition")
        XCTAssertEqual(decoder.decodeCount, 2)

        let anotherMember = await cache.image(for: anotherMemberRendition)
        XCTAssertNotNil(anotherMember)
        XCTAssertEqual(recorder.requestCount, 3, "A different member's signed URL is a distinct private cache key")
        XCTAssertEqual(decoder.decodeCount, 3)

        cache.removeAll()
        let afterMemberChange = await cache.image(for: currentRendition)
        XCTAssertNotNil(afterMemberChange)
        XCTAssertEqual(recorder.requestCount, 4, "Changing members discards retained decoded artwork even if a server reused a signed URL")
        XCTAssertEqual(decoder.decodeCount, 4)

        let pendingRendition = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=pending&ApiKey=member-one"))
        let staleLoad = Task { await cache.image(for: pendingRendition) }
        let started = await blocker.waitForStart()
        XCTAssertTrue(started, "The pending artwork request did not start.")
        cache.removeAll()
        blocker.unblock()
        let staleImage = await staleLoad.value
        XCTAssertNil(staleImage, "A member change must not deliver an in-flight predecessor's image")
        XCTAssertEqual(recorder.requestCount, 5)
        XCTAssertEqual(decoder.decodeCount, 4, "Cancelled predecessor work is not decoded or retained")

        let tooSmallToRetain = ArtworkImageCache(
            session: URLSession(configuration: configuration),
            maximumDecodedBytes: 4,
            imageCountLimit: 4,
            decode: { data in
                decoder.record()
                return BillboardArtworkPreparation.image(from: data)
            }
        )
        let oversizedFirst = await tooSmallToRetain.image(for: refreshedRendition)
        let oversizedSecond = await tooSmallToRetain.image(for: refreshedRendition)
        XCTAssertNotNil(oversizedFirst)
        XCTAssertNotNil(oversizedSecond)
        XCTAssertEqual(recorder.requestCount, 7, "An image exceeding the resident byte target is never retained")
        XCTAssertEqual(decoder.decodeCount, 6)
    }
}

private final class ArtworkRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var requestCount: Int { lock.withLock { count } }
    func record(_ url: URL?) { lock.withLock { count += 1 } }
}

private final class ArtworkDecodeRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var decodeCount: Int { lock.withLock { count } }
    func record() { lock.withLock { count += 1 } }
}

private final class ArtworkResponseBlocker: @unchecked Sendable {
    private let started = DispatchSemaphore(value: 0)
    private let gate = DispatchSemaphore(value: 0)

    func arriveAndWait() {
        started.signal()
        gate.wait()
    }

    func waitForStart() async -> Bool {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: self.waitForStartSynchronously())
            }
        }
    }

    private func waitForStartSynchronously() -> Bool {
        started.wait(timeout: .now() + 5) == .success
    }

    func unblock() { gate.signal() }
}

private final class ArtworkCacheURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        let (response, data) = handler(request)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
