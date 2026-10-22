import SwiftUI
import UIKit
import LuminaCore

@MainActor
struct MobileRootView: View {
    @Bindable var store: LuminaStore
    @State private var audioSession = NativeAudioSession()
    @AppStorage("lumina.appearance") private var appearance = "dark"
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if store.session == nil {
                ConnectionView(store: store)
            } else {
                LibraryShellView(store: store)
            }
        }
        .tint(LuminaTheme.gold)
        .foregroundStyle(LuminaTheme.ink)
        .background(LuminaTheme.paper.ignoresSafeArea())
        .environment(audioSession)
        .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
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
    }
}

@MainActor
private struct ConnectionView: View {
    @Bindable var store: LuminaStore

    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @FocusState private var focusedField: Field?

    private enum Field { case server, username, password }

    private var canConnect: Bool {
        !server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !password.isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Lumina")
                        .font(.system(.largeTitle, design: .serif))
                        .fontWeight(.bold)
                        .italic()
                    Text("Your household library, ready for the room you are in.")
                        .font(.system(.title3, design: .serif))
                        .foregroundStyle(LuminaTheme.muted)
                }

                VStack(alignment: .leading, spacing: 16) {
                    ConnectionField(title: "Server URL") {
                        TextField("https://lumina.example", text: $server)
                            .keyboardType(.URL)
                            .textContentType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($focusedField, equals: .server)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .username }
                            .accessibilityLabel("Server URL")
                            .accessibilityIdentifier("serverURLField")
                    }

                    ConnectionField(title: "Username") {
                        TextField("Household account", text: $username)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($focusedField, equals: .username)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .password }
                            .accessibilityLabel("Username")
                            .accessibilityIdentifier("usernameField")
                    }

                    ConnectionField(title: "Password") {
                        SecureField("Password or app password", text: $password)
                            .textContentType(.password)
                            .focused($focusedField, equals: .password)
                            .submitLabel(.go)
                            .onSubmit { connect() }
                            .accessibilityLabel("Password")
                            .accessibilityIdentifier("passwordField")
                    }
                }

                Button {
                    connect()
                } label: {
                    HStack(spacing: 10) {
                        if store.isBusy {
                            ProgressView()
                                .tint(LuminaTheme.paper)
                        }
                        Text(store.isBusy ? "Connecting…" : "Connect")
                    }
                    .frame(maxWidth: .infinity)
                    .foregroundStyle(LuminaTheme.paper)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!canConnect || store.isBusy)
                .accessibilityIdentifier("connectButton")

                VStack(alignment: .leading, spacing: 10) {
                    Text("Server setup")
                        .font(.headline)
                    Text("An administrator must enable Lumina’s opt-in Jellyfin-compatible API in Manage Server → Connections → Metadata → Apps like Infuse before this app can connect. It is off by default.")
                        .font(.footnote)
                        .foregroundStyle(LuminaTheme.muted)
                    Text("If this account uses two-factor authentication, enter an app password in the password field. Lumina uses it only for this sign-in attempt.")
                        .font(.footnote)
                        .foregroundStyle(LuminaTheme.muted)
                }

                if let message = store.errorMessage, !message.isEmpty {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.gold)
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(.isStaticText)
                        .accessibilityIdentifier("connectionError")
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 36)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .scrollDismissesKeyboard(.interactively)
    }

    private func connect() {
        let server = server.trimmingCharacters(in: .whitespacesAndNewlines)
        let username = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !server.isEmpty, !username.isEmpty, !password.isEmpty, !store.isBusy else { return }

        focusedField = nil
        store.clearError()
        Task {
            await store.connect(server: server, username: username, password: password)
            password = ""
        }
    }
}

@MainActor
private struct ConnectionField<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.subheadline.weight(.semibold))
            content
                .padding(.horizontal, 14)
                .frame(minHeight: 50)
                .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 12))
        }
    }
}

private enum LibraryDestination: Hashable, CaseIterable {
    case home, video, audio, books, account

    var title: String {
        switch self {
        case .home: "Home"
        case .video: "Video"
        case .audio: "Audio"
        case .books: "Books"
        case .account: "Account"
        }
    }

    var symbol: String {
        switch self {
        case .home: "house"
        case .video: "film"
        case .audio: "music.note"
        case .books: "books.vertical"
        case .account: "person.crop.circle"
        }
    }
}

