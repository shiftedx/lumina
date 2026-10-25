import Foundation

public struct MusicTrack: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var title: String
    public var artist: String
    public var albumArtist: String
    public var album: String
    public var albumID: String?
    public var artistID: String?
    public var number: Int?
    public var disc: Int?
    public var duration: Double?
    public var artworkURL: String?
    public var audioURL: String
    public var codec: String?
    public var sampleRate: Int?
    public var bitDepth: Int?
    public var channels: Int?
    public var gainDB: Double?
    public var peak: Double?
    public var albumGainDB: Double?
    public var albumPeak: Double?
    public var gainIsManual: Bool
    public var genres: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, artist, album, number, disc, duration, codec, channels, peak, genres
        case albumArtist = "album_artist", albumID = "album_id", artistID = "artist_id"
        case artworkURL = "artwork_url", audioURL = "audio_url", sampleRate = "sample_rate"
        case bitDepth = "bit_depth", gainDB = "gain_db", albumGainDB = "album_gain_db"
        case albumPeak = "album_peak", gainIsManual = "gain_is_manual"
    }

    public var nativeID: String { id.replacingOccurrences(of: "-", with: "").lowercased() }
    public var mediaItem: MediaItem {
        MediaItem(id: nativeID, name: title, type: "Audio", overview: nil, productionYear: nil,
                  seriesName: nil, seriesID: nil, seasonID: nil, collectionType: nil, indexNumber: number,
                  parentIndexNumber: disc, runTimeTicks: duration.map { Int64(min(604800, max(0, $0)) * tickScale) },
                  userData: nil, imageTags: artworkURL == nil ? nil : ["Primary": "music"],
                  backdropImageTags: nil, parentBackdropItemID: nil, parentBackdropImageTags: nil,
                  album: album, albumID: albumID?.replacingOccurrences(of: "-", with: ""),
                  albumPrimaryImageTag: artworkURL == nil ? nil : "music", artists: [artist],
                  albumArtist: albumArtist, childCount: nil, container: codec)
    }
}

extension MusicTrack {
    /// Local playback metadata for an audio file retained in the member's downloads.
    public init(downloadedItem item: MediaItem) {
        id = item.id
        title = item.name
        artist = item.artists?.joined(separator: ", ") ?? item.albumArtist ?? ""
        albumArtist = item.albumArtist ?? artist
        album = item.album ?? ""
        albumID = item.albumID
        artistID = nil
        number = item.indexNumber
        disc = item.parentIndexNumber
        duration = item.durationSeconds
        artworkURL = nil
        audioURL = ""
        codec = item.container
        sampleRate = nil; bitDepth = nil; channels = nil
        gainDB = nil; peak = nil; albumGainDB = nil; albumPeak = nil
        gainIsManual = false
        genres = item.genres ?? []
    }

}

public struct MusicPlaylistRules: Codable, Hashable, Sendable {
    public var genres: [String] = []
    public var artistID: String?
    public var yearFrom: Int?
    public var yearTo: Int?
    public var favorites = false
    public var unplayed = false
    public init(genres: [String] = [], artistID: String? = nil, yearFrom: Int? = nil, yearTo: Int? = nil, favorites: Bool = false, unplayed: Bool = false) {
        self.genres = genres; self.artistID = artistID; self.yearFrom = yearFrom
        self.yearTo = yearTo; self.favorites = favorites; self.unplayed = unplayed
    }
    enum CodingKeys: String, CodingKey {
        case genres, favorites, unplayed
        case artistID = "artist_id", yearFrom = "year_from", yearTo = "year_to"
    }
}

public struct MusicPlaylist: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var shared: Bool
    public var rules: MusicPlaylistRules?
    public var revision: Int
    public var count: Int?
    public var canEdit: Bool
    enum CodingKeys: String, CodingKey {
        case id, name, shared, rules, revision, count
        case canEdit = "can_edit"
    }
}

public struct MusicCue: Codable, Hashable, Sendable {
    public var start: Double
    public var text: String
}

public struct MusicLyrics: Codable, Sendable {
    public var plain: String?
    public var cues: [MusicCue] = []
    public var source: String?
    public var instrumental = false
    public var refreshing = false
    public var error: String?
    public var retryAfterSeconds: Int?
    public init() {}
    enum CodingKeys: String, CodingKey {
        case plain, cues, source, instrumental, refreshing, error
        case retryAfterSeconds = "retry_after_seconds"
    }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        plain = try values.decodeIfPresent(String.self, forKey: .plain)
        cues = try values.decodeIfPresent([MusicCue].self, forKey: .cues) ?? []
        source = try values.decodeIfPresent(String.self, forKey: .source)
        instrumental = try values.decodeIfPresent(Bool.self, forKey: .instrumental) ?? false
        refreshing = try values.decodeIfPresent(Bool.self, forKey: .refreshing) ?? false
        error = try values.decodeIfPresent(String.self, forKey: .error)
        retryAfterSeconds = try values.decodeIfPresent(Int.self, forKey: .retryAfterSeconds)
    }
}

public struct MusicOutput: Codable, Hashable, Sendable {
    public var kind: String
    public var ticketID: String?
    public var speakerID: String?
    public var queueIndices: [Int]?
    public init(kind: String = "browser", ticketID: String? = nil, speakerID: String? = nil, queueIndices: [Int]? = nil) {
        self.kind = kind; self.ticketID = ticketID; self.speakerID = speakerID; self.queueIndices = queueIndices
    }
    enum CodingKeys: String, CodingKey {
        case kind
        case ticketID = "ticket_id", speakerID = "speaker_id", queueIndices = "queue_indices"
    }
}

