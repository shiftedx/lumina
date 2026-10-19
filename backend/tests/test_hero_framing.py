"""The requested-artwork hero framing queue stays bounded and never analyses in routes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers import hero_framing as routes
from app.services import hero_framing
from app.services import titles
from app.services.titles import image_url


def ready() -> hero_framing.Result:
    return hero_framing.Result(
        "ready", width=1920, height=1080, digest="a" * 64,
        regions=({"x": 0.25, "y": 0.2, "width": 0.2, "height": 0.3, "kind": "face", "weight": 1.0},),
    )


def test_queue_deduplicates_caps_and_reuses_a_ready_cache_after_restart(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: ready())
    worker = hero_framing.HeroFraming()
    calls: list[bytes] = []
    loader = lambda: (calls.append(b"image") or b"image", "image/jpeg")
    assert worker.request("a" * 64, loader).state == "pending"
    assert worker.request("a" * 64, loader).state == "pending"
    assert len(worker._pending) == 1  # one source, one pending child
    for index in range(hero_framing.MAX_PENDING - 1):
        assert worker.request(f"{index:064x}", loader).state == "pending"
    assert worker.request("f" * 64, loader).state == "unavailable"
    assert worker.run_once() is True and calls == [b"image"]
    assert worker.request("a" * 64, loader).body()["state"] == "ready"
    restarted = hero_framing.HeroFraming()
    assert restarted.request("a" * 64, loader).body()["state"] == "ready"
    assert restarted.request("b" * 64, loader).state == "pending"


def test_failed_result_is_cached_for_an_hour_and_bad_analyzer_payload_is_unavailable(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: hero_framing.Result("unavailable", expires_at=9_999_999_999))
    worker = hero_framing.HeroFraming()
    assert worker.request("b" * 64, lambda: (b"image", "image/jpeg")).state == "pending"
    assert worker.run_once() is True
    assert worker.request("b" * 64, lambda: (_ for _ in ()).throw(AssertionError("cached failure"))).state == "unavailable"
    assert hero_framing._validate({"version": 1, "state": "ready", "width": 100, "height": 100, "digest": "a" * 64,
                                   "regions": [{"x": 0.9, "y": 0, "width": 0.2, "height": 1, "kind": "face", "weight": 1}]}) is None


def test_inflight_deduplicates_and_expired_memory_failure_requeues(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    worker = hero_framing.HeroFraming()
    worker._running.add("a" * 64)
    assert worker.request("a" * 64, lambda: (b"image", "image/jpeg")).state == "pending"
    assert "a" * 64 not in worker._pending
    worker._cache["b" * 64] = hero_framing.Result("unavailable", expires_at=0)
    assert worker.request("b" * 64, lambda: (b"image", "image/jpeg")).state == "pending"


def test_warm_queue_is_bounded_promoted_and_yields_to_interactive_work(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing.background_work, "busy", lambda: False)
    monkeypatch.setattr(hero_framing.sessions, "video_encodes", lambda: 0)
    order: list[str] = []
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: ready())
    worker = hero_framing.HeroFraming()
    warm_key, interactive_key = "a" * 64, "b" * 64
    worker.warm(warm_key, lambda: (order.append("warm") or b"warm", "image/jpeg"))
    worker.request(interactive_key, lambda: (order.append("interactive") or b"interactive", "image/jpeg"))
    assert worker.run_once() is True and order == ["interactive"]
    worker.request(warm_key, lambda: (order.append("promoted") or b"promoted", "image/jpeg"))
    assert warm_key in worker._pending and warm_key not in worker._warm
    for index in range(hero_framing.MAX_WARM_PENDING + 2):
        worker.warm(f"{index:064x}", lambda: (b"image", "image/jpeg"))
    assert len(worker._warm) == hero_framing.MAX_WARM_PENDING


def test_interactive_promotion_bypasses_the_quiet_warm_gate_and_high_queue_stays_capped(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing.sessions, "video_encodes", lambda: 0)
    busy = {"value": True}
    monkeypatch.setattr(hero_framing.background_work, "busy", lambda: busy["value"])
    worker = hero_framing.HeroFraming()
    key, loaded = "d" * 64, []
    worker.warm(key, lambda: (_ for _ in ()).throw(AssertionError("warm loader must stay gated")))
    assert worker.run_once() is False
    assert key in worker._warm and key not in worker._running
    assert worker.request(key, lambda: (loaded.append("interactive") or b"image", "image/jpeg")).state == "pending"
    assert key in worker._pending and key not in worker._warm
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: ready())
    assert worker.run_once() is True and loaded == ["interactive"]

    for index in range(hero_framing.MAX_PENDING):
        assert worker.request(f"{index:064x}", lambda: (b"image", "image/jpeg")).state == "pending"
    warm_key = "e" * 64
    worker.warm(warm_key, lambda: (b"image", "image/jpeg"))
    assert worker.request(warm_key, lambda: (b"image", "image/jpeg")).state == "unavailable"
    assert len(worker._pending) == hero_framing.MAX_PENDING
    assert warm_key in worker._warm


def test_interactive_retry_is_discarded_when_its_running_warm_load_succeeds(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing.background_work, "busy", lambda: False)
    monkeypatch.setattr(hero_framing.sessions, "video_encodes", lambda: 0)
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: ready())
    worker = hero_framing.HeroFraming()
    key = "f" * 64

    def cache_only_loader() -> tuple[bytes, str]:
        assert worker.request(key, lambda: (b"interactive", "image/jpeg")).state == "pending"
        return b"warm", "image/jpeg"

    worker.warm(key, cache_only_loader)
    assert worker.run_once() is True
    assert key not in worker._interactive_retries


def test_warm_waits_for_quiet_and_a_cache_only_miss_does_not_poison_cache(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing.sessions, "video_encodes", lambda: 0)
    monkeypatch.setattr(hero_framing, "POLL_SECONDS", .01)
    busy = {"value": True}
    monkeypatch.setattr(hero_framing.background_work, "busy", lambda: busy["value"])
    worker = hero_framing.HeroFraming()
    key, calls = "c" * 64, []
    worker.warm(key, lambda: (calls.append("load") and (b"image", "image/jpeg")))
    assert worker.run_once() is False
    assert calls == []
    busy["value"] = False
    assert worker.run_once() is True and calls == ["load"]
    assert key not in worker._cache and hero_framing._read(key) is None


def test_authorized_browse_warms_only_four_movie_or_series_backdrops(monkeypatch) -> None:  # noqa: ANN001
    worker = hero_framing.HeroFraming()
    queued: list[tuple[str, hero_framing.Loader]] = []
    monkeypatch.setattr(worker, "warm", lambda key, loader: queued.append((key, loader)))
    rows = [
        SimpleNamespace(id=f"00000000-0000-0000-0000-00000000000{index}", type=kind, images={"Backdrop": {"path": f"{index}.jpg", "tag": str(index)}})
        for index, kind in enumerate(("episode", "movie", "series", "movie", "series", "movie"))
    ]
    worker.warm_library("member", rows)
    assert len(queued) == hero_framing.WARM_CANDIDATES
    assert all(key == routes.art_urls.source_key(rows[index].id, "Backdrop", str(index)) for key, index in zip((item[0] for item in queued), (1, 2, 3, 4), strict=True))


def test_shutdown_drops_the_running_key_without_caching_a_cancelled_job(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    monkeypatch.setattr(hero_framing.sessions, "video_encodes", lambda: 1)
    worker = hero_framing.HeroFraming()
    key = "c" * 64
    assert worker.request(key, lambda: (_ for _ in ()).throw(AssertionError("loader must not run"))).state == "pending"
    worker._stop.set()
    assert worker.run_once() is False
    assert key not in worker._running
    assert key not in worker._cache


def test_local_loader_normalizes_title_image_bytes_and_stop_kills_an_owned_child(monkeypatch) -> None:  # noqa: ANN001
    title = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", images={"Backdrop": {"path": "fanart.jpg", "tag": "fresh"}})
    expected = image_url(title, "Backdrop")
    key = routes.art_urls.source_key(title.id, "Backdrop", "fresh")

    class Db:
        def get(self, model, title_id):  # noqa: ANN001
            assert title_id == title.id
            return title

    class Context:
        def __enter__(self):
            return Db()

        def __exit__(self, *args):  # noqa: ANN002
            return False

    monkeypatch.setattr(titles, "title_image_bytes", lambda db, title, kind, artwork: ("image/jpeg", b"image"))
    assert hero_framing.local_loader(title.id, expected, key, object(), session_factory=Context)() == (b"image", "image/jpeg")

    class Child:
        killed = False

        def kill(self):
            self.killed = True

    worker = hero_framing.HeroFraming()
    child = Child()
    worker._child = child
    worker.stop()
    assert child.killed


def test_route_rejects_untrusted_urls_and_hides_inaccessible_titles(monkeypatch) -> None:  # noqa: ANN001
    user = SimpleNamespace(id="member")
    assert routes.framing("https://127.0.0.1/t/p/w1280/a.jpg", user, object()) == {"state": "unavailable"}
    assert routes.framing("https://image.tmdb.org/t/p/w1280/a.jpg?x=1", user, object()) == {"state": "unavailable"}

    class Hidden:
        def __init__(self, db):  # noqa: ANN001
            del db

        def get_visible(self, title_id, current_user):  # noqa: ANN001
            del title_id, current_user
            return None

    monkeypatch.setattr(routes, "TitleService", Hidden)
    with pytest.raises(HTTPException) as raised:
        routes.framing("/api/titles/00000000-0000-0000-0000-000000000001/images/Backdrop?tag=0000000000000000", user, object())
    assert raised.value.status_code == 404


def test_catalog_backdrop_must_have_been_issued_before_remote_enqueue(monkeypatch) -> None:  # noqa: ANN001
    src = "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
    monkeypatch.setattr(routes, "_artwork", object(), raising=False)
    monkeypatch.setattr(routes, "_result", lambda key, loader: {"state": "pending"})
    assert routes.framing(src, SimpleNamespace(id="member"), object()) == {"state": "unavailable"}
    hero_framing.catalog_sources.register({"results": [{"backdrop_url": src}]})
    assert routes.framing(src, SimpleNamespace(id="member"), object()) == {"state": "pending"}


def test_route_rejects_a_stale_local_backdrop_without_enqueueing(monkeypatch) -> None:  # noqa: ANN001
    title = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", images={"Backdrop": {"path": "fanart.jpg", "tag": "fresh"}})

    class Visible:
        def __init__(self, db):  # noqa: ANN001
            del db

        def get_visible(self, title_id, current_user):  # noqa: ANN001
            del title_id, current_user
            return title

    monkeypatch.setattr(routes, "TitleService", Visible)
    monkeypatch.setattr(routes, "_result", lambda key, loader: (_ for _ in ()).throw(AssertionError("must not enqueue")))
    stale = image_url(title, "Backdrop").replace("tag=", "tag=0")
    assert routes.framing(stale, SimpleNamespace(id="member"), object()) == {"state": "unavailable"}


def test_route_passes_the_member_to_its_deferred_local_source_recheck(monkeypatch) -> None:  # noqa: ANN001
    title = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", images={"Backdrop": {"path": "fanart.jpg", "tag": "fresh"}})

    class Visible:
        def __init__(self, db):  # noqa: ANN001
            del db

        def get_visible(self, title_id, current_user):  # noqa: ANN001
            assert title_id == title.id and current_user.id == "member"
            return title

    captured: dict[str, object] = {}
    monkeypatch.setattr(routes, "TitleService", Visible)
    monkeypatch.setattr(routes, "_artwork", object(), raising=False)
    monkeypatch.setattr(
        routes.hero_framing,
        "local_loader",
        lambda title_id, src, key, artwork, *, user_id: captured.update({"title_id": title_id, "src": src, "key": key, "user_id": user_id}) or (lambda: None),
    )
    monkeypatch.setattr(routes, "_result", lambda key, loader: {"state": "pending"})
    assert routes.framing(image_url(title, "Backdrop"), SimpleNamespace(id="member"), object()) == {"state": "pending"}
    assert captured["title_id"] == title.id and captured["user_id"] == "member"


def test_deferred_library_load_rechecks_member_access_and_current_source(monkeypatch) -> None:  # noqa: ANN001
    title = SimpleNamespace(id="00000000-0000-0000-0000-000000000001", images={"Backdrop": {"path": "fanart.jpg", "tag": "fresh"}})
    expected = image_url(title, "Backdrop")
    key = routes.art_urls.source_key(title.id, "Backdrop", "fresh")
    access = {"user": None, "visible": None}

    class Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ANN002
            return False

        def get(self, model, identifier):  # noqa: ANN001
            assert identifier == "member", "a missing member must not become an unscoped title read"
            return access["user"]

    monkeypatch.setattr(titles, "TitleService", lambda db: SimpleNamespace(get_visible=lambda title_id, user: access["visible"]))
    monkeypatch.setattr(titles, "title_image_bytes", lambda *args: (_ for _ in ()).throw(AssertionError("denied or stale source must not be read")))
    load = hero_framing.local_loader(title.id, expected, key, object(), user_id="member", session_factory=Db)
    assert load() is None
    access["user"] = SimpleNamespace(id="member")
    assert load() is None
    access["visible"] = title
    title.images["Backdrop"]["tag"] = "replacement"
    assert load() is None


def test_warm_dispatch_yields_if_activity_resumes_after_selection(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(hero_framing.HeroFraming, "running", property(lambda _: True))
    busy = {"value": False, "checks": 0}
    monkeypatch.setattr(hero_framing.background_work, "busy", lambda: busy["value"])

    def encodes():
        busy["checks"] += 1
        if busy["checks"] == 2:
            busy["value"] = True
        return 0

    monkeypatch.setattr(hero_framing.sessions, "video_encodes", encodes)
    monkeypatch.setattr(hero_framing, "analyze", lambda data, **_: ready())
    worker = hero_framing.HeroFraming()
    calls = []
    worker.warm("a" * 64, lambda: (calls.append("warm") or b"warm", "image/jpeg"))
    assert worker.run_once() is False and calls == []
    assert "a" * 64 in worker._warm and not worker._running
    worker.request("b" * 64, lambda: (calls.append("interactive") or b"image", "image/jpeg"))
    assert worker.run_once() is True and calls == ["interactive"]
