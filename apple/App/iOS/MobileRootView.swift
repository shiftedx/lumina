import SwiftUI
import UIKit
import LuminaCore

@MainActor
struct MobileRootView: View {
    @Bindable var store: LuminaStore
    @State private var audioSession = NativeAudioSession()
    @State private var userSettings = LuminaUserSettings()
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
        .environment(userSettings)
        .preferredColorScheme(appearance == "system" ? nil : appearance == "light" ? .light : .dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil { audioSession.stop() }
        }
        .task(id: store.session?.accessToken) {
            userSettings.configure(session: store.session)
            if store.session != nil {
                await userSettings.load()
                if userSettings.hasLoaded { appearance = userSettings.theme }
                await audioSession.restore(store: store)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                audioSession.resumeAfterActivation()
                Task {
                    await audioSession.refreshSharedQueue()
                    if store.session != nil {
                        await userSettings.load()
                        if userSettings.hasLoaded { appearance = userSettings.theme }
                    }
                }
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
                    HStack(spacing: 12) {
                        LuminaBrandMark()
                            .frame(width: 46, height: 28)
                        Text("LUMINA")
                            .font(.title2.weight(.black))
                            .tracking(3)
                    }
                    Text("Your household library, ready for the room you are in.")
                        .font(LuminaTheme.editorial(23, relativeTo: .title3, italic: true))
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
                .padding(18)
                .background(LuminaTheme.ink.opacity(0.07), in: .rect(cornerRadius: 16))

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
            .padding(.vertical, 44)
            .frame(maxWidth: 480)
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
    case home, library, requests, account
}

private enum LibraryCategory: String, CaseIterable {
    case movies, tv, anime, music, books

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "TV"
        case .anime: "Anime"
        case .music: "Music"
        case .books: "Books"
        }
    }

    var videoKind: VideoBrowseKind {
        switch self {
        case .tv: .shows
        case .anime: .anime
        default: .movies
        }
    }
}

@MainActor
private struct LibraryShellView: View {
    @Bindable var store: LuminaStore
    @State private var destination: LibraryDestination = .home
    @State private var category: LibraryCategory = .movies
    @Environment(\.horizontalSizeClass) private var widthClass

