import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVVideoView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Binding var browse: TVVideoBrowseState
    let focusRequest: Int
    let restoreFocusRequest: Int?
    let onRequestHeaderFocus: () -> Void
    @State private var pendingRestoredItemID: String?
    @State private var submittedSearch: String?
    @State private var hasLoadedCategory = false
    @FocusState private var focusedItemID: String?

    @State private var alphabetLetter: String?
    @State private var alphabetRequestKey: String?
    @State private var fastScrolling = false
    @State private var lastScrollSample: (offset: Int, time: TimeInterval)?
    @State private var alphabetTask: Task<Void, Never>?
    @State private var artworkWindow = 0
    @State private var lastFocusedIndex = 0
    @State private var movingForward = true
    @FocusState private var alphabetFocused: Bool

    private var requestKey: String { "\(browse.kind.rawValue)-\(browse.sort.rawValue)-\(browse.query)-\(browse.filters.collectionID ?? "")-\(browse.filters.genre)-\(browse.filters.year ?? 0)-\(browse.filters.unwatchedOnly)-\(browse.filters.favoritesOnly)" }
    private var watchlistID: String? {
        store.session.flatMap { LuminaWatchlist.libraryFilterID(userID: $0.userID) }
    }
    private var isWatchlist: Bool {
        watchlistID != nil && browse.filters.collectionID == watchlistID
    }
    private var hasFilters: Bool {
        var filters = browse.filters
        filters.collectionID = nil
        filters.collectionName = nil
        return filters != VideoFilters()
    }
    private var canBrowseAlphabet: Bool {
        alphabetRequestKey == requestKey && browse.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && browse.sort == .name && [.movies, .shows, .anime].contains(browse.kind)
            && store.supportsLibraryAlphabet && !store.libraryAlphabet.isEmpty
    }

    var body: some View {
        ScrollViewReader { proxy in
                HStack(alignment: .center, spacing: 0) {
                    ScrollView {
                        if displayedItems.isEmpty && (store.isBusy || !hasLoadedCategory && store.errorMessage == nil) {
                            ProgressView("Loading video…").frame(maxWidth: .infinity, minHeight: 360)
                        } else if displayedItems.isEmpty, isWatchlist, store.errorMessage == nil {
                            if hasFilters || !browse.query.isEmpty {
                                TVMessageView(title: "No matching saved titles", message: "Try clearing search and filters to see more of your Watchlist.", actionTitle: "Clear search and filters", focusActionOnAppear: true) {
                                    browse.query = ""
                                    browse.filters = VideoFilters(collectionID: watchlistID, collectionName: "Watchlist")
                                }
                                .containerRelativeFrame(.vertical)
                                .focusSection()
                            } else {
                                TVMessageView(title: "Nothing saved here yet", message: "Add a title to your Watchlist from its details. It will appear here in its library category.", actionTitle: "Browse all titles", focusActionOnAppear: true) {
                                    selectCollection(nil, name: nil)
                                }
                                .containerRelativeFrame(.vertical)
                                .focusSection()
                            }
                        } else if displayedItems.isEmpty {
                            TVMessageView(title: "No matching videos", message: store.errorMessage ?? "Try another search or choose a different category.", actionTitle: "Refresh") {
                                Task { await reload(forceRefresh: true) }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 42) {
                                TVCollectionGrid(title: nil, items: displayedItems, store: store,
                                                 focusedItemID: $focusedItemID, onOpen: onOpen, onItemDidAppear: itemDidAppear,
                                                 onExitTop: onRequestHeaderFocus)
                            }
                            .padding(.horizontal, 80)
                            .padding(.top, 24)
                            .accessibilityIdentifier("libraryPosterWall")
                            if store.hasMore {
                                Button(store.isCatalogLoading ? "Loading library…" : "Load more") { Task { await store.loadMore() } }
                                    .buttonStyle(LuminaQuietActionStyle())
                                    .disabled(store.isBusy || store.isCatalogLoading)
                                    .accessibilityIdentifier("videoLoadMoreButton")
                                    .padding(32)
                            }
                            if let error = store.errorMessage {
                                Text(error).foregroundStyle(LuminaTheme.accentInk).padding(24)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityIdentifier("libraryScreen")
                    #if LUMINA_UI_TESTING
                    .accessibilityValue(Text(verbatim: "catalog=\(displayedItems.count) loading=\(store.isCatalogLoading)"))
                    #endif
                    .onScrollGeometryChange(for: Int.self) { geometry in
                        Int(geometry.contentOffset.y / 128)
                    } action: { previousOffset, offset in
                        if offset != previousOffset {
                            let forward = offset > previousOffset
                            if movingForward != forward { movingForward = forward }
                        }
                        let now = ProcessInfo.processInfo.systemUptime
                        if let lastScrollSample {
                            let elapsed = now - lastScrollSample.time
                            if elapsed > 0, elapsed < 0.7,
                               Double(abs(offset - lastScrollSample.offset) * 128) / elapsed > 900 {
                                fastScrolling = true
                            }
                        }
                        lastScrollSample = (offset, now)
                    }
                    .onScrollPhaseChange { _, phase in
                        if phase == .idle { fastScrolling = false; lastScrollSample = nil }
                    }
                    if canBrowseAlphabet {
                        TVLibraryAlphabetRail(
                            letters: store.libraryAlphabet.map(\.letter),
                            selection: $alphabetLetter,
                            expanded: fastScrolling || alphabetFocused,
                            isFocused: alphabetFocused,
                            onReturn: {
                                alphabetFocused = false
                                focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
                            },
                            onSelect: { letter in
                                guard canBrowseAlphabet,
                                      let anchor = store.libraryAlphabet.first(where: { $0.letter == letter }) else { return }
                                alphabetTask?.cancel()
                                alphabetTask = Task {
                                    guard let firstID = await store.jumpLibrary(to: anchor), !Task.isCancelled else { return }
                                    await Task.yield()
                                    guard !Task.isCancelled else { return }
                                    proxy.scrollTo(firstID, anchor: .top)
                                    await Task.yield()
                                    guard !Task.isCancelled else { return }
                                    focusedItemID = firstID
                                    alphabetFocused = false
                                }
                            }
                        )
                        .focused($alphabetFocused)
                        .onExitCommand {
                            alphabetFocused = false
                            focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
                        }
                        .padding(.trailing, 32)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .onChange(of: focusedItemID) { _, itemID in
            if let itemID {
                browse.focusedItemID = itemID
                if let index = displayedItems.firstIndex(where: { $0.id == itemID }) {
                    let forward = index >= lastFocusedIndex
                    if movingForward != forward { movingForward = forward }
                    lastFocusedIndex = index
                    let window = index / 6
                    if window != artworkWindow { artworkWindow = window }
                }
            }
        }
        .onChange(of: restoreFocusRequest, initial: true) { _, request in
            guard request != nil else { return }
            pendingRestoredItemID = browse.focusedItemID
            restoreSelection(restorableItemID)
        }
        .onChange(of: restorableItemID) { _, itemID in restoreSelection(itemID) }
        .onChange(of: focusRequest) { _, _ in focusVisibleGridItem() }
        .task(id: "\(displayedItems.first?.id ?? "")-\(artworkWindow)-\(displayedItems.count)-\(movingForward)", priority: .utility) {
            // Advance lookahead in small batches instead of rebuilding the queue for every focused or visible card.
            let items = displayedItems
            guard !items.isEmpty else { return }
            let anchor = min(items.count - 1, artworkWindow * 6 + (movingForward ? 0 : 5))
            let upcoming = movingForward ? Array(items.dropFirst(anchor + 1).prefix(36))
                : Array(items.prefix(anchor).suffix(36).reversed())
            await ArtworkImageCache.shared.prefetch(upcoming.compactMap { store.posterURL(for: $0, maxWidth: 480) }, limit: 36)
        }
        .onDisappear { alphabetTask?.cancel() }
        .task(id: requestKey) {
            let requestedKey = requestKey
            alphabetTask?.cancel()
            artworkWindow = 0
            lastFocusedIndex = 0
            movingForward = true
            alphabetLetter = nil
            alphabetRequestKey = nil
            let query = browse.query
            if let submittedSearch, query != submittedSearch, !query.isEmpty {
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            }
            guard !Task.isCancelled else { return }
            submittedSearch = query
            await reload()
            guard !Task.isCancelled, requestedKey == requestKey else { return }
            alphabetRequestKey = requestedKey
        }
    }

    private func selectCollection(_ id: String?, name: String?) {
        var filters = browse.filters
        filters.collectionID = id
        filters.collectionName = name
        browse.filters = filters
    }

    private var displayedItems: [MediaItem] {
        // The shared store can still contain the previous category while page one loads.
        hasLoadedCategory ? store.items : []
    }

    private func focusVisibleGridItem() {
        focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
    }

    private func itemDidAppear(_ item: MediaItem) {
        let loadMoreAnchor = displayedItems.suffix(6).first?.id
        guard store.hasMore, !store.isCatalogLoading, item.id == loadMoreAnchor else { return }
        Task { await store.loadMore() }
    }

    private var restorableItemID: String? {
        guard let itemID = pendingRestoredItemID else { return nil }
        return displayedItems.contains { $0.id == itemID } ? itemID : nil
    }

    private func restoreSelection(_ itemID: String?) {
        guard let itemID else { return }
        focusedItemID = itemID
        pendingRestoredItemID = nil
    }

    private func reload(forceRefresh: Bool = false) async {
        await store.loadLibrary(search: browse.query, kind: browse.kind, sort: browse.sort, filters: browse.filters,
                                forceRefresh: forceRefresh || isWatchlist, preloadCatalog: true)
        if !Task.isCancelled && store.errorMessage == nil { hasLoadedCategory = true }
    }
}

enum TVVideoBrowseRoute { case collections, filters, sort }

struct TVVideoBrowseSheet: View {
    let store: LuminaStore
    @Binding var browse: TVVideoBrowseState
    @Binding var route: TVVideoBrowseRoute?
    let watchlistID: String?
    let onClose: () -> Void
    @FocusState private var allTitlesFocused: Bool

    private var isWatchlist: Bool {
        watchlistID != nil && browse.filters.collectionID == watchlistID
    }

    private var hasFilters: Bool {
        var filters = browse.filters
        filters.collectionID = nil
        filters.collectionName = nil
        return filters != VideoFilters()
    }

    private var selectedCollectionName: String? {
        isWatchlist ? nil : browse.filters.collectionName
    }

    private var filterSummary: String {
        [browse.filters.genre.isEmpty ? nil : browse.filters.genre, browse.filters.year.map(String.init),
         browse.filters.unwatchedOnly ? "Unwatched" : nil, browse.filters.favoritesOnly ? "Favorites" : nil]
            .compactMap { $0 }
            .joined(separator: " · ")
    }

    var body: some View {
        ZStack {
            switch route {
            case nil:
                browseControls
            case .collections:
                TVCollectionFilterPicker(
                    store: store,
                    watchlistID: watchlistID,
                    includesFranchises: browse.kind == .movies,
                    selection: isWatchlist ? nil : browse.filters.collectionID,
                    onSelect: selectCollection,
                    onClose: { route = nil }
                )
            case .filters:
                TVVideoFiltersView(filters: browse.filters, onApply: { browse.filters = $0 }, onClose: { route = nil })
            case .sort:
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Sort library").font(TVTypography.pageTitle).padding(.bottom, 20)
                        ForEach(VideoSort.allCases, id: \.self) { sort in
                            Button {
                                browse.sort = sort
                                onClose()
                            } label: {
                                browseRow(sort.label, value: browse.sort == sort ? "Selected" : nil)
                            }
                        }
                    }
                    .buttonStyle(LuminaQuietActionStyle())
                    .frame(maxWidth: 880)
                    .padding(.horizontal, 80)
                    .padding(.vertical, 36)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoBrowseSheet")
    }

    private var browseControls: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Filters").font(TVTypography.pageTitle)
                    Spacer()
                    Button("Done", action: onClose)
                        .accessibilityIdentifier("videoBrowseDoneButton")
                }
                .padding(.bottom, 20)
                Button { selectCollection(nil, nil) } label: {
                    browseRow("All titles", value: browse.filters.collectionID == nil ? "Selected" : nil)
                }
                    .focused($allTitlesFocused)
                    .accessibilityAddTraits(browse.filters.collectionID == nil ? .isSelected : [])
                    .accessibilityValue(browse.filters.collectionID == nil ? "Selected" : "")
                    .accessibilityIdentifier("videoAllTitlesButton")
                if let watchlistID {
                    Button { selectCollection(watchlistID, "Watchlist") } label: {
                        browseRow("Watchlist", value: isWatchlist ? "Selected" : nil)
                    }
                        .accessibilityAddTraits(isWatchlist ? .isSelected : [])
                        .accessibilityValue(isWatchlist ? "Selected" : "")
                        .accessibilityIdentifier("videoLibraryWatchlistButton")
                }
                Button { route = .collections } label: {
                    browseRow("Collections", value: selectedCollectionName ?? "Any collection")
                }
                .accessibilityValue(selectedCollectionName ?? "Any collection")
                .accessibilityIdentifier("videoCollectionFilterButton")

                if browse.query.isEmpty {
                    Button { route = .sort } label: {
                        browseRow("Sort", value: browse.sort == .name ? "Title A–Z" : browse.sort.label)
                    }
                    .accessibilityIdentifier("videoSortPicker")
                    .accessibilityLabel("Sort library")
                    .accessibilityValue(browse.sort.label)
                } else {
                    browseRow("Sort", value: "By relevance")
                        .foregroundStyle(LuminaTheme.muted)
                }

                Button { route = .filters } label: {
                    browseRow("More filters", value: hasFilters ? filterSummary : "No filters")
                }
                .accessibilityValue(hasFilters ? filterSummary : "No filters")
                .accessibilityIdentifier("videoFiltersButton")

                if hasFilters {
                    Button("Clear filters") {
                        browse.filters = VideoFilters(
                            collectionID: browse.filters.collectionID,
                            collectionName: browse.filters.collectionName
                        )
                    }
                    .accessibilityIdentifier("videoClearFiltersButton")
                }

                TextField("Search this library", text: $browse.query)
                    .font(TVTypography.body)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("searchField")
            }
            .buttonStyle(LuminaQuietActionStyle())
            .frame(maxWidth: 880)
            .padding(.horizontal, 80)
            .padding(.vertical, 36)
            .frame(maxWidth: .infinity)
        }
        .foregroundStyle(LuminaTheme.ink)
        .defaultFocus($allTitlesFocused, true)
        .onAppear { allTitlesFocused = true }
    }

    private func selectCollection(_ id: String?, _ name: String?) {
        var filters = browse.filters
        filters.collectionID = id
        filters.collectionName = name
        browse.filters = filters
    }

    private func browseRow(_ title: String, value: String? = nil) -> some View {
        HStack {
            Text(title)
            Spacer()
            if let value {
                Text(value).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

enum TVStreamingTab: Hashable {
    case saved, recommendations, live
}
