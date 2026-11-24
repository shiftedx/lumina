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
