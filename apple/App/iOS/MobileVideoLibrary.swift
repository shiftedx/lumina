import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct VideoLibraryView: View {
    @Bindable var store: LuminaStore

    @State private var searchText = ""
    @State private var searchVisible = false
    @Binding var kind: VideoBrowseKind
    let activeKind: VideoBrowseKind?
    var usesPortraitNavigationRail = false
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersVisible = false
    @State private var scrollPhase: ScrollPhase = .idle
    @State private var pullIntent: PullIntent = .none
    @State private var alphabetRailVisible = false
    @State private var selectedAlphabetAnchor: LibraryAlphabetAnchor?
    @State private var queryTask: Task<Void, Never>?
    @State private var alphabetJumpTask: Task<Void, Never>?
    @State private var alphabetRailDismissTask: Task<Void, Never>?
    @State private var alphabetJumpID = UUID()
    @State private var alphabetAnchorsScopeKey: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private enum PullIntent { case none, search, refresh }

    var body: some View {
        GeometryReader { geometry in
        ScrollViewReader { scrollProxy in
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
                            .accessibilityIdentifier("videoSearchCancelButton")
                        }
                        .padding(.trailing, geometry.safeAreaInsets.trailing)
                        .padding(.trailing, usesPortraitNavigationRail ? 76 : 0)
                        .frame(height: 56)
                    }

                    if filters.collectionID != nil {
                        HStack {
                            Label(filters.collectionName ?? "Collection", glyph: .library)
                                .font(.subheadline.weight(.semibold))
                            Spacer()
                            Button("Clear collection", glyph: .close) {
                                filters.collectionID = nil
                                filters.collectionName = nil
                            }
                            .labelStyle(.iconOnly)
                            .frame(minWidth: 44, minHeight: 44)
                            .accessibilityIdentifier("videoClearCollectionFilterButton")
                        }
                        .accessibilityElement(children: .contain)
                        .accessibilityIdentifier("videoActiveCollectionFilter")
                    }

                    if store.isBusy && store.items.isEmpty && store.householdCollections.isEmpty {
                        LibraryLoadingView()
                    } else if store.items.isEmpty && (kind != .collections || store.householdCollections.isEmpty) {
                        if let message = store.errorMessage, !message.isEmpty {
                            InlineErrorView(message: message) {
                                refresh()
                            }
                        } else if kind == .collections, let householdError = store.householdCollectionsError {
                            InlineErrorView(message: "Your lists could not load: \(householdError)") {
                                refresh()
                            }
                        } else if kind == .collections {
                            ContentUnavailableView(
                                "No collections yet",
                                systemImage: "rectangle.stack",
                                description: Text("Movie franchises and your saved lists will appear here.")
                            )
                        } else if !searchText.isEmpty {
                            ContentUnavailableView.search(text: searchText)
                        } else if kind != .all || filters != VideoFilters() {
                            ContentUnavailableView {
                                Label("No matching video", glyph: .movies)
                            } description: {
                                Text("Try another video type or clear your filters.")
                            }
                        } else {
                            EmptyLibraryView {
                                refresh()
                            }
                        }
                    } else {
                        if let message = store.errorMessage, !message.isEmpty {
                            InlineErrorView(message: message) {
                                refresh()
                            }
                        }

                        if kind == .collections {
                            if !store.items.isEmpty {
                                Text("Movie collections")
                                    .font(.title2.bold())
                                    .accessibilityAddTraits(.isHeader)
                                    .accessibilityIdentifier("movieCollectionsHeading")
                                VideoPosterWall(items: store.items, store: store)
                            }
                            if !store.householdCollections.isEmpty {
                                Text("Your lists")
                                    .font(.title2.bold())
                                    .accessibilityAddTraits(.isHeader)
                                    .accessibilityIdentifier("householdCollectionsHeading")
                                    .padding(.top, store.items.isEmpty ? 0 : 16)
                                VideoPosterWall(items: store.householdCollections, store: store)
                            }
                            if let householdError = store.householdCollectionsError {
                                InlineErrorView(message: "Your lists could not load: \(householdError)") { refresh() }
                            }
                        } else if !store.items.isEmpty {
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
                                    Text(store.isCatalogLoading ? "Loading library…" : "Load more")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(LuminaQuietActionStyle())
                            .controlSize(.large)
                            .disabled(store.isBusy || store.isCatalogLoading)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 28)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .overlay(alignment: .trailing) {
                if isAlphabetRailAvailable && alphabetRailVisible && !usesAlphabetMenu {
                    VideoAlphabetRail(
                        anchors: store.libraryAlphabet,
                        selectedAnchor: $selectedAlphabetAnchor,
                        onInteraction: cancelAlphabetRailDismiss,
                        onCommit: { jump(to: $0, using: scrollProxy) }
                    )
                    .padding(.trailing, 6)
                    .transition(.opacity)
                    .accessibilityHidden(true)
                }
            }
            .background(LuminaTheme.paper)
            .ignoresSafeArea(.container, edges: .horizontal)
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
            .onScrollPhaseChange { previous, phase, context in
                let intent = pullIntent
                scrollPhase = phase
                if previous == .interacting, isAlphabetRailAvailable, abs(context.velocity?.dy ?? 0) >= 700 {
                    revealAlphabetRail()
                }
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
            .onChange(of: searchText) { _, text in
                resetAlphabetNavigation()
                if text.isEmpty { search() }
            }
            .onChange(of: kind) {
                if !supportsTechnicalFilterKind {
                    var compatible = filters
                    compatible.resolution = nil; compatible.hdr = nil
                    compatible.audio = nil; compatible.imaxOnly = false
                    if compatible != filters { filters = compatible; return }
                }
                resetAlphabetNavigation(); search()
            }
            .onChange(of: sort) { resetAlphabetNavigation(); search() }
            .onChange(of: filters) { resetAlphabetNavigation(); search() }
            .onDisappear {
                queryTask?.cancel()
                queryTask = nil
                alphabetJumpTask?.cancel()
                alphabetRailDismissTask?.cancel()
            }
            .sheet(isPresented: $filtersVisible) {
                VideoFilterSheet(filters: $filters, store: store, allowsTechnicalFilters: supportsTechnicalFilterKind)
                    .presentationDetents([.large])
                    .presentationDragIndicator(.visible)
            }
            .toolbar {
                if !usesPortraitNavigationRail {
                    if !searchVisible {
                        ToolbarItem(placement: .topBarTrailing) { videoSearchButton }
                    }
                    if isAlphabetRailAvailable {
                        ToolbarItem(placement: .topBarTrailing) { alphabetMenu(using: scrollProxy) }
                    }
                    ToolbarItem(placement: .topBarTrailing) { videoOptionsMenu }
                }
            }
            .portraitNavigationRail(isEnabled: usesPortraitNavigationRail, store: store) {
                if !searchVisible { videoSearchButton }
                if isAlphabetRailAvailable { alphabetMenu(using: scrollProxy) }
                videoOptionsMenu
            }
            .task(id: "\(kind.rawValue)-\(activeKind?.rawValue ?? "inactive")") {
                guard activeKind == kind else { return }
                await loadInitialContent()
            }
        }
        }
    }

    private var videoSearchButton: some View {
        Button("Search video", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("videoSearchButton")
    }

    private var videoOptionsMenu: some View {
        Menu {
            Picker("Sort: \(sort.label)", selection: $sort) {
                ForEach(VideoSort.allCases, id: \.self) { sort in Text(sort.label).tag(sort) }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("videoSortPicker")
            .accessibilityValue(sort.label)

            Divider()
            Button("Filters", glyph: .filter) {
                filtersVisible = true
            }
            .accessibilityIdentifier("videoFiltersButton")
            .accessibilityValue(filters == VideoFilters() ? "No filters" : filterSummary)

            if filters != VideoFilters() {
                Button("Clear filters", glyph: .close) { filters = VideoFilters() }
                    .accessibilityIdentifier("videoClearFiltersButton")
            }
        } label: {
            Label("Video options, \(optionsAccessibilityValue)", glyph: .moreHorizontal)
                .labelStyle(.iconOnly)
        }
        .accessibilityIdentifier("videoOptionsMenu")
        .accessibilityValue(optionsAccessibilityValue)
    }

    private var isAlphabetRailAvailable: Bool {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && sort == .name
            && [.movies, .shows, .anime].contains(kind)
            && alphabetAnchorsScopeKey == alphabetScopeKey
            && store.supportsLibraryAlphabet
            && !store.libraryAlphabet.isEmpty
    }

    private var alphabetScopeKey: String {
        [
            kind.rawValue,
            sort.rawValue,
            searchText.trimmingCharacters(in: .whitespacesAndNewlines),
            filters.collectionID ?? "",
            filters.genre,
            filters.year.map(String.init) ?? "",
            String(filters.unwatchedOnly),
            String(filters.favoritesOnly),
            filters.resolution?.rawValue ?? "",
            filters.hdr?.rawValue ?? "",
            filters.audio?.rawValue ?? "",
            String(filters.imaxOnly),
        ].joined(separator: "\u{1F}")
    }

    private var usesAlphabetMenu: Bool {
        voiceOver || dynamicTypeSize.isAccessibilitySize
    }

    @ViewBuilder
    private func alphabetMenu(using scrollProxy: ScrollViewProxy) -> some View {
        Menu {
            ForEach(store.libraryAlphabet) { anchor in
                Button(anchor.letter) {
                    jump(to: anchor, using: scrollProxy)
                }
            }
        } label: {
            Label("Browse alphabet", systemImage: "textformat.abc")
                .labelStyle(.iconOnly)
        }
        .accessibilityIdentifier("videoAlphabetButton")
        .accessibilityLabel("Browse library alphabet")
        .accessibilityValue(selectedAlphabetAnchor?.letter ?? "")
    }

    private func revealAlphabetRail() {
        guard isAlphabetRailAvailable, !usesAlphabetMenu else { return }
        alphabetRailVisible = true
        scheduleAlphabetRailDismiss()
    }

    private func scheduleAlphabetRailDismiss() {
        alphabetRailDismissTask?.cancel()
        alphabetRailDismissTask = Task {
            try? await Task.sleep(for: .seconds(3))
            guard !Task.isCancelled else { return }
            alphabetRailVisible = false
        }
    }

    private func cancelAlphabetRailDismiss() {
        alphabetRailDismissTask?.cancel()
        alphabetRailDismissTask = nil
    }

    private func resetAlphabetNavigation() {
        alphabetJumpTask?.cancel()
        alphabetJumpTask = nil
        cancelAlphabetRailDismiss()
        alphabetRailVisible = false
        selectedAlphabetAnchor = nil
        alphabetJumpID = UUID()
        alphabetAnchorsScopeKey = nil
    }

    private func jump(to anchor: LibraryAlphabetAnchor, using scrollProxy: ScrollViewProxy) {
        guard isAlphabetRailAvailable else { return }
        alphabetJumpTask?.cancel()
        let jumpID = UUID()
        alphabetJumpID = jumpID
        selectedAlphabetAnchor = anchor
        alphabetRailVisible = false

        alphabetJumpTask = Task {
            guard let firstItemID = await store.jumpLibrary(to: anchor), !Task.isCancelled, alphabetJumpID == jumpID else { return }
            // The page replacement publishes on the main actor. Yield once so the
            // lazy wall has installed the returned item's scroll target.
            await Task.yield()
            guard !Task.isCancelled, alphabetJumpID == jumpID else { return }
            if reduceMotion {
                scrollProxy.scrollTo(firstItemID, anchor: .top)
            } else {
                withAnimation(.easeOut(duration: 0.22)) {
                    scrollProxy.scrollTo(firstItemID, anchor: .top)
                }
            }
        }
    }

    private var supportsTechnicalFilterKind: Bool {
        kind == .movies || kind == .all || kind == .episodes
    }

    private var filterSummary: String {
        var labels = [String]()
        if filters.collectionID != nil { labels.append(filters.collectionName ?? "Collection") }
        if !filters.genre.isEmpty { labels.append(filters.genre) }
        if let year = filters.year { labels.append(String(year)) }
        if filters.unwatchedOnly { labels.append("Unwatched") }
        if filters.favoritesOnly { labels.append("Favorites") }
        if let resolution = filters.resolution { labels.append(resolution.label) }
        if let hdr = filters.hdr { labels.append(hdr.label) }
        if let audio = filters.audio { labels.append(audio.label) }
        if filters.imaxOnly { labels.append("IMAX") }
        return labels.joined(separator: " · ")
    }

    private var optionsAccessibilityValue: String {
        let activeFilters = filters == VideoFilters() ? "No filters" : filterSummary
        return "\(kind.label), sorted by \(sort.label); \(activeFilters)"
    }

    private func loadInitialContent() async {
        guard store.session != nil else { return }
        let scopeKey = alphabetScopeKey
        await store.loadLibrary(search: searchText, kind: kind, sort: sort, filters: filters, preloadCatalog: true)
        guard scopeKey == alphabetScopeKey else { return }
        alphabetAnchorsScopeKey = scopeKey
    }

    private func search() {
        queryTask?.cancel()
        store.clearError()
        let scopeKey = alphabetScopeKey
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        let kind = kind, sort = sort, filters = filters
        queryTask = Task {
            guard !Task.isCancelled else { return }
            await store.loadLibrary(search: query, kind: kind, sort: sort, filters: filters, forceRefresh: true, preloadCatalog: true)
            guard scopeKey == alphabetScopeKey else { return }
            alphabetAnchorsScopeKey = scopeKey
        }
    }

    private func refresh() { search() }
}

@MainActor
private struct VideoAlphabetRail: View {
    let anchors: [LibraryAlphabetAnchor]
    @Binding var selectedAnchor: LibraryAlphabetAnchor?
    let onInteraction: () -> Void
    let onCommit: (LibraryAlphabetAnchor) -> Void

    private var railHeight: CGFloat {
        min(max(CGFloat(anchors.count) * 16 + 12, 108), 480)
    }

    var body: some View {
        GeometryReader { proxy in
            VStack(spacing: 0) {
                ForEach(anchors) { anchor in
                    Text(anchor.letter)
                        .font(.caption2.weight(selectedAnchor == anchor ? .bold : .medium))
                        .foregroundStyle(selectedAnchor == anchor ? LuminaTheme.accentInk : LuminaTheme.muted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .padding(.vertical, 6)
            .background(.thinMaterial, in: Capsule())
            .contentShape(Capsule())
            .gesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .local)
                    .onChanged { value in
                        onInteraction()
                        selectedAnchor = anchor(at: value.location.y, height: proxy.size.height)
                    }
                    .onEnded { value in
                        guard let anchor = anchor(at: value.location.y, height: proxy.size.height) else { return }
                        selectedAnchor = anchor
                        onCommit(anchor)
                    }
            )
        }
        .frame(width: 38, height: railHeight)
    }

    private func anchor(at y: CGFloat, height: CGFloat) -> LibraryAlphabetAnchor? {
        guard !anchors.isEmpty, height > 0 else { return nil }
        let proportion = min(max(y / height, 0), 0.999)
        let index = min(Int(proportion * CGFloat(anchors.count)), anchors.count - 1)
        return anchors[index]
    }
}

@MainActor
private struct CollectionFilterPicker: View {
    let store: LuminaStore
    @Binding var selection: String?
    @Binding var selectedName: String?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicType
    @State private var options = [MediaItem]()
    @State private var search = ""
    @State private var loading = true
    @State private var failure: String?

    private var visible: [MediaItem] {
        search.isEmpty ? options : options.filter { $0.name.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Button("Any collection") { selection = nil; selectedName = nil; dismiss() }
                    .buttonStyle(LuminaQuietActionStyle())
                if loading {
                    ProgressView("Loading collections…")
                } else if let failure {
                    InlineErrorView(message: failure) { Task { await load() } }
                } else if visible.isEmpty {
                    ContentUnavailableView.search(text: search)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: dynamicType.isAccessibilitySize ? 220 : 144), spacing: 14, alignment: .top)], spacing: 22) {
                        ForEach(visible) { item in
                            Button {
                                selection = item.id
                                selectedName = item.name
                                dismiss()
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    ArtworkView(url: store.imageURL(for: item), name: item.name, aspect: 2 / 3, glyph: .library)
                                        .overlay(alignment: .topTrailing) {
                                            if selection == item.id {
                                                Image(systemName: "checkmark.circle.fill")
                                                    .symbolRenderingMode(.palette)
                                                    .foregroundStyle(LuminaTheme.paper, LuminaTheme.ink)
                                                    .padding(8)
                                            }
                                        }
                                    Text(item.name).font(.subheadline.weight(.semibold)).lineLimit(2)
                                    if let count = item.childCount {
                                        Text(count == 1 ? "1 title" : "\(count) titles").font(.caption).foregroundStyle(LuminaTheme.muted)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(item.name)
                            .accessibilityValue(selection == item.id ? "Selected" : "")
                            .accessibilityIdentifier("collectionFilter-\(item.id)")
                        }
                    }
                }
            }
            .padding(LuminaTheme.contentGutter)
        }
        .background(LuminaTheme.paper)
        .foregroundStyle(LuminaTheme.ink)
        .navigationTitle("Collection")
        .toolbarVisibility(.visible, for: .navigationBar)
        .searchable(text: $search, prompt: "Find a collection")
        .accessibilityIdentifier("collectionFilterPicker")
        .task { await load() }
    }

    private func load() async {
        loading = true; failure = nil
        defer { loading = false }
        do { options = try await store.collectionFilterOptions() }
        catch is CancellationError { }
        catch { failure = "Collections could not load. Check the connection and try again." }
    }
}

