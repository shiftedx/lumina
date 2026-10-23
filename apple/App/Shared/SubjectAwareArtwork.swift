import SwiftUI
import LuminaCore
import UIKit

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

    var body: some View {
        GeometryReader { geometry in
            if let source, loadedURL == url {
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
        .task(id: url) {
            source = nil
            loadedURL = nil
            framing = nil
            guard let url else { return }
            async let proposed: HeroFraming? = fit ? nil : loadFraming(url)
            do {
                let (data, response) = try await URLSession.shared.data(from: url)
                guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode),
                      data.count <= BillboardArtworkPreparation.maximumEncodedBytes, !Task.isCancelled else { return }
                let image = await Task.detached(priority: .userInitiated) {
                    BillboardArtworkPreparation.image(from: data)
                }.value
                let chosen = await proposed
                guard !Task.isCancelled, let image else { return }
                framing = chosen
                source = UIImage(cgImage: image)
                loadedURL = url
            } catch { /* Retain the artwork placeholder when the source is unavailable. */ }
        }
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }
}
