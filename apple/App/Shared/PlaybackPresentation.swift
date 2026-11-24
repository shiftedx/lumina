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

extension EnvironmentValues {
    @Entry var returnToLuminaHome: () -> Void = {}
}

extension QueuePresentationRow where Item == MediaItem {
    var accessibilityID: String { "audioQueueItem-\(item.id)" + (duplicated ? "-\(id.uuidString)" : "") }
}

@MainActor
extension View {
    func nativeAudioPlayer(playback: Binding<PlaybackModel?>, store: LuminaStore,
                           onDismiss: @escaping () -> Void = {}) -> some View {
        modifier(NativeAudioPlayerPresentation(playback: playback, store: store, onDismiss: onDismiss))
    }

    func nativeVideoResumeChoice(request: Binding<PlaybackRequest?>, store: LuminaStore,
                                 onReady: @escaping (PlaybackRequest) -> Void,
                                 onCancel: @escaping () -> Void = {}) -> some View {
        modifier(NativeVideoResumeChoice(request: request, store: store, onReady: onReady, onCancel: onCancel))
    }

    #if !os(tvOS)
    func nativePlayback(request: Binding<PlaybackRequest?>, store: LuminaStore,
                        onDismiss: @escaping () -> Void) -> some View {
        modifier(NativePlaybackPresentation(request: request, store: store, onDismiss: onDismiss))
    }
    #endif
}

/// Explicit entries resolve fresh progress before the long-lived player owns a stream.
private struct NativeVideoResumeChoice: ViewModifier {
    @Binding var request: PlaybackRequest?
    let store: LuminaStore
    let onReady: (PlaybackRequest) -> Void
    let onCancel: () -> Void
    private struct Choice: Identifiable {
        let request: PlaybackRequest
        let owner: String?
        var id: String { request.id }
    }

    @State private var pending: Choice?
    @State private var approved: PlaybackRequest?
    @State private var choiceOriginID: String?
    @State private var choiceOwner: String?

    func body(content: Content) -> some View {
        content
            .fullScreenCover(item: $pending, onDismiss: finishChoice) { candidate in
                NativeResumeChoiceView(request: candidate.request, store: store, owner: candidate.owner) { selected in
                    guard request?.id == candidate.id, pending?.id == candidate.id,
                          store.session?.accessToken == candidate.owner else { return }
                    approved = selected
                    pending = nil
                } onCancel: {
                    pending = nil
                }
                .presentationBackground(.clear)
            }
            .onChange(of: request?.id, initial: true) { _, _ in
                approved = nil
                guard let request else {
                    pending = nil
                    choiceOriginID = nil
                    return
                }
                if !request.item.isAudio, request.startPositionSeconds > 0 {
                    choiceOriginID = request.id
                    let owner = store.session?.accessToken
                    choiceOwner = owner
                    pending = Choice(request: request, owner: owner)
                } else if pending != nil {
                    choiceOriginID = request.id
                    choiceOwner = store.session?.accessToken
                    approved = request
                    pending = nil
                } else {
                    onReady(request)
                }
            }
            .onChange(of: store.session?.accessToken) { _, _ in
                if choiceOriginID != nil {
                    approved = nil
                    pending = nil
                    request = nil
                }
            }
    }

    private func finishChoice() {
        let selected = approved
        approved = nil
        let origin = choiceOriginID
        choiceOriginID = nil
        guard request?.id == origin else { return }
        guard store.session?.accessToken == choiceOwner else { request = nil; return }
        // Present the player after the chooser has finished dismissing.
        if let selected { onReady(selected) }
        else { request = nil; onCancel() }
    }
}

private struct NativeResumeChoiceView: View {
    let request: PlaybackRequest
    let store: LuminaStore
    let owner: String?
    let onSelect: (PlaybackRequest) -> Void
    let onCancel: () -> Void
    @State private var restarting = false
    @State private var errorMessage: String?
    @FocusState private var focusedAction: Action?
    private enum Action: Hashable { case resume, startOver, cancel }

