"""Library gallery T3: album and artist titles never reach video surfaces or the title search index."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.models import MediaTitle, MemberFavorite, PlaybackProgress, User, utcnow
from app.services import embeddings, library_search
from app.services.library_search import TITLE_FTS_MAP_TABLE, ensure_search_index, index_title
from app.services.member_recommendations import MemberRecommendationPolicy
from app.services.playlists import PlaylistBadRequest, _resolve_items
from support import make_user
from title_support import ALBUM, ALICE, ARTIST, BOB, MOVIE, TRACKS, seed_gallery, seed_tree


@pytest.fixture
def gallery(db_factory, tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path.resolve() / "media"
    with db_factory() as session:
        session.add_all([make_user(ALICE, username="alice"), make_user(BOB, username="bob")])
        seed_tree(session, root)
        seed_gallery(session, root)
        session.commit()
    return db_factory


def test_music_titles_stay_out_of_the_title_index_and_never_force_a_rebuild(gallery, monkeypatch) -> None:  # noqa: ANN001
    engine = gallery.kw["bind"]
    ensure_search_index(engine)  # the seeded rows were never indexed: this rebuilds
    with gallery() as session:
        indexed = set(session.execute(text(f"SELECT title_id FROM {TITLE_FTS_MAP_TABLE}")).scalars())
        assert MOVIE in indexed and not indexed & {ALBUM, ARTIST}
        index_title(session, session.get(MediaTitle, ALBUM))
        session.commit()
        assert session.execute(text(f"SELECT count(*) FROM {TITLE_FTS_MAP_TABLE} WHERE title_id = :id"), {"id": ALBUM}).scalar() == 0
    monkeypatch.setattr(library_search, "_rebuild", lambda _connection: pytest.fail("rebuilt an index that was in sync"))
    ensure_search_index(engine)


def test_the_embedding_backfill_never_picks_music_titles(gallery) -> None:  # noqa: ANN001
    with gallery() as session:
        documents = embeddings._documents(session, "model-1", 100)  # noqa: SLF001
    titles = {target for target, kind, _text in documents if kind == "title"}
    assert MOVIE in titles and not titles & {ALBUM, ARTIST}


def test_vectors_embedded_for_music_titles_are_never_served(gallery) -> None:  # noqa: ANN001
    from app.models import SearchEmbedding

    blob = embeddings.unit([1.0, 0.0]).tobytes()
    with gallery() as session:
        session.add_all([SearchEmbedding(target_id=t, model_id="m-music", kind="title", signature="s", vector=blob)
                         for t in (MOVIE, ALBUM, ARTIST)])
        session.commit()
        assert set(embeddings.vector_map(session, "m-music")) == {MOVIE}


def test_a_played_or_favourite_album_is_never_a_watched_title(gallery) -> None:  # noqa: ANN001
    with gallery() as session:
        session.add_all([
            PlaybackProgress(id="track-done", user_id=ALICE, item_id=TRACKS[0], position_seconds=0, duration_seconds=180,
                             completed=True, last_watched_at=utcnow()),
            MemberFavorite(user_id=ALICE, target_id=ALBUM),
        ])
        session.commit()
        alice = session.get(User, ALICE)
        policy = MemberRecommendationPolicy(session)
        assert policy.recently_completed(alice, since=utcnow() - timedelta(days=1)) == []
        assert policy._title_history(alice) == {}  # noqa: SLF001


def test_an_album_cannot_be_added_to_a_playlist(gallery) -> None:  # noqa: ANN001
    with gallery() as session:
        with pytest.raises(PlaylistBadRequest):
            _resolve_items(session, session.get(User, ALICE), [ALBUM])
