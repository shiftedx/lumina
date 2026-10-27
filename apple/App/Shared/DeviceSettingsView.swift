import SwiftUI
import LuminaCore

/// Preferences that apply only to this app installation. Server account and household
/// administration remain on the Account screen.
@MainActor
struct DeviceSettingsView: View {
    let store: LuminaStore
    var onSignOut: (() -> Void)?
    var onClose: (() -> Void)?
    private let embedded: Bool
    private let query: String
    @AppStorage("native.appearance") private var appearance = "account"
    @AppStorage("native.video.subtitleAppearance") private var subtitleStyleData = ""
    @Bindable private var preferences: NativePlaybackPreferences
    @State private var licensesPresented = false
    @FocusState private var qualityFocused: Bool

    init(store: LuminaStore, onSignOut: (() -> Void)? = nil, onClose: (() -> Void)? = nil, embedded: Bool = false, query: String = "") {
        self.store = store
        self.embedded = embedded
        self.query = query
        self.onSignOut = onSignOut
        self.onClose = onClose
        _preferences = Bindable(store.playbackPreferences)
    }

    var body: some View {
        #if os(tvOS)
        TVDeviceSettingsView(
            store: store,
            preferences: preferences,
            subtitleStyle: subtitleStyle,
            onSignOut: onSignOut,
            onClose: onClose
        )
        #else
        mobileSettings
        #endif
    }

