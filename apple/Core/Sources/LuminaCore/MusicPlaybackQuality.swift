import AVFoundation

/// The format read from the active native asset, rather than inferred from its filename.
public struct MusicAudioFormat: Equatable, Sendable {
    public let codec: String
    public let sampleRate: Double
    public let bitDepth: Int?
    public let channels: Int
    public let bitRate: Int?

    init(_ format: AVAudioFormat, estimatedBitRate: Double? = nil) {
        let description = format.streamDescription.pointee
        switch description.mFormatID {
        case kAudioFormatFLAC: codec = "FLAC"
        case kAudioFormatAppleLossless: codec = "ALAC"
        case kAudioFormatMPEG4AAC, kAudioFormatMPEG4AAC_HE, kAudioFormatMPEG4AAC_HE_V2: codec = "AAC"
        case kAudioFormatMPEGLayer3: codec = "MP3"
        case kAudioFormatOpus: codec = "Opus"
        case kAudioFormatLinearPCM: codec = description.mFormatFlags & kAudioFormatFlagIsFloat != 0 ? "PCM Float" : "PCM"
        default: codec = "Audio"
        }
        sampleRate = format.sampleRate
        bitDepth = description.mBitsPerChannel > 0 ? Int(description.mBitsPerChannel) : Self.losslessBitDepth(format)
        channels = Int(format.channelCount)
        let rate = description.mFormatID == kAudioFormatLinearPCM
            ? format.sampleRate * Double(description.mBitsPerChannel) * Double(format.channelCount)
            : estimatedBitRate
        bitRate = rate.flatMap { $0.isFinite && $0 > 0 && $0 < Double(Int.max) ? Int($0.rounded()) : nil }
    }

    private static func losslessBitDepth(_ format: AVAudioFormat) -> Int? {
        let description = format.streamDescription.pointee
        if description.mFormatID == kAudioFormatAppleLossless {
            return [kAppleLosslessFormatFlag_16BitSourceData: 16, kAppleLosslessFormatFlag_20BitSourceData: 20,
                    kAppleLosslessFormatFlag_24BitSourceData: 24, kAppleLosslessFormatFlag_32BitSourceData: 32][description.mFormatFlags]
        }
        guard description.mFormatID == kAudioFormatFLAC, let cookie = format.magicCookie,
              cookie.count >= 42, cookie.count <= 4_096 else { return nil }
        let bytes = Array(cookie.prefix(64))
        let header: Int
        if bytes.starts(with: [0x66, 0x4c, 0x61, 0x43]) { header = 4 }
        else if bytes.count >= 50, Array(bytes[4..<8]) == [0x64, 0x66, 0x4c, 0x61],
                bytes[0..<4].reduce(0, { $0 << 8 | Int($1) }) == cookie.count,
                bytes[8..<12].allSatisfy({ $0 == 0 }) { header = 12 }
        else { return nil }
        guard bytes.count >= header + 38, bytes[header] & 0x7f == 0,
              Array(bytes[(header + 1)..<(header + 4)]) == [0, 0, 34] else { return nil }
        // RFC 9639 §8.2: STREAMINFO's packed sample rate/channels/bit-depth/sample-count field.
        let packed = bytes[(header + 14)..<(header + 22)].reduce(UInt64(0)) { $0 << 8 | UInt64($1) }
        let depth = Int((packed >> 36) & 31) + 1
        guard Double(packed >> 44) == format.sampleRate,
              Int((packed >> 41) & 7) + 1 == Int(format.channelCount), (4...32).contains(depth) else { return nil }
        return depth
    }

    public var summary: String {
        var parts = [codec]
        if let bitDepth { parts.append("\(bitDepth)-bit") }
        if sampleRate.isFinite, sampleRate > 0 {
            parts.append("\((sampleRate / 1_000).formatted(.number.precision(.fractionLength(0...1)))) kHz")
        }
        if let bitRate { parts.append("\(Int((Double(bitRate) / 1_000).rounded()).formatted()) kbps") }
        return parts.joined(separator: " · ")
    }
}

public struct MusicPlaybackQuality: Equatable, Sendable {
    public let input: MusicAudioFormat
    /// The actual engine connection format when local processing is active.
    public let processed: MusicAudioFormat?

    public var playback: MusicAudioFormat { processed ?? input }
}