    private var panelWidth: CGFloat {
        #if os(tvOS)
        640
        #else
        480
        #endif
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.opacity(0.65).ignoresSafeArea().accessibilityHidden(true)
                ScrollView {
                    panel
                        .padding(24)
                        .frame(maxWidth: .infinity, minHeight: geometry.size.height)
                }
                .scrollIndicators(.hidden)
            }
        }
        #if os(tvOS)
        .onExitCommand(perform: onCancel)
        #endif
        .task(id: restarting) {
            guard restarting, store.session?.accessToken == owner else { return }
            do {
                let fresh = try await store.preparePlaybackFromBeginning(after: request)
                guard !Task.isCancelled, store.session?.accessToken == owner else { return }
                onSelect(fresh)
            } catch {
                guard !Task.isCancelled, store.session?.accessToken == owner else { return }
                restarting = false
                errorMessage = "Playback could not start over. Check your connection and try again."
            }
        }
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(spacing: 12) {
                LuminaBrandMark().frame(width: 36, height: 22)
                Text("CONTINUE WATCHING")
                    .font(.caption.weight(.semibold)).tracking(1.5)
                    .foregroundStyle(LuminaTheme.muted)
            }
            VStack(alignment: .leading, spacing: 10) {
                Text(request.item.name)
                    .font(LuminaTheme.heading(32, relativeTo: .title))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text("Resume from \(Duration.seconds(request.startPositionSeconds).formatted(.time(pattern: .hourMinuteSecond))) or start from the beginning.")
                    .font(.body).foregroundStyle(LuminaTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let errorMessage {
                Text(errorMessage).font(.callout).foregroundStyle(.red)
                    .accessibilityLabel("Playback error: \(errorMessage)")
            }
            VStack(spacing: 12) {
                Button { onSelect(request) } label: {
                    Label("Resume", glyph: .play, variant: .solid)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .focused($focusedAction, equals: .resume)
                .disabled(restarting)
                Button { restarting = true } label: {
                    HStack(spacing: 10) {
                        if restarting { ProgressView() }
                        Text("Start Over")
                    }.frame(maxWidth: .infinity)
                }
                .buttonStyle(LuminaQuietActionStyle(selected: true))
                .focused($focusedAction, equals: .startOver)
                .disabled(restarting)
                Button("Cancel", role: .cancel, action: onCancel)
                    .frame(maxWidth: .infinity)
                    .buttonStyle(LuminaQuietActionStyle())
                    .focused($focusedAction, equals: .cancel)
            }
        }
        .padding(32)
        .frame(maxWidth: panelWidth, alignment: .leading)
        .background(LuminaTheme.paper, in: .rect(cornerRadius: 24))
        .overlay { RoundedRectangle(cornerRadius: 24).strokeBorder(LuminaTheme.ink.opacity(0.12)) }
        .shadow(color: .black.opacity(0.3), radius: 32, y: 16)
        .defaultFocus($focusedAction, .resume)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Resume playback?")
        .accessibilityIdentifier("resumePlaybackChoice")
    }
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
    @Environment(\.returnToLuminaHome) private var returnHome
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
        .nativeVideoResumeChoice(request: $request, store: store, onReady: { candidate in
            guard !candidate.item.isAudio else { return }
            videoSession.present(request: candidate, store: store, audioSession: audioSession, onReturnHome: returnHome) {
                self.request = nil
                onDismiss()
            }
        }, onCancel: onDismiss)
    }
}

/// The session and its startup outlive adaptive navigation and cover content.
@MainActor @Observable
final class NativeVideoSession {
    struct Presentation: Identifiable {
        let id = UUID()
        let playback: PlaybackModel
        let returnHome: () -> Void
    }
    private(set) var presentation: Presentation?
    let casting = NativeVideoCasting()
    @ObservationIgnored private var startup: Task<Void, Never>?
    @ObservationIgnored private var onDismiss: (() -> Void)?

    func present(request: PlaybackRequest, store: LuminaStore, audioSession: NativeAudioSession,
                 onReturnHome: @escaping () -> Void = {}, onDismiss: @escaping () -> Void) {
        guard presentation == nil else { return }
        audioSession.stop()
        let model = PlaybackModel(request: request, store: store, queue: [])
        casting.attach(model)
        model.setActive(UIApplication.shared.applicationState == .active)
        self.onDismiss = onDismiss
        presentation = Presentation(playback: model, returnHome: onReturnHome)
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
                           onVideoDismiss: { session.dismiss(id: presentation.id) },
                           onReturnHome: presentation.returnHome)
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
struct AudioArtworkSwipe: UIGestureRecognizerRepresentable {
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
