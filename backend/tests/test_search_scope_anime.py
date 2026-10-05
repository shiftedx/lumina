"""Search scope anime: a wall's "Find the one where…" keeps its own category's titles and
moments; Movies and Shows never return anime."""
from __future__ import annotations

import app.main as main
from app.models import LibraryItem, MediaTitle, User
from app.services.semantic_discovery import DiscoveryMatch, DiscoveryResult
from discovery_support import add_movie, add_series
from support import make_user

TITLES = ("ferry", "harbor", "reel", "kaiju")
MOMENTS = ("ferry-v", "harbor-s1e1-v", "reel-v", "kaiju-s1e1-v")


def _match(kind: str, record_id: str, **fields) -> DiscoveryMatch:
    return DiscoveryMatch(kind=kind, record_id=record_id, title="t", subtitle="", score=1.0, lexical_score=1.0,
                          semantic_score=0.0, match_mode="lexical", **fields)


def test_each_scope_keeps_its_own_categorys_titles_and_moments(db_factory, api_client, monkeypatch) -> None:  # noqa: ANN001
    with db_factory() as session:
        session.add_all([make_user("owner"), make_user("member")])
        add_movie(session, "ferry", "Night Ferry")
        add_series(session, "harbor", "Harbor", seasons={1: 1})
        add_movie(session, "reel", "Anime Reel").category = "anime"
        add_series(session, "kaiju", "Kaiju", seasons={1: 1})
        session.flush()
        for title_id in ("kaiju", "kaiju-s1", "kaiju-s1e1"):
            session.get(MediaTitle, title_id).category = "anime"
        session.commit()
        member = session.get(User, "member")
    seen: list[object] = []

    def fake_search(db, _member, q, *, limit=12, types=None):  # noqa: ANN001, ANN202
        seen.append(types)
        titles = [db.get(MediaTitle, title_id) for title_id in TITLES]
        matches = [_match("title", title.id, media_title=title) for title in titles if types is None or title.type in types]
        matches += [_match("moment", f"{item}@5000", library_item=db.get(LibraryItem, item), start_ms=5_000) for item in MOMENTS]
        return DiscoveryResult(query=q, mode="lexical", matches=tuple(matches), index_generation=0)

    monkeypatch.setattr(main.local_discovery, "search", fake_search)
    client = api_client(user=member, base_url="http://localhost")

    def hits(scope: str) -> list[tuple[str, str | None]]:
        body = client.get("/api/search", params={"q": "lamp", "scope": scope}).json()
        return [(match["kind"], match["title_id"]) for match in body["matches"]]

    assert hits("anime") == [("title", "reel"), ("title", "kaiju"), ("moment", "reel"), ("moment", "kaiju-s1e1")]
    assert hits("movies") == [("title", "ferry"), ("moment", "ferry")]
    assert hits("shows") == [("title", "harbor"), ("moment", "harbor-s1e1")]
    assert seen == [("movie", "series", "episode", "moment"), ("movie", "moment"), ("series", "episode", "moment")]
