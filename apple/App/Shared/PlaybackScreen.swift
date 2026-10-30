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

extension QueuePresentationRow where Item == MediaItem {
    var accessibilityID: String { "audioQueueItem-\(item.id)" + (duplicated ? "-\(id.uuidString)" : "") }
}

@MainActor
extension View {
    func nativeAudioPlayer(playback: Binding<PlaybackModel?>, store: LuminaStore,
                           onDismiss: @escaping () -> Void = {}) -> some View {
        modifier(NativeAudioPlayerPresentation(playback: playback, store: store, onDismiss: onDismiss))
    }

    #if !os(tvOS)
    func nativePlayback(request: Binding<PlaybackRequest?>, store: LuminaStore,
                        onDismiss: @escaping () -> Void) -> some View {
        modifier(NativePlaybackPresentation(request: request, store: store, onDismiss: onDismiss))
    }
    #endif
}

private struct NativeAudioPlayerPresentation: ViewModifier {
    @Binding var playback: PlaybackModel?
    let store: LuminaStore
    let onDismiss: () -> Void

    private var presented: Binding<Bool> {
        Binding(get: { playback != nil }, set: { if !$0 { playback = nil } })
    }

    func body(content: Content) -> some View {
        #if os(tvOS)
        content.fullScreenCover(isPresented: presented, onDismiss: onDismiss) {
            if let playback { PlaybackScreen(playback: playback, store: store) }
        }
        #else
        content.navigationDestination(isPresented: presented) {
            if let playback { PlaybackScreen(playback: playback, store: store) }
        }
        .onChange(of: playback == nil) { _, dismissed in if dismissed { onDismiss() } }
        #endif
    }
}

#if !os(tvOS)
#if LUMINA_UI_TESTING
private let rebuildVideoPresentation = Notification.Name("Lumina.RebuildVideoPresentation")
#endif

private struct NativePlaybackPresentation: ViewModifier {
    @Binding var request: PlaybackRequest?
    let store: LuminaStore
    let onDismiss: () -> Void
    @Environment(NativeVideoSession.self) private var videoSession
    @Environment(NativeAudioSession.self) private var audioSession

    private var audioPresented: Binding<Bool> {
        Binding(get: { request?.item.isAudio == true }, set: { if !$0, request?.item.isAudio == true { request = nil } })
    }

    func body(content: Content) -> some View {
        content.navigationDestination(isPresented: audioPresented) {
            if let request, request.item.isAudio { PlaybackScreen(request: request, store: store) }
        }
        .onChange(of: audioPresented.wrappedValue) { _, presented in if !presented { onDismiss() } }
        .onChange(of: request?.id, initial: true) { _, _ in
            guard let request, !request.item.isAudio else { return }
            videoSession.present(request: request, store: store, audioSession: audioSession) {
                self.request = nil
                onDismiss()
            }
        }
    }
}

/// The session and its startup outlive adaptive navigation and cover content.
@MainActor @Observable
final class NativeVideoSession {
    struct Presentation: Identifiable {
        let id = UUID()
        let playback: PlaybackModel
    }
    private(set) var presentation: Presentation?
    let casting = NativeVideoCasting()
    @ObservationIgnored private var startup: Task<Void, Never>?
    @ObservationIgnored private var onDismiss: (() -> Void)?

    func present(request: PlaybackRequest, store: LuminaStore, audioSession: NativeAudioSession,
                 onDismiss: @escaping () -> Void) {
        guard presentation == nil else { return }
        audioSession.stop()
        let model = PlaybackModel(request: request, store: store, queue: [])
        casting.attach(model)
        model.setActive(UIApplication.shared.applicationState == .active)
        self.onDismiss = onDismiss
        presentation = Presentation(playback: model)
        startup = Task { await model.start() }
    }

    func dismiss(id: UUID? = nil) {
        guard let presentation, id == nil || id == presentation.id else { return }
        self.presentation = nil
        startup?.cancel()
        startup = nil
        presentation.playback.close()
        let callback = onDismiss
        onDismiss = nil
        callback?()
    }
}

struct NativeVideoPlayerPresentation: ViewModifier {
    let session: NativeVideoSession
    let store: LuminaStore
    #if LUMINA_UI_TESTING
    @State private var videoPresentationGeneration = 0
    #endif

    func body(content: Content) -> some View {
        let presentationID = session.presentation?.id
        return content.fullScreenCover(item: Binding(
            get: { session.presentation },
            set: { if $0 == nil, let presentationID { session.dismiss(id: presentationID) } }
        )) { presentation in
            PlaybackScreen(playback: presentation.playback, store: store,
                           onVideoDismiss: { session.dismiss(id: presentation.id) })
                #if LUMINA_UI_TESTING
                .id(videoPresentationGeneration)
                #endif
        }
        #if LUMINA_UI_TESTING
        .onReceive(NotificationCenter.default.publisher(for: rebuildVideoPresentation)) { _ in
            videoPresentationGeneration += 1
        }
        #endif
    }
}
#endif

#if os(iOS)
private struct AudioArtworkSwipe: UIGestureRecognizerRepresentable {
    let onSkip: (Int) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator { Coordinator() }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let recognizer = UIPanGestureRecognizer()
        recognizer.maximumNumberOfTouches = 1
        recognizer.delegate = context.coordinator
        return recognizer
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        guard recognizer.state == .ended else { return }
        let distance = recognizer.translation(in: recognizer.view).x
        if abs(distance) >= 40 { onSkip(distance < 0 ? 1 : -1) }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let pan = gestureRecognizer as? UIPanGestureRecognizer else { return false }
            let velocity = pan.velocity(in: pan.view)
            return abs(velocity.x) > abs(velocity.y) * 1.5
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer) -> Bool {
            // Horizontal artwork swipes own track navigation; vertical drags remain scrolling.
            otherGestureRecognizer is UIPanGestureRecognizer
        }
    }
}
#endif