    #if !os(tvOS)
    private var mobileSettings: some View {
        Group {
            if embedded { mobilePreferences }
            else {
                ScrollView {
                    mobilePreferences.padding(24).frame(maxWidth: 920).frame(maxWidth: .infinity)
                }
                .background(LuminaTheme.paper)
                .navigationTitle("Device settings")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("deviceSettingsScreen")
        .sheet(isPresented: $licensesPresented) {
            PlaybackLicensesView().accessibilityIdentifier("deviceLicensesSheet")
        }
    }

    private var mobilePreferences: some View {
        VStack(alignment: .leading, spacing: 20) {
            if matches("appearance theme dark light oled system") || matches("playback video quality resolution data saver") || matches("audio language english japanese spanish") {
                LuminaSettingsSection("This device") {
                    if matches("appearance theme dark light oled system") {
                        LuminaSettingsPicker("Appearance", selection: $appearance,
                            options: ["account", "system", "light", "dark", "oled"],
                            identifier: "deviceAppearancePicker") {
                                $0 == "account" ? "Default" : $0 == "oled" ? "OLED" : $0.capitalized
                            }
                    }
                    if matches("playback video quality resolution data saver") {
                        LuminaSettingsPicker("Video quality", selection: $preferences.quality,
                            options: PlaybackQuality.allCases, identifier: "devicePlaybackQualityPicker") {
                                $0 == .automatic ? "Original" : $0.label
                            }
                    }
                    if matches("audio language english japanese spanish") {
                        DeviceLanguagePicker(title: "Audio language", languages: $preferences.preferredAudioLanguages,
                                             identifier: "devicePreferredAudioLanguagePicker")
                    }
                }
            }
            if matches("subtitles captions language appearance") {
                LuminaSettingsSection("Subtitles") {
                    DeviceLanguagePicker(title: "Language", languages: $preferences.preferredSubtitleLanguages,
                                         identifier: "devicePreferredSubtitleLanguagePicker")
                    LuminaSettingsPicker("Show subtitles", selection: $preferences.subtitleMode,
                        options: NativeSubtitleMode.allCases, identifier: "deviceSubtitleModePicker") { $0.label }
                    NavigationLink {
                        DeviceSubtitleAppearanceView(store: store, style: subtitleStyle)
                    } label: {
                        LuminaSettingsActionLabel("Caption appearance", glyph: .subtitles)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("deviceSubtitleAppearanceButton")
                }
            }
            if matches("gestures swipe seek skip tap hold haptics playback video") {
                LuminaSettingsSection("Playback gestures") {
                    VideoGestureSettingsView(preferences: preferences)
                }
            }
            if matches("downloads storage offline music movies episodes video") {
                LuminaSettingsSection("Storage") {
                    NavigationLink {
                        NativeOfflineLibraryView(store: store)
                    } label: {
                        LuminaSettingsActionLabel("Downloads", glyph: .download)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("deviceDownloadsButton")
                }
            }
            if !embedded {
                LuminaSettingsSection("About") {
                    Button { licensesPresented = true } label: {
                        LuminaSettingsActionLabel("Open-source licenses", symbol: "doc.text")
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("deviceLicensesButton")
                }
                if let onSignOut {
                    Button("Sign out", glyph: .logout, role: .destructive, action: onSignOut)
                        .buttonStyle(.plain).frame(minHeight: 44)
                        .accessibilityIdentifier("deviceSignOutButton")
                }
            }
        }
    }

    private func matches(_ keywords: String) -> Bool {
        query.split(whereSeparator: \.isWhitespace).allSatisfy {
            keywords.localizedCaseInsensitiveContains(String($0))
        }
    }
    #endif

    /// Playback can change this same preference while the settings view is open.
    /// Keep AppStorage as the source of truth so visiting Device Settings never
    /// restores an older in-memory value over a recent player choice.
    private var subtitleStyle: Binding<VideoSubtitleStyle> {
        Binding(
            get: { VideoSubtitleStyle(encodedString: subtitleStyleData) },
            set: { next in
                subtitleStyleData = next.encodedString
                preferences.subtitleStyle = next
            }
        )
    }
}

#if os(iOS)
struct LuminaSettingsSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(LuminaTheme.editorial(22, relativeTo: .title3))
                .accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}

struct LuminaSettingsActionLabel: View {
    let title: String
    let glyph: LuminaGlyph?
    let symbol: String?
    let trailingGlyph: LuminaGlyph?

    init(_ title: String, glyph: LuminaGlyph, trailingGlyph: LuminaGlyph? = .forward) {
        self.title = title; self.glyph = glyph; symbol = nil; self.trailingGlyph = trailingGlyph
    }

    init(_ title: String, symbol: String, trailingGlyph: LuminaGlyph? = .forward) {
        self.title = title; glyph = nil; self.symbol = symbol; self.trailingGlyph = trailingGlyph
    }

    var body: some View {
        HStack(spacing: 12) {
            if let glyph {
                LuminaIcon(glyph: glyph, size: 20).frame(width: 22)
            } else if let symbol {
                Image(systemName: symbol).frame(width: 22).accessibilityHidden(true)
            }
            Text(title)
            Spacer(minLength: 12)
            if let trailingGlyph {
                LuminaIcon(glyph: trailingGlyph, size: 16).foregroundStyle(.secondary)
            }
        }
        .frame(minHeight: 44)
        .contentShape(.rect)
        .foregroundStyle(LuminaTheme.ink)
    }
}

struct LuminaSettingsPicker<Value: Hashable>: View {
    let title: String
    @Binding var selection: Value
    let options: [Value]
    let identifier: String
    let label: (Value) -> String
    @State private var expanded = false

    init(_ title: String, selection: Binding<Value>, options: [Value], identifier: String,
         label: @escaping (Value) -> String) {
        self.title = title; _selection = selection; self.options = options
        self.identifier = identifier; self.label = label
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { expanded.toggle() } label: {
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text(title).frame(maxWidth: .infinity, alignment: .leading)
                    Text(label(selection)).fontWeight(.medium)
                        .padding(.bottom, 5)
                        .overlay(alignment: .bottom) { Rectangle().fill(LuminaTheme.brand).frame(height: 2) }
                        .multilineTextAlignment(.trailing)
                    LuminaIcon(glyph: expanded ? .chevronUp : .chevronDown, size: 16)
                        .foregroundStyle(.secondary)
                }
                .frame(minHeight: 44).contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title)
            .accessibilityValue(label(selection))
            .accessibilityIdentifier(identifier)
            if expanded {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(options, id: \.self) { option in
                        Button { selection = option; expanded = false } label: {
                            Text(label(option)).fixedSize(horizontal: false, vertical: true)
                        }
                        .buttonStyle(LuminaUnderlineTabStyle(selected: selection == option))
                        .accessibilityAddTraits(selection == option ? .isSelected : [])
                        .accessibilityIdentifier("\(identifier)-option-\(options.firstIndex(of: option) ?? 0)")
                    }
                }
                .padding(.bottom, 8)
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("\(identifier)-choices")
            }
        }
        .foregroundStyle(LuminaTheme.ink)
    }
}

struct LuminaSettingsSwitch: View {
    let title: String
    @Binding var isOn: Bool
    let identifier: String

