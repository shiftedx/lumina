import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVHomeSessionScope: Hashable {
    let serverURL: URL
    let userID: String
    let accessToken: String

    init?(_ session: LuminaSession?) {
        guard let session else { return nil }
        serverURL = session.serverURL
        userID = session.userID
        accessToken = session.accessToken
    }
}

/// Keep the visible Video controls and the last focused card while another tab
/// is selected. The destination view is recreated on a tab change, so its own
/// `@State` would otherwise discard the person's search and place focus back
/// in the header when they return with Menu.
struct TVVideoBrowseState {
    var query = ""
    var kind: VideoBrowseKind = .movies
    var sort: VideoSort = .name
    var filters = VideoFilters()
    var focusedItemID: String?
}

/// Audio uses the same root-owned browsing state as Video.
struct TVAudioBrowseState {
    var kind: MusicBrowseKind = .albums
    var query = ""
    var focusedItemID: String?
}

struct TVHomeSearchView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var results: [MediaItem] = []
    @State private var loading = false
    @State private var message: String?
    @State private var searchTask: Task<Void, Never>?
    @State private var searchGeneration = 0
    @FocusState private var searchFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                HStack {
                    TextField("Search films, shows, and music", text: $query)
                        .font(TVTypography.body)
                        .focused($searchFocused)
                        .onSubmit { search() }
                        .accessibilityIdentifier("homeSearchField")
                    Button("Search", glyph: .search, size: 24) { search() }
                        .buttonStyle(LuminaUnderlineTabStyle())
                    Button("Done") { dismiss() }.buttonStyle(LuminaUnderlineTabStyle())
                }
                if loading { ProgressView("Searching…") }
                if let message { Text(message).foregroundStyle(LuminaTheme.muted) }
                TVRail(title: "Results", items: results, store: store, poster: true,
                    identifierPrefix: "homeSearchResult", onOpen: onOpen)
            }.padding(64)
        }
        .background(LuminaTheme.theater)
        .accessibilityIdentifier("homeSearchScreen")
        .defaultFocus($searchFocused, true)
        .onChange(of: query) { _, _ in
            searchTask?.cancel()
            searchGeneration += 1
            loading = false
            results = []
            message = nil
        }
        .onDisappear { searchTask?.cancel(); searchGeneration += 1 }
        .onExitCommand { dismiss() }
    }

    private func search() {
        searchTask?.cancel()
        searchGeneration += 1
        let generation = searchGeneration
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { results = []; message = nil; return }
        loading = true; message = nil
        searchTask = Task {
            do {
                let found = try await store.searchHome(query: term)
                guard !Task.isCancelled, generation == searchGeneration else { return }
                results = found
                loading = false
                if found.isEmpty { message = "No results. Try another title." }
            } catch {
                guard !Task.isCancelled, generation == searchGeneration else { return }
                loading = false
                message = "Search could not finish. Try again."
            }
        }
    }
}

struct TVHomeView<Header: View>: View {
    let store: LuminaStore
    let layout: LuminaHomeLayout
    let feed: LuminaHomeFeed
    @Binding var heroSelection: String?
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    let onRequestHeaderFocus: () -> Void
    @ViewBuilder let header: Header
    @Environment(\.scenePhase) private var scenePhase
    #if LUMINA_UI_TESTING
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var uiTestingHomeLifecycleRefreshCount = 0
    #endif

    @State private var loadingInitialLibrary = false
    @State private var initialHomeLoadOwner: TVHomeSessionScope?
    @State private var homeRefreshTask: Task<Void, Never>?
    @State private var homeRefreshID: UUID?
    @State private var needsHomeRefresh = false
    @State private var hiddenRecommendationID: String?
    @State private var showRecommendationUndo = false
    private var shelves: [HomeShelfPreference] {
        layout.shelves
    }

