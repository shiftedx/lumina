import Foundation
import CoreGraphics
import ImageIO

public struct BookItem: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let author: String
    public let format: String
    public let kind: String
    public let seriesID: String?
    public let available: Bool
    public let readingFileID: String
    public let files: [BookFile]
    public let progress: BookProgress
    public let coverURL: String?
    enum CodingKeys: String, CodingKey {
        case id, title, author, format, kind, available, files, progress
        case readingFileID = "reading_file_id"
        case coverURL = "cover_url"
        case seriesID = "series_id"
    }
}

public struct BookFile: Decodable, Identifiable, Sendable {
    public let id: String
    public let filename: String
    public let format: String
    public let available: Bool
}

public struct BookProgress: Decodable, Sendable {
    public let unitIndex: Int
    public let offset: Double
    public let completed: Bool
    public let percent: Double?
    enum CodingKeys: String, CodingKey { case unitIndex = "unit_index", offset, completed, percent }
}

public struct BooksOverview: Decodable, Sendable {
    public let continueReading: [BookItem]
    public let shelves: [BookShelf]
    enum CodingKeys: String, CodingKey { case continueReading = "continue_reading", shelves }
}

public struct BookShelf: Decodable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let count: Int
    public let bookIDs: [String]
    public let covers: [String]?
    public var coverBookIDs: [String] { (covers ?? []).compactMap { bookCoverID($0) } }
    enum CodingKeys: String, CodingKey { case id, name, count, covers, bookIDs = "book_ids" }
}

public struct BookSeries: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let author: String
    public let coverURL: String?
    public let volumeCount: Int
    enum CodingKeys: String, CodingKey { case id, title, author, coverURL = "cover_url", volumeCount = "volume_count" }
    public var coverBookID: String? { bookCoverID(coverURL) }
}

private func bookCoverID(_ path: String?) -> String? {
    guard let path else { return nil }
    let parts = path.split(separator: "/")
    guard parts.count == 4, path == "/api/books/\(parts[2])/cover", UUID(uuidString: String(parts[2])) != nil else { return nil }
    return String(parts[2])
}

public struct BookSeriesCatalog: Decodable, Sendable {
    public let items: [BookSeries]
    public let nextCursor: String?
    enum CodingKeys: String, CodingKey { case items, nextCursor = "next_cursor" }
}

public struct BookSeriesDetail: Decodable, Sendable { public let books: [BookItem] }

public struct BookCatalog: Decodable, Sendable {
    public let items: [BookItem]
    public let nextCursor: String?
    enum CodingKeys: String, CodingKey { case items, nextCursor = "next_cursor" }
}

public enum BookCatalogKind: String, Sendable {
    case ebook
    case manga
}

public enum BookCatalogStatus: String, Sendable {
    case all
    case unread
    case reading
    case read
    case favorites
    case readingList = "reading_list"
}

public enum BookCatalogSort: String, Sendable {
    case added
    case title
    case author
    case custom
}

public struct BookManifest: Decodable, Sendable {
    public struct Chapter: Decodable, Identifiable, Sendable {
        public let id: Int
        public let label: String
        private enum CodingKeys: String, CodingKey { case label }
        public init(from decoder: Decoder) throws {
            id = decoder.codingPath.last?.intValue ?? 0
            label = try decoder.container(keyedBy: CodingKeys.self).decode(String.self, forKey: .label)
        }
    }
    public let book: BookItem
    public let chapters: [Chapter]
    public let pageCount: Int
    public let readingDirection: String
    public var count: Int { book.format == "epub" ? chapters.count : pageCount }
    enum CodingKeys: String, CodingKey {
        case book, chapters, pageCount = "page_count", readingDirection = "reading_direction"
    }
}

public struct BookNode: Decodable, Sendable {
    public let tag: String
    public let id: String?
    public let text: String?
    public let children: [BookNode]?
    public let member: String?
    public let alt: String?
    public let chapter: Int?
    public let anchor: String?
}

/// A reader owns this member-bound client. No cookie jar, public resource URL, or shared disk cache.
public struct LuminaBooks: Sendable {
    private let owner: LuminaSession
    private let client: LuminaClient
    public init(session: LuminaSession) { owner = session; client = LuminaClient() }
    init(session: LuminaSession, client: LuminaClient) { owner = session; self.client = client }

