import SwiftUI
import LuminaCore

/// The living-room client: a small native shell over the shared Lumina session and library store.
public struct TVRootView: View {
    private let store: LuminaStore
    @State private var path: [MediaItem] = []
    @State private var playbackRequest: PlaybackRequest?

    public init(store: LuminaStore) {
        self.store = store
    }

    public var body: some View {
        NavigationStack(path: $path) {
            Group {
                if store.session == nil {
                    TVConnectionView(store: store)
                } else {
                    TVLibraryView(store: store) { item in
                        path.append(item)
                    } onSignOut: {
                        path.removeAll()
                    }
                }
            }
            .navigationDestination(for: MediaItem.self) { item in
                TVDetailView(item: item, store: store) { request in
                    playbackRequest = request
                }
            }
        }
        .tint(LuminaTheme.gold)
        .preferredColorScheme(.dark)
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
    }
}

private struct TVConnectionView: View {
    let store: LuminaStore
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @FocusState private var focusedField: Field?

    private enum Field: Hashable {
        case server, username, password, connect
    }

    var body: some View {
        ZStack {
            LuminaTheme.paper.ignoresSafeArea()

            VStack(alignment: .leading, spacing: 28) {
                Spacer()

                Text("Lumina")
                    .font(.system(size: 72, weight: .bold, design: .serif))
                    .italic()
                    .foregroundStyle(LuminaTheme.ink)

                Text("Bring your household library to the biggest screen.")
                    .font(.title2)
                    .foregroundStyle(LuminaTheme.muted)

                VStack(alignment: .leading, spacing: 18) {
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

                    if let error = store.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.body)
                            .accessibilityLabel("Connection error: \(error)")
                            .accessibilityIdentifier("connectionError")
                    }

                    Button {
                        store.clearError()
                        Task {
                            await store.connect(server: server, username: username, password: password)
                            password = ""
                        }
                    } label: {
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
                    .disabled(store.isBusy || server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || username.isEmpty || password.isEmpty)
                }
                .frame(maxWidth: 720)

                Text("First enable Apps like Infuse in Lumina on the web: Manage Server → Connections → Metadata. Sign in with your Lumina account. If it uses two-factor authentication, create an app password in Connected apps.")
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
                    .frame(maxWidth: 820, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer()
            }
            .padding(.horizontal, 140)
            .padding(.vertical, 72)
        }
        .defaultFocus($focusedField, .server)
    }
}

private struct TVLibraryView: View {
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onSignOut: () -> Void
    @State private var query = ""
    @State private var requestedInitialData = false
    @State private var lastFocusedItemID: String?
    @FocusState private var focusedItemID: String?

    private var shownItems: [MediaItem] { store.items.filter(\.isPlayable) }
    private var resumeItems: [MediaItem] { store.resumeItems.filter(\.isPlayable) }

    var body: some View {
        ZStack {
            LuminaTheme.paper.ignoresSafeArea()

            VStack(spacing: 20) {
                header
                    .padding(.horizontal, 82)
                    .padding(.top, 54)
                    .focusSection()

                ScrollView {
                    VStack(alignment: .leading, spacing: 42) {
                    if store.isBusy && shownItems.isEmpty && resumeItems.isEmpty {
                        ProgressView("Loading your library…")
                            .frame(maxWidth: .infinity, minHeight: 360)
                    } else if let error = store.errorMessage, shownItems.isEmpty {
                        TVMessageView(
                            title: "Your library could not load",
                            message: error,
                            actionTitle: "Try again",
                            action: reload
                        )
                    } else if shownItems.isEmpty {
                        TVMessageView(
                            title: query.isEmpty ? "No videos yet" : "No matching videos",
                            message: query.isEmpty ? "Add videos to Lumina on the web, then refresh your library." : "Try another title, series, or year.",
                            actionTitle: query.isEmpty ? "Refresh" : "Clear search",
                            action: query.isEmpty ? reload : { query = "" }
                        )
                    } else {
                        if !resumeItems.isEmpty && query.isEmpty {
                            TVRail(title: "Continue watching", items: resumeItems, store: store, onOpen: open)
                                .focusSection()
                        }

                        VStack(alignment: .leading, spacing: 18) {
                            Text(query.isEmpty ? "Your library" : "Results")
                                .font(.system(size: 38, weight: .semibold, design: .serif))
                                .italic()
                                .foregroundStyle(LuminaTheme.ink)

                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 250, maximum: 330), spacing: 28)], spacing: 36) {
                                ForEach(shownItems) { item in
                                    TVMediaCard(item: item, store: store) { open(item) }
                                        .focused($focusedItemID, equals: item.id)
                                        .accessibilityIdentifier("libraryItem-\(item.id)")
                                }
                            }
                            .focusSection()

                            if store.hasMore {
                                Button("Show more", action: loadMore)
                                    .buttonStyle(.bordered)
                                    .controlSize(.large)
                                    .frame(maxWidth: .infinity)
                            }
                        }
                    }
                }
                    .padding(.horizontal, 82)
                    .padding(.top, 16)
                    .padding(.bottom, 54)
                }
                .accessibilityIdentifier("libraryScreen")
            }
        }
        .task {
            guard !requestedInitialData else { return }
            requestedInitialData = true
            await store.loadResume()
            await store.loadLibrary()
        }
        .task(id: query) {
            guard requestedInitialData, !Task.isCancelled else { return }
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            await store.loadLibrary(search: query)
        }
        .onChange(of: shownItems.map(\.id)) {
            guard let lastFocusedItemID, shownItems.contains(where: { $0.id == lastFocusedItemID }) else { return }
            focusedItemID = lastFocusedItemID
        }
        .onChange(of: focusedItemID) {
            if let focusedItemID { lastFocusedItemID = focusedItemID }
        }
    }

    private var header: some View {
        HStack(alignment: .bottom, spacing: 28) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Lumina")
                    .font(.system(size: 56, weight: .bold, design: .serif))
                    .italic()
                    .foregroundStyle(LuminaTheme.ink)
                Text(store.session?.serverName ?? "Household library")
                    .font(.title3)
                    .foregroundStyle(LuminaTheme.muted)
            }

            Spacer()

            TextField("Search your library", text: $query)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .frame(width: 430)
                .accessibilityIdentifier("searchField")

            Button(action: reload) {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityIdentifier("refreshButton")

            Button(role: .destructive) {
                Task {
                    await store.signOut()
                    onSignOut()
                }
            } label: {
                Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityIdentifier("signOutButton")
        }
    }

    private func open(_ item: MediaItem) {
        lastFocusedItemID = item.id
        onOpen(item)
    }

    private func reload() {
        store.clearError()
        Task { await store.refresh() }
    }

    private func loadMore() {
        Task { await store.loadMore() }
    }
}

