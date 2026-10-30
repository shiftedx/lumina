#if os(macOS)
import Foundation
import Network

/// Synthetic range server with paced chunks; no cache or personal media.
final class ProgressiveAudioHTTPFixture: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "lumina.test.progressive-http")
    private let lock = NSLock()
    private let bytes: Data
    private let delay: Int
    private let mime: String
    private let ranges: Bool
    private var status: Int
    private let redirect: URL?
    private let advertisedLength: Int?
    private let relativeRedirect: Bool
    private let etag: String?
    private let seekIndexResponses: [Data]
    private var connections: [ObjectIdentifier: NWConnection] = [:]
    private var delivered = 0
    private var requests = 0
    private var rangeStarts: [Int] = []
    private var credentials = false
    private var seekIndexRequests = 0
    var receivedCredentials: Bool { lock.withLock { credentials } }
    var deliveredBytes: Int { lock.withLock { delivered } }
    var activeConnections: Int { lock.withLock { connections.count } }
    var requestCount: Int { lock.withLock { requests } }
    var seekIndexRequestCount: Int { lock.withLock { seekIndexRequests } }
    /// Test-only source-range evidence. A missing Range header records zero.
    var requestedRangeStarts: [Int] { lock.withLock { rangeStarts } }
    func reject(_ code: Int) { lock.withLock { status = code } }

    init(bytes: Data, mime: String = "audio/wav", delay: Int = 20, ranges: Bool = true, status: Int = 200, redirect: URL? = nil, advertisedLength: Int? = nil, relativeRedirect: Bool = false, etag: String? = nil, seekIndexResponses: [Data] = []) throws {
        self.bytes = bytes; self.mime = mime; self.delay = delay; self.ranges = ranges
        self.status = status; self.redirect = redirect; self.advertisedLength = advertisedLength; self.relativeRedirect = relativeRedirect
        self.etag = etag; self.seekIndexResponses = seekIndexResponses
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
    }

    func start() async throws -> URL {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            let id = ObjectIdentifier(connection)
            lock.withLock { connections[id] = connection }
            connection.stateUpdateHandler = { [weak self] state in
                if case .cancelled = state { self?.remove(id) }
                if case .failed = state { connection.cancel() }
            }
            connection.start(queue: queue)
            connection.receive(minimumIncompleteLength: 1, maximumLength: 8_192) { [weak self] data, _, _, _ in
                guard let self, let data else { connection.cancel(); return }
                lock.withLock { requests += 1 }
                let headers = String(decoding: data, as: UTF8.self).components(separatedBy: "\r\n")
                lock.withLock { credentials = credentials || headers.contains { $0.lowercased().hasPrefix("cookie:") || $0.lowercased().hasPrefix("authorization:") } }
                let target = headers[0].split(separator: " ", omittingEmptySubsequences: true).dropFirst().first
                if target?.split(separator: "?", maxSplits: 1).first == "/seek-index" {
                    let response = lock.withLock {
                        let index = min(seekIndexRequests, max(0, seekIndexResponses.count - 1))
                        seekIndexRequests += 1
                        return seekIndexResponses.indices.contains(index) ? seekIndexResponses[index] : nil
                    }
                    guard let response else {
                        connection.send(content: Data("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8), completion: .contentProcessed { _ in connection.cancel() })
                        return
                    }
                    let etagHeader = etag.map { "ETag: \($0)\r\n" } ?? ""
                    let reply = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(response.count)\r\n\(etagHeader)Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                    connection.send(content: Data(reply.utf8) + response, completion: .contentProcessed { _ in connection.cancel() })
                    return
                }
                let status = lock.withLock { self.status }
                if status != 200 || redirect != nil || (relativeRedirect && headers[0].contains("/redirect ")) {
                    let target = redirect?.absoluteString ?? "/tone.wav"
                    let code = redirect != nil || relativeRedirect ? 302 : status
                    let reply = "HTTP/1.1 \(code) Test\r\nContent-Length: 0\r\nLocation: \(target)\r\nConnection: close\r\n\r\n"
                    connection.send(content: Data(reply.utf8), completion: .contentProcessed { _ in connection.cancel() })
                    return
                }
                let header = headers.first { $0.lowercased().hasPrefix("range: bytes=") }
                let range = header?.split(separator: "=").last?.split(separator: "-", omittingEmptySubsequences: false)
                let start = ranges ? range?.first.flatMap { Int($0) } ?? 0 : 0
                let end = ranges ? min(range?.last.flatMap { Int($0) } ?? (bytes.count - 1), bytes.count - 1) : bytes.count - 1
                guard start >= 0, end >= start, start < bytes.count else { connection.cancel(); return }
                lock.withLock { rangeStarts.append(start) }
                let partial = ranges && header != nil
                let rangeHeader = partial ? "Content-Range: bytes \(start)-\(end)/\(advertisedLength ?? bytes.count)\r\n" : ""
                let etagHeader = etag.map { "ETag: \($0)\r\n" } ?? ""
                let reply = "HTTP/1.1 \(partial ? "206 Partial Content" : "200 OK")\r\nContent-Type: \(mime)\r\nContent-Length: \(end - start + 1)\r\n\(rangeHeader)\(etagHeader)Accept-Ranges: \(ranges ? "bytes" : "none")\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
                connection.send(content: Data(reply.utf8), completion: .contentProcessed { [weak self] error in
                    guard error == nil, let self else { connection.cancel(); return }
                    send(connection, offset: start, end: end + 1)
                })
            }
        }
        return try await withCheckedThrowingContinuation { continuation in
            listener.stateUpdateHandler = { [self] state in
                switch state {
                case .ready:
                    listener.stateUpdateHandler = nil
                    continuation.resume(returning: URL(string: "http://127.0.0.1:\(listener.port!.rawValue)/tone.wav")!)
                case .failed(let error):
                    listener.stateUpdateHandler = nil
                    continuation.resume(throwing: error)
                default: break
                }
            }
            listener.start(queue: queue)
        }
    }

    private func send(_ connection: NWConnection, offset: Int, end: Int) {
        guard lock.withLock({ connections[ObjectIdentifier(connection)] != nil }) else { return }
        let next = min(offset + 65_536, end)
        connection.send(content: bytes.subdata(in: offset..<next), completion: .contentProcessed { [weak self] error in
            guard let self, error == nil else { connection.cancel(); return }
            lock.withLock { delivered += next - offset }
            if next == end { connection.cancel() }
            else { queue.asyncAfter(deadline: .now() + .milliseconds(delay)) { [weak self] in self?.send(connection, offset: next, end: end) } }
        })
    }

    private func remove(_ id: ObjectIdentifier) { lock.withLock { _ = connections.removeValue(forKey: id) } }
    func stop() {
        listener.cancel()
        let open = lock.withLock { Array(connections.values) }
        open.forEach { $0.cancel() }
    }
}
#endif
