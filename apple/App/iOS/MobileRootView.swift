import SwiftUI
import LuminaCore

@MainActor
struct MobileRootView: View {
    @Bindable var store: LuminaStore

    var body: some View {
        Group {
            if store.session == nil {
                ConnectionView(store: store)
            } else {
                LibraryShellView(store: store)
            }
        }
        .tint(LuminaTheme.gold)
        .foregroundStyle(LuminaTheme.ink)
        .background(LuminaTheme.paper.ignoresSafeArea())
    }
}

@MainActor
private struct ConnectionView: View {
    @Bindable var store: LuminaStore

    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @FocusState private var focusedField: Field?

    private enum Field { case server, username, password }

    private var canConnect: Bool {
        !server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !password.isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Lumina")
                        .font(.system(.largeTitle, design: .serif))
                        .fontWeight(.bold)
                        .italic()
                    Text("Your household library, ready for the room you are in.")
                        .font(.system(.title3, design: .serif))
                        .foregroundStyle(LuminaTheme.muted)
                }

                VStack(alignment: .leading, spacing: 16) {
                    ConnectionField(title: "Server URL") {
                        TextField("https://lumina.example", text: $server)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($focusedField, equals: .server)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .username }
                            .accessibilityIdentifier("serverURLField")
                    }

                    ConnectionField(title: "Username") {
                        TextField("Household account", text: $username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($focusedField, equals: .username)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .password }
                            .accessibilityIdentifier("usernameField")
                    }

                    ConnectionField(title: "Password") {
                        SecureField("Password or app password", text: $password)
                            .focused($focusedField, equals: .password)
                            .submitLabel(.go)
                            .onSubmit { connect() }
                            .accessibilityIdentifier("passwordField")
                    }
                }

                Button {
                    connect()
                } label: {
                    HStack(spacing: 10) {
                        if store.isBusy {
                            ProgressView()
                                .tint(LuminaTheme.paper)
                        }
                        Text(store.isBusy ? "Connecting…" : "Connect")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!canConnect || store.isBusy)
                .accessibilityIdentifier("connectButton")

                VStack(alignment: .leading, spacing: 10) {
                    Text("Server setup")
                        .font(.headline)
                    Text("An administrator must enable Lumina’s opt-in Jellyfin-compatible API in Manage Server → Connections → Metadata → Apps like Infuse before this app can connect. It is off by default.")
                        .font(.footnote)
                        .foregroundStyle(LuminaTheme.muted)
                    Text("If this account uses two-factor authentication, enter an app password in the password field. Lumina uses it only for this sign-in attempt.")
                        .font(.footnote)
                        .foregroundStyle(LuminaTheme.muted)
                }

                if let message = store.errorMessage, !message.isEmpty {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.gold)
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(.isStaticText)
                        .accessibilityIdentifier("connectionError")
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 36)
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
        }
        .background(LuminaTheme.paper)
        .scrollDismissesKeyboard(.interactively)
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
}

@MainActor
private struct ConnectionField<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.subheadline.weight(.semibold))
            content
                .padding(.horizontal, 14)
                .frame(minHeight: 50)
                .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 12))
        }
    }
}

private enum LibrarySection: Hashable {
    case video
}

@MainActor
private struct LibraryShellView: View {
    @Bindable var store: LuminaStore
    @State private var selection: LibrarySection? = .video

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("Library") {
                    Label("Video", systemImage: "film.stack")
                        .tag(LibrarySection.video)
                }
                if let serverName = store.session?.serverName, !serverName.isEmpty {
                    Section { Text(serverName).foregroundStyle(LuminaTheme.muted) }
                }
            }
            .navigationTitle("Lumina")
            .listStyle(.sidebar)
        } detail: {
            NavigationStack {
                VideoLibraryView(store: store)
                    .navigationDestination(for: MediaItem.self) { item in
                        TitleDetailView(item: item, store: store)
                    }
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button {
                                Task {
                                    store.clearError()
                                    await store.signOut()
                                }
                            } label: {
                                Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                            }
                            .disabled(store.isBusy)
                            .accessibilityIdentifier("signOutButton")
                        }
                    }
            }
        }
    }
}

@MainActor
private struct VideoLibraryView: View {
    @Bindable var store: LuminaStore

