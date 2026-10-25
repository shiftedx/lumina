import Foundation

/// Geometry intentionally uses local player-surface coordinates. The reducer never
/// looks at global screen bounds, so folding displays and letterboxed media keep the
/// same interaction semantics.
public struct VideoGesturePoint: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

public struct VideoGestureVector: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

public struct VideoGestureSize: Equatable, Sendable {
    public var width: Double
    public var height: Double

    public init(width: Double, height: Double) { self.width = width; self.height = height }
}

public struct VideoGestureInsets: Equatable, Sendable {
    public var top: Double
    public var leading: Double
    public var bottom: Double
    public var trailing: Double

    public init(top: Double = 0, leading: Double = 0, bottom: Double = 0, trailing: Double = 0) {
        self.top = top; self.leading = leading; self.bottom = bottom; self.trailing = trailing
    }
}

public struct VideoGestureGeometry: Equatable, Sendable {
    public var size: VideoGestureSize
    public var safeArea: VideoGestureInsets

    public init(size: VideoGestureSize, safeArea: VideoGestureInsets = .init()) {
        self.size = size; self.safeArea = safeArea
    }

    public func isEligible(_ point: VideoGesturePoint, edgeExclusion: Double) -> Bool {
        let left = max(edgeExclusion, safeArea.leading)
        let right = max(edgeExclusion, safeArea.trailing)
        let top = max(edgeExclusion, safeArea.top)
        let bottom = max(edgeExclusion, safeArea.bottom)
        return point.x >= left && point.x <= size.width - right && point.y >= top && point.y <= size.height - bottom
    }
}

public enum VideoGestureAdjustment: Equatable, Sendable { case volume, brightness }
public enum VideoGestureSkipDirection: Equatable, Sendable { case forward, backward }

/// Semantic output only. Playback time, volume level, display ownership and HUDs
/// remain owned by the existing player coordinator.
public enum VideoGestureAction: Equatable, Sendable {
    case toggleControls
    case togglePlayback
    case beginAdjustment(VideoGestureAdjustment)
    case updateAdjustment(kind: VideoGestureAdjustment, delta: VideoGestureVector, size: VideoGestureSize)
    case endAdjustment(VideoGestureAdjustment)
    case cancelAdjustment(VideoGestureAdjustment)
    case beginSurfaceScrub
    case updateSurfaceScrub(delta: VideoGestureVector, size: VideoGestureSize)
    case commitSurfaceScrub(delta: VideoGestureVector, size: VideoGestureSize)
    case cancelSurfaceScrub
    /// `count` is the accepted interval count in the current burst, starting at 1
    /// for a double tap. It lets the HUD reset correctly after burst expiry.
    case relativeSkip(VideoGestureSkipDirection, count: Int)
    case beginTemporarySpeedHold
    case endTemporarySpeedHold
}

/// Preferences are explicit so presentation code can cancel/reconfigure the reducer
/// when a setting changes without teaching it about storage.
public struct VideoGestureConfiguration: Equatable, Sendable {
    public var advancedEnabled: Bool
    public var volumeEnabled: Bool
    public var brightnessEnabled: Bool
    public var horizontalSeekEnabled: Bool
    public var doubleTapEnabled: Bool
    public var twoFingerEnabled: Bool
    public var holdEnabled: Bool
    public var voiceOverEnabled: Bool
    public var edgeExclusion: Double

    public init(
        advancedEnabled: Bool = true,
        volumeEnabled: Bool = true,
        brightnessEnabled: Bool = true,
        horizontalSeekEnabled: Bool = true,
        doubleTapEnabled: Bool = true,
        twoFingerEnabled: Bool = true,
        holdEnabled: Bool = true,
        voiceOverEnabled: Bool = false,
        edgeExclusion: Double = 24
    ) {
        self.advancedEnabled = advancedEnabled
        self.volumeEnabled = volumeEnabled
        self.brightnessEnabled = brightnessEnabled
        self.horizontalSeekEnabled = horizontalSeekEnabled
        self.doubleTapEnabled = doubleTapEnabled
        self.twoFingerEnabled = twoFingerEnabled
        self.holdEnabled = holdEnabled
        self.voiceOverEnabled = voiceOverEnabled
        self.edgeExclusion = edgeExclusion
    }

