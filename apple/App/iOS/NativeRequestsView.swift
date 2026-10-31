import SwiftUI
import LuminaCore

/// Catalog artwork stays continuous behind the system navigation rail.
private struct RequestArtwork: View {
    let url: URL?
    var fit = false
    var home = false
    var catalog: LuminaRequests?

    var body: some View {
        Color.black.overlay {
            SubjectAwareArtwork(url: url, fit: fit, home: home) { source in
                await catalog?.artworkFraming(for: source)
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

@MainActor
struct NativeRequestsView: View {
    let store: LuminaStore
    let session: LuminaSession
    var isActive: Bool
    var usesPortraitNavigationRail: Bool
    @State private var catalog: LuminaRequests
    @State private var selected: RequestCatalogItem?
    @State private var myRequestsPresented = false
    @State private var search = ""
    @State private var searchVisible = false
    @State private var searchResults: [RequestCatalogItem]?
    @State private var searchError: String?
    @State private var kind: RequestCatalogKind?
    @State private var section = "trending"
    @State private var genre: String?
    @State private var genres: [RequestCatalogGenre] = []
    @State private var resultPage = 0
    @State private var totalPages = 0
    @State private var loadingResults = false
    @State private var resultGeneration = UUID()
    @State private var featuredTrailer: URL?
    @State private var trailerItemID: String?
    @Environment(\.horizontalSizeClass) private var widthClass
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.openURL) private var openURL

    init(store: LuminaStore, session: LuminaSession, isActive: Bool, usesPortraitNavigationRail: Bool = false) {
        self.store = store
        self.session = session
        self.isActive = isActive
        self.usesPortraitNavigationRail = usesPortraitNavigationRail
        _catalog = State(initialValue: LuminaRequests(session: session))
    }

    private var items: [RequestCatalogItem] {
        var seen = Set<String>()
        let source = searchResults ?? (browsingResults ? [] : ((catalog.home?.hero ?? []) + (catalog.home?.rails.flatMap(\.items) ?? [])))
        return source.filter { (!query.isEmpty || kind == nil || $0.kind == kind) && seen.insert($0.id).inserted }
    }
    private var featured: RequestCatalogItem? { items.first }
    private var compact: Bool { widthClass != .regular }
    private var query: String { search.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var browsingResults: Bool { !query.isEmpty || kind != nil }
    private var queryKey: String { query.isEmpty ? "browse|\(kind?.rawValue ?? "")|\(section)|\(genre ?? "")" : "search|\(query)" }
    private var sections: [(String, String)] {
        switch kind {
        case .anime: [("trending", "Trending"), ("this_season", "This season"), ("next_season", "Next season"), ("popular", "Popular"), ("top", "Top rated"), ("movies", "Films")]
        case .show: [("trending", "Trending"), ("popular", "Popular"), ("on_the_air", "On the air"), ("airing_today", "Airing today"), ("top_rated", "Top rated")]
        default: [("trending", "Trending"), ("popular", "Popular"), ("upcoming", "Upcoming"), ("now_playing", "In cinemas"), ("top_rated", "Top rated")]
        }
    }

    var body: some View {
        GeometryReader { geometry in
            Group {
                if catalog.isLoading && catalog.home == nil {
                    ProgressView("Finding your next watch…").frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if items.isEmpty && !browsingResults {
                    ContentUnavailableView {
                        Label(search.isEmpty ? "Discover something new" : "No matching titles", glyph: .discover)
                    } description: {
                        Text(searchError ?? catalog.error?.localizedDescription ?? "Titles will appear here when your household’s Requests catalog is available.")
                    } actions: {
                        Button("Try again") { Task { await catalog.loadHome() } }
                    }
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 28) {
                            if browsingResults {
                                if query.isEmpty, let kind {
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text(kind == .movie ? "Films" : kind == .show ? "Series" : "Anime").font(.title2.bold())
                                        Text([sections.first { $0.0 == section }?.1, genres.first { $0.id == genre }?.name].compactMap { $0 }.joined(separator: " · "))
                                            .font(.subheadline).foregroundStyle(.secondary)
                                    }
                                    .padding(.horizontal, 20)
                                    .padding(.trailing, usesPortraitNavigationRail ? 76 : 0)
                                    .padding(.top, 12)
                                }
                                if loadingResults && resultPage == 0 { ProgressView("Searching the catalog…").padding(24) }
                                if items.isEmpty && !loadingResults {
                                    if query.isEmpty {
                                        ContentUnavailableView {
                                            Label("No titles in this collection", glyph: .movies)
                                        } description: {
                                            Text("Try another genre or collection in Requests options.")
                                        }
                                    } else { ContentUnavailableView.search(text: query) }
                                }
                                LazyVGrid(columns: [GridItem(.adaptive(minimum: compact ? 130 : 180), spacing: 14)], alignment: .leading, spacing: 20) {
                                    ForEach(items) { item in
                                        VStack(alignment: .leading, spacing: 8) {
                                            poster(item).aspectRatio(2 / 3, contentMode: .fit)
                                            Text(item.title).font(.subheadline.weight(.semibold)).lineLimit(2)
                                                .accessibilityHidden(true)
                                            if let year = item.year {
                                                Text(String(year)).font(.caption).foregroundStyle(.secondary).accessibilityHidden(true)
                                            }
                                        }
                                    }
                                }
                                .padding(.horizontal, 20)
                                .padding(.trailing, usesPortraitNavigationRail ? 76 : 0)
                                .padding(.top, 12)
                                if let searchError {
                                    Text(searchError).foregroundStyle(.secondary).padding(.horizontal, 20)
                                    Button("Try again") { Task { await loadResults(reset: resultPage == 0) } }.padding(.horizontal, 20)
                                }
                                if resultPage < totalPages {
                                    Button { Task { await loadResults(reset: false) } } label: {
                                        if loadingResults { ProgressView() } else { Text("Load more titles") }
                                    }
                                    .disabled(loadingResults).padding(20)
                                    .accessibilityIdentifier("requestsLoadMoreButton")
                                }
                            } else {
                                if let item = featured {
                                    feature(item)
                                        .frame(minHeight: typeSize.isAccessibilitySize ? 680 : min(720, max(430, geometry.size.height * (compact ? 0.75 : 0.72))))
                                        .accessibilityElement(children: .contain)
                                        .accessibilityIdentifier("requestsFeaturedHero")
                                }
                                ForEach(catalog.home?.rails ?? [], id: \.id) { rail in
                                    if !rail.items.isEmpty { posterRail(rail.title, items: rail.items) }
                                }
                            }
                        }.padding(.bottom, 32)
                    }
                    .ignoresSafeArea(.container, edges: usesPortraitNavigationRail && !browsingResults && !searchVisible ? .top : [])
                    .scrollClipDisabled()
                    .modifier(HomeHeroScrollEdge(isEnabled: usesPortraitNavigationRail && !browsingResults))
                    .scrollDismissesKeyboard(.interactively)
                    .refreshable {
                        if browsingResults { await loadResults(reset: true) } else { await catalog.loadHome() }
                        await catalog.loadQuotas()
                    }
                }
            }
        }
        .background(.black)
        .foregroundStyle(.white)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            if !usesPortraitNavigationRail {
                ToolbarItem(placement: .topBarLeading) { searchButton.labelStyle(.titleAndIcon) }
                ToolbarItem(placement: .topBarTrailing) { optionsMenu }
            }
        }
        .portraitNavigationRail(isEnabled: usesPortraitNavigationRail, store: store) {
            searchButton
            optionsMenu
        }
        .modifier(NativeSearchPresentation(text: $search, isPresented: $searchVisible,
            prompt: "Search all films, series, and anime", usesInlineSearch: usesPortraitNavigationRail))
        .accessibilityElement(children: .contain)
        .ignoresSafeArea(.container, edges: usesPortraitNavigationRail && !browsingResults && !searchVisible ? [.top, .horizontal] : .horizontal)
        .accessibilityIdentifier("requestsScreen")
        .sheet(item: $selected) { RequestDetailSheet(item: $0, store: store, catalog: catalog) }
        .sheet(isPresented: $myRequestsPresented) { RequestHistorySheet(catalog: catalog) }
        .task { if catalog.home == nil { await catalog.loadHome() }; await catalog.loadQuotas() }
        .task(id: queryKey) {
            resultGeneration = UUID()
            searchResults = browsingResults ? [] : nil; searchError = nil; resultPage = 0; totalPages = 0; loadingResults = browsingResults
            guard !query.isEmpty || kind != nil else { return }
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            await loadResults(reset: true)
        }
        .task(id: kind) {
            genres = []
            guard let kind else { return }
            let values = try? await catalog.genres(kind)
            guard !Task.isCancelled else { return }
            genres = values ?? []
        }
        .onChange(of: kind) { _, _ in section = "trending"; genre = nil }
        .task(id: "\(featured?.id ?? "")|\(isActive)") {
            if trailerItemID != featured?.id {
                featuredTrailer = nil
                trailerItemID = nil
            }
            guard isActive, let item = featured, let id = item.catalogID, trailerItemID != item.id else { return }
            guard let detail = try? await catalog.detail(kind: item.kind, id: id), !Task.isCancelled else { return }
            featuredTrailer = detail.trailers.compactMap(\.youtubeURL).first
            trailerItemID = item.id
        }
    }

    private var searchButton: some View {
        Button("Search requests", glyph: .search) {
            searchVisible.toggle()
        }
        .accessibilityIdentifier("requestsSearchButton")
    }

    private var optionsMenu: some View {
        Menu {
            if query.isEmpty {
                Picker("Browse", selection: $kind) {
                    Text("Everything").tag(nil as RequestCatalogKind?)
                    ForEach([RequestCatalogKind.movie, .show, .anime], id: \.self) { value in
                        Text(value == .movie ? "Films" : value == .show ? "Series" : "Anime").tag(Optional(value))
                    }
                }
            }
            if kind != nil && query.isEmpty {
                Picker("Discover", selection: $section) {
                    ForEach(sections, id: \.0) { Text($0.1).tag($0.0) }
                }
                if !genres.isEmpty {
                    Picker("Genre", selection: $genre) {
                        Text("All genres").tag(nil as String?)
                        ForEach(genres) { Text($0.name).tag(Optional($0.id)) }
                    }
                }
            }
            if kind != nil || genre != nil {
                Button("Clear filters", glyph: .filter) {
                    kind = nil; genre = nil; section = "trending"
                }
            }
            Divider()
            Button("My requests", glyph: .history) { myRequestsPresented = true }
                .accessibilityIdentifier("requestsMyListButton")
        } label: { Label("Requests options", glyph: .moreHorizontal) }
        .accessibilityIdentifier("requestsOptionsMenu")
    }

    private func loadResults(reset: Bool) async {
        let key = queryKey
        guard reset || !loadingResults else { return }
        if reset { resultGeneration = UUID() }
        let generation = resultGeneration
        loadingResults = true
        defer { if generation == resultGeneration && key == queryKey { loadingResults = false } }
        let next = reset ? 1 : resultPage + 1
        do {
            let page: RequestCatalogPage
            if !query.isEmpty { page = try await catalog.search(query, page: next) }
            else if let kind { page = try await catalog.list(kind, section: section, genre: genre, page: next) }
            else { return }
            guard !Task.isCancelled, key == queryKey, generation == resultGeneration else { return }
            searchResults = (reset ? [] : (searchResults ?? [])) + page.items
            resultPage = page.page; totalPages = min(page.totalPages, 500); searchError = nil
        } catch {
            guard !Task.isCancelled, key == queryKey, generation == resultGeneration else { return }
            if searchResults == nil { searchResults = [] }
            searchError = "The catalog couldn’t load. Try again."
        }
    }

    private func feature(_ item: RequestCatalogItem) -> some View {
        ZStack(alignment: .bottomLeading) {
            RequestArtwork(url: item.backdropURL ?? item.posterURL, home: true, catalog: catalog)
                .ignoresSafeArea(.container, edges: [.top, .horizontal])
            LinearGradient(colors: [.clear, .black.opacity(0.2), .black], startPoint: .center, endPoint: .bottom)
                .allowsHitTesting(false)
            VStack(alignment: .leading, spacing: 14) {
                Button { searchVisible = false; selected = item } label: {
                    MediaTitleArtwork(name: item.title, url: item.logoURL,
                                      maximumWidth: compact ? 280 : 440,
                                      maximumHeight: compact ? 88 : 140) {
                        Text(item.title)
                            .font(LuminaTheme.editorial(compact ? 32 : 46, relativeTo: .largeTitle, italic: true))
                            .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                    }
                }.buttonStyle(.plain).accessibilityLabel(item.title).accessibilityIdentifier("requestDetails-\(item.id)")
                Text(metadata(item)).font(.subheadline).foregroundStyle(.white.opacity(0.8))
                if let overview = item.overview, !overview.isEmpty {
                    Text(overview).font(.body).lineLimit(3).frame(maxWidth: 580, alignment: .leading)
                }
                HStack(spacing: 16) {
                    let hasVaultItem = item.status.libraryTitleID?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
                    Button { searchVisible = false; selected = item } label: {
                        Label(hasVaultItem ? "View" : ((item.canRequest && item.status.canCreateRequest) ? "Request" : (item.status.label.isEmpty ? "Details" : item.status.label)),
                              glyph: hasVaultItem ? .library : ((item.canRequest && item.status.canCreateRequest) ? .add : .info))
                            .font(.headline).padding(.horizontal, 20).padding(.vertical, 14)
                            .foregroundStyle(.black).background(.white, in: .capsule)
                    }.buttonStyle(.plain).accessibilityIdentifier("requestPrimary-\(item.id)")
                    if let featuredTrailer {
                        Button { openURL(featuredTrailer) } label: {
                            Label("Trailer", glyph: .externalLink).font(.headline).padding(.vertical, 14)
                        }.buttonStyle(.plain).accessibilityLabel("Watch trailer on YouTube")
                    }
                }
            }.padding(LuminaTheme.contentGutter)
        }.background(.black)
    }

    private func poster(_ item: RequestCatalogItem) -> some View {
        Button { searchVisible = false; selected = item } label: {
            RequestArtwork(url: item.posterURL ?? item.backdropURL, catalog: catalog)
                .clipShape(.rect(cornerRadius: 8))
                .overlay(alignment: .bottomLeading) {
                    if item.status.state != .none {
                        Text(item.status.label).font(.caption.weight(.semibold))
                            .padding(8).background(.black.opacity(0.8), in: .rect(topTrailingRadius: 8))
                    }
                }
                .contentShape(.rect)
        }.buttonStyle(.plain).accessibilityLabel(item.title)
            .accessibilityIdentifier("requestPoster-\(item.id)")
    }

    private func posterRail(_ title: String, items: [RequestCatalogItem]) -> some View {
        let posterWidth: CGFloat = compact ? 144 : 176
        let posterHeight = posterWidth * 1.5

        return VStack(alignment: .leading, spacing: 14) {
            Text(title).font(.title2.weight(.bold)).padding(.horizontal, 20)
            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 12) {
                    ForEach(items) { item in
                        poster(item)
                            .frame(width: posterWidth, height: posterHeight, alignment: .top)
                    }
                }
                .padding(.horizontal, 20)
            }
            .scrollIndicators(.hidden)
            .modifier(LuminaCarouselEdges(inset: 0))
        }
    }

    private func metadata(_ item: RequestCatalogItem) -> String {
        ([item.year.map(String.init)] + [item.genres.prefix(2).joined(separator: " · ")])
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

@MainActor
private struct RequestDetailSheet: View {
    let item: RequestCatalogItem
    let store: LuminaStore
    @Bindable var catalog: LuminaRequests
    @State private var detail: RequestCatalogDetail?
    @State private var loading = true
    @State private var problem: String?
    @State private var submitting = false
    @State private var created: RequestRecord?
    @State private var recommendation: RequestCatalogItem?
    @State private var seasonMode = "all"
    @State private var pickedSeasons = Set<Int>()
    @State private var language: RequestLanguage?
    @State private var languageRequired = false
    @State private var vaultItem: MediaItem?
    @State private var openingVault = false
    @State private var vaultProblem: String?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.dynamicTypeSize) private var typeSize

    private var current: RequestCatalogItem { detail?.item ?? item }
    private var quota: RequestQuota? { catalog.quotas.first { $0.kind == current.kind } }
    private var vaultID: String? {
        guard let id = current.status.libraryTitleID?.trimmingCharacters(in: .whitespacesAndNewlines), !id.isEmpty else { return nil }
        return id
    }
    private var series: Bool { current.mediaType == .tv }
    private var canRequestMore: Bool { vaultID != nil && series && current.status.state == .partiallyAvailable }
    private var asksLanguage: Bool { (current.kind == .anime && series) || languageRequired }
    private var seasons: RequestSeasonSelection? {
        guard series else { return nil }
        if seasonMode == "choose" { return pickedSeasons.isEmpty ? nil : .selected(pickedSeasons.sorted()) }
        if seasonMode == "latest" {
            guard let latest = detail?.seasons.map(\.number).filter({ $0 > 0 }).max() else { return nil }
            return .selected([latest])
        }
        return .all
    }
    private var canSubmit: Bool {
        (vaultID == nil || canRequestMore) && !loading && !submitting && created == nil && current.canRequest && current.status.canCreateRequest
        && quota?.canRequest != false && quota?.remaining != 0
        && (!series || seasons != nil) && (!asksLanguage || language != nil)
    }

    var body: some View {
        NavigationStack {
            GeometryReader { geometry in
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        ZStack(alignment: .bottomLeading) {
                            RequestArtwork(url: current.backdropURL ?? current.posterURL, catalog: catalog)
                            LinearGradient(colors: [.clear, .black.opacity(0.9)], startPoint: .center, endPoint: .bottom)
                            MediaTitleArtwork(name: current.title, url: current.logoURL) {
                                Text(current.title)
                                    .font(LuminaTheme.editorial(38, relativeTo: .largeTitle, italic: true))
                            }
                            .padding(.vertical, 24)
                            .padding(.leading, 24 + geometry.safeAreaInsets.leading)
                            .padding(.trailing, 24 + geometry.safeAreaInsets.trailing)
                        }
                        .frame(height: typeSize.isAccessibilitySize ? 380 : 300)
                        VStack(alignment: .leading, spacing: 22) {
                            HStack(spacing: 12) {
                                if let year = current.year { Text(String(year)) }
                                if let runtime = detail?.runtime { Text("\(runtime) min") }
                                if let rating = current.rating {
                                    Label(rating.formatted(.number.precision(.fractionLength(1))), glyph: .ratingStar, variant: .solid)
                                }
                            }.font(.subheadline).foregroundStyle(.secondary)
                            if let tagline = detail?.tagline, !tagline.isEmpty { Text(tagline).font(.headline) }
                            if let overview = current.overview { Text(overview).font(.body) }
                            if !current.genres.isEmpty {
                                Text(current.genres.joined(separator: " · ")).font(.subheadline).foregroundStyle(.secondary)
                            }
                            if let trailerURL = detail?.trailers.compactMap(\.youtubeURL).first {
                                Button { openURL(trailerURL) } label: {
                                    Label("Watch trailer on YouTube", glyph: .externalLink)
                                }
                                .buttonStyle(.plain).font(.headline)
                                .accessibilityIdentifier("requestYouTubeTrailerButton")
                            }
                            if loading { ProgressView("Loading title details…") }
                            else if vaultID != nil {
                                vaultAction
                                if canRequestMore {
                                    if let created {
                                        Label(created.status.label, glyph: .check)
                                            .font(.title3.weight(.semibold)).foregroundStyle(LuminaTheme.accentInk)
                                            .accessibilityIdentifier("requestSubmittedStatus")
                                        Text(created.status == .pending ? "Your household’s administrator will review this request." : "Your request is saved. Follow its progress in My requests.")
                                            .foregroundStyle(.secondary)
                                    } else if current.canRequest && current.status.canCreateRequest {
                                        requestChoices
                                    }
                                }
                            } else if let created {
                                Label(created.status.label, glyph: .check)
                                    .font(.title3.weight(.semibold)).foregroundStyle(LuminaTheme.accentInk)
                                    .accessibilityIdentifier("requestSubmittedStatus")
                                Text(created.status == .pending ? "Your household’s administrator will review this request." : "Your request is saved. Follow its progress in My requests.")
                                    .foregroundStyle(.secondary)
                            } else if current.canRequest && current.status.canCreateRequest {
                                requestChoices
                            } else {
                                Label(current.status.state == .none ? "Not available to request yet" : current.status.label, glyph: .info)
                                    .foregroundStyle(.secondary)
                            }
                            if let vaultProblem {
                                Text(vaultProblem).foregroundStyle(.red).accessibilityIdentifier("requestVaultLookupError")
                            }
                            if let problem {
                                Text(problem).foregroundStyle(.red).accessibilityIdentifier("requestSubmissionError")
                            }
                            if let cast = detail?.cast, !cast.isEmpty {
                                VStack(alignment: .leading, spacing: 10) {
                                    Text("Starring").font(.headline)
                                    Text(cast.prefix(8).map(\.name).joined(separator: ", ")).foregroundStyle(.secondary)
                                }
                            }
                            if let recommendations = detail?.recommendations, !recommendations.isEmpty {
                                VStack(alignment: .leading, spacing: 14) {
                                    Text("More like this").font(.title2.weight(.bold))
                                    ScrollView(.horizontal) {
                                        LazyHStack(spacing: 12) {
                                            ForEach(recommendations.prefix(12)) { entry in
                                                Button { recommendation = entry } label: {
                                                    RequestArtwork(url: entry.posterURL, catalog: catalog)
                                                        .frame(width: 128, height: 192).clipShape(.rect(cornerRadius: 8))
                                                }.accessibilityLabel(entry.title)
                                            }
                                        }
                                    }.scrollIndicators(.hidden)
                                    .modifier(LuminaCarouselEdges(inset: 24))
                                }
                            }
                        }
                        .padding(.leading, 24 + geometry.safeAreaInsets.leading)
                        .padding(.trailing, 24 + geometry.safeAreaInsets.trailing)
                        .padding(.bottom, 32)
                    }
                }
                .ignoresSafeArea(.container, edges: [.top, .horizontal])
                .scrollClipDisabled()
                .background(.black).foregroundStyle(.white)
                .navigationTitle("")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbarColorScheme(.dark, for: .navigationBar)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done", glyph: .close) { dismiss() }
                            .labelStyle(.iconOnly).accessibilityIdentifier("requestDetailsCloseButton")
                    }
                }
                .task { await load() }
                .navigationDestination(item: $vaultItem) { item in
                    NativeRequestVaultDestination(item: item, store: store)
                }
                .sheet(item: $recommendation) { entry in
                    RequestDetailSheet(item: entry, store: store, catalog: catalog)
                }
                .accessibilityIdentifier("requestDetailScreen")
            }
        }
        .preferredColorScheme(.dark)
    }

    private var requestChoices: some View {
        VStack(alignment: .leading, spacing: 20) {
            if series {
                Picker("Seasons", selection: $seasonMode) {
                    Text("All seasons").tag("all")
                    if detail?.seasons.contains(where: { $0.number > 0 }) == true {
                        Text("Latest season").tag("latest")
                    }
                    if detail?.seasons.isEmpty == false { Text("Choose seasons").tag("choose") }
                }
                .accessibilityIdentifier("requestSeasonPicker")
                if seasonMode == "choose" {
                    ForEach((detail?.seasons ?? []).sorted { ($0.number == 0 ? Int.max : $0.number) < ($1.number == 0 ? Int.max : $1.number) }) { season in
                        Toggle(isOn: Binding(get: { pickedSeasons.contains(season.number) }, set: { value in
                            if value { pickedSeasons.insert(season.number) } else { pickedSeasons.remove(season.number) }
                        })) {
                            VStack(alignment: .leading) {
                                Text(season.name)
                                Text("\(season.episodeCount) episodes").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .tint(LuminaTheme.brand)
                        .accessibilityIdentifier("requestSeason-\(season.number)")
                    }
                }
            }
            if asksLanguage {
                Picker("Audio language", selection: $language) {
                    Text("Choose language").tag(nil as RequestLanguage?)
                    Text("English dub").tag(Optional(RequestLanguage.dub))
                    Text("Japanese + subtitles").tag(Optional(RequestLanguage.sub))
                }.accessibilityIdentifier("requestLanguagePicker")
            }
            if let quota {
                VStack(alignment: .leading, spacing: 6) {
                    if !quota.canRequest { Text("Your account can’t request this category. Ask an administrator.") }
                    else if let remaining = quota.remaining, let limit = quota.limit { Text("\(remaining) of \(limit) requests remaining") }
                    Text(quota.autoApprove ? "Requests are approved automatically." : "Requests need your administrator’s approval.")
                }.font(.subheadline).foregroundStyle(.secondary)
            }
            Button {
                Task { await submit() }
            } label: {
                HStack {
                    if submitting { ProgressView() }
                    Label(canRequestMore ? "Request more from \(current.title)" : "Request \(current.title)", glyph: .add)
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(LuminaMediaActionStyle(primary: true))
            .disabled(!canSubmit).accessibilityIdentifier("requestConfirmButton")
        }
    }

    @ViewBuilder private var vaultAction: some View {
        if let id = vaultID {
            Button {
                Task { await openVault(id: id) }
            } label: {
                HStack {
                    if openingVault { ProgressView() }
                    Label("View", glyph: .library)
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(LuminaMediaActionStyle(primary: false))
            .disabled(openingVault)
            .accessibilityIdentifier("requestViewVaultButton")
        }
    }

    private func openVault(id: String) async {
        guard !openingVault else { return }
        openingVault = true
        vaultProblem = nil
        defer { openingVault = false }
        do {
            vaultItem = try await store.libraryItem(id: id)
        } catch {
            vaultProblem = "This title is no longer available in your vault."
        }
    }

    private func load() async {
        guard let id = item.catalogID else { loading = false; problem = "This title hasn’t been matched yet."; return }
        do { detail = try await catalog.detail(kind: item.kind, id: id) }
        catch { problem = error.localizedDescription }
        await catalog.loadQuotas()
        loading = false
    }

    private func submit() async {
        guard canSubmit else { return }
        submitting = true; problem = nil
        defer { submitting = false }
        do {
            created = try await catalog.create(current, seasons: seasons, language: language)
            await catalog.loadQuotas()
            await catalog.loadHome()
        } catch {
            if case RequestAPIError.server(_, "language_required") = error { languageRequired = true }
            problem = error.localizedDescription
        }
    }
}

@MainActor
private struct NativeRequestVaultDestination: View {
    let item: MediaItem
    let store: LuminaStore

    var body: some View {
        if item.isCollection {
            VideoCollectionView(item: item, store: store)
        } else {
            TitleDetailView(item: item, store: store)
        }
    }
}

@MainActor
private struct RequestHistorySheet: View {
    @Bindable var catalog: LuminaRequests
    @State private var status: RequestState?
    @State private var page = 1
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let error = catalog.error { Text(error.localizedDescription).foregroundStyle(.red) }
                if let records = catalog.myRequests?.items, !records.isEmpty {
                    ForEach(records) { record in
                        HStack(alignment: .top, spacing: 16) {
                            RequestArtwork(url: record.posterURL, catalog: catalog).frame(width: 68, height: 102).clipShape(.rect(cornerRadius: 6))
                            VStack(alignment: .leading, spacing: 8) {
                                Text(record.title).font(.headline)
                                Text(record.status.label).foregroundStyle(LuminaTheme.accentInk)
                                if record.status == .processing {
                                    ProgressView(value: min(1, max(0, record.progress ?? 0))).tint(LuminaTheme.brand)
                                }
                                if let language = record.language {
                                    Text(language == .dub ? "English dub" : "Japanese + subtitles").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }.listRowBackground(Color.white.opacity(0.04))
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel(record.title)
                        .accessibilityValue([record.status.label, record.language.map { $0 == .dub ? "English dub" : "Japanese + subtitles" }].compactMap { $0 }.joined(separator: ", "))
                        .accessibilityIdentifier("requestHistory-\(record.key)")
                    }
                    if let result = catalog.myRequests, result.totalPages > 1 {
                        HStack {
                            Button("Previous") { page -= 1 }.disabled(page <= 1)
                            Spacer()
                            Text("\(page) of \(result.totalPages)").font(.caption)
                            Spacer()
                            Button("Next") { page += 1 }.disabled(page >= result.totalPages)
                        }.listRowBackground(Color.clear)
                    }
                } else if catalog.isLoading { ProgressView("Loading requests…") }
                else {
                    ContentUnavailableView {
                        Label("Your next watch starts here", glyph: .discover)
                    } description: {
                        Text("Titles you request will appear here with their progress.")
                    }
                }
            }
            .scrollContentBackground(.hidden).background(.black)
            .navigationTitle("My requests").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        Picker("Status", selection: $status) {
                            Text("All requests").tag(nil as RequestState?)
                            ForEach([RequestState.pending, .approved, .processing, .partiallyAvailable, .available, .declined, .failed], id: \.self) { value in
                                Text(value.label).tag(Optional(value))
                            }
                        }
                    } label: { Label("Filter requests", glyph: .filter) }
                }
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .task(id: "\(status?.rawValue ?? "all")|\(page)") { await catalog.loadMyRequests(status: status, page: page) }
            .onChange(of: status) { page = 1 }
            .refreshable { await catalog.loadMyRequests(status: status, page: page) }
            .accessibilityIdentifier("requestHistoryScreen")
        }.preferredColorScheme(.dark)
    }
}
