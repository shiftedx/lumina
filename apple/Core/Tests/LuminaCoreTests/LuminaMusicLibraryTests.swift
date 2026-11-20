import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaMusicLibraryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MusicURLProtocol.handler = nil
    }

    func testHomeCapabilityFallsBackWithoutTouchingSavedQueue() async throws {
        let requests = MusicRequests()
        let library = library()
        library.configure(session: session())
        MusicURLProtocol.handler = { transport in
            requests.append(transport.request)
            switch transport.request.url?.lastPathComponent {
            case "capability": transport.respond(["enabled": false])
            case "session": transport.respond(snapshotJSON(revision: 4, position: 23))
            default: transport.respond([:], status: 404)
            }
        }
        await library.refreshHome()
        XCTAssertEqual(library.homeCapability, .disabled)
        XCTAssertNil(library.homeState.content)
        let saved = try await library.loadSession()
        XCTAssertEqual(saved.positionSeconds, 23)
        XCTAssertEqual(saved.currentIndex, 2)
        library.configure(session: session(user: "bob"))
        MusicURLProtocol.handler = { $0.respond([:], status: 404) }
        await library.refreshHome()
        XCTAssertEqual(library.homeCapability, .unsupported)
        XCTAssertNil(library.homeState.content)
        XCTAssertEqual(requests.all.map { $0.url?.lastPathComponent }, ["capability", "session"])
    }

    func testHomeLayoutAndContextualMixShareTypedMemberContracts() async throws {
        let requests = MusicRequests()
        let library = library()
        library.configure(session: session())
        let hidden = ["downloads"]
        MusicURLProtocol.handler = { transport in
            requests.append(transport.request)
            switch transport.request.url?.lastPathComponent {
            case "capability": transport.respond(["enabled": true])
            case "home":
                transport.respond([
                    "layout": ["order": ["resume", "downloads"], "hidden": []],
                    "resume": ["state": "playable", "revision": 2, "entry_index": 1, "current_index": 1,
                               "position_seconds": 23, "track": ["id": musicTrackID, "title": "Saved track"]],
                    "recent": [], "playlists": [], "mixes": [["kind": "rotation", "name": "Rotation"]],
                ])
            case "layout": transport.respond(["order": ["resume", "downloads"], "hidden": hidden])
            case "contextual-mix":
                var track = trackJSON(id: musicTrackTwoID)
                track["reason"] = ["code": "shared_genre", "genre": "Soul"]
                transport.respond([
                    "seed": ["id": musicTrackID, "kind": "track", "name": "Saved track", "genres": ["Soul"]],
                    "items": [track], "exhausted": true,
                ])
            case "session":
                var saved = snapshotJSON(revision: 3, position: 23)
                saved["entries"] = [
                    ["kind": "track", "item_id": musicTrackID],
                    ["kind": "track", "item_id": musicTrackTwoID,
                     "provenance": ["source": "contextual_mix", "reason": "shared_genre"]],
                ]
                transport.respond(saved)
            default: transport.respond([:], status: 404)
            }
        }
        await library.refreshHome()
        XCTAssertEqual(library.homeCapability, .available)
        XCTAssertEqual(library.homeState.content?.resume?.positionSeconds, 23)
        let newLayout = MusicHomeLayout(order: [.resume, .downloads], hidden: [.downloads])
        try await library.saveHomeLayout(newLayout)
        XCTAssertEqual(library.homeState.content?.layout, newLayout)
        XCTAssertEqual(try musicBody(requests.all[2])["hidden"] as? [String], hidden)
        let preview = try await library.contextualMix(kind: .track, seedID: musicTrackID, excluding: [musicTrackID])
        XCTAssertEqual(preview.items.first?.reason.genre, "Soul")
        XCTAssertEqual(preview.items.first?.track.id, musicTrackTwoID)
        let query = try XCTUnwrap(requests.all[3].url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.queryItems })
        XCTAssertTrue(query.contains(URLQueryItem(name: "exclude_item_ids", value: musicTrackID)))

        let generated = MusicQueueEntry(kind: "track", itemID: musicTrackTwoID, provenance: .init(reason: .sharedGenre))
        let saved = try await library.saveSession(itemIDs: [musicTrackID, musicTrackTwoID], currentIndex: 0,
            positionSeconds: 23, repeatMode: "off", shuffle: false, baseRevision: 2,
            entries: [.init(kind: "track", itemID: musicTrackID), generated], entryIndex: 0)
        XCTAssertEqual(saved.entries[1].provenance, generated.provenance)
        let entries = try XCTUnwrap(try musicBody(requests.all[4])["entries"] as? [[String: Any]])
        XCTAssertNil(entries[0]["provenance"])
        XCTAssertEqual(entries[1]["provenance"] as? [String: String], ["source": "contextual_mix", "reason": "shared_genre"])

        library.configure(session: session(user: "bob"))
        XCTAssertEqual(library.homeCapability, .unknown)
        XCTAssertNil(library.homeState.content)
    }

    func testCollectionFileQualityUsesOneNativeReadAndSeparatesMixedAndUnknownFacts() async throws {
        let requests = MusicRequests()
        let library = library()
        library.configure(session: session())
        let album: [String: Any] = ["track_count": 2, "codecs": ["aac", "flac"], "bit_depths": [24],
            "sample_rates": [44100, 96000], "bitrate_kbps": 1028, "formats_complete": true]
        MusicURLProtocol.handler = { transport in
            requests.append(transport.request)
            transport.respond(["album": album, "albums": ["native-album": album, "partial-album": ["track_count": 2, "codecs": ["flac"], "bit_depths": [24], "sample_rates": [96000], "bitrate_kbps": NSNull(), "formats_complete": false]], "tracks": [
                "native-track": ["codec": "flac", "bitrate_kbps": 1800, "bit_depth": 24, "sample_rate": 96000],
                "unknown-track": ["codec": NSNull(), "bitrate_kbps": NSNull(), "bit_depth": NSNull(), "sample_rate": NSNull()]]])
        }
        let result = try await library.fileQuality(for: "native-album")
        XCTAssertEqual(requests.all.count, 1)
        XCTAssertEqual(requests.all[0].url?.path, "/Lumina/Music/titles/native-album/quality")
        XCTAssertEqual(requests.all[0].httpMethod, "GET")
        XCTAssertTrue(requests.all[0].value(forHTTPHeaderField: "Authorization")?.contains("Token=\"music-test-token-alice\"") == true)
        XCTAssertTrue(result.albums["partial-album"]?.formatSummary.contains("Partial file metadata") == true)
        XCTAssertEqual(result.albums["partial-album"]?.bitrateSummary, "Bitrate unavailable")
        XCTAssertEqual(result.album?.codecs, ["aac", "flac"])
        XCTAssertEqual(result.albums["native-album"]?.sampleRates, [44100, 96000])
        XCTAssertTrue(result.album?.formatSummary.contains("Mixed formats") == true)
        XCTAssertFalse(result.album?.formatSummary.contains("24-bit") == true, "A mixed AAC/FLAC album must not imply every track is 24-bit")
        XCTAssertEqual(result.tracks["native-track"]?.bitDepth, 24)
        XCTAssertEqual(result.tracks["native-track"]?.bitrateKbps, 1800)
        XCTAssertTrue(result.tracks["native-track"]?.summary.contains("96 kHz") == true)
        XCTAssertEqual(result.tracks["unknown-track"]?.summary, "Format unavailable · Bitrate unavailable")
        MusicURLProtocol.handler = { $0.respond([:], status: 404) }
        do { _ = try await library.fileQuality(for: "native-album"); XCTFail("Old-server availability must remain distinguishable from file quality") }
        catch MusicAPIError.http(let status) { XCTAssertEqual(status, 404) }
    }

    func testPlaylistCreateRefreshesSmartListsAndSurfacesRevisionAndOwnerErrors() async throws {
        let requests = MusicRequests()
        let library = library()
        library.configure(session: session())
        MusicURLProtocol.handler = { transport in
            let request = transport.request
            requests.append(request)
            switch request.httpMethod {
            case "POST": transport.respond(playlistJSON())
            case "GET": transport.respond([playlistJSON(), playlistJSON(id: "smart", count: NSNull(), rules: ["genres": ["Soul"], "favorites": true, "unplayed": false])])
            case "PUT": transport.respond([:], status: 409)
            case "DELETE": transport.respond([:], status: 403)
            default: transport.respond([:], status: 404)
            }
        }
        let saved = try await library.savePlaylist(name: "Morning", shared: true, itemIDs: [musicTrackID], rules: nil, revision: nil)
        XCTAssertEqual(saved.id, "playlist-one")
        XCTAssertEqual(library.playlists.map(\.id), ["playlist-one", "smart"])
        XCTAssertNil(library.errorMessage, "Smart playlist summaries use count:null")
        let created = try musicBody(requests.all[0])
        XCTAssertEqual(created["item_ids"] as? [String], [musicTrackID])
        XCTAssertEqual(created["shared"] as? Bool, true)
        XCTAssertEqual(requests.all[0].url?.path, "/Lumina/Music/playlists")
        do {
            _ = try await library.savePlaylist(id: saved.id, name: "Changed", shared: true, itemIDs: [], rules: nil, revision: 1)
            XCTFail("A stale playlist revision must be surfaced")
        } catch MusicAPIError.http(let code) {
            XCTAssertEqual(code, 409)
        }
        XCTAssertEqual(try musicBody(requests.all[2])["revision"] as? Int, 1)
        do {
            try await library.deletePlaylist(saved)
            XCTFail("An owner-only mutation must be surfaced")
        } catch MusicAPIError.http(let code) {
            XCTAssertEqual(code, 403)
            XCTAssertTrue(errorText(.http(code)).contains("access and listening limits"))
        }
        XCTAssertEqual(library.playlists.map(\.id), ["playlist-one", "smart"], "Failed writes must not replace the visible list")
    }

    func testDeleteRefreshCannotBeOverwrittenByEarlierCatalogResponse() async throws {
        let library = library()
        library.configure(session: session())
        let requests = MusicRequests()
        let pending = PendingMusicResponse()
        let started = expectation(description: "Older playlist refresh is in flight")
        MusicURLProtocol.handler = { transport in
            requests.append(transport.request)
            if transport.request.httpMethod == "DELETE" {
                transport.respond(["deleted": true])
            } else {
                let reads = requests.getCount
                if reads == 2 {
                    pending.set(transport)
                    started.fulfill()
                } else { transport.respond(reads == 1 ? [playlistJSON()] : []) }
            }
        }
        await library.refresh()
        let playlist = try XCTUnwrap(library.playlists.first)
        let oldRefresh = Task { await library.refresh() }
        await fulfillment(of: [started], timeout: 2)
        try await library.deletePlaylist(playlist)
        XCTAssertTrue(library.playlists.isEmpty)
        pending.respond([playlistJSON()])
        await oldRefresh.value
        XCTAssertTrue(library.playlists.isEmpty, "An older catalog response must not resurrect a deleted playlist")
        XCTAssertFalse(library.isBusy)
        XCTAssertNil(library.errorMessage)
    }

    func testSessionWritesPreserveDuplicateOrderIntentionalRewindAndConflictSnapshot() async throws {
        let requests = MusicRequests()
        let library = library()
        library.configure(session: session())
        MusicURLProtocol.handler = { transport in
            let request = transport.request
            requests.append(request)
            let body = try! musicBody(request)
            let revision = body["base_revision"] as! Int
            let position = body["position_seconds"] as! Double
            if revision == 1 && position == 1 {
                transport.respond(["detail": ["code": "music_session_conflict", "session": snapshotJSON(revision: 2, position: 7)]], status: 409)
            } else {
                transport.respond(snapshotJSON(revision: revision + 1, position: position))
            }
        }
        let ids = [musicTrackID, musicTrackTwoID, musicTrackID]
        let first = try await library.saveSession(itemIDs: ids, currentIndex: 2, positionSeconds: 50, repeatMode: "all", shuffle: true, baseRevision: 0)
        XCTAssertEqual(first.queue.map(\.id), ids)
        let rewound = try await library.saveSession(itemIDs: ids, currentIndex: 2, positionSeconds: 7, repeatMode: "all", shuffle: true, baseRevision: 1)
        XCTAssertEqual(rewound.positionSeconds, 7)
        XCTAssertEqual(rewound.revision, 2)
        let write = try musicBody(requests.all[1])
        XCTAssertEqual(write["item_ids"] as? [String], ids)
        XCTAssertEqual(write["current_index"] as? Int, 2)
        XCTAssertEqual(write["position_seconds"] as? Double, 7)
        XCTAssertEqual((write["active_output"] as? [String: String])?["kind"], "browser")
        do {
            _ = try await library.saveSession(itemIDs: [musicTrackTwoID], currentIndex: 0, positionSeconds: 1, repeatMode: "off", shuffle: false, baseRevision: 1)
            XCTFail("Concurrent changes must return the latest queue")
        } catch MusicAPIError.conflict(let current) {
            XCTAssertEqual(current.queue.map(\.id), ids)
            XCTAssertEqual(current.currentIndex, 2)
            XCTAssertEqual(current.positionSeconds, 7)
            XCTAssertEqual(current.revision, 2)
        }
    }

    func testAccountSwitchRejectsLateResponseAndCheckpointsStayOwnerScopedWithoutURLs() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let library = library(root: root)
        let alice = session()
        let bob = session(user: "bob", token: alice.accessToken)
        library.configure(session: alice)
        var checkpointJSON = snapshotJSON(revision: 4, position: 7)
        checkpointJSON["entries"] = [["kind": "track", "item_id": musicTrackID, "tracks": [trackJSON()]]]
        let checkpoint = try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(checkpointJSON))
        library.persistCheckpoint(snapshot: checkpoint)
        let aliceIdentity = try XCTUnwrap(library.sessionIdentity)
        let started = expectation(description: "First member's session request started")
        let pending = PendingMusicResponse()
        MusicURLProtocol.handler = { transport in
            pending.set(transport)
            started.fulfill()
        }
        let loading = Task { try await library.loadSession() }
        await fulfillment(of: [started], timeout: 2)
        library.configure(session: bob)
        XCTAssertNotEqual(library.sessionIdentity, aliceIdentity)
        XCTAssertNil(library.cachedSession)
        pending.respond(snapshotJSON(revision: 5, position: 30))
        do {
            _ = try await loading.value
            XCTFail("Late metadata from a different member must be discarded")
        } catch is CancellationError {}
        XCTAssertNil(library.cachedTrack(for: musicTrackID))
        library.persistCheckpoint(snapshot: .init(revision: 1))
        library.persistCheckpoint(snapshot: checkpoint, owner: aliceIdentity)
        XCTAssertEqual(library.cachedSession?.revision, 1, "A stale owner's checkpoint must not replace the new owner's in-memory queue")
        library.configure(session: alice)
        await library.waitForPersistentCache()
        XCTAssertEqual(library.cachedSession?.revision, 4)
        XCTAssertEqual(library.cachedSession?.queue.map(\.id), [musicTrackID, musicTrackTwoID, musicTrackID])
        XCTAssertEqual(library.cachedSession?.positionSeconds, 7)
        let nested = try XCTUnwrap(library.cachedSession?.entries.first?.tracks?.first)
        XCTAssertEqual(nested.id, musicTrackID)
        XCTAssertEqual(nested.audioURL, "")
        XCTAssertNil(nested.artworkURL)
        let directories = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        XCTAssertEqual(directories.count, 2)
        for directory in directories {
            let file = directory.appending(path: "queue.json")
            let text = String(decoding: try Data(contentsOf: file), as: UTF8.self)
            XCTAssertFalse(text.contains("music-test-token"))
            XCTAssertFalse(text.contains("ApiKey"))
            XCTAssertFalse(text.contains("example.test"))
            XCTAssertFalse(text.contains("/Audio/"))
            XCTAssertFalse(text.contains("/Items/"))
        }
        library.configure(session: bob)
        await library.waitForPersistentCache()
        XCTAssertEqual(library.cachedSession?.revision, 1)
        XCTAssertEqual(library.cachedSession?.queue.count, 0)
    }

    func testCheckpointWriterRestoresLatestDuplicateQueueAcrossSignOutAndAccountSwitch() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let library = library(root: root)
        let alice = session()
        let bob = session(user: "bob", token: "music-test-token-bob")
        library.configure(session: alice)
        for revision in 4...104 {
            let intermediate = try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(snapshotJSON(revision: revision, position: Double(revision))))
            library.persistCheckpoint(snapshot: intermediate)
        }
        let latest = try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(snapshotJSON(revision: 105, position: 7)))
        library.persistCheckpoint(snapshot: latest)
        XCTAssertEqual(library.cachedSession?.revision, 105, "The current playback snapshot remains available before background writing completes")
        await library.flushCheckpoint()
        library.configure(session: nil)
        library.configure(session: alice)
        await library.waitForPersistentCache()
        XCTAssertEqual(library.cachedSession?.revision, 105)
        XCTAssertEqual(library.cachedSession?.positionSeconds, 7, "A deliberate rewind must win over an earlier higher position")
        XCTAssertEqual(library.cachedSession?.queue.map(\.id), [musicTrackID, musicTrackTwoID, musicTrackID])
        XCTAssertEqual(library.cachedSession?.repeatMode, "all")
        XCTAssertTrue(library.cachedSession?.shuffle == true)
        let aliceIdentity = try XCTUnwrap(library.sessionIdentity)
        var final = latest
        final.revision = 106; final.positionSeconds = 6
        library.persistCheckpoint(snapshot: final)
        library.configure(session: nil)
        await library.flushCheckpoint()
        let restored = self.library(root: root)
        restored.configure(session: alice)
        await restored.waitForPersistentCache()
        XCTAssertEqual(restored.cachedSession?.revision, 106, "Signing out must drain an accepted checkpoint before a new library restores it")
        XCTAssertEqual(restored.cachedSession?.positionSeconds, 6)
        restored.configure(session: bob)
        restored.persistCheckpoint(snapshot: latest, owner: aliceIdentity)
        await restored.flushCheckpoint()
        XCTAssertNil(restored.cachedSession, "A former member cannot replace the active member's checkpoint")
        restored.configure(session: alice)
        await restored.waitForPersistentCache()
        XCTAssertEqual(restored.cachedSession?.revision, 106, "A rejected stale checkpoint must not overwrite the member's latest queue")
    }

    func testPersistedLyricsLoadWithTheQueueAndANewerCheckpointWins() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let alice = session()
        let track = try JSONDecoder().decode(MusicTrack.self, from: musicJSON(trackJSON()))
        let secondTrack = try JSONDecoder().decode(MusicTrack.self, from: musicJSON(trackJSON(id: musicTrackTwoID)))
        let saved = library(root: root)
        saved.configure(session: alice)
        MusicURLProtocol.handler = { transport in
            let plain = transport.request.url?.path.contains(secondTrack.nativeID) == true ? "Later lyrics" : "Cached lyrics"
            transport.respond(["plain": plain, "cues": [], "source": "fixture"])
        }
        _ = try await saved.lyrics(for: track.mediaItem)
        _ = try await saved.lyrics(for: secondTrack.mediaItem)
        let original = try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(snapshotJSON(revision: 4, position: 7)))
        saved.persistCheckpoint(snapshot: original)
        await saved.flushCheckpoint()

        let restored = library(root: root)
        restored.configure(session: alice)
        let newer = try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(snapshotJSON(revision: 5, position: 3)))
        restored.persistCheckpoint(snapshot: newer)
        await restored.waitForPersistentCache()
        XCTAssertEqual(restored.cachedSession?.revision, 5, "A late disk read must not replace an accepted in-memory checkpoint")
        XCTAssertEqual(restored.cachedLyrics(for: track.nativeID)?.plain, "Cached lyrics")
        XCTAssertEqual(restored.cachedLyrics(for: secondTrack.nativeID)?.plain, "Later lyrics")
    }

    func testLatePersistedQueueDoesNotReplaceForegroundTrackMetadata() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let alice = session()
        var cached = snapshotJSON(revision: 4, position: 7)
        cached["queue"] = [trackJSON(title: "Cached title")]
        let saved = library(root: root)
        saved.configure(session: alice)
        saved.persistCheckpoint(snapshot: try JSONDecoder().decode(MusicListeningSnapshot.self, from: musicJSON(cached)))
        await saved.flushCheckpoint()

        let restored = library(root: root)
        restored.configure(session: alice)
        MusicURLProtocol.handler = { transport in
            var live = snapshotJSON(revision: 5, position: 3)
            live["queue"] = [trackJSON(title: "Live title")]
            transport.respond(live)
        }
        _ = try await restored.loadSession()
        await restored.waitForPersistentCache()
        XCTAssertEqual(restored.cachedTrack(for: musicTrackID)?.title, "Live title", "A late persisted queue must only fill missing track metadata")
    }

    func testDownloadRequestUsesOwnOriginNativeFormatAndMediaBrowserHeaderOnly() throws {
        let library = library()
        library.configure(session: session())
        let track = try JSONDecoder().decode(MusicTrack.self, from: musicJSON(trackJSON()))
        let request = try library.downloadRequest(for: track.mediaItem)
        XCTAssertEqual(request.url?.scheme, "https")
        XCTAssertEqual(request.url?.host, "media.example.test")
        XCTAssertEqual(request.url?.port, 8765)
        XCTAssertEqual(request.url?.path, "/Lumina/Music/tracks/\(track.nativeID)/download")
        let query = URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(query, [URLQueryItem(name: "format", value: "native")])
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("Token=\"music-test-token-alice\"") == true)
        XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.contains("DeviceId=\"music-test-device\"") == true)
        XCTAssertNil(request.value(forHTTPHeaderField: "Cookie"))
        XCTAssertFalse(request.url!.absoluteString.contains("music-test-token"))
        let compatible = try library.downloadRequest(for: track.mediaItem, format: "flac")
        XCTAssertEqual(URLComponents(url: compatible.url!, resolvingAgainstBaseURL: false)?.queryItems, [URLQueryItem(name: "format", value: "flac")])
        library.configure(session: nil)
        XCTAssertThrowsError(try library.downloadRequest(for: track.mediaItem))
    }

    func testMetadataHistoryIsBoundedWithoutTruncatingTheResolvedQueue() async throws {
        let library = library()
        library.configure(session: session())
        let ids = (1...640).map { String(format: "00000000-0000-0000-0001-%012d", $0) }
        let requests = MusicRequests()
        MusicURLProtocol.handler = { @Sendable transport in
            requests.append(transport.request)
            if transport.request.httpMethod == "POST" { transport.respond(ids.map { trackJSON(id: $0) }) }
            else { transport.respond(trackJSON(id: ids[0])) }
        }
        let queue = try await library.resolve(items: [])
        XCTAssertEqual(queue.map(\.id), ids, "Cache eviction must never truncate or reorder playback's queue")
        let retained = ids.filter { library.cachedTrack(for: $0) != nil }.count
        print("LUMINA_METADATA_BENCH resolved=\(queue.count) retained=\(retained)")
        XCTAssertLessThanOrEqual(retained, 512, "Browsing new albums must not retain every earlier track for the session")
        let latest = try XCTUnwrap(queue.last)
        XCTAssertEqual(library.cachedTrack(for: latest.id), latest)
        XCTAssertEqual(library.cachedTrack(for: latest.nativeID), latest)
        XCTAssertNil(library.cachedTrack(for: ids[0]))
        XCTAssertNil(library.cachedTrack(for: queue[0].nativeID))
        let reloaded = try await library.track(for: queue[0].mediaItem)
        XCTAssertEqual(reloaded.id, ids[0], "An evicted track remains available through the member API")
        XCTAssertEqual(requests.getCount, 1)
    }

    private func library(root: URL? = nil) -> LuminaMusicLibrary {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicURLProtocol.self]
        return LuminaMusicLibrary(client: LuminaClient(configuration: configuration), root: root ?? FileManager.default.temporaryDirectory.appending(path: UUID().uuidString))
    }

    private func session(user: String = "alice", token: String = "music-test-token-alice") -> LuminaSession {
        LuminaSession(serverURL: URL(string: "https://media.example.test:8765")!, accessToken: token, userID: user,
                      username: user, serverName: "Lumina", deviceID: "music-test-device")
    }

    private func errorText(_ error: MusicAPIError) -> String { error.localizedDescription }
}

