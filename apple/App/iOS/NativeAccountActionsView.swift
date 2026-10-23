import SwiftUI
import UniformTypeIdentifiers
import LuminaCore

/// Account actions which do not belong to the preference document itself.
/// The containing account screen supplies the Form and search field; this view
/// contributes only matching native sections.
@MainActor
struct NativeAccountActionsView: View {
    let settings: LuminaUserSettings
    let store: LuminaStore
    let query: String

    @State private var profileName = ""
    @State private var notificationsEnabled = false
    @State private var notificationEmail = ""
    @State private var searchHistory = [NativeSearchHistoryEntry]()
    @State private var interests = [NativeInterestCategory]()
    @State private var selectedInterestKeys = Set<String>()
    @State private var suppressions = [NativeSuppression]()
    @State private var isLoading = false
    @State private var isSavingProfile = false
    @State private var isSavingNotifications = false
    @State private var isSavingInterests = false
    @State private var actionError: String?
    @State private var exportFile: NativeSettingsExport?
    @State private var isExporting = false
    @State private var exportPresented = false
    @State private var clearRecoPresented = false
    @State private var clearingRecommendations = false
    @State private var recommendationsCleared = false
    @State private var clearSearchPresented = false

    private var trimmedQuery: String { query.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        Group {
        if matches("Profile display name household identity") {
            Section("Profile") {
                TextField("Display name", text: $profileName)
                    .textContentType(.name)
                    .submitLabel(.done)
                    .onSubmit { saveProfile() }
                    .accessibilityIdentifier("accountDisplayName")
                Button("Save name") { saveProfile() }
                    .disabled(profileName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSavingProfile)
                    .accessibilityIdentifier("accountSaveNameButton")
                if isSavingProfile { ProgressView("Saving name…") }
            }
        }

        if matches("Notifications request email approved declined ready") {
            Section {
                Toggle("Email me about my requests", isOn: $notificationsEnabled)
                    .accessibilityIdentifier("requestNotificationsEnabled")
                TextField("Email address", text: $notificationEmail)
                    .textContentType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.emailAddress)
                    .autocorrectionDisabled()
                    .disabled(!notificationsEnabled)
                    .accessibilityIdentifier("requestNotificationsEmail")
                Button("Save request notifications") { saveNotifications() }
                    .disabled(isSavingNotifications || (notificationsEnabled && notificationEmail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                    .accessibilityIdentifier("requestNotificationsSaveButton")
                if isSavingNotifications { ProgressView("Saving notifications…") }
            } header: {
                Text("Request notifications")
            } footer: {
                Text("Lumina emails updates about requests only when your household has email configured.")
            }
        }

        if matches("Interests discovery recommendations categories personalize") {
            Section {
                if interests.isEmpty {
                    Text(isLoading ? "Loading interests…" : "No interests are available yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(interests) { interest in
                        Toggle(interest.label, isOn: interestBinding(for: interest.key))
                            .disabled(isSavingInterests)
                            .accessibilityIdentifier("accountInterest-\(interest.key)")
                    }
                    Button("Save interests") { saveInterests() }
                        .disabled(isSavingInterests)
                        .accessibilityIdentifier("accountSaveInterestsButton")
                    if isSavingInterests { ProgressView("Saving interests…") }
                }
            } header: { Text("Your interests").accessibilityIdentifier("accountInterestsSection") } footer: {
                Text("Lumina uses these only to tune recommendations for your account.")
            }
        }

        if matches("Hidden recommendations suppressed restore channels titles discovery") {
            Section {
                if suppressions.isEmpty {
                    Text(isLoading ? "Loading hidden recommendations…" : "Nothing is hidden.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(suppressions) { suppression in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(suppression.name)
                                Text(suppression.scopeLabel)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 8)
                            Button("Restore") { restore(suppression) }
                                .buttonStyle(.borderless)
                                .accessibilityLabel("Restore \(suppression.name)")
                        }
                    }
                }
            } header: { Text("Hidden recommendations") } footer: {
                Text("Restoring makes an item eligible for future recommendations; it does not force it back into a current row.")
            }
        }

        if matches("Data export download privacy json") {
            Section {
                Button("Export my data", systemImage: "square.and.arrow.up") { exportData() }
                    .disabled(isExporting)
                    .accessibilityIdentifier("accountExportButton")
                if isExporting { ProgressView("Preparing your export…") }
                if exportFile != nil {
                    Button("Save export…", systemImage: "folder") { exportPresented = true }
                        .accessibilityIdentifier("accountSaveExportButton")
                }
            } header: {
                Text("Your data")
            } footer: {
                Text("Your export includes your preferences, follows, queue, notes, history, and playback progress.")
            }
        }

        if matches("Recommendation history discovery home reset forget") {
            Section("Recommendation history") {
                Button("Clear recommendation history", role: .destructive) { clearRecoPresented = true }
                    .disabled(clearingRecommendations)
                    .accessibilityIdentifier("accountClearRecoHistoryButton")
                if recommendationsCleared { Text("Recommendation history cleared.").foregroundStyle(.secondary) }
                Text("This forgets what Lumina showed you and opened. Watch history and hidden recommendations stay.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }

        if matches("Search history recent searches suggestions privacy") {
            Section("Search history") {
                if searchHistory.isEmpty {
                    Text(isLoading ? "Loading search history…" : "No search history yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(searchHistory) { entry in
                        HStack {
                            Text(entry.query)
                            Spacer(minLength: 8)
                            Button("Remove", role: .destructive) { removeSearch(entry) }
                                .buttonStyle(.borderless)
                                .accessibilityLabel("Remove \(entry.query) from search history")
                        }
                    }
                    Button("Clear search history", role: .destructive) { clearSearchPresented = true }
                        .accessibilityIdentifier("accountClearSearchHistoryButton")
                }
            }
        }

        if let actionError, matches("Profile notifications allowances interests hidden data recommendation search error") {
            Section {
                Text(actionError).foregroundStyle(.red)
                Button("Try again") { Task { await load() } }
            }
        }
        }
        .task(id: settings.hasLoaded) {
            guard settings.hasLoaded else { return }
            await load()
        }
        .fileExporter(
            isPresented: $exportPresented,
            document: exportFile,
            contentType: .json,
            defaultFilename: "lumina-export"
        ) { result in
            if case let .failure(error) = result { actionError = message(for: error) }
        }
        .confirmationDialog("Clear recommendation history?", isPresented: $clearRecoPresented, titleVisibility: .visible) {
            Button("Clear history", role: .destructive) { clearRecommendationHistory() }
        } message: {
            Text("Lumina will forget recommendations it showed you and opened. Your watch history and hidden recommendations stay.")
        }
        .confirmationDialog("Clear search history?", isPresented: $clearSearchPresented, titleVisibility: .visible) {
            Button("Clear search history", role: .destructive) { clearSearchHistory() }
        } message: {
            Text("This removes all of your saved search suggestions.")
        }
    }

    private func matches(_ terms: String) -> Bool {
        trimmedQuery.split(whereSeparator: \.isWhitespace).allSatisfy { word in
            terms.localizedCaseInsensitiveContains(String(word))
        }
    }

    private func load() async {
        guard !isLoading else { return }
        isLoading = true
        actionError = nil
        defer { isLoading = false }
        await loadProfile()
        await loadNotifications()
        await loadInterests()
        await loadSuppressions()
        await loadSearchHistory()
    }

    private func loadProfile() async {
        do {
            let value = try await settings.action("profile")
            profileName = value["display_name"]?.stringValue ?? value["username"]?.stringValue ?? store.session?.username ?? ""
        } catch { actionError = message(for: error) }
    }

    private func loadNotifications() async {
        do {
            let value = try await settings.action("notifications")
            notificationsEnabled = value["enabled"]?.boolValue ?? false
            notificationEmail = value["email"]?.stringValue ?? ""
        } catch { actionError = message(for: error) }
    }

    private func loadInterests() async {
        do {
            let value = try await settings.action("interests")
            interests = (value["categories"]?.arrayValue ?? []).compactMap(NativeInterestCategory.init)
            selectedInterestKeys = Set(value["selected_keys"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        } catch { actionError = message(for: error) }
    }

    private func loadSuppressions() async {
        do {
            let value = try await settings.action("suppressions")
            suppressions = ["items", "channels", "fewer", "titles"]
                .flatMap { value[$0]?.arrayValue ?? [] }
                .compactMap(NativeSuppression.init)
        } catch { actionError = message(for: error) }
    }

    private func loadSearchHistory() async {
        do {
            let value = try await settings.action("search-history")
            searchHistory = (value.arrayValue ?? []).compactMap(NativeSearchHistoryEntry.init)
        } catch { actionError = message(for: error) }
    }

    private func saveProfile() {
        let name = profileName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !isSavingProfile else { return }
        isSavingProfile = true
        actionError = nil
        Task {
            defer { isSavingProfile = false }
            do { _ = try await settings.action("profile", method: "PUT", body: ["display_name": .string(name)]) }
            catch { actionError = message(for: error) }
        }
    }

    private func saveNotifications() {
        guard !isSavingNotifications else { return }
        let email = notificationEmail.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !notificationsEnabled || !email.isEmpty else { return }
        isSavingNotifications = true
        actionError = nil
        Task {
            defer { isSavingNotifications = false }
            do {
                _ = try await settings.action("notifications", method: "PUT", body: [
                    "email": email.isEmpty ? .null : .string(email),
                    "enabled": .bool(notificationsEnabled),
                ])
            } catch { actionError = message(for: error) }
        }
    }

    private func interestBinding(for key: String) -> Binding<Bool> {
        Binding(
            get: { selectedInterestKeys.contains(key) },
            set: { selected in
                if selected {
                    selectedInterestKeys.insert(key)
                } else {
                    selectedInterestKeys.remove(key)
                }
            }
        )
    }

    private func saveInterests() {
        guard !isSavingInterests else { return }
        isSavingInterests = true
        actionError = nil
        let keys = selectedInterestKeys.sorted()
        Task {
            defer { isSavingInterests = false }
            do {
                let value = try await settings.action("interests", method: "PUT", body: ["keys": .array(keys.map { .string($0) })])
                selectedInterestKeys = Set(value["selected_keys"]?.arrayValue?.compactMap(\.stringValue) ?? keys)
            } catch { actionError = message(for: error) }
        }
    }

    private func restore(_ suppression: NativeSuppression) {
        actionError = nil
        Task {
            do {
                _ = try await settings.action("suppressions/\(suppression.id)", method: "DELETE")
                suppressions.removeAll { $0.id == suppression.id }
            } catch { actionError = message(for: error) }
        }
    }

    private func exportData() {
        guard !isExporting else { return }
        isExporting = true
        actionError = nil
        Task {
            defer { isExporting = false }
            do {
                let value = try await settings.action("export")
                exportFile = try NativeSettingsExport(value: value)
                exportPresented = true
            } catch { actionError = message(for: error) }
        }
    }

    private func clearRecommendationHistory() {
        guard !clearingRecommendations else { return }
        clearingRecommendations = true
        actionError = nil
        Task {
            defer { clearingRecommendations = false }
            do {
                _ = try await settings.action("discovery/recommendation-history", method: "DELETE")
                recommendationsCleared = true
            }
            catch { actionError = message(for: error) }
        }
    }

    private func removeSearch(_ entry: NativeSearchHistoryEntry) {
        actionError = nil
        Task {
            do {
                _ = try await settings.action("search-history/\(entry.id)", method: "DELETE")
                searchHistory.removeAll { $0.id == entry.id }
            } catch { actionError = message(for: error) }
        }
    }

    private func clearSearchHistory() {
        actionError = nil
        Task {
            do {
                _ = try await settings.action("search-history", method: "DELETE")
                searchHistory = []
            } catch { actionError = message(for: error) }
        }
    }

    private func message(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? "Lumina could not complete that account action."
    }
}

private struct NativeSearchHistoryEntry: Identifiable {
    let id: String
    let query: String

    init?(_ value: SettingsValue) {
        guard let id = value["id"]?.stringValue, let query = value["query"]?.stringValue else { return nil }
        self.id = id
        self.query = query
    }
}

private struct NativeInterestCategory: Identifiable {
    let key: String
    let label: String
    var id: String { key }

    init?(_ value: SettingsValue) {
        guard let key = value["key"]?.stringValue, let label = value["label"]?.stringValue else { return nil }
        self.key = key
        self.label = label
    }
}

private struct NativeSuppression: Identifiable {
    let id: String
    let name: String
    let scope: String

    init?(_ value: SettingsValue) {
        guard let id = value["id"]?.stringValue, let scope = value["scope"]?.stringValue else { return nil }
        self.id = id
        self.scope = scope
        name = value["title"]?.stringValue ?? value["channel_name"]?.stringValue ?? value["target_key"]?.stringValue ?? "Hidden recommendation"
    }

    var scopeLabel: String {
        switch scope {
        case "channel": "Channel"
        case "fewer": "Show fewer"
        case "title": "Title"
        default: "Recommendation"
        }
    }
}

private struct NativeSettingsExport: FileDocument {
    static let readableContentTypes: [UTType] = [.json]
    let data: Data

    init(value: SettingsValue) throws {
        data = try JSONEncoder().encode(value)
    }

    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}
