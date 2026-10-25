import Foundation

/// A finite, inclusive media-time range offered by the active video item.
///
/// The app layer translates AVFoundation's `CMTimeRange`s at the boundary so
/// gesture math can remain deterministic and independently testable.
public struct VideoSeekRange: Equatable, Sendable {
    public let lowerBound: Double
    public let upperBound: Double

    public init?(_ lowerBound: Double, _ upperBound: Double) {
        guard lowerBound.isFinite, upperBound.isFinite, lowerBound <= upperBound else { return nil }
        self.lowerBound = lowerBound
        self.upperBound = upperBound
    }

    public var duration: Double { upperBound - lowerBound }
    public var span: ClosedRange<Double> { lowerBound...upperBound }
    public func contains(_ time: Double) -> Bool { time >= lowerBound && time <= upperBound }
}

public enum VideoSeekDirection: Equatable, Sendable {
    case backward
    case forward
}

/// Identifies one engine seek issued by `VideoSeekAuthority`.
public struct VideoSeekSubmission: Equatable, Sendable {
    public let operationID: UUID
    public let generation: Int
    public let revision: Int
    public let target: Double
    /// The request's intended direction, retained when a coalesced target is
    /// submitted after another seek completes.
    public let direction: VideoSeekDirection?
}

/// The result of an engine seek completion.
public enum VideoSeekCompletion: Equatable, Sendable {
    /// A completion from a replaced item or superseded operation.
    case ignored
    /// Submit this coalesced, newer target next.
    case submit(VideoSeekSubmission)
    /// No newer target remains; reconcile the UI to this engine position.
    case settled(Double?)
}

/// One shared authority for timeline, button, and gesture video seeks.
///
/// The authority intentionally has no AVFoundation dependency. It serializes
/// requests, holds the optimistic target while a seek is in flight, and drops
/// stale completions after the item changes.
public struct VideoSeekAuthority: Sendable {
    private struct Pending: Sendable {
        let generation: Int
        let revision: Int
        let target: Double
        let direction: VideoSeekDirection
    }

    public private(set) var optimisticTarget: Double?
    public private(set) var ranges: [VideoSeekRange] = []
    public private(set) var generation = 0
    public private(set) var revision = 0
    private var inFlight: VideoSeekSubmission?
    private var latest: Pending?

    public init() { }

    /// Normalizes arbitrary finite ranges, merging touching/overlapping spans.
    public static func normalizedRanges(_ ranges: [VideoSeekRange]) -> [VideoSeekRange] {
        let sorted = ranges.sorted { lhs, rhs in
            lhs.lowerBound == rhs.lowerBound ? lhs.upperBound < rhs.upperBound : lhs.lowerBound < rhs.lowerBound
        }
        return sorted.reduce(into: []) { result, range in
            guard let previous = result.last else { result.append(range); return }
            if range.lowerBound <= previous.upperBound {
                result.removeLast()
                result.append(VideoSeekRange(previous.lowerBound, max(previous.upperBound, range.upperBound))!)
            } else {
                result.append(range)
            }
        }
    }

    /// Projects a candidate into a valid seek span. Gaps resolve to the nearest
    /// boundary; exact ties follow the caller's movement direction.
    public static func project(_ candidate: Double, into ranges: [VideoSeekRange], direction: VideoSeekDirection) -> Double? {
        guard candidate.isFinite else { return nil }
        let normalized = normalizedRanges(ranges)
        guard !normalized.isEmpty else { return nil }
        if let containing = normalized.first(where: { $0.contains(candidate) }) { return candidate.clamped(to: containing) }
        var best: (time: Double, distance: Double)?
        for range in normalized {
            for boundary in [range.lowerBound, range.upperBound] {
                let distance = abs(boundary - candidate)
                guard let current = best else { best = (boundary, distance); continue }
                if distance < current.distance {
                    best = (boundary, distance)
                } else if distance == current.distance {
                    let preferred = direction == .forward ? max(current.time, boundary) : min(current.time, boundary)
                    best = (preferred, distance)
                }
            }
        }
        return best?.time
    }

