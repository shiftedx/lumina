import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVStreamingView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let savedOnly: Bool
    @Environment(\.scenePhase) private var scenePhase
    @State private var savedLibrary: LuminaStreamingLibrary
    @State private var remoteFeed: LuminaHomeFeed
    @State private var selectedTab: TVStreamingTab = .saved
    @State private var selectedRemote: HomeExternalItem?
    @State private var hasUserSelectedTab = false
    @FocusState private var focusedTab: TVStreamingTab?

    init(store: LuminaStore, onOpen: @escaping (MediaItem) -> Void, savedOnly: Bool = false) {
        self.store = store
        self.onOpen = onOpen
        self.savedOnly = savedOnly
        _savedLibrary = State(initialValue: LuminaStreamingLibrary(session: store.session))
        _remoteFeed = State(initialValue: LuminaHomeFeed(session: store.session, includesExternalSources: true))
    }

    private var remoteAvailable: Bool { store.nativeStreamingCapabilities?.remote.available == true }
    private var remoteLiveAvailable: Bool { store.nativeStreamingCapabilities?.remote.liveAvailable == true }
    private var recommendationItems: [HomeExternalItem] {
        deduplicated(remoteFeed.rows
            .filter { $0.shelfID != .live }
            .flatMap { $0.externalItems ?? [] })
    }
    private var liveItems: [HomeExternalItem] {
        deduplicated(remoteFeed.rows
            .filter { $0.shelfID == .live }
            .flatMap { $0.externalItems ?? [] })
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                if savedOnly {
                    savedContent
                } else {
                    HStack(spacing: 22) {
                        streamingTab("Saved", tab: .saved, identifier: "streamingDownloadedTab")
                        if remoteAvailable {
                            streamingTab("For you", tab: .recommendations, identifier: "streamingRecommendationsTab")
                        }
                        if remoteAvailable && remoteLiveAvailable {
                            streamingTab("Live", tab: .live, identifier: "streamingLiveTab")
                        }
                        Spacer()
                        Button("Refresh") { Task { await load() } }
                            .buttonStyle(LuminaUnderlineTabStyle())
                            .disabled(savedLibrary.isBusy || remoteFeed.isLoading)
                    }
                    .focusSection()

                    Group {
                        switch selectedTab {
                        case .saved:
                            savedContent
                        case .recommendations:
                            remoteContent(items: recommendationItems, emptyTitle: "Nothing recommended yet", emptyMessage: "Try again once your server has new recommendations.")
                        case .live:
                            remoteContent(items: liveItems, emptyTitle: "No live streams right now", emptyMessage: "Live sources will appear here when they are available.")
                        }
                    }
                }
            }
            .padding(.horizontal, 80)
            .padding(.top, 28)
            .padding(.bottom, 80)
        }
        .background(LuminaTheme.theater)
        .accessibilityIdentifier(savedOnly ? "savedStreamingLibrary" : "streamingScreen")
        .task(id: TVHomeSessionScope(store.session)) {
            await load()
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task { await load() }
        }
        .fullScreenCover(item: $selectedRemote) { item in
            NativeRemotePlayer(store: store, item: item)
        }
    }

    private func streamingTab(_ title: String, tab: TVStreamingTab, identifier: String) -> some View {
        Button {
            hasUserSelectedTab = true
            selectedTab = tab
        } label: { Text(title) }
            .buttonStyle(LuminaUnderlineTabStyle(selected: selectedTab == tab))
            .focused($focusedTab, equals: tab)
            .accessibilityValue(selectedTab == tab ? "Selected" : "")
            .accessibilityAddTraits(selectedTab == tab ? .isSelected : [])
            .accessibilityIdentifier(identifier)
    }

    @ViewBuilder private var savedContent: some View {
        if savedLibrary.isBusy && savedLibrary.items.isEmpty {
            ProgressView("Loading saved videos…")
                .frame(maxWidth: .infinity, minHeight: 360)
        } else if savedLibrary.items.isEmpty {
            TVMessageView(
                title: "No saved streaming videos",
                message: savedLibrary.errorMessage ?? "Videos you download from supported sources will appear here.",
                actionTitle: "Refresh"
            ) { Task { await savedLibrary.load() } }
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300, maximum: 440), spacing: 30, alignment: .top)], spacing: 42) {
                ForEach(savedLibrary.items) { item in
                    TVMediaCard(item: item, store: store, poster: false, artworkWidth: 760) { onOpen(item) }
                        .accessibilityIdentifier("streamingSaved-\(item.id)")
                }
            }
            .focusSection()
            if savedLibrary.hasMore {
                Button("Load more") { Task { await savedLibrary.loadMore() } }
                    .buttonStyle(LuminaQuietActionStyle())
                    .disabled(savedLibrary.isBusy)
            }
        }
    }

    @ViewBuilder private func remoteContent(items: [HomeExternalItem], emptyTitle: String, emptyMessage: String) -> some View {
        if remoteFeed.isLoading && items.isEmpty {
            ProgressView("Loading streaming…")
                .frame(maxWidth: .infinity, minHeight: 360)
        } else if items.isEmpty {
            TVMessageView(title: emptyTitle, message: remoteFeed.errorMessage ?? emptyMessage, actionTitle: "Refresh") {
                Task { await load() }
            }
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 340, maximum: 480), spacing: 30, alignment: .top)], spacing: 42) {
                ForEach(items) { item in
                    TVStreamingRemoteCard(item: item) { selectedRemote = item }
                        .accessibilityIdentifier("streamingRemote-\(item.id)")
                }
            }
            .focusSection()
        }
    }

    private func load() async {
        savedLibrary.configure(session: store.session)
        if savedOnly {
            await savedLibrary.load()
            return
        }
        remoteFeed.configure(session: store.session)
        async let saved: Void = savedLibrary.load()
        await refreshRemoteContent()
        await saved
    }

    private func refreshRemoteContent() async {
        let capabilities = await store.loadNativeStreamingCapabilities()
        guard let remote = capabilities?.remote, remote.available else {
            if selectedTab != .saved { selectedTab = .saved }
            return
        }

        if !hasUserSelectedTab, selectedTab == .saved {
            selectedTab = .recommendations
        } else if selectedTab == .live, !remote.liveAvailable {
            selectedTab = .recommendations
        }
        await remoteFeed.load()
    }

    private func deduplicated(_ items: [HomeExternalItem]) -> [HomeExternalItem] {
        var itemIDs = Set<String>()
        return items.filter { itemIDs.insert($0.id).inserted }
    }
}

