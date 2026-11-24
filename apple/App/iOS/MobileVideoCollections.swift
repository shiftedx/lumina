import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct VideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let initialSeasonID: String?
    let initialEpisodeID: String?
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var children: [MediaItem] = []
    @State private var selectedSeasonEpisodes: [MediaItem] = []
    @State private var selectedSeasonID: String?
    @State private var selectedEpisodeID: String?
    @State private var isLoadingSelectedSeason = false
    @State private var hasPositionedInitialEpisode = false
    @State private var hasAlignedInitialEpisode = false
    @State private var episodeRailWidth: CGFloat = 0
    @State private var detailItem: MediaItem?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?
    @State private var downloadsPresented = false

    init(item: MediaItem, store: LuminaStore, initialSeasonID: String? = nil, initialEpisodeID: String? = nil) {
        self.item = item
        self.store = store
        self.initialSeasonID = initialSeasonID
        self.initialEpisodeID = initialEpisodeID
        _selectedSeasonID = State(initialValue: initialSeasonID)
        _selectedEpisodeID = State(initialValue: initialEpisodeID)
    }

    private var collectionControlTrailingInset: CGFloat {
        UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome ? 0 : 76
    }

    private var isSeriesCollection: Bool { item.type == "Series" }
    private var collectionSectionTitle: String { "Episodes" }
    private var collectionSectionID: String { "collection-\(item.id)-content" }
    private var hasResumeEpisode: Bool {
        if isSeriesCollection,
           let selectedEpisode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
            return selectedEpisode.resumeSeconds > 5 && selectedEpisode.userData?.played != true
        }
        return store.resumeItems.contains { candidate in
            candidate.type == "Episode"
                && (candidate.seriesID == item.id || candidate.seasonID == item.id)
                && candidate.resumeSeconds > 5
                && candidate.userData?.played != true
        }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    collectionHero(using: proxy)

                    VStack(alignment: .leading, spacing: 28) {
                        if item.type != "Series" {
                            HStack {
                                Text(collectionSectionTitle)
                                    .font(.title2.weight(.bold))
                                    .accessibilityAddTraits(.isHeader)
                                Spacer()
                                downloadButton
                            }
                                .id(collectionSectionID)
                        }
                        if let errorMessage {
                            InlineErrorView(message: errorMessage) { Task { await load() } }
                        }
                        if isLoading && children.isEmpty {
                            ProgressView(item.type == "Series" ? "Loading seasons…" : "Loading episodes…")
                        } else if children.isEmpty && errorMessage == nil {
                            ContentUnavailableView {
                                Label("Nothing here yet", glyph: .series)
                            } description: {
                                Text("No \(item.type == "Series" ? "seasons" : "episodes") are available. Pull to refresh after adding them to Lumina.")
                            }
                        } else if item.type == "Series" {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Episodes")
                                    .font(.title2.weight(.bold))
                                    .accessibilityAddTraits(.isHeader)
                                ViewThatFits(in: .horizontal) {
                                    HStack { seasonSelector; Spacer(); downloadButton }
                                    VStack(alignment: .leading, spacing: 12) { seasonSelector; downloadButton }
                                }
                            }
                            .id(collectionSectionID)
                            if isSeriesCollection {
                                VStack(alignment: .leading, spacing: 12) {
                                    if let playbackError {
                                        Text(playbackError)
                                            .font(.callout).foregroundStyle(LuminaTheme.accentInk)
                                            .accessibilityIdentifier("selectedEpisodePlaybackError")
                                    }
                                    if isLoadingSelectedSeason {
                                        ProgressView("Loading episodes…")
                                    } else {
                                        episodeCards(selectedSeasonEpisodes)
                                            .padding(.trailing, -collectionControlTrailingInset)
                                    }
                                }
                                .id(initialEpisodeSectionID)
                            }
                        } else {
                            episodeCards(children)
                                .padding(.trailing, -collectionControlTrailingInset)
                        }

                        collectionAbout

                        NativeTitleSupplement(item: detailItem ?? item, store: store)
                            .padding(.top, 4)
                    }
                    .padding(.trailing, collectionControlTrailingInset)
                    .padding(.horizontal, LuminaTheme.contentGutter)
                    .padding(.vertical, 28)
                }
                .frame(maxWidth: .infinity)
                // The hero extends under system chrome; the last disclosure must
                // still scroll fully above the floating phone tab bar.
                .padding(.bottom, usesStandardPhonePushedChrome ? 100 : 0)
            }
            .onChange(of: selectedSeasonEpisodes.map(\.id)) {
                positionInitialEpisode(using: proxy)
            }
        }
        .background(LuminaTheme.paper)
        .modifier(VideoDetailChrome())
        .accessibilityIdentifier("videoCollectionScreen")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { collectionActionsMenu } }
        .scrollClipDisabled()
        .task(id: item.id + "-\(store.metadata.revision)") { await load() }
        .refreshable { await load() }
        .sheet(isPresented: $downloadsPresented) {
            EpisodeDownloadPicker(
                store: store, seasons: isSeriesCollection ? children : [item],
                initialSeasonID: isSeriesCollection ? selectedSeasonID : item.id,
                initialEpisodes: isSeriesCollection ? selectedSeasonEpisodes : children
            )
            .preferredColorScheme(colorScheme)
        }
        .nativePlayback(request: $playbackRequest, store: store) {
            Task { await load() }
        }
    }

    private var downloadButton: some View {
        Button("Download to Device", glyph: .download) { downloadsPresented = true }
            .fixedSize(horizontal: true, vertical: false)
            .buttonStyle(LuminaQuietActionStyle())
            .disabled(isLoading || isLoadingSelectedSeason || store.isOffline)
            .accessibilityHint("Choose episodes or whole seasons to save for offline playback.")
            .accessibilityIdentifier("downloadEpisodesButton")
    }

    @ViewBuilder private var collectionActionsMenu: some View {
        if store.metadata.canEdit, store.metadata.supports(detailItem ?? item) {
            Menu { NativeMetadataActions(item: detailItem ?? item) } label: {
                Label("More actions", glyph: .moreHorizontal)
            }
            .accessibilityIdentifier("titleActionsMenu")
        }
    }

    @ViewBuilder private func collectionHero(using proxy: ScrollViewProxy) -> some View {
        MediaBillboard(item: detailItem ?? item, store: store, context: "", showsOverview: false,
                       showsContext: false, showsMetadata: false, artworkFirst: true,
                       compactMinimumHeight: 0, wideMinimumHeight: 0) {
            VStack(alignment: .leading, spacing: 14) {
                collectionHeroMetadata
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .center, spacing: 12) { playbackActions }
                    VStack(alignment: .leading, spacing: 12) { playbackActions }
                }
                Button {
                    if reduceMotion {
                        proxy.scrollTo(collectionSectionID, anchor: .top)
                    } else {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            proxy.scrollTo(collectionSectionID, anchor: .top)
                        }
                    }
                } label: {
                    Label("Browse episodes", glyph: .chevronDown)
                        .font(.subheadline.weight(.semibold))
                }
                .buttonStyle(.plain)
                .frame(minHeight: 44)
                .accessibilityHint("Shows \(collectionSectionTitle.lowercased()) below this artwork")
                .accessibilityIdentifier("collectionSeasonsButton")

                if let playbackError {
                    Label(playbackError, glyph: .warning)
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.accentInk)
                        .accessibilityIdentifier("videoCollectionPlaybackError")
                }
            }
        }
        .containerRelativeFrame(.vertical) { length, _ in length }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("collectionHero")
    }

    private var collectionHeroMetadata: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { collectionHeroMetadataContent }
            VStack(alignment: .leading, spacing: 8) { collectionHeroMetadataContent }
        }
        .font(.caption.weight(.medium))
        .textCase(.uppercase)
        .tracking(1)
        .foregroundStyle(.white.opacity(0.85))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var collectionHeroMetadataContent: some View {
        if let year = (detailItem ?? item).productionYear { Text(String(year)) }
        if let rating = (detailItem ?? item).officialRating, !rating.isEmpty { Text(rating) }
        if let count = (detailItem ?? item).childCount ?? (children.isEmpty ? nil : children.count) {
            let unit = item.type == "Series"
                ? (count == 1 ? "season" : "seasons")
                : (count == 1 ? "episode" : "episodes")
            Text("\(count) \(unit)")
        }
    }

    private var seasonSelector: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { seasonSelectorContent }
            VStack(alignment: .leading, spacing: 8) { seasonSelectorContent }
        }
    }

    @ViewBuilder private var seasonSelectorContent: some View {
        Menu {
            Picker("Season", selection: Binding(
                get: { selectedSeasonID },
                set: { id in
                    guard let season = children.first(where: { $0.id == id }),
                          id != selectedSeasonID else { return }
                    Task { await selectSeason(season) }
                }
            )) {
                ForEach(children) { season in
                    Text(season.name)
                        .tag(Optional(season.id))
                        .accessibilityIdentifier("videoSeason-\(season.id)")
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 8) {
                Text(children.first(where: { $0.id == selectedSeasonID })?.name ?? "Select season")
                    .fixedSize(horizontal: false, vertical: true)
                LuminaIcon(glyph: .chevronDown, size: 16)
            }
            .font(.headline)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading || isLoadingSelectedSeason)
        .accessibilityLabel("Season")
        .accessibilityValue(children.first(where: { $0.id == selectedSeasonID })?.name ?? "Select season")
        .accessibilityIdentifier("videoSeasonSelector")
    }

    @ViewBuilder private func episodeCards(_ episodes: [MediaItem]) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            LazyVStack(spacing: 12) {
                ForEach(episodes) { episode in
                    episodeLink(episode)
                }
            }
        } else {
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 12) {
                        ForEach(episodes) { episode in
                            episodeLink(episode)
                                .frame(width: episodeCardWidth)
                        }
                    }
                    .scrollTargetLayout()
                }
                .onAppear { alignInitialEpisode(using: proxy) }
                .onChange(of: episodeRailWidth) { alignInitialEpisode(using: proxy) }
                .scrollTargetBehavior(.viewAligned)
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { episodeRailWidth = $0 }
                // Leave room for the final card to settle at the same content gutter.
                .contentMargins(.trailing, max(0, episodeRailWidth - episodeCardWidth - episodeRailInset), for: .scrollContent)
                .modifier(LuminaCarouselEdges(inset: episodeRailInset))
            }
        }
    }

    private var episodeRailInset: CGFloat { LuminaTheme.contentGutter }
    private var episodeCardWidth: CGFloat {
        let width = max(0, episodeRailWidth - 2 * episodeRailInset)
        return width > 0 ? min(max(340, width * 0.30), width * 0.86) : 340
    }

    private func collectionPlayButton(identifier: String) -> some View {
        Button {
            preparePlayback()
        } label: {
            Label(isPreparingPlayback ? "Preparing…" : hasResumeEpisode ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 24)
        }
        .fixedSize(horizontal: true, vertical: false)
        .buttonStyle(LuminaQuietActionStyle(primary: true))
        .disabled(isPreparingPlayback || isLoading || isLoadingSelectedSeason)
        .accessibilityIdentifier(identifier)
    }

    @ViewBuilder
    private var playbackActions: some View {
        collectionPlayButton(identifier: "playButton")
        NativeWatchlistButton(item: item, store: store)
            .buttonStyle(LuminaMediaActionStyle(primary: false))
            .accessibilityIdentifier("videoWatchlistButton")
    }

    private func episodeLink(_ episode: MediaItem) -> some View {
        Button {
            guard !isPreparingPlayback else { return }
            selectedEpisodeID = episode.id
            preparePlayback(for: episode)
        } label: {
            episodeCard(episode, selected: episode.id == selectedEpisodeID)
        }
        .buttonStyle(.plain)
        .disabled(isPreparingPlayback || isLoading || isLoadingSelectedSeason)
        .contextMenu { NativeMetadataActions(item: episode) }
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(mediaAccessibilityLabel(for: episode) + (episode.userData?.played == true ? ", Watched" : episode.resumeSeconds > 0 ? "" : ", Unwatched"))
        .accessibilityHint(episode.resumeSeconds > 5 && episode.userData?.played != true ? "Resumes this episode" : "Plays this episode")
        .accessibilityValue(episode.id == selectedEpisodeID ? "Selected" : "")
        .accessibilityAddTraits(episode.id == selectedEpisodeID ? .isSelected : [])
        .accessibilityIdentifier("videoEpisode-\(episode.id)")
        .id(episodeScrollID(episode.id))
    }

    @ViewBuilder
    private var collectionAbout: some View {
        VStack(alignment: .leading, spacing: 12) {
            if isSeriesCollection,
               let episode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
                Text("About this episode")
                    .font(.title3.bold()).accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("episodeAboutHeading")
                if let overview = episode.overview, !overview.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    NativeMediaAbout(overview: MediaOverview(overview))
                        .id(episode.id)
                        .accessibilityIdentifier("episodeAbout")
                } else {
                    Text("An episode synopsis is not available yet.")
                        .font(.body).foregroundStyle(.secondary)
                }
                if let overview = (detailItem ?? item).overview, !overview.isEmpty {
                    DisclosureGroup {
                        NativeMediaAbout(overview: MediaOverview(overview))
                            .padding(.top, 12)
                            .accessibilityIdentifier("seriesAbout")
                    } label: {
                        Text("About the series").font(.headline)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .accessibilityIdentifier("seriesAboutToggle")
                    }
                }
            } else if let overview = (detailItem ?? item).overview, !overview.isEmpty {
                Text("About").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                NativeMediaAbout(overview: MediaOverview(overview))
            }
        }
        .tint(LuminaTheme.ink)
        .frame(maxWidth: 600, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("collectionAbout")
    }

    private func episodeCard(_ episode: MediaItem, selected: Bool = false) -> some View {
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
                        Label("Watched", glyph: .check)
                            .labelStyle(.iconOnly)
                            .foregroundStyle(LuminaTheme.accentInk)
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.88))
                Text(episode.name)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                if let overview = episode.overview,
                   let synopsis = MediaOverview(overview).synopsis.first?.text {
                    Text(synopsis)
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
                    .tint(LuminaTheme.brand)
                    .frame(height: 3)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 5)
                    .frame(maxWidth: .infinity, alignment: .bottom)
            }
        }
        .overlay(alignment: .topTrailing) {
            UnwatchedEpisodeCorner(episode: episode)
        }
        .clipShape(.rect(cornerRadius: 8))
        .overlay {
            if selected { RoundedRectangle(cornerRadius: 8).strokeBorder(LuminaTheme.brand, lineWidth: 3) }
        }
        .contentShape(Rectangle())
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        detailItem = try? await store.loadItem(item)
        do {
            children = try await store.loadChildren(of: item)
            if isSeriesCollection {
                let resume = store.resumeItems.first {
                    $0.type == "Episode" && $0.seriesID == item.id && $0.userData?.played != true
                }
                if let season = children.first(where: { $0.id == selectedSeasonID })
                    ?? children.first(where: { $0.id == resume?.seasonID })
                    ?? children.first {
                    if selectedEpisodeID == nil, season.id == resume?.seasonID {
                        selectedEpisodeID = resume?.id
                    }
                    await selectSeason(season)
                }
            }
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This collection could not load. Check your connection and try again."
        }
        isLoading = false
    }

    private func selectSeason(_ season: MediaItem) async {
        guard !isLoadingSelectedSeason else { return }
        isLoadingSelectedSeason = true
        defer { isLoadingSelectedSeason = false }
        do {
            let episodes = try await store.loadChildren(of: season)
            selectedSeasonID = season.id
            selectedSeasonEpisodes = episodes
            selectedEpisodeID = episodes.contains(where: { $0.id == selectedEpisodeID })
                ? selectedEpisodeID
                : episodes.first?.id
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This season could not load. Check your connection and try again."
        }
    }

    private func episodeScrollID(_ episodeID: String) -> String {
        "selected-episode-\(episodeID)"
    }

    private var initialEpisodeSectionID: String { "initial-episode-section" }

    private func positionInitialEpisode(using proxy: ScrollViewProxy) {
        guard isSeriesCollection, initialEpisodeID != nil, !hasPositionedInitialEpisode,
              let selectedEpisodeID,
              selectedSeasonEpisodes.contains(where: { $0.id == selectedEpisodeID }) else { return }
        hasPositionedInitialEpisode = true
        DispatchQueue.main.async {
            proxy.scrollTo(initialEpisodeSectionID, anchor: .top)
        }
    }

    private func alignInitialEpisode(using proxy: ScrollViewProxy) {
        guard isSeriesCollection, initialEpisodeID != nil, episodeRailWidth > 0, !hasAlignedInitialEpisode,
              let selectedEpisodeID,
              selectedSeasonEpisodes.contains(where: { $0.id == selectedEpisodeID }) else { return }
        hasAlignedInitialEpisode = true
        DispatchQueue.main.async {
            proxy.scrollTo(episodeScrollID(selectedEpisodeID), anchor: .leading)
        }
    }

    private func preparePlayback(for episode: MediaItem? = nil) {
        guard !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            defer { isPreparingPlayback = false }
            do {
                let target: MediaItem
                if let episode { target = episode } else { target = try await playableEpisode() }
                playbackRequest = try await store.preparePlayback(for: target)
            } catch {
                playbackError = (error as? LocalizedError)?.errorDescription ?? "No playable episode is available yet. Check the collection and try again."
            }
        }
    }

    private func playableEpisode() async throws -> MediaItem {
        if isSeriesCollection,
           let selectedEpisode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
            return selectedEpisode
        }
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

