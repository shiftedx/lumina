import CoreGraphics
import Darwin
import Foundation
import ImageIO

// Compile from the repository root with the three production artwork sources:
// mise exec -- swiftc -swift-version 6 -O apple/Core/Sources/LuminaCore/BillboardArtworkPreparation.swift apple/Core/Sources/LuminaCore/ArtworkImageCache.swift apple/Core/Sources/LuminaCore/ArtworkDiskCache.swift apple/scripts/benchmark_background_artwork.swift -o /tmp/lumina-background-artwork
// Run three fresh processes per mode. --foreground-control leaves the warm cache resident.
@main struct BackgroundArtworkMemory {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = ArtworkImageCache(diskCache: nil)
        let baseline = footprint()
        var urls = [URL]()
        for i in 0..<100 {
            let url = directory.appending(path: "\(i).jpg")
            try fixture(seed: UInt32(i + 42)).write(to: url)
            urls.append(url)
            _ = await cache.image(for: url)
        }
        let loaded = footprint()
        let retainedBefore = urls.filter { cache.cachedImage(for: $0) != nil }.count
        if !CommandLine.arguments.contains("--foreground-control") { cache.setBackgrounded(true) }
        try await Task.sleep(for: .milliseconds(200))
        let trimmed = footprint()
        print("baseline_bytes=\(baseline) loaded_bytes=\(loaded) background_bytes=\(trimmed) cached_before=\(retainedBefore) cached_after=\(urls.filter { cache.cachedImage(for: $0) != nil }.count)")
    }
    static func fixture(seed: UInt32) -> Data {
        let context = CGContext(data: nil, width: 400, height: 600, bitsPerComponent: 8, bytesPerRow: 1600,
                                space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue)!
        let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
        var seed = seed
        for index in 0..<(400 * 600 * 4) {
            seed = seed &* 1664525 &+ 1013904223
            pixels[index] = UInt8(truncatingIfNeeded: seed >> 24)
        }
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, context.makeImage()!, nil)
        CGImageDestinationFinalize(destination)
        return data as Data
    }
    static func footprint() -> UInt64 {
        var info = rusage_info_v4()
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { proc_pid_rusage(getpid(), RUSAGE_INFO_V4, $0) }
        }
        return result == 0 ? info.ri_phys_footprint : 0
    }
}
