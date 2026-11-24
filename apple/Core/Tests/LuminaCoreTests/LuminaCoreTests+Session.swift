import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
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
        MockURLProtocol.handler = { request in requests.append(request); return coreResponse(request) }
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
        MockURLProtocol.handler = { request in coreResponse(request, status: 401) }

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
            return coreResponse(request, json: ["ServerName": "Lumina"])
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
            case "/System/Info/Public": return coreResponse(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return coreResponse(request, json: ["AccessToken": "new-token", "User": ["Id": "user-1", "Name": "member", "ServerName": "Lumina"]])
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
                return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": saved.userID, "Name": saved.username])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Items/episode-2/Download": return coreResponse(request, status: 403)
            case "/Items/episode-1/Download", "/Items/episode-3/Download":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                    headerFields: ["Content-Type": "video/mp4"])!, Data(repeating: 0, count: 64))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Items/movie-1/Download":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "video/x-matroska"])!, Data(repeating: 0, count: 64))
            case "/Items/movie-1/Images/Primary":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "image/jpeg"])!, Data(repeating: 0, count: 32))
            default: return coreResponse(request, status: 404)
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

    func testSavedStreamingLibraryPagesProvidersSeparatelyAndDiscardsFormerOwnerResponse() async throws {
        let first = LuminaSession(serverURL: URL(string: "https://first.example")!, accessToken: "first-token",
                                  userID: "first", username: "First", serverName: "Lumina", deviceID: "fixture")
        let second = LuminaSession(serverURL: URL(string: "https://second.example")!, accessToken: "second-token",
                                   userID: "second", username: "Second", serverName: "Lumina", deviceID: "fixture")
        let normalLibrary = LuminaStore(client: LuminaClient(configuration: mockConfiguration()),
                                        credentials: TestCredentials(session: first))
        MockURLProtocol.handler = { request in
            switch request.url?.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "first", "Name": "First"])
            case "/Items": return coreResponse(request, json: page(items: [["Id": "normal-library", "Name": "Movie", "Type": "Movie"]]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }
        await normalLibrary.restore()
        XCTAssertEqual(normalLibrary.items.map(\.id), ["normal-library"])

        defer { HomeDelayedURLProtocol.reset() }
        let requests = RequestRecorder()
        let staleRequestStarted = expectation(description: "former owner saved-streaming request started")
        HomeDelayedURLProtocol.handler = { request in
            requests.append(request)
            switch request.url?.path {
            case "/UserViews":
                let owner = request.url?.host == "first.example" ? "first" : "second"
                return coreResponse(request, json: page(items: [["Id": "channels-\(owner)", "Name": "Channels", "Type": "Channel"]]))
            case "/Items":
                let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
                let start = components?.queryItems?.first(where: { $0.name == "StartIndex" })?.value
                let provider = components?.queryItems?.first(where: { $0.name == "SourceProvider" })?.value
                XCTAssertEqual(provider, "youtube,twitch,kick")
                if request.url?.host == "first.example" {
                    return coreResponse(request, json: ["Items": [["Id": "former-owner", "Name": "Former", "Type": "Episode"]], "TotalRecordCount": 1, "StartIndex": 0])
                }
                if start == "0" {
                    return coreResponse(request, json: ["Items": [["Id": "youtube-saved", "Name": "YouTube", "Type": "Episode"]], "TotalRecordCount": 2, "StartIndex": 0])
                }
                return coreResponse(request, json: ["Items": [["Id": "twitch-saved", "Name": "Twitch", "Type": "Episode"]], "TotalRecordCount": 2, "StartIndex": 1])
            default: return coreResponse(request, status: 404)
            }
        }
        HomeDelayedURLProtocol.delay = { request in
            request.url?.host == "first.example" && request.url?.path == "/Items" ? 0.15 : 0
        }
        HomeDelayedURLProtocol.onStart = { request in
            if request.url?.host == "first.example", request.url?.path == "/Items" { staleRequestStarted.fulfill() }
        }

        let streamingLibrary = LuminaStreamingLibrary(client: LuminaClient(configuration: homeConfiguration()), session: first)
        let formerOwnerLoad = Task { await streamingLibrary.load() }
        await fulfillment(of: [staleRequestStarted], timeout: 2)
        streamingLibrary.configure(session: second)
        await formerOwnerLoad.value
        XCTAssertTrue(streamingLibrary.items.isEmpty, "A former member's saved videos must not publish after an owner switch.")

        await streamingLibrary.load()
        XCTAssertEqual(streamingLibrary.items.map(\.id), ["youtube-saved"])
        XCTAssertTrue(streamingLibrary.hasMore)
        await streamingLibrary.loadMore()
        XCTAssertEqual(streamingLibrary.items.map(\.id), ["youtube-saved", "twitch-saved"])
        XCTAssertFalse(streamingLibrary.hasMore)
        XCTAssertEqual(normalLibrary.items.map(\.id), ["normal-library"], "Saved-streaming paging must not replace the normal Library wall.")
        XCTAssertTrue(requests.all.filter { $0.url?.path == "/Items" }.allSatisfy { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "ParentId" })?.value?.hasPrefix("channels-") == true
        })
    }

    func testNativeStreamingFailsClosedAndCleansRemotePlaybackWithOriginalOwner() async throws {
        let first = LuminaSession(serverURL: URL(string: "https://lumina.example")!, accessToken: "first-token",
                                  userID: "first", username: "First", serverName: "Lumina", deviceID: "fixture")
        let second = LuminaSession(serverURL: URL(string: "https://second.example")!, accessToken: "second-token",
                                   userID: "second", username: "Second", serverName: "Lumina", deviceID: "fixture")
        let credentials = TestCredentials(session: first)
        let requests = RequestRecorder()
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: credentials)
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url?.path {
            case "/Users/Me":
                let secondMember = request.value(forHTTPHeaderField: "Authorization")?.contains("second-token") == true
                return coreResponse(request, json: ["Id": secondMember ? "second" : "first", "Name": secondMember ? "Second" : "First"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()

        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url?.path {
            case "/Lumina/Settings/capabilities": return coreResponse(request, json: ["streaming": ["saved_library": [:]]])
            default: return coreResponse(request, status: 404)
            }
        }
        let malformedCapabilities = await store.loadNativeStreamingCapabilities()
        XCTAssertNil(malformedCapabilities)
        XCTAssertNil(store.nativeStreamingCapabilities, "Absent or malformed remote capability fields must fail closed.")

        MockURLProtocol.handler = { request in
            requests.append(request)
            guard request.url?.path == "/Lumina/Streaming/playback" else { return coreResponse(request, status: 404) }
            return coreResponse(request, json: [
                "status": "ready", "session_id": "stream-foreign", "playback_url": "https://foreign.example/stream.m3u8",
                "seekable": true, "live": false,
            ])
        }
        do {
            _ = try await store.prepareNativeRemotePlayback(sourceURL: URL(string: "https://provider.example/watch?v=1")!)
            XCTFail("A server-provided foreign playback URL must be rejected.")
        } catch {
            XCTAssertEqual(error as? LuminaError, .malformedResponse)
        }

        let playbackStarted = expectation(description: "remote playback started under original owner")
        let releasePlayback = DispatchSemaphore(value: 0)
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url?.path {
            case "/Lumina/Streaming/playback":
                playbackStarted.fulfill()
                releasePlayback.wait()
                return coreResponse(request, json: [
                    "status": "ready", "session_id": "stream-1", "playback_url": "/Lumina/Streaming/playback/stream-1/master.m3u8",
                    "seekable": true, "live": false,
                ])
            case "/Lumina/Streaming/playback/stream-1": return coreResponse(request)
            case "/Users/Me":
                let secondMember = request.value(forHTTPHeaderField: "Authorization")?.contains("second-token") == true
                return coreResponse(request, json: ["Id": secondMember ? "second" : "first", "Name": secondMember ? "Second" : "First"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }
        let preparation = Task { @MainActor in
            try await store.prepareNativeRemotePlayback(sourceURL: URL(string: "https://provider.example/watch?v=1")!)
        }
        await fulfillment(of: [playbackStarted], timeout: 2)
        credentials.stored = second
        let restoration = Task { @MainActor in await store.restore() }
        await Task.yield()
        XCTAssertEqual(store.session?.accessToken, "second-token")
        releasePlayback.signal()
        await restoration.value
        do {
            _ = try await preparation.value
            XCTFail("Playback created for a former owner must not be returned to the next member.")
        } catch {
            XCTAssertEqual(error as? LuminaError, .notSignedIn)
        }
        let cleanup = try XCTUnwrap(requests.all.last { $0.url?.path == "/Lumina/Streaming/playback/stream-1" })
        XCTAssertTrue(cleanup.value(forHTTPHeaderField: "Authorization")?.contains("first-token") == true,
                      "Remote playback cleanup must use the session that created it, not the current member.")
    }
}
