import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

struct NativeSearchPresentation: ViewModifier {
    @Binding var text: String
    @Binding var isPresented: Bool
    let prompt: String
    var usesInlineSearch = false
    var onSubmit: () -> Void = {}

    func body(content: Content) -> some View {
        Group {
            if isPresented && usesInlineSearch {
                content.safeAreaInset(edge: .top, spacing: 0) {
                    HStack(spacing: 8) {
                        LibrarySearchBar(text: $text, prompt: prompt, onSubmit: onSubmit)
                            .frame(height: 44)
                        Button("Cancel") { isPresented = false }
                            .frame(minWidth: 44, minHeight: 44)
                    }
                    .padding(.horizontal, 20)
                    .padding(.trailing, 76)
                    .padding(.vertical, 8)
                    .background(LuminaTheme.paper)
                }
            } else if isPresented {
                content.searchable(text: $text, isPresented: $isPresented,
                    placement: .navigationBarDrawer(displayMode: .always), prompt: Text(prompt))
                    .safeAreaInset(edge: .top, spacing: 0) {
                        if UIDevice.current.userInterfaceIdiom == .pad {
                            HStack {
                                Spacer()
                                Button("Cancel") { isPresented = false }
                                    .frame(minWidth: 44, minHeight: 44)
                                    .accessibilityIdentifier("nativeSearchDismissButton")
                            }
                            .padding(.horizontal, 16)
                            .background(.bar)
                        }
                    }
            } else {
                content
            }
        }
        .onChange(of: isPresented) { _, presented in
            if !presented { text = "" }
        }
    }
}

struct LibrarySearchBar: UIViewRepresentable {
    @Binding var text: String
    var prompt = String(localized: "Search video")
    let onSubmit: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> UISearchBar {
        let searchBar = UISearchBar()
        searchBar.delegate = context.coordinator
        searchBar.placeholder = prompt
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
struct LibraryFeaturedView: View {
    let store: LuminaStore
    var items: [MediaItem]? = nil
    var compactMinimumHeight: CGFloat? = 320
    var compactArtworkMinimumHeight: CGFloat?
    var wideMinimumHeight: CGFloat? = 540
    var topContentClearance: CGFloat = 0
    var bottomContentClearance: CGFloat = 0
    var onWatchlistChanged: @MainActor () async -> Void = {}

    @State private var request: PlaybackRequest?
    @State private var preparing = false
    @State private var playbackError: String?
    @State private var detailItem: MediaItem?
    @State private var heroIsVisible = true
    @State private var watchlistIsBusy = false

    var body: some View {
        FeaturedShelf(
            items: items ?? store.featuredItems,
            store: store,
            isInteracting: preparing || request != nil || watchlistIsBusy || !heroIsVisible,
            showsOverview: false,
            showsContext: false,
            showsMetadata: false,
            showsProgress: false,
            compactMinimumHeight: compactMinimumHeight,
            compactArtworkMinimumHeight: compactArtworkMinimumHeight,
            wideMinimumHeight: wideMinimumHeight,
            topContentClearance: topContentClearance,
            bottomContentClearance: bottomContentClearance,
            onOpen: { detailItem = $0 }
        ) { item in
            VStack(alignment: .leading, spacing: 12) {
                if let context = context(for: item) {
                    Text(context)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { heroActionButtons(for: item) }
                    VStack(alignment: .leading, spacing: 12) { heroActionButtons(for: item) }
                }
            }
            if let playbackError {
                Text(playbackError).font(.callout)
            }
        }
        .onScrollVisibilityChange(threshold: 0.2) { heroIsVisible = $0 }
        .nativePlayback(request: $request, store: store) {
            Task { await store.refresh() }
        }
        .navigationDestination(item: $detailItem) { item in
            TitleDetailView(item: item, store: store)
                .toolbarVisibility(.visible, for: .navigationBar)
        }
    }

    @ViewBuilder private func heroActionButtons(for item: MediaItem) -> some View {
        Button {
            guard !preparing else { return }
            preparing = true
            playbackError = nil
            Task {
                defer { preparing = false }
                do { request = try await store.preparePlayback(for: item) }
                catch { playbackError = "Playback could not start. Try again." }
            }
        } label: {
            Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 24)
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")

        NativeWatchlistButton(
            item: item,
            store: store,
            identifier: "featuredWatchlistButton",
            onWatchlistChanged: onWatchlistChanged,
            onInteractionChanged: { watchlistIsBusy = $0 }
        )
    }

    private func context(for item: MediaItem) -> String? {
        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            return Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)) + " remaining"
        }
        return nil
    }
}

