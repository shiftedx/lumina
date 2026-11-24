import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct AudioLibraryView: View {
    let store: LuminaStore
    var usesPortraitNavigationRail = false
    @State private var kind: MusicBrowseKind = .albums
    @State private var searchText = ""
    @State private var searchVisible = false
    @State private var showingBrowse = false

    private var featuredAlbum: MediaItem? {
        guard kind == .albums, searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return store.audioItems.first { $0.type == "MusicAlbum" }
    }

    var body: some View {
        Group {
            if store.music.homeCapability == .available && !showingBrowse {
                NativePhoneMusicHome(store: store) { showingBrowse = true }
            } else {
                browseBody
            }
        }
        .accessibilityIdentifier("audioScreen")
        .task(id: "\(store.music.sessionIdentity ?? ""):\(store.isOffline)") {
            showingBrowse = false
            if store.isOffline {
                await store.music.refreshOfflineHome(isDownloaded: { store.offline.localURL(for: $0) != nil })
            } else {
                await store.music.refreshHomeCapability()
                if store.music.homeCapability == .available { await store.music.refreshHome() }
            }
        }
    }

    private var browseBody: some View {
        GeometryReader { geometry in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let album = featuredAlbum {
                    NavigationLink(value: album) {
                        MusicCollectionSpotlight(item: album, store: store,
                            minimumHeight: min(680, max(360, geometry.size.height * 0.78)),
                            topContentClearance: geometry.safeAreaInsets.top)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("audioFeaturedAlbum")
                    .padding(.horizontal, -20)
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await load() } }
                }
                if store.audioItems.isEmpty {
                    if store.isAudioBusy {
                        ProgressView("Loading music…")
                    } else if store.errorMessage == nil {
                        ContentUnavailableView {
                            Label(searchText.isEmpty ? "Your listening room is quiet" : "No music found",
                                  glyph: .music, variant: .solid)
                        } description: {
                            Text(searchText.isEmpty ? "Add music to Lumina, then refresh to find your albums, artists, and tracks here." : "Try another search or browse a different collection.")
                        } actions: {
                            Button("Refresh") { Task { await load() } }
                        }
                    }
                } else {
                    MusicGrid(items: store.audioItems, store: store, identifierPrefix: "audioItem") { item in
                        if store.audioHasMore, !store.isAudioBusy, item.id == store.audioItems.suffix(6).first?.id {
                            Task { await store.loadMoreAudio() }
                        }
                    }
                    if store.audioHasMore {
                        Button {
                            Task { await store.loadMoreAudio() }
                        } label: {
                            HStack {
                                if store.isAudioBusy { ProgressView() }
                                Text("Load more music")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(LuminaQuietActionStyle())
                        .disabled(store.isAudioBusy)
                        .accessibilityIdentifier("audioLoadMoreButton")
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, featuredAlbum == nil ? 20 : 0)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .ignoresSafeArea(.container, edges: featuredAlbum == nil ? .horizontal : [.top, .horizontal])
        .modifier(HomeHeroScrollEdge(isEnabled: featuredAlbum != nil))
        .scrollClipDisabled()
        .toolbarColorScheme(featuredAlbum == nil ? nil : .dark, for: .navigationBar)
        .accessibilityIdentifier("audioScreen")
        .toolbar {
            if !usesPortraitNavigationRail {
                ToolbarItem(placement: .topBarTrailing) { musicOptionsMenu }
                ToolbarItem(placement: .topBarTrailing) { musicSearchButton }
            }
        }
        .portraitNavigationRail(isEnabled: usesPortraitNavigationRail, store: store) {
            musicSearchButton
            musicOptionsMenu
        }
        .modifier(NativeSearchPresentation(text: $searchText, isPresented: $searchVisible, prompt: "Search music",
                                           usesInlineSearch: usesPortraitNavigationRail,
                                           onSubmit: { Task { await load() } }))
        .onSubmit(of: .search) { Task { await load() } }
        .onChange(of: searchText) { if searchText.isEmpty { Task { await load() } } }
        .task(id: kind) { await load() }
        .refreshable { await load() }
        }
    }

    private var musicSearchButton: some View {
        Button("Search music", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("musicSearchButton")
    }

    private var musicOptionsMenu: some View {
        Menu {
            if store.music.homeCapability == .available {
                Button("Music Home", glyph: .music) { showingBrowse = false }
                    .accessibilityIdentifier("musicHomeButton")
            }
            ForEach(NativeMusicSection.allCases, id: \.self) { section in
                NavigationLink {
                    NativeMusicLibraryView(store: store, section: section)
                        .toolbarVisibility(.visible, for: .navigationBar)
                        .toolbar {
                            NativeNowPlayingToolbar(store: store)
                        }
                } label: {
                    Label(section.rawValue, glyph: section.glyph)
                }
                .accessibilityIdentifier("musicLibrary-\(section.rawValue.lowercased())")
            }
            Divider()
            Picker("Browse music", selection: $kind) {
                ForEach(MusicBrowseKind.allCases, id: \.self) { value in Text(value.label).tag(value) }
            }
            .accessibilityIdentifier("audioKindPicker")
        } label: { Label("Music options", glyph: .moreHorizontal) }
        .accessibilityIdentifier("musicOptionsMenu")
    }

    private func load() async {
        await store.loadAudio(kind: kind, search: searchText.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

@MainActor
struct NativePhoneMusicHome: View {
    let store: LuminaStore
    let onBrowse: () -> Void
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var editing = false
    @State private var playback: PlaybackModel?
    @State private var resumeError: String?
    @State private var resuming = false

    var body: some View {
        List {
            if !store.isOffline {
                Section {
                    Button("Browse music", glyph: .music, action: onBrowse)
                        .accessibilityIdentifier("musicHomeBrowseButton")
                }
            }
            switch store.music.homeState {
            case .idle, .loading:
                ProgressView("Loading Music Home…")
            case .failed(let message):
                Section {
                    Text(message)
                    Button("Retry Music Home") { Task { await store.music.refreshHome() } }
                }
            case .empty(let home), .ready(let home):
                ForEach(home.layout.order, id: \.self) { shelf in
                    if !home.layout.hidden.contains(shelf) { shelfContent(shelf, home: home) }
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(LuminaTheme.paper.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .navigationTitle("Music")
        .accessibilityIdentifier("musicHomeScreen")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Edit shelves", glyph: .edit) { editing = true }
                    .accessibilityIdentifier("musicHomeEditButton")
                    .disabled(home == nil || store.isOffline)
            }
            NativeNowPlayingToolbar(store: store)
        }
        .sheet(isPresented: $editing) {
            if let home { NativePhoneMusicHomeEditor(store: store, layout: home.layout) }
        }
        .nativeAudioPlayer(playback: $playback, store: store)
        .refreshable { await store.music.refreshHome() }
    }

    private var home: MusicHome? {
        switch store.music.homeState {
        case .empty(let home), .ready(let home): home
        default: nil
        }
    }

    @ViewBuilder private func shelfContent(_ shelf: MusicHomeShelf, home: MusicHome) -> some View {
        switch shelf {
        case .resume:
            if let resume = home.resume {
                Section("Resume") {
                    if resume.state == .waiting {
                        Text("Waiting for import\(resume.track.title.map { ": \($0)" } ?? "")")
                    } else if resume.state == .unavailable {
                        Text("Download this track when connected to resume on this device.")
                    } else {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(resume.track.title ?? "Saved queue").font(.headline)
                            Text([resume.track.artist, resume.track.album].compactMap { $0 }.joined(separator: " · "))
                                .foregroundStyle(.secondary)
                            Text("Queue item \((resume.currentIndex ?? 0) + 1) · \(time(resume.positionSeconds))\(resume.track.duration.map { " / \(time($0))" } ?? "")")
                                .font(.caption).foregroundStyle(.secondary)
                            Button("Resume \(resume.track.title ?? "saved queue")", glyph: .play, variant: .solid) {
                                resuming = true; resumeError = nil
                                Task {
                                    defer { resuming = false }
                                    do {
                                        playback = try await audioSession.resumeFromHome(resume, store: store)
                                        if playback == nil { resumeError = audioSession.message ?? "Resume is unavailable. Refresh Music Home and try again." }
                                    } catch { resumeError = error.localizedDescription }
                                }
                            }
                            .disabled(resuming)
                            .accessibilityIdentifier("musicHomeResumeButton")
                            if let resumeError { Text(resumeError).foregroundStyle(.secondary) }
                        }
                    }
                }
            }
        case .recent:
            if !home.recent.isEmpty {
                Section("Recently played") {
                    ForEach(home.recent, id: \.id) { album in
                        NavigationLink(value: recentItem(id: album.id, name: album.name, artist: album.artistName)) {
                            VStack(alignment: .leading) {
                                Text(album.name)
                                if let artist = album.artistName { Text(artist).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                        .accessibilityIdentifier("musicHomeRecent-\(album.id)")
                    }
                }.accessibilityIdentifier("musicHomeShelf-recent")
            }
        case .playlists:
            Section("Playlists") {
                if home.playlists.isEmpty { Text("No playlists yet.").foregroundStyle(.secondary) }
                ForEach(home.playlists) { playlist in
                    NavigationLink(playlist.name) { NativePlaylistDetail(store: store, playlist: playlist) }
                        .accessibilityIdentifier("musicHomePlaylist-\(playlist.id)")
                }
                NavigationLink("All playlists") { NativeMusicLibraryView(store: store, section: .playlists) }
            }
        case .downloads:
            Section("Downloads") {
                let count = store.offline.downloadedItems.filter(\.isAudio).count
                Text(count == 0 ? "Save music to listen on this device." : "\(count) tracks saved on this device.")
                    .foregroundStyle(.secondary)
                NavigationLink("Open downloads") { NativeMusicLibraryView(store: store, section: .downloads) }
            }.accessibilityIdentifier("musicHomeShelf-downloads")
        case .mixes:
            Section("Mixes") {
                ForEach(home.mixes, id: \.kind) { mix in
                    NavigationLink(mix.name) { NativeMusicMixView(store: store, kind: mix.kind, title: mix.name) }
                }
            }.accessibilityIdentifier("musicHomeShelf-mixes")
        }
    }

    private func time(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.isFinite ? seconds : 0))
        return "\(total / 60):\(String(format: "%02d", total % 60))"
    }

    private func recentItem(id: String, name: String, artist: String?) -> MediaItem {
        MediaItem(id: id, name: name, type: "MusicAlbum", overview: nil, productionYear: nil,
                  seriesName: nil, seriesID: nil, seasonID: nil, collectionType: nil, indexNumber: nil,
                  parentIndexNumber: nil, runTimeTicks: nil, userData: nil, imageTags: nil,
                  backdropImageTags: nil, parentBackdropItemID: nil, parentBackdropImageTags: nil,
                  album: nil, albumID: nil, albumPrimaryImageTag: nil, artists: artist.map { [$0] },
                  albumArtist: artist, childCount: nil, container: nil)
    }
}

@MainActor
private struct NativePhoneMusicHomeEditor: View {
    let store: LuminaStore
    @State var layout: MusicHomeLayout
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(Array(layout.order.enumerated()), id: \.element) { index, shelf in
                        HStack {
                            Toggle(shelfTitle(shelf), isOn: Binding(
                                get: { !layout.hidden.contains(shelf) },
                                set: { visible in
                                    var next = layout
                                    if visible { next.hidden.removeAll { $0 == shelf } }
                                    else { next.hidden.append(shelf) }
                                    save(next)
                                }))
                            .tint(LuminaTheme.brand)
                            .accessibilityIdentifier("musicHomeToggle-\(shelf.rawValue)")
                            Menu {
                                Button("Move earlier") { move(index, -1) }.disabled(index == 0)
                                Button("Move later") { move(index, 1) }.disabled(index == layout.order.count - 1)
                            } label: {
                                Label("Reorder \(shelfTitle(shelf))", glyph: .moreHorizontal)
                            }
                            .labelStyle(.iconOnly)
                            .buttonStyle(.borderless)
                            .accessibilityIdentifier("musicHomeReorder-\(shelf.rawValue)")
                        }
                    }
                } footer: { Text("Show, hide, and reorder shelves for your account.") }
                Section {
                    Button("Reset shelves") { save(.reset) }
                        .accessibilityIdentifier("musicHomeResetButton")
                }
                if let error { Section { Text(error).foregroundStyle(.secondary) } }
            }
            .disabled(saving)
            .navigationTitle("Edit shelves")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }

    private func move(_ index: Int, _ offset: Int) {
        var next = layout
        next.order.swapAt(index, index + offset)
        save(next)
    }

    private func save(_ next: MusicHomeLayout) {
        saving = true; error = nil
        Task {
            defer { saving = false }
            do {
                try await store.music.saveHomeLayout(next)
                if case .ready(let home) = store.music.homeState { layout = home.layout }
                else if case .empty(let home) = store.music.homeState { layout = home.layout }
            } catch { self.error = error.localizedDescription }
        }
    }

    private func shelfTitle(_ shelf: MusicHomeShelf) -> String {
        switch shelf {
        case .resume: "Resume"
        case .recent: "Recently played"
        case .playlists: "Playlists"
        case .downloads: "Downloads"
        case .mixes: "Mixes"
        }
    }
}

@MainActor
struct MusicCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var children: [MediaItem] = []
    @State private var fileQuality: MusicCollectionFileQuality?
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var albumPlayback: PlaybackModel?
    @State private var mixSeed: NativeMixSeed?
    @State private var preparingPlayback = false
    @State private var playbackError: String?

    @State private var layoutWidth: CGFloat = 0
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 720
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ScrollView {
            Group {
                if layoutWidth >= minimumWideWidth && !dynamicTypeSize.isAccessibilitySize {
                    HStack(alignment: .top, spacing: 40) {
                        collectionArtwork.frame(width: 280)
                        collectionBody
                    }
                } else {
                    VStack(alignment: .leading, spacing: 28) {
                        collectionArtwork
                            .frame(maxWidth: 260)
                            .frame(maxWidth: .infinity)
                        collectionBody
                    }
                }
            }
            .padding(20)
            .padding(.top, 12)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { layoutWidth = $0 }
        }
        .modifier(AlbumArtworkBackground(url: store.imageURL(for: item, maxWidth: 760), enabled: item.type == "MusicAlbum"))
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarColorScheme(item.type == "MusicAlbum" ? .dark : nil, for: .navigationBar)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("musicCollectionScreen")
        .standardPhonePushedChrome(store: store,
                                   foreground: item.type == "MusicAlbum" ? .white : LuminaTheme.ink,
                                   background: item.type == "MusicAlbum" ? LuminaTheme.theater : LuminaTheme.paper)
        .task(id: item.id) { await load() }
        .refreshable { await load() }
        .nativeAudioPlayer(playback: $albumPlayback, store: store) {
            Task { await store.refresh() }
        }
        .sheet(item: $mixSeed) { seed in
            NativeContextualMixView(store: store, audio: audioSession, seed: seed) { albumPlayback = $0 }
        }
    }

    private var collectionArtwork: some View {
        ArtworkView(url: store.imageURL(for: item, maxWidth: 760), name: item.name, aspect: 1, glyph: .music)
            .shadow(color: .black.opacity(0.18), radius: 20, y: 10)
    }

    private var collectionBody: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(item.name)
                .font(LuminaTheme.heading(34, relativeTo: .largeTitle))
                .accessibilityAddTraits(.isHeader)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle).foregroundStyle(LuminaTheme.muted)
            }
            if let overview = item.overview, !overview.isEmpty {
                Text(overview).font(.body)
            }
            if let quality = fileQuality?.album {
                NativeAlbumFileQuality(quality: quality)
            }
            if children.contains(where: \.isAudio) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { albumActions }
                    VStack(alignment: .leading, spacing: 12) { albumActions }
                }
            }
            if store.music.homeCapability == .available && !store.isOffline, item.type == "MusicAlbum" || item.type == "MusicArtist" {
                Button("Instant Mix", glyph: .discover) {
                    mixSeed = NativeMixSeed(kind: item.type == "MusicAlbum" ? .album : .artist, itemID: item.id, name: item.name)
                }
                .accessibilityIdentifier("nativeInstantMixButton")
            }
            if let playbackError {
                Text(playbackError)
                    .font(.callout)
                    .foregroundStyle(LuminaTheme.muted)
            }
            if loading {
                ProgressView("Loading music…")
            } else if let errorMessage {
                InlineErrorView(message: errorMessage) { Task { await load() } }
            } else if children.isEmpty {
                ContentUnavailableView {
                    Label("No music here yet", glyph: .music, variant: .solid)
                } description: {
                    Text("Refresh after adding music to this collection.")
                }
            } else if item.type == "MusicAlbum", children.allSatisfy(\.isAudio) {
                albumTracks
            } else {
                MusicGrid(items: children, store: store, identifierPrefix: "musicItem", fileQuality: fileQuality?.albums ?? [:])
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var albumTracks: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(children.enumerated()), id: \.element.id) { index, track in
                Button { playTrack(track) } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(String(track.indexNumber ?? index + 1))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(LuminaTheme.muted)
                            .frame(minWidth: 24, alignment: .leading)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(track.name)
                                .font(LuminaTheme.heading(18, relativeTo: .body))
                                .fixedSize(horizontal: false, vertical: true)
                            if let quality = fileQuality?.tracks[track.id] {
                                NativeTrackFileQuality(quality: quality)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        if track.durationSeconds > 0 {
                            Text(Duration.seconds(track.durationSeconds).formatted(.time(pattern: .minuteSecond)))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(LuminaTheme.muted)
                        }
                    }
                    .padding(.vertical, 16)
                    .frame(minHeight: 56)
                    .contentShape(.rect)
                    .overlay(alignment: .top) {
                        Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
                    }
                }
                .buttonStyle(.plain)
                .contextMenu {
                    if store.music.homeCapability == .available && !store.isOffline {
                        Button("Track Radio", glyph: .discover) {
                            mixSeed = NativeMixSeed(kind: .track, itemID: track.id, name: track.name)
                        }
                    }
                }
                .disabled(preparingPlayback || audioSession.busy)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: track))
                .accessibilityValue([track.durationSeconds > 0 ? Duration.seconds(track.durationSeconds).formatted(.units(allowed: [.minutes, .seconds], width: .wide)) : "", fileQuality?.tracks[track.id]?.summary ?? ""].filter { !$0.isEmpty }.joined(separator: ", "))
                .accessibilityIdentifier("musicItem-\(track.id)")
            }
        }
    }

    @ViewBuilder private var albumActions: some View {
        Button {
            playCollection(shuffled: false)
        } label: {
            Label(preparingPlayback ? "Preparing…" : "Play album", glyph: .play, variant: .solid, size: 24)
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicPlayButton")
        Button {
            playCollection(shuffled: true)
        } label: {
            Label("Shuffle", glyph: .shuffle, size: 24)
        }
        .buttonStyle(LuminaQuietActionStyle())
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicShuffleButton")
    }

    private func playTrack(_ track: MediaItem) {
        guard !preparingPlayback else { return }
        preparingPlayback = true
        playbackError = nil
        let items = children
        Task {
            defer { preparingPlayback = false }
            do { albumPlayback = try await audioSession.play(items: items, startingAt: track, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

    private func playCollection(shuffled: Bool) {
        guard !preparingPlayback else { return }
        let tracks = children.filter(\.isAudio)
        guard !tracks.isEmpty else { return }
        preparingPlayback = true
        playbackError = nil
        Task {
            defer { preparingPlayback = false }
            do {
                let resolved = try await store.music.resolve(items: tracks)
                let first = shuffled ? Int.random(in: 0..<max(1, resolved.count)) : 0
                albumPlayback = try await audioSession.play(tracks: resolved, index: first, store: store, shuffle: shuffled)
            } catch {
                playbackError = (error as? LocalizedError)?.errorDescription
                    ?? "Playback could not start. Check your connection and try again."
            }
        }
    }

    private func load() async {
        loading = true
        errorMessage = nil
        defer { loading = false }
        async let quality = try? await store.music.fileQuality(for: item.id)
        do {
            let loaded = try await store.musicChildren(of: item)
            guard !Task.isCancelled else { return }
            children = loaded
            loading = false
            let facts = await quality
            guard !Task.isCancelled else { return }
            fileQuality = facts
        }
        catch { errorMessage = "This music collection could not load. Check your connection and try again." }
    }
}

@MainActor
private struct MusicGrid: View {
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    var fileQuality: [String: MusicAlbumFileQuality] = [:]
    var onItemAppear: (MediaItem) -> Void = { _ in }
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.horizontalSizeClass) private var widthClass
    @State private var lastVisibleItemID: String?
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var presentedPlayback: PlaybackModel?
    @State private var mixSeed: NativeMixSeed?
    @State private var startingTrack = false
    @State private var playbackError: String?

    private var columns: [GridItem] {
        if dynamicTypeSize.isAccessibilitySize { return libraryGridColumns(for: dynamicTypeSize) }
        if widthClass == .regular { return [GridItem(.adaptive(minimum: 168, maximum: 220), spacing: 16, alignment: .top)] }
        return [GridItem(.flexible(), spacing: 16, alignment: .top), GridItem(.flexible(), spacing: 16, alignment: .top)]
    }

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
            ForEach(items) { item in
                itemControl(item)
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: item))
                .accessibilityValue(fileQuality[item.id]?.accessibilitySummary ?? "")
                .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                .onAppear {
                    lastVisibleItemID = item.id
                    onItemAppear(item)
                }
            }
        }
        .nativeAudioPlayer(playback: $presentedPlayback, store: store)
        .sheet(item: $mixSeed) { seed in
            NativeContextualMixView(store: store, audio: audioSession, seed: seed) { presentedPlayback = $0 }
        }
        .alert("Playback unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
        .task(id: lastVisibleItemID, priority: .utility) {
            await store.prefetchArtwork(after: lastVisibleItemID, in: items)
        }
    }
    @ViewBuilder
    private func itemControl(_ item: MediaItem) -> some View {
        if item.isAudio {
            Button { playTrack(item) } label: { card(item) }
                .contextMenu {
                    if store.music.homeCapability == .available && !store.isOffline {
                        Button("Track Radio", glyph: .discover) {
                            mixSeed = NativeMixSeed(kind: .track, itemID: item.id, name: item.name)
                        }
                    }
                }
                .disabled(startingTrack || audioSession.busy)
        } else {
            NavigationLink(value: item) { card(item) }
                .contextMenu { NativeMetadataActions(item: item) }
        }
    }

    private func card(_ item: MediaItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            MediaCard(item: item, imageURL: store.imageURL(for: item), identifierPrefix: identifierPrefix,
                      loadFraming: { await store.artworkFraming(for: $0) })
            if let quality = fileQuality[item.id] {
                NativeAlbumFileQualityCaption(quality: quality)
            }
        }
    }

    private func playTrack(_ item: MediaItem) {
        guard !startingTrack else { return }
        startingTrack = true
        let sourceItems = items
        Task {
            defer { startingTrack = false }
            do { presentedPlayback = try await audioSession.play(items: sourceItems, startingAt: item, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

}

/// An inactive native search drawer still occupies space on Duo. Install it only
/// after the member chooses Search, and remove it completely when dismissed.
