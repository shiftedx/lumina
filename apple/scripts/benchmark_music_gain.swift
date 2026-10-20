import Foundation

// Compile alongside Core sources so this measures the actual cached gain policy:
// swiftc -swift-version 6 -O -parse-as-library Core/Sources/LuminaCore/*.swift scripts/benchmark_music_gain.swift -o /tmp/lumina-gain-benchmark
// /tmp/lumina-gain-benchmark
@main
struct MusicGainBenchmark {
    @inline(never)
    static func previousGain(_ tracks: [MusicTrack], index: Int) -> Float {
        let track = tracks[index]
        let sameAlbum = track.albumID != nil && tracks.allSatisfy { $0.albumID == track.albumID }
        let ordered = zip(tracks, tracks.dropFirst()).allSatisfy { a, b in
            (a.disc ?? 1) < (b.disc ?? 1) || (a.disc ?? 1) == (b.disc ?? 1) && (a.number ?? Int.max) <= (b.number ?? Int.max)
        }
        return MusicPlaybackPolicy.gain(enabled: true, manual: track.gainIsManual, trackGain: track.gainDB,
            albumGain: track.albumGainDB, peak: track.peak, albumPeak: track.albumPeak, sequentialAlbum: sameAlbum && ordered)
    }

    static func main() throws {
        let data = Data("""
            {"id":"synthetic", "title":"Tone", "artist":"Test", "album_artist":"Test", "album":"Test",
             "album_id":"album", "audio_url":"", "gain_is_manual":false, "gain_db":-6, "album_gain_db":-3, "genres":[]}
            """.utf8)
        let track = try JSONDecoder().decode(MusicTrack.self, from: data)
        let clock = ContinuousClock()
        var checksum: Float = 0
        for count in [100, 1_000, 10_000] {
            let tracks = (0..<count).map { index in
                var value = track
                value.number = index + 1
                return value
            }
            var previous = [Double](), cached = [Double]()
            for _ in 0..<5 {
                let before = clock.measure {
                    for index in 0..<1_000 { checksum += previousGain(tracks, index: index % count) }
                }
                let after = clock.measure {
                    let gains = MusicPlaybackPolicy.queueGains(tracks, enabled: true, shuffle: false)
                    for index in 0..<1_000 { checksum += gains[index % count] }
                }
                previous.append(seconds(before)); cached.append(seconds(after))
            }
            let old = previous.sorted()[2] * 1_000, new = cached.sorted()[2] * 1_000
            print("\(count) tracks; 1000 gain applications; median of 5; previous=\(old) ms; cached incl. rebuild=\(new) ms")
        }
        print("checksum=\(checksum)")
    }

    static func seconds(_ duration: Duration) -> Double {
        let parts = duration.components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }
}
