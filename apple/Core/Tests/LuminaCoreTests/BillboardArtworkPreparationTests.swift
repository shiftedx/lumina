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
    func testBackgroundArtworkTrimsMemoryStopsLookaheadAndRetainsDiskRenditions() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let disk = ArtworkDiskCache(directory: directory, maximumBytes: 8 * 1024 * 1024)
        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let cache = ArtworkImageCache(session: URLSession(configuration: configuration), diskCache: disk)
        let url = URL(string: "https://images.example/visited")!
        let next = URL(string: "https://images.example/upcoming")!
        let image = await cache.image(for: url)
        XCTAssertNotNil(image)
        // A cache write is asynchronous; wait for this rendition before testing reuse.
        for _ in 0..<100 where await disk.usageBytes() == 0 { try await Task.sleep(for: .milliseconds(10)) }
        cache.setBackgrounded(true)
        XCTAssertNil(cache.cachedImage(for: url), "Suspension must release disposable decoded artwork")
        await cache.prefetch([next])
        XCTAssertEqual(recorder.requestCount, 1, "Background artwork lookahead must do no network work")
        let backgroundArtwork = await cache.image(for: url)
        XCTAssertNotNil(backgroundArtwork, "System Now Playing artwork must remain available")
        XCTAssertNil(cache.cachedImage(for: url), "Background requests must not refill resident artwork")
        cache.setBackgrounded(false)
        let restored = await cache.image(for: url)
        XCTAssertNotNil(restored)
        XCTAssertEqual(recorder.requestCount, 1, "Foreground return must reuse the private disk rendition")
        await cache.prefetch([next])
        XCTAssertEqual(recorder.requestCount, 2, "Foreground lookahead must resume")
        cache.trimMemory()
        XCTAssertNil(cache.cachedImage(for: next), "A memory warning must release resident artwork too")
    }

    @MainActor
    func testBackgroundingCancelsActiveLookaheadAndDiscardsItsRemainingWindow() async throws {
        let requests = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            requests.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let blocker = ArtworkResponseBlocker()
        let concurrency = ArtworkDecodeConcurrency()
        let cache = ArtworkImageCache(session: URLSession(configuration: configuration),
            maximumDecodedBytes: 8 * 1024 * 1024, imageCountLimit: 32,
            decode: { bytes in
                let shouldBlock = concurrency.begin()
                defer { concurrency.end() }
                if shouldBlock { blocker.arriveAndWait() }
                return BillboardArtworkPreparation.image(from: bytes)
            }, diskCache: nil)
        let urls = (0..<16).map { URL(string: "https://images.example/\($0)")! }
        let prefetch = Task { await cache.prefetch(urls) }
        for _ in 0..<4 { let started = await blocker.waitForStart(); XCTAssertTrue(started) }
        cache.setBackgrounded(true)
        for _ in 0..<4 { blocker.unblock() }
        await prefetch.value
        XCTAssertEqual(requests.requestCount, 4, "Only the admitted batch may start; backgrounding discards the other twelve")
        XCTAssertTrue(urls.allSatisfy { cache.cachedImage(for: $0) == nil }, "Late decoded results must not refill the suspended cache")
    }

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
        XCTAssertEqual(recorder.requestCount, 6, "Returning to an evicted decoded image must reuse its compressed rendition without another network request")
        XCTAssertEqual(decoder.decodeCount, 6)

        tooSmallToRetain.removeAll()
        let afterClearingBothTiers = await tooSmallToRetain.image(for: refreshedRendition)
        XCTAssertNotNil(afterClearingBothTiers)
        XCTAssertEqual(recorder.requestCount, 7, "Account teardown clears compressed artwork as well as decoded images")

        let noRetention = ArtworkImageCache(session: URLSession(configuration: configuration),
            maximumDecodedBytes: 4, imageCountLimit: 4, maximumEncodedBytes: 4, diskCache: nil)
        _ = await noRetention.image(for: currentRendition)
        _ = await noRetention.image(for: currentRendition)
        XCTAssertEqual(recorder.requestCount, 9, "A rendition exceeding both cache budgets must not be retained")
    }

    @MainActor
    func testUpcomingArtworkIsReadyWithoutAnotherFetchAndLookaheadIsBounded() async throws {
        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let cache = ArtworkImageCache(session: URLSession(configuration: configuration), diskCache: nil)
        let upcoming = (0..<40).map { URL(string: "https://images.example/Items/\($0)/Images/Primary?tag=current")! }
        await cache.prefetch(upcoming)
        XCTAssertEqual(recorder.requestCount, 16, "Lookahead must stop at a small window, not fetch the entire library")
        XCTAssertNotNil(cache.cachedImage(for: upcoming[0]), "The upcoming tile must mount with decoded artwork immediately available")
        XCTAssertNil(cache.cachedImage(for: upcoming[16]))
        _ = await cache.image(for: upcoming[0])
        XCTAssertEqual(recorder.requestCount, 16, "Displaying a prefetched tile must not request it again")
        cache.removeAll()
        XCTAssertNil(cache.cachedImage(for: upcoming[0]))
    }
    @MainActor
    func testNewLookaheadReplacesQueuedArtworkAndSharesFourLoadLimit() async throws {
        let data = jpeg(width: 120, height: 180)
        let requests = ArtworkRequestRecorder()
        ArtworkCacheURLProtocol.handler = { request in
            requests.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let blocker = ArtworkResponseBlocker()
        let concurrency = ArtworkDecodeConcurrency()
        let cache = ArtworkImageCache(session: URLSession(configuration: configuration),
            maximumDecodedBytes: 16 * 1024 * 1024, imageCountLimit: 80,
            decode: { data in
                let block = concurrency.begin()
                defer { concurrency.end() }
                if block { blocker.arriveAndWait() }
                return BillboardArtworkPreparation.image(from: data)
            }, diskCache: nil)
        let firstWindow = (0..<16).map { URL(string: "https://images.example/Items/\($0)/Images/Primary")! }
        let nextWindow = (16..<32).map { URL(string: "https://images.example/Items/\($0)/Images/Primary")! }
        let first = Task { await cache.prefetch(firstWindow) }
        for _ in 0..<4 {
            let started = await blocker.waitForStart()
            XCTAssertTrue(started)
        }
        let release = Task { for _ in 0..<4 { blocker.unblock() } }
        await cache.prefetch(nextWindow)
        await release.value
        await first.value
        XCTAssertLessThanOrEqual(concurrency.maximumActive, 4,
                                 "Rapidly replacing lookahead must share one four-load budget")
        XCTAssertEqual(requests.requestCount, 20, "Finish four active loads, then load the latest window; discard stale queued images")
        XCTAssertNil(cache.cachedImage(for: firstWindow[4]))
        XCTAssertNotNil(cache.cachedImage(for: nextWindow[0]))
    }

    @MainActor
    func testArtworkTransportBoundsDeclaredAndChunkedResponsesBeforeDecode() async throws {
        let jpeg = jpeg(width: 120, height: 180)
        let chunk = Data(repeating: 0, count: 1_024 * 1_024)
        let requests = ArtworkRequestRecorder()
        let stops = ArtworkStopRecorder()
        let decoder = ArtworkDecodeRecorder()
        BoundedArtworkURLProtocol.reply = { request in
            requests.record(request.url)
            let url = request.url!
            if url.lastPathComponent == "declared" {
                let response = HTTPURLResponse(
                    url: url, statusCode: 200, httpVersion: nil,
                    headerFields: ["Content-Length": String(BillboardArtworkPreparation.maximumEncodedBytes + 1)]
                )!
                return (response, [jpeg])
            }
            if url.lastPathComponent == "chunked" {
                let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!
                return (response, Array(repeating: chunk, count: 13))
            }
            let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil,
                                           headerFields: ["Content-Length": String(jpeg.count)])!
            return (response, [jpeg])
        }
        BoundedArtworkURLProtocol.onStop = { stops.record() }
        defer {
            BoundedArtworkURLProtocol.reply = nil
            BoundedArtworkURLProtocol.onStop = nil
        }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BoundedArtworkURLProtocol.self]
        let cache = ArtworkImageCache(
            session: URLSession(configuration: configuration),
            maximumDecodedBytes: 2 * 1_024 * 1_024,
            imageCountLimit: 4,
            decode: { data in
                decoder.record()
                return BillboardArtworkPreparation.image(from: data)
            }
        )
        let declared = try XCTUnwrap(URL(string: "https://images.example/declared"))
        let chunked = try XCTUnwrap(URL(string: "https://images.example/chunked"))
        let normal = try XCTUnwrap(URL(string: "https://images.example/normal"))

        let declaredImage = await cache.image(for: declared)
        let declaredStopped = await stops.waitForStop()
        XCTAssertNil(declaredImage)
        XCTAssertTrue(declaredStopped, "An advertised oversized response must be cancelled before its body is buffered")
        let chunkedImage = await cache.image(for: chunked)
        let chunkedStopped = await stops.waitForStop()
        XCTAssertNil(chunkedImage)
        XCTAssertTrue(chunkedStopped, "An unknown-length response must be cancelled as soon as it crosses the byte limit")
        XCTAssertEqual(decoder.decodeCount, 0)

        let normalImage = await cache.image(for: normal)
        let cachedNormalImage = await cache.image(for: normal)
        XCTAssertNotNil(normalImage)
        XCTAssertNotNil(cachedNormalImage)
        XCTAssertEqual(requests.requestCount, 3, "A valid streamed response must still populate the decoded cache")
        XCTAssertEqual(decoder.decodeCount, 1)
    }

    @MainActor
    func testDiskArtworkCacheWarmsNewInstanceAndClearForcesNetwork() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: 8 * 1024 * 1024)
        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let firstURL = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=member-one"))
        let anotherMemberURL = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=member-two"))

        let firstCache = ArtworkImageCache(session: session, diskCache: diskCache)
        let firstImage = await firstCache.image(for: firstURL)
        XCTAssertNotNil(firstImage)
        XCTAssertEqual(recorder.requestCount, 1)

        let warmCache = ArtworkImageCache(session: session, diskCache: diskCache)
        let warmImage = await warmCache.image(for: firstURL)
        XCTAssertNotNil(warmImage)
        XCTAssertEqual(recorder.requestCount, 1, "A new cache instance reuses the persisted compressed rendition")
        let anotherMemberImage = await warmCache.image(for: anotherMemberURL)
        XCTAssertNotNil(anotherMemberImage)
        XCTAssertEqual(recorder.requestCount, 2, "The full signed URL, including member credential, is part of the disk key")
        let usageBeforeClear = await warmCache.diskUsageBytes()
        XCTAssertGreaterThan(usageBeforeClear, 0)

        await warmCache.clearAllArtwork()
        let usageAfterClear = await warmCache.diskUsageBytes()
        XCTAssertEqual(usageAfterClear, 0)

        let afterClear = ArtworkImageCache(session: session, diskCache: diskCache)
        let afterClearImage = await afterClear.image(for: firstURL)
        XCTAssertNotNil(afterClearImage)
        XCTAssertEqual(recorder.requestCount, 3, "An explicit clear prevents a persisted rendition from satisfying the next request")
    }

    @MainActor
    func testClearingDiskArtworkCacheRejectsAnInflightWrite() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: 8 * 1024 * 1024)
        let recorder = ArtworkRequestRecorder()
        let blocker = ArtworkResponseBlocker()
        let requestGate = ArtworkRequestGate()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            if requestGate.claimFirst() { blocker.arriveAndWait() }
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let url = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=pending&ApiKey=member-one"))
        let cache = ArtworkImageCache(session: session, diskCache: diskCache)

        let staleLoad = Task { await cache.image(for: url) }
        let started = await blocker.waitForStart()
        XCTAssertTrue(started)
        await cache.clearAllArtwork()
        blocker.unblock()
        let staleImage = await staleLoad.value
        XCTAssertNil(staleImage)

        let nextCache = ArtworkImageCache(session: session, diskCache: diskCache)
        let imageAfterClear = await nextCache.image(for: url)
        XCTAssertNotNil(imageAfterClear)
        XCTAssertEqual(recorder.requestCount, 2, "The completed predecessor must not recreate disk data after a clear")
    }

    func testDiskArtworkCachePrunesToItsByteLimit() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: 4)
        let firstURL = try XCTUnwrap(URL(string: "https://images.example/one?ApiKey=member-one"))
        let secondURL = try XCTUnwrap(URL(string: "https://images.example/two?ApiKey=member-one"))
        let generation = diskCache.currentGeneration
        await diskCache.store(Data([1, 2, 3]), for: firstURL, generation: generation)
        await diskCache.store(Data([4, 5, 6]), for: secondURL, generation: generation)

        let usage = await diskCache.usageBytes()
        XCTAssertLessThanOrEqual(usage, 4)
    }

    @MainActor
    func testDiskArtworkCacheRetainsRecentlyReadRenditionWhenPruning() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let data = jpeg(width: 120, height: 180)
        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: data.count * 2)
        let firstURL = try XCTUnwrap(URL(string: "https://images.example/one?ApiKey=member-one"))
        let secondURL = try XCTUnwrap(URL(string: "https://images.example/two?ApiKey=member-one"))
        let thirdURL = try XCTUnwrap(URL(string: "https://images.example/three?ApiKey=member-one"))
        let generation = diskCache.currentGeneration
        await diskCache.store(data, for: firstURL, generation: generation)
        try? await Task.sleep(for: .milliseconds(10))
        await diskCache.store(data, for: secondURL, generation: generation)

        let recorder = ArtworkRequestRecorder()
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]

        let recentReader = ArtworkImageCache(session: URLSession(configuration: configuration), diskCache: diskCache)
        let recentlyRead = await recentReader.image(for: firstURL)
        XCTAssertNotNil(recentlyRead)
        XCTAssertEqual(recorder.requestCount, 0, "The first rendition should come from disk before exercising eviction")

        try? await Task.sleep(for: .milliseconds(10))
        await diskCache.store(data, for: thirdURL, generation: generation)
        let usage = await diskCache.usageBytes()
        XCTAssertLessThanOrEqual(usage, data.count * 2)

        let returningCache = ArtworkImageCache(session: URLSession(configuration: configuration), diskCache: diskCache)
        let retainedImage = await returningCache.image(for: firstURL)
        XCTAssertNotNil(retainedImage)
        XCTAssertEqual(recorder.requestCount, 0, "A read hit must refresh the rendition before bounded LRU pruning")
        let evictedImage = await returningCache.image(for: secondURL)
        XCTAssertNotNil(evictedImage)
        XCTAssertEqual(recorder.requestCount, 1, "The least recently used rendition must refetch after the byte limit is reached")
    }

    @MainActor
    func testCorruptAndOversizedDiskArtworkFallThroughToNetwork() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let oversized = Data(repeating: 0, count: BillboardArtworkPreparation.maximumEncodedBytes + 1)
        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: oversized.count + 1)
        let corruptURL = try XCTUnwrap(URL(string: "https://images.example/corrupt?ApiKey=member-one"))
        let oversizedURL = try XCTUnwrap(URL(string: "https://images.example/oversized?ApiKey=member-one"))
        let generation = diskCache.currentGeneration
        await diskCache.store(Data("not an image".utf8), for: corruptURL, generation: generation)
        await diskCache.store(oversized, for: oversizedURL, generation: generation)

        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let cache = ArtworkImageCache(session: URLSession(configuration: configuration), diskCache: diskCache)

        let corruptImage = await cache.image(for: corruptURL)
        let oversizedImage = await cache.image(for: oversizedURL)
        XCTAssertNotNil(corruptImage)
        XCTAssertNotNil(oversizedImage)
        XCTAssertEqual(recorder.requestCount, 2, "Invalid persisted bytes must be deleted and refetched, never delivered as a permanent artwork failure")
    }

    @MainActor
    func testCancellingADiskDecodeDoesNotEvictItsValidRendition() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: 8 * 1024 * 1024)
        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let url = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=member-one"))

        let primingCache = ArtworkImageCache(session: session, diskCache: diskCache)
        let primedImage = await primingCache.image(for: url)
        XCTAssertNotNil(primedImage)
        var persistedBytes = 0
        for _ in 0..<100 where persistedBytes == 0 {
            persistedBytes = await diskCache.usageBytes()
            await Task.yield()
        }
        XCTAssertGreaterThan(persistedBytes, 0)

        let decoder = ArtworkDecodeBlocker()
        let cancellingCache = ArtworkImageCache(
            session: session,
            maximumDecodedBytes: ArtworkImageCache.defaultDecodedByteLimit,
            imageCountLimit: ArtworkImageCache.defaultImageCountLimit,
            decode: { bytes in
                decoder.arriveAndWait()
                return BillboardArtworkPreparation.image(from: bytes)
            },
            diskCache: diskCache
        )
        let pendingImage = Task { await cancellingCache.image(for: url) }
        let decodeStarted = await decoder.waitForStart()
        XCTAssertTrue(decodeStarted)
        pendingImage.cancel()
        let cancelledImage = await pendingImage.value
        XCTAssertNil(cancelledImage)
        decoder.unblock()
        let decodeFinished = await decoder.waitForFinish()
        XCTAssertTrue(decodeFinished)

        let returningCache = ArtworkImageCache(session: session, diskCache: diskCache)
        let retainedImage = await returningCache.image(for: url)
        XCTAssertNotNil(retainedImage)
        XCTAssertEqual(recorder.requestCount, 1, "A canceled valid disk decode must leave the persisted rendition available")
    }

    @MainActor
    func testSynchronousRemovalPurgesDiskBeforeAnImmediateSameURLLoad() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("LuminaArtworkTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let diskCache = ArtworkDiskCache(directory: directory, maximumBytes: 8 * 1024 * 1024)
        let recorder = ArtworkRequestRecorder()
        let data = jpeg(width: 120, height: 180)
        ArtworkCacheURLProtocol.handler = { request in
            recorder.record(request.url)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, data)
        }
        defer { ArtworkCacheURLProtocol.handler = nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkCacheURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let url = try XCTUnwrap(URL(string: "https://images.example/Items/one/Images/Primary?tag=current&ApiKey=reused-url"))

        let cache = ArtworkImageCache(session: session, diskCache: diskCache)
        let primedImage = await cache.image(for: url)
        XCTAssertNotNil(primedImage)
        var persistedBytes = 0
        for _ in 0..<100 where persistedBytes == 0 {
            persistedBytes = await diskCache.usageBytes()
            await Task.yield()
        }
        XCTAssertGreaterThan(persistedBytes, 0)

        cache.removeAll()
        let immediateImage = await cache.image(for: url)
        XCTAssertNotNil(immediateImage)
        XCTAssertEqual(recorder.requestCount, 2, "An immediate same-URL load after account teardown must not read the prior generation's disk bytes")
    }
}

