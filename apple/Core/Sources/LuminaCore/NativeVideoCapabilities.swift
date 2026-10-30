import AVFoundation
import Foundation
import VideoToolbox

/// Delivery capabilities observed from the current native AVFoundation stack.
///
/// These are intentionally delivery-specific. AV1 fMP4 HLS is a documented
/// eligibility policy, while AV1 direct MP4 uses an exact container/codec
/// query. HDR is advertised only while AVPlayer can present HDR.
struct NativeVideoCapabilities: Equatable, Sendable {
    enum Codec: String, CaseIterable, Sendable {
        case h264
        case hevc
        case av1
    }

    enum Range: String, CaseIterable, Sendable {
        case sdr = "SDR"
        case hdr10 = "HDR10"
        case hlg = "HLG"
    }

    /// MP4 codec queries gate independently qualified original delivery paths.
    /// Dolby Vision must not broaden the server's HLS/transcode ranges.
    struct DolbyVisionProfile: Equatable, Sendable {
        let profile: Int
        let compatibilityIDs: [Int]
        let maxLevel: Int
    }

    /// A packaging-only conversion. It does not authorize original Profile 7
    /// delivery, an HLS rendition, or distinguish MEL from FEL.
    struct DolbyVisionPackagingConversion: Equatable, Sendable {
        let sourceProfile: Int
        let sourceCompatibilityIDs: [Int]
        let targetProfile: Int
        let targetCompatibilityID: Int
        let maxLevel: Int
        let enhancementLayerDisposition: String
    }

    let directMP4: [Codec: Set<Range>]
    let directMP4BitDepth: [Codec: Int]
    let hls: [Codec: Set<Range>]
    let hlsBitDepth: [Codec: Int]
    let directMP4DolbyVisionProfiles: [DolbyVisionProfile]
    let matroskaDolbyVisionPackagingConversions: [DolbyVisionPackagingConversion]
    /// M2TS pair conversion is a separately qualified transport path. It is
    /// never inferred from the Matroska declaration at the protocol boundary.
    let transportDolbyVisionPackagingConversions: [DolbyVisionPackagingConversion]

    init(directMP4: [Codec: Set<Range>], directMP4BitDepth: [Codec: Int],
         hls: [Codec: Set<Range>], hlsBitDepth: [Codec: Int],
         directMP4DolbyVisionProfiles: [DolbyVisionProfile] = [],
         matroskaDolbyVisionPackagingConversions: [DolbyVisionPackagingConversion] = [],
         transportDolbyVisionPackagingConversions: [DolbyVisionPackagingConversion] = []) {
        self.directMP4 = directMP4
        self.directMP4BitDepth = directMP4BitDepth
        self.hls = hls
        self.hlsBitDepth = hlsBitDepth
        self.directMP4DolbyVisionProfiles = directMP4DolbyVisionProfiles
        self.matroskaDolbyVisionPackagingConversions = matroskaDolbyVisionPackagingConversions
        self.transportDolbyVisionPackagingConversions = transportDolbyVisionPackagingConversions
    }

    /// The established compatibility request never relies on a modern decoder
    /// or HDR output route.
    static let compatibility = Self(
        directMP4: [.h264: [.sdr]],
        directMP4BitDepth: [.h264: 8],
        hls: [.h264: [.sdr]],
        hlsBitDepth: [.h264: 8],
        directMP4DolbyVisionProfiles: [],
        matroskaDolbyVisionPackagingConversions: [],
        transportDolbyVisionPackagingConversions: []
    )

    static func current() -> Self {
        let queriedCodecs = Self.mp4CodecQueries.filter(playableMP4)
        return from(RuntimeProbe(
            hevcHardwareDecode: hardwareDecode(kCMVideoCodecType_HEVC),
            dolbyVisionHardwareDecode: hardwareDecode(kCMVideoCodecType_DolbyVisionHEVC),
            av1HardwareDecode: hardwareDecode(av1Codec),
            hdrEligible: AVPlayer.eligibleForHDRPlayback,
            dolbyVisionRoute: dolbyVisionRouteAvailable(),
            p81Supported: p81RuntimeSupported(),
            converterAvailable: NativeVideoSource.supportsDolbyVisionProfile7Conversion,
            playableMP4Codecs: Set(queriedCodecs)
        ))
    }

