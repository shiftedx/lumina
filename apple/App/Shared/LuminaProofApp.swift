import SwiftUI
import LuminaCore

@main
@MainActor
struct LuminaProofApp: App {
    @State private var store = LuminaStore()
    @State private var didRestore = false

    var body: some Scene {
        WindowGroup {
            Group {
                #if os(tvOS)
                TVRootView(store: store)
                #else
                MobileRootView(store: store)
                #endif
            }
            .task {
                guard !didRestore else { return }
                didRestore = true
                await store.restore()
            }
        }
    }
}

/// Licenses are bundled with the app, including transitive native-library notices.
struct PlaybackLicensesView: View {
    @Environment(\.dismiss) private var dismiss
    private let licenses = ["PrismCoreLicense", "MPVKitLicense"].compactMap { name in
        Bundle.main.url(forResource: name, withExtension: "txt").flatMap { try? String(contentsOf: $0, encoding: .utf8) }
    }.joined(separator: "\n\n")

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Playback includes PrismCore, MPVKit and FFmpeg. Complete transitive notices are included in PlaybackNotices.txt in the app bundle.")
                    Link("PrismCore source", destination: URL(string: "https://github.com/Wenzlik/PrismCore/tree/3.8.1")!)
                    Link("MPVKit and FFmpeg source and build recipes", destination: URL(string: "https://github.com/Wenzlik/MPVKit/tree/1.0.1")!)
                    Text(licenses).font(.footnote)
                }.padding()
            }
            .navigationTitle("Open-source licenses")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}
