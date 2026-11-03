import AVKit
import SwiftUI
import LuminaCore

/// Remote providers retain their own session lifecycle; saved videos use the library player.
@MainActor
struct NativeRemotePlayer: View {
    let store: LuminaStore
    let item: HomeExternalItem
    @Environment(\.dismiss) private var dismiss
    @Environment(NativeAudioSession.self) private var audioSession
    @Environment(\.scenePhase) private var scenePhase
    @State private var playback: NativeRemotePlayback?
    @State private var owningSession: LuminaSession?
    @State private var player: AVPlayer?
    @State private var failure: String?
    @State private var attempt = UUID()
    @State private var observation: NSKeyValueObservation?
    @State private var resumeAfterBackground = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let player {
                RemotePlayerController(player: player)
                    .ignoresSafeArea()
                    .accessibilityIdentifier("nativeRemoteVideo")
            }
            if let failure {
                VStack(spacing: 20) {
                    Text(item.title).font(.title2)
                    Text(failure).font(.body).foregroundStyle(.secondary)
                    Button("Try again") { attempt = UUID() }
                    if let source = item.sourceURL {
                        Link("Open source", destination: source)
                    }
                    Button("Close") { dismiss() }
                }
                .padding(32)
                .frame(maxWidth: 640)
            } else if player == nil {
                ProgressView("Opening stream…").tint(.white)
            }
            #if os(iOS)
            VStack {
                HStack {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark").font(.headline)
                            .frame(width: 44, height: 44)
                            .background(.black.opacity(0.65), in: Circle())
                    }
                    .accessibilityLabel("Close streaming video")
                    .accessibilityIdentifier("nativeRemoteCloseButton")
                    Spacer()
                }
                Spacer()
            }.padding()
            #endif
        }
        #if os(iOS) && LUMINA_UI_TESTING
        .overlay(alignment: .bottomTrailing) {
            if let player {
                TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                    Color.clear.frame(width: 1, height: 1)
                        .accessibilityElement()
                        .accessibilityIdentifier("nativeRemotePlaybackTime")
                        .accessibilityValue(String(player.currentTime().seconds))
                }
                .allowsHitTesting(false)
            }
        }
        #endif
        .foregroundStyle(.white)
        .preferredColorScheme(.dark)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("nativeRemotePlaybackScreen")
        .task(id: attempt) { await prepare() }
        .onChange(of: store.session.map { [$0.serverURL.absoluteString, $0.userID, $0.accessToken] }) { _, _ in dismiss() }
        .onDisappear { tearDown() }
        .onChange(of: scenePhase) { _, phase in
            // A layout or display transition can briefly make the scene inactive.
            if phase == .background {
                resumeAfterBackground = player.map { $0.timeControlStatus != .paused } ?? false
                player?.pause()
            } else if phase == .active && resumeAfterBackground {
                resumeAfterBackground = false
                player?.play()
            }
        }
        #if os(tvOS)
        .onExitCommand { dismiss() }
        #endif
    }

    private func prepare() async {
        tearDown()
        failure = nil
        guard let source = item.sourceURL else {
            failure = "This source is unavailable."
            return
        }
        do {
            let owner = store.session
            let prepared = try await store.prepareNativeRemotePlayback(sourceURL: source)
            guard !Task.isCancelled else {
                await store.stopNativeRemotePlayback(prepared, session: owner)
                return
            }
            playback = prepared
            owningSession = owner
            guard prepared.status == "ready", let playbackURL = prepared.playbackURL else {
                tearDown()
                failure = "This source cannot play here. Open it in the source app or website."
                return
            }
            audioSession.stop()
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
            try AVAudioSession.sharedInstance().setActive(true)
            let media = AVPlayerItem(url: playbackURL)
            let activePlayer = AVPlayer(playerItem: media)
            player = activePlayer
            observation = media.observe(\.status, options: [.new]) { media, _ in
                Task { @MainActor in
                    guard activePlayer === player, media.status == .failed else { return }
                    failure = "This stream could not play. Try again or open the source."
                    activePlayer.pause()
                }
            }
            activePlayer.play()
        } catch is CancellationError { return }
        catch {
            guard !Task.isCancelled else { return }
            tearDown()
            failure = "This stream is unavailable. Try again or open the source."
        }
    }

    private func tearDown() {
        resumeAfterBackground = false
        observation?.invalidate()
        observation = nil
        player?.pause()
        player = nil
        if let active = playback {
            let owner = owningSession
            playback = nil
            owningSession = nil
            Task { await store.stopNativeRemotePlayback(active, session: owner) }
        }
    }
}

private struct RemotePlayerController: UIViewControllerRepresentable {
    let player: AVPlayer

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        controller.player = player
        controller.allowsPictureInPicturePlayback = false
        return controller
    }

    func updateUIViewController(_ controller: AVPlayerViewController, context: Context) {
        if controller.player !== player { controller.player = player }
    }

    static func dismantleUIViewController(_ controller: AVPlayerViewController, coordinator: ()) {
        controller.player = nil
    }
}
