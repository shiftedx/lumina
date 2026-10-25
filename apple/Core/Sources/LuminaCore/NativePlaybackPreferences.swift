import Foundation
import Observation

/// Device-local subtitle behavior. Automatic deliberately leaves the server and AVFoundation
/// defaults in control; the other modes produce an explicit source-track request when possible.
public enum NativeSubtitleMode: String, CaseIterable, Codable, Hashable, Identifiable, Sendable {
    case automatic
    case off
    case always
    case forcedOnly

    public var id: Self { self }

    public var label: String {
        switch self {
        case .automatic: "Automatic"
        case .off: "Off"
        case .always: "Always"
        case .forcedOnly: "Forced only"
        }
    }
}

/// Per-device native playback choices. These values never leave the device as account settings:
/// they are translated into a fresh server playback decision for every video request.
@MainActor @Observable public final class NativePlaybackPreferences {
    public static let qualityKey = "native.playback.preferences.v1.quality"
    public static let audioLanguagesKey = "native.playback.preferences.v1.audioLanguages"
    public static let subtitleLanguagesKey = "native.playback.preferences.v1.subtitleLanguages"
    public static let subtitleModeKey = "native.playback.preferences.v1.subtitleMode"
    public static let subtitleStyleKey = "native.video.subtitleAppearance"
    public static let videoGesturesEnabledKey = "native.playback.preferences.v1.videoGesturesEnabled"
    public static let volumeSwipeEnabledKey = "native.playback.preferences.v1.volumeSwipeEnabled"
    public static let brightnessSwipeEnabledKey = "native.playback.preferences.v1.brightnessSwipeEnabled"
    public static let horizontalSeekEnabledKey = "native.playback.preferences.v1.horizontalSeekEnabled"
    public static let doubleTapSkipEnabledKey = "native.playback.preferences.v1.doubleTapSkipEnabled"
    public static let twoFingerTapEnabledKey = "native.playback.preferences.v1.twoFingerTapEnabled"
    public static let holdSpeedEnabledKey = "native.playback.preferences.v1.holdSpeedEnabled"
    public static let gestureHapticsEnabledKey = "native.playback.preferences.v1.gestureHapticsEnabled"
    public static let skipForwardSecondsKey = "native.playback.preferences.v1.skipForwardSeconds"
    public static let skipBackwardSecondsKey = "native.playback.preferences.v1.skipBackwardSeconds"
    public static let gestureSkipSecondChoices = [3, 5, 10, 15, 30, 60]

