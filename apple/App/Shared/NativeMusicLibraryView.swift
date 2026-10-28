import AVFoundation
import LuminaCore
import Observation
import SwiftUI

/// Keep native list behavior on iOS and an explicit, focusable scrolling surface on Apple TV.
private struct NativeMusicList<Content: View>: View {
    let horizontalInset: CGFloat
    let content: Content

    init(horizontalInset: CGFloat = 80, @ViewBuilder content: () -> Content) {
        self.horizontalInset = horizontalInset
        self.content = content()
    }

    var body: some View {
        #if os(tvOS)
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, horizontalInset)
            .padding(.vertical, 24)
        }
        .background(LuminaTheme.paper.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .buttonStyle(LuminaQuietActionStyle())
        .focusSection()
        #else
        List {
            content
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .contentMargins(.horizontal, 20, for: .scrollContent)
        .listRowBackground(Color.clear)
        .background(LuminaTheme.paper.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        #endif
    }
}

enum NativeMusicSection: String, CaseIterable {
    case playlists = "Playlists", mixes = "Mixes", downloads = "Downloads", queue = "Queue"

    var glyph: LuminaGlyph {
        switch self {
        case .playlists: .playlist
        case .mixes: .discover
        case .downloads: .download
        case .queue: .queue
        }
    }
}

struct NativeMusicLibraryLinks: View {
    let store: LuminaStore
    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 10) {
                ForEach(NativeMusicSection.allCases, id: \.self) { section in
                    NavigationLink {
                        NativeMusicLibraryView(store: store, section: section)
                    } label: {
                        Label(section.rawValue, glyph: section.glyph)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    #if os(tvOS)
                    .buttonStyle(LuminaUnderlineTabStyle())
                    #else
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(LuminaTheme.ink)
                    .padding(.horizontal, 4)
                    .buttonStyle(.plain)
                    #endif
                    .accessibilityIdentifier("musicLibrary-\(section.rawValue.lowercased())")
                }
            }
            .padding(.vertical, 6)
        }
        #if os(tvOS)
        .scrollClipDisabled()
        .focusSection()
        #endif
    }
}

/// The same listening library adapts to touch navigation and the Siri Remote.
struct NativeMusicLibraryView: View {
    @Environment(\.scenePhase) private var scenePhase
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var section: NativeMusicSection
    @State private var playlistDraft: PlaylistDraft?
    @State private var clearDownloads = false
    @State private var clearingQueue = false
    @State private var errorMessage: String?
    @State private var playback: PlaybackModel?

    private struct PlaylistDraft: Identifiable {
        let id = UUID()
        let tracks: [MediaItem]
    }

    init(store: LuminaStore, section: NativeMusicSection = .playlists) {
        self.store = store
        _section = State(initialValue: section)
    }

