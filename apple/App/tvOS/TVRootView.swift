import SwiftUI
import LuminaCore

/// The living-room client: a small native shell over the shared Lumina session and library store.
public struct TVRootView: View {
    private let store: LuminaStore
    @State private var path: [MediaItem] = []
    @State private var playbackRequest: PlaybackRequest?
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
                }
            }
            .navigationDestination(for: MediaItem.self) { item in
                if item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" {
                    TVMusicDetailView(item: item, store: store) { path.append($0) }
                } else if item.isCollection {
                    TVVideoCollectionView(item: item, store: store, onOpen: { path.append($0) }, onPlayback: { playbackRequest = $0 })
                } else {
                    TVDetailView(item: item, store: store, onOpen: { path.append($0) }) { request in
                        playbackRequest = request
                    }
                }
            }
        }
        .tint(LuminaTheme.gold)
        .preferredColorScheme(.dark)
        .onChange(of: store.session?.accessToken) { _, token in
            if token == nil { audioSession.stop() }
        }
        .task(id: store.session?.accessToken) {
            if store.session != nil { await audioSession.restore(store: store) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                audioSession.resumeAfterActivation()
                Task { await audioSession.refreshSharedQueue() }
            } else if phase == .background {
                Task { await audioSession.flushCheckpoint() }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.session != nil { NativeNowPlayingBar(store: store) }
        }
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
        .onPlayPauseCommand {
            // The compact bar can disappear while it owns focus. Keep the
            // shared session reachable from every browsing destination.
            guard playbackRequest == nil, audioSession.playback != nil else { return }
            Task { await audioSession.toggle() }
        }
        .environment(audioSession)
    }
}

private struct TVConnectionView: View {
    let store: LuminaStore
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var showcaseSlides = [PublicShowcaseSlide]()
    @State private var usesRemoteShowcase = false
    @State private var displayedShowcaseID = Self.bundledShowcaseSlides[0].id
    @State private var decodedShowcaseIDs = Set<String>()
    @State private var decodedPosterIDs = Set<String>()
    @State private var showcaseOrigin: String?
    @State private var artworkPaused = false
    @State private var helpPresented = false
    @AppStorage("lumina.tv.lastSuccessfulServerOrigin") private var lastSuccessfulServerOrigin = ""
    @FocusState private var focusedField: Field?
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver

    private enum Field: Hashable {
        case server, username, password, connect, help
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
                    .frame(width: min(560, max(500, geometry.size.width * 0.34)))
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                    .padding(.leading, max(80, geometry.size.width * 0.095))
                    .padding(.vertical, 64)

                connectionFooter
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                    .padding(.horizontal, max(72, geometry.size.width * 0.06))
                    .padding(.bottom, 52)

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
        .defaultFocus($focusedField, .server)
        .task {
            prefillLastSuccessfulServer()
        }
        .task(id: validServerOrigin) {
            await loadShowcase()
        }
        .task(id: slideshowKey) {
            await advanceShowcaseWhenReady()
        }
        .onChange(of: validServerOrigin) { oldOrigin, newOrigin in
            guard oldOrigin != newOrigin else { return }
            resetShowcase(for: newOrigin)
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
                Text("LUMINA")
                    .font(LuminaTheme.editorial(32, relativeTo: .title3))
                    .tracking(2.4)
            }
            .foregroundStyle(.white)

            Text("Sign in")
                .font(.title2.weight(.semibold))
                .foregroundStyle(.white)

