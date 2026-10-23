import SwiftUI
import LuminaCore
import UIKit

/// Landscape primary images are usable billboards too. Portrait sources remain
/// intact over a diffuse field, so neither shape is stretched to fill the screen.
private struct BillboardArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    var showsPoster = false
    var home = false
    @State private var framing: HeroFraming?
    @State private var loadedURL: URL?
    @State private var source: UIImage?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var backdropURL: URL? { store.backdropURL(for: item) }
    private var url: URL? { backdropURL ?? store.imageURL(for: item, maxWidth: 1920) }
    private var isWide: Bool {
        backdropURL != nil || (source.map { $0.size.width / max(1, $0.size.height) >= 1.4 } ?? false)
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                LuminaTheme.theater
                if let source, loadedURL == url {
                    let crop = HeroFraming.crop(isWide ? framing : nil, source: source.size, viewport: geometry.size, home: home)
                    Image(uiImage: source)
                        .resizable()
                        .frame(width: geometry.size.width / crop.width, height: geometry.size.height / crop.height)
                        .offset(x: -crop.minX * geometry.size.width / crop.width, y: -crop.minY * geometry.size.height / crop.height)
                        .frame(width: geometry.size.width, height: geometry.size.height, alignment: .topLeading)
                        .blur(radius: isWide ? 0 : reduceTransparency ? 0 : 36)
                        .opacity(isWide ? 1 : reduceTransparency ? 0 : 0.45)
                    if showsPoster && !isWide {
                        Image(uiImage: source)
                            .resizable()
                            .scaledToFit()
                            .frame(width: geometry.size.width * (geometry.size.width > 700 ? 0.28 : 0.42), height: geometry.size.height * 0.78)
                            .shadow(color: .black.opacity(0.4), radius: 20, y: 10)
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: geometry.size.width > 700 ? .trailing : .center)
                            .padding(.trailing, geometry.size.width > 700 ? geometry.size.width * 0.08 : 0)
                    }
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .task(id: url) {
            source = nil
            framing = nil
            loadedURL = nil
            guard let url else { return }
            async let proposedFraming = store.backdropFraming(for: item)
            do {
                let data: Data
                if url.isFileURL { data = try Data(contentsOf: url) }
                else {
                    let response: URLResponse
                    (data, response) = try await URLSession.shared.data(from: url)
                    guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return }
                }
                guard !Task.isCancelled, data.count <= BillboardArtworkPreparation.maximumEncodedBytes else { return }
                let image = await Task.detached(priority: .userInitiated) {
                    BillboardArtworkPreparation.image(from: data)
                }.value
                guard !Task.isCancelled, let image else { return }
                let chosenFraming = await proposedFraming
                guard !Task.isCancelled else { return }
                framing = chosenFraming
                source = UIImage(cgImage: image)
                loadedURL = url
            } catch { /* Keep the finished text and theater field when artwork is unavailable. */ }
        }
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }
}

struct MediaBackdrop: View {
    let item: MediaItem
    let store: LuminaStore
    var body: some View { BillboardArtwork(item: item, store: store) }
}

