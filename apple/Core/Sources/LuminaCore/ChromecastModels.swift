import Foundation

/// A short-lived, receiver-scoped URL for direct Chromecast playback.
///
/// The URL deliberately belongs to the receiver rather than the signed-in app. It carries only
/// its opaque ticket token, never the member credential used to mint it.
public struct ChromecastTicket: Equatable, Sendable {
    public let ticketID: UUID
    public let mediaURL: URL
    public let contentType: String
    public let title: String
    public let durationSeconds: Double?
    public let positionSeconds: Double
    public let expiresAt: Date
    public let subtitle: ChromecastSubtitle?
    public let qualification: String

    init(ticketID: UUID, mediaURL: URL, contentType: String, title: String,
         durationSeconds: Double?, positionSeconds: Double, expiresAt: Date,
         subtitle: ChromecastSubtitle?, qualification: String) throws {
        guard !contentType.isEmpty, !title.isEmpty, !qualification.isEmpty,
              positionSeconds.isFinite, positionSeconds >= 0,
              durationSeconds.map({ $0.isFinite && $0 >= 0 }) ?? true,
              durationSeconds.map({ positionSeconds <= $0 }) ?? true,
              ChromecastReceiverURL.media(mediaURL, ticketID: ticketID) else {
            throw LuminaError.malformedResponse
        }
        if let subtitle, !ChromecastReceiverURL.subtitle(subtitle.url, ticketID: ticketID) {
            throw LuminaError.malformedResponse
        }
        self.ticketID = ticketID
        self.mediaURL = mediaURL
        self.contentType = contentType
        self.title = title
        self.durationSeconds = durationSeconds
        self.positionSeconds = positionSeconds
        self.expiresAt = expiresAt
        self.subtitle = subtitle
        self.qualification = qualification
    }
}

public struct MusicReceiverQueueEntry: Equatable, Sendable {
    public let itemID: String
    public let mediaURL: URL
    public let contentType: String
    public let title: String
    public let durationSeconds: Double?
}

public struct MusicReceiverTicket: Equatable, Sendable {
    public let ticket: ChromecastTicket
    /// Receiver queue projection in the caller-supplied order; the app remains queue authority.
    public let queue: [MusicReceiverQueueEntry]
    public let queueLimit: Int

    /// Exact receiver capabilities minted with this ticket. This deliberately does not accept
    /// another ticket's otherwise-valid URL or a path derived from one of these URLs.
    public func ownsMediaURL(_ url: URL?) -> Bool {
        guard let url else { return false }
        return ticket.mediaURL == url || queue.contains { $0.mediaURL == url }
    }
}

public struct ChromecastSubtitle: Equatable, Sendable {
    public let url: URL
    public let label: String
    public let language: String?

    init(url: URL, label: String, language: String?) {
        self.url = url
        self.label = label
        self.language = language
    }
}

struct ChromecastTicketResponse: Decodable {
    let ticketID: UUID
    let mediaURL: URL
    let contentType: String
    let title: String
    let durationSeconds: Double?
    let positionSeconds: Double
    let expiresAt: String
    let subtitle: ChromecastSubtitleResponse?
    let qualification: String

    enum CodingKeys: String, CodingKey {
        case ticketID = "ticket_id"
        case mediaURL = "media_url"
        case contentType = "content_type"
        case title
        case durationSeconds = "duration"
        case positionSeconds = "position_seconds"
        case expiresAt = "expires_at"
        case subtitle
        case qualification
    }

    func ticket() throws -> ChromecastTicket {
        guard let expiration = ChromecastDate.parse(expiresAt) else { throw LuminaError.malformedResponse }
        let decodedSubtitle = subtitle.map { ChromecastSubtitle(url: $0.url, label: $0.label, language: $0.language) }
        return try ChromecastTicket(ticketID: ticketID, mediaURL: mediaURL, contentType: contentType,
                                   title: title, durationSeconds: durationSeconds,
                                   positionSeconds: positionSeconds, expiresAt: expiration,
                                   subtitle: decodedSubtitle, qualification: qualification)
    }
}

struct MusicReceiverTicketResponse: Decodable {
    let ticketID: UUID
    let mediaURL: URL
    let contentType: String
    let title: String
    let durationSeconds: Double?
    let positionSeconds: Double
    let expiresAt: String
    let qualification: String
    let queue: [MusicReceiverQueueEntryResponse]
    let queueLimit: Int

    enum CodingKeys: String, CodingKey {
        case ticketID = "ticket_id", mediaURL = "media_url", contentType = "content_type", title
        case durationSeconds = "duration", positionSeconds = "position_seconds", expiresAt = "expires_at"
        case qualification, queue
        case queueLimit = "queue_limit"
    }

