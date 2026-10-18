import SwiftUI
import UIKit

/// Lumina's small shared palette. The dynamic providers keep the native clients
/// aligned with the web paper/ink tokens while respecting the system appearance.
public enum LuminaTheme {
    public static let paper = dynamicColor(
        dark: (15, 14, 12),
        light: (244, 241, 234),
        highContrastDark: (0, 0, 0),
        highContrastLight: (255, 255, 255)
    )

    public static let ink = dynamicColor(
        dark: (239, 233, 221),
        light: (21, 19, 15),
        highContrastDark: (255, 255, 255),
        highContrastLight: (0, 0, 0)
    )

    public static let muted = dynamicColor(
        dark: (157, 150, 138),
        light: (97, 92, 83),
        highContrastDark: (190, 184, 172),
        highContrastLight: (65, 60, 52)
    )

    public static let gold = dynamicColor(
        dark: (229, 160, 13),
        light: (138, 90, 0),
        highContrastDark: (255, 184, 32),
        highContrastLight: (112, 70, 0)
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