/// AVPlayer renders video while Lumina provides transport and track controls; the listening stage shares its audio model with the app shell.
struct PlaybackScreen: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var requestedPlayback: PlaybackModel
    @State private var startedPresentation = false
    @State private var attemptedAudioAdoption = false
    @State private var presentationError: String?
    @State private var queuePresented = false
    @State private var audioOptionsFocusRequest = 0
    @State private var isMuted = false
    @State private var videoChromeVisible = true
    @State private var pictureInPicturePossible = false
    @State private var pictureInPictureActive = false
    @State private var pictureInPictureContinuing = false
    @State private var pictureInPictureStartRequest = 0
    @State private var videoSettingsVisible = false
    @State private var restoreSettingsFocus = false
    @State private var videoSettingsTab: VideoSettingsTab = .info
    @State private var videoSettingsPresented = false
    @State private var videoGravity: AVLayerVideoGravity = .resizeAspect
    @State private var videoTracks = VideoMediaTracks()
    @AppStorage("native.video.subtitleAppearance") private var subtitleStyleData = ""
    @State private var videoChromeHideTask: Task<Void, Never>?
    @State private var videoTracksLoadTask: Task<Void, Never>?
    @FocusState private var videoControlsSurfaceFocused: Bool
    @FocusState private var focusedVideoControl: String?
    #if os(tvOS)
    private enum TVAudioFocus: Hashable {
        case artwork
        case queue
        case playPause
        case repeatMode
        case stop
        case timeline
    }
    @FocusState private var focusedAudioControl: TVAudioFocus?
    #endif
    @State private var videoIsScrubbing = false
    #if os(iOS)
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    @State private var videoGestures = VideoGesturePresentation()
    @State private var videoGestureCancellationID = UUID()
    @State private var videoGestureContainerSize = CGSize.zero
    @State private var videoGestureItemID: ObjectIdentifier?
    @State private var videoSettingsDetent: PresentationDetent = .medium
    private enum AudioCompanion { case lyrics, queue }
    @State private var audioCompanion = AudioCompanion.lyrics
    @State private var audioOptionsPresented = false
    @State private var audioStageSize = CGSize.zero
    @State private var audioTimelineVisible = true
    @State private var audioIsScrubbing = false
    @State private var audioTimelineHideTask: Task<Void, Never>?
    #if LUMINA_UI_TESTING
    @State private var uiTestingDidBecomeActiveOutcome = "none"
    #endif
    #endif
    private let store: LuminaStore
    private let onVideoDismiss: (() -> Void)?

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem] = [], queueIndex: Int? = nil) {
        self.store = store
        onVideoDismiss = nil
        _requestedPlayback = State(initialValue: PlaybackModel(request: request, store: store, queue: queue, queueIndex: queueIndex))
    }

    init(playback: PlaybackModel, store: LuminaStore, onVideoDismiss: (() -> Void)? = nil) {
        self.store = store
        self.onVideoDismiss = onVideoDismiss
        _requestedPlayback = State(initialValue: playback)
    }

    private var playback: PlaybackModel {
        if requestedPlayback.request.item.isAudio, attemptedAudioAdoption {
            return audioSession.playback ?? requestedPlayback
        }
        return requestedPlayback
    }
    private var displayedError: String? { presentationError ?? playback.errorMessage }

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
                        #endif
                }
            } else {
                GeometryReader { container in
                    videoStage(safeArea: container.safeAreaInsets).ignoresSafeArea()
                }
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
        .ignoresSafeArea(.container, edges: requestedPlayback.request.item.isAudio ? .top : [])
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if requestedPlayback.request.item.isAudio {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    if !usesExpandedAudioLayout { audioHeaderActions }
                }
            }
        }
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarVisibility(requestedPlayback.request.item.isAudio && !usesExpandedAudioLayout ? .visible : .hidden, for: .navigationBar)
        .toolbarVisibility(.hidden, for: .tabBar)
        .statusBarHidden(!requestedPlayback.request.item.isAudio && !videoSystemOverlaysVisible)
        .persistentSystemOverlays(requestedPlayback.request.item.isAudio || videoSystemOverlaysVisible ? .visible : .hidden)
        #endif
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
        #if os(iOS)
        .standardPhonePushedChrome(isEnabled: requestedPlayback.request.item.isAudio && !usesExpandedAudioLayout,
                                   foreground: .white, background: .black) {
            audioHeaderActions
        }
        #endif
        .preferredColorScheme(.dark)
        .sheet(isPresented: $queuePresented) { audioQueue }
        #if !os(tvOS)
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
        .onChange(of: scenePhase) { _, phase in
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
        #if os(tvOS)
        .onPlayPauseCommand {
            guard !playback.request.item.isAudio else { playback.togglePlayback(); return }
            if playback.hasFinished, !playback.isSwitchingVideoStream { replay() } else { playback.togglePlayback() }
            revealVideoChrome()
        }
        .onMoveCommand { direction in
            guard !playback.request.item.isAudio, !videoSettingsVisible else { return }
            switch direction {
            case .down where !videoChromeVisible:
                presentVideoSettings()
            case .left where !videoChromeVisible:
                playback.seek(to: playback.elapsedSeconds - 10)
                revealVideoChrome()
            case .right where !videoChromeVisible:
                playback.seek(to: playback.elapsedSeconds + 10)
                revealVideoChrome()
            default:
                break
            }
        }
        .onExitCommand { handleExitCommand() }
        #endif
    }

    #if os(tvOS)
    private var audioStage: some View {
        GeometryReader { geometry in
            ZStack(alignment: .trailing) {
                ScrollView {
                    VStack(spacing: 0) {
                        ZStack(alignment: .bottom) {
                            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 2000)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Color.black.overlay {
                                    LuminaIcon(glyph: .music, variant: .solid, size: 40)
                                }
                            }
                            .frame(width: geometry.size.width, height: geometry.size.height)
                            .clipped()
                            .accessibilityHidden(true)

                            Button { playback.togglePlayback() } label: {
                                Color.clear
                                    .frame(width: geometry.size.width, height: geometry.size.height)
                                    .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            .focusEffectDisabled()
                            .focused($focusedAudioControl, equals: .artwork)
                            .defaultFocus($focusedAudioControl, .artwork)
                            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
                            .accessibilityValue(playback.request.item.name)
                            .accessibilityIdentifier("audioAlbumArtwork")
                            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
                            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
                            .onMoveCommand { direction in
                                switch direction {
                                case .left:
                                    skipAudioTrack(offset: -1)
                                case .right:
                                    skipAudioTrack(offset: 1)
                                case .down:
                                    focusedAudioControl = playback.queue.isEmpty ? .playPause : .queue
                                default:
                                    break
                                }
                            }

                            VStack(alignment: .leading, spacing: 12) {
                                audioTimeline
                            }
                            .padding(.horizontal, audioHorizontalInset)
                            .padding(.bottom, 60)
                            .background {
                                LinearGradient(colors: [.clear, .black.opacity(0.88)], startPoint: .top, endPoint: .bottom)
                                    .frame(height: 220)
                                    .frame(maxHeight: .infinity, alignment: .bottom)
                            }
                        }
                        .frame(width: geometry.size.width, height: geometry.size.height)

                        VStack(alignment: .leading, spacing: 32) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(playback.request.item.name)
                                    .font(.system(size: 42, weight: .semibold))
                                    .fixedSize(horizontal: false, vertical: true)
                                    .accessibilityIdentifier("nowPlayingTitle")
                                Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                    .font(.system(size: 25))
                                    .foregroundStyle(.white.opacity(0.9))
                                if let album = playback.request.item.album {
                                    Text(album)
                                        .font(.system(size: 23))
                                        .foregroundStyle(.white.opacity(0.65))
                                }
                            }
                            Text(playback.request.methodLabel)
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.6))
                                .accessibilityIdentifier("audioPlaybackMethod")
                            NativeAudioOptions(session: audioSession, playback: playback,
                                               focusLyricsRequest: audioOptionsFocusRequest)
                        }
                        .frame(maxWidth: 1120, alignment: .leading)
                        .padding(.horizontal, audioHorizontalInset)
                        .padding(.vertical, 60)
                        .frame(maxWidth: .infinity, alignment: .top)
                        .background(LuminaTheme.theater)
                        .focusSection()
                    }
                }
                .scrollIndicators(.hidden)
                .scrollClipDisabled()
                tvAudioControlBand
                    .padding(.trailing, audioHorizontalInset)
            }
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
        .accessibilityIdentifier("audioPlayerStage")
        .onAppear { focusedAudioControl = .artwork }
        .onChange(of: queuePresented) { _, presented in
            if !presented { focusedAudioControl = .queue }
        }
    }

    #else
    private var audioStage: some View {
        GeometryReader { geometry in
            if expandedAudioFits(geometry.size) {
                expandedAudioStage(in: geometry)
            } else {
                compactAudioStage
            }
        }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { audioStageSize = $0 }
    }

    private func expandedAudioFits(_ size: CGSize) -> Bool {
        size.width >= 640 && size.height >= 560 && !dynamicTypeSize.isAccessibilitySize
    }

    private var usesExpandedAudioLayout: Bool { expandedAudioFits(audioStageSize) }

    private func expandedAudioStage(in geometry: GeometryProxy) -> some View {
        let gutter: CGFloat = geometry.size.width >= 1_100 ? 56 : 32
        let gap: CGFloat = geometry.size.width >= 1_100 ? 64 : 32
        let top = max(56, geometry.safeAreaInsets.top + 24)
        let height = max(1, geometry.size.height - top - 24)
        let columnWidth = min(520, (geometry.size.width - gutter * 2 - gap) * 0.48)
        let hasOutputStatus = audioSession.isSonos || audioSession.pendingSpeakerStop != nil || audioSession.casting.isRemoteOutput || audioSession.casting.canReturnToDevice
        // Align artwork with the prominent lyrics' first line, keeping room for controls below.
        let primaryTopInset: CGFloat = 48
        let artworkSide = max(180, min(columnWidth, height - primaryTopInset - (hasOutputStatus ? 288 : 224)))
        return HStack(alignment: .top, spacing: gap) {
            VStack(spacing: 12) {
                expandedAudioArtwork(side: artworkSide)
                audioTimeline
                    .overlay(alignment: .bottom) {
                        Text(playback.request.methodLabel)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.8))
                            .accessibilityIdentifier("audioPlaybackMethod")
                            .allowsHitTesting(false)
                    }
                expandedAudioTransport
                NativeMusicCastStatus(session: audioSession)
                    .font(.caption)
                if audioSession.isSonos || audioSession.pendingSpeakerStop != nil {
                    Button("Return to this device") { Task { await audioSession.returnHere() } }
                        .frame(minHeight: 44)
                        .disabled(audioSession.busy)
                        .accessibilityIdentifier("audioReturnHereButton")
                }
                if audioSession.pendingSpeakerStop != nil {
                    Button("Retry stopping remote output") { Task { await audioSession.retryStoppingSpeaker() } }
                        .frame(minHeight: 44)
                        .disabled(audioSession.busy)
                        .accessibilityIdentifier("audioRetryStoppingRemoteOutputButton")
                }
                if let error = displayedError ?? playback.syncError {
                    Text(error).font(.callout).foregroundStyle(.white)
                }
                Spacer(minLength: 0)
                expandedAudioRoutes
            }
            .frame(width: artworkSide)
            .frame(width: columnWidth)
            .frame(maxHeight: .infinity, alignment: .top)
            .padding(.top, primaryTopInset)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("audioPlayerPrimaryPane")

            VStack(alignment: .leading, spacing: 16) {
                if audioCompanion == .lyrics {
                    NativeAudioLyrics(session: audioSession, playback: playback, prominent: true)
                } else {
                    HStack {
                        Text("Play queue").font(.title2.bold())
                        Spacer()
                        Button { audioCompanion = .lyrics } label: {
                            Text("Done").frame(minWidth: 44, minHeight: 44).contentShape(.rect)
                        }
                        .accessibilityIdentifier("audioQueueDoneButton")
                    }
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) { audioQueueRows }
                            .padding(.vertical, 16)
                    }
                    .accessibilityIdentifier("audioQueueScreen")
                }
                HStack(spacing: 24) {
                    Spacer(minLength: 0)
                    Button { audioCompanion = .lyrics } label: {
                        Label("Lyrics", glyph: .subtitles).frame(minHeight: 44).contentShape(.rect)
                    }
                    .font(.callout.weight(audioCompanion == .lyrics ? .bold : .regular))
                    .accessibilityIdentifier("audioLyrics")
                    .accessibilityAddTraits(audioCompanion == .lyrics ? .isSelected : [])
                    .foregroundStyle(audioCompanion == .lyrics ? .white : .white.opacity(0.65))
                    Button { audioCompanion = .queue } label: {
                        Label("Queue", glyph: .queue).frame(minHeight: 44).contentShape(.rect)
                    }
                    .font(.callout.weight(audioCompanion == .queue ? .bold : .regular))
                    .accessibilityIdentifier("audioQueueButton")
                    .accessibilityAddTraits(audioCompanion == .queue ? .isSelected : [])
                    .foregroundStyle(audioCompanion == .queue ? .white : .white.opacity(0.65))
                }
                .font(.callout.weight(.semibold))
                .frame(minHeight: 44)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("audioPlayerCompanionPane")
        }
        .padding(.horizontal, gutter)
        .padding(.top, top)
        .padding(.bottom, 24)
        .frame(width: geometry.size.width, height: geometry.size.height)
        .background {
            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 400)) { image in
                image.resizable().scaledToFill().blur(radius: 70)
            } placeholder: { LuminaTheme.theater }
                .overlay(.black.opacity(0.68))
                .ignoresSafeArea()
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .overlay(alignment: .topLeading) {
            Button { finish() } label: {
                LuminaIcon(glyph: .chevronDown, size: 24)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
                .accessibilityLabel("Close player")
                .padding(.leading, gutter - 10)
                .padding(.top, max(32, geometry.safeAreaInsets.top + 8))
                .accessibilityIdentifier("closePlayerButton")
        }
        .foregroundStyle(.white)
        .buttonStyle(.plain)
        .accessibilityAction(.escape) { finish() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioPlayerStage")
        .sheet(isPresented: $audioOptionsPresented) {
            NavigationStack {
                ScrollView {
                    NativeAudioOptions(session: audioSession, playback: playback, showsLyrics: false)
                        .padding(24)
                }
                .background(LuminaTheme.theater)
                .navigationTitle("Listening options")
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { audioOptionsPresented = false }
                    }
                }
            }
            .preferredColorScheme(.dark)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("audioOptionsSheet")
        }
    }

    private func expandedAudioArtwork(side: CGFloat) -> some View {
        ZStack(alignment: .bottom) {
            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 1_600)) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                LuminaTheme.theater.overlay { LuminaIcon(glyph: .music, size: 64) }
            }
            .frame(width: side, height: side)
            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
            .accessibilityValue(playback.request.item.name)
            .accessibilityIdentifier("audioAlbumArtwork")
            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
            .gesture(AudioArtworkSwipe(onSkip: skipAudioTrack))

            HStack(alignment: .bottom, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(playback.request.item.name)
                        .font(.title3.weight(.semibold)).lineLimit(2)
                        .accessibilityIdentifier("nowPlayingTitle")
                    Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                        .font(.body).foregroundStyle(.white.opacity(0.8)).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(16)
            .padding(.top, 32)
            .background {
                LinearGradient(colors: [.clear, .black.opacity(0.85)], startPoint: .top, endPoint: .bottom)
            }
        }
        .frame(width: side, height: side)
        .clipShape(.rect(cornerRadius: 12))
    }

    private var expandedAudioTransport: some View {
        HStack(spacing: 2) {
            Button { playback.cycleRepeat() } label: {
                LuminaIcon(glyph: playback.repeatMode == .one ? .repeatOne : .repeat)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
                .accessibilityLabel(playback.repeatMode.label)
                .accessibilityValue(playback.repeatMode.label)
                .accessibilityIdentifier("audioRepeatButton")
                .foregroundStyle(playback.repeatMode == .off ? .white.opacity(0.65) : LuminaTheme.brand)
            Spacer(minLength: 0)
            Button { Task { await audioSession.step(-1) } } label: {
                LuminaIcon(glyph: .previous, variant: .solid, size: 28)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Previous track")
            .disabled((!playback.hasPrevious && !audioSession.isSonos) || audioSession.busy)
            .accessibilityIdentifier("audioPreviousButton")
            Button {
                if playback.hasFinished { replay() } else { playback.togglePlayback() }
            } label: {
                LuminaIcon(glyph: playback.hasFinished || playback.errorMessage != nil ? .sync : playback.isPlaying ? .pause : .play,
                           variant: .solid, size: 42)
                    .frame(width: 64, height: 64).contentShape(.rect)
            }
            .accessibilityLabel(playback.hasFinished ? "Replay" : playback.errorMessage != nil ? "Retry" : playback.isPlaying ? "Pause" : "Play")
            .accessibilityIdentifier("audioPlayPauseButton")
            .disabled(playback.isVideoOutputUnavailable || playback.isReplaying)
            Button { Task { await audioSession.step(1) } } label: {
                LuminaIcon(glyph: .next, variant: .solid, size: 28)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Next track")
            .disabled((!playback.hasNext && !audioSession.isSonos) || audioSession.busy)
            .accessibilityIdentifier("audioNextButton")
            Spacer(minLength: 0)
            Menu {
                Button("Listening options") { audioOptionsPresented = true }
                Button("Stop", glyph: .stop) { stopAudio() }
                    .accessibilityIdentifier("audioStopButton")
            } label: {
                Label("More playback actions", glyph: .moreHorizontal)
                    .labelStyle(.iconOnly).frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityIdentifier("audioOptionsButton")
        }
        .frame(height: 64)
    }

    private var expandedAudioRoutes: some View {
        HStack(spacing: 12) {
            Spacer(minLength: 0)
            NativeMusicAirPlayButton(session: audioSession)
            NativeMusicCastButton(session: audioSession)
        }
        .frame(height: 44)
    }

    private var compactAudioStage: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 0) {
                    ZStack(alignment: .bottom) {
                        GeometryReader { artwork in
                            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 2000)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Color.black.overlay {
                                    LuminaIcon(glyph: .music, variant: .solid, size: 40)
                                }
                            }
                            .frame(width: artwork.size.width, height: artwork.size.height)
                            .clipped()
                            .contentShape(.rect)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
                            .accessibilityIdentifier("audioAlbumArtwork")
                            .accessibilityValue(playback.request.item.name)
                            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
                            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
                            .gesture(AudioArtworkSwipe(onSkip: skipAudioTrack))
                            .simultaneousGesture(TapGesture().onEnded { revealAudioTimeline() })
                            .accessibilityAction(named: "Show track progress") { revealAudioTimeline() }
                        }
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        VStack(alignment: .leading, spacing: 12) {
                            if let error = displayedError {
                                Label(error, glyph: .warning)
                                    .font(.callout)
                            }
                            if let syncError = playback.syncError { Text(syncError).font(.caption) }
                            audioTimeline
                        }
                        .padding(.horizontal, 24)
                        .padding(.top, 64)
                        .padding(.bottom, 20 + geometry.safeAreaInsets.bottom)
                        .padding(.leading, -geometry.safeAreaInsets.leading)
                        .padding(.trailing, -geometry.safeAreaInsets.trailing)
                        .background {
                            LinearGradient(colors: [.clear, .black.opacity(0.88)], startPoint: .top, endPoint: .bottom)
                                .ignoresSafeArea(.container, edges: .horizontal)
                        }
                        .opacity(showsAudioTimeline ? 1 : 0)
                        .allowsHitTesting(showsAudioTimeline)
                        .accessibilityHidden(!showsAudioTimeline)
                    }
                    .frame(height: geometry.size.height + geometry.safeAreaInsets.bottom)
                    VStack(alignment: .leading, spacing: 24) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(playback.request.item.name)
                                .font(LuminaTheme.editorial(30, relativeTo: .title))
                                .accessibilityIdentifier("nowPlayingTitle")
                            Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                .font(.title3)
                                .foregroundStyle(.secondary)
                            if let album = playback.request.item.album {
                                Text(album).foregroundStyle(.secondary)
                            }
                        }
                        Text(playback.request.methodLabel).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("audioPlaybackMethod")
                        NativeAudioOptions(session: audioSession, playback: playback)
                    }
                    .frame(maxWidth: 1120, alignment: .leading)
                    .padding(24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(LuminaTheme.theater.ignoresSafeArea(.container, edges: .horizontal))
                }
            }
            .scrollClipDisabled()
        }
        .foregroundStyle(.white)
        .accessibilityIdentifier("audioPlayerStage")
        .task(id: playback.request.id) { revealAudioTimeline() }
        .onDisappear { audioTimelineHideTask?.cancel() }
    }

    private var showsAudioTimeline: Bool {
        audioTimelineVisible || displayedError != nil || playback.syncError != nil
    }

    private func revealAudioTimeline() {
        audioTimelineHideTask?.cancel()
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { audioTimelineVisible = true }
        guard !audioIsScrubbing, !UIAccessibility.isVoiceOverRunning else { return }
        audioTimelineHideTask = Task {
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) { audioTimelineVisible = false }
        }
    }

    private func finishAudioScrubbing() {
        audioIsScrubbing = false
        revealAudioTimeline()
    }

    #endif

    #if os(iOS)
    private var videoGestureSettingsFingerprint: [Int] {
        let preferences = store.playbackPreferences
        return [preferences.videoGesturesEnabled, preferences.volumeSwipeEnabled,
                preferences.brightnessSwipeEnabled, preferences.horizontalSeekEnabled,
                preferences.doubleTapSkipEnabled, preferences.twoFingerTapEnabled,
                preferences.holdSpeedEnabled, preferences.gestureHapticsEnabled].map { $0 ? 1 : 0 }
            + [preferences.skipForwardSeconds, preferences.skipBackwardSeconds]
    }

    private func cancelVideoGestures(releaseBrightness: Bool) {
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

    private func videoStage(safeArea: EdgeInsets) -> some View {
        GeometryReader { geometry in
            let division = tabletopDivision(in: geometry)
            #if os(tvOS)
            let showsTapSurface = !videoChromeVisible
            #else
            let showsTapSurface = true
            #endif
            ZStack(alignment: .topLeading) {
                nativeVideoPlayer
                    .frame(maxWidth: .infinity)
                    .frame(height: division?.minY ?? geometry.size.height)
                    .frame(maxHeight: .infinity, alignment: .top)
                #if os(iOS)
                videoTouchSurface(safeArea: safeArea)
                    .frame(maxWidth: .infinity)
                    .frame(height: division?.minY ?? geometry.size.height)
                #endif
                if let division {
                    videoTransport
                        .padding(.top, division.maxY)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                } else {
                    #if os(tvOS)
                    if showsTapSurface, !videoSettingsVisible, displayedError == nil, !playback.hasFinished {
                        Button {
                            #if os(tvOS)
                            revealVideoChrome()
                            #else
                            toggleVideoChrome()
                            #endif
                        } label: {
                            Color.clear.contentShape(.rect)
                        }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .contentShape(.rect)
                            .accessibilityLabel(videoChromeVisible ? "Hide playback controls" : "Show playback controls")
                            .accessibilityIdentifier("videoControlsSurface")
                            #if os(tvOS)
                            // The reveal surface owns focus after the controls dismiss. A system button
                            // style can paint its focus treatment over the entire video, so keep this
                            // transparent and reserve visible focus for the actual transport controls.
                            .buttonStyle(VideoControlsRevealButtonStyle())
                            .focusEffectDisabled()
                            .focused($videoControlsSurfaceFocused)
                            #else
                            .buttonStyle(.plain)
                            #endif
                    }
                    #endif
                    if !videoSettingsVisible && (videoChromeVisible || displayedError != nil || playback.hasFinished) {
                        videoOverlay(safeArea: safeArea)
                            #if os(iOS)
                            .padding(.top, safeArea.top)
                            .padding(.bottom, safeArea.bottom)
                            #endif
                            .background { videoScrim.allowsHitTesting(false) }
                            .transition(.opacity)
                    }
                    if !videoSettingsVisible, !videoSettingsPresented, displayedError == nil {
                        videoContextActions(showNext: showsContextualNextEpisode)
                            .padding(.horizontal, videoHorizontalInset)
                            #if os(tvOS)
                            .padding(.bottom, 260)
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                            .onMoveCommand { direction in
                                if direction == .down { focusedVideoControl = "videoPlayPauseButton" }
                                if direction == .up { focusedVideoControl = "videoSettingsButton" }
                            }
                            #else
                            .padding(.top, safeArea.top + videoVerticalInset + 56)
                            .padding(.trailing, max(0, safeArea.trailing - videoHorizontalInset))
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                            #endif
                    }
                    #if os(tvOS)
                    if videoSettingsVisible {
                        videoSettingsPanel
                            .frame(maxWidth: 1_560,
                                   maxHeight: min(760, max(1, geometry.size.height - max(48, geometry.safeAreaInsets.top + 24) - 32)))
                            .padding(.top, max(48, geometry.safeAreaInsets.top + 24))
                            .padding(.horizontal, max(72, geometry.safeAreaInsets.leading + 36))
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    }
                    #endif
                }
                #if !os(tvOS)
                if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
                    playbackTimeProbe
                }
                #endif
                #if os(iOS)
                    VideoGestureFeedbackOverlay(presentation: videoGestures, safeArea: safeArea, reduceMotion: reduceMotion)
                #endif
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            #if os(iOS)
            .onChange(of: geometry.size, initial: true) { _, size in
                if videoGestureContainerSize != .zero { cancelVideoGestures(releaseBrightness: false) }
                playback.traceContinuity("geometry=\(size)")
                videoGestureContainerSize = size
            }
            #endif
        }
    }

    private func tabletopDivision(in geometry: GeometryProxy) -> CGRect? {
        #if os(iOS)
        guard #available(iOS 27.1, *) else { return nil }
        return geometry.reservedRegions(kind: .division, layoutDirectionBehavior: .fixed)
            .map { region in
                CGRect(x: region.frame.minX - region.margins.leading,
                       y: region.frame.minY - region.margins.top,
                       width: region.frame.width + region.margins.leading + region.margins.trailing,
                       height: region.frame.height + region.margins.top + region.margins.bottom)
            }
            .first { $0.width > $0.height }
        #else
        return nil
        #endif
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
                    #if os(iOS)
                    videoPictureInPictureButton
                    VideoRoutePicker()
                        .frame(width: 44, height: 44)
                    #endif
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
                    #if os(tvOS)
                    ProgressView(value: min(playback.elapsedSeconds, max(1, playback.request.item.durationSeconds)), total: max(1, playback.request.item.durationSeconds))
                        .tint(.white)
                        .accessibilityLabel("Video position")
                    #else
                    videoTimeline
                    #endif
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

    private var subtitleStyleBinding: Binding<VideoSubtitleStyle> {
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

    private var videoSettingsPanel: some View {
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

    private func presentVideoSettings() {
        videoSettingsTab = .info
        #if os(tvOS)
        videoSettingsVisible = true
        revealVideoChrome()
        #else
        videoSettingsDetent = .medium
        videoSettingsPresented = true
        #endif
    }

    private func dismissVideoSettings() {
        restoreSettingsFocus = true
        videoSettingsVisible = false
        revealVideoChrome()
    }

    private func toggleVideoChrome() {
        guard !videoSettingsVisible, !videoSettingsPresented, displayedError == nil, !playback.hasFinished else { return }
        if videoChromeVisible { hideVideoChrome() }
        else { revealVideoChrome() }
    }

    private var videoSystemOverlaysVisible: Bool {
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

    private func revealVideoChrome() {
        videoChromeHideTask?.cancel()
        videoChromeVisible = true
        if videoControlsSurfaceFocused { videoControlsSurfaceFocused = false }
        #if os(tvOS)
        if !videoSettingsVisible, focusedVideoControl == nil {
            focusedVideoControl = "videoPlayPauseButton"
        }
        #endif
        guard playback.isPlaying, !playback.hasFinished, !videoSettingsVisible, !videoSettingsPresented,
              !videoIsScrubbing, displayedError == nil else { return }
        #if os(iOS)
        guard !voiceOverEnabled, !videoGestures.interacting else { return }
        #endif
        videoChromeHideTask = Task {
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            hideVideoChrome()
        }
    }

    private func handleExitCommand() {
        if queuePresented {
            queuePresented = false
        } else if !playback.request.item.isAudio, videoSettingsVisible {
            dismissVideoSettings()
        } else if !playback.request.item.isAudio, displayedError != nil || playback.hasFinished {
            finish()
        } else if !playback.request.item.isAudio, videoChromeVisible {
            hideVideoChrome()
        } else {
            finish()
        }
    }

    private func loadVideoTracks(for item: AVPlayerItem?) async {
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

    private func selectAudio(_ index: Int) {
        guard let group = videoTracks.audioGroup, videoTracks.audioOptions.indices.contains(index),
              let item = playback.player.currentItem, videoTracks.item === item else { return }
        item.select(videoTracks.audioOptions[index], in: group)
        videoTracks.selectAudio(index)
    }

    private func selectAudioAutomatically() {
        guard let group = videoTracks.audioGroup, let item = playback.player.currentItem, videoTracks.item === item else { return }
        item.selectMediaOptionAutomatically(in: group)
        videoTracks.markAudioAutomatic()
    }

    private func selectSubtitle(_ selection: VideoSubtitleSelection) {
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

    @ViewBuilder private func videoOverlay(safeArea: EdgeInsets) -> some View {
        #if os(iOS)
        videoOverlayIOS(safeArea: safeArea)
        #else
        VStack(spacing: 24) {
            HStack(spacing: 12) {
                Spacer(minLength: 0)
                #if os(iOS)
                videoPictureInPictureButton
                VideoRoutePicker().frame(width: 44, height: 44)
                #endif
                Button("Playback settings", glyph: .subtitles) { presentVideoSettings() }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSettingsButton")
                    .focused($focusedVideoControl, equals: "videoSettingsButton")
                Button(isMuted ? "Unmute" : "Mute", glyph: isMuted ? .volumeHigh : .volumeOff, variant: .solid) {
                    isMuted.toggle()
                    playback.player.isMuted = isMuted
                }
                .labelStyle(.iconOnly)
                .accessibilityIdentifier("videoMuteButton")
                    .focused($focusedVideoControl, equals: "videoMuteButton")
            }
            .buttonStyle(LuminaQuietActionStyle())
            .controlSize(.large)
            #if os(iOS)
            .padding(.leading, safeArea.leading)
            .padding(.trailing, safeArea.trailing)
            #endif
            #if os(tvOS)
            .focusSection()
            .onMoveCommand { direction in
                if direction == .down {
                    focusedVideoControl = preferredContextualControl ?? "videoPlayPauseButton"
                }
            }
            #endif
            Spacer(minLength: 0)
            VStack(spacing: 12) {
                Text(playback.request.item.name)
                    .font(.headline)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                #if os(tvOS)
                ProgressView(value: min(playback.elapsedSeconds, max(1, playback.request.item.durationSeconds)), total: max(1, playback.request.item.durationSeconds))
                    .tint(.white)
                #else
                videoTimeline
                #endif
                HStack {
                    Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                    Spacer()
                    Text("−" + Duration.seconds(max(0, playback.request.item.durationSeconds - playback.elapsedSeconds)).formatted(.time(pattern: .minuteSecond)))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.white.opacity(0.72))
                HStack(spacing: videoTransportSpacing) {
                    #if os(iOS)
                    Color.clear
                        .frame(width: videoTransportSideReserve, height: 44)
                        .accessibilityHidden(true)
                    #endif
                    Button("Back \(videoSkipBackwardSeconds) seconds", glyph: videoSkipBackwardSeconds == 10 ? .seekBack10 : .rewind, size: 28) { skipVideo(forward: false); revealVideoChrome() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSeekBackwardButton")
                    .focused($focusedVideoControl, equals: "videoSeekBackwardButton")
                    #if os(iOS)
                    .frame(maxWidth: .infinity)
                    #endif
                    Button {
                        if playback.hasFinished { replay() } else { playback.togglePlayback() }
                        revealVideoChrome()
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
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.circle)
                    .tint(.white)
                    .foregroundStyle(.black)
                    .accessibilityIdentifier(playback.hasFinished ? "replayButton" : "videoPlayPauseButton")
                    .focused($focusedVideoControl, equals: "videoPlayPauseButton")
                    .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
                    #if os(iOS)
                    .frame(maxWidth: .infinity)
                    #endif
                    Button("Forward \(videoSkipForwardSeconds) seconds", glyph: videoSkipForwardSeconds == 10 ? .seekForward10 : .fastForward, size: 28) { skipVideo(forward: true); revealVideoChrome() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSeekForwardButton")
                    .focused($focusedVideoControl, equals: "videoSeekForwardButton")
                    #if os(iOS)
                    .frame(maxWidth: .infinity)
                    #endif
                    #if os(iOS)
                    if playback.nextEpisode != nil {
                        nextEpisodeTransport
                            .frame(width: videoTransportSideReserve, height: 44, alignment: .trailing)
                    } else {
                        Color.clear
                            .frame(width: videoTransportSideReserve, height: 44)
                            .accessibilityHidden(true)
                    }
                    #endif
                }
                .buttonStyle(LuminaQuietActionStyle())
                .controlSize(.large)
                .frame(maxWidth: .infinity)
                #if os(tvOS)
                .focusSection()
                .onMoveCommand { direction in
                    if direction == .up {
                        focusedVideoControl = preferredContextualControl ?? "videoSettingsButton"
                    }
                }
                #endif
            }
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
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .padding(.horizontal, videoHorizontalInset)
        .padding(.vertical, videoVerticalInset)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoPlaybackChrome")
        #if os(tvOS)
        .task {
            guard restoreSettingsFocus else { return }
            focusedVideoControl = nil
            await Task.yield()
            guard !Task.isCancelled, !videoSettingsVisible else { return }
            focusedVideoControl = "videoSettingsButton"
            restoreSettingsFocus = false
        }
        #endif
        #endif
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

    private var playbackTimeProbe: some View {
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

    #if LUMINA_UI_TESTING
    private var systemArtworkProbeValue: String {
        let info = MPNowPlayingInfoCenter.default().nowPlayingInfo
        guard let mediaType = info?[MPNowPlayingInfoPropertyMediaType] as? NSNumber,
              mediaType.uintValue == MPNowPlayingInfoMediaType.audio.rawValue,
              let artwork = info?[MPMediaItemPropertyArtwork] as? MPMediaItemArtwork,
              let image = artwork.image(at: CGSize(width: 32, height: 32)),
              image.size.width > 0, image.size.height > 0, image.cgImage != nil else { return "Missing" }
        return info?[MPMediaItemPropertyTitle] as? String ?? "Missing title"
    }
    #endif

    private var audioHorizontalInset: CGFloat {
        #if os(tvOS)
        80
        #else
        24
        #endif
    }
    private var videoHorizontalInset: CGFloat {
        #if os(tvOS)
        64
        #else
        20
        #endif
    }
    private var videoVerticalInset: CGFloat {
        #if os(tvOS)
        48
        #else
        16
        #endif
    }
    private var videoTransportSpacing: CGFloat {
        #if os(tvOS)
        28
        #else
        0
        #endif
    }
    private var videoTransportSideReserve: CGFloat {
        playback.nextEpisode == nil ? 0 : 64
    }
    #if os(tvOS)
    private var tvAudioControlBand: some View {
        VStack(spacing: 18) {
            Button("Queue", glyph: .queue) { queuePresented = true }
                .labelStyle(.iconOnly)
                .accessibilityValue(playback.queue.isEmpty ? "Empty" : "\(playback.queueIndex + 1) of \(playback.queue.count)")
                .accessibilityIdentifier("audioQueueButton")
                .disabled(playback.queue.isEmpty)
                .focused($focusedAudioControl, equals: .queue)
                .onMoveCommand { direction in
                    if direction == .up { focusedAudioControl = .artwork }
                }
            audioPlayPauseButton
                .focused($focusedAudioControl, equals: .playPause)
            audioRepeatButton
                .labelStyle(.iconOnly)
                .focused($focusedAudioControl, equals: .repeatMode)
            Button("Stop", glyph: .stop, variant: .solid) { stopAudio() }
                .labelStyle(.iconOnly)
                .accessibilityIdentifier("audioStopButton")
                .focused($focusedAudioControl, equals: .stop)
                .onMoveCommand { direction in
                    if direction == .down { focusedAudioControl = .timeline }
                }
        }
        .font(.title2)
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .focusSection()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioControlBand")
    }
    #endif

    private var audioTimeline: some View {
        VStack(spacing: 8) {
            #if os(tvOS)
            ProgressView(value: min(playback.elapsedSeconds, max(1, playback.request.item.durationSeconds)), total: max(1, playback.request.item.durationSeconds))
                .tint(focusedAudioControl == .timeline ? .white : LuminaTheme.brand)
            #else
            Slider(value: Binding(get: { playback.elapsedSeconds }, set: { playback.seek(to: $0) }), in: 0...max(1, playback.request.item.durationSeconds), onEditingChanged: { editing in
                if editing {
                    audioIsScrubbing = true
                    revealAudioTimeline()
                } else {
                    finishAudioScrubbing()
                }
            })
                .tint(LuminaTheme.brand)
                // A physical Slider drag can update its binding while UIKit drops its
                // editing-end callback. The paired SwiftUI gesture keeps that missed
                // release from permanently suppressing the progress fade.
                .simultaneousGesture(DragGesture(minimumDistance: 0).onEnded { _ in
                    finishAudioScrubbing()
                })
                .accessibilityLabel("Track position")
            #endif
            HStack {
                Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                Spacer()
                Text(Duration.seconds(playback.request.item.durationSeconds).formatted(.time(pattern: .minuteSecond)))
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.white.opacity(0.8))
        }
        #if os(tvOS)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(focusedAudioControl == .timeline ? .white.opacity(0.1) : .clear,
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .contentShape(.rect)
        .focusable()
        .focusEffectDisabled()
        .focused($focusedAudioControl, equals: .timeline)
        .onMoveCommand { direction in
            switch direction {
            case .left:
                playback.seek(to: playback.elapsedSeconds - 15)
            case .right:
                playback.seek(to: playback.elapsedSeconds + 15)
            case .up:
                focusedAudioControl = .artwork
            case .down:
                audioOptionsFocusRequest += 1
            default:
                break
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Track position")
        .accessibilityValue(
            "\(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond))) of "
                + Duration.seconds(playback.request.item.durationSeconds).formatted(.time(pattern: .minuteSecond))
        )
        .accessibilityAdjustableAction { direction in
            playback.seek(to: playback.elapsedSeconds + (direction == .increment ? 15 : -15))
        }
        #else
        .accessibilityElement(children: .contain)
        #endif
        .accessibilityIdentifier("audioTimeline")
    }

    @ViewBuilder private var audioHeaderActions: some View {
        Button("Queue", glyph: .queue) { queuePresented = true }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("audioQueueButton")
        audioPlayPauseButton
        audioRepeatButton.labelStyle(.iconOnly)
        #if os(iOS)
        NativeMusicAirPlayButton(session: audioSession)
        NativeMusicCastButton(session: audioSession)
        #endif
        Button("Stop", glyph: .stop, variant: .solid) { stopAudio() }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("audioStopButton")
    }

    private var audioPlayPauseButton: some View {
        Button {
            if playback.hasFinished { replay() } else { playback.togglePlayback() }
        } label: {
            if playback.errorMessage != nil {
                Label("Retry", glyph: .sync)
            } else if playback.hasFinished {
                Label("Replay", systemImage: "arrow.counterclockwise")
            } else {
                Label(playback.isPlaying ? "Pause" : "Play",
                      glyph: playback.isPlaying ? .pause : .play, variant: .solid)
            }
        }
        .labelStyle(.iconOnly)
        .accessibilityIdentifier("audioPlayPauseButton")
        .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
    }

    private var audioRepeatButton: some View {
        Button(playback.repeatMode.label,
               glyph: playback.repeatMode == .one ? .repeatOne : .repeat) { playback.cycleRepeat() }
            .accessibilityValue(playback.repeatMode.label)
            .accessibilityIdentifier("audioRepeatButton")
    }

    private func skipAudioTrack(offset: Int) {
        guard !playback.isReplaying, offset > 0 ? playback.hasNext : playback.hasPrevious else { return }
        Task { await playback.playQueued(offset: offset) }
    }

    private var audioQueue: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Play queue").font(.title2.bold())
                Spacer()
                Button("Done") { queuePresented = false }
                    .accessibilityIdentifier("audioQueueDoneButton")
            }
            .padding(.horizontal)
            #if os(tvOS)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    audioQueueRows
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal)
                .padding(.vertical, 24)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .focusSection()
            #else
            List {
                audioQueueRows
            }
            .listStyle(.plain)
            #endif
        }
        #if os(tvOS)
        .frame(width: 1000, height: 650)
        #endif
        .padding(.vertical)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioQueueScreen")
        #if os(tvOS)
        .onExitCommand { queuePresented = false }
        #endif
    }

    @ViewBuilder private var audioQueueRows: some View {
        ForEach(playback.queueRows) { row in
            let index = row.index
            let item = row.item
            HStack(spacing: 16) {
                Button {
                    Task { await playback.playOccurrence(row.id) }
                } label: {
                    HStack(spacing: 16) {
                        LuminaIcon(glyph: index == playback.queueIndex ? .volumeHigh : .music,
                                   variant: .solid, size: 20)
                        Text(item.name).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .disabled(playback.isReplaying || audioSession.isSonos || audioSession.isRemoteOutput)
                .accessibilityIdentifier(row.accessibilityID)
                if index > playback.queueIndex {
                    Menu {
                        Button("Play next") { playback.moveOccurrence(row.id) }
                        Button("Move up") { playback.moveOccurrence(row.id, offset: -1) }
                            .disabled(index == playback.queueIndex + 1)
                        Button("Move down") { playback.moveOccurrence(row.id, offset: 1) }
                            .disabled(index == playback.queue.count - 1)
                        Button("Remove from queue", role: .destructive) { playback.removeOccurrence(row.id) }
                    } label: {
                        Label("Track actions", glyph: .moreHorizontal)
                    }
                    .labelStyle(.iconOnly)
                    .disabled(audioSession.isSonos || audioSession.isRemoteOutput)
                }
            }
        }
    }

    private func finish() {
        if let onVideoDismiss { onVideoDismiss(); return }
        if !playback.request.item.isAudio { playback.close() }
        dismiss()
    }

    private func stopAudio() {
        audioSession.stop()
        dismiss()
    }

    private func replay() {
        Task { await playback.replay() }
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
                                  rateReason: AVPlayer.RateDidChangeReason? = nil, seekFinished: Bool? = nil) {
        guard !request.item.isAudio, PlaybackDiagnostics.shared.isCapturing else { return }
        var sample = PlaybackDiagnosticSample()
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
        if event == .errorLog, let log = item?.errorLog()?.events.last {
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

    private func installPlaybackDiagnosticObservers() {
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

    private func playNativePlayer() {
        guard !isCasting else { return }
        guard videoDolbyVisionConversion?.hasFailure != true else {
            pauseNativePlayer()
            return
        }
        recordPlaybackDiagnostic(.play)
        player.play()
    }

    private func pauseNativePlayer() {
        recordPlaybackDiagnostic(.pause)
        player.pause()
    }

    private func replaceNativeItem(_ item: AVPlayerItem?) {
        player.replaceCurrentItem(with: item)
        recordPlaybackDiagnostic(.itemReplaced)
    }

    private func seekNativePlayer(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) async -> Bool {
        recordPlaybackDiagnostic(.seekRequested, seekTarget: time.seconds)
        let result = await player.seek(to: time, toleranceBefore: toleranceBefore, toleranceAfter: toleranceAfter)
        recordPlaybackDiagnostic(.seekCompleted, seekTarget: time.seconds, seekFinished: result)
        return result
    }

    private(set) var request: PlaybackRequest
    let store: LuminaStore
    weak var audioOwner: NativeAudioSession?
    var audioTransport: NativeMusicTransport?
    private var reportedAudioStart = false
    let initiallyPaused: Bool
    var elapsedSeconds: Double
    var hasFinished = false
    var isReplaying = false
    var errorMessage: String?
    var syncError: String?
    var isPlaying = false
    private(set) var queue: [MediaItem]
    private var occurrenceIDs: [UUID]
    typealias QueueRow = QueuePresentationRow<MediaItem>
    private(set) var queueRows: [QueueRow]

    private static func makeQueueRows(items: [MediaItem], occurrenceIDs: [UUID]) -> [QueueRow] {
        QueuePresentation.rows(items: items, occurrenceIDs: occurrenceIDs, itemID: { $0.id })
    }
    private func rebuildQueueRows() { queueRows = Self.makeQueueRows(items: queue, occurrenceIDs: occurrenceIDs) }
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
    private(set) var repeatMode = RepeatMode.off
    private(set) var queueIndex: Int
    var hasNext: Bool { queueIndex + 1 < queue.count }
    var hasPrevious: Bool { !queue.isEmpty && queueIndex > 0 }
    private var timeObserver: Any?
    private var controlObservation: NSKeyValueObservation?
    private var endObserver: NSObjectProtocol?
    nonisolated(unsafe) private var playbackDiagnosticObservers: [NSObjectProtocol] = []
    #if os(iOS)
    // NotificationCenter permits unregistering its token during nonisolated deinit.
    nonisolated(unsafe) private var videoRateChangeObserver: NSObjectProtocol?
    @ObservationIgnored private weak var pendingSystemBackgroundPauseItem: AVPlayerItem?
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
    private var videoSource = NativeVideoSource() {
        didSet { videoDolbyVisionConversion = nil }
    }
    var videoSourceBytesRead: Int64? { videoSource.sourceBytesRead }
    private(set) var videoDolbyVisionConversion: NativeVideoSource.DolbyVisionConversionSnapshot?
    private(set) var isSwitchingVideoStream = false
    private(set) var videoStreamError: String?
    private(set) var sourceAudioIsAutomatic = true
    private(set) var attachedVideoSubtitle: PlaybackStream?
    private var selectedEmbeddedSubtitleStreamIndex: Int?
    private var hasExplicitEmbeddedSubtitleSelection = false
    private var hasUnresolvedEmbeddedSubtitleSelection = false
    private(set) var videoQuality: PlaybackQuality = .automatic
    private(set) var subtitleDelaySeconds: Double = 0
    private(set) var videoSpeed: Float = 1
    private var videoSeekAuthority = VideoSeekAuthority()
    private var videoSeekTask: Task<Void, Never>?
    private var videoRateLease = VideoTemporaryRateLease()
    private(set) var isSeekingChapter = false
    private(set) var playbackMarkers: [PlaybackMarker] = []
    private(set) var nextEpisode: MediaItem?
    private(set) var videoActionsError: String?
    private var nextEpisodeAutoplay: Bool?
    private var pendingVideoSource: NativeVideoSource?
    private var pendingVideoPlayer: AVPlayer?
    private var episodeTask: Task<Void, Never>?
    private var actionsGeneration = UUID()
    var canUseVideoActions: Bool { !isCasting && didSeek && !closed && !isSwitchingVideoStream && !isSeekingChapter && !isReplaying }
    var canAdjustSubtitleTiming: Bool { request.usesClientDemux || attachedVideoSubtitle != nil }
    var subtitlesExplicitlyOff = false
    private var isCommittingVideoStream = false
    private var reportQueue: Task<Void, Never>?
    private var pendingReports: [(event: PlaybackEvent, request: PlaybackRequest, seconds: Double, paused: Bool)] = []
    private var didSeek = false
    private var compatibilityRetryRequestID: String?
    private var videoAdvancedRequestID: String?
    private var started = false
    private var autoplayWhenReady = true
    private var isActive = true
    private var closed = false
    private var didReportCompletion = false
    private var lastReport: Double = -1
    private var remoteTargets: [(command: MPRemoteCommand, target: Any)] = []
    #if os(iOS)
    private var nowPlayingArtwork: MPMediaItemArtwork?
    private var nowPlayingArtworkItemID: String?
    private var nowPlayingArtworkURL: URL?
    private var nowPlayingArtworkGeneration = 0
    private var nowPlayingArtworkTask: Task<Void, Never>?
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
        #if os(iOS)
        if let videoRateChangeObserver {
            NotificationCenter.default.removeObserver(videoRateChangeObserver)
        }
        #endif
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE playback-model deinit \(lifecycleToken)")
        #endif
    }

    private func installVideoRateChangeObserver() {
        #if os(iOS)
        guard !request.item.isAudio, videoRateChangeObserver == nil else { return }
        videoRateChangeObserver = NotificationCenter.default.addObserver(
            forName: AVPlayer.rateDidChangeNotification,
            object: player,
            queue: .main
        ) { [weak self] notification in
            let reason = (notification.userInfo?[AVPlayer.rateDidChangeReasonKey] as? AVPlayer.RateDidChangeReason)
                ?? (notification.userInfo?[AVPlayer.rateDidChangeReasonKey] as? String).map(AVPlayer.RateDidChangeReason.init(rawValue:))
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.handleVideoRateChange(reason)
            }
        }
        #endif
    }

    private func removeVideoRateChangeObserver() {
        #if os(iOS)
        if let videoRateChangeObserver {
            NotificationCenter.default.removeObserver(videoRateChangeObserver)
            self.videoRateChangeObserver = nil
        }
        #endif
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

    private func awaitInitialPlayback(positionSeconds: Double) async throws {
        try await NativeVideoSource.waitUntilReady(player)
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

    private func handleVideoPlaybackFailure(_ error: Error?) async {
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

    private func retryInitialCompatibleVideo(after error: Error) async throws {
        let failed = request
        guard !closed, !didSeek, compatibilityRetryRequestID != failed.id,
              failed.canRetryCompatibleVideo(after: error) else { throw error }
        compatibilityRetryRequestID = failed.id
        let candidateSource = NativeVideoSource()
        var candidateRequest: PlaybackRequest?
        do {
            let next = try await store.prepareCompatiblePlayback(after: failed, error: error)
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed, request.id == failed.id else { throw CancellationError() }
            let subtitle = next.mediaStreams.first { $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle }
            let item = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: subtitle)
            try Task.checkCancellation()
            guard !closed, request.id == failed.id else { throw CancellationError() }
            replaceNativeItem(item)
            try await awaitInitialPlayback(positionSeconds: next.startPositionSeconds)
            try Task.checkCancellation()
            guard !closed, request.id == failed.id else { throw CancellationError() }
            let previousSource = videoSource
            enqueue(.stop, paused: true, for: failed, position: failed.startPositionSeconds)
            request = next
            videoSource = candidateSource
            attachedVideoSubtitle = subtitle
            subtitlesExplicitlyOff = next.subtitleStreamIndex == -1
            selectedEmbeddedSubtitleStreamIndex = subtitle == nil ? next.subtitleStreamIndex : nil
            previousSource.stop()
        } catch {
            candidateSource.stop()
            if let candidateRequest {
                enqueue(.stop, paused: true, for: candidateRequest, position: failed.startPositionSeconds)
            }
            throw error
        }
    }

    func start() async {
        traceContinuity("start-enter")
        guard !started, !closed else { return }
        installPlaybackDiagnosticObservers()
        recordPlaybackDiagnostic(.start, requestedPosition: request.startPositionSeconds)
        if request.item.isAudio, let audioOwner {
            started = true
            replaceNativeItem(nil)
            installRemoteControls()
            await audioOwner.start(self)
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: request.item.isAudio ? .default : .moviePlayback)
            #if os(iOS)
            try AVAudioSession.sharedInstance().setActive(true)
            #endif
        } catch {
            errorMessage = "Audio playback could not be configured."
            return
        }
        started = true
        if request.item.isAudio { installRemoteControls() }
        defer {
            if !didSeek { started = false; videoSource.stop() }
        }
        do {
            if request.usesClientDemux || attachedVideoSubtitle != nil {
                let item = try await prepareVideoItem(request, source: videoSource, externalSubtitle: attachedVideoSubtitle)
                try Task.checkCancellation()
                guard !closed else { throw CancellationError() }
                replaceNativeItem(item)
            }
            try await awaitInitialPlayback(positionSeconds: request.startPositionSeconds)
        } catch is CancellationError {
            return
        } catch {
            do {
                let failedSource = videoSource
                let failedRequestID = request.id
                let playbackError = await failedSource.playbackError(for: error)
                let conversion = await failedSource.dolbyVisionConversion
                try Task.checkCancellation()
                guard !closed, videoSource === failedSource, request.id == failedRequestID else { throw CancellationError() }
                if conversion?.hasFailure == true { videoDolbyVisionConversion = conversion }
                try await retryInitialCompatibleVideo(after: playbackError)
            } catch is CancellationError {
                return
            } catch {
                videoSource.stop()
                errorMessage = player.currentItem?.status == .failed
                    ? (request.item.isAudio ? "This track could not be played. Check its format and try again." : "This video could not be played in its current format.")
                    : "The media did not become ready. Check the server connection."
                return
            }
        }
        // Do not report a zero position while the item is loading or seeking to resume.
        guard let item = player.currentItem else { return }
        guard !closed, !Task.isCancelled else { return }
        if !request.item.isAudio {
            item.audioTimePitchAlgorithm = .spectral
            if videoSpeed < 1, !item.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
        }
        didSeek = true
        updatePosition()
        enqueue(.start, paused: !autoplayWhenReady)
        let progressInterval = CMTime(seconds: 1, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: progressInterval, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isCasting, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.traceContinuity("periodic-callback")
                let source = self.videoSource
                let requestID = self.request.id
                let conversion = await source.dolbyVisionConversion
                guard !self.closed, !self.hasFinished, self.videoSource === source, self.request.id == requestID,
                      !self.isCasting, !self.isSwitchingVideoStream, !self.isCommittingVideoStream else { return }
                self.videoDolbyVisionConversion = conversion
                if conversion?.hasFailure == true {
                    self.updatePosition()
                    self.pauseNativePlayer()
                    await self.handleVideoPlaybackFailure(NativeVideoSource.SelectionError.invalidDolbyVisionConversion)
                    return
                }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.applyEffectiveVideoRateIfPermitted()
                self.updatePosition()
                if !self.request.item.isAudio, self.elapsedSeconds > self.request.startPositionSeconds + 0.25 {
                    self.videoAdvancedRequestID = self.request.id
                }
                let shouldReport = abs(self.elapsedSeconds - self.lastReport) >= NativeVideoSource.progressObservationInterval.seconds
                if shouldReport {
                    self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
                }
                if self.player.currentItem?.status == .failed {
                    await self.handleVideoPlaybackFailure(self.player.currentItem?.error)
                }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isCasting, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.traceContinuity("control-status-callback")
                self.isPlaying = self.player.timeControlStatus == .playing
                self.applyEffectiveVideoRateIfPermitted()
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        installVideoRateChangeObserver()
        installEndObserver()
        if autoplayWhenReady { playNativePlayer() }
    }

    func pause() {
        traceContinuity("pause")
        guard !closed else { return }
        if let audioOwner { audioOwner.pause(); return }
        #if os(iOS)
        if let castOwner, isCasting { castOwner.pause(); return }
        #endif
        if nextEpisodeAutoplay != nil { nextEpisodeAutoplay = false }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        videoRateLease.cancel()
        #if os(iOS)
        pendingSystemBackgroundPauseItem = nil
        #endif
        pauseNativePlayer()
        isPlaying = false
        guard !hasFinished, !isCommittingVideoStream else { return }
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func togglePlayback() {
        #if os(iOS)
        if let castOwner, isCasting { castOwner.togglePlayback(); return }
        #endif
        if let audioOwner { Task { await audioOwner.toggle() }; return }
        guard !closed else { return }
        if let nextEpisodeAutoplay {
            if nextEpisodeAutoplay { pause() } else { self.nextEpisodeAutoplay = true }
            return
        }
        guard !closed, !hasFinished else { return }
        if isCommittingVideoStream { autoplayWhenReady.toggle(); return }
        if !didSeek {
            guard !request.item.isAudio else { return }
            // Loading/resume seeking still belongs to start(). Only change the
            // requested intent here; do not start an unprepared item at zero.
            if autoplayWhenReady { pause() } else { autoplayWhenReady = true }
            return
        }
        let shouldPause = request.item.isAudio ? player.timeControlStatus == .playing : autoplayWhenReady
        if shouldPause { pause() }
        else { autoplayWhenReady = true; playNativePlayer(); isPlaying = true }
    }

    func setActive(_ active: Bool) {
        traceContinuity("active=\(active)")
        guard isActive != active else { return }
        isActive = active
        #if os(tvOS)
        // TV video remains paused when returning until the user presses Play.
        if !request.item.isAudio { pause() }
        #endif
        // On iOS, AVFoundation handles background pausing and PiP continuation.
        // Pausing during willResignActive would prevent automatic PiP from starting.
    }

    func seek(to seconds: Double) {
        if let audioOwner { Task { await audioOwner.seek(seconds) }; return }
        if request.item.isAudio {
            guard seconds.isFinite, didSeek, !closed, !hasFinished, !isCommittingVideoStream else { return }
            elapsedSeconds = min(max(0, seconds), request.item.durationSeconds)
            player.seek(to: CMTime(seconds: elapsedSeconds, preferredTimescale: 600),
                        toleranceBefore: .zero, toleranceAfter: .zero)
            return
        }
        let direction: VideoSeekDirection = seconds >= gestureSeekTime ? .forward : .backward
        requestVideoSeek(to: seconds, direction: direction)
    }

    func requestRelativeVideoSeek(_ delta: Double) {
        guard delta.isFinite else { return }
        requestVideoSeek(to: gestureSeekTime + delta, direction: delta >= 0 ? .forward : .backward)
    }

    func supportsVideoSpeed(_ rate: Float) -> Bool {
        guard !request.item.isAudio, let item = player.currentItem, item.status == .readyToPlay else { return false }
        return VideoPlaybackSpeed.allCases.contains(where: { $0.rawValue == rate })
            && (rate >= 1 || item.canPlaySlowForward)
    }

    func selectVideoSpeed(_ rate: Float) {
        setPreferredVideoRate(Double(rate))
    }

    func setPreferredVideoRate(_ rate: Double) {
        guard rate.isFinite, rate > 0 else { return }
        let nativeRate = Float(rate)
        guard !isCasting, !closed, !hasFinished, !isSwitchingVideoStream, supportsVideoSpeed(nativeRate) else { return }
        setStoredPreferredVideoRate(Double(nativeRate))
        applyEffectiveVideoRateIfPermitted()
    }

    func beginTemporaryVideoRate() -> UUID? {
        guard canUseVideoActions, permitsVideoRateApplication,
              player.currentItem?.status == .readyToPlay,
              !player.isExternalPlaybackActive,
              !AVAudioSession.sharedInstance().currentRoute.outputs.contains(where: { $0.portType == .airPlay }) else { return nil }
        let token = videoRateLease.begin(isEligible: true)
        applyEffectiveVideoRateIfPermitted()
        return token
    }

    func endTemporaryVideoRate(_ token: UUID) {
        guard videoRateLease.end(token) else { return }
        restorePreferredVideoRateIfRunning()
    }

    func cancelVideoGestureOverrides() {
        let wasOverridden = videoRateLease.token != nil
        videoRateLease.cancel()
        if wasOverridden { restorePreferredVideoRateIfRunning() }
    }

    private func currentVideoSeekRanges() -> [VideoSeekRange] {
        guard !request.item.isAudio, let item = player.currentItem else { return [] }
        let seekable = item.seekableTimeRanges.compactMap { value -> VideoSeekRange? in
            let range = value.timeRangeValue
            let start = range.start.seconds
            let duration = range.duration.seconds
            guard start.isFinite, duration.isFinite, duration > 0 else { return nil }
            // CMTimeRange's end is exclusive. Keep the target playable after
            // conversion to the seek command's 600-tick timebase as well.
            let tick = max(1.0 / 600, 1.0 / Double(max(1, range.duration.timescale)))
            return VideoSeekRange(start, max(start, start + duration - tick))
        }
        if !seekable.isEmpty { return VideoSeekAuthority.normalizedRanges(seekable) }

        let itemDuration = item.duration.seconds
        guard itemDuration.isFinite, itemDuration > 0,
              let range = VideoSeekRange(0, max(0, itemDuration - 1.0 / 600)) else { return [] }
        return [range]
    }

    private func requestVideoSeek(to candidate: Double, direction: VideoSeekDirection) {
        traceContinuity("seek-request=\(candidate)")
        #if os(iOS)
        if let castOwner, isCasting {
            guard candidate.isFinite else { return }
            castOwner.seek(to: min(max(0, candidate), request.item.durationSeconds))
            return
        }
        #endif
        guard candidate.isFinite, canUseVideoActions, !hasFinished else { return }
        guard let submission = videoSeekAuthority.request(
            target: candidate, ranges: currentVideoSeekRanges(), direction: direction
        ) else { return }
        submitVideoSeek(submission)
    }

    private func submitVideoSeek(_ submission: VideoSeekSubmission) {
        guard let item = player.currentItem else {
            _ = videoSeekAuthority.complete(submission, actualTime: nil)
            return
        }
        videoSeekTask?.cancel()
        item.cancelPendingSeeks()
        videoSeekTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let playhead = self.player.currentTime().seconds
            let direction: VideoSeekDirection = playhead.isFinite && submission.target < playhead ? .backward : .forward
            guard let revalidated = self.videoSeekAuthority.revalidatedSubmission(
                submission, ranges: self.currentVideoSeekRanges(), direction: direction
            ) else { return }
            let didSeek = await self.seekNativePlayer(
                to: CMTime(seconds: revalidated.target, preferredTimescale: 600),
                toleranceBefore: .zero, toleranceAfter: .zero
            )
            guard !Task.isCancelled else { return }
            self.completeVideoSeek(revalidated, item: item, didSeek: didSeek)
        }
    }

    private func completeVideoSeek(_ submission: VideoSeekSubmission, item: AVPlayerItem, didSeek: Bool) {
        guard !closed, player.currentItem === item else { return }
        let actual = player.currentTime().seconds
        switch videoSeekAuthority.complete(submission, actualTime: actual) {
        case .ignored:
            return
        case .settled(let position):
            videoSeekTask = nil
            if let position { elapsedSeconds = position }
            else { updatePosition() }
            if didSeek {
                isPlaying = player.timeControlStatus == .playing
                enqueue(.progress, paused: !isPlaying)
            }
            recoverSystemBackgroundPauseIfNeeded()
        }
    }

    private func invalidateVideoSeekWork() {
        if videoSeekTask != nil {
            videoSeekTask?.cancel()
            player.currentItem?.cancelPendingSeeks()
        }
        videoSeekTask = nil
        videoSeekAuthority.invalidate()
    }

    private func setStoredPreferredVideoRate(_ rate: Double) {
        videoRateLease.setPreferredRate(rate)
        videoSpeed = Float(videoRateLease.preferredRate)
        player.defaultRate = videoSpeed
    }

    private var permitsVideoRateApplication: Bool {
        !isCasting && autoplayWhenReady && isActive && !closed && !hasFinished
            && !isSwitchingVideoStream && !isCommittingVideoStream
            && videoDolbyVisionConversion?.hasFailure != true
            && player.timeControlStatus == .playing
    }

    private func applyEffectiveVideoRateIfPermitted() {
        traceContinuity("rate-application-attempt")
        guard !request.item.isAudio,
              let rate = videoRateLease.rateToApply(isPlaybackAllowed: permitsVideoRateApplication) else { return }
        let target = Float(rate)
        // Equal assignments retrigger AVPlayer rate/status observation without
        // changing playback, feeding periodic callbacks back into this path.
        guard player.rate != target else { return }
        traceContinuity("rate-write")
        player.rate = target
    }

    private func restorePreferredVideoRateIfRunning() {
        // PiP may continue after scene deactivation. Also replace a buffering
        // engine's pending 2× rate; a paused engine never receives a nonzero rate.
        guard !isCasting, !request.item.isAudio, autoplayWhenReady, !closed, !hasFinished,
              !isSwitchingVideoStream, !isCommittingVideoStream,
              videoDolbyVisionConversion?.hasFailure != true,
              player.timeControlStatus != .paused else { return }
        player.rate = Float(videoRateLease.preferredRate)
    }

    func refreshVideoActions() async {
        guard !request.item.isAudio, !closed else { return }
        let current = request
        let generation = UUID()
        actionsGeneration = generation
        playbackMarkers = []
        nextEpisode = nil
        videoActionsError = nil
        async let markers = store.playbackMarkers(for: current)
        async let following = store.nextEpisode(after: current.item)
        do {
            let result = try await markers
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            playbackMarkers = result
        } catch {
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            videoActionsError = "Some playback actions could not load. Try again."
        }
        do {
            let result = try await following
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            nextEpisode = result
        } catch {
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            videoActionsError = "Some playback actions could not load. Try again."
        }
    }

    var activeIntro: PlaybackMarker? {
        guard !hasFinished else { return nil }
        return playbackMarkers.first { $0.kind == .intro && elapsedSeconds >= $0.startSeconds && elapsedSeconds < $0.endSeconds }
    }

    var activeCredits: PlaybackMarker? {
        guard !hasFinished else { return nil }
        return playbackMarkers.first { $0.kind == .credits && elapsedSeconds >= $0.startSeconds && elapsedSeconds < $0.endSeconds }
    }

    func skip(_ marker: PlaybackMarker) async {
        guard !closed, !hasFinished, !isSwitchingVideoStream, !isSeekingChapter, !isReplaying, didSeek,
              playbackMarkers.contains(marker), marker == activeIntro || marker == activeCredits,
              let item = player.currentItem else { return }
        invalidateVideoSeekWork()
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await seekNativePlayer(to: CMTime(seconds: marker.endSeconds, preferredTimescale: 600),
                                        toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, player.currentItem === item else { return }
        updatePosition()
        if success {
            if marker.kind == .credits, marker.endSeconds >= request.item.durationSeconds,
               request.item.durationSeconds > 0 {
                finishPlayback()
            } else {
                isPlaying = player.timeControlStatus == .playing
                enqueue(.progress, paused: !isPlaying)
            }
        } else {
            videoStreamError = "This segment could not be skipped. Try again."
        }
    }

    func playNextEpisode() async {
        guard episodeTask == nil else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("busy")
            #endif
            return
        }
        guard canUseVideoActions else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("unavailable")
            #endif
            return
        }
        #if os(iOS) && LUMINA_UI_TESTING
        recordNextEpisodeDiagnostic("requested")
        #endif
        let task = Task { await advanceToNextEpisode() }
        episodeTask = task
        await task.value
        episodeTask = nil
    }

    private func advanceToNextEpisode() async {
        guard canUseVideoActions, !request.item.isAudio, let episode = nextEpisode else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("rejected")
            #endif
            return
        }
        #if os(iOS) && LUMINA_UI_TESTING
        recordNextEpisodeDiagnostic("entry")
        #endif
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isSwitchingVideoStream = true
        nextEpisodeAutoplay = true
        videoStreamError = nil
        defer { isSwitchingVideoStream = false; isCommittingVideoStream = false; nextEpisodeAutoplay = nil }
        let previousRequest = request
        let previousItem = player.currentItem
        let previousSource = videoSource
        let candidateSource = NativeVideoSource()
        let candidatePlayer = AVPlayer()
        configureDeviceAudioLanguage(candidatePlayer, preferences: store.playbackPreferences)
        pendingVideoSource = candidateSource
        pendingVideoPlayer = candidatePlayer
        defer { pendingVideoSource = nil; pendingVideoPlayer = nil }
        var candidateRequest: PlaybackRequest?
        var installed = false
        var previousPosition = elapsedSeconds
        do {
            let next = try await store.preparePlayback(for: episode, quality: videoQuality, startPositionSeconds: 0)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("prepared")
            #endif
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed, request.id == previousRequest.id else { throw CancellationError() }
            let item = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: nil, subtitleDelay: 0)
            // A player item cannot be transferred between AVPlayers. Preflight a distinct
            // item sharing the asset, leaving the configured item for the visible player.
            candidatePlayer.replaceCurrentItem(with: AVPlayerItem(asset: item.asset))
            try await NativeVideoSource.waitUntilReady(candidatePlayer)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("candidate-ready")
            #endif
            guard await candidatePlayer.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed, request.id == previousRequest.id,
                  player.currentItem === previousItem || (hasFinished && player.currentItem == nil) else {
                throw CancellationError()
            }
            updatePosition()
            previousPosition = elapsedSeconds
            isCommittingVideoStream = true
            pauseNativePlayer()
            candidatePlayer.replaceCurrentItem(with: nil)
            replaceNativeItem(item)
            installed = true
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("installed")
            #endif
            try await NativeVideoSource.waitUntilReady(player)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("visible-ready")
            #endif
            guard await seekNativePlayer(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero), item.status == .readyToPlay else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            if !didReportCompletion { enqueue(.stop, paused: true, for: previousRequest, position: previousPosition) }
            request = next
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("committed")
            #endif
            videoSource = candidateSource
            attachedVideoSubtitle = next.mediaStreams.first {
                $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle
            }
            selectedEmbeddedSubtitleStreamIndex = attachedVideoSubtitle == nil ? next.subtitleStreamIndex : nil
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = false
            subtitleDelaySeconds = 0
            subtitlesExplicitlyOff = next.subtitleStreamIndex == -1
            sourceAudioIsAutomatic = true
            playbackMarkers = []
            nextEpisode = nil
            videoActionsError = nil
            elapsedSeconds = 0
            hasFinished = false
            didReportCompletion = false
            errorMessage = nil
            lastReport = -1
            if videoSpeed < 1, !item.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
            installEndObserver()
            isCommittingVideoStream = false
            autoplayWhenReady = nextEpisodeAutoplay == true && isActive
            enqueue(.start, paused: !autoplayWhenReady)
            if autoplayWhenReady { playNativePlayer() }
            isPlaying = autoplayWhenReady
            previousSource.stop()
        } catch {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("failed-\(nextEpisodeFailureKind(error))")
            #endif
            candidatePlayer.replaceCurrentItem(with: nil)
            if installed, !closed {
                if !hasFinished, let previousItem {
                    replaceNativeItem(previousItem)
                    _ = await seekNativePlayer(to: CMTime(seconds: previousPosition, preferredTimescale: 600),
                                          toleranceBefore: .zero, toleranceAfter: .zero)
                    if autoplayWhenReady && isActive { playNativePlayer() }
                } else {
                    replaceNativeItem(nil)
                }
            }
            candidateSource.stop()
            if let candidateRequest { enqueue(.stop, paused: true, for: candidateRequest, position: 0) }
            guard !closed else { return }
            if !(error is CancellationError) { videoStreamError = "The next episode could not start. Try again." }
        }
    }

    var currentChapterID: Int64? {
        request.chapters.last(where: { $0.startPositionSeconds <= elapsedSeconds })?.id
    }

    func selectChapter(_ chapter: PlaybackChapter) async {
        guard !request.item.isAudio, !closed, !isSwitchingVideoStream, !isSeekingChapter, !isReplaying,
              request.chapters.contains(chapter), chapter.startPositionSeconds.isFinite,
              chapter.startPositionSeconds >= 0 else { return }
        if hasFinished {
            await replay(startPositionSeconds: chapter.startPositionSeconds, initiallyPaused: true)
            return
        }
        guard didSeek, let item = player.currentItem, item.status == .readyToPlay else { return }
        invalidateVideoSeekWork()
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await seekNativePlayer(to: CMTime(seconds: chapter.startPositionSeconds, preferredTimescale: 600),
                                        toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, player.currentItem === item else { return }
        updatePosition()
        if success {
            isPlaying = player.timeControlStatus == .playing
            enqueue(.progress, paused: !isPlaying)
        } else {
            videoStreamError = "This chapter could not be opened. Try again."
        }
    }

    /// Prepare independently so a bad track never tears down the current picture or session.
    func selectVideoAudioStream(_ audioIndex: Int?) async {
        await replaceVideoStream(audioIndex: audioIndex, externalSubtitle: attachedVideoSubtitle,
                                 selectsExternalSubtitle: false, changesAudio: true)
    }

    func selectExternalVideoSubtitle(_ stream: PlaybackStream) async {
        guard stream.type == .subtitle, stream.hasExternalSubtitle else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: stream, selectsExternalSubtitle: true, changesAudio: false)
    }

    func setSubtitleDelay(_ seconds: Double) async {
        guard canAdjustSubtitleTiming else { return }
        let delay = NativeVideoSource.normalizedSubtitleDelay(seconds)
        guard delay != subtitleDelaySeconds else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: attachedVideoSubtitle, selectsExternalSubtitle: false,
                                 changesAudio: false, subtitleDelay: delay)
    }

    func selectEmbeddedSubtitle(_ option: AVMediaSelectionOption?) {
        guard let option else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = nil
            return
        }
        hasExplicitEmbeddedSubtitleSelection = true
        let streams = request.mediaStreams.filter { $0.type == .subtitle && !$0.hasExternalSubtitle }
        guard !streams.isEmpty else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = true
            selectedEmbeddedSubtitleStreamIndex = nil
            return
        }
        let labelMatches = streams.filter {
            $0.label.caseInsensitiveCompare(option.displayName) == .orderedSame
        }
        if labelMatches.count == 1 {
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = labelMatches[0].index
            return
        }
        if let language = option.extendedLanguageTag ?? option.locale?.identifier {
            let languageMatches = streams.filter {
                NativePlaybackPreferences.languageMatches($0.language, preferred: language)
            }
            if languageMatches.count == 1 {
                hasUnresolvedEmbeddedSubtitleSelection = false
                selectedEmbeddedSubtitleStreamIndex = languageMatches[0].index
                return
            }
        }
        if streams.count == 1 {
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = streams[0].index
        } else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = true
            selectedEmbeddedSubtitleStreamIndex = nil
        }
    }

    func selectVideoQuality(_ quality: PlaybackQuality) async {
        guard quality != videoQuality else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: attachedVideoSubtitle, selectsExternalSubtitle: false,
                                 changesAudio: false, quality: quality)
    }

    private func prepareVideoItem(_ next: PlaybackRequest, source: NativeVideoSource,
                                  externalSubtitle: PlaybackStream?, subtitleDelay: Double? = nil) async throws -> AVPlayerItem {
        let externalSubtitle = externalSubtitle ?? next.mediaStreams.first {
            $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle
        }
        var downloaded: URL?
        defer { if let downloaded { try? FileManager.default.removeItem(at: downloaded) } }
        var subtitles: [NativeVideoSource.ExternalSubtitle] = []
        if let externalSubtitle {
            let file = try await store.downloadSubtitle(for: externalSubtitle)
            downloaded = file
            subtitles = [.init(fileURL: file, language: externalSubtitle.language,
                               name: externalSubtitle.label, isForced: externalSubtitle.isForced,
                               durationSeconds: next.item.durationSeconds)]
        }
        // Server HLS keeps its policy-limited variant and gets a local subtitle master.
        let videoURL = externalSubtitle != nil && !next.usesClientDemux
            ? try await store.hlsMediaVariant(for: next) : next.url
        let item = try await source.prepare(
            url: videoURL, usesClientDemux: next.usesClientDemux,
            requireDolbyVisionTransportPairing: next.usesClientDemux && next.sourceContainer?.lowercased() == "m2ts",
            preferredAudioStreamIndex: next.audioStreamIndex,
            preferredSubtitleStreamIndex: externalSubtitle == nil
                ? next.subtitleStreamIndex.flatMap { $0 >= 0 ? $0 : nil } : nil,
            externalSubtitles: subtitles, bandwidth: next.bitRate,
            subtitleDelaySeconds: subtitleDelay ?? subtitleDelaySeconds
        )
        item.audioTimePitchAlgorithm = .spectral
        let conversion = await source.dolbyVisionConversion
        if source === videoSource { videoDolbyVisionConversion = conversion }
        return item
    }

    @discardableResult
    private func replaceVideoStream(audioIndex: Int?, externalSubtitle: PlaybackStream?,
                                    selectsExternalSubtitle: Bool, changesAudio: Bool,
                                    subtitleDelay: Double? = nil, quality: PlaybackQuality? = nil,
                                    resumePosition: Double? = nil, compatibilityError: Error? = nil) async -> Bool {
        guard !isCasting, !request.item.isAudio, didSeek, !closed, !hasFinished, !isSwitchingVideoStream, !isSeekingChapter,
              let previousItem = player.currentItem else {
            return false
        }
        guard resumePosition.map({ $0.isFinite && $0 >= 0 }) ?? true else { return false }
        guard selectsExternalSubtitle || !hasUnresolvedEmbeddedSubtitleSelection else {
            videoStreamError = "Select a uniquely identified subtitle or Auto before changing this stream."
            return false
        }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isSwitchingVideoStream = true
        videoStreamError = nil
        defer { isSwitchingVideoStream = false; isCommittingVideoStream = false }
        let candidateSource = NativeVideoSource()
        var candidateRequest: PlaybackRequest?
        let candidatePlayer = AVPlayer()
        configureDeviceAudioLanguage(candidatePlayer, preferences: store.playbackPreferences)
        let previousRequest = request
        let previousSource = videoSource
        var replacementInstalled = false
        var commitPosition = resumePosition ?? elapsedSeconds
        do {
            if resumePosition == nil { updatePosition() }
            let next: PlaybackRequest
            if let compatibilityError {
                next = try await store.prepareCompatiblePlayback(after: request, error: compatibilityError,
                                                                 positionSeconds: commitPosition)
            } else {
                next = try await store.preparePlayback(
                    for: request.item,
                    forceTranscode: (quality == nil && request.isTranscoding)
                        || (externalSubtitle != nil && !request.usesClientDemux)
                        || (audioIndex != nil && !request.usesClientDemux),
                    quality: quality ?? videoQuality,
                    audioStreamIndex: audioIndex,
                    subtitleStreamIndex: externalSubtitle != nil || subtitlesExplicitlyOff ? -1
                        : hasExplicitEmbeddedSubtitleSelection
                            ? selectedEmbeddedSubtitleStreamIndex ?? request.subtitleStreamIndex
                            : nil,
                    mediaSourceID: request.mediaSourceID, startPositionSeconds: commitPosition
                )
            }
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            let nextItem = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: externalSubtitle,
                                                      subtitleDelay: subtitleDelay)
            candidatePlayer.replaceCurrentItem(with: AVPlayerItem(asset: nextItem.asset))
            try await NativeVideoSource.waitUntilReady(candidatePlayer)
            // Carry a deliberate caption choice across a repackaged audio track.
            if selectsExternalSubtitle {
                guard let group = try await nextItem.asset.loadMediaSelectionGroup(for: .legible) else {
                    throw POSIXError(.ENOTSUP)
                }
                let matching = group.options.filter { $0.displayName == candidateSource.externalSubtitleDisplayName }
                guard matching.count == 1, let option = matching.first else { throw POSIXError(.ENOTSUP) }
                nextItem.select(option, in: group)
            } else if let oldGroup = try? await previousItem.asset.loadMediaSelectionGroup(for: .legible),
               !previousItem.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: oldGroup) {
                let selected = previousItem.currentMediaSelection.selectedMediaOption(in: oldGroup)
                guard let nextGroup = try await nextItem.asset.loadMediaSelectionGroup(for: .legible) else {
                    throw POSIXError(.ENOTSUP)
                }
                if let selected {
                    let equivalents = nextGroup.options.filter {
                        $0.displayName == selected.displayName && $0.extendedLanguageTag == selected.extendedLanguageTag
                    }
                    if let matching = nextGroup.mediaSelectionOption(withPropertyList: selected.propertyList())
                        ?? (equivalents.count == 1 ? equivalents.first : nil) {
                        nextItem.select(matching, in: nextGroup)
                    } else if next.subtitleStreamIndex.map({ $0 >= 0 }) == true,
                              let serverSelected = nextGroup.defaultOption {
                        // Prism's opaque option property list cannot survive a switch to
                        // server HLS. The replacement request already names this source
                        // subtitle stream, so retain the server's corresponding default.
                        nextItem.select(serverSelected, in: nextGroup)
                    } else {
                        throw POSIXError(.ENOTSUP)
                    }
                } else if nextGroup.allowsEmptySelection {
                    nextItem.select(nil, in: nextGroup)
                } else {
                    throw POSIXError(.ENOTSUP)
                }
            }
            try Task.checkCancellation()
            guard !closed, player.currentItem === previousItem else { throw CancellationError() }
            if resumePosition == nil {
                updatePosition()
                commitPosition = elapsedSeconds
            }
            var position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            guard await candidatePlayer.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero) else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed, !hasFinished, player.currentItem === previousItem else { throw CancellationError() }
            if resumePosition == nil {
                updatePosition()
                commitPosition = elapsedSeconds
            }
            position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            // AVPlayer stays stable across rotations and Duo poses. Only its item changes.
            isCommittingVideoStream = true
            pauseNativePlayer()
            candidatePlayer.replaceCurrentItem(with: nil)
            replaceNativeItem(nextItem)
            replacementInstalled = true
            try await NativeVideoSource.waitUntilReady(player)
            guard await seekNativePlayer(to: position, toleranceBefore: .zero, toleranceAfter: .zero),
                  nextItem.status == .readyToPlay else { throw POSIXError(.EIO) }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            elapsedSeconds = commitPosition
            enqueue(.stop, paused: true)
            request = next
            videoSource = candidateSource
            if videoSpeed < 1, !nextItem.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
            if changesAudio { sourceAudioIsAutomatic = audioIndex == nil }
            attachedVideoSubtitle = externalSubtitle
            if selectsExternalSubtitle {
                selectedEmbeddedSubtitleStreamIndex = nil
                hasExplicitEmbeddedSubtitleSelection = false
                hasUnresolvedEmbeddedSubtitleSelection = false
            } else if externalSubtitle == nil {
                selectedEmbeddedSubtitleStreamIndex = next.subtitleStreamIndex
            }
            if let quality { videoQuality = quality }
            if let subtitleDelay { subtitleDelaySeconds = subtitleDelay }
            if selectsExternalSubtitle { subtitlesExplicitlyOff = false }
            installEndObserver()
            lastReport = -1
            isCommittingVideoStream = false
            let resume = autoplayWhenReady && isActive
            enqueue(.start, paused: !resume)
            if resume { playNativePlayer() }
            isPlaying = resume
            previousSource.stop()
            return true
        } catch {
            candidatePlayer.replaceCurrentItem(with: nil)
            if replacementInstalled, !closed {
                replaceNativeItem(previousItem)
                _ = await seekNativePlayer(to: CMTime(seconds: commitPosition, preferredTimescale: 600),
                                      toleranceBefore: .zero, toleranceAfter: .zero)
                if autoplayWhenReady && isActive { playNativePlayer() }
                isPlaying = player.timeControlStatus == .playing
            }
            candidateSource.stop()
            isCommittingVideoStream = false
            if !closed, resumePosition == nil { updatePosition() }
            if let candidateRequest {
                enqueue(.stop, paused: true, for: candidateRequest, position: commitPosition)
            }
            guard !closed else { return false }
            request = previousRequest
            if !(error is CancellationError) {
                if error as? NativeVideoSource.SelectionError == .subtitleDelayUnavailable {
                    videoStreamError = "Reset subtitle timing before changing to this stream. Your previous stream is still selected."
                } else if error as? NativeVideoSource.SelectionError == .externalSubtitleUnsupportedTiming {
                    videoStreamError = "Timing could not be adjusted for this subtitle file. Your previous timing is still selected."
                } else {
                    videoStreamError = "That stream could not be opened. Your previous stream is still selected."
                }
            }
            return false
        }
    }

    func cycleRepeat() {
        if let audioOwner { audioOwner.cycleRepeat(); return }
        switch repeatMode { case .off: repeatMode = .all; case .all: repeatMode = .one; case .one: repeatMode = .off }
    }

    func moveQueued(at source: Int, to target: Int) {
        guard source > queueIndex, target > queueIndex, queue.indices.contains(source), queue.indices.contains(target) else { return }
        if let audioOwner { audioOwner.move(from: source, to: target); return }
        reflectQueueMove(from: source, to: target)
    }

    func reflectQueueMove(from source: Int, to target: Int) {
        guard queue.indices.contains(source), queue.indices.contains(target) else { return }
        let item = queue.remove(at: source), occurrence = occurrenceIDs.remove(at: source)
        queue.insert(item, at: target); occurrenceIDs.insert(occurrence, at: target)
        rebuildQueueRows()
    }
    func reflectQueueRemoval(at index: Int) {
        guard queue.indices.contains(index) else { return }
        queue.remove(at: index); occurrenceIDs.remove(at: index)
        rebuildQueueRows()
    }

    func removeQueued(at index: Int) {
        guard queue.indices.contains(index), index > queueIndex else { return }
        if let audioOwner { audioOwner.remove(at: index); return }
        reflectQueueRemoval(at: index)
    }

    func configureAudioQueue(_ items: [MediaItem], index: Int, repeatMode: String) {
        if queue != items {
            if queue.map(\.id) != items.map(\.id) { occurrenceIDs = items.map { _ in UUID() } }
            queue = items
            rebuildQueueRows()
        }
        if queueIndex != index { queueIndex = index }
        let nextRepeatMode: RepeatMode = repeatMode == "one" ? .one : repeatMode == "all" ? .all : .off
        if self.repeatMode != nextRepeatMode { self.repeatMode = nextRepeatMode }
    }

    func receiveAudioTrack(_ request: PlaybackRequest, index: Int) {
        if reportedAudioStart { enqueue(.stop, paused: true) }
        self.request = request
        queueIndex = index
        elapsedSeconds = 0
        reportedAudioStart = false
        didReportCompletion = false
        errorMessage = nil
        updateNowPlaying()
    }

    func receiveAudioState(_ state: NativeMusicTransport.State) {
        let wasPlaying = isPlaying
        elapsedSeconds = state.position
        isPlaying = state.playing
        hasFinished = state.finished
        errorMessage = state.error
        if state.playing && !reportedAudioStart {
            reportedAudioStart = true
            enqueue(.start, paused: false)
        } else if reportedAudioStart && (abs(elapsedSeconds - lastReport) >= 5 || wasPlaying != isPlaying) {
            enqueue(.progress, paused: !isPlaying)
        }
        if state.finished && !didReportCompletion && reportedAudioStart {
            didReportCompletion = true
            enqueue(.stop, paused: true)
        }
        updateNowPlaying()
    }

    func playQueued(offset: Int) async {
        let nextIndex = queueIndex + offset
        if let audioOwner { await audioOwner.step(offset); return }
        guard queue.indices.contains(nextIndex), !closed, !isReplaying else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isReplaying = true
        defer { isReplaying = false }
        do {
            let next = try await store.preparePlayback(for: queue[nextIndex])
            guard !closed, !Task.isCancelled else { return }
            updatePosition()
            if !didReportCompletion { enqueue(.stop, paused: true) }
            pauseNativePlayer()
            isPlaying = false
            if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
            controlObservation = nil
            if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
            request = next
            queueIndex = nextIndex
            elapsedSeconds = next.startPositionSeconds
            didSeek = false
            started = false
            hasFinished = false
            didReportCompletion = false
            lastReport = -1
            autoplayWhenReady = next.item.isAudio || isActive
            errorMessage = nil
            replaceNativeItem(AVPlayerItem(url: next.url))
            await start()
        } catch {
            errorMessage = "The next track could not start. Check your connection and try again."
        }
    }

    func replay(startPositionSeconds: Double = 0, initiallyPaused: Bool = false) async {
        if let audioOwner { await audioOwner.select(queueIndex); return }
        guard hasFinished, !closed, !isReplaying, !isSwitchingVideoStream else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isReplaying = true
        defer { isReplaying = false }
        autoplayWhenReady = !initiallyPaused && isActive
        errorMessage = nil
        // Capture the rendered choice before stopping the old loopback source.
        let previousItem = player.currentItem
        let previousSubtitleGroup = try? await previousItem?.asset.loadMediaSelectionGroup(for: .legible)
        let restoresSubtitleSelection = previousSubtitleGroup.map { group in
            previousItem?.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: group) == false
        } ?? false
        let previousSubtitle = previousSubtitleGroup.flatMap { previousItem?.currentMediaSelection.selectedMediaOption(in: $0) }
        guard !closed, !Task.isCancelled else { return }
        let newRequest: PlaybackRequest
        do {
            newRequest = try await store.preparePlayback(
                for: request.item, forceTranscode: request.isTranscoding,
                quality: videoQuality,
                audioStreamIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                subtitleStreamIndex: subtitlesExplicitlyOff || attachedVideoSubtitle != nil ? -1 : request.subtitleStreamIndex,
                mediaSourceID: request.mediaSourceID, startPositionSeconds: startPositionSeconds
            )
        } catch {
            errorMessage = "Replay could not start. Check the server connection and try again."
            return
        }
        guard !closed, !Task.isCancelled else {
            try? await store.reportPlayback(event: .stop, request: newRequest,
                                            positionSeconds: newRequest.startPositionSeconds, isPaused: true)
            return
        }
        request = newRequest
        elapsedSeconds = newRequest.startPositionSeconds
        didReportCompletion = false
        do {
            let item = try await prepareVideoItem(newRequest, source: videoSource, externalSubtitle: attachedVideoSubtitle)
            guard !closed, !Task.isCancelled else { videoSource.stop(); return }
            replaceNativeItem(item)
        } catch {
            failReplay("Replay could not start. Reopen the video and try again.")
            return
        }
        installEndObserver()
        for _ in 0..<300 {
            guard !Task.isCancelled, !closed else { return }
            guard let item = player.currentItem else { return }
            if item.status == .failed {
                failReplay("Replay could not start. Check the server connection and try again.")
                return
            }
            if item.status == .readyToPlay { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        guard !closed, !Task.isCancelled else { return }
        guard player.currentItem?.status == .readyToPlay else {
            failReplay("Replay did not become ready. Check the server connection.")
            return
        }
        if restoresSubtitleSelection, let item = player.currentItem {
            do {
                let group = try await item.asset.loadMediaSelectionGroup(for: .legible)
                if let previousSubtitle {
                    guard let group else { throw POSIXError(.ENOTSUP) }
                    let equivalents = group.options.filter {
                        $0.displayName == previousSubtitle.displayName && $0.extendedLanguageTag == previousSubtitle.extendedLanguageTag
                    }
                    guard let option = group.mediaSelectionOption(withPropertyList: previousSubtitle.propertyList())
                            ?? (equivalents.count == 1 ? equivalents.first : nil) else { throw POSIXError(.ENOTSUP) }
                    item.select(option, in: group)
                } else if let group {
                    guard group.allowsEmptySelection else { throw POSIXError(.ENOTSUP) }
                    item.select(nil, in: group)
                }
            } catch {
                failReplay("Your subtitle selection could not be restored. Try replay again.")
                return
            }
        }
        if videoSpeed < 1, player.currentItem?.canPlaySlowForward != true { setStoredPreferredVideoRate(1) }
        player.defaultRate = videoSpeed
        guard await seekNativePlayer(to: CMTime(seconds: newRequest.startPositionSeconds, preferredTimescale: 600),
                                toleranceBefore: .zero, toleranceAfter: .zero) else {
            failReplay("Playback could not seek to the requested position. Try again.")
            return
        }
        guard !closed, !Task.isCancelled else { return }
        updatePosition()
        hasFinished = false
        enqueue(.start, paused: !autoplayWhenReady)
        if autoplayWhenReady { playNativePlayer() }
    }

    #if os(iOS)
    func chromecastTrackSelection() async -> (audioIndex: Int?, subtitle: String?)? {
        guard !hasUnresolvedEmbeddedSubtitleSelection, subtitleDelaySeconds == 0,
              let item = player.currentItem else { return nil }
        let requestID = request.id
        let audioGroup = try? await item.asset.loadMediaSelectionGroup(for: .audible)
        let subtitleGroup = try? await item.asset.loadMediaSelectionGroup(for: .legible)
        guard player.currentItem === item, request.id == requestID, !closed else { return nil }
        func index(for option: AVMediaSelectionOption, group: AVMediaSelectionGroup,
                   type: PlaybackStreamType) -> Int? {
            let streams = request.mediaStreams.filter { $0.type == type }
            let named = streams.filter { $0.label.caseInsensitiveCompare(option.displayName) == .orderedSame }
            if named.count == 1 { return named[0].index }
            if let language = option.extendedLanguageTag ?? option.locale?.identifier {
                let matching = streams.filter { NativePlaybackPreferences.languageMatches($0.language, preferred: language) }
                if matching.count == 1 { return matching[0].index }
            }
            if option === group.defaultOption {
                let defaults = streams.filter(\.isDefault)
                if defaults.count == 1 { return defaults[0].index }
            }
            return streams.count == 1 ? streams[0].index : nil
        }
        var audioIndex = sourceAudioIsAutomatic ? nil : request.audioStreamIndex
        if let group = audioGroup, let selected = item.currentMediaSelection.selectedMediaOption(in: group) {
            guard let mapped = index(for: selected, group: group, type: .audio) else { return nil }
            audioIndex = mapped
        }
        let subtitleIndex: Int?
        if subtitlesExplicitlyOff { subtitleIndex = nil }
        else if let attachedVideoSubtitle { subtitleIndex = attachedVideoSubtitle.index }
        else if let selectedEmbeddedSubtitleStreamIndex { subtitleIndex = selectedEmbeddedSubtitleStreamIndex }
        else if let group = subtitleGroup {
            if let selected = item.currentMediaSelection.selectedMediaOption(in: group) {
                guard let mapped = index(for: selected, group: group, type: .subtitle) else { return nil }
                subtitleIndex = mapped
            } else { subtitleIndex = nil }
        } else { subtitleIndex = request.subtitleStreamIndex }
        return (audioIndex, subtitleIndex.flatMap { $0 >= 0 ? "j:\($0)" : nil })
    }

    func beginCastHandoff() {
        guard !closed else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        pendingSystemBackgroundPauseItem = nil
        pauseNativePlayer()
    }

    func receiveCastStatus(position: Double, playing: Bool) {
        guard !closed, isCasting, position.isFinite, position >= 0 else { return }
        elapsedSeconds = position
        isPlaying = playing
        if abs(elapsedSeconds - lastReport) >= NativeVideoSource.progressObservationInterval.seconds {
            enqueue(.progress, paused: !playing)
        }
    }

    func returnFromCast(position: Double, playing: Bool) async {
        guard !closed, position.isFinite, position >= 0 else { return }
        autoplayWhenReady = false
        isPlaying = false
        if request.isTranscoding {
            let recovered = await replaceVideoStream(
                audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                externalSubtitle: attachedVideoSubtitle,
                selectsExternalSubtitle: false,
                changesAudio: false,
                resumePosition: position
            )
            guard !closed, !isCasting else { return }
            guard recovered, player.currentItem?.status == .readyToPlay else {
                autoplayWhenReady = false
                isPlaying = false
                videoStreamError = nil
                errorMessage = "Couldn’t resume on this device. Reopen the video and try again."
                return
            }
            elapsedSeconds = position
            let resume = playing && isActive
            autoplayWhenReady = resume
            if resume { playNativePlayer() }
            isPlaying = resume
            enqueue(.progress, paused: !isPlaying)
            return
        }
        let finished = await seekNativePlayer(to: CMTime(seconds: position, preferredTimescale: 600),
                                              toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, !isCasting else { return }
        guard finished, player.currentItem?.status == .readyToPlay else {
            autoplayWhenReady = false
            errorMessage = "Couldn’t resume on this device. Reopen the video and try again."
            return
        }
        elapsedSeconds = position
        let resume = playing && isActive
        autoplayWhenReady = resume
        if resume { playNativePlayer() }
        isPlaying = resume
        enqueue(.progress, paused: !isPlaying)
    }
    #endif

    func close() {
        traceContinuity("close")
        guard !closed else {
            #if LUMINA_UI_TESTING
            PlaybackLifecycleProbe.recordClose(state: "already-closed", player: player)
            #endif
            return
        }
        let wasCasting = isCasting
        #if os(iOS)
        castOwner?.detach()
        #endif
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        closed = true
        episodeTask?.cancel()
        episodeTask = nil
        pendingVideoPlayer?.replaceCurrentItem(with: nil)
        pendingVideoSource?.stop()
        pendingVideoPlayer = nil
        pendingVideoSource = nil
        videoSource.stop()
        if !isCommittingVideoStream, !wasCasting { updatePosition() }
        audioTransport?.stop()
        audioTransport = nil
        #if os(iOS)
        pendingSystemBackgroundPauseItem = nil
        #endif
        pauseNativePlayer()
        isPlaying = false
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        removeVideoRateChangeObserver()
        for observer in playbackDiagnosticObservers { NotificationCenter.default.removeObserver(observer) }
        playbackDiagnosticObservers.removeAll()
        if !didReportCompletion && (!request.item.isAudio || reportedAudioStart) {
            enqueue(.stop, paused: true)
        }
        replaceNativeItem(nil)
        #if LUMINA_UI_TESTING
        PlaybackLifecycleProbe.recordClose(state: "closed", player: player)
        #endif
        #if os(iOS)
        if !request.item.isAudio {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
        #endif
        if request.item.isAudio {
            #if os(iOS)
            resetNowPlayingArtwork()
            #endif
            for entry in remoteTargets { entry.command.removeTarget(entry.target) }
            remoteTargets.removeAll()
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        }
    }

    private func finishPlayback() {
        guard !closed, !hasFinished else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        updatePosition()
        hasFinished = true
        videoSource.stop()
        autoplayWhenReady = false
        pauseNativePlayer()
        isPlaying = false
        // The stopped server session is no longer a valid replay source (especially for HLS).
        // Remove it so AVKit can't restart the obsolete item behind the fresh Replay action.
        replaceNativeItem(nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
        updateNowPlaying()
        if request.item.isAudio {
            if repeatMode == .one { Task { await replay() } }
            else if hasNext { Task { await playQueued(offset: 1) } }
            else if repeatMode == .all && !queue.isEmpty { Task { await playQueued(offset: -queueIndex) } }
        }
    }

    private func failReplay(_ message: String) {
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        videoSource.stop()
        errorMessage = message
        replaceNativeItem(nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
    }

    private func installEndObserver() {
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        guard let item = player.currentItem else {
            endObserver = nil
            return
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard self?.player.currentItem === item else { return }
                self?.finishPlayback()
            }
        }
    }

    private func updatePosition() {
        guard !isCasting else { return }
        traceContinuity("position")
        let seconds = player.currentTime().seconds
        if didSeek, seconds.isFinite, seconds >= 0, player.currentItem?.status == .readyToPlay {
            elapsedSeconds = seconds
        }
        updateNowPlaying()
    }

    private func updateNowPlaying() {
        guard request.item.isAudio, !closed else { return }
        #if os(iOS)
        loadNowPlayingArtworkIfNeeded()
        #endif
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: request.item.name,
            MPMediaItemPropertyArtist: request.item.artists?.joined(separator: ", ") ?? request.item.albumArtist ?? "",
            MPMediaItemPropertyAlbumTitle: request.item.album ?? "",
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
            MPMediaItemPropertyPlaybackDuration: request.item.durationSeconds,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsedSeconds,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        #if os(iOS)
        if let nowPlayingArtwork { info[MPMediaItemPropertyArtwork] = nowPlayingArtwork }
        #endif
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        let commands = MPRemoteCommandCenter.shared()
        commands.nextTrackCommand.isEnabled = hasNext
        commands.previousTrackCommand.isEnabled = hasPrevious
    }

    #if os(iOS)
    private func loadNowPlayingArtworkIfNeeded() {
        let itemID = request.item.id
        let url = store.imageURL(for: request.item, maxWidth: 760)
        guard itemID != nowPlayingArtworkItemID || url != nowPlayingArtworkURL else { return }

        nowPlayingArtworkGeneration &+= 1
        let generation = nowPlayingArtworkGeneration
        nowPlayingArtworkTask?.cancel()
        nowPlayingArtworkTask = nil
        nowPlayingArtwork = nil
        nowPlayingArtworkItemID = itemID
        nowPlayingArtworkURL = url
        guard let url else { return }

        nowPlayingArtworkTask = Task { [weak self] in
            var image = await ArtworkImageCache.shared.image(for: url)
            if image == nil, !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) }
                catch { return }
                guard !Task.isCancelled else { return }
                image = await ArtworkImageCache.shared.image(for: url)
            }
            guard let self, generation == nowPlayingArtworkGeneration else { return }
            nowPlayingArtworkTask = nil
            guard !Task.isCancelled, !closed, request.item.id == itemID,
                  nowPlayingArtworkURL == url, let image else { return }
            let source = UIImage(cgImage: image)
            nowPlayingArtwork = Self.makeNowPlayingArtwork(from: source)
            updateNowPlaying()
        }
    }

    nonisolated private static func makeNowPlayingArtwork(from source: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: source.size) { _ in source }
    }

    private func resetNowPlayingArtwork() {
        nowPlayingArtworkGeneration &+= 1
        nowPlayingArtworkTask?.cancel()
        nowPlayingArtworkTask = nil
        nowPlayingArtwork = nil
        nowPlayingArtworkItemID = nil
        nowPlayingArtworkURL = nil
    }
    #endif

    private func installRemoteControls() {
        guard remoteTargets.isEmpty else { return }
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.isEnabled = true
        commands.pauseCommand.isEnabled = true
        commands.togglePlayPauseCommand.isEnabled = true
        commands.changePlaybackPositionCommand.isEnabled = true
        remoteTargets = [
            (commands.playCommand, commands.playCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() }
                    else if !self.isPlaying { self.togglePlayback() }
                }
                return .success
            }),
            (commands.pauseCommand, commands.pauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in self?.pause() }
                return .success
            }),
            (commands.togglePlayPauseCommand, commands.togglePlayPauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() } else { self.togglePlayback() }
                }
                return .success
            }),
            (commands.nextTrackCommand, commands.nextTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: 1) }
                return .success
            }),
            (commands.previousTrackCommand, commands.previousTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: -1) }
                return .success
            }),
            (commands.changePlaybackPositionCommand, commands.changePlaybackPositionCommand.addTarget { [weak self] event in
                guard let position = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
                let seconds = position.positionTime
                Task { @MainActor in self?.seek(to: seconds) }
                return .success
            }),
        ]
        updateNowPlaying()
    }

    private func diagnosticReportCode(_ event: PlaybackEvent) -> Int {
        switch event { case .start: 0; case .progress: 1; case .stop: 2 }
    }

    private func enqueue(_ event: PlaybackEvent, paused: Bool,
                         for requestOverride: PlaybackRequest? = nil, position: Double? = nil) {
        traceContinuity("report-enqueue=\(event)")
        recordPlaybackDiagnostic(.reportQueued, requestedPosition: position ?? elapsedSeconds, nativeReason: diagnosticReportCode(event))
        let reportedRequest = requestOverride ?? request
        let reportedPosition = position ?? elapsedSeconds
        if requestOverride == nil { lastReport = elapsedSeconds }
        // Keep one pending progress snapshot. Stop follows the in-flight request,
        // so an old progress response cannot overwrite the final saved position.
        if case .progress = event, let last = pendingReports.last, case .progress = last.event,
           last.request.id == reportedRequest.id {
            traceContinuity("report-coalesced")
            pendingReports.removeLast()
        }
        if case .stop = event {
            pendingReports.removeAll {
                if case .progress = $0.event { return $0.request.id == reportedRequest.id }
                return false
            }
        }
        pendingReports.append((event, reportedRequest, reportedPosition, paused))
        guard reportQueue == nil else { return }
        reportQueue = Task { [self] in
            while !pendingReports.isEmpty {
                let report = pendingReports.removeFirst()
                do {
                    traceContinuity("report-send=\(report.event)")
                    try await store.reportPlayback(event: report.event, request: report.request,
                                                   positionSeconds: report.seconds, isPaused: report.paused)
                    recordPlaybackDiagnostic(.reportAcknowledged, requestedPosition: report.seconds, nativeReason: diagnosticReportCode(report.event))
                    syncError = nil
                    if case .stop = report.event { await store.refresh() }
                } catch {
                    recordPlaybackDiagnostic(.reportFailed, requestedPosition: report.seconds, nativeReason: diagnosticReportCode(report.event))
                    syncError = report.request.item.isAudio ? "Listening progress could not sync. Check the server connection." : "Watch progress could not sync. Check the server connection."
                }
            }
            reportQueue = nil
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

#if os(iOS)
@MainActor
var usesStandardPhonePushedChrome: Bool {
    UIDevice.current.userInterfaceIdiom == .phone
        && min(UIScreen.main.bounds.width, UIScreen.main.bounds.height) < 450
}

private struct StandardPhoneLeadingEdgePopGesture: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.layoutDirection) private var layoutDirection
    @State private var isDismissing = false

    var body: some View {
        Color.clear
            .frame(width: 18)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 12)
                    .onEnded { value in
                        let horizontal = layoutDirection == .leftToRight
                            ? value.translation.width
                            : -value.translation.width
                        guard !isDismissing,
                              horizontal >= 72,
                              abs(value.translation.width) > abs(value.translation.height) * 1.5 else { return }
                        isDismissing = true
                        dismiss()
                    }
            )
    }
}

