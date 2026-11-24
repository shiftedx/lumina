import AVKit
import AVFoundation
import Combine
import LuminaCore
import Observation
import SwiftUI
import MediaPlayer
#if canImport(UIKit)
import UIKit
#endif

extension PlaybackModel {
    func cycleRepeat() {
        if let audioOwner { audioOwner.cycleRepeat(); return }
        switch repeatMode { case .off: repeatMode = .all; case .all: repeatMode = .one; case .one: repeatMode = .off }
    }

    func moveQueued(at source: Int, to target: Int) {
        guard source > queueIndex, target > queueIndex, queue.indices.contains(source), queue.indices.contains(target) else { return }
        if let audioOwner { audioOwner.move(from: source, to: target); return }
        reflectQueueMove(from: source, to: target)
    }

    func reflectQueueMove(from source: Int, to target: Int) {
        guard queue.indices.contains(source), queue.indices.contains(target) else { return }
        let item = queue.remove(at: source), occurrence = occurrenceIDs.remove(at: source)
        queue.insert(item, at: target); occurrenceIDs.insert(occurrence, at: target)
        rebuildQueueRows()
    }
    func reflectQueueRemoval(at index: Int) {
        guard queue.indices.contains(index) else { return }
        queue.remove(at: index); occurrenceIDs.remove(at: index)
        rebuildQueueRows()
    }

    func removeQueued(at index: Int) {
        guard queue.indices.contains(index), index > queueIndex else { return }
        if let audioOwner { audioOwner.remove(at: index); return }
        reflectQueueRemoval(at: index)
    }

    func configureAudioQueue(_ items: [MediaItem], index: Int, repeatMode: String, retainingOccurrences: [Int]? = nil) {
        if queue != items {
            let ids = items.map(\.id)
            let oldIDs = queue.map(\.id)
            if oldIDs != ids {
                if let retained = retainingOccurrences, retained.allSatisfy({ queue.indices.contains($0) }),
                   retained.map({ queue[$0].id }) == ids {
                    occurrenceIDs = retained.map { occurrenceIDs[$0] }
                } else if ids.starts(with: oldIDs) {
                    occurrenceIDs += (oldIDs.count..<ids.count).map { _ in UUID() }
                } else {
                    occurrenceIDs = items.map { _ in UUID() }
                }
            }
            queue = items
            rebuildQueueRows()
        }
        if queueIndex != index { queueIndex = index }
        let nextRepeatMode: RepeatMode = repeatMode == "one" ? .one : repeatMode == "all" ? .all : .off
        if self.repeatMode != nextRepeatMode { self.repeatMode = nextRepeatMode }
    }

    func receiveAudioTrack(_ request: PlaybackRequest, index: Int) {
        if reportedAudioStart { enqueue(.stop, paused: true) }
        self.request = request
        queueIndex = index
        elapsedSeconds = 0
        reportedAudioStart = false
        didReportCompletion = false
        errorMessage = nil
        updateNowPlaying()
    }

    func receiveAudioState(_ state: NativeMusicTransport.State) {
        let wasPlaying = isPlaying
        elapsedSeconds = state.position
        isPlaying = state.playing
        hasFinished = state.finished
        errorMessage = state.error
        if state.playing && !reportedAudioStart {
            reportedAudioStart = true
            enqueue(.start, paused: false)
        } else if reportedAudioStart && (abs(elapsedSeconds - lastReport) >= 5 || wasPlaying != isPlaying) {
            enqueue(.progress, paused: !isPlaying)
        }
        if state.finished && !didReportCompletion && reportedAudioStart {
            didReportCompletion = true
            enqueue(.stop, paused: true)
        }
        updateNowPlaying()
    }

    func playQueued(offset: Int) async {
        let nextIndex = queueIndex + offset
        if let audioOwner { await audioOwner.step(offset); return }
        guard queue.indices.contains(nextIndex), !closed, !isReplaying else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isReplaying = true
        defer { isReplaying = false }
        do {
            let next = try await store.preparePlayback(for: queue[nextIndex])
            guard !closed, !Task.isCancelled else { return }
            updatePosition()
            if !didReportCompletion { enqueue(.stop, paused: true) }
            pauseNativePlayer()
            isPlaying = false
            if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
            controlObservation = nil
            if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
            request = next
            queueIndex = nextIndex
            elapsedSeconds = next.startPositionSeconds
            didSeek = false
            started = false
            hasFinished = false
            didReportCompletion = false
            lastReport = -1
            autoplayWhenReady = next.item.isAudio || isActive
            errorMessage = nil
            replaceNativeItem(AVPlayerItem(url: next.url))
            await start()
        } catch {
            errorMessage = "The next track could not start. Check your connection and try again."
        }
    }

