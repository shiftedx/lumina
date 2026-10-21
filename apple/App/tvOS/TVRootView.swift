import SwiftUI
import LuminaCore

/// The living-room client: a small native shell over the shared Lumina session and library store.
public struct TVRootView: View {
    private let store: LuminaStore
    @State private var path: [MediaItem] = []
    @State private var playbackRequest: PlaybackRequest?
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
                    TVLibraryView(store: store) { item in
                        path.append(item)
                    } onPlayback: { request in
                        playbackRequest = request
                    } onSignOut: {
                        path.removeAll()
                    }
                }
            }
            .navigationDestination(for: MediaItem.self) { item in
                if item.type == "MusicAlbum" || item.type == "MusicArtist" {
                    TVMusicCollectionView(item: item, store: store) { path.append($0) }
                } else if item.isCollection {
                    TVVideoCollectionView(item: item, store: store) { path.append($0) }
                } else {
                    TVDetailView(item: item, store: store) { request in
                        playbackRequest = request
                    }
                }
            }
        }
        .tint(LuminaTheme.gold)
        .preferredColorScheme(.dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil { audioSession.stop() }
        }
        .task(id: store.session?.accessToken) {
            if store.session != nil { await audioSession.restore(store: store) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                audioSession.resumeAfterActivation()
                Task { await audioSession.refreshSharedQueue() }
            } else if phase == .background {
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.session != nil { NativeNowPlayingBar(store: store) }
        }
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
        .environment(audioSession)
    }
}

private struct TVConnectionView: View {
    let store: LuminaStore
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @FocusState private var focusedField: Field?

    private enum Field: Hashable {
        case server, username, password, connect
    }

    var body: some View {
        ZStack {
            LuminaTheme.paper.ignoresSafeArea()

            VStack(alignment: .leading, spacing: 28) {
                Spacer()

                Text("Lumina")
                    .font(LuminaTheme.editorial(72, relativeTo: .largeTitle, italic: true))
                    .foregroundStyle(LuminaTheme.ink)

                Text("Bring your household library to the biggest screen.")
                    .font(.title2)
                    .foregroundStyle(LuminaTheme.muted)

                VStack(alignment: .leading, spacing: 18) {
                    TextField("Server address", text: $server)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("serverURLField")
                        .focused($focusedField, equals: .server)

                    TextField("Lumina username", text: $username)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("usernameField")
                        .focused($focusedField, equals: .username)

                    SecureField("Password or app password", text: $password)
                        .textContentType(.password)
                        .accessibilityIdentifier("passwordField")
                        .focused($focusedField, equals: .password)

                    if let error = store.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.body)
                            .accessibilityLabel("Connection error: \(error)")
                            .accessibilityIdentifier("connectionError")
                    }

                    Button {
                        store.clearError()
                        Task {
                            await store.connect(server: server, username: username, password: password)
                            password = ""
                        }
                    } label: {
                        HStack(spacing: 12) {
                            if store.isBusy { ProgressView() }
                            Text(store.isBusy ? "Connecting…" : "Connect")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .accessibilityIdentifier("connectButton")
                    .focused($focusedField, equals: .connect)
                    .disabled(store.isBusy || server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || username.isEmpty || password.isEmpty)
                }
                .frame(maxWidth: 720)

                Text("First enable Apps like Infuse in Lumina on the web: Manage Server → Connections → Metadata. Sign in with your Lumina account. If it uses two-factor authentication, create an app password in Connected apps.")
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
                    .frame(maxWidth: 820, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer()
            }
            .padding(.horizontal, 140)
            .padding(.vertical, 72)
        }
        .defaultFocus($focusedField, .server)
    }
}

private enum TVDestination: String, CaseIterable {
    case home, video, audio
    var title: String { rawValue.capitalized }
    var symbol: String { switch self { case .home: "house"; case .video: "play.rectangle"; case .audio: "music.note" } }
}

private struct TVLibraryView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let onSignOut: () -> Void
    @State private var destination: TVDestination = .home
    @State private var accountPresented = false
    @State private var licensesPresented = false
    @FocusState private var focusedDestination: TVDestination?