    @State private var searchText = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Video")
                        .font(.system(.largeTitle, design: .serif))
                        .fontWeight(.bold)
                        .italic()
                    Text("Browse the household library")
                        .font(.subheadline)
                        .foregroundStyle(LuminaTheme.muted)
                    SearchBar(text: $searchText) {
                        search()
                    }
                }

                if store.isBusy && store.items.isEmpty && store.resumeItems.isEmpty {
                    LibraryLoadingView()
                } else if store.items.isEmpty && store.resumeItems.isEmpty {
                    if let message = store.errorMessage, !message.isEmpty {
                        InlineErrorView(message: message) {
                            refresh()
                        }
                    } else {
                        EmptyLibraryView {
                            refresh()
                        }
                    }
                } else {
                    if store.isBusy {
                        ProgressView("Refreshing…")
                            .font(.footnote)
                            .tint(LuminaTheme.gold)
                    }

                    if let message = store.errorMessage, !message.isEmpty {
                        InlineErrorView(message: message) {
                            refresh()
                        }
                    }

                    if !store.resumeItems.isEmpty {
                        MediaRail(title: "Continue Watching", items: store.resumeItems, store: store, identifierPrefix: "resumeItem")
                    }

                    if !store.items.isEmpty {
                        LibraryGrid(items: store.items, store: store)
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
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        .disabled(store.isBusy)
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 28)
            .frame(maxWidth: 1200)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("libraryScreen")
        .refreshable {
            await store.refresh()
        }
        .task {
            await loadInitialContent()
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    refresh()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(store.isBusy)
                .accessibilityIdentifier("refreshButton")
            }
        }
    }

    private func loadInitialContent() async {
        guard store.session != nil else { return }
        if store.items.isEmpty {
            await store.loadLibrary(search: searchText)
        }
        if store.resumeItems.isEmpty {
            await store.loadResume()
        }
    }

    private func search() {
        store.clearError()
        Task {
            await store.loadLibrary(search: searchText.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    private func refresh() {
        store.clearError()
        Task {
            await store.refresh()
        }
    }
}

@MainActor
private struct SearchBar: View {
    @Binding var text: String
    let onSubmit: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .accessibilityHidden(true)
                .foregroundStyle(LuminaTheme.muted)
            TextField("Search video", text: $text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit(onSubmit)
                .accessibilityIdentifier("searchField")
            if !text.isEmpty {
                Button {
                    text = ""
                    onSubmit()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .accessibilityLabel("Clear search")
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 50)
        .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 14))
    }
}

@MainActor
private struct LibraryGrid: View {
    let items: [MediaItem]
    let store: LuminaStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("All video")
                .font(.system(.title2, design: .serif))
                .fontWeight(.semibold)
                .italic()

            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 136, maximum: 220), spacing: 20)],
                alignment: .leading,
                spacing: 28
            ) {
                ForEach(items) { item in
                    NavigationLink(value: item) {
                        MediaCard(item: item, imageURL: store.imageURL(for: item), width: nil)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(mediaAccessibilityLabel(for: item))
                    .accessibilityIdentifier("libraryItem-\(item.id)")
                }
            }
        }
    }
}

@MainActor
private struct MediaRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    let identifierPrefix: String

    @ScaledMetric(relativeTo: .body) private var cardWidth: CGFloat = 158

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title)
                .font(.system(.title2, design: .serif))
                .fontWeight(.semibold)
                .italic()

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 16) {
                    ForEach(items) { item in
                        NavigationLink(value: item) {
                            MediaCard(item: item, imageURL: store.imageURL(for: item), width: cardWidth)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(mediaAccessibilityLabel(for: item))
                        .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .padding(.horizontal, 1)
            }
            .scrollClipDisabled()
        }
    }
}

@MainActor
private struct MediaCard: View {
    let item: MediaItem
    let imageURL: URL?
    let width: CGFloat?

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 8) {
            ArtworkView(url: imageURL, name: item.name)
                .overlay(alignment: .bottom) {
                    if progressFraction > 0 {
                        ProgressView(value: progressFraction)
                            .progressViewStyle(.linear)
                            .tint(LuminaTheme.gold)
                            .padding(.horizontal, 8)
                            .padding(.bottom, 8)
                    }
                }

            Text(item.name)
                .font(.system(.subheadline, design: .serif))
                .fontWeight(.semibold)
                .lineLimit(2)

            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle)
                    .font(.caption)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(2)
            }
        }

        if let width {
            content.frame(width: width, alignment: .leading)
        } else {
            content.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var progressFraction: Double {
        guard item.durationSeconds > 0, item.resumeSeconds > 0 else { return 0 }
        return min(max(item.resumeSeconds / item.durationSeconds, 0), 1)
    }
}

@MainActor
private struct ArtworkView: View {
    let url: URL?
    let name: String

    var body: some View {
        ZStack {
            LuminaTheme.ink.opacity(0.08)

            if let url {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .empty:
                        ProgressView()
                            .tint(LuminaTheme.gold)
                    case .success(let image):
                        image
                            .resizable()
                            .scaledToFill()
                    case .failure:
                        placeholder
                    @unknown default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }

            LinearGradient(
                colors: [.clear, LuminaTheme.paper.opacity(0.35)],
                startPoint: .top,
                endPoint: .bottom
            )
            .allowsHitTesting(false)
        }
        .aspectRatio(2 / 3, contentMode: .fit)
        .clipped()
        .clipShape(.rect(cornerRadius: 12))
        .accessibilityHidden(true)
    }

