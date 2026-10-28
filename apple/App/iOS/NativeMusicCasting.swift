import Foundation
import GoogleCast
import LuminaCore
import Observation
import SwiftUI
import UIKit

/// Receiver-scoped music handoff. It deliberately owns no shared queue state: the app's local
/// queue remains authoritative and each receiver track receives a separate short-lived ticket.
@MainActor @Observable
final class NativeMusicCasting: NSObject {
    private(set) var isRemoteOutput = false
    private(set) var canReturnToDevice = false
    private(set) var isPlaying = false
    private(set) var position: Double = 0
    private(set) var receiverName: String?
    private(set) var errorMessage: String?
    private(set) var volume: Double = 1
    private(set) var isMuted = false

    @ObservationIgnored private weak var audio: NativeAudioSession?
    @ObservationIgnored private weak var sessionManager: GCKSessionManager?
    @ObservationIgnored private weak var remoteMediaClient: GCKRemoteMediaClient?
    @ObservationIgnored private var ticket: ChromecastTicket?
    @ObservationIgnored private var receipt: MusicReceiverTicket?
    @ObservationIgnored private var ticketIndex: Int?
    @ObservationIgnored private var queueEntries: [(index: Int, entry: MusicReceiverQueueEntry)] = []
    @ObservationIgnored private var queueItemIndexes: [UInt: Int] = [:]
    @ObservationIgnored private var castIntent = false
    @ObservationIgnored private var handoffTask: Task<Void, Never>?
    @ObservationIgnored private var statusTask: Task<Void, Never>?
    @ObservationIgnored private var renewalTask: Task<Void, Never>?
    @ObservationIgnored private var isReturning = false
    @ObservationIgnored private var lifecycleGeneration = 0
    private(set) var queueLimited = false
    private(set) var queueLimit = 0

    func attach(_ audio: NativeAudioSession) {
        self.audio = audio
        NativeVideoCasting.configure()
        guard GCKCastContext.isSharedInstanceInitialized() else { return }
        if sessionManager == nil {
            let manager = GCKCastContext.sharedInstance().sessionManager
            sessionManager = manager
            manager.add(self)
        }
        if let session = sessionManager?.currentCastSession { bind(to: session) }
    }

    func detach() {
        lifecycleGeneration &+= 1
        castIntent = false
        handoffTask?.cancel(); handoffTask = nil
        statusTask?.cancel(); renewalTask?.cancel()
        if let receipt, ownsReceiverMedia(receipt) {
            remoteMediaClient?.stop()
            _ = sessionManager?.endSessionAndStopCasting(true)
        }
        if let ticket { revoke(ticket) }
        ticket = nil; receipt = nil; ticketIndex = nil; queueEntries = []; queueItemIndexes = [:]; queueLimited = false; queueLimit = 0
        isRemoteOutput = false; canReturnToDevice = false; isPlaying = false
        errorMessage = nil
    }

    func requestCastFromUser() {
        guard audio?.isSonos != true, !isRemoteOutput, !canReturnToDevice else { return }
        lifecycleGeneration &+= 1
        castIntent = true
        errorMessage = nil
        if let session = sessionManager?.currentCastSession {
            bind(to: session)
            beginHandoff()
        }
    }

    func togglePlayback() {
        guard isRemoteOutput else { return }
        if isPlaying { remoteMediaClient?.pause() } else { remoteMediaClient?.play() }
    }

    func pause() { guard isRemoteOutput else { return }; remoteMediaClient?.pause() }

    func seek(to value: Double) {
        guard isRemoteOutput, value.isFinite else { return }
        let seek = GCKMediaSeekOptions()
        seek.interval = max(0, value); seek.relative = false
        seek.resumeState = isPlaying ? .play : .pause
        remoteMediaClient?.seek(with: seek)
    }

    func setVolume(_ value: Double) {
        guard isRemoteOutput, value.isFinite else { return }
        volume = min(1, max(0, value))
        remoteMediaClient?.setStreamVolume(Float(volume))
    }