private final class ArtworkDecodeConcurrency: @unchecked Sendable {
    private let lock = NSLock()
    private var active = 0
    private var calls = 0
    private var peak = 0
    var maximumActive: Int { lock.withLock { peak } }
    func begin() -> Bool {
        lock.withLock {
            active += 1
            calls += 1
            peak = max(peak, active)
            return calls <= 4
        }
    }
    func end() { lock.withLock { active -= 1 } }
}

private final class ArtworkRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var requestCount: Int { lock.withLock { count } }
    func record(_ url: URL?) { lock.withLock { count += 1 } }
}

private final class ArtworkRequestGate: @unchecked Sendable {
    private let lock = NSLock()
    private var first = true

    func claimFirst() -> Bool {
        lock.withLock {
            defer { first = false }
            return first
        }
    }
}

private final class ArtworkDecodeBlocker: @unchecked Sendable {
    private let started = DispatchSemaphore(value: 0)
    private let gate = DispatchSemaphore(value: 0)
    private let finished = DispatchSemaphore(value: 0)

    func arriveAndWait() {
        started.signal()
        gate.wait()
        finished.signal()
    }

    func waitForStart() async -> Bool {
        await wait(on: started)
    }

    func waitForFinish() async -> Bool {
        await wait(on: finished)
    }

