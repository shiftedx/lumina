import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


enum TVLibraryCategory: String, CaseIterable {
    case movies, tv, anime, music, streaming

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "Shows"
        case .anime: "Anime"
        case .music: "Music"
        case .streaming: "Saved Streaming"
        }
    }

    static let preferenceKey = "native.tv.defaultLibrary"

    @MainActor static func available(in store: LuminaStore) -> [Self] {
        allCases.filter {
            $0 == .streaming
                ? store.streamingAvailable || store.availableLibraryCategories.contains("youtube")
                : store.availableLibraryCategories.contains($0.rawValue)
        }
    }

    @MainActor func label(in store: LuminaStore) -> String {
        self == .streaming && store.streamingAvailable ? "Streaming" : title
    }

    var videoKind: VideoBrowseKind {
        switch self {
        case .tv: .shows
        case .anime: .anime
        default: .movies
        }
    }
}

enum TVLibraryHeaderFocus: Hashable {
    case destination(TVDestination)
    case category(TVLibraryCategory)
    case browse
    case search
    case account
}

struct TVLibraryView: View {
    let store: LuminaStore
    let isAtNavigationRoot: Bool
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let onSignOut: () -> Void
    @State private var destination: TVDestination = .home
    @State private var history: [(destination: TVDestination, category: TVLibraryCategory)] = []
    @State private var homeSearchPresented = false
    @State private var requestsUnavailable = false
    @State private var homeLayout = LuminaHomeLayout()
    @State private var homeFeed = LuminaHomeFeed()
    @State private var requestsCatalog = LuminaRequests()
    @State private var homeSessionScope: TVHomeSessionScope?
    @State private var homeHeroID: String?
    @AppStorage(TVLibraryCategory.preferenceKey) private var defaultLibrary: TVLibraryCategory = .movies
    @State private var libraryCategory: TVLibraryCategory = .movies
    @State private var videoBrowse: [TVLibraryCategory: TVVideoBrowseState] = [:]
    @State private var audioBrowse = TVAudioBrowseState()
    @State private var deviceSettingsPresented = false
    @State private var browsePresented = false
    @State private var browseRoute: TVVideoBrowseRoute?
    @State private var restoreBrowseFocus = false
    @State private var contentFocusRequest = 0
    @State private var libraryFocusRequest = 0
    @State private var lastLibraryHeaderFocus: TVLibraryHeaderFocus?
    @FocusState private var focusedDestination: TVDestination?
    @FocusState private var focusedLibraryHeader: TVLibraryHeaderFocus?
    @FocusState private var searchFocused: Bool
    @FocusState private var accountFocused: Bool

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                Group {
                    switch destination {
                    case .home:
                        if homeSessionScope == TVHomeSessionScope(store.session) {
                            TVHomeView(
                                store: store,
                                layout: homeLayout,
                                feed: homeFeed,
                                heroSelection: $homeHeroID,
                                onOpen: onOpen,
                                onPlayback: onPlayback,
                                focusRequest: contentFocusRequest,
                                onRequestHeaderFocus: { focusActiveHeader() },
                                header: { navigationHeader(topInset: max(geometry.safeAreaInsets.top, geometry.frame(in: .global).minY)) }
                            )
                        } else {
                            ProgressView("Loading Home…")
                        }
                    case .library:
                        libraryContent
                    case .requests:
                        if store.requestsAvailable, homeSessionScope == TVHomeSessionScope(store.session) {
                            TVRequestsView(
                                store: store,
                                catalog: requestsCatalog,
                                focusRequest: contentFocusRequest,
                                onOpen: onOpen,
                                onPlayback: onPlayback,
                                onOpenVault: { id in try await store.libraryItem(id: id) }
                            )
                        }
                    }
                }
                .padding(.top, contentTopPadding)
                .disabled(browsePresented)
                .accessibilityHidden(browsePresented)
                if destination != .home && !browsePresented {
                    navigationHeader()
                        .defaultFocus($focusedLibraryHeader, restoreBrowseFocus ? .browse : nil)
                        .onAppear {
                            if restoreBrowseFocus { focusedLibraryHeader = .browse }
                        }
                }
                if browsePresented {
                    TVVideoBrowseSheet(
                        store: store,
                        browse: browseState(for: libraryCategory),
                        route: $browseRoute,
                        watchlistID: store.session.flatMap { LuminaWatchlist.libraryFilterID(userID: $0.userID) },
                        onClose: closeLibraryBrowse
                    )
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .sheet(isPresented: $homeSearchPresented, onDismiss: { searchFocused = true }) {
            TVHomeSearchView(store: store) { item in
                homeSearchPresented = false
                onOpen(item)
            }
        }
        .fullScreenCover(isPresented: $deviceSettingsPresented) {
            DeviceSettingsView(
                store: store,
                onSignOut: {
                    deviceSettingsPresented = false
                    onSignOut()
                    Task { await store.signOut() }
                },
                onClose: {
                    deviceSettingsPresented = false
                    Task { await store.refreshNavigationCategories() }
                }
            )
            .background(LuminaTheme.theater.ignoresSafeArea())
            .preferredColorScheme(.dark)
        }
        .task(id: TVHomeSessionScope(store.session)) {
            homeLayout.configure(session: store.session)
            homeFeed.configure(session: store.session)
            requestsCatalog.configure(session: store.requestsAvailable ? store.session : nil)
            homeSessionScope = TVHomeSessionScope(store.session)
        }
        .task(id: destination) {
            if destination == .library, store.navigationLoadState != .loaded {
                await store.refreshNavigationCategories()
            }
        }
        .onChange(of: store.navigationLoadState) { _, state in
            if destination == .library, state == .loaded,
               !TVLibraryCategory.available(in: store).contains(libraryCategory) {
                openLibrary(libraryCategory)
            }
        }
        .onChange(of: store.requestsAvailable) { _, available in
            requestsCatalog.configure(session: available ? store.session : nil)
            if !available {
                history.removeAll { $0.destination == .requests }
                if destination == .requests {
                    returnToPreviousDestination()
                    requestsUnavailable = true
                }
            }
        }
        .onChange(of: focusedLibraryHeader) { _, focus in
            if let focus {
                lastLibraryHeaderFocus = focus
                if focus == .browse { restoreBrowseFocus = false }
            }
        }
        .onChange(of: focusedDestination) { _, destinationFocus in
            if destination == .library, let destinationFocus {
                lastLibraryHeaderFocus = .destination(destinationFocus)
            }
        }
        .onChange(of: searchFocused) { _, isFocused in
            if destination == .library, isFocused {
                lastLibraryHeaderFocus = .search
            }
        }
        .onChange(of: accountFocused) { _, isFocused in
            if destination == .library, isFocused {
                lastLibraryHeaderFocus = .account
            }
        }
        .alert("Requests unavailable", isPresented: $requestsUnavailable) {
            Button("OK") { focusActiveHeader() }
        } message: {
            Text(store.navigationLoadState == .failed
                 ? "Lumina couldn't check Requests access. Open Library to retry."
                 : "Requests is no longer available for this account.")
        }
        .onExitCommand(perform: backAction)
    }

