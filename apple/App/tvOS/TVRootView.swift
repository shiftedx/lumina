import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore

/// The living-room client: a small native shell over the shared Lumina session and library store.
public struct TVRootView: View {
    private let store: LuminaStore
    @State private var path: [MediaItem] = []
    @State private var playbackRequest: PlaybackRequest?
    @State private var presentedPlaybackRequest: PlaybackRequest?
    @State private var libraryGeneration = 0
    @AppStorage("lumina.tv.lastSuccessfulServerOrigin") private var lastSuccessfulServerOrigin = ""
    @State private var audioSession = NativeAudioSession()
    @Environment(\.scenePhase) private var scenePhase

    public init(store: LuminaStore) {
        self.store = store
    }

    public var body: some View {
        NavigationStack(path: $path) {
            Group {
                if store.session == nil {
                    TVConnectionView(store: store)
                } else {
                    TVLibraryView(store: store, isAtNavigationRoot: path.isEmpty) { item in
                        path.append(item)
                    } onPlayback: { request in
                        playbackRequest = request
                    } onSignOut: {
                        path.removeAll()
                    }
                    .id(libraryGeneration)
                }
            }
            .onPlayPauseCommand(perform: toggleAudioPlayback)
            .navigationDestination(for: MediaItem.self) { item in
                if item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" {
                    TVMusicDetailView(item: item, store: store) { path.append($0) }
                } else if item.type == "Episode" {
                    TVEpisodeCollectionDestination(
                        item: item,
                        store: store,
                        onOpen: { path.append($0) },
                        onPlayback: { playbackRequest = $0 }
                    )
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else if item.type == "BoxSet" || item.type == "Playlist" {
                    TVMovieCollectionView(item: item, store: store, onOpen: { path.append($0) })
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else if item.isCollection {
                    TVVideoCollectionView(item: item, store: store, onOpen: { path.append($0) }, onPlayback: { playbackRequest = $0 })
                        .onPlayPauseCommand(perform: toggleAudioPlayback)
                } else {
                    TVDetailView(item: item, store: store, onOpen: { path.append($0) }) { request in
                        playbackRequest = request
                    }
                    .onPlayPauseCommand(perform: toggleAudioPlayback)
                }
            }
        }
        .preferredColorScheme(.dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil { audioSession.stop() }
        }
        .task(id: store.session?.accessToken) {
            if store.session != nil { audioSession.restoreFromTVRoot(store: store) }
        }
        .onChange(of: store.session?.serverURL) { _, origin in
            if let origin { lastSuccessfulServerOrigin = origin.absoluteString }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                ArtworkImageCache.shared.setBackgrounded(false)
                store.setCatalogBackgrounded(false)
                audioSession.resumeAfterActivation()
                Task {
                    await store.refreshNavigationCategories()
                    await audioSession.refreshSharedQueue()
                }
            } else if phase == .background {
                ArtworkImageCache.shared.setBackgrounded(true)
                store.setCatalogBackgrounded(true)
                audioSession.enterBackground()
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didReceiveMemoryWarningNotification)) { _ in
            ArtworkImageCache.shared.trimMemory()
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.session != nil { NativeNowPlayingBar(store: store) }
        }
        .nativeVideoResumeChoice(request: $playbackRequest, store: store, onReady: { presentedPlaybackRequest = $0 })
        .fullScreenCover(item: $presentedPlaybackRequest, onDismiss: {
            playbackRequest = nil
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store, onReturnHome: {
                path.removeAll()
                libraryGeneration += 1
            })
        }
        .environment(audioSession)
        .tint(LuminaTheme.ink)
    }

    private func toggleAudioPlayback() {
        // Keep background music reachable without intercepting a music detail's
        // command to start its album when no session exists yet.
        guard playbackRequest == nil else {
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-cover-present")
            #endif
            return
        }
        let isAtNavigationRoot = path.isEmpty
        Task {
            if isAtNavigationRoot {
                if await audioSession.toggleTVPausedRestoreIfNeeded() {
                    #if LUMINA_UI_TESTING
                    audioSession.recordTVRestoreRemoteOutcome("root-restore-handled")
                    #endif
                    return
                }
            }
            guard audioSession.playback != nil else {
                #if LUMINA_UI_TESTING
                audioSession.recordTVRestoreRemoteOutcome("root-no-playback")
                #endif
                return
            }
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-toggle")
            #endif
            await audioSession.toggle()
            #if LUMINA_UI_TESTING
            audioSession.recordTVRestoreRemoteOutcome("root-toggle-finished")
            #endif
        }
    }
}

@MainActor
struct TVMovieCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @State private var children = [MediaItem]()
    @State private var isLoading = true
    @State private var errorMessage: String?

    private var title: String { item.type == "BoxSet" ? "Movies" : "Titles" }

    var body: some View {
        TVHeroScrollView {
            VStack(alignment: .leading, spacing: 34) {
                MediaBillboard(item: item, store: store, context: "", showsOverview: false, showsContext: false,
                               artworkFirst: true, wideMinimumHeight: 620) {
                    Text(title)
                        .font(.system(size: 25, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.9))
                }
                if isLoading {
                    ProgressView("Loading \(title.lowercased())…")
                        .frame(maxWidth: .infinity, minHeight: 240)
                } else if let errorMessage {
                    TVMessageView(title: "Could not load this collection", message: errorMessage, actionTitle: "Try again") {
                        Task { await load() }
                    }
                } else if children.isEmpty {
                    TVMessageView(title: item.type == "Playlist" ? "No playable video titles" : "No movies yet", message: item.type == "Playlist" ? "This list has no video titles Lumina can play here." : "Add movies to Lumina, then refresh this collection.", actionTitle: "Refresh") {
                        Task { await load() }
                    }
                } else {
                    Text(title)
                        .font(LuminaTheme.editorial(34, relativeTo: .title2, italic: true))
                        .foregroundStyle(LuminaTheme.ink)
                        .padding(.horizontal, 82)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(children) { child in
                            TVMediaCard(item: child, store: store, poster: true, showsCaption: false, artworkWidth: 480) { onOpen(child) }
                                .accessibilityIdentifier("movieCollectionItem-\(child.id)")
                        }
                    }
                    .padding(.horizontal, 82)
                    .focusSection()
                }
            }
            .padding(.bottom, 76)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("movieCollectionScreen")
        .task(id: item.id) { await load() }
    }

    private func load() async {
        isLoading = children.isEmpty
        errorMessage = nil
        do {
            children = try await store.loadChildren(of: item)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "Check your connection and try again."
        }
        isLoading = false
    }
}

private struct TVEpisodeCollectionDestination: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    @State private var series: MediaItem?
    @State private var couldNotResolveSeries = false

    var body: some View {
        Group {
            if let series {
                TVVideoCollectionView(
                    item: series,
                    store: store,
                    onOpen: onOpen,
                    onPlayback: onPlayback,
                    initialSeasonID: item.seasonID,
                    initialEpisodeID: item.id
                )
            } else if couldNotResolveSeries {
                TVDetailView(item: item, store: store, onOpen: onOpen, onPlayback: onPlayback)
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
                series = try await store.seriesCollection(id: seriesID, useCache: true)
            } catch {
                couldNotResolveSeries = true
            }
        }
    }
}

private struct TVConnectionView: View {
    let store: LuminaStore
    @State private var screen = SignInScreen.welcome
    @State private var handoff: TVPairingHandoff?
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var tvPairingRequest: TVPairingRequest?
    @State private var tvPairingServerOrigin: String?
    @State private var tvPairingPhase = TVPairingPhase.idle
    @State private var tvPairingError: String?
    @State private var tvPairingGeneration = UUID()
    @State private var showcaseSlides = [PublicShowcaseSlide]()
    @State private var usesRemoteShowcase = false
    @State private var displayedShowcaseID = Self.bundledShowcaseSlides[0].id
    @State private var decodedShowcaseIDs = Set<String>()
    @State private var decodedPosterIDs = Set<String>()
    @State private var showcaseOrigin: String?
    @State private var helpPresented = false
    @AppStorage("lumina.tv.lastSuccessfulServerOrigin") private var lastSuccessfulServerOrigin = ""
    @FocusState private var focusedField: Field?
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver

    private enum SignInScreen { case welcome, nearby, manual }

    private enum Field: Hashable {
        case server, pairingStart, manual, username, password, connect, pairingRegenerate, pairingCancel, help
    }

    private enum TVPairingPhase: Equatable {
        case idle
        case starting
        case waiting
        case completing
        case denied
        case expired
        case failed
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                TVConnectionShowcaseArtwork(
                    slides: artworkSlides,
                    displayedID: displayedShowcaseID,
                    onBackdropDecoded: artworkDecoded
                )
                .frame(width: geometry.size.width, height: geometry.size.height)
                .clipped()
                .accessibilityIdentifier("connectionShowcase")

                LinearGradient(
                    stops: [
                        .init(color: LuminaTheme.theater.opacity(0.98), location: 0),
                        .init(color: LuminaTheme.theater.opacity(0.91), location: 0.38),
                        .init(color: LuminaTheme.theater.opacity(0.54), location: 0.72),
                        .init(color: LuminaTheme.theater.opacity(0.72), location: 1),
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
                .allowsHitTesting(false)
                .accessibilityHidden(true)

                ZStack {
                    ForEach(artworkSlides) { slide in
                        TVConnectionPoster(slide: slide) { posterDecoded(slide.id) }
                            .opacity(displayedShowcaseID == slide.id ? 1 : 0.001)
                    }
                }
                        .frame(
                            width: min(430, max(300, geometry.size.width * 0.255)),
                            height: min(645, max(450, geometry.size.width * 0.3825))
                        )
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .trailing)
                        .padding(.trailing, max(72, geometry.size.width * 0.08))
                        .offset(y: -8)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)

                connectionForm
                    .frame(width: min(560, max(500, geometry.size.width * 0.34)), alignment: .leading)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                    .padding(.leading, max(80, geometry.size.width * 0.095))
                    .padding(.vertical, 64)

