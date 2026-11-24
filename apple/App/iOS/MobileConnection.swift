import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct ConnectionView: View {
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
            let centerForm = UIDevice.current.userInterfaceIdiom == .pad || usesStandardPhonePushedChrome
            ScrollView {
                connectionForm
                    .frame(maxWidth: 420)
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("connectionForm")
                    .padding(.horizontal, geometry.size.width > 700 ? 64 : 28)
                    .padding(.vertical, 32)
                    .frame(maxWidth: .infinity, minHeight: geometry.size.height,
                           alignment: centerForm ? .center : (geometry.size.width > 700 ? .leading : .bottom))
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
                        if geometry.size.width > 700 && !centerForm {
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
                    .font(LuminaTheme.heading(28, relativeTo: .title2))
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

enum LibraryDestination: Hashable, CaseIterable {
    case home, library, streaming, requests, account
}

struct TVApprovalRequest: Identifiable, Hashable {
    let code: String
    let expectedServerID: String?
    var id: String { "\(expectedServerID ?? "manual"):\(code)" }
}

enum LibraryCategory: String, CaseIterable {
    case movies, tv, anime, music, books, streaming

    var title: String {
        switch self {
        case .movies: "Movies"
        case .tv: "Shows"
        case .anime: "Anime"
        case .music: "Music"
        case .books: "Books"
        case .streaming: "Streaming"
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
