import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

private extension EnvironmentValues {
    @Entry var returnToLuminaHome: () -> Void = {}
}

@MainActor
struct MobileRootView: View {
    @Bindable var store: LuminaStore
    let isRestoring: Bool
    @State private var audioSession = NativeAudioSession()
    @State private var videoSession = NativeVideoSession()
    @State private var userSettings = LuminaUserSettings()
    @State private var hasShownOnlineShell = false
    @AppStorage("lumina.appearance") private var appearance = "dark"
    @AppStorage("native.appearance") private var deviceAppearance = "account"
    @Environment(\.scenePhase) private var scenePhase
    @State private var incomingPairingLink: URL?
    @State private var pairingLinkMessage: String?

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
                        LibraryShellView(store: store)
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
        .environment(videoSession)
        .environment(audioSession)
        .environment(userSettings)
        .preferredColorScheme(effectiveAppearance == "system" ? nil : effectiveAppearance == "light" ? .light : .dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil { audioSession.stop(); videoSession.dismiss() }
        }
        .onChange(of: store.isOffline) { _, _ in audioSession.connectivityDidChange() }
        .task { store.startConnectivityMonitoring() }
        .task(id: MobileHomeSessionScope(store.session)) {
            userSettings.configure(session: store.session)
            if store.session != nil {
                if !store.isOffline {
                    await userSettings.load()
                    if userSettings.hasLoaded { appearance = userSettings.theme }
                }
                await audioSession.restore(store: store)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                audioSession.resumeAfterActivation()
                Task {
                    await store.refreshConnectivity()
                    await audioSession.refreshSharedQueue()
                    if store.session != nil, !store.isOffline {
                        await userSettings.load()
                        if userSettings.hasLoaded { appearance = userSettings.theme }
                    }
                }
            } else if phase == .background {
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .onOpenURL { link in
            guard store.session == nil else {
                pairingLinkMessage = "Sign out before connecting another server."
                return
            }
            incomingPairingLink = link
        }
        .onChange(of: isRestoring) { _, restoring in
            guard !restoring, incomingPairingLink != nil, store.session != nil else { return }
            incomingPairingLink = nil
            pairingLinkMessage = "Sign out before connecting another server."
        }
        .alert("Mobile connect", isPresented: Binding(
            get: { pairingLinkMessage != nil },
            set: { if !$0 { pairingLinkMessage = nil } }
        )) {
            Button("OK") { pairingLinkMessage = nil }
        } message: {
            Text(pairingLinkMessage ?? "")
        }
    }

    private var effectiveAppearance: String {
        deviceAppearance == "account" ? appearance : deviceAppearance
    }

}

@MainActor
struct NativeOfflineLibraryView: View {
    @Bindable var store: LuminaStore
    var usesPushedChrome = true
    @State private var query = ""
    @State private var searchVisible = false
    @State private var playbackRequest: PlaybackRequest?
    @State private var preparingID: String?
    @State private var playbackError: String?

    private var savedItems: [MediaItem] {
        store.offline.downloadedItems.filter {
            query.isEmpty || ($0.name + " " + ($0.seriesName ?? "") + " " + ($0.albumArtist ?? ""))
                .localizedStandardContains(query)
        }
    }

    var body: some View {
        navigationChrome {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                HStack {
                    Label(store.isOffline ? "Offline" : "Downloaded", glyph: store.isOffline ? .wifiOff : .download)
                        .font(.subheadline).foregroundStyle(LuminaTheme.muted)
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount: store.offline.usedBytes, countStyle: .file))
                        .font(.caption.monospacedDigit()).foregroundStyle(LuminaTheme.muted)
                }
                if searchVisible {
                    HStack {
                        TextField("Search downloads", text: $query).submitLabel(.search)
                        Button("Cancel") { query = ""; searchVisible = false }
                    }
                    .padding(.bottom, 10)
                    .overlay(alignment: .bottom) { Rectangle().fill(LuminaTheme.accentInk).frame(height: 1) }
                }
                if savedItems.isEmpty {
                    ContentUnavailableView {
                        Label(store.offline.downloadedItems.isEmpty ? "No downloads yet" : "No matching downloads", glyph: .download, size: 36)
                    } description: {
                        Text(store.offline.downloadedItems.isEmpty
                             ? "Download movies, episodes, or music while connected to watch and listen here offline."
                             : "Try a different search.")
                    }
                    .accessibilityIdentifier("offlineEmptyState")
                }
                ForEach(savedItems) { item in
                    HStack(spacing: 16) {
                        Button { play(item) } label: {
                            HStack(spacing: 16) {
                                ArtworkView(url: store.offline.artworkURL(for: item.id), name: item.name,
                                            aspect: item.isAudio ? 1 : 2 / 3, glyph: item.isAudio ? .music : .movies)
                                    .frame(width: 70, height: item.isAudio ? 70 : 105)
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(item.name).font(.headline).lineLimit(2)
                                    if !item.displaySubtitle.isEmpty {
                                        Text(item.displaySubtitle).font(.subheadline).foregroundStyle(LuminaTheme.muted).lineLimit(2)
                                    }
                                    Label("On this device", glyph: .downloadDone, size: 16)
                                        .font(.caption).foregroundStyle(LuminaTheme.muted)
                                }
                                Spacer(minLength: 0)
                                if preparingID == item.id { ProgressView() }
                                else { LuminaIcon(glyph: .play, variant: .solid, size: 24) }
                            }
                            .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                        .disabled(preparingID != nil)
                        .accessibilityLabel("Play \(item.name) offline")
                        .accessibilityIdentifier("offlineItem-\(item.id)")
                        Menu {
                            Button("Remove Download", glyph: .trash, role: .destructive) {
                                store.offline.remove(id: item.id)
                            }
                        } label: { Label("Download actions", glyph: .moreHorizontal).labelStyle(.iconOnly) }
                    }
                    Divider()
                }
                if !store.isOffline, !store.offline.pendingItems.isEmpty {
                    Text("Downloads").font(.title3.bold())
                    ForEach(store.offline.pendingItems) { item in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(item.name).font(.headline)
                            switch store.offline.downloadStates[item.id] {
                            case .queued:
                                Text("Queued").font(.caption)
                                Button("Cancel Download", glyph: .close) { store.offline.cancel(id: item.id) }
                            case .downloading(let received, let total):
                                if let total, total > 0 { ProgressView(value: Double(received), total: Double(total)) }
                                Text(ByteCountFormatter.string(fromByteCount: received, countStyle: .file)).font(.caption)
                                Button("Cancel Download", glyph: .close) { store.offline.cancel(id: item.id) }
                            case .failed(let message):
                                Text(message).font(.caption).foregroundStyle(LuminaTheme.muted)
                                retryDownload(item)
                            case .cancelled:
                                retryDownload(item)
                            default:
                                EmptyView()
                            }
                        }
                    }
                }
                if let playbackError {
                    Label(playbackError, glyph: .warning).foregroundStyle(LuminaTheme.muted)
                }
                if let error = store.offline.errorMessage {
                    Label(error, glyph: .warning).foregroundStyle(LuminaTheme.muted)
                }
            }
            .padding(24)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper.ignoresSafeArea())
        .navigationTitle("On this device")
        .tint(LuminaTheme.ink)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                offlineHeaderActions
            }
            NativeNowPlayingToolbar(store: store)
        }
        .task { store.offline.refresh() }
        .refreshable { store.offline.refresh(); await store.refreshConnectivity() }
        .nativePlayback(request: $playbackRequest, store: store) { store.offline.refresh() }
        .accessibilityIdentifier("offlineLibraryScreen")
        }
    }

    @ViewBuilder private func navigationChrome<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        if usesPushedChrome {
            content()
                .standardPhonePushedChrome(store: store) { offlineHeaderActions }
        } else {
            content()
        }
    }

    @ViewBuilder private var offlineHeaderActions: some View {
        Button("Search", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("offlineSearchButton")
        if store.isOffline {
            Button("Reconnect", glyph: .sync) { Task { await store.refreshConnectivity() } }
                .accessibilityIdentifier("offlineReconnectButton")
        }
    }

    @ViewBuilder private func retryDownload(_ item: MediaItem) -> some View {
        if item.isAudio {
            NativeDownloadControls(items: [item], store: store, title: "Retry Download")
        } else {
            Button("Retry Download", glyph: .download) { Task { await store.downloadVideo(item: item) } }
        }
    }

    private func play(_ item: MediaItem) {
        guard preparingID == nil else { return }
        preparingID = item.id
        playbackError = nil
        Task {
            defer { preparingID = nil }
            do { playbackRequest = try await store.preparePlayback(for: item) }
            catch { playbackError = "This download is no longer available. Remove it and download it again when connected." }
        }
    }
}

@MainActor
private struct ConnectionView: View {
    @Bindable var store: LuminaStore
    let incomingPairingLink: URL?
    let onPairingLinkHandled: () -> Void

    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var scannerAvailable = false
    @State private var scannerCheckFinished = false
    @State private var showScanner = false
    @State private var showManual = false
    @State private var manualRequested = false
    @State private var claimingPair = false
    @FocusState private var focusedField: Field?

    private enum Field { case server, username, password }

    private var canConnect: Bool {
        !server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !password.isEmpty
    }

    @State private var helpPresented = false
    @State private var artworkIndex = 0
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private let artwork = ["WelcomeCoast", "WelcomeOrbit", "WelcomeDunes"]

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                connectionForm
                    .frame(maxWidth: 420)
                    .padding(.horizontal, geometry.size.width > 700 ? 64 : 28)
                    .padding(.vertical, 32)
                    .frame(maxWidth: .infinity, minHeight: geometry.size.height,
                           alignment: geometry.size.width > 700 ? .leading : .bottom)
            }
            .scrollDismissesKeyboard(.interactively)
            .background {
                GeometryReader { artworkGeometry in
                    ZStack {
                        ForEach(artwork, id: \.self) { name in
                            Image(name)
                                .resizable()
                                .scaledToFill()
                                .frame(width: artworkGeometry.size.width, height: artworkGeometry.size.height, alignment: .trailing)
                                .clipped()
                                .opacity(artwork[artworkIndex] == name ? 1 : 0)
                        }
                        LinearGradient(colors: [.black.opacity(0.15), .black.opacity(0.65), .black.opacity(0.96)],
                                       startPoint: .top, endPoint: .bottom)
                        if geometry.size.width > 700 {
                            LinearGradient(colors: [.black.opacity(0.88), .clear],
                                           startPoint: .leading, endPoint: .trailing)
                        }
                    }
                }
                .ignoresSafeArea()
                .accessibilityHidden(true)
            }
        }
        .background(.black)
        .foregroundStyle(.white)
        .preferredColorScheme(.dark)
        .tint(.white)
        .task(id: scenePhase == .active && !reduceMotion && !voiceOver && focusedField == nil) {
            guard scenePhase == .active, !reduceMotion, !voiceOver, focusedField == nil else { return }
            do {
                while !Task.isCancelled {
                    try await Task.sleep(for: .seconds(9))
                    withAnimation(.easeInOut(duration: 1.5)) { artworkIndex = (artworkIndex + 1) % artwork.count }
                }
            } catch { }
        }
        .task {
            if let incomingPairingLink {
                onPairingLinkHandled()
                claim(link: incomingPairingLink)
            } else {
                await prepareScanner()
            }
        }
        .onChange(of: incomingPairingLink) { _, link in
            guard let link else { return }
            onPairingLinkHandled()
            claim(link: link)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active {
                showScanner = false
            } else if !manualRequested, !claimingPair {
                Task { await prepareScanner() }
            }
        }
        .onChange(of: helpPresented) { _, presented in
            if presented {
                showScanner = false
            } else if !manualRequested, !claimingPair {
                Task { await prepareScanner() }
            }
        }
        .sheet(isPresented: $helpPresented) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("QR setup").font(.headline)
                            Text("Web app → Member menu → Mobile connect → Show QR code")
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Sign in manually").font(.headline)
                            LabeledContent("Server address", value: "From your administrator")
                            LabeledContent("Username", value: "Household account")
                            LabeledContent("2FA", value: "App password")
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Server setting").font(.headline)
                            Text("Manage Server → Connections → Metadata → Apps like Infuse")
                            Text("Enable Jellyfin-compatible API")
                                .fontWeight(.semibold)
                        }
                    }
                    .padding(28)
                }
                .navigationTitle("Connection help")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done", glyph: .close) { helpPresented = false }
                            .labelStyle(.iconOnly)
                            .accessibilityIdentifier("connectionHelpDoneButton")
                    }
                }
                .accessibilityIdentifier("connectionHelpSheet")
            }
            .tint(.white)
            .preferredColorScheme(.dark)
        }
    }

    private var connectionForm: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                LuminaBrandMark()
                    .frame(width: 40, height: 25)
                    .luminaBrandMarkBaseline()
                Text("LUMINA")
                    .font(LuminaTheme.editorial(28, relativeTo: .title2))
                    .tracking(2.4)
            }
            Text(showManual ? "Sign in" : "Connect")
                .font(.title2.weight(.semibold))

            connectionMethod
            if let message = store.errorMessage, !message.isEmpty {
                Label(message, glyph: .warning)
                    .font(.callout)
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isStaticText)
                    .accessibilityIdentifier("connectionError")
            }
        }
    }

    @ViewBuilder private var connectionMethod: some View {
        if claimingPair {
            VStack(alignment: .leading, spacing: 14) {
                ProgressView("Connecting…")
                    .frame(maxWidth: .infinity, minHeight: 280)
                connectionHelpAction
            }
        } else if showManual {
            manualConnection
        } else if showScanner {
            VStack(alignment: .leading, spacing: 14) {
                pairingSetupPath
                MobilePairScanner(onCode: claim(code:), onUnavailable: scannerUnavailable)
                    .frame(height: 280)
                    .clipShape(.rect(cornerRadius: 16))
                    .accessibilityLabel("QR code scanner")
                secondaryConnectionActions
            }
        } else {
            qrPairingLanding
        }
    }

    private var qrPairingLanding: some View {
        VStack(alignment: .leading, spacing: 14) {
            pairingSetupPath
            if !scannerCheckFinished {
                ProgressView("Checking camera…")
            } else if !scannerAvailable {
                Text("Camera unavailable")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.74))
                    .accessibilityIdentifier("connectionCameraStatus")
            }
            Button {
                Task { await prepareScanner(requestPermission: true) }
            } label: {
                Text("Scan QR code").frame(maxWidth: .infinity)
            }
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .disabled(!scannerAvailable)
                .accessibilityIdentifier("scanPairingCodeButton")
            secondaryConnectionActions
        }
    }

    private var pairingSetupPath: some View {
        Text("Web app → Member menu → Mobile connect → Show QR code")
            .font(.footnote.weight(.medium))
            .foregroundStyle(.white.opacity(0.82))
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("qrPairingLanding")
    }

    @ViewBuilder private var secondaryConnectionActions: some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                manualSignInAction
                connectionHelpAction
            }
        } else {
            HStack(spacing: 8) {
                manualSignInAction.frame(maxWidth: .infinity)
                connectionHelpAction.frame(maxWidth: .infinity)
            }
        }
    }

    private var manualSignInAction: some View {
        Button("Sign in manually") {
            showScanner = false
            showManual = true
            manualRequested = true
        }
        .buttonStyle(LuminaQuietActionStyle())
        .accessibilityIdentifier("advancedConnectionButton")
    }

    private var connectionHelpAction: some View {
        Button("Help") {
            focusedField = nil
            helpPresented = true
        }
        .buttonStyle(LuminaQuietActionStyle())
        .accessibilityIdentifier("connectionHelpButton")
    }

    private var manualConnection: some View {
        VStack(spacing: 8) {
            ConnectionField(active: focusedField == .server) {
                TextField("Server address", text: $server, prompt: Text("Server address").foregroundStyle(.white.opacity(0.7)))
                    .keyboardType(.URL)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .server)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .username }
                    .accessibilityLabel("Server URL")
                    .accessibilityIdentifier("serverURLField")
            }
            ConnectionField(active: focusedField == .username) {
                TextField("Lumina username", text: $username, prompt: Text("Lumina username").foregroundStyle(.white.opacity(0.7)))
                    .textContentType(.username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .username)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .password }
                    .accessibilityLabel("Username")
                    .accessibilityIdentifier("usernameField")
            }
            ConnectionField(active: focusedField == .password) {
                SecureField("Password or app password", text: $password, prompt: Text("Password or app password").foregroundStyle(.white.opacity(0.7)))
                    .textContentType(.password)
                    .focused($focusedField, equals: .password)
                    .submitLabel(.go)
                    .onSubmit { connect() }
                    .accessibilityLabel("Password")
                    .accessibilityIdentifier("passwordField")
            }
            Button(action: connect) {
                HStack(spacing: 10) {
                    if store.isBusy { ProgressView() }
                    Text(store.isBusy ? "Connecting…" : "Connect")
                }
                .frame(maxWidth: .infinity, minHeight: 8)
            }
            .buttonStyle(LuminaQuietActionStyle(primary: true))
            .disabled(!canConnect || store.isBusy)
            .accessibilityIdentifier("connectButton")

            if scannerAvailable {
                Button("Scan code") {
                    manualRequested = false
                    showManual = false
                    Task { await prepareScanner(requestPermission: true) }
                }
                .buttonStyle(LuminaQuietActionStyle())
                .accessibilityIdentifier("scanPairingCodeButton")
            }

            HStack {
                connectionHelpAction
                Spacer(minLength: 0)
            }
        }
    }

    private func connect() {
        let server = server.trimmingCharacters(in: .whitespacesAndNewlines)
        let username = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !server.isEmpty, !username.isEmpty, !password.isEmpty, !store.isBusy else { return }

        focusedField = nil
        store.clearError()
        Task {
            await store.connect(server: server, username: username, password: password)
            password = ""
        }
    }

    private func prepareScanner(requestPermission: Bool = false) async {
        guard !manualRequested, !claimingPair, !helpPresented, scenePhase == .active else { return }
        guard DataScannerViewController.isSupported else {
            scannerAvailable = false
            scannerCheckFinished = true
            showScanner = false
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            break
        case .notDetermined:
            guard requestPermission else {
                scannerAvailable = true
                scannerCheckFinished = true
                showScanner = false
                return
            }
            guard await AVCaptureDevice.requestAccess(for: .video) else {
                scannerAvailable = false
                scannerCheckFinished = true
                showScanner = false
                return
            }
        default:
            scannerAvailable = false
            scannerCheckFinished = true
            showScanner = false
            return
        }
        guard !manualRequested, !claimingPair, !helpPresented, scenePhase == .active else { return }
        guard DataScannerViewController.isAvailable else {
            scannerAvailable = false
            scannerCheckFinished = true
            showScanner = false
            return
        }
        scannerAvailable = true
        scannerCheckFinished = true
        showManual = false
        showScanner = true
    }

    private func scannerUnavailable() {
        scannerAvailable = false
        scannerCheckFinished = true
        showScanner = false
    }

    private func claim(code: String) {
        guard !claimingPair else { return }
        claimingPair = true
        showScanner = false
        Task {
            await store.connect(pairingCode: code, appName: pairingAppName)
            claimingPair = false
        }
    }

    private func claim(link: URL) {
        guard !claimingPair else { return }
        claimingPair = true
        showScanner = false
        Task {
            await store.connect(pairingLink: link, appName: pairingAppName)
            claimingPair = false
            if store.session == nil, !manualRequested { await prepareScanner() }
        }
    }

    private var pairingAppName: String {
        "Lumina for \(UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone")"
    }
}

