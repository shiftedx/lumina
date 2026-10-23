import SwiftUI
import LuminaCore

/// Personal settings stay on one searchable surface; every result is the control itself.
@MainActor
struct NativeAccountSettingsView: View {
    let store: LuminaStore
    @Environment(LuminaUserSettings.self) private var settings
    @AppStorage("lumina.appearance") private var appearance = "dark"
    @State private var query = ""
    @State private var licensesPresented = false
    @State private var editingHome = false

    var body: some View {
        Form {
            if query.isEmpty {
                Section {
                    Label(store.session?.username ?? "Your account", systemImage: "person.crop.circle")
                        .font(.title3.weight(.semibold))
                    Text("Your preferences, across Lumina.").foregroundStyle(.secondary)
                    Button("Sign out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
                        Task { store.clearError(); await store.signOut() }
                    }.accessibilityIdentifier("signOutButton")
                }
            }
            if matchesHomePreferences {
                Section("Home") {
                    Button("Edit Home", systemImage: "slider.horizontal.3") { editingHome = true }
                        .accessibilityIdentifier("homeEditButton")
                }
            }
            if settings.isLoading { ProgressView("Loading your settings…") }
            if let error = settings.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                    Button("Try again") { Task { await settings.load() } }
                }
            }
            ForEach(SettingGroup.allCases) { group in
                let fields = NativeSetting.fields.filter {
                    $0.group == group && $0.matches(query)
                }
                if !fields.isEmpty {
                    Section {
                        ForEach(fields) { field in
                            NativeSettingRow(field: field, value: value(field), saving: settings.isSaving) { next in
                                save(field, value: next)
                            }
                        }
                    } header: { Text(group.rawValue) } footer: {
                        if group == .captions {
                            Text("Language defaults apply across Lumina. Caption size and background style apply in the browser; full-screen iPhone and iPad captions follow the system caption style.")
                        }
                    }
                    .disabled(!settings.hasLoaded || settings.isSaving)
                }
            }
            NativeAccountActionsView(settings: settings, store: store, query: query)
            NativeAccountSecurityView(settings: settings, store: store, query: query)
            NativeAccountConnectionsView(settings: settings, query: query)
            NativeAccountAllowancesView(settings: settings, query: query)
            if query.isEmpty || "about credits licenses sign out logout".localizedCaseInsensitiveContains(query) {
                Section("About") {
                    Button("Open-source licenses", systemImage: "doc.text") { licensesPresented = true }
                        .accessibilityIdentifier("accountLicensesButton")
                    Text("Movie and show artwork and metadata are provided by TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Link("All credits", destination: URL(string: "https://github.com/shiftedx/lumina/blob/main/CREDITS.md")!)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(LuminaTheme.paper)
        .navigationTitle("Account")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarVisibility(.visible, for: .navigationBar)
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search your settings")
        .accessibilityIdentifier("accountScreen")
        .refreshable { await settings.load() }
        .sheet(isPresented: $licensesPresented) {
            PlaybackLicensesView().accessibilityIdentifier("accountLicensesSheet")
        }
        .sheet(isPresented: $editingHome) { NativeHomePreferencesView(store: store) }
        .onChange(of: settings.document, initial: true) { _, _ in
            if settings.hasLoaded { appearance = settings.theme }
        }
    }

    private var matchesHomePreferences: Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy {
            "home layout rows shelves edit".localizedCaseInsensitiveContains(String($0))
        }
    }

    private func value(_ field: NativeSetting) -> SettingsValue {
        var current = SettingsValue.object(settings.document)
        for key in field.path { current = current[key] ?? .null }
        return current == .null ? field.fallback : current
    }

    private func save(_ field: NativeSetting, value: SettingsValue) {
        // Patch one UI preference key while preserving sibling nested settings.
        let path = field.path
        guard let root = path.first else { return }
        var patch: SettingsValue = value
        if root == "ui_prefs", path.count > 1 {
            let key = path[1]
            var branch = settings.document[root]?[key] ?? .object([:])
            branch = replacing(branch, path: Array(path.dropFirst(2)), value: value)
            patch = .object([key: branch])
        } else {
            patch = replacing(settings.document[root] ?? .object([:]), path: Array(path.dropFirst()), value: value)
        }
        Task { _ = await settings.update([root: patch]) }
    }

    private func replacing(_ object: SettingsValue, path: [String], value: SettingsValue) -> SettingsValue {
        guard let key = path.first else { return value }
        var values = object.objectValue ?? [:]
        values[key] = replacing(values[key] ?? .object([:]), path: Array(path.dropFirst()), value: value)
        return .object(values)
    }
}

private enum SettingGroup: String, CaseIterable, Identifiable {
    case display = "Display"
    case watching = "Watching"
    case captions = "Subtitles & languages"
    case skipping = "Automatic skipping"
    var id: String { rawValue }
}

private struct NativeSetting: Identifiable {
    enum Control { case toggle, choice([(String, SettingsValue)]), text, lines, languages }
    let id: String
    let title: String
    let group: SettingGroup
    let path: [String]
    let fallback: SettingsValue
    let control: Control
    var note: String = ""
    var keywords: String = ""

    func matches(_ query: String) -> Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy {
            "\(title) \(group.rawValue) \(note) \(keywords)".localizedCaseInsensitiveContains(String($0))
        }
    }

