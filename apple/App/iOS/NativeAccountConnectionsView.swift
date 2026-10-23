import SwiftUI
import LuminaCore

@MainActor
struct NativeAccountConnectionsView: View {
    let settings: LuminaUserSettings
    let query: String
    @State private var connections: [SettingsValue] = []
    @State private var events: [SettingsValue] = []
    @State private var action: PersonalConnectionAction?
    @State private var error: String?
    @State private var page = 1

    var body: some View {
        Group {
            if matches("listening history export music scrobble Last.fm ListenBrainz") {
                Section("Listening history export") {
                    Text("Optionally export eligible browser music listens to your own account.").font(.footnote).foregroundStyle(.secondary)
                    ForEach(["listenbrainz", "lastfm"], id: \.self) { provider in
                        let connection = connections.first { $0["provider"]?.stringValue == provider }
                        LabeledContent(provider == "lastfm" ? "Last.fm" : "ListenBrainz", value: connection?["enabled"]?.boolValue == true ? connection?["username"]?.stringValue ?? "Connected" : "Not connected")
                        Button(provider == "lastfm" ? "Connect Last.fm" : "Connect ListenBrainz") { action = provider == "lastfm" ? .lastfm : .listenbrainz }
                        if provider == "lastfm", connection?["authorization_pending"]?.boolValue == true {
                            Button("Finish Last.fm connection") { action = .finishLastfm }
                        }
                        if connection?["credentials_set"]?.boolValue == true {
                            Button("Disconnect \(provider == "lastfm" ? "Last.fm" : "ListenBrainz")", role: .destructive) { action = .disconnect(provider) }
                        }
                    }
                    ForEach(events, id: \.connectionRecordID) { event in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(event["title"]?.stringValue ?? "Listening export").font(.subheadline.weight(.medium))
                            ForEach((event["deliveries"]?.objectValue ?? [:]).keys.sorted(), id: \.self) { provider in
                                Text("\(provider): \(event["deliveries"]?[provider]?["status"]?.stringValue ?? "Unknown")").font(.caption).foregroundStyle(.secondary)
                            }
                            if (event["deliveries"]?.objectValue ?? [:]).values.contains(where: { $0["status"]?.stringValue == "uncertain" }) {
                                Button("Check provider history") { action = .reconcile(event.connectionRecordID) }
                            }
                        }
                    }
                    HStack {
                        Button("Previous") { page -= 1; Task { await loadEvents() } }.disabled(page <= 1)
                        Spacer()
                        Text("Page \(page)").foregroundStyle(.secondary)
                        Spacer()
                        Button("Next") { page += 1; Task { await loadEvents() } }.disabled(events.count < 50)
                    }.buttonStyle(.borderless)
                }
            }
            if matches("Privacy import watch history Jellyfin Plex watched resume favorites") {
                Section {
                    Button("Import from Jellyfin") { action = .jellyfin }
                        .accessibilityIdentifier("accountImportJellyfinButton")
                    Button("Import from Plex") { action = .plex }
                    Text("Preview changes before importing. Newer Lumina watch progress is preserved.").font(.footnote).foregroundStyle(.secondary)
                } header: {
                    Text("Import watch history").accessibilityIdentifier("accountConnectionsImportSection")
                }
            }
            if let error, query.isEmpty || matches("connections listening import error") {
                Section { Text(error).foregroundStyle(.red); Button("Retry connections") { Task { await load() } } }
            }
        }
        .task(id: settings.hasLoaded) { if settings.hasLoaded { await load() } }
        .sheet(item: $action, onDismiss: { Task { await load() } }) { selected in
            PersonalConnectionSheet(settings: settings, action: selected)
        }
    }
    private func matches(_ text: String) -> Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy { text.localizedCaseInsensitiveContains(String($0)) }
    }
    private func load() async {
        do {
            connections = try await settings.action("listening-export")["items"]?.arrayValue ?? []
            await loadEvents()
            error = nil
        } catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }
    private func loadEvents() async {
        do { events = try await settings.action("listening-events/page/\(page)")["items"]?.arrayValue ?? [] }
        catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }
}

private enum PersonalConnectionAction: Identifiable {
    case listenbrainz, lastfm, finishLastfm, disconnect(String), jellyfin, plex, reconcile(String)
    var id: String {
        switch self {
        case .disconnect(let provider): "disconnect-\(provider)"
        case .reconcile(let event): "reconcile-\(event)"
        default: title
        }
    }
    var title: String {
        switch self {
        case .listenbrainz: "Connect ListenBrainz"
        case .lastfm: "Connect Last.fm"
        case .finishLastfm: "Finish Last.fm connection"
        case .disconnect(let provider): "Disconnect \(provider)"
        case .jellyfin: "Import from Jellyfin"
        case .plex: "Import from Plex"
        case .reconcile: "Check provider history"
        }
    }
    var importPath: String? { if case .jellyfin = self { "jellyfin-import" } else if case .plex = self { "plex-import" } else { nil } }
}