@MainActor
struct VideoPosterWall: View {
    let items: [MediaItem]
    let store: LuminaStore

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .body) private var minimumPosterWidth: CGFloat = 128
    @ScaledMetric(relativeTo: .body) private var posterGap: CGFloat = 10
    @State private var availableWidth: CGFloat = 0
    @State private var lastVisibleItemID: String?
    @State private var lastVisibleIndex = 0
    @State private var movingForward = true
    @State private var preparedArtwork: [URL]

    init(items: [MediaItem], store: LuminaStore) {
        self.items = items
        self.store = store
        let firstScreen = items.prefix(36).compactMap { store.imageURL(for: $0) }
        _preparedArtwork = State(initialValue: firstScreen.allSatisfy {
            ArtworkImageCache.shared.cachedImage(for: $0) != nil
        } ? firstScreen : [])
    }

    var body: some View {
        Group {
            if !items.isEmpty && preparedArtwork != firstScreenArtwork {
                LibraryLoadingView()
            } else {
                wall(width: availableWidth)
            }
        }
            .frame(maxWidth: .infinity, alignment: .leading)
            .task(id: firstScreenArtwork) {
                let urls = firstScreenArtwork
                await withTaskGroup(of: Void.self) { group in
                    for url in urls {
                        group.addTask { _ = await ArtworkImageCache.shared.image(for: url) }
                    }
                }
                guard !Task.isCancelled else { return }
                preparedArtwork = urls
            }
            #if LUMINA_UI_TESTING
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("libraryArtworkReadiness")
            .accessibilityValue("ready=\(preparedArtwork == firstScreenArtwork ? firstScreenArtwork.count : 0) total=\(firstScreenArtwork.count) catalog=\(items.count)")
            #endif
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { availableWidth = $0 }
            .task(id: "\(lastVisibleItemID ?? "")-\(items.count)", priority: .utility) {
                await store.prefetchArtwork(after: lastVisibleItemID, in: items, count: 72, movingForward: movingForward)
            }
    }

    // Only the initial viewport gates a new wall. Appending catalog metadata must
    // never wait for another batch of artwork or copy/scan the entire library.
    private var firstScreenArtwork: [URL] {
        items.prefix(36).compactMap { store.imageURL(for: $0) }
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
        .id(item.id)
        .onAppear {
            if store.hasMore, !store.isCatalogLoading, item.id == items.suffix(6).first?.id {
                Task { await store.loadMore() }
            }
        }
        .onScrollVisibilityChange(threshold: 0.01) { visible in
            guard visible, let index = items.firstIndex(where: { $0.id == item.id }) else { return }
            movingForward = index >= lastVisibleIndex
            lastVisibleIndex = index
            lastVisibleItemID = item.id
        }
        .contextMenu { NativeMetadataActions(item: item) }
    }

    private func columnCount(for width: CGFloat) -> Int {
        let count = Int((width + posterGap) / (minimumPosterWidth + posterGap))
        return min(max(count, 2), 6)
    }


}

