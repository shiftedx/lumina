import Foundation

extension LuminaStore {
    public func preparePlayback(for item: MediaItem, forceTranscode: Bool = false,
                                quality: PlaybackQuality? = nil,
                                audioStreamIndex: Int? = nil, subtitleStreamIndex: Int? = nil,
                                mediaSourceID: String? = nil, startPositionSeconds: Double? = nil,
                                forceCompatibleVideo: Bool = false,
                                usesServerVideoRecovery: Bool = false) async throws -> PlaybackRequest {
        if let local = offline.localURL(for: item.id) {
            return PlaybackRequest(id: UUID().uuidString, item: item, url: local,
                                   startPositionSeconds: startPositionSeconds ?? (item.isAudio ? 0 : offline.resumeSeconds(for: item.id)),
                                   mediaSourceID: item.id, isTranscoding: false,
                                   methodLabel: item.isAudio ? "Downloaded audio" : "Downloaded video",
                                   usesClientDemux: !item.isAudio && local.pathExtension.lowercased() == "mkv",
                                   sourceContainer: local.pathExtension.isEmpty ? nil : local.pathExtension)
        }
        guard !isOffline, let active = session else { throw LuminaError.notSignedIn }
        let current = try await client.item(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, id: item.id, userID: active.userID)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        if current.isAudio { return try client.audioRequest(origin: active.serverURL, token: active.accessToken, item: current, forceCompatible: forceTranscode) }
        let resolvedQuality = quality ?? playbackPreferences.quality
        let result = try await client.playbackInfo(origin: active.serverURL, token: active.accessToken,
                                                    deviceID: active.deviceID, item: current,
                                                    forceTranscode: forceTranscode,
                                                    quality: resolvedQuality,
                                                    audioStreamIndex: audioStreamIndex,
                                                    subtitleStreamIndex: subtitleStreamIndex,
                                                    mediaSourceID: mediaSourceID,
                                                    startPositionSeconds: startPositionSeconds,
                                                    forceCompatibleVideo: forceCompatibleVideo)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let source = try client.preferredSource(in: result, mediaSourceID: mediaSourceID)
        let streams = try client.playbackStreams(for: source, origin: active.serverURL)
        let preferredAudio = audioStreamIndex ?? playbackPreferences.preferredAudioStreamIndex(in: streams)
        let preferredSubtitle = subtitleStreamIndex ?? playbackPreferences.preferredSubtitleStreamIndex(in: streams)
        let selectedExternalSubtitle = preferredSubtitle.flatMap { index in
            index >= 0 ? streams.first { $0.index == index && $0.hasExternalSubtitle } : nil
        }
        // AVFoundation cannot append a sidecar legible group to a progressive MP4. Preserve
        // the qualified local packaging route, but ask the server for its compatible HLS decision
        // before handing a direct MP4 sidecar to the playback layer.
        let usesClientPackaging = ["mkv", "matroska"].contains((source.container ?? current.container ?? "").lowercased())
            || (source.container?.lowercased() == "m2ts" && source.clientPackaging == "p7_m2ts_to_p81_drop_el")
        let requiresServerHLS = selectedExternalSubtitle != nil && source.supportsDirectPlay && !usesClientPackaging
        let needsSelectionRequest = (audioStreamIndex == nil && preferredAudio != nil && preferredAudio != source.defaultAudioStreamIndex)
            || (subtitleStreamIndex == nil && preferredSubtitle != nil && preferredSubtitle != source.defaultSubtitleStreamIndex)
            || requiresServerHLS

        if needsSelectionRequest {
            let selected = try await client.playbackInfo(origin: active.serverURL, token: active.accessToken,
                                                          deviceID: active.deviceID, item: current,
                                                          forceTranscode: forceTranscode || requiresServerHLS,
                                                          quality: resolvedQuality,
                                                          audioStreamIndex: preferredAudio,
                                                          subtitleStreamIndex: preferredSubtitle,
                                                          mediaSourceID: source.id,
                                                          startPositionSeconds: startPositionSeconds,
                                                          forceCompatibleVideo: forceCompatibleVideo)
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            let selectedSource = try client.preferredSource(in: selected, mediaSourceID: source.id)
            let selectedSourceChapters = try await chaptersForSelectedPlaybackSource(
                selectedSource, loadedItem: current, active: active
            )
            let request = try client.playbackRequest(origin: active.serverURL, token: active.accessToken, item: current,
                                                      result: selected, quality: resolvedQuality,
                                                      audioStreamIndex: preferredAudio, subtitleStreamIndex: preferredSubtitle,
                                                      mediaSourceID: source.id, startPositionSeconds: startPositionSeconds,
                                                      selectedSourceChapters: selectedSourceChapters,
                                                      usesCompatibleVideo: forceCompatibleVideo,
                                                      usesServerVideoRecovery: usesServerVideoRecovery)
            guard !requiresServerHLS || request.isTranscoding else { throw LuminaError.malformedResponse }
            return request
        }
        let selectedSourceChapters = try await chaptersForSelectedPlaybackSource(
            source, loadedItem: current, active: active
        )
        return try client.playbackRequest(origin: active.serverURL, token: active.accessToken, item: current,
                                          result: result, quality: resolvedQuality,
                                          audioStreamIndex: preferredAudio, subtitleStreamIndex: preferredSubtitle,
                                          mediaSourceID: source.id, startPositionSeconds: startPositionSeconds,
                                          selectedSourceChapters: selectedSourceChapters,
                                          usesCompatibleVideo: forceCompatibleVideo,
                                          usesServerVideoRecovery: usesServerVideoRecovery)
    }

