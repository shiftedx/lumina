import SwiftUI
import LuminaCore
import UIKit

extension EnvironmentValues {
    @Entry var luminaArtworkMotionEnabled = true
}

/// Landscape primary images are usable billboards too. Portrait sources remain
/// intact over a diffuse field, so neither shape is stretched to fill the screen.
private struct BillboardArtwork: View {
    let item: MediaItem
    let store: LuminaStore
    var showsPoster = false
    var home = false
    var motionDirection: Int? = nil
    @State private var framing: HeroFraming?
    @State private var loadedURL: URL?
    @State private var source: UIImage?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var backdropURL: URL? { store.backdropURL(for: item) }
    private var url: URL? { backdropURL ?? store.imageURL(for: item, maxWidth: 1920) }
    private var displayedSource: UIImage? {
        loadedURL == url ? source : nil
    }

    private var isWide: Bool {
        backdropURL != nil || (displayedSource.map { $0.size.width / max(1, $0.size.height) >= 1.4 } ?? false)
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                LuminaTheme.theater
                if let source = displayedSource {
                    ZStack {
                        CroppedBillboardArtwork(
                            source: source,
                            framing: isWide ? framing : nil,
                            viewport: geometry.size,
                            home: home,
                            motionDirection: motionDirection
                        )
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
                    .transition(.identity)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .onDisappear { source = nil; loadedURL = nil }
        .task(id: url) {
            source = nil
            framing = nil
            loadedURL = nil
            guard let url else { return }
            async let proposedFraming = store.backdropFraming(for: item)
            let image = await ArtworkImageCache.shared.image(for: url)
            guard !Task.isCancelled, let image else { return }
            let chosenFraming = await proposedFraming
            guard !Task.isCancelled else { return }
            framing = chosenFraming
            source = UIImage(cgImage: image)
            loadedURL = url
        }
        #if os(iOS) && LUMINA_UI_TESTING
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Hero artwork bounds")
        .accessibilityIdentifier(home ? "homeHeroArtworkBounds" : "")
        .accessibilityHidden(!home)
        #else
        .accessibilityHidden(true)
        #endif
        .allowsHitTesting(false)
    }
}

/// Changes the normalized source crop so Home motion remains subject-safe instead of
/// scaling an already-cropped viewport. Portrait poster treatments remain stationary.
private struct CroppedBillboardArtwork: View {
    let source: UIImage
    let framing: HeroFraming?
    let viewport: CGSize
    let home: Bool
    let motionDirection: Int?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.luminaArtworkMotionEnabled) private var motionEnabled

    var body: some View {
        Group {
            if let motionDirection, motionEnabled, !reduceMotion, scenePhase == .active,
               HeroFraming.motionCrop(framing, source: source.size, viewport: viewport, home: home,
                                      progress: 1, direction: motionDirection)
                != HeroFraming.crop(framing, source: source.size, viewport: viewport, home: home) {
                Image(uiImage: source)
                    .resizable()
                    .phaseAnimator([0.0, 1.0]) { artwork, progress in
                        artwork.modifier(NormalizedArtworkCrop(
                            framing: framing,
                            source: source.size,
                            viewport: viewport,
                            home: home,
                            progress: progress,
                            direction: motionDirection
                        ))
                    } animation: { _ in
                        .easeInOut(duration: 18)
                    }
                    .transition(.identity)
            } else {
                Image(uiImage: source)
                    .resizable()
                    .modifier(NormalizedArtworkCrop(
                        framing: framing,
                        source: source.size,
                        viewport: viewport,
                        home: home,
                        progress: 0,
                        direction: nil
                    ))
                    .transition(.identity)
            }
        }
    }
}

private nonisolated struct NormalizedArtworkCrop: ViewModifier, Animatable {
    let framing: HeroFraming?
    let source: CGSize
    let viewport: CGSize
    let home: Bool
    var progress: Double
    let direction: Int?

    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }

