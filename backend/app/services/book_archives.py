"""Bounded, dependency-free readers for EPUB and CBZ books.

The files in a book library are untrusted archives.  This module never extracts
them and returns only plain metadata and a small safe markup tree for readers.
"""
from __future__ import annotations

import math
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET


MAX_MEMBERS = 10_000
MAX_PATH_DEPTH = 32
MAX_CHAPTER_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_MARKUP_NODES = 50_000
MAX_MARKUP_DEPTH = 128
MAX_DESCRIPTION_CHARS = 10_000
IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
UNSAFE_TAGS = {"head", "script", "style", "iframe", "object", "embed", "form", "input", "button", "svg", "math", "template"}
ALLOWED_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "em", "strong", "span", "br", "hr", "img", "a"}
_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
_NATURAL = re.compile(r"(\d+)")


class BookArchiveError(ValueError):
    """The book is malformed, unsafe, or cannot be read as the requested format."""


@dataclass
class _Markup:
    tag: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list[_Markup] = field(default_factory=list)


def _clean(value: str | None, limit: int = 500) -> str | None:
    value = " ".join((value or "").split())
    return value[:limit] if value else None


def _raw_text(element: ET.Element | None) -> str:
    return "".join(element.itertext()) if element is not None else ""


def _element_markup(element: ET.Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    tag = _local_name(element.tag)
    attrs = "".join(f' {_local_name(key)}="{escape(value, quote=True)}"' for key, value in element.attrib.items())
    inner = element.text or ""
    for child in element:
        inner += _element_markup(child)
        inner += child.tail or ""
    return f"<{tag}{attrs}>{inner}</{tag}>"


def _metadata_markup(element: ET.Element | None) -> str:
    if element is None:
        return ""
    if not list(element):
        return element.text or ""
    value = element.text or ""
    for child in element:
        value += _element_markup(child)
        value += child.tail or ""
    return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _safe_name(name: str) -> str:
    """Return a normal archive member name, rejecting paths that could escape an extraction root."""
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[a-zA-Z]:", name):
        raise BookArchiveError("Book archive has an unsafe member name")
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or len(parts) > MAX_PATH_DEPTH:
        raise BookArchiveError("Book archive has an unsafe member name")
    return "/".join(parts)


def _resolve_member(base: str, href: str) -> str | None:
    """Resolve an archive-relative href; remote and escaping references are never valid."""
    href = unquote(href.strip().split("#", 1)[0].split("?", 1)[0])
    if not href or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("//") or "\\" in href:
        return None
    try:
        return _safe_name(posixpath.normpath(posixpath.join(posixpath.dirname(base), href)))
    except BookArchiveError:
        return None


def _xml(data: bytes, label: str) -> ET.Element:
    # ElementTree does not fetch external entities, but rejecting DTD/entity declarations also prevents expansion work.
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise BookArchiveError(f"Book {label} contains unsupported XML entities")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise BookArchiveError(f"Book {label} is invalid") from exc


class _Archive:
    def __init__(self, path: Path):
        try:
            self.zip = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise BookArchiveError("Book archive is invalid") from exc
        try:
            infos = self.zip.infolist()
            if len(infos) > MAX_MEMBERS:
                raise BookArchiveError("Book archive has too many members")
            total = 0
            self.info: dict[str, zipfile.ZipInfo] = {}
            for item in infos:
                _safe_name(item.filename.rstrip("/"))
                if item.is_dir():
                    continue
                name = _safe_name(item.filename)
                if item.flag_bits & 0x1:
                    raise BookArchiveError("Encrypted or DRM-protected books are unsupported")
                if item.file_size > MAX_IMAGE_BYTES:
                    raise BookArchiveError("Book archive has an oversized member")
                total += item.file_size
                if total > MAX_TOTAL_UNCOMPRESSED:
                    raise BookArchiveError("Book archive is too large")
                if name in self.info:
                    raise BookArchiveError("Book archive has duplicate members")
                self.info[name] = item
        except BookArchiveError:
            self.zip.close()
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            self.zip.close()
            raise BookArchiveError("Book archive is invalid") from exc

    def close(self) -> None:
        self.zip.close()

    def read(self, name: str, limit: int) -> bytes:
        try:
            item = self.info[name]
        except KeyError as exc:
            raise BookArchiveError("Book archive is missing a required member") from exc
        if item.file_size > limit:
            raise BookArchiveError("Book member exceeds the allowed size")
        try:
            with self.zip.open(item) as handle:
                data = handle.read(limit + 1)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise BookArchiveError("Book archive is invalid") from exc
        if len(data) > limit:
            raise BookArchiveError("Book member exceeds the allowed size")
        return data


def _text(element: ET.Element | None) -> str | None:
    return _clean(_raw_text(element))


def _unique(values: list[str], limit: int = 20) -> list[str]:
    found: dict[str, str] = {}
    for value in values:
        if cleaned := _clean(value):
            found.setdefault(cleaned.casefold(), cleaned)
    return list(found.values())[:limit]


def _metadata(opf: ET.Element) -> tuple[dict[str, Any], dict[str, tuple[str, str]], list[str], str]:
    metadata = next((node for node in opf if _local_name(node.tag) == "metadata"), None)
    manifest_node = next((node for node in opf if _local_name(node.tag) == "manifest"), None)
    spine = next((node for node in opf if _local_name(node.tag) == "spine"), None)
    if metadata is None or manifest_node is None or spine is None:
        raise BookArchiveError("EPUB package is missing metadata, manifest, or spine")
    fields: dict[str, list[str]] = {"title": [], "creator": [], "description": [], "language": [], "subject": []}
    meta: dict[str, str] = {}
    for node in metadata:
        name = _local_name(node.tag)
        if name in fields:
            value = clean_synopsis(_metadata_markup(node)) if name == "description" else _text(node)
            if value:
                fields[name].append(value)
        elif name == "meta":
            key = (node.get("name") or node.get("property") or "").casefold()
            value = _clean(node.get("content") or node.text)
            if key and value:
                meta[key] = value
    manifest: dict[str, tuple[str, str]] = {}
    for item in manifest_node:
        if _local_name(item.tag) != "item":
            continue
        identifier, href = item.get("id"), item.get("href")
        if not identifier or not href:
            continue
        manifest[identifier] = (href, item.get("media-type", ""))
    order = [node.get("idref", "") for node in spine if _local_name(node.tag) == "itemref" and node.get("idref")]
    if not order:
        raise BookArchiveError("EPUB package has no readable spine")
    return {
        "title": fields["title"][0] if fields["title"] else None,
        "author": fields["creator"][0] if fields["creator"] else None,
        "description": fields["description"][0] if fields["description"] else None,
        "language": fields["language"][0] if fields["language"] else None,
        "genres": _unique(fields["subject"]),
        "series_name": meta.get("calibre:series"),
        "volume_number": _volume(meta.get("calibre:series_index")),
        "reading_direction": "rtl" if spine.get("page-progression-direction", "").casefold() == "rtl" else "ltr",
    }, manifest, order, meta.get("cover", "")


def _volume(value: str | None) -> float | None:
    match = _NUMBER.match(value or "")
    if not match:
        return None
    number = float(match.group(1))
    return number if math.isfinite(number) else None


def _epub(archive: _Archive, path: Path) -> dict[str, Any]:
    container = _xml(archive.read("META-INF/container.xml", MAX_METADATA_BYTES), "container")
    rootfile = next((node.get("full-path") for node in container.iter() if _local_name(node.tag) == "rootfile" and node.get("full-path")), None)
    if not rootfile:
        raise BookArchiveError("EPUB container has no package document")
    opf_name = _safe_name(rootfile)
    opf = _xml(archive.read(opf_name, MAX_METADATA_BYTES), "package document")
    result, manifest, spine, cover_id = _metadata(opf)
    resolved: dict[str, tuple[str, str]] = {}
    for ident, (href, mime) in manifest.items():
        member = _resolve_member(opf_name, href)
        if member and member in archive.info:
            resolved[ident] = (member, mime)
    chapters: list[dict[str, str]] = []
    for index, ident in enumerate(spine, 1):
        if ident not in resolved:
            raise BookArchiveError("EPUB spine references a missing document")
        member, _mime = resolved[ident]
        if archive.info[member].file_size > MAX_CHAPTER_BYTES:
            raise BookArchiveError("EPUB chapter exceeds the allowed size")
        chapters.append({"label": f"Chapter {index}", "member": member})
    labels = _toc_labels(archive, resolved, opf)
    for chapter in chapters:
        chapter["label"] = labels.get(chapter["member"], chapter["label"])
    cover_member = resolved.get(cover_id, (None, ""))[0]
    if not cover_member:
        manifest_node = next((node for node in opf if _local_name(node.tag) == "manifest"), None)
        for item in manifest_node or []:
            if "cover-image" in item.get("properties", "").split() and item.get("id") in resolved:
                cover_member = resolved[item.get("id")][0]
                break
    if cover_member and _image_type(cover_member) is None:
        cover_member = None
    return {
        "format": "epub", "title": result["title"] or _clean(path.stem), "author": result["author"],
        "description": result["description"], "language": result["language"], "genres": result["genres"],
        "series_name": result["series_name"], "volume_number": result["volume_number"], "cover_member": cover_member,
        "chapters": chapters, "pages": [], "reading_direction": result["reading_direction"],
    }


def _toc_labels(archive: _Archive, manifest: dict[str, tuple[str, str]], opf: ET.Element) -> dict[str, str]:
    manifest_node = next((node for node in opf if _local_name(node.tag) == "manifest"), None)
    toc_id = next((node.get("toc") for node in opf.iter() if _local_name(node.tag) == "spine"), None)
    manifest_items = manifest_node if manifest_node is not None else []
    nav_id = next((node.get("id") for node in manifest_items if "nav" in node.get("properties", "").split()), toc_id)
    if not nav_id or nav_id not in manifest:
        return {}
    nav_name, _mime = manifest[nav_id]
    if nav_name not in archive.info:
        return {}
    try:
        root = _xml(archive.read(nav_name, MAX_CHAPTER_BYTES), "navigation document")
    except BookArchiveError:
        return {}
    labels: dict[str, str] = {}
    for node in root.iter():
        if _local_name(node.tag) == "a" and node.get("href") and (label := _text(node)):
            member = _resolve_member(nav_name, node.get("href"))
            if member:
                labels.setdefault(member, label)
    # NCX navPoints pair content with its navLabel without relying on parent pointers.
    for point in root.iter():
        if _local_name(point.tag) != "navpoint":
            continue
        content = next((child.get("src") for child in point.iter() if _local_name(child.tag) == "content"), None)
        label = next((_text(child) for child in point.iter() if _local_name(child.tag) == "text"), None)
        if content and label and (member := _resolve_member(nav_name, content)):
            labels.setdefault(member, label)
    return labels


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in _NATURAL.split(name)]


