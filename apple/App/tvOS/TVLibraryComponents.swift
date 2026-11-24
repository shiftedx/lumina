import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins
import LuminaCore


struct TVFeaturedView: View {
    let store: LuminaStore
    var items: [MediaItem]? = nil
    @Binding var heroSelection: String?
    let onOpen: (MediaItem) -> Void
    let onPlayback: (PlaybackRequest) -> Void
    let focusRequest: Int
    let onRequestHeaderFocus: () -> Void
    var minimumHeight: CGFloat
    var onWatchlistChanged: @MainActor () async -> Void = {}
    @State private var preparing = false
    @State private var playbackError: String?
    @State private var watchlist: LuminaWatchlist?
    @State private var watchlistItemID: String?
    @State private var heroIsVisible = true
    @FocusState private var focusedAction: String?

    var body: some View {
        FeaturedShelf(
            items: items ?? store.featuredItems,
            store: store,
            isInteracting: preparing || focusedAction != nil || !heroIsVisible,
            showsOverview: false,
            showsContext: false,
            showsMetadata: false,
            showsProgress: false,
            wideMinimumHeight: minimumHeight,
            onOpen: onOpen,
            selection: $heroSelection
        ) { item in
            VStack(alignment: .leading, spacing: 16) {
                if let context = context(for: item) {
                    Text(context)
                        .font(TVTypography.metadata.weight(.medium))
                        .foregroundStyle(.white.opacity(0.82))
                        .lineLimit(1)
                }
                HStack(spacing: 24) {
                    heroActionButtons(for: item)
                }
            }
            if let playbackError { Text(playbackError).font(TVTypography.body) }
        }
        .environment(\.luminaArtworkMotionEnabled, heroIsVisible)
        .onScrollVisibilityChange(threshold: 0.2) { heroIsVisible = $0 }
        .task(id: watchlistTaskID) {
            guard let item = selectedItem else {
                watchlist = nil
                watchlistItemID = nil
                return
            }
            let model = LuminaWatchlist(session: store.session)
            watchlist = model
            watchlistItemID = item.id
            await model.load(itemID: item.id)
        }
        .onChange(of: focusRequest) { _, _ in
            guard selectedItem != nil, !preparing else { return }
            focusedAction = "play"
        }
        .onMoveCommand { direction in
            if direction == .up { onRequestHeaderFocus() }
        }
        // Menu restores the last browsing selection. Deliberately avoid
        // redirecting that return path to Play when Home is recreated.
    }

    @ViewBuilder private func heroActionButtons(for item: MediaItem) -> some View {
        Button {
            guard !preparing else { return }
            preparing = true
            playbackError = nil
            Task {
                defer { preparing = false }
                do { onPlayback(try await store.preparePlayback(for: item)) }
                catch { playbackError = "Playback could not start. Try again." }
            }
        } label: {
            Label(preparing ? "Preparing…" : item.resumeSeconds > 0 ? "Resume" : "Play",
                  glyph: .play, variant: .solid, size: 28)
        }
        .buttonStyle(LuminaQuietActionStyle(primary: true))
        .disabled(preparing)
        .accessibilityIdentifier("featuredPlayButton")
        .focused($focusedAction, equals: "play")

        Button {
            guard watchlistItemID == item.id, let watchlist else { return }
            if watchlist.errorMessage != nil {
                Task { await watchlist.load(itemID: item.id) }
            } else {
                Task {
                    let didChange = await watchlist.set(itemID: item.id, included: !watchlist.isIncluded)
                    if didChange { await onWatchlistChanged() }
                }
            }
        } label: {
            Label(
                watchlist?.errorMessage == nil
                    ? watchlist?.isIncluded == true ? "In watchlist" : "Add to watchlist"
                    : "Retry watchlist",
                glyph: .bookmark,
                variant: watchlist?.isIncluded == true ? .solid : .outline
            )
        }
        .buttonStyle(LuminaQuietActionStyle(mediaOverlay: true))
        .disabled(watchlistItemID != item.id || watchlist?.isLoading == true || watchlist?.isSaving == true)
        .accessibilityIdentifier("featuredWatchlistButton")
        .accessibilityValue(watchlist?.isIncluded == true ? "In watchlist" : "Not in watchlist")
        .accessibilityHint(watchlist?.isIncluded == true ? "Remove from your watchlist" : "Save to your watchlist")
        .focused($focusedAction, equals: "watchlist")
    }