public struct MusicQueueEntry: Codable, Hashable, Sendable {
    public var kind: String
    public var itemID: String?
    public var requestID: String?
    public var title: String?
    public var status: String?
    public var state: String?
    public var name: String?
    public var message: String?
    public var tracks: [MusicTrack]?
    public init(kind: String, itemID: String? = nil, requestID: String? = nil) {
        self.kind = kind; self.itemID = itemID; self.requestID = requestID
    }
    enum CodingKeys: String, CodingKey {
        case kind, title, status, state, name, message, tracks
        case itemID = "item_id", requestID = "request_id"
    }
}

public struct MusicListeningSnapshot: Codable, Sendable {
    public var revision: Int
    public var queue: [MusicTrack]
    public var currentIndex: Int?
    public var positionSeconds: Double
    public var repeatMode: String
    public var shuffle: Bool
    public var activeOutput: MusicOutput
    public var updatedAt: String?
    public var removedCount: Int
    public var entries: [MusicQueueEntry]
    public var entryIndex: Int?
    public var unavailablePolicy: String
    public init(revision: Int = 0, queue: [MusicTrack] = [], currentIndex: Int? = nil, positionSeconds: Double = 0, repeatMode: String = "off", shuffle: Bool = false, activeOutput: MusicOutput = .init(), entries: [MusicQueueEntry] = [], entryIndex: Int? = nil, unavailablePolicy: String = "skip") {
        self.revision = revision; self.queue = queue; self.currentIndex = currentIndex
        self.positionSeconds = positionSeconds; self.repeatMode = repeatMode; self.shuffle = shuffle
        self.activeOutput = activeOutput; self.entries = entries; self.entryIndex = entryIndex
        self.unavailablePolicy = unavailablePolicy; self.removedCount = 0
    }
    enum CodingKeys: String, CodingKey {
        case revision, queue, shuffle, entries
        case currentIndex = "current_index", positionSeconds = "position_seconds", repeatMode = "repeat"
        case activeOutput = "active_output", updatedAt = "updated_at", removedCount = "removed_count"
        case entryIndex = "entry_index", unavailablePolicy = "unavailable_policy"
    }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        revision = try values.decode(Int.self, forKey: .revision)
        queue = try values.decode([MusicTrack].self, forKey: .queue)
        currentIndex = try values.decodeIfPresent(Int.self, forKey: .currentIndex)
        positionSeconds = try values.decode(Double.self, forKey: .positionSeconds)
        repeatMode = try values.decode(String.self, forKey: .repeatMode)
        shuffle = try values.decode(Bool.self, forKey: .shuffle)
        activeOutput = try values.decode(MusicOutput.self, forKey: .activeOutput)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        removedCount = try values.decodeIfPresent(Int.self, forKey: .removedCount) ?? 0
        entries = try values.decodeIfPresent([MusicQueueEntry].self, forKey: .entries) ?? []
        entryIndex = try values.decodeIfPresent(Int.self, forKey: .entryIndex)
        unavailablePolicy = try values.decodeIfPresent(String.self, forKey: .unavailablePolicy) ?? "skip"
    }
}

public struct MusicSpeaker: Decodable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var model: String
    public var coordinatorID: String
    enum CodingKeys: String, CodingKey { case id, name, model; case coordinatorID = "coordinator_id" }
}

public struct MusicOutputTrack: Decodable, Sendable {
    public var durationSeconds: Double?
    public var positionSeconds: Double?
    public var trackNumber: Int?
    enum CodingKeys: String, CodingKey {
        case durationSeconds = "duration_seconds", positionSeconds = "position_seconds", trackNumber = "track_number"
    }
}

public struct MusicOutputStatus: Decodable, Sendable {
    public var playing: Bool?
    public var transportState: String?
    public var state: String?
    public var volume: Int?
    public var positionSeconds: Double?
    public var track: MusicOutputTrack?
    enum CodingKeys: String, CodingKey {
        case playing, state, volume, track
        case transportState = "transport_state", positionSeconds = "position_seconds"
    }
}

public struct MusicCastResult: Decodable, Sendable {
    public var ticketID: String
    public var session: MusicListeningSnapshot
    public var queueIndices: [Int]
    public var requestedCount: Int
    public var queuedCount: Int
    public var excludedCount: Int
    public var currentIndex: Int
    public var positionSeconds: Double
    enum CodingKeys: String, CodingKey {
        case session
        case ticketID = "ticket_id", queueIndices = "queue_indices"
        case requestedCount = "requested_count", queuedCount = "queued_count", excludedCount = "excluded_count"
        case currentIndex = "current_index", positionSeconds = "position_seconds"
    }
}

public enum MusicAPIError: Error, LocalizedError {
    case conflict(MusicListeningSnapshot)
    case http(Int)
    public var errorDescription: String? {
        switch self {
        case .conflict: "Your queue changed on another device. Choose which queue to keep."
        case .http(403): "Your account cannot perform this action. Check access and listening limits."
        case .http(409): "This playlist changed on another screen. Reopen it before saving."
        case .http(let code): "The server returned HTTP \(code). Try again."
        }
    }
}

struct MusicSessionConflict: Decodable {
    struct Detail: Decodable { var session: MusicListeningSnapshot }
    var detail: Detail
}
