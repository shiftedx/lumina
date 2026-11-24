import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
    func testStartOverReusesPreparedOriginalWithoutWaitingForServer() async throws {
        let saved = syntheticKeychainSession()
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie(positionTicks: 60_000_000))
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "prepared-original", "MediaSources": [[
                    "Id": "movie-1", "Container": "mkv", "Bitrate": 24_000_000,
                    "SupportsDirectPlay": true, "SupportsTranscoding": false,
                    "DefaultAudioStreamIndex": 1, "DefaultSubtitleStreamIndex": -1,
                    "MediaStreams": [["Index": 1, "Type": "Audio", "Codec": "aac", "Channels": 2]],
                    "Chapters": [["StartPositionTicks": 60_000_000, "Name": "Opening"]],
                ]]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "replacement", "MediaSources": [[
                    "Id": "source-2", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-2/master.m3u8?ApiKey=test-token",
                    "DefaultAudioStreamIndex": 3,
                    "MediaStreams": [["Index": 3, "Type": "Audio", "DisplayTitle": "French - AAC", "Codec": "aac", "Language": "fra", "Channels": 2]],
                ]]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "compatible", "MediaSources": [[
                    "Id": "source-2", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-2/master.m3u8?ApiKey=test-token",
                    "DefaultAudioStreamIndex": 3,
                    "MediaStreams": [["Index": 3, "Type": "Audio", "Codec": "aac"]],
                ]]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "server-recovery", "MediaSources": [[
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
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
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
                return coreResponse(request, json: ["PlaySessionId": "language-preferences", "MediaSources": requestedSource == nil ? [hd, source] : [source]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                let posted = try body(request)
                let selectedSource = posted["MediaSourceId"] as? String
                let streams: [[String: Any]] = [
                    ["Index": 0, "Type": "Video", "Height": 2160],
                    ["Index": 3, "Type": "Subtitle", "Codec": "vtt", "Language": "spa", "IsExternal": true,
                     "DeliveryMethod": "External", "DeliveryUrl": "/Videos/movie-1/Subtitles/3/0/Stream.vtt?ApiKey=test-token"],
                ]
                if selectedSource == nil {
                    return coreResponse(request, json: ["PlaySessionId": "direct-mp4", "MediaSources": [[
                        "Id": "source-mp4", "Container": "mp4", "Bitrate": 24_000_000,
                        "SupportsDirectPlay": true, "SupportsTranscoding": true, "MediaStreams": streams,
                    ]]])
                }
                return coreResponse(request, json: ["PlaySessionId": "server-hls", "MediaSources": [[
                    "Id": "source-mp4", "Container": "mp4", "Bitrate": 24_000_000,
                    "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-mp4/master.m3u8?ApiKey=test-token", "MediaStreams": streams,
                ]]])
            default: return coreResponse(request, status: 404)
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
                case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
                case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
                case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
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
                        return coreResponse(request, json: ["PlaySessionId": "m2ts", "MediaSources": [source]])
                    }
                    return coreResponse(request, json: ["PlaySessionId": "server-hls", "MediaSources": [[
                        "Id": "source-m2ts", "Container": "m2ts", "Bitrate": 24_000_000,
                        "SupportsDirectPlay": false, "SupportsTranscoding": true,
                        "TranscodingUrl": "/Videos/source-m2ts/master.m3u8?ApiKey=test-token", "MediaStreams": streams,
                    ]]])
                default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                var detail = movie(positionTicks: 250_000_000)
                detail["RunTimeTicks"] = 300_000_000
                return coreResponse(request, json: detail)
            case "/Items/movie-1/PlaybackInfo": return coreResponse(request, json: ["PlaySessionId": "session-1", "MediaSources": [["Id": "source-1", "SupportsDirectPlay": true, "SupportsTranscoding": false]]])
            default: return coreResponse(request, status: 404)
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

}