    var body: some View {
        VStack(spacing: 16) {
            header
                .padding(.horizontal, 24)
                .focusSection()
            Group {
                switch destination {
                case .home:
                    TVHomeView(store: store, onOpen: onOpen, onPlayback: onPlayback) { destination = $0 }
                case .video:
                    TVVideoView(store: store, onOpen: onOpen)
                case .audio:
                    TVAudioView(store: store, onOpen: onOpen)
                }
            }
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .onExitCommand { focusedDestination = destination }
        .sheet(isPresented: $licensesPresented) { PlaybackLicensesView() }
    }

    private var header: some View {
        HStack(spacing: 28) {
            HStack(spacing: 10) {
                LuminaBrandMark().frame(width: 42, height: 26)
                Text("LUMINA")
                    .font(LuminaTheme.editorial(36, relativeTo: .largeTitle))
                    .tracking(3)
                    .foregroundStyle(LuminaTheme.ink)
            }
            Spacer()
            ForEach(TVDestination.allCases, id: \.self) { tab in
                Button { destination = tab } label: {
                    Text(tab.title)
                        .font(.system(size: 26, weight: destination == tab ? .semibold : .regular))
                        .foregroundStyle(destination == tab || focusedDestination == tab ? LuminaTheme.ink : LuminaTheme.muted)
                        .frame(minWidth: 144, minHeight: 64)
                        .background(focusedDestination == tab ? LuminaTheme.ink.opacity(0.12) : .clear, in: .rect(cornerRadius: 6))
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(destination == tab ? LuminaTheme.gold : .clear).frame(height: 2)
                        }
                }
                .buttonStyle(.plain)
                .scaleEffect(focusedDestination == tab ? 1.04 : 1)
                .focused($focusedDestination, equals: tab)
                .accessibilityAddTraits(destination == tab ? .isSelected : [])
                .accessibilityIdentifier("\(tab.rawValue)Tab")
            }
            Spacer()
            Button { accountPresented = true } label: {
                Image(systemName: "person.crop.circle")
            }
            .buttonStyle(.bordered)
            .accessibilityLabel("Account")
            .accessibilityIdentifier("accountMenuButton")
            .confirmationDialog("Account", isPresented: $accountPresented, titleVisibility: .visible) {
                Button("Open-source licenses") { licensesPresented = true }
                Button("Refresh library") { Task { await store.refresh() } }
                Button("Sign out", role: .destructive) {
                    Task { await store.signOut(); onSignOut() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                if let username = store.session?.username { Text(username) }
            }
        }
        .overlay(alignment: .bottom) { Rectangle().fill(LuminaTheme.muted.opacity(0.22)).frame(height: 1) }
    }
}

private struct TVHomeView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let onNavigate: (TVDestination) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                HStack(alignment: .firstTextBaseline) {
                    Text(greeting).font(LuminaTheme.editorial(38, relativeTo: .title))
                    Spacer()
                    Text(Date.now.formatted(.dateTime.weekday(.wide).month(.wide).day()).uppercased())
                        .font(.system(size: 18, weight: .medium)).tracking(1.4)
                        .foregroundStyle(LuminaTheme.muted)
                }
                .padding(.horizontal, 80)
                if !store.featuredItems.isEmpty {
                    TVFeaturedView(store: store, onOpen: onOpen, onPlayback: onPlayback)
                        .focusSection()
                } else if store.isHomeBusy {
                    ProgressView("Loading Home…").frame(maxWidth: .infinity, minHeight: 540)
                } else {
                    TVMessageView(title: "Your next great watch starts here", message: store.errorMessage ?? "Add media to Lumina to fill Home with your household library.", actionTitle: "Refresh") {
                        Task { await store.loadHome() }
                    }
                }
                if !store.resumeItems.isEmpty {
                    TVRail(title: "Continue watching", items: store.resumeItems, store: store, onOpen: onOpen)
                        .focusSection()
                }
                if !store.nextUpItems.isEmpty {
                    TVRail(title: "Next up", items: store.nextUpItems, store: store, identifierPrefix: "nextUpItem", onOpen: onOpen)
                        .focusSection()
                }
                if !store.recentItems.isEmpty {
                    TVRail(title: "New in your library", items: store.recentItems, store: store, poster: true, identifierPrefix: "recentItem", onOpen: onOpen)
                        .focusSection()
                }
                HStack(spacing: 40) {
                    destinationButton(.video, subtitle: "Films, episodes, and your video collection")
                    destinationButton(.audio, subtitle: "Albums, artists, and tracks")
                }
                .padding(.horizontal, 80)
                .focusSection()
            }
            .padding(.bottom, 80)
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .accessibilityIdentifier("homeScreen")
        .task { await store.loadHome() }
    }

    private var greeting: String {
        switch Calendar.current.component(.hour, from: .now) {
        case 5..<12: "Good morning."
        case 12..<18: "Good afternoon."
        default: "Good evening."
        }
    }

    private func destinationButton(_ destination: TVDestination, subtitle: String) -> some View {
        Button { onNavigate(destination) } label: {
            HStack(spacing: 24) {
                Image(systemName: destination.symbol)
                    .font(.system(size: 44))
                    .foregroundStyle(LuminaTheme.spotlight)
                VStack(alignment: .leading, spacing: 12) {
                    Text(destination.title).font(LuminaTheme.editorial(38, relativeTo: .title))
                    Text(subtitle).font(.body).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.forward")
            }
            .padding(32)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.card)
        .accessibilityIdentifier("home\(destination.title)Button")
    }
}

