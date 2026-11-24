import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore

/// The living-room client: a small native shell over the shared Lumina session and library store.
public struct TVRootView: View {
    private let store: LuminaStore
    @State private var path: [MediaItem] = []
    @State private var playbackRequest: PlaybackRequest?
    @State private var presentedPlaybackRequest: PlaybackRequest?
    @State private var libraryGeneration = 0
    @State private var isStandby = false
    @AppStorage("lumina.tv.lastSuccessfulServerOrigin") private var lastSuccessfulServerOrigin = ""
    @State private var audioSession = NativeAudioSession()
    @Environment(\.scenePhase) private var scenePhase

    public init(store: LuminaStore) {
        self.store = store
    }

    public var body: some View {
        NavigationStack(path: $path) {
            Group {
                if store.session == nil {
                    TVConnectionView(store: store)
                } else {
                    TVLibraryView(store: store, isAtNavigationRoot: path.isEmpty) { item in
                        path.append(item)
                    } onPlayback: { request in
                        playbackRequest = request
                    } onSignOut: {
                        path.removeAll()
                    }
                    .id(libraryGeneration)
                }
            }
            .onPlayPauseCommand(perform: toggleAudioPlayback)
            .navigationDestination(for: MediaItem.self) { item in
                if item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" {
                    TVMusicDetailView(item: item, store: store) { path.append($0) }
                } else if item.type == "Episode" {
                    TVEpisodeCollectionDestination(
                        item: item,
                        store: store,
                        onOpen: { path.append($0) },
                        onPlayback: { playbackRequest = $0 }
                    )
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else if item.type == "BoxSet" || item.type == "Playlist" {
                    TVMovieCollectionView(item: item, store: store, onOpen: { path.append($0) })
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else if item.isCollection {
                    TVVideoCollectionView(item: item, store: store, onOpen: { path.append($0) }, onPlayback: { playbackRequest = $0 })
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else {
                    TVDetailView(item: item, store: store, onOpen: { path.append($0) }) { request in
                        playbackRequest = request
                    }
                    .onPlayPauseCommand(perform: toggleAudioPlayback)
                }
            }
        }
        .preferredColorScheme(.dark)
        .onChange(of: store.session?.accessToken) { _, token in
            isStandby = false
            if token == nil { audioSession.stop() }
        }
        .task(id: store.session?.accessToken) {
            if store.session != nil { audioSession.restoreFromRoot(store: store) }
        }
        .onChange(of: store.session?.serverURL) { _, origin in
            isStandby = false
            if let origin { lastSuccessfulServerOrigin = origin.absoluteString }
        }
        .onChange(of: store.session?.userID) { _, _ in isStandby = false }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                ArtworkImageCache.shared.setBackgrounded(false)
                store.setCatalogBackgrounded(false)
                audioSession.resumeAfterActivation()
                Task {
                    await store.refreshNavigationCategories()
                    await audioSession.refreshSharedQueue()
                }
            } else if phase == .background {
                ArtworkImageCache.shared.setBackgrounded(true)
                store.setCatalogBackgrounded(true)
                audioSession.enterBackground()
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didReceiveMemoryWarningNotification)) { _ in
            ArtworkImageCache.shared.trimMemory()
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.session != nil { NativeNowPlayingBar(store: store) }
        }
        .nativeVideoResumeChoice(request: $playbackRequest, store: store, onReady: { presentedPlaybackRequest = $0 })
        .fullScreenCover(item: $presentedPlaybackRequest, onDismiss: {
            playbackRequest = nil
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store, onReturnHome: {
                path.removeAll()
                libraryGeneration += 1
            }, onAutoplayStandby: {
                audioSession.stop()
                path.removeAll()
                libraryGeneration += 1
                isStandby = true
            })
        }
        .accessibilityHidden(isStandby)
        .overlay {
            if isStandby, store.session != nil, presentedPlaybackRequest == nil {
                TVStandbyArtworkView(store: store) { isStandby = false }
            }
        }
        .environment(audioSession)
        .tint(LuminaTheme.ink)
    }

    private func toggleAudioPlayback() {
        // Keep background music reachable without intercepting a music detail's
        // command to start its album when no session exists yet.
        guard playbackRequest == nil else {
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-cover-present")
            #endif
            return
        }
        let isAtNavigationRoot = path.isEmpty
        Task {
            if isAtNavigationRoot {
                if await audioSession.toggleTVPausedRestoreIfNeeded() {
                    #if LUMINA_UI_TESTING
                    audioSession.recordTVRestoreRemoteOutcome("root-restore-handled")
                    #endif
                    return
                }
            }
            guard audioSession.playback != nil else {
                #if LUMINA_UI_TESTING
                audioSession.recordTVRestoreRemoteOutcome("root-no-playback")
                #endif
                return
            }
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-toggle")
            #endif
            await audioSession.toggle()
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-toggle-finished")
            #endif
        }
    }
}