                if Self.isAutomationRun {
                    // Keep this verification hook out of the production accessibility tree.
                    Color.clear
                        .frame(width: 1, height: 1)
                        .accessibilityElement()
                        .accessibilityIdentifier("connectionShowcaseReady")
                        .accessibilityValue(displayedShowcaseID)
                        .allowsHitTesting(false)
                }
            }
            .background(LuminaTheme.theater)
        }
        .ignoresSafeArea()
        .defaultFocus($focusedField, .pairingStart)
        .task {
            prefillLastSuccessfulServer()
        }
        .task(id: validServerOrigin) {
            await loadShowcase()
        }
        .task(id: slideshowKey) {
            await advanceShowcaseWhenReady()
        }
        .task(id: handoff) {
            guard let handoff, screen == .nearby else { return }
            await acceptPhone(handoff)
        }
        .task(id: tvPairingPollID) {
            await pollTVPairingIfNeeded()
        }
        .onChange(of: validServerOrigin) { oldOrigin, newOrigin in
            guard oldOrigin != newOrigin else { return }
            resetShowcase(for: newOrigin)
            if tvPairingPhase == .starting || (tvPairingRequest != nil && tvPairingServerOrigin != newOrigin) {
                cancelTVPairing(message: "Server changed. Start phone sign-in again.")
            }
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .background else { return }
            if screen == .nearby { returnToWelcome(); return }
            tvPairingGeneration = UUID()
            if tvPairingPhase == .starting {
                tvPairingPhase = .idle
                tvPairingError = nil
            }
        }
        .onDisappear {
            tvPairingGeneration = UUID()
        }
        .onExitCommand {
            if screen != .welcome { returnToWelcome() }
        }
        .sheet(isPresented: $helpPresented, onDismiss: {
            focusedField = .help
        }) {
            TVConnectionHelp()
        }
    }

    private var connectionForm: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                LuminaBrandMark()
                    .frame(width: 44, height: 27)
                    .luminaBrandMarkBaseline()
                Text("LUMINA")
                    .font(LuminaTheme.editorial(32, relativeTo: .title3))
                    .tracking(2.4)
            }
            .foregroundStyle(.white)

            switch screen {
            case .welcome:
                welcomeActions
            case .nearby:
                nearbySignIn
            case .manual:
                manualSignIn
            }
        }
        .foregroundStyle(.white)
        .focusSection()
    }

    private var welcomeActions: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Sign in").font(.title2.weight(.semibold))
            Button("Sign in with phone", action: showNearbySignIn)
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .controlSize(.large)
                .accessibilityIdentifier("tvPairingStartButton")
                .focused($focusedField, equals: .pairingStart)
            Button("Enter server and password") {
                screen = .manual
                focusedField = .server
            }
            .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
            .accessibilityIdentifier("tvManualSignInButton")
            .focused($focusedField, equals: .manual)

            Button("Connection help") { helpPresented = true }
                .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
                .accessibilityIdentifier("connectionHelpButton")
                .focused($focusedField, equals: .help)
        }
    }

    private var manualSignIn: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Sign in manually").font(.title3.weight(.semibold))
            TextField("Server address", text: $server)
                .textContentType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("serverURLField")
                .focused($focusedField, equals: .server)

            if let request = tvPairingRequest {
                tvPairingView(request)
            } else {
                manualSignInFields
                if validServerOrigin != nil {
                    Button("Get phone code", action: beginTVPairing)
                        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
                        .accessibilityIdentifier("tvPairingKnownServerButton")
                        .disabled(tvPairingPhase == .starting || store.isBusy)
                }

                if let tvPairingError {
                    Label(tvPairingError, glyph: .warning)
                        .foregroundStyle(LuminaTheme.accentInk)
                        .font(.callout)
                        .accessibilityLabel("Phone sign-in error: \(tvPairingError)")
                        .accessibilityIdentifier("tvPairingStatus")
                }
            }
        }
    }

    private var nearbySignIn: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Sign in with your phone").font(.title3.weight(.semibold))
            if let request = tvPairingRequest {
                Text("Confirm this code in Lumina on your phone.")
                Text(request.code)
                    .font(.system(.title, design: .monospaced, weight: .semibold))
                    .accessibilityIdentifier("tvPairingCode")
            } else if let handoff, tvPairingError == nil {
                TVPairingQRCode(url: handoff.link)
                    .frame(width: 256, height: 256)
                    .accessibilityIdentifier("tvPairingQRCode")
                    .accessibilityLabel("QR code for phone sign-in")
                Text("In Lumina on your phone, go to Account → Connect a TV and scan.")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if tvPairingRequest != nil || tvPairingError != nil {
                Text(tvPairingStatusText)
                    .font(.callout)
                    .accessibilityIdentifier("tvPairingStatus")
            }
            HStack(spacing: 18) {
                if [.denied, .expired, .failed].contains(tvPairingPhase) {
                    Button("Get a new code", action: showNearbySignIn)
                        .buttonStyle(LuminaQuietActionStyle(primary: true))
                        .focused($focusedField, equals: .pairingRegenerate)
                }
                Button("Cancel", action: returnToWelcome)
                    .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
                    .focused($focusedField, equals: .pairingCancel)
                    .accessibilityIdentifier("tvPairingCancelButton")
                    .disabled(tvPairingPhase == .completing)
            }
        }
    }

    private func showNearbySignIn() {
        cancelTVPairing()
        screen = .nearby
        handoff = TVPairingHandoff()
        focusedField = .pairingCancel
    }

    private func returnToWelcome() {
        handoff = nil
        cancelTVPairing()
        screen = .welcome
        focusedField = .pairingStart
    }

    private func acceptPhone(_ handoff: TVPairingHandoff) async {
        let generation = tvPairingGeneration
        do {
            try await handoff.awaitPhone { origin in
                let request = try await store.beginTVPairing(server: origin.absoluteString, deviceName: "Apple TV")
                guard !Task.isCancelled, tvPairingGeneration == generation else { throw CancellationError() }
                tvPairingServerOrigin = origin.absoluteString
                tvPairingRequest = request
                tvPairingPhase = .waiting
                return request
            }
        } catch {
            guard !Task.isCancelled, tvPairingGeneration == generation else { return }
            tvPairingRequest = nil
            tvPairingServerOrigin = nil
            tvPairingPhase = .failed
            tvPairingError = "Couldn't connect to the phone or its server. Get a new code or sign in manually."
            focusedField = .pairingRegenerate
        }
    }

    private var manualSignInFields: some View {
        Group {
            TextField("Lumina username", text: $username)
                .textContentType(.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("usernameField")
                .focused($focusedField, equals: .username)

            SecureField("Password or app password", text: $password)
                .textContentType(.password)
                .accessibilityIdentifier("passwordField")
                .focused($focusedField, equals: .password)

            if let error = store.errorMessage, !error.isEmpty {
                Label(error, glyph: .warning)
                    .foregroundStyle(LuminaTheme.accentInk)
                    .font(.callout)
                    .accessibilityLabel("Connection error: \(error)")
                    .accessibilityIdentifier("connectionError")
            }

            Button(action: connect) {
                HStack(spacing: 12) {
                    if store.isBusy { ProgressView() }
                    Text(store.isBusy ? "Connecting…" : "Connect")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .controlSize(.large)
            .accessibilityIdentifier("connectButton")
            .focused($focusedField, equals: .connect)
            .disabled(!canConnect || store.isBusy)
        }
    }

    @ViewBuilder
    private func tvPairingView(_ request: TVPairingRequest) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Scan with Lumina on your phone")
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white)

            HStack(alignment: .center, spacing: 28) {
                if let origin = tvPairingServerOrigin,
                   let serverURL = URL(string: origin),
                   let qrLink = try? request.qrLink(serverURL: serverURL) {
                    TVPairingQRCode(url: qrLink)
                        .frame(width: 200, height: 200)
                        .accessibilityIdentifier("tvPairingQRCode")
                        .accessibilityLabel("QR code. Scan with Lumina on your phone.")
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("Confirmation code")
                        .font(.callout.weight(.medium))
                        .foregroundStyle(.white.opacity(0.72))
                    Text(request.code)
                        .font(.system(.title2, design: .monospaced, weight: .semibold))
                        .accessibilityIdentifier("tvPairingCode")
                        .accessibilityLabel(request.code)
                        .accessibilityHint("Confirmation code")
                        .accessibilityValue(request.code)
                    Text(tvPairingStatusText)
                        .font(.callout)
                        .foregroundStyle(tvPairingPhase == .denied || tvPairingPhase == .expired || tvPairingPhase == .failed ? LuminaTheme.accentInk : .white.opacity(0.75))
                        .accessibilityIdentifier("tvPairingStatus")
                }
            }

            HStack(spacing: 18) {
                Button("Get a new code", action: beginTVPairing)
                    .buttonStyle(LuminaQuietActionStyle())
                    .accessibilityIdentifier("tvPairingRegenerateButton")
                    .focused($focusedField, equals: .pairingRegenerate)
                    .disabled(tvPairingPhase == .starting || tvPairingPhase == .completing)

                Button("Cancel") { cancelTVPairing() }
                    .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
                    .accessibilityIdentifier("tvPairingCancelButton")
                    .focused($focusedField, equals: .pairingCancel)
                    .disabled(tvPairingPhase == .completing)
            }
        }
        .focusSection()
    }

    private var canConnect: Bool {
        !server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !password.isEmpty
    }

    private var validServerOrigin: String? {
        Self.normalizedServerOrigin(server)
    }

    private var tvPairingPollID: String? {
        guard tvPairingPhase == .waiting || tvPairingPhase == .completing,
              tvPairingRequest != nil,
              tvPairingServerOrigin != nil,
              scenePhase == .active else { return nil }
        return tvPairingRequest?.id
    }

    private var tvPairingStatusText: String {
        if let tvPairingError { return tvPairingError }
        switch tvPairingPhase {
        case .idle, .starting:
            return "Preparing sign-in…"
        case .waiting:
            return "Waiting for approval…"
        case .completing:
            return "Finishing sign-in…"
        case .denied:
            return "Approval was declined. Get a new code to try again."
        case .expired:
            return "This code expired. Get a new code to try again."
        case .failed:
            return "Couldn't finish sign-in. Get a new code to try again."
        }
    }

    private func beginTVPairing() {
        guard let origin = validServerOrigin, !store.isBusy, tvPairingPhase != .starting else { return }
        let generation = UUID()
        tvPairingGeneration = generation
        tvPairingRequest = nil
        tvPairingServerOrigin = nil
        tvPairingPhase = .starting
        tvPairingError = nil
        Task {
            do {
                let request = try await store.beginTVPairing(server: origin, deviceName: "Apple TV")
                guard !Task.isCancelled, tvPairingGeneration == generation else { return }
                tvPairingServerOrigin = origin
                tvPairingRequest = request
                tvPairingPhase = isTVPairingExpired(request) ? .expired : .waiting
                focusedField = .pairingCancel
            } catch is CancellationError {
                guard tvPairingGeneration == generation else { return }
                tvPairingPhase = .idle
            } catch {
                guard tvPairingGeneration == generation else { return }
                tvPairingPhase = .failed
                tvPairingError = "Couldn't start phone sign-in. Check the server address and try again."
            }
        }
    }

    private func pollTVPairingIfNeeded() async {
        guard let request = tvPairingRequest,
              let origin = tvPairingServerOrigin,
              tvPairingPhase == .waiting,
              scenePhase == .active else { return }
        let generation = tvPairingGeneration

        while isCurrentTVPairing(request, generation: generation), !isTVPairingExpired(request) {
            do {
                let status = try await store.tvPairingStatus(server: origin, request: request)
                guard isCurrentTVPairing(request, generation: generation) else { return }
                switch status {
                case .pending:
                    try await Task.sleep(for: .seconds(max(1, request.pollIntervalSeconds)))
                case .approved:
                    tvPairingPhase = .completing
                    try await store.completeTVPairing(server: origin, request: request)
                    lastSuccessfulServerOrigin = origin
                    return
                case .denied:
                    tvPairingPhase = .denied
                    return
                case .expired, .consumed:
                    tvPairingPhase = .expired
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                guard isCurrentTVPairing(request, generation: generation) else { return }
                tvPairingPhase = .failed
                tvPairingError = "Couldn't check phone approval. Get a new code to try again."
                return
            }
        }

        if isCurrentTVPairing(request, generation: generation), isTVPairingExpired(request) {
            tvPairingPhase = .expired
        }
    }

    private func isCurrentTVPairing(_ request: TVPairingRequest, generation: UUID) -> Bool {
        !Task.isCancelled &&
            tvPairingGeneration == generation &&
            tvPairingRequest?.id == request.id &&
            (screen == .nearby || tvPairingServerOrigin == validServerOrigin) &&
            scenePhase == .active
    }

    private func cancelTVPairing(message: String? = nil) {
        tvPairingGeneration = UUID()
        tvPairingRequest = nil
        tvPairingServerOrigin = nil
        tvPairingPhase = .idle
        tvPairingError = message
        focusedField = .server
    }

    private func isTVPairingExpired(_ request: TVPairingRequest) -> Bool {
        let fractionalFormatter = ISO8601DateFormatter()
        fractionalFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let expiresAt = fractionalFormatter.date(from: request.expiresAt)
            ?? ISO8601DateFormatter().date(from: request.expiresAt)
        guard let expiresAt else { return true }
        return expiresAt <= Date()
    }

    private static let bundledShowcaseSlides = [
        TVConnectionShowcaseSlide.bundled(assetName: "WelcomeCoast"),
        TVConnectionShowcaseSlide.bundled(assetName: "WelcomeOrbit"),
        TVConnectionShowcaseSlide.bundled(assetName: "WelcomeDunes"),
    ]

    private var remoteShowcaseSlides: [TVConnectionShowcaseSlide] {
        showcaseSlides.map(TVConnectionShowcaseSlide.remote)
    }

    private var activeShowcaseSlides: [TVConnectionShowcaseSlide] {
        usesRemoteShowcase && !remoteShowcaseSlides.isEmpty
            ? remoteShowcaseSlides
            : Self.bundledShowcaseSlides
    }

    private var displayedShowcase: TVConnectionShowcaseSlide? {
        activeShowcaseSlides.first { $0.id == displayedShowcaseID }
    }

    private var pendingShowcase: TVConnectionShowcaseSlide? {
        let activeSlides = activeShowcaseSlides
        guard !activeSlides.isEmpty else { return nil }
        guard let index = activeSlides.firstIndex(where: { $0.id == displayedShowcaseID }) else {
            return activeSlides.first
        }
        return activeSlides[(index + 1) % activeSlides.count]
    }

    /// Keep the fallback visible while the first remote slide decodes, then preload the next remote slide
    /// before the nine-second rotation advances.
    private var artworkSlides: [TVConnectionShowcaseSlide] {
        let candidates: [TVConnectionShowcaseSlide]
        if usesRemoteShowcase {
            candidates = [displayedShowcase, pendingShowcase].compactMap { $0 }
        } else {
            candidates = [displayedShowcase, pendingShowcase].compactMap { $0 } + Array(remoteShowcaseSlides.prefix(2))
        }
        return candidates.reduce(into: []) { result, slide in
            if !result.contains(where: { $0.id == slide.id }) { result.append(slide) }
        }
    }

    private var shouldRotateArtwork: Bool {
        activeShowcaseSlides.count > 1 &&
            !reduceMotion &&
            !voiceOver &&
            scenePhase == .active &&
            displayedShowcase != nil &&
            pendingShowcase != nil &&
            isShowcaseReady(pendingShowcase)
    }

    private var slideshowKey: String {
        "\(displayedShowcaseID)-\(pendingShowcase?.id ?? "")-\(shouldRotateArtwork)-\(decodedShowcaseIDs.sorted().joined(separator: ","))"
    }

    private func prefillLastSuccessfulServer() {
        guard !Self.isAutomationRun,
              server.isEmpty,
              let savedOrigin = Self.normalizedServerOrigin(lastSuccessfulServerOrigin) else { return }
        server = savedOrigin
    }

    private func loadShowcase() async {
        guard let origin = validServerOrigin else { return }
        do {
            try await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled, validServerOrigin == origin else { return }
            let fetched = try await store.publicShowcase(server: origin)
            guard !Task.isCancelled, validServerOrigin == origin else { return }
            let upcoming = fetched.filter { $0.kind == "movie" && $0.section == "upcoming" && $0.posterURL != nil }
            let ranked = Self.rankedShowcase(upcoming.count > 1 ? upcoming : fetched)
            guard showcaseOrigin == origin else { return }
            showcaseSlides = ranked
        } catch is CancellationError {
            return
        } catch {
            // A public showcase should never block, expose, or distract from sign-in.
        }
    }

    private func resetShowcase(for origin: String?) {
        showcaseOrigin = origin
        showcaseSlides = []
        usesRemoteShowcase = false
        displayedShowcaseID = Self.bundledShowcaseSlides[0].id
        decodedShowcaseIDs = []
        decodedPosterIDs = []
    }

    private func artworkDecoded(_ id: String) {
        guard remoteShowcaseSlides.contains(where: { $0.id == id }) else { return }
        decodedShowcaseIDs.insert(id)
        promoteInitialShowcaseIfReady(id)
    }

    private func posterDecoded(_ id: String) {
        guard remoteShowcaseSlides.contains(where: { $0.id == id }) else { return }
        decodedPosterIDs.insert(id)
        promoteInitialShowcaseIfReady(id)
    }

    private func promoteInitialShowcaseIfReady(_ id: String) {
        guard !usesRemoteShowcase,
              let initialRemote = remoteShowcaseSlides.first,
              initialRemote.id == id,
              isShowcaseReady(initialRemote) else { return }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.8)) {
            usesRemoteShowcase = true
            displayedShowcaseID = initialRemote.id
        }
    }

    private func advanceShowcaseWhenReady() async {
        guard shouldRotateArtwork else { return }
        let currentID = displayedShowcaseID
        do {
            try await Task.sleep(for: .seconds(9))
        } catch {
            return
        }
        guard !Task.isCancelled,
              shouldRotateArtwork,
              displayedShowcaseID == currentID,
              let nextID = pendingShowcase?.id,
              isShowcaseReady(pendingShowcase) else { return }
        withAnimation(.easeInOut(duration: 0.8)) {
            displayedShowcaseID = nextID
        }
    }

    private func connect() {
        guard canConnect, !store.isBusy else { return }
        cancelTVPairing()
        let requestedOrigin = validServerOrigin
        store.clearError()
        Task {
            await store.connect(server: server, username: username, password: password)
            password = ""
            if store.session != nil, let requestedOrigin {
                lastSuccessfulServerOrigin = requestedOrigin
            }
        }
    }

    private static var isAutomationRun: Bool {
        let arguments = ProcessInfo.processInfo.arguments
        return arguments.contains("--ui-testing") || arguments.contains("--reset-session")
    }

    private static func normalizedServerOrigin(_ value: String) -> String? {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), ["http", "https"].contains(scheme), parts.host != nil,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { return nil }
        parts.scheme = scheme
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        return parts.url?.absoluteString
    }

    private static func rankedShowcase(_ slides: [PublicShowcaseSlide]) -> [PublicShowcaseSlide] {
        slides.sorted { left, right in
            let leftRank = showcaseRank(left)
            let rightRank = showcaseRank(right)
            return leftRank == rightRank ? left.id < right.id : leftRank > rightRank
        }
    }

    private static func showcaseRank(_ slide: PublicShowcaseSlide) -> Int {
        let isUpcoming = slide.section?.caseInsensitiveCompare("upcoming") == .orderedSame
        let isMovie = slide.kind.caseInsensitiveCompare("movie") == .orderedSame
        switch (isUpcoming, isMovie, slide.posterURL != nil) {
        case (true, true, true): return 4
        case (_, true, true): return 3
        case (true, true, false): return 2
        case (_, true, false): return 1
        default: return 0
        }
    }

    private func isShowcaseReady(_ slide: TVConnectionShowcaseSlide?) -> Bool {
        guard let slide else { return false }
        guard let remote = slide.remoteSlide else { return true }
        guard decodedShowcaseIDs.contains(slide.id) else { return false }
        return remote.posterURL == nil || decodedPosterIDs.contains(slide.id)
    }
}

