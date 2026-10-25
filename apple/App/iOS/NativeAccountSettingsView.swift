import SwiftUI
import LuminaCore

/// The native settings surface prioritizes this installation; account administration stays on the web.
@MainActor
struct NativeAccountSettingsView: View {
    let store: LuminaStore
    @Environment(LuminaUserSettings.self) private var settings
    @AppStorage("lumina.appearance") private var accountAppearance = "dark"
    @State private var query = ""
    @State private var licensesPresented = false
    @State private var creditsPresented = false
    @State private var editingHome = false
    @State private var profile: SettingsValue = .null
    @State private var profileOwner: String?
    @State private var profileError: String?
    @FocusState private var searchFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text("Settings").font(LuminaTheme.editorial(36, relativeTo: .largeTitle)).fixedSize(horizontal: true, vertical: false)
                    Spacer(minLength: 12)
                    Text(store.session?.username ?? "").font(.subheadline).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                HStack(spacing: 10) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary).accessibilityHidden(true)
                    TextField("Find a setting", text: $query)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .textFieldStyle(.plain)
                        .focused($searchFocused).submitLabel(.search)
                        .onSubmit { searchFocused = false }
                        .accessibilityIdentifier("settingsSearchField")
                    if !query.isEmpty {
                        Button("Clear search", systemImage: "xmark") { query = "" }
                            .labelStyle(.iconOnly).buttonStyle(.plain).frame(minWidth: 44, minHeight: 44)
                    }
                }
                .frame(minHeight: 44)
                .overlay(alignment: .bottom) { Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1) }

                DeviceSettingsView(store: store, embedded: true, query: query)

                if matches("home layout rows shelves personalize edit") {
                    LuminaSettingsSection("Home") {
                        Button { editingHome = true } label: {
                            LuminaSettingsActionLabel("Edit Home", symbol: "slider.horizontal.3")
                        }
                        .buttonStyle(.plain).accessibilityIdentifier("homeEditButton")
                    }
                }

                if let session = store.session, profileOwner == session.accessToken, profile["role"]?.stringValue == "admin" {
                    NativeAdminQuickActions(settings: settings, session: session,
                        requiresTwoFactor: profile["two_factor_enabled"]?.boolValue == true, query: query)
                        .id(session.accessToken)
                }
                if let profileError {
                    HStack {
                        Text(profileError).font(.caption).foregroundStyle(.red)
                        Spacer()
                        Button("Retry") { Task { await loadProfile() } }.buttonStyle(.plain)
                    }
                }
                if matches("account security password sign out logout") {
                    LuminaSettingsSection("Account") {
                        if let server = store.session?.serverURL,
                           let accountURL = URL(string: "/settings", relativeTo: server)?.absoluteURL {
                            Link(destination: accountURL) {
                                LuminaSettingsActionLabel("Account & security", symbol: "arrow.up.right")
                            }
                            .buttonStyle(.plain).accessibilityIdentifier("accountWebSettingsLink")
                        }
                        Button("Sign out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
                            Task { store.clearError(); await store.signOut() }
                        }
                        .buttonStyle(.plain).frame(minHeight: 44).accessibilityIdentifier("signOutButton")
                    }
                }
                if matches("about credits licenses legal") {
                    LuminaSettingsSection("About") {
                        Button { licensesPresented = true } label: {
                            LuminaSettingsActionLabel("Open-source licenses", symbol: "doc.text")
                        }
                        .buttonStyle(.plain).accessibilityIdentifier("accountLicensesButton")
                        Button { creditsPresented = true } label: {
                            LuminaSettingsActionLabel("Credits", symbol: "info.circle")
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 920)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .background(LuminaTheme.paper)
        .scrollDismissesKeyboard(.interactively)
        .foregroundStyle(LuminaTheme.ink)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarVisibility(.visible, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .accessibilityIdentifier("accountScreen")
        .sheet(isPresented: $licensesPresented) {
            PlaybackLicensesView().accessibilityIdentifier("accountLicensesSheet")
        }
        .sheet(isPresented: $creditsPresented) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        Text("Movie and show artwork and metadata are provided by TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.")
                        Link("All credits", destination: URL(string: "https://github.com/shiftedx/lumina/blob/main/CREDITS.md")!)
                    }.padding(24)
                }
                .navigationTitle("Credits")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { creditsPresented = false } } }
            }
        }
        .sheet(isPresented: $editingHome) { NativeHomePreferencesView(store: store) }
        .task(id: store.session?.accessToken) { await loadProfile() }
        .onChange(of: settings.document, initial: true) { _, _ in
            if settings.hasLoaded { accountAppearance = settings.theme }
        }
    }

    private func matches(_ keywords: String) -> Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy {
            keywords.localizedCaseInsensitiveContains(String($0))
        }
    }

    private func loadProfile() async {
        let owner = store.session?.accessToken
        profile = .null; profileOwner = nil; profileError = nil
        do {
            let loaded = try await settings.action("profile")
            guard owner == store.session?.accessToken else { return }
            profile = loaded; profileOwner = owner
        } catch is CancellationError { }
        catch { if owner == store.session?.accessToken { profileError = error.localizedDescription } }
    }
}

