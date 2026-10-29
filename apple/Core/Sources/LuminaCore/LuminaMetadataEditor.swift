import Foundation
import Observation

public struct MetadataDestination: Identifiable, Sendable {
    public enum Mode: Sendable, Equatable { case edit, match }
    public let id = UUID()
    public let titleID: String
    public let name: String
    public let year: Int?
    public let mode: Mode
}

public struct MetadataField: Decodable, Sendable {
    public let value: SettingsValue?
    public let locked: Bool
}

public struct MetadataDocument: Decodable, Sendable {
    public let titleID: String
    public let name: String
    public let type: String
    public let fields: [String: MetadataField]
    public let canIdentify: Bool
    public let tmdbConfigured: Bool
    public let seasons: [Season]
    public struct Season: Decodable, Identifiable, Sendable {
        public let id: String
        public let name: String
    }
    enum CodingKeys: String, CodingKey {
        case titleID = "title_id", canIdentify = "can_identify", tmdbConfigured = "tmdb_configured"
        case name, type, fields, seasons
    }
}

public struct MetadataMatch: Decodable, Identifiable, Sendable {
    public let tmdbID: Int
    public let name: String
    public let year: Int?
    public let overview: String?
    public var id: Int { tmdbID }
    enum CodingKeys: String, CodingKey { case tmdbID = "tmdb_id", name, year, overview }
}

/// Focused native editing over the existing server validation/history/conflict boundary.
@MainActor @Observable public final class LuminaMetadataEditor {
    public struct Field: Identifiable, Sendable {
        public enum Kind: Sendable, Equatable { case text, multiline, integer, decimal, list, season }
        public let id: String
        public let title: String
        public let kind: Kind
        init(_ id: String, _ title: String, _ kind: Kind = .text) { self.id = id; self.title = title; self.kind = kind }
    }
    public private(set) var canEdit = false
    public private(set) var canIdentify = false
    public var destination: MetadataDestination?
    public private(set) var document: MetadataDocument?
    public var draft = [String: String]()
    public var query = ""
    public var year = ""
    public private(set) var candidates: [MetadataMatch] = []
    public private(set) var hasSearched = false
    public private(set) var isLoading = false
    public private(set) var isSaving = false
    public private(set) var errorMessage: String?
    public private(set) var revision = 0
    @ObservationIgnored private var session: LuminaSession?
    @ObservationIgnored private let client: LuminaClient
    private var generation = 0

    public init() { client = LuminaClient() }
    init(client: LuminaClient) { self.client = client }
    public func configure(session next: LuminaSession?) {
        guard session?.accessToken != next?.accessToken || session?.serverURL != next?.serverURL || session?.userID != next?.userID else { return }
        session = next; generation += 1
        canEdit = false; canIdentify = false; destination = nil; document = nil
        draft = [:]; candidates = []; errorMessage = nil; isLoading = false; isSaving = false
    }

    public func reloadPermissions() async {
        guard let session else { return }
        let owner = generation
        struct Capabilities: Decodable { let can_edit_details: Bool; let can_identify: Bool }
        do {
            let result: Capabilities = try await request(session, path: "/capabilities")
            guard owner == generation else { return }
            canEdit = result.can_edit_details; canIdentify = result.can_identify
        } catch {
            guard owner == generation else { return }
            canEdit = false; canIdentify = false
        }
    }

    public func supports(_ item: MediaItem) -> Bool {
        ["Movie", "Series", "Season", "Episode", "MusicAlbum", "MusicArtist"].contains(item.type)
    }
    public func canMatch(_ item: MediaItem) -> Bool { canIdentify && ["Movie", "Series"].contains(item.type) }
    public func open(_ item: MediaItem, mode: MetadataDestination.Mode) {
        guard supports(item), canEdit, mode != .match || canMatch(item) else { return }
        destination = MetadataDestination(titleID: item.id, name: item.name, year: item.productionYear, mode: mode)
        document = nil; draft = [:]; candidates = []; hasSearched = false; errorMessage = nil
        query = item.name; year = item.productionYear.map(String.init) ?? ""
    }

