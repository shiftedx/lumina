"""Bounded OMDb score lookups keyed only by a known IMDb id.

OMDb is an optional supplement to Lumina's TMDB refresh. Its response is untrusted:
only its three named score entries are parsed and no provider response can carry a
URL, secret, or error body into logs or API errors.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from app.config import settings
from app.models import AppSettings
from app.services.artwork import PublicArtworkFetcher, RemoteArtworkFetcher
from app.services.network_policy import PublicSourcePolicy, PublicSourcePolicyError

API_HOST = "www.omdbapi.com"
API_BASE = f"https://{API_HOST}/"
MAX_RESPONSE_BYTES = 512 * 1024
IMDB_ID = re.compile(r"tt\d{1,10}")
_FRACTION = re.compile(r"(\d+(?:\.\d+)?)/(10|100)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

policy = PublicSourcePolicy()
transport: RemoteArtworkFetcher = PublicArtworkFetcher(policy)


class OmdbError(RuntimeError):
    """Content-free OMDb failure."""


def effective_api_key(record: AppSettings) -> str | None:
    value = (getattr(record, "omdb_api_key", None) or settings.omdb_api_key or "").strip()
    return value or None


def pinned_validator(source_policy: PublicSourcePolicy) -> Callable[[str], str]:
    def validate(url: str) -> str:
        try:
            parts = urlsplit(url.strip() if isinstance(url, str) else "")
            port = parts.port
        except ValueError as exc:
            raise PublicSourcePolicyError() from exc
        if (parts.scheme != "https" or parts.hostname != API_HOST or port not in (None, 443)
                or parts.username is not None or parts.password is not None):
            raise PublicSourcePolicyError()
        return source_policy.validate_url(url)
    return validate


class OmdbClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._fetcher = transport
        self._validate = pinned_validator(policy)

    def __repr__(self) -> str:
        return "OmdbClient()"

    def get(self, imdb_id: str) -> dict[str, Any]:
        if not IMDB_ID.fullmatch(imdb_id):
            raise OmdbError("OMDb received an invalid IMDb id")
        url = f"{API_BASE}?{urlencode({'apikey': self._api_key, 'i': imdb_id, 'r': 'json'})}"
        try:
            response = self._fetcher.fetch(url, validate_redirect=self._validate, headers={"Accept": "application/json"})
        except PublicSourcePolicyError:
            raise OmdbError("OMDb request went to an unapproved host") from None
        except Exception:  # noqa: BLE001 - transport details can contain the key
            raise OmdbError("OMDb is unreachable") from None
        try:
            if response.final_url is not None:
                self._validate(response.final_url)
            if not 200 <= int(response.status_code) < 300:
                raise OmdbError(f"OMDb returned HTTP {int(response.status_code)}")
            if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise OmdbError("OMDb returned a non-JSON response")
            body = bytearray()
            for chunk in response.body:
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise OmdbError("OMDb response exceeded the size limit")
            raw = json.loads(body)
            if not isinstance(raw, dict):
                raise OmdbError("OMDb returned an unexpected response")
            return raw
        except OmdbError:
            raise
        except PublicSourcePolicyError:
            raise OmdbError("OMDb request went to an unapproved host") from None
        except (ValueError, RecursionError):
            raise OmdbError("OMDb returned malformed JSON") from None
        except Exception:  # noqa: BLE001
            raise OmdbError("OMDb is unreachable") from None
        finally:
            response.close()

    def ratings(self, imdb_id: str) -> dict[str, float]:
        raw = self.get(imdb_id)
        # A title-search miss or a response for another title must not become a durable score.
        if raw.get("Response") == "False":
            raise OmdbError("OMDb rejected the API key or found no matching title")
        if raw.get("imdbID") != imdb_id:
            raise OmdbError("OMDb returned a mismatched title")
        return parse_ratings(raw)


def client_for(record: AppSettings) -> OmdbClient | None:
    key = effective_api_key(record)
    return OmdbClient(key) if key else None


def _score(value: object, maximum: int, *, fraction: bool = False, percent: bool = False) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _FRACTION.fullmatch(text) if fraction else _PERCENT.fullmatch(text) if percent else _NUMBER.fullmatch(text)
    if match is None:
        return None
    if fraction and int(match.group(2)) != maximum:
        return None
    try:
        score = float(match.group(1) if (fraction or percent) else match.group(0))
    except ValueError:
        return None
    return score if score == score and score != float("inf") and score != float("-inf") and 0 <= score <= maximum else None


def parse_ratings(raw: object) -> dict[str, float]:
    """Accept exactly OMDb's named critic entries plus documented IMDb/Metascore fallbacks."""
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, float] = {}
    ratings = data.get("Ratings")
    if isinstance(ratings, list):
        for entry in ratings:
            if not isinstance(entry, dict):
                continue
            source, value = entry.get("Source"), entry.get("Value")
            if source == "Internet Movie Database":
                score = _score(value, 10, fraction=True)
                if score is not None:
                    out["imdb_rating"] = score
            elif source == "Rotten Tomatoes":
                score = _score(value, 100, percent=True)
                if score is not None:
                    out["rotten_tomatoes_rating"] = score
            elif source == "Metacritic":
                score = _score(value, 100, fraction=True)
                if score is not None:
                    out["metacritic_rating"] = score
    if "imdb_rating" not in out and (score := _score(data.get("imdbRating"), 10)) is not None:
        out["imdb_rating"] = score
    if "metacritic_rating" not in out and (score := _score(data.get("Metascore"), 100)) is not None:
        out["metacritic_rating"] = score
    return out
