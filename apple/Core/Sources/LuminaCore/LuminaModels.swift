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

public struct MediaUserData: Codable, Hashable, Sendable {
    public let playbackPositionTicks: Int64
    public let played: Bool
    public let isFavorite: Bool

    enum CodingKeys: String, CodingKey {
        case playbackPositionTicks = "PlaybackPositionTicks"
        case played = "Played"
        case isFavorite = "IsFavorite"
    }

    public init(playbackPositionTicks: Int64 = 0, played: Bool = false, isFavorite: Bool = false) {
        self.playbackPositionTicks = playbackPositionTicks
        self.played = played
        self.isFavorite = isFavorite
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        playbackPositionTicks = try values.decodeIfPresent(Int64.self, forKey: .playbackPositionTicks) ?? 0
        played = try values.decodeIfPresent(Bool.self, forKey: .played) ?? false
        isFavorite = try values.decodeIfPresent(Bool.self, forKey: .isFavorite) ?? false
    }
}

public struct MediaItem: Identifiable, Codable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let type: String
    public let overview: String?
    public let productionYear: Int?
    public let seriesName: String?
    public let seriesID: String?
    public private(set) var seriesPrimaryImageTag: String? = nil
    public let seasonID: String?
    public let collectionType: String?
    public let indexNumber: Int?
    public let parentIndexNumber: Int?
    public let runTimeTicks: Int64?
    public let userData: MediaUserData?
    public let imageTags: [String: String]?
    public let backdropImageTags: [String]?
    public let parentBackdropItemID: String?
    public let parentBackdropImageTags: [String]?
    public let album: String?
    public let albumID: String?
    public let albumPrimaryImageTag: String?
    public let artists: [String]?
    public let albumArtist: String?
    public let childCount: Int?
    public let container: String?

    enum CodingKeys: String, CodingKey {
        case id = "Id", name = "Name", type = "Type", overview = "Overview"
        case productionYear = "ProductionYear", seriesName = "SeriesName"
        case seriesID = "SeriesId", seasonID = "SeasonId", collectionType = "CollectionType"
        case seriesPrimaryImageTag = "SeriesPrimaryImageTag"
        case indexNumber = "IndexNumber", parentIndexNumber = "ParentIndexNumber"
        case runTimeTicks = "RunTimeTicks", userData = "UserData", imageTags = "ImageTags"
        case backdropImageTags = "BackdropImageTags"
        case parentBackdropItemID = "ParentBackdropItemId", parentBackdropImageTags = "ParentBackdropImageTags"
        case albumID = "AlbumId", albumPrimaryImageTag = "AlbumPrimaryImageTag"
        case album = "Album", artists = "Artists", albumArtist = "AlbumArtist", childCount = "ChildCount", container = "Container"
    }

    public var resumeSeconds: Double { Double(userData?.playbackPositionTicks ?? 0) / 10_000_000 }
    public var durationSeconds: Double { Double(runTimeTicks ?? 0) / 10_000_000 }
    public var displaySubtitle: String {
        var parts = [String]()
        if let albumArtist { parts.append(albumArtist) }
        else if let artists, !artists.isEmpty { parts.append(artists.joined(separator: ", ")) }
        if let seriesName { parts.append(seriesName) }
        if type == "Episode", let parentIndexNumber, let indexNumber { parts.append("S\(parentIndexNumber) · E\(indexNumber)") }
        else if isAudio, let album { parts.append(album) }
        else if let productionYear { parts.append(String(productionYear)) }
        return parts.joined(separator: " · ")
    }
    public var isCollection: Bool { ["Series", "Season"].contains(type) }
    public var isAudio: Bool { type == "Audio" }
    public var isPlayable: Bool { ["Movie", "Episode", "Video", "Audio"].contains(type) }
}

public struct PlaybackRequest: Identifiable, Sendable {
    public let id: String
    public let item: MediaItem
    public let url: URL
    public let startPositionSeconds: Double
    public let mediaSourceID: String
    public let isTranscoding: Bool
    public let methodLabel: String
    public let usesClientDemux: Bool

    public init(id: String, item: MediaItem, url: URL, startPositionSeconds: Double, mediaSourceID: String, isTranscoding: Bool, methodLabel: String, usesClientDemux: Bool = false) {
        self.id = id
        self.item = item
        self.url = url
        self.startPositionSeconds = startPositionSeconds
        self.mediaSourceID = mediaSourceID
        self.isTranscoding = isTranscoding
        self.methodLabel = methodLabel
        self.usesClientDemux = usesClientDemux
    }
}

public enum PlaybackEvent: Sendable {
    case start
    case progress
    case stop
}

public enum MusicBrowseKind: String, CaseIterable, Sendable {
    case albums, artists, tracks
    public var label: String { rawValue.capitalized }
    var itemTypes: String { switch self { case .albums: "MusicAlbum"; case .artists: "MusicArtist"; case .tracks: "Audio" } }
}

public enum VideoBrowseKind: String, CaseIterable, Sendable {
    case all, movies, shows, anime, episodes
    public var label: String { rawValue.capitalized }
    var itemTypes: String { switch self { case .all: "Movie,Series,Video"; case .movies: "Movie"; case .shows, .anime: "Series"; case .episodes: "Episode" } }
}

public enum VideoSort: String, CaseIterable, Sendable {
    case name, recentlyAdded, year
    public var label: String { switch self { case .name: "Name"; case .recentlyAdded: "Recently added"; case .year: "Year" } }
    var field: String { switch self { case .name: "SortName"; case .recentlyAdded: "DateCreated"; case .year: "ProductionYear" } }
}

public struct VideoFilters: Equatable, Sendable {
    public var genre: String
    public var year: Int?
    public var unwatchedOnly: Bool
    public var favoritesOnly: Bool

    public init(genre: String = "", year: Int? = nil, unwatchedOnly: Bool = false, favoritesOnly: Bool = false) {
        self.genre = genre
        self.year = year
        self.unwatchedOnly = unwatchedOnly
        self.favoritesOnly = favoritesOnly
    }
}
