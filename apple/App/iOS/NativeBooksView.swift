import SwiftUI
import LuminaCore
import UIKit

@MainActor
struct NativeBooksView: View {
    let session: LuminaSession
    var shelf: BookShelf?
    var series: BookSeries?
    var catalogOnly = false
    @State private var books = [BookItem]()
    @State private var overview: BooksOverview?
    @State private var seriesItems = [BookSeries]()
    @State private var seriesCursor: String?
    @State private var search = ""
    @State private var searchVisible = false
    @State private var kindFilter = NativeBookKindFilter.all
    @State private var readingFilter = NativeBookReadingFilter.all
    @State private var sort = NativeBookSort.added
    @State private var nextCursor: String?
    @State private var busy = false
    @State private var error: String?
    @State private var selected: BookItem?
    @State private var api: LuminaBooks?
    @State private var covers: NativeBookCovers?
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    init(session: LuminaSession, shelf: BookShelf? = nil, series: BookSeries? = nil, catalogOnly: Bool = false, covers: NativeBookCovers? = nil) {
        self.session = session; self.shelf = shelf; self.series = series; self.catalogOnly = catalogOnly
        _covers = State(initialValue: covers)
    }

    private var root: Bool { shelf == nil && series == nil && !catalogOnly }
    private var showsOverview: Bool {
        root && search.isEmpty && !hasCatalogFilters
    }
    private var hasCatalogFilters: Bool { kindFilter != .all || readingFilter != .all || sort != .added }
    private var catalogQuery: NativeBookCatalogQuery {
        NativeBookCatalogQuery(search: search, kind: kindFilter, status: readingFilter, sort: sort)
    }
    private var title: String { shelf?.name ?? series?.title ?? (catalogOnly ? "All books" : "Books") }
    private var featuredBooks: [BookItem] {
        if let reading = overview?.continueReading, !reading.isEmpty { return reading }
        var seen = Set<String>()
        return Array(books.filter { book in
            guard book.kind == "manga", let series = book.seriesID else { return true }
            return seen.insert(series).inserted
        }.prefix(8))
    }
    private var columns: [GridItem] {
        let compact = horizontalSizeClass == .compact
        return [GridItem(
            .adaptive(
                minimum: typeSize.isAccessibilitySize ? 220 : compact ? 116 : 150,
                maximum: typeSize.isAccessibilitySize ? 360 : compact ? 180 : 230
            ),
            spacing: compact ? 14 : 22,
            alignment: .top
        )]
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 30) {
                if let error {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(error).foregroundStyle(.secondary)
                        Button("Retry", systemImage: "arrow.clockwise") { Task { await load() } }
                    }
                }
                if let covers {
                    if showsOverview {
                        if !featuredBooks.isEmpty {
                            sectionTitle(overview?.continueReading.isEmpty == false ? "Continue reading" : "Recently added")
                            ScrollView(.horizontal) {
                                LazyHStack(alignment: .top, spacing: 14) {
                                    ForEach(featuredBooks) { book in
                                        bookCard(book, covers: covers)
                                            .frame(width: typeSize.isAccessibilitySize ? 220 : 148)
                                    }
                                }.padding(.horizontal, 4)
                            }.scrollIndicators(.hidden)
                            .modifier(LuminaCarouselEdges(inset: horizontalSizeClass == .compact ? 16 : 28))
                        }
                        if let shelves = overview?.shelves, !shelves.isEmpty {
                            sectionTitle("Shelves")
                            ScrollView(.horizontal) {
                                LazyHStack(alignment: .top, spacing: 18) {
                                    ForEach(shelves) { shelf in
                                        NavigationLink {
                                            NativeBooksView(session: session, shelf: shelf, covers: covers)
                                        } label: {
                                            VStack(alignment: .leading, spacing: 10) {
                                                NativeBookStack(ids: Array(shelf.coverBookIDs.prefix(3)), title: shelf.name, covers: covers, count: shelf.count)
                                                Text(shelf.name).font(.headline).foregroundStyle(.primary)
                                                Text("\(shelf.count) \(shelf.count == 1 ? "book" : "books")").font(.caption).foregroundStyle(.secondary)
                                            }.frame(width: 150)
                                        }.buttonStyle(.plain).accessibilityIdentifier("bookShelf-\(shelf.id)")
                                    }
                                }.padding(.horizontal, 8).padding(.top, 12)
                            }.scrollIndicators(.hidden)
                            .modifier(LuminaCarouselEdges(inset: horizontalSizeClass == .compact ? 16 : 28))
                        }
                        if !seriesItems.isEmpty {
                            sectionTitle("Manga series")
                            LazyVGrid(columns: columns, alignment: .leading, spacing: 24) {
                                ForEach(seriesItems) { series in
                                    NavigationLink {
                                        NativeBooksView(session: session, series: series, covers: covers)
                                    } label: {
                                        VStack(alignment: .leading, spacing: 10) {
                                            NativeBookStack(ids: series.coverBookID.map { [$0] } ?? [], title: series.title, covers: covers, count: series.volumeCount)
                                            Text(series.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                                            Text("\(series.volumeCount) \(series.volumeCount == 1 ? "volume" : "volumes") · \(series.author)").font(.caption).foregroundStyle(.secondary)
                                        }
                                    }.buttonStyle(.plain).accessibilityIdentifier("bookSeries-\(series.id)")
                                }
                            }
                            if seriesCursor != nil {
                                Button("More manga series") { Task { await loadSeries(more: true) } }.disabled(busy)
                            }
                        }
                        ViewThatFits(in: .horizontal) {
                            HStack {
                                sectionTitle("All books")
                                Spacer()
                                catalogLink(covers: covers)
                            }
                            VStack(alignment: .leading, spacing: 10) {
                                sectionTitle("All books")
                                catalogLink(covers: covers)
                            }
                        }
                    }
                    LazyVGrid(columns: columns, alignment: .leading, spacing: 26) {
                        ForEach(books) { book in bookCard(book, covers: covers) }
                    }
                }
                if busy { ProgressView("Loading books…").frame(maxWidth: .infinity) }
                if nextCursor != nil {
                    Button("Load more books") { Task { await load(more: true) } }.disabled(busy)
                }
                if !busy, error == nil, books.isEmpty {
                    if search.isEmpty && !hasCatalogFilters {
                        ContentUnavailableView("No books", systemImage: "books.vertical", description: Text("Import EPUB, PDF, CBZ or CBR books in Lumina’s browser library."))
                    } else {
                        ContentUnavailableView("No matching books", systemImage: "line.3.horizontal.decrease.circle", description: Text("Change a filter or search to see more of your library."))
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, horizontalSizeClass == .compact ? 16 : 28)
            .padding(.vertical, 18)
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .scrollClipDisabled()
        .background(LuminaTheme.paper.ignoresSafeArea())
        .navigationTitle(title)
        .toolbarVisibility(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Type", selection: $kindFilter) {
                        ForEach(NativeBookKindFilter.allCases) { filter in Text(filter.title).tag(filter) }
                    }
                    Picker("Reading", selection: $readingFilter) {
                        ForEach(NativeBookReadingFilter.allCases) { filter in Text(filter.title).tag(filter) }
                    }
                    Picker("Sort", selection: $sort) {
                        ForEach(NativeBookSort.allCases) { option in Text(option.title).tag(option) }
                    }
                } label: {
                    Label("Book options", systemImage: "ellipsis.circle")
                }
                .accessibilityIdentifier("booksOptionsMenu")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("Search books", systemImage: "magnifyingglass") { searchVisible = true }
                    .accessibilityIdentifier("booksSearchButton")
            }
        }
        .modifier(NativeSearchPresentation(text: $search, isPresented: $searchVisible, prompt: "Title or author"))
        .task(id: catalogQuery) {
            do { try await Task.sleep(for: .milliseconds(250)); await load() } catch { }
        }
        .refreshable { await load() }
        .fullScreenCover(item: $selected, onDismiss: { Task { await load() } }) { book in
            NativeBookReader(session: session, book: book, covers: covers)
        }
        .accessibilityIdentifier("booksScreen")
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(.title2.weight(.bold))
            .accessibilityAddTraits(.isHeader)
    }
    private func catalogLink(covers: NativeBookCovers) -> some View {
        NavigationLink {
            NativeBooksView(session: session, catalogOnly: true, covers: covers)
        } label: { Label("Browse all", systemImage: "square.grid.2x2") }
        .accessibilityIdentifier("bookCatalogButton")
    }
    private func bookCard(_ book: BookItem, covers: NativeBookCovers) -> some View {
        Button { selected = book } label: {
            VStack(alignment: .leading, spacing: 8) {
                NativeBookCover(id: book.coverURL == nil ? nil : book.id, title: book.title, author: book.author, covers: covers)
                    .overlay(alignment: .bottomLeading) {
                        if !book.progress.completed, let percent = book.progress.percent, percent > 0 {
                            GeometryReader { geometry in
                                Rectangle().fill(LuminaTheme.brand).frame(width: geometry.size.width * min(1, max(0.01, percent / 100)))
                            }.frame(height: 4)
                        }
                    }
                Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                Text(book.author.isEmpty ? "Unknown author" : book.author).font(.subheadline).foregroundStyle(LuminaTheme.muted).lineLimit(1)
                Text(book.progress.completed ? "Read · \(book.format.uppercased())" : "\(book.format.uppercased()) · \(book.progress.unitIndex > 0 || book.progress.offset > 0 ? "Continue reading" : "Start reading")")
                    .font(.caption).foregroundStyle(LuminaTheme.muted)
            }
        }
        .buttonStyle(.plain).disabled(!book.available)
        .accessibilityIdentifier("book-\(book.id)")
    }

    private func load(more: Bool = false) async {
        if more && busy { return }
        let requestedQuery = catalogQuery
        busy = true; error = nil
        defer { if requestedQuery == catalogQuery { busy = false } }
        do {
            let api = self.api ?? LuminaBooks(session: session)
            self.api = api
            if covers == nil { covers = NativeBookCovers(api: api) }
            let loadOverview = root && requestedQuery.search.isEmpty && !requestedQuery.hasFilters && !more
            async let summary: BooksOverview? = loadOverview ? api.overview() : nil
            async let manga: BookSeriesCatalog? = loadOverview ? api.series() : nil
            let result: [BookItem]
            let cursor: String?
            if let series {
                let detail = try await api.series(id: series.id)
                result = locallyFilteredSeries(detail.books, matching: requestedQuery)
                cursor = nil
            } else {
                let page = try await api.catalog(
                    search: requestedQuery.search,
                    cursor: more ? nextCursor : nil,
                    shelfID: shelf?.id,
                    kind: requestedQuery.kind.catalogKind,
                    status: requestedQuery.status.catalogStatus,
                    sort: requestedQuery.sort.catalogSort
                )
                result = page.items; cursor = page.nextCursor
            }
            let (loadedOverview, loadedSeries) = try await (summary, manga)
            try Task.checkCancellation()
            guard requestedQuery == catalogQuery else { return }
            if let loadedOverview { overview = loadedOverview }
            if let loadedSeries { seriesItems = loadedSeries.items; seriesCursor = loadedSeries.nextCursor }
            let existing = more ? books : []
            var ids = Set(existing.map(\.id))
            books = existing + result.filter { ids.insert($0.id).inserted }
            nextCursor = cursor
        } catch is CancellationError { }
        catch { if requestedQuery == catalogQuery { self.error = error.localizedDescription } }
    }
    private func locallyFilteredSeries(_ items: [BookItem], matching query: NativeBookCatalogQuery) -> [BookItem] {
        let filtered = items.filter { book in
            let started = book.progress.unitIndex > 0 || book.progress.offset > 0
            let matchingText = query.search.isEmpty || (book.title + " " + book.author).localizedCaseInsensitiveContains(query.search)
            let matchingKind = query.kind == .all || book.kind == query.kind.rawValue
            let matchingStatus: Bool
            switch query.status {
            case .all: matchingStatus = true
            case .unread: matchingStatus = !book.progress.completed && !started
            case .reading: matchingStatus = !book.progress.completed && started
            case .finished: matchingStatus = book.progress.completed
            }
            return matchingText && matchingKind && matchingStatus
        }
        switch query.sort {
        case .added: return filtered
        case .title: return filtered.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        case .author: return filtered.sorted { $0.author.localizedStandardCompare($1.author) == .orderedAscending }
        }
    }
    private func loadSeries(more: Bool) async {
        guard let api, !busy else { return }
        busy = true; defer { busy = false }
        do {
            let page = try await api.series(cursor: more ? seriesCursor : nil)
            try Task.checkCancellation()
            var ids = Set(seriesItems.map(\.id))
            seriesItems += page.items.filter { ids.insert($0.id).inserted }
            seriesCursor = page.nextCursor
        } catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }
}

