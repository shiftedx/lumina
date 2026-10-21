#!/usr/bin/env python3
"""Compare the synchronous and queued native music checkpoint paths.

Run from the repository root with two Core package paths. Each sample uses a
fresh synthetic member and removes only its own Application Support directory.
The before binary measures the old synchronous call; the after binary reports
both synchronous main-actor admission and completion after its writer drains.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile


RUNNER = r'''import CryptoKit
import Foundation
import LuminaCore

@main @MainActor struct QueueCheckpointBenchmark {
    static func main() async throws {
        let count = Int(CommandLine.arguments[1])!
        let samples = Int(CommandLine.arguments[2])!
        var rows = [[String: Any]]()
        for _ in 0..<samples {
            let member = "queue-benchmark-\(UUID().uuidString)"
            let session = LuminaSession(serverURL: URL(string: "https://queue-benchmark.invalid")!, accessToken: "synthetic",
                userID: member, username: "Synthetic", serverName: "Synthetic", deviceID: "benchmark")
            let directory = ownerDirectory(session)
            defer { try? FileManager.default.removeItem(at: directory) }
            let snapshot = try syntheticSnapshot(count: count)
            let library = LuminaMusicLibrary()
            library.configure(session: session)
            let started = ContinuousClock.now
            library.persistCheckpoint(snapshot: snapshot)
            let admission = started.duration(to: .now)
            FLUSH
            let completion = started.duration(to: .now)
            let data = try Data(contentsOf: directory.appending(path: "queue.json"))
            let saved = try JSONDecoder().decode(MusicListeningSnapshot.self, from: data)
            let checksum = saved.queue.reduce(0) { $0 + $1.id.utf8.count }
            guard saved.revision == snapshot.revision, saved.queue.count == count,
                  saved.queue.allSatisfy({ $0.audioURL.isEmpty && $0.artworkURL == nil })
            else { throw POSIXError(.EIO) }
            var updated = snapshot
            updated.revision = 2; updated.positionSeconds = 7
            let warmStarted = ContinuousClock.now
            library.persistCheckpoint(snapshot: updated)
            let warmAdmission = warmStarted.duration(to: .now)
            FLUSH
            let warmCompletion = warmStarted.duration(to: .now)
            let warmData = try Data(contentsOf: directory.appending(path: "queue.json"))
            let warmSaved = try JSONDecoder().decode(MusicListeningSnapshot.self, from: warmData)
            guard warmSaved.revision == updated.revision, warmSaved.positionSeconds == 7,
                  warmSaved.queue.reduce(0, { $0 + $1.id.utf8.count }) == checksum
            else { throw POSIXError(.EIO) }
            rows.append(["cold_admission_ms": milliseconds(admission), "cold_completion_ms": milliseconds(completion),
                         "warm_admission_ms": milliseconds(warmAdmission), "warm_completion_ms": milliseconds(warmCompletion),
                         "bytes": data.count, "warm_bytes": warmData.count, "checksum": checksum])
            try? FileManager.default.removeItem(at: directory)
        }
        print(String(decoding: try JSONSerialization.data(withJSONObject: [
            "items": count, "samples": rows, "mode": "MODE",
        ], options: [.sortedKeys]), as: UTF8.self))
    }

    static func syntheticSnapshot(count: Int) throws -> MusicListeningSnapshot {
        let tracks = (0..<count).map { index -> [String: Any] in
            ["id": String(format: "%08d-0000-0000-0000-000000000000", index), "title": "Synthetic track \(index)",
             "artist": "Synthetic Artist", "album_artist": "Synthetic Artist", "album": "Synthetic Album",
             "audio_url": "https://queue-benchmark.invalid/Audio/\(index)?ApiKey=synthetic", "artwork_url": "/Items/\(index)",
             "gain_is_manual": false, "genres": ["Synthetic"]]
        }
        let object: [String: Any] = ["revision": 1, "queue": tracks, "current_index": count - 1,
                                     "position_seconds": 19, "repeat": "all", "shuffle": false,
                                     "active_output": ["kind": "browser"]]
        return try JSONDecoder().decode(MusicListeningSnapshot.self, from: JSONSerialization.data(withJSONObject: object))
    }

    static func ownerDirectory(_ session: LuminaSession) -> URL {
        let identity = SHA256.hash(data: Data((session.serverURL.absoluteString.lowercased() + "|" + session.userID).utf8))
            .map { String(format: "%02x", $0) }.joined()
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "LuminaMusic", directoryHint: .isDirectory)
            .appending(path: identity, directoryHint: .isDirectory)
    }

    static func milliseconds(_ duration: Duration) -> Double {
        let parts = duration.components
        return Double(parts.seconds) * 1_000 + Double(parts.attoseconds) / 1e15
    }
}
'''


def build(core: pathlib.Path, mode: str, root: pathlib.Path) -> pathlib.Path:
    (root / "Sources" / "QueueCheckpointBenchmark").mkdir(parents=True)
    (root / "Package.swift").write_text(
        "// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package("
        'name: "QueueCheckpointBenchmark", platforms: [.macOS(.v15)], dependencies: [.package(path: ' + json.dumps(str(core.resolve())) + ")], "
        'targets: [.executableTarget(name: "QueueCheckpointBenchmark", dependencies: [.product(name: "LuminaCore", package: "Core")])])\n'
    )
    flush = "await library.flushCheckpoint()" if mode == "after" else ""
    (root / "Sources" / "QueueCheckpointBenchmark" / "main.swift").write_text(RUNNER.replace("MODE", mode).replace("FLUSH", flush))
    subprocess.run(["swift", "build", "-c", "release", "--package-path", str(root)], check=True,
                   stdout=sys.stderr, stderr=sys.stderr)
    return root / ".build" / "release" / "QueueCheckpointBenchmark"


def run(binary: pathlib.Path, count: int, samples: int) -> dict:
    output = subprocess.check_output([str(binary), str(count), str(samples)], text=True)
    return json.loads(output)


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def percentile95(values):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * .95 + .999999))]


def summary(values):
    return {"median_ms": median(values), "p95_ms": percentile95(values)}


def summarize(row):
    return {
        "cold_admission": summary([sample["cold_admission_ms"] for sample in row["samples"]]),
        "cold_completion": summary([sample["cold_completion_ms"] for sample in row["samples"]]),
        "warm_admission": summary([sample["warm_admission_ms"] for sample in row["samples"]]),
        "warm_completion": summary([sample["warm_completion_ms"] for sample in row["samples"]]),
        "bytes": row["samples"][0]["bytes"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-core", type=pathlib.Path, required=True)
    parser.add_argument("--after-core", type=pathlib.Path, required=True)
    parser.add_argument("--samples", type=int, default=11)
    arguments = parser.parse_args()
    if arguments.samples < 3 or arguments.samples % 2 == 0:
        raise SystemExit("--samples must be an odd number of at least 3")
    with tempfile.TemporaryDirectory(prefix="lumina-queue-benchmark-") as temporary:
        root = pathlib.Path(temporary)
        before_binary = build(arguments.before_core, "before", root / "before")
        after_binary = build(arguments.after_core, "after", root / "after")
        output = {}
        for count in (100, 1_000, 10_000):
            before_rows, after_rows = [], []
            for index in range(arguments.samples):
                order = ((before_binary, before_rows), (after_binary, after_rows)) if index % 2 == 0 else ((after_binary, after_rows), (before_binary, before_rows))
                for binary, rows in order:
                    rows.extend(run(binary, count, 1)["samples"])
            before = {"items": count, "mode": "before", "samples": before_rows}
            after = {"items": count, "mode": "after", "samples": after_rows}
            output[str(count)] = {
                "before": summarize(before), "after": summarize(after),
                "raw": {"before": before["samples"], "after": after["samples"]},
            }
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
