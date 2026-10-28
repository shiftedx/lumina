import Foundation
import GoogleCast
import LuminaCore
import Observation
import SwiftUI
import UIKit

/// Owns an opt-in Cast handoff for one native video session. The receiver sees only the
/// short-lived, ticketed media URL; the signed-in playback request never leaves this device.
@MainActor @Observable
final class NativeVideoCasting: NSObject {
    private(set) var isCasting = false
    private(set) var isPlaying = false
    private(set) var position: Double = 0
    private(set) var receiverName: String?
    private(set) var errorMessage: String?
    private(set) var volume: Double = 1
    private(set) var isMuted = false
    private(set) var canReturnToDevice = false

    @ObservationIgnored private weak var playback: PlaybackModel?
    @ObservationIgnored private weak var remoteMediaClient: GCKRemoteMediaClient?
    @ObservationIgnored private var sessionManager: GCKSessionManager?
    @ObservationIgnored private var issuedTicket: ChromecastTicket?
    @ObservationIgnored private var handoffTask: Task<Void, Never>?
    @ObservationIgnored private var handoffGeneration = UUID()
    @ObservationIgnored private var requestAwaiter: CastRequestAwaiter?
    @ObservationIgnored private var castIntent = false
    @ObservationIgnored private var isReturning = false
    @ObservationIgnored private var statusTask: Task<Void, Never>?
    @ObservationIgnored private var renewalTask: Task<Void, Never>?

    static func configure() {
        guard !GCKCastContext.isSharedInstanceInitialized() else { return }
        let criteria = GCKDiscoveryCriteria(applicationID: kGCKDefaultMediaReceiverApplicationID)
        let options = GCKCastOptions(discoveryCriteria: criteria)
        options.disableAnalyticsLogging = true
        // The SDK first-tap permission flow requires autostart to remain enabled.
        options.disableDiscoveryAutostart = false
        options.startDiscoveryAfterFirstTapOnCastButton = true
        let logger = GCKLogger.sharedInstance()
        logger.loggingEnabled = false
        logger.fileLoggingEnabled = false
        logger.consoleLoggingEnabled = false
        _ = GCKCastContext.setSharedInstanceWith(options, error: nil)
    }

    func attach(_ model: PlaybackModel) {
        Self.configure()
        detachListeners()
        playback = model
        model.castOwner = self
        guard GCKCastContext.isSharedInstanceInitialized() else {
            errorMessage = "Chromecast is unavailable on this device."
            return
        }
        let manager = GCKCastContext.sharedInstance().sessionManager
        sessionManager = manager
        manager.add(self)
        if let session = manager.currentCastSession {
            bind(to: session)
        }
    }

    /// Stops only the media ticket this playback session owns, then removes all SDK listeners.
    /// It intentionally never resumes local video because its caller is closing that video.
    func detach() {
        castIntent = false
        errorMessage = nil
        isReturning = false
        handoffGeneration = UUID()
        handoffTask?.cancel()
        handoffTask = nil
        let ticket = issuedTicket
        statusTask?.cancel()
        renewalTask?.cancel()
        if let ticket, ownsReceiverMedia(ticket) {
            remoteMediaClient?.stop()
            _ = sessionManager?.endSessionAndStopCasting(true)
        }
        if let ticket { revoke(ticket) }
        isCasting = false
        isPlaying = false
        canReturnToDevice = false
        receiverName = nil
        playback?.castOwner = nil
        playback = nil
        detachListeners()
    }

    func togglePlayback() {
        guard isCasting, let remoteMediaClient else { return }
        if isPlaying { remoteMediaClient.pause() }
        else { remoteMediaClient.play() }
    }

    func pause() {
        guard isCasting else { return }
        remoteMediaClient?.pause()
    }

    func seek(to requestedPosition: Double) {
        guard isCasting, requestedPosition.isFinite, requestedPosition >= 0,
              let remoteMediaClient else { return }
        let options = GCKMediaSeekOptions()
        options.interval = requestedPosition
        options.relative = false
        options.resumeState = isPlaying ? .play : .pause
        remoteMediaClient.seek(with: options)
    }

    func setVolume(_ value: Double) {
        guard isCasting, value.isFinite, let remoteMediaClient else { return }
        volume = min(1, max(0, value))
        remoteMediaClient.setStreamVolume(Float(volume))
    }

    func toggleMute() {
        guard isCasting, let remoteMediaClient else { return }
        isMuted.toggle()
        remoteMediaClient.setStreamMuted(isMuted)
    }