@MainActor
private struct ConnectionField<Content: View>: View {
    var active = false
    @ViewBuilder let content: Content

    var body: some View {
        content
            .font(.body)
            .padding(.vertical, 12)
            .frame(minHeight: 50)
            .overlay(alignment: .bottom) {
                Rectangle().fill(active ? LuminaTheme.brand : .white.opacity(0.35))
                    .frame(height: active ? 2 : 1)
            }
    }
}

private enum LibraryDestination: Hashable, CaseIterable {
    case home, library, requests, account
}

private enum LibraryCategory: String, CaseIterable {
    case movies, tv, anime, music, books

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "TV"
        case .anime: "Anime"
        case .music: "Music"
        case .books: "Books"
        }
    }

    var videoKind: VideoBrowseKind {
        switch self {
        case .tv: .shows
        case .anime: .anime
        default: .movies
        }
    }
}

@MainActor
private struct LibraryShellView: View {
    @Bindable var store: LuminaStore
    @State private var destination: LibraryDestination = .home
    @State private var homeNavigationID = UUID()
    #if LUMINA_UI_TESTING
    @State private var homeNavigationGeneration = 0
    @State private var uiTestingHomeNavigationDiagnostics = "route=root generation=0"
    #endif
    @State private var libraryPath = [MediaItem]()
    @State private var homeCategory: HomeBrowseCategory = .all
    @State private var category: LibraryCategory = .movies
    @State private var homeLayout = LuminaHomeLayout()
    @State private var homeFeed = LuminaHomeFeed(includesExternalSources: true)
    @State private var homeSessionScope: MobileHomeSessionScope?

    var body: some View {
        // Keep the system TabView alive for selection and navigation state.
        // iPad uses one unified category row instead of its large top tab pill;
        // iPhone and Duo retain the system-managed tab placement.
        GeometryReader { geometry in
            let displayWidth = geometry.size.width + geometry.safeAreaInsets.leading + geometry.safeAreaInsets.trailing
            let displayHeight = geometry.size.height + geometry.safeAreaInsets.top + geometry.safeAreaInsets.bottom
            // Standard phones use their bottom tabs; Duo's larger display keeps its category header.
            let showsCategoryHeader = usesIPadHeaderNavigation || min(displayWidth, displayHeight) >= 450
            let usesClosedCategoryRow = !usesIPadHeaderNavigation && showsCategoryHeader && displayWidth < 600
            // The system has already removed its camera/status region from this width.
            let closedHeaderInsets = LuminaTheme.contentGutter + 44 + 12 + 12 // Logo and its two gaps.
            let categoryBarWidth = min(560, max(100, geometry.size.width - (usesClosedCategoryRow ? closedHeaderInsets : 180)))
            destinations(categoryBarWidth: categoryBarWidth,
                         protectedHeaderCategoryWidth: headerCategoryWidth(for: geometry.size.width),
                         showsCategoryHeader: showsCategoryHeader,
                         usesClosedCategoryRow: usesClosedCategoryRow,
                         stacksCategoryHeader: !usesIPadHeaderNavigation && displayWidth >= 600 && displayWidth < displayHeight)
            #if LUMINA_UI_TESTING
            .overlay {
                Color.clear.accessibilityElement()
                    .accessibilityIdentifier("navigationOcclusions")
                    .accessibilityValue(navigationOcclusions(in: geometry))
                    .allowsHitTesting(false)
            }
            #endif
        }
        .task(id: MobileHomeSessionScope(store.session)) {
            let scope = MobileHomeSessionScope(store.session)
            homeLayout.configure(session: store.session)
            homeFeed.configure(session: store.session)
            homeSessionScope = scope
        }
    }

    #if LUMINA_UI_TESTING
    private func navigationOcclusions(in geometry: GeometryProxy) -> String {
        guard #available(iOS 27.1, *) else { return "" }
        let origin = geometry.frame(in: .global).origin
        return geometry.reservedRegions(kind: .occlusion, layoutDirectionBehavior: .fixed)
            .filter(\.isActive).map { region in
                let frame = region.frame.offsetBy(dx: origin.x, dy: origin.y)
                return "\(frame.minX),\(frame.minY),\(frame.width),\(frame.height)"
            }.joined(separator: ";")
    }
    #endif

    private func destinations(categoryBarWidth: CGFloat, protectedHeaderCategoryWidth: CGFloat,
                              showsCategoryHeader: Bool, usesClosedCategoryRow: Bool, stacksCategoryHeader: Bool) -> some View {
        TabView(selection: Binding(get: { destination }, set: { selected in
            if selected == .home {
                homeCategory = .all
                resetHomeNavigation()
            } else if selected == .library, !libraryPath.isEmpty {
                var transaction = Transaction(animation: nil)
                transaction.disablesAnimations = true
                withTransaction(transaction) { libraryPath.removeAll() }
            }
            destination = selected
        })) {
            Tab("Home", image: LuminaGlyph.home.assetName(for: destination == .home ? .solid : .outline), value: .home) {
                destinationStack(owner: .home, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                 usesPortraitRail: stacksCategoryHeader) {
                    if homeSessionScope == MobileHomeSessionScope(store.session) {
                        HomeLibraryView(store: store, homeLayout: homeLayout, feed: homeFeed,
                                        category: $homeCategory, categoryBarWidth: categoryBarWidth,
                                        protectedHeaderCategoryWidth: protectedHeaderCategoryWidth,
                                        stacksCategoryHeader: stacksCategoryHeader,
                                        usesClosedCategoryRow: usesClosedCategoryRow,
                                        showsBrowsingRow: showsCategoryHeader && !usesIPadHeaderNavigation,
                                        uiTestingNavigationDiagnostics: homeNavigationDiagnosticValue
                        ) { selected in
                            openLibrary(selected, fromHome: true)
                        }
                    } else {
                        ProgressView("Loading Home…")
                    }
                }
                .id(homeNavigationID)
                #if LUMINA_UI_TESTING
                .onAppear { recordHomeNavigationDiagnostic("root") }
                #endif
            }
            Tab("Library", image: LuminaGlyph.library.assetName(for: destination == .library ? .solid : .outline), value: .library) {
                destinationStack(owner: .library, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                 usesPortraitRail: stacksCategoryHeader, path: $libraryPath) {
                    libraryContent(usesPortraitRail: stacksCategoryHeader)
                        .environment(\.luminaRetainsArtworkWhileHidden, !libraryPath.isEmpty)
                        .safeAreaInset(edge: .top, spacing: 0) {
                            if stacksCategoryHeader {
                                PortraitNavigationHeader {
                                    libraryCategories(centersContent: false)
                                }
                            }
                        }
                        .ignoresSafeArea(.container, edges: stacksCategoryHeader ? .top : [])
                        .modifier(LibraryStableNavigationBar())
                        .toolbarVisibility(stacksCategoryHeader ? .hidden : .visible, for: .navigationBar)
                        .toolbarBackground(.hidden, for: .navigationBar)
                        .toolbar {
                            if showsCategoryHeader && !usesIPadHeaderNavigation && !stacksCategoryHeader && usesClosedCategoryRow {
                                let header = HStack(spacing: 12) {
                                    HomeHeroChrome().fixedSize()
                                    libraryCategories(centersContent: false)
                                        .frame(width: categoryBarWidth).clipped()
                                }
                                if #available(iOS 26.0, *) {
                                    ToolbarItem(placement: .topBarLeading) { header }
                                        .sharedBackgroundVisibility(.hidden)
                                } else {
                                    ToolbarItem(placement: .topBarLeading) { header }
                                }
                            } else if showsCategoryHeader && !usesIPadHeaderNavigation && !stacksCategoryHeader {
                                ToolbarItem(placement: .principal) {
                                    libraryCategories()
                                        .frame(width: protectedHeaderCategoryWidth)
                                        .clipped()
                                }
                            }
                            if !stacksCategoryHeader && !usesClosedCategoryRow {
                                if #available(iOS 26.0, *) {
                                    ToolbarItem(placement: .topBarLeading) {
                                        HomeHeroChrome().fixedSize()
                                    }
                                    .sharedBackgroundVisibility(.hidden)
                                } else {
                                    ToolbarItem(placement: .topBarLeading) {
                                        HomeHeroChrome().fixedSize()
                                    }
                                }
                            }
                        }
                }
            }
            Tab("Requests", image: LuminaGlyph.requests.assetName(for: destination == .requests ? .solid : .outline), value: .requests) {
                destinationStack(owner: .requests, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader) {
                    if let session = store.session {
                        NativeRequestsView(store: store, session: session, isActive: destination == .requests)
                            .id([session.serverURL.absoluteString, session.userID, session.accessToken])
                    }
                }
            }
            Tab("Account", image: LuminaGlyph.userSettings.assetName(for: destination == .account ? .solid : .outline), value: .account) {
                destinationStack(owner: .account, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader) {
                    NativeAccountSettingsView(store: store, onOpenRequests: usesIPadHeaderNavigation ? {
                        destination = .requests
                    } : nil)
                }
            }
        }
        .environment(\.returnToLuminaHome, {
            homeCategory = .all
            resetHomeNavigation() // Pop Home details, clear its refinement, and return to the top.
            destination = .home
        })
        .onChange(of: store.availableLibraryCategories, initial: true) { _, available in
            guard !available.contains(category.rawValue) else { return }
            if let first = LibraryCategory.allCases.first(where: { available.contains($0.rawValue) }) {
                category = first
            } else if destination == .library {
                destination = .home
            }
        }
        .tint(LuminaTheme.ink)
    }

    /// Principal toolbar content is centered independently of its side controls.
    /// Reserve the larger side on both edges so scrolling labels stay clear.
    private func headerCategoryWidth(for width: CGFloat) -> CGFloat {
        let masthead = 44.0
        let trailingControls = 3 * 44.0
        let sideInset = max(masthead, trailingControls) + 40 + LuminaTheme.contentGutter
        return min(560, max(100, width - 2 * sideInset))
    }

    @ViewBuilder private func libraryContent(usesPortraitRail: Bool) -> some View {
        if store.availableLibraryCategories.contains(category.rawValue) {
            switch category {
            case .movies, .tv, .anime:
                VideoLibraryView(store: store, kind: .constant(category.videoKind),
                                 activeKind: destination == .library ? category.videoKind : nil,
                                 usesPortraitNavigationRail: usesPortraitRail)
                    .id(category)
            case .music:
                AudioLibraryView(store: store, usesPortraitNavigationRail: usesPortraitRail)
            case .books:
                if let session = store.session {
                    NativeBooksView(store: store, session: session, isActive: destination == .library,
                                    usesPortraitNavigationRail: usesPortraitRail)
                        .id([session.serverURL.absoluteString, session.userID, session.accessToken])
                }
            }
        } else {
            ContentUnavailableView("No libraries available", systemImage: "books.vertical")
        }
    }

    private func libraryCategories(centersContent: Bool = true) -> some View {
        LuminaCategoryBar(selection: category.rawValue, centersContent: centersContent) {
            ForEach(LibraryCategory.allCases.filter { store.availableLibraryCategories.contains($0.rawValue) }, id: \.self) { value in
                Button {
                    category = value
                } label: {
                    Text(value.title)
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: category == value, alignsWithMasthead: true))
                .accessibilityValue(category == value ? "Selected" : "")
                .accessibilityAddTraits(category == value ? .isSelected : [])
                .accessibilityIdentifier("libraryCategory-\(value.rawValue)")
                .id(value.rawValue)
            }
        }
        .accessibilityIdentifier("libraryCategories")
    }

    private var unifiedCategorySelection: String? {
        switch destination {
        case .home: homeCategory == .watchlist ? HomeBrowsingDestination.watchlist.rawValue : HomeBrowsingDestination.all.rawValue
        case .library: category.rawValue
        case .account: "account"
        case .requests: "account"
        }
    }

    private var unifiedCategories: some View {
        LuminaCategoryBar(selection: unifiedCategorySelection) {
            unifiedCategoryButton("Home", id: HomeBrowsingDestination.all.rawValue,
                                  selected: destination == .home && homeCategory == .all) {
                homeCategory = .all
                resetHomeNavigation()
                destination = .home
            }
            ForEach(LibraryCategory.allCases.filter { store.availableLibraryCategories.contains($0.rawValue) }, id: \.self) { value in
                unifiedCategoryButton(value.title, id: value.rawValue,
                                      selected: destination == .library && category == value) {
                    openLibrary(value, fromHome: destination == .home)
                }
            }
            unifiedCategoryButton("Watchlist", id: HomeBrowsingDestination.watchlist.rawValue,
                                  selected: destination == .home && homeCategory == .watchlist) {
                homeCategory = .watchlist
                resetHomeNavigation()
                destination = .home
            }
            unifiedCategoryButton("Account", id: "account",
                                  selected: destination == .account || destination == .requests) {
                destination = .account
            }
        }
        .accessibilityIdentifier("iPadUnifiedCategories")
    }

    private func unifiedCategoryButton(_ title: String, id: String, selected: Bool,
                                       action: @escaping () -> Void) -> some View {
        Button(action: action) { Text(title) }
            .buttonStyle(LuminaUnderlineTabStyle(selected: selected, alignsWithMasthead: true))
            .accessibilityValue(selected ? "Selected" : "")
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier("iPadCategory-\(id)")
            .id(id)
    }

    @ViewBuilder
    private func destinationStack<Content: View>(owner: LibraryDestination, categoryBarWidth: CGFloat, showsCategoryHeader: Bool,
                                                  usesPortraitRail: Bool = false,
                                                  path: Binding<[MediaItem]>? = nil,
                                                  @ViewBuilder content: () -> Content) -> some View {
        if let path {
            NavigationStack(path: path) {
                destinationStackContent(owner: owner, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                        usesPortraitRail: usesPortraitRail, content: content)
            }
        } else {
            NavigationStack {
                destinationStackContent(owner: owner, categoryBarWidth: categoryBarWidth, showsCategoryHeader: showsCategoryHeader,
                                        usesPortraitRail: usesPortraitRail, content: content)
            }
        }
    }

    @ViewBuilder
    private func destinationStackContent<Content: View>(owner: LibraryDestination, categoryBarWidth: CGFloat, showsCategoryHeader: Bool,
                                                         usesPortraitRail: Bool,
                                                         @ViewBuilder content: () -> Content) -> some View {
            content()
                .toolbar {
                    if usesIPadHeaderNavigation, owner == destination {
                        ToolbarItem(placement: .principal) {
                            unifiedCategories.frame(width: categoryBarWidth)
                        }
                    }
                    if !showsCategoryHeader, owner == destination, (owner == .home || owner == .library) {
                        ToolbarItem(placement: .topBarTrailing) {
                            Menu {
                                ForEach(LibraryCategory.allCases.filter { store.availableLibraryCategories.contains($0.rawValue) }, id: \.self) { value in
                                    Button(value.title) {
                                        openLibrary(value, fromHome: destination == .home)
                                    }
                                    .accessibilityIdentifier("compactCategory-\(value.rawValue)")
                                }
                                Button("Watchlist") {
                                    homeCategory = .watchlist
                                    resetHomeNavigation()
                                    destination = .home
                                }
                                .accessibilityIdentifier("compactCategory-watchlist")
                                if destination == .home, !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                                    NavigationLink {
                                        NativeOfflineLibraryView(store: store)
                                    } label: { Label("Downloads", glyph: .download) }
                                    .accessibilityIdentifier("homeDownloadsButton")
                                }
                            } label: {
                                Label(owner == .library ? "Libraries" : "Browse",
                                      glyph: owner == .library ? .library : .moreHorizontal)
                            }
                            .accessibilityIdentifier("compactBrowseMenu")
                        }
                    }
                    if owner == .home, destination == .home, !usesPortraitRail {
                        ToolbarItemGroup(placement: .topBarTrailing) {
                            if showsCategoryHeader, !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                                NavigationLink {
                                    NativeOfflineLibraryView(store: store)
                                } label: { Label("Downloads", glyph: .download) }
                                .accessibilityIdentifier("homeDownloadsButton")
                            }
                            NavigationLink {
                                HomeSearchView(store: store)
                                    .toolbar {
                                        NativeNowPlayingToolbar(store: store)
                                    }
                            } label: { Label("Search", glyph: .search) }
                            .accessibilityIdentifier("homeSearchButton")
                        }
                    }
                    if !usesPortraitRail { NativeNowPlayingToolbar(store: store) }
                }
                .navigationDestination(for: MediaItem.self) { item in
                    Group {
                        if item.type == "MusicAlbum" || item.type == "MusicArtist" {
                            MusicCollectionView(item: item, store: store)
                        } else if item.type == "Episode" {
                            EpisodeCollectionDestination(item: item, store: store)
                        } else if item.isCollection {
                            VideoCollectionView(item: item, store: store)
                        } else {
                            TitleDetailView(item: item, store: store)
                        }
                    }
                    .toolbarVisibility(.visible, for: .navigationBar)
                    .toolbar {
                        NativeNowPlayingToolbar(store: store)
                    }
                    #if LUMINA_UI_TESTING
                    .onAppear {
                        if owner == .home { recordHomeNavigationDiagnostic("destination-appeared") }
                    }
                    .onDisappear {
                        if owner == .home { recordHomeNavigationDiagnostic("destination-disappeared") }
                    }
                    #endif
                }
            .toolbarVisibility(usesIPadHeaderNavigation ? .hidden : .automatic, for: .tabBar)
            .tint(LuminaTheme.ink)
    }

    private var usesIPadHeaderNavigation: Bool {
        UIDevice.current.userInterfaceIdiom == .pad
    }

    private func resetHomeNavigation() {
        homeNavigationID = UUID()
        #if LUMINA_UI_TESTING
        homeNavigationGeneration += 1
        recordHomeNavigationDiagnostic("root")
        #endif
    }

    #if LUMINA_UI_TESTING
    private func recordHomeNavigationDiagnostic(_ route: String) {
        uiTestingHomeNavigationDiagnostics = "route=\(route) generation=\(homeNavigationGeneration)"
    }
    #endif

    private var homeNavigationDiagnosticValue: String? {
        #if LUMINA_UI_TESTING
        uiTestingHomeNavigationDiagnostics
        #else
        nil
        #endif
    }

    private func openLibrary(_ selected: LibraryCategory, fromHome: Bool) {
        guard store.availableLibraryCategories.contains(selected.rawValue) else { return }
        if fromHome {
            var transaction = Transaction(animation: nil)
            transaction.disablesAnimations = true
            withTransaction(transaction) { libraryPath.removeAll() }
        }
        category = selected
        destination = .library
    }
}

