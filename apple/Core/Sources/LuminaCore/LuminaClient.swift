import Foundation

final class LuminaClient: @unchecked Sendable {
    private let session: URLSession
    private let redirectGuard: RedirectGuard
    private let videoCapabilities: @Sendable () -> NativeVideoCapabilities

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30
        redirectGuard = RedirectGuard()
        session = URLSession(configuration: configuration, delegate: redirectGuard, delegateQueue: nil)
        videoCapabilities = NativeVideoCapabilities.current
    }

    init(configuration: URLSessionConfiguration,
         videoCapabilities: @escaping @Sendable () -> NativeVideoCapabilities = NativeVideoCapabilities.current) {
        redirectGuard = RedirectGuard()
        session = URLSession(configuration: configuration, delegate: redirectGuard, delegateQueue: nil)
        self.videoCapabilities = videoCapabilities
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

    func claimMobileConnect(origin: URL, token: String, name: String) async throws -> MobileConnectClaim {
        try await json(request(origin: origin, path: "/api/mobile-connect/claim", method: "POST", body: ["token": token, "name": name]))
    }

    func me(origin: URL, token: String, deviceID: String) async throws -> UserInfo {
        try await json(request(origin: origin, path: "/Users/Me", token: token, deviceID: deviceID))
    }

    func logout(origin: URL, token: String, deviceID: String) async throws {
        _ = try await send(request(origin: origin, path: "/Sessions/Logout", method: "POST", token: token, deviceID: deviceID))
    }

    func prepareChromecast(origin: URL, token: String, deviceID: String, itemID: String,
                           positionSeconds: Double, audioIndex: Int?, subtitle: String?) async throws -> ChromecastTicket {
        guard !itemID.isEmpty, itemID.utf8.count <= 64,
              positionSeconds.isFinite, (0...604_800).contains(positionSeconds),
              audioIndex.map({ $0 >= 0 }) ?? true,
              subtitle.map({ !$0.isEmpty && $0.utf8.count <= 128 }) ?? true else {
            throw LuminaError.malformedResponse
        }
        var body: [String: Any] = ["item_id": itemID, "position_seconds": positionSeconds]
        if let audioIndex { body["audio_index"] = audioIndex }
        if let subtitle { body["subtitle"] = subtitle }
        let response: ChromecastTicketResponse = try await json(request(
            origin: origin, path: "/Lumina/Cast/tickets", method: "POST", token: token,
            deviceID: deviceID, body: body
        ))
        let ticket = try response.ticket()
        guard receiverCapability(in: ticket.mediaURL) != token,
              ticket.subtitle.map({ receiverCapability(in: $0.url) != token }) ?? true else {
            throw LuminaError.malformedResponse
        }
        return ticket
    }

    func renewChromecast(origin: URL, token: String, deviceID: String, ticketID: UUID) async throws {
        let response: ChromecastRenewalResponse = try await json(request(
            origin: origin, path: "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())/renew",
            method: "POST", token: token, deviceID: deviceID
        ))
        guard response.validates(ticketID: ticketID) else { throw LuminaError.malformedResponse }
    }

    func revokeChromecast(origin: URL, token: String, deviceID: String, ticketID: UUID) async throws {
        _ = try await send(request(
            origin: origin, path: "/Lumina/Cast/tickets/\(ticketID.uuidString.lowercased())",
            method: "DELETE", token: token, deviceID: deviceID
        ))
    }

    func prepareMusicReceiver(origin: URL, token: String, deviceID: String, itemIDs: [String],
                              currentIndex: Int, positionSeconds: Double) async throws -> MusicReceiverTicket {
        guard !itemIDs.isEmpty, itemIDs.count <= 100, itemIDs.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 64 }),
              itemIDs.indices.contains(currentIndex), positionSeconds.isFinite,
              (0...604_800).contains(positionSeconds) else {
            throw LuminaError.malformedResponse
        }
        let response: MusicReceiverTicketResponse = try await json(request(
            origin: origin, path: "/Lumina/Music/Receiver/tickets", method: "POST", token: token,
            deviceID: deviceID, body: ["item_ids": itemIDs, "current_index": currentIndex,
                                        "position_seconds": positionSeconds]
        ))
        let ticket = try response.ticket(currentIndex: currentIndex)
        guard receiverCapability(in: ticket.ticket.mediaURL) != token,
              ticket.queue.allSatisfy({ receiverCapability(in: $0.mediaURL) != token }) else {
            throw LuminaError.malformedResponse
        }
        return ticket
    }

    func renewMusicReceiver(origin: URL, token: String, deviceID: String, ticketID: UUID) async throws {
        let response: ChromecastRenewalResponse = try await json(request(
            origin: origin, path: "/Lumina/Music/Receiver/tickets/\(ticketID.uuidString.lowercased())/renew",
            method: "POST", token: token, deviceID: deviceID
        ))
        guard response.validates(ticketID: ticketID) else { throw LuminaError.malformedResponse }
    }

    func revokeMusicReceiver(origin: URL, token: String, deviceID: String, ticketID: UUID) async throws {
        _ = try await send(request(
            origin: origin, path: "/Lumina/Music/Receiver/tickets/\(ticketID.uuidString.lowercased())",
            method: "DELETE", token: token, deviceID: deviceID
        ))
    }

    func items(origin: URL, token: String, deviceID: String, search: String, start: Int, types: String = "Movie,Episode,Video", sort: VideoSort = .name, parentID: String? = nil, sourceProvider: String? = nil, luminaCategory: String? = nil, filters: VideoFilters = .init(), catalog: Bool = false, limit: Int = 50) async throws -> ItemPage {
        let query = libraryQuery(search: search, start: start, types: types, sort: sort, parentID: parentID,
                                 sourceProvider: sourceProvider, luminaCategory: luminaCategory, filters: filters)
        let projected = catalog ? query.filter { $0.name != "Fields" && $0.name != "Limit" } + [
            URLQueryItem(name: "Fields", value: "LuminaCatalog"),
            URLQueryItem(name: "Limit", value: String(min(1000, max(1, limit)))),
        ] : query
        return try await json(request(origin: origin, path: "/Items", query: projected, token: token, deviceID: deviceID))
    }

    func libraryAlphabet(origin: URL, token: String, deviceID: String, types: String, parentID: String? = nil, luminaCategory: String? = nil, filters: VideoFilters = .init()) async throws -> LibraryAlphabetPage {
        let query = libraryQuery(search: "", start: 0, types: types, sort: .name, parentID: parentID,
                                 sourceProvider: nil, luminaCategory: luminaCategory, filters: filters)
        return try await json(request(origin: origin, path: "/Lumina/Library/alphabet", query: query, token: token, deviceID: deviceID))
    }

    func supportsTechnicalVideoFilters(origin: URL, token: String, deviceID: String) async throws -> Bool {
        let capability: TechnicalVideoFiltersCapability = try await json(request(
            origin: origin, path: "/Items/Filters2",
            query: [
                URLQueryItem(name: "IncludeItemTypes", value: "Movie"),
                URLQueryItem(name: "Recursive", value: "true"),
                URLQueryItem(name: "Fields", value: "LuminaTechnicalFilters"),
            ],
            token: token, deviceID: deviceID
        ))
        return capability.luminaTechnicalFilters == true
    }

    private struct TechnicalVideoFiltersCapability: Decodable {
        let luminaTechnicalFilters: Bool?

        enum CodingKeys: String, CodingKey {
            case luminaTechnicalFilters = "LuminaTechnicalFilters"
        }
    }

    private func libraryQuery(search: String, start: Int, types: String, sort: VideoSort, parentID: String?, sourceProvider: String?, luminaCategory: String?, filters: VideoFilters) -> [URLQueryItem] {
        var query = [
            URLQueryItem(name: "IncludeItemTypes", value: types),
            URLQueryItem(name: "Recursive", value: "true"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags,People"),
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
        if let resolution = filters.resolution { query.append(URLQueryItem(name: "LuminaResolution", value: resolution.rawValue)) }
        if let hdr = filters.hdr { query.append(URLQueryItem(name: "LuminaHDR", value: hdr.rawValue)) }
        if let audio = filters.audio { query.append(URLQueryItem(name: "LuminaAudio", value: audio.rawValue)) }
        if filters.imaxOnly { query.append(URLQueryItem(name: "LuminaIMAX", value: "true")) }
        if let parentID { query.append(URLQueryItem(name: "ParentId", value: parentID)) }
        if let sourceProvider { query.append(URLQueryItem(name: "SourceProvider", value: sourceProvider)) }
        if let luminaCategory { query.append(URLQueryItem(name: "LuminaCategory", value: luminaCategory)) }
        if !search.isEmpty { query.append(URLQueryItem(name: "SearchTerm", value: search)) }
        return query
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

    func navigationCategories(origin: URL, token: String, deviceID: String) async throws -> Set<String> {
        let result: NativeNavigation = try await json(request(origin: origin, path: "/Lumina/Settings/navigation", token: token, deviceID: deviceID))
        return Set(result.categories)
    }

    private struct NativeNavigation: Decodable { let categories: [String] }

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

    func relatedItems(origin: URL, token: String, deviceID: String, itemID: String) async throws -> [MediaItem] {
        let query = [
            URLQueryItem(name: "Limit", value: "12"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags,People"),
        ]
        let page: ItemPage = try await json(request(
            origin: origin,
            path: "/Items/\(itemID)/Similar",
            query: query,
            token: token,
            deviceID: deviceID
        ))
        return page.items
    }

    func nextEpisode(origin: URL, token: String, deviceID: String, after item: MediaItem) async throws -> MediaItem? {
        guard item.type == "Episode", let seriesID = item.seriesID, !seriesID.isEmpty else { return nil }
        let query = [
            URLQueryItem(name: "StartItemId", value: item.id),
            URLQueryItem(name: "Limit", value: "2"),
            URLQueryItem(name: "Fields", value: "Overview,UserData,ImageTags"),
        ]
        let page: ItemPage = try await json(request(origin: origin, path: "/Shows/\(seriesID)/Episodes", query: query, token: token, deviceID: deviceID))
        guard page.items.first?.id == item.id else { return nil }
        guard let following = page.items.dropFirst().first,
              following.type == "Episode", following.seriesID == seriesID else { return nil }
        return following
    }

    func playbackMarkers(origin: URL, token: String, deviceID: String, mediaSourceID: String,
                         durationSeconds: Double) async throws -> [PlaybackMarker] {
        let page: MediaSegmentsPage = try await json(request(
            origin: origin, path: "/MediaSegments/\(mediaSourceID)", token: token, deviceID: deviceID,
        ))
        return sanitizedPlaybackMarkers(page.items, durationSeconds: durationSeconds)
    }

    func updateItemState(origin: URL, token: String, deviceID: String, userID: String, id: String, played: Bool?, favorite: Bool?) async throws {
        var body = [String: Any]()
        if let played { body["Played"] = played }
        if let favorite { body["IsFavorite"] = favorite }
        _ = try await send(request(origin: origin, path: "/Users/\(userID)/Items/\(id)/UserData", method: "POST", token: token, deviceID: deviceID, body: body))
    }

    func audioRequest(origin: URL, token: String, item: MediaItem, forceCompatible: Bool = false) throws -> PlaybackRequest {
        let nativeContainers = ["mp3", "aac", "m4a", "mp4", "m4b", "flac", "wav", "wave", "aif", "aiff", "caf", "alac"]
        let compatible = forceCompatible || (item.container.map { !nativeContainers.contains($0.lowercased()) } ?? false)
        var query = [URLQueryItem(name: "ApiKey", value: token)]
        if compatible { query.append(URLQueryItem(name: "format", value: "flac")) }
        guard item.isAudio, let stream = url(origin: origin, path: "/Audio/\(item.id)/stream", query: query), sameOrigin(origin, stream) else { throw LuminaError.unsafeURL }
        return PlaybackRequest(id: UUID().uuidString, item: item, url: stream, startPositionSeconds: 0, mediaSourceID: item.id, isTranscoding: false, methodLabel: compatible ? "Compatible audio" : "Direct audio", sourceContainer: item.container)
    }

    func playbackInfo(origin: URL, token: String, deviceID: String, item: MediaItem, forceTranscode: Bool,
                      quality: PlaybackQuality = .automatic,
                      audioStreamIndex: Int? = nil, subtitleStreamIndex: Int? = nil,
                      mediaSourceID: String? = nil, startPositionSeconds: Double? = nil,
                      forceCompatibleVideo: Bool = false) async throws -> PlaybackInfo {
        let capabilities = forceCompatibleVideo ? .compatibility : videoCapabilities()
        var profile = nativePlaybackProfile(capabilities, quality: quality, forceCompatibleVideo: forceCompatibleVideo)
        if let maximumBitRate = quality.maximumBitRate { profile["MaxStreamingBitrate"] = maximumBitRate }
        let start = try boundedStartPosition(startPositionSeconds, item: item)
        var body: [String: Any] = [
            "DeviceProfile": profile,
            "StartTimeTicks": Int64(start * tickScale),
            "EnableDirectPlay": !forceTranscode,
        ]
        if let maximumBitRate = quality.maximumBitRate { body["MaxStreamingBitrate"] = maximumBitRate }
        if let audioStreamIndex, audioStreamIndex >= 0 { body["AudioStreamIndex"] = audioStreamIndex }
        if let subtitleStreamIndex { body["SubtitleStreamIndex"] = subtitleStreamIndex }
        if let mediaSourceID { body["MediaSourceId"] = mediaSourceID }
        return try await json(request(origin: origin, path: "/Items/\(item.id)/PlaybackInfo", method: "POST", token: token, deviceID: deviceID, body: body))
    }

    private func nativePlaybackProfile(_ capabilities: NativeVideoCapabilities, quality: PlaybackQuality,
                                       forceCompatibleVideo: Bool) -> [String: Any] {
        let ordered = NativeVideoCapabilities.Codec.allCases
        let direct = ordered.filter { !capabilities.ranges(for: $0, delivery: .directMP4).isEmpty }
        let hls = ordered.filter { !capabilities.ranges(for: $0, delivery: .hls).isEmpty }
        // ADR 0031 qualifies PrismCore's Matroska loopback route only for
        // H.264 and HEVC. AV1 fMP4 HLS eligibility does not establish this
        // independent local demux/packaging path.
        let matroska = hls.filter { $0 == .h264 || $0 == .hevc }
        let codecList: ([NativeVideoCapabilities.Codec]) -> String = { codecs in
            codecs.map(\.rawValue).joined(separator: ",")
        }
        let rangeList: ([NativeVideoCapabilities.Range]) -> String = { ranges in
            ranges.map(\.rawValue).joined(separator: "|")
        }
        let audio = forceCompatibleVideo ? "aac" : "aac,ac3,eac3,mp3,alac,flac"
        let matroskaAudio = forceCompatibleVideo ? "aac" : "aac,ac3,eac3,alac,flac"
        let dolbyVisionProfiles: [[String: Any]] = capabilities.directMP4DolbyVisionProfiles.map {
            ["Profile": $0.profile, "CompatibilityIDs": $0.compatibilityIDs, "MaxLevel": $0.maxLevel]
        }
        let dolbyVisionPackagingConversions: [[String: Any]] = capabilities.matroskaDolbyVisionPackagingConversions.map {
            [
                "SourceProfile": $0.sourceProfile,
                "SourceCompatibilityIDs": $0.sourceCompatibilityIDs,
                "TargetProfile": $0.targetProfile,
                "TargetCompatibilityID": $0.targetCompatibilityID,
                "MaxLevel": $0.maxLevel,
                "EnhancementLayerDisposition": $0.enhancementLayerDisposition,
            ]
        }
        let transportDolbyVisionPackagingConversions: [[String: Any]] = capabilities.transportDolbyVisionPackagingConversions.map {
            [
                "SourceProfile": $0.sourceProfile,
                "SourceCompatibilityIDs": $0.sourceCompatibilityIDs,
                "TargetProfile": $0.targetProfile,
                "TargetCompatibilityID": $0.targetCompatibilityID,
                "MaxLevel": $0.maxLevel,
                "EnhancementLayerDisposition": $0.enhancementLayerDisposition,
            ]
        }
        // The pinned PrismCore engine's qualified Matroska packaging retains the complete
        // P5/P8 configuration and RPU sequence, including subtitle masters.
        // This independent delivery policy does not authorize server HLS DV.
        let directProfiles: [[String: Any]] = [
            [
                "Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": codecList(direct), "AudioCodec": audio,
                "LuminaDolbyVisionProfiles": dolbyVisionProfiles,
            ],
            [
                "Type": "Video", "Container": "mkv", "VideoCodec": codecList(matroska), "AudioCodec": matroskaAudio,
                "LuminaDolbyVisionProfiles": matroska.contains(.hevc) ? dolbyVisionProfiles : [],
                "LuminaDolbyVisionPackagingConversions": matroska.contains(.hevc) ? dolbyVisionPackagingConversions : [],
                "LuminaDolbyVisionSeparateTrackPackaging": matroska.contains(.hevc) && !dolbyVisionPackagingConversions.isEmpty,
            ],
        ]
        var profile: [String: Any] = ["DirectPlayProfiles": directProfiles]
        // This custom top-level declaration is intentionally not an ordinary
        // M2TS DirectPlayProfile: old servers ignore it rather than widening
        // generic HEVC/M2TS direct playback without pair validation.
        if !forceCompatibleVideo, !transportDolbyVisionPackagingConversions.isEmpty {
            profile["LuminaDolbyVisionTransportProfiles"] = [[
                "Type": "Video", "Container": "m2ts", "VideoCodec": "hevc", "AudioCodec": "aac,ac3,eac3",
                "LuminaDolbyVisionProfiles": [],
                "LuminaDolbyVisionPackagingConversions": transportDolbyVisionPackagingConversions,
                "LuminaDolbyVisionM2TSPairing": true,
            ]]
        }
        var videoProfiles = [[String: Any]]()
        for codec in ordered {
            let directRanges = capabilities.ranges(for: codec, delivery: .directMP4)
            let hlsRanges = capabilities.ranges(for: codec, delivery: .hls)
            let directDepth = capabilities.bitDepth(for: codec, delivery: .directMP4)
            let hlsDepth = capabilities.bitDepth(for: codec, delivery: .hls)
            let ranges: Set<NativeVideoCapabilities.Range>
            let depth: Int?
            switch (directRanges.isEmpty, hlsRanges.isEmpty) {
            case (false, false):
                // Codec profiles constrain both delivery paths. Do not let a
                // direct-only HDR/Main 10 claim broaden HLS eligibility.
                ranges = directRanges.intersection(hlsRanges)
                if let directDepth, let hlsDepth {
                    depth = min(directDepth, hlsDepth)
                } else {
                    depth = nil
                }
            case (false, true):
                ranges = directRanges
                depth = directDepth
            case (true, false):
                ranges = hlsRanges
                depth = hlsDepth
            case (true, true):
                ranges = []
                depth = nil
            }
            guard let depth, !ranges.isEmpty else { continue }
            var conditions = [
                ["Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": String(depth)],
                ["Property": "VideoRangeType", "Condition": "EqualsAny", "Value": rangeList(NativeVideoCapabilities.Range.allCases.filter(ranges.contains))],
            ]
            if codec != .h264 {
                // The HEVC Main 10 and AV1 codec strings queried above cover
                // Lumina's 4K output ceiling, not an unbounded resolution.
                conditions += [
                    ["Property": "Width", "Condition": "LessThanEqual", "Value": "3840"],
                    ["Property": "Height", "Condition": "LessThanEqual", "Value": "2160"],
                ]
            }
            if let maximumHeight = quality.maximumHeight {
                conditions.append(["Property": "Height", "Condition": "LessThanEqual", "Value": String(maximumHeight)])
            }
            videoProfiles.append(["Type": "Video", "Codec": codec.rawValue, "Conditions": conditions])
        }
        if forceCompatibleVideo {
            videoProfiles.append([
                "Type": "VideoAudio", "Codec": "aac",
                "Conditions": [["Property": "AudioChannels", "Condition": "LessThanEqual", "Value": "2"]],
            ])
        } else {
            videoProfiles.append([
                "Type": "VideoAudio", "Codec": "flac",
                "Conditions": [["Property": "AudioBitDepth", "Condition": "LessThanEqual", "Value": "24"]],
            ])
        }
        profile.merge([
            "TranscodingProfiles": [["Type": "Video", "Protocol": "hls", "Container": "fmp4", "VideoCodec": codecList(hls), "AudioCodec": "aac"]],
            "SubtitleProfiles": [["Format": "vtt", "Method": "External"]],
            "CodecProfiles": videoProfiles,
        ], uniquingKeysWith: { _, new in new })
        return profile
    }

    func playbackRequest(origin: URL, token: String, item: MediaItem, result: PlaybackInfo,
                         quality: PlaybackQuality = .automatic,
                         audioStreamIndex: Int? = nil, subtitleStreamIndex: Int? = nil, mediaSourceID: String? = nil,
                         startPositionSeconds: Double? = nil,
                         selectedSourceChapters: [PlaybackChapter]? = nil,
                         usesCompatibleVideo: Bool = false,
                         usesServerVideoRecovery: Bool = false) throws -> PlaybackRequest {
        let source = try preferredSource(in: result, mediaSourceID: mediaSourceID)
        let streams = try source.mediaStreams.map { try PlaybackStream($0, origin: origin) }
        let sourceChapters = source.chapters.map {
            sanitizedPlaybackChapters(
                $0,
                durationSeconds: source.runTimeTicks.map { Double($0) / tickScale } ?? item.durationSeconds,
            )
        }
        // The item detail's chapters describe its preferred source. The preferred source uses
        // the item id on Lumina; legacy callers omit an id. Explicit alternates never inherit it.
        let chapters = sourceChapters ?? selectedSourceChapters
            ?? ((mediaSourceID == nil || source.id == item.id) ? item.chapters : [])
        let resolvedAudioStreamIndex = audioStreamIndex ?? source.defaultAudioStreamIndex
        let usesClientDemux = source.supportsDirectPlay && !item.isAudio && (
            ["mkv", "matroska"].contains((source.container ?? item.container ?? "").lowercased())
            || (source.container?.lowercased() == "m2ts" && source.clientPackaging == "p7_m2ts_to_p81_drop_el")
        )
        // Automatic subtitle selection must remain unselected for Prism's original-file
        // packaging. Supplying a server default here turns it into an explicit embedded
        // subtitle request, which can require a master the media-direct route does not publish.
        let resolvedSubtitleStreamIndex = usesClientDemux && subtitleStreamIndex == nil
            ? nil : subtitleStreamIndex ?? source.defaultSubtitleStreamIndex
        let start = try boundedStartPosition(startPositionSeconds, item: item)
        if source.supportsDirectPlay {
            // A compatible retry must receive the forced H.264/SDR HLS
            // decision. Treat a direct response as the server ignoring that
            // request rather than relabelling the original media compatible.
            guard !usesCompatibleVideo, !usesServerVideoRecovery else { throw LuminaError.malformedResponse }
            guard let direct = url(origin: origin, path: "/Videos/\(item.id)/stream", query: [
                URLQueryItem(name: "MediaSourceId", value: source.id),
                URLQueryItem(name: "Static", value: "true"),
                URLQueryItem(name: "ApiKey", value: token),
            ]), sameOrigin(origin, direct) else { throw LuminaError.unsafeURL }
            var seekIndex: M2TSSeekIndexReference?
            if source.container?.lowercased() == "m2ts", source.clientPackaging == "p7_m2ts_to_p81_drop_el",
               let raw = source.m2tsSeekIndexURL,
               let endpoint = URL(string: raw, relativeTo: origin)?.absoluteURL,
               sameOrigin(origin, endpoint), endpoint.user == nil, endpoint.password == nil,
               endpoint.path.lowercased().hasSuffix("/lumina-m2ts-irap-index"),
               var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) {
                components.queryItems = [
                    URLQueryItem(name: "MediaSourceId", value: source.id),
                    URLQueryItem(name: "ApiKey", value: token),
                ]
                components.fragment = nil
                if let target = components.url { seekIndex = .init(url: target, sourceID: source.id) }
            }
            return PlaybackRequest(id: result.playSessionID, item: item, url: direct, startPositionSeconds: start,
                                   mediaSourceID: source.id, isTranscoding: false, methodLabel: "Direct",
                                   usesClientDemux: usesClientDemux,
                                   usesCompatibleVideo: usesCompatibleVideo,
                                   usesServerVideoRecovery: usesServerVideoRecovery,
                                   bitRate: source.bitRate, sourceContainer: source.container, m2tsSeekIndex: seekIndex,
                                   mediaStreams: streams, audioStreamIndex: resolvedAudioStreamIndex,
                                   subtitleStreamIndex: resolvedSubtitleStreamIndex, chapters: chapters,
                                   resolvedQuality: quality)
        }
        guard source.supportsTranscoding, let raw = source.transcodingURL,
              let hls = URL(string: raw, relativeTo: origin)?.absoluteURL, sameOrigin(origin, hls) else { throw LuminaError.unsafeURL }
        return PlaybackRequest(id: result.playSessionID, item: item, url: hls, startPositionSeconds: start,
                               mediaSourceID: source.id, isTranscoding: true, methodLabel: "HLS",
                               usesCompatibleVideo: usesCompatibleVideo,
                               usesServerVideoRecovery: usesServerVideoRecovery,
                               bitRate: source.bitRate, sourceContainer: source.container,
                               mediaStreams: streams, audioStreamIndex: resolvedAudioStreamIndex,
                               subtitleStreamIndex: resolvedSubtitleStreamIndex, chapters: chapters,
                               resolvedQuality: quality)
    }

    /// Selects the strongest source the current native profile can play directly. This keeps the
    /// uncapped device preference from silently preferring the server's first (often 1080p) row.
    /// A direct-compatible source still outranks a larger source that would require conversion.
    func preferredSource(in result: PlaybackInfo, mediaSourceID: String? = nil) throws -> MediaSource {
        if let mediaSourceID {
            guard let selected = result.mediaSources.first(where: { $0.id == mediaSourceID }) else {
                throw LuminaError.malformedResponse
            }
            return selected
        }
        guard let best = result.mediaSources.enumerated().max(by: { lhs, rhs in
            let lhsDelivery = lhs.element.deliveryPreference
            let rhsDelivery = rhs.element.deliveryPreference
            if lhsDelivery != rhsDelivery { return lhsDelivery < rhsDelivery }
            if lhs.element.videoHeight != rhs.element.videoHeight { return lhs.element.videoHeight < rhs.element.videoHeight }
            if lhs.element.bitRate != rhs.element.bitRate { return (lhs.element.bitRate ?? 0) < (rhs.element.bitRate ?? 0) }
            // `max` retains the right-hand candidate on equality, so reverse the source order.
            return lhs.offset > rhs.offset
        })?.element else {
            throw LuminaError.malformedResponse
        }
        return best
    }

    func playbackStreams(for source: MediaSource, origin: URL) throws -> [PlaybackStream] {
        try source.mediaStreams.map { try PlaybackStream($0, origin: origin) }
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
        let (data, response) = try await response(request)
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

    func subtitle(origin: URL, token: String, deviceID: String, url: URL) async throws -> Data {
        guard let safeURL = sanitizedSubtitleURL(origin: origin, raw: url.absoluteString) else {
            throw LuminaError.unsafeURL
        }
        let authorization = mediaBrowserHeader(token: token, deviceID: deviceID)
        var request = URLRequest(url: safeURL)
        request.setValue(authorization, forHTTPHeaderField: "Authorization")
        request.setValue("text/vtt", forHTTPHeaderField: "Accept")
        let guardDelegate = SubtitleDownloadGuard(origin: origin, authorization: authorization)
        let (bytes, response) = try await session.bytes(for: request, delegate: guardDelegate)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        guard response.statusCode == 200 else { throw LuminaError.http(response.statusCode) }
        if response.expectedContentLength > Int64(SubtitleDownloadGuard.maximumBytes) { throw LuminaError.malformedResponse }

        var data = Data()
        for try await byte in bytes {
            guard data.count < SubtitleDownloadGuard.maximumBytes else { throw LuminaError.malformedResponse }
            data.append(byte)
        }
        guard isWebVTT(data) else { throw LuminaError.malformedResponse }
        return data
    }

    func hlsMediaVariant(origin: URL, token: String, deviceID: String, masterURL: URL) async throws -> URL {
        guard sameOrigin(origin, masterURL), masterURL.user == nil, masterURL.password == nil else {
            throw LuminaError.unsafeURL
        }
        let authorization = mediaBrowserHeader(token: token, deviceID: deviceID)
        var request = URLRequest(url: masterURL)
        request.setValue(authorization, forHTTPHeaderField: "Authorization")
        request.setValue("application/vnd.apple.mpegurl, application/x-mpegURL", forHTTPHeaderField: "Accept")
        let guardDelegate = HLSPlaylistDownloadGuard(origin: origin, authorization: authorization)
        let (bytes, response) = try await session.bytes(for: request, delegate: guardDelegate)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse else { throw LuminaError.malformedResponse }
        guard response.statusCode == 200 else { throw LuminaError.http(response.statusCode) }
        guard let responseURL = response.url, sameOrigin(origin, responseURL),
              responseURL.user == nil, responseURL.password == nil else {
            throw LuminaError.unsafeURL
        }
        if response.expectedContentLength > Int64(HLSPlaylistDownloadGuard.maximumBytes) { throw LuminaError.malformedResponse }

        var data = Data()
        for try await byte in bytes {
            guard data.count < HLSPlaylistDownloadGuard.maximumBytes else { throw LuminaError.malformedResponse }
            data.append(byte)
        }
        return try resolvedHLSMediaVariant(origin: origin, masterURL: responseURL, playlist: data)
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

private final class SubtitleDownloadGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static let maximumBytes = 10 * 1024 * 1024
    let origin: URL
    let authorization: String

    init(origin: URL, authorization: String) {
        self.origin = origin
        self.authorization = authorization
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let rawTarget = request.url,
              let target = sanitizedSubtitleURL(origin: origin, raw: rawTarget.absoluteString) else {
            completionHandler(nil)
            task.cancel()
            return
        }
        var safeRequest = request
        safeRequest.url = target
        safeRequest.setValue(authorization, forHTTPHeaderField: "Authorization")
        completionHandler(safeRequest)
    }
}

private final class HLSPlaylistDownloadGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static let maximumBytes = 1 * 1024 * 1024
    let origin: URL
    let authorization: String

    init(origin: URL, authorization: String) {
        self.origin = origin
        self.authorization = authorization
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let target = request.url, sameOrigin(origin, target), target.user == nil, target.password == nil else {
            completionHandler(nil)
            task.cancel()
            return
        }
        var safeRequest = request
        safeRequest.setValue(authorization, forHTTPHeaderField: "Authorization")
        completionHandler(safeRequest)
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

private func receiverCapability(in url: URL) -> String? {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
          let items = components.queryItems, items.count == 1,
          items[0].name == "token" else { return nil }
    return items[0].value
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

func sameOrigin(_ left: URL, _ right: URL) -> Bool {
    let a = URLComponents(url: left, resolvingAgainstBaseURL: false)
    let b = URLComponents(url: right, resolvingAgainstBaseURL: false)
    return a?.scheme?.lowercased() == b?.scheme?.lowercased()
        && a?.host?.lowercased() == b?.host?.lowercased()
        && effectivePort(a) == effectivePort(b)
}

private func sanitizedSubtitleURL(origin: URL, raw: String) -> URL? {
    guard let target = URL(string: raw, relativeTo: origin)?.absoluteURL,
          sameOrigin(origin, target), target.user == nil, target.password == nil,
          var components = URLComponents(url: target, resolvingAgainstBaseURL: false) else { return nil }
    // Jellyfin supplies ApiKey in a DeliveryUrl. The native request instead authenticates with
    // the existing MediaBrowser header, so no credential-bearing address leaves this boundary.
    components.query = nil
    components.fragment = nil
    return components.url
}

private func isWebVTT(_ data: Data) -> Bool {
    let bom = Data([0xEF, 0xBB, 0xBF])
    var body = data
    if body.starts(with: bom) { body.removeFirst(bom.count) }
    guard let text = String(data: body, encoding: .utf8), let newline = text.firstIndex(of: "\n") else {
        return false
    }
    var header = String(text[..<newline])
    if header.last == "\r" { header.removeLast() }
    return header == "WEBVTT" || header.hasPrefix("WEBVTT ") || header.hasPrefix("WEBVTT\t")
}

private func resolvedHLSMediaVariant(origin: URL, masterURL: URL, playlist: Data) throws -> URL {
    guard let source = String(data: playlist, encoding: .utf8) else { throw LuminaError.malformedResponse }
    let lines = source.split(whereSeparator: { $0.isNewline }).map(String.init)
    guard lines.first?.trimmingCharacters(in: .whitespacesAndNewlines) == "#EXTM3U" else {
        throw LuminaError.malformedResponse
    }
    let streamInfos = lines.indices.filter { lines[$0].hasPrefix("#EXT-X-STREAM-INF:") }
    let hasAudioGroup = lines.contains { $0.hasPrefix("#EXT-X-MEDIA:") && $0.uppercased().contains("TYPE=AUDIO") }
    let hasIFrameVariant = lines.contains { $0.hasPrefix("#EXT-X-I-FRAME-STREAM-INF:") }
    let hasMediaMarker = lines.contains { $0.hasPrefix("#EXTINF:") || $0.hasPrefix("#EXT-X-TARGETDURATION:") }
    let hasOtherMasterTag = lines.contains {
        $0.hasPrefix("#EXT-X-MEDIA:") || $0.hasPrefix("#EXT-X-I-FRAME-STREAM-INF:") || $0.hasPrefix("#EXT-X-SESSION-")
    }
    if streamInfos.isEmpty {
        guard hasMediaMarker, !hasAudioGroup, !hasIFrameVariant, !hasOtherMasterTag else {
            throw LuminaError.malformedResponse
        }
        return masterURL
    }
    guard streamInfos.count == 1, !hasAudioGroup, !hasIFrameVariant, !hasOtherMasterTag,
          !lines[streamInfos[0]].uppercased().contains("AUDIO=") else { throw LuminaError.malformedResponse }
    let variantLine = lines[(streamInfos[0] + 1)...].first {
        !$0.isEmpty && !$0.hasPrefix("#")
    }
    guard let variantLine,
          let rawVariant = URL(string: variantLine, relativeTo: masterURL)?.absoluteURL,
          rawVariant.pathExtension.lowercased() == "m3u8", sameOrigin(origin, rawVariant),
          rawVariant.user == nil, rawVariant.password == nil else {
        throw LuminaError.unsafeURL
    }
    // AVPlayer subsequently fetches this URL itself, so retain the query credential carried by
    // the master when a single relative variant omits it.
    guard var components = URLComponents(url: rawVariant, resolvingAgainstBaseURL: false) else {
        throw LuminaError.malformedResponse
    }
    if components.query == nil {
        components.query = URLComponents(url: masterURL, resolvingAgainstBaseURL: false)?.query
    }
    guard let variant = components.url, sameOrigin(origin, variant) else { throw LuminaError.unsafeURL }
    return variant
}

private func boundedStartPosition(_ proposed: Double?, item: MediaItem) throws -> Double {
    let requested = proposed ?? item.resumeSeconds
    guard requested.isFinite else { throw LuminaError.malformedResponse }
    // This leaves a wide margin below Int64 overflow after conversion to ticks.
    let tickSafeMaximum = Double(Int64.max) / tickScale / 2
    let duration = item.durationSeconds
    let mediaMaximum = duration.isFinite && duration > 0 ? duration : tickSafeMaximum
    return min(max(0, requested), min(mediaMaximum, tickSafeMaximum))
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
    /// Jellyfin normally echoes the request offset, but older compatible
    /// implementations may omit it. Callers retain their requested offset.
    let startIndex: Int?
    enum CodingKeys: String, CodingKey { case items = "Items", total = "TotalRecordCount", startIndex = "StartIndex" }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        items = try values.decode([MediaItem].self, forKey: .items)
        total = try values.decode(Int.self, forKey: .total)
        startIndex = try values.decodeIfPresent(Int.self, forKey: .startIndex)
    }
}

struct LibraryAlphabetPage: Decodable {
    let anchors: [LibraryAlphabetAnchor]
    let total: Int
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
    let bitRate: Int?
    let supportsTranscoding: Bool
    let transcodingURL: String?
    let clientPackaging: String?
    let m2tsSeekIndexURL: String?
    let mediaStreams: [MediaStream]
    let defaultAudioStreamIndex: Int?
    let defaultSubtitleStreamIndex: Int?
    let runTimeTicks: Int64?
    /// Nil means an older server omitted source-specific markers; an empty array means this
    /// selected source has no markers.
    let chapters: [PlaybackChapter]?
    enum CodingKeys: String, CodingKey {
        case container = "Container", bitRate = "Bitrate"
        case id = "Id", supportsDirectPlay = "SupportsDirectPlay", supportsTranscoding = "SupportsTranscoding", transcodingURL = "TranscodingUrl", clientPackaging = "LuminaClientPackaging"
        case m2tsSeekIndexURL = "LuminaM2TSIRAPIndexUrl"
        case mediaStreams = "MediaStreams", defaultAudioStreamIndex = "DefaultAudioStreamIndex", defaultSubtitleStreamIndex = "DefaultSubtitleStreamIndex"
        case runTimeTicks = "RunTimeTicks", chapters = "Chapters"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        supportsDirectPlay = try values.decodeIfPresent(Bool.self, forKey: .supportsDirectPlay) ?? false
        container = try values.decodeIfPresent(String.self, forKey: .container)
        let rawBitRate = try values.decodeIfPresent(Int.self, forKey: .bitRate)
        bitRate = rawBitRate.flatMap { (1...1_000_000_000).contains($0) ? $0 : nil }
        supportsTranscoding = try values.decodeIfPresent(Bool.self, forKey: .supportsTranscoding) ?? false
        transcodingURL = try values.decodeIfPresent(String.self, forKey: .transcodingURL)
        clientPackaging = try values.decodeIfPresent(String.self, forKey: .clientPackaging)
        m2tsSeekIndexURL = try? values.decodeIfPresent(String.self, forKey: .m2tsSeekIndexURL)
        mediaStreams = try values.decodeIfPresent([MediaStream].self, forKey: .mediaStreams) ?? []
        defaultAudioStreamIndex = try values.decodeIfPresent(Int.self, forKey: .defaultAudioStreamIndex)
        defaultSubtitleStreamIndex = try values.decodeIfPresent(Int.self, forKey: .defaultSubtitleStreamIndex)
        runTimeTicks = try values.decodeIfPresent(Int64.self, forKey: .runTimeTicks)
        chapters = try? values.decodeIfPresent([PlaybackChapter].self, forKey: .chapters)
    }

    var videoHeight: Int {
        mediaStreams.lazy.filter { $0.type == .video }.compactMap(\.height).max() ?? 0
    }

    var deliveryPreference: Int {
        if supportsDirectPlay { return 2 }
        if supportsTranscoding { return 1 }
        return 0
    }
}

private struct MediaSegmentsPage: Decodable {
    let items: [MediaSegment]
    enum CodingKeys: String, CodingKey { case items = "Items" }
}

private struct MediaSegment: Decodable {
    let type: String?
    let startTicks: Int64?
    let endTicks: Int64?

    enum CodingKeys: String, CodingKey {
        case type = "Type", startTicks = "StartTicks", endTicks = "EndTicks"
    }

    init(from decoder: Decoder) throws {
        guard let values = try? decoder.container(keyedBy: CodingKeys.self) else {
            type = nil
            startTicks = nil
            endTicks = nil
            return
        }
        type = try? values.decode(String.self, forKey: .type)
        startTicks = try? values.decode(Int64.self, forKey: .startTicks)
        endTicks = try? values.decode(Int64.self, forKey: .endTicks)
    }
}

private func sanitizedPlaybackMarkers(_ records: [MediaSegment], durationSeconds: Double) -> [PlaybackMarker] {
    let hasDuration = durationSeconds.isFinite && durationSeconds > 0
    var seen = Set<String>()
    return records.compactMap { record in
        guard let type = record.type, let kind = PlaybackMarker.Kind(jellyfinType: type),
              let startTicks = record.startTicks, let endTicks = record.endTicks,
              startTicks >= 0, endTicks > startTicks else { return nil }
        let start = Double(startTicks) / tickScale
        let end = Double(endTicks) / tickScale
        guard start.isFinite, end.isFinite, end > start,
              !hasDuration || end <= durationSeconds else { return nil }
        let marker = PlaybackMarker(kind: kind, startSeconds: start, endSeconds: end)
        return seen.insert(marker.id).inserted ? marker : nil
    }
    .sorted { lhs, rhs in
        if lhs.startSeconds != rhs.startSeconds { return lhs.startSeconds < rhs.startSeconds }
        if lhs.endSeconds != rhs.endSeconds { return lhs.endSeconds < rhs.endSeconds }
        return lhs.kind.rawValue < rhs.kind.rawValue
    }
    .prefix(50)
    .map { $0 }
}

struct MediaStream: Decodable {
    let index: Int
    let type: PlaybackStreamType
    let label: String
    let codec: String?
    let language: String?
    let channels: Int?
    let height: Int?
    let isDefault: Bool
    let isForced: Bool
    let isExternal: Bool
    let deliveryMethod: String?
    let deliveryURL: String?

    enum CodingKeys: String, CodingKey {
        case index = "Index", type = "Type", displayTitle = "DisplayTitle", title = "Title", codec = "Codec"
        case language = "Language", channels = "Channels", height = "Height", isDefault = "IsDefault", isForced = "IsForced", isExternal = "IsExternal"
        case deliveryMethod = "DeliveryMethod", deliveryURL = "DeliveryUrl"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        index = try values.decode(Int.self, forKey: .index)
        switch try values.decodeIfPresent(String.self, forKey: .type)?.lowercased() {
        case "video": type = .video
        case "audio": type = .audio
        case "subtitle": type = .subtitle
        default: type = .other
        }
        codec = try values.decodeIfPresent(String.self, forKey: .codec)
        language = try values.decodeIfPresent(String.self, forKey: .language)
        channels = try values.decodeIfPresent(Int.self, forKey: .channels)
        height = try values.decodeIfPresent(Int.self, forKey: .height).flatMap { (1...20_000).contains($0) ? $0 : nil }
        isDefault = try values.decodeIfPresent(Bool.self, forKey: .isDefault) ?? false
        isForced = try values.decodeIfPresent(Bool.self, forKey: .isForced) ?? false
        isExternal = try values.decodeIfPresent(Bool.self, forKey: .isExternal) ?? false
        deliveryMethod = try values.decodeIfPresent(String.self, forKey: .deliveryMethod)
        deliveryURL = try values.decodeIfPresent(String.self, forKey: .deliveryURL)
        let displayTitle = try values.decodeIfPresent(String.self, forKey: .displayTitle)
        let title = try values.decodeIfPresent(String.self, forKey: .title)
        label = displayTitle ?? title ?? language ?? "Track \(index)"
    }
}

private extension PlaybackStream {
    init(_ stream: MediaStream, origin: URL) throws {
        let isExternalTextSubtitle = stream.type == .subtitle
            && ["srt", "subrip", "vtt", "webvtt"].contains(stream.codec?.lowercased())
            && stream.deliveryMethod?.caseInsensitiveCompare("External") == .orderedSame
        let subtitleDeliveryURL: URL?
        if isExternalTextSubtitle, let raw = stream.deliveryURL {
            guard let safeURL = sanitizedSubtitleURL(origin: origin, raw: raw) else { throw LuminaError.unsafeURL }
            // The server's external subtitle profile converts text sidecars to WebVTT while
            // preserving the source codec in MediaStreams. Only fetch that converted VTT URL.
            subtitleDeliveryURL = safeURL.pathExtension.lowercased() == "vtt" ? safeURL : nil
        } else {
            subtitleDeliveryURL = nil
        }
        self.init(index: stream.index, type: stream.type, label: stream.label, codec: stream.codec,
                  language: stream.language, channels: stream.channels, isDefault: stream.isDefault,
                  isForced: stream.isForced, isExternal: stream.isExternal, subtitleDeliveryURL: subtitleDeliveryURL)
    }
}
