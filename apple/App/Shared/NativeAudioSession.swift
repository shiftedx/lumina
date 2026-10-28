import AVFoundation
import LuminaCore
import Observation
import UIKit

private enum NativeAudioOutputError: LocalizedError {
    case returnRequired
    var errorDescription: String? { "Return to this device before starting another queue." }
}

@MainActor @Observable
final class NativeAudioSession {
    #if os(iOS)
    let casting = NativeMusicCasting()
    #endif
    private(set) var playback: PlaybackModel?
    private var compatibleAudioIDs = Set<String>()
    private(set) var tracks: [LuminaCore.MusicTrack] = []
    private(set) var lyrics: MusicLyrics?
    private(set) var lyricsError: String?
    private(set) var lyricsLoading = false
    private(set) var retryAllowedAt: Date?
    private(set) var speakers: [MusicSpeaker] = []
    private(set) var outputStatus: MusicOutputStatus?
    private(set) var conflict: MusicListeningSnapshot?
    private(set) var snapshot = MusicListeningSnapshot()
    private(set) var message: String?
    private(set) var busy = false
    private(set) var playbackMode = NativeMusicTransport.Mode.streaming
    var compatibility = false { didSet { persistPreferences() } }
    var normalization = true { didSet { configureTransport(); persistPreferences() } }
    var crossfadeSeconds = 0.0 {
        didSet {
            if ![0.0, 3, 6, 12].contains(crossfadeSeconds) { crossfadeSeconds = 0 }
            configureTransport(); persistPreferences()
        }
    }
    #if os(iOS)
    private static let defaultLocalVolume: Float = 1
    #else
    private static let defaultLocalVolume: Float = 0.8
    #endif
    var volume: Float = NativeAudioSession.defaultLocalVolume {
        didSet {
            let clamped: Float = volume.isFinite ? min(1, max(0, volume)) : Self.defaultLocalVolume
            if volume != clamped { volume = clamped; return }
            configureTransport(); persistPreferences()
        }
    }
    private var restoringPreferences = false
    private(set) var sleepUntil: Date?
    private(set) var pendingSpeakerStop: String?
    private(set) var remoteVolumeDraft: Float?
    private var volumeTask: Task<Void, Never>?
    private var interruptionObserver: NSObjectProtocol?
    private var routeObserver: NSObjectProtocol?
    private var store: LuminaStore?
    private var identity: String?
    private var restoreTask: Task<Void, Never>?
    private var restoreStartTask: Task<Void, Never>?
    private var restoreTaskGeneration = 0
    private var startTask: Task<Void, Never>?
    private var lyricsTask: Task<Void, Never>?
    private var statusTask: Task<Void, Never>?
    private var syncTask: Task<Void, Never>?
    private var sleepTimer: DispatchSourceTimer?
    private var transport: NativeMusicTransport?
    private var dirty = false
    private var syncing = false
    private var syncAgain = false
    private var syncAgainRestoreTaskID: Int?
    private var generation = 0
    private var lastCheckpoint = Date.distantPast
    private var preparedTracks: [LuminaCore.MusicTrack]?
    private var transferring = false
    private var refreshingShared = false
    private var waitingForEntry: Int?
    private var waitingNextIndex: Int?
    private var restoringPausedQueue = false
    private var transportReady = false
    private var pendingTVRestoreToggle = false

    #if LUMINA_UI_TESTING
    /// A deliberately small, non-identifying snapshot for the paused-queue
    /// restore race. It is surfaced through the existing Home semantic root
    /// only in UI-test builds, then removed once the ordering is proven.
    private(set) var uiTestingTVRestoreDiagnostics = "outcome=initial events=initial restoring=0 playback=0 ready=0 pending=0 conflict=0 index=0 generation=0"
    private var uiTestingTVRestoreEvents = ["initial"]

    private func uiTestingTVRestoreFailureKind(_ error: Error) -> String {
        let error = error as NSError
        switch error.domain {
        case NSURLErrorDomain:
            return "url-\(error.code)"
        case NSPOSIXErrorDomain:
            return "posix-\(error.code)"
        default:
            return "other"
        }
    }

    private func uiTestingTVRestoreCancellationMarker() -> String {
        "taskcancelled=\(Task.isCancelled ? 1 : 0)"
    }

    private func recordTVRestoreDiagnostic(_ outcome: String) {
        uiTestingTVRestoreEvents.append(outcome)
        if uiTestingTVRestoreEvents.count > 8 { uiTestingTVRestoreEvents.removeFirst() }
        uiTestingTVRestoreDiagnostics = "outcome=\(outcome) events=\(uiTestingTVRestoreEvents.joined(separator: ",")) restoring=\(restoringPausedQueue ? 1 : 0) playback=\(playback == nil ? 0 : 1) ready=\(transportReady ? 1 : 0) pending=\(pendingTVRestoreToggle ? 1 : 0) conflict=\(conflict == nil ? 0 : 1) index=\(snapshot.currentIndex == nil ? 0 : 1) generation=\(generation)"
    }
    #endif

    var isSonos: Bool { snapshot.activeOutput.kind == "sonos" && snapshot.activeOutput.ticketID != nil }
    var isRemoteOutput: Bool {
        #if os(iOS)
        casting.isRemoteOutput || casting.canReturnToDevice
        #else
        false
        #endif
    }
    var repeatMode: String { snapshot.repeatMode }
    var pendingEntries: [MusicQueueEntry] { snapshot.entries.filter { $0.state != nil && $0.state != "playable" } }

