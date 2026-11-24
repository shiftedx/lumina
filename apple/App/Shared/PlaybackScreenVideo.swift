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

extension PlaybackScreen {
    #if os(iOS)
    var videoGestureSettingsFingerprint: [Int] {
        let preferences = store.playbackPreferences
        return [preferences.videoGesturesEnabled, preferences.volumeSwipeEnabled,
                preferences.brightnessSwipeEnabled, preferences.horizontalSeekEnabled,
                preferences.doubleTapSkipEnabled, preferences.twoFingerTapEnabled,
                preferences.holdSpeedEnabled, preferences.gestureHapticsEnabled].map { $0 ? 1 : 0 }
            + [preferences.skipForwardSeconds, preferences.skipBackwardSeconds]
    }

    func cancelVideoGestures(releaseBrightness: Bool) {
        guard !requestedPlayback.request.item.isAudio else { return }
        videoGestureCancellationID = UUID()
        videoGestures.cancel(playback: playback, releaseBrightness: releaseBrightness)
        playback.cancelVideoGestureOverrides()
    }

    private func videoTouchSurface(safeArea: EdgeInsets) -> some View {
        let preferences = store.playbackPreferences
        let topChromeBand: [CGRect]
        if videoChromeVisible, videoGestureContainerSize.width > 0 {
            topChromeBand = [CGRect(
                x: 0,
                y: 0,
                width: videoGestureContainerSize.width,
                height: safeArea.top + videoVerticalInset + 44
            )]
        } else {
            topChromeBand = []
        }
        return VideoGestureSurface(
            configuration: .init(gestures: .init(
                advancedEnabled: preferences.videoGesturesEnabled,
                volumeEnabled: preferences.volumeSwipeEnabled,
                brightnessEnabled: preferences.brightnessSwipeEnabled,
                horizontalSeekEnabled: preferences.horizontalSeekEnabled,
                doubleTapEnabled: preferences.doubleTapSkipEnabled,
                twoFingerEnabled: preferences.twoFingerTapEnabled,
                holdEnabled: preferences.holdSpeedEnabled),
                safeArea: UIEdgeInsets(top: safeArea.top, left: safeArea.leading, bottom: safeArea.bottom, right: safeArea.trailing),
                excludedRegions: topChromeBand),
            isInteractionEnabled: scenePhase == .active && !pictureInPictureActive && !pictureInPictureContinuing && !videoSettingsPresented && !videoSettingsVisible && displayedError == nil && !playback.hasFinished && !playback.isSwitchingVideoStream,
            isVoiceOverRunning: voiceOverEnabled,
            cancellationID: videoGestureCancellationID,
            onWindow: { window in videoGestures.attach(window: window) },
            action: { action in
                videoGestures.handle(action, playback: playback, preferences: preferences, toggleControls: toggleVideoChrome)
                isMuted = playback.player.isMuted
            }
        )
    }

    private var videoTimeline: some View {
        VideoGestureTimeline(playback: playback, presentation: videoGestures, preferences: store.playbackPreferences,
                             cancellationID: videoGestureCancellationID, containerHeight: videoGestureContainerSize.height) { editing in
            videoIsScrubbing = editing
            revealVideoChrome()
        }
    }
    #endif