    var body: some View {
        NativeMusicList {
            Section {
                Picker("Listening library", selection: $section) {
                    ForEach(NativeMusicSection.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("musicLibrarySectionPicker")
            }
            switch section {
            case .playlists: playlistSections
            case .mixes: mixSections
            case .downloads: downloadSections
            case .queue: queueSections
            }
            if let message = errorMessage {
                Section { Label(message, glyph: .warning) }
            }
        }
        .navigationTitle(section.rawValue)
        .accessibilityIdentifier("musicLibraryScreen")
        #if os(iOS)
        .standardPhonePushedChrome(store: store)
        #endif
        .sheet(item: $playlistDraft) { draft in
            NativePlaylistEditor(store: store, playlist: nil, initialTracks: draft.tracks) { }
        }
        .confirmationDialog("Remove all downloaded music?", isPresented: $clearDownloads, titleVisibility: .visible) {
            Button("Remove downloads", role: .destructive) { store.offline.clear() }
        } message: { Text("The server library and playlists stay available.") }
        .confirmationDialog("Clear the listening queue?", isPresented: $clearingQueue, titleVisibility: .visible) {
            Button("Clear queue", role: .destructive) { audioSession.stop() }
        } message: { Text("Playback stops and the saved queue is cleared for your account.") }
        .nativeAudioPlayer(playback: $playback, store: store)
        .onChange(of: scenePhase) { _, phase in
            if phase == .active, section == .downloads { store.offline.refresh() }
        }
        .task(id: section) {
            if section == .playlists { await store.music.refresh() }
            if section == .downloads { store.offline.refresh() }
            if section == .queue { await audioSession.refreshSharedQueue() }
        }
    }

    @ViewBuilder private var playlistSections: some View {
        Section {
            Button("New playlist", glyph: .add) {
                playlistDraft = PlaylistDraft(tracks: [])
            }
                .accessibilityIdentifier("createMusicPlaylistButton")
            Button("Save listening queue as a playlist", glyph: .playlist) {
                playlistDraft = PlaylistDraft(tracks: audioSession.playback?.queue ?? [])
            }
            .disabled(audioSession.playback?.queue.isEmpty != false)
            Button("Refresh playlists", glyph: .sync) { Task { await store.music.refresh() } }
        }
        Section("Saved playlists") {
            if store.music.isBusy { ProgressView("Loading playlists…") }
            if let message = store.music.errorMessage { Text(message).foregroundStyle(.secondary) }
            if store.music.playlists.isEmpty && !store.music.isBusy {
                Text("Keep an album sequence, make a household playlist, or build a smart selection that updates with your library.")
                    .foregroundStyle(.secondary)
            }
            ForEach(store.music.playlists) { playlist in
                NavigationLink {
                    NativePlaylistDetail(store: store, playlist: playlist)
                } label: {
                    Label {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(playlist.name).font(.headline)
                            Text([playlist.rules == nil ? "\(playlist.count ?? 0) tracks" : "Smart playlist",
                                  playlist.shared ? "Household" : "Private", playlist.canEdit ? "" : "Read only"]
                                .filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } icon: {
                        LuminaIcon(glyph: playlist.rules == nil ? .playlist : .discover, size: 20)
                    }
                }
                .accessibilityIdentifier("musicPlaylist-\(playlist.id)")
            }
        }
    }

    @ViewBuilder private var mixSections: some View {
        Section("Made from your library") {
            mixLink("In rotation", kind: "rotation", glyph: .history,
                    description: "Familiar favorites with something you have not heard recently.")
            mixLink("Favorites", kind: "favorites", glyph: .favorite,
                    description: "Tracks from your favorite music.")
            mixLink("Unplayed", kind: "unplayed", glyph: .discover,
                    description: "A fresh listen from your own collection.")
            if let current = audioSession.playback?.request.item {
                NavigationLink {
                    NativeMusicMixView(store: store, kind: "radio", title: "Track radio", seedID: current.id)
                } label: { Label("Radio from \(current.name)", systemImage: "dot.radiowaves.left.and.right") }
                .accessibilityIdentifier("musicTrackRadioButton")
            } else {
                Text("Play a track to start its radio mix.").foregroundStyle(.secondary)
            }
        }
    }

    private func mixLink(_ title: String, kind: String, glyph: LuminaGlyph, description: String) -> some View {
        NavigationLink {
            NativeMusicMixView(store: store, kind: kind, title: title)
        } label: {
            Label {
                VStack(alignment: .leading, spacing: 5) {
                    Text(title).font(.headline)
                    Text(description).font(.caption).foregroundStyle(.secondary)
                }
            } icon: { LuminaIcon(glyph: glyph, size: 20) }
        }
        .accessibilityIdentifier("musicMix-\(kind)")
    }

    @ViewBuilder private var downloadSections: some View {
        Section("Available on this device") {
            Text("\(ByteCountFormatter.string(fromByteCount: store.offline.usedBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: store.offline.limitBytes, countStyle: .file))")
                .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
            Text("Downloaded music is private to this account. Signing out removes it from this device.")
                .font(.footnote).foregroundStyle(.secondary)
            #if os(tvOS)
            Text("Apple TV may remove saved audio when it needs space. Refresh checks which tracks are still on this device.")
                .font(.footnote).foregroundStyle(.secondary)
            #endif
            if let message = store.offline.errorMessage { Text(message).foregroundStyle(.secondary) }
            if store.offline.downloadedItems.filter(\.isAudio).isEmpty {
                Text("Save tracks, albums, or your listening queue from their playback controls.").foregroundStyle(.secondary)
            } else {
                Button("Play downloaded music", glyph: .play, variant: .solid) { playDownloads(at: 0) }
                    .accessibilityIdentifier("playDownloadsButton")
            }
            ForEach(store.offline.downloadedItems.filter(\.isAudio)) { item in
                HStack {
                    Button { playDownloads(at: store.offline.downloadedItems.filter(\.isAudio).firstIndex(of: item) ?? 0) } label: {
                        NativeMusicTrackLabel(item: item, store: store)
                    }
                    .accessibilityIdentifier("downloadedTrack-\(item.id)")
                    Spacer()
                    Button("Remove download", glyph: .trash, role: .destructive) { store.offline.remove(id: item.id) }
                        .labelStyle(.iconOnly)
                }
                .buttonStyle(LuminaQuietActionStyle())
            }
            Button("Refresh downloads", glyph: .sync) { store.offline.refresh() }
            Button("Remove all downloads", glyph: .trash, role: .destructive) { clearDownloads = true }
                .disabled(store.offline.downloadedItems.filter(\.isAudio).isEmpty && store.offline.pendingItems.filter(\.isAudio).isEmpty
                          && store.offline.usedBytes == 0 && store.offline.errorMessage == nil)
        }
        if !store.offline.pendingItems.filter(\.isAudio).isEmpty {
            Section("Downloads in progress") {
                ForEach(store.offline.pendingItems.filter(\.isAudio)) { item in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(item.name).font(.headline)
                        switch store.offline.downloadStates[item.id] {
                        case .queued:
                            Text("Queued").font(.caption)
                            Button("Cancel download") { store.offline.cancel(id: item.id) }
                        case .downloading(let received, let total):
                            if let total, total > 0 {
                                ProgressView(value: Double(received), total: Double(total))
                            } else { ProgressView("Saving audio…") }
                            Button("Cancel download") { store.offline.cancel(id: item.id) }
                        case .failed(let message):
                            Text(message).font(.caption)
                            NativeDownloadControls(items: [item], store: store, title: "Retry download")
                        case .cancelled:
                            Text("Cancelled").font(.caption)
                            NativeDownloadControls(items: [item], store: store, title: "Retry download")
                        default:
                            EmptyView()
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private var queueSections: some View {
        Section("Your listening queue") {
            Text("\(audioSession.snapshot.queue.count) available tracks · revision \(audioSession.snapshot.revision)")
                .font(.subheadline).foregroundStyle(.secondary)
            Text("Queues restored from another device stay paused until you choose Play.")
                .font(.footnote).foregroundStyle(.secondary)
            Button("Refresh shared queue", glyph: .sync) {
                Task { await audioSession.refreshSharedQueue() }
            }
            .disabled(audioSession.isRemoteOutput)
            .accessibilityIdentifier("refreshSharedQueueButton")
            if audioSession.conflict != nil {
                Text("The server and this device have different queues. Choose which one to keep.")
                Button("Use server queue") { Task { await audioSession.useServerSession() } }
                    .disabled(audioSession.isRemoteOutput)
                    .accessibilityIdentifier("libraryUseServerQueueButton")
                Button("Keep this device's queue") { Task { await audioSession.keepDeviceSession() } }
                    .disabled(audioSession.isRemoteOutput)
                    .accessibilityIdentifier("libraryKeepDeviceQueueButton")
            }
            if let model = audioSession.playback {
                Button("Open player", glyph: .music, variant: .solid) { playback = model }
            }
            Button("Play saved queue", glyph: .play, variant: .solid) {
                Task {
                    do { playback = try await audioSession.playSavedQueue() }
                    catch { errorMessage = error.localizedDescription }
                }
            }
            .disabled(audioSession.snapshot.queue.isEmpty || audioSession.busy || audioSession.conflict != nil || audioSession.isRemoteOutput)
            .accessibilityIdentifier("playSavedQueueButton")
            Button("Clear listening queue", glyph: .trash, role: .destructive) { clearingQueue = true }
                .disabled((audioSession.snapshot.queue.isEmpty && audioSession.snapshot.entries.isEmpty) || audioSession.conflict != nil)
                .accessibilityIdentifier("clearSavedQueueButton")
            if let message = audioSession.message { Text(message).foregroundStyle(.secondary) }
        }
        if !audioSession.pendingEntries.isEmpty {
            Section("Requested albums") {
                Text(audioSession.pendingEntries.map {
                    "\($0.name ?? $0.title ?? "Requested album")\n\($0.message ?? $0.state ?? $0.status ?? "Waiting for import")"
                }.joined(separator: "\n\n"))
                Picker("When an album is unavailable", selection: Binding(get: { audioSession.snapshot.unavailablePolicy }, set: audioSession.setUnavailablePolicy)) {
                    Text("Skip it and continue").tag("skip")
                    Text("Wait here").tag("wait")
                }
                .pickerStyle(.menu)
                .disabled(audioSession.isSonos)
                Button("Refresh requested albums", glyph: .sync) {
                    Task { await audioSession.refreshRequestedAlbums() }
                }
            }
        }
    }

    private func playDownloads(at index: Int) {
        let items = store.offline.downloadedItems.filter(\.isAudio)
        guard items.indices.contains(index) else { return }
        Task {
            do {
                let request = try await store.preparePlayback(for: items[index])
                let model = PlaybackModel(request: request, store: store, queue: items, queueIndex: index)
                try await audioSession.adoptForPlayback(model)
                playback = model
            } catch { errorMessage = error.localizedDescription }
        }
    }
}

private struct NativePlaylistDetail: View {
    let store: LuminaStore
    let playlist: MusicPlaylist
    @Environment(\.dismiss) private var dismiss
    @State private var tracks: [LuminaCore.MusicTrack] = []
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var deleting = false
    @State private var editing = false
    @State private var playback: PlaybackModel?

    private var current: MusicPlaylist { store.music.playlists.first { $0.id == playlist.id } ?? playlist }

    var body: some View {
        NativeMusicList {
            Section {
                if current.rules != nil { Label("Smart selection · updates with your library", glyph: .discover) }
                if !current.canEdit { Label("Shared by another household member · read only", glyph: .users) }
                if loading { ProgressView("Loading playlist…") }
                if let errorMessage { Text(errorMessage).foregroundStyle(.secondary) }
                Button("Refresh playlist", glyph: .sync) { Task { await load() } }
                if current.canEdit {
                    Button("Edit playlist", glyph: .edit) { editing = true }
                        .disabled(loading)
                        .accessibilityIdentifier("editMusicPlaylistButton")
                    Button("Delete playlist", glyph: .trash, role: .destructive) { deleting = true }
                }
            }
            NativeMusicTrackSections(store: store, tracks: tracks) { playback = $0 }
        }
        .navigationTitle(current.name)
        .accessibilityIdentifier("musicPlaylistDetailScreen")
        #if os(iOS)
        .standardPhonePushedChrome(store: store)
        #endif
        .nativeAudioPlayer(playback: $playback, store: store)
        .task(id: playlist.id) { await load() }
        .sheet(isPresented: $editing) {
            NativePlaylistEditor(store: store, playlist: current, initialTracks: tracks.map(\.mediaItem)) {
                Task { await load() }
            }
        }
        .confirmationDialog("Delete \(current.name)?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete playlist", role: .destructive) {
                Task {
                    do { try await store.music.deletePlaylist(current); dismiss() }
                    catch { errorMessage = error.localizedDescription }
                }
            }
        }
    }

    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        await store.music.refresh()
        do { tracks = try await store.music.tracks(in: current) }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct NativeMusicMixView: View {
    let store: LuminaStore
    let kind: String
    let title: String
    var seedID: String?
    @State private var tracks: [LuminaCore.MusicTrack] = []
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var playback: PlaybackModel?

    var body: some View {
        NativeMusicList {
            Section {
                if loading { ProgressView("Building your mix…") }
                if let errorMessage { Text(errorMessage).foregroundStyle(.secondary) }
                Button("Refresh mix", glyph: .sync) { Task { await load() } }
                if !loading && tracks.isEmpty {
                    Text("No tracks match yet. Try another mix or add more music to your library.").foregroundStyle(.secondary)
                }
            }
            NativeMusicTrackSections(store: store, tracks: tracks) { playback = $0 }
        }
        .navigationTitle(title)
        #if os(iOS)
        .standardPhonePushedChrome(store: store)
        #endif
        .nativeAudioPlayer(playback: $playback, store: store)
        .task { await load() }
    }

    private func load() async {
        loading = true; errorMessage = nil
        defer { loading = false }
        do { tracks = try await store.music.mix(kind, seedID: seedID) }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct NativeMusicTrackSections: View {
    let store: LuminaStore
    let tracks: [LuminaCore.MusicTrack]
    let onPlayback: (PlaybackModel) -> Void
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var errorMessage: String?
    @State private var preparing = false

    var body: some View {
        Section {
            ViewThatFits(in: .horizontal) {
                HStack { transportActions }
                VStack(alignment: .leading) { transportActions }
            }
            .buttonStyle(LuminaQuietActionStyle())
            .disabled(tracks.isEmpty || preparing)
            NativeDownloadControls(items: tracks.map(\.mediaItem), store: store)
            if let errorMessage { Text(errorMessage).foregroundStyle(.secondary) }
        }
        Section("Tracks · \(tracks.count)") {
            ForEach(tracks) { track in
                Button { play(at: tracks.firstIndex(of: track) ?? 0, shuffle: false) } label: {
                    NativeMusicTrackLabel(item: track.mediaItem, store: store)
                }
                .disabled(preparing)
                .accessibilityIdentifier("playlistTrack-\(track.nativeID)")
            }
        }
    }

    @ViewBuilder private var transportActions: some View {
        Button("Play", glyph: .play, variant: .solid) { play(at: 0, shuffle: false) }
        Button("Shuffle", glyph: .shuffle) { play(at: 0, shuffle: true) }
    }

    private func play(at index: Int, shuffle: Bool) {
        guard tracks.indices.contains(index), !preparing else { return }
        preparing = true
        Task {
            defer { preparing = false }
            do { onPlayback(try await audioSession.play(tracks: tracks, index: index, store: store, shuffle: shuffle)) }
            catch { errorMessage = error.localizedDescription }
        }
    }
}

private struct NativeMusicTrackLabel: View {
    let item: MediaItem
    let store: LuminaStore
    var body: some View {
        HStack(spacing: 14) {
            CachedArtworkImage(url: store.imageURL(for: item, maxWidth: 160)) { image in
                image.resizable().scaledToFill()
            } placeholder: {
                LuminaIcon(glyph: .music, variant: .solid, size: 24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
                .frame(width: 58, height: 58)
                .clipShape(.rect(cornerRadius: 9))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name).font(.headline).lineLimit(2)
                Text(item.displaySubtitle).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 4)
    }
}

struct NativePlaylistEditor: View {
    let store: LuminaStore
    let playlist: MusicPlaylist?
    let initialTracks: [MediaItem]
    var onSaved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var name = ""
    @State private var shared = false
    @State private var smart = false
    @State private var genres = ""
    @State private var yearFrom = ""
    @State private var yearTo = ""
    @State private var favorites = false
    @State private var unplayed = false
    @State private var currentArtist = false
    @State private var artistID: String?
    @State private var tracks: [MediaItem] = []
    @State private var saving = false
    @State private var errorMessage: String?
    @FocusState private var focusedField: Field?
    private enum Field: Hashable { case name, genres, yearFrom, yearTo }

    private var validYears: Bool {
        let lower = Int(yearFrom), upper = Int(yearTo)
        return (yearFrom.isEmpty || lower.map { (1000...2200).contains($0) } == true)
            && (yearTo.isEmpty || upper.map { (1000...2200).contains($0) } == true)
            && (lower == nil || upper == nil || lower! <= upper!)
    }

    var body: some View {
        NavigationStack {
            NativeMusicList(horizontalInset: 40) {
                Section("Playlist") {
                    TextField("Name", text: $name)
                        .focused($focusedField, equals: .name)
                        .submitLabel(.done)
                        .accessibilityIdentifier("musicPlaylistNameField")
                    Toggle("Share with household", isOn: $shared)
                        .tint(LuminaTheme.brand)
                        .accessibilityIdentifier("musicPlaylistSharedToggle")
                    if playlist == nil {
                        Toggle("Smart playlist", isOn: $smart)
                            .tint(LuminaTheme.brand)
                            .accessibilityIdentifier("musicPlaylistSmartToggle")
                    }
                }
                if smart {
                    Section("Choose music automatically") {
                        TextField("Genres, separated by commas", text: $genres)
                            .focused($focusedField, equals: .genres)
                            .submitLabel(.done)
                            .accessibilityIdentifier("musicPlaylistGenresField")
                        TextField("From year", text: $yearFrom)
                            .focused($focusedField, equals: .yearFrom)
                            .submitLabel(.done)
                        TextField("Through year", text: $yearTo)
                            .focused($focusedField, equals: .yearTo)
                            .submitLabel(.done)
                        Toggle("Favorites only", isOn: $favorites)
                            .tint(LuminaTheme.brand)
                        Toggle("Unplayed only", isOn: $unplayed)
                            .tint(LuminaTheme.brand)
                        Toggle("Current artist", isOn: $currentArtist)
                            .tint(LuminaTheme.brand)
                            .disabled(artistID == nil)
                        if !validYears { Text("Use years from 1000 to 2200, with the end after the start.").foregroundStyle(.secondary) }
                    }
                } else {
                    Section("Tracks · \(tracks.count)") {
                        Button("Add listening queue", glyph: .queue) {
                            var seen = Set(tracks.map(\.id))
                            tracks.append(contentsOf: (audioSession.playback?.queue ?? []).filter { seen.insert($0.id).inserted })
                        }
                        .disabled(audioSession.playback?.queue.isEmpty != false)
                        if tracks.isEmpty { Text("Start empty or save your current listening queue.").foregroundStyle(.secondary) }
                        ForEach(tracks) { item in
                            HStack {
                                Text(item.name).frame(maxWidth: .infinity, alignment: .leading)
                                Menu {
                                    Button("Move up") { move(item, offset: -1) }
                                        .disabled(tracks.first?.id == item.id)
                                    Button("Move down") { move(item, offset: 1) }
                                        .disabled(tracks.last?.id == item.id)
                                    Button("Remove", role: .destructive) { tracks.removeAll { $0.id == item.id } }
                                } label: {
                                    Label("Track actions", glyph: .moreHorizontal)
                                }
                                .labelStyle(.iconOnly)
                            }
                        }
                    }
                }
                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.secondary).accessibilityIdentifier("musicPlaylistSaveError")
                    }
                }
            }
            .navigationTitle(playlist == nil ? "New playlist" : "Edit playlist")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel", role: .cancel) { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Saving…" : "Save") { save() }
                        .disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !validYears)
                        .accessibilityIdentifier("saveMusicPlaylistButton")
                }
            }
            .onSubmit { focusedField = nil }
        }
        .task {
            name = playlist?.name ?? ""; shared = playlist?.shared ?? false
            let rules = playlist?.rules
            smart = rules != nil; genres = rules?.genres.joined(separator: ", ") ?? ""
            yearFrom = rules?.yearFrom.map(String.init) ?? ""; yearTo = rules?.yearTo.map(String.init) ?? ""
            favorites = rules?.favorites ?? false; unplayed = rules?.unplayed ?? false
            currentArtist = rules?.artistID != nil
            var seen = Set<String>()
            tracks = initialTracks.filter { seen.insert($0.id).inserted }
            artistID = rules?.artistID
            if artistID == nil, let item = initialTracks.first ?? audioSession.playback?.request.item {
                artistID = try? await store.music.track(for: item).artistID
            }
        }
    }

    private func move(_ item: MediaItem, offset: Int) {
        guard let index = tracks.firstIndex(of: item), tracks.indices.contains(index + offset) else { return }
        tracks.swapAt(index, index + offset)
    }

    private func save() {
        saving = true; errorMessage = nil
        Task {
            defer { saving = false }
            do {
                let rules = smart ? MusicPlaylistRules(
                    genres: genres.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty },
                    artistID: currentArtist ? artistID : nil, yearFrom: Int(yearFrom), yearTo: Int(yearTo),
                    favorites: favorites, unplayed: unplayed
                ) : nil
                _ = try await store.music.savePlaylist(id: playlist?.id, name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                                                       shared: shared, itemIDs: smart ? [] : tracks.map(\.id),
                                                       rules: rules, revision: playlist?.revision)
                onSaved(); dismiss()
            } catch { errorMessage = error.localizedDescription }
        }
    }
}

struct NativeDownloadControls: View {
    let items: [MediaItem]
    let store: LuminaStore
    var title: String? = nil
    @State private var batch = NativeMusicDownloadBatch()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if batch.running {
                ProgressView(value: Double(batch.completed), total: Double(max(1, batch.total)))
                Button("Cancel saving · \(batch.completed) of \(batch.total)") { batch.cancel() }
                    .accessibilityIdentifier("cancelAudioDownloadsButton")
            } else {
                Button(title ?? (items.count == 1 ? "Download track" : "Download tracks · \(min(100, items.count))"), glyph: .download) {
                    batch.start(items: items, store: store)
                }
                .disabled(items.isEmpty)
                .accessibilityIdentifier("downloadAudioButton")
            }
            if let message = batch.message { Text(message).font(.caption).foregroundStyle(.secondary) }
        }
    }
}

@MainActor @Observable
private final class NativeMusicDownloadBatch {
    var running = false
    var completed = 0
    var total = 0
    var message: String?
    private var task: Task<Void, Never>?

