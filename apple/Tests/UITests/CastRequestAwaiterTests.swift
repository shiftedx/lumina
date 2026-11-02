#if os(iOS)
import GoogleCast
import XCTest

@MainActor
final class CastRequestAwaiterTests: XCTestCase {
    func testCancellationCancelsPendingRequestPromptly() async {
        let request = GCKRequest.application()
        let awaiter = CastRequestAwaiter()
        let task = Task<Void, Error> {
            try await awaiter.wait(for: request, timeout: .seconds(30))
        }
        await waitForDelegate(on: request)
        XCTAssertTrue(request.inProgress)

        let started = Date()
        task.cancel()
        do {
            try await task.value
            XCTFail("Cancelled request completed successfully")
        } catch is CancellationError { }
        catch { XCTFail("Expected cancellation, got \(error)") }

        XCTAssertLessThan(Date().timeIntervalSince(started), 1)
        XCTAssertFalse(request.inProgress)
        XCTAssertNil(request.delegate)
    }

    func testAlreadyCancelledTaskCancelsRequestWithoutInstallingDelegate() async {
        let request = GCKRequest.application()
        let awaiter = CastRequestAwaiter()
        let task = Task<Void, Error> {
            withUnsafeCurrentTask { $0?.cancel() }
            try await awaiter.wait(for: request, timeout: .seconds(30))
        }

        do {
            try await task.value
            XCTFail("Cancelled request completed successfully")
        } catch is CancellationError { }
        catch { XCTFail("Expected cancellation, got \(error)") }

        XCTAssertFalse(request.inProgress)
        XCTAssertNil(request.delegate)
    }

    func testCompletionClearsDelegateAndFinishesOnce() async throws {
        let request = GCKRequest.application()
        let awaiter = CastRequestAwaiter()
        let task = Task<Void, Error> {
            try await awaiter.wait(for: request, timeout: .seconds(30))
        }
        await waitForDelegate(on: request)
        XCTAssertTrue(request.inProgress)

        request.complete()
        try await task.value
        XCTAssertFalse(request.inProgress)
        XCTAssertNil(request.delegate)

        awaiter.requestDidComplete(request)
        XCTAssertNil(request.delegate)
    }

    func testTimeoutCancelsRequestAndIgnoresLateCompletion() async {
        let request = GCKRequest.application()
        let awaiter = CastRequestAwaiter()
        let task = Task<Void, Error> {
            try await awaiter.wait(for: request, timeout: .milliseconds(10))
        }
        await waitForDelegate(on: request)
        XCTAssertTrue(request.inProgress)

        do {
            try await task.value
            XCTFail("Timed out request completed successfully")
        } catch let error as NativeVideoCastError {
            guard case .receiverDidNotAcknowledge = error else {
                return XCTFail("Expected timeout, got \(error)")
            }
        } catch {
            XCTFail("Expected timeout, got \(error)")
        }

        XCTAssertFalse(request.inProgress)
        XCTAssertNil(request.delegate)
        awaiter.requestDidComplete(request)
        XCTAssertNil(request.delegate)
    }

    private func waitForDelegate(on request: GCKRequest) async {
        for _ in 0..<100 where request.delegate == nil {
            await Task.yield()
        }
        XCTAssertNotNil(request.delegate)
    }
}
#endif
