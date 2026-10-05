"""Read-only import of an external storage root.

A scan walks the root depth-first in sorted order, in bounded batches. Each
batch commits its index rows together with the cursor, so a cancelled or
interrupted run resumes exactly where it stopped. Filesystem work happens
before the batch's write transaction; the external root is only ever read,
and symlinks are never followed.

Rescans are incremental: a file whose (size, mtime, inode) fingerprint is
unchanged is not re-hashed; a new path whose size and inode (or 64 KiB hash
prefix) match exactly one artifact whose old path is gone is the same file
renamed, so its library entries, grants and history keep their identity.
Only a run with complete coverage on a still-online root may mark unseen
files missing, and never by deleting rows.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
import os
import stat
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Literal, TypedDict

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import ImportEntry, ImportRun, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, utcnow
from app.persistence import queue_after_commit, write_transaction
from app.services.library import EXTERNAL_LIBRARY_ORIGIN, LibraryService
from app.services.library_search import LibrarySearchService, index_title, title_search_fields
from app.services.audio_tags import AudioTags, read_tags
from app.services.cast_photos import save_people, split_crew, split_people
from app.services.local_metadata import AUDIO_EXTENSIONS, describe, read_bounded
from app.services.media_titles import apply_field, refresh_added_at, scanned_parent
from app.services.categories import refresh_category
from app.services.storage_roots import ONLINE_STATES, StorageRootService
from app.services.transcripts import MAX_TRACK_BYTES, TEXT_TRACK_EXTS, TranscriptService, parse_caption

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_DEPTH = 24
HASH_PREFIX_BYTES = 64 * 1024

# Post-import work other parts register at startup (probe warming, captions bulk-queue, embedding
# backfill). Each gets the finished run id, must return quickly and never fails the scan.
after_import_hooks: list[Callable[[str], None]] = []


def _after_import(run_id: str) -> None:
    for hook in after_import_hooks:
        try:
            hook(run_id)
        except Exception:  # noqa: BLE001 - one broken consumer never blocks the others or the scan
            logger.exception("Post-import hook %s failed", getattr(hook, "__name__", hook))


def _wake_renditions(title_ids: list[str]) -> None:
    """Art a scan added or changed is prepared first."""
    from app.services.renditions import renditions  # renditions imports this module for its after-import hook

    renditions.wake(priority_titles=title_ids)


# Failed/skipped entries are kept up to this cap per run; counters stay exact.
MAX_ENTRIES_PER_RUN = 1000
MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".ts", ".wmv", ".mpg", ".mpeg",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus",
})
# Jellyfin-layout folders that never hold library media (".actors" is already hidden).
SKIP_DIRS = frozenset({"backdrops", "theme-music"})
ACTIVE_STATES = ("running", "cancel_requested")
RESUMABLE_STATES = ("cancelled", "failed", "interrupted")
ORIGIN = EXTERNAL_LIBRARY_ORIGIN
# Titles keyed across roots: boxsets by set name, music by album artist and album.
GLOBAL_KEY_TYPES = ("boxset", "album", "artist")
# Music "Recently added": an album arrives with its newest track file, an artist with
# its newest album. The first dating uses every track; afterwards only tracks first indexed in this run can make the
# album later, so a track replaced in place never does. Same file-time guard as db._FILE_TIME: 0 < mtime ≤ now + 1 day.
_TRACK_TIME = (
    "strftime('%Y-%m-%d %H:%M:%S', a.mtime_ns / 1000000000, 'unixepoch') || printf('.%06d', a.mtime_ns / 1000 % 1000000)"
)
MUSIC_ADDED_AT_SQL = (
    "UPDATE media_titles SET added_at = (SELECT CASE WHEN media_titles.added_at IS NULL THEN max(" + _TRACK_TIME + ")"
    " ELSE max(media_titles.added_at, coalesce(max(CASE WHEN i.created_at >= :since THEN " + _TRACK_TIME + " END), media_titles.added_at)) END"
    " FROM library_items i JOIN library_item_artifacts l ON l.library_item_id = i.id JOIN media_artifacts a ON a.id = l.artifact_id"
    " WHERE i.title_id = media_titles.id AND i.extra_type IS NULL AND a.mtime_ns > 0"
    " AND a.mtime_ns <= CAST(strftime('%s', 'now') AS INTEGER) * 1000000000 + 86400000000000)"
    " WHERE type = 'album' AND id IN :ids",
    "UPDATE media_titles SET added_at = (SELECT max(c.added_at) FROM media_titles c WHERE c.parent_id = media_titles.id)"
    " WHERE type = 'artist' AND id IN :ids",
)

# Fixed household-scale guard; make it a per-root setting if bulk deletions become routine.
MASS_MISSING_MIN = 25
MASS_MISSING_SHARE = 0.2
# A scoped run lstat()s each unseen candidate; past this it is held for an admin without any lstat.
MAX_GONE_CHECKS = 5000
GONE_CHUNK = 500
HARMLESS_SKIPS = frozenset({"symlink", "too_deep"})  # skipped, not failed: coverage stays complete


def mass_missing(candidates: int, available: int) -> bool:
    """More than 20% (and more than 25) of a root's available files vanishing looks like a half-mounted share."""
    return candidates > MASS_MISSING_MIN and candidates > available * MASS_MISSING_SHARE


