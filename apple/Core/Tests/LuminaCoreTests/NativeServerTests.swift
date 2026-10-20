#if os(macOS)
import AVFoundation
import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativeServerTests: XCTestCase {
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
        let player = AVPlayer(url: request.url)
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
    let serverURL: URL
    let username: String
    let password: String
    let directItemID: String
    let convertedItemID: String
    let audioTrackID: String?
}
#endif
