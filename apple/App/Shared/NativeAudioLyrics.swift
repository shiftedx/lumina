import LuminaCore
import SwiftUI

struct NativeAudioLyrics: View {
    let session: NativeAudioSession
    let playback: PlaybackModel
    var prominent: Bool = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let lyrics = session.lyrics, !lyrics.cues.isEmpty {
                cueLyrics(lyrics.cues)
            } else if let plain = session.lyrics?.plain,
                      !plain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                plainLyrics(plain)
            } else {
                Text(statusMessage)
                    .accessibilityLabel(statusMessage)
            }
            if let source = session.lyrics?.source {
                Text(source).font(.caption).foregroundStyle(.secondary)
            }
            if session.lyrics?.error != nil || session.lyricsError != nil {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Button("Retry lyrics") { session.loadLyrics(retry: true) }
                        .accessibilityIdentifier("audioLyricsRetryButton")
                        .disabled(session.lyricsLoading || (session.retryAllowedAt.map { $0 > context.date } ?? false))
                }
            }
        }
        .padding(.vertical, 8)
        .frame(maxHeight: prominent ? .infinity : nil, alignment: .topLeading)
        .accessibilityIdentifier("audioLyricsContent")
    }

    @ViewBuilder
    private func cueLyrics(_ cues: [MusicCue]) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: prominent ? 20 : 12) {
                    ForEach(Array(cues.enumerated()), id: \.offset) { index, cue in
                        Button { playback.seek(to: cue.start) } label: {
                            Text(cue.text)
                                .font(cueFont(index))
                                .foregroundStyle(cueColor(index))
                                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                .padding(.vertical, prominent ? 10 : 6)
                                .contentShape(.rect)
                        }
                        #if !os(tvOS)
                        .buttonStyle(.plain)
                        #endif
                        .id(index)
                        .accessibilityIdentifier("audioLyricCue-\(index)")
                        .accessibilityAddTraits(index == activeCue ? .isSelected : [])
                    }
                }
                .padding(.vertical, prominent ? 24 : 0)
            }
            .frame(maxHeight: prominent ? .infinity : 300)
            .onAppear {
                if prominent {
                    scroll(to: activeCue, with: proxy)
                }
            }
            .onChange(of: activeCue) { _, cue in scroll(to: cue, with: proxy) }
        }
        .id(playback.request.item.id)
    }

    @ViewBuilder
    private func plainLyrics(_ plain: String) -> some View {
        if prominent {
            ScrollView {
                Text(plain)
                    .font(.largeTitle.bold())
                    .foregroundStyle(.white.opacity(0.88))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 24)
            }
            .frame(maxHeight: .infinity)
        } else {
            Text(plain).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var activeCue: Int? {
        session.lyrics?.cues.lastIndex { $0.start <= playback.elapsedSeconds }
    }

    private var statusMessage: String {
        session.lyrics?.instrumental == true ? "Instrumental" : session.lyrics?.error
            ?? session.lyricsError
            ?? (session.lyricsLoading ? "Finding lyrics…" : "Lyrics are not available for this recording yet.")
    }

    private func cueFont(_ index: Int) -> Font {
        prominent ? .largeTitle.bold() : LuminaTheme.editorial(index == activeCue ? 28 : 24, relativeTo: .body)
    }

    private func cueColor(_ index: Int) -> Color {
        if prominent { return index == activeCue ? .white : .white.opacity(0.72) }
        return index == activeCue ? LuminaTheme.accentInk : .white.opacity(0.75)
    }

    private func scroll(to cue: Int?, with proxy: ScrollViewProxy) {
        guard let cue else { return }
        if !prominent || reduceMotion {
            proxy.scrollTo(cue, anchor: .center)
        } else {
            withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(cue, anchor: .center) }
        }
    }
}