    func requestCastFromUser() {
        castIntent = true
        errorMessage = nil
        if let session = sessionManager?.currentCastSession {
            bind(to: session)
            beginHandoff()
        }
    }

    /// Stops the ticketed receiver item and waits for the Cast SDK acknowledgement before local
    /// playback is restored. A failed stop leaves the local player paused rather than duplicating
    /// playback on two outputs.
    func returnToDevice() {
        guard canReturnToDevice, !isReturning, let model = playback else { return }
        isReturning = true
        let generation = handoffGeneration
        let ticket = issuedTicket
        handoffTask?.cancel()
        handoffTask = Task { [weak self, weak model] in
            guard let self, let model else { return }
            defer {
                if self.handoffGeneration == generation {
                    self.isReturning = false
                    self.handoffTask = nil
                }
            }
            do {
                let ownsTicket = ticket.map(self.ownsReceiverMedia) ?? false
                let status = ownsTicket ? self.remoteMediaClient?.mediaStatus : nil
                let savedPosition = ownsTicket
                    ? (self.remoteMediaClient?.approximateStreamPosition() ?? self.position)
                    : self.position
                let savedPlaying = ownsTicket ? status?.playerState == .playing : self.isPlaying
                if ownsTicket, let remoteMediaClient = self.remoteMediaClient {
                    try await self.awaitCompletion(of: remoteMediaClient.stop(), timeout: .seconds(30))
                }
                guard !Task.isCancelled, self.handoffGeneration == generation,
                      self.playback === model else { return }
                self.isCasting = false
                self.isPlaying = false
                self.canReturnToDevice = false
                if let ticket { self.revoke(ticket) }
                await model.returnFromCast(position: savedPosition, playing: savedPlaying)
                if ownsTicket { _ = self.sessionManager?.endSessionAndStopCasting(true) }
            } catch {
                self.showFailure("Couldn’t stop Chromecast playback. Try again.")
            }
        }
    }

    private func bind(to session: GCKCastSession) {
        receiverName = session.device.friendlyName
        volume = Double(session.currentDeviceVolume)
        isMuted = session.currentDeviceMuted
        if remoteMediaClient !== session.remoteMediaClient {
            remoteMediaClient?.remove(self)
            remoteMediaClient = session.remoteMediaClient
            session.remoteMediaClient?.add(self)
        }
    }

    private func beginHandoff() {
        guard castIntent, !isCasting, handoffTask == nil, let model = playback,
              !model.request.item.isAudio, model.canUseVideoActions else { return }
        let generation = UUID()
        let requestID = model.request.id
        handoffGeneration = generation
        errorMessage = nil
        handoffTask = Task { [weak self, weak model] in
            guard let self, let model else { return }
            defer { if self.handoffGeneration == generation { self.handoffTask = nil } }
            var ticket: ChromecastTicket?
            do {
                guard let selection = await model.chromecastTrackSelection() else {
                    throw NativeVideoCastError.unsupportedTrackSelection
                }
                guard self.isCurrent(model, requestID: requestID, generation: generation) else { return }
                let localPosition = self.livePosition(for: model)
                let localPlaying = model.videoPlaybackRequested
                let playbackRate = model.preferredVideoRate
                guard (0.5 ... 2).contains(playbackRate) else {
                    throw NativeVideoCastError.unsupportedPlaybackRate
                }
                let issued = try await model.store.prepareChromecast(
                    itemID: model.request.mediaSourceID,
                    positionSeconds: localPosition,
                    audioIndex: selection.audioIndex,
                    subtitle: selection.subtitle
                )
                ticket = issued
                guard self.isCurrent(model, requestID: requestID, generation: generation) else {
                    self.revoke(issued)
                    return
                }
                self.issuedTicket = issued
                try await self.load(ticket: issued, localPosition: localPosition, localPlaying: localPlaying,
                                    playbackRate: playbackRate)
                guard self.isCurrent(model, requestID: requestID, generation: generation) else {
                    self.stopAndRevokeOwnedTicket(issued)
                    return
                }
                let acknowledgement = try await self.waitForReceiverAcknowledgement(
                    ticket: issued,
                    model: model
                )
                guard self.isCurrent(model, requestID: requestID, generation: generation) else {
                    self.stopAndRevokeOwnedTicket(issued)
                    return
                }
                self.isCasting = true
                self.canReturnToDevice = true
                self.castIntent = false
                model.beginCastHandoff()
                self.position = acknowledgement.position
                self.isPlaying = acknowledgement.isPlaying
                model.receiveCastStatus(position: acknowledgement.position, playing: acknowledgement.isPlaying)
                self.startRemoteMaintenance(ticket: issued)
            } catch is CancellationError {
                if let ticket { self.stopAndRevokeOwnedTicket(ticket) }
            } catch {
                if let ticket { self.stopAndRevokeOwnedTicket(ticket) }
                self.showFailure(self.castFailureMessage(for: error))
            }
        }
    }

