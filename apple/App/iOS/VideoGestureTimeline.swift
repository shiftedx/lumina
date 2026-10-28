import LuminaCore
import SwiftUI
import UIKit

/// The timeline owns its entire touch, including vertical fine-scrub movement.
struct VideoGestureTimeline: View {
    let playback: PlaybackModel
    let presentation: VideoGesturePresentation
    let preferences: NativePlaybackPreferences
    let cancellationID: UUID
    let containerHeight: Double
    let onEditing: (Bool) -> Void
    @State private var fine = VideoFineScrub()
    @State private var candidate: Double?
    @State private var startY = 0.0
    @State private var lastPointerX = 0.0
    @State private var direction = VideoSeekDirection.forward

    var body: some View {
        // Previewing a drag can run at display cadence. Read AVFoundation's live
        // seekable ranges once for this render; PlaybackModel validates them again
        // before a seek is committed so live/DVR bounds remain authoritative.
        let ranges = playback.gestureSeekRanges
        let lower = ranges.first?.lowerBound ?? 0
        let upper = ranges.last?.upperBound ?? 0
        let position = presentation.previewTime ?? playback.gestureSeekTime
        let progress = upper > lower ? min(1, max(0, (position - lower) / (upper - lower))) : 0
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.white.opacity(0.2)).frame(height: 5)
                Capsule().fill(.white).frame(width: max(0, geometry.size.width * progress), height: 5)
                Circle().fill(.white).frame(width: 18, height: 18)
                    .offset(x: max(0, min(geometry.size.width - 18, geometry.size.width * progress - 9)))
                VideoTimelineTouchSurface(
                    enabled: !ranges.isEmpty && playback.canSeekVideo && !playback.hasFinished,
                    value: VideoGesturePresentation.clock(position),
                    cancellationID: cancellationID,
                    event: { event, point, size in
                        handle(event, point: point, size: size, containerHeight: max(1, containerHeight),
                               ranges: ranges, lower: lower, upper: upper)
                    },
                    adjust: { direction in
                        playback.requestRelativeVideoSeek(Double(direction > 0 ? preferences.skipForwardSeconds : -preferences.skipBackwardSeconds))
                    }
                )
            }
            .frame(height: 44)
        }
        .frame(height: 44)
        .onDisappear { cancel() }
        .onChange(of: playback.request.id) { _, _ in cancel() }
        .onChange(of: cancellationID) { _, _ in cancel() }
        .accessibilityIdentifier("videoTimeline")
    }

    private func handle(_ event: VideoTimelineTouchSurface.Event, point: CGPoint, size: CGSize, containerHeight: Double,
                        ranges: [VideoSeekRange], lower: Double, upper: Double) {
        if case .cancelled = event {
            cancel()
            return
        }
        guard size.width > 0, upper > lower else { return }
        let pointer = VideoGesturePoint(x: Double(point.x), y: Double(point.y))
        switch event {
        case .began:
            startY = Double(point.y)
            lastPointerX = Double(point.x)
            candidate = lower + min(1, max(0, point.x / size.width)) * (upper - lower)
            direction = (candidate ?? lower) >= playback.gestureSeekTime ? .forward : .backward
            fine.begin(at: pointer)
            onEditing(true)
            show(direction: direction, ranges: ranges)
        case .moved, .ended:
            guard candidate != nil else { return }
            let movement = Double(point.x) - lastPointerX
            if movement != 0 { direction = movement > 0 ? .forward : .backward }
            lastPointerX = Double(point.x)
            if let change = fine.update(to: pointer, upwardDistance: max(0, startY - Double(point.y)), containerHeight: containerHeight) {
                if fine.isFineMode {
                    candidate = min(upper, max(lower, (candidate ?? lower) + change.deltaX / size.width * (upper - lower) * change.multiplier))
                } else {
                    candidate = lower + min(1, max(0, point.x / size.width)) * (upper - lower)
                }
                show(direction: direction, ranges: ranges)
            }
            if event == .ended {
                if let target = presentation.previewTime { playback.seek(to: target) }
                finish()
            }
        case .cancelled:
            break
        }
    }

    private func show(direction: VideoSeekDirection, ranges: [VideoSeekRange]) {
        guard let candidate,
              let target = VideoSeekAuthority.project(candidate, into: ranges, direction: direction) else { return }
        presentation.showTimeline(target: target, multiplier: fine.multiplier)
    }

    private func finish() {
        candidate = nil
        fine.cancel()
        presentation.finishTimeline()
        onEditing(false)
    }

    private func cancel() { if candidate != nil { finish() } }
}

private struct VideoTimelineTouchSurface: UIViewRepresentable {
    enum Event { case began, moved, ended, cancelled }
    let enabled: Bool
    let value: String
    let cancellationID: UUID
    let event: (Event, CGPoint, CGSize) -> Void
    let adjust: (Int) -> Void

    func makeUIView(context: Context) -> TimelineControl { TimelineControl() }
    func updateUIView(_ view: TimelineControl, context: Context) {
        let previousEvent = view.event
        if view.cancellationID != cancellationID {
            view.cancelContact(deferred: true, event: previousEvent)
            view.cancellationID = cancellationID
        }
        if !enabled { view.cancelContact(deferred: true, event: previousEvent) }
        view.event = event
        view.adjust = adjust
        view.accessibilityValue = value
        view.isEnabled = enabled
    }
    static func dismantleUIView(_ view: TimelineControl, coordinator: ()) { view.cancelContact(deferred: true) }

    final class TimelineControl: UIControl {
        var event: ((VideoTimelineTouchSurface.Event, CGPoint, CGSize) -> Void)?
        var adjust: ((Int) -> Void)?
        var cancellationID: UUID?
        private var active: UITouch?
        private var suppressed = false
        private var interactionGeneration = 0
        override init(frame: CGRect) {
            super.init(frame: frame)
            isMultipleTouchEnabled = true
            isAccessibilityElement = true
            accessibilityLabel = "Video position"
            accessibilityTraits = .adjustable
            accessibilityIdentifier = "videoTimelineControl"
            backgroundColor = .clear
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        override func accessibilityIncrement() { adjust?(1) }
        override func accessibilityDecrement() { adjust?(-1) }
        override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
            guard isEnabled, !suppressed, active == nil, touches.count == 1,
                  event?.allTouches?.filter({ $0.phase != .ended && $0.phase != .cancelled }).count == 1,
                  let touch = touches.first else {
                cancelContact(); suppressed = true; return
            }
            active = touch
            interactionGeneration &+= 1
            self.event?(.began, touch.location(in: self), bounds.size)
        }
        override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
            guard let active, touches.contains(active), !suppressed else { return }
            self.event?(.moved, active.location(in: self), bounds.size)
        }
        override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
            if let active, touches.contains(active), !suppressed {
                self.event?(.ended, active.location(in: self), bounds.size)
                self.active = nil
            }
            if event?.allTouches?.allSatisfy({ $0.phase == .ended || $0.phase == .cancelled }) != false { suppressed = false }
        }
        override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { cancelContact(); suppressed = false }
        func cancelContact(deferred: Bool = false,
                           event callback: ((VideoTimelineTouchSurface.Event, CGPoint, CGSize) -> Void)? = nil) {
            guard active != nil else { return }
            active = nil
            let callback = callback ?? event
            let generation = interactionGeneration
            let size = bounds.size
            if deferred {
                DispatchQueue.main.async { [weak self] in
                    guard self?.interactionGeneration == generation else { return }
                    callback?(.cancelled, .zero, size)
                }
            } else {
                callback?(.cancelled, .zero, size)
            }
        }
    }
}