    public var fields: [Field] {
        let all: [Field] = [
            Field("name", "Title"), Field("original_title", "Original title"), Field("sort_name", "Sort title"),
            Field("year", "Year", .integer), Field("overview", "Overview", .multiline), Field("tagline", "Tagline"),
            Field("premiered", "Premiere date (YYYY-MM-DD)"), Field("end_date", "End date (YYYY-MM-DD)"),
            Field("official_rating", "Content rating"), Field("community_rating", "Community rating", .decimal),
            Field("critic_rating", "Critic rating", .integer), Field("runtime_minutes", "Runtime (minutes)", .integer),
            Field("genres", "Genres", .list), Field("tags", "Tags", .list), Field("studios", "Studios", .list),
            Field("parent_id", "Season", .season), Field("index_number", document?.type == "season" ? "Season number" : "Episode number", .integer),
            Field("index_number_end", "Last episode number", .integer), Field("status", "Series status"),
            Field("display_order", "Episode order"), Field("edition", "Edition"), Field("label", "Label"), Field("country", "Country")
        ]
        return all.filter { document?.fields[$0.id] != nil && ($0.kind != .season || document?.seasons.isEmpty == false) }
    }
    public var hasChanges: Bool {
        fields.contains { draft[$0.id, default: ""] != Self.text(document?.fields[$0.id]?.value) }
    }

    public func load() async {
        guard let target = destination, let session else { return }
        let owner = generation
        isLoading = true; errorMessage = nil
        defer { if owns(target, owner) { isLoading = false } }
        do {
            let titleID = try component(target.titleID)
            await reloadPermissions()
            guard owns(target, owner) else { return }
            guard canEdit else { throw EditorError.message("Editing permissions could not be verified. Reconnect or sign in with an account allowed to edit this library.") }
            let doc: MetadataDocument = try await request(session, path: "/titles/\(titleID)")
            guard owns(target, owner) else { return }
            document = doc
            draft = doc.fields.mapValues { Self.text($0.value) }
            if target.mode == .match {
                guard doc.canIdentify, canIdentify else { throw EditorError.message("You no longer have permission to fix this match.") }
                guard doc.tmdbConfigured else { throw EditorError.message("TMDB is not configured on this server. Add a key in the server’s Media server settings, then try again.") }
                isLoading = false
                await search()
            }
        } catch {
            if owns(target, owner) {
                errorMessage = Self.message(error)
                if case EditorError.server(403, _) = error { await reloadPermissions() }
            }
        }
    }

    public func search() async {
        guard !isLoading, !isSaving, let target = destination, let session else { return }
        let owner = generation
        isLoading = true; errorMessage = nil; candidates = []; hasSearched = false
        defer { if owns(target, owner) { isLoading = false } }
        do {
            var params = [URLQueryItem(name: "q", value: query.trimmingCharacters(in: .whitespacesAndNewlines))]
            if !year.isEmpty {
                guard let value = Int(year), (1870...2100).contains(value) else { throw EditorError.message("Enter a year between 1870 and 2100, or leave it empty.") }
                params.append(URLQueryItem(name: "year", value: String(value)))
            }
            let result: [MetadataMatch] = try await request(session, path: "/titles/\(try component(target.titleID))/matches", query: params)
            guard owns(target, owner) else { return }
            candidates = result; hasSearched = true
        } catch {
            if owns(target, owner) {
                errorMessage = Self.message(error)
                if case EditorError.server(403, _) = error { await reloadPermissions() }
            }
        }
    }

    public func save() async -> Bool {
        guard canEdit, !isSaving, !isLoading, let doc = document else { return false }
        do {
            var changes: [String: SettingsValue] = [:]
            for field in fields where draft[field.id, default: ""] != Self.text(doc.fields[field.id]?.value) {
                changes[field.id] = .object(["value": try value(for: field), "base": doc.fields[field.id]?.value ?? .null])
            }
            guard !changes.isEmpty else { return false }
            return await mutate(path: "/edits", body: ["edits": .array([.object([
                "title_id": .string(doc.titleID), "changes": .object(changes)
            ])])])
        } catch { errorMessage = Self.message(error); return false }
    }

