import Foundation

/// A lossless, presentation-ready decomposition of a server-provided media overview.
///
/// Only standalone `(Source: …)` credits and `Notes:` bullet lists receive structure;
/// all other prose remains in the synopsis.
public struct MediaOverview: Hashable, Sendable {
    public struct Paragraph: Identifiable, Hashable, Sendable {
        public let id: Int
        public let text: String

        init(id: Int, text: String) {
            self.id = id
            self.text = text
        }
    }

    public let synopsis: [Paragraph]
    public let source: String?
    public let notes: [Paragraph]

    public init(_ raw: String) {
        let parsed = Self.parse(Self.cleanedText(from: raw))
        synopsis = parsed.synopsis.enumerated().map { Paragraph(id: $0.offset, text: $0.element) }
        source = parsed.source
        notes = parsed.notes.enumerated().map { Paragraph(id: $0.offset, text: $0.element) }
    }

    public var isEmpty: Bool { synopsis.isEmpty && source == nil && notes.isEmpty }

    private static func parse(_ text: String) -> (synopsis: [String], source: String?, notes: [String]) {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n")
        var synopsisLines = [String](), source: String?, notes = [String](), index = 0

        while index < lines.count {
            let line = lines[index].trimmingCharacters(in: .whitespacesAndNewlines)
            if source == nil, let credit = sourceCredit(in: line) {
                source = credit
                index += 1
                continue
            }
            if line == "Notes:", let parsedNotes = parsedNotes(after: index, in: lines) {
                notes.append(contentsOf: parsedNotes.entries)
                index = parsedNotes.nextIndex
                continue
            }
            synopsisLines.append(line)
            index += 1
        }

        return (paragraphs(from: synopsisLines), source, notes)
    }

    private static func sourceCredit(in line: String) -> String? {
        guard line.hasPrefix("(Source:"), line.hasSuffix(")") else { return nil }
        let start = line.index(line.startIndex, offsetBy: "(Source:".count)
        let credit = line[start..<line.index(before: line.endIndex)].trimmingCharacters(in: .whitespacesAndNewlines)
        return credit.isEmpty ? nil : credit
    }

    private static func parsedNotes(after heading: Int, in lines: [String]) -> (entries: [String], nextIndex: Int)? {
        var index = heading + 1
        while index < lines.count, lines[index].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            index += 1
        }

        var entries = [String]()
        while index < lines.count {
            let line = lines[index].trimmingCharacters(in: .whitespacesAndNewlines)
            if let bullet = bulletText(in: line) {
                entries.append(bullet)
                index += 1
                continue
            }
            if let continuation = indentedContinuation(in: lines[index]), !entries.isEmpty {
                entries[entries.count - 1] += " \(continuation)"
                index += 1
                continue
            }
            if line.isEmpty, !entries.isEmpty {
                var next = index + 1
                while next < lines.count, lines[next].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { next += 1 }
                if next < lines.count, bulletText(in: lines[next].trimmingCharacters(in: .whitespacesAndNewlines)) != nil {
                    index = next
                    continue
                }
            }
            break
        }
        return entries.isEmpty ? nil : (entries, index)
    }

    private static func bulletText(in line: String) -> String? {
        let prefixes = ["• ", "- ", "* "]
        guard let prefix = prefixes.first(where: { line.hasPrefix($0) }) else { return nil }
        let text = line.dropFirst(prefix.count).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    private static func indentedContinuation(in rawLine: String) -> String? {
        guard rawLine.hasPrefix("  ") || rawLine.hasPrefix("\t") else { return nil }
        let text = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty || bulletText(in: text) != nil ? nil : text
    }

    private static func paragraphs(from lines: [String]) -> [String] {
        var result = [String](), current = [String]()
        for line in lines {
            if line.isEmpty {
                if !current.isEmpty {
                    result.append(current.joined(separator: " "))
                    current.removeAll(keepingCapacity: true)
                }
            } else {
                current.append(line)
            }
        }
        if !current.isEmpty { result.append(current.joined(separator: " ")) }
        return result
    }

    private static func cleanedText(from raw: String) -> String {
        guard looksLikeHTML(raw), raw.utf8.count <= 200_000 else { return decodeEntities(in: raw) }
        var text = raw
        text = replace("<\\s*br\\s*/?\\s*>", in: text, with: "\n")
        text = replace("<\\s*li\\b[^>]*>", in: text, with: "\n• ")
        text = replace("<\\s*/\\s*li\\s*>", in: text, with: "\n")
        text = replace("<\\s*/?\\s*(?:p|div|section|article|blockquote|h[1-6]|ul|ol)\\b[^>]*>", in: text, with: "\n\n")
        text = replace("<\\s*/?\\s*(?:b|strong|i|em|span|a)\\b[^>]*>", in: text, with: "")
        text = replace("<[^>]{0,2048}>", in: text, with: "")
        return decodeEntities(in: text)
    }

    private static func looksLikeHTML(_ text: String) -> Bool {
        text.range(of: "<\\s*/?\\s*(?:p|br|ul|ol|li|div|section|article|blockquote|h[1-6]|b|strong|i|em|span|a)\\b",
                   options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func replace(_ pattern: String, in text: String, with replacement: String) -> String {
        text.replacingOccurrences(of: pattern, with: replacement, options: [.regularExpression, .caseInsensitive])
    }

    private static func decodeEntities(in text: String) -> String {
        var result = "", index = text.startIndex
        while index < text.endIndex {
            guard text[index] == "&",
                  let semicolon = text[index...].prefix(13).firstIndex(of: ";") else {
                result.append(text[index])
                index = text.index(after: index)
                continue
            }
            let entity = String(text[text.index(after: index)..<semicolon])
            if let decoded = decodedEntity(entity) {
                result.append(contentsOf: decoded)
                index = text.index(after: semicolon)
            } else {
                result.append(text[index])
                index = text.index(after: index)
            }
        }
        return result
    }

    private static func decodedEntity(_ entity: String) -> String? {
        switch entity {
        case "amp": return "&"
        case "lt": return "<"
        case "gt": return ">"
        case "quot": return "\""
        case "apos", "#39": return "'"
        case "nbsp": return " "
        case "copy": return "©"
        case "reg": return "®"
        case "ndash": return "–"
        case "mdash": return "—"
        case "hellip": return "…"
        default:
            let scalar: UInt32?
            if entity.hasPrefix("#x") || entity.hasPrefix("#X") {
                scalar = UInt32(entity.dropFirst(2), radix: 16)
            } else if entity.hasPrefix("#") {
                scalar = UInt32(entity.dropFirst(), radix: 10)
            } else {
                scalar = nil
            }
            return scalar.flatMap(UnicodeScalar.init).map(String.init)
        }
    }
}
