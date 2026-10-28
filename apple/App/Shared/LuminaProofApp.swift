import SwiftUI
import LuminaCore

@main
@MainActor
struct LuminaProofApp: App {
    init() {
        #if os(iOS)
        NativeVideoCasting.configure()
        #endif
    }

    @State private var store = makeStore()
    @State private var restorationStarted = false
    @State private var isRestoring = true

    private static func makeStore() -> LuminaStore {
        #if LUMINA_UI_TESTING
        if ProcessInfo.processInfo.arguments.contains("--ui-testing-offline") {
            return LuminaStore(playbackPreferences: NativePlaybackPreferences(defaults: LuminaPreferences.defaults),
                               connectivity: LuminaConnectivity(status: .offline, monitoringEnabled: false))
        }
        #endif
        return LuminaStore(playbackPreferences: NativePlaybackPreferences(defaults: LuminaPreferences.defaults))
    }

    var body: some Scene {
        WindowGroup {
            Group {
                #if os(tvOS)
                TVRootView(store: store)
                #else
                MobileRootView(store: store, isRestoring: isRestoring)
                #endif
            }
            .defaultAppStorage(LuminaPreferences.defaults)
            .task {
                guard !restorationStarted else { return }
                restorationStarted = true
                await store.restore()
                isRestoring = false
            }
        }
    }
}

/// Licenses are bundled with the app, including transitive native-library notices.
struct PlaybackLicensesView: View {
    @Environment(\.dismiss) private var dismiss
    private let licenses = ["PrismCoreLicense", "MPVKitLicense", "GoogleCastNotices"].compactMap { name in
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

@MainActor
enum LuminaPreferences {
    static let defaults: UserDefaults = {
        #if DEBUG || LUMINA_UI_TESTING
        if let namespace = ProcessInfo.processInfo.environment["LUMINA_UI_TEST_NAMESPACE"],
           UUID(uuidString: namespace) != nil {
            return UserDefaults(suiteName: "dev.lumina.ui-test.\(namespace)")!
        }
        #endif
        return .standard
    }()
}
