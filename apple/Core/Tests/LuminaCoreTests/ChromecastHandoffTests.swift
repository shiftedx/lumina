import Foundation
import XCTest
@testable import LuminaCore

final class ChromecastHandoffTests: XCTestCase {
    private let expected = URL(string: "https://receiver.example/api/chromecast/11111111-1111-1111-1111-111111111111/media?token=capability")!

    func testAcceptsAcknowledgedPlayingAndPausedHandoffs() {
        XCTAssertTrue(ChromecastHandoff.acknowledges(
            expectedMediaURL: expected, receiverMediaURL: expected,
            localPosition: 42, receiverPosition: 43.5, localPlaying: true, receiverPlaying: true
        ))
        XCTAssertTrue(ChromecastHandoff.acknowledges(
            expectedMediaURL: expected, receiverMediaURL: expected,
            localPosition: 42, receiverPosition: 40.5, localPlaying: false, receiverPlaying: false
        ))
    }

    func testRejectsForeignTicketAndMissingReceiverItem() {
        let foreign = URL(string: "https://receiver.example/api/chromecast/22222222-2222-2222-2222-222222222222/media?token=capability")!
        XCTAssertFalse(ChromecastHandoff.acknowledges(
            expectedMediaURL: expected, receiverMediaURL: foreign,
            localPosition: 42, receiverPosition: 42, localPlaying: true, receiverPlaying: true
        ))
        XCTAssertFalse(ChromecastHandoff.acknowledges(
            expectedMediaURL: expected, receiverMediaURL: nil,
            localPosition: 42, receiverPosition: 42, localPlaying: true, receiverPlaying: true
        ))
    }

    func testRejectsInvalidClocksAndMismatchedPlaybackIntent() {
        for clocks in [(Double.nan, 42.0), (42.0, .infinity), (-0.1, 42.0), (42.0, -0.1), (42.0, 43.6)] {
            XCTAssertFalse(ChromecastHandoff.acknowledges(
                expectedMediaURL: expected, receiverMediaURL: expected,
                localPosition: clocks.0, receiverPosition: clocks.1, localPlaying: true, receiverPlaying: true
            ))
        }
        XCTAssertFalse(ChromecastHandoff.acknowledges(
            expectedMediaURL: expected, receiverMediaURL: expected,
            localPosition: 42, receiverPosition: 42, localPlaying: true, receiverPlaying: false
        ))
    }
}
