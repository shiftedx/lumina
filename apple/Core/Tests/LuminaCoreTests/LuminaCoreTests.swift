import AVFoundation
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

    func testRepeatedDetailBrowsingReusesMetadata() async throws {
        let credentials = TestCredentials()
        credentials.stored = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!,
            accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                Thread.sleep(forTimeInterval: 0.05)
                return response(request, json: movie())
            default: return response(request, status: 404)
            }
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = MediaMetadataCache(directory: directory)
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials, metadataCache: cache)
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let started = ContinuousClock.now
        _ = try await store.loadItem(item, useCache: true)
        let warmStarted = ContinuousClock.now
        for _ in 0..<2 { _ = try await store.loadItem(item, useCache: true) }
        print("DETAIL_WARM_RETURNS elapsed=\(warmStarted.duration(to: .now)) reads=2 network_reads=0")
        print("DETAIL_RETURN_BASELINE elapsed=\(started.duration(to: .now)) requests=\(calls.all.filter { $0.url?.path == "/Users/user-1/Items/movie-1" }.count)")
        XCTAssertEqual(calls.all.filter { $0.url?.path == "/Users/user-1/Items/movie-1" }.count, 1,
                       "Repeated detail visits must reuse fresh device metadata.")
        // A recreated store/process reads the same bounded device snapshot, never credentials.
        let restoredCache = MediaMetadataCache(directory: directory)
        let restored = LuminaStore(client: LuminaClient(configuration: mockConfiguration()),
                                   credentials: credentials, metadataCache: restoredCache)
        await restored.restore()
        _ = try await restored.loadItem(item, useCache: true)
        XCTAssertEqual(calls.all.filter { $0.url?.path == "/Users/user-1/Items/movie-1" }.count, 1)
        let active = try XCTUnwrap(restored.session)
        let key = MediaMetadataCache.key("item/movie-1/0", session: active)
        let otherMember = LuminaSession(serverURL: active.serverURL, accessToken: active.accessToken,
            userID: "other-member", username: "Other", serverName: "Lumina", deviceID: active.deviceID)
        let otherCredential = LuminaSession(serverURL: active.serverURL, accessToken: "rotated-token",
            userID: active.userID, username: active.username, serverName: "Lumina", deviceID: active.deviceID)
        let otherServer = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8766")!, accessToken: active.accessToken,
            userID: active.userID, username: active.username, serverName: "Lumina", deviceID: active.deviceID)
        for owner in [otherMember, otherCredential, otherServer] {
            let leaked = await restoredCache.items(for: MediaMetadataCache.key("item/movie-1/0", session: owner))
            XCTAssertNil(leaked, "Metadata must never cross a member, credential or server boundary.")
        }
        let expiredCache = MediaMetadataCache(directory: directory, now: { Date.distantFuture })
        let expired = await expiredCache.items(for: key)
        XCTAssertNil(expired, "Expired detail snapshots must require a fresh server read.")
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        XCTAssertFalse(files.isEmpty)
        for file in files {
            XCTAssertFalse(file.lastPathComponent.contains("test-token"))
            XCTAssertFalse(String(decoding: try Data(contentsOf: file), as: UTF8.self).contains("test-token"))
        }
        _ = try await restored.loadItem(item)
        XCTAssertEqual(calls.all.filter { $0.url?.path == "/Users/user-1/Items/movie-1" }.count, 2,
                       "An explicit fresh/default read must bypass the device cache.")
        MockURLProtocol.handler = { request in response(request, status: 401) }
        do { _ = try await restored.loadItem(item); XCTFail("Fresh authorization failures must remain actionable") }
        catch { XCTAssertNil(restored.session) }
        let cleared = await restoredCache.items(for: key)
        XCTAssertNil(cleared, "Sign-out/revocation must synchronously invalidate resident and pending disk snapshots.")

    }

    func testDeviceSnapshotsRestoreHomeAndSeasonMetadataWithoutCrossingRefreshBoundaries() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let credentials = TestCredentials()
        credentials.stored = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token",
            userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let calls = RequestRecorder()
        MockURLProtocol.handler = { request in
            calls.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return response(request, json: page(items: [movie(positionTicks: 80_000_000)]))
            case "/Shows/NextUp": return response(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            case "/Shows/show/Seasons": return response(request, json: page(items: [["Id": "season", "Name": "Season 1", "Type": "Season", "IndexNumber": 1]]))
            default: return response(request, status: 404)
            }
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials,
                                metadataCache: MediaMetadataCache(directory: directory))
        await store.restore()
        let show = try JSONDecoder().decode(MediaItem.self, from: json(["Id": "show", "Name": "Show", "Type": "Series"]))
        for _ in 0..<3 {
            let seasons = try await store.loadChildren(of: show, useCache: true)
            XCTAssertEqual(seasons.map(\.id), ["season"])
        }
        XCTAssertEqual(calls.all.filter { $0.url?.path == "/Shows/show/Seasons" }.count, 1)
        MockURLProtocol.handler = { request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "user-1", "Name": "member"]) }
            return response(request, status: 503)
        }
        let restored = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials,
                                   metadataCache: MediaMetadataCache(directory: directory))
        await restored.restore()
        XCTAssertNotNil(restored.session)
        XCTAssertEqual(restored.recentItems.map(\.id), ["movie-1"])
        XCTAssertEqual(restored.resumeItems.map(\.id), ["movie-1"])
        XCTAssertEqual(restored.nextUpItems.map(\.id), ["next"])
        let cachedSeasons = try await restored.loadChildren(of: show, useCache: true)
        XCTAssertEqual(cachedSeasons.map(\.id), ["season"])
        await restored.loadHome()
        do {
            _ = try await restored.loadChildren(of: show, useCache: true)
            XCTFail("An explicit Home/activity refresh must invalidate old episode/member-state snapshots.")
        } catch { XCTAssertNotNil(restored.session) }
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
        XCTAssertEqual(store.thumbnailURL(for: posterEpisode, poster: true)?.path, "/Items/series-1/Images/Primary",
                       "Portrait Continue Watching/Next Up tiles must never stretch the episode still into a series poster.")

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
        var matchedEpisode = savedEpisode
        matchedEpisode["ParentPrimaryImageItemId"] = "catalog-series"
        let catalogEpisode = try JSONDecoder().decode(MediaItem.self, from: json(matchedEpisode))
        XCTAssertEqual(store.thumbnailURL(for: catalogEpisode, poster: true)?.path, "/Items/catalog-series/Images/Primary")
        XCTAssertEqual(store.logoURL(for: queuedEpisode)?.path, "/Items/series-1/Images/Logo")
        XCTAssertEqual(store.backdropURL(for: queuedEpisode)?.path, "/Items/series-1/Images/Backdrop/0")
        XCTAssertEqual(store.thumbnailURL(for: queuedEpisode)?.path, "/Items/movie-1/Images/Primary",
                       "Continue Watching and Next Up must use an episode's still, while the hero retains series artwork.")

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
        await store.loadLibrary()
        XCTAssertEqual(store.items.map(\.id), ["movie-1", "movie-2"],
                       "Returning to Library must retain all loaded pages instead of replacing the wall with page one.")
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items" }.count, 3,
                       "A warm Library return must not wait for another catalog request.")
        await store.loadLibrary(kind: .movies)
        await store.loadLibrary()
        XCTAssertEqual(store.items.map(\.id), ["movie-1", "movie-2"],
                       "Switching categories and back must restore the complete wall and pagination cursor.")
        XCTAssertFalse(store.hasMore)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items" }.count, 4)
        await store.loadLibrary(forceRefresh: true)
        XCTAssertEqual(store.items.map(\.id), ["movie-1"], "Explicit refresh must still query the server.")
        XCTAssertTrue(store.hasMore)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items" }.count, 5)
    }

    func testFullLibraryCatalogLoadsAheadAndAlphabetJumpRetainsTheEntireWall() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return response(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return response(request, json: ["anchors": [["letter": "A", "index": 0], ["letter": "W", "index": 1000]], "total": 1200])
            case "/Items":
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
                let value = { (name: String) in query.first { $0.name == name }?.value }
                guard value("Fields") == "LuminaCatalog" else { return response(request, json: page(items: [])) }
                let start = Int(value("StartIndex")!)!, limit = Int(value("Limit")!)!
                let rows = (start..<min(1200, start + limit)).map { index in
                    ["Id": "title-\(index)", "Name": "Title \(index)", "Type": "Movie", "ImageTags": ["Primary": "poster"]] as [String: Any]
                }
                return response(request, json: ["Items": rows, "TotalRecordCount": 1200, "StartIndex": start])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(kind: .movies, filters: VideoFilters(favoritesOnly: true), preloadCatalog: true)
        XCTAssertEqual(store.items.count, 50, "The first viewport must publish without waiting for the complete catalog")
        await store.loadMore() // Joins the store-owned catalog task, without a scroll/end-of-page trigger.
        XCTAssertEqual(store.items.map(\.id), (0..<1200).map { "title-\($0)" })
        XCTAssertFalse(store.hasMore)
        XCTAssertFalse(store.isCatalogLoading)
        XCTAssertNil(store.errorMessage)
        let catalog = requests.all.filter { $0.url?.query?.contains("LuminaCatalog") == true }
        XCTAssertEqual(catalog.count, 4)
        let queries = catalog.map { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)!.queryItems! }
        XCTAssertEqual(queries.map { $0.first { $0.name == "StartIndex" }?.value }, ["0", "50", "550", "1050"])
        XCTAssertEqual(queries.map { $0.first { $0.name == "Limit" }?.value }, ["50", "500", "500", "500"])
        XCTAssertTrue(queries.allSatisfy { $0.contains { $0.name == "IsFavorite" && $0.value == "true" } })
        for _ in 0..<100 where store.libraryAlphabet.isEmpty { try await Task.sleep(for: .milliseconds(5)) }
        let w = try XCTUnwrap(store.libraryAlphabet.first { $0.letter == "W" })
        let jump = await store.jumpLibrary(to: w)
        XCTAssertEqual(jump, "title-1000")
        XCTAssertEqual(store.items.count, 1200, "Alphabet seeking must preserve scrollable A-Z content")
        await store.loadLibrary(kind: .movies, filters: VideoFilters(favoritesOnly: true), preloadCatalog: true)
        XCTAssertEqual(requests.all.filter { $0.url?.query?.contains("LuminaCatalog") == true }.count, 4, "Warm returns and alphabet navigation reuse the complete catalog")
    }

    func testFullLibraryCatalogSuspendsResumesAndRejectsObsoleteCategoryPages() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "user-1", "Name": "member"]) }
            if request.url?.path == "/Lumina/Library/alphabet" {
                return response(request, json: ["anchors": [["letter": "A", "index": 0]], "total": 3])
            }
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard request.url?.path == "/Items", query.contains(where: { $0.value == "LuminaCatalog" }) else {
                return response(request, json: page(items: []))
            }
            let start = Int(query.first { $0.name == "StartIndex" }!.value!)!
            let anime = query.contains { $0.name == "LuminaCategory" && $0.value == "anime" }
            return response(request, json: ["Items": [["Id": anime ? "anime" : "movie-\(start)", "Name": "Title", "Type": "Movie"]], "TotalRecordCount": anime ? 1 : 3, "StartIndex": start])
        }
        HomeDelayedURLProtocol.delay = { request in
            request.url?.query?.contains("StartIndex=1") == true ? 0.2 : 0
        }
        await store.restore()
        let firstPage = expectation(description: "Background catalog page started")
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.query?.contains("StartIndex=1") == true { firstPage.fulfill() }
        }
        await store.loadLibrary(kind: .movies, preloadCatalog: true)
        await fulfillment(of: [firstPage], timeout: 2)
        for _ in 0..<100 where store.libraryAlphabet.isEmpty { try await Task.sleep(for: .milliseconds(1)) }
        let a = try XCTUnwrap(store.libraryAlphabet.first)
        let jump = await store.jumpLibrary(to: a)
        XCTAssertEqual(jump, "movie-0")
        XCTAssertEqual(store.items.count, 1, "Seeking an already loaded letter must not wait for the background catalog")
        store.setCatalogBackgrounded(true)
        for _ in 0..<100 where store.isBusy { try await Task.sleep(for: .milliseconds(5)) }
        XCTAssertFalse(store.isBusy)
        XCTAssertFalse(store.isCatalogLoading)
        XCTAssertEqual(store.items.map(\.id), ["movie-0"])
        XCTAssertTrue(store.hasMore)
        XCTAssertNil(store.errorMessage, "Suspension is cancellation, not a catalog error")
        let resumed = expectation(description: "The suspended catalog resumes from its cursor")
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.query?.contains("StartIndex=1") == true { resumed.fulfill() }
        }
        store.setCatalogBackgrounded(false)
        await fulfillment(of: [resumed], timeout: 2)
        await store.loadLibrary(kind: .anime, preloadCatalog: true)
        try await Task.sleep(for: .milliseconds(250))
        XCTAssertEqual(store.items.map(\.id), ["anime"], "An obsolete Movies response must never append into Anime")
        XCTAssertFalse(store.hasMore)
        XCTAssertFalse(store.isCatalogLoading)
        XCTAssertFalse(store.isBusy)
        XCTAssertGreaterThanOrEqual(HomeDelayedURLProtocol.cancelledRequests, 2)
        HomeDelayedURLProtocol.onStart = nil
        await store.loadLibrary(kind: .movies, preloadCatalog: true)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["movie-0", "movie-1", "movie-2"], "A partial category snapshot resumes at its retained cursor")
    }

    func testFullLibraryCatalogFailureRetainsPostersAndExplicitRetryCompletes() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let attempts = PathCounter()
        MockURLProtocol.handler = { @Sendable request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "user-1", "Name": "member"]) }
            guard request.url?.path == "/Items", request.url?.query?.contains("LuminaCatalog") == true else {
                return response(request, json: page(items: []))
            }
            let next = request.url?.query?.contains("StartIndex=1") == true
            if next, attempts.next("next") == 1 { return response(request, status: 503) }
            return response(request, json: ["Items": [["Id": next ? "last" : "first", "Name": "Title", "Type": "Movie"]], "TotalRecordCount": 2, "StartIndex": next ? 1 : 0])
        }
        await store.restore()
        await store.loadLibrary(kind: .movies, preloadCatalog: true)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["first"])
        XCTAssertTrue(store.hasMore)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(store.isBusy)
        XCTAssertFalse(store.isCatalogLoading)
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["first", "last"])
        XCTAssertFalse(store.hasMore)
        XCTAssertNil(store.errorMessage)
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

    func testCurrentLibraryLoadSurvivesAppearanceTaskCancellation() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token",
            userID: "one", username: "One", serverName: "Lumina", deviceID: "fixture")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            if request.url?.path == "/Users/Me" { return response(request, json: ["Id": "one", "Name": "One"]) }
            return response(request, json: page(items: []))
        }
        await store.restore()
        let started = expectation(description: "The current Library query reached transport")
        started.assertForOverFulfill = true
        HomeDelayedURLProtocol.handler = { request in
            response(request, json: page(items: [["Id": "current-library", "Name": "Current", "Type": "Movie"]]))
        }
        HomeDelayedURLProtocol.delay = { request in request.url?.path == "/Items" ? 0.15 : 0 }
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.path == "/Items" { started.fulfill() }
        }
        let appearance = Task { await store.loadLibrary(search: "current") }
        await fulfillment(of: [started], timeout: 2)
        let reappearance = Task { await store.loadLibrary(search: "current") }
        await Task.yield()
        appearance.cancel()
        await appearance.value
        await reappearance.value
        XCTAssertEqual(store.items.map(\.id), ["current-library"],
                       "A navigation transition may cancel a view's appearance task, but its still-current Library query must finish.")
        XCTAssertFalse(store.isBusy)
        XCTAssertNil(store.errorMessage)
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
            if request.url?.path == "/Items" {
                return response(request, json: page(items: [["Id": "retained-one", "Name": "Retained", "Type": "Movie"]]))
            }
            return response(request, json: page(items: []))
        }
        await store.restore()
        XCTAssertEqual(store.items.map(\.id), ["retained-one"])
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
        XCTAssertTrue(store.items.isEmpty, "A prior member's retained pages and in-flight page must not appear under the new member")
    }

    func testNewHomeLoadCancelsTheObsoleteShelfRequests() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        func awaitTransportCancellations(_ expected: Int) async {
            let deadline = Date().addingTimeInterval(2)
            while HomeDelayedURLProtocol.cancelledRequests < expected, Date() < deadline {
                try? await Task.sleep(nanoseconds: 1_000_000)
            }
        }
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
        HomeDelayedURLProtocol.onStart = { request in
            if ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) {
                obsoleteStarts.record(request)
            }
        }
        let obsolete = Task { await store.loadHome() }
        await obsoleteStarts.waitForThree()
        HomeDelayedURLProtocol.delay = { _ in 0.01 }
        let current = Task { await store.loadHome() }
        await current.value
        await obsolete.value

        await awaitTransportCancellations(3)
        XCTAssertGreaterThanOrEqual(HomeDelayedURLProtocol.cancelledRequests, 3)
        XCTAssertEqual(store.recentItems.map(\.id), ["current-recent"])
        XCTAssertEqual(store.resumeItems.map(\.id), ["current-resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["current-next"])

        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.3 : 0
        }
        let cancelledStarts = RequestStartRecorder()
        HomeDelayedURLProtocol.onStart = { request in
            if ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) {
                cancelledStarts.record(request)
            }
        }
        let cancelled = Task { await store.loadHome() }
        await cancelledStarts.waitForThree()
        cancelled.cancel()
        await cancelled.value
        await awaitTransportCancellations(6)
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

    func testSeriesCollectionResolvesOnlyTheAuthenticatedParentSeriesWithoutReplacingBrowsePage() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/series-1": return response(request, json: ["Id": "series-1", "Name": "Series", "Type": "Series"])
            case "/Users/user-1/Items/wrong-type": return response(request, json: ["Id": "wrong-type", "Name": "Movie", "Type": "Movie"])
            case "/Users/user-1/Items/wrong-id": return response(request, json: ["Id": "another-id", "Name": "Series", "Type": "Series"])
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let series = try await store.seriesCollection(id: "series-1")
        XCTAssertEqual([series.id, series.type], ["series-1", "Series"])
        XCTAssertEqual(store.items.map(\.id), ["movie-1"], "A parent-series pivot must not replace the video browse page.")

        for id in ["", "wrong-type", "wrong-id"] {
            do {
                _ = try await store.seriesCollection(id: id)
                XCTFail("Parent-series pivots must reject missing, mismatched, and non-series responses.")
            } catch {
                XCTAssertEqual(error as? LuminaError, .malformedResponse)
            }
        }

        let pivots = requests.all.filter { $0.url?.path.hasPrefix("/Users/user-1/Items/") == true }
        XCTAssertEqual(pivots.map { $0.url?.path }, [
            "/Users/user-1/Items/series-1",
            "/Users/user-1/Items/wrong-type",
            "/Users/user-1/Items/wrong-id",
        ])
        XCTAssertTrue(pivots.allSatisfy {
            $0.httpMethod == "GET" && $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true
        })
    }

    func testLibraryItemResolvesAnExactAuthenticatedVaultTitleWithoutReplacingBrowsePage() async throws {
        let vaultMovieID = "B313B443-44D5-44A1-95E2-052A2D34C25B"
        let vaultMovieResponseID = "b313b44344d544a195e2052a2d34c25b"
        let mismatchedVaultID = "F982FDE7-27C6-441B-A915-046B0D29FFBB"
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return response(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/\(vaultMovieID)": return response(request, json: ["Id": vaultMovieResponseID, "Name": "Vault movie", "Type": "Movie"])
            case "/Users/user-1/Items/vault-series": return response(request, json: ["Id": "vault-series", "Name": "Vault series", "Type": "Series"])
            case "/Users/user-1/Items/\(mismatchedVaultID)": return response(request, json: ["Id": "1A5D6533-B044-45A3-BE3B-71FC48705696", "Name": "Wrong", "Type": "Movie"])
            default: return response(request, status: 404)
            }
        }

        await store.restore()
        let movie = try await store.libraryItem(id: vaultMovieID)
        let series = try await store.libraryItem(id: "vault-series")
        XCTAssertEqual([movie.id, movie.type], [vaultMovieResponseID, "Movie"])
        XCTAssertEqual([series.id, series.type], ["vault-series", "Series"])
        XCTAssertEqual(store.items.map(\.id), ["movie-1"], "A Requests pivot must not replace the active video browse page.")

        for id in ["", mismatchedVaultID] {
            do {
                _ = try await store.libraryItem(id: id)
                XCTFail("Requests pivots must reject blank and mismatched vault IDs.")
            } catch {
                XCTAssertEqual(error as? LuminaError, .malformedResponse)
            }
        }

        let pivots = requests.all.filter { $0.url?.path.hasPrefix("/Users/user-1/Items/") == true }
        XCTAssertEqual(pivots.map { $0.url?.path }, [
            "/Users/user-1/Items/\(vaultMovieID)",
            "/Users/user-1/Items/vault-series",
            "/Users/user-1/Items/\(mismatchedVaultID)",
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
        payload["CriticRating"] = 88
        payload["LuminaIMDbRating"] = 8.2
        payload["LuminaRottenTomatoesRating"] = 91.5
        payload["LuminaMetacriticRating"] = 73
        payload["Genres"] = ["Animation", "Adventure"]
        payload["People"] = [["Name": "Example Actor", "Type": "Actor"]]

        let item = try JSONDecoder().decode(MediaItem.self, from: json(payload))

        XCTAssertEqual(item.officialRating, "PG-13")
        XCTAssertEqual(item.communityRating, 8.4)
        XCTAssertEqual(item.criticRatingPercentage, 88)
        XCTAssertEqual(item.validIMDbRating, 8.2)
        XCTAssertEqual(item.validRottenTomatoesRating, 91.5)
        XCTAssertEqual(item.validMetacriticRating, 73)
        XCTAssertEqual(item.genres, ["Animation", "Adventure"])
        XCTAssertEqual(item.people?.first?.name, "Example Actor")
        XCTAssertEqual(item.people?.first?.type, "Actor")
    }

    func testHeroCriticRatingOnlyDisplaysValidServerPercentages() throws {
        for rating in [nil, -1.0, 0, 88, 100, 101] as [Double?] {
            var payload = movie()
            payload["LuminaIMDbRating"] = rating.map { $0 as Any } ?? NSNull()
            payload["LuminaRottenTomatoesRating"] = rating.map { $0 as Any } ?? NSNull()
            payload["LuminaMetacriticRating"] = rating.map { $0 as Any } ?? NSNull()
            payload["CriticRating"] = rating.map { $0 as Any } ?? NSNull()
            let item = try JSONDecoder().decode(MediaItem.self, from: json(payload))
            let expected = rating.flatMap { (0...100).contains($0) ? Int($0) : nil }
            XCTAssertEqual(item.criticRatingPercentage, expected)
            let roundTrip = try JSONDecoder().decode(MediaItem.self, from: JSONEncoder().encode(item))
            XCTAssertEqual(roundTrip.criticRatingPercentage, expected)
            XCTAssertEqual(roundTrip.validIMDbRating, rating.flatMap { (0...10).contains($0) ? $0 : nil })
            XCTAssertEqual(roundTrip.validRottenTomatoesRating, rating.flatMap { (0...100).contains($0) ? $0 : nil })
            XCTAssertEqual(roundTrip.validMetacriticRating, rating.flatMap { (0...100).contains($0) ? $0 : nil })
        }
        XCTAssertNil(try JSONDecoder().decode(MediaItem.self, from: json(movie())).criticRatingPercentage)
    }

    func testMediaOverviewKeepsProseWhileStructuringExplicitCreditsAndNotes() {
        let overview = MediaOverview("""
        <p>The <strong>first</strong> sentence &amp; <em>its punctuation</em> &copy;.</p><p>The second paragraph remains intact.</p>
        <p>(Source: The Archive)</p>
        <p>Notes:</p><ul><li>Restored from a 35mm print.</li><li>Restored from a 35mm print.</li></ul>
        """)

        XCTAssertEqual(overview.synopsis.map(\.text), [
            "The first sentence & its punctuation ©.",
            "The second paragraph remains intact.",
        ])
        XCTAssertEqual(overview.source, "The Archive")
        XCTAssertEqual(overview.notes.map(\.text), ["Restored from a 35mm print.", "Restored from a 35mm print."])

        let manyAmpersands = String(repeating: "&", count: 100_000)
        let plain = MediaOverview("Unknown prose &amp; <3.\n\nNotes: not a bullet list\n\n(Source credit: not structured)\n\n\(manyAmpersands)")
        XCTAssertEqual(Array(plain.synopsis.map(\.text).dropLast()), ["Unknown prose & <3.", "Notes: not a bullet list", "(Source credit: not structured)"])
        XCTAssertEqual(plain.synopsis.last?.text.count, manyAmpersands.count)
        XCTAssertNil(plain.source)
        XCTAssertTrue(plain.notes.isEmpty)

        let continuedNote = MediaOverview("Notes:\n- First note\n  continues here.\nUnindented prose remains a synopsis.")
        XCTAssertEqual(continuedNote.notes.map(\.text), ["First note continues here."])
        XCTAssertEqual(continuedNote.synopsis.map(\.text), ["Unindented prose remains a synopsis."])
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
        let client = LuminaClient(configuration: mockConfiguration(), videoCapabilities: {
            NativeVideoCapabilities(
                directMP4: [.h264: [.sdr], .hevc: [.sdr]], directMP4BitDepth: [.h264: 8, .hevc: 10],
                hls: [.h264: [.sdr], .hevc: [.sdr]], hlsBitDepth: [.h264: 8, .hevc: 10]
            )
        })
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "session-1", "MediaSources": [[
                    "Id": "source-1", "Container": "mkv", "SupportsDirectPlay": false, "SupportsTranscoding": true,
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
        XCTAssertEqual(request.sourceContainer, "mkv", "Source MKV must stay distinct from HLS delivery.")
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

    func testPlaybackInfoUsesInjectedNativeCodecAndRangeCapabilities() async throws {
        let capabilities = NativeVideoCapabilities(
            directMP4: [.h264: [.sdr], .hevc: [.sdr, .hdr10, .hlg], .av1: [.sdr]],
            directMP4BitDepth: [.h264: 8, .hevc: 10, .av1: 10],
            // AV1 is eligible for direct MP4 and fMP4 HLS, but never the
            // independently qualified PrismCore Matroska path. HLS HEVC is
            // intentionally SDR 8-bit; global codec-profile rules must not
            // broaden either delivery.
            hls: [.h264: [.sdr], .hevc: [.sdr], .av1: [.sdr]],
            hlsBitDepth: [.h264: 8, .hevc: 8, .av1: 8],
            directMP4DolbyVisionProfiles: [
                .init(profile: 5, compatibilityIDs: [0], maxLevel: 9),
                .init(profile: 8, compatibilityIDs: [1], maxLevel: 8),
                .init(profile: 8, compatibilityIDs: [4], maxLevel: 7),
            ],
            matroskaDolbyVisionPackagingConversions: [
                .init(
                    sourceProfile: 7, sourceCompatibilityIDs: [6],
                    targetProfile: 8, targetCompatibilityID: 1,
                    maxLevel: 8, enhancementLayerDisposition: "dropped"
                ),
            ],
            transportDolbyVisionPackagingConversions: [
                .init(
                    sourceProfile: 7, sourceCompatibilityIDs: [6],
                    targetProfile: 8, targetCompatibilityID: 1,
                    maxLevel: 8, enhancementLayerDisposition: "dropped"
                ),
            ]
        )
        let client = LuminaClient(configuration: mockConfiguration(), videoCapabilities: { capabilities })
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: ["PlaySessionId": "session", "MediaSources": []])
        }

        let modernResult = try await client.playbackInfo(origin: URL(string: "http://127.0.0.1:8765")!, token: "token", deviceID: "device", item: item, forceTranscode: false)
        _ = try await client.playbackInfo(origin: URL(string: "http://127.0.0.1:8765")!, token: "token", deviceID: "device", item: item, forceTranscode: true, forceCompatibleVideo: true)

        let modern = try XCTUnwrap(try body(requests.all[0])["DeviceProfile"] as? [String: Any])
        XCTAssertEqual(modernResult.negotiationDiagnostics?.advertisedHEVC, true)
        XCTAssertEqual(modernResult.negotiationDiagnostics?.advertisedHDR10, false,
                       "Diagnostics must capture the sent codec-profile intersection, not a broader direct-play HDR claim.")
        XCTAssertEqual(modernResult.negotiationDiagnostics?.advertisedProfile81MaxLevel, 8)
        let direct = try XCTUnwrap(modern["DirectPlayProfiles"] as? [[String: Any]])
        XCTAssertEqual(direct[0]["VideoCodec"] as? String, "h264,hevc,av1")
        let dolbyVision = try XCTUnwrap(direct[0]["LuminaDolbyVisionProfiles"] as? [[String: Any]])
        XCTAssertEqual(dolbyVision.count, 3)
        XCTAssertEqual(dolbyVision[0]["Profile"] as? Int, 5)
        XCTAssertEqual(dolbyVision[0]["CompatibilityIDs"] as? [Int], [0])
        XCTAssertEqual(dolbyVision[0]["MaxLevel"] as? Int, 9)
        XCTAssertEqual(dolbyVision[1]["Profile"] as? Int, 8)
        XCTAssertEqual(dolbyVision[1]["CompatibilityIDs"] as? [Int], [1])
        XCTAssertEqual(dolbyVision[1]["MaxLevel"] as? Int, 8)
        XCTAssertEqual(dolbyVision[2]["Profile"] as? Int, 8)
        XCTAssertEqual(dolbyVision[2]["CompatibilityIDs"] as? [Int], [4])
        XCTAssertEqual(dolbyVision[2]["MaxLevel"] as? Int, 7)
        XCTAssertNil(direct[0]["LuminaDolbyVisionPackagingConversions"], "Profile 7 conversion is never an MP4 direct-play claim.")
        XCTAssertNil(direct[0]["LuminaDolbyVisionSeparateTrackPackaging"])
        XCTAssertEqual(direct[1]["VideoCodec"] as? String, "h264,hevc", "PrismCore's qualified Matroska route excludes AV1 even when fMP4 HLS can use it.")
        let matroskaDolbyVision = try XCTUnwrap(direct[1]["LuminaDolbyVisionProfiles"] as? [[String: Any]])
        XCTAssertEqual(try JSONSerialization.data(withJSONObject: matroskaDolbyVision, options: .sortedKeys),
                       try JSONSerialization.data(withJSONObject: dolbyVision, options: .sortedKeys),
                       "The qualified Matroska path retains these same P5/P8 profiles and levels.")
        let packagingConversions = try XCTUnwrap(direct[1]["LuminaDolbyVisionPackagingConversions"] as? [[String: Any]])
        XCTAssertEqual(packagingConversions.count, 1)
        XCTAssertEqual(packagingConversions[0]["SourceProfile"] as? Int, 7)
        XCTAssertEqual(packagingConversions[0]["SourceCompatibilityIDs"] as? [Int], [6])
        XCTAssertEqual(packagingConversions[0]["TargetProfile"] as? Int, 8)
        XCTAssertEqual(packagingConversions[0]["TargetCompatibilityID"] as? Int, 1)
        XCTAssertEqual(packagingConversions[0]["MaxLevel"] as? Int, 8)
        XCTAssertEqual(packagingConversions[0]["EnhancementLayerDisposition"] as? String, "dropped")
        XCTAssertEqual(direct[1]["LuminaDolbyVisionSeparateTrackPackaging"] as? Bool, true)
        let transportProfiles = try XCTUnwrap(modern["LuminaDolbyVisionTransportProfiles"] as? [[String: Any]])
        XCTAssertEqual(transportProfiles.count, 1)
        let transport = transportProfiles[0]
        XCTAssertEqual(transport["Type"] as? String, "Video")
        XCTAssertEqual(transport["Container"] as? String, "m2ts")
        XCTAssertEqual(transport["VideoCodec"] as? String, "hevc")
        XCTAssertEqual(transport["AudioCodec"] as? String, "aac,ac3,eac3")
        XCTAssertEqual((transport["LuminaDolbyVisionProfiles"] as? [[String: Any]])?.isEmpty, true)
        XCTAssertEqual(transport["LuminaDolbyVisionM2TSPairing"] as? Bool, true)
        let transportConversions = try XCTUnwrap(transport["LuminaDolbyVisionPackagingConversions"] as? [[String: Any]])
        XCTAssertEqual(transportConversions.count, 1)
        XCTAssertEqual(transportConversions[0]["SourceProfile"] as? Int, 7)
        XCTAssertEqual(transportConversions[0]["TargetProfile"] as? Int, 8)
        XCTAssertEqual(transportConversions[0]["TargetCompatibilityID"] as? Int, 1)
        XCTAssertEqual(transportConversions[0]["EnhancementLayerDisposition"] as? String, "dropped")
        let hls = try XCTUnwrap(modern["TranscodingProfiles"] as? [[String: Any]])
        XCTAssertEqual(hls[0]["VideoCodec"] as? String, "h264,hevc,av1")
        XCTAssertNil(hls[0]["LuminaDolbyVisionProfiles"], "Original Dolby Vision delivery must not broaden server HLS.")
        XCTAssertNil(hls[0]["LuminaDolbyVisionPackagingConversions"], "Profile 7 conversion must not broaden HLS.")
        XCTAssertNil(hls[0]["LuminaDolbyVisionSeparateTrackPackaging"])
        let codecProfiles = try XCTUnwrap(modern["CodecProfiles"] as? [[String: Any]])
        let hevc = try XCTUnwrap(codecProfiles.first { $0["Codec"] as? String == "hevc" })
        let hevcConditions = try XCTUnwrap(hevc["Conditions"] as? [[String: String]])
        XCTAssertTrue(hevcConditions.contains(["Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR"]))
        XCTAssertTrue(hevcConditions.contains(["Property": "VideoBitDepth", "Condition": "LessThanEqual", "Value": "8"]))
        XCTAssertTrue(hevcConditions.contains(["Property": "Height", "Condition": "LessThanEqual", "Value": "2160"]))
        let av1 = try XCTUnwrap(codecProfiles.first { $0["Codec"] as? String == "av1" })
        XCTAssertTrue((av1["Conditions"] as? [[String: String]])?.contains([
            "Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR",
        ]) == true)

        let fallback = try XCTUnwrap(try body(requests.all[1])["DeviceProfile"] as? [String: Any])
        let fallbackHLS = try XCTUnwrap(fallback["TranscodingProfiles"] as? [[String: Any]])
        XCTAssertEqual(fallbackHLS[0]["VideoCodec"] as? String, "h264")
        let fallbackProfiles = try XCTUnwrap(fallback["CodecProfiles"] as? [[String: Any]])
        let fallbackVideoProfiles = fallbackProfiles.filter { $0["Type"] as? String == "Video" }
        XCTAssertEqual(fallbackVideoProfiles.compactMap { $0["Codec"] as? String }, ["h264"])
        let h264 = try XCTUnwrap(fallbackProfiles.first { $0["Codec"] as? String == "h264" })
        XCTAssertTrue((h264["Conditions"] as? [[String: String]])?.contains(["Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR"]) == true)
        let fallbackDirect = try XCTUnwrap(fallback["DirectPlayProfiles"] as? [[String: Any]])
        XCTAssertTrue(fallbackDirect.allSatisfy { $0["AudioCodec"] as? String == "aac" })
        XCTAssertTrue(fallbackDirect.allSatisfy { ($0["LuminaDolbyVisionProfiles"] as? [[String: Any]])?.isEmpty == true })
        XCTAssertTrue(fallbackDirect.allSatisfy { (($0["LuminaDolbyVisionPackagingConversions"] as? [[String: Any]]) ?? []).isEmpty })
        XCTAssertTrue(fallbackDirect.allSatisfy { ($0["LuminaDolbyVisionSeparateTrackPackaging"] as? Bool) != true })
        XCTAssertNil(fallback["LuminaDolbyVisionTransportProfiles"], "Forced compatibility must not advertise a transport conversion route.")
        let aac = try XCTUnwrap(fallbackProfiles.first { $0["Codec"] as? String == "aac" })
        XCTAssertEqual(aac["Type"] as? String, "VideoAudio")
        XCTAssertEqual(aac["Conditions"] as? [[String: String]], [
            ["Property": "AudioChannels", "Condition": "LessThanEqual", "Value": "2"],
        ])
    }

    func testPlaybackNegotiationCaptureKeepsRequestEvidenceAndExcludesPrivateURLData() async throws {
        let capabilities = NativeVideoCapabilities.from(.init(
            hevcHardwareDecode: false, dolbyVisionHardwareDecode: false,
            av1HardwareDecode: false, hdrEligible: false, dolbyVisionRoute: false,
            p81Supported: true, converterAvailable: false,
            playableMP4Codecs: ["hvc1.2.4.L153.B0", "hvc1.2.4.L150.B0", "hvc1"]
        ))
        let client = LuminaClient(configuration: mockConfiguration(), videoCapabilities: { capabilities })
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            return response(request, json: [
                "PlaySessionId": "private-session", "MediaSources": [[
                    "Id": "private-source", "Container": "mkv",
                    "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/private-item/master.m3u8?ApiKey=private-stream-key&VideoCodec=h264&MaxHeight=1080&TranscodeReasons=VideoCodecNotSupported,private-reason",
                ]],
                "negotiationDiagnostics": ["advertisedHEVC": true],
            ])
        }
        let origin = URL(string: "https://example.com")!
        let result = try await client.playbackInfo(origin: origin, token: "private-token", deviceID: "private-device",
                                                  item: item, forceTranscode: false)
        let request = try client.playbackRequest(origin: origin, token: "private-token", item: item, result: result)
        let evidence = try XCTUnwrap(request.negotiationDiagnostics)
        XCTAssertEqual(evidence.hevcHardwareDecode, false)
        XCTAssertEqual(evidence.hevcMain10MP4Playable, true)
        XCTAssertEqual(evidence.hevcMain10Level50MP4Playable, true)
        XCTAssertEqual(evidence.hevcBasicMP4Playable, true)
        XCTAssertEqual(evidence.hdrDisplayEligible, false)
        XCTAssertEqual(evidence.profile81RuntimeSupported, true)
        XCTAssertFalse(evidence.advertisedHEVC)
        XCTAssertFalse(evidence.advertisedHDR10)
        XCTAssertFalse(evidence.forcedServerHLS)
        XCTAssertFalse(evidence.forcedCompatibility)
        XCTAssertFalse(evidence.audioSelectionRequested)
        XCTAssertFalse(evidence.subtitleSelectionRequested)
        XCTAssertNil(evidence.qualityHeightLimit)
        XCTAssertNil(evidence.maximumStreamingBitrate)
        XCTAssertEqual(evidence.serverDirectPlay, false)
        XCTAssertEqual(evidence.serverVideoCodec, .h264)
        XCTAssertEqual(evidence.serverMaxHeight, 1080)
        XCTAssertEqual(evidence.serverReasons, [.videoCodec])
        XCTAssertTrue(evidence.unknownServerReason)
        let posted = try body(try XCTUnwrap(requests.all.first))
        XCTAssertEqual(posted["EnableDirectPlay"] as? Bool, true)
        let profile = try XCTUnwrap(posted["DeviceProfile"] as? [String: Any])
        let codecs = try XCTUnwrap(profile["CodecProfiles"] as? [[String: Any]])
        XCTAssertFalse(codecs.contains { $0["Codec"] as? String == "hevc" })

        let retryResult = try await client.playbackInfo(origin: origin, token: "private-token", deviceID: "private-device",
                                                       item: item, forceTranscode: true, quality: .upTo1080p,
                                                       audioStreamIndex: 3, subtitleStreamIndex: -1, forceCompatibleVideo: true)
        let retry = try client.playbackRequest(origin: origin, token: "private-token", item: item, result: retryResult,
                                               usesCompatibleVideo: true)
        XCTAssertEqual(retry.negotiationDiagnostics?.forcedCompatibility, true)
        XCTAssertEqual(retry.negotiationDiagnostics?.forcedServerHLS, true)
        XCTAssertEqual(retry.negotiationDiagnostics?.audioSelectionRequested, true)
        XCTAssertEqual(retry.negotiationDiagnostics?.subtitleSelectionRequested, true)
        XCTAssertEqual(retry.negotiationDiagnostics?.subtitlesOffRequested, true)
        XCTAssertEqual(retry.negotiationDiagnostics?.qualityHeightLimit, 1080)
        XCTAssertEqual(retry.negotiationDiagnostics?.maximumStreamingBitrate, 8_000_000)
        XCTAssertNil(retry.negotiationDiagnostics?.hevcHardwareDecode,
                     "A forced compatibility profile must not fabricate hardware observations.")
        XCTAssertFalse(evidence.forcedCompatibility, "Later requests must not overwrite earlier request evidence.")

        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jsonl")
        defer { try? FileManager.default.removeItem(at: file) }
        let capture = try PlaybackDiagnosticCapture.start(fileURL: file, version: "0.1.0", build: "42",
                                                          osVersion: .init(majorVersion: 27, minorVersion: 2, patchVersion: 0))
        var sample = PlaybackDiagnosticSample()
        sample.negotiation = evidence
        XCTAssertTrue(capture.record(.modelCreated, sample: sample))
        _ = try await capture.finish(reason: .user)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains("VideoCodecNotSupported"))
        XCTAssertTrue(text.contains("hevcMain10MP4Playable"))
        for secret in ["private-stream-key", "private-token", "private-device", "private-source", "private-item",
                       "private-session", "private-reason", "example.com", "ApiKey", "/Videos/"] {
            XCTAssertFalse(text.contains(secret), "Negotiation capture must exclude private URLs and identifiers.")
        }
    }

    func testDolbyVisionCapabilityPolicyRequiresExactRouteAndProfileQueries() {
        let baselineCodecs: Set<String> = ["hvc1.2.4.L153.B0", "av01.0.13M.08", "av01.0.13M.10"]
        let p5 = "dvh1.05.09"
        let p8 = "dvh1.08.07"
        func capabilities(
            main10: Bool = true, dvHardware: Bool = true, hdr: Bool = true,
            route: Bool = true, p81: Bool = false, converter: Bool = false, codecs: Set<String>
        ) -> NativeVideoCapabilities {
            NativeVideoCapabilities.from(.init(
                hevcHardwareDecode: main10,
                dolbyVisionHardwareDecode: dvHardware,
                av1HardwareDecode: true,
                hdrEligible: hdr,
                dolbyVisionRoute: route,
                p81Supported: p81,
                converterAvailable: converter,
                playableMP4Codecs: codecs
            ))
        }

        XCTAssertEqual(capabilities(codecs: baselineCodecs).directMP4DolbyVisionProfiles, [])
        XCTAssertNil(capabilities(codecs: ["hvc1", "hvc1.2.4.L150.B0"]).hls[.hevc],
                     "Diagnostic-only lower codec queries must not broaden the playback profile.")
        XCTAssertEqual(capabilities(route: false, codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [])
        XCTAssertEqual(capabilities(hdr: false, codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [])
        XCTAssertEqual(capabilities(main10: false, codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [])
        XCTAssertEqual(capabilities(dvHardware: false, codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [
            .init(profile: 8, compatibilityIDs: [4], maxLevel: 7),
        ])
        XCTAssertEqual(capabilities(codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [
            .init(profile: 5, compatibilityIDs: [0], maxLevel: 9),
            .init(profile: 8, compatibilityIDs: [4], maxLevel: 7),
        ])
        XCTAssertEqual(capabilities(p81: true, codecs: baselineCodecs.union([p5, p8])).directMP4DolbyVisionProfiles, [
            .init(profile: 5, compatibilityIDs: [0], maxLevel: 9),
            .init(profile: 8, compatibilityIDs: [1], maxLevel: 7),
            .init(profile: 8, compatibilityIDs: [4], maxLevel: 7),
        ])
        XCTAssertEqual(capabilities(p81: true, codecs: baselineCodecs.union([p5, p8])).matroskaDolbyVisionPackagingConversions, [])
        XCTAssertEqual(capabilities(p81: true, converter: true, codecs: baselineCodecs.union([p5, p8])).matroskaDolbyVisionPackagingConversions, [
            .init(
                sourceProfile: 7, sourceCompatibilityIDs: [6],
                targetProfile: 8, targetCompatibilityID: 1,
                maxLevel: 7, enhancementLayerDisposition: "dropped"
            ),
        ])
        XCTAssertEqual(
            capabilities(p81: true, converter: true, codecs: baselineCodecs.union([p5, p8])).transportDolbyVisionPackagingConversions,
            capabilities(p81: true, converter: true, codecs: baselineCodecs.union([p5, p8])).matroskaDolbyVisionPackagingConversions,
            "M2TS conversion cannot be advertised before the same P8.1/converter gates pass."
        )
    }

    func testOnlyNarrowAVFoundationCodecFailuresCanRetryCompatibility() throws {
        let format = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.fileFormatNotRecognized.rawValue)
        let decoder = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue)
        let unsupported = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.formatUnsupported.rawValue)
        let parse = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.fileFailedToParse.rawValue)
        let failedToParse = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.failedToParse.rawValue)
        let dolbyVisionConversion = NativeVideoSource.SelectionError.invalidDolbyVisionConversion
        let nestedNetwork = NSError(
            domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue,
            userInfo: [NSUnderlyingErrorKey: URLError(.cannotConnectToHost)]
        )
        let nestedAuthorization = NSError(
            domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue,
            userInfo: [NSUnderlyingErrorKey: NSError(
                domain: AVFoundationErrorDomain, code: AVError.Code.contentIsNotAuthorized.rawValue
            )]
        )
        let nestedProtectedContent = NSError(
            domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue,
            userInfo: [NSUnderlyingErrorKey: NSError(
                domain: AVFoundationErrorDomain, code: AVError.Code.contentIsProtected.rawValue
            )]
        )
        let authorization = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.contentIsNotAuthorized.rawValue)
        let network = URLError(.cannotConnectToHost)
        let rejectedMaster = NSError(domain: "CoreMediaErrorDomain", code: -12927)
        let rejectedMasterNetwork = NSError(domain: "CoreMediaErrorDomain", code: -12927,
                                             userInfo: [NSUnderlyingErrorKey: URLError(.cannotConnectToHost)])
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(rejectedMaster))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(NSError(domain: "CoreMediaErrorDomain", code: -1)))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(rejectedMasterNetwork))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(format))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(decoder))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(unsupported))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(parse))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(failedToParse))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(NativeVideoSource.SelectionError.unsupportedAudioStream(2)))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(dolbyVisionConversion))
        XCTAssertTrue(NativeVideoCapabilities.isFormatFailure(NativeVideoSource.SelectionError.subtitleMasterUnavailable))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(authorization))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(network))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(nestedNetwork))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(nestedAuthorization))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(nestedProtectedContent))
        XCTAssertFalse(NativeVideoCapabilities.isFormatFailure(CancellationError()))
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let request = PlaybackRequest(id: "play", item: item, url: URL(string: "https://lumina.example/hls")!,
                                      startPositionSeconds: 12, mediaSourceID: "source", isTranscoding: false, methodLabel: "Direct")
        XCTAssertTrue(request.canRetryCompatibleVideo(after: format))
        XCTAssertTrue(request.canRetryCompatibleVideo(after: rejectedMaster))
        XCTAssertFalse(request.canRetryCompatibleVideo(after: rejectedMaster, hasAdvanced: true))
        XCTAssertTrue(request.canRetryCompatibleVideo(after: NativeVideoSource.SelectionError.subtitleMasterUnavailable))
        XCTAssertFalse(request.canRetryCompatibleVideo(after: NativeVideoSource.SelectionError.subtitleMasterUnavailable, hasAdvanced: true))
        XCTAssertFalse(request.canRetryCompatibleVideo(after: network))
        XCTAssertTrue(request.canRetryCompatibleVideo(after: dolbyVisionConversion, hasAdvanced: true))
        XCTAssertFalse(request.canRetryCompatibleVideo(after: format, hasAdvanced: true))
        XCTAssertFalse(request.canRetryCompatibleVideo(after: network, hasAdvanced: true))
        let compatible = PlaybackRequest(id: "retry", item: item, url: request.url,
                                         startPositionSeconds: 12, mediaSourceID: "source", isTranscoding: true,
                                         methodLabel: "HLS", usesCompatibleVideo: true)
        XCTAssertFalse(compatible.canRetryCompatibleVideo(after: format))
        XCTAssertFalse(compatible.canRetryCompatibleVideo(after: rejectedMaster))
        XCTAssertFalse(compatible.canRetryCompatibleVideo(after: NativeVideoSource.SelectionError.subtitleMasterUnavailable))
        XCTAssertFalse(compatible.canRetryCompatibleVideo(after: dolbyVisionConversion, hasAdvanced: true))
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

    func testPlaybackRequestKeepsAutomaticSubtitleUnselectedForClientPackaging() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        for (container, clientPackaging) in [("mkv", nil), ("m2ts", "p7_m2ts_to_p81_drop_el")] {
            var source: [String: Any] = [
                "Id": "source-\(container)", "Container": container,
                "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "DefaultSubtitleStreamIndex": 5,
                "MediaStreams": [["Index": 5, "Type": "Subtitle", "Codec": "subrip"]],
            ]
            if let clientPackaging { source["LuminaClientPackaging"] = clientPackaging }
            let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
                "PlaySessionId": "automatic-subtitle-\(container)", "MediaSources": [source],
            ]))

            let automatic = try LuminaClient().playbackRequest(
                origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info
            )
            let explicit = try LuminaClient().playbackRequest(
                origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
                subtitleStreamIndex: 5
            )
            let off = try LuminaClient().playbackRequest(
                origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
                subtitleStreamIndex: -1
            )

            XCTAssertTrue(automatic.usesClientDemux, "\(container) must retain its qualified local packaging route.")
            XCTAssertNil(automatic.subtitleStreamIndex, "Automatic must not turn a source default into an explicit embedded selection.")
            XCTAssertEqual(explicit.subtitleStreamIndex, 5)
            XCTAssertEqual(off.subtitleStreamIndex, -1)
        }

        let mp4 = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "default-subtitle-mp4", "MediaSources": [[
                "Id": "source-mp4", "Container": "mp4", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                "DefaultSubtitleStreamIndex": 5,
            ]],
        ]))
        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: mp4
        )
        XCTAssertEqual(request.subtitleStreamIndex, 5, "Non-packaged routes retain the server-default subtitle behavior.")

        let hls = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "default-subtitle-hls", "MediaSources": [[
                "Id": "source-mkv-hls", "Container": "mkv", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                "TranscodingUrl": "/Videos/source-mkv-hls/master.m3u8?ApiKey=test-token",
                "DefaultSubtitleStreamIndex": 5,
            ]],
        ]))
        let serverRequest = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: hls
        )
        XCTAssertFalse(serverRequest.usesClientDemux)
        XCTAssertEqual(serverRequest.subtitleStreamIndex, 5,
                       "A server HLS delivery retains its default captions even when the original container was MKV.")
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
            selectedSourceChapters: [PlaybackChapter(id: 10_000_000, title: "Wrong fallback")],
        )
        XCTAssertTrue(alternate.chapters.isEmpty)
    }

    func testPreparePlaybackReadsOnlyTheSelectedVersionChaptersWhenPlaybackInfoOmitsThem() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                var title = movie()
                title["Chapters"] = [["StartPositionTicks": 0, "Name": "Preferred opening"]]
                return response(request, json: title)
            case "/Users/user-1/Items/source-version":
                var version = movie()
                version["Id"] = "source-version"
                version["Chapters"] = [["StartPositionTicks": 100_000_000, "Name": "Version chapter"]]
                return response(request, json: version)
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "version-chapters", "MediaSources": [[
                    "Id": "source-version", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: item)

        XCTAssertEqual(request.chapters.map(\.id), [100_000_000])
        XCTAssertEqual(request.chapters.map(\.title), ["Version chapter"])
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Users/user-1/Items/source-version" }.count, 1)
    }

    func testPreparePlaybackDoesNotBorrowTitleChaptersWhenSelectedVersionIsUnavailable() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                var title = movie()
                title["Chapters"] = [["StartPositionTicks": 0, "Name": "Preferred opening"]]
                return response(request, json: title)
            case "/Users/user-1/Items/missing-version": return response(request, status: 404)
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "missing-version", "MediaSources": [[
                    "Id": "missing-version", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

        let request = try await store.preparePlayback(for: item)

        XCTAssertTrue(request.chapters.isEmpty)
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Users/user-1/Items/missing-version" }.count, 1)
    }

    func testPlaybackMarkersAndNextEpisodeUseRecordedSourceMetadataAndSeriesOrder() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        var currentPayload = movie()
        currentPayload["Id"] = "episode-1"
        currentPayload["Type"] = "Episode"
        currentPayload["SeriesId"] = "series-1"
        currentPayload["RunTimeTicks"] = 14_000_000_000
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
                    ["Type": "Intro", "StartTicks": 104_700_000, "EndTicks": 1_005_640_000],
                    ["Type": "Outro", "StartTicks": 13_305_800_000, "EndTicks": 13_906_530_000],
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
        XCTAssertEqual(markers.map(\.startSeconds), [10.47, 1330.58])
        XCTAssertEqual(markers.map(\.endSeconds), [100.564, 1390.653])
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

    func testStartOverReusesPreparedOriginalWithoutWaitingForServer() async throws {
        let saved = syntheticKeychainSession()
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie(positionTicks: 60_000_000))
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "prepared-original", "MediaSources": [[
                    "Id": "movie-1", "Container": "mkv", "Bitrate": 24_000_000,
                    "SupportsDirectPlay": true, "SupportsTranscoding": false,
                    "DefaultAudioStreamIndex": 1, "DefaultSubtitleStreamIndex": -1,
                    "MediaStreams": [["Index": 1, "Type": "Audio", "Codec": "aac", "Channels": 2]],
                    "Chapters": [["StartPositionTicks": 60_000_000, "Name": "Opening"]],
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let prepared = try await store.preparePlayback(for: item)
        XCTAssertEqual(prepared.startPositionSeconds, 6)
        MockURLProtocol.handler = { request in
            requests.append(request)
            throw URLError(.timedOut)
        }

        let restarted = try await store.preparePlaybackFromBeginning(after: prepared)

        XCTAssertTrue(requests.all.isEmpty, "Start Over must not wait for another metadata or playback decision for an already prepared original.")
        XCTAssertEqual(restarted.startPositionSeconds, 0)
        XCTAssertEqual(restarted.item, prepared.item, "The chooser must leave saved progress untouched until playback begins.")
        XCTAssertEqual(restarted.url, prepared.url)
        XCTAssertEqual(restarted.id, prepared.id)
        XCTAssertEqual(restarted.mediaSourceID, prepared.mediaSourceID)
        XCTAssertEqual(restarted.audioStreamIndex, prepared.audioStreamIndex)
        XCTAssertEqual(restarted.subtitleStreamIndex, prepared.subtitleStreamIndex)
        XCTAssertEqual(restarted.chapters, prepared.chapters)
        XCTAssertEqual(restarted.resolvedQuality, prepared.resolvedQuality)
        XCTAssertTrue(restarted.usesClientDemux)
        XCTAssertEqual(restarted.bitRate, prepared.bitRate)
        XCTAssertEqual(restarted.sourceContainer, prepared.sourceContainer)
        XCTAssertEqual(restarted.negotiationDiagnostics, prepared.negotiationDiagnostics)
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
                                                       startPositionSeconds: 11.25, usesServerVideoRecovery: true)

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

        let restarted = try await store.preparePlaybackFromBeginning(after: request)
        XCTAssertTrue(restarted.isTranscoding)
        XCTAssertTrue(restarted.usesServerVideoRecovery, "Start Over must retain the quality-preserving recovery tier.")
        XCTAssertEqual(restarted.startPositionSeconds, 0)
        let negotiations = requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }
        XCTAssertEqual(negotiations.count, 2, "HLS must obtain a fresh zero-position server session.")
        let restartBody = try body(try XCTUnwrap(negotiations.last))
        XCTAssertEqual(restartBody["StartTimeTicks"] as? Int64, 0)
        XCTAssertEqual(restartBody["EnableDirectPlay"] as? Bool, false)
        XCTAssertEqual(restartBody["AudioStreamIndex"] as? Int, 3)
        XCTAssertEqual(restartBody["SubtitleStreamIndex"] as? Int, -1)
        XCTAssertEqual(restartBody["MediaSourceId"] as? String, "source-2")
        XCTAssertEqual(restarted.resolvedQuality, .upTo720p)
    }

    func testPrepareCompatiblePlaybackPreservesSelectionAndRequiresServerHLS() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let client = LuminaClient(configuration: mockConfiguration())
        let store = LuminaStore(client: client, credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "compatible", "MediaSources": [[
                    "Id": "source-2", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-2/master.m3u8?ApiKey=test-token",
                    "DefaultAudioStreamIndex": 3,
                    "MediaStreams": [["Index": 3, "Type": "Audio", "Codec": "aac"]],
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let original = PlaybackRequest(
            id: "original", item: item, url: URL(string: "http://127.0.0.1:8765/Videos/movie-1/stream")!,
            startPositionSeconds: 11.25, mediaSourceID: "source-2", isTranscoding: false,
            methodLabel: "Direct", audioStreamIndex: 3, subtitleStreamIndex: -1,
            resolvedQuality: .upTo720p
        )

        let retry = try await store.prepareCompatiblePlayback(
            after: original,
            error: NSError(domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue)
        )

        XCTAssertTrue(retry.isTranscoding)
        XCTAssertTrue(retry.usesCompatibleVideo)
        XCTAssertFalse(retry.usesServerVideoRecovery)
        XCTAssertEqual(retry.mediaSourceID, "source-2")
        XCTAssertEqual(retry.resolvedQuality, .upTo720p)
        XCTAssertEqual(retry.audioStreamIndex, 3)
        XCTAssertEqual(retry.subtitleStreamIndex, -1)
        XCTAssertEqual(retry.startPositionSeconds, 11.25)
        let playbackInfo = try XCTUnwrap(requests.all.first { $0.url?.path == "/Items/movie-1/PlaybackInfo" })
        let posted = try body(playbackInfo)
        XCTAssertEqual(posted["EnableDirectPlay"] as? Bool, false)
        XCTAssertEqual(posted["MediaSourceId"] as? String, "source-2")
        XCTAssertEqual(posted["AudioStreamIndex"] as? Int, 3)
        XCTAssertEqual(posted["SubtitleStreamIndex"] as? Int, -1)
        XCTAssertEqual(posted["StartTimeTicks"] as? Int64, 112_500_000)
        let profile = try XCTUnwrap(posted["DeviceProfile"] as? [String: Any])
        XCTAssertEqual((try XCTUnwrap(profile["TranscodingProfiles"] as? [[String: Any]])).first?["VideoCodec"] as? String, "h264")
        XCTAssertTrue((try XCTUnwrap(profile["DirectPlayProfiles"] as? [[String: Any]])).allSatisfy { $0["AudioCodec"] as? String == "aac" })
        let h264 = try XCTUnwrap((profile["CodecProfiles"] as? [[String: Any]])?.first { $0["Codec"] as? String == "h264" })
        XCTAssertTrue((h264["Conditions"] as? [[String: String]])?.contains([
            "Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR",
        ]) == true)
        let aac = try XCTUnwrap((profile["CodecProfiles"] as? [[String: Any]])?.first { $0["Codec"] as? String == "aac" })
        XCTAssertEqual(aac["Conditions"] as? [[String: String]], [
            ["Property": "AudioChannels", "Condition": "LessThanEqual", "Value": "2"],
        ])

        let ignoredFallback = try JSONDecoder().decode(PlaybackInfo.self, from: json(["PlaySessionId": "ignored", "MediaSources": [[
            "Id": "source-2", "SupportsDirectPlay": true, "SupportsTranscoding": true,
        ]]]))
        XCTAssertThrowsError(try client.playbackRequest(
            origin: saved.serverURL, token: saved.accessToken, item: item, result: ignoredFallback,
            mediaSourceID: "source-2", usesCompatibleVideo: true
        )) { XCTAssertEqual($0 as? LuminaError, .malformedResponse) }
        XCTAssertThrowsError(try client.playbackRequest(
            origin: saved.serverURL, token: saved.accessToken, item: item, result: ignoredFallback,
            mediaSourceID: "source-2", usesServerVideoRecovery: true
        )) { XCTAssertEqual($0 as? LuminaError, .malformedResponse) }

        let playbackRequests = requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }
        do {
            _ = try await store.prepareCompatiblePlayback(after: original, error: URLError(.cannotConnectToHost))
            XCTFail("Transport failures must not start a compatibility request.")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
        XCTAssertEqual(requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }.count, playbackRequests.count)
    }

    func testPrepareCompatiblePlaybackUsesNormalHLSBeforeConservativeRetryForClientPackaging() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let capabilities = NativeVideoCapabilities(
            directMP4: [.h264: [.sdr], .hevc: [.sdr, .hdr10, .hlg]],
            directMP4BitDepth: [.h264: 8, .hevc: 10],
            hls: [.h264: [.sdr], .hevc: [.sdr, .hdr10, .hlg]],
            hlsBitDepth: [.h264: 8, .hevc: 10]
        )
        let client = LuminaClient(configuration: mockConfiguration(), videoCapabilities: { capabilities })
        let store = LuminaStore(client: client, credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return response(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return response(request, json: ["PlaySessionId": "server-recovery", "MediaSources": [[
                    "Id": "source-4k", "Container": "mkv", "Bitrate": 25_000_000,
                    "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-4k/master.m3u8?ApiKey=test-token",
                    "DefaultAudioStreamIndex": 3,
                    "MediaStreams": [
                        ["Index": 0, "Type": "Video", "Codec": "hevc", "Height": 2160],
                        ["Index": 3, "Type": "Audio", "Codec": "eac3"],
                        ["Index": 7, "Type": "Subtitle", "Codec": "subrip"],
                    ],
                ]]])
            default: return response(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let local = PlaybackRequest(
            id: "local-package", item: item, url: URL(string: "http://127.0.0.1:8765/Videos/movie-1/stream")!,
            startPositionSeconds: 11.25, mediaSourceID: "source-4k", isTranscoding: false,
            methodLabel: "Direct", usesClientDemux: true, audioStreamIndex: 3, subtitleStreamIndex: 7,
            resolvedQuality: .automatic
        )
        let formatFailure = NSError(domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue)

        let serverRecovery = try await store.prepareCompatiblePlayback(after: local, error: formatFailure)

        XCTAssertTrue(serverRecovery.isTranscoding)
        XCTAssertTrue(serverRecovery.usesServerVideoRecovery)
        XCTAssertFalse(serverRecovery.usesCompatibleVideo)
        XCTAssertEqual(serverRecovery.mediaSourceID, "source-4k")
        XCTAssertEqual(serverRecovery.audioStreamIndex, 3)
        XCTAssertEqual(serverRecovery.subtitleStreamIndex, 7)
        XCTAssertEqual(serverRecovery.startPositionSeconds, 11.25)
        XCTAssertEqual(serverRecovery.resolvedQuality, .automatic)
        let modernRequest = try XCTUnwrap(requests.all.first { $0.url?.path == "/Items/movie-1/PlaybackInfo" })
        let modern = try body(modernRequest)
        XCTAssertEqual(modern["EnableDirectPlay"] as? Bool, false)
        XCTAssertEqual(modern["MediaSourceId"] as? String, "source-4k")
        XCTAssertEqual(modern["AudioStreamIndex"] as? Int, 3)
        XCTAssertEqual(modern["SubtitleStreamIndex"] as? Int, 7)
        XCTAssertEqual(modern["StartTimeTicks"] as? Int64, 112_500_000)
        let modernProfile = try XCTUnwrap(modern["DeviceProfile"] as? [String: Any])
        XCTAssertEqual((try XCTUnwrap(modernProfile["TranscodingProfiles"] as? [[String: Any]])).first?["VideoCodec"] as? String, "h264,hevc")
        let hevc = try XCTUnwrap((modernProfile["CodecProfiles"] as? [[String: Any]])?.first { $0["Codec"] as? String == "hevc" })
        let hevcConditions = try XCTUnwrap(hevc["Conditions"] as? [[String: String]])
        XCTAssertTrue(hevcConditions.contains([
            "Property": "VideoRangeType", "Condition": "EqualsAny", "Value": "SDR|HDR10|HLG",
        ]))
        XCTAssertTrue(hevcConditions.contains([
            "Property": "Height", "Condition": "LessThanEqual", "Value": "2160",
        ]))

        let conservative = try await store.prepareCompatiblePlayback(after: serverRecovery, error: formatFailure)

        XCTAssertTrue(conservative.isTranscoding)
        XCTAssertFalse(conservative.usesServerVideoRecovery)
        XCTAssertTrue(conservative.usesCompatibleVideo)
        XCTAssertEqual(conservative.mediaSourceID, "source-4k")
        XCTAssertEqual(conservative.audioStreamIndex, 3)
        XCTAssertEqual(conservative.subtitleStreamIndex, 7)
        XCTAssertEqual(conservative.startPositionSeconds, 11.25)
        XCTAssertEqual(conservative.resolvedQuality, .automatic)
        XCTAssertFalse(conservative.canRetryCompatibleVideo(after: formatFailure))
        let compatibility = try body(try XCTUnwrap(requests.all.last { $0.url?.path == "/Items/movie-1/PlaybackInfo" }))
        let compatibilityProfile = try XCTUnwrap(compatibility["DeviceProfile"] as? [String: Any])
        XCTAssertEqual((try XCTUnwrap(compatibilityProfile["TranscodingProfiles"] as? [[String: Any]])).first?["VideoCodec"] as? String, "h264")
        XCTAssertEqual(compatibility["EnableDirectPlay"] as? Bool, false)

        let conversion = try await store.prepareCompatiblePlayback(
            after: local, error: NativeVideoSource.SelectionError.invalidDolbyVisionConversion
        )
        XCTAssertTrue(conversion.usesCompatibleVideo,
                      "A failed Profile 7 conversion must not retry as normal HEVC HLS.")
        XCTAssertFalse(conversion.usesServerVideoRecovery)

        do {
            _ = try await store.prepareCompatiblePlayback(after: conservative, error: formatFailure)
            XCTFail("The conservative request must be the final recovery tier.")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
    }

    func testPlaybackRequestChoosesTheExplicitReturnedMediaSourceAndBoundsStartPosition() throws {
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
            "PlaySessionId": "version", "MediaSources": [
                ["Id": "source-one", "Container": "mp4", "SupportsDirectPlay": true, "SupportsTranscoding": false],
                ["Id": "source-two", "Container": "mkv", "SupportsDirectPlay": true, "SupportsTranscoding": false, "Bitrate": 2_000_000],
                ["Id": "unknown-container", "SupportsDirectPlay": true, "SupportsTranscoding": false],
            ],
        ]))
        let request = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
            mediaSourceID: "source-two", startPositionSeconds: .greatestFiniteMagnitude
        )

        XCTAssertEqual(request.mediaSourceID, "source-two")
        XCTAssertEqual(request.bitRate, 2_000_000)
        XCTAssertEqual(request.sourceContainer, "mkv", "Stats must describe the selected version, not the preferred file.")
        let unknown = try LuminaClient().playbackRequest(
            origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info,
            mediaSourceID: "unknown-container"
        )
        XCTAssertNil(unknown.sourceContainer, "An unknown alternate container must not inherit another file's format.")
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

    func testPreparePlaybackKeepsMarkedM2TSExternalSubtitleDirectAndUnmarkedFallsBackToHLS() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        for (marked, expectedRequests) in [(true, 1), (false, 2)] {
            let defaultsName = "LuminaCoreTests.m2ts-external-subtitle.\(UUID().uuidString)"
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
                    let selectedSource = try body(request)["MediaSourceId"] as? String
                    let streams: [[String: Any]] = [
                        ["Index": 0, "Type": "Video", "Height": 2160],
                        ["Index": 3, "Type": "Subtitle", "Codec": "vtt", "Language": "spa", "IsExternal": true,
                         "DeliveryMethod": "External", "DeliveryUrl": "/Videos/movie-1/Subtitles/3/0/Stream.vtt?ApiKey=test-token"],
                    ]
                    if selectedSource == nil {
                        var source: [String: Any] = [
                            "Id": "source-m2ts", "Container": "m2ts", "Bitrate": 24_000_000,
                            "SupportsDirectPlay": true, "SupportsTranscoding": true,
                            "DefaultSubtitleStreamIndex": 3, "MediaStreams": streams,
                        ]
                        if marked { source["LuminaClientPackaging"] = "p7_m2ts_to_p81_drop_el" }
                        return response(request, json: ["PlaySessionId": "m2ts", "MediaSources": [source]])
                    }
                    return response(request, json: ["PlaySessionId": "server-hls", "MediaSources": [[
                        "Id": "source-m2ts", "Container": "m2ts", "Bitrate": 24_000_000,
                        "SupportsDirectPlay": false, "SupportsTranscoding": true,
                        "TranscodingUrl": "/Videos/source-m2ts/master.m3u8?ApiKey=test-token", "MediaStreams": streams,
                    ]]])
                default: return response(request, status: 404)
                }
            }
            await store.restore()
            let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))

            let playback = try await store.preparePlayback(for: item)
            let playbackInfo = requests.all.filter { $0.url?.path == "/Items/movie-1/PlaybackInfo" }
            XCTAssertEqual(playbackInfo.count, expectedRequests, "marked=\(marked)")
            XCTAssertEqual(playback.subtitleStreamIndex, 3, "The preferred external subtitle remains selected.")
            XCTAssertTrue(playback.mediaStreams.contains { $0.index == 3 && $0.hasExternalSubtitle })
            XCTAssertEqual(playback.usesClientDemux, marked)
            XCTAssertEqual(playback.isTranscoding, !marked)
            if !marked {
                let fallback = try body(try XCTUnwrap(playbackInfo.last))
                XCTAssertEqual(fallback["EnableDirectPlay"] as? Bool, false)
                XCTAssertEqual(fallback["MediaSourceId"] as? String, "source-m2ts")
                XCTAssertEqual(fallback["SubtitleStreamIndex"] as? Int, 3)
            }
        }
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

    func testDirectM2TSDolbyVisionPairRequiresTheExactServerPackagingMarker() throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        func request(container: String, packaging: String?) throws -> PlaybackRequest {
            var source: [String: Any] = [
                "Id": "source", "Container": container,
                "SupportsDirectPlay": true, "SupportsTranscoding": false,
            ]
            if let packaging { source["LuminaClientPackaging"] = packaging }
            let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
                "PlaySessionId": "m2ts", "MediaSources": [source],
            ]))
            return try client.playbackRequest(origin: URL(string: "http://127.0.0.1:8765")!, token: "test-token", item: item, result: info)
        }

        XCTAssertTrue(try request(container: "m2ts", packaging: "p7_m2ts_to_p81_drop_el").usesClientDemux)
        XCTAssertFalse(try request(container: "m2ts", packaging: nil).usesClientDemux)
        XCTAssertFalse(try request(container: "m2ts", packaging: "p7_to_p81_drop_el").usesClientDemux)
        XCTAssertFalse(try request(container: "mts", packaging: "p7_m2ts_to_p81_drop_el").usesClientDemux)
    }

    func testDirectM2TSSeekIndexStaysBoundToSelectedSourceAndOrigin() throws {
        let client = LuminaClient(configuration: mockConfiguration())
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let origin = URL(string: "http://127.0.0.1:8765")!
        let sourceID = String(repeating: "a", count: 32)
        func request(indexURL: String, marked: Bool = true, container: String = "m2ts") throws -> PlaybackRequest {
            var source: [String: Any] = [
                "Id": sourceID, "Container": container, "SupportsDirectPlay": true,
                "SupportsTranscoding": false, "LuminaM2TSIRAPIndexUrl": indexURL,
            ]
            if marked { source["LuminaClientPackaging"] = "p7_m2ts_to_p81_drop_el" }
            let info = try JSONDecoder().decode(PlaybackInfo.self, from: json([
                "PlaySessionId": "indexed", "MediaSources": [source],
            ]))
            return try client.playbackRequest(origin: origin, token: "test-token", item: item, result: info)
        }
        let endpoint = "/videos/movie-1/lumina-m2ts-irap-index"
        let selected = try XCTUnwrap(request(indexURL: endpoint + "?MediaSourceId=wrong&ApiKey=stale#ignored").m2tsSeekIndex)
        XCTAssertEqual(selected.sourceID, sourceID)
        XCTAssertEqual(selected.url.path, endpoint)
        let query = URLComponents(url: selected.url, resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query, [URLQueryItem(name: "MediaSourceId", value: sourceID), URLQueryItem(name: "ApiKey", value: "test-token")])
        XCTAssertNil(selected.url.fragment)
        XCTAssertNil(try request(indexURL: "https://elsewhere.invalid" + endpoint).m2tsSeekIndex)
        XCTAssertNil(try request(indexURL: "http://member@127.0.0.1:8765" + endpoint).m2tsSeekIndex)
        XCTAssertNil(try request(indexURL: "/videos/movie-1/stream").m2tsSeekIndex)
        XCTAssertNil(try request(indexURL: endpoint, marked: false).m2tsSeekIndex)
        XCTAssertNil(try request(indexURL: endpoint, container: "mkv").m2tsSeekIndex)
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

    func testSelectedEpisodeDownloadsPersistOfflineAndAllowPartialFailureRetry() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-season-downloads-\(UUID())")
        defer { try? FileManager.default.removeItem(at: root) }
        let saved = syntheticKeychainSession()
        let episodes = try ["episode-1", "episode-2", "episode-3"].map { id in
            var payload = movie()
            payload["Id"] = id
            payload["Name"] = "Synthetic \(id)"
            payload["Type"] = "Episode"
            payload["SeriesId"] = "series-1"
            payload["SeasonId"] = "season-1"
            return try JSONDecoder().decode(MediaItem.self, from: json(payload))
        }
        var seasonPayload = movie()
        seasonPayload["Id"] = "season-1"
        seasonPayload["Type"] = "Season"
        let season = try JSONDecoder().decode(MediaItem.self, from: json(seasonPayload))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return response(request, json: ["Id": saved.userID, "Name": saved.username])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return response(request, json: page(items: []))
            case "/Items/episode-2/Download": return response(request, status: 403)
            case "/Items/episode-1/Download", "/Items/episode-3/Download":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                    headerFields: ["Content-Type": "video/mp4"])!, Data(repeating: 0, count: 64))
            default: return response(request, status: 404)
            }
        }
        let credentials = TestCredentials(session: saved)
        let offline = LuminaOfflineLibrary(root: root, configuration: mockConfiguration())
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials,
            connectivity: LuminaConnectivity(status: .online, monitoringEnabled: false), offline: offline)
        await store.restore()
        await store.downloadVideos(items: episodes + [episodes[0], season])
        XCTAssertEqual(Set(offline.downloadedItems.map(\.id)), ["episode-1", "episode-3"],
                       "One unavailable episode must not prevent other selected episodes from completing.")
        guard case .failed = offline.downloadStates["episode-2"] else { return XCTFail("The rejected download needs a retry state") }
        XCTAssertEqual(requests.all.compactMap(\.url?.path).filter { $0.hasSuffix("/Download") }.sorted(),
                       episodes.map { "/Items/\($0.id)/Download" }.sorted(),
                       "Duplicate selections and season containers must not cause extra file requests.")
        MockURLProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                headerFields: ["Content-Type": "video/mp4"])!, Data(repeating: 0, count: 64))
        }
        await store.downloadVideos(items: [episodes[1]])
        XCTAssertEqual(offline.downloadStates["episode-2"], .downloaded)
        let restored = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials,
            connectivity: LuminaConnectivity(status: .offline, monitoringEnabled: false),
            offline: LuminaOfflineLibrary(root: root, configuration: mockConfiguration()))
        await restored.restore()
        XCTAssertEqual(Set(restored.offlineVideoItems.map(\.id)), Set(episodes.map(\.id)))
        for episode in episodes {
            let playback = try await restored.preparePlayback(for: episode)
            XCTAssertTrue(playback.url.isFileURL, "Every selected episode must reopen from its local file while disconnected.")
        }
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
        do {
            _ = try await store.prepareCompatiblePlayback(
                after: playback,
                error: NSError(domain: AVFoundationErrorDomain, code: AVError.Code.decoderNotFound.rawValue)
            )
            XCTFail("Offline originals cannot masquerade as server-HLS recovery.")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }
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
