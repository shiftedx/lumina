import SwiftUI
import LuminaCore

/// A cinematic tvOS title screen backed by the same metadata and artwork used throughout Lumina.
@MainActor
struct TVDetailView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void

    @State private var isPreparingPlayback = false
    @State private var playbackError: String?
    @State private var refreshedItem: MediaItem?
    @State private var relatedItems = [MediaItem]()
    @State private var detailError: String?
    @State private var isLoadingDetail = true
    @State private var playbackPreparationTask: Task<Void, Never>?
    @State private var playbackPreparationID: UUID?
    @FocusState private var focusedAction: Action?

    private enum Action: Hashable { case play, watchlist, more }
    private var currentItem: MediaItem { refreshedItem ?? item }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                MediaBillboard(item: currentItem, store: store, context: "", showsOverview: false, showsContext: false) {
                    if !metadataFacts.isEmpty {
                        Text(metadataFacts.joined(separator: " · "))
                            .font(.system(size: 22, weight: .medium))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                    detailActionRow
                    detailStatus
                }

                if let overview = currentItem.overview, !overview.isEmpty {
                    TVDetailStory(overview: overview)
                        .padding(.horizontal, 82)
                }

                if !castMembers.isEmpty {
                    TVDetailCastRail(people: castMembers, store: store)
                        .padding(.horizontal, 82)
                }

                if !relatedItems.isEmpty {
                    TVDetailRelatedRail(items: relatedItems, store: store, onOpen: onOpen)
                        .padding(.horizontal, 82)
                }
            }
            .padding(.bottom, 76)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("detailScreen")
        .onPlayPauseCommand { preparePlayback(forceTranscode: false) }
        .defaultFocus($focusedAction, .play)
        .task(id: item.id) { await refreshItem() }
        .onDisappear { cancelPlaybackPreparation() }
    }

    private var metadataFacts: [String] {
        var facts = [String]()
        if let genres = currentItem.genres?.filter({ !$0.isEmpty }), !genres.isEmpty {
            facts.append(genres.prefix(3).joined(separator: " · "))
        }
        if let officialRating = currentItem.officialRating, !officialRating.isEmpty {
            facts.append(officialRating)
        }
        if let communityRating = currentItem.communityRating {
            facts.append("\(communityRating.formatted(.number.precision(.fractionLength(1)))) / 10")
        }
        return facts
    }


    private var detailActionRow: some View {
        HStack(alignment: .top, spacing: 16) {
            playbackButton
            if !currentItem.isAudio {
                TVWatchlistButton(item: currentItem, store: store)
                    .focused($focusedAction, equals: .watchlist)
                TVDetailOverflowActions(
                    item: currentItem,
                    store: store,
                    isLoading: isLoadingDetail || isPreparingPlayback,
                    onUpdated: { refreshedItem = $0 },
                    onCompatibilityPlayback: { preparePlayback(forceTranscode: true) }
                )
                .focused($focusedAction, equals: .more)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .focusSection()
    }

    private var castMembers: [MediaPerson] {
        var seen = Set<String>()
        return (currentItem.people ?? []).filter { person in
            guard person.type?.caseInsensitiveCompare("Actor") == .orderedSame else { return false }
            return seen.insert(person.detailIdentity).inserted
        }
    }

    private var playbackButton: some View {
        Button {
            preparePlayback(forceTranscode: false)
        } label: {
            Label(isPreparingPlayback ? "Preparing…" : currentItem.resumeSeconds > 5 ? "Resume" : "Play", systemImage: "play.fill")
                .frame(minWidth: 172)
        }
        .buttonStyle(LuminaQuietActionStyle(primary: true))
        .tint(LuminaTheme.gold)
        .foregroundStyle(LuminaTheme.theater)
        .controlSize(.large)
        .disabled(isPreparingPlayback || !currentItem.isPlayable)
        .accessibilityIdentifier("playButton")
        .focused($focusedAction, equals: .play)
    }

    @ViewBuilder private var detailStatus: some View {
        if let detailError {
            Label(detailError, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(LuminaTheme.spotlight)
                .accessibilityIdentifier("detailStateError")
        }
        if let playbackError {
            Label(playbackError, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(LuminaTheme.spotlight)
                .accessibilityIdentifier("playbackError")
        }
    }

    private func refreshItem() async {
        isLoadingDetail = true
        detailError = nil
        defer { isLoadingDetail = false }
        do {
            let loaded = try await store.loadItem(item)
            guard !Task.isCancelled else { return }
            refreshedItem = loaded
            // Related titles enhance the screen when available but never replace usable title details.
            let related = try? await store.relatedItems(for: loaded)
            guard !Task.isCancelled else { return }
            relatedItems = related?.filter { $0.id != loaded.id } ?? []
        } catch {
            detailError = (error as? LocalizedError)?.errorDescription ?? "The latest title details could not load. Check your connection and try again."
        }
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard currentItem.isPlayable, !isPreparingPlayback else { return }
        let preparationID = UUID()
        let playbackItem = currentItem
        playbackPreparationID = preparationID
        isPreparingPlayback = true
        playbackError = nil
        playbackPreparationTask = Task {
            defer { finishPlaybackPreparation(preparationID) }
            do {
                let request = try await store.preparePlayback(for: playbackItem, forceTranscode: forceTranscode)
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                onPlayback(request)
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                playbackError = forceTranscode
                    ? "Compatibility conversion could not start. Check the server and try again."
                    : "Playback could not start. Try Convert for compatibility."
            }
        }
    }

    private func finishPlaybackPreparation(_ preparationID: UUID) {
        guard playbackPreparationID == preparationID else { return }
        playbackPreparationTask = nil
        playbackPreparationID = nil
        isPreparingPlayback = false
    }

    private func cancelPlaybackPreparation() {
        playbackPreparationTask?.cancel()
        playbackPreparationTask = nil
        playbackPreparationID = nil
        isPreparingPlayback = false
    }
}

/// Series and seasons retain their regular navigation callbacks while offering one-click next playback.
@MainActor
struct TVVideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void

    @State private var children = [MediaItem]()
    @State private var refreshedItem: MediaItem?
    @State private var relatedItems = [MediaItem]()
    @State private var isLoading = true
    @State private var isPreparingPlayback = false
    @State private var errorMessage: String?
    @State private var playbackPreparationTask: Task<Void, Never>?
    @State private var playbackPreparationID: UUID?
    @FocusState private var focusedAction: Action?

    private enum Action: Hashable { case play, watchlist, more }
    private var currentItem: MediaItem { refreshedItem ?? item }
    private var isSeries: Bool { currentItem.type == "Series" }
    private var sectionTitle: String { isSeries ? "Seasons" : "Episodes" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                MediaBillboard(item: currentItem, store: store, context: LocalizedStringKey(isSeries ? "TV show" : currentItem.seriesName ?? "Season"), showsOverview: false) {
                    collectionActionRow
                    if let errorMessage, !isLoading {
                        Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(LuminaTheme.spotlight)
                            .accessibilityIdentifier("collectionError")
                    }
                }

                VStack(alignment: .leading, spacing: 26) {
                    Text(sectionTitle)
                        .font(LuminaTheme.editorial(42, relativeTo: .title, italic: true))
                        .foregroundStyle(.white)
                    collectionContent
                }
                .padding(.horizontal, 82)

                if !castMembers.isEmpty {
                    TVDetailCastRail(people: castMembers, store: store)
                        .padding(.horizontal, 82)
                }

                if !relatedItems.isEmpty {
                    TVDetailRelatedRail(items: relatedItems, store: store, onOpen: onOpen)
                        .padding(.horizontal, 82)
                }
            }
            .padding(.bottom, 76)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("videoCollectionScreen")
        .onPlayPauseCommand { if !isLoading { preparePlayback(forceTranscode: false) } }
        .defaultFocus($focusedAction, .play)
        .task(id: item.id) { await load() }
        .onDisappear { cancelPlaybackPreparation() }
    }

    private var collectionActionRow: some View {
        HStack(alignment: .top, spacing: 16) {
            collectionPlayButton
            TVWatchlistButton(item: currentItem, store: store)
                .focused($focusedAction, equals: .watchlist)
            TVDetailOverflowActions(
                item: currentItem,
                store: store,
                isLoading: isLoading || isPreparingPlayback,
                onUpdated: { refreshedItem = $0 },
                onCompatibilityPlayback: { preparePlayback(forceTranscode: true) }
            )
            .focused($focusedAction, equals: .more)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .focusSection()
    }

    private var castMembers: [MediaPerson] {
        var seen = Set<String>()
        return (currentItem.people ?? []).filter { person in
            guard person.type?.caseInsensitiveCompare("Actor") == .orderedSame else { return false }
            return seen.insert(person.detailIdentity).inserted
        }
    }

    private var collectionPlayButton: some View {
        Button {
            preparePlayback(forceTranscode: false)
        } label: {
            Label(isPreparingPlayback ? "Preparing…" : playLabel, systemImage: "play.fill")
                .frame(minWidth: 172)
        }
        .buttonStyle(LuminaQuietActionStyle(primary: true))
        .tint(LuminaTheme.gold)
        .foregroundStyle(LuminaTheme.theater)
        .controlSize(.large)
        .disabled(isPreparingPlayback || isLoading)
        .accessibilityIdentifier("playButton")
        .focused($focusedAction, equals: .play)
    }

    private var playLabel: String {
        if hasResumableEpisode { return "Resume next" }
        return "Play next"
    }

    private var hasResumableEpisode: Bool {
        store.resumeItems.contains { candidate in
            candidate.type == "Episode" && (candidate.seriesID == item.id || candidate.seasonID == item.id) && candidate.userData?.played != true
        }
    }

    @ViewBuilder private var collectionContent: some View {
        if isLoading {
            ProgressView("Loading \(sectionTitle.lowercased())…")
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if let errorMessage {
            TVMessageView(title: "Could not load this collection", message: errorMessage, actionTitle: "Try again") { Task { await load() } }
        } else if children.isEmpty {
            TVMessageView(title: "No \(sectionTitle.lowercased()) yet", message: "Refresh after adding this title to your Lumina library.", actionTitle: "Refresh") { Task { await load() } }
        } else if isSeries {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 230, maximum: 286), spacing: 30, alignment: .top)], spacing: 48) {
                ForEach(children) { season in
                    TVMediaCard(item: season, store: store, poster: true) { onOpen(season) }
                        .accessibilityIdentifier("videoCollectionItem-\(season.id)")
                }
            }
            .focusSection()
        } else {
            LazyVStack(spacing: 22) {
                ForEach(children) { episode in
                    Button { onOpen(episode) } label: {
                        episodeRow(episode)
                    }
                    .buttonStyle(.card)
                    .accessibilityIdentifier("videoCollectionItem-\(episode.id)")
                }
            }
            .focusSection()
        }
    }

    private func episodeRow(_ episode: MediaItem) -> some View {
        HStack(spacing: 28) {
            TVArtwork(item: episode, store: store, aspect: 16 / 9)
                .frame(width: 330)
                .overlay(alignment: .bottom) {
                    if episode.resumeSeconds > 0, episode.durationSeconds > 0 {
                        ProgressView(value: min(episode.resumeSeconds / episode.durationSeconds, 1))
                            .progressViewStyle(.linear)
                            .tint(LuminaTheme.gold)
                            .frame(height: 4)
                            .padding(.horizontal, 12)
                            .padding(.bottom, 10)
                    }
                }
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Text(episode.displaySubtitle)
                    if episode.userData?.played == true {
                        Label("Watched", systemImage: "checkmark.circle.fill")
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.78))
                Text(episode.name)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                if let overview = episode.overview, !overview.isEmpty {
                    Text(overview)
                        .font(.body)
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 24)
            Image(systemName: "chevron.forward")
                .foregroundStyle(.white.opacity(0.72))
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.06), in: .rect(cornerRadius: 12))
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Open \(episode.name), \(episode.displaySubtitle)")
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let loaded = try await store.loadItem(item)
            guard !Task.isCancelled else { return }
            refreshedItem = loaded
            children = try await store.loadChildren(of: item)
            guard !Task.isCancelled else { return }
            let related = try? await store.relatedItems(for: loaded)
            guard !Task.isCancelled else { return }
            relatedItems = related?.filter { $0.id != loaded.id } ?? []
        } catch {
            errorMessage = (error as? LocalizedError)?.errorDescription ?? "This collection could not load. Check your connection and try again."
        }
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard !isPreparingPlayback else { return }
        let preparationID = UUID()
        playbackPreparationID = preparationID
        isPreparingPlayback = true
        errorMessage = nil
        playbackPreparationTask = Task {
            defer { finishPlaybackPreparation(preparationID) }
            do {
                let episode = try await nextPlayableEpisode()
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                let request = try await store.preparePlayback(for: episode, forceTranscode: forceTranscode)
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                onPlayback(request)
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                errorMessage = forceTranscode
                    ? "Compatibility conversion could not start. Check the server and try again."
                    : (error as? LocalizedError)?.errorDescription ?? "No playable episode is available yet. Check the collection and try again."
            }
        }
    }

    private func finishPlaybackPreparation(_ preparationID: UUID) {
        guard playbackPreparationID == preparationID else { return }
        playbackPreparationTask = nil
        playbackPreparationID = nil
        isPreparingPlayback = false
    }

    private func cancelPlaybackPreparation() {
        playbackPreparationTask?.cancel()
        playbackPreparationTask = nil
        playbackPreparationID = nil
        isPreparingPlayback = false
    }

    private func nextPlayableEpisode() async throws -> MediaItem {
        if let resume = store.resumeItems.first(where: { candidate in
            candidate.type == "Episode" && (candidate.seriesID == item.id || candidate.seasonID == item.id) && candidate.userData?.played != true
        }) {
            return resume
        }

        let seasons: [MediaItem] = isSeries ? (children.isEmpty ? try await store.loadChildren(of: item) : children) : [item]
        var episodes = [MediaItem]()
        for season in seasons {
            episodes.append(contentsOf: try await store.loadChildren(of: season))
        }
        guard let episode = episodes.first(where: { $0.resumeSeconds > 0 && $0.userData?.played != true })
            ?? episodes.first(where: { $0.userData?.played != true })
            ?? episodes.first else {
            throw TVCollectionPlaybackError.noEpisodes
        }
        return episode
    }
}