private let musicTrackID = "00000000-0000-0000-0000-000000000001"
private let musicTrackTwoID = "00000000-0000-0000-0000-000000000002"

private func trackJSON(id: String = musicTrackID, title: String = "Synthetic track") -> [String: Any] {
    ["id": id, "title": title, "artist": "Artist", "album_artist": "Artist", "album": "Album",
     "audio_url": "https://media.example.test/Audio/\(id)/stream?ApiKey=music-test-token-alice",
     "artwork_url": "/Items/album/Images/Primary?ApiKey=music-test-token-alice", "gain_is_manual": false, "genres": []]
}

private func playlistJSON(id: String = "playlist-one", count: Any = 2, rules: Any = NSNull()) -> [String: Any] {
    ["id": id, "name": "Morning", "shared": true, "revision": 1, "count": count, "can_edit": true, "rules": rules]
}

private func snapshotJSON(revision: Int, position: Double) -> [String: Any] {
    ["revision": revision, "queue": [trackJSON(), trackJSON(id: musicTrackTwoID), trackJSON()],
     "current_index": 2, "position_seconds": position, "repeat": "all", "shuffle": true, "active_output": ["kind": "browser"]]
}

private func musicJSON(_ value: Any) -> Data { try! JSONSerialization.data(withJSONObject: value) }