def _cbz(archive: _Archive, path: Path) -> dict[str, Any]:
    pages = sorted((name for name in archive.info if _image_type(name)), key=_natural_key)
    if not pages:
        raise BookArchiveError("CBZ archive has no readable image pages")
    if len(pages) > 2500:
        raise BookArchiveError("CBZ archive has too many pages")
    data: dict[str, Any] = {}
    comic = next((name for name in archive.info if name.casefold() == "comicinfo.xml"), None)
    if comic:
        try:
            root = _xml(archive.read(comic, MAX_METADATA_BYTES), "ComicInfo")
            data = {}
            for node in root:
                name = _local_name(node.tag)
                value = clean_synopsis(_metadata_markup(node)) if name == "summary" else _text(node)
                if value:
                    data[name] = value
        except BookArchiveError:
            data = {}
    genres = _unique(re.split(r"[;,/]", ";".join(filter(None, (data.get("genres"), data.get("genre"), data.get("tags"))))))
    direction = (data.get("manga", "") + " " + data.get("readingdirection", "")).casefold()
    return {
        "format": "cbz", "title": data.get("title") or _clean(path.stem), "author": data.get("writer"),
        "description": data.get("summary"), "language": data.get("languageiso") or data.get("language"), "genres": genres,
        "series_name": data.get("series"), "volume_number": _volume(data.get("number")), "cover_member": pages[0],
        "chapters": [], "pages": pages, "reading_direction": "rtl" if ("yes" in direction or "rtl" in direction or "righttoleft" in direction) else "ltr",
    }