@MainActor
private struct LibraryShellView: View {
    @Bindable var store: LuminaStore
    @State private var destination: LibraryDestination = .home
    @State private var compactColumn: NavigationSplitViewColumn = .detail
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @Environment(\.horizontalSizeClass) private var widthClass

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility, preferredCompactColumn: $compactColumn) {
            List(selection: Binding<LibraryDestination?>(
                get: { destination },
                set: { if let value = $0 { destination = value } }
            )) {
                ForEach(LibraryDestination.allCases, id: \.self) { tab in
                    Label(tab.title, systemImage: tab.symbol)
                        .tag(tab)
                        .accessibilityIdentifier("\(tab.title.lowercased())Tab")
                }
            }
            .navigationTitle("Lumina")
            .scrollContentBackground(.hidden)
            .background(LuminaTheme.paper)
            .accessibilityIdentifier("librarySidebar")
        } detail: {
            destinations
        }
        .navigationSplitViewStyle(.balanced)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if widthClass == .regular {
                NativeNowPlayingBar(store: store)
            }
        }
    }

    private var destinations: some View {
        TabView(selection: $destination) {
            Tab("Home", systemImage: "house", value: .home) {
                destinationStack {
                    HomeLibraryView(store: store, destination: $destination)
                }
            }
            Tab("Video", systemImage: "film", value: .video) {
                destinationStack { VideoLibraryView(store: store) }
            }
            Tab("Audio", systemImage: "music.note", value: .audio) {
                destinationStack { AudioLibraryView(store: store) }
            }
            Tab("Books", systemImage: "books.vertical", value: .books) {
                destinationStack {
                    if let session = store.session {
                        NativeBooksView(session: session).id(session.accessToken)
                    }
                }
            }
            Tab("Account", systemImage: "person.crop.circle", value: .account) {
                destinationStack { AccountView(store: store) }
            }
        }
    }

    private func destinationStack<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        NavigationStack {
            content()
                .safeAreaInset(edge: .bottom, spacing: 0) { compactPlayerBar }
                .toolbarVisibility(widthClass == .regular && columnVisibility != .detailOnly ? .hidden : .visible, for: .tabBar)
                .navigationDestination(for: MediaItem.self) { item in
                    Group {
                        if item.type == "MusicAlbum" || item.type == "MusicArtist" {
                            MusicCollectionView(item: item, store: store)
                        } else if item.isCollection {
                            VideoCollectionView(item: item, store: store)
                        } else {
                            TitleDetailView(item: item, store: store)
                        }
                    }
                    .toolbarVisibility(.visible, for: .navigationBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { compactPlayerBar }
                }
        }
    }

    @ViewBuilder
    private var compactPlayerBar: some View {
        if widthClass != .regular {
            NativeNowPlayingBar(store: store)
        }
    }
}

@MainActor
private struct AccountView: View {
    @State private var licensesPresented = false
    let store: LuminaStore
    @AppStorage("lumina.appearance") private var appearance = "dark"

    var body: some View {
        Form {
            Section {
                Label {
                    Text(store.session?.username ?? "Your account")
                        .font(LuminaTheme.editorial(30, relativeTo: .title, italic: true))
                } icon: {
                    Image(systemName: "person.crop.circle")
                        .font(.title)
                        .foregroundStyle(LuminaTheme.gold)
                }
                .padding(.vertical, 12)
            }
            .listRowBackground(Color.clear)

            Section { Button("Open-source licenses") { licensesPresented = true } }

            Section("Appearance") {
                Picker("Appearance", selection: $appearance) {
                    Text("Dark").tag("dark")
                    Text("Light").tag("light")
                    Text("System").tag("system")
                }
                .pickerStyle(.menu)
                .accessibilityValue(appearance == "system" ? Text("System") : appearance == "light" ? Text("Light") : Text("Dark"))
                .accessibilityIdentifier("appearancePicker")
            }
            .listRowBackground(LuminaTheme.ink.opacity(0.06))

            Section {
                Button(role: .destructive) {
                    Task {
                        store.clearError()
                        await store.signOut()
                    }
                } label: {
                    Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                }
                .accessibilityIdentifier("signOutButton")
            }
            .listRowBackground(LuminaTheme.ink.opacity(0.06))
        }
        .scrollContentBackground(.hidden)
        .background(LuminaTheme.paper)
        .toolbarVisibility(.hidden, for: .navigationBar)
        .accessibilityIdentifier("accountScreen")
        .sheet(isPresented: $licensesPresented) { PlaybackLicensesView() }
    }
}

@MainActor
private struct HomeLibraryView: View {
    let store: LuminaStore
    @Binding var destination: LibraryDestination

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text("Welcome home")
                        .font(LuminaTheme.editorial(28, relativeTo: .title, italic: true))
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 0)
                    Text(Date.now, format: .dateTime.month(.abbreviated).day())
                        .font(.caption)
                        .foregroundStyle(LuminaTheme.muted)
                        .fixedSize()
                }
                .padding(.bottom, 4)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
                }
                if !store.featuredItems.isEmpty {
                    LibraryFeaturedView(store: store)
                        .padding(.horizontal, -20)
                }
                if !store.resumeItems.isEmpty {
                    MediaRail(title: "Continue watching", items: store.resumeItems, store: store, identifierPrefix: "resumeItem")
                }
                if !store.nextUpItems.isEmpty {
                    MediaRail(title: "Next up", items: store.nextUpItems, store: store, identifierPrefix: "nextUpItem")
                }
                if !store.recentItems.isEmpty {
                    MediaRail(title: "Recently added", items: store.recentItems, store: store, identifierPrefix: "recentItem", wide: false)
                } else if store.isHomeBusy || store.isBusy {
                    ProgressView("Loading your library…")
                } else if store.featuredItems.isEmpty && store.errorMessage == nil {
                    EmptyLibraryView { Task { await store.loadHome() } }
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await store.loadHome() } }
                }
                Button { destination = .audio } label: {
                    HStack(alignment: .top, spacing: 16) {
                        Image(systemName: "music.note")
                            .font(.title)
                        VStack(alignment: .leading, spacing: 8) {
                            Text("A little listening room")
                                .font(LuminaTheme.editorial(24, relativeTo: .title2, italic: true))
                            Text("Explore your albums, artists, and tracks.")
                                .font(.body)
                                .foregroundStyle(LuminaTheme.muted)
                            Label("Open Audio", systemImage: "arrow.right")
                                .font(.subheadline.weight(.semibold))
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(22)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(LuminaTheme.gold.opacity(0.1), in: .rect(cornerRadius: 18))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("homeAudioButton")
                Button("Explore all video", systemImage: "film") { destination = .video }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("homeVideoButton")
            }
            .padding(20)
            .frame(maxWidth: 1200)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarVisibility(.hidden, for: .navigationBar)
        .accessibilityIdentifier("homeScreen")
        .refreshable { await store.loadHome() }
        .task { if store.recentItems.isEmpty { await store.loadHome() } }
    }
}