    var body: some View {
        // TabView is system managed, so iPhone Duo can relocate it to the
        // appropriate edge without a second app-made sidebar duplicating it.
        destinations
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if widthClass == .regular {
                NativeNowPlayingBar(store: store)
            }
        }
    }

    private var destinations: some View {
        TabView(selection: $destination) {
            Tab("Home", image: "LuminaNavHome", value: .home) {
                destinationStack {
                    HomeLibraryView(store: store)
                }
            }
            Tab("Library", image: "LuminaNavLibrary", value: .library) {
                destinationStack {
                    libraryContent
                        .safeAreaInset(edge: .top, spacing: 0) { libraryCategories }
                }
            }
            Tab("Requests", image: "LuminaNavRequests", value: .requests) {
                destinationStack {
                    if let session = store.session {
                        NativeRequestsView(session: session, isActive: destination == .requests)
                            .id(session.accessToken)
                    }
                }
            }
            Tab("Account", image: "LuminaNavAccount", value: .account) {
                destinationStack { NativeAccountSettingsView(store: store) }
            }
        }
    }

    @ViewBuilder private var libraryContent: some View {
        switch category {
        case .movies, .tv, .anime:
            VideoLibraryView(store: store, kind: .constant(category.videoKind))
                .id(category)
        case .music:
            AudioLibraryView(store: store)
        case .books:
            if let session = store.session {
                NativeBooksView(session: session).id(session.accessToken)
            }
        }
    }

    private var libraryCategories: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 26) {
                ForEach(LibraryCategory.allCases, id: \.self) { value in
                    Button {
                        category = value
                    } label: {
                        Text(value.title)
                            .font(.subheadline.weight(category == value ? .bold : .medium))
                            .foregroundStyle(category == value ? LuminaTheme.ink : LuminaTheme.muted)
                            .padding(.vertical, 13)
                            .overlay(alignment: .bottom) {
                                if category == value {
                                    Capsule().fill(LuminaTheme.gold).frame(height: 2)
                                }
                            }
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(category == value ? .isSelected : [])
                    .accessibilityIdentifier("libraryCategory-\(value.rawValue)")
                }
            }
            .padding(.horizontal, 20)
        }
        .scrollIndicators(.hidden)
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("libraryCategories")
    }

    private func destinationStack<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        NavigationStack {
            content()
                .safeAreaInset(edge: .bottom, spacing: 0) { compactPlayerBar }
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
private struct HomeLibraryView: View {
    let store: LuminaStore
    @State private var homeLayout: LuminaHomeLayout?
    @State private var feed: LuminaHomeFeed?
    @State private var completedInitialHomeLoad = false
    @Environment(\.scenePhase) private var scenePhase

    private var shelves: [HomeShelfPreference] {
        homeLayout?.shelves ?? HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if !store.featuredItems.isEmpty {
                    LibraryFeaturedView(store: store)
                        .padding(.horizontal, -20)
                        .padding(.top, -20)
                }
                ForEach(shelves.filter(\.visible)) { shelf in
                    homeShelf(shelf.id)
                }
                if store.isHomeBusy || store.isBusy {
                    ProgressView("Loading your library…")
                } else if store.featuredItems.isEmpty && store.errorMessage == nil && !(feed?.rows.contains(where: \.hasItems) ?? false) {
                    EmptyLibraryView { Task { await store.loadHome() } }
                }
                if let error = feed?.errorMessage {
                    InlineErrorView(message: error) { Task { await feed?.load() } }
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await store.loadHome() } }
                }
            }
            .padding(20)
            .frame(maxWidth: 1200)
            .frame(maxWidth: .infinity)
        }
        .scrollClipDisabled()
        .background(LuminaTheme.paper)
        .toolbarVisibility(.visible, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar {
            if #available(iOS 26.0, *) {
                ToolbarItem(placement: .topBarLeading) {
                    HomeHeroChrome().fixedSize()
                }
                .sharedBackgroundVisibility(.hidden)
            } else {
                ToolbarItem(placement: .topBarLeading) {
                    HomeHeroChrome().fixedSize()
                }
            }
        }
        .accessibilityIdentifier("homeScreen")
        .refreshable { await store.loadHome(); await homeLayout?.load(); await feed?.load() }
        .task(id: store.session?.accessToken) {
            guard let session = store.session else {
                homeLayout = nil
                feed = nil
                completedInitialHomeLoad = false
                return
            }
            completedInitialHomeLoad = false
            let layout = LuminaHomeLayout(session: session)
            homeLayout = layout
            let homeFeed = LuminaHomeFeed(session: session, includesExternalSources: true)
            feed = homeFeed
            async let savedLayout: Void = layout.load()
            async let rows: Void = homeFeed.load()
            _ = await (savedLayout, rows)
            guard !Task.isCancelled else { return }
            completedInitialHomeLoad = true
            if store.recentItems.isEmpty { await store.loadHome() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { refreshHomeRowsAfterInitialLoad() }
        }
        .onAppear { refreshHomeRowsAfterInitialLoad() }
    }

    private func refreshHomeRowsAfterInitialLoad() {
        guard completedInitialHomeLoad, let homeLayout, let feed else { return }
        Task {
            async let layout: Void = homeLayout.load()
            async let rows: Void = feed.load()
            _ = await (layout, rows)
        }
    }

    @ViewBuilder
    private func homeShelf(_ id: HomeShelfID) -> some View {
        Group {
            if let feed, feed.hasLoaded {
                ForEach(feed.rows.filter { $0.shelfID == id }) { row in
                    if !row.items.isEmpty {
                    MediaRail(title: row.title, items: row.items, store: store,
                              identifierPrefix: id == .continueWatching ? "resumeItem" : id == .nextUp ? "nextUpItem" : id == .newInLibrary ? "recentItem" : "homeRow-\(row.id)",
                              wide: row.shape == .still && id != .continueWatching && id != .nextUp)
                    }
                    if !(row.externalItems ?? []).isEmpty { HomeExternalRail(row: row) }
                }
            } else {
            switch id.rawValue {
            case "continue":
                if !store.resumeItems.isEmpty {
                    MediaRail(title: id.title, items: store.resumeItems, store: store, identifierPrefix: "resumeItem", wide: false)
                }
            case "next_up":
                if !store.nextUpItems.isEmpty {
                    MediaRail(title: id.title, items: store.nextUpItems, store: store, identifierPrefix: "nextUpItem", wide: false)
                }
            case "new_in_library":
                if !store.recentItems.isEmpty {
                    MediaRail(title: id.title, items: store.recentItems, store: store, identifierPrefix: "recentItem", wide: false)
                }
            default: EmptyView()
            }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("homeShelf-\(id.rawValue)")
    }
}

@MainActor
private struct HomeExternalRail: View {
    let row: HomeFeedRow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(row.title).font(.title3.weight(.bold)).padding(.top, 10)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(row.externalItems ?? []) { item in
                        if let destination = item.sourceURL {
                            Link(destination: destination) {
                                VStack(alignment: .leading, spacing: 6) {
                                    ArtworkView(url: item.artworkURL, name: item.title, aspect: 16 / 9)
                                    Text(item.title).font(.caption.weight(.semibold)).lineLimit(2)
                                    Text(item.subtitle).font(.caption2).foregroundStyle(LuminaTheme.muted).lineLimit(1)
                                    Label("Open source", systemImage: "arrow.up.right").font(.caption2)
                                }
                                .containerRelativeFrame(.horizontal) { width, _ in min(276, width * 0.68) }
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(item.title), \(item.subtitle). Open source app or website.")
                            .accessibilityIdentifier("homeExternal-\(item.id)")
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
        }
    }
}

/// Account owns Home customization; the editor keeps the same server-backed layout contract.
@MainActor
struct NativeHomePreferencesView: View {
    let store: LuminaStore
    @State private var layout: LuminaHomeLayout?

    var body: some View {
        Group {
            if let layout {
                NativeHomeLayoutEditor(layout: layout, store: store, feed: nil)
            } else {
                ProgressView("Loading Home layout…")
            }
        }
        .task(id: store.session?.accessToken) {
            guard let session = store.session else { return }
            let loaded = LuminaHomeLayout(session: session)
            layout = loaded
            await loaded.load()
        }
    }
}

@MainActor
private struct NativeHomeLayoutEditor: View {
    let layout: LuminaHomeLayout
    let store: LuminaStore
    let feed: LuminaHomeFeed?
    @State private var draft: [HomeShelfPreference] = []
    @State private var changed = false
    @State private var reset = false
    @Environment(\.dismiss) private var dismiss
    private var rows: [HomeShelfPreference] { draft }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(rows) { shelf in
                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(shelf.id.title)
                                if feed?.hasLoaded == true && isEmpty(shelf.id) {
                                    Text("Nothing to show right now").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .accessibilityHidden(true)
                            Spacer(minLength: 8)
                            Toggle(shelf.id.title, isOn: Binding(get: {
                                draft.first { $0.id == shelf.id }?.visible ?? true
                            }, set: { visible in
                                if let index = draft.firstIndex(where: { $0.id == shelf.id }) {
                                    draft[index] = HomeShelfPreference(id: shelf.id, visible: visible)
                                    changed = true; reset = false
                                }
                            }))
                            .labelsHidden()
                            .toggleStyle(.switch)
                            .accessibilityLabel(shelf.id.title)
                            .accessibilityIdentifier("homeShelfVisibility-\(shelf.id.rawValue)")
                            .accessibilityAction(named: "Move up") { move(shelf.id, offset: -1) }
                            .accessibilityAction(named: "Move down") { move(shelf.id, offset: 1) }
                        }
                    }
                    .onMove { source, target in
                        var reordered = rows
                        reordered.move(fromOffsets: source, toOffset: target)
                        applyOrder(reordered)
                    }
                } footer: {
                    Text("Drag the handles to reorder your Home rows. Turn off a row to hide it. Your changes also apply on the website.")
                }
                .disabled(!layout.hasLoaded || layout.isSaving)
                Section {
                    Button("Reset to default") {
                        draft = HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }
                        changed = true; reset = true
                    }.accessibilityIdentifier("homeLayoutResetButton")
                        .disabled(!layout.hasLoaded || layout.isSaving)
                }
                if layout.isLoading { ProgressView("Loading Home layout…") }
                if let error = layout.errorMessage {
                    Section {
                        Text(error).foregroundStyle(.red)
                        Button("Try again") { Task { await layout.load() } }
                    }
                }
            }
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Edit Home").navigationBarTitleDisplayMode(.inline)
            .toolbarVisibility(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(layout.isSaving)
                        .accessibilityIdentifier("homeLayoutCancelButton")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        Task { if await layout.save(reset ? nil : draft) { dismiss() } }
                    }.disabled(!layout.hasLoaded || layout.isSaving)
                        .accessibilityIdentifier("homeLayoutSaveButton")
                }
            }
            .interactiveDismissDisabled(layout.isSaving)
            .onAppear {
                draft = layout.shelves
                changed = false
                reset = false
            }
            .onChange(of: layout.shelves, initial: true) { _, value in
                if !changed { draft = value }
            }
            .accessibilityIdentifier("homeLayoutEditor")
        }
        .preferredColorScheme(.dark)
    }

    private func applyOrder(_ reordered: [HomeShelfPreference]) {
        draft = reordered
        changed = true; reset = false
    }

    private func move(_ id: HomeShelfID, offset: Int) {
        var reordered = rows
        guard let index = reordered.firstIndex(where: { $0.id == id }) else { return }
        let target = index + offset
        guard reordered.indices.contains(target) else { return }
        reordered.swapAt(index, target)
        applyOrder(reordered)
    }

    private func isEmpty(_ id: HomeShelfID) -> Bool {
        if let feed, feed.hasLoaded {
            return !feed.rows.contains { $0.shelfID == id && $0.hasItems }
        }
        return switch id.rawValue {
        case "continue": store.resumeItems.isEmpty
        case "next_up": store.nextUpItems.isEmpty
        case "new_in_library": store.recentItems.isEmpty
        default: true
        }
    }
}

