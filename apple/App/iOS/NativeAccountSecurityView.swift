import SwiftUI
import LuminaCore

@MainActor
struct NativeAccountSecurityView: View {
    let settings: LuminaUserSettings
    let store: LuminaStore
    let query: String
    @State private var status: SettingsValue = .null
    @State private var apps: [SettingsValue] = []
    @State private var action: AccountSecurityAction?
    @State private var error: String?

    var body: some View {
        Group {
            if matches("security password two-step verification 2fa authenticator recovery codes") {
                Section("Security") {
                    Button("Change password") { action = .password }
                        .accessibilityIdentifier("accountChangePassword")
                    LabeledContent("Two-step verification", value: status["enabled"]?.boolValue == true ? "On" : "Off")
                    if status["enabled"]?.boolValue == true {
                        LabeledContent("Recovery codes remaining", value: String(Int(status["recovery_codes_left"]?.numberValue ?? 0)))
                        Button("New recovery codes") { action = .recovery }
                        if status["required"]?.boolValue != true {
                            Button("Turn off two-step verification", role: .destructive) { action = .disable }
                        }
                    } else {
                        Button("Turn on two-step verification") { action = .setup }
                    }
                    Text("Changes require your account password and, when enabled, an authenticator or recovery code.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if matches("connected apps devices tokens app passwords infuse revoke sign out all") {
                Section("Connected apps") {
                    Button("Create app password") { action = .appPassword }
                    Button("Create agent token") { action = .token }
                    ForEach(apps, id: \.accountRecordID) { app in
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(app["device_name"]?.stringValue ?? "Connected app")
                                Text(app["client"]?.stringValue ?? app["kind"]?.stringValue ?? "")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Revoke", role: .destructive) { action = .revoke(app.accountRecordID) }
                                .buttonStyle(.borderless)
                        }
                    }
                    if apps.isEmpty { Text("No connected apps to show.").foregroundStyle(.secondary) }
                    Button("Sign out all apps", role: .destructive) { action = .signOutAll }
                }
            }
            if let error {
                Section { Text(error).foregroundStyle(.red); Button("Retry account security") { Task { await reload() } } }
            }
        }
        .task { await reload() }
        .sheet(item: $action, onDismiss: { Task { await reload() } }) { action in
            AccountSecuritySheet(settings: settings, store: store, action: action)
        }
    }

    private func matches(_ text: String) -> Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy { text.localizedCaseInsensitiveContains(String($0)) }
    }
    private func reload() async {
        do {
            status = try await settings.action("two-factor")
            apps = try await settings.action("connected-apps").arrayValue ?? []
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

private enum AccountSecurityAction: Identifiable {
    case password, setup, disable, recovery, appPassword, token, signOutAll, revoke(String)
    var id: String {
        switch self {
        case .password: "password"
        case .setup: "setup"
        case .disable: "disable"
        case .recovery: "recovery"
        case .appPassword: "app-password"
        case .token: "token"
        case .signOutAll: "sign-out-all"
        case .revoke(let id): "revoke-\(id)"
        }
    }
    var title: String {
        switch self {
        case .password: "Change password"
        case .setup: "Set up two-step verification"
        case .disable: "Turn off two-step verification"
        case .recovery: "New recovery codes"
        case .appPassword: "Create app password"
        case .token: "Create agent token"
        case .signOutAll: "Sign out all apps"
        case .revoke: "Revoke app access"
        }
    }
    var needsName: Bool { if case .appPassword = self { true } else if case .token = self { true } else { false } }
}

@MainActor
private struct AccountSecuritySheet: View {
    let settings: LuminaUserSettings
    let store: LuminaStore
    let action: AccountSecurityAction
    @State private var password = ""
    @State private var code = ""
    @State private var recoveryCode = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var name = ""
    @State private var scope = "read"
    @State private var setup: SettingsValue?
    @State private var enrollmentCode = ""
    @State private var result: SettingsValue?
    @State private var busy = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            Form {
                if let result {
                    resultView(result)
                } else {
                    if let setup {
                        Section("Authenticator setup") {
                            Text("Add this key to your authenticator, then enter the new six-digit code. The key is shown only during this setup.")
                            Text(setup["secret"]?.stringValue ?? "").font(.body.monospaced()).textSelection(.enabled)
                            if let raw = setup["otpauth_uri"]?.stringValue, let url = URL(string: raw), url.scheme == "otpauth" {
                                Link("Add to authenticator", destination: url)
                            }
                            TextField("New authenticator code", text: $enrollmentCode).keyboardType(.numberPad)
                        }
                    }
                    Section("Verify your identity") {
                        SecureField("Account password", text: $password).textContentType(.password)
                            .accessibilityIdentifier("accountVerificationPassword")
                        TextField("Authenticator code, if enabled", text: $code).textContentType(.oneTimeCode)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        SecureField("Or a recovery code", text: $recoveryCode)
                        Text("An app password cannot authorize account changes.").font(.footnote).foregroundStyle(.secondary)
                    }
                    if case .password = action {
                        Section("New password") {
                            SecureField("New password", text: $newPassword).textContentType(.newPassword)
                            SecureField("Confirm new password", text: $confirmPassword).textContentType(.newPassword)
                            Text("At least 12 characters. Other devices, including this app, will be signed out.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if action.needsName {
                        Section("App") {
                            TextField("App name", text: $name)
                            if case .token = action {
                                Picker("Permission", selection: $scope) { Text("Read only").tag("read"); Text("Read & write").tag("write") }
                            }
                        }
                    }
                    if case .signOutAll = action {
                        Text("Jellyfin apps and agent tokens will be signed out, including this app. App passwords keep working until you revoke them.")
                    }
                    if case .revoke = action { Text("This app’s token will stop working immediately.") }
                    if let error { Text(error).foregroundStyle(.red) }
                    Button(busy ? "Working…" : setup == nil ? action.title : "Enable two-step verification") { Task { await perform() } }
                        .disabled(!canSubmit || busy)
                        .accessibilityIdentifier("accountSecuritySubmit")
                }
            }
            .disabled(busy)
            .navigationTitle(action.title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) {
                Button(result == nil ? "Cancel" : "Done") { dismiss() }.disabled(busy)
                    .accessibilityIdentifier("accountSecurityCancel")
            } }
            .interactiveDismissDisabled(busy || result?["recovery_codes"] != nil)
        }
        .accessibilityIdentifier("accountSecuritySheet")
        .overlay {
            if scenePhase != .active {
                LuminaTheme.paper.ignoresSafeArea()
                Label("Lumina", systemImage: "lock.fill").font(.headline)
            }
        }
        .onChange(of: scenePhase) { _, phase in if phase == .background { clearProofInputs() } }
        .onDisappear { clearProofInputs(); newPassword = ""; confirmPassword = ""; setup = nil; result = nil }
    }

    private var canSubmit: Bool {
        guard !password.isEmpty else { return false }
        if setup != nil { return enrollmentCode.count == 6 }
        if case .password = action { return newPassword.count >= 12 && newPassword == confirmPassword }
        if action.needsName { return !name.trimmingCharacters(in: .whitespaces).isEmpty }
        return true
    }

    @ViewBuilder private func resultView(_ value: SettingsValue) -> some View {
        if let codes = value["recovery_codes"]?.arrayValue {
            Section("Save your recovery codes") {
                Text("These are shown once. Each code can be used once in place of an authenticator code.")
                ForEach(codes.compactMap(\.stringValue), id: \.self) { Text($0).font(.body.monospaced()).textSelection(.enabled) }
                ShareLink("Save recovery codes", item: codes.compactMap(\.stringValue).joined(separator: "\n"))
            }
        } else if let secret = value["password"]?.stringValue ?? value["token"]?.stringValue {
            Section("Shown once") {
                Text("Save this now. Lumina cannot show it again.")
                Text(secret).font(.body.monospaced()).textSelection(.enabled)
                ShareLink("Save securely", item: secret)
            }
        } else { Text("Your account has been updated.") }
    }

    private func perform() async {
        busy = true; error = nil
        defer { busy = false }
        do {
            var proofBody: [String: SettingsValue] = ["password": .string(password)]
            if !code.isEmpty { proofBody["code"] = .string(code.trimmingCharacters(in: .whitespaces)) }
            if !recoveryCode.isEmpty { proofBody["recovery_code"] = .string(recoveryCode.trimmingCharacters(in: .whitespaces)) }
            let authentication = try await settings.action("reauth", method: "POST", body: proofBody)
            guard let proof = authentication["proof"]?.stringValue else { throw AccountSecurityError.missingProof }
            clearProofInputs()
            let path: String
            var body: [String: SettingsValue] = [:]
            var method = "POST"
            switch action {
            case .password: path = "password"; body = ["new_password": .string(newPassword)]
            case .setup:
                path = setup == nil ? "two-factor/setup" : "two-factor/enable"
                if setup != nil { body = ["code": .string(enrollmentCode)] }
            case .disable: path = "two-factor/disable"
            case .recovery: path = "two-factor/recovery-codes"
            case .appPassword: path = "connected-apps/app-passwords"; body = ["name": .string(name)]
            case .token: path = "connected-apps"; body = ["name": .string(name), "scope": .string(scope)]
            case .signOutAll: path = "connected-apps/sign-out-all"
            case .revoke(let id): path = "connected-apps/\(id)"; method = "DELETE"
            }
            let response = try await settings.action(path, method: method, body: body, reauth: proof)
            if case .setup = action, setup == nil { setup = response; return }
            newPassword = ""; confirmPassword = ""
            if case .password = action { await store.signOut(); dismiss(); return }
            if case .signOutAll = action { await store.signOut(); dismiss(); return }
            result = response
        } catch UserSettingsError.server(403) {
            self.error = "The password or verification code was not accepted. Use your account password and a fresh code."
        } catch { self.error = error.localizedDescription }
    }
    private func clearProofInputs() { password = ""; code = ""; recoveryCode = "" }
}

private enum AccountSecurityError: LocalizedError {
    case missingProof
    var errorDescription: String? { "Identity verification did not complete. Try again." }
}

private extension SettingsValue {
    var accountRecordID: String { self["id"]?.stringValue ?? "" }
}
