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
    func start() async {
        traceContinuity("start-enter")
        guard !started, !closed else { return }
        installPlaybackDiagnosticObservers()
        recordPlaybackDiagnostic(.start, requestedPosition: request.startPositionSeconds)
        if request.item.isAudio, let audioOwner {
            started = true
            replaceNativeItem(nil)
            installRemoteControls()
            await audioOwner.start(self)
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: request.item.isAudio ? .default : .moviePlayback)
            #if os(iOS)
            try AVAudioSession.sharedInstance().setActive(true)
            #endif
        } catch {
            errorMessage = "Audio playback could not be configured."
            return
        }
        started = true
        if request.item.isAudio { installRemoteControls() }
        defer {
            if !didSeek { started = false; videoSource.stop() }
        }
        do {
            if request.usesClientDemux || attachedVideoSubtitle != nil {
                let item = try await prepareVideoItem(request, source: videoSource, externalSubtitle: attachedVideoSubtitle)
                try Task.checkCancellation()
                guard !closed else { throw CancellationError() }
                replaceNativeItem(item)
            }
            try await awaitInitialPlayback(positionSeconds: request.startPositionSeconds)
        } catch is CancellationError {
            return
        } catch {
            do {
                // Startup errors do not reliably create an AVPlayer error-log entry.
                // Record only its typed domain/code and the exact request evidence before recovery.
                recordPlaybackDiagnostic(.errorLog, requestedPosition: request.startPositionSeconds,
                                         includeNegotiation: true, failure: error)
                let failedSource = videoSource
                let failedRequestID = request.id
                let playbackError = await failedSource.playbackError(for: error)
                let conversion = await failedSource.dolbyVisionConversion
                try Task.checkCancellation()
                guard !closed, videoSource === failedSource, request.id == failedRequestID else { throw CancellationError() }
                if conversion?.hasFailure == true { videoDolbyVisionConversion = conversion }
                try await retryInitialCompatibleVideo(after: playbackError)
            } catch is CancellationError {
                return
            } catch {
                videoSource.stop()
                errorMessage = player.currentItem?.status == .failed
                    ? (request.item.isAudio ? "This track could not be played. Check its format and try again." : "This video could not be played in its current format.")
                    : "The media did not become ready. Check the server connection."
                return
            }
        }
        // Do not report a zero position while the item is loading or seeking to resume.
        guard let item = player.currentItem else { return }
        guard !closed, !Task.isCancelled else { return }
        if !request.item.isAudio {
            item.audioTimePitchAlgorithm = .spectral
            if videoSpeed < 1, !item.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
        }
        didSeek = true
        updatePosition()
        enqueue(.start, paused: !autoplayWhenReady)
        let progressInterval = CMTime(seconds: 1, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: progressInterval, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isCasting, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.traceContinuity("periodic-callback")
                let source = self.videoSource
                let requestID = self.request.id
                let conversion = await source.dolbyVisionConversion
                guard !self.closed, !self.hasFinished, self.videoSource === source, self.request.id == requestID,
                      !self.isCasting, !self.isSwitchingVideoStream, !self.isCommittingVideoStream else { return }
                self.videoDolbyVisionConversion = conversion
                if conversion?.hasFailure == true {
                    self.updatePosition()
                    self.pauseNativePlayer()
                    await self.handleVideoPlaybackFailure(NativeVideoSource.SelectionError.invalidDolbyVisionConversion)
                    return
                }
                self.isPlaying = self.player.timeControlStatus == .playing
                #if os(iOS)
                self.applyEffectiveVideoRateIfPermitted()
                #endif
                self.updatePosition()
                if !self.request.item.isAudio, self.elapsedSeconds > self.request.startPositionSeconds + 0.25 {
                    self.videoAdvancedRequestID = self.request.id
                }
                let shouldReport = abs(self.elapsedSeconds - self.lastReport) >= NativeVideoSource.progressObservationInterval.seconds
                if shouldReport {
                    self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
                }
                if self.player.currentItem?.status == .failed {
                    await self.handleVideoPlaybackFailure(self.player.currentItem?.error)
                }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isCasting, !self.closed, !self.hasFinished, !self.isCommittingVideoStream else { return }
                self.traceContinuity("control-status-callback")
                self.isPlaying = self.player.timeControlStatus == .playing
                #if os(iOS)
                self.applyEffectiveVideoRateIfPermitted()
                #endif
                self.updatePosition()
                self.enqueue(.progress, paused: self.player.timeControlStatus != .playing)
            }
        }
        installVideoRateChangeObserver()
        installEndObserver()
        if autoplayWhenReady { playNativePlayer() }
    }

    func pause() {
        traceContinuity("pause")
        guard !closed else { return }
        if let audioOwner { audioOwner.pause(); return }
        #if os(iOS)
        if let castOwner, isCasting { castOwner.pause(); return }
        #endif
        if nextEpisodeAutoplay != nil { nextEpisodeAutoplay = false }
        // Leaving during asset loading must also suppress the later autoplay.
        autoplayWhenReady = false
        videoRateLease.cancel()
        #if os(iOS)
        pendingSystemBackgroundPauseItem = nil
        #endif
        pauseNativePlayer()
        isPlaying = false
        guard !hasFinished, !isCommittingVideoStream else { return }
        updatePosition()
        enqueue(.progress, paused: true)
    }

    func togglePlayback() {
        #if os(iOS)
        if let castOwner, isCasting { castOwner.togglePlayback(); return }
        #endif
        if let audioOwner { Task { await audioOwner.toggle() }; return }
        guard !closed else { return }
        if let nextEpisodeAutoplay {
            if nextEpisodeAutoplay { pause() } else { self.nextEpisodeAutoplay = true }
            return
        }
        guard !closed, !hasFinished else { return }
        if isCommittingVideoStream { autoplayWhenReady.toggle(); return }
        if !didSeek {
            guard !request.item.isAudio else { return }
            // Loading/resume seeking still belongs to start(). Only change the
            // requested intent here; do not start an unprepared item at zero.
            if autoplayWhenReady { pause() } else { autoplayWhenReady = true }
            return
        }
        let shouldPause = request.item.isAudio ? player.timeControlStatus == .playing : autoplayWhenReady
        if shouldPause { pause() }
        else { autoplayWhenReady = true; playNativePlayer(); isPlaying = true }
    }

    func setActive(_ active: Bool) {
        traceContinuity("active=\(active)")
        guard isActive != active else { return }
        isActive = active
        if active { advanceCompletedEpisodeIfNeeded() }
        #if os(tvOS)
        // TV video remains paused when returning until the user presses Play.
        if !request.item.isAudio { pause() }
        #endif
        // On iOS, AVFoundation handles background pausing and PiP continuation.
        // Pausing during willResignActive would prevent automatic PiP from starting.
    }

    func seek(to seconds: Double) {
        if let audioOwner { Task { await audioOwner.seek(seconds) }; return }
        if request.item.isAudio {
            guard seconds.isFinite, didSeek, !closed, !hasFinished, !isCommittingVideoStream else { return }
            elapsedSeconds = min(max(0, seconds), request.item.durationSeconds)
            player.seek(to: CMTime(seconds: elapsedSeconds, preferredTimescale: 600),
                        toleranceBefore: .zero, toleranceAfter: .zero)
            return
        }
        let direction: VideoSeekDirection = seconds >= gestureSeekTime ? .forward : .backward
        requestVideoSeek(to: seconds, direction: direction)
    }

    func requestRelativeVideoSeek(_ delta: Double) {
        guard delta.isFinite else { return }
        requestVideoSeek(to: gestureSeekTime + delta, direction: delta >= 0 ? .forward : .backward)
    }

    func supportsVideoSpeed(_ rate: Float) -> Bool {
        guard !request.item.isAudio, let item = player.currentItem, item.status == .readyToPlay else { return false }
        return VideoPlaybackSpeed.allCases.contains(where: { $0.rawValue == rate })
            && (rate >= 1 || item.canPlaySlowForward)
    }

    func selectVideoSpeed(_ rate: Float) {
        setPreferredVideoRate(Double(rate))
    }

    func setPreferredVideoRate(_ rate: Double) {
        guard rate.isFinite, rate > 0 else { return }
        let nativeRate = Float(rate)
        guard !isCasting, !closed, !hasFinished, !isSwitchingVideoStream, supportsVideoSpeed(nativeRate) else { return }
        setStoredPreferredVideoRate(Double(nativeRate))
        applyEffectiveVideoRateIfPermitted()
    }

    func beginTemporaryVideoRate() -> UUID? {
        guard canUseVideoActions, permitsVideoRateApplication,
              player.currentItem?.status == .readyToPlay,
              !player.isExternalPlaybackActive,
              !AVAudioSession.sharedInstance().currentRoute.outputs.contains(where: { $0.portType == .airPlay }) else { return nil }
        let token = videoRateLease.begin(isEligible: true)
        applyEffectiveVideoRateIfPermitted()
        return token
    }

    func endTemporaryVideoRate(_ token: UUID) {
        guard videoRateLease.end(token) else { return }
        restorePreferredVideoRateIfRunning()
    }

    func cancelVideoGestureOverrides() {
        let wasOverridden = videoRateLease.token != nil
        videoRateLease.cancel()
        if wasOverridden { restorePreferredVideoRateIfRunning() }
    }

    func currentVideoSeekRanges() -> [VideoSeekRange] {
        guard !request.item.isAudio, let item = player.currentItem else { return [] }
        let seekable = item.seekableTimeRanges.compactMap { value -> VideoSeekRange? in
            let range = value.timeRangeValue
            let start = range.start.seconds
            let duration = range.duration.seconds
            guard start.isFinite, duration.isFinite, duration > 0 else { return nil }
            // CMTimeRange's end is exclusive. Keep the target playable after
            // conversion to the seek command's 600-tick timebase as well.
            let tick = max(1.0 / 600, 1.0 / Double(max(1, range.duration.timescale)))
            return VideoSeekRange(start, max(start, start + duration - tick))
        }
        if !seekable.isEmpty { return VideoSeekAuthority.normalizedRanges(seekable) }

        let itemDuration = item.duration.seconds
        guard itemDuration.isFinite, itemDuration > 0,
              let range = VideoSeekRange(0, max(0, itemDuration - 1.0 / 600)) else { return [] }
        return [range]
    }

    private func requestVideoSeek(to candidate: Double, direction: VideoSeekDirection) {
        traceContinuity("seek-request=\(candidate)")
        #if os(iOS)
        if let castOwner, isCasting {
            guard candidate.isFinite else { return }
            castOwner.seek(to: min(max(0, candidate), request.item.durationSeconds))
            return
        }
        #endif
        guard candidate.isFinite, canUseVideoActions, !hasFinished else { return }
        guard let submission = videoSeekAuthority.request(
            target: candidate, ranges: currentVideoSeekRanges(), direction: direction
        ) else { return }
        submitVideoSeek(submission)
    }

    private func submitVideoSeek(_ submission: VideoSeekSubmission) {
        guard let item = player.currentItem else {
            _ = videoSeekAuthority.complete(submission, actualTime: nil)
            return
        }
        videoSeekTask?.cancel()
        item.cancelPendingSeeks()
        videoSeekTask = Task { @MainActor [weak self] in
            guard let self else { return }
            let playhead = self.player.currentTime().seconds
            let direction: VideoSeekDirection = playhead.isFinite && submission.target < playhead ? .backward : .forward
            guard let revalidated = self.videoSeekAuthority.revalidatedSubmission(
                submission, ranges: self.currentVideoSeekRanges(), direction: direction
            ) else { return }
            let didSeek = await self.seekNativePlayer(
                to: CMTime(seconds: revalidated.target, preferredTimescale: 600),
                toleranceBefore: .zero, toleranceAfter: .zero
            )
            guard !Task.isCancelled else { return }
            self.completeVideoSeek(revalidated, item: item, didSeek: didSeek)
        }
    }

    private func completeVideoSeek(_ submission: VideoSeekSubmission, item: AVPlayerItem, didSeek: Bool) {
        guard !closed, player.currentItem === item else { return }
        let actual = player.currentTime().seconds
        switch videoSeekAuthority.complete(submission, actualTime: actual) {
        case .ignored:
            return
        case .settled(let position):
            videoSeekTask = nil
            if let position { elapsedSeconds = position }
            else { updatePosition() }
            if didSeek {
                isPlaying = player.timeControlStatus == .playing
                enqueue(.progress, paused: !isPlaying)
            }
            recoverSystemBackgroundPauseIfNeeded()
        }
    }

    func invalidateVideoSeekWork() {
        if videoSeekTask != nil {
            videoSeekTask?.cancel()
            player.currentItem?.cancelPendingSeeks()
        }
        videoSeekTask = nil
        videoSeekAuthority.invalidate()
    }

    func setStoredPreferredVideoRate(_ rate: Double) {
        videoRateLease.setPreferredRate(rate)
        videoSpeed = Float(videoRateLease.preferredRate)
        player.defaultRate = videoSpeed
    }

    private var permitsVideoRateApplication: Bool {
        !isCasting && autoplayWhenReady && isActive && !closed && !hasFinished
            && !isSwitchingVideoStream && !isCommittingVideoStream
            && videoDolbyVisionConversion?.hasFailure != true
            && player.timeControlStatus == .playing
    }

    private func applyEffectiveVideoRateIfPermitted() {
        traceContinuity("rate-application-attempt")
        guard !request.item.isAudio,
              let rate = videoRateLease.rateToApply(isPlaybackAllowed: permitsVideoRateApplication) else { return }
        let target = Float(rate)
        // Equal assignments retrigger AVPlayer rate/status observation without
        // changing playback, feeding periodic callbacks back into this path.
        guard player.rate != target else { return }
        traceContinuity("rate-write")
        player.rate = target
    }

    private func restorePreferredVideoRateIfRunning() {
        // PiP may continue after scene deactivation. Also replace a buffering
        // engine's pending 2× rate; a paused engine never receives a nonzero rate.
        guard !isCasting, !request.item.isAudio, autoplayWhenReady, !closed, !hasFinished,
              !isSwitchingVideoStream, !isCommittingVideoStream,
              videoDolbyVisionConversion?.hasFailure != true,
              player.timeControlStatus != .paused else { return }
        player.rate = Float(videoRateLease.preferredRate)
    }

    func refreshVideoActions() async {
        guard !request.item.isAudio, !closed else { return }
        let current = request
        let generation = UUID()
        actionsGeneration = generation
        playbackMarkers = []
        nextEpisode = nil
        videoActionsError = nil
        async let preferences: Void = refreshEpisodePreferences(for: current, generation: generation)
        async let markers = store.playbackMarkers(for: current)
        async let following = store.nextEpisode(after: current.item)
        do {
            let result = try await markers
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            playbackMarkers = result
        } catch {
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            videoActionsError = "Some playback actions could not load. Try again."
        }
        do {
            let result = try await following
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            nextEpisode = result
            advanceCompletedEpisodeIfNeeded()
        } catch {
            guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
            videoActionsError = "Some playback actions could not load. Try again."
        }
        await preferences
        advanceCompletedEpisodeIfNeeded()
    }

    private func refreshEpisodePreferences(for current: PlaybackRequest, generation: UUID) async {
        guard current.item.type == "Episode", episodePreferencesRequestID != current.id else { return }
        episodeSettings.configure(session: store.session)
        await episodeSettings.load()
        guard !closed, request.id == current.id, actionsGeneration == generation, !Task.isCancelled else { return }
        episodePreferencesRequestID = current.id
        episodeAutoplay.updatePolicy(episodeSettings.autoplayPresencePolicy)
    }

    func confirmEpisodeAutoplay() -> Bool {
        guard !closed else { return false }
        return episodeAutoplay.confirm()
    }

    func advanceCompletedEpisodeIfNeeded() {
        guard !closed, hasFinished, isActive, !isCasting, request.item.type == "Episode",
              episodePreferencesRequestID == request.id, episodeSettings.autoplayUpNext,
              nextEpisode != nil, automaticAdvanceRequestID != request.id else { return }
        automaticAdvanceRequestID = request.id
        switch episodeAutoplay.episodeEnded(hasNextEpisode: true) {
        case .playNext:
            Task { await playNextEpisode(automatically: true) }
        case .confirmPresence, .stop:
            break
        }
    }

    var activeIntro: PlaybackMarker? {
        guard !hasFinished else { return nil }
        return playbackMarkers.first { $0.kind == .intro && elapsedSeconds >= $0.startSeconds && elapsedSeconds < $0.endSeconds }
    }

    var activeCredits: PlaybackMarker? {
        guard !hasFinished else { return nil }
        return playbackMarkers.first { $0.kind == .credits && elapsedSeconds >= $0.startSeconds && elapsedSeconds < $0.endSeconds }
    }

    func skip(_ marker: PlaybackMarker) async {
        guard !closed, !hasFinished, !isSwitchingVideoStream, !isSeekingChapter, !isReplaying, didSeek,
              playbackMarkers.contains(marker), marker == activeIntro || marker == activeCredits,
              let item = player.currentItem else { return }
        invalidateVideoSeekWork()
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await seekNativePlayer(to: CMTime(seconds: marker.endSeconds, preferredTimescale: 600),
                                        toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, player.currentItem === item else { return }
        updatePosition()
        if success {
            if marker.kind == .credits, marker.endSeconds >= request.item.durationSeconds,
               request.item.durationSeconds > 0 {
                finishPlayback()
            } else {
                isPlaying = player.timeControlStatus == .playing
                enqueue(.progress, paused: !isPlaying)
            }
        } else {
            videoStreamError = "This segment could not be skipped. Try again."
        }
    }

    func playNextEpisode(automatically: Bool = false) async {
        guard episodeTask == nil else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("busy")
            #endif
            return
        }
        guard canUseVideoActions else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("unavailable")
            #endif
            return
        }
        #if os(iOS) && LUMINA_UI_TESTING
        recordNextEpisodeDiagnostic("requested")
        #endif
        let task = Task { await advanceToNextEpisode(automatically: automatically) }
        episodeTask = task
        await task.value
        episodeTask = nil
    }

    private func advanceToNextEpisode(automatically: Bool) async {
        guard canUseVideoActions, !request.item.isAudio, let episode = nextEpisode else {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("rejected")
            #endif
            return
        }
        #if os(iOS) && LUMINA_UI_TESTING
        recordNextEpisodeDiagnostic("entry")
        #endif
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isSwitchingVideoStream = true
        nextEpisodeAutoplay = true
        videoStreamError = nil
        defer { isSwitchingVideoStream = false; isCommittingVideoStream = false; nextEpisodeAutoplay = nil }
        let previousRequest = request
        let previousItem = player.currentItem
        let previousSource = videoSource
        let candidateSource = NativeVideoSource()
        let candidatePlayer = AVPlayer()
        configureDeviceAudioLanguage(candidatePlayer, preferences: store.playbackPreferences)
        pendingVideoSource = candidateSource
        pendingVideoPlayer = candidatePlayer
        defer { pendingVideoSource = nil; pendingVideoPlayer = nil }
        var candidateRequest: PlaybackRequest?
        var installed = false
        var previousPosition = elapsedSeconds
        do {
            let next = try await store.preparePlayback(for: episode, quality: videoQuality, startPositionSeconds: 0)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("prepared")
            #endif
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed, request.id == previousRequest.id else { throw CancellationError() }
            let item = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: nil, subtitleDelay: 0)
            // A player item cannot be transferred between AVPlayers. Preflight a distinct
            // item sharing the asset, leaving the configured item for the visible player.
            candidatePlayer.replaceCurrentItem(with: AVPlayerItem(asset: item.asset))
            try await NativeVideoSource.waitUntilReady(candidatePlayer)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("candidate-ready")
            #endif
            guard await candidatePlayer.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero) else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed, request.id == previousRequest.id,
                  player.currentItem === previousItem || (hasFinished && player.currentItem == nil) else {
                throw CancellationError()
            }
            updatePosition()
            previousPosition = elapsedSeconds
            isCommittingVideoStream = true
            pauseNativePlayer()
            candidatePlayer.replaceCurrentItem(with: nil)
            replaceNativeItem(item)
            installed = true
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("installed")
            #endif
            try await NativeVideoSource.waitUntilReady(player)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("visible-ready")
            #endif
            guard await seekNativePlayer(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero), item.status == .readyToPlay else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            if !didReportCompletion { enqueue(.stop, paused: true, for: previousRequest, position: previousPosition) }
            request = next
            episodeAutoplay.didStartNextEpisode(automatically: automatically)
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("committed")
            #endif
            videoSource = candidateSource
            attachedVideoSubtitle = next.mediaStreams.first {
                $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle
            }
            selectedEmbeddedSubtitleStreamIndex = attachedVideoSubtitle == nil ? next.subtitleStreamIndex : nil
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = false
            subtitleDelaySeconds = 0
            subtitlesExplicitlyOff = next.subtitleStreamIndex == -1
            sourceAudioIsAutomatic = true
            playbackMarkers = []
            nextEpisode = nil
            videoActionsError = nil
            elapsedSeconds = 0
            hasFinished = false
            didReportCompletion = false
            errorMessage = nil
            lastReport = -1
            if videoSpeed < 1, !item.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
            installEndObserver()
            isCommittingVideoStream = false
            autoplayWhenReady = nextEpisodeAutoplay == true && isActive
            enqueue(.start, paused: !autoplayWhenReady)
            if autoplayWhenReady { playNativePlayer() }
            isPlaying = autoplayWhenReady
            previousSource.stop()
        } catch {
            #if os(iOS) && LUMINA_UI_TESTING
            recordNextEpisodeDiagnostic("failed-\(nextEpisodeFailureKind(error))")
            #endif
            candidatePlayer.replaceCurrentItem(with: nil)
            if installed, !closed {
                if !hasFinished, let previousItem {
                    replaceNativeItem(previousItem)
                    _ = await seekNativePlayer(to: CMTime(seconds: previousPosition, preferredTimescale: 600),
                                          toleranceBefore: .zero, toleranceAfter: .zero)
                    if autoplayWhenReady && isActive { playNativePlayer() }
                } else {
                    replaceNativeItem(nil)
                }
            }
            candidateSource.stop()
            if let candidateRequest { enqueue(.stop, paused: true, for: candidateRequest, position: 0) }
            guard !closed else { return }
            if !(error is CancellationError) { videoStreamError = "The next episode could not start. Try again." }
        }
    }

    var currentChapterID: Int64? {
        request.chapters.last(where: { $0.startPositionSeconds <= elapsedSeconds })?.id
    }

    func selectChapter(_ chapter: PlaybackChapter) async {
        guard !request.item.isAudio, !closed, !isSwitchingVideoStream, !isSeekingChapter, !isReplaying,
              request.chapters.contains(chapter), chapter.startPositionSeconds.isFinite,
              chapter.startPositionSeconds >= 0 else { return }
        if hasFinished {
            await replay(startPositionSeconds: chapter.startPositionSeconds, initiallyPaused: true)
            return
        }
        guard didSeek, let item = player.currentItem, item.status == .readyToPlay else { return }
        invalidateVideoSeekWork()
        isSeekingChapter = true
        videoStreamError = nil
        defer { isSeekingChapter = false }
        let success = await seekNativePlayer(to: CMTime(seconds: chapter.startPositionSeconds, preferredTimescale: 600),
                                        toleranceBefore: .zero, toleranceAfter: .zero)
        guard !closed, player.currentItem === item else { return }
        updatePosition()
        if success {
            isPlaying = player.timeControlStatus == .playing
            enqueue(.progress, paused: !isPlaying)
        } else {
            videoStreamError = "This chapter could not be opened. Try again."
        }
    }

    /// Prepare independently so a bad track never tears down the current picture or session.
    func selectVideoAudioStream(_ audioIndex: Int?) async {
        await replaceVideoStream(audioIndex: audioIndex, externalSubtitle: attachedVideoSubtitle,
                                 selectsExternalSubtitle: false, changesAudio: true)
    }

    func selectExternalVideoSubtitle(_ stream: PlaybackStream) async {
        guard stream.type == .subtitle, stream.hasExternalSubtitle else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: stream, selectsExternalSubtitle: true, changesAudio: false)
    }

    func setSubtitleDelay(_ seconds: Double) async {
        guard canAdjustSubtitleTiming else { return }
        let delay = NativeVideoSource.normalizedSubtitleDelay(seconds)
        guard delay != subtitleDelaySeconds else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: attachedVideoSubtitle, selectsExternalSubtitle: false,
                                 changesAudio: false, subtitleDelay: delay)
    }

    func selectEmbeddedSubtitle(_ option: AVMediaSelectionOption?) {
        guard let option else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = nil
            return
        }
        hasExplicitEmbeddedSubtitleSelection = true
        let streams = request.mediaStreams.filter { $0.type == .subtitle && !$0.hasExternalSubtitle }
        guard !streams.isEmpty else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = true
            selectedEmbeddedSubtitleStreamIndex = nil
            return
        }
        let labelMatches = streams.filter {
            $0.label.caseInsensitiveCompare(option.displayName) == .orderedSame
        }
        if labelMatches.count == 1 {
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = labelMatches[0].index
            return
        }
        if let language = option.extendedLanguageTag ?? option.locale?.identifier {
            let languageMatches = streams.filter {
                NativePlaybackPreferences.languageMatches($0.language, preferred: language)
            }
            if languageMatches.count == 1 {
                hasUnresolvedEmbeddedSubtitleSelection = false
                selectedEmbeddedSubtitleStreamIndex = languageMatches[0].index
                return
            }
        }
        if streams.count == 1 {
            hasUnresolvedEmbeddedSubtitleSelection = false
            selectedEmbeddedSubtitleStreamIndex = streams[0].index
        } else {
            hasExplicitEmbeddedSubtitleSelection = false
            hasUnresolvedEmbeddedSubtitleSelection = true
            selectedEmbeddedSubtitleStreamIndex = nil
        }
    }

    func selectVideoQuality(_ quality: PlaybackQuality) async {
        guard quality != videoQuality else { return }
        await replaceVideoStream(audioIndex: sourceAudioIsAutomatic ? nil : request.audioStreamIndex,
                                 externalSubtitle: attachedVideoSubtitle, selectsExternalSubtitle: false,
                                 changesAudio: false, quality: quality)
    }

    func prepareVideoItem(_ next: PlaybackRequest, source: NativeVideoSource,
                                  externalSubtitle: PlaybackStream?, subtitleDelay: Double? = nil) async throws -> AVPlayerItem {
        #if LUMINA_UI_TESTING
        if !uiTestingInjectedClientDemuxFailure,
           next.usesClientDemux,
           ProcessInfo.processInfo.arguments.contains("--ui-testing-client-demux-format-failure") {
            uiTestingInjectedClientDemuxFailure = true
            throw NativeVideoSource.SelectionError.subtitleMasterUnavailable
        }
        if !uiTestingInjectedServerVideoRecoveryFailure,
           next.usesServerVideoRecovery,
           ProcessInfo.processInfo.arguments.contains("--ui-testing-server-video-recovery-format-failure") {
            uiTestingInjectedServerVideoRecoveryFailure = true
            throw NativeVideoSource.SelectionError.subtitleMasterUnavailable
        }
        if !uiTestingInjectedServerMediaRecoveryFailure,
           next.usesServerMediaRecovery,
           ProcessInfo.processInfo.arguments.contains("--ui-testing-server-media-recovery-format-failure") {
            uiTestingInjectedServerMediaRecoveryFailure = true
            throw NativeVideoSource.SelectionError.subtitleMasterUnavailable
        }
        #endif
        let externalSubtitle = externalSubtitle ?? next.mediaStreams.first {
            $0.index == next.subtitleStreamIndex && $0.hasExternalSubtitle
        }
        var downloaded: URL?
        defer { if let downloaded { try? FileManager.default.removeItem(at: downloaded) } }
        var subtitles: [NativeVideoSource.ExternalSubtitle] = []
        if let externalSubtitle {
            let file = try await store.downloadSubtitle(for: externalSubtitle)
            downloaded = file
            subtitles = [.init(fileURL: file, language: externalSubtitle.language,
                               name: externalSubtitle.label, isForced: externalSubtitle.isForced,
                               durationSeconds: next.item.durationSeconds)]
        }
        // Server HLS keeps its policy-limited variant and gets a local subtitle master.
        let videoURL = externalSubtitle != nil && !next.usesClientDemux
            ? try await store.hlsMediaVariant(for: next) : next.url
        let item = try await source.prepare(
            url: videoURL, usesClientDemux: next.usesClientDemux,
            requireDolbyVisionTransportPairing: next.usesClientDemux && next.sourceContainer?.lowercased() == "m2ts",
            m2tsSeekIndex: next.m2tsSeekIndex,
            preferredAudioStreamIndex: next.audioStreamIndex,
            preferredSubtitleStreamIndex: externalSubtitle == nil
                ? next.subtitleStreamIndex.flatMap { $0 >= 0 ? $0 : nil } : nil,
            externalSubtitles: subtitles, bandwidth: next.bitRate,
            subtitleDelaySeconds: subtitleDelay ?? subtitleDelaySeconds
        )
        item.audioTimePitchAlgorithm = .spectral
        let conversion = await source.dolbyVisionConversion
        if source === videoSource { videoDolbyVisionConversion = conversion }
        return item
    }

    @discardableResult
    func replaceVideoStream(audioIndex: Int?, externalSubtitle: PlaybackStream?,
                                    selectsExternalSubtitle: Bool, changesAudio: Bool,
                                    subtitleDelay: Double? = nil, quality: PlaybackQuality? = nil,
                                    resumePosition: Double? = nil, compatibilityError: Error? = nil,
                                    recoveryAttempts: Int = 0,
                                    failedRecoveryRequest: PlaybackRequest? = nil) async -> Bool {
        var candidateRequest: PlaybackRequest?
        var transferredRecovery = false
        var releasedSessions: Set<String> = []
        var commitPosition = resumePosition ?? elapsedSeconds
        defer {
            if !transferredRecovery {
                for unused in [candidateRequest, failedRecoveryRequest].compactMap({ $0 })
                    where unused.id != request.id && releasedSessions.insert(unused.id).inserted {
                    enqueue(.stop, paused: true, for: unused, position: commitPosition)
                }
            }
        }
        guard !isCasting, !request.item.isAudio, didSeek, !closed, !hasFinished, !isSwitchingVideoStream, !isSeekingChapter,
              let previousItem = player.currentItem else {
            return false
        }
        guard resumePosition.map({ $0.isFinite && $0 >= 0 }) ?? true else { return false }
        guard selectsExternalSubtitle || !hasUnresolvedEmbeddedSubtitleSelection else {
            videoStreamError = "Select a uniquely identified subtitle or Auto before changing this stream."
            return false
        }
        invalidateVideoSeekWork()
        cancelVideoGestureOverrides()
        isSwitchingVideoStream = true
        videoStreamError = nil
        defer { isSwitchingVideoStream = false; isCommittingVideoStream = false }
        let candidateSource = NativeVideoSource()
        let candidatePlayer = AVPlayer()
        configureDeviceAudioLanguage(candidatePlayer, preferences: store.playbackPreferences)
        let previousRequest = request
        let previousSource = videoSource
        var replacementInstalled = false
        do {
            if resumePosition == nil { updatePosition() }
            let next: PlaybackRequest
            if let compatibilityError {
                let recoveryRequest = failedRecoveryRequest ?? request
                if videoAdvancedRequestID != recoveryRequest.id,
                   let mediaRecovery = try await store.prepareServerMediaRecovery(
                    after: recoveryRequest, error: compatibilityError
                   ) {
                    next = mediaRecovery
                } else {
                    if recoveryRequest.id != previousRequest.id,
                       releasedSessions.insert(recoveryRequest.id).inserted {
                        enqueue(.stop, paused: true, for: recoveryRequest, position: commitPosition)
                    }
                    next = try await store.prepareCompatiblePlayback(
                        after: recoveryRequest, error: compatibilityError, positionSeconds: commitPosition,
                        hasAdvanced: videoAdvancedRequestID == recoveryRequest.id
                    )
                }
            } else {
                next = try await store.preparePlayback(
                    for: request.item,
                    forceTranscode: (quality == nil && request.isTranscoding)
                        || (externalSubtitle != nil && !request.usesClientDemux)
                        || (audioIndex != nil && !request.usesClientDemux),
                    quality: quality ?? videoQuality,
                    audioStreamIndex: audioIndex,
                    subtitleStreamIndex: externalSubtitle != nil || subtitlesExplicitlyOff ? -1
                        : hasExplicitEmbeddedSubtitleSelection
                            ? selectedEmbeddedSubtitleStreamIndex ?? request.subtitleStreamIndex
                            : nil,
                    mediaSourceID: request.mediaSourceID, startPositionSeconds: commitPosition
                )
            }
            candidateRequest = next
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            let nextItem = try await prepareVideoItem(next, source: candidateSource, externalSubtitle: externalSubtitle,
                                                      subtitleDelay: subtitleDelay)
            candidatePlayer.replaceCurrentItem(with: AVPlayerItem(asset: nextItem.asset))
            try await NativeVideoSource.waitUntilReady(candidatePlayer, seekingTo: commitPosition)
            // Carry a deliberate caption choice across a repackaged audio track.
            if selectsExternalSubtitle {
                guard let group = try await nextItem.asset.loadMediaSelectionGroup(for: .legible) else {
                    throw POSIXError(.ENOTSUP)
                }
                let matching = group.options.filter { $0.displayName == candidateSource.externalSubtitleDisplayName }
                guard matching.count == 1, let option = matching.first else { throw POSIXError(.ENOTSUP) }
                nextItem.select(option, in: group)
            } else if let oldGroup = try? await previousItem.asset.loadMediaSelectionGroup(for: .legible),
               !previousItem.currentMediaSelection.mediaSelectionCriteriaCanBeAppliedAutomatically(to: oldGroup) {
                let selected = previousItem.currentMediaSelection.selectedMediaOption(in: oldGroup)
                guard let nextGroup = try await nextItem.asset.loadMediaSelectionGroup(for: .legible) else {
                    throw POSIXError(.ENOTSUP)
                }
                if let selected {
                    let equivalents = nextGroup.options.filter {
                        $0.displayName == selected.displayName && $0.extendedLanguageTag == selected.extendedLanguageTag
                    }
                    if let matching = nextGroup.mediaSelectionOption(withPropertyList: selected.propertyList())
                        ?? (equivalents.count == 1 ? equivalents.first : nil) {
                        nextItem.select(matching, in: nextGroup)
                    } else if next.subtitleStreamIndex.map({ $0 >= 0 }) == true,
                              let serverSelected = nextGroup.defaultOption {
                        // Prism's opaque option property list cannot survive a switch to
                        // server HLS. The replacement request already names this source
                        // subtitle stream, so retain the server's corresponding default.
                        nextItem.select(serverSelected, in: nextGroup)
                    } else {
                        throw POSIXError(.ENOTSUP)
                    }
                } else if nextGroup.allowsEmptySelection {
                    nextItem.select(nil, in: nextGroup)
                } else {
                    throw POSIXError(.ENOTSUP)
                }
            }
            try Task.checkCancellation()
            guard !closed, player.currentItem === previousItem else { throw CancellationError() }
            if resumePosition == nil {
                updatePosition()
                commitPosition = elapsedSeconds
            }
            var position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            guard await candidatePlayer.seek(to: position, toleranceBefore: .zero, toleranceAfter: .zero) else {
                throw POSIXError(.EIO)
            }
            try Task.checkCancellation()
            guard !closed, !hasFinished, player.currentItem === previousItem else { throw CancellationError() }
            if resumePosition == nil {
                updatePosition()
                commitPosition = elapsedSeconds
            }
            position = CMTime(seconds: commitPosition, preferredTimescale: 600)
            // AVPlayer stays stable across rotations and Duo poses. Only its item changes.
            isCommittingVideoStream = true
            pauseNativePlayer()
            candidatePlayer.replaceCurrentItem(with: nil)
            replaceNativeItem(nextItem)
            replacementInstalled = true
            try await NativeVideoSource.waitUntilReady(player, seekingTo: position.seconds)
            guard await seekNativePlayer(to: position, toleranceBefore: .zero, toleranceAfter: .zero),
                  nextItem.status == .readyToPlay else { throw nextItem.error ?? POSIXError(.EIO) }
            try Task.checkCancellation()
            guard !closed else { throw CancellationError() }
            elapsedSeconds = commitPosition
            if next.id != previousRequest.id { enqueue(.stop, paused: true) }
            request = next
            if next.usesServerMediaRecovery { compatibilityRetryRequestID = nil }
            videoSource = candidateSource
            if videoSpeed < 1, !nextItem.canPlaySlowForward { setStoredPreferredVideoRate(1) }
            player.defaultRate = videoSpeed
            if changesAudio { sourceAudioIsAutomatic = audioIndex == nil }
            attachedVideoSubtitle = externalSubtitle
            if selectsExternalSubtitle {
                selectedEmbeddedSubtitleStreamIndex = nil
                hasExplicitEmbeddedSubtitleSelection = false
                hasUnresolvedEmbeddedSubtitleSelection = false
            } else if externalSubtitle == nil {
                selectedEmbeddedSubtitleStreamIndex = next.subtitleStreamIndex
            }
            if let quality { videoQuality = quality }
            if let subtitleDelay { subtitleDelaySeconds = subtitleDelay }
            if selectsExternalSubtitle { subtitlesExplicitlyOff = false }
            installEndObserver()
            lastReport = -1
            isCommittingVideoStream = false
            let resume = autoplayWhenReady && isActive
            enqueue(.start, paused: !resume)
            if resume { playNativePlayer() }
            isPlaying = resume
            previousSource.stop()
            return true
        } catch {
            let candidateError = await candidateSource.playbackError(for: error)
            candidatePlayer.replaceCurrentItem(with: nil)
            if replacementInstalled, !closed {
                replaceNativeItem(previousItem)
                _ = await seekNativePlayer(to: CMTime(seconds: commitPosition, preferredTimescale: 600),
                                      toleranceBefore: .zero, toleranceAfter: .zero)
                if autoplayWhenReady && isActive { playNativePlayer() }
                isPlaying = player.timeControlStatus == .playing
            }
            candidateSource.stop()
            isCommittingVideoStream = false
            if !closed, resumePosition == nil { updatePosition() }
            guard !closed else { return false }
            if let candidateRequest,
               recoveryAttempts < 2,
               candidateRequest.isTranscoding,
               candidateRequest.canRetryCompatibleVideo(after: candidateError),
               !Task.isCancelled,
               request.id == previousRequest.id,
               player.currentItem === previousItem {
                isSwitchingVideoStream = false
                transferredRecovery = true
                return await replaceVideoStream(
                    audioIndex: audioIndex, externalSubtitle: externalSubtitle,
                    selectsExternalSubtitle: selectsExternalSubtitle, changesAudio: changesAudio,
                    subtitleDelay: subtitleDelay, quality: quality, resumePosition: commitPosition,
                    compatibilityError: candidateError, recoveryAttempts: recoveryAttempts + 1,
                    failedRecoveryRequest: candidateRequest
                )
            }
            request = previousRequest
            if !(error is CancellationError) {
                if error as? NativeVideoSource.SelectionError == .subtitleDelayUnavailable {
                    videoStreamError = "Reset subtitle timing before changing to this stream. Your previous stream is still selected."
                } else if error as? NativeVideoSource.SelectionError == .externalSubtitleUnsupportedTiming {
                    videoStreamError = "Timing could not be adjusted for this subtitle file. Your previous timing is still selected."
                } else {
                    videoStreamError = "That stream could not be opened. Your previous stream is still selected."
                }
            }
            return false
        }
    }

}
