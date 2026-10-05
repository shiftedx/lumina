"""Parse and rewrite HLS playlists without trusting the upstream document.

This module is deliberately network-free and stateless. It classifies a
playlist as a master or media playlist, resolves every referenced address to an
absolute URL against the playlist's own location, and rewrites each reference
through a caller-supplied ``rewrite`` callback that returns the Lumina-owned
address to expose to the browser. The relay uses the callback to revalidate the
public-source policy and register an owner-scoped resource for every address, so
no upstream URL, host, or query string survives into the rewritten output.

Anything the parser cannot account for is rejected fail-closed with
``MalformedManifestError`` rather than passed through.
"""

from __future__ import annotations

import re
from typing import Callable, Literal

HlsResourceKind = Literal["media_playlist", "segment", "map", "key"]
Rewriter = Callable[[HlsResourceKind, str], str]

PlaylistKind = Literal["master", "media"]


class MalformedManifestError(ValueError):
    """A playlist could not be safely parsed and is rejected fail-closed."""


# Match the URI attribute of a recognized tag in the forms lenient HLS parsers
# accept: quoted or unquoted value, and optional whitespace around the ``=``. The
# lookbehind keeps this from matching the tail of another attribute name such as
# SERVER-URI (which only appears on tags the relay rejects outright).
_URI_ATTRIBUTE = re.compile(r'(?<![A-Z0-9-])URI\s*=\s*(?:"([^"]*)"|([^",\s]+))')

# Tags whose ``URI="..."`` attribute addresses another playlist.
_MASTER_PLAYLIST_URI_TAGS = ("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:")
# Tags in a media playlist whose ``URI="..."`` attribute addresses media bytes.
_MEDIA_SEGMENT_URI_TAGS = ("#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:")
_PREFETCH_TAGS = ("#EXT-X-PREFETCH:", "#EXT-X-TWITCH-PREFETCH:")

_MASTER_MARKERS = (
    "#EXT-X-STREAM-INF",
    "#EXT-X-I-FRAME-STREAM-INF",
    "#EXT-X-MEDIA:",
    "#EXT-X-SESSION-KEY",
)
_MEDIA_MARKERS = (
    "#EXTINF",
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA-SEQUENCE",
    "#EXT-X-ENDLIST",
    "#EXT-X-MAP",
)


def _lines(text: str) -> list[str]:
    stripped = text.lstrip("﻿")
    return [line.rstrip("\r") for line in stripped.split("\n")]


def _require_header(lines: list[str]) -> None:
    for line in lines:
        if not line.strip():
            continue
        if line.strip() != "#EXTM3U":
            raise MalformedManifestError("The playlist did not begin with #EXTM3U.")
        return
    raise MalformedManifestError("The playlist was empty.")


def _markers_present(lines: list[str], markers: tuple[str, ...]) -> bool:
    for line in lines:
        for marker in markers:
            if line.startswith(marker):
                return True
    return False


def classify(text: str) -> PlaylistKind:
    lines = _lines(text)
    _require_header(lines)
    is_master = _markers_present(lines, _MASTER_MARKERS)
    is_media = _markers_present(lines, _MEDIA_MARKERS)
    if is_master and is_media:
        raise MalformedManifestError("The playlist mixed master and media markers.")
    if is_master:
        return "master"
    if is_media:
        return "media"
    raise MalformedManifestError("The playlist was neither a master nor a media playlist.")


def _resolve(base_url: str, uri: str) -> str:
    from urllib.parse import urljoin

    candidate = uri.strip()
    if not candidate:
        raise MalformedManifestError("The playlist referenced an empty address.")
    return urljoin(base_url, candidate)


def _rewrite_uri_attribute(line: str, base_url: str, kind: HlsResourceKind, rewrite: Rewriter) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        absolute = _resolve(base_url, raw)
        return f'URI="{rewrite(kind, absolute)}"'

    return _URI_ATTRIBUTE.sub(replace, line)


# Any HLS attribute whose name ends in URI addresses an upstream resource
# (URI, SERVER-URI, ...). Detect it at an attribute boundary after blanking
# quoted values, so a leak can never survive a verbatim pass-through.
_QUOTED_VALUE = re.compile(r'"[^"]*"')
_ADDRESS_ATTRIBUTE = re.compile(r"[,:][A-Z0-9-]*URI\s*=")