@MainActor
private struct EpisodeCollectionDestination: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var series: MediaItem?
    @State private var couldNotResolveSeries = false

    var body: some View {
        Group {
            if let series {
                VideoCollectionView(item: series, store: store, initialSeasonID: item.seasonID, initialEpisodeID: item.id)
            } else if couldNotResolveSeries {
                TitleDetailView(item: item, store: store)
            } else {
                ProgressView("Loading show…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityIdentifier("videoCollectionLoading")
            }
        }
        .task(id: item.id) {
            guard let seriesID = item.seriesID else {
                couldNotResolveSeries = true
                return
            }
            do {
                series = try await store.seriesCollection(id: seriesID)
            } catch {
                couldNotResolveSeries = true
            }
        }
    }
}

private struct MobileHomeSessionScope: Hashable {
    let serverURL: URL
    let userID: String
    let accessToken: String

    init?(_ session: LuminaSession?) {
        guard let session else { return nil }
        serverURL = session.serverURL
        userID = session.userID
        accessToken = session.accessToken
    }
}

@MainActor
private struct HomeLibraryView: View {
    let store: LuminaStore
    let homeLayout: LuminaHomeLayout
    let feed: LuminaHomeFeed
    @Binding var category: HomeBrowseCategory
    let categoryBarWidth: CGFloat
    let protectedHeaderCategoryWidth: CGFloat
    let stacksCategoryHeader: Bool
    let usesClosedCategoryRow: Bool
    let showsBrowsingRow: Bool
    let uiTestingNavigationDiagnostics: String?
    let onOpenLibrary: (LibraryCategory) -> Void
    @State private var completedInitialHomeLoad = false
    #if LUMINA_UI_TESTING
    @State private var uiTestingHomeRefreshCount = 0
    #endif
    @Environment(\.scenePhase) private var scenePhase
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var scrollPosition = ScrollPosition(edge: .top)
    @State private var hiddenRecommendationID: String?
    @State private var showRecommendationUndo = false

    private var refinement: HomeRefinementResult {
        feed.refined(HomeRefinement(category: category), heroItems: store.featuredItems)
    }

    private func refinedItems(_ items: [MediaItem]) -> [MediaItem] {
        feed.refined(HomeRefinement(category: category), heroItems: items).items
    }


    private var shelves: [HomeShelfPreference] {
        homeLayout.shelves
    }

    var body: some View {
        let snapshot = refinement
        GeometryReader { geometry in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if !snapshot.items.isEmpty {
                    LibraryFeaturedView(store: store, items: snapshot.items,
                        compactMinimumHeight: max(460, geometry.size.height * 0.78),
                        compactArtworkMinimumHeight: geometry.size.height + geometry.safeAreaInsets.top + geometry.safeAreaInsets.bottom,
                        wideMinimumHeight: min(720, max(540, geometry.size.height * 0.78)),
                        topContentClearance: geometry.safeAreaInsets.top + 32,
                        bottomContentClearance: geometry.safeAreaInsets.bottom,
                        onWatchlistChanged: { await feed.load() })
                        .padding(.horizontal, -20)
                }
                ForEach(shelves.filter(\.visible)) { shelf in
                    homeShelf(shelf.id, rows: snapshot.rows)
                }
                if category != .all, snapshot.items.isEmpty,
                   !snapshot.rows.contains(where: \.hasItems), feed.hasLoaded {
                    ContentUnavailableView {
                        Label("No matches on Home", glyph: .filter)
                    } description: {
                        Text("Choose Home or add something to your watchlist.")
                    }
                }
                if store.isHomeBusy || store.isBusy {
                    ProgressView("Loading your library…")
                } else if store.featuredItems.isEmpty && store.errorMessage == nil && !feed.rows.contains(where: \.hasItems) {
                    EmptyLibraryView { Task { await store.loadHome() } }
                }
                if let error = feed.errorMessage {
                    InlineErrorView(message: error) { Task { await feed.load() } }
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await store.loadHome() } }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 20)
            .frame(maxWidth: .infinity)
        }
        .ignoresSafeArea(edges: snapshot.items.isEmpty ? [] : .top)
        .modifier(HomeHeroScrollEdge(isEnabled: !snapshot.items.isEmpty))
        .scrollClipDisabled()
        .scrollPosition($scrollPosition)
        .onChange(of: category) { _, _ in
            scrollPosition.scrollTo(edge: .top)
        }
        .refreshable {
            #if LUMINA_UI_TESTING
            uiTestingHomeRefreshCount += 1
            #endif
            await store.loadHome(); await homeLayout.load(); await feed.load()
        }
        }

        .navigationBarTitleDisplayMode(.inline)
        .background(LuminaTheme.paper)
        .toolbarVisibility(.visible, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarColorScheme(snapshot.items.isEmpty ? nil : .dark, for: .navigationBar)
        .toolbar {
            if !stacksCategoryHeader {
                if showsBrowsingRow, usesClosedCategoryRow {
                    let header = HStack(spacing: 12) {
                        HomeHeroChrome().fixedSize()
                        browsingRow.frame(width: browsingHeaderWidth).clipped()
                    }
                    if #available(iOS 26.0, *) {
                        ToolbarItem(placement: .topBarLeading) { header }
                            .sharedBackgroundVisibility(.hidden)
                    } else {
                        ToolbarItem(placement: .topBarLeading) { header }
                    }
                } else {
                    if showsBrowsingRow, !stacksCategoryHeader {
                        ToolbarItem(placement: .principal) {
                            browsingRow.frame(width: browsingHeaderWidth).clipped()
                        }
                    }
                    if #available(iOS 26.0, *) {
                        ToolbarItem(placement: .topBarLeading) {
                            HomeHeroChrome().fixedSize()
                        }
                        .sharedBackgroundVisibility(.hidden)
                    } else {
                        ToolbarItem(placement: .topBarLeading) {
                            HomeHeroChrome().fixedSize()
                        }
                    }
                }
            }
        }
        .overlay(alignment: .topLeading) {
            if showsBrowsingRow, stacksCategoryHeader {
                PortraitNavigationHeader { browsingRow }
                    .environment(\.colorScheme, .dark)
            }
        }
        .portraitNavigationRail(isEnabled: stacksCategoryHeader, store: store) {
            NavigationLink {
                HomeSearchView(store: store)
                    .toolbarVisibility(.visible, for: .navigationBar)
                    .toolbar { NativeNowPlayingToolbar(store: store) }
            } label: { Label("Search", glyph: .search) }
            .accessibilityIdentifier("homeSearchButton")
            if !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty {
                NavigationLink {
                    NativeOfflineLibraryView(store: store)
                        .toolbarVisibility(.visible, for: .navigationBar)
                } label: { Label("Downloads", glyph: .download) }
                .accessibilityIdentifier("homeDownloadsButton")
            }
        }
        .ignoresSafeArea(.container, edges: stacksCategoryHeader ? .top : [])
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("homeScreen")
        .alert("Recommendation hidden", isPresented: $showRecommendationUndo) {
            Button("Undo") { if let hiddenRecommendationID { Task { await feed.undoHide(hiddenRecommendationID) } } }
            Button("Done", role: .cancel) {}
        }
        #if LUMINA_UI_TESTING
        .accessibilityValue("\(uiTestingNavigationDiagnostics ?? "") refreshes=\(uiTestingHomeRefreshCount)")
        #endif
        .task(id: MobileHomeSessionScope(store.session)) {
            guard store.session != nil else {
                completedInitialHomeLoad = false
                return
            }
            let needsInitialLibraryLoad = !homeLayout.hasLoaded || !feed.hasLoaded
            async let savedLayout: Void = homeLayout.load()
            async let rows: Void = feed.load()
            if needsInitialLibraryLoad, store.recentItems.isEmpty {
                async let library: Void = store.loadHome()
                _ = await (savedLayout, rows, library)
            } else {
                _ = await (savedLayout, rows)
            }
            guard !Task.isCancelled else { return }
            completedInitialHomeLoad = true
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { refreshHomeRowsAfterInitialLoad() }
        }
        .onAppear {
            refreshHomeRowsAfterInitialLoad()
        }
    }

    private var browsingRow: some View {
        HomeBrowsingRow(category: category, centersContent: !stacksCategoryHeader && !usesClosedCategoryRow,
                        destinations: HomeBrowsingDestination.allCases.filter {
            $0 != .all && ($0.homeCategory != nil || store.availableLibraryCategories.contains($0.rawValue))
        }, onSelect: selectCategory)
    }

    private var browsingHeaderWidth: CGFloat {
        guard usesClosedCategoryRow else {
            // The shared Library cap reserves three actions. Home can add both
            // now-playing controls alongside Search and Downloads.
            let extraControls = max(0, homeTrailingControlSlots - 3)
            return max(100, protectedHeaderCategoryWidth - 2 * 44 * extraControls)
        }
        // Closed Duo places its actions in the system's vertical trailing rail.
        return categoryBarWidth
    }

    private var homeTrailingControlSlots: CGFloat {
        var slots = 1.0 // Search
        if !store.offline.downloadedItems.isEmpty || !store.offline.pendingItems.isEmpty { slots += 1 }
        if let playback = audioSession.playback,
           !playback.hasFinished,
           (playback.isPlaying || !playback.initiallyPaused) {
            slots += 2
        }
        return slots
    }

    private func selectCategory(_ selected: HomeBrowsingDestination) {
        switch selected {
        case .movies: onOpenLibrary(.movies)
        case .tv: onOpenLibrary(.tv)
        case .anime: onOpenLibrary(.anime)
        case .music: onOpenLibrary(.music)
        case .books: onOpenLibrary(.books)
        case .all: category = .all
        case .watchlist: category = .watchlist
        }
    }

    private func refreshHomeRowsAfterInitialLoad() {
        guard completedInitialHomeLoad else { return }
        Task {
            async let layout: Void = homeLayout.load()
            async let rows: Void = feed.load()
            async let navigation: Void = store.refreshNavigationCategories()
            _ = await (layout, rows, navigation)
        }
    }

    @ViewBuilder
    private func homeShelf(_ id: HomeShelfID, rows: [HomeFeedRow]) -> some View {
        Group {
            if feed.hasLoaded {
                ForEach(rows.filter { $0.shelfID == id }) { row in
                    if !row.items.isEmpty {
                    MediaRail(title: row.title, items: row.items, store: store,
                              identifierPrefix: id == .continueWatching ? "resumeItem" : id == .nextUp ? "nextUpItem" : id == .newInLibrary ? "recentItem" : "homeRow-\(row.id)",
                              wide: row.shape == .still && id != .continueWatching && id != .nextUp,
                              feed: feed, onHide: { hiddenRecommendationID = $0; showRecommendationUndo = true })
                    }
                    if !(row.externalItems ?? []).isEmpty { HomeExternalRail(row: row) }
                }
            } else {
            switch id.rawValue {
            case "continue":
                if !store.resumeItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.resumeItems), store: store, identifierPrefix: "resumeItem", wide: false)
                }
            case "next_up":
                if !store.nextUpItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.nextUpItems), store: store, identifierPrefix: "nextUpItem", wide: false)
                }
            case "new_in_library":
                if !store.recentItems.isEmpty {
                    MediaRail(title: id.title, items: refinedItems(store.recentItems), store: store, identifierPrefix: "recentItem", wide: false)
                }
            default: EmptyView()
            }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("homeShelf-\(id.rawValue)")
    }
}

@MainActor
private struct HomeSearchView: View {
    let store: LuminaStore
    @State private var query = ""
    @State private var searchPresented = true
    @FocusState private var searchFocused: Bool
    @State private var results: [MediaItem] = []
    @State private var loading = false
    @State private var message: String?
    @State private var selectedSearchItem: MediaItem?
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var presentedPlayback: PlaybackModel?
    @State private var startingTrack = false
    @State private var playbackError: String?

