import SwiftUI
import UIKit

/// Keeps native slider accessibility and touch tracking, with a thumb only during contact.
struct MusicTimelineSlider: UIViewRepresentable {
    @Binding var value: Double
    let duration: Double
    let onEditing: (Bool) -> Void

    func makeUIView(context: Context) -> Control {
        let control = Control()
        control.minimumTrackTintColor = UIColor(LuminaTheme.brand)
        control.maximumTrackTintColor = UIColor.white.withAlphaComponent(0.2)
        control.accessibilityLabel = "Track position"
        control.addTarget(context.coordinator, action: #selector(Coordinator.changed(_:)), for: .valueChanged)
        return control
    }

    func updateUIView(_ control: Control, context: Context) {
        context.coordinator.parent = self
        control.onEditing = onEditing
        control.maximumValue = Float(max(1, duration))
        if !control.isTracking { control.value = Float(value) }
    }

    static func dismantleUIView(_ control: Control, coordinator: Coordinator) {
        let wasTracking = control.isTracking
        let finish = control.onEditing
        control.onEditing = nil
        control.cancelTracking(with: nil)
        if wasTracking { DispatchQueue.main.async { finish?(false) } }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject {
        var parent: MusicTimelineSlider
        init(_ parent: MusicTimelineSlider) { self.parent = parent }
        @objc func changed(_ control: UISlider) { parent.value = Double(control.value) }
    }

    final class Control: UISlider {
        var onEditing: ((Bool) -> Void)?
        private let idleThumb = UIGraphicsImageRenderer(size: CGSize(width: 20, height: 20)).image { _ in }
        private let activeThumb = UIGraphicsImageRenderer(size: CGSize(width: 20, height: 20)).image { context in
            UIColor.white.setFill()
            context.cgContext.fillEllipse(in: CGRect(x: 2, y: 2, width: 16, height: 16))
        }
        override init(frame: CGRect) {
            super.init(frame: frame)
            setThumbImage(idleThumb, for: .normal)
            setThumbImage(activeThumb, for: .highlighted)
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        override func beginTracking(_ touch: UITouch, with event: UIEvent?) -> Bool {
            // Start anywhere along the bar, with the same track geometry at rest and in contact.
            let track = trackRect(forBounds: bounds)
            let fraction = min(1, max(0, (touch.location(in: self).x - track.minX) / max(1, track.width)))
            value = minimumValue + Float(fraction) * (maximumValue - minimumValue)
            let began = super.beginTracking(touch, with: event)
            if began { onEditing?(true); sendActions(for: .valueChanged) }
            return began
        }
        override func endTracking(_ touch: UITouch?, with event: UIEvent?) {
            super.endTracking(touch, with: event)
            onEditing?(false)
        }
        override func cancelTracking(with event: UIEvent?) {
            super.cancelTracking(with: event)
            onEditing?(false)
        }
    }
}