    private var selectedItem: MediaItem? {
        (items ?? store.featuredItems).first { $0.id == heroSelection } ?? (items ?? store.featuredItems).first
    }

    private var watchlistTaskID: String {
        "\(selectedItem?.id ?? "")-\(store.session?.accessToken ?? "")"
    }

    private func context(for item: MediaItem) -> String? {
        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            return Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)) + " remaining"
        }
        return nil
    }
}

private struct TVSectionHeading: View {
    let title: String
    var count: Int?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 20) {
            Text(title).font(TVTypography.sectionTitle)
                .foregroundStyle(LuminaTheme.ink).accessibilityAddTraits(.isHeader)
            Spacer()
            if let count { Text(count, format: .number).font(TVTypography.metadata).foregroundStyle(LuminaTheme.muted) }
        }
    }
}

struct TVRail: View {
    let title: String
    let items: [MediaItem]
    let store: LuminaStore
    var poster = false
    var identifierPrefix = "resumeItem"
    let onOpen: (MediaItem) -> Void
    var feed: LuminaHomeFeed? = nil
    var onHide: (String) -> Void = { _ in }
    @FocusState private var focusedItemID: String?
    @State private var movingForward = true
    @State private var lastFocusedItemID: String?

    private var captionPresentation: TVRailCaptionPresentation {
        TVRailCaptionPresentation(identifierPrefix: identifierPrefix)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TVSectionHeading(title: title, count: items.count)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 32) {
                    ForEach(items) { item in
                        TVRecommendationCard(item: item, feed: feed, onHide: onHide) {
                            TVMediaCard(item: item, store: store, poster: poster, captionPresentation: captionPresentation,
                                        artworkWidth: poster ? 480 : 760) {
                                if let reco = item.luminaReco { Task { await feed?.record("open", recommendation: reco) } }
                                onOpen(item)
                            }
                        }
                            .frame(width: item.isAudio || item.type.hasPrefix("Music") ? 260 : poster ? 220 : 400)
                            .focused($focusedItemID, equals: item.id)
                            .accessibilityIdentifier("\(identifierPrefix)-\(item.id)")
                    }
                }
                .padding(.vertical, 24)
            }
            .scrollClipDisabled()
        }
        .padding(.horizontal, 80)
        // Enter using this row’s history, rather than the previous row’s horizontal position.
        .defaultFocus($focusedItemID, items.first(where: { $0.id == lastFocusedItemID })?.id ?? items.first?.id,
                      priority: .userInitiated)
        .onChange(of: focusedItemID) { old, new in
            if let new { lastFocusedItemID = new }
            guard let old, let new,
                  let previous = items.firstIndex(where: { $0.id == old }),
                  let current = items.firstIndex(where: { $0.id == new }) else { return }
            movingForward = current >= previous
        }
        .task(id: "\(focusedItemID ?? "")-\(movingForward)", priority: .utility) {
            // Only the focused shelf speculates; all rails share the four-load artwork budget.
            guard let focusedItemID, let index = items.firstIndex(where: { $0.id == focusedItemID }) else { return }
            let upcoming = movingForward ? Array(items.dropFirst(index + 1).prefix(8))
                : Array(items.prefix(index).suffix(8).reversed())
            let urls = upcoming.compactMap {
                store.thumbnailURL(for: $0, poster: poster || $0.isAudio || $0.type.hasPrefix("Music"),
                                   maxWidth: poster ? 480 : 760)
            }
            await ArtworkImageCache.shared.prefetch(urls, limit: 8)
        }
    }
}

@MainActor private struct TVRecommendationCard<Content: View>: View {
    let item: MediaItem
    let feed: LuminaHomeFeed?
    let onHide: (String) -> Void
    let content: Content
    @State private var isVisible = false

    init(item: MediaItem, feed: LuminaHomeFeed?, onHide: @escaping (String) -> Void, @ViewBuilder content: () -> Content) {
        self.item = item
        self.feed = feed
        self.onHide = onHide
        self.content = content()
    }