    /// Keeps the source and selections approved by the resume chooser.
    public func preparePlaybackFromBeginning(after request: PlaybackRequest) async throws -> PlaybackRequest {
        // Original-file URLs have no server-owned start offset. Reuse the decision
        // already made for this chooser instead of repeating potentially slow probes.
        if !request.isTranscoding {
            return PlaybackRequest(
                id: request.id, item: request.item, url: request.url,
                startPositionSeconds: 0, mediaSourceID: request.mediaSourceID,
                isTranscoding: false, methodLabel: request.methodLabel,
                usesClientDemux: request.usesClientDemux,
                usesCompatibleVideo: request.usesCompatibleVideo,
                usesServerVideoRecovery: request.usesServerVideoRecovery,
                usesServerMediaRecovery: request.usesServerMediaRecovery,
                bitRate: request.bitRate, sourceContainer: request.sourceContainer,
                m2tsSeekIndex: request.m2tsSeekIndex, mediaStreams: request.mediaStreams,
                audioStreamIndex: request.audioStreamIndex,
                subtitleStreamIndex: request.subtitleStreamIndex,
                chapters: request.chapters, resolvedQuality: request.resolvedQuality,
                negotiationDiagnostics: request.negotiationDiagnostics
            )
        }
        // HLS sessions carry the original offset and must be negotiated at zero.
        return try await preparePlayback(
            for: request.item, forceTranscode: request.isTranscoding,
            quality: request.resolvedQuality,
            audioStreamIndex: request.audioStreamIndex,
            subtitleStreamIndex: request.subtitleStreamIndex,
            mediaSourceID: request.mediaSourceID, startPositionSeconds: 0,
            forceCompatibleVideo: request.usesCompatibleVideo,
            usesServerVideoRecovery: request.usesServerVideoRecovery
        )
    }

