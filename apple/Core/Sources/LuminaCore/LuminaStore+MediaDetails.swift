import Foundation

extension LuminaStore {
    public func loadChildren(of item: MediaItem, useCache: Bool = false) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard item.isCollection else { return [] }
        let cacheKey = MediaMetadataCache.key("children/\(item.id)/\(metadata.revision)", session: active)
        if useCache, let cached = await metadataCache?.items(for: cacheKey), isCurrent(active) { return cached }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        var result = [MediaItem]()
        var start = 0
        while true {
            let page = try await client.children(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, item: item, start: start)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            let supported: [String] = switch item.type {
            case "Series": ["Season"]
            case "Season": ["Episode"]
            case "BoxSet": ["Movie"]
            case "Playlist": ["Movie", "Series", "Season", "Episode", "Video"]
            default: []
            }
            result.append(contentsOf: page.items.filter { supported.contains($0.type) })
            start += page.items.count
            if item.type == "Series" || page.items.isEmpty || start >= page.total {
                let ordered: [MediaItem]
                if item.type == "Playlist" {
                    ordered = result
                } else if item.type == "BoxSet" {
                    ordered = result.sorted { ($0.productionYear ?? Int.max, $0.name, $0.id) < ($1.productionYear ?? Int.max, $1.name, $1.id) }
                } else {
                    ordered = result.sorted { ($0.indexNumber ?? Int.max, $0.id) < ($1.indexNumber ?? Int.max, $1.id) }
                }
                if useCache { await metadataCache?.store(ordered, for: cacheKey) }
                guard isCurrent(active) else { throw LuminaError.notSignedIn }
                return ordered
            }
        }
    }

    public func loadItem(_ item: MediaItem, useCache: Bool = false) async throws -> MediaItem {
        guard let active = session else { throw LuminaError.notSignedIn }
        let cacheKey = MediaMetadataCache.key("item/\(item.id)/\(metadata.revision)", session: active)
        if useCache, let cached = await metadataCache?.items(for: cacheKey)?.first,
           cached.id == item.id, cached.userData == item.userData, isCurrent(active) { return cached }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        do {
            let fresh = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            replaceCachedItem(fresh)
            await metadataCache?.store([fresh], for: cacheKey)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            return fresh
        } catch {
            if isCurrent(active), let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    /// Resolves a Requests-provided vault title without replacing the active browse page.
    public func libraryItem(id: String, useCache: Bool = false) async throws -> MediaItem {
        guard !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw LuminaError.malformedResponse
        }
        guard let active = session else { throw LuminaError.notSignedIn }
        let cacheKey = MediaMetadataCache.key("item/\(canonicalItemID(id))/\(metadata.revision)", session: active)
        if useCache, let cached = await metadataCache?.items(for: cacheKey)?.first,
           canonicalItemID(cached.id) == canonicalItemID(id), isCurrent(active) { return cached }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        do {
            let fresh = try await client.item(
                origin: active.serverURL,
                token: active.accessToken,
                deviceID: active.deviceID,
                id: id,
                userID: active.userID
            )
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            guard canonicalItemID(fresh.id) == canonicalItemID(id) else { throw LuminaError.malformedResponse }
            replaceCachedItem(fresh)
            await metadataCache?.store([fresh], for: cacheKey)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            return fresh
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) {
                clearSession()
            }
            throw error
        }
    }

    /// Resolves a concrete music pivot without changing the active browse page.
    /// TV uses this for album and artist tiles that were sourced from a related
    /// media item rather than the current audio catalogue.
    public func musicCollection(id: String, expectedType: String) async throws -> MediaItem {
        guard !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              ["MusicAlbum", "MusicArtist"].contains(expectedType) else {
            throw LuminaError.malformedResponse
        }
        guard let active = session else { throw LuminaError.notSignedIn }
        let requestedID = canonicalItemID(id)
        do {
            let fresh = try await client.item(
                origin: active.serverURL,
                token: active.accessToken,
                deviceID: active.deviceID,
                id: requestedID,
                userID: active.userID
            )
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            guard canonicalItemID(fresh.id) == requestedID,
                  fresh.type == expectedType else { throw LuminaError.malformedResponse }
            replaceCachedItem(fresh)
            return fresh
        } catch {
            if isCurrent(active), let failure = error as? LuminaError,
               failure == .http(401) || failure == .http(403) {
                clearSession()
            }
            throw error
        }
    }

    /// Resolves the parent series for an episode without replacing the active browse page.
    public func seriesCollection(id: String, useCache: Bool = false) async throws -> MediaItem {
        let fresh = try await libraryItem(id: id, useCache: useCache)
        guard fresh.id == id, fresh.type == "Series" else { throw LuminaError.malformedResponse }
        return fresh
    }

    public func updateItemState(item: MediaItem, played: Bool? = nil, favorite: Bool? = nil) async throws -> MediaItem {
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            if played != nil || favorite != nil {
                try await client.updateItemState(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, userID: active.userID, id: item.id, played: played, favorite: favorite)
            }
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            metadataCache?.clear()
            return try await loadItem(item)
        } catch {
            if isCurrent(active), let failure = error as? LuminaError, failure == .http(401) || failure == .http(403) { clearSession() }
            throw error
        }
    }

    /// Jellyfin commonly represents a UUID link with hyphens, while its item
    /// endpoint returns the same value in compact lowercase form.
    /// Non-UUID fixture and provider identifiers deliberately remain byte-for-byte
    /// unchanged.
    private func canonicalItemID(_ id: String) -> String {
        let compact = id.replacingOccurrences(of: "-", with: "")
        guard compact.utf8.count == 32,
              compact.unicodeScalars.allSatisfy({ CharacterSet(charactersIn: "0123456789abcdefABCDEF").contains($0) }) else {
            return id
        }
        let parts = [
            String(compact.prefix(8)),
            String(compact.dropFirst(8).prefix(4)),
            String(compact.dropFirst(12).prefix(4)),
            String(compact.dropFirst(16).prefix(4)),
            String(compact.dropFirst(20)),
        ]
        guard let uuid = UUID(uuidString: parts.joined(separator: "-")) else { return id }
        return uuid.uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }

    public func musicChildren(of item: MediaItem) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard ["MusicAlbum", "MusicArtist"].contains(item.type) else { return [] }
        var result = [MediaItem]()
        var start = 0
        while true {
            let page = try await client.items(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, search: "", start: start, types: item.type == "MusicArtist" ? "MusicAlbum" : "Audio", parentID: item.id)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            result.append(contentsOf: page.items)
            start += page.items.count
            if page.items.isEmpty || start >= page.total { return result }
        }
    }

    public func relatedItems(for item: MediaItem, useCache: Bool = false) async throws -> [MediaItem] {
        guard let active = session else { throw LuminaError.notSignedIn }
        let cacheKey = MediaMetadataCache.key("related/\(item.id)/\(metadata.revision)", session: active)
        if useCache, let cached = await metadataCache?.items(for: cacheKey), isCurrent(active) { return cached }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let loaded = try await client.relatedItems(
            origin: active.serverURL,
            token: active.accessToken,
            deviceID: active.deviceID,
            itemID: item.id
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        var seen = Set<String>()
        let result = loaded.filter { related in
            related.id != item.id && seen.insert(related.id).inserted
        }.prefix(12).map { $0 }
        if useCache { await metadataCache?.store(result, for: cacheKey) }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return result
    }

    public func personImageURL(for person: MediaPerson, maxWidth: Int = 240) -> URL? {
        guard let active = session,
              let id = person.id, !id.isEmpty,
              let tag = person.primaryImageTag, !tag.isEmpty else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(id)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func posterURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        if item.parentPrimaryImageItemID != nil, item.parentPrimaryImageTag != nil {
            return imageURL(for: item, maxWidth: maxWidth)
        }
        guard ["Episode", "Season"].contains(item.type),
              let seriesID = item.seriesID, let tag = item.seriesPrimaryImageTag,
              let active = session else { return imageURL(for: item, maxWidth: maxWidth) }
        return client.url(origin: active.serverURL, path: "/Items/\(seriesID)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    public func imageURL(for item: MediaItem, maxWidth: Int = 400) -> URL? {
        if let artwork = offline.artworkURL(for: item.id) { return artwork }
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if item.isAudio, let albumID = item.albumID, let albumTag = item.albumPrimaryImageTag {
            imageID = albumID
            tag = albumTag
        } else if let parentID = item.parentPrimaryImageItemID, let parentTag = item.parentPrimaryImageTag {
            imageID = parentID
            tag = parentTag
        } else if let ownTag = item.imageTags?["Primary"] {
            imageID = item.id
            tag = ownTag
        } else if ["Episode", "Season"].contains(item.type), let seriesID = item.seriesID, let seriesTag = item.seriesPrimaryImageTag {
            imageID = seriesID
            tag = seriesTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Primary", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    /// Warms the next visible slice without making scrolling views own fetch tasks per cell.
    public func prefetchArtwork(after itemID: String?, in items: [MediaItem], maxWidth: Int = 400, count: Int = 16, movingForward: Bool = true) async {
        guard !Task.isCancelled, let itemID, let index = items.firstIndex(where: { $0.id == itemID }) else { return }
        let count = min(72, max(0, count))
        let candidates = movingForward
            ? Array(items.dropFirst(index + 1).prefix(count))
            : Array(items.prefix(index).suffix(count).reversed())
        let urls = candidates.compactMap { imageURL(for: $0, maxWidth: maxWidth) }
        await ArtworkImageCache.shared.prefetch(urls, limit: count)
    }

    public func backdropFraming(for item: MediaItem) async -> HeroFraming? {
        guard let source = backdropURL(for: item) else { return nil }
        return await artworkFraming(for: source)
    }

    public func logoURL(for item: MediaItem, maxWidth: Int = 600) -> URL? {
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if let ownTag = item.imageTags?["Logo"] {
            imageID = item.id; tag = ownTag
        } else if let parentID = item.parentLogoItemID, let parentTag = item.parentLogoImageTag {
            imageID = parentID; tag = parentTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Logo", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }

    /// Browse tile artwork, independently of hero presentation.
    public func thumbnailURL(for item: MediaItem, poster: Bool = false, maxWidth: Int = 760) -> URL? {
        if poster { return posterURL(for: item, maxWidth: maxWidth) }
        if item.type == "Episode", let tag = item.imageTags?["Primary"], !tag.isEmpty, let active = session {
            return client.url(origin: active.serverURL, path: "/Items/\(item.id)/Images/Primary", query: [
                URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
                URLQueryItem(name: "tag", value: tag),
                URLQueryItem(name: "ApiKey", value: active.accessToken),
            ])
        }
        return backdropURL(for: item, maxWidth: maxWidth) ?? imageURL(for: item, maxWidth: maxWidth)
    }

    public func backdropURL(for item: MediaItem, maxWidth: Int = 1920) -> URL? {
        guard let active = session else { return nil }
        let imageID: String
        let tag: String
        if let ownTag = item.backdropImageTags?.first {
            imageID = item.id
            tag = ownTag
        } else if let parentID = item.parentBackdropItemID, let parentTag = item.parentBackdropImageTags?.first {
            imageID = parentID
            tag = parentTag
        } else { return nil }
        return client.url(origin: active.serverURL, path: "/Items/\(imageID)/Images/Backdrop/0", query: [
            URLQueryItem(name: "maxWidth", value: String(max(1, maxWidth))),
            URLQueryItem(name: "tag", value: tag),
            URLQueryItem(name: "ApiKey", value: active.accessToken),
        ])
    }
}