private enum NativeBookKindFilter: String, CaseIterable, Identifiable, Hashable {
    case all
    case ebook
    case manga

    var id: Self { self }
    var title: String {
        switch self {
        case .all: "All books"
        case .ebook: "Books"
        case .manga: "Manga"
        }
    }
}

private enum NativeBookReadingFilter: CaseIterable, Identifiable, Hashable {
    case all
    case unread
    case reading
    case finished

    var id: Self { self }
    var title: String {
        switch self {
        case .all: "Any reading status"
        case .unread: "Not started"
        case .reading: "Continue reading"
        case .finished: "Finished"
        }
    }
}

private enum NativeBookSort: CaseIterable, Identifiable, Hashable {
    case added
    case title
    case author

    var id: Self { self }
    var title: String {
        switch self {
        case .added: "Recently added"
        case .title: "Title"
        case .author: "Author"
        }
    }
}

private struct NativeBookCatalogQuery: Hashable {
    let search: String
    let kind: NativeBookKindFilter
    let status: NativeBookReadingFilter
    let sort: NativeBookSort

    var hasFilters: Bool { kind != .all || status != .all || sort != .added }
}

private extension NativeBookKindFilter {
    var catalogKind: BookCatalogKind? {
        switch self {
        case .all: nil
        case .ebook: .ebook
        case .manga: .manga
        }
    }
}