    #if os(iOS)
    func videoStage(safeArea: EdgeInsets) -> some View {
        GeometryReader { geometry in
            let division = tabletopDivision(in: geometry)
            ZStack(alignment: .topLeading) {
                nativeVideoPlayer
                    .frame(maxWidth: .infinity)
                    .frame(height: division?.minY ?? geometry.size.height)
                    .frame(maxHeight: .infinity, alignment: .top)
                videoTouchSurface(safeArea: safeArea)
                    .frame(maxWidth: .infinity)
                    .frame(height: division?.minY ?? geometry.size.height)
                if let division {
                    videoTransport
                        .padding(.top, division.maxY)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                } else {
                    if !videoSettingsVisible && (videoChromeVisible || displayedError != nil || playback.hasFinished) {
                        videoOverlayIOS(safeArea: safeArea)
                            .padding(.top, safeArea.top)
                            .padding(.bottom, safeArea.bottom)
                            .background { videoScrim.allowsHitTesting(false) }
                            .transition(.opacity)
                    }
                    if !videoSettingsVisible, !videoSettingsPresented, displayedError == nil {
                        videoContextActions(showNext: showsContextualNextEpisode)
                            .padding(.horizontal, videoHorizontalInset)
                            .padding(.top, safeArea.top + videoVerticalInset + 56)
                            .padding(.trailing, max(0, safeArea.trailing - videoHorizontalInset))
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                    }
                }
                if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                    playbackTimeProbe
                }
                VideoGestureFeedbackOverlay(presentation: videoGestures, safeArea: safeArea, reduceMotion: reduceMotion)
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .onChange(of: geometry.size, initial: true) { _, size in
                if videoGestureContainerSize != .zero { cancelVideoGestures(releaseBrightness: false) }
                playback.traceContinuity("geometry=\(size)")
                videoGestureContainerSize = size
            }
        }
    }

    private func tabletopDivision(in geometry: GeometryProxy) -> CGRect? {
        guard #available(iOS 27.1, *) else { return nil }
        return geometry.reservedRegions(kind: .division, layoutDirectionBehavior: .fixed)
            .map { region in
                CGRect(x: region.frame.minX - region.margins.leading,
                       y: region.frame.minY - region.margins.top,
                       width: region.frame.width + region.margins.leading + region.margins.trailing,
                       height: region.frame.height + region.margins.top + region.margins.bottom)
            }
            .first { $0.width > $0.height }
    }

    private var nativeVideoPlayer: some View {
        NativePlayerLayer(
            player: playback.player,
            videoGravity: videoGravity,
            onVideoTap: toggleVideoChrome,
            pictureInPictureStartRequest: pictureInPictureStartRequest,
            onPictureInPicturePossibleChange: { pictureInPicturePossible = $0 },
            onPictureInPictureActiveChange: { pictureInPictureActive = $0 },
            onPictureInPictureContinuationChange: {
                pictureInPictureContinuing = $0
                playback.setActive(scenePhase == .active || $0)
            },
            onPictureInPictureRestoreUI: { revealVideoChrome() }
        )
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .accessibilityIdentifier("nativeVideoPlayer")
    }

    #endif

    @ViewBuilder private var videoPictureInPictureButton: some View {
        #if os(iOS)
        if AVPictureInPictureController.isPictureInPictureSupported() {
            Button("Picture in Picture", glyph: .pictureInPicture, variant: .solid, size: 24) {
                pictureInPictureStartRequest += 1
            }
            .labelStyle(.iconOnly)
            .disabled(playback.isCasting || !pictureInPicturePossible || pictureInPictureContinuing || playback.hasFinished)
            .accessibilityIdentifier("videoPictureInPictureButton")
        }
        #endif
    }

    private var videoSkipForwardSeconds: Int {
        #if os(iOS)
        store.playbackPreferences.skipForwardSeconds
        #else
        10
        #endif
    }

    private var videoSkipBackwardSeconds: Int {
        #if os(iOS)
        store.playbackPreferences.skipBackwardSeconds
        #else
        10
        #endif
    }

    private func skipVideo(forward: Bool) {
        let delta = Double(forward ? videoSkipForwardSeconds : -videoSkipBackwardSeconds)
        #if os(iOS)
        playback.requestRelativeVideoSeek(delta)
        #else
        playback.seek(to: playback.elapsedSeconds + delta)
        #endif
    }

