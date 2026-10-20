import Foundation

/// Short-lived, private decoding input; never a shared URLSession cache or cookie jar.
final class BoundedMusicDownload: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let origin: URL
    private var continuation: CheckedContinuation<URL, Error>?
    private var session: URLSession?
    private var task: URLSessionDownloadTask?
    private var cancelled = false
    private var failure: Error?
    private var completed = false

    init(origin: URL) { self.origin = origin }

    func download() async throws -> URL {
        guard ["http", "https"].contains(origin.scheme?.lowercased() ?? ""), origin.host != nil,
              origin.user == nil, origin.password == nil else { throw URLError(.unsupportedURL) }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { next in
                lock.lock()
                if cancelled { lock.unlock(); next.resume(throwing: CancellationError()); return }
                continuation = next
                let configuration = URLSessionConfiguration.ephemeral
                configuration.httpShouldSetCookies = false
                configuration.httpCookieStorage = nil
                configuration.urlCredentialStorage = nil
                configuration.urlCache = nil
                configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
                configuration.timeoutIntervalForResource = 120
                let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
                self.session = session
                let task = session.downloadTask(with: URLRequest(url: origin, cachePolicy: .reloadIgnoringLocalCacheData))
                self.task = task
                lock.unlock()
                task.resume()
            }
        } onCancel: { self.cancel() }
    }

    private func cancel() {
        lock.lock(); cancelled = true; let task = task; lock.unlock()
        task?.cancel()
    }
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard let url = request.url, sameOrigin(url), url.user == nil, url.password == nil else {
            lock.lock(); failure = URLError(.redirectToNonExistentLocation); lock.unlock()
            completionHandler(nil)
            task.cancel()
            return
        }
        completionHandler(request)
    }
    private func sameOrigin(_ url: URL) -> Bool {
        let port: (URL) -> Int = { $0.port ?? ($0.scheme?.lowercased() == "https" ? 443 : 80) }
        return url.scheme?.lowercased() == origin.scheme?.lowercased()
            && url.host?.lowercased() == origin.host?.lowercased() && port(url) == port(origin)
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        if totalBytesWritten > Int64(MusicPlaybackPolicy.maximumTrackBytes)
            || totalBytesExpectedToWrite > Int64(MusicPlaybackPolicy.maximumTrackBytes) {
            lock.lock(); failure = URLError(.dataLengthExceedsMaximum); lock.unlock()
            downloadTask.cancel()
        }
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        lock.lock(); let failure = failure; let cancelled = cancelled; lock.unlock()
        do {
            if let failure { throw failure }
            if cancelled { throw CancellationError() }
            guard let response = downloadTask.response as? HTTPURLResponse, let url = response.url, sameOrigin(url) else { throw URLError(.badServerResponse) }
            guard (200..<300).contains(response.statusCode) else {
                throw URLError(response.statusCode == 401 || response.statusCode == 403 ? .userAuthenticationRequired : .badServerResponse)
            }
            let bytes = try location.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard bytes > 0, bytes <= MusicPlaybackPolicy.maximumTrackBytes else { throw URLError(.dataLengthExceedsMaximum) }
            let target = FileManager.default.temporaryDirectory.appendingPathComponent("lumina-audio-\(UUID().uuidString).tmp")
            try FileManager.default.moveItem(at: location, to: target)
            do {
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            #if os(iOS)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: target.path)
            #endif
            } catch { try? FileManager.default.removeItem(at: target); throw error }
            finish(.success(target))
        } catch { finish(.failure(error)) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            lock.lock(); let known = failure; let cancelled = cancelled; lock.unlock()
            finish(.failure(known ?? (cancelled ? CancellationError() : error)))
        }
    }
    private func finish(_ result: Result<URL, Error>) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let next = continuation, session = session
        continuation = nil; self.session = nil; task = nil
        lock.unlock()
        session?.finishTasksAndInvalidate()
        next?.resume(with: result)
    }
}