    func body(content: Content) -> some View {
        let crop = if let direction {
            HeroFraming.motionCrop(
                framing,
                source: source,
                viewport: viewport,
                home: home,
                progress: progress,
                direction: direction
            )
        } else {
            HeroFraming.crop(framing, source: source, viewport: viewport, home: home)
        }
        content
            .frame(width: viewport.width / crop.width, height: viewport.height / crop.height)
            .offset(x: -crop.minX * viewport.width / crop.width, y: -crop.minY * viewport.height / crop.height)
            .frame(width: viewport.width, height: viewport.height, alignment: .topLeading)
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
    var showsContext = true
    var showsMetadata = true
    var showsProgress = true
    /// Detail heroes make the artwork the primary surface. Keep the readability
    /// treatment at the copy edge instead of dimming the whole image.
    var artworkFirst = false
    var featured = false
    var artworkMotionDirection: Int? = nil
    var compactMinimumHeight: CGFloat?
    var compactArtworkMinimumHeight: CGFloat?
    var wideMinimumHeight: CGFloat?
    var topContentClearance: CGFloat = 0
    var bottomContentClearance: CGFloat = 0
    var onOpen: ((MediaItem) -> Void)? = nil
    /// tvOS Home uses the focused title as its single browse surface. Detail
    /// billboards leave this nil so their remote directions remain unchanged.
    var onBrowse: ((Int) -> Void)? = nil
    @ViewBuilder let actions: Actions
    @State private var compactLayout = true
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorSchemeContrast) private var contrast
    #if !os(tvOS)
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 600
    @ScaledMetric(relativeTo: .caption) private var featureHeaderClearance: CGFloat = 16
    #endif

    var body: some View {
        Group {
            if compact {
                // Keep the title, actions, and artwork in one cinematic field on
                // compact displays. The copy stays with the artwork instead
                // of becoming a second, disconnected card below the artwork.
                copy
                    .foregroundStyle(.white)
                    .shadow(color: featured ? .black.opacity(0.75) : .clear, radius: 2, y: 1)
                    .frame(maxWidth: .infinity, minHeight: compactMinimumHeight ?? (featured ? 336 : 380), alignment: .bottomLeading)
                    .padding(.horizontal, LuminaTheme.contentGutter)
                    .padding(.top, topInset)
                    .padding(.bottom, featured ? 68 + bottomContentClearance : artworkFirst ? 44 : 24)
                    .frame(minHeight: compactArtworkMinimumHeight,
                           maxHeight: artworkFirst ? .infinity : nil,
                           alignment: artworkFirst ? .bottomLeading : .topLeading)
                    .background {
                        ZStack {
                            BillboardArtwork(item: item, store: store, showsPoster: true, home: featured, motionDirection: artworkMotionDirection)
                                .overlay(alignment: .topLeading) {
                                    if featured {
                                        LinearGradient(colors: [.black.opacity(0.45), .clear], startPoint: .top, endPoint: .bottom)
                                            .mask(LinearGradient(colors: [.black, .clear], startPoint: .leading, endPoint: .trailing))
                                            .frame(maxWidth: 720)
                                            .frame(height: 104)
                                            .allowsHitTesting(false)
                                    }
                                }
                            if !featured {
                                LinearGradient(stops: [
                                    .init(color: LuminaTheme.theater.opacity(contrast == .increased ? 0.92 : 0.16), location: 0),
                                    .init(color: LuminaTheme.theater.opacity(0.16), location: 0.40),
                                    .init(color: LuminaTheme.theater.opacity(0.96), location: 1),
                                ], startPoint: .top, endPoint: .bottom)
                            }
                            if !artworkFirst && !featured {
                                LinearGradient(colors: [LuminaTheme.theater.opacity(0.64), .clear], startPoint: .leading, endPoint: .trailing)
                            }
                        }
                        #if !os(tvOS)
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        #endif
                    }
            } else {
                copy
                    .foregroundStyle(.white)
                    .shadow(color: featured ? .black.opacity(0.75) : .clear, radius: 2, y: 1)
                    .frame(maxWidth: copyWidth, alignment: .leading)
                    .padding(.horizontal, contentInset)
                    .padding(.top, topInset)
                    .padding(.bottom, artworkFirst ? 44 : 24)
                    .frame(maxWidth: .infinity, minHeight: minimumHeight,
                           maxHeight: artworkFirst ? .infinity : nil,
                           alignment: .bottomLeading)
                    .background {
                        ZStack {
                            BillboardArtwork(item: item, store: store, showsPoster: true, home: featured, motionDirection: artworkMotionDirection)
                                .overlay(alignment: .topLeading) {
                                    if featured {
                                        LinearGradient(colors: [.black.opacity(0.45), .clear], startPoint: .top, endPoint: .bottom)
                                            .mask(LinearGradient(colors: [.black, .clear], startPoint: .leading, endPoint: .trailing))
                                            .frame(maxWidth: 720)
                                            .frame(height: 104)
                                            .allowsHitTesting(false)
                                    }
                                }
                            if !artworkFirst && !featured {
                                LinearGradient(stops: [
                                    .init(color: LuminaTheme.theater.opacity(contrast == .increased ? 0.96 : 0.72), location: 0),
                                    .init(color: LuminaTheme.theater.opacity(0.30), location: 0.48),
                                    .init(color: .clear, location: 0.76),
                                ], startPoint: .leading, endPoint: .trailing)
                            }
                            if !featured {
                                LinearGradient(stops: [
                                    .init(color: .clear, location: 0.35),
                                    .init(color: LuminaTheme.theater.opacity(0.4), location: 0.8),
                                    .init(color: LuminaTheme.theater, location: 1),
                                ], startPoint: .top, endPoint: .bottom)
                            }
                        }
                        #if !os(tvOS)
                        .ignoresSafeArea(.container, edges: [.top, .horizontal])
                        #endif
                    }
            }
        }
        #if !os(tvOS)
        .onGeometryChange(for: Bool.self) { $0.size.width < minimumWideWidth } action: { compactLayout = $0 }
        #endif
        .tint(.white)
    }

