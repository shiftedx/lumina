import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaOfflineLibraryTests: XCTestCase {
    private var root: URL!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-offline-tests-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        OfflineURLProtocol.stub.reset()
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
        OfflineURLProtocol.stub.reset()
    }

    func testDownloadsPersistWithoutCredentialsAndStayOwnerScopedAfterPurge() async throws {
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 32) }
        let owner = session()
        let track = try item("one")
        let library = library()
        library.configure(session: owner)
        var authenticated = request("one")
        authenticated.url = URL(string: "https://offline.example/Audio/one/stream")
        authenticated.setValue(nil, forHTTPHeaderField: "X-Emby-Token")
        authenticated.setValue("MediaBrowser Client=\"Offline proof\", Token=\"discard-token\"", forHTTPHeaderField: "Authorization")
        await library.download(item: track, request: authenticated)
        XCTAssertEqual(library.downloadStates[track.id], .downloaded)
        XCTAssertEqual(library.usedBytes, 32)
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 6, mime: "image/jpeg") }
        var artworkRequest = request("one")
        artworkRequest.url = URL(string: "https://offline.example/Items/one/Images/Primary?ApiKey=discard-token")
        await library.downloadArtwork(item: track, request: artworkRequest)
        let artwork = try XCTUnwrap(library.artworkURL(for: track.id))
        XCTAssertEqual(artwork.pathExtension, "jpg")
        XCTAssertEqual(library.usedBytes, 38)
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 32) }
        let local = try XCTUnwrap(library.localURL(for: track.id))
        XCTAssertEqual(local.pathExtension, "m4a")
        XCTAssertEqual(library.downloadedItems.map(\.name), ["Synthetic track one"])
        let manifest = local.deletingLastPathComponent().appendingPathComponent("manifest.json")
        let persisted = try String(contentsOf: manifest, encoding: .utf8)
        XCTAssertFalse(persisted.contains("discard-token"))
        XCTAssertFalse(persisted.contains("https://"))
        XCTAssertFalse(persisted.contains("ApiKey"))
        XCTAssertFalse(local.path.contains("offline.example"))
        XCTAssertEqual(try local.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)

        library.configure(session: session(user: "another-member"))
        XCTAssertTrue(library.downloadedItems.isEmpty)
        XCTAssertNil(library.localURL(for: track.id))
        library.configure(session: session(host: "another.example"))
        XCTAssertTrue(library.downloadedItems.isEmpty)
        library.configure(session: nil)
        XCTAssertTrue(library.downloadedItems.isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: local.path), "Hiding a session must not delete its downloads")

        let restored = self.library()
        restored.configure(session: owner)
        XCTAssertEqual(restored.localURL(for: track.id), local)
        XCTAssertEqual(restored.usedBytes, 38)
        XCTAssertEqual(restored.artworkURL(for: track.id), artwork)
        try FileManager.default.removeItem(at: local)
        restored.refresh()
        XCTAssertNil(restored.localURL(for: track.id))
        XCTAssertTrue(restored.downloadedItems.isEmpty)
        XCTAssertEqual(restored.usedBytes, 0)
        await restored.download(item: track, request: request("one"))
        XCTAssertNotNil(restored.localURL(for: track.id), "A purged track can be explicitly retried")
        restored.configure(session: session(user: "another-member"))
        restored.clear(session: owner)
        restored.configure(session: owner)
        XCTAssertTrue(restored.downloadedItems.isEmpty, "Explicit sign-out cleanup removes only the requested owner's files")
    }

    func testBudgetAndTrackCapsDoNotEvictAndAllowExplicitRemoveClearAndRetry() async throws {
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 32) }
        let library = library(limit: 16)
        library.configure(session: session())
        let first = try item("one")
        await library.download(item: first, request: request("one"))
        guard case .failed = library.downloadStates[first.id] else { return XCTFail("Oversized download was not rejected") }
        XCTAssertNil(library.localURL(for: first.id))
        XCTAssertEqual(library.usedBytes, 0)

        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 8) }
        await library.download(item: first, request: request("one"))
        let retained = try XCTUnwrap(library.localURL(for: first.id))
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 12) }
        let second = try item("two")
        await library.download(item: second, request: request("two"))
        XCTAssertEqual(library.localURL(for: first.id), retained, "The budget must not silently evict another saved track")
        XCTAssertNil(library.localURL(for: second.id))
        XCTAssertEqual(library.usedBytes, 8)
        library.remove(id: first.id)
        await library.download(item: second, request: request("two"))
        XCTAssertEqual(library.usedBytes, 12)
        library.clear()
        XCTAssertTrue(library.downloadedItems.isEmpty)
        XCTAssertEqual(library.usedBytes, 0)

        let capped = self.library(limit: LuminaOfflineLibrary.maximumTrackBytes * 2)
        capped.configure(session: session())
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 1, expected: LuminaOfflineLibrary.maximumTrackBytes + 1) }
        await capped.download(item: first, request: request("one"))
        guard case .failed(let message) = capped.downloadStates[first.id] else { return XCTFail("The per-track cap was not enforced") }
        XCTAssertTrue(message.contains("128 MiB"))
        XCTAssertNil(capped.localURL(for: first.id))
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 8) }
        await capped.download(item: first, request: request("one"))
        OfflineURLProtocol.stub.handler = { $0.respond(bytes: 1, expected: LuminaOfflineLibrary.maximumArtworkBytes + 1, mime: "image/jpeg") }
        await capped.downloadArtwork(item: first, request: request("one"))
        XCTAssertNil(capped.artworkURL(for: first.id))
        XCTAssertNotNil(capped.localURL(for: first.id), "An oversized cover must not discard the saved audio")
        XCTAssertEqual(capped.downloadStates[first.id], .downloaded)
        XCTAssertEqual(capped.usedBytes, 8)
    }

    func testDownloadsAreSequentialAndBothQueuedAndActiveCancellationReleaseTheQueue() async throws {
        OfflineURLProtocol.stub.handler = { protocolTask in
            if protocolTask.request.url?.path.contains("one") == true {
                protocolTask.begin(expected: 32)
            } else { protocolTask.respond(bytes: 4) }
        }
        let library = library()
        library.configure(session: session())
        let first = try item("one"), second = try item("two"), third = try item("three")
        let firstDownload = Task { await library.download(item: first, request: request("one")) }
        try await waitUntil { OfflineURLProtocol.stub.requestCount == 1 }
        let secondDownload = Task { await library.download(item: second, request: request("two")) }
        let thirdDownload = Task { await library.download(item: third, request: request("three")) }
        try await waitUntil { library.downloadStates[second.id] == .queued && library.downloadStates[third.id] == .queued }
        XCTAssertEqual(OfflineURLProtocol.stub.requestCount, 1)
        XCTAssertEqual(Set(library.pendingItems.map(\.id)), Set([first.id, second.id, third.id]))
        library.cancel(id: third.id)
        await thirdDownload.value
        XCTAssertEqual(library.downloadStates[third.id], .cancelled)
        firstDownload.cancel()
        await firstDownload.value
        await secondDownload.value
        XCTAssertEqual(library.downloadStates[first.id], .cancelled)
        XCTAssertEqual(library.downloadStates[second.id], .downloaded)
        XCTAssertEqual(OfflineURLProtocol.stub.requestCount, 2)
        XCTAssertNil(library.localURL(for: first.id))
        XCTAssertNotNil(library.localURL(for: second.id))
        XCTAssertEqual(library.usedBytes, 4)
        XCTAssertEqual(Set(library.pendingItems.map(\.id)), Set([first.id, third.id]))

        OfflineURLProtocol.stub.handler = { protocolTask in
            if protocolTask.request.value(forHTTPHeaderField: "X-Emby-Token") == "discard-token" {
                protocolTask.begin(expected: 32)
            } else { protocolTask.respond(bytes: 4) }
        }
        let before = OfflineURLProtocol.stub.requestCount
        let oldOwnerDownload = Task { await library.download(item: third, request: request("three")) }
        try await waitUntil { OfflineURLProtocol.stub.requestCount == before + 1 }
        library.configure(session: session(user: "another-member", token: "another-token"))
        var newOwnerRequest = request("three")
        newOwnerRequest.url = URL(string: "https://offline.example/Audio/three/stream?ApiKey=another-token")
        newOwnerRequest.setValue("another-token", forHTTPHeaderField: "X-Emby-Token")
        await library.download(item: third, request: newOwnerRequest)
        await oldOwnerDownload.value
        XCTAssertNotNil(library.localURL(for: third.id), "The new owner can download the same item ID after cancelling the previous owner")
        library.configure(session: session())
        XCTAssertNil(library.localURL(for: third.id), "An old-owner completion must not publish into either member's manifest")
        XCTAssertNotNil(library.localURL(for: second.id))
    }

    func testUnsafeInitialOriginIsRejectedBeforeCredentialsLeaveTheDevice() async throws {
        let library = library()
        library.configure(session: session())
        let track = try item("one")
        await library.download(item: track, request: URLRequest(url: URL(string: "https://another.example/audio?ApiKey=discard-token")!))
        guard case .failed = library.downloadStates[track.id] else { return XCTFail("Cross-origin request was accepted") }
        XCTAssertEqual(OfflineURLProtocol.stub.requestCount, 0)
        library.configure(session: session(user: "another-member", token: "another-token"))
        await library.download(item: track, request: request("one"))
        guard case .failed = library.downloadStates[track.id] else { return XCTFail("A stale prior-member request was accepted") }
        XCTAssertEqual(OfflineURLProtocol.stub.requestCount, 0)
        XCTAssertTrue(library.pendingItems.isEmpty, "A stale member request must not leak item metadata into the current owner's list")
    }

    func testSameOriginRedirectsWorkAndCrossOriginRedirectsCannotForwardCredentials() async throws {
        OfflineURLProtocol.stub.handler = { protocolTask in
            if protocolTask.request.url?.path == "/same" {
                protocolTask.redirect(to: URL(string: "https://offline.example/complete")!)
            } else if protocolTask.request.url?.path == "/other" {
                protocolTask.redirect(to: URL(string: "https://another.example/complete")!)
            } else { protocolTask.respond(bytes: 4) }
        }
        let library = library()
        library.configure(session: session())
        let first = try item("one"), second = try item("two")
        var same = request("one")
        same.url = URL(string: "https://offline.example/same?ApiKey=discard-token")
        await library.download(item: first, request: same)
        XCTAssertNotNil(library.localURL(for: first.id))
        let before = OfflineURLProtocol.stub.requestCount
        var other = request("two")
        other.url = URL(string: "https://offline.example/other?ApiKey=discard-token")
        await library.download(item: second, request: other)
        XCTAssertNil(library.localURL(for: second.id))
        XCTAssertEqual(OfflineURLProtocol.stub.requestCount, before + 1, "No request may reach the redirect's other origin")
        guard case .failed(let message) = library.downloadStates[second.id] else { return XCTFail("Cross-origin redirect was accepted") }
        XCTAssertTrue(message.contains("another server"))
    }

    private func library(limit: Int64 = 1_024) -> LuminaOfflineLibrary {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [OfflineURLProtocol.self]
        config.timeoutIntervalForResource = 3
        return LuminaOfflineLibrary(root: root, limitBytes: limit, configuration: config)
    }

    private func session(user: String = "member", host: String = "offline.example", token: String = "discard-token") -> LuminaSession {
        LuminaSession(serverURL: URL(string: "https://\(host)")!, accessToken: token, userID: user,
                      username: "Synthetic member", serverName: "Synthetic server", deviceID: "offline-test")
    }

    private func item(_ id: String) throws -> MediaItem {
        try JSONDecoder().decode(MediaItem.self, from: JSONSerialization.data(withJSONObject: [
            "Id": id, "Name": "Synthetic track \(id)", "Type": "Audio", "Container": "m4a",
        ]))
    }

    private func request(_ id: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://offline.example/Audio/\(id)/stream?ApiKey=discard-token")!)
        request.setValue("discard-token", forHTTPHeaderField: "X-Emby-Token")
        return request
    }

    private func waitUntil(_ condition: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(3)
        while !condition() && Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(condition())
    }
}

