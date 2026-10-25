import CoreGraphics

/// A dominant cover-art color, shaded to keep white album-page content readable.
public struct AlbumArtworkColor: Sendable {
    public let red: Double
    public let green: Double
    public let blue: Double

    public init?(image: CGImage) {
        let size = 32
        var pixels = [UInt8](repeating: 0, count: size * size * 4)
        guard let space = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: size, height: size,
                bitsPerComponent: 8, bytesPerRow: size * 4, space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.interpolationQuality = .low
            context.draw(image, in: CGRect(x: 0, y: 0, width: size, height: size))
            return true
        }
        guard drawn else { return nil }
        var bins = [Int: (count: Int, red: Double, green: Double, blue: Double)]()
        for offset in stride(from: 0, to: pixels.count, by: 4) {
            let alpha = Double(pixels[offset + 3]) / 255
            guard alpha > 0.5 else { continue }
            let r = Double(pixels[offset]) / 255 / alpha
            let g = Double(pixels[offset + 1]) / 255 / alpha
            let b = Double(pixels[offset + 2]) / 255 / alpha
            let maximum = max(r, g, b), minimum = min(r, g, b)
            // Borders, black lettering and white highlights should not drown out the cover's color.
            guard maximum > 0.08, minimum < 0.92 else { continue }
            let key = (Int(r * 15) << 8) | (Int(g * 15) << 4) | Int(b * 15)
            let bin = bins[key] ?? (0, 0, 0, 0)
            bins[key] = (bin.count + 1, bin.red + r, bin.green + g, bin.blue + b)
        }
        func score(_ bin: (count: Int, red: Double, green: Double, blue: Double)) -> Double {
            let maximum = max(bin.red, bin.green, bin.blue)
            let saturation = maximum > 0 ? (maximum - min(bin.red, bin.green, bin.blue)) / maximum : 0
            return Double(bin.count) * (0.4 + saturation)
        }
        guard let dominant = bins.max(by: { score($0.value) < score($1.value) })?.value else {
            red = 0.10; green = 0.10; blue = 0.10
            return
        }
        let maximum = max(dominant.red, dominant.green, dominant.blue)
        // Capping every sRGB channel at 0.28 preserves at least 9:1 white-text contrast.
        let scale = min(1 / Double(dominant.count), 0.28 / maximum)
        red = dominant.red * scale
        green = dominant.green * scale
        blue = dominant.blue * scale
    }
}
