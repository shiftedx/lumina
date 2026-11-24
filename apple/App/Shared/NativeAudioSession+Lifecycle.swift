import LuminaCore
import UIKit

/// Both music and reader checkpoints must surrender their assertion when iOS expires it.
@MainActor
enum NativeBackgroundCheckpoint {
    static func run(name: String, onExpiration: @escaping @MainActor () -> Void = {},
                    operation: @escaping @MainActor () async -> Void) async {
        var identifier = UIBackgroundTaskIdentifier.invalid
        let work = ExpiringBackgroundWork {
            guard identifier != .invalid else { return }
            UIApplication.shared.endBackgroundTask(identifier)
            identifier = .invalid
        }
        identifier = UIApplication.shared.beginBackgroundTask(withName: name) {
            work.expire()
            onExpiration()
        }
        guard identifier != .invalid else { work.expire(); return }
        work.start(operation)
        await work.wait()
    }
}

extension NativeAudioSession {
    func connectivityDidChange() {
        if store?.isOffline == true {
            cancelContinuation()
            lyricsTask?.cancel()
            statusTask?.cancel()
            syncTask?.cancel(); syncTask = nil
            lyricsLoading = false
        } else {
            Task { await refreshSharedQueue() }
        }
    }

    func enterBackground() {
        isForeground = false
        lyricsTask?.cancel(); lyricsTask = nil
        lyricsLoading = false
        statusTask?.cancel(); statusTask = nil
    }

    func resumeAfterActivation() {
        isForeground = true
        enforceSleepDeadline()
        if isSonos { startStatusPolling() }
        if playback != nil, lyrics?.refreshing != false { loadLyrics() }
    }

    func flushCheckpoint() async {
        guard let store, identity == store.music.sessionIdentity else { return }
        persist()
        await NativeBackgroundCheckpoint.run(name: "Music checkpoint") {
            await store.music.flushCheckpoint()
        }
    }
}
