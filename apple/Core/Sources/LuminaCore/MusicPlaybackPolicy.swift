import Foundation

/// The same gain and bounded-decoding contract used by the web listening session.
public enum MusicPlaybackPolicy {
    public static let maximumTrackBytes = 128 * 1024 * 1024

    public static func canBuffer(duration: Double?, sampleRate: Int?, channels: Int?) -> Bool {
        guard let duration, duration.isFinite, duration > 0,
              let sampleRate, sampleRate > 0, let channels, (1...2).contains(channels) else { return false }
        // Include both source PCM and the hardware-rate stereo conversion estimate.
        return duration * (Double(sampleRate) * Double(channels) + 48_000 * 2) * 4 + 8 <= Double(maximumTrackBytes)
    }

    public static func gain(enabled: Bool, manual: Bool, trackGain: Double?, albumGain: Double?,
                            peak: Double?, albumPeak: Double?, sequentialAlbum: Bool) -> Float {
        guard enabled else { return 1 }
        let album = !manual && sequentialAlbum && albumGain != nil
        let db = album ? albumGain : trackGain
        var value = db.flatMap { $0.isFinite ? pow(10, $0 / 20) : nil } ?? 1
        if let limit = album ? albumPeak : peak, limit.isFinite, limit > 0 { value = min(value, 1 / limit) }
        return value.isFinite ? Float(min(value, Double(Float.greatestFiniteMagnitude))) : 1
    }

    static func queueGains(_ tracks: [MusicTrack], enabled: Bool, shuffle: Bool) -> [Float] {
        let albumID = tracks.first?.albumID
        let sameAlbum = albumID != nil && tracks.allSatisfy { $0.albumID == albumID }
        let ordered = zip(tracks, tracks.dropFirst()).allSatisfy { a, b in
            (a.disc ?? 1) < (b.disc ?? 1) || (a.disc ?? 1) == (b.disc ?? 1) && (a.number ?? Int.max) <= (b.number ?? Int.max)
        }
        let sequential = sameAlbum && ordered && !shuffle
        return tracks.map { track in
            gain(enabled: enabled, manual: track.gainIsManual, trackGain: track.gainDB,
                 albumGain: track.albumGainDB, peak: track.peak, albumPeak: track.albumPeak, sequentialAlbum: sequential)
        }
    }

    public static func nextIndex(current: Int, count: Int, repeatMode: String) -> Int? {
        guard count > 0, (0..<count).contains(current) else { return nil }
        if repeatMode == "one" { return current }
        if current + 1 < count { return current + 1 }
        return repeatMode == "all" ? 0 : nil
    }
}
