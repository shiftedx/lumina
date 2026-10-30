import XCTest
@testable import LuminaCore

final class EpisodeAutoplaySessionTests: XCTestCase {
    func testThreeEpisodeBatchesRequireConfirmationAndExpireAfterFiveMinutes() {
        var session = EpisodeAutoplaySession()
        let start = Date(timeIntervalSince1970: 1000)
        for _ in 0..<2 {
            XCTAssertEqual(session.episodeEnded(hasNextEpisode: true, now: start), .playNext)
            session.didStartNextEpisode(automatically: true)
        }
        XCTAssertEqual(session.episodesStarted, 3)
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: true, now: start), .confirmPresence)
        XCTAssertEqual(session.confirmationDeadline, start.addingTimeInterval(300))
        // Repeated completion or a view rebuild must not extend the deadline.
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: true, now: start.addingTimeInterval(60)), .confirmPresence)
        XCTAssertFalse(session.confirmationExpired(now: start.addingTimeInterval(299)))
        var unattended = session
        XCTAssertTrue(unattended.confirmationExpired(now: start.addingTimeInterval(300)))
        XCTAssertFalse(unattended.confirm(now: start.addingTimeInterval(301)))
        XCTAssertTrue(session.confirm(now: start.addingTimeInterval(100)))
        session.didStartNextEpisode(automatically: true)
        XCTAssertEqual(session.episodesStarted, 1)
        XCTAssertNil(session.confirmationDeadline)
        for _ in 0..<2 {
            XCTAssertEqual(session.episodeEnded(hasNextEpisode: true), .playNext)
            session.didStartNextEpisode(automatically: true)
        }
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: true), .confirmPresence)
        session.didStartNextEpisode(automatically: false)
        XCTAssertEqual(session.episodesStarted, 1)
        XCTAssertNil(session.confirmationDeadline)
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: false), .stop)
    }

    func testServerCanExtendOrDisablePresenceChecksWithoutDisablingAutoplay() {
        let now = Date(timeIntervalSince1970: 2000)
        var session = EpisodeAutoplaySession(policy: .init(episodeLimit: 5, timeoutSeconds: 900))
        for _ in 0..<4 {
            XCTAssertEqual(session.episodeEnded(hasNextEpisode: true, now: now), .playNext)
            session.didStartNextEpisode(automatically: true)
        }
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: true, now: now), .confirmPresence)
        XCTAssertEqual(session.confirmationDeadline, now.addingTimeInterval(900))
        session.updatePolicy(.init(episodeLimit: 5, timeoutSeconds: 1200), now: now.addingTimeInterval(100))
        XCTAssertEqual(session.confirmationDeadline, now.addingTimeInterval(1300))
        session.updatePolicy(.init(enabled: false, episodeLimit: 5, timeoutSeconds: 1200))
        XCTAssertNil(session.confirmationDeadline)
        for _ in 0..<110 {
            XCTAssertEqual(session.episodeEnded(hasNextEpisode: true), .playNext)
            session.didStartNextEpisode(automatically: true)
        }
        XCTAssertNil(session.confirmationDeadline)
        XCTAssertEqual(session.episodesStarted, 100)
        XCTAssertEqual(session.episodeEnded(hasNextEpisode: false), .stop)
    }

}