    /// Injection keeps the capability policy table-testable without claiming
    /// that a simulator's display route is a physical Dolby Vision result.
    struct RuntimeProbe: Sendable {
        let hevcHardwareDecode: Bool
        let dolbyVisionHardwareDecode: Bool
        let av1HardwareDecode: Bool
        let hdrEligible: Bool
        let dolbyVisionRoute: Bool
        let p81Supported: Bool
        let converterAvailable: Bool
        let playableMP4Codecs: Set<String>
    }

    static func from(_ probe: RuntimeProbe) -> Self {
        var direct = compatibility.directMP4
        var directBitDepth = compatibility.directMP4BitDepth
        var hls = compatibility.hls
        var hlsBitDepth = compatibility.hlsBitDepth
        let main10 = probe.hevcHardwareDecode && probe.playableMP4Codecs.contains(hevcMain10Codec)

        if main10 {
            direct[.hevc] = ranges(main10: true, hdr: probe.hdrEligible)
            directBitDepth[.hevc] = 10
            // Apple documents native fMP4 HEVC HDR10/HLG HLS. This is a
            // separate, deliberately conservative HLS capability claim.
            hls[.hevc] = ranges(main10: true, hdr: probe.hdrEligible)
            hlsBitDepth[.hevc] = 10
        }

        let av1Main8 = probe.av1HardwareDecode && probe.playableMP4Codecs.contains(av1Main8Codec)
        let av1Main10 = probe.av1HardwareDecode && probe.playableMP4Codecs.contains(av1Main10Codec)
        if av1Main8 || av1Main10 {
            direct[.av1] = ranges(main10: av1Main10, hdr: probe.hdrEligible)
            directBitDepth[.av1] = av1Main10 ? 10 : 8
            // Apple publishes fMP4 AV1 HLS examples that require AV1
            // hardware. This is eligibility policy based on documented HLS
            // support plus the exact MP4 codec query, not an observed HLS
            // playback proof; startup retains the bounded compatibility retry.
            hls[.av1] = ranges(main10: av1Main10, hdr: probe.hdrEligible)
            hlsBitDepth[.av1] = av1Main10 ? 10 : 8
        }
        // `isPlayableExtendedMIMEType` answers an asset/container query, not
        // an HLS MIME type. The AV1 HLS policy above rests on Apple's separate
        // documented fMP4 AV1 HLS support, plus this exact codec query.
        let dolbyVisionProfiles: [DolbyVisionProfile]
        if main10 && probe.hdrEligible && probe.dolbyVisionRoute {
            var profiles = [DolbyVisionProfile]()
            if probe.dolbyVisionHardwareDecode,
               let level = highestDolbyVisionLevel(profile: 5, playable: probe.playableMP4Codecs) {
                profiles.append(DolbyVisionProfile(profile: 5, compatibilityIDs: [0], maxLevel: level))
            }
            if let level = highestDolbyVisionLevel(profile: 8, playable: probe.playableMP4Codecs) {
                if probe.p81Supported {
                    profiles.append(DolbyVisionProfile(profile: 8, compatibilityIDs: [1], maxLevel: level))
                }
                profiles.append(DolbyVisionProfile(profile: 8, compatibilityIDs: [4], maxLevel: level))
            }
            dolbyVisionProfiles = profiles
        } else {
            dolbyVisionProfiles = []
        }
        let matroskaConversions: [DolbyVisionPackagingConversion]
        if probe.converterAvailable,
           let p81 = dolbyVisionProfiles.first(where: { $0.profile == 8 && $0.compatibilityIDs == [1] }) {
            matroskaConversions = [DolbyVisionPackagingConversion(
                sourceProfile: 7,
                sourceCompatibilityIDs: [6],
                targetProfile: 8,
                targetCompatibilityID: 1,
                maxLevel: p81.maxLevel,
                enhancementLayerDisposition: "dropped"
            )]
        } else {
            matroskaConversions = []
        }
        return Self(directMP4: direct, directMP4BitDepth: directBitDepth, hls: hls,
                    hlsBitDepth: hlsBitDepth, directMP4DolbyVisionProfiles: dolbyVisionProfiles,
                    matroskaDolbyVisionPackagingConversions: matroskaConversions,
                    transportDolbyVisionPackagingConversions: matroskaConversions)
    }

