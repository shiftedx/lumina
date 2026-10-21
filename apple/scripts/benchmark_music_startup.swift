import AVFoundation
import Darwin
import Foundation

// Fresh-process workload; generate synthetic media before launching this binary.
// From apple/: swiftc -swift-version 6 -O -parse-as-library Core/Sources/LuminaCore/*.swift scripts/benchmark_music_startup.swift -o /tmp/lumina-music-startup
// /tmp/lumina-music-startup /tmp/tone-120.flac 120 48000 2 0
// Arguments: file path or HTTP URL, duration, sample rate, queue count (1/2), seek offset.
// Optional codec benchmarks source-loudness playback; pause-seconds/play-seconds select the measurement window.
@main
struct MusicStartupBenchmark {
    @MainActor
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard (6...9).contains(arguments.count), let duration = Double(arguments[2]), duration.isFinite, duration > 0,
              let rate = Int(arguments[3]), rate > 0, let count = Int(arguments[4]), (1...2).contains(count),
              let offset = Double(arguments[5]), offset.isFinite, offset >= 0, offset < duration else {
            throw NSError(domain: "MusicStartupBenchmark", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Expected: path-or-url duration sample-rate queue-count seek-offset [codec [pause-seconds [play-seconds]]]"])
        }
        let progressive = arguments.count >= 7
        let pauseSeconds = arguments.count >= 8 ? Double(arguments[7]) ?? -1 : 0
        let playSeconds = arguments.count == 9 ? Double(arguments[8]) ?? -1 : 2
        guard pauseSeconds.isFinite, pauseSeconds >= 0, playSeconds.isFinite, playSeconds > 0 else { throw POSIXError(.EINVAL) }
        let codec = progressive ? arguments[6] : "flac"
        let url = arguments[1].contains("://") ? URL(string: arguments[1])! : URL(fileURLWithPath: arguments[1])
        let tracks = try (0..<count).map { index in
            let data = try JSONSerialization.data(withJSONObject: [
                "codec": codec, "id": "synthetic-\(index)", "title": "Synthetic tone", "artist": "Test", "album_artist": "Test",
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
        transport.configure(repeatMode: "off", crossfade: progressive ? 0 : 3, normalize: !progressive, volume: 0)
        let clock = ContinuousClock()
        let started = clock.now
        await transport.start()
        let confirmed = await transport.confirmPlaying(timeout: 10)
        let latency = started.duration(to: clock.now)
        guard confirmed, transport.mode != .streaming else {
            throw NSError(domain: "MusicStartupBenchmark", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "The workload did not confirm native playback"])
        }
        let startup: [String: Any] = ["startup_ms": milliseconds(latency), "mode": String(describing: transport.mode)]
        let startupData = try JSONSerialization.data(withJSONObject: startup, options: .sortedKeys)
        FileHandle.standardOutput.write(Data("STARTUP ".utf8) + startupData + Data("\n".utf8))
        // The optional pause workload measures retained resources without rendering audio.
        if pauseSeconds > 0 { transport.pause() }
        let pausedPosition = transport.state.position
        try await Task.sleep(for: .seconds(pauseSeconds > 0 ? pauseSeconds : playSeconds))
        guard resolutions == count, transport.state.playing == (pauseSeconds == 0), transport.state.error == nil,
              pauseSeconds == 0 || transport.state.position == pausedPosition,
              pauseSeconds > 0 || transport.state.position >= pausedPosition + playSeconds - 0.5 else {
            throw NSError(domain: "MusicStartupBenchmark", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "The workload did not retain the expected active queue"])
        }
        let final = try memory()
        var results: [String: Any] = [
            "play_seconds": playSeconds, "pause_seconds": pauseSeconds, "mode": String(describing: transport.mode), "codec": codec, "duration_seconds": duration, "sample_rate": rate, "queue_count": count, "offset_seconds": offset,
            "startup_ms": milliseconds(latency), "source_resolutions": resolutions,
            "active_index": transport.state.index, "position_seconds": transport.state.position,
            "baseline_footprint_bytes": initial.ri_phys_footprint,
            "peak_footprint_bytes": final.ri_lifetime_max_phys_footprint,
            "active_footprint_bytes": final.ri_phys_footprint,
            "workload_cpu_ms": try cpuMilliseconds() - initialCPU,
        ]
        FileHandle.standardOutput.write(Data("MEASURED\n".utf8))
        if pauseSeconds > 0 {
            await transport.resume()
            guard await transport.confirmPlaying(timeout: 3) else { throw POSIXError(.EIO) }
        }
        if progressive, String(describing: transport.mode) == "progressive" {
            // Qualify range seeking and reuse after the measured CPU/footprint window.
            let seek = duration / 2
            await transport.seek(seek)
            let deadline = clock.now.advanced(by: .seconds(5))
            while transport.state.position < seek + 0.05, clock.now < deadline {
                try await Task.sleep(for: .milliseconds(50))
            }
            guard transport.state.position >= seek + 0.05, transport.state.error == nil else {
                throw NSError(domain: "MusicStartupBenchmark", code: 4,
                    userInfo: [NSLocalizedDescriptionKey: "Progressive seek did not render beyond the requested position"])
            }
            transport.pause()
            try await Task.sleep(for: .milliseconds(50))
            await transport.resume()
            guard await transport.confirmPlaying(timeout: 3), resolutions == count else {
                throw NSError(domain: "MusicStartupBenchmark", code: 5,
                    userInfo: [NSLocalizedDescriptionKey: "Progressive pause/resume did not reuse the source"])
            }
            results["seek_resume_verified"] = true
        }
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