private struct TVVideoView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @State private var query = ""
    @State private var kind: VideoBrowseKind = .all
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersPresented = false
    @FocusState private var focusedItemID: String?

    private var requestKey: String { "\(kind.rawValue)-\(sort.rawValue)-\(query)-\(filters.genre)-\(filters.year ?? 0)-\(filters.unwatchedOnly)-\(filters.favoritesOnly)" }
    private var hasFilters: Bool { filters != VideoFilters() }

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            HStack(spacing: 28) {
                Picker("Show", selection: $kind) {
                    ForEach(VideoBrowseKind.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)
                .frame(width: 290)
                .accessibilityIdentifier("videoKindPicker")
                if query.isEmpty {
                    Picker("Sort", selection: $sort) {
                        ForEach(VideoSort.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.menu)
                    .frame(width: 320)
                } else {
                    Text("By relevance").font(.body).foregroundStyle(LuminaTheme.muted).frame(width: 320)
                }
                Spacer()
                TextField("Search video", text: $query)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .frame(width: 400)
                    .accessibilityIdentifier("searchField")
            }
            .padding(.horizontal, 24)
            .focusSection()

            HStack(spacing: 28) {
                Button { filtersPresented = true } label: {
                    Label(hasFilters ? "Filters applied" : "Filters", systemImage: "line.3.horizontal.decrease")
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("videoFiltersButton")
                if hasFilters {
                    Text(filterSummary).font(.caption).foregroundStyle(LuminaTheme.muted)
                    Spacer()
                    Button("Clear filters") { filters = VideoFilters() }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("videoClearFiltersButton")
                }
            }
            .padding(.horizontal, 24)
            .focusSection()

            ScrollView {
                if store.isBusy && store.items.isEmpty {
                    ProgressView("Loading video…").frame(maxWidth: .infinity, minHeight: 360)
                } else if store.items.isEmpty {
                    TVMessageView(title: query.isEmpty && !hasFilters ? "No videos here yet" : "No matching videos", message: store.errorMessage ?? "Try another search or choose a different category.", actionTitle: "Refresh") { Task { await reload() } }
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(store.items) { item in
                            TVMediaCard(item: item, store: store, poster: true) { onOpen(item) }
                                .focused($focusedItemID, equals: item.id)
                                .accessibilityIdentifier("libraryItem-\(item.id)")
                                .onAppear {
                                    if store.hasMore && item.id == store.items.suffix(6).first?.id { Task { await store.loadMore() } }
                                }
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.top, 24)
                    .focusSection()
                    if store.hasMore {
                        Button("Show more") { Task { await store.loadMore() } }
                            .buttonStyle(.bordered)
                            .padding(32)
                    }
                    if let error = store.errorMessage {
                        Text(error).foregroundStyle(LuminaTheme.spotlight).padding(24)
                    }
                }
            }
            .accessibilityIdentifier("libraryScreen")
        }
        .sheet(isPresented: $filtersPresented) {
            TVVideoFiltersView(filters: filters) { filters = $0 }
        }
        .task(id: requestKey) {
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await reload()
        }
    }

    private var filterSummary: String {
        [filters.genre.isEmpty ? nil : filters.genre, filters.year.map(String.init), filters.unwatchedOnly ? "Unwatched" : nil, filters.favoritesOnly ? "Favorites" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func reload() async { await store.loadLibrary(search: query, kind: kind, sort: sort, filters: filters) }
}

private struct TVVideoFiltersView: View {
    let filters: VideoFilters
    let onApply: (VideoFilters) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var genre = ""
    @State private var year = ""
    @State private var unwatchedOnly = false
    @State private var favoritesOnly = false

    private var validYear: Bool { year.isEmpty || (Int(year).map { (1...9999).contains($0) } ?? false) }

    var body: some View {
        NavigationStack {
            Form {
                Section("Find your next watch") {
                    TextField("Genre", text: $genre)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("videoGenreField")
                    TextField("Year", text: $year)
                        .accessibilityIdentifier("videoYearField")
                    if !validYear { Text("Enter a year, or leave it blank for all years.").foregroundStyle(LuminaTheme.spotlight) }
                    Toggle("Unwatched only", isOn: $unwatchedOnly)
                        .accessibilityIdentifier("videoUnwatchedToggle")
                    Toggle("Favorites only", isOn: $favoritesOnly)
                        .accessibilityIdentifier("videoFavoritesToggle")
                }
                Section {
                    Button("Apply filters") {
                        onApply(VideoFilters(genre: genre.trimmingCharacters(in: .whitespacesAndNewlines), year: Int(year), unwatchedOnly: unwatchedOnly, favoritesOnly: favoritesOnly))
                        dismiss()
                    }
                    .disabled(!validYear)
                    .accessibilityIdentifier("videoApplyFiltersButton")
                    Button("Reset filters") {
                        genre = ""; year = ""; unwatchedOnly = false; favoritesOnly = false
                    }
                    Button("Cancel", role: .cancel) { dismiss() }
                }
            }
            .navigationTitle("Video filters")
        }
        .onAppear {
            genre = filters.genre
            year = filters.year.map(String.init) ?? ""
            unwatchedOnly = filters.unwatchedOnly
            favoritesOnly = filters.favoritesOnly
        }
    }
}

private struct TVVideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @State private var children = [MediaItem]()
    @State private var refreshedItem: MediaItem?
    @State private var loading = true
    @State private var errorMessage: String?

    private var currentItem: MediaItem { refreshedItem ?? item }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 40) {
                HStack(alignment: .top, spacing: 48) {
                    TVArtwork(item: currentItem, store: store, aspect: 2 / 3).frame(width: 280)
                    VStack(alignment: .leading, spacing: 24) {
                        Text(item.type == "Series" ? "TV show" : item.seriesName ?? "Season")
                            .font(.body).foregroundStyle(LuminaTheme.spotlight)
                        Text(currentItem.name).font(LuminaTheme.editorial(64, relativeTo: .largeTitle))
                        Text(currentItem.displaySubtitle).font(.body).foregroundStyle(LuminaTheme.muted)
                        if let overview = currentItem.overview, !overview.isEmpty {
                            Text(overview).font(.body).lineLimit(5)
                        }
                        TVItemStateActions(item: currentItem, store: store) {
                            refreshedItem = $0
                            Task { await load() }
                        }
                        .disabled(loading)
                    }
                }
                .focusSection()
                Text(item.type == "Series" ? "Seasons" : "Episodes")
                    .font(LuminaTheme.editorial(42, relativeTo: .title))
                if loading {
                    ProgressView("Loading \(item.type == "Series" ? "seasons" : "episodes")…")
                } else if let errorMessage {
                    TVMessageView(title: "Could not load this collection", message: errorMessage, actionTitle: "Try again") { Task { await load() } }
                } else if children.isEmpty {
                    TVMessageView(title: item.type == "Series" ? "No seasons yet" : "No episodes yet", message: "Refresh after adding this show to your Lumina library.", actionTitle: "Refresh") { Task { await load() } }
                } else if item.type == "Series" {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(children) { child in
                            TVMediaCard(item: child, store: store, poster: true) { onOpen(child) }
                                .accessibilityIdentifier("videoCollectionItem-\(child.id)")
                        }
                    }
                    .padding(.top, 20)
                    .focusSection()
                } else {
                    LazyVStack(spacing: 32) {
                        ForEach(children) { child in
                            Button { onOpen(child) } label: {
                                HStack(spacing: 32) {
                                    TVArtwork(item: child, store: store, aspect: 16 / 9).frame(width: 300)
                                    VStack(alignment: .leading, spacing: 12) {
                                        Text(child.name).font(.headline)
                                        Text(child.displaySubtitle).font(.caption).foregroundStyle(.secondary)
                                        if let overview = child.overview { Text(overview).font(.body).lineLimit(2) }
                                    }
                                    Spacer()
                                    if child.userData?.played == true { Image(systemName: "checkmark.circle.fill").accessibilityLabel("Watched") }
                                    Image(systemName: "chevron.forward")
                                }
                                .padding(16)
                            }
                            .buttonStyle(.card)
                            .accessibilityIdentifier("videoCollectionItem-\(child.id)")
                        }
                    }
                    .focusSection()
                }
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
            .padding(.bottom, 60)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("videoCollectionScreen")
        .task(id: item.id) { await load() }
    }

    private func load() async {
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            refreshedItem = try await store.loadItem(item)
            children = try await store.loadChildren(of: item)
        } catch { errorMessage = error.localizedDescription }
    }
}

