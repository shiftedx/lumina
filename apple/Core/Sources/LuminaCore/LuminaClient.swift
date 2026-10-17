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

    func url(origin: URL, path: String, query: [URLQueryItem] = []) -> URL? {
        guard var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false) else { return nil }
        parts.path = path
        parts.queryItems = query.isEmpty ? nil : query
        return parts.url
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

    func items(origin: URL, token: String, deviceID: String, search: String, start: Int) async throws -> ItemPage {
        var query = [
            URLQueryItem(name: "IncludeItemTypes", value: "Movie,Episode,Video"),
            URLQueryItem(name: "Recursive", value: "true"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags"),
            URLQueryItem(name: "SortBy", value: "SortName"),
            URLQueryItem(name: "StartIndex", value: String(start)),
            URLQueryItem(name: "Limit", value: "50"),
        ]
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

    func playbackInfo(origin: URL, token: String, deviceID: String, item: MediaItem, forceTranscode: Bool) async throws -> PlaybackInfo {
        let profile: [String: Any] = [
            "DirectPlayProfiles": [["Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3"]],
            "TranscodingProfiles": [["Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": "h264", "AudioCodec": "aac"]],
            "SubtitleProfiles": [["Format": "vtt", "Method": "External"]],
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
            return PlaybackRequest(id: result.playSessionID, item: item, url: direct, startPositionSeconds: item.resumeSeconds, mediaSourceID: source.id, isTranscoding: false, methodLabel: "Direct")
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

    private func request(origin: URL, path: String, method: String = "GET", query: [URLQueryItem] = [], token: String? = nil, deviceID: String = "", body: [String: Any]? = nil) -> URLRequest {
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

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        guard (200..<300).contains(response.statusCode) else { throw LuminaError.http(response.statusCode) }
        return data
    }

    private func json<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data = try await send(request)
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw LuminaError.malformedResponse }
    }
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
    let supportsTranscoding: Bool
    let transcodingURL: String?
    enum CodingKeys: String, CodingKey {
        case id = "Id", supportsDirectPlay = "SupportsDirectPlay", supportsTranscoding = "SupportsTranscoding", transcodingURL = "TranscodingUrl"
    }
}