struct MediaBillboard<Actions: View>: View {
    let item: MediaItem
    let store: LuminaStore
    let context: LocalizedStringKey
    var showsOverview = true
    var featured = false
    @ViewBuilder let actions: Actions
    @State private var compactLayout = true
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorSchemeContrast) private var contrast
    #if !os(tvOS)
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 600
    #endif

    var body: some View {
        Group {
            if compact {
                VStack(alignment: .leading, spacing: 0) {
                    BillboardArtwork(item: item, store: store, showsPoster: true, home: featured)
                        .aspectRatio(16 / 9, contentMode: .fit)
                    copy
                        .foregroundStyle(LuminaTheme.ink)
                        .padding(20)
                        .frame(maxWidth: .infinity, minHeight: featured ? 216 : 0, alignment: .topLeading)
                        .background(LuminaTheme.paper)
                }
            } else {
                copy
                    .foregroundStyle(.white)
                    .frame(maxWidth: copyWidth, alignment: .leading)
                    .padding(.horizontal, contentInset)
                    .padding(.top, 32)
                    .padding(.bottom, featured ? 52 : 44)
                    .frame(maxWidth: .infinity, minHeight: minimumHeight, alignment: .bottomLeading)
                    .background {
                        ZStack {
                            BillboardArtwork(item: item, store: store, showsPoster: true, home: featured)
                            LinearGradient(stops: [
                                .init(color: LuminaTheme.theater.opacity(contrast == .increased ? 0.96 : 0.72), location: 0),
                                .init(color: LuminaTheme.theater.opacity(0.30), location: 0.48),
                                .init(color: .clear, location: 0.76),
                            ], startPoint: .leading, endPoint: .trailing)
                            LinearGradient(stops: [
                                .init(color: .clear, location: 0.35),
                                .init(color: LuminaTheme.theater.opacity(0.4), location: 0.8),
                                .init(color: LuminaTheme.theater, location: 1),
                            ], startPoint: .top, endPoint: .bottom)
                        }
                    }
            }
        }
        #if !os(tvOS)
        .onGeometryChange(for: Bool.self) { $0.size.width < minimumWideWidth } action: { compactLayout = $0 }
        #endif
        .tint(compact ? LuminaTheme.ink : .white)
    }

    private var copy: some View {
        VStack(alignment: .leading, spacing: copySpacing) {
            Text(context)
                .font(.system(size: compact ? 11 : labelSize, weight: .semibold))
                .tracking(2)
                .textCase(.uppercase)
                .foregroundStyle(compact ? LuminaTheme.muted : .white.opacity(0.85))
            Text(item.name)
                .font(titleFont)
                .tracking(-0.6)
                .lineLimit(nil)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier(featured ? "featuredTitle" : "detailTitle")
            MediaMetadata(item: item, onArtwork: !compact)
            if showsOverview && !(featured && compact), let overview = item.overview, !overview.isEmpty {
                Text(overview)
                    .font(LuminaTheme.editorial(bodySize, relativeTo: .body))
                    .foregroundStyle(compact ? LuminaTheme.muted : .white.opacity(0.9))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
                VStack(alignment: .leading, spacing: 6) {
                    GeometryReader { geometry in
                        Rectangle().fill((compact ? LuminaTheme.ink : Color.white).opacity(0.2))
                        Rectangle().fill(LuminaTheme.spotlight)
                            .frame(width: geometry.size.width * min(1, item.resumeSeconds / item.durationSeconds))
                    }
                    .frame(height: 3)
                    Text(Duration.seconds(item.durationSeconds - item.resumeSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)) + " left")
                        .font(.caption)
                }
                .frame(maxWidth: 260)
                .accessibilityElement(children: .combine)
            }
            actions.padding(.top, compact ? 8 : 14)
        }
    }

    private var compact: Bool {
        #if os(tvOS)
        false
        #else
        // A regular window can still contain a narrow split-view detail pane.
        compactLayout
        #endif
    }
    private var titleFont: Font {
        #if os(tvOS)
        LuminaTheme.editorial(68, relativeTo: .largeTitle, italic: true).weight(.bold)
        #else
        LuminaTheme.editorial(compact ? 34 : 58, relativeTo: .largeTitle, italic: true).weight(.bold)
        #endif
    }
    private var labelSize: CGFloat {
        #if os(tvOS)
        16
        #else
        12
        #endif
    }
    private var bodySize: CGFloat {
        #if os(tvOS)
        24
        #else
        18
        #endif
    }
    private var copyWidth: CGFloat {
        #if os(tvOS)
        1000
        #else
        dynamicTypeSize.isAccessibilitySize ? .infinity : 600
        #endif
    }
    private var copySpacing: CGFloat {
        #if os(tvOS)
        12
        #else
        10
        #endif
    }
    private var contentInset: CGFloat {
        #if os(tvOS)
        80
        #else
        32
        #endif
    }
    private var minimumHeight: CGFloat {
        #if os(tvOS)
        560
        #else
        featured ? 480 : 420
        #endif
    }
}