private struct TVPairingQRCode: View {
    let url: URL

    private static let context = CIContext(options: [.cacheIntermediates: false])

    var body: some View {
        Group {
            if let image = qrImage {
                Image(uiImage: image)
                    .interpolation(.none)
                    .resizable()
                    .scaledToFit()
                    .clipShape(.rect(cornerRadius: 8))
            } else {
                Image(systemName: "qrcode")
                    .font(.system(size: 88))
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.white, in: .rect(cornerRadius: 12))
            }
        }
    }

    private var qrImage: UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(url.absoluteString.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }

        // Four clear modules plus integer scaling keeps the camera-safe quiet zone
        // and crisp QR cells on a television display.
        let quietZone: CGFloat = 4
        let extent = output.extent
        let background = CIImage(color: CIColor.white)
            .cropped(to: extent.insetBy(dx: -quietZone, dy: -quietZone))
        let padded = output.composited(over: background)
        let scale: CGFloat = 10
        let scaled = padded.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cgImage = Self.context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}

private struct TVConnectionShowcaseSlide: Identifiable {
    enum Artwork {
        case bundled(assetName: String)
        case remote(PublicShowcaseSlide)
    }

    let artwork: Artwork

    static func bundled(assetName: String) -> Self {
        Self(artwork: .bundled(assetName: assetName))
    }

    static func remote(_ slide: PublicShowcaseSlide) -> Self {
        Self(artwork: .remote(slide))
    }

    var id: String {
        switch artwork {
        case .bundled(let assetName): "asset:\(assetName)"
        case .remote(let slide): "remote:\(slide.id)"
        }
    }

    var remoteSlide: PublicShowcaseSlide? {
        guard case .remote(let slide) = artwork else { return nil }
        return slide
    }
}

private struct TVConnectionShowcaseArtwork: View {
    let slides: [TVConnectionShowcaseSlide]
    let displayedID: String?
    let onBackdropDecoded: (String) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            LuminaTheme.theater
            ForEach(slides) { slide in
                switch slide.artwork {
                case .bundled(let assetName):
                    Image(assetName)
                        .resizable()
                        .scaledToFill()
                        .opacity(displayedID == slide.id ? 1 : 0.001)
                case .remote(let remote):
                    AsyncImage(url: remote.backdropURL, transaction: .init(animation: reduceMotion ? nil : .easeInOut(duration: 0.8))) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .scaledToFill()
                                .opacity(displayedID == slide.id ? 1 : 0.001)
                                .onAppear { onBackdropDecoded(slide.id) }
                        case .empty, .failure:
                            Color.clear
                        @unknown default:
                            Color.clear
                        }
                    }
                }
            }
        }
        .clipped()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private struct TVConnectionPoster: View {
    let slide: TVConnectionShowcaseSlide
    let onDecoded: () -> Void

    var body: some View {
        Group {
            if let remote = slide.remoteSlide {
                AsyncImage(url: remote.posterURL ?? remote.backdropURL) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill().onAppear(perform: onDecoded)
                    case .empty, .failure:
                        Color.clear
                    @unknown default:
                        Color.clear
                    }
                }
            } else {
                Color.clear
            }
        }
        .clipShape(.rect(cornerRadius: 18))
        .shadow(color: .black.opacity(0.45), radius: 30, y: 18)
        .accessibilityHidden(true)
    }
}

private struct TVConnectionHelp: View {
    @Environment(\.dismiss) private var dismiss
    @FocusState private var doneFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            Text("Connection help")
                .font(.title2.weight(.semibold))

            Text("For phone sign-in, connect both devices to the same network and sign in to Lumina on your phone first.")
                .font(.body)
                .foregroundStyle(LuminaTheme.muted)
                .fixedSize(horizontal: false, vertical: true)

            Text("For manual sign-in, enter your server address and Lumina account. If you use two-factor authentication, enter an app password.")
                .font(.body)
                .foregroundStyle(LuminaTheme.muted)
                .fixedSize(horizontal: false, vertical: true)

            Button("Done") { dismiss() }
                .buttonStyle(LuminaQuietActionStyle())
                .controlSize(.large)
                .accessibilityIdentifier("connectionHelpDoneButton")
                .focused($doneFocused)
        }
        .padding(64)
        .frame(maxWidth: 720, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("connectionHelpSheet")
        .defaultFocus($doneFocused, true)
        .onExitCommand { dismiss() }
    }
}

private enum TVDestination: String, CaseIterable {
    case home, library, streaming, requests
    var title: String { rawValue.capitalized }
}

