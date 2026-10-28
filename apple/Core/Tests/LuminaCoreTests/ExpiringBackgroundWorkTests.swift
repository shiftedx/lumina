import XCTest
@testable import LuminaCore

final class ExpiringBackgroundWorkTests: XCTestCase {
    @MainActor
    func testExpirationBeforeDispatchDoesNotStartQueuedWork() async {
        var ends = 0
        var ran = false
        let work = ExpiringBackgroundWork { ends += 1 }
        work.start { ran = true }
        work.expire()
        await work.wait()
        XCTAssertFalse(ran, "Expiration before dispatch must prevent a new checkpoint from starting")
        XCTAssertEqual(ends, 1)
    }

    @MainActor
    func testExpirationCancelsOutstandingWorkAndEndsBeforeItUnwinds() async {
        var ends = 0
        var cancelled = false
        let (started, continuation) = AsyncStream<Void>.makeStream()
        let work = ExpiringBackgroundWork { ends += 1 }
        work.start {
            continuation.yield(())
            continuation.finish()
            do { try await Task.sleep(for: .seconds(60)) }
            catch { cancelled = Task.isCancelled }
        }
        var start = started.makeAsyncIterator()
        _ = await start.next()
        work.expire()
        XCTAssertEqual(ends, 1, "The system assertion must end immediately on expiration")
        await work.wait()
        XCTAssertTrue(cancelled)
        work.expire()
        XCTAssertEqual(ends, 1, "Late completion and repeated expiration must not end an assertion twice")
    }

    @MainActor
    func testCompletionAndDeniedBackgroundTimeDoNotLeaveAssertionsOpen() async {
        var ends = 0
        let completed = ExpiringBackgroundWork { ends += 1 }
        completed.start { }
        await completed.wait()
        XCTAssertEqual(ends, 1)
        completed.expire()
        XCTAssertEqual(ends, 1)
        let denied = ExpiringBackgroundWork { ends += 1 }
        denied.expire()
        var ran = false
        denied.start { ran = true }
        await denied.wait()
        XCTAssertFalse(ran, "Expired or denied work cannot begin after the assertion has ended")
    }
}
