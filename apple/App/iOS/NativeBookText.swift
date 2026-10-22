import SwiftUI
import UIKit
import LuminaCore

/// Immutable after construction; isolated TextKit pagination is performed on a worker.
final class NativeBookDocument: @unchecked Sendable {
    let text: NSAttributedString
    let anchors: [String: Int]
    init(text: NSAttributedString, anchors: [String: Int]) { self.text = NSAttributedString(attributedString: text); self.anchors = anchors }

    func pages(size: CGSize) throws -> [NSRange] {
        let storage = NSTextStorage(attributedString: text)
        let manager = NSLayoutManager()
        storage.addLayoutManager(manager)
        var result = [NSRange](), location = 0
        while location < text.length {
            try Task.checkCancellation()
            let container = NSTextContainer(size: size)
            container.lineFragmentPadding = 0
            manager.addTextContainer(container)
            let glyphs = manager.glyphRange(for: container)
            let visible = manager.characterRange(forGlyphRange: glyphs, actualGlyphRange: nil)
            guard visible.length > 0, NSMaxRange(visible) > location else { throw LuminaError.malformedResponse }
            result.append(visible)
            location = NSMaxRange(visible)
        }
        return result.isEmpty ? [NSRange(location: 0, length: 0)] : result
    }
}

enum NativeBookTypesetter {
    static func document(nodes: [BookNode], size: CGFloat, color: UIColor, images: [String: UIImage], pageSize: CGSize) throws -> NativeBookDocument {
        let output = NSMutableAttributedString(string: "")
        var anchors = [String: Int](), visited = 0
        func append(_ nodes: [BookNode], depth: Int, font: UIFont, ordered: Bool = false) throws {
            guard depth <= 40 else { throw LuminaError.malformedResponse }
            var listNumber = 0
            for node in nodes {
                visited += 1
                guard visited <= 50_000, output.length <= 1_000_000 else { throw LuminaError.malformedResponse }
                if let id = node.id { anchors[id] = output.length }
                let start = output.length
                var currentFont = font
                if node.tag == "strong" { currentFont = UIFont(descriptor: font.fontDescriptor.withSymbolicTraits(font.fontDescriptor.symbolicTraits.union(.traitBold)) ?? font.fontDescriptor, size: font.pointSize) }
                if node.tag == "em" { currentFont = UIFont(descriptor: font.fontDescriptor.withSymbolicTraits(font.fontDescriptor.symbolicTraits.union(.traitItalic)) ?? font.fontDescriptor, size: font.pointSize) }
                if node.tag.hasPrefix("h"), let level = Int(node.tag.dropFirst()), (1...6).contains(level) { currentFont = .systemFont(ofSize: size * (level <= 2 ? 1.4 : 1.15), weight: .semibold) }
                let paragraph = NSMutableParagraphStyle()
                paragraph.lineSpacing = size * 0.18
                paragraph.paragraphSpacing = size * 0.5
                paragraph.baseWritingDirection = .natural
                let attributes: [NSAttributedString.Key: Any] = [.font: currentFont, .foregroundColor: color, .paragraphStyle: paragraph]
                if node.tag == "li" { listNumber += 1; output.append(NSAttributedString(string: ordered ? "\(listNumber). " : "• ", attributes: attributes)) }
                if let text = node.text { output.append(NSAttributedString(string: text, attributes: attributes)) }
                if let children = node.children { try append(children, depth: depth + 1, font: currentFont, ordered: node.tag == "ol") }
                if node.tag == "img", let member = node.member, let image = images[member] {
                    let attachment = NSTextAttachment()
                    attachment.image = image
                    let scale = min(1, pageSize.width / max(1, image.size.width), pageSize.height * 0.7 / max(1, image.size.height))
                    attachment.bounds = CGRect(x: 0, y: 0, width: image.size.width * scale, height: image.size.height * scale)
                    output.append(NSAttributedString(attachment: attachment))
                    output.append(NSAttributedString(string: "\n", attributes: attributes))
                }
                if node.tag == "a", node.chapter != nil || node.anchor != nil {
                    var parts = URLComponents()
                    parts.scheme = "lumina-book"; parts.host = "chapter"; parts.path = "/\(node.chapter ?? -1)"
                    parts.queryItems = node.anchor.map { [URLQueryItem(name: "anchor", value: $0)] }
                    if let url = parts.url { output.addAttribute(.link, value: url, range: NSRange(location: start, length: output.length - start)) }
                }
                if ["p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "br", "hr"].contains(node.tag) {
                    output.append(NSAttributedString(string: "\n", attributes: attributes))
                }
            }
        }
        try append(nodes, depth: 0, font: .systemFont(ofSize: size, weight: .regular, width: .standard).withDesign(.serif))
        return NativeBookDocument(text: output, anchors: anchors)
    }

    static func imageMembers(_ nodes: [BookNode], depth: Int = 0) -> [String] {
        guard depth <= 40 else { return [] }
        return nodes.flatMap { node in (node.member.map { [$0] } ?? []) + imageMembers(node.children ?? [], depth: depth + 1) }
    }
}

