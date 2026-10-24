import SwiftUI
import LuminaCore

/// A focused catalog for titles that have not reached the household library yet.
///
/// The view deliberately uses the Requests service rather than the library store: a
/// request can be submitted only after the catalog has confirmed the title's current
/// status and the member's quota.
private enum TVRequestQuotaState: Equatable {
    case loading
    case ready
    case failed(String)
}

@MainActor
struct TVRequestsView: View {
    let store: LuminaStore
    var focusRequest = 0
    @FocusState private var focusedSearch: Bool
    @State private var catalog = LuminaRequests()
    @State private var search = ""
    @State private var searchResults: [RequestCatalogItem] = []
    @State private var searchPage = 0
    @State private var searchTotalPages = 1
    @State private var searchError: String?
    @State private var searching = false
    @State private var loadingMoreSearch = false
    @State private var searchRetry = 0
    @State private var browseKind: RequestCatalogKind?
    @State private var browseGenre = ""
    @State private var availableGenres: [RequestCatalogGenre] = []
    @State private var browseResults: [RequestCatalogItem] = []
    @State private var browsePage = 0
    @State private var browseTotalPages = 1
    @State private var browsing = false
    @State private var loadingMoreBrowse = false
    @State private var browseError: String?
    @State private var browseRetry = 0
    @State private var genreError: String?
    @State private var quotaState: TVRequestQuotaState = .loading
    @State private var historyPresented = false

