import LuminaCore
import SwiftUI

struct NativeMetadataActions: View {
    let item: MediaItem
    @Environment(LuminaMetadataEditor.self) private var editor
    var body: some View {
        if editor.canEdit, editor.supports(item) {
            Button("Edit metadata", glyph: .edit) { editor.open(item, mode: .edit) }
                .accessibilityIdentifier("editMetadataAction")
            if editor.canMatch(item) {
                Button("Fix match", glyph: .search) { editor.open(item, mode: .match) }
                    .accessibilityIdentifier("fixMatchAction")
            }
        }
    }
}

struct NativeMetadataPresentation: ViewModifier {
    let store: LuminaStore
    func body(content: Content) -> some View {
        @Bindable var editor = store.metadata
        content.environment(editor)
            .sheet(item: $editor.destination) { target in NativeMetadataEditorSheet(editor: editor, target: target) }
            .onChange(of: editor.revision) { _, _ in Task { await store.refresh() } }
    }
}

private struct NativeMetadataEditorSheet: View {
    @Bindable var editor: LuminaMetadataEditor
    let target: MetadataDestination
    @FocusState private var focusedField: String?
    @State private var selectedMatch: MetadataMatch?
    @State private var showsReloadConfirmation = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(editor.document?.name ?? target.name).font(.headline)
                    Text("These changes affect the household library. Saved edits are kept over provider refreshes.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if editor.document == nil && editor.isLoading { ProgressView("Loading metadata…") }
                else if editor.document != nil {
                    if target.mode == .edit { editFields } else { matchFields }
                }
                if let message = editor.errorMessage {
                    Section {
                        Text(message).foregroundStyle(.red).accessibilityIdentifier("metadataEditorError")
                        if target.mode == .edit {
                            Button("Reload latest details") { showsReloadConfirmation = true }.disabled(editor.isSaving)
                        } else if editor.document == nil { Button("Retry") { Task { await editor.load() } } }
                    }
                }
                if editor.isSaving { ProgressView("Saving…") }
            }
            .scrollDismissesKeyboard(.interactively)
            .accessibilityIdentifier("metadataEditorForm")
            .navigationTitle(target.mode == .edit ? "Edit metadata" : "Fix match")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedField = nil }.accessibilityIdentifier("metadataKeyboardDone")
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { editor.destination = nil }.disabled(editor.isSaving)
                        .accessibilityIdentifier("metadataEditorCancel")
                }
                if target.mode == .edit {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") { focusedField = nil; Task { if await editor.save() { editor.destination = nil } } }
                            .disabled(!editor.canEdit || !editor.hasChanges || editor.isLoading || editor.isSaving)
                            .accessibilityIdentifier("metadataEditorSave")
                    }
                }
            }
            .interactiveDismissDisabled(editor.isSaving || editor.hasChanges)
            .task(id: target.id) { await editor.load() }
            .confirmationDialog("Discard your unsaved edits and reload?", isPresented: $showsReloadConfirmation, titleVisibility: .visible) {
                Button("Reload", role: .destructive) { Task { await editor.load() } }
            }
            .confirmationDialog("Use this match?", isPresented: Binding(
                get: { selectedMatch != nil }, set: { if !$0 { selectedMatch = nil } }
            ), titleVisibility: .visible) {
                if let match = selectedMatch {
                    Button("Use \(match.name)") {
                        Task { if await editor.identify(match) { editor.destination = nil } }
                    }.accessibilityIdentifier("metadataMatchConfirm")
                }
            } message: {
                if let match = selectedMatch {
                    Text("Match \(target.name) to \(match.name)\(match.year.map { " (\($0))" } ?? ""). Your own edits and NFO details are kept. Provider details and artwork update in the background.")
                }
            }
        }
    }
    private var editFields: some View {
        Section {
            ForEach(editor.fields) { field in
                let value = Binding(get: { editor.draft[field.id, default: ""] }, set: { editor.draft[field.id] = $0 })
                if field.kind == .season, let seasons = editor.document?.seasons, !seasons.isEmpty {
                    Picker(field.title, selection: value) { ForEach(seasons) { Text($0.name).tag($0.id) } }
                        .accessibilityIdentifier("metadataField-\(field.id)")
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(field.title).font(.caption).foregroundStyle(.secondary)
                        TextField(field.title, text: value, axis: field.kind == .multiline || field.kind == .list ? .vertical : .horizontal)
                            .focused($focusedField, equals: field.id)
                            .lineLimit(field.kind == .multiline ? 4...12 : field.kind == .list ? 2...8 : 1...1)
                            .keyboardType(field.kind == .integer ? .numberPad : field.kind == .decimal ? .decimalPad : .default)
                            .textInputAutocapitalization(field.kind == .list || field.kind == .text || field.kind == .multiline ? .sentences : .never)
                            .accessibilityLabel(field.title).accessibilityIdentifier("metadataField-\(field.id)")
                        if field.kind == .list { Text("One entry per line.").font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
        }.disabled(!editor.canEdit || editor.isSaving || editor.isLoading)
    }
    @ViewBuilder private var matchFields: some View {
        if editor.document?.canIdentify == true, editor.document?.tmdbConfigured == true {
            Section("Search TMDB") {
                TextField("Name", text: $editor.query).focused($focusedField, equals: "matchQuery").onSubmit { focusedField = nil; Task { await editor.search() } }.accessibilityIdentifier("metadataMatchQuery")
                TextField("Year (optional)", text: $editor.year).focused($focusedField, equals: "matchYear").keyboardType(.numberPad).accessibilityIdentifier("metadataMatchYear")
                Button("Search") { focusedField = nil; Task { await editor.search() } }
                    .disabled(!editor.canIdentify || editor.isLoading || editor.isSaving || editor.query.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityIdentifier("metadataMatchSearch")
            }
            Section("Matches") {
                if editor.isLoading { ProgressView("Searching…") }
                ForEach(editor.candidates) { candidate in
                    Button { selectedMatch = candidate } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(candidate.name).font(.headline)
                            if let year = candidate.year { Text(String(year)).font(.subheadline) }
                            if let overview = candidate.overview { Text(overview).font(.caption).foregroundStyle(.secondary).lineLimit(4) }
                            Text("TMDB \(candidate.tmdbID)").font(.caption).foregroundStyle(.secondary)
                        }.foregroundStyle(.primary)
                    }.disabled(!editor.canIdentify || editor.isSaving).accessibilityIdentifier("metadataMatch-\(candidate.id)")
                }
                if editor.hasSearched && editor.candidates.isEmpty && !editor.isLoading { Text("No matches. Try another name or year.") }
            }
        }
    }
}
