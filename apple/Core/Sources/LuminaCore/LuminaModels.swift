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

public struct MediaPerson: Codable, Hashable, Sendable {
    public let id: String?
    public let name: String
    public let type: String?
    public let role: String?
    public let primaryImageTag: String?

    enum CodingKeys: String, CodingKey {
        case id = "Id", name = "Name", type = "Type", role = "Role", primaryImageTag = "PrimaryImageTag"
    }
}

/// A server-supplied video chapter, safe to expose as a seek target.
public struct PlaybackChapter: Identifiable, Codable, Hashable, Sendable {
    public let id: Int64
    public let title: String

    enum CodingKeys: String, CodingKey {
        case id = "StartPositionTicks"
        case title = "Name"
    }

    public init(id: Int64, title: String) {
        self.id = id
        self.title = title
    }

    public var startPositionSeconds: Double { Double(id) / 10_000_000 }

    public init(from decoder: Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            id = -1
            title = ""
            return
        }
        id = (try? values.decode(Int64.self, forKey: .id)) ?? -1
        title = (try? values.decode(String.self, forKey: .title)) ?? ""
    }
}

/// A detected skip range from Lumina's media analysis. The range is shown only when the
/// server has recorded it for the media source being played.
public struct PlaybackMarker: Identifiable, Hashable, Sendable {
    public enum Kind: String, CaseIterable, Hashable, Sendable {
        case intro
        case credits
        case recap
        case preview
        case commercial

        var jellyfinType: String {
            switch self {
            case .intro: "Intro"
            case .credits: "Outro"
            case .recap: "Recap"
            case .preview: "Preview"
            case .commercial: "Commercial"
            }
        }

        init?(jellyfinType: String) {
            switch jellyfinType.lowercased() {
            case "intro": self = .intro
            case "outro": self = .credits
            case "recap": self = .recap
            case "preview": self = .preview
            case "commercial": self = .commercial
            default: return nil
            }
        }
    }

    public let kind: Kind
    public let startSeconds: Double
    public let endSeconds: Double
    public var id: String { "\(kind.rawValue):\(startSeconds):\(endSeconds)" }

    public init(kind: Kind, startSeconds: Double, endSeconds: Double) {
        self.kind = kind
        self.startSeconds = startSeconds
        self.endSeconds = endSeconds
    }
}

/// Optional probed file facts for detail screens. Never describes the active output route.
public struct MediaTechnicalStream: Codable, Hashable, Sendable {
    public let type: String?
    public let displayTitle: String?
    public let codec: String?
    public let width: Int?
    public let height: Int?
    public let channels: Int?
    public let isDefault: Bool?
    public let isInterlaced: Bool?

    enum CodingKeys: String, CodingKey {
        case type = "Type", displayTitle = "DisplayTitle", codec = "Codec"
        case width = "Width", height = "Height", channels = "Channels", isDefault = "IsDefault", isInterlaced = "IsInterlaced"
    }
}