private enum TVLibraryCategory: String, CaseIterable {
    case movies, tv, anime, music, streaming

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "TV Shows"
        case .anime: "Anime"
        case .music: "Music"
        case .streaming: "Saved Streaming"
        }
    }

    var tileArtwork: String {
        switch self {
        case .movies: "WelcomeCoast"
        case .tv: "WelcomeOrbit"
        case .anime: "WelcomeDunes"
        case .music, .streaming: "WelcomeCoast"
        }
    }

    var glyph: LuminaGlyph {
        switch self {
        case .movies: .movies
        case .tv, .anime: .series
        case .music: .music
        case .streaming: .video
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

private struct TVLibraryView: View {
    let store: LuminaStore
    let isAtNavigationRoot: Bool
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let onSignOut: () -> Void
    @State private var destination: TVDestination = .home
    @State private var homeSearchPresented = false
    @State private var requestsUnavailable = false
    @State private var homeLayout = LuminaHomeLayout()
    @State private var homeFeed = LuminaHomeFeed()
    @State private var homeSessionScope: TVHomeSessionScope?
    @State private var homeHeroID: String?
    @State private var homeCategory: HomeBrowseCategory = .all
    @State private var libraryCategory: TVLibraryCategory?
    @State private var lastLibraryTile: TVLibraryCategory?
    @State private var videoBrowse: [TVLibraryCategory: TVVideoBrowseState] = [:]
    @State private var audioBrowse = TVAudioBrowseState()
    @State private var deviceSettingsPresented = false
    @State private var contentFocusRequest = 0
    @State private var libraryFocusRequest = 0
    @FocusState private var focusedDestination: TVDestination?
    @FocusState private var focusedLibraryTile: TVLibraryCategory?
    @FocusState private var watchlistFocused: Bool
    @FocusState private var searchFocused: Bool
    @FocusState private var accountFocused: Bool

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                Group {
                    switch destination {
                    case .home:
                        if homeSessionScope == TVHomeSessionScope(store.session) {
                            TVHomeView(
                                store: store,
                                layout: homeLayout,
                                feed: homeFeed,
                                heroSelection: $homeHeroID,
                                category: $homeCategory,
                                onOpen: onOpen,
                                onPlayback: onPlayback,
                                focusRequest: contentFocusRequest,
                                onRequestHeaderFocus: { focusActiveHeader() },
                                header: { navigationHeader(topInset: max(geometry.safeAreaInsets.top, geometry.frame(in: .global).minY)) }
                            )
                        } else {
                            ProgressView("Loading Home…")
                        }
                    case .library:
                        if libraryCategory == .streaming {
                            TVStreamingView(store: store, onOpen: onOpen, savedOnly: true)
                        } else if libraryCategory == .music {
                            TVAudioView(
                                store: store,
                                onOpen: onOpen,
                                browse: $audioBrowse,
                                focusRequest: contentFocusRequest,
                                restoreFocusRequest: libraryFocusRequest,
                                onRequestHeaderFocus: { focusedDestination = .library }
                            )
                        } else if let libraryCategory {
                            TVVideoView(
                                store: store,
                                onOpen: onOpen,
                                browse: browseState(for: libraryCategory),
                                focusRequest: contentFocusRequest,
                                restoreFocusRequest: libraryFocusRequest
                            )
                            .id(libraryCategory)
                        } else {
                            libraryHub
                        }
                    case .streaming:
                        TVStreamingView(store: store, onOpen: onOpen)
                    case .requests:
                        TVRequestsView(
                            store: store,
                            focusRequest: contentFocusRequest,
                            onOpen: onOpen,
                            onPlayback: onPlayback,
                            onOpenVault: { id in try await store.libraryItem(id: id) }
                        )
                    }
                }
                .padding(.top, contentTopPadding)
                if destination != .home { navigationHeader() }
            }
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .sheet(isPresented: $homeSearchPresented, onDismiss: { searchFocused = true }) {
            TVHomeSearchView(store: store) { item in
                homeSearchPresented = false
                onOpen(item)
            }
        }
        .fullScreenCover(isPresented: $deviceSettingsPresented) {
            DeviceSettingsView(
                store: store,
                onSignOut: {
                    deviceSettingsPresented = false
                    onSignOut()
                    Task { await store.signOut() }
                },
                onClose: {
                    deviceSettingsPresented = false
                    Task { await store.refreshNavigationCategories() }
                }
            )
            .background(LuminaTheme.theater.ignoresSafeArea())
            .preferredColorScheme(.dark)
        }
        .task(id: TVHomeSessionScope(store.session)) {
            homeLayout.configure(session: store.session)
            homeFeed.configure(session: store.session)
            homeSessionScope = TVHomeSessionScope(store.session)
        }
        .onChange(of: availableLibraryTiles) { _, available in
            guard let libraryCategory, !available.contains(libraryCategory) else { return }
            self.libraryCategory = nil
            if destination == .library { focusedLibraryTile = available.first }
        }
        .onChange(of: store.streamingAvailable) { _, available in
            if !available, destination == .streaming {
                selectDestination(showsSavedStreaming ? .library : .home)
                focusActiveHeader()
            }
        }
        .onChange(of: store.requestsAvailable) { _, available in
            if !available, destination == .requests {
                selectDestination(.home)
                requestsUnavailable = true
            }
        }
        .alert("Requests unavailable", isPresented: $requestsUnavailable) {
            Button("OK") { focusActiveHeader() }
        } message: {
            Text(store.navigationLoadState == .failed
                 ? "Lumina couldn't check Requests access. Open Library to retry."
                 : "Requests is no longer available for this account.")
        }
        .onExitCommand(perform: backAction)
    }

    private func navigationHeader(topInset: CGFloat = 0) -> some View {
        header
            .focusedValue(\.tvHeroRegion, true)
            .padding(.horizontal, 80)
            .padding(.top, 12 + topInset)
            .padding(.bottom, 12)
            .background {
                LinearGradient(stops: [
                    .init(color: .black.opacity(0.75), location: 0),
                    .init(color: .black.opacity(0.5), location: 0.65),
                    .init(color: .clear, location: 1),
                ], startPoint: .top, endPoint: .bottom)
                    .padding(.bottom, -32)
                    .ignoresSafeArea(.container, edges: [.top, .horizontal])
                    .allowsHitTesting(false)
            }
            .focusSection()
            .onMoveCommand { direction in
                guard direction == .down else { return }
                if destination == .library, libraryCategory == nil, let tile = preferredLibraryTile {
                    focusedLibraryTile = tile
                } else if destination != .library || libraryCategory == .music {
                    contentFocusRequest += 1
                }
            }
            .ignoresSafeArea(.container, edges: .horizontal)
    }

    private var backAction: (() -> Void)? {
        guard isAtNavigationRoot else { return nil }
        if destination == .library, let libraryCategory {
            return {
                lastLibraryTile = libraryCategory
                self.libraryCategory = nil
                focusedLibraryTile = preferredLibraryTile
            }
        }
        let headerFocused = focusedDestination != nil || watchlistFocused || searchFocused || accountFocused
        if !headerFocused { return { focusActiveHeader() } }
        if destination != .home || homeCategory == .watchlist {
            return {
                selectDestination(.home)
                focusActiveHeader()
            }
        }
        return nil
    }

    private var contentTopPadding: CGFloat {
        switch destination {
        case .home: 0
        case .library: libraryCategory == .music ? 0 : 112
        case .streaming, .requests: 112
        }
    }

    private func selectDestination(_ next: TVDestination) {
        libraryCategory = nil
        if next == .home { homeCategory = .all }
        destination = next
    }

    private func focusActiveHeader() {
        if destination == .home, homeCategory == .watchlist {
            watchlistFocused = true
        } else {
            focusedDestination = destination
        }
    }

    private func browseState(for category: TVLibraryCategory) -> Binding<TVVideoBrowseState> {
        Binding(
            get: { videoBrowse[category] ?? TVVideoBrowseState(kind: category.videoKind) },
            set: { videoBrowse[category] = $0 }
        )
    }

    private var availableLibraryTiles: [TVLibraryCategory] {
        TVLibraryCategory.allCases.filter {
            $0 == .streaming ? showsSavedStreaming : store.availableLibraryCategories.contains($0.rawValue)
        }
    }

    private var preferredLibraryTile: TVLibraryCategory? {
        if let lastLibraryTile, availableLibraryTiles.contains(lastLibraryTile) { return lastLibraryTile }
        return availableLibraryTiles.first
    }

    private var libraryHub: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                Text("Your library")
                    .font(LuminaTheme.editorial(64, relativeTo: .largeTitle))
                    .accessibilityAddTraits(.isHeader)
                if !availableLibraryTiles.isEmpty {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 28), count: 4), spacing: 28) {
                        ForEach(availableLibraryTiles, id: \.self) { category in
                            Button {
                                lastLibraryTile = category
                                libraryCategory = category
                                libraryFocusRequest += 1
                            } label: {
                                libraryTile(category)
                            }
                            .buttonStyle(TVNavigationButtonStyle())
                            .focusEffectDisabled()
                            .focused($focusedLibraryTile, equals: category)
                            .accessibilityLabel(category.title)
                            .accessibilityIdentifier("homeBrowse-\(category == .streaming ? "savedStreaming" : category.rawValue)")
                        }
                    }
                    .focusSection()
                }
                if store.navigationLoadState == .failed {
                    Text(availableLibraryTiles.isEmpty ? "Couldn't load your libraries." : "Couldn't refresh your libraries.")
                        .foregroundStyle(LuminaTheme.muted)
                    Button("Retry") { Task { await store.refreshNavigationCategories() } }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("libraryRetryButton")
                } else if availableLibraryTiles.isEmpty {
                    if store.navigationLoadState == .loaded {
                        Text("No libraries are available for this account.")
                            .foregroundStyle(LuminaTheme.muted)
                    } else {
                        ProgressView("Loading libraries…")
                    }
                }
            }
            .padding(.horizontal, 100)
            .padding(.vertical, 54)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("libraryHubScreen")
    }

    private func libraryTile(_ category: TVLibraryCategory) -> some View {
        let focused = focusedLibraryTile == category
        return Color.clear
            .aspectRatio(4 / 3, contentMode: .fit)
            .overlay {
                if category == .music {
                    RadialGradient(colors: [LuminaTheme.brand.opacity(0.7), LuminaTheme.theater],
                                   center: .topTrailing, startRadius: 15, endRadius: 420)
                } else {
                    Image(category.tileArtwork)
                        .resizable()
                        .scaledToFill()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .overlay {
                LinearGradient(colors: [.black.opacity(0.08), .black.opacity(0.88)], startPoint: .top, endPoint: .bottom)
            }
            .overlay(alignment: .bottomLeading) {
                Label(category.title, glyph: category.glyph, size: 29)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(22)
            }
            .clipShape(.rect(cornerRadius: 18))
            .overlay {
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(focused ? Color.white : .clear, lineWidth: 4)
            }
            .zIndex(focused ? 1 : 0)
    }

    private var header: some View {
        HStack(spacing: 0) {
            HStack(spacing: 24) {
                LuminaBrandMark()
                    .frame(width: 42, height: 26)
                    .accessibilityHidden(false)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Lumina")
                    .accessibilityAddTraits(.isImage)
                    .accessibilityIdentifier("homeMasthead")
                Spacer(minLength: 0)
            }
            .frame(width: 260, alignment: .leading)

            HStack(spacing: 20) {
                navigationTab(.home, identifier: "homeNavigationButton", selected: destination == .home && homeCategory == .all)
                navigationTab(.library, identifier: "libraryNavigationButton")
                Button("Watchlist") {
                    selectDestination(.home)
                    homeCategory = .watchlist
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: destination == .home && homeCategory == .watchlist))
                .focusEffectDisabled()
                .focused($watchlistFocused)
                .accessibilityValue(destination == .home && homeCategory == .watchlist ? "Selected" : "")
                .accessibilityAddTraits(destination == .home && homeCategory == .watchlist ? .isSelected : [])
                .accessibilityIdentifier("homeBrowse-watchlist")
                if store.requestsAvailable {
                    navigationTab(.requests, identifier: "requestsButton")
                }
                if store.streamingAvailable {
                    navigationTab(.streaming, identifier: "homeBrowse-streaming")
                }
            }
            .frame(maxWidth: .infinity)

            HStack(spacing: 10) {
                Button { homeSearchPresented = true } label: {
                    Label("Search", glyph: .search, size: 28).labelStyle(.iconOnly)
                        .frame(width: 64, height: 64)
                }
                .buttonStyle(TVNavigationButtonStyle())
                .focusEffectDisabled()
                .focused($searchFocused)
                .accessibilityIdentifier("homeSearchButton")

                Button { deviceSettingsPresented = true } label: {
                    LuminaIcon(glyph: .userSettings,
                               variant: deviceSettingsPresented ? .solid : .outline, size: 28)
                        .foregroundStyle(deviceSettingsPresented ? LuminaTheme.accentInk : accountFocused ? LuminaTheme.ink : LuminaTheme.muted)
                        .frame(width: 64, height: 64)
                }
                .buttonStyle(TVNavigationButtonStyle())
                .focusEffectDisabled()
                .focused($accountFocused)
                .accessibilityLabel("Account")
                .accessibilityValue(deviceSettingsPresented ? "Selected" : "")
                .accessibilityAddTraits(deviceSettingsPresented ? .isSelected : [])
                .accessibilityIdentifier("accountButton")
            }
            .frame(width: 260, alignment: .trailing)
        }
        .frame(height: 64)
    }

    private var showsSavedStreaming: Bool {
        !store.streamingAvailable && store.availableLibraryCategories.contains("youtube")
    }

    private func navigationTab(_ target: TVDestination, identifier: String, selected: Bool? = nil) -> some View {
        let isSelected = selected ?? (destination == target)
        return Button(target.title) { selectDestination(target) }
            .buttonStyle(LuminaUnderlineTabStyle(selected: isSelected, alignsWithMasthead: true))
            .focusEffectDisabled()
            .focused($focusedDestination, equals: target)
            .accessibilityValue(isSelected ? "Selected" : "")
            .accessibilityAddTraits(isSelected ? .isSelected : [])
            .accessibilityIdentifier(identifier)
    }
}

/// Keep remote focus legible without placing a system capsule over the artwork.
private struct TVNavigationButtonStyle: ButtonStyle {
    @Environment(\.isFocused) private var isFocused

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.7 : 1)
            .scaleEffect(isFocused ? 1.04 : 1)
    }
}

private struct TVHomeSessionScope: Hashable {
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

/// Keep the visible Video controls and the last focused card while another tab
/// is selected. The destination view is recreated on a tab change, so its own
/// `@State` would otherwise discard the person's search and place focus back
/// in the header when they return with Menu.
private struct TVVideoBrowseState {
    var query = ""
    var kind: VideoBrowseKind = .movies
    var sort: VideoSort = .name
    var filters = VideoFilters()
    var focusedItemID: String?
}

/// Audio uses the same root-owned lifetime as Video. `focusedItemID` includes
/// the featured album so Menu can return to the actual prior selection.
private struct TVAudioBrowseState {
    var kind: MusicBrowseKind = .albums
    var query = ""
    var focusedItemID: String?
}

private struct TVHomeSearchView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var results: [MediaItem] = []
    @State private var loading = false
    @State private var message: String?
    @State private var searchTask: Task<Void, Never>?
    @State private var searchGeneration = 0
    @FocusState private var searchFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                HStack {
                    TextField("Search films, shows, and music", text: $query)
                        .focused($searchFocused)
                        .onSubmit { search() }
                        .accessibilityIdentifier("homeSearchField")
                    Button("Search", glyph: .search, size: 24) { search() }
                        .buttonStyle(LuminaUnderlineTabStyle())
                    Button("Done") { dismiss() }.buttonStyle(LuminaUnderlineTabStyle())
                }
                if loading { ProgressView("Searching…") }
                if let message { Text(message).foregroundStyle(LuminaTheme.muted) }
                TVRail(title: "Results", items: results, store: store, poster: true,
                    identifierPrefix: "homeSearchResult", onOpen: onOpen)
            }.padding(64)
        }
        .background(LuminaTheme.theater)
        .accessibilityIdentifier("homeSearchScreen")
        .defaultFocus($searchFocused, true)
        .onChange(of: query) { _, _ in
            searchTask?.cancel()
            searchGeneration += 1
            loading = false
            results = []
            message = nil
        }
        .onDisappear { searchTask?.cancel(); searchGeneration += 1 }
        .onExitCommand { dismiss() }
    }

    private func search() {
        searchTask?.cancel()
        searchGeneration += 1
        let generation = searchGeneration
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { results = []; message = nil; return }
        loading = true; message = nil
        searchTask = Task {
            do {
                let found = try await store.searchHome(query: term)
                guard !Task.isCancelled, generation == searchGeneration else { return }
                results = found
                loading = false
                if found.isEmpty { message = "No results. Try another title." }
            } catch {
                guard !Task.isCancelled, generation == searchGeneration else { return }
                loading = false
                message = "Search could not finish. Try again."
            }
        }
    }
}

private struct TVHomeView<Header: View>: View {
    let store: LuminaStore
    let layout: LuminaHomeLayout
    let feed: LuminaHomeFeed
    @Binding var heroSelection: String?
    @Binding var category: HomeBrowseCategory
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    let onRequestHeaderFocus: () -> Void
    @ViewBuilder let header: Header
    @Environment(\.scenePhase) private var scenePhase
    #if LUMINA_UI_TESTING
    @Environment(NativeAudioSession.self) private var audioSession
    @State private var uiTestingHomeLifecycleRefreshCount = 0
    #endif

