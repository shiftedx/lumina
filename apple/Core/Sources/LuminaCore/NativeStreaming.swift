import Foundation
import Observation

public struct NativeSavedStreamingLibrary: Codable, Equatable, Sendable {
    public let available: Bool
    public let providers: [String]
}

public struct NativeRemoteStreamingCapability: Codable, Equatable, Sendable {
    public let available: Bool
    public let providers: [String]
    public let liveAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case available, providers
        case liveAvailable = "live_available"
    }
}

public struct NativeStreamingCapabilities: Codable, Equatable, Sendable {
    public let savedLibrary: NativeSavedStreamingLibrary
    public let remote: NativeRemoteStreamingCapability

    enum CodingKeys: String, CodingKey {
        case savedLibrary = "saved_library"
        case remote
    }
}

struct NativeStreamingCapabilitiesResponse: Codable {
    let streaming: NativeStreamingCapabilities
}

/// A narrow saved-download wall. It deliberately never replaces the primary Library state.
@MainActor @Observable public final class LuminaStreamingLibrary {
    public private(set) var items = [MediaItem]()
    public private(set) var isBusy = false
    public private(set) var hasMore = false
    public private(set) var errorMessage: String?

    private var session: LuminaSession?
    private let client: LuminaClient
    private var nextIndex = 0
    private var channelID: String?
    private var operation = 0

    public init(session: LuminaSession? = nil) {
        self.session = session
        client = LuminaClient()
    }

    init(client: LuminaClient, session: LuminaSession? = nil) {
        self.session = session
        self.client = client
    }

    public func configure(session: LuminaSession?) {
        guard self.session?.accessToken != session?.accessToken || self.session?.serverURL != session?.serverURL || self.session?.userID != session?.userID else { return }
        self.session = session
        items = []
        nextIndex = 0
        channelID = nil
        hasMore = false
        errorMessage = nil
        isBusy = false
    }

    public func load() async {
        guard let active = session, !isBusy else { return }
        let operation = operation
        isBusy = true
        defer { if operation == self.operation { isBusy = false } }
        do {
            let views = try await client.views(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID)
            try Task.checkCancellation()
            guard operation == self.operation, isCurrent(active) else { return }
            guard let channels = views.items.first(where: { $0.name == "Channels" }) else {
                items = []; hasMore = false; return
            }
            channelID = channels.id
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                                              search: "", start: 0, types: "Episode", parentID: channels.id,
                                              sourceProvider: "youtube,twitch,kick")
            try Task.checkCancellation()
            guard operation == self.operation, isCurrent(active) else { return }
            items = page.items.filter { $0.isPlayable && !$0.isAudio }
            nextIndex = (page.startIndex ?? 0) + page.items.count
            hasMore = nextIndex < page.total
            errorMessage = nil
        } catch {
            guard operation == self.operation, isCurrent(active), !Task.isCancelled else { return }
            items = []; hasMore = false
            errorMessage = "Saved streaming videos could not be loaded."
        }
    }

    public func loadMore() async {
        guard let active = session, let channelID, hasMore, !isBusy else { return }
        let operation = operation
        isBusy = true
        defer { if operation == self.operation { isBusy = false } }
        do {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                                              search: "", start: nextIndex, types: "Episode", parentID: channelID,
                                              sourceProvider: "youtube,twitch,kick")
            try Task.checkCancellation()
            guard operation == self.operation, isCurrent(active) else { return }
            let seen = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { $0.isPlayable && !$0.isAudio && !seen.contains($0.id) })
            nextIndex = (page.startIndex ?? nextIndex) + page.items.count
            hasMore = nextIndex < page.total
        } catch {
            guard operation == self.operation, isCurrent(active), !Task.isCancelled else { return }
            errorMessage = "More saved streaming videos could not be loaded."
        }
    }

    private func isCurrent(_ value: LuminaSession) -> Bool {
        session?.serverURL == value.serverURL && session?.accessToken == value.accessToken && session?.userID == value.userID
    }
}

public struct NativeRemotePlayback: Codable, Equatable, Sendable, Identifiable {
    public let status: String
    public let sessionID: String
    private let playbackURLValue: String?
    private let fallbackURLValue: String?
    public let seekable: Bool
    public let live: Bool

    public var id: String { sessionID }
    public var playbackURL: URL? { Self.playbackURL(playbackURLValue) }
    public var fallbackURL: URL? { Self.httpsURL(fallbackURLValue) }

    enum CodingKeys: String, CodingKey {
        case status
        case sessionID = "session_id"
        case playbackURLValue = "playback_url"
        case fallbackURLValue = "fallback_url"
        case seekable, live
    }

    private static func playbackURL(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value), url.user == nil, url.password == nil else { return nil }
        if url.host == nil { return value.hasPrefix("/Lumina/Streaming/") ? url : nil }
        return ["http", "https"].contains(url.scheme) ? url : nil
    }

    private static func httpsURL(_ value: String?) -> URL? {
        guard let value, let parts = URLComponents(string: value), parts.scheme == "https",
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil else { return nil }
        return parts.url
    }

    func resolved(origin: URL) throws -> NativeRemotePlayback {
        guard sessionID.utf8.count <= 128, !sessionID.isEmpty,
              sessionID.unicodeScalars.allSatisfy({ $0.isASCII && (($0.value >= 48 && $0.value <= 57) || ($0.value >= 65 && $0.value <= 90) || ($0.value >= 97 && $0.value <= 122) || $0 == "-" || $0 == "_") }),
              ["ready", "unsupported"].contains(status) else { throw LuminaError.malformedResponse }
        guard let value = playbackURLValue else {
            guard status == "unsupported" else { throw LuminaError.malformedResponse }
            return self
        }
        guard value.hasPrefix("/Lumina/Streaming/"), let relative = URL(string: value),
              relative.host == nil, relative.user == nil, relative.password == nil,
              let absolute = URL(string: relative.relativeString, relativeTo: origin)?.absoluteURL,
              sameOrigin(origin, absolute) else { throw LuminaError.malformedResponse }
        return NativeRemotePlayback(status: status, sessionID: sessionID, playbackURLValue: absolute.absoluteString,
                                    fallbackURLValue: fallbackURLValue, seekable: seekable, live: live)
    }

    private init(status: String, sessionID: String, playbackURLValue: String?, fallbackURLValue: String?, seekable: Bool, live: Bool) {
        self.status = status
        self.sessionID = sessionID
        self.playbackURLValue = playbackURLValue
        self.fallbackURLValue = fallbackURLValue
        self.seekable = seekable
        self.live = live
    }
}
