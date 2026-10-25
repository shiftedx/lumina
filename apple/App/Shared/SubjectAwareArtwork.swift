import SwiftUI
import LuminaCore
import UIKit

/// Presents decoded artwork from Lumina's process cache and keeps loading work tied to the URL.
/// A remounted cell can synchronously reuse its decoded image instead of flashing its placeholder.
@MainActor
struct CachedArtworkImage<Content: View, Placeholder: View>: View {
    let url: URL?
    @ViewBuilder let content: (Image) -> Content
    @ViewBuilder let placeholder: () -> Placeholder
    @State private var source: UIImage?
    @State private var loadedURL: URL?

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
        .onDisappear { source = nil; loadedURL = nil }
        .task(id: url) {
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
    @State private var framing: HeroFraming?

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
        .onDisappear { source = nil; loadedURL = nil }
        .task(id: url) {
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
}
