import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct HomeLibraryView<Header: View>: View {
    let store: LuminaStore
    let homeLayout: LuminaHomeLayout
    let feed: LuminaHomeFeed
    @Binding var category: HomeBrowseCategory
    let stacksCategoryHeader: Bool
    let usesLandscapeHeader: Bool
    let uiTestingNavigationDiagnostics: String?
    let scrollsHeader: Bool
    @ViewBuilder let header: Header
    @State private var initialHomeLoadOwner: MobileHomeSessionScope?
    @State private var homeRowsRefreshTask: Task<Void, Never>?
    @State private var homeRowsRefreshID: UUID?
    @State private var homeScrollPhase: ScrollPhase = .idle
    @State private var homePullStartedAtTop = false
    @State private var homePullArmed = false
    @State private var isHomePullRefreshing = false
    @State private var needsHomeRowsRefresh = false
    #if LUMINA_UI_TESTING
    @State private var uiTestingHomeRefreshCount = 0
    @State private var uiTestingHomeLifecycleRefreshCount = 0
    #endif
    @Environment(\.layoutDirection) private var layoutDirection
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @State private var scrollPosition = ScrollPosition(edge: .top)
    @State private var hiddenRecommendationID: String?
    @State private var showRecommendationUndo = false

    private var refinement: HomeRefinementResult {
        feed.refined(HomeRefinement(category: category), heroItems: store.featuredItems)
    }

    private func refinedItems(_ items: [MediaItem]) -> [MediaItem] {
        feed.refined(HomeRefinement(category: category), heroItems: items).items
    }


    private var shelves: [HomeShelfPreference] {
        homeLayout.shelves
    }

    private var expectedCompatibilityShelves: Set<HomeShelfID> {
        Set(shelves.lazy.filter(\.visible).map(\.id)).intersection([.continueWatching, .nextUp, .newInLibrary])
    }

    var body: some View {
        let snapshot = refinement
        GeometryReader { geometry in
        let emptyHeaderClearance = scrollsHeader || stacksCategoryHeader || usesLandscapeHeader ? geometry.safeAreaInsets.top + 76 : 0
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if !snapshot.items.isEmpty {
                    LibraryFeaturedView(store: store, items: snapshot.items,
                        compactMinimumHeight: max(460, geometry.size.height * 0.78),
                        compactArtworkMinimumHeight: geometry.size.height + geometry.safeAreaInsets.top + geometry.safeAreaInsets.bottom,
                        wideMinimumHeight: min(720, max(540, geometry.size.height * 0.78)),
                        topContentClearance: geometry.safeAreaInsets.top + 32,
                        bottomContentClearance: geometry.safeAreaInsets.bottom,
                        onWatchlistChanged: { await feed.load() })
                        .padding(.horizontal, -20)
                }
                ForEach(shelves.filter(\.visible)) { shelf in
                    homeShelf(shelf.id, rows: snapshot.rows)
                }
                if category != .all, snapshot.items.isEmpty,
                   !snapshot.rows.contains(where: \.hasItems), feed.hasLoaded {
                    ContentUnavailableView {
                        Label("No matches on Home", glyph: .filter)
                    } description: {
                        Text("Choose Home or add something to your watchlist.")
                    }
                    .frame(minHeight: max(0, geometry.size.height - emptyHeaderClearance - 20))
                }
                if store.isHomeBusy || store.isBusy || (!feed.hasLoaded && feed.isLoading) {
                    ProgressView("Loading your library…")
                } else if store.featuredItems.isEmpty && store.errorMessage == nil && !feed.rows.contains(where: \.hasItems) {
                    EmptyLibraryView { Task { await reloadHome() } }
                }
                if let error = feed.errorMessage {
                    InlineErrorView(message: error) { Task { await reloadHome() } }
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await reloadHome() } }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 20)
            .frame(maxWidth: .infinity)
            .padding(.top, snapshot.items.isEmpty ? emptyHeaderClearance : 0)
            .overlay(alignment: .top) {
                header
                    .environment(\.colorScheme, snapshot.items.isEmpty ? colorScheme : .dark)
                    .padding(.top, geometry.safeAreaInsets.top)
            }
        }
        .ignoresSafeArea(edges: snapshot.items.isEmpty ? [] : .top)
        .modifier(HomeHeroScrollEdge(isEnabled: !snapshot.items.isEmpty))
        .scrollClipDisabled()
        .scrollPosition($scrollPosition)
        .onChange(of: category) { _, _ in
            scrollPosition.scrollTo(edge: .top)
        }
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top <= -80
        } action: { _, pastThreshold in
            if homeScrollPhase == .interacting && homePullStartedAtTop {
                homePullArmed = pastThreshold
            }
        }
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top < -2
        } action: { _, isBeyondTop in
            // UIKit's status tap can settle above SwiftUI's full-bleed origin
            // without reporting a scroll phase. Leave touch-driven pulls alone.
            guard isBeyondTop, homeScrollPhase == .idle, !snapshot.items.isEmpty else { return }
            scrollPosition.scrollTo(edge: .top)
        }
        .onScrollPhaseChange { previous, phase, context in
            homeScrollPhase = phase
            let offset = context.geometry.contentOffset.y + context.geometry.contentInsets.top
            if phase == .tracking || (phase == .interacting && previous != .tracking) {
                homePullStartedAtTop = abs(offset) <= 2
                homePullArmed = false
            } else if previous == .interacting {
                if homePullArmed { isHomePullRefreshing = true }
                homePullStartedAtTop = false
                homePullArmed = false
            }
        }
        // Keep refresh feedback out of the scroll content so it cannot hold the hero down.
        .overlay(alignment: .top) {
            if homePullArmed || isHomePullRefreshing {
                ProgressView()
                    .tint(snapshot.items.isEmpty ? LuminaTheme.ink : .white)
                    .padding(.top, snapshot.items.isEmpty ? 8 : geometry.safeAreaInsets.top + 8)
                    .allowsHitTesting(false)
            }
        }
        .accessibilityAction(named: "Refresh Home") { isHomePullRefreshing = true }
        .task(id: isHomePullRefreshing) {
            guard isHomePullRefreshing else { return }
            defer { isHomePullRefreshing = false }
            #if LUMINA_UI_TESTING
            uiTestingHomeRefreshCount += 1
            #endif
            await reloadHome(refreshingLegacyRecent: true)
        }
        }

        .navigationBarTitleDisplayMode(.inline)
        .background(LuminaTheme.paper)
        .toolbarVisibility(scrollsHeader || usesLandscapeHeader ? .hidden : .visible, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarColorScheme(snapshot.items.isEmpty ? nil : .dark, for: .navigationBar)
        .toolbar {
            if !stacksCategoryHeader && !scrollsHeader && !usesLandscapeHeader {
                if #available(iOS 26.0, *) {
                    ToolbarItem(placement: .topBarLeading) { HomeHeroChrome().fixedSize() }
                        .sharedBackgroundVisibility(.hidden)
                } else {
                    ToolbarItem(placement: .topBarLeading) { HomeHeroChrome().fixedSize() }
                }
            }
        }
        .overlay(alignment: .topLeading) {
            if stacksCategoryHeader {
                PortraitNavigationHeader { EmptyView() }
                    .environment(\.colorScheme, snapshot.items.isEmpty ? colorScheme : .dark)
            } else if usesLandscapeHeader {
                // The system stacks its status and tab controls on the right in
                // this posture. Keep Home actions beside the masthead.
                HStack(spacing: 12) {
                    HomeHeroChrome().fixedSize()
                    HStack(spacing: 4) {
                        homeUtilityActions
                        NativeNowPlayingControls(store: store)
                    }
                    .labelStyle(.iconOnly)
                    .buttonStyle(NavigationUtilityButtonStyle())
                    .padding(4)
                    .background(.regularMaterial, in: .capsule)
                }
                .environment(\.layoutDirection, layoutDirection)
                .padding(.leading, 20)
                .padding(.top, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .environment(\.layoutDirection, .leftToRight)
                .environment(\.colorScheme, snapshot.items.isEmpty ? colorScheme : .dark)
            }
        }
        .portraitNavigationRail(isEnabled: stacksCategoryHeader, store: store) {
            homeUtilityActions
        }
        .ignoresSafeArea(.container, edges: stacksCategoryHeader ? .top : [])
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("homeScreen")
        .alert("Recommendation hidden", isPresented: $showRecommendationUndo) {
            Button("Undo") { if let hiddenRecommendationID { Task { await feed.undoHide(hiddenRecommendationID) } } }
            Button("Done", role: .cancel) {}
        }
        #if LUMINA_UI_TESTING
        .accessibilityValue("\(uiTestingNavigationDiagnostics ?? "") refreshes=\(uiTestingHomeRefreshCount) lifecycleRefreshes=\(uiTestingHomeLifecycleRefreshCount)")
        #endif
        .task(id: MobileHomeSessionScope(store.session)) {
            cancelHomeRowsRefresh()
            needsHomeRowsRefresh = scenePhase == .background
            initialHomeLoadOwner = nil
            guard let owner = MobileHomeSessionScope(store.session) else { return }
            await reloadHome()
            guard !Task.isCancelled, MobileHomeSessionScope(store.session) == owner else { return }
            initialHomeLoadOwner = owner
            refreshHomeRowsAfterInitialLoad()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                needsHomeRowsRefresh = true
                cancelHomeRowsRefresh()
            } else if phase == .active {
                refreshHomeRowsAfterInitialLoad()
            }
        }
        .onDisappear { cancelHomeRowsRefresh() }
    }

    @ViewBuilder private var homeUtilityActions: some View {
        NavigationLink {
            HomeSearchView(store: store)
                .toolbarVisibility(.visible, for: .navigationBar)
                .toolbar { NativeNowPlayingToolbar(store: store) }
        } label: { Label("Search", glyph: .search) }
        .accessibilityIdentifier("homeSearchButton")
        Menu {
            Button("Home") { category = .all }
                .accessibilityIdentifier("homeMenu-home")
            Button("Watchlist") { category = .watchlist }
                .accessibilityIdentifier("homeMenu-watchlist")
        } label: { Label("Browse", glyph: .moreHorizontal) }
        .accessibilityIdentifier("homeBrowseMenu")
        if !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
            NavigationLink {
                NativeOfflineLibraryView(store: store)
                    .toolbarVisibility(.visible, for: .navigationBar)
            } label: { Label("Downloads", glyph: .download) }
            .accessibilityIdentifier("homeDownloadsButton")
        }
    }

    private func refreshHomeRowsAfterInitialLoad() {
        guard scenePhase == .active, homeRowsRefreshTask == nil,
              needsHomeRowsRefresh, let scope = MobileHomeSessionScope(store.session),
              initialHomeLoadOwner == scope else { return }
        let id = UUID()
        needsHomeRowsRefresh = false
        #if LUMINA_UI_TESTING
        uiTestingHomeLifecycleRefreshCount += 1
        #endif
        homeRowsRefreshID = id
        homeRowsRefreshTask = Task {
            defer {
                if homeRowsRefreshID == id {
                    homeRowsRefreshTask = nil
                    homeRowsRefreshID = nil
                }
            }
            await reloadHome()
        }
    }

    private func cancelHomeRowsRefresh() {
        homeRowsRefreshTask?.cancel()
        homeRowsRefreshTask = nil
        homeRowsRefreshID = nil
    }

    private func reloadHome(refreshingLegacyRecent: Bool = false) async {
        // Match the established bootstrap behavior: a persisted recent shelf
        // admits layout, rich rows, and navigation without a second legacy Home
        // load. A truly empty first visit still starts all shelves in parallel.
        let coldCompatibility = (!homeLayout.hasLoaded || !feed.hasLoaded) && store.recentItems.isEmpty
        let expected = expectedCompatibilityShelves
        let legacyRecent = refreshingLegacyRecent ? expected.intersection([.newInLibrary]) : []
        let richCompatibility = expected.subtracting(legacyRecent)
        async let layout: Void = homeLayout.load()
        async let rows: Void = feed.load()
        async let navigation: Void = store.refreshNavigationCategories()
        if coldCompatibility {
            // Preserve the current cold first-shelf behavior. Once this member's
            // rich Home snapshot is loaded, warm refreshes avoid these requests.
            async let legacy: Void = store.loadHomeFallback(for: expected)
            _ = await (layout, rows, navigation, legacy)
        } else {
            _ = await (layout, rows, navigation)
        }
        guard !Task.isCancelled else { return }
        let missing = await store.reconcileRichHomeRows(
            feed.rows,
            completedShelfIDs: feed.completedShelfIDs,
            loadSucceeded: feed.lastLoadSucceeded,
            expectedCompatibilityShelves: richCompatibility
        )
        let fallback = missing.union(legacyRecent)
        if !coldCompatibility, !fallback.isEmpty {
            await store.loadHomeFallback(for: fallback)
        }
    }

    @ViewBuilder
    private func homeShelf(_ id: HomeShelfID, rows: [HomeFeedRow]) -> some View {
        Group {
            let richRows = rows.filter { $0.shelfID == id }
            if !richRows.isEmpty {
                ForEach(richRows) { row in
                    if !row.items.isEmpty {
                    MediaRail(title: row.displayTitle, items: row.items, store: store,
                              identifierPrefix: id == .continueWatching ? "resumeItem" : id == .nextUp ? "nextUpItem" : id == .newInLibrary ? "recentItem" : "homeRow-\(row.id)",
                              wide: row.shape == .still && id != .continueWatching && id != .nextUp,
                              feed: feed, onHide: { hiddenRecommendationID = $0; showRecommendationUndo = true })
                    }
                    if store.streamingAvailable && !(row.externalItems ?? []).isEmpty { HomeExternalRail(row: row, store: store) }
                }
            } else if !feed.hasLoaded || feed.completedShelfIDs?.contains(id) != true {
            switch id.rawValue {
            case "continue":
                if !store.resumeItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.resumeItems), store: store, identifierPrefix: "resumeItem", wide: false)
                }
            case "next_up":
                if !store.nextUpItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.nextUpItems), store: store, identifierPrefix: "nextUpItem", wide: false)
                }
            case "new_in_library":
                if !store.recentItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.recentItems), store: store, identifierPrefix: "recentItem", wide: false)
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
struct HomeSearchView: View {
    let store: LuminaStore
    @State private var query = ""
    @State private var searchPresented = true
    @FocusState private var searchFocused: Bool
    @State private var results: [MediaItem] = []
    @State private var loading = false
    @State private var message: String?
    @State private var selectedSearchItem: MediaItem?
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var presentedPlayback: PlaybackModel?
    @State private var startingTrack = false
    @State private var playbackError: String?