def _image_type(member: str) -> str | None:
    return IMAGE_TYPES.get(PurePosixPath(member).suffix.casefold())


def _is_image(data: bytes, mime: str) -> bool:
    return {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }.get(mime, False)


def inspect_book(path: Path, format: str, *, filename: str | None = None) -> dict[str, Any]:
    """Inspect an EPUB or CBZ without extracting it; all returned values are JSON-serializable."""
    if format in {"cbr", "pdf"}:
        from app.services.book_native import native_book
        manifest = native_book(path, format)
        if filename and manifest.get("title") == _clean(path.stem):
            manifest["title"] = _clean(Path(filename).stem)
        return _filename_series(manifest, filename or path.name)
    if format not in {"epub", "cbz"}:
        raise BookArchiveError("Unsupported book format")
    archive = _Archive(path)
    try:
        metadata_name = Path(filename) if filename is not None else path
        manifest = _epub(archive, metadata_name) if format == "epub" else _cbz(archive, metadata_name)
        return _filename_series(manifest, metadata_name.name)
    finally:
        archive.close()


def _filename_series(manifest: dict[str, Any], filename: str) -> dict[str, Any]:
    """Fill missing comic metadata only for explicit volume or numbered/year filenames."""
    if manifest["format"] not in {"cbz", "cbr"} or manifest.get("series_name"):
        return manifest
    stem = Path(filename).stem
    match = re.match(r"^(.+?)\s+v(?:ol(?:ume)?\.?)?\s*(\d+(?:\.\d+)?)(?:\s|$)", stem, re.I)
    if match is None:
        match = re.match(r"^(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+\((?:\d{4}|of\s+\d+)\)", stem, re.I)
    if match:
        manifest["series_name"] = _clean(match[1])
        if manifest.get("volume_number") is None:
            manifest["volume_number"] = _volume(match[2])
    return manifest


