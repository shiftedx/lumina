import AVFoundation
import LuminaCore
import MediaPlayer
import Observation
import SwiftUI
import UIKit

/// Transient presentation state only. PlaybackModel remains the transport authority.
@MainActor @Observable
final class VideoGesturePresentation {
    struct Feedback {
        var title: String
        var detail: String? = nil
        var level: Double? = nil
        var glyph: LuminaGlyph? = nil
        var symbol: String? = nil
        var side: HorizontalAlignment = .center
    }

    var feedback: Feedback?
    var previewTime: Double?
    var interacting = false
    private(set) var externalBrightnessRevision = 0
    private(set) var screen: UIScreen?
    @ObservationIgnored private weak var attachedScreen: UIScreen?
    private let owner = UUID()
    private var rateToken: UUID?
    var hasTemporarySpeedHold: Bool { rateToken != nil }
    #if LUMINA_UI_TESTING
    private(set) var actionCount = 0
    private(set) var lastAction = "none"
    #endif
    private var adjustmentLevel: Double?
    private var lastAdjustmentY = 0.0
    private var scrubCandidate: Double?
    private var scrubAnchor = 0.0
    private var lastScrubX = 0.0
    private var surfaceSeekSpan = 0.0
    private var scrubDirection = VideoSeekDirection.forward
    private var lastAdjustmentUpdate = 0.0
    private var dismissal: Task<Void, Never>?

    func attach(window: UIWindow?) {
        let next = window?.windowScene?.screen
        guard attachedScreen !== next else { return }
        attachedScreen = next
        releaseBrightness()
        // Brightness is defined only for the built-in display. Resolve the target
        // through the window; main is used only as UIKit's capability identity.
        screen = next === UIScreen.main ? next : nil
    }

    func cancel(playback: PlaybackModel, releaseBrightness: Bool) {
        if let rateToken { playback.endTemporaryVideoRate(rateToken) }
        rateToken = nil
        previewTime = nil
        scrubCandidate = nil
        adjustmentLevel = nil
        interacting = false
        dismissal?.cancel()
        feedback = nil
        if releaseBrightness { self.releaseBrightness() }
    }

    func releaseBrightness() {
        if let screen { VideoScreenBrightness.shared.release(screen: screen, owner: owner) }
    }

    func setBrightness(_ value: Double) {
        guard let screen else { return }
        _ = VideoScreenBrightness.shared.apply(value, screen: screen, owner: owner, revoked: { [weak self] in
            self?.adjustmentLevel = nil
            self?.externalBrightnessRevision += 1
        })
        showBrightness()
        endFeedback()
    }