@MainActor
private struct StandardPhonePushedHeader<Actions: View>: View {
    let store: LuminaStore?
    let foreground: Color
    let background: Color
    let actions: Actions

    var body: some View {
        HStack(spacing: 10) {
            Spacer(minLength: 12)

            actions.labelStyle(.iconOnly)

            if let store {
                HStack(spacing: 0) {
                    NativeNowPlayingControls(store: store)
                }
            }
        }
        .foregroundStyle(foreground)
        .tint(foreground)
        .frame(minHeight: 44)
        .padding(.horizontal, 20)
        .padding(.vertical, 4)
        .background {
            background
                .ignoresSafeArea(.container, edges: .top)
        }
        .overlay {
            Color.clear
                .accessibilityElement()
                .accessibilityLabel("Detail navigation")
                .accessibilityIdentifier("standardPhoneNavigationHeader")
                .allowsHitTesting(false)
        }
    }
}

@MainActor
private struct StandardPhonePushedChrome<Actions: View>: ViewModifier {
    let isEnabled: Bool
    let store: LuminaStore?
    let foreground: Color
    let background: Color
    let actions: Actions
    @Environment(\.dismiss) private var dismiss

    func body(content: Content) -> some View {
        if isEnabled && usesStandardPhonePushedChrome {
            content
                .toolbarVisibility(.hidden, for: .navigationBar)
                .safeAreaInset(edge: .top, spacing: 0) {
                    StandardPhonePushedHeader(store: store, foreground: foreground, background: background, actions: actions)
                }
                .accessibilityAction(.escape) { dismiss() }
                .overlay(alignment: .leading) {
                    StandardPhoneLeadingEdgePopGesture()
                }
        } else {
            content
        }
    }
}

