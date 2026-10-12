"""Storage routing: ordered admin rules choose the managed root + folder for finished media.

Rules match the ACTUAL output facts (source, media kind, height), never the
requested quality: "best" that resolved to 1080p routes as 1080p. Within a rule
every condition must hold (AND); values inside one list are alternatives (OR);
an empty list matches anything. A height-bounded rule never matches audio or an
unknown height, so unknown output falls through to an unbounded rule or the
default instead of being guessed as UHD. Lowest priority wins, ties by rule ID.

Publication: media is downloaded into ``<root>/.lumina-staging/<key>/`` and
placed with a no-clobber hard link, copying into the target root's own staging
dir first when it lives on another filesystem, so a published path is never
partial and never replaces another file. Until the Library row commits, every
published file shares an inode with a staging file; that is how recovery tells
an uncommitted publication from unrelated media.
"""
from __future__ import annotations

import errno
import glob
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models import StorageRoot, StorageRuleSet
from app.services.storage_roots import StorageRootError, StorageRootService

Source = Literal["youtube", "twitch", "kick", "soundcloud", "generic"]
MediaKind = Literal["video", "audio", "recording"]
SOURCES = ("youtube", "twitch", "kick", "soundcloud", "generic")
_FIELD = re.compile(r"\{([a-z_]+)\}")
_UNSAFE_PART = re.compile(r"[\\:\x00-\x1f]")
STAGING_DIRNAME = ".lumina-staging"
T = TypeVar("T")


class RuleSetConflict(RuntimeError):
    """The rule set changed since the editor loaded it (409)."""


class StorageUnavailable(RuntimeError):
    """The routed root cannot receive files now; never silently written elsewhere."""