    private var query: String { search.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var browseKey: String { "\(browseKind?.rawValue ?? "home")|\(browseGenre)|\(browseRetry)" }
    private var browseTitle: String { browseGenre.isEmpty ? (browseKind?.rawValue.capitalized ?? "Featured") : browseGenre }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 48) {
                controls

                if !query.isEmpty {
                    searchContent
                } else if browseKind != nil {
                    browseContent
                } else if catalog.isLoading, catalog.home == nil {
                    ProgressView("Finding something new…")
                        .frame(maxWidth: .infinity, minHeight: 460)
                } else if let home = catalog.home, let featured = home.hero.first {
                    TVRequestHero(item: featured, catalog: catalog, quotaState: quotaState, reloadQuotas: loadQuotas)
                        .focusSection()

                    ForEach(home.rails) { rail in
                        if !rail.items.isEmpty {
                            TVRequestRail(title: rail.title, items: rail.items, catalog: catalog, quotaState: quotaState, reloadQuotas: loadQuotas)
                                .focusSection()
                        }
                    }
                } else {
                    TVRequestMessage(
                        title: "Discover something new",
                        message: catalog.error?.localizedDescription ?? "Requests will appear here when the household catalog is available.",
                        actionTitle: "Try again"
                    ) {
                        Task {
                            await catalog.loadHome()
                            await loadQuotas()
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 460)
                }
            }
            .padding(.top, 28)
            .padding(.bottom, 96)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .accessibilityIdentifier("requestsScreen")
        .onChange(of: focusRequest) { _, _ in focusedSearch = true }
        .task(id: store.session?.accessToken) {
            catalog.configure(session: store.session)
            guard store.session != nil else {
                quotaState = .failed("Sign in to check your request allowance.")
                return
            }
            await catalog.loadHome()
            await loadQuotas()
        }
        .task(id: "\(query)|\(searchRetry)") { await startSearch() }
        .task(id: browseKey) { await startBrowse() }
        .sheet(isPresented: $historyPresented) {
            TVRequestHistory(catalog: catalog)
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 24) {
            Text("Requests")
                .font(LuminaTheme.editorial(52, relativeTo: .largeTitle, italic: true))

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 24) { searchControl; browseControls }
                VStack(alignment: .leading, spacing: 24) { searchControl; browseControls }
            }
        }
        .padding(.horizontal, 80)
        .focusSection()
    }

    private var searchControl: some View {
        TextField("Search films, series, and anime", text: $search)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .frame(minWidth: 280, idealWidth: 440, maxWidth: .infinity)
            .accessibilityIdentifier("requestsSearchField")
            .focused($focusedSearch)
    }

    private var browseControls: some View {
        HStack(spacing: 24) {
            Menu {
                Picker("Catalog", selection: Binding(
                    get: { browseKind },
                    set: { next in
                        browseGenre = ""
                        browseKind = next
                    }
                )) {
                    Text("Featured").tag(nil as RequestCatalogKind?)
                    ForEach(RequestCatalogKind.allCases, id: \.self) { kind in
                        Text(kind.rawValue.capitalized).tag(Optional(kind))
                    }
                }
                .accessibilityIdentifier("requestsBrowseKindPicker")

                if browseKind != nil {
                    Picker("Genre", selection: $browseGenre) {
                        Text("All genres").tag("")
                        ForEach(availableGenres) { genre in
                            Text(genre.name).tag(genre.id)
                        }
                    }
                    .accessibilityIdentifier("requestsBrowseGenrePicker")

                    if let genreError {
                        Text(genreError)
                    }
                }
            } label: {
                Label(browseTitle, systemImage: "line.3.horizontal.decrease.circle")
                    .frame(minWidth: 240, alignment: .leading)
            }
            .buttonStyle(LuminaUnderlineTabStyle())
            .controlSize(.large)
            .accessibilityIdentifier("requestsBrowseMenu")

            Button {
                historyPresented = true
            } label: {
                Label("My requests", systemImage: "clock.arrow.circlepath")
                    .frame(minWidth: 240, alignment: .leading)
            }
            .buttonStyle(LuminaUnderlineTabStyle())
            .controlSize(.large)
            .accessibilityIdentifier("requestsMyListButton")
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    @ViewBuilder private var searchContent: some View {
        VStack(alignment: .leading, spacing: 24) {
            TVRequestSectionHeading(title: "Search results", count: searching ? nil : searchResults.count)
                .padding(.horizontal, 80)

            if searching {
                ProgressView("Searching the catalog…")
                    .frame(maxWidth: .infinity, minHeight: 320)
            } else if let searchError {
                TVRequestMessage(title: "The catalog could not load", message: searchError, actionTitle: "Try again") {
                    searchRetry &+= 1
                }
                .frame(maxWidth: .infinity, minHeight: 320)
            } else if searchResults.isEmpty {
                TVRequestMessage(title: "No matching titles", message: "Try a different title, person, or genre.", actionTitle: "Clear search") {
                    search = ""
                }
                .frame(maxWidth: .infinity, minHeight: 320)
            } else {
                requestGrid(searchResults)
                if searchPage < searchTotalPages {
                    Button(loadingMoreSearch ? "Loading…" : "Show more") { loadMoreSearch() }
                        .buttonStyle(LuminaQuietActionStyle())
                        .controlSize(.large)
                        .disabled(loadingMoreSearch)
                        .accessibilityIdentifier("requestsSearchMoreButton")
                        .padding(.horizontal, 80)
                }
            }
        }
    }

    @ViewBuilder private var browseContent: some View {
        VStack(alignment: .leading, spacing: 24) {
            TVRequestSectionHeading(title: browseTitle, count: browsing ? nil : browseResults.count)
                .padding(.horizontal, 80)

            if browsing {
                ProgressView("Loading the catalog…")
                    .frame(maxWidth: .infinity, minHeight: 320)
            } else if let browseError {
                TVRequestMessage(title: "The catalog could not load", message: browseError, actionTitle: "Try again") {
                    browseRetry &+= 1
                }
                .frame(maxWidth: .infinity, minHeight: 320)
            } else if browseResults.isEmpty {
                TVRequestMessage(title: "No titles here yet", message: "Choose another genre or browse a different catalog.", actionTitle: "Clear genre") {
                    browseGenre = ""
                }
                .frame(maxWidth: .infinity, minHeight: 320)
            } else {
                requestGrid(browseResults)
                if browsePage < browseTotalPages {
                    Button(loadingMoreBrowse ? "Loading…" : "Show more") { loadMoreBrowse() }
                        .buttonStyle(LuminaQuietActionStyle())
                        .controlSize(.large)
                        .disabled(loadingMoreBrowse)
                        .accessibilityIdentifier("requestsBrowseMoreButton")
                        .padding(.horizontal, 80)
                }
            }
        }
    }

    private func requestGrid(_ items: [RequestCatalogItem]) -> some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 210, maximum: 260), spacing: 32, alignment: .top)],
            spacing: 44
        ) {
            ForEach(items) { item in
                TVRequestPoster(item: item, width: 238, catalog: catalog, quotaState: quotaState, reloadQuotas: loadQuotas)
            }
        }
        .padding(.horizontal, 80)
        .focusSection()
    }

    @discardableResult
    private func loadQuotas() async -> TVRequestQuotaState {
        quotaState = .loading
        await catalog.loadQuotas()
        let resolved: TVRequestQuotaState
        if let error = catalog.error {
            resolved = .failed(error.localizedDescription)
        } else {
            resolved = .ready
        }
        quotaState = resolved
        return resolved
    }

    private func startSearch() async {
        guard !query.isEmpty else {
            searchResults = []
            searchError = nil
            searching = false
            return
        }
        let submittedQuery = query
        searching = true
        searchError = nil
        searchPage = 0
        searchTotalPages = 1
        defer { if submittedQuery == query { searching = false } }
        do { try await Task.sleep(for: .milliseconds(320)) }
        catch { return }
        guard !Task.isCancelled, submittedQuery == query else { return }
        do {
            let page = try await catalog.search(submittedQuery)
            guard !Task.isCancelled, submittedQuery == query else { return }
            searchResults = deduplicated(page.items)
            searchPage = page.page
            searchTotalPages = page.totalPages
        } catch {
            guard !Task.isCancelled, submittedQuery == query else { return }
            searchResults = []
            searchError = error.localizedDescription
        }
    }

    private func loadMoreSearch() {
        guard !loadingMoreSearch, searchPage < searchTotalPages else { return }
        let submittedQuery = query
        let nextPage = searchPage + 1
        loadingMoreSearch = true
        Task {
            defer { if submittedQuery == query { loadingMoreSearch = false } }
            do {
                let page = try await catalog.search(submittedQuery, page: nextPage)
                guard !Task.isCancelled, submittedQuery == query else { return }
                searchResults = deduplicated(searchResults + page.items)
                searchPage = page.page
                searchTotalPages = page.totalPages
            } catch {
                guard !Task.isCancelled, submittedQuery == query else { return }
                searchError = error.localizedDescription
            }
        }
    }

    private func startBrowse() async {
        guard let kind = browseKind else {
            browseResults = []
            browseError = nil
            availableGenres = []
            browsing = false
            return
        }
        let submittedGenre = browseGenre
        browsing = true
        browseError = nil
        browsePage = 0
        browseTotalPages = 1
        async let genres = loadGenres(for: kind)
        do {
            let page = try await catalog.list(kind, genre: submittedGenre.isEmpty ? nil : submittedGenre)
            guard !Task.isCancelled, kind == browseKind, submittedGenre == browseGenre else { return }
            browseResults = deduplicated(page.items)
            browsePage = page.page
            browseTotalPages = page.totalPages
        } catch {
            guard !Task.isCancelled, kind == browseKind, submittedGenre == browseGenre else { return }
            browseResults = []
            browseError = error.localizedDescription
        }
        _ = await genres
        if kind == browseKind, submittedGenre == browseGenre { browsing = false }
    }

    private func loadGenres(for kind: RequestCatalogKind) async {
        genreError = nil
        do {
            let values = try await catalog.genres(kind)
            guard !Task.isCancelled, kind == browseKind else { return }
            availableGenres = values
        } catch {
            guard !Task.isCancelled, kind == browseKind else { return }
            availableGenres = []
            genreError = "Genres could not load. You can still browse all titles."
        }
    }

    private func loadMoreBrowse() {
        guard let kind = browseKind, !loadingMoreBrowse, browsePage < browseTotalPages else { return }
        let submittedGenre = browseGenre
        let nextPage = browsePage + 1
        loadingMoreBrowse = true
        Task {
            defer {
                if kind == browseKind, submittedGenre == browseGenre { loadingMoreBrowse = false }
            }
            do {
                let page = try await catalog.list(kind, genre: submittedGenre.isEmpty ? nil : submittedGenre, page: nextPage)
                guard !Task.isCancelled, kind == browseKind, submittedGenre == browseGenre else { return }
                browseResults = deduplicated(browseResults + page.items)
                browsePage = page.page
                browseTotalPages = page.totalPages
            } catch {
                guard !Task.isCancelled, kind == browseKind, submittedGenre == browseGenre else { return }
                browseError = error.localizedDescription
            }
        }
    }

    private func deduplicated(_ items: [RequestCatalogItem]) -> [RequestCatalogItem] {
        var seen = Set<String>()
        return items.filter { seen.insert($0.id).inserted }
    }
}