@MainActor
struct MediaRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    var wide = true
    var feed: LuminaHomeFeed? = nil
    var onHide: (String) -> Void = { _ in }

    @ScaledMetric(relativeTo: .body) private var posterWidth: CGFloat = 132
    @ScaledMetric(relativeTo: .body) private var stillWidth: CGFloat = 276

    private var captionPresentation: HomeRailCaptionPresentation {
        HomeRailCaptionPresentation(identifierPrefix: identifierPrefix)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title3.weight(.bold))
                .padding(.top, 10)
                .frame(maxWidth: .infinity, alignment: .leading)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(items) { item in
                        HomeRailCard(item: item, feed: feed, onHide: onHide) {
                        NavigationLink(value: item) {
                            MediaCard(
                                item: item,
                                imageURL: wide ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item) : store.posterURL(for: item),
                                identifierPrefix: identifierPrefix,
                                wide: wide,
                                captionPresentation: captionPresentation,
                                loadFraming: { await store.artworkFraming(for: $0) }
                            )
                            .frame(width: cardWidth(for: item), alignment: .leading)
                        }
                        .contentShape([.interaction, .accessibility], Rectangle())
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityAddTraits(.isButton)
                        .accessibilityLabel(mediaAccessibilityLabel(for: item, presentation: captionPresentation))
                        .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .modifier(LuminaCarouselEdges())
            .accessibilityIdentifier("mediaRail-\(identifierPrefix)")
        }
    }

    private func cardWidth(for item: MediaItem) -> CGFloat {
        if item.isAudio || item.type.hasPrefix("Music") { return posterWidth * 1.2 }
        return wide ? stillWidth : posterWidth
    }
}

@MainActor private struct HomeRailCard<Content: View>: View {
    let item: MediaItem
    let feed: LuminaHomeFeed?
    let onHide: (String) -> Void
    let content: Content
    @State private var isVisible = false

    init(item: MediaItem, feed: LuminaHomeFeed?, onHide: @escaping (String) -> Void, @ViewBuilder content: () -> Content) {
        self.item = item
        self.feed = feed
        self.onHide = onHide
        self.content = content()
    }

    var body: some View {
        content
            .onScrollVisibilityChange(threshold: 0.6) { isVisible = $0 }
            .task(id: isVisible) {
                guard isVisible, let reco = item.luminaReco else { return }
                try? await Task.sleep(for: .milliseconds(600))
                guard !Task.isCancelled else { return }
                await feed?.record("impression", recommendation: reco)
            }
            .simultaneousGesture(TapGesture().onEnded {
                if let reco = item.luminaReco { Task { await feed?.record("open", recommendation: reco) } }
            })
            .contextMenu {
                NativeMetadataActions(item: item)
                if item.luminaReco != nil {
                    Button("Not interested") {
                        Task {
                            if let hidden = await feed?.hide(item) { onHide(hidden) }
                        }
                    }
                }
            }
    }
}

@MainActor
struct MediaCard: View {
    let item: MediaItem
    let imageURL: URL?
    let identifierPrefix: String
    var wide = false
    var showsCaption = true
    var captionPresentation: HomeRailCaptionPresentation = .standard
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var titleHeight: CGFloat = 34
    @ScaledMetric(relativeTo: .caption2) private var metadataHeight: CGFloat = 18

    private var caption: HomeRailCaption {
        HomeRailCaption(item: item, presentation: captionPresentation)
    }

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 6) {
            ArtworkView(url: imageURL, name: item.name,
                        aspect: item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" ? 1 : wide ? 16 / 9 : 2 / 3,
                        glyph: item.isAudio || item.type.hasPrefix("Music") ? .music : .movies,
                        loadFraming: loadFraming)
                .accessibilityIdentifier("\(identifierPrefix)Artwork-\(item.id)")
                .overlay(alignment: .bottom) {
                    if progressFraction > 0 {
                        ProgressView(value: progressFraction)
                            .progressViewStyle(.linear)
                            .tint(LuminaTheme.brand)
                            .frame(height: 3)
                            .clipShape(.rect(cornerRadius: 2))
                    }
                }

            if showsCaption {
                Text(caption.primary)
                    .font(.caption.weight(.semibold))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : titleHeight, alignment: .topLeading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("\(identifierPrefix)Title-\(item.id)")

                Group {
                    if let secondary = caption.secondary {
                        Text(secondary)
                    } else {
                        Color.clear.accessibilityHidden(true)
                    }
                }
                .font(.caption2)
                .foregroundStyle(LuminaTheme.muted)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .truncationMode(.tail)
                .fixedSize(horizontal: false, vertical: true)
                .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataHeight, alignment: .topLeading)
                .frame(maxWidth: .infinity, alignment: .leading)

                if captionPresentation.reservesProgressBaseline {
                    Group {
                        if let progress = caption.progress {
                            Text(progress)
                        } else {
                            Color.clear.accessibilityHidden(true)
                        }
                    }
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataHeight, alignment: .topLeading)
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

enum HomeRailCaptionPresentation: Equatable {
    case standard
    case continueWatching
    case nextUp

    init(identifierPrefix: String) {
        switch identifierPrefix {
        case "resumeItem": self = .continueWatching
        case "nextUpItem": self = .nextUp
        default: self = .standard
        }
    }

    var reservesProgressBaseline: Bool {
        self != .standard
    }
}

private struct HomeRailCaption {
    let primary: String
    let secondary: String?
    let progress: String?

    init(item: MediaItem, presentation: HomeRailCaptionPresentation) {
        guard presentation != .standard else {
            primary = item.name
            secondary = item.displaySubtitle.isEmpty ? nil : item.displaySubtitle
            progress = nil
            return
        }

        let episodeLabel = Self.episodeLabel(for: item)
        primary = item.type == "Episode" ? item.seriesName ?? item.name : item.name
        secondary = episodeLabel ?? (item.displaySubtitle.isEmpty ? nil : item.displaySubtitle)

        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            let remaining = Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated))
            progress = "\(remaining) remaining"
        } else if presentation == .nextUp, item.type == "Episode" {
            progress = "Next episode"
        } else {
            progress = nil
        }
    }

    private static func episodeLabel(for item: MediaItem) -> String? {
        guard item.type == "Episode" else { return nil }
        let number: String? = switch (item.parentIndexNumber, item.indexNumber) {
        case let (.some(season), .some(episode)): "S\(season) · E\(episode)"
        default: nil
        }
        return [number, item.name].compactMap { $0 }.joined(separator: " · ")
    }

    var accessibilityLabel: String {
        [primary, secondary, progress].compactMap { $0 }.joined(separator: ", ")
    }
}