    public var quality: PlaybackQuality {
        didSet { defaults.set(quality.rawValue, forKey: Self.qualityKey) }
    }
    public var preferredAudioLanguages: [String] {
        didSet { persistAudioLanguages() }
    }
    public var preferredSubtitleLanguages: [String] {
        didSet { persistSubtitleLanguages() }
    }
    public var subtitleMode: NativeSubtitleMode {
        didSet { defaults.set(subtitleMode.rawValue, forKey: Self.subtitleModeKey) }
    }
    /// The existing portable AVFoundation caption style, kept beside the other device playback choices.
    public var subtitleStyle: VideoSubtitleStyle {
        didSet { defaults.set(subtitleStyle.encodedString, forKey: Self.subtitleStyleKey) }
    }
    /// The master switch leaves ordinary controls and single-tap control revelation available.
    public var videoGesturesEnabled: Bool {
        didSet { defaults.set(videoGesturesEnabled, forKey: Self.videoGesturesEnabledKey) }
    }
    public var volumeSwipeEnabled: Bool {
        didSet { defaults.set(volumeSwipeEnabled, forKey: Self.volumeSwipeEnabledKey) }
    }
    public var brightnessSwipeEnabled: Bool {
        didSet { defaults.set(brightnessSwipeEnabled, forKey: Self.brightnessSwipeEnabledKey) }
    }
    public var horizontalSeekEnabled: Bool {
        didSet { defaults.set(horizontalSeekEnabled, forKey: Self.horizontalSeekEnabledKey) }
    }
    public var doubleTapSkipEnabled: Bool {
        didSet { defaults.set(doubleTapSkipEnabled, forKey: Self.doubleTapSkipEnabledKey) }
    }
    public var twoFingerTapEnabled: Bool {
        didSet { defaults.set(twoFingerTapEnabled, forKey: Self.twoFingerTapEnabledKey) }
    }
    public var holdSpeedEnabled: Bool {
        didSet { defaults.set(holdSpeedEnabled, forKey: Self.holdSpeedEnabledKey) }
    }
    public var gestureHapticsEnabled: Bool {
        didSet { defaults.set(gestureHapticsEnabled, forKey: Self.gestureHapticsEnabledKey) }
    }
    public var skipForwardSeconds: Int {
        didSet { persistSkipForwardSeconds() }
    }
    public var skipBackwardSeconds: Int {
        didSet { persistSkipBackwardSeconds() }
    }

    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        quality = defaults.string(forKey: Self.qualityKey).flatMap(PlaybackQuality.init(rawValue:)) ?? .automatic
        preferredAudioLanguages = Self.normalizedLanguages(defaults.stringArray(forKey: Self.audioLanguagesKey) ?? [])
        preferredSubtitleLanguages = Self.normalizedLanguages(defaults.stringArray(forKey: Self.subtitleLanguagesKey) ?? [])
        subtitleMode = defaults.string(forKey: Self.subtitleModeKey).flatMap(NativeSubtitleMode.init(rawValue:)) ?? .automatic
        subtitleStyle = VideoSubtitleStyle(encodedString: defaults.string(forKey: Self.subtitleStyleKey))
        videoGesturesEnabled = Self.storedBoolean(forKey: Self.videoGesturesEnabledKey, defaults: defaults)
        volumeSwipeEnabled = Self.storedBoolean(forKey: Self.volumeSwipeEnabledKey, defaults: defaults)
        brightnessSwipeEnabled = Self.storedBoolean(forKey: Self.brightnessSwipeEnabledKey, defaults: defaults)
        horizontalSeekEnabled = Self.storedBoolean(forKey: Self.horizontalSeekEnabledKey, defaults: defaults)
        doubleTapSkipEnabled = Self.storedBoolean(forKey: Self.doubleTapSkipEnabledKey, defaults: defaults)
        twoFingerTapEnabled = Self.storedBoolean(forKey: Self.twoFingerTapEnabledKey, defaults: defaults)
        holdSpeedEnabled = Self.storedBoolean(forKey: Self.holdSpeedEnabledKey, defaults: defaults)
        gestureHapticsEnabled = Self.storedBoolean(forKey: Self.gestureHapticsEnabledKey, defaults: defaults)
        skipForwardSeconds = Self.storedSkipSeconds(forKey: Self.skipForwardSecondsKey, defaults: defaults)
        skipBackwardSeconds = Self.storedSkipSeconds(forKey: Self.skipBackwardSecondsKey, defaults: defaults)
    }

    /// Returns a matching audio stream only when the member has chosen a device language.
    /// Nil preserves the server's saved/default audio selection.
    public func preferredAudioStreamIndex(in streams: [PlaybackStream]) -> Int? {
        guard !preferredAudioLanguages.isEmpty else { return nil }
        return Self.matchingStream(in: streams.filter { $0.type == .audio }, languages: preferredAudioLanguages)?.index
    }

    /// Returns nil for Automatic so the server and AVFoundation can retain their own defaults.
    /// `-1` is Lumina's explicit source-level Off sentinel.
    public func preferredSubtitleStreamIndex(in streams: [PlaybackStream]) -> Int? {
        let subtitles = streams.filter { $0.type == .subtitle }
        switch subtitleMode {
        case .automatic:
            return nil
        case .off:
            return -1
        case .always:
            let candidates = subtitles.filter { !$0.isForced }
            guard !candidates.isEmpty else { return -1 }
            if preferredSubtitleLanguages.isEmpty {
                return candidates.first(where: \.isDefault)?.index ?? candidates.first?.index
            }
            return Self.matchingStream(in: candidates, languages: preferredSubtitleLanguages)?.index
                ?? candidates.first(where: \.isDefault)?.index ?? candidates.first?.index
        case .forcedOnly:
            let candidates = subtitles.filter(\.isForced)
            guard !candidates.isEmpty else { return -1 }
            if preferredSubtitleLanguages.isEmpty {
                return candidates.first(where: \.isDefault)?.index ?? candidates.first?.index
            }
            return Self.matchingStream(in: candidates, languages: preferredSubtitleLanguages)?.index ?? -1
        }
    }

    private func persistAudioLanguages() {
        let normalized = Self.normalizedLanguages(preferredAudioLanguages)
        defaults.set(normalized, forKey: Self.audioLanguagesKey)
        if preferredAudioLanguages != normalized {
            preferredAudioLanguages = normalized
        }
    }

    private func persistSubtitleLanguages() {
        let normalized = Self.normalizedLanguages(preferredSubtitleLanguages)
        defaults.set(normalized, forKey: Self.subtitleLanguagesKey)
        if preferredSubtitleLanguages != normalized {
            preferredSubtitleLanguages = normalized
        }
    }

    private func persistSkipForwardSeconds() {
        let normalized = Self.normalizedSkipSeconds(skipForwardSeconds)
        defaults.set(normalized, forKey: Self.skipForwardSecondsKey)
        if skipForwardSeconds != normalized { skipForwardSeconds = normalized }
    }

    private func persistSkipBackwardSeconds() {
        let normalized = Self.normalizedSkipSeconds(skipBackwardSeconds)
        defaults.set(normalized, forKey: Self.skipBackwardSecondsKey)
        if skipBackwardSeconds != normalized { skipBackwardSeconds = normalized }
    }

    private static func storedBoolean(forKey key: String, defaults: UserDefaults) -> Bool {
        (defaults.object(forKey: key) as? Bool) ?? true
    }

    private static func storedSkipSeconds(forKey key: String, defaults: UserDefaults) -> Int {
        guard let value = defaults.object(forKey: key) as? Int else { return 10 }
        return normalizedSkipSeconds(value)
    }

    private static func normalizedSkipSeconds(_ value: Int) -> Int {
        value > 0 ? value : 10
    }

    private static func normalizedLanguages(_ values: [String]) -> [String] {
        var seen = Set<String>()
        return values.compactMap(normalizedLanguage).filter { seen.insert($0).inserted }.prefix(8).map { $0 }
    }

    private static func matchingStream(in streams: [PlaybackStream], languages: [String]) -> PlaybackStream? {
        for preferred in languages {
            if let matching = streams.first(where: { languageMatches($0.language, preferred: preferred) }) { return matching }
        }
        return nil
    }

    /// Shared ISO-639 matching for server stream metadata and AVFoundation locale tags.
    /// Exact region matches win at the caller's ordering; this accepts common two/three-letter
    /// equivalents such as `en`/`eng` and `es`/`spa` before falling back to the primary tag.
    public static func languageMatches(_ candidate: String?, preferred: String) -> Bool {
        guard let candidate = candidate.flatMap(normalizedLanguage),
              let preferred = normalizedLanguage(preferred) else { return false }
        if candidate == preferred { return true }
        let candidatePrimary = canonicalPrimary(primaryLanguage(candidate))
        let preferredPrimary = canonicalPrimary(primaryLanguage(preferred))
        return !candidatePrimary.isEmpty && candidatePrimary == preferredPrimary
    }

    private static func normalizedLanguage(_ raw: String) -> String? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "_", with: "-")
            .lowercased()
        guard !value.isEmpty, value.count <= 35 else { return nil }
        let parts = value.split(separator: "-", omittingEmptySubsequences: false)
        guard let first = parts.first, (2...3).contains(first.count), first.allSatisfy({ $0.isASCII && $0.isLetter }),
              parts.dropFirst().allSatisfy({ (1...8).contains($0.count) && $0.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) } }) else {
            return nil
        }
        return value
    }

    private static func primaryLanguage(_ language: String) -> Substring {
        language.split(separator: "-", maxSplits: 1).first ?? ""
    }

    private static func canonicalPrimary(_ primary: Substring) -> String {
        let value = String(primary)
        return iso639Aliases[value] ?? value
    }

    private static let iso639Aliases = [
        "ara": "ar", "ar": "ar", "bul": "bg", "bg": "bg", "cat": "ca", "ca": "ca",
        "ces": "cs", "cze": "cs", "cs": "cs", "dan": "da", "da": "da", "deu": "de",
        "ger": "de", "de": "de", "ell": "el", "gre": "el", "el": "el", "eng": "en",
        "en": "en", "est": "et", "et": "et", "fin": "fi", "fi": "fi", "fra": "fr",
        "fre": "fr", "fr": "fr", "heb": "he", "he": "he", "hin": "hi", "hi": "hi",
        "hrv": "hr", "hr": "hr", "hun": "hu", "hu": "hu", "ind": "id", "id": "id",
        "ita": "it", "it": "it", "jpn": "ja", "ja": "ja", "kor": "ko", "ko": "ko",
        "nld": "nl", "dut": "nl", "nl": "nl", "nor": "no", "no": "no", "pol": "pl",
        "pl": "pl", "por": "pt", "pt": "pt", "ron": "ro", "rum": "ro", "ro": "ro",
        "rus": "ru", "ru": "ru", "slk": "sk", "slo": "sk", "sk": "sk", "slv": "sl",
        "sl": "sl", "spa": "es", "es": "es", "srp": "sr", "sr": "sr", "swe": "sv",
        "sv": "sv", "tha": "th", "th": "th", "tur": "tr", "tr": "tr", "ukr": "uk",
        "uk": "uk", "vie": "vi", "vi": "vi", "zho": "zh", "chi": "zh", "zh": "zh",
    ]
}
