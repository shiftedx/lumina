import XCTest
@testable import LuminaCore

final class MusicPlaybackPolicyTests: XCTestCase {
    func testListeningGainPreservesAlbumDynamicsManualOverridesAndPeakSafety() {
        XCTAssertEqual(MusicPlaybackPolicy.gain(enabled: true, manual: false, trackGain: -6,
            albumGain: -3, peak: nil, albumPeak: nil, sequentialAlbum: true), Float(pow(10, -3.0 / 20)), accuracy: 0.0001)
        XCTAssertEqual(MusicPlaybackPolicy.gain(enabled: true, manual: true, trackGain: -6,
            albumGain: -3, peak: nil, albumPeak: nil, sequentialAlbum: true), Float(pow(10, -6.0 / 20)), accuracy: 0.0001)
        XCTAssertEqual(MusicPlaybackPolicy.gain(enabled: true, manual: false, trackGain: 12,
            albumGain: -3, peak: 2, albumPeak: nil, sequentialAlbum: false), 0.5)
        XCTAssertEqual(MusicPlaybackPolicy.gain(enabled: false, manual: true, trackGain: -12,
            albumGain: nil, peak: 2, albumPeak: nil, sequentialAlbum: false), 1)
    }

    func testQueueGainRefreshPreservesAlbumOrderShuffleAndManualOverrides() throws {
        let data = Data("""
            {"id":"synthetic", "title":"Tone", "artist":"Test", "album_artist":"Test", "album":"Test", "album_id":"album", "number":1,
             "audio_url":"", "gain_is_manual":false, "gain_db":-6, "album_gain_db":-3, "genres":[]}
            """.utf8)
        let first = try JSONDecoder().decode(MusicTrack.self, from: data)
        var second = first
        second.number = 2
        let album = Float(pow(10, -3.0 / 20)), track = Float(pow(10, -6.0 / 20))
        XCTAssertEqual(MusicPlaybackPolicy.queueGains([first, second], enabled: true, shuffle: false), [album, album])
        XCTAssertEqual(MusicPlaybackPolicy.queueGains([second, first], enabled: true, shuffle: false), [track, track])
        XCTAssertEqual(MusicPlaybackPolicy.queueGains([first, second], enabled: true, shuffle: true), [track, track])
        second.gainIsManual = true
        XCTAssertEqual(MusicPlaybackPolicy.queueGains([first, second], enabled: true, shuffle: false), [album, track])
        XCTAssertEqual(MusicPlaybackPolicy.queueGains([first, second], enabled: false, shuffle: false), [1, 1])
    }

    func testOrdinaryAlbumTracksCanPreloadButLongAndUnprobedSourcesMustStream() {
        XCTAssertTrue(MusicPlaybackPolicy.canBuffer(duration: 120, sampleRate: 48_000, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: 240, sampleRate: 48_000, channels: 2),
            "Source and converted PCM must both fit before downloading a complete track")
        XCTAssertTrue(MusicPlaybackPolicy.canBuffer(duration: 60, sampleRate: 192_000, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: 120, sampleRate: 192_000, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: 3_600, sampleRate: 48_000, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: 180, sampleRate: nil, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: .infinity, sampleRate: 48_000, channels: 2))
        XCTAssertFalse(MusicPlaybackPolicy.canBuffer(duration: 180, sampleRate: 48_000, channels: 8))
    }

    func testQueueTransitionsUsePositionsSoRepeatedTrackIDsDoNotSkipEntries() {
        // Two identical recordings at positions zero and one must still advance once.
        XCTAssertEqual(MusicPlaybackPolicy.nextIndex(current: 0, count: 2, repeatMode: "off"), 1)
        XCTAssertNil(MusicPlaybackPolicy.nextIndex(current: 1, count: 2, repeatMode: "off"))
        XCTAssertEqual(MusicPlaybackPolicy.nextIndex(current: 1, count: 2, repeatMode: "all"), 0)
        XCTAssertEqual(MusicPlaybackPolicy.nextIndex(current: 1, count: 2, repeatMode: "one"), 1)
    }
}
