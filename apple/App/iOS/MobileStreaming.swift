import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

@MainActor
struct NativeStreamingView: View {
    @Bindable var store: LuminaStore
    let isActive: Bool
    var savedOnly = false

    private enum Section: Hashable {
        case recommendations, live, saved
    }

    private struct RemoteRow: Identifiable {
        let id: String
        let title: String
        let items: [HomeExternalItem]
    }

    private struct TaskScope: Hashable {
        let owner: MobileHomeSessionScope?
        let isActive: Bool
    }

    @State private var savedLibrary = LuminaStreamingLibrary()
    @State private var remoteFeed = LuminaHomeFeed(streamingOnly: true)
    @State private var section: Section = .saved
    @State private var sectionOwner: MobileHomeSessionScope?
    @Environment(\.playRemoteStream) private var playRemoteStream
    @State private var playbackRequest: PlaybackRequest?
    @State private var preparingID: String?
    @State private var playbackError: String?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.scenePhase) private var scenePhase

    private var remoteAvailable: Bool {
        !savedOnly && store.nativeStreamingCapabilities?.remote.available == true
    }

    private var liveAvailable: Bool {
        remoteAvailable && store.nativeStreamingCapabilities?.remote.liveAvailable == true
    }

    private var savedProviders: String {
        let providers = store.nativeStreamingCapabilities?.savedLibrary.providers ?? []
        return providers.map(\.capitalized).joined(separator: " · ")
    }

    private var recommendationRows: [RemoteRow] {
        deduplicatedRemoteRows(remoteFeed.rows.filter { $0.shelfID != .live })
    }

    private var liveRows: [RemoteRow] {
        deduplicatedRemoteRows(remoteFeed.rows.filter { $0.shelfID == .live })
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if savedOnly {
                    savedLibraryView
                } else {
                    sectionTabs

                    switch section {
                    case .recommendations:
                        remoteRowsView(recommendationRows, emptyTitle: "Nothing recommended yet",
                                       emptyDetail: "Recommendations will appear when your connected services have something new.")
                    case .live:
                        remoteRowsView(liveRows, usesGrid: true, emptyTitle: "Nothing live right now",
                                       emptyDetail: "Live streams from connected services will appear here.")
                    case .saved:
                        savedLibraryView
                    }
                }
            }
            .padding(.horizontal, LuminaTheme.contentGutter)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(LuminaTheme.paper)
        .accessibilityIdentifier(savedOnly ? "savedStreamingLibrary" : "streamingScreen")
        .task(id: TaskScope(owner: MobileHomeSessionScope(store.session), isActive: isActive)) {
            guard isActive else { return }
            await load()
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active, isActive else { return }
            Task { await load() }
        }
        .refreshable { await load() }
        .nativePlayback(request: $playbackRequest, store: store) { Task { await savedLibrary.load() } }
        .alert("Playback unavailable", isPresented: Binding(
            get: { playbackError != nil }, set: { if !$0 { playbackError = nil } }
        )) {
            Button("OK") { playbackError = nil }
        } message: {
            Text(playbackError ?? "")
        }
    }

    private var sectionTabs: some View {
        HStack(spacing: 20) {
            if remoteAvailable {
                sectionButton("Recommendations", section: .recommendations, identifier: "streamingRecommendationsTab")
                if liveAvailable {
                    sectionButton("Live", section: .live, identifier: "streamingLiveTab")
                }
            }
            sectionButton("Saved", section: .saved, identifier: "streamingDownloadedTab")
            Spacer(minLength: 0)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private func sectionButton(_ title: String, section target: Section, identifier: String) -> some View {
        Button(title) {
            section = target
        }
        .buttonStyle(LuminaUnderlineTabStyle(selected: section == target, alignsWithMasthead: true))
        .accessibilityIdentifier(identifier)
        .accessibilityValue(section == target ? "Selected" : "")
        .accessibilityAddTraits(section == target ? .isSelected : [])
    }

    @ViewBuilder
    private func remoteRowsView(_ rows: [RemoteRow], usesGrid: Bool = false, emptyTitle: String, emptyDetail: String) -> some View {
        if remoteFeed.isLoading && rows.isEmpty {
            LibraryLoadingView()
        } else if rows.isEmpty && remoteFeed.errorMessage != nil {
            InlineErrorView(message: "Couldn't load streaming discovery.") { Task { await load() } }
        } else if rows.isEmpty {
            ContentUnavailableView(emptyTitle, systemImage: "dot.radiowaves.left.and.right", description: Text(emptyDetail))
        } else {
            LazyVStack(alignment: .leading, spacing: 24) {
                ForEach(rows) { row in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(row.title).font(.title3.weight(.bold))
                        if usesGrid {
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: dynamicTypeSize.isAccessibilitySize ? 240 : 150),
                                                        spacing: 14)], alignment: .leading, spacing: 18) {
                                ForEach(row.items) { item in
                                    remoteCard(item) { StreamingRemoteCard(item: item) }
                                }
                            }
                        } else {
                            ScrollView(.horizontal, showsIndicators: false) {
                                LazyHStack(alignment: .top, spacing: 12) {
                                    ForEach(row.items) { item in
                                        remoteCard(item) {
                                            StreamingRemoteCard(item: item)
                                                .containerRelativeFrame(.horizontal) { width, _ in min(292, width * 0.76) }
                                        }
                                    }
                                }
                                .scrollTargetLayout()
                            }
                            .scrollTargetBehavior(.viewAligned)
                            .modifier(LuminaCarouselEdges())
                        }
                    }
                }
            }
        }
    }

    private func remoteCard<Card: View>(_ item: HomeExternalItem, @ViewBuilder label: () -> Card) -> some View {
        Button { playRemoteStream(item) } label: { label() }
            .buttonStyle(.plain)
            .accessibilityIdentifier("streamingRemote-\(item.id)")
            .accessibilityLabel("\(item.title), \(item.subtitle)")
    }

    private func deduplicatedRemoteRows(_ rows: [HomeFeedRow]) -> [RemoteRow] {
        var seen = Set<String>()
        return rows.compactMap { row in
            let items = (row.externalItems ?? []).filter { seen.insert($0.id).inserted && $0.sourceURL != nil }
            guard !items.isEmpty else { return nil }
            return RemoteRow(id: row.id, title: row.displayTitle, items: items)
        }
    }

    @ViewBuilder private var savedLibraryView: some View {
        if savedLibrary.isBusy && savedLibrary.items.isEmpty {
            LibraryLoadingView()
        } else if savedLibrary.items.isEmpty {
            ContentUnavailableView {
                Label("No saved streaming videos", glyph: .video, size: 36)
            } description: {
                Text(savedProviders.isEmpty ? "Videos downloaded from supported streaming services will appear here."
                     : "Saved \(savedProviders) videos will appear here.")
            }
        } else {
            VStack(alignment: .leading, spacing: 14) {
                if !savedOnly {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Saved").font(.title3.weight(.bold))
                        Spacer()
                        if !savedProviders.isEmpty {
                            Text(savedProviders).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(1)
                        }
                    }
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: dynamicTypeSize.isAccessibilitySize ? 220 : 150), spacing: 14)], spacing: 18) {
                    ForEach(savedLibrary.items) { item in
                        Button { play(item) } label: {
                            StreamingSavedCard(item: item, imageURL: store.imageURL(for: item), preparing: preparingID == item.id)
                        }
                        .buttonStyle(.plain)
                        .disabled(preparingID != nil)
                        .accessibilityIdentifier("streamingSaved-\(item.id)")
                        .accessibilityLabel("Play \(item.name)")
                    }
                }
                if savedLibrary.hasMore {
                    Button("Load more") { Task { await savedLibrary.loadMore() } }
                        .buttonStyle(LuminaQuietActionStyle())
                        .frame(maxWidth: .infinity)
                        .disabled(savedLibrary.isBusy)
                }
            }
        }
    }

    private func load() async {
        let owner = MobileHomeSessionScope(store.session)
        savedLibrary.configure(session: store.session)
        if savedOnly {
            await savedLibrary.load()
            return
        }
        remoteFeed.configure(session: store.session)
        async let capabilities: NativeStreamingCapabilities? = store.loadNativeStreamingCapabilities()
        async let saved: Void = savedLibrary.load()
        let resolvedCapabilities = await capabilities
        _ = await saved
        guard !Task.isCancelled, owner == MobileHomeSessionScope(store.session) else { return }
        let remoteIsAvailable = resolvedCapabilities?.remote.available == true
        let liveIsAvailable = remoteIsAvailable && resolvedCapabilities?.remote.liveAvailable == true
        if sectionOwner != owner {
            section = remoteIsAvailable ? .recommendations : .saved
            sectionOwner = owner
        } else if !remoteIsAvailable, section != .saved {
            section = .saved
        } else if !liveIsAvailable, section == .live {
            section = .recommendations
        }
        if remoteIsAvailable {
            await remoteFeed.load()
        }
    }

    private func play(_ item: MediaItem) {
        guard preparingID == nil else { return }
        preparingID = item.id
        playbackError = nil
        Task {
            defer { preparingID = nil }
            do { playbackRequest = try await store.preparePlayback(for: item) }
            catch { playbackError = "This saved video could not be played. Try again from your library." }
        }
    }
}