    public var advancedActionsAllowed: Bool { advancedEnabled && !voiceOverEnabled }
}

public struct VideoGestureThresholds: Equatable, Sendable {
    public var panActivation: Double = 12
    public var axisDominance: Double = 1.35
    public var diagonalFallback: Double = 24
    public var tapDisplacement: Double = 12
    public var tapDuration: TimeInterval = 0.300
    public var initialTapGap: TimeInterval = 0.260
    public var burstGap: TimeInterval = 0.650
    public var holdDelay: TimeInterval = 0.450
    public var secondFingerJoin: TimeInterval = 0.120
    public var twoFingerDuration: TimeInterval = 0.350
    public var twoFingerReleaseSkew: TimeInterval = 0.120
    public var holdDrift: Double = 32

    public init() {}
}

/// Deterministic, clock-injected reducer for the main video surface. Call
/// `advance(to:)` from the UI adapter at `nextDeadline` instead of relying on real
/// sleeps. It emits no engine commands by itself.
public struct VideoGestureClassifier: Sendable {
    public private(set) var configuration: VideoGestureConfiguration
    public let thresholds: VideoGestureThresholds
    public private(set) var geometry: VideoGestureGeometry

    private enum Side: Equatable { case left, right }
    private enum Phase: Equatable {
        case pending
        case vertical(VideoGestureAdjustment, VideoGesturePoint)
        case horizontal(VideoGesturePoint)
        case speedHold(VideoGesturePoint)
        case chord
        case suppressed
    }
    private struct Contact: Equatable {
        let id: Int
        let beganAt: TimeInterval
        let initial: VideoGesturePoint
        var current: VideoGesturePoint
        let side: Side
        let eligible: Bool
        var phase: Phase
        var competesWithPendingTap: Bool
    }
    private struct PendingTap: Equatable {
        let side: Side
        let upAt: TimeInterval
        var deadline: TimeInterval
        var suspended: Bool = false
    }
    private struct SkipBurst: Equatable {
        let side: Side
        var count: Int
        var deadline: TimeInterval?
    }

    private var contacts: [Int: Contact] = [:]
    private var pendingTap: PendingTap?
    private var burst: SkipBurst?
    private var holdDeadline: TimeInterval?
    private var chordFirstDown: TimeInterval?
    private var chordFirstUp: TimeInterval?

    public init(
        configuration: VideoGestureConfiguration = .init(),
        geometry: VideoGestureGeometry,
        thresholds: VideoGestureThresholds = .init()
    ) {
        self.configuration = configuration
        self.geometry = geometry
        self.thresholds = thresholds
    }

    /// The adapter uses this to arm one safe timer. A new event or configuration
    /// update must invalidate its captured deadline before it acts.
    public var nextDeadline: TimeInterval? {
        [pendingTap?.suspended == false ? pendingTap?.deadline : nil, holdDeadline, burst?.deadline]
            .compactMap { $0 }.min()
    }

    public mutating func reconfigure(_ configuration: VideoGestureConfiguration, geometry: VideoGestureGeometry) -> [VideoGestureAction] {
        guard self.configuration != configuration || self.geometry != geometry else { return [] }
        self.configuration = configuration
        self.geometry = geometry
        return cancelAll()
    }

