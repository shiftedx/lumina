import Foundation

/// A public, release-only slide for the sign-in experience.
///
/// Artwork URLs are accepted only when they use Lumina's signed public showcase route.
public struct PublicShowcaseSlide: Identifiable, Sendable {
    public let title: String
    public let kind: String
    public let section: String?
    public let backdropURL: URL
    public let posterURL: URL?
    public let id: String

    public init(title: String, kind: String, section: String?, backdropURL: URL, posterURL: URL?, id: String) {
        self.title = title
        self.kind = kind
        self.section = section
        self.backdropURL = backdropURL
        self.posterURL = posterURL
        self.id = id
    }
}

extension LuminaClient {
    func publicShowcase(origin: URL) async throws -> [PublicShowcaseSlide] {
        guard let endpoint = url(origin: origin, path: "/api/public/showcase") else {
            throw LuminaError.invalidOrigin
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"
        request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let response: PublicShowcaseResponse
        do {
            response = try JSONDecoder().decode(PublicShowcaseResponse.self, from: try await send(request))
        } catch let error as LuminaError {
            throw error
        } catch {
            throw LuminaError.malformedResponse
        }

        var seenArtwork = Set<URL>()
        var acceptedSlides = [PublicShowcaseSlide]()
        acceptedSlides.reserveCapacity(12)

        for slide in response.slides where acceptedSlides.count < 12 {
            guard let backdropURL = publicShowcaseArtworkURL(origin: origin, relativePath: slide.backdropURL),
                  seenArtwork.insert(backdropURL).inserted else {
                continue
            }
            acceptedSlides.append(PublicShowcaseSlide(
                title: slide.title,
                kind: slide.kind,
                section: slide.section,
                backdropURL: backdropURL,
                posterURL: slide.posterURL.flatMap { publicShowcaseArtworkURL(origin: origin, relativePath: $0) },
                id: backdropURL.absoluteString
            ))
        }
        return acceptedSlides
    }
}

private struct PublicShowcaseResponse: Decodable {
    let slides: [PublicShowcaseWireSlide]
}

private struct PublicShowcaseWireSlide: Decodable {
    let title: String
    let kind: String
    let section: String?
    let backdropURL: String
    let posterURL: String?

    enum CodingKeys: String, CodingKey {
        case title, kind, section
        case backdropURL = "backdrop_url"
        case posterURL = "poster_url"
    }
}

private func publicShowcaseArtworkURL(origin: URL, relativePath: String) -> URL? {
    let prefix = "/api/public/showcase/art/"
    guard relativePath.hasPrefix(prefix) else { return nil }
    let token = relativePath.dropFirst(prefix.count)
    guard token.utf8.count == 22,
          token.utf8.allSatisfy({ byte in
              (65...90).contains(byte) || (97...122).contains(byte) || (48...57).contains(byte) || byte == 45 || byte == 95
          }) else {
        return nil
    }
    return URL(string: relativePath, relativeTo: origin)?.absoluteURL
}