private struct TVAudioView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @State private var kind: MusicBrowseKind = .albums
    @State private var query = ""
    @FocusState private var focusedItemID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            NativeMusicLibraryLinks(store: store)
                .padding(.horizontal, 24)
            ScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if kind == .albums, let featured = store.audioItems.first(where: { $0.type == "MusicAlbum" }) {
                        Button { onOpen(featured) } label: {
                            MusicCollectionSpotlight(item: featured, store: store)
                        }
                        .buttonStyle(.plain)
                        .focused($focusedItemID, equals: "audioFeaturedAlbum")
                        .background(focusedItemID == "audioFeaturedAlbum" ? LuminaTheme.ink.opacity(0.08) : .clear)
                        .overlay {
                            RoundedRectangle(cornerRadius: 6)
                                .strokeBorder(focusedItemID == "audioFeaturedAlbum" ? LuminaTheme.gold : .clear, lineWidth: 2)
                        }
                        .accessibilityLabel("Open album \(featured.name), \(featured.displaySubtitle)")
                        .accessibilityIdentifier("audioFeaturedAlbum")
                        .padding(.horizontal, 24)
                        .padding(.top, 16)
                        .focusSection()
                    }
                    HStack(spacing: 32) {
                        Picker("Browse", selection: $kind) {
                            ForEach(MusicBrowseKind.allCases, id: \.self) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 700)
                        Spacer()
                        TextField("Search music", text: $query)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .frame(width: 440)
                            .accessibilityIdentifier("audioSearchField")
                    }
                    .padding(.horizontal, 24)
                    .focusSection()
                    if store.isAudioBusy && store.audioItems.isEmpty {
                        ProgressView("Loading music…").frame(maxWidth: .infinity, minHeight: 360)
                    } else if store.audioItems.isEmpty {
                        TVMessageView(title: "Your music library", message: store.errorMessage ?? "Add music to Lumina, or try another search.", actionTitle: "Refresh") { Task { await store.loadAudio(kind: kind, search: query) } }
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 290), spacing: 32, alignment: .top)], spacing: 48) {
                            ForEach(store.audioItems) { item in
                                TVMediaCard(item: item, store: store) { onOpen(item) }
                                    .accessibilityIdentifier("audioItem-\(item.id)")
                                    .onAppear {
                                        if store.audioHasMore && item.id == store.audioItems.suffix(6).first?.id { Task { await store.loadMoreAudio() } }
                                    }
                            }
                        }
                        .padding(.horizontal, 24)
                        .padding(.top, 24)
                        .focusSection()
                        if store.audioHasMore {
                            Button("Show more") { Task { await store.loadMoreAudio() } }
                                .buttonStyle(.bordered).padding(32)
                        }
                        if let error = store.errorMessage {
                            Text(error).foregroundStyle(LuminaTheme.spotlight).padding(24)
                        }
                    }
                }
                .padding(.bottom, 32)
            }
        }
        .accessibilityIdentifier("audioScreen")
        .task(id: "\(kind.rawValue)-\(query)") {
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await store.loadAudio(kind: kind, search: query)
        }
    }
}