    var body: some View {
        Group {
        if usesStandardPhonePushedChrome {
            searchContent
                .navigationDestination(item: $selectedSearchItem) { item in
                    searchDestination(for: item)
                }
                .standardPhonePushedChrome(store: store)
                .task { searchFocused = true }
        } else {
            searchContent
                .searchable(text: $query, isPresented: $searchPresented, prompt: "Films, shows, and music")
        }
        }
        .nativeAudioPlayer(playback: $presentedPlayback, store: store)
        .alert("Playback unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
    }

    private var searchContent: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if usesStandardPhonePushedChrome {
                    HStack(spacing: 10) {
                        LuminaIcon(glyph: .search, size: 20).foregroundStyle(.secondary)
                        TextField("Search your library", text: $query)
                            .textFieldStyle(.plain)
                            .focused($searchFocused)
                            .submitLabel(.search)
                            .accessibilityIdentifier("homeSearchField")
                        if !query.isEmpty {
                            Button { query = "" } label: {
                                Label("Clear search", glyph: .close)
                                    .labelStyle(.iconOnly)
                                    .frame(minWidth: 44, minHeight: 44)
                                    .contentShape(.rect)
                            }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("homeSearchClearButton")
                        }
                    }
                    .frame(minHeight: 44)
                    .padding(.horizontal, 12)
                    .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 12))
                }
                if loading { ProgressView("Searching…").frame(maxWidth: .infinity) }
                if let message { Text(message).foregroundStyle(.secondary) }
                if results.isEmpty, !loading {
                    ContentUnavailableView {
                        Label(query.isEmpty ? "Search your library" : "No results", glyph: .search)
                    } description: {
                        Text(query.isEmpty ? "Find films, shows, episodes, and music." : "Try another title.")
                    }
                }
                ForEach(results) { item in
                    Group {
                        if item.isAudio {
                            Button { playTrack(item) } label: { searchResultRow(item) }
                                .disabled(startingTrack || audioSession.busy)
                        } else if usesStandardPhonePushedChrome {
                            Button { selectedSearchItem = item } label: {
                                searchResultRow(item)
                            }
                        } else {
                            NavigationLink(value: item) {
                                searchResultRow(item)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("homeSearchResult-\(item.id)")
                }
            }.padding(20)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("Search")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("homeSearchScreen")
        .task(id: query.trimmingCharacters(in: .whitespacesAndNewlines)) {
            let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
            results = []; message = nil
            guard !term.isEmpty else { loading = false; return }
            loading = true
            do {
                try await Task.sleep(for: .milliseconds(300))
                let found = try await store.searchHome(query: term)
                try Task.checkCancellation()
                results = found; loading = false
            } catch {
                guard !Task.isCancelled else { return }
                message = "Search could not finish. Try again."; loading = false
            }
        }
    }

    private func searchResultRow(_ item: MediaItem) -> some View {
        HStack(spacing: 16) {
            ArtworkView(url: store.imageURL(for: item), name: item.name)
                .frame(width: 64, height: 96).clipped()
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name).font(.headline)
                if !item.displaySubtitle.isEmpty {
                    Text(item.displaySubtitle).font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Spacer()
            LuminaIcon(glyph: item.isAudio ? .play : .forward, size: 20).foregroundStyle(.secondary)
        }
    }

    private func playTrack(_ item: MediaItem) {
        guard !startingTrack else { return }
        startingTrack = true
        let sourceItems = results.filter(\.isAudio)
        Task {
            defer { startingTrack = false }
            do { presentedPlayback = try await audioSession.play(items: sourceItems, startingAt: item, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

    @ViewBuilder
    private func searchDestination(for item: MediaItem) -> some View {
        if item.type == "MusicAlbum" || item.type == "MusicArtist" {
            MusicCollectionView(item: item, store: store)
        } else if item.type == "Episode" {
            EpisodeCollectionDestination(item: item, store: store)
        } else if item.isCollection {
            VideoCollectionView(item: item, store: store)
        } else {
            TitleDetailView(item: item, store: store)
        }
    }
}

private struct HomeExternalRail: View {
    let row: HomeFeedRow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(row.title).font(.title3.weight(.bold)).padding(.top, 10)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(row.externalItems ?? []) { item in
                        if let destination = item.sourceURL {
                            Link(destination: destination) {
                                VStack(alignment: .leading, spacing: 6) {
                                    ArtworkView(url: item.artworkURL, name: item.title, aspect: 16 / 9)
                                    Text(item.title).font(.caption.weight(.semibold)).lineLimit(2)
                                    Text(item.subtitle).font(.caption2).foregroundStyle(LuminaTheme.muted).lineLimit(1)
                                    Label("Open source", glyph: .externalLink, size: 16).font(.caption2)
                                }
                                .containerRelativeFrame(.horizontal) { width, _ in min(276, width * 0.68) }
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(item.title), \(item.subtitle). Open source app or website.")
                            .accessibilityIdentifier("homeExternal-\(item.id)")
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .modifier(LuminaCarouselEdges())
        }
    }
}

/// Account owns Home customization; the editor keeps the same server-backed layout contract.
@MainActor
struct NativeHomePreferencesView: View {
    let store: LuminaStore
    @State private var layout: LuminaHomeLayout?

    var body: some View {
        Group {
            if let layout {
                NativeHomeLayoutEditor(layout: layout, store: store, feed: nil)
            } else {
                ProgressView("Loading Home layout…")
            }
        }
        .task(id: MobileHomeSessionScope(store.session)) {
            guard let session = store.session else { return }
            let loaded = LuminaHomeLayout(session: session)
            layout = loaded
            await loaded.load()
        }
    }
}

@MainActor
private struct NativeHomeLayoutEditor: View {
    let layout: LuminaHomeLayout
    let store: LuminaStore
    let feed: LuminaHomeFeed?
    @State private var draft: [HomeShelfPreference] = []
    @State private var changed = false
    @State private var reset = false
    @Environment(\.dismiss) private var dismiss
    private var rows: [HomeShelfPreference] { draft }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Cancel") { dismiss() }.disabled(layout.isSaving)
                    .accessibilityIdentifier("homeLayoutCancelButton")
                Spacer()
                Text("Edit Home").font(LuminaTheme.editorial(22, relativeTo: .title3))
                Spacer()
                Button("Done") {
                    Task { if await layout.save(reset ? nil : draft) { dismiss() } }
                }.disabled(!layout.hasLoaded || layout.isSaving)
                    .accessibilityIdentifier("homeLayoutSaveButton")
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .padding(.horizontal, 24).padding(.vertical, 8)
            List {
                Section {
                    ForEach(rows) { shelf in
                        VStack(alignment: .leading, spacing: 0) {
                            LuminaSettingsSwitch(shelf.id.title, isOn: Binding(get: {
                                draft.first { $0.id == shelf.id }?.visible ?? true
                            }, set: { visible in
                                if let index = draft.firstIndex(where: { $0.id == shelf.id }) {
                                    draft[index] = HomeShelfPreference(id: shelf.id, visible: visible)
                                    changed = true; reset = false
                                }
                            }), identifier: "homeShelfVisibility-\(shelf.id.rawValue)")
                            .accessibilityAction(named: "Move up") { move(shelf.id, offset: -1) }
                            .accessibilityAction(named: "Move down") { move(shelf.id, offset: 1) }
                            if feed?.hasLoaded == true && isEmpty(shelf.id) {
                                Text("Empty").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    }
                    .onMove { source, target in
                        var reordered = rows
                        reordered.move(fromOffsets: source, toOffset: target)
                        applyOrder(reordered)
                    }
                }
                .disabled(!layout.hasLoaded || layout.isSaving)
                Section {
                    Button("Reset to default") {
                        draft = HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }
                        changed = true; reset = true
                    }.accessibilityIdentifier("homeLayoutResetButton")
                        .disabled(!layout.hasLoaded || layout.isSaving)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if layout.isLoading { ProgressView("Loading Home layout…") }
                if let error = layout.errorMessage {
                    Section {
                        Text(error).foregroundStyle(.red)
                        Button("Try again") { Task { await layout.load() } }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .environment(\.editMode, .constant(.active))
            .interactiveDismissDisabled(layout.isSaving)
            .onAppear {
                draft = layout.shelves
                changed = false
                reset = false
            }
            .onChange(of: layout.shelves, initial: true) { _, value in
                if !changed { draft = value }
            }
            .accessibilityIdentifier("homeLayoutEditor")
        }
        .background(LuminaTheme.paper)
        .foregroundStyle(LuminaTheme.ink)
    }

    private func applyOrder(_ reordered: [HomeShelfPreference]) {
        draft = reordered
        changed = true; reset = false
    }

    private func move(_ id: HomeShelfID, offset: Int) {
        var reordered = rows
        guard let index = reordered.firstIndex(where: { $0.id == id }) else { return }
        let target = index + offset
        guard reordered.indices.contains(target) else { return }
        reordered.swapAt(index, target)
        applyOrder(reordered)
    }

    private func isEmpty(_ id: HomeShelfID) -> Bool {
        if let feed, feed.hasLoaded {
            return !feed.rows.contains { $0.shelfID == id && $0.hasItems }
        }
        return switch id.rawValue {
        case "continue": store.resumeItems.isEmpty
        case "next_up": store.nextUpItems.isEmpty
        case "new_in_library": store.recentItems.isEmpty
        default: true
        }
    }
}

private struct LibraryStableNavigationBar: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 27.0, *) {
            content.toolbarMinimizationBehavior(.never, for: .navigationBar)
        } else { content }
    }
}

private struct HomeHeroScrollEdge: ViewModifier {
    var isEnabled: Bool

    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            content.scrollEdgeEffectHidden(isEnabled, for: .top)
        } else { content }
    }
}

private struct VideoDetailChrome: ViewModifier {
    var isEnabled = true

    func body(content: Content) -> some View {
        if isEnabled {
            content
                .ignoresSafeArea(.container)
                .modifier(HomeHeroScrollEdge(isEnabled: true))
                .navigationTitle("")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbarColorScheme(.dark, for: .navigationBar)
        } else {
            content
        }
    }
}

private struct PortraitNavigationHeader<Categories: View>: View {
    @ViewBuilder let categories: Categories
    @Environment(\.layoutDirection) private var layoutDirection

    var body: some View {
        HStack(spacing: 8) {
            HomeHeroChrome()
            categories.frame(maxWidth: .infinity).clipped()
        }
        .padding(.horizontal, 8)
        .frame(height: 44)
        .background(.ultraThinMaterial, in: .capsule)
        .padding(.leading, layoutDirection == .leftToRight ? 20 : 96)
        .padding(.trailing, layoutDirection == .leftToRight ? 96 : 20)
        .padding(.top, 16)
        .padding(.bottom, 8)
    }
}

private struct PortraitRailButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(width: 44, height: 44)
            .contentShape(.rect)
            .opacity(configuration.isPressed ? 0.65 : 1)
    }
}

private struct PortraitNavigationRail<Actions: View>: ViewModifier {
    let isEnabled: Bool
    let store: LuminaStore
    @ViewBuilder let actions: Actions
    @Environment(\.layoutDirection) private var layoutDirection

    func body(content: Content) -> some View {
        if isEnabled {
            content
                .toolbarVisibility(.hidden, for: .navigationBar)
                .overlay {
                    GeometryReader { geometry in
                        VStack(spacing: 8) {
                            actions
                            NativeNowPlayingControls(store: store)
                        }
                        .labelStyle(.iconOnly)
                        .buttonStyle(PortraitRailButtonStyle())
                        .padding(4)
                        .background(.regularMaterial, in: .capsule)
                        .environment(\.layoutDirection, layoutDirection)
                        .frame(width: 52)
                        .padding(.top, topInset(in: geometry))
                        .padding(.trailing, 20)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                        .environment(\.layoutDirection, .leftToRight)
                    }
                }
        } else {
            content
        }
    }

    private func topInset(in geometry: GeometryProxy) -> CGFloat {
        // Keep all browse roots below the physical status corner, including
        // Library roots whose category header has already consumed safe area.
        max(0, 88 - geometry.frame(in: .global).minY)
    }
}

extension View {
    func portraitNavigationRail<Actions: View>(isEnabled: Bool, store: LuminaStore,
                                               @ViewBuilder actions: () -> Actions) -> some View {
        modifier(PortraitNavigationRail(isEnabled: isEnabled, store: store, actions: actions))
    }
}

@MainActor
private struct HomeHeroChrome: View {
    @Environment(\.returnToLuminaHome) private var returnHome

    var body: some View {
        Button(action: returnHome) {
            LuminaBrandMark()
                .frame(width: 28, height: 28 * 34 / 56)
            .foregroundStyle(LuminaTheme.ink)
        }
        .buttonStyle(.plain)
        .frame(minWidth: 44, minHeight: 44, alignment: .leading)
        .contentShape(.rect)
        .accessibilityLabel("Lumina")
        .accessibilityHint("Return to Home")
        .accessibilityIdentifier("homeMasthead")
    }

}

@MainActor
private struct VideoLibraryView: View {
    @Bindable var store: LuminaStore

    @State private var searchText = ""
    @State private var searchVisible = false
    @Binding var kind: VideoBrowseKind
    let activeKind: VideoBrowseKind?
    var usesPortraitNavigationRail = false
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersVisible = false
    @State private var scrollPhase: ScrollPhase = .idle
    @State private var pullIntent: PullIntent = .none
    @State private var alphabetRailVisible = false
    @State private var selectedAlphabetAnchor: LibraryAlphabetAnchor?
    @State private var queryTask: Task<Void, Never>?
    @State private var alphabetJumpTask: Task<Void, Never>?
    @State private var alphabetRailDismissTask: Task<Void, Never>?
    @State private var alphabetJumpID = UUID()
    @State private var alphabetAnchorsScopeKey: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private enum PullIntent { case none, search, refresh }

    var body: some View {
        GeometryReader { geometry in
        ScrollViewReader { scrollProxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if searchVisible {
                        HStack(spacing: 0) {
                            LibrarySearchBar(text: $searchText, onSubmit: search)
                                .frame(maxWidth: .infinity)
                            Button("Cancel") {
                                searchText = ""
                                searchVisible = false
                            }
                            .buttonStyle(.plain)
                            .font(.body)
                            .accessibilityIdentifier("videoSearchCancelButton")
                        }
                        .padding(.trailing, geometry.safeAreaInsets.trailing)
                        .frame(height: 56)
                    }

                    if store.isBusy && store.items.isEmpty {
                        LibraryLoadingView()
                    } else if store.items.isEmpty {
                        if let message = store.errorMessage, !message.isEmpty {
                            InlineErrorView(message: message) {
                                refresh()
                            }
                        } else if !searchText.isEmpty {
                            ContentUnavailableView.search(text: searchText)
                        } else if kind != .all || filters != VideoFilters() {
                            ContentUnavailableView {
                                Label("No matching video", glyph: .movies)
                            } description: {
                                Text("Try another video type or clear your filters.")
                            }
                        } else {
                            EmptyLibraryView {
                                refresh()
                            }
                        }
                    } else {
                        if let message = store.errorMessage, !message.isEmpty {
                            InlineErrorView(message: message) {
                                refresh()
                            }
                        }

                        if !store.items.isEmpty {
                            VideoPosterWall(items: store.items, store: store)
                        }

                        if store.hasMore {
                            Button {
                                Task { await store.loadMore() }
                            } label: {
                                HStack(spacing: 8) {
                                    if store.isBusy {
                                        ProgressView()
                                    }
                                    Text("Load more")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(LuminaQuietActionStyle())
                            .controlSize(.large)
                            .disabled(store.isBusy)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.trailing, usesPortraitNavigationRail ? 76 : 0)
                .padding(.bottom, 28)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .overlay(alignment: .trailing) {
                if isAlphabetRailAvailable && alphabetRailVisible && !usesAlphabetMenu {
                    VideoAlphabetRail(
                        anchors: store.libraryAlphabet,
                        selectedAnchor: $selectedAlphabetAnchor,
                        onInteraction: cancelAlphabetRailDismiss,
                        onCommit: { jump(to: $0, using: scrollProxy) }
                    )
                    .padding(.trailing, 6)
                    .transition(.opacity)
                    .accessibilityHidden(true)
                }
            }
            .background(LuminaTheme.paper)
            .ignoresSafeArea(.container, edges: .horizontal)
            .accessibilityIdentifier("libraryScreen")
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .onScrollGeometryChange(for: PullIntent.self) { geometry in
                guard scrollPhase == .interacting else { return .none }
                let distance = -(geometry.contentOffset.y + geometry.contentInsets.top)
                if distance >= 80 { return .refresh }
                return distance >= 12 ? .search : .none
            } action: { _, intent in
                pullIntent = intent
            }
            .onScrollPhaseChange { previous, phase, context in
                let intent = pullIntent
                scrollPhase = phase
                if previous == .interacting, isAlphabetRailAvailable, abs(context.velocity?.dy ?? 0) >= 700 {
                    revealAlphabetRail()
                }
                guard previous == .interacting else { return }
                pullIntent = .none
                // Commit on release so revealing search never moves the refresh target mid-drag.
                switch intent {
                case .search:
                    searchVisible = true
                case .refresh:
                    if searchText.isEmpty { searchVisible = false }
                    refresh()
                case .none:
                    break
                }
            }
            .accessibilityAction(named: "Search video") {
                searchVisible = true
            }
            .accessibilityAction(named: "Refresh library", refresh)
            .onChange(of: searchText) { _, text in
                resetAlphabetNavigation()
                if text.isEmpty { search() }
            }
            .onChange(of: kind) {
                if !supportsTechnicalFilterKind {
                    var compatible = filters
                    compatible.resolution = nil; compatible.hdr = nil
                    compatible.audio = nil; compatible.imaxOnly = false
                    if compatible != filters { filters = compatible; return }
                }
                resetAlphabetNavigation(); search()
            }
            .onChange(of: sort) { resetAlphabetNavigation(); search() }
            .onChange(of: filters) { resetAlphabetNavigation(); search() }
            .onDisappear {
                queryTask?.cancel()
                queryTask = nil
                alphabetJumpTask?.cancel()
                alphabetRailDismissTask?.cancel()
            }
            .sheet(isPresented: $filtersVisible) {
                VideoFilterSheet(filters: $filters, store: store, allowsTechnicalFilters: supportsTechnicalFilterKind)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
            .toolbar {
                if !usesPortraitNavigationRail {
                    if !searchVisible {
                        ToolbarItem(placement: .topBarTrailing) { videoSearchButton }
                    }
                    if isAlphabetRailAvailable {
                        ToolbarItem(placement: .topBarTrailing) { alphabetMenu(using: scrollProxy) }
                    }
                    ToolbarItem(placement: .topBarTrailing) { videoOptionsMenu }
                }
            }
            .portraitNavigationRail(isEnabled: usesPortraitNavigationRail, store: store) {
                if !searchVisible { videoSearchButton }
                if isAlphabetRailAvailable { alphabetMenu(using: scrollProxy) }
                videoOptionsMenu
            }
            .task(id: "\(kind.rawValue)-\(activeKind?.rawValue ?? "inactive")") {
                guard activeKind == kind else { return }
                await loadInitialContent()
            }
        }
        }
    }

    private var videoSearchButton: some View {
        Button("Search video", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("videoSearchButton")
    }

    private var videoOptionsMenu: some View {
        Menu {
            Picker("Sort: \(sort.label)", selection: $sort) {
                ForEach(VideoSort.allCases, id: \.self) { sort in Text(sort.label).tag(sort) }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("videoSortPicker")
            .accessibilityValue(sort.label)

            Divider()
            Button("Filters", glyph: .filter) {
                filtersVisible = true
            }
            .accessibilityIdentifier("videoFiltersButton")
            .accessibilityValue(filters == VideoFilters() ? "No filters" : filterSummary)

            if filters != VideoFilters() {
                Button("Clear filters", glyph: .close) { filters = VideoFilters() }
                    .accessibilityIdentifier("videoClearFiltersButton")
            }
        } label: {
            Label("Video options, \(optionsAccessibilityValue)", glyph: .moreHorizontal)
                .labelStyle(.iconOnly)
        }
        .accessibilityIdentifier("videoOptionsMenu")
        .accessibilityValue(optionsAccessibilityValue)
    }

    private var isAlphabetRailAvailable: Bool {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && sort == .name
            && [.movies, .shows, .anime].contains(kind)
            && alphabetAnchorsScopeKey == alphabetScopeKey
            && store.supportsLibraryAlphabet
            && !store.libraryAlphabet.isEmpty
    }

    private var alphabetScopeKey: String {
        [
            kind.rawValue,
            sort.rawValue,
            searchText.trimmingCharacters(in: .whitespacesAndNewlines),
            filters.genre,
            filters.year.map(String.init) ?? "",
            String(filters.unwatchedOnly),
            String(filters.favoritesOnly),
            filters.resolution?.rawValue ?? "",
            filters.hdr?.rawValue ?? "",
            filters.audio?.rawValue ?? "",
            String(filters.imaxOnly),
        ].joined(separator: "\u{1F}")
    }

    private var usesAlphabetMenu: Bool {
        voiceOver || dynamicTypeSize.isAccessibilitySize
    }

    @ViewBuilder
    private func alphabetMenu(using scrollProxy: ScrollViewProxy) -> some View {
        Menu {
            ForEach(store.libraryAlphabet) { anchor in
                Button(anchor.letter) {
                    jump(to: anchor, using: scrollProxy)
                }
            }
        } label: {
            Label("Browse alphabet", systemImage: "textformat.abc")
                .labelStyle(.iconOnly)
        }
        .accessibilityIdentifier("videoAlphabetButton")
        .accessibilityLabel("Browse library alphabet")
        .accessibilityValue(selectedAlphabetAnchor?.letter ?? "")
    }

    private func revealAlphabetRail() {
        guard isAlphabetRailAvailable, !usesAlphabetMenu else { return }
        alphabetRailVisible = true
        scheduleAlphabetRailDismiss()
    }

    private func scheduleAlphabetRailDismiss() {
        alphabetRailDismissTask?.cancel()
        alphabetRailDismissTask = Task {
            try? await Task.sleep(for: .seconds(3))
            guard !Task.isCancelled else { return }
            alphabetRailVisible = false
        }
    }

    private func cancelAlphabetRailDismiss() {
        alphabetRailDismissTask?.cancel()
        alphabetRailDismissTask = nil
    }

    private func resetAlphabetNavigation() {
        alphabetJumpTask?.cancel()
        alphabetJumpTask = nil
        cancelAlphabetRailDismiss()
        alphabetRailVisible = false
        selectedAlphabetAnchor = nil
        alphabetJumpID = UUID()
        alphabetAnchorsScopeKey = nil
    }

    private func jump(to anchor: LibraryAlphabetAnchor, using scrollProxy: ScrollViewProxy) {
        guard isAlphabetRailAvailable else { return }
        alphabetJumpTask?.cancel()
        let jumpID = UUID()
        alphabetJumpID = jumpID
        selectedAlphabetAnchor = anchor
        alphabetRailVisible = false

        alphabetJumpTask = Task {
            guard let firstItemID = await store.jumpLibrary(to: anchor), !Task.isCancelled, alphabetJumpID == jumpID else { return }
            // The page replacement publishes on the main actor. Yield once so the
            // lazy wall has installed the returned item's scroll target.
            await Task.yield()
            guard !Task.isCancelled, alphabetJumpID == jumpID else { return }
            if reduceMotion {
                scrollProxy.scrollTo(firstItemID, anchor: .top)
            } else {
                withAnimation(.easeOut(duration: 0.22)) {
                    scrollProxy.scrollTo(firstItemID, anchor: .top)
                }
            }
        }
    }

    private var supportsTechnicalFilterKind: Bool {
        kind == .movies || kind == .all || kind == .episodes
    }

    private var filterSummary: String {
        var labels = [String]()
        if !filters.genre.isEmpty { labels.append(filters.genre) }
        if let year = filters.year { labels.append(String(year)) }
        if filters.unwatchedOnly { labels.append("Unwatched") }
        if filters.favoritesOnly { labels.append("Favorites") }
        if let resolution = filters.resolution { labels.append(resolution.label) }
        if let hdr = filters.hdr { labels.append(hdr.label) }
        if let audio = filters.audio { labels.append(audio.label) }
        if filters.imaxOnly { labels.append("IMAX") }
        return labels.joined(separator: " · ")
    }

    private var optionsAccessibilityValue: String {
        let activeFilters = filters == VideoFilters() ? "No filters" : filterSummary
        return "\(kind.label), sorted by \(sort.label); \(activeFilters)"
    }

    private func loadInitialContent() async {
        guard store.session != nil else { return }
        let scopeKey = alphabetScopeKey
        await store.loadLibrary(search: searchText, kind: kind, sort: sort, filters: filters)
        guard scopeKey == alphabetScopeKey else { return }
        alphabetAnchorsScopeKey = scopeKey
    }

    private func search() {
        queryTask?.cancel()
        store.clearError()
        let scopeKey = alphabetScopeKey
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        let kind = kind, sort = sort, filters = filters
        queryTask = Task {
            guard !Task.isCancelled else { return }
            await store.loadLibrary(search: query, kind: kind, sort: sort, filters: filters, forceRefresh: true)
            guard scopeKey == alphabetScopeKey else { return }
            alphabetAnchorsScopeKey = scopeKey
        }
    }

    private func refresh() { search() }
}

@MainActor
private struct VideoAlphabetRail: View {
    let anchors: [LibraryAlphabetAnchor]
    @Binding var selectedAnchor: LibraryAlphabetAnchor?
    let onInteraction: () -> Void
    let onCommit: (LibraryAlphabetAnchor) -> Void

    private var railHeight: CGFloat {
        min(max(CGFloat(anchors.count) * 16 + 12, 108), 480)
    }

    var body: some View {
        GeometryReader { proxy in
            VStack(spacing: 0) {
                ForEach(anchors) { anchor in
                    Text(anchor.letter)
                        .font(.caption2.weight(selectedAnchor == anchor ? .bold : .medium))
                        .foregroundStyle(selectedAnchor == anchor ? LuminaTheme.accentInk : LuminaTheme.muted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .padding(.vertical, 6)
            .background(.thinMaterial, in: Capsule())
            .contentShape(Capsule())
            .gesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .local)
                    .onChanged { value in
                        onInteraction()
                        selectedAnchor = anchor(at: value.location.y, height: proxy.size.height)
                    }
                    .onEnded { value in
                        guard let anchor = anchor(at: value.location.y, height: proxy.size.height) else { return }
                        selectedAnchor = anchor
                        onCommit(anchor)
                    }
            )
        }
        .frame(width: 38, height: railHeight)
    }

    private func anchor(at y: CGFloat, height: CGFloat) -> LibraryAlphabetAnchor? {
        guard !anchors.isEmpty, height > 0 else { return nil }
        let proportion = min(max(y / height, 0), 0.999)
        let index = min(Int(proportion * CGFloat(anchors.count)), anchors.count - 1)
        return anchors[index]
    }
}

@MainActor
private struct VideoFilterSheet: View {
    @Binding var filters: VideoFilters
    let store: LuminaStore
    let allowsTechnicalFilters: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var draft = VideoFilters()
    @State private var year = ""
    @State private var technicalSupported: Bool?
    @State private var checkingSupport = false
    @State private var supportError: String?

    private var validYear: Bool {
        year.isEmpty || (year.count == 4 && Int(year).map { (1000...9999).contains($0) } == true)
    }

    private var hasTechnicalSelection: Bool {
        draft.resolution != nil || draft.hdr != nil || draft.audio != nil || draft.imaxOnly
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    HStack(alignment: .top, spacing: 24) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Genre").font(.caption).foregroundStyle(LuminaTheme.muted)
                            TextField("Genre", text: $draft.genre, prompt: Text("Any genre"))
                                .autocorrectionDisabled()
                                .accessibilityIdentifier("videoGenreField")
                        }
                        .padding(.bottom, 10)
                        .overlay(alignment: .bottom) { Divider() }
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Year").font(.caption).foregroundStyle(LuminaTheme.muted)
                            TextField("Year", text: $year, prompt: Text("Any year"))
                                .keyboardType(.numberPad)
                                .accessibilityIdentifier("videoYearField")
                        }
                        .padding(.bottom, 10)
                        .overlay(alignment: .bottom) { Divider() }
                    }
                    .textFieldStyle(.plain)
                    if !validYear {
                        Text("Enter a four-digit year or leave it empty.")
                            .font(.caption).foregroundStyle(LuminaTheme.muted)
                    }
                    if allowsTechnicalFilters {
                        if technicalSupported == true {
                            choiceRow("Resolution", id: "resolution", values: VideoResolution.allCases,
                                      selection: $draft.resolution, label: { $0.label })
                            choiceRow("HDR", id: "hdr", values: VideoHDR.allCases,
                                      selection: $draft.hdr, label: { $0.label })
                            binaryRow("Edition", id: "imax", selectedTitle: "IMAX", selection: $draft.imaxOnly)
                            choiceRow("Audio", id: "audio", values: VideoAudioFormat.allCases,
                                      selection: $draft.audio, label: { $0.label })
                        } else if checkingSupport || (technicalSupported == nil && supportError == nil) {
                            ProgressView("Checking filter support…")
                        } else {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(supportError ?? "Update the connected server to enable picture and audio filters.")
                                    .font(.caption).foregroundStyle(LuminaTheme.muted)
                                Button("Check again", glyph: .sync) { Task { await checkSupport() } }
                                    .buttonStyle(LuminaQuietActionStyle())
                                    .accessibilityIdentifier("videoTechnicalFiltersRetryButton")
                            }
                        }
                    }
                    binaryRow("Watch status", id: "unwatched", selectedTitle: "Unwatched", selection: $draft.unwatchedOnly)
                    binaryRow("Favorites", id: "favorites", selectedTitle: "Favorites only", selection: $draft.favoritesOnly)
                    Button("Reset filters", glyph: .restore) { draft = VideoFilters(); year = "" }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("videoResetFiltersButton")
                }
                .padding(24)
            }
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        draft.genre = draft.genre.trimmingCharacters(in: .whitespacesAndNewlines)
                        draft.year = Int(year)
                        filters = draft
                        dismiss()
                    }
                    .disabled(!validYear || (hasTechnicalSelection && technicalSupported != true))
                    .accessibilityIdentifier("videoApplyFiltersButton")
                }
            }
            .onAppear { draft = filters; year = filters.year.map(String.init) ?? "" }
            .task { if allowsTechnicalFilters { await checkSupport() } }
        }
        .tint(LuminaTheme.ink)
        .presentationDetents([.large])
        .accessibilityIdentifier("videoFiltersSheet")
    }

    private func checkSupport() async {
        guard !checkingSupport else { return }
        checkingSupport = true
        supportError = nil
        defer { checkingSupport = false }
        do { technicalSupported = try await store.supportsTechnicalVideoFilters() }
        catch { supportError = "Filter options could not load. Check the connection and retry." }
    }

    private func choiceRow<Value>(_ title: String, id: String, values: [Value], selection: Binding<Value?>,
                                  label: @escaping (Value) -> String) -> some View
        where Value: Hashable & RawRepresentable, Value.RawValue == String {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 16) {
                    choice("Any", id: "\(id)-any", selected: selection.wrappedValue == nil) { selection.wrappedValue = nil }
                    ForEach(values, id: \.self) { value in
                        choice(label(value), id: "\(id)-\(value.rawValue)", selected: selection.wrappedValue == value) {
                            selection.wrappedValue = value
                        }
                    }
                }
            }
        }
    }

    private func binaryRow(_ title: String, id: String, selectedTitle: String, selection: Binding<Bool>) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            HStack(spacing: 16) {
                choice("Any", id: "\(id)-any", selected: !selection.wrappedValue) { selection.wrappedValue = false }
                choice(selectedTitle, id: "\(id)-only", selected: selection.wrappedValue) { selection.wrappedValue = true }
            }
        }
    }

    private func choice(_ title: String, id: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(LuminaUnderlineTabStyle(selected: selected))
            .fixedSize(horizontal: true, vertical: true)
            .accessibilityValue(selected ? "Selected" : "Not selected")
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier("videoFilter-\(id)")
    }
}