@MainActor
private struct VideoLibraryView: View {
    @Bindable var store: LuminaStore

    @State private var searchText = ""
    @State private var searchVisible = false
    @State private var kind: VideoBrowseKind = .all
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersVisible = false
    @State private var scrollPhase: ScrollPhase = .idle
    @State private var pullIntent: PullIntent = .none

    private enum PullIntent { case none, search, refresh }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if searchVisible {
                    HStack(spacing: 0) {
                        LibrarySearchBar(text: $searchText, onSubmit: search)
                            .frame(maxWidth: .infinity)
                        Button("Cancel") {
                            searchText = ""
                            searchVisible = false
                        }
                        .buttonStyle(.plain)
                        .font(.body)
                    }
                    .frame(height: 56)
                }

                ViewThatFits(in: .horizontal) {
                    HStack { catalogueFilters }
                    VStack(alignment: .leading) { catalogueFilters }
                }

                if filters != VideoFilters() {
                    HStack(alignment: .firstTextBaseline) {
                        Text(filterSummary)
                            .font(.footnote)
                            .foregroundStyle(LuminaTheme.muted)
                        Spacer()
                        Button("Clear filters") { filters = VideoFilters() }
                            .font(.footnote.weight(.semibold))
                            .accessibilityIdentifier("videoClearFiltersButton")
                    }
                }

                if store.isBusy && store.items.isEmpty {
                    LibraryLoadingView()
                } else if store.items.isEmpty {
                    if let message = store.errorMessage, !message.isEmpty {
                        InlineErrorView(message: message) {
                            refresh()
                        }
                    } else if !searchText.isEmpty {
                        ContentUnavailableView.search(text: searchText)
                    } else if kind != .all || filters != VideoFilters() {
                        ContentUnavailableView("No matching video", systemImage: "film", description: Text("Try another video type or clear your filters."))
                    } else {
                        EmptyLibraryView {
                            refresh()
                        }
                    }
                } else {
                    if store.isBusy {
                        ProgressView("Refreshing…")
                            .font(.footnote)
                            .tint(LuminaTheme.gold)
                    }

                    if let message = store.errorMessage, !message.isEmpty {
                        InlineErrorView(message: message) {
                            refresh()
                        }
                    }

                    if !store.items.isEmpty {
                        LibraryGrid(items: store.items, store: store)
                    }

                    if store.hasMore {
                        Button {
                            Task { await store.loadMore() }
                        } label: {
                            HStack(spacing: 8) {
                                if store.isBusy {
                                    ProgressView()
                                }
                                Text("Load more")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .disabled(store.isBusy)
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
            .frame(maxWidth: 1200)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("libraryScreen")
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .onScrollGeometryChange(for: PullIntent.self) { geometry in
            guard scrollPhase == .interacting else { return .none }
            let distance = -(geometry.contentOffset.y + geometry.contentInsets.top)
            if distance >= 80 { return .refresh }
            return distance >= 12 ? .search : .none
        } action: { _, intent in
            pullIntent = intent
        }
        .onScrollPhaseChange { previous, phase in
            let intent = pullIntent
            scrollPhase = phase
            guard previous == .interacting else { return }
            pullIntent = .none
            // Commit on release so revealing search never moves the refresh target mid-drag.
            switch intent {
            case .search:
                searchVisible = true
            case .refresh:
                if searchText.isEmpty { searchVisible = false }
                refresh()
            case .none:
                break
            }
        }
        .accessibilityAction(named: "Search video") {
            searchVisible = true
        }
        .accessibilityAction(named: "Refresh library", refresh)
        .onChange(of: searchText) {
            if searchText.isEmpty { search() }
        }
        .onChange(of: kind) { search() }
        .onChange(of: sort) { search() }
        .onChange(of: filters) { search() }
        .sheet(isPresented: $filtersVisible) {
            VideoFilterSheet(filters: $filters)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("Search video", systemImage: "magnifyingglass") { searchVisible = true }
                    .accessibilityIdentifier("videoSearchButton")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Filter video", systemImage: filters == VideoFilters() ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill") {
                    filtersVisible = true
                }
                .accessibilityIdentifier("videoFiltersButton")
                .accessibilityValue(filters == VideoFilters() ? "No filters" : filterSummary)
            }
        }
        .task {
            await loadInitialContent()
        }
    }

    @ViewBuilder private var catalogueFilters: some View {
        Picker("Video type", selection: $kind) {
            ForEach(VideoBrowseKind.allCases, id: \.self) { kind in Text(kind.label).tag(kind) }
        }
        .pickerStyle(.menu)
        .accessibilityIdentifier("videoKindPicker")
        if searchText.isEmpty {
            Picker("Sort", selection: $sort) {
                ForEach(VideoSort.allCases, id: \.self) { sort in Text(sort.label).tag(sort) }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("videoSortPicker")
        } else {
            Text("By relevance").font(.subheadline).foregroundStyle(LuminaTheme.muted)
        }
    }

    private var filterSummary: String {
        var labels = [String]()
        if !filters.genre.isEmpty { labels.append(filters.genre) }
        if let year = filters.year { labels.append(String(year)) }
        if filters.unwatchedOnly { labels.append("Unwatched") }
        if filters.favoritesOnly { labels.append("Favorites") }
        return labels.joined(separator: " · ")
    }

    private func loadInitialContent() async {
        guard store.session != nil else { return }
        await store.loadLibrary(search: searchText, kind: kind, sort: sort, filters: filters)
    }

    private func search() {
        store.clearError()
        Task {
            await store.loadLibrary(search: searchText.trimmingCharacters(in: .whitespacesAndNewlines), kind: kind, sort: sort, filters: filters)
        }
    }

    private func refresh() {
        store.clearError()
        Task {
            await store.loadLibrary(search: searchText.trimmingCharacters(in: .whitespacesAndNewlines), kind: kind, sort: sort, filters: filters)
        }
    }
}

@MainActor
private struct VideoFilterSheet: View {
    @Binding var filters: VideoFilters
    @Environment(\.dismiss) private var dismiss
    @State private var genre = ""
    @State private var year = ""
    @State private var unwatchedOnly = false
    @State private var favoritesOnly = false

    private var validYear: Bool {
        year.isEmpty || (year.count == 4 && Int(year).map { (1000...9999).contains($0) } == true)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Narrow your library") {
                    TextField("Genre", text: $genre, prompt: Text("Any genre"))
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("videoGenreField")
                    TextField("Year", text: $year, prompt: Text("Any year"))
                        .keyboardType(.numberPad)
                        .accessibilityIdentifier("videoYearField")
                    if !validYear {
                        Text("Enter a four-digit year or leave it empty.")
                            .font(.footnote)
                            .foregroundStyle(LuminaTheme.muted)
                    }
                }
                Section("Your activity") {
                    Toggle("Unwatched only", isOn: $unwatchedOnly)
                        .accessibilityIdentifier("videoUnwatchedToggle")
                    Toggle("Favorites only", isOn: $favoritesOnly)
                        .accessibilityIdentifier("videoFavoritesToggle")
                }
                Button("Reset filters") {
                    genre = ""
                    year = ""
                    unwatchedOnly = false
                    favoritesOnly = false
                }
                .accessibilityIdentifier("videoResetFiltersButton")
            }
            .navigationTitle("Video filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        filters = VideoFilters(
                            genre: genre.trimmingCharacters(in: .whitespacesAndNewlines),
                            year: Int(year),
                            unwatchedOnly: unwatchedOnly,
                            favoritesOnly: favoritesOnly
                        )
                        dismiss()
                    }
                    .disabled(!validYear)
                    .accessibilityIdentifier("videoApplyFiltersButton")
                }
            }
            .onAppear {
                genre = filters.genre
                year = filters.year.map(String.init) ?? ""
                unwatchedOnly = filters.unwatchedOnly
                favoritesOnly = filters.favoritesOnly
            }
        }
        .tint(LuminaTheme.gold)
        .presentationDetents([.large])
        .accessibilityIdentifier("videoFiltersSheet")
    }
}