@MainActor
private struct HomeHeroChrome: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                LuminaBrandMark()
                    .frame(width: 24, height: 24 * 34 / 56)
                    .alignmentGuide(.firstTextBaseline) { dimensions in
                        // SunriseShape's horizon is at 31/34 of its height;
                        // include the rounded stroke's lower edge.
                        dimensions.height * 31 / 34 + dimensions.width * 1.5 / 56
                    }
                Text("LUMINA")
                    .font(.caption.weight(.black))
                    .tracking(1.8)
            }
            .foregroundStyle(LuminaTheme.ink)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Lumina")
            .accessibilityIdentifier("homeMasthead")
        }
    }

}

@MainActor
private struct VideoLibraryView: View {
    @Bindable var store: LuminaStore

    @State private var searchText = ""
    @State private var searchVisible = false
    @Binding var kind: VideoBrowseKind
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersVisible = false
    @State private var scrollPhase: ScrollPhase = .idle
    @State private var pullIntent: PullIntent = .none

    private enum PullIntent { case none, search, refresh }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
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
                        VideoPosterWall(items: store.items, store: store)
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
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("Search video", systemImage: "magnifyingglass") { searchVisible = true }
                    .accessibilityIdentifier("videoSearchButton")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Sort: \(sort.label)", selection: $sort) {
                        ForEach(VideoSort.allCases, id: \.self) { sort in Text(sort.label).tag(sort) }
                    }
                    .pickerStyle(.menu)
                    .accessibilityIdentifier("videoSortPicker")
                    .accessibilityValue(sort.label)

                    Divider()
                    Button("Filters", systemImage: filters == VideoFilters() ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill") {
                        filtersVisible = true
                    }
                    .accessibilityIdentifier("videoFiltersButton")
                    .accessibilityValue(filters == VideoFilters() ? "No filters" : filterSummary)

                    if filters != VideoFilters() {
                        Button("Clear filters", systemImage: "xmark.circle") { filters = VideoFilters() }
                            .accessibilityIdentifier("videoClearFiltersButton")
                    }
                } label: {
                    Label("Video options, \(optionsAccessibilityValue)", systemImage: "ellipsis.circle")
                        .labelStyle(.iconOnly)
                }
                .accessibilityIdentifier("videoOptionsMenu")
                .accessibilityValue(optionsAccessibilityValue)
            }
        }
        .task {
            await loadInitialContent()
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

    private var optionsAccessibilityValue: String {
        let activeFilters = filters == VideoFilters() ? "No filters" : filterSummary
        return "\(kind.label), sorted by \(sort.label); \(activeFilters)"
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
private struct NativeWatchlistButton: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var watchlist: LuminaWatchlist?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                guard let watchlist else { return }
                Task { await watchlist.set(itemID: item.id, included: !watchlist.isIncluded) }
            } label: {
                Label(watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist",
                      systemImage: watchlist?.isIncluded == true ? "checkmark" : "plus")
            }
            .buttonStyle(LuminaMediaActionStyle())
            .disabled(watchlist == nil || watchlist?.isLoading == true || watchlist?.isSaving == true || watchlist?.errorMessage != nil)
            .accessibilityIdentifier("videoWatchlistButton")
            .accessibilityValue(watchlist?.isIncluded == true ? "In watchlist" : "Not in watchlist")
            .accessibilityHint(watchlist?.isIncluded == true ? "Remove from your watchlist" : "Save to your watchlist")

            if let message = watchlist?.errorMessage {
                InlineErrorView(message: message) {
                    Task { await watchlist?.load(itemID: item.id) }
                }
            }
        }
        .task(id: item.id + (store.session?.accessToken ?? "")) {
            let model = LuminaWatchlist(session: store.session)
            watchlist = model
            await model.load(itemID: item.id)
        }
    }
}