@MainActor
private struct NativeWatchlistButton: View {
    let item: MediaItem
    let store: LuminaStore
    var identifier = "videoWatchlistButton"
    var onWatchlistChanged: @MainActor () async -> Void = {}
    var onInteractionChanged: @MainActor (Bool) -> Void = { _ in }
    @State private var watchlist: LuminaWatchlist?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                guard let watchlist else { return }
                onInteractionChanged(true)
                Task {
                    defer { onInteractionChanged(false) }
                    let didChange = await watchlist.set(itemID: item.id, included: !watchlist.isIncluded)
                    if didChange { await onWatchlistChanged() }
                }
            } label: {
                Label(watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist",
                      glyph: .bookmark, variant: watchlist?.isIncluded == true ? .solid : .outline)
            }
            .buttonStyle(LuminaMediaActionStyle())
            .disabled(watchlist == nil || watchlist?.isLoading == true || watchlist?.isSaving == true || watchlist?.errorMessage != nil)
            .accessibilityIdentifier(identifier)
            .accessibilityValue(watchlist?.isIncluded == true ? "In watchlist" : "Not in watchlist")
            .accessibilityHint(watchlist?.isIncluded == true ? "Remove from your watchlist" : "Save to your watchlist")

            if let message = watchlist?.errorMessage {
                InlineErrorView(message: message) {
                    Task { await watchlist?.load(itemID: item.id) }
                }
            }
        }
        .task(id: item.id + (store.session?.accessToken ?? "")) {
            let model = LuminaWatchlist(session: store.session)
            watchlist = model
            await model.load(itemID: item.id)
        }
    }
}

