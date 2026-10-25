import XCTest
@testable import LuminaCore

final class VideoPlaybackGestureStateTests: XCTestCase {
    private let range = VideoSeekRange(0, 180)!

    func testSeekAuthorityCoalescesRelativeTargetsFromOptimisticTime() {
        var authority = VideoSeekAuthority()
        let first = try! XCTUnwrap(authority.request(target: 70, ranges: [range], direction: .forward))
        XCTAssertEqual(authority.optimisticTarget, 70)
        XCTAssertNil(authority.request(target: 80, ranges: [range], direction: .forward))
        XCTAssertNil(authority.request(target: 90, ranges: [range], direction: .forward))

        guard case let .submit(latest) = authority.complete(first, actualTime: 70) else {
            return XCTFail("The latest coalesced target must be submitted after the first completion")
        }
        XCTAssertEqual(latest.target, 90)
        XCTAssertEqual(authority.optimisticTarget, 90)
        XCTAssertEqual(authority.complete(latest, actualTime: 90), .settled(90))
        XCTAssertNil(authority.optimisticTarget)
    }

    func testSeekAuthorityIgnoresCompletionFromReplacedItem() {
        var authority = VideoSeekAuthority()
        let stale = try! XCTUnwrap(authority.request(target: 40, ranges: [range], direction: .forward))
        authority.invalidate()
        let fresh = try! XCTUnwrap(authority.request(target: 120, ranges: [range], direction: .forward))

        XCTAssertEqual(authority.complete(stale, actualTime: 40), .ignored)
        XCTAssertEqual(authority.optimisticTarget, 120)
        XCTAssertEqual(authority.complete(fresh, actualTime: 120), .settled(120))
    }

    func testRevalidationClampsCoalescedDVRTargetAndIgnoresPreRevalidatedCompletion() {
        var authority = VideoSeekAuthority()
        let first = try! XCTUnwrap(authority.request(target: 40, ranges: [VideoSeekRange(0, 100)!], direction: .forward))
        XCTAssertNil(authority.request(target: 90, ranges: [VideoSeekRange(0, 100)!], direction: .forward))
        guard case let .submit(coalesced) = authority.complete(first, actualTime: 40) else {
            return XCTFail("The queued target must be promoted after the older seek completes")
        }

        let revalidated = try! XCTUnwrap(authority.revalidatedSubmission(
            coalesced, ranges: [VideoSeekRange(50, 80)!], direction: .backward
        ))
        XCTAssertEqual(revalidated.operationID, coalesced.operationID)
        XCTAssertEqual(revalidated.generation, coalesced.generation)
        XCTAssertEqual(revalidated.revision, coalesced.revision)
        XCTAssertEqual(revalidated.target, 80)
        XCTAssertEqual(authority.optimisticTarget, 80)

        XCTAssertEqual(authority.complete(coalesced, actualTime: 40), .ignored)
        XCTAssertEqual(authority.optimisticTarget, 80)
        XCTAssertEqual(authority.complete(revalidated, actualTime: 80), .settled(80))
    }

    func testRevalidationWithoutCurrentRangesInvalidatesObsoleteWork() {
        var authority = VideoSeekAuthority()
        let submission = try! XCTUnwrap(authority.request(target: 40, ranges: [range], direction: .forward))

        XCTAssertNil(authority.revalidatedSubmission(submission, ranges: [], direction: .forward))
        XCTAssertNil(authority.optimisticTarget)
        XCTAssertEqual(authority.complete(submission, actualTime: 40), .ignored)
        XCTAssertNotNil(authority.request(target: 70, ranges: [range], direction: .forward))
    }

    func testProjectionClampsGapsAndUsesDirectionForDistanceTie() {
        let lower = VideoSeekRange(0, 20)!
        let upper = VideoSeekRange(40, 60)!
        XCTAssertEqual(VideoSeekAuthority.project(-5, into: [lower, upper], direction: .backward), 0)
        XCTAssertEqual(VideoSeekAuthority.project(80, into: [lower, upper], direction: .forward), 60)
        XCTAssertEqual(VideoSeekAuthority.project(30, into: [lower, upper], direction: .backward), 20)
        XCTAssertEqual(VideoSeekAuthority.project(30, into: [lower, upper], direction: .forward), 40)
    }

    func testTemporaryRateLeaseNeverProducesRateForPausedIntentAndNormalRateWins() {
        var lease = VideoTemporaryRateLease(preferredRate: 1.25)
        let token = try! XCTUnwrap(lease.begin(isEligible: true))
        XCTAssertEqual(lease.effectiveRate, 2)
        XCTAssertNil(lease.rateToApply(isPlaybackAllowed: false))

        lease.setPreferredRate(1.5)
        XCTAssertEqual(lease.effectiveRate, 1.5)
        XCTAssertFalse(lease.end(token), "A normal rate change must revoke the old temporary token")
        XCTAssertEqual(lease.rateToApply(isPlaybackAllowed: true), 1.5)
    }
}