    public mutating func touchBegan(
        id: Int,
        at point: VideoGesturePoint,
        timestamp: TimeInterval,
        eligible: Bool = true
    ) -> [VideoGestureAction] {
        var actions = advance(to: timestamp)
        guard contacts[id] == nil else { return actions }
        let isEligible = eligible && geometry.isEligible(point, edgeExclusion: configuration.edgeExclusion)
        let side: Side = point.x < geometry.size.width / 2 ? .left : .right

        if !configuration.advancedActionsAllowed {
            // G01 survives a disabled master switch, but a second finger must not
            // become two independent immediate single-tap toggles.
            if !contacts.isEmpty {
                actions += suppressAll()
                contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: isEligible, phase: .suppressed, competesWithPendingTap: false)
                return actions
            }
            contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: isEligible, phase: .pending, competesWithPendingTap: false)
            return actions
        }

        if contacts.count >= 2 {
            actions += suppressAll()
            contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: isEligible, phase: .suppressed, competesWithPendingTap: false)
            return actions
        }

        if let first = contacts.values.first {
            guard first.phase == .pending,
                  first.eligible, isEligible,
                  configuration.twoFingerEnabled,
                  timestamp - first.beganAt <= thresholds.secondFingerJoin else {
                actions += suppressAll()
                contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: isEligible, phase: .suppressed, competesWithPendingTap: false)
                return actions
            }
            pendingTap = nil; burst = nil; holdDeadline = nil
            contacts[first.id]?.phase = .chord
            contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: true, phase: .chord, competesWithPendingTap: false)
            chordFirstDown = first.beganAt; chordFirstUp = nil
            return actions
        }

        var competes = false
        if let pendingTap, timestamp <= pendingTap.deadline {
            self.pendingTap?.suspended = true
            competes = true
        }
        if let burst, let deadline = burst.deadline, timestamp <= deadline {
            if burst.side == side {
                self.burst?.deadline = nil // continuation keeps the burst alive while resolving.
                competes = true
            } else {
                self.burst = nil
            }
        }
        contacts[id] = Contact(id: id, beganAt: timestamp, initial: point, current: point, side: side, eligible: isEligible, phase: .pending, competesWithPendingTap: competes)
        if isEligible && configuration.holdEnabled { holdDeadline = timestamp + thresholds.holdDelay }
        return actions
    }

    public mutating func touchMoved(id: Int, to point: VideoGesturePoint, timestamp: TimeInterval) -> [VideoGestureAction] {
        var actions = advance(to: timestamp)
        guard var contact = contacts[id] else { return actions }
        contact.current = point
        contacts[id] = contact
        switch contact.phase {
        case .chord:
            if exceedsTapDistance(contact) { actions += suppressAll() }
        case .pending:
            let delta = vector(from: contact.initial, to: point)
            let absX = abs(delta.x), absY = abs(delta.y)
            // A diagonal can exceed the tap/hold drift threshold before either
            // individual axis reaches the pan-lock threshold. It is no longer a
            // stationary hold candidate in that state.
            if exceedsTapDistance(contact) { holdDeadline = nil }
            guard contact.eligible, configuration.advancedActionsAllowed else {
                if exceedsTapDistance(contact) { contacts[id]?.phase = .suppressed }
                return actions
            }
            guard max(absX, absY) >= thresholds.panActivation else { return actions }
            let horizontal = absX >= thresholds.axisDominance * absY || (max(absX, absY) >= thresholds.diagonalFallback && absX >= absY)
            let vertical = absY >= thresholds.axisDominance * absX || (max(absX, absY) >= thresholds.diagonalFallback && absY > absX)
            guard horizontal || vertical else { return actions }
            holdDeadline = nil
            if contact.competesWithPendingTap { pendingTap = nil; burst = nil }
            if horizontal, configuration.horizontalSeekEnabled {
                contact.phase = .horizontal(point); contacts[id] = contact
                actions.append(.beginSurfaceScrub)
            } else if vertical {
                let adjustment: VideoGestureAdjustment = contact.side == .right ? .volume : .brightness
                let enabled = adjustment == .volume ? configuration.volumeEnabled : configuration.brightnessEnabled
                guard enabled else { actions += suppressAll(); return actions }
                contact.phase = .vertical(adjustment, point); contacts[id] = contact
                actions.append(.beginAdjustment(adjustment))
            } else {
                actions += suppressAll()
            }
        case let .vertical(kind, lock):
            actions.append(.updateAdjustment(kind: kind, delta: vector(from: lock, to: point), size: geometry.size))
        case let .horizontal(lock):
            actions.append(.updateSurfaceScrub(delta: vector(from: lock, to: point), size: geometry.size))
        case let .speedHold(activation):
            if distance(from: activation, to: point) > thresholds.holdDrift { actions += suppressAll() }
        case .suppressed:
            break
        }
        return actions
    }

    public mutating func touchEnded(id: Int, at point: VideoGesturePoint, timestamp: TimeInterval) -> [VideoGestureAction] {
        var actions = advance(to: timestamp)
        guard var contact = contacts[id] else { return actions }
        contact.current = point
        contacts[id] = contact
        switch contact.phase {
        case .chord:
            if chordFirstUp == nil { chordFirstUp = timestamp }
            contacts.removeValue(forKey: id)
            if contacts.isEmpty {
                let valid = (chordFirstDown.map { timestamp - $0 <= thresholds.twoFingerDuration } ?? false)
                    && (chordFirstUp.map { timestamp - $0 <= thresholds.twoFingerReleaseSkew } ?? false)
                if valid { actions.append(.togglePlayback) }
                resetChord()
            }
        case let .vertical(kind, _):
            actions.append(.endAdjustment(kind)); contacts.removeValue(forKey: id); holdDeadline = nil
        case let .horizontal(lock):
            actions.append(.commitSurfaceScrub(delta: vector(from: lock, to: point), size: geometry.size)); contacts.removeValue(forKey: id); holdDeadline = nil
        case .speedHold:
            actions.append(.endTemporarySpeedHold); contacts.removeValue(forKey: id); holdDeadline = nil
        case .pending:
            contacts.removeValue(forKey: id); holdDeadline = nil
            if validTap(contact, endedAt: timestamp) {
                actions += resolveTap(contact, endedAt: timestamp)
            } else if contact.competesWithPendingTap {
                pendingTap = nil; burst = nil
            }
        case .suppressed:
            contacts.removeValue(forKey: id); holdDeadline = nil
        }
        return actions
    }

    public mutating func touchCancelled(id: Int, timestamp: TimeInterval) -> [VideoGestureAction] {
        var actions = advance(to: timestamp)
        guard let contact = contacts.removeValue(forKey: id) else { return actions }
        holdDeadline = nil
        switch contact.phase {
        case let .vertical(kind, _): actions.append(.cancelAdjustment(kind))
        case .horizontal: actions.append(.cancelSurfaceScrub)
        case .speedHold: actions.append(.endTemporarySpeedHold)
        case .pending where contact.competesWithPendingTap: pendingTap = nil; burst = nil
        default: break
        }
        if contacts.isEmpty { resetChord() }
        return actions
    }

    public mutating func advance(to timestamp: TimeInterval) -> [VideoGestureAction] {
        var actions: [VideoGestureAction] = []
        if let deadline = holdDeadline, timestamp >= deadline,
           contacts.count == 1, let contact = contacts.values.first,
           contact.phase == .pending, contact.eligible, configuration.advancedActionsAllowed,
           configuration.holdEnabled {
            holdDeadline = nil; pendingTap = nil; burst = nil
            contacts[contact.id]?.phase = .speedHold(contact.current)
            actions.append(.beginTemporarySpeedHold)
        }
        if let pending = pendingTap, !pending.suspended, timestamp >= pending.deadline, contacts.isEmpty {
            pendingTap = nil; actions.append(.toggleControls)
        }
        if let deadline = burst?.deadline, timestamp >= deadline { burst = nil }
        return actions
    }

    /// Explicit lifecycle/modal/route/VoiceOver cancellation. Existing committed
    /// engine seeks are deliberately outside this reducer and are not rolled back.
    public mutating func cancelAll() -> [VideoGestureAction] {
        var actions: [VideoGestureAction] = []
        for contact in contacts.values {
            switch contact.phase {
            case let .vertical(kind, _): actions.append(.cancelAdjustment(kind))
            case .horizontal: actions.append(.cancelSurfaceScrub)
            case .speedHold: actions.append(.endTemporarySpeedHold)
            default: break
            }
        }
        contacts.removeAll(); pendingTap = nil; burst = nil; holdDeadline = nil; resetChord()
        return actions
    }

    private mutating func resolveTap(_ contact: Contact, endedAt: TimeInterval) -> [VideoGestureAction] {
        guard configuration.advancedActionsAllowed else { return [.toggleControls] }
        // With skip disabled there is no competing action, so controls should not
        // pay the double-tap delay.
        guard configuration.doubleTapEnabled else { return [.toggleControls] }
        if let burst, burst.side == contact.side, contact.competesWithPendingTap {
            let count = burst.count + 1
            self.burst = SkipBurst(side: burst.side, count: count, deadline: endedAt + thresholds.burstGap)
            return [.relativeSkip(direction(for: contact.side), count: count)]
        }
        if let pending = pendingTap, contact.competesWithPendingTap {
            self.pendingTap = nil
            if pending.side == contact.side {
                burst = SkipBurst(side: contact.side, count: 1, deadline: endedAt + thresholds.burstGap)
                return [.relativeSkip(direction(for: contact.side), count: 1)]
            }
            // The older normal tap becomes visible now; the opposite one is a new
            // single candidate and must not synthesize a skip.
            pendingTap = PendingTap(side: contact.side, upAt: endedAt, deadline: endedAt + thresholds.initialTapGap)
            return [.toggleControls]
        }
        pendingTap = PendingTap(side: contact.side, upAt: endedAt, deadline: endedAt + thresholds.initialTapGap)
        return []
    }

    private mutating func suppressAll() -> [VideoGestureAction] {
        var actions: [VideoGestureAction] = []
        let phases = contacts.values.map(\.phase)
        for phase in phases {
            switch phase {
            case let .vertical(kind, _): actions.append(.cancelAdjustment(kind))
            case .horizontal: actions.append(.cancelSurfaceScrub)
            case .speedHold: actions.append(.endTemporarySpeedHold)
            default: break
            }
        }
        for id in contacts.keys { contacts[id]?.phase = .suppressed }
        pendingTap = nil; burst = nil; holdDeadline = nil
        return actions
    }

    private mutating func resetChord() { chordFirstDown = nil; chordFirstUp = nil }
    private func direction(for side: Side) -> VideoGestureSkipDirection { side == .left ? .backward : .forward }
    private func vector(from a: VideoGesturePoint, to b: VideoGesturePoint) -> VideoGestureVector { .init(x: b.x - a.x, y: b.y - a.y) }
    private func distance(from a: VideoGesturePoint, to b: VideoGesturePoint) -> Double { hypot(b.x - a.x, b.y - a.y) }
    private func exceedsTapDistance(_ contact: Contact) -> Bool { distance(from: contact.initial, to: contact.current) > thresholds.tapDisplacement }
    private func validTap(_ contact: Contact, endedAt: TimeInterval) -> Bool {
        contact.eligible && endedAt - contact.beganAt <= thresholds.tapDuration && !exceedsTapDistance(contact)
    }
}