@MainActor
extension View {
    func standardPhonePushedChrome(isEnabled: Bool = true,
                                   store: LuminaStore? = nil,
                                   foreground: Color = LuminaTheme.ink,
                                   background: Color = LuminaTheme.paper) -> some View {
        modifier(StandardPhonePushedChrome(isEnabled: isEnabled, store: store, foreground: foreground,
                                            background: background, actions: EmptyView()))
    }

    func standardPhonePushedChrome<Actions: View>(isEnabled: Bool = true,
                                                   store: LuminaStore? = nil,
                                                   foreground: Color = LuminaTheme.ink,
                                                   background: Color = LuminaTheme.paper,
                                                   @ViewBuilder actions: () -> Actions) -> some View {
        modifier(StandardPhonePushedChrome(isEnabled: isEnabled, store: store, foreground: foreground,
                                            background: background, actions: actions()))
    }
}

@MainActor
struct NativeNowPlayingControls: View {
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession

    var body: some View {
        if let playback = audioSession.playback,
           !playback.hasFinished,
           (playback.isPlaying || !playback.initiallyPaused) {
            Group {
                artworkLink(for: playback)
                playPauseButton(for: playback)
            }
        }
    }

    private func artworkLink(for playback: PlaybackModel) -> some View {
        NavigationLink {
            PlaybackScreen(playback: playback, store: store)
        } label: {
            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 160)) { image in
                image.resizable().scaledToFill()
            } placeholder: {
                LuminaIcon(glyph: .music, variant: .solid, size: 24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .frame(width: 34, height: 34)
            .clipShape(.rect(cornerRadius: 8))
            .frame(width: 44, height: 44)
            .contentShape(.rect)
        }
        .accessibilityLabel("Now playing: \(playback.request.item.name)")
        .accessibilityHint("Open full player")
        .accessibilityIdentifier("nowPlayingBar")
    }

