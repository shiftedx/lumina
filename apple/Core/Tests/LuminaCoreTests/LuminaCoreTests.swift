import Foundation
import Security
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaCoreTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MockURLProtocol.handler = nil
        HomeDelayedURLProtocol.reset()
    }

    func testEpisodeDetailDecodesOnlyAvailableSourceMetadata() throws {
        var payload = movie()
        payload["Type"] = "Episode"
        payload["PremiereDate"] = "2026-10-14T00:00:00Z"
        payload["MediaStreams"] = [
            ["Type": "Video", "Height": 1080, "IsInterlaced": false],
            ["Type": "Audio", "Codec": "eac3", "DisplayTitle": "E-AC3 5.1", "Channels": 6, "IsDefault": true],
        ]
        let episode = try JSONDecoder().decode(MediaItem.self, from: json(payload))
        XCTAssertEqual(episode.premiereDate, "2026-10-14T00:00:00Z")
        XCTAssertEqual(episode.mediaStreams?.first?.height, 1080)
        XCTAssertEqual(episode.mediaStreams?.first?.isInterlaced, false)
        XCTAssertEqual(episode.mediaStreams?.last?.displayTitle, "E-AC3 5.1")
        XCTAssertEqual(episode.mediaStreams?.last?.isDefault, true)
        let roundTrip = try JSONDecoder().decode(MediaItem.self, from: JSONEncoder().encode(episode))
        XCTAssertEqual(roundTrip, episode)
        let withoutProbe = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        XCTAssertNil(withoutProbe.premiereDate)
        XCTAssertNil(withoutProbe.mediaStreams)
    }

    func testMobilePairingPayloadRejectsUnsupportedAndUnsafeCodes() throws {
        let accepted = try MobilePairingPayload(scannedCode: """
        {"type":"lumina-pair","version":1,"server_url":"https://lumina.example:8443","token":"one-time"}
        """)
        XCTAssertEqual(accepted.serverURL.absoluteString, "https://lumina.example:8443")
        var link = URLComponents()
        link.scheme = "lumina"
        link.host = "pair"
        link.fragment = "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"https://lumina.example:8443\",\"token\":\"one-time\"}"
        XCTAssertEqual(try MobilePairingPayload(deepLink: XCTUnwrap(link.url)).serverURL, accepted.serverURL)
        XCTAssertThrowsError(try MobilePairingPayload(deepLink: URL(string: "lumina://other#code")!))
        XCTAssertThrowsError(try MobilePairingPayload(deepLink: URL(string: "lumina://pair?code=value#code")!))
        XCTAssertThrowsError(try MobilePairingPayload(scannedCode: "{\"type\":\"other\",\"version\":1,\"server_url\":\"https://lumina.example\",\"token\":\"one-time\"}"))
        XCTAssertThrowsError(try MobilePairingPayload(scannedCode: "{\"type\":\"lumina-pair\",\"version\":2,\"server_url\":\"https://lumina.example\",\"token\":\"one-time\"}"))
        XCTAssertThrowsError(try MobilePairingPayload(scannedCode: "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"https://lumina.example/path\",\"token\":\"one-time\"}"))
        XCTAssertThrowsError(try MobilePairingPayload(scannedCode: "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"https://lumina.example:\",\"token\":\"one-time\"}"))
    }

    func testMobilePairingClaimsOnlyScannedOriginAndStoresSession() async throws {
        let credentials = TestCredentials()
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: credentials)
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/api/mobile-connect/claim":
                return response(request, json: ["username": "member", "password": "app-password", "server_url": "http://127.0.0.1:8765"])
            case "/System/Info/Public": return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return response(request, json: ["AccessToken": "paired-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        await store.connect(pairingCode: "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"http://127.0.0.1:8765\",\"token\":\"one-time\"}", appName: "Lumina for iPhone")

        let claim = try XCTUnwrap(requests.all.first { $0.url?.path == "/api/mobile-connect/claim" })
        XCTAssertEqual(try body(claim)["token"] as? String, "one-time")
        XCTAssertEqual(try body(claim)["name"] as? String, "Lumina for iPhone")
        XCTAssertEqual(store.session?.accessToken, "paired-token")
        XCTAssertEqual(credentials.stored?.serverURL.absoluteString, "http://127.0.0.1:8765")
        XCTAssertNil(store.errorMessage)
    }

    func testMobilePairingDoesNotForwardClaimedPasswordToAnotherOrigin() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: TestCredentials())
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: ["username": "member", "password": "app-password", "server_url": "https://elsewhere.example"])
        }

        await store.connect(pairingCode: "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"http://127.0.0.1:8765\",\"token\":\"one-time\"}", appName: "Lumina for iPhone")

        XCTAssertEqual(requests.all.map { $0.url?.path }, ["/api/mobile-connect/claim"])
        XCTAssertNil(store.session)
        XCTAssertEqual(store.errorMessage, "This code belongs to a different server.")
    }

    func testMobilePairingExplainsAnUnreachableServer() async throws {
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials())
        MockURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }

        await store.connect(pairingCode: "{\"type\":\"lumina-pair\",\"version\":1,\"server_url\":\"http://127.0.0.1:8765\",\"token\":\"one-time\"}", appName: "Lumina for iPhone")

        XCTAssertEqual(store.errorMessage, "Cannot reach this server. Check Wi-Fi or VPN.")
    }

    func testConnectLoadsLibraryAndSendsMediaBrowserCredentials() async throws {
        let credentials = TestCredentials()
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: credentials)
        let requests = RequestRecorder()
        let pendingFramingAttempts = PathCounter()
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
                featured["ImageTags"] = ["Primary": "poster-tag", "Logo": "logo-tag"]
                featured["BackdropImageTags"] = ["backdrop-tag"]
                featured["ParentBackdropItemId"] = "series-1"
                featured["ParentBackdropImageTags"] = ["series-backdrop-tag"]
                return response(request, json: page(items: [featured]))
            case "/Items/movie-1/Images/Backdrop/0/Framing", "/Items/series-1/Images/Backdrop/0/Framing":
                return response(request, json: ["state": "ready", "width": 1920, "height": 1080, "regions": []])
            case "/Items/pending/Images/Backdrop/0/Framing":
                let attempt = pendingFramingAttempts.next(request.url!.path)
                return response(request, json: ["state": attempt == 1 ? "pending" : "ready", "width": 1920, "height": 1080, "regions": []])
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
        let logo = try XCTUnwrap(store.logoURL(for: featured, maxWidth: 600))
        XCTAssertEqual(logo.host, "127.0.0.1")
        XCTAssertEqual(logo.path, "/Items/movie-1/Images/Logo")
        let logoQuery = URLComponents(url: logo, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(logoQuery?.first { $0.name == "maxWidth" }?.value, "600")
        XCTAssertEqual(logoQuery?.first { $0.name == "tag" }?.value, "logo-tag")
        XCTAssertEqual(logoQuery?.first { $0.name == "ApiKey" }?.value, "test-token")

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
        let ownFraming = await store.backdropFraming(for: featured)
        XCTAssertEqual(ownFraming?.state, "ready")
        let repeatedOwnFraming = await store.backdropFraming(for: featured)
        XCTAssertEqual(repeatedOwnFraming?.state, "ready")
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items/movie-1/Images/Backdrop/0/Framing" }.count, 1,
                       "Returning to a ready hero must not repeat its framing request.")
        let pendingBackdrop = try XCTUnwrap(URL(string: "http://127.0.0.1:8765/Items/pending/Images/Backdrop/0?tag=pending&ApiKey=test-token"))
        let pendingFraming = await store.artworkFraming(for: pendingBackdrop)
        XCTAssertEqual(pendingFraming?.state, "pending")
        let completedFraming = await store.artworkFraming(for: pendingBackdrop)
        XCTAssertEqual(completedFraming?.state, "ready")
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items/pending/Images/Backdrop/0/Framing" }.count, 2,
                       "Pending framing remains retryable until the service reports ready.")
        let inheritedFraming = await store.backdropFraming(for: episode)
        XCTAssertEqual(inheritedFraming?.state, "ready")
        XCTAssertTrue(requests.all.contains { $0.url?.path == inheritedBackdrop.path + "/Framing" })
        for request in requests.all.filter({ $0.url?.path.hasSuffix("/Framing") == true }) {
            XCTAssertEqual(request.timeoutInterval, 0.15)
            XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true)
            XCTAssertNil(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "ApiKey" })
        }
        let inheritedQuery = URLComponents(url: inheritedBackdrop, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(inheritedQuery?.first { $0.name == "maxWidth" }?.value, "1400")
        XCTAssertEqual(inheritedQuery?.first { $0.name == "tag" }?.value, "series-backdrop-tag")
        XCTAssertEqual(inheritedQuery?.first { $0.name == "ApiKey" }?.value, "test-token")
        var episodeWithStill = inheritedArtwork
        episodeWithStill["ImageTags"] = ["Primary": "episode-still-tag"]
        let posterEpisode = try JSONDecoder().decode(MediaItem.self, from: json(episodeWithStill))
        XCTAssertEqual(store.imageURL(for: posterEpisode)?.path, "/Items/movie-1/Images/Primary")
        XCTAssertEqual(store.posterURL(for: posterEpisode)?.path, "/Items/series-1/Images/Primary")

        var savedEpisode = episodeWithStill
        savedEpisode["Name"] = "Pilot"
        savedEpisode["SeriesName"] = "The Observatory"
        savedEpisode["IndexNumber"] = 1
        savedEpisode["ParentIndexNumber"] = 2
        savedEpisode["ParentPrimaryImageItemId"] = "series-1"
        savedEpisode["ParentPrimaryImageTag"] = "series-poster-tag"
        savedEpisode["ParentLogoItemId"] = "series-1"
        savedEpisode["ParentLogoImageTag"] = "series-logo-tag"
        let queuedEpisode = try JSONDecoder().decode(MediaItem.self, from: json(savedEpisode))
        XCTAssertEqual(queuedEpisode.homeHeroTitle, "The Observatory")
        XCTAssertEqual(queuedEpisode.homeHeroSubtitle, "S2 · E1 · Pilot")
        XCTAssertEqual(queuedEpisode.id, posterEpisode.id, "Presentation must preserve the saved playable version.")
        XCTAssertEqual(store.imageURL(for: queuedEpisode)?.path, "/Items/series-1/Images/Primary")
        XCTAssertEqual(store.logoURL(for: queuedEpisode)?.path, "/Items/series-1/Images/Logo")
        XCTAssertEqual(store.backdropURL(for: queuedEpisode)?.path, "/Items/series-1/Images/Backdrop/0")

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
        XCTAssertNil(store.logoURL(for: withoutArtwork))
        let calls = requests.all
        XCTAssertEqual(Array(calls.compactMap(\.url?.path).prefix(3)), ["/System/Info/Public", "/Users/AuthenticateByName", "/Users/Me"])
        XCTAssertEqual(calls.filter { $0.url?.path == "/Items" }.count, 2)
        XCTAssertEqual(calls.filter { $0.url?.path == "/UserItems/Resume" }.count, 1)
        XCTAssertEqual(calls.filter { $0.url?.path == "/Shows/NextUp" }.count, 1)
        XCTAssertEqual(calls[1].httpMethod, "POST")
        XCTAssertTrue(calls[1].value(forHTTPHeaderField: "Authorization")!.contains("DeviceId=\"test-device\""))
        XCTAssertEqual(try body(calls[1])["Username"] as? String, "member")
        XCTAssertTrue(calls[2].value(forHTTPHeaderField: "Authorization")!.contains("Token=\"test-token\""))
        let catalog = try XCTUnwrap(calls.first { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "IncludeItemTypes" })?.value == "Movie,Series,Video" })
        let recent = try XCTUnwrap(calls.first { $0.url?.path == "/Items" && URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "IncludeItemTypes" })?.value == "Movie,Episode,Video" })
        XCTAssertEqual(catalog.url?.path, "/Items")
        XCTAssertEqual(recent.url?.path, "/Items", "Home recently added keeps playable episodes")
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

    func testTechnicalMovieFiltersCarryAcrossLibraryPagingAndAlphabetIndex() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        HomeDelayedURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return response(request, json: ["anchors": [["letter": "A", "index": 0]], "total": 2])
            case "/Items":
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
                let start = query.first { $0.name == "StartIndex" }?.value
                var item = movie()
                item["Id"] = start == "0" ? "filtered-1" : "filtered-2"
                return response(request, json: ["Items": [item], "TotalRecordCount": 2])
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let filters = VideoFilters(
            resolution: .ultraHD, hdr: .dolbyVision, audio: .atmos, imaxOnly: true
        )
        await store.loadLibrary(kind: .movies, filters: filters)
        await store.loadMore()
        try await Task.sleep(nanoseconds: 20_000_000)

        let catalogRequests = requests.all.filter { request in
            guard request.url?.path == "/Items" else { return false }
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "LuminaResolution" && $0.value == "4k" }
        }
        XCTAssertEqual(catalogRequests.count, 2)
        for request in catalogRequests {
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            XCTAssertEqual(query.first { $0.name == "LuminaHDR" }?.value, "dolbyvision")
            XCTAssertEqual(query.first { $0.name == "LuminaAudio" }?.value, "atmos")
            XCTAssertEqual(query.first { $0.name == "LuminaIMAX" }?.value, "true")
            XCTAssertEqual(query.first { $0.name == "IncludeItemTypes" }?.value, "Movie")
        }

        let alphabet = try XCTUnwrap(requests.all.first { request in
            guard request.url?.path == "/Lumina/Library/alphabet" else { return false }
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "LuminaResolution" && $0.value == "4k" }
        })
        let alphabetQuery = URLComponents(url: alphabet.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(alphabetQuery?.first { $0.name == "LuminaResolution" }?.value, "4k")
        XCTAssertEqual(alphabetQuery?.first { $0.name == "LuminaHDR" }?.value, "dolbyvision")
        XCTAssertEqual(alphabetQuery?.first { $0.name == "LuminaAudio" }?.value, "atmos")
        XCTAssertEqual(alphabetQuery?.first { $0.name == "LuminaIMAX" }?.value, "true")

        await store.loadLibrary(search: "needle", kind: .movies, filters: filters)
        let searched = try XCTUnwrap(requests.all.last { request in
            guard request.url?.path == "/Items" else { return false }
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "SearchTerm" && $0.value == "needle" }
        })
        let searchQuery = URLComponents(url: searched.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(searchQuery?.first { $0.name == "LuminaResolution" }?.value, "4k")
        XCTAssertEqual(searchQuery?.first { $0.name == "LuminaHDR" }?.value, "dolbyvision")
        XCTAssertEqual(searchQuery?.first { $0.name == "LuminaAudio" }?.value, "atmos")
        XCTAssertEqual(searchQuery?.first { $0.name == "LuminaIMAX" }?.value, "true")
    }

    func testTechnicalMovieFiltersReadTheServerCapabilityMarker() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: []))
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Items/Filters2": return response(request, json: ["LuminaTechnicalFilters": true])
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let firstSupportCheck = try await store.supportsTechnicalVideoFilters()
        XCTAssertTrue(firstSupportCheck)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items/Filters2" }.count, 1)
    }

    func testTechnicalMovieFiltersRequireTheServerCapabilityMarker() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: [:])
        }

        let supported = try await client.supportsTechnicalVideoFilters(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", deviceID: "test-device"
        )

        XCTAssertFalse(supported, "A legacy /Items/Filters2 response must not enable queries it would ignore.")
        XCTAssertEqual(requests.all.count, 1)
        let request = try XCTUnwrap(requests.all.first)
        XCTAssertEqual(request.url?.path, "/Items/Filters2")
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "Fields" }?.value, "LuminaTechnicalFilters")
    }

    func testLibraryAlphabetSeeksTheServerIndexAndLatestRapidJumpWins() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        HomeDelayedURLProtocol.handler = { request in
            requests.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return response(request, json: ["anchors": [["letter": "A", "index": 0], ["letter": "M", "index": 50], ["letter": "Z", "index": 200]], "total": 250])
            case "/Items":
                let start = Int(value("StartIndex") ?? "0") ?? 0
                var item = movie()
                item["Id"] = start == 200 ? "zulu" : start == 50 ? "middle" : "alpha"
                return response(request, json: ["Items": [item], "TotalRecordCount": 250, "StartIndex": start])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        try await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertTrue(store.supportsLibraryAlphabet)
        XCTAssertEqual(store.libraryAlphabet, [
            LibraryAlphabetAnchor(letter: "A", index: 0), LibraryAlphabetAnchor(letter: "M", index: 50), LibraryAlphabetAnchor(letter: "Z", index: 200),
        ])

        let middle = try XCTUnwrap(store.libraryAlphabet.first { $0.letter == "M" })
        let middleID = await store.jumpLibrary(to: middle)
        XCTAssertEqual(middleID, "middle")
        await store.loadMore()
        let alphabetRequest = try XCTUnwrap(requests.all.first { $0.url?.path == "/Lumina/Library/alphabet" })
        let alphabetQuery = URLComponents(url: alphabetRequest.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(alphabetQuery?.first { $0.name == "IncludeItemTypes" }?.value, "Movie,Series,Video")
        XCTAssertEqual(alphabetQuery?.first { $0.name == "SortBy" }?.value, "SortName")
        XCTAssertEqual(alphabetQuery?.first { $0.name == "Recursive" }?.value, "true")
        let pageStarts = requests.all.compactMap { request -> String? in
            guard request.url?.path == "/Items" else { return nil }
            return URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "StartIndex" }?.value
        }
        XCTAssertTrue(pageStarts.suffix(2).elementsEqual(["50", "51"]))

        HomeDelayedURLProtocol.delay = { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "StartIndex" }?.value == "50" ? 0.15 : 0.01
        }
        async let stale = store.jumpLibrary(to: middle)
        await Task.yield()
        let zulu = try XCTUnwrap(store.libraryAlphabet.first { $0.letter == "Z" })
        async let current = store.jumpLibrary(to: zulu)
        let staleID = await stale
        let currentID = await current
        XCTAssertNil(staleID)
        XCTAssertEqual(currentID, "zulu")
        XCTAssertEqual(store.items.map(\.id), ["zulu"])

        // A departing screen can cancel a jump without replacing it.  It must
        // retain the last visible page and avoid surfacing the cancellation.
        let visibleBeforeCancellation = store.items.map(\.id)
        let canceled = Task { await store.jumpLibrary(to: middle) }
        try await Task.sleep(nanoseconds: 20_000_000)
        canceled.cancel()
        let canceledID = await canceled.value
        XCTAssertNil(canceledID)
        XCTAssertEqual(store.items.map(\.id), visibleBeforeCancellation)
        XCTAssertNil(store.errorMessage)

        await store.loadLibrary(search: "Movie", kind: .movies)
        XCTAssertFalse(store.supportsLibraryAlphabet)
        XCTAssertTrue(store.libraryAlphabet.isEmpty)
    }

    func testUnavailableLibraryAlphabetKeepsTheNormalFirstPage() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/Lumina/Library/alphabet": return response(request, status: 404)
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])
        XCTAssertTrue(store.libraryAlphabet.isEmpty)
        XCTAssertNil(store.errorMessage)
    }

    func testSlowLibraryAlphabetDoesNotDelayTheFirstCatalogPage() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.delay = { request in request.url?.path == "/Lumina/Library/alphabet" ? 0.15 : 0 }
        HomeDelayedURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/Lumina/Library/alphabet": return response(request, json: ["anchors": [["letter": "M", "index": 0]], "total": 1])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])
        XCTAssertTrue(store.libraryAlphabet.isEmpty)
        try await Task.sleep(nanoseconds: 200_000_000)
        XCTAssertEqual(store.libraryAlphabet, [LibraryAlphabetAnchor(letter: "M", index: 0)])
    }

    func testNavigationCategoriesRefreshAndStayScopedToTheConnectedServer() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let credentials = TestCredentials(session: saved)
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: credentials)
        func serve(_ categories: [String], status: Int = 200) {
            HomeDelayedURLProtocol.handler = { request in
                switch request.url!.path {
                case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
                case "/Lumina/Settings/navigation": return response(request, status: status, json: ["categories": categories])
                default: return response(request, json: page(items: []))
                }
            }
        }
        serve(["movies", "anime", "books"])
        await store.restore()
        XCTAssertEqual(store.availableLibraryCategories, ["movies", "anime", "books"])
        serve(["tv"])
        await store.refreshNavigationCategories()
        XCTAssertEqual(store.availableLibraryCategories, ["tv"])
        serve([], status: 503)
        await store.loadHome()
        XCTAssertEqual(store.availableLibraryCategories, ["tv"], "Transient errors retain this server's last successful menu.")
        await store.signOut()
        XCTAssertTrue(store.availableLibraryCategories.isEmpty)
        credentials.stored = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8766")!, accessToken: "other-token", userID: "user-1", username: "member", serverName: "Other", deviceID: "test-device")
        serve(["music"])
        await store.restore()
        XCTAssertEqual(store.availableLibraryCategories, ["music"], "A new server must never inherit another server's libraries.")
        serve([])
        await store.loadHome()
        XCTAssertTrue(store.availableLibraryCategories.isEmpty, "Removing the final library must clear its category.")
    }

    func testHomeLoadsShelvesConcurrentlyWithTheSameFirstShelfTime() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let timeline = HomeRequestTimeline()
        HomeDelayedURLProtocol.timeline = timeline
        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.1 : 0
        }
        HomeDelayedURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items":
                var recent = movie()
                recent["Id"] = "recent"
                return response(request, json: page(items: [recent]))
            case "/UserItems/Resume":
                var resume = movie(positionTicks: 10_000_000)
                resume["Id"] = "resume"
                return response(request, json: page(items: [resume]))
            case "/Shows/NextUp":
                return response(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        timeline.start()
        let baseline = LuminaClient(configuration: homeConfiguration())
        _ = try await baseline.items(origin: saved.serverURL, token: saved.accessToken, deviceID: saved.deviceID, search: "", start: 0, sort: .recentlyAdded)
        _ = try await baseline.resume(origin: saved.serverURL, token: saved.accessToken, deviceID: saved.deviceID)
        _ = try await baseline.nextUp(origin: saved.serverURL, token: saved.accessToken, deviceID: saved.deviceID)
        let before = timeline.measurement
        timeline.start()
        await store.loadHome()
        let after = timeline.measurement
        print("PERF native Home: before completion ms=\(before.elapsedMilliseconds), first shelf ms=\(before.firstCompletionMilliseconds), max concurrent requests=\(before.maxConcurrent); after completion ms=\(after.elapsedMilliseconds), first shelf ms=\(after.firstCompletionMilliseconds), max concurrent requests=\(after.maxConcurrent)")

        XCTAssertGreaterThanOrEqual(before.elapsedMilliseconds, 280)
        XCTAssertEqual(before.maxConcurrent, 1)
        XCTAssertLessThan(after.elapsedMilliseconds, 180)
        XCTAssertGreaterThanOrEqual(after.maxConcurrent, 3)
        XCTAssertLessThanOrEqual(after.firstCompletionMilliseconds, before.firstCompletionMilliseconds + 30)
        XCTAssertEqual(store.recentItems.map(\.id), ["recent"])
        XCTAssertEqual(store.resumeItems.map(\.id), ["resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["next"])
    }

    func testHomePublishesAnAvailableShelfWhileAnotherShelfIsStillLoading() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let counts = PathCounter()
        HomeDelayedURLProtocol.delay = { request in
            switch request.url?.path {
            case "/Items": 0.3
            case "/UserItems/Resume": 0.05
            case "/Shows/NextUp": 0.1
            default: 0
            }
        }
        HomeDelayedURLProtocol.handler = { request in
            let sort = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SortBy" }?.value
            let count = counts.next("\(request.url!.path):\(sort ?? "")")
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [["Id": count == 1 ? "old-recent" : "recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return response(request, json: page(items: [["Id": count == 1 ? "old-resume" : "resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let resumePublished = expectation(description: "resume shelf published")
        withObservationTracking {
            _ = store.resumeItems
        } onChange: {
            resumePublished.fulfill()
        }
        let load = Task { await store.loadHome() }
        await fulfillment(of: [resumePublished], timeout: 1)
        XCTAssertEqual(store.resumeItems.map(\.id), ["resume"])
        XCTAssertEqual(store.recentItems.map(\.id), ["old-recent"])
        await load.value
        XCTAssertEqual(store.recentItems.map(\.id), ["recent"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["next"])
    }

    func testHomeKeepsSuccessfulShelvesWhenRecentFails() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            let sort = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SortBy" }?.value
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items" where sort == "DateCreated": return response(request, status: 404)
            case "/Items": return response(request, json: page(items: []))
            case "/UserItems/Resume": return response(request, json: page(items: [["Id": "resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        XCTAssertEqual(store.resumeItems.map(\.id), ["resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["next"])
        XCTAssertEqual(store.errorMessage, "The server returned HTTP 404 (not found). Check the server address and that Apps like Infuse is enabled in Lumina Settings.")
    }

    func testNewLibraryQueryCancelsTheObsoleteTransportAndPublishesOnlyTheLatestPage() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token",
            userID: "user-1", username: "Member", serverName: "Lumina", deviceID: "fixture")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { @Sendable request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "user-1", "Name": "Member"]) }
            if request.url?.path == "/Items" {
                let search = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SearchTerm" }?.value ?? "initial"
                return response(request, json: page(items: [["Id": search, "Name": search, "Type": "Movie"]]))
            }
            return response(request, json: page(items: []))
        }
        await store.restore()
        let started = expectation(description: "Obsolete search reached transport")
        HomeDelayedURLProtocol.delay = { @Sendable request in
            request.url?.query?.contains("SearchTerm=obsolete") == true ? 0.3 : 0
        }
        HomeDelayedURLProtocol.onStart = { @Sendable request in
            if request.url?.query?.contains("SearchTerm=obsolete") == true { started.fulfill() }
        }
        let before = HomeDelayedURLProtocol.cancelledRequests
        let obsolete = Task { await store.loadLibrary(search: "obsolete") }
        await fulfillment(of: [started], timeout: 2)
        await store.loadLibrary(search: "current")
        await obsolete.value
        let cancellations = HomeDelayedURLProtocol.cancelledRequests - before
        print("LUMINA_LIBRARY_BENCH obsolete_transport_cancellations=\(cancellations)")
        XCTAssertGreaterThanOrEqual(cancellations, 1, "A superseded search must stop its network/decoding work")
        XCTAssertEqual(store.items.map(\.id), ["current"])
        XCTAssertFalse(store.isBusy)
        XCTAssertNil(store.errorMessage)
    }

    func testLibraryResponseCannotPublishAfterMemberIdentityChangesWithSameCredential() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token",
            userID: "one", username: "One", serverName: "Lumina", deviceID: "fixture")
        let credentials = TestCredentials(session: saved)
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: credentials)
        HomeDelayedURLProtocol.handler = { @Sendable request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "one", "Name": "One"]) }
            return response(request, json: page(items: []))
        }
        await store.restore()
        let started = expectation(description: "First member search reached transport")
        HomeDelayedURLProtocol.handler = { @Sendable request in
            response(request, json: page(items: [["Id": "private-one", "Name": "Private", "Type": "Movie"]]))
        }
        HomeDelayedURLProtocol.delay = { @Sendable request in request.url?.path == "/Items" ? 0.3 : 0 }
        HomeDelayedURLProtocol.onStart = { @Sendable request in
            if request.url?.path == "/Items" { started.fulfill() }
        }
        let loading = Task { await store.loadLibrary(search: "first-member") }
        await fulfillment(of: [started], timeout: 2)
        credentials.stored = LuminaSession(serverURL: saved.serverURL, accessToken: saved.accessToken,
            userID: "two", username: "Two", serverName: "Lumina", deviceID: "fixture")
        HomeDelayedURLProtocol.handler = { @Sendable request in response(request, status: 503, json: [:]) }
        await store.restore()
        await loading.value
        XCTAssertEqual(store.session?.userID, "two")
        XCTAssertTrue(store.items.isEmpty, "A prior member's in-flight page must not publish under the new member")
    }

    func testNewHomeLoadCancelsTheObsoleteShelfRequests() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let counts = PathCounter()
        HomeDelayedURLProtocol.handler = { request in
            let sort = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SortBy" }?.value
            let count = counts.next("\(request.url!.path):\(sort ?? "")")
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [["Id": count == 1 ? "restore-recent" : "current-recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return response(request, json: page(items: [["Id": count == 1 ? "restore-resume" : "current-resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": count == 1 ? "restore-next" : "current-next", "Name": "Next", "Type": "Episode"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.3 : 0
        }
        let obsoleteStarts = RequestStartRecorder()
        HomeDelayedURLProtocol.onStart = { obsoleteStarts.record($0) }
        let obsolete = Task { await store.loadHome() }
        await obsoleteStarts.waitForThree()
        HomeDelayedURLProtocol.delay = { _ in 0.01 }
        let current = Task { await store.loadHome() }
        await current.value
        await obsolete.value

        XCTAssertGreaterThanOrEqual(HomeDelayedURLProtocol.cancelledRequests, 3)
        XCTAssertEqual(store.recentItems.map(\.id), ["current-recent"])
        XCTAssertEqual(store.resumeItems.map(\.id), ["current-resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["current-next"])

        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.3 : 0
        }
        let cancelledStarts = RequestStartRecorder()
        HomeDelayedURLProtocol.onStart = { cancelledStarts.record($0) }
        let cancelled = Task { await store.loadHome() }
        await cancelledStarts.waitForThree()
        cancelled.cancel()
        await cancelled.value
        XCTAssertGreaterThanOrEqual(HomeDelayedURLProtocol.cancelledRequests, 6)
        XCTAssertFalse(store.isHomeBusy)
    }

    func testSignOutCancelsPendingHomeBeforeReconnect() async throws {
        let old = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "old-token", userID: "old-user", username: "old member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: old))
        HomeDelayedURLProtocol.handler = { request in
            let token = request.value(forHTTPHeaderField: "Authorization")?.contains("new-token") == true
            switch request.url!.path {
            case "/System/Info/Public": return response(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return response(request, json: ["AccessToken": "new-token", "User": ["Id": "new-user", "Name": "new member", "ServerName": "Lumina"]])
            case "/Users/Me": return response(request, json: ["Id": token ? "new-user" : "old-user", "Name": token ? "new member" : "old member"])
            case "/Sessions/Logout": return response(request, status: 204)
            case "/Items": return response(request, json: page(items: [["Id": token ? "new-recent" : "old-recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return response(request, json: page(items: [["Id": token ? "new-resume" : "old-resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": token ? "new-next" : "old-next", "Name": "Next", "Type": "Episode"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let starts = RequestStartRecorder()
        HomeDelayedURLProtocol.onStart = { request in
            if ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) { starts.record(request) }
        }
        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.3 : 0
        }
        let oldHome = Task { await store.loadHome() }
        await starts.waitForThree()
        await store.signOut()
        await oldHome.value

        XCTAssertNil(store.session)
        XCTAssertGreaterThanOrEqual(HomeDelayedURLProtocol.cancelledRequests, 3)
        HomeDelayedURLProtocol.delay = { _ in 0 }
        await store.connect(server: "http://127.0.0.1:8765", username: "new member", password: "test-password")
        XCTAssertEqual(store.session?.userID, "new-user")
        XCTAssertEqual(store.recentItems.map(\.id), ["new-recent"])
        XCTAssertEqual(store.resumeItems.map(\.id), ["new-resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["new-next"])
    }

    func testRefreshPublishesHomeWhileCatalogIsSlowAndKeepsCatalogError() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let counts = PathCounter()
        HomeDelayedURLProtocol.delay = { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let sort = query.first { $0.name == "SortBy" }?.value
            return request.url?.path == "/Items" && sort == "SortName" ? 0.3 : 0.03
        }
        HomeDelayedURLProtocol.handler = { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let sort = query.first { $0.name == "SortBy" }?.value
            let count = counts.next("\(request.url!.path):\(sort ?? "")")
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items" where sort == "SortName":
                return response(request, json: page(items: [["Id": "catalog-\(count)", "Name": "Catalog", "Type": "Movie"]]))
            case "/Items":
                return response(request, json: page(items: [["Id": "recent-\(count)", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let homePublished = expectation(description: "Home recent shelf published")
        withObservationTracking {
            _ = store.recentItems
        } onChange: {
            homePublished.fulfill()
        }
        let refresh = Task { await store.refresh() }
        await fulfillment(of: [homePublished], timeout: 1)
        XCTAssertEqual(store.recentItems.map(\.id), ["recent-2"])
        XCTAssertEqual(store.items.map(\.id), ["catalog-1"])
        await refresh.value
        XCTAssertEqual(store.items.map(\.id), ["catalog-2"])

        HomeDelayedURLProtocol.delay = { request in
            let sort = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SortBy" }?.value
            return request.url?.path == "/Items" && sort == "SortName" ? 0 : 0.1
        }
        HomeDelayedURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Items":
                let sort = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SortBy" }?.value
                if sort == "SortName" { return response(request, status: 404) }
                return response(request, json: page(items: [["Id": "recent-after-error", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }
        await store.refresh()
        XCTAssertEqual(store.recentItems.map(\.id), ["recent-after-error"])
        XCTAssertEqual(store.errorMessage, "The server returned HTTP 404 (not found). Check the server address and that Apps like Infuse is enabled in Lumina Settings.")
    }

    func testMusicCollectionResolvesOnlyAuthenticatedAlbumAndArtistPivotsWithoutReplacingBrowsePage() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        let hyphenatedAlbumID = "123e4567-e89b-12d3-a456-426614174000"
        let compactAlbumID = "123e4567e89b12d3a456426614174000"
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/album-1":
                return response(request, json: ["Id": "album-1", "Name": "Album", "Type": "MusicAlbum"])
            case "/Users/user-1/Items/\(compactAlbumID)":
                return response(request, json: ["Id": compactAlbumID, "Name": "UUID Album", "Type": "MusicAlbum"])
            case "/Users/user-1/Items/artist-1":
                return response(request, json: ["Id": "artist-1", "Name": "Artist", "Type": "MusicArtist"])
            case "/Users/user-1/Items/wrong-type":
                return response(request, json: ["Id": "wrong-type", "Name": "Artist", "Type": "MusicArtist"])
            case "/Users/user-1/Items/wrong-id":
                return response(request, json: ["Id": "another-id", "Name": "Album", "Type": "MusicAlbum"])
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])

        let album = try await store.musicCollection(id: "album-1", expectedType: "MusicAlbum")
        let uuidAlbum = try await store.musicCollection(id: hyphenatedAlbumID, expectedType: "MusicAlbum")
        let artist = try await store.musicCollection(id: "artist-1", expectedType: "MusicArtist")
        XCTAssertEqual([album.id, album.type], ["album-1", "MusicAlbum"])
        XCTAssertEqual([uuidAlbum.id, uuidAlbum.type], [compactAlbumID, "MusicAlbum"])
        XCTAssertEqual([artist.id, artist.type], ["artist-1", "MusicArtist"])
        XCTAssertEqual(store.items.map(\.id), ["movie-1"], "A music pivot must not replace the video browse page.")

        for (id, type) in [("wrong-type", "MusicAlbum"), ("wrong-id", "MusicAlbum"), ("album-1", "Audio")] {
            do {
                _ = try await store.musicCollection(id: id, expectedType: type)
                XCTFail("Music pivots must reject unexpected identifiers and types.")
            } catch {
                XCTAssertEqual(error as? LuminaError, .malformedResponse)
            }
        }
        XCTAssertEqual(store.items.map(\.id), ["movie-1"])

        let pivots = requests.all.filter { $0.url?.path.hasPrefix("/Users/user-1/Items/") == true }
        XCTAssertEqual(pivots.map { $0.url?.path }, [
            "/Users/user-1/Items/album-1",
            "/Users/user-1/Items/\(compactAlbumID)",
            "/Users/user-1/Items/artist-1",
            "/Users/user-1/Items/wrong-type",
            "/Users/user-1/Items/wrong-id",
        ])
        XCTAssertTrue(pivots.allSatisfy {
            $0.httpMethod == "GET" && $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true
        })
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
                if value("ParentId") == "shows-view" {
                    return response(request, json: ["Items": [["Id": value("StartIndex") == "0" ? "series-1" : "series-2", "Name": "Show", "Type": "Series"]], "TotalRecordCount": 2])
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
        await store.loadLibrary(search: "Show", kind: .shows, sort: .recentlyAdded, filters: filters)
        XCTAssertTrue(store.items.first?.isCollection == true)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["series-1", "series-2"])
        let series = try XCTUnwrap(store.items.first)
        let seasons = try await store.loadChildren(of: series)
        XCTAssertEqual(seasons.map(\.id), ["season-1", "season-2"])
        let episodes = try await store.loadChildren(of: seasons[0])
        XCTAssertEqual(episodes.map(\.id), ["episode-1", "episode-2"])
        let pages = calls.all.filter { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "ParentId" && $0.value == "shows-view" } == true }
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

    func testNativeMovieAndAnimeCategoriesUseTheWebsiteWallTaxonomy() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Items":
                switch value("LuminaCategory") {
                case "anime": return response(request, json: page(items: [
                    ["Id": "anime-film", "Name": "Anime Film", "Type": "Movie"],
                    ["Id": "anime-series", "Name": "Anime Series", "Type": "Series"],
                ]))
                case "movies": return response(request, json: page(items: [["Id": "movie", "Name": "Movie", "Type": "Movie"]]))
                default: return response(request, json: page(items: [movie()]))
                }
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        await store.loadLibrary(kind: .anime)
        XCTAssertEqual(store.items.map(\.id), ["anime-film", "anime-series"])
        await store.loadLibrary(kind: .movies)
        XCTAssertEqual(store.items.map(\.id), ["movie"])

        let categoryRequests = calls.all.filter { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "LuminaCategory" } == true
        }
        let anime = try XCTUnwrap(categoryRequests.first { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "LuminaCategory" && $0.value == "anime" } == true
        })
        let animeQuery = URLComponents(url: anime.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(animeQuery?.first { $0.name == "IncludeItemTypes" }?.value, "Movie,Series")
        XCTAssertNil(animeQuery?.first { $0.name == "ParentId" })
        XCTAssertTrue(categoryRequests.contains { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "LuminaCategory" && $0.value == "movies" } == true
        })
    }

    func testHomeSearchIsBoundedAndDoesNotReplaceTheActiveLibraryWall() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let calls = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            calls.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let search = query.first { $0.name == "SearchTerm" }?.value
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Lumina/Library/alphabet": return response(request, json: ["anchors": [], "total": 1])
            case "/Items":
                if search == "signal" {
                    return response(request, json: page(items: [
                        ["Id": "signal-movie", "Name": "Signal", "Type": "Movie"],
                        ["Id": "signal-album", "Name": "Signal", "Type": "MusicAlbum"],
                    ]))
                }
                return response(request, json: page(items: [["Id": "library-movie", "Name": "Library", "Type": "Movie"]]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        await store.loadLibrary(kind: .movies)
        let libraryBeforeSearch = store.items.map(\.id)
        let results = try await store.searchHome(query: " signal ")

        XCTAssertEqual(results.map(\.id), ["signal-movie", "signal-album"])
        XCTAssertEqual(store.items.map(\.id), libraryBeforeSearch)
        XCTAssertFalse(store.isBusy)
        let searchRequest = try XCTUnwrap(calls.all.last { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "SearchTerm" && $0.value == "signal" } == true
        })
        let searchQuery = URLComponents(url: searchRequest.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(searchQuery?.first { $0.name == "IncludeItemTypes" }?.value, "Movie,Series,Episode,MusicAlbum,MusicArtist,Audio")
        XCTAssertEqual(searchQuery?.first { $0.name == "Limit" }?.value, "50")
    }

    func testYouTubeLibraryUsesSavedProviderChannelOnly() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/UserViews": return response(request, json: page(items: [["Id": "channels-view", "Name": "Channels", "Type": "CollectionFolder"]]))
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Items":
                if value("ParentId") == "channels-view" {
                    return response(request, json: page(items: [["Id": "saved-youtube-video", "Name": "Saved video", "Type": "Episode"]]))
                }
                return response(request, json: page(items: [movie()]))
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        await store.loadLibrary(search: "Saved", kind: .youtube, sort: .recentlyAdded)

        XCTAssertEqual(store.items.map(\.id), ["saved-youtube-video"])
        let request = try XCTUnwrap(calls.all.last { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "ParentId" && $0.value == "channels-view" }
        })
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "IncludeItemTypes" }?.value, "Episode")
        XCTAssertEqual(query?.first { $0.name == "SourceProvider" }?.value, "youtube")
        XCTAssertEqual(query?.first { $0.name == "SearchTerm" }?.value, "Saved")
    }

    func testMediaItemDecodesOptionalTitleMetadata() throws {
        var payload = movie()
        payload["OfficialRating"] = "PG-13"
        payload["CommunityRating"] = 8.4
        payload["Genres"] = ["Animation", "Adventure"]
        payload["People"] = [["Name": "Example Actor", "Type": "Actor"]]

        let item = try JSONDecoder().decode(MediaItem.self, from: json(payload))

        XCTAssertEqual(item.officialRating, "PG-13")
        XCTAssertEqual(item.communityRating, 8.4)
        XCTAssertEqual(item.genres, ["Animation", "Adventure"])
        XCTAssertEqual(item.people?.first?.name, "Example Actor")
        XCTAssertEqual(item.people?.first?.type, "Actor")
    }

    func testMediaPersonDecodesCreditAndBuildsSignedImageURL() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://example.test")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }
        await store.restore()

        var payload = movie()
        payload["People"] = [[
            "Id": "person-1", "Name": "Example Actor", "Type": "Actor",
            "Role": "Lead", "PrimaryImageTag": "person-image-tag",
        ]]
        let person = try XCTUnwrap(JSONDecoder().decode(MediaItem.self, from: json(payload)).people?.first)
        XCTAssertEqual(person.id, "person-1")
        XCTAssertEqual(person.name, "Example Actor")
        XCTAssertEqual(person.type, "Actor")
        XCTAssertEqual(person.role, "Lead")
        XCTAssertEqual(person.primaryImageTag, "person-image-tag")

        let image = try XCTUnwrap(store.personImageURL(for: person, maxWidth: 0))
        XCTAssertEqual(image.host, "example.test")
        XCTAssertEqual(image.path, "/Items/person-1/Images/Primary")
        let query = URLComponents(url: image, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "maxWidth" }?.value, "1")
        XCTAssertEqual(query?.first { $0.name == "tag" }?.value, "person-image-tag")
        XCTAssertEqual(query?.first { $0.name == "ApiKey" }?.value, "test-token")
    }

    func testRelatedItemsUseAuthenticatedBoundedSimilarEndpointWithoutMutatingLibrary() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://example.test")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Items/movie-1/Similar":
                return response(request, json: page(items: [
                    movie(),
                    ["Id": "related-1", "Name": "Related One", "Type": "Movie"],
                    ["Id": "related-2", "Name": "Related Two", "Type": "Movie"],
                    ["Id": "related-1", "Name": "Duplicate", "Type": "Movie"],
                ]))
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try XCTUnwrap(store.items.first)
        let libraryBefore = store.items

        let related = try await store.relatedItems(for: item)

        XCTAssertEqual(related.map(\.id), ["related-1", "related-2"])
        XCTAssertEqual(store.items, libraryBefore, "Related titles must not replace the active library page.")
        let request = try XCTUnwrap(requests.all.last { $0.url?.path == "/Items/movie-1/Similar" })
        XCTAssertEqual(request.url?.host, "example.test")
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true)
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "Limit" }?.value, "12")
        XCTAssertEqual(query?.first { $0.name == "Fields" }?.value, "Overview,UserData,ImageTags,People")
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
        XCTAssertNil(playbackBody["MaxStreamingBitrate"], "Automatic quality must preserve the uncapped request contract.")
        let profile = playbackBody["DeviceProfile"] as? [String: Any]
        let direct = (profile?["DirectPlayProfiles"] as? [[String: Any]])?.first?["Container"] as? String
        XCTAssertEqual(direct, "mp4,m4v,mov")
        let mkv = (profile?["DirectPlayProfiles"] as? [[String: Any]])?.last
        XCTAssertEqual(mkv?["Container"] as? String, "mkv")
        XCTAssertEqual(mkv?["AudioCodec"] as? String, "aac,ac3,eac3,alac,flac")
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

    func testPlaybackRequestCarriesServerStreamInventoryAndExplicitSubtitleOff() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "tracks", "MediaSources": [[
                "Id": "source-1", "Container": "mkv", "Bitrate": 1_500_000, "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "DefaultAudioStreamIndex": 2,
                "MediaStreams": [
                    ["Index": 0, "Type": "Video", "DisplayTitle": "2160p HEVC", "Codec": "hevc"],
                    ["Index": 2, "Type": "Audio", "DisplayTitle": "English - E-AC-3 5.1", "Codec": "eac3", "Language": "eng", "Channels": 6, "IsDefault": true],
                    ["Index": 5, "Type": "Subtitle", "Title": "English SDH", "Codec": "subrip", "Language": "eng", "IsExternal": true],
                ],
            ]],
        ]))

        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item,
            result: info, subtitleStreamIndex: -1
        )

        XCTAssertTrue(request.usesClientDemux)
        XCTAssertEqual(request.audioStreamIndex, 2)
        XCTAssertEqual(request.bitRate, 1_500_000)
        XCTAssertEqual(request.subtitleStreamIndex, -1)
        XCTAssertEqual(request.mediaStreams.map(\.index), [0, 2, 5])
        XCTAssertEqual(request.mediaStreams[1].type, .audio)
        XCTAssertEqual(request.mediaStreams[1].channels, 6)
        XCTAssertTrue(request.mediaStreams[1].isDefault)
        XCTAssertEqual(request.mediaStreams[2].label, "English SDH")
        XCTAssertTrue(request.mediaStreams[2].isExternal)
    }

    func testPlaybackRequestCarriesSanitizedDetailChapters() throws {
        var payload = movie()
        payload["Chapters"] = [
            ["StartPositionTicks": 50_000_000, "Name": "Middle"],
            ["StartPositionTicks": 0, "Name": " Introduction "],
            ["StartPositionTicks": 50_000_000, "Name": "Duplicate"],
            ["StartPositionTicks": 120_000_000, "Name": "At end"],
            ["StartPositionTicks": -1, "Name": "Negative"],
            "not a chapter",
            NSNull(),
        ]
        let item = try JSONDecoder().decode(MediaItem.self, from: json(payload))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "chapters", "MediaSources": [[
                "Id": "source-1", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "RunTimeTicks": 120_000_000,
                "Chapters": [
                    ["StartPositionTicks": 60_000_000, "Name": "Source cut"],
                ],
            ]],
        ]))

        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
        )

        XCTAssertEqual(request.chapters.map(\.id), [60_000_000])
        XCTAssertEqual(request.chapters.map(\.title), ["Source cut"])
        XCTAssertEqual(request.chapters.map(\.startPositionSeconds), [6])

        let legacyInfo = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "legacy", "MediaSources": [[
                "Id": "source-preferred", "SupportsDirectPlay": true, "SupportsTranscoding": false,
            ]],
        ]))
        let legacy = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: legacyInfo,
        )
        XCTAssertEqual(legacy.chapters.map(\.id), [0, 50_000_000])

        let preferredInfo = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "preferred", "MediaSources": [["Id": item.id, "SupportsDirectPlay": true, "SupportsTranscoding": false]],
        ]))
        let preferred = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item,
            result: preferredInfo, mediaSourceID: item.id,
        )
        XCTAssertEqual(preferred.chapters.map(\.id), [0, 50_000_000],
                       "Selecting the preferred source must retain item-level Jellyfin chapters.")

        let alternateInfo = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "alternate", "MediaSources": [[
                "Id": "source-alternate", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "RunTimeTicks": 120_000_000, "Chapters": [],
            ]],
        ]))
        let alternate = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item,
            result: alternateInfo, mediaSourceID: "source-alternate",
        )
        XCTAssertTrue(alternate.chapters.isEmpty)
    }

    func testPlaybackMarkersAndNextEpisodeUseRecordedSourceMetadataAndSeriesOrder() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        var currentPayload = movie()
        currentPayload["Id"] = "episode-1"
        currentPayload["Type"] = "Episode"
        currentPayload["SeriesId"] = "series-1"
        let current = currentPayload
        var followingPayload = current
        followingPayload["Id"] = "episode-2"
        followingPayload["Name"] = "Following Episode"
        let following = followingPayload
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/MediaSegments/source-episode":
                return response(request, json: ["Items": [
                    ["Type": "Intro", "StartTicks": 0, "EndTicks": 100_000_000],
                    ["Type": "Outro", "StartTicks": 100_000_000, "EndTicks": 120_000_000],
                    ["Type": "Unknown", "StartTicks": 20, "EndTicks": 30],
                    ["Type": "Recap", "StartTicks": 50, "EndTicks": 50],
                    NSNull(),
                ]])
            case "/Shows/series-1/Episodes":
                return response(request, json: page(items: [current, following]))
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(current))
        let request = PlaybackRequest(id: "play", item: item, url: URL(string: "http://127.0.0.1:8765/Videos/episode-1/stream")!,
                                      startPositionSeconds: 0, mediaSourceID: "source-episode", isTranscoding: false, methodLabel: "Direct")

        let markers = try await store.playbackMarkers(for: request)
        let next = try await store.nextEpisode(after: item)

        XCTAssertEqual(markers.map(\.kind), [.intro, .credits])
        XCTAssertEqual(markers.map(\.startSeconds), [0, 10])
        XCTAssertEqual(next?.id, "episode-2")
        XCTAssertTrue(requests.all.contains { $0.url?.path == "/MediaSegments/source-episode" })
        let nextEpisodeRequest = try XCTUnwrap(requests.all.first { $0.url?.path == "/Shows/series-1/Episodes" })
        let nextEpisodeQuery = URLComponents(url: nextEpisodeRequest.url!, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(nextEpisodeQuery?.first { $0.name == "StartItemId" }?.value, "episode-1")
        XCTAssertEqual(nextEpisodeQuery?.first { $0.name == "Limit" }?.value, "2")
    }

    func testExternalVTTSubtitleUsesAnAuthenticatedCredentialFreeDeliveryRequest() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "vtt", "MediaSources": [[
                    "Id": "source-1", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                    "MediaStreams": [[
                        "Index": 5, "Type": "Subtitle", "DisplayTitle": "English", "Codec": "subrip",
                        "Language": "eng", "IsExternal": true, "DeliveryMethod": "External",
                        "DeliveryUrl": "/Videos/source-1/Subtitles/5/Stream.vtt?ApiKey=leaked-token",
                    ]],
                ]]])
            case "/Videos/source-1/Subtitles/5/Stream.vtt":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "text/vtt"])!, Data("WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n".utf8))
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let playback = try await store.preparePlayback(for: item)
        let stream = try XCTUnwrap(playback.mediaStreams.first)

        XCTAssertTrue(stream.hasExternalSubtitle)
        let file = try await store.downloadSubtitle(for: stream)
        defer { try? FileManager.default.removeItem(at: file) }
        XCTAssertTrue(try String(contentsOf: file, encoding: .utf8).hasPrefix("WEBVTT"))
        XCTAssertEqual(try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int, 0o600)
        let subtitleRequest = try XCTUnwrap(requests.all.first { $0.url?.path == "/Videos/source-1/Subtitles/5/Stream.vtt" })
        XCTAssertNil(URLComponents(url: subtitleRequest.url!, resolvingAgainstBaseURL: false)?.query)
        XCTAssertTrue(subtitleRequest.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true)
    }

    func testRefusesCrossOriginExternalVTTMetadata() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "vtt", "MediaSources": [[
                "Id": "source", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "MediaStreams": [[
                    "Index": 1, "Type": "Subtitle", "Codec": "vtt", "DeliveryMethod": "External",
                    "DeliveryUrl": "https://elsewhere.invalid/subtitle.vtt",
                ]],
            ]],
        ]))

        XCTAssertThrowsError(try LuminaClient().playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)) {
            XCTAssertEqual($0 as? LuminaError, .unsafeURL)
        }
    }

    func testExternalTextSubtitleRequiresConvertedVTTDelivery() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "subrip", "MediaSources": [[
                "Id": "source", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "MediaStreams": [[
                    "Index": 7, "Type": "Subtitle", "Codec": "subrip", "DeliveryMethod": "External",
                    "DeliveryUrl": "/Videos/source/Subtitles/7/Stream.srt?ApiKey=test-token",
                ]],
            ]],
        ]))

        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info
        )
        XCTAssertFalse(request.mediaStreams[0].hasExternalSubtitle)
    }

    func testServerHLSMasterResolvesItsSingleMuxedVariantAndRetainsPlaybackQuery() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let origin = URL(string: "http://127.0.0.1:8765")!
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            let playlist: String
            if request.url?.path == "/Videos/source/master.m3u8" {
                playlist = """
                #EXTM3U
                #EXT-X-VERSION:7
                #EXT-X-STREAM-INF:BANDWIDTH=2000000
                hls/session-1/index.m3u8
                """
            } else {
                playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.m4s\n"
            }
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                    headerFields: ["Content-Type": "application/vnd.apple.mpegurl"])!, Data(playlist.utf8))
        }

        let variant = try await client.hlsMediaVariant(
            origin: origin, token: "test-token", deviceID: "test-device",
            masterURL: URL(string: "http://127.0.0.1:8765/Videos/source/master.m3u8?ApiKey=playback-token")!
        )

        XCTAssertEqual(variant.path, "/Videos/source/hls/session-1/index.m3u8")
        XCTAssertEqual(URLComponents(url: variant, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "playback-token")
        let media = try await client.hlsMediaVariant(origin: origin, token: "test-token", deviceID: "test-device", masterURL: variant)
        XCTAssertEqual(media, variant)
        let hlsRequests = requests.all
        XCTAssertEqual(hlsRequests.map { $0.url?.path }, ["/Videos/source/master.m3u8", "/Videos/source/hls/session-1/index.m3u8"])
        XCTAssertTrue(hlsRequests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true })
        XCTAssertEqual(URLComponents(url: hlsRequests[0].url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.name, "ApiKey")
    }

    func testServerHLSVariantResolverRejectsMultiVariantAndAlternateAudioMasters() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let origin = URL(string: "http://127.0.0.1:8765")!
        MockURLProtocol.handler = { request in
            let playlist: String
            if request.url?.path == "/multi.m3u8" {
                playlist = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\none.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2\ntwo.m3u8\n"
            } else {
                playlist = "#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"alternate\"\n#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO=\"alternate\"\nvideo.m3u8\n"
            }
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                    headerFields: ["Content-Type": "application/vnd.apple.mpegurl"])!, Data(playlist.utf8))
        }
        for path in ["multi.m3u8", "alternate.m3u8"] {
            do {
                _ = try await client.hlsMediaVariant(origin: origin, token: "test-token", deviceID: "test-device",
                                                     masterURL: origin.appendingPathComponent(path))
                XCTFail("Expected unsupported HLS master rejection")
            } catch {
                XCTAssertEqual(error as? LuminaError, .malformedResponse)
            }
        }
    }

    func testExternalVTTDownloadRejectsOversizedOrMalformedBodies() async throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let origin = URL(string: "http://127.0.0.1:8765")!
        MockURLProtocol.handler = { request in
            if request.url!.path == "/too-large.vtt" {
                let data = Data("WEBVTT\n\n".utf8) + Data(repeating: 0x20, count: 10 * 1024 * 1024)
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "text/vtt"])!, data)
            }
            if request.url!.path == "/bad-vtt-prefix.vtt" {
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "text/vtt"])!, Data("WEBVTTgarbage\n\n".utf8))
            }
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                    headerFields: ["Content-Type": "text/vtt"])!, Data("not a subtitle".utf8))
        }

        do {
            _ = try await client.subtitle(origin: origin, token: "test-token", deviceID: "test-device",
                                          url: URL(string: "http://127.0.0.1:8765/too-large.vtt")!)
            XCTFail("Expected oversized subtitle rejection")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        do {
            _ = try await client.subtitle(origin: origin, token: "test-token", deviceID: "test-device",
                                          url: URL(string: "http://127.0.0.1:8765/not-vtt.vtt")!)
            XCTFail("Expected non-WebVTT subtitle rejection")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        do {
            _ = try await client.subtitle(origin: origin, token: "test-token", deviceID: "test-device",
                                          url: URL(string: "http://127.0.0.1:8765/bad-vtt-prefix.vtt")!)
            XCTFail("Expected malformed WebVTT header rejection")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
    }

    func testPreparePlaybackSendsTrackSelectionSourceAndReplacementPosition() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "replacement", "MediaSources": [[
                    "Id": "source-2", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-2/master.m3u8?ApiKey=test-token",
                    "DefaultAudioStreamIndex": 3,
                    "MediaStreams": [["Index": 3, "Type": "Audio", "DisplayTitle": "French - AAC", "Codec": "aac", "Language": "fra", "Channels": 2]],
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: item, quality: .upTo720p, audioStreamIndex: 3,
                                                       subtitleStreamIndex: -1, mediaSourceID: "source-2",
                                                       startPositionSeconds: 11.25)

        XCTAssertTrue(request.isTranscoding, "The server fallback remains authoritative for an incompatible selection.")
        XCTAssertEqual(request.startPositionSeconds, 11.25)
        XCTAssertEqual(request.mediaSourceID, "source-2")
        XCTAssertEqual(request.audioStreamIndex, 3)
        XCTAssertEqual(request.subtitleStreamIndex, -1)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }.count, 1,
                       "Explicit stream choices are already in the initial request; playback must not repeat that decision.")
        let playbackInfo = try XCTUnwrap(requests.all.first { $0.url?.path == "/Items/movie-1/PlaybackInfo" })
        let posted = try body(playbackInfo)
        XCTAssertEqual(posted["AudioStreamIndex"] as? Int, 3)
        XCTAssertEqual(posted["SubtitleStreamIndex"] as? Int, -1)
        XCTAssertEqual(posted["MediaSourceId"] as? String, "source-2")
        XCTAssertEqual(posted["StartTimeTicks"] as? Int64, 112_500_000)
        XCTAssertEqual(posted["MaxStreamingBitrate"] as? Int, 4_000_000)
        let profile = try XCTUnwrap(posted["DeviceProfile"] as? [String: Any])
        XCTAssertEqual(profile["MaxStreamingBitrate"] as? Int, 4_000_000)
        let videoRules = try XCTUnwrap(profile["CodecProfiles"] as? [[String: Any]])
        for codec in ["h264", "hevc"] {
            let rule = try XCTUnwrap(videoRules.first { $0["Codec"] as? String == codec })
            XCTAssertTrue((rule["Conditions"] as? [[String: String]])?.contains([
                "Property": "Height", "Condition": "LessThanEqual", "Value": "720",
            ]) == true)
        }
    }

    func testPlaybackRequestChoosesTheExplicitReturnedMediaSourceAndBoundsStartPosition() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "version", "MediaSources": [
                ["Id": "source-one", "SupportsDirectPlay": true, "SupportsTranscoding": false],
                ["Id": "source-two", "SupportsDirectPlay": true, "SupportsTranscoding": false, "Bitrate": 2_000_000],
            ],
        ]))
        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
            mediaSourceID: "source-two", startPositionSeconds: .greatestFiniteMagnitude
        )

        XCTAssertEqual(request.mediaSourceID, "source-two")
        XCTAssertEqual(request.bitRate, 2_000_000)
        XCTAssertEqual(request.startPositionSeconds, item.durationSeconds)
        XCTAssertThrowsError(try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
            mediaSourceID: "not-returned"
        )) { XCTAssertEqual($0 as? LuminaError, .malformedResponse) }
        XCTAssertThrowsError(try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
            startPositionSeconds: .infinity
        )) { XCTAssertEqual($0 as? LuminaError, .malformedResponse) }
    }

    func testPlaybackPrefersHighestDirectCompatibleSourceInsteadOfFirstReturnedSource() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "versions", "MediaSources": [
                ["Id": "source-1080", "Bitrate": 8_000_000, "SupportsDirectPlay": true, "SupportsTranscoding": false,
                 "MediaStreams": [["Index": 0, "Type": "Video", "Height": 1080]]],
                ["Id": "source-4k", "Bitrate": 24_000_000, "SupportsDirectPlay": true, "SupportsTranscoding": false,
                 "MediaStreams": [["Index": 0, "Type": "Video", "Height": 2160]]],
            ],
        ]))

        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
        )

        XCTAssertEqual(request.mediaSourceID, "source-4k")
        XCTAssertEqual(request.bitRate, 24_000_000)
        XCTAssertFalse(request.isTranscoding)
        XCTAssertEqual(request.resolvedQuality, .automatic)
    }

    func testPreparePlaybackAppliesDeviceLanguageChoicesToTheSelectedSource() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let defaultsName = "LuminaCoreTests.playback-preferences.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        defer { defaults.removePersistentDomain(forName: defaultsName) }
        let preferences = NativePlaybackPreferences(defaults: defaults)
        preferences.preferredAudioLanguages = ["en"]
        preferences.preferredSubtitleLanguages = ["es"]
        preferences.subtitleMode = .always
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved), playbackPreferences: preferences)
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                let posted = try body(request)
                let requestedSource = posted["MediaSourceId"] as? String
                var source: [String: Any] = [
                    "Id": "source-4k", "Bitrate": 24_000_000, "SupportsDirectPlay": true, "SupportsTranscoding": false,
                    "DefaultAudioStreamIndex": requestedSource == nil ? 1 : 2,
                    "MediaStreams": [
                        ["Index": 0, "Type": "Video", "Height": 2160],
                        ["Index": 1, "Type": "Audio", "Language": "fra", "IsDefault": true],
                        ["Index": 2, "Type": "Audio", "Language": "eng"],
                        ["Index": 3, "Type": "Subtitle", "Language": "spa"],
                    ],
                ]
                if requestedSource != nil { source["DefaultSubtitleStreamIndex"] = 3 }
                let hd: [String: Any] = [
                    "Id": "source-1080", "Bitrate": 8_000_000, "SupportsDirectPlay": true, "SupportsTranscoding": false,
                    "MediaStreams": [["Index": 0, "Type": "Video", "Height": 1080]],
                ]
                return response(request, json: ["PlaySessionId": "language-preferences", "MediaSources": requestedSource == nil ? [hd, source] : [source]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: item)

        XCTAssertEqual(request.mediaSourceID, "source-4k")
        XCTAssertEqual(request.audioStreamIndex, 2)
        XCTAssertEqual(request.subtitleStreamIndex, 3)
        XCTAssertEqual(request.resolvedQuality, .automatic)
        let playbackInfo = requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }
        XCTAssertEqual(playbackInfo.count, 2)
        XCTAssertNil(try body(playbackInfo[0])["MediaSourceId"])
        let selected = try body(playbackInfo[1])
        XCTAssertEqual(selected["MediaSourceId"] as? String, "source-4k")
        XCTAssertEqual(selected["AudioStreamIndex"] as? Int, 2)
        XCTAssertEqual(selected["SubtitleStreamIndex"] as? Int, 3)
    }

    func testPreparePlaybackUsesServerHLSForSelectedExternalSubtitleOnDirectMP4() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let defaultsName = "LuminaCoreTests.external-subtitle-hls.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        defer { defaults.removePersistentDomain(forName: defaultsName) }
        let preferences = NativePlaybackPreferences(defaults: defaults)
        preferences.preferredSubtitleLanguages = ["es"]
        preferences.subtitleMode = .always
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved), playbackPreferences: preferences)
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                let posted = try body(request)
                let selectedSource = posted["MediaSourceId"] as? String
                let streams: [[String: Any]] = [
                    ["Index": 0, "Type": "Video", "Height": 2160],
                    ["Index": 3, "Type": "Subtitle", "Codec": "vtt", "Language": "spa", "IsExternal": true,
                     "DeliveryMethod": "External", "DeliveryUrl": "/Videos/movie-1/Subtitles/3/0/Stream.vtt?ApiKey=test-token"],
                ]
                if selectedSource == nil {
                    return response(request, json: ["PlaySessionId": "direct-mp4", "MediaSources": [[
                        "Id": "source-mp4", "Container": "mp4", "Bitrate": 24_000_000,
                        "SupportsDirectPlay": true, "SupportsTranscoding": true, "MediaStreams": streams,
                    ]]])
                }
                return response(request, json: ["PlaySessionId": "server-hls", "MediaSources": [[
                    "Id": "source-mp4", "Container": "mp4", "Bitrate": 24_000_000,
                    "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-mp4/master.m3u8?ApiKey=test-token", "MediaStreams": streams,
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let playback = try await store.preparePlayback(for: item)

        XCTAssertTrue(playback.isTranscoding)
        XCTAssertEqual(playback.url.path, "/Videos/source-mp4/master.m3u8")
        XCTAssertEqual(playback.mediaSourceID, "source-mp4")
        XCTAssertEqual(playback.subtitleStreamIndex, 3)
        XCTAssertEqual(playback.resolvedQuality, .automatic)
        let playbackInfo = requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }
        XCTAssertEqual(playbackInfo.count, 2)
        XCTAssertEqual(try body(playbackInfo[0])["EnableDirectPlay"] as? Bool, true)
        let compatibleHLS = try body(playbackInfo[1])
        XCTAssertEqual(compatibleHLS["EnableDirectPlay"] as? Bool, false)
        XCTAssertEqual(compatibleHLS["MediaSourceId"] as? String, "source-mp4")
        XCTAssertEqual(compatibleHLS["SubtitleStreamIndex"] as? Int, 3)
        XCTAssertNil(compatibleHLS["MaxStreamingBitrate"], "Original quality remains uncapped when the server can remux it into HLS.")
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
            case "/Users/user-1/Items/movie-1":
                var detail = movie(positionTicks: 250_000_000)
                detail["RunTimeTicks"] = 300_000_000
                return response(request, json: detail)
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

    func testDirectMKVUsesClientDemuxWhileHLSDoesNot() throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let direct = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "mkv", "MediaSources": [["Id": "source", "Container": "mkv",
                "SupportsDirectPlay": true, "SupportsTranscoding": false]],
        ]))
        let request = try client.playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: direct)
        XCTAssertTrue(request.usesClientDemux)
        XCTAssertFalse(request.isTranscoding)
        XCTAssertEqual(request.url.path, "/Videos/movie-1/stream")
        let hls = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "fallback", "MediaSources": [["Id": "source", "Container": "mkv",
                "SupportsDirectPlay": false, "SupportsTranscoding": true, "TranscodingUrl": "/Videos/source/master.m3u8"]],
        ]))
        let fallback = try client.playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: hls)
        XCTAssertFalse(fallback.usesClientDemux)
        XCTAssertTrue(fallback.isTranscoding)
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

    func testRejectedNativeAudioCanRequestAnAuthenticatedCompatibleCopy() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(["Id": "track-1", "Name": "Synthetic song", "Type": "Audio", "Container": "mp3"]))
        let client = LuminaClient()
        let origin = URL(string: "http://127.0.0.1:8765")!
        let direct = try client.audioRequest(origin: origin, token: "test-token", item: item)
        XCTAssertFalse(URLComponents(url: direct.url, resolvingAgainstBaseURL: false)!.queryItems!.contains { $0.name == "format" })
        let retry = try client.audioRequest(origin: origin, token: "test-token", item: item, forceCompatible: true)
        XCTAssertEqual(retry.url.path, direct.url.path)
        let query = URLComponents(url: retry.url, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query?.first { $0.name == "format" }?.value, "flac")
        XCTAssertEqual(query?.first { $0.name == "ApiKey" }?.value, "test-token")
        XCTAssertEqual(retry.methodLabel, "Compatible audio")
    }

    func testKeychainNewItemsStayDeviceOnlyAndKeepDeviceIdentityAcrossAccounts() throws {
        let keychain = SyntheticKeychain()
        let credentials = KeychainCredentials(operations: keychain.operations)
        let deviceID = try credentials.deviceID()
        let saved = syntheticKeychainSession(deviceID: deviceID)
        try credentials.save(session: saved)
        for item in keychain.items.values {
            XCTAssertEqual(item[kSecAttrAccessible as String] as? String, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String, "New items must already be device-only before a later read can migrate them.")
        }
        XCTAssertEqual(try credentials.load()?.accessToken, saved.accessToken)
        let other = LuminaSession(serverURL: saved.serverURL, accessToken: "other-test-token", userID: "user-2", username: "other", serverName: saved.serverName, deviceID: deviceID)
        try credentials.save(session: other)
        XCTAssertEqual(try credentials.load()?.accessToken, other.accessToken)
        XCTAssertEqual(try credentials.load()?.userID, other.userID)
        XCTAssertEqual(try credentials.deviceID(), deviceID)
        XCTAssertEqual(keychain.items.count, 2)
        for item in keychain.items.values {
            XCTAssertEqual(item[kSecAttrAccessible as String] as? String, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String)
        }
        XCTAssertEqual(keychain.deletions, 0, "Credential replacement must update in place.")
    }

    func testKeychainLegacyReadsMigrateBothItemsWithoutChangingTheirValues() throws {
        let saved = syntheticKeychainSession()
        let keychain = try SyntheticKeychain(legacySession: saved)
        let before = keychain.items.mapValues { $0[kSecValueData as String] as? Data }
        let credentials = KeychainCredentials(operations: keychain.operations)
        XCTAssertEqual(try credentials.load()?.accessToken, saved.accessToken)
        XCTAssertEqual(try credentials.deviceID(), saved.deviceID)
        XCTAssertEqual(keychain.items.mapValues { $0[kSecValueData as String] as? Data }, before)
        XCTAssertEqual(keychain.updates, 2)
        XCTAssertEqual(try credentials.load()?.accessToken, saved.accessToken)
        XCTAssertEqual(try credentials.deviceID(), saved.deviceID)
        XCTAssertEqual(keychain.updates, 2, "Already protected reads need no migration write.")
        for item in keychain.items.values {
            XCTAssertEqual(item[kSecAttrAccessible as String] as? String, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String)
        }
        XCTAssertEqual(keychain.deletions, 0)
    }

    func testKeychainMigrationAndReadFailuresRetainCredentialsWithoutMintingAnIdentity() async throws {
        let saved = syntheticKeychainSession()
        let keychain = try SyntheticKeychain(legacySession: saved)
        let before = keychain.items.mapValues { $0[kSecValueData as String] as? Data }
        let credentials = KeychainCredentials(operations: keychain.operations)
        keychain.updateFailure = errSecInteractionNotAllowed
        XCTAssertThrowsError(try credentials.load()) { XCTAssertEqual($0 as? LuminaError, .keychain(errSecInteractionNotAllowed)) }
        XCTAssertThrowsError(try credentials.deviceID())
        XCTAssertThrowsError(try credentials.save(session: syntheticKeychainSession(deviceID: "replacement-device")))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in requests.append(request); return response(request) }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        await store.restore()
        XCTAssertNil(store.session, "A failed migration must not return an unprotected session.")
        XCTAssertTrue(requests.all.isEmpty)
        keychain.updateFailure = nil
        keychain.readFailure = errSecInteractionNotAllowed
        XCTAssertThrowsError(try credentials.deviceID())
        XCTAssertEqual(keychain.items.mapValues { $0[kSecValueData as String] as? Data }, before)
        XCTAssertEqual(keychain.additions, 0)
        XCTAssertEqual(keychain.deletions, 0)
        keychain.readFailure = nil
        try credentials.save(session: saved)
        XCTAssertEqual(keychain.items["session"]?[kSecAttrAccessible as String] as? String, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String, "Saving a legacy session upgrades it in place.")
        XCTAssertEqual(try credentials.deviceID(), saved.deviceID, "Retry must recover the same identity.")
        XCTAssertEqual(try credentials.load()?.accessToken, saved.accessToken)
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
        let keychain = try SyntheticKeychain(legacySession: saved)
        let credentials = KeychainCredentials(operations: keychain.operations)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }

        await store.restore()

        XCTAssertEqual(store.session?.accessToken, "saved-token")
        XCTAssertEqual(try credentials.load()?.accessToken, "saved-token")
        XCTAssertEqual(try credentials.deviceID(), saved.deviceID)
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
        let keychain = try SyntheticKeychain(legacySession: syntheticKeychainSession())
        let credentials = KeychainCredentials(operations: keychain.operations)
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
        XCTAssertNil(try credentials.load())
        XCTAssertEqual(try credentials.deviceID(), "test-device")
        XCTAssertNil(store.errorMessage)
        releaseLogout.signal()
        await signOut.value
        XCTAssertNil(store.errorMessage, "A failed revocation must not replace the signed-out connection form with an old session error.")

        await store.connect(server: "http://127.0.0.1:8765", username: "member", password: "test-password")

        XCTAssertEqual(store.session?.accessToken, "new-token")
        XCTAssertEqual(store.session?.deviceID, "test-device")
        XCTAssertEqual(try credentials.load()?.deviceID, "test-device")
        XCTAssertNil(store.errorMessage)
        // The optional library index started during restore may race the later
        // cancellation, but it must not displace the actual revocation before
        // any subsequent connection work.
        let nonIndexPaths = requests.all.compactMap(\.url?.path).filter { $0 != "/Lumina/Library/alphabet" }
        XCTAssertEqual(nonIndexPaths.first, "/Sessions/Logout")
    }

    func testOfflineRestoreKeepsRetainedSessionWithoutSendingRequests() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "saved-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            throw URLError(.notConnectedToInternet)
        }
        let connectivity = LuminaConnectivity(status: .offline, monitoringEnabled: false)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()),
                                credentials: TestCredentials(session: saved), connectivity: connectivity)

        await store.restore()
        await store.loadLibrary(kind: .movies)
        await store.loadHome()

        XCTAssertTrue(store.isOffline)
        XCTAssertEqual(store.session?.accessToken, "saved-token")
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertTrue(requests.all.isEmpty, "An offline cold launch must only use durable local state.")
    }

    func testServerRetryRecoversWhenRouteRemainsAvailable() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "saved-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let requests = RequestRecorder()
        let meAttempts = Counter()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me":
                if meAttempts.increment() == 1 { throw URLError(.cannotConnectToHost) }
                return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            default: return response(request, status: 404)
            }
        }
        let connectivity = LuminaConnectivity(status: .online, monitoringEnabled: false)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()),
                                credentials: TestCredentials(session: saved), connectivity: connectivity)

        await store.restore()
        XCTAssertTrue(store.isOffline)

        await store.refreshConnectivity()

        XCTAssertFalse(store.isOffline)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Users/Me" }.count, 2)
    }

    func testDownloadedMKVUsesClientDemuxAndHonorsExplicitReplayPosition() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-store-offline-\(UUID())", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "saved-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        var payload = movie(positionTicks: 420_000_000)
        payload["Container"] = "mkv"
        payload["ImageTags"] = ["Primary": "poster-tag"]
        let item = try JSONDecoder().decode(MediaItem.self, from: json(payload))
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Items/movie-1/Download":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "video/x-matroska"])!, Data(repeating: 0, count: 64))
            case "/Items/movie-1/Images/Primary":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "image/jpeg"])!, Data(repeating: 0, count: 32))
            default: return response(request, status: 404)
            }
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()),
                                credentials: TestCredentials(session: saved),
                                connectivity: LuminaConnectivity(status: .online, monitoringEnabled: false),
                                offline: LuminaOfflineLibrary(root: root, configuration: mockConfiguration()))
        await store.restore()
        await store.downloadVideo(item: item)
        store.offline.recordPlaybackPosition(id: item.id, seconds: 42)

        let playback = try await store.preparePlayback(for: item, startPositionSeconds: 0)

        XCTAssertEqual(playback.url, try XCTUnwrap(store.offline.localURL(for: item.id)))
        XCTAssertTrue(playback.usesClientDemux)
        XCTAssertEqual(playback.startPositionSeconds, 0)
        XCTAssertNotNil(store.offline.artworkURL(for: item.id))
    }
}

