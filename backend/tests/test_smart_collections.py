"""Smart collections: allowlisted rules evaluated as the viewer."""
from __future__ import annotations

import typing
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.media_schemas import SmartCollectionRule, SmartRuleField
from app.models import LibraryItem, LibraryTag, User
from app.services.smart_collections import (
    FIELDS,
    RuleError,
    describe_rule,
    evaluate,
    parse_rule,
    preview,
    validate_rule,
)
from discovery_support import BASE, add_movie, add_progress, add_series
from support import make_user, memory_session_factory

NOW = BASE + timedelta(days=1)


def rule(type_: str = "movie", *conditions, match: str = "all", sort: dict | None = None, limit: int = 100) -> SmartCollectionRule:
    return SmartCollectionRule.model_validate({
        "type": type_, "match": match, "sort": sort, "limit": limit,
        "conditions": [{"field": field, "op": op, "value": value} for field, op, value in conditions],
    })


def _library(session=None):  # noqa: ANN001
    session = memory_session_factory()() if session is None else session
    session.add_all([make_user("owner"), make_user("member"), make_user("other")])
    add_movie(session, "arrival", "Arrival", year=2016, genres=["Drama", "Science Fiction"], people=["Amy Adams"], rating=7.9,
              official_rating="PG-13", provider_ids={"Imdb": "tt2543164"}, runtime_minutes=116)
    add_movie(session, "paddington", "Paddington", year=2014, genres=["Comedy", "Family"], people=["Ben Whishaw"], rating=7.3,
              official_rating="PG", runtime_minutes=95, days=-10)
    add_movie(session, "home", "Home Video", genres=["Drama", "Documentary"], owner="other", visibility="private")
    add_series(session, "show", "Show", seasons={0: 1, 1: 2}, genres=["Drama"])
    session.add(LibraryItem(id="vid", user_id="owner", visibility="shared", title="Why the sky is blue", uploader="Veritasium",
                            kind="video", duration=900, metadata_json={"upload_date": "20240105"}, status="available"))
    session.add(LibraryTag(id="tag1", item_id="arrival-v", user_id="member", tag="Favorite"))
    session.add(LibraryTag(id="tag2", item_id="paddington-v", user_id="other", tag="favorite"))
    add_progress(session, "member", "paddington-v", completed=True)
    add_progress(session, "member", "show-s1e1-v", completed=True)
    session.commit()
    return session


@pytest.mark.parametrize(
    ("built", "expected"),
    [
        (rule("movie", ("genre", "is", "drama")), {"arrival"}),
        (rule("movie", ("genre", "not_in", ["drama"])), {"paddington"}),
        (rule("movie", ("genre", "in", ["Family", "science fiction"])), {"arrival", "paddington"}),
        (rule("movie", ("people", "is", "AMY ADAMS")), {"arrival"}),
        (rule("movie", ("year", "gte", 2015)), {"arrival"}),
        (rule("movie", ("year", "is_not", 2016)), {"paddington"}),
        (rule("movie", ("rating", "gte", 7.5)), {"arrival"}),
        (rule("movie", ("official_rating", "in", ["pg"])), {"paddington"}),
        (rule("movie", ("provider", "is", "Imdb")), {"arrival"}),
        (rule("movie", ("provider", "is_not", "Imdb")), {"paddington"}),
        (rule("movie", ("watched", "is", "watched")), {"paddington"}),
        (rule("movie", ("watched", "is", "unwatched")), {"arrival"}),
        (rule("movie", ("added", "within_days", 3)), {"arrival"}),
        (rule("movie", ("runtime", "lte", 100)), {"paddington"}),
        (rule("movie", ("tags", "is", "favorite")), {"arrival"}),
        (rule("movie", ("genre", "is", "comedy"), ("year", "gte", 2015), match="any"), {"arrival", "paddington"}),
        (rule("movie", ("genre", "is", "comedy"), ("year", "gte", 2015)), set()),
        (rule("series", ("watched", "is", "in_progress")), {"show"}),
        (rule("series", ("watched", "is", "watched")), set()),
        (rule("episode", ("watched", "is", "watched")), {"show-s1e1"}),
        (rule("channel_video", ("channel", "is", "veritasium")), {"vid"}),
        (rule("channel_video", ("year", "is", 2024), ("runtime", "gte", 15)), {"vid"}),
    ],
)
def test_field_to_sql_table(built: SmartCollectionRule, expected: set[str]) -> None:
    session = _library()

    assert {row.id for row in evaluate(session, session.get(User, "member"), validate_rule(built), now=NOW)} == expected