/// Pure incremental timeline fine-scrub reducer. The host supplies seconds per
/// point and projects candidates into the current seekable ranges.
public struct VideoFineScrub: Sendable {
    public private(set) var multiplier: Double = 1
    public private(set) var isFineMode = false
    private var lastPoint: VideoGesturePoint?
    private var lastBand: Int = 0

    public init() {}

    public mutating func begin(at point: VideoGesturePoint) { lastPoint = point; multiplier = 1; isFineMode = false; lastBand = 0 }

    /// Returns an incremental horizontal multiplier; callers retain their own
    /// unprojected candidate time so gaps cannot trap the scrub at a boundary.
    public mutating func update(to point: VideoGesturePoint, upwardDistance: Double, containerHeight: Double) -> (deltaX: Double, multiplier: Double)? {
        guard let previous = lastPoint else { return nil }
        let scale = min(1, max(0.35, containerHeight / 360))
        let bands = [40 * scale, 80 * scale, 120 * scale]
        let hysteresis = 8 * scale
        let rawBand = upwardDistance >= bands[2] ? 3 : upwardDistance >= bands[1] ? 2 : upwardDistance >= bands[0] ? 1 : 0
        var band = rawBand
        if rawBand < lastBand {
            // Promotion is immediate; only a reverse crossing gets hysteresis.
            // This avoids trapping a drag in a coarse band while it moves farther
            // from the timeline, while still resisting boundary chatter on return.
            let retainBoundary = bands[lastBand - 1] - hysteresis
            if upwardDistance >= retainBoundary { band = lastBand }
        }
        let changed = band != lastBand
        lastBand = band; multiplier = [1, 0.5, 0.25, 0.1][band]; isFineMode = isFineMode || band > 0
        lastPoint = point
        // Rebase at a band boundary so a change in sensitivity never makes the
        // optimistic target jump. The next movement supplies the first increment.
        return (changed ? 0 : point.x - previous.x, multiplier)
    }

    public mutating func cancel() { lastPoint = nil; multiplier = 1; isFineMode = false; lastBand = 0 }
}