@MainActor
private struct TVRequestHero: View {
    let item: RequestCatalogItem
    @Bindable var catalog: LuminaRequests
    let quotaState: TVRequestQuotaState
    let reloadQuotas: @MainActor () async -> TVRequestQuotaState

    var body: some View {
        NavigationLink {
            TVRequestDetailView(item: item, catalog: catalog, initialQuotaState: quotaState, reloadQuotas: reloadQuotas)
        } label: {
            ZStack(alignment: .bottomLeading) {
                TVRequestArtwork(url: item.backdropURL ?? item.posterURL, aspect: 16 / 7, fillsCanvas: true, home: true, catalog: catalog)
                LinearGradient(
                    colors: [.clear, LuminaTheme.theater.opacity(0.35), LuminaTheme.theater],
                    startPoint: .center,
                    endPoint: .bottom
                )

                VStack(alignment: .leading, spacing: 16) {
                    MediaTitleArtwork(name: item.title, url: item.logoURL, maximumWidth: 520, maximumHeight: 150) {
                        Text(item.title)
                            .font(LuminaTheme.editorial(56, relativeTo: .largeTitle, italic: true))
                            .multilineTextAlignment(.leading)
                            .lineLimit(2)
                    }

                    Text(metadata(for: item))
                        .font(.system(size: 24))
                        .foregroundStyle(LuminaTheme.ink.opacity(0.82))

                    if let overview = item.overview, !overview.isEmpty {
                        Text(overview)
                            .font(.body)
                            .foregroundStyle(LuminaTheme.ink.opacity(0.9))
                            .lineLimit(3)
                            .frame(maxWidth: 760, alignment: .leading)
                    }

                    Label(actionLabel(for: item), systemImage: item.status.canCreateRequest && item.canRequest ? "plus" : "info.circle")
                        .font(.headline)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 12)
                        .foregroundStyle(LuminaTheme.theater)
                        .background(.white, in: .capsule)
                }
                .padding(.horizontal, 82)
                .padding(.vertical, 58)
            }
            .frame(maxWidth: .infinity, minHeight: 570, maxHeight: 640)
            .contentShape(.rect)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: 16 / 7))
        .accessibilityLabel("\(item.title), \(actionLabel(for: item))")
        .accessibilityIdentifier("requestFeatured-\(item.id)")
    }
}

