import AVFoundation
import Foundation
import Security
import XCTest
@testable import LuminaCore

@MainActor
final class LuminaCoreTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MockURLProtocol.handler = nil
        HomeDelayedURLProtocol.reset()
    }
}
