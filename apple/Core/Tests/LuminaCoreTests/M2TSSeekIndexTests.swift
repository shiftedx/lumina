#if os(macOS)
import Foundation
import XCTest
@testable import LuminaCore

final class M2TSSeekIndexTests: XCTestCase {
    private let sourceID = String(repeating: "a", count: 32)

    private func document(status: String, sourceID: String? = nil) throws -> Data {
        var value: [String: Any] = ["Status": status, "SourceId": sourceID ?? self.sourceID]
        if status == "complete" {
            value.merge([
                "Version": 1, "Validator": "\"index-validator\"", "SourceSize": 384,
                "BasePID": 256, "EnhancementPID": 257, "StreamIndex": 0,
                "TimeBaseNum": 1, "TimeBaseDen": 90_000,
                "KeyframePTS": [0, 90_000], "PacketOffsets": [0, 192],
            ], uniquingKeysWith: { _, new in new })
        }
        return try JSONSerialization.data(withJSONObject: value)
    }

    func testSeekIndexUsesOnlyCompleteBoundAndWellFormedHints() async throws {
        let cases: [(Data, Bool)] = [
            (try document(status: "unsupported"), false),
            (try document(status: "complete", sourceID: String(repeating: "b", count: 32)), false),
            (Data("{bad".utf8), false),
            (try JSONSerialization.data(withJSONObject: ["Status": "complete", "SourceId": sourceID]), false),
            (try document(status: "complete"), true),
        ]
        for (response, expected) in cases {
            let server = try ProgressiveAudioHTTPFixture(bytes: Data(repeating: 0, count: 384), delay: 0, seekIndexResponses: [response])
            let original = try await server.start()
            defer { server.stop() }
            let reference = M2TSSeekIndexReference(url: original.deletingLastPathComponent().appendingPathComponent("seek-index"), sourceID: sourceID)
            let hints = try await reference.sourceOpenHints(for: original)
            XCTAssertEqual(hints != nil, expected)
        }
    }

    func testSeekIndexPropagatesAuthorizationAndCancelsPendingPoll() async throws {
        for status in [401, 403] {
            let server = try ProgressiveAudioHTTPFixture(bytes: Data(repeating: 0, count: 384), delay: 0, status: status)
            let original = try await server.start()
            defer { server.stop() }
            let reference = M2TSSeekIndexReference(url: original, sourceID: sourceID)
            do {
                _ = try await reference.sourceOpenHints(for: original)
                XCTFail("Expected HTTP \(status)")
            } catch let error as LuminaError {
                XCTAssertEqual(error, .http(status))
            }
        }

        let server = try ProgressiveAudioHTTPFixture(bytes: Data(repeating: 0, count: 384), delay: 0, seekIndexResponses: [try document(status: "pending")])
        let original = try await server.start()
        defer { server.stop() }
        let reference = M2TSSeekIndexReference(url: original.deletingLastPathComponent().appendingPathComponent("seek-index"), sourceID: sourceID)
        let polling = Task { try await reference.sourceOpenHints(for: original) }
        for _ in 0..<100 where server.seekIndexRequestCount == 0 {
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertGreaterThanOrEqual(server.seekIndexRequestCount, 1)
        polling.cancel()
        do {
            _ = try await polling.value
            XCTFail("Expected cancellation")
        } catch is CancellationError {
            XCTAssertGreaterThanOrEqual(server.seekIndexRequestCount, 1)
        }
    }
}
#endif
