import CoreGraphics
import Foundation

/// The server's source-keyed face/subject proposals, shared with the web heroes.
public struct HeroFraming: Decodable, Sendable {
    private struct CropPlan {
        let crop: CGRect
        let safeBounds: CGRect?
        let focus: CGPoint
        let preferredX: Double
    }

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
        cropPlan(framing, source: source, viewport: viewport, home: home).crop
    }

    /// Normalized full-bleed crop for a subtle, subject-safe camera move.
    public static func motionCrop(
        _ framing: HeroFraming?,
        source: CGSize,
        viewport: CGSize,
        home: Bool = true,
        progress: Double,
        direction: Int
    ) -> CGRect {
        let plan = cropPlan(framing, source: source, viewport: viewport, home: home)
        let staticCrop = plan.crop
        let progress = progress.isFinite ? min(1, max(0, progress)) : 0
        guard progress > 0,
              source.width.isFinite, source.height.isFinite, viewport.width.isFinite, viewport.height.isFinite,
              source.width > 0, source.height > 0, viewport.width > 0, viewport.height > 0 else {
            return staticCrop
        }

        func ranges(width: Double, height: Double, safeBounds: CGRect) -> (x: ClosedRange<Double>, y: ClosedRange<Double>)? {
            let xLow = max(0, safeBounds.maxX - width)
            let xHigh = min(1 - width, safeBounds.minX)
            let yLow = max(0, safeBounds.maxY - height * 0.9)
            let yHigh = min(1 - height, safeBounds.minY)
            guard xLow <= xHigh, yLow <= yHigh else { return nil }
            return (xLow...xHigh, yLow...yHigh)
        }

        let maximumScale: Double
        if let safeBounds = plan.safeBounds {
            // If the static crop already has to compromise on the padded material bounds,
            // moving farther in can only make that compromise worse.
            guard ranges(width: staticCrop.width, height: staticCrop.height, safeBounds: safeBounds) != nil else {
                return staticCrop
            }
            maximumScale = min(
                1.06,
                staticCrop.width / safeBounds.width,
                staticCrop.height * 0.9 / safeBounds.height
            )
        } else {
            maximumScale = 1.06
        }

        guard maximumScale > 1.000_001 else { return staticCrop }
        let scale = 1 + (maximumScale - 1) * progress
        let width = staticCrop.width / scale
        let height = staticCrop.height / scale
        let normalizedDirection = ((direction % 4) + 4) % 4
        let xSign: Double = normalizedDirection == 0 || normalizedDirection == 3 ? -1 : 1
        let ySign: Double = normalizedDirection < 2 ? -1 : 1
        let xDrift = min(0.015, staticCrop.width * 0.025) * progress * xSign
        let yDrift = min(0.015, staticCrop.height * 0.025) * progress * ySign
        func clamp(_ value: Double, _ range: ClosedRange<Double>) -> Double {
            min(range.upperBound, max(range.lowerBound, value))
        }

        let x: Double
        let y: Double
        if let safeBounds = plan.safeBounds,
           let placement = ranges(width: width, height: height, safeBounds: safeBounds) {
            x = clamp(plan.focus.x - width * plan.preferredX + xDrift, placement.x)
            y = clamp(plan.focus.y - height * 0.42 + yDrift, placement.y)
        } else {
            x = clamp(staticCrop.midX - width * 0.5 + xDrift, 0...(1 - width))
            y = clamp(staticCrop.midY - height * 0.5 + yDrift, 0...(1 - height))
        }
        return CGRect(x: x, y: y, width: width, height: height)
    }

    private static func cropPlan(_ framing: HeroFraming?, source: CGSize, viewport: CGSize, home: Bool) -> CropPlan {
        guard source.width.isFinite, source.height.isFinite, viewport.width.isFinite, viewport.height.isFinite,
              source.width > 0, source.height > 0, viewport.width > 0, viewport.height > 0 else {
            return CropPlan(crop: CGRect(x: 0, y: 0, width: 1, height: 1), safeBounds: nil, focus: CGPoint(x: 0.5, y: 0.5), preferredX: 0.5)
        }
        let cropWidth = min(1, (viewport.width / viewport.height) / (source.width / source.height))
        let cropHeight = min(1, (source.width / source.height) / (viewport.width / viewport.height))
        let fallback = CGRect(x: (1 - cropWidth) * 0.5, y: (1 - cropHeight) * 0.15, width: cropWidth, height: cropHeight)
        guard let regions = framing?.validRegions else {
            return CropPlan(crop: fallback, safeBounds: nil, focus: CGPoint(x: fallback.midX, y: fallback.midY), preferredX: 0.5)
        }
        let faces = regions.filter { $0.kind == "face" }
        let candidates = faces.isEmpty ? regions : faces
        guard let strongest = candidates.map(\.weight).max() else {
            return CropPlan(crop: fallback, safeBounds: nil, focus: CGPoint(x: fallback.midX, y: fallback.midY), preferredX: 0.5)
        }
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
        return CropPlan(
            crop: CGRect(x: x, y: y, width: cropWidth, height: cropHeight),
            safeBounds: CGRect(x: left, y: top, width: right - left, height: bottom - top),
            focus: CGPoint(x: focusX, y: focusY),
            preferredX: preferredX
        )
    }
}
