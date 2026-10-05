"""Search scope: "Find the one where…" on a wall searches only that wall's kinds and moments."""
from __future__ import annotations

import app.main as main
from app.models import LibraryItem, MediaTitle, User
from app.services.semantic_discovery import DiscoveryMatch, DiscoveryResult
from discovery_support import add_movie, add_series
from support import make_user


def _match(kind: str, record_id: str, **fields) -> DiscoveryMatch:
    return DiscoveryMatch(kind=kind, record_id=record_id, title="t", subtitle="", score=1.0, lexical_score=1.0,
                          semantic_score=0.0, match_mode="lexical", **fields)


def test_scope_limits_kinds_and_moments_to_the_wall(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        add_movie(session, "ferry", "Night Ferry")
        add_series(session, "harbor", "Harbor", seasons={1: 1})
        session.commit()
        member = session.get(User, "member")
    seen: list[object] = []

    def fake_search(db, _member, q, *, limit=12, types=None):  # noqa: ANN001, ANN202
        seen.append(types)
        titles = [db.get(MediaTitle, title_id) for title_id in ("ferry", "harbor")]
        matches = [_match("title", title.id, media_title=title) for title in titles if types is None or title.type in types]
        matches += [
            _match("moment", "ferry-v@761000", library_item=db.get(LibraryItem, "ferry-v"), start_ms=761_000),
            _match("moment", "harbor-s1e1-v@5000", library_item=db.get(LibraryItem, "harbor-s1e1-v"), start_ms=5_000),
        ]
        return DiscoveryResult(query=q, mode="lexical", matches=tuple(matches), index_generation=0)

    monkeypatch.setattr(main.local_discovery, "search", fake_search)
    client = api_client(user=member, base_url="http://localhost")

    def hits(**params) -> list[tuple[str, str | None]]:
        body = client.get("/api/search", params={"q": "lamp", **params}).json()
        return [(match["kind"], match["title_id"]) for match in body["matches"]]

    assert hits(scope="movies") == [("title", "ferry"), ("moment", "ferry")]
    assert hits(scope="shows") == [("title", "harbor"), ("moment", "harbor-s1e1")]
    assert hits() == [("title", "ferry"), ("title", "harbor"), ("moment", "ferry"), ("moment", "harbor-s1e1")]
    assert seen == [("movie", "moment"), ("series", "episode", "moment"), None]
    assert client.get("/api/search", params={"q": "lamp", "scope": "music"}).status_code == 422


def test_scope_through_the_real_search() -> None:
    """The route with the real local_discovery.search: its ``types`` keeps titles and moments of the wall's kinds."""
    from app.services.library_search import ensure_search_index
    from app.services.transcripts import TranscriptService
    from support import memory_session_factory

    with memory_session_factory()() as session:
        session.add_all([make_user("owner"), make_user("member")])
        add_movie(session, "ferry", "Lantern Ferry")
        add_series(session, "harbor", "Lantern Harbor", seasons={1: 1})
        session.commit()
        ensure_search_index(session.get_bind())
        transcripts = TranscriptService(session)
        transcripts.store("ferry-v", language="en", source_kind="source_caption", cues=[(761_000, 761_900, "a lantern on the ferry deck")])
        transcripts.store("harbor-s1e1-v", language="en", source_kind="source_caption", cues=[(5_000, 5_900, "the lantern at the harbor")])
        session.commit()
        member = session.get(User, "member")

        def hits(scope: str | None) -> set[tuple[str, str | None]]:
            matches = main.search_library(q="lantern", limit=12, scope=scope, current_user=member, db=session).matches
            return {(match.kind, match.title_id) for match in matches}

        assert hits("movies") == {("title", "ferry"), ("moment", "ferry")}
        assert hits("shows") == {("title", "harbor"), ("title", "harbor-s1e1"), ("moment", "harbor-s1e1")}  # episodes too; never the season
        assert hits(None) == {("title", "ferry"), ("title", "harbor"), ("title", "harbor-s1"), ("title", "harbor-s1e1"), ("moment", "ferry"), ("moment", "harbor-s1e1")}