    var body: some View {
        Group {
        if usesStandardPhonePushedChrome {
            searchContent
                .navigationDestination(item: $selectedSearchItem) { item in
                    searchDestination(for: item)
                }
                .standardPhonePushedChrome(store: store)
                .task { searchFocused = true }
        } else {
            searchContent
                .searchable(text: $query, isPresented: $searchPresented, prompt: "Films, shows, and music")
        }
        }
        .nativeAudioPlayer(playback: $presentedPlayback, store: store)
        .alert("Playback unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
    }

    private var searchContent: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if usesStandardPhonePushedChrome {
                    HStack(spacing: 10) {
                        LuminaIcon(glyph: .search, size: 20).foregroundStyle(.secondary)
                        TextField("Search your library", text: $query)
                            .textFieldStyle(.plain)
                            .focused($searchFocused)
                            .submitLabel(.search)
                            .accessibilityIdentifier("homeSearchField")
                        if !query.isEmpty {
                            Button { query = "" } label: {
                                Label("Clear search", glyph: .close)
                                    .labelStyle(.iconOnly)
                                    .frame(minWidth: 44, minHeight: 44)
                                    .contentShape(.rect)
                            }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("homeSearchClearButton")
                        }
                    }
                    .frame(minHeight: 44)
                    .padding(.horizontal, 12)
                    .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 12))
                }
                if loading { ProgressView("Searching…").frame(maxWidth: .infinity) }
                if let message { Text(message).foregroundStyle(.secondary) }
                if results.isEmpty, !loading {
                    ContentUnavailableView {
                        Label(query.isEmpty ? "Search your library" : "No results", glyph: .search)
                    } description: {
                        Text(query.isEmpty ? "Find films, shows, episodes, and music." : "Try another title.")
                    }
                }
                ForEach(results) { item in
                    Group {
                        if item.isAudio {
                            Button { playTrack(item) } label: { searchResultRow(item) }
                                .disabled(startingTrack || audioSession.busy)
                        } else if usesStandardPhonePushedChrome {
                            Button { selectedSearchItem = item } label: {
                                searchResultRow(item)
                            }
                        } else {
                            NavigationLink(value: item) {
                                searchResultRow(item)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("homeSearchResult-\(item.id)")
                }
            }.padding(20)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("Search")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("homeSearchScreen")
        .task(id: query.trimmingCharacters(in: .whitespacesAndNewlines)) {
            let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
            results = []; message = nil
            guard !term.isEmpty else { loading = false; return }
            loading = true
            do {
                try await Task.sleep(for: .milliseconds(300))
                let found = try await store.searchHome(query: term)
                try Task.checkCancellation()
                results = found; loading = false
            } catch {
                guard !Task.isCancelled else { return }
                message = "Search could not finish. Try again."; loading = false
            }
        }
    }

    private func searchResultRow(_ item: MediaItem) -> some View {
        HStack(spacing: 16) {
            ArtworkView(url: store.imageURL(for: item), name: item.name)
                .frame(width: 64, height: 96).clipped()
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name).font(.headline)
                if !item.displaySubtitle.isEmpty {
                    Text(item.displaySubtitle).font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Spacer()
            LuminaIcon(glyph: item.isAudio ? .play : .forward, size: 20).foregroundStyle(.secondary)
        }
    }

    private func playTrack(_ item: MediaItem) {
        guard !startingTrack else { return }
        startingTrack = true
        let sourceItems = results.filter(\.isAudio)
        Task {
            defer { startingTrack = false }
            do { presentedPlayback = try await audioSession.play(items: sourceItems, startingAt: item, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

    @ViewBuilder
    private func searchDestination(for item: MediaItem) -> some View {
        if item.type == "MusicAlbum" || item.type == "MusicArtist" {
            MusicCollectionView(item: item, store: store)
        } else if item.type == "Episode" {
            EpisodeCollectionDestination(item: item, store: store)
        } else if item.isCollection {
            VideoCollectionView(item: item, store: store)
        } else {
            TitleDetailView(item: item, store: store)
        }
    }
}

@MainActor
private struct HomeExternalRail: View {
    let row: HomeFeedRow
    @Bindable var store: LuminaStore
    @Environment(\.playRemoteStream) private var playRemoteStream

    private var nativeRemoteAvailable: Bool {
        // Navigation is loaded with Home and reflects the same member-scoped
        // remote readiness, avoiding a second capabilities round trip just to
        // decide whether a Home card should open natively.
        store.streamingAvailable
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(row.displayTitle).font(.title3.weight(.bold)).padding(.top, 10)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(row.externalItems ?? []) { item in
                        if let destination = item.sourceURL {
                            if nativeRemoteAvailable {
                                Button { playRemoteStream(item) } label: {
                                    card(item, actionTitle: "Play", actionGlyph: .play)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(item.title), \(item.subtitle). Play in Lumina.")
                                .accessibilityIdentifier("homeExternal-\(item.id)")
                            } else {
                                Link(destination: destination) {
                                    card(item, actionTitle: "Open source", actionGlyph: .externalLink)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(item.title), \(item.subtitle). Open source app or website.")
                                .accessibilityIdentifier("homeExternal-\(item.id)")
                            }
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .modifier(LuminaCarouselEdges())
        }
    }

    private func card(_ item: HomeExternalItem, actionTitle: String, actionGlyph: LuminaGlyph) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ArtworkView(url: item.artworkURL, name: item.title, aspect: 16 / 9)
            Text(item.title).font(.caption.weight(.semibold)).lineLimit(2)
            Text(item.subtitle).font(.caption2).foregroundStyle(LuminaTheme.muted).lineLimit(1)
            Label(actionTitle, glyph: actionGlyph, size: 16).font(.caption2)
        }
        .containerRelativeFrame(.horizontal) { width, _ in min(276, width * 0.68) }
    }
}