private final class OfflineStub: @unchecked Sendable {
    private let lock = NSLock()
    private var action: (@Sendable (OfflineURLProtocol) -> Void)?
    private var count = 0
    var handler: (@Sendable (OfflineURLProtocol) -> Void)? {
        get { lock.withLock { action } }
        set { lock.withLock { action = newValue } }
    }
    var requestCount: Int { lock.withLock { count } }
    func record() { lock.withLock { count += 1 } }
    func reset() { lock.withLock { action = nil; count = 0 } }
}

private final class OfflineURLProtocol: URLProtocol {
    static let stub = OfflineStub()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Self.stub.record(); Self.stub.handler?(self) }
    override func stopLoading() { }
    func redirect(to url: URL) {
        let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil, headerFields: ["Location": url.absoluteString])!
        client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: url), redirectResponse: response)
    }
    func begin(expected: Int64, mime: String = "audio/mp4") {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                       headerFields: ["Content-Type": mime, "Content-Length": String(expected)])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
    }
    func respond(bytes: Int, expected: Int64? = nil, mime: String = "audio/mp4") {
        begin(expected: expected ?? Int64(bytes), mime: mime)
        client?.urlProtocol(self, didLoad: Data(repeating: 7, count: bytes))
        client?.urlProtocolDidFinishLoading(self)
    }
}