private func syntheticKeychainSession(deviceID: String = "test-device") -> LuminaSession {
    LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: deviceID)
}

/// A synthetic Security API boundary: no process, simulator or signed app Keychain is touched.
private final class SyntheticKeychain {
    var items = [String: [String: Any]]()
    var readFailure: OSStatus?
    var updateFailure: OSStatus?
    var updates = 0
    var additions = 0
    var deletions = 0

    init() {}
    convenience init(legacySession: LuminaSession) throws {
        self.init()
        for (key, data) in [("session", try JSONEncoder().encode(legacySession)), ("device-id", Data(legacySession.deviceID.utf8))] {
            items[key] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock]
        }
    }

    var operations: KeychainCredentials.Operations {
        var operations = KeychainCredentials.Operations()
        operations.copyMatching = { [self] query, result in
            if let readFailure { return readFailure }
            let query = query as! [String: Any]
            guard let item = items[query[kSecAttrAccount as String] as! String] else { return errSecItemNotFound }
            XCTAssertEqual(query[kSecReturnData as String] as? Bool, true)
            XCTAssertEqual(query[kSecReturnAttributes as String] as? Bool, true)
            result?.pointee = item as CFDictionary
            return errSecSuccess
        }
        operations.update = { [self] query, attributes in
            updates += 1
            if let updateFailure { return updateFailure }
            let key = (query as! [String: Any])[kSecAttrAccount as String] as! String
            guard let old = items[key] else { return errSecItemNotFound }
            items[key] = old.merging(attributes as! [String: Any]) { _, new in new }
            return errSecSuccess
        }
        operations.add = { [self] query, _ in
            additions += 1
            let item = query as! [String: Any]
            let key = item[kSecAttrAccount as String] as! String
            guard items[key] == nil else { return errSecDuplicateItem }
            items[key] = item
            return errSecSuccess
        }
        operations.delete = { [self] query in
            deletions += 1
            let key = (query as! [String: Any])[kSecAttrAccount as String] as! String
            return items.removeValue(forKey: key) == nil ? errSecItemNotFound : errSecSuccess
        }
        return operations
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

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func increment() -> Int { lock.withLock { value += 1; return value } }
}

