import CoreGraphics
import Foundation
import ImageIO

/// Decodes billboard artwork within the native rendition pixel budget.
/// The source image stays out of ImageIO's decoded-image cache.
public enum BillboardArtworkPreparation {
    public static let maximumEncodedBytes = 12 * 1024 * 1024
    public static let maximumPixelSize = 1_920

    public static func image(from data: Data) -> CGImage? {
        guard data.count <= maximumEncodedBytes,
              let source = CGImageSourceCreateWithData(data as CFData, [
                kCGImageSourceShouldCache: false,
              ] as CFDictionary)
        else { return nil }

        return CGImageSourceCreateThumbnailAtIndex(source, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maximumPixelSize,
            kCGImageSourceShouldCacheImmediately: true,
        ] as CFDictionary)
    }
}