    func toggleMute() {
        guard isRemoteOutput else { return }
        isMuted.toggle()
        remoteMediaClient?.setStreamMuted(isMuted)
    }

    func step(by offset: Int) async {
        guard isRemoteOutput else { return }
        if offset < 0 { remoteMediaClient?.queuePreviousItem() }
        else { remoteMediaClient?.queueNextItem() }
    }

    func returnToDevice() {
        guard canReturnToDevice, !isReturning, let audio else { return }
        guard let savedReceipt = receipt, let savedTicket = ticket else { return }
        let generation = lifecycleGeneration
        isReturning = true
        update(from: remoteMediaClient?.mediaStatus)
        let savedIndex = ticketIndex ?? audio.musicCastState()?.index
        let savedPosition = remoteMediaClient?.approximateStreamPosition() ?? position
        let savedPlaying = remoteMediaClient?.mediaStatus?.playerState == .playing
        handoffTask = Task { [weak self, weak audio] in
            guard let self, let audio else { return }
            defer {
                if self.lifecycleGeneration == generation {
                    self.isReturning = false
                    self.handoffTask = nil
                }
            }
            do {
                let ownsTicket = self.ownsReceiverMedia(savedReceipt)
                if ownsTicket, let client = self.remoteMediaClient {
                    try await self.awaitCompletion(client.stop())
                }
                guard self.lifecycleGeneration == generation,
                      self.ticket?.ticketID == savedTicket.ticketID,
                      self.receipt?.ticket.ticketID == savedReceipt.ticket.ticketID else { return }
                guard let savedIndex else { throw NativeMusicCastError.unavailable }
                self.isRemoteOutput = false
                self.canReturnToDevice = false
                self.isPlaying = false
                self.revoke(savedTicket)
                guard await audio.returnFromMusicCast(index: savedIndex, position: savedPosition,
                                                      playing: savedPlaying) else {
                    throw NativeMusicCastError.returnFailed
                }
                guard self.lifecycleGeneration == generation else { return }
                if ownsTicket { _ = self.sessionManager?.endSessionAndStopCasting(true) }
            } catch {
                if self.lifecycleGeneration == generation {
                    self.errorMessage = "Couldn’t return playback to this device."
                }
            }
        }
    }

    private func beginHandoff() {
        guard castIntent, !isRemoteOutput, handoffTask == nil, let audio,
              let state = audio.musicCastState(), let playback = audio.playback else { return }
        let generation = lifecycleGeneration
        let projection = audio.musicCastTracks(from: state.index)
        guard !projection.isEmpty else { return }
        handoffTask = Task { [weak self, weak audio] in
            guard let self, let audio else { return }
            defer {
                if self.lifecycleGeneration == generation { self.handoffTask = nil }
            }
            var issuedReceipt: MusicReceiverTicket?
            do {
                let issued = try await audio.prepareMusicCastTicket(for: state.index, position: state.position)
                issuedReceipt = issued
                guard self.canContinueHandoff(generation, audio: audio, playback: playback,
                                               state: state, projection: projection) else {
                    self.revoke(issued.ticket)
                    return
                }
                guard issued.queue.count == projection.count,
                      zip(issued.queue, projection).allSatisfy({
                          $0.0.itemID.caseInsensitiveCompare($0.1.track.id) == .orderedSame
                      }) else {
                    throw NativeMusicCastError.unavailable
                }
                let ticket = issued.ticket
                guard self.canContinueHandoff(generation, audio: audio, playback: playback,
                                               state: state, projection: projection) else {
                    self.revoke(ticket)
                    return
                }
                self.ticket = ticket; self.ticketIndex = state.index
                self.receipt = issued
                self.queueEntries = zip(projection, issued.queue).map { (index: $0.0.index, entry: $0.1) }
                self.queueLimit = issued.queueLimit
                self.queueLimited = audio.musicCastQueueIsLimited(from: state.index, limit: issued.queueLimit)
                try await self.load(ticket: ticket, queue: self.queueEntries,
                                    position: state.position, playing: state.playing)
                guard self.canContinueHandoff(generation, audio: audio, playback: playback,
                                               state: state, projection: projection),
                      self.ticket?.ticketID == ticket.ticketID else {
                    self.stopAndRevokeOwned(issued)
                    return
                }
                let acknowledgement = try await self.waitForAcknowledgement(ticket: ticket, audio: audio)
                guard self.canContinueHandoff(generation, audio: audio, playback: playback,
                                               state: state, projection: projection),
                      self.ticket?.ticketID == ticket.ticketID else {
                    self.stopAndRevokeOwned(issued)
                    return
                }
                self.position = acknowledgement.position; self.isPlaying = acknowledgement.playing
                self.isRemoteOutput = true; self.canReturnToDevice = true; self.castIntent = false
                audio.beginMusicCastHandoff()
                audio.receiveMusicCastStatus(index: state.index, position: acknowledgement.position,
                                             playing: acknowledgement.playing)
                self.startMaintenance(ticket: ticket)
            } catch {
                guard self.lifecycleGeneration == generation else { return }
                if let issuedReceipt, self.receipt?.ticket.ticketID == issuedReceipt.ticket.ticketID {
                    self.stopAndRevokeOwned(issuedReceipt)
                } else if let issuedReceipt {
                    self.revoke(issuedReceipt.ticket)
                }
                self.errorMessage = "Couldn’t start Chromecast music playback. Local playback is still active."
            }
        }
    }