@MainActor
private struct VideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var children: [MediaItem] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                MediaBillboard(item: item, store: store, context: LocalizedStringKey(item.type == "Series" ? "TV show" : item.seriesName ?? "Season"), showsOverview: true) {
                    Text(item.type == "Series" ? "Explore the seasons below." : "Choose an episode below.")
                        .font(.callout)
                        .foregroundStyle(.white.opacity(0.85))
                }
                .padding(.horizontal, -20)

                Text(item.type == "Series" ? "Seasons" : "Episodes")
                    .font(LuminaTheme.editorial(25, relativeTo: .title2, italic: true))
                    .accessibilityAddTraits(.isHeader)
                if let errorMessage {
                    InlineErrorView(message: errorMessage) { Task { await load() } }
                }
                if isLoading && children.isEmpty {
                    ProgressView(item.type == "Series" ? "Loading seasons…" : "Loading episodes…")
                } else if children.isEmpty && errorMessage == nil {
                    ContentUnavailableView("Nothing here yet", systemImage: "tv", description: Text("No \(item.type == "Series" ? "seasons" : "episodes") are available. Pull to refresh after adding them to Lumina."))
                } else if item.type == "Series" {
                    LazyVGrid(columns: libraryGridColumns(for: dynamicTypeSize), alignment: .leading, spacing: 28) {
                        ForEach(children) { child in
                            NavigationLink(value: child) {
                                MediaCard(item: child, imageURL: store.imageURL(for: child), identifierPrefix: "season")
                            }
                            .buttonStyle(.plain)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel(child.name)
                            .accessibilityIdentifier("videoSeason-\(child.id)")
                        }
                    }
                } else {
                    LazyVStack(spacing: 0) {
                        ForEach(children) { episode in
                            NavigationLink(value: episode) {
                                episodeRow(episode)
                            }
                            .buttonStyle(.plain)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel(mediaAccessibilityLabel(for: episode) + (episode.userData?.played == true ? ", Watched" : ""))
                            .accessibilityIdentifier("videoEpisode-\(episode.id)")
                            Divider()
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
            .frame(maxWidth: 1000)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle(item.name)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("videoCollectionScreen")
        .task(id: item.id) { await load() }
        .refreshable { await load() }
    }

    private func episodeRow(_ episode: MediaItem) -> some View {
        HStack(alignment: .top, spacing: 16) {
            if !dynamicTypeSize.isAccessibilitySize {
                ArtworkView(url: store.backdropURL(for: episode, maxWidth: 400) ?? store.imageURL(for: episode), name: episode.name, aspect: 16 / 9)
                    .frame(width: 112)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(episode.name)
                    .font(LuminaTheme.editorial(19, relativeTo: .headline))
                Text(episode.displaySubtitle)
                    .font(.caption)
                    .foregroundStyle(LuminaTheme.muted)
                if let overview = episode.overview, !overview.isEmpty {
                    Text(overview)
                        .font(.subheadline)
                        .foregroundStyle(LuminaTheme.muted)
                        .lineLimit(3)
                }
                if episode.userData?.played == true {
                    Label("Watched", systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(LuminaTheme.gold)
                } else if episode.resumeSeconds > 0 {
                    Text("Continue watching")
                        .font(.caption)
                        .foregroundStyle(LuminaTheme.gold)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.forward")
                .font(.caption.weight(.semibold))
                .foregroundStyle(LuminaTheme.muted)
                .padding(.top, 4)
        }
        .padding(.vertical, 16)
        .contentShape(Rectangle())
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            children = try await store.loadChildren(of: item)
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This collection could not load. Check your connection and try again."
        }
        isLoading = false
    }
}

@MainActor
private struct AudioLibraryView: View {
    let store: LuminaStore
    @State private var kind: MusicBrowseKind = .albums
    @State private var searchText = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                NativeMusicLibraryLinks(store: store)
                if kind == .albums, let album = store.audioItems.first(where: { $0.type == "MusicAlbum" }) {
                    NavigationLink(value: album) {
                        MusicCollectionSpotlight(item: album, store: store)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("audioFeaturedAlbum")
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Your listening room")
                            .font(LuminaTheme.editorial(34, relativeTo: .largeTitle, italic: true))
                            .accessibilityAddTraits(.isHeader)
                        Text("Albums to return to. Artists to spend time with.")
                            .foregroundStyle(LuminaTheme.muted)
                    }
                }
                HStack(alignment: .firstTextBaseline) {
                    Text("YOUR COLLECTION")
                        .font(.caption2.weight(.semibold))
                        .tracking(1.5)
                        .foregroundStyle(LuminaTheme.muted)
                    Spacer()
                    Picker("Browse music", selection: $kind) {
                        ForEach(MusicBrowseKind.allCases, id: \.self) { kind in Text(kind.label).tag(kind) }
                    }
                    .pickerStyle(.menu)
                    .accessibilityIdentifier("audioKindPicker")
                }
                .padding(.vertical, 12)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await load() } }
                }
                if store.audioItems.isEmpty {
                    if store.isAudioBusy {
                        ProgressView("Loading music…")
                    } else if store.errorMessage == nil {
                        ContentUnavailableView {
                            Label(searchText.isEmpty ? "Your listening room is quiet" : "No music found", systemImage: "music.note")
                        } description: {
                            Text(searchText.isEmpty ? "Add music to Lumina, then refresh to find your albums, artists, and tracks here." : "Try another search or browse a different collection.")
                        } actions: {
                            Button("Refresh") { Task { await load() } }
                        }
                    }
                } else {
                    MusicGrid(items: store.audioItems, store: store, identifierPrefix: "audioItem")
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
                        .buttonStyle(.bordered)
                        .disabled(store.isAudioBusy)
                        .accessibilityIdentifier("audioLoadMoreButton")
                    }
                }
            }
            .padding(20)
            .frame(maxWidth: 1200)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("audioScreen")
        .searchable(text: $searchText, prompt: "Search music")
        .onSubmit(of: .search) { Task { await load() } }
        .onChange(of: searchText) { if searchText.isEmpty { Task { await load() } } }
        .task(id: kind) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        await store.loadAudio(kind: kind, search: searchText.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

@MainActor
private struct MusicCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var children: [MediaItem] = []
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var albumPlayback: PlaybackModel?
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
        .background(LuminaTheme.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("musicCollectionScreen")
        .task(id: item.id) { await load() }
        .refreshable { await load() }
        .fullScreenCover(isPresented: Binding(get: { albumPlayback != nil }, set: { if !$0 { albumPlayback = nil } }), onDismiss: {
            Task { await store.refresh() }
        }) {
            if let albumPlayback { PlaybackScreen(playback: albumPlayback, store: store) }
        }
    }

    private var collectionArtwork: some View {
        ArtworkView(url: store.imageURL(for: item, maxWidth: 760), name: item.name, aspect: 1, symbol: "music.note")
            .shadow(color: .black.opacity(0.18), radius: 20, y: 10)
    }

    private var collectionBody: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(item.name)
                .font(LuminaTheme.editorial(34, relativeTo: .largeTitle))
                .accessibilityAddTraits(.isHeader)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle).foregroundStyle(LuminaTheme.muted)
            }
            if let overview = item.overview, !overview.isEmpty {
                Text(overview).font(.body)
            }
            if children.contains(where: \.isAudio) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { albumActions }
                    VStack(alignment: .leading, spacing: 12) { albumActions }
                }
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
                ContentUnavailableView("No music here yet", systemImage: "music.note", description: Text("Refresh after adding music to this collection."))
            } else if item.type == "MusicAlbum", children.allSatisfy(\.isAudio) {
                albumTracks
            } else {
                MusicGrid(items: children, store: store, identifierPrefix: "musicItem")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var albumTracks: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(children.enumerated()), id: \.element.id) { index, track in
                NavigationLink(value: track) {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(String(track.indexNumber ?? index + 1))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(LuminaTheme.muted)
                            .frame(minWidth: 24, alignment: .leading)
                        Text(track.name)
                            .font(LuminaTheme.editorial(18, relativeTo: .body))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
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
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: track))
                .accessibilityValue(track.durationSeconds > 0 ? Duration.seconds(track.durationSeconds).formatted(.units(allowed: [.minutes, .seconds], width: .wide)) : "")
                .accessibilityIdentifier("musicItem-\(track.id)")
            }
        }
    }

    @ViewBuilder private var albumActions: some View {
        Button {
            playCollection(shuffled: false)
        } label: {
            Label(preparingPlayback ? "Preparing…" : "Play album", systemImage: "play.fill")
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicPlayButton")
        Button {
            playCollection(shuffled: true)
        } label: {
            Label("Shuffle", systemImage: "shuffle")
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicShuffleButton")
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
        do { children = try await store.musicChildren(of: item) }
        catch { errorMessage = "This music collection could not load. Check your connection and try again." }
    }
}

@MainActor
private struct MusicGrid: View {
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.horizontalSizeClass) private var widthClass

    private var columns: [GridItem] {
        if dynamicTypeSize.isAccessibilitySize { return libraryGridColumns(for: dynamicTypeSize) }
        if widthClass == .regular { return [GridItem(.adaptive(minimum: 168, maximum: 220), spacing: 16, alignment: .top)] }
        return [GridItem(.flexible(), spacing: 16, alignment: .top), GridItem(.flexible(), spacing: 16, alignment: .top)]
    }

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
            ForEach(items) { item in
                NavigationLink(value: item) {
                    MediaCard(item: item, imageURL: store.imageURL(for: item), identifierPrefix: identifierPrefix)
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: item))
                .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
            }
        }
    }
}

