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
struct PlaybackScreen: View {
    @Environment(\.dismiss) var dismiss
    @Environment(\.scenePhase) var scenePhase
    @Environment(\.dynamicTypeSize) var dynamicTypeSize
    @Environment(NativeAudioSession.self) var audioSession
    @State var requestedPlayback: PlaybackModel
    @State private var startedPresentation = false
    @State private var attemptedAudioAdoption = false
    @State private var presentationError: String?
    @State var queuePresented = false
    @State var audioOptionsFocusRequest = 0
    @State var isMuted = false
    @State var videoChromeVisible = true
    @State var pictureInPicturePossible = false
    @State var pictureInPictureActive = false
    @State var pictureInPictureContinuing = false
    @State var pictureInPictureStartRequest = 0
    @State var videoSettingsVisible = false
    @State var videoSettingsTab: VideoSettingsTab = .info
    @State var videoSettingsPresented = false
    @State var videoGravity: AVLayerVideoGravity = .resizeAspect
    @State var videoTracks = VideoMediaTracks()
    @AppStorage("native.video.subtitleAppearance") var subtitleStyleData = ""
    @State var videoChromeHideTask: Task<Void, Never>?
    @State private var videoTracksLoadTask: Task<Void, Never>?
    @FocusState var videoControlsSurfaceFocused: Bool
    @FocusState var focusedVideoControl: String?
    #if os(tvOS)
    enum TVAudioFocus: Hashable {
        case artwork
        case queue
        case playPause
        case repeatMode
        case stop
        case timeline
    }
    @FocusState var focusedAudioControl: TVAudioFocus?
    #endif
    @State var videoIsScrubbing = false
    #if os(iOS)
    @Environment(\.accessibilityReduceMotion) var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) var voiceOverEnabled
    @State var videoGestures = VideoGesturePresentation()
    @State var videoGestureCancellationID = UUID()
    @State var videoGestureContainerSize = CGSize.zero
    @State private var videoGestureItemID: ObjectIdentifier?
    @State var videoSettingsDetent: PresentationDetent = .medium
    enum AudioCompanion { case lyrics, queue }
    @State var audioCompanion = AudioCompanion.lyrics
    @State var audioOptionsPresented = false
    @State var audioQualityPresented = false
    @State var phoneLyricsPresented = false
    @State var audioStageSize = CGSize.zero
    @State var audioTimelineVisible = true
    @State var audioIsScrubbing = false
    @State var audioTimelineHideTask: Task<Void, Never>?
    #if LUMINA_UI_TESTING
    @State private var uiTestingDidBecomeActiveOutcome = "none"
    #endif
    #endif
    let store: LuminaStore
    let onVideoDismiss: (() -> Void)?
    let onReturnHome: () -> Void
    let onAutoplayStandby: (() -> Void)?

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem] = [], queueIndex: Int? = nil,
         onReturnHome: @escaping () -> Void = {}, onAutoplayStandby: (() -> Void)? = nil) {
        self.store = store
        onVideoDismiss = nil
        self.onReturnHome = onReturnHome
        self.onAutoplayStandby = onAutoplayStandby
        _requestedPlayback = State(initialValue: PlaybackModel(request: request, store: store, queue: queue, queueIndex: queueIndex))
    }

    init(playback: PlaybackModel, store: LuminaStore, onVideoDismiss: (() -> Void)? = nil,
         onReturnHome: @escaping () -> Void = {}, onAutoplayStandby: (() -> Void)? = nil) {
        self.store = store
        self.onVideoDismiss = onVideoDismiss
        self.onReturnHome = onReturnHome
        self.onAutoplayStandby = onAutoplayStandby
        _requestedPlayback = State(initialValue: playback)
    }

    var playback: PlaybackModel {
        if requestedPlayback.request.item.isAudio, attemptedAudioAdoption {
            return audioSession.playback ?? requestedPlayback
        }
        return requestedPlayback
    }
    var displayedError: String? { presentationError ?? playback.errorMessage }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            if requestedPlayback.request.item.isAudio {
                MediaBackdrop(item: playback.request.item, store: store).opacity(0.3)
                    .overlay {
                        LinearGradient(colors: [LuminaTheme.theater.opacity(0.35), LuminaTheme.theater],
                                       startPoint: .top, endPoint: .bottom)
                    }
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                if attemptedAudioAdoption, audioSession.playback == nil {
                    ContentUnavailableView {
                        Label("No playable track", glyph: .music, variant: .solid)
                    } description: {
                        Text(audioSession.message ?? "Your shared queue is paused. Browse Audio to manage requested albums.")
                    } actions: {
                        Button("Refresh shared queue") { Task { await audioSession.refreshSharedQueue() } }
                        Button("Browse Audio") { finish() }
                    }
                } else {
                    audioStage
                        #if os(tvOS)
                        .onPlayPauseCommand { playback.togglePlayback() }
                        .onExitCommand { handleExitCommand() }
                        #endif
                }
            } else {
                #if os(tvOS)
                TVNativeVideoPlayer(playback: playback, videoGravity: videoGravity,
                                    onSettings: presentVideoSettings, onDismiss: finish,
                                    onMediaSelection: nativeTVMediaSelection)
                    .ignoresSafeArea()
                #else
                GeometryReader { container in
                    videoStage(safeArea: container.safeAreaInsets).ignoresSafeArea()
                }
                #endif
            }
            #if !os(tvOS)
            if requestedPlayback.request.item.isAudio, ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                playbackTimeProbe
            }
            #endif
            #if LUMINA_UI_TESTING && os(iOS)
            if ProcessInfo.processInfo.arguments.contains("--ui-testing-rebuild-video-presentation") {
                Button("Rebuild video presentation") {
                    NotificationCenter.default.post(name: rebuildVideoPresentation, object: nil)
                }
                .accessibilityIdentifier("rebuildVideoPresentationButton")
                .padding(.top, 60)
            }
            if !requestedPlayback.request.item.isAudio {
                Color.clear.frame(width: 1, height: 1)
                    .accessibilityElement()
                    .accessibilityLabel("Gesture diagnostics")
                    .accessibilityIdentifier("videoGestureDiagnostics")
                    .accessibilityValue("player=\(ObjectIdentifier(playback.player).hashValue) rateEvents=\(playback.videoRateChangeCount) phase=\(String(describing: scenePhase)) appState=\(UIApplication.shared.applicationState.rawValue) gestureEnabled=\(scenePhase == .active && !pictureInPictureActive && !pictureInPictureContinuing && !videoSettingsPresented && !videoSettingsVisible && displayedError == nil && !playback.hasFinished && !playback.isSwitchingVideoStream) didBecome=\(uiTestingDidBecomeActiveOutcome) size=\(videoGestureContainerSize) modal=\(videoSettingsPresented) pip=\(pictureInPictureActive) playing=\(playback.isPlaying) requested=\(playback.videoPlaybackRequested) status=\(playback.player.timeControlStatus.rawValue) policy=\(playback.videoBackgroundPlaybackPolicyRawValue) rateReason=\(playback.lastVideoRateChangeReason) pauseReason=\(playback.lastVideoNativePauseReason) systemPauseInjections=\(playback.videoSystemPauseInjectionCount) next=\(playback.uiTestingNextEpisodeDiagnostics) brightness=\(videoGestures.screen.map { Double($0.brightness) } ?? -1) brightnessRequested=\(videoGestures.lastBrightnessRequest ?? -1) brightnessTargetMatchesDisplay=\(videoGestures.brightnessTargetMatchesDisplay ? 1 : 0) displayBrightness=\(videoGestures.displayBrightness ?? -1) playerVolume=\(playback.player.volume) actions=\(videoGestures.actionCount) last=\(videoGestures.lastAction)")
                Color.clear.frame(width: 1, height: 1)
                    .accessibilityElement()
                    .accessibilityLabel("System Picture in Picture")
                    .accessibilityIdentifier("pictureInPictureState")
                    .accessibilityValue(!AVPictureInPictureController.isPictureInPictureSupported() ? "Unsupported" : pictureInPictureActive ? "Active" : pictureInPicturePossible ? "Ready" : "Unavailable")
            }
            #endif
            #if os(tvOS)
            if displayedError != nil || playback.syncError != nil {
                VStack(alignment: .leading, spacing: 8) {
                    if let error = displayedError {
                        Text(error).padding().background(.black.opacity(0.8))
                        Button("Close player") { finish() }
                            .accessibilityIdentifier("closePlayerButton")
                    }
                    if let syncError = playback.syncError {
                        Text(syncError).font(.caption)
                    }
                }
                .foregroundStyle(.white)
                .padding()
                .background(.black.opacity(0.45))
            }
            if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                Color.clear
                    .frame(width: 1, height: 1)
                    .accessibilityElement()
                    .accessibilityLabel("Playback seconds")
                    .accessibilityIdentifier("playbackTime")
                    .accessibilityValue(String(Int(playback.elapsedSeconds)))
            }
            #endif
        }
        #if !os(tvOS)
        .ignoresSafeArea(.container, edges: requestedPlayback.request.item.isAudio && !usesStandardPhonePushedChrome ? .top : [])
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if requestedPlayback.request.item.isAudio {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    if !usesExpandedAudioLayout && !usesStandardPhonePushedChrome { audioHeaderActions }
                }
            }
        }
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarVisibility(requestedPlayback.request.item.isAudio && !usesExpandedAudioLayout && !usesStandardPhonePushedChrome ? .visible : .hidden, for: .navigationBar)
        .toolbarVisibility(.hidden, for: .tabBar)
        .statusBarHidden(!requestedPlayback.request.item.isAudio && !videoSystemOverlaysVisible)
        .persistentSystemOverlays(requestedPlayback.request.item.isAudio || videoSystemOverlaysVisible ? .visible : .hidden)
        #endif
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
        .preferredColorScheme(.dark)
        .sheet(isPresented: $queuePresented) { audioQueue }
        #if os(tvOS)
        .fullScreenCover(isPresented: $videoSettingsVisible) {
            ZStack {
                Color.black.opacity(0.9).ignoresSafeArea()
                videoSettingsPanel.padding(60)
            }
            .preferredColorScheme(.dark)
            .onExitCommand { dismissVideoSettings() }
            .onPlayPauseCommand { playback.togglePlayback() }
        }
        #else
        .sheet(isPresented: $videoSettingsPresented) {
            VideoPlaybackSettings(
                tab: $videoSettingsTab,
                playback: playback,
                subtitleStyle: subtitleStyleBinding,
                videoGravity: $videoGravity,
                tracks: videoTracks,
                selectAudio: selectAudio,
                selectAudioAutomatically: selectAudioAutomatically,
                selectSubtitle: selectSubtitle,
                gestures: videoGestures,
                onDone: { videoSettingsPresented = false }
            )
            .background(LuminaTheme.theater)
            .preferredColorScheme(.dark)
            .presentationDetents([.medium, .large], selection: $videoSettingsDetent)
        }
        #endif
        .task {
            guard !startedPresentation else { return }
            startedPresentation = true
            let initial = requestedPlayback
            initial.setActive(scenePhase == .active || pictureInPictureContinuing)
            if initial.request.item.isAudio {
                do { try await audioSession.adoptForPlayback(initial) }
                catch { presentationError = error.localizedDescription }
                attemptedAudioAdoption = true
            } else if onVideoDismiss != nil {
                isMuted = initial.player.isMuted
                revealVideoChrome()
            } else {
                audioSession.stop()
                await initial.start()
            }
        }
        .task(id: playback.request.id) {
            await playback.refreshVideoActions()
        }
        .background {
            Color.clear
                .alert("Still watching?", isPresented: Binding(
                    get: { playback.episodeAutoplay.confirmationDeadline != nil },
                    set: { _ in }
                )) {
                    Button("Continue watching") {
                        Task {
                            if playback.episodeAutoplay.confirmationExpired() { finishAfterPresenceTimeout(); return }
                            guard playback.confirmEpisodeAutoplay() else { return }
                            await playback.playNextEpisode(automatically: true)
                        }
                    }
                    Button("Go Home", role: .cancel) { finishAndReturnHome() }
                } message: {
                    Text("Continue watching, or Lumina will \(onAutoplayStandby == nil ? "return Home" : "enter standby") after \(Duration.seconds(playback.episodeAutoplay.policy.timeoutSeconds).formatted(.units(allowed: [.hours, .minutes, .seconds], width: .wide))) without a response.")
                }
                #if os(tvOS)
                // The library’s white tint makes the focused system-alert action unreadable.
                .tint(nil)
                #endif
        }
        .task(id: playback.episodeAutoplay.confirmationDeadline) {
            guard let deadline = playback.episodeAutoplay.confirmationDeadline else { return }
            while !Task.isCancelled {
                let remaining = deadline.timeIntervalSinceNow
                if remaining <= 0 {
                    guard playback.episodeAutoplay.confirmationExpired() else { return }
                    finishAfterPresenceTimeout()
                    return
                }
                do { try await Task.sleep(for: .seconds(remaining)) }
                catch { return }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                if playback.episodeAutoplay.confirmationExpired() {
                    finishAfterPresenceTimeout()
                    return
                }
            }
            playback.traceContinuity("scene=\(String(describing: phase))")
            playback.setActive(phase == .active || pictureInPictureContinuing)
            #if os(iOS)
            if phase == .active, !playback.request.item.isAudio {
                playback.recoverSystemBackgroundPauseIfNeeded()
            }
            if phase != .active { cancelVideoGestures(releaseBrightness: true) }
            #endif
        }
        .onChange(of: playback.isPlaying) { _, isPlaying in
            guard !playback.request.item.isAudio else { return }
            if isPlaying { revealVideoChrome() }
            else {
                videoChromeHideTask?.cancel()
                videoChromeVisible = true
                #if os(iOS)
                if videoGestures.hasTemporarySpeedHold { cancelVideoGestures(releaseBrightness: false) }
                #endif
            }
        }
        .onChange(of: focusedVideoControl) { _, control in
            if control != nil { revealVideoChrome() }
        }
        .onChange(of: playback.hasFinished) { _, finished in
            guard !playback.request.item.isAudio, finished else { return }
            #if os(iOS)
            cancelVideoGestures(releaseBrightness: false)
            #endif
            videoChromeHideTask?.cancel()
            videoChromeVisible = true
        }
        .onReceive(playback.player.publisher(for: \.currentItem, options: [.initial, .new])) { item in
            guard !playback.request.item.isAudio else { return }
            #if os(iOS)
            let identity = item.map(ObjectIdentifier.init)
            if videoGestureItemID != identity {
                videoGestureItemID = identity
                cancelVideoGestures(releaseBrightness: false)
            }
            #endif
            item?.textStyleRules = VideoSubtitleStyle(encodedString: subtitleStyleData).makeTextStyleRules()
            videoTracksLoadTask?.cancel()
            videoTracksLoadTask = Task { await loadVideoTracks(for: item) }
        }
        .onReceive(NotificationCenter.default.publisher(for: AVPlayerItem.mediaSelectionDidChangeNotification)) { notification in
            guard !playback.request.item.isAudio,
                  let item = notification.object as? AVPlayerItem,
                  item === playback.player.currentItem, videoTracks.item === item else { return }
            videoTracks.refreshSelection(from: item)
        }
        .onChange(of: subtitleStyleData) { _, data in
            playback.player.currentItem?.textStyleRules = VideoSubtitleStyle(encodedString: data).makeTextStyleRules()
        }
        .onChange(of: videoSettingsPresented) { _, isPresented in
            #if os(iOS)
            if isPresented { cancelVideoGestures(releaseBrightness: false) }
            #endif
            if !isPresented { revealVideoChrome() }
        }
        .onDisappear {
            #if os(tvOS)
            // A settings cover is part of this playback presentation, not its exit.
            guard !videoSettingsVisible else { return }
            #endif
            videoChromeHideTask?.cancel()
            videoTracksLoadTask?.cancel()
            #if os(iOS)
            cancelVideoGestures(releaseBrightness: true)
            #endif
            if !requestedPlayback.request.item.isAudio, onVideoDismiss == nil {
                playback.close()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            playback.recordPlaybackDiagnostic(.appResignActive)
            #if os(iOS)
            cancelVideoGestures(releaseBrightness: true)
            #endif
            playback.setActive(pictureInPictureContinuing)
        }
        #if os(iOS) && LUMINA_UI_TESTING
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
            playback.injectSystemBackgroundPauseForUITesting()
        }
        #endif
        #if os(iOS)
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            playback.recordPlaybackDiagnostic(.appBecomeActive)
            #if LUMINA_UI_TESTING
            uiTestingDidBecomeActiveOutcome = "seen-appState=\(UIApplication.shared.applicationState.rawValue)-\(scenePhase == .active ? "accepted" : "ignored")"
            #endif
            guard scenePhase == .active, !playback.request.item.isAudio else { return }
            playback.setActive(true)
            playback.recoverSystemBackgroundPauseIfNeeded()
        }
        #endif
        #if os(iOS)
        .onChange(of: voiceOverEnabled) { _, enabled in
            cancelVideoGestures(releaseBrightness: false)
            if enabled { revealVideoChrome() }
        }
        .onChange(of: videoGestureSettingsFingerprint) { _, _ in
            cancelVideoGestures(releaseBrightness: !store.playbackPreferences.videoGesturesEnabled || !store.playbackPreferences.brightnessSwipeEnabled)
        }
        .onChange(of: videoGestures.interacting) { _, active in
            if active { videoChromeHideTask?.cancel() }
            else if videoChromeVisible { revealVideoChrome() }
        }
        .onChange(of: pictureInPictureActive) { _, active in
            playback.recordPlaybackDiagnostic(.pictureInPictureChanged, nativeReason: active ? 1 : 0)
            if active { cancelVideoGestures(releaseBrightness: true) }
        }
        .onChange(of: pictureInPictureContinuing) { _, active in
            if active { cancelVideoGestures(releaseBrightness: true) }
        }
        .onChange(of: displayedError) { _, error in
            if error != nil { cancelVideoGestures(releaseBrightness: false) }
        }
        .onChange(of: playback.isCasting) { _, _ in
            cancelVideoGestures(releaseBrightness: false)
            revealVideoChrome()
        }
        .onChange(of: playback.isSwitchingVideoStream) { _, changing in
            if changing { cancelVideoGestures(releaseBrightness: false) }
        }
        .onChange(of: playback.preferredVideoRate) { _, _ in cancelVideoGestures(releaseBrightness: false) }
        .onChange(of: playback.videoPlaybackRequested) { _, requested in
            if !requested { cancelVideoGestures(releaseBrightness: false) }
        }
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { notification in
            let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ?? 0
            playback.recordPlaybackDiagnostic(.audioRouteChanged, nativeReason: Int(clamping: reason))
            cancelVideoGestures(releaseBrightness: false)
        }
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) { notification in
            guard !requestedPlayback.request.item.isAudio else { return }
            let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt ?? 99
            if type == AVAudioSession.InterruptionType.began.rawValue {
                playback.recordPlaybackDiagnostic(.audioInterruptionBegan, nativeReason: Int(clamping: type))
            } else if type == AVAudioSession.InterruptionType.ended.rawValue {
                playback.recordPlaybackDiagnostic(.audioInterruptionEnded, nativeReason: Int(clamping: type))
            }
            playback.traceContinuity("audio-interruption=\(notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt ?? 99)")
            cancelVideoGestures(releaseBrightness: true)
            if (notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt) == AVAudioSession.InterruptionType.began.rawValue {
                playback.pause()
            }
        }
        #endif
    }

}

