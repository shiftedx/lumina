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
        .buttonStyle(TVDetailTextActionStyle(primary: true))
        .tint(LuminaTheme.gold)
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

/// A show opens on its current episode; seasons stay in place above a horizontal episode rail.
@MainActor
struct TVVideoCollectionView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void

    @State private var seasons = [MediaItem]()
    @State private var episodes = [MediaItem]()
    @State private var selectedSeasonID: String?
    @State private var selectedEpisodeID: String?
    @State private var detailRevision = 0
    @State private var episodeDetails = [String: MediaItem]()
    @State private var seasonEpisodes = [String: [MediaItem]]()
    @State private var refreshedItem: MediaItem?
    @State private var relatedItems = [MediaItem]()
    @State private var isLoading = true
    @State private var isLoadingSeason = false
    @State private var isPreparingPlayback = false
    @State private var errorMessage: String?
    @State private var playbackPreparationTask: Task<Void, Never>?
    @State private var playbackPreparationID: UUID?
    @State private var seasonTask: Task<Void, Never>?
    @State private var seasonGeneration = UUID()
    @FocusState private var focusedAction: Action?
    @State private var returnEpisodeID: String?

    private enum Action: Hashable { case play, watchlist, more, episode(String) }
    private var currentItem: MediaItem { refreshedItem ?? item }
    private var selectedEpisode: MediaItem? {
        guard let selectedEpisodeID else { return nil }
        if let detail = episodeDetails[selectedEpisodeID] { return detail }
        return episodes.first { $0.id == selectedEpisodeID }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                episodeHero
                collectionContent
                if !castMembers.isEmpty {
                    TVDetailCastRail(people: castMembers, store: store)
                        .padding(.horizontal, 82).padding(.top, 30)
                }
                if !relatedItems.isEmpty {
                    TVDetailRelatedRail(items: relatedItems, store: store, onOpen: onOpen)
                        .padding(.horizontal, 82).padding(.top, 30)
                }
            }
            .padding(.bottom, 76)
        }
        .ignoresSafeArea(.container, edges: [.horizontal, .top])
        .background(LuminaTheme.theater.ignoresSafeArea())
        .accessibilityIdentifier("videoCollectionScreen")
        .onPlayPauseCommand { if !isLoading { preparePlayback(forceTranscode: false) } }
        .defaultFocus($focusedAction, returnEpisodeID.map(Action.episode) ?? .play)
        .task(id: item.id) { await load() }
        .task(id: "\(selectedEpisodeID ?? "")-\(detailRevision)") { await loadEpisodeDetail() }
        .onChange(of: focusedAction) { _, action in
            if case let .episode(id)? = action { selectedEpisodeID = id }
        }
        .onDisappear {
            cancelPlaybackPreparation()
            seasonTask?.cancel()
            seasonGeneration = UUID()
            isLoadingSeason = false
        }
    }

    private var episodeHero: some View {
        VStack(alignment: .leading, spacing: 20) {
            MediaTitleArtwork(name: currentItem.name, url: store.logoURL(for: currentItem), maximumWidth: 380, maximumHeight: 130) {
                Text(currentItem.name)
                    .font(.system(size: 54, weight: .bold))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("detailTitle")
            }
            .frame(height: 130, alignment: .leading)

            if let episode = selectedEpisode {
                Text(episodeHeading(episode))
                    .font(.system(size: 29, weight: .semibold))
                    .lineLimit(2)
                    .accessibilityIdentifier("collectionEpisodeTitle")
                episodeFacts(episode)
                Text(episode.overview ?? currentItem.overview ?? "")
                    .font(.system(size: 25))
                    .lineSpacing(3)
                    .lineLimit(3)
                    .frame(height: 96, alignment: .topLeading)
                    .accessibilityIdentifier("collectionEpisodeSynopsis")
            } else {
                Text(currentItem.overview ?? "")
                    .font(.system(size: 25))
                    .lineLimit(3)
                    .frame(height: 180, alignment: .topLeading)
            }
            Spacer(minLength: 24)
            collectionActionRow
            if let errorMessage, !isLoading {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 23))
                    .foregroundStyle(LuminaTheme.spotlight)
                    .accessibilityIdentifier("collectionError")
            }
        }
        .foregroundStyle(.white)
        .frame(width: 760, alignment: .leading)
        .frame(minHeight: 584, alignment: .topLeading)
        .padding(.horizontal, 82)
        .padding(.top, 64)
        .padding(.bottom, 32)
        .frame(maxWidth: .infinity, minHeight: 680, alignment: .topLeading)
        .background {
            ZStack {
                MediaBackdrop(item: currentItem, store: store)
                LinearGradient(stops: [
                    .init(color: LuminaTheme.theater.opacity(0.94), location: 0),
                    .init(color: LuminaTheme.theater.opacity(0.72), location: 0.32),
                    .init(color: .clear, location: 0.72),
                ], startPoint: .leading, endPoint: .trailing)
                LinearGradient(stops: [
                    .init(color: .clear, location: 0.50),
                    .init(color: LuminaTheme.theater, location: 1),
                ], startPoint: .top, endPoint: .bottom)
            }
        }
    }

    private func episodeHeading(_ episode: MediaItem) -> String {
        let number = [episode.parentIndexNumber.map { "S\($0)" }, episode.indexNumber.map { "E\($0)" }]
            .compactMap { $0 }.joined(separator: " · ")
        return number.isEmpty ? episode.name : "\(number) — \(episode.name)"
    }

    private func episodeFacts(_ episode: MediaItem) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(technicalFacts(episode).joined(separator: "  ·  "))
            if let genres = currentItem.genres, !genres.isEmpty {
                Text(genres.prefix(3).joined(separator: ", "))
            }
            if let rating = episode.communityRating ?? currentItem.communityRating {
                Label("\(rating.formatted(.number.precision(.fractionLength(1)))) / 10", systemImage: "star.fill")
            }
        }
        .font(.system(size: 23, weight: .medium))
        .foregroundStyle(.white.opacity(0.86))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("collectionEpisodeMetadata")
    }

    private func technicalFacts(_ episode: MediaItem) -> [String] {
        var facts = [String]()
        if episode.durationSeconds > 0 {
            facts.append(Duration.seconds(episode.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
        }
        if let rawDate = episode.premiereDate,
           let date = try? Date(rawDate, strategy: .iso8601) {
            facts.append(date.formatted(date: .abbreviated, time: .omitted))
        } else if let year = episode.productionYear { facts.append(String(year)) }
        if let rating = episode.officialRating ?? currentItem.officialRating { facts.append(rating) }
        let streams = episode.mediaStreams ?? []
        // These describe the source file, not the device's current HDMI/audio output.
        if let video = streams.first(where: { $0.type == "Video" }), let height = video.height, height > 0 {
            facts.append("\(height)\(video.isInterlaced == true ? "i" : "p")")
        }
        if let audio = streams.first(where: { $0.type == "Audio" && $0.isDefault == true })
            ?? streams.first(where: { $0.type == "Audio" }) {
            if let label = audio.displayTitle, !label.isEmpty { facts.append(label) }
            else if let codec = audio.codec { facts.append(codec.uppercased()) }
        }
        return facts
    }

    private var collectionActionRow: some View {
        HStack(spacing: 16) {
            Button { preparePlayback(forceTranscode: false) } label: {
                Label(isPreparingPlayback ? "Preparing…" : playLabel, systemImage: "play.fill")
                    .frame(minWidth: 216)
            }
            .buttonStyle(TVDetailTextActionStyle(primary: true))
            .disabled(isPreparingPlayback || selectedEpisode == nil || isLoadingSeason)
            .accessibilityIdentifier("playButton")
            .focused($focusedAction, equals: .play)
            TVWatchlistButton(item: currentItem, store: store)
                .focused($focusedAction, equals: .watchlist)
            TVDetailOverflowActions(
                item: currentItem, store: store,
                isLoading: isLoading || isPreparingPlayback,
                onUpdated: { refreshedItem = $0 },
                onCompatibilityPlayback: { preparePlayback(forceTranscode: true) }
            )
            .focused($focusedAction, equals: .more)
        }
        .focusSection()
    }

    private var playLabel: String {
        guard let episode = selectedEpisode, episode.resumeSeconds > 5 else { return "Play episode" }
        let total = Int(episode.resumeSeconds)
        return String(format: "Resume %d:%02d", total / 60, total % 60)
    }

    private var castMembers: [MediaPerson] {
        var seen = Set<String>()
        return (currentItem.people ?? []).filter {
            $0.type?.caseInsensitiveCompare("Actor") == .orderedSame && seen.insert($0.detailIdentity).inserted
        }
    }

    @ViewBuilder private var collectionContent: some View {
        if isLoading {
            ProgressView("Loading episodes…").frame(maxWidth: .infinity, minHeight: 220)
        } else if episodes.isEmpty, let errorMessage {
            TVMessageView(title: "Could not load episodes", message: errorMessage, actionTitle: "Try again") { Task { await load() } }
        } else {
            VStack(alignment: .leading, spacing: 18) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 18) {
                        ForEach(seasons) { season in
                            Button { selectSeason(season) } label: {
                                HStack(spacing: 10) {
                                    Text(season.name)
                                }
                            }
                            .buttonStyle(TVDetailTextActionStyle(selected: season.id == selectedSeasonID))
                            .accessibilityIdentifier("videoCollectionItem-\(season.id)")
                            .accessibilityAddTraits(season.id == selectedSeasonID ? .isSelected : [])
                        }
                    }
                    .padding(.horizontal, 82).padding(.vertical, 8)
                }
                .accessibilityIdentifier("collectionSeasonSelector")
                .focusSection()
                if isLoadingSeason {
                    ProgressView("Loading episodes…").frame(height: 200).frame(maxWidth: .infinity)
                } else if episodes.isEmpty {
                    Text("No episodes yet").padding(.horizontal, 82)
                } else {
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(alignment: .top, spacing: 30) {
                            ForEach(episodes) { episode in
                                Button {
                                    returnEpisodeID = episode.id
                                    onOpen(episode)
                                } label: { episodeCard(episode) }
                                    .buttonStyle(TVArtworkButtonStyle(aspect: 16 / 9))
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(width: 320, height: 248, alignment: .topLeading)
                                    .focused($focusedAction, equals: .episode(episode.id))
                                    .accessibilityIdentifier("videoCollectionItem-\(episode.id)")
                            }
                        }
                        .padding(.horizontal, 82).padding(.vertical, 16)
                    }
                    .frame(height: 280)
                    .scrollClipDisabled()
                    .accessibilityIdentifier("collectionEpisodeRail")
                    .focusSection()
                }
            }
        }
    }

    private func episodeCard(_ episode: MediaItem) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            TVArtwork(item: episode, store: store, aspect: 16 / 9)
                .frame(width: 320, height: 180)
                .overlay(alignment: .bottom) {
                    if episode.resumeSeconds > 0, episode.durationSeconds > 0 {
                        ProgressView(value: min(episode.resumeSeconds / episode.durationSeconds, 1))
                            .tint(LuminaTheme.gold).padding(.horizontal, 10).padding(.bottom, 8)
                    }
                }
            Text(episodeHeading(episode))
                .font(.system(size: 23, weight: .medium))
                .foregroundStyle(.white).lineLimit(2)
                .frame(height: 58, alignment: .topLeading)
        }
        .frame(width: 320, height: 248, alignment: .topLeading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(episodeHeading(episode))\(episode.userData?.played == true ? ", Watched" : "")")
    }

    private func preferredEpisode(in values: [MediaItem]) -> MediaItem? {
        values.first { $0.resumeSeconds > 0 && $0.userData?.played != true }
            ?? values.first { $0.userData?.played != true } ?? values.first
    }

    private func load() async {
        isLoading = episodes.isEmpty
        errorMessage = nil
        episodeDetails.removeAll()
        seasonEpisodes.removeAll()
        defer { isLoading = false }
        do {
            let loaded = try await store.loadItem(item)
            guard !Task.isCancelled else { return }
            refreshedItem = loaded
            seasons = item.type == "Series" ? try await store.loadChildren(of: item) : [loaded]
            guard !Task.isCancelled else { return }
            let preferred = (store.resumeItems + store.nextUpItems).first {
                $0.type == "Episode" && ($0.seriesID == item.id || $0.seasonID == item.id) && $0.userData?.played != true
            }
            let initialSeason = seasons.first { $0.id == selectedSeasonID }
                ?? seasons.first { $0.id == preferred?.seasonID } ?? seasons.first
            if let initialSeason {
                selectedSeasonID = initialSeason.id
                episodes = try await store.loadChildren(of: initialSeason)
                seasonEpisodes[initialSeason.id] = episodes
                guard !Task.isCancelled else { return }
                // When no Home continuation is cached, walk only as far as the first
                // season with an unwatched episode rather than loading every season.
                if preferred == nil && selectedEpisodeID == nil && episodes.allSatisfy({ $0.userData?.played == true }) {
                    for season in seasons where season.id != initialSeason.id {
                        let candidates = try await store.loadChildren(of: season)
                        seasonEpisodes[season.id] = candidates
                        guard !Task.isCancelled else { return }
                        if candidates.contains(where: { $0.userData?.played != true }) {
                            selectedSeasonID = season.id
                            episodes = candidates
                            break
                        }
                    }
                }
                selectedEpisodeID = episodes.first { $0.id == selectedEpisodeID }?.id
                    ?? episodes.first { $0.id == preferred?.id }?.id ?? preferredEpisode(in: episodes)?.id
            }
            detailRevision += 1
            isLoading = false
            let related = try? await store.relatedItems(for: loaded)
            guard !Task.isCancelled else { return }
            relatedItems = related?.filter { $0.id != loaded.id } ?? []
        } catch is CancellationError { return }
        catch { errorMessage = "Episodes could not load. Check your connection and try again." }
    }

    private func selectSeason(_ season: MediaItem) {
        guard selectedSeasonID != season.id || episodes.isEmpty else { return }
        seasonTask?.cancel()
        returnEpisodeID = nil
        cancelPlaybackPreparation()
        if let cached = seasonEpisodes[season.id] {
            isLoadingSeason = false
            selectedSeasonID = season.id
            episodes = cached
            selectedEpisodeID = preferredEpisode(in: cached)?.id
            return
        }
        let generation = UUID()
        seasonGeneration = generation
        isLoadingSeason = true
        errorMessage = nil
        seasonTask = Task {
            defer { if seasonGeneration == generation { isLoadingSeason = false; seasonTask = nil } }
            do {
                let loaded = try await store.loadChildren(of: season)
                guard !Task.isCancelled, seasonGeneration == generation else { return }
                seasonEpisodes[season.id] = loaded
                selectedSeasonID = season.id
                episodes = loaded
                selectedEpisodeID = preferredEpisode(in: loaded)?.id
            } catch is CancellationError { return }
            catch {
                guard !Task.isCancelled, seasonGeneration == generation else { return }
                errorMessage = "This season could not load. Try again."
            }
        }
    }

    private func loadEpisodeDetail() async {
        guard let selectedEpisodeID, let episode = episodes.first(where: { $0.id == selectedEpisodeID }) else { return }
        guard episodeDetails[selectedEpisodeID] == nil else { return }
        // Focus can move rapidly; the keyed task cancels superseded detail work.
        guard let detail = try? await store.loadItem(episode), !Task.isCancelled,
              self.selectedEpisodeID == selectedEpisodeID else { return }
        episodeDetails[selectedEpisodeID] = detail
    }

    private func preparePlayback(forceTranscode: Bool) {
        guard let episode = selectedEpisode, !isPreparingPlayback, !isLoadingSeason else { return }
        let preparationID = UUID()
        playbackPreparationID = preparationID
        isPreparingPlayback = true
        errorMessage = nil
        playbackPreparationTask = Task {
            defer { finishPlaybackPreparation(preparationID) }
            do {
                let request = try await store.preparePlayback(for: episode, forceTranscode: forceTranscode)
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                onPlayback(request)
            } catch is CancellationError { return }
            catch {
                guard !Task.isCancelled, playbackPreparationID == preparationID else { return }
                errorMessage = "Playback could not start. Try Convert for compatibility."
            }
        }
    }

    private func finishPlaybackPreparation(_ id: UUID) {
        guard playbackPreparationID == id else { return }
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
            .buttonStyle(TVDetailTextActionStyle())
            .tint(.white)
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
            .buttonStyle(TVDetailTextActionStyle())
            .tint(.white)
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

/// Detail actions defer to the artwork; an underline carries focus and season selection.
private struct TVDetailTextActionStyle: ButtonStyle {
    var primary = false
    var selected = false
    @Environment(\.isFocused) private var isFocused
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 27, weight: isFocused || primary || selected ? .semibold : .regular))
            .padding(.horizontal, 4)
            .frame(minWidth: 64, minHeight: 64)
            .foregroundStyle(.white.opacity(isFocused || primary ? 1 : 0.82))
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(.white.opacity(isFocused ? 1 : selected ? 0.5 : 0))
                    .frame(height: isFocused ? 3 : 1)
            }
            .opacity(isEnabled ? configuration.isPressed ? 0.65 : 1 : 0.4)
            .contentShape(.rect)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: isFocused)
    }
}