private struct LibrarySearchBar: UIViewRepresentable {
    @Binding var text: String
    let onSubmit: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> UISearchBar {
        let searchBar = UISearchBar()
        searchBar.delegate = context.coordinator
        searchBar.placeholder = String(localized: "Search video")
        searchBar.searchBarStyle = .minimal
        searchBar.autocapitalizationType = .none
        searchBar.autocorrectionType = .no
        searchBar.searchTextField.accessibilityIdentifier = "searchField"
        return searchBar
    }

    func updateUIView(_ searchBar: UISearchBar, context: Context) {
        context.coordinator.parent = self
        if searchBar.text != text { searchBar.text = text }
    }

    @MainActor final class Coordinator: NSObject, UISearchBarDelegate {
        var parent: LibrarySearchBar
        init(_ parent: LibrarySearchBar) { self.parent = parent }

        func searchBar(_ searchBar: UISearchBar, textDidChange searchText: String) {
            parent.text = searchText
        }

        func searchBarSearchButtonClicked(_ searchBar: UISearchBar) {
            searchBar.resignFirstResponder()
            parent.onSubmit()
        }

    }
}

@MainActor
private struct LibraryFeaturedView: View {
    let store: LuminaStore

    @Environment(\.horizontalSizeClass) private var widthClass
    private var compact: Bool { widthClass != .regular }

