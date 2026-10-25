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
            .onPlayPauseCommand(perform: toggleAudioPlayback)
            .navigationDestination(for: MediaItem.self) { item in
                if item.isAudio || item.type == "MusicAlbum" || item.type == "MusicArtist" {
                    TVMusicDetailView(item: item, store: store) { path.append($0) }
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
        .environment(audioSession)
        .tint(LuminaTheme.ink)
    }

    private func toggleAudioPlayback() {
        // Keep background music reachable without intercepting a music detail's
        // command to start its album when no session exists yet.
        guard playbackRequest == nil, audioSession.playback != nil else { return }
        Task { await audioSession.toggle() }
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
                .buttonStyle(LuminaQuietActionStyle(primary: true))
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
    case home, library, requests
    var title: String { rawValue.capitalized }
}

private enum TVLibraryCategory: String, CaseIterable {
    case movies, tv, anime, music

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "TV"
        case .anime: "Anime"
        case .music: "Music"
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
    @State private var homeLayout = LuminaHomeLayout()
    @State private var homeFeed = LuminaHomeFeed()
    @State private var homeSessionScope: TVHomeSessionScope?
    @State private var destinationHistory = [TVDestination]()
    @State private var homeHeroID: String?
    @State private var homeCategory: HomeBrowseCategory = .all
    @State private var libraryCategory: TVLibraryCategory = .movies
    @State private var videoBrowse = TVVideoBrowseState()
    @State private var audioBrowse = TVAudioBrowseState()
    @State private var deviceSettingsPresented = false
    @State private var contentFocusRequest = 0
    @State private var restorationDestination: TVDestination?
    @State private var restorationRequest = 0
    @FocusState private var focusedDestination: TVDestination?
    @FocusState private var focusedPrimaryCategory: HomeBrowsingDestination?
    @FocusState private var searchFocused: Bool
    @FocusState private var accountFocused: Bool

    var body: some View {
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
                            focusRequest: contentFocusRequest
                        )
                    } else {
                        ProgressView("Loading Home…")
                    }
                case .library:
                    if libraryCategory == .music {
                        TVAudioView(
                            store: store,
                            onOpen: onOpen,
                            browse: $audioBrowse,
                            focusRequest: contentFocusRequest,
                            restoreFocusRequest: restorationDestination == .library ? restorationRequest : nil,
                            onRequestHeaderFocus: { focusedPrimaryCategory = .music }
                        )
                    } else {
                        TVVideoView(
                            store: store,
                            onOpen: onOpen,
                            browse: $videoBrowse,
                            focusRequest: contentFocusRequest,
                            restoreFocusRequest: restorationDestination == .library ? restorationRequest : nil
                        )
                    }
                case .requests:
                    TVRequestsView(store: store, focusRequest: contentFocusRequest)
                }
            }
            .padding(.top, contentTopPadding)
            header
                .padding(.horizontal, 80)
                .padding(.top, 12)
                .padding(.bottom, 12)
                .background {
                    LinearGradient(colors: [.black.opacity(0.55), .clear], startPoint: .top, endPoint: .bottom)
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        .allowsHitTesting(false)
                }
                .focusSection()
                .onMoveCommand { direction in
                    if direction == .down, destination == .requests { contentFocusRequest += 1 }
                }
                .ignoresSafeArea(.container, edges: .horizontal)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .sheet(isPresented: $homeSearchPresented) {
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
                onClose: { deviceSettingsPresented = false }
            )
            .background(LuminaTheme.theater.ignoresSafeArea())
            .preferredColorScheme(.dark)
        }
        .task(id: TVHomeSessionScope(store.session)) {
            homeLayout.configure(session: store.session)
            homeFeed.configure(session: store.session)
            homeSessionScope = TVHomeSessionScope(store.session)
        }
        .onChange(of: store.availableLibraryCategories) { _, available in
            if destination == .library, !available.contains(libraryCategory.rawValue) {
                selectHomeDestination(.all)
            }
        }
        .onExitCommand(perform: backAction)
    }

    private var backAction: (() -> Void)? {
        guard isAtNavigationRoot, !destinationHistory.isEmpty else { return nil }
        return { returnToPreviousDestination() }
    }

    private var contentTopPadding: CGFloat {
        switch destination {
        case .home: 0
        case .library: libraryCategory == .music ? 0 : 112
        case .requests: 112
        }
    }

    /// Keep chronological selections, including revisited tabs. Back only pops
    /// this trail so each press returns to the preceding browsing context.
    private func selectDestination(_ next: TVDestination) {
        guard next != destination else { return }
        destinationHistory.append(destination)
        if destinationHistory.count > 32 {
            destinationHistory.removeFirst(destinationHistory.count - 32)
        }
        destination = next
        restorationDestination = nil
    }

    private func selectHomeDestination(_ selected: HomeBrowsingDestination) {
        switch selected {
        case .all:
            homeCategory = .all
            selectDestination(.home)
        case .watchlist:
            homeCategory = .watchlist
            selectDestination(.home)
        case .movies, .tv, .anime:
            selectLibraryCategory(selected == .movies ? .movies : selected == .tv ? .tv : .anime)
            selectDestination(.library)
        case .music:
            selectLibraryCategory(.music)
            selectDestination(.library)
        case .books:
            break
        }
    }

    private func returnToPreviousDestination() {
        guard let previous = destinationHistory.popLast() else { return }
        destination = previous
        restorationDestination = previous
        restorationRequest &+= 1
        // A tab button should not steal the restored card's focus.
        focusedDestination = nil
    }

    private func selectLibraryCategory(_ category: TVLibraryCategory) {
        libraryCategory = category
        if category == .music {
            audioBrowse.kind = .albums
        } else {
            videoBrowse.kind = category.videoKind
        }
    }

    private var header: some View {
        HStack(spacing: 0) {
            Button { selectHomeDestination(.all) } label: {
                HStack(alignment: .lastTextBaseline, spacing: 10) {
                    LuminaBrandMark().frame(width: 42, height: 26)
                        .alignmentGuide(.lastTextBaseline) { $0[.bottom] }
                    Text("LUMINA")
                        .font(.system(size: 26, weight: .heavy))
                        .tracking(3)
                        .foregroundStyle(LuminaTheme.ink)
                }
                .frame(width: 260, alignment: .leading)
            }
            .buttonStyle(LuminaUnderlineTabStyle(selected: false, alignsWithMasthead: true))
            .accessibilityLabel("Lumina")
            .accessibilityHint("Return to Home")
            .accessibilityIdentifier("homeMasthead")

            HStack(spacing: 20) {
                ForEach(primaryCategories, id: \.self) { category in
                    let selected = selectedPrimaryCategory == category
                    Button { selectHomeDestination(category) } label: {
                        Text(category.title)
                    }
                    .buttonStyle(LuminaUnderlineTabStyle(selected: selected, alignsWithMasthead: true))
                    .focused($focusedPrimaryCategory, equals: category)
                    .accessibilityValue(selected ? "Selected" : "")
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .accessibilityIdentifier("homeBrowse-\(category.rawValue)")
                    .id(category.rawValue)
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

                utilityButton(.requests, glyph: .requests, identifier: "requestsButton")
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

    private var primaryCategories: [HomeBrowsingDestination] {
        [.movies, .tv, .anime, .music, .watchlist].filter {
            $0.homeCategory != nil || store.availableLibraryCategories.contains($0.rawValue)
        }
    }

    private var selectedPrimaryCategory: HomeBrowsingDestination? {
        switch destination {
        case .home: homeCategory == .watchlist ? .watchlist : nil
        case .library:
            switch libraryCategory {
            case .movies: .movies
            case .tv: .tv
            case .anime: .anime
            case .music: .music
            }
        case .requests:
            nil
        }
    }

    private func utilityButton(_ target: TVDestination, glyph: LuminaGlyph, identifier: String) -> some View {
        let selected = destination == target
        return Button { selectDestination(target) } label: {
            LuminaIcon(glyph: glyph, variant: selected ? .solid : .outline, size: 28)
                .foregroundStyle(selected ? LuminaTheme.accentInk : focusedDestination == target ? LuminaTheme.ink : LuminaTheme.muted)
                .frame(width: 64, height: 64)
        }
        .buttonStyle(TVNavigationButtonStyle())
        .focusEffectDisabled()
        .focused($focusedDestination, equals: target)
        .accessibilityLabel(target.title)
        .accessibilityValue(selected ? "Selected" : "")
        .accessibilityAddTraits(selected ? .isSelected : [])
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

private struct TVHomeView: View {
    let store: LuminaStore
    let layout: LuminaHomeLayout
    let feed: LuminaHomeFeed
    @Binding var heroSelection: String?
    @Binding var category: HomeBrowseCategory
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    @Environment(\.scenePhase) private var scenePhase

    @State private var loadingInitialLibrary = false
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
            ScrollView {
                VStack(alignment: .leading, spacing: 32) {
                    if !snapshot.items.isEmpty {
                        TVFeaturedView(
                            store: store,
                            items: snapshot.items,
                            heroSelection: $heroSelection,
                            onOpen: onOpen,
                            onPlayback: onPlayback,
                            focusRequest: focusRequest,
                            minimumHeight: min(900, max(620, geometry.size.height * 0.82)),
                            onWatchlistChanged: { await feed.load() }
                        )
                            .focusSection()
                    } else if category != .all {
                        Text("No watchlist titles are available. Choose Home or add something to your watchlist.")
                            .foregroundStyle(LuminaTheme.muted)
                            .padding(.horizontal, 80)
                            .padding(.top, 112)
                    } else if store.isHomeBusy {
                        ProgressView("Loading Home…").frame(maxWidth: .infinity, minHeight: 650)
                    } else {
                        TVMessageView(title: "Your next great watch starts here", message: store.errorMessage ?? "Add media to Lumina to fill Home with your household library.", actionTitle: "Refresh") {
                            Task { await store.loadHome() }
                        }
                        .padding(.top, 112)
                    }
                    if feed.hasLoaded {
                        ForEach(shelves.filter(\.visible)) { shelf in
                        ForEach(snapshot.rows.filter { $0.shelfID == shelf.id }) { row in
                            TVRail(title: row.title, items: row.items, store: store,
                                   poster: row.shape == .poster,
                                   identifierPrefix: row.shelfID == .continueWatching ? "resumeItem" : row.shelfID == .nextUp ? "nextUpItem" : "homeRow-\(row.id)",
                                   onOpen: onOpen)
                                .focusSection()
                        }
                        }
                    } else {
                        ForEach(shelves.filter(\.visible)) { shelf in
                            homeShelf(shelf.id)
                        }
                    }
                    if let error = feed.errorMessage {
                        TVMessageView(title: "Some Home shelves could not load", message: error, actionTitle: "Try again") {
                            Task { await feed.load() }
                        }
                    }
                }
                .padding(.bottom, 80)
            }
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .accessibilityIdentifier("homeScreen")
        .task(id: TVHomeSessionScope(store.session)) {
            guard store.session != nil else { return }
            let needsInitialLibraryLoad = !layout.hasLoaded || !feed.hasLoaded
            loadingInitialLibrary = needsInitialLibraryLoad
            defer { loadingInitialLibrary = false }
            async let savedLayout: Void = layout.load()
            async let rows: Void = feed.load()
            if needsInitialLibraryLoad {
                async let library: Void = store.loadHome()
                _ = await (library, savedLayout, rows)
            } else {
                async let navigation: Void = store.refreshNavigationCategories()
                _ = await (savedLayout, rows, navigation)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await layout.load(); await feed.load(); await store.refreshNavigationCategories() }
            }
        }
        .onChange(of: store.resumeItems) { _, _ in
            if feed.hasLoaded, !loadingInitialLibrary { Task { await feed.load() } }
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
    @FocusState private var alphabetFocused: Bool

    private var requestKey: String { "\(browse.kind.rawValue)-\(browse.sort.rawValue)-\(browse.query)-\(browse.filters.genre)-\(browse.filters.year ?? 0)-\(browse.filters.unwatchedOnly)-\(browse.filters.favoritesOnly)" }
    private var hasFilters: Bool { browse.filters != VideoFilters() }
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
                if hasFilters {
                    Button("Clear") { browse.filters = VideoFilters() }
                        .buttonStyle(LuminaUnderlineTabStyle())
                        .accessibilityLabel("Clear filters")
                        .accessibilityIdentifier("videoClearFiltersButton")
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
                            TVMessageView(title: browse.query.isEmpty && !hasFilters ? "No videos here yet" : "No matching videos", message: store.errorMessage ?? "Try another search or choose a different category.", actionTitle: "Refresh") { Task { await reload() } }
                        } else {
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 52) {
                                ForEach(store.items) { item in
                                    TVMediaCard(item: item, store: store, poster: true, showsCaption: false) { onOpen(item) }
                                        .focused($focusedItemID, equals: item.id)
                                        .id(item.id)
                                        .accessibilityIdentifier("libraryItem-\(item.id)")
                                        .onAppear { itemDidAppear(item) }
                                }
                            }
                            .padding(.horizontal, 80)
                            .padding(.top, 24)
                            .focusSection()
                            .accessibilityIdentifier("libraryPosterWall")
                            if store.hasMore {
                                Button("Show more") { Task { await store.loadMore() } }
                                    .buttonStyle(LuminaQuietActionStyle())
                                    .padding(32)
                            }
                            if let error = store.errorMessage {
                                Text(error).foregroundStyle(LuminaTheme.accentInk).padding(24)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityIdentifier("libraryScreen")
                    .onScrollGeometryChange(for: Int.self) { geometry in
                        Int(geometry.contentOffset.y / 128)
                    } action: { _, offset in
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
                                focusedItemID = store.items.first { $0.id == browse.focusedItemID }?.id ?? store.items.first?.id
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
                            focusedItemID = store.items.first { $0.id == browse.focusedItemID }?.id ?? store.items.first?.id
                        }
                        .padding(.trailing, 32)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .ignoresSafeArea(.container, edges: .horizontal)
        .onChange(of: focusedItemID) { _, itemID in
            if let itemID { browse.focusedItemID = itemID }
        }
        .onChange(of: restoreFocusRequest, initial: true) { _, request in
            guard request != nil else { return }
            pendingRestoredItemID = browse.focusedItemID
            restoreLastSelectionIfAvailable()
        }
        .onChange(of: store.items.map(\.id)) { _, _ in restoreLastSelectionIfAvailable() }
        .task(id: lastVisibleItemID, priority: .utility) {
            await store.prefetchArtwork(after: lastVisibleItemID, in: store.items, maxWidth: 760)
        }
        .sheet(isPresented: $filtersPresented) {
            TVVideoFiltersView(filters: browse.filters) { browse.filters = $0 }
        }
        .onDisappear { alphabetTask?.cancel() }
        .task(id: requestKey) {
            let requestedKey = requestKey
            alphabetTask?.cancel()
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
        [browse.filters.genre.isEmpty ? nil : browse.filters.genre, browse.filters.year.map(String.init), browse.filters.unwatchedOnly ? "Unwatched" : nil, browse.filters.favoritesOnly ? "Favorites" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func focusVisibleGridItem() {
        focusedItemID = store.items.first { $0.id == browse.focusedItemID }?.id ?? store.items.first?.id
    }

    private func itemDidAppear(_ item: MediaItem) {
        lastVisibleItemID = item.id
        let loadMoreAnchor = store.items.suffix(6).first?.id
        guard store.hasMore, item.id == loadMoreAnchor else { return }
        Task { await store.loadMore() }
    }

    private func restoreLastSelectionIfAvailable() {
        guard let itemID = pendingRestoredItemID,
              store.items.contains(where: { $0.id == itemID }) else { return }
        focusedItemID = itemID
        pendingRestoredItemID = nil
    }

    private func reload() async {
        await store.loadLibrary(search: browse.query, kind: browse.kind, sort: browse.sort, filters: browse.filters)
    }
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
            ScrollView {
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
                        TVMediaCard(item: item, store: store, poster: presentsPosters, captionPresentation: captionPresentation) { onOpen(item) }
                            .frame(width: item.isAudio || item.type.hasPrefix("Music") ? 260 : presentsPosters ? 220 : 400)
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
    var captionPresentation: TVRailCaptionPresentation = .standard
    var showsCaption = true
    let action: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var caption: TVRailCaption {
        TVRailCaption(item: item, presentation: captionPresentation)
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                TVArtwork(item: item, store: store, aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9)
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
            LinearGradient(colors: [LuminaTheme.brand.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            LuminaIcon(glyph: item.isAudio || item.type.hasPrefix("Music") ? .music : .movies,
                       variant: .solid, size: 28)
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
