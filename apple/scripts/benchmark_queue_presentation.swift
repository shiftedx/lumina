import Foundation

// From apple/:
// mise exec -- swiftc -swift-version 6 -parse-as-library -O -whole-module-optimization App/Shared/QueuePresentation.swift scripts/benchmark_queue_presentation.swift -o /tmp/lumina-queue-presentation-benchmark
// mise exec -- ../scripts/with-lock.sh /tmp/lumina-perf.lock 1800 /tmp/lumina-queue-presentation-benchmark
// The current rows are the production QueuePresentation implementation. The
// baseline function is the exact pre-change PlaybackModel queueRows body, with
// only its local item type substituted so it can compile beside the helper.
// This measures model work, not SwiftUI rendering.
@main
struct QueuePresentationBenchmark {
    private struct Item { let id: String }
    private struct BaselineRow { let id: UUID; let item: Item; let duplicated: Bool }

    static func main() {
        for count in [100, 1_000, 10_000] {
            let items = (0..<count).map { Item(id: "track-\($0 % max(1, count / 10))") }
            let occurrences = items.map { _ in UUID() }
            let baseline = baselineRows(items: items, occurrenceIDs: occurrences)
            let cached = currentRows(items: items, occurrenceIDs: occurrences)
            let baselineChecksum = rowChecksum(baseline)
            let currentChecksum = rowChecksum(cached)
            guard baseline.count == cached.count,
                  baselineChecksum == currentChecksum,
                  zip(baseline, cached).enumerated().allSatisfy({ offset, pair in
                      pair.0.id == pair.1.id && pair.0.item.id == pair.1.item.id
                          && pair.0.duplicated == pair.1.duplicated && pair.1.index == offset
                  }) else { fatalError("queue rows changed") }
            var rebuild = [Double](), before = [Double](), after = [Double]()
            var consumedChecksum = 0
            for sample in 0..<21 {
                let runRebuild = { milliseconds { consume(currentRows(items: items, occurrenceIDs: occurrences), into: &consumedChecksum) } }
                let runBefore = { milliseconds { for _ in 0..<100 { consume(baselineRows(items: items, occurrenceIDs: occurrences), into: &consumedChecksum) } } }
                let runAfter = { milliseconds { for _ in 0..<100 { consume(cached, into: &consumedChecksum) } } }
                let currentRebuild: Double
                let currentBefore: Double
                let currentAfter: Double
                if sample.isMultiple(of: 2) {
                    currentAfter = runAfter(); currentBefore = runBefore()
                    currentRebuild = runRebuild()
                } else {
                    currentBefore = runBefore(); currentAfter = runAfter()
                    currentRebuild = runRebuild()
                }
                rebuild.append(currentRebuild); before.append(currentBefore); after.append(currentAfter)
            }
            print("items=\(count) reads_per_sample=100 runs=21 baseline_checksum=\(baselineChecksum) current_checksum=\(currentChecksum) consumed_checksum=\(consumedChecksum)")
            print("rebuild median ms=\(format(median(rebuild)))")
            print("before rows rebuilt per read median ms=\(format(median(before) / 100))")
            print("after cached rows iterated per read median ms=\(format(median(after) / 100))")
        }
    }

    private static func currentRows(items: [Item], occurrenceIDs: [UUID]) -> [QueuePresentationRow<Item>] {
        QueuePresentation.rows(items: items, occurrenceIDs: occurrenceIDs, itemID: { $0.id })
    }

    private static func baselineRows(items: [Item], occurrenceIDs: [UUID]) -> [BaselineRow] {
        let counts = Dictionary(grouping: items, by: \.id).mapValues(\.count)
        return zip(occurrenceIDs, items).map { BaselineRow(id: $0.0, item: $0.1, duplicated: (counts[$0.1.id] ?? 0) > 1) }
    }

    @inline(never) private static func consume(_ rows: [BaselineRow], into checksum: inout Int) {
        checksum &+= rows.reduce(0) { $0 &+ $1.item.id.utf8.count &+ ($1.duplicated ? 1 : 0) }
    }
    @inline(never) private static func consume(_ rows: [QueuePresentationRow<Item>], into checksum: inout Int) {
        checksum &+= rows.reduce(0) { $0 &+ $1.item.id.utf8.count &+ ($1.duplicated ? 1 : 0) }
    }
    private static func rowChecksum(_ rows: [BaselineRow]) -> Int {
        rows.reduce(0) { $0 &+ $1.item.id.utf8.count &+ ($1.duplicated ? 1 : 0) }
    }
    private static func rowChecksum(_ rows: [QueuePresentationRow<Item>]) -> Int {
        rows.reduce(0) { $0 &+ $1.item.id.utf8.count &+ ($1.duplicated ? 1 : 0) }
    }
    private static func milliseconds(_ operation: () -> Void) -> Double {
        let clock = ContinuousClock(); let started = clock.now; operation()
        let parts = started.duration(to: clock.now).components
        return Double(parts.seconds) * 1_000 + Double(parts.attoseconds) / 1e15
    }
    private static func median(_ values: [Double]) -> Double { values.sorted()[values.count / 2] }
    private static func format(_ value: Double) -> String { String(format: "%.4f", value) }
}