    @State private var request: PlaybackRequest?
    @State private var preparing = false
    @State private var playbackError: String?

    var body: some View {
        FeaturedShelf(items: store.featuredItems, store: store, isInteracting: preparing || request != nil) { item in
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { actions(for: item) }
                VStack(alignment: .leading, spacing: 12) { actions(for: item) }
            }
            if let playbackError {
                Text(playbackError).font(.callout)
            }
        }
        .fullScreenCover(item: $request, onDismiss: {
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
    }

    @ViewBuilder private func actions(for item: MediaItem) -> some View {
        Button {
            guard !preparing else { return }
            preparing = true
            playbackError = nil
            Task {
                defer { preparing = false }
                do { request = try await store.preparePlayback(for: item) }
                catch { playbackError = "Playback could not start. Open Details to try compatible video." }
            }
        } label: {
            Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play", systemImage: "play.fill")
        }
        .buttonStyle(.borderedProminent)
        .tint(compact ? LuminaTheme.ink : .white)
        .foregroundStyle(compact ? LuminaTheme.paper : LuminaTheme.theater)
        .controlSize(.large)
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")

        NavigationLink(value: item) {
            Label("Details", systemImage: "info.circle")
        }
        .buttonStyle(.bordered)
        .tint(compact ? LuminaTheme.ink : .white)
        .controlSize(.large)
        .accessibilityIdentifier("featuredDetailsButton")
    }
}

@MainActor
private struct LibraryGrid: View {
    let items: [MediaItem]
    let store: LuminaStore

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        LazyVGrid(
            columns: libraryGridColumns(for: dynamicTypeSize),
            alignment: .leading,
            spacing: 18
        ) {
            ForEach(items) { item in
                NavigationLink(value: item) {
                    MediaCard(
                        item: item,
                        imageURL: store.imageURL(for: item),
                        identifierPrefix: "library"
                    )
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape([.interaction, .accessibility], Rectangle())
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityAddTraits(.isButton)
                .accessibilityLabel(mediaAccessibilityLabel(for: item))
                .accessibilityIdentifier("libraryItem-\(item.id)")
                .onAppear {
                    if store.hasMore && item.id == items.suffix(6).first?.id {
                        Task { await store.loadMore() }
                    }
                }
            }
        }
    }
}

@MainActor
private struct MediaRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    var wide = true