@MainActor
private struct EpisodeDownloadPicker: View {
    let store: LuminaStore
    let seasons: [MediaItem]
    @Environment(\.dismiss) private var dismiss
    @State private var seasonID: String
    @State private var episodesBySeason: [String: [MediaItem]]
    @State private var selectedIDs = Set<String>()
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var retry = 0

    init(store: LuminaStore, seasons: [MediaItem], initialSeasonID: String?, initialEpisodes: [MediaItem]) {
        self.store = store
        self.seasons = seasons
        let season = seasons.first { $0.id == initialSeasonID } ?? seasons.first
        _seasonID = State(initialValue: season?.id ?? "")
        _episodesBySeason = State(initialValue: season?.id == initialSeasonID
            ? [season?.id ?? "": initialEpisodes] : [:])
    }

    private var episodes: [MediaItem] { episodesBySeason[seasonID] ?? [] }
    private var selectableIDs: Set<String> { Set(episodes.filter(canDownload).map(\.id)) }
    private var selectedEpisodes: [MediaItem] {
        seasons.flatMap { episodesBySeason[$0.id] ?? [] }
            .filter { selectedIDs.contains($0.id) && canDownload($0) }
    }
    private var wholeSeasonSelected: Bool { !selectableIDs.isEmpty && selectableIDs.isSubset(of: selectedIDs) }

