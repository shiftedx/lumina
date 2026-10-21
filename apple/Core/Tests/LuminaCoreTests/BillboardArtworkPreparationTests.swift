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