@MainActor
private struct TVRequestRail: View {
    let title: String
    let items: [RequestCatalogItem]
    @Bindable var catalog: LuminaRequests
    let quotaState: TVRequestQuotaState
    let reloadQuotas: @MainActor () async -> TVRequestQuotaState

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            TVRequestSectionHeading(title: title, count: items.count)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 30) {
                    ForEach(items) { item in
                        TVRequestPoster(item: item, width: 224, catalog: catalog, quotaState: quotaState, reloadQuotas: reloadQuotas)
                    }
                }
                .padding(.vertical, 24)
            }
            .scrollClipDisabled()
        }
        .padding(.horizontal, 80)
    }
}

@MainActor
private struct TVRequestPoster: View {
    let item: RequestCatalogItem
    let width: CGFloat
    @Bindable var catalog: LuminaRequests
    let quotaState: TVRequestQuotaState
    let reloadQuotas: @MainActor () async -> TVRequestQuotaState

    var body: some View {
        NavigationLink {
            TVRequestDetailView(item: item, catalog: catalog, initialQuotaState: quotaState, reloadQuotas: reloadQuotas)
        } label: {
            VStack(alignment: .leading, spacing: 12) {
                TVRequestArtwork(url: item.posterURL ?? item.backdropURL, aspect: 2 / 3, catalog: catalog)
                    .overlay(alignment: .bottomLeading) {
                        if item.status.state != .none {
                            Text(item.status.label)
                                .font(.caption.weight(.semibold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 7)
                                .background(.black.opacity(0.82), in: .rect(topTrailingRadius: 8))
                        }
                    }

                Text(item.title)
                    .font(.title3.weight(.medium))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(metadata(for: item))
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)
            }
            .frame(width: width, alignment: .leading)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: 2 / 3))
        .accessibilityLabel("\(item.title), \(item.status.label.isEmpty ? "Available to request" : item.status.label)")
        .accessibilityIdentifier("requestPoster-\(item.id)")
    }
}