@MainActor
struct VideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let initialSeasonID: String?
    let initialEpisodeID: String?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var children: [MediaItem] = []
    @State private var selectedSeasonEpisodes: [MediaItem] = []
    @State private var selectedSeasonID: String?
    @State private var selectedEpisodeID: String?
    @State private var isLoadingSelectedSeason = false
    @State private var hasPositionedInitialEpisode = false
    @State private var hasCenteredInitialEpisode = false
    @State private var detailItem: MediaItem?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?

    init(item: MediaItem, store: LuminaStore, initialSeasonID: String? = nil, initialEpisodeID: String? = nil) {
        self.item = item
        self.store = store
        self.initialSeasonID = initialSeasonID
        self.initialEpisodeID = initialEpisodeID
        _selectedSeasonID = State(initialValue: initialSeasonID)
        _selectedEpisodeID = State(initialValue: initialEpisodeID)
    }

    private var isSeriesCollection: Bool { item.type == "Series" }
    private var collectionSectionTitle: String { "Episodes" }
    private var collectionSectionID: String { "collection-\(item.id)-content" }
    private var hasResumeEpisode: Bool {
        if isSeriesCollection,
           let selectedEpisode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
            return selectedEpisode.resumeSeconds > 5 && selectedEpisode.userData?.played != true
        }
        return store.resumeItems.contains { candidate in
            candidate.type == "Episode"
                && (candidate.seriesID == item.id || candidate.seasonID == item.id)
                && candidate.resumeSeconds > 5
                && candidate.userData?.played != true
        }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    collectionHero(using: proxy)

                    VStack(alignment: .leading, spacing: 28) {
                        if item.type != "Series" {
                            Text(collectionSectionTitle)
                                .font(.title2.weight(.bold))
                                .accessibilityAddTraits(.isHeader)
                                .id(collectionSectionID)
                        }
                        if let errorMessage {
                            InlineErrorView(message: errorMessage) { Task { await load() } }
                        }
                        if isLoading && children.isEmpty {
                            ProgressView(item.type == "Series" ? "Loading seasons…" : "Loading episodes…")
                        } else if children.isEmpty && errorMessage == nil {
                            ContentUnavailableView {
                                Label("Nothing here yet", glyph: .series)
                            } description: {
                                Text("No \(item.type == "Series" ? "seasons" : "episodes") are available. Pull to refresh after adding them to Lumina.")
                            }
                        } else if item.type == "Series" {
                            seasonSelector
                                .id(collectionSectionID)
                            if isSeriesCollection {
                                VStack(alignment: .leading, spacing: 12) {
                                    if let selectedEpisode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
                                        Text(selectedEpisode.name)
                                            .font(.headline)
                                            .accessibilityIdentifier("collectionEpisodeTitle")
                                        if !selectedEpisode.displaySubtitle.isEmpty {
                                            Text(selectedEpisode.displaySubtitle)
                                                .font(.subheadline)
                                                .foregroundStyle(.secondary)
                                        }
                                    }
                                    if isLoadingSelectedSeason {
                                        ProgressView("Loading episodes…")
                                    } else {
                                        episodeCards(selectedSeasonEpisodes)
                                    }
                                }
                                .id(initialEpisodeSectionID)
                            }
                        } else {
                            episodeCards(children)
                        }

                        collectionAbout

                        NativeTitleSupplement(item: detailItem ?? item, store: store)
                            .padding(.top, 4)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 28)
                }
                .frame(maxWidth: .infinity)
                // The hero extends under system chrome; the last disclosure must
                // still scroll fully above the floating phone tab bar.
                .padding(.bottom, usesStandardPhonePushedChrome ? 100 : 0)
            }
            .onChange(of: selectedSeasonEpisodes.map(\.id)) {
                positionInitialEpisode(using: proxy)
            }
        }
        .background(LuminaTheme.paper)
        .modifier(VideoDetailChrome())
        .accessibilityIdentifier("videoCollectionScreen")
        .standardPhonePushedChrome(store: store, foreground: .white, background: LuminaTheme.theater)
        .scrollClipDisabled()
        .task(id: item.id) { await load() }
        .refreshable { await load() }
        .nativePlayback(request: $playbackRequest, store: store) {
            Task { await load() }
        }
    }

    @ViewBuilder private func collectionHero(using proxy: ScrollViewProxy) -> some View {
        MediaBillboard(item: detailItem ?? item, store: store, context: "", showsOverview: false,
                       showsContext: false, showsMetadata: false, artworkFirst: true,
                       compactMinimumHeight: 0, wideMinimumHeight: 0) {
            VStack(alignment: .leading, spacing: 14) {
                collectionHeroMetadata
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .center, spacing: 12) { playbackActions }
                    VStack(alignment: .leading, spacing: 12) { playbackActions }
                }
                Button {
                    if reduceMotion {
                        proxy.scrollTo(collectionSectionID, anchor: .top)
                    } else {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            proxy.scrollTo(collectionSectionID, anchor: .top)
                        }
                    }
                } label: {
                    Label("Browse episodes", glyph: .chevronDown)
                        .font(.subheadline.weight(.semibold))
                }
                .buttonStyle(.plain)
                .frame(minHeight: 44)
                .accessibilityHint("Shows \(collectionSectionTitle.lowercased()) below this artwork")
                .accessibilityIdentifier("collectionSeasonsButton")

                if let playbackError {
                    Label(playbackError, glyph: .warning)
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.accentInk)
                        .accessibilityIdentifier("videoCollectionPlaybackError")
                }
            }
        }
        .containerRelativeFrame(.vertical) { length, _ in length }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("collectionHero")
    }

    private var collectionHeroMetadata: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { collectionHeroMetadataContent }
            VStack(alignment: .leading, spacing: 8) { collectionHeroMetadataContent }
        }
        .font(.caption.weight(.medium))
        .textCase(.uppercase)
        .tracking(1)
        .foregroundStyle(.white.opacity(0.85))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var collectionHeroMetadataContent: some View {
        if let year = (detailItem ?? item).productionYear { Text(String(year)) }
        if let rating = (detailItem ?? item).officialRating, !rating.isEmpty { Text(rating) }
        if let count = (detailItem ?? item).childCount ?? (children.isEmpty ? nil : children.count) {
            let unit = item.type == "Series"
                ? (count == 1 ? "season" : "seasons")
                : (count == 1 ? "episode" : "episodes")
            Text("\(count) \(unit)")
        }
    }

    private var seasonSelector: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { seasonSelectorContent }
            VStack(alignment: .leading, spacing: 8) { seasonSelectorContent }
        }
    }

    @ViewBuilder private var seasonSelectorContent: some View {
        Text("Episodes")
            .font(.title2.weight(.bold))
            .accessibilityAddTraits(.isHeader)
        Menu {
            Picker("Season", selection: Binding(
                get: { selectedSeasonID },
                set: { id in
                    guard let season = children.first(where: { $0.id == id }),
                          id != selectedSeasonID else { return }
                    Task { await selectSeason(season) }
                }
            )) {
                ForEach(children) { season in
                    Text(season.name)
                        .tag(Optional(season.id))
                        .accessibilityIdentifier("videoSeason-\(season.id)")
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 8) {
                Text(children.first(where: { $0.id == selectedSeasonID })?.name ?? "Select season")
                    .fixedSize(horizontal: false, vertical: true)
                LuminaIcon(glyph: .chevronDown, size: 16)
            }
            .font(.headline)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading || isLoadingSelectedSeason)
        .accessibilityLabel("Season")
        .accessibilityValue(children.first(where: { $0.id == selectedSeasonID })?.name ?? "Select season")
        .accessibilityIdentifier("videoSeasonSelector")
    }

    @ViewBuilder private func episodeCards(_ episodes: [MediaItem]) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            LazyVStack(spacing: 12) {
                ForEach(episodes) { episode in
                    episodeLink(episode)
                }
            }
        } else {
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: 12) {
                        ForEach(episodes) { episode in
                            episodeLink(episode)
                                .containerRelativeFrame(.horizontal) { width, _ in
                                    min(max(340, width * 0.30), width * 0.86)
                                }
                        }
                    }
                    .scrollTargetLayout()
                }
                .onAppear { centerInitialEpisode(using: proxy) }
                .scrollTargetBehavior(.viewAligned)
                .modifier(LuminaCarouselEdges())
            }
        }
    }

    @ViewBuilder
    private var playbackActions: some View {
        Button {
            preparePlayback()
        } label: {
            Label(isPreparingPlayback ? "Preparing…" : hasResumeEpisode ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 24)
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(isPreparingPlayback || isLoading || isLoadingSelectedSeason)
        .accessibilityIdentifier("playButton")

        NativeWatchlistButton(item: item, store: store)
            .buttonStyle(LuminaMediaActionStyle(primary: false))
            .accessibilityIdentifier("videoWatchlistButton")
    }

    private func episodeLink(_ episode: MediaItem) -> some View {
        Group {
            if isSeriesCollection {
                Button { selectedEpisodeID = episode.id } label: {
                    episodeCard(episode, selected: episode.id == selectedEpisodeID)
                }
            } else {
                NavigationLink(value: episode) {
                    episodeCard(episode)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(mediaAccessibilityLabel(for: episode) + (episode.userData?.played == true ? ", Watched" : ""))
        .accessibilityValue(episode.id == selectedEpisodeID ? "Selected" : "")
        .accessibilityAddTraits(episode.id == selectedEpisodeID ? .isSelected : [])
        .accessibilityIdentifier("videoEpisode-\(episode.id)")
        .id(episodeScrollID(episode.id))
    }

    @ViewBuilder
    private var collectionAbout: some View {
        VStack(alignment: .leading, spacing: 12) {
            if isSeriesCollection,
               let episode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
                Text("About this episode")
                    .font(.title3.bold()).accessibilityAddTraits(.isHeader)
                if let overview = episode.overview, !overview.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    NativeMediaAbout(overview: MediaOverview(overview))
                        .id(episode.id)
                        .accessibilityIdentifier("episodeAbout")
                } else {
                    Text("An episode synopsis is not available yet.")
                        .font(.body).foregroundStyle(.secondary)
                }
                if let overview = (detailItem ?? item).overview, !overview.isEmpty {
                    DisclosureGroup {
                        NativeMediaAbout(overview: MediaOverview(overview))
                            .padding(.top, 12)
                            .accessibilityIdentifier("seriesAbout")
                    } label: {
                        Text("About the series").font(.headline)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .accessibilityIdentifier("seriesAboutToggle")
                    }
                }
            } else if let overview = (detailItem ?? item).overview, !overview.isEmpty {
                Text("About").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                NativeMediaAbout(overview: MediaOverview(overview))
            }
        }
        .tint(LuminaTheme.ink)
        .frame(maxWidth: 600, alignment: .leading)
        .padding(.trailing, UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome ? 0 : 64)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("collectionAbout")
    }

    private func episodeCard(_ episode: MediaItem, selected: Bool = false) -> some View {
        ZStack(alignment: .bottomLeading) {
            ArtworkView(
                url: store.backdropURL(for: episode, maxWidth: 920) ?? store.imageURL(for: episode),
                name: episode.name,
                aspect: 16 / 9,
                loadFraming: { await store.artworkFraming(for: $0) }
            )
            LinearGradient(
                colors: [.clear, LuminaTheme.theater.opacity(0.22), LuminaTheme.theater.opacity(0.96)],
                startPoint: .top,
                endPoint: .bottom
            )
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    if !episode.displaySubtitle.isEmpty {
                        Text(episode.displaySubtitle)
                    }
                    if episode.durationSeconds > 0 {
                        Text(Duration.seconds(episode.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
                    }
                    if episode.userData?.played == true {
                        Label("Watched", glyph: .check)
                            .labelStyle(.iconOnly)
                            .foregroundStyle(LuminaTheme.accentInk)
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.88))
                Text(episode.name)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                if let overview = episode.overview,
                   let synopsis = MediaOverview(overview).synopsis.first?.text {
                    Text(synopsis)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.84))
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                }
            }
            .padding(14)
            .fixedSize(horizontal: false, vertical: dynamicTypeSize.isAccessibilitySize)
            .background {
                if dynamicTypeSize.isAccessibilitySize {
                    LuminaTheme.theater
                }
            }
            if episode.resumeSeconds > 0, episode.durationSeconds > 0 {
                ProgressView(value: min(episode.resumeSeconds / episode.durationSeconds, 1))
                    .progressViewStyle(.linear)
                    .tint(LuminaTheme.brand)
                    .frame(height: 3)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 5)
                    .frame(maxWidth: .infinity, alignment: .bottom)
            }
        }
        .clipShape(.rect(cornerRadius: 8))
        .overlay {
            if selected { RoundedRectangle(cornerRadius: 8).strokeBorder(LuminaTheme.brand, lineWidth: 3) }
        }
        .contentShape(Rectangle())
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        detailItem = try? await store.loadItem(item)
        do {
            children = try await store.loadChildren(of: item)
            if isSeriesCollection {
                let resume = store.resumeItems.first {
                    $0.type == "Episode" && $0.seriesID == item.id && $0.userData?.played != true
                }
                if let season = children.first(where: { $0.id == selectedSeasonID })
                    ?? children.first(where: { $0.id == resume?.seasonID })
                    ?? children.first {
                    if selectedEpisodeID == nil, season.id == resume?.seasonID {
                        selectedEpisodeID = resume?.id
                    }
                    await selectSeason(season)
                }
            }
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This collection could not load. Check your connection and try again."
        }
        isLoading = false
    }

    private func selectSeason(_ season: MediaItem) async {
        guard !isLoadingSelectedSeason else { return }
        isLoadingSelectedSeason = true
        defer { isLoadingSelectedSeason = false }
        do {
            let episodes = try await store.loadChildren(of: season)
            selectedSeasonID = season.id
            selectedSeasonEpisodes = episodes
            selectedEpisodeID = episodes.contains(where: { $0.id == selectedEpisodeID })
                ? selectedEpisodeID
                : episodes.first?.id
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This season could not load. Check your connection and try again."
        }
    }

    private func episodeScrollID(_ episodeID: String) -> String {
        "selected-episode-\(episodeID)"
    }

    private var initialEpisodeSectionID: String { "initial-episode-section" }

    private func positionInitialEpisode(using proxy: ScrollViewProxy) {
        guard isSeriesCollection, initialEpisodeID != nil, !hasPositionedInitialEpisode,
              let selectedEpisodeID,
              selectedSeasonEpisodes.contains(where: { $0.id == selectedEpisodeID }) else { return }
        hasPositionedInitialEpisode = true
        DispatchQueue.main.async {
            proxy.scrollTo(initialEpisodeSectionID, anchor: .top)
        }
    }

    private func centerInitialEpisode(using proxy: ScrollViewProxy) {
        guard isSeriesCollection, initialEpisodeID != nil, !hasCenteredInitialEpisode,
              let selectedEpisodeID,
              selectedSeasonEpisodes.contains(where: { $0.id == selectedEpisodeID }) else { return }
        hasCenteredInitialEpisode = true
        DispatchQueue.main.async {
            proxy.scrollTo(episodeScrollID(selectedEpisodeID), anchor: .center)
        }
    }

    private func preparePlayback() {
        guard !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            defer { isPreparingPlayback = false }
            do {
                let episode = try await playableEpisode()
                playbackRequest = try await store.preparePlayback(for: episode)
            } catch {
                playbackError = (error as? LocalizedError)?.errorDescription ?? "No playable episode is available yet. Check the collection and try again."
            }
        }
    }

    private func playableEpisode() async throws -> MediaItem {
        if isSeriesCollection,
           let selectedEpisode = selectedSeasonEpisodes.first(where: { $0.id == selectedEpisodeID }) {
            return selectedEpisode
        }
        if let resume = store.resumeItems.first(where: { candidate in
            candidate.type == "Episode" && (candidate.seriesID == item.id || candidate.seasonID == item.id) && candidate.userData?.played != true
        }) {
            return resume
        }

        let seasons: [MediaItem]
        if item.type == "Series" {
            seasons = children.isEmpty ? try await store.loadChildren(of: item) : children
        } else {
            seasons = [item]
        }
        var episodes = [MediaItem]()
        for season in seasons {
            episodes.append(contentsOf: try await store.loadChildren(of: season))
        }
        guard let episode = episodes.first(where: { $0.resumeSeconds > 0 && $0.userData?.played != true })
            ?? episodes.first(where: { $0.userData?.played != true })
            ?? episodes.first else {
            throw VideoCollectionPlaybackError.noEpisodes
        }
        return episode
    }
}

private enum VideoCollectionPlaybackError: LocalizedError {
    case noEpisodes

    var errorDescription: String? {
        switch self {
        case .noEpisodes: "No playable episode is available yet."
        }
    }
}

@MainActor
private struct AudioLibraryView: View {
    let store: LuminaStore
    var usesPortraitNavigationRail = false
    @State private var kind: MusicBrowseKind = .albums
    @State private var searchText = ""
    @State private var searchVisible = false

    private var featuredAlbum: MediaItem? {
        guard kind == .albums, searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return store.audioItems.first { $0.type == "MusicAlbum" }
    }