private struct StreamingRemoteCard: View {
    let item: HomeExternalItem

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ArtworkView(url: item.artworkURL, name: item.title, aspect: 16 / 9, glyph: .video)
                .overlay(alignment: .bottomLeading) {
                    Label("Play", glyph: .play, variant: .solid, size: 16)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8).padding(.vertical, 5)
                        .background(.black.opacity(0.72), in: Capsule())
                        .padding(8)
                }
            Text(item.title).font(.subheadline.weight(.semibold)).lineLimit(2)
            Text(item.subtitle).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(1)
        }
        .contentShape(.rect)
    }
}

private struct StreamingSavedCard: View {
    let item: MediaItem
    let imageURL: URL?
    let preparing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ArtworkView(url: imageURL, name: item.name, aspect: 16 / 9, glyph: .video)
                .overlay {
                    if preparing { ProgressView() }
                }
                .overlay(alignment: .bottomLeading) {
                    Label("Saved", glyph: .downloadDone, size: 14)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 7).padding(.vertical, 4)
                        .background(LuminaTheme.paper.opacity(0.9), in: Capsule())
                        .padding(8)
                }
            Text(item.name).font(.subheadline.weight(.semibold)).lineLimit(2)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle).font(.caption).foregroundStyle(LuminaTheme.muted).lineLimit(1)
            }
        }
        .contentShape(.rect)
    }
}