    private func playPauseButton(for playback: PlaybackModel) -> some View {
        Button(playback.isPlaying ? "Pause" : "Play",
               glyph: playback.isPlaying ? .pause : .play, variant: .solid) {
            playback.togglePlayback()
        }
        .labelStyle(.iconOnly)
        .frame(minWidth: 44, minHeight: 44)
        .accessibilityIdentifier("nowPlayingPauseButton")
    }
}

struct NativeNowPlayingToolbar: ToolbarContent {
    let store: LuminaStore

    @ToolbarContentBuilder
    var body: some ToolbarContent {
        if #available(iOS 27.1, *) {
            ToolbarItemGroup(placement: .topBarTrailing) {
                NativeNowPlayingControls(store: store)
            }
            .axisBehavior(.verticalPreferred)
        } else {
            ToolbarItemGroup(placement: .topBarTrailing) {
                NativeNowPlayingControls(store: store)
            }
        }
    }
}
#endif

struct NativeNowPlayingBar: View {
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    #if os(tvOS)
    @State private var presentedPlayback: PlaybackModel?
    #else
    @State private var expanded = false
    #endif

    var body: some View {
        // Keep the presentation host alive when pausing removes the bar's content.
        VStack(spacing: 0) {
        if let playback = audioSession.playback, playback.isPlaying, !playback.hasFinished {
            HStack(spacing: 16) {
                Button {
                    #if os(tvOS)
                    presentedPlayback = playback
                    #else
                    expanded = true
                    #endif
                } label: {
                    HStack(spacing: 14) {
                        CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 160)) { image in
                            image.resizable().scaledToFill()
                        } placeholder: {
                            LuminaIcon(glyph: .music, variant: .solid, size: artworkSize)
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        .frame(width: artworkSize, height: artworkSize)
                        .clipShape(.rect(cornerRadius: 8))
                        .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(playback.request.item.name).font(.headline).lineLimit(1)
                            Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .contentShape(.rect)
                }
                .accessibilityLabel("Now playing: \(playback.request.item.name)")
                .accessibilityHint("Open playback and queue controls")
                .accessibilityIdentifier("nowPlayingBar")

                #if os(tvOS)
                Button("Previous track", glyph: .previous, variant: .solid, size: 24) {
                    Task { await audioSession.step(-1) }
                }
                .labelStyle(.iconOnly)
                .disabled((!playback.hasPrevious && !audioSession.isSonos) || audioSession.busy)
                .accessibilityIdentifier("nowPlayingPreviousButton")
                #endif
                Button {
                    if playback.hasFinished { Task { await playback.replay() } }
                    else { playback.togglePlayback() }
                } label: {
                    if playback.hasFinished {
                        Label("Replay", systemImage: "arrow.counterclockwise")
                    } else {
                        Label(playback.isPlaying ? "Pause" : "Play",
                              glyph: playback.isPlaying ? .pause : .play,
                              variant: .solid, size: 24)
                    }
                }
                .labelStyle(.iconOnly)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
                .accessibilityIdentifier("nowPlayingPauseButton")
                #if os(tvOS)
                Button("Next track", glyph: .next, variant: .solid, size: 24) {
                    Task { await audioSession.step(1) }
                }
                .labelStyle(.iconOnly)
                .disabled((!playback.hasNext && !audioSession.isSonos) || audioSession.busy)
                .accessibilityIdentifier("nowPlayingNextButton")
                #endif
            }
            #if os(tvOS)
            .buttonStyle(LuminaQuietActionStyle())
            .padding(.horizontal, 80)
            .padding(.vertical, 12)
            .focusSection()
            #else
            .buttonStyle(.plain)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            #endif
            .foregroundStyle(LuminaTheme.ink)
            .background(.regularMaterial)
        }
        }
        #if os(tvOS)
        .nativeAudioPlayer(playback: $presentedPlayback, store: store)
        .onChange(of: audioSession.snapshot.queue.isEmpty) { _, empty in
            if empty { presentedPlayback = nil }
        }
        #else
        .navigationDestination(isPresented: $expanded) {
            if let playback = audioSession.playback { PlaybackScreen(playback: playback, store: store) }
        }
        .onChange(of: audioSession.playback == nil) { _, stopped in
            if stopped { expanded = false }
        }
        #endif
        #if os(tvOS)
        .onPlayPauseCommand { Task { await audioSession.toggle() } }
        #endif
    }