    private func canContinueHandoff(_ generation: Int, audio: NativeAudioSession,
                                    playback: PlaybackModel,
                                    state: (index: Int, track: MusicTrack, position: Double, playing: Bool),
                                    projection: [(index: Int, track: MusicTrack)]) -> Bool {
        guard lifecycleGeneration == generation, !Task.isCancelled, castIntent,
              audio.playback === playback, let current = audio.musicCastState() else { return false }
        guard current.index == state.index, current.track.id == state.track.id else { return false }
        let currentProjection = audio.musicCastTracks(from: state.index)
        return currentProjection.count == projection.count
            && zip(currentProjection, projection).allSatisfy {
                $0.0.index == $0.1.index && $0.0.track.id == $0.1.track.id
            }
    }

    private func load(ticket: ChromecastTicket, queue: [(index: Int, entry: MusicReceiverQueueEntry)],
                      position: Double, playing: Bool) async throws {
        guard let client = remoteMediaClient else { throw NativeMusicCastError.unavailable }
        let items = queue.enumerated().map { offset, value -> GCKMediaQueueItem in
            let info = GCKMediaInformationBuilder(contentURL: value.entry.mediaURL)
            info.streamType = .buffered
            info.contentType = value.entry.contentType
            info.streamDuration = value.entry.durationSeconds ?? 0
            let metadata = GCKMediaMetadata(metadataType: .musicTrack)
            metadata.setString(value.entry.title, forKey: kGCKMetadataKeyTitle)
            info.metadata = metadata
            let item = GCKMediaQueueItemBuilder()
            item.mediaInformation = info.build()
            item.autoplay = playing
            item.startTime = offset == 0 ? max(0, position) : 0
            item.preloadTime = 10
            item.customData = ["app_index": value.index]
            return item.build()
        }
        guard !items.isEmpty, items[0].mediaInformation.contentURL == ticket.mediaURL else {
            throw NativeMusicCastError.unavailable
        }
        let options = GCKMediaQueueLoadOptions()
        options.startIndex = 0
        options.playPosition = max(0, position)
        options.repeatMode = .off
        try await awaitCompletion(client.queueLoad(items, with: options))
        client.requestStatus()
    }