    init(_ title: String, isOn: Binding<Bool>, identifier: String) {
        self.title = title; _isOn = isOn; self.identifier = identifier
    }

    var body: some View {
        HStack(spacing: 14) {
            Text(title).frame(maxWidth: .infinity, alignment: .leading)
            ForEach([false, true], id: \.self) { option in
                Button(option ? "On" : "Off") { isOn = option }
                    .buttonStyle(LuminaUnderlineTabStyle(selected: isOn == option))
                    .accessibilityAddTraits(isOn == option ? .isSelected : [])
                    .accessibilityIdentifier("\(identifier)-\(option ? "on" : "off")")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityIdentifier(identifier)
    }
}
#endif

private enum DeviceLanguages {
    static let options: [(tag: String, title: String)] = [
        ("", "Automatic"),
        ("en", "English"),
        ("es", "Spanish"),
        ("fr", "French"),
        ("de", "German"),
        ("it", "Italian"),
        ("ja", "Japanese"),
        ("ko", "Korean"),
        ("pt", "Portuguese"),
        ("zh", "Chinese"),
        ("bg", "Bulgarian"),
    ]

    static func title(for tag: String) -> String {
        options.first(where: { $0.tag == tag })?.title ?? "Automatic"
    }
}

@MainActor
private struct DeviceLanguagePicker: View {
    let title: String
    @Binding var languages: [String]
    let identifier: String

    var body: some View {
        #if os(iOS)
        LuminaSettingsPicker(title, selection: primaryLanguage,
            options: DeviceLanguages.options.map(\.tag), identifier: identifier) { DeviceLanguages.title(for: $0) }
        #else
        Picker(title, selection: primaryLanguage) {
            ForEach(DeviceLanguages.options, id: \.tag) { option in Text(option.title).tag(option.tag) }
        }
        .accessibilityIdentifier(identifier)
        #endif
    }

    private var primaryLanguage: Binding<String> {
        Binding(
            get: { languages.first ?? "" },
            set: { languages = $0.isEmpty ? [] : [$0] }
        )
    }
}

@MainActor
private struct DeviceSubtitleAppearanceView: View {
    let store: LuminaStore
    @Binding var style: VideoSubtitleStyle
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        #if os(tvOS)
        // The settings root supplies the active NavigationStack route on TV.
        // This fallback is retained only for callers outside that route.
        TVDeviceSubtitleAppearanceView(style: $style, openRoute: { _ in })
        #else
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                LuminaSettingsSwitch("Customize subtitles", isOn: $style.isCustom,
                                     identifier: "deviceCustomizeSubtitlesToggle")
                if style.isCustom {
                    LuminaSettingsSection("Text") {
                        subtitlePicker("Font", value: $style.font, options: VideoSubtitleStyle.FontFamily.allCases)
                        subtitlePicker("Size", value: $style.size, options: VideoSubtitleStyle.Size.allCases)
                        subtitlePicker("Color", value: $style.color, options: VideoSubtitleStyle.Color.allCases)
                        subtitlePicker("Weight", value: $style.weight, options: VideoSubtitleStyle.Weight.allCases)
                    }
                    LuminaSettingsSection("Background & position") {
                        subtitlePicker("Outline", value: $style.edgeStyle, options: VideoSubtitleStyle.EdgeStyle.allCases)
                        subtitlePicker("Background", value: $style.background, options: VideoSubtitleStyle.Background.allCases)
                        subtitlePicker("Opacity", value: $style.opacity, options: VideoSubtitleStyle.Opacity.allCases)
                        subtitlePicker("Vertical alignment", value: $style.verticalAlignment, options: VideoSubtitleStyle.VerticalAlignment.allCases)
                    }
                    Button("Reset appearance") { style.restoreDefaults() }
                        .buttonStyle(.plain).frame(minHeight: 44)
                        .accessibilityIdentifier("deviceResetSubtitleAppearanceButton")
                }
            }
            .padding(24).frame(maxWidth: 920).frame(maxWidth: .infinity, alignment: .top)
        }
        .background(LuminaTheme.paper)
        .navigationTitle("Caption appearance")
        .accessibilityIdentifier("deviceSubtitleAppearanceScreen")
        .standardPhonePushedChrome(store: store)
        #endif
    }