public struct MediaItem: Identifiable, Codable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let type: String
    public let overview: String?
    public let productionYear: Int?
    public private(set) var premiereDate: String? = nil
    public private(set) var mediaStreams: [MediaTechnicalStream]? = nil
    public private(set) var officialRating: String? = nil
    public private(set) var communityRating: Double? = nil
    public private(set) var genres: [String]? = nil
    /// Native Home row-only classification. General Jellyfin item DTOs intentionally omit it.
    public private(set) var luminaCategory: String? = nil
    public private(set) var people: [MediaPerson]? = nil
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
    /// Native Home can retain a saved file's identity while borrowing its catalog artwork.
    public private(set) var parentPrimaryImageItemID: String? = nil
    public private(set) var parentPrimaryImageTag: String? = nil
    public private(set) var parentLogoItemID: String? = nil
    public private(set) var parentLogoImageTag: String? = nil
    public let album: String?
    public let albumID: String?
    public let albumPrimaryImageTag: String?
    public let artists: [String]?
    public let albumArtist: String?
    public let childCount: Int?
    public let container: String?
    private let chapterMarkers: [PlaybackChapter]?

    public init(id: String, name: String, type: String, overview: String?, productionYear: Int?,
                officialRating: String? = nil, communityRating: Double? = nil,
                genres: [String]? = nil, luminaCategory: String? = nil, people: [MediaPerson]? = nil,
                seriesName: String?, seriesID: String?, seriesPrimaryImageTag: String? = nil,
                seasonID: String?, collectionType: String?, indexNumber: Int?, parentIndexNumber: Int?,
                runTimeTicks: Int64?, userData: MediaUserData?, imageTags: [String: String]?,
                backdropImageTags: [String]?, parentBackdropItemID: String?, parentBackdropImageTags: [String]?,
                album: String?, albumID: String?, albumPrimaryImageTag: String?, artists: [String]?,
                albumArtist: String?, childCount: Int?, container: String?, chapters: [PlaybackChapter]? = nil) {
        self.id = id
        self.name = name
        self.type = type
        self.overview = overview
        self.productionYear = productionYear
        self.officialRating = officialRating
        self.communityRating = communityRating
        self.genres = genres
        self.luminaCategory = luminaCategory
        self.people = people
        self.seriesName = seriesName
        self.seriesID = seriesID
        self.seriesPrimaryImageTag = seriesPrimaryImageTag
        self.seasonID = seasonID
        self.collectionType = collectionType
        self.indexNumber = indexNumber
        self.parentIndexNumber = parentIndexNumber
        self.runTimeTicks = runTimeTicks
        self.userData = userData
        self.imageTags = imageTags
        self.backdropImageTags = backdropImageTags
        self.parentBackdropItemID = parentBackdropItemID
        self.parentBackdropImageTags = parentBackdropImageTags
        self.album = album
        self.albumID = albumID
        self.albumPrimaryImageTag = albumPrimaryImageTag
        self.artists = artists
        self.albumArtist = albumArtist
        self.childCount = childCount
        self.container = container
        chapterMarkers = chapters
    }

    enum CodingKeys: String, CodingKey {
        case id = "Id", name = "Name", type = "Type", overview = "Overview"
        case premiereDate = "PremiereDate", mediaStreams = "MediaStreams"
        case productionYear = "ProductionYear", officialRating = "OfficialRating", communityRating = "CommunityRating"
        case genres = "Genres", luminaCategory = "LuminaCategory", people = "People", seriesName = "SeriesName"
        case seriesID = "SeriesId", seasonID = "SeasonId", collectionType = "CollectionType"
        case seriesPrimaryImageTag = "SeriesPrimaryImageTag"
        case indexNumber = "IndexNumber", parentIndexNumber = "ParentIndexNumber"
        case runTimeTicks = "RunTimeTicks", userData = "UserData", imageTags = "ImageTags"
        case backdropImageTags = "BackdropImageTags"
        case parentBackdropItemID = "ParentBackdropItemId", parentBackdropImageTags = "ParentBackdropImageTags"
        case parentPrimaryImageItemID = "ParentPrimaryImageItemId", parentPrimaryImageTag = "ParentPrimaryImageTag"
        case parentLogoItemID = "ParentLogoItemId", parentLogoImageTag = "ParentLogoImageTag"
        case albumID = "AlbumId", albumPrimaryImageTag = "AlbumPrimaryImageTag"
        case album = "Album", artists = "Artists", albumArtist = "AlbumArtist", childCount = "ChildCount", container = "Container"
        case chapterMarkers = "Chapters"
    }

    public var resumeSeconds: Double { Double(userData?.playbackPositionTicks ?? 0) / 10_000_000 }
    public var homeHeroTitle: String {
        type == "Episode" ? seriesName.flatMap { $0.isEmpty ? nil : $0 } ?? name : name
    }
    public var homeHeroSubtitle: String? {
        guard type == "Episode" else { return nil }
        let number = parentIndexNumber.flatMap { season in indexNumber.map { "S\(season) · E\($0)" } }
        return [number, name].compactMap { $0 }.joined(separator: " · ")
    }
    public var durationSeconds: Double { Double(runTimeTicks ?? 0) / 10_000_000 }
    /// Ordered chapter targets from the item-detail response. Invalid, duplicate, and out-of-range
    /// values are ignored so a malformed server response cannot offer an unsafe seek.
    public var chapters: [PlaybackChapter] {
        sanitizedPlaybackChapters(chapterMarkers ?? [], durationSeconds: durationSeconds)
    }
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

func sanitizedPlaybackChapters(_ markers: [PlaybackChapter], durationSeconds: Double) -> [PlaybackChapter] {
    let hasDuration = durationSeconds.isFinite && durationSeconds > 0
    var seen = Set<Int64>()
    let valid = markers
        .filter { chapter in
            chapter.id >= 0 && chapter.startPositionSeconds.isFinite
                && (!hasDuration || chapter.startPositionSeconds < durationSeconds)
        }
        .sorted { $0.id < $1.id }
        .filter { seen.insert($0.id).inserted }
        .prefix(200)
    return valid.enumerated().map { index, chapter in
        let title = chapter.title.trimmingCharacters(in: .whitespacesAndNewlines)
        return PlaybackChapter(
            id: chapter.id,
            title: title.isEmpty ? "Chapter \(index + 1)" : String(title.prefix(240)),
        )
    }
}

