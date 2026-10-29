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
    private(set) var screen: UIScreen?
    @ObservationIgnored private var attachedScreen: UIScreen?
    private let owner = UUID()
    // Keep the native volume control installed while video is presented. Gestures
    // drive its UISlider; hardware buttons update that same system-owned value.
    @ObservationIgnored private let systemVolumeView: MPVolumeView = {
        let view = MPVolumeView(frame: CGRect(x: -200, y: -100, width: 120, height: 44))
        view.showsRouteButton = false
        view.isUserInteractionEnabled = false
        view.accessibilityElementsHidden = true
        return view
    }()
    private var systemVolumeSlider: UISlider? {
        systemVolumeView.subviews.compactMap { $0 as? UISlider }.first
    }
    private func volumeLevel(_ playback: PlaybackModel) -> Double {
        playback.isCasting ? (playback.castOwner?.volume ?? 0)
            : Double(systemVolumeSlider?.value ?? AVAudioSession.sharedInstance().outputVolume)
    }
    private var rateToken: UUID?
    var hasTemporarySpeedHold: Bool { rateToken != nil }
    #if LUMINA_UI_TESTING
    private(set) var actionCount = 0
    private(set) var lastAction = "none"
    private(set) var lastBrightnessRequest: Double?
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
        if systemVolumeView.superview !== window {
            systemVolumeView.removeFromSuperview()
            window?.addSubview(systemVolumeView)
        }
        let scene = window?.windowScene
        let next = scene?.session.role == .windowApplication ? scene?.screen : nil
        guard attachedScreen !== next else { return }
        attachedScreen = next
        releaseBrightness()
        // UIKit supports brightness writes only through the main-screen control.
        // A Duo app window can belong to the inner display instead; track that
        // display for transitions, without treating it as a separate backlight API.
        screen = next == nil ? nil : UIScreen.main
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
        guard let applied = VideoScreenBrightness.shared.apply(value, screen: screen, owner: owner) else { return }
        #if LUMINA_UI_TESTING
        lastBrightnessRequest = applied
        #endif
        showBrightness(level: applied)
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
                adjustmentLevel = playback.player.isExternalPlaybackActive && !playback.isCasting ? nil : volumeLevel(playback)
                showVolume(playback)
            } else {
                adjustmentLevel = screen.map { Double($0.brightness) }
                if let screen { VideoScreenBrightness.shared.begin(screen: screen, owner: owner) }
                showBrightness()
            }
        case .updateAdjustment(let kind, let delta, let size):
            guard let level = adjustmentLevel, size.height > 0 else { return }
            let now = ProcessInfo.processInfo.systemUptime
            guard now - lastAdjustmentUpdate >= 1.0 / 60 else { return }
            let movement = delta.y - lastAdjustmentY
            let currentLevel = kind == .volume && !playback.isCasting ? volumeLevel(playback) : level
            let applied = min(1, max(0, currentLevel - movement / (0.6 * size.height)))
            lastAdjustmentY = delta.y
            lastAdjustmentUpdate = now
            if kind == .volume {
                if movement < 0 { playback.setVideoOutputMuted(false) }
                if playback.isCasting {
                    playback.castOwner?.setVolume(applied)
                } else if let slider = systemVolumeSlider {
                    slider.setValue(Float(applied), animated: false)
                    slider.sendActions(for: .valueChanged)
                }
                adjustmentLevel = applied
                showVolume(playback)
            } else if let screen {
                if let applied = VideoScreenBrightness.shared.apply(applied, screen: screen, owner: owner) {
                    adjustmentLevel = applied
                    #if LUMINA_UI_TESTING
                    lastBrightnessRequest = applied
                    #endif
                    showBrightness(level: applied)
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
        if playback.player.isExternalPlaybackActive && !playback.isCasting {
            show(Feedback(title: "Adjust volume on the connected device", glyph: .volumeHigh, side: .trailing))
        } else {
            let level = volumeLevel(playback)
            show(Feedback(title: playback.isCasting ? "Chromecast volume" : "Device volume", detail: playback.videoOutputMuted ? "Muted" : "\(Int((level * 100).rounded()))%", level: level,
                          glyph: playback.videoOutputMuted || level == 0 ? .volumeOff : .volumeHigh, side: .trailing))
        }
    }

    private func showBrightness(level requested: Double? = nil) {
        if let screen {
            let level = requested ?? Double(screen.brightness)
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
        var ownership = VideoBrightnessOwnership()
    }
    private var entries: [ObjectIdentifier: Entry] = [:]

    func begin(screen: UIScreen, owner: UUID) {
        let id = ObjectIdentifier(screen)
        var entry = entries[id] ?? Entry()
        let observed = Double(screen.brightness)
        _ = entry.ownership.apply(observed, owner: owner, observed: observed)
        entries[id] = entry
    }

    func apply(_ value: Double, screen: UIScreen, owner: UUID) -> Double? {
        let id = ObjectIdentifier(screen)
        if entries[id] == nil { begin(screen: screen, owner: owner) }
        guard var entry = entries[id], let level = entry.ownership.update(value, owner: owner) else { return nil }
        entries[id] = entry
        // UIScreen readback/notifications can lag our write. Only reconcile with
        // the physical display at gesture boundaries, never between swipe events.
        screen.brightness = CGFloat(level)
        return level
    }

    func release(screen: UIScreen, owner: UUID) {
        let id = ObjectIdentifier(screen)
        guard var entry = entries[id], entry.ownership.owner == owner else { return }
        let restore = entry.ownership.release(owner: owner, observed: Double(screen.brightness))
        entries.removeValue(forKey: id)
        if let restore { screen.brightness = CGFloat(restore) }
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