# One scan step at a time per root: a step hung on one root's dead share (a hard NFS mount) holds that root only.
# Steps of different roots may overlap; their writes still serialize through write_transaction.
_step_locks: dict[str, threading.Lock] = {}
CONFIRM_WAIT_S = 10  # confirm runs in a request thread: past this wait for the root's step it answers "busy"
stop_event = threading.Event()


def _step_lock(root_id: str) -> threading.Lock:
    return _step_locks.setdefault(root_id, threading.Lock())  # setdefault is atomic: one lock per root, ever

Found = tuple[tuple[str, ...], "os.DirEntry | None", "str | None", frozenset[str]]


class ImportRunError(RuntimeError):
    """Rejected import action (message is safe to show an admin)."""


IGNORE_MARKER = ".ignore"  # a folder holding this file is skipped with everything beneath it


def is_media_name(lower: str) -> bool:
    """walk()'s file-name filter (lower-case name), shared with the watcher so the two cannot drift."""
    path = Path(lower)
    return (
        not lower.startswith(".") and path.suffix in MEDIA_EXTENSIONS
        and not lower.startswith("theme.") and not path.stem.endswith("-sample")
    )


def is_walked_dir(name: str) -> bool:
    """walk()'s folder-name filter, shared with the watcher: hidden and backdrops/theme-music folders are skipped."""
    return not name.startswith(".") and name.lower() not in SKIP_DIRS


def walk(root: Path, after: tuple[str, ...], parts: tuple[str, ...] = (), *, recurse: bool = True) -> Iterator[Found]:
    """Yield (parts, entry, error, sibling files) for media strictly after ``after``, in sorted DFS order.

    Hidden names, folders holding .ignore, backdrops/theme-music folders, theme.* and
    *-sample files and unsupported extensions are skipped silently; symlinks,
    unreadable and too-deep directories are yielded with an error. ``recurse=False`` lists one folder's files only.
    """
    if len(parts) > MAX_DEPTH:
        yield parts, None, "too_deep", frozenset()
        return
    try:
        with os.scandir(root.joinpath(*parts)) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
    except PermissionError:
        yield parts, None, "permission_denied", frozenset()
        return
    except OSError:
        yield parts, None, "unreadable", frozenset()
        return
    siblings = frozenset(entry.name for entry in entries if entry.is_file(follow_symlinks=False))
    if any(entry.name == IGNORE_MARKER for entry in entries):
        return  # checked before the hidden-name skip: the marker is itself a hidden file
    for entry in entries:
        path = (*parts, entry.name)
        lower = entry.name.lower()
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            if path > after:
                yield path, None, "symlink", siblings
        elif entry.is_dir(follow_symlinks=False):
            # A directory wholly before the cursor, or one that itself failed last time, is done.
            if recurse and is_walked_dir(entry.name) and path != after and path >= after[: len(path)]:
                yield from walk(root, after, path)
        elif entry.is_file(follow_symlinks=False) and is_media_name(lower):
            if path > after:
                yield path, entry, None, siblings


# Only the entry folder itself is checked for .ignore; an .ignore on an ancestor of a scope entry is not
# honoured (a full walk() hides it). The watcher baseline honours it, so the gap is narrow; walk ancestors if it bites.
def _walk_entry(root: Path, after: tuple[str, ...], parts: tuple[str, ...], deep: bool) -> Iterator[Found]:
    if after and (parts == after or parts < after[: len(parts)]):
        return  # every path under it sorts before the cursor, or its own error was already reported
    try:
        mode = os.lstat(root.joinpath(*parts)).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return  # removed or renamed: its files fall to the missing rule
    except OSError:
        yield parts, None, "unreadable", frozenset()
        return
    if stat.S_ISLNK(mode):
        yield parts, None, "symlink", frozenset()
    elif stat.S_ISDIR(mode):
        yield from walk(root, after, parts, recurse=deep)


def walk_scope(root: Path, after: tuple[str, ...], scope: list[ScopeEntry]) -> Iterator[Found]:
    """``walk()`` over a normalised scope, still in global sorted order.

    Merged, not concatenated: a shallow folder and a deep folder beneath it interleave in sorted order
    (``X/Season 2/…`` sorts before ``X/a.mkv``), and the resume cursor is a single path.
    The first batch lists each scope entry once (<= 200); list lazily per entry if 200-entry scopes get slow.
    """
    entries = [_walk_entry(root, after, tuple(e["dir"].split("/")) if e["dir"] else (), e["deep"]) for e in scope]
    yield from heapq.merge(*entries, key=lambda found: found[0])


def hash_prefix(path: Path) -> str | None:
    try:
        return hashlib.sha256(read_bounded(path, HASH_PREFIX_BYTES)).hexdigest()
    except OSError:
        return None


def _fingerprint(status: os.stat_result) -> tuple[int, int, int]:
    return status.st_size, status.st_mtime_ns, status.st_ino % (1 << 63)  # fits a signed SQLite integer