    func replay(startPositionSeconds: Double = 0, initiallyPaused: Bool = false) async {
        if let audioOwner { await audioOwner.select(queueIndex); return }
        guard hasFinished, !closed, !isReplaying, !isSwitchingVideoStream else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isReplaying = true
        defer { isReplaying = false }
        autoplayWhenReady = !initiallyPaused && isActive
        errorMessage = nil
        // Capture the rendered choice before stopping the old loopback source.
        let previousItem = player.currentItem
        let previousSubtitleGroup = try? await previousItem?.asset.loadMediaSelectionGroup(for: .legible)
        let restoresSubtitleSelection = previousSubtitleGroup.map { group in
            previousItem?.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: group) == false
        } ?? false
        let previousSubtitle = previousSubtitleGroup.flatMap { previousItem?.currentMediaSelection.selectedMediaOption(in: $0) }
        guard !closed, !Task.isCancelled else { return }
        let newRequest: PlaybackRequest
        do {
            newRequest = try await store.preparePlayback(
                for: request.item, forceTranscode: request.isTranscoding,
                quality: videoQuality,
                audioStreamIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                subtitleStreamIndex: subtitlesExplicitlyOff || attachedVideoSubtitle != nil ? -1 : request.subtitleStreamIndex,
                mediaSourceID: request.mediaSourceID, startPositionSeconds: startPositionSeconds
            )
        } catch {
            errorMessage = "Replay could not start. Check the server connection and try again."
            return
        }
        guard !closed, !Task.isCancelled else {
            try? await store.reportPlayback(event: .stop, request: newRequest,
                                            positionSeconds: newRequest.startPositionSeconds, isPaused: true)
            return
        }
        request = newRequest
        if newRequest.item.type == "Episode" { episodeAutoplay = EpisodeAutoplaySession(policy: episodeAutoplay.policy) }
        elapsedSeconds = newRequest.startPositionSeconds
        didReportCompletion = false
        do {
            let item = try await prepareVideoItem(newRequest, source: videoSource, externalSubtitle: attachedVideoSubtitle)
            guard !closed, !Task.isCancelled else { videoSource.stop(); return }
            replaceNativeItem(item)
        } catch {
            failReplay("Replay could not start. Reopen the video and try again.")
            return
        }
        installEndObserver()
        for _ in 0..<300 {
            guard !Task.isCancelled, !closed else { return }
            guard let item = player.currentItem else { return }
            if item.status == .failed {
                failReplay("Replay could not start. Check the server connection and try again.")
                return
            }
            if item.status == .readyToPlay { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        guard !closed, !Task.isCancelled else { return }
        guard player.currentItem?.status == .readyToPlay else {
            failReplay("Replay did not become ready. Check the server connection.")
            return
        }
        if restoresSubtitleSelection, let item = player.currentItem {
            do {
                let group = try await item.asset.loadMediaSelectionGroup(for: .legible)
                if let previousSubtitle {
                    guard let group else { throw POSIXError(.ENOTSUP) }
                    let equivalents = group.options.filter {
                        $0.displayName == previousSubtitle.displayName && $0.extendedLanguageTag == previousSubtitle.extendedLanguageTag
                    }
                    guard let option = group.mediaSelectionOption(withPropertyList: previousSubtitle.propertyList())
                            ?? (equivalents.count == 1 ? equivalents.first : nil) else { throw POSIXError(.ENOTSUP) }
                    item.select(option, in: group)
                } else if let group {
                    guard group.allowsEmptySelection else { throw POSIXError(.ENOTSUP) }
                    item.select(nil, in: group)
                }
            } catch {
                failReplay("Your subtitle selection could not be restored. Try replay again.")
                return
            }
        }
        if videoSpeed < 1, player.currentItem?.canPlaySlowForward != true { setStoredPreferredVideoRate(1) }
        player.defaultRate = videoSpeed
        guard await seekNativePlayer(to: CMTime(seconds: newRequest.startPositionSeconds, preferredTimescale: 600),
                                toleranceBefore: .zero, toleranceAfter: .zero) else {
            failReplay("Playback could not seek to the requested position. Try again.")
            return
        }
        guard !closed, !Task.isCancelled else { return }
        updatePosition()
        hasFinished = false
        enqueue(.start, paused: !autoplayWhenReady)
        if autoplayWhenReady { playNativePlayer() }
    }

    #if os(iOS)
    func chromecastTrackSelection() async -> (audioIndex: Int?, subtitle: String?)? {
        guard !hasUnresolvedEmbeddedSubtitleSelection, subtitleDelaySeconds == 0,
              let item = player.currentItem else { return nil }
        let requestID = request.id
        let audioGroup = try? await item.asset.loadMediaSelectionGroup(for: .audible)
        let subtitleGroup = try? await item.asset.loadMediaSelectionGroup(for: .legible)
        guard player.currentItem === item, request.id == requestID, !closed else { return nil }
        func index(for option: AVMediaSelectionOption, group: AVMediaSelectionGroup,
                   type: PlaybackStreamType) -> Int? {
            let streams = request.mediaStreams.filter { $0.type == type }
            let named = streams.filter { $0.label.caseInsensitiveCompare(option.displayName) == .orderedSame }
            if named.count == 1 { return named[0].index }
            if let language = option.extendedLanguageTag ?? option.locale?.identifier {
                let matching = streams.filter { NativePlaybackPreferences.languageMatches($0.language, preferred: language) }
                if matching.count == 1 { return matching[0].index }
            }
            if option === group.defaultOption {
                let defaults = streams.filter(\.isDefault)
                if defaults.count == 1 { return defaults[0].index }
            }
            return streams.count == 1 ? streams[0].index : nil
        }
        var audioIndex = sourceAudioIsAutomatic ? nil : request.audioStreamIndex
        if let group = audioGroup, let selected = item.currentMediaSelection.selectedMediaOption(in: group) {
            guard let mapped = index(for: selected, group: group, type: .audio) else { return nil }
            audioIndex = mapped
        }
        let subtitleIndex: Int?
        if subtitlesExplicitlyOff { subtitleIndex = nil }
        else if let attachedVideoSubtitle { subtitleIndex = attachedVideoSubtitle.index }
        else if let selectedEmbeddedSubtitleStreamIndex { subtitleIndex = selectedEmbeddedSubtitleStreamIndex }
        else if let group = subtitleGroup {
            if let selected = item.currentMediaSelection.selectedMediaOption(in: group) {
                guard let mapped = index(for: selected, group: group, type: .subtitle) else { return nil }
                subtitleIndex = mapped
            } else { subtitleIndex = nil }
        } else { subtitleIndex = request.subtitleStreamIndex }
        return (audioIndex, subtitleIndex.flatMap { $0 >= 0 ? "j:\($0)" : nil })
    }

    func beginCastHandoff() {
        guard !closed else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        pendingSystemBackgroundPauseItem = nil
        pauseNativePlayer()
    }

    func receiveCastStatus(position: Double, playing: Bool) {
        guard !closed, isCasting, position.isFinite, position >= 0 else { return }
        elapsedSeconds = position
        isPlaying = playing
        if abs(elapsedSeconds - lastReport) >= NativeVideoSource.progressObservationInterval.seconds {
            enqueue(.progress, paused: !playing)
        }
    }

    func returnFromCast(position: Double, playing: Bool) async {
        guard !closed, position.isFinite, position >= 0 else { return }
        autoplayWhenReady = false
        isPlaying = false
        if request.isTranscoding {
            let recovered = await replaceVideoStream(
                audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                externalSubtitle: attachedVideoSubtitle,
                selectsExternalSubtitle: false,
                changesAudio: false,
                resumePosition: position
            )
            guard !closed, !isCasting else { return }
            guard recovered, player.currentItem?.status == .readyToPlay else {
                autoplayWhenReady = false
                isPlaying = false
                videoStreamError = nil
                errorMessage = "Couldn’t resume on this device. Reopen the video and try again."
                return
            }
            elapsedSeconds = position
            let resume = playing && isActive
            autoplayWhenReady = resume
            if resume { playNativePlayer() }
            isPlaying = resume
            enqueue(.progress, paused: !isPlaying)
            return
        }
        let finished = await seekNativePlayer(to: CMTime(seconds: position, preferredTimescale: 600),
                                              toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, !isCasting else { return }
        guard finished, player.currentItem?.status == .readyToPlay else {
            autoplayWhenReady = false
            errorMessage = "Couldn’t resume on this device. Reopen the video and try again."
            return
        }
        elapsedSeconds = position
        let resume = playing && isActive
        autoplayWhenReady = resume
        if resume { playNativePlayer() }
        isPlaying = resume
        enqueue(.progress, paused: !isPlaying)
    }
    #endif

    func close() {
        traceContinuity("close")
        guard !closed else {
            #if LUMINA_UI_TESTING
            PlaybackLifecycleProbe.recordClose(state: "already-closed", player: player)
            #endif
            return
        }
        let wasCasting = isCasting
        #if os(iOS)
        castOwner?.detach()
        #endif
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        closed = true
        episodeAutoplay = EpisodeAutoplaySession()
        episodeTask?.cancel()
        episodeTask = nil
        pendingVideoPlayer?.replaceCurrentItem(with: nil)
        pendingVideoSource?.stop()
        pendingVideoPlayer = nil
        pendingVideoSource = nil
        videoSource.stop()
        if !isCommittingVideoStream, !wasCasting { updatePosition() }
        audioTransport?.stop()
        audioTransport = nil
        #if os(iOS)
        pendingSystemBackgroundPauseItem = nil
        #endif
        pauseNativePlayer()
        isPlaying = false
        if let timeObserver { player.removeTimeObserver(timeObserver); self.timeObserver = nil }
        controlObservation = nil
        if let endObserver { NotificationCenter.default.removeObserver(endObserver); self.endObserver = nil }
        removeVideoRateChangeObserver()
        for observer in playbackDiagnosticObservers { NotificationCenter.default.removeObserver(observer) }
        playbackDiagnosticObservers.removeAll()
        if !didReportCompletion && (!request.item.isAudio || reportedAudioStart) {
            enqueue(.stop, paused: true)
        }
        replaceNativeItem(nil)
        #if LUMINA_UI_TESTING
        PlaybackLifecycleProbe.recordClose(state: "closed", player: player)
        #endif
        #if os(iOS)
        if !request.item.isAudio {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
        #endif
        if request.item.isAudio {
            #if os(iOS)
            resetNowPlayingArtwork()
            #endif
            for entry in remoteTargets { entry.command.removeTarget(entry.target) }
            remoteTargets.removeAll()
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        }
    }

    func finishPlayback() {
        guard !closed, !hasFinished else { return }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        updatePosition()
        hasFinished = true
        videoSource.stop()
        autoplayWhenReady = false
        pauseNativePlayer()
        isPlaying = false
        // The stopped server session is no longer a valid replay source (especially for HLS).
        // Remove it so AVKit can't restart the obsolete item behind the fresh Replay action.
        replaceNativeItem(nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
        updateNowPlaying()
        if request.item.isAudio {
            if repeatMode == .one { Task { await replay() } }
            else if hasNext { Task { await playQueued(offset: 1) } }
            else if repeatMode == .all && !queue.isEmpty { Task { await playQueued(offset: -queueIndex) } }
        } else {
            advanceCompletedEpisodeIfNeeded()
        }
    }

    private func failReplay(_ message: String) {
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        videoSource.stop()
        errorMessage = message
        replaceNativeItem(nil)
        didReportCompletion = true
        enqueue(.stop, paused: true)
    }

    func installEndObserver() {
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        guard let item = player.currentItem else {
            endObserver = nil
            return
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard self?.player.currentItem === item else { return }
                self?.finishPlayback()
            }
        }
    }

    func updatePosition() {
        guard !isCasting else { return }
        traceContinuity("position")
        let seconds = player.currentTime().seconds
        if didSeek, seconds.isFinite, seconds >= 0, player.currentItem?.status == .readyToPlay {
            elapsedSeconds = seconds
        }
        updateNowPlaying()
    }

    private func updateNowPlaying() {
        guard request.item.isAudio, !closed else { return }
        #if os(iOS)
        loadNowPlayingArtworkIfNeeded()
        #endif
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: request.item.name,
            MPMediaItemPropertyArtist: request.item.artists?.joined(separator: ", ") ?? request.item.albumArtist ?? "",
            MPMediaItemPropertyAlbumTitle: request.item.album ?? "",
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
            MPMediaItemPropertyPlaybackDuration: request.item.durationSeconds,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsedSeconds,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        #if os(iOS)
        if let nowPlayingArtwork { info[MPMediaItemPropertyArtwork] = nowPlayingArtwork }
        #endif
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        let commands = MPRemoteCommandCenter.shared()
        commands.nextTrackCommand.isEnabled = hasNext
        commands.previousTrackCommand.isEnabled = hasPrevious
    }

    #if os(iOS)
    private func loadNowPlayingArtworkIfNeeded() {
        let itemID = request.item.id
        let url = store.imageURL(for: request.item, maxWidth: 760)
        guard itemID != nowPlayingArtworkItemID || url != nowPlayingArtworkURL else { return }

        nowPlayingArtworkGeneration &+= 1
        let generation = nowPlayingArtworkGeneration
        nowPlayingArtworkTask?.cancel()
        nowPlayingArtworkTask = nil
        nowPlayingArtwork = nil
        nowPlayingArtworkItemID = itemID
        nowPlayingArtworkURL = url
        guard let url else { return }

        nowPlayingArtworkTask = Task { [weak self] in
            var image = await ArtworkImageCache.shared.image(for: url)
            if image == nil, !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) }
                catch { return }
                guard !Task.isCancelled else { return }
                image = await ArtworkImageCache.shared.image(for: url)
            }
            guard let self, generation == nowPlayingArtworkGeneration else { return }
            nowPlayingArtworkTask = nil
            guard !Task.isCancelled, !closed, request.item.id == itemID,
                  nowPlayingArtworkURL == url, let image else { return }
            let source = UIImage(cgImage: image)
            nowPlayingArtwork = Self.makeNowPlayingArtwork(from: source)
            updateNowPlaying()
        }
    }