    private func waitForAcknowledgement(ticket: ChromecastTicket,
                                        audio: NativeAudioSession) async throws -> MusicAcknowledgement {
        let deadline = ContinuousClock.now.advanced(by: .seconds(30))
        while ContinuousClock.now < deadline {
            try Task.checkCancellation()
            guard let client = remoteMediaClient, let state = audio.musicCastState() else {
                throw NativeMusicCastError.unavailable
            }
            let status = client.mediaStatus
            let url = status?.mediaInformation?.contentURL as URL?
            let receiverPosition = client.approximateStreamPosition()
            let receiverState = status?.playerState
            let receiverPlaying = receiverState == .playing
            if (receiverState == .playing || receiverState == .paused),
               ChromecastHandoff.acknowledges(expectedMediaURL: ticket.mediaURL, receiverMediaURL: url,
                                               localPosition: state.position, receiverPosition: receiverPosition,
                                               localPlaying: state.playing, receiverPlaying: receiverPlaying) {
                return MusicAcknowledgement(position: receiverPosition, playing: receiverPlaying)
            }
            if url == ticket.mediaURL {
                if abs(receiverPosition - state.position) > 1.5 {
                    let seek = GCKMediaSeekOptions()
                    seek.interval = max(0, state.position)
                    seek.relative = false
                    seek.resumeState = state.playing ? .play : .pause
                    client.seek(with: seek)
                }
                if state.playing, receiverState == .paused { client.play() }
                if !state.playing, receiverState == .playing { client.pause() }
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw NativeMusicCastError.unavailable
    }

    private func bind(to session: GCKCastSession) {
        receiverName = session.device.friendlyName
        volume = Double(session.currentDeviceVolume); isMuted = session.currentDeviceMuted
        if remoteMediaClient !== session.remoteMediaClient {
            remoteMediaClient?.remove(self)
            remoteMediaClient = session.remoteMediaClient
            remoteMediaClient?.add(self)
        }
    }

    private func startMaintenance(ticket: ChromecastTicket) {
        statusTask?.cancel(); renewalTask?.cancel()
        statusTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, self.isRemoteOutput, self.ticket?.ticketID == ticket.ticketID else { return }
                guard !Task.isCancelled else { return }
                guard UIApplication.shared.applicationState == .active else { continue }
                self.remoteMediaClient?.requestStatus()
                self.update(from: self.remoteMediaClient?.mediaStatus)
            }
        }
        renewalTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                guard let self, self.isRemoteOutput, self.ticket?.ticketID == ticket.ticketID,
                      UIApplication.shared.applicationState == .active,
                      let store = self.audio?.playback?.store else { continue }
                do { try await store.renewMusicReceiver(ticketID: ticket.ticketID) }
                catch { self.errorMessage = "Chromecast music access expired. Return to this device to continue." }
            }
        }
    }

    private func update(from status: GCKMediaStatus?) {
        guard let status, ticket != nil else { return }
        let receiverURL = status.mediaInformation?.contentURL as URL?
        let mappedIndex = queueItemIndexes[status.currentItemID]
        let entry = mappedIndex.flatMap { index in queueEntries.first(where: { $0.index == index }) }
            ?? ticketIndex.flatMap { index in
                queueEntries.first(where: { $0.index == index && $0.entry.mediaURL == receiverURL })
            }
            ?? ({
                let matches = queueEntries.filter { $0.entry.mediaURL == receiverURL }
                return matches.count == 1 ? matches[0] : nil
            }())
        guard let entry, entry.entry.mediaURL == receiverURL else {
            if isRemoteOutput { lostReceiver("Chromecast changed media. Return to this device to continue.") }
            return
        }
        if status.currentItemID != kGCKMediaQueueInvalidItemID {
            queueItemIndexes[status.currentItemID] = entry.index
        }
        position = remoteMediaClient?.approximateStreamPosition() ?? status.streamPosition
        isPlaying = status.playerState == .playing
        volume = Double(status.volume); isMuted = status.isMuted
        ticketIndex = entry.index
        audio?.receiveMusicCastStatus(index: entry.index, position: position, playing: isPlaying)
    }

    private func lostReceiver(_ message: String) {
        isRemoteOutput = false; isPlaying = false; canReturnToDevice = true
        statusTask?.cancel(); renewalTask?.cancel(); errorMessage = message
    }

    private func ownsReceiverMedia(_ receipt: MusicReceiverTicket) -> Bool {
        receipt.ownsMediaURL(remoteMediaClient?.mediaStatus?.mediaInformation?.contentURL as URL?)
    }

    private func stopAndRevokeOwned(_ receipt: MusicReceiverTicket) {
        if ownsReceiverMedia(receipt) { remoteMediaClient?.stop() }
        revoke(receipt.ticket)
    }

    private func revoke(_ ticket: ChromecastTicket) {
        if self.ticket?.ticketID == ticket.ticketID {
            self.ticket = nil
            self.receipt = nil
            ticketIndex = nil
        }
        guard let store = audio?.playback?.store else { return }
        Task { try? await store.revokeMusicReceiver(ticketID: ticket.ticketID) }
    }

    private func awaitCompletion(_ request: GCKRequest) async throws {
        let awaiter = CastRequestAwaiter()
        try await awaiter.wait(for: request, timeout: .seconds(30))
    }
}