struct MediaMetadata: View {
    let item: MediaItem
    var onArtwork = true

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { metadata }
            VStack(alignment: .leading, spacing: 8) { metadata }
        }
        .font(.caption.weight(.medium))
        .textCase(.uppercase)
        .tracking(1)
        .foregroundStyle(onArtwork ? .white.opacity(0.85) : LuminaTheme.muted)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var metadata: some View {
        if !item.displaySubtitle.isEmpty { Text(item.displaySubtitle) }
        if item.durationSeconds > 0 {
            Text(Duration.seconds(item.durationSeconds).formatted(.units(allowed: [.hours, .minutes], width: .abbreviated)))
        }
        Text(item.type == "Movie" ? "Film" : item.type == "Episode" ? "Episode" : item.isAudio ? "Track" : "Video")
    }
}


/// The artwork and copy move together while the controls retain their focus and position.
struct FeaturedShelf<Actions: View>: View {
    let items: [MediaItem]
    let store: LuminaStore
    var isInteracting = false
    @ViewBuilder let actions: (MediaItem) -> Actions
    @State private var selectedID: String?
    @State private var movingForward = true
    @State private var hovered = false
    @State private var dragging = false
    @State private var paused = false
    @State private var compactLayout = true
    @FocusState private var focusedID: String?
    #if os(tvOS)
    @FocusedValue(\.featuredBillboard) private var billboardHasFocus
    #endif
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    #if !os(tvOS)
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 600
    #endif

    private var selected: MediaItem? { items.first { $0.id == selectedID } ?? items.first }
    private var index: Int { items.firstIndex { $0.id == selected?.id } ?? 0 }
    private var canAdvance: Bool {
        items.count > 1 && !reduceMotion && !voiceOver && scenePhase == .active
            && !isInteracting && !containsFocus && !hovered && !dragging && !paused
    }
    private var containsFocus: Bool {
        #if os(tvOS)
        billboardHasFocus == true || focusedID != nil
        #else
        focusedID != nil
        #endif
    }
    private var timerKey: String { "\(selected?.id ?? "")-\(canAdvance)" }
    private var compact: Bool {
        #if os(tvOS)
        false
        #else
        compactLayout
        #endif
    }

    var body: some View {
        if let selected {
            ZStack {
                MediaBillboard(item: selected, store: store, context: selected.resumeSeconds > 0 ? "Continue watching" : "New in your library", featured: true) {
                    actions(selected)
                }
                .id(selected.id)
                .transition(reduceMotion ? .opacity : .asymmetric(
                    insertion: .move(edge: movingForward ? .trailing : .leading).combined(with: .opacity),
                    removal: .move(edge: movingForward ? .leading : .trailing).combined(with: .opacity)
                ))
            }
            .clipped()
            .overlay(alignment: .bottomTrailing) {
                if items.count > 1 && !compact {
                    controls.padding(.trailing, controlInset).padding(.bottom, 20)
                }
            }
            .overlay(alignment: .top) {
                if items.count > 1 && compact {
                    Color.clear.aspectRatio(16 / 9, contentMode: .fit)
                        .overlay(alignment: .bottomTrailing) { controls.padding(8) }
                        .frame(maxHeight: .infinity, alignment: .top)
                }
            }
            #if !os(tvOS)
            .onGeometryChange(for: Bool.self) { $0.size.width < minimumWideWidth } action: { compactLayout = $0 }
            #endif
            #if os(tvOS)
            .focusedValue(\.featuredBillboard, true)
            #endif
            #if !os(tvOS)
            .simultaneousGesture(DragGesture(minimumDistance: 24)
                .onChanged { _ in dragging = true }
                .onEnded { value in
                    dragging = false
                    if abs(value.translation.width) > 48 && abs(value.translation.width) > abs(value.translation.height) {
                        step(value.translation.width < 0 ? 1 : -1)
                    }
                })
            .onHover { hovered = $0 }
            #endif
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Featured titles, \(index + 1) of \(items.count)")
            .task(id: timerKey) {
                guard canAdvance, items.count > 1 else { return }
                let next = items[(index + 1) % items.count]
                if let url = store.backdropURL(for: next) ?? store.imageURL(for: next, maxWidth: 1920) {
                    // Warm the next rendition in the system cache before its slide arrives.
                    _ = try? await URLSession.shared.data(from: url)
                }
            }
            .task(id: timerKey) {
                guard canAdvance else { return }
                do { try await Task.sleep(for: .seconds(8)) } catch { return }
                guard canAdvance, !Task.isCancelled else { return }
                step(1)
            }
            .onChange(of: focusedID) {
                #if os(tvOS)
                if let focusedID, items.contains(where: { $0.id == focusedID }) { select(focusedID) }
                #endif
            }
        }
    }

