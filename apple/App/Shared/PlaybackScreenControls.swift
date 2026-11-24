import AVKit
import AVFoundation
import Combine
import LuminaCore
import Observation
import SwiftUI
import MediaPlayer
#if canImport(UIKit)
import UIKit
#endif

extension PlaybackScreen {
    #if LUMINA_UI_TESTING
    private var systemArtworkProbeValue: String {
        let info = MPNowPlayingInfoCenter.default().nowPlayingInfo
        guard let mediaType = info?[MPNowPlayingInfoPropertyMediaType] as? NSNumber,
              mediaType.uintValue == MPNowPlayingInfoMediaType.audio.rawValue,
              let artwork = info?[MPMediaItemPropertyArtwork] as? MPMediaItemArtwork,
              let image = artwork.image(at: CGSize(width: 32, height: 32)),
              image.size.width > 0, image.size.height > 0, image.cgImage != nil else { return "Missing" }
        return info?[MPMediaItemPropertyTitle] as? String ?? "Missing title"
    }
    #endif

    var audioHorizontalInset: CGFloat {
        #if os(tvOS)
        80
        #else
        24
        #endif
    }
    var videoHorizontalInset: CGFloat {
        #if os(tvOS)
        64
        #else
        20
        #endif
    }
    var videoVerticalInset: CGFloat {
        #if os(tvOS)
        48
        #else
        16
        #endif
    }
    #if os(tvOS)
    var tvAudioControlBand: some View {
        VStack(spacing: 18) {
            Button("Queue", glyph: .queue) { queuePresented = true }
                .labelStyle(.iconOnly)
                .accessibilityValue(playback.queue.isEmpty ? "Empty" : "\(playback.queueIndex + 1) of \(playback.queue.count)")
                .accessibilityIdentifier("audioQueueButton")
                .disabled(playback.queue.isEmpty)
                .focused($focusedAudioControl, equals: .queue)
                .onMoveCommand { direction in
                    if direction == .up { focusedAudioControl = .artwork }
                }
            audioPlayPauseButton
                .focused($focusedAudioControl, equals: .playPause)
            audioRepeatButton
                .labelStyle(.iconOnly)
                .focused($focusedAudioControl, equals: .repeatMode)
            Button("Stop", glyph: .stop, variant: .solid) { stopAudio() }
                .labelStyle(.iconOnly)
                .accessibilityIdentifier("audioStopButton")
                .focused($focusedAudioControl, equals: .stop)
                .onMoveCommand { direction in
                    if direction == .down { focusedAudioControl = .timeline }
                }
        }
        .font(.title2)
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .focusSection()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioControlBand")
    }
    #endif