    private var copy: some View {
        VStack(alignment: .leading, spacing: copySpacing) {
            if showsContext {
                Text(context)
                    .font(.system(size: compact ? 11 : labelSize, weight: .semibold))
                    .tracking(2)
                    .textCase(.uppercase)
                    .foregroundStyle(.white.opacity(0.85))
            }
            title
            if featured, let subtitle = item.homeHeroSubtitle {
                Text(subtitle).font(.subheadline.weight(.medium))
                    .foregroundStyle(.white.opacity(0.85)).lineLimit(2)
                    .accessibilityIdentifier("featuredEpisodeSubtitle")
            }
            if showsMetadata {
                MediaMetadata(item: item, onArtwork: true)
            }
            if showsOverview && !(featured && compact), let overview = item.overview, !overview.isEmpty {
                Text(overview)
                    .font(LuminaTheme.editorial(bodySize, relativeTo: .body))
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if showsProgress, item.resumeSeconds > 0, item.durationSeconds > item.resumeSeconds {
                VStack(alignment: .leading, spacing: 6) {
                    GeometryReader { geometry in
                        Rectangle().fill(Color.white.opacity(0.2))
                        Rectangle().fill(LuminaTheme.brand)
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

    @ViewBuilder private var title: some View {
        if let onOpen {
            Button { onOpen(item) } label: { titleArtwork }
                #if os(tvOS)
                .buttonStyle(BillboardTitleButtonStyle())
                #else
                .buttonStyle(.plain)
                #endif
                .accessibilityLabel(featured ? item.homeHeroTitle : item.name)
                .accessibilityHint(featured && onBrowse != nil
                    ? "Open details. Swipe left or right to browse featured titles."
                    : "Open details")
                .accessibilityIdentifier("featuredTitleButton")
                #if os(tvOS)
                .onMoveCommand { direction in
                    guard featured, let onBrowse else { return }
                    switch direction {
                    case .left: onBrowse(-1)
                    case .right: onBrowse(1)
                    default: break
                    }
                }
                #endif
        } else {
            titleArtwork
        }
    }

    private var titleArtwork: some View {
        MediaTitleArtwork(name: featured ? item.homeHeroTitle : item.name, url: store.logoURL(for: item),
                          maximumWidth: (compact ? 280 : 440) * 0.8,
                          maximumHeight: (compact ? 88 : 140) * 0.8) {
            titleText
        }
    }

    private var titleText: some View {
        Text(featured ? item.homeHeroTitle : item.name)
            .font(titleFont)
            .tracking(-0.6)
            .lineLimit(nil)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.isHeader)
            .accessibilityIdentifier(featured ? "featuredTitle" : "detailTitle")
    }
    private var titleFont: Font {
        #if os(tvOS)
        LuminaTheme.editorial((featured ? 54 : 64) * 0.8, relativeTo: .largeTitle, italic: true).weight(.bold)
        #else
        LuminaTheme.editorial((compact ? 34 : 58) * (featured ? 0.75 : 1) * 0.8, relativeTo: .largeTitle, italic: true).weight(.bold)
        #endif
    }
    private var topInset: CGFloat {
        #if os(tvOS)
        32
        #else
        featured ? max(featureHeaderClearance, topContentClearance) : compact ? 88 : 32
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
        820
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
        LuminaTheme.contentGutter
        #endif
    }
    private var minimumHeight: CGFloat {
        #if os(tvOS)
        wideMinimumHeight ?? 560
        #else
        wideMinimumHeight ?? (featured ? 480 : 420)
        #endif
    }
}

/// Display the supplied title treatment intact, retaining readable copy while it loads or if it fails.
struct MediaTitleArtwork<Fallback: View>: View {
    let name: String
    let url: URL?
    var maximumWidth: CGFloat = 320
    var maximumHeight: CGFloat = 100
    @ViewBuilder let fallback: Fallback

    var body: some View {
        CachedArtworkImage(url: url) { image in
            image
                .resizable()
                .renderingMode(.original)
                .scaledToFit()
                .frame(maxWidth: maximumWidth, maxHeight: maximumHeight, alignment: .leading)
                .accessibilityElement(children: .ignore)
                .accessibilityHidden(false)
                .accessibilityLabel(name)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("titleLogoArtwork")
        } placeholder: {
            fallback
        }
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
    var showsOverview = true
    var showsContext = true
    var showsMetadata = true
    var showsProgress = true
    var compactMinimumHeight: CGFloat?
    var compactArtworkMinimumHeight: CGFloat?
    var wideMinimumHeight: CGFloat?
    var topContentClearance: CGFloat = 0
    var bottomContentClearance: CGFloat = 0
    var onOpen: ((MediaItem) -> Void)? = nil
    /// An optional owner for the chosen hero. tvOS keeps this in its tab root
    /// so Back returns to the title the viewer last browsed; iOS keeps the
    /// existing local selection by default.
    var selection: Binding<String?>? = nil
    @ViewBuilder let actions: (MediaItem) -> Actions
    @State private var selectedID: String?
    @State private var movingForward = true
    @State private var hovered = false
    @State private var dragging = false
    @State private var advanceNotBefore = Date.distantPast
    @State private var compactLayout = true
    @FocusState private var focusedID: String?
    #if os(tvOS)
    @FocusedValue(\.featuredBillboard) private var billboardHasFocus
    #endif
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.luminaArtworkMotionEnabled) private var motionEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    #if !os(tvOS)
    @ScaledMetric(relativeTo: .largeTitle) private var minimumWideWidth: CGFloat = 600
    #endif

    private var selectedIdentifier: String? { selection?.wrappedValue ?? selectedID }
    private var selected: MediaItem? { items.first { $0.id == selectedIdentifier } ?? items.first }
    private var index: Int { items.firstIndex { $0.id == selected?.id } ?? 0 }
    private var canAdvance: Bool {
        items.count > 1 && motionEnabled && !reduceMotion && !voiceOver && scenePhase == .active
            && !isInteracting && !containsFocus && !hovered && !dragging
    }
    private var containsFocus: Bool {
        #if os(tvOS)
        billboardHasFocus == true || focusedID != nil
        #else
        focusedID != nil
        #endif
    }
    private var timerKey: String { "\(selected?.id ?? "")-\(canAdvance)-\(advanceNotBefore.timeIntervalSinceReferenceDate)" }
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
                MediaBillboard(
                    item: selected,
                    store: store,
                    context: selected.resumeSeconds > 0 ? "Continue watching" : "New in your library",
                    showsOverview: showsOverview,
                    showsContext: showsContext,
                    showsMetadata: showsMetadata,
                    showsProgress: showsProgress,
                    featured: true,
                    artworkMotionDirection: isInteracting ? nil : index,
                    compactMinimumHeight: compactMinimumHeight,
                    compactArtworkMinimumHeight: compactArtworkMinimumHeight,
                    wideMinimumHeight: wideMinimumHeight,
                    topContentClearance: topContentClearance,
                    bottomContentClearance: bottomContentClearance,
                    onOpen: onOpen,
                    onBrowse: { direction in step(direction) }
                ) {
                    actions(selected)
                }
                #if !os(tvOS)
                .id(selected.id)
                .transition(reduceMotion ? .identity : .asymmetric(
                    insertion: .move(edge: movingForward ? .trailing : .leading),
                    removal: .move(edge: movingForward ? .leading : .trailing)
                ))
                #endif
            }
            // On iPhone Duo the artwork is allowed to become the canvas under
            // the system rail. Only tvOS needs this local transition clip.
            #if os(tvOS)
            .clipped()
            #endif
            .overlay(alignment: .bottom) {
                if items.count > 1 {
                    controls
                        .frame(maxWidth: .infinity, alignment: compact ? .center : .trailing)
                        .padding(.horizontal, compact ? 12 : controlInset)
                        .padding(.bottom, 8 + (compact ? bottomContentClearance : 0))
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
            .simultaneousGesture(TapGesture().onEnded { deferAutomaticAdvance() })
            .gesture(TapGesture().onEnded { onOpen?(selected) }, including: .gesture)
            #endif
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Featured titles, \(index + 1) of \(items.count)")
            .task(id: selected.id) {
                guard items.count > 1 else { return }
                let next = items[(index + 1) % items.count]
                if let url = store.backdropURL(for: next) ?? store.imageURL(for: next, maxWidth: 1920) {
                    // Keep the decoded rendition ready for either remote browsing
                    // or the automatic carousel; raw URLSession bytes would not
                    // help a newly created Home artwork view.
                    _ = await ArtworkImageCache.shared.image(for: url)
                }
            }
            .task(id: timerKey) {
                guard canAdvance else { return }
                let delay = max(8, advanceNotBefore.timeIntervalSinceNow)
                do { try await Task.sleep(for: .seconds(delay)) } catch { return }
                guard canAdvance, !Task.isCancelled else { return }
                step(1, automatic: true)
            }
            .onAppear { seedSelectionIfNeeded() }
            .onChange(of: items.map(\.id)) { _, _ in seedSelectionIfNeeded() }
            .onChange(of: focusedID) {
                #if os(tvOS)
                if let focusedID, items.contains(where: { $0.id == focusedID }) { select(focusedID) }
                #endif
            }
        }
    }

    private var controls: some View {
        HStack(spacing: 2) {
            if !compact { stepButton("Previous featured title", glyph: .back, direction: -1) }
            #if os(tvOS)
            // Home supplies at most eight titles. Keeping their controls in one
            // row lets the remote leave the last title for Next.
            featureChoices
            #else
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) { featureChoices }
                .onChange(of: selectedIdentifier) {
                    if let selectedIdentifier { proxy.scrollTo(selectedIdentifier, anchor: .center) }
                }
            }
            .frame(width: CGFloat(min(items.count, compact ? 5 : 6)) * (controlSize + 2))
            .fixedSize(horizontal: false, vertical: true)
            #endif
            if !compact { stepButton("Next featured title", glyph: .forward, direction: 1) }

        }
        .foregroundStyle(.white)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("featuredControls")
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
    private func stepButton(_ label: String, glyph: LuminaGlyph, direction: Int) -> some View {
        let id = direction < 0 ? "previous" : "next"
        return Button { step(direction) } label: {
            LuminaIcon(glyph: glyph, size: 16)
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
    private func deferAutomaticAdvance() {
        // A chosen title stays put while the member considers its actions.
        // One cancellable sleep resumes the slideshow without polling.
        advanceNotBefore = Date().addingTimeInterval(30)
    }
    private func step(_ direction: Int, automatic: Bool = false) {
        guard !items.isEmpty else { return }
        select(items[(index + direction + items.count) % items.count].id,
               forward: direction > 0, userInitiated: !automatic)
    }
    private func select(_ id: String, forward: Bool? = nil, userInitiated: Bool = true) {
        if userInitiated { deferAutomaticAdvance() }
        guard id != selected?.id else { return }
        movingForward = forward ?? ((items.firstIndex { $0.id == id } ?? 0) > index)
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.72)) {
            if let selection {
                selection.wrappedValue = id
            } else {
                selectedID = id
            }
        }
    }

    private func seedSelectionIfNeeded() {
        guard let first = items.first,
              !items.contains(where: { $0.id == selectedIdentifier }) else { return }
        if let selection {
            selection.wrappedValue = first.id
        } else if selectedID != nil {
            selectedID = first.id
        }
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

/// Album art leads the music library; its caption stays on the artwork.
struct MusicCollectionSpotlight: View {
    let item: MediaItem
    let store: LuminaStore
    var minimumHeight: CGFloat = 460
    var topContentClearance: CGFloat = 0
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.name)
                .font(LuminaTheme.editorial(28, relativeTo: .title2))
                .fixedSize(horizontal: false, vertical: true)
            if !item.displaySubtitle.isEmpty {
                Text(item.displaySubtitle)
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Label("Open album", glyph: .forward)
                .font(.callout.weight(.semibold))
                .padding(.top, 8)
        }
        .foregroundStyle(.white)
        .multilineTextAlignment(.leading)
        .padding(.horizontal, horizontalInset)
        .padding(.top, topContentClearance + 32)
        .padding(.bottom, 32)
        .frame(maxWidth: .infinity, minHeight: minimumHeight, alignment: .bottomLeading)
        .background {
            GeometryReader { geometry in
                CachedArtworkImage(url: store.imageURL(for: item, maxWidth: 1920)) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    LuminaTheme.theater.overlay {
                        LuminaIcon(glyph: .music, variant: .solid, size: 40).foregroundStyle(.white.opacity(0.5))
                    }
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
                .clipped()
                .overlay {
                    LinearGradient(stops: [
                        .init(color: .black.opacity(contrast == .increased ? 0.8 : 0.66), location: 0),
                        .init(color: .black.opacity(contrast == .increased ? 0.7 : 0.6), location: 0.22),
                        .init(color: .clear, location: 0.5),
                        .init(color: .black.opacity(0.86), location: 1),
                    ], startPoint: .top, endPoint: .bottom)
                }
            }
            .accessibilityHidden(true)
        }
    }

    private var horizontalInset: CGFloat {
        #if os(tvOS)
        80
        #else
        LuminaTheme.contentGutter
        #endif
    }
}

/// Compact media actions use the web gallery's neutral ink/paper hierarchy.
struct LuminaMediaActionStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    var primary = false
    func makeBody(configuration: Configuration) -> some View {
        let foreground: Color = configuration.role == .destructive ? .red : primary ? LuminaTheme.onPrimaryControl : .white
        return configuration.label
            .font(.subheadline.weight(.semibold))
            .imageScale(.small)
            .padding(.horizontal, 16)
            .frame(minWidth: 44, minHeight: 44)
            .foregroundStyle(foreground)
            .tint(foreground)
            .background(primary ? LuminaTheme.primaryControlFill : Color.white.opacity(configuration.isPressed ? 0.14 : 0), in: .rect(cornerRadius: 10))
            .opacity(isEnabled ? (configuration.isPressed ? 0.78 : 1) : 0.45)
            .contentShape(.rect(cornerRadius: 10))
    }
}

#if os(tvOS)
private struct BillboardTitleButtonStyle: ButtonStyle {
    @Environment(\.isFocused) private var isFocused

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.7 : 1)
            .overlay(alignment: .bottomLeading) {
                Rectangle().fill(isFocused ? .white : .clear)
                    .frame(height: 2).offset(y: 8)
            }
    }
}
#endif

