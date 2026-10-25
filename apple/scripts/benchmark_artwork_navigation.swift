import CoreGraphics
import Darwin
import Foundation
import ImageIO

// From apple/, compile the production cache and preparation implementation directly:
// mise exec -- swiftc -swift-version 6 -O Core/Sources/LuminaCore/BillboardArtworkPreparation.swift Core/Sources/LuminaCore/ArtworkImageCache.swift scripts/benchmark_artwork_navigation.swift -o /tmp/lumina-artwork-navigation-benchmark
// Run the same no-argument executable three times in a fresh process for each revision:
// /tmp/lumina-artwork-navigation-benchmark

@main
struct ArtworkNavigationBenchmark {
    private static let navigationCallerCount = 29
    private static let totalCallerCount = 32
    fileprivate static let responseDelayMilliseconds = 150
    private static let cancellationBoundMilliseconds = 30

    @MainActor
    static func main() async {
        do {
            writeJSON(try await run())
        } catch {
            writeJSON([
                "status": "failed",
                "error": (error as? BenchmarkFailure)?.message ?? String(describing: error),
            ])
            Darwin.exit(EXIT_FAILURE)
        }
    }

    @MainActor
    private static func run() async throws -> [String: Any] {
        let fixture = try syntheticJPEG(width: 400, height: 600)
        let metrics = NavigationMetrics()
        let decoder = ArtworkDecodeCounter()
        DelayedArtworkURLProtocol.install(fixture: fixture, metrics: metrics)

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [DelayedArtworkURLProtocol.self]
        configuration.httpMaximumConnectionsPerHost = 64
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 10
        let session = URLSession(configuration: configuration)
        let cache = ArtworkImageCache(
            session: session,
            // Keep both cache tiers below this fixture so the benchmark harness does not retain
            // cancelled navigation images after the view boundary discards them.
            maximumDecodedBytes: 1,
            imageCountLimit: 1,
            maximumEncodedBytes: 0,
            decode: { data in
                decoder.record()
                return BillboardArtworkPreparation.image(from: data)
            }
        )
        defer {
            cache.removeAll()
            session.invalidateAndCancel()
            DelayedArtworkURLProtocol.clearConfiguration()
        }

        let navigationURLs = (0..<navigationCallerCount).map { index in
            URL(string: "https://artwork-navigation-\(index).example/Items/navigation-\(index)/Images/Primary?fixture=cancelled-navigation-v1")!
        }
        let visibleURL = URL(string: "https://artwork-visible.example/Items/visible/Images/Primary?fixture=cancelled-navigation-v1")!
        let sharedURL = URL(string: "https://artwork-shared.example/Items/shared/Images/Primary?fixture=cancelled-navigation-v1")!

        // The 32 callers are 29 unique cancelled navigation cards, one visible card,
        // and two callers for one shared card. Only the shared card's second caller survives.
        let callerCount = navigationURLs.count + 1 + 2
        guard callerCount == totalCallerCount else {
            throw BenchmarkFailure(message: "fixture caller count is \(callerCount), expected \(totalCallerCount)")
        }
        let distinctURLCount = navigationURLs.count + 2

        let clock = ContinuousClock()
        let startedAt = clock.now
        let initialCPU = try processCPUMilliseconds()

        let navigationTasks: [Task<ConsumerOutcome, Never>] = navigationURLs.map { url in
            Task { @MainActor in
                await consume(cache: cache, url: url, metrics: metrics)
            }
        }
        let visibleTask = Task { @MainActor in
            await consume(cache: cache, url: visibleURL, metrics: metrics)
        }
        let sharedCancelledTask = Task { @MainActor in
            await consume(cache: cache, url: sharedURL, metrics: metrics)
        }
        let sharedVisibleTask = Task { @MainActor in
            await consume(cache: cache, url: sharedURL, metrics: metrics)
        }

        let allRequestsStarted = await metrics.waitForNetworkStarts(
            atLeast: distinctURLCount,
            withinMilliseconds: cancellationBoundMilliseconds
        )
        let cancellationSnapshot = metrics.snapshot()
        let cancellationTrigger = allRequestsStarted ? "all_network_starts" : "30_ms_bound"
        navigationTasks.forEach { $0.cancel() }
        sharedCancelledTask.cancel()

        var navigationOutcomes = [ConsumerOutcome]()
        navigationOutcomes.reserveCapacity(navigationTasks.count)
        for task in navigationTasks {
            navigationOutcomes.append(await task.value)
        }
        let visibleOutcome = await visibleTask.value
        let sharedCancelledOutcome = await sharedCancelledTask.value
        let sharedVisibleOutcome = await sharedVisibleTask.value

        let settled = await metrics.waitForSettled(withinMilliseconds: responseDelayMilliseconds + 250)
        let finalSnapshot = metrics.snapshot()
        let elapsedMilliseconds = milliseconds(startedAt.duration(to: clock.now))
        let processCPU = try processCPUMilliseconds() - initialCPU

        guard finalSnapshot.callerStarts == callerCount else {
            throw BenchmarkFailure(message: "only (finalSnapshot.callerStarts) of (callerCount) callers reached cache.image(for:)")
        }
        let expectedDimensions = ImageDimensions(width: 400, height: 600)
        guard case let .received(visibleDimensions) = visibleOutcome,
              visibleDimensions == expectedDimensions else {
            throw BenchmarkFailure(message: "the visible consumer did not receive the expected image")
        }
        guard case let .received(sharedDimensions) = sharedVisibleOutcome,
              sharedDimensions == expectedDimensions else {
            throw BenchmarkFailure(message: "the surviving shared consumer did not receive the expected image")
        }
        guard sharedCancelledOutcome == .discarded else {
            throw BenchmarkFailure(message: "the cancelled shared consumer retained or used an image")
        }
        guard navigationOutcomes.allSatisfy({ $0 == .discarded }) else {
            throw BenchmarkFailure(message: "a cancelled navigation consumer retained or used an image")
        }

        if CommandLine.arguments.contains("--check") {
            guard cancellationSnapshot.responseHandlersBegun == 0 else {
                throw BenchmarkFailure(message: "host missed the controlled cancellation window; rerun on a quieter host")
            }
            guard finalSnapshot.networkStarts <= 6, decoder.decodeCount == 2 else {
                throw BenchmarkFailure(message: "cancelled navigation exceeded its six-request/two-decode budget")
            }
        }

        return [
            "status": "ok",
            "caller_count": callerCount,
            "navigation_caller_count": navigationURLs.count,
            "cancelled_caller_count": navigationURLs.count + 1,
            "visible_caller_count": 2,
            "distinct_url_count": distinctURLCount,
            "response_delay_ms": responseDelayMilliseconds,
            "cancellation_bound_ms": cancellationBoundMilliseconds,
            "cancellation_trigger": cancellationTrigger,
            "caller_starts_at_cancellation": cancellationSnapshot.callerStarts,
            "caller_starts": finalSnapshot.callerStarts,
            "network_starts_at_cancellation": cancellationSnapshot.networkStarts,
            "response_handlers_begun_at_cancellation": cancellationSnapshot.responseHandlersBegun,
            "network_starts": finalSnapshot.networkStarts,
            "network_completions": finalSnapshot.networkCompletions,
            "network_cancellations": finalSnapshot.networkCancellations,
            "response_handlers_begun": finalSnapshot.responseHandlersBegun,
            "shared_url_network_starts": finalSnapshot.networkStarts(for: sharedURL),
            "decode_count": decoder.decodeCount,
            "elapsed_ms": elapsedMilliseconds,
            "process_cpu_ms": processCPU,
            "network_events_settled": settled,
            "fixture": [
                "source_width": expectedDimensions.width,
                "source_height": expectedDimensions.height,
                "jpeg_bytes": fixture.count,
            ],
            "visible_image_dimensions": [
                "width": visibleDimensions.width,
                "height": visibleDimensions.height,
            ],
            "shared_image_dimensions": [
                "width": sharedDimensions.width,
                "height": sharedDimensions.height,
            ],
        ]
    }