@MainActor
private struct TVRequestDetailView: View {
    let item: RequestCatalogItem
    @Bindable var catalog: LuminaRequests
    let initialQuotaState: TVRequestQuotaState
    let reloadQuotas: @MainActor () async -> TVRequestQuotaState
    @State private var quotaState: TVRequestQuotaState = .loading
    @State private var detail: RequestCatalogDetail?
    @State private var loading = true
    @State private var problem: String?
    @State private var submitting = false
    @State private var created: RequestRecord?
    @State private var seasonMode = TVSeasonMode.all
    @State private var selectedSeasons = Set<Int>()
    @State private var language: RequestLanguage?
    @State private var languageRequired = false
    @State private var trailerUnavailable = false
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    private var current: RequestCatalogItem { detail?.item ?? item }
    private var quota: RequestQuota? { catalog.quotas.first { $0.kind == current.kind } }
    private var isSeries: Bool { current.mediaType == .tv }
    private var needsLanguage: Bool { languageRequired || (current.kind == .anime && current.mediaType == .tv) }
    private var seasons: RequestSeasonSelection? {
        guard isSeries else { return nil }
        switch seasonMode {
        case .all: return .all
        case .chosen: return selectedSeasons.isEmpty ? nil : .selected(selectedSeasons.sorted())
        }
    }
    private var canSubmit: Bool {
        guard case .ready = quotaState, let quota else { return false }
        return !loading && !submitting && created == nil && current.canRequest && current.status.canCreateRequest && quota.canRequest && quota.remaining != 0 && (!isSeries || seasons != nil) && (!needsLanguage || language != nil)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 38) {
                hero

                if loading {
                    ProgressView("Loading title details…")
                        .frame(maxWidth: .infinity, minHeight: 200)
                } else {
                    requestPanel
                    details
                    recommendations
                }
            }
            .padding(.bottom, 96)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .navigationTitle("")
        .accessibilityIdentifier("requestDetailScreen")
        .onExitCommand { dismiss() }
        .task(id: item.id) { await load() }
        .task(id: item.id) {
            quotaState = initialQuotaState
            await refreshQuota()
        }
    }

    private var hero: some View {
        ZStack(alignment: .bottomLeading) {
            TVRequestArtwork(url: current.backdropURL ?? current.posterURL, aspect: 16 / 7, fillsCanvas: true, catalog: catalog)
            LinearGradient(colors: [.clear, LuminaTheme.theater.opacity(0.3), LuminaTheme.theater], startPoint: .center, endPoint: .bottom)

            VStack(alignment: .leading, spacing: 14) {
                MediaTitleArtwork(name: current.title, url: current.logoURL, maximumWidth: 560, maximumHeight: 160) {
                    Text(current.title)
                        .font(LuminaTheme.editorial(58, relativeTo: .largeTitle, italic: true))
                        .lineLimit(2)
                }

                Text(metadata(for: current))
                    .font(.system(size: 24))
                    .foregroundStyle(LuminaTheme.ink.opacity(0.82))

                if let tagline = detail?.tagline, !tagline.isEmpty {
                    Text(tagline)
                        .font(.system(size: 24).italic())
                        .foregroundStyle(LuminaTheme.ink.opacity(0.9))
                }
            }
            .padding(.horizontal, 82)
            .padding(.vertical, 52)
        }
        .frame(maxWidth: .infinity, minHeight: 500, maxHeight: 600)
    }

    @ViewBuilder private var requestPanel: some View {
        VStack(alignment: .leading, spacing: 22) {
            TVRequestSectionHeading(title: "Add to your household")

            if let trailerURL = detail?.trailers.compactMap(\.youtubeURL).first {
                Button {
                    openURL(trailerURL) { accepted in
                        trailerUnavailable = !accepted
                    }
                } label: {
                    Label("Watch trailer on YouTube", systemImage: "arrow.up.right")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .controlSize(.large)
                .accessibilityIdentifier("requestYouTubeTrailerButton")
                if trailerUnavailable {
                    Text("YouTube could not open on this Apple TV. You can watch this trailer from Lumina on your phone or the website.")
                        .font(.body)
                        .foregroundStyle(LuminaTheme.muted)
                }
            }

            if let created {
                Label(created.status.label, systemImage: "checkmark.circle.fill")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(LuminaTheme.accentInk)
                    .accessibilityIdentifier("requestSubmittedStatus")
            } else if !current.status.label.isEmpty, !current.status.canCreateRequest {
                Label(current.status.label, systemImage: "clock.fill")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(LuminaTheme.accentInk)
                    .accessibilityIdentifier("requestStatus")
            } else if current.canRequest && current.status.canCreateRequest {
                requestChoices
            } else {
                Label(current.unrequestableReason ?? "This title cannot be requested yet.", systemImage: "info.circle")
                    .foregroundStyle(LuminaTheme.muted)
            }

            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(LuminaTheme.accentInk)
                    .accessibilityIdentifier("requestSubmissionError")
            }
        }
        .padding(.horizontal, 80)
        .focusSection()
    }

    @ViewBuilder private var requestChoices: some View {
        VStack(alignment: .leading, spacing: 20) {
            if isSeries {
                Picker("Seasons", selection: $seasonMode) {
                    Text("All seasons").tag(TVSeasonMode.all)
                    if !(detail?.seasons.isEmpty ?? true) { Text("Choose seasons").tag(TVSeasonMode.chosen) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 760)
                .accessibilityIdentifier("requestSeasonPicker")

                if seasonMode == .chosen {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 270), spacing: 18)], alignment: .leading, spacing: 18) {
                        ForEach(detail?.seasons ?? []) { season in
                            Toggle(isOn: Binding(
                                get: { selectedSeasons.contains(season.number) },
                                set: { selected in
                                    if selected { selectedSeasons.insert(season.number) }
                                    else { selectedSeasons.remove(season.number) }
                                }
                            )) {
                                Text("\(season.name) · \(season.episodeCount) episodes")
                            }
                            .accessibilityIdentifier("requestSeason-\(season.number)")
                        }
                    }
                    .frame(maxWidth: 920, alignment: .leading)
                }
            }

            if needsLanguage {
                Picker("Audio language", selection: $language) {
                    Text("Choose language").tag(nil as RequestLanguage?)
                    Text("English dub").tag(Optional(RequestLanguage.dub))
                    Text("Japanese + subtitles").tag(Optional(RequestLanguage.sub))
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 760)
                .accessibilityIdentifier("requestLanguagePicker")
            }

            quotaNotice

            Button {
                Task { await submit() }
            } label: {
                HStack(spacing: 12) {
                    if submitting { ProgressView() }
                    Label("Request \(current.title)", systemImage: "plus")
                }
            }
            .buttonStyle(LuminaQuietActionStyle(primary: true))
            .controlSize(.large)
            .disabled(!canSubmit)
            .accessibilityIdentifier("requestConfirmButton")
        }
    }

    @ViewBuilder private var quotaNotice: some View {
        switch quotaState {
        case .loading:
            HStack(spacing: 12) {
                ProgressView()
                Text("Checking your request allowance…")
            }
            .font(.body)
            .foregroundStyle(LuminaTheme.muted)
            .accessibilityIdentifier("requestQuotaLoading")
        case .failed(let message):
            VStack(alignment: .leading, spacing: 12) {
                Label("Could not check your request allowance", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(LuminaTheme.accentInk)
                Text(message)
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
                Button("Try again") {
                    Task { await refreshQuota() }
                }
                .buttonStyle(LuminaQuietActionStyle())
                .accessibilityIdentifier("requestRetryQuotaButton")
            }
        case .ready:
            if let quota {
                Text(quotaText(quota))
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    Label("Your request allowance is unavailable", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(LuminaTheme.accentInk)
                    Button("Try again") {
                        Task { await refreshQuota() }
                    }
                    .buttonStyle(LuminaQuietActionStyle())
                    .accessibilityIdentifier("requestRetryQuotaButton")
                }
            }
        }
    }

    @ViewBuilder private var details: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let overview = current.overview, !overview.isEmpty {
                TVRequestSectionHeading(title: "About this title")
                Text(overview)
                    .font(.system(size: 24))
                    .foregroundStyle(LuminaTheme.ink.opacity(0.88))
                    .frame(maxWidth: 980, alignment: .leading)
            }

            if let cast = detail?.cast, !cast.isEmpty {
                Text("Starring \(cast.prefix(8).map(\.name).joined(separator: ", "))")
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
            }
        }
        .padding(.horizontal, 80)
    }

    @ViewBuilder private var recommendations: some View {
        let entries = detail?.recommendations ?? []
        if !entries.isEmpty {
            TVRequestRail(title: "More like this", items: entries, catalog: catalog, quotaState: quotaState, reloadQuotas: reloadQuotas)
                .focusSection()
        }
    }

    private func refreshQuota() async {
        quotaState = .loading
        quotaState = await reloadQuotas()
    }

    private func load() async {
        guard let id = item.catalogID else {
            loading = false
            problem = "This title has not been matched yet."
            return
        }

        loading = true
        problem = nil
        defer { loading = false }
        do { detail = try await catalog.detail(kind: item.kind, id: id) }
        catch { problem = error.localizedDescription }
    }

    private func submit() async {
        guard canSubmit else { return }
        submitting = true
        problem = nil
        defer { submitting = false }

        do {
            created = try await catalog.create(current, seasons: seasons, language: language)
            await refreshQuota()
            await catalog.loadHome()
        } catch {
            if case RequestAPIError.server(_, "language_required") = error { languageRequired = true }
            problem = error.localizedDescription
        }
    }
}