@MainActor
private struct VideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var children: [MediaItem] = []
    @State private var detailItem: MediaItem?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                MediaBillboard(item: detailItem ?? item, store: store, context: "", showsOverview: true, showsContext: false, compactMinimumHeight: 460, wideMinimumHeight: 540) {
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .top, spacing: 12) { playbackActions }
                        VStack(alignment: .leading, spacing: 12) { playbackActions }
                    }
                    if let playbackError {
                        Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                            .font(.callout)
                            .foregroundStyle(LuminaTheme.gold)
                            .accessibilityIdentifier("videoCollectionPlaybackError")
                    }
                }
                .padding(.horizontal, -20)

                Text(item.type == "Series" ? "Seasons" : "Episodes")
                    .font(.title2.weight(.bold))
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
                                MediaCard(item: child, imageURL: store.imageURL(for: child), identifierPrefix: "season", loadFraming: { await store.artworkFraming(for: $0) })
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape([.interaction, .accessibility], Rectangle())
                            .buttonStyle(.plain)
                            .accessibilityElement(children: .ignore)
                            .accessibilityAddTraits(.isButton)
                            .accessibilityLabel(child.name)
                            .accessibilityIdentifier("videoSeason-\(child.id)")
                        }
                    }
                } else {
                    if dynamicTypeSize.isAccessibilitySize {
                        LazyVStack(spacing: 12) {
                            ForEach(children) { episode in
                                episodeLink(episode)
                            }
                        }
                    } else {
                        ScrollView(.horizontal, showsIndicators: false) {
                            LazyHStack(alignment: .top, spacing: 12) {
                                ForEach(children) { episode in
                                    episodeLink(episode)
                                        .containerRelativeFrame(.horizontal) { width, _ in
                                            min(max(340, width * 0.30), width * 0.86)
                                        }
                                }
                            }
                            .scrollTargetLayout()
                        }
                        .scrollTargetBehavior(.viewAligned)
                    }
                }
                NativeTitleSupplement(item: detailItem ?? item, store: store)
                    .padding(.top, 18)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle(item.name)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("videoCollectionScreen")
        .task(id: item.id) { await load() }
        .refreshable { await load() }
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task { await load() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
    }

    @ViewBuilder
    private var playbackActions: some View {
        Button {
            preparePlayback()
        } label: {
            Label(isPreparingPlayback ? "Preparing…" : "Play", systemImage: "play.fill")
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(isPreparingPlayback)
        .accessibilityIdentifier("playButton")

        NativeWatchlistButton(item: item, store: store)
            .buttonStyle(LuminaMediaActionStyle(primary: false))
            .accessibilityIdentifier("videoWatchlistButton")
    }

    private func episodeLink(_ episode: MediaItem) -> some View {
        NavigationLink(value: episode) {
            episodeCard(episode)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(mediaAccessibilityLabel(for: episode) + (episode.userData?.played == true ? ", Watched" : ""))
        .accessibilityIdentifier("videoEpisode-\(episode.id)")
    }

    private func episodeCard(_ episode: MediaItem) -> some View {
        ZStack(alignment: .bottomLeading) {
            ArtworkView(
                url: store.backdropURL(for: episode, maxWidth: 920) ?? store.imageURL(for: episode),
                name: episode.name,
                aspect: 16 / 9,
                loadFraming: { await store.artworkFraming(for: $0) }
            )
            LinearGradient(
                colors: [.clear, LuminaTheme.theater.opacity(0.22), LuminaTheme.theater.opacity(0.96)],
                startPoint: .top,
                endPoint: .bottom
            )
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    if !episode.displaySubtitle.isEmpty {
                        Text(episode.displaySubtitle)
                    }
                    if episode.durationSeconds > 0 {
                        Text(Duration.seconds(episode.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
                    }
                    if episode.userData?.played == true {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(LuminaTheme.spotlight)
                            .accessibilityLabel("Watched")
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.88))
                Text(episode.name)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                if let overview = episode.overview, !overview.isEmpty {
                    Text(overview)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.84))
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                }
            }
            .padding(14)
            .fixedSize(horizontal: false, vertical: dynamicTypeSize.isAccessibilitySize)
            .background {
                if dynamicTypeSize.isAccessibilitySize {
                    LuminaTheme.theater
                }
            }
            if episode.resumeSeconds > 0, episode.durationSeconds > 0 {
                ProgressView(value: min(episode.resumeSeconds / episode.durationSeconds, 1))
                    .progressViewStyle(.linear)
                    .tint(LuminaTheme.spotlight)
                    .frame(height: 3)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 5)
                    .frame(maxWidth: .infinity, alignment: .bottom)
            }
        }
        .clipShape(.rect(cornerRadius: 8))
        .contentShape(Rectangle())
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        detailItem = try? await store.loadItem(item)
        do {
            children = try await store.loadChildren(of: item)
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This collection could not load. Check your connection and try again."
        }
        isLoading = false
    }

    private func preparePlayback() {
        guard !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            defer { isPreparingPlayback = false }
            do {
                let episode = try await playableEpisode()
                playbackRequest = try await store.preparePlayback(for: episode)
            } catch {
                playbackError = (error as? LocalizedError)?.errorDescription ?? "No playable episode is available yet. Check the collection and try again."
            }
        }
    }

    private func playableEpisode() async throws -> MediaItem {
        if let resume = store.resumeItems.first(where: { candidate in
            candidate.type == "Episode" && (candidate.seriesID == item.id || candidate.seasonID == item.id) && candidate.userData?.played != true
        }) {
            return resume
        }

        let seasons: [MediaItem]
        if item.type == "Series" {
            seasons = children.isEmpty ? try await store.loadChildren(of: item) : children
        } else {
            seasons = [item]
        }
        var episodes = [MediaItem]()
        for season in seasons {
            episodes.append(contentsOf: try await store.loadChildren(of: season))
        }
        guard let episode = episodes.first(where: { $0.resumeSeconds > 0 && $0.userData?.played != true })
            ?? episodes.first(where: { $0.userData?.played != true })
            ?? episodes.first else {
            throw VideoCollectionPlaybackError.noEpisodes
        }
        return episode
    }
}