public enum PlaybackStreamType: String, Hashable, Sendable {
    case video
    case audio
    case subtitle
    case other
}

/// A bounded client-side video quality preference for a playback request.
///
/// Automatic leaves rendition selection to Lumina's server policy. The capped choices are
/// limited to the server's H.264 rendition rungs.
public enum PlaybackQuality: String, CaseIterable, Codable, Hashable, Sendable {
    case automatic
    case upTo1080p
    case upTo720p
    case upTo480p

    public var label: String {
        switch self {
        case .automatic: "Automatic"
        case .upTo1080p: "Up to 1080p"
        case .upTo720p: "Up to 720p"
        case .upTo480p: "Up to 480p"
        }
    }

    /// The maximum encoded delivery bitrate advertised to Lumina, in bits per second.
    public var maximumBitRate: Int? {
        switch self {
        case .automatic: nil
        case .upTo1080p: 8_000_000
        case .upTo720p: 4_000_000
        case .upTo480p: 1_500_000
        }
    }

    /// The maximum video height advertised to Lumina's playback decision.
    public var maximumHeight: Int? {
        switch self {
        case .automatic: nil
        case .upTo1080p: 1_080
        case .upTo720p: 720
        case .upTo480p: 480
        }
    }
}

public struct PlaybackStream: Identifiable, Hashable, Sendable {
    public let index: Int
    public let type: PlaybackStreamType
    public let label: String
    public let codec: String?
    public let language: String?
    public let channels: Int?
    public let isDefault: Bool
    public let isForced: Bool
    public let isExternal: Bool
    /// Whether this is an origin-bound WebVTT sidecar that the store can fetch for native playback.
    public let hasExternalSubtitle: Bool
    // The server delivery address is deliberately not public API. It may have arrived with a
    // token query that the client removes before retaining this authenticated request target.
    let subtitleDeliveryURL: URL?

    public var id: Int { index }

    public init(index: Int, type: PlaybackStreamType, label: String, codec: String? = nil,
                language: String? = nil, channels: Int? = nil, isDefault: Bool = false,
                isForced: Bool = false, isExternal: Bool = false) {
        self.init(index: index, type: type, label: label, codec: codec, language: language,
                  channels: channels, isDefault: isDefault, isForced: isForced,
                  isExternal: isExternal, subtitleDeliveryURL: nil)
    }

    init(index: Int, type: PlaybackStreamType, label: String, codec: String? = nil,
         language: String? = nil, channels: Int? = nil, isDefault: Bool = false,
         isForced: Bool = false, isExternal: Bool = false, subtitleDeliveryURL: URL?) {
        self.index = index
        self.type = type
        self.label = label
        self.codec = codec
        self.language = language
        self.channels = channels
        self.isDefault = isDefault
        self.isForced = isForced
        self.isExternal = isExternal
        self.subtitleDeliveryURL = subtitleDeliveryURL
        self.hasExternalSubtitle = subtitleDeliveryURL != nil
    }
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
    /// The selected source's server-reported bitrate, when it is a sane positive value.
    public let bitRate: Int?
    /// Server-reported streams for the selected media source. Delivery URLs stay private to the client.
    public let mediaStreams: [PlaybackStream]
    /// The server-resolved audio stream index, or nil when the source has no audio stream.
    public let audioStreamIndex: Int?
    /// The server-resolved subtitle stream index. `-1` represents an explicit Off choice.
    public let subtitleStreamIndex: Int?
    /// Safe chapter targets from the refreshed item detail.
    public let chapters: [PlaybackChapter]
    /// The device quality preference used to obtain this server playback decision.
    /// Playback replacements retain it instead of silently returning to an uncapped request.
    public let resolvedQuality: PlaybackQuality

