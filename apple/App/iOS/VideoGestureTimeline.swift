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

    private var ranges: [VideoSeekRange] { playback.gestureSeekRanges }
    private var lower: Double { ranges.first?.lowerBound ?? 0 }
    private var upper: Double { ranges.last?.upperBound ?? 0 }
    private var position: Double { presentation.previewTime ?? playback.gestureSeekTime }
    private var fraction: Double {
        let currentRanges = ranges
        let lower = currentRanges.first?.lowerBound ?? 0
        let upper = currentRanges.last?.upperBound ?? 0
        return upper > lower ? min(1, max(0, (position - lower) / (upper - lower))) : 0
    }

    var body: some View {
        let progress = fraction
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.white.opacity(0.2)).frame(height: 5)
                Capsule().fill(.white).frame(width: max(0, geometry.size.width * progress), height: 5)
                Circle().fill(.white).frame(width: 18, height: 18)
                    .offset(x: max(0, min(geometry.size.width - 18, geometry.size.width * progress - 9)))
                VideoTimelineTouchSurface(
                    enabled: !ranges.isEmpty && playback.canUseVideoActions && !playback.hasFinished,
                    value: VideoGesturePresentation.clock(position),
                    cancellationID: cancellationID,
                    event: { event, point, size in
                        handle(event, point: point, size: size, containerHeight: max(1, containerHeight))
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

    private func handle(_ event: VideoTimelineTouchSurface.Event, point: CGPoint, size: CGSize, containerHeight: Double) {
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
            show(direction: direction)
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
                show(direction: direction)
            }
            if event == .ended {
                if let target = presentation.previewTime { playback.seek(to: target) }
                finish()
            }
        case .cancelled:
            cancel()
        }
    }

    private func show(direction: VideoSeekDirection) {
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
        view.event = event
        view.adjust = adjust
        view.accessibilityValue = value
        if view.cancellationID != cancellationID { view.cancelContact(); view.cancellationID = cancellationID }
        if !enabled { view.cancelContact() }
        view.isEnabled = enabled
    }
    static func dismantleUIView(_ view: TimelineControl, coordinator: ()) { view.cancelContact() }

    final class TimelineControl: UIControl {
        var event: ((VideoTimelineTouchSurface.Event, CGPoint, CGSize) -> Void)?
        var adjust: ((Int) -> Void)?
        var cancellationID: UUID?
        private var active: UITouch?
        private var suppressed = false
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
        func cancelContact() {
            guard active != nil else { return }
            active = nil
            event?(.cancelled, .zero, bounds.size)
        }
    }
}