# A scheme-bearing ("scheme://") or protocol-relative ('="//') attribute value is
# an upstream address that escaped rewriting. Lumina-owned addresses are always
# root-relative single-slash paths, so neither form ever appears legitimately in a
# rewritten manifest. This is name-independent, so it also catches address-bearing
# tags whose attribute name does not end in URI (for example HLS Interstitials'
# X-ASSET-LIST), which the name-based detector above cannot see. Both separators
# accept slash or backslash: WHATWG new URL() (a hls.js code path) lenifies "\" to
# "/" for special schemes, so "https:\/\/host" resolves and must fail closed too.
# Lumina paths contain no ":" and no doubled slash, so this never over-rejects them.
_RESIDUAL_ADDRESS = re.compile(r':[\\/]{2}|=\s*"?\s*[\\/]{2}')


def _reject_if_unhandled_address(line: str) -> str:
    if _ADDRESS_ATTRIBUTE.search(_QUOTED_VALUE.sub('""', line)):
        raise MalformedManifestError(
            "The playlist carried an upstream address on a tag the relay does not rewrite."
        )
    return line


# EXT-X-DATERANGE is timed metadata: a player never needs it to fetch or decode
# media bytes. When one of its attributes carries an address in any form the
# residual scan would catch (Twitch's X-TV-TWITCH-TRIGGER-URL, HLS Interstitials'
# X-ASSET-LIST/X-ASSET-URI, backslash-obfuscated variants), the whole line is
# dropped instead of rejecting the playlist: the address still never reaches the
# browser, and a live broadcast keeps playing. Address-free DATERANGE metadata
# passes through verbatim.
def _daterange_carries_address(line: str) -> bool:
    return bool(
        _ADDRESS_ATTRIBUTE.search(_QUOTED_VALUE.sub('""', line))
        or _RESIDUAL_ADDRESS.search(line)
    )


def _reject_residual_addresses(rendered: str) -> str:
    if _RESIDUAL_ADDRESS.search(rendered):
        raise MalformedManifestError("An upstream address survived manifest rewriting.")
    return rendered


def rewrite_master(text: str, base_url: str, rewrite: Rewriter) -> str:
    lines = _lines(text)
    _require_header(lines)
    if _markers_present(lines, ("#EXTINF", "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE")):
        raise MalformedManifestError("A master playlist must not contain media segments.")
    out: list[str] = []
    for line in lines:
        if not line:
            out.append(line)
            continue
        if line.startswith("#"):
            if line.startswith(_MASTER_PLAYLIST_URI_TAGS):
                out.append(_rewrite_uri_attribute(line, base_url, "media_playlist", rewrite))
            elif line.startswith("#EXT-X-SESSION-KEY"):
                out.append(_rewrite_uri_attribute(line, base_url, "key", rewrite))
            elif line.startswith("#EXT-X-DATERANGE"):
                if not _daterange_carries_address(line):
                    out.append(line)
            else:
                out.append(_reject_if_unhandled_address(line))
            continue
        # A bare line in a master playlist is a variant media-playlist address.
        out.append(rewrite("media_playlist", _resolve(base_url, line)))
    return _reject_residual_addresses("\n".join(out))


def rewrite_media(text: str, base_url: str, rewrite: Rewriter) -> str:
    lines = _lines(text)
    _require_header(lines)
    if _markers_present(lines, ("#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF", "#EXT-X-MEDIA:")):
        raise MalformedManifestError("A media playlist must not nest another playlist.")
    out: list[str] = []
    for line in lines:
        if not line:
            out.append(line)
            continue
        if line.startswith("#"):
            if line.startswith("#EXT-X-MAP"):
                out.append(_rewrite_uri_attribute(line, base_url, "map", rewrite))
            elif line.startswith(("#EXT-X-KEY", "#EXT-X-SESSION-KEY")):
                out.append(_rewrite_uri_attribute(line, base_url, "key", rewrite))
            elif line.startswith(_MEDIA_SEGMENT_URI_TAGS):
                out.append(_rewrite_uri_attribute(line, base_url, "segment", rewrite))
            elif line.startswith("#EXT-X-DATERANGE"):
                if not _daterange_carries_address(line):
                    out.append(line)
            elif line.startswith(_PREFETCH_TAGS):
                # IVS/Twitch low-latency hints name not-yet-published segments by
                # raw address. Players treat them as optional; drop, never relay.
                continue
            else:
                out.append(_reject_if_unhandled_address(line))
            continue
        # A bare line in a media playlist is a media-segment address.
        out.append(rewrite("segment", _resolve(base_url, line)))
    return _reject_residual_addresses("\n".join(out))
