import AVFoundation
import CoreMedia
import Foundation

/// A bounded, portable subtitle presentation preference for AVFoundation's WebVTT renderer.
///
/// `isCustom == false` deliberately produces no rule, allowing the user's system caption
/// appearance preferences and authored subtitle markup to take precedence.
public struct VideoSubtitleStyle: Codable, Equatable, Sendable {
    public enum FontFamily: String, Codable, CaseIterable, Identifiable, Sendable {
        case system
        case sansSerif
        case serif
        case monospace

        public var id: Self { self }
        public var label: String {
            switch self {
            case .system: "System"
            case .sansSerif: "Sans Serif"
            case .serif: "Serif"
            case .monospace: "Monospace"
            }
        }

        fileprivate var markupValue: String {
            switch self {
            case .system: kCMTextMarkupGenericFontName_Default as String
            case .sansSerif: kCMTextMarkupGenericFontName_SansSerif as String
            case .serif: kCMTextMarkupGenericFontName_Serif as String
            case .monospace: kCMTextMarkupGenericFontName_Monospace as String
            }
        }
    }

    public enum Size: String, Codable, CaseIterable, Identifiable, Sendable {
        case small
        case medium
        case large
        case extraLarge

        public var id: Self { self }
        public var label: String {
            switch self {
            case .small: "Small"
            case .medium: "Medium"
            case .large: "Large"
            case .extraLarge: "Extra Large"
            }
        }

        fileprivate var relativePercentage: Int {
            switch self {
            case .small: 80
            case .medium: 100
            case .large: 120
            case .extraLarge: 140
            }
        }
    }

    public enum Color: String, Codable, CaseIterable, Identifiable, Sendable {
        case white
        case yellow
        case cyan
        case green

        public var id: Self { self }
        public var label: String { rawValue.capitalized }

        fileprivate var rgb: (Double, Double, Double) {
            switch self {
            case .white: (1, 1, 1)
            case .yellow: (1, 0.92, 0)
            case .cyan: (0, 0.88, 1)
            case .green: (0.33, 1, 0.42)
            }
        }
    }

    public enum Weight: String, Codable, CaseIterable, Identifiable, Sendable {
        case regular
        case bold

        public var id: Self { self }
        public var label: String { rawValue.capitalized }
    }

    public enum EdgeStyle: String, Codable, CaseIterable, Identifiable, Sendable {
        case none
        case bordered
        case dropShadow
        case raised
        case depressed

        public var id: Self { self }
        public var label: String {
            switch self {
            case .none: "None"
            case .bordered: "Bordered"
            case .dropShadow: "Drop Shadow"
            case .raised: "Raised"
            case .depressed: "Depressed"
            }
        }

        fileprivate var markupValue: String {
            switch self {
            case .none: kCMTextMarkupCharacterEdgeStyle_None as String
            case .bordered: kCMTextMarkupCharacterEdgeStyle_Uniform as String
            case .dropShadow: kCMTextMarkupCharacterEdgeStyle_DropShadow as String
            case .raised: kCMTextMarkupCharacterEdgeStyle_Raised as String
            case .depressed: kCMTextMarkupCharacterEdgeStyle_Depressed as String
            }
        }
    }

    public enum Background: String, Codable, CaseIterable, Identifiable, Sendable {
        case off
        case translucent
        case opaque

        public var id: Self { self }
        public var label: String { rawValue.capitalized }

        fileprivate var alpha: Double {
            switch self {
            case .off: 0
            case .translucent: 0.6
            case .opaque: 1
            }
        }
    }

    public enum Opacity: String, Codable, CaseIterable, Identifiable, Sendable {
        case opaque
        case translucent
        case faint

        public var id: Self { self }
        public var label: String { rawValue.capitalized }

        fileprivate var alpha: Double {
            switch self {
            case .opaque: 1
            case .translucent: 0.75
            case .faint: 0.5
            }
        }
    }