def _gone(path: Path) -> bool:
    """Confirmed absent; an unreadable path (e.g. permission denied) is not proof of a move."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


class ScopeEntry(TypedDict):
    dir: str  # root-relative POSIX path; "" = the root's top level
    deep: bool


MAX_SCOPE_ENTRIES = 200


def _scope_filter(scope: list[ScopeEntry]):  # noqa: ANN202
    """Artifacts inside a normalised scope; an exact, case-sensitive prefix (substr, not LIKE: no wildcards, no case folding)."""
    col = MediaArtifact.relative_path
    clauses = []
    for entry in scope:
        path = entry["dir"]
        n = len(path) + 1
        prefix = func.substr(col, 1, n) == path + "/" if path else None
        if entry["deep"]:
            clauses.append(prefix)  # a deep "" never reaches here: normalize_scope makes it a full run
        else:
            rest = func.instr(func.substr(col, n + 1 if path else 1), "/") == 0
            clauses.append(and_(prefix, rest) if path else rest)
    return or_(*clauses)


def _scope_parts(entry: object) -> tuple[tuple[str, ...], bool]:
    if not isinstance(entry, dict) or not isinstance(entry.get("dir"), str) or not isinstance(entry.get("deep"), bool):
        raise ImportRunError("A scope entry needs a folder path and a deep flag.")
    path = entry["dir"]
    parts = tuple(path.split("/")) if path else ()
    if len(parts) > MAX_DEPTH or any(
        not part or part in {".", ".."} or part.startswith(".") or "\\" in part or part.lower() in SKIP_DIRS
        or not part.isprintable()  # a NUL or control character would make os.lstat raise ValueError
        for part in parts
    ):
        raise ImportRunError(f"Not a scannable folder path: {path!r}")
    return parts, entry["deep"]


def normalize_scope(entries: list[ScopeEntry]) -> list[ScopeEntry] | None:
    """Validate, collapse ancestors and sort a scope; None = the whole root (a full run)."""
    if not isinstance(entries, list) or not entries:
        raise ImportRunError("A scoped import needs at least one folder.")
    wanted: dict[tuple[str, ...], bool] = {}
    for entry in entries:
        parts, deep = _scope_parts(entry)
        wanted[parts] = wanted.get(parts, False) or deep
    deep_dirs = [parts for parts, deep in wanted.items() if deep]
    if () in deep_dirs:
        return None
    kept = sorted(
        parts for parts in wanted
        if not any(len(d) < len(parts) and parts[: len(d)] == d for d in deep_dirs)
    )
    if len(kept) > MAX_SCOPE_ENTRIES:
        raise ImportRunError(f"A scoped import covers at most {MAX_SCOPE_ENTRIES} folders.")
    return [{"dir": "/".join(parts), "deep": wanted[parts]} for parts in kept]


class LibraryImportService:
    def __init__(self, db: Session):
        self.db = db

    def start(
        self, root_id: str, user_id: str, visibility: str = "shared", *,
        trigger: Literal["manual", "scheduled", "watch"] = "manual", scope: list[dict] | None = None,  # raw; normalize_scope validates
    ) -> ImportRun:
        root = self.db.get(StorageRoot, root_id)
        if root is None:
            raise LookupError("Storage root not found")
        if root.mode != "external":
            raise ImportRunError("Only external roots are imported; managed roots are Lumina's own library.")
        if trigger not in {"manual", "scheduled", "watch"}:
            raise ImportRunError(f"Unknown import trigger: {trigger!r}")
        normalized = normalize_scope(scope) if scope is not None else None  # before db.add: a bad scope leaves no run
        run = ImportRun(
            id=str(uuid.uuid4()),
            root_id=root_id,
            user_id=user_id,
            visibility=visibility if visibility in {"private", "shared"} else "private",
            state="running",
            cursor="[]",
            counters={},
            trigger=trigger,
        )
        if normalized is not None:
            run.scope = normalized  # never assigned for a full run: the column stays SQL NULL
        with write_transaction(self.db, name="import_start"):  # the idle check and the insert are one transaction
            self._require_idle(root_id)
            self.db.add(run)
        return run

    def cancel(self, run: ImportRun) -> ImportRun:
        if run.state == "running":
            run.state = "cancel_requested"
            self.db.flush()
        return run

    def resume(self, run: ImportRun) -> ImportRun:
        if run.state not in RESUMABLE_STATES:
            raise ImportRunError("Only a cancelled, failed or interrupted import can be resumed.")
        with write_transaction(self.db, name="import_resume"):
            # Its cursor and last_seen stamps predate the newer run's: finishing it would mark that run's files missing.
            self._require_newest(run)
            self._require_idle(run.root_id)
            run.state, run.error, run.finished_at = "running", None, None
        return run

    def confirm(self, run: ImportRun) -> ImportRun:
        """Apply the missing marks the mass-missing guard held back, once an admin confirms the files are gone."""
        lock = _step_lock(run.root_id)
        if not lock.acquire(timeout=CONFIRM_WAIT_S):  # a step hung on a dead share never holds a request thread
            raise ImportRunError("An import is busy on this folder. Try again in a minute.")
        try:
            self.db.refresh(run)
            if run.state != "needs_confirmation":
                raise ImportRunError("Only an import that is waiting for confirmation can be confirmed.")
            self._require_newest(run)  # unseen is relative to this run's last_seen stamps; a newer run overwrote them
            root = self.db.get(StorageRoot, run.root_id)
            # A scoped run lstat()s its candidates first; the probe after them catches a share that dropped meanwhile.
            scope_gone = self._scope_gone(run, root, set(), confirmed=True) if root is not None and run.scope else None
            if root is None or not self._online(run, root):
                raise ImportRunError("The folder is not reachable, so nothing was marked missing.")
            with write_transaction(self.db, name="import_confirm"):
                counters = dict(run.counters or {})
                counters["missing"] = self._mark_unseen_missing(run, root, counters, confirmed=True, scope_gone=scope_gone)
                run.counters = counters
                self._finish(run, "succeeded")
        finally:
            lock.release()
        return run

    def _require_newest(self, run: ImportRun) -> None:
        newer = self.db.query(ImportRun.id).filter(ImportRun.root_id == run.root_id, ImportRun.created_at > run.created_at).first()
        if newer is not None:
            raise ImportRunError("A newer import ran for this folder. Rescan instead.")

    def _require_idle(self, root_id: str) -> None:
        if self.db.query(ImportRun.id).filter(ImportRun.root_id == root_id, ImportRun.state.in_(ACTIVE_STATES)).first():
            raise ImportRunError("An import is already running for this root.")

    def step(self, run_id: str) -> bool:
        """Advance one bounded batch; True once the run is terminal."""
        root_id = self.db.scalar(select(ImportRun.root_id).where(ImportRun.id == run_id))
        if root_id is None:
            return True
        with _step_lock(root_id):
            return self._step(run_id)

    def advance_active(self) -> None:
        """Maintenance hook: one batch per active run, skipped while a driver holds that root's lock."""
        for run_id, root_id in self.db.query(ImportRun.id, ImportRun.root_id).filter(ImportRun.state.in_(ACTIVE_STATES)).all():
            lock = _step_lock(root_id)
            if lock.acquire(blocking=False):
                try:
                    self._step(run_id)
                finally:
                    lock.release()

    def _step(self, run_id: str) -> bool:
        self.db.expire_all()
        run = self.db.get(ImportRun, run_id)
        if run is None or run.state not in ACTIVE_STATES:
            return True
        if run.state == "cancel_requested":
            with write_transaction(self.db, name="import_cancel"):
                self._finish(run, "cancelled")
            return True
        root = self.db.get(StorageRoot, run.root_id)
        if root is None or not self._online(run, root):
            # A missing or swapped mount is never read as an empty library.
            with write_transaction(self.db, name="import_root_offline"):
                run.coverage = "incomplete"
                run.error = run.root_observation.get("state", "offline") if root else "root_removed"
                self._finish(run, "failed")
            return True

        # Filesystem phase: no writer slot held.
        root_path = Path(root.path)
        after = tuple(json.loads(run.cursor or "[]"))
        batch: list[Found] = []
        exhausted = True
        walking = walk_scope(root_path, after, run.scope) if run.scope else walk(root_path, after)
        for item in walking:
            batch.append(item)
            if len(batch) >= BATCH_SIZE:
                exhausted = False
                break
        errors = [(parts, error) for parts, _, error, _ in batch if error is not None]
        found: list[tuple[tuple[str, ...], os.stat_result, frozenset[str]]] = []
        for parts, entry, error, siblings in batch:
            if error is not None:
                continue
            try:
                found.append((parts, entry.stat(follow_symlinks=False), siblings))
            except OSError:
                errors.append((parts, "unreadable"))
        records: dict[str, dict] = {}
        audio_tags = self._audio_tags(root, root_path, {"/".join(parts): status for parts, status, _ in found}, records)
        files: list[tuple[str, os.stat_result, dict]] = []
        folder_nfos: dict = {}
        for parts, status, siblings in found:
            relative = "/".join(parts)
            described = describe(root_path, parts, siblings, folder_nfos, audio_tags=audio_tags)
            if relative in records:  # The tags travel with the item under the file's fingerprint
                described["metadata"]["lumina_audio_tags"] = records[relative]
            files.append((relative, status, described))
        known = {
            artifact.relative_path: artifact
            for artifact in self.db.query(MediaArtifact).filter(
                MediaArtifact.root_id == root.id, MediaArtifact.relative_path.in_([file[0] for file in files])
            )
        } if files else {}
        hashes = {
            relative: hash_prefix(root_path / relative)
            for relative, status, _ in files
            if relative not in known or (known[relative].size, known[relative].mtime_ns, known[relative].inode) != _fingerprint(status)
        }
        moves, review = self._find_moves(run, root_path, [(r, s) for r, s, _ in files if r not in known], hashes)
        scope_gone = None
        # Scoped gate also needs coverage complete (earlier failed batches set it incomplete); the end-of-walk probe below uses failed alone.
        failed = any(error not in HARMLESS_SKIPS for _, error in errors)  # a symlink or too-deep skip keeps coverage complete
        if exhausted and run.scope and not failed and run.coverage == "complete":
            # this batch's artifacts are seen (or moved) once it commits, so they are not candidates
            scope_gone = self._scope_gone(run, root, {a.id for a in [*known.values(), *moves.values()]}, confirmed=False)
        # After the lstats: a share that dropped while they ran is caught here, before anything is marked.
        if exhausted and not failed and not self._online(run, root):
            errors.append(((), run.root_observation.get("state", "offline")))

        with write_transaction(self.db, name="import_batch"):
            counters = dict(run.counters or {})
            counters["inspected"] = counters.get("inspected", 0) + len(files)
            search = LibrarySearchService(self.db)
            items = self._linked_items([artifact.id for artifact in [*known.values(), *moves.values()]])
            linked: list[tuple[LibraryItem, dict, bool]] = []
            sidecars: list[tuple[str, str, list[dict]]] = []
            for relative, status, described in files:
                subtitles = described["metadata"].get("lumina_subtitles") or []
                text_tracks = [subtitle for subtitle in subtitles if subtitle["format"] in TEXT_TRACK_EXTS]
                artifact = known.get(relative) or moves.get(relative)
                if artifact is None:
                    artifact, item = self._index_new(run, root, relative, status, described, search)
                    outcome = "indexed"
                    if relative in review:
                        self._entry(run, counters, relative, "review", "possible_move", item.id)
                    linked.append((item, described, False))
                    if text_tracks:
                        sidecars.append((item.id, os.path.dirname(relative), text_tracks))
                else:
                    outcome = "relinked" if relative in moves else "updated" if relative in hashes or artifact.lifecycle != "available" else "unchanged"
                    artifact.relative_path, artifact.lifecycle = relative, "available"
                    for item in items.get(artifact.id, []):
                        # A sidecar edited in place under the same name is re-read only when its
                        # video changes; add the sidecar's mtime to lumina_subtitles when that bites.
                        if text_tracks and (outcome != "unchanged" or (item.metadata_json or {}).get("lumina_subtitles") != subtitles):
                            sidecars.append((item.id, os.path.dirname(relative), text_tracks))
                        _apply(item, described, status, search)
                        linked.append((item, described, relative in moves))
                artifact.size, artifact.mtime_ns, artifact.inode = _fingerprint(status)
                artifact.content_hash = hashes.get(relative, artifact.content_hash)
                artifact.last_seen_run_id = run.id
                counters[outcome] = counters.get(outcome, 0) + 1
            self._link_titles(root, linked, run.created_at)
            for parts, error in errors:
                key = "skipped" if error in HARMLESS_SKIPS else "failed"
                counters[key] = counters.get(key, 0) + 1
                if key == "failed":
                    run.coverage = "incomplete"
                self._entry(run, counters, "/".join(parts), key, error)
            if batch:
                run.cursor = json.dumps(list(batch[-1][0]))
            if exhausted:
                state = "succeeded"
                if run.coverage == "complete":
                    missing = self._mark_unseen_missing(run, root, counters, scope_gone=scope_gone)
                    if missing is None:
                        state = "needs_confirmation"
                    else:
                        counters["missing"] = missing
                self._finish(run, "partial" if run.coverage == "incomplete" else state)
            run.counters = counters
        # After the batch commit: transcript writes take their own short transactions.
        self._ingest_sidecars(root_path, sidecars)
        if exhausted:
            _after_import(run_id)
        return exhausted

    def _online(self, run: ImportRun, root: StorageRoot) -> bool:
        StorageRootService(self.db).probe(root)
        run.root_observation = dict(root.observation or {})
        return bool(root.enabled) and run.root_observation.get("state") in ONLINE_STATES

    def _find_moves(self, run: ImportRun, root_path: Path, new: list[tuple[str, os.stat_result]], hashes: dict) -> tuple[dict[str, MediaArtifact], set[str]]:
        """Confirmed renames: same size and inode or hash prefix, not seen this run, old path gone.

        More than one match is ambiguous: the file is indexed separately and flagged for review.
        """
        if not new:
            return {}, set()
        candidates = self.db.query(MediaArtifact).filter(
            MediaArtifact.root_id == run.root_id,
            MediaArtifact.size.in_(list({status.st_size for _, status in new})),
            or_(MediaArtifact.last_seen_run_id.is_(None), MediaArtifact.last_seen_run_id != run.id),
        ).all()
        moves: dict[str, MediaArtifact] = {}
        review: set[str] = set()
        for relative, status in new:
            matches = [
                artifact for artifact in candidates
                if artifact.size == status.st_size
                and (artifact.inode == _fingerprint(status)[2] or (artifact.content_hash is not None and artifact.content_hash == hashes.get(relative)))
                and artifact not in moves.values()
                and _gone(root_path / artifact.relative_path)
            ]
            if len(matches) == 1:
                moves[relative] = matches[0]
            elif matches:
                review.add(relative)
        return moves, review

    def _linked_items(self, artifact_ids: list[str]) -> dict[str, list[LibraryItem]]:
        linked: dict[str, list[LibraryItem]] = {}
        if artifact_ids:
            rows = (
                self.db.query(LibraryItemArtifact.artifact_id, LibraryItem)
                .join(LibraryItem, LibraryItem.id == LibraryItemArtifact.library_item_id)
                .filter(LibraryItemArtifact.artifact_id.in_(artifact_ids))
            )
            for artifact_id, item in rows:
                linked.setdefault(artifact_id, []).append(item)
        return linked

    def _audio_tags(self, root: StorageRoot, root_path: Path, statuses: dict[str, os.stat_result],
                    records: dict[str, dict]) -> Callable[[tuple[str, ...]], AudioTags | None]:
        """describe()'s tag reader for one batch.

        A file whose item already stores tags under its current "size:mtime_ns:inode" is never opened. A file outside
        the batch (an album's first track, for its embedded cover) is looked up on its own. ``records`` collects what
        each file's item stores as ``lumina_audio_tags``.
        """
        stored = self._stored_audio_tags(root.id, [relative for relative in statuses if os.path.splitext(relative)[1].lower() in AUDIO_EXTENSIONS])

        def read(parts: tuple[str, ...]) -> AudioTags | None:
            relative = "/".join(parts)
            if relative not in records:
                status = statuses.get(relative)
                if status is None:
                    try:
                        status = os.lstat(root_path / relative)
                    except OSError:
                        return None
                    stored.update(self._stored_audio_tags(root.id, [relative]))
                fingerprint = "{}:{}:{}".format(*_fingerprint(status))
                record = stored.get(relative)
                if not isinstance(record, dict) or record.get("fp") != fingerprint:
                    tags = read_tags(root_path / relative)
                    record = {"fp": fingerprint} if tags is None else tags.record(fingerprint)
                records[relative] = record
            return AudioTags.from_record(records[relative])

        return read

    def _stored_audio_tags(self, root_id: str, relatives: list[str]) -> dict[str, dict]:
        """relative path -> the ``lumina_audio_tags`` its item stored at the last scan: one query."""
        if not relatives:
            return {}
        rows = self.db.execute(
            select(MediaArtifact.relative_path, func.json_extract(LibraryItem.metadata_json, "$.lumina_audio_tags"))
            .join(LibraryItemArtifact, LibraryItemArtifact.artifact_id == MediaArtifact.id)
            .join(LibraryItem, LibraryItem.id == LibraryItemArtifact.library_item_id)
            .where(MediaArtifact.root_id == root_id, MediaArtifact.relative_path.in_(relatives))
        )
        return {relative: json.loads(value) for relative, value in rows if value}

    def _link_titles(self, root: StorageRoot, linked: list[tuple[LibraryItem, dict, bool]], since: datetime) -> None:
        """Upsert each file's title chain outer→leaf and point the item at its leaf.

        One prefetch per batch. Fields go through ``apply_field`` (sources ``path`` then ``nfo``), so a
        ``user``/``nfo`` value is never overwritten by a lower-ranked scan and ``None`` never erases.
        Titles are never deleted: an item that stops classifying just loses its link, and a title
        with no available item is invisible (``visible_title_predicate``).
        """
        def full_key(spec: dict) -> str:
            return spec["key"] if spec["type"] in GLOBAL_KEY_TYPES else f"{root.id}:{spec['key']}"

        keys = {full_key(spec) for _, described, _ in linked for spec in described["titles"]}
        titles = {title.key: title for title in self.db.scalars(select(MediaTitle).where(MediaTitle.key.in_(keys)))} if keys else {}
        lookup = lambda title_id: self.db.get(MediaTitle, title_id) if title_id else None  # noqa: E731
        before = {title.id: title_search_fields(title, lookup) for title in titles.values()}
        art_before = {title.id: title.images for title in titles.values()}
        now = utcnow()
        photos: dict[str, dict] = {}  # person id -> nfo_people values
        for item, described, moved in linked:
            chain = described["titles"]
            if not chain:
                if item.title_id is not None or item.extra_type is not None:
                    item.title_id = item.extra_type = None
                continue
            current = self.db.get(MediaTitle, item.title_id) if item.title_id else None
            if current is not None and (moved or (current.key != full_key(chain[-1]) and self._sole_item(current.id, item.id))):
                # A move carries its titles and ancestors; a key the rules now spell differently carries only
                # the leaf (no other file shares it), since its season/series may still hold other files.
                self._rekey(item.title_id, [full_key(spec) for spec in chain], chain, titles, leaf_only=not moved)
            parent: MediaTitle | None = None
            for spec in chain:
                key = full_key(spec)
                title = titles.get(key)
                if title is None:
                    fields = spec["fields"]
                    title = MediaTitle(
                        id=str(uuid.uuid4()), type=spec["type"], key=key,
                        root_id=None if spec["type"] == "boxset" else root.id,
                        name=fields["nfo"].get("name") or fields["path"].get("name") or "",
                        provider_ids={}, field_sources={}, images={}, metadata_json={},
                        metadata_due_at=now if spec["type"] in ("series", "movie") else None,  # Queue
                    )
                    self.db.add(title)
                    titles[key] = title
                elif title.type != spec["type"]:
                    title.type = spec["type"]  # a folder that became a show (tvshow.nfo added) keeps its id
                if spec["type"] == "episode" and parent is not None:
                    scanned_parent(title, parent.id)  # a user-moved episode keeps its season (#164)
                elif spec["type"] in ("season", "album") and parent is not None and title.parent_id != parent.id:
                    title.parent_id = parent.id
                if spec["type"] == "movie" and parent is not None and parent.type == "boxset":
                    apply_field(title, "boxset_id", parent.id, "nfo")
                foreign = spec["type"] in ("album", "artist") and title.root_id != root.id  # global keys; art resolves under root_id
                for source in ("path", "nfo"):
                    for field, value in spec["fields"][source].items():
                        if foreign and field.startswith("images."):
                            continue
                        if field == "provider_ids":
                            value = {**(title.provider_ids or {}), **value}  # apply_field replaces dicts
                        elif field == "people":
                            value = split_people(value, photos)  # thumbs go to nfo_people rows, not into every title's refs
                        elif field == "crew":
                            value = split_crew(value, photos)  # nfo_title_fields puts "crew" after "people": thumbs first
                        apply_field(title, field, value, source)
                parent = title
            if item.title_id != parent.id:
                item.title_id = parent.id
            if item.extra_type != described["extra_type"]:
                item.extra_type = described["extra_type"]
        # Re-index only new titles or changed search text; index_title drops the
        # stored embedding, so an unchanged rescan must not call it (else the backfill re-embeds everything).
        touched = {title.id: title for _, described, _ in linked for spec in described["titles"] if (title := titles.get(full_key(spec)))}
        art_changed = [title.id for title in touched.values() if title.images and title.images != art_before.get(title.id)]
        if art_changed:  # New or changed art jumps the rendition queue once this batch commits
            queue_after_commit(self.db, lambda: _wake_renditions(art_changed))
        save_people(self.db, photos)
        self.db.flush()
        music = [title_id for title_id, title in touched.items() if title.type in ("album", "artist")]
        stamp = since.strftime("%Y-%m-%d %H:%M:%S.%f")  # created_at as SQLAlchemy stores it in SQLite
        for statement in MUSIC_ADDED_AT_SQL if music else ():  # albums first, then their artists
            self.db.execute(text(statement).bindparams(bindparam("ids", expanding=True)), {"ids": music, "since": stamp})
        refresh_added_at(self.db, touched)  # this batch's files may be a title's first, or a show's newest episode
        refresh_category(self.db, touched)  # The batch's titles take their folders' category
        changed = [title for title in touched.values() if before.get(title.id) != title_search_fields(title, lookup)]
        for title in changed:
            index_title(self.db, title)

    def _ingest_sidecars(self, root_path: Path, pending: list[tuple[str, str, list[dict]]]) -> None:
        """SRT/VTT sidecars become ``source_caption`` transcripts, so local TV gets moments and summaries.

        Read bounded and never through a symlink; the transcript store dedupes by content digest,
        so re-reading an unchanged file stores nothing. A bad file is logged and skipped.
        """
        transcripts = TranscriptService(self.db)
        for item_id, directory, subtitles in pending:
            for subtitle in subtitles:
                try:
                    data = read_bounded(root_path / directory / subtitle["filename"], MAX_TRACK_BYTES + 1)
                    transcripts.store(
                        item_id, language=subtitle["language"] or "und", source_kind="source_caption",
                        cues=parse_caption(data, subtitle["format"]), derived_from=f"sidecar:{subtitle['filename']}"[:80],
                    )
                except Exception as exc:  # noqa: BLE001 - a bad sidecar or busy writer never fails the scan
                    logger.warning("Skipped a subtitle sidecar for library item %s: %s", item_id, type(exc).__name__)

    def _sole_item(self, title_id: str, item_id: str) -> bool:
        return self.db.query(LibraryItem.id).filter(LibraryItem.title_id == title_id, LibraryItem.id != item_id).first() is None

    def _rekey(self, title_id: str, keys: list[str], chain: list[dict], titles: dict[str, MediaTitle], *, leaf_only: bool = False) -> None:
        """A moved file whose new keys are unused takes its old titles (and ancestors) along;
        with ``leaf_only`` (a re-keyed file that did not move) only its own leaf title moves.

        A folder renamed *and* re-encoded gets new titles; add provider-id matching when that bites.
        """
        title = self.db.get(MediaTitle, title_id)
        for key, spec in zip(reversed(keys), reversed(chain)):
            if title is None or key in titles or title.type != spec["type"]:
                return
            titles.pop(title.key, None)
            title.key = key
            titles[key] = title
            if leaf_only or (title.field_sources or {}).get("parent_id") == "user":
                return  # a user-moved episode's season is not the folder's: it never takes the folder's key
            title = self.db.get(MediaTitle, title.parent_id) if title.parent_id else None

    def _mark_unseen_missing(
        self, run: ImportRun, root: StorageRoot, counters: dict, *, confirmed: bool = False,
        scope_gone: tuple[int, list[str] | None] | None = None,
    ) -> int | None:
        """Complete coverage only: artifacts this run never saw become missing (tombstoned, never deleted).

        Returns None when the mass-missing guard held the marks back for an admin to confirm.
        """
        if run.scope:
            return self._mark_scope_missing(root, counters, scope_gone, confirmed=confirmed)
        self.db.flush()  # this batch's last_seen marks must precede the bulk update
        unseen = self.db.query(MediaArtifact).filter(
            MediaArtifact.root_id == root.id,
            MediaArtifact.lifecycle == "available",
            or_(MediaArtifact.last_seen_run_id.is_(None), MediaArtifact.last_seen_run_id != run.id),
        )
        if counters.get("inspected", 0) == 0 and unseen.first() is not None:
            # An empty listing of a root that held media looks like a lost mount, not a deleted library.
            run.coverage, run.error = "incomplete", "root_empty"
            return 0
        if not confirmed:
            candidates = unseen.count()
            available = self.db.query(MediaArtifact).filter(MediaArtifact.root_id == root.id, MediaArtifact.lifecycle == "available").count()
            if mass_missing(candidates, available):
                counters["missing_candidates"], counters["available"] = candidates, available
                return None
        missing = unseen.update({MediaArtifact.lifecycle: "missing"}, synchronize_session=False)
        self._flag_missing_items(root)
        return missing

    def _scope_gone(self, run: ImportRun, root: StorageRoot, skip: set[str], *, confirmed: bool) -> tuple[int, list[str] | None]:
        """Filesystem phase, no writer slot: (candidates, ids lstat() confirms gone); None = over the lstat budget.

        A scoped run's candidates are its in-scope unseen artifacts; one still on disk
        (e.g. under a new .ignore) is left for a full scan.
        """
        rows = [
            row for row in self.db.query(MediaArtifact.id, MediaArtifact.relative_path).filter(
                MediaArtifact.root_id == root.id,
                MediaArtifact.lifecycle == "available",
                or_(MediaArtifact.last_seen_run_id.is_(None), MediaArtifact.last_seen_run_id != run.id),
                _scope_filter(run.scope),
            )
            if row.id not in skip
        ]
        if not confirmed and len(rows) > MAX_GONE_CHECKS:
            return len(rows), None
        # Confirm lstats every candidate (an admin asked for it), outside the slot; page it if roots reach 1e6 files.
        root_path = Path(root.path)
        return len(rows), [artifact_id for artifact_id, relative in rows if _gone(root_path / relative)]

    def _mark_scope_missing(self, root: StorageRoot, counters: dict, scope_gone: tuple[int, list[str] | None] | None, *, confirmed: bool) -> int | None:
        """Under the writer slot: mark the gone ids _scope_gone found; the guard's denominator is the whole root."""
        if scope_gone is None:  # Callers always pass it for a scoped run; marking nothing beats lstat under the slot
            return 0
        candidates, gone = scope_gone
        self.db.flush()
        available = self.db.query(MediaArtifact).filter(MediaArtifact.root_id == root.id, MediaArtifact.lifecycle == "available").count()
        if gone is None or (not confirmed and mass_missing(len(gone), available)):
            counters["missing_candidates"], counters["available"] = candidates if gone is None else len(gone), available
            return None
        missing = 0
        for start in range(0, len(gone), GONE_CHUNK):
            missing += self.db.query(MediaArtifact).filter(
                MediaArtifact.id.in_(gone[start:start + GONE_CHUNK]), MediaArtifact.lifecycle == "available"
            ).update({MediaArtifact.lifecycle: "missing"}, synchronize_session=False)
        self._flag_missing_items(root)
        return missing

    def _flag_missing_items(self, root: StorageRoot) -> None:
        gone = (
            select(LibraryItemArtifact.library_item_id)
            .join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
            .where(MediaArtifact.root_id == root.id, MediaArtifact.lifecycle == "missing")
        )
        self.db.query(LibraryItem).filter(LibraryItem.id.in_(gone), LibraryItem.status != "missing").update(
            {LibraryItem.status: "missing"}, synchronize_session=False
        )

    def _index_new(self, run: ImportRun, root: StorageRoot, relative: str, status: os.stat_result, described: dict, search: LibrarySearchService) -> tuple[MediaArtifact, LibraryItem]:
        artifact = MediaArtifact(
            id=str(uuid.uuid4()),
            root_id=root.id,
            relative_path=relative,
            ownership="external",
            owner_user_id=run.user_id,
        )
        item = LibraryItem(
            id=str(uuid.uuid4()),
            user_id=run.user_id,
            visibility=run.visibility,
            extractor=ORIGIN,
            downloaded_at=utcnow(),
        )
        _apply(item, described, status, None)
        self.db.add_all([artifact, item])
        self.db.flush()
        self.db.add(LibraryItemArtifact(library_item_id=item.id, artifact_id=artifact.id))
        search.index_new_item(item)
        return artifact, item

    def _entry(self, run: ImportRun, counters: dict, relative: str, outcome: str, error: str | None, item_id: str | None = None) -> None:
        if counters.get("entries", 0) >= MAX_ENTRIES_PER_RUN:
            return
        counters["entries"] = counters.get("entries", 0) + 1
        self.db.add(ImportEntry(run_id=run.id, relative_path=relative, outcome=outcome, error=error, library_item_id=item_id))

    def _finish(self, run: ImportRun, state: str) -> None:
        run.state = state
        run.finished_at = utcnow()


def _apply(item: LibraryItem, described: dict, status: os.stat_result, search: LibrarySearchService | None) -> None:
    """Refresh import-owned fields; the FTS row is rewritten only when its text changed."""
    item.status = "available"
    item.file_size = status.st_size
    fields = (described["title"], described["uploader"], described["playlist_name"], described["metadata"])
    if (item.title, item.uploader, item.playlist_name, item.metadata_json) == fields:
        return
    item.title, item.uploader, item.playlist_name = fields[:3]
    LibraryService._set_metadata(item, described["metadata"])
    if search is not None:
        search.sync_item(item)


def drive(run_id: str) -> None:
    """Run an import to a terminal state in bounded steps (FastAPI background task)."""
    while not stop_event.is_set():
        with session_scope() as db:
            if LibraryImportService(db).step(run_id):
                return