    private func subtitlePicker<Value>(
        _ title: String,
        value: Binding<Value>,
        options: [Value]
    ) -> some View where Value: CaseIterable & Identifiable & Hashable, Value.ID == Value, Value: Sendable {
        #if os(iOS)
        LuminaSettingsPicker(title, selection: value, options: options,
            identifier: "deviceSubtitleStyle\(title.replacingOccurrences(of: " ", with: ""))Picker") { subtitleLabel($0) }
        #else
        Picker(title, selection: value) {
            ForEach(options, id: \.self) { option in Text(subtitleLabel(option)).tag(option) }
        }
        #endif
    }

    fileprivate func subtitleLabel(_ value: some Any) -> String {
        switch value {
        case let value as VideoSubtitleStyle.FontFamily: value.label
        case let value as VideoSubtitleStyle.Size: value.label
        case let value as VideoSubtitleStyle.Color: value.label
        case let value as VideoSubtitleStyle.Weight: value.label
        case let value as VideoSubtitleStyle.EdgeStyle: value.label
        case let value as VideoSubtitleStyle.Background: value.label
        case let value as VideoSubtitleStyle.Opacity: value.label
        case let value as VideoSubtitleStyle.VerticalAlignment: value.label
        default: ""
        }
    }
}

#if os(tvOS)
private enum TVDeviceSettingsCategory: String, CaseIterable, Identifiable {
    case playback
    case audio
    case subtitles
    case storage
    case about
    case account

    var id: Self { self }

    var title: String {
        switch self {
        case .playback: "Playback"
        case .audio: "Audio"
        case .subtitles: "Subtitles"
        case .storage: "Storage"
        case .about: "About"
        case .account: "Account"
        }
    }
}

private enum TVDeviceSettingsRoute: Hashable {
    case quality
    case audioLanguage
    case subtitleLanguage
    case subtitleMode
    case captionAppearance
    case subtitleFont
    case subtitleSize
    case subtitleColor
    case subtitleWeight
    case subtitleOutline
    case subtitleBackground
    case subtitleOpacity
    case subtitleVerticalAlignment
    case downloads
    case licenses
}

@MainActor
private struct TVDeviceSettingsView: View {
    let store: LuminaStore
    var onSignOut: (() -> Void)?
    var onClose: (() -> Void)?
    @Bindable private var preferences: NativePlaybackPreferences
    @Binding private var subtitleStyle: VideoSubtitleStyle
    @State private var category: TVDeviceSettingsCategory = .playback
    @State private var path = [TVDeviceSettingsRoute]()
    @FocusState private var focusedCategory: TVDeviceSettingsCategory?

    init(
        store: LuminaStore,
        preferences: NativePlaybackPreferences,
        subtitleStyle: Binding<VideoSubtitleStyle>,
        onSignOut: (() -> Void)?,
        onClose: (() -> Void)?
    ) {
        self.store = store
        self.onSignOut = onSignOut
        self.onClose = onClose
        _preferences = Bindable(preferences)
        _subtitleStyle = subtitleStyle
    }

    var body: some View {
        NavigationStack(path: $path) {
            VStack(alignment: .leading, spacing: 42) {
                Text("Settings")
                    .font(LuminaTheme.editorial(42, relativeTo: .title))
                    .foregroundStyle(LuminaTheme.ink)
                    .accessibilityIdentifier("deviceSettingsScreen")

                HStack(alignment: .top, spacing: 76) {
                    categoryNavigation
                        .frame(width: 250, alignment: .leading)
                        .focusSection()

                    Divider()
                        .overlay(Color.white.opacity(0.16))
                        .frame(height: 660)

                    settingsContent
                        .frame(maxWidth: 860, alignment: .leading)
                        .focusSection()
                }
            }
            .padding(.horizontal, 120)
            .padding(.top, 84)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(LuminaTheme.theater.ignoresSafeArea())
            .foregroundStyle(LuminaTheme.ink)
            .preferredColorScheme(.dark)
            .defaultFocus($focusedCategory, .playback)
            .navigationDestination(for: TVDeviceSettingsRoute.self) { route in
                destination(for: route)
            }
        }
        .onExitCommand {
            if path.isEmpty { onClose?() }
            else { path.removeLast() }
        }
    }