@MainActor
struct ArtworkView: View {
    let url: URL?
    let name: String
    var aspect: CGFloat = 2 / 3
    var glyph: LuminaGlyph = .movies
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
            LuminaIcon(glyph: glyph, variant: .solid, size: 28)
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
struct ActorLibraryView: View {
    let person: MediaPerson
    let store: LuminaStore
    @State private var items: [MediaItem] = []
    @State private var loading = true
    @State private var message: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(person.name).font(.largeTitle.bold())
                Text("In your library").font(.subheadline).foregroundStyle(LuminaTheme.muted)
                if loading {
                    LibraryLoadingView()
                } else if let message {
                    InlineErrorView(message: message) { Task { await load() } }
                } else if items.isEmpty {
                    ContentUnavailableView("No titles found", systemImage: "person",
                        description: Text("No movies or shows featuring this person are available in your library."))
                } else {
                    VideoPosterWall(items: items, store: store)
                }
            }
            .padding(.horizontal, LuminaTheme.contentGutter)
            .padding(.vertical, 24)
        }
        .background(LuminaTheme.paper)
        .navigationTitle(person.name)
        .standardPhonePushedChrome(store: store)
        .accessibilityIdentifier("actorLibraryScreen")
        .task(id: person) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        loading = items.isEmpty
        message = nil
        defer { loading = false }
        do {
            let loaded = try await store.titles(featuring: person)
            try Task.checkCancellation()
            items = loaded
        } catch is CancellationError {
            return
        } catch {
            message = "These titles could not load. Check your connection and try again."
        }
    }
}

@MainActor
struct NativeTitleSupplement: View {
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
                                NavigationLink(value: person) {
                                    VStack(alignment: .leading, spacing: 8) {
                                        CachedArtworkImage(url: store.personImageURL(for: person)) { image in
                                            image.resizable().scaledToFill()
                                        } placeholder: {
                                            LuminaTheme.ink.opacity(0.06)
                                                .overlay { LuminaIcon(glyph: .profile, variant: .solid, size: 40).foregroundStyle(LuminaTheme.muted) }
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
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Find titles with \(person.name)")
                                .accessibilityHint("Shows movies and shows in your library")
                                .accessibilityIdentifier("detailActor-\(person.name)")
                            }
                        }
                    }
                    .modifier(LuminaCarouselEdges())
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
struct TitleDetailView: View {
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
                    .padding(.top, currentItem.isAudio ? -20 : 0)

