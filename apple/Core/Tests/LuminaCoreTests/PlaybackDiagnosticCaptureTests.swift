import Foundation
import XCTest
@testable import LuminaCore

@MainActor
final class PlaybackDiagnosticCaptureTests: XCTestCase {
    private func capture(at url: URL) throws -> PlaybackDiagnosticCapture {
        try PlaybackDiagnosticCapture.start(fileURL: url, version: "1.2.3", build: "24",
                                            osVersion: .init(majorVersion: 27, minorVersion: 0, patchVersion: 1))
    }

    private func temporaryFile() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jsonl")
    }

    private func read(_ url: URL) throws -> [[String: Any]] {
        let data = try Data(contentsOf: url)
        XCTAssertEqual(data.last, 0x0A)
        return try data.split(separator: 0x0A).map {
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data($0)) as? [String: Any])
        }
    }

    func testFinalizationDrainsWritesAndReplacesOldFileWithTypedSanitizedCapture() async throws {
        let url = temporaryFile()
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("old private contents".utf8).write(to: url)
        let capture = try capture(at: url)
        var sample = PlaybackDiagnosticSample()
        sample.modelID = UUID()
        sample.positionSeconds = 35.5
        sample.rate = .nan
        sample.requestedPositionSeconds = .infinity
        sample.rateChangeReason = .appBackgrounded
        sample.waitingReason = .minimizeStalls
        sample.nativeReason = 1
        sample.viewID = 12
        sample.layerID = 13
        sample.seekFinished = false
        sample.bufferedUntilSeconds = .infinity
        sample.seekTargetSeconds = -.infinity
        sample.width = .nan
        sample.height = .infinity
        sample.accessObservedBitrate = .nan
        sample.accessIndicatedBitrate = -.infinity
        sample.errorDomain = .avFoundation
        sample.errorCode = -11800
        XCTAssertTrue(capture.record(.timeJump, sample: sample))
        let result = try await capture.finish(reason: .timeout)
        XCTAssertEqual(result, url)
        XCTAssertFalse(capture.isRecording)
        XCTAssertFalse(capture.record(.sample, sample: sample))
        let repeated = try await capture.finish(reason: .user)
        XCTAssertEqual(repeated, url)
        let rows = try read(url)
        XCTAssertEqual(rows.count, 3)
        XCTAssertEqual(rows[0]["event"] as? String, "captureStarted")
        XCTAssertEqual(rows[0]["schema"] as? Int, 1)
        XCTAssertEqual(rows[0]["version"] as? String, "1.2.3")
        XCTAssertEqual(rows[0]["build"] as? String, "24")
        XCTAssertEqual(rows[0]["osMajor"] as? Int, 27)
        let state = try XCTUnwrap(rows[1]["sample"] as? [String: Any])
        XCTAssertEqual(state["positionSeconds"] as? Double, 35.5)
        XCTAssertEqual(state["errorDomain"] as? String, "avFoundation")
        XCTAssertEqual(state["rateChangeReason"] as? String, "appBackgrounded")
        XCTAssertEqual(state["waitingReason"] as? String, "minimizeStalls")
        XCTAssertEqual(state["nativeReason"] as? Int, 1)
        XCTAssertEqual(state["viewID"] as? UInt64, 12)
        XCTAssertEqual(state["layerID"] as? UInt64, 13)
        XCTAssertEqual(state["seekFinished"] as? Bool, false)
        XCTAssertEqual(Set(state.keys), Set(["modelID", "positionSeconds", "errorDomain", "errorCode",
                                             "rateChangeReason", "waitingReason", "nativeReason", "viewID", "layerID", "seekFinished"]))
        XCTAssertEqual(rows.last?["event"] as? String, "captureStopped")
        XCTAssertEqual(rows.last?["records"] as? Int, rows.count)
        XCTAssertEqual(rows.last?["stopReason"] as? String, "timeout")
    }

    func testBurstIsCoalescedAndHardLimitStillLeavesCompleteSummary() async throws {
        let url = temporaryFile()
        defer { try? FileManager.default.removeItem(at: url) }
        let capture = try capture(at: url)
        let uptime = ProcessInfo.processInfo.systemUptime
        var sample = PlaybackDiagnosticSample()
        sample.positionSeconds = 10
        sample.requestedPositionSeconds = 8
        sample.rateChangeReason = .setRateFailed
        for _ in 0..<10_000 {
            XCTAssertTrue(capture.record(.rendererLayout, sample: sample, uptime: uptime))
        }
        // Move across simulated windows to exercise the independent total-record limit.
        for offset in 1...2_100 {
            if !capture.record(.sample, sample: sample, uptime: uptime + Double(offset)) { break }
        }
        XCTAssertFalse(capture.isRecording)
        _ = try await capture.finish(reason: .limit)
        let data = try Data(contentsOf: url)
        let rows = try read(url)
        XCTAssertLessThanOrEqual(data.count, PlaybackDiagnosticCapture.maximumBytes)
        XCTAssertEqual(rows.count, PlaybackDiagnosticCapture.maximumRecords)
        XCTAssertEqual(rows.last?["event"] as? String, "captureStopped")
        XCTAssertEqual(rows.last?["suppressedEvents"] as? Int, 9_992)
        let state = try XCTUnwrap(rows[1]["sample"] as? [String: Any])
        XCTAssertEqual(state["positionSeconds"] as? Double, 10)
        XCTAssertEqual(state["requestedPositionSeconds"] as? Double, 8)
        XCTAssertEqual(state["rateChangeReason"] as? String, "setRateFailed")
        XCTAssertEqual(rows.last?["stopReason"] as? String, "limit")
        XCTAssertEqual(rows.last?["records"] as? Int, rows.count)
    }

    func testHotBurstLeavesRoomForTimeJumpAndTransportEventsWithinTotalRateLimit() async throws {
        let url = temporaryFile()
        defer { try? FileManager.default.removeItem(at: url) }
        let capture = try capture(at: url)
        let uptime = ProcessInfo.processInfo.systemUptime
        var sample = PlaybackDiagnosticSample()
        sample.positionSeconds = 40
        for index in 0..<1_000 {
            XCTAssertTrue(capture.record(index.isMultiple(of: 2) ? .sample : .rendererLayout,
                                         sample: sample, uptime: uptime))
        }
        sample.positionSeconds = 35
        sample.rateChangeReason = .audioSessionInterrupted
        XCTAssertTrue(capture.record(.timeJump, sample: sample, uptime: uptime))
        for _ in 0..<100 {
            XCTAssertTrue(capture.record(.rateChanged, sample: sample, uptime: uptime))
        }
        _ = try await capture.finish()
        let rows = try read(url)
        let events = rows.compactMap { $0["event"] as? String }
        XCTAssertEqual(events.filter { $0 == "sample" || $0 == "rendererLayout" }.count, 8)
        XCTAssertEqual(events.filter { $0 == "timeJump" }.count, 1)
        XCTAssertEqual(events.filter { $0 == "rateChanged" }.count, 7)
        XCTAssertEqual(rows.count, 18) // Header, 16 events, final summary.
        XCTAssertEqual(rows.last?["suppressedEvents"] as? Int, 1_085)
        let jump = try XCTUnwrap(rows.first { $0["event"] as? String == "timeJump" })
        let state = try XCTUnwrap(jump["sample"] as? [String: Any])
        XCTAssertEqual(state["positionSeconds"] as? Double, 35)
        XCTAssertEqual(state["rateChangeReason"] as? String, "audioSessionInterrupted")
        XCTAssertEqual(rows.last?["stopReason"] as? String, "user")
    }

    func testLargeSamplesRespectByteLimitAndKeepMonotonicFinalSummary() async throws {
        let url = temporaryFile()
        defer { try? FileManager.default.removeItem(at: url) }
        let capture = try capture(at: url)
        var sample = PlaybackDiagnosticSample()
        sample.modelID = UUID()
        sample.playerID = .max
        sample.itemID = .max
        sample.windowID = .max
        sample.positionSeconds = .greatestFiniteMagnitude
        sample.rate = .greatestFiniteMagnitude
        sample.bufferedUntilSeconds = .greatestFiniteMagnitude
        sample.seekTargetSeconds = .greatestFiniteMagnitude
        sample.width = .greatestFiniteMagnitude
        sample.height = .greatestFiniteMagnitude
        sample.accessObservedBitrate = .greatestFiniteMagnitude
        sample.accessIndicatedBitrate = .greatestFiniteMagnitude
        sample.timeControlStatus = .max
        sample.itemStatus = .max
        sample.sceneState = .max
        sample.errorCode = .min
        sample.accessStalls = .max
        sample.playbackRequested = true
        sample.bufferEmpty = false
        sample.likelyToKeepUp = true
        sample.displayReady = true
        sample.usesClientDemux = true
        sample.errorDomain = .avFoundation
        let uptime = ProcessInfo.processInfo.systemUptime
        for offset in 0..<2_100 {
            if !capture.record(.accessLog, sample: sample, uptime: uptime + Double(offset)) { break }
        }
        _ = try await capture.finish()
        let rows = try read(url)
        XCTAssertLessThan(rows.count, PlaybackDiagnosticCapture.maximumRecords)
        XCTAssertLessThanOrEqual(try Data(contentsOf: url).count, PlaybackDiagnosticCapture.maximumBytes)
        XCTAssertEqual(rows.last?["event"] as? String, "captureStopped")
        let times = rows.compactMap { $0["elapsedSeconds"] as? Double }
        XCTAssertEqual(times, times.sorted())
    }

    func testMetadataRejectsArbitraryTextWithoutTouchingFile() throws {
        let url = temporaryFile()
        defer { try? FileManager.default.removeItem(at: url) }
        for token in ["", "1..2", "1\n2", "secret", "https://example.invalid", String(repeating: "1", count: 33)] {
            XCTAssertThrowsError(try PlaybackDiagnosticCapture.start(
                fileURL: url, version: token, build: "24",
                osVersion: .init(majorVersion: 27, minorVersion: 0, patchVersion: 0)))
            XCTAssertThrowsError(try PlaybackDiagnosticCapture.start(
                fileURL: url, version: "1.0", build: token,
                osVersion: .init(majorVersion: 27, minorVersion: 0, patchVersion: 0)))
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testWriteFailureSurfacesAtFinalization() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let capture = try capture(at: directory.appendingPathComponent("capture.jsonl"))
        do {
            _ = try await capture.finish()
            XCTFail("Missing parent directory must fail the export")
        } catch {
            XCTAssertFalse(capture.isRecording)
        }
    }
}
