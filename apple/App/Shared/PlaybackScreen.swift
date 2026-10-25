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
private struct NativePlaybackPresentation: ViewModifier {
    @Binding var request: PlaybackRequest?
    let store: LuminaStore
    let onDismiss: () -> Void

    private var audioPresented: Binding<Bool> {
        Binding(get: { request?.item.isAudio == true }, set: { if !$0, request?.item.isAudio == true { request = nil } })
    }
    private var videoRequest: Binding<PlaybackRequest?> {
        Binding(get: { request?.item.isAudio == false ? request : nil }, set: { value in
            if request?.item.isAudio != true { request = value }
        })
    }

    func body(content: Content) -> some View {
        content.navigationDestination(isPresented: audioPresented) {
            if let request, request.item.isAudio { PlaybackScreen(request: request, store: store) }
        }
        .onChange(of: audioPresented.wrappedValue) { _, presented in if !presented { onDismiss() } }
        .fullScreenCover(item: videoRequest, onDismiss: onDismiss) { request in
            PlaybackScreen(request: request, store: store)
        }
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
    @State private var audioTimelineVisible = true
    @State private var audioIsScrubbing = false
    @State private var audioTimelineHideTask: Task<Void, Never>?
    #endif
    private let store: LuminaStore

    init(request: PlaybackRequest, store: LuminaStore, queue: [MediaItem] = [], queueIndex: Int? = nil) {
        self.store = store
        _requestedPlayback = State(initialValue: PlaybackModel(request: request, store: store, queue: queue, queueIndex: queueIndex))
    }

    init(playback: PlaybackModel, store: LuminaStore) {
        self.store = store
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
                        Label("No playable track", systemImage: "music.note")
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
                    Button("Queue", systemImage: "list.bullet") { queuePresented = true }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("audioQueueButton")
                    audioPlayPauseButton
                    audioRepeatButton.labelStyle(.iconOnly)
                    Button("Stop", systemImage: "stop.fill") { stopAudio() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("audioStopButton")
                }
            }
        }
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarVisibility(requestedPlayback.request.item.isAudio ? .visible : .hidden, for: .navigationBar)
        .toolbarVisibility(.hidden, for: .tabBar)
        .statusBarHidden(!requestedPlayback.request.item.isAudio && !videoSystemOverlaysVisible)
        .persistentSystemOverlays(requestedPlayback.request.item.isAudio || videoSystemOverlaysVisible ? .visible : .hidden)
        #endif
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("playbackScreen")
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
                onDone: { videoSettingsPresented = false }
            )
            .background(LuminaTheme.theater)
            .preferredColorScheme(.dark)
            .presentationDetents([.medium, .large])
        }
        #endif
        .task {
            guard !startedPresentation else { return }
            startedPresentation = true
            let initial = requestedPlayback
            initial.setActive(scenePhase == .active)
            if initial.request.item.isAudio {
                do { try await audioSession.adoptForPlayback(initial) }
                catch { presentationError = error.localizedDescription }
                attemptedAudioAdoption = true
            } else {
                audioSession.stop()
                await initial.start()
            }
        }
        .task(id: playback.request.id) {
            await playback.refreshVideoActions()
        }
        .onChange(of: scenePhase) { _, phase in
            playback.setActive(phase == .active)
        }
        .onChange(of: playback.isPlaying) { _, isPlaying in
            guard !playback.request.item.isAudio else { return }
            if isPlaying { revealVideoChrome() }
            else {
                videoChromeHideTask?.cancel()
                videoChromeVisible = true
            }
        }
        .onChange(of: focusedVideoControl) { _, control in
            if control != nil { revealVideoChrome() }
        }
        .onChange(of: playback.hasFinished) { _, finished in
            guard !playback.request.item.isAudio, finished else { return }
            videoChromeHideTask?.cancel()
            videoChromeVisible = true
        }
        .onReceive(playback.player.publisher(for: \.currentItem, options: [.initial, .new])) { item in
            guard !playback.request.item.isAudio else { return }
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
            if !isPresented { revealVideoChrome() }
        }
        .onDisappear {
            videoChromeHideTask?.cancel()
            videoTracksLoadTask?.cancel()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            playback.setActive(false)
        }
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
                                    Image(systemName: "music.note").font(.largeTitle)
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
            ScrollView {
                VStack(spacing: 0) {
                    ZStack(alignment: .bottom) {
                        GeometryReader { artwork in
                            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 2000)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Color.black.overlay {
                                    Image(systemName: "music.note").font(.largeTitle)
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
                                Label(error, systemImage: "exclamationmark.triangle")
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
                if let division {
                    videoTransport
                        .padding(.top, division.maxY)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                } else {
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
                    if !videoSettingsVisible && (videoChromeVisible || displayedError != nil || playback.hasFinished) {
                        videoOverlay
                            #if os(iOS)
                            .padding(.top, safeArea.top)
                            .padding(.bottom, safeArea.bottom)
                            .padding(.horizontal, max(safeArea.leading, safeArea.trailing))
                            #endif
                            .background { videoScrim.allowsHitTesting(false) }
                            .transition(.opacity)
                    }
                    if !videoSettingsVisible, !videoSettingsPresented, displayedError == nil {
                        videoContextActions(showNext: playback.activeCredits != nil || playback.hasFinished)
                            .padding(.horizontal, videoHorizontalInset)
                            #if os(tvOS)
                            .padding(.bottom, 260)
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                            .onMoveCommand { direction in
                                if direction == .down { focusedVideoControl = "videoPlayPauseButton" }
                                if direction == .up { focusedVideoControl = "videoSettingsButton" }
                            }
                            #else
                            .padding(.top, videoVerticalInset + 80)
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
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
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
            onTearDown: { [playback] in playback.close() },
            onVideoTap: toggleVideoChrome
        )
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .accessibilityIdentifier("nativeVideoPlayer")
    }

    private var videoTransport: some View {
        ScrollView {
            VStack(spacing: 14) {
                Capsule()
                    .fill(.white.opacity(0.25))
                    .frame(width: 38, height: 5)
                    .accessibilityHidden(true)
                HStack(spacing: 12) {
                    Button("Close player", systemImage: "xmark") { finish() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("closePlayerButton")
                    Spacer(minLength: 0)
                    #if os(iOS)
                    VideoRoutePicker()
                        .frame(width: 44, height: 44)
                    #endif
                    Button("Playback settings", systemImage: "captions.bubble") { presentVideoSettings() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSettingsButton")
                    Button(isMuted ? "Unmute" : "Mute", systemImage: isMuted ? "speaker.slash.fill" : "speaker.wave.2.fill") {
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
                    Slider(value: Binding(get: { playback.elapsedSeconds }, set: { playback.seek(to: $0) }),
                           in: 0...max(1, playback.request.item.durationSeconds))
                        .tint(.white)
                        .accessibilityLabel("Video position")
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
                    Button("Back 10 seconds", systemImage: "gobackward.10") {
                        playback.seek(to: playback.elapsedSeconds - 10)
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSeekBackwardButton")

                    Button(playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play",
                           systemImage: playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
                        if playback.hasFinished { replay() } else { playback.togglePlayback() }
                    }
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.circle)
                    .tint(.white)
                    .foregroundStyle(.black)
                    .accessibilityIdentifier(playback.hasFinished ? "replayButton" : "videoPlayPauseButton")
                    .disabled(playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))

                    Button("Forward 10 seconds", systemImage: "goforward.10") {
                        playback.seek(to: playback.elapsedSeconds + 10)
                    }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSeekForwardButton")
                }
                .controlSize(.large)
                .buttonStyle(LuminaQuietActionStyle())
                .frame(maxWidth: .infinity)
                .overlay(alignment: .trailing) { nextEpisodeTransport }
                videoContextActions(showNext: playback.activeCredits != nil || playback.hasFinished)
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
            Button("Next episode", systemImage: "forward.end.fill") {
                Task { await playback.playNextEpisode(); revealVideoChrome() }
            }
            .labelStyle(.iconOnly)
            .accessibilityHint(episode.name)
            .accessibilityIdentifier("nextEpisodeTransportButton")
            .focused($focusedVideoControl, equals: "nextEpisodeTransportButton")
            .disabled(!playback.canUseVideoActions)
        }
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
                Button("Next episode", systemImage: "forward.end.fill") {
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
            item.selectMediaOptionAutomatically(in: group)
            videoTracks.markSubtitlesAutomatic()
        case .off:
            guard group.allowsEmptySelection else { return }
            playback.subtitlesExplicitlyOff = true
            item.select(nil, in: group)
            videoTracks.markSubtitlesOff()
        case let .option(index):
            guard videoTracks.subtitleOptions.indices.contains(index) else { return }
            playback.subtitlesExplicitlyOff = false
            item.select(videoTracks.subtitleOptions[index], in: group)
            videoTracks.selectSubtitle(index)
        }
    }

    @ViewBuilder private var videoOverlay: some View {
        VStack(spacing: 24) {
            HStack(spacing: 12) {
                Button("Close player", systemImage: "xmark") { finish() }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("closePlayerButton")
                    .focused($focusedVideoControl, equals: "closePlayerButton")
                Spacer(minLength: 0)
                #if os(iOS)
                VideoRoutePicker().frame(width: 44, height: 44)
                #endif
                Button("Playback settings", systemImage: "captions.bubble") { presentVideoSettings() }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("videoSettingsButton")
                    .focused($focusedVideoControl, equals: "videoSettingsButton")
                Button(isMuted ? "Unmute" : "Mute", systemImage: isMuted ? "speaker.slash.fill" : "speaker.wave.2.fill") {
                    isMuted.toggle()
                    playback.player.isMuted = isMuted
                }
                .labelStyle(.iconOnly)
                .accessibilityIdentifier("videoMuteButton")
                    .focused($focusedVideoControl, equals: "videoMuteButton")
            }
            .buttonStyle(LuminaQuietActionStyle())
            .controlSize(.large)
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
                Slider(value: Binding(get: { playback.elapsedSeconds }, set: { playback.seek(to: $0) }), in: 0...max(1, playback.request.item.durationSeconds), onEditingChanged: { editing in
                    videoIsScrubbing = editing
                    revealVideoChrome()
                })
                    .tint(.white)
                    .accessibilityLabel("Video position")
                #endif
                HStack {
                    Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                    Spacer()
                    Text("−" + Duration.seconds(max(0, playback.request.item.durationSeconds - playback.elapsedSeconds)).formatted(.time(pattern: .minuteSecond)))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.white.opacity(0.72))
                HStack(spacing: 28) {
                    Button("Back 10 seconds", systemImage: "gobackward.10") { playback.seek(to: playback.elapsedSeconds - 10); revealVideoChrome() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSeekBackwardButton")
                    .focused($focusedVideoControl, equals: "videoSeekBackwardButton")
                    Button(playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play", systemImage: playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
                        if playback.hasFinished { replay() } else { playback.togglePlayback() }
                        revealVideoChrome()
                    }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.circle)
                    .tint(.white)
                    .foregroundStyle(.black)
                    .accessibilityIdentifier(playback.hasFinished ? "replayButton" : "videoPlayPauseButton")
                    .focused($focusedVideoControl, equals: "videoPlayPauseButton")
                    .disabled(playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
                    Button("Forward 10 seconds", systemImage: "goforward.10") { playback.seek(to: playback.elapsedSeconds + 10); revealVideoChrome() }
                        .labelStyle(.iconOnly)
                        .accessibilityIdentifier("videoSeekForwardButton")
                    .focused($focusedVideoControl, equals: "videoSeekForwardButton")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .controlSize(.large)
                .frame(maxWidth: .infinity)
                .overlay(alignment: .trailing) { nextEpisodeTransport }
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
    }

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
        Color.clear
            .frame(width: 1, height: 1)
            .accessibilityElement()
            .accessibilityLabel("Playback seconds")
            .accessibilityIdentifier("playbackTime")
            .accessibilityValue(String(Int(playback.elapsedSeconds)))
    }

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
    #if os(tvOS)
    private var tvAudioControlBand: some View {
        VStack(spacing: 18) {
            Button("Queue", systemImage: "list.bullet") { queuePresented = true }
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
            Button("Stop", systemImage: "stop.fill") { stopAudio() }
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
                audioIsScrubbing = editing
                revealAudioTimeline()
            })
                .tint(LuminaTheme.brand)
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

    private var audioPlayPauseButton: some View {
        Button(playback.errorMessage != nil ? "Retry" : playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play", systemImage: playback.errorMessage != nil ? "arrow.clockwise" : playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
            if playback.hasFinished { replay() } else { playback.togglePlayback() }
        }
        .labelStyle(.iconOnly)
        .accessibilityIdentifier("audioPlayPauseButton")
        .disabled(playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
    }

    private var audioRepeatButton: some View {
        Button(playback.repeatMode.label, systemImage: playback.repeatMode.symbol) { playback.cycleRepeat() }
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
                        Image(systemName: index == playback.queueIndex ? "speaker.wave.2.fill" : "music.note")
                        Text(item.name).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .disabled(playback.isReplaying || audioSession.isSonos)
                .accessibilityIdentifier(row.accessibilityID)
                if index > playback.queueIndex {
                    Menu("Track actions", systemImage: "ellipsis") {
                        Button("Play next") { playback.moveOccurrence(row.id) }
                        Button("Move up") { playback.moveOccurrence(row.id, offset: -1) }
                            .disabled(index == playback.queueIndex + 1)
                        Button("Move down") { playback.moveOccurrence(row.id, offset: 1) }
                            .disabled(index == playback.queue.count - 1)
                        Button("Remove from queue", role: .destructive) { playback.removeOccurrence(row.id) }
                    }
                    .labelStyle(.iconOnly)
                    .disabled(audioSession.isSonos)
                }
            }
        }
    }

    private func finish() {
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
    #if LUMINA_UI_TESTING
    private let lifecycleToken = UUID().uuidString
    #endif
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
    private var videoSource = NativeVideoSource()
    private(set) var isSwitchingVideoStream = false
    private(set) var videoStreamError: String?
    private(set) var sourceAudioIsAutomatic = true
    private(set) var attachedVideoSubtitle: PlaybackStream?
    private(set) var videoQuality: PlaybackQuality = .automatic
    private(set) var subtitleDelaySeconds: Double = 0
    private(set) var videoSpeed: Float = 1
    private(set) var isSeekingChapter = false
    private(set) var playbackMarkers: [PlaybackMarker] = []
    private(set) var nextEpisode: MediaItem?
    private(set) var videoActionsError: String?
    private var nextEpisodeAutoplay: Bool?
    private var pendingVideoSource: NativeVideoSource?
    private var pendingVideoPlayer: AVPlayer?
    private var episodeTask: Task<Void, Never>?
    private var actionsGeneration = UUID()
    var canUseVideoActions: Bool { didSeek && !closed && !isSwitchingVideoStream && !isSeekingChapter && !isReplaying }
    var canAdjustSubtitleTiming: Bool { request.usesClientDemux || attachedVideoSubtitle != nil }
    var subtitlesExplicitlyOff = false
    private var isCommittingVideoStream = false
    private var reportQueue: Task<Void, Never>?
    private var pendingReports: [(event: PlaybackEvent, request: PlaybackRequest, seconds: Double, paused: Bool)] = []
    private var didSeek = false
    private var started = false
    private var autoplayWhenReady = true
    private var isActive = true
    private var closed = false
    private var didReportCompletion = false
    private var lastReport: Double = -1
    private var remoteTargets: [(command: MPRemoteCommand, target: Any)] = []

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
        videoQuality = request.resolvedQuality
        subtitlesExplicitlyOff = request.subtitleStreamIndex == -1
        let initialSubtitle = request.mediaStreams.first {
            $0.index == request.subtitleStreamIndex && $0.hasExternalSubtitle
        }
        attachedVideoSubtitle = initialSubtitle
        elapsedSeconds = request.startPositionSeconds
        player = request.item.isAudio || request.usesClientDemux || initialSubtitle != nil
            ? AVPlayer() : AVPlayer(url: request.url)
        if !request.item.isAudio {
            configureDeviceAudioLanguage(player, preferences: store.playbackPreferences)
        }
        player.preventsDisplaySleepDuringVideoPlayback = true
        if !request.item.isAudio { player.audiovisualBackgroundPlaybackPolicy = .pauses }
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE playback-model init \(lifecycleToken)")
        #endif
    }

    deinit {
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE playback-model deinit \(lifecycleToken)")
        #endif
    }

    func start() async {
        guard !started, !closed else { return }
        if request.item.isAudio, let audioOwner {
            started = true
            player.replaceCurrentItem(with: nil)
            installRemoteControls()
            await audioOwner.start(self)
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: request.item.isAudio ? .default : .moviePlayback)
        } catch {
            errorMessage = "Audio playback could not be configured."
            return
        }
        started = true
        if request.item.isAudio { installRemoteControls() }
        defer {
            if !didSeek { started = false; videoSource.stop() }
        }
        if request.usesClientDemux || attachedVideoSubtitle != nil {
            do {
                let item = try await prepareVideoItem(request, source: videoSource, externalSubtitle: attachedVideoSubtitle)
                guard !closed, !Task.isCancelled else { videoSource.stop(); return }
                player.replaceCurrentItem(with: item)
            } catch {
                videoSource.stop()
                errorMessage = "This video could not be played. Try Convert to compatible video."
                return
            }
        }
        // Do not report a zero position while the item is loading or seeking to resume.
        guard let item = player.currentItem else { return }
        do {
            try await NativeVideoSource.waitUntilReady(player)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = item.status == .failed
                ? (request.item.isAudio ? "This track could not be played. Check its format and try again." : "This video could not be played. Try Convert to compatible video.")
                : "The media did not become ready. Check the server connection."
            return
        }
        guard !closed, !Task.isCancelled else { return }
        if request.startPositionSeconds > 0 {
            await player.seek(to: CMTime(seconds: request.startPositionSeconds, preferredTimescale: 600),
                              toleranceBefore: .zero, toleranceAfter: .zero)
        }
        guard !closed, !Task.isCancelled else { return }
        if !request.item.isAudio {
            item.audioTimePitchAlgorithm = .spectral
            if videoSpeed < 1, !item.canPlaySlowForward { videoSpeed = 1 }
            player.defaultRate = videoSpeed
        }
        didSeek = true
        updatePosition()
        enqueue(.start, paused: !autoplayWhenReady)
        let progressInterval = CMTime(seconds: 1, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: progressInterval, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.updatePosition()
                let shouldReport = abs(self.elapsedSeconds - self.lastReport) >= NativeVideoSource.progressObservationInterval.seconds
                if shouldReport {
                    self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
                }
                if self.player.currentItem?.status == .failed {
                    self.errorMessage = "Playback stopped. Check your connection and try again."
                }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.isPlaying = self.player.timeControlStatus == .playing
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        installEndObserver()
        if autoplayWhenReady { player.play() }
    }

    func pause() {
        guard !closed else { return }
        if let audioOwner { audioOwner.pause(); return }
        if nextEpisodeAutoplay != nil { nextEpisodeAutoplay = false }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        player.pause()
        isPlaying = false
        guard !hasFinished, !isCommittingVideoStream else { return }
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func togglePlayback() {
        if let audioOwner { Task { await audioOwner.toggle() }; return }
        guard !closed else { return }
        if let nextEpisodeAutoplay {
            if nextEpisodeAutoplay { pause() } else { self.nextEpisodeAutoplay = true }
            return
        }
        guard didSeek, !closed, !hasFinished else { return }
        if isCommittingVideoStream { autoplayWhenReady.toggle(); return }
        if player.timeControlStatus == .playing { pause() }
        else { autoplayWhenReady = true; player.play(); isPlaying = true }
    }

    func setActive(_ active: Bool) {
        guard isActive != active else { return }
        isActive = active
        // Video remains paused when returning; AVKit must wait for a new Play action.
        if !request.item.isAudio { pause() }
    }

    func seek(to seconds: Double) {
        if let audioOwner { Task { await audioOwner.seek(seconds) }; return }
        guard didSeek, !closed, !hasFinished, !isCommittingVideoStream else { return }
        elapsedSeconds = min(max(0, seconds), request.item.durationSeconds)
        player.seek(to: CMTime(seconds: elapsedSeconds, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }

    func supportsVideoSpeed(_ rate: Float) -> Bool {
        guard !request.item.isAudio, let item = player.currentItem, item.status == .readyToPlay else { return false }
        return VideoPlaybackSpeed.allCases.contains(where: { $0.rawValue == rate })
            && (rate >= 1 || item.canPlaySlowForward)
    }

    func selectVideoSpeed(_ rate: Float) {
        guard !closed, !hasFinished, !isSwitchingVideoStream, supportsVideoSpeed(rate) else { return }
        videoSpeed = rate
        player.defaultRate = rate
        // Changing speed while paused must not resume playback.
        if autoplayWhenReady, isActive, player.timeControlStatus != .paused { player.play() }
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
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await player.seek(to: CMTime(seconds: marker.endSeconds, preferredTimescale: 600),
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
        guard episodeTask == nil, canUseVideoActions else { return }
        let task = Task { await advanceToNextEpisode() }
        episodeTask = task
        await task.value
        episodeTask = nil
    }

    private func advanceToNextEpisode() async {
        guard canUseVideoActions, !request.item.isAudio, let episode = nextEpisode else { return }
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
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed, request.id == previousRequest.id else { throw CancellationError() }
            let item = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: nil, subtitleDelay: 0)
            // A player item cannot be transferred between AVPlayers. Preflight a distinct
            // item sharing the asset, leaving the configured item for the visible player.
            candidatePlayer.replaceCurrentItem(with: AVPlayerItem(asset: item.asset))
            try await NativeVideoSource.waitUntilReady(candidatePlayer)
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
            player.pause()
            candidatePlayer.replaceCurrentItem(with: nil)
            player.replaceCurrentItem(with: item)
            installed = true
            try await NativeVideoSource.waitUntilReady(player)
            guard await player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero), item.status == .readyToPlay else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            if !didReportCompletion { enqueue(.stop, paused: true, for: previousRequest, position: previousPosition) }
            request = next
            videoSource = candidateSource
            attachedVideoSubtitle = next.mediaStreams.first {
                $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle
            }
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
            if videoSpeed < 1, !item.canPlaySlowForward { videoSpeed = 1 }
            player.defaultRate = videoSpeed
            installEndObserver()
            isCommittingVideoStream = false
            autoplayWhenReady = nextEpisodeAutoplay == true && isActive
            enqueue(.start, paused: !autoplayWhenReady)
            if autoplayWhenReady { player.play() }
            isPlaying = autoplayWhenReady
            previousSource.stop()
        } catch {
            candidatePlayer.replaceCurrentItem(with: nil)
            if installed, !closed {
                if !hasFinished, let previousItem {
                    player.replaceCurrentItem(with: previousItem)
                    _ = await player.seek(to: CMTime(seconds: previousPosition, preferredTimescale: 600),
                                          toleranceBefore: .zero, toleranceAfter: .zero)
                    if autoplayWhenReady && isActive { player.play() }
                } else {
                    player.replaceCurrentItem(with: nil)
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
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await player.seek(to: CMTime(seconds: chapter.startPositionSeconds, preferredTimescale: 600),
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
            preferredAudioStreamIndex: next.audioStreamIndex,
            preferredSubtitleStreamIndex: externalSubtitle == nil
                ? next.subtitleStreamIndex.flatMap { $0 >= 0 ? $0 : nil } : nil,
            externalSubtitles: subtitles, bandwidth: next.bitRate,
            subtitleDelaySeconds: subtitleDelay ?? subtitleDelaySeconds
        )
        item.audioTimePitchAlgorithm = .spectral
        return item
    }

    private func replaceVideoStream(audioIndex: Int?, externalSubtitle: PlaybackStream?,
                                    selectsExternalSubtitle: Bool, changesAudio: Bool,
                                    subtitleDelay: Double? = nil, quality: PlaybackQuality? = nil) async {
        guard !request.item.isAudio, didSeek, !closed, !hasFinished, !isSwitchingVideoStream, !isSeekingChapter,
              let previousItem = player.currentItem else { return }
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
        var commitPosition = elapsedSeconds
        do {
            updatePosition()
            let next = try await store.preparePlayback(
                for: request.item,
                forceTranscode: (quality == nil && request.isTranscoding)
                    || (externalSubtitle != nil && !request.usesClientDemux)
                    || (audioIndex != nil && !request.usesClientDemux),
                quality: quality ?? videoQuality,
                audioStreamIndex: audioIndex,
                subtitleStreamIndex: externalSubtitle != nil || subtitlesExplicitlyOff ? -1 : request.subtitleStreamIndex,
                mediaSourceID: request.mediaSourceID, startPositionSeconds: elapsedSeconds
            )
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
                    guard let matching = nextGroup.mediaSelectionOption(withPropertyList: selected.propertyList())
                            ?? (equivalents.count == 1 ? equivalents.first : nil) else {
                        throw POSIXError(.ENOTSUP)
                    }
                    nextItem.select(matching, in: nextGroup)
                } else if nextGroup.allowsEmptySelection {
                    nextItem.select(nil, in: nextGroup)
                } else {
                    throw POSIXError(.ENOTSUP)
                }
            }
            try Task.checkCancellation()
            guard !closed, player.currentItem === previousItem else { throw CancellationError() }
            updatePosition()
            commitPosition = elapsedSeconds
            var position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            guard await candidatePlayer.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero) else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed, !hasFinished, player.currentItem === previousItem else { throw CancellationError() }
            updatePosition()
            commitPosition = elapsedSeconds
            position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            // AVPlayer stays stable across rotations and Duo poses. Only its item changes.
            isCommittingVideoStream = true
            player.pause()
            candidatePlayer.replaceCurrentItem(with: nil)
            player.replaceCurrentItem(with: nextItem)
            replacementInstalled = true
            try await NativeVideoSource.waitUntilReady(player)
            guard await player.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero),
                  nextItem.status == .readyToPlay else { throw POSIXError(.EIO) }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            elapsedSeconds = commitPosition
            enqueue(.stop, paused: true)
            request = next
            videoSource = candidateSource
            if videoSpeed < 1, !nextItem.canPlaySlowForward { videoSpeed = 1 }
            player.defaultRate = videoSpeed
            if changesAudio { sourceAudioIsAutomatic = audioIndex == nil }
            attachedVideoSubtitle = externalSubtitle
            if let quality { videoQuality = quality }
            if let subtitleDelay { subtitleDelaySeconds = subtitleDelay }
            if selectsExternalSubtitle { subtitlesExplicitlyOff = false }
            installEndObserver()
            lastReport = -1
            isCommittingVideoStream = false
            let resume = autoplayWhenReady && isActive
            enqueue(.start, paused: !resume)
            if resume { player.play() }
            isPlaying = resume
            previousSource.stop()
        } catch {
            candidatePlayer.replaceCurrentItem(with: nil)
            if replacementInstalled, !closed {
                player.replaceCurrentItem(with: previousItem)
                _ = await player.seek(to: CMTime(seconds: commitPosition, preferredTimescale: 600),
                                      toleranceBefore: .zero, toleranceAfter: .zero)
                if autoplayWhenReady && isActive { player.play() }
                isPlaying = player.timeControlStatus == .playing
            }
            candidateSource.stop()
            isCommittingVideoStream = false
            if !closed { updatePosition() }
            if let candidateRequest {
                enqueue(.stop, paused: true, for: candidateRequest, position: elapsedSeconds)
            }
            guard !closed else { return }
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
        isReplaying = true
        defer { isReplaying = false }
        do {
            let next = try await store.preparePlayback(for: queue[nextIndex])
            guard !closed, !Task.isCancelled else { return }
            updatePosition()
            if !didReportCompletion { enqueue(.stop, paused: true) }
            player.pause()
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
            player.replaceCurrentItem(with: AVPlayerItem(url: next.url))
            await start()
        } catch {
            errorMessage = "The next track could not start. Check your connection and try again."
        }
    }

    func replay(startPositionSeconds: Double = 0, initiallyPaused: Bool = false) async {
        if let audioOwner { await audioOwner.select(queueIndex); return }
        guard hasFinished, !closed, !isReplaying, !isSwitchingVideoStream else { return }
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
            player.replaceCurrentItem(with: item)
        } catch {
            failReplay("Replay could not start. Try Convert to compatible video.")
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
        if videoSpeed < 1, player.currentItem?.canPlaySlowForward != true { videoSpeed = 1 }
        player.defaultRate = videoSpeed
        guard await player.seek(to: CMTime(seconds: newRequest.startPositionSeconds, preferredTimescale: 600),
                                toleranceBefore: .zero, toleranceAfter: .zero) else {
            failReplay("Playback could not seek to the requested position. Try again.")
            return
        }
        guard !closed, !Task.isCancelled else { return }
        updatePosition()
        hasFinished = false
        enqueue(.start, paused: !autoplayWhenReady)
        if autoplayWhenReady { player.play() }
    }

    func close() {
        guard !closed else { return }
        closed = true
        episodeTask?.cancel()
        episodeTask = nil
        pendingVideoPlayer?.replaceCurrentItem(with: nil)
        pendingVideoSource?.stop()
        pendingVideoPlayer = nil
        pendingVideoSource = nil
        videoSource.stop()
        if !isCommittingVideoStream { updatePosition() }
        audioTransport?.stop()
        audioTransport = nil
        player.pause()
        isPlaying = false
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        if !didReportCompletion && (!request.item.isAudio || reportedAudioStart) {
            enqueue(.stop, paused: true)
        }
        player.replaceCurrentItem(with: nil)
        if request.item.isAudio {
            for entry in remoteTargets { entry.command.removeTarget(entry.target) }
            remoteTargets.removeAll()
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        }
    }

    private func finishPlayback() {
        guard !closed, !hasFinished else { return }
        updatePosition()
        hasFinished = true
        videoSource.stop()
        autoplayWhenReady = false
        player.pause()
        isPlaying = false
        // The stopped server session is no longer a valid replay source (especially for HLS).
        // Remove it so AVKit can't restart the obsolete item behind the fresh Replay action.
        player.replaceCurrentItem(with: nil)
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
        videoSource.stop()
        errorMessage = message
        player.replaceCurrentItem(with: nil)
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
        let seconds = player.currentTime().seconds
        if didSeek, seconds.isFinite, seconds >= 0, player.currentItem?.status == .readyToPlay {
            elapsedSeconds = seconds
        }
        updateNowPlaying()
    }

    private func updateNowPlaying() {
        guard request.item.isAudio, !closed else { return }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: request.item.name,
            MPMediaItemPropertyArtist: request.item.artists?.joined(separator: ", ") ?? request.item.albumArtist ?? "",
            MPMediaItemPropertyAlbumTitle: request.item.album ?? "",
            MPMediaItemPropertyPlaybackDuration: request.item.durationSeconds,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsedSeconds,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        let commands = MPRemoteCommandCenter.shared()
        commands.nextTrackCommand.isEnabled = hasNext
        commands.previousTrackCommand.isEnabled = hasPrevious
    }

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

    private func enqueue(_ event: PlaybackEvent, paused: Bool,
                         for requestOverride: PlaybackRequest? = nil, position: Double? = nil) {
        let reportedRequest = requestOverride ?? request
        let reportedPosition = position ?? elapsedSeconds
        if requestOverride == nil { lastReport = elapsedSeconds }
        // Keep one pending progress snapshot. Stop follows the in-flight request,
        // so an old progress response cannot overwrite the final saved position.
        if case .progress = event, let last = pendingReports.last, case .progress = last.event,
           last.request.id == reportedRequest.id {
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
                    try await store.reportPlayback(event: report.event, request: report.request,
                                                   positionSeconds: report.seconds, isPaused: report.paused)
                    syncError = nil
                    if case .stop = report.event { await store.refresh() }
                } catch {
                    syncError = report.request.item.isAudio ? "Listening progress could not sync. Check the server connection." : "Watch progress could not sync. Check the server connection."
                }
            }
            reportQueue = nil
        }
    }

}

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
                            Image(systemName: "music.note").frame(maxWidth: .infinity, maxHeight: .infinity)
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
                Button("Previous track", systemImage: "backward.end.fill") {
                    Task { await audioSession.step(-1) }
                }
                .labelStyle(.iconOnly)
                .disabled((!playback.hasPrevious && !audioSession.isSonos) || audioSession.busy)
                .accessibilityIdentifier("nowPlayingPreviousButton")
                #endif
                Button(playback.hasFinished ? "Replay" : playback.isPlaying ? "Pause" : "Play", systemImage: playback.hasFinished ? "arrow.counterclockwise" : playback.isPlaying ? "pause.fill" : "play.fill") {
                    if playback.hasFinished { Task { await playback.replay() } }
                    else { playback.togglePlayback() }
                }
                .labelStyle(.iconOnly)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
                .accessibilityIdentifier("nowPlayingPauseButton")
                #if os(tvOS)
                Button("Next track", systemImage: "forward.end.fill") {
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
    private var item: MediaItem { playback.request.item }
    private var methodLabel: String { playback.request.methodLabel }
    @Binding var videoGravity: AVLayerVideoGravity
    let tracks: VideoMediaTracks
    let selectAudio: (Int) -> Void
    let selectAudioAutomatically: () -> Void
    let selectSubtitle: (VideoSubtitleSelection) -> Void
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
        .defaultFocus($focusedTab, tab)
        #endif
    }

    private var categoryHeader: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack {
                Text("Playback settings").font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 16)
                if let onDone {
                    Button("Done", action: onDone)
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("videoSettingsDoneButton")
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(VideoSettingsTab.allCases) { candidate in
                        let isSelected = candidate == tab
                        Button(candidate.title) { tab = candidate }
                            .focused($focusedTab, equals: candidate)
                            .buttonStyle(LuminaUnderlineTabStyle(selected: isSelected))
                            .accessibilityAddTraits(isSelected ? .isSelected : [])
                            .accessibilityValue(isSelected ? "Selected" : "Not selected")
                            .accessibilityIdentifier("videoSettingsTab-\(candidate.rawValue)")
                    }
                }
            }
            .controlSize(.large)
            .fixedSize(horizontal: false, vertical: true)
            #if os(tvOS)
            .focusSection()
            #endif
        }
    }

    @ViewBuilder private var settingsContent: some View {
        switch tab {
        case .info:
            LabeledContent("Title", value: item.name)
            LabeledContent("Stream", value: methodLabel)
            if let container = item.container, !container.isEmpty {
                LabeledContent("Container", value: container.uppercased())
            }
            if let error = playback.videoActionsError {
                Text(error).font(.callout)
                Button("Retry playback actions") { Task { await playback.refreshVideoActions() } }
                    .accessibilityIdentifier("videoActionsRetryButton")
            }
        case .video:
            LabeledContent("Video fit") {
                Picker("Video fit", selection: $videoGravity) {
                    Text("Fit").tag(AVLayerVideoGravity.resizeAspect)
                    Text("Fill").tag(AVLayerVideoGravity.resizeAspectFill)
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .accessibilityLabel("Video fit")
                .accessibilityIdentifier("videoFitPicker")
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
        case .audio:
                audioButtons
        case .subtitles:
            subtitleButtons
        case .chapters:
            chapterButtons
        }
    }

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
                                Image(systemName: "checkmark")
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
                        if selected { Image(systemName: "checkmark") }
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
                Button("Caption appearance", systemImage: subtitleAppearanceExpanded ? "chevron.up" : "chevron.down") {
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
        #if os(tvOS)
        .focusSection()
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
    let onTearDown: @MainActor () -> Void
    let onVideoTap: @MainActor () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onTearDown: onTearDown, onVideoTap: onVideoTap) }

    func makeUIView(context: Context) -> PlayerLayerView {
        #if LUMINA_UI_TESTING
        print("LUMINA_LIFECYCLE native-player-layer make \(ObjectIdentifier(player).hashValue)")
        #endif
        let view = PlayerLayerView()
        view.playerLayer.player = player
        view.playerLayer.videoGravity = videoGravity
        context.coordinator.attach(view: view, player: player)
        return view
    }

    func updateUIView(_ view: PlayerLayerView, context: Context) {
        context.coordinator.onVideoTap = onVideoTap
        view.playerLayer.player = player
        view.playerLayer.videoGravity = videoGravity
        context.coordinator.attach(view: view, player: player)
    }

    static func dismantleUIView(_ view: PlayerLayerView, coordinator: Coordinator) {
        #if LUMINA_UI_TESTING
        let playerToken = view.playerLayer.player.map { String(ObjectIdentifier($0).hashValue) } ?? "none"
        print("LUMINA_LIFECYCLE native-player-layer dismantle \(playerToken)")
        #endif
        coordinator.detach(from: view)
        view.playerLayer.player = nil
        coordinator.onTearDown()
    }

    @MainActor final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        let onTearDown: @MainActor () -> Void
        var onVideoTap: @MainActor () -> Void
        private weak var view: PlayerLayerView?
        private weak var player: AVPlayer?

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

        init(onTearDown: @escaping @MainActor () -> Void, onVideoTap: @escaping @MainActor () -> Void) {
            self.onTearDown = onTearDown
            self.onVideoTap = onVideoTap
            super.init()
        }

        func attach(view: PlayerLayerView, player: AVPlayer) {
            #if os(tvOS)
            let playerChanged = self.player !== player
            #endif
            self.view = view
            self.player = player
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

private final class PlayerLayerView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    var didMoveToWindowHandler: (() -> Void)?

    override func didMoveToWindow() {
        super.didMoveToWindow()
        didMoveToWindowHandler?()
    }
}

#if os(iOS)
private struct VideoRoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.tintColor = .white
        picker.activeTintColor = UIColor(LuminaTheme.brand)
        picker.accessibilityLabel = "Audio output"
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