private extension MediaPerson {
    /// Server IDs are preferred; the name/type fallback remains stable for people without IDs.
    var detailIdentity: String { id ?? "\(type ?? "person")-\(name)" }

    var detailRole: String { role ?? type ?? "Cast" }
}

private struct TVDetailStory: View {
    let overview: String

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("The story")
                .font(LuminaTheme.editorial(30, relativeTo: .title3, italic: true))
                .foregroundStyle(.white)
            Text(overview)
                .font(.system(size: 26))
                .lineSpacing(4)
                .foregroundStyle(.white.opacity(0.9))
        }
        .frame(maxWidth: 1_060, alignment: .leading)
    }
}

private struct TVDetailCastRail: View {
    let people: [MediaPerson]
    let store: LuminaStore

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Cast")
                .font(LuminaTheme.editorial(34, relativeTo: .title2, italic: true))
                .foregroundStyle(.white)
            HStack(alignment: .top, spacing: 24) {
                ForEach(people.prefix(8), id: \.detailIdentity) { person in
                    TVDetailCastMember(person: person, store: store)
                }
            }
            .accessibilityElement(children: .contain)
        }
        .accessibilityIdentifier("detailCastRail")
    }
}

private struct TVDetailCastMember: View {
    let person: MediaPerson
    let store: LuminaStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            portrait
                .frame(width: 148, height: 204)
                .clipShape(.rect(cornerRadius: 10))
                .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(.white.opacity(0.12)) }
            Text(person.name)
                .font(.system(size: 23, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(2)
            Text(person.detailRole)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.7))
                .lineLimit(2)
        }
        .frame(width: 164, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(person.name), \(person.detailRole)")
    }

    @ViewBuilder private var portrait: some View {
        if let url = store.personImageURL(for: person, maxWidth: 240) {
            AsyncImage(url: url) { phase in
                if case .success(let image) = phase {
                    image.resizable().scaledToFill()
                } else {
                    placeholder
                }
            }
        } else {
            placeholder
        }
    }

    private var placeholder: some View {
        ZStack {
            Rectangle().fill(.white.opacity(0.06))
            Image(systemName: "person.crop.circle")
                .font(.system(size: 42, weight: .light))
                .foregroundStyle(.white.opacity(0.38))
        }
    }
}