private enum VideoCollectionPlaybackError: LocalizedError {
    case noEpisodes

    var errorDescription: String? {
        switch self {
        case .noEpisodes: "No playable episode is available yet."
        }
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
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    ForEach(NativeMusicSection.allCases, id: \.self) { section in
                        NavigationLink {
                            NativeMusicLibraryView(store: store, section: section)
                        } label: {
                            Label(section.rawValue, systemImage: section.symbol)
                        }
                        .accessibilityIdentifier("musicLibrary-\(section.rawValue.lowercased())")
                    }
                    Divider()
                    Picker("Browse music", selection: $kind) {
                        ForEach(MusicBrowseKind.allCases, id: \.self) { value in Text(value.label).tag(value) }
                    }
                    .accessibilityIdentifier("audioKindPicker")
                } label: { Label("Music options", systemImage: "ellipsis.circle") }
                .accessibilityIdentifier("musicOptionsMenu")
            }
        }
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
        .buttonStyle(LuminaQuietActionStyle())
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
                    MediaCard(item: item, imageURL: store.imageURL(for: item), identifierPrefix: identifierPrefix, loadFraming: { await store.artworkFraming(for: $0) })
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

    @State private var request: PlaybackRequest?
    @State private var preparing = false
    @State private var playbackError: String?
    @State private var detailItem: MediaItem?

    var body: some View {
        FeaturedShelf(
            items: store.featuredItems,
            store: store,
            isInteracting: preparing || request != nil,
            showsOverview: false,
            showsContext: false,
            showsMetadata: false,
            showsProgress: false,
            compactMinimumHeight: 360,
            wideMinimumHeight: 480,
            onOpen: { detailItem = $0 }
        ) { item in
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
        .navigationDestination(item: $detailItem) { item in
            TitleDetailView(item: item, store: store)
                .toolbarVisibility(.visible, for: .navigationBar)
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
            Label(preparing ? "Preparing…" : "Play", systemImage: "play.fill")
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")

        Button { detailItem = item } label: {
            Label("Details", systemImage: "arrow.right")
        }
        .buttonStyle(LuminaMediaActionStyle())
        .accessibilityIdentifier("featuredDetailsButton")
    }
}

@MainActor
private struct VideoPosterWall: View {
    let items: [MediaItem]
    let store: LuminaStore

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .body) private var minimumPosterWidth: CGFloat = 128
    @ScaledMetric(relativeTo: .body) private var posterGap: CGFloat = 10
    @State private var availableWidth: CGFloat = 0

    var body: some View {
        wall(width: availableWidth)
            .frame(maxWidth: .infinity, alignment: .leading)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { availableWidth = $0 }
    }

    @ViewBuilder
    private func wall(width: CGFloat) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            LazyVStack(alignment: .leading, spacing: posterGap) {
                ForEach(items) { item in
                    posterLink(item)
                        .frame(width: min(width, 220), alignment: .leading)
                }
            }
        } else {
            LazyVGrid(
                columns: Array(repeating: GridItem(.flexible(), spacing: posterGap), count: columnCount(for: width)),
                alignment: .leading,
                spacing: posterGap
            ) {
                ForEach(items) { item in
                    posterLink(item)
                }
            }
        }
    }

    private func posterLink(_ item: MediaItem) -> some View {
        NavigationLink(value: item) {
            MediaCard(
                item: item,
                imageURL: store.imageURL(for: item),
                identifierPrefix: "library",
                showsCaption: false,
                loadFraming: { await store.artworkFraming(for: $0) }
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

    private func columnCount(for width: CGFloat) -> Int {
        let count = Int((width + posterGap) / (minimumPosterWidth + posterGap))
        return min(max(count, 2), 6)
    }


}

@MainActor
private struct MediaRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    var wide = true

    @ScaledMetric(relativeTo: .body) private var posterWidth: CGFloat = 132
    @ScaledMetric(relativeTo: .body) private var stillWidth: CGFloat = 276

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title3.weight(.bold))
                .padding(.top, 10)
                .frame(maxWidth: .infinity, alignment: .leading)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(items) { item in
                        NavigationLink(value: item) {
                            MediaCard(
                                item: item,
                                imageURL: wide ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item) : store.posterURL(for: item),
                                identifierPrefix: identifierPrefix,
                                wide: wide,
                                loadFraming: { await store.artworkFraming(for: $0) }
                            )
                            .containerRelativeFrame(.horizontal) { availableWidth, _ in
                                let square = item.isAudio || item.type.hasPrefix("Music")
                                if square { return min(posterWidth * 1.2, availableWidth * 0.4) }
                                if wide { return min(stillWidth, availableWidth < 600 ? min(276, availableWidth * 0.68) : availableWidth * 0.42) }
                                return min(posterWidth, availableWidth * 0.26)
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
    var showsCaption = true
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 6) {
            ArtworkView(url: imageURL, name: item.name, aspect: item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" ? 1 : wide ? 16 / 9 : 2 / 3, symbol: item.isAudio || item.type.hasPrefix("Music") ? "music.note" : "film", loadFraming: loadFraming)
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

            if showsCaption {
                Text(item.name)
                    .font(.caption.weight(.semibold))
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
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                placeholder.overlay {
                    SubjectAwareArtwork(url: url, loadFraming: loadFraming)
                }
            }
            .clipShape(.rect(cornerRadius: 6))
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
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

@MainActor
private struct NativeTitleSupplement: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var related: [MediaItem] = []

    private var cast: [MediaPerson] {
        Array((item.people ?? []).filter { $0.type?.lowercased() == "actor" }.prefix(24))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            if !cast.isEmpty {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Cast").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(alignment: .top, spacing: 16) {
                            ForEach(Array(cast.enumerated()), id: \.offset) { _, person in
                                VStack(alignment: .leading, spacing: 8) {
                                    AsyncImage(url: store.personImageURL(for: person)) { image in
                                        image.resizable().scaledToFill()
                                    } placeholder: {
                                        LuminaTheme.ink.opacity(0.06)
                                            .overlay { Image(systemName: "person.fill").font(.largeTitle).foregroundStyle(LuminaTheme.muted) }
                                    }
                                    .frame(width: 112, height: 148)
                                    .clipShape(.rect(cornerRadius: 8))
                                    .accessibilityHidden(true)
                                    Text(person.name).font(.subheadline.weight(.medium)).lineLimit(2)
                                    if let role = person.role, !role.isEmpty {
                                        Text(role).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(2)
                                    }
                                }
                                .frame(width: 112, alignment: .topLeading)
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                }
                .accessibilityIdentifier("detailCastSection")
            }
            if !related.isEmpty {
                MediaRail(title: "More like this", items: related, store: store, identifierPrefix: "related", wide: false)
                    .accessibilityIdentifier("detailRelatedSection")
            }
        }
        .foregroundStyle(LuminaTheme.ink)
        .task(id: item.id) {
            related = []
            let matches = (try? await store.relatedItems(for: item)) ?? []
            guard !Task.isCancelled else { return }
            related = matches
        }
    }
}

@MainActor
private struct TitleDetailView: View {
    let item: MediaItem
    @Bindable var store: LuminaStore

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
            VStack(alignment: .leading, spacing: 24) {
                detailHeader
                    .padding(.horizontal, -20)
                    .padding(.top, -20)

                VStack(alignment: .leading, spacing: 20) {
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
                .frame(maxWidth: 680, alignment: .leading)
                NativeTitleSupplement(item: currentItem, store: store)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("detailScreen")
        .toolbar {
            if !currentItem.isAudio {
                ToolbarItem(placement: .topBarTrailing) {
                    titleActionsMenu
                }
            }
        }
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

    private var favoriteButton: some View {
        Button {
            updateState(favorite: currentItem.userData?.isFavorite != true)
        } label: {
            Label(currentItem.userData?.isFavorite == true ? "Remove favorite" : "Add favorite", systemImage: currentItem.userData?.isFavorite == true ? "heart.fill" : "heart")
                .frame(minWidth: 132)
                .foregroundStyle(.white)
        }
        .buttonStyle(LuminaMediaActionStyle())
        .controlSize(.large)
        .disabled(isSavingState || isLoadingItem)
        .accessibilityValue(currentItem.userData?.isFavorite == true ? "Favorite" : "Not a favorite")
        .accessibilityIdentifier("videoFavoriteButton")
    }

    private var titleActionsMenu: some View {
        Menu {
            favoriteButton
            Button {
                updateState(played: currentItem.userData?.played != true)
            } label: {
                Label(currentItem.userData?.played == true ? "Mark unwatched" : "Mark watched", systemImage: currentItem.userData?.played == true ? "checkmark.circle.fill" : "checkmark.circle")
            }
            .accessibilityValue(currentItem.userData?.played == true ? "Watched" : "Unwatched")
            .accessibilityIdentifier("videoWatchedButton")

            if currentItem.isPlayable {
                Button {
                    preparePlayback(forceTranscode: true)
                } label: {
                    Label("Convert", systemImage: "arrow.triangle.2.circlepath")
                }
                .disabled(isPreparingPlayback)
                .accessibilityHint("Use compatibility conversion before playback.")
                .accessibilityIdentifier("convertButton")
            }
        } label: {
            Label("More actions", systemImage: "ellipsis.circle")
        }
        .disabled(isSavingState || isLoadingItem)
        .accessibilityIdentifier("titleActionsMenu")
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
        MediaBillboard(item: currentItem, store: store, context: "", showsOverview: false, showsContext: false, compactMinimumHeight: 460, wideMinimumHeight: 540) {
            if currentItem.isPlayable {
                playbackActions
            } else {
                Text("Playback is unavailable for this title.")
                    .font(.callout)
            }
        }
    }

    @ViewBuilder
    private var playbackActions: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 12) {
                playbackButton
                if !currentItem.isAudio { NativeWatchlistButton(item: currentItem, store: store) }
            }
            VStack(alignment: .leading, spacing: 12) {
                playbackButton
                if !currentItem.isAudio { NativeWatchlistButton(item: currentItem, store: store) }
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
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(isPreparingPlayback)
        .accessibilityIdentifier("playButton")
    }

    private var playLabel: String {
        currentItem.resumeSeconds > 5 ? "Resume" : "Play"
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
    return [GridItem(.adaptive(minimum: 96, maximum: 166), spacing: 10, alignment: .top)]
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