private struct TVMusicCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var children = [MediaItem]()
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var albumPlayback: PlaybackModel?
    @State private var preparing = false

    var body: some View {
        ScrollView {
            HStack(alignment: .top, spacing: 72) {
                VStack(alignment: .leading, spacing: 24) {
                    TVArtwork(item: item, store: store, aspect: 1)
                    if !loading, children.contains(where: \.isAudio) {
                        Text("\(children.filter(\.isAudio).count) tracks · \(Int(children.reduce(0) { $0 + $1.durationSeconds } / 60)) min")
                            .font(.system(size: 20)).foregroundStyle(LuminaTheme.muted)
                    }
                }
                .frame(width: 380)
                VStack(alignment: .leading, spacing: 28) {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(item.type == "MusicArtist" ? "ARTIST" : "ALBUM")
                            .font(.system(size: 18, weight: .medium)).tracking(2)
                            .foregroundStyle(LuminaTheme.gold)
                        Text(item.name).font(LuminaTheme.editorial(64, relativeTo: .largeTitle))
                            .fixedSize(horizontal: false, vertical: true)
                        Text(item.displaySubtitle).font(.system(size: 24)).foregroundStyle(LuminaTheme.muted)
                        if let overview = item.overview {
                            Text(overview).font(.system(size: 22)).foregroundStyle(LuminaTheme.muted).lineLimit(4)
                        }
                    }
                    if children.contains(where: \.isAudio) {
                        HStack(spacing: 24) {
                            Button("Play album", systemImage: "play.fill") { playAlbum(shuffle: false) }
                                .buttonStyle(.borderedProminent)
                                .accessibilityIdentifier("musicPlayButton")
                            Button("Shuffle", systemImage: "shuffle") { playAlbum(shuffle: true) }
                                .buttonStyle(.bordered)
                                .accessibilityIdentifier("musicShuffleButton")
                        }
                        .disabled(preparing)
                        .focusSection()
                    }
                    if loading {
                        ProgressView("Loading music…")
                    } else if children.isEmpty {
                        TVMessageView(title: "No tracks available", message: errorMessage ?? "Refresh to check this collection again.", actionTitle: "Refresh") { Task { await load() } }
                    } else {
                        TVSectionHeading(title: children.contains(where: \.isAudio) ? "Track listing" : "Albums", count: children.count)
                        if let errorMessage { Text(errorMessage).foregroundStyle(LuminaTheme.spotlight) }
                        LazyVStack(spacing: 12) {
                            ForEach(children) { child in
                                Button { onOpen(child) } label: {
                                    HStack(spacing: 24) {
                                        Text(child.indexNumber.map(String.init) ?? "—")
                                            .font(.system(size: 21)).monospacedDigit()
                                            .foregroundStyle(LuminaTheme.muted).frame(width: 44)
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text(child.name).font(.system(size: 24, weight: .medium))
                                                .foregroundStyle(LuminaTheme.ink)
                                            Text(child.displaySubtitle).font(.system(size: 19)).foregroundStyle(LuminaTheme.muted)
                                        }
                                        Spacer(minLength: 24)
                                        if child.durationSeconds > 0 {
                                            Text(duration(child.durationSeconds)).font(.system(size: 21)).monospacedDigit()
                                                .foregroundStyle(LuminaTheme.muted)
                                        }
                                        Image(systemName: child.isAudio ? "play" : "chevron.forward")
                                            .font(.system(size: 20)).foregroundStyle(LuminaTheme.muted)
                                    }
                                    .padding(.horizontal, 20).padding(.vertical, 18)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .overlay(alignment: .bottom) {
                                        Rectangle().fill(LuminaTheme.muted.opacity(0.2)).frame(height: 1)
                                    }
                                }
                                .buttonStyle(.card)
                                .accessibilityIdentifier("musicItem-\(child.id)")
                            }
                        }
                        .focusSection()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 40).padding(.top, 36).padding(.bottom, 72)
        }
        .background(LuminaTheme.theater)
        .accessibilityIdentifier("musicCollectionScreen")
        .task { await load() }
        .fullScreenCover(isPresented: Binding(get: { albumPlayback != nil }, set: { if !$0 { albumPlayback = nil } }), onDismiss: { Task { await store.refresh() } }) {
            if let albumPlayback { PlaybackScreen(playback: albumPlayback, store: store) }
        }
    }

    private func duration(_ seconds: Double) -> String {
        let value = Int(seconds)
        return value >= 3600 ? String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
            : String(format: "%d:%02d", value / 60, value % 60)
    }

    private func load() async {
        loading = true
        errorMessage = nil
        defer { loading = false }
        do { children = try await store.musicChildren(of: item) }
        catch { errorMessage = "This collection could not load. Check your connection and try again." }
    }

    private func playAlbum(shuffle: Bool) {
        guard !preparing else { return }
        let tracks = children.filter(\.isAudio)
        guard !tracks.isEmpty else { return }
        preparing = true
        errorMessage = nil
        Task {
            defer { preparing = false }
            do {
                let resolved = try await store.music.resolve(items: tracks)
                let first = shuffle ? Int.random(in: 0..<max(1, resolved.count)) : 0
                albumPlayback = try await audioSession.play(tracks: resolved, index: first, store: store, shuffle: shuffle)
            }
            catch { errorMessage = "This album could not start. Check the connection and try again." }
        }
    }
}

