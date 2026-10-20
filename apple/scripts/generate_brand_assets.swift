// Reproduce the approved flat sunrise, with five detached rays on charcoal.
// Run from apple/: swift scripts/generate_brand_assets.swift
import AppKit
import ImageIO
import UniformTypeIdentifiers

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent("Assets")
let info: [String: Any] = ["author": "xcode", "version": 1]
let gold = CGColor(red: 210 / 255, green: 168 / 255, blue: 117 / 255, alpha: 1)
let dark = CGColor(red: 17 / 255, green: 17 / 255, blue: 18 / 255, alpha: 1)

func json(_ value: [String: Any], at url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]).write(to: url)
}

func glyph(_ context: CGContext, center: CGPoint, width: CGFloat) {
    let scale = width / 52
    context.saveGState()
    context.translateBy(x: center.x - 32 * scale, y: center.y + 26 * scale)
    context.scaleBy(x: scale, y: -scale)
    context.setStrokeColor(gold)
    context.setLineWidth(3)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    // Radius-19 ray starts leave a 5-unit gap outside the radius-11 sun's strokes.
    for (a, b) in [(CGPoint(x: 10, y: 40), CGPoint(x: 54, y: 40)),
                   (CGPoint(x: 32, y: 12), CGPoint(x: 32, y: 21)),
                   (CGPoint(x: 18, y: 15.751), CGPoint(x: 22.5, y: 23.546)),
                   (CGPoint(x: 46, y: 15.751), CGPoint(x: 41.5, y: 23.546)),
                   (CGPoint(x: 7.751, y: 26), CGPoint(x: 15.546, y: 30.5)),
                   (CGPoint(x: 56.249, y: 26), CGPoint(x: 48.454, y: 30.5))] {
        context.move(to: a); context.addLine(to: b)
    }
    context.move(to: CGPoint(x: 21, y: 40))
    context.addCurve(to: CGPoint(x: 32, y: 29), control1: CGPoint(x: 21, y: 33.925), control2: CGPoint(x: 25.925, y: 29))
    context.addCurve(to: CGPoint(x: 43, y: 40), control1: CGPoint(x: 38.075, y: 29), control2: CGPoint(x: 43, y: 33.925))
    context.strokePath()
    context.restoreGState()
}

func png(at url: URL, width: Int, height: Int, background: Bool, foreground: Bool, shelf: Bool = false, markWidth: CGFloat? = nil) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let alpha: CGImageAlphaInfo = background ? .noneSkipLast : .premultipliedLast
    let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: alpha.rawValue)!
    let w = CGFloat(width), h = CGFloat(height)
    if background { context.setFillColor(dark); context.fill(CGRect(x: 0, y: 0, width: w, height: h)) }
    if foreground {
        glyph(context, center: CGPoint(x: w / 2, y: shelf ? h * 0.61 : h / 2),
              width: markWidth ?? (shelf ? h * 0.43 : min(w * 0.74, h * 0.74)))
    }
    if shelf {
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        let descriptor = NSFont.systemFont(ofSize: h * 0.15, weight: .semibold).fontDescriptor.withDesign(.serif)!
        let word = NSAttributedString(string: "Lumina", attributes: [
            .font: NSFont(descriptor: descriptor, size: h * 0.15)!,
            .foregroundColor: NSColor(cgColor: gold)!,
        ])
        word.draw(at: CGPoint(x: (w - word.size().width) / 2, y: h * 0.20))
        NSGraphicsContext.restoreGraphicsState()
    }
    let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(destination, context.makeImage()!, nil)
    precondition(CGImageDestinationFinalize(destination))
}

let ios = root.appendingPathComponent("iOS.xcassets")
let tv = root.appendingPathComponent("tvOS.xcassets")
try json(["info": info], at: ios.appendingPathComponent("Contents.json"))
try json(["info": info], at: tv.appendingPathComponent("Contents.json"))
let icon = ios.appendingPathComponent("AppIcon.appiconset")
try png(at: icon.appendingPathComponent("Lumina.png"), width: 1024, height: 1024, background: true, foreground: true)
try json(["info": info, "images": [["filename": "Lumina.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"]]],
         at: icon.appendingPathComponent("Contents.json"))

let brand = tv.appendingPathComponent("App Icon & Top Shelf Image.brandassets")
var assets = [[String: String]]()
for (name, w, h, scales) in [("App Icon - Small", 400, 240, [1, 2]), ("App Icon - App Store", 1280, 768, [1])] {
    let stack = brand.appendingPathComponent("\(name).imagestack")
    var layers = [[String: String]]()
    for layer in ["Front", "Back"] {
        let layerURL = stack.appendingPathComponent("\(layer).imagestacklayer")
        let content = layerURL.appendingPathComponent("Content.imageset")
        var images = [[String: String]]()
        for scale in scales {
            let file = "\(layer)@\(scale)x.png"
            try png(at: content.appendingPathComponent(file), width: w * scale, height: h * scale,
                    background: layer == "Back", foreground: layer == "Front")
            images.append(["idiom": "tv", "scale": "\(scale)x", "filename": file])
        }
        try json(["info": info, "images": images], at: content.appendingPathComponent("Contents.json"))
        try json(["info": info], at: layerURL.appendingPathComponent("Contents.json"))
        layers.append(["filename": "\(layer).imagestacklayer"])
    }
    try json(["info": info, "layers": layers], at: stack.appendingPathComponent("Contents.json"))
    assets.append(["idiom": "tv", "size": "\(w)x\(h)", "role": "primary-app-icon", "filename": "\(name).imagestack"])
}
for (name, w, role) in [("Top Shelf Image", 1920, "top-shelf-image"), ("Top Shelf Image Wide", 2320, "top-shelf-image-wide")] {
    let folder = brand.appendingPathComponent("\(name).imageset")
    var images = [[String: String]]()
    for scale in [1, 2] {
        let file = "Shelf@\(scale)x.png"
        try png(at: folder.appendingPathComponent(file), width: w * scale, height: 720 * scale,
                background: true, foreground: true, shelf: true)
        images.append(["idiom": "tv", "scale": "\(scale)x", "filename": file])
    }
    try json(["info": info, "images": images], at: folder.appendingPathComponent("Contents.json"))
    assets.append(["idiom": "tv", "size": "\(w)x720", "role": role, "filename": "\(name).imageset"])
}
try json(["info": info, "assets": assets], at: brand.appendingPathComponent("Contents.json"))
print("Generated iOS icon and layered tvOS icon/top shelf assets.")

let publicAssets = root.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("frontend/public")
try png(at: publicAssets.appendingPathComponent("email-mark.png"), width: 128, height: 128,
        background: true, foreground: true)
try png(at: publicAssets.appendingPathComponent("email/mark-v1.png"), width: 144, height: 112,
        background: false, foreground: true, markWidth: 128)