    @ScaledMetric(relativeTo: .body) private var posterWidth: CGFloat = 168
    @ScaledMetric(relativeTo: .body) private var stillWidth: CGFloat = 320

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title)
                .font(LuminaTheme.editorial(25, relativeTo: .title2, italic: true))
                .padding(.top, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(alignment: .top) {
                    Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
                }

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 16) {
                    ForEach(items) { item in
                        NavigationLink(value: item) {
                            MediaCard(
                                item: item,
                                imageURL: wide ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item) : store.imageURL(for: item),
                                identifierPrefix: identifierPrefix,
                                wide: wide
                            )
                            .containerRelativeFrame(.horizontal) { availableWidth, _ in
                                let square = item.isAudio || item.type.hasPrefix("Music")
                                if square { return min(posterWidth * 1.2, availableWidth * 0.4) }
                                if wide { return min(stillWidth, availableWidth < 600 ? min(300, availableWidth * 0.72) : availableWidth * 0.48) }
                                return min(posterWidth, availableWidth * 0.3)
                            }
                        }
                        .contentShape([.interaction, .accessibility], Rectangle())
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityAddTraits(.isButton)
                        .accessibilityLabel(mediaAccessibilityLabel(for: item))
                        .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .scrollTargetLayout()
                .padding(.horizontal, 1)
            }
            .scrollTargetBehavior(.viewAligned)
        }
    }
}

@MainActor
private struct MediaCard: View {
    let item: MediaItem
    let imageURL: URL?
    let identifierPrefix: String
    var wide = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 8) {
            ArtworkView(url: imageURL, name: item.name, aspect: item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" ? 1 : wide ? 16 / 9 : 2 / 3, symbol: item.isAudio || item.type.hasPrefix("Music") ? "music.note" : "film")
                .accessibilityIdentifier("\(identifierPrefix)Artwork-\(item.id)")
                .overlay(alignment: .bottom) {
                    if progressFraction > 0 {
                        ProgressView(value: progressFraction)
                            .progressViewStyle(.linear)
                            .tint(LuminaTheme.gold)
                            .frame(height: 3)
                            .clipShape(.rect(cornerRadius: 2))
                    }
                }

            Text(item.name)
                .font(LuminaTheme.editorial(15, relativeTo: .subheadline))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .truncationMode(.tail)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("\(identifierPrefix)Title-\(item.id)")

            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle)
                    .font(.caption2)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }

        content
            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
            .clipped()
    }

    private var progressFraction: Double {
        guard item.durationSeconds > 0, item.resumeSeconds > 0 else { return 0 }
        return min(max(item.resumeSeconds / item.durationSeconds, 0), 1)
    }
}

@MainActor
private struct ArtworkView: View {
    let url: URL?
    let name: String
    var aspect: CGFloat = 2 / 3
    var symbol = "film"

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                GeometryReader { geometry in
                    if let url {
                        AsyncImage(url: url) { phase in
                            switch phase {
                            case .empty:
                                ProgressView()
                                    .tint(LuminaTheme.gold)
                            case .success(let image):
                                image
                                    .resizable()
                                    .scaledToFill()
                                    .frame(width: geometry.size.width, height: geometry.size.height)
                                    .clipped()
                            case .failure:
                                placeholder
                            @unknown default:
                                placeholder
                            }
                        }
                        .frame(width: geometry.size.width, height: geometry.size.height)
                        .clipped()
                    } else {
                        placeholder
                            .frame(width: geometry.size.width, height: geometry.size.height)
                            .clipped()
                    }
                }
            }
            .clipShape(.rect(cornerRadius: 12))
            .accessibilityHidden(true)
    }

    private var placeholder: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.title2)
            Text(name)
                .font(.caption)
                .multilineTextAlignment(.center)
                .lineLimit(3)
        }
        .foregroundStyle(LuminaTheme.muted)
        .padding(12)
    }
}

@MainActor
private struct TitleDetailView: View {
    let item: MediaItem
    @Bindable var store: LuminaStore

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?
    @State private var updatedItem: MediaItem?
    @State private var isSavingState = false
    @State private var isLoadingItem = true
    @State private var stateError: String?

    private var currentItem: MediaItem { updatedItem ?? item }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                detailHeader

                if !currentItem.isAudio {
                    personalActions
                }
                if let stateError {
                    Label(stateError, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.gold)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("videoStateError")
                }

                if let overview = currentItem.overview, !overview.isEmpty {
                    Text(overview)
                        .font(LuminaTheme.editorial(17, relativeTo: .body))
                        .lineSpacing(4)
                        .foregroundStyle(LuminaTheme.ink)
                }