private struct TVFeaturedView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    @State private var preparing = false
    @State private var playbackError: String?
    @FocusState private var focusedAction: String?

    var body: some View {
        FeaturedShelf(items: store.featuredItems, store: store, isInteracting: preparing || focusedAction != nil) { item in
            HStack(spacing: 24) {
                Button {
                    guard !preparing else { return }
                    preparing = true
                    playbackError = nil
                    Task {
                        defer { preparing = false }
                        do { onPlayback(try await store.preparePlayback(for: item)) }
                        catch { playbackError = "Playback could not start. Open Details to try compatible video." }
                    }
                } label: {
                    Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(.white)
                .foregroundStyle(LuminaTheme.theater)
                .disabled(preparing)
                .accessibilityIdentifier("featuredPlayButton")
                .focused($focusedAction, equals: "play")

                Button { onOpen(item) } label: {
                    Label("Details", systemImage: "info.circle")
                }
                .buttonStyle(.bordered)
                .tint(.white)
                .accessibilityIdentifier("featuredDetailsButton")
                .focused($focusedAction, equals: "details")
            }
            .controlSize(.large)
            if let playbackError { Text(playbackError).font(.body) }
        }
    }
}

private struct TVSectionHeading: View {
    let title: String
    var count: Int?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 20) {
            Text(title).font(LuminaTheme.editorial(34, relativeTo: .title2))
                .foregroundStyle(LuminaTheme.ink).accessibilityAddTraits(.isHeader)
            Rectangle().fill(LuminaTheme.muted.opacity(0.24)).frame(height: 1)
                .accessibilityHidden(true)
            if let count { Text(count, format: .number).font(.system(size: 18)).foregroundStyle(LuminaTheme.muted) }
        }
    }
}