private final class HomeRequestTimeline: @unchecked Sendable {
    struct Measurement {
        let elapsedMilliseconds: Int
        let firstCompletionMilliseconds: Int
        let maxConcurrent: Int
    }

    private let lock = NSLock()
    private var startedAt: Date?
    private var firstCompletedAt: Date?
    private var activeRequests = 0
    private var peakRequests = 0

    var isMeasuring: Bool { lock.withLock { startedAt != nil } }

    func start() {
        lock.withLock {
            startedAt = Date()
            firstCompletedAt = nil
            activeRequests = 0
            peakRequests = 0
        }
    }

    func begin(_ path: String) {
        lock.withLock {
            guard Self.homePaths.contains(path) else { return }
            guard startedAt != nil else { return }
            activeRequests += 1
            peakRequests = max(peakRequests, activeRequests)
        }
    }

    func end(_ path: String) {
        lock.withLock {
            guard Self.homePaths.contains(path) else { return }
            guard activeRequests > 0 else { return }
            activeRequests -= 1
            if firstCompletedAt == nil { firstCompletedAt = Date() }
        }
    }

    private static let homePaths: Set<String> = ["/Items", "/UserItems/Resume", "/Shows/NextUp"]

    var measurement: Measurement {
        lock.withLock {
            Measurement(
                elapsedMilliseconds: Int(Date().timeIntervalSince(startedAt ?? Date()) * 1_000),
                firstCompletionMilliseconds: Int((firstCompletedAt ?? Date()).timeIntervalSince(startedAt ?? Date()) * 1_000),
                maxConcurrent: peakRequests
            )
        }
    }
}