    /// Re-request a failed local package as normal server HLS once before the
    /// existing H.264/SDR fallback. Callers keep the one-attempt guard; this
    /// method does not turn transport or authorization errors into a recovery.
    public func prepareCompatiblePlayback(after request: PlaybackRequest, error: Error,
                                          positionSeconds: Double? = nil,
                                          hasAdvanced: Bool = false) async throws -> PlaybackRequest {
        guard request.canRetryCompatibleVideo(after: error, hasAdvanced: hasAdvanced) else {
            throw LuminaError.malformedResponse
        }
        let conversionFailure = (error as? NativeVideoSource.SelectionError) == .invalidDolbyVisionConversion
        let serverVideoRecovery = !hasAdvanced && !conversionFailure
            && request.usesClientDemux && !request.usesServerVideoRecovery
        let recovered = try await preparePlayback(
            for: request.item, forceTranscode: true, quality: request.resolvedQuality,
            audioStreamIndex: request.audioStreamIndex, subtitleStreamIndex: request.subtitleStreamIndex,
            mediaSourceID: request.mediaSourceID,
            startPositionSeconds: positionSeconds ?? request.startPositionSeconds,
            forceCompatibleVideo: !serverVideoRecovery,
            usesServerVideoRecovery: serverVideoRecovery
        )
        guard recovered.isTranscoding else { throw LuminaError.malformedResponse }
        return recovered
    }

    /// Retry a rejected one-variant HLS master through its already selected
    /// media playlist, retaining the existing server session and negotiation.
    public func prepareServerMediaRecovery(after request: PlaybackRequest, error: Error) async throws -> PlaybackRequest? {
        guard request.isTranscoding, !request.usesClientDemux,
              !request.usesCompatibleVideo, !request.usesServerMediaRecovery,
              request.canRetryCompatibleVideo(after: error),
              request.mediaStreams.first(where: { $0.index == request.subtitleStreamIndex })?.hasExternalSubtitle != true else {
            return nil
        }
        let mediaURL = try await hlsMediaVariant(for: request)
        guard mediaURL != request.url else { return nil }
        return PlaybackRequest(
            id: request.id, item: request.item, url: mediaURL,
            startPositionSeconds: request.startPositionSeconds, mediaSourceID: request.mediaSourceID,
            isTranscoding: true, methodLabel: request.methodLabel,
            usesClientDemux: false, usesCompatibleVideo: false,
            usesServerVideoRecovery: request.usesServerVideoRecovery,
            usesServerMediaRecovery: true,
            bitRate: request.bitRate, sourceContainer: request.sourceContainer,
            m2tsSeekIndex: request.m2tsSeekIndex, mediaStreams: request.mediaStreams,
            audioStreamIndex: request.audioStreamIndex, subtitleStreamIndex: request.subtitleStreamIndex,
            chapters: request.chapters, resolvedQuality: request.resolvedQuality,
            negotiationDiagnostics: request.negotiationDiagnostics
        )
    }