    init() {
        #if os(iOS)
        casting.attach(self)
        #endif
        interruptionObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] notification in
            let began = (notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt) == AVAudioSession.InterruptionType.began.rawValue
            if began { Task { @MainActor in self?.transport?.pause() } }
        }
        routeObserver = NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] notification in
            let unplugged = (notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt) == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue
            if unplugged { Task { @MainActor in self?.transport?.pause() } }
        }
    }

    func adoptForPlayback(_ model: PlaybackModel) async throws {
        if playback === model { return }
        guard !transferring else { throw URLError(.cancelled) }
        guard !isRemoteOutput else {
            message = NativeAudioOutputError.returnRequired.localizedDescription
            throw NativeAudioOutputError.returnRequired
        }
        guard let owner = model.store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        cancelRestoreTask()
        let token = generation
        if model.store.isOffline {
            pendingSpeakerStop = nil
            snapshot.activeOutput = .init()
        }
        if let ticket = pendingSpeakerStop ?? snapshot.activeOutput.ticketID {
            guard owner == identity, !busy, !transferring else { throw URLError(.cancelled) }
            busy = true; transferring = true
            defer { if owner == identity { busy = false; transferring = false } }
            _ = try await model.store.music.control(ticketID: ticket, action: "stop")
            guard token == generation, owner == model.store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
            pendingSpeakerStop = nil
            snapshot.activeOutput = .init()
            dirty = true
        }
        guard token == generation, owner == model.store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
        guard !isRemoteOutput else {
            message = NativeAudioOutputError.returnRequired.localizedDescription
            throw NativeAudioOutputError.returnRequired
        }
        adopt(model)
    }

    func adopt(_ model: PlaybackModel, preservingTVRestoreToggle: Bool = false,
               preparedTracks: [LuminaCore.MusicTrack]? = nil) {
        guard playback !== model else { return }
        guard !isRemoteOutput else {
            message = "Return playback here before starting local playback."
            return
        }
        #if os(iOS)
        casting.detach()
        #endif
        generation += 1
        transportReady = false
        if !preservingTVRestoreToggle { clearTVRestoreToggle() }
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel()
        transport?.onChange = nil; transport?.onTrack = nil; transport?.onUnsupportedAudio = nil
        playback?.audioOwner = nil
        playback?.close()
        transport?.stop(); transport = nil
        playback = model
        self.preparedTracks = preparedTracks
        compatibleAudioIDs.removeAll()
        let ownerChanged = identity != model.store.music.sessionIdentity
        store = model.store
        identity = model.store.music.sessionIdentity
        if ownerChanged { restorePreferences(owner: identity) }
        model.audioOwner = self
        startTask = Task { await model.start() }
    }

    func play(items: [MediaItem], startingAt item: MediaItem, store: LuminaStore) async throws -> PlaybackModel {
        let owner = store.music.sessionIdentity
        let tracks = try await store.music.resolve(items: items.filter(\.isAudio))
        guard owner == store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        let selectedID = item.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard let index = tracks.firstIndex(where: { $0.nativeID == selectedID }) else {
            throw URLError(.resourceUnavailable)
        }
        return try await play(tracks: tracks, index: index, store: store)
    }

    func play(tracks: [LuminaCore.MusicTrack], index: Int = 0, store: LuminaStore, shuffle: Bool = false) async throws -> PlaybackModel {
        guard !busy, !transferring else { throw URLError(.cancelled) }
        guard !isRemoteOutput else {
            message = NativeAudioOutputError.returnRequired.localizedDescription
            throw NativeAudioOutputError.returnRequired
        }
        guard tracks.indices.contains(index), let owner = store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        cancelRestoreTask()
        let token = generation
        busy = true; transferring = true
        defer {
            if token == generation, owner == store.music.sessionIdentity { busy = false; transferring = false }
        }
        let selected = tracks[index]
        let orderedTracks = shuffle ? [selected] + tracks.enumerated().filter { $0.offset != index }.map(\.element).shuffled() : tracks
        let selectedIndex = shuffle ? 0 : index
        // A failed remote Stop must not start a second output.
        if let ticket = pendingSpeakerStop ?? (isSonos ? snapshot.activeOutput.ticketID : nil) {
            guard owner == identity else { throw LuminaError.notSignedIn }
            _ = try await store.music.control(ticketID: ticket, action: "stop")
            guard token == generation, owner == store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
            pendingSpeakerStop = nil
        }
        let request = try await store.preparePlayback(for: selected.mediaItem)
        guard token == generation, owner == store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
        let revision = store.isOffline ? snapshot.revision : (try? await store.music.loadSession())?.revision ?? snapshot.revision
        guard token == generation, owner == store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
        guard !isRemoteOutput else {
            message = NativeAudioOutputError.returnRequired.localizedDescription
            throw NativeAudioOutputError.returnRequired
        }
        let ownerChanged = identity != owner
        self.store = store; identity = owner
        if ownerChanged { restorePreferences(owner: owner) }
        snapshot = MusicListeningSnapshot(revision: revision, queue: orderedTracks, currentIndex: selectedIndex,
            shuffle: shuffle, entries: orderedTracks.map { MusicQueueEntry(kind: "track", itemID: $0.id) }, entryIndex: selectedIndex)
        self.tracks = orderedTracks
        let model = PlaybackModel(request: request, store: store, queue: orderedTracks.map(\.mediaItem), queueIndex: selectedIndex)
        busy = false; transferring = false
        adopt(model, preparedTracks: orderedTracks)
        changed()
        return model
    }

    func start(_ model: PlaybackModel) async {
        guard playback === model, let store else { return }
        let owner = identity, token = generation
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)
            let resolved: [LuminaCore.MusicTrack]
            if let preparedTracks { resolved = preparedTracks; self.preparedTracks = nil }
            else if store.isOffline {
                resolved = model.queue.filter { store.offline.localURL(for: $0.id) != nil }
                    .map { store.music.cachedTrack(for: $0.id) ?? MusicTrack(downloadedItem: $0) }
            } else { resolved = try await store.music.resolve(items: model.queue) }
            guard token == generation, owner == store.music.sessionIdentity, playback === model else { return }
            guard !resolved.isEmpty, resolved.indices.contains(model.queueIndex) else { throw URLError(.resourceUnavailable) }
            tracks = resolved
            if snapshot.queue.map(\.id) != resolved.map(\.id) {
                snapshot = MusicListeningSnapshot(revision: store.music.cachedSession?.revision ?? snapshot.revision,
                    queue: resolved, currentIndex: model.queueIndex, positionSeconds: model.elapsedSeconds,
                    entries: resolved.map { MusicQueueEntry(kind: "track", itemID: $0.id) }, entryIndex: model.queueIndex)
                dirty = !model.initiallyPaused
            }
            model.configureAudioQueue(resolved.map(\.mediaItem), index: model.queueIndex, repeatMode: snapshot.repeatMode)
            let transport = NativeMusicTransport(tracks: resolved, index: model.queueIndex, position: model.elapsedSeconds) { [weak self, weak store] index in
                guard let self, let store, self.identity == owner, self.tracks.indices.contains(index) else { throw LuminaError.notSignedIn }
                return try await store.preparePlayback(for: self.tracks[index].mediaItem,
                    forceTranscode: self.compatibleAudioIDs.contains(self.tracks[index].mediaItem.id))
            }
            self.transport = transport; model.audioTransport = transport
            waitingForEntry = nil; waitingNextIndex = nil
            transport.allowsAdvance = { [weak self] from, to in
                self?.pendingBoundary(from: from, to: to) == nil
            }
            transport.onBlockedAdvance = { [weak self] from, to in
                guard let self, let entry = pendingBoundary(from: from, to: to) else { return }
                waitingForEntry = entry; waitingNextIndex = to
                snapshot.entryIndex = entry
                message = "Waiting for requested album. Refresh requested albums or choose Skip, then Play."
                changed()
            }
            transport.onTrack = { [weak self, weak model] request, index in
                guard let self, let model, self.playback === model else { return }
                model.receiveAudioTrack(request, index: index)
                self.snapshot.currentIndex = index
                self.snapshot.entryIndex = self.logicalIndex(for: index)
                self.loadLyrics()
            }
            var receivedState = false
            transport.onChange = { [weak self, weak model] state in
                guard let self, let model, self.playback === model, !self.isSonos, !self.isRemoteOutput else { return }
                model.receiveAudioState(state)
                self.snapshot.currentIndex = state.index
                self.snapshot.positionSeconds = state.position
                if !receivedState && model.initiallyPaused && !state.playing {
                    receivedState = true; self.persist(); return
                }
                receivedState = true
                if Date().timeIntervalSince(self.lastCheckpoint) >= 5 || !state.playing {
                    self.lastCheckpoint = Date()
                    self.changed()
                }
                self.enforceSleepDeadline()
            }
            transport.onMode = { [weak self] mode in self?.playbackMode = mode }
            transport.onUnsupportedAudio = { [weak self, weak model, weak transport] in
                guard let self, let model, let transport, self.playback === model,
                      !self.compatibleAudioIDs.contains(model.request.item.id) else { return }
                let itemID = model.request.item.id, index = model.queueIndex, position = model.elapsedSeconds
                self.compatibleAudioIDs.insert(itemID)
                Task { [weak self, weak model, weak transport] in
                    guard let self, let model, let transport, self.playback === model,
                          self.transport === transport, model.request.item.id == itemID,
                          model.queueIndex == index else { return }
                    await transport.select(index, position: position)
                }
            }
            configureTransport()
            if isSonos {
                await transport.start(playing: false)
                startStatusPolling()
            } else { await transport.start(playing: !model.initiallyPaused) }
            guard token == generation, owner == store.music.sessionIdentity, playback === model else { return }
            transportReady = true
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("transport-ready")
            #endif
            if model.initiallyPaused, pendingTVRestoreToggle {
                pendingTVRestoreToggle = false
                if conflict == nil {
                    #if LUMINA_UI_TESTING
                    recordTVRestoreDiagnostic("pending-consumed")
                    #endif
                    await toggle()
                }
            }
        } catch {
            guard token == generation, owner == store.music.sessionIdentity else { return }
            if model.initiallyPaused { clearTVRestoreToggle() }
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("transport-failed")
            #endif
            model.errorMessage = error.localizedDescription
        }
    }

    func restore(store: LuminaStore) async {
        await restore(store: store, restoreTaskID: nil)
    }

    /// The tvOS root's `.task` is a presentation lifetime, not a retained
    /// playback lifetime. Keep this operation owned here so a root redraw cannot
    /// cancel a valid paused shared-queue restoration.
    func restoreFromTVRoot(store: LuminaStore) {
        let owner = store.music.sessionIdentity
        guard identity != owner || self.store == nil else { return }
        cancelRestoreTask()
        let taskID = restoreTaskGeneration
        restoreTask = Task { [weak self, weak store] in
            guard let self, let store else { return }
            await self.restore(store: store, restoreTaskID: taskID)
        }
    }

    private func restore(store: LuminaStore, restoreTaskID: Int?) async {
        let owner = store.music.sessionIdentity
        guard isCurrentRestoreTask(restoreTaskID), identity != owner || self.store == nil else { return }
        generation += 1
        let token = generation
        #if os(iOS)
        casting.detach()
        #endif
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel(); syncTask?.cancel(); syncTask = nil
        transport?.onChange = nil; transport?.onTrack = nil; transport?.onUnsupportedAudio = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil; transport?.stop(); transport = nil
        transportReady = false
        clearTVRestoreToggle()
        self.store = store; identity = owner
        tracks = []; lyrics = nil; conflict = nil; message = nil
        compatibleAudioIDs.removeAll()
        speakers = []; outputStatus = nil; pendingSpeakerStop = nil; busy = false; transferring = false
        volumeTask?.cancel(); volumeTask = nil; remoteVolumeDraft = nil
        snapshot = MusicListeningSnapshot()
        sleepTimer?.cancel(); sleepTimer = nil; sleepUntil = nil
        restorePreferences(owner: owner)
        guard let owner else { return }
        restoringPausedQueue = true
        #if LUMINA_UI_TESTING
        recordTVRestoreDiagnostic("restore-start")
        #endif
        defer {
            if isCurrentRestoreTask(restoreTaskID) {
                restoringPausedQueue = false
                if !transportReady { pendingTVRestoreToggle = false }
                if restoreTaskID != nil {
                    restoreTask = nil
                    restoreStartTask = nil
                }
                #if LUMINA_UI_TESTING
                recordTVRestoreDiagnostic("restore-finished")
                #endif
            }
        }
        dirty = LuminaPreferences.defaults.bool(forKey: "lumina.music.dirty.\(owner)")
        let cached = store.music.cachedSession
        if let cached { snapshot = cached }
        guard !store.isOffline else {
            snapshot.activeOutput = .init()
            restoreSleepDeadline(owner: owner)
            return
        }
        do {
            let server = try await store.music.loadSession()
            guard token == generation, owner == store.music.sessionIdentity,
                  isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
            if dirty, let cached, cached.revision != server.revision {
                conflict = server
                pendingTVRestoreToggle = false
                message = "Your saved queue differs from the server. Choose which queue to keep."
            } else if !dirty { snapshot = server }
        } catch {
            guard token == generation, owner == store.music.sessionIdentity,
                  isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
            message = "Queue restore is unavailable. Your saved queue is still available."
        }
        guard owner == store.music.sessionIdentity, isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
        restoreSleepDeadline(owner: owner)
        let restoredStartTask = await install(snapshot, store: store, preservingTVRestoreToggle: true,
                                              restoreTaskID: restoreTaskID)
        guard isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
        if let restoredStartTask { await restoredStartTask.value }
        guard isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
        if dirty, conflict == nil { await syncNow(restoreTaskID: restoreTaskID) }
    }

    @discardableResult
    private func install(_ value: MusicListeningSnapshot, store: LuminaStore,
                         preservingTVRestoreToggle: Bool = false,
                         restoreTaskID: Int? = nil) async -> Task<Void, Never>? {
        guard isCurrentRestoreTask(restoreTaskID), !Task.isCancelled, !isRemoteOutput else {
            if isRemoteOutput { message = "Return playback here before changing the shared queue." }
            return nil
        }
        generation += 1
        let token = generation, owner = identity
        transportReady = false
        #if LUMINA_UI_TESTING
        recordTVRestoreDiagnostic("install-start")
        #endif
        if !preservingTVRestoreToggle { clearTVRestoreToggle() }
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel()
        transport?.onChange = nil; transport?.onTrack = nil; transport?.onUnsupportedAudio = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil
        transport?.stop(); transport = nil
        snapshot = value; tracks = value.queue
        store.music.persistCheckpoint(snapshot: value, owner: identity)
        guard let index = value.currentIndex, value.queue.indices.contains(index) else {
            clearTVRestoreToggle()
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("install-no-index")
            #endif
            playback?.audioOwner = nil; playback?.close(); playback = nil
            transport?.stop(); transport = nil
            if isSonos { startStatusPolling() }
            return nil
        }
        do {
            let owner = identity
            let track = value.queue[index]
            let request = try await store.preparePlayback(for: track.mediaItem)
            guard token == generation, owner == identity, owner == store.music.sessionIdentity,
                  isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else {
                #if LUMINA_UI_TESTING
                recordTVRestoreDiagnostic("install-stale-\(uiTestingTVRestoreCancellationMarker())")
                #endif
                return nil
            }
            guard !isRemoteOutput else {
                message = "Return playback here before changing the shared queue."
                return nil
            }
            let resumed = PlaybackRequest(id: request.id, item: request.item, url: request.url,
                startPositionSeconds: value.positionSeconds, mediaSourceID: request.mediaSourceID,
                isTranscoding: request.isTranscoding, methodLabel: request.methodLabel)
            let model = PlaybackModel(request: resumed, store: store, queue: value.queue.map(\.mediaItem), queueIndex: index, initiallyPaused: true)
            adopt(model, preservingTVRestoreToggle: preservingTVRestoreToggle, preparedTracks: value.queue)
            if restoreTaskID != nil { restoreStartTask = startTask }
            return startTask
        } catch {
            guard token == generation, owner == store.music.sessionIdentity,
                  isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return nil }
            clearTVRestoreToggle()
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("install-failed-\(uiTestingTVRestoreFailureKind(error))-\(uiTestingTVRestoreCancellationMarker())")
            #endif
            message = "Saved playback is paused. The track is unavailable until the server or download is reachable."
            if isSonos { startStatusPolling() }
            return nil
        }
    }

    func refreshSharedQueue() async {
        guard !refreshingShared, !transferring, !restoringPausedQueue, let store, !store.isOffline,
              let owner = identity, owner == store.music.sessionIdentity, !isRemoteOutput else { return }
        refreshingShared = true
        defer { refreshingShared = false }
        // Never interrupt an active output to publish or adopt a foreground refresh.
        if dirty, playback?.isPlaying != true, !isSonos, conflict == nil { await syncNow() }
        guard owner == store.music.sessionIdentity, !restoringPausedQueue, !isRemoteOutput else { return }
        let token = generation
        do {
            let server = try await store.music.loadSession()
            guard token == generation, owner == store.music.sessionIdentity, !restoringPausedQueue, !isRemoteOutput else { return }
            let changedRemotely = server.revision != snapshot.revision || server.queue.map(\.id) != snapshot.queue.map(\.id)
                || server.entries != snapshot.entries || server.activeOutput.kind != snapshot.activeOutput.kind
                || server.activeOutput.ticketID != snapshot.activeOutput.ticketID
            if changedRemotely {
                if dirty || playback?.isPlaying == true || isSonos || pendingSpeakerStop != nil {
                    conflict = server
                    message = "Your saved queue differs from the server. Choose which queue to keep."
                } else {
                    dirty = false
                    await install(server, store: store)
                    guard owner == store.music.sessionIdentity else { return }
                    message = "Shared queue refreshed. Playback is paused."
                }
            } else if isSonos { await pollStatus() }
        } catch {
            guard token == generation, owner == store.music.sessionIdentity else { return }
            message = "Shared queue refresh is unavailable. Your saved queue is still available."
        }
    }

    func playSavedQueue(index requestedIndex: Int? = nil) async throws -> PlaybackModel? {
        guard !busy, !transferring else { throw URLError(.cancelled) }
        guard let store, let owner = identity, owner == store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        guard conflict == nil else { message = "Choose Use server or Keep this device before playing the shared queue."; return nil }
        guard !isRemoteOutput else {
            message = "Return playback here before starting local playback."
            return playback
        }
        if isSonos {
            await pollStatus()
            guard owner == store.music.sessionIdentity else { throw LuminaError.notSignedIn }
            message = "Use the speaker controls or return playback to this device."
            return playback
        }
        guard pendingSpeakerStop == nil else { message = "Stop the previous speaker before starting local playback."; return nil }
        guard !snapshot.queue.isEmpty else {
            message = snapshot.entries.isEmpty ? "Your shared queue is empty." : "Waiting for requested album. Refresh requested albums when an import is ready."
            return nil
        }
        if requestedIndex == nil, snapshot.unavailablePolicy == "wait", let entry = snapshot.entryIndex,
           snapshot.entries.indices.contains(entry), let state = snapshot.entries[entry].state, state != "playable" {
            message = "Waiting for requested album. Refresh requested albums or choose Skip, then Play."
            return nil
        }
        let index = requestedIndex ?? snapshot.currentIndex ?? 0
        guard snapshot.queue.indices.contains(index) else { return nil }
        let value = snapshot, token = generation
        let request = try await store.preparePlayback(for: value.queue[index].mediaItem)
        guard token == generation, owner == store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        guard !isRemoteOutput else {
            message = "Return playback here before starting local playback."
            return playback
        }
        let position = requestedIndex == nil && value.currentIndex != nil ? value.positionSeconds : 0
        let resumed = PlaybackRequest(id: request.id, item: request.item, url: request.url,
            startPositionSeconds: position, mediaSourceID: request.mediaSourceID,
            isTranscoding: request.isTranscoding, methodLabel: request.methodLabel)
        tracks = value.queue
        snapshot.currentIndex = index; snapshot.positionSeconds = position
        snapshot.entryIndex = logicalIndex(for: index)
        let model = PlaybackModel(request: resumed, store: store, queue: value.queue.map(\.mediaItem), queueIndex: index)
        adopt(model, preparedTracks: value.queue)
        changed()
        return model
    }

    func syncNow(restoreTaskID: Int? = nil) async {
        if syncing {
            syncAgain = true
            syncAgainRestoreTaskID = restoreTaskID
            return
        }
        guard !transferring, !isSonos, conflict == nil, dirty, let store, !store.isOffline, let owner = identity,
              isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
        syncing = true
        defer {
            syncing = false
            if syncAgain {
                let followUpRestoreTaskID = syncAgainRestoreTaskID
                syncAgain = false
                syncAgainRestoreTaskID = nil
                Task { await syncNow(restoreTaskID: followUpRestoreTaskID) }
            }
        }
        let sent = snapshot
        let token = generation
        do {
            let saved = try await store.music.saveSession(itemIDs: sent.queue.map(\.id), currentIndex: sent.currentIndex,
                positionSeconds: sent.positionSeconds, repeatMode: sent.repeatMode, shuffle: sent.shuffle,
                baseRevision: sent.revision, entries: sent.entries, entryIndex: sent.entryIndex, unavailablePolicy: sent.unavailablePolicy)
            guard owner == store.music.sessionIdentity, isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
            if token != generation { snapshot.revision = max(snapshot.revision, saved.revision); persist(); return }
            // Preserve a newer local position/order while advancing its optimistic base.
            let unchanged = snapshot.queue.map(\.id) == sent.queue.map(\.id) && snapshot.currentIndex == sent.currentIndex
                && snapshot.positionSeconds == sent.positionSeconds && snapshot.repeatMode == sent.repeatMode
                && snapshot.unavailablePolicy == sent.unavailablePolicy
            snapshot.revision = saved.revision
            if unchanged {
                dirty = false
                if saved.queue.map(\.id) != sent.queue.map(\.id) {
                    if playback?.isPlaying == true || isRemoteOutput {
                        conflict = saved
                        message = "The shared queue changed while playing. Choose which queue to keep."
                    } else { await install(saved, store: store, restoreTaskID: restoreTaskID) }
                } else { snapshot.entries = saved.entries; snapshot.entryIndex = saved.entryIndex }
            }
            persist()
        } catch MusicAPIError.conflict(let server) {
            guard owner == store.music.sessionIdentity, isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
            conflict = server
            message = "Your saved queue differs from the server. Choose which queue to keep."
        } catch {
            guard owner == store.music.sessionIdentity, isCurrentRestoreTask(restoreTaskID), !Task.isCancelled else { return }
            if case MusicAPIError.http(let status) = error, status == 401 || status == 403 { transport?.stop() }
            message = "Queue sync is unavailable. Changes remain on this device."
        }
    }

    func useServerSession() async {
        guard !transferring else { return }
        guard !isRemoteOutput else {
            message = "Return playback here before changing the shared queue."
            return
        }
        guard let server = conflict, let store else { return }
        conflict = nil; dirty = false
        await install(server, store: store)
        persist()
    }
    func keepDeviceSession() async {
        guard !transferring else { return }
        guard !isRemoteOutput else {
            message = "Return playback here before changing the shared queue."
            return
        }
        guard let server = conflict else { return }
        snapshot.revision = server.revision; conflict = nil; dirty = true
        await syncNow()
    }
    private func changed() {
        dirty = true; persist()
        guard store?.isOffline != true, syncTask == nil else { return }
        syncTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled, let self else { return }
            syncTask = nil
            await syncNow()
        }
    }
    private func persist() {
        store?.music.persistCheckpoint(snapshot: snapshot, owner: identity)
        if let identity { LuminaPreferences.defaults.set(dirty, forKey: "lumina.music.dirty.\(identity)") }
    }

    func toggle() async {
        guard !transferring else { return }
        #if os(iOS)
        if isRemoteOutput { casting.togglePlayback(); return }
        #endif
        guard pendingSpeakerStop == nil else { message = "Stop the previous speaker before starting local playback."; return }
        if isSonos { await remote(action: playback?.isPlaying == true ? "pause" : "play"); return }
        if waitingForEntry != nil {
            guard snapshot.unavailablePolicy == "skip" else {
                message = "Waiting for requested album. Refresh requested albums or choose Skip, then Play."
                return
            }
            self.waitingForEntry = nil
            if let next = waitingNextIndex { await select(next) }
            else { message = "There are no more playable tracks after this requested album." }
            waitingNextIndex = nil
            return
        }
        if playback?.isPlaying == true { pause() }
        else { await transport?.resume() }
    }

    /// Returns true only when a valid paused queue is still restoring. The TV
    /// root can then retain one Remote Play/Pause intent without stealing a
    /// music detail's command to begin a fresh album.
    func toggleTVPausedRestoreIfNeeded() async -> Bool {
        guard restoringPausedQueue,
              conflict == nil,
              let index = snapshot.currentIndex,
              snapshot.queue.indices.contains(index),
              playback == nil || playback?.initiallyPaused == true else {
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("remote-not-restoring")
            #endif
            return false
        }
        if transportReady {
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("remote-ready")
            #endif
            await toggle()
        } else {
            pendingTVRestoreToggle.toggle()
            #if LUMINA_UI_TESTING
            recordTVRestoreDiagnostic("remote-pending")
            #endif
        }
        return true
    }

    #if LUMINA_UI_TESTING
    func recordTVRestoreRemoteOutcome(_ outcome: String) {
        recordTVRestoreDiagnostic(outcome)
    }
    #endif
    func pause() {
        guard !transferring else { return }
        #if os(iOS)
        if isRemoteOutput { casting.pause(); return }
        #endif
        if isSonos { Task { await remote(action: "pause") } }
        else { transport?.pause(); changed() }
    }
    func seek(_ seconds: Double) async {
        guard !transferring else { return }
        #if os(iOS)
        if isRemoteOutput { casting.seek(to: seconds); return }
        #endif
        if isSonos { await remote(action: "seek", value: Int(max(0, seconds))); return }
        await transport?.seek(seconds); changed()
    }
    func select(_ index: Int) async {
        guard !transferring else { return }
        guard tracks.indices.contains(index) else { return }
        if isSonos || isRemoteOutput {
            message = "Return playback here before choosing another queued track."
            return
        }
        await transport?.select(index)
        changed()
    }
    func step(_ offset: Int) async {
        guard !transferring else { return }
        #if os(iOS)
        if isRemoteOutput { await casting.step(by: offset); return }
        #endif
        if isSonos { await remote(action: offset < 0 ? "previous" : "next") }
        else { await select((playback?.queueIndex ?? 0) + offset) }
    }
    func cycleRepeat() {
        guard !transferring else { return }
        guard !isSonos, !isRemoteOutput else { return }
        snapshot.repeatMode = snapshot.repeatMode == "off" ? "all" : snapshot.repeatMode == "all" ? "one" : "off"
        playback?.configureAudioQueue(tracks.map(\.mediaItem), index: playback?.queueIndex ?? 0, repeatMode: snapshot.repeatMode)
        configureTransport(); changed()
    }
    func move(from: Int, to: Int) {
        guard !transferring else { return }
        guard !isSonos, !isRemoteOutput, tracks.indices.contains(from), tracks.indices.contains(to),
              from > (playback?.queueIndex ?? -1), to > (playback?.queueIndex ?? -1) else { return }
        guard !snapshot.entries.contains(where: { $0.kind != "track" }) else {
            message = "Reorder after requested albums finish importing."; return
        }
        let item = tracks.remove(at: from); tracks.insert(item, at: to)
        playback?.reflectQueueMove(from: from, to: to)
        queueEdited()
    }
    func remove(at index: Int) {
        guard !transferring else { return }
        guard !isSonos, !isRemoteOutput, tracks.indices.contains(index), index > (playback?.queueIndex ?? -1) else { return }
        guard !snapshot.entries.contains(where: { $0.kind != "track" }) else { message = "Refresh requested albums before removing entries."; return }
        tracks.remove(at: index); playback?.reflectQueueRemoval(at: index); queueEdited()
    }
    private func queueEdited() {
        snapshot.queue = tracks
        snapshot.entries = tracks.map { MusicQueueEntry(kind: "track", itemID: $0.id) }
        snapshot.entryIndex = playback?.queueIndex
        playback?.configureAudioQueue(tracks.map(\.mediaItem), index: playback?.queueIndex ?? 0, repeatMode: snapshot.repeatMode)
        transport?.replaceUpcoming(tracks)
        changed()
    }
    private func logicalIndex(for flat: Int) -> Int? {
        var cursor = 0
        for (index, entry) in snapshot.entries.enumerated() {
            let count = entry.tracks?.count ?? (entry.kind == "track" ? 1 : 0)
            if flat >= cursor && flat < cursor + count { return index }
            cursor += count
        }
        return snapshot.entries.isEmpty ? nil : snapshot.entryIndex
    }
    private func pendingBoundary(from: Int, to: Int?) -> Int? {
        guard snapshot.unavailablePolicy == "wait", let first = logicalIndex(for: from) else { return nil }
        let last = to.flatMap { logicalIndex(for: $0) }
        if to == from { return nil }
        let candidates: [Int]
        if let last, last > first { candidates = Array((first + 1)..<last) }
        else if let last { candidates = Array((first + 1)..<snapshot.entries.count) + Array(0..<last) }
        else { candidates = Array((first + 1)..<snapshot.entries.count) }
        return candidates.first { snapshot.entries[$0].state.map { $0 != "playable" } ?? false }
    }
    func refreshRequestedAlbums() async {
        guard !isRemoteOutput else {
            message = "Return playback here before changing the queued albums."
            return
        }
        await refreshSharedQueue()
    }
    func setUnavailablePolicy(_ policy: String) {
        guard !transferring else { return }
        guard !isRemoteOutput else {
            message = "Return playback here before changing the queued albums."
            return
        }
        snapshot.unavailablePolicy = policy == "wait" ? "wait" : "skip"
        transport?.replaceUpcoming(tracks)
        changed()
    }

    func loadLyrics(retry: Bool = false) {
        lyricsTask?.cancel()
        guard let store, let playback else { return }
        let item = playback.request.item, owner = identity
        lyrics = store.music.cachedLyrics(for: item.id)
        lyricsError = nil; lyricsLoading = !store.isOffline
        guard !store.isOffline else { return }
        lyricsTask = Task { [weak self] in
            guard let self else { return }
            defer { if identity == owner, self.playback?.request.item.id == item.id { lyricsLoading = false } }
            do {
                for poll in 0..<13 {
                    guard !store.isOffline else { return }
                    let value = try await store.music.lyrics(for: item, retry: retry && poll == 0)
                    guard !Task.isCancelled, owner == store.music.sessionIdentity, self.playback?.request.item.id == item.id else { return }
                    lyrics = value
                    retryAllowedAt = value.retryAfterSeconds.map { Date().addingTimeInterval(Double(max(0, $0))) }
                    if !value.refreshing { return }
                    try await Task.sleep(for: .seconds(5))
                }
            } catch {
                guard !Task.isCancelled, owner == store.music.sessionIdentity, self.playback?.request.item.id == item.id else { return }
                lyricsError = "Lyrics are unavailable. Try again when the server is reachable."
            }
        }
    }

    private func restorePreferences(owner: String?) {
        restoringPreferences = true
        defer { restoringPreferences = false }
        let values = owner.flatMap { LuminaPreferences.defaults.dictionary(forKey: "lumina.music.preferences.\($0)") }
        normalization = values?["normalization"] as? Bool ?? true
        crossfadeSeconds = values?["crossfadeSeconds"] as? Double ?? 0
        #if os(iOS)
        volume = Self.defaultLocalVolume
        #else
        volume = Float(values?["volume"] as? Double ?? Double(Self.defaultLocalVolume))
        #endif
        compatibility = values?["compatibility"] as? Bool ?? false
    }
    private func persistPreferences() {
        guard !restoringPreferences, let identity, identity == store?.music.sessionIdentity else { return }
        var values: [String: Any] = ["normalization": normalization, "crossfadeSeconds": crossfadeSeconds,
                                     "compatibility": compatibility]
        #if os(tvOS)
        values["volume"] = Double(volume)
        #endif
        LuminaPreferences.defaults.set(values, forKey: "lumina.music.preferences.\(identity)")
    }

    private func configureTransport() {
        transport?.configure(repeatMode: snapshot.repeatMode, crossfade: crossfadeSeconds, normalize: normalization, volume: volume, shuffle: snapshot.shuffle)
    }
    func setSleep(minutes: Int?) {
        sleepTimer?.cancel(); sleepTimer = nil
        sleepUntil = minutes.map { Date().addingTimeInterval(Double($0) * 60) }
        if let identity {
            let key = "lumina.music.sleep.\(identity)"
            if let sleepUntil { LuminaPreferences.defaults.set(sleepUntil.timeIntervalSince1970, forKey: key) }
            else { LuminaPreferences.defaults.removeObject(forKey: key) }
        }
        guard let deadline = sleepUntil else { return }
        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        timer.schedule(deadline: .now() + max(0, deadline.timeIntervalSinceNow), leeway: .milliseconds(100))
        timer.setEventHandler { [weak self] in Task { @MainActor in self?.enforceSleepDeadline() } }
        sleepTimer = timer; timer.resume()
    }
    private func restoreSleepDeadline(owner: String) {
        let value = LuminaPreferences.defaults.double(forKey: "lumina.music.sleep.\(owner)")
        if value > Date().timeIntervalSince1970 {
            sleepUntil = Date(timeIntervalSince1970: value)
            let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
            timer.schedule(deadline: .now() + max(0, value - Date().timeIntervalSince1970))
            timer.setEventHandler { [weak self] in Task { @MainActor in self?.enforceSleepDeadline() } }
            sleepTimer?.cancel(); sleepTimer = timer; timer.resume()
        } else { sleepUntil = nil }
    }
    private func enforceSleepDeadline() {
        guard let sleepUntil, Date() >= sleepUntil else { return }
        transport?.pause()
        setSleep(minutes: nil)
        message = "Sleep timer paused playback."
    }
    func connectivityDidChange() {
        if store?.isOffline == true {
            lyricsTask?.cancel()
            statusTask?.cancel()
            syncTask?.cancel(); syncTask = nil
            lyricsLoading = false
        } else {
            Task { await refreshSharedQueue() }
        }
    }

    func resumeAfterActivation() {
        enforceSleepDeadline()
        if isSonos { Task { await pollStatus() } }
    }

    func flushCheckpoint() async {
        guard let store, identity == store.music.sessionIdentity else { return }
        let task = UIApplication.shared.beginBackgroundTask(withName: "Music checkpoint")
        defer { if task != .invalid { UIApplication.shared.endBackgroundTask(task) } }
        persist()
        await store.music.flushCheckpoint()
    }

    func discoverSpeakers() async {
        guard let store else { return }
        let owner = identity
        busy = true; defer { if owner == identity { busy = false } }
        do {
            let discovered = try await store.music.speakers()
            guard owner == store.music.sessionIdentity else { return }
            speakers = discovered
        } catch { if owner == store.music.sessionIdentity { message = error.localizedDescription } }
    }
    func cast(to speaker: MusicSpeaker) async {
        guard !busy, let store, let index = snapshot.currentIndex, tracks.indices.contains(index) else { return }
        let owner = identity, token = generation
        busy = true; transferring = true
        defer { if owner == identity, token == generation { busy = false; transferring = false } }
        do {
            // Acknowledge the server's actual transfer before stopping local audio.
            let result = try await store.music.cast(speakerID: speaker.id,
                itemIDs: Array(tracks.dropFirst(index).prefix(100)).map(\.id), baseRevision: snapshot.revision,
                currentIndex: index, positionSeconds: playback?.elapsedSeconds ?? snapshot.positionSeconds, compatibility: compatibility)
            guard owner == store.music.sessionIdentity, token == generation else {
                if owner == store.music.sessionIdentity {
                    message = "The speaker accepted a transfer after playback changed. Refresh the shared queue to check its output."
                }
                return
            }
            transport?.pause()
            snapshot = result.session; tracks = result.session.queue
            dirty = false; persist()
            playback?.configureAudioQueue(tracks.map(\.mediaItem), index: result.currentIndex, repeatMode: snapshot.repeatMode)
            if result.excludedCount > 0 { message = "\(result.excludedCount) unsupported or unavailable tracks were excluded from the speaker." }
            startStatusPolling()
            await pollStatus()
        } catch MusicAPIError.conflict(let server) {
            guard owner == store.music.sessionIdentity, token == generation else { return }
            conflict = server; message = "The shared queue changed. Choose which queue to keep before transferring."
        } catch { if owner == store.music.sessionIdentity, token == generation { message = error.localizedDescription } }
    }
    func returnHere() async {
        guard !busy, let store, let ticket = snapshot.activeOutput.ticketID else { return }
        let owner = identity, token = generation
        let targetTransport = transport
        busy = true; transferring = true
        defer { if owner == identity, token == generation { busy = false; transferring = false } }
        do {
            // Read the actual remote track/position immediately before the handoff.
            guard await pollStatus() else { throw URLError(.cannotConnectToHost) }
            guard owner == store.music.sessionIdentity, token == generation else { return }
            let index = snapshot.currentIndex ?? 0
            guard tracks.indices.contains(index) else { return }
            targetTransport?.replaceUpcoming(tracks)
            await targetTransport?.select(index, position: snapshot.positionSeconds)
            guard owner == store.music.sessionIdentity, token == generation else { return }
            let confirmed = await targetTransport?.confirmPlaying() == true
            guard owner == store.music.sessionIdentity, token == generation else { return }
            guard confirmed else { targetTransport?.pause(); throw URLError(.cannotDecodeContentData) }
            do {
                let saved = try await store.music.saveSession(itemIDs: tracks.map(\.id), currentIndex: index,
                    positionSeconds: snapshot.positionSeconds, repeatMode: snapshot.repeatMode, shuffle: snapshot.shuffle,
                    baseRevision: snapshot.revision, acknowledgedOutputTicket: ticket, entries: snapshot.entries,
                    entryIndex: snapshot.entryIndex, unavailablePolicy: snapshot.unavailablePolicy)
                guard owner == store.music.sessionIdentity, token == generation else {
                    if owner == store.music.sessionIdentity {
                        message = "Output ownership changed during the return. Refresh the shared queue before continuing."
                    }
                    return
                }
                snapshot = saved; dirty = false; persist()
            } catch {
                if owner == store.music.sessionIdentity, token == generation { targetTransport?.pause() }
                throw error
            }
            do { _ = try await store.music.control(ticketID: ticket, action: "stop") }
            catch {
                guard owner == store.music.sessionIdentity, token == generation else { return }
                targetTransport?.pause(); pendingSpeakerStop = ticket
                throw error
            }
            guard owner == store.music.sessionIdentity, token == generation else { return }
            statusTask?.cancel(); outputStatus = nil
            if let state = targetTransport?.state { playback?.receiveAudioState(state) }
        } catch MusicAPIError.conflict(let server) {
            guard owner == store.music.sessionIdentity, token == generation else { return }
            conflict = server; message = "Output ownership changed. Resolve the shared queue before returning here."
        } catch { if owner == store.music.sessionIdentity, token == generation { message = error.localizedDescription } }
    }
    private func startStatusPolling() {
        statusTask?.cancel()
        statusTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.isSonos, self.store?.isOffline != true else { return }
                await pollStatus()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }
    @discardableResult private func pollStatus() async -> Bool {
        guard let ticket = snapshot.activeOutput.ticketID, let store else { return false }
        let owner = identity
        do {
            let value = try await store.music.status(ticketID: ticket)
            guard owner == store.music.sessionIdentity, ticket == snapshot.activeOutput.ticketID else { return false }
            outputStatus = value
            if let number = value.track?.trackNumber, let indices = snapshot.activeOutput.queueIndices,
               indices.indices.contains(number - 1), tracks.indices.contains(indices[number - 1]) {
                let index = indices[number - 1]
                if index != snapshot.currentIndex {
                    snapshot.currentIndex = index
                    playback?.configureAudioQueue(tracks.map(\.mediaItem), index: index, repeatMode: snapshot.repeatMode)
                    if let request = try? await store.preparePlayback(for: tracks[index].mediaItem) {
                        guard owner == store.music.sessionIdentity, ticket == snapshot.activeOutput.ticketID else { return false }
                        playback?.receiveAudioTrack(request, index: index)
                    }
                }
            }
            snapshot.positionSeconds = max(0, value.track?.positionSeconds ?? value.positionSeconds ?? snapshot.positionSeconds)
            let playing = value.playing ?? ((value.transportState ?? value.state ?? "").uppercased() == "PLAYING")
            playback?.receiveAudioState(.init(index: snapshot.currentIndex ?? 0, position: snapshot.positionSeconds, playing: playing))
            persist()
            return true
        } catch {
            guard owner == store.music.sessionIdentity else { return false }
            message = "The remote output is not responding. Retry before returning playback here."
            return false
        }
    }
    func retryStoppingSpeaker() async {
        guard let ticket = pendingSpeakerStop, let store else { return }
        let owner = identity
        do {
            _ = try await store.music.control(ticketID: ticket, action: "stop")
            guard owner == store.music.sessionIdentity else { return }
            pendingSpeakerStop = nil
            message = "Speaker stopped. Choose Play to resume here."
        } catch {
            if owner == store.music.sessionIdentity { message = "The speaker has not acknowledged Stop. Local playback remains paused." }
        }
    }
    private func remote(action: String, value: Int? = nil) async {
        guard let ticket = snapshot.activeOutput.ticketID, let store else { return }
        let owner = identity
        do {
            _ = try await store.music.control(ticketID: ticket, action: action, value: value)
            guard owner == store.music.sessionIdentity, ticket == snapshot.activeOutput.ticketID else { return }
            await pollStatus()
        } catch { if owner == store.music.sessionIdentity { message = error.localizedDescription } }
    }

    /// Snapshot only local queue state. The Cast controller owns receiver state and may not
    /// infer a track from a receiver URL.
    func musicCastState() -> (index: Int, track: LuminaCore.MusicTrack, position: Double, playing: Bool)? {
        guard let index = snapshot.currentIndex, tracks.indices.contains(index) else { return nil }
        let position = max(0, playback?.elapsedSeconds ?? snapshot.positionSeconds)
        return (index, tracks[index], position, playback?.isPlaying == true)
    }

    func prepareMusicCastTicket(for index: Int, position: Double) async throws -> MusicReceiverTicket {
        guard let store, identity == store.music.sessionIdentity else {
            throw LuminaError.notSignedIn
        }
        let projection = musicCastTracks(from: index)
        guard !projection.isEmpty else { throw LuminaError.notSignedIn }
        return try await store.prepareMusicReceiver(itemIDs: projection.map { $0.track.id }, currentIndex: 0,
                                                    positionSeconds: position)
    }

    func beginMusicCastHandoff() {
        transport?.pause()
        playback?.receiveAudioState(.init(index: snapshot.currentIndex ?? 0,
                                           position: snapshot.positionSeconds, playing: false))
    }

    func receiveMusicCastStatus(index: Int, position: Double, playing: Bool) {
        guard tracks.indices.contains(index) else { return }
        snapshot.currentIndex = index
        snapshot.entryIndex = logicalIndex(for: index)
        snapshot.positionSeconds = max(0, position)
        playback?.receiveAudioState(.init(index: index, position: snapshot.positionSeconds, playing: playing))
        persist()
    }

    func returnFromMusicCast(index: Int, position: Double, playing: Bool) async -> Bool {
        guard tracks.indices.contains(index), let transport else { return false }
        await transport.select(index, position: max(0, position), playing: false)
        if playing { await transport.resume() }
        let resumed: Bool
        if playing { resumed = await transport.confirmPlaying() }
        else { resumed = true }
        guard resumed else { transport.pause(); return false }
        receiveMusicCastStatus(index: index, position: max(0, position), playing: playing)
        return true
    }

    func musicCastTrack(at index: Int) -> LuminaCore.MusicTrack? {
        tracks.indices.contains(index) ? tracks[index] : nil
    }

    func musicCastTracks(from index: Int) -> [(index: Int, track: LuminaCore.MusicTrack)] {
        guard tracks.indices.contains(index) else { return [] }
        return Array(tracks[index...].prefix(100).enumerated()).map { offset, track in
            (index + offset, track)
        }
    }

    func musicCastQueueIsLimited(from index: Int, limit: Int) -> Bool {
        tracks.indices.contains(index) && tracks.distance(from: index, to: tracks.endIndex) > limit
    }
    func setOutputVolume(_ value: Float) async {
        volumeTask?.cancel()
        #if os(iOS)
        if isRemoteOutput { casting.setVolume(Double(value)); return }
        #endif
        guard isSonos else {
            remoteVolumeDraft = nil
            #if os(tvOS)
            volume = value
            #endif
            return
        }
        let target = min(1, max(0, value)), owner = identity, ticket = snapshot.activeOutput.ticketID
        remoteVolumeDraft = target
        volumeTask = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
            guard let self, !Task.isCancelled, owner == identity,
                  owner == store?.music.sessionIdentity, ticket == snapshot.activeOutput.ticketID else { return }
            await remote(action: "volume", value: Int(target * 100))
            if !Task.isCancelled, owner == identity { remoteVolumeDraft = nil }
        }
    }
    func stop() {
        // Session teardown hides local state without writing another member's queue.
        cancelRestoreTask()
        guard let store, identity == store.music.sessionIdentity else {
            #if os(iOS)
            casting.detach()
            #endif
            clearPlayback()
            identity = nil; speakers = []; outputStatus = nil; conflict = nil; pendingSpeakerStop = nil
            return
        }
        guard !busy else { return }
        if isRemoteOutput {
            #if os(iOS)
            casting.detach()
            #endif
            clearPlayback()
            return
        }
        let owner = identity
        if let ticket = pendingSpeakerStop ?? snapshot.activeOutput.ticketID {
            busy = true
            Task {
                defer { if owner == identity { busy = false } }
                do {
                    _ = try await store.music.control(ticketID: ticket, action: "stop")
                    guard owner == store.music.sessionIdentity else { return }
                    clearPlayback(); pendingSpeakerStop = nil
                    dirty = true; persist(); await syncNow()
                } catch {
                    guard owner == store.music.sessionIdentity else { return }
                    message = "The speaker has not acknowledged Stop. Its controls remain available."
                }
            }
        } else {
            clearPlayback()
            dirty = true; persist()
            Task { await syncNow() }
        }
    }

    private func clearPlayback() {
        #if os(iOS)
        casting.detach()
        #endif
        cancelRestoreTask()
        volumeTask?.cancel(); volumeTask = nil; remoteVolumeDraft = nil
        waitingForEntry = nil; waitingNextIndex = nil
        generation += 1
        transportReady = false
        clearTVRestoreToggle()
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel(); syncTask?.cancel(); syncTask = nil
        transport?.onChange = nil; transport?.onTrack = nil; transport?.onUnsupportedAudio = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil
        transport?.stop(); transport = nil
        preparedTracks = nil
        tracks = []; snapshot.queue = []; snapshot.currentIndex = nil; snapshot.positionSeconds = 0
        compatibleAudioIDs.removeAll()
        snapshot.entries = []; snapshot.entryIndex = nil; snapshot.activeOutput = .init()
        lyrics = nil; lyricsError = nil; lyricsLoading = false
        setSleep(minutes: nil)
    }

    private func clearTVRestoreToggle() {
        pendingTVRestoreToggle = false
        restoringPausedQueue = false
    }

    private func cancelRestoreTask() {
        restoreTaskGeneration += 1
        restoreTask?.cancel()
        restoreStartTask?.cancel()
        restoreTask = nil
        restoreStartTask = nil
        if syncAgainRestoreTaskID != nil { syncAgain = false }
        syncAgainRestoreTaskID = nil
        clearTVRestoreToggle()
    }

    private func isCurrentRestoreTask(_ taskID: Int?) -> Bool {
        taskID == nil || taskID == restoreTaskGeneration
    }
}