                if let playbackError, !playbackError.isEmpty {
                    Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.gold)
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(.isStaticText)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 28)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("detailScreen")
        .task(id: item.id) { await refreshItem() }
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task {
                await store.refresh()
                await refreshItem()
            }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
    }

    private var personalActions: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 20) {
                watchedButton
                favoriteButton
            }
            VStack(alignment: .leading, spacing: 8) {
                watchedButton
                favoriteButton
            }
        }
        .disabled(isSavingState || isLoadingItem)
        .opacity(isSavingState || isLoadingItem ? 0.6 : 1)
    }

    private var watchedButton: some View {
        Button {
            updateState(played: currentItem.userData?.played != true)
        } label: {
            Label(currentItem.userData?.played == true ? "Mark unwatched" : "Mark watched", systemImage: currentItem.userData?.played == true ? "checkmark.circle.fill" : "checkmark.circle")
                .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(LuminaTheme.gold)
        .accessibilityValue(currentItem.userData?.played == true ? "Watched" : "Unwatched")
        .accessibilityIdentifier("videoWatchedButton")
    }

    private var favoriteButton: some View {
        Button {
            updateState(favorite: currentItem.userData?.isFavorite != true)
        } label: {
            Label(currentItem.userData?.isFavorite == true ? "Remove favorite" : "Add favorite", systemImage: currentItem.userData?.isFavorite == true ? "heart.fill" : "heart")
                .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(LuminaTheme.gold)
        .accessibilityValue(currentItem.userData?.isFavorite == true ? "Favorite" : "Not a favorite")
        .accessibilityIdentifier("videoFavoriteButton")
    }

    private func updateState(played: Bool? = nil, favorite: Bool? = nil) {
        guard !isSavingState, !isLoadingItem else { return }
        isSavingState = true
        stateError = nil
        Task {
            do {
                updatedItem = try await store.updateItemState(item: currentItem, played: played, favorite: favorite)
            } catch {
                stateError = (error as? LocalizedError)?.errorDescription ?? "Your activity could not be saved. Check your connection and try again."
            }
            isSavingState = false
        }
    }

    private func refreshItem() async {
        isLoadingItem = true
        defer { isLoadingItem = false }
        do {
            updatedItem = try await store.loadItem(item)
            stateError = nil
        } catch {
            stateError = (error as? LocalizedError)?.errorDescription ?? "The latest activity could not load. Check your connection and reopen this title."
        }
    }

    private var detailHeader: some View {
        MediaBillboard(item: currentItem, store: store, context: "In your library", showsOverview: false) {
            if currentItem.isPlayable {
                playbackActions
            } else {
                Text("Playback is unavailable for this title.")
                    .font(.callout)
            }
        }
        .padding(.horizontal, -20)
    }

    @ViewBuilder
    private var playbackActions: some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 12) {
                playbackButton
                convertButton
            }
        } else {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    playbackButton
                    convertButton
                }
                VStack(alignment: .leading, spacing: 12) {
                    playbackButton
                    convertButton
                }
            }
        }
    }

    private var playbackButton: some View {
        Button {
            preparePlayback(forceTranscode: false)
        } label: {
            HStack(spacing: 8) {
                if isPreparingPlayback {
                    ProgressView()
                }
                Label(playLabel, systemImage: "play.fill")
            }
            .frame(minWidth: 132)
            .foregroundStyle(LuminaTheme.theater)
        }
        .buttonStyle(.borderedProminent)
        .tint(.white)
        .controlSize(.large)
        .disabled(isPreparingPlayback)
        .accessibilityIdentifier("playButton")
    }

    @ViewBuilder private var convertButton: some View {
        if !currentItem.isAudio {
            Button {
                preparePlayback(forceTranscode: true)
            } label: {
                Label("Convert", systemImage: "arrow.triangle.2.circlepath")
                    .frame(minWidth: 132)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(isPreparingPlayback)
            .tint(.white)
            .accessibilityHint("Use compatibility conversion before playback.")
            .accessibilityIdentifier("convertButton")
        }
    }

    private var playLabel: String {
        currentItem.resumeSeconds > 0 ? "Resume" : "Play"
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard currentItem.isPlayable, !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            do {
                playbackRequest = try await store.preparePlayback(for: currentItem, forceTranscode: forceTranscode)
            } catch {
                playbackError = currentItem.isAudio
                    ? (error as? LocalizedError)?.errorDescription ?? "Playback could not start. Check your connection and try again."
                    : forceTranscode
                        ? "Compatibility conversion could not start. Check the server and try again."
                        : "Playback could not start. Try Convert for compatibility."
            }
            isPreparingPlayback = false
        }
    }
}

@MainActor
private struct LibraryLoadingView: View {
    private let placeholders = [0, 1, 2, 3, 4, 5]

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        LazyVGrid(
            columns: libraryGridColumns(for: dynamicTypeSize),
            alignment: .leading,
            spacing: 28
        ) {
            ForEach(placeholders, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(LuminaTheme.ink.opacity(0.08))
                    .aspectRatio(2 / 3, contentMode: .fit)
                    .redacted(reason: .placeholder)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading video library")
    }
}

private func libraryGridColumns(for dynamicTypeSize: DynamicTypeSize) -> [GridItem] {
    if dynamicTypeSize.isAccessibilitySize {
        return [GridItem(.flexible(minimum: 0, maximum: 220), spacing: 20, alignment: .top)]
    }
    return [GridItem(.adaptive(minimum: 104, maximum: 180), spacing: 12, alignment: .top)]
}

@MainActor
private struct EmptyLibraryView: View {
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: "film.stack")
                .font(.largeTitle)
                .foregroundStyle(LuminaTheme.gold)
                .accessibilityHidden(true)
            Text("Your video library is quiet.")
                .font(.system(.title2, design: .serif))
                .fontWeight(.semibold)
                .italic()
            Text("Add video to Lumina, then refresh to see it here.")
                .foregroundStyle(LuminaTheme.muted)
            Button("Refresh", action: onRefresh)
                .buttonStyle(.bordered)
        }
        .padding(.vertical, 24)
    }
}

@MainActor
private struct InlineErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.callout)
                .foregroundStyle(LuminaTheme.gold)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isStaticText)
            Button("Try again", action: onRetry)
                .buttonStyle(.bordered)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 14))
    }
}

private func mediaAccessibilityLabel(for item: MediaItem) -> String {
    var parts = [item.name]
    if !item.displaySubtitle.isEmpty {
        parts.append(item.displaySubtitle)
    }
    if item.resumeSeconds > 0 {
        parts.append(item.durationSeconds > item.resumeSeconds ? "in progress" : "played")
    }
    return parts.joined(separator: ", ")
}