@MainActor @Observable
final class PlaybackModel {
    enum RepeatMode: CaseIterable {
        case off, all, one
        var label: String { switch self { case .off: "Repeat off"; case .all: "Repeat queue"; case .one: "Repeat track" } }
        var symbol: String { self == .one ? "repeat.1" : "repeat" }
    }
    let player: AVPlayer
    #if os(iOS)
    @ObservationIgnored weak var castOwner: NativeVideoCasting?
    #endif
    var isCasting: Bool {
        #if os(iOS)
        castOwner.map { $0.isCasting || $0.canReturnToDevice } ?? false
        #else
        false
        #endif
    }
    var videoOutputMuted: Bool {
        #if os(iOS)
        if let castOwner, isCasting { return castOwner.isMuted }
        #endif
        return player.isMuted
    }
    func setVideoOutputMuted(_ muted: Bool) {
        #if os(iOS)
        if let castOwner, isCasting {
            if castOwner.isMuted != muted { castOwner.toggleMute() }
            return
        }
        #endif
        player.isMuted = muted
    }
    private let lifecycleToken = UUID().uuidString
    #if LUMINA_UI_TESTING
    @ObservationIgnored private let continuityTrace = PlaybackContinuityTrace()
    #endif
    // Opt-in simulator diagnostics contain transport state only, never media URLs or titles.
    func traceContinuity(_ event: @autoclosure () -> String) {
        var enabled = PlaybackDiagnostics.shared.isCapturing
        #if LUMINA_UI_TESTING
        enabled = enabled || PlaybackContinuityTrace.enabled
        #endif
        guard enabled else { return }
        let event = event()
        if PlaybackDiagnostics.shared.isCapturing, !request.item.isAudio {
            let parts = event.split(separator: "=", maxSplits: 1).map(String.init)
            let kind: PlaybackDiagnosticEvent?
            switch parts.first {
            case "initial-seek": kind = .initialSeek
            case "periodic-callback": kind = .sample
            case "control-status-callback": kind = .timeControlChanged
            case "close": kind = .close
            case "scene":
                switch parts.last {
                case "active": kind = .sceneActive
                case "inactive": kind = .sceneInactive
                case "background": kind = .sceneBackground
                default: kind = nil
                }
            default: kind = nil
            }
            if let kind {
                recordPlaybackDiagnostic(kind, seekTarget: kind == .initialSeek ? request.startPositionSeconds : nil)
            }
        }
        #if LUMINA_UI_TESTING
        guard PlaybackContinuityTrace.enabled,
              !request.item.isAudio else { return }
        let item = player.currentItem
        continuityTrace.record(event, state: "model=\(lifecycleToken) player=\(ObjectIdentifier(player)) item=\(item.map { String(describing: ObjectIdentifier($0)) } ?? "nil") time=\(player.currentTime().seconds) saved=\(request.startPositionSeconds) rate=\(player.rate) status=\(player.timeControlStatus.rawValue) waiting=\(player.reasonForWaitingToPlay?.rawValue ?? "none") ready=\(item?.status.rawValue ?? -1) empty=\(item?.isPlaybackBufferEmpty ?? false) keepUp=\(item?.isPlaybackLikelyToKeepUp ?? false) requested=\(autoplayWhenReady) closed=\(closed) queuedReports=\(pendingReports.count)")
        #endif
    }