    public func identify(_ candidate: MetadataMatch) async -> Bool {
        guard let target = destination, canIdentify else { return false }
        do {
            return await mutate(path: "/titles/\(try component(target.titleID))/match", body: ["tmdb_id": .number(Double(candidate.tmdbID))])
        } catch { errorMessage = Self.message(error); return false }
    }

    private func mutate(path: String, body: [String: SettingsValue]) async -> Bool {
        guard !isSaving, let target = destination, let session else { return false }
        let owner = generation
        isSaving = true; errorMessage = nil
        defer { if owns(target, owner) { isSaving = false } }
        do {
            let result: SettingsValue = try await request(session, path: path, method: "POST", body: body)
            guard owns(target, owner) else { return false }
            if result["conflicts"]?.arrayValue?.isEmpty == false {
                throw EditorError.message("These details changed since you opened them. Reload to review the latest values; your edits have not been overwritten.")
            }
            revision += 1
            return true
        } catch {
            guard owns(target, owner) else { return false }
            errorMessage = Self.message(error)
            if case EditorError.server(403, _) = error { await reloadPermissions() }
            return false
        }
    }

    private func value(for field: Field) throws -> SettingsValue {
        let text = draft[field.id, default: ""].trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return field.id == "name" ? .string("") : .null }
        switch field.kind {
        case .integer:
            guard let number = Int(text) else { throw EditorError.message("Enter a whole number for \(field.title).") }
            return .number(Double(number))
        case .decimal:
            guard let number = Double(text.replacingOccurrences(of: ",", with: ".")), number.isFinite else { throw EditorError.message("Enter a number for \(field.title).") }
            return .number(number)
        case .list: return .array(text.split(whereSeparator: \.isNewline).map { .string($0.trimmingCharacters(in: .whitespaces)) })
        default: return .string(text)
        }
    }
    private static func text(_ value: SettingsValue?) -> String {
        if let value = value?.stringValue { return value }
        if let number = value?.numberValue {
            if number.isFinite, number.rounded() == number, number > Double(Int.min), number < Double(Int.max) { return String(Int(number)) }
            return String(number)
        }
        return value?.arrayValue?.compactMap(\.stringValue).joined(separator: "\n") ?? ""
    }
    private func owns(_ target: MetadataDestination, _ owner: Int) -> Bool { generation == owner && destination?.id == target.id }
    private func component(_ id: String) throws -> String {
        guard !id.isEmpty, id.count <= 64, id.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" }) else { throw EditorError.message("This title cannot be edited.") }
        return id
    }
    private enum EditorError: Error { case server(Int, String?), message(String) }
    private static func message(_ error: Error) -> String {
        switch error {
        case EditorError.message(let message): return message
        case EditorError.server(409, "tmdb_not_configured"): return "TMDB is not configured on this server."
        case EditorError.server(409, _): return "These details changed or are locked. Your edits are kept here. Reload to review the current details."
        case EditorError.server(403, _): return "Your account or this connected app no longer has permission for this action."
        case EditorError.server(401, _): return "Your session has expired. Sign in again."
        case EditorError.server(404, _): return "This title is unavailable, or this server needs an update to support native editing."
        case EditorError.server(422, _): return "Check your changes. One of the fields is invalid."
        default: return "Lumina could not complete this action. Check your connection and try again."
        }
    }
    private func request<T: Decodable>(_ session: LuminaSession, path: String, method: String = "GET",
                                       query: [URLQueryItem] = [], body: [String: SettingsValue]? = nil) async throws -> T {
        var req = client.request(origin: session.serverURL, path: "/Lumina/Metadata" + path, method: method,
                                 query: query, token: session.accessToken, deviceID: session.deviceID)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        if let body { req.httpBody = try JSONEncoder().encode(body); req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await client.response(req)
        guard (200..<300).contains(response.statusCode) else {
            let payload = try? JSONDecoder().decode(SettingsValue.self, from: data)
            if response.statusCode == 422, let key = payload?["field"]?.stringValue {
                let label = fields.first { $0.id == key }?.title ?? key
                let reason = payload?["reason"]?.stringValue?.replacingOccurrences(of: "_", with: " ") ?? "invalid value"
                throw EditorError.message("Check \(label): \(reason).")
            }
            throw EditorError.server(response.statusCode, payload?["detail"]?.stringValue)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