    private var artworkSize: CGFloat {
        #if os(tvOS)
        64
        #else
        44
        #endif
    }
}

private enum VideoPlaybackSpeed: Float, CaseIterable, Identifiable {
    case half = 0.5, threeQuarters = 0.75, normal = 1, oneAndQuarter = 1.25, oneAndHalf = 1.5, double = 2
    var id: Self { self }
    var label: String { rawValue.formatted(.number.precision(.fractionLength(0...2))) + "×" }
}

private enum VideoSettingsTab: String, CaseIterable, Identifiable {
    case info, video, audio, subtitles, chapters
    var id: Self { self }
    var title: String { rawValue.capitalized }
}

private enum VideoSubtitleSelection: Equatable {
    case automatic, off, option(Int)
}

private struct VideoMediaTracks {
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

private struct VideoPlaybackSettings: View {
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

#if os(tvOS)
/// A focusable hit target for revealing chrome must not draw a system focus plate over the video.
private struct VideoControlsRevealButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
    }
}
#endif

private struct NativePlayerLayer: UIViewRepresentable {
    let player: AVPlayer
    let videoGravity: AVLayerVideoGravity
    let onVideoTap: @MainActor () -> Void
    let pictureInPictureStartRequest: Int
    let onPictureInPicturePossibleChange: @MainActor (Bool) -> Void
    let onPictureInPictureActiveChange: @MainActor (Bool) -> Void
    let onPictureInPictureContinuationChange: @MainActor (Bool) -> Void
    let onPictureInPictureRestoreUI: @MainActor () -> Void