    func recordPlaybackDiagnostic(_ event: PlaybackDiagnosticEvent, seekTarget: Double? = nil,
                                  requestedPosition: Double? = nil, nativeReason: Int? = nil,
                                  rateReason: AVPlayer.RateDidChangeReason? = nil, seekFinished: Bool? = nil,
                                  includeNegotiation: Bool = false, failure: Error? = nil) {
        guard !request.item.isAudio, PlaybackDiagnostics.shared.isCapturing else { return }
        var sample = PlaybackDiagnosticSample()
        if includeNegotiation || event == .modelCreated || event == .start {
            sample.negotiation = request.negotiationDiagnostics
        }
        let item = player.currentItem
        sample.modelID = UUID(uuidString: lifecycleToken)
        sample.playerID = PlaybackDiagnostics.identity(player)
        sample.itemID = PlaybackDiagnostics.identity(item)
        sample.positionSeconds = player.currentTime().seconds
        sample.rate = Double(player.rate)
        sample.timeControlStatus = player.timeControlStatus.rawValue
        sample.itemStatus = item?.status.rawValue
        sample.playbackRequested = videoPlaybackRequested
        sample.bufferEmpty = item?.isPlaybackBufferEmpty
        sample.likelyToKeepUp = item?.isPlaybackLikelyToKeepUp
        sample.bufferedUntilSeconds = item?.loadedTimeRanges.map { $0.timeRangeValue.end.seconds }.filter { $0.isFinite }.max()
        sample.seekTargetSeconds = seekTarget
        sample.requestedPositionSeconds = requestedPosition
        sample.sceneState = UIApplication.shared.applicationState.rawValue
        #if os(iOS)
        sample.lowPowerModeEnabled = ProcessInfo.processInfo.isLowPowerModeEnabled
        #endif
        sample.usesClientDemux = request.usesClientDemux
        sample.nativeReason = nativeReason
        sample.seekFinished = seekFinished
        if event == .rateChanged {
            if rateReason == .appBackgrounded { sample.rateChangeReason = .appBackgrounded }
            else if rateReason == .setRateCalled { sample.rateChangeReason = .setRateCalled }
            else if rateReason == .audioSessionInterrupted { sample.rateChangeReason = .audioSessionInterrupted }
            else if rateReason == .setRateFailed { sample.rateChangeReason = .setRateFailed }
            else { sample.rateChangeReason = rateReason == nil ? .unknown : .other }
        }
        if let reason = player.reasonForWaitingToPlay {
            switch reason {
            case .toMinimizeStalls: sample.waitingReason = .minimizeStalls
            case .evaluatingBufferingRate: sample.waitingReason = .evaluateBuffering
            case .noItemToPlay: sample.waitingReason = .noItem
            default: sample.waitingReason = .other
            }
        }
        if event == .accessLog, let log = item?.accessLog()?.events.last {
            sample.accessStalls = log.numberOfStalls
            sample.accessObservedBitrate = log.observedBitrate
            sample.accessIndicatedBitrate = log.indicatedBitrate
        }
        if let failure {
            let error = failure as NSError
            sample.errorCode = error.code
            switch error.domain {
            case AVFoundationErrorDomain: sample.errorDomain = .avFoundation
            case NSURLErrorDomain: sample.errorDomain = .url
            case NSOSStatusErrorDomain: sample.errorDomain = .osStatus
            default: sample.errorDomain = .other
            }
        } else if event == .errorLog, let log = item?.errorLog()?.events.last {
            sample.errorCode = log.errorStatusCode
            switch log.errorDomain {
            case AVFoundationErrorDomain: sample.errorDomain = .avFoundation
            case NSURLErrorDomain: sample.errorDomain = .url
            case NSOSStatusErrorDomain: sample.errorDomain = .osStatus
            default: sample.errorDomain = .other
            }
        }
        PlaybackDiagnostics.shared.record(event, sample: sample)
    }

