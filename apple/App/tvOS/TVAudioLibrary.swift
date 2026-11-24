import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVCollectionFilterPicker: View {
    let store: LuminaStore
    let watchlistID: String?
    let includesFranchises: Bool
    let selection: String?
    let onSelect: (String?, String?) -> Void
    let onClose: () -> Void
    @State private var collections = [MediaItem]()
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack {
                    Text("Collections").font(TVTypography.pageTitle)
                    Spacer()
                    Button("Cancel", action: onClose)
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("collectionFilterCancelButton")
                }
                Button("All titles") {
                    onSelect(nil, nil)
                    onClose()
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: selection == nil))
                .accessibilityValue(selection == nil ? "Selected" : "")
                .accessibilityAddTraits(selection == nil ? .isSelected : [])
                .accessibilityIdentifier("collectionFilter-any")

                if isLoading {
                    ProgressView("Loading collections…")
                        .frame(maxWidth: .infinity, minHeight: 360)
                } else if let errorMessage {
                    TVMessageView(title: "Collections could not load", message: errorMessage, actionTitle: "Try again") {
                        Task { await load() }
                    }
                } else if collections.isEmpty {
                    TVMessageView(title: "No collections yet", message: "Collections will appear here when available.", actionTitle: "Refresh") {
                        Task { await load() }
                    }
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(collections) { item in
                            TVMediaCard(item: item, store: store, poster: true, artworkWidth: 480) {
                                onSelect(item.id, item.name)
                                onClose()
                            }
                            .accessibilityValue(selection == item.id ? "Selected" : "")
                            .accessibilityIdentifier("collectionFilter-\(item.id)")
                        }
                    }
                    .focusSection()
                }
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
        }
        .background(LuminaTheme.theater)
        .foregroundStyle(LuminaTheme.ink)
        .accessibilityIdentifier("collectionFilterPicker")
        .task { await load() }
    }

    private func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            collections = try await store.collectionFilterOptions().filter {
                $0.id != watchlistID && (includesFranchises || $0.type != "BoxSet")
            }
        }
        catch is CancellationError { }
        catch { errorMessage = "Check the connection and try again." }
    }
}

