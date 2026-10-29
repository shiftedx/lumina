import CoreGraphics
import Foundation
import ImageIO

/// Decodes billboard artwork within the native rendition pixel budget.
/// The source image stays out of ImageIO's decoded-image cache.
public enum BillboardArtworkPreparation {
    public static let maximumEncodedBytes = 12 * 1024 * 1024
    public static let maximumPixelSize = 1_920

    public static func image(from data: Data) -> CGImage? {
        image(from: data, maximumWidth: nil)
    }

    static func image(from data: Data, maximumWidth: Int?) -> CGImage? {
        guard data.count <= maximumEncodedBytes,
              let source = CGImageSourceCreateWithData(data as CFData, [
                kCGImageSourceShouldCache: false,
              ] as CFDictionary)
        else { return nil }

        var pixelBudget = maximumPixelSize
        if let maximumWidth, maximumWidth > 0,
           let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
           let rawWidth = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
           let rawHeight = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue,
           rawWidth.isFinite, rawHeight.isFinite, rawWidth > 0, rawHeight > 0 {
            let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
            let rotated = (5...8).contains(orientation)
            let width = rotated ? rawHeight : rawWidth
            let height = rotated ? rawWidth : rawHeight
            let requested = Double(min(maximumWidth, maximumPixelSize)) * max(1, height / width)
            pixelBudget = max(1, Int(min(Double(maximumPixelSize), ceil(requested))))
        }
        return CGImageSourceCreateThumbnailAtIndex(source, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: pixelBudget,
            kCGImageSourceShouldCacheImmediately: true,
        ] as CFDictionary)
    }
}