private struct TVRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(title)
                .font(.system(size: 38, weight: .semibold, design: .serif))
                .italic()
                .foregroundStyle(LuminaTheme.ink)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 28) {
                    ForEach(items) { item in
                        TVMediaCard(item: item, store: store) { onOpen(item) }
                            .frame(width: 360)
                            .accessibilityIdentifier("libraryItem-\(item.id)")
                    }
                }
                .padding(.vertical, 10)
            }
        }
    }
}

private struct TVMediaCard: View {
    let item: MediaItem
    let store: LuminaStore
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                TVArtwork(item: item, store: store)
                    .frame(maxWidth: .infinity)
                    .aspectRatio(16 / 9, contentMode: .fit)

                Text(item.name)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(LuminaTheme.ink)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(item.displaySubtitle)
                    .font(.body)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)

                if item.resumeSeconds > 0, item.durationSeconds > 0 {
                    ProgressView(value: item.resumeSeconds, total: item.durationSeconds)
                        .tint(LuminaTheme.gold)
                        .accessibilityLabel("Watch progress")
                        .accessibilityValue("\(Int((item.resumeSeconds / item.durationSeconds) * 100)) percent")
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(item.name), \(item.displaySubtitle)")
    }
}

private struct TVArtwork: View {
    let item: MediaItem
    let store: LuminaStore

    var body: some View {
        Group {
            if let url = store.imageURL(for: item, maxWidth: 760) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        placeholder
                    default:
                        ZStack { placeholder; ProgressView() }
                    }
                }
            } else {
                placeholder
            }
        }
        .clipped()
        .clipShape(.rect(cornerRadius: 8))
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [LuminaTheme.gold.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            Image(systemName: "film")
                .font(.largeTitle)
                .foregroundStyle(LuminaTheme.ink.opacity(0.7))
        }
    }
}

private struct TVDetailView: View {
    let item: MediaItem
    let store: LuminaStore
    let onPlayback: (PlaybackRequest) -> Void
    @State private var isPreparing = false
    @State private var playbackError: String?
    @FocusState private var focusedAction: Action?

    private enum Action: Hashable { case play, convert }

    var body: some View {
        ZStack {
            LuminaTheme.paper.ignoresSafeArea()

            ScrollView {
                HStack(alignment: .top, spacing: 52) {
                    TVArtwork(item: item, store: store)
                        .frame(width: 640, height: 360)

                    VStack(alignment: .leading, spacing: 24) {
                        Text(item.name)
                            .font(.system(size: 56, weight: .bold, design: .serif))
                            .italic()
                            .foregroundStyle(LuminaTheme.ink)
                            .fixedSize(horizontal: false, vertical: true)

                        Text(item.displaySubtitle)
                            .font(.title2)
                            .foregroundStyle(LuminaTheme.muted)

                        if let overview = item.overview, !overview.isEmpty {
                            Text(overview)
                                .font(.title3)
                                .foregroundStyle(LuminaTheme.ink)
                                .lineLimit(6)
                        }

                        if let playbackError {
                            Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.red)
                                .font(.body)
                        }

                        HStack(spacing: 18) {
                            Button {
                                preparePlayback(forceTranscode: false)
                            } label: {
                                Label(item.resumeSeconds > 5 ? "Resume" : "Play", systemImage: "play.fill")
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                            .accessibilityIdentifier("playButton")
                            .focused($focusedAction, equals: .play)
                            .disabled(isPreparing)

                            Button {
                                preparePlayback(forceTranscode: true)
                            } label: {
                                Label("Convert for compatibility", systemImage: "arrow.triangle.2.circlepath")
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.large)
                            .accessibilityIdentifier("convertButton")
                            .focused($focusedAction, equals: .convert)
                            .disabled(isPreparing)
                        }

                        if isPreparing {
                            ProgressView("Preparing playback…")
                                .foregroundStyle(LuminaTheme.muted)
                        }
                    }
                    .frame(maxWidth: 760, alignment: .leading)
                }
                .padding(.horizontal, 112)
                .padding(.vertical, 88)
            }
            .accessibilityIdentifier("detailScreen")
        }
        .defaultFocus($focusedAction, .play)
        .navigationBarBackButtonHidden(false)
    }

    private func preparePlayback(forceTranscode: Bool) {
        playbackError = nil
        isPreparing = true
        Task {
            defer { isPreparing = false }
            do {
                onPlayback(try await store.preparePlayback(for: item, forceTranscode: forceTranscode))
            } catch {
                playbackError = error.localizedDescription
            }
        }
    }
}

private struct TVMessageView: View {
    let title: String
    let message: String
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            Text(title)
                .font(.system(size: 42, weight: .semibold, design: .serif))
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