    func installPlaybackDiagnosticObservers() {
        guard !request.item.isAudio, playbackDiagnosticObservers.isEmpty else { return }
        let events: [(Notification.Name, PlaybackDiagnosticEvent)] = [
            (AVPlayerItem.timeJumpedNotification, .timeJump),
            (AVPlayerItem.newAccessLogEntryNotification, .accessLog),
            (AVPlayerItem.newErrorLogEntryNotification, .errorLog)
        ]
        for (name, event) in events {
            let token = NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] notification in
                guard let item = notification.object as? AVPlayerItem else { return }
                let identity = ObjectIdentifier(item)
                MainActor.assumeIsolated {
                    guard let self, self.player.currentItem.map(ObjectIdentifier.init) == identity else { return }
                    self.recordPlaybackDiagnostic(event)
                }
            }
            playbackDiagnosticObservers.append(token)
        }
        let failure = NotificationCenter.default.addObserver(forName: AVPlayerItem.failedToPlayToEndTimeNotification,
                                                            object: nil, queue: .main) { [weak self] notification in
            guard let item = notification.object as? AVPlayerItem else { return }
            let identity = ObjectIdentifier(item)
            let error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error ?? item.error
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem.map(ObjectIdentifier.init) == identity else { return }
                await self.handleVideoPlaybackFailure(error)
            }
        }
        playbackDiagnosticObservers.append(failure)
    }

    func playNativePlayer() {
        guard !isCasting else { return }
        guard videoDolbyVisionConversion?.hasFailure != true else {
            pauseNativePlayer()
            return
        }
        recordPlaybackDiagnostic(.play)
        player.play()
    }

    func pauseNativePlayer() {
        recordPlaybackDiagnostic(.pause)
        #if os(tvOS)
        isPausingNativePlayer = true
        defer { isPausingNativePlayer = false }
        #endif
        player.pause()
    }

    func replaceNativeItem(_ item: AVPlayerItem?) {
        player.replaceCurrentItem(with: item)
        recordPlaybackDiagnostic(.itemReplaced)
    }

    func seekNativePlayer(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) async -> Bool {
        recordPlaybackDiagnostic(.seekRequested, seekTarget: time.seconds)
        let result = await player.seek(to: time, toleranceBefore: toleranceBefore, toleranceAfter: toleranceAfter)
        recordPlaybackDiagnostic(.seekCompleted, seekTarget: time.seconds, seekFinished: result)
        return result
    }

    var request: PlaybackRequest
    let store: LuminaStore
    weak var audioOwner: NativeAudioSession?
    var audioTransport: NativeMusicTransport?
    var reportedAudioStart = false
    let initiallyPaused: Bool
    var elapsedSeconds: Double
    var hasFinished = false
    var isReplaying = false
    var errorMessage: String?
    var syncError: String?
    var isPlaying = false
    var queue: [MediaItem]
    var occurrenceIDs: [UUID]
    typealias QueueRow = QueuePresentationRow<MediaItem>
    private(set) var queueRows: [QueueRow]

    private static func makeQueueRows(items: [MediaItem], occurrenceIDs: [UUID]) -> [QueueRow] {
        QueuePresentation.rows(items: items, occurrenceIDs: occurrenceIDs, itemID: { $0.id })
    }
    func rebuildQueueRows() { queueRows = Self.makeQueueRows(items: queue, occurrenceIDs: occurrenceIDs) }
    func occurrenceIndex(_ id: UUID) -> Int? { occurrenceIDs.firstIndex(of: id) }
    func playOccurrence(_ id: UUID) async {
        guard let index = occurrenceIndex(id) else { return }
        await playQueued(offset: index - queueIndex)
    }
    func moveOccurrence(_ id: UUID, offset: Int? = nil) {
        guard let index = occurrenceIndex(id) else { return }
        moveQueued(at: index, to: offset.map { index + $0 } ?? queueIndex + 1)
    }
    func removeOccurrence(_ id: UUID) {
        guard let index = occurrenceIndex(id) else { return }
        removeQueued(at: index)
    }
    var repeatMode = RepeatMode.off
    var queueIndex: Int
    var hasNext: Bool { queueIndex + 1 < queue.count }
    var hasPrevious: Bool { !queue.isEmpty && queueIndex > 0 }
    var timeObserver: Any?
    var controlObservation: NSKeyValueObservation?
    var endObserver: NSObjectProtocol?
    nonisolated(unsafe) var playbackDiagnosticObservers: [NSObjectProtocol] = []
    #if LUMINA_UI_TESTING
    private var uiTestingInjectedClientDemuxFailure = false
    private var uiTestingInjectedServerVideoRecoveryFailure = false
    private var uiTestingInjectedServerMediaRecoveryFailure = false
    #endif
    // NotificationCenter permits unregistering its token during nonisolated deinit.
    nonisolated(unsafe) private var videoRateChangeObserver: NSObjectProtocol?
    #if os(tvOS)
    private var isPausingNativePlayer = false
    #endif
    #if os(iOS)
    @ObservationIgnored weak var pendingSystemBackgroundPauseItem: AVPlayerItem?
    #endif
    #if os(iOS) && LUMINA_UI_TESTING
    @ObservationIgnored private(set) var videoRateChangeCount = 0
    private(set) var lastVideoRateChangeReason = "none"
    private(set) var lastVideoNativePauseReason = "none"
    private(set) var videoSystemPauseInjectionCount = 0
    private(set) var uiTestingNextEpisodeDiagnostics = "initial"

    private func recordNextEpisodeDiagnostic(_ outcome: String) {
        uiTestingNextEpisodeDiagnostics = "\(outcome)-finished=\(hasFinished ? 1 : 0)-switching=\(isSwitchingVideoStream ? 1 : 0)-taskcancelled=\(Task.isCancelled ? 1 : 0)"
    }

    private func nextEpisodeFailureKind(_ error: Error) -> String {
        if error is CancellationError { return "cancelled" }
        let error = error as NSError
        switch error.domain {
        case NSURLErrorDomain: return "url-\(error.code)"
        case NSPOSIXErrorDomain: return "posix-\(error.code)"
        default: return "other"
        }
    }

    var videoBackgroundPlaybackPolicyRawValue: Int {
        request.item.isAudio ? -1 : player.audiovisualBackgroundPlaybackPolicy.rawValue
    }
    #endif
    var videoSource = NativeVideoSource() {
        didSet { videoDolbyVisionConversion = nil }
    }
    var videoSourceBytesRead: Int64? { videoSource.sourceBytesRead }
    var videoDolbyVisionConversion: NativeVideoSource.DolbyVisionConversionSnapshot?
    var isSwitchingVideoStream = false
    var videoStreamError: String?
    var sourceAudioIsAutomatic = true
    var attachedVideoSubtitle: PlaybackStream?
    var selectedEmbeddedSubtitleStreamIndex: Int?
    var hasExplicitEmbeddedSubtitleSelection = false
    var hasUnresolvedEmbeddedSubtitleSelection = false
    var videoQuality: PlaybackQuality = .automatic
    var subtitleDelaySeconds: Double = 0
    var videoSpeed: Float = 1
    var videoSeekAuthority = VideoSeekAuthority()
    var videoSeekTask: Task<Void, Never>?
    var videoRateLease = VideoTemporaryRateLease()
    var isSeekingChapter = false
    var playbackMarkers: [PlaybackMarker] = []
    var nextEpisode: MediaItem?
    var videoActionsError: String?
    var nextEpisodeAutoplay: Bool?
    var pendingVideoSource: NativeVideoSource?
    var pendingVideoPlayer: AVPlayer?
    var episodeTask: Task<Void, Never>?
    var actionsGeneration = UUID()
    var episodeAutoplay = EpisodeAutoplaySession()
    var automaticAdvanceRequestID: String?
    let episodeSettings = LuminaUserSettings()
    var episodePreferencesRequestID: String?
    var canUseVideoActions: Bool { !isCasting && didSeek && !closed && !isSwitchingVideoStream && !isSeekingChapter && !isReplaying }
    var canAdjustSubtitleTiming: Bool { request.usesClientDemux || attachedVideoSubtitle != nil }
    var subtitlesExplicitlyOff = false
    var isCommittingVideoStream = false
    var reportQueue: Task<Void, Never>?
    var pendingReports: [(event: PlaybackEvent, request: PlaybackRequest, seconds: Double, paused: Bool)] = []
    var didSeek = false
    var compatibilityRetryRequestID: String?
    var videoAdvancedRequestID: String?
    var started = false
    var autoplayWhenReady = true
    var isActive = true
    var closed = false
    var didReportCompletion = false
    var lastReport: Double = -1
    var remoteTargets: [(command: MPRemoteCommand, target: Any)] = []
    #if os(iOS)
    var nowPlayingArtwork: MPMediaItemArtwork?
    var nowPlayingArtworkItemID: String?
    var nowPlayingArtworkURL: URL?
    var nowPlayingArtworkGeneration = 0
    var nowPlayingArtworkTask: Task<Void, Never>?
    #endif

    /// The most recently requested video position while AVFoundation catches up.
    /// Audio keeps its independent transport authority.
    var gestureSeekTime: Double { videoSeekAuthority.optimisticTarget ?? elapsedSeconds }

    /// Valid spans for gesture projection, including live-window gaps when present.
    var gestureSeekRanges: [VideoSeekRange] {
        if isCasting, let range = VideoSeekRange(0, max(0, request.item.durationSeconds - 1.0 / 600)) { return [range] }
        return currentVideoSeekRanges()
    }
    var canSeekVideo: Bool { (canControlCast || canUseVideoActions) && !closed && !hasFinished }
    private var canControlCast: Bool {
        #if os(iOS)
        castOwner?.isCasting == true
        #else
        false
        #endif
    }
    var isVideoOutputUnavailable: Bool { isCasting && !canControlCast }

    /// The user's durable speed choice; a temporary hold never changes this value.
    var preferredVideoRate: Double { videoRateLease.preferredRate }
    var videoPlaybackRequested: Bool {
        #if os(iOS)
        if let castOwner, isCasting { return castOwner.isPlaying && !closed }
        #endif
        return autoplayWhenReady && !closed && !hasFinished
    }

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem], queueIndex: Int? = nil, initiallyPaused: Bool = false) {
        self.request = request
        self.store = store
        let resolvedQueue = request.item.isAudio && queue.isEmpty ? [request.item] : queue
        let resolvedOccurrences = resolvedQueue.map { _ in UUID() }
        self.queue = resolvedQueue
        self.occurrenceIDs = resolvedOccurrences
        self.queueRows = Self.makeQueueRows(items: resolvedQueue, occurrenceIDs: resolvedOccurrences)
        self.queueIndex = min(max(0, queueIndex ?? resolvedQueue.firstIndex(where: { $0.id == request.item.id }) ?? 0), max(0, resolvedQueue.count - 1))
        self.initiallyPaused = initiallyPaused
        autoplayWhenReady = !initiallyPaused
        videoQuality = request.resolvedQuality
        subtitlesExplicitlyOff = request.subtitleStreamIndex == -1
        let initialSubtitle = request.mediaStreams.first {
            $0.index == request.subtitleStreamIndex && $0.hasExternalSubtitle
        }
        attachedVideoSubtitle = initialSubtitle
        selectedEmbeddedSubtitleStreamIndex = initialSubtitle == nil ? request.subtitleStreamIndex : nil
        elapsedSeconds = request.startPositionSeconds
        player = request.item.isAudio || request.usesClientDemux || initialSubtitle != nil
            ? AVPlayer() : AVPlayer(url: request.url)
        if !request.item.isAudio {
            configureDeviceAudioLanguage(player, preferences: store.playbackPreferences)
        }
        player.preventsDisplaySleepDuringVideoPlayback = true
        if !request.item.isAudio {
            #if os(iOS)
            #if LUMINA_UI_TESTING
            if ProcessInfo.processInfo.arguments.contains("--ui-testing-force-video-system-pause") {
                player.audiovisualBackgroundPlaybackPolicy = .pauses
            } else {
                player.audiovisualBackgroundPlaybackPolicy = .automatic
            }
            #else
            player.audiovisualBackgroundPlaybackPolicy = .automatic
            #endif
            #else
            player.audiovisualBackgroundPlaybackPolicy = .pauses
            #endif
        }
        recordPlaybackDiagnostic(.modelCreated)
        #if LUMINA_UI_TESTING
        PlaybackLifecycleProbe.resetCloseMarker()
        print("LUMINA_LIFECYCLE playback-model init \(lifecycleToken)")
        #endif
    }

    deinit {
        for observer in playbackDiagnosticObservers { NotificationCenter.default.removeObserver(observer) }
        if let videoRateChangeObserver {
            NotificationCenter.default.removeObserver(videoRateChangeObserver)
        }
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE playback-model deinit \(lifecycleToken)")
        #endif
    }

    func installVideoRateChangeObserver() {
        guard !request.item.isAudio, videoRateChangeObserver == nil else { return }
        videoRateChangeObserver = NotificationCenter.default.addObserver(
            forName: AVPlayer.rateDidChangeNotification,
            object: player,
            queue: .main
        ) { [weak self] notification in
            let reason = (notification.userInfo?[AVPlayer.rateDidChangeReasonKey] as? AVPlayer.RateDidChangeReason)
                ?? (notification.userInfo?[AVPlayer.rateDidChangeReasonKey] as? String).map(AVPlayer.RateDidChangeReason.init(rawValue:))
            #if os(tvOS)
            MainActor.assumeIsolated {
                guard let self, reason == .setRateCalled, !self.isPausingNativePlayer,
                      self.didSeek, !self.closed, !self.isCommittingVideoStream else { return }
                if self.hasFinished {
                    if self.player.rate > 0, !self.isReplaying { Task { await self.replay() } }
                    return
                }
                // AVKit owns remote pause/resume and temporary scanning rates.
                self.autoplayWhenReady = self.player.rate != 0
                if self.nextEpisodeAutoplay != nil { self.nextEpisodeAutoplay = self.autoplayWhenReady }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.updatePosition()
                self.enqueue(.progress, paused: !self.autoplayWhenReady)
            }
            #else
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.handleVideoRateChange(reason)
            }
            #endif
        }
    }

    func removeVideoRateChangeObserver() {
        if let videoRateChangeObserver {
            NotificationCenter.default.removeObserver(videoRateChangeObserver)
            self.videoRateChangeObserver = nil
        }
    }

    private func handleVideoRateChange(_ reason: AVPlayer.RateDidChangeReason?) {
        recordPlaybackDiagnostic(.rateChanged, rateReason: reason)
        traceContinuity("rate-change=\(reason?.rawValue ?? "unknown")")
        #if os(iOS)
        #if LUMINA_UI_TESTING
        let reasonValue = reason?.rawValue ?? "unknown"
        videoRateChangeCount += 1
        lastVideoRateChangeReason = reasonValue
        if player.rate == 0 { lastVideoNativePauseReason = reasonValue }
        #endif
        guard player.rate == 0 else {
            pendingSystemBackgroundPauseItem = nil
            return
        }
        guard reason == .appBackgrounded,
              videoPlaybackRequested,
              let item = player.currentItem else {
            pendingSystemBackgroundPauseItem = nil
            return
        }
        pendingSystemBackgroundPauseItem = item
        recoverSystemBackgroundPauseIfNeeded()
        #endif
    }

    func recoverSystemBackgroundPauseIfNeeded() {
        #if os(iOS)
        guard !isCasting, !request.item.isAudio,
              let pausedItem = pendingSystemBackgroundPauseItem,
              UIApplication.shared.applicationState == .active,
              isActive,
              videoPlaybackRequested,
              !closed,
              !hasFinished,
              didSeek,
              !isSwitchingVideoStream,
              !isCommittingVideoStream,
              !isReplaying,
              !isSeekingChapter,
              videoSeekAuthority.optimisticTarget == nil,
              pendingVideoPlayer == nil,
              player.currentItem === pausedItem,
              player.timeControlStatus == .paused,
              pausedItem.status == .readyToPlay else { return }
        pendingSystemBackgroundPauseItem = nil
        playNativePlayer()
        #endif
    }

    #if os(iOS) && LUMINA_UI_TESTING
    func injectSystemBackgroundPauseForUITesting() {
        guard ProcessInfo.processInfo.arguments.contains("--ui-testing-force-video-system-pause"),
              !request.item.isAudio,
              videoPlaybackRequested else { return }
        pauseNativePlayer()
        isPlaying = false
        videoSystemPauseInjectionCount += 1
        Task { @MainActor [weak self] in
            await Task.yield()
            guard let self, self.player.rate == 0 else { return }
            NotificationCenter.default.post(
                name: AVPlayer.rateDidChangeNotification,
                object: self.player,
                userInfo: [AVPlayer.rateDidChangeReasonKey: AVPlayer.RateDidChangeReason.appBackgrounded]
            )
        }
    }
    #endif

    func awaitInitialPlayback(positionSeconds: Double) async throws {
        try await NativeVideoSource.waitUntilReady(player, seekingTo: positionSeconds)
        try Task.checkCancellation()
        guard !closed, let item = player.currentItem else { throw CancellationError() }
        if positionSeconds > 0 {
            traceContinuity("initial-seek")
            let sought = await seekNativePlayer(to: CMTime(seconds: positionSeconds, preferredTimescale: 600),
                                               toleranceBefore: .zero, toleranceAfter: .zero)
            try Task.checkCancellation()
            guard !closed, player.currentItem === item else { throw CancellationError() }
            guard sought else { throw item.error ?? POSIXError(.EIO) }
        }
        guard item.status == .readyToPlay else { throw item.error ?? POSIXError(.EIO) }
    }

    func handleVideoPlaybackFailure(_ error: Error?) async {
        guard didSeek, !closed, !hasFinished, !isCasting, !isCommittingVideoStream, !isSwitchingVideoStream else { return }
        let failedSource = videoSource
        let failedRequestID = request.id
        let error: Error? = if let error { await failedSource.playbackError(for: error) } else { error }
        let conversion = await failedSource.dolbyVisionConversion
        guard !closed, !hasFinished, !isCasting, !isCommittingVideoStream, !isSwitchingVideoStream,
              videoSource === failedSource, request.id == failedRequestID else { return }
        if conversion?.hasFailure == true {
            videoDolbyVisionConversion = conversion
            pauseNativePlayer()
        }
        if compatibilityRetryRequestID != request.id,
           let error, request.canRetryCompatibleVideo(after: error, hasAdvanced: videoAdvancedRequestID == request.id) {
            compatibilityRetryRequestID = request.id
            let recovered = await replaceVideoStream(
                audioIndex: request.audioStreamIndex, externalSubtitle: attachedVideoSubtitle,
                selectsExternalSubtitle: false, changesAudio: false,
                resumePosition: max(request.startPositionSeconds, elapsedSeconds), compatibilityError: error
            )
            if recovered || closed { return }
        }
        let conversionFailure = (error as? NativeVideoSource.SelectionError) == .invalidDolbyVisionConversion
        errorMessage = conversionFailure
            ? "Dolby Vision conversion failed. Compatible playback could not be started."
            : "Playback stopped. Check your connection and try again."
    }

    func retryInitialCompatibleVideo(after error: Error) async throws {
        let failed = request
        guard !closed, !didSeek, compatibilityRetryRequestID != failed.id,
              failed.canRetryCompatibleVideo(after: error) else { throw error }
        compatibilityRetryRequestID = failed.id
        var recoveryError = error
        var recoveryRequest = failed
        var retriedServerMediaRecovery = false
        var pendingSessions: [String: PlaybackRequest] = [:]
        var adoptedSessionID: String?
        defer {
            for candidate in pendingSessions.values where candidate.id != failed.id && candidate.id != adoptedSessionID {
                enqueue(.stop, paused: true, for: candidate, position: failed.startPositionSeconds)
            }
        }
        while true {
            let candidateSource = NativeVideoSource()
            var candidateRequest: PlaybackRequest?
            var candidateItem: AVPlayerItem?
            do {
                let next: PlaybackRequest
                if !retriedServerMediaRecovery,
                   let mediaRecovery = try await store.prepareServerMediaRecovery(
                    after: recoveryRequest, error: recoveryError
                   ) {
                    retriedServerMediaRecovery = true
                    next = mediaRecovery
                } else {
                    // Keep a server session alive while trying its media rendition,
                    // but release a failed candidate before opening another encode.
                    if let superseded = pendingSessions.removeValue(forKey: recoveryRequest.id),
                       superseded.id != failed.id {
                        enqueue(.stop, paused: true, for: superseded, position: failed.startPositionSeconds)
                    }
                    next = try await store.prepareCompatiblePlayback(after: recoveryRequest, error: recoveryError)
                }
                candidateRequest = next
                pendingSessions[next.id] = next
                try Task.checkCancellation()
                guard !closed, request.id == failed.id else { throw CancellationError() }
                let subtitle = next.mediaStreams.first { $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle }
                let item = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: subtitle)
                try Task.checkCancellation()
                guard !closed, request.id == failed.id else { throw CancellationError() }
                replaceNativeItem(item)
                candidateItem = item
                try await awaitInitialPlayback(positionSeconds: next.startPositionSeconds)
                try Task.checkCancellation()
                guard !closed, request.id == failed.id else { throw CancellationError() }
                let previousSource = videoSource
                if next.id != failed.id {
                    enqueue(.stop, paused: true, for: failed, position: failed.startPositionSeconds)
                }
                adoptedSessionID = next.id
                request = next
                if next.usesServerMediaRecovery { compatibilityRetryRequestID = nil }
                videoSource = candidateSource
                attachedVideoSubtitle = subtitle
                subtitlesExplicitlyOff = next.subtitleStreamIndex == -1
                selectedEmbeddedSubtitleStreamIndex = subtitle == nil ? next.subtitleStreamIndex : nil
                previousSource.stop()
                return
            } catch {
                let candidateError = await candidateSource.playbackError(for: error)
                candidateSource.stop()
                if let candidateItem, player.currentItem === candidateItem { replaceNativeItem(nil) }
                try Task.checkCancellation()
                guard !closed, request.id == failed.id else { throw CancellationError() }
                // These tiers advance only toward compatibility: original package,
                // server master, selected server media, then H.264/SDR.
                guard let candidateRequest,
                      candidateRequest.usesServerVideoRecovery || candidateRequest.usesServerMediaRecovery,
                      candidateRequest.canRetryCompatibleVideo(after: candidateError) else { throw error }
                recoveryRequest = candidateRequest
                recoveryError = candidateError
            }
        }
    }

}