private struct TVDetailRelatedRail: View {
    let items: [MediaItem]
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("More like this")
                .font(LuminaTheme.editorial(34, relativeTo: .title2, italic: true))
                .foregroundStyle(.white)
            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 28) {
                    ForEach(items) { related in
                        TVMediaCard(item: related, store: store, poster: true) { onOpen(related) }
                            .frame(width: 220)
                            .accessibilityIdentifier("relatedTitle-\(related.id)")
                    }
                }
                .padding(.vertical, 6)
            }
            .scrollIndicators(.hidden)
            .scrollClipDisabled()
            .focusSection()
        }
        .accessibilityIdentifier("relatedTitlesRail")
    }
}

@MainActor
private struct TVWatchlistButton: View {
    let item: MediaItem
    let store: LuminaStore
    @State private var watchlist: LuminaWatchlist?
    @FocusState private var isFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                guard let watchlist else { return }
                Task { await watchlist.set(itemID: item.id, included: !watchlist.isIncluded) }
            } label: {
                Label(watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist", systemImage: watchlist?.isIncluded == true ? "checkmark" : "plus")
                    .frame(minWidth: 190)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .tint(.white)
            .foregroundStyle(isFocused ? LuminaTheme.theater : .white)
            .focused($isFocused)
            .controlSize(.large)
            .disabled(watchlist == nil || watchlist?.isLoading == true || watchlist?.isSaving == true || watchlist?.errorMessage != nil)
            .accessibilityIdentifier("videoWatchlistButton")
            .accessibilityValue(watchlist?.isIncluded == true ? "In watchlist" : "Not in watchlist")
            .accessibilityHint(watchlist?.isIncluded == true ? "Remove from your watchlist" : "Save to your watchlist")

            if let message = watchlist?.errorMessage {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(LuminaTheme.spotlight)
            }
        }
        .task(id: item.id + (store.session?.accessToken ?? "")) {
            let model = LuminaWatchlist(session: store.session)
            watchlist = model
            await model.load(itemID: item.id)
        }
    }
}

