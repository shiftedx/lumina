import Foundation

public struct LuminaSession: Codable, Sendable {
    public let serverURL: URL
    public let accessToken: String
    public let userID: String
    public let username: String
    public let serverName: String
    public let deviceID: String

    public init(serverURL: URL, accessToken: String, userID: String, username: String, serverName: String, deviceID: String) {
        self.serverURL = serverURL
        self.accessToken = accessToken
        self.userID = userID
        self.username = username
        self.serverName = serverName
        self.deviceID = deviceID
    }
}

public struct MediaUserData: Decodable, Hashable, Sendable {
    public let playbackPositionTicks: Int64
    public let played: Bool

    enum CodingKeys: String, CodingKey {
        case playbackPositionTicks = "PlaybackPositionTicks"
        case played = "Played"
    }

    public init(playbackPositionTicks: Int64 = 0, played: Bool = false) {
        self.playbackPositionTicks = playbackPositionTicks
        self.played = played
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        playbackPositionTicks = try values.decodeIfPresent(Int64.self, forKey: .playbackPositionTicks) ?? 0
        played = try values.decodeIfPresent(Bool.self, forKey: .played) ?? false
    }
}

public struct MediaItem: Identifiable, Decodable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let type: String
    public let overview: String?
    public let productionYear: Int?
    public let seriesName: String?
    public let indexNumber: Int?
    public let parentIndexNumber: Int?
    public let runTimeTicks: Int64?
    public let userData: MediaUserData?
    public let imageTags: [String: String]?

    enum CodingKeys: String, CodingKey {
        case id = "Id", name = "Name", type = "Type", overview = "Overview"
        case productionYear = "ProductionYear", seriesName = "SeriesName"
        case indexNumber = "IndexNumber", parentIndexNumber = "ParentIndexNumber"
        case runTimeTicks = "RunTimeTicks", userData = "UserData", imageTags = "ImageTags"
    }

    public var resumeSeconds: Double { Double(userData?.playbackPositionTicks ?? 0) / 10_000_000 }
    public var durationSeconds: Double { Double(runTimeTicks ?? 0) / 10_000_000 }
    public var displaySubtitle: String {
        var parts = [String]()
        if let seriesName { parts.append(seriesName) }
        if let parentIndexNumber, let indexNumber { parts.append("S\(parentIndexNumber) · E\(indexNumber)") }
        else if let productionYear { parts.append(String(productionYear)) }
        return parts.joined(separator: " · ")
    }
    public var isPlayable: Bool { ["Movie", "Episode", "Video"].contains(type) }
}

public struct PlaybackRequest: Identifiable, Sendable {
    public let id: String
    public let item: MediaItem
    public let url: URL
    public let startPositionSeconds: Double
    public let mediaSourceID: String
    public let isTranscoding: Bool
    public let methodLabel: String

    public init(id: String, item: MediaItem, url: URL, startPositionSeconds: Double, mediaSourceID: String, isTranscoding: Bool, methodLabel: String) {
        self.id = id
        self.item = item
        self.url = url
        self.startPositionSeconds = startPositionSeconds
        self.mediaSourceID = mediaSourceID
        self.isTranscoding = isTranscoding
        self.methodLabel = methodLabel
    }
}

public enum PlaybackEvent: Sendable {
    case start
    case progress
    case stop
}