    var body: some View {
        GeometryReader { geometry in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let album = featuredAlbum {
                    NavigationLink(value: album) {
                        MusicCollectionSpotlight(item: album, store: store,
                            minimumHeight: min(680, max(360, geometry.size.height * 0.78)),
                            topContentClearance: geometry.safeAreaInsets.top)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("audioFeaturedAlbum")
                    .padding(.horizontal, -20)
                }
                if let message = store.errorMessage {
                    InlineErrorView(message: message) { Task { await load() } }
                }
                if store.audioItems.isEmpty {
                    if store.isAudioBusy {
                        ProgressView("Loading music…")
                    } else if store.errorMessage == nil {
                        ContentUnavailableView {
                            Label(searchText.isEmpty ? "Your listening room is quiet" : "No music found",
                                  glyph: .music, variant: .solid)
                        } description: {
                            Text(searchText.isEmpty ? "Add music to Lumina, then refresh to find your albums, artists, and tracks here." : "Try another search or browse a different collection.")
                        } actions: {
                            Button("Refresh") { Task { await load() } }
                        }
                    }
                } else {
                    MusicGrid(items: store.audioItems, store: store, identifierPrefix: "audioItem")
                        .padding(.trailing, usesPortraitNavigationRail ? 76 : 0)
                    if store.audioHasMore {
                        Button {
                            Task { await store.loadMoreAudio() }
                        } label: {
                            HStack {
                                if store.isAudioBusy { ProgressView() }
                                Text("Load more music")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(LuminaQuietActionStyle())
                        .disabled(store.isAudioBusy)
                        .accessibilityIdentifier("audioLoadMoreButton")
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, featuredAlbum == nil ? 20 : 0)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .ignoresSafeArea(.container, edges: featuredAlbum == nil ? .horizontal : [.top, .horizontal])
        .modifier(HomeHeroScrollEdge(isEnabled: featuredAlbum != nil))
        .scrollClipDisabled()
        .toolbarColorScheme(featuredAlbum == nil ? nil : .dark, for: .navigationBar)
        .accessibilityIdentifier("audioScreen")
        .toolbar {
            if !usesPortraitNavigationRail {
                ToolbarItem(placement: .topBarTrailing) { musicOptionsMenu }
                ToolbarItem(placement: .topBarTrailing) { musicSearchButton }
            }
        }
        .portraitNavigationRail(isEnabled: usesPortraitNavigationRail, store: store) {
            musicSearchButton
            musicOptionsMenu
        }
        .modifier(NativeSearchPresentation(text: $searchText, isPresented: $searchVisible, prompt: "Search music",
                                           usesInlineSearch: usesPortraitNavigationRail,
                                           onSubmit: { Task { await load() } }))
        .onSubmit(of: .search) { Task { await load() } }
        .onChange(of: searchText) { if searchText.isEmpty { Task { await load() } } }
        .task(id: kind) { await load() }
        .refreshable { await load() }
        }
    }

    private var musicSearchButton: some View {
        Button("Search music", glyph: .search) { searchVisible = true }
            .accessibilityIdentifier("musicSearchButton")
    }

    private var musicOptionsMenu: some View {
        Menu {
            ForEach(NativeMusicSection.allCases, id: \.self) { section in
                NavigationLink {
                    NativeMusicLibraryView(store: store, section: section)
                        .toolbarVisibility(.visible, for: .navigationBar)
                        .toolbar {
                            NativeNowPlayingToolbar(store: store)
                        }
                } label: {
                    Label(section.rawValue, glyph: section.glyph)
                }
                .accessibilityIdentifier("musicLibrary-\(section.rawValue.lowercased())")
            }
            Divider()
            Picker("Browse music", selection: $kind) {
                ForEach(MusicBrowseKind.allCases, id: \.self) { value in Text(value.label).tag(value) }
            }
            .accessibilityIdentifier("audioKindPicker")
        } label: { Label("Music options", glyph: .moreHorizontal) }
        .accessibilityIdentifier("musicOptionsMenu")
    }

    private func load() async {
        await store.loadAudio(kind: kind, search: searchText.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

@MainActor
private struct MusicCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var children: [MediaItem] = []
    @State private var loading = true
    @State private var errorMessage: String?
    @State private var albumPlayback: PlaybackModel?
    @State private var preparingPlayback = false
    @State private var playbackError: String?

    @State private var layoutWidth: CGFloat = 0
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 720
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ScrollView {
            Group {
                if layoutWidth >= minimumWideWidth && !dynamicTypeSize.isAccessibilitySize {
                    HStack(alignment: .top, spacing: 40) {
                        collectionArtwork.frame(width: 280)
                        collectionBody
                    }
                } else {
                    VStack(alignment: .leading, spacing: 28) {
                        collectionArtwork
                            .frame(maxWidth: 260)
                            .frame(maxWidth: .infinity)
                        collectionBody
                    }
                }
            }
            .padding(20)
            .padding(.top, 12)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { layoutWidth = $0 }
        }
        .modifier(AlbumArtworkBackground(url: store.imageURL(for: item, maxWidth: 760), enabled: item.type == "MusicAlbum"))
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarColorScheme(item.type == "MusicAlbum" ? .dark : nil, for: .navigationBar)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("musicCollectionScreen")
        .standardPhonePushedChrome(store: store,
                                   foreground: item.type == "MusicAlbum" ? .white : LuminaTheme.ink,
                                   background: item.type == "MusicAlbum" ? LuminaTheme.theater : LuminaTheme.paper)
        .task(id: item.id) { await load() }
        .refreshable { await load() }
        .nativeAudioPlayer(playback: $albumPlayback, store: store) {
            Task { await store.refresh() }
        }
    }

    private var collectionArtwork: some View {
        ArtworkView(url: store.imageURL(for: item, maxWidth: 760), name: item.name, aspect: 1, glyph: .music)
            .shadow(color: .black.opacity(0.18), radius: 20, y: 10)
    }

    private var collectionBody: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(item.name)
                .font(LuminaTheme.editorial(34, relativeTo: .largeTitle))
                .accessibilityAddTraits(.isHeader)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle).foregroundStyle(LuminaTheme.muted)
            }
            if let overview = item.overview, !overview.isEmpty {
                Text(overview).font(.body)
            }
            if children.contains(where: \.isAudio) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { albumActions }
                    VStack(alignment: .leading, spacing: 12) { albumActions }
                }
            }
            if let playbackError {
                Text(playbackError)
                    .font(.callout)
                    .foregroundStyle(LuminaTheme.muted)
            }
            if loading {
                ProgressView("Loading music…")
            } else if let errorMessage {
                InlineErrorView(message: errorMessage) { Task { await load() } }
            } else if children.isEmpty {
                ContentUnavailableView {
                    Label("No music here yet", glyph: .music, variant: .solid)
                } description: {
                    Text("Refresh after adding music to this collection.")
                }
            } else if item.type == "MusicAlbum", children.allSatisfy(\.isAudio) {
                albumTracks
            } else {
                MusicGrid(items: children, store: store, identifierPrefix: "musicItem")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var albumTracks: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(children.enumerated()), id: \.element.id) { index, track in
                Button { playTrack(track) } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(String(track.indexNumber ?? index + 1))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(LuminaTheme.muted)
                            .frame(minWidth: 24, alignment: .leading)
                        Text(track.name)
                            .font(LuminaTheme.editorial(18, relativeTo: .body))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
                        if track.durationSeconds > 0 {
                            Text(Duration.seconds(track.durationSeconds).formatted(.time(pattern: .minuteSecond)))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(LuminaTheme.muted)
                        }
                    }
                    .padding(.vertical, 16)
                    .frame(minHeight: 56)
                    .contentShape(.rect)
                    .overlay(alignment: .top) {
                        Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
                    }
                }
                .buttonStyle(.plain)
                .disabled(preparingPlayback || audioSession.busy)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: track))
                .accessibilityValue(track.durationSeconds > 0 ? Duration.seconds(track.durationSeconds).formatted(.units(allowed: [.minutes, .seconds], width: .wide)) : "")
                .accessibilityIdentifier("musicItem-\(track.id)")
            }
        }
    }

    @ViewBuilder private var albumActions: some View {
        Button {
            playCollection(shuffled: false)
        } label: {
            Label(preparingPlayback ? "Preparing…" : "Play album", glyph: .play, variant: .solid, size: 24)
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicPlayButton")
        Button {
            playCollection(shuffled: true)
        } label: {
            Label("Shuffle", glyph: .shuffle, size: 24)
        }
        .buttonStyle(LuminaQuietActionStyle())
        .controlSize(.large)
        .disabled(preparingPlayback)
        .accessibilityIdentifier("musicShuffleButton")
    }

    private func playTrack(_ track: MediaItem) {
        guard !preparingPlayback else { return }
        preparingPlayback = true
        playbackError = nil
        let items = children
        Task {
            defer { preparingPlayback = false }
            do { albumPlayback = try await audioSession.play(items: items, startingAt: track, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

    private func playCollection(shuffled: Bool) {
        guard !preparingPlayback else { return }
        let tracks = children.filter(\.isAudio)
        guard !tracks.isEmpty else { return }
        preparingPlayback = true
        playbackError = nil
        Task {
            defer { preparingPlayback = false }
            do {
                let resolved = try await store.music.resolve(items: tracks)
                let first = shuffled ? Int.random(in: 0..<max(1, resolved.count)) : 0
                albumPlayback = try await audioSession.play(tracks: resolved, index: first, store: store, shuffle: shuffled)
            } catch {
                playbackError = (error as? LocalizedError)?.errorDescription
                    ?? "Playback could not start. Check your connection and try again."
            }
        }
    }

    private func load() async {
        loading = true
        errorMessage = nil
        defer { loading = false }
        do { children = try await store.musicChildren(of: item) }
        catch { errorMessage = "This music collection could not load. Check your connection and try again." }
    }
}

@MainActor
private struct MusicGrid: View {
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.horizontalSizeClass) private var widthClass
    @State private var lastVisibleItemID: String?
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var presentedPlayback: PlaybackModel?
    @State private var startingTrack = false
    @State private var playbackError: String?

    private var columns: [GridItem] {
        if dynamicTypeSize.isAccessibilitySize { return libraryGridColumns(for: dynamicTypeSize) }
        if widthClass == .regular { return [GridItem(.adaptive(minimum: 168, maximum: 220), spacing: 16, alignment: .top)] }
        return [GridItem(.flexible(), spacing: 16, alignment: .top), GridItem(.flexible(), spacing: 16, alignment: .top)]
    }

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
            ForEach(items) { item in
                itemControl(item)
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(mediaAccessibilityLabel(for: item))
                .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                .onAppear { lastVisibleItemID = item.id }
            }
        }
        .nativeAudioPlayer(playback: $presentedPlayback, store: store)
        .alert("Playback unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
        .task(id: lastVisibleItemID, priority: .utility) {
            await store.prefetchArtwork(after: lastVisibleItemID, in: items)
        }
    }
    @ViewBuilder
    private func itemControl(_ item: MediaItem) -> some View {
        if item.isAudio {
            Button { playTrack(item) } label: { card(item) }
                .disabled(startingTrack || audioSession.busy)
        } else {
            NavigationLink(value: item) { card(item) }
        }
    }

    private func card(_ item: MediaItem) -> some View {
        MediaCard(item: item, imageURL: store.imageURL(for: item), identifierPrefix: identifierPrefix,
                  loadFraming: { await store.artworkFraming(for: $0) })
    }

    private func playTrack(_ item: MediaItem) {
        guard !startingTrack else { return }
        startingTrack = true
        let sourceItems = items
        Task {
            defer { startingTrack = false }
            do { presentedPlayback = try await audioSession.play(items: sourceItems, startingAt: item, store: store) }
            catch { playbackError = "Playback could not start. Check your connection and try again." }
        }
    }

}

/// An inactive native search drawer still occupies space on Duo. Install it only
/// after the member chooses Search, and remove it completely when dismissed.
struct NativeSearchPresentation: ViewModifier {
    @Binding var text: String
    @Binding var isPresented: Bool
    let prompt: String
    var usesInlineSearch = false
    var onSubmit: () -> Void = {}

    func body(content: Content) -> some View {
        Group {
            if isPresented && usesInlineSearch {
                content.safeAreaInset(edge: .top, spacing: 0) {
                    HStack(spacing: 8) {
                        LibrarySearchBar(text: $text, prompt: prompt, onSubmit: onSubmit)
                            .frame(height: 44)
                        Button("Cancel") { isPresented = false }
                            .frame(minWidth: 44, minHeight: 44)
                    }
                    .padding(.horizontal, 20)
                    .padding(.trailing, 76)
                    .padding(.vertical, 8)
                    .background(LuminaTheme.paper)
                }
            } else if isPresented {
                content.searchable(text: $text, isPresented: $isPresented,
                    placement: .navigationBarDrawer(displayMode: .always), prompt: Text(prompt))
                    .safeAreaInset(edge: .top, spacing: 0) {
                        if UIDevice.current.userInterfaceIdiom == .pad {
                            HStack {
                                Spacer()
                                Button("Cancel") { isPresented = false }
                                    .frame(minWidth: 44, minHeight: 44)
                                    .accessibilityIdentifier("nativeSearchDismissButton")
                            }
                            .padding(.horizontal, 16)
                            .background(.bar)
                        }
                    }
            } else {
                content
            }
        }
        .onChange(of: isPresented) { _, presented in
            if !presented { text = "" }
        }
    }
}

private struct LibrarySearchBar: UIViewRepresentable {
    @Binding var text: String
    var prompt = String(localized: "Search video")
    let onSubmit: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> UISearchBar {
        let searchBar = UISearchBar()
        searchBar.delegate = context.coordinator
        searchBar.placeholder = prompt
        searchBar.searchBarStyle = .minimal
        searchBar.autocapitalizationType = .none
        searchBar.autocorrectionType = .no
        searchBar.searchTextField.accessibilityIdentifier = "searchField"
        return searchBar
    }

    func updateUIView(_ searchBar: UISearchBar, context: Context) {
        context.coordinator.parent = self
        if searchBar.text != text { searchBar.text = text }
    }

    @MainActor final class Coordinator: NSObject, UISearchBarDelegate {
        var parent: LibrarySearchBar
        init(_ parent: LibrarySearchBar) { self.parent = parent }

        func searchBar(_ searchBar: UISearchBar, textDidChange searchText: String) {
            parent.text = searchText
        }

        func searchBarSearchButtonClicked(_ searchBar: UISearchBar) {
            searchBar.resignFirstResponder()
            parent.onSubmit()
        }

    }
}

@MainActor
private struct LibraryFeaturedView: View {
    let store: LuminaStore
    var items: [MediaItem]? = nil
    var compactMinimumHeight: CGFloat? = 320
    var compactArtworkMinimumHeight: CGFloat?
    var wideMinimumHeight: CGFloat? = 540
    var topContentClearance: CGFloat = 0
    var bottomContentClearance: CGFloat = 0
    var onWatchlistChanged: @MainActor () async -> Void = {}

    @State private var request: PlaybackRequest?
    @State private var preparing = false
    @State private var playbackError: String?
    @State private var detailItem: MediaItem?
    @State private var heroIsVisible = true
    @State private var watchlistIsBusy = false

    var body: some View {
        FeaturedShelf(
            items: items ?? store.featuredItems,
            store: store,
            isInteracting: preparing || request != nil || watchlistIsBusy || !heroIsVisible,
            showsOverview: false,
            showsContext: false,
            showsMetadata: false,
            showsProgress: false,
            compactMinimumHeight: compactMinimumHeight,
            compactArtworkMinimumHeight: compactArtworkMinimumHeight,
            wideMinimumHeight: wideMinimumHeight,
            topContentClearance: topContentClearance,
            bottomContentClearance: bottomContentClearance,
            onOpen: { detailItem = $0 }
        ) { item in
            VStack(alignment: .leading, spacing: 12) {
                if let context = context(for: item) {
                    Text(context)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { heroActionButtons(for: item) }
                    VStack(alignment: .leading, spacing: 12) { heroActionButtons(for: item) }
                }
            }
            if let playbackError {
                Text(playbackError).font(.callout)
            }
        }
        .onScrollVisibilityChange(threshold: 0.2) { heroIsVisible = $0 }
        .nativePlayback(request: $request, store: store) {
            Task { await store.refresh() }
        }
        .navigationDestination(item: $detailItem) { item in
            TitleDetailView(item: item, store: store)
                .toolbarVisibility(.visible, for: .navigationBar)
        }
    }

    @ViewBuilder private func heroActionButtons(for item: MediaItem) -> some View {
        Button {
            guard !preparing else { return }
            preparing = true
            playbackError = nil
            Task {
                defer { preparing = false }
                do { request = try await store.preparePlayback(for: item) }
                catch { playbackError = "Playback could not start. Try again." }
            }
        } label: {
            Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 24)
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")

        NativeWatchlistButton(
            item: item,
            store: store,
            identifier: "featuredWatchlistButton",
            onWatchlistChanged: onWatchlistChanged,
            onInteractionChanged: { watchlistIsBusy = $0 }
        )
    }

    private func context(for item: MediaItem) -> String? {
        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            return Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)) + " remaining"
        }
        return nil
    }
}

@MainActor
private struct VideoPosterWall: View {
    let items: [MediaItem]
    let store: LuminaStore

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .body) private var minimumPosterWidth: CGFloat = 128
    @ScaledMetric(relativeTo: .body) private var posterGap: CGFloat = 10
    @State private var availableWidth: CGFloat = 0
    @State private var lastVisibleItemID: String?
    @State private var preparedItems: [MediaItem]
    @State private var artworkPreparationTask: Task<Void, Never>?

    init(items: [MediaItem], store: LuminaStore) {
        self.items = items
        self.store = store
        let firstScreen = items.prefix(36).compactMap { store.imageURL(for: $0) }
        _preparedItems = State(initialValue: firstScreen.allSatisfy {
            ArtworkImageCache.shared.cachedImage(for: $0) != nil
        } ? items : [])
    }

    var body: some View {
        Group {
            if !items.isEmpty && (preparedItems.isEmpty || !retainsPreparedPrefix) {
                LibraryLoadingView()
            } else {
                wall(width: availableWidth)
            }
        }
            .frame(maxWidth: .infinity, alignment: .leading)
            .onChange(of: items, initial: true) { _, items in
                artworkPreparationTask?.cancel()
                let previous = Set(preparedItems.compactMap { store.imageURL(for: $0) })
                let urls = Array(items.compactMap { store.imageURL(for: $0) }
                    .filter { !previous.contains($0) }.prefix(36))
                // Keep staging alive across a transient NavigationStack disappearance.
                // A new item set cancels this work before it can publish an old wall.
                artworkPreparationTask = Task {
                    await withTaskGroup(of: Void.self) { group in
                        for url in urls {
                            group.addTask { _ = await ArtworkImageCache.shared.image(for: url) }
                        }
                    }
                    guard !Task.isCancelled else { return }
                    preparedItems = items
                }
            }
            #if LUMINA_UI_TESTING
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("libraryArtworkReadiness")
            .accessibilityValue("ready=\(preparedItems.count) total=\(items.count)")
            #endif
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { availableWidth = $0 }
            .task(id: lastVisibleItemID, priority: .utility) {
                await store.prefetchArtwork(after: lastVisibleItemID, in: items)
            }
    }

    private var retainsPreparedPrefix: Bool {
        preparedItems.count <= items.count
            && zip(items, preparedItems).allSatisfy { $0.id == $1.id }
    }

    @ViewBuilder
    private func wall(width: CGFloat) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            LazyVStack(alignment: .leading, spacing: posterGap) {
                ForEach(preparedItems) { item in
                    posterLink(item)
                        .frame(width: min(width, 220), alignment: .leading)
                }
            }
        } else {
            LazyVGrid(
                columns: Array(repeating: GridItem(.flexible(), spacing: posterGap), count: columnCount(for: width)),
                alignment: .leading,
                spacing: posterGap
            ) {
                ForEach(preparedItems) { item in
                    posterLink(item)
                }
            }
        }
    }

    private func posterLink(_ item: MediaItem) -> some View {
        NavigationLink(value: item) {
            MediaCard(
                item: item,
                imageURL: store.imageURL(for: item),
                identifierPrefix: "library",
                showsCaption: false,
                loadFraming: { await store.artworkFraming(for: $0) }
            )
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape([.interaction, .accessibility], Rectangle())
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(mediaAccessibilityLabel(for: item))
        .accessibilityIdentifier("libraryItem-\(item.id)")
        .id(item.id)
        .onAppear {
            lastVisibleItemID = item.id
            if store.hasMore && item.id == items.suffix(6).first?.id {
                Task { await store.loadMore() }
            }
        }
    }

    private func columnCount(for width: CGFloat) -> Int {
        let count = Int((width + posterGap) / (minimumPosterWidth + posterGap))
        return min(max(count, 2), 6)
    }


}

@MainActor
private struct MediaRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String
    var wide = true
    var feed: LuminaHomeFeed? = nil
    var onHide: (String) -> Void = { _ in }

    @ScaledMetric(relativeTo: .body) private var posterWidth: CGFloat = 132
    @ScaledMetric(relativeTo: .body) private var stillWidth: CGFloat = 276

    private var captionPresentation: HomeRailCaptionPresentation {
        HomeRailCaptionPresentation(identifierPrefix: identifierPrefix)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title3.weight(.bold))
                .padding(.top, 10)
                .frame(maxWidth: .infinity, alignment: .leading)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 10) {
                    ForEach(items) { item in
                        HomeRailCard(item: item, feed: feed, onHide: onHide) {
                        NavigationLink(value: item) {
                            MediaCard(
                                item: item,
                                imageURL: wide ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item) : store.posterURL(for: item),
                                identifierPrefix: identifierPrefix,
                                wide: wide,
                                captionPresentation: captionPresentation,
                                loadFraming: { await store.artworkFraming(for: $0) }
                            )
                            .frame(width: cardWidth(for: item), alignment: .leading)
                            .overlay(alignment: .bottomLeading) {
                                if let reason = item.luminaReco?.reason {
                                    Text(reason).font(.caption2).lineLimit(2).padding(6)
                                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
                                        .accessibilityHidden(true)
                                }
                            }
                        }
                        .contentShape([.interaction, .accessibility], Rectangle())
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityAddTraits(.isButton)
                        .accessibilityLabel(mediaAccessibilityLabel(for: item, presentation: captionPresentation))
                        .accessibilityHint(item.luminaReco?.reason ?? "")
                        .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .modifier(LuminaCarouselEdges())
            .accessibilityIdentifier("mediaRail-\(identifierPrefix)")
        }
    }

    private func cardWidth(for item: MediaItem) -> CGFloat {
        if item.isAudio || item.type.hasPrefix("Music") { return posterWidth * 1.2 }
        return wide ? stillWidth : posterWidth
    }
}

@MainActor private struct HomeRailCard<Content: View>: View {
    let item: MediaItem
    let feed: LuminaHomeFeed?
    let onHide: (String) -> Void
    let content: Content
    @State private var isVisible = false

    init(item: MediaItem, feed: LuminaHomeFeed?, onHide: @escaping (String) -> Void, @ViewBuilder content: () -> Content) {
        self.item = item
        self.feed = feed
        self.onHide = onHide
        self.content = content()
    }

    var body: some View {
        content
            .onScrollVisibilityChange(threshold: 0.6) { isVisible = $0 }
            .task(id: isVisible) {
                guard isVisible, let reco = item.luminaReco else { return }
                try? await Task.sleep(for: .milliseconds(600))
                guard !Task.isCancelled else { return }
                await feed?.record("impression", recommendation: reco)
            }
            .simultaneousGesture(TapGesture().onEnded {
                if let reco = item.luminaReco { Task { await feed?.record("open", recommendation: reco) } }
            })
            .contextMenu {
                if item.luminaReco != nil {
                    Button("Not interested") {
                        Task {
                            if let hidden = await feed?.hide(item) { onHide(hidden) }
                        }
                    }
                }
            }
    }
}