private struct MusicAcknowledgement { let position: Double; let playing: Bool }
private enum NativeMusicCastError: Error { case unavailable; case returnFailed }

extension NativeMusicCasting: @preconcurrency GCKSessionManagerListener, @preconcurrency GCKRemoteMediaClientListener {
    func sessionManager(_: GCKSessionManager, didStart session: GCKCastSession) {
        bind(to: session); if castIntent { beginHandoff() }
    }
    func sessionManager(_: GCKSessionManager, didResume session: GCKCastSession) { bind(to: session) }
    func sessionManager(_: GCKSessionManager, didEnd _: GCKCastSession, withError _: Error?) {
        remoteMediaClient?.remove(self); remoteMediaClient = nil
        if !isReturning, ticket != nil || isRemoteOutput || canReturnToDevice || castIntent {
            lostReceiver("Chromecast disconnected. Return to this device when you’re ready.")
        }
    }
    func sessionManager(_: GCKSessionManager, didFailToStart _: GCKCastSession, withError _: Error) {
        castIntent = false; errorMessage = "Couldn’t connect to that Chromecast."
    }
    func remoteMediaClient(_: GCKRemoteMediaClient, didUpdate status: GCKMediaStatus?) { update(from: status) }
    func remoteMediaClient(_: GCKRemoteMediaClient, didReceive queueItems: [GCKMediaQueueItem]) {
        for item in queueItems {
            let rawIndex = (item.customData as? [String: Any])?["app_index"] as? NSNumber
                ?? (item.customData as? [String: Int])?["app_index"].map(NSNumber.init(value:))
            if let rawIndex { queueItemIndexes[item.itemID] = rawIndex.intValue }
        }
    }
    func sessionManager(_: GCKSessionManager, castSession _: GCKCastSession,
                        didReceiveDeviceVolume volume: Float, muted: Bool) {
        self.volume = Double(volume); isMuted = muted
    }
}

struct NativeMusicCastButton: View {
    let session: NativeAudioSession
    var body: some View {
        Button {
            session.casting.requestCastFromUser()
            NativeVideoCasting.presentDevicePicker()
        } label: {
            LuminaIcon(glyph: .cast, size: 24)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("musicChromecastButton")
        .accessibilityLabel("Chromecast")
        .disabled(session.isSonos || session.isRemoteOutput || session.busy)
        .frame(width: 44, height: 44)
    }
}

struct NativeMusicCastStatus: View {
    let session: NativeAudioSession
    var body: some View {
        let casting = session.casting
        if casting.isRemoteOutput || casting.canReturnToDevice {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                LuminaIcon(glyph: .cast, size: 20)
                Text(casting.receiverName ?? "Chromecast").lineLimit(1)
                Button("Return to this device") { casting.returnToDevice() }
                    .buttonStyle(.plain).frame(minHeight: 44)
                    .accessibilityIdentifier("musicCastReturnButton")
                }
                if casting.queueLimited {
                    Text("Next \(casting.queueLimit) tracks queued")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityIdentifier("musicCastStatus")
        }
    }
}
