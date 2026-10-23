import Foundation

final class LuminaClient: @unchecked Sendable {
    private let session: URLSession
    private let redirectGuard: RedirectGuard

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30
        redirectGuard = RedirectGuard()
        session = URLSession(configuration: configuration, delegate: redirectGuard, delegateQueue: nil)
    }

    init(configuration: URLSessionConfiguration) {
        redirectGuard = RedirectGuard()
        session = URLSession(configuration: configuration, delegate: redirectGuard, delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    func url(origin: URL, path: String, query: [URLQueryItem] = []) -> URL? {
        guard var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false) else { return nil }
        parts.path = path
        parts.queryItems = query.isEmpty ? nil : query
        return parts.url
    }

    func heroFraming(origin: URL, token: String, deviceID: String, backdrop: URL) async throws -> HeroFraming {
        guard sameOrigin(origin, backdrop), let parts = URLComponents(url: backdrop, resolvingAgainstBaseURL: false),
              let tag = parts.queryItems?.first(where: { $0.name == "tag" })?.value else { throw LuminaError.unsafeURL }
        var lookup = request(origin: origin, path: backdrop.path + "/Framing",
                             query: [URLQueryItem(name: "tag", value: tag)], token: token, deviceID: deviceID)
        // Match the web's first-response hold. A late analysis is reused on a later visit.
        lookup.timeoutInterval = 0.15
        return try await json(lookup)
    }

    func publicInfo(origin: URL) async throws -> ServerInfo {
        try await json(request(origin: origin, path: "/System/Info/Public"))
    }

    func authenticate(origin: URL, username: String, password: String, deviceID: String) async throws -> SignedIn {
        let body = ["Username": username, "Pw": password]
        return try await json(request(origin: origin, path: "/Users/AuthenticateByName", method: "POST", deviceID: deviceID, body: body))
    }

    func me(origin: URL, token: String, deviceID: String) async throws -> UserInfo {
        try await json(request(origin: origin, path: "/Users/Me", token: token, deviceID: deviceID))
    }

    func logout(origin: URL, token: String, deviceID: String) async throws {
        _ = try await send(request(origin: origin, path: "/Sessions/Logout", method: "POST", token: token, deviceID: deviceID))
    }

    func items(origin: URL, token: String, deviceID: String, search: String, start: Int, types: String = "Movie,Episode,Video", sort: VideoSort = .name, parentID: String? = nil, filters: VideoFilters = .init()) async throws -> ItemPage {
        var query = [
            URLQueryItem(name: "IncludeItemTypes", value: types),
            URLQueryItem(name: "Recursive", value: "true"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags"),
            URLQueryItem(name: "SortBy", value: sort.field),
            URLQueryItem(name: "SortOrder", value: sort == .name ? "Ascending" : "Descending"),
            URLQueryItem(name: "StartIndex", value: String(start)),
            URLQueryItem(name: "Limit", value: "50"),
        ]
        let genre = filters.genre.trimmingCharacters(in: .whitespacesAndNewlines)
        if !genre.isEmpty { query.append(URLQueryItem(name: "Genres", value: genre)) }
        if let year = filters.year { query.append(URLQueryItem(name: "Years", value: String(year))) }
        if filters.unwatchedOnly { query.append(URLQueryItem(name: "IsPlayed", value: "false")) }
        if filters.favoritesOnly { query.append(URLQueryItem(name: "IsFavorite", value: "true")) }
        if let parentID { query.append(URLQueryItem(name: "ParentId", value: parentID)) }
        if !search.isEmpty { query.append(URLQueryItem(name: "SearchTerm", value: search)) }
        return try await json(request(origin: origin, path: "/Items", query: query, token: token, deviceID: deviceID))
    }

    func item(origin: URL, token: String, deviceID: String, id: String, userID: String? = nil) async throws -> MediaItem {
        let path = userID.map { "/Users/\($0)/Items/\(id)" } ?? "/Items/\(id)"
        return try await json(request(origin: origin, path: path, token: token, deviceID: deviceID))
    }

    func resume(origin: URL, token: String, deviceID: String) async throws -> [MediaItem] {
        let query = [
            URLQueryItem(name: "IncludeItemTypes", value: "Movie,Episode,Video"),
            URLQueryItem(name: "Recursive", value: "true"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags"),
            URLQueryItem(name: "Limit", value: "50"),
        ]
        let page: ItemPage = try await json(request(origin: origin, path: "/UserItems/Resume", query: query, token: token, deviceID: deviceID))
        return page.items
    }

    func views(origin: URL, token: String, deviceID: String) async throws -> ItemPage {
        try await json(request(origin: origin, path: "/UserViews", token: token, deviceID: deviceID))
    }

    func children(origin: URL, token: String, deviceID: String, item: MediaItem, start: Int) async throws -> ItemPage {
        if item.type == "Series" {
            return try await json(request(origin: origin, path: "/Shows/\(item.id)/Seasons", token: token, deviceID: deviceID))
        }
        let query = [URLQueryItem(name: "ParentId", value: item.id), URLQueryItem(name: "IncludeItemTypes", value: "Episode"),
                     URLQueryItem(name: "SortBy", value: "IndexNumber"), URLQueryItem(name: "SortOrder", value: "Ascending"),
                     URLQueryItem(name: "StartIndex", value: String(start)), URLQueryItem(name: "Limit", value: "50"),
                     URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags")]
        return try await json(request(origin: origin, path: "/Items", query: query, token: token, deviceID: deviceID))
    }

    func nextUp(origin: URL, token: String, deviceID: String) async throws -> [MediaItem] {
        let page: ItemPage = try await json(request(origin: origin, path: "/Shows/NextUp", query: [URLQueryItem(name: "Limit", value: "50")], token: token, deviceID: deviceID))
        return page.items
    }

    func updateItemState(origin: URL, token: String, deviceID: String, userID: String, id: String, played: Bool?, favorite: Bool?) async throws {
        var body = [String: Any]()
        if let played { body["Played"] = played }
        if let favorite { body["IsFavorite"] = favorite }
        _ = try await send(request(origin: origin, path: "/Users/\(userID)/Items/\(id)/UserData", method: "POST", token: token, deviceID: deviceID, body: body))
    }

    func audioRequest(origin: URL, token: String, item: MediaItem) throws -> PlaybackRequest {
        let nativeContainers = ["mp3", "aac", "m4a", "mp4", "m4b", "flac", "wav", "wave", "aif", "aiff", "caf", "alac"]
        let compatible = item.container.map { !nativeContainers.contains($0.lowercased()) } ?? false
        var query = [URLQueryItem(name: "ApiKey", value: token)]
        if compatible { query.append(URLQueryItem(name: "format", value: "flac")) }
        guard item.isAudio, let stream = url(origin: origin, path: "/Audio/\(item.id)/stream", query: query), sameOrigin(origin, stream) else { throw LuminaError.unsafeURL }
        return PlaybackRequest(id: UUID().uuidString, item: item, url: stream, startPositionSeconds: 0, mediaSourceID: item.id, isTranscoding: false, methodLabel: compatible ? "Compatible audio" : "Direct audio")
    }

    func playbackInfo(origin: URL, token: String, deviceID: String, item: MediaItem, forceTranscode: Bool) async throws -> PlaybackInfo {
        let profile: [String: Any] = [
            "DirectPlayProfiles": [["Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,mp3,alac,flac"],
                                   ["Type": "Video", "Container": "mkv", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,alac,flac"]],
            "TranscodingProfiles": [["Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"]],
            "SubtitleProfiles": [["Format": "vtt", "Method": "External"]],
            "CodecProfiles": [
                ["Type": "Video", "Codec": "h264", "Conditions": [["Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "8"]]],
                ["Type": "Video", "Codec": "hevc", "Conditions": [["Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "10"]]],
                ["Type": "VideoAudio", "Codec": "flac", "Conditions": [["Property": "AudioBitDepth", "Condition": "LessThanEqual", "Value": "24"]]],
            ],
        ]
        let body: [String: Any] = [
            "DeviceProfile": profile,
            "StartTimeTicks": Int64(max(0, item.resumeSeconds) * tickScale),
            "EnableDirectPlay": !forceTranscode,
        ]
        return try await json(request(origin: origin, path: "/Items/\(item.id)/PlaybackInfo", method: "POST", token: token, deviceID: deviceID, body: body))
    }

    func playbackRequest(origin: URL, token: String, item: MediaItem, result: PlaybackInfo) throws -> PlaybackRequest {
        guard let source = result.mediaSources.first else { throw LuminaError.malformedResponse }
        if source.supportsDirectPlay {
            guard let direct = url(origin: origin, path: "/Videos/\(item.id)/stream", query: [
                URLQueryItem(name: "MediaSourceId", value: source.id),
                URLQueryItem(name: "Static", value: "true"),
                URLQueryItem(name: "ApiKey", value: token),
            ]), sameOrigin(origin, direct) else { throw LuminaError.unsafeURL }
            return PlaybackRequest(id: result.playSessionID, item: item, url: direct, startPositionSeconds: item.resumeSeconds, mediaSourceID: source.id, isTranscoding: false, methodLabel: "Direct", usesClientDemux: !item.isAudio && ["mkv", "matroska"].contains((source.container ?? item.container ?? "").lowercased()))
        }
        guard source.supportsTranscoding, let raw = source.transcodingURL,
              let hls = URL(string: raw, relativeTo: origin)?.absoluteURL, sameOrigin(origin, hls) else { throw LuminaError.unsafeURL }
        return PlaybackRequest(id: result.playSessionID, item: item, url: hls, startPositionSeconds: item.resumeSeconds, mediaSourceID: source.id, isTranscoding: true, methodLabel: "HLS")
    }

    func report(origin: URL, token: String, deviceID: String, event: PlaybackEvent, request: PlaybackRequest, positionSeconds: Double, isPaused: Bool) async throws {
        let path: String
        switch event {
        case .start: path = "/Sessions/Playing"
        case .progress: path = "/Sessions/Playing/Progress"
        case .stop: path = "/Sessions/Playing/Stopped"
        }
        let body: [String: Any] = [
            "ItemId": request.item.id,
            "MediaSourceId": request.mediaSourceID,
            "PlaySessionId": request.id,
            "PositionTicks": Int64(max(0, positionSeconds) * tickScale),
            "IsPaused": isPaused,
        ]
        var httpRequest = self.request(origin: origin, path: path, method: "POST", token: token, deviceID: deviceID, body: body)
        httpRequest.timeoutInterval = 5
        _ = try await send(httpRequest)
    }

    func request(origin: URL, path: String, method: String = "GET", query: [URLQueryItem] = [], token: String? = nil, deviceID: String = "", body: [String: Any]? = nil) -> URLRequest {
        var request = URLRequest(url: url(origin: origin, path: path, query: query)!)
        request.httpMethod = method
        request.setValue(mediaBrowserHeader(token: token, deviceID: deviceID), forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func mediaBrowserHeader(token: String?, deviceID: String) -> String {
        var values = [
            "Client=\"Lumina Apple\"",
            "Device=\"\(platformName)\"",
            "DeviceId=\"\(deviceID)\"",
            "Version=\"1.0\"",
        ]
        if let token { values.append("Token=\"\(token)\"") }
        return "MediaBrowser " + values.joined(separator: ", ")
    }

    func response(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        return (data, response)
    }

    func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        guard (200..<300).contains(response.statusCode) else {
            if response.statusCode == 409, let conflict = try? JSONDecoder().decode(MusicSessionConflict.self, from: data) {
                throw MusicAPIError.conflict(conflict.detail.session)
            }
            throw LuminaError.http(response.statusCode)
        }
        return data
    }

    func bookImage(_ request: URLRequest) async throws -> Data {
        let guardDelegate = BookImageDownloadGuard(origin: request.url!)
        let (file, response) = try await session.download(for: request, delegate: guardDelegate)
        defer { try? FileManager.default.removeItem(at: file) }
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        guard response.statusCode == 200 else { throw LuminaError.http(response.statusCode) }
        guard ["image/jpeg", "image/png", "image/gif", "image/webp"].contains(response.mimeType ?? ""),
              let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize,
              size > 0, size <= BookPagePreparation.maximumEncodedBytes else { throw LuminaError.malformedResponse }
        return try Data(contentsOf: file)
    }

    private func json<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data = try await send(request)
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw LuminaError.malformedResponse }
    }
}

private final class BookImageDownloadGuard: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let origin: URL
    init(origin: URL) { self.origin = origin }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let target = request.url, sameOrigin(origin, target), target.user == nil, target.password == nil else {
            completionHandler(nil); task.cancel(); return
        }
        completionHandler(request)
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if max(totalBytesWritten, totalBytesExpectedToWrite) > Int64(BookPagePreparation.maximumEncodedBytes) { downloadTask.cancel() }
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) { }
}

private final class RedirectGuard: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let from = task.currentRequest?.url, let to = request.url, sameOrigin(from, to) else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}

private let platformName: String = {
    #if os(tvOS)
    return "Lumina tvOS"
    #elseif os(iOS)
    return "Lumina iOS"
    #else
    return "Lumina macOS"
    #endif
}()

private func sameOrigin(_ left: URL, _ right: URL) -> Bool {
    let a = URLComponents(url: left, resolvingAgainstBaseURL: false)
    let b = URLComponents(url: right, resolvingAgainstBaseURL: false)
    return a?.scheme?.lowercased() == b?.scheme?.lowercased()
        && a?.host?.lowercased() == b?.host?.lowercased()
        && effectivePort(a) == effectivePort(b)
}

private func effectivePort(_ components: URLComponents?) -> Int? {
    guard let components else { return nil }
    return components.port ?? (components.scheme?.lowercased() == "https" ? 443 : components.scheme?.lowercased() == "http" ? 80 : nil)
}

struct ServerInfo: Decodable {
    let serverName: String
    enum CodingKeys: String, CodingKey { case serverName = "ServerName" }
}

struct SignedIn: Decodable {
    let token: String
    let serverName: String
    enum CodingKeys: String, CodingKey {
        case token = "AccessToken"
        case user = "User"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        token = try values.decode(String.self, forKey: .token)
        serverName = try values.decode(UserInfo.self, forKey: .user).serverName ?? "Lumina"
    }
}

struct UserInfo: Decodable {
    let id: String
    let name: String
    let serverName: String?
    enum CodingKeys: String, CodingKey { case id = "Id", name = "Name", serverName = "ServerName" }
}

struct ItemPage: Decodable {
    let items: [MediaItem]
    let total: Int
    enum CodingKeys: String, CodingKey { case items = "Items", total = "TotalRecordCount" }
}

struct PlaybackInfo: Decodable {
    let mediaSources: [MediaSource]
    let playSessionID: String
    enum CodingKeys: String, CodingKey { case mediaSources = "MediaSources", playSessionID = "PlaySessionId" }
}

struct MediaSource: Decodable {
    let id: String
    let supportsDirectPlay: Bool
    let container: String?
    let supportsTranscoding: Bool
    let transcodingURL: String?
    enum CodingKeys: String, CodingKey {
        case container = "Container"
        case id = "Id", supportsDirectPlay = "SupportsDirectPlay", supportsTranscoding = "SupportsTranscoding", transcodingURL = "TranscodingUrl"
    }
}