    func handle(_ action: VideoGestureAction, playback: PlaybackModel,
                preferences: NativePlaybackPreferences, toggleControls: () -> Void) {
        #if LUMINA_UI_TESTING
        actionCount += 1
        lastAction = String(describing: action)
        #endif
        switch action {
        case .toggleControls:
            toggleControls()
        case .togglePlayback:
            playback.togglePlayback()
            show(Feedback(title: playback.isPlaying ? "Playing" : "Paused", glyph: playback.isPlaying ? .play : .pause))
            endFeedback()
        case .beginAdjustment(let kind):
            beginFeedback(preferences)
            lastAdjustmentY = 0
            lastAdjustmentUpdate = 0
            if kind == .volume {
                adjustmentLevel = playback.player.isExternalPlaybackActive ? nil : Double(playback.player.volume)
                showVolume(playback)
            } else {
                adjustmentLevel = screen.map { Double($0.brightness) }
                if let screen, let adjustmentLevel {
                    _ = VideoScreenBrightness.shared.apply(adjustmentLevel, screen: screen, owner: owner, revoked: { [weak self] in
                        self?.adjustmentLevel = nil
                        self?.externalBrightnessRevision += 1
                    })
                }
                showBrightness()
            }
        case .updateAdjustment(let kind, let delta, let size):
            guard let level = adjustmentLevel, size.height > 0 else { return }
            let now = ProcessInfo.processInfo.systemUptime
            guard now - lastAdjustmentUpdate >= 1.0 / 60 else { return }
            let movement = delta.y - lastAdjustmentY
            let applied = min(1, max(0, level - movement / (0.6 * size.height)))
            lastAdjustmentY = delta.y
            lastAdjustmentUpdate = now
            if kind == .volume {
                if movement < 0 { playback.player.isMuted = false }
                playback.player.volume = Float(applied)
                adjustmentLevel = Double(playback.player.volume)
                showVolume(playback)
            } else if let screen {
                if VideoScreenBrightness.shared.apply(applied, screen: screen, owner: owner, revoked: { [weak self] in
                    self?.adjustmentLevel = nil
                    self?.externalBrightnessRevision += 1
                }) {
                    adjustmentLevel = Double(screen.brightness)
                    showBrightness()
                }
            }
        case .endAdjustment, .cancelAdjustment:
            adjustmentLevel = nil
            endFeedback()
        case .beginSurfaceScrub:
            beginFeedback(preferences)
            scrubAnchor = playback.gestureSeekTime
            let ranges = playback.gestureSeekRanges
            let duration = (ranges.last?.upperBound ?? 0) - (ranges.first?.lowerBound ?? 0)
            surfaceSeekSpan = min(duration, min(180, max(30, duration * 0.05)))
            scrubCandidate = VideoSeekAuthority.project(scrubAnchor, into: ranges, direction: .forward)
            lastScrubX = 0
            scrubDirection = .forward
            previewTime = scrubCandidate
            if let previewTime { showScrub(target: previewTime) }
            else { show(Feedback(title: "Seeking unavailable")) }
        case .updateSurfaceScrub(let delta, let size):
            updateScrub(delta: delta, size: size, playback: playback)
        case .commitSurfaceScrub(let delta, let size):
            updateScrub(delta: delta, size: size, playback: playback)
            if let previewTime { playback.seek(to: previewTime) }
            previewTime = nil
            scrubCandidate = nil
            endFeedback()
        case .cancelSurfaceScrub:
            previewTime = nil
            scrubCandidate = nil
            endFeedback()
        case .relativeSkip(let direction, let count):
            let amount = direction == .forward ? preferences.skipForwardSeconds : preferences.skipBackwardSeconds
            let burstSeconds = (Double(count) * Double(amount)).formatted(.number.precision(.fractionLength(0)))
            let delta = Double(direction == .forward ? amount : -amount)
            guard !playback.gestureSeekRanges.isEmpty else {
                show(Feedback(title: "Seeking unavailable")); endFeedback(); return
            }
            playback.requestRelativeVideoSeek(delta)
            show(Feedback(title: "\(direction == .forward ? "+" : "−")\(burstSeconds) seconds",
                          detail: Self.clock(playback.gestureSeekTime),
                          glyph: direction == .forward ? .fastForward : .rewind,
                          side: direction == .forward ? .trailing : .leading))
            haptic(preferences)
            endFeedback()
        case .beginTemporarySpeedHold:
            beginFeedback(preferences)
            rateToken = playback.beginTemporaryVideoRate()
            show(Feedback(title: rateToken != nil ? "2×" : playback.preferredVideoRate >= 2 ? "Already at 2× or faster" : "Hold unavailable", glyph: .playbackSpeed))
        case .endTemporarySpeedHold:
            if let rateToken { playback.endTemporaryVideoRate(rateToken) }
            rateToken = nil
            endFeedback()
        }
    }

    private func updateScrub(delta: VideoGestureVector, size: VideoGestureSize, playback: PlaybackModel) {
        let ranges = playback.gestureSeekRanges
        guard var candidate = scrubCandidate, size.width > 0,
              let first = ranges.first, let last = ranges.last else {
            previewTime = nil
            show(Feedback(title: "Seeking unavailable")); return
        }
        let movement = delta.x - lastScrubX
        if movement != 0 { scrubDirection = movement > 0 ? .forward : .backward }
        candidate += movement / size.width * surfaceSeekSpan
        lastScrubX = delta.x
        scrubCandidate = min(last.upperBound, max(first.lowerBound, candidate))
        let target = VideoSeekAuthority.project(scrubCandidate ?? candidate, into: ranges, direction: scrubDirection)
        previewTime = target
        if let target { showScrub(target: target) }
    }

    func showTimeline(target: Double, multiplier: Double) {
        previewTime = target
        show(Feedback(title: Self.clock(target), detail: multiplier < 1 ? "Fine scrubbing · \(multiplier.formatted())×" : nil, glyph: .playbackSpeed))
    }

    func finishTimeline() { previewTime = nil; endFeedback() }

    private func showScrub(target: Double) {
        let delta = target - scrubAnchor
        show(Feedback(title: Self.clock(target), detail: "\(delta >= 0 ? "+" : "−")\(Self.clock(abs(delta)))", glyph: delta >= 0 ? .fastForward : .rewind))
    }

    private func showVolume(_ playback: PlaybackModel) {
        if playback.player.isExternalPlaybackActive {
            show(Feedback(title: "Adjust volume on the connected device", glyph: .volumeHigh, side: .trailing))
        } else {
            let level = Double(playback.player.volume)
            show(Feedback(title: "Player volume", detail: playback.player.isMuted ? "Muted" : "\(Int((level * 100).rounded()))%", level: level,
                          glyph: playback.player.isMuted || level == 0 ? .volumeOff : .volumeHigh, side: .trailing))
        }
    }

    private func showBrightness() {
        if let screen {
            let level = Double(screen.brightness)
            show(Feedback(title: "Brightness", detail: "\(Int((level * 100).rounded()))%", level: level, symbol: "sun.max", side: .leading))
        } else { show(Feedback(title: "Adjust brightness on the display", symbol: "sun.max", side: .leading)) }
    }

