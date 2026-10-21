import Foundation
import Security
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaCoreTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MockURLProtocol.handler = nil
    }

    func testConnectLoadsLibraryAndSendsMediaBrowserCredentials() async throws {
        let credentials = TestCredentials()
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: credentials)
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/System/Info/Public":
                return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName":
                return response(request, json: ["AccessToken": "test-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me":
                return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items":
                var featured = movie()
                featured["ImageTags"] = ["Primary": "poster-tag"]
                featured["BackdropImageTags"] = ["backdrop-tag"]
                featured["ParentBackdropItemId"] = "series-1"
                featured["ParentBackdropImageTags"] = ["series-backdrop-tag"]
                return response(request, json: page(items: [featured]))
            case "/Shows/NextUp": return response(request, json: page(items: []))
            case "/UserItems/Resume":
                return response(request, json: page(items: []))
            default:
                return response(request, status: 404)
            }
        }

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(store.session?.serverURL.absoluteString, "http://127.0.0.1:8765")
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])
        let featured = try XCTUnwrap(store.items.first)
        let backdrop = try XCTUnwrap(store.backdropURL(for: featured, maxWidth: 1200))
        XCTAssertEqual(backdrop.host, "127.0.0.1")
        XCTAssertEqual(backdrop.path, "/Items/movie-1/Images/Backdrop/0")
        let imageQuery = URLComponents(url: backdrop, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(imageQuery?.first { $0.name == "maxWidth" }?.value, "1200")
        XCTAssertEqual(imageQuery?.first { $0.name == "tag" }?.value, "backdrop-tag")
        XCTAssertEqual(imageQuery?.first { $0.name == "ApiKey" }?.value, "test-token")
        var inheritedArtwork = movie()
        inheritedArtwork["Type"] = "Episode"
        inheritedArtwork["BackdropImageTags"] = []
        inheritedArtwork["ParentBackdropItemId"] = "series-1"
        inheritedArtwork["ParentBackdropImageTags"] = ["series-backdrop-tag"]
        inheritedArtwork["SeriesId"] = "series-1"
        inheritedArtwork["SeriesPrimaryImageTag"] = "series-poster-tag"
        let episode = try JSONDecoder().decode(MediaItem.self, from: json(inheritedArtwork))
        let inheritedBackdrop = try XCTUnwrap(store.backdropURL(for: episode, maxWidth: 1400))
        XCTAssertEqual(inheritedBackdrop.scheme, backdrop.scheme)
        XCTAssertEqual(inheritedBackdrop.host, backdrop.host)
        XCTAssertEqual(inheritedBackdrop.port, backdrop.port)
        XCTAssertEqual(inheritedBackdrop.path, "/Items/series-1/Images/Backdrop/0")
        let inheritedQuery = URLComponents(url: inheritedBackdrop, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(inheritedQuery?.first { $0.name == "maxWidth" }?.value, "1400")
        XCTAssertEqual(inheritedQuery?.first { $0.name == "tag" }?.value, "series-backdrop-tag")
        XCTAssertEqual(inheritedQuery?.first { $0.name == "ApiKey" }?.value, "test-token")
        let seriesPoster = try XCTUnwrap(store.imageURL(for: episode, maxWidth: 0))
        XCTAssertEqual(seriesPoster.scheme, backdrop.scheme)
        XCTAssertEqual(seriesPoster.host, backdrop.host)
        XCTAssertEqual(seriesPoster.port, backdrop.port)
        XCTAssertEqual(seriesPoster.path, "/Items/series-1/Images/Primary")
        let seriesQuery = URLComponents(url: seriesPoster, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(seriesQuery?.first { $0.name == "tag" }?.value, "series-poster-tag")
        XCTAssertEqual(seriesQuery?.first { $0.name == "ApiKey" }?.value, "test-token")
        XCTAssertEqual(seriesQuery?.first { $0.name == "maxWidth" }?.value, "1")
        inheritedArtwork["Type"] = "Season"
        let season = try JSONDecoder().decode(MediaItem.self, from: json(inheritedArtwork))
        XCTAssertEqual(store.imageURL(for: season)?.path, "/Items/series-1/Images/Primary")
        inheritedArtwork["Type"] = "Episode"
        inheritedArtwork["ImageTags"] = ["Primary": "own-poster-tag"]
        let ownPosterItem = try JSONDecoder().decode(MediaItem.self, from: json(inheritedArtwork))
        let ownPoster = try XCTUnwrap(store.imageURL(for: ownPosterItem))
        XCTAssertEqual(ownPoster.path, "/Items/movie-1/Images/Primary")
        XCTAssertEqual(URLComponents(url: ownPoster, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "tag" }?.value, "own-poster-tag")
        let poster = try XCTUnwrap(store.imageURL(for: featured))
        XCTAssertEqual(URLComponents(url: poster, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "tag" }?.value, "poster-tag")
        let withoutArtwork = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        XCTAssertNil(store.backdropURL(for: withoutArtwork))
        let calls = requests.all
        XCTAssertEqual(calls.compactMap(\.url?.path), ["/System/Info/Public", "/Users/AuthenticateByName", "/Users/Me", "/Items", "/Items", "/UserItems/Resume", "/Shows/NextUp"])
        XCTAssertEqual(calls[1].httpMethod, "POST")
        XCTAssertTrue(calls[1].value(forHTTPHeaderField: "Authorization")!.contains("DeviceId=\"test-device\""))
        XCTAssertEqual(try body(calls[1])["Username"] as? String, "member")
        XCTAssertTrue(calls[2].value(forHTTPHeaderField: "Authorization")!.contains("Token=\"test-token\""))
        XCTAssertEqual(URLComponents(url: calls[3].url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "IncludeItemTypes" })?.value, "Movie,Series,Video")
        XCTAssertEqual(URLComponents(url: calls[4].url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "IncludeItemTypes" })?.value, "Movie,Episode,Video", "Home recently added keeps playable episodes")
        XCTAssertNil(store.errorMessage)
    }

    func testLibraryPaginationAppendsTheNextPageAndStopsAtTheCatalogTotal() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp": return response(request, json: page(items: []))
            case "/UserItems/Resume": return response(request, json: page(items: [movie(positionTicks: 12_000_000)]))
            case "/Items":
                let start = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "StartIndex" }?.value
                var item = movie()
                item["Id"] = start == "0" ? "movie-1" : "movie-2"
                return response(request, json: ["Items": [item], "TotalRecordCount": 2])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        XCTAssertTrue(store.hasMore)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["movie-1", "movie-2"])
        XCTAssertEqual(store.featuredItems.map(\.id), ["movie-1"])
        XCTAssertFalse(store.hasMore)
        await store.loadMore()
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items" }.count, 3)
    }

    func testHomeRemainsStableWhileVideoAndMusicBrowseIndependently() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp": return response(request, json: page(items: []))
            case "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Users/user-1/Items/track-1": return response(request, json: ["Id": "track-1", "Name": "Song", "Type": "Audio", "Album": "Album", "AlbumId": "album-1", "AlbumPrimaryImageTag": "album-art", "Artists": ["Artist"], "IndexNumber": 1, "ParentIndexNumber": 2])
            case "/Items":
                if value("IncludeItemTypes") == "MusicAlbum" {
                    return response(request, json: page(items: [["Id": "album-1", "Name": "Album", "Type": "MusicAlbum", "AlbumArtist": "Artist"]]))
                }
                if value("IncludeItemTypes") == "Audio" {
                    let start = value("StartIndex") ?? "0"
                    return response(request, json: ["Items": [["Id": start == "0" ? "track-1" : "track-2", "Name": "Song", "Type": "Audio"]], "TotalRecordCount": 2])
                }
                var item = movie()
                if value("SearchTerm") != nil { item["Id"] = "search-result" }
                return response(request, json: page(items: [item]))
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(search: "search", kind: .movies, sort: .year)
        await store.loadAudio()
        XCTAssertEqual(store.items.map(\.id), ["search-result"])
        XCTAssertEqual(store.recentItems.map(\.id), ["movie-1"])
        XCTAssertEqual(store.featuredItems.map(\.id), ["movie-1"])
        let album = try XCTUnwrap(store.audioItems.first)
        XCTAssertEqual(album.albumArtist, "Artist")
        let tracks = try await store.musicChildren(of: album)
        XCTAssertEqual(tracks.map(\.id), ["track-1", "track-2"])
        let playback = try await store.preparePlayback(for: tracks[0])
        XCTAssertTrue(playback.item.isAudio)
        XCTAssertEqual(playback.item.album, "Album")
        XCTAssertEqual(playback.item.displaySubtitle, "Artist · Album")
        XCTAssertEqual(store.imageURL(for: playback.item)?.path, "/Items/album-1/Images/Primary")
        XCTAssertEqual(playback.url.path, "/Audio/track-1/stream")
        XCTAssertEqual(URLComponents(url: playback.url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "ApiKey" }?.value, "test-token")
        XCTAssertEqual(playback.startPositionSeconds, 0)
        XCTAssertFalse(calls.all.contains { $0.url?.path.hasPrefix("/api/") == true })
        let searched = try XCTUnwrap(calls.all.first { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "SearchTerm" } == true })
        let query = URLComponents(url: searched.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "IncludeItemTypes" }?.value, "Movie")
        XCTAssertEqual(query?.first { $0.name == "SortBy" }?.value, "ProductionYear")
        await store.signOut()
        XCTAssertTrue(store.recentItems.isEmpty)
        XCTAssertTrue(store.audioItems.isEmpty)
    }

    func testSeriesBrowsingKeepsViewFiltersPagingAndOrderedChildren() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/UserViews": return response(request, json: page(items: [
                ["Id": "shows-view", "Name": "Shows", "Type": "CollectionFolder", "CollectionType": "tvshows"],
                ["Id": "anime-view", "Name": "Anime", "Type": "CollectionFolder", "CollectionType": "tvshows"],
            ]))
            case "/Shows/series-1/Seasons": return response(request, json: page(items: [
                ["Id": "season-2", "Name": "Season 2", "Type": "Season", "IndexNumber": 2],
                ["Id": "season-1", "Name": "Season 1", "Type": "Season", "IndexNumber": 1],
            ]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": "next-episode", "Name": "Next", "Type": "Episode", "SeriesId": "series-1", "SeasonId": "season-1"]]))
            case "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Items":
                if value("ParentId") == "anime-view" {
                    return response(request, json: ["Items": [["Id": value("StartIndex") == "0" ? "series-1" : "series-2", "Name": "Anime show", "Type": "Series"]], "TotalRecordCount": 2])
                }
                if value("ParentId") == "season-1" {
                    return response(request, json: ["Items": [["Id": value("StartIndex") == "0" ? "episode-2" : "episode-1", "Name": "Episode", "Type": "Episode", "IndexNumber": value("StartIndex") == "0" ? 2 : 1]], "TotalRecordCount": 2])
                }
                return response(request, json: page(items: [movie()]))
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        XCTAssertEqual(store.nextUpItems.first?.seriesID, "series-1")
        let filters = VideoFilters(genre: "Drama", year: 2023, unwatchedOnly: true, favoritesOnly: true)
        await store.loadLibrary(search: "Anime", kind: .anime, sort: .recentlyAdded, filters: filters)
        XCTAssertTrue(store.items.first?.isCollection == true)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["series-1", "series-2"])
        let series = try XCTUnwrap(store.items.first)
        let seasons = try await store.loadChildren(of: series)
        XCTAssertEqual(seasons.map(\.id), ["season-1", "season-2"])
        let episodes = try await store.loadChildren(of: seasons[0])
        XCTAssertEqual(episodes.map(\.id), ["episode-1", "episode-2"])
        let pages = calls.all.filter { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "ParentId" && $0.value == "anime-view" } == true }
        XCTAssertEqual(pages.count, 2)
        for request in pages {
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            XCTAssertEqual(query.first { $0.name == "Genres" }?.value, "Drama")
            XCTAssertEqual(query.first { $0.name == "Years" }?.value, "2023")
            XCTAssertEqual(query.first { $0.name == "IsPlayed" }?.value, "false")
            XCTAssertEqual(query.first { $0.name == "IsFavorite" }?.value, "true")
            XCTAssertEqual(query.first { $0.name == "IncludeItemTypes" }?.value, "Series")
        }
        XCTAssertFalse(store.hasMore)
    }

    func testWatchedFavoriteMutationRefreshesCachesAndRevocationClearsSession() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let credentials = TestCredentials(session: saved)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return response(request, json: page(items: [movie(positionTicks: 50_000_000)]))
            case "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1/UserData": return response(request, status: 204)
            case "/Users/user-1/Items/movie-1":
                var item = movie()
                item["UserData"] = ["Played": true, "IsFavorite": true]
                return response(request, json: item)
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(filters: VideoFilters(unwatchedOnly: true))
        let original = try XCTUnwrap(store.items.first)
        let fresh = try await store.updateItemState(item: original, played: true, favorite: true)
        XCTAssertEqual(fresh.userData?.played, true)
        XCTAssertEqual(fresh.userData?.isFavorite, true)
        XCTAssertTrue(store.items.isEmpty, "Marking watched removes the row from the active unwatched filter")
        XCTAssertEqual(store.recentItems.first?.userData?.played, true)
        XCTAssertTrue(store.resumeItems.isEmpty)
        let mutation = try XCTUnwrap(calls.all.first { $0.httpMethod == "POST" })
        XCTAssertEqual(try body(mutation)["Played"] as? Bool, true)
        XCTAssertEqual(try body(mutation)["IsFavorite"] as? Bool, true)
        XCTAssertTrue(mutation.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true)
        MockURLProtocol.handler = { request in response(request, status: 401) }
        do { _ = try await store.loadItem(fresh); XCTFail("Revoked credentials must fail") }
        catch { XCTAssertEqual(error.localizedDescription, "Your session expired. Sign in again.") }
        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertTrue(store.nextUpItems.isEmpty)
    }

    func testMissingSignInEndpointDoesNotClaimAPIIsDisabled() async throws {
        let credentials = TestCredentials()
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in response(request, status: 404) }

        await store.connect(server: "https://lumina.example", username: "member", password: "test-password")

        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertEqual(store.errorMessage, "The server returned HTTP 404 (not found). Check the server address and that Apps like Infuse is enabled in Lumina Settings.")
    }

    func testPlaybackInfoUsesOnlySameOriginHLSAndReportsPositions() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "session-1", "MediaSources": [[
                    "Id": "source-1", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-1/master.m3u8?ApiKey=test-token",
                ]]])
            case "/Sessions/Playing/Progress":
                return response(request, status: 204)
            default:
                return response(request, status: 404)
            }
        }

        let info = try await client.playbackInfo(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", deviceID: "test-device", item: item, forceTranscode: true)
        let request = try client.playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)
        try await client.report(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", deviceID: "test-device", event: .progress, request: request, positionSeconds: 12.5, isPaused: true)

        XCTAssertEqual(request.url.host, "127.0.0.1")
        XCTAssertTrue(request.isTranscoding)
        let playbackBody = try body(requests.all[0])
        XCTAssertEqual(playbackBody["EnableDirectPlay"] as? Bool, false)
        let profile = playbackBody["DeviceProfile"] as? [String: Any]
        let direct = (profile?["DirectPlayProfiles"] as? [[String: Any]])?.first?["Container"] as? String
        XCTAssertEqual(direct, "mp4,m4v,mov")
        let audio = (profile?["DirectPlayProfiles"] as? [[String: Any]])?.first?["AudioCodec"] as? String
        XCTAssertEqual(audio, "aac,ac3,eac3,mp3,alac,flac")
        let codecs = try XCTUnwrap(profile?["CodecProfiles"] as? [[String: Any]])
        for (codec, depth) in [("h264", "8"), ("hevc", "10")] {
            let rule = try XCTUnwrap(codecs.first { $0["Codec"] as? String == codec })
            let condition = try XCTUnwrap((rule["Conditions"] as? [[String: String]])?.first)
            XCTAssertEqual(condition, ["Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": depth])
        }
        let flac = try XCTUnwrap(codecs.first { $0["Codec"] as? String == "flac" })
        XCTAssertEqual(flac["Type"] as? String, "VideoAudio")
        XCTAssertEqual(flac["Conditions"] as? [[String: String]], [
            ["Property": "AudioBitDepth", "Condition": "LessThanEqual", "Value": "24"],
        ])
        XCTAssertEqual(try body(requests.all[1])["PositionTicks"] as? Int64, 125_000_000)
        XCTAssertEqual(try body(requests.all[1])["IsPaused"] as? Bool, true)
    }

    func testPlaybackRefreshesAStaleDetailItemBeforePreparing() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie(positionTicks: 250_000_000))
            case "/Items/movie-1/PlaybackInfo": return response(request, json: ["PlaySessionId": "session-1", "MediaSources": [["Id": "source-1", "SupportsDirectPlay": true, "SupportsTranscoding": false]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let stale = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: stale)

        XCTAssertEqual(request.startPositionSeconds, 25)
        let infoRequest = requests.all.first(where: { $0.url?.path == "/Items/movie-1/PlaybackInfo" })!
        XCTAssertEqual(try body(infoRequest)["StartTimeTicks"] as? Int64, 250_000_000)
    }

    func testRefusesCrossOriginPlaybackURL() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json(["PlaySessionId": "session-1", "MediaSources": [[
            "Id": "source-1", "SupportsDirectPlay": false, "SupportsTranscoding": true, "TranscodingUrl": "https://elsewhere.invalid/master.m3u8",
        ]]]))
        XCTAssertThrowsError(try LuminaClient().playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)) {
            XCTAssertEqual($0 as? LuminaError, .unsafeURL)
        }
    }

    func testUnsupportedNativeAudioUsesAuthenticatedCompatibleStream() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(["Id": "track-1", "Name": "Song", "Type": "Audio", "Container": "ogg"]))
        let request = try LuminaClient().audioRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item)
        XCTAssertEqual(request.url.path, "/Audio/track-1/stream")
        let query = URLComponents(url: request.url, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "format" }?.value, "flac")
        XCTAssertEqual(query?.first { $0.name == "ApiKey" }?.value, "test-token")
        XCTAssertEqual(request.methodLabel, "Compatible audio")
        XCTAssertFalse(request.isTranscoding, "Compatibility audio is a cached file, not an HLS playback session")
    }

    func testUnauthorizedRestoreClearsStoredSessionAndExplainsAppPassword() async throws {
        let credentials = TestCredentials(session: LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "expired-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device"))
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in response(request, status: 401) }

        await store.restore()

        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertEqual(store.errorMessage, "Your session expired. Sign in again.")
    }

    func testOfflineRestoreKeepsStoredSessionForRetry() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "saved-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let credentials = TestCredentials(session: saved)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }

        await store.restore()

        XCTAssertEqual(store.session?.accessToken, "saved-token")
        XCTAssertEqual(credentials.stored?.accessToken, "saved-token")
        XCTAssertEqual(store.errorMessage, "Could not reach the Lumina server.")
    }

    func testUnavailableKeychainDoesNotAttemptAuthentication() async throws {
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: ["ServerName": "Lumina"])
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: FailingCredentials())

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(requests.all.compactMap(\.url?.path), ["/System/Info/Public"])
        XCTAssertEqual(store.errorMessage, "Secure credential storage is unavailable. Use a signed app build and try again.")
    }

    func testSignOutClearsLocalSessionBeforeDelayedFailedRevocation() async throws {
        let credentials = TestCredentials(session: LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device"))
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }
        await store.restore()

        let requests = RequestRecorder()
        let logoutStarted = expectation(description: "logout request started")
        let releaseLogout = DispatchSemaphore(value: 0)
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Sessions/Logout":
                logoutStarted.fulfill()
                releaseLogout.wait()
                throw URLError(.notConnectedToInternet)
            case "/System/Info/Public": return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return response(request, json: ["AccessToken": "new-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        let signOut = Task { await store.signOut() }
        await fulfillment(of: [logoutStarted], timeout: 1)

        XCTAssertNil(store.session)
        XCTAssertNil(credentials.stored)
        XCTAssertNil(store.errorMessage)
        releaseLogout.signal()
        await signOut.value
        XCTAssertNil(store.errorMessage, "A failed revocation must not replace the signed-out connection form with an old session error.")

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(store.session?.accessToken, "new-token")
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(requests.all.first?.url?.path, "/Sessions/Logout")
    }
}

private final class TestCredentials: CredentialStore {
    var stored: LuminaSession?
    init(session: LuminaSession? = nil) { stored = session }
    func deviceID() throws -> String { "test-device" }
    func save(session: LuminaSession) throws { stored = session }
    func load() throws -> LuminaSession? { stored }
    func deleteSession() { stored = nil }
}

private final class FailingCredentials: CredentialStore {
    func deviceID() throws -> String { throw LuminaError.keychain(errSecMissingEntitlement) }
    func save(session: LuminaSession) throws {}
    func load() throws -> LuminaSession? { nil }
    func deleteSession() {}
}

private final class RequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()
    func append(_ request: URLRequest) { lock.withLock { requests.append(request) } }
    var all: [URLRequest] { lock.withLock { requests } }
}

private final class MockURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.badServerResponse) }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
    override func stopLoading() {}
}

private func mockConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [MockURLProtocol.self]
    return configuration
}

private func response(_ request: URLRequest, status: Int = 200, json object: Any = [:]) -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, json(object))
}

private func body(_ request: URLRequest) throws -> [String: Any] {
    let data: Data
    if let body = request.httpBody {
        data = body
    } else if let stream = request.httpBodyStream {
        stream.open()
        defer { stream.close() }
        var bytes = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while true {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count > 0 else { break }
            bytes.append(buffer, count: count)
        }
        data = bytes
    } else {
        data = Data()
    }
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

private func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }
private func page(items: [[String: Any]]) -> [String: Any] { ["Items": items, "TotalRecordCount": items.count, "StartIndex": 0] }
private func movie(positionTicks: Int64? = nil) -> [String: Any] {
    var item: [String: Any] = ["Id": "movie-1", "Name": "Example Movie", "Type": "Movie", "RunTimeTicks": 120_000_000]
    if let positionTicks { item["UserData"] = ["PlaybackPositionTicks": positionTicks, "Played": false] }
    return item
}
