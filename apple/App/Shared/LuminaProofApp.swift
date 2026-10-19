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