    public init(id: String, item: MediaItem, url: URL, startPositionSeconds: Double, mediaSourceID: String,
                isTranscoding: Bool, methodLabel: String, usesClientDemux: Bool = false,
                bitRate: Int? = nil,
                mediaStreams: [PlaybackStream] = [], audioStreamIndex: Int? = nil,
                subtitleStreamIndex: Int? = nil, chapters: [PlaybackChapter] = [],
                resolvedQuality: PlaybackQuality = .automatic) {
        self.id = id
        self.item = item
        self.url = url
        self.startPositionSeconds = startPositionSeconds
        self.mediaSourceID = mediaSourceID
        self.isTranscoding = isTranscoding
        self.methodLabel = methodLabel
        self.usesClientDemux = usesClientDemux
        self.bitRate = bitRate
        self.mediaStreams = mediaStreams
        self.audioStreamIndex = audioStreamIndex
        self.subtitleStreamIndex = subtitleStreamIndex
        self.chapters = chapters
        self.resolvedQuality = resolvedQuality
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
    case all, movies, shows, anime, youtube, episodes
    public var label: String {
        switch self {
        case .youtube: "YouTube"
        default: rawValue.capitalized
        }
    }
    var itemTypes: String {
        switch self {
        case .all: "Movie,Series,Video"
        case .movies: "Movie"
        case .shows: "Series"
        case .anime: "Movie,Series"
        // Saved provider videos are represented as Episodes below the native
        // Channels view. This deliberately excludes remote discovery entries.
        case .youtube: "Episode"
        case .episodes: "Episode"
        }
    }

    var luminaCategory: String? {
        switch self {
        case .movies: "movies"
        case .anime: "anime"
        default: nil
        }
    }
}

/// A server-indexed position in a name-sorted video wall.  The index is an
/// absolute offset in the active member-visible, filtered catalog.
public struct LibraryAlphabetAnchor: Codable, Identifiable, Equatable, Sendable {
    public let letter: String
    public let index: Int

    public var id: String { letter }

    public init(letter: String, index: Int) {
        self.letter = letter
        self.index = index
    }
}

public enum VideoSort: String, CaseIterable, Sendable {
    case name, recentlyAdded, year
    public var label: String { switch self { case .name: "Name"; case .recentlyAdded: "Recently added"; case .year: "Year" } }
    var field: String { switch self { case .name: "SortName"; case .recentlyAdded: "DateCreated"; case .year: "ProductionYear" } }
}

/// A server-indexed movie resolution. The raw value is the stable Lumina API alias,
/// while `label` is intended for the native filter control.
public enum VideoResolution: String, CaseIterable, Hashable, Sendable {
    case ultraHD = "4k"
    case fullHD = "1080p"
    case hd = "720p"
    case standardDefinition = "sd"

    public var label: String {
        switch self {
        case .ultraHD: "4K"
        case .fullHD: "1080p"
        case .hd: "720p"
        case .standardDefinition: "SD"
        }
    }
}

/// A server-indexed HDR transfer format. Unknown source metadata never matches a
/// selected format so the filter remains an explicit technical promise.
public enum VideoHDR: String, CaseIterable, Hashable, Sendable {
    case sdr
    case hdr10
    case hdr10Plus = "hdr10plus"
    case dolbyVision = "dolbyvision"
    case hlg

    public var label: String {
        switch self {
        case .sdr: "SDR"
        case .hdr10: "HDR10"
        case .hdr10Plus: "HDR10+"
        case .dolbyVision: "Dolby Vision"
        case .hlg: "HLG"
        }
    }
}

/// A server-indexed premium audio format. The raw values deliberately describe
/// formats rather than device output capabilities.
public enum VideoAudioFormat: String, CaseIterable, Hashable, Sendable {
    case atmos
    case trueHD = "truehd"
    case dtsHD = "dtshd"
    case dts
    case dolbyDigitalPlus = "ddplus"
    case dolbyDigital = "dd"
    case aac

    public var label: String {
        switch self {
        case .atmos: "Dolby Atmos"
        case .trueHD: "Dolby TrueHD"
        case .dtsHD: "DTS-HD"
        case .dts: "DTS"
        case .dolbyDigitalPlus: "Dolby Digital+"
        case .dolbyDigital: "Dolby Digital"
        case .aac: "AAC"
        }
    }
}

public struct VideoFilters: Equatable, Sendable {
    public var genre: String
    public var year: Int?
    public var unwatchedOnly: Bool
    public var favoritesOnly: Bool
    public var resolution: VideoResolution?
    public var hdr: VideoHDR?
    public var audio: VideoAudioFormat?
    /// Matches an IMAX release marker in the source filename or edition metadata.
    /// Server matching deliberately does not infer IMAX from aspect ratio.
    public var imaxOnly: Bool

    public init(genre: String = "", year: Int? = nil, unwatchedOnly: Bool = false, favoritesOnly: Bool = false,
                resolution: VideoResolution? = nil, hdr: VideoHDR? = nil, audio: VideoAudioFormat? = nil,
                imaxOnly: Bool = false) {
        self.genre = genre
        self.year = year
        self.unwatchedOnly = unwatchedOnly
        self.favoritesOnly = favoritesOnly
        self.resolution = resolution
        self.hdr = hdr
        self.audio = audio
        self.imaxOnly = imaxOnly
    }
}