    private var controls: some View {
        HStack(spacing: 2) {
            if !compact { stepButton("Previous featured title", symbol: "chevron.backward", direction: -1) }
            #if os(tvOS)
            // Home supplies at most eight titles. Keeping their controls in one
            // row lets the remote leave the last title for Next and Pause.
            featureChoices
            #else
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) { featureChoices }
                .onChange(of: selectedID) {
                    if let selectedID { proxy.scrollTo(selectedID, anchor: .center) }
                }
            }
            .frame(width: CGFloat(min(items.count, compact ? 5 : 6)) * (controlSize + 2))
            .fixedSize(horizontal: false, vertical: true)
            #endif
            if !compact { stepButton("Next featured title", symbol: "chevron.forward", direction: 1) }
            Button { paused.toggle() } label: {
                Image(systemName: paused ? "play.fill" : "pause.fill")
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: controlSize, height: controlSize)
                    .background(focusFill("pause"), in: .rect(cornerRadius: 8))
                    .overlay(focusRing("pause"))
                    .contentShape(.rect)
            }
            .buttonStyle(FeaturedControlStyle())
            .focused($focusedID, equals: "pause")
            .accessibilityLabel(paused ? "Resume featured rotation" : "Pause featured rotation")
            .accessibilityIdentifier("featuredPauseButton")
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 4)
        .shadow(color: .black.opacity(0.65), radius: 2, y: 1)
        #if os(tvOS)
        .focusSection()
        #endif
    }

    private var featureChoices: some View {
        HStack(spacing: 2) {
            ForEach(items) { item in
                Button { select(item.id) } label: {
                    Capsule()
                        .fill(selected?.id == item.id ? .white : .white.opacity(0.45))
                        .frame(width: selected?.id == item.id ? 30 : 16, height: 3)
                        .frame(width: controlSize, height: controlSize)
                        .background(focusFill(item.id), in: .rect(cornerRadius: 8))
                        .overlay(focusRing(item.id))
                        .contentShape(.rect)
                }
                .buttonStyle(FeaturedControlStyle())
                .focused($focusedID, equals: item.id)
                .accessibilityLabel("Feature \(item.name)")
                .accessibilityValue(selected?.id == item.id ? "Selected" : "")
                .accessibilityIdentifier("featuredChoice-\(item.id)")
                .id(item.id)
            }
        }
    }

    private var controlSize: CGFloat {
        #if os(tvOS)
        52
        #else
        44
        #endif
    }
    private var controlInset: CGFloat {
        #if os(tvOS)
        80
        #else
        24
        #endif
    }
    private func focusFill(_ id: String) -> Color { .white.opacity(focusedID == id ? 0.22 : 0) }
    private func focusRing(_ id: String) -> some View {
        RoundedRectangle(cornerRadius: 8).stroke(focusedID == id ? .white : .clear, lineWidth: 2)
    }
    private func stepButton(_ label: String, symbol: String, direction: Int) -> some View {
        let id = direction < 0 ? "previous" : "next"
        return Button { step(direction) } label: {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .frame(width: controlSize, height: controlSize)
                .background(focusFill(id), in: .rect(cornerRadius: 8))
                .overlay(focusRing(id))
                .contentShape(.rect)
        }
        .buttonStyle(FeaturedControlStyle())
        .focused($focusedID, equals: id)
        .accessibilityLabel(label)
        .accessibilityIdentifier(direction < 0 ? "featuredPreviousButton" : "featuredNextButton")
    }
    private func step(_ direction: Int) {
        guard !items.isEmpty else { return }
        select(items[(index + direction + items.count) % items.count].id, forward: direction > 0)
    }
    private func select(_ id: String, forward: Bool? = nil) {
        guard id != selected?.id else { return }
        movingForward = forward ?? ((items.firstIndex { $0.id == id } ?? 0) > index)
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.72)) { selectedID = id }
    }
}