private struct TVStreamingRemoteCard: View {
    let item: HomeExternalItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                AsyncImage(url: item.artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image.resizable().scaledToFill()
                    default:
                        Rectangle().fill(.white.opacity(0.08))
                            .overlay { LuminaIcon(glyph: .play, size: 42).foregroundStyle(LuminaTheme.muted) }
                    }
                }
                .frame(height: 220)
                .clipShape(.rect(cornerRadius: 14))

                Text(item.title)
                    .font(TVTypography.cardTitle.weight(.semibold))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(2)
                Text(item.subtitle)
                    .font(TVTypography.metadata)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: 16 / 9))
        .accessibilityLabel("\(item.title), \(item.subtitle)")
    }
}

struct TVCollectionGrid: View {
    let title: String?
    let items: [MediaItem]
    let store: LuminaStore
    let focusedItemID: FocusState<String?>.Binding
    let onOpen: (MediaItem) -> Void
    let onItemDidAppear: (MediaItem) -> Void
    let onExitTop: () -> Void
    @State private var columnCount = 1
    private let minimumPosterWidth: CGFloat = 220
    private let columnSpacing: CGFloat = 32

    var body: some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 24) {
                if let title {
                    Text(title)
                        .font(TVTypography.sectionTitle)
                        .foregroundStyle(LuminaTheme.ink)
                        .accessibilityAddTraits(.isHeader)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: minimumPosterWidth, maximum: 260), spacing: columnSpacing, alignment: .top)], spacing: 52) {
                    ForEach(items) { item in
                        TVMediaCard(item: item, store: store, poster: true, showsCaption: false, artworkWidth: 480) { onOpen(item) }
                            .focused(focusedItemID, equals: item.id)
                            .id(item.id)
                            .accessibilityIdentifier("libraryItem-\(item.id)")
                            .onAppear { onItemDidAppear(item) }
                    }
                }
                .onGeometryChange(for: Int.self) { geometry in
                    max(1, Int((geometry.size.width + columnSpacing) / (minimumPosterWidth + columnSpacing)))
                } action: { columnCount = $0 }
                .onMoveCommand { direction in
                    guard direction == .up,
                          let focusedID = focusedItemID.wrappedValue,
                          items.prefix(columnCount).contains(where: { $0.id == focusedID }) else { return }
                    onExitTop()
                }
            }
        }
    }
}

struct TVVideoFiltersView: View {
    let filters: VideoFilters
    let onApply: (VideoFilters) -> Void
    let onClose: () -> Void
    @State private var genre = ""
    @State private var year = ""
    @State private var unwatchedOnly = false
    @State private var favoritesOnly = false

    private var validYear: Bool { year.isEmpty || (Int(year).map { (1...9999).contains($0) } ?? false) }

    var body: some View {
        Form {
            Section("Video filters") {
                TextField("Genre", text: $genre)
                    .font(TVTypography.body)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("videoGenreField")
                TextField("Year", text: $year)
                    .font(TVTypography.body)
                    .accessibilityIdentifier("videoYearField")
                if !validYear { Text("Enter a year, or leave it blank for all years.").foregroundStyle(LuminaTheme.accentInk) }
                Toggle("Unwatched only", isOn: $unwatchedOnly)
                    .tint(LuminaTheme.brand)
                    .accessibilityIdentifier("videoUnwatchedToggle")
                Toggle("Favorites only", isOn: $favoritesOnly)
                    .tint(LuminaTheme.brand)
                    .accessibilityIdentifier("videoFavoritesToggle")
            }
            Section {
                Button("Apply filters") {
                    var updated = filters
                    updated.genre = genre.trimmingCharacters(in: .whitespacesAndNewlines)
                    updated.year = Int(year)
                    updated.unwatchedOnly = unwatchedOnly
                    updated.favoritesOnly = favoritesOnly
                    onApply(updated)
                    onClose()
                }
                .disabled(!validYear)
                .accessibilityIdentifier("videoApplyFiltersButton")
                Button("Reset filters") {
                    genre = ""; year = ""; unwatchedOnly = false; favoritesOnly = false
                }
                Button("Cancel", role: .cancel) { onClose() }
                    .accessibilityIdentifier("videoCancelFiltersButton")
            }
        }
        .onAppear {
            genre = filters.genre
            year = filters.year.map(String.init) ?? ""
            unwatchedOnly = filters.unwatchedOnly
            favoritesOnly = filters.favoritesOnly
        }
    }
}