            VStack(alignment: .leading, spacing: 16) {
                TextField("Server address", text: $server)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("serverURLField")
                    .focused($focusedField, equals: .server)

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
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(LuminaTheme.spotlight)
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
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .accessibilityIdentifier("connectButton")
                .focused($focusedField, equals: .connect)
                .disabled(!canConnect || store.isBusy)
            }
            .padding(22)
            .background(.black.opacity(0.32), in: .rect(cornerRadius: 18))

        }
        .foregroundStyle(.white)
    }

    private var connectionFooter: some View {
        HStack {
            Button("Connection help") {
                helpPresented = true
            }
            .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
            .accessibilityIdentifier("connectionHelpButton")
            .focused($focusedField, equals: .help)

            Spacer()

            if canOfferArtworkPause {
                artworkPauseButton
            }
        }
        .focusSection()
    }

    private var artworkPauseButton: some View {
        Button {
            artworkPaused.toggle()
        } label: {
            Image(systemName: artworkPaused ? "play.fill" : "pause.fill")
                .font(.body.weight(.semibold))
                .frame(width: 64, height: 64)
        }
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .accessibilityLabel(artworkPaused ? "Resume artwork" : "Pause artwork")
        .accessibilityValue(artworkPaused ? "Paused" : "Playing")
        .accessibilityIdentifier("connectionArtworkPauseButton")
    }

    private var canConnect: Bool {
        !server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !password.isEmpty
    }

    private var validServerOrigin: String? {
        Self.normalizedServerOrigin(server)
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
            !artworkPaused &&
            !reduceMotion &&
            !voiceOver &&
            scenePhase == .active &&
            displayedShowcase != nil &&
            pendingShowcase != nil &&
            isShowcaseReady(pendingShowcase)
    }

    private var canOfferArtworkPause: Bool {
        activeShowcaseSlides.count > 1 && !reduceMotion && !voiceOver && scenePhase == .active
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
        artworkPaused = false
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

            Text("Enable Apps like Infuse in Lumina on the web: Manage Server → Connections → Metadata. If your account uses two-factor authentication, enter an app password.")
                .font(.body)
                .foregroundStyle(LuminaTheme.muted)
                .fixedSize(horizontal: false, vertical: true)

            Button("Done") { dismiss() }
                .buttonStyle(.borderedProminent)
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
    case home, video, audio, requests
    var title: String { rawValue.capitalized }
    var symbol: String { switch self { case .home: "house"; case .video: "play.rectangle"; case .audio: "music.note"; case .requests: "sparkles.tv" } }
}

private struct TVLibraryView: View {
    let store: LuminaStore
    let isAtNavigationRoot: Bool
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let onSignOut: () -> Void
    @State private var destination: TVDestination = .home
    @State private var accountPresented = false
    @State private var licensesPresented = false
    @State private var contentFocusRequest = 0
    @FocusState private var focusedDestination: TVDestination?
    @FocusState private var focusedAccount: Bool