/// Keep the visible focus ring and the directional target on the same bounds.
/// tvOS's plain style adds a larger plate that overlaps neighboring hero controls.
private struct FeaturedControlStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.65 : 1)
    }
}

#if os(tvOS)
private struct FeaturedBillboardFocus: FocusedValueKey { typealias Value = Bool }
private extension FocusedValues {
    var featuredBillboard: Bool? {
        get { self[FeaturedBillboardFocus.self] }
        set { self[FeaturedBillboardFocus.self] = newValue }
    }
}
#endif

/// The listening room starts with a record, using the same collection composition as the web.
struct MusicCollectionSpotlight: View {
    let item: MediaItem
    let store: LuminaStore
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #if !os(tvOS)
    @State private var layoutWidth: CGFloat = 0
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 720
    #endif

    var body: some View {
        Group {
            if wide {
                HStack(alignment: .center, spacing: 64) {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Your collection").font(LuminaTheme.editorial(20, relativeTo: .body))
                        Text("A room full\nof records")
                            .font(LuminaTheme.editorial(displaySize, relativeTo: .largeTitle))
                            .tracking(-1)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Pick a record from your library and settle into a listen.")
                            .font(.body).foregroundStyle(LuminaTheme.muted)
                            .frame(maxWidth: 360, alignment: .leading)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .leading, spacing: 14) {
                        artwork.frame(width: coverSize)
                        albumCopy
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                if !dynamicTypeSize.isAccessibilitySize {
                    HStack(alignment: .center, spacing: 20) {
                        artwork.frame(width: 132)
                        albumCopy.frame(maxWidth: .infinity, alignment: .leading)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 20) {
                        artwork.frame(maxWidth: 220)
                        albumCopy
                    }
                }
            }
        }
        .padding(.vertical, 24)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) {
            Rectangle().fill(LuminaTheme.ink.opacity(0.18)).frame(height: 1)
        }
        .foregroundStyle(LuminaTheme.ink)
        .multilineTextAlignment(.leading)
        #if !os(tvOS)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { layoutWidth = $0 }
        #endif
    }

    private var albumCopy: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !wide {
                Text("From your library").font(.caption).foregroundStyle(LuminaTheme.muted)
            }
            Text(item.name)
                .font(LuminaTheme.editorial(wide ? 26 : 25, relativeTo: .title2))
                .fixedSize(horizontal: false, vertical: true)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle).font(.subheadline).foregroundStyle(LuminaTheme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Label("Open album", systemImage: "arrow.right")
                .font(.caption.weight(.semibold)).foregroundStyle(LuminaTheme.gold)
                .padding(.top, 6)
        }
    }

    private var artwork: some View {
        Rectangle().fill(LuminaTheme.ink.opacity(0.06))
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                GeometryReader { geometry in
                    AsyncImage(url: store.imageURL(for: item, maxWidth: 760)) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        Image(systemName: "music.note").font(.largeTitle)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .foregroundStyle(LuminaTheme.muted)
                    }
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .clipped()
                }
            }
            .clipShape(.rect(cornerRadius: 12))
            .accessibilityHidden(true)
    }
    private var wide: Bool {
        #if os(tvOS)
        true
        #else
        layoutWidth >= minimumWideWidth && !dynamicTypeSize.isAccessibilitySize
        #endif
    }
    private var displaySize: CGFloat {
        #if os(tvOS)
        64
        #else
        52
        #endif
    }
    private var coverSize: CGFloat {
        #if os(tvOS)
        280
        #else
        240
        #endif
    }
}
