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
        XCTAssertNil(lease.release(owner: first, observed: 0.8))
        XCTAssertEqual(lease.release(owner: second, observed: 0.8), 0.4)
    }
}