/// Account owns Home customization; the editor keeps the same server-backed layout contract.
@MainActor
struct NativeHomePreferencesView: View {
    let store: LuminaStore
    @State private var layout: LuminaHomeLayout?

    var body: some View {
        Group {
            if let layout {
                NativeHomeLayoutEditor(layout: layout, store: store, feed: nil)
            } else {
                ProgressView("Loading Home layout…")
            }
        }
        .task(id: MobileHomeSessionScope(store.session)) {
            guard let session = store.session else { return }
            let loaded = LuminaHomeLayout(session: session)
            layout = loaded
            await loaded.load()
        }
    }
}

@MainActor
private struct NativeHomeLayoutEditor: View {
    let layout: LuminaHomeLayout
    let store: LuminaStore
    let feed: LuminaHomeFeed?
    @State private var draft: [HomeShelfPreference] = []
    @State private var changed = false
    @State private var reset = false
    @Environment(\.dismiss) private var dismiss
    private var rows: [HomeShelfPreference] { draft }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Cancel") { dismiss() }.disabled(layout.isSaving)
                    .accessibilityIdentifier("homeLayoutCancelButton")
                Spacer()
                Text("Edit Home").font(LuminaTheme.heading(22, relativeTo: .title3))
                Spacer()
                Button("Done") {
                    Task { if await layout.save(reset ? nil : draft) { dismiss() } }
                }.disabled(!layout.hasLoaded || layout.isSaving)
                    .accessibilityIdentifier("homeLayoutSaveButton")
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .padding(.horizontal, 24).padding(.vertical, 8)
            List {
                Section {
                    ForEach(rows) { shelf in
                        VStack(alignment: .leading, spacing: 0) {
                            LuminaSettingsSwitch(shelf.id.title, isOn: Binding(get: {
                                draft.first { $0.id == shelf.id }?.visible ?? true
                            }, set: { visible in
                                if let index = draft.firstIndex(where: { $0.id == shelf.id }) {
                                    draft[index] = HomeShelfPreference(id: shelf.id, visible: visible)
                                    changed = true; reset = false
                                }
                            }), identifier: "homeShelfVisibility-\(shelf.id.rawValue)")
                            .accessibilityAction(named: "Move up") { move(shelf.id, offset: -1) }
                            .accessibilityAction(named: "Move down") { move(shelf.id, offset: 1) }
                            if feed?.hasLoaded == true && isEmpty(shelf.id) {
                                Text("Empty").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    }
                    .onMove { source, target in
                        var reordered = rows
                        reordered.move(fromOffsets: source, toOffset: target)
                        applyOrder(reordered)
                    }
                }
                .disabled(!layout.hasLoaded || layout.isSaving)
                Section {
                    Button("Reset to default") {
                        draft = HomeShelfID.allCases.map { HomeShelfPreference(id: $0, visible: true) }
                        changed = true; reset = true
                    }.accessibilityIdentifier("homeLayoutResetButton")
                        .disabled(!layout.hasLoaded || layout.isSaving)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if layout.isLoading { ProgressView("Loading Home layout…") }
                if let error = layout.errorMessage {
                    Section {
                        Text(error).foregroundStyle(.red)
                        Button("Try again") { Task { await layout.load() } }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(LuminaTheme.paper)
            .foregroundStyle(LuminaTheme.ink)
            .environment(\.editMode, .constant(.active))
            .interactiveDismissDisabled(layout.isSaving)
            .onAppear {
                draft = layout.shelves
                changed = false
                reset = false
            }
            .onChange(of: layout.shelves, initial: true) { _, value in
                if !changed { draft = value }
            }
            .accessibilityIdentifier("homeLayoutEditor")
        }
        .background(LuminaTheme.paper)
        .foregroundStyle(LuminaTheme.ink)
    }

    private func applyOrder(_ reordered: [HomeShelfPreference]) {
        draft = reordered
        changed = true; reset = false
    }

    private func move(_ id: HomeShelfID, offset: Int) {
        var reordered = rows
        guard let index = reordered.firstIndex(where: { $0.id == id }) else { return }
        let target = index + offset
        guard reordered.indices.contains(target) else { return }
        reordered.swapAt(index, target)
        applyOrder(reordered)
    }

    private func isEmpty(_ id: HomeShelfID) -> Bool {
        if let feed, feed.hasLoaded {
            return !feed.rows.contains { $0.shelfID == id && $0.hasItems }
        }
        return switch id.rawValue {
        case "continue": store.resumeItems.isEmpty
        case "next_up": store.nextUpItems.isEmpty
        case "new_in_library": store.recentItems.isEmpty
        default: true
        }
    }
}