    private var categoryNavigation: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(TVDeviceSettingsCategory.allCases) { candidate in
                Button { category = candidate } label: {
                    Text(candidate.title)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(LuminaUnderlineTabStyle(selected: category == candidate))
                .focused($focusedCategory, equals: candidate)
                .accessibilityIdentifier("deviceSettingsCategory-\(candidate.rawValue)")
                .accessibilityValue(category == candidate ? "Selected" : "")
                .accessibilityAddTraits(category == candidate ? .isSelected : [])
            }
        }
    }

    @ViewBuilder
    private var settingsContent: some View {
        switch category {
        case .playback:
            TVDeviceSettingsSection(
                title: "Playback",
                detail: "Original requests the highest compatible stream. The server can still select a lower compatible format."
            ) {
                Button { path.append(.quality) } label: {
                    TVDeviceSettingsRow(title: "Video quality", value: qualityTitle)
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("devicePlaybackQualityPicker")
            }
        case .audio:
            TVDeviceSettingsSection(
                title: "Audio",
                detail: "Choose a preferred language for the next playback session, or leave the server default in control."
            ) {
                Button { path.append(.audioLanguage) } label: {
                    TVDeviceSettingsRow(title: "Audio language", value: DeviceLanguages.title(for: preferences.preferredAudioLanguages.first ?? ""))
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("devicePreferredAudioLanguagePicker")
            }
        case .subtitles:
            TVDeviceSettingsSection(
                title: "Subtitles",
                detail: "Automatic follows stream and system defaults. Always and Forced only use your preferred subtitle language."
            ) {
                Button { path.append(.subtitleLanguage) } label: {
                    TVDeviceSettingsRow(title: "Subtitle language", value: DeviceLanguages.title(for: preferences.preferredSubtitleLanguages.first ?? ""))
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("devicePreferredSubtitleLanguagePicker")

                Button { path.append(.subtitleMode) } label: {
                    TVDeviceSettingsRow(title: "Subtitles", value: preferences.subtitleMode.label)
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("deviceSubtitleModePicker")

                Button { path.append(.captionAppearance) } label: {
                    TVDeviceSettingsRow(title: "Caption appearance", value: subtitleStyle.isCustom ? "Custom" : "System")
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("deviceSubtitleAppearanceButton")
            }
        case .storage:
            TVDeviceSettingsSection(
                title: "Storage",
                detail: "Downloads saved on this Apple TV are available when the server is unavailable."
            ) {
                Button { path.append(.downloads) } label: {
                    TVDeviceSettingsRow(title: "Downloads", value: "")
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("deviceDownloadsButton")
            }
        case .about:
            TVDeviceSettingsSection(
                title: "About",
                detail: "Lumina uses open-source playback components. Their notices and source references are available here."
            ) {
                Button { path.append(.licenses) } label: {
                    TVDeviceSettingsRow(title: "Open-source licenses", value: "")
                }
                .buttonStyle(TVDeviceSettingsRowStyle())
                .accessibilityIdentifier("deviceLicensesButton")
            }
        case .account:
            TVDeviceSettingsSection(
                title: "Account",
                detail: "This device keeps playback preferences locally. Signing out removes this device session."
            ) {
                if let onSignOut {
                    Button(action: onSignOut) {
                        TVDeviceSettingsRow(title: "Sign out", value: "", showsDisclosure: false)
                    }
                    .buttonStyle(TVDeviceSettingsRowStyle(destructive: true))
                    .accessibilityIdentifier("deviceSignOutButton")
                }
            }
        }
    }

    @ViewBuilder
    private func destination(for route: TVDeviceSettingsRoute) -> some View {
        switch route {
        case .quality:
            TVDeviceChoiceScreen(
                title: "Video quality",
                choices: PlaybackQuality.allCases,
                selection: $preferences.quality,
                label: { $0 == .automatic ? "Original (best available)" : $0.label },
                identifier: { "deviceQualityOption-\($0.rawValue)" },
                onSelection: popRoute
            )
        case .audioLanguage:
            TVDeviceChoiceScreen(
                title: "Audio language",
                choices: DeviceLanguages.options.map(\.tag),
                selection: primaryAudioLanguage,
                label: DeviceLanguages.title(for:),
                identifier: { "deviceAudioLanguageOption-\($0.isEmpty ? "automatic" : $0)" },
                onSelection: popRoute
            )
        case .subtitleLanguage:
            TVDeviceChoiceScreen(
                title: "Subtitle language",
                choices: DeviceLanguages.options.map(\.tag),
                selection: primarySubtitleLanguage,
                label: DeviceLanguages.title(for:),
                identifier: { "deviceSubtitleLanguageOption-\($0.isEmpty ? "automatic" : $0)" },
                onSelection: popRoute
            )
        case .subtitleMode:
            TVDeviceChoiceScreen(
                title: "Subtitles",
                choices: NativeSubtitleMode.allCases,
                selection: $preferences.subtitleMode,
                label: \.label,
                identifier: { "deviceSubtitleModeOption-\($0.rawValue)" },
                onSelection: popRoute
            )
        case .captionAppearance:
            TVDeviceSubtitleAppearanceView(style: $subtitleStyle, openRoute: { path.append($0) })
        case .subtitleFont:
            subtitleChoiceDestination("Font", selection: $subtitleStyle.font, choices: VideoSubtitleStyle.FontFamily.allCases)
        case .subtitleSize:
            subtitleChoiceDestination("Size", selection: $subtitleStyle.size, choices: VideoSubtitleStyle.Size.allCases)
        case .subtitleColor:
            subtitleChoiceDestination("Color", selection: $subtitleStyle.color, choices: VideoSubtitleStyle.Color.allCases)
        case .subtitleWeight:
            subtitleChoiceDestination("Weight", selection: $subtitleStyle.weight, choices: VideoSubtitleStyle.Weight.allCases)
        case .subtitleOutline:
            subtitleChoiceDestination("Outline", selection: $subtitleStyle.edgeStyle, choices: VideoSubtitleStyle.EdgeStyle.allCases)
        case .subtitleBackground:
            subtitleChoiceDestination("Background", selection: $subtitleStyle.background, choices: VideoSubtitleStyle.Background.allCases)
        case .subtitleOpacity:
            subtitleChoiceDestination("Opacity", selection: $subtitleStyle.opacity, choices: VideoSubtitleStyle.Opacity.allCases)
        case .subtitleVerticalAlignment:
            subtitleChoiceDestination("Vertical alignment", selection: $subtitleStyle.verticalAlignment, choices: VideoSubtitleStyle.VerticalAlignment.allCases)
        case .downloads:
            NativeMusicLibraryView(store: store, section: .downloads)
        case .licenses:
            TVDeviceLicensesView()
        }
    }

    private func subtitleChoiceDestination<Value>(
        _ title: String,
        selection: Binding<Value>,
        choices: [Value]
    ) -> some View where Value: CaseIterable & Identifiable & Hashable, Value.ID == Value, Value: Sendable {
        TVDeviceChoiceScreen(
            title: title,
            choices: choices,
            selection: selection,
            label: subtitleLabel,
            identifier: { "deviceSubtitleStyle\(title.replacingOccurrences(of: " ", with: ""))Option-\($0.id)" },
            onSelection: popRoute
        )
    }

    private func subtitleLabel(_ value: some Any) -> String {
        switch value {
        case let value as VideoSubtitleStyle.FontFamily: value.label
        case let value as VideoSubtitleStyle.Size: value.label
        case let value as VideoSubtitleStyle.Color: value.label
        case let value as VideoSubtitleStyle.Weight: value.label
        case let value as VideoSubtitleStyle.EdgeStyle: value.label
        case let value as VideoSubtitleStyle.Background: value.label
        case let value as VideoSubtitleStyle.Opacity: value.label
        case let value as VideoSubtitleStyle.VerticalAlignment: value.label
        default: ""
        }
    }

    private func popRoute() {
        guard !path.isEmpty else { return }
        path.removeLast()
    }

    private var qualityTitle: String {
        preferences.quality == .automatic ? "Original (best available)" : preferences.quality.label
    }

    private var primaryAudioLanguage: Binding<String> {
        Binding(
            get: { preferences.preferredAudioLanguages.first ?? "" },
            set: { preferences.preferredAudioLanguages = $0.isEmpty ? [] : [$0] }
        )
    }

    private var primarySubtitleLanguage: Binding<String> {
        Binding(
            get: { preferences.preferredSubtitleLanguages.first ?? "" },
            set: { preferences.preferredSubtitleLanguages = $0.isEmpty ? [] : [$0] }
        )
    }
}

private struct TVDeviceSettingsSection<Content: View>: View {
    let title: String
    let detail: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(title)
                .font(LuminaTheme.editorial(34, relativeTo: .title2))
                .foregroundStyle(LuminaTheme.ink)
            Text(detail)
                .font(.system(size: 21))
                .foregroundStyle(LuminaTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 720, alignment: .leading)
                .padding(.bottom, 14)
            VStack(spacing: 12) { content }
        }
    }
}

private struct TVDeviceSettingsRow: View {
    let title: String
    let value: String
    var showsDisclosure = true

    var body: some View {
        HStack(spacing: 24) {
            Text(title)
                .font(.system(size: 27, weight: .regular))
                .lineLimit(1)
            Spacer(minLength: 32)
            if !value.isEmpty {
                Text(value)
                    .font(.system(size: 24))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)
            }
            if showsDisclosure {
                LuminaIcon(glyph: .forward, size: 18)
                    .foregroundStyle(LuminaTheme.muted)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
        .contentShape(.rect)
    }
}

private struct TVDeviceSettingsRowStyle: ButtonStyle {
    var selected = false
    var destructive = false
    @Environment(\.isFocused) private var isFocused
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(destructive ? Color.red.opacity(isFocused ? 1 : 0.82) : LuminaTheme.ink.opacity(isFocused ? 1 : 0.86))
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(isFocused ? Color.white : selected ? LuminaTheme.brand : .clear)
                    .frame(height: isFocused ? 3 : selected ? 2 : 1)
            }
            .opacity(isEnabled ? (configuration.isPressed ? 0.65 : 1) : 0.4)
            .contentShape(.rect)
    }
}

private struct TVDeviceChoiceScreen<Value: Hashable>: View {
    let title: String
    let choices: [Value]
    @Binding var selection: Value
    let label: (Value) -> String
    let identifier: (Value) -> String
    let onSelection: () -> Void
    @FocusState private var focusedChoice: Value?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(title)
                    .font(LuminaTheme.editorial(40, relativeTo: .title))
                    .foregroundStyle(LuminaTheme.ink)
                    .padding(.bottom, 22)

                ForEach(choices, id: \.self) { choice in
                    Button {
                        selection = choice
                        onSelection()
                    } label: {
                        HStack {
                            Text(label(choice))
                            Spacer()
                            if selection == choice {
                                LuminaIcon(glyph: .check, size: 22)
                                    .foregroundStyle(LuminaTheme.accentInk)
                            }
                        }
                        .font(.system(size: 28))
                        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
                    }
                    .buttonStyle(TVDeviceSettingsRowStyle(selected: selection == choice))
                    .focused($focusedChoice, equals: choice)
                    .accessibilityIdentifier(identifier(choice))
                    .accessibilityValue(selection == choice ? "Selected" : "")
                    .accessibilityAddTraits(selection == choice ? .isSelected : [])
                }
            }
            .padding(.horizontal, 120)
            .padding(.vertical, 88)
            .frame(maxWidth: 1_040, alignment: .leading)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .onAppear { focusedChoice = selection }
    }
}

