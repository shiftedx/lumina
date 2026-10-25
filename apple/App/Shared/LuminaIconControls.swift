import SwiftUI
import UIKit

/// Keep control labels in SwiftUI's native `Text`/`Image` shape. In particular,
/// `Menu` can bridge this form into a UIKit `UIMenu`, while arbitrary icon views
/// are treated as menu content rather than the action's image.
/// The image retains its action name so icon-only labels remain accessible.
typealias LuminaControlLabel = Label<Text, Image>

#if os(tvOS)
private let defaultLuminaControlIconSize: CGFloat = 24
#else
private let defaultLuminaControlIconSize: CGFloat = 20
#endif

@MainActor
private enum LuminaControlIconImages {
    static let cache: NSCache<NSString, UIImage> = {
        let cache = NSCache<NSString, UIImage>()
        cache.countLimit = 192
        cache.totalCostLimit = 8 * 1_024 * 1_024
        return cache
    }()

    static func image(glyph: LuminaGlyph, variant: LuminaIconVariant, size: CGFloat, label: Text) -> Image {
        let asset = glyph.assetName(for: variant)
        guard size.isFinite, size > 0,
              let source = approvedUIImage(named: asset) else {
            return Image(asset, label: label).renderingMode(.template)
        }

        let format = UIGraphicsImageRendererFormat()
        format.opaque = false
        let scale = format.scale
        let key = "\(asset)|\(size)|\(scale)" as NSString
        let rendered: UIImage
        if let cached = cache.object(forKey: key) {
            rendered = cached
        } else {
            rendered = UIGraphicsImageRenderer(size: CGSize(width: size, height: size), format: format).image { _ in
                source.draw(in: CGRect(x: 0, y: 0, width: size, height: size))
            }
            let pixels = Int(rendered.size.width * rendered.scale * rendered.size.height * rendered.scale)
            cache.setObject(rendered, forKey: key, cost: min(8 * 1_024 * 1_024, max(1, pixels * 4)))
        }

        guard let cgImage = rendered.cgImage else {
            return Image(asset, label: label).renderingMode(.template)
        }
        return Image(cgImage, scale: rendered.scale, label: label).renderingMode(.template)
    }

    /// This file is the sole adapter from an approved catalog SVG to a UIKit image.
    /// It intentionally cannot resolve arbitrary file or network URLs.
    private static func approvedUIImage(named asset: String) -> UIImage? {
        UIImage(named: asset)?.withRenderingMode(.alwaysTemplate)
    }
}

@MainActor
extension Label where Title == Text, Icon == Image {
    init(_ titleKey: LocalizedStringKey, glyph: LuminaGlyph,
         variant: LuminaIconVariant = .outline, size: CGFloat = defaultLuminaControlIconSize) {
        self.init {
            Text(titleKey)
        } icon: {
            LuminaControlIconImages.image(glyph: glyph, variant: variant, size: size, label: Text(titleKey))
        }
    }

    init<S>(_ title: S, glyph: LuminaGlyph,
            variant: LuminaIconVariant = .outline, size: CGFloat = defaultLuminaControlIconSize) where S: StringProtocol {
        self.init {
            Text(title)
        } icon: {
            LuminaControlIconImages.image(glyph: glyph, variant: variant, size: size, label: Text(title))
        }
    }
}

@MainActor
extension Button where Label == LuminaControlLabel {
    init(_ titleKey: LocalizedStringKey, glyph: LuminaGlyph,
         variant: LuminaIconVariant = .outline, size: CGFloat = defaultLuminaControlIconSize,
         role: ButtonRole? = nil, action: @escaping () -> Void) {
        self.init(role: role, action: action) {
            Label(titleKey, glyph: glyph, variant: variant, size: size)
        }
    }

    init<S>(_ title: S, glyph: LuminaGlyph,
            variant: LuminaIconVariant = .outline, size: CGFloat = defaultLuminaControlIconSize,
            role: ButtonRole? = nil, action: @escaping () -> Void) where S: StringProtocol {
        self.init(role: role, action: action) {
            Label(title, glyph: glyph, variant: variant, size: size)
        }
    }
}
