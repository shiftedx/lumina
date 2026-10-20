import AVFoundation
import Darwin
import Foundation

// Fresh-process workload; generate synthetic media before launching this binary.
// From apple/: swiftc -swift-version 6 -O -parse-as-library Core/Sources/LuminaCore/*.swift scripts/benchmark_music_startup.swift -o /tmp/lumina-music-startup
// /tmp/lumina-music-startup /tmp/tone-120.flac 120 48000 2 0
// Arguments: file path or HTTP URL, duration, sample rate, queue count (1/2), seek offset.
@main
struct MusicStartupBenchmark {
    @MainActor
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count == 6, let duration = Double(arguments[2]), duration.isFinite, duration > 0,
              let rate = Int(arguments[3]), rate > 0, let count = Int(arguments[4]), (1...2).contains(count),
              let offset = Double(arguments[5]), offset.isFinite, offset >= 0, offset < duration else {
            throw NSError(domain: "MusicStartupBenchmark", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Expected: path-or-url duration sample-rate queue-count seek-offset"])
        }
        let url = arguments[1].contains("://") ? URL(string: arguments[1])! : URL(fileURLWithPath: arguments[1])
        let tracks = try (0..<count).map { index in
            let data = try JSONSerialization.data(withJSONObject: [
                "id": "synthetic-\(index)", "title": "Synthetic tone", "artist": "Test", "album_artist": "Test",
                "album": "Startup proof", "album_id": "synthetic-album", "number": index + 1,
                "audio_url": "", "duration": duration, "sample_rate": rate, "channels": 2,
                "gain_is_manual": false, "gain_db": -6, "album_gain_db": -3, "genres": [],
            ])
            return try JSONDecoder().decode(MusicTrack.self, from: data)
        }
        let initial = try memory()
        let initialCPU = try cpuMilliseconds()
        var resolutions = 0
        let transport = NativeMusicTransport(tracks: tracks, index: 0, position: offset) { index in
            resolutions += 1
            return PlaybackRequest(id: "synthetic-\(index)", item: tracks[index].mediaItem, url: url,
                startPositionSeconds: offset, mediaSourceID: "synthetic", isTranscoding: false, methodLabel: "Benchmark")
        }
        defer { transport.stop() }
        transport.configure(repeatMode: "off", crossfade: 3, normalize: true, volume: 0)
        let clock = ContinuousClock()
        let started = clock.now
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 10)
        let latency = started.duration(to: clock.now)
        guard confirmed, transport.mode == .decoded else {
            throw NSError(domain: "MusicStartupBenchmark", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "The workload did not render through the audio engine"])
        }
        // Include successor preparation and two seconds of real engine playback.
        try await Task.sleep(for: .seconds(2))
        guard resolutions == count, transport.state.playing, transport.state.error == nil else {
            throw NSError(domain: "MusicStartupBenchmark", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "The workload did not retain the expected active queue"])
        }
        let final = try memory()
        let results: [String: Any] = [
            "duration_seconds": duration, "sample_rate": rate, "queue_count": count, "offset_seconds": offset,
            "startup_ms": milliseconds(latency), "source_resolutions": resolutions,
            "active_index": transport.state.index, "position_seconds": transport.state.position,
            "baseline_footprint_bytes": initial.ri_phys_footprint,
            "peak_footprint_bytes": final.ri_lifetime_max_phys_footprint,
            "active_footprint_bytes": final.ri_phys_footprint,
            "workload_cpu_ms": try cpuMilliseconds() - initialCPU,
        ]
        print(String(decoding: try JSONSerialization.data(withJSONObject: results, options: .sortedKeys), as: UTF8.self))
    }

    static func memory() throws -> rusage_info_v4 {
        var info = rusage_info_v4()
        let status = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) {
                proc_pid_rusage(getpid(), RUSAGE_INFO_V4, $0)
            }
        }
        guard status == 0 else { throw POSIXError(.EIO) }
        return info
    }

    static func cpuMilliseconds() throws -> Double {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { throw POSIXError(.EIO) }
        return Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1_000
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1_000
    }

    static func milliseconds(_ duration: Duration) -> Double {
        Double(duration.components.seconds) * 1_000 + Double(duration.components.attoseconds) / 1e15
    }
}
