from __future__ import annotations


from app.models import DownloadJob, SourceAutomation
from app.services.channel_discovery import normalize_channel_source_url
from app.services.member_follows import FollowRequest, MemberFollowService
from support import make_user, memory_session_factory


def _session():
    return memory_session_factory()()


def _service(db):
    # Hermetic: never resolve DNS for a channel host in a unit test.
    return MemberFollowService(db, validate_url=lambda url: url)


def _follow(display_name: str, source_url: str) -> FollowRequest:
    return FollowRequest(source_url=source_url, display_name=display_name)


def test_follow_creates_active_channel_automation_with_acquisition_disabled() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()

    outcomes = _service(db).follow_channels(
        member, [_follow("Veritasium", "https://www.youtube.com/@veritasium")]
    )
    db.flush()

    assert [outcome.status for outcome in outcomes] == ["created"]
    automations = db.query(SourceAutomation).all()
    assert len(automations) == 1
    automation = automations[0]
    assert automation.user_id == "member"
    assert automation.source_type == "channel"
    assert automation.active is True
    assert automation.auto_download is False  # binding invariant: no acquisition
    assert automation.label == "Veritasium"
    assert automation.source_url == normalize_channel_source_url("https://www.youtube.com/@veritasium")
    assert automation.next_check_at is None  # due now: the first feed read is not deferred a cron period
    # No media acquisition as an onboarding side effect.
    assert db.query(DownloadJob).count() == 0


def test_follow_is_idempotent_across_retries_and_host_variants() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()
    service = _service(db)

    service.follow_channels(member, [_follow("Veritasium", "https://www.youtube.com/@veritasium")])
    db.flush()
    # A completion retry or reload replays the same follow, including a host
    # variant and trailing slash: still exactly one automation.
    replay = service.follow_channels(
        member,
        [
            _follow("Veritasium", "https://m.youtube.com/@veritasium/"),
            _follow("Veritasium", "https://www.youtube.com/@veritasium"),
        ],
    )
    db.flush()

    assert {outcome.status for outcome in replay} == {"existing"}
    assert db.query(SourceAutomation).count() == 1


def test_follow_dedupes_within_a_single_batch() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()

    _service(db).follow_channels(
        member,
        [
            _follow("Veritasium", "https://www.youtube.com/@veritasium"),
            _follow("Veritasium", "https://youtube.com/@veritasium/"),
        ],
    )
    db.flush()
    assert db.query(SourceAutomation).count() == 1


def test_invalid_channel_address_is_reported_and_creates_no_automation() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()

    outcomes = _service(db).follow_channels(member, [_follow("Broken", "not a url")])
    db.flush()
    assert [outcome.status for outcome in outcomes] == ["invalid"]
    assert db.query(SourceAutomation).count() == 0


def test_followed_channel_keys_are_casefolded_display_names_for_the_home_seam() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()
    service = _service(db)

    service.follow_channels(
        member,
        [
            _follow("Veritasium", "https://www.youtube.com/@veritasium"),
            _follow("Kurzgesagt", "https://www.youtube.com/@kurzgesagt"),
        ],
    )
    db.flush()
    assert service.followed_channel_keys(member) == frozenset({"veritasium", "kurzgesagt"})
    assert service.existing_follow_identities(member) == {
        normalize_channel_source_url("https://www.youtube.com/@veritasium"),
        normalize_channel_source_url("https://www.youtube.com/@kurzgesagt"),
    }


def test_follows_and_seam_keys_are_isolated_per_member() -> None:
    db = _session()
    first, second = make_user("first"), make_user("second")
    db.add_all([first, second])
    db.flush()
    service = _service(db)

    service.follow_channels(first, [_follow("Veritasium", "https://www.youtube.com/@veritasium")])
    db.flush()

    assert service.followed_channel_keys(second) == frozenset()
    assert service.existing_follow_identities(second) == set()
    # The second member can still follow the same channel independently.
    outcomes = service.follow_channels(second, [_follow("Veritasium", "https://www.youtube.com/@veritasium")])
    db.flush()
    assert [outcome.status for outcome in outcomes] == ["created"]
    assert db.query(SourceAutomation).filter(SourceAutomation.user_id == "second").count() == 1


def test_existing_manual_channel_follow_blocks_a_duplicate_onboarding_follow() -> None:
    db = _session()
    member = make_user("member")
    db.add(member)
    db.flush()
    # A follow created elsewhere (for example the watch page) is recognized so
    # onboarding never creates a second automation for the same channel.
    db.add(
        SourceAutomation(
            id="manual-1", user_id="member", label="Veritasium",
            source_url="https://www.youtube.com/@veritasium", source_type="channel",
            cron_expression="*/30 * * * *", active=True, auto_download=True,
        )
    )
    db.flush()

    outcomes = _service(db).follow_channels(member, [_follow("Veritasium", "https://www.youtube.com/@veritasium")])
    db.flush()
    assert [outcome.status for outcome in outcomes] == ["existing"]
    assert db.query(SourceAutomation).count() == 1
