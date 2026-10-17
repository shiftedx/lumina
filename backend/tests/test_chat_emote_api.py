from app.models import User
from app.services.artwork import ArtworkResponse

import app.main as main


class StubEmoteArtwork:
    def load_remote_artwork(self, artwork_id, *, before_upstream_fetch=None):
        return ArtworkResponse(content_type="image/png", content=b"emote-image")


def test_emote_images_require_authentication_and_use_private_cache(api_client, monkeypatch) -> None:
    monkeypatch.setattr(main, "chat_emote_artwork", StubEmoteArtwork())
    endpoint = "/api/artwork/emotes/opaque"
    assert api_client(base_url="http://localhost").get(endpoint).status_code == 401
    member = User(id="emote-viewer", username="viewer", display_name="Viewer", role="viewer", is_active=True)
    client = api_client(user=member, base_url="http://localhost")
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.content == b"emote-image"
    assert response.headers["cache-control"].startswith("private")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(endpoint, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
