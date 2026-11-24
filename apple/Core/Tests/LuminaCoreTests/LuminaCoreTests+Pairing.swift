import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
    func testEpisodeDetailDecodesOnlyAvailableSourceMetadata() throws {
        var payload = movie()
        payload["Type"] = "Episode"
        payload["PremiereDate"] = "2026-10-14T00:00:00Z"
        payload["MediaStreams"] = [
            ["Type": "Video", "Height": 1080, "IsInterlaced": false, "VideoRangeType": "HDR10"],
            ["Type": "Audio", "Codec": "eac3", "DisplayTitle": "E-AC3 5.1", "Channels": 6, "IsDefault": true],
        ]
        let episode = try JSONDecoder().decode(MediaItem.self, from: json(payload))
        XCTAssertEqual(episode.premiereDate, "2026-10-14T00:00:00Z")
        XCTAssertEqual(episode.mediaStreams?.first?.height, 1080)
        XCTAssertEqual(episode.mediaStreams?.first?.isInterlaced, false)
        XCTAssertEqual(episode.mediaStreams?.first?.videoRangeType, "HDR10")
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

    func testTVPairingApprovalLinkOnlyAcceptsBoundedExpectedDeepLinks() throws {
        let payload = """
        {"type":"lumina-tv-pair","version":1,"server_url":"https://lumina.example:8443","server_id":"server-1","code":"ABCDEFGH"}
        """
        let accepted = try TVPairingApprovalLink(scannedCode: payload)
        XCTAssertEqual(accepted.serverURL.absoluteString, "https://lumina.example:8443")
        XCTAssertEqual(accepted.code, "ABCDEFGH")
        var link = URLComponents()
        link.scheme = "lumina"
        link.host = "approve-tv"
        link.fragment = payload
        XCTAssertEqual(try TVPairingApprovalLink(deepLink: XCTUnwrap(link.url)), accepted)
        XCTAssertThrowsError(try TVPairingApprovalLink(deepLink: URL(string: "lumina://approve-tv?code=ABCDEFGH#x")!))
        XCTAssertThrowsError(try TVPairingApprovalLink(scannedCode: "{\"type\":\"lumina-tv-pair\",\"version\":1,\"server_url\":\"https://lumina.example/path\",\"server_id\":\"server-1\",\"code\":\"ABCDEFGH\"}"))
        XCTAssertThrowsError(try TVPairingApprovalLink(scannedCode: "{\"type\":\"lumina-tv-pair\",\"version\":1,\"server_url\":\"https://lumina.example\",\"server_id\":\"server-1\",\"code\":\"O0IL1234\"}"))
    }

    func testTVPairingHandoffAcceptsOnlyTheNearbyQRShape() throws {
        let handoff = TVPairingHandoff()
        XCTAssertEqual(try TVPairingHandoff(link: handoff.link), handoff)
        var link = try XCTUnwrap(URLComponents(url: handoff.link, resolvingAgainstBaseURL: false))
        link.queryItems = [URLQueryItem(name: "server", value: "https://other.example")]
        XCTAssertThrowsError(try TVPairingHandoff(link: XCTUnwrap(link.url)))
        link.query = nil
        link.fragment = "short"
        XCTAssertThrowsError(try TVPairingHandoff(link: XCTUnwrap(link.url)))
    }

    func testTVApprovalNeverUsesScannedOriginOrChangesPhoneSession() async throws {
        let active = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "phone-token",
                                   userID: "member-1", username: "member", serverName: "Lumina", deviceID: "phone-device")
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "member-1", "Name": "member"])
            case "/Lumina/DevicePairing/Inspect":
                return coreResponse(request, json: ["code": "ABCDEFGH", "device_name": "Living room TV",
                                                "expires_at": "2026-11-02T12:00:00.123456Z", "server_id": "server-1", "status": "pending"])
            case "/Lumina/DevicePairing/Approve": return coreResponse(request, json: ["status": "approved"])
            case "/Items", "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: active))
        await store.restore()
        let scanned = try TVPairingApprovalLink(scannedCode: """
        {"type":"lumina-tv-pair","version":1,"server_url":"https://elsewhere.example","server_id":"server-1","code":"ABCDEFGH"}
        """)

        let inspection = try await store.inspectTVPairing(code: scanned.code)
        try await store.approveTVPairing(code: inspection.code)

        let managed = requests.all.filter { $0.url?.path.hasPrefix("/Lumina/DevicePairing/") == true }
        XCTAssertEqual(managed.map { $0.url?.host }, ["127.0.0.1", "127.0.0.1"])
        XCTAssertTrue(managed.allSatisfy { $0.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"phone-token\"") == true })
        XCTAssertEqual(store.session?.serverURL, active.serverURL)
        XCTAssertEqual(store.session?.accessToken, active.accessToken)
    }

    func testTVPairingClaimsIntoNormalValidatedSession() async throws {
        let request = try JSONDecoder().decode(TVPairingRequest.self, from: json([
            "id": "00000000-0000-0000-0000-000000000001", "device_secret": String(repeating: "s", count: 32),
            "code": "ABCDEFGH", "expires_at": "2026-11-02T12:00:00.123456Z", "poll_interval_seconds": 3, "server_id": "server-1",
        ]))
        let credentials = TestCredentials()
        let requests = RequestRecorder()
        MockURLProtocol.handler = { http in
            requests.append(http)
            switch http.url!.path {
            case "/Lumina/DevicePairing/Claim": return coreResponse(http, json: ["username": "member", "password": "one-use-password"])
            case "/System/Info/Public": return coreResponse(http, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return coreResponse(http, json: ["AccessToken": "tv-token", "User": ["Id": "member-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return coreResponse(http, json: ["Id": "member-1", "Name": "member"])
            case "/Items", "/Shows/NextUp", "/UserItems/Resume": return coreResponse(http, json: page(items: []))
            default: return coreResponse(http, status: 404)
            }
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)

        try await store.completeTVPairing(server: "http://127.0.0.1:8765", request: request)

        let claim = try XCTUnwrap(requests.all.first { $0.url?.path == "/Lumina/DevicePairing/Claim" })
        XCTAssertEqual(try body(claim)["device_secret"] as? String, request.deviceSecret)
        let signIn = try XCTUnwrap(requests.all.first { $0.url?.path == "/Users/AuthenticateByName" })
        XCTAssertEqual(try body(signIn)["Pw"] as? String, "one-use-password")
        XCTAssertEqual(store.session?.accessToken, "tv-token")
        XCTAssertEqual(credentials.stored?.accessToken, "tv-token")
    }

    func testCancelledTVPairingDoesNotClaimOrPersistCredentials() async throws {
        let request = try JSONDecoder().decode(TVPairingRequest.self, from: json([
            "id": "00000000-0000-0000-0000-000000000001", "device_secret": String(repeating: "s", count: 32),
            "code": "ABCDEFGH", "expires_at": "2026-11-02T12:00:00.123456Z", "poll_interval_seconds": 3, "server_id": "server-1",
        ]))
        let credentials = TestCredentials()
        let calls = RequestRecorder()
        MockURLProtocol.handler = { http in
            calls.append(http)
            return coreResponse(http, status: 500)
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        let task = Task { @MainActor in
            try await store.completeTVPairing(server: "http://127.0.0.1:8765", request: request)
        }
        task.cancel()

        do {
            try await task.value
            XCTFail("A cancelled enrollment must not continue to claim credentials.")
        } catch is CancellationError { }
        XCTAssertTrue(calls.all.isEmpty)
        XCTAssertNil(credentials.stored)
        XCTAssertNil(store.session)
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
                return coreResponse(request, json: ["username": "member", "password": "app-password", "server_url": "http://127.0.0.1:8765"])
            case "/System/Info/Public": return coreResponse(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return coreResponse(request, json: ["AccessToken": "paired-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
        MockURLProtocol.handler = { @Sendable request in
            requests.append(request)
            return coreResponse(request, json: ["username": "member", "password": "app-password", "server_url": "https://elsewhere.example"])
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                Thread.sleep(forTimeInterval: 0.05)
                return coreResponse(request, json: movie())
            default: return coreResponse(request, status: 404)
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
        MockURLProtocol.handler = { request in coreResponse(request, status: 401) }
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return coreResponse(request, json: page(items: [movie(positionTicks: 80_000_000)]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            case "/Shows/show/Seasons": return coreResponse(request, json: page(items: [["Id": "season", "Name": "Season 1", "Type": "Season", "IndexNumber": 1]]))
            default: return coreResponse(request, status: 404)
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "user-1", "Name": "member"]) }
            return coreResponse(request, status: 503)
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

}