    #if os(iOS)
    private var videoTransport: some View {
        ScrollView {
            VStack(spacing: 14) {
                Capsule()
                    .fill(.white.opacity(0.25))
                    .frame(width: 38, height: 5)
                    .accessibilityHidden(true)
                HStack(spacing: 12) {
                    Button("Close player", glyph: .close) { finish() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("closePlayerButton")
                    Spacer(minLength: 0)
                    videoPictureInPictureButton
                    VideoRoutePicker()
                        .frame(width: 44, height: 44)
                    Button("Playback settings", glyph: .subtitles) { presentVideoSettings() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSettingsButton")
                    Button(isMuted ? "Unmute" : "Mute", glyph: isMuted ? .volumeHigh : .volumeOff, variant: .solid) {
                        isMuted.toggle()
                        playback.player.isMuted = isMuted
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoMuteButton")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .controlSize(.large)
                if let error = displayedError {
                    Text(error)
                        .font(.callout)
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Text(playback.request.item.name)
                    .font(.headline)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .foregroundStyle(.white.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
                VStack(spacing: 6) {
                    videoTimeline
                    HStack {
                        Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                        Spacer()
                        Text("−" + Duration.seconds(max(0, playback.request.item.durationSeconds - playback.elapsedSeconds)).formatted(.time(pattern: .minuteSecond)))
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.72))
                }
                HStack(spacing: 28) {
                    Button("Back \(videoSkipBackwardSeconds) seconds", glyph: videoSkipBackwardSeconds == 10 ? .seekBack10 : .rewind, size: 28) {
                        skipVideo(forward: false)
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSeekBackwardButton")

                    Button {
                        if playback.hasFinished { replay() } else { playback.togglePlayback() }
                    } label: {
                        if playback.hasFinished {
                            Label("Replay", systemImage: "arrow.counterclockwise")
                        } else {
                            Label(playback.videoPlaybackRequested ? "Pause" : "Play",
                                  glyph: playback.videoPlaybackRequested ? .pause : .play,
                                  variant: .solid, size: 28)
                        }
                    }
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.circle)
                    .tint(.white)
                    .foregroundStyle(.black)
                    .accessibilityIdentifier(playback.hasFinished ? "replayButton" : "videoPlayPauseButton")
                    .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))

                    Button("Forward \(videoSkipForwardSeconds) seconds", glyph: videoSkipForwardSeconds == 10 ? .seekForward10 : .fastForward, size: 28) {
                        skipVideo(forward: true)
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSeekForwardButton")
                }
                .controlSize(.large)
                .buttonStyle(LuminaQuietActionStyle())
                .frame(maxWidth: .infinity)
                .overlay(alignment: .trailing) { nextEpisodeTransport }
                videoContextActions(showNext: false)
                if let syncError = playback.syncError {
                    Text(syncError)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.72))
                }
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 18)
        .background(.ultraThinMaterial)
        .tint(.white)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoTabletopControls")
    }

    #endif

    var subtitleStyleBinding: Binding<VideoSubtitleStyle> {
        Binding(get: { VideoSubtitleStyle(encodedString: subtitleStyleData) },
                set: { subtitleStyleData = $0.encodedString })
    }

    @ViewBuilder private var nextEpisodeTransport: some View {
        if let episode = playback.nextEpisode {
            Button("Next episode", glyph: .next, variant: .solid, size: 24) {
                Task { await playback.playNextEpisode(); revealVideoChrome() }
            }
            .labelStyle(.iconOnly)
            .accessibilityHint(episode.name)
            .accessibilityIdentifier("nextEpisodeTransportButton")
            .focused($focusedVideoControl, equals: "nextEpisodeTransportButton")
            .disabled(!playback.canUseVideoActions)
        }
    }

    private var showsContextualNextEpisode: Bool {
        #if os(tvOS)
        playback.activeCredits != nil || playback.hasFinished
        #else
        // Visible chrome already provides Next in the transport row.
        playback.activeCredits != nil && !videoChromeVisible && !playback.hasFinished
        #endif
    }

    private func videoContextActions(showNext: Bool) -> some View {
        VStack(alignment: .trailing, spacing: 12) {
            if let intro = playback.activeIntro {
                Button("Skip intro") { Task { await playback.skip(intro); revealVideoChrome() } }
                    .accessibilityIdentifier("skipIntroButton")
                    .focused($focusedVideoControl, equals: "skipIntroButton")
                    .disabled(!playback.canUseVideoActions)
            }
            if let credits = playback.activeCredits {
                Button("Skip credits") { Task { await playback.skip(credits); revealVideoChrome() } }
                    .accessibilityIdentifier("skipCreditsButton")
                    .focused($focusedVideoControl, equals: "skipCreditsButton")
                    .disabled(!playback.canUseVideoActions)
            }
            if showNext, let episode = playback.nextEpisode {
                Button("Next episode", glyph: .next, variant: .solid, size: 24) {
                    Task { await playback.playNextEpisode(); revealVideoChrome() }
                }
                .accessibilityHint(episode.name)
                .accessibilityIdentifier("nextEpisodeButton")
                .focused($focusedVideoControl, equals: "nextEpisodeButton")
                .disabled(!playback.canUseVideoActions)
            }
            if playback.isSwitchingVideoStream {
                ProgressView("Preparing playback…").tint(.white)
            }
            if let error = playback.videoStreamError {
                Text(error).font(.callout).padding(10)
                    .background(.black.opacity(0.8), in: .rect(cornerRadius: 12))
                    .accessibilityIdentifier("videoActionError")
            }
        }
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .foregroundStyle(.white)
        .controlSize(.large)
        .frame(maxWidth: 480, alignment: .trailing)
        #if os(tvOS)
        .focusSection()
        #endif
    }

    var videoSettingsPanel: some View {
        VideoPlaybackSettings(
            tab: $videoSettingsTab,
            playback: playback,
            subtitleStyle: subtitleStyleBinding,
            videoGravity: $videoGravity,
            tracks: videoTracks,
            selectAudio: selectAudio,
            selectAudioAutomatically: selectAudioAutomatically,
            selectSubtitle: selectSubtitle,
            onDone: dismissVideoSettings
        )
        .frame(maxWidth: 1_560)
        .padding(34)
        .background {
            RoundedRectangle(cornerRadius: 30)
                .fill(.ultraThinMaterial)
                .overlay {
                    RoundedRectangle(cornerRadius: 30).fill(.black.opacity(0.76))
                }
        }
        .overlay { RoundedRectangle(cornerRadius: 30).strokeBorder(.white.opacity(0.14)) }
        .shadow(color: .black.opacity(0.45), radius: 30, y: 16)
    }

    func presentVideoSettings() {
        videoSettingsTab = .info
        #if os(tvOS)
        videoSettingsVisible = true
        revealVideoChrome()
        #else
        videoSettingsDetent = .medium
        videoSettingsPresented = true
        #endif
    }

    func dismissVideoSettings() {
        videoSettingsVisible = false
        revealVideoChrome()
    }

    private func toggleVideoChrome() {
        guard !videoSettingsVisible, !videoSettingsPresented, displayedError == nil, !playback.hasFinished else { return }
        if videoChromeVisible { hideVideoChrome() }
        else { revealVideoChrome() }
    }

    var videoSystemOverlaysVisible: Bool {
        videoChromeVisible || videoSettingsPresented || displayedError != nil || playback.hasFinished
    }

    private func hideVideoChrome() {
        #if os(iOS)
        guard !voiceOverEnabled else { return }
        #endif
        videoChromeHideTask?.cancel()
        focusedVideoControl = nil
        videoChromeVisible = false
        videoControlsSurfaceFocused = true
    }

    func revealVideoChrome() {
        #if os(iOS)
        videoChromeHideTask?.cancel()
        videoChromeVisible = true
        if videoControlsSurfaceFocused { videoControlsSurfaceFocused = false }
        guard playback.isPlaying, !playback.hasFinished, !videoSettingsVisible, !videoSettingsPresented,
              !videoIsScrubbing, displayedError == nil else { return }
        guard !voiceOverEnabled, !videoGestures.interacting else { return }
        videoChromeHideTask = Task {
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            hideVideoChrome()
        }
        #endif
    }

    func handleExitCommand() {
        if queuePresented { queuePresented = false }
        else { finish() }
    }

    func loadVideoTracks(for item: AVPlayerItem?) async {
        guard !playback.request.item.isAudio, let item else {
            videoTracks = VideoMediaTracks()
            return
        }
        guard videoTracks.item !== item else { return }
        let asset = item.asset
        let audioGroup = try? await asset.loadMediaSelectionGroup(for: .audible)
        let subtitleGroup = try? await asset.loadMediaSelectionGroup(for: .legible)
        guard !Task.isCancelled else { return }
        if !playback.isSwitchingVideoStream, let subtitleGroup {
            if playback.subtitlesExplicitlyOff, subtitleGroup.allowsEmptySelection {
                item.select(nil, in: subtitleGroup)
            } else if item.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: subtitleGroup) {
                applyDeviceSubtitlePreference(to: item, group: subtitleGroup)
            }
        }
        let next = VideoMediaTracks(audioGroup: audioGroup, subtitleGroup: subtitleGroup, currentItem: item)
        guard item === playback.player.currentItem else { return }
        videoTracks = next
    }

    #if os(tvOS)
    func nativeTVMediaSelection(_ option: AVMediaSelectionOption?, _ group: AVMediaSelectionGroup) {
        guard group === videoTracks.subtitleGroup else { return }
        if playback.player.currentItem?.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: group) == true {
            selectSubtitle(.automatic)
        } else if let option, let index = videoTracks.subtitleOptions.firstIndex(where: { $0 === option }) {
            selectSubtitle(.option(index))
        } else if option == nil {
            selectSubtitle(.off)
        }
    }
    #endif

    /// Defaults apply once to a new item; an explicit per-film selection wins thereafter.
    private func applyDeviceSubtitlePreference(to item: AVPlayerItem, group: AVMediaSelectionGroup) {
        let preferences = store.playbackPreferences
        guard preferences.subtitleMode != .automatic else { return }
        let options = AVMediaSelectionGroup.playableMediaSelectionOptions(from: group.options)
        let candidates: [AVMediaSelectionOption]
        switch preferences.subtitleMode {
        case .automatic: return
        case .off:
            if group.allowsEmptySelection { item.select(nil, in: group) }
            return
        case .forcedOnly:
            candidates = options.filter { $0.hasMediaCharacteristic(.containsOnlyForcedSubtitles) }
        case .always:
            candidates = options.filter { !$0.hasMediaCharacteristic(.containsOnlyForcedSubtitles) }
        }
        let selected: AVMediaSelectionOption?
        if preferences.preferredSubtitleLanguages.isEmpty {
            selected = candidates.first(where: { $0 === group.defaultOption }) ?? candidates.first
        } else {
            let matching = preferences.preferredSubtitleLanguages.lazy.compactMap { language in
                candidates.first { option in
                    NativePlaybackPreferences.languageMatches(option.extendedLanguageTag ?? option.locale?.identifier,
                                                              preferred: language)
                }
            }.first
            selected = matching ?? (preferences.subtitleMode == .always
                ? candidates.first(where: { $0 === group.defaultOption }) ?? candidates.first : nil)
        }
        if let selected { item.select(selected, in: group) }
        else if group.allowsEmptySelection { item.select(nil, in: group) }
    }

    func selectAudio(_ index: Int) {
        guard let group = videoTracks.audioGroup, videoTracks.audioOptions.indices.contains(index),
              let item = playback.player.currentItem, videoTracks.item === item else { return }
        item.select(videoTracks.audioOptions[index], in: group)
        videoTracks.selectAudio(index)
    }

    func selectAudioAutomatically() {
        guard let group = videoTracks.audioGroup, let item = playback.player.currentItem, videoTracks.item === item else { return }
        item.selectMediaOptionAutomatically(in: group)
        videoTracks.markAudioAutomatic()
    }

    func selectSubtitle(_ selection: VideoSubtitleSelection) {
        guard let group = videoTracks.subtitleGroup, let item = playback.player.currentItem, videoTracks.item === item else { return }
        switch selection {
        case .automatic:
            playback.subtitlesExplicitlyOff = false
            playback.selectEmbeddedSubtitle(nil)
            item.selectMediaOptionAutomatically(in: group)
            videoTracks.markSubtitlesAutomatic()
        case .off:
            guard group.allowsEmptySelection else { return }
            playback.subtitlesExplicitlyOff = true
            playback.selectEmbeddedSubtitle(nil)
            item.select(nil, in: group)
            videoTracks.markSubtitlesOff()
        case let .option(index):
            guard videoTracks.subtitleOptions.indices.contains(index) else { return }
            playback.subtitlesExplicitlyOff = false
            let option = videoTracks.subtitleOptions[index]
            playback.selectEmbeddedSubtitle(option)
            item.select(option, in: group)
            videoTracks.selectSubtitle(index)
        }
    }

    #if os(iOS)
    private func videoOverlayIOS(safeArea: EdgeInsets) -> some View {
        ZStack {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Button("Close player", glyph: .close, size: 24) { finish() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("closePlayerButton")
                        .frame(width: 44, height: 44)
                    videoPictureInPictureButton
                        .frame(width: 44, height: 44)
                    Spacer(minLength: 0)
                    Button("Playback info", glyph: .info, size: 24) {
                        presentVideoSettings()
                        videoSettingsDetent = .large
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoInfoButton")
                    .frame(width: 44, height: 44)
                    videoMuteControl
                }
                .buttonStyle(.plain)
                .foregroundStyle(.white)
                .padding(.leading, max(0, safeArea.leading - videoHorizontalInset))
                .padding(.trailing, max(0, safeArea.trailing - videoHorizontalInset))

                Spacer(minLength: 0)

                VStack(alignment: .leading, spacing: 10) {
                    if let error = displayedError {
                        Text(error)
                            .font(.callout)
                            .padding(10)
                            .background(.black.opacity(0.7), in: .rect(cornerRadius: 12))
                    }
                    if let syncError = playback.syncError {
                        Text(syncError)
                            .font(.caption)
                            .padding(10)
                            .background(.black.opacity(0.7), in: .rect(cornerRadius: 12))
                    }
                    NativeVideoCastStatus(playback: playback)
                    videoOverlayBottomContent
                }
            }

            videoCenterTransport
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, videoHorizontalInset)
        .padding(.vertical, videoVerticalInset)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoPlaybackChrome")
    }

    private var videoOverlayBottomContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            videoPlaybackMetadata
            videoUtilityControls
        }
    }

    private var videoPlaybackMetadata: some View {
        let item = playback.request.item
        return VStack(alignment: .leading, spacing: 8) {
            if item.type == "Episode", let seriesName = item.seriesName, !seriesName.isEmpty {
                Text(seriesName)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.8))
            }
            Text(item.type == "Episode" ? item.homeHeroSubtitle ?? item.name : item.name)
                .font(.headline)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            videoTimeline
            HStack {
                Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                Spacer()
                Text("−" + Duration.seconds(max(0, item.durationSeconds - playback.elapsedSeconds)).formatted(.time(pattern: .minuteSecond)))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.white.opacity(0.72))
        }
    }

    private var videoMuteControl: some View {
        Button(playback.videoOutputMuted ? "Unmute" : "Mute", glyph: playback.videoOutputMuted ? .volumeOff : .volumeHigh, size: 24) {
            playback.setVideoOutputMuted(!playback.videoOutputMuted)
            isMuted = playback.player.isMuted
        }
        .labelStyle(.iconOnly)
        .accessibilityIdentifier("videoMuteButton")
        .disabled(playback.isVideoOutputUnavailable)
        .frame(width: 44, height: 44)
    }

    private var videoUtilityControls: some View {
        HStack(spacing: 0) {
            Button("Playback settings", glyph: .settings, size: 24) { presentVideoSettings() }
                .labelStyle(.iconOnly)
                .accessibilityIdentifier("videoSettingsButton")
                .frame(width: 44, height: 44)
                .disabled(playback.isCasting)
            Spacer(minLength: 12)
            HStack(spacing: 12) {
                VideoRoutePicker(active: playback.player.isExternalPlaybackActive)
                    .frame(width: 44, height: 44)
                    .disabled(playback.isCasting)
                NativeVideoCastButton(playback: playback)
                    .frame(width: 44, height: 44)
            }
            Spacer(minLength: 12)
            if playback.nextEpisode != nil {
                nextEpisodeTransport
                    .frame(width: 44, height: 44)
            } else {
                Color.clear.frame(width: 44, height: 44)
                    .accessibilityHidden(true)
            }
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white)
    }

    private var videoCenterTransport: some View {
        HStack(spacing: 24) {
            Button("Back \(videoSkipBackwardSeconds) seconds", glyph: videoSkipBackwardSeconds == 10 ? .seekBack10 : .rewind, size: 32) {
                skipVideo(forward: false)
                revealVideoChrome()
            }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("videoSeekBackwardButton")
            .disabled(!playback.canSeekVideo)
            .frame(width: 72, height: 72)

            Button {
                if playback.hasFinished { replay() } else { playback.togglePlayback() }
                revealVideoChrome()
            } label: {
                if playback.hasFinished {
                    LuminaIcon(glyph: .play, variant: .solid, size: 56)
                } else {
                    LuminaIcon(glyph: playback.videoPlaybackRequested ? .pause : .play,
                               variant: .solid,
                               size: 56)
                }
            }
            .accessibilityLabel(playback.hasFinished ? "Replay" : playback.videoPlaybackRequested ? "Pause" : "Play")
            .accessibilityIdentifier(playback.hasFinished ? "replayButton" : "videoPlayPauseButton")
            .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
            .frame(width: 72, height: 72)

            Button("Forward \(videoSkipForwardSeconds) seconds", glyph: videoSkipForwardSeconds == 10 ? .seekForward10 : .fastForward, size: 32) {
                skipVideo(forward: true)
                revealVideoChrome()
            }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("videoSeekForwardButton")
            .disabled(!playback.canSeekVideo)
            .frame(width: 72, height: 72)
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white)
        .accessibilityElement(children: .contain)
    }
    #endif

    private var preferredContextualControl: String? {
        if playback.activeIntro != nil { return "skipIntroButton" }
        if playback.activeCredits != nil { return "skipCreditsButton" }
        if playback.hasFinished, playback.nextEpisode != nil { return "nextEpisodeButton" }
        return nil
    }

    private var videoScrim: some View {
        LinearGradient(colors: [.black.opacity(0.72), .clear, .black.opacity(0.76)],
                       startPoint: .top, endPoint: .bottom)
    }

    var playbackTimeProbe: some View {
        VStack(spacing: 0) {
            Color.clear
                .frame(width: 1, height: 1)
                .accessibilityElement()
                .accessibilityLabel("Playback seconds")
                .accessibilityIdentifier("playbackTime")
                .accessibilityValue(String(Int(playback.elapsedSeconds)))
            #if LUMINA_UI_TESTING
            Color.clear
                .frame(width: 1, height: 1)
                .accessibilityElement()
                .accessibilityLabel("System Now Playing artwork")
                .accessibilityIdentifier("nowPlayingArtwork")
                .accessibilityValue(systemArtworkProbeValue)
            #endif
        }
    }

}