    var body: some View {
        ZStack(alignment: .top) {
            Group {
                switch destination {
                case .home:
                    TVHomeView(store: store, onOpen: onOpen, onPlayback: onPlayback, focusRequest: contentFocusRequest)
                case .video:
                    TVVideoView(store: store, onOpen: onOpen, focusRequest: contentFocusRequest)
                case .audio:
                    TVAudioView(store: store, onOpen: onOpen, focusRequest: contentFocusRequest)
                case .requests:
                    TVRequestsView(store: store, focusRequest: contentFocusRequest)
                }
            }
            .padding(.top, destination == .home ? 0 : 112)
            header
                .padding(.horizontal, 64)
                .padding(.top, 12)
                .padding(.bottom, 24)
                .background {
                    LinearGradient(colors: [.black.opacity(0.55), .clear], startPoint: .top, endPoint: .bottom)
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        .allowsHitTesting(false)
                }
                .focusSection()
                .onMoveCommand { direction in
                    if direction == .down { contentFocusRequest += 1 }
                }
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .onExitCommand(perform: backAction)
        .sheet(isPresented: $licensesPresented) { PlaybackLicensesView() }
    }

    private var backAction: (() -> Void)? {
        guard destination != .home, isAtNavigationRoot else { return nil }
        return { returnHome() }
    }

    private func returnHome() {
        destination = .home
        focusedDestination = .home
    }

    private var header: some View {
        HStack(spacing: 28) {
            HStack(alignment: .lastTextBaseline, spacing: 10) {
                LuminaBrandMark().frame(width: 42, height: 26)
                    .alignmentGuide(.lastTextBaseline) { $0[.bottom] }
                Text("LUMINA")
                    .font(.system(size: 26, weight: .heavy))
                    .tracking(3)
                    .foregroundStyle(LuminaTheme.ink)
            }
            Spacer()
            ForEach(TVDestination.allCases, id: \.self) { tab in
                Button { destination = tab } label: {
                    Text(tab.title)
                        .font(.system(size: 26, weight: destination == tab ? .semibold : .regular))
                        .foregroundStyle(destination == tab || focusedDestination == tab ? LuminaTheme.ink : LuminaTheme.muted)
                        .frame(minWidth: 126, minHeight: 64)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(destination == tab ? LuminaTheme.gold : focusedDestination == tab ? .white.opacity(0.7) : .clear).frame(height: 2)
                        }
                }
                .buttonStyle(TVNavigationButtonStyle())
                .focusEffectDisabled()
                .focused($focusedDestination, equals: tab)
                .accessibilityAddTraits(destination == tab ? .isSelected : [])
                .accessibilityIdentifier("\(tab.rawValue)Tab")
            }
            Spacer()
            Button { accountPresented = true } label: {
                Image(systemName: "person.crop.circle")
                    .font(.system(size: 28))
                    .foregroundStyle(focusedAccount ? LuminaTheme.gold : .white.opacity(0.85))
                    .frame(width: 64, height: 64)
            }
            .buttonStyle(TVNavigationButtonStyle())
            .focusEffectDisabled()
            .focused($focusedAccount)
            .accessibilityLabel("Account")
            .accessibilityIdentifier("accountMenuButton")
            .confirmationDialog("Account", isPresented: $accountPresented, titleVisibility: .visible) {
                Button("Open-source licenses") { licensesPresented = true }
                Button("Refresh library") { Task { await store.refresh() } }
                Button("Sign out", role: .destructive) {
                    Task { await store.signOut(); onSignOut() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                if let username = store.session?.username { Text(username) }
            }
        }
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

private struct TVHomeView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    @State private var layout: LuminaHomeLayout?
    @State private var feed: LuminaHomeFeed?
    @Environment(\.scenePhase) private var scenePhase

    private var shelves: [HomeShelfPreference] {
        layout?.shelves ?? HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                if !store.featuredItems.isEmpty {
                    TVFeaturedView(store: store, onOpen: onOpen, onPlayback: onPlayback, focusRequest: focusRequest)
                        .focusSection()
                } else if store.isHomeBusy {
                    ProgressView("Loading Home…").frame(maxWidth: .infinity, minHeight: 650)
                } else {
                    TVMessageView(title: "Your next great watch starts here", message: store.errorMessage ?? "Add media to Lumina to fill Home with your household library.", actionTitle: "Refresh") {
                        Task { await store.loadHome() }
                    }
                    .padding(.top, 112)
                }
                if let feed, feed.hasLoaded {
                    ForEach(feed.rows) { row in
                        TVRail(title: row.title, items: row.items, store: store,
                               poster: row.shape == .poster,
                               identifierPrefix: row.shelfID == .continueWatching ? "resumeItem" : row.shelfID == .nextUp ? "nextUpItem" : "homeRow-\(row.id)",
                               onOpen: onOpen)
                            .focusSection()
                    }
                } else {
                    ForEach(shelves.filter(\.visible)) { shelf in
                        homeShelf(shelf.id)
                    }
                }
                if let feed, let error = feed.errorMessage {
                    TVMessageView(title: "Some Home shelves could not load", message: error, actionTitle: "Try again") {
                        Task { await feed.load() }
                    }
                }
            }
            .padding(.bottom, 80)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .accessibilityIdentifier("homeScreen")
        .task(id: store.session?.accessToken) {
            guard let session = store.session else { layout = nil; feed = nil; return }
            let preferences = LuminaHomeLayout(session: session)
            let homeFeed = LuminaHomeFeed(session: session)
            layout = preferences
            feed = homeFeed
            async let library: Void = store.loadHome()
            async let savedLayout: Void = preferences.load()
            async let rows: Void = homeFeed.load()
            _ = await (library, savedLayout, rows)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await layout?.load(); await feed?.load() }
            }
        }
        .onChange(of: store.resumeItems) { _, _ in
            if feed?.hasLoaded == true { Task { await feed?.load() } }
        }
    }

    @ViewBuilder private func homeShelf(_ id: HomeShelfID) -> some View {
        switch id {
        case .continueWatching:
            if !store.resumeItems.isEmpty {
                TVRail(title: id.title, items: store.resumeItems, store: store, onOpen: onOpen).focusSection()
            }
        case .nextUp:
            if !store.nextUpItems.isEmpty {
                TVRail(title: id.title, items: store.nextUpItems, store: store, identifierPrefix: "nextUpItem", onOpen: onOpen).focusSection()
            }
        case .newInLibrary:
            if !store.recentItems.isEmpty {
                TVRail(title: id.title, items: store.recentItems, store: store, poster: true, identifierPrefix: "recentItem", onOpen: onOpen).focusSection()
            }
        default:
            EmptyView()
        }
    }
}

