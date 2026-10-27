import AVKit
import Combine
import SwiftUI
import UIKit

/// AVRoutePickerView owns route discovery and selection; Lumina supplies only
/// the approved glyph so this control remains visually consistent with Cast.
struct NativeMusicAirPlayButton: View {
    let session: NativeAudioSession
    @State private var airPlayOutputName: String?

    var body: some View {
        ZStack {
            NativeMusicAirPlayRoutePicker()
            LuminaIcon(glyph: .airplay, size: 24)
                .foregroundStyle(airPlayOutputName == nil ? .white : LuminaTheme.brand)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .frame(width: 44, height: 44)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("AirPlay")
        .accessibilityValue(airPlayOutputName ?? "This device")
        .accessibilityIdentifier("audioAirPlayButton")
        .disabled(session.isSonos || session.isRemoteOutput || session.busy)
        .onAppear(perform: refreshRoute)
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { _ in
            refreshRoute()
        }
    }

    private func refreshRoute() {
        airPlayOutputName = AVAudioSession.sharedInstance().currentRoute.outputs
            .first(where: { $0.portType == .airPlay })?.portName
    }
}

private struct NativeMusicAirPlayRoutePicker: UIViewRepresentable {
    func makeUIView(context _: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.tintColor = .clear
        picker.activeTintColor = .clear
        picker.prioritizesVideoDevices = false
        picker.accessibilityLabel = "AirPlay"
        picker.accessibilityIdentifier = "audioAirPlayButton"
        return picker
    }

    func updateUIView(_: AVRoutePickerView, context _: Context) { }
}