struct TVAudioView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Binding var browse: TVAudioBrowseState
    let restoreFocusRequest: Int?
    let isAtNavigationRoot: Bool
    @State private var pendingRestoredItemID: String?
    @State private var submittedSearch: String?
    @State private var lastVisibleItemID: String?
    @State private var mountedItemIDs = Set<String>()
    @State private var presentedMusicItem: MediaItem?
    @FocusState private var focusedItemID: String?
    @FocusState private var focusedSearch: Bool
    @FocusState private var focusedCategory: MusicBrowseKind?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    TVMusicHome(store: store, onOpen: onOpen) {
                        browse.kind = .albums
                        browse.query = ""
                        proxy.scrollTo("tvAudioBrowse", anchor: .top)
                        focusedCategory = .albums
                    }
                    NativeMusicLibraryLinks(store: store)
                        .padding(.horizontal, 80)
                        .focusSection()
                    HStack(spacing: 32) {
                        HStack(spacing: 28) {
                            ForEach(MusicBrowseKind.allCases, id: \.self) { category in
                                Button(category.label) { browse.kind = category }
                                    .buttonStyle(LuminaUnderlineTabStyle(selected: browse.kind == category))
                                    .accessibilityAddTraits(browse.kind == category ? .isSelected : [])
                                    .accessibilityIdentifier("audioCategory-\(category.rawValue)")
                                    .focused($focusedCategory, equals: category)
                            }
                        }
                        Spacer()
                        TextField("Search music", text: $browse.query)
                            .font(TVTypography.body)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .frame(width: 440)
                            .accessibilityIdentifier("audioSearchField")
                            .focused($focusedSearch)
                    }
                    .padding(.horizontal, 80)
                    .focusSection()
                    .id("tvAudioBrowse")
                    if store.isAudioBusy && store.audioItems.isEmpty {
                        ProgressView("Loading music…").frame(maxWidth: .infinity, minHeight: 360)
                    } else if store.audioItems.isEmpty {
                        TVMessageView(title: "Your music library", message: store.errorMessage ?? "Add music to Lumina, or try another search.", actionTitle: "Refresh") { Task { await store.loadAudio(kind: browse.kind, search: browse.query) } }
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 290), spacing: 32, alignment: .top)], spacing: 48) {
                            ForEach(store.audioItems) { item in
                                TVMediaCard(item: item, store: store) { presentedMusicItem = item }
                                    .focused($focusedItemID, equals: item.id)
                                    .accessibilityIdentifier("audioItem-\(item.id)")
                                    .id(item.id)
                                    .onAppear {
                                        mountedItemIDs.insert(item.id)
                                        if pendingRestoredItemID == item.id { restoreSelection(item.id) }
                                        lastVisibleItemID = item.id
                                        if store.audioHasMore && item.id == store.audioItems.suffix(6).first?.id { Task { await store.loadMoreAudio() } }
                                    }
                                    .onDisappear { mountedItemIDs.remove(item.id) }
                            }
                        }
                        .padding(.horizontal, 80)
                        .padding(.top, 24)
                        .focusSection()
                        if store.audioHasMore {
                            Button("Show more") { Task { await store.loadMoreAudio() } }
                                .buttonStyle(LuminaQuietActionStyle()).padding(32)
                        }
                        if let error = store.errorMessage {
                            Text(error).foregroundStyle(LuminaTheme.accentInk).padding(24)
                        }
                    }
                }
                .padding(.bottom, 32)
            }
            .onAppear {
                guard isAtNavigationRoot, let itemID = browse.focusedItemID else { return }
                pendingRestoredItemID = itemID
                proxy.scrollTo(itemID, anchor: .center)
            }
            .onChange(of: isAtNavigationRoot) { wasRoot, isRoot in
                guard !wasRoot, isRoot, let itemID = browse.focusedItemID else { return }
                pendingRestoredItemID = itemID
                proxy.scrollTo(itemID, anchor: .center)
            }
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .accessibilityIdentifier("audioScreen")
        .fullScreenCover(item: $presentedMusicItem) { item in
            TVMusicDetailCover(item: item, store: store)
        }
        .task(id: "\(store.music.sessionIdentity ?? "")-\(store.isOffline)") {
            if store.isOffline {
                await store.music.refreshOfflineHome(isDownloaded: { store.offline.localURL(for: $0) != nil })
            } else {
                await store.music.refreshHomeCapability()
                if store.music.homeCapability == .available { await store.music.refreshHome() }
            }
        }
        .task(id: lastVisibleItemID, priority: .utility) {
            await store.prefetchArtwork(after: lastVisibleItemID, in: store.audioItems, maxWidth: 760)
        }
        .onChange(of: focusedItemID) { _, itemID in
            if let itemID { browse.focusedItemID = itemID }
        }
        .onChange(of: restoreFocusRequest, initial: true) { _, request in
            guard request != nil else { return }
            pendingRestoredItemID = browse.focusedItemID
            restoreSelection(restorableItemID)
        }
        .onChange(of: restorableItemID) { _, itemID in restoreSelection(itemID) }
        .task(id: "\(browse.kind.rawValue)-\(browse.query)") {
            let query = browse.query
            if let submittedSearch, query != submittedSearch, !query.isEmpty {
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            }
            guard !Task.isCancelled else { return }
            submittedSearch = query
            await store.loadAudio(kind: browse.kind, search: browse.query)
        }
    }

    private var restorableItemID: String? {
        guard let itemID = pendingRestoredItemID else { return nil }
        return store.audioItems.contains { $0.id == itemID } ? itemID : nil
    }

    private func restoreSelection(_ itemID: String?) {
        guard let itemID, mountedItemIDs.contains(itemID) else { return }
        focusedItemID = itemID
        pendingRestoredItemID = nil
    }
}

/// Keep an Audio-grid detail in the grid's presentation hierarchy. tvOS then
/// restores the presenting card through UIKit rather than asking the Home and
/// Browse focus regions to compete after a root NavigationStack pop.
private struct TVMusicDetailCover: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(\.dismiss) private var dismiss
    @State private var path = [MediaItem]()

    var body: some View {
        NavigationStack(path: $path) {
            TVMusicDetailView(item: item, store: store) { path.append($0) }
                .navigationDestination(for: MediaItem.self) { nested in
                    TVMusicDetailView(item: nested, store: store) { path.append($0) }
                }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            NativeNowPlayingBar(store: store)
        }
        .onExitCommand {
            if path.isEmpty { dismiss() }
            else { path.removeLast() }
        }
    }
}

