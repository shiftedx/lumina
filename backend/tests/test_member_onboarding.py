from __future__ import annotations

from app.models import User
from app.schemas import UserCreateRequest
from app.services.member_onboarding import MemberOnboardingService
from app.services.member_recommendations import MemberInterestService
from app.services.users import UserService
from support import make_user as _member, memory_session_factory


def _session():
    return memory_session_factory()()


def test_every_creation_path_begins_a_member_pending() -> None:
    db = _session()
    users = UserService(db)
    admin = users.create_initial_admin("owner", "Test-only passphrase 42", "Owner")
    member = users.create_user(UserCreateRequest(username="member", password="Test-only passphrase 42", role="viewer"))
    db.flush()

    assert admin.onboarding_status == MemberOnboardingService.PENDING
    assert member.onboarding_status == MemberOnboardingService.PENDING


def test_complete_saves_interests_marks_completed_idempotent_and_member_scoped() -> None:
    db = _session()
    first, second = _member("first"), _member("second")
    db.add_all([first, second])
    db.flush()
    service = MemberOnboardingService(db)

    status, selected = service.complete(first, ["music", "cooking", "music"])
    assert status == MemberOnboardingService.COMPLETED
    assert selected == ("music", "cooking")
    assert first.onboarding_status == MemberOnboardingService.COMPLETED

    # Idempotent: replaying the same decision yields the same durable state.
    assert service.complete(first, ["music", "cooking"]) == (MemberOnboardingService.COMPLETED, ("music", "cooking"))
    assert MemberInterestService(db).list_for(first) == ("music", "cooking")

    # Member-scoped: the second member is untouched and still pending.
    assert second.onboarding_status == MemberOnboardingService.PENDING
    assert MemberInterestService(db).list_for(second) == ()


def test_skip_marks_skipped_is_idempotent_and_never_regresses_a_completion() -> None:
    db = _session()
    member, other = _member("member"), _member("other")
    db.add_all([member, other])
    db.flush()
    service = MemberOnboardingService(db)

    assert service.skip(member) == MemberOnboardingService.SKIPPED
    assert service.skip(member) == MemberOnboardingService.SKIPPED

    service.complete(other, ["music"])
    # A stale second session choosing skip must not erase a real completion.
    assert service.skip(other) == MemberOnboardingService.COMPLETED
    assert MemberInterestService(db).list_for(other) == ("music",)


def test_onboarding_endpoints_reject_unknown_keys_and_are_durable_idempotent(db_factory, api_client) -> None:
    factory = db_factory
    member = _member("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    rejected = client.post("/api/onboarding/complete", json={"keys": ["music", "not-a-category"]})
    assert rejected.status_code == 422
    with factory() as session:
        assert session.get(User, "member").onboarding_status == MemberOnboardingService.PENDING

    first = client.post("/api/onboarding/complete", json={"keys": ["music"]})
    assert first.status_code == 200
    assert first.json() == {"status": "completed", "selected_keys": ["music"], "followed": []}

    # Replaying completion (a second tab or device) yields the same state.
    again = client.post("/api/onboarding/complete", json={"keys": ["music"]})
    assert again.json() == {"status": "completed", "selected_keys": ["music"], "followed": []}

    # A late skip from another session does not regress the durable completion.
    skipped = client.post("/api/onboarding/skip")
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "completed"

    with factory() as session:
        reloaded = session.get(User, "member")
        assert reloaded.onboarding_status == MemberOnboardingService.COMPLETED
        assert MemberInterestService(session).list_for(reloaded) == ("music",)


def test_skip_endpoint_takes_a_pending_member_out_of_onboarding(db_factory, api_client) -> None:
    factory = db_factory
    member = _member("member")
    with factory.begin() as session:
        session.add(member)

    client = api_client(user=member, base_url="http://localhost")
    response = client.post("/api/onboarding/skip")
    assert response.status_code == 200
    assert response.json() == {"status": "skipped", "selected_keys": [], "followed": []}
    with factory() as session:
        assert session.get(User, "member").onboarding_status == MemberOnboardingService.SKIPPED
