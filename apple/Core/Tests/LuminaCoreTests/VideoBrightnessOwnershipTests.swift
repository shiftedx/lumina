import XCTest
@testable import LuminaCore

final class VideoBrightnessOwnershipTests: XCTestCase {
    func testViewingLevelRestoresOnlyWhileStillOwned() {
        var lease = VideoBrightnessOwnership()
        let owner = UUID()
        XCTAssertEqual(lease.apply(0.7, owner: owner, observed: 0.4), 0.7)
        XCTAssertNil(lease.observe(0.7))
        XCTAssertEqual(lease.release(owner: owner, observed: 0.7), 0.4)
        XCTAssertNil(lease.release(owner: owner, observed: 0.4))
    }

    func testContinuousSwipeSurvivesDelayedReadbackAndRestoresOriginalBrightness() {
        var lease = VideoBrightnessOwnership()
        let owner = UUID()
        _ = lease.apply(0.5, owner: owner, observed: 0.5)
        // The display may still report 0.5 while successive swipe writes arrive.
        XCTAssertEqual(lease.update(0.6, owner: owner), 0.6)
        XCTAssertEqual(lease.update(0.8, owner: owner), 0.8)
        XCTAssertEqual(lease.release(owner: owner, observed: 0.8), 0.5)
        XCTAssertNil(lease.update(0.9, owner: owner))
    }

    func testExternalAdjustmentRevokesRestorationAndAdoptsNewBaseline() {
        var lease = VideoBrightnessOwnership()
        let owner = UUID()
        _ = lease.apply(0.7, owner: owner, observed: 0.4)
        XCTAssertEqual(lease.observe(0.5), owner)
        XCTAssertNil(lease.release(owner: owner, observed: 0.5))
        _ = lease.apply(0.8, owner: owner, observed: 0.5)
        XCTAssertEqual(lease.release(owner: owner, observed: 0.8), 0.5)
    }

    func testNewPresentationCarriesOriginalBaselineAndOldOwnerCannotReleaseIt() {
        var lease = VideoBrightnessOwnership()
        let first = UUID(), second = UUID()
        _ = lease.apply(0.7, owner: first, observed: 0.4)
        _ = lease.apply(0.8, owner: second, observed: 0.7)
        XCTAssertNil(lease.update(0.9, owner: first))
        XCTAssertNil(lease.release(owner: first, observed: 0.8))
        XCTAssertEqual(lease.release(owner: second, observed: 0.8), 0.4)
    }
}
