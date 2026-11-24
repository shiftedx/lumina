import AVKit
import AVFoundation
import Combine
import LuminaCore
import Observation
import SwiftUI
import MediaPlayer
#if canImport(UIKit)
import UIKit
#endif
enum VideoPlaybackSpeed: Float, CaseIterable, Identifiable {
    case half = 0.5, threeQuarters = 0.75, normal = 1, oneAndQuarter = 1.25, oneAndHalf = 1.5, double = 2
    var id: Self { self }
    var label: String { rawValue.formatted(.number.precision(.fractionLength(0...2))) + "×" }
}

enum VideoSettingsTab: String, CaseIterable, Identifiable {
    case info, video, audio, subtitles, chapters
    var id: Self { self }
    var title: String { rawValue.capitalized }
}

enum VideoSubtitleSelection: Equatable {
    case automatic, off, option(Int)
}

struct VideoMediaTracks {
    let item: AVPlayerItem?
    let audioGroup: AVMediaSelectionGroup?
    let subtitleGroup: AVMediaSelectionGroup?
    let audioOptions: [AVMediaSelectionOption]
    let subtitleOptions: [AVMediaSelectionOption]
    private(set) var selectedAudioIndex: Int?
    private(set) var audioIsAutomatic = true
    private(set) var selectedSubtitleIndex: Int?
    private(set) var subtitlesAreAutomatic = true
    private(set) var subtitlesAreOff = false

    init(audioGroup: AVMediaSelectionGroup? = nil, subtitleGroup: AVMediaSelectionGroup? = nil, currentItem: AVPlayerItem? = nil) {
        self.item = currentItem
        self.audioGroup = audioGroup
        self.subtitleGroup = subtitleGroup
        audioOptions = audioGroup.map { AVMediaSelectionGroup.playableMediaSelectionOptions(from: $0.options) } ?? []
        subtitleOptions = subtitleGroup.map { AVMediaSelectionGroup.playableMediaSelectionOptions(from: $0.options) } ?? []
        selectedAudioIndex = nil
        selectedSubtitleIndex = nil
        if let currentItem {
            refreshSelection(from: currentItem)
        }
    }

    mutating func refreshSelection(from item: AVPlayerItem) {
        if let audioGroup {
            audioIsAutomatic = item.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: audioGroup)
            let current = item.currentMediaSelection.selectedMediaOption(in: audioGroup)
            selectedAudioIndex = audioIsAutomatic ? nil : audioOptions.firstIndex { $0 === current }
        }
        if let subtitleGroup {
            subtitlesAreAutomatic = item.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: subtitleGroup)
            let current = item.currentMediaSelection.selectedMediaOption(in: subtitleGroup)
            subtitlesAreOff = !subtitlesAreAutomatic && current == nil
            selectedSubtitleIndex = subtitlesAreAutomatic || subtitlesAreOff ? nil : subtitleOptions.firstIndex { $0 === current }
        }
    }

    mutating func selectAudio(_ index: Int) {
        selectedAudioIndex = index
        audioIsAutomatic = false
    }

    mutating func markAudioAutomatic() {
        selectedAudioIndex = nil
        audioIsAutomatic = true
    }

    mutating func selectSubtitle(_ index: Int) {
        selectedSubtitleIndex = index
        subtitlesAreAutomatic = false
        subtitlesAreOff = false
    }

    mutating func markSubtitlesAutomatic() {
        selectedSubtitleIndex = nil
        subtitlesAreAutomatic = true
        subtitlesAreOff = false
    }

    mutating func markSubtitlesOff() {
        selectedSubtitleIndex = nil
        subtitlesAreAutomatic = false
        subtitlesAreOff = true
    }
}