private struct TVDeviceSubtitleAppearanceView: View {
    @Binding var style: VideoSubtitleStyle
    let openRoute: (TVDeviceSettingsRoute) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Caption appearance")
                    .font(LuminaTheme.editorial(40, relativeTo: .title))
                    .foregroundStyle(LuminaTheme.ink)
                Text("System leaves caption presentation to the device and authored subtitles. Custom styling applies to supported WebVTT captions.")
                    .font(.system(size: 21))
                    .foregroundStyle(LuminaTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 720, alignment: .leading)
                    .padding(.bottom, 18)

                Button { style.isCustom.toggle() } label: {
                    TVDeviceSettingsRow(title: "Customize subtitles", value: style.isCustom ? "On" : "Off", showsDisclosure: false)
                }
                .buttonStyle(TVDeviceSettingsRowStyle(selected: style.isCustom))
                .accessibilityIdentifier("deviceCustomizeSubtitlesToggle")
                .accessibilityValue(style.isCustom ? "On" : "Off")
                .accessibilityAddTraits(style.isCustom ? .isSelected : [])

                if style.isCustom {
                    subtitleRows
                    Button { style.restoreDefaults() } label: {
                        TVDeviceSettingsRow(title: "Reset appearance", value: "", showsDisclosure: false)
                    }
                    .buttonStyle(TVDeviceSettingsRowStyle())
                    .accessibilityIdentifier("deviceResetSubtitleAppearanceButton")
                }
            }
            .padding(.horizontal, 120)
            .padding(.vertical, 88)
            .frame(maxWidth: 1_040, alignment: .leading)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .accessibilityIdentifier("deviceSubtitleAppearanceScreen")
    }

    private var subtitleRows: some View {
        VStack(spacing: 12) {
            subtitleChoice("Font", selection: $style.font, route: .subtitleFont)
            subtitleChoice("Size", selection: $style.size, route: .subtitleSize)
            subtitleChoice("Color", selection: $style.color, route: .subtitleColor)
            subtitleChoice("Weight", selection: $style.weight, route: .subtitleWeight)
            subtitleChoice("Outline", selection: $style.edgeStyle, route: .subtitleOutline)
            subtitleChoice("Background", selection: $style.background, route: .subtitleBackground)
            subtitleChoice("Opacity", selection: $style.opacity, route: .subtitleOpacity)
            subtitleChoice("Vertical alignment", selection: $style.verticalAlignment, route: .subtitleVerticalAlignment)
        }
    }

    private func subtitleChoice<Value>(
        _ title: String,
        selection: Binding<Value>,
        route: TVDeviceSettingsRoute
    ) -> some View where Value: CaseIterable & Identifiable & Hashable, Value.ID == Value, Value: Sendable {
        Button { openRoute(route) } label: {
            TVDeviceSettingsRow(title: title, value: subtitleLabel(selection.wrappedValue))
        }
        .buttonStyle(TVDeviceSettingsRowStyle())
        .accessibilityIdentifier("deviceSubtitleStyle\(title.replacingOccurrences(of: " ", with: ""))Picker")
    }

    private func subtitleLabel(_ value: some Any) -> String {
        switch value {
        case let value as VideoSubtitleStyle.FontFamily: value.label
        case let value as VideoSubtitleStyle.Size: value.label
        case let value as VideoSubtitleStyle.Color: value.label
        case let value as VideoSubtitleStyle.Weight: value.label
        case let value as VideoSubtitleStyle.EdgeStyle: value.label
        case let value as VideoSubtitleStyle.Background: value.label
        case let value as VideoSubtitleStyle.Opacity: value.label
        case let value as VideoSubtitleStyle.VerticalAlignment: value.label
        default: ""
        }
    }
}

private struct TVDeviceLicensesView: View {
    private let licenses = ["PrismCoreLicense", "MPVKitLicense"].compactMap { name in
        Bundle.main.url(forResource: name, withExtension: "txt").flatMap { try? String(contentsOf: $0, encoding: .utf8) }
    }.joined(separator: "\n\n")
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Open-source licenses")
                    .font(LuminaTheme.editorial(40, relativeTo: .title))
                Text("Playback includes PrismCore, MPVKit and FFmpeg. Complete transitive notices are included in PlaybackNotices.txt in the app bundle.")
                    .font(.system(size: 21))
                    .foregroundStyle(LuminaTheme.muted)
                Link("PrismCore source", destination: URL(string: "https://github.com/Wenzlik/PrismCore/tree/3.8.1")!)
                Link("MPVKit and FFmpeg source and build recipes", destination: URL(string: "https://github.com/Wenzlik/MPVKit/tree/1.0.1")!)
                Text(licenses).font(.footnote)
            }
            .padding(.horizontal, 120)
            .padding(.vertical, 88)
            .frame(maxWidth: 1_040, alignment: .leading)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
    }
}
#endif