    @MainActor
    private static func consume(
        cache: ArtworkImageCache,
        url: URL,
        metrics: NavigationMetrics
    ) async -> ConsumerOutcome {
        metrics.recordCallerStart()
        let image = await cache.image(for: url)
        // This is the view boundary: a cancelled view discards the result and does not retain
        // the CGImage or even read its dimensions. Only surviving consumers report dimensions.
        guard !Task.isCancelled else { return .discarded }
        guard let image else { return .missing }
        return .received(ImageDimensions(width: image.width, height: image.height))
    }

    private static func syntheticJPEG(width: Int, height: Int) throws -> Data {
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ), let image = {
            context.setFillColor(CGColor(red: 0.1, green: 0.3, blue: 0.6, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            return context.makeImage()
        }() else {
            throw BenchmarkFailure(message: "could not create the synthetic source image")
        }

        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else {
            throw BenchmarkFailure(message: "could not create the synthetic JPEG destination")
        }
        CGImageDestinationAddImage(destination, image, [
            kCGImageDestinationLossyCompressionQuality: 0.9,
        ] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else {
            throw BenchmarkFailure(message: "could not finalize the synthetic JPEG")
        }
        return data as Data
    }

    private static func processCPUMilliseconds() throws -> Double {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else {
            throw BenchmarkFailure(message: "getrusage failed")
        }
        let userSeconds = Double(usage.ru_utime.tv_sec) + Double(usage.ru_utime.tv_usec) / 1_000_000
        let systemSeconds = Double(usage.ru_stime.tv_sec) + Double(usage.ru_stime.tv_usec) / 1_000_000
        return (userSeconds + systemSeconds) * 1_000
    }

    private static func milliseconds(_ duration: Duration) -> Double {
        let parts = duration.components
        return Double(parts.seconds) * 1_000 + Double(parts.attoseconds) / 1e15
    }

    private static func writeJSON(_ object: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
            ?? Data("{\"status\":\"failed\",\"error\":\"JSON encoding failed\"}".utf8)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }

    private struct BenchmarkFailure: Error {
        let message: String
    }

    private struct ImageDimensions: Sendable, Equatable {
        let width: Int
        let height: Int
    }

    private enum ConsumerOutcome: Sendable, Equatable {
        case discarded
        case missing
        case received(ImageDimensions)
    }
}

private final class ArtworkDecodeCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var decodeCount: Int { lock.withLock { count } }

    func record() {
        lock.withLock { count += 1 }
    }
}

private final class NavigationMetrics: @unchecked Sendable {
    struct Snapshot: Sendable {
        let callerStarts: Int
        let networkStarts: Int
        let responseHandlersBegun: Int
        let networkCompletions: Int
        let networkCancellations: Int
        private let startsByURL: [String: Int]