    nonisolated private static func makeNowPlayingArtwork(from source: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: source.size) { _ in source }
    }

    private func resetNowPlayingArtwork() {
        nowPlayingArtworkGeneration &+= 1
        nowPlayingArtworkTask?.cancel()
        nowPlayingArtworkTask = nil
        nowPlayingArtwork = nil
        nowPlayingArtworkItemID = nil
        nowPlayingArtworkURL = nil
    }
    #endif

    func installRemoteControls() {
        guard remoteTargets.isEmpty else { return }
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.isEnabled = true
        commands.pauseCommand.isEnabled = true
        commands.togglePlayPauseCommand.isEnabled = true
        commands.changePlaybackPositionCommand.isEnabled = true
        remoteTargets = [
            (commands.playCommand, commands.playCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() }
                    else if !self.isPlaying { self.togglePlayback() }
                }
                return .success
            }),
            (commands.pauseCommand, commands.pauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in self?.pause() }
                return .success
            }),
            (commands.togglePlayPauseCommand, commands.togglePlayPauseCommand.addTarget { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    if self.hasFinished { await self.replay() } else { self.togglePlayback() }
                }
                return .success
            }),
            (commands.nextTrackCommand, commands.nextTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: 1) }
                return .success
            }),
            (commands.previousTrackCommand, commands.previousTrackCommand.addTarget { [weak self] _ in
                Task { @MainActor in await self?.playQueued(offset: -1) }
                return .success
            }),
            (commands.changePlaybackPositionCommand, commands.changePlaybackPositionCommand.addTarget { [weak self] event in
                guard let position = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
                let seconds = position.positionTime
                Task { @MainActor in self?.seek(to: seconds) }
                return .success
            }),
        ]
        updateNowPlaying()
    }

    private func diagnosticReportCode(_ event: PlaybackEvent) -> Int {
        switch event { case .start: 0; case .progress: 1; case .stop: 2 }
    }

    func enqueue(_ event: PlaybackEvent, paused: Bool,
                         for requestOverride: PlaybackRequest? = nil, position: Double? = nil) {
        traceContinuity("report-enqueue=\(event)")
        recordPlaybackDiagnostic(.reportQueued, requestedPosition: position ?? elapsedSeconds,
                                 nativeReason: diagnosticReportCode(event), includeNegotiation: event == .start)
        let reportedRequest = requestOverride ?? request
        let reportedPosition = position ?? elapsedSeconds
        if requestOverride == nil { lastReport = elapsedSeconds }
        // Keep one pending progress snapshot. Stop follows the in-flight request,
        // so an old progress response cannot overwrite the final saved position.
        if case .progress = event, let last = pendingReports.last, case .progress = last.event,
           last.request.id == reportedRequest.id {
            traceContinuity("report-coalesced")
            pendingReports.removeLast()
        }
        if case .stop = event {
            pendingReports.removeAll {
                if case .progress = $0.event { return $0.request.id == reportedRequest.id }
                return false
            }
        }
        pendingReports.append((event, reportedRequest, reportedPosition, paused))
        guard reportQueue == nil else { return }
        reportQueue = Task { [self] in
            while !pendingReports.isEmpty {
                let report = pendingReports.removeFirst()
                do {
                    traceContinuity("report-send=\(report.event)")
                    try await store.reportPlayback(event: report.event, request: report.request,
                                                   positionSeconds: report.seconds, isPaused: report.paused)
                    recordPlaybackDiagnostic(.reportAcknowledged, requestedPosition: report.seconds, nativeReason: diagnosticReportCode(report.event))
                    syncError = nil
                    if case .stop = report.event { await store.refresh() }
                } catch {
                    recordPlaybackDiagnostic(.reportFailed, requestedPosition: report.seconds, nativeReason: diagnosticReportCode(report.event))
                    syncError = report.request.item.isAudio ? "Listening progress could not sync. Check the server connection." : "Watch progress could not sync. Check the server connection."
                }
            }
            reportQueue = nil
        }
    }

}
