import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct LibraryShellView: View {
    @Bindable var store: LuminaStore
    let onConnectTV: () -> Void
    @Environment(\.colorScheme) private var colorScheme
    @State private var destination: LibraryDestination = .home
    @State private var homeNavigationID = UUID()
    #if LUMINA_UI_TESTING
    @State private var homeNavigationGeneration = 0
    @State private var uiTestingHomeNavigationDiagnostics = "route=root generation=0"
    #endif
    @State private var libraryPath = NavigationPath()
    @State private var homeCategory: HomeBrowseCategory = .all
    @State private var category: LibraryCategory = .movies
    @State private var homeLayout = LuminaHomeLayout()
    @State private var homeFeed = LuminaHomeFeed(includesExternalSources: true)
    @State private var homeSessionScope: MobileHomeSessionScope?

    var body: some View {
        // Keep the system TabView alive for selection and navigation state.
        // iPad uses one unified category row instead of its large top tab pill;
        // iPhone and Duo retain the system-managed tab placement.
        GeometryReader { geometry in
            let displayWidth = geometry.size.width + geometry.safeAreaInsets.leading + geometry.safeAreaInsets.trailing
            let displayHeight = geometry.size.height + geometry.safeAreaInsets.top + geometry.safeAreaInsets.bottom
            // Standard phones use their bottom tabs; Duo's larger display keeps its category header.
            let showsCategoryHeader = usesIPadHeaderNavigation || min(displayWidth, displayHeight) >= 450
            let usesClosedCategoryRow = !usesIPadHeaderNavigation && showsCategoryHeader && displayWidth < 600
            let usesLandscapeHomeHeader = !usesIPadHeaderNavigation && displayHeight >= 600 && displayWidth > displayHeight
            // The system has already removed its camera/status region from this width.
            let closedHeaderInsets = LuminaTheme.contentGutter + 44 + 12 + 12 // Logo and its two gaps.
            let categoryBarWidth = min(560, max(100, geometry.size.width - (usesClosedCategoryRow ? closedHeaderInsets : 180)))
            destinations(categoryBarWidth: categoryBarWidth,
                         protectedHeaderCategoryWidth: headerCategoryWidth(for: geometry.size.width),
                         showsCategoryHeader: showsCategoryHeader,
                         usesClosedCategoryRow: usesClosedCategoryRow,
                         stacksCategoryHeader: !usesIPadHeaderNavigation && displayWidth >= 600 && displayWidth < displayHeight,
                         usesLandscapeHomeHeader: usesLandscapeHomeHeader)
            #if LUMINA_UI_TESTING
            .overlay {
                Color.clear.accessibilityElement()
                    .accessibilityIdentifier("navigationOcclusions")
                    .accessibilityValue(navigationOcclusions(in: geometry))
                    .allowsHitTesting(false)
            }
            #endif
        }
        .task(id: MobileHomeSessionScope(store.session)) {
            let scope = MobileHomeSessionScope(store.session)
            homeLayout.configure(session: store.session)
            homeFeed.configure(session: store.session)
            homeSessionScope = scope
        }
    }

    #if LUMINA_UI_TESTING
    private func navigationOcclusions(in geometry: GeometryProxy) -> String {
        guard #available(iOS 27.1, *) else { return "" }
        let origin = geometry.frame(in: .global).origin
        return geometry.reservedRegions(kind: .occlusion, layoutDirectionBehavior: .fixed)
            .filter(\.isActive).map { region in
                let frame = region.frame.offsetBy(dx: origin.x, dy: origin.y)
                return "\(frame.minX),\(frame.minY),\(frame.width),\(frame.height)"
            }.joined(separator: ";")
    }
    #endif

    private func destinations(categoryBarWidth: CGFloat, protectedHeaderCategoryWidth: CGFloat,
                              showsCategoryHeader: Bool, usesClosedCategoryRow: Bool, stacksCategoryHeader: Bool,
                              usesLandscapeHomeHeader: Bool) -> some View {
        TabView(selection: Binding(get: { destination }, set: { selected in
            if selected == .home {
                homeCategory = .all
                resetHomeNavigation()
            } else if selected == .library, !libraryPath.isEmpty {
                var transaction = Transaction(animation: nil)
                transaction.disablesAnimations = true
                withTransaction(transaction) { libraryPath = NavigationPath() }
            }
            destination = selected
        })) {
            Tab("Home", image: LuminaGlyph.home.assetName(for: destination == .home ? .solid : .outline), value: .home) {
                destinationStack(owner: .home, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                 usesCustomHeader: stacksCategoryHeader || usesLandscapeHomeHeader) {
                    if homeSessionScope == MobileHomeSessionScope(store.session) {
                        HomeLibraryView(store: store, homeLayout: homeLayout, feed: homeFeed,
                                        category: $homeCategory, stacksCategoryHeader: stacksCategoryHeader,
                                        usesLandscapeHeader: usesLandscapeHomeHeader,
                                        uiTestingNavigationDiagnostics: homeNavigationDiagnosticValue,
                                        scrollsHeader: usesIPadHeaderNavigation,
                                        header: { if usesIPadHeaderNavigation { iPadHomeHeader } }
                        )
                    } else {
                        ProgressView("Loading Home…")
                    }
                }
                .id(homeNavigationID)
                #if LUMINA_UI_TESTING
                .onAppear { recordHomeNavigationDiagnostic("root") }
                #endif
            }
            if LibraryCategory.allCases.contains(where: isLibraryCategoryAvailable) {
            Tab("Library", image: LuminaGlyph.library.assetName(for: destination == .library ? .solid : .outline), value: .library) {
                destinationStack(owner: .library, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                 usesCustomHeader: stacksCategoryHeader, path: $libraryPath) {
                    libraryContent(usesPortraitRail: stacksCategoryHeader)
                        .environment(\.luminaRetainsArtworkWhileHidden, !libraryPath.isEmpty)
                        .safeAreaInset(edge: .top, spacing: 0) {
                            if stacksCategoryHeader {
                                PortraitNavigationHeader {
                                    libraryCategories(centersContent: false)
                                }
                            }
                        }
                        .ignoresSafeArea(.container, edges: stacksCategoryHeader ? .top : [])
                        .modifier(LibraryStableNavigationBar())
                        .toolbarVisibility(stacksCategoryHeader ? .hidden : .visible, for: .navigationBar)
                        .toolbarBackground(.hidden, for: .navigationBar)
                        .toolbar {
                            if showsCategoryHeader && !usesIPadHeaderNavigation && !stacksCategoryHeader && usesClosedCategoryRow {
                                let header = HStack(spacing: 12) {
                                    HomeHeroChrome().fixedSize()
                                    libraryCategories(centersContent: false)
                                        .frame(width: categoryBarWidth).clipped()
                                }
                                if #available(iOS 26.0, *) {
                                    ToolbarItem(placement: .topBarLeading) { header }
                                        .sharedBackgroundVisibility(.hidden)
                                } else {
                                    ToolbarItem(placement: .topBarLeading) { header }
                                }
                            } else if showsCategoryHeader && !usesIPadHeaderNavigation && !stacksCategoryHeader {
                                ToolbarItem(placement: .principal) {
                                    libraryCategories()
                                        .frame(width: protectedHeaderCategoryWidth)
                                        .clipped()
                                }
                            }
                            if !stacksCategoryHeader && !usesClosedCategoryRow {
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
                        }
                }
            }
            }
            if store.streamingAvailable {
                Tab("Streaming", image: LuminaGlyph.video.assetName(for: destination == .streaming ? .solid : .outline), value: .streaming) {
                    destinationStack(owner: .streaming, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                     usesCustomHeader: stacksCategoryHeader) {
                        NativeStreamingView(store: store, isActive: destination == .streaming)
                    }
                    .accessibilityIdentifier("streamingTab")
                }
            }
            if store.requestsAvailable {
            Tab("Requests", image: LuminaGlyph.requests.assetName(for: destination == .requests ? .solid : .outline), value: .requests) {
                destinationStack(owner: .requests, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                 usesCustomHeader: stacksCategoryHeader) {
                    if let session = store.session {
                        NativeRequestsView(store: store, session: session, isActive: destination == .requests,
                                           usesPortraitNavigationRail: stacksCategoryHeader)
                            .id([session.serverURL.absoluteString, session.userID, session.accessToken])
                    }
                }
            }
            }
            Tab("Account", image: LuminaGlyph.userSettings.assetName(for: destination == .account ? .solid : .outline), value: .account) {
                destinationStack(owner: .account, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader) {
                    NativeAccountSettingsView(store: store, onOpenRequests: usesIPadHeaderNavigation && store.requestsAvailable ? {
                        destination = .requests
                    } : nil, onConnectTV: onConnectTV)
                }
            }
        }
        .environment(\.returnToLuminaHome, {
            homeCategory = .all
            resetHomeNavigation() // Pop Home details, clear its refinement, and return to the top.
            destination = .home
        })
        .onChange(of: store.availableLibraryCategories, initial: true) { _, available in
            guard !isLibraryCategoryAvailable(category) else { return }
            libraryPath = NavigationPath()
            if let first = LibraryCategory.allCases.first(where: isLibraryCategoryAvailable) {
                category = first
            } else if destination == .library {
                destination = .home
            }
        }
        .onChange(of: destination) { previous, next in
            if previous == .account && next != .account { Task { await store.refreshNavigationCategories() } }
        }
        .onChange(of: store.streamingAvailable) { _, available in
            if !available && destination == .streaming { destination = .home }
            if available && category == .streaming {
                category = LibraryCategory.allCases.first(where: isLibraryCategoryAvailable) ?? .movies
                libraryPath = NavigationPath()
            }
        }
        .onChange(of: store.requestsAvailable) { _, available in
            if !available && destination == .requests { destination = .home }
        }
        .tint(LuminaTheme.ink)
    }

    /// Principal toolbar content is centered independently of its side controls.
    /// Reserve the larger side on both edges so scrolling labels stay clear.
    private func headerCategoryWidth(for width: CGFloat) -> CGFloat {
        let masthead = 44.0
        let trailingControls = 3 * 44.0
        let sideInset = max(masthead, trailingControls) + 40 + LuminaTheme.contentGutter
        return min(560, max(100, width - 2 * sideInset))
    }

    @ViewBuilder private func libraryContent(usesPortraitRail: Bool) -> some View {
        if isLibraryCategoryAvailable(category) {
            switch category {
            case .movies, .tv, .anime:
                VideoLibraryView(store: store, kind: .constant(category.videoKind),
                                 activeKind: destination == .library ? category.videoKind : nil,
                                 usesPortraitNavigationRail: usesPortraitRail)
                    .id(category)
            case .music:
                AudioLibraryView(store: store, usesPortraitNavigationRail: usesPortraitRail)
            case .streaming:
                NativeStreamingView(store: store, isActive: destination == .library, savedOnly: true)
            case .books:
                if let session = store.session {
                    NativeBooksView(store: store, session: session, isActive: destination == .library,
                                    usesPortraitNavigationRail: usesPortraitRail)
                        .id([session.serverURL.absoluteString, session.userID, session.accessToken])
                }
            }
        } else {
            ContentUnavailableView("No libraries available", systemImage: "books.vertical")
        }
    }

    private func isLibraryCategoryAvailable(_ category: LibraryCategory) -> Bool {
        if category == .streaming {
            return !store.streamingAvailable && store.availableLibraryCategories.contains("youtube")
        }
        return store.availableLibraryCategories.contains(category.rawValue)
    }

    private func libraryCategories(centersContent: Bool = true) -> some View {
        LuminaCategoryBar(selection: category.rawValue, centersContent: centersContent) {
            ForEach(LibraryCategory.allCases.filter(isLibraryCategoryAvailable), id: \.self) { value in
                Button {
                    category = value
                } label: {
                    Text(value.title)
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: category == value, alignsWithMasthead: true))
                .accessibilityValue(category == value ? "Selected" : "")
                .accessibilityAddTraits(category == value ? .isSelected : [])
                .accessibilityIdentifier("libraryCategory-\(value.rawValue)")
                .id(value.rawValue)
            }
        }
        .accessibilityIdentifier("libraryCategories")
    }

    private var iPadHomeHeader: some View {
        HStack(spacing: 12) {
            HomeHeroChrome().fixedSize()
            Spacer(minLength: 0)
            Menu {
                Button("Home") { homeCategory = .all }
                    .accessibilityIdentifier("homeMenu-home")
                Button("Watchlist") { homeCategory = .watchlist }
                    .accessibilityIdentifier("homeMenu-watchlist")
                if LibraryCategory.allCases.contains(where: isLibraryCategoryAvailable) {
                Button("Library") {
                    var transaction = Transaction(animation: nil)
                    transaction.disablesAnimations = true
                    withTransaction(transaction) {
                        libraryPath = NavigationPath()
                        destination = .library
                    }
                }
                    .accessibilityIdentifier("homeMenu-library")
                }
                Button("Account") { destination = .account }
                    .accessibilityIdentifier("homeMenu-account")
            } label: {
                Label("Browse", glyph: .moreHorizontal).labelStyle(.iconOnly)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityIdentifier("homeBrowseMenu")
            HStack(spacing: 12) {
                if !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                    NavigationLink {
                        NativeOfflineLibraryView(store: store)
                            .environment(\.colorScheme, colorScheme)
                            .toolbarVisibility(.visible, for: .navigationBar)
                    } label: { Label("Downloads", glyph: .download).labelStyle(.iconOnly).frame(width: 44, height: 44).contentShape(.rect) }
                        .accessibilityIdentifier("homeDownloadsButton")
                }
                NavigationLink {
                    HomeSearchView(store: store)
                        .environment(\.colorScheme, colorScheme)
                        .toolbarVisibility(.visible, for: .navigationBar)
                        .toolbar { NativeNowPlayingToolbar(store: store) }
                } label: { Label("Search", glyph: .search).labelStyle(.iconOnly).frame(width: 44, height: 44).contentShape(.rect) }
                    .accessibilityIdentifier("homeSearchButton")
                NativeNowPlayingControls(store: store)
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
        }
        .frame(height: 44)
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
    }

    private var unifiedCategorySelection: String? {
        switch destination {
        case .home: homeCategory == .watchlist ? HomeBrowsingDestination.watchlist.rawValue : HomeBrowsingDestination.all.rawValue
        case .library: category.rawValue
        case .streaming: "streaming"
        case .account: "account"
        case .requests: "account"
        }
    }

    private var unifiedCategories: some View {
        LuminaCategoryBar(selection: unifiedCategorySelection) {
            unifiedCategoryButton("Home", id: HomeBrowsingDestination.all.rawValue,
                                  selected: destination == .home && homeCategory == .all) {
                homeCategory = .all
                resetHomeNavigation()
                destination = .home
            }
            ForEach(LibraryCategory.allCases.filter(isLibraryCategoryAvailable), id: \.self) { value in
                unifiedCategoryButton(value.title, id: value.rawValue,
                                      selected: destination == .library && category == value) {
                    openLibrary(value, fromHome: destination == .home)
                }
            }
            if store.streamingAvailable {
                unifiedCategoryButton("Streaming", id: "streaming", selected: destination == .streaming) {
                    destination = .streaming
                }
            }
            unifiedCategoryButton("Watchlist", id: HomeBrowsingDestination.watchlist.rawValue,
                                  selected: destination == .home && homeCategory == .watchlist) {
                homeCategory = .watchlist
                resetHomeNavigation()
                destination = .home
            }
            unifiedCategoryButton("Account", id: "account",
                                  selected: destination == .account || destination == .requests) {
                destination = .account
            }
        }
        .accessibilityIdentifier("iPadUnifiedCategories")
    }

    private func unifiedCategoryButton(_ title: String, id: String, selected: Bool,
                                       action: @escaping () -> Void) -> some View {
        Button(action: action) { Text(title) }
            .buttonStyle(LuminaUnderlineTabStyle(selected: selected, alignsWithMasthead: true))
            .accessibilityValue(selected ? "Selected" : "")
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier("iPadCategory-\(id)")
            .id(id)
    }

    @ViewBuilder
    private func destinationStack<Content: View>(owner: LibraryDestination, categoryBarWidth: CGFloat, showsCategoryHeader: Bool,
                                                  usesCustomHeader: Bool = false,
                                                  path: Binding<NavigationPath>? = nil,
                                                  @ViewBuilder content: () -> Content) -> some View {
        if let path {
            NavigationStack(path: path) {
                destinationStackContent(owner: owner, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                        usesCustomHeader: usesCustomHeader, content: content)
            }
        } else {
            NavigationStack {
                destinationStackContent(owner: owner, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                        usesCustomHeader: usesCustomHeader, content: content)
            }
        }
    }

    @ViewBuilder
    private func destinationStackContent<Content: View>(owner: LibraryDestination, categoryBarWidth: CGFloat, showsCategoryHeader: Bool,
                                                         usesCustomHeader: Bool,
                                                         @ViewBuilder content: () -> Content) -> some View {
            content()
                .toolbar {
                    if usesIPadHeaderNavigation, owner == destination, owner != .home {
                        ToolbarItem(placement: .principal) {
                            unifiedCategories.frame(width: categoryBarWidth)
                        }
                    }
                    if owner == destination, (owner == .home && !usesCustomHeader && !usesIPadHeaderNavigation || owner == .library && !showsCategoryHeader) {
                        ToolbarItem(placement: .topBarTrailing) {
                            Menu {
                                if owner == .library {
                                    ForEach(LibraryCategory.allCases.filter(isLibraryCategoryAvailable), id: \.self) { value in
                                        Button(value.title) {
                                            openLibrary(value, fromHome: false)
                                        }
                                        .accessibilityIdentifier("compactCategory-\(value.rawValue)")
                                    }
                                } else {
                                    Button("Home") { homeCategory = .all }
                                }
                                Button("Watchlist") {
                                    homeCategory = .watchlist
                                    resetHomeNavigation()
                                    destination = .home
                                }
                                .accessibilityIdentifier("compactCategory-watchlist")
                                if destination == .home, !showsCategoryHeader, !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                                    NavigationLink {
                                        NativeOfflineLibraryView(store: store)
                                    } label: { Label("Downloads", glyph: .download) }
                                    .accessibilityIdentifier("homeDownloadsButton")
                                }
                            } label: {
                                Label(owner == .library ? "Libraries" : "Browse",
                                      glyph: owner == .library ? .library : .moreHorizontal)
                            }
                            .accessibilityIdentifier("compactBrowseMenu")
                        }
                    }
                    if owner == .home, destination == .home, !usesCustomHeader, !usesIPadHeaderNavigation {
                        ToolbarItemGroup(placement: .topBarTrailing) {
                            if showsCategoryHeader, !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                                NavigationLink {
                                    NativeOfflineLibraryView(store: store)
                                } label: { Label("Downloads", glyph: .download) }
                                .accessibilityIdentifier("homeDownloadsButton")
                            }
                            NavigationLink {
                                HomeSearchView(store: store)
                                    .toolbar {
                                        NativeNowPlayingToolbar(store: store)
                                    }
                            } label: { Label("Search", glyph: .search) }
                            .accessibilityIdentifier("homeSearchButton")
                        }
                    }
                    if !usesCustomHeader, owner != .home || !usesIPadHeaderNavigation { NativeNowPlayingToolbar(store: store) }
                }
                .navigationDestination(for: MediaItem.self) { item in
                    Group {
                        if item.type == "MusicAlbum" || item.type == "MusicArtist" {
                            MusicCollectionView(item: item, store: store)
                        } else if item.type == "Episode" {
                            EpisodeCollectionDestination(item: item, store: store)
                        } else if item.type == "BoxSet" || item.type == "Playlist" {
                            MovieCollectionView(item: item, store: store)
                        } else if item.isCollection {
                            VideoCollectionView(item: item, store: store)
                        } else {
                            TitleDetailView(item: item, store: store)
                        }
                    }
                    .toolbarVisibility(.visible, for: .navigationBar)
                    .toolbar {
                        NativeNowPlayingToolbar(store: store)
                    }
                    #if LUMINA_UI_TESTING
                    .onAppear {
                        if owner == .home { recordHomeNavigationDiagnostic("destination-appeared") }
                    }
                    .onDisappear {
                        if owner == .home { recordHomeNavigationDiagnostic("destination-disappeared") }
                    }
                    #endif
                }
                .navigationDestination(for: MediaPerson.self) { person in
                    ActorLibraryView(person: person, store: store)
                        .toolbar { NativeNowPlayingToolbar(store: store) }
                }
            .toolbarVisibility(usesIPadHeaderNavigation ? .hidden : .automatic, for: .tabBar)
            .tint(LuminaTheme.ink)
    }

    private var usesIPadHeaderNavigation: Bool {
        UIDevice.current.userInterfaceIdiom == .pad
    }

    private func resetHomeNavigation() {
        homeNavigationID = UUID()
        #if LUMINA_UI_TESTING
        homeNavigationGeneration += 1
        recordHomeNavigationDiagnostic("root")
        #endif
    }

    #if LUMINA_UI_TESTING
    private func recordHomeNavigationDiagnostic(_ route: String) {
        uiTestingHomeNavigationDiagnostics = "route=\(route) generation=\(homeNavigationGeneration)"
    }
    #endif

    private var homeNavigationDiagnosticValue: String? {
        #if LUMINA_UI_TESTING
        uiTestingHomeNavigationDiagnostics
        #else
        nil
        #endif
    }

    private func openLibrary(_ selected: LibraryCategory, fromHome: Bool) {
        guard isLibraryCategoryAvailable(selected) else { return }
        if fromHome {
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) { libraryPath = NavigationPath() }
        }
        category = selected
        destination = .library
    }
}

@MainActor
struct EpisodeCollectionDestination: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var series: MediaItem?
    @State private var couldNotResolveSeries = false

    var body: some View {
        Group {
            if let series {
                VideoCollectionView(item: series, store: store, initialSeasonID: item.seasonID, initialEpisodeID: item.id)
            } else if couldNotResolveSeries {
                TitleDetailView(item: item, store: store)
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
                series = try await store.seriesCollection(id: seriesID)
            } catch {
                couldNotResolveSeries = true
            }
        }
    }
}

struct MobileHomeSessionScope: Hashable {
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