private struct TVMusicHome: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onBrowse: () -> Void
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var editing = false
    @State private var editLayout: MusicHomeLayout?
    @State private var playback: PlaybackModel?
    @State private var errorMessage: String?
    @FocusState private var focusedAction: String?
    @State private var lastAction: String?

    private var home: MusicHome? {
        switch store.music.homeState {
        case .ready(let home), .empty(let home): home
        default: nil
        }
    }

    var body: some View {
        Group {
            if store.music.homeCapability == .available {
                VStack(alignment: .leading, spacing: 28) {
                    HStack(spacing: 40) {
                        Text("Music Home").font(TVTypography.sectionTitle).accessibilityIdentifier("tvMusicHome")
                        Button("Browse Music", action: onBrowse)
                            .focused($focusedAction, equals: "browse")
                            .accessibilityIdentifier("tvMusicHomeBrowse")
                        if let home, !store.isOffline {
                            Button("Edit Home") { editLayout = home.layout; editing = true }
                                .focused($focusedAction, equals: "edit")
                                .accessibilityIdentifier("tvMusicHomeEdit")
                        }
                    }
                    switch store.music.homeState {
                    case .idle, .loading:
                        ProgressView("Loading Music Home…")
                    case .failed(let message):
                        Text(message)
                        Button("Try again") { Task { await store.music.refreshHome() } }
                            .focused($focusedAction, equals: "retry")
                            .accessibilityIdentifier("tvMusicHomeRetry")
                    case .empty(let home), .ready(let home):
                        ForEach(home.layout.order, id: \.self) { shelf in
                            if !home.layout.hidden.contains(shelf) {
                                shelfView(shelf, home: home)
                            }
                        }
                    }
                    if let errorMessage { Text(errorMessage).foregroundStyle(.secondary) }
                }
                .padding(.horizontal, 80)
                .padding(.vertical, 24)
                .focusSection()
                .defaultFocus($focusedAction, lastAction ?? "browse")
                .sheet(isPresented: $editing, onDismiss: { focusedAction = "edit" }) {
                    if let editLayout { TVMusicHomeEditor(store: store, layout: editLayout) }
                }
                .onChange(of: focusedAction) { _, action in
                    if let action { lastAction = action }
                }
                .onAppear {
                    if let lastAction { focusedAction = lastAction }
                }
                .nativeAudioPlayer(playback: $playback, store: store)
            }
        }
    }

    @ViewBuilder private func shelfView(_ shelf: MusicHomeShelf, home: MusicHome) -> some View {
        switch shelf {
        case .resume:
            if let resume = home.resume {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Resume").font(TVTypography.sectionTitle)
                    if resume.state == .waiting {
                        Text("Waiting for import").accessibilityIdentifier("tvMusicHomeWaiting")
                    } else if resume.state == .unavailable {
                        Text("\(resume.track.title ?? "Saved track") — download when connected to resume.")
                            .accessibilityIdentifier("tvMusicHomeUnavailable")
                    } else {
                        let track = resume.track
                        Button {
                            Task {
                                do {
                                    playback = try await audioSession.resumeFromHome(resume, store: store)
                                    if playback == nil { errorMessage = audioSession.message }
                                } catch { errorMessage = "Could not resume playback. Try again." }
                            }
                        } label: {
                            VStack(alignment: .leading) {
                                Text(track.title ?? "Saved track")
                                Text("\(track.artist ?? "Unknown artist") · Track \((resume.currentIndex ?? 0) + 1) · \(Int(resume.positionSeconds)) seconds")
                                    .font(TVTypography.body)
                            }
                        }
                        .focused($focusedAction, equals: "resume")
                        .accessibilityIdentifier("tvMusicHomeResume")
                    }
                }.focusSection()
            }
        case .recent:
            if !home.recent.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Recently Played").font(TVTypography.sectionTitle)
                    ScrollView(.horizontal) {
                        HStack(spacing: 24) {
                            ForEach(home.recent, id: \.id) { album in
                                Button(album.name) {
                                    Task {
                                        do { onOpen(try await store.musicCollection(id: album.id, expectedType: "MusicAlbum")) }
                                        catch { errorMessage = "Could not open this album. Try again." }
                                    }
                                }
                                .focused($focusedAction, equals: "recent-\(album.id)")
                                .accessibilityIdentifier("tvMusicHomeRecent-\(album.id)")
                            }
                        }.padding(.vertical, 10)
                    }.scrollClipDisabled()
                }.focusSection()
            }
        case .playlists:
            if !home.playlists.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Playlists").font(TVTypography.sectionTitle)
                    ScrollView(.horizontal) {
                        HStack(spacing: 24) {
                            ForEach(home.playlists) { playlist in
                                NavigationLink(playlist.name) { NativePlaylistDetail(store: store, playlist: playlist) }
                                    .focused($focusedAction, equals: "playlist-\(playlist.id)")
                                    .accessibilityIdentifier("tvMusicHomePlaylist-\(playlist.id)")
                            }
                        }.padding(.vertical, 10)
                    }.scrollClipDisabled()
                }.focusSection()
            }
        case .downloads:
            VStack(alignment: .leading, spacing: 12) {
                Text("Downloads").font(TVTypography.sectionTitle)
                NavigationLink {
                    NativeMusicLibraryView(store: store, section: .downloads)
                } label: {
                    Text(store.offline.downloadedItems.contains(where: \.isAudio) ? "Downloaded music" : "No downloaded music yet")
                }
                .focused($focusedAction, equals: "downloads")
                .accessibilityIdentifier("tvMusicHomeDownloads")
            }.focusSection()
        case .mixes:
            if !home.mixes.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Mixes").font(TVTypography.sectionTitle)
                    ScrollView(.horizontal) {
                        HStack(spacing: 24) {
                            ForEach(home.mixes, id: \.kind) { mix in
                                NavigationLink(mix.name) { NativeMusicMixView(store: store, kind: mix.kind, title: mix.name) }
                                    .focused($focusedAction, equals: "mix-\(mix.kind)")
                                    .accessibilityIdentifier("tvMusicHomeMix-\(mix.kind)")
                            }
                        }.padding(.vertical, 10)
                    }.scrollClipDisabled()
                }.focusSection()
            }
        }
    }
}