    func cancel() { task?.cancel() }

    func start(items: [MediaItem], store: LuminaStore) {
        guard !running else { return }
        var seen = Set<String>()
        let selected = Array(items.filter { $0.isAudio && seen.insert($0.id).inserted }.prefix(100))
        let identity = store.music.sessionIdentity
        running = true; completed = 0; total = selected.count; message = nil
        task = Task { [self] in
            defer { running = false; task = nil }
            for item in selected {
                guard !Task.isCancelled, store.music.sessionIdentity == identity else { message = "Saving cancelled. Completed downloads are kept."; return }
                do {
                    let request = try store.music.downloadRequest(for: item)
                    await store.offline.download(item: item, request: request)
                    guard !Task.isCancelled, store.music.sessionIdentity == identity else { return }
                    if let url = store.offline.localURL(for: item.id) {
                        let playable = (try? await AVURLAsset(url: url).load(.isPlayable)) == true
                        guard !Task.isCancelled, store.music.sessionIdentity == identity else { return }
                        if !playable {
                            store.offline.remove(id: item.id)
                            let compatible = try store.music.downloadRequest(for: item, format: "wav")
                            await store.offline.download(item: item, request: compatible)
                            guard !Task.isCancelled, store.music.sessionIdentity == identity else { return }
                            if let converted = store.offline.localURL(for: item.id) {
                                let playable = (try? await AVURLAsset(url: converted).load(.isPlayable)) == true
                                guard !Task.isCancelled, store.music.sessionIdentity == identity else { return }
                                if !playable {
                                    store.offline.remove(id: item.id)
                                    message = "This track could not be saved in a playable format."
                                }
                            }
                        }
                    }
                    if store.offline.localURL(for: item.id) != nil {
                        completed += 1
                        _ = try? await store.music.lyrics(for: item)
                        if !Task.isCancelled, store.music.sessionIdentity == identity,
                           let artwork = store.imageURL(for: item), artwork.scheme != "file" {
                            await store.offline.downloadArtwork(item: item, request: URLRequest(url: artwork))
                        }
                    } else { message = "Some tracks could not be saved. Open Downloads to review storage and retry." }
                } catch { message = error.localizedDescription }
            }
            if message == nil { message = "\(completed) tracks available offline." }
        }
    }
}
