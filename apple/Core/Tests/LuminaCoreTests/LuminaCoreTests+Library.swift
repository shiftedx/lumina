import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
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
                return coreResponse(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName":
                return coreResponse(request, json: ["AccessToken": "test-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me":
                return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items":
                var featured = movie()
                featured["ImageTags"] = ["Primary": "poster-tag", "Logo": "logo-tag"]
                featured["BackdropImageTags"] = ["backdrop-tag"]
                featured["ParentBackdropItemId"] = "series-1"
                featured["ParentBackdropImageTags"] = ["series-backdrop-tag"]
                return coreResponse(request, json: page(items: [featured]))
            case "/Items/movie-1/Images/Backdrop/0/Framing", "/Items/series-1/Images/Backdrop/0/Framing":
                return coreResponse(request, json: ["state": "ready", "width": 1920, "height": 1080, "regions": []])
            case "/Items/pending/Images/Backdrop/0/Framing":
                let attempt = pendingFramingAttempts.next(request.url!.path)
                return coreResponse(request, json: ["state": attempt == 1 ? "pending" : "ready", "width": 1920, "height": 1080, "regions": []])
            case "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/UserItems/Resume":
                return coreResponse(request, json: page(items: []))
            default:
                return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [movie(positionTicks: 12_000_000)]))
            case "/Items":
                let start = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "StartIndex" }?.value
                var item = movie()
                item["Id"] = start == "0" ? "movie-1" : "movie-2"
                return coreResponse(request, json: ["Items": [item], "TotalRecordCount": 2])
            default: return coreResponse(request, status: 404)
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

    func testStandbyArtworkUsesIndependentMemberCatalogAndRetainsLibrary() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume", "/UserViews": return coreResponse(request, json: page(items: []))
            case "/Items":
                if value("IncludeItemTypes") == "Movie,Series" {
                    return coreResponse(request, json: page(items: [
                        ["Id": "backdrop", "Name": "Film", "Type": "Movie", "BackdropImageTags": ["backdrop-tag"]],
                        ["Id": "poster", "Name": "Show", "Type": "Series", "ImageTags": ["Primary": "poster-tag"]],
                        ["Id": "missing", "Name": "No art", "Type": "Movie"],
                        ["Id": "episode", "Name": "Episode", "Type": "Episode", "ImageTags": ["Primary": "tag"]],
                    ]))
                }
                return coreResponse(request, json: page(items: [["Id": "library-film", "Name": "Library", "Type": "Movie"]]))
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(kind: .movies, filters: VideoFilters(favoritesOnly: true))
        let retained = store.items
        let artwork = try await store.loadStandbyArtwork()
        XCTAssertEqual(artwork.map(\.id), ["backdrop", "poster"])
        XCTAssertEqual(store.items, retained)
        let request = try XCTUnwrap(requests.all.last { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "IncludeItemTypes" && $0.value == "Movie,Series" } == true
        })
        let query = URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(query.first { $0.name == "Limit" }?.value, "50")
        XCTAssertEqual(query.first { $0.name == "StartIndex" }?.value, "0")
        XCTAssertEqual(query.first { $0.name == "SortBy" }?.value, "DateCreated")
        XCTAssertNil(query.first { $0.name == "IsFavorite" })
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"test-token\"") == true)
        let count = requests.all.count
        await store.loadLibrary(kind: .movies, filters: VideoFilters(favoritesOnly: true))
        XCTAssertEqual(store.items, retained)
        XCTAssertEqual(requests.all.count, count)
    }

    func testStandbyArtworkRejectsResponseAfterSignOut() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "user-1", "Name": "member"]) }
            return coreResponse(request, json: page(items: [["Id": "old", "Name": "Old", "Type": "Movie", "ImageTags": ["Primary": "tag"]]]))
        }
        await store.restore()
        let started = expectation(description: "Standby request started")
        let isStandbyRequest: @Sendable (URLRequest) -> Bool = { request in
            guard let url = request.url, url.path == "/Items" else { return false }
            let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "IncludeItemTypes" && $0.value == "Movie,Series" }
                && query.contains { $0.name == "Limit" && $0.value == "50" }
                && query.contains { $0.name == "SortBy" && $0.value == "DateCreated" }
        }
        HomeDelayedURLProtocol.delay = { request in isStandbyRequest(request) ? 0.2 : 0 }
        HomeDelayedURLProtocol.onStart = { request in
            if isStandbyRequest(request) { started.fulfill() }
        }
        let loading = Task { try await store.loadStandbyArtwork() }
        await fulfillment(of: [started], timeout: 2)
        await store.signOut()
        do {
            _ = try await loading.value
            XCTFail("An old member's artwork must not be returned after sign-out")
        } catch {
            XCTAssertEqual(error as? LuminaError, .notSignedIn)
        }
    }

    func testActorTitlesUseThePersonFilterAndReadEveryPageWithoutReplacingLibrary() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume", "/UserViews": return coreResponse(request, json: page(items: []))
            case "/Items":
                if value("PersonIds") == "actor-1" || value("Persons") == "Synthetic Performer" {
                    XCTAssertEqual(value("IncludeItemTypes"), "Movie,Series")
                    XCTAssertEqual(value("Recursive"), "true")
                    XCTAssertNil(value("SearchTerm"), "A title-name search does not filter cast credits.")
                    XCTAssertNil(value("ParentId"))
                    let offset = Int(value("StartIndex") ?? "0") ?? 0
                    let row = offset == 0 ? ["Id": "actor-movie", "Name": "A film", "Type": "Movie"] : ["Id": "actor-series", "Name": "B show", "Type": "Series"]
                    return coreResponse(request, json: ["Items": [row], "TotalRecordCount": 2, "StartIndex": offset])
                }
                return coreResponse(request, json: page(items: [["Id": "library-film", "Name": "Retained library", "Type": "Movie"]]))
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(kind: .movies)
        let retained = store.items
        let person = try JSONDecoder().decode(MediaPerson.self, from: json(["Id": "actor-1", "Name": "Synthetic Performer", "Type": "Actor"]))
        let titles = try await store.titles(featuring: person)
        XCTAssertEqual(titles.map(\.id), ["actor-movie", "actor-series"])
        XCTAssertEqual(store.items, retained)
        let nameOnly = try JSONDecoder().decode(MediaPerson.self, from: json(["Name": "Synthetic Performer", "Type": "Actor"]))
        let fallback = try await store.titles(featuring: nameOnly)
        XCTAssertEqual(fallback.map(\.id), titles.map(\.id))
        XCTAssertEqual(requests.all.filter { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return query.contains { $0.name == "PersonIds" || $0.name == "Persons" }
        }.count, 4)
    }

    func testCollectionsKeepMovieFranchisesPagedAndHouseholdListsOrdered() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        MockURLProtocol.handler = { @Sendable request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in query.first { $0.name == name }?.value }
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/UserViews": return coreResponse(request, json: page(items: [["Id": "playlists", "Name": "Playlists", "Type": "CollectionFolder", "CollectionType": "playlists"]]))
            case "/Items":
                switch (value("IncludeItemTypes"), value("ParentId"), value("StartIndex")) {
                case ("BoxSet", nil, "0"):
                    return coreResponse(request, json: ["Items": [["Id": "boxset-1", "Name": "First collection", "Type": "BoxSet"]], "TotalRecordCount": 2, "StartIndex": 0])
                case ("BoxSet", nil, "1"):
                    return coreResponse(request, json: ["Items": [["Id": "boxset-2", "Name": "Second collection", "Type": "BoxSet"]], "TotalRecordCount": 2, "StartIndex": 1])
                case ("Playlist", "playlists", "0"):
                    return coreResponse(request, json: ["Items": [["Id": "list-1", "Name": "Friday", "Type": "Playlist"]], "TotalRecordCount": 2, "StartIndex": 0])
                case ("Playlist", "playlists", "1"):
                    return coreResponse(request, json: ["Items": [["Id": "list-2", "Name": "Smart picks", "Type": "Playlist"]], "TotalRecordCount": 2, "StartIndex": 1])
                case ("Movie", "boxset-1", _): return coreResponse(request, json: page(items: [["Id": "box-film", "Name": "First film", "Type": "Movie"]]))
                case ("Movie,Episode,Video", "list-1", _): return coreResponse(request, json: page(items: [["Id": "z-first", "Name": "First", "Type": "Movie"], ["Id": "a-second", "Name": "Second", "Type": "Movie"]]))
                default: return coreResponse(request, json: page(items: []))
                }
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()
        await store.loadLibrary(kind: .collections, forceRefresh: true, preloadCatalog: true)
        XCTAssertEqual(store.items.map(\.id), ["boxset-1"])
        XCTAssertEqual(store.householdCollections.map(\.id), ["list-1", "list-2"])
        let choices = try await store.collectionFilterOptions()
        XCTAssertEqual(Set(choices.map(\.id)), Set(["boxset-1", "boxset-2", "list-1", "list-2"]))
        XCTAssertEqual(store.items.map(\.id), ["boxset-1"], "Picker reads must retain the active library wall.")
        await store.loadMore()
        XCTAssertEqual(store.items.map(\.id), ["boxset-1", "boxset-2"])
        let franchiseMovies = try await store.loadChildren(of: store.items[0])
        let playlistTitles = try await store.loadChildren(of: store.householdCollections[0])
        XCTAssertEqual(franchiseMovies.map(\.id), ["box-film"])
        XCTAssertEqual(playlistTitles.map(\.id), ["z-first", "a-second"], "Playlist members must keep the server's manual or rule order.")
    }

    func testCollectionsAuthenticationFailureClearsSession() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        MockURLProtocol.handler = { @Sendable request in
            let itemTypes = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "IncludeItemTypes" }?.value
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume", "/UserViews": return coreResponse(request, json: page(items: []))
            case "/Items": return itemTypes == "BoxSet" ? coreResponse(request, status: 401) : coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }

        await store.restore()
        XCTAssertNotNil(store.session)
        await store.loadLibrary(kind: .collections, forceRefresh: true)

        XCTAssertNil(store.session)
    }

    func testFullLibraryCatalogLoadsAheadAndAlphabetJumpRetainsTheEntireWall() async throws {
        let saved = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return coreResponse(request, json: ["anchors": [["letter": "A", "index": 0], ["letter": "W", "index": 1000]], "total": 1200])
            case "/Items":
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
                let value = { (name: String) in query.first { $0.name == name }?.value }
                guard value("Fields") == "LuminaCatalog" else { return coreResponse(request, json: page(items: [])) }
                let start = Int(value("StartIndex")!)!, limit = Int(value("Limit")!)!
                let rows = (start..<min(1200, start + limit)).map { index in
                    ["Id": "title-\(index)", "Name": "Title \(index)", "Type": "Movie", "ImageTags": ["Primary": "poster"]] as [String: Any]
                }
                return coreResponse(request, json: ["Items": rows, "TotalRecordCount": 1200, "StartIndex": start])
            default: return coreResponse(request, status: 404)
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "user-1", "Name": "member"]) }
            if request.url?.path == "/Lumina/Library/alphabet" {
                return coreResponse(request, json: ["anchors": [["letter": "A", "index": 0]], "total": 3])
            }
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard request.url?.path == "/Items", query.contains(where: { $0.value == "LuminaCatalog" }) else {
                return coreResponse(request, json: page(items: []))
            }
            let start = Int(query.first { $0.name == "StartIndex" }!.value!)!
            let anime = query.contains { $0.name == "LuminaCategory" && $0.value == "anime" }
            return coreResponse(request, json: ["Items": [["Id": anime ? "anime" : "movie-\(start)", "Name": "Title", "Type": "Movie"]], "TotalRecordCount": anime ? 1 : 3, "StartIndex": start])
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "user-1", "Name": "member"]) }
            guard request.url?.path == "/Items", request.url?.query?.contains("LuminaCatalog") == true else {
                return coreResponse(request, json: page(items: []))
            }
            let next = request.url?.query?.contains("StartIndex=1") == true
            if next, attempts.next("next") == 1 { return coreResponse(request, status: 503) }
            return coreResponse(request, json: ["Items": [["Id": next ? "last" : "first", "Name": "Title", "Type": "Movie"]], "TotalRecordCount": 2, "StartIndex": next ? 1 : 0])
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return coreResponse(request, json: ["anchors": [["letter": "A", "index": 0]], "total": 2])
            case "/Items":
                let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
                let start = query.first { $0.name == "StartIndex" }?.value
                var item = movie()
                item["Id"] = start == "0" ? "filtered-1" : "filtered-2"
                return coreResponse(request, json: ["Items": [item], "TotalRecordCount": 2])
            default: return coreResponse(request, status: 404)
            }
        }

        await store.restore()
        let filters = VideoFilters(
            resolution: .ultraHD, hdr: .dolbyVision, audio: .atmos, imaxOnly: true, collectionID: "collection-filter"
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
            XCTAssertEqual(query.first { $0.name == "LuminaCollectionId" }?.value, "collection-filter")
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
        XCTAssertEqual(alphabetQuery?.first { $0.name == "LuminaCollectionId" }?.value, "collection-filter")
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: []))
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Items/Filters2": return coreResponse(request, json: ["LuminaTechnicalFilters": true])
            default: return coreResponse(request, status: 404)
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
            return coreResponse(request, json: [:])
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Lumina/Library/alphabet":
                return coreResponse(request, json: ["anchors": [["letter": "A", "index": 0], ["letter": "M", "index": 50], ["letter": "Z", "index": 200]], "total": 250])
            case "/Items":
                let start = Int(value("StartIndex") ?? "0") ?? 0
                var item = movie()
                item["Id"] = start == 200 ? "zulu" : start == 50 ? "middle" : "alpha"
                return coreResponse(request, json: ["Items": [item], "TotalRecordCount": 250, "StartIndex": start])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/Lumina/Library/alphabet": return coreResponse(request, status: 404)
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/Lumina/Library/alphabet": return coreResponse(request, json: ["anchors": [["letter": "M", "index": 0]], "total": 1])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
        func serve(_ categories: [String], features: [String: Bool]? = nil, status: Int = 200) {
            HomeDelayedURLProtocol.handler = { request in
                switch request.url!.path {
                case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
                case "/Lumina/Settings/navigation":
                    var body: [String: Any] = ["categories": categories]
                    if let features { body["features"] = features }
                    return coreResponse(request, status: status, json: body)
                default: return coreResponse(request, json: page(items: []))
                }
            }
        }
        serve(["movies", "anime", "books"], features: ["requests": true, "streaming": false])
        await store.restore()
        XCTAssertEqual(store.availableLibraryCategories, ["movies", "anime", "books"])
        XCTAssertTrue(store.requestsAvailable)
        XCTAssertFalse(store.streamingAvailable)
        XCTAssertEqual(store.navigationLoadState, .loaded)
        serve([], status: 503)
        await store.refreshNavigationCategories()
        XCTAssertTrue(store.requestsAvailable, "A transient refresh must keep the confirmed Requests destination available.")
        XCTAssertEqual(store.navigationLoadState, .failed)
        serve(["tv"], features: ["requests": false, "streaming": true])
        await store.refreshNavigationCategories()
        XCTAssertEqual(store.availableLibraryCategories, ["tv"])
        XCTAssertFalse(store.requestsAvailable)
        XCTAssertTrue(store.streamingAvailable)
        XCTAssertEqual(store.navigationLoadState, .loaded)
        serve([], status: 503)
        await store.loadHome()
        XCTAssertEqual(store.availableLibraryCategories, ["tv"])
        XCTAssertFalse(store.requestsAvailable)
        XCTAssertTrue(store.streamingAvailable)
        XCTAssertEqual(store.navigationLoadState, .failed)
        serve([], status: 401)
        await store.refreshNavigationCategories()
        XCTAssertTrue(store.availableLibraryCategories.isEmpty)
        XCTAssertFalse(store.requestsAvailable)
        XCTAssertFalse(store.streamingAvailable)
        XCTAssertEqual(store.navigationLoadState, .failed)
        await store.signOut()
        XCTAssertTrue(store.availableLibraryCategories.isEmpty)
        XCTAssertEqual(store.navigationLoadState, .loading)
        credentials.stored = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8766")!, accessToken: "other-token", userID: "user-1", username: "member", serverName: "Other", deviceID: "test-device")
        serve(["music"])
        await store.restore()
        XCTAssertEqual(store.availableLibraryCategories, ["music"], "A new server must never inherit another server's libraries.")
        XCTAssertFalse(store.requestsAvailable, "Older servers without feature flags fail closed.")
        XCTAssertFalse(store.streamingAvailable)
        XCTAssertEqual(store.navigationLoadState, .loaded)
        serve([])
        await store.loadHome()
        XCTAssertTrue(store.availableLibraryCategories.isEmpty, "Removing the final library must clear its category.")
    }

    func testCancelledNavigationRefreshRestoresLastTerminalState() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token", userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Lumina/Settings/navigation": return coreResponse(request, json: ["categories": ["movies"]])
            default: return coreResponse(request, json: page(items: []))
            }
        }
        await store.restore()
        XCTAssertEqual(store.navigationLoadState, .loaded)

        let navigationRequestStarted = expectation(description: "Navigation refresh starts")
        HomeDelayedURLProtocol.delay = { request in
            request.url?.path == "/Lumina/Settings/navigation" ? 0.3 : 0
        }
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.path == "/Lumina/Settings/navigation" { navigationRequestStarted.fulfill() }
        }
        let refresh = Task { await store.refreshNavigationCategories() }
        await fulfillment(of: [navigationRequestStarted], timeout: 1)
        XCTAssertEqual(store.navigationLoadState, .loading)

        refresh.cancel()
        await refresh.value

        XCTAssertEqual(store.navigationLoadState, .loaded)
        XCTAssertEqual(store.availableLibraryCategories, ["movies"])
    }

    func testNavigationRefreshSurvivesConcurrentHomeFallbackGeneration() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        HomeDelayedURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Lumina/Settings/navigation": return coreResponse(request, json: ["categories": ["movies", "music"]])
            default: return coreResponse(request, json: page(items: []))
            }
        }
        await store.restore()
        XCTAssertEqual(store.navigationLoadState, .loaded)

        let navigationStarted = expectation(description: "navigation refresh starts")
        HomeDelayedURLProtocol.delay = { request in
            request.url?.path == "/Lumina/Settings/navigation" ? 0.2 : 0
        }
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.path == "/Lumina/Settings/navigation" { navigationStarted.fulfill() }
        }
        let navigation = Task { await store.refreshNavigationCategories() }
        await fulfillment(of: [navigationStarted], timeout: 1)
        await store.loadHomeFallback(for: [.continueWatching])
        await navigation.value

        XCTAssertEqual(store.availableLibraryCategories, ["movies", "music"])
        XCTAssertEqual(store.navigationLoadState, .loaded,
                       "A current navigation response must survive an unrelated Home generation advance.")
        HomeDelayedURLProtocol.delay = { _ in 0 }
        HomeDelayedURLProtocol.onStart = nil
    }

}