    var body: some View {
        content
            .onScrollVisibilityChange(threshold: 0.6) { isVisible = $0 }
            .task(id: isVisible) {
                guard isVisible, let reco = item.luminaReco else { return }
                try? await Task.sleep(for: .milliseconds(600))
                guard !Task.isCancelled else { return }
                await feed?.record("impression", recommendation: reco)
            }
            .contextMenu {
                if item.luminaReco != nil {
                    Button("Not interested") {
                        Task {
                            if let hidden = await feed?.hide(item) { onHide(hidden) }
                        }
                    }
                }
            }
    }
}

struct TVMediaCard: View {
    let item: MediaItem
    let store: LuminaStore
    var poster = false
    var captionPresentation: TVRailCaptionPresentation = .standard
    var showsCaption = true
    var artworkWidth = 760
    let action: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var titleLinesHeight: CGFloat = 64
    @ScaledMetric(relativeTo: .caption2) private var metadataLineHeight: CGFloat = 30

    private var caption: TVRailCaption {
        TVRailCaption(item: item, presentation: captionPresentation)
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                TVArtwork(item: item, store: store, aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9, maxWidth: artworkWidth)
                    .frame(maxWidth: .infinity)
                    .overlay(alignment: .bottomLeading) {
                        if !showsCaption {
                            TVFocusedArtworkCaption(caption: caption)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if item.resumeSeconds > 0, item.durationSeconds > 0 {
                            ProgressView(value: min(item.resumeSeconds, item.durationSeconds), total: item.durationSeconds)
                                .progressViewStyle(.linear).tint(LuminaTheme.brand)
                                .frame(height: 3).background(.black.opacity(0.4))
                                .accessibilityLabel("Watch progress")
                                .accessibilityValue("\(Int(min(1, item.resumeSeconds / item.durationSeconds) * 100)) percent")
                        }
                    }

                if showsCaption {
                    Text(caption.primary)
                        .font(TVTypography.cardTitle)
                        .foregroundStyle(LuminaTheme.ink)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                        .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : titleLinesHeight, alignment: .topLeading)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    Group {
                        if let secondary = caption.secondary {
                            Text(secondary)
                        } else {
                            Color.clear.accessibilityHidden(true)
                        }
                    }
                    .font(TVTypography.metadata)
                    .foregroundStyle(LuminaTheme.muted)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataLineHeight, alignment: .topLeading)
                    .frame(maxWidth: .infinity, alignment: .leading)

                    if captionPresentation.reservesProgressBaseline {
                        Group {
                            if let progress = caption.progress {
                                Text(progress)
                            } else {
                                Color.clear.accessibilityHidden(true)
                            }
                        }
                        .font(TVTypography.metadata.weight(.medium))
                        .foregroundStyle(LuminaTheme.muted)
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                        .frame(height: dynamicTypeSize.isAccessibilitySize ? nil : metadataLineHeight, alignment: .topLeading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(TVArtworkButtonStyle(aspect: item.isAudio || item.type.hasPrefix("Music") ? 1 : poster ? 2 / 3 : 16 / 9))
        .accessibilityLabel(caption.accessibilityLabel)
        .accessibilityValue(caption.progress ?? "")
    }
}

/// Grid posters remain artwork-first at rest. Focus reveals the same caption
/// without changing card geometry, while the Button keeps its full accessible
/// title at every focus state.
private struct TVFocusedArtworkCaption: View {
    let caption: TVRailCaption
    @Environment(\.isFocused) private var isFocused
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(caption.primary)
                .font(TVTypography.cardTitle.weight(.semibold))
                .lineLimit(2)
            if let secondary = caption.secondary {
                Text(secondary)
                    .font(TVTypography.metadata)
                    .foregroundStyle(.white.opacity(0.82))
                    .lineLimit(1)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            LinearGradient(colors: [.clear, .black.opacity(0.86)], startPoint: .top, endPoint: .bottom)
                .padding(.top, -48)
        }
        .opacity(isFocused ? 1 : 0)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.14), value: isFocused)
        .accessibilityHidden(true)
    }
}

