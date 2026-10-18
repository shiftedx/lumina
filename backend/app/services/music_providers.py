"""Bounded, cached public music enrichment. A provider guess never overwrites a user edit."""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import quote, unquote, urlencode, urlparse

import httpx
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import aliased

from app.db import session_scope
from app.config import settings
from app.models import LibraryItem, MediaTitle, MusicEditBatch, MusicLookup, utcnow
from app.music_schemas import MusicCue, MusicLyrics, MusicVideo
from app.persistence import write_transaction
from app.services.media_titles import apply_field

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="music-metadata")
_pending: set[str] = set()
_rerun: dict[str, tuple[str, Callable[[], dict], int]] = {}
_lock = threading.Lock()
_brainz_lock = threading.Lock()
_brainz_at = 0.0
_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_PUBLIC_HOSTS = {"musicbrainz.org", "coverartarchive.org", "lrclib.net", "en.wikipedia.org", "www.wikidata.org", "commons.wikimedia.org"}
_artwork = None


def configure_artwork(artwork) -> None:  # noqa: ANN001
    global _artwork
    _artwork = artwork


def validate_art_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not (
        hostname in {"coverartarchive.org", "archive.org", "upload.wikimedia.org", "thumb.wikimedia.org"} or hostname.endswith(".archive.org")
    ):
        raise ValueError("Unsupported music artwork host")
    return value


def normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    letters = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"\w+", letters.replace("_", " ")))


def key_for(kind: str, *parts: str) -> str:
    return hashlib.sha256(json.dumps([kind, *parts], ensure_ascii=False).encode()).hexdigest()


