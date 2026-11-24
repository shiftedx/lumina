import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

extension EnvironmentValues {
    @Entry var playRemoteStream: (HomeExternalItem) -> Void = { _ in }
}

@MainActor
struct MobileRootView: View {
    @Bindable var store: LuminaStore
    let isRestoring: Bool
    @State private var audioSession = NativeAudioSession()
    @State private var videoSession = NativeVideoSession()
    @State private var remoteStream: HomeExternalItem?
    @State private var userSettings = LuminaUserSettings()
    @State private var hasShownOnlineShell = false
    @State private var artworkReclamationGeneration = 0
    @State private var foregroundRefreshTask: Task<Void, Never>?
    @State private var foregroundRefreshID: UUID?
    @State private var needsForegroundRefresh = false
    #if LUMINA_UI_TESTING
    @State private var foregroundRefreshStarts = 0
    #endif
    @AppStorage("lumina.appearance") private var appearance = "dark"
    @AppStorage("native.appearance") private var deviceAppearance = "account"
    @Environment(\.scenePhase) private var scenePhase
    @State private var incomingPairingLink: URL?
    @State private var pairingLinkMessage: String?
    @State private var pendingTVApproval: TVApprovalRequest?
    @State private var pendingTVHandoff: TVPairingHandoff?
    @State private var tvPairingPresented = false

    var body: some View {
        Group {
            if isRestoring && store.session == nil {
                ProgressView()
            } else if store.session == nil {
                ConnectionView(store: store, incomingPairingLink: incomingPairingLink) {
                    incomingPairingLink = nil
                }
            } else {
                ZStack {
                    if !store.isOffline || hasShownOnlineShell {
                        LibraryShellView(store: store) {
                            pendingTVApproval = nil
                            tvPairingPresented = true
                        }
                            .allowsHitTesting(!store.isOffline)
                            .accessibilityHidden(store.isOffline)
                            .environment(\.luminaArtworkMotionEnabled, !store.isOffline)
                            .onAppear { hasShownOnlineShell = true }
                    }
                    if store.isOffline {
                        NavigationStack { NativeOfflineLibraryView(store: store, usesPushedChrome: false) }
                    }
                }
            }
        }
        .foregroundStyle(LuminaTheme.ink)
        .background(LuminaTheme.paper.ignoresSafeArea())
        .modifier(NativeVideoPlayerPresentation(session: videoSession, store: store))
        .fullScreenCover(item: $remoteStream) { item in
            NativeRemotePlayer(store: store, item: item)
        }
        .environment(\.playRemoteStream, { remoteStream = $0 })
        .environment(videoSession)
        .environment(audioSession)
        .environment(userSettings)
        .environment(\.luminaArtworkReclamationGeneration, artworkReclamationGeneration)
        #if LUMINA_UI_TESTING
        .overlay(alignment: .bottomLeading) {
            if ProcessInfo.processInfo.arguments.contains("--ui-testing-ios-lifecycle") {
                Button("Simulate memory warning") {
                    NotificationCenter.default.post(name: UIApplication.didReceiveMemoryWarningNotification, object: nil)
                }
                .font(.caption2)
                .padding(8)
                .background(.black)
                .accessibilityIdentifier("iosLifecycleProbe")
                .accessibilityValue("refreshes=\(foregroundRefreshStarts) reclamations=\(artworkReclamationGeneration) phase=\(scenePhase)")
            }
        }
        #endif
        .modifier(NativeMetadataPresentation(store: store))
        .preferredColorScheme(effectiveAppearance == "system" ? nil : effectiveAppearance == "light" ? .light : .dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil {
                audioSession.stop(); videoSession.dismiss()
            } else if pendingTVApproval != nil {
                tvPairingPresented = true
            }
        }
        .onChange(of: MobileHomeSessionScope(store.session)) { _, _ in
            cancelForegroundRefresh()
            needsForegroundRefresh = false
        }
        .onChange(of: store.isOffline) { _, _ in audioSession.connectivityDidChange() }
        .task { store.startConnectivityMonitoring() }
        .task(id: MobileHomeSessionScope(store.session)) {
            if !store.isOffline { await store.metadata.reloadPermissions() }
        }
        .task(id: MobileHomeSessionScope(store.session)) {
            userSettings.configure(session: store.session)
            if store.session != nil {
                // A restored listening queue is member state required to surface
                // a deliberate device/server conflict. Keep it session-owned so
                // a root redraw cannot strand state after it claims restoration.
                audioSession.restoreFromRoot(store: store)
                if !store.isOffline {
                    await userSettings.load()
                    if userSettings.hasLoaded { appearance = userSettings.theme }
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                ArtworkImageCache.shared.setBackgrounded(false)
                store.setCatalogBackgrounded(false)
                audioSession.resumeAfterActivation()
                refreshAfterForegrounding()
            } else if phase == .background {
                ArtworkImageCache.shared.setBackgrounded(true)
                artworkReclamationGeneration &+= 1
                store.setCatalogBackgrounded(true)
                audioSession.enterBackground()
                needsForegroundRefresh = true
                cancelForegroundRefresh()
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didReceiveMemoryWarningNotification)) { _ in
            ArtworkImageCache.shared.trimMemory()
            artworkReclamationGeneration &+= 1
        }
        .onOpenURL { link in
            if let handoff = try? TVPairingHandoff(link: link) {
                beginTVHandoff(handoff)
                return
            }
            if let approval = try? TVPairingApprovalLink(deepLink: link) {
                pendingTVHandoff = nil
                pendingTVApproval = TVApprovalRequest(code: approval.code, expectedServerID: approval.serverID)
                if store.session != nil { tvPairingPresented = true }
                else { pairingLinkMessage = "Sign in before approving this TV request." }
                return
            }
            guard store.session == nil else {
                pairingLinkMessage = "Sign out before connecting another server."
                return
            }
            incomingPairingLink = link
        }
        .onChange(of: isRestoring) { _, restoring in
            guard !restoring, store.session != nil else { return }
            if incomingPairingLink != nil {
                incomingPairingLink = nil
                pairingLinkMessage = "Sign out before connecting another server."
            }
        }
        .onDisappear { cancelForegroundRefresh() }
        .alert("Mobile connect", isPresented: Binding(
            get: { pairingLinkMessage != nil },
            set: { if !$0 { pairingLinkMessage = nil } }
        )) {
            Button("OK") { pairingLinkMessage = nil }
        } message: {
            Text(pairingLinkMessage ?? "")
        }
        .sheet(isPresented: $tvPairingPresented, onDismiss: { pendingTVHandoff = nil }) {
            if let handoff = pendingTVHandoff {
                NavigationStack {
                    ProgressView("Connecting to Apple TV…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(LuminaTheme.paper)
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) {
                                Button("Cancel") { tvPairingPresented = false }
                            }
                        }
                }
                .presentationDetents([.medium])
                .task(id: isRestoring ? nil : handoff) {
                    if !isRestoring { await finishTVHandoff(handoff) }
                }
            } else if let request = pendingTVApproval {
                TVPairingApprovalView(store: store, request: request)
            } else {
                TVPairingEntryView(onSelect: { pendingTVApproval = $0 }, onHandoff: beginTVHandoff)
            }
        }
    }