    @State private var loadingInitialLibrary = false
    @State private var initialHomeLoadOwner: TVHomeSessionScope?
    @State private var homeRefreshTask: Task<Void, Never>?
    @State private var homeRefreshID: UUID?
    @State private var needsHomeRefresh = false
    @State private var hiddenRecommendationID: String?
    @State private var showRecommendationUndo = false
    private var refinement: HomeRefinementResult {
        feed.refined(HomeRefinement(category: category), heroItems: store.featuredItems)
    }
    private func filtered(_ items: [MediaItem]) -> [MediaItem] {
        feed.refined(HomeRefinement(category: category), heroItems: items).items
    }


    private var shelves: [HomeShelfPreference] {
        layout.shelves
    }

    var body: some View {
        let snapshot = refinement
        GeometryReader { geometry in
            TVHeroScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if !snapshot.items.isEmpty {
                        TVFeaturedView(
                            store: store,
                            items: snapshot.items,
                            heroSelection: $heroSelection,
                            onOpen: onOpen,
                            onPlayback: onPlayback,
                            focusRequest: focusRequest,
                            onRequestHeaderFocus: onRequestHeaderFocus,
                            minimumHeight: min(900, max(620, geometry.size.height * 0.82)),
                            onWatchlistChanged: { await feed.load() }
                        )
                            .focusedValue(\.tvHeroRegion, true)
                            .focusSection()
                    } else if category != .all {
                        Text("No watchlist titles are available. Choose Home or add something to your watchlist.")
                            .foregroundStyle(LuminaTheme.muted)
                            .padding(.horizontal, 80)
                            .padding(.top, geometry.safeAreaInsets.top + 112)
                    } else if store.isHomeBusy || (!feed.hasLoaded && feed.isLoading) {
                        ProgressView("Loading Home…").frame(maxWidth: .infinity, minHeight: 650)
                    } else {
                        TVMessageView(title: "Your next great watch starts here", message: store.errorMessage ?? "Add media to Lumina to fill Home with your household library.", actionTitle: "Refresh") {
                            Task { await reloadHome() }
                        }
                        .padding(.top, geometry.safeAreaInsets.top + 112)
                    }
                    if feed.hasLoaded {
                        ForEach(shelves.filter(\.visible)) { shelf in
                        let richRows = snapshot.rows.filter { $0.shelfID == shelf.id }
                        if !richRows.isEmpty {
                        ForEach(richRows) { row in
                            TVRail(title: row.displayTitle, items: row.items, store: store,
                                   poster: row.shape == .poster,
                                   identifierPrefix: row.shelfID == .continueWatching ? "resumeItem" : row.shelfID == .nextUp ? "nextUpItem" : "homeRow-\(row.id)",
                                   onOpen: onOpen, feed: feed,
                                   onHide: { hiddenRecommendationID = $0; showRecommendationUndo = true })
                                .focusSection()
                        }
                        } else if feed.completedShelfIDs?.contains(shelf.id) != true {
                            homeShelf(shelf.id)
                        }
                        }
                    } else {
                        ForEach(shelves.filter(\.visible)) { shelf in
                            homeShelf(shelf.id)
                        }
                    }
                    if let error = feed.errorMessage {
                        TVMessageView(title: "Some Home shelves could not load", message: error, actionTitle: "Try again") {
                            Task { await reloadHome() }
                        }
                    }
                }
                .padding(.bottom, 80)
                .overlay(alignment: .top) {
                    header
                }
            }
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .accessibilityIdentifier("homeScreen")
        .alert("Recommendation hidden", isPresented: $showRecommendationUndo) {
            Button("Undo") { if let hiddenRecommendationID { Task { await feed.undoHide(hiddenRecommendationID) } } }
            Button("Done", role: .cancel) {}
        }
        #if LUMINA_UI_TESTING
        .accessibilityValue("\(audioSession.uiTestingTVRestoreDiagnostics) lifecycleRefreshes=\(uiTestingHomeLifecycleRefreshCount)")
        #endif
        .task(id: TVHomeSessionScope(store.session)) {
            let scope = TVHomeSessionScope(store.session)
            let preservesAdmission = initialHomeLoadOwner == scope
            cancelHomeRefresh()
            needsHomeRefresh = preservesAdmission ? needsHomeRefresh || scenePhase == .background : scenePhase == .background
            initialHomeLoadOwner = nil
            guard let owner = scope else { return }
            let needsInitialLibraryLoad = !layout.hasLoaded || !feed.hasLoaded
            loadingInitialLibrary = needsInitialLibraryLoad
            defer { loadingInitialLibrary = false }
            await reloadHome()
            guard !Task.isCancelled, TVHomeSessionScope(store.session) == owner else { return }
            initialHomeLoadOwner = owner
            refreshAfterForegrounding()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                needsHomeRefresh = true
                cancelHomeRefresh()
            } else if phase == .active {
                refreshAfterForegrounding()
            }
        }
        .onChange(of: store.resumeItems) { _, _ in
            let richResume = feed.rows.first(where: { $0.shelfID == .continueWatching })?.items
                .filter { $0.isPlayable && !$0.isAudio } ?? []
            let isFreshRichResumePublication = feed.lastLoadSucceeded
                && feed.completedShelfIDs?.contains(.continueWatching) == true
                && store.resumeItems == richResume
            if feed.hasLoaded, !loadingInitialLibrary, !isFreshRichResumePublication { Task { await feed.load() } }
        }
        .onDisappear { cancelHomeRefresh(preservingAdmission: true) }
    }

    private func refreshAfterForegrounding() {
        guard scenePhase == .active, needsHomeRefresh, homeRefreshTask == nil,
              let owner = TVHomeSessionScope(store.session), initialHomeLoadOwner == owner else { return }
        let id = UUID()
        needsHomeRefresh = false
        #if LUMINA_UI_TESTING
        uiTestingHomeLifecycleRefreshCount += 1
        #endif
        homeRefreshID = id
        homeRefreshTask = Task {
            defer {
                if homeRefreshID == id {
                    homeRefreshTask = nil
                    homeRefreshID = nil
                }
            }
            await reloadHome(refreshingLegacyRecent: true)
        }
    }

    private func cancelHomeRefresh(preservingAdmission: Bool = false) {
        if preservingAdmission, homeRefreshTask != nil { needsHomeRefresh = true }
        homeRefreshTask?.cancel()
        homeRefreshTask = nil
        homeRefreshID = nil
    }

    private var expectedCompatibilityShelves: Set<HomeShelfID> {
        Set(shelves.lazy.filter(\.visible).map(\.id)).intersection([.continueWatching, .nextUp, .newInLibrary])
    }

    private func reloadHome(refreshingLegacyRecent: Bool = false) async {
        let coldCompatibility = !layout.hasLoaded || !feed.hasLoaded
        let expected = expectedCompatibilityShelves
        let legacyRecent = refreshingLegacyRecent ? expected.intersection([.newInLibrary]) : []
        let richCompatibility = expected.subtracting(legacyRecent)
        async let savedLayout: Void = layout.load()
        async let rows: Void = feed.load()
        async let navigation: Void = store.refreshNavigationCategories()
        if coldCompatibility {
            // Keep the established cold remote focus path. After a rich snapshot
            // exists, foreground refreshes use its completed shelf facts.
            async let legacy: Void = store.loadHomeFallback(for: expected)
            _ = await (savedLayout, rows, navigation, legacy)
        } else {
            _ = await (savedLayout, rows, navigation)
        }
        guard !Task.isCancelled else { return }
        let missing = await store.reconcileRichHomeRows(
            feed.rows,
            completedShelfIDs: feed.completedShelfIDs,
            loadSucceeded: feed.lastLoadSucceeded,
            expectedCompatibilityShelves: richCompatibility
        )
        let fallback = missing.union(legacyRecent)
        if !coldCompatibility, !fallback.isEmpty {
            await store.loadHomeFallback(for: fallback)
        }
    }

    @ViewBuilder private func homeShelf(_ id: HomeShelfID) -> some View {
        switch id {
        case .continueWatching:
            if !store.resumeItems.isEmpty {
                TVRail(title: id.title, items: filtered(store.resumeItems), store: store, onOpen: onOpen).focusSection()
            }
        case .nextUp:
            if !store.nextUpItems.isEmpty {
                TVRail(title: id.title, items: filtered(store.nextUpItems), store: store, identifierPrefix: "nextUpItem", onOpen: onOpen).focusSection()
            }
        case .newInLibrary:
            if !store.recentItems.isEmpty {
                TVRail(title: id.title, items: filtered(store.recentItems), store: store, poster: true, identifierPrefix: "recentItem", onOpen: onOpen).focusSection()
            }
        default:
            EmptyView()
        }
    }
}