        init(
            callerStarts: Int,
            networkStarts: Int,
            responseHandlersBegun: Int,
            networkCompletions: Int,
            networkCancellations: Int,
            startsByURL: [String: Int]
        ) {
            self.callerStarts = callerStarts
            self.networkStarts = networkStarts
            self.responseHandlersBegun = responseHandlersBegun
            self.networkCompletions = networkCompletions
            self.networkCancellations = networkCancellations
            self.startsByURL = startsByURL
        }

        func networkStarts(for url: URL) -> Int {
            startsByURL[url.absoluteString, default: 0]
        }
    }

    private let lock = NSLock()
    private var callerStarts = 0
    private var networkStarts = 0
    private var responseHandlersBegun = 0
    private var networkCompletions = 0
    private var networkCancellations = 0
    private var startsByURL = [String: Int]()

    func recordCallerStart() {
        lock.withLock { callerStarts += 1 }
    }

    func recordNetworkStart(url: URL) {
        lock.withLock {
            networkStarts += 1
            startsByURL[url.absoluteString, default: 0] += 1
        }
    }

    func recordResponseHandlerBegan() {
        lock.withLock { responseHandlersBegun += 1 }
    }

    func recordNetworkCompletion() {
        lock.withLock { networkCompletions += 1 }
    }

    func recordNetworkCancellation() {
        lock.withLock { networkCancellations += 1 }
    }