private struct TVRequestHistoryQuery: Hashable {
    var status: RequestState?
    var page = 1
}

@MainActor
private struct TVRequestHistory: View {
    @Bindable var catalog: LuminaRequests
    @State private var query = TVRequestHistoryQuery()
    @Environment(\.dismiss) private var dismiss

    private var status: Binding<RequestState?> {
        Binding(
            get: { query.status },
            set: { query = TVRequestHistoryQuery(status: $0) }
        )
    }

    var body: some View {
        NavigationStack {
            List {
                if let records = catalog.myRequests?.items, !records.isEmpty {
                    ForEach(records) { record in
                        HStack(spacing: 24) {
                            TVRequestArtwork(url: record.posterURL, aspect: 2 / 3, catalog: catalog)
                                .frame(width: 90)
                            VStack(alignment: .leading, spacing: 8) {
                                Text(record.title).font(.system(size: 26, weight: .semibold))
                                Text(record.status.label).foregroundStyle(LuminaTheme.accentInk)
                                if record.status == .processing {
                                    ProgressView(value: min(1, max(0, record.progress ?? 0)))
                                        .frame(width: 360)
                                }
                            }
                        }
                        .padding(.vertical, 10)
                        .accessibilityIdentifier("requestHistory-\(record.key)")
                    }
                    if let result = catalog.myRequests, result.totalPages > 1 {
                        HStack {
                            Button("Previous") { query.page -= 1 }
                                .disabled(query.page <= 1)
                            Spacer()
                            Text("\(query.page) of \(result.totalPages)")
                                .foregroundStyle(LuminaTheme.muted)
                            Spacer()
                            Button("Next") { query.page += 1 }
                                .disabled(query.page >= result.totalPages)
                        }
                        .padding(.vertical, 16)
                    }
                } else if catalog.isLoading {
                    ProgressView("Loading requests…")
                } else {
                    ContentUnavailableView("No requests yet", systemImage: "sparkles.tv", description: Text("Titles you request will appear here with their progress."))
                }
            }
            .navigationTitle("My requests")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Picker("Status", selection: status) {
                        Text("All requests").tag(nil as RequestState?)
                        ForEach([RequestState.pending, .approved, .processing, .partiallyAvailable, .available, .declined, .failed], id: \.self) { state in
                            Text(state.label).tag(Optional(state))
                        }
                    }
                    .accessibilityIdentifier("requestHistoryStatusPicker")
                }
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .task(id: query) {
                await catalog.loadMyRequests(status: query.status, page: query.page)
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct TVRequestArtwork: View {
    let url: URL?
    let aspect: CGFloat
    var fillsCanvas = false
    var home = false
    var catalog: LuminaRequests?

    var body: some View {
        Group {
            if fillsCanvas { canvas }
            else { canvas.aspectRatio(aspect, contentMode: .fit) }
        }
        .clipped()
        .clipShape(.rect(cornerRadius: 8))
        .accessibilityHidden(true)
    }

    private var canvas: some View {
        Rectangle().fill(LuminaTheme.ink.opacity(0.08))
            .overlay {
                SubjectAwareArtwork(url: url, home: home) { source in
                    await catalog?.artworkFraming(for: source)
                }
            }
    }
}

private struct TVRequestSectionHeading: View {
    let title: String
    var count: Int?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 20) {
            Text(title)
                .font(LuminaTheme.editorial(34, relativeTo: .title2))
                .foregroundStyle(LuminaTheme.ink)
                .accessibilityAddTraits(.isHeader)
            if let count {
                Text(count, format: .number)
                    .font(.system(size: 24))
                    .foregroundStyle(LuminaTheme.muted)
            }
            Rectangle()
                .fill(LuminaTheme.muted.opacity(0.24))
                .frame(height: 1)
                .accessibilityHidden(true)
        }
    }
}

private struct TVRequestMessage: View {
    let title: String
    let message: String
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Text(title).font(.title.weight(.semibold))
            Text(message).font(.system(size: 24)).foregroundStyle(LuminaTheme.muted).multilineTextAlignment(.center)
            Button(actionTitle, action: action)
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .controlSize(.large)
        }
        .padding(48)
    }
}

private enum TVSeasonMode: Hashable {
    case all
    case chosen
}

private func metadata(for item: RequestCatalogItem) -> String {
    ([item.year.map(String.init), item.genres.prefix(2).joined(separator: " · ")])
        .compactMap { $0 }
        .filter { !$0.isEmpty }
        .joined(separator: " · ")
}

private func actionLabel(for item: RequestCatalogItem) -> String {
    if item.canRequest && item.status.canCreateRequest { return "Request" }
    return item.status.label.isEmpty ? "Details" : item.status.label
}

private func quotaText(_ quota: RequestQuota) -> String {
    if !quota.canRequest { return "Your account cannot request this category. Ask an administrator." }
    if let remaining = quota.remaining, let limit = quota.limit { return "\(remaining) of \(limit) requests remaining" }
    return quota.autoApprove ? "Requests are approved automatically." : "Requests need your administrator’s approval."
}