    private func isCurrent(_ model: PlaybackModel, requestID: String, generation: UUID) -> Bool {
        !Task.isCancelled && handoffGeneration == generation && playback === model
            && model.request.id == requestID && model.canUseVideoActions
    }

    private func livePosition(for model: PlaybackModel) -> Double {
        let position = model.player.currentTime().seconds
        return position.isFinite && position >= 0 ? position : model.elapsedSeconds
    }

    private func load(ticket: ChromecastTicket, localPosition: Double, localPlaying: Bool,
                      playbackRate: Double) async throws {
        guard let remoteMediaClient else { throw NativeVideoCastError.receiverUnavailable }
        let builder = GCKMediaInformationBuilder(contentURL: ticket.mediaURL)
        builder.streamType = .buffered
        builder.contentType = ticket.contentType
        builder.streamDuration = ticket.durationSeconds ?? 0
        if ticket.contentType.lowercased().contains("mpegurl") {
            builder.hlsSegmentFormat = .FMP4
            builder.hlsVideoSegmentFormat = .FMP4
        }
        let metadata = GCKMediaMetadata(metadataType: .movie)
        metadata.setString(ticket.title, forKey: kGCKMetadataKeyTitle)
        builder.metadata = metadata
        if let subtitle = ticket.subtitle,
           let track = GCKMediaTrack(identifier: 1, contentIdentifier: subtitle.url.absoluteString,
                                     contentType: "text/vtt", type: .text, textSubtype: .subtitles,
                                     name: subtitle.label, languageCode: subtitle.language, customData: nil) {
            builder.mediaTracks = [track]
        }
        let request = GCKMediaLoadRequestDataBuilder()
        request.mediaInformation = builder.build()
        request.autoplay = NSNumber(value: localPlaying)
        request.startTime = localPosition
        request.playbackRate = Float(playbackRate)
        if ticket.subtitle != nil { request.activeTrackIDs = [1] }
        try await awaitCompletion(of: remoteMediaClient.loadMedia(with: request.build()))
        remoteMediaClient.requestStatus()
    }

