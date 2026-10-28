import Foundation

/// One physical display's temporary viewing brightness. External adjustments win.
public struct VideoBrightnessOwnership: Sendable {
    public private(set) var owner: UUID?
    private var baseline: Double?
    private var lastApplied: Double?
    public static let tolerance = 0.01

    public init() {}

    @discardableResult
    public mutating func observe(_ value: Double) -> UUID? {
        guard let owner, let lastApplied,
              !value.isFinite || abs(value - lastApplied) > Self.tolerance else { return nil }
        self.owner = nil
        baseline = nil
        self.lastApplied = nil
        return owner
    }

    /// Transferring a still-owned override preserves the original restoration baseline.
    public mutating func apply(_ value: Double, owner: UUID, observed: Double) -> Double? {
        guard value.isFinite, observed.isFinite else { return nil }
        observe(observed)
        if self.owner == nil { baseline = observed }
        self.owner = owner
        let applied = min(1, max(0, value))
        lastApplied = applied
        return applied
    }

    /// Continue an explicit adjustment without mistaking delayed display readback
    /// for an external change. Observation belongs at the start/end of a gesture.
    public mutating func update(_ value: Double, owner: UUID) -> Double? {
        guard self.owner == owner, value.isFinite else { return nil }
        let applied = min(1, max(0, value))
        lastApplied = applied
        return applied
    }

    public mutating func release(owner: UUID, observed: Double) -> Double? {
        guard self.owner == owner else { return nil }
        let restore = observed.isFinite && lastApplied.map { abs(observed - $0) <= Self.tolerance } == true
            ? baseline : nil
        self.owner = nil
        baseline = nil
        lastApplied = nil
        return restore
    }
}
