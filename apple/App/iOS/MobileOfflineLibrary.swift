import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct NativeOfflineLibraryView: View {
    @Bindable var store: LuminaStore
    var usesPushedChrome = true
    @State private var query = ""
    @State private var searchVisible = false
    @State private var playbackRequest: PlaybackRequest?
    @State private var preparingID: String?
    @State private var playbackError: String?

    private var savedItems: [MediaItem] {
        store.offline.downloadedItems.filter {
            query.isEmpty || ($0.name + " " + ($0.seriesName ?? "") + " " + ($0.albumArtist ?? ""))
                .localizedStandardContains(query)
        }
    }

    var body: some View {
        navigationChrome {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                HStack {
                    Label(store.isOffline ? "Offline" : "Downloaded", glyph: store.isOffline ? .wifiOff : .download)
                        .font(.subheadline).foregroundStyle(LuminaTheme.muted)
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount: store.offline.usedBytes, countStyle: .file))
                        .font(.caption.monospacedDigit()).foregroundStyle(LuminaTheme.muted)
                }
                if store.isOffline, store.music.homeCapability == .available {
                    NavigationLink("Music Home") {
                        NativePhoneMusicHome(store: store) { }
                    }
                    .accessibilityIdentifier("offlineMusicHomeButton")
                }
                if searchVisible {
                    HStack {
                        TextField("Search downloads", text: $query).submitLabel(.search)
                        Button("Cancel") { query = ""; searchVisible = false }
                    }
                    .padding(.bottom, 10)
                    .overlay(alignment: .bottom) { Rectangle().fill(LuminaTheme.accentInk).frame(height: 1) }
                }
                if savedItems.isEmpty {
                    ContentUnavailableView {
                        Label(store.offline.downloadedItems.isEmpty ? "No downloads yet" : "No matching downloads", glyph: .download, size: 36)
                    } description: {
                        Text(store.offline.downloadedItems.isEmpty
                             ? "Download movies, episodes, or music while connected to watch and listen here offline."
                             : "Try a different search.")
                    }
                    .accessibilityIdentifier("offlineEmptyState")
                }
                ForEach(savedItems) { item in
                    HStack(spacing: 16) {
                        Button { play(item) } label: {
                            HStack(spacing: 16) {
                                ArtworkView(url: store.offline.artworkURL(for: item.id), name: item.name,
                                            aspect: item.isAudio ? 1 : 2 / 3, glyph: item.isAudio ? .music : .movies)
                                    .frame(width: 70, height: item.isAudio ? 70 : 105)
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(item.name).font(.headline).lineLimit(2)
                                    if !item.displaySubtitle.isEmpty {
                                        Text(item.displaySubtitle).font(.subheadline).foregroundStyle(LuminaTheme.muted).lineLimit(2)
                                    }
                                    Label("On this device", glyph: .downloadDone, size: 16)
                                        .font(.caption).foregroundStyle(LuminaTheme.muted)
                                }
                                Spacer(minLength: 0)
                                if preparingID == item.id { ProgressView() }
                                else { LuminaIcon(glyph: .play, variant: .solid, size: 24) }
                            }
                            .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        .disabled(preparingID != nil)
                        .accessibilityLabel("Play \(item.name) offline")
                        .accessibilityIdentifier("offlineItem-\(item.id)")
                        Menu {
                            Button("Remove Download", glyph: .trash, role: .destructive) {
                                store.offline.remove(id: item.id)
                            }
                        } label: { Label("Download actions", glyph: .moreHorizontal).labelStyle(.iconOnly) }
                    }
                    Divider()
                }
                if !store.isOffline, !store.offline.pendingItems.isEmpty {
                    Text("Downloads").font(.title3.bold())
                    ForEach(store.offline.pendingItems) { item in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(item.name).font(.headline)
                            switch store.offline.downloadStates[item.id] {
                            case .queued:
                                Text("Queued").font(.caption)
                                Button("Cancel Download", glyph: .close) { store.offline.cancel(id: item.id) }
                            case .downloading(let received, let total):
                                if let total, total > 0 { ProgressView(value: Double(received), total: Double(total)) }
                                Text(ByteCountFormatter.string(fromByteCount: received, countStyle: .file)).font(.caption)
                                Button("Cancel Download", glyph: .close) { store.offline.cancel(id: item.id) }
                            case .failed(let message):
                                Text(message).font(.caption).foregroundStyle(LuminaTheme.muted)
                                retryDownload(item)
                            case .cancelled:
                                retryDownload(item)
                            default:
                                EmptyView()
                            }
                        }
                    }
                }
                if let playbackError {
                    Label(playbackError, glyph: .warning).foregroundStyle(LuminaTheme.muted)
                }
                if let error = store.offline.errorMessage {
                    Label(error, glyph: .warning).foregroundStyle(LuminaTheme.muted)
                }
            }
            .padding(24)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper.ignoresSafeArea())
        .navigationTitle("On this device")
        .tint(LuminaTheme.ink)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                offlineHeaderActions
            }
            NativeNowPlayingToolbar(store: store)
        }
        .task(id: store.isOffline) {
            store.offline.refresh()
            if store.isOffline {
                await store.music.refreshOfflineHome(isDownloaded: { store.offline.localURL(for: $0) != nil })
            }
        }
        .refreshable { store.offline.refresh(); await store.refreshConnectivity() }
        .nativePlayback(request: $playbackRequest, store: store) { store.offline.refresh() }
        .accessibilityIdentifier("offlineLibraryScreen")
        }
    }

    @ViewBuilder private func navigationChrome<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        if usesPushedChrome {
            content()
                .standardPhonePushedChrome(store: store) { offlineHeaderActions }
        } else {
            content()
        }
    }

    @ViewBuilder private var offlineHeaderActions: some View {
        Button("Search", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("offlineSearchButton")
        if store.isOffline {
            Button("Reconnect", glyph: .sync) { Task { await store.refreshConnectivity() } }
                .accessibilityIdentifier("offlineReconnectButton")
        }
    }

    @ViewBuilder private func retryDownload(_ item: MediaItem) -> some View {
        if item.isAudio {
            NativeDownloadControls(items: [item], store: store, title: "Retry Download")
        } else {
            Button("Retry Download", glyph: .download) { Task { await store.downloadVideo(item: item) } }
        }
    }

    private func play(_ item: MediaItem) {
        guard preparingID == nil else { return }
        preparingID = item.id
        playbackError = nil
        Task {
            defer { preparingID = nil }
            do { playbackRequest = try await store.preparePlayback(for: item) }
            catch { playbackError = "This download is no longer available. Remove it and download it again when connected." }
        }
    }
}