/// One preparation lane per reader; cancelled layout requests stop before doing expensive work.
actor NativeBookPreparation {
    func image(_ data: Data, pixels: Int) throws -> CGImage {
        try Task.checkCancellation()
        guard let image = BookPagePreparation.image(from: data, maximumPixelSize: pixels) else { throw LuminaError.malformedResponse }
        try Task.checkCancellation()
        return image
    }
    func document(nodes: [BookNode], fontSize: CGFloat, color: UIColor, images: [String: UIImage], size: CGSize) throws -> (NativeBookDocument, [NSRange]) {
        try Task.checkCancellation()
        let document = try NativeBookTypesetter.document(nodes: nodes, size: fontSize, color: color, images: images, pageSize: size)
        let readableSize = CGSize(width: size.width, height: max(size.height, fontSize * 2))
        return (document, try document.pages(size: readableSize))
    }
}

private extension UIFont {
    func withDesign(_ design: UIFontDescriptor.SystemDesign) -> UIFont {
        UIFont(descriptor: fontDescriptor.withDesign(design) ?? fontDescriptor, size: pointSize)
    }
}

struct NativeBookTextPage: UIViewRepresentable {
    let document: NativeBookDocument
    let range: NSRange
    let rightToLeft: Bool
    let onLink: (URL) -> Void
    let onTurn: (Bool) -> Void
    let onControls: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.isEditable = false; view.isScrollEnabled = true
        view.backgroundColor = .clear
        view.textContainerInset = .zero
        view.textContainer.lineFragmentPadding = 0
        view.adjustsFontForContentSizeCategory = false
        view.delegate = context.coordinator
        context.coordinator.interaction = BookReadingGestures(scrollView: view, accessibilityTarget: view)
        view.accessibilityIdentifier = "bookProsePage"
        return view
    }
    func updateUIView(_ view: UITextView, context: Context) {
        context.coordinator.onLink = onLink
        context.coordinator.interaction?.rightToLeft = rightToLeft
        context.coordinator.interaction?.onTurn = onTurn
        context.coordinator.interaction?.onControls = onControls
        if context.coordinator.document !== document || context.coordinator.range != range {
            view.attributedText = document.text.attributedSubstring(from: range)
            view.setContentOffset(.zero, animated: false)
            context.coordinator.document = document; context.coordinator.range = range
        }
    }
    final class Coordinator: NSObject, UITextViewDelegate {
        var document: NativeBookDocument?
        var range: NSRange?
        var onLink: ((URL) -> Void)?
        var interaction: BookReadingGestures?
        func textView(_ textView: UITextView, primaryActionFor textItem: UITextItem, defaultAction: UIAction) -> UIAction? {
            guard case .link(let url) = textItem.content, url.scheme == "lumina-book" else { return nil }
            return UIAction { [weak self] _ in self?.onLink?(url) }
        }
    }
}

/// Native page gestures coexist with text selection, links and magnified artwork panning.
@MainActor
final class BookReadingGestures: NSObject, UIGestureRecognizerDelegate {
    weak var scrollView: UIScrollView?
    var rightToLeft = false
    var onTurn: ((Bool) -> Void)?
    var onControls: (() -> Void)?
    init(scrollView: UIScrollView, accessibilityTarget: UIView, doubleTap: UITapGestureRecognizer? = nil) {
        self.scrollView = scrollView
        super.init()
        let tap = UITapGestureRecognizer(target: self, action: #selector(tapped(_:)))
        tap.cancelsTouchesInView = false; tap.delegate = self
        if let doubleTap { tap.require(toFail: doubleTap) }
        scrollView.addGestureRecognizer(tap)
        for direction: UISwipeGestureRecognizer.Direction in [.left, .right] {
            let swipe = UISwipeGestureRecognizer(target: self, action: #selector(swiped(_:)))
            swipe.direction = direction; swipe.delegate = self
            scrollView.addGestureRecognizer(swipe)
        }
        accessibilityTarget.accessibilityCustomActions = [
            UIAccessibilityCustomAction(name: "Next page") { [weak self] _ in self?.onTurn?(true); return true },
            UIAccessibilityCustomAction(name: "Previous page") { [weak self] _ in self?.onTurn?(false); return true },
            UIAccessibilityCustomAction(name: "Show or hide reading controls") { [weak self] _ in self?.onControls?(); return true },
        ]
    }
    func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if let text = scrollView as? UITextView, text.selectedRange.length > 0 { return false }
        if gestureRecognizer is UISwipeGestureRecognizer {
            guard let scrollView else { return false }
            return scrollView.zoomScale <= 1 && scrollView.contentSize.width <= scrollView.bounds.width + 1
        }
        return true
    }
    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }
    @objc private func swiped(_ gesture: UISwipeGestureRecognizer) {
        onTurn?((gesture.direction == .left) != rightToLeft)
    }
    @objc private func tapped(_ gesture: UITapGestureRecognizer) {
        if let text = scrollView as? UITextView,
           let position = text.closestPosition(to: gesture.location(in: text)) {
            let index = text.offset(from: text.beginningOfDocument, to: position)
            if index < text.attributedText.length, text.attributedText.attribute(.link, at: index, effectiveRange: nil) != nil { return }
        }
        onControls?()
    }
}