    private func navigationHeader(topInset: CGFloat = 0) -> some View {
        header
            .focusedValue(\.tvHeroRegion, true)
            .padding(.horizontal, 80)
            .padding(.top, 12 + topInset)
            .padding(.bottom, 12)
            .background {
                LinearGradient(stops: [
                    .init(color: .black.opacity(0.75), location: 0),
                    .init(color: .black.opacity(0.5), location: 0.65),
                    .init(color: .clear, location: 1),
                ], startPoint: .top, endPoint: .bottom)
                    .padding(.bottom, -32)
                    .ignoresSafeArea(.container, edges: [.top, .horizontal])
                    .allowsHitTesting(false)
            }
            .focusSection()
            .onMoveCommand { direction in
                guard direction == .down else { return }
                contentFocusRequest += 1
            }
            .ignoresSafeArea(.container, edges: .horizontal)
    }

    private var backAction: (() -> Void)? {
        guard isAtNavigationRoot else { return nil }
        if browsePresented {
            return {
                if browseRoute == nil { closeLibraryBrowse() }
                else { browseRoute = nil }
            }
        }
        if destination != .home { return { returnToPreviousDestination() } }
        if focusedDestination == nil, !searchFocused, !accountFocused { return { focusActiveHeader() } }
        return nil
    }

    private func closeLibraryBrowse() {
        guard browsePresented else { return }
        restoreBrowseFocus = true
        browseRoute = nil
        browsePresented = false
    }

    private var contentTopPadding: CGFloat {
        switch destination {
        case .home: 0
        case .library: 96
        case .requests: 112
        }
    }

    private func selectDestination(_ next: TVDestination) {
        if next == .home {
            history.removeAll()
        } else if next != destination {
            history.append((destination, libraryCategory))
        }
        destination = next
        if next == .library {
            openLibrary(defaultLibrary)
            // Selecting the global Library icon should retain focus on that icon;
            // moving into the poster wall will subsequently restore this control.
            lastLibraryHeaderFocus = .destination(.library)
        }
        focusActiveHeader()
    }

