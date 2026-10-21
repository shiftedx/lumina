import AVFoundation
import LuminaCore
import Observation
import UIKit

@MainActor @Observable
final class NativeAudioSession {
    private(set) var playback: PlaybackModel?
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
    var volume: Float = 0.8 {
        didSet {
            let clamped: Float = volume.isFinite ? min(1, max(0, volume)) : 0.8
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
    private var startTask: Task<Void, Never>?
    private var lyricsTask: Task<Void, Never>?
    private var statusTask: Task<Void, Never>?
    private var syncTask: Task<Void, Never>?
    private var sleepTimer: DispatchSourceTimer?
    private var transport: NativeMusicTransport?
    private var dirty = false
    private var syncing = false
    private var syncAgain = false
    private var generation = 0
    private var lastCheckpoint = Date.distantPast
    private var preparedTracks: [LuminaCore.MusicTrack]?
    private var transferring = false
    private var refreshingShared = false
    private var waitingForEntry: Int?
    private var waitingNextIndex: Int?

    var isSonos: Bool { snapshot.activeOutput.kind == "sonos" && snapshot.activeOutput.ticketID != nil }
    var repeatMode: String { snapshot.repeatMode }
    var pendingEntries: [MusicQueueEntry] { snapshot.entries.filter { $0.state != nil && $0.state != "playable" } }

    init() {
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
        guard let owner = model.store.music.sessionIdentity else { throw LuminaError.notSignedIn }
        let token = generation
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
        adopt(model)
    }

    func adopt(_ model: PlaybackModel) {
        guard playback !== model else { return }
        generation += 1
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel()
        transport?.onChange = nil; transport?.onTrack = nil
        playback?.audioOwner = nil
        playback?.close()
        transport?.stop(); transport = nil
        playback = model
        let ownerChanged = identity != model.store.music.sessionIdentity
        store = model.store
        identity = model.store.music.sessionIdentity
        if ownerChanged { restorePreferences(owner: identity) }
        model.audioOwner = self
        startTask = Task { await model.start() }
    }

    func play(tracks: [LuminaCore.MusicTrack], index: Int = 0, store: LuminaStore, shuffle: Bool = false) async throws -> PlaybackModel {
        guard !busy, !transferring else { throw URLError(.cancelled) }
        guard tracks.indices.contains(index), let owner = store.music.sessionIdentity else { throw LuminaError.notSignedIn }
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
        let revision = (try? await store.music.loadSession())?.revision ?? snapshot.revision
        guard token == generation, owner == store.music.sessionIdentity, !Task.isCancelled else { throw CancellationError() }
        let ownerChanged = identity != owner
        self.store = store; identity = owner
        if ownerChanged { restorePreferences(owner: owner) }
        snapshot = MusicListeningSnapshot(revision: revision, queue: orderedTracks, currentIndex: selectedIndex,
            shuffle: shuffle, entries: orderedTracks.map { MusicQueueEntry(kind: "track", itemID: $0.id) }, entryIndex: selectedIndex)
        self.tracks = orderedTracks
        preparedTracks = orderedTracks
        let model = PlaybackModel(request: request, store: store, queue: orderedTracks.map(\.mediaItem), queueIndex: selectedIndex)
        busy = false; transferring = false
        adopt(model)
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
            else { resolved = try await store.music.resolve(items: model.queue) }
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
                return try await store.preparePlayback(for: self.tracks[index].mediaItem)
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
                guard let self, let model, self.playback === model, !self.isSonos else { return }
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
            configureTransport()
            if isSonos {
                await transport.start(playing: false)
                startStatusPolling()
            } else { await transport.start(playing: !model.initiallyPaused) }
        } catch {
            guard token == generation, owner == store.music.sessionIdentity else { return }
            model.errorMessage = error.localizedDescription
        }
    }

    func restore(store: LuminaStore) async {
        let owner = store.music.sessionIdentity
        guard identity != owner || self.store == nil else { return }
        generation += 1
        let token = generation
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel(); syncTask?.cancel(); syncTask = nil
        transport?.onChange = nil; transport?.onTrack = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil; transport?.stop(); transport = nil
        self.store = store; identity = owner
        tracks = []; lyrics = nil; conflict = nil; message = nil
        speakers = []; outputStatus = nil; pendingSpeakerStop = nil; busy = false; transferring = false
        volumeTask?.cancel(); volumeTask = nil; remoteVolumeDraft = nil
        snapshot = MusicListeningSnapshot()
        sleepTimer?.cancel(); sleepTimer = nil; sleepUntil = nil
        restorePreferences(owner: owner)
        guard let owner else { return }
        dirty = UserDefaults.standard.bool(forKey: "lumina.music.dirty.\(owner)")
        let cached = store.music.cachedSession
        if let cached { snapshot = cached }
        do {
            let server = try await store.music.loadSession()
            guard token == generation, owner == store.music.sessionIdentity else { return }
            if dirty, let cached, cached.revision != server.revision {
                conflict = server
                message = "Your saved queue differs from the server. Choose which queue to keep."
            } else if !dirty { snapshot = server }
        } catch {
            guard token == generation, owner == store.music.sessionIdentity else { return }
            message = "Queue restore is unavailable. Your saved queue is still available."
        }
        guard owner == store.music.sessionIdentity else { return }
        restoreSleepDeadline(owner: owner)
        await install(snapshot, store: store)
        if dirty, conflict == nil { await syncNow() }
    }

    private func install(_ value: MusicListeningSnapshot, store: LuminaStore) async {
        generation += 1
        let token = generation, owner = identity
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel()
        transport?.onChange = nil; transport?.onTrack = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil
        transport?.stop(); transport = nil
        snapshot = value; tracks = value.queue
        store.music.persistCheckpoint(snapshot: value, owner: identity)
        guard let index = value.currentIndex, value.queue.indices.contains(index) else {
            playback?.audioOwner = nil; playback?.close(); playback = nil
            transport?.stop(); transport = nil
            if isSonos { startStatusPolling() }
            return
        }
        do {
            let owner = identity
            let track = value.queue[index]
            let request = try await store.preparePlayback(for: track.mediaItem)
            guard token == generation, owner == identity, owner == store.music.sessionIdentity else { return }
            let resumed = PlaybackRequest(id: request.id, item: request.item, url: request.url,
                startPositionSeconds: value.positionSeconds, mediaSourceID: request.mediaSourceID,
                isTranscoding: request.isTranscoding, methodLabel: request.methodLabel)
            preparedTracks = value.queue
            let model = PlaybackModel(request: resumed, store: store, queue: value.queue.map(\.mediaItem), queueIndex: index, initiallyPaused: true)
            adopt(model)
        } catch {
            guard token == generation, owner == store.music.sessionIdentity else { return }
            message = "Saved playback is paused. The track is unavailable until the server or download is reachable."
            if isSonos { startStatusPolling() }
        }
    }

    func refreshSharedQueue() async {
        guard !refreshingShared, !transferring, let store, let owner = identity,
              owner == store.music.sessionIdentity else { return }
        refreshingShared = true
        defer { refreshingShared = false }
        // Never interrupt an active output to publish or adopt a foreground refresh.
        if dirty, playback?.isPlaying != true, !isSonos, conflict == nil { await syncNow() }
        guard owner == store.music.sessionIdentity else { return }
        let token = generation
        do {
            let server = try await store.music.loadSession()
            guard token == generation, owner == store.music.sessionIdentity else { return }
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
        let position = requestedIndex == nil && value.currentIndex != nil ? value.positionSeconds : 0
        let resumed = PlaybackRequest(id: request.id, item: request.item, url: request.url,
            startPositionSeconds: position, mediaSourceID: request.mediaSourceID,
            isTranscoding: request.isTranscoding, methodLabel: request.methodLabel)
        tracks = value.queue; preparedTracks = value.queue
        snapshot.currentIndex = index; snapshot.positionSeconds = position
        snapshot.entryIndex = logicalIndex(for: index)
        let model = PlaybackModel(request: resumed, store: store, queue: value.queue.map(\.mediaItem), queueIndex: index)
        adopt(model)
        changed()
        return model
    }

    func syncNow() async {
        if syncing { syncAgain = true; return }
        guard !transferring, !isSonos, conflict == nil, dirty, let store, let owner = identity else { return }
        syncing = true
        defer {
            syncing = false
            if syncAgain { syncAgain = false; Task { await syncNow() } }
        }
        let sent = snapshot
        let token = generation
        do {
            let saved = try await store.music.saveSession(itemIDs: sent.queue.map(\.id), currentIndex: sent.currentIndex,
                positionSeconds: sent.positionSeconds, repeatMode: sent.repeatMode, shuffle: sent.shuffle,
                baseRevision: sent.revision, entries: sent.entries, entryIndex: sent.entryIndex, unavailablePolicy: sent.unavailablePolicy)
            guard owner == store.music.sessionIdentity else { return }
            if token != generation { snapshot.revision = max(snapshot.revision, saved.revision); persist(); return }
            // Preserve a newer local position/order while advancing its optimistic base.
            let unchanged = snapshot.queue.map(\.id) == sent.queue.map(\.id) && snapshot.currentIndex == sent.currentIndex
                && snapshot.positionSeconds == sent.positionSeconds && snapshot.repeatMode == sent.repeatMode
                && snapshot.unavailablePolicy == sent.unavailablePolicy
            snapshot.revision = saved.revision
            if unchanged {
                dirty = false
                if saved.queue.map(\.id) != sent.queue.map(\.id) {
                    if playback?.isPlaying == true {
                        conflict = saved
                        message = "The shared queue changed while playing. Choose which queue to keep."
                    } else { await install(saved, store: store) }
                } else { snapshot.entries = saved.entries; snapshot.entryIndex = saved.entryIndex }
            }
            persist()
        } catch MusicAPIError.conflict(let server) {
            guard owner == store.music.sessionIdentity else { return }
            conflict = server
            message = "Your saved queue differs from the server. Choose which queue to keep."
        } catch {
            guard owner == store.music.sessionIdentity else { return }
            if case MusicAPIError.http(let status) = error, status == 401 || status == 403 { transport?.stop() }
            message = "Queue sync is unavailable. Changes remain on this device."
        }
    }

    func useServerSession() async {
        guard !transferring else { return }
        guard let server = conflict, let store else { return }
        conflict = nil; dirty = false
        await install(server, store: store)
        persist()
    }
    func keepDeviceSession() async {
        guard !transferring else { return }
        guard let server = conflict else { return }
        snapshot.revision = server.revision; conflict = nil; dirty = true
        await syncNow()
    }
    private func changed() {
        dirty = true; persist()
        guard syncTask == nil else { return }
        syncTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled, let self else { return }
            syncTask = nil
            await syncNow()
        }
    }
    private func persist() {
        store?.music.persistCheckpoint(snapshot: snapshot, owner: identity)
        if let identity { UserDefaults.standard.set(dirty, forKey: "lumina.music.dirty.\(identity)") }
    }

    func toggle() async {
        guard !transferring else { return }
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
    func pause() {
        guard !transferring else { return }
        if isSonos { Task { await remote(action: "pause") } }
        else { transport?.pause(); changed() }
    }
    func seek(_ seconds: Double) async {
        guard !transferring else { return }
        if isSonos { await remote(action: "seek", value: Int(max(0, seconds))); return }
        await transport?.seek(seconds); changed()
    }
    func select(_ index: Int) async {
        guard !transferring else { return }
        guard tracks.indices.contains(index) else { return }
        if isSonos { message = "Return playback here before choosing another queued track."; return }
        await transport?.select(index)
        changed()
    }
    func step(_ offset: Int) async {
        guard !transferring else { return }
        if isSonos { await remote(action: offset < 0 ? "previous" : "next") }
        else { await select((playback?.queueIndex ?? 0) + offset) }
    }
    func cycleRepeat() {
        guard !transferring else { return }
        guard !isSonos else { return }
        snapshot.repeatMode = snapshot.repeatMode == "off" ? "all" : snapshot.repeatMode == "all" ? "one" : "off"
        playback?.configureAudioQueue(tracks.map(\.mediaItem), index: playback?.queueIndex ?? 0, repeatMode: snapshot.repeatMode)
        configureTransport(); changed()
    }
    func move(from: Int, to: Int) {
        guard !transferring else { return }
        guard !isSonos, tracks.indices.contains(from), tracks.indices.contains(to),
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
        guard !isSonos, tracks.indices.contains(index), index > (playback?.queueIndex ?? -1) else { return }
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
        await refreshSharedQueue()
    }
    func setUnavailablePolicy(_ policy: String) {
        guard !transferring else { return }
        snapshot.unavailablePolicy = policy == "wait" ? "wait" : "skip"
        transport?.replaceUpcoming(tracks)
        changed()
    }

    func loadLyrics(retry: Bool = false) {
        lyricsTask?.cancel()
        guard let store, let playback else { return }
        let item = playback.request.item, owner = identity
        lyrics = store.music.cachedLyrics(for: item.id)
        lyricsError = nil; lyricsLoading = true
        lyricsTask = Task { [weak self] in
            guard let self else { return }
            defer { if identity == owner, self.playback?.request.item.id == item.id { lyricsLoading = false } }
            do {
                for poll in 0..<13 {
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
        let values = owner.flatMap { UserDefaults.standard.dictionary(forKey: "lumina.music.preferences.\($0)") }
        normalization = values?["normalization"] as? Bool ?? true
        crossfadeSeconds = values?["crossfadeSeconds"] as? Double ?? 0
        volume = Float(values?["volume"] as? Double ?? 0.8)
        compatibility = values?["compatibility"] as? Bool ?? false
    }
    private func persistPreferences() {
        guard !restoringPreferences, let identity, identity == store?.music.sessionIdentity else { return }
        let values: [String: Any] = ["normalization": normalization, "crossfadeSeconds": crossfadeSeconds,
                                     "volume": Double(volume), "compatibility": compatibility]
        UserDefaults.standard.set(values, forKey: "lumina.music.preferences.\(identity)")
    }

    private func configureTransport() {
        transport?.configure(repeatMode: snapshot.repeatMode, crossfade: crossfadeSeconds, normalize: normalization, volume: volume, shuffle: snapshot.shuffle)
    }
    func setSleep(minutes: Int?) {
        sleepTimer?.cancel(); sleepTimer = nil
        sleepUntil = minutes.map { Date().addingTimeInterval(Double($0) * 60) }
        if let identity {
            let key = "lumina.music.sleep.\(identity)"
            if let sleepUntil { UserDefaults.standard.set(sleepUntil.timeIntervalSince1970, forKey: key) }
            else { UserDefaults.standard.removeObject(forKey: key) }
        }
        guard let deadline = sleepUntil else { return }
        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
        timer.schedule(deadline: .now() + max(0, deadline.timeIntervalSinceNow), leeway: .milliseconds(100))
        timer.setEventHandler { [weak self] in Task { @MainActor in self?.enforceSleepDeadline() } }
        sleepTimer = timer; timer.resume()
    }
    private func restoreSleepDeadline(owner: String) {
        let value = UserDefaults.standard.double(forKey: "lumina.music.sleep.\(owner)")
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
    func resumeAfterActivation() {
        enforceSleepDeadline()
        if isSonos { Task { await pollStatus() } }
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
            if result.excludedCount > 0 { message = "\(result.excludedCount) unsupported or unavailable tracks were excluded from Sonos." }
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
                guard let self, self.isSonos else { return }
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
            message = "Sonos is not responding. Retry before returning playback here."
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
    func setOutputVolume(_ value: Float) async {
        volumeTask?.cancel()
        guard isSonos else { remoteVolumeDraft = nil; volume = value; return }
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
        guard let store, identity == store.music.sessionIdentity else {
            clearPlayback()
            identity = nil; speakers = []; outputStatus = nil; conflict = nil; pendingSpeakerStop = nil
            return
        }
        guard !busy else { return }
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
        volumeTask?.cancel(); volumeTask = nil; remoteVolumeDraft = nil
        waitingForEntry = nil; waitingNextIndex = nil
        generation += 1
        startTask?.cancel(); lyricsTask?.cancel(); statusTask?.cancel(); syncTask?.cancel(); syncTask = nil
        transport?.onChange = nil; transport?.onTrack = nil
        playback?.audioOwner = nil; playback?.close(); playback = nil
        transport?.stop(); transport = nil
        tracks = []; snapshot.queue = []; snapshot.currentIndex = nil; snapshot.positionSeconds = 0
        snapshot.entries = []; snapshot.entryIndex = nil; snapshot.activeOutput = .init()
        lyrics = nil; lyricsError = nil; lyricsLoading = false
        setSleep(minutes: nil)
    }
}