    private func beginTVHandoff(_ handoff: TVPairingHandoff) {
        guard store.session != nil || isRestoring else {
            pairingLinkMessage = "Sign in to Lumina on your phone, then scan the TV code again."
            return
        }
        pendingTVApproval = nil
        pendingTVHandoff = handoff
        tvPairingPresented = true
    }

    private func finishTVHandoff(_ handoff: TVPairingHandoff) async {
        do {
            guard let session = store.session else { throw LuminaError.malformedResponse }
            let approval = try await handoff.requestApproval(serverURL: session.serverURL)
            guard !Task.isCancelled, pendingTVHandoff == handoff,
                  store.session?.accessToken == session.accessToken,
                  store.session?.serverURL == session.serverURL else { return }
            pendingTVApproval = TVApprovalRequest(code: approval.code, expectedServerID: approval.serverID)
            pendingTVHandoff = nil
        } catch {
            guard !Task.isCancelled, pendingTVHandoff == handoff else { return }
            pendingTVHandoff = nil
            tvPairingPresented = false
            pairingLinkMessage = "Couldn't reach Apple TV. Make sure both devices are on the same network and Lumina has Local Network access, then scan again."
        }
    }

    private var effectiveAppearance: String {
        deviceAppearance == "account" ? appearance : deviceAppearance
    }

    private func refreshAfterForegrounding() {
        guard needsForegroundRefresh, foregroundRefreshTask == nil,
              let owner = MobileHomeSessionScope(store.session) else { return }
        let id = UUID()
        needsForegroundRefresh = false
        #if LUMINA_UI_TESTING
        foregroundRefreshStarts += 1
        #endif
        foregroundRefreshID = id
        foregroundRefreshTask = Task {
            defer {
                if foregroundRefreshID == id {
                    foregroundRefreshTask = nil
                    foregroundRefreshID = nil
                }
            }
            await store.refreshConnectivity()
            guard ownsForegroundRefresh(id, owner: owner) else { return }
            if !store.isOffline {
                await store.metadata.reloadPermissions()
                await store.refreshNavigationCategories()
                guard ownsForegroundRefresh(id, owner: owner) else { return }
            }
            await audioSession.refreshSharedQueue()
            guard ownsForegroundRefresh(id, owner: owner), !store.isOffline else { return }
            await userSettings.load()
            guard ownsForegroundRefresh(id, owner: owner) else { return }
            if userSettings.hasLoaded { appearance = userSettings.theme }
        }
    }

    private func cancelForegroundRefresh() {
        foregroundRefreshTask?.cancel()
        foregroundRefreshTask = nil
        foregroundRefreshID = nil
    }

    private func ownsForegroundRefresh(_ id: UUID, owner: MobileHomeSessionScope) -> Bool {
        !Task.isCancelled && foregroundRefreshID == id && MobileHomeSessionScope(store.session) == owner
    }

}