    var body: some View {
        let heroItems = store.featuredItems
        let rowsByShelf = Dictionary(grouping: feed.rows.filter { !$0.items.isEmpty || !($0.externalItems ?? []).isEmpty }, by: \.shelfID)
        let showsContinueWatching = shelves.contains { $0.id == .continueWatching && $0.visible }
            && (rowsByShelf[.continueWatching]?.contains { !$0.items.isEmpty } == true
                || (feed.completedShelfIDs?.contains(.continueWatching) != true && !store.resumeItems.isEmpty))
        GeometryReader { geometry in
            TVHeroScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if !heroItems.isEmpty {
                        TVFeaturedView(
                            store: store,
                            items: heroItems,
                            heroSelection: $heroSelection,
                            onOpen: onOpen,
                            onPlayback: onPlayback,
                            focusRequest: focusRequest,
                            onRequestHeaderFocus: onRequestHeaderFocus,
                            minimumHeight: showsContinueWatching
                                ? min(560, max(440, geometry.size.height * 0.50))
                                : min(900, max(620, geometry.size.height * 0.82)),
                            onWatchlistChanged: { await feed.load() }
                        )
                            .focusedValue(\.tvHeroRegion, true)
                            .focusSection()
                    } else if store.isHomeBusy || (!feed.hasLoaded && feed.isLoading) {
                        ProgressView("Loading Home…").frame(maxWidth: .infinity, minHeight: 650)
                    } else {
                        TVMessageView(title: "Your next great watch starts here", message: store.errorMessage ?? "Add media to Lumina to fill Home with your household library.", actionTitle: "Refresh") {
                            Task { await reloadHome() }
                        }
                        .padding(.top, geometry.safeAreaInsets.top + 112)
                    }
                    if feed.hasLoaded {
                        ForEach(shelves.filter(\.visible)) { shelf in
                        if let richRows = rowsByShelf[shelf.id] {
                        ForEach(richRows) { row in
                            TVRail(title: row.displayTitle, items: row.items, store: store,
                                   poster: row.shelfID == .nextUp || (row.shelfID != .continueWatching && row.shape == .poster),
                                   identifierPrefix: row.shelfID == .continueWatching ? "resumeItem" : row.shelfID == .nextUp ? "nextUpItem" : "homeRow-\(row.id)",
                                   onOpen: onOpen, feed: feed,
                                   onHide: { hiddenRecommendationID = $0; showRecommendationUndo = true })
                                .focusSection()
                        }
                        } else if feed.completedShelfIDs?.contains(shelf.id) != true {
                            homeShelf(shelf.id)
                        }
                        }
                    } else {
                        ForEach(shelves.filter(\.visible)) { shelf in
                            homeShelf(shelf.id)
                        }
                    }
                    if let error = feed.errorMessage {
                        TVMessageView(title: "Some Home shelves could not load", message: error, actionTitle: "Try again") {
                            Task { await reloadHome() }
                        }
                    }
                }
                .padding(.bottom, 80)
                .overlay(alignment: .top) {
                    header
                }
            }
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .accessibilityIdentifier("homeScreen")
        .alert("Recommendation hidden", isPresented: $showRecommendationUndo) {
            Button("Undo") { if let hiddenRecommendationID { Task { await feed.undoHide(hiddenRecommendationID) } } }
            Button("Done", role: .cancel) {}
        }
        #if LUMINA_UI_TESTING
        .accessibilityValue("\(audioSession.uiTestingTVRestoreDiagnostics) lifecycleRefreshes=\(uiTestingHomeLifecycleRefreshCount)")
        #endif
        .task(id: TVHomeSessionScope(store.session)) {
            let scope = TVHomeSessionScope(store.session)
            let preservesAdmission = initialHomeLoadOwner == scope
            cancelHomeRefresh()
            needsHomeRefresh = preservesAdmission ? needsHomeRefresh || scenePhase == .background : scenePhase == .background
            initialHomeLoadOwner = nil
            guard let owner = scope else { return }
            let needsInitialLibraryLoad = !layout.hasLoaded || !feed.hasLoaded
            loadingInitialLibrary = needsInitialLibraryLoad
            defer { loadingInitialLibrary = false }
            await reloadHome(refreshingConfiguration: needsInitialLibraryLoad)
            guard !Task.isCancelled, TVHomeSessionScope(store.session) == owner else { return }
            initialHomeLoadOwner = owner
            refreshAfterForegrounding()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                needsHomeRefresh = true
                cancelHomeRefresh()
            } else if phase == .active {
                refreshAfterForegrounding()
            }
        }
        .onChange(of: store.resumeItems) { _, _ in
            let richResume = feed.rows.first(where: { $0.shelfID == .continueWatching })?.items
                .filter { $0.isPlayable && !$0.isAudio } ?? []
            let isFreshRichResumePublication = feed.lastLoadSucceeded
                && feed.completedShelfIDs?.contains(.continueWatching) == true
                && store.resumeItems == richResume
            if feed.hasLoaded, !loadingInitialLibrary, !isFreshRichResumePublication { Task { await feed.load() } }
        }
        .onDisappear { cancelHomeRefresh(preservingAdmission: true) }
    }

    private func refreshAfterForegrounding() {
        guard scenePhase == .active, needsHomeRefresh, homeRefreshTask == nil,
              let owner = TVHomeSessionScope(store.session), initialHomeLoadOwner == owner else { return }
        let id = UUID()
        needsHomeRefresh = false
        #if LUMINA_UI_TESTING
        uiTestingHomeLifecycleRefreshCount += 1
        #endif
        homeRefreshID = id
        homeRefreshTask = Task {
            defer {
                if homeRefreshID == id {
                    homeRefreshTask = nil
                    homeRefreshID = nil
                }
            }
            await reloadHome(refreshingLegacyRecent: true)
        }
    }

    private func cancelHomeRefresh(preservingAdmission: Bool = false) {
        if preservingAdmission, homeRefreshTask != nil { needsHomeRefresh = true }
        homeRefreshTask?.cancel()
        homeRefreshTask = nil
        homeRefreshID = nil
    }

    private var expectedCompatibilityShelves: Set<HomeShelfID> {
        Set(shelves.lazy.filter(\.visible).map(\.id)).intersection([.continueWatching, .nextUp, .newInLibrary])
    }

    private func reloadHome(refreshingLegacyRecent: Bool = false, refreshingConfiguration: Bool = true) async {
        let coldCompatibility = !layout.hasLoaded || !feed.hasLoaded
        let expected = expectedCompatibilityShelves
        let legacyRecent = refreshingLegacyRecent ? expected.intersection([.newInLibrary]) : []
        let richCompatibility = expected.subtracting(legacyRecent)
        async let rows: Void = feed.load()
        if refreshingConfiguration {
            async let savedLayout: Void = layout.load()
            async let navigation: Void = store.refreshNavigationCategories()
            if coldCompatibility {
                // Keep the established cold remote focus path.
                async let legacy: Void = store.loadHomeFallback(for: expected)
                _ = await (savedLayout, navigation, legacy)
            } else {
                _ = await (savedLayout, navigation)
            }
        }
        await rows
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

    @ViewBuilder private func homeShelf(_ id: HomeShelfID) -> some View {
        switch id {
        case .continueWatching:
            if !store.resumeItems.isEmpty {
                TVRail(title: id.title, items: store.resumeItems, store: store, onOpen: onOpen).focusSection()
            }
        case .nextUp:
            if !store.nextUpItems.isEmpty {
                TVRail(title: id.title, items: store.nextUpItems, store: store, poster: true, identifierPrefix: "nextUpItem", onOpen: onOpen).focusSection()
            }
        case .newInLibrary:
            if !store.recentItems.isEmpty {
                TVRail(title: id.title, items: store.recentItems, store: store, poster: true, identifierPrefix: "recentItem", onOpen: onOpen).focusSection()
            }
        default:
            EmptyView()
        }
    }
}