def test_added_rule_and_sort_use_when_the_files_arrived() -> None:
    """A scan met every title recently; "added" means when its files arrived (MediaTitle.added_at), else created_at."""
    from app.models import MediaTitle

    session = _library()
    session.get(MediaTitle, "arrival").added_at = BASE - timedelta(days=400)  # an old file the first scan just met
    session.get(MediaTitle, "paddington").added_at = BASE  # created ten days before the scan's clock, arrived today
    session.commit()
    member = session.get(User, "member")
    assert {row.id for row in evaluate(session, member, validate_rule(rule("movie", ("added", "within_days", 3))), now=NOW)} == {"paddington"}
    newest = rule("movie", sort={"field": "added", "order": "desc"})
    assert [row.id for row in evaluate(session, member, validate_rule(newest), now=NOW)] == ["paddington", "arrival"]


def test_watching_only_a_special_does_not_start_a_series() -> None:
    session = _library()
    add_progress(session, "other", "show-s0e1-v", completed=True)
    session.commit()

    unwatched = validate_rule(rule("series", ("watched", "is", "unwatched")))
    in_progress = validate_rule(rule("series", ("watched", "is", "in_progress")))
    assert {row.id for row in evaluate(session, session.get(User, "other"), unwatched, now=NOW)} == {"show"}
    assert evaluate(session, session.get(User, "other"), in_progress, now=NOW) == []


def test_a_tag_on_a_special_still_matches_its_series() -> None:
    session = _library()
    session.add(LibraryTag(id="tag3", item_id="show-s0e1-v", user_id="other", tag="Holiday"))
    session.commit()

    tagged = validate_rule(rule("series", ("tags", "is", "holiday")))
    assert {row.id for row in evaluate(session, session.get(User, "other"), tagged, now=NOW)} == {"show"}


def test_rules_are_evaluated_as_the_viewer() -> None:
    session = _library()
    other, member = session.get(User, "other"), session.get(User, "member")

    drama = validate_rule(rule("movie", ("genre", "is", "drama")))
    assert {row.id for row in evaluate(session, other, drama, now=NOW)} == {"arrival", "home"}
    assert {row.id for row in evaluate(session, member, drama, now=NOW)} == {"arrival"}
    unwatched = validate_rule(rule("movie", ("watched", "is", "unwatched")))
    assert {row.id for row in evaluate(session, other, unwatched, now=NOW)} == {"arrival", "paddington", "home"}
    tagged = validate_rule(rule("movie", ("tags", "is", "favorite")))
    assert {row.id for row in evaluate(session, other, tagged, now=NOW)} == {"paddington"}


def test_every_contract_field_has_an_allowlist_entry() -> None:
    assert set(FIELDS) == set(typing.get_args(SmartRuleField))


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "movie", "conditions": [{"field": "path", "op": "is", "value": "/"}]},
        {"type": "movie", "conditions": [{"field": "genre", "op": "gte", "value": "x"}]},
        {"type": "movie", "conditions": [{"field": "channel", "op": "is", "value": "x"}]},
        {"type": "channel_video", "conditions": [{"field": "genre", "op": "is", "value": "x"}]},
        {"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": ["a"]}]},
        {"type": "movie", "conditions": [{"field": "genre", "op": "in", "value": "a"}]},
        {"type": "movie", "conditions": [{"field": "genre", "op": "in", "value": []}]},
        {"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": "x" * 10_000}]},
        {"type": "movie", "conditions": [{"field": "provider", "op": "is", "value": "Netflix"}]},
        {"type": "movie", "conditions": [{"field": "watched", "op": "is", "value": True}]},
        {"type": "movie", "conditions": [{"field": "year", "op": "gte", "value": 99999}]},
        {"type": "movie", "conditions": [{"field": "year", "op": "gte", "value": 2000.5}]},
        {"type": "movie", "conditions": [{"field": "added", "op": "within_days", "value": 0}]},
        {"type": "movie", "conditions": [{"field": "rating", "op": "gte", "value": "high"}]},
        {"type": "channel_video", "sort": {"field": "rating", "order": "desc"}},
        {"type": "movie", "limit": 501},
        {"type": "movie", "titles": ["Arrival"]},
    ],
)
def test_validator_rejects_bad_values(payload: dict) -> None:
    with pytest.raises((ValidationError, RuleError)):
        validate_rule(SmartCollectionRule.model_validate(payload))


def test_parse_rule_is_strict_about_untrusted_json() -> None:
    assert parse_rule({"type": "movie", "conditions": [{"field": "year", "op": "gte", "value": 2000}]}).conditions[0].value == 2000
    for raw in (None, "movie", {"type": "movie", "titles": ["x"]},
                {"type": "movie", "conditions": [{"field": "year", "op": "gte", "value": True}]}):
        with pytest.raises(RuleError):
            parse_rule(raw)


