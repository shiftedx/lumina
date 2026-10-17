import SwiftUI
import UIKit

/// Lumina's small shared palette. The dynamic providers keep the native clients
/// aligned with the web paper/ink tokens while respecting the system appearance.
public enum LuminaTheme {
    public static let paper = dynamicColor(
        dark: (15, 14, 12),
        light: (244, 241, 234)
    )

    public static let ink = dynamicColor(
        dark: (239, 233, 221),
        light: (21, 19, 15)
    )

    public static let muted = dynamicColor(
        dark: (157, 150, 138),
        light: (97, 92, 83)
    )

    public static let gold = dynamicColor(
        dark: (229, 160, 13),
        light: (138, 90, 0)
    )

    private static func dynamicColor(
        dark: (red: Int, green: Int, blue: Int),
        light: (red: Int, green: Int, blue: Int)
    ) -> Color {
        Color(uiColor: UIColor { traits in
            let rgb = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(
                red: CGFloat(rgb.red) / 255,
                green: CGFloat(rgb.green) / 255,
                blue: CGFloat(rgb.blue) / 255,
                alpha: 1
            )
        })
    }
}
