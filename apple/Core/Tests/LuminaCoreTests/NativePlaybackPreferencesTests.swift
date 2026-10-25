import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class NativePlaybackPreferencesTests: XCTestCase {
    func testDevicePreferencesPersistTrackChoicesAndReuseExistingSubtitleStyleKey() throws {
        let name = "LuminaCoreTests.native-playback.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let style = VideoSubtitleStyle(isCustom: true, size: .large, color: .yellow)
        defaults.set(style.encodedString, forKey: "native.video.subtitleAppearance")

        let preferences = NativePlaybackPreferences(defaults: defaults)
        XCTAssertEqual(preferences.subtitleStyle, style)
        preferences.quality = .upTo1080p
        preferences.preferredAudioLanguages = [" EN_us ", "eng", "invalid!", "en_us"]
        preferences.preferredSubtitleLanguages = ["spa", "en", "  ", "SPA"]
        preferences.subtitleMode = .forcedOnly
        preferences.videoGesturesEnabled = false
        preferences.volumeSwipeEnabled = false
        preferences.brightnessSwipeEnabled = false
        preferences.horizontalSeekEnabled = false
        preferences.doubleTapSkipEnabled = false
        preferences.twoFingerTapEnabled = false
        preferences.holdSpeedEnabled = false
        preferences.gestureHapticsEnabled = false
        preferences.skipForwardSeconds = 30
        preferences.skipBackwardSeconds = 5

        XCTAssertEqual(defaults.stringArray(forKey: NativePlaybackPreferences.audioLanguagesKey), ["en-us", "eng"])
        XCTAssertEqual(defaults.stringArray(forKey: NativePlaybackPreferences.subtitleLanguagesKey), ["spa", "en"])
        let restored = NativePlaybackPreferences(defaults: defaults)
        XCTAssertEqual(restored.quality, .upTo1080p)
        XCTAssertEqual(restored.preferredAudioLanguages, ["en-us", "eng"])
        XCTAssertEqual(restored.preferredSubtitleLanguages, ["spa", "en"])
        XCTAssertEqual(restored.subtitleMode, .forcedOnly)
        XCTAssertEqual(restored.subtitleStyle, style)
        XCTAssertFalse(restored.videoGesturesEnabled)
        XCTAssertFalse(restored.volumeSwipeEnabled)
        XCTAssertFalse(restored.brightnessSwipeEnabled)
        XCTAssertFalse(restored.horizontalSeekEnabled)
        XCTAssertFalse(restored.doubleTapSkipEnabled)
        XCTAssertFalse(restored.twoFingerTapEnabled)
        XCTAssertFalse(restored.holdSpeedEnabled)
        XCTAssertFalse(restored.gestureHapticsEnabled)
        XCTAssertEqual(restored.skipForwardSeconds, 30)
        XCTAssertEqual(restored.skipBackwardSeconds, 5)
    }

    func testGestureDefaultsAreEnabledAndCustomSkipIntervalsSurviveRestore() throws {
        let name = "LuminaCoreTests.native-playback-gestures.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }

        let defaultsPreferences = NativePlaybackPreferences(defaults: defaults)
        XCTAssertTrue(defaultsPreferences.videoGesturesEnabled)
        XCTAssertTrue(defaultsPreferences.volumeSwipeEnabled)
        XCTAssertTrue(defaultsPreferences.brightnessSwipeEnabled)
        XCTAssertTrue(defaultsPreferences.horizontalSeekEnabled)
        XCTAssertTrue(defaultsPreferences.doubleTapSkipEnabled)
        XCTAssertTrue(defaultsPreferences.twoFingerTapEnabled)
        XCTAssertTrue(defaultsPreferences.holdSpeedEnabled)
        XCTAssertTrue(defaultsPreferences.gestureHapticsEnabled)
        XCTAssertEqual(defaultsPreferences.skipForwardSeconds, 10)
        XCTAssertEqual(defaultsPreferences.skipBackwardSeconds, 10)
        XCTAssertEqual(NativePlaybackPreferences.gestureSkipSecondChoices, [3, 5, 10, 15, 30, 60])

        defaults.set(45, forKey: NativePlaybackPreferences.skipForwardSecondsKey)
        defaults.set(17, forKey: NativePlaybackPreferences.skipBackwardSecondsKey)
        let restored = NativePlaybackPreferences(defaults: defaults)
        XCTAssertEqual(restored.skipForwardSeconds, 45)
        XCTAssertEqual(restored.skipBackwardSeconds, 17)
    }

    func testLanguageMatchingAndSubtitleModesChooseOnlyPermittedTracks() throws {
        let name = "LuminaCoreTests.native-playback.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let preferences = NativePlaybackPreferences(defaults: defaults)
        let streams = [
            PlaybackStream(index: 1, type: .audio, label: "French", language: "fra", isDefault: true),
            PlaybackStream(index: 2, type: .audio, label: "English", language: "eng"),
            PlaybackStream(index: 3, type: .subtitle, label: "Spanish", language: "spa"),
            PlaybackStream(index: 4, type: .subtitle, label: "English forced", language: "eng", isForced: true),
        ]
        preferences.preferredAudioLanguages = ["en-US"]
        preferences.preferredSubtitleLanguages = ["es"]

        XCTAssertTrue(NativePlaybackPreferences.languageMatches("eng", preferred: "en-US"))
        XCTAssertTrue(NativePlaybackPreferences.languageMatches("es-MX", preferred: "spa"))
        XCTAssertFalse(NativePlaybackPreferences.languageMatches("fra", preferred: "en"))
        XCTAssertEqual(preferences.preferredAudioStreamIndex(in: streams), 2)

        preferences.subtitleMode = .always
        XCTAssertEqual(preferences.preferredSubtitleStreamIndex(in: streams), 3)
        preferences.subtitleMode = .forcedOnly
        XCTAssertEqual(preferences.preferredSubtitleStreamIndex(in: streams), -1)
        preferences.subtitleMode = .off
        XCTAssertEqual(preferences.preferredSubtitleStreamIndex(in: streams), -1)
        preferences.subtitleMode = .automatic
        XCTAssertNil(preferences.preferredSubtitleStreamIndex(in: streams))
    }
}
