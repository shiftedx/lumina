import AVFoundation
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

    let directMP4: [Codec: Set<Range>]
    let directMP4BitDepth: [Codec: Int]
    let hls: [Codec: Set<Range>]
    let hlsBitDepth: [Codec: Int]

    /// The established compatibility request never relies on a modern decoder
    /// or HDR output route.
    static let compatibility = Self(
        directMP4: [.h264: [.sdr]],
        directMP4BitDepth: [.h264: 8],
        hls: [.h264: [.sdr]],
        hlsBitDepth: [.h264: 8]
    )

    static func current() -> Self {
        var direct = compatibility.directMP4
        var directBitDepth = compatibility.directMP4BitDepth
        var hls = compatibility.hls
        var hlsBitDepth = compatibility.hlsBitDepth
        let hdr = AVPlayer.eligibleForHDRPlayback

        if hardwareDecode(kCMVideoCodecType_HEVC),
           playableMP4("hvc1.2.4.L153.B0") {
            direct[.hevc] = ranges(main10: true, hdr: hdr)
            directBitDepth[.hevc] = 10
            // Apple documents native fMP4 HEVC HDR10/HLG HLS. This is a
            // separate, deliberately conservative HLS capability claim.
            hls[.hevc] = ranges(main10: true, hdr: hdr)
            hlsBitDepth[.hevc] = 10
        }

        let av1Main8 = hardwareDecode(av1Codec) && playableMP4("av01.0.13M.08")
        let av1Main10 = hardwareDecode(av1Codec) && playableMP4("av01.0.13M.10")
        if av1Main8 || av1Main10 {
            direct[.av1] = ranges(main10: av1Main10, hdr: hdr)
            directBitDepth[.av1] = av1Main10 ? 10 : 8
            // Apple publishes fMP4 AV1 HLS examples that require AV1
            // hardware. This is eligibility policy based on documented HLS
            // support plus the exact MP4 codec query, not an observed HLS
            // playback proof; startup retains the bounded compatibility retry.
            hls[.av1] = ranges(main10: av1Main10, hdr: hdr)
            hlsBitDepth[.av1] = av1Main10 ? 10 : 8
        }
        // `isPlayableExtendedMIMEType` answers an asset/container query, not
        // an HLS MIME type. The AV1 HLS policy above rests on Apple's separate
        // documented fMP4 AV1 HLS support, plus this exact codec query.
        return Self(directMP4: direct, directMP4BitDepth: directBitDepth, hls: hls, hlsBitDepth: hlsBitDepth)
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

    private static func hardwareDecode(_ codec: CMVideoCodecType) -> Bool {
        VTIsHardwareDecodeSupported(codec)
    }

    private static func playableMP4(_ codec: String) -> Bool {
        AVURLAsset.isPlayableExtendedMIMEType("video/mp4; codecs=\"\(codec),mp4a.40.2\"")
    }

    private static func ranges(main10: Bool, hdr: Bool) -> Set<Range> {
        guard main10 && hdr else { return [.sdr] }
        return [.sdr, .hdr10, .hlg]
    }

    /// Only codec/container failures may request the one compatibility retry.
    /// Network, authorization, cancellation, and transient resource failures
    /// must retain their ordinary error behavior.
    static func isFormatFailure(_ error: Error) -> Bool {
        if let selection = error as? NativeVideoSource.SelectionError,
           case .unsupportedAudioStream = selection {
            return true
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