    var audioTimeline: some View {
        VStack(spacing: 8) {
            #if os(tvOS)
            ProgressView(value: min(playback.elapsedSeconds, max(1, playback.request.item.durationSeconds)), total: max(1, playback.request.item.durationSeconds))
                .tint(focusedAudioControl == .timeline ? .white : LuminaTheme.brand)
            #else
            MusicTimelineSlider(value: Binding(get: { playback.elapsedSeconds }, set: { playback.seek(to: $0) }), duration: playback.request.item.durationSeconds, onEditing: { editing in
                if editing {
                    audioIsScrubbing = true
                    revealAudioTimeline()
                } else {
                    finishAudioScrubbing()
                }
            })
                .frame(height: 44)
                .accessibilityLabel("Track position")
            #endif
            HStack {
                Text(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond)))
                Spacer()
                Text(Duration.seconds(playback.request.item.durationSeconds).formatted(.time(pattern: .minuteSecond)))
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.white.opacity(0.8))
        }
        #if os(tvOS)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(focusedAudioControl == .timeline ? .white.opacity(0.1) : .clear,
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .contentShape(.rect)
        .focusable()
        .focusEffectDisabled()
        .focused($focusedAudioControl, equals: .timeline)
        .onMoveCommand { direction in
            switch direction {
            case .left:
                playback.seek(to: playback.elapsedSeconds - 15)
            case .right:
                playback.seek(to: playback.elapsedSeconds + 15)
            case .up:
                focusedAudioControl = .artwork
            case .down:
                audioOptionsFocusRequest += 1
            default:
                break
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Track position")
        .accessibilityValue(
            "\(Duration.seconds(playback.elapsedSeconds).formatted(.time(pattern: .minuteSecond))) of "
                + Duration.seconds(playback.request.item.durationSeconds).formatted(.time(pattern: .minuteSecond))
        )
        .accessibilityAdjustableAction { direction in
            playback.seek(to: playback.elapsedSeconds + (direction == .increment ? 15 : -15))
        }
        #else
        .accessibilityElement(children: .contain)
        #endif
        .accessibilityIdentifier("audioTimeline")
    }

    @ViewBuilder var audioHeaderActions: some View {
        Button("Queue", glyph: .queue) { queuePresented = true }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("audioQueueButton")
        audioPlayPauseButton
        audioRepeatButton.labelStyle(.iconOnly)
        #if os(iOS)
        NativeMusicAirPlayButton(session: audioSession)
        NativeMusicCastButton(session: audioSession)
        #endif
        Button("Stop", glyph: .stop, variant: .solid) { stopAudio() }
            .labelStyle(.iconOnly)
            .accessibilityIdentifier("audioStopButton")
    }

    private var audioPlayPauseButton: some View {
        Button {
            if playback.hasFinished { replay() } else { playback.togglePlayback() }
        } label: {
            if playback.errorMessage != nil {
                Label("Retry", glyph: .sync)
            } else if playback.hasFinished {
                Label("Replay", systemImage: "arrow.counterclockwise")
            } else {
                Label(playback.isPlaying ? "Pause" : "Play",
                      glyph: playback.isPlaying ? .pause : .play, variant: .solid)
            }
        }
        .labelStyle(.iconOnly)
        .accessibilityIdentifier("audioPlayPauseButton")
        .disabled(playback.isVideoOutputUnavailable || playback.isReplaying || (playback.hasFinished && playback.isSwitchingVideoStream))
    }

    var audioRepeatButton: some View {
        Button(playback.repeatMode.label,
               glyph: playback.repeatMode == .one ? .repeatOne : .repeat) { playback.cycleRepeat() }
            .accessibilityValue(playback.repeatMode.label)
            .accessibilityIdentifier("audioRepeatButton")
    }

    func skipAudioTrack(offset: Int) {
        guard !playback.isReplaying, offset > 0 ? playback.hasNext : playback.hasPrevious else { return }
        Task { await playback.playQueued(offset: offset) }
    }

    var audioQueue: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Play queue").font(.title2.bold())
                Spacer()
                Button("Done") { queuePresented = false }
                    .accessibilityIdentifier("audioQueueDoneButton")
            }
            .padding(.horizontal)
            #if os(tvOS)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    audioQueueRows
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal)
                .padding(.vertical, 24)
            }
            .buttonStyle(LuminaQuietActionStyle())
            .focusSection()
            #else
            List {
                audioQueueRows
            }
            .listStyle(.plain)
            #endif
        }
        #if os(tvOS)
        .frame(width: 1000, height: 650)
        #endif
        .padding(.vertical)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioQueueScreen")
        #if os(tvOS)
        .onExitCommand { queuePresented = false }
        #endif
    }

    @ViewBuilder var audioQueueRows: some View {
        if audioSession.hasUpcomingRecommendations {
            Button("Remove upcoming recommendations") { audioSession.removeUpcomingRecommendations() }
                .disabled(!audioSession.canEditMixQueue)
        }
        ForEach(playback.queueRows) { row in
            let index = row.index
            let item = row.item
            HStack(spacing: 16) {
                Button {
                    Task { await playback.playOccurrence(row.id) }
                } label: {
                    HStack(spacing: 16) {
                        LuminaIcon(glyph: index == playback.queueIndex ? .volumeHigh : .music,
                                   variant: .solid, size: 20)
                        VStack(alignment: .leading) {
                            Text(item.name)
                            if audioSession.isRecommended(at: index) {
                                Text("Recommended").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .disabled(playback.isReplaying || audioSession.isSonos || audioSession.isRemoteOutput)
                .accessibilityIdentifier(row.accessibilityID)
                if index > playback.queueIndex {
                    Menu {
                        Button("Play next") { playback.moveOccurrence(row.id) }
                        Button("Move up") { playback.moveOccurrence(row.id, offset: -1) }
                            .disabled(index == playback.queueIndex + 1)
                        Button("Move down") { playback.moveOccurrence(row.id, offset: 1) }
                            .disabled(index == playback.queue.count - 1)
                        Button("Remove from queue", role: .destructive) { playback.removeOccurrence(row.id) }
                    } label: {
                        Label("Track actions", glyph: .moreHorizontal)
                    }
                    .labelStyle(.iconOnly)
                    .disabled(audioSession.isSonos || audioSession.isRemoteOutput)
                }
            }
        }
    }

    func finish() {
        if let onVideoDismiss { onVideoDismiss(); return }
        if !playback.request.item.isAudio { playback.close() }
        dismiss()
    }

    func finishAndReturnHome() {
        playback.close()
        finish()
        onReturnHome()
    }

    func finishAfterPresenceTimeout() {
        guard let onAutoplayStandby else { finishAndReturnHome(); return }
        playback.close()
        finish()
        onAutoplayStandby()
    }

    func stopAudio() {
        audioSession.stop()
        dismiss()
    }

    func replay() {
        Task { await playback.replay() }
    }


}