    private var placeholder: some View {
        VStack(spacing: 8) {
            Image(systemName: "film")
                .font(.title2)
            Text(name)
                .font(.caption)
                .multilineTextAlignment(.center)
                .lineLimit(3)
        }
        .foregroundStyle(LuminaTheme.muted)
        .padding(12)
    }
}

@MainActor
private struct TitleDetailView: View {
    let item: MediaItem
    @Bindable var store: LuminaStore

    @State private var playbackRequest: PlaybackRequest?
    @State private var isPreparingPlayback = false
    @State private var playbackError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 24) {
                        ArtworkView(url: store.imageURL(for: item, maxWidth: 600), name: item.name)
                            .frame(width: 184)
                        detailCopy
                    }

                    VStack(alignment: .leading, spacing: 20) {
                        ArtworkView(url: store.imageURL(for: item, maxWidth: 600), name: item.name)
                            .frame(maxWidth: 260)
                            .frame(maxWidth: .infinity, alignment: .center)
                        detailCopy
                    }
                }

                if let overview = item.overview, !overview.isEmpty {
                    Text(overview)
                        .font(.system(.body, design: .serif))
                        .lineSpacing(4)
                        .foregroundStyle(LuminaTheme.ink)
                }

                if let playbackError, !playbackError.isEmpty {
                    Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(LuminaTheme.gold)
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(.isStaticText)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 28)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier("detailScreen")
        .fullScreenCover(item: $playbackRequest, onDismiss: {
            Task { await store.refresh() }
        }) { request in
            PlaybackScreen(request: request, store: store)
        }
    }

    private var detailCopy: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(item.name)
                .font(.system(.largeTitle, design: .serif))
                .fontWeight(.bold)
                .italic()
                .fixedSize(horizontal: false, vertical: true)

            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle)
                    .font(.subheadline)
                    .foregroundStyle(LuminaTheme.muted)
            }

            if item.isPlayable {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) {
                        playbackButton
                        convertButton
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        playbackButton
                        convertButton
                    }
                }
            } else {
                Text("Playback is unavailable for this title.")
                    .font(.callout)
                    .foregroundStyle(LuminaTheme.muted)
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
                Label(playLabel, systemImage: "play.fill")
            }
            .frame(minWidth: 132)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(isPreparingPlayback)
        .accessibilityIdentifier("playButton")
    }

    private var convertButton: some View {
        Button {
            preparePlayback(forceTranscode: true)
        } label: {
            Label("Convert", systemImage: "arrow.triangle.2.circlepath")
                .frame(minWidth: 132)
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
        .disabled(isPreparingPlayback)
        .accessibilityHint("Use compatibility conversion before playback.")
        .accessibilityIdentifier("convertButton")
    }

    private var playLabel: String {
        item.resumeSeconds > 0 ? "Resume" : "Play"
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard item.isPlayable, !isPreparingPlayback else { return }
        isPreparingPlayback = true
        playbackError = nil
        Task {
            do {
                playbackRequest = try await store.preparePlayback(for: item, forceTranscode: forceTranscode)
            } catch {
                playbackError = forceTranscode
                    ? "Compatibility conversion could not start. Check the server and try again."
                    : "Playback could not start. Try Convert for compatibility."
            }
            isPreparingPlayback = false
        }
    }
}

@MainActor
private struct LibraryLoadingView: View {
    private let placeholders = [0, 1, 2, 3, 4, 5]

    var body: some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 136, maximum: 220), spacing: 20)],
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

@MainActor
private struct EmptyLibraryView: View {
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: "film.stack")
                .font(.largeTitle)
                .foregroundStyle(LuminaTheme.gold)
                .accessibilityHidden(true)
            Text("Your video library is quiet.")
                .font(.system(.title2, design: .serif))
                .fontWeight(.semibold)
                .italic()
            Text("Add video to Lumina, then refresh to see it here.")
                .foregroundStyle(LuminaTheme.muted)
            Button("Refresh", action: onRefresh)
                .buttonStyle(.bordered)
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
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.callout)
                .foregroundStyle(LuminaTheme.gold)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isStaticText)
            Button("Try again", action: onRetry)
                .buttonStyle(.bordered)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LuminaTheme.ink.opacity(0.08), in: .rect(cornerRadius: 14))
    }
}

private func mediaAccessibilityLabel(for item: MediaItem) -> String {
    var parts = [item.name]
    if !item.displaySubtitle.isEmpty {
        parts.append(item.displaySubtitle)
    }
    if item.resumeSeconds > 0 {
        parts.append(item.durationSeconds > item.resumeSeconds ? "in progress" : "played")
    }
    return parts.joined(separator: ", ")
}