    public func catalog(
        search: String,
        cursor: String? = nil,
        shelfID: String? = nil,
        kind: BookCatalogKind? = nil,
        status: BookCatalogStatus = .all,
        sort: BookCatalogSort = .added
    ) async throws -> BookCatalog {
        var query = [URLQueryItem(name: "q", value: String(search.prefix(200)))]
        if let cursor { query.append(URLQueryItem(name: "cursor", value: cursor)) }
        if let shelfID { query.append(URLQueryItem(name: "shelf_id", value: try safeID(shelfID))) }
        if let kind { query.append(URLQueryItem(name: "kind", value: kind.rawValue)) }
        if status != .all { query.append(URLQueryItem(name: "status", value: status.rawValue)) }
        if sort != .added { query.append(URLQueryItem(name: "sort", value: sort.rawValue)) }
        return try await json("", query: query)
    }
    public func overview() async throws -> BooksOverview { try await json("/overview") }
    public func series(cursor: String? = nil) async throws -> BookSeriesCatalog {
        try await json("/series", query: cursor.map { [URLQueryItem(name: "cursor", value: $0)] } ?? [])
    }
    public func series(id: String) async throws -> BookSeriesDetail { try await json("/series/\(try safeID(id))") }
    public func cover(id: String) async throws -> Data {
        let path = "/\(try safeID(id))/cover"
        let delays = [1.0, 1.5, 2.5, 4, 6, 8, 10]
        for attempt in 0...delays.count {
            do { return try await raster(path) }
            catch LuminaError.http(202) where attempt < delays.count {
                try await Task.sleep(for: .seconds(delays[attempt]))
            }
        }
        throw LuminaError.malformedResponse
    }
    public func manifest(id: String) async throws -> BookManifest { try await json("/\(try safeID(id))/reader") }
    public func chapter(id: String, index: Int) async throws -> [BookNode] {
        struct Chapter: Decodable { let nodes: [BookNode] }
        let chapter: Chapter = try await json("/\(try safeID(id))/chapters/\(max(0, index))")
        return chapter.nodes
    }
    public func page(id: String, index: Int) async throws -> Data {
        try await raster("/\(try safeID(id))/pages/\(max(0, index))")
    }
    public func resource(id: String, member: String) async throws -> Data {
        guard member.utf8.count <= 1024 else { throw LuminaError.unsafeURL }
        return try await raster("/\(try safeID(id))/resources", query: [URLQueryItem(name: "member", value: member)])
    }
    public func save(id: String, unit: Int, offset: Double, completed: Bool? = nil) async throws -> BookItem {
        var body: [String: Any] = ["unit_index": max(0, unit), "offset": offset.isFinite ? min(1, max(0, offset)) : 0]
        if let completed { body["completed"] = completed }
        return try await json("/\(try safeID(id))/state", method: "PATCH", body: body)
    }
    private func safeID(_ id: String) throws -> String {
        guard UUID(uuidString: id) != nil else { throw LuminaError.unsafeURL }
        return id
    }
    private func request(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: [String: Any]? = nil) -> URLRequest {
        var request = client.request(origin: owner.serverURL, path: "/Lumina/Books" + path, method: method,
                                     query: query, token: owner.accessToken, deviceID: owner.deviceID, body: body)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if method == "PATCH" { request.timeoutInterval = 5 }
        return request
    }
    private func json<Value: Decodable>(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: [String: Any]? = nil) async throws -> Value {
        let data = try await client.send(request(path, method: method, query: query, body: body))
        try Task.checkCancellation()
        return try JSONDecoder().decode(Value.self, from: data)
    }
    private func raster(_ path: String, query: [URLQueryItem] = []) async throws -> Data {
        let data = try await client.bookImage(request(path, query: query))
        try Task.checkCancellation()
        guard !data.isEmpty, data.count <= BookPagePreparation.maximumEncodedBytes else { throw LuminaError.malformedResponse }
        return data
    }
}

/// Downsample before decoding pixels. Call from a worker, never a SwiftUI body.
public enum BookPagePreparation {
    public static let maximumEncodedBytes = 24 * 1024 * 1024
    public static let maximumPixelSize = 3072
    public static func image(from data: Data, maximumPixelSize: Int) -> CGImage? {
        guard data.count <= maximumEncodedBytes,
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
              width.doubleValue > 0, height.doubleValue > 0,
              width.doubleValue * height.doubleValue <= 40_000_000 else { return nil }
        return CGImageSourceCreateThumbnailAtIndex(source, 0, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: min(Self.maximumPixelSize, max(1, maximumPixelSize)),
        ] as CFDictionary)
    }
}