    private func waitForReceiverAcknowledgement(ticket: ChromecastTicket,
                                                model: PlaybackModel) async throws -> ReceiverAcknowledgement {
        let deadline = ContinuousClock.now.advanced(by: .seconds(30))
        for _ in 0..<48 {
            guard ContinuousClock.now < deadline else { throw NativeVideoCastError.receiverDidNotAcknowledge }
            try Task.checkCancellation()
            guard let remoteMediaClient else { throw NativeVideoCastError.receiverUnavailable }
            let status = remoteMediaClient.mediaStatus
            let receiverURL = status?.mediaInformation?.contentURL as URL?
            let receiverPosition = remoteMediaClient.approximateStreamPosition()
            let receiverState = status?.playerState
            let receiverPlaying = receiverState == .playing
            let localPosition = livePosition(for: model)
            let localPlaying = model.videoPlaybackRequested
            if (receiverState == .playing || receiverState == .paused),
               ChromecastHandoff.acknowledges(expectedMediaURL: ticket.mediaURL,
                                               receiverMediaURL: receiverURL,
                                               localPosition: localPosition,
                                               receiverPosition: receiverPosition,
                                               localPlaying: localPlaying,
                                               receiverPlaying: receiverPlaying) {
                return ReceiverAcknowledgement(position: receiverPosition, isPlaying: receiverPlaying)
            }
            if receiverURL == ticket.mediaURL {
                if abs(receiverPosition - localPosition) > 1.5 {
                    try await synchronizeReceiver(remoteMediaClient, position: localPosition)
                }
                if localPlaying, receiverState == .paused {
                    try await awaitCompletion(of: remoteMediaClient.play(), timeout: .seconds(30))
                } else if !localPlaying, receiverState == .playing {
                    try await awaitCompletion(of: remoteMediaClient.pause(), timeout: .seconds(30))
                }
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw NativeVideoCastError.receiverDidNotAcknowledge
    }

    private func synchronizeReceiver(_ remoteMediaClient: GCKRemoteMediaClient,
                                     position: Double) async throws {
        let seek = GCKMediaSeekOptions()
        seek.interval = position
        seek.relative = false
        seek.resumeState = .unchanged
        try await awaitCompletion(of: remoteMediaClient.seek(with: seek), timeout: .seconds(30))
    }

    private func awaitCompletion(of request: GCKRequest, timeout: Duration = .seconds(30)) async throws {
        try Task.checkCancellation()
        let awaiter = CastRequestAwaiter()
        requestAwaiter = awaiter
        defer { if requestAwaiter === awaiter { requestAwaiter = nil } }
        try await awaiter.wait(for: request, timeout: timeout)
    }

    private func update(from status: GCKMediaStatus?) {
        guard let status else { return }
        guard let ticket = issuedTicket, status.mediaInformation?.contentURL as URL? == ticket.mediaURL else {
            if isCasting { foreignReceiverMedia() }
            return
        }
        position = remoteMediaClient?.approximateStreamPosition() ?? status.streamPosition
        isPlaying = status.playerState == .playing
        volume = Double(status.volume)
        isMuted = status.isMuted
        if isCasting { playback?.receiveCastStatus(position: position, playing: isPlaying) }
    }

    private func returnFromEndedSession() {
        guard isCasting || canReturnToDevice else { return }
        // A disconnect does not prove the receiver stopped. Keep local video paused and make the
        // member choose Return rather than risk duplicate playback.
        isCasting = false
        isPlaying = false
        showFailure("Chromecast disconnected. Return to this device when you’re ready.")
    }

    private func publishRemoteStatus() {
        guard let status = remoteMediaClient?.mediaStatus else { return }
        update(from: status)
    }

    private func ownsReceiverMedia(_ ticket: ChromecastTicket) -> Bool {
        remoteMediaClient?.mediaStatus?.mediaInformation?.contentURL as URL? == ticket.mediaURL
    }

    private func foreignReceiverMedia() {
        isCasting = false
        isPlaying = false
        statusTask?.cancel()
        renewalTask?.cancel()
        canReturnToDevice = true
        showFailure("Chromecast changed media. Return to this device to continue.")
    }

    private func stopAndRevokeOwnedTicket(_ ticket: ChromecastTicket) {
        if ownsReceiverMedia(ticket) { remoteMediaClient?.stop() }
        revoke(ticket)
    }

    private func revoke(_ ticket: ChromecastTicket) {
        if issuedTicket?.ticketID == ticket.ticketID { issuedTicket = nil }
        guard let store = playback?.store else { return }
        Task { try? await store.revokeChromecast(ticketID: ticket.ticketID) }
    }

    private func startRemoteMaintenance(ticket: ChromecastTicket) {
        statusTask?.cancel()
        renewalTask?.cancel()
        statusTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, self.isCasting, self.issuedTicket?.ticketID == ticket.ticketID else { return }
                self.remoteMediaClient?.requestStatus()
                self.publishRemoteStatus()
            }
        }
        renewalTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                guard let self, self.isCasting, self.issuedTicket?.ticketID == ticket.ticketID else { return }
                guard UIApplication.shared.applicationState == .active,
                      let store = self.playback?.store else { continue }
                do { try await store.renewChromecast(ticketID: ticket.ticketID) }
                catch { self.showFailure("Chromecast access expired. Return to this device to continue.") }
            }
        }
    }

    private func detachListeners() {
        remoteMediaClient?.remove(self)
        if let sessionManager { sessionManager.remove(self) }
        remoteMediaClient = nil
        sessionManager = nil
    }

    private func castFailureMessage(for error: Error) -> String {
        switch error {
        case NativeVideoCastError.unsupportedTrackSelection:
            "Chromecast can’t use the selected audio or captions. Choose Automatic tracks and try again."
        case NativeVideoCastError.unsupportedPlaybackRate:
            "Chromecast supports speeds from 0.5× to 2×. Choose a supported speed and try again."
        case NativeVideoCastError.receiverUnavailable:
            "The selected Chromecast is no longer available."
        case NativeVideoCastError.receiverDidNotAcknowledge:
            "Chromecast didn’t confirm playback. Local playback is still active."
        default:
            "Couldn’t start Chromecast playback. Local playback is still active."
        }
    }

    private func showFailure(_ message: String) {
        errorMessage = message
    }
}

private struct ReceiverAcknowledgement {
    let position: Double
    let isPlaying: Bool
}

