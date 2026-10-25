import CoreGraphics
import XCTest
@testable import LuminaCore

final class AlbumArtworkColorTests: XCTestCase {
    func testDominantCoverColorIgnoresWhiteBorderAndRetainsReadableShade() throws {
        let space = try XCTUnwrap(CGColorSpace(name: CGColorSpace.sRGB))
        let context = try XCTUnwrap(CGContext(data: nil, width: 100, height: 100, bitsPerComponent: 8,
            bytesPerRow: 400, space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        context.setFillColor(red: 0.8, green: 0.1, blue: 0.3, alpha: 1)
        context.fill(CGRect(x: 10, y: 10, width: 80, height: 80))
        context.setFillColor(red: 0.1, green: 0.2, blue: 0.8, alpha: 1)
        context.fill(CGRect(x: 20, y: 20, width: 10, height: 10))
        let palette = try XCTUnwrap(AlbumArtworkColor(image: XCTUnwrap(context.makeImage())))
        XCTAssertGreaterThan(palette.red, palette.blue * 2)
        XCTAssertGreaterThan(palette.blue, palette.green)
        XCTAssertLessThanOrEqual(max(palette.red, palette.green, palette.blue), 0.28)
        // Switching covers must select that cover's color rather than retain the preceding album.
        context.setFillColor(red: 0.05, green: 0.65, blue: 0.1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        let green = try XCTUnwrap(AlbumArtworkColor(image: XCTUnwrap(context.makeImage())))
        XCTAssertGreaterThan(green.green, green.red * 3)
    }
}