/// Category and section navigation uses one underline, without a button plate.
struct LuminaUnderlineTabStyle: ButtonStyle {
    var selected = false
    var alignsWithMasthead = false
    @Environment(\.isFocused) private var isFocused
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            #if os(tvOS)
            .font(.system(size: 24, weight: selected ? .semibold : .medium))
            .frame(minWidth: 64, minHeight: 64)
            #else
            .font(.body.weight(selected ? .semibold : .medium))
            .offset(y: alignsWithMasthead
                ? (UIFont.preferredFont(forTextStyle: .body).descender
                    - UIFont.preferredFont(forTextStyle: .caption1).descender)
                : 0)
            .frame(minWidth: 44, minHeight: 44)
            #endif
            .foregroundStyle(LuminaTheme.ink.opacity(selected || isFocused ? 1 : 0.8))
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(isFocused ? LuminaTheme.ink : selected ? LuminaTheme.brand : Color.clear)
                    .frame(height: isFocused ? 3 : 2)
            }
            .opacity(isEnabled ? configuration.isPressed ? 0.65 : 1 : 0.4)
            .contentShape(.rect)
    }
}

/// Secondary actions stay quiet at rest while keeping an unmistakable TV focus state.
struct LuminaQuietActionStyle: ButtonStyle {
    @Environment(\.isFocused) private var isFocused
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.colorSchemeContrast) private var contrast
    var primary = false
    var selected = false
    var mediaOverlay = false

    func makeBody(configuration: Configuration) -> some View {
        let foreground: Color = configuration.role == .destructive ? .red : isFocused ? LuminaTheme.theater : primary ? LuminaTheme.onPrimaryControl : Color.primary
        return configuration.label
            .font(.body.weight(primary || selected ? .semibold : .regular))
            .padding(.horizontal, 16)
            #if os(tvOS)
            .frame(minWidth: 64, minHeight: 64)
            #else
            .frame(minWidth: 44, minHeight: 44)
            #endif
            .foregroundStyle(foreground)
            .tint(foreground)
            .background(isFocused ? Color.white : primary ? LuminaTheme.primaryControlFill : mediaOverlay ? Color.black.opacity(0.72) : Color.primary.opacity(configuration.isPressed ? 0.18 : selected ? 0.12 : 0), in: .rect(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(isFocused ? Color.white : contrast == .increased ? Color.primary : Color.clear, lineWidth: 2)
            }
            .opacity(isEnabled ? (configuration.isPressed ? 0.75 : 1) : 0.45)
            .contentShape(.rect(cornerRadius: 10))
    }
}

