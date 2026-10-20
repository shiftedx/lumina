import LuminaCore
import SwiftUI

struct NativeAudioOptions: View {
    @Bindable var session: NativeAudioSession
    let playback: PlaybackModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if session.conflict != nil {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Your saved queue differs from the server. Choose which queue to keep.")
                    HStack {
                        Button("Use server") { Task { await session.useServerSession() } }.accessibilityIdentifier("audioUseServerButton")
                        Button("Keep this device") { Task { await session.keepDeviceSession() } }.accessibilityIdentifier("audioKeepDeviceButton")
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.vertical, 20)
            }
            NativeAudioDisclosure("Listening options", identifier: "audioListeningOptions") {
                VStack(alignment: .leading, spacing: 16) {
                    Text(session.isSonos ? "Sonos output" : session.decodedPlayback ? "Gapless buffer · 48 kHz" : "Streaming source audio")
                        .font(.caption).foregroundStyle(.secondary)
                    if let sourceFormat {
                        Text(sourceFormat).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("audioSourceFormat")
                    }
                    Toggle("Normalize loudness", isOn: $session.normalization)
                        .accessibilityIdentifier("audioNormalizeToggle")
                        .disabled(session.isSonos || !session.decodedPlayback || !hasLoudnessMetadata)
                    if !hasLoudnessMetadata {
                        Text("Loudness metadata is not available for this recording yet.").font(.caption)
                    }
                    if !session.decodedPlayback && !session.isSonos {
                        Text("Streaming preserves source loudness. Normalization and crossfade use buffered playback.")
                            .font(.caption)
                    }
                    Text("Crossfade").font(.subheadline).foregroundStyle(.secondary)
                    Picker("Crossfade", selection: $session.crossfadeSeconds) {
                        Text("Off").tag(0.0)
                        Text("3 seconds").tag(3.0)
                        Text("6 seconds").tag(6.0)
                        Text("12 seconds").tag(12.0)
                    }
                    .pickerStyle(.menu)
                    .disabled(session.isSonos || !session.decodedPlayback)
                    .accessibilityIdentifier("audioCrossfadePicker")
                    Menu {
                        Button("Off") { session.setSleep(minutes: nil) }
                        Button("15 minutes") { session.setSleep(minutes: 15) }
                        Button("30 minutes") { session.setSleep(minutes: 30) }
                        Button("1 hour") { session.setSleep(minutes: 60) }
                    } label: {
                        if let until = session.sleepUntil {
                            Label("Sleep timer: \(until, style: .relative)", systemImage: "moon.zzz")
                        } else { Label("Sleep timer: Off", systemImage: "moon.zzz") }
                    }
                    .disabled(session.isSonos)
                    .accessibilityIdentifier("audioSleepTimerButton")
                    if session.isSonos {
                        Text("Normalization, crossfade, repeat and sleep timer stay on this device and are unavailable on Sonos.")
                            .font(.caption)
                    }
                }
                .padding(.vertical, 8)
            }
            NativeAudioDisclosure("Lyrics", identifier: "audioLyrics") { lyrics }
            NativeAudioDisclosure("Audio output", identifier: "audioOutput") { output }
            if !session.pendingEntries.isEmpty {
                NativeAudioDisclosure("Requested albums", identifier: "audioRequestedAlbums") {
                    Picker("When unavailable", selection: Binding(get: { session.snapshot.unavailablePolicy }, set: session.setUnavailablePolicy)) {
                        Text("Skip it and continue").tag("skip")
                        Text("Wait here").tag("wait")
                    }
                    .pickerStyle(.menu)
                    .disabled(session.isSonos)
                    ForEach(Array(session.pendingEntries.enumerated()), id: \.offset) { _, entry in
                        VStack(alignment: .leading) {
                            Text(entry.name ?? entry.title ?? "Requested album")
                            Text(entry.message ?? entry.state ?? entry.status ?? "Waiting for import").font(.caption)
                        }
                    }
                    Button("Refresh requested albums") { Task { await session.refreshRequestedAlbums() } }
                    Text("Refreshing never starts playback. Choose Play after an album appears.").font(.caption)
                }
            }
            NativeAudioDisclosure("Download music", identifier: "audioDownloads") {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Current track").font(.subheadline).foregroundStyle(.secondary)
                        NativeDownloadControls(items: [playback.request.item], store: playback.store)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Album").font(.subheadline).foregroundStyle(.secondary)
                        NativeDownloadControls(items: albumItems, store: playback.store)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Queue").font(.subheadline).foregroundStyle(.secondary)
                        NativeDownloadControls(items: playback.queue, store: playback.store)
                    }
                }
            }
            Button("Refresh shared queue", systemImage: "arrow.clockwise") { Task { await session.refreshSharedQueue() } }
                .font(.callout)
                .padding(.vertical, 14)
                .accessibilityIdentifier("audioRefreshSharedQueueButton")
            if let message = session.message {
                Text(message).font(.callout).foregroundStyle(.white.opacity(0.8))
                    .padding(.bottom, 16).accessibilityIdentifier("audioSessionMessage")
            }
        }
        #if os(tvOS)
        .buttonStyle(.bordered)
        #else
        .buttonStyle(.plain)
        #endif
        .tint(LuminaTheme.spotlight)
    }

    private var albumItems: [MediaItem] {
        guard let track = session.tracks[safe: playback.queueIndex] else { return [] }
        return session.tracks.filter { entry in
            if let album = track.albumID { return entry.albumID == album }
            return entry.album == track.album && entry.artist == track.artist
        }.map(\.mediaItem)
    }

    private var lyrics: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let value = session.lyrics, !value.cues.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            ForEach(Array(value.cues.enumerated()), id: \.offset) { index, cue in
                                Button { playback.seek(to: cue.start) } label: {
                                    Text(cue.text)
                                        .font(LuminaTheme.editorial(index == activeCue ? 28 : 24, relativeTo: .body))
                                        .foregroundStyle(index == activeCue ? LuminaTheme.spotlight : .white.opacity(0.75))
                                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                        .padding(.vertical, 6)
                                }
                                #if !os(tvOS)
                                .buttonStyle(.plain)
                                #endif
                                .id(index)
                                .accessibilityAddTraits(index == activeCue ? .isSelected : [])
                            }
                        }
                    }
                    .frame(maxHeight: 300)
                    .onChange(of: activeCue) { _, cue in if let cue { proxy.scrollTo(cue, anchor: .center) } }
                }
            } else if let plain = session.lyrics?.plain, !plain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text(plain).fixedSize(horizontal: false, vertical: true)
            } else {
                Text(session.lyrics?.instrumental == true ? "Instrumental" : session.lyrics?.error ?? session.lyricsError ?? (session.lyricsLoading ? "Finding lyrics…" : "Lyrics are not available for this recording yet."))
            }
            if let source = session.lyrics?.source { Text(source).font(.caption).foregroundStyle(.secondary) }
            if session.lyrics?.error != nil || session.lyricsError != nil {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Button("Retry lyrics") { session.loadLyrics(retry: true) }
                        .accessibilityIdentifier("audioLyricsRetryButton")
                        .disabled(session.lyricsLoading || (session.retryAllowedAt.map { $0 > context.date } ?? false))
                }
            }
        }
        .padding(.vertical, 8)
    }
    private var activeCue: Int? { session.lyrics?.cues.lastIndex(where: { $0.start <= playback.elapsedSeconds }) }

    private var sourceFormat: String? {
        guard let track = session.tracks[safe: playback.queueIndex] else { return nil }
        let parts = [track.codec?.uppercased(), track.sampleRate.map { "\($0.formatted()) Hz" },
                     track.bitDepth.map { "\($0)-bit" }, track.channels.map { "\($0) " + ($0 == 1 ? "channel" : "channels") }].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
    private var hasLoudnessMetadata: Bool {
        guard let track = session.tracks[safe: playback.queueIndex] else { return false }
        return track.gainDB != nil || track.albumGainDB != nil
    }
    private var outputVolume: Float {
        session.isSonos ? session.remoteVolumeDraft ?? Float(session.outputStatus?.volume ?? 50) / 100 : session.volume
    }
    private var output: some View {
        VStack(alignment: .leading, spacing: 16) {
            Button(session.isSonos ? "Return playback to this device" : "This device") {
                if session.isSonos { Task { await session.returnHere() } }
            }
            .disabled(session.busy || !session.isSonos)
            .accessibilityIdentifier("audioReturnHereButton")
            #if os(tvOS)
            HStack {
                Button("Volume down") { Task { await session.setOutputVolume(outputVolume - 0.1) } }
                Text("\(Int(outputVolume * 100))%")
                Button("Volume up") { Task { await session.setOutputVolume(outputVolume + 0.1) } }
            }
            #else
            Slider(value: Binding(get: { outputVolume }, set: { value in Task { await session.setOutputVolume(value) } }), in: 0...1)
                .accessibilityLabel("Music volume")
            #endif
            if session.pendingSpeakerStop != nil {
                Button("Retry stopping speaker") { Task { await session.retryStoppingSpeaker() } }
            }
            Toggle("Compatibility mode for Sonos S1", isOn: $session.compatibility)
            Button(session.busy ? "Looking for speakers…" : "Refresh Sonos speakers") { Task { await session.discoverSpeakers() } }
                .disabled(session.busy)
                .accessibilityIdentifier("audioRefreshSpeakersButton")
            ForEach(session.speakers) { speaker in
                Button { Task { await session.cast(to: speaker) } } label: {
                    VStack(alignment: .leading) {
                        Text(speaker.name)
                        Text("\(speaker.model) · \(session.compatibility ? "16-bit compatibility" : "up to 24-bit / 48 kHz")").font(.caption)
                    }
                }
                .disabled(session.busy || session.tracks.isEmpty)
            }
            Text("Sonos receives at most the next 100 tracks. The local player pauses after the speaker acknowledges the transfer.")
                .font(.caption)
        }
        .padding(.vertical, 8)
    }
}

/// Keep the expansion action and accessibility identity on the heading button alone.
private struct NativeAudioDisclosure<Content: View>: View {
    let title: String
    let identifier: String
    let content: Content
    @State private var expanded = false

    init(_ title: String, identifier: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.identifier = identifier
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { expanded.toggle() } label: {
                HStack {
                    Text(title).font(.body.weight(.medium))
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.caption)
                }
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(.rect)
            }
            #if os(tvOS)
            .buttonStyle(.bordered)
            .padding(.vertical, 12)
            #else
            .buttonStyle(.plain)
            .padding(.vertical, 4)
            #endif
            .accessibilityIdentifier(identifier)
            .accessibilityValue(expanded ? "Expanded" : "Collapsed")
            if expanded { content.padding(.top, 12).padding(.bottom, 24).buttonStyle(.bordered) }
            Rectangle().fill(.white.opacity(0.14)).frame(height: 1).accessibilityHidden(true)
        }
    }
}

private extension Array {
    subscript(safe index: Index) -> Element? { indices.contains(index) ? self[index] : nil }
}
