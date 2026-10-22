#if os(macOS)
import AVFoundation
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativeServerTests: XCTestCase {
    func testNativeBooksReadAllFormatsAndRestoreRewindsAgainstFixture() async throws {
        guard let fixturePath = ProcessInfo.processInfo.environment["LUMINA_NATIVE_FIXTURE"] else { throw XCTSkip("LUMINA_NATIVE_FIXTURE is not configured") }
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: fixturePath)))
        let ids = try [fixture.comicBookID, fixture.pdfBookID, fixture.cbrBookID].map { try XCTUnwrap($0) }
        let prose = try XCTUnwrap(fixture.proseBookID)
        let client = LuminaClient(), deviceID = "native-books-proof"
        let signedIn = try await client.authenticate(origin: fixture.serverURL, username: fixture.username, password: fixture.password, deviceID: deviceID)
        let me = try await client.me(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
        let owner = LuminaSession(serverURL: fixture.serverURL, accessToken: signedIn.token, userID: me.id, username: fixture.username, serverName: "Fixture", deviceID: deviceID)
        let api = LuminaBooks(session: owner)
        do {
            let catalog = try await api.catalog(search: "")
            XCTAssertTrue(Set(ids + [prose]).isSubset(of: Set(catalog.items.map(\.id))))
            for id in ids {
                let manifest = try await api.manifest(id: id)
                XCTAssertGreaterThan(manifest.count, 1)
                let data = try await api.page(id: id, index: 1)
                XCTAssertNotNil(BookPagePreparation.image(from: data, maximumPixelSize: 1024))
                let cover = try await api.cover(id: id)
                XCTAssertNotNil(BookPagePreparation.image(from: cover, maximumPixelSize: 768))
                _ = try await api.save(id: id, unit: 1, offset: 0.2)
                let restored = try await api.manifest(id: id)
                XCTAssertEqual(restored.book.progress.unitIndex, 1)
                XCTAssertEqual(restored.book.progress.offset, 0.2)
                _ = try await api.save(id: id, unit: 0, offset: 0)
            }
            let nodes = try await api.chapter(id: prose, index: 0)
            XCTAssertFalse(nodes.isEmpty)
            let proseCover = try await api.cover(id: prose)
            XCTAssertNotNil(BookPagePreparation.image(from: proseCover, maximumPixelSize: 768))
            let overview = try await api.overview()
            XCTAssertTrue(overview.shelves.contains { Set(ids + [prose]).isSubset(of: Set($0.bookIDs)) })
            _ = try await api.save(id: prose, unit: 0, offset: 0.6)
            _ = try await api.save(id: prose, unit: 0, offset: 0.1)
            let restored = try await api.manifest(id: prose)
            XCTAssertEqual(restored.book.progress.offset, 0.1)
            _ = try await api.save(id: prose, unit: 0, offset: 0)
            try await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
        } catch {
            try? await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            throw error
        }
    }

    func testNativeDirectAndHLSPlaybackAgainstFixture() async throws {
        guard let fixturePath = ProcessInfo.processInfo.environment["LUMINA_NATIVE_FIXTURE"] else {
            throw XCTSkip("LUMINA_NATIVE_FIXTURE is not configured")
        }
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: fixturePath)))
        let client = LuminaClient()
        let deviceID = "native-proof-\(UUID().uuidString.lowercased())"
        let signedIn = try await client.authenticate(origin: fixture.serverURL, username: fixture.username, password: fixture.password, deviceID: deviceID)

        do {
            let listed = try await client.items(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, search: "", start: 0)
            XCTAssertFalse(listed.items.isEmpty)
            XCTAssertFalse(listed.items.contains(where: \.isAudio))


            let directItem = try await client.item(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, id: fixture.directItemID)
            let directInfo = try await client.playbackInfo(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, item: directItem, forceTranscode: false)
            let direct = try client.playbackRequest(origin: fixture.serverURL, token: signedIn.token, item: directItem, result: directInfo)
            XCTAssertFalse(direct.isTranscoding)
            let directPosition = try await playAndReport(direct, client: client, origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            XCTAssertGreaterThan(directPosition, direct.startPositionSeconds + 2)
            let refreshedDirect = try await client.item(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, id: fixture.directItemID)
            XCTAssertLessThan(abs(refreshedDirect.resumeSeconds - directPosition), 2)

            let convertedItem = try await client.item(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, id: fixture.convertedItemID)
            let mkvInfo = try await client.playbackInfo(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, item: convertedItem, forceTranscode: false)
            let mkv = try client.playbackRequest(origin: fixture.serverURL, token: signedIn.token, item: convertedItem, result: mkvInfo)
            XCTAssertTrue(mkv.usesClientDemux)
            XCTAssertFalse(mkv.isTranscoding)
            let mkvPosition = try await playAndReport(mkv, client: client, origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            XCTAssertGreaterThan(mkvPosition, mkv.startPositionSeconds + 2)

            let convertedInfo = try await client.playbackInfo(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, item: convertedItem, forceTranscode: true)
            let converted = try client.playbackRequest(origin: fixture.serverURL, token: signedIn.token, item: convertedItem, result: convertedInfo)
            XCTAssertTrue(converted.isTranscoding)
            XCTAssertTrue(converted.url.path.contains("/master.m3u8"))
            let convertedPosition = try await playAndReport(converted, client: client, origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            XCTAssertGreaterThan(convertedPosition, 1)

            let resumed = try await client.resume(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            let directProgress = resumed.first(where: { $0.id == directItem.id })?.resumeSeconds ?? 0
            XCTAssertGreaterThan(directProgress, 0)
            try await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
        } catch {
            try? await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            throw error
        }
    }

    func testNativeAudioPlaybackAgainstFixture() async throws {
        try await checkAudioPlayback(compatible: false)
    }

    func testNativeCompatibleAudioPlaybackAgainstFixture() async throws {
        try await checkAudioPlayback(compatible: true)
    }

    func testNativeProgressiveAudioPlaybackAgainstFixture() async throws {
        guard let fixturePath = ProcessInfo.processInfo.environment["LUMINA_NATIVE_FIXTURE"] else {
            throw XCTSkip("LUMINA_NATIVE_FIXTURE is not configured")
        }
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: fixturePath)))
        let audioID = try XCTUnwrap(fixture.audioTrackID)
        let client = LuminaClient()
        let deviceID = "native-progressive-proof-\(UUID().uuidString.lowercased())"
        let signedIn = try await client.authenticate(origin: fixture.serverURL, username: fixture.username, password: fixture.password, deviceID: deviceID)
        do {
            let item = try await client.item(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, id: audioID)
            let metadata = client.request(origin: fixture.serverURL, path: "/Lumina/Music/tracks/\(item.id)", token: signedIn.token, deviceID: deviceID)
            let track = try JSONDecoder().decode(MusicTrack.self, from: await client.send(metadata))
            let audio = try client.audioRequest(origin: fixture.serverURL, token: signedIn.token, item: item)
            var resolutions = 0
            let transport = NativeMusicTransport(tracks: [track], index: 0, position: 0) { _ in
                resolutions += 1
                return audio
            }
            defer { transport.stop() }
            transport.configure(repeatMode: "off", crossfade: 0, normalize: false, volume: 0)
            await transport.start()
            let confirmed = await transport.confirmPlaying(timeout: 5)
            XCTAssertTrue(confirmed)
            XCTAssertEqual(transport.mode, .progressive)
            await transport.seek(0.5)
            transport.pause()
            await transport.resume()
            let resumed = await transport.confirmPlaying(timeout: 3)
            XCTAssertTrue(resumed)
            try await Task.sleep(for: .milliseconds(300))
            XCTAssertGreaterThan(transport.state.position, 0.5)
            XCTAssertEqual(resolutions, 1)
            XCTAssertNil(transport.state.error)
            transport.stop()
            try await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
        } catch {
            try? await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            throw error
        }
    }

    private func checkAudioPlayback(compatible: Bool) async throws {
        guard let fixturePath = ProcessInfo.processInfo.environment["LUMINA_NATIVE_FIXTURE"] else {
            throw XCTSkip("LUMINA_NATIVE_FIXTURE is not configured")
        }
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: fixturePath)))
        guard let audioID = fixture.audioTrackID else { throw XCTSkip("Fixture has no audio track") }
        let client = LuminaClient()
        let deviceID = "native-audio-proof-\(UUID().uuidString.lowercased())"
        let signedIn = try await client.authenticate(origin: fixture.serverURL, username: fixture.username, password: fixture.password, deviceID: deviceID)
        do {
            let item = try await client.item(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID, id: audioID)
            XCTAssertTrue(item.isAudio)
            var audio = try client.audioRequest(origin: fixture.serverURL, token: signedIn.token, item: item)
            if compatible {
                var parts = URLComponents(url: audio.url, resolvingAgainstBaseURL: false)!
                parts.queryItems = (parts.queryItems ?? []) + [URLQueryItem(name: "format", value: "flac")]
                audio = PlaybackRequest(id: audio.id, item: item, url: parts.url!, startPositionSeconds: 0, mediaSourceID: audio.mediaSourceID, isTranscoding: false, methodLabel: "Compatible audio")
            }
            XCTAssertFalse(audio.isTranscoding)
            let position = try await playAndReport(audio, client: client, origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            XCTAssertGreaterThan(position, 2)
            try await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
        } catch {
            try? await client.logout(origin: fixture.serverURL, token: signedIn.token, deviceID: deviceID)
            throw error
        }
    }

    private func playAndReport(_ request: PlaybackRequest, client: LuminaClient, origin: URL, token: String, deviceID: String) async throws -> Double {
        let source = NativeVideoSource()
        let player = AVPlayer(playerItem: try await source.prepare(url: request.url, usesClientDemux: request.usesClientDemux))
        defer { source.stop() }
        player.isMuted = true
        defer { player.pause(); player.replaceCurrentItem(with: nil) }

        for _ in 0..<100 {
            switch player.currentItem?.status {
            case .readyToPlay: break
            case .failed:
                let error = player.currentItem?.error as NSError?
                throw NSError(domain: error?.domain ?? NSURLErrorDomain, code: error?.code ?? URLError.cannotDecodeContentData.rawValue)
            default:
                try await Task.sleep(for: .milliseconds(100))
                continue
            }
            break
        }
        guard player.currentItem?.status == .readyToPlay else { throw URLError(.timedOut) }
        if request.startPositionSeconds > 0 {
            await player.seek(to: CMTime(seconds: request.startPositionSeconds, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        }
        var started = false
        var position = player.currentTime().seconds
        do {
            try await client.report(origin: origin, token: token, deviceID: deviceID, event: .start, request: request, positionSeconds: position, isPaused: false)
            started = true
            player.play()
            try await Task.sleep(for: .seconds(3.2))
            position = player.currentTime().seconds
            try await client.report(origin: origin, token: token, deviceID: deviceID, event: .progress, request: request, positionSeconds: position, isPaused: false)
            try await client.report(origin: origin, token: token, deviceID: deviceID, event: .stop, request: request, positionSeconds: position, isPaused: true)
            return position
        } catch {
            if started {
                position = player.currentTime().seconds
                try? await client.report(origin: origin, token: token, deviceID: deviceID, event: .stop, request: request, positionSeconds: position, isPaused: true)
            }
            throw error
        }
    }
}

private struct Fixture: Decodable {
    let comicBookID: String?
    let proseBookID: String?
    let pdfBookID: String?
    let cbrBookID: String?
    let serverURL: URL
    let username: String
    let password: String
    let directItemID: String
    let convertedItemID: String
    let audioTrackID: String?
}
#endif