private struct TVVideoView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let focusRequest: Int
    @State private var query = ""
    @State private var kind: VideoBrowseKind = .movies
    @State private var sort: VideoSort = .name
    @State private var filters = VideoFilters()
    @State private var filtersPresented = false
    @FocusState private var focusedItemID: String?
    @FocusState private var focusedCategory: VideoBrowseKind?

    private var requestKey: String { "\(kind.rawValue)-\(sort.rawValue)-\(query)-\(filters.genre)-\(filters.year ?? 0)-\(filters.unwatchedOnly)-\(filters.favoritesOnly)" }
    private var hasFilters: Bool { filters != VideoFilters() }

    var body: some View {
        VStack(alignment: .leading, spacing: 32) {
            HStack(spacing: 28) {
                HStack(spacing: 16) {
                    ForEach([VideoBrowseKind.movies, .shows, .anime, .youtube], id: \.self) { category in
                        Button { kind = category } label: {
                            Text(category.label)
                                .font(.system(size: 24, weight: kind == category ? .semibold : .regular))
                        }
                        .buttonStyle(LuminaQuietActionStyle())
                        .tint(kind == category ? LuminaTheme.gold : .white)
                        .accessibilityAddTraits(kind == category ? .isSelected : [])
                        .accessibilityIdentifier("videoCategory-\(category.rawValue)")
                        .focused($focusedCategory, equals: category)
                    }
                }
                if query.isEmpty {
                    Picker("Sort", selection: $sort) {
                        ForEach(VideoSort.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.menu)
                    .frame(width: 240)
                } else {
                    Text("By relevance").font(.body).foregroundStyle(LuminaTheme.muted).frame(width: 240)
                }
                Spacer()
                TextField("Search video", text: $query)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .frame(width: 340)
                    .accessibilityIdentifier("searchField")
            }
            .padding(.horizontal, 64)
            .focusSection()

            HStack(spacing: 28) {
                Button { filtersPresented = true } label: {
                    Label(hasFilters ? "Filters applied" : "Filters", systemImage: "line.3.horizontal.decrease")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .accessibilityIdentifier("videoFiltersButton")
                if hasFilters {
                    Text(filterSummary).font(.caption).foregroundStyle(LuminaTheme.muted)
                    Spacer()
                    Button("Clear filters") { filters = VideoFilters() }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("videoClearFiltersButton")
                }
            }
            .padding(.horizontal, 64)
            .focusSection()

            ScrollView {
                if store.isBusy && store.items.isEmpty {
                    ProgressView("Loading video…").frame(maxWidth: .infinity, minHeight: 360)
                } else if store.items.isEmpty {
                    TVMessageView(title: query.isEmpty && !hasFilters ? "No videos here yet" : "No matching videos", message: store.errorMessage ?? "Try another search or choose a different category.", actionTitle: "Refresh") { Task { await reload() } }
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                        ForEach(store.items) { item in
                            TVMediaCard(item: item, store: store, poster: true) { onOpen(item) }
                                .focused($focusedItemID, equals: item.id)
                                .accessibilityIdentifier("libraryItem-\(item.id)")
                                .onAppear {
                                    if store.hasMore && item.id == store.items.suffix(6).first?.id { Task { await store.loadMore() } }
                                }
                        }
                    }
                    .padding(.horizontal, 64)
                    .padding(.top, 24)
                    .focusSection()
                    if store.hasMore {
                        Button("Show more") { Task { await store.loadMore() } }
                            .buttonStyle(LuminaQuietActionStyle())
                            .padding(32)
                    }
                    if let error = store.errorMessage {
                        Text(error).foregroundStyle(LuminaTheme.spotlight).padding(24)
                    }
                }
            }
            .accessibilityIdentifier("libraryScreen")
        }
        .onChange(of: focusRequest) { _, _ in focusedCategory = kind }
        .sheet(isPresented: $filtersPresented) {
            TVVideoFiltersView(filters: filters) { filters = $0 }
        }
        .task(id: requestKey) {
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await reload()
        }
    }

    private var filterSummary: String {
        [filters.genre.isEmpty ? nil : filters.genre, filters.year.map(String.init), filters.unwatchedOnly ? "Unwatched" : nil, filters.favoritesOnly ? "Favorites" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func reload() async { await store.loadLibrary(search: query, kind: kind, sort: sort, filters: filters) }
}

private struct TVVideoFiltersView: View {
    let filters: VideoFilters
    let onApply: (VideoFilters) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var genre = ""
    @State private var year = ""
    @State private var unwatchedOnly = false
    @State private var favoritesOnly = false

    private var validYear: Bool { year.isEmpty || (Int(year).map { (1...9999).contains($0) } ?? false) }

    var body: some View {
        NavigationStack {
            Form {
                Section("Find your next watch") {
                    TextField("Genre", text: $genre)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("videoGenreField")
                    TextField("Year", text: $year)
                        .accessibilityIdentifier("videoYearField")
                    if !validYear { Text("Enter a year, or leave it blank for all years.").foregroundStyle(LuminaTheme.spotlight) }
                    Toggle("Unwatched only", isOn: $unwatchedOnly)
                        .accessibilityIdentifier("videoUnwatchedToggle")
                    Toggle("Favorites only", isOn: $favoritesOnly)
                        .accessibilityIdentifier("videoFavoritesToggle")
                }
                Section {
                    Button("Apply filters") {
                        onApply(VideoFilters(genre: genre.trimmingCharacters(in: .whitespacesAndNewlines), year: Int(year), unwatchedOnly: unwatchedOnly, favoritesOnly: favoritesOnly))
                        dismiss()
                    }
                    .disabled(!validYear)
                    .accessibilityIdentifier("videoApplyFiltersButton")
                    Button("Reset filters") {
                        genre = ""; year = ""; unwatchedOnly = false; favoritesOnly = false
                    }
                    Button("Cancel", role: .cancel) { dismiss() }
                }
            }
            .navigationTitle("Video filters")
        }
        .onAppear {
            genre = filters.genre
            year = filters.year.map(String.init) ?? ""
            unwatchedOnly = filters.unwatchedOnly
            favoritesOnly = filters.favoritesOnly
        }
    }
}

private struct TVAudioView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let focusRequest: Int
    @State private var kind: MusicBrowseKind = .albums
    @State private var query = ""
    @FocusState private var focusedItemID: String?
    @FocusState private var focusedSearch: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            NativeMusicLibraryLinks(store: store)
                .padding(.horizontal, 64)
            ScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if kind == .albums, let featured = store.audioItems.first(where: { $0.type == "MusicAlbum" }) {
                        Button { onOpen(featured) } label: {
                            MusicCollectionSpotlight(item: featured, store: store)
                        }
                        .buttonStyle(.plain)
                        .focused($focusedItemID, equals: "audioFeaturedAlbum")
                        .background(focusedItemID == "audioFeaturedAlbum" ? LuminaTheme.ink.opacity(0.08) : .clear)
                        .overlay {
                            RoundedRectangle(cornerRadius: 6)
                                .strokeBorder(focusedItemID == "audioFeaturedAlbum" ? LuminaTheme.gold : .clear, lineWidth: 2)
                        }
                        .accessibilityLabel("Open album \(featured.name), \(featured.displaySubtitle)")
                        .accessibilityIdentifier("audioFeaturedAlbum")
                        .padding(.horizontal, 64)
                        .padding(.top, 16)
                        .focusSection()
                    }
                    HStack(spacing: 32) {
                        Picker("Browse", selection: $kind) {
                            ForEach(MusicBrowseKind.allCases, id: \.self) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 700)
                        Spacer()
                        TextField("Search music", text: $query)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .frame(width: 440)
                            .accessibilityIdentifier("audioSearchField")
                            .focused($focusedSearch)
                    }
                    .padding(.horizontal, 64)
                    .focusSection()
                    if store.isAudioBusy && store.audioItems.isEmpty {
                        ProgressView("Loading music…").frame(maxWidth: .infinity, minHeight: 360)
                    } else if store.audioItems.isEmpty {
                        TVMessageView(title: "Your music library", message: store.errorMessage ?? "Add music to Lumina, or try another search.", actionTitle: "Refresh") { Task { await store.loadAudio(kind: kind, search: query) } }
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 290), spacing: 32, alignment: .top)], spacing: 48) {
                            ForEach(store.audioItems) { item in
                                TVMediaCard(item: item, store: store) { onOpen(item) }
                                    .accessibilityIdentifier("audioItem-\(item.id)")
                                    .onAppear {
                                        if store.audioHasMore && item.id == store.audioItems.suffix(6).first?.id { Task { await store.loadMoreAudio() } }
                                    }
                            }
                        }
                        .padding(.horizontal, 64)
                        .padding(.top, 24)
                        .focusSection()
                        if store.audioHasMore {
                            Button("Show more") { Task { await store.loadMoreAudio() } }
                                .buttonStyle(LuminaQuietActionStyle()).padding(32)
                        }
                        if let error = store.errorMessage {
                            Text(error).foregroundStyle(LuminaTheme.spotlight).padding(24)
                        }
                    }
                }
                .padding(.bottom, 32)
            }
        }
        .accessibilityIdentifier("audioScreen")
        .onChange(of: focusRequest) { _, _ in
            if kind == .albums, store.audioItems.contains(where: { $0.type == "MusicAlbum" }) {
                focusedItemID = "audioFeaturedAlbum"
            } else { focusedSearch = true }
        }
        .task(id: "\(kind.rawValue)-\(query)") {
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            guard !Task.isCancelled else { return }
            await store.loadAudio(kind: kind, search: query)
        }
    }
}

