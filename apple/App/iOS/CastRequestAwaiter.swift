import Foundation
import GoogleCast

enum NativeVideoCastError: Error {
    case receiverUnavailable
    case receiverDidNotAcknowledge
    case unsupportedTrackSelection
    case unsupportedPlaybackRate
}

@MainActor
final class CastRequestAwaiter: NSObject, @preconcurrency GCKRequestDelegate {
    private var continuation: CheckedContinuation<Void, Error>?
    private var timeoutTask: Task<Void, Never>?
    private var request: GCKRequest?

    func wait(for request: GCKRequest, timeout: Duration) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                guard !Task.isCancelled else {
                    request.cancel()
                    continuation.resume(throwing: CancellationError())
                    return
                }
                self.continuation = continuation
                self.request = request
                request.delegate = self
                // A request may notify its delegate synchronously while assigning it.
                guard self.continuation != nil else { return }
                timeoutTask = Task { [weak self] in
                    do {
                        try await Task.sleep(for: timeout)
                        guard !Task.isCancelled else { return }
                        self?.cancel(.failure(NativeVideoCastError.receiverDidNotAcknowledge))
                    } catch { }
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.cancel(.failure(CancellationError()))
            }
        }
    }

    func requestDidComplete(_: GCKRequest) {
        finish(.success(()))
    }

    func request(_: GCKRequest, didFailWithError _: GCKError) {
        finish(.failure(NativeVideoCastError.receiverUnavailable))
    }

    func request(_: GCKRequest, didAbortWith _: GCKRequestAbortReason) {
        finish(.failure(CancellationError()))
    }

    private func finish(_ result: Result<Void, Error>) {
        timeoutTask?.cancel()
        timeoutTask = nil
        request?.delegate = nil
        request = nil
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(with: result)
    }

    private func cancel(_ result: Result<Void, Error>) {
        let request = request
        finish(result)
        request?.cancel()
    }
}