def read_resource(path: Path, member: str) -> tuple[bytes, str]:
    """Read one bounded raster image resource. SVG and all non-image resources stay private."""
    try:
        name = _safe_name(member)
    except BookArchiveError as exc:
        raise FileNotFoundError("Book resource is unavailable") from exc
    mime = _image_type(name)
    if not mime:
        raise FileNotFoundError("Book resource is unavailable")
    if path.suffix.lower() in {".cbr", ".pdf"}:
        from app.services.book_native import native_book
        try:
            return native_book(path, path.suffix.lower()[1:], name), mime
        except BookArchiveError as exc:
            raise FileNotFoundError("Book resource is unavailable") from exc
    archive = _Archive(path)
    try:
        if name not in archive.info:
            raise FileNotFoundError("Book resource is unavailable")
        try:
            data = archive.read(name, MAX_IMAGE_BYTES)
        except BookArchiveError as exc:
            raise FileNotFoundError("Book resource is unavailable") from exc
        if not _is_image(data, mime):
            raise FileNotFoundError("Book resource is unavailable")
        return data, mime
    finally:
        archive.close()


class _LooseHtml(HTMLParser):
    """A bounded, deliberately tiny DOM for non-XHTML EPUB chapters."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Markup("root")
        self.stack = [self.root]
        self.count = 1

    def _add(self, node: _Markup) -> None:
        self.count += 1
        if self.count > MAX_MARKUP_NODES:
            raise BookArchiveError("EPUB chapter has too much markup")
        self.stack[-1].children.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.stack) > MAX_MARKUP_DEPTH:
            raise BookArchiveError("EPUB chapter markup is too deeply nested")
        node = _Markup(tag.casefold(), {key.casefold(): value or "" for key, value in attrs})
        self._add(node)
        if tag.casefold() not in {"br", "hr", "img", "meta", "link", "input"}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in {"br", "hr", "img", "meta", "link", "input"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._add(_Markup(text=data))


def _from_element(element: ET.Element, depth: int = 0, count: list[int] | None = None) -> _Markup:
    count = count if count is not None else [0]
    count[0] += 1
    if depth > MAX_MARKUP_DEPTH or count[0] > MAX_MARKUP_NODES:
        raise BookArchiveError("EPUB chapter has too much nested markup")
    node = _Markup(_local_name(element.tag), { _local_name(key): value for key, value in element.attrib.items() }, element.text or "")
    for child in element:
        if isinstance(child.tag, str):
            node.children.append(_from_element(child, depth + 1, count))
        if child.tail:
            node.children.append(_Markup(text=child.tail))
    return node


def _hidden(node: _Markup) -> bool:
    style = re.sub(r"\s+", "", node.attrs.get("style", "")).casefold()
    return "hidden" in node.attrs or node.attrs.get("aria-hidden", "").casefold() == "true" or "display:none" in style or "visibility:hidden" in style


_SYNOPSIS_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "figcaption", "figure", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


def _synopsis_parts(node: _Markup) -> list[str]:
    if node.tag is None:
        return [node.text] if node.text else []
    tag = node.tag.casefold()
    if tag in UNSAFE_TAGS or _hidden(node):
        return []
    if tag == "br":
        return ["\n"]
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node.children:
        parts.extend(_synopsis_parts(child))
    if tag in _SYNOPSIS_BLOCK_TAGS:
        return ["\n\n", *parts, "\n\n"]
    return parts


def clean_synopsis(value: object, limit: int = MAX_DESCRIPTION_CHARS) -> str | None:
    """Return bounded plain synopsis text without allowing markup to cross the archive boundary."""
    if not isinstance(value, str):
        return None
    parser = _LooseHtml()
    try:
        parser.feed(value)
        parser.close()
    except BookArchiveError:
        return None
    raw = "".join(part for node in parser.root.children for part in _synopsis_parts(node))
    paragraphs = [
        "\n".join(" ".join(line.split()) for line in part.splitlines() if line.strip())
        for part in re.split(r"\n{2,}", raw)
    ]
    cleaned = "\n\n".join(part for part in paragraphs if part).strip()
    return cleaned[:limit] or None


def _fragment(href: str) -> str | None:
    return _clean(unquote(href.split("#", 1)[1]), 300) if "#" in href else None


def _render(node: _Markup, chapter_member: str, chapter_index: int, positions: dict[str, int], archive: _Archive) -> list[dict[str, Any]]:
    if node.tag is None:
        text = re.sub(r"\s+", " ", node.text)
        return [{"tag": "span", "text": text}] if text else []
    tag = node.tag.casefold()
    if tag in UNSAFE_TAGS or _hidden(node):
        return []
    children: list[dict[str, Any]] = []
    if text := re.sub(r"\s+", " ", node.text):
        children.append({"tag": "span", "text": text})
    for child in node.children:
        children.extend(_render(child, chapter_member, chapter_index, positions, archive))
    identifier = _clean(node.attrs.get("id") or (node.attrs.get("name") if tag == "a" else None), 300)
    destination = {"id": identifier} if identifier else {}
    if tag in {"html", "body", "root", "div", "section", "article", "main", "figure", "header", "footer"} or tag not in ALLOWED_TAGS:
        return ([{"tag": "span", **destination}] if destination else []) + children
    if tag in {"br", "hr"}:
        return [{"tag": tag, **destination}]
    if tag == "img":
        member = _resolve_member(chapter_member, node.attrs.get("src", ""))
        if not member or member not in archive.info or _image_type(member) is None or archive.info[member].file_size > MAX_IMAGE_BYTES:
            return []
        result: dict[str, Any] = {"tag": "img", "member": member, **destination}
        if alt := _clean(node.attrs.get("alt")):
            result["alt"] = alt
        return [result]
    if tag == "a":
        href = node.attrs.get("href", "")
        target = _resolve_member(chapter_member, href) if href.split("#", 1)[0] else chapter_member
        if not target or target not in positions:
            return ([{"tag": "span", **destination}] if destination else []) + children
        result = {"tag": "a", "children": children, **destination}
        if target != chapter_member:
            result["chapter"] = positions[target]
        if anchor := _fragment(href):
            result["anchor"] = anchor
        return [result]
    result: dict[str, Any] = {"tag": tag, **destination}
    if children:
        result["children"] = children
    return [result]


def chapter_nodes(path: Path, manifest: dict[str, Any], chapter_index: int) -> list[dict[str, Any]]:
    """Return one EPUB chapter as inert structured markup, never raw HTML or CSS."""
    if manifest.get("format") != "epub" or not isinstance(manifest.get("chapters"), list):
        raise BookArchiveError("Book has no EPUB chapters")
    chapters = manifest["chapters"]
    if not 0 <= chapter_index < len(chapters) or not isinstance(chapters[chapter_index], dict):
        raise BookArchiveError("Chapter index is invalid")
    member = chapters[chapter_index].get("member")
    if not isinstance(member, str):
        raise BookArchiveError("Chapter is invalid")
    archive = _Archive(path)
    try:
        data = archive.read(member, MAX_CHAPTER_BYTES)
        try:
            root = _from_element(_xml(data, "chapter"))
        except BookArchiveError:
            parser = _LooseHtml()
            try:
                parser.feed(data.decode("utf-8", "replace"))
                parser.close()
            except BookArchiveError:
                raise
            root = parser.root
        body = next((child for child in root.children if child.tag == "body"), root)
        positions = {chapter.get("member"): index for index, chapter in enumerate(chapters) if isinstance(chapter, dict) and isinstance(chapter.get("member"), str)}
        nodes = _render(body, member, chapter_index, positions, archive)
        if len(nodes) > MAX_MARKUP_NODES:
            raise BookArchiveError("EPUB chapter has too much markup")
        return nodes
    finally:
        archive.close()