#if os(iOS)
/// Cards keep their resting inset while their scroll viewport reaches the bezel.
struct LuminaCarouselEdges: ViewModifier {
    var inset: CGFloat = LuminaTheme.contentGutter

    func body(content: Content) -> some View {
        content
            .contentMargins(.horizontal, inset, for: .scrollContent)
            .padding(.horizontal, -inset)
            .ignoresSafeArea(.container, edges: .horizontal)
            .scrollClipDisabled()
    }
}
#endif

/// Keeps short category menus centered and long menus reachable by scrolling.
struct LuminaCategoryBar<Content: View>: View {
    var selection: String? = nil
    var centersContent = true
    @ViewBuilder let content: Content
    #if os(iOS) && LUMINA_UI_TESTING
    @State private var uiTestingMaxVerticalPull: CGFloat = 0
    #endif

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 20) {
                    content
                }
                #if os(iOS)
                .padding(.horizontal, LuminaTheme.contentGutter)
                #else
                .padding(.horizontal, 2)
                #endif
            }
            #if os(iOS)
            .scrollBounceBehavior(.basedOnSize, axes: .vertical)
            .defaultScrollAnchor(.leading, for: .initialOffset)
            .defaultScrollAnchor(centersContent ? .center : .leading, for: .alignment)
            .fixedSize(horizontal: false, vertical: true)
            #if LUMINA_UI_TESTING
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                abs(geometry.contentOffset.y + geometry.contentInsets.top)
            } action: { _, distance in
                uiTestingMaxVerticalPull = max(uiTestingMaxVerticalPull, distance)
            }
            .accessibilityValue("verticalPull=\(uiTestingMaxVerticalPull)")
            #endif
            #else
            .scrollClipDisabled()
            #endif
            .onAppear { revealSelection(using: proxy) }
            .onChange(of: selection) { revealSelection(using: proxy) }
        }
    }

    private func revealSelection(using proxy: ScrollViewProxy) {
        guard let selection else { return }
        proxy.scrollTo(selection, anchor: .center)
    }
}