    var body: some View {
        NavigationStack {
            List {
                if seasons.count > 1 {
                    Section {
                        Picker("Season", selection: $seasonID) {
                            ForEach(seasons) { season in Text(season.name).tag(season.id) }
                        }
                        .pickerStyle(.menu)
                        .accessibilityIdentifier("episodeDownloadSeasonPicker")
                    }
                    .listRowBackground(LuminaTheme.paper)
                }
                Section {
                    if isLoading {
                        ProgressView("Loading episodes…")
                    } else if let errorMessage {
                        InlineErrorView(message: errorMessage) { retry += 1 }
                    } else if episodes.isEmpty {
                        Text("No episodes are available in this season.").foregroundStyle(LuminaTheme.muted)
                    } else {
                        ForEach(episodes) { episode in
                            Button {
                                if !selectedIDs.insert(episode.id).inserted { selectedIDs.remove(episode.id) }
                            } label: {
                                HStack(spacing: 16) {
                                    ZStack {
                                        Circle().fill(selectedIDs.contains(episode.id) ? LuminaTheme.primaryControlFill : .clear)
                                        Circle().strokeBorder(LuminaTheme.muted, lineWidth: selectedIDs.contains(episode.id) ? 0 : 1.5)
                                        if selectedIDs.contains(episode.id) {
                                            LuminaIcon(glyph: .check, size: 16)
                                                .foregroundStyle(LuminaTheme.onPrimaryControl)
                                        }
                                    }
                                    .frame(width: 24, height: 24)
                                    .accessibilityHidden(true)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(episode.name).font(.body)
                                        if !episode.displaySubtitle.isEmpty {
                                            Text(episode.displaySubtitle).font(.caption).foregroundStyle(LuminaTheme.muted)
                                        }
                                        if let status = status(for: episode) {
                                            Text(status).font(.caption).foregroundStyle(LuminaTheme.muted)
                                        }
                                    }
                                    Spacer(minLength: 0)
                                }
                                .frame(minHeight: 44)
                                .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            .disabled(!canDownload(episode))
                            .accessibilityAddTraits(selectedIDs.contains(episode.id) ? .isSelected : [])
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("episodeDownload-\(episode.id)")
                        }
                    }
                } header: {
                    HStack {
                        Text(seasons.first { $0.id == seasonID }?.name ?? "Episodes")
                            .font(LuminaTheme.heading(26, relativeTo: .title2))
                            .foregroundStyle(LuminaTheme.ink)
                        Spacer()
                        Button(wholeSeasonSelected ? "Deselect season" : "Select whole season") {
                            if wholeSeasonSelected { selectedIDs.subtract(selectableIDs) }
                            else { selectedIDs.formUnion(selectableIDs) }
                        }
                        .buttonStyle(LuminaQuietActionStyle())
                        .disabled(isLoading || selectableIDs.isEmpty)
                        .accessibilityIdentifier("selectDownloadSeasonButton")
                    }
                } footer: {
                    Text("Selections are kept when you change seasons. Downloaded and queued episodes are already saved or in progress.")
                        .foregroundStyle(LuminaTheme.muted)
                }
                .listRowBackground(LuminaTheme.paper)
                Section {
                    Text("\(selectedEpisodes.count) selected")
                        .accessibilityIdentifier("episodeDownloadSelectionCount")
                    Text("Videos are saved at original quality. Manage progress, cancellation and storage in Downloads.")
                        .font(.footnote).foregroundStyle(LuminaTheme.muted)
                }
                .listRowBackground(LuminaTheme.paper)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(LuminaTheme.paper.ignoresSafeArea())
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Episodes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.accessibilityIdentifier("cancelEpisodeDownloadsButton")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Download (\(selectedEpisodes.count))") {
                        let selected = selectedEpisodes
                        Task { await store.downloadVideos(items: selected) }
                        dismiss()
                    }
                    .disabled(selectedEpisodes.isEmpty || store.isOffline)
                    .accessibilityIdentifier("confirmEpisodeDownloadsButton")
                }
            }
            .task(id: "\(seasonID):\(retry)") { await loadSeason() }
            .accessibilityIdentifier("episodeDownloadPicker")
        }
        .tint(LuminaTheme.ink)
        .presentationBackground(LuminaTheme.paper)
    }

    private func canDownload(_ episode: MediaItem) -> Bool {
        guard LuminaOfflineLibrary.supportsVideoDownload(episode) else { return false }
        switch store.offline.downloadStates[episode.id] {
        case .downloaded, .queued, .downloading: return false
        default: return true
        }
    }

    private func status(for episode: MediaItem) -> String? {
        switch store.offline.downloadStates[episode.id] {
        case .downloaded: return "On this device"
        case .queued: return "Queued"
        case .downloading: return "Downloading…"
        case .failed(let message): return message
        default: return LuminaOfflineLibrary.supportsVideoDownload(episode) ? nil : "Unavailable to download"
        }
    }

    private func loadSeason() async {
        store.offline.refresh()
        errorMessage = nil
        guard episodesBySeason[seasonID] == nil,
              let season = seasons.first(where: { $0.id == seasonID }) else { isLoading = false; return }
        let requestedID = seasonID
        isLoading = true
        defer { if seasonID == requestedID { isLoading = false } }
        do {
            let loaded = try await store.loadChildren(of: season)
            guard !Task.isCancelled, seasonID == requestedID else { return }
            episodesBySeason[requestedID] = loaded
        } catch {
            guard !Task.isCancelled, seasonID == requestedID else { return }
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This season could not load. Try again."
        }
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
