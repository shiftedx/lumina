import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

extension LuminaCoreTests {
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items": return coreResponse(request, json: page(items: [movie()]))
            case "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Items/movie-1/Similar":
                return coreResponse(request, json: page(items: [
                    movie(),
                    ["Id": "related-1", "Name": "Related One", "Type": "Movie"],
                    ["Id": "related-2", "Name": "Related Two", "Type": "Movie"],
                    ["Id": "related-1", "Name": "Duplicate", "Type": "Movie"],
                ]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume": return coreResponse(request, json: page(items: [movie(positionTicks: 50_000_000)]))
            case "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1/UserData": return coreResponse(request, status: 204)
            case "/Users/user-1/Items/movie-1":
                var item = movie()
                item["UserData"] = ["Played": true, "IsFavorite": true]
                return coreResponse(request, json: item)
            default: return coreResponse(request, status: 404)
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
        MockURLProtocol.handler = { request in coreResponse(request, status: 401) }
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
        MockURLProtocol.handler = { request in coreResponse(request, status: 404) }

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
                return coreResponse(request, json: ["PlaySessionId": "session-1", "MediaSources": [[
                    "Id": "source-1", "Container": "mkv", "SupportsDirectPlay": false, "SupportsTranscoding": true,
                    "TranscodingUrl": "/Videos/source-1/master.m3u8?ApiKey=test-token",
                ]]])
            case "/Sessions/Playing/Progress":
                return coreResponse(request, status: 204)
            default:
                return coreResponse(request, status: 404)
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
            return coreResponse(request, json: ["PlaySessionId": "session", "MediaSources": []])
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
            return coreResponse(request, json: [
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                var title = movie()
                title["Chapters"] = [["StartPositionTicks": 0, "Name": "Preferred opening"]]
                return coreResponse(request, json: title)
            case "/Users/user-1/Items/source-version":
                var version = movie()
                version["Id"] = "source-version"
                version["Chapters"] = [["StartPositionTicks": 100_000_000, "Name": "Version chapter"]]
                return coreResponse(request, json: version)
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "version-chapters", "MediaSources": [[
                    "Id": "source-version", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                ]]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1":
                var title = movie()
                title["Chapters"] = [["StartPositionTicks": 0, "Name": "Preferred opening"]]
                return coreResponse(request, json: title)
            case "/Users/user-1/Items/missing-version": return coreResponse(request, status: 404)
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "missing-version", "MediaSources": [[
                    "Id": "missing-version", "SupportsDirectPlay": true, "SupportsTranscoding": false,
                ]]])
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/MediaSegments/source-episode":
                return coreResponse(request, json: ["Items": [
                    ["Type": "Intro", "StartTicks": 104_700_000, "EndTicks": 1_005_640_000],
                    ["Type": "Outro", "StartTicks": 13_305_800_000, "EndTicks": 13_906_530_000],
                    ["Type": "Unknown", "StartTicks": 20, "EndTicks": 30],
                    ["Type": "Recap", "StartTicks": 50, "EndTicks": 50],
                    NSNull(),
                ]])
            case "/Shows/series-1/Episodes":
                return coreResponse(request, json: page(items: [current, following]))
            default: return coreResponse(request, status: 404)
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
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Users/user-1/Items/movie-1": return coreResponse(request, json: movie())
            case "/Items/movie-1/PlaybackInfo":
                return coreResponse(request, json: ["PlaySessionId": "vtt", "MediaSources": [[
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
            default: return coreResponse(request, status: 404)
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

    func testServerMediaRecoveryKeepsTheApprovedHLSRequestAndSession() async throws {
        let saved = syntheticKeychainSession()
        let store = LuminaStore(client: LuminaClient(configuration: mockConfiguration()), credentials: TestCredentials(session: saved))
        let requests = RequestRecorder()
        MockURLProtocol.handler = { request in
            requests.append(request)
            switch request.url!.path {
            case "/Users/Me": return coreResponse(request, json: ["Id": "user-1", "Name": "member"])
            case "/Items", "/UserItems/Resume", "/Shows/NextUp": return coreResponse(request, json: page(items: []))
            case "/Videos/source-4k/master.m3u8":
                return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                        headerFields: ["Content-Type": "application/vnd.apple.mpegurl"])!, Data("""
                #EXTM3U
                #EXT-X-STREAM-INF:BANDWIDTH=24000000
                hls/session-4k/index.m3u8
                """.utf8))
            default: return coreResponse(request, status: 404)
            }
        }
        await store.restore()
        let item = try JSONDecoder().decode(MediaItem.self, from: json(movie()))
        let master = PlaybackRequest(
            id: "approved-session", item: item,
            url: URL(string: "http://127.0.0.1:8765/Videos/source-4k/master.m3u8?ApiKey=playback-token")!,
            startPositionSeconds: 11.25, mediaSourceID: "source-4k", isTranscoding: true,
            methodLabel: "HLS", bitRate: 24_000_000, sourceContainer: "mkv",
            mediaStreams: [
                .init(index: 3, type: .audio, label: "English", codec: "eac3", channels: 6),
                .init(index: 7, type: .subtitle, label: "English", codec: "pgs"),
            ],
            audioStreamIndex: 3, subtitleStreamIndex: 7,
            chapters: [.init(id: 10_000_000, title: "Opening")], resolvedQuality: .automatic
        )

        let recoveredRequest = try await store.prepareServerMediaRecovery(
            after: master, error: NSError(domain: "CoreMediaErrorDomain", code: -12927)
        )
        let recovered = try XCTUnwrap(recoveredRequest)

        XCTAssertEqual(recovered.id, master.id)
        XCTAssertEqual(recovered.url.path, "/Videos/source-4k/hls/session-4k/index.m3u8")
        XCTAssertEqual(URLComponents(url: recovered.url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "playback-token")
        XCTAssertTrue(recovered.usesServerMediaRecovery)
        XCTAssertFalse(recovered.usesCompatibleVideo)
        XCTAssertEqual(recovered.mediaSourceID, master.mediaSourceID)
        XCTAssertEqual(recovered.startPositionSeconds, master.startPositionSeconds)
        XCTAssertEqual(recovered.resolvedQuality, master.resolvedQuality)
        XCTAssertEqual(recovered.audioStreamIndex, master.audioStreamIndex)
        XCTAssertEqual(recovered.subtitleStreamIndex, master.subtitleStreamIndex)
        XCTAssertEqual(recovered.mediaStreams, master.mediaStreams)
        XCTAssertEqual(recovered.chapters, master.chapters)
        XCTAssertEqual(requests.all.map { $0.url?.path }.filter { $0?.contains("master.m3u8") == true }, ["/Videos/source-4k/master.m3u8"])

        let sidecarMaster = PlaybackRequest(
            id: "sidecar-session", item: item, url: master.url,
            startPositionSeconds: master.startPositionSeconds, mediaSourceID: master.mediaSourceID,
            isTranscoding: true, methodLabel: master.methodLabel,
            mediaStreams: [.init(index: 7, type: .subtitle, label: "English", codec: "vtt",
                                 isExternal: true,
                                 subtitleDeliveryURL: URL(string: "http://127.0.0.1:8765/Videos/source-4k/subtitles/7.vtt")!)],
            subtitleStreamIndex: 7
        )
        let sidecarRecovery = try await store.prepareServerMediaRecovery(
            after: sidecarMaster, error: NSError(domain: "CoreMediaErrorDomain", code: -12927)
        )
        XCTAssertNil(sidecarRecovery)

        let repeatedMaster = PlaybackRequest(
            id: master.id, item: master.item, url: master.url,
            startPositionSeconds: master.startPositionSeconds, mediaSourceID: master.mediaSourceID,
            isTranscoding: master.isTranscoding, methodLabel: master.methodLabel,
            usesServerMediaRecovery: true
        )
        let repeatedRecovery = try await store.prepareServerMediaRecovery(
            after: repeatedMaster, error: NSError(domain: "CoreMediaErrorDomain", code: -12927)
        )
        XCTAssertNil(repeatedRecovery)
        let transportRecovery = try await store.prepareServerMediaRecovery(
            after: master, error: URLError(.notConnectedToInternet)
        )
        XCTAssertNil(transportRecovery)
        let displayRecovery = try await store.prepareServerMediaRecovery(
            after: master, error: NSError(domain: AVFoundationErrorDomain, code: -11868)
        )
        XCTAssertNil(displayRecovery)
        XCTAssertEqual(requests.all.map { $0.url?.path }.filter { $0?.contains("master.m3u8") == true }, ["/Videos/source-4k/master.m3u8"])
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

}