enum HomeBrowsingDestination: String, CaseIterable, Hashable {
    case all
    case movies
    case tv
    case anime
    case music
    case books
    case watchlist

    var title: String {
        switch self {
        case .all: "Home"
        case .movies: "Movies"
        case .tv: "TV"
        case .anime: "Anime"
        case .music: "Music"
        case .books: "Books"
        case .watchlist: "Watchlist"
        }
    }

    var homeCategory: HomeBrowseCategory? {
        switch self {
        case .all: .all
        case .watchlist: .watchlist
        case .movies, .tv, .anime, .music, .books: nil
        }
    }
}

/// Watchlist refines Home in place. Library destinations remain neutral
/// commands so returning to Home preserves the member's current Home selection.
struct HomeBrowsingRow: View {
    let category: HomeBrowseCategory
    var centersContent = true
    var destinations = HomeBrowsingDestination.allCases.filter { $0 != .all }
    let onSelect: (HomeBrowsingDestination) -> Void

    var body: some View {
        LuminaCategoryBar(selection: destinations.first { $0.homeCategory == category }?.rawValue,
                          centersContent: centersContent) {
            ForEach(destinations, id: \.self) { option in
                let selected = option.homeCategory == category
                Button { onSelect(option) } label: { Text(option.title) }
                    .buttonStyle(LuminaUnderlineTabStyle(selected: selected, alignsWithMasthead: true))
                    .accessibilityValue(selected ? "Selected" : "")
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .accessibilityIdentifier("homeBrowse-\(option.rawValue)")
                    .id(option.rawValue)
            }
        }
        .accessibilityIdentifier("homeBrowsingRow")
    }
}

/// The cover supplies the album page's atmosphere without another artwork request or disk cache.
struct AlbumArtworkBackground: ViewModifier {
    let url: URL?
    var enabled = true
    @State private var palette: AlbumArtworkColor?
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        content
            .environment(\.colorScheme, enabled ? .dark : colorScheme)
            .background {
                Group {
                    if enabled, let palette {
                        Color(red: palette.red, green: palette.green, blue: palette.blue)
                    } else {
                        LuminaTheme.paper
                    }
                }
                .ignoresSafeArea()
            }
            .task(id: enabled ? url : nil) {
                palette = nil
                guard enabled, let url, let image = await ArtworkImageCache.shared.image(for: url),
                      !Task.isCancelled else { return }
                palette = AlbumArtworkColor(image: image)
            }
    }
}