@MainActor
private struct TVDetailOverflowActions: View {
    let item: MediaItem
    let store: LuminaStore
    let isLoading: Bool
    let onUpdated: (MediaItem) -> Void
    let onCompatibilityPlayback: () -> Void

    @State private var isSaving = false
    @FocusState private var isFocused: Bool
    @State private var errorMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Menu {
                Button {
                    update(played: item.userData?.played != true)
                } label: {
                    Label(item.userData?.played == true ? "Mark unwatched" : "Mark watched", systemImage: item.userData?.played == true ? "checkmark.circle.fill" : "checkmark.circle")
                }
                .accessibilityIdentifier("videoWatchedButton")

                Button {
                    update(favorite: item.userData?.isFavorite != true)
                } label: {
                    Label(item.userData?.isFavorite == true ? "Remove favorite" : "Add favorite", systemImage: item.userData?.isFavorite == true ? "heart.fill" : "heart")
                }
                .accessibilityIdentifier("videoFavoriteButton")

                if item.isPlayable {
                    Divider()
                    Button {
                        onCompatibilityPlayback()
                    } label: {
                        Label("Convert for compatibility", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .accessibilityIdentifier("convertButton")
                }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
                    .frame(minWidth: 132)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .tint(.white)
            .foregroundStyle(isFocused ? LuminaTheme.theater : .white)
            .focused($isFocused)
            .controlSize(.large)
            .disabled(isLoading || isSaving)
            .accessibilityIdentifier("titleActionsMenu")
            .accessibilityHint("Mark watched, change favorite, or convert for compatibility")

            if let errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(LuminaTheme.spotlight)
                    .accessibilityIdentifier("videoStateError")
            }
        }
    }

    private func update(played: Bool? = nil, favorite: Bool? = nil) {
        guard !isSaving, !isLoading else { return }
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                onUpdated(try await store.updateItemState(item: item, played: played, favorite: favorite))
            } catch {
                errorMessage = (error as? LocalizedError)?.errorDescription ?? "Your activity could not be saved. Check your connection and try again."
            }
        }
    }
}

private enum TVCollectionPlaybackError: LocalizedError {
    case noEpisodes

    var errorDescription: String? {
        switch self {
        case .noEpisodes: "No playable episode is available yet."
        }
    }
}