@MainActor
private struct PersonalConnectionSheet: View {
    let settings: LuminaUserSettings
    let action: PersonalConnectionAction
    @State private var password = ""
    @State private var code = ""
    @State private var credential = ""
    @State private var apiKey = ""
    @State private var username = ""
    @State private var preview: SettingsValue?
    @State private var importAvailable: Bool?
    @State private var importServer: String?
    @State private var result: SettingsValue?
    @State private var busy = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            Form {
                if let result {
                    Section {
                        Text("Completed.")
                        if let raw = result["authorization_url"]?.stringValue, let url = URL(string: raw), url.scheme == "https", ["last.fm", "www.last.fm"].contains(url.host?.lowercased() ?? "") {
                            Link("Approve in Last.fm", destination: url)
                            Text("After approval, close this sheet and choose Finish Last.fm connection.").font(.footnote)
                        }
                        if action.importPath != nil { summary(result) }
                    }
                } else if importAvailable == false {
                    Text("Ask a vault owner to configure this import source on the server first.")
                        .accessibilityIdentifier("accountConnectionUnavailable")
                } else {
                    fields
                    if let preview {
                        Section("Preview") {
                            summary(preview)
                            Text("Newer progress in Lumina is preserved. Nothing changes until you choose Import.").font(.footnote)
                        }
                    }
                    Section("Verify your identity") {
                        SecureField("Lumina account password", text: $password).textContentType(.password)
                        TextField("Authenticator or recovery code, if enabled", text: $code)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                    }
                    if let error { Text(error).foregroundStyle(.red) }
                    Button(busy ? "Working…" : action.importPath == nil ? action.title : preview == nil ? "Preview" : "Import history") { Task { await perform() } }
                        .disabled(busy || password.isEmpty || !ready)
                }
            }
            .disabled(busy)
            .navigationTitle(action.title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) {
                Button(result == nil ? "Cancel" : "Done") { dismiss() }
                    .disabled(busy)
                    .accessibilityIdentifier("accountConnectionCancel")
            } }
            .interactiveDismissDisabled(busy)
        }
        .accessibilityIdentifier("accountConnectionSheet")
        .task {
            if let path = action.importPath {
                do {
                    importServer = try await settings.action(path)["server"]?.stringValue
                    importAvailable = importServer != nil
                }
                catch { self.error = error.localizedDescription }
            }
        }
        .onChange(of: scenePhase) { _, phase in if phase == .background { clearSecrets() } }
        .onDisappear { clearSecrets() }
    }

    @ViewBuilder private var fields: some View {
        switch action {
        case .listenbrainz:
            Section("ListenBrainz") {
                SecureField("User token", text: $credential)
                Link("Find my token", destination: URL(string: "https://listenbrainz.org/profile/")!)
            }
        case .lastfm:
            Section("Last.fm API account") {
                SecureField("API key", text: $apiKey)
                SecureField("API secret", text: $credential)
                Link("Create an API account", destination: URL(string: "https://www.last.fm/api/account/create")!)
            }
        case .jellyfin:
            Section("Jellyfin sign-in") {
                if let importServer { Text("Server: \(importServer)").font(.footnote).foregroundStyle(.secondary) }
                TextField("Jellyfin username", text: $username).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Jellyfin password", text: $credential)
            }
        case .plex:
            Section("Plex account") {
                if let importServer { Text("Server: \(importServer)").font(.footnote).foregroundStyle(.secondary) }
                SecureField("Plex token", text: $credential)
                Text("Favorites and watchlists are not transferred from Plex.").font(.footnote)
            }
        default: Text("This changes only your account.")
        }
    }
    @ViewBuilder private func summary(_ value: SettingsValue) -> some View {
        ForEach(["watched", "in_progress", "favorites", "up_to_date", "unmatched"], id: \.self) { key in
            if let number = value[key]?.numberValue { LabeledContent(key.replacingOccurrences(of: "_", with: " ").capitalized, value: String(Int(number))) }
        }
        ForEach(value["unmatched_names"]?.arrayValue?.compactMap(\.stringValue) ?? [], id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
    }
    private var ready: Bool {
        switch action {
        case .listenbrainz, .plex: !credential.isEmpty
        case .lastfm: apiKey.count == 32 && credential.count == 32
        case .jellyfin: !username.trimmingCharacters(in: .whitespaces).isEmpty
        default: true
        }
    }
    private func perform() async {
        busy = true; error = nil
        defer { busy = false }
        do {
            var credentials: [String: SettingsValue] = ["password": .string(password)]
            if !code.isEmpty {
                let trimmed = code.trimmingCharacters(in: .whitespaces)
                credentials[trimmed.count == 6 && trimmed.allSatisfy(\.isNumber) ? "code" : "recovery_code"] = .string(trimmed)
            }
            let authentication = try await settings.action("reauth", method: "POST", body: credentials)
            guard let proof = authentication["proof"]?.stringValue else { throw PersonalConnectionError.proof }
            password = ""; code = ""
            var method = "POST"
            let path: String
            var body: [String: SettingsValue] = [:]
            switch action {
            case .listenbrainz: path = "listening-export/listenbrainz"; method = "PUT"; body = ["token": .string(credential)]
            case .lastfm: path = "listening-export/lastfm/authorize"; body = ["api_key": .string(apiKey), "api_secret": .string(credential)]
            case .finishLastfm: path = "listening-export/lastfm/finish"
            case .disconnect(let provider): path = "listening-export/\(provider)"; method = "DELETE"
            case .jellyfin, .plex:
                path = (action.importPath ?? "") + (preview == nil ? "/preview" : "")
                body = action.importPath == "jellyfin-import" ? ["username": .string(username), "password": .string(credential)] : ["token": .string(credential)]
            case .reconcile(let id): path = "listening-events/\(id)/reconcile"
            }
            let response = try await settings.action(path, method: method, body: body, reauth: proof)
            if action.importPath != nil, preview == nil { preview = response; return }
            result = response
            clearSecrets()
        } catch UserSettingsError.server(403) {
            self.error = "The password or verification code was not accepted. Use your account password and a fresh code."
        } catch { self.error = error.localizedDescription }
    }
    private func clearSecrets() { password = ""; code = ""; credential = ""; apiKey = "" }
}

private enum PersonalConnectionError: LocalizedError {
    case proof
    var errorDescription: String? { "Identity verification did not complete. Try again." }
}
private extension SettingsValue { var connectionRecordID: String { self["id"]?.stringValue ?? "" } }
