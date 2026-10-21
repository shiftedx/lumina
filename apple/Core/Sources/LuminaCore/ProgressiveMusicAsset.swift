import AVFoundation
import UniformTypeIdentifiers

/// AVFoundation owns read-ahead/decoding; this bridge keeps HTTP private and same-origin.
/// All loader/session callbacks and mutable state run on one serial queue.
final class ProgressiveMusicAsset: NSObject, AVAssetResourceLoaderDelegate, URLSessionDataDelegate, @unchecked Sendable {
    let id = UUID()
    let asset: AVURLAsset
    private let origin: URL
    private let queue = DispatchQueue(label: "lumina.music.progressive")
    private var session: URLSession!
    private var pending: [Int: Load] = [:]
    private var stopped = false
    private var paused = false
    private var seeks = 0
    private let failed: @Sendable (UUID, URLError.Code) -> Void

    private final class Load {
        let request: AVAssetResourceLoadingRequest
        let offset: Int64
        var task: URLSessionDataTask?
        var skip: Int64 = 0
        var remaining: Int64 = 0
        init(_ request: AVAssetResourceLoadingRequest) {
            self.request = request
            offset = request.dataRequest.map { max($0.requestedOffset, $0.currentOffset) } ?? 0
        }
    }

    init(url: URL, failed: @escaping @Sendable (UUID, URLError.Code) -> Void) throws {
        guard ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
              url.user == nil, url.password == nil,
              var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw URLError(.unsupportedURL) }
        origin = url
        self.failed = failed
        components.scheme = "lumina-music"
        guard let resource = components.url else { throw URLError(.unsupportedURL) }
        asset = AVURLAsset(url: resource)
        super.init()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForResource = 120
        let callbacks = OperationQueue()
        callbacks.maxConcurrentOperationCount = 1
        callbacks.underlyingQueue = queue
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: callbacks)
        asset.resourceLoader.setDelegate(self, queue: queue)
    }

    func setPaused(_ paused: Bool) {
        queue.async { [self] in self.paused = paused; updateTransfers() }
    }

    // A paused seek still needs metadata/audio bytes. Overlapping seeks share this lease.
    func beginSeek() { queue.async { [self] in seeks += 1; updateTransfers() } }
    func endSeek() { queue.async { [self] in seeks -= 1; updateTransfers() } }

    private func updateTransfers() {
        guard !stopped else { return }
        for load in pending.values {
            guard let task = load.task else { continue }
            if paused && seeks == 0 {
                if task.state == .running { task.suspend() }
            } else if task.state == .suspended { task.resume() }
        }
    }

    func cancel() {
        queue.async { [self] in
            stopped = true
            let loads = pending.values
            pending.removeAll()
            loads.filter { !$0.request.isCancelled && !$0.request.isFinished }.forEach { $0.request.finishLoading(with: URLError(.cancelled)) }
            session.invalidateAndCancel()
        }
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        shouldWaitForLoadingOfRequestedResource request: AVAssetResourceLoadingRequest) -> Bool {
        guard !stopped else { request.finishLoading(with: URLError(.cancelled)); return true }
        guard request.request.url == asset.url else { request.finishLoading(with: URLError(.unsupportedURL)); return true }
        let load = Load(request)
        guard load.offset >= 0, load.offset < Int64(MusicPlaybackPolicy.maximumTrackBytes) else {
            request.finishLoading(with: URLError(.dataLengthExceedsMaximum)); return true
        }
        var http = URLRequest(url: origin, cachePolicy: .reloadIgnoringLocalCacheData)
        let data = request.dataRequest
        let length = Int64(data?.requestedLength ?? 2)
        guard length > 0 else { request.finishLoading(); return true }
        let end = load.offset + min(length, Int64(MusicPlaybackPolicy.maximumTrackBytes) - load.offset) - 1
        http.setValue(data?.requestsAllDataToEndOfResource == true ? "bytes=\(load.offset)-" : "bytes=\(load.offset)-\(end)", forHTTPHeaderField: "Range")
        let task = session.dataTask(with: http)
        load.task = task
        pending[task.taskIdentifier] = load
        if !paused || seeks > 0 { task.resume() }
        return true
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, didCancel request: AVAssetResourceLoadingRequest) {
        let identifiers = pending.filter { $0.value.request === request }.map(\.key)
        for id in identifiers { pending.removeValue(forKey: id)?.task?.cancel() }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let url = request.url, sameOrigin(url), url.user == nil, url.password == nil else {
            finish(task, error: URLError(.redirectToNonExistentLocation))
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let load = pending[dataTask.taskIdentifier] else { completionHandler(.cancel); return }
        do {
            guard let response = response as? HTTPURLResponse, let url = response.url, sameOrigin(url) else { throw URLError(.badServerResponse) }
            if response.statusCode == 401 || response.statusCode == 403 {
                throw URLError(.userAuthenticationRequired)
            }
            let total: Int64
            if response.statusCode == 206 {
                let range = (response.value(forHTTPHeaderField: "Content-Range") ?? "").split(separator: "/")
                let bounds = range.first?.replacingOccurrences(of: "bytes ", with: "").split(separator: "-") ?? []
                guard range.count == 2, bounds.count == 2, let size = Int64(range[1]),
                      let start = Int64(bounds[0]), let end = Int64(bounds[1]), start == load.offset,
                      end >= start, end < size else { throw URLError(.badServerResponse) }
                total = size
            } else if response.statusCode == 200 {
                total = response.expectedContentLength
                load.skip = load.offset // Servers may ignore Range; discard the prefix incrementally.
            } else { throw URLError(.badServerResponse) }
            guard total > 0, total <= Int64(MusicPlaybackPolicy.maximumTrackBytes), load.offset < total else {
                throw URLError(.dataLengthExceedsMaximum)
            }
            if let info = load.request.contentInformationRequest {
                info.contentType = response.mimeType.flatMap { UTType(mimeType: $0)?.identifier }
                info.contentLength = total
                info.isByteRangeAccessSupported = response.statusCode == 206 || response.value(forHTTPHeaderField: "Accept-Ranges")?.lowercased() == "bytes"
            }
            guard let data = load.request.dataRequest else {
                finish(dataTask); completionHandler(.cancel); return
            }
            load.remaining = data.requestsAllDataToEndOfResource ? total - load.offset : min(Int64(data.requestedLength), total - load.offset)
            completionHandler(.allow)
        } catch { finish(dataTask, error: error); completionHandler(.cancel) }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let load = pending[dataTask.taskIdentifier] else { return }
        let skipped = min(Int64(data.count), load.skip)
        load.skip -= skipped
        let count = min(Int64(data.count) - skipped, load.remaining)
        if count > 0 {
            let chunk = skipped == 0 && count == Int64(data.count) ? data : data.subdata(in: Int(skipped)..<Int(skipped + count))
            load.request.dataRequest?.respond(with: chunk)
            load.remaining -= count
        }
        if load.remaining == 0 { finish(dataTask) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let load = pending[task.taskIdentifier] else { return }
        finish(task, error: error ?? (load.remaining > 0 ? URLError(.networkConnectionLost) : nil))
    }

    private func finish(_ task: URLSessionTask, error: Error? = nil) {
        guard let load = pending.removeValue(forKey: task.taskIdentifier) else { return }
        guard !load.request.isCancelled, !load.request.isFinished else { task.cancel(); return }
        if let error {
            let code = (error as? URLError)?.code ?? .cannotDecodeContentData
            if code != .cancelled { failed(id, code) }
            load.request.finishLoading(with: error)
        } else { load.request.finishLoading() }
        task.cancel()
    }

    private func sameOrigin(_ url: URL) -> Bool {
        let port: (URL) -> Int = { $0.port ?? ($0.scheme?.lowercased() == "https" ? 443 : 80) }
        return url.scheme?.lowercased() == origin.scheme?.lowercased()
            && url.host?.lowercased() == origin.host?.lowercased() && port(url) == port(origin)
    }
}