    static func pref(_ id: String, _ title: String, _ group: SettingGroup, _ suffix: [String], _ fallback: SettingsValue,
                     _ control: Control, note: String = "", keywords: String = "") -> Self {
        Self(id: id, title: title, group: group, path: ["ui_prefs"] + suffix, fallback: fallback,
             control: control, note: note, keywords: keywords)
    }
    static func strings(_ options: [(String, String)]) -> Control { .choice(options.map { ($0.0, .string($0.1)) }) }

    static let fields: [Self] = [
        pref("theme", "Appearance", .display, ["theme"], .string("system"), strings([("System", "system"), ("Light", "light"), ("Dark", "dark"), ("OLED", "oled")]), keywords: "theme dark mode"),
        pref("quality", "Video quality", .watching, ["playback_max_height"], .null, .choice([("Best available", .null), ("Up to 1440p", .number(1440)), ("Up to 1080p", .number(1080)), ("Up to 720p", .number(720)), ("Data saver · 480p", .number(480))]), note: "A manual choice in the player takes priority. Downloads are unaffected."),
        pref("loudness", "Even out loudness", .watching, ["normalize_loudness"], .bool(true), .toggle, note: "Keeps volume steady across videos; files are unchanged."),
        pref("caption-size", "Caption size", .captions, ["captions", "size"], .string("medium"), strings([("Small", "small"), ("Medium", "medium"), ("Large", "large"), ("Extra large", "xlarge")])),
        pref("caption-background", "Caption background", .captions, ["captions", "background"], .string("shadow"), strings([("None", "none"), ("Shadow", "shadow"), ("Box", "box"), ("Solid", "solid")]), note: "Full-screen iPhone and iPad captions use the system caption style."),
        pref("audio-languages", "Audio languages", .captions, ["captions", "tracks", "audioLanguages"], .array([]), .languages, note: "Language codes in preference order, such as en, ja, es. Leave empty for the file’s default."),
        pref("subtitle-languages", "Subtitle languages", .captions, ["captions", "tracks", "subtitleLanguages"], .array([]), .languages),
        pref("subtitles", "Enable subtitles by default", .captions, ["captions", "tracks", "subtitlesEnabled"], .bool(false), .toggle),
        pref("forced", "Use only forced subtitles", .captions, ["captions", "tracks", "forcedOnly"], .bool(false), .toggle),
        pref("sdh", "Prefer SDH subtitles", .captions, ["captions", "tracks", "preferSdh"], .bool(false), .toggle, note: "Subtitles for deaf and hard of hearing."),
        pref("skip-intro", "Skip intros automatically", .skipping, ["auto_skip", "intro"], .bool(false), .toggle),
        pref("skip-recap", "Skip recaps automatically", .skipping, ["auto_skip", "recap"], .bool(false), .toggle),
        pref("skip-credits", "Skip credits automatically", .skipping, ["auto_skip", "credits"], .bool(false), .toggle),
    ]
}

@MainActor
private struct NativeSettingRow: View {
    let field: NativeSetting
    let value: SettingsValue
    let saving: Bool
    let onChange: (SettingsValue) -> Void
    @State private var draft = ""
    @State private var validation: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch field.control {
            case .toggle:
                Toggle(field.title, isOn: Binding(get: { value.boolValue ?? false }, set: { onChange(.bool($0)) }))
            case .choice(let choices):
                Picker(field.title, selection: Binding(get: { choices.firstIndex { $0.1 == value } ?? 0 }, set: { onChange(choices[$0].1) })) {
                    ForEach(choices.indices, id: \.self) { index in Text(choices[index].0).tag(index) }
                }
                .pickerStyle(.menu)
                .accessibilityValue(choices.first { $0.1 == value }?.0 ?? "")
                .accessibilityIdentifier(field.id == "theme" ? "appearancePicker" : "setting-\(field.id)")
            case .text, .lines, .languages:
                Text(field.title).font(.body)
                TextField(field.title, text: $draft, axis: field.isLines ? .vertical : .horizontal)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .lineLimit(field.isLines ? 3...6 : 1...1)
                    .submitLabel(.done)
                    .onSubmit { commit() }
                if draft != textValue { Button("Save \(field.title)") { commit() }.font(.callout) }
            }
            if !field.note.isEmpty { Text(field.note).font(.footnote).foregroundStyle(.secondary) }
            if let validation { Text(validation).font(.footnote).foregroundStyle(.red) }
        }
        .accessibilityIdentifier(field.id == "theme" ? "appearancePicker" : "setting-\(field.id)")
        .onChange(of: value, initial: true) { _, _ in draft = textValue }
    }

    private var textValue: String {
        value.stringValue ?? value.arrayValue?.compactMap(\.stringValue).joined(separator: field.isLines ? "\n" : ", ") ?? ""
    }
    private func commit() {
        validation = nil
        switch field.control {
        case .languages:
            let languages = draft.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased().replacingOccurrences(of: "_", with: "-") }
            guard languages.count <= 10, languages.allSatisfy({ $0.range(of: "^[a-z]{2,3}(-[a-z0-9]{2,8})*$", options: .regularExpression) != nil }) else {
                validation = "Use up to ten language codes, such as en, ja, es."; return
            }
            var seen = Set<String>()
            onChange(.array(languages.filter { seen.insert($0).inserted }.map(SettingsValue.string)))
        case .lines:
            let words = draft.split(separator: "\n").map { String($0).trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            onChange(.array(words.map(SettingsValue.string)))
        default: onChange(.string(draft.trimmingCharacters(in: .whitespacesAndNewlines)))
        }
    }
}

private extension NativeSetting {
    var isLines: Bool { if case .lines = control { true } else { false } }
}
