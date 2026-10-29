from __future__ import annotations

import pytest

from app.services import omdb
from app.services.artwork import RemoteArtworkResponse
from app.services.network_policy import PublicSourcePolicy
from test_artwork import resolver_for


def test_parse_ratings_accepts_only_named_critic_scores_and_fallbacks() -> None:
    assert omdb.parse_ratings({"Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.7/10"},
        {"Source": "Rotten Tomatoes", "Value": "87%"},
        {"Source": "Metacritic", "Value": "73/100"},
        {"Source": "Rotten Tomatoes Audience", "Value": "99%"},
    ]}) == {"imdb_rating": 8.7, "rotten_tomatoes_rating": 87.0, "metacritic_rating": 73.0}
    assert omdb.parse_ratings({"Ratings": [], "imdbRating": "8.2", "Metascore": "74"}) == {"imdb_rating": 8.2, "metacritic_rating": 74.0}


@pytest.mark.parametrize("raw", [
    {"Ratings": [{"Source": "Internet Movie Database", "Value": "true/10"}]},
    {"Ratings": [{"Source": "Rotten Tomatoes", "Value": "101%"}]},
    {"Ratings": [{"Source": "Metacritic", "Value": "73/10"}]},
    {"imdbRating": float("nan"), "Metascore": True},
])
def test_parse_ratings_rejects_malformed_values(raw: dict) -> None:
    assert omdb.parse_ratings(raw) == {}


@pytest.mark.parametrize("raw", [
    {"Response": "False", "Error": "invalid key"},
    {"Response": "True", "imdbID": "tt9999999", "Ratings": []},
])
def test_ratings_rejects_unavailable_or_mismatched_title(raw: dict, monkeypatch) -> None:  # noqa: ANN001
    client = omdb.OmdbClient("test-key")
    monkeypatch.setattr(client, "get", lambda imdb_id: raw)
    with pytest.raises(omdb.OmdbError):
        client.ratings("tt0133093")


def test_client_rejects_a_final_redirect_off_the_fixed_omdb_host(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(omdb, "policy", PublicSourcePolicy(resolver=resolver_for({
        "www.omdbapi.com": ["93.184.216.34"], "evil.example": ["93.184.216.34"],
    })))

    class RedirectingFetcher:
        def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201
            validate_redirect(url)
            return RemoteArtworkResponse("application/json", [b"{}"], final_url="https://evil.example/steal")

    monkeypatch.setattr(omdb, "transport", RedirectingFetcher())
    with pytest.raises(omdb.OmdbError, match="unapproved host"):
        omdb.OmdbClient("test-key").get("tt0133093")


def test_client_uses_bounded_json_and_redacts_the_key_in_failures(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(omdb, "policy", PublicSourcePolicy(resolver=resolver_for({"www.omdbapi.com": ["93.184.216.34"]})))
    cases = [
        RemoteArtworkResponse("text/html", [b"no"]),
        RemoteArtworkResponse("application/json", [b"not json"]),
        RemoteArtworkResponse("application/json", [b"x" * (omdb.MAX_RESPONSE_BYTES + 1)]),
        RemoteArtworkResponse("application/json", [b"{}"], status_code=503),
        TimeoutError(),
    ]

    class FakeFetcher:
        def __init__(self, reply):  # noqa: ANN001
            self.reply = reply

        def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201
            validate_redirect(url)
            if isinstance(self.reply, BaseException):
                raise self.reply
            return self.reply

    for reply in cases:
        monkeypatch.setattr(omdb, "transport", FakeFetcher(reply))
        client = omdb.OmdbClient("private-test-key")
        with pytest.raises(omdb.OmdbError) as raised:
            client.get("tt0133093")
        assert "private-test-key" not in str(raised.value) and "private-test-key" not in repr(client)


def test_client_returns_a_matching_bounded_json_document(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(omdb, "policy", PublicSourcePolicy(resolver=resolver_for({"www.omdbapi.com": ["93.184.216.34"]})))

    class FakeFetcher:
        def fetch(self, url, *, validate_redirect, headers=None):  # noqa: ANN001, ANN201
            validate_redirect(url)
            assert headers == {"Accept": "application/json"}
            return RemoteArtworkResponse("application/json; charset=utf-8", [b'{"imdbID":"tt0133093"}'])

    monkeypatch.setattr(omdb, "transport", FakeFetcher())
    assert omdb.OmdbClient("test-key").get("tt0133093") == {"imdbID": "tt0133093"}
