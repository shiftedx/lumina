import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items":
                var recent = movie()
                recent["Id"] = "recent"
                return coreResponse(request, json: page(items: [recent]))
            case "/UserItems/Resume":
                var resume = movie(positionTicks: 10_000_000)
                resume["Id"] = "resume"
                return coreResponse(request, json: page(items: [resume]))
            case "/Shows/NextUp":
                return coreResponse(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return coreResponse(request, status: 404)
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

    func testCompletedRichHomeRowsAvoidCompatibilityReadsAndKeepLegacyRecentOwnership() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [["Id": "legacy-recent", "Name": "Legacy recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [["Id": "legacy-resume", "Name": "Legacy resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": "legacy-next", "Name": "Legacy next", "Type": "Episode"]]))
            case "/Lumina/Settings/navigation": return coreResponse(request, json: ["categories": ["movies"]])
            default: return coreResponse(request, status: 404)
            }
        }
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        await store.restore()

        let richRows = try JSONDecoder().decode([HomeFeedRow].self, from: json([
            ["id": "continue", "shelf_id": "continue", "title": "Continue watching", "shape": "still",
             "items": [["Id": "rich-resume", "Name": "Rich resume", "Type": "Movie"]]],
            ["id": "next", "shelf_id": "next_up", "title": "Next up", "shape": "still",
             "items": [["Id": "rich-next", "Name": "Rich next", "Type": "Episode"]]],
            ["id": "arrivals", "shelf_id": "new_in_library", "title": "New in your library", "shape": "poster",
             "items": [["Id": "rich-arrival", "Name": "Rich arrival", "Type": "Series"]]],
        ]))
        let expected: Set<HomeShelfID> = [.continueWatching, .nextUp]
        let beforeAdoption = requests.all.count
        let missing = await store.adoptRichHomeRows(richRows, completedShelfIDs: expected,
                                                    expectedCompatibilityShelves: expected)

        XCTAssertTrue(missing.isEmpty)
        XCTAssertEqual(store.resumeItems.map(\.id), ["rich-resume"])
        XCTAssertEqual(store.nextUpItems.map(\.id), ["rich-next"])
        XCTAssertEqual(store.recentItems.map(\.id), ["legacy-recent"],
                       "Balanced rich arrivals must not replace the legacy playable-version recent shelf.")
        await store.loadHomeFallback(for: missing)
        XCTAssertEqual(requests.all.count, beforeAdoption,
                       "A completed warm rich Home response must not reissue compatibility reads.")

        let missingNext = await store.adoptRichHomeRows(
            [richRows[0]], completedShelfIDs: [.continueWatching], expectedCompatibilityShelves: expected
        )
        XCTAssertEqual(missingNext, [.nextUp])
        let beforeFallback = requests.all.count
        await store.loadHomeFallback(for: missingNext)
        let fallbackPaths = requests.all.dropFirst(beforeFallback).compactMap(\.url?.path).filter {
            ["/Items", "/UserItems/Resume", "/Shows/NextUp", "/Lumina/Settings/navigation"].contains($0)
        }
        XCTAssertEqual(Set(fallbackPaths), ["/Shows/NextUp"])
        XCTAssertFalse(fallbackPaths.contains("/Lumina/Settings/navigation"), "Home owns the single navigation refresh.")

        let beforeFailedRefreshFallback = requests.all.count
        let missingAfterFailedRichRefresh = await store.reconcileRichHomeRows(
            richRows, completedShelfIDs: expected, loadSucceeded: false, expectedCompatibilityShelves: expected
        )
        XCTAssertEqual(missingAfterFailedRichRefresh, expected,
                       "A retained rich snapshot must not suppress warm compatibility refresh after its request failed.")
        XCTAssertEqual(store.resumeItems.map(\.id), ["rich-resume"], "Failed reconciliation preserves the visible cached shelf.")
        await store.loadHomeFallback(for: missingAfterFailedRichRefresh)
        let failedRefreshPaths = requests.all.dropFirst(beforeFailedRefreshFallback).compactMap(\.url?.path).filter {
            ["/Items", "/UserItems/Resume", "/Shows/NextUp", "/Lumina/Settings/navigation"].contains($0)
        }
        XCTAssertEqual(Set(failedRefreshPaths), ["/UserItems/Resume", "/Shows/NextUp"])
        XCTAssertFalse(failedRefreshPaths.contains("/Lumina/Settings/navigation"))
    }

    func testHomeFallbackJoinsAnInFlightHomeLoadInsteadOfRestartingIt() async throws {
        let saved = LuminaSession(serverURL: URL(string: "http://127.0.0.1:8765")!, accessToken: "test-token",
                                  userID: "user-1", username: "member", serverName: "Lumina", deviceID: "test-device")
        let store = LuminaStore(client: LuminaClient(configuration: homeConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        HomeDelayedURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()

        let started = expectation(description: "complete Home load starts every compatibility request")
        started.expectedFulfillmentCount = 3
        let startsByPath = PathCounter()
        HomeDelayedURLProtocol.delay = { request in
            ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) ? 0.12 : 0
        }
        HomeDelayedURLProtocol.onStart = { request in
            if ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) {
                _ = startsByPath.next(request.url!.path)
                started.fulfill()
            }
        }
        let home = Task { await store.loadHome() }
        await fulfillment(of: [started], timeout: 1)
        await store.loadHomeFallback(for: [.continueWatching, .nextUp])
        await home.value
        XCTAssertEqual(startsByPath.count(for: "/Items"), 1)
        XCTAssertEqual(startsByPath.count(for: "/UserItems/Resume"), 1)
        XCTAssertEqual(startsByPath.count(for: "/Shows/NextUp"), 1,
                       "Fallback joins the active complete Home load rather than cancelling and duplicating its shelves.")

        let subsetStarted = expectation(description: "subset Home load starts its sole requested shelf")
        let subsetStartsByPath = PathCounter()
        HomeDelayedURLProtocol.onStart = { request in
            if ["/Items", "/UserItems/Resume", "/Shows/NextUp"].contains(request.url?.path) {
                _ = subsetStartsByPath.next(request.url!.path)
                if request.url?.path == "/Items" { subsetStarted.fulfill() }
            }
        }
        let recentOnly = Task { await store.loadHomeFallback(for: [.newInLibrary]) }
        await fulfillment(of: [subsetStarted], timeout: 1)
        await store.loadHomeFallback(for: [.continueWatching])
        await recentOnly.value
        XCTAssertEqual(subsetStartsByPath.count(for: "/Items"), 1)
        XCTAssertEqual(subsetStartsByPath.count(for: "/UserItems/Resume"), 1,
                       "Joining a subset load still requests compatibility shelves it did not cover.")
        XCTAssertEqual(subsetStartsByPath.count(for: "/Shows/NextUp"), 0)
        HomeDelayedURLProtocol.delay = { _ in 0 }
        HomeDelayedURLProtocol.onStart = nil
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [["Id": count == 1 ? "old-recent" : "recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [["Id": count == 1 ? "old-resume" : "resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items" where sort == "DateCreated": return coreResponse(request, status: 404)
            case "/Items": return coreResponse(request, json: page(items: []))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [["Id": "resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": "next", "Name": "Next", "Type": "Episode"]]))
            default: return coreResponse(request, status: 404)
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "one", "Name": "One"]) }
            return coreResponse(request, json: page(items: []))
        }
        await store.restore()
        let started = expectation(description: "The current Library query reached transport")
        started.assertForOverFulfill = true
        HomeDelayedURLProtocol.handler = { request in
            coreResponse(request, json: page(items: [["Id": "current-library", "Name": "Current", "Type": "Movie"]]))
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "user-1", "Name": "Member"]) }
            if request.url?.path == "/Items" {
                let search = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "SearchTerm" }?.value ?? "initial"
                return coreResponse(request, json: page(items: [["Id": search, "Name": search, "Type": "Movie"]]))
            }
            return coreResponse(request, json: page(items: []))
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
            if request.url?.path == "/Users/Me" { return coreResponse(request, json: ["Id": "one", "Name": "One"]) }
            if request.url?.path == "/Items" {
                return coreResponse(request, json: page(items: [["Id": "retained-one", "Name": "Retained", "Type": "Movie"]]))
            }
            return coreResponse(request, json: page(items: []))
        }
        await store.restore()
        XCTAssertEqual(store.items.map(\.id), ["retained-one"])
        let started = expectation(description: "First member search reached transport")
        HomeDelayedURLProtocol.handler = { @Sendable request in
            coreResponse(request, json: page(items: [["Id": "private-one", "Name": "Private", "Type": "Movie"]]))
        }
        HomeDelayedURLProtocol.delay = { @Sendable request in request.url?.path == "/Items" ? 0.3 : 0 }
        HomeDelayedURLProtocol.onStart = { @Sendable request in
            if request.url?.path == "/Items" { started.fulfill() }
        }
        let loading = Task { await store.loadLibrary(search: "first-member") }
        await fulfillment(of: [started], timeout: 2)
        credentials.stored = LuminaSession(serverURL: saved.serverURL, accessToken: saved.accessToken,
            userID: "two", username: "Two", serverName: "Lumina", deviceID: "fixture")
        HomeDelayedURLProtocol.handler = { @Sendable request in coreResponse(request, status: 503, json: [:]) }
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [["Id": count == 1 ? "restore-recent" : "current-recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [["Id": count == 1 ? "restore-resume" : "current-resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": count == 1 ? "restore-next" : "current-next", "Name": "Next", "Type": "Episode"]]))
            default: return coreResponse(request, status: 404)
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
            case "/System/Info/Public": return coreResponse(request, json: ["ServerName": "Lumina"])
            case "/Users/AuthenticateByName": return coreResponse(request, json: ["AccessToken": "new-token", "User": ["Id": "new-user", "Name": "new member", "ServerName": "Lumina"]])
            case "/Users/Me": return coreResponse(request, json: ["Id": token ? "new-user" : "old-user", "Name": token ? "new member" : "old member"])
            case "/Sessions/Logout": return coreResponse(request, status: 204)
            case "/Items": return coreResponse(request, json: page(items: [["Id": token ? "new-recent" : "old-recent", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: [["Id": token ? "new-resume" : "old-resume", "Name": "Resume", "Type": "Movie"]]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": token ? "new-next" : "old-next", "Name": "Next", "Type": "Episode"]]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items" where sort == "SortName":
                return coreResponse(request, json: page(items: [["Id": "catalog-\(count)", "Name": "Catalog", "Type": "Movie"]]))
            case "/Items":
                return coreResponse(request, json: page(items: [["Id": "recent-\(count)", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
                if sort == "SortName" { return coreResponse(request, status: 404) }
                return coreResponse(request, json: page(items: [["Id": "recent-after-error", "Name": "Recent", "Type": "Movie"]]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/album-1":
                return coreResponse(request, json: ["Id": "album-1", "Name": "Album", "Type": "MusicAlbum"])
            case "/Users/user-1/Items/\(compactAlbumID)":
                return coreResponse(request, json: ["Id": compactAlbumID, "Name": "UUID Album", "Type": "MusicAlbum"])
            case "/Users/user-1/Items/artist-1":
                return coreResponse(request, json: ["Id": "artist-1", "Name": "Artist", "Type": "MusicArtist"])
            case "/Users/user-1/Items/wrong-type":
                return coreResponse(request, json: ["Id": "wrong-type", "Name": "Artist", "Type": "MusicArtist"])
            case "/Users/user-1/Items/wrong-id":
                return coreResponse(request, json: ["Id": "another-id", "Name": "Album", "Type": "MusicAlbum"])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/series-1": return coreResponse(request, json: ["Id": "series-1", "Name": "Series", "Type": "Series"])
            case "/Users/user-1/Items/wrong-type": return coreResponse(request, json: ["Id": "wrong-type", "Name": "Movie", "Type": "Movie"])
            case "/Users/user-1/Items/wrong-id": return coreResponse(request, json: ["Id": "another-id", "Name": "Series", "Type": "Series"])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/\(vaultMovieID)": return coreResponse(request, json: ["Id": vaultMovieResponseID, "Name": "Vault movie", "Type": "Movie"])
            case "/Users/user-1/Items/vault-series": return coreResponse(request, json: ["Id": "vault-series", "Name": "Vault series", "Type": "Series"])
            case "/Users/user-1/Items/\(mismatchedVaultID)": return coreResponse(request, json: ["Id": "1A5D6533-B044-45A3-BE3B-71FC48705696", "Name": "Wrong", "Type": "Movie"])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/track-1": return coreResponse(request, json: ["Id": "track-1", "Name": "Song", "Type": "Audio", "Album": "Album", "AlbumId": "album-1", "AlbumPrimaryImageTag": "album-art", "Artists": ["Artist"], "IndexNumber": 1, "ParentIndexNumber": 2])
            case "/Items":
                if value("IncludeItemTypes") == "MusicAlbum" {
                    return coreResponse(request, json: page(items: [["Id": "album-1", "Name": "Album", "Type": "MusicAlbum", "AlbumArtist": "Artist"]]))
                }
                if value("IncludeItemTypes") == "Audio" {
                    let start = value("StartIndex") ?? "0"
                    return coreResponse(request, json: ["Items": [["Id": start == "0" ? "track-1" : "track-2", "Name": "Song", "Type": "Audio"]], "TotalRecordCount": 2])
                }
                var item = movie()
                if value("SearchTerm") != nil { item["Id"] = "search-result" }
                return coreResponse(request, json: page(items: [item]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/UserViews": return coreResponse(request, json: page(items: [
                ["Id": "shows-view", "Name": "Shows", "Type": "CollectionFolder", "CollectionType": "tvshows"],
                ["Id": "anime-view", "Name": "Anime", "Type": "CollectionFolder", "CollectionType": "tvshows"],
            ]))
            case "/Shows/series-1/Seasons": return coreResponse(request, json: page(items: [
                ["Id": "season-2", "Name": "Season 2", "Type": "Season", "IndexNumber": 2],
                ["Id": "season-1", "Name": "Season 1", "Type": "Season", "IndexNumber": 1],
            ]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: [["Id": "next-episode", "Name": "Next", "Type": "Episode", "SeriesId": "series-1", "SeasonId": "season-1"]]))
            case "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Items":
                if value("ParentId") == "shows-view" {
                    return coreResponse(request, json: ["Items": [["Id": value("StartIndex") == "0" ? "series-1" : "series-2", "Name": "Show", "Type": "Series"]], "TotalRecordCount": 2])
                }
                if value("ParentId") == "season-1" {
                    return coreResponse(request, json: ["Items": [["Id": value("StartIndex") == "0" ? "episode-2" : "episode-1", "Name": "Episode", "Type": "Episode", "IndexNumber": value("StartIndex") == "0" ? 2 : 1]], "TotalRecordCount": 2])
                }
                return coreResponse(request, json: page(items: [movie()]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Items":
                switch value("LuminaCategory") {
                case "anime": return coreResponse(request, json: page(items: [
                    ["Id": "anime-film", "Name": "Anime Film", "Type": "Movie"],
                    ["Id": "anime-series", "Name": "Anime Series", "Type": "Series"],
                ]))
                case "movies": return coreResponse(request, json: page(items: [["Id": "movie", "Name": "Movie", "Type": "Movie"]]))
                default: return coreResponse(request, json: page(items: [movie()]))
                }
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Lumina/Library/alphabet": return coreResponse(request, json: ["anchors": [], "total": 1])
            case "/Items":
                if search == "signal" {
                    return coreResponse(request, json: page(items: [
                        ["Id": "signal-movie", "Name": "Signal", "Type": "Movie"],
                        ["Id": "signal-album", "Name": "Signal", "Type": "MusicAlbum"],
                    ]))
                }
                return coreResponse(request, json: page(items: [["Id": "library-movie", "Name": "Library", "Type": "Movie"]]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/UserViews": return coreResponse(request, json: page(items: [["Id": "channels-view", "Name": "Channels", "Type": "CollectionFolder"]]))
            case "/Shows/NextUp", "/UserItems/Resume": return coreResponse(request, json: page(items: []))
            case "/Items":
                if value("ParentId") == "channels-view" {
                    return coreResponse(request, json: page(items: [["Id": "saved-youtube-video", "Name": "Saved video", "Type": "Episode"]]))
                }
                return coreResponse(request, json: page(items: [movie()]))
            default: return coreResponse(request, status: 404)
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

}