private struct TVVideoView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Binding var browse: TVVideoBrowseState
    let focusRequest: Int
    let restoreFocusRequest: Int?
    @State private var filtersPresented = false
    @State private var pendingRestoredItemID: String?
    @FocusState private var focusedItemID: String?

    @State private var alphabetLetter: String?
    @State private var alphabetRequestKey: String?
    @State private var fastScrolling = false
    @State private var lastScrollSample: (offset: Int, time: TimeInterval)?
    @State private var alphabetTask: Task<Void, Never>?
    @State private var lastVisibleItemID: String?
    @State private var lastFocusedIndex = 0
    @State private var movingForward = true
    @FocusState private var alphabetFocused: Bool

    private var requestKey: String { "\(browse.kind.rawValue)-\(browse.sort.rawValue)-\(browse.query)-\(browse.filters.collectionID ?? "")-\(browse.filters.genre)-\(browse.filters.year ?? 0)-\(browse.filters.unwatchedOnly)-\(browse.filters.favoritesOnly)" }
    private var hasFilters: Bool { browse.filters != VideoFilters() }
    private var hasNonCollectionFilters: Bool {
        VideoFilters(genre: browse.filters.genre, year: browse.filters.year, unwatchedOnly: browse.filters.unwatchedOnly,
                     favoritesOnly: browse.filters.favoritesOnly, resolution: browse.filters.resolution, hdr: browse.filters.hdr,
                     audio: browse.filters.audio, imaxOnly: browse.filters.imaxOnly) != VideoFilters()
    }
    private var canBrowseAlphabet: Bool {
        alphabetRequestKey == requestKey && browse.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && browse.sort == .name && [.movies, .shows, .anime].contains(browse.kind)
            && store.supportsLibraryAlphabet && !store.libraryAlphabet.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            HStack(spacing: 28) {
                if browse.query.isEmpty {
                    Menu {
                        Picker("Sort", selection: $browse.sort) {
                            ForEach(VideoSort.allCases, id: \.self) { Text($0.label).tag($0) }
                        }
                    } label: {
                        HStack(spacing: 10) {
                            Text(browse.sort == .name ? "Title A–Z" : browse.sort.label)
                            LuminaIcon(glyph: .chevronDown, size: 16)
                        }
                    }
                    .buttonStyle(LuminaUnderlineTabStyle())
                    .accessibilityIdentifier("videoSortPicker")
                    .accessibilityLabel("Sort library")
                    .accessibilityValue(browse.sort.label)
                } else {
                    Text("By relevance").font(.body).foregroundStyle(LuminaTheme.muted)
                }
                Button { filtersPresented = true } label: {
                    Label(hasFilters ? "Filters applied" : "Filters", glyph: .filter)
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: hasFilters))
                .accessibilityIdentifier("videoFiltersButton")
                .accessibilityValue(hasFilters ? filterSummary : "No filters")
                .onMoveCommand { direction in
                    if direction == .down { focusVisibleGridItem() }
                }
                if hasNonCollectionFilters {
                    Button("Clear filters") {
                        browse.filters = VideoFilters(collectionID: browse.filters.collectionID, collectionName: browse.filters.collectionName)
                    }
                        .buttonStyle(LuminaUnderlineTabStyle())
                        .accessibilityLabel("Clear filters")
                        .accessibilityIdentifier("videoClearFiltersButton")
                }
                if browse.kind == .movies, browse.filters.collectionID != nil {
                    Button("Clear collection") {
                        browse.filters.collectionID = nil
                        browse.filters.collectionName = nil
                    }
                    .buttonStyle(LuminaUnderlineTabStyle())
                    .accessibilityIdentifier("videoClearCollectionFilterButton")
                }
                Spacer()
                TextField("Search video", text: $browse.query)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .frame(width: 340)
                    .accessibilityIdentifier("searchField")
            }
            .padding(.horizontal, 80)
            .focusSection()

            ScrollViewReader { proxy in
                HStack(alignment: .center, spacing: 0) {
                    ScrollView {
                        if store.isBusy && store.items.isEmpty {
                            ProgressView("Loading video…").frame(maxWidth: .infinity, minHeight: 360)
                        } else if store.items.isEmpty {
                            TVMessageView(title: "No matching videos", message: store.errorMessage ?? "Try another search or choose a different category.", actionTitle: "Refresh") {
                                Task { await reload(forceRefresh: true) }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 42) {
                                TVCollectionGrid(title: nil, items: store.items, store: store,
                                                 focusedItemID: $focusedItemID, onOpen: onOpen, onItemDidAppear: itemDidAppear,
                                                 onVisible: { lastVisibleItemID = $0 })
                            }
                            .padding(.horizontal, 80)
                            .padding(.top, 24)
                            .focusSection()
                            .accessibilityIdentifier("libraryPosterWall")
                            if store.hasMore {
                                Button(store.isCatalogLoading ? "Loading library…" : "Load more") { Task { await store.loadMore() } }
                                    .buttonStyle(LuminaQuietActionStyle())
                                    .disabled(store.isBusy || store.isCatalogLoading)
                                    .accessibilityIdentifier("videoLoadMoreButton")
                                    .padding(32)
                            }
                            if let error = store.errorMessage {
                                Text(error).foregroundStyle(LuminaTheme.accentInk).padding(24)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityIdentifier("libraryScreen")
                    #if LUMINA_UI_TESTING
                    .accessibilityValue("catalog=\(store.items.count) loading=\(store.isCatalogLoading)")
                    #endif
                    .onScrollGeometryChange(for: Int.self) { geometry in
                        Int(geometry.contentOffset.y / 128)
                    } action: { previousOffset, offset in
                        if offset != previousOffset {
                            let forward = offset > previousOffset
                            if movingForward != forward { movingForward = forward }
                        }
                        let now = ProcessInfo.processInfo.systemUptime
                        if let lastScrollSample {
                            let elapsed = now - lastScrollSample.time
                            if elapsed > 0, elapsed < 0.7,
                               Double(abs(offset - lastScrollSample.offset) * 128) / elapsed > 900 {
                                fastScrolling = true
                            }
                        }
                        lastScrollSample = (offset, now)
                    }
                    .onScrollPhaseChange { _, phase in
                        if phase == .idle { fastScrolling = false; lastScrollSample = nil }
                    }
                    if canBrowseAlphabet {
                        TVLibraryAlphabetRail(
                            letters: store.libraryAlphabet.map(\.letter),
                            selection: $alphabetLetter,
                            expanded: fastScrolling || alphabetFocused,
                            isFocused: alphabetFocused,
                            onReturn: {
                                alphabetFocused = false
                                focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
                            },
                            onSelect: { letter in
                                guard canBrowseAlphabet,
                                      let anchor = store.libraryAlphabet.first(where: { $0.letter == letter }) else { return }
                                alphabetTask?.cancel()
                                alphabetTask = Task {
                                    guard let firstID = await store.jumpLibrary(to: anchor), !Task.isCancelled else { return }
                                    await Task.yield()
                                    guard !Task.isCancelled else { return }
                                    proxy.scrollTo(firstID, anchor: .top)
                                    await Task.yield()
                                    guard !Task.isCancelled else { return }
                                    focusedItemID = firstID
                                    alphabetFocused = false
                                }
                            }
                        )
                        .focused($alphabetFocused)
                        .onExitCommand {
                            alphabetFocused = false
                            focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
                        }
                        .padding(.trailing, 32)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .onChange(of: focusedItemID) { _, itemID in
            if let itemID {
                browse.focusedItemID = itemID
                if let index = displayedItems.firstIndex(where: { $0.id == itemID }) {
                    let forward = index >= lastFocusedIndex
                    if movingForward != forward { movingForward = forward }
                    lastFocusedIndex = index
                }
                lastVisibleItemID = itemID
            }
        }
        .onChange(of: restoreFocusRequest, initial: true) { _, request in
            guard request != nil else { return }
            pendingRestoredItemID = browse.focusedItemID
            restoreLastSelectionIfAvailable()
        }
        .onChange(of: displayedItems.map(\.id)) { _, _ in restoreLastSelectionIfAvailable() }
        .task(id: "\(lastVisibleItemID ?? "")-\(store.items.count)-\(movingForward)", priority: .utility) {
            // Warm the poster window at its displayed rendition within the shared cache and four-load budgets.
            await store.prefetchArtwork(after: lastVisibleItemID, in: store.items, maxWidth: 480,
                                        count: 36, movingForward: movingForward)
        }
        .sheet(isPresented: $filtersPresented) {
            TVVideoFiltersView(filters: browse.filters, store: store, allowsCollectionFilter: browse.kind == .movies) { browse.filters = $0 }
        }
        .onDisappear { alphabetTask?.cancel() }
        .task(id: requestKey) {
            let requestedKey = requestKey
            alphabetTask?.cancel()
            lastVisibleItemID = nil
            lastFocusedIndex = 0
            movingForward = true
            alphabetLetter = nil
            alphabetRequestKey = nil
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await reload()
            guard !Task.isCancelled, requestedKey == requestKey else { return }
            alphabetRequestKey = requestedKey
        }
    }

    private var filterSummary: String {
        [browse.filters.collectionName, browse.filters.genre.isEmpty ? nil : browse.filters.genre, browse.filters.year.map(String.init), browse.filters.unwatchedOnly ? "Unwatched" : nil, browse.filters.favoritesOnly ? "Favorites" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private var displayedItems: [MediaItem] {
        store.items
    }

    private func focusVisibleGridItem() {
        focusedItemID = displayedItems.first { $0.id == browse.focusedItemID }?.id ?? displayedItems.first?.id
    }

    private func itemDidAppear(_ item: MediaItem) {
        let loadMoreAnchor = store.items.suffix(6).first?.id
        guard store.hasMore, !store.isCatalogLoading, item.id == loadMoreAnchor else { return }
        Task { await store.loadMore() }
    }

    private func restoreLastSelectionIfAvailable() {
        guard let itemID = pendingRestoredItemID,
              displayedItems.contains(where: { $0.id == itemID }) else { return }
        focusedItemID = itemID
        pendingRestoredItemID = nil
    }

    private func reload(forceRefresh: Bool = false) async {
        await store.loadLibrary(search: browse.query, kind: browse.kind, sort: browse.sort, filters: browse.filters,
                                forceRefresh: forceRefresh, preloadCatalog: true)
    }
}

private enum TVStreamingTab: Hashable {
    case saved, recommendations, live
}

private struct TVStreamingView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let savedOnly: Bool
    @Environment(\.scenePhase) private var scenePhase
    @State private var savedLibrary: LuminaStreamingLibrary
    @State private var remoteFeed: LuminaHomeFeed
    @State private var selectedTab: TVStreamingTab = .saved
    @State private var selectedRemote: HomeExternalItem?
    @State private var hasUserSelectedTab = false
    @FocusState private var focusedTab: TVStreamingTab?

    init(store: LuminaStore, onOpen: @escaping (MediaItem) -> Void, savedOnly: Bool = false) {
        self.store = store
        self.onOpen = onOpen
        self.savedOnly = savedOnly
        _savedLibrary = State(initialValue: LuminaStreamingLibrary(session: store.session))
        _remoteFeed = State(initialValue: LuminaHomeFeed(session: store.session, includesExternalSources: true))
    }

    private var remoteAvailable: Bool { store.nativeStreamingCapabilities?.remote.available == true }
    private var remoteLiveAvailable: Bool { store.nativeStreamingCapabilities?.remote.liveAvailable == true }
    private var recommendationItems: [HomeExternalItem] {
        deduplicated(remoteFeed.rows
            .filter { $0.shelfID != .live }
            .flatMap { $0.externalItems ?? [] })
    }
    private var liveItems: [HomeExternalItem] {
        deduplicated(remoteFeed.rows
            .filter { $0.shelfID == .live }
            .flatMap { $0.externalItems ?? [] })
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                if savedOnly {
                    savedContent
                } else {
                    HStack(spacing: 22) {
                        streamingTab("Saved", tab: .saved, identifier: "streamingDownloadedTab")
                        if remoteAvailable {
                            streamingTab("For you", tab: .recommendations, identifier: "streamingRecommendationsTab")
                        }
                        if remoteAvailable && remoteLiveAvailable {
                            streamingTab("Live", tab: .live, identifier: "streamingLiveTab")
                        }
                        Spacer()
                        Button("Refresh") { Task { await load() } }
                            .buttonStyle(LuminaUnderlineTabStyle())
                            .disabled(savedLibrary.isBusy || remoteFeed.isLoading)
                    }
                    .focusSection()

                    Group {
                        switch selectedTab {
                        case .saved:
                            savedContent
                        case .recommendations:
                            remoteContent(items: recommendationItems, emptyTitle: "Nothing recommended yet", emptyMessage: "Try again once your server has new recommendations.")
                        case .live:
                            remoteContent(items: liveItems, emptyTitle: "No live streams right now", emptyMessage: "Live sources will appear here when they are available.")
                        }
                    }
                }
            }
            .padding(.horizontal, 80)
            .padding(.top, 28)
            .padding(.bottom, 80)
        }
        .background(LuminaTheme.theater)
        .accessibilityIdentifier(savedOnly ? "savedStreamingLibrary" : "streamingScreen")
        .task(id: TVHomeSessionScope(store.session)) {
            await load()
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task { await load() }
        }
        .fullScreenCover(item: $selectedRemote) { item in
            NativeRemotePlayer(store: store, item: item)
        }
    }

    private func streamingTab(_ title: String, tab: TVStreamingTab, identifier: String) -> some View {
        Button {
            hasUserSelectedTab = true
            selectedTab = tab
        } label: { Text(title) }
            .buttonStyle(LuminaUnderlineTabStyle(selected: selectedTab == tab))
            .focused($focusedTab, equals: tab)
            .accessibilityValue(selectedTab == tab ? "Selected" : "")
            .accessibilityAddTraits(selectedTab == tab ? .isSelected : [])
            .accessibilityIdentifier(identifier)
    }

    @ViewBuilder private var savedContent: some View {
        if savedLibrary.isBusy && savedLibrary.items.isEmpty {
            ProgressView("Loading saved videos…")
                .frame(maxWidth: .infinity, minHeight: 360)
        } else if savedLibrary.items.isEmpty {
            TVMessageView(
                title: "No saved streaming videos",
                message: savedLibrary.errorMessage ?? "Videos you download from supported sources will appear here.",
                actionTitle: "Refresh"
            ) { Task { await savedLibrary.load() } }
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300, maximum: 440), spacing: 30, alignment: .top)], spacing: 42) {
                ForEach(savedLibrary.items) { item in
                    TVMediaCard(item: item, store: store, poster: false, artworkWidth: 760) { onOpen(item) }
                        .accessibilityIdentifier("streamingSaved-\(item.id)")
                }
            }
            .focusSection()
            if savedLibrary.hasMore {
                Button("Load more") { Task { await savedLibrary.loadMore() } }
                    .buttonStyle(LuminaQuietActionStyle())
                    .disabled(savedLibrary.isBusy)
            }
        }
    }

    @ViewBuilder private func remoteContent(items: [HomeExternalItem], emptyTitle: String, emptyMessage: String) -> some View {
        if remoteFeed.isLoading && items.isEmpty {
            ProgressView("Loading streaming…")
                .frame(maxWidth: .infinity, minHeight: 360)
        } else if items.isEmpty {
            TVMessageView(title: emptyTitle, message: remoteFeed.errorMessage ?? emptyMessage, actionTitle: "Refresh") {
                Task { await load() }
            }
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 340, maximum: 480), spacing: 30, alignment: .top)], spacing: 42) {
                ForEach(items) { item in
                    TVStreamingRemoteCard(item: item) { selectedRemote = item }
                        .accessibilityIdentifier("streamingRemote-\(item.id)")
                }
            }
            .focusSection()
        }
    }

    private func load() async {
        savedLibrary.configure(session: store.session)
        if savedOnly {
            await savedLibrary.load()
            return
        }
        remoteFeed.configure(session: store.session)
        async let saved: Void = savedLibrary.load()
        await refreshRemoteContent()
        await saved
    }

    private func refreshRemoteContent() async {
        let capabilities = await store.loadNativeStreamingCapabilities()
        guard let remote = capabilities?.remote, remote.available else {
            if selectedTab != .saved { selectedTab = .saved }
            return
        }

        if !hasUserSelectedTab, selectedTab == .saved {
            selectedTab = .recommendations
        } else if selectedTab == .live, !remote.liveAvailable {
            selectedTab = .recommendations
        }
        await remoteFeed.load()
    }

    private func deduplicated(_ items: [HomeExternalItem]) -> [HomeExternalItem] {
        var itemIDs = Set<String>()
        return items.filter { itemIDs.insert($0.id).inserted }
    }
}

private struct TVStreamingRemoteCard: View {
    let item: HomeExternalItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                AsyncImage(url: item.artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image.resizable().scaledToFill()
                    default:
                        Rectangle().fill(.white.opacity(0.08))
                            .overlay { LuminaIcon(glyph: .play, size: 42).foregroundStyle(LuminaTheme.muted) }
                    }
                }
                .frame(height: 220)
                .clipShape(.rect(cornerRadius: 14))

                Text(item.title)
                    .font(.system(size: 25, weight: .semibold))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(2)
                Text(item.subtitle)
                    .font(.caption)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: 16 / 9))
        .accessibilityLabel("\(item.title), \(item.subtitle)")
    }
}

private struct TVCollectionGrid: View {
    let title: String?
    let items: [MediaItem]
    let store: LuminaStore
    let focusedItemID: FocusState<String?>.Binding
    let onOpen: (MediaItem) -> Void
    let onItemDidAppear: (MediaItem) -> Void
    let onVisible: (String) -> Void

