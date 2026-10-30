import Foundation

/// Server-owned limits, separate from the member's choice to turn autoplay off.
public struct EpisodeAutoplayPolicy: Equatable, Sendable {
    public let enabled: Bool
    public let episodeLimit: Int
    public let timeoutSeconds: Int

    public init(enabled: Bool = true, episodeLimit: Int = 3, timeoutSeconds: Int = 300) {
        self.enabled = enabled
        self.episodeLimit = (1...100).contains(episodeLimit) ? episodeLimit : 3
        self.timeoutSeconds = (60...86_400).contains(timeoutSeconds) ? timeoutSeconds : 300
    }
}

/// One explicitly started episode plus its automatic successors. The pending
/// deadline belongs to the session so rebuilding a player view cannot extend it.
public struct EpisodeAutoplaySession: Equatable, Sendable {
    public enum Completion: Equatable, Sendable { case playNext, confirmPresence, stop }
    public private(set) var policy: EpisodeAutoplayPolicy
    public private(set) var episodesStarted = 1
    public private(set) var confirmationDeadline: Date?

    public init(policy: EpisodeAutoplayPolicy = .init()) { self.policy = policy }

    public mutating func updatePolicy(_ policy: EpisodeAutoplayPolicy, now: Date = .now) {
        guard policy != self.policy else { return }
        self.policy = policy
        if confirmationDeadline != nil {
            confirmationDeadline = policy.enabled && episodesStarted >= policy.episodeLimit
                ? now.addingTimeInterval(Double(policy.timeoutSeconds)) : nil
        }
    }

    public mutating func episodeEnded(hasNextEpisode: Bool, now: Date = .now) -> Completion {
        guard hasNextEpisode else { return .stop }
        guard policy.enabled, episodesStarted >= policy.episodeLimit else { return .playNext }
        if confirmationDeadline == nil { confirmationDeadline = now.addingTimeInterval(Double(policy.timeoutSeconds)) }
        return .confirmPresence
    }

    public mutating func didStartNextEpisode(automatically: Bool) {
        episodesStarted = automatically ? min(100, episodesStarted + 1) : 1
        confirmationDeadline = nil
    }

    public mutating func confirm(now: Date = .now) -> Bool {
        guard let confirmationDeadline, now < confirmationDeadline else { return false }
        self.confirmationDeadline = nil
        episodesStarted = 0
        return true
    }

    public func confirmationExpired(now: Date = .now) -> Bool {
        confirmationDeadline.map { now >= $0 } ?? false
    }
}