    func unblock() { gate.signal() }

    private func wait(on semaphore: DispatchSemaphore) async -> Bool {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: semaphore.wait(timeout: .now() + 5) == .success)
            }
        }
    }
}

private final class ArtworkDecodeRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var decodeCount: Int { lock.withLock { count } }
    func record() { lock.withLock { count += 1 } }
}

private final class ArtworkStopRecorder: @unchecked Sendable {
    private let stopped = DispatchSemaphore(value: 0)

    func record() { stopped.signal() }

    func waitForStop() async -> Bool {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                continuation.resume(returning: self.stopped.wait(timeout: .now() + 5) == .success)
            }
        }
    }
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

private final class BoundedArtworkURLProtocol: URLProtocol {
    nonisolated(unsafe) static var reply: ((URLRequest) -> (HTTPURLResponse, [Data]))?
    nonisolated(unsafe) static var onStop: (() -> Void)?
    private let lock = NSLock()
    private var stopped = false

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let reply = Self.reply else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        let (response, chunks) = reply(request)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        for chunk in chunks {
            Thread.sleep(forTimeInterval: 0.005)
            guard !lock.withLock({ stopped }) else { return }
            client?.urlProtocol(self, didLoad: chunk)
        }
        guard !lock.withLock({ stopped }) else { return }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {
        let shouldReport = lock.withLock {
            guard !stopped else { return false }
            stopped = true
            return true
        }
        if shouldReport { Self.onStop?() }
    }
}
