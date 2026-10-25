import XCTest
@testable import LuminaCore

final class VideoGestureClassifierTests: XCTestCase {
    private let geometry = VideoGestureGeometry(size: .init(width: 300, height: 600))

    func testSingleTapDelaysControlsUntilDoubleTapWindowExpires() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        XCTAssertEqual(classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1), [])
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 150, y: 300), timestamp: 1.1), [])
        XCTAssertEqual(classifier.advance(to: 1.35), [])
        XCTAssertEqual(classifier.advance(to: 1.37), [.toggleControls])
    }

    func testSecondTouchdownSuspendsSingleAndSameSideDoubleTapSkipsOnce() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 250, y: 300), timestamp: 1)
        _ = classifier.touchEnded(id: 1, at: .init(x: 250, y: 300), timestamp: 1.05)
        XCTAssertEqual(classifier.touchBegan(id: 2, at: .init(x: 250, y: 300), timestamp: 1.25), [])
        XCTAssertEqual(classifier.advance(to: 1.40), [])
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 250, y: 300), timestamp: 1.45), [.relativeSkip(.forward, count: 1)])
        XCTAssertEqual(classifier.advance(to: 1.70), [])
    }

    func testSkipBurstAccumulatesEveryAcceptedContinuation() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        func tap(_ id: Int, _ down: TimeInterval, _ up: TimeInterval) -> [VideoGestureAction] {
            _ = classifier.touchBegan(id: id, at: .init(x: 250, y: 300), timestamp: down)
            return classifier.touchEnded(id: id, at: .init(x: 250, y: 300), timestamp: up)
        }
        XCTAssertEqual(tap(1, 1, 1.05), [])
        XCTAssertEqual(tap(2, 1.2, 1.25), [.relativeSkip(.forward, count: 1)])
        XCTAssertEqual(tap(3, 1.5, 1.55), [.relativeSkip(.forward, count: 2)])
        XCTAssertEqual(tap(4, 1.8, 1.85), [.relativeSkip(.forward, count: 3)])
    }

    func testOppositeSideAfterFirstTapCreatesTwoSinglesNotSeek() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 60, y: 300), timestamp: 1)
        _ = classifier.touchEnded(id: 1, at: .init(x: 60, y: 300), timestamp: 1.04)
        _ = classifier.touchBegan(id: 2, at: .init(x: 250, y: 300), timestamp: 1.2)
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 250, y: 300), timestamp: 1.24), [.toggleControls])
        XCTAssertEqual(classifier.advance(to: 1.51), [.toggleControls])
    }

    func testAxisLockUsesOriginalHalfAndStartsAtLockWithoutJump() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 40, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 90, y: 270), timestamp: 1.1), [.beginSurfaceScrub])
        XCTAssertEqual(
            classifier.touchMoved(id: 1, to: .init(x: 120, y: 240), timestamp: 1.2),
            [.updateSurfaceScrub(delta: .init(x: 30, y: -30), size: .init(width: 300, height: 600))]
        )
        _ = classifier.touchEnded(id: 1, at: .init(x: 120, y: 240), timestamp: 1.3)

        _ = classifier.touchBegan(id: 2, at: .init(x: 40, y: 300), timestamp: 2)
        XCTAssertEqual(classifier.touchMoved(id: 2, to: .init(x: 43, y: 270), timestamp: 2.1), [.beginAdjustment(.brightness)])
        XCTAssertEqual(
            classifier.touchMoved(id: 2, to: .init(x: 250, y: 240), timestamp: 2.2),
            [.updateAdjustment(kind: .brightness, delta: .init(x: 207, y: -30), size: .init(width: 300, height: 600))]
        )
    }

    func testTwoFingerChordTogglesPlaybackAndNotControls() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 100, y: 300), timestamp: 1)
        _ = classifier.touchBegan(id: 2, at: .init(x: 200, y: 300), timestamp: 1.08)
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 100, y: 300), timestamp: 1.16), [])
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 200, y: 300), timestamp: 1.20), [.togglePlayback])
    }

    func testSecondFingerCancelsActiveSurfaceScrubAndSuppressesRemainder() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 100, y: 300), timestamp: 1)
        _ = classifier.touchMoved(id: 1, to: .init(x: 140, y: 300), timestamp: 1.1)
        XCTAssertEqual(classifier.touchBegan(id: 2, at: .init(x: 200, y: 300), timestamp: 1.2), [.cancelSurfaceScrub])
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 140, y: 300), timestamp: 1.3), [])
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 200, y: 300), timestamp: 1.3), [])
    }

    func testHoldHasLeaseEndOnCancellationAndDoesNotTriggerControls() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.advance(to: 1.46), [.beginTemporarySpeedHold])
        XCTAssertEqual(classifier.touchCancelled(id: 1, timestamp: 1.5), [.endTemporarySpeedHold])
        XCTAssertEqual(classifier.advance(to: 2), [])
    }

    func testMasterOffKeepsSingleTapImmediateAndSuppressesAdvancedActions() {
        var configuration = VideoGestureConfiguration(advancedEnabled: false)
        var classifier = VideoGestureClassifier(configuration: configuration, geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 250, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 250, y: 300), timestamp: 1.05), [.toggleControls])
        configuration.voiceOverEnabled = true
        _ = classifier.reconfigure(configuration, geometry: geometry)
        _ = classifier.touchBegan(id: 2, at: .init(x: 250, y: 300), timestamp: 2)
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 250, y: 300), timestamp: 2.05), [.toggleControls])
    }

    func testDisabledDoubleTapMakesSingleControlsImmediate() {
        var classifier = VideoGestureClassifier(configuration: .init(doubleTapEnabled: false), geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 250, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 250, y: 300), timestamp: 1.05), [.toggleControls])
        XCTAssertNil(classifier.nextDeadline)
    }

    func testCancellationDiscardsDelayedTapAndEndsAnActiveHoldOnce() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        _ = classifier.touchEnded(id: 1, at: .init(x: 150, y: 300), timestamp: 1.05)
        XCTAssertEqual(classifier.cancelAll(), [])
        XCTAssertEqual(classifier.advance(to: 2), [])

        _ = classifier.touchBegan(id: 2, at: .init(x: 150, y: 300), timestamp: 3)
        XCTAssertEqual(classifier.advance(to: 3.5), [.beginTemporarySpeedHold])
        XCTAssertEqual(classifier.cancelAll(), [.endTemporarySpeedHold])
        XCTAssertEqual(classifier.cancelAll(), [])
    }

    func testEdgeAndInvalidSecondContactDiscardPendingTap() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        _ = classifier.touchEnded(id: 1, at: .init(x: 150, y: 300), timestamp: 1.05)
        _ = classifier.touchBegan(id: 2, at: .init(x: 3, y: 300), timestamp: 1.2)
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 3, y: 300), timestamp: 1.24), [])
        XCTAssertEqual(classifier.advance(to: 2), [])
    }

    func testIneligibleSurfaceNeverStartsPanHoldOrTap() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 3, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 100, y: 300), timestamp: 1.1), [])
        XCTAssertEqual(classifier.advance(to: 2), [])
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 100, y: 300), timestamp: 2.1), [])
    }

    func testMasterOffPanAndTwoFingerContactDoNotProduceAdvancedOrDoubleControls() {
        var classifier = VideoGestureClassifier(configuration: .init(advancedEnabled: false), geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 230, y: 300), timestamp: 1.1), [])
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 230, y: 300), timestamp: 1.2), [])

        _ = classifier.touchBegan(id: 2, at: .init(x: 140, y: 300), timestamp: 2)
        XCTAssertEqual(classifier.touchBegan(id: 3, at: .init(x: 180, y: 300), timestamp: 2.04), [])
        XCTAssertEqual(classifier.touchEnded(id: 2, at: .init(x: 140, y: 300), timestamp: 2.1), [])
        XCTAssertEqual(classifier.touchEnded(id: 3, at: .init(x: 180, y: 300), timestamp: 2.1), [])
    }

    func testAmbiguousDiagonalCancelsHoldBeforeAxisLock() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 161, y: 311), timestamp: 1.1), [])
        XCTAssertEqual(classifier.advance(to: 1.6), [])
        XCTAssertEqual(classifier.touchEnded(id: 1, at: .init(x: 161, y: 311), timestamp: 1.7), [])
    }

    func testHoldDriftUsesActivationPointNotInitialTouchdown() {
        var classifier = VideoGestureClassifier(geometry: geometry)
        _ = classifier.touchBegan(id: 1, at: .init(x: 150, y: 300), timestamp: 1)
        _ = classifier.touchMoved(id: 1, to: .init(x: 158, y: 300), timestamp: 1.1)
        XCTAssertEqual(classifier.advance(to: 1.5), [.beginTemporarySpeedHold])
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 188, y: 300), timestamp: 1.6), [])
        XCTAssertEqual(classifier.touchMoved(id: 1, to: .init(x: 191, y: 300), timestamp: 1.7), [.endTemporarySpeedHold])
    }

    func testFineScrubPromotesAndDemotesWithIncrementalRebasing() {
        var scrub = VideoFineScrub()
        scrub.begin(at: .init(x: 100, y: 300))
        XCTAssertEqual(scrub.update(to: .init(x: 120, y: 260), upwardDistance: 40, containerHeight: 360)?.deltaX, 0)
        XCTAssertEqual(scrub.multiplier, 0.5)
        XCTAssertEqual(scrub.update(to: .init(x: 140, y: 220), upwardDistance: 80, containerHeight: 360)?.deltaX, 0)
        XCTAssertEqual(scrub.multiplier, 0.25)
        XCTAssertEqual(scrub.update(to: .init(x: 160, y: 180), upwardDistance: 120, containerHeight: 360)?.deltaX, 0)
        XCTAssertEqual(scrub.multiplier, 0.1)
        XCTAssertEqual(scrub.update(to: .init(x: 180, y: 176), upwardDistance: 124, containerHeight: 360)?.deltaX, 20)
        XCTAssertEqual(scrub.update(to: .init(x: 200, y: 296), upwardDistance: 4, containerHeight: 360)?.deltaX, 0)
        XCTAssertEqual(scrub.multiplier, 1)
        XCTAssertEqual(scrub.update(to: .init(x: 220, y: 296), upwardDistance: 4, containerHeight: 360)?.deltaX, 20)
        XCTAssertTrue(scrub.isFineMode)
    }
}