    init(player: AVPlayer, videoGravity: AVLayerVideoGravity,
         onVideoTap: @escaping @MainActor () -> Void,
         pictureInPictureStartRequest: Int = 0,
         onPictureInPicturePossibleChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureActiveChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureContinuationChange: @escaping @MainActor (Bool) -> Void = { _ in },
         onPictureInPictureRestoreUI: @escaping @MainActor () -> Void = {}) {
        self.player = player
        self.videoGravity = videoGravity
        self.onVideoTap = onVideoTap
        self.pictureInPictureStartRequest = pictureInPictureStartRequest
        self.onPictureInPicturePossibleChange = onPictureInPicturePossibleChange
        self.onPictureInPictureActiveChange = onPictureInPictureActiveChange
        self.onPictureInPictureContinuationChange = onPictureInPictureContinuationChange
        self.onPictureInPictureRestoreUI = onPictureInPictureRestoreUI
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onVideoTap: onVideoTap,
                    onPictureInPicturePossibleChange: onPictureInPicturePossibleChange,
                    onPictureInPictureActiveChange: onPictureInPictureActiveChange,
                    onPictureInPictureContinuationChange: onPictureInPictureContinuationChange,
                    onPictureInPictureRestoreUI: onPictureInPictureRestoreUI)
    }

    func makeUIView(context: Context) -> PlayerLayerView {
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE native-player-layer make \(ObjectIdentifier(player).hashValue)")
        #endif
        let view = PlayerLayerView()
        view.traceRendering("view-create")
        if view.playerLayer.player !== player {
            view.traceRendering("layer-player-write")
            view.playerLayer.player = player
        }
        if view.playerLayer.videoGravity != videoGravity {
            view.traceRendering("layer-gravity-write")
            view.playerLayer.videoGravity = videoGravity
        }
        context.coordinator.attach(view: view, player: player)
        view.traceRendering("view-attached")
        context.coordinator.handlePictureInPictureStartRequest(pictureInPictureStartRequest)
        return view
    }

    func updateUIView(_ view: PlayerLayerView, context: Context) {
        view.traceRendering("view-update")
        context.coordinator.onVideoTap = onVideoTap
        context.coordinator.updatePictureInPictureCallbacks(
            possible: onPictureInPicturePossibleChange,
            active: onPictureInPictureActiveChange,
            continuation: onPictureInPictureContinuationChange,
            restoreUI: onPictureInPictureRestoreUI
        )
        if view.playerLayer.player !== player {
            view.traceRendering("layer-player-write")
            view.playerLayer.player = player
        }
        if view.playerLayer.videoGravity != videoGravity {
            view.traceRendering("layer-gravity-write")
            view.playerLayer.videoGravity = videoGravity
        }
        context.coordinator.attach(view: view, player: player)
        context.coordinator.handlePictureInPictureStartRequest(pictureInPictureStartRequest)
    }

    static func dismantleUIView(_ view: PlayerLayerView, coordinator: Coordinator) {
        view.traceRendering("view-dismantle")
        coordinator.detach(from: view)
        view.playerLayer.player = nil
    }

    @MainActor final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var onVideoTap: @MainActor () -> Void
        private weak var view: PlayerLayerView?
        private weak var player: AVPlayer?

        #if os(iOS)
        private var onPictureInPicturePossibleChange: @MainActor (Bool) -> Void
        private var onPictureInPictureActiveChange: @MainActor (Bool) -> Void
        private var onPictureInPictureContinuationChange: @MainActor (Bool) -> Void
        private var onPictureInPictureRestoreUI: @MainActor () -> Void
        private var pictureInPictureController: AVPictureInPictureController?
        private weak var pictureInPictureLayer: AVPlayerLayer?
        private var pictureInPicturePossibleObservation: NSKeyValueObservation?
        private var pendingPictureInPictureStartRequest = 0
        private var handledPictureInPictureStartRequest = 0
        private var reportedPictureInPicturePossible: Bool?
        private var reportedPictureInPictureActive: Bool?
        private var reportedPictureInPictureContinuation: Bool?
        #endif

        #if os(tvOS)
        private weak var displayWindow: UIWindow?
        private var videoTapRecognizer: UITapGestureRecognizer?
        private var currentItemObservation: NSKeyValueObservation?
        private var displayCriteriaTask: Task<Void, Never>?
        private var criteriaItem: AVPlayerItem?
        private var previousDisplayCriteria: AVDisplayCriteria?
        private var installedDisplayCriteria: AVDisplayCriteria?
        private var displayCriteriaLeaseLost = false
        #endif

        init(onVideoTap: @escaping @MainActor () -> Void,
             onPictureInPicturePossibleChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureActiveChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureContinuationChange: @escaping @MainActor (Bool) -> Void,
             onPictureInPictureRestoreUI: @escaping @MainActor () -> Void) {
            self.onVideoTap = onVideoTap
            #if os(iOS)
            self.onPictureInPicturePossibleChange = onPictureInPicturePossibleChange
            self.onPictureInPictureActiveChange = onPictureInPictureActiveChange
            self.onPictureInPictureContinuationChange = onPictureInPictureContinuationChange
            self.onPictureInPictureRestoreUI = onPictureInPictureRestoreUI
            #endif
            super.init()
        }

        func attach(view: PlayerLayerView, player: AVPlayer) {
            #if os(tvOS)
            let playerChanged = self.player !== player
            #endif
            self.view = view
            self.player = player
            #if os(iOS)
            configurePictureInPicture(for: view.playerLayer)
            #endif
            #if os(tvOS)
            view.didMoveToWindowHandler = { [weak self] in
                Task { @MainActor [weak self] in
                    self?.rendererWindowDidChange()
                }
            }
            if playerChanged {
                displayCriteriaTask?.cancel()
                displayCriteriaTask = nil
                releaseDisplayCriteria()
                criteriaItem = nil
                displayCriteriaLeaseLost = false
                currentItemObservation = player.observe(\.currentItem, options: [.initial, .new]) { [weak self] observedPlayer, _ in
                    Task { @MainActor [weak self, weak observedPlayer] in
                        guard let self, let observedPlayer, self.player === observedPlayer else { return }
                        self.setDisplayCriteriaItem(observedPlayer.currentItem)
                    }
                }
            }
            rendererWindowDidChange()
            #endif
        }

        func detach(from view: PlayerLayerView) {
            guard self.view === view else { return }
            view.didMoveToWindowHandler = nil
            #if os(iOS)
            releasePictureInPicture()
            #endif
            #if os(tvOS)
            if let recognizer = videoTapRecognizer { recognizer.view?.removeGestureRecognizer(recognizer) }
            videoTapRecognizer = nil
            displayCriteriaTask?.cancel()
            displayCriteriaTask = nil
            currentItemObservation = nil
            releaseDisplayCriteria()
            criteriaItem = nil
            displayWindow = nil
            #endif
            self.view = nil
            player = nil
        }

        #if os(iOS)
        func updatePictureInPictureCallbacks(
            possible: @escaping @MainActor (Bool) -> Void,
            active: @escaping @MainActor (Bool) -> Void,
            continuation: @escaping @MainActor (Bool) -> Void,
            restoreUI: @escaping @MainActor () -> Void
        ) {
            onPictureInPicturePossibleChange = possible
            onPictureInPictureActiveChange = active
            onPictureInPictureContinuationChange = continuation
            onPictureInPictureRestoreUI = restoreUI
        }

        func handlePictureInPictureStartRequest(_ request: Int) {
            guard request != 0, request != handledPictureInPictureStartRequest else { return }
            pendingPictureInPictureStartRequest = request
            startPictureInPictureIfPossible()
        }

        private func configurePictureInPicture(for layer: AVPlayerLayer) {
            guard pictureInPictureLayer !== layer else { return }
            releasePictureInPicture()
            #if LUMINA_UI_TESTING
            if ProcessInfo.processInfo.arguments.contains("--ui-testing-force-video-system-pause") {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            #endif
            guard AVPictureInPictureController.isPictureInPictureSupported() else {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            guard let controller = AVPictureInPictureController(playerLayer: layer) else {
                reportPictureInPicturePossible(false)
                reportPictureInPictureActive(false)
                return
            }
            controller.delegate = self
            controller.canStartPictureInPictureAutomaticallyFromInline = true
            pictureInPictureLayer = layer
            pictureInPictureController = controller
            let controllerID = ObjectIdentifier(controller)
            pictureInPicturePossibleObservation = controller.observe(
                \.isPictureInPicturePossible, options: [.initial, .new]
            ) { [weak self] _, change in
                let possible = change.newValue
                Task { @MainActor [weak self] in
                    guard let self, let current = self.pictureInPictureController,
                          ObjectIdentifier(current) == controllerID else { return }
                    self.reportPictureInPicturePossible(possible ?? current.isPictureInPicturePossible)
                    self.startPictureInPictureIfPossible()
                }
            }
            reportPictureInPictureActive(controller.isPictureInPictureActive)
        }

        private func startPictureInPictureIfPossible() {
            guard pendingPictureInPictureStartRequest != 0,
                  pendingPictureInPictureStartRequest != handledPictureInPictureStartRequest,
                  let controller = pictureInPictureController,
                  controller.isPictureInPicturePossible else { return }
            handledPictureInPictureStartRequest = pendingPictureInPictureStartRequest
            controller.startPictureInPicture()
        }

        private func releasePictureInPicture() {
            pictureInPicturePossibleObservation = nil
            if pictureInPictureController?.isPictureInPictureActive == true {
                pictureInPictureController?.stopPictureInPicture()
            }
            pictureInPictureController?.delegate = nil
            pictureInPictureController = nil
            pictureInPictureLayer = nil
            pendingPictureInPictureStartRequest = 0
            reportPictureInPicturePossible(false)
            reportPictureInPictureActive(false)
            reportPictureInPictureContinuation(false)
        }

        private func reportPictureInPicturePossible(_ possible: Bool) {
            guard reportedPictureInPicturePossible != possible else { return }
            reportedPictureInPicturePossible = possible
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPicturePossible == possible else { return }
                self.onPictureInPicturePossibleChange(possible)
            }
        }

        private func reportPictureInPictureActive(_ active: Bool) {
            guard reportedPictureInPictureActive != active else { return }
            reportedPictureInPictureActive = active
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPictureActive == active else { return }
                self.onPictureInPictureActiveChange(active)
            }
        }

        private func reportPictureInPictureContinuation(_ continuing: Bool, notifyImmediately: Bool = false) {
            guard reportedPictureInPictureContinuation != continuing else { return }
            reportedPictureInPictureContinuation = continuing
            if notifyImmediately {
                onPictureInPictureContinuationChange(continuing)
                return
            }
            Task { @MainActor [weak self] in
                guard let self, self.reportedPictureInPictureContinuation == continuing else { return }
                self.onPictureInPictureContinuationChange(continuing)
            }
        }

        nonisolated func pictureInPictureControllerWillStartPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureContinuation(true, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureController(
            _ pictureInPictureController: AVPictureInPictureController,
            failedToStartPictureInPictureWithError error: any Error
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureActive(false)
                self.reportPictureInPictureContinuation(false, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureControllerDidStartPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureContinuation(true, notifyImmediately: true)
                self.reportPictureInPictureActive(true)
            }
        }

        nonisolated func pictureInPictureControllerDidStopPictureInPicture(
            _ pictureInPictureController: AVPictureInPictureController
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else { return }
                self.reportPictureInPictureActive(false)
                self.reportPictureInPictureContinuation(false, notifyImmediately: true)
            }
        }

        nonisolated func pictureInPictureController(
            _ pictureInPictureController: AVPictureInPictureController,
            restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void
        ) {
            let controllerID = ObjectIdentifier(pictureInPictureController)
            let completion = PictureInPictureRestoreCompletion(completionHandler)
            Task { @MainActor [weak self] in
                guard let self, let current = self.pictureInPictureController,
                      ObjectIdentifier(current) == controllerID else {
                    completion(false)
                    return
                }
                self.onPictureInPictureRestoreUI()
                completion(self.view != nil)
            }
        }
        #else
        func updatePictureInPictureCallbacks(
            possible: @escaping @MainActor (Bool) -> Void,
            active: @escaping @MainActor (Bool) -> Void,
            continuation: @escaping @MainActor (Bool) -> Void,
            restoreUI: @escaping @MainActor () -> Void
        ) { }

        func handlePictureInPictureStartRequest(_ request: Int) { }
        #endif

        #if os(tvOS)
        private func rendererWindowDidChange() {
            guard let view else { return }
            if displayWindow !== view.window {
                if let recognizer = videoTapRecognizer { recognizer.view?.removeGestureRecognizer(recognizer) }
                videoTapRecognizer = nil
                if let window = view.window {
                    let recognizer = UITapGestureRecognizer(target: self, action: #selector(videoTapped))
                    // A light touchpad tap toggles chrome; a Select press still activates the focused control.
                    recognizer.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.indirect.rawValue)]
                    recognizer.allowedPressTypes = []
                    recognizer.cancelsTouchesInView = false
                    recognizer.delegate = self
                    window.addGestureRecognizer(recognizer)
                    videoTapRecognizer = recognizer
                }
                displayCriteriaTask?.cancel()
                displayCriteriaTask = nil
                releaseDisplayCriteria()
                displayWindow = view.window
                displayCriteriaLeaseLost = false
                setDisplayCriteriaItem(player?.currentItem, force: true)
            }
        }

        @objc private func videoTapped() { onVideoTap() }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }

        private func setDisplayCriteriaItem(_ item: AVPlayerItem?, force: Bool = false) {
            guard force || criteriaItem !== item else { return }
            criteriaItem = item
            displayCriteriaTask?.cancel()
            displayCriteriaTask = nil
            guard let item else {
                releaseDisplayCriteria()
                displayCriteriaLeaseLost = false
                return
            }
            guard !displayCriteriaLeaseLost, let player, player.currentItem === item,
                  let view, let window = view.window else { return }
            displayWindow = window
            displayCriteriaTask = Task { [weak self, weak player, weak window] in
                do {
                    let criteria = try await item.asset.load(.preferredDisplayCriteria)
                    guard !Task.isCancelled, let self, let player, let window,
                          self.player === player, player.currentItem === item,
                          self.criteriaItem === item, self.view?.window === window else { return }
                    self.installDisplayCriteria(criteria, in: window)
                } catch is CancellationError {
                    return
                } catch {
                    guard !Task.isCancelled, let self, let player, let window,
                          self.player === player, player.currentItem === item,
                          self.criteriaItem === item, self.view?.window === window else { return }
                    self.releaseDisplayCriteria()
                    return
                }
            }
        }

        private func installDisplayCriteria(_ criteria: AVDisplayCriteria, in window: UIWindow) {
            let manager = window.avDisplayManager
            if displayWindow !== window {
                releaseDisplayCriteria()
                displayWindow = window
            }
            if let installedDisplayCriteria {
                guard let currentCriteria = manager.preferredDisplayCriteria,
                      currentCriteria === installedDisplayCriteria else {
                    previousDisplayCriteria = nil
                    self.installedDisplayCriteria = nil
                    displayCriteriaLeaseLost = true
                    return
                }
            } else {
                previousDisplayCriteria = manager.preferredDisplayCriteria
            }
            manager.preferredDisplayCriteria = criteria
            // Read back the manager-owned copy so cleanup can leave any newer
            // display request alone rather than overwriting it with a stale value.
            guard let installed = manager.preferredDisplayCriteria else {
                previousDisplayCriteria = nil
                displayCriteriaLeaseLost = true
                return
            }
            installedDisplayCriteria = installed
        }

        private func releaseDisplayCriteria() {
            defer {
                previousDisplayCriteria = nil
                installedDisplayCriteria = nil
            }
            guard let window = displayWindow, let installedDisplayCriteria,
                  let currentCriteria = window.avDisplayManager.preferredDisplayCriteria,
                  currentCriteria === installedDisplayCriteria else { return }
            window.avDisplayManager.preferredDisplayCriteria = previousDisplayCriteria
        }
        #endif
    }
}

