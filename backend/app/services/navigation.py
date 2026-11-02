"""Current member-visible, populated destinations shared by browser and native apps."""
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.book_models import BookItem
from app.models import AppSettings, LibraryItem, LibraryItemArtifact, MediaArtifact, MediaTitle, StorageRoot, User
from app.photo_models import PhotoItem
from app.photo_root_models import PhotoImportSource
from app.services import member_access, two_factor
from app.services.books import BookService
from app.services.library import LibraryService
from app.services.photos import PhotoService
from app.services.requests.engine import policy_for


def _usable_item():  # noqa: ANN202
    """Use enabled roots; legacy items without artifact links remain usable."""
    links = select(LibraryItemArtifact.library_item_id).where(LibraryItemArtifact.library_item_id == LibraryItem.id)
    active = links.join(MediaArtifact, MediaArtifact.id == LibraryItemArtifact.artifact_id).join(
        StorageRoot, StorageRoot.id == MediaArtifact.root_id,
    ).where(
        MediaArtifact.lifecycle == "available", StorageRoot.enabled.is_(True),
    )
    return and_(LibraryItem.status != "missing", or_(~links.exists(), active.exists()))


def navigation(db: Session, user: User) -> dict:
    # Follow indexed title-parent and item-title links from each wall title. A single
    # OR across all three levels prevents SQLite from using those indexes and scans
    # the whole catalog once per title on large libraries.
    leaf, parent = aliased(MediaTitle), aliased(MediaTitle)
    items = select(LibraryItem.id).where(LibraryService.visible_predicate(user), _usable_item())
    direct = items.where(LibraryItem.title_id == MediaTitle.id).exists()
    children = items.join(leaf, leaf.id == LibraryItem.title_id).where(leaf.parent_id == MediaTitle.id).exists()
    grandchildren = items.join(leaf, leaf.id == LibraryItem.title_id).join(
        parent, parent.id == leaf.parent_id,
    ).where(parent.parent_id == MediaTitle.id).exists()
    wall = select(MediaTitle.id).where(
        MediaTitle.type.in_(("movie", "series")), or_(direct, children, grandchildren),
    )
    if (access_clause := member_access.title_clause(user)) is not None:
        wall = wall.where(access_clause)
    categories = {
        key for category, key in (("movies", "movies"), ("shows", "tv"), ("anime", "anime"))
        if db.scalar(wall.where(MediaTitle.category == category).limit(1)) is not None
    }
    visible_item = LibraryService.visible_predicate(user)
    if db.scalar(select(LibraryItem.id).where(LibraryItem.kind.in_(("track", "audio")), visible_item, _usable_item()).limit(1)):
        categories.add("music")
    if db.scalar(select(LibraryItem.id).where(
        LibraryItem.kind == "video", *LibraryService.view_filters(source="saved"), visible_item, _usable_item(),
    ).limit(1)):
        categories.add("youtube")
    if db.scalar(select(LibraryItem.id).where(LibraryItem.kind == "recording", visible_item, _usable_item()).limit(1)):
        categories.add("recordings")
    if db.scalar(select(BookItem.id).where(BookService(db).catalog_predicate(user), BookItem.available.is_(True)).limit(1)):
        categories.add("books")
    external_photo = select(PhotoImportSource.id).join(StorageRoot, StorageRoot.id == PhotoImportSource.root_id).where(
        PhotoImportSource.id == PhotoItem.source_id, PhotoImportSource.enabled.is_(True), StorageRoot.enabled.is_(True),
    ).exists()
    if db.scalar(select(PhotoItem.id).where(
        PhotoService(db).visible_predicate(user), PhotoItem.deleted_at.is_(None), PhotoItem.missing_at.is_(None),
        or_(PhotoItem.storage_kind == "managed", external_photo),
    ).limit(1)):
        categories.add("photos")
    settings = db.get(AppSettings, 1)
    access = member_access.for_user(db, user) or member_access.EffectiveAccess()
    # Native streaming is an independent opt-in; browser discovery remains available.
    streaming = any(
        access.streaming.get(provider, True) for provider in ("youtube", "twitch", "kick")
    )
    requests = bool(settings and settings.requests_enabled) and (
        two_factor.owner_capable(db, user) or any(policy_for(db, user.id, kind)["can_request"] for kind in ("movie", "show", "anime"))
    )
    return {
        "categories": [key for key in ("movies", "tv", "anime", "music", "books", "youtube", "recordings", "photos") if key in categories],
        "features": {"streaming": streaming, "requests": requests},
    }
