"""One invited member cannot keep the shared AI/ASR workers busy for the household: a per-member hourly job budget."""
from __future__ import annotations

from app.services.rate_limit import RATE_LIMIT_RULES
from support import make_user

ITEM = "/api/library/00000000-0000-0000-0000-000000000000"
JOB_ROUTES = [
    (ITEM + "/summaries", {}),
    (ITEM + "/idea-graphs", None),
    (ITEM + "/subtitle-tracks/generate", None),
    (ITEM + "/subtitle-tracks/t/sync", None),
    (ITEM + "/subtitle-tracks/t/translate", {"language": "fr"}),
    (ITEM + "/segments/detect", None),
    (ITEM + "/transcripts/asr", None),
]


def test_member_job_starts_share_one_per_member_budget(db_factory, api_client) -> None:  # noqa: ANN001
    alice, bob = make_user("user-a", username="alice"), make_user("user-b", username="bob")
    with db_factory.begin() as session:
        session.add_all([alice, bob])
    budget = RATE_LIMIT_RULES["member_job"].max_requests
    client = api_client(user=alice, base_url="http://localhost")
    for index in range(budget):
        path, body = JOB_ROUTES[index % len(JOB_ROUTES)]
        assert client.post(path, json=body).status_code != 429
    for path, body in JOB_ROUTES:
        assert client.post(path, json=body).status_code == 429, path
    # Per member, not per address: another member on the same address is unaffected.
    other = api_client(user=bob, base_url="http://localhost")
    path, body = JOB_ROUTES[0]
    assert other.post(path, json=body).status_code != 429


def test_vault_owner_job_starts_are_not_metered(db_factory, api_client) -> None:  # noqa: ANN001
    owner = make_user("user-o", username="owner", role="admin")
    with db_factory.begin() as session:
        session.add(owner)
    client = api_client(user=owner, base_url="http://localhost")
    path, body = JOB_ROUTES[0]
    for _ in range(RATE_LIMIT_RULES["member_job"].max_requests + 1):
        assert client.post(path, json=body).status_code != 429
