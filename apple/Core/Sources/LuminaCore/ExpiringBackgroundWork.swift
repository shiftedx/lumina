import Foundation

/// Ends a finite platform background assertion exactly once, even if work finishes after expiry.
@MainActor
public final class ExpiringBackgroundWork {
    private var end: (() -> Void)?
    private var task: Task<Void, Never>?

    public init(end: @escaping () -> Void) { self.end = end }

    public func start(_ operation: @escaping @MainActor () async -> Void) {
        guard end != nil, task == nil else { return }
        task = Task {
            defer { finish() }
            guard !Task.isCancelled else { return }
            await operation()
        }
    }

    public func wait() async { await task?.value }

    public func expire() {
        task?.cancel()
        finish()
    }

    private func finish() {
        guard let end else { return }
        self.end = nil
        end()
    }
}
