import CoreGraphics
import Foundation

/// The server's source-keyed face/subject proposals, shared with the web heroes.
public struct HeroFraming: Decodable, Sendable {
    public struct Region: Decodable, Sendable {
        public let x: Double
        public let y: Double
        public let width: Double
        public let height: Double
        public let kind: String
        public let weight: Double
    }

    public let state: String
    public let width: Int?
    public let height: Int?
    public let regions: [Region]?

    private var validRegions: [Region]? {
        guard state == "ready", let width, let height,
              (1...16_384).contains(width), (1...16_384).contains(height), width * height <= 40_000_000,
              let regions, regions.count <= 16,
              regions.allSatisfy({ region in
                  [region.x, region.y, region.width, region.height, region.weight].allSatisfy(\.isFinite)
                      && ["face", "subject"].contains(region.kind)
                      && region.x >= 0 && region.y >= 0 && region.width > 0 && region.height > 0
                      && region.x + region.width <= 1 && region.y + region.height <= 1
                      && (0...1).contains(region.weight)
              }) else { return nil }
        return regions
    }

    /// Normalized full-bleed crop. Mirrors HeroFrame's static geometry without its camera animation.
    public static func crop(_ framing: HeroFraming?, source: CGSize, viewport: CGSize, home: Bool = false) -> CGRect {
        guard source.width.isFinite, source.height.isFinite, viewport.width.isFinite, viewport.height.isFinite,
              source.width > 0, source.height > 0, viewport.width > 0, viewport.height > 0 else {
            return CGRect(x: 0, y: 0, width: 1, height: 1)
        }
        let cropWidth = min(1, (viewport.width / viewport.height) / (source.width / source.height))
        let cropHeight = min(1, (source.width / source.height) / (viewport.width / viewport.height))
        let fallback = CGRect(x: (1 - cropWidth) * 0.5, y: (1 - cropHeight) * 0.15, width: cropWidth, height: cropHeight)
        guard let regions = framing?.validRegions else { return fallback }
        let faces = regions.filter { $0.kind == "face" }
        let candidates = faces.isEmpty ? regions : faces
        guard let strongest = candidates.map(\.weight).max() else { return fallback }
        let material = candidates.filter { $0.weight >= strongest * 0.55 }
        func clamp(_ value: Double, _ low: Double = 0, _ high: Double = 1) -> Double { min(high, max(low, value)) }
        let left = material.map { clamp($0.x - $0.width * 0.1) }.min()!
        let right = material.map { clamp($0.x + $0.width * 1.1) }.max()!
        let top = material.map { clamp($0.y - $0.height * (faces.isEmpty ? 0.07 : 0.45)) }.min()!
        let bottom = material.map { clamp($0.y + $0.height * (faces.isEmpty ? 1.03 : 1.2)) }.max()!
        let weight = material.reduce(0) { $0 + $1.weight * $1.width * $1.height }
        let focusX = weight > 0 ? material.reduce(0) { $0 + ($1.x + $1.width / 2) * $1.weight * $1.width * $1.height } / weight : 0.5
        let focusY = weight > 0 ? material.reduce(0) { $0 + ($1.y + $1.height / 2) * $1.weight * $1.width * $1.height } / weight : 0.5
        let preferredX = home && viewport.width >= 600 ? 0.68 : 0.5
        let xLow = max(0, right - cropWidth), xHigh = min(1 - cropWidth, left)
        let x = xLow <= xHigh ? clamp(focusX - cropWidth * preferredX, xLow, xHigh) : clamp(focusX - cropWidth * preferredX, 0, 1 - cropWidth)
        let yLow = max(0, bottom - cropHeight * 0.9), yHigh = min(1 - cropHeight, top)
        let y = yLow <= yHigh ? clamp(focusY - cropHeight * 0.42, yLow, yHigh) : clamp(top, 0, 1 - cropHeight)
        return CGRect(x: x, y: y, width: cropWidth, height: cropHeight)
    }
}