extension NativeVideoCasting: @preconcurrency GCKSessionManagerListener, @preconcurrency GCKRemoteMediaClientListener {
    func sessionManager(_: GCKSessionManager, didStart session: GCKCastSession) {
        bind(to: session)
        if castIntent { beginHandoff() }
    }

    func sessionManager(_: GCKSessionManager, didResume session: GCKCastSession) {
        bind(to: session)
        if isCasting, let ticket = issuedTicket, ownsReceiverMedia(ticket) {
            startRemoteMaintenance(ticket: ticket)
        }
    }

    func sessionManager(_: GCKSessionManager, didEnd _: GCKCastSession, withError _: Error?) {
        if !isReturning { returnFromEndedSession() }
        remoteMediaClient?.remove(self)
        remoteMediaClient = nil
    }

    func sessionManager(_: GCKSessionManager, didFailToStart _: GCKCastSession, withError _: Error) {
        castIntent = false
        showFailure("Couldn’t connect to that Chromecast.")
    }

    func remoteMediaClient(_: GCKRemoteMediaClient, didUpdate mediaStatus: GCKMediaStatus?) {
        update(from: mediaStatus)
    }

    func sessionManager(_: GCKSessionManager, castSession _: GCKCastSession,
                        didReceiveDeviceVolume volume: Float, muted: Bool) {
        self.volume = Double(volume)
        isMuted = muted
    }
}

/// The SDK owns device discovery and the receiver picker; Lumina supplies only its approved
/// glyph over that native control so the overlay keeps its flat visual language.
struct NativeVideoCastButton: View {
    let playback: PlaybackModel

    var body: some View {
        ZStack {
            NativeGoogleCastButton { playback.castOwner?.requestCastFromUser() }
            LuminaIcon(glyph: .cast, size: 24)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("videoChromecastButton")
        .accessibilityLabel("Chromecast")
        .disabled(playback.request.item.isAudio)
    }
}

/// Compact active-Cast state for whichever video chrome chooses to place it.
struct NativeVideoCastStatus: View {
    let playback: PlaybackModel

    var body: some View {
        if let casting = playback.castOwner, casting.isCasting || casting.canReturnToDevice {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    LuminaIcon(glyph: .cast, size: 20)
                    Text(casting.receiverName ?? "Chromecast")
                        .lineLimit(1)
                    Button("Return to this device") { casting.returnToDevice() }
                        .buttonStyle(.plain)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("videoCastReturnButton")
                }
                if let error = casting.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.82))
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("videoCastStatus")
        } else if let casting = playback.castOwner, let error = casting.errorMessage {
            Text(error)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.82))
                .accessibilityIdentifier("videoCastError")
        }
    }
}

private struct NativeGoogleCastButton: UIViewRepresentable {
    let onTap: () -> Void

    func makeUIView(context _: Context) -> GCKUICastButton {
        NativeVideoCasting.configure()
        let button = GCKUICastButton(frame: .zero)
        button.tintColor = .clear
        button.accessibilityLabel = "Chromecast"
        button.accessibilityIdentifier = "videoChromecastButton"
        button.addAction(UIAction { _ in onTap() }, for: .touchDown)
        return button
    }

    func updateUIView(_: GCKUICastButton, context _: Context) {}
}

private enum NativeVideoCastError: Error {
    case receiverUnavailable
    case receiverDidNotAcknowledge
    case unsupportedTrackSelection
    case unsupportedPlaybackRate
}

@MainActor
final class CastRequestAwaiter: NSObject, @preconcurrency GCKRequestDelegate {
    private var continuation: CheckedContinuation<Void, Error>?
    private var timeoutTask: Task<Void, Never>?

    func wait(for request: GCKRequest, timeout: Duration) async throws {
        try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            request.delegate = self
            timeoutTask = Task { [weak self, weak request] in
                do {
                    try await Task.sleep(for: timeout)
                    guard !Task.isCancelled else { return }
                    request?.cancel()
                    self?.finish(.failure(NativeVideoCastError.receiverDidNotAcknowledge))
                } catch { }
            }
        }
    }

    func requestDidComplete(_: GCKRequest) {
        finish(.success(()))
    }

    func request(_: GCKRequest, didFailWithError _: GCKError) {
        finish(.failure(NativeVideoCastError.receiverUnavailable))
    }

    func request(_: GCKRequest, didAbortWith _: GCKRequestAbortReason) {
        finish(.failure(CancellationError()))
    }

    private func finish(_ result: Result<Void, Error>) {
        timeoutTask?.cancel()
        timeoutTask = nil
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(with: result)
    }
}