@MainActor
private struct NativeAdminQuickActions: View {
    let settings: LuminaUserSettings
    let session: LuminaSession
    let requiresTwoFactor: Bool
    let query: String
    @State private var roots: [SettingsValue] = []
    @State private var scanning = false
    @State private var activityVisible = false
    @State private var result: String?
    @State private var error: String?

    var body: some View {
        if query.split(whereSeparator: \.isWhitespace).allSatisfy({
            "server admin scan library activity invite users invitations manage".localizedCaseInsensitiveContains(String($0))
        }) {
            LuminaSettingsSection("Server") {
                Button { Task { await scan() } } label: {
                    LuminaSettingsActionLabel(scanning ? "Starting scan…" : "Scan libraries", symbol: "arrow.triangle.2.circlepath", chevron: nil)
                }
                .buttonStyle(.plain).disabled(scanning)
                .accessibilityIdentifier("adminScanLibrariesButton")
                Button { activityVisible.toggle(); if activityVisible { Task { await loadActivity() } } } label: {
                    LuminaSettingsActionLabel("Library activity", symbol: "waveform.path",
                                             chevron: activityVisible ? "chevron.up" : "chevron.down")
                }
                .buttonStyle(.plain).accessibilityIdentifier("adminLibraryActivityButton")
                if activityVisible {
                    ForEach(roots.compactMap(LibraryActivityRow.init)) { root in
                        HStack { Text(root.label); Spacer(); Text(root.state).foregroundStyle(.secondary) }
                            .font(.subheadline).frame(minHeight: 44)
                    }
                    if roots.isEmpty { Text("No active libraries").font(.subheadline).foregroundStyle(.secondary) }
                    Button("Refresh activity") { Task { await loadActivity() } }
                        .buttonStyle(.plain).frame(minHeight: 44)
                }
                NavigationLink {
                    NativeInvitationsView(settings: settings, serverURL: session.serverURL, requiresTwoFactor: requiresTwoFactor)
                } label: {
                    LuminaSettingsActionLabel("Invite users", symbol: "person.badge.plus")
                }
                .buttonStyle(.plain).accessibilityIdentifier("adminInvitationsButton")
                Link(destination: session.serverURL.appendingPathComponent("manage")) {
                    LuminaSettingsActionLabel("Manage server", symbol: "arrow.up.right")
                }
                .buttonStyle(.plain)
                if let result { Text(result).font(.caption).accessibilityIdentifier("adminScanResult") }
                if let error { Text(error).font(.caption).foregroundStyle(.red).accessibilityIdentifier("adminActionError") }
            }
            .accessibilityIdentifier("nativeAdminSettings")
        }
    }

    private func loadActivity() async {
        do {
            roots = try await settings.action("admin/library")["roots"]?.arrayValue ?? []
            error = nil
        } catch { self.error = adminMessage(error) }
    }

