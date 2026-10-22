import Foundation
import CoreGraphics
import ImageIO
import LuminaCore

let width = 6000, height = 4000
let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
for row in stride(from: 0, to: height, by: 32) {
    context.setFillColor(CGColor(red: CGFloat(row % 255) / 255, green: 0.4, blue: 0.7, alpha: 1))
    context.fill(CGRect(x: 0, y: row, width: width, height: 32))
}
let encoded = NSMutableData()
let destination = CGImageDestinationCreateWithData(encoded, "public.jpeg" as CFString, 1, nil)!
CGImageDestinationAddImage(destination, context.makeImage()!, nil)
precondition(CGImageDestinationFinalize(destination))
let data = encoded as Data
var before = [Double](), after = [Double]()
var beforeBytes = 0, afterBytes = 0
func sample(bounded: Bool) -> (Double, Int) {
    autoreleasepool {
        let start = ContinuousClock.now
        let image: CGImage
        if bounded {
            image = BookPagePreparation.image(from: data, maximumPixelSize: 3072)!
        } else {
            let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary)!
            image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary)!
        }
        let pixels = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                               bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                               bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
        pixels.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        precondition(pixels.data!.assumingMemoryBound(to: UInt8.self)[3] == 255)
        let elapsed = start.duration(to: .now)
        let ms = Double(elapsed.components.seconds) * 1000 + Double(elapsed.components.attoseconds) / 1e15
        precondition(image.width > 0 && image.height > 0)
        return (ms, pixels.bytesPerRow * pixels.height)
    }
}
for index in 0..<21 {
    for bounded in index.isMultiple(of: 2) ? [false, true] : [true, false] {
        let (ms, bytes) = sample(bounded: bounded)
        if bounded { after.append(ms); afterBytes = bytes } else { before.append(ms); beforeBytes = bytes }
    }
}
let result: [String: Any] = ["source_width": width, "source_height": height, "encoded_bytes": data.count,
                            "timing_method": "Prepare, draw into an owned RGBA CGContext, and consume an alpha byte",
                            "full_decode_ms": before, "bounded_decode_ms": after,
                            "full_decoded_bytes": beforeBytes, "bounded_decoded_bytes": afterBytes,
                            "full_median_ms": before.sorted()[10], "bounded_median_ms": after.sorted()[10]]
print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
