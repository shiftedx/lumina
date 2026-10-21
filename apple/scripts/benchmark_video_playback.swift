import AVFoundation
import CoreVideo
import Darwin
import Foundation
#if canImport(LuminaCore)
import LuminaCore
#endif

@MainActor
private final class ProgressProbe {
    var positions = [Double]()
}

// Synthetic video qualification: actual frames, advancing time and a seek, rather than readiness alone.
// Compile with swiftc -swift-version 6 -O -parse-as-library; accepts a file path or loopback HTTP URL.
@main
struct VideoPlaybackBenchmark {
    @MainActor
    static func main() async throws {
        guard (2...3).contains(CommandLine.arguments.count) else { throw POSIXError(.EINVAL) }
        let input = CommandLine.arguments[1]
        let url = input.contains("://") ? URL(string: input)! : URL(fileURLWithPath: input)
        let started = ContinuousClock.now
        let cpu = cpuMilliseconds()
        let asset = AVURLAsset(url: url)
        let item: AVPlayerItem
#if canImport(LuminaCore)
        let videoSource = NativeVideoSource()
        item = try await videoSource.prepare(url: url, usesClientDemux: CommandLine.arguments.count == 3)
#else
        item = AVPlayerItem(asset: asset)
#endif
        let output = AVPlayerItemVideoOutput(pixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        ])
        item.add(output)
        let player = AVPlayer(playerItem: item)
        player.volume = 0
        let progressProbe = ProgressProbe()
        var progressObserver: Any?
        if ProcessInfo.processInfo.environment["LUMINA_VIDEO_PROGRESS_PROBE"] == "1" {
#if canImport(LuminaCore)
            progressObserver = player.addPeriodicTimeObserver(forInterval: NativeVideoSource.progressObservationInterval, queue: .main) { time in
                Task { @MainActor in progressProbe.positions.append(time.seconds) }
            }
#else
            throw POSIXError(.EINVAL)
#endif
        }
        defer { if let progressObserver { player.removeTimeObserver(progressObserver) } }
        let waitStarted = ContinuousClock.now
        var readinessPolls = 0
        if ProcessInfo.processInfo.environment["LUMINA_VIDEO_WAIT_MODE"] == "poll" {
            for _ in 0..<300 {
                readinessPolls += 1
                if item.status == .failed { throw item.error ?? POSIXError(.EIO) }
                if item.status == .readyToPlay { break }
                try await Task.sleep(for: .milliseconds(100))
            }
            guard item.status == .readyToPlay else { throw POSIXError(.ETIMEDOUT) }
        }
#if canImport(LuminaCore)
        if ProcessInfo.processInfo.environment["LUMINA_VIDEO_WAIT_MODE"] == "event" {
            try await NativeVideoSource.waitUntilReady(player)
        }
#endif
        let readinessWait = milliseconds(since: waitStarted)
        player.play()
        var firstFrame: Double?
        var frames = 0
        while ContinuousClock.now < started.advanced(by: .seconds(15)) {
            let time = item.currentTime()
            if output.hasNewPixelBuffer(forItemTime: time),
               output.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil {
                frames += 1
                if firstFrame == nil {
                    firstFrame = milliseconds(since: started)
                    print("FIRST_FRAME")
                    fflush(stdout)
                }
            }
            if item.status == .failed { throw item.error ?? POSIXError(.EIO) }
            if frames > 0, time.seconds >= 1 { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        guard let firstFrame, frames > 0, item.currentTime().seconds >= 1 else { throw POSIXError(.ETIMEDOUT) }
        guard item.tracks.contains(where: { $0.isEnabled && $0.assetTrack?.mediaType == .audio }) else {
            throw POSIXError(.ENODATA)
        }
        let startupCPU = cpuMilliseconds() - cpu
        var steady: [String: Any] = [:]
        if let seconds = Double(ProcessInfo.processInfo.environment["LUMINA_VIDEO_STEADY_SECONDS"] ?? ""), seconds > 0 {
            let begin = ContinuousClock.now
            let beginCPU = cpuMilliseconds()
            let beginPosition = item.currentTime().seconds
            var sampledFrames = 0
            var residentSamples = [residentBytes()]
            var cpuSamples: [Double] = [0]
            var nextMemorySample = begin.advanced(by: .seconds(1))
            while ContinuousClock.now < begin.advanced(by: .seconds(seconds)) {
                let time = item.currentTime()
                if output.hasNewPixelBuffer(forItemTime: time),
                   output.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil { sampledFrames += 1 }
                if item.status == .failed { throw item.error ?? POSIXError(.EIO) }
                if ContinuousClock.now >= nextMemorySample {
                    residentSamples.append(residentBytes())
                    cpuSamples.append(cpuMilliseconds() - beginCPU)
                    nextMemorySample = ContinuousClock.now.advanced(by: .seconds(1))
                }
                try await Task.sleep(for: .milliseconds(10))
            }
            residentSamples.append(residentBytes())
            cpuSamples.append(cpuMilliseconds() - beginCPU)
            let elapsed = milliseconds(since: begin)
            let endPosition = item.currentTime().seconds
            let advanced = endPosition - beginPosition
            guard advanced >= seconds * 0.9, sampledFrames > 0 else { throw POSIXError(.ETIMEDOUT) }
            steady = ["steady_cpu_ms": cpuMilliseconds() - beginCPU,
                      "steady_wall_ms": elapsed, "steady_advanced_seconds": advanced,
                      "steady_sampled_frames": sampledFrames,
                      "steady_rss_start_bytes": residentSamples[0],
                      "steady_rss_end_bytes": residentSamples.last!,
                      "steady_rss_peak_bytes": residentSamples.max()!,
                      "steady_rss_samples_bytes": residentSamples,
                      "steady_cpu_samples_ms": cpuSamples]
            player.pause()
            let pauseCPU = cpuMilliseconds()
            try await Task.sleep(for: .seconds(2))
            steady["paused_two_seconds_cpu_ms"] = cpuMilliseconds() - pauseCPU
            if progressObserver != nil {
                steady["steady_progress_callbacks"] = progressProbe.positions.filter { $0 > beginPosition && $0 <= endPosition }.count
            }
            player.play()
        }
        let seekStarted = ContinuousClock.now
        let sought = await player.seek(to: CMTime(seconds: 6, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        guard sought else { throw POSIXError(.EIO) }
        var seekFrame = false
        while ContinuousClock.now < seekStarted.advanced(by: .seconds(15)) {
            let time = item.currentTime()
            if time.seconds >= 6, output.hasNewPixelBuffer(forItemTime: time),
               output.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil { seekFrame = true }
            if seekFrame, time.seconds >= 6.2 { break }
            if item.status == .failed { throw item.error ?? POSIXError(.EIO) }
            try await Task.sleep(for: .milliseconds(10))
        }
        guard seekFrame, item.currentTime().seconds >= 6.2 else { throw POSIXError(.ETIMEDOUT) }
        player.pause()
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        var result: [String: Any] = [
            "native_peak_rss_bytes": usage.ru_maxrss,
            "readiness_wait_ms": readinessWait, "readiness_polls": readinessPolls,
            "first_frame_ms": firstFrame, "startup_plus_one_second_cpu_ms": startupCPU,
            "seek_plus_200ms_ms": milliseconds(since: seekStarted), "decoded_frames": frames,
        ]
        result.merge(steady) { _, new in new }
        // AVAssetReader needs a local file. Qualify the same compressed audio separately from HTTP timing.
        if url.isFileURL && url.pathExtension != "mkv" {
            guard let audio = try await asset.loadTracks(withMediaType: .audio).first else { throw POSIXError(.ENODATA) }
            let reader = try AVAssetReader(asset: asset)
            let output = AVAssetReaderTrackOutput(track: audio, outputSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVLinearPCMIsFloatKey: true,
                AVLinearPCMBitDepthKey: 32, AVLinearPCMIsNonInterleaved: false,
            ])
            reader.add(output)
            guard reader.startReading(), let sample = output.copyNextSampleBuffer(),
                  CMSampleBufferGetNumSamples(sample) > 0 else { throw reader.error ?? POSIXError(.EIO) }
            reader.cancelReading()
        }
        print(String(decoding: try JSONSerialization.data(withJSONObject: result, options: .sortedKeys), as: UTF8.self))
        player.replaceCurrentItem(with: nil)
#if canImport(LuminaCore)
        await videoSource.stop().value
#endif
    }

    static func milliseconds(since start: ContinuousClock.Instant) -> Double {
        let elapsed = start.duration(to: .now).components
        return Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1e15
    }

    static func residentBytes() -> UInt64 {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size / MemoryLayout<integer_t>.size)
        let status = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        precondition(status == KERN_SUCCESS)
        return info.resident_size
    }

    static func cpuMilliseconds() -> Double {
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        return Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000
    }
}
