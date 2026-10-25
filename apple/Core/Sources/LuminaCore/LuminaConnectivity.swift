import Foundation
import Network
import Observation

/// A small, event-driven view of whether the device currently has a usable route.
///
/// This deliberately describes reachability rather than "airplane mode": a device can
/// be in airplane mode with Wi-Fi re-enabled, or have a route which cannot reach a
/// household server. `LuminaStore` combines this signal with real request failures.
@MainActor @Observable public final class LuminaConnectivity {
    public enum Status: Equatable, Sendable {
        case unknown
        case online
        case offline
    }

    public private(set) var status: Status
    @ObservationIgnored private var monitor: NWPathMonitor?
    @ObservationIgnored private let queue = DispatchQueue(label: "ai.cozylabs.lumina.connectivity")
    @ObservationIgnored private let monitoringEnabled: Bool
    @ObservationIgnored var onChange: (@MainActor (Status) -> Void)?

    /// Set `monitoringEnabled` false only for deterministic previews and UI tests.
    public init(status: Status = .unknown, monitoringEnabled: Bool = true) {
        self.status = status
        self.monitoringEnabled = monitoringEnabled
    }

    deinit { monitor?.cancel() }

    /// Safe to call repeatedly. Tests can leave monitoring stopped and set a status directly.
    public func start() {
        guard monitoringEnabled, monitor == nil else { return }
        let monitor = NWPathMonitor()
        self.monitor = monitor
        monitor.pathUpdateHandler = { [weak self] path in
            let next: Status = path.status == .satisfied ? .online : .offline
            Task { @MainActor [weak self] in self?.setStatus(next) }
        }
        monitor.start(queue: queue)
    }

    func setStatus(_ next: Status) {
        guard status != next else { return }
        status = next
        onChange?(next)
    }
}