private final class PathCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var counts = [String: Int]()

    func next(_ path: String) -> Int {
        lock.withLock {
            counts[path, default: 0] += 1
            return counts[path]!
        }
    }
}

private final class RequestStartRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    private var waiter: CheckedContinuation<Void, Never>?

    func record(_ request: URLRequest) {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            count += 1
            guard let waiter, count >= 3 else { return nil }
            self.waiter = nil
            return waiter
        }
        waiter?.resume()
    }

    func waitForThree() async {
        await withCheckedContinuation { continuation in
            let resume = lock.withLock { () -> Bool in
                guard count < 3 else { return true }
                waiter = continuation
                return false
            }
            if resume { continuation.resume() }
        }
    }
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

private func homeConfiguration() -> URLSessionConfiguration {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [HomeDelayedURLProtocol.self]
    return configuration
}

private final class HomeDelayedURLProtocol: URLProtocol {
    private struct Pending {
        let `protocol`: HomeDelayedURLProtocol
        let timeline: HomeRequestTimeline?
        let path: String
    }

    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var delay: @Sendable (URLRequest) -> TimeInterval = { _ in 0 }
    nonisolated(unsafe) static var onStart: (@Sendable (URLRequest) -> Void)?
    nonisolated(unsafe) static weak var timeline: HomeRequestTimeline?
    private static let lock = NSLock()
    nonisolated(unsafe) private static var pending = [UUID: Pending]()
    nonisolated(unsafe) private static var cancellationCount = 0

