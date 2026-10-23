import LuminaCore
import SwiftUI

/// A music-first detail surface that leaves the shared listening session running while people browse.
@MainActor
struct TVMusicDetailView: View {
    let item: MediaItem
    let store: LuminaStore
    let onOpen: (MediaItem) -> Void

    @Environment(NativeAudioSession.self) private var audioSession
    @State private var currentItem: MediaItem
    @State private var album: MediaItem?
    @State private var artist: MediaItem?
    @State private var albumTracks = [MediaItem]()
    @State private var artistAlbums = [MediaItem]()
    @State private var selectedTrackID: String?
    @State private var loading = true
    @State private var preparing = false
    @State private var errorMessage: String?
    @FocusState private var focusedControl: FocusTarget?

    private enum FocusTarget: Hashable {
        case play
        case shuffle
        case album
        case artist
        case track(String)
        case albumCard(String)
    }

    init(item: MediaItem, store: LuminaStore, onOpen: @escaping (MediaItem) -> Void) {
        self.item = item
        self.store = store
        self.onOpen = onOpen
        _currentItem = State(initialValue: item)
    }

    var body: some View {
        ScrollView {
            Group {
                if loading {
                    ProgressView("Loading music…")
                        .frame(maxWidth: .infinity, minHeight: 520)
                } else if currentItem.type == "MusicArtist" {
                    artistDetail
                } else {
                    albumOrTrackDetail
                }
            }
            .padding(.horizontal, 80)
            .padding(.top, 54)
            .padding(.bottom, 120)
        }
        .background(LuminaTheme.theater.ignoresSafeArea())
        .foregroundStyle(LuminaTheme.ink)
        .accessibilityIdentifier(currentItem.isAudio ? "detailScreen" : "musicCollectionScreen")
        .task(id: item.id) { await load() }
        .onPlayPauseCommand { handlePlayPause() }
    }

