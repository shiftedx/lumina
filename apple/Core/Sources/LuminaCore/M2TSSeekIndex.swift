import Foundation
@preconcurrency import PrismCore

/// A server-owned, representation-bound seek index for a direct M2TS source.
public struct M2TSSeekIndexReference: Sendable, Equatable {
    public let url: URL
    public let sourceID: String

    public init(url: URL, sourceID: String) {
        self.url = url
        self.sourceID = sourceID.lowercased()
    }

    /// A missing, stale, malformed, or unreachable index is only an optimization miss.
    /// Authorization and cancellation still belong to the caller's normal playback path.
    func sourceOpenHints(for originalURL: URL) async throws -> SourceOpenHints? {
        guard Self.validSourceID(sourceID), Self.safeSameOrigin(url, originalURL) else { return nil }
        let redirectGuard = M2TSSeekIndexRedirectGuard(origin: originalURL)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        let session = URLSession(configuration: configuration, delegate: redirectGuard, delegateQueue: nil)
        defer { session.invalidateAndCancel() }

        do {
            while true {
                try Task.checkCancellation()
                guard let response = try await Self.fetch(url, session: session, origin: originalURL) else { return nil }
                guard response.sourceID.lowercased() == sourceID else { return nil }
                switch response.status {
                case .pending:
                    try await Task.sleep(for: .milliseconds(500))
                case .unsupported:
                    return nil
                case .complete:
                    return response.hints()
                }
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as LuminaError where error == .http(401) || error == .http(403) {
            throw error
        } catch {
            if Task.isCancelled { throw CancellationError() }
            return nil
        }
    }

    private static let maximumResponseBytes = 1 << 20

    private static func fetch(
        _ url: URL,
        session: URLSession,
        origin: URL
    ) async throws -> M2TSSeekIndexDocument? {
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse,
              let responseURL = response.url,
              safeSameOrigin(responseURL, origin) else { return nil }
        switch response.statusCode {
        case 200..<300:
            break
        case 401, 403:
            throw LuminaError.http(response.statusCode)
        case 404:
            return nil
        default:
            return nil
        }
        if let length = response.value(forHTTPHeaderField: "Content-Length"),
           let bytes = Int(length), bytes > maximumResponseBytes {
            return nil
        }
        var data = Data()
        for try await byte in bytes {
            guard data.count < maximumResponseBytes else { return nil }
            data.append(byte)
        }
        return try? JSONDecoder().decode(M2TSSeekIndexDocument.self, from: data)
    }

    private static func validSourceID(_ value: String) -> Bool {
        value.utf8.count == 32 && value.unicodeScalars.allSatisfy {
            ($0.value >= 48 && $0.value <= 57) || ($0.value >= 97 && $0.value <= 102)
        }
    }

    fileprivate static func safeSameOrigin(_ left: URL, _ right: URL) -> Bool {
        guard left.user == nil, left.password == nil,
              right.user == nil, right.password == nil,
              left.fragment == nil, right.fragment == nil else { return false }
        return sameOrigin(left, right)
    }
}

private final class M2TSSeekIndexRedirectGuard: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let origin: URL

    init(origin: URL) { self.origin = origin }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        guard let target = request.url,
              M2TSSeekIndexReference.safeSameOrigin(target, origin) else {
            completionHandler(nil)
            task.cancel()
            return
        }
        completionHandler(request)
    }
}

private struct M2TSSeekIndexDocument: Decodable {
    enum Status: String, Decodable {
        case pending
        case complete
        case unsupported
    }

    let status: Status
    let sourceID: String
    let version: Int?
    let validator: String?
    let sourceSize: Int64?
    let basePID: Int?
    let enhancementPID: Int?
    let streamIndex: Int32?
    let timeBaseNum: Int32?
    let timeBaseDen: Int32?
    let keyframePTS: [Int64]?
    let packetOffsets: [Int64]?

    enum CodingKeys: String, CodingKey {
        case status = "Status"
        case sourceID = "SourceId"
        case version = "Version"
        case validator = "Validator"
        case sourceSize = "SourceSize"
        case basePID = "BasePID"
        case enhancementPID = "EnhancementPID"
        case streamIndex = "StreamIndex"
        case timeBaseNum = "TimeBaseNum"
        case timeBaseDen = "TimeBaseDen"
        case keyframePTS = "KeyframePTS"
        case packetOffsets = "PacketOffsets"
    }

    func hints() -> SourceOpenHints? {
        guard version == 1,
              let validator, Self.strongETag(validator),
              let sourceSize, sourceSize > 0, sourceSize % 192 == 0,
              let basePID, (1..<0x1FFF).contains(basePID),
              let enhancementPID, (1..<0x1FFF).contains(enhancementPID), enhancementPID != basePID,
              let streamIndex, streamIndex >= 0,
              timeBaseNum == 1, timeBaseDen == 90_000,
              let keyframePTS, (2...20_000).contains(keyframePTS.count),
              let packetOffsets, packetOffsets.count == keyframePTS.count,
              zip(keyframePTS, keyframePTS.dropFirst()).allSatisfy({ $0 < $1 }),
              packetOffsets.allSatisfy({ $0 >= 0 && $0 < sourceSize && $0 % 192 == 0 }),
              zip(packetOffsets, packetOffsets.dropFirst()).allSatisfy({ $0 <= $1 })
        else { return nil }
        return SourceOpenHints(
            expectedValidator: validator,
            keyframes: SuppliedKeyframeMap(
                streamIndex: streamIndex,
                timeBaseNum: 1,
                timeBaseDen: 90_000,
                keyframePTS: keyframePTS,
                completeness: .complete,
                transportPID: Int32(basePID),
                sourceSize: sourceSize,
                packetOffsets: packetOffsets
            )
        )
    }

    private static func strongETag(_ value: String) -> Bool {
        value.utf8.count >= 3 && value.utf8.count <= 1024 &&
            value.first == "\"" && value.last == "\"" &&
            !value.uppercased().hasPrefix("W/") &&
            !value.contains(where: { $0.isNewline || $0 == "\r" })
    }
}
