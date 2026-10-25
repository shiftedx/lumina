import SwiftUI
import LuminaCore

/// Device-local video-surface gesture controls shared by Settings and the player sheet.
@MainActor
struct VideoGestureSettingsView: View {
    @Bindable var preferences: NativePlaybackPreferences
    var includeHelp = true
    @State private var helpPresented = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            LuminaSettingsSwitch("Playback gestures", isOn: $preferences.videoGesturesEnabled,
                                 identifier: "videoGesturesEnabledToggle")
            Group {
                LuminaSettingsSwitch("Volume swipe", isOn: $preferences.volumeSwipeEnabled,
                                     identifier: "videoVolumeSwipeToggle")
                LuminaSettingsSwitch("Brightness swipe", isOn: $preferences.brightnessSwipeEnabled,
                                     identifier: "videoBrightnessSwipeToggle")
                LuminaSettingsSwitch("Horizontal swipe to seek", isOn: $preferences.horizontalSeekEnabled,
                                     identifier: "videoHorizontalSeekToggle")
                LuminaSettingsSwitch("Double-tap to skip", isOn: $preferences.doubleTapSkipEnabled,
                                     identifier: "videoDoubleTapSkipToggle")
                LuminaSettingsSwitch("Two-finger tap to play/pause", isOn: $preferences.twoFingerTapEnabled,
                                     identifier: "videoTwoFingerTapToggle")
                LuminaSettingsSwitch("Hold for 2×", isOn: $preferences.holdSpeedEnabled,
                                     identifier: "videoHoldSpeedToggle")
                LuminaSettingsPicker("Forward skip", selection: $preferences.skipForwardSeconds,
                                     options: NativePlaybackPreferences.gestureSkipSecondChoices,
                                     identifier: "videoForwardSkipPicker") { "\($0) seconds" }
                LuminaSettingsPicker("Backward skip", selection: $preferences.skipBackwardSeconds,
                                     options: NativePlaybackPreferences.gestureSkipSecondChoices,
                                     identifier: "videoBackwardSkipPicker") { "\($0) seconds" }
                LuminaSettingsSwitch("Gesture haptics", isOn: $preferences.gestureHapticsEnabled,
                                     identifier: "videoGestureHapticsToggle")
            }
            .disabled(!preferences.videoGesturesEnabled)

            if includeHelp {
                Button { helpPresented = true } label: {
                    LuminaSettingsActionLabel("Playback gestures help", glyph: .help)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("videoGesturesHelpButton")
            }
        }
        .sheet(isPresented: $helpPresented) {
            VideoGestureHelpView()
        }
    }
}

@MainActor
struct VideoGestureHelpView: View {
    @Environment(\.dismiss) private var dismiss

    private let rows = [
        ("Tap", "Show or hide playback controls"),
        ("Two-finger tap", "Play or pause"),
        ("Swipe right half vertically", "Adjust volume"),
        ("Swipe left half vertically", "Adjust brightness"),
        ("Swipe horizontally", "Preview then seek"),
        ("Double-tap right", "Skip forward"),
        ("Double-tap left", "Skip backward"),
        ("Repeat a double-tap", "Add another skip interval"),
        ("Press and hold", "Temporarily play at 2×"),
        ("Drag the timeline", "Seek precisely"),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(rows, id: \.0) { interaction, action in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(interaction).font(.body.weight(.semibold))
                            Text(action).font(.subheadline).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("\(interaction). \(action)")
                        Divider()
                    }
                }
                .padding(24)
                .frame(maxWidth: 620, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Playback gestures")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .accessibilityIdentifier("videoGesturesHelpScreen")
        }
    }
}