    var body: some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 24) {
                if let title {
                    Text(title)
                        .font(LuminaTheme.editorial(34, relativeTo: .title2, italic: true))
                        .foregroundStyle(LuminaTheme.ink)
                        .accessibilityAddTraits(.isHeader)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                    ForEach(items) { item in
                        TVMediaCard(item: item, store: store, poster: true, showsCaption: false, artworkWidth: 480) { onOpen(item) }
                            .focused(focusedItemID, equals: item.id)
                            .id(item.id)
                            .accessibilityIdentifier("libraryItem-\(item.id)")
                            .onAppear { onItemDidAppear(item) }
                            .onScrollVisibilityChange(threshold: 0.01) { visible in
                                if visible { onVisible(item.id) }
                            }
                    }
                }
                .focusSection()
            }
        }
    }
}

private struct TVVideoFiltersView: View {
    let filters: VideoFilters
    let store: LuminaStore
    let allowsCollectionFilter: Bool
    let onApply: (VideoFilters) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var genre = ""
    @State private var year = ""
    @State private var unwatchedOnly = false
    @State private var favoritesOnly = false
    @State private var collectionID: String?
    @State private var collectionName: String?
    @State private var collectionPickerPresented = false

    private var validYear: Bool { year.isEmpty || (Int(year).map { (1...9999).contains($0) } ?? false) }

    var body: some View {
        NavigationStack {
            Form {
                if allowsCollectionFilter {
                    Section("Collection") {
                        Button {
                            collectionPickerPresented = true
                        } label: {
                            LabeledContent("Collection", value: collectionName ?? "Any collection")
                        }
                        .accessibilityIdentifier("videoCollectionFilterButton")
                    }
                }
                Section("Find your next watch") {
                    TextField("Genre", text: $genre)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("videoGenreField")
                    TextField("Year", text: $year)
                        .accessibilityIdentifier("videoYearField")
                    if !validYear { Text("Enter a year, or leave it blank for all years.").foregroundStyle(LuminaTheme.accentInk) }
                    Toggle("Unwatched only", isOn: $unwatchedOnly)
                        .tint(LuminaTheme.brand)
                        .accessibilityIdentifier("videoUnwatchedToggle")
                    Toggle("Favorites only", isOn: $favoritesOnly)
                        .tint(LuminaTheme.brand)
                        .accessibilityIdentifier("videoFavoritesToggle")
                }
                Section {
                    Button("Apply filters") {
                        onApply(VideoFilters(genre: genre.trimmingCharacters(in: .whitespacesAndNewlines), year: Int(year),
                                             unwatchedOnly: unwatchedOnly, favoritesOnly: favoritesOnly,
                                             collectionID: collectionID, collectionName: collectionName))
                        dismiss()
                    }
                    .disabled(!validYear)
                    .accessibilityIdentifier("videoApplyFiltersButton")
                    Button("Reset filters") {
                        collectionID = nil; collectionName = nil; genre = ""; year = ""; unwatchedOnly = false; favoritesOnly = false
                    }
                    Button("Cancel", role: .cancel) { dismiss() }
                }
            }
            .navigationTitle("Video filters")
        }
        .fullScreenCover(isPresented: $collectionPickerPresented) {
            NavigationStack {
                TVCollectionFilterPicker(store: store, selection: $collectionID, selectedName: $collectionName)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel") { collectionPickerPresented = false }
                                .accessibilityIdentifier("collectionFilterCancelButton")
                        }
                    }
            }
            .background(LuminaTheme.theater.ignoresSafeArea())
            .onExitCommand { collectionPickerPresented = false }
        }
        .onAppear {
            collectionID = filters.collectionID
            collectionName = filters.collectionName
            genre = filters.genre
            year = filters.year.map(String.init) ?? ""
            unwatchedOnly = filters.unwatchedOnly
            favoritesOnly = filters.favoritesOnly
        }
    }
}

private struct TVCollectionFilterPicker: View {
    let store: LuminaStore
    @Binding var selection: String?
    @Binding var selectedName: String?
    @Environment(\.dismiss) private var dismiss
    @State private var collections = [MediaItem]()
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Button("Any collection") {
                    selection = nil
                    selectedName = nil
                    dismiss()
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: selection == nil))
                .accessibilityValue(selection == nil ? "Selected" : "")
                .accessibilityAddTraits(selection == nil ? .isSelected : [])
                .accessibilityIdentifier("collectionFilter-any")

                if isLoading {
                    ProgressView("Loading collections…")
                        .frame(maxWidth: .infinity, minHeight: 360)
                } else if let errorMessage {
                    TVMessageView(title: "Collections could not load", message: errorMessage, actionTitle: "Try again") {
                        Task { await load() }
                    }
                } else if collections.isEmpty {
                    TVMessageView(title: "No collections yet", message: "Movie collections will appear here when available.", actionTitle: "Refresh") {
                        Task { await load() }
                    }
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(collections) { item in
                            TVMediaCard(item: item, store: store, poster: true, artworkWidth: 480) {
                                selection = item.id
                                selectedName = item.name
                                dismiss()
                            }
                            .accessibilityValue(selection == item.id ? "Selected" : "")
                            .accessibilityIdentifier("collectionFilter-\(item.id)")
                        }
                    }
                    .focusSection()
                }
            }
            .padding(.horizontal, 80)
            .padding(.vertical, 48)
        }
        .background(LuminaTheme.theater)
        .foregroundStyle(LuminaTheme.ink)
        .navigationTitle("Collection")
        .accessibilityIdentifier("collectionFilterPicker")
        .task { await load() }
    }

    private func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do { collections = try await store.collectionFilterOptions() }
        catch is CancellationError { }
        catch { errorMessage = "Check the connection and try again." }
    }
}

private struct TVAudioView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    @Binding var browse: TVAudioBrowseState
    let focusRequest: Int
    let restoreFocusRequest: Int?
    let onRequestHeaderFocus: () -> Void
    @State private var pendingRestoredItemID: String?
    @State private var lastVisibleItemID: String?
    @FocusState private var focusedItemID: String?
    @FocusState private var focusedSearch: Bool

    private var featuredAlbum: MediaItem? {
        guard browse.kind == .albums,
              browse.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return store.audioItems.first { $0.type == "MusicAlbum" }
    }

    var body: some View {
        GeometryReader { geometry in
            let heroHeight = min(900, max(620, geometry.size.height * 0.82))
            TVHeroScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if let featured = featuredAlbum {
                        Button { onOpen(featured) } label: {
                            MusicCollectionSpotlight(
                                item: featured,
                                store: store,
                                minimumHeight: heroHeight,
                                topContentClearance: 88
                            )
                        }
                        .buttonStyle(TVArtworkButtonStyle(aspect: 16 / 9))
                        .frame(height: heroHeight)
                        .clipped()
                        .contentShape(.rect)
                        .focused($focusedItemID, equals: "audioFeaturedAlbum")
                        .accessibilityLabel("Open album \(featured.name), \(featured.displaySubtitle)")
                        .accessibilityIdentifier("audioFeaturedAlbum")
                        .ignoresSafeArea(.container, edges: .horizontal)
                        .focusedValue(\.tvHeroRegion, true)
                        .focusSection()
                        .onMoveCommand { direction in
                            if direction == .up, focusedItemID == "audioFeaturedAlbum" {
                                onRequestHeaderFocus()
                            }
                        }
                    }
                    NativeMusicLibraryLinks(store: store)
                        .padding(.horizontal, 80)
                        .focusSection()
                    HStack(spacing: 32) {
                        HStack(spacing: 28) {
                            ForEach(MusicBrowseKind.allCases, id: \.self) { category in
                                Button(category.label) { browse.kind = category }
                                    .buttonStyle(LuminaUnderlineTabStyle(selected: browse.kind == category))
                                    .accessibilityAddTraits(browse.kind == category ? .isSelected : [])
                                    .accessibilityIdentifier("audioCategory-\(category.rawValue)")
                            }
                        }
                        Spacer()
                        TextField("Search music", text: $browse.query)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .frame(width: 440)
                            .accessibilityIdentifier("audioSearchField")
                            .focused($focusedSearch)
                    }
                    .padding(.horizontal, 80)
                    .focusSection()
                    if store.isAudioBusy && store.audioItems.isEmpty {
                        ProgressView("Loading music…").frame(maxWidth: .infinity, minHeight: 360)
                    } else if store.audioItems.isEmpty {
                        TVMessageView(title: "Your music library", message: store.errorMessage ?? "Add music to Lumina, or try another search.", actionTitle: "Refresh") { Task { await store.loadAudio(kind: browse.kind, search: browse.query) } }
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 290), spacing: 32, alignment: .top)], spacing: 48) {
                            ForEach(store.audioItems) { item in
                                TVMediaCard(item: item, store: store) { onOpen(item) }
                                    .focused($focusedItemID, equals: item.id)
                                    .accessibilityIdentifier("audioItem-\(item.id)")
                                    .onAppear {
                                        lastVisibleItemID = item.id
                                        if store.audioHasMore && item.id == store.audioItems.suffix(6).first?.id { Task { await store.loadMoreAudio() } }
                                    }
                            }
                        }
                        .padding(.horizontal, 80)
                        .padding(.top, 24)
                        .focusSection()
                        if store.audioHasMore {
                            Button("Show more") { Task { await store.loadMoreAudio() } }
                                .buttonStyle(LuminaQuietActionStyle()).padding(32)
                        }
                        if let error = store.errorMessage {
                            Text(error).foregroundStyle(LuminaTheme.accentInk).padding(24)
                        }
                    }
                }
                .padding(.top, featuredAlbum == nil ? 112 : 0)
                .padding(.bottom, 32)
            }
        }
        .ignoresSafeArea(.container, edges: featuredAlbum == nil ? .horizontal : [.top, .horizontal])
        .accessibilityIdentifier("audioScreen")
        .task(id: lastVisibleItemID, priority: .utility) {
            await store.prefetchArtwork(after: lastVisibleItemID, in: store.audioItems, maxWidth: 760)
        }
        .onChange(of: focusedItemID) { _, itemID in
            if let itemID { browse.focusedItemID = itemID }
        }
        .onChange(of: focusRequest) { _, _ in
            if featuredAlbum != nil {
                focusedItemID = "audioFeaturedAlbum"
            } else { focusedSearch = true }
        }
        .onChange(of: restoreFocusRequest, initial: true) { _, request in
            guard request != nil else { return }
            pendingRestoredItemID = browse.focusedItemID
            restoreLastSelectionIfAvailable()
        }
        .onChange(of: store.audioItems.map(\.id)) { _, _ in restoreLastSelectionIfAvailable() }
        .task(id: "\(browse.kind.rawValue)-\(browse.query)") {
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await store.loadAudio(kind: browse.kind, search: browse.query)
        }
    }

    private func restoreLastSelectionIfAvailable() {
        guard let itemID = pendingRestoredItemID else { return }
        if itemID == "audioFeaturedAlbum", featuredAlbum != nil {
            focusedItemID = itemID
            pendingRestoredItemID = nil
        } else if store.audioItems.contains(where: { $0.id == itemID }) {
            focusedItemID = itemID
            pendingRestoredItemID = nil
        }
    }
}

private struct TVFeaturedView: View {
    let store: LuminaStore
    var items: [MediaItem]? = nil
    @Binding var heroSelection: String?
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    let onRequestHeaderFocus: () -> Void
    var minimumHeight: CGFloat
    var onWatchlistChanged: @MainActor () async -> Void = {}
    @State private var preparing = false
    @State private var playbackError: String?
    @State private var watchlist: LuminaWatchlist?
    @State private var watchlistItemID: String?
    @State private var heroIsVisible = true
    @FocusState private var focusedAction: String?

    var body: some View {
        FeaturedShelf(
            items: items ?? store.featuredItems,
            store: store,
            isInteracting: preparing || focusedAction != nil || !heroIsVisible,
            showsOverview: false,
            showsContext: false,
            showsMetadata: false,
            showsProgress: false,
            wideMinimumHeight: minimumHeight,
            onOpen: onOpen,
            selection: $heroSelection
        ) { item in
            VStack(alignment: .leading, spacing: 16) {
                if let context = context(for: item) {
                    Text(context)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                }
                HStack(spacing: 24) {
                    heroActionButtons(for: item)
                }
            }
            if let playbackError { Text(playbackError).font(.body) }
        }
        .environment(\.luminaArtworkMotionEnabled, heroIsVisible)
        .onScrollVisibilityChange(threshold: 0.2) { heroIsVisible = $0 }
        .task(id: watchlistTaskID) {
            guard let item = selectedItem else {
                watchlist = nil
                watchlistItemID = nil
                return
            }
            let model = LuminaWatchlist(session: store.session)
            watchlist = model
            watchlistItemID = item.id
            await model.load(itemID: item.id)
        }
        .onChange(of: focusRequest) { _, _ in
            guard selectedItem != nil, !preparing else { return }
            focusedAction = "play"
        }
        .onMoveCommand { direction in
            if direction == .up { onRequestHeaderFocus() }
        }
        // Menu restores the last browsing selection. Deliberately avoid
        // redirecting that return path to Play when Home is recreated.
    }