private struct TVRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    var poster = false
    var identifierPrefix = "resumeItem"
    let onOpen: (MediaItem) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TVSectionHeading(title: title, count: items.count)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 32) {
                    ForEach(items) { item in
                        TVMediaCard(item: item, store: store, poster: poster) { onOpen(item) }
                            .frame(width: item.isAudio || item.type.hasPrefix("Music") ? 260 : poster ? 220 : 400)
                            .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .padding(.vertical, 24)
            }
            .scrollClipDisabled()
        }
        .padding(.horizontal, 80)
    }
}

private struct TVMediaCard: View {
    let item: MediaItem
    let store: LuminaStore
    var poster = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                TVArtwork(item: item, store: store, aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9)
                    .frame(maxWidth: .infinity)
                    .overlay(alignment: .bottom) {
                        if item.resumeSeconds > 0, item.durationSeconds > 0 {
                            ProgressView(value: min(item.resumeSeconds, item.durationSeconds), total: item.durationSeconds)
                                .progressViewStyle(.linear).tint(LuminaTheme.gold)
                                .frame(height: 3).background(.black.opacity(0.4))
                                .accessibilityLabel("Watch progress")
                                .accessibilityValue("\(Int(min(1, item.resumeSeconds / item.durationSeconds) * 100)) percent")
                        }
                    }

                Text(item.name)
                    .font(.system(size: 23, weight: .medium))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(item.displaySubtitle)
                    .font(.system(size: 19))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)

            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.card)
        .accessibilityLabel("\(item.name), \(item.displaySubtitle)")
    }
}

private struct TVArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    let aspect: CGFloat

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                GeometryReader { geometry in
                    if let url = artworkURL {
                        AsyncImage(url: url) { phase in
                            switch phase {
                            case .success(let image):
                                image.resizable().scaledToFill()
                                    .frame(width: geometry.size.width, height: geometry.size.height)
                                    .clipped()
                            case .failure:
                                placeholder
                            default:
                                ZStack { placeholder; ProgressView() }
                            }
                        }
                        .frame(width: geometry.size.width, height: geometry.size.height)
                        .clipped()
                    } else {
                        placeholder
                    }
                }
            }
            .clipShape(.rect(cornerRadius: 4))
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [LuminaTheme.gold.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: item.isAudio || item.type.hasPrefix("Music") ? "music.note" : "film")
                .font(.largeTitle)
                .foregroundStyle(LuminaTheme.ink.opacity(0.7))
        }
    }

    private var artworkURL: URL? {
        aspect > 1 ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item, maxWidth: 760) : store.imageURL(for: item, maxWidth: 760)
    }
}

private struct TVDetailView: View {
    let item: MediaItem
    let store: LuminaStore
    let onPlayback: (PlaybackRequest) -> Void
    @State private var isPreparing = false
    @State private var playbackError: String?
    @State private var refreshedItem: MediaItem?
    @State private var detailError: String?
    @State private var loadingDetail = true

    private var currentItem: MediaItem { refreshedItem ?? item }
    @FocusState private var focusedAction: Action?

    private enum Action: Hashable { case play, convert }

