import SwiftUI
import UIKit

/// Neutral theater surfaces with Lumina's warm accent and adaptive light appearance.
public enum LuminaTheme {
    /// The same licensed Newsreader face used by the web gallery, scaled with native text settings.
    public static func editorial(_ size: CGFloat, relativeTo style: Font.TextStyle = .title2, italic: Bool = false) -> Font {
        .custom(italic ? "Newsreader-16ptItalic" : "Newsreader-16ptRegular", size: size, relativeTo: style)
    }

    public static let theater = Color(red: 11 / 255, green: 11 / 255, blue: 13 / 255)
    /// Approved sunrise foreground shared with media-mark.svg and the app icon (#DBB688).
    public static let brand = Color(red: 219 / 255, green: 182 / 255, blue: 136 / 255)
    public static var paper: Color {
        if requestedAppearance == "oled" { return .black }
        return standardPaper
    }

    private static var requestedAppearance: String {
        let deviceOverride = UserDefaults.standard.string(forKey: "native.appearance")
        if let deviceOverride, deviceOverride != "account" { return deviceOverride }
        return UserDefaults.standard.string(forKey: "lumina.appearance") ?? "system"
    }
    private static let standardPaper = dynamicColor(
        dark: (11, 11, 13),
        light: (244, 241, 234),
        highContrastDark: (0, 0, 0),
        highContrastLight: (255, 255, 255)
    )

    public static let ink = dynamicColor(
        dark: (245, 245, 247),
        light: (21, 19, 15),
        highContrastDark: (255, 255, 255),
        highContrastLight: (0, 0, 0)
    )

    public static let muted = dynamicColor(
        dark: (171, 171, 179),
        light: (97, 92, 83),
        highContrastDark: (202, 202, 210),
        highContrastLight: (65, 60, 52)
    )

    /// Neutral primary actions match the web gallery's ink/paper controls.
    public static let primaryControlFill = dynamicColor(
        dark: (239, 233, 221),
        light: (21, 19, 15),
        highContrastDark: (255, 255, 255),
        highContrastLight: (0, 0, 0)
    )

    public static let onPrimaryControl = dynamicColor(
        dark: (15, 14, 12),
        light: (244, 241, 234),
        highContrastDark: (0, 0, 0),
        highContrastLight: (255, 255, 255)
    )

    /// Brand-colored text on dark surfaces; neutral ink preserves light/contrast readability.
    public static let accentInk = dynamicColor(
        dark: (219, 182, 136),
        light: (21, 19, 15),
        highContrastDark: (255, 255, 255),
        highContrastLight: (0, 0, 0)
    )

    private static func dynamicColor(
        dark: (red: Int, green: Int, blue: Int),
        light: (red: Int, green: Int, blue: Int),
        highContrastDark: (red: Int, green: Int, blue: Int),
        highContrastLight: (red: Int, green: Int, blue: Int)
    ) -> Color {
        Color(uiColor: UIColor { traits in
            let rgb: (red: Int, green: Int, blue: Int)
            switch (traits.userInterfaceStyle, traits.accessibilityContrast) {
            case (.dark, .high):
                rgb = highContrastDark
            case (_, .high):
                rgb = highContrastLight
            case (.dark, _):
                rgb = dark
            default:
                rgb = light
            }
            return UIColor(
                red: CGFloat(rgb.red) / 255,
                green: CGFloat(rgb.green) / 255,
                blue: CGFloat(rgb.blue) / 255,
                alpha: 1
            )
        })
    }
}

/// The web wordmark's original flat sunrise, preserving its five detached rays.
struct LuminaBrandMark: View {
    var body: some View {
        GeometryReader { geometry in
            SunriseShape().stroke(LuminaTheme.brand,
                style: StrokeStyle(lineWidth: geometry.size.width * 3 / 56, lineCap: .round, lineJoin: .round))
        }
        .aspectRatio(56 / 34, contentMode: .fit)
        .accessibilityHidden(true)
    }

    private struct SunriseShape: Shape {
        func path(in rect: CGRect) -> Path {
            var path = Path()
            for (start, end) in [
                (CGPoint(x: 10, y: 40), CGPoint(x: 54, y: 40)),
                (CGPoint(x: 32, y: 12), CGPoint(x: 32, y: 21)),
                (CGPoint(x: 18, y: 15.751), CGPoint(x: 22.5, y: 23.546)),
                (CGPoint(x: 46, y: 15.751), CGPoint(x: 41.5, y: 23.546)),
                (CGPoint(x: 7.751, y: 26), CGPoint(x: 15.546, y: 30.5)),
                (CGPoint(x: 56.249, y: 26), CGPoint(x: 48.454, y: 30.5)),
            ] {
                path.move(to: start); path.addLine(to: end)
            }
            path.move(to: CGPoint(x: 21, y: 40))
            path.addCurve(to: CGPoint(x: 32, y: 29), control1: CGPoint(x: 21, y: 33.925), control2: CGPoint(x: 25.925, y: 29))
            path.addCurve(to: CGPoint(x: 43, y: 40), control1: CGPoint(x: 38.075, y: 29), control2: CGPoint(x: 43, y: 33.925))
            return path.applying(CGAffineTransform(translationX: -4, y: -9)
                .concatenating(CGAffineTransform(scaleX: rect.width / 56, y: rect.height / 34)))
        }
    }
}

private struct LuminaBrandMarkBaseline: ViewModifier {
    func body(content: Content) -> some View {
        content.alignmentGuide(.firstTextBaseline) { dimensions in
            // The mark fits a 56 × 34 canvas. Its lowest painted point is the
            // rounded horizon stroke at y 31, not the canvas edge. Align that
            // painted edge to the all-caps wordmark baseline (the bottom of L).
            let fittedWidth = min(dimensions.width, dimensions.height * 56 / 34)
            let fittedHeight = fittedWidth * 34 / 56
            let verticalInset = (dimensions.height - fittedHeight) / 2
            return verticalInset + fittedHeight * 31 / 34 + fittedWidth * 1.5 / 56
        }
    }
}

extension View {
    /// Aligns the sunrise mark's painted horizon to an all-caps Lumina wordmark.
    func luminaBrandMarkBaseline() -> some View {
        modifier(LuminaBrandMarkBaseline())
    }
}