#if os(iOS)
private final class PictureInPictureRestoreCompletion: @unchecked Sendable {
    private let completion: (Bool) -> Void

    init(_ completion: @escaping (Bool) -> Void) {
        self.completion = completion
    }

    func callAsFunction(_ restored: Bool) {
        completion(restored)
    }
}

extension NativePlayerLayer.Coordinator: AVPictureInPictureControllerDelegate { }
#endif

#if LUMINA_UI_TESTING
// Aggregate hot callbacks and cap detailed output so diagnosis cannot become a log storm.
@MainActor
private final class PlaybackContinuityTrace {
    static let enabled = ProcessInfo.processInfo.environment["LUMINA_PLAYBACK_TRACE"] == "1"
    private var counts: [String: Int] = [:]
    private var windowStart = ProcessInfo.processInfo.systemUptime
    private var detailedEvents = 0

    func record(_ event: String, state: @autoclosure () -> String) {
        guard Self.enabled else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if now - windowStart >= 1 {
            let totals = counts.keys.sorted().map { "\($0):\(counts[$0]!)" }.joined(separator: ",")
            write("wall=\(now) interval=\(now - windowStart) counts=\(totals) \(state())")
            counts.removeAll(keepingCapacity: true)
            detailedEvents = 0
            windowStart = now
        }
        let key = String(event.split(separator: "=", maxSplits: 1)[0])
        counts[key, default: 0] += 1
        if detailedEvents < 20 {
            write("wall=\(now) event=\(event) \(state())")
            detailedEvents += 1
        }
    }

    private func write(_ line: String) {
        FileHandle.standardError.write(Data("[LUMINA_CONTINUITY] \(line)\n".utf8))
    }
}
#endif

private final class PlayerLayerView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    var didMoveToWindowHandler: (() -> Void)?
    #if LUMINA_UI_TESTING
    private let continuityTrace = PlaybackContinuityTrace()
    #endif
    private var readyObservation: NSKeyValueObservation?

    override init(frame: CGRect) {
        super.init(frame: frame)
        observeReadiness()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        observeReadiness()
    }

    private func observeReadiness() {
        readyObservation = playerLayer.observe(\.isReadyForDisplay, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.traceRendering("render-ready-change") }
        }
    }

    func traceRendering(_ event: String) {
        if PlaybackDiagnostics.shared.isCapturing {
            let kind: PlaybackDiagnosticEvent?
            switch event {
            case "view-attached": kind = .rendererCreated
            case "view-dismantle": kind = .rendererDetached
            case "layer-window-change": kind = .rendererWindowChanged
            case "layer-layout", "view-update": kind = .rendererLayout
            case "render-ready-change": kind = .rendererReadyChanged
            default: kind = nil
            }
            if let kind {
                var sample = PlaybackDiagnosticSample()
                let player = playerLayer.player
                sample.playerID = PlaybackDiagnostics.identity(player)
                sample.itemID = PlaybackDiagnostics.identity(player?.currentItem)
                sample.positionSeconds = player?.currentTime().seconds
                sample.rate = player.map { Double($0.rate) }
                sample.timeControlStatus = player?.timeControlStatus.rawValue
                sample.width = Double(bounds.width)
                sample.height = Double(bounds.height)
                sample.displayReady = playerLayer.isReadyForDisplay
                sample.windowID = PlaybackDiagnostics.identity(window)
                sample.viewID = PlaybackDiagnostics.identity(self)
                sample.layerID = PlaybackDiagnostics.identity(playerLayer)
                PlaybackDiagnostics.shared.record(kind, sample: sample)
            }
        }
        #if LUMINA_UI_TESTING
        guard PlaybackContinuityTrace.enabled else { return }
        continuityTrace.record(event, state: "view=\(ObjectIdentifier(self)) layer=\(ObjectIdentifier(playerLayer)) player=\(playerLayer.player.map { String(describing: ObjectIdentifier($0)) } ?? "nil") window=\(window.map { String(describing: ObjectIdentifier($0)) } ?? "nil") bounds=\(bounds) ready=\(playerLayer.isReadyForDisplay) hidden=\(isHidden) opacity=\(layer.opacity)")
        #endif
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        traceRendering("layer-layout")
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        traceRendering("layer-window-change")
        didMoveToWindowHandler?()
    }
}

#if os(iOS)
private struct VideoRoutePicker: View {
    var active = false

    var body: some View {
        ZStack {
            NativeAirPlayRoutePicker()
            LuminaIcon(glyph: .airplay, size: 24)
                .foregroundStyle(active ? LuminaTheme.brand : .white)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
    }
}

private struct NativeAirPlayRoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.tintColor = .clear
        picker.activeTintColor = .clear
        picker.prioritizesVideoDevices = true
        picker.accessibilityLabel = "AirPlay"
        picker.accessibilityIdentifier = "videoAirPlayButton"
        return picker
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) { }
}

#endif

@MainActor
private func configureDeviceAudioLanguage(_ player: AVPlayer, preferences: NativePlaybackPreferences) {
    guard !preferences.preferredAudioLanguages.isEmpty else { return }
    let criteria = AVPlayerMediaSelectionCriteria(preferredLanguages: preferences.preferredAudioLanguages,
                                                 preferredMediaCharacteristics: nil)
    player.setMediaSelectionCriteria(criteria, forMediaCharacteristic: .audible)
}
