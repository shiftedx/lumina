"""Ui_prefs.home_shelves round-trips through PUT /api/settings/me, and the shallow merge keeps every
other preference (user_settings.py merges ui_prefs key by key)."""
from __future__ import annotations

from support import make_user

LAYOUT = [{"id": "next_up", "visible": True}, {"id": "live", "visible": False}]


def test_home_shelves_round_trip_without_touching_other_prefs(db_factory, api_client) -> None:  # noqa: ANN001
    member = make_user("member-home")
    with db_factory() as session:
        session.add(member)
        session.commit()
    client = api_client(user=member, base_url="http://localhost")
    assert client.put("/api/settings/me", json={"ui_prefs": {"theme": "light", "player_volume": 0.4}}).status_code == 200

    saved = client.put("/api/settings/me", json={"ui_prefs": {"home_shelves": LAYOUT}})
    assert saved.status_code == 200, saved.text
    prefs = client.get("/api/settings/me").json()["ui_prefs"]
    assert prefs["home_shelves"] == LAYOUT
    assert (prefs["theme"], prefs["player_volume"]) == ("light", 0.4)

    assert client.put("/api/settings/me", json={"ui_prefs": {"home_shelves": None}}).status_code == 200
    prefs = client.get("/api/settings/me").json()["ui_prefs"]
    assert prefs["home_shelves"] is None
    assert (prefs["theme"], prefs["player_volume"]) == ("light", 0.4)