    private func returnToPreviousDestination() {
        let previous = history.popLast()
        destination = previous?.destination ?? .home
        if destination == .library, let previous { openLibrary(previous.category) }
        focusActiveHeader()
    }

    private func focusActiveHeader() {
        if destination == .library {
            restoreLastLibraryHeaderFocus()
        } else {
            focusedLibraryHeader = nil
            focusedDestination = destination
        }
    }

    private func restoreLastLibraryHeaderFocus() {
        let available = TVLibraryCategory.available(in: store)
        let categories = available.isEmpty ? [libraryCategory] : available
        let preferredFocus: TVLibraryHeaderFocus

        switch lastLibraryHeaderFocus {
        case .some(.destination(let target)):
            focusedLibraryHeader = nil
            searchFocused = false
            accountFocused = false
            focusedDestination = target
            return
        case .some(.search):
            focusedLibraryHeader = nil
            focusedDestination = nil
            accountFocused = false
            searchFocused = true
            return
        case .some(.account):
            focusedLibraryHeader = nil
            focusedDestination = nil
            searchFocused = false
            accountFocused = true
            return
        case .some(.browse) where isVideoLibraryCategory(libraryCategory):
            preferredFocus = .browse
        case .some(.category(let category)) where categories.contains(category):
            preferredFocus = .category(category)
        case .some(.browse), .some(.category), .none:
            preferredFocus = .category(libraryCategory)
        }

        focusedDestination = nil
        searchFocused = false
        accountFocused = false
        focusedLibraryHeader = preferredFocus
    }

    private func browseState(for category: TVLibraryCategory) -> Binding<TVVideoBrowseState> {
        Binding(
            get: { videoBrowse[category] ?? TVVideoBrowseState(kind: category.videoKind) },
            set: { videoBrowse[category] = $0 }
        )
    }

    private func openLibrary(_ category: TVLibraryCategory) {
        let available = TVLibraryCategory.available(in: store)
        libraryCategory = available.isEmpty || available.contains(category) ? category : available[0]
        libraryFocusRequest += 1
    }

    private func isVideoLibraryCategory(_ category: TVLibraryCategory) -> Bool {
        switch category {
        case .movies, .tv, .anime: true
        case .music, .streaming: false
        }
    }