    private var albumOrTrackDetail: some View {
        HStack(alignment: .top, spacing: 72) {
            VStack(alignment: .leading, spacing: 24) {
                TVArtwork(item: artworkItem, store: store, aspect: 1)
                    .frame(width: 420, height: 420)

                VStack(alignment: .leading, spacing: 10) {
                    Text(currentItem.isAudio ? currentItem.name : (album?.name ?? currentItem.name))
                        .font(.system(size: currentItem.isAudio ? 38 : 42, weight: .bold))
                        .lineLimit(3)
                        .minimumScaleFactor(0.72)
                        .fixedSize(horizontal: false, vertical: true)
                    if let artistName, !artistName.isEmpty {
                        Text(artistName)
                            .font(.system(size: 25, weight: .regular))
                            .foregroundStyle(LuminaTheme.muted)
                            .lineLimit(2)
                    }
                    if let albumName = album?.name, currentItem.isAudio, albumName != currentItem.name {
                        Text(albumName)
                            .font(.system(size: 23))
                            .foregroundStyle(LuminaTheme.muted)
                            .lineLimit(2)
                    }
                }

                actionRow
                pivotActions
                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 23))
                        .foregroundStyle(LuminaTheme.spotlight)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("musicDetailError")
                }
            }
            .frame(width: 440, alignment: .leading)

            trackListing
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var artistDetail: some View {
        HStack(alignment: .top, spacing: 72) {
            VStack(alignment: .leading, spacing: 24) {
                TVArtwork(item: currentItem, store: store, aspect: 1)
                    .frame(width: 420, height: 420)
                Text(currentItem.name)
                    .font(.system(size: 42, weight: .bold))
                    .lineLimit(3)
                    .minimumScaleFactor(0.72)
                    .fixedSize(horizontal: false, vertical: true)
                if albumCount > 0 {
                    Text(albumCount == 1 ? "1 album" : "\(albumCount) albums")
                        .font(.system(size: 23))
                        .foregroundStyle(LuminaTheme.muted)
                }
                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 23))
                        .foregroundStyle(LuminaTheme.spotlight)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("musicDetailError")
                }
            }
            .frame(width: 440, alignment: .leading)

            VStack(alignment: .leading, spacing: 28) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Albums")
                        .font(.system(size: 34, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    if !artistAlbums.isEmpty {
                        Text("\(artistAlbums.count)")
                            .font(.system(size: 23))
                            .foregroundStyle(LuminaTheme.muted)
                    }
                }
                if artistAlbums.isEmpty {
                    emptyMusicContent(title: "No albums available", message: "This artist has no albums available right now.")
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220, maximum: 260), spacing: 32, alignment: .top)], spacing: 42) {
                        ForEach(artistAlbums) { album in
                            TVMusicAlbumCard(item: album, store: store) { onOpen(album) }
                                .focused($focusedControl, equals: .albumCard(album.id))
                                .accessibilityIdentifier("musicItem-\(album.id)")
                        }
                    }
                    .focusSection()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var actionRow: some View {
        HStack(spacing: 16) {
            Button {
                playSelectedTrack()
            } label: {
                Label(preparing ? "Preparing…" : primaryButtonLabel, systemImage: primaryButtonSymbol)
            }
            .buttonStyle(LuminaQuietActionStyle(primary: true))
            .disabled(preparing || albumTracks.isEmpty)
            .accessibilityIdentifier(currentItem.isAudio ? "playButton" : "musicPlayButton")
            .accessibilityValue(primaryButtonLabel)
            .focused($focusedControl, equals: .play)
            .defaultFocus($focusedControl, .play)

            if albumTracks.count > 1 {
                Button {
                    play(at: 0, shuffle: true)
                } label: {
                    Label("Shuffle", systemImage: "shuffle")
                }
                .buttonStyle(LuminaQuietActionStyle())
                .disabled(preparing)
                .accessibilityIdentifier("musicShuffleButton")
                .focused($focusedControl, equals: .shuffle)
            }
        }
        .accessibilityIdentifier(currentItem.isAudio ? "musicTrackActions" : "musicAlbumActions")
        .focusSection()
    }

    @ViewBuilder private var pivotActions: some View {
        if album != nil || artist != nil {
            HStack(spacing: 16) {
                if let album, currentItem.isAudio {
                    Button("Album", systemImage: "square.stack") { onOpen(album) }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("musicAlbumButton")
                        .focused($focusedControl, equals: .album)
                }
                if let artist {
                    Button("Artist", systemImage: "person") { onOpen(artist) }
                        .buttonStyle(LuminaQuietActionStyle())
                        .accessibilityIdentifier("musicArtistButton")
                        .focused($focusedControl, equals: .artist)
                }
            }
            .focusSection()
        }
    }

    private var trackListing: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .firstTextBaseline) {
                Text(album?.name ?? "Track listing")
                    .font(.system(size: 34, weight: .semibold))
                    .lineLimit(2)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                if !albumTracks.isEmpty {
                    Text(albumTracks.count == 1 ? "1 track" : "\(albumTracks.count) tracks")
                        .font(.system(size: 23))
                        .foregroundStyle(LuminaTheme.muted)
                }
            }

            if albumTracks.isEmpty {
                emptyMusicContent(title: "No tracks available", message: "Try refreshing this album.")
            } else {
                LazyVStack(spacing: 6) {
                    ForEach(albumTracks) { track in
                        Button {
                            play(trackID: track.id)
                        } label: {
                            TVMusicTrackRow(
                                track: track,
                                isCurrent: isCurrentTrack(track),
                                duration: formattedDuration(track.durationSeconds)
                            )
                        }
                        .buttonStyle(LuminaQuietActionStyle(selected: isCurrentTrack(track)))
                        .focused($focusedControl, equals: .track(track.id))
                        .accessibilityLabel(trackAccessibilityLabel(track))
                        .accessibilityValue(isCurrentTrack(track) ? (audioSession.playback?.isPlaying == true ? "Playing" : "Paused") : "")
                        .accessibilityIdentifier("musicItem-\(track.id)")
                    }
                }
                .focusSection()
            }
        }
    }

    private var artworkItem: MediaItem { album ?? currentItem }
    private var albumCount: Int { currentItem.childCount ?? artistAlbums.count }
    private var artistName: String? { currentItem.artists?.joined(separator: ", ") ?? currentItem.albumArtist ?? artist?.name }
    private var playLabel: String { currentItem.isAudio ? "Play song" : "Play album" }
    private var controlsCurrentAlbum: Bool {
        sharesActiveAlbumQueue && audioSession.playback != nil
    }
    private var primaryButtonLabel: String {
        guard controlsCurrentAlbum else { return playLabel }
        return audioSession.playback?.isPlaying == true ? "Pause" : "Resume"
    }
    private var primaryButtonSymbol: String {
        guard !preparing else { return "play.fill" }
        return controlsCurrentAlbum && audioSession.playback?.isPlaying == true ? "pause.fill" : "play.fill"
    }

    private func emptyMusicContent(title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.system(size: 26, weight: .semibold))
            Text(message).font(.system(size: 23)).foregroundStyle(LuminaTheme.muted)
            Button("Try again", systemImage: "arrow.clockwise") { Task { await load() } }
                .buttonStyle(LuminaQuietActionStyle())
        }
        .frame(maxWidth: .infinity, minHeight: 240, alignment: .leading)
        .focusSection()
    }

    private func load() async {
        loading = true
        preparing = false
        errorMessage = nil
        album = nil
        artist = nil
        albumTracks = []
        artistAlbums = []
        selectedTrackID = nil
        defer { loading = false }

        do {
            let loaded = try await store.loadItem(item)
            guard !Task.isCancelled else { return }
            currentItem = loaded

            switch loaded.type {
            case "MusicArtist":
                artistAlbums = try await store.musicChildren(of: loaded)
                guard !Task.isCancelled else { return }
                if let first = artistAlbums.first { focusedControl = .albumCard(first.id) }
            case "MusicAlbum":
                album = loaded
                albumTracks = try await store.musicChildren(of: loaded)
                guard !Task.isCancelled else { return }
                loading = false
                await resolveArtist(for: albumTracks.first)
            case "Audio":
                // A track remains playable even if its optional album/artist pivots cannot load.
                selectedTrackID = loaded.id
                albumTracks = [loaded]
                guard let track = try? await store.music.track(for: loaded), !Task.isCancelled else { return }
                if let albumID = track.albumID,
                   let resolvedAlbum = try? await store.musicCollection(id: albumID, expectedType: "MusicAlbum"),
                   !Task.isCancelled {
                    album = resolvedAlbum
                    if let resolvedTracks = try? await store.musicChildren(of: resolvedAlbum),
                       !Task.isCancelled, !resolvedTracks.isEmpty {
                        albumTracks = resolvedTracks
                    }
                }
                if let artistID = track.artistID {
                    artist = try? await store.musicCollection(id: artistID, expectedType: "MusicArtist")
                    guard !Task.isCancelled else { return }
                }
            default:
                errorMessage = "This music item is unavailable."
            }
            guard !Task.isCancelled else { return }
            if let selectedTrackID, albumTracks.contains(where: { $0.id == selectedTrackID }) {
                focusedControl = .track(selectedTrackID)
            }
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "This music collection could not load. Check your connection and try again."
        }
    }

    private func resolveArtist(for track: MediaItem?) async {
        guard let track else { return }
        guard let metadata = try? await store.music.track(for: track), let artistID = metadata.artistID,
              !Task.isCancelled else { return }
        artist = try? await store.musicCollection(id: artistID, expectedType: "MusicArtist")
        guard !Task.isCancelled else { return }
    }

    private func handlePlayPause() {
        if audioSession.playback != nil {
            Task { await audioSession.toggle() }
        } else {
            playSelectedTrack()
        }
    }

    private func playSelectedTrack() {
        guard !albumTracks.isEmpty else { return }
        if controlsCurrentAlbum {
            Task { await audioSession.toggle() }
            return
        }
        let index = selectedTrackID.flatMap { id in albumTracks.firstIndex { $0.id == id } } ?? 0
        play(at: index, shuffle: false)
    }

    private func play(trackID: String) {
        guard let index = albumTracks.firstIndex(where: { $0.id == trackID }) else { return }
        selectedTrackID = trackID
        play(at: index, shuffle: false)
    }

    private func play(at index: Int, shuffle: Bool) {
        guard albumTracks.indices.contains(index), !preparing else { return }
        if !shuffle, sharesActiveAlbumQueue {
            Task { await audioSession.select(index) }
            return
        }

        preparing = true
        errorMessage = nil
        Task {
            defer { preparing = false }
            do {
                let tracks = try await store.music.resolve(items: albumTracks)
                guard !Task.isCancelled, tracks.indices.contains(index) else { return }
                _ = try await audioSession.play(tracks: tracks, index: index, store: store, shuffle: shuffle)
            } catch is CancellationError {
                return
            } catch {
                errorMessage = "This album could not start. Check the connection and try again."
            }
        }
    }

    private var sharesActiveAlbumQueue: Bool {
        guard audioSession.tracks.count == albumTracks.count, !albumTracks.isEmpty else { return false }
        return zip(audioSession.tracks, albumTracks).allSatisfy { existing, displayed in
            existing.mediaItem.id == displayed.id
        }
    }

    private func isCurrentTrack(_ track: MediaItem) -> Bool {
        audioSession.playback?.request.item.id == track.id
    }

    private func trackAccessibilityLabel(_ track: MediaItem) -> String {
        let number = track.indexNumber.map { "Track \($0), " } ?? ""
        return number + track.name + (track.durationSeconds > 0 ? ", \(formattedDuration(track.durationSeconds))" : "")
    }

    private func formattedDuration(_ seconds: Double) -> String {
        let value = max(0, Int(seconds.rounded()))
        return value >= 3600
            ? String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
            : String(format: "%d:%02d", value / 60, value % 60)
    }
}

