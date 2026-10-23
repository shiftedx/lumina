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

    private func decode(_ json: String) throws -> HeroFraming {
        try JSONDecoder().decode(HeroFraming.self, from: Data(json.utf8))
    }

    private func assertFullBleed(_ crop: CGRect, viewport: CGSize, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertGreaterThanOrEqual(crop.minX, 0, file: file, line: line)
        XCTAssertGreaterThanOrEqual(crop.minY, 0, file: file, line: line)
        XCTAssertLessThanOrEqual(crop.maxX, 1, file: file, line: line)
        XCTAssertLessThanOrEqual(crop.maxY, 1, file: file, line: line)
        XCTAssertEqual(source.width * crop.width / (source.height * crop.height), viewport.width / viewport.height, accuracy: 0.0001, file: file, line: line)
    }
}