    public enum VerticalAlignment: String, Codable, CaseIterable, Identifiable, Sendable {
        case top
        case center
        case bottom

        public var id: Self { self }
        public var label: String { rawValue.capitalized }

        /// The line position is a percentage from the top for horizontal subtitle writing.
        fileprivate var linePositionPercentage: Int {
            switch self {
            case .top: 12
            case .center: 50
            case .bottom: 88
            }
        }
    }

    public var isCustom: Bool
    public var font: FontFamily
    public var size: Size
    public var color: Color
    public var weight: Weight
    public var edgeStyle: EdgeStyle
    public var background: Background
    public var opacity: Opacity
    public var verticalAlignment: VerticalAlignment

    public init(
        isCustom: Bool = false,
        font: FontFamily = .system,
        size: Size = .medium,
        color: Color = .white,
        weight: Weight = .regular,
        edgeStyle: EdgeStyle = .none,
        background: Background = .off,
        opacity: Opacity = .opaque,
        verticalAlignment: VerticalAlignment = .bottom
    ) {
        self.isCustom = isCustom
        self.font = font
        self.size = size
        self.color = color
        self.weight = weight
        self.edgeStyle = edgeStyle
        self.background = background
        self.opacity = opacity
        self.verticalAlignment = verticalAlignment
    }

    /// Use this when the app should defer entirely to the system Caption & Subtitles settings.
    public static let system = VideoSubtitleStyle()

    /// The restore point for Lumina's explicit subtitle customization controls.
    public static let defaultCustom = VideoSubtitleStyle(isCustom: true)

    /// Decodes a value stored by `encodedString`, falling back to the system appearance when
    /// a previous app version or a corrupt preference contains an unsupported value.
    public init(encodedString: String?) {
        guard let encodedString,
              let data = encodedString.data(using: .utf8),
              let decoded = try? JSONDecoder().decode(Self.self, from: data)
        else {
            self = .system
            return
        }
        self = decoded
    }

    /// A UserDefaults-safe representation for `@AppStorage`.
    public var encodedString: String {
        guard let data = try? JSONEncoder().encode(self),
              let string = String(data: data, encoding: .utf8)
        else { return "" }
        return string
    }

    public mutating func restoreDefaults() {
        self = .defaultCustom
    }

    /// Returns `nil` for system styling, otherwise a rule that AVPlayerItem can assign to
    /// `textStyleRules`. AVFoundation applies these fallback rules to WebVTT subtitle tracks;
    /// authored markup with an equivalent property still takes precedence.
    public func makeTextStyleRules() -> [AVTextStyleRule]? {
        guard isCustom else { return nil }
        let (red, green, blue) = color.rgb
        let attributes: [String: Any] = [
            kCMTextMarkupAttribute_GenericFontFamilyName as String: font.markupValue,
            kCMTextMarkupAttribute_RelativeFontSize as String: NSNumber(value: size.relativePercentage),
            kCMTextMarkupAttribute_ForegroundColorARGB as String: [
                NSNumber(value: opacity.alpha), NSNumber(value: red), NSNumber(value: green), NSNumber(value: blue),
            ],
            kCMTextMarkupAttribute_BackgroundColorARGB as String: [
                NSNumber(value: background.alpha), NSNumber(value: 0), NSNumber(value: 0), NSNumber(value: 0),
            ],
            kCMTextMarkupAttribute_BoldStyle as String: NSNumber(value: weight == .bold),
            kCMTextMarkupAttribute_CharacterEdgeStyle as String: edgeStyle.markupValue,
            kCMTextMarkupAttribute_OrthogonalLinePositionPercentageRelativeToWritingDirection as String:
                NSNumber(value: verticalAlignment.linePositionPercentage),
        ]
        guard let rule = AVTextStyleRule(textMarkupAttributes: attributes) else { return nil }
        return [rule]
    }
}