    func snapshot() -> Snapshot {
        lock.withLock {
            Snapshot(
                callerStarts: callerStarts,
                networkStarts: networkStarts,
                responseHandlersBegun: responseHandlersBegun,
                networkCompletions: networkCompletions,
                networkCancellations: networkCancellations,
                startsByURL: startsByURL
            )
        }
    }

    func waitForNetworkStarts(atLeast expected: Int, withinMilliseconds limit: Int) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .milliseconds(limit))
        while snapshot().networkStarts < expected, clock.now < deadline {
            try? await Task.sleep(for: .milliseconds(1))
        }
        return snapshot().networkStarts >= expected
    }

    func waitForSettled(withinMilliseconds limit: Int) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .milliseconds(limit))
        while true {
            let current = snapshot()
            if current.networkStarts <= current.networkCompletions + current.networkCancellations {
                return true
            }
            guard clock.now < deadline else { return false }
            try? await Task.sleep(for: .milliseconds(1))
        }
    }
}

private final class DelayedArtworkURLProtocol: URLProtocol, @unchecked Sendable {
    private static let configuration = ProtocolConfiguration()

    private let stateLock = NSLock()
    private var hasStarted = false
    private var isStopped = false
    private var isFinished = false
    private var workItem: DispatchWorkItem?
    private var metrics: NavigationMetrics?

    static func install(fixture: Data, metrics: NavigationMetrics) {
        configuration.install(fixture: fixture, metrics: metrics)
    }

    static func clearConfiguration() {
        configuration.clear()
    }

    override class func canInit(with request: URLRequest) -> Bool {
        request.url != nil
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let (fixture, metrics) = Self.configuration.snapshot() else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }

        let shouldStart = stateLock.withLock { () -> Bool in
            guard !hasStarted, !isStopped else { return false }
            hasStarted = true
            self.metrics = metrics
            return true
        }
        guard shouldStart else { return }

        let url = request.url!
        metrics.recordNetworkStart(url: url)
        let work = DispatchWorkItem { [weak self] in
            self?.deliver(fixture: fixture)
        }
        let shouldSchedule = stateLock.withLock { () -> Bool in
            guard !isStopped else { return false }
            workItem = work
            return true
        }
        guard shouldSchedule else {
            work.cancel()
            return
        }
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + .milliseconds(ArtworkNavigationBenchmark.responseDelayMilliseconds),
            execute: work
        )
    }

    override func stopLoading() {
        guard let stopped = stateLock.withLock({ () -> (DispatchWorkItem?, NavigationMetrics)? in
            guard hasStarted, !isStopped, !isFinished, let metrics else { return nil }
            isStopped = true
            return (workItem, metrics)
        }) else {
            return
        }
        stopped.0?.cancel()
        stopped.1.recordNetworkCancellation()
    }

    private func deliver(fixture: Data) {
        guard beginResponse(), let metrics = stateLock.withLock({ self.metrics }) else { return }
        metrics.recordResponseHandlerBegan()
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: 200,
            httpVersion: nil,
            headerFields: ["Content-Length": String(fixture.count)]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        guard isActive() else { return }
        client?.urlProtocol(self, didLoad: fixture)
        guard finish() else { return }
        client?.urlProtocolDidFinishLoading(self)
        metrics.recordNetworkCompletion()
    }

    private func beginResponse() -> Bool {
        stateLock.withLock {
            guard !isStopped, !isFinished else { return false }
            return true
        }
    }

    private func isActive() -> Bool {
        stateLock.withLock { !isStopped && !isFinished }
    }

    private func finish() -> Bool {
        stateLock.withLock {
            guard !isStopped, !isFinished else { return false }
            isFinished = true
            return true
        }
    }
}

private final class ProtocolConfiguration: @unchecked Sendable {
    private let lock = NSLock()
    private var fixture: Data?
    private var metrics: NavigationMetrics?

    func install(fixture: Data, metrics: NavigationMetrics) {
        lock.withLock {
            self.fixture = fixture
            self.metrics = metrics
        }
    }

    func snapshot() -> (Data, NavigationMetrics)? {
        lock.withLock {
            guard let fixture, let metrics else { return nil }
            return (fixture, metrics)
        }
    }

    func clear() {
        lock.withLock {
            fixture = nil
            metrics = nil
        }
    }
}