    private var identifier: UUID?

    static var cancelledRequests: Int { lock.withLock { cancellationCount } }

    static func reset() {
        let pending = lock.withLock { () -> [Pending] in
            let pending = Array(Self.pending.values)
            Self.pending.removeAll()
            Self.handler = nil
            Self.delay = { _ in 0 }
            Self.onStart = nil
            Self.timeline = nil
            Self.cancellationCount = 0
            return pending
        }
        pending.forEach { $0.timeline?.end($0.path) }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let request = request
        let timeline = Self.timeline
        let path = request.url?.path ?? ""
        let identifier = UUID()
        self.identifier = identifier
        let handler = Self.handler
        let delay = Self.delay(request)
        timeline?.begin(path)
        Self.lock.withLock {
            Self.pending[identifier] = Pending(protocol: self, timeline: timeline, path: path)
        }
        Self.onStart?(request)
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) {
            Self.complete(identifier: identifier, request: request, handler: handler)
        }
    }

    override func stopLoading() {
        guard let identifier else { return }
        self.identifier = nil
        let pending = Self.lock.withLock { () -> Pending? in
            guard let pending = Self.pending.removeValue(forKey: identifier) else { return nil }
            Self.cancellationCount += 1
            return pending
        }
        if let pending { pending.timeline?.end(pending.path) }
    }

    private static func complete(identifier: UUID, request: URLRequest, handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?) {
        guard let pending = lock.withLock({ Self.pending.removeValue(forKey: identifier) }) else { return }
        defer { pending.timeline?.end(pending.path) }
        do {
            guard let handler else { throw URLError(.badServerResponse) }
            let (response, data) = try handler(request)
            pending.protocol.client?.urlProtocol(pending.protocol, didReceive: response, cacheStoragePolicy: .notAllowed)
            pending.protocol.client?.urlProtocol(pending.protocol, didLoad: data)
            pending.protocol.client?.urlProtocolDidFinishLoading(pending.protocol)
        } catch {
            pending.protocol.client?.urlProtocol(pending.protocol, didFailWithError: error)
        }
    }
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