def test_values_are_bound_parameters_never_sql() -> None:
    session = _library()
    hostile = validate_rule(rule("movie", ("genre", "is", "x'); DROP TABLE media_titles; --")))

    assert evaluate(session, session.get(User, "member"), hostile, now=NOW) == []
    assert session.execute(text("SELECT count(*) FROM media_titles")).scalar() > 0


def test_preview_counts_as_the_viewer_and_describe_is_deterministic() -> None:
    session = _library()
    built = validate_rule(rule("movie", ("genre", "in", ["drama", "comedy"]), ("year", "gte", 2000),
                               sort={"field": "year", "order": "desc"}, limit=1))

    result = preview(session, session.get(User, "member"), built, now=NOW)

    assert result.count == 1 and [sample.id for sample in result.sample] == ["arrival"]
    assert describe_rule(built) == "Movies where genre is one of drama, comedy and year is at least 2000, sorted by year (descending), up to 1."


# ---- Collections API ----------------------------------------------------------------------

import json  # noqa: E402

from app.services import smart_collections  # noqa: E402
from support import seed_app_settings  # noqa: E402

SMART = {"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": "drama"}]}


def _users(db_factory) -> dict[str, User]:
    with db_factory() as session:
        _library(session)
        return {user.id: user for user in session.query(User)}


def test_smart_collection_is_evaluated_per_viewer_and_refuses_manual_edits(db_factory, api_client) -> None:
    users = _users(db_factory)
    current = {"user": users["other"]}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")

    created = client.post("/api/collections", json={"name": "Dramas", "visibility": "shared", "rules": SMART})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["rules"]["conditions"][0]["field"] == "genre"
    assert ({title["id"] for title in body["titles"]}, body["item_count"]) == ({"arrival", "home"}, 2)
    collection_id = body["id"]

    current["user"] = users["member"]
    seen = client.get(f"/api/collections/{collection_id}").json()
    assert ({title["id"] for title in seen["titles"]}, seen["item_count"]) == ({"arrival"}, 1)
    assert next(c for c in client.get("/api/collections").json() if c["id"] == collection_id)["item_count"] == 1

    current["user"] = users["other"]
    assert client.post(f"/api/collections/{collection_id}/items/arrival-v").status_code == 409
    assert client.post(f"/api/collections/{collection_id}/remote-items", json={"url": "https://www.youtube.com/watch?v=x"}).status_code == 409
    assert client.delete(f"/api/collections/{collection_id}/entries/any").status_code == 409
    assert client.patch(f"/api/collections/{collection_id}/entries/any", json={"position": 0, "expected_revision": 0}).status_code == 409


def test_put_rules_validates_and_only_converts_an_empty_collection(db_factory, api_client) -> None:
    users = _users(db_factory)
    current = {"user": users["owner"]}
    client = api_client(user=lambda: current["user"], base_url="http://localhost")
    manual = client.post("/api/collections", json={"name": "Manual", "visibility": "shared"}).json()["id"]
    client.post(f"/api/collections/{manual}/items/arrival-v")
    empty = client.post("/api/collections", json={"name": "Empty", "visibility": "shared"}).json()["id"]

    assert client.put(f"/api/collections/{manual}/rules", json=SMART).status_code == 409
    converted = client.put(f"/api/collections/{empty}/rules", json=SMART)
    assert converted.status_code == 200 and converted.json()["rules"]["type"] == "movie"
    assert client.put(f"/api/collections/{empty}/rules", json={"type": "movie", "conditions": [{"field": "path", "op": "is", "value": "/"}]}).status_code == 422
    assert client.put(f"/api/collections/{empty}/rules", json={"type": "movie", "conditions": [{"field": "genre", "op": "gte", "value": "x"}]}).status_code == 422
    current["user"] = users["member"]
    assert client.put(f"/api/collections/{empty}/rules", json=SMART).status_code == 403


def test_preview_route_counts_as_the_caller(db_factory, api_client) -> None:
    users = _users(db_factory)
    client = api_client(user=users["member"], base_url="http://localhost")

    body = client.post("/api/collections/rules/preview", json=SMART).json()

    assert body["count"] == 1 and [sample["id"] for sample in body["sample"]] == ["arrival"]
    assert client.post("/api/collections/rules/preview", json={"type": "movie", "limit": 501}).status_code == 422


def test_draft_route_is_hidden_without_ai(db_factory, api_client) -> None:
    users = _users(db_factory)
    client = api_client(user=users["member"], base_url="http://localhost")

    assert client.post("/api/collections/rules/draft", json={"prompt": "dramas"}).status_code == 404