    @ViewBuilder private func heroActionButtons(for item: MediaItem) -> some View {
        Button {
            guard !preparing else { return }
            preparing = true
            playbackError = nil
            Task {
                defer { preparing = false }
                do { onPlayback(try await store.preparePlayback(for: item)) }
                catch { playbackError = "Playback could not start. Try again." }
            }
        } label: {
            Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 28)
        }
        .buttonStyle(LuminaQuietActionStyle(primary: true))
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")
        .focused($focusedAction, equals: "play")

        Button {
            guard watchlistItemID == item.id, let watchlist else { return }
            if watchlist.errorMessage != nil {
                Task { await watchlist.load(itemID: item.id) }
            } else {
                Task {
                    let didChange = await watchlist.set(itemID: item.id, included: !watchlist.isIncluded)
                    if didChange { await onWatchlistChanged() }
                }
            }
        } label: {
            Label(
                watchlist?.errorMessage == nil
                    ? watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist"
                    : "Retry watchlist",
                glyph: .bookmark,
                variant: watchlist?.isIncluded == true ? .solid : .outline
            )
        }
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .disabled(watchlistItemID != item.id || watchlist?.isLoading == true || watchlist?.isSaving == true)
        .accessibilityIdentifier("featuredWatchlistButton")
        .accessibilityValue(watchlist?.isIncluded == true ? "In watchlist" : "Not in watchlist")
        .accessibilityHint(watchlist?.isIncluded == true ? "Remove from your watchlist" : "Save to your watchlist")
        .focused($focusedAction, equals: "watchlist")
    }

    private var selectedItem: MediaItem? {
        (items ?? store.featuredItems).first { $0.id == heroSelection } ?? (items ?? store.featuredItems).first
    }

    private var watchlistTaskID: String {
        "\(selectedItem?.id ?? "")-\(store.session?.accessToken ?? "")"
    }

    private func context(for item: MediaItem) -> String? {
        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            return Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)) + " remaining"
        }
        return nil
    }
}

private struct TVSectionHeading: View {
    let title: String
    var count: Int?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 20) {
            Text(title).font(.system(size: 30, weight: .semibold))
                .foregroundStyle(LuminaTheme.ink).accessibilityAddTraits(.isHeader)
            Spacer()
            if let count { Text(count, format: .number).font(.system(size: 18)).foregroundStyle(LuminaTheme.muted) }
        }
    }
}

private struct TVRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    var poster = false
    var identifierPrefix = "resumeItem"
    let onOpen: (MediaItem) -> Void
    var feed: LuminaHomeFeed? = nil
    var onHide: (String) -> Void = { _ in }
    @FocusState private var focusedItemID: String?
    @State private var movingForward = true
    @State private var lastFocusedItemID: String?

    private var captionPresentation: TVRailCaptionPresentation {
        TVRailCaptionPresentation(identifierPrefix: identifierPrefix)
    }

    private var presentsPosters: Bool {
        poster || captionPresentation != .standard
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TVSectionHeading(title: title, count: items.count)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 32) {
                    ForEach(items) { item in
                        TVRecommendationCard(item: item, feed: feed, onHide: onHide) {
                            TVMediaCard(item: item, store: store, poster: presentsPosters, captionPresentation: captionPresentation,
                                        artworkWidth: presentsPosters ? 480 : 760) {
                                if let reco = item.luminaReco { Task { await feed?.record("open", recommendation: reco) } }
                                onOpen(item)
                            }
                        }
                            .frame(width: item.isAudio || item.type.hasPrefix("Music") ? 260 : presentsPosters ? 220 : 400)
                            .focused($focusedItemID, equals: item.id)
                            .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .padding(.vertical, 24)
            }
            .scrollClipDisabled()
        }
        .padding(.horizontal, 80)
        // Enter using this row’s history, rather than the previous row’s horizontal position.
        .defaultFocus($focusedItemID, items.first(where: { $0.id == lastFocusedItemID })?.id ?? items.first?.id,
                      priority: .userInitiated)
        .onChange(of: focusedItemID) { old, new in
            if let new { lastFocusedItemID = new }
            guard let old, let new,
                  let previous = items.firstIndex(where: { $0.id == old }),
                  let current = items.firstIndex(where: { $0.id == new }) else { return }
            movingForward = current >= previous
        }
        .task(id: "\(focusedItemID ?? "")-\(movingForward)", priority: .utility) {
            // Only the focused shelf speculates; all rails share the four-load artwork budget.
            guard let focusedItemID, let index = items.firstIndex(where: { $0.id == focusedItemID }) else { return }
            let upcoming = movingForward ? Array(items.dropFirst(index + 1).prefix(8))
                : Array(items.prefix(index).suffix(8).reversed())
            let urls = upcoming.compactMap {
                store.thumbnailURL(for: $0, poster: presentsPosters || $0.isAudio || $0.type.hasPrefix("Music"),
                                   maxWidth: presentsPosters ? 480 : 760)
            }
            await ArtworkImageCache.shared.prefetch(urls, limit: 8)
        }
    }
}

@MainActor private struct TVRecommendationCard<Content: View>: View {
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

struct TVMediaCard: View {
    let item: MediaItem
    let store: LuminaStore
    var poster = false
    var captionPresentation: TVRailCaptionPresentation = .standard
    var showsCaption = true
    var artworkWidth = 760
    let action: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var caption: TVRailCaption {
        TVRailCaption(item: item, presentation: captionPresentation)
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                TVArtwork(item: item, store: store, aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9, maxWidth: artworkWidth)
                    .frame(maxWidth: .infinity)
                    .overlay(alignment: .bottomLeading) {
                        if !showsCaption {
                            TVFocusedArtworkCaption(caption: caption)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if item.resumeSeconds > 0, item.durationSeconds > 0 {
                            ProgressView(value: min(item.resumeSeconds, item.durationSeconds), total: item.durationSeconds)
                                .progressViewStyle(.linear).tint(LuminaTheme.brand)
                                .frame(height: 3).background(.black.opacity(0.4))
                                .accessibilityLabel("Watch progress")
                                .accessibilityValue("\(Int(min(1, item.resumeSeconds / item.durationSeconds) * 100)) percent")
                        }
                    }

                if showsCaption {
                    Text(caption.primary)
                        .font(.system(size: 23, weight: .medium))
                        .foregroundStyle(LuminaTheme.ink)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                        .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : 58, alignment: .topLeading)
                        .frame(maxWidth: .infinity, alignment: .leading)

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
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : 28, alignment: .topLeading)
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
                        .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : 28, alignment: .topLeading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9))
        .accessibilityLabel(caption.accessibilityLabel)
        .accessibilityValue(caption.progress ?? "")
    }
}

/// Grid posters remain artwork-first at rest. Focus reveals the same caption
/// without changing card geometry, while the Button keeps its full accessible
/// title at every focus state.
private struct TVFocusedArtworkCaption: View {
    let caption: TVRailCaption
    @Environment(\.isFocused) private var isFocused
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(caption.primary)
                .font(.system(size: 23, weight: .semibold))
                .lineLimit(2)
            if let secondary = caption.secondary {
                Text(secondary)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.82))
                    .lineLimit(1)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            LinearGradient(colors: [.clear, .black.opacity(0.86)], startPoint: .top, endPoint: .bottom)
                .padding(.top, -48)
        }
        .opacity(isFocused ? 1 : 0)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.14), value: isFocused)
        .accessibilityHidden(true)
    }
}

enum TVRailCaptionPresentation: Equatable {
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

private struct TVRailCaption {
    let primary: String
    let secondary: String?
    let progress: String?

    init(item: MediaItem, presentation: TVRailCaptionPresentation) {
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

/// Focus follows the artwork; captions stay directly on the page.
struct TVArtworkButtonStyle: ButtonStyle {
    /// Callers provide the artwork aspect so existing media-card declarations
    /// remain explicit even though focus now scales the full card.
    init(aspect _: CGFloat) {}

    @Environment(\.isFocused) private var isFocused
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(isFocused ? 1.04 : 1, anchor: .top)
            .shadow(color: .black.opacity(isFocused ? 0.48 : 0), radius: isFocused ? 18 : 0, y: isFocused ? 10 : 0)
            .opacity(configuration.isPressed ? 0.75 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: isFocused)
    }
}

struct TVArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    let aspect: CGFloat
    var maxWidth = 760

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                placeholder.overlay {
                    SubjectAwareArtwork(url: artworkURL) { await store.artworkFraming(for: $0) }
                }
            }
            .clipShape(.rect(cornerRadius: 4))
            #if LUMINA_UI_TESTING
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Artwork")
            .accessibilityIdentifier("tvArtwork-\(item.id)")
            .accessibilityValue(artworkURL?.path ?? "Missing artwork")
            #endif
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [LuminaTheme.brand.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            LuminaIcon(glyph: item.isAudio || item.type.hasPrefix("Music") ? .music : .movies,
                       variant: .solid, size: 28)
                .foregroundStyle(LuminaTheme.ink.opacity(0.7))
        }
    }

    private var artworkURL: URL? {
        store.thumbnailURL(for: item, poster: aspect <= 1, maxWidth: maxWidth)
    }
}

struct TVMessageView: View {
    let title: String
    let message: String
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            Text(title)
                .font(LuminaTheme.editorial(42, relativeTo: .title))
                .italic()
                .foregroundStyle(LuminaTheme.ink)
            Text(message)
                .font(.title3)
                .foregroundStyle(LuminaTheme.muted)
                .multilineTextAlignment(.center)
            Button(actionTitle, action: action)
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .controlSize(.large)
        }
        .frame(maxWidth: .infinity, minHeight: 360)
        .padding(48)
    }
}

/// A single remote scrubber avoids 27 tiny focus targets. Up/Down previews a
/// letter; Select commits one indexed page request, Left returns to the grid.
private struct TVLibraryAlphabetRail: View {
    let letters: [String]
    @Binding var selection: String?
    let expanded: Bool
    let isFocused: Bool
    let onReturn: () -> Void
    let onSelect: (String) -> Void

    private var selectedIndex: Int { letters.firstIndex(of: selection ?? "") ?? 0 }
    private var visibleLetters: [String] {
        let lower = max(0, min(selectedIndex - 3, letters.count - 7))
        return Array(letters.dropFirst(lower).prefix(7))
    }

    var body: some View {
        Button { if !letters.isEmpty { onSelect(letters[selectedIndex]) } } label: {
            VStack(spacing: 14) {
                LuminaIcon(glyph: .chevronUp, size: 14)
                if expanded {
                    ForEach(visibleLetters, id: \.self) { letter in
                        Text(letter)
                            .font(.system(size: letter == letters[selectedIndex] ? 34 : 20,
                                          weight: letter == letters[selectedIndex] ? .semibold : .regular))
                            .foregroundStyle(.white.opacity(letter == letters[selectedIndex] ? 1 : 0.4))
                            .frame(height: 36)
                            .overlay(alignment: .bottom) {
                                if isFocused && letter == letters[selectedIndex] {
                                    Rectangle().frame(width: 26, height: 2)
                                }
                            }
                    }
                } else {
                    Text("A–Z").font(.system(size: 19, weight: .medium)).frame(height: 44)
                }
                LuminaIcon(glyph: .chevronDown, size: 14)
            }
            .foregroundStyle(.white.opacity(isFocused ? 1 : 0.5))
            .frame(width: 72, height: 420)
            .contentShape(.rect)
        }
        .buttonStyle(TVAlphabetButtonStyle())
        .accessibilityIdentifier("libraryAlphabetIndex")
        .accessibilityLabel("Jump to title letter")
        .accessibilityValue(letters.isEmpty ? "" : letters[selectedIndex])
        .accessibilityHint("Up or down chooses a letter. Select jumps to its first title.")
        .accessibilityAdjustableAction { direction in
            guard !letters.isEmpty else { return }
            switch direction {
            case .increment: selection = letters[min(letters.count - 1, selectedIndex + 1)]
            case .decrement: selection = letters[max(0, selectedIndex - 1)]
            @unknown default: break
            }
        }
        .onMoveCommand { direction in
            guard !letters.isEmpty else { return }
            switch direction {
            case .up: selection = letters[max(0, selectedIndex - 1)]
            case .down: selection = letters[min(letters.count - 1, selectedIndex + 1)]
            case .left: onReturn()
            default: break
            }
        }
    }
}

private struct TVAlphabetButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.65 : 1)
    }
}

// Native focus scrolling reveals the focused button, which can leave the rest of
// a tall hero above the viewport. Restore its full artwork when focus returns.
private struct TVHeroRegionKey: FocusedValueKey { typealias Value = Bool }

extension FocusedValues {
    var tvHeroRegion: Bool? {
        get { self[TVHeroRegionKey.self] }
        set { self[TVHeroRegionKey.self] = newValue }
    }
}

struct TVHeroScrollView<Content: View>: View {
    @ViewBuilder let content: Content
    @State private var position = ScrollPosition(edge: .top)
    @FocusedValue(\.tvHeroRegion) private var heroHasFocus
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ScrollView { content }
            .scrollPosition($position)
            .onChange(of: heroHasFocus) { _, focused in
                guard focused == true else { return }
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
                    position.scrollTo(edge: .top)
                }
            }
    }
}
