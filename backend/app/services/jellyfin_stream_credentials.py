"""Credential and selected playback file lookup for Jellyfin streams."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import and_, bindparam, literal, literal_column, or_, select, true
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from app.models import AppSettings, DeviceToken, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, MemberAccess, StorageRoot, User
from app.services.library import LibraryService
from app.services.media_artifacts import artifact_file
from app.services.media_titles import parse_item_id
from app.services import member_access
from app.services.titles import LEAF_TYPES, TitleService


@dataclass(frozen=True)
class CredentialStreamFile:
    """One explicit-token stream lookup: gate/auth rows plus an optional selected file."""

    enabled: bool
    token: DeviceToken | None
    user: User | None
    access: MemberAccess | None
    selected: tuple[str, str | None, str | None, bool | None, str | None] | None


def _credential_user(credential, role: str) -> User:  # noqa: ANN001
    """A transient user whose identity comes from the one-row credential CTE."""
    return User(
        id=select(credential.c.user_id).scalar_subquery(),
        role=role,
    )


def _credential_visible_item(credential):  # noqa: ANN001, ANN202
    """The normal live member predicate, driven by credential CTE scalars.

    A transient viewer retains the uncorrelated MemberAccess subqueries; the
    admin branch deliberately has the ordinary unrestricted/ownerless rule.
    """
    viewer = _credential_user(credential, "viewer")
    admin = _credential_user(credential, "admin")
    return or_(
        and_(credential.c.user_role == "admin", LibraryService.visible_predicate(admin)),
        and_(credential.c.user_role != "admin", LibraryService.visible_predicate(viewer)),
    )


def _credential_visible_title(credential):  # noqa: ANN001, ANN202
    viewer = _credential_user(credential, "viewer")
    admin = _credential_user(credential, "admin")
    return or_(
        and_(credential.c.user_role == "admin", TitleService.visible(admin)),
        and_(credential.c.user_role != "admin", TitleService.visible(viewer)),
    )


_STATIC_STREAM_TYPE_SETS = frozenset((
    LEAF_TYPES,
    member_access.MUSIC_TYPES,
    ("movie",),
    member_access.TV_TYPES,
    ("movie", *member_access.TV_TYPES),
))
_STREAM_RUNTIME_BIND_KEYS = frozenset(("token_digest", "cutoff", "entity_id", "wanted"))
_STATIC_STREAM_VALUE_TYPES = (str, int, bool)
_SQLITE_DIALECT = sqlite.dialect()


def _fixed_stream_type_lists(statement):  # noqa: ANN001, ANN202
    """Replace only the cached statement's immutable type-list expansions.

    The live member-access predicates deliberately use scalar subqueries.  Their
    fixed media-type categories otherwise become SQLAlchemy expanding parameters
    on every range request, even though this cached template never changes.
    """
    def replace(node):  # noqa: ANN001, ANN202
        if not (
            isinstance(node, BinaryExpression)
            and node.operator in (operators.in_op, operators.not_in_op)
            and isinstance(node.right, BindParameter)
            and node.right.expanding
            and isinstance(node.right.value, (list, tuple))
            and tuple(node.right.value) in _STATIC_STREAM_TYPE_SETS
        ):
            return None
        values = tuple(literal(value) for value in node.right.value)
        return node.left.in_(values) if node.operator is operators.in_op else node.left.not_in(values)

    return visitors.replacement_traverse(statement, {}, replace)


def _inline_static_stream_values(statement):  # noqa: ANN001, ANN202
    """Inline this cached SQLite statement's immutable primitive defaults.

    The four caller values remain ordinary binds.  SQLAlchemy's SQLite compiler
    owns quoting each static token, while unknown or non-scalar defaults stay
    bound for their normal runtime handling.
    """
    def replace(node):  # noqa: ANN001, ANN202
        if not (
            isinstance(node, BindParameter)
            and node.key not in _STREAM_RUNTIME_BIND_KEYS
            and node.unique
            and not node.required
            and node.callable is None
            and not node.expanding
            and not node.literal_execute
            and type(node.value) in _STATIC_STREAM_VALUE_TYPES
        ):
            return None
        token = str(node.compile(dialect=_SQLITE_DIALECT, compile_kwargs={"literal_binds": True}))
        return literal_column(token, type_=node.type)

    return visitors.replacement_traverse(statement, {}, replace)


@lru_cache(maxsize=1)
def _selected_stream_with_credential_statement():  # noqa: ANN202
    """The immutable SQL shape for the explicit selected-stream fast path.

    Values remain bind parameters: this only avoids rebuilding the large visibility
    expression for every range request.
    """
    token_digest = bindparam("token_digest")
    cutoff = bindparam("cutoff")
    entity_id = bindparam("entity_id")
    wanted = bindparam("wanted")
    credential = (
        select(
            AppSettings.jellyfin_enabled.label("enabled"),
            DeviceToken.id.label("token_id"),
            User.id.label("user_id"),
            User.role.label("user_role"),
            User.is_active.label("user_active"),
            DeviceToken.last_seen_at.label("last_seen_at"),
        )
        .select_from(AppSettings)
        .outerjoin(DeviceToken, and_(DeviceToken.token_digest == token_digest, DeviceToken.kind == "jellyfin"))
        .outerjoin(User, User.id == DeviceToken.user_id)
        .where(AppSettings.id == 1)
        .cte("stream_credential")
    )
    caller_is_live = and_(
        credential.c.enabled.is_(True),
        credential.c.user_id.is_not(None),
        credential.c.user_role.is_not(None),
        credential.c.user_active.is_(True),
        credential.c.last_seen_at > cutoff,
    )
    parent = select(MediaTitle.id).where(
        MediaTitle.id == entity_id,
        MediaTitle.type.in_(LEAF_TYPES),
        _credential_visible_title(credential),
    )
    selected = (
        select(
            LibraryItem.id.label("version_id"),
            MediaArtifact.lifecycle.label("lifecycle"),
            MediaArtifact.relative_path.label("relative_path"),
            StorageRoot.enabled.label("root_enabled"),
            StorageRoot.path.label("root_path"),
        )
        .select_from(LibraryItem)
        .join(credential, true())
        .outerjoin(LibraryItemArtifact, LibraryItemArtifact.library_item_id == LibraryItem.id)
        .outerjoin(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id)
        .outerjoin(StorageRoot, StorageRoot.id == MediaArtifact.root_id)
        .where(
            caller_is_live,
            LibraryItem.id == wanted,
            LibraryItem.title_id.in_(parent),
            LibraryItem.extra_type.is_(None),
            LibraryItem.status != "missing",
            _credential_visible_item(credential),
        )
        .cte("selected_stream_file")
    )
    statement = (
        select(
            credential.c.enabled,
            DeviceToken,
            User,
            MemberAccess,
            selected.c.version_id,
            selected.c.lifecycle,
            selected.c.relative_path,
            selected.c.root_enabled,
            selected.c.root_path,
        )
        .select_from(credential)
        .outerjoin(DeviceToken, DeviceToken.id == credential.c.token_id)
        .outerjoin(User, User.id == credential.c.user_id)
        .outerjoin(MemberAccess, MemberAccess.user_id == credential.c.user_id)
        .outerjoin(selected, true())
    )
    return _inline_static_stream_values(_fixed_stream_type_lists(statement))


def warm_selected_stream_statement(db: Session) -> None:
    """Compile the selected-stream shape before the first playback request."""
    # NULL comparisons match no credential or file. Only SQL compilation is
    # reused, never the result or request values.
    db.execute(_selected_stream_with_credential_statement(), {
        "token_digest": None, "cutoff": datetime.min, "entity_id": None, "wanted": None,
    }).all()


def selected_stream_with_credential(
    db: Session, raw_id: str, media_source_id: str, *, token_digest: str, cutoff: datetime,
) -> CredentialStreamFile | None:
    """Load the enabled gate, current credential, and a distinct selected file in one statement.

    This does not validate or cache credentials.  The caller validates the returned
    token/member immediately, so idle, revoked, orphaned, and inactive credentials
    retain their existing lifecycle behavior before a path can be used.
    """
    entity_id, wanted = parse_item_id(raw_id), parse_item_id(media_source_id)
    if entity_id is None or wanted is None or wanted == entity_id:
        raise ValueError("This resolver is only for a distinct valid MediaSourceId.")

    row = db.execute(_selected_stream_with_credential_statement(), {
        "token_digest": token_digest,
        "cutoff": cutoff,
        "entity_id": entity_id,
        "wanted": wanted,
    }).one_or_none()
    if row is None:
        return None
    enabled, token, user, access, *file_row = row
    selected_row = tuple(file_row) if file_row[0] is not None else None
    return CredentialStreamFile(enabled=enabled, token=token, user=user, access=access, selected=selected_row)


def credential_stream_file(result: CredentialStreamFile) -> tuple[str, Path] | None:
    """Turn a selected-file row into a safely registered path after the credential is validated."""
    if result.selected is None:
        return None
    version_id, lifecycle, relative_path, enabled, root_path = result.selected
    if lifecycle != "available" or not enabled or relative_path is None or root_path is None:
        raise FileNotFoundError("Library item media is not available.")
    return version_id, artifact_file(root_path, relative_path)