    /// Mints a brief, receiver-scoped Chromecast capability. The returned media URL is safe to
    /// hand to a Cast receiver and intentionally never inherits the signed-in app credential.
    /// Jellyfin places chapter markers on BaseItemDto, not MediaSourceInfo. When a
    /// selected version omits the optional legacy source markers, read only that
    /// visible version's item detail. A missing version leaves playback usable
    /// without borrowing markers from the title's preferred file.
    private func chaptersForSelectedPlaybackSource(_ source: MediaSource, loadedItem: MediaItem,
                                                    active: LuminaSession) async throws -> [PlaybackChapter]? {
        guard source.chapters == nil, source.id != loadedItem.id else { return nil }
        do {
            let selected = try await client.item(
                origin: active.serverURL,
                token: active.accessToken,
                deviceID: active.deviceID,
                id: source.id,
                userID: active.userID
            )
            try Task.checkCancellation()
            guard isCurrent(active) else { throw LuminaError.notSignedIn }
            guard selected.id == source.id else { return [] }
            return selected.chapters
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as LuminaError where error == .http(404) {
            return []
        }
    }

    /// Saves the server's original movie, episode, or video attachment for offline playback.
    public func downloadVideo(item: MediaItem) async {
        guard let active = session else { return }
        await downloadVideo(item: item, for: active)
    }

    private func downloadVideo(item: MediaItem, for active: LuminaSession) async {
        guard !Task.isCancelled, !isOffline, isCurrent(active), LuminaOfflineLibrary.supportsVideoDownload(item) else { return }
        let request = client.request(origin: active.serverURL, path: "/Items/\(item.id)/Download",
                                     token: active.accessToken, deviceID: active.deviceID)
        await offline.downloadVideo(item: item, request: request)
        guard isCurrent(active), let artwork = imageURL(for: item), artwork.scheme != "file" else { return }
        await offline.downloadArtwork(item: item, request: URLRequest(url: artwork))
    }

    /// Registers a selection with the existing serial transfer queue, scoped to the initiating session.
    public func downloadVideos(items: [MediaItem]) async {
        guard !isOffline, let active = session else { return }
        var seen = Set<String>()
        let selected = items.filter { LuminaOfflineLibrary.supportsVideoDownload($0) && seen.insert($0.id).inserted }
        await withTaskGroup(of: Void.self) { group in
            for item in selected {
                group.addTask { await self.downloadVideo(item: item, for: active) }
            }
        }
    }

    /// Downloads an origin-validated WebVTT sidecar to a private temporary file. The caller owns
    /// the returned file and must remove it after its player item is discarded.
    public func downloadSubtitle(for stream: PlaybackStream) async throws -> URL {
        guard let active = session, stream.hasExternalSubtitle,
              let deliveryURL = stream.subtitleDeliveryURL else { throw LuminaError.malformedResponse }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let data = try await client.subtitle(origin: active.serverURL, token: active.accessToken,
                                             deviceID: active.deviceID, url: deliveryURL)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("lumina-subtitle-\(UUID().uuidString)")
            .appendingPathExtension("vtt")
        do {
            try data.write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            guard isCurrent(active) else {
                try? FileManager.default.removeItem(at: file)
                throw LuminaError.notSignedIn
            }
            return file
        } catch {
            try? FileManager.default.removeItem(at: file)
            throw error
        }
    }

    /// Resolve Lumina's one-variant server HLS master before a local subtitle wrapper references
    /// it. AVPlayer fetches the returned URL itself, so its existing playback query is retained.
    public func hlsMediaVariant(for request: PlaybackRequest) async throws -> URL {
        guard let active = session, request.isTranscoding,
              request.url.pathExtension.lowercased() == "m3u8" else { throw LuminaError.malformedResponse }
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        let variant = try await client.hlsMediaVariant(origin: active.serverURL, token: active.accessToken,
                                                       deviceID: active.deviceID, masterURL: request.url)
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return variant
    }

    /// Reads recorded skip ranges for this concrete playback source. The caller keeps this
    /// optional metadata separate from playback preparation and may offer a retry on failure.
    public func playbackMarkers(for request: PlaybackRequest) async throws -> [PlaybackMarker] {
        guard let active = session else { throw LuminaError.notSignedIn }
        guard !request.item.isAudio else { return [] }
        let markers = try await client.playbackMarkers(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
            mediaSourceID: request.mediaSourceID, durationSeconds: request.item.durationSeconds,
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return markers
    }

    /// Returns only the immediate following episode from Lumina's established series order.
    /// The optional lookup is intentionally independent of playback preparation and reporting.
    public func nextEpisode(after item: MediaItem) async throws -> MediaItem? {
        guard let active = session else { throw LuminaError.notSignedIn }
        let episode = try await client.nextEpisode(
            origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID, after: item,
        )
        guard isCurrent(active) else { throw LuminaError.notSignedIn }
        return episode
    }

    public func reportPlayback(event: PlaybackEvent, request: PlaybackRequest, positionSeconds: Double, isPaused: Bool) async throws {
        if offline.localURL(for: request.item.id) != nil {
            offline.recordPlaybackPosition(id: request.item.id, seconds: positionSeconds)
            // Keep the on-device resume durable first, but still reconcile it
            // with the server whenever the member's server is reachable.
            if isOffline { return }
        }
        guard !isOffline else { return }
        guard let active = session else { throw LuminaError.notSignedIn }
        do {
            try await client.report(origin: active.serverURL, token: active.accessToken, deviceID: active.deviceID,
                                    event: event, request: request, positionSeconds: positionSeconds, isPaused: isPaused)
        } catch {
            guard Self.isConnectionFailure(error) else { throw error }
            handleRequestFailure(error, for: active)
        }
    }
}
