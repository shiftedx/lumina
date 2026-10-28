import CoreGraphics
import Foundation
import ImageIO

// From apple/, compile with the production preparation code:
// mise exec -- swiftc -swift-version 6 -O Core/Sources/LuminaCore/BillboardArtworkPreparation.swift Core/Sources/LuminaCore/ArtworkImageCache.swift Core/Sources/LuminaCore/ArtworkDiskCache.swift scripts/benchmark_artwork_preparation.swift -o /tmp/lumina-artwork-benchmark
// mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 /tmp/lumina-artwork-benchmark
@main
struct ArtworkPreparationBenchmark {
    @MainActor static func main() async throws {
        let data = try sourceJPEG(width: 4_000, height: 3_000)
        let source = try fullResolutionImage(data)
        let prepared = try unwrap(BillboardArtworkPreparation.image(from: data))
        let clock = ContinuousClock()
        var fullTimes = [Double](), preparedTimes = [Double]()
        for index in 0..<5 {
            let full = { seconds(clock.measure { try! forceDecode(fullResolutionImage(data)) }) }
            let prepared = { seconds(clock.measure { try! forceDecode(unwrap(BillboardArtworkPreparation.image(from: data))) }) }
            if index.isMultiple(of: 2) {
                fullTimes.append(full())
                preparedTimes.append(prepared())
            } else {
                preparedTimes.append(prepared())
                fullTimes.append(full())
            }
        }

        print("fixture=4000x3000 JPEG; encoded bytes=\(data.count); runs=5")
        print("before samples ms=\(fullTimes.map(milliseconds).joined(separator: ","))")
        print("after samples ms=\(preparedTimes.map(milliseconds).joined(separator: ","))")
        print("before=\(source.width)x\(source.height); decoded RGBA bytes=\(rgbaBytes(source)); median decode ms=\(milliseconds(fullTimes.sorted()[2]))")
        print("after=\(prepared.width)x\(prepared.height); decoded RGBA bytes=\(rgbaBytes(prepared)); median prepare ms=\(milliseconds(preparedTimes.sorted()[2]))")
        try await benchmarkLibraryReturns()
        try await benchmarkPersistentLibraryReturns()

    }

    @MainActor private static func benchmarkLibraryReturns() async throws {
        ArtworkDelayProtocol.artwork = try sourceJPEG(width: 400, height: 600)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkDelayProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let clock = ContinuousClock()
        var before = [Double](), after = [Double]()
        var beforeRequests = 0, afterRequests = 0
        for run in 0..<5 {
            for encodedBytes in run.isMultiple(of: 2) ? [0, ArtworkImageCache.defaultEncodedByteLimit] : [ArtworkImageCache.defaultEncodedByteLimit, 0] {
                let cache = ArtworkImageCache(session: session, maximumDecodedBytes: 4,
                    maximumEncodedBytes: encodedBytes, diskCache: nil)
                let url = URL(string: "https://images.example/Items/\(run)-\(encodedBytes)/Images/Primary?tag=current")!
                _ = try unwrap(await cache.image(for: url))
                let requests = ArtworkDelayProtocol.requestCount
                let start = clock.now
                _ = try unwrap(await cache.image(for: url))
                let elapsed = seconds(start.duration(to: clock.now))
                if encodedBytes == 0 {
                    before.append(elapsed)
                    beforeRequests += ArtworkDelayProtocol.requestCount - requests
                } else {
                    after.append(elapsed)
                    afterRequests += ArtworkDelayProtocol.requestCount - requests
                }
            }
        }
        print("library-return fixture=400x600 JPEG; encoded bytes=\(ArtworkDelayProtocol.artwork.count); network delay=100 ms; decoded residency forced off; runs=5")
        print("without compressed cache: median return ms=\(milliseconds(before.sorted()[2])); additional requests=\(beforeRequests)")
        print("with compressed cache: median return ms=\(milliseconds(after.sorted()[2])); additional requests=\(afterRequests)")
    }

    @MainActor private static func benchmarkPersistentLibraryReturns() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ArtworkDelayProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("LuminaArtworkBenchmark-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let clock = ContinuousClock()
        var cold = [Double](), warm = [Double]()
        var warmRequests = 0
        for run in 0..<5 {
            let disk = ArtworkDiskCache(directory: root.appendingPathComponent(String(run)), maximumBytes: 8 * 1024 * 1024)
            let url = URL(string: "https://images.example/Items/disk-\(run)/Images/Primary?tag=current")!
            let initial = ArtworkImageCache(session: session, maximumDecodedBytes: 4, maximumEncodedBytes: 0, diskCache: disk)
            let coldStart = clock.now
            _ = try unwrap(await initial.image(for: url))
            cold.append(seconds(coldStart.duration(to: clock.now)))
            let deadline = clock.now.advanced(by: .seconds(2))
            while await disk.usageBytes() == 0, clock.now < deadline {
                try await Task.sleep(for: .milliseconds(5))
            }
            guard await disk.usageBytes() > 0 else { throw BenchmarkError.image }
            let restored = ArtworkImageCache(session: session, maximumDecodedBytes: 4, maximumEncodedBytes: 0, diskCache: disk)
            let requests = ArtworkDelayProtocol.requestCount
            let warmStart = clock.now
            _ = try unwrap(await restored.image(for: url))
            warm.append(seconds(warmStart.duration(to: clock.now)))
            warmRequests += ArtworkDelayProtocol.requestCount - requests
        }
        print("persistent-return fixture=400x600 JPEG; network delay=100 ms; new cache instance; memory retention disabled; runs=5")
        print("cold median ms=\(milliseconds(cold.sorted()[2])); persisted median ms=\(milliseconds(warm.sorted()[2])); warm additional requests=\(warmRequests)")
    }

    private static func sourceJPEG(width: Int, height: Int) throws -> Data {
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let image: CGImage = {
                  context.setFillColor(CGColor(red: 0.1, green: 0.3, blue: 0.6, alpha: 1))
                  context.fill(CGRect(x: 0, y: 0, width: width, height: height))
                  return context.makeImage()
              }()
        else { throw BenchmarkError.image }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else { throw BenchmarkError.image }
        CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw BenchmarkError.image }
        return data as Data
    }

    private static func fullResolutionImage(_ data: Data) throws -> CGImage {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary)
        else { throw BenchmarkError.image }
        return image
    }

    private static func forceDecode(_ image: CGImage) throws {
        guard let context = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { throw BenchmarkError.image }
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    }

    private static func rgbaBytes(_ image: CGImage) -> Int { image.bytesPerRow * image.height }
    private static func milliseconds(_ seconds: Double) -> String { String(format: "%.3f", seconds * 1_000) }
    private static func seconds(_ duration: Duration) -> Double {
        let parts = duration.components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }
    private static func unwrap<T>(_ value: T?) throws -> T { guard let value else { throw BenchmarkError.image }; return value }

    private enum BenchmarkError: Error { case image }
}

private final class ArtworkDelayProtocol: URLProtocol {
    nonisolated(unsafe) static var artwork = Data()
    private static let lock = NSLock()
    nonisolated(unsafe) private static var count = 0
    static var requestCount: Int { lock.withLock { count } }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Thread.sleep(forTimeInterval: 0.1)
        Self.lock.withLock { Self.count += 1 }
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.artwork)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