                VStack(alignment: .leading, spacing: 20) {
                    if let stateError {
                        Label(stateError, glyph: .warning)
                            .font(.callout)
                            .foregroundStyle(LuminaTheme.accentInk)
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("videoStateError")
                    }

                    if let overview = currentItem.overview, !overview.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("About").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                            NativeMediaAbout(overview: MediaOverview(overview))
                                .padding(.trailing, UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome ? 0 : 64)
                        }
                        .foregroundStyle(LuminaTheme.ink)
                    }

                    videoDownloadStatus

                    if let playbackError, !playbackError.isEmpty {
                        Label(playbackError, glyph: .warning)
                            .font(.callout)
                            .foregroundStyle(LuminaTheme.accentInk)
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
        .scrollClipDisabled()
        .accessibilityIdentifier("detailScreen")
        .modifier(VideoDetailChrome(isEnabled: !currentItem.isAudio))
        .standardPhonePushedChrome(isEnabled: currentItem.isAudio, store: store,
                                   foreground: currentItem.isAudio ? LuminaTheme.ink : .white,
                                   background: currentItem.isAudio ? LuminaTheme.paper : LuminaTheme.theater)
        .toolbar {
            if !currentItem.isAudio {
                ToolbarItem(placement: .topBarTrailing) {
                    titleActionsMenu
                }
            }
        }
        .task(id: item.id + "-\(store.metadata.revision)") { await refreshItem() }
        .nativePlayback(request: $playbackRequest, store: store) {
            Task {
                await store.refresh()
                await refreshItem()
            }
        }
    }

    private var favoriteButton: some View {
        Button {
            updateState(favorite: currentItem.userData?.isFavorite != true)
        } label: {
            Label(currentItem.userData?.isFavorite == true ? "Remove favorite" : "Add favorite",
                  glyph: .favorite,
                  variant: currentItem.userData?.isFavorite == true ? .solid : .outline)
                .frame(minWidth: 132)
        }
        .buttonStyle(LuminaMediaActionStyle())
        .controlSize(.large)
        .disabled(isSavingState || isLoadingItem)
        .accessibilityValue(currentItem.userData?.isFavorite == true ? "Favorite" : "Not a favorite")
        .accessibilityIdentifier("videoFavoriteButton")
    }

    private var titleActionsMenu: some View {
        Menu {
            NativeMetadataActions(item: currentItem)
            favoriteButton
            Button {
                updateState(played: currentItem.userData?.played != true)
            } label: {
                Label(currentItem.userData?.played == true ? "Mark unwatched" : "Mark watched", glyph: .check)
            }
            .accessibilityValue(currentItem.userData?.played == true ? "Watched" : "Unwatched")
            .accessibilityIdentifier("videoWatchedButton")

            if currentItem.isPlayable, !currentItem.isAudio {
                videoDownloadAction
            }

        } label: {
            Label("More actions", glyph: .moreHorizontal)
        }
        .disabled(isSavingState || isLoadingItem)
        .accessibilityIdentifier("titleActionsMenu")
    }

    @ViewBuilder private var videoDownloadStatus: some View {
        switch store.offline.downloadStates[currentItem.id] {
        case .queued:
            Label("Download queued", glyph: .download).font(.caption)
        case .downloading(let received, let total):
            VStack(alignment: .leading, spacing: 6) {
                if let total, total > 0 { ProgressView(value: Double(received), total: Double(total)) }
                Text("Downloading · \(ByteCountFormatter.string(fromByteCount: received, countStyle: .file))")
                    .font(.caption.monospacedDigit())
            }
            .accessibilityIdentifier("videoDownloadProgress")
        case .downloaded:
            Label("Downloaded to this device", glyph: .downloadDone).font(.caption)
                .accessibilityIdentifier("videoDownloadedStatus")
        case .failed(let message):
            Label(message, glyph: .warning).font(.caption)
                .accessibilityIdentifier("videoDownloadError")
        case .cancelled, .none:
            EmptyView()
        }
    }

