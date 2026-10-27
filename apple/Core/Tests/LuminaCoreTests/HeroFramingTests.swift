import CoreGraphics
import Foundation
import XCTest
@testable import LuminaCore

final class HeroFramingTests: XCTestCase {
    private let source = CGSize(width: 1920, height: 1080)

    func testPhoneCropKeepsOffCenterFaceAndHeadroomInsteadOfEmptyCenter() throws {
        let framing = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.8,"y":0.15,"width":0.1,"height":0.18,"weight":0.9}]}"#)
        let viewport = CGSize(width: 390, height: 500)
        let fallback = HeroFraming.crop(nil, source: source, viewport: viewport)
        let crop = HeroFraming.crop(framing, source: source, viewport: viewport)
        XCTAssertLessThan(fallback.maxX, 0.8)
        XCTAssertLessThanOrEqual(crop.minX, 0.79)
        XCTAssertGreaterThanOrEqual(crop.maxX, 0.91)
        XCTAssertLessThanOrEqual(crop.minY, 0.069)
        assertFullBleed(crop, viewport: viewport)
    }

    func testTVWideCropBringsLowSubjectAboveTextAndPrioritizesFaces() throws {
        let viewport = CGSize(width: 1920, height: 760)
        let lowSubject = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"subject","x":0.39,"y":0.64,"width":0.18,"height":0.28,"weight":0.42}]}"#)
        let crop = HeroFraming.crop(lowSubject, source: source, viewport: viewport, home: true)
        XCTAssertGreaterThan(crop.minY, 0.24)
        XCTAssertGreaterThanOrEqual(crop.minY + crop.height * 0.9, 0.9284 - 0.00001)
        assertFullBleed(crop, viewport: viewport)
        let faceAndSubject = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.4,"y":0.15,"width":0.12,"height":0.18,"weight":0.9},{"kind":"subject","x":0.4,"y":0.8,"width":0.1,"height":0.2,"weight":1}]}"#)
        let faceCrop = HeroFraming.crop(faceAndSubject, source: source, viewport: viewport)
        XCTAssertLessThanOrEqual(faceCrop.minY, 0.069)
        XCTAssertGreaterThanOrEqual(faceCrop.maxY, 0.366)
    }

    func testUntrustedOrUnavailableAnalysisUsesConservativeCrop() throws {
        let viewport = CGSize(width: 1920, height: 760)
        let fallback = HeroFraming.crop(nil, source: source, viewport: viewport)
        for payload in [
            #"{"state":"pending"}"#,
            #"{"state":"unavailable"}"#,
            #"{"state":"ready","width":1920,"height":1080,"regions":[]}"#,
            #"{"state":"ready","width":999999,"height":1080,"regions":[]}"#,
            #"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.9,"y":0.1,"width":0.4,"height":0.1,"weight":1}]}"#,
        ] {
            XCTAssertEqual(HeroFraming.crop(try decode(payload), source: source, viewport: viewport), fallback)
        }
        XCTAssertEqual(HeroFraming.crop(nil, source: .zero, viewport: viewport), CGRect(x: 0, y: 0, width: 1, height: 1))
    }

    func testDifficultPortraitGroupRetainsUpperEdgeAcrossResizing() throws {
        let source = CGSize(width: 900, height: 1600)
        let framing = try decode(#"{"state":"ready","width":900,"height":1600,"regions":[{"kind":"face","x":0.35,"y":0.1,"width":0.3,"height":0.32,"weight":1}]}"#)
        for viewport in [CGSize(width: 1920, height: 760), CGSize(width: 390, height: 500)] {
            let crop = HeroFraming.crop(framing, source: source, viewport: viewport)
            XCTAssertEqual(crop.minY, 0, accuracy: 0.0001)
            XCTAssertGreaterThan(crop.height, 0)
            XCTAssertLessThanOrEqual(crop.maxY, 1)
        }
    }

    func testMotionCropPreservesStaticStartAndPaddedMaterialAcrossAnimation() throws {
        let edgeFace = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.82,"y":0.08,"width":0.13,"height":0.18,"weight":1}]}"#)
        let offCenterGroup = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.65,"y":0.16,"width":0.1,"height":0.16,"weight":1},{"kind":"face","x":0.78,"y":0.18,"width":0.11,"height":0.17,"weight":0.8}]}"#)
        let viewports = [CGSize(width: 466, height: 678), CGSize(width: 951, height: 600)]

        for (framing, bounds) in [
            (edgeFace, CGRect(x: 0.807, y: 0, width: 0.156, height: 0.296)),
            (offCenterGroup, CGRect(x: 0.64, y: 0.088, width: 0.261, height: 0.296)),
        ] {
            for viewport in viewports {
                let staticCrop = HeroFraming.crop(framing, source: source, viewport: viewport, home: true)
                for direction in 0...3 {
                    for progress in [0.0, 0.25, 0.5, 0.75, 1.0] {
                        let crop = HeroFraming.motionCrop(
                            framing,
                            source: source,
                            viewport: viewport,
                            progress: progress,
                            direction: direction
                        )
                        if progress == 0 {
                            XCTAssertEqual(crop, staticCrop)
                        }
                        assertFullBleed(crop, source: source, viewport: viewport)
                        XCTAssertLessThanOrEqual(crop.minX, bounds.minX + 0.000_001)
                        XCTAssertGreaterThanOrEqual(crop.maxX, bounds.maxX - 0.000_001)
                        XCTAssertLessThanOrEqual(crop.minY, bounds.minY + 0.000_001)
                        XCTAssertGreaterThanOrEqual(crop.minY + crop.height * 0.9, bounds.maxY - 0.000_001)
                    }
                }
            }
        }
    }

    func testMotionCropClampsProgressAndSafelyHandlesUnavailableAnalysis() throws {
        let viewport = CGSize(width: 951, height: 600)
        let unavailable = try decode(#"{"state":"unavailable"}"#)
        let staticCrop = HeroFraming.crop(unavailable, source: source, viewport: viewport, home: true)

        for progress in [Double.nan, -.infinity, .infinity, -1] {
            XCTAssertEqual(
                HeroFraming.motionCrop(unavailable, source: source, viewport: viewport, progress: progress, direction: 0),
                staticCrop
            )
        }
        let endpoint = HeroFraming.motionCrop(unavailable, source: source, viewport: viewport, progress: 1, direction: 2)
        XCTAssertEqual(
            HeroFraming.motionCrop(unavailable, source: source, viewport: viewport, progress: 2, direction: 2),
            endpoint
        )
        XCTAssertEqual(
            HeroFraming.motionCrop(nil, source: source, viewport: viewport, progress: 1, direction: 2),
            endpoint
        )
        assertFullBleed(endpoint, source: source, viewport: viewport)
        XCTAssertEqual(
            HeroFraming.motionCrop(nil, source: .zero, viewport: viewport, progress: 1, direction: 0),
            CGRect(x: 0, y: 0, width: 1, height: 1)
        )
    }

    func testMotionCropRemainsStaticWhenPaddedGroupCannotFit() throws {
        let framing = try decode(#"{"state":"ready","width":1920,"height":1080,"regions":[{"kind":"face","x":0.05,"y":0.14,"width":0.35,"height":0.3,"weight":1},{"kind":"face","x":0.6,"y":0.12,"width":0.35,"height":0.3,"weight":1}]}"#)
        let viewport = CGSize(width: 466, height: 678)
        let staticCrop = HeroFraming.crop(framing, source: source, viewport: viewport, home: true)

        for direction in 0...3 {
            for progress in [0.25, 0.5, 0.75, 1.0] {
                XCTAssertEqual(
                    HeroFraming.motionCrop(framing, source: source, viewport: viewport, progress: progress, direction: direction),
                    staticCrop
                )
            }
        }
    }

    private func decode(_ json: String) throws -> HeroFraming {
        try JSONDecoder().decode(HeroFraming.self, from: Data(json.utf8))
    }

    private func assertFullBleed(_ crop: CGRect, source: CGSize? = nil, viewport: CGSize, file: StaticString = #filePath, line: UInt = #line) {
        let source = source ?? self.source
        XCTAssertGreaterThanOrEqual(crop.minX, 0, file: file, line: line)
        XCTAssertGreaterThanOrEqual(crop.minY, 0, file: file, line: line)
        XCTAssertLessThanOrEqual(crop.maxX, 1, file: file, line: line)
        XCTAssertLessThanOrEqual(crop.maxY, 1, file: file, line: line)
        XCTAssertEqual(source.width * crop.width / (source.height * crop.height), viewport.width / viewport.height, accuracy: 0.0001, file: file, line: line)
    }
}
