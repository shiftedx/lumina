import SwiftUI
import LuminaCore
import UIKit

extension EnvironmentValues {
    @Entry var luminaRetainsArtworkWhileHidden = false
    /// iOS increments this after trimming the process cache for backgrounding or memory pressure.
    /// Mounted views then discard their own decoded UIKit image references too.
    @Entry var luminaArtworkReclamationGeneration = 0
}

struct ArtworkLoadKey: Hashable {
    let url: URL?
    let reloadGeneration: Int

    init(url: URL?, reloadGeneration: Int = 0) {
        self.url = url
        self.reloadGeneration = reloadGeneration
    }
}

/// Presents decoded artwork from Lumina's process cache and keeps loading work tied to the URL.
/// A remounted cell can synchronously reuse its decoded image instead of flashing its placeholder.
@MainActor
struct CachedArtworkImage<Content: View, Placeholder: View>: View {
    let url: URL?
    @ViewBuilder let content: (Image) -> Content
    @ViewBuilder let placeholder: () -> Placeholder
    @State private var source: UIImage?
    @State private var loadedURL: URL?
    @Environment(\.luminaRetainsArtworkWhileHidden) private var retainsArtworkWhileHidden
    #if os(iOS)
    @Environment(\.luminaArtworkReclamationGeneration) private var artworkReclamationGeneration
    @Environment(\.scenePhase) private var scenePhase
    @State private var isVisible = false
    @State private var reloadGeneration = 0
    #endif

    private var loadKey: ArtworkLoadKey {
        #if os(iOS)
        ArtworkLoadKey(url: url, reloadGeneration: reloadGeneration)
        #else
        ArtworkLoadKey(url: url)
        #endif
    }

    private var displayedSource: UIImage? {
        if loadedURL == url, let source { return source }
        guard let url, let image = ArtworkImageCache.shared.cachedImage(for: url) else { return nil }
        return UIImage(cgImage: image)
    }

    var body: some View {
        Group {
            if let displayedSource {
                content(Image(uiImage: displayedSource))
            } else {
                placeholder()
            }
        }
        #if os(iOS)
        .onAppear {
            isVisible = true
            reloadArtworkIfVisible()
        }
        .onChange(of: artworkReclamationGeneration) { _, _ in
            reclaimArtwork()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { reclaimArtwork() }
            else if phase == .active { reloadArtworkIfVisible() }
        }
        #endif
        .onDisappear {
            #if os(iOS)
            isVisible = false
            #endif
            if !retainsArtworkWhileHidden { source = nil; loadedURL = nil }
        }
        .task(id: loadKey) {
            #if os(iOS)
            guard isVisible, scenePhase == .active else { return }
            #endif
            if loadedURL == url, source != nil { return }
            guard let url else {
                source = nil
                loadedURL = nil
                return
            }
            if let cached = ArtworkImageCache.shared.cachedImage(for: url) {
                source = UIImage(cgImage: cached)
                loadedURL = url
                return
            }
            guard let image = await ArtworkImageCache.shared.image(for: url), !Task.isCancelled else { return }
            source = UIImage(cgImage: image)
            loadedURL = url
        }
    }

    #if os(iOS)
    private func reclaimArtwork() {
        source = nil
        loadedURL = nil
        reloadGeneration &+= 1
    }

    private func reloadArtworkIfVisible() {
        guard isVisible, scenePhase == .active, source == nil else { return }
        reloadGeneration &+= 1
    }
    #endif
}

/// Catalog heroes use the same static crop as library billboards on both platforms.
@MainActor
struct SubjectAwareArtwork: View {
    let url: URL?
    var fit = false
    var home = false
    var loadFraming: @MainActor (URL) async -> HeroFraming? = { _ in nil }
    @State private var source: UIImage?
    @State private var loadedURL: URL?
    @Environment(\.luminaRetainsArtworkWhileHidden) private var retainsArtworkWhileHidden
    @State private var framing: HeroFraming?
    #if os(iOS)
    @Environment(\.luminaArtworkReclamationGeneration) private var artworkReclamationGeneration
    @Environment(\.scenePhase) private var scenePhase
    @State private var isVisible = false
    @State private var reloadGeneration = 0
    #endif

    private var loadKey: ArtworkLoadKey {
        #if os(iOS)
        ArtworkLoadKey(url: url, reloadGeneration: reloadGeneration)
        #else
        ArtworkLoadKey(url: url)
        #endif
    }

    private var displayedSource: UIImage? {
        if loadedURL == url, let source { return source }
        guard let url, let image = ArtworkImageCache.shared.cachedImage(for: url) else { return nil }
        return UIImage(cgImage: image)
    }

    var body: some View {
        GeometryReader { geometry in
            if let source = displayedSource {
                if fit {
                    Image(uiImage: source).resizable().scaledToFit()
                        .frame(width: geometry.size.width, height: geometry.size.height)
                } else {
                    let crop = HeroFraming.crop(framing, source: source.size, viewport: geometry.size, home: home)
                    Image(uiImage: source).resizable()
                        .frame(width: geometry.size.width / crop.width, height: geometry.size.height / crop.height)
                        .offset(x: -crop.minX * geometry.size.width / crop.width, y: -crop.minY * geometry.size.height / crop.height)
                        .frame(width: geometry.size.width, height: geometry.size.height, alignment: .topLeading)
                }
            }
        }
        .clipped()
        #if os(iOS)
        .onAppear {
            isVisible = true
            reloadArtworkIfVisible()
        }
        .onChange(of: artworkReclamationGeneration) { _, _ in
            reclaimArtwork()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { reclaimArtwork() }
            else if phase == .active { reloadArtworkIfVisible() }
        }
        #endif
        .onDisappear {
            #if os(iOS)
            isVisible = false
            #endif
            if !retainsArtworkWhileHidden { source = nil; loadedURL = nil }
        }
        .task(id: loadKey) {
            #if os(iOS)
            guard isVisible, scenePhase == .active else { return }
            #endif
            if loadedURL == url, source != nil { return }
            source = nil
            loadedURL = nil
            framing = nil
            guard let url else { return }
            async let proposed: HeroFraming? = fit ? nil : loadFraming(url)
            let image = await ArtworkImageCache.shared.image(for: url)
            let chosen = await proposed
            guard !Task.isCancelled, let image else { return }
            framing = chosen
            source = UIImage(cgImage: image)
            loadedURL = url
        }
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }

    #if os(iOS)
    private func reclaimArtwork() {
        source = nil
        loadedURL = nil
        framing = nil
        reloadGeneration &+= 1
    }

    private func reloadArtworkIfVisible() {
        guard isVisible, scenePhase == .active, source == nil else { return }
        reloadGeneration &+= 1
    }
    #endif
}