private func musicBody(_ request: URLRequest) throws -> [String: Any] {
    if let data = request.httpBody { return try JSONSerialization.jsonObject(with: data) as! [String: Any] }
    let stream = try XCTUnwrap(request.httpBodyStream)
    stream.open()
    defer { stream.close() }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 1024)
    while true {
        let count = stream.read(&buffer, maxLength: buffer.count)
        guard count > 0 else { break }
        data.append(buffer, count: count)
    }
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

private final class MusicRequests: @unchecked Sendable {
    private let lock = NSLock()
    private var requests = [URLRequest]()
    func append(_ request: URLRequest) { lock.withLock { requests.append(request) } }
    var all: [URLRequest] { lock.withLock { requests } }
    var getCount: Int { lock.withLock { requests.filter { $0.httpMethod == "GET" }.count } }
}

private final class PendingMusicResponse: @unchecked Sendable {
    private let lock = NSLock()
    private var transport: MusicURLProtocol?
    func set(_ value: MusicURLProtocol) { lock.withLock { transport = value } }
    func respond(_ value: Any) { lock.withLock { transport }?.respond(value) }
}

private final class MusicURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((MusicURLProtocol) -> Void)?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Self.handler?(self) }
    override func stopLoading() {}
    func respond(_ value: Any, status: Int = 200) {
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: musicJSON(value))
        client?.urlProtocolDidFinishLoading(self)
    }
}
