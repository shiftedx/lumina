import Darwin
import Foundation
import LuminaCore

/// Synthetic manifest/list benchmark; no network, real media or credentials.
@main
struct OfflineLibraryBenchmark {
    @MainActor
    static func main() throws {
        let count = Int(CommandLine.arguments.dropFirst().first ?? "1000") ?? 1000
        guard (1...10000).contains(count) else { throw POSIXError(.EINVAL) }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let session = LuminaSession(serverURL: URL(string: "https://offline.example")!, accessToken: "synthetic",
            userID: "member", username: "Synthetic", serverName: "Synthetic", deviceID: "benchmark")
        let seed = LuminaOfflineLibrary(root: root)
        seed.configure(session: session)
        let directory = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)[0]
        var entries = [[String: Any]]()
        for index in 0..<count {
            let filename = UUID().uuidString + ".m4a"
            try Data(repeating: 0, count: 32).write(to: directory.appendingPathComponent(filename))
            entries.append(["item": ["Id": "synthetic-\(index)", "Name": "Synthetic track \(index)", "Type": "Audio"],
                            "filename": filename, "bytes": 32])
        }
        try JSONSerialization.data(withJSONObject: entries).write(to: directory.appendingPathComponent("manifest.json"))
        let library = LuminaOfflineLibrary(root: root)
        library.configure(session: session)
        guard library.downloadedItems.count == count else { throw POSIXError(.EIO) }
        var wall = [Double]()
        var cpu = [Double]()
        var checksum = 0
        for _ in 0..<21 {
            let started = ContinuousClock.now
            let initialCPU = cpuMilliseconds()
            let items = library.downloadedItems
            checksum += items.reduce(0) { $0 + $1.id.utf8.count }
            let elapsed = started.duration(to: .now).components
            wall.append(Double(elapsed.seconds) * 1000 + Double(elapsed.attoseconds) / 1e15)
            cpu.append(cpuMilliseconds() - initialCPU)
        }
        // Opening a file must revalidate storage even if the displayed list is a snapshot.
        guard let first = library.localURL(for: "synthetic-0") else { throw POSIXError(.ENOENT) }
        try FileManager.default.removeItem(at: first)
        guard library.localURL(for: "synthetic-0") == nil,
              library.downloadedItems.count == count - 1 else { throw POSIXError(.EIO) }
        library.configure(session: nil)
        guard library.downloadedItems.isEmpty else { throw POSIXError(.EIO) }
        print(String(decoding: try JSONSerialization.data(withJSONObject: [
            "items": count, "median_list_wall_ms": wall.sorted()[10],
            "median_list_cpu_ms": cpu.sorted()[10], "checksum": checksum,
            "purge_and_owner_checks_passed": true,
        ], options: .sortedKeys), as: UTF8.self))
    }

    static func cpuMilliseconds() -> Double {
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        return Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000
    }
}