enum TVRailCaptionPresentation: Equatable {
    case standard
    case continueWatching
    case nextUp

    init(identifierPrefix: String) {
        switch identifierPrefix {
        case "resumeItem": self = .continueWatching
        case "nextUpItem": self = .nextUp
        default: self = .standard
        }
    }

    var reservesProgressBaseline: Bool {
        self != .standard
    }
}

private struct TVRailCaption {
    let primary: String
    let secondary: String?
    let progress: String?

    init(item: MediaItem, presentation: TVRailCaptionPresentation) {
        guard presentation != .standard else {
            primary = item.name
            secondary = item.displaySubtitle.isEmpty ? nil : item.displaySubtitle
            progress = nil
            return
        }

        let episodeLabel = Self.episodeLabel(for: item)
        primary = item.type == "Episode" ? item.seriesName ?? item.name : item.name
        secondary = episodeLabel ?? (item.displaySubtitle.isEmpty ? nil : item.displaySubtitle)

        if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
            let remaining = Duration.seconds(item.durationSeconds - item.resumeSeconds)
                .formatted(.units(allowed: [.hours, .minutes], width: .abbreviated))
            progress = "\(remaining) remaining"
        } else if presentation == .nextUp, item.type == "Episode" {
            progress = "Next episode"
        } else {
            progress = nil
        }
    }

    private static func episodeLabel(for item: MediaItem) -> String? {
        guard item.type == "Episode" else { return nil }
        let number: String? = switch (item.parentIndexNumber, item.indexNumber) {
        case let (.some(season), .some(episode)): "S\(season) · E\(episode)"
        default: nil
        }
        return [number, item.name].compactMap { $0 }.joined(separator: " · ")
    }

    var accessibilityLabel: String {
        [primary, secondary, progress].compactMap { $0 }.joined(separator: ", ")
    }
}

/// Focus follows the artwork; captions stay directly on the page.
struct TVArtworkButtonStyle: ButtonStyle {
    private let focusedScale: CGFloat

    init(aspect: CGFloat) {
        focusedScale = aspect < 1 ? 1.08 : 1.04
    }

    @Environment(\.isFocused) private var isFocused
    @Environment(\.luminaReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(isFocused && !reduceMotion ? focusedScale : 1, anchor: .top)
            .shadow(color: .black.opacity(isFocused ? 0.48 : 0), radius: isFocused ? 18 : 0, y: isFocused ? 10 : 0)
            .overlay {
                if reduceMotion && isFocused {
                    RoundedRectangle(cornerRadius: 4)
                        .strokeBorder(.white, lineWidth: 3)
                }
            }
            .opacity(configuration.isPressed ? 0.75 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: isFocused)
    }
}

struct TVArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    let aspect: CGFloat
    var maxWidth = 760

    var body: some View {
        Rectangle()
            .fill(LuminaTheme.ink.opacity(0.08))
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                placeholder.overlay {
                    SubjectAwareArtwork(url: artworkURL) { await store.artworkFraming(for: $0) }
                }
            }
            .clipShape(.rect(cornerRadius: 4))
            #if LUMINA_UI_TESTING
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Artwork")
            .accessibilityIdentifier("tvArtwork-\(item.id)")
            .accessibilityValue(artworkURL?.path ?? "Missing artwork")
            #endif
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [LuminaTheme.brand.opacity(0.40), LuminaTheme.paper], startPoint: .topLeading, endPoint: .bottomTrailing)
            LuminaIcon(glyph: item.isAudio || item.type.hasPrefix("Music") ? .music : .movies,
                       variant: .solid, size: 28)
                .foregroundStyle(LuminaTheme.ink.opacity(0.7))
        }
    }

    private var artworkURL: URL? {
        store.thumbnailURL(for: item, poster: aspect <= 1, maxWidth: maxWidth)
    }
}

struct TVMessageView: View {
    let title: String
    let message: String
    let actionTitle: String
    var focusActionOnAppear = false
    let action: () -> Void
    @FocusState private var actionFocused: Bool