#if LUMINA_UI_TESTING
private enum PlaybackLifecycleProbe {
    private static let markerName = "lumina-playback-close.txt"

    static func resetCloseMarker() {
        for directory in directories {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent(markerName))
        }
    }

    static func recordClose(state: String, player: AVPlayer) {
        let snapshot = "state=\(state) rate=\(player.rate) item-nil=\(player.currentItem == nil)"
        print("LUMINA_LIFECYCLE playback-close \(snapshot)")
        for directory in directories {
            do {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                let marker = directory.appendingPathComponent(markerName)
                guard state == "closed" || !FileManager.default.fileExists(atPath: marker.path) else { continue }
                try Data(snapshot.utf8).write(to: marker, options: .atomic)
            } catch {
                // The test runner reads the marker if this process can write its own sandbox.
            }
        }
    }

    private static var directories: [URL] {
        let manager = FileManager.default
        return [manager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first,
                manager.urls(for: .cachesDirectory, in: .userDomainMask).first].compactMap { $0 }
    }
}

#endif

@MainActor
func configureDeviceAudioLanguage(_ player: AVPlayer, preferences: NativePlaybackPreferences) {
    guard !preferences.preferredAudioLanguages.isEmpty else { return }
    let criteria = AVPlayerMediaSelectionCriteria(preferredLanguages: preferences.preferredAudioLanguages,
                                                 preferredMediaCharacteristics: nil)
    player.setMediaSelectionCriteria(criteria, forMediaCharacteristic: .audible)
}