private extension NativeBookReadingFilter {
    var catalogStatus: BookCatalogStatus {
        switch self {
        case .all: .all
        case .unread: .unread
        case .reading: .reading
        case .finished: .read
        }
    }
}

private extension NativeBookSort {
    var catalogSort: BookCatalogSort {
        switch self {
        case .added: .added
        case .title: .title
        case .author: .author
        }
    }
}

private struct NativeBookStack: View {
    let ids: [String]
    let title: String
    let covers: NativeBookCovers
    var count: Int = 0
    var body: some View {
        NativeBookCover(id: ids.first, title: title, covers: covers)
            .background {
                ForEach(1..<min(3, max(1, max(ids.count, count))), id: \.self) { index in
                    NativeBookCover(id: ids.indices.contains(index) ? ids[index] : ids.first, title: title, covers: covers)
                        .rotationEffect(.degrees(Double(index) * 3))
                        .offset(x: CGFloat(index) * 5, y: -CGFloat(index) * 4)
                }
            }
            .padding(.top, 10).padding(.trailing, 12)
    }
}

struct NativeBookCover: View {
    let id: String?
    let title: String
    var author = ""
    let covers: NativeBookCovers
    @State private var image: UIImage?
    init(id: String?, title: String, author: String = "", covers: NativeBookCovers) {
        self.id = id; self.title = title; self.author = author; self.covers = covers
    }
    var body: some View {
        Rectangle().fill(LuminaTheme.brand.opacity(0.14)).aspectRatio(2 / 3, contentMode: .fit)
            .overlay {
                if let image { Image(uiImage: image).resizable().scaledToFill() }
                else {
                    VStack(spacing: 12) {
                        Image(systemName: "book.closed").font(.title)
                        Text(title).font(.system(.headline, design: .serif)).multilineTextAlignment(.center).lineLimit(4)
                        if !author.isEmpty { Text(author).font(.caption).lineLimit(2) }
                    }.foregroundStyle(.primary).padding(12)
                }
            }
            .clipShape(.rect(cornerRadius: 7))
            .overlay(alignment: .leading) { Rectangle().fill(.black.opacity(0.12)).frame(width: 3).padding(.leading, 3) }
            .shadow(color: .black.opacity(0.16), radius: 4, y: 3)
            .task(id: id) {
                guard let id else { return }
                let prepared = await covers.image(id)
                guard !Task.isCancelled, let prepared else { return }
                image = UIImage(cgImage: prepared)
            }
            .onDisappear { image = nil }
            .accessibilityHidden(true)
    }
}

/// Session-local thumbnails, coalesced across shelves and the grid; all decode work stays on this actor.
actor NativeBookCovers {
    private final class Entry {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }
    let api: LuminaBooks
    private let cache = NSCache<NSString, Entry>()
    private var pending = [String: Task<CGImage?, Never>]()
    init(api: LuminaBooks) { self.api = api; cache.countLimit = 32; cache.totalCostLimit = 24 * 1024 * 1024 }
    func image(_ id: String) async -> CGImage? {
        if let image = cache.object(forKey: id as NSString)?.image { return image }
        if let task = pending[id] { return await task.value }
        let api = api
        let task = Task<CGImage?, Never> {
            do { return BookPagePreparation.image(from: try await api.cover(id: id), maximumPixelSize: 600) }
            catch { return nil }
        }
        pending[id] = task
        let image = await task.value
        pending[id] = nil
        if let image { cache.setObject(Entry(image), forKey: id as NSString, cost: image.bytesPerRow * image.height) }
        return image
    }
}