private struct TVMusicHomeEditor: View {
    let store: LuminaStore
    @Environment(\.dismiss) private var dismiss
    @State var layout: MusicHomeLayout
    @State private var saving = false
    @State private var errorMessage: String?
    @FocusState private var focusedAction: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Edit Music Home").font(TVTypography.sectionTitle).accessibilityIdentifier("tvMusicHomeEditor")
                ForEach(Array(layout.order.enumerated()), id: \.element) { index, shelf in
                    HStack {
                        Text(title(shelf)).frame(width: 320, alignment: .leading)
                        Button(layout.hidden.contains(shelf) ? "Show" : "Hide") {
                            var updated = layout
                            if updated.hidden.contains(shelf) { updated.hidden.removeAll { $0 == shelf } }
                            else { updated.hidden.append(shelf) }
                            save(updated)
                        }
                        .accessibilityIdentifier("tvMusicHomeVisibility-\(shelf.rawValue)")
                        Button("Move Up") {
                            var updated = layout
                            updated.order.swapAt(index, index - 1)
                            save(updated)
                        }.disabled(index == 0)
                            .accessibilityIdentifier("tvMusicHomeMoveUp-\(shelf.rawValue)")
                        Button("Move Down") {
                            var updated = layout
                            updated.order.swapAt(index, index + 1)
                            save(updated)
                        }.disabled(index == layout.order.count - 1)
                            .accessibilityIdentifier("tvMusicHomeMoveDown-\(shelf.rawValue)")
                    }.focusSection()
                }
                if saving { ProgressView("Saving…").accessibilityIdentifier("tvMusicHomeSaving") }
                if let errorMessage { Text(errorMessage).foregroundStyle(.secondary) }
                HStack {
                    Button("Reset") { save(.reset) }.accessibilityIdentifier("tvMusicHomeReset")
                    Button("Done") { dismiss() }.focused($focusedAction, equals: "done")
                        .accessibilityIdentifier("tvMusicHomeDone")
                }
                .padding(.leading, 320)
                .focusSection()
            }
            .padding(80)
        }
        .background(LuminaTheme.paper.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .buttonStyle(LuminaQuietActionStyle())
        .defaultFocus($focusedAction, "done")
    }

    private func title(_ shelf: MusicHomeShelf) -> String {
        switch shelf {
        case .resume: "Resume"
        case .recent: "Recently Played"
        case .playlists: "Playlists"
        case .downloads: "Downloads"
        case .mixes: "Mixes"
        }
    }

    private func save(_ updated: MusicHomeLayout) {
        guard !saving else { return }
        saving = true
        errorMessage = nil
        Task {
            do {
                try await store.music.saveHomeLayout(updated)
                switch store.music.homeState {
                case .ready(let home), .empty(let home): layout = home.layout
                default: layout = updated
                }
            } catch { errorMessage = "Could not save your layout. Try again." }
            saving = false
        }
    }
}
