import Foundation

/// Source-file facts; these never describe the decoder, processing engine or output device.
public struct MusicFileQuality: Codable, Hashable, Sendable {
    public let codec: String?
    public let bitrateKbps: Double?
    public let bitDepth: Int?
    public let sampleRate: Int?

    enum CodingKeys: String, CodingKey {
        case codec, bitrateKbps = "bitrate_kbps", bitDepth = "bit_depth", sampleRate = "sample_rate"
    }

    public var summary: String {
        [codec.map(Self.codecName) ?? "Format unavailable",
         Self.resolution(depths: bitDepth.map { [$0] } ?? [], rates: sampleRate.map { [$0] } ?? []),
         Self.bitrate(bitrateKbps)].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    static func codecName(_ codec: String) -> String { codec.hasPrefix("pcm_") ? "PCM" : codec.uppercased() }

    static func bitrate(_ rate: Double?) -> String {
        guard let rate, rate.isFinite, rate > 0 else { return "Bitrate unavailable" }
        return "\(rate < 1 ? "<1" : rate.formatted(.number.precision(.fractionLength(0)))) kbps"
    }

    static func resolution(depths: [Int], rates: [Int]) -> String {
        let depths = Array(Set(depths.filter { $0 > 0 })).sorted()
        let rates = Array(Set(rates.filter { $0 > 0 })).sorted()
        var parts: [String] = []
        if let first = depths.first, let last = depths.last {
            parts.append("\(first == last ? String(first) : "\(first)–\(last)")-bit")
        }
        if let first = rates.first, let last = rates.last {
            let start = (Double(first) / 1_000).formatted(.number.precision(.fractionLength(0...1)))
            let end = (Double(last) / 1_000).formatted(.number.precision(.fractionLength(0...1)))
            parts.append("\(first == last ? start : "\(start)–\(end)") kHz")
        }
        return parts.joined(separator: " / ")
    }
}

public struct MusicAlbumFileQuality: Codable, Hashable, Sendable {
    public let trackCount: Int
    public let codecs: [String]
    public let bitDepths: [Int]
    public let sampleRates: [Int]
    public let bitrateKbps: Double?
    public let formatsComplete: Bool

    enum CodingKeys: String, CodingKey {
        case trackCount = "track_count", codecs, bitDepths = "bit_depths", sampleRates = "sample_rates"
        case bitrateKbps = "bitrate_kbps", formatsComplete = "formats_complete"
    }

    public var formatSummary: String {
        let format = codecs.isEmpty ? "Format unavailable"
            : codecs.count > 1 ? "Mixed formats · \(codecs.map(MusicFileQuality.codecName).joined(separator: " + "))"
            : MusicFileQuality.codecName(codecs[0])
        return [format, MusicFileQuality.resolution(depths: codecs.count > 1 ? [] : bitDepths, rates: sampleRates),
                !formatsComplete && !codecs.isEmpty ? "Partial file metadata" : ""]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }
    public var bitrateSummary: String {
        MusicFileQuality.bitrate(bitrateKbps) + (bitrateKbps.map { $0.isFinite && $0 > 0 } == true ? " avg" : "")
    }
    public var bitDepthSummary: String {
        let depths = MusicFileQuality.resolution(depths: bitDepths, rates: [])
        return depths.isEmpty ? "Not reported" : depths + (codecs.count > 1 ? " (where reported)" : "")
    }
    public var sampleRateSummary: String {
        let rates = MusicFileQuality.resolution(depths: [], rates: sampleRates)
        return rates.isEmpty ? "Not reported" : rates
    }
    public var codecSummary: String { codecs.isEmpty ? "Unavailable" : codecs.map(MusicFileQuality.codecName).joined(separator: " + ") }
    public var accessibilitySummary: String { "\(formatSummary), \(bitrateSummary)" }
}

public struct MusicCollectionFileQuality: Decodable, Sendable {
    public let album: MusicAlbumFileQuality?
    public let albums: [String: MusicAlbumFileQuality]
    public let tracks: [String: MusicFileQuality]
}
