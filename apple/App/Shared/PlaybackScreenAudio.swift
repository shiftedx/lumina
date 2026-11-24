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
    #if os(tvOS)
    var audioStage: some View {
        GeometryReader { geometry in
            ZStack(alignment: .trailing) {
                ScrollView {
                    VStack(spacing: 0) {
                        ZStack(alignment: .bottom) {
                            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 2000)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Color.black.overlay {
                                    LuminaIcon(glyph: .music, variant: .solid, size: 40)
                                }
                            }
                            .frame(width: geometry.size.width, height: geometry.size.height)
                            .clipped()
                            .accessibilityHidden(true)

                            Button { playback.togglePlayback() } label: {
                                Color.clear
                                    .frame(width: geometry.size.width, height: geometry.size.height)
                                    .contentShape(.rect)
                            }
                            .buttonStyle(.plain)
                            .focusEffectDisabled()
                            .focused($focusedAudioControl, equals: .artwork)
                            .defaultFocus($focusedAudioControl, .artwork)
                            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
                            .accessibilityValue(playback.request.item.name)
                            .accessibilityIdentifier("audioAlbumArtwork")
                            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
                            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
                            .onMoveCommand { direction in
                                switch direction {
                                case .left:
                                    skipAudioTrack(offset: -1)
                                case .right:
                                    skipAudioTrack(offset: 1)
                                case .down:
                                    focusedAudioControl = playback.queue.isEmpty ? .playPause : .queue
                                default:
                                    break
                                }
                            }

                            VStack(alignment: .leading, spacing: 12) {
                                audioTimeline
                            }
                            .padding(.horizontal, audioHorizontalInset)
                            .padding(.bottom, 60)
                            .background {
                                LinearGradient(colors: [.clear, .black.opacity(0.88)], startPoint: .top, endPoint: .bottom)
                                    .frame(height: 220)
                                    .frame(maxHeight: .infinity, alignment: .bottom)
                            }
                        }
                        .frame(width: geometry.size.width, height: geometry.size.height)

                        VStack(alignment: .leading, spacing: 32) {
                            VStack(alignment: .leading, spacing: 10) {
                                Text(playback.request.item.name)
                                    .font(TVTypography.pageTitle)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .accessibilityIdentifier("nowPlayingTitle")
                                Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                    .font(TVTypography.body)
                                    .foregroundStyle(.white.opacity(0.9))
                                if let album = playback.request.item.album {
                                    Text(album)
                                        .font(TVTypography.metadata)
                                        .foregroundStyle(.white.opacity(0.65))
                                }
                            }
                            Text(playback.request.methodLabel)
                                .font(TVTypography.metadata)
                                .foregroundStyle(.white.opacity(0.6))
                                .accessibilityIdentifier("audioPlaybackMethod")
                            NativeAudioOptions(session: audioSession, playback: playback,
                                               focusLyricsRequest: audioOptionsFocusRequest)
                        }
                        .frame(maxWidth: 1120, alignment: .leading)
                        .padding(.horizontal, audioHorizontalInset)
                        .padding(.vertical, 60)
                        .frame(maxWidth: .infinity, alignment: .top)
                        .background(LuminaTheme.theater)
                        .focusSection()
                    }
                }
                .scrollIndicators(.hidden)
                .scrollClipDisabled()
                tvAudioControlBand
                    .padding(.trailing, audioHorizontalInset)
            }
        }
        .ignoresSafeArea()
        .foregroundStyle(.white)
        .accessibilityIdentifier("audioPlayerStage")
        .onAppear { focusedAudioControl = .artwork }
        .onChange(of: queuePresented) { _, presented in
            if !presented { focusedAudioControl = .queue }
        }
    }

    #else
    var audioStage: some View {
        GeometryReader { geometry in
            if expandedAudioFits(geometry.size) {
                expandedAudioStage(in: geometry)
            } else if usesStandardPhonePushedChrome {
                phoneAudioStage(in: geometry)
            } else {
                compactAudioStage
            }
        }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { audioStageSize = $0 }
    }

    private func expandedAudioFits(_ size: CGSize) -> Bool {
        size.width >= 640 && size.height >= 560 && !dynamicTypeSize.isAccessibilitySize
    }

    var usesExpandedAudioLayout: Bool { expandedAudioFits(audioStageSize) }

    private func phoneAudioStage(in geometry: GeometryProxy) -> some View {
        let top: CGFloat = 8
        // Reserve room for metadata and transport before assigning space to the cover.
        let side = max(120, min(geometry.size.width - 48, geometry.size.height - top - 444))
        return ScrollView {
            VStack(spacing: 0) {
                VStack(spacing: 12) {
                    HStack {
                        Button { finish() } label: {
                            LuminaIcon(glyph: .chevronDown, size: 24)
                                .frame(width: 44, height: 44).contentShape(.rect)
                        }
                        .accessibilityLabel("Close player")
                        .accessibilityIdentifier("closePlayerButton")
                        Spacer()
                        Text("Now playing").font(.subheadline.weight(.medium))
                            .foregroundStyle(.white.opacity(0.7))
                        Spacer()
                        audioOptionsMenu
                    }
                    CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 1_600)) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        LuminaTheme.theater.overlay { LuminaIcon(glyph: .music, size: 64) }
                    }
                    .frame(width: side, height: side)
                    .clipShape(.rect(cornerRadius: 12))
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
                    .accessibilityValue(playback.request.item.name)
                    .accessibilityIdentifier("audioAlbumArtwork")
                    .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
                    .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
                    .gesture(AudioArtworkSwipe(onSkip: skipAudioTrack))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(playback.request.item.name)
                            .font(LuminaTheme.heading(22, relativeTo: .title2).weight(.medium))
                            .accessibilityIdentifier("nowPlayingTitle")
                        Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                            .font(.body).foregroundStyle(.white.opacity(0.8))
                        if let album = playback.request.item.album {
                            Text(album).font(.subheadline).foregroundStyle(.white.opacity(0.6))
                        }
                    }
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    audioQualityReadout
                    audioTimeline
                    phoneAudioTransport
                }
                .frame(minHeight: max(0, geometry.size.height - top - 16), alignment: .top)
                .padding(.top, top)
                .padding(.bottom, 16)
                VStack(alignment: .leading, spacing: 16) {
                    if let error = displayedError ?? playback.syncError {
                        Text(error).font(.callout)
                    }
                    Text(playback.request.methodLabel).font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("audioPlaybackMethod")
                    NativeAudioOptions(session: audioSession, playback: playback)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 16)
            }
            .padding(.horizontal, 24)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            phoneAudioUtilities
                .padding(.horizontal, 24)
                .padding(.top, 32)
                .padding(.bottom, 20)
                .background(LuminaTheme.theater)
        }
        .scrollBounceBehavior(.basedOnSize)
        .foregroundStyle(.white)
        .buttonStyle(.plain)
        .accessibilityIdentifier("audioPlayerStage")
        .sheet(isPresented: $audioOptionsPresented) { audioOptionsSheet }
        .sheet(isPresented: $audioQualityPresented) { audioQualitySheet }
        .sheet(isPresented: $phoneLyricsPresented) {
            NavigationStack {
                NativeAudioLyrics(session: audioSession, playback: playback, prominent: true)
                    .padding(24)
                    .background(LuminaTheme.theater)
                    .navigationTitle("Lyrics")
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { phoneLyricsPresented = false } } }
            }
            .preferredColorScheme(.dark)
        }
    }

    private var phoneAudioTransport: some View {
        HStack(spacing: 28) {
            Spacer(minLength: 0)
            audioTrackTransportButtons
            Spacer(minLength: 0)
        }
        .frame(height: 64)
    }

    private var phoneAudioUtilities: some View {
        HStack(spacing: 0) {
            Button { phoneLyricsPresented = true } label: {
                LuminaIcon(glyph: .subtitles).frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Lyrics")
            .accessibilityIdentifier("audioLyricsButton")
            Spacer(minLength: 0)
            audioRepeatButton.labelStyle(.iconOnly).frame(width: 44, height: 44)
            Spacer(minLength: 0)
            Button { queuePresented = true } label: {
                LuminaIcon(glyph: .queue).frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Queue")
            .accessibilityIdentifier("audioQueueButton")
            Spacer(minLength: 0)
            NativeMusicAirPlayButton(session: audioSession)
            Spacer(minLength: 0)
            NativeMusicCastButton(session: audioSession)
        }
        .frame(height: 44)
        .foregroundStyle(.white.opacity(0.75))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioUtilityRow")
    }

    private var audioQualityReadout: some View {
        Button { audioQualityPresented = true } label: {
            Text(activeAudioQualityLabel)
                .font(.caption).multilineTextAlignment(.center)
                .foregroundStyle(.white.opacity(0.75))
                .frame(maxWidth: .infinity, minHeight: 32)
                .contentShape(.rect)
        }
        .accessibilityLabel("Playback quality")
        .accessibilityValue(activeAudioQualityLabel)
        .accessibilityIdentifier("audioQualityButton")
    }

    private var activeAudioQualityLabel: String {
        if audioSession.isSonos || audioSession.isRemoteOutput { return "Remote output · Quality details" }
        guard let quality = audioSession.playbackQuality else { return "Reading playback format…" }
        if quality.processed != nil, quality.input.codec != quality.playback.codec {
            return "\(quality.input.codec) → \(quality.playback.summary)"
        }
        return quality.playback.summary
    }

    private var audioQualitySheet: some View {
        NavigationStack {
            List {
                Section("Source file") {
                    if audioSession.tracks.indices.contains(playback.queueIndex) {
                        let track = audioSession.tracks[playback.queueIndex]
                        Text([track.codec?.uppercased(), track.bitDepth.map { "\($0)-bit" },
                              track.sampleRate.map { "\((Double($0) / 1_000).formatted(.number.precision(.fractionLength(0...1)))) kHz" }]
                            .compactMap { $0 }.joined(separator: " · "))
                            .accessibilityIdentifier("audioSourceQuality")
                    } else { Text("Source format unavailable") }
                }
                Section("Active playback") {
                    if audioSession.isSonos || audioSession.isRemoteOutput {
                        Text("The receiver owns playback. Its actual output format is not reported to this device.")
                    } else if let quality = audioSession.playbackQuality {
                        Text(quality.playback.summary).accessibilityIdentifier("audioPlaybackFormat")
                        if quality.processed != nil {
                            Text("Local processing · input \(quality.input.summary)")
                            Text("The audio engine uses the PCM format shown above. Source resolution does not describe the processed output.")
                                .font(.callout).foregroundStyle(.secondary)
                        } else {
                            Text(playback.request.methodLabel == "Compatible audio" ? "Compatible stream" : "Original stream")
                        }
                        Text("Bitrate is an average for compressed audio, or the PCM data rate. It is not download speed.")
                            .font(.callout).foregroundStyle(.secondary)
                    } else { Text("Format details are available after native playback starts.") }
                }
                if !audioSession.isSonos && !audioSession.isRemoteOutput {
                    Section("Device output") {
                        Text("\((AVAudioSession.sharedInstance().sampleRate / 1_000).formatted(.number.precision(.fractionLength(0...1)))) kHz · \(AVAudioSession.sharedInstance().outputNumberOfChannels) channels")
                        Text("The system or receiver may convert audio for the selected route. This readout does not certify a bit-perfect connection or the final DAC format.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Audio quality")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { audioQualityPresented = false } } }
        }
        .preferredColorScheme(.dark)
        .accessibilityIdentifier("audioQualitySheet")
    }

    private var audioOptionsSheet: some View {
        NavigationStack {
            ScrollView {
                NativeAudioOptions(session: audioSession, playback: playback, showsLyrics: false)
                    .padding(24)
            }
            .background(LuminaTheme.theater)
            .navigationTitle("Listening options")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { audioOptionsPresented = false }
                }
            }
        }
        .preferredColorScheme(.dark)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioOptionsSheet")
    }

    private func expandedAudioStage(in geometry: GeometryProxy) -> some View {
        let gutter: CGFloat = geometry.size.width >= 1_100 ? 56 : 32
        let gap: CGFloat = geometry.size.width >= 1_100 ? 64 : 32
        let top = max(56, geometry.safeAreaInsets.top + 24)
        let height = max(1, geometry.size.height - top - 24)
        let columnWidth = min(520, (geometry.size.width - gutter * 2 - gap) * 0.48)
        let hasOutputStatus = audioSession.isSonos || audioSession.pendingSpeakerStop != nil || audioSession.casting.isRemoteOutput || audioSession.casting.canReturnToDevice
        // Align artwork with the prominent lyrics' first line, keeping room for controls below.
        let primaryTopInset: CGFloat = 48
        let artworkSide = max(180, min(columnWidth, height - primaryTopInset - (hasOutputStatus ? 288 : 224)))
        return HStack(alignment: .top, spacing: gap) {
            VStack(spacing: 12) {
                expandedAudioArtwork(side: artworkSide)
                audioTimeline
                    .overlay(alignment: .bottom) {
                        Text(playback.request.methodLabel)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.8))
                            .accessibilityIdentifier("audioPlaybackMethod")
                            .allowsHitTesting(false)
                    }
                expandedAudioTransport
                audioQualityReadout
                NativeMusicCastStatus(session: audioSession)
                    .font(.caption)
                if audioSession.isSonos || audioSession.pendingSpeakerStop != nil {
                    Button("Return to this device") { Task { await audioSession.returnHere() } }
                        .frame(minHeight: 44)
                        .disabled(audioSession.busy)
                        .accessibilityIdentifier("audioReturnHereButton")
                }
                if audioSession.pendingSpeakerStop != nil {
                    Button("Retry stopping remote output") { Task { await audioSession.retryStoppingSpeaker() } }
                        .frame(minHeight: 44)
                        .disabled(audioSession.busy)
                        .accessibilityIdentifier("audioRetryStoppingRemoteOutputButton")
                }
                if let error = displayedError ?? playback.syncError {
                    Text(error).font(.callout).foregroundStyle(.white)
                }
                Spacer(minLength: 0)
                expandedAudioRoutes
            }
            .frame(width: artworkSide)
            .frame(width: columnWidth)
            .frame(maxHeight: .infinity, alignment: .top)
            .padding(.top, primaryTopInset)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("audioPlayerPrimaryPane")

            VStack(alignment: .leading, spacing: 16) {
                if audioCompanion == .lyrics {
                    NativeAudioLyrics(session: audioSession, playback: playback, prominent: true)
                } else {
                    HStack {
                        Text("Play queue").font(.title2.bold())
                        Spacer()
                        Button { audioCompanion = .lyrics } label: {
                            Text("Done").frame(minWidth: 44, minHeight: 44).contentShape(.rect)
                        }
                        .accessibilityIdentifier("audioQueueDoneButton")
                    }
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) { audioQueueRows }
                            .padding(.vertical, 16)
                    }
                    .accessibilityIdentifier("audioQueueScreen")
                }
                HStack(spacing: 24) {
                    Spacer(minLength: 0)
                    Button { audioCompanion = .lyrics } label: {
                        Label("Lyrics", glyph: .subtitles).frame(minHeight: 44).contentShape(.rect)
                    }
                    .font(.callout.weight(audioCompanion == .lyrics ? .bold : .regular))
                    .accessibilityIdentifier("audioLyrics")
                    .accessibilityAddTraits(audioCompanion == .lyrics ? .isSelected : [])
                    .foregroundStyle(audioCompanion == .lyrics ? .white : .white.opacity(0.65))
                    Button { audioCompanion = .queue } label: {
                        Label("Queue", glyph: .queue).frame(minHeight: 44).contentShape(.rect)
                    }
                    .font(.callout.weight(audioCompanion == .queue ? .bold : .regular))
                    .accessibilityIdentifier("audioQueueButton")
                    .accessibilityAddTraits(audioCompanion == .queue ? .isSelected : [])
                    .foregroundStyle(audioCompanion == .queue ? .white : .white.opacity(0.65))
                }
                .font(.callout.weight(.semibold))
                .frame(minHeight: 44)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("audioPlayerCompanionPane")
        }
        .padding(.horizontal, gutter)
        .padding(.top, top)
        .padding(.bottom, 24)
        .frame(width: geometry.size.width, height: geometry.size.height)
        .background {
            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 400)) { image in
                image.resizable().scaledToFill().blur(radius: 70)
            } placeholder: { LuminaTheme.theater }
                .overlay(.black.opacity(0.68))
                .ignoresSafeArea()
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .overlay(alignment: .topLeading) {
            Button { finish() } label: {
                LuminaIcon(glyph: .chevronDown, size: 24)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
                .accessibilityLabel("Close player")
                .padding(.leading, gutter - 10)
                .padding(.top, max(32, geometry.safeAreaInsets.top + 8))
                .accessibilityIdentifier("closePlayerButton")
        }
        .foregroundStyle(.white)
        .buttonStyle(.plain)
        .accessibilityAction(.escape) { finish() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("audioPlayerStage")
        .sheet(isPresented: $audioOptionsPresented) { audioOptionsSheet }
        .sheet(isPresented: $audioQualityPresented) { audioQualitySheet }
    }

    private func expandedAudioArtwork(side: CGFloat) -> some View {
        ZStack(alignment: .bottom) {
            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 1_600)) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                LuminaTheme.theater.overlay { LuminaIcon(glyph: .music, size: 64) }
            }
            .frame(width: side, height: side)
            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
            .accessibilityValue(playback.request.item.name)
            .accessibilityIdentifier("audioAlbumArtwork")
            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
            .gesture(AudioArtworkSwipe(onSkip: skipAudioTrack))

            HStack(alignment: .bottom, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(playback.request.item.name)
                        .font(.title3.weight(.semibold)).lineLimit(2)
                        .accessibilityIdentifier("nowPlayingTitle")
                    Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                        .font(.body).foregroundStyle(.white.opacity(0.8)).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(16)
            .padding(.top, 32)
            .background {
                LinearGradient(colors: [.clear, .black.opacity(0.85)], startPoint: .top, endPoint: .bottom)
            }
        }
        .frame(width: side, height: side)
        .clipShape(.rect(cornerRadius: 12))
    }

    private var expandedAudioTransport: some View {
        HStack(spacing: 2) {
            Button { playback.cycleRepeat() } label: {
                LuminaIcon(glyph: playback.repeatMode == .one ? .repeatOne : .repeat)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
                .accessibilityLabel(playback.repeatMode.label)
                .accessibilityValue(playback.repeatMode.label)
                .accessibilityIdentifier("audioRepeatButton")
                .foregroundStyle(playback.repeatMode == .off ? .white.opacity(0.65) : LuminaTheme.brand)
            Spacer(minLength: 0)
            audioTrackTransportButtons
            Spacer(minLength: 0)
            audioOptionsMenu
        }
        .frame(height: 64)
    }

    private var audioTrackTransportButtons: some View {
        Group {
            Button { Task { await audioSession.step(-1) } } label: {
                LuminaIcon(glyph: .previous, variant: .solid, size: 28)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Previous track")
            .disabled((!playback.hasPrevious && !audioSession.isSonos) || audioSession.busy)
            .accessibilityIdentifier("audioPreviousButton")
            Button {
                if playback.hasFinished { replay() } else { playback.togglePlayback() }
            } label: {
                LuminaIcon(glyph: playback.hasFinished || playback.errorMessage != nil ? .sync : playback.isPlaying ? .pause : .play,
                           variant: .solid, size: 32)
                    .frame(width: 64, height: 64).background(.white.opacity(0.10), in: .circle).contentShape(.rect)
            }
            .accessibilityLabel(playback.hasFinished ? "Replay" : playback.errorMessage != nil ? "Retry" : playback.isPlaying ? "Pause" : "Play")
            .accessibilityIdentifier("audioPlayPauseButton")
            .disabled(playback.isVideoOutputUnavailable || playback.isReplaying)
            Button { Task { await audioSession.step(1) } } label: {
                LuminaIcon(glyph: .next, variant: .solid, size: 28)
                    .frame(width: 44, height: 44).contentShape(.rect)
            }
            .accessibilityLabel("Next track")
            .disabled((!playback.hasNext && !audioSession.isSonos) || audioSession.busy)
            .accessibilityIdentifier("audioNextButton")
        }
    }

    private var audioOptionsMenu: some View {
        Menu {
            Button("Listening options") { audioOptionsPresented = true }
            Button("Stop", glyph: .stop) { stopAudio() }
                .accessibilityIdentifier("audioStopButton")
        } label: {
            Label("More playback actions", glyph: .moreHorizontal)
                .labelStyle(.iconOnly).frame(width: 44, height: 44).contentShape(.rect)
        }
        .accessibilityIdentifier("audioOptionsButton")
    }

    private var expandedAudioRoutes: some View {
        HStack(spacing: 12) {
            Spacer(minLength: 0)
            NativeMusicAirPlayButton(session: audioSession)
            NativeMusicCastButton(session: audioSession)
        }
        .frame(height: 44)
    }

    private var compactAudioStage: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 0) {
                    ZStack(alignment: .bottom) {
                        GeometryReader { artwork in
                            CachedArtworkImage(url: store.imageURL(for: playback.request.item, maxWidth: 2000)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Color.black.overlay {
                                    LuminaIcon(glyph: .music, variant: .solid, size: 40)
                                }
                            }
                            .frame(width: artwork.size.width, height: artwork.size.height)
                            .clipped()
                            .contentShape(.rect)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel("Album artwork for \(playback.request.item.album ?? playback.request.item.name)")
                            .accessibilityIdentifier("audioAlbumArtwork")
                            .accessibilityValue(playback.request.item.name)
                            .accessibilityAction(named: "Next track") { skipAudioTrack(offset: 1) }
                            .accessibilityAction(named: "Previous track") { skipAudioTrack(offset: -1) }
                            .gesture(AudioArtworkSwipe(onSkip: skipAudioTrack))
                            .simultaneousGesture(TapGesture().onEnded { revealAudioTimeline() })
                            .accessibilityAction(named: "Show track progress") { revealAudioTimeline() }
                        }
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        VStack(alignment: .leading, spacing: 12) {
                            if let error = displayedError {
                                Label(error, glyph: .warning)
                                    .font(.callout)
                            }
                            if let syncError = playback.syncError { Text(syncError).font(.caption) }
                            audioTimeline
                        }
                        .padding(.horizontal, 24)
                        .padding(.top, 64)
                        .padding(.bottom, 20 + geometry.safeAreaInsets.bottom)
                        .padding(.leading, -geometry.safeAreaInsets.leading)
                        .padding(.trailing, -geometry.safeAreaInsets.trailing)
                        .background {
                            LinearGradient(colors: [.clear, .black.opacity(0.88)], startPoint: .top, endPoint: .bottom)
                                .ignoresSafeArea(.container, edges: .horizontal)
                        }
                        .opacity(showsAudioTimeline ? 1 : 0)
                        .allowsHitTesting(showsAudioTimeline)
                        .accessibilityHidden(!showsAudioTimeline)
                    }
                    .frame(height: geometry.size.height + geometry.safeAreaInsets.bottom)
                    VStack(alignment: .leading, spacing: 24) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(playback.request.item.name)
                                .font(LuminaTheme.heading(30, relativeTo: .title))
                                .accessibilityIdentifier("nowPlayingTitle")
                            Text(playback.request.item.artists?.joined(separator: ", ") ?? playback.request.item.albumArtist ?? "")
                                .font(.title3)
                                .foregroundStyle(.secondary)
                            if let album = playback.request.item.album {
                                Text(album).foregroundStyle(.secondary)
                            }
                        }
                        Text(playback.request.methodLabel).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("audioPlaybackMethod")
                        NativeAudioOptions(session: audioSession, playback: playback)
                    }
                    .frame(maxWidth: 1120, alignment: .leading)
                    .padding(24)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(LuminaTheme.theater.ignoresSafeArea(.container, edges: .horizontal))
                }
            }
            .scrollClipDisabled()
        }
        .foregroundStyle(.white)
        .accessibilityIdentifier("audioPlayerStage")
        .task(id: playback.request.id) { revealAudioTimeline() }
        .onDisappear { audioTimelineHideTask?.cancel() }
    }

    private var showsAudioTimeline: Bool {
        audioTimelineVisible || displayedError != nil || playback.syncError != nil
    }

    func revealAudioTimeline() {
        audioTimelineHideTask?.cancel()
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { audioTimelineVisible = true }
        guard !audioIsScrubbing, !UIAccessibility.isVoiceOverRunning else { return }
        audioTimelineHideTask = Task {
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) { audioTimelineVisible = false }
        }
    }

    func finishAudioScrubbing() {
        audioIsScrubbing = false
        revealAudioTimeline()
    }

    #endif
}