def test_draft_rejects_titles_bogus_fields_and_ungrounded_values(db_factory, api_client, monkeypatch) -> None:
    users = _users(db_factory)
    with db_factory() as session:
        seed_app_settings(session, ai_base_url="http://127.0.0.1:9/v1", ai_model="fake-model")
    prompts: list[str] = []
    replies = iter([
        json.dumps({"type": "movie", "titles": ["Arrival"]}),
        json.dumps({"type": "movie", "conditions": [{"field": "title", "op": "is", "value": "Arrival"}]}),
        json.dumps({"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": "Westerns"}]}),
        json.dumps({"type": "movie", "conditions": [{"field": "year", "op": "gte", "value": True}]}),
        "I cannot help with that",
        json.dumps({"type": "movie", "conditions": [{"field": "genre", "op": "is", "value": "Drama"}]}),
    ])

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001
        prompts.append(messages[1]["content"])
        return next(replies)

    monkeypatch.setattr(smart_collections, "chat", fake_chat)
    client = api_client(user=users["member"], base_url="http://localhost")

    for _attempt in range(5):
        assert client.post("/api/collections/rules/draft", json={"prompt": "dramas please"}).status_code == 422
    good = client.post("/api/collections/rules/draft", json={"prompt": "dramas please"})
    assert good.status_code == 200
    assert good.json()["preview"] == {"count": 1, "sample": [{"id": "arrival", "name": "Arrival", "poster_url": None}]}
    assert good.json()["description"] == "Movies where genre is Drama, up to 100."
    assert "Documentary" not in prompts[0] and "Home Video" not in prompts[0] and "Drama" in prompts[0]
    assert client.post("/api/collections/rules/draft", json={"prompt": ""}).status_code == 422


def test_rule_routes_reject_a_bool_value_by_reparsing_the_raw_body(db_factory, api_client) -> None:
    """Pydantic's lax ``str | int | float`` union would coerce True -> 1; routes re-parse raw JSON through parse_rule."""
    users = _users(db_factory)
    client = api_client(user=users["owner"], base_url="http://localhost")
    bool_rule = {"type": "movie", "conditions": [{"field": "rating", "op": "gte", "value": True}]}
    empty = client.post("/api/collections", json={"name": "Empty2", "visibility": "shared"}).json()["id"]

    assert client.post("/api/collections", json={"name": "Bad", "visibility": "shared", "rules": bool_rule}).status_code == 422
    assert client.put(f"/api/collections/{empty}/rules", json=bool_rule).status_code == 422
    assert client.post("/api/collections/rules/preview", json=bool_rule).status_code == 422


def test_draft_grounding_is_not_capped_by_the_listed_values(monkeypatch) -> None:  # noqa: ANN001
    """Review 11: past 50 channels / 100 tags the model sees a capped list, but grounding checks every real value."""
    session = _library()
    session.add_all([
        LibraryItem(id=f"c{n}", user_id="owner", visibility="shared", title=f"Clip {n}", uploader=f"Channel {n:02}",
                    kind="video", metadata_json={}, status="available")
        for n in range(60)
    ])
    session.add(LibraryItem(id="song", user_id="owner", visibility="shared", title="Song", uploader="Aaa Music Label",
                            kind="audio", metadata_json={}, status="available"))
    session.add_all([LibraryTag(id=f"t{n}", item_id="vid", user_id="member", tag=f"tag {n:03}") for n in range(110)])
    session.commit()
    member = session.get(User, "member")
    prompts: list[str] = []
    replies = iter([
        json.dumps({"type": "channel_video", "conditions": [{"field": "channel", "op": "is", "value": "Channel 59"}]}),
        json.dumps({"type": "channel_video", "conditions": [{"field": "tags", "op": "is", "value": "tag 109"}]}),
    ])

    def fake_chat(config, messages, *, max_tokens):  # noqa: ANN001
        prompts.append(messages[1]["content"])
        return next(replies)

    monkeypatch.setattr(smart_collections, "chat", fake_chat)
    assert smart_collections.draft_rule(session, member, "channel 59", object()).rule.conditions[0].value == "Channel 59"
    assert smart_collections.draft_rule(session, member, "tag 109", object()).rule.conditions[0].value == "tag 109"
    listed = json.loads(prompts[0].split("<values>\n")[1].split("\n</values>")[0])
    assert len(listed["channel"]) == 50 and "Channel 59" not in listed["channel"] and len(listed["tags"]) == 100
    assert "Aaa Music Label" not in listed["channel"]  # only channel kinds are channels