    var body: some View {
        VStack(spacing: 24) {
            Text(title)
                .font(TVTypography.sectionTitle)
                .foregroundStyle(LuminaTheme.ink)
            Text(message)
                .font(TVTypography.body)
                .foregroundStyle(LuminaTheme.muted)
                .multilineTextAlignment(.center)
            Button(actionTitle, action: action)
                .buttonStyle(LuminaQuietActionStyle(primary: true))
                .controlSize(.large)
                .focused($actionFocused)
                .task {
                    if focusActionOnAppear { actionFocused = true }
                }
        }
        .frame(maxWidth: .infinity, minHeight: 360)
        .padding(48)
    }
}

/// A single remote scrubber avoids 27 tiny focus targets. Up/Down previews a
/// letter; Select commits one indexed page request, Left returns to the grid.
struct TVLibraryAlphabetRail: View {
    let letters: [String]
    @Binding var selection: String?
    let expanded: Bool
    let isFocused: Bool
    let onReturn: () -> Void
    let onSelect: (String) -> Void

    private var selectedIndex: Int { letters.firstIndex(of: selection ?? "") ?? 0 }
    private var visibleLetters: [String] {
        let lower = max(0, min(selectedIndex - 3, letters.count - 7))
        return Array(letters.dropFirst(lower).prefix(7))
    }

    var body: some View {
        Button { if !letters.isEmpty { onSelect(letters[selectedIndex]) } } label: {
            VStack(spacing: 14) {
                LuminaIcon(glyph: .chevronUp, size: 14)
                if expanded {
                    ForEach(visibleLetters, id: \.self) { letter in
                        Text(letter)
                            .font(letter == letters[selectedIndex] ? TVTypography.sectionTitle : TVTypography.metadata)
                            .foregroundStyle(.white.opacity(letter == letters[selectedIndex] ? 1 : 0.72))
                            .frame(height: 36)
                            .overlay(alignment: .bottom) {
                                if isFocused && letter == letters[selectedIndex] {
                                    Rectangle().frame(width: 26, height: 2)
                                }
                            }
                    }
                } else {
                    Text("A–Z").font(TVTypography.cardTitle).frame(height: 44)
                }
                LuminaIcon(glyph: .chevronDown, size: 14)
            }
            .foregroundStyle(.white.opacity(isFocused ? 1 : 0.72))
            .frame(width: 72, height: 420)
            .contentShape(.rect)
        }
        .buttonStyle(TVAlphabetButtonStyle())
        .accessibilityIdentifier("libraryAlphabetIndex")
        .accessibilityLabel("Jump to title letter")
        .accessibilityValue(letters.isEmpty ? "" : letters[selectedIndex])
        .accessibilityHint("Up or down chooses a letter. Select jumps to its first title.")
        .accessibilityAdjustableAction { direction in
            guard !letters.isEmpty else { return }
            switch direction {
            case .increment: selection = letters[min(letters.count - 1, selectedIndex + 1)]
            case .decrement: selection = letters[max(0, selectedIndex - 1)]
            @unknown default: break
            }
        }
        .onMoveCommand { direction in
            guard !letters.isEmpty else { return }
            switch direction {
            case .up: selection = letters[max(0, selectedIndex - 1)]
            case .down: selection = letters[min(letters.count - 1, selectedIndex + 1)]
            case .left: onReturn()
            default: break
            }
        }
    }
}

private struct TVAlphabetButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.65 : 1)
    }
}

// Native focus scrolling reveals the focused button, which can leave the rest of
// a tall hero above the viewport. Restore its full artwork when focus returns.
private struct TVHeroRegionKey: FocusedValueKey { typealias Value = Bool }

extension FocusedValues {
    var tvHeroRegion: Bool? {
        get { self[TVHeroRegionKey.self] }
        set { self[TVHeroRegionKey.self] = newValue }
    }
}

struct TVHeroScrollView<Content: View>: View {
    @ViewBuilder let content: Content
    @State private var position = ScrollPosition(edge: .top)
    @FocusedValue(\.tvHeroRegion) private var heroHasFocus
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ScrollView { content }
            .scrollPosition($position)
            .onChange(of: heroHasFocus) { _, focused in
                guard focused == true else { return }
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
                    position.scrollTo(edge: .top)
                }
            }
    }
}