    /// Accepts a desired target. A non-nil result must be issued to the engine;
    /// otherwise an older seek is in flight and this target is coalesced.
    public mutating func request(target candidate: Double, ranges input: [VideoSeekRange], direction: VideoSeekDirection) -> VideoSeekSubmission? {
        let normalized = Self.normalizedRanges(input)
        guard let target = Self.project(candidate, into: normalized, direction: direction) else { return nil }
        ranges = normalized
        revision += 1
        optimisticTarget = target
        let pending = Pending(generation: generation, revision: revision, target: target, direction: direction)
        guard inFlight == nil else {
            latest = pending
            return nil
        }
        return begin(pending)
    }

    /// Reprojects an operation immediately before the engine issues it. This is
    /// necessary for DVR/live windows that may have moved since the request was
    /// coalesced. The operation identity stays stable so a completion from the
    /// pre-revalidated target cannot settle the newer target.
    public mutating func revalidatedSubmission(
        _ submission: VideoSeekSubmission,
        ranges input: [VideoSeekRange],
        direction fallbackDirection: VideoSeekDirection
    ) -> VideoSeekSubmission? {
        guard inFlight == submission, submission.generation == generation else { return nil }
        let normalized = Self.normalizedRanges(input)
        guard !normalized.isEmpty else {
            invalidate()
            return nil
        }
        ranges = normalized

        // A newer target will be promoted when this submission completes. Do
        // not let an old operation overwrite its optimistic position.
        guard latest == nil else { return submission }
        guard let target = Self.project(submission.target, into: normalized,
                                        direction: submission.direction ?? fallbackDirection) else {
            invalidate()
            return nil
        }
        let revalidated = VideoSeekSubmission(
            operationID: submission.operationID,
            generation: submission.generation,
            revision: submission.revision,
            target: target,
            direction: submission.direction ?? fallbackDirection
        )
        inFlight = revalidated
        optimisticTarget = target
        return revalidated
    }

    /// Resolves only the current operation. Newer requests are emitted before
    /// settling, so an old completion can never snap the optimistic UI backward.
    public mutating func complete(_ submission: VideoSeekSubmission, actualTime: Double?) -> VideoSeekCompletion {
        guard inFlight == submission, submission.generation == generation else { return .ignored }
        inFlight = nil
        if let latest {
            self.latest = nil
            return .submit(begin(latest))
        }
        optimisticTarget = nil
        return .settled(actualTime?.isFinite == true ? actualTime : nil)
    }

    /// Invalidates in-flight work after item replacement, dismissal, or a
    /// terminal playback state. Late completions will be ignored.
    public mutating func invalidate() {
        generation += 1
        revision += 1
        inFlight = nil
        latest = nil
        optimisticTarget = nil
        ranges = []
    }

    private mutating func begin(_ pending: Pending) -> VideoSeekSubmission {
        let submission = VideoSeekSubmission(operationID: UUID(), generation: pending.generation,
                                             revision: pending.revision, target: pending.target,
                                             direction: pending.direction)
        inFlight = submission
        return submission
    }
}

/// Token-owned, temporary rate state. The presentation decides whether the
/// engine is currently allowed to apply a nonzero rate; this state object never
/// turns playback on by itself.
public struct VideoTemporaryRateLease: Sendable {
    public private(set) var preferredRate: Double
    public private(set) var token: UUID?

    public init(preferredRate: Double = 1) {
        self.preferredRate = preferredRate.isFinite && preferredRate > 0 ? preferredRate : 1
    }

    public var effectiveRate: Double { token == nil ? preferredRate : 2 }

    public mutating func begin(isEligible: Bool) -> UUID? {
        guard isEligible, preferredRate < 2, token == nil else { return nil }
        let token = UUID()
        self.token = token
        return token
    }

    @discardableResult public mutating func end(_ candidate: UUID) -> Bool {
        guard candidate == token else { return false }
        token = nil
        return true
    }

    public mutating func cancel() { token = nil }

    public mutating func setPreferredRate(_ rate: Double) {
        guard rate.isFinite, rate > 0 else { return }
        preferredRate = rate
        token = nil
    }

    /// A nil result means the caller must leave a paused/interrupted engine
    /// alone. Applying an effective rate is never permission to call `play()`.
    public func rateToApply(isPlaybackAllowed: Bool) -> Double? {
        isPlaybackAllowed ? effectiveRate : nil
    }
}

private extension Double {
    func clamped(to range: VideoSeekRange) -> Double { min(max(self, range.lowerBound), range.upperBound) }
}