    var body: some View {
        ZStack {
            LuminaTheme.paper.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    MediaBillboard(item: currentItem, store: store, context: "In your library", showsOverview: false) {
                        playbackActions
                        if !item.isAudio {
                            TVItemStateActions(item: currentItem, store: store) { refreshedItem = $0 }
                                .disabled(loadingDetail)
                        }
                        if let detailError {
                            Text(detailError).font(.body).accessibilityIdentifier("detailStateError")
                        }
                        if isPreparing {
                            ProgressView("Preparing playback…")
                        }
                        if let playbackError {
                            Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                                .font(.body)
                        }
                    }
                    if let overview = currentItem.overview, !overview.isEmpty {
                        Text("The story")
                            .font(LuminaTheme.editorial(28, relativeTo: .headline, italic: true))
                            .foregroundStyle(LuminaTheme.ink)
                            .padding(.horizontal, 82)
                        Text(overview)
                            .font(.body)
                            .foregroundStyle(LuminaTheme.ink)
                            .frame(maxWidth: 1000, alignment: .leading)
                            .padding(.horizontal, 82)
                    }
                }
                .padding(.bottom, 60)
            }
            .accessibilityIdentifier("detailScreen")
        }
        .defaultFocus($focusedAction, .play)
        .navigationBarBackButtonHidden(false)
        .task(id: item.id) {
            loadingDetail = true
            detailError = nil
            defer { loadingDetail = false }
            do { refreshedItem = try await store.loadItem(item) }
            catch { detailError = error.localizedDescription }
        }
    }

    private var playbackActions: some View {
        HStack(spacing: 24) {
            Button {
                preparePlayback(forceTranscode: false)
            } label: {
                Label(currentItem.resumeSeconds > 5 ? "Resume" : "Play", systemImage: "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .tint(.white)
            .foregroundStyle(LuminaTheme.theater)
            .controlSize(.large)
            .accessibilityIdentifier("playButton")
            .focused($focusedAction, equals: .play)
            .disabled(isPreparing)

            if !item.isAudio {
                Button {
                    preparePlayback(forceTranscode: true)
                } label: {
                    Label("Convert for compatibility", systemImage: "arrow.triangle.2.circlepath")
                }
                .buttonStyle(.bordered)
                .tint(.white)
                .controlSize(.large)
                .accessibilityIdentifier("convertButton")
                .focused($focusedAction, equals: .convert)
                .disabled(isPreparing)
            }
        }
    }

    private func preparePlayback(forceTranscode: Bool) {
        playbackError = nil
        isPreparing = true
        Task {
            defer { isPreparing = false }
            do {
                onPlayback(try await store.preparePlayback(for: currentItem, forceTranscode: forceTranscode))
            } catch {
                playbackError = error.localizedDescription
            }
        }
    }
}

private struct TVItemStateActions: View {
    let item: MediaItem
    let store: LuminaStore
    let onUpdated: (MediaItem) -> Void
    @State private var updating = false
    @State private var errorMessage: String?

    private var played: Bool { item.userData?.played ?? false }
    private var favorite: Bool { item.userData?.isFavorite ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 24) {
                Button { update(played: !played) } label: {
                    Label(played ? "Mark unwatched" : "Mark watched", systemImage: played ? "checkmark.circle.fill" : "checkmark.circle")
                }
                .accessibilityValue(played ? "Watched" : "Unwatched")
                .accessibilityIdentifier("videoWatchedButton")
                Button { update(favorite: !favorite) } label: {
                    Label(favorite ? "Remove favorite" : "Add favorite", systemImage: favorite ? "heart.fill" : "heart")
                }
                .accessibilityValue(favorite ? "Favorite" : "Not a favorite")
                .accessibilityIdentifier("videoFavoriteButton")
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(updating)
            .focusSection()
            if updating { ProgressView("Saving…") }
            if let errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.body)
                    .accessibilityIdentifier("videoStateError")
            }
        }
    }

    private func update(played: Bool? = nil, favorite: Bool? = nil) {
        updating = true
        errorMessage = nil
        Task {
            defer { updating = false }
            do { onUpdated(try await store.updateItemState(item: item, played: played, favorite: favorite)) }
            catch { errorMessage = error.localizedDescription }
        }
    }
}

private struct TVMessageView: View {
    let title: String
    let message: String
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            Text(title)
                .font(LuminaTheme.editorial(42, relativeTo: .title))
                .italic()
                .foregroundStyle(LuminaTheme.ink)
            Text(message)
                .font(.title3)
                .foregroundStyle(LuminaTheme.muted)
                .multilineTextAlignment(.center)
            Button(actionTitle, action: action)
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
        }
        .frame(maxWidth: .infinity, minHeight: 360)
        .padding(48)
    }
}
