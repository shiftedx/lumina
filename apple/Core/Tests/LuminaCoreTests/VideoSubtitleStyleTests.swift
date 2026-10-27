import AVFoundation
import CoreMedia
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class VideoSubtitleStyleTests: XCTestCase {
    func testSystemStyleDefersToSystemCaptionPreferencesAndInvalidPersistenceFallsBack() {
        XCTAssertNil(VideoSubtitleStyle.system.makeTextStyleRules())
        XCTAssertEqual(VideoSubtitleStyle(encodedString: nil), .system)
        XCTAssertEqual(VideoSubtitleStyle(encodedString: "not JSON"), .system)
        XCTAssertEqual(VideoSubtitleStyle(encodedString: #"{"isCustom":true,"font":"unsupported"}"#), .system)
    }

    func testCustomStyleRoundTripsAndMapsOnlySupportedCoreMediaMarkup() throws {
        let style = VideoSubtitleStyle(
            isCustom: true,
            font: .monospace,
            size: .large,
            color: .yellow,
            weight: .bold,
            edgeStyle: .bordered,
            background: .translucent,
            opacity: .translucent,
            verticalAlignment: .center
        )

        XCTAssertEqual(VideoSubtitleStyle(encodedString: style.encodedString), style)
        let rules = try XCTUnwrap(style.makeTextStyleRules())
        XCTAssertEqual(rules.count, 1)
        let rule = try XCTUnwrap(rules.first)
        let attributes = rule.textMarkupAttributes
        XCTAssertEqual(attributes[kCMTextMarkupAttribute_GenericFontFamilyName as String] as? String,
                       kCMTextMarkupGenericFontName_Monospace as String)
        XCTAssertEqual((attributes[kCMTextMarkupAttribute_RelativeFontSize as String] as? NSNumber)?.intValue, 120)
        XCTAssertEqual((attributes[kCMTextMarkupAttribute_BoldStyle as String] as? NSNumber)?.boolValue, true)
        XCTAssertEqual(attributes[kCMTextMarkupAttribute_CharacterEdgeStyle as String] as? String,
                       kCMTextMarkupCharacterEdgeStyle_Uniform as String)
        XCTAssertEqual((attributes[kCMTextMarkupAttribute_OrthogonalLinePositionPercentageRelativeToWritingDirection as String] as? NSNumber)?.intValue, 50)
        XCTAssertEqual(numberArray(attributes[kCMTextMarkupAttribute_ForegroundColorARGB as String]), [0.75, 1, 0.92, 0])
        XCTAssertEqual(numberArray(attributes[kCMTextMarkupAttribute_BackgroundColorARGB as String]), [0.6, 0, 0, 0])
    }

    private func numberArray(_ value: Any?) -> [Double]? {
        (value as? [NSNumber])?.map(\.doubleValue)
    }
}