/// A bounded encoded-page window. A failed speculative request is discarded so an explicit retry can fetch again.
public actor BookPageWindow {
    private let load: @Sendable (Int) async throws -> Data
    private var cache = [Int: Data]()
    private var lookahead: (index: Int, task: Task<Data, Error>)?

    public init(api: LuminaBooks, bookID: String) {
        load = { index in try await api.page(id: bookID, index: index) }
    }

    init(load: @escaping @Sendable (Int) async throws -> Data) {
        self.load = load
    }

    public func bytes(_ index: Int) async throws -> Data {
        if let data = cache[index] { return data }
        let data: Data
        if let pending = lookahead, pending.index == index {
            lookahead = nil
            data = try await pending.task.value
        } else {
            data = try await load(index)
        }
        try Task.checkCancellation()
        cache[index] = data
        return data
    }

    public func retain(_ visible: [Int], next: Int?) {
        if let pending = lookahead, !visible.contains(pending.index), pending.index != next {
            pending.task.cancel()
            lookahead = nil
        }
        cache = cache.filter { visible.contains($0.key) }
        guard let next, cache[next] == nil, lookahead == nil,
              cache.values.reduce(0, { $0 + $1.count }) <= 24 * 1024 * 1024 else { return }
        let load = load
        lookahead = (next, Task { try await load(next) })
    }

    public func stop() {
        lookahead?.task.cancel()
        lookahead = nil
        cache = [:]
    }
}

/// Stable consecutive spreads: 1–2, 3–4, 5–6. Artwork is never mirrored.
public enum BookSpread {
    public static func start(current: Int) -> Int { max(0, current) / 2 * 2 }
    public static func primary(current: Int, count: Int, rightToLeft: Bool) -> Int? {
        let right = start(current: current) + (rightToLeft ? 0 : 1)
        return (0..<count).contains(right) ? right : nil
    }
    public static func companion(current: Int, count: Int, rightToLeft: Bool) -> Int? {
        let left = start(current: current) + (rightToLeft ? 1 : 0)
        return (0..<count).contains(left) ? left : nil
    }
}

public struct BookReaderLayout: Equatable, Sendable {
    public let primary: CGRect
    public let companion: CGRect?
    public init(size: CGSize, expanded: Bool, divisions: [CGRect] = [], occlusions: [CGRect] = [], artwork: Bool = false) {
        var area = CGRect(origin: .zero, size: size).insetBy(dx: artwork ? 0 : 16, dy: artwork ? 0 : 12)
        // A camera occlusion is rare and small; reserve its complete top/bottom strip rather than cover text.
        for camera in occlusions where area.intersects(camera) {
            if camera.midY < area.midY {
                let bottom = area.maxY; area.origin.y = camera.maxY + 12; area.size.height = bottom - area.minY
            } else { area.size.height = camera.minY - 12 - area.minY }
        }
        area.size.width = max(1, area.width); area.size.height = max(1, area.height)
        if let fold = divisions.first(where: { area.intersects($0) }) {
            let before: CGRect, after: CGRect
            if fold.width > fold.height {
                let top = min(area.maxY, max(area.minY, fold.minY - 12))
                let bottom = min(area.maxY, max(area.minY, fold.maxY + 12))
                before = CGRect(x: area.minX, y: area.minY, width: area.width, height: top - area.minY)
                after = CGRect(x: area.minX, y: bottom, width: area.width, height: area.maxY - bottom)
            } else {
                let left = min(area.maxX, max(area.minX, fold.minX - 12))
                let right = min(area.maxX, max(area.minX, fold.maxX + 12))
                before = CGRect(x: area.minX, y: area.minY, width: left - area.minX, height: area.height)
                after = CGRect(x: right, y: area.minY, width: area.maxX - right, height: area.height)
            }
            // Split View may expose only one side of a fold. Never manufacture an offscreen pane.
            if expanded, !before.isEmpty, !after.isEmpty {
                primary = after; companion = before
            } else {
                primary = after.isEmpty ? before : after
                companion = nil
            }
        } else if expanded {
            let width = max(1, (area.width - (artwork ? 8 : 24)) / 2)
            companion = CGRect(x: area.minX, y: area.minY, width: width, height: area.height)
            primary = CGRect(x: area.maxX - width, y: area.minY, width: width, height: area.height)
        } else {
            primary = area; companion = nil
        }
    }
}