    private func scan() async {
        guard !scanning else { return }
        scanning = true; error = nil; result = nil
        defer { scanning = false }
        do {
            let response = try await settings.action("admin/library/scan", method: "POST")
            let started = response["started"]?.arrayValue?.count ?? 0
            let queued = response["queued"]?.arrayValue?.count ?? 0
            let skipped = response["skipped"]?.arrayValue?.count ?? 0
            result = "\(started) scanning · \(queued) queued · \(skipped) skipped"
            if activityVisible { await loadActivity() }
        } catch { self.error = adminMessage(error) }
    }

    private struct LibraryActivityRow: Identifiable {
        let id: String
        let label: String
        let state: String
        init?(_ value: SettingsValue) {
            guard let id = value["root_id"]?.stringValue else { return nil }
            self.id = id; label = value["label"]?.stringValue ?? "Library"
            state = (value["state"]?.stringValue ?? "Ready").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

@MainActor
private struct NativeInvitationsView: View {
    let settings: LuminaUserSettings
    let serverURL: URL
    let requiresTwoFactor: Bool
    @State private var email = ""
    @State private var sections = Set<String>()
    @State private var password = ""
    @State private var code = ""
    @State private var invitations: [Invitation] = []
    @State private var invitationURL: URL?
    @State private var revoking: Invitation?
    @State private var busy = false
    @State private var error: String?
    private enum Field: Hashable { case email, password, code }
    @FocusState private var editingField: Field?
    private let libraries = [("movies", "Movies"), ("shows", "TV"), ("anime", "Anime"),
                             ("music", "Music"), ("photos", "Photos"), ("books", "Books")]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let revoking {
                    LuminaSettingsSection("Revoke invitation") {
                        Text(revoking.email).font(.headline)
                        verification
                        Button("Revoke invitation", role: .destructive) { Task { await revoke(revoking) } }
                            .buttonStyle(.plain).frame(minHeight: 44).disabled(!canVerify || busy)
                            .accessibilityIdentifier("adminRevokeInviteConfirm")
                        Button("Cancel") { self.revoking = nil; clearCredentials() }.buttonStyle(.plain).frame(minHeight: 44)
                    }
                } else {
                    if let invitationURL {
                        LuminaSettingsSection("Invitation ready") {
                            ShareLink(item: invitationURL) { LuminaSettingsActionLabel("Share invitation", symbol: "square.and.arrow.up") }
                                .buttonStyle(.plain).accessibilityIdentifier("adminShareInvitation")
                            Button("Invite another") { self.invitationURL = nil; email = ""; sections = [] }
                                .buttonStyle(.plain).frame(minHeight: 44)
                        }
                    } else {
                        LuminaSettingsSection("New invitation") {
                            TextField("Email address", text: $email)
                                .textContentType(.emailAddress).keyboardType(.emailAddress)
                                .textInputAutocapitalization(.never).autocorrectionDisabled()
                                .focused($editingField, equals: .email).submitLabel(.done)
                                .onSubmit { editingField = nil }
                                .textFieldStyle(.plain).frame(minHeight: 44)
                                .accessibilityIdentifier("adminInviteEmail")
                            Text("Libraries").font(.subheadline).foregroundStyle(.secondary)
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), alignment: .leading)], alignment: .leading, spacing: 8) {
                                ForEach(libraries, id: \.0) { key, title in
                                    Button(title) {
                                        if sections.contains(key) { sections.remove(key) } else { sections.insert(key) }
                                    }
                                    .buttonStyle(LuminaUnderlineTabStyle(selected: sections.contains(key)))
                                    .accessibilityAddTraits(sections.contains(key) ? .isSelected : [])
                                    .accessibilityIdentifier("adminInviteLibrary-\(key)")
                                }
                            }
                            verification
                            Button(busy ? "Creating…" : "Create invitation") { Task { await create() } }
                                .buttonStyle(.plain).frame(minHeight: 44)
                                .disabled(email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sections.isEmpty || !canVerify || busy)
                                .accessibilityIdentifier("adminCreateInvitation")
                        }
                    }
                    LuminaSettingsSection("Invitations") {
                        ForEach(invitations) { invitation in
                            HStack(alignment: .firstTextBaseline, spacing: 16) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(invitation.email).lineLimit(2)
                                    Text(invitation.status.capitalized).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 8)
                                if invitation.status == "pending" {
                                    Button("Revoke", role: .destructive) { revoking = invitation; clearCredentials() }
                                        .buttonStyle(.plain).frame(minHeight: 44)
                                        .accessibilityIdentifier("adminRevokeInvite-\(invitation.id)")
                                }
                            }
                        }
                        if invitations.isEmpty { Text("No invitations").foregroundStyle(.secondary) }
                    }
                }
                if let error { Text(error).font(.callout).foregroundStyle(.red).accessibilityIdentifier("adminInviteError") }
            }
            .padding(24).frame(maxWidth: 920).frame(maxWidth: .infinity, alignment: .top)
        }
        .background(LuminaTheme.paper).foregroundStyle(LuminaTheme.ink)
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Invitations")
        .accessibilityIdentifier("nativeInvitationsScreen")
        .task { await reload() }
        .onDisappear { clearCredentials(); invitationURL = nil }
    }

    private var canVerify: Bool { !password.isEmpty && (!requiresTwoFactor || !code.isEmpty) }

    private var verification: some View {
        VStack(alignment: .leading, spacing: 4) {
            SecureField("Admin password", text: $password)
                .textContentType(.password).textFieldStyle(.plain).frame(minHeight: 44)
                .focused($editingField, equals: .password).submitLabel(.done).onSubmit { editingField = nil }
                .privacySensitive().accessibilityIdentifier("adminVerificationPassword")
            if requiresTwoFactor {
                TextField("Authenticator code", text: $code)
                    .keyboardType(.numberPad).textContentType(.oneTimeCode).textFieldStyle(.plain).frame(minHeight: 44)
                    .focused($editingField, equals: .code)
                    .privacySensitive().accessibilityIdentifier("adminVerificationCode")
            }
        }
    }

    private func proof() async throws -> String {
        var body: [String: SettingsValue] = ["password": .string(password)]
        if !code.isEmpty { body["code"] = .string(code) }
        let response = try await settings.action("reauth", method: "POST", body: body)
        guard let proof = response["proof"]?.stringValue else { throw UserSettingsError.malformedResponse }
        return proof
    }

    private func create() async {
        guard !busy, canVerify else { return }
        busy = true; error = nil
        defer { busy = false; clearCredentials() }
        do {
            let verified = try await proof()
            clearCredentials()
            let response = try await settings.action("admin/invites", method: "POST", body: [
                "email": .string(email.trimmingCharacters(in: .whitespacesAndNewlines)),
                "send": .bool(false),
                "access": .object(["sections": .array(sections.sorted().map(SettingsValue.string))]),
            ], reauth: verified)
            guard let raw = response["invitation_url"]?.stringValue,
                  let url = URL(string: raw, relativeTo: serverURL)?.absoluteURL, ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
                  url.host != nil else { throw UserSettingsError.malformedResponse }
            invitationURL = url
            await reload()
        } catch { self.error = adminMessage(error) }
    }

    private func revoke(_ invitation: Invitation) async {
        guard !busy, canVerify else { return }
        busy = true; error = nil
        defer { busy = false; clearCredentials() }
        do {
            let verified = try await proof()
            clearCredentials()
            _ = try await settings.action("admin/invites/\(invitation.id)", method: "DELETE", reauth: verified)
            revoking = nil
            await reload()
        } catch { self.error = adminMessage(error) }
    }

    private func reload() async {
        do { invitations = try await settings.action("admin/invites").arrayValue?.compactMap(Invitation.init) ?? [] }
        catch { self.error = adminMessage(error) }
    }

    private func clearCredentials() { password = ""; code = ""; editingField = nil }

    private struct Invitation: Identifiable {
        let id: String
        let email: String
        let status: String
        init?(_ value: SettingsValue) {
            guard let id = value["id"]?.stringValue, let email = value["email"]?.stringValue else { return nil }
            self.id = id; self.email = email; status = value["status"]?.stringValue ?? "pending"
        }
    }
}

private func adminMessage(_ error: Error) -> String {
    if let error = error as? UserSettingsError {
        if error == .server(404) { return "Update the server to use native admin actions." }
        if error == .server(422) { return "Check the invitation details." }
    }
    return error.localizedDescription
}