def render_folder(template: str, source: str, media_kind: str, height: int | None) -> str:
    """Relative folder for a template; placeholders are {source}, {kind}, {height}."""
    values = {"source": source, "kind": media_kind, "height": f"{height}p" if height else "unknown"}

    def substitute(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            raise ValueError(f"Unknown folder placeholder {{{match.group(1)}}}.")
        return values[match.group(1)]

    rendered = _FIELD.sub(substitute, template.strip())
    if "{" in rendered or "}" in rendered:
        raise ValueError("Folder templates only support {source}, {kind} and {height}.")
    path = PurePosixPath(rendered)
    if rendered.startswith("/") or any(
        part in {".", ".."} or part.startswith(".") or len(part) > 120 or _UNSAFE_PART.search(part) for part in path.parts
    ):
        raise ValueError("Folder template must be a safe relative folder.")
    return path.as_posix() if rendered else ""


class StorageRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    enabled: bool = True
    priority: int = Field(ge=0, le=1_000_000)
    sources: list[Source] = Field(default_factory=list, max_length=len(SOURCES))
    media_kinds: list[MediaKind] = Field(default_factory=list, max_length=3)
    min_height: int | None = Field(default=None, ge=1, le=20_000)
    max_height: int | None = Field(default=None, ge=1, le=20_000)
    target_root_id: str = Field(min_length=1, max_length=36)
    relative_template: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _check(self) -> "StorageRule":
        if self.min_height and self.max_height and self.min_height > self.max_height:
            raise ValueError("min_height must not exceed max_height.")
        render_folder(self.relative_template, "youtube", "video", 1080)
        return self

    @property
    def height_bounded(self) -> bool:
        return self.min_height is not None or self.max_height is not None

    def matches(self, source: str, media_kind: str, height: int | None) -> bool:
        if not self.enabled or (self.sources and source not in self.sources) or (self.media_kinds and media_kind not in self.media_kinds):
            return False
        if not self.height_bounded:
            return True
        if media_kind == "audio" or height is None:
            return False
        return (self.min_height or 0) <= height <= (self.max_height or height)

    def overlaps(self, other: "StorageRule") -> bool:
        def meet(a: list, b: list) -> bool:
            return not a or not b or bool(set(a) & set(b))

        low, high = max(self.min_height or 0, other.min_height or 0), min(self.max_height or 10**9, other.max_height or 10**9)
        return meet(self.sources, other.sources) and meet(self.media_kinds, other.media_kinds) and low <= high


@dataclass(frozen=True)
class RouteDecision:
    rule_id: str | None  # None: the default rule
    revision: int
    root_id: str
    folder: str
    reason: str

    def snapshot(self) -> dict:
        return {"rule_id": self.rule_id, "revision": self.revision, "root_id": self.root_id, "folder": self.folder}


def decide(rules: list[StorageRule], default_root_id: str, revision: int, source: str, media_kind: str, height: int | None) -> RouteDecision:
    """Pure evaluator: first matching rule by (priority, id), else the default root."""
    source = source if source in SOURCES else "generic"
    for rule in sorted(rules, key=lambda rule: (rule.priority, rule.id)):
        if rule.matches(source, media_kind, height):
            folder = render_folder(rule.relative_template, source, media_kind, height)
            return RouteDecision(rule.id, revision, rule.target_root_id, folder, f"Matched rule {rule.id}.")
    reason = "No rule matched; using the default root."
    if height is None and media_kind != "audio":
        reason += " Output height is unknown, so height rules were skipped."
    return RouteDecision(None, revision, default_root_id, "", reason)


class StorageRoutingService:
    def __init__(self, db: Session):
        self.db = db

    def rule_set(self) -> StorageRuleSet:
        return self.db.get(StorageRuleSet, 1) or StorageRuleSet(id=1, revision=0, default_root_id=None, rules=[])

    def rules(self, rule_set: StorageRuleSet | None = None) -> list[StorageRule]:
        return [StorageRule.model_validate(rule) for rule in (rule_set or self.rule_set()).rules]

    def replace(self, *, expected_revision: int, default_root_id: str | None, rules: list[StorageRule]) -> StorageRuleSet:
        """Validate and store a complete rule set; future work only, existing files never move."""
        current = self.rule_set()
        if current.revision != expected_revision:
            raise RuleSetConflict("Storage rules changed since you loaded them; reload and try again.")
        if len({rule.id for rule in rules}) != len(rules):
            raise StorageRootError("Rule IDs must be unique.")
        for index, rule in enumerate(rules):
            for other in rules[index + 1:]:
                if rule.enabled and other.enabled and rule.priority == other.priority and rule.overlaps(other):
                    raise StorageRootError(f"Rules {rule.id} and {other.id} share a priority and overlap; give one a different priority.")
        for root_id in {rule.target_root_id for rule in rules} | ({default_root_id} if default_root_id else set()):
            root = self.db.get(StorageRoot, root_id)
            if root is None or root.mode != "managed":
                raise StorageRootError("Rules may only target registered managed roots.")
        if self.db.get(StorageRuleSet, 1) is None:
            self.db.add(current)
        current.revision += 1
        current.default_root_id = default_root_id
        current.rules = [rule.model_dump() for rule in rules]
        self.db.flush()
        return current

    def decide(self, source: str, media_kind: str, height: int | None) -> RouteDecision:
        rule_set = self.rule_set()
        default_root_id = rule_set.default_root_id or StorageRootService(self.db).library_root().id
        return decide(self.rules(rule_set), default_root_id, rule_set.revision, source, media_kind, height)

    def writable_root(self, decision: RouteDecision) -> StorageRoot | None:
        """The decision's root when it can receive files now; never a fallback directory."""
        root = self.db.get(StorageRoot, decision.root_id)
        if root is None or root.mode != "managed" or not StorageRootService(self.db).is_online(root):
            return None
        return root


def staging_dir(root: StorageRoot, key: str) -> Path:
    return Path(root.path) / STAGING_DIRNAME / key


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_filesystem(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


# Raised by os.link when the filesystem has no hard-link support at all (some
# SMB/exFAT mounts), as opposed to FileExistsError which just means "retry".
_NO_HARDLINK_ERRNOS = frozenset(
    code for code in (errno.EXDEV, errno.EPERM, getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None))
    if code is not None
)


def _place(staged: Path, root: StorageRoot, target: Path, key: str, journal: Callable[[str], None] | None) -> tuple[Path, Path | None]:
    """Link staged bytes to target (or ``name (n).ext``); returns (published path, partial copy).

    Falls back to a fsynced copy + atomic ``os.replace`` when the target
    filesystem can't hard link at all (EXDEV/EPERM/ENOTSUP).

    The fallback's published file no longer shares an inode with a
    staging file, so job_manager._discard_staging's crash-recovery check can't
    see it on that filesystem; a publication journal entry would be needed to
    recover those crashes too.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    source, partial = staged, None
    if not _same_filesystem(staged, target.parent):
        temp = staging_dir(root, key)
        temp.mkdir(parents=True, exist_ok=True)
        source = partial = temp / f"{uuid.uuid4().hex}.partial"
        shutil.copyfile(staged, partial)
        if partial.stat().st_size != staged.stat().st_size:
            raise OSError("Copying media into its storage root was incomplete.")
    _fsync(source)
    for attempt in range(1, 100):
        candidate = target if attempt == 1 else target.with_name(f"{target.stem} ({attempt}){target.suffix}")
        if os.path.lexists(candidate):
            continue
        if journal is not None:
            journal(candidate.relative_to(root.path).as_posix())
        try:
            os.link(source, candidate)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in _NO_HARDLINK_ERRNOS:
                raise
            copy = partial
            if copy is None:
                copy = staging_dir(root, key) / f"{uuid.uuid4().hex}.partial"
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, copy)
                if copy.stat().st_size != source.stat().st_size:
                    copy.unlink(missing_ok=True)
                    raise OSError("Copying media into its storage root was incomplete.") from exc
                _fsync(copy)
            if os.path.lexists(candidate):
                if copy is not partial:
                    copy.unlink(missing_ok=True)
                continue
            os.replace(copy, candidate)
            partial = None
        _fsync(candidate.parent)
        return candidate, partial
    raise FileExistsError("Too many files with this name already exist in the storage root.")


# Text playlists name other files/URLs; ffmpeg would follow them if one were published as media.
PLAYLIST_SUFFIXES = frozenset({".m3u", ".m3u8", ".pls", ".ffconcat", ".txt"})


def publish_file(
    db: Session,
    staged: Path,
    *,
    source: str,
    media_kind: str,
    height: int | None,
    relative: str,
    key: str,
    record: Callable[[Path, RouteDecision], T],
    journal: Callable[[str, str], None] | None = None,
) -> T:
    """Route finished media by its actual facts, place it durably, then let ``record`` commit the Library row.

    ``record`` runs after the file is in place; if it raises, the placed file is
    removed again and the staged bytes stay where they were.
    """
    if staged.suffix.lower() in PLAYLIST_SUFFIXES:
        raise ValueError("A playlist file is not media and is never published.")
    routing = StorageRoutingService(db)
    decision = routing.decide(source, media_kind, height)
    root = routing.writable_root(decision)
    if root is None:
        raise StorageUnavailable("The storage location for this media is offline or unavailable.")
    target = Path(root.path) / decision.folder / relative
    if not target.resolve().is_relative_to(Path(root.path).resolve()):
        raise ValueError("Media must stay inside its storage root.")
    note = (lambda path: journal(root.id, path)) if journal else None
    final, partial = _place(staged, root, target, key, note)
    try:
        result = record(final, decision)
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    # Sidecars (subtitles) follow best-effort; they are not Library artifacts.
    for sidecar in staged.parent.glob(glob.escape(staged.stem) + ".*"):
        if sidecar != staged and sidecar.is_file() and not sidecar.is_symlink():
            try:
                _, copy = _place(sidecar, root, final.with_name(final.stem + sidecar.name[len(staged.stem):]), key, None)
                if copy is not None:
                    copy.unlink()
            except OSError:
                pass
    return result