@MainActor
private struct VideoFilterSheet: View {
    @Binding var filters: VideoFilters
    let store: LuminaStore
    let allowsTechnicalFilters: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var draft = VideoFilters()
    @State private var year = ""
    @State private var technicalSupported: Bool?
    @State private var checkingSupport = false
    @State private var supportError: String?

    init(filters: Binding<VideoFilters>, store: LuminaStore, allowsTechnicalFilters: Bool) {
        self._filters = filters
        self.store = store
        self.allowsTechnicalFilters = allowsTechnicalFilters
        self._draft = State(initialValue: filters.wrappedValue)
        self._year = State(initialValue: filters.wrappedValue.year.map(String.init) ?? "")
    }

    private var validYear: Bool {
        year.isEmpty || (year.count == 4 && Int(year).map { (1000...9999).contains($0) } == true)
    }

    private var hasTechnicalSelection: Bool {
        draft.resolution != nil || draft.hdr != nil || draft.audio != nil || draft.imaxOnly
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    NavigationLink {
                        CollectionFilterPicker(store: store, selection: $draft.collectionID, selectedName: $draft.collectionName)
                    } label: {
                        HStack {
                            Label("Collection", glyph: .library)
                            Spacer()
                            Text(draft.collectionName ?? "Any collection").foregroundStyle(LuminaTheme.muted)
                            Image(systemName: "chevron.right").font(.caption)
                        }
                        .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("videoCollectionFilterButton")
                    HStack(alignment: .top, spacing: 24) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Genre").font(.caption).foregroundStyle(LuminaTheme.muted)
                            TextField("Genre", text: $draft.genre, prompt: Text("Any genre"))
                                .autocorrectionDisabled()
                                .accessibilityIdentifier("videoGenreField")
                        }
                        .padding(.bottom, 10)
                        .overlay(alignment: .bottom) { Divider() }
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Year").font(.caption).foregroundStyle(LuminaTheme.muted)
                            TextField("Year", text: $year, prompt: Text("Any year"))
                                .keyboardType(.numberPad)
                                .accessibilityIdentifier("videoYearField")
                        }
                        .padding(.bottom, 10)
                        .overlay(alignment: .bottom) { Divider() }
                    }
                    .textFieldStyle(.plain)
                    if !validYear {
                        Text("Enter a four-digit year or leave it empty.")
                            .font(.caption).foregroundStyle(LuminaTheme.muted)
                    }
                    if allowsTechnicalFilters {
                        if technicalSupported == true {
                            choiceRow("Resolution", id: "resolution", values: VideoResolution.allCases,
                                      selection: $draft.resolution, label: { $0.label })
                            choiceRow("HDR", id: "hdr", values: VideoHDR.allCases,
                                      selection: $draft.hdr, label: { $0.label })
                            binaryRow("Edition", id: "imax", selectedTitle: "IMAX", selection: $draft.imaxOnly)
                            choiceRow("Audio", id: "audio", values: VideoAudioFormat.allCases,
                                      selection: $draft.audio, label: { $0.label })
                        } else if checkingSupport || (technicalSupported == nil && supportError == nil) {
                            ProgressView("Checking filter support…")
                        } else {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(supportError ?? "Update the connected server to enable picture and audio filters.")
                                    .font(.caption).foregroundStyle(LuminaTheme.muted)
                                Button("Check again", glyph: .sync) { Task { await checkSupport() } }
                                    .buttonStyle(LuminaQuietActionStyle())
                                    .accessibilityIdentifier("videoTechnicalFiltersRetryButton")
                            }
                        }
                    }
                    binaryRow("Watch status", id: "unwatched", selectedTitle: "Unwatched", selection: $draft.unwatchedOnly)
                    binaryRow("Favorites", id: "favorites", selectedTitle: "Favorites only", selection: $draft.favoritesOnly)
                    Button("Reset filters", glyph: .restore) { draft = VideoFilters(); year = "" }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("videoResetFiltersButton")
                }
                .padding(24)
            }
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Filters")
            .toolbarVisibility(.visible, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        draft.genre = draft.genre.trimmingCharacters(in: .whitespacesAndNewlines)
                        draft.year = Int(year)
                        filters = draft
                        dismiss()
                    }
                    .disabled(!validYear || (hasTechnicalSelection && technicalSupported != true))
                    .accessibilityIdentifier("videoApplyFiltersButton")
                }
            }
            .task { if allowsTechnicalFilters { await checkSupport() } }
        }
        .tint(LuminaTheme.ink)
        .presentationDetents([.large])
        .accessibilityIdentifier("videoFiltersSheet")
    }

    private func checkSupport() async {
        guard !checkingSupport else { return }
        checkingSupport = true
        supportError = nil
        defer { checkingSupport = false }
        do { technicalSupported = try await store.supportsTechnicalVideoFilters() }
        catch { supportError = "Filter options could not load. Check the connection and retry." }
    }

    private func choiceRow<Value>(_ title: String, id: String, values: [Value], selection: Binding<Value?>,
                                  label: @escaping (Value) -> String) -> some View
        where Value: Hashable & RawRepresentable, Value.RawValue == String {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 16) {
                    choice("Any", id: "\(id)-any", selected: selection.wrappedValue == nil) { selection.wrappedValue = nil }
                    ForEach(values, id: \.self) { value in
                        choice(label(value), id: "\(id)-\(value.rawValue)", selected: selection.wrappedValue == value) {
                            selection.wrappedValue = value
                        }
                    }
                }
            }
        }
    }

    private func binaryRow(_ title: String, id: String, selectedTitle: String, selection: Binding<Bool>) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            HStack(spacing: 16) {
                choice("Any", id: "\(id)-any", selected: !selection.wrappedValue) { selection.wrappedValue = false }
                choice(selectedTitle, id: "\(id)-only", selected: selection.wrappedValue) { selection.wrappedValue = true }
            }
        }
    }

    private func choice(_ title: String, id: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(LuminaUnderlineTabStyle(selected: selected))
            .fixedSize(horizontal: true, vertical: true)
            .accessibilityValue(selected ? "Selected" : "Not selected")
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier("videoFilter-\(id)")
    }
}