def fetch_json(url: str) -> dict:
    """Fixed public providers only; never resolve a library-supplied URL."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _PUBLIC_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Unsupported music provider")
    if parsed.hostname == "musicbrainz.org":
        global _brainz_at
        with _brainz_lock:
            remaining = 1.05 - (time.monotonic() - _brainz_at)
            if remaining > 0:
                time.sleep(remaining)
            _brainz_at = time.monotonic()
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "Lumina/0.3 music-library (https://github.com/shiftedx/lumina)", "Accept": "application/json"}) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Music provider response too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid music provider response")
    return value


def cached(db, key: str) -> dict | None:  # noqa: ANN001
    row = db.get(MusicLookup, key)
    return row.payload if row else None


def enqueue(key: str, kind: str, load: Callable[[], dict], *, ttl_days: int = 7, force: bool = False) -> bool:
    if (kind == "videos" and not settings.music_youtube_discovery) or (kind != "videos" and not settings.music_metadata_enabled):
        return False
    with session_scope() as db:
        row = db.get(MusicLookup, key)
        if not force and row and row.expires_at > utcnow():
            return False
    with _lock:
        if key in _pending:
            if force:
                _rerun[key] = (kind, load, ttl_days)
                return True
            return False
        if len(_pending) >= 128:
            return False
        _pending.add(key)

    def run() -> None:
        try:
            try:
                payload = load()
                expiry = utcnow() if payload.get("changed") else utcnow() + (timedelta(hours=1) if payload.get("artwork_error") else timedelta(days=ttl_days))
            except Exception:  # noqa: BLE001 - availability is reported without private provider details
                payload, expiry = {"error": "Music provider is temporarily unavailable"}, utcnow() + timedelta(hours=1)
            with session_scope() as db, write_transaction(db, name="music_lookup"):
                row = db.get(MusicLookup, key)
                if row is None:
                    row = MusicLookup(key=key, kind=kind)
                    db.add(row)
                # Keep a usable stale answer during an outage.
                if "error" not in payload or not row.payload:
                    row.payload = payload
                row.refreshed_at, row.expires_at = utcnow(), expiry
                stale = select(MusicLookup.key).order_by(MusicLookup.refreshed_at.desc()).offset(10_000)
                db.execute(delete(MusicLookup).where(MusicLookup.key.in_(stale)))
        finally:
            with _lock:
                _pending.discard(key)
                again = _rerun.pop(key, None)
            if again:
                enqueue(key, again[0], again[1], ttl_days=again[2], force=True)

    _pool.submit(run)
    return True


def pending(key: str) -> bool:
    with _lock:
        return key in _pending


def parse_lyrics(text: str | None, *, source: str | None = None, instrumental: bool = False) -> MusicLyrics:
    if not text:
        return MusicLyrics(source=source, instrumental=instrumental)
    cues, plain = [], []
    offset_match = re.search(r"\[offset:([+-]?\d{1,7})\]", text[:100_000], re.I)
    offset = int(offset_match.group(1)) / 1000 if offset_match else 0
    for line in text[:100_000].splitlines()[:3000]:
        stamps = list(_TIMESTAMP.finditer(line))
        if stamps:
            lyric = _TIMESTAMP.sub("", line).strip()
            if lyric:
                plain.append(lyric)
                for stamp in stamps:
                    minutes, seconds, fraction = stamp.groups()
                    if int(seconds) < 60:
                        cues.append(MusicCue(start=max(0, int(minutes) * 60 + int(seconds) + int(fraction or "0") / (10 ** len(fraction or "0")) + offset), text=lyric))
        elif not re.fullmatch(r"\[[a-zA-Z]+:.*\]", line.strip()):
            plain.append(line)
    cues.sort(key=lambda cue: cue.start)
    return MusicLyrics(plain="\n".join(plain).strip() or None, cues=cues, source=source, instrumental=instrumental)


def lyric_lookup(title: str, artist: str, album: str, duration: float | None) -> dict:
    params = {"track_name": title, "artist_name": artist, "album_name": album}
    if duration:
        params["duration"] = str(round(duration))
    try:
        value = fetch_json("https://lrclib.net/api/get?" + urlencode(params))
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return MusicLyrics(source="LRCLIB").model_dump()
        raise
    return parse_lyrics(value.get("syncedLyrics") or value.get("plainLyrics"), source="LRCLIB", instrumental=value.get("instrumental") is True).model_dump()


def classify_video(title: str, uploader: str | None) -> str:
    text = normalized(title)
    if re.search(r"\b(cover|covered)\b", text):
        return "cover"
    if re.search(r"\b(remix|remixed|rework|mashup)\b", text):
        return "remix"
    if re.search(r"\b(live|concert|session|acoustic|unplugged)\b", text):
        return "live"
    if re.search(r"\b(official|vevo)\b", text + " " + normalized(uploader or "")):
        return "official"
    return "performance"


def correlate_video(entry: Any, artist: str, track: str | None = None) -> MusicVideo | None:
    title, uploader = entry.title or "", entry.uploader or ""
    text, channel = normalized(title), normalized(uploader)
    if re.search(r"\b(reaction|reacts|tutorial|interview|review|explained|analysis|news|how to)\b", text):
        return None
    artist_words = set(normalized(artist).split())
    track_words = set(normalized(track or "").split())
    if not artist_words or not artist_words <= set((text + " " + channel).split()):
        return None
    if track_words and not track_words <= set(text.split()):
        return None
    url = entry.webpage_url or ""
    match = re.fullmatch(r"[A-Za-z0-9_-]{11}", entry.id or "")
    if not match:
        parsed = urlparse(url)
        if parsed.hostname not in ("www.youtube.com", "youtube.com", "youtu.be"):
            return None
        from urllib.parse import parse_qs
        video_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else (parse_qs(parsed.query).get("v") or [""])[0]
        match = re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
    if not match:
        return None
    video_id = match.group()
    duration = entry.duration if type(entry.duration) in (int, float) and 0 < entry.duration < 86_400 else None
    return MusicVideo(id=video_id, title=title[:500], url=f"https://www.youtube.com/watch?v={video_id}",
                      thumbnail_url=entry.thumbnail_url, uploader=uploader[:300], duration=duration,
                      performance=classify_video(title, uploader), association="recording" if track else "artist")


def video_lookup(artist: str, track: str | None = None) -> dict:
    from app.services.yt_dlp_service import YtDlpService
    query = f"{artist} {track or ''} music video live".strip()
    with session_scope() as db:
        answer = YtDlpService(db).youtube_search(query, limit=12)
    videos = [video for entry in answer.items if (video := correlate_video(entry, artist, track))]
    return {"items": [v.model_dump() for v in videos[:12]]}


def candidates_for(kind: str, name: str, artist: str | None = None) -> list[dict]:
    entity = "artist" if kind == "artist" else "release-group" if kind == "release-group" else "release"
    escaped = name.replace('"', " ").replace("\\", " ")[:300]
    query = f'{entity.replace("-", "")}:"{escaped}"'
    if artist:
        query += f' AND artist:"{artist.replace(chr(34), " ")[:300]}"'
    value = fetch_json(f"https://musicbrainz.org/ws/2/{entity}/?" + urlencode({"query": query, "fmt": "json", "limit": 10}))
    results = value.get("artists" if kind == "artist" else "release-groups" if kind == "release-group" else "releases", [])
    rows = []
    for entry in results[:10]:
        try:
            identity = str(uuid.UUID(entry["id"]))
        except (ValueError, KeyError, TypeError):
            continue
        credits = "".join(c.get("name", "") + c.get("joinphrase", " ") for c in entry.get("artist-credit", []) if isinstance(c, dict)).strip()
        names = [entry.get("name") or entry.get("title") or ""]
        if kind == "artist":
            names.extend(alias["name"] for alias in entry.get("aliases", [])[:30] if isinstance(alias, dict) and isinstance(alias.get("name"), str))
        exact = bool(normalized(name)) and any(normalized(candidate) == normalized(name) for candidate in names)
        if artist:
            exact = exact and normalized(credits) == normalized(artist)
        # A partial search page cannot establish a unique identity.
        count = value.get("count")
        exact = exact and not (type(count) is int and count > len(results))
        rows.append({"id": identity, "name": entry.get("name") or entry.get("title"), "artist": credits,
                     "release_group_id": (entry.get("release-group") or {}).get("id"),
                     "disambiguation": entry.get("disambiguation"), "date": entry.get("date"), "country": entry.get("country"),
                     "score": int(entry.get("score") or 0), "exact": exact})
    return rows


def release_page(artist_id: str, offset: int = 0) -> dict:
    identity = str(uuid.UUID(artist_id))
    value = fetch_json("https://musicbrainz.org/ws/2/release-group?" + urlencode({"artist": identity, "fmt": "json", "limit": 100, "offset": offset}))
    items = []
    for release in value.get("release-groups", [])[:100]:
        try:
            release_id = str(uuid.UUID(release.get("id")))
        except (TypeError, ValueError, AttributeError):
            continue
        items.append({"id": release_id, "name": str(release.get("title") or "Untitled")[:300],
                      "year": str(release.get("first-release-date") or "")[:4], "type": str(release.get("primary-type") or "Release")[:100]})
    count = value.get("release-group-count")
    return {"items": items, "next_offset": offset + 100 if type(count) is int and offset + 100 < count else None}


def _credits(value: dict) -> list[dict]:
    result = []
    recordings = [track.get("recording", {}) for medium in value.get("media", [])[:30] for track in medium.get("tracks", [])[:200]]
    for subject in [value, *recordings]:
        for credit in subject.get("artist-credit", [])[:50]:
            if isinstance(credit, dict) and isinstance(credit.get("name"), str):
                result.append({"name": credit["name"][:200], "role": "Performer", "instrument": None})
        for relation in subject.get("relations", [])[:200]:
            name = (relation.get("artist") or {}).get("name")
            if isinstance(name, str):
                instruments = [str(a)[:100] for a in relation.get("attributes", [])[:5]]
                result.append({"name": name[:200], "role": str(relation.get("type") or "Performer")[:200], "instrument": ", ".join(instruments)[:200] or None})
    unique = {json.dumps(row, sort_keys=True): row for row in result}
    return list(unique.values())[:200]


def release_editions(group_id: str, offset: int = 0) -> dict:
    identity = str(uuid.UUID(group_id))
    value = fetch_json("https://musicbrainz.org/ws/2/release?" + urlencode({"release-group": identity, "fmt": "json", "limit": 100, "offset": offset, "inc": "labels"}))
    items = []
    for release in value.get("releases", [])[:100]:
        try:
            release_id = str(uuid.UUID(release.get("id")))
        except (ValueError, TypeError, AttributeError):
            continue
        labels = release.get("label-info") or []
        label = (labels[0].get("label") or {}).get("name") if labels and isinstance(labels[0], dict) else None
        items.append({"id": release_id, "name": str(release.get("title") or "Untitled")[:300], "date": str(release.get("date") or "")[:10],
                      "country": str(release["country"])[:100] if release.get("country") else None,
                      "edition": str(release["disambiguation"])[:300] if release.get("disambiguation") else None,
                      "label": str(label)[:300] if label else None})
    count = value.get("release-count")
    return {"items": items, "next_offset": offset + 100 if type(count) is int and offset + 100 < count else None}


def artist_profile(value: dict) -> tuple[dict, list[dict]]:
    """Follow identity-linked articles and P18 photographs, never a name-based image search."""
    fields, pictures, entity = {}, [], {}
    retry = False
    relations = value.get("relations", [])[:200]
    wiki = next((r.get("url", {}).get("resource", "") for r in relations if r.get("type") == "wikipedia"), "")
    wikidata = next((r.get("url", {}).get("resource", "") for r in relations if r.get("type") == "wikidata"), "")
    entity_id = wikidata.rsplit("/", 1)[-1]
    if re.fullmatch(r"Q\d{1,12}", entity_id):
        try:
            entity = fetch_json(f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json").get("entities", {}).get(entity_id, {})
            article_name = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
            if not wiki and isinstance(article_name, str):
                wiki = "https://en.wikipedia.org/wiki/" + quote(article_name.replace(" ", "_"), safe="")
        except (httpx.HTTPError, ValueError):
            retry = True
    portrait = None
    if wiki.startswith("https://en.wikipedia.org/wiki/"):
        try:
            article = fetch_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(unquote(wiki.partition("/wiki/")[2]), safe=""))
            fields = {"overview": article.get("extract", "")[:10_000], "biography_url": wiki}
            portrait = (article.get("originalimage") or article.get("thumbnail") or {}).get("source")
            article_entity = article.get("wikibase_item", "")
            if not entity and isinstance(article_entity, str) and re.fullmatch(r"Q\d{1,12}", article_entity):
                entity = fetch_json(f"https://www.wikidata.org/wiki/Special:EntityData/{article_entity}.json").get("entities", {}).get(article_entity, {})
        except (httpx.HTTPError, ValueError):
            retry = True
    # Commons supplies image dimensions and per-photo attribution. Up to three
    # explicit images of this entity can fill different roles without borrowing a sleeve.
    claims = sorted(entity.get("claims", {}).get("P18", [])[:20], key=lambda c: c.get("rank") != "preferred")
    filenames = []
    for claim in claims:
        filename = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if claim.get("rank") != "deprecated" and isinstance(filename, str) and 0 < len(filename) <= 300 and not any(ch in filename for ch in "|\r\n"):
            filenames.append("File:" + filename)
    if isinstance(portrait, str) and urlparse(portrait).hostname in ("upload.wikimedia.org", "thumb.wikimedia.org"):
        parts = urlparse(portrait).path.split("/")
        filename = unquote(parts[-2] if "/thumb/" in portrait else parts[-1])
        if re.search(r"\.(jpe?g|png|webp)$", filename, re.I) and len(filename) <= 300 and not any(ch in filename for ch in "|\r\n"):
            filenames.insert(0, "File:" + filename)
    if filenames:
        try:
            answer = fetch_json("https://commons.wikimedia.org/w/api.php?" + urlencode({"action": "query", "format": "json", "prop": "imageinfo",
                "titles": "|".join(dict.fromkeys(filenames[:3])), "iiprop": "url|size|extmetadata", "iiurlwidth": 1600}))
            for page in answer.get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                width, height = info.get("width"), info.get("height")
                url = info.get("thumburl") or info.get("url")
                if type(width) is not int or type(height) is not int or min(width, height) < 400 or not isinstance(url, str):
                    continue
                validate_art_url(url)
                metadata = info.get("extmetadata", {})
                def text(key: str) -> str:
                    return html.unescape(re.sub(r"<[^>]*>", "", str(metadata.get(key, {}).get("value", ""))))[:500].strip()
                source = info.get("descriptionurl", "")
                if not source.startswith("https://commons.wikimedia.org/wiki/File:"):
                    continue
                pictures.append({"url": url, "width": width, "height": height, "source_url": source,
                                 "author": text("Artist"), "license": text("LicenseShortName")})
        except (httpx.HTTPError, ValueError):
            retry = True
    if retry:
        fields["_artwork_retry"] = True
    if pictures:
        primary = min(pictures, key=lambda p: abs(p["width"] / p["height"] - 0.85))
        primary = {**primary, "image_type": "Primary"}
        background = next((p for p in pictures if p["source_url"] != primary["source_url"] and p["width"] >= p["height"] * 1.4), None)
        return fields, [primary, *([{**background, "image_type": "Backdrop"}] if background else [])]
    return fields, [{"url": portrait, "image_type": "Primary", "source_url": wiki, "author": "", "license": ""}] if portrait else []


def enrich_title(title_id: str, provider_id: str | None = None) -> dict:
    with session_scope() as db:
        title = db.get(MediaTitle, title_id)
        if title is None or title.type not in ("album", "artist"):
            return {}
        if title.locked:
            return {"locked": True}
        kind, name = title.type, title.name
        parent = db.get(MediaTitle, title.parent_id) if title.parent_id else None
        artist = parent.name if parent else None
        existing_id = (title.provider_ids or {}).get("MusicBrainz")
        release_group = (title.metadata_json or {}).get("release_group_id") if kind == "album" else None
        identity_before, parent_before = dict(title.provider_ids or {}), title.parent_id
        if (title.field_sources or {}).get("provider_ids") == "user" and not existing_id and not provider_id:
            return {"unmatched": True}
    candidates = [] if provider_id or existing_id else candidates_for(kind, name, artist)
    exact = [c for c in candidates if c["exact"] and c["score"] >= 95 and (not release_group or c.get("release_group_id") == release_group)]
    chosen = provider_id or existing_id or (exact[0]["id"] if len(exact) == 1 else None)
    if not chosen:
        group = release_group
        if kind == "album" and not group:
            groups = [c for c in candidates_for("release-group", name, artist) if c["exact"] and c["score"] >= 95]
            group = groups[0]["id"] if len(groups) == 1 else None
        group = str(uuid.UUID(group)) if group else None
        current = False
        with session_scope() as db, write_transaction(db, name="music_candidates"):
            title = db.get(MediaTitle, title_id)
            if title and not title.locked and title.name == name and title.parent_id == parent_before and (title.provider_ids or {}) == identity_before:
                current = True
                title.metadata_json = {**(title.metadata_json or {}), "music_candidates": candidates}
                if group:
                    apply_field(title, "release_group_id", group, "musicbrainz")
                title.metadata_due_at = utcnow() + timedelta(days=30)
        if group and current:
            try:
                import_art(title_id, f"https://coverartarchive.org/release-group/{group}/front-1200", release_group_id=group)
            except Exception as error:  # artwork availability does not establish an edition identity
                from app.services.artwork import ArtworkNotFoundError
                if not isinstance(error, ArtworkNotFoundError):
                    with session_scope() as db, write_transaction(db, name="music_art_retry"):
                        title = db.get(MediaTitle, title_id)
                        if title and (title.metadata_json or {}).get("release_group_id") == group:
                            title.metadata_due_at = utcnow() + timedelta(hours=1)
                    return {"candidates": candidates, "artwork_error": "Album artwork is temporarily unavailable"}
        return {"candidates": candidates}
    chosen = str(uuid.UUID(chosen))
    entity = "artist" if kind == "artist" else "release"
    includes = "release-groups+genres+tags+url-rels" if kind == "artist" else "artist-credits+recordings+release-groups+labels+genres+tags+artist-rels"
    value = fetch_json(f"https://musicbrainz.org/ws/2/{entity}/{chosen}?" + urlencode({"fmt": "json", "inc": includes}))
    fields = {"name": value.get("name") or value.get("title"), "genres": [g["name"] for g in value.get("genres", [])[:30] if isinstance(g.get("name"), str)],
              "styles": [g["name"] for g in sorted(value.get("tags", []), key=lambda t: t.get("count", 0), reverse=True)[:15] if isinstance(g.get("name"), str)]}
    if kind == "artist":
        life = value.get("life-span", {})
        fields.update(origin=(value.get("begin-area") or value.get("area") or {}).get("name"), overview=value.get("disambiguation"))
        if value.get("type") == "Person":
            fields.update({field: date for field, key in (("birth_date", "begin"), ("death_date", "end"))
                           if isinstance(date := life.get(key), str) and re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", date)})
        else:
            fields.update(active_since=life.get("begin"), active_until=life.get("end"))
        releases = release_page(chosen)["items"]
    else:
        group = value.get("release-group", {})
        labels = value.get("label-info", [])
        date = value.get("date", "")
        fields.update(year=int(date[:4]) if date[:4].isdigit() else None, premiered=date if len(date) == 10 else None,
                      country=value.get("country"), edition=value.get("disambiguation"), release_type=(group.get("primary-type") or "album").lower(),
                      label=(labels[0].get("label") or {}).get("name") if labels else None,
                      catalogue_number=labels[0].get("catalog-number") if labels else None,
                      original_year=int(group.get("first-release-date", "")[:4]) if group.get("first-release-date", "")[:4].isdigit() else None)
        releases = []
        fields["credits"] = _credits(value)
        fields["release_group_id"] = group.get("id")
    pictures = []
    profile_retry = False
    if kind == "artist":
        profile, pictures = artist_profile(value)
        profile_retry = profile.pop("_artwork_retry", False)
        fields.update({k: v for k, v in profile.items() if v})
    with session_scope() as db, write_transaction(db, name="music_enrichment"):
        title = db.get(MediaTitle, title_id)
        if title is None or title.locked:
            return {"locked": True}
        if title.name != name or title.parent_id != parent_before or (title.provider_ids or {}) != identity_before:
            return {"changed": True}
        if kind == "artist" and value.get("type") == "Person":
            # Earlier provider refreshes labelled person life spans as careers.
            # Remove only those derived values; a household edit remains authoritative.
            for field in ("active_since", "active_until"):
                if (title.field_sources or {}).get(field) == "musicbrainz":
                    title.metadata_json = {k: v for k, v in (title.metadata_json or {}).items() if k != field}
                    title.field_sources = {k: v for k, v in (title.field_sources or {}).items() if k != field}
        for field, content in fields.items():
            if content:
                from app.services.metadata_editor import CATALOGUE, FieldError, validate
                if field in CATALOGUE:
                    try:
                        content = validate(title.type, field, content)
                    except FieldError:
                        continue
                elif field == "release_group_id":
                    try:
                        content = str(uuid.UUID(content))
                    except (ValueError, TypeError):
                        continue
                apply_field(title, field, content, "musicbrainz")
        apply_field(title, "provider_ids", {**(title.provider_ids or {}), "MusicBrainz": chosen}, "musicbrainz")
        title.metadata_json = {**(title.metadata_json or {}), "music_candidates": [], "discography": releases}
        title.metadata_due_at = utcnow() + timedelta(days=30)
        from app.services.library_search import index_title
        db.flush()
        index_title(db, title)
    artwork_error = profile_retry
    if kind == "album":
        pictures = [{"url": f"https://coverartarchive.org/release/{chosen}/front-1200", "image_type": "Primary"}]
    for picture in pictures:
        try:
            import_art(title_id, picture["url"], identity=chosen, image_type=picture["image_type"], credit=picture if kind == "artist" else None)
        except Exception as error:  # noqa: BLE001 - artwork retries without discarding a valid identity
            from app.services.artwork import ArtworkNotFoundError
            artwork_error = artwork_error or not isinstance(error, ArtworkNotFoundError)
    if artwork_error:
        with session_scope() as db, write_transaction(db, name="music_art_retry"):
            title = db.get(MediaTitle, title_id)
            if title and (title.provider_ids or {}).get("MusicBrainz") == chosen:
                title.metadata_due_at = utcnow() + timedelta(hours=1)
    return {"identified": True, "id": chosen, **({"artwork_error": "Artist or release artwork is temporarily unavailable"} if artwork_error else {})}


def request_enrichment(title_id: str, provider_id: str | None = None, *, force: bool = False) -> bool:
    return enqueue(key_for("metadata", title_id), "metadata", lambda: enrich_title(title_id, provider_id), ttl_days=30, force=force)


def import_art(title_id: str, url: str | None, *, identity: str | None = None, image_type: str = "Primary", credit: dict | None = None,
               release_group_id: str | None = None) -> None:
    if not url or _artwork is None:
        return
    response = _artwork.load_pinned(url, bucket="music", max_bytes=2 * 1024**3, validate_redirect=validate_art_url)
    tag = hashlib.sha256(response.content).hexdigest()
    with session_scope() as db, write_transaction(db, name="music_art"):
        title = db.get(MediaTitle, title_id)
        if (title is not None and (identity is None or (title.provider_ids or {}).get("MusicBrainz") == identity)
                and (release_group_id is None or (title.metadata_json or {}).get("release_group_id") == release_group_id)):
            from app.services.media_titles import write_outcome
            field = f"images.{image_type}"
            image = {"music": url, "tag": tag}
            writable = write_outcome(title, field, image, "musicbrainz") in ("write", "same")
            apply_field(title, field, image, "musicbrainz")
            if credit and writable:
                credits = [c for c in (title.metadata_json or {}).get("artwork_credits", []) if c.get("image_type") != image_type]
                credits.append({**{k: credit[k] for k in ("image_type", "source_url", "author", "license")}, "tag": tag})
                title.metadata_json = {**(title.metadata_json or {}), "artwork_credits": credits[:3]}


def maintenance() -> None:
    """A restart-safe, small library backfill shares the existing server maintenance clock."""
    now = utcnow()
    child = aliased(MediaTitle)
    present_album = exists(select(LibraryItem.id).where(LibraryItem.title_id == MediaTitle.id, LibraryItem.status != "missing"))
    present_artist = exists(select(LibraryItem.id).join(child, LibraryItem.title_id == child.id).where(child.parent_id == MediaTitle.id, LibraryItem.status != "missing"))
    with session_scope() as db, write_transaction(db, name="music_maintenance"):
        db.execute(delete(MusicLookup).where(MusicLookup.kind.in_(("artwork_plan", "writeback_plan")), MusicLookup.expires_at <= now))
        db.execute(delete(MusicEditBatch).where(MusicEditBatch.created_at < now - timedelta(days=365)))
        if not settings.music_metadata_enabled:
            return
        titles = list(db.scalars(select(MediaTitle).where(MediaTitle.type.in_(("artist", "album")), MediaTitle.locked.is_(False),
            or_(MediaTitle.metadata_due_at.is_(None), MediaTitle.metadata_due_at <= now),
            or_(present_album, present_artist)).order_by(MediaTitle.metadata_due_at, MediaTitle.created_at, MediaTitle.id).limit(8)))
        queued = []
        for title in titles:
            cached = db.get(MusicLookup, key_for("metadata", title.id))
            title.metadata_due_at = cached.expires_at if cached and cached.expires_at > now else now + timedelta(hours=1)
            if cached is None or cached.expires_at <= now:
                queued.append(title.id)
    for title_id in queued:
        request_enrichment(title_id)