private struct TVFeaturedView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    @State private var preparing = false
    @State private var playbackError: String?
    @FocusState private var focusedAction: String?

    var body: some View {
        FeaturedShelf(items: store.featuredItems, store: store, isInteracting: preparing || focusedAction != nil, showsContext: false, showsProgress: false, wideMinimumHeight: 650) { item in
            HStack(spacing: 24) {
                Button {
                    guard !preparing else { return }
                    preparing = true
                    playbackError = nil
                    Task {
                        defer { preparing = false }
                        do { onPlayback(try await store.preparePlayback(for: item)) }
                        catch { playbackError = "Playback could not start. Open Details to try compatible video." }
                    }
                } label: {
                    Label(preparing ? "Preparing…" : "Play", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(LuminaTheme.gold)
                .foregroundStyle(LuminaTheme.theater)
                .disabled(preparing)
                .accessibilityIdentifier("featuredPlayButton")
                .focused($focusedAction, equals: "play")

                Button { onOpen(item) } label: {
                    Label("Details", systemImage: "info.circle")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .tint(.white)
                .foregroundStyle(focusedAction == "details" ? LuminaTheme.theater : .white)
                .accessibilityIdentifier("featuredDetailsButton")
                .focused($focusedAction, equals: "details")
            }
            .controlSize(.large)
            if let playbackError { Text(playbackError).font(.body) }
        }
        .onChange(of: focusRequest) { _, _ in focusedAction = "play" }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TVSectionHeading(title: title, count: items.count)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 32) {
                    ForEach(items) { item in
                        TVMediaCard(item: item, store: store, poster: poster) { onOpen(item) }
                            .frame(width: item.isAudio || item.type.hasPrefix("Music") ? 260 : poster ? 220 : 400)
                            .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .padding(.vertical, 24)
            }
            .scrollClipDisabled()
        }
        .padding(.horizontal, 80)
    }
}

struct TVMediaCard: View {
    let item: MediaItem
    let store: LuminaStore
    var poster = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                TVArtwork(item: item, store: store, aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9)
                    .frame(maxWidth: .infinity)
                    .overlay(alignment: .bottom) {
                        if item.resumeSeconds > 0, item.durationSeconds > 0 {
                            ProgressView(value: min(item.resumeSeconds, item.durationSeconds), total: item.durationSeconds)
                                .progressViewStyle(.linear).tint(LuminaTheme.gold)
                                .frame(height: 3).background(.black.opacity(0.4))
                                .accessibilityLabel("Watch progress")
                                .accessibilityValue("\(Int(min(1, item.resumeSeconds / item.durationSeconds) * 100)) percent")
                        }
                    }

                Text(item.name)
                    .font(.system(size: 23, weight: .medium))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(item.displaySubtitle)
                    .font(.system(size: 19))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)

            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9))
        .accessibilityLabel("\(item.name), \(item.displaySubtitle)")
    }
}

/// Focus follows the artwork; captions stay directly on the page.
struct TVArtworkButtonStyle: ButtonStyle {
    let aspect: CGFloat
    @Environment(\.isFocused) private var isFocused
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .overlay(alignment: .top) {
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(isFocused ? .white : .clear, lineWidth: 3)
                    .aspectRatio(aspect, contentMode: .fit)
            }
            .scaleEffect(isFocused ? 1.04 : 1, anchor: .top)
            .opacity(configuration.isPressed ? 0.75 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: isFocused)
    }
}

struct TVArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    let aspect: CGFloat

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
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [LuminaTheme.gold.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: item.isAudio || item.type.hasPrefix("Music") ? "music.note" : "film")
                .font(.largeTitle)
                .foregroundStyle(LuminaTheme.ink.opacity(0.7))
        }
    }

    private var artworkURL: URL? {
        aspect > 1 ? store.backdropURL(for: item, maxWidth: 760) ?? store.imageURL(for: item, maxWidth: 760) : store.imageURL(for: item, maxWidth: 760)
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
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
        }
        .frame(maxWidth: .infinity, minHeight: 360)
        .padding(48)
    }
}