@MainActor
struct NativeWatchlistButton: View {
    let item: MediaItem
    let store: LuminaStore
    var identifier = "videoWatchlistButton"
    var onWatchlistChanged: @MainActor () async -> Void = {}
    var onInteractionChanged: @MainActor (Bool) -> Void = { _ in }
    @State private var watchlist: LuminaWatchlist?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                guard let watchlist else { return }
                onInteractionChanged(true)
                Task {
                    defer { onInteractionChanged(false) }
                    let didChange = await watchlist.set(itemID: item.id, included: !watchlist.isIncluded)
                    if didChange { await onWatchlistChanged() }
                }
            } label: {
                Label(watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist",
                      glyph: .bookmark, variant: watchlist?.isIncluded == true ? .solid : .outline)
            }
            .buttonStyle(LuminaMediaActionStyle())
            .disabled(watchlist == nil || watchlist?.isLoading == true || watchlist?.isSaving == true || watchlist?.errorMessage != nil)
            .accessibilityIdentifier(identifier)
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
struct MovieCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var children = [MediaItem]()
    @State private var isLoading = true
    @State private var errorMessage: String?

    private var title: String { item.type == "BoxSet" ? "Movies" : "Titles" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(item.name).font(.largeTitle.bold())
                    if let overview = item.overview, !overview.isEmpty {
                        Text(overview).foregroundStyle(.secondary)
                    }
                }
                .accessibilityIdentifier("movieCollectionHeader")
                if isLoading {
                    LibraryLoadingView()
                } else if let errorMessage {
                    InlineErrorView(message: errorMessage) { Task { await load() } }
                } else if children.isEmpty {
                    ContentUnavailableView(
                        item.type == "Playlist" ? "No playable video titles" : "No movies yet",
                        systemImage: "rectangle.stack",
                        description: Text(item.type == "Playlist" ? "This list has no video titles Lumina can play here." : "Add movies to Lumina, then refresh this collection.")
                    )
                } else {
                    Text(title).font(.title2.bold()).accessibilityAddTraits(.isHeader)
                    VideoPosterWall(items: children, store: store)
                }
            }
            .padding(.horizontal, LuminaTheme.contentGutter)
            .padding(.vertical, 24)
        }
        .background(LuminaTheme.paper)
        .navigationTitle(item.name)
        .accessibilityIdentifier("movieCollectionScreen")
        .task(id: item.id) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        isLoading = children.isEmpty
        errorMessage = nil
        do {
            children = try await store.loadChildren(of: item)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "This collection could not load. Check your connection and try again."
        }
        isLoading = false
    }
}