@MainActor
private struct MediaCard: View {
    let item: MediaItem
    let imageURL: URL?
    let identifierPrefix: String
    var wide = false
    var showsCaption = true
    var captionPresentation: HomeRailCaptionPresentation = .standard
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var titleHeight: CGFloat = 34
    @ScaledMetric(relativeTo: .caption2) private var metadataHeight: CGFloat = 18

    private var caption: HomeRailCaption {
        HomeRailCaption(item: item, presentation: captionPresentation)
    }

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 6) {
            ArtworkView(url: imageURL, name: item.name,
                        aspect: item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" ? 1 : wide ? 16 / 9 : 2 / 3,
                        glyph: item.isAudio || item.type.hasPrefix("Music") ? .music : .movies,
                        loadFraming: loadFraming)
                .accessibilityIdentifier("\(identifierPrefix)Artwork-\(item.id)")
                .overlay(alignment: .bottom) {
                    if progressFraction > 0 {
                        ProgressView(value: progressFraction)
                            .progressViewStyle(.linear)
                            .tint(LuminaTheme.brand)
                            .frame(height: 3)
                            .clipShape(.rect(cornerRadius: 2))
                    }
                }

            if showsCaption {
                Text(caption.primary)
                    .font(.caption.weight(.semibold))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : titleHeight, alignment: .topLeading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("\(identifierPrefix)Title-\(item.id)")

                Group {
                    if let secondary = caption.secondary {
                        Text(secondary)
                    } else {
                        Color.clear.accessibilityHidden(true)
                    }
                }
                .font(.caption2)
                .foregroundStyle(LuminaTheme.muted)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .truncationMode(.tail)
                .fixedSize(horizontal: false, vertical: true)
                .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataHeight, alignment: .topLeading)
                .frame(maxWidth: .infinity, alignment: .leading)

                if captionPresentation.reservesProgressBaseline {
                    Group {
                        if let progress = caption.progress {
                            Text(progress)
                        } else {
                            Color.clear.accessibilityHidden(true)
                        }
                    }
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataHeight, alignment: .topLeading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }

        content
            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
            .clipped()
    }

    private var progressFraction: Double {
        guard item.durationSeconds > 0, item.resumeSeconds > 0 else { return 0 }
        return min(max(item.resumeSeconds / item.durationSeconds, 0), 1)
    }
}

private enum HomeRailCaptionPresentation: Equatable {
    case standard
    case continueWatching
    case nextUp

    init(identifierPrefix: String) {
        switch identifierPrefix {
        case "resumeItem": self = .continueWatching
        case "nextUpItem": self = .nextUp
        default: self = .standard
        }
    }

    var reservesProgressBaseline: Bool {
        self != .standard
    }
}

private struct HomeRailCaption {
    let primary: String
    let secondary: String?
    let progress: String?

    init(item: MediaItem, presentation: HomeRailCaptionPresentation) {
        guard presentation != .standard else {
            primary = item.name
            secondary = item.displaySubtitle.isEmpty ? nil : item.displaySubtitle
            progress = nil
            return
        }

        let episodeLabel = Self.episodeLabel(for: item)
        primary = item.type == "Episode" ? item.seriesName ?? item.name : item.name
        secondary = episodeLabel ?? (item.displaySubtitle.isEmpty ? nil : item.displaySubtitle)

        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            let remaining = Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated))
            progress = "\(remaining) remaining"
        } else if presentation == .nextUp, item.type == "Episode" {
            progress = "Next episode"
        } else {
            progress = nil
        }
    }

    private static func episodeLabel(for item: MediaItem) -> String? {
        guard item.type == "Episode" else { return nil }
        let number: String? = switch (item.parentIndexNumber, item.indexNumber) {
        case let (.some(season), .some(episode)): "S\(season) · E\(episode)"
        default: nil
        }
        return [number, item.name].compactMap { $0 }.joined(separator: " · ")
    }

    var accessibilityLabel: String {
        [primary, secondary, progress].compactMap { $0 }.joined(separator: ", ")
    }
}

@MainActor
private struct ArtworkView: View {
    let url: URL?
    let name: String
    var aspect: CGFloat = 2 / 3
    var glyph: LuminaGlyph = .movies
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                placeholder.overlay {
                    SubjectAwareArtwork(url: url, loadFraming: loadFraming)
                }
            }
            .clipShape(.rect(cornerRadius: 6))
            .accessibilityHidden(true)
    }

    private var placeholder: some View {
        VStack(spacing: 8) {
            LuminaIcon(glyph: glyph, variant: .solid, size: 28)
            Text(name)
                .font(.caption)
                .multilineTextAlignment(.center)
                .lineLimit(3)
        }
        .foregroundStyle(LuminaTheme.muted)
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

@MainActor
private struct NativeTitleSupplement: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var related: [MediaItem] = []

    private var cast: [MediaPerson] {
        Array((item.people ?? []).filter { $0.type?.lowercased() == "actor" }.prefix(24))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            if !cast.isEmpty {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Cast").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(alignment: .top, spacing: 16) {
                            ForEach(Array(cast.enumerated()), id: \.offset) { _, person in
                                VStack(alignment: .leading, spacing: 8) {
                                    CachedArtworkImage(url: store.personImageURL(for: person)) { image in
                                        image.resizable().scaledToFill()
                                    } placeholder: {
                                        LuminaTheme.ink.opacity(0.06)
                                            .overlay { LuminaIcon(glyph: .profile, variant: .solid, size: 40).foregroundStyle(LuminaTheme.muted) }
                                    }
                                    .frame(width: 112, height: 148)
                                    .clipShape(.rect(cornerRadius: 8))
                                    .accessibilityHidden(true)
                                    Text(person.name).font(.subheadline.weight(.medium)).lineLimit(2)
                                    if let role = person.role, !role.isEmpty {
                                        Text(role).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(2)
                                    }
                                }
                                .frame(width: 112, alignment: .topLeading)
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                    .modifier(LuminaCarouselEdges())
                }
                .accessibilityIdentifier("detailCastSection")
            }
            if !related.isEmpty {
                MediaRail(title: "More like this", items: related, store: store, identifierPrefix: "related", wide: false)
                    .accessibilityIdentifier("detailRelatedSection")
            }
        }
        .foregroundStyle(LuminaTheme.ink)
        .task(id: item.id) {
            related = []
            let matches = (try? await store.relatedItems(for: item)) ?? []
            guard !Task.isCancelled else { return }
            related = matches
        }
    }
}

@MainActor
struct TitleDetailView: View {
    let item: MediaItem
    @Bindable var store: LuminaStore

    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?
    @State private var updatedItem: MediaItem?
    @State private var isSavingState = false
    @State private var isLoadingItem = true
    @State private var stateError: String?

    private var currentItem: MediaItem { updatedItem ?? item }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                detailHeader
                    .padding(.horizontal, -20)
                    .padding(.top, currentItem.isAudio ? -20 : 0)

                VStack(alignment: .leading, spacing: 20) {
                    if let stateError {
                        Label(stateError, glyph: .warning)
                            .font(.callout)
                            .foregroundStyle(LuminaTheme.accentInk)
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("videoStateError")
                    }

                    if let overview = currentItem.overview, !overview.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("About").font(.title3.bold()).accessibilityAddTraits(.isHeader)
                            NativeMediaAbout(overview: MediaOverview(overview))
                                .padding(.trailing, UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome ? 0 : 64)
                        }
                        .foregroundStyle(LuminaTheme.ink)
                    }

                    videoDownloadStatus

                    if let playbackError, !playbackError.isEmpty {
                        Label(playbackError, glyph: .warning)
                            .font(.callout)
                            .foregroundStyle(LuminaTheme.accentInk)
                            .accessibilityElement(children: .combine)
                            .accessibilityAddTraits(.isStaticText)
                    }
                }
                .frame(maxWidth: 680, alignment: .leading)
                NativeTitleSupplement(item: currentItem, store: store)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .scrollClipDisabled()
        .accessibilityIdentifier("detailScreen")
        .modifier(VideoDetailChrome(isEnabled: !currentItem.isAudio))
        .standardPhonePushedChrome(store: store,
                                   foreground: currentItem.isAudio ? LuminaTheme.ink : .white,
                                   background: currentItem.isAudio ? LuminaTheme.paper : LuminaTheme.theater) {
            if !currentItem.isAudio {
                titleActionsMenu
            }
        }
        .toolbar {
            if !currentItem.isAudio {
                ToolbarItem(placement: .topBarTrailing) {
                    titleActionsMenu
                }
            }
        }
        .task(id: item.id) { await refreshItem() }
        .nativePlayback(request: $playbackRequest, store: store) {
            Task {
                await store.refresh()
                await refreshItem()
            }
        }
    }

    private var favoriteButton: some View {
        Button {
            updateState(favorite: currentItem.userData?.isFavorite != true)
        } label: {
            Label(currentItem.userData?.isFavorite == true ? "Remove favorite" : "Add favorite",
                  glyph: .favorite,
                  variant: currentItem.userData?.isFavorite == true ? .solid : .outline)
                .frame(minWidth: 132)
        }
        .buttonStyle(LuminaMediaActionStyle())
        .controlSize(.large)
        .disabled(isSavingState || isLoadingItem)
        .accessibilityValue(currentItem.userData?.isFavorite == true ? "Favorite" : "Not a favorite")
        .accessibilityIdentifier("videoFavoriteButton")
    }

    private var titleActionsMenu: some View {
        Menu {
            favoriteButton
            Button {
                updateState(played: currentItem.userData?.played != true)
            } label: {
                Label(currentItem.userData?.played == true ? "Mark unwatched" : "Mark watched", glyph: .check)
            }
            .accessibilityValue(currentItem.userData?.played == true ? "Watched" : "Unwatched")
            .accessibilityIdentifier("videoWatchedButton")

            if currentItem.isPlayable, !currentItem.isAudio {
                videoDownloadAction
            }

        } label: {
            Label("More actions", glyph: .moreHorizontal)
        }
        .disabled(isSavingState || isLoadingItem)
        .accessibilityIdentifier("titleActionsMenu")
    }

    @ViewBuilder private var videoDownloadStatus: some View {
        switch store.offline.downloadStates[currentItem.id] {
        case .queued:
            Label("Download queued", glyph: .download).font(.caption)
        case .downloading(let received, let total):
            VStack(alignment: .leading, spacing: 6) {
                if let total, total > 0 { ProgressView(value: Double(received), total: Double(total)) }
                Text("Downloading · \(ByteCountFormatter.string(fromByteCount: received, countStyle: .file))")
                    .font(.caption.monospacedDigit())
            }
            .accessibilityIdentifier("videoDownloadProgress")
        case .downloaded:
            Label("Downloaded to this device", glyph: .downloadDone).font(.caption)
                .accessibilityIdentifier("videoDownloadedStatus")
        case .failed(let message):
            Label(message, glyph: .warning).font(.caption)
                .accessibilityIdentifier("videoDownloadError")
        case .cancelled, .none:
            EmptyView()
        }
    }

    @ViewBuilder private var videoDownloadAction: some View {
        switch store.offline.downloadStates[currentItem.id] {
        case .downloaded:
            Button("Remove Download", glyph: .trash, role: .destructive) {
                store.offline.remove(id: currentItem.id)
            }
            .accessibilityIdentifier("removeVideoDownloadButton")
        case .queued, .downloading:
            Button("Cancel Download", glyph: .close) { store.offline.cancel(id: currentItem.id) }
                .accessibilityIdentifier("cancelVideoDownloadButton")
        default:
            Button("Download to Device", glyph: .download) {
                Task { await store.downloadVideo(item: currentItem) }
            }
            .disabled(store.isOffline || !LuminaOfflineLibrary.supportsVideoDownload(currentItem))
            .accessibilityHint("Save this video for offline playback on this device.")
            .accessibilityIdentifier("downloadVideoButton")
        }
    }

    private func updateState(played: Bool? = nil, favorite: Bool? = nil) {
        guard !isSavingState, !isLoadingItem else { return }
        isSavingState = true
        stateError = nil
        Task {
            do {
                updatedItem = try await store.updateItemState(item: currentItem, played: played, favorite: favorite)
            } catch {
                stateError = (error as? LocalizedError)?.errorDescription ?? "Your activity could not be saved. Check your connection and try again."
            }
            isSavingState = false
        }
    }

    private func refreshItem() async {
        isLoadingItem = true
        defer { isLoadingItem = false }
        do {
            updatedItem = try await store.loadItem(item)
            stateError = nil
        } catch {
            stateError = (error as? LocalizedError)?.errorDescription ?? "The latest activity could not load. Check your connection and reopen this title."
        }
    }

    @ViewBuilder private var detailHeader: some View {
        if currentItem.isAudio {
            detailBillboard
        } else {
            detailBillboard
                .containerRelativeFrame(.vertical) { length, _ in length }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("detailHero")
        }
    }

    private var detailBillboard: some View {
        MediaBillboard(item: currentItem, store: store, context: "", showsOverview: false, showsContext: false,
                       showsMetadata: currentItem.isAudio, artworkFirst: !currentItem.isAudio,
                       compactMinimumHeight: currentItem.isAudio ? nil : 0,
                       wideMinimumHeight: currentItem.isAudio ? nil : 0) {
            if !currentItem.isAudio { detailHeroMetadata }
            if currentItem.isPlayable {
                playbackActions
            } else {
                Text("Playback is unavailable for this title.")
                    .font(.callout)
            }
        }
    }

    private var detailHeroMetadata: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { detailHeroMetadataContent }
            VStack(alignment: .leading, spacing: 8) { detailHeroMetadataContent }
        }
        .font(.caption.weight(.medium))
        .textCase(.uppercase)
        .tracking(1)
        .foregroundStyle(.white.opacity(0.85))
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var detailHeroMetadataContent: some View {
        if let year = currentItem.productionYear { Text(String(year)) }
        if let rating = currentItem.officialRating, !rating.isEmpty { Text(rating) }
        if currentItem.durationSeconds > 0 {
            Text(Duration.seconds(currentItem.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
        }
    }

    @ViewBuilder
    private var playbackActions: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 12) {
                playbackButton
                if !currentItem.isAudio { NativeWatchlistButton(item: currentItem, store: store) }
            }
            VStack(alignment: .leading, spacing: 12) {
                playbackButton
                if !currentItem.isAudio { NativeWatchlistButton(item: currentItem, store: store) }
            }
        }
    }

    private var playbackButton: some View {
        Button {
            preparePlayback(forceTranscode: false)
        } label: {
            HStack(spacing: 8) {
                if isPreparingPlayback {
                    ProgressView()
                }
                Label(playLabel, glyph: .play, variant: .solid, size: 24)
            }
        }
        .buttonStyle(LuminaMediaActionStyle(primary: true))
        .disabled(isPreparingPlayback)
        .accessibilityIdentifier("playButton")
    }

    private var playLabel: String {
        currentItem.resumeSeconds > 5 ? "Resume" : "Play"
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard currentItem.isPlayable, !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            do {
                playbackRequest = try await store.preparePlayback(for: currentItem, forceTranscode: forceTranscode)
            } catch {
                playbackError = currentItem.isAudio
                    ? (error as? LocalizedError)?.errorDescription ?? "Playback could not start. Check your connection and try again."
                    : forceTranscode
                        ? "Compatibility conversion could not start. Check the server and try again."
                        : "Playback could not start. Check your connection or play a downloaded copy."
            }
            isPreparingPlayback = false
        }
    }
}

@MainActor
private struct LibraryLoadingView: View {
    private let placeholders = [0, 1, 2, 3, 4, 5]

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        LazyVGrid(
            columns: libraryGridColumns(for: dynamicTypeSize),
            alignment: .leading,
            spacing: 28
        ) {
            ForEach(placeholders, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(LuminaTheme.ink.opacity(0.08))
                    .aspectRatio(2 / 3, contentMode: .fit)
                    .redacted(reason: .placeholder)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading video library")
    }
}

private func libraryGridColumns(for dynamicTypeSize: DynamicTypeSize) -> [GridItem] {
    if dynamicTypeSize.isAccessibilitySize {
        return [GridItem(.flexible(minimum: 0, maximum: 220), spacing: 20, alignment: .top)]
    }
    return [GridItem(.adaptive(minimum: 96, maximum: 166), spacing: 10, alignment: .top)]
}

@MainActor
private struct EmptyLibraryView: View {
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            LuminaIcon(glyph: .movies, variant: .solid, size: 40)
                .foregroundStyle(LuminaTheme.accentInk)
                .accessibilityHidden(true)
            Text("Your video library is quiet.")
                .font(.system(.title2, design: .serif))
                .fontWeight(.semibold)
                .italic()
            Text("Add video to Lumina, then refresh to see it here.")
                .foregroundStyle(LuminaTheme.muted)
            Button("Refresh", action: onRefresh)
                .buttonStyle(LuminaQuietActionStyle())
        }
        .padding(.vertical, 24)
    }
}

@MainActor
private struct InlineErrorView: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(message, glyph: .warning)
                .font(.callout)
                .foregroundStyle(LuminaTheme.accentInk)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isStaticText)
            Button("Try again", action: onRetry)
                .buttonStyle(LuminaQuietActionStyle())
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 14))
    }
}

private func mediaAccessibilityLabel(
    for item: MediaItem,
    presentation: HomeRailCaptionPresentation = .standard
) -> String {
    let caption = HomeRailCaption(item: item, presentation: presentation)
    var parts = [caption.accessibilityLabel]
    if item.resumeSeconds > 0, presentation == .standard {
        parts.append(item.durationSeconds > item.resumeSeconds ? "in progress" : "played")
    }
    return parts.filter { !$0.isEmpty }.joined(separator: ", ")
}
