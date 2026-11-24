import Foundation
import Observation

public extension RequestCatalogItem {
    var vaultTitleID: String? {
        guard let id = status.libraryTitleID?.trimmingCharacters(in: .whitespacesAndNewlines),
              !id.isEmpty else { return nil }
        return id
    }

    var canRequestMore: Bool {
        vaultTitleID != nil && (status.canRequestLanguageVariant
            || (mediaType == .tv && status.state == .partiallyAvailable))
    }

    var hasRequestChoices: Bool { status.canCreateRequest || status.canRequestLanguageVariant }
}

public extension RequestCatalogDetail {
    var latestRegularSeason: Int? { seasons.lazy.map(\.number).filter { $0 > 0 }.max() }
}

/// The request form shared by iOS and tvOS. Each screen owns detail loading and quota refresh.
@MainActor @Observable public final class RequestDraft {
    public enum SeasonMode: Hashable {
        case all, latest, chosen
    }

    public var seasonMode: SeasonMode = .all
    public var selectedSeasons: Set<Int> = []
    public var audioLanguage = ""
    public var subtitleLanguage = ""
    public private(set) var submitting = false
    public private(set) var created: RequestRecord?

    private var language: RequestLanguage?
    private var languageRequired = false

    public init() {}

    public func needsLanguage(for item: RequestCatalogItem) -> Bool {
        languageRequired || (item.kind == .anime && item.mediaType == .tv)
    }

    public func languagePreset(for item: RequestCatalogItem) -> RequestLanguage? {
        guard item.status.canRequestLanguageVariant else { return language }
        if audioLanguage == "en" && subtitleLanguage.isEmpty { return .dub }
        if audioLanguage == "ja" && subtitleLanguage == "en" { return .sub }
        return nil
    }

    public func setLanguagePreset(_ preset: RequestLanguage?, for item: RequestCatalogItem) {
        language = preset
        if item.status.canRequestLanguageVariant, let preset {
            audioLanguage = preset == .dub ? "en" : "ja"
            subtitleLanguage = preset == .dub ? "" : "en"
        }
    }

    public var hasLanguagePreference: Bool { !audioLanguage.isEmpty || !subtitleLanguage.isEmpty }

    public func seasons(for item: RequestCatalogItem, detail: RequestCatalogDetail?) -> RequestSeasonSelection? {
        guard item.mediaType == .tv else { return nil }
        switch seasonMode {
        case .all: return .all
        case .latest: return detail?.latestRegularSeason.map { .selected([$0]) }
        case .chosen: return selectedSeasons.isEmpty ? nil : .selected(selectedSeasons.sorted())
        }
    }

    /// tvOS requires a successful quota check before calling this policy.
    /// iOS allows the server to decide when no allowance is available locally.
    public func canSubmit(_ item: RequestCatalogItem, detail: RequestCatalogDetail?, quota: RequestQuota?) -> Bool {
        guard !submitting, created == nil, item.canRequest, item.hasRequestChoices,
              item.vaultTitleID == nil || item.canRequestMore,
              item.status.canCreateRequest || hasLanguagePreference,
              quota?.canRequest != false, quota?.remaining != 0,
              item.mediaType != .tv || seasons(for: item, detail: detail) != nil else { return false }
        if needsLanguage(for: item) {
            return item.status.canRequestLanguageVariant ? hasLanguagePreference : language != nil
        }
        return true
    }

    public func submit(
        _ item: RequestCatalogItem,
        detail: RequestCatalogDetail?,
        quota: RequestQuota?,
        using catalog: LuminaRequests,
        refreshQuotas: () async -> Void
    ) async throws {
        guard canSubmit(item, detail: detail, quota: quota) else { return }
        submitting = true
        defer { submitting = false }

        let supportsLanguages = item.status.canRequestLanguageVariant
        do {
            created = try await catalog.create(
                item,
                seasons: seasons(for: item, detail: detail),
                language: languagePreset(for: item),
                audioLanguage: supportsLanguages && !audioLanguage.isEmpty ? audioLanguage : nil,
                subtitleLanguage: supportsLanguages && !subtitleLanguage.isEmpty ? subtitleLanguage : nil
            )
        } catch {
            if case RequestAPIError.server(_, "language_required") = error { languageRequired = true }
            throw error
        }
        await refreshQuotas()
        await catalog.loadHome()
    }
}