struct VideoPlaybackSettings: View {
    @Binding var tab: VideoSettingsTab
    let playback: PlaybackModel
    @Binding var subtitleStyle: VideoSubtitleStyle
    @State private var subtitleAppearanceExpanded = false
    @FocusState private var focusedTab: VideoSettingsTab?
    #if os(tvOS)
    @FocusState private var focusedVideoFitChoiceID: String?
    #endif
    private var item: MediaItem { playback.request.item }
    private var methodLabel: String { playback.request.methodLabel }
    @Binding var videoGravity: AVLayerVideoGravity
    let tracks: VideoMediaTracks
    let selectAudio: (Int) -> Void
    let selectAudioAutomatically: () -> Void
    let selectSubtitle: (VideoSubtitleSelection) -> Void
    #if os(iOS)
    var gestures: VideoGesturePresentation? = nil
    #endif
    let onDone: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            categoryHeader
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    settingsContent
                        .frame(maxWidth: .infinity, alignment: .leading)
                        #if os(tvOS)
                        .focusSection()
                        #endif
                    if playback.isSwitchingVideoStream {
                        ProgressView("Changing stream…")
                            .accessibilityIdentifier("videoStreamChanging")
                    }
                    if let error = playback.videoStreamError {
                        Text(error).font(.callout).accessibilityIdentifier("videoStreamError")
                    }
                }
            }
        }
        .padding()
        .foregroundStyle(.white)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoSettingsPanel")
        #if os(tvOS)
        .defaultFocus($focusedTab, tab, priority: .userInitiated)
        #endif
    }

    private var categoryHeader: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack {
                Text(tab == .info ? "Playback info" : "Playback settings").font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 16)
                if let onDone {
                    Button("Done", action: onDone)
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("videoSettingsDoneButton")
                }
            }
            #if os(tvOS)
            .focusSection()
            #endif
            Group {
                #if os(iOS)
                ViewThatFits(in: .horizontal) {
                    categoryTabs(spread: true)
                    ScrollView(.horizontal, showsIndicators: false) { categoryTabs(spread: false) }
                }
                #else
                ScrollView(.horizontal, showsIndicators: false) { categoryTabs(spread: false) }
                #endif
            }
            .controlSize(.large)
            .fixedSize(horizontal: false, vertical: true)
            #if os(tvOS)
            .focusSection()
            #endif
        }
    }

    private func categoryTabs(spread: Bool) -> some View {
        HStack(spacing: spread ? 0 : 12) {
            ForEach(VideoSettingsTab.allCases) { candidate in
                let isSelected = candidate == tab
                Button(candidate.title) { tab = candidate }
                    .focused($focusedTab, equals: candidate)
                    .buttonStyle(LuminaUnderlineTabStyle(selected: isSelected))
                    .fixedSize(horizontal: true, vertical: true)
                    .accessibilityAddTraits(isSelected ? .isSelected : [])
                    .accessibilityValue(isSelected ? "Selected" : "Not selected")
                    .accessibilityIdentifier("videoSettingsTab-\(candidate.rawValue)")
                if spread, candidate != VideoSettingsTab.allCases.last {
                    Spacer(minLength: 12)
                }
            }
        }
    }

    @ViewBuilder private var settingsContent: some View {
        switch tab {
        case .info:
            LabeledContent("Title", value: item.name)
            LabeledContent("Stream") {
                Text(methodLabel)
                    .accessibilityIdentifier("videoPlaybackMethod")
            }
            if let conversion = playback.videoDolbyVisionConversion, conversion.hasFailure || conversion.convertedRPUs > 0 {
                LabeledContent("Dolby Vision conversion", value: conversion.hasFailure
                    ? "Conversion failed"
                    : "Profile 7 → 8.1; enhancement layer omitted")
                    .accessibilityIdentifier("videoDolbyVisionConversion")
            }
            #if os(iOS)
            PlaybackInfoView(playback: playback, audioGroup: tracks.audioGroup)
            PlaybackDiagnosticsControls(playback: playback)
            #else
            if let container = item.container, !container.isEmpty {
                LabeledContent("Container", value: container.uppercased())
            }
            #endif
            if let error = playback.videoActionsError {
                Text(error).font(.callout)
                Button("Retry playback actions") { Task { await playback.refreshVideoActions() } }
                    .accessibilityIdentifier("videoActionsRetryButton")
            }
        case .video:
            LabeledContent("Video fit") {
                #if os(tvOS)
                HStack(spacing: 12) {
                    videoFitChoice("Fit", gravity: .resizeAspect, identifier: "videoFitFitButton")
                    videoFitChoice("Fill", gravity: .resizeAspectFill, identifier: "videoFitFillButton")
                }
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Video fit")
                .accessibilityValue(videoGravity == .resizeAspect ? "Fit" : "Fill")
                .accessibilityIdentifier("videoFitPicker")
                .focusSection()
                #else
                Picker("Video fit", selection: $videoGravity) {
                    Text("Fit").tag(AVLayerVideoGravity.resizeAspect)
                    Text("Fill").tag(AVLayerVideoGravity.resizeAspectFill)
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .accessibilityLabel("Video fit")
                .accessibilityIdentifier("videoFitPicker")
                #endif
            }
            LabeledContent("Stream quality") {
                Picker("Stream quality", selection: Binding(
                    get: { playback.videoQuality },
                    set: { quality in Task { await playback.selectVideoQuality(quality) } }
                )) {
                    ForEach(PlaybackQuality.allCases, id: \.self) { quality in
                        Text(quality.label).tag(quality)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .accessibilityLabel("Stream quality")
                .accessibilityIdentifier("videoQualityPicker")
                .accessibilityValue(playback.videoQuality.label)
                .disabled(playback.isSwitchingVideoStream)
            }
            if let size = playback.player.currentItem?.presentationSize, size.width > 0, size.height > 0 {
                LabeledContent("Playing resolution", value: "\(Int(size.width)) × \(Int(size.height))")
                    .accessibilityIdentifier("videoPlayingResolution")
            }
            Text("Maximum quality requests the original source when compatible. Server bandwidth limits still apply. Lower choices cap delivery at 1080p / 8 Mbps, 720p / 4 Mbps, or 480p / 1.5 Mbps; sources are never upscaled.")
                .font(.caption).foregroundStyle(.secondary)
            LabeledContent("Playback speed") {
                Picker("Playback speed", selection: Binding(
                    get: { playback.videoSpeed },
                    set: { playback.selectVideoSpeed($0) }
                )) {
                    ForEach(VideoPlaybackSpeed.allCases) { speed in
                        Text(speed.label).tag(speed.rawValue)
                            .disabled(!playback.supportsVideoSpeed(speed.rawValue))
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .accessibilityLabel("Playback speed")
                .accessibilityIdentifier("videoSpeedPicker")
                .accessibilityValue(VideoPlaybackSpeed(rawValue: playback.videoSpeed)?.label ?? "1×")
                .disabled(playback.isSwitchingVideoStream || playback.hasFinished || playback.isReplaying)
            }
            #if os(iOS)
            VideoGestureSettingsView(preferences: playback.store.playbackPreferences)
            if let gestures, let screen = gestures.screen {
                Text("Brightness").font(.headline)
                Slider(value: Binding(get: { Double(screen.brightness) }, set: { gestures.setBrightness($0) }), in: 0...1)
                    .accessibilityLabel("Brightness")
            }
            #endif
        case .audio:
            #if os(iOS)
            Text(playback.isCasting
                 ? "Swipe vertically on the right half of the video to adjust Chromecast volume."
                 : "Use the device volume buttons or swipe vertically on the right half of the video to adjust volume.")
                .font(.caption).foregroundStyle(.secondary)
            #endif
                audioButtons
        case .subtitles:
            subtitleButtons
        case .chapters:
            chapterButtons
        }
    }

    #if os(tvOS)
    private func videoFitChoice(_ title: String, gravity: AVLayerVideoGravity, identifier: String) -> some View {
        let selected = videoGravity == gravity
        return Button {
            videoGravity = gravity
            focusedVideoFitChoiceID = identifier
        } label: {
            HStack(spacing: 8) {
                Text(title)
                if selected {
                    LuminaIcon(glyph: .check, size: 20)
                        .accessibilityHidden(true)
                }
            }
        }
        .buttonStyle(LuminaQuietActionStyle(selected: selected))
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .accessibilityIdentifier(identifier)
        .focused($focusedVideoFitChoiceID, equals: identifier)
    }
    #endif

    @ViewBuilder private func trackButtons(options: [AVMediaSelectionOption], selected: Int?, emptyMessage: String, action: @escaping (Int) -> Void) -> some View {
        if options.isEmpty {
            Text(emptyMessage).foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(options.enumerated()), id: \.element) { index, option in
                    let isSelected = index == selected
                    Button(option.displayName) { action(index) }
                        .buttonStyle(LuminaQuietActionStyle(selected: isSelected))
                        .accessibilityValue(isSelected ? "Selected" : "Not selected")
                        .accessibilityIdentifier("videoAudioTrack-\(index)")
                }
            }
        }
    }

    @ViewBuilder private var audioButtons: some View {
        let sourceStreams = playback.request.mediaStreams.filter { $0.type == .audio }
        if !sourceStreams.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Button("Auto") { Task { await playback.selectVideoAudioStream(nil) } }
                    .buttonStyle(LuminaQuietActionStyle(selected: playback.sourceAudioIsAutomatic))
                    .accessibilityValue(playback.sourceAudioIsAutomatic ? "Selected" : "Not selected")
                    .accessibilityIdentifier("videoAudioAutoButton")
                ForEach(sourceStreams) { stream in
                    Button { Task { await playback.selectVideoAudioStream(stream.index) } } label: {
                        HStack {
                            Text(stream.label)
                            Spacer(minLength: 12)
                            if !playback.sourceAudioIsAutomatic, stream.index == playback.request.audioStreamIndex {
                                LuminaIcon(glyph: .check, size: 20)
                            }
                        }
                    }
                    .accessibilityValue(!playback.sourceAudioIsAutomatic && stream.index == playback.request.audioStreamIndex ? "Selected" : "Not selected")
                    .accessibilityIdentifier("videoSourceAudioTrack-\(stream.index)")
                }
                Text("Choose a source audio track. Incompatible formats use a compatible stream.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .disabled(playback.isSwitchingVideoStream)
        } else if tracks.audioGroup == nil {
            Text("This stream exposes no selectable audio tracks.").foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                Button("Auto") { selectAudioAutomatically() }
                    .buttonStyle(LuminaQuietActionStyle(selected: tracks.audioIsAutomatic))
                    .accessibilityValue(tracks.audioIsAutomatic ? "Selected" : "Not selected")
                    .accessibilityIdentifier("videoAudioAutoButton")
                trackButtons(options: tracks.audioOptions, selected: tracks.selectedAudioIndex,
                             emptyMessage: "This stream exposes no selectable audio tracks.", action: selectAudio)
            }
        }
    }

    @ViewBuilder private var chapterButtons: some View {
        if playback.request.chapters.isEmpty {
            Text("No chapters are available for this video.").foregroundStyle(.secondary)
        } else {
            let currentChapterID = playback.currentChapterID
            ForEach(playback.request.chapters) { chapter in
                let selected = currentChapterID == chapter.id
                Button { Task { await playback.selectChapter(chapter) } } label: {
                    HStack(spacing: 16) {
                        Text(chapter.title)
                        Spacer(minLength: 8)
                        Text(Duration.seconds(chapter.startPositionSeconds).formatted(.time(pattern: .hourMinuteSecond)))
                            .font(.caption.monospacedDigit())
                        if selected { LuminaIcon(glyph: .check, size: 20) }
                    }
                }
                .buttonStyle(LuminaQuietActionStyle(selected: selected))
                .accessibilityValue(selected ? "Selected" : "Not selected")
                .accessibilityIdentifier("videoChapter-\(chapter.id)")
                .disabled(playback.isSwitchingVideoStream || playback.isSeekingChapter || playback.isReplaying)
            }
        }
    }

    @ViewBuilder private var subtitleButtons: some View {
        #if os(tvOS)
        HStack(alignment: .top, spacing: 64) {
            subtitleTracksAndFiles.frame(maxWidth: .infinity, alignment: .leading)
            subtitleAppearance.frame(maxWidth: .infinity, alignment: .leading)
        }
        #else
        VStack(alignment: .leading, spacing: 24) {
            subtitleTracksAndFiles
            subtitleAppearance
        }
        #endif
    }

    private var subtitleTracksAndFiles: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Tracks").font(.headline)
            subtitleTrackButtons
            let sidecars = playback.request.mediaStreams.filter { $0.type == .subtitle && $0.hasExternalSubtitle }
            if !sidecars.isEmpty {
                Text("Subtitle files").font(.headline)
                ForEach(sidecars) { stream in
                    Button(stream.label) { Task { await playback.selectExternalVideoSubtitle(stream) } }
                        .buttonStyle(LuminaQuietActionStyle())
                        .disabled(playback.isSwitchingVideoStream)
                        .accessibilityIdentifier("videoExternalSubtitle-\(stream.index)")
                }
            }
            subtitleTiming
        }
        .disabled(playback.isSwitchingVideoStream)
    }

    private var subtitleTiming: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Subtitle timing").font(.headline)
            if playback.canAdjustSubtitleTiming, !tracks.subtitleOptions.isEmpty {
                Text("\(playback.subtitleDelaySeconds, format: .number.precision(.fractionLength(1)).sign(strategy: .always())) s")
                    .monospacedDigit()
                    .accessibilityLabel("Subtitle delay in seconds")
                    .accessibilityValue(playback.subtitleDelaySeconds.formatted(.number.precision(.fractionLength(1)).sign(strategy: .always())))
                    .accessibilityIdentifier("videoSubtitleTimingValue")
                HStack(spacing: 12) {
                    Button { Task { await playback.setSubtitleDelay(playback.subtitleDelaySeconds - 0.5) } } label: {
                        Text("Earlier").frame(maxWidth: .infinity)
                    }
                    .disabled(playback.subtitleDelaySeconds <= -10)
                    .accessibilityLabel("Earlier by 0.5 seconds")
                    .accessibilityIdentifier("videoSubtitleEarlierButton")
                    Button { Task { await playback.setSubtitleDelay(0) } } label: {
                        Text("Reset").frame(maxWidth: .infinity)
                    }
                    .disabled(playback.subtitleDelaySeconds == 0)
                    .accessibilityIdentifier("videoSubtitleTimingResetButton")
                    Button { Task { await playback.setSubtitleDelay(playback.subtitleDelaySeconds + 0.5) } } label: {
                        Text("Later").frame(maxWidth: .infinity)
                    }
                    .disabled(playback.subtitleDelaySeconds >= 10)
                    .accessibilityLabel("Later by 0.5 seconds")
                    .accessibilityIdentifier("videoSubtitleLaterButton")
                }
                Text("Adjust by 0.5 seconds. Positive values show subtitles later. Timing applies to this video.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Timing is available for packaged MKV text captions and attached subtitle files. Embedded captions in this stream and burned-in captions cannot be shifted.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .buttonStyle(LuminaQuietActionStyle())
    }

    private var subtitleAppearance: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Appearance").font(.headline)
            Toggle("Customize subtitles", isOn: $subtitleStyle.isCustom)
                .tint(LuminaTheme.brand)
                .accessibilityIdentifier("videoSubtitleCustomizeToggle")
            if subtitleStyle.isCustom {
                Button("Caption appearance",
                       glyph: subtitleAppearanceExpanded ? .chevronUp : .chevronDown) {
                    subtitleAppearanceExpanded.toggle()
                }
                .buttonStyle(LuminaQuietActionStyle())
                .accessibilityValue(subtitleAppearanceExpanded ? "Expanded" : "Collapsed")
                .accessibilityIdentifier("videoSubtitleAppearanceDisclosure")
                    #if os(tvOS)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .focusSection()
                    #endif
                if subtitleAppearanceExpanded {
                    stylePicker("Font", value: $subtitleStyle.font, options: VideoSubtitleStyle.FontFamily.allCases, label: { $0.label })
                    stylePicker("Size", value: $subtitleStyle.size, options: VideoSubtitleStyle.Size.allCases, label: { $0.label })
                    stylePicker("Color", value: $subtitleStyle.color, options: VideoSubtitleStyle.Color.allCases, label: { $0.label })
                    stylePicker("Weight", value: $subtitleStyle.weight, options: VideoSubtitleStyle.Weight.allCases, label: { $0.label })
                    stylePicker("Outline", value: $subtitleStyle.edgeStyle, options: VideoSubtitleStyle.EdgeStyle.allCases, label: { $0.label })
                    stylePicker("Background", value: $subtitleStyle.background, options: VideoSubtitleStyle.Background.allCases, label: { $0.label })
                    stylePicker("Opacity", value: $subtitleStyle.opacity, options: VideoSubtitleStyle.Opacity.allCases, label: { $0.label })
                    stylePicker("Vertical alignment", value: $subtitleStyle.verticalAlignment, options: VideoSubtitleStyle.VerticalAlignment.allCases, label: { $0.label })
                    Button("Reset appearance") { subtitleStyle.restoreDefaults() }
                        .accessibilityIdentifier("videoSubtitleResetButton")
                    #if os(tvOS)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .focusSection()
                    #endif
                }
            }
            Text("Custom appearance applies to WebVTT captions. Authored styling and system caption preferences can take precedence.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func stylePicker<Option: Hashable & Identifiable>(
        _ title: String, value: Binding<Option>, options: [Option], label: @escaping (Option) -> String
    ) -> some View {
        #if os(tvOS)
        LabeledContent(title) {
            HStack(spacing: 12) {
                ForEach(options) { option in
                    let selected = option == value.wrappedValue
                    Button {
                        value.wrappedValue = option
                    } label: {
                        HStack(spacing: 8) {
                            Text(label(option))
                            if selected {
                                LuminaIcon(glyph: .check, size: 20)
                                    .accessibilityHidden(true)
                            }
                        }
                    }
                    .buttonStyle(LuminaQuietActionStyle(selected: selected))
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .accessibilityValue(selected ? "Selected" : "Not selected")
                    .accessibilityIdentifier("videoSubtitleStyle-\(title.replacingOccurrences(of: " ", with: ""))-\(String(describing: option.id))")
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(title)
            .accessibilityValue(label(value.wrappedValue))
            .accessibilityIdentifier("videoSubtitleStyle-" + title)
            .focusSection()
        }
        #else
        LabeledContent(title) {
            Picker(title, selection: value) {
                ForEach(options) { option in Text(label(option)).tag(option) }
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .accessibilityLabel(title)
            .accessibilityValue(label(value.wrappedValue))
            .accessibilityIdentifier("videoSubtitleStyle-" + title)
        }
        #endif
    }

    @ViewBuilder private var subtitleTrackButtons: some View {
        if tracks.subtitleGroup == nil {
            Text("This stream exposes no selectable subtitles.").foregroundStyle(.secondary)
        } else {
            #if os(tvOS)
            VStack(alignment: .leading, spacing: 12) {
                subtitleTrackChoices
            }
            #else
            ScrollView(.horizontal) {
                HStack(spacing: 12) {
                    subtitleTrackChoices
                }
                .fixedSize(horizontal: true, vertical: false)
            }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("videoSubtitleTrackRow")
            #endif
        }
    }

    @ViewBuilder private var subtitleTrackChoices: some View {
        subtitleChoice("Auto", selected: tracks.subtitlesAreAutomatic, identifier: "videoSubtitleAutoButton") {
            selectSubtitle(.automatic)
        }
        if tracks.subtitleGroup?.allowsEmptySelection == true {
            subtitleChoice("Off", selected: tracks.subtitlesAreOff, identifier: "videoSubtitleOffButton") {
                selectSubtitle(.off)
            }
        }
        ForEach(Array(tracks.subtitleOptions.enumerated()), id: \.element) { index, option in
            subtitleChoice(option.displayName, selected: index == tracks.selectedSubtitleIndex,
                           identifier: "videoSubtitleTrack-\(index)") {
                selectSubtitle(.option(index))
            }
        }
    }

    private func subtitleChoice(_ title: String, selected: Bool, identifier: String,
                                action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                #if os(tvOS)
                .frame(maxWidth: .infinity, alignment: .leading)
                #else
                .frame(minWidth: 44)
                #endif
        }
        .buttonStyle(LuminaQuietActionStyle(selected: selected))
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .accessibilityIdentifier(identifier)
    }
}