    private var libraryBrowseSummary: String? {
        let browse = videoBrowse[libraryCategory] ?? TVVideoBrowseState(kind: libraryCategory.videoKind)
        if !browse.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Search"
        }
        if let collectionName = browse.filters.collectionName {
            return collectionName
        }
        if browse.filters.collectionID != nil {
            return "Watchlist"
        }
        var filters = browse.filters
        filters.collectionID = nil
        filters.collectionName = nil
        if filters != VideoFilters() { return "Active" }
        return browse.sort == .name ? nil : browse.sort.label
    }

    private var libraryContent: some View {
        Group {
            switch libraryCategory {
            case .streaming:
                TVStreamingView(store: store, onOpen: onOpen, savedOnly: !store.streamingAvailable)
            case .music:
                TVAudioView(store: store, onOpen: onOpen, browse: $audioBrowse,
                            restoreFocusRequest: libraryFocusRequest, isAtNavigationRoot: isAtNavigationRoot)
            case .movies, .tv, .anime:
                TVVideoView(store: store, onOpen: onOpen, browse: browseState(for: libraryCategory),
                            focusRequest: contentFocusRequest, restoreFocusRequest: libraryFocusRequest,
                            onRequestHeaderFocus: restoreLastLibraryHeaderFocus)
                    .id(libraryCategory)
            }
        }
    }

    private var header: some View {
        HStack(spacing: 0) {
            Button { selectDestination(.home) } label: {
                LuminaBrandMark()
                    .frame(width: 42, height: 26)
                    .overlay {
                        if focusedDestination == .home {
                            RoundedRectangle(cornerRadius: 7)
                                .strokeBorder(LuminaTheme.ink, lineWidth: 2)
                                .padding(-7)
                                .allowsHitTesting(false)
                        }
                    }
                    .frame(width: 64, height: 64, alignment: .leading)
                    .overlay(alignment: .bottomLeading) {
                        if destination == .home {
                            Rectangle().fill(LuminaTheme.brand)
                                .frame(width: 42, height: 2)
                                .allowsHitTesting(false)
                        }
                    }
                    .contentShape(.rect)
            }
            .buttonStyle(TVNavigationButtonStyle(showsReducedMotionOutline: false))
            .focusEffectDisabled()
            .focused($focusedDestination, equals: .home)
            .accessibilityLabel("Home")
            .accessibilityAddTraits(destination == .home ? .isSelected : [])
            .accessibilityValue(destination == .home ? "Selected" : "")
            .accessibilityIdentifier("homeNavigationButton")

            if destination == .library {
                libraryHeaderControls
                    .padding(.leading, 20)
                    .layoutPriority(1)
            }

            Spacer()
            HStack(spacing: 10) {
                destinationIcon(.library, glyph: .library, identifier: "libraryNavigationButton")
                if store.requestsAvailable {
                    destinationIcon(.requests, glyph: .requests, identifier: "requestsButton")
                }
                Button { homeSearchPresented = true } label: {
                    Label("Search", glyph: .search, size: 28).labelStyle(.iconOnly)
                        .frame(width: 64, height: 64)
                }
                .buttonStyle(TVNavigationButtonStyle())
                .focusEffectDisabled()
                .focused($searchFocused)
                .accessibilityIdentifier("homeSearchButton")

                Button { deviceSettingsPresented = true } label: {
                    LuminaIcon(glyph: .userSettings,
                               variant: deviceSettingsPresented ? .solid : .outline, size: 28)
                        .foregroundStyle(deviceSettingsPresented ? LuminaTheme.accentInk : accountFocused ? LuminaTheme.ink : LuminaTheme.muted)
                        .frame(width: 64, height: 64)
                }
                .buttonStyle(TVNavigationButtonStyle())
                .focusEffectDisabled()
                .focused($accountFocused)
                .accessibilityLabel("Account")
                .accessibilityValue(deviceSettingsPresented ? "Selected" : "")
                .accessibilityAddTraits(deviceSettingsPresented ? .isSelected : [])
                .accessibilityIdentifier("accountButton")
            }
        }
        .frame(height: 64)
    }

    private var libraryHeaderControls: some View {
        let available = TVLibraryCategory.available(in: store)
        let categories = available.isEmpty ? [libraryCategory] : available

        return HStack(spacing: 16) {
            ForEach(categories, id: \.self) { category in
                Button {
                    openLibrary(category)
                } label: {
                    Text(category.label(in: store))
                        .lineLimit(1)
                        .minimumScaleFactor(0.82)
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: category == libraryCategory))
                .focused($focusedLibraryHeader, equals: .category(category))
                .accessibilityAddTraits(category == libraryCategory ? .isSelected : [])
                .accessibilityIdentifier("homeBrowse-\(category == .streaming && showsSavedStreaming ? "savedStreaming" : category.rawValue)")
            }

            if isVideoLibraryCategory(libraryCategory) {
                Button {
                    focusedLibraryHeader = nil
                    browsePresented = true
                } label: {
                    HStack(spacing: 4) {
                        Text("Filters")
                        if let libraryBrowseSummary {
                            Text("· \(libraryBrowseSummary)")
                                .font(TVTypography.metadata)
                                .foregroundStyle(LuminaTheme.muted)
                                .lineLimit(1)
                        }
                    }
                }
                .buttonStyle(LuminaUnderlineTabStyle())
                .focused($focusedLibraryHeader, equals: .browse)
                .accessibilityLabel("Filters")
                .accessibilityValue(libraryBrowseSummary ?? "All titles")
                .accessibilityIdentifier("videoBrowseButton")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("libraryCategoriesScreen")
    }

    private var showsSavedStreaming: Bool {
        !store.streamingAvailable && store.availableLibraryCategories.contains("youtube")
    }

    private func destinationIcon(_ target: TVDestination, glyph: LuminaGlyph, identifier: String) -> some View {
        let isSelected = destination == target
        return Button { selectDestination(target) } label: {
            Label(target.title, glyph: glyph, variant: isSelected ? .solid : .outline, size: 28)
                .labelStyle(.iconOnly)
                .frame(width: 64, height: 64)
                .contentShape(.rect)
        }
        .buttonStyle(LuminaUnderlineTabStyle(selected: isSelected))
        .focusEffectDisabled()
        .focused($focusedDestination, equals: target)
        .accessibilityValue(isSelected ? "Selected" : "")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

/// Keep remote focus legible without placing a system capsule over the artwork.
private struct TVNavigationButtonStyle: ButtonStyle {
    var showsReducedMotionOutline = true
    @Environment(\.isFocused) private var isFocused
    @Environment(\.luminaReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.7 : 1)
            .scaleEffect(isFocused && !reduceMotion ? 1.04 : 1)
            .overlay {
                if showsReducedMotionOutline && reduceMotion && isFocused {
                    RoundedRectangle(cornerRadius: 8)
                        .strokeBorder(LuminaTheme.ink, lineWidth: 2)
                }
            }
    }
}