    @ViewBuilder private var videoDownloadAction: some View {
        switch store.offline.downloadStates[currentItem.id] {
        case .downloaded:
            Button("Remove Download", glyph: .trash, role: .destructive) {
                store.offline.remove(id: currentItem.id)
            }
            .accessibilityIdentifier("removeVideoDownloadButton")
        case .queued, .downloading:
            Button("Cancel Download", glyph: .close) { store.offline.cancel(id: currentItem.id) }
                .accessibilityIdentifier("cancelVideoDownloadButton")
        default:
            Button("Download to Device", glyph: .download) {
                Task { await store.downloadVideo(item: currentItem) }
            }
            .disabled(store.isOffline || !LuminaOfflineLibrary.supportsVideoDownload(currentItem))
            .accessibilityHint("Save this video for offline playback on this device.")
            .accessibilityIdentifier("downloadVideoButton")
        }
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

    @ViewBuilder private var detailHeader: some View {
        if currentItem.isAudio {
            detailBillboard
        } else {
            detailBillboard
                .containerRelativeFrame(.vertical) { length, _ in length }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("detailHero")
        }
    }

    private var detailBillboard: some View {
        MediaBillboard(item: currentItem, store: store, context: "", showsOverview: false, showsContext: false,
                       showsMetadata: currentItem.isAudio, artworkFirst: !currentItem.isAudio,
                       compactMinimumHeight: currentItem.isAudio ? nil : 0,
                       wideMinimumHeight: currentItem.isAudio ? nil : 0,
                       bottomContentClearance: !currentItem.isAudio && usesStandardPhonePushedChrome ? 100 : 0) {
            if !currentItem.isAudio { detailHeroMetadata }
            if currentItem.isPlayable {
                playbackActions
            } else {
                Text("Playback is unavailable for this title.")
                    .font(.callout)
            }
        }
    }

    private var detailHeroMetadata: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { detailHeroMetadataContent }
            VStack(alignment: .leading, spacing: 8) { detailHeroMetadataContent }
        }
        .font(.caption.weight(.medium))
        .textCase(.uppercase)
        .tracking(1)
        .foregroundStyle(.white.opacity(0.85))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var detailHeroMetadataContent: some View {
        if let year = currentItem.productionYear { Text(String(year)) }
        if let rating = currentItem.officialRating, !rating.isEmpty { Text(rating) }
        if currentItem.durationSeconds > 0 {
            Text(Duration.seconds(currentItem.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
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
                Label(playLabel, glyph: .play, variant: .solid, size: 24)
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
                        : "Playback could not start. Check your connection or play a downloaded copy."
            }
            isPreparingPlayback = false
        }
    }
}

@MainActor
struct LibraryLoadingView: View {
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

func libraryGridColumns(for dynamicTypeSize: DynamicTypeSize) -> [GridItem] {
    if dynamicTypeSize.isAccessibilitySize {
        return [GridItem(.flexible(minimum: 0, maximum: 220), spacing: 20, alignment: .top)]
    }
    return [GridItem(.adaptive(minimum: 96, maximum: 166), spacing: 10, alignment: .top)]
}

@MainActor
struct EmptyLibraryView: View {
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            LuminaIcon(glyph: .movies, variant: .solid, size: 40)
                .foregroundStyle(LuminaTheme.accentInk)
                .accessibilityHidden(true)
            Text("Your video library is quiet.")
                .font(.title2)
                .fontWeight(.semibold)
            Text("Add video to Lumina, then refresh to see it here.")
                .foregroundStyle(LuminaTheme.muted)
            Button("Refresh", action: onRefresh)
                .buttonStyle(LuminaQuietActionStyle())
        }
        .padding(.vertical, 24)
    }
}

@MainActor
struct InlineErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(message, glyph: .warning)
                .font(.callout)
                .foregroundStyle(LuminaTheme.accentInk)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isStaticText)
            Button("Try again", action: onRetry)
                .buttonStyle(LuminaQuietActionStyle())
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 14))
    }
}

func mediaAccessibilityLabel(
    for item: MediaItem,
    presentation: HomeRailCaptionPresentation = .standard
) -> String {
    let caption = HomeRailCaption(item: item, presentation: presentation)
    var parts = [caption.accessibilityLabel]
    if item.resumeSeconds > 0, presentation == .standard {
        parts.append(item.durationSeconds > item.resumeSeconds ? "in progress" : "played")
    }
    return parts.filter { !$0.isEmpty }.joined(separator: ", ")
}