    private func beginFeedback(_ preferences: NativePlaybackPreferences) {
        interacting = true
        dismissal?.cancel()
        haptic(preferences)
    }

    private func haptic(_ preferences: NativePlaybackPreferences) {
        guard preferences.gestureHapticsEnabled else { return }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    private func show(_ value: Feedback) {
        dismissal?.cancel()
        feedback = value
    }

    private func endFeedback() {
        interacting = false
        dismissal?.cancel()
        dismissal = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(750))
            guard !Task.isCancelled else { return }
            self?.feedback = nil
        }
    }

    static func clock(_ value: Double) -> String {
        guard value.isFinite, value >= 0 else { return "—" }
        let seconds = Int(min(value, Double(Int.max / 2)))
        return seconds >= 3600
            ? String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}

@MainActor
private final class VideoScreenBrightness {
    static let shared = VideoScreenBrightness()
    private struct Entry {
        let screen: UIScreen
        var ownership = VideoBrightnessOwnership()
        var revoked: (() -> Void)?
    }
    private var entries: [ObjectIdentifier: Entry] = [:]
    private var observer: NSObjectProtocol?

    private init() {
        observer = NotificationCenter.default.addObserver(forName: UIScreen.brightnessDidChangeNotification, object: nil, queue: .main) { [weak self] notification in
            guard let screen = notification.object as? UIScreen else { return }
            MainActor.assumeIsolated {
                self?.observe(screen)
            }
        }
    }

    func apply(_ value: Double, screen: UIScreen, owner: UUID, revoked: @escaping () -> Void) -> Bool {
        let id = ObjectIdentifier(screen)
        var entry = entries[id] ?? Entry(screen: screen)
        if entry.ownership.observe(Double(screen.brightness)) != nil {
            entries.removeValue(forKey: id)
            entry.revoked?()
            return false
        }
        if entry.ownership.owner != nil, entry.ownership.owner != owner { entry.revoked?() }
        guard let level = entry.ownership.apply(value, owner: owner, observed: Double(screen.brightness)) else { return false }
        entry.revoked = revoked
        entries[id] = entry
        screen.brightness = CGFloat(level)
        return true
    }

    func release(screen: UIScreen, owner: UUID) {
        let id = ObjectIdentifier(screen)
        guard var entry = entries[id], entry.ownership.owner == owner else { return }
        let restore = entry.ownership.release(owner: owner, observed: Double(screen.brightness))
        entries.removeValue(forKey: id)
        if let restore { screen.brightness = CGFloat(restore) }
    }

    private func observe(_ screen: UIScreen) {
        let id = ObjectIdentifier(screen)
        guard var entry = entries[id] else { return }
        if entry.ownership.observe(Double(screen.brightness)) != nil {
            entries.removeValue(forKey: id)
            entry.revoked?()
        }
    }
}

/// Keeps display-rate gesture feedback out of the video stage's dependency set.
struct VideoGestureFeedbackOverlay: View {
    let presentation: VideoGesturePresentation
    let safeArea: EdgeInsets
    let reduceMotion: Bool

    var body: some View {
        ZStack {
            if let feedback = presentation.feedback {
                HStack {
                    if feedback.side != .leading { Spacer(minLength: 0) }
                    VideoGestureFeedbackView(feedback: feedback)
                    if feedback.side != .trailing { Spacer(minLength: 0) }
                }
                .padding(.horizontal, max(24, max(safeArea.leading, safeArea.trailing)))
                .padding(.top, safeArea.top + 84)
                .frame(maxHeight: .infinity, alignment: .top)
                .transition(.opacity)
            }
        }
        .allowsHitTesting(false)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: presentation.feedback == nil)
    }
}

struct VideoGestureFeedbackView: View {
    let feedback: VideoGesturePresentation.Feedback
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        HStack(spacing: 12) {
            if let glyph = feedback.glyph { LuminaIcon(glyph: glyph, size: 24) }
            else if let symbol = feedback.symbol { Image(systemName: symbol).font(.title3) }
            VStack(alignment: .leading, spacing: 4) {
                Text(feedback.title).font(.headline.monospacedDigit())
                if let detail = feedback.detail { Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.white.opacity(0.8)) }
                if let level = feedback.level { ProgressView(value: level).tint(LuminaTheme.brand) }
            }
        }
        .foregroundStyle(.white).padding(14).frame(maxWidth: 250)
        .background {
            if reduceTransparency { RoundedRectangle(cornerRadius: 12).fill(Color.black) }
            else { RoundedRectangle(cornerRadius: 12).fill(.ultraThinMaterial) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("videoGestureFeedback")
        .allowsHitTesting(false)
    }
}

struct VideoSystemVolumeView: UIViewRepresentable {
    func makeUIView(context: Context) -> MPVolumeView {
        let view = MPVolumeView()
        view.accessibilityLabel = "System volume"
        return view
    }
    func updateUIView(_ uiView: MPVolumeView, context: Context) {}
}
