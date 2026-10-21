import AVFoundation
import CoreVideo
import Darwin
import Foundation

// Synthetic video qualification: actual frames, advancing time and a seek, rather than readiness alone.
// Compile with swiftc -swift-version 6 -O -parse-as-library; accepts a file path or loopback HTTP URL.
@main
struct VideoPlaybackBenchmark {
    @MainActor
    static func main() async throws {
        guard CommandLine.arguments.count == 2 else { throw POSIXError(.EINVAL) }
        let input = CommandLine.arguments[1]
        let url = input.contains("://") ? URL(string: input)! : URL(fileURLWithPath: input)
        let asset = AVURLAsset(url: url)
        let item = AVPlayerItem(asset: asset)
        let output = AVPlayerItemVideoOutput(pixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        ])
        item.add(output)
        let player = AVPlayer(playerItem: item)
        player.volume = 0
        let started = ContinuousClock.now
        let cpu = cpuMilliseconds()
        player.play()
        var firstFrame: Double?
        var frames = 0
        while ContinuousClock.now < started.advanced(by: .seconds(15)) {
            let time = item.currentTime()
            if output.hasNewPixelBuffer(forItemTime: time),
               output.copyPixelBuffer(forItemTime: time, itemTimeForDisplay: nil) != nil {
                frames += 1
                if firstFrame == nil { firstFrame = milliseconds(since: started) }
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
        let result: [String: Any] = [
            "native_peak_rss_bytes": usage.ru_maxrss,
            "first_frame_ms": firstFrame, "startup_plus_one_second_cpu_ms": startupCPU,
            "seek_plus_200ms_ms": milliseconds(since: seekStarted), "decoded_frames": frames,
        ]
        // AVAssetReader needs a local file. Qualify the same compressed audio separately from HTTP timing.
        if url.isFileURL {
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
    }

    static func milliseconds(since start: ContinuousClock.Instant) -> Double {
        let elapsed = start.duration(to: .now).components
        return Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1e15
    }

    static func cpuMilliseconds() -> Double {
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        return Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000
    }
}
