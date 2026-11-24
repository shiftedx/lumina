import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVConnectionView: View {
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
                    .font(TVTypography.sectionTitle)
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
            Text("Sign in").font(TVTypography.pageTitle)
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
            Text("Sign in manually").font(TVTypography.sectionTitle)
            TextField("Server address", text: $server)
                .font(TVTypography.body)
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
                        .font(TVTypography.body)
                        .accessibilityLabel("Phone sign-in error: \(tvPairingError)")
                        .accessibilityIdentifier("tvPairingStatus")
                }
            }
        }
    }

    private var nearbySignIn: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Sign in with your phone").font(TVTypography.sectionTitle)
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
                    .font(TVTypography.body)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if tvPairingRequest != nil || tvPairingError != nil {
                Text(tvPairingStatusText)
                    .font(TVTypography.body)
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
                .font(TVTypography.body)
                .textContentType(.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("usernameField")
                .focused($focusedField, equals: .username)

            SecureField("Password or app password", text: $password)
                .font(TVTypography.body)
                .textContentType(.password)
                .accessibilityIdentifier("passwordField")
                .focused($focusedField, equals: .password)

            if let error = store.errorMessage, !error.isEmpty {
                Label(error, glyph: .warning)
                    .foregroundStyle(LuminaTheme.accentInk)
                    .font(TVTypography.body)
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
                .font(TVTypography.sectionTitle)
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
                        .font(TVTypography.body)
                        .foregroundStyle(.white.opacity(0.72))
                    Text(request.code)
                        .font(.system(.title2, design: .monospaced, weight: .semibold))
                        .accessibilityIdentifier("tvPairingCode")
                        .accessibilityLabel(request.code)
                        .accessibilityHint("Confirmation code")
                        .accessibilityValue(request.code)
                    Text(tvPairingStatusText)
                        .font(TVTypography.body)
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
                .font(TVTypography.pageTitle)

            Text("For phone sign-in, connect both devices to the same network and sign in to Lumina on your phone first.")
                .font(TVTypography.body)
                .foregroundStyle(LuminaTheme.muted)
                .fixedSize(horizontal: false, vertical: true)

            Text("For manual sign-in, enter your server address and Lumina account. If you use two-factor authentication, enter an app password.")
                .font(TVTypography.body)
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

enum TVDestination: String, CaseIterable {
    case home, library, requests
    var title: String { rawValue.capitalized }
}