    func ranges(for codec: Codec, delivery: Delivery) -> Set<Range> {
        switch delivery {
        case .directMP4: directMP4[codec] ?? []
        case .hls: hls[codec] ?? []
        }
    }

    func bitDepth(for codec: Codec, delivery: Delivery) -> Int? {
        switch delivery {
        case .directMP4: directMP4BitDepth[codec]
        case .hls: hlsBitDepth[codec]
        }
    }

    enum Delivery: Sendable {
        case directMP4
        case hls
    }

    private static let av1Codec: CMVideoCodecType = 0x6176_3031 // `av01`
    private static let hevcMain10Codec = "hvc1.2.4.L153.B0"
    private static let av1Main8Codec = "av01.0.13M.08"
    private static let av1Main10Codec = "av01.0.13M.10"
    private static let mp4CodecQueries = [hevcMain10Codec, av1Main8Codec, av1Main10Codec]
        + (1...9).flatMap { level in
            [dolbyVisionCodec(profile: 5, level: level), dolbyVisionCodec(profile: 8, level: level)]
        }

    private static func hardwareDecode(_ codec: CMVideoCodecType) -> Bool {
        VTIsHardwareDecodeSupported(codec)
    }

    private static func playableMP4(_ codec: String) -> Bool {
        AVURLAsset.isPlayableExtendedMIMEType("video/mp4; codecs=\"\(codec),mp4a.40.2\"")
    }

    private static func highestDolbyVisionLevel(profile: Int, playable: Set<String>) -> Int? {
        (1...9).reversed().first { playable.contains(dolbyVisionCodec(profile: profile, level: $0)) }
    }

    private static func dolbyVisionCodec(profile: Int, level: Int) -> String {
        String(format: "dvh1.%02d.%02d", profile, level)
    }

    private static func dolbyVisionRouteAvailable() -> Bool {
        #if os(iOS) || os(tvOS)
        // `eligibleForHDRPlayback` is generic. This deprecated API remains
        // AVFoundation's public route-specific Dolby Vision signal.
        return AVPlayer.availableHDRModes.contains(.dolbyVision)
        #else
        return false
        #endif
    }

    private static func p81RuntimeSupported() -> Bool {
        #if os(iOS)
        if #available(iOS 27, *) { return true }
        return false
        #elseif os(tvOS)
        if #available(tvOS 18, *) { return true }
        return false
        #else
        return false
        #endif
    }

    private static func ranges(main10: Bool, hdr: Bool) -> Set<Range> {
        guard main10 && hdr else { return [.sdr] }
        return [.sdr, .hdr10, .hlg]
    }

    /// Only codec/container failures may request the one compatibility retry.
    /// Network, authorization, cancellation, and transient resource failures
    /// must retain their ordinary error behavior.
    static func isFormatFailure(_ error: Error) -> Bool {
        if let selection = error as? NativeVideoSource.SelectionError {
            switch selection {
            case .unsupportedAudioStream, .invalidDolbyVisionConversion, .subtitleMasterUnavailable:
                return true
            default:
                return false
            }
        }
        if error is CancellationError { return false }
        var current: NSError? = error as NSError
        var foundFormatFailure = false
        for _ in 0..<4 {
            guard let node = current else { break }
            // An AVFoundation wrapper can carry a specific decoder error over
            // a URL/auth failure. Transport and authorization always win.
            if node.domain == NSURLErrorDomain ||
                (node.domain == AVFoundationErrorDomain && [
                    AVError.Code.contentIsNotAuthorized.rawValue,
                    AVError.Code.contentIsProtected.rawValue,
                ].contains(node.code)) {
                return false
            }
            // Observed when AVPlayer rejects a locally packaged HLS master
            // before its first frame. Keep this exact code narrow; unrelated
            // CoreMedia failures must not become compatibility retries.
            if node.domain == "CoreMediaErrorDomain", node.code == -12927 {
                foundFormatFailure = true
            }
            if node.domain == AVFoundationErrorDomain {
                let formatCodes = [
                    AVError.Code.decoderNotFound.rawValue,
                    AVError.Code.fileFormatNotRecognized.rawValue,
                    AVError.Code.formatUnsupported.rawValue,
                    AVError.Code.fileFailedToParse.rawValue,
                    AVError.Code.failedToParse.rawValue,
                ]
                foundFormatFailure = foundFormatFailure || formatCodes.contains(node.code)
            }
            current = node.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return foundFormatFailure
    }
}