private struct TVMusicTrackRow: View {
    let track: MediaItem
    let isCurrent: Bool
    let duration: String
    @Environment(\.isFocused) private var isFocused

    var body: some View {
        HStack(spacing: 20) {
            Text(track.indexNumber.map(String.init) ?? "–")
                .font(.system(size: 23, weight: .medium, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(isFocused ? Color.black.opacity(0.7) : isCurrent ? LuminaTheme.gold : LuminaTheme.muted)
                .frame(width: 48, alignment: .trailing)
            VStack(alignment: .leading, spacing: 4) {
                Text(track.name)
                    .font(.system(size: 25, weight: isCurrent ? .semibold : .regular))
                    .lineLimit(1)
                if let subtitle = track.displaySubtitle.nilIfEmpty {
                    Text(subtitle)
                        .font(.system(size: 23))
                        .foregroundStyle(isFocused ? Color.black.opacity(0.7) : LuminaTheme.muted)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if isCurrent {
                Image(systemName: "speaker.wave.2.fill")
                    .foregroundStyle(isFocused ? Color.black : LuminaTheme.gold)
                    .accessibilityHidden(true)
            }
            Text(duration)
                .font(.system(size: 23, design: .monospaced))
                .foregroundStyle(isFocused ? Color.black.opacity(0.7) : LuminaTheme.muted)
                .frame(width: 80, alignment: .trailing)
        }
        .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
    }
}

private struct TVMusicAlbumCard: View {
    let item: MediaItem
    let store: LuminaStore
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                TVArtwork(item: item, store: store, aspect: 1)
                Text(item.name)
                    .font(.system(size: 23, weight: .medium))
                    .lineLimit(2)
                Text(item.albumArtist ?? item.displaySubtitle)
                    .font(.system(size: 23))
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: 1))
        .accessibilityLabel("Open album \(item.name)")
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