    func ticket(currentIndex: Int) throws -> MusicReceiverTicket {
        guard let expiration = ChromecastDate.parse(expiresAt) else { throw LuminaError.malformedResponse }
        let primary = try ChromecastTicket(ticketID: ticketID, mediaURL: mediaURL, contentType: contentType,
                                          title: title, durationSeconds: durationSeconds,
                                          positionSeconds: positionSeconds, expiresAt: expiration,
                                          subtitle: nil, qualification: qualification)
        let entries = try queue.map { try $0.entry(ticketID: ticketID) }
        guard entries.indices.contains(currentIndex), entries[currentIndex].mediaURL == primary.mediaURL,
              (1...100).contains(queueLimit), entries.count <= queueLimit else {
            throw LuminaError.malformedResponse
        }
        return MusicReceiverTicket(ticket: primary, queue: entries, queueLimit: queueLimit)
    }
}

struct MusicReceiverQueueEntryResponse: Decodable {
    let itemID: String
    let mediaURL: URL
    let contentType: String
    let title: String
    let durationSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case itemID = "item_id", mediaURL = "media_url", contentType = "content_type", title
        case durationSeconds = "duration"
    }

    func entry(ticketID: UUID) throws -> MusicReceiverQueueEntry {
        guard !itemID.isEmpty, itemID.utf8.count <= 64, !contentType.isEmpty, !title.isEmpty,
              durationSeconds.map({ $0.isFinite && $0 >= 0 }) ?? true,
              ChromecastReceiverURL.music(mediaURL, ticketID: ticketID, itemID: itemID) else { throw LuminaError.malformedResponse }
        return MusicReceiverQueueEntry(itemID: itemID, mediaURL: mediaURL, contentType: contentType,
                                       title: title, durationSeconds: durationSeconds)
    }
}

struct ChromecastSubtitleResponse: Decodable {
    let url: URL
    let label: String
    let language: String?
}

struct ChromecastRenewalResponse: Decodable {
    let ticketID: UUID
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case ticketID = "ticket_id"
        case expiresAt = "expires_at"
    }

    func validates(ticketID: UUID) -> Bool {
        self.ticketID == ticketID && ChromecastDate.parse(expiresAt) != nil
    }
}

private enum ChromecastDate {
    static func parse(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: value) { return date }
        return ISO8601DateFormatter().date(from: value)
    }
}

private enum ChromecastReceiverURL {
    static func media(_ url: URL, ticketID: UUID) -> Bool {
        videoMedia(url, ticketID: ticketID) || music(url, ticketID: ticketID)
    }

    static func subtitle(_ url: URL, ticketID: UUID) -> Bool {
        valid(url, ticketID: ticketID,
              path: ["api", "chromecast", ticketID.uuidString.lowercased(), "subtitle.vtt"])
    }

    private static func videoMedia(_ url: URL, ticketID: UUID) -> Bool {
        valid(url, ticketID: ticketID, path: ["api", "chromecast", ticketID.uuidString.lowercased(), "media"])
    }

    static func music(_ url: URL, ticketID: UUID, itemID: String? = nil) -> Bool {
        guard let segments = safeSegments(url),
              let ticketIndex = segments.indices.last(where: { UUID(uuidString: String(segments[$0])) == ticketID }),
              ticketIndex >= 3,
              String(segments[ticketIndex - 3]) == "api",
              String(segments[ticketIndex - 2]) == "music",
              String(segments[ticketIndex - 1]) == "receivers",
              UUID(uuidString: String(segments[ticketIndex])) == ticketID else { return false }
        let trailing = Array(segments.dropFirst(ticketIndex + 1).map(String.init))
        if trailing == ["audio"] { return true }
        return trailing.count == 2 && trailing[1] == "audio" && validMusicItemID(trailing[0])
            && (itemID == nil || trailing[0].caseInsensitiveCompare(itemID!) == .orderedSame)
    }

    private static func valid(_ url: URL, ticketID: UUID, path expectedPath: [String]) -> Bool {
        guard let segments = safeSegments(url) else { return false }
        let ticketIndex = expectedPath.count - 2
        let tail = Array(segments.suffix(expectedPath.count))
        guard tail.count == expectedPath.count,
              UUID(uuidString: String(tail[ticketIndex])) == ticketID else { return false }
        return expectedPath.indices.allSatisfy { index in
            index == ticketIndex || tail[index] == Substring(expectedPath[index])
        }
    }

    private static func safeSegments(_ url: URL) -> [Substring]? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = components.host, !host.isEmpty, !isLoopback(host),
              components.user == nil, components.password == nil, components.fragment == nil,
              let query = components.queryItems, query.count == 1,
              query[0].name == "token", let token = query[0].value, !token.isEmpty else {
            return nil
        }
        return components.path.split(separator: "/", omittingEmptySubsequences: true)
    }

    private static func validMusicItemID(_ value: String) -> Bool {
        UUID(uuidString: value) != nil || (value.count == 32 && value.unicodeScalars.allSatisfy {
            switch $0.value {
            case 48...57, 65...70, 97...102: true
            default: false
            }
        })
    }

    private static func isLoopback(_ host: String) -> Bool {
        let normalized = host.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased()
        if normalized == "localhost" || normalized == "::1" || normalized.hasPrefix("::ffff:127.") { return true }
        let octets = normalized.split(separator: ".", omittingEmptySubsequences: false)
        return octets.count == 4 && UInt8(octets[0]) == 127 && octets.dropFirst().allSatisfy { UInt8($0) != nil }
    }
}
