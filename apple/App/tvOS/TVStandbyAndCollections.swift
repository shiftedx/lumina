import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVStandbyArtworkView: View {
    let store: LuminaStore
    let onDismiss: () -> Void
    @State private var items: [MediaItem] = []
    @State private var index = 0
    @FocusState private var focused: Bool
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver

    private var canRotate: Bool {
        scenePhase == .active && !reduceMotion && !voiceOver && items.count > 1
    }

    var body: some View {
        Button(action: onDismiss) {
            ZStack {
                Color.black
                if scenePhase == .active, items.indices.contains(index) {
                    BillboardArtwork(item: items[index], store: store, showsPoster: true)
                        .id(items[index].id)
                        .transition(.opacity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focusEffectDisabled()
        .focused($focused)
        .ignoresSafeArea()
        .accessibilityLabel("Standby. Press any button to return Home.")
        .accessibilityValue(items.indices.contains(index) ? items[index].name : "")
        .accessibilityIdentifier("tvStandbyArtwork")
        .onAppear { focused = true }
        .onMoveCommand { _ in onDismiss() }
        .onExitCommand(perform: onDismiss)
        .onPlayPauseCommand(perform: onDismiss)
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            let loaded = (try? await store.loadStandbyArtwork()) ?? store.featuredItems.filter {
                $0.type == "Movie" || $0.type == "Series"
            }
            guard !Task.isCancelled else { return }
            items = loaded.shuffled()
            index = 0
        }
        .task(id: canRotate) {
            while canRotate {
                do { try await Task.sleep(for: .seconds(20)) }
                catch { return }
                guard !Task.isCancelled, canRotate else { return }
                withAnimation(.easeInOut(duration: 2)) { index = (index + 1) % items.count }
            }
        }
    }
}

@MainActor
struct TVMovieCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @State private var children = [MediaItem]()
    @State private var isLoading = true
    @State private var errorMessage: String?

    private var title: String { item.type == "BoxSet" ? "Movies" : "Titles" }

    var body: some View {
        TVHeroScrollView {
            VStack(alignment: .leading, spacing: 34) {
                MediaBillboard(item: item, store: store, context: "", showsOverview: false, showsContext: false,
                               artworkFirst: true, wideMinimumHeight: 620) {
                    Text(title)
                        .font(TVTypography.cardTitle.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.9))
                }
                if isLoading {
                    ProgressView("Loading \(title.lowercased())…")
                        .frame(maxWidth: .infinity, minHeight: 240)
                } else if let errorMessage {
                    TVMessageView(title: "Could not load this collection", message: errorMessage, actionTitle: "Try again") {
                        Task { await load() }
                    }
                } else if children.isEmpty {
                    TVMessageView(title: item.type == "Playlist" ? "No playable video titles" : "No movies yet", message: item.type == "Playlist" ? "This list has no video titles Lumina can play here." : "Add movies to Lumina, then refresh this collection.", actionTitle: "Refresh") {
                        Task { await load() }
                    }
                } else {
                    Text(title)
                        .font(TVTypography.sectionTitle)
                        .foregroundStyle(LuminaTheme.ink)
                        .padding(.horizontal, 82)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(children) { child in
                            TVMediaCard(item: child, store: store, poster: true, showsCaption: false, artworkWidth: 480) { onOpen(child) }
                                .accessibilityIdentifier("movieCollectionItem-\(child.id)")
                        }
                    }
                    .padding(.horizontal, 82)
                    .focusSection()
                }
            }
            .padding(.bottom, 76)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("movieCollectionScreen")
        .task(id: item.id) { await load() }
    }

    private func load() async {
        isLoading = children.isEmpty
        errorMessage = nil
        do {
            children = try await store.loadChildren(of: item)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "Check your connection and try again."
        }
        isLoading = false
    }
}

struct TVEpisodeCollectionDestination: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    @State private var series: MediaItem?
    @State private var couldNotResolveSeries = false

    var body: some View {
        Group {
            if let series {
                TVVideoCollectionView(
                    item: series,
                    store: store,
                    onOpen: onOpen,
                    onPlayback: onPlayback,
                    initialSeasonID: item.seasonID,
                    initialEpisodeID: item.id
                )
            } else if couldNotResolveSeries {
                TVDetailView(item: item, store: store, onOpen: onOpen, onPlayback: onPlayback)
            } else {
                ProgressView("Loading show…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityIdentifier("videoCollectionLoading")
            }
        }
        .task(id: item.id) {
            guard let seriesID = item.seriesID else {
                couldNotResolveSeries = true
                return
            }
            do {
                series = try await store.seriesCollection(id: seriesID, useCache: true)
            } catch {
                couldNotResolveSeries = true
            }
        }
    }
}
