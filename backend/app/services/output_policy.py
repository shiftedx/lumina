from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from app.schemas import OutputProfile


class OutputPolicy:
    """Resolve household output preferences beneath one server-owned user root."""

    def __init__(self, library_root: str | Path, user_id: str):
        if not user_id or user_id in {".", ".."} or PurePosixPath(user_id).name != user_id or "\\" in user_id or ":" in user_id:
            raise ValueError("Invalid Library owner identifier.")
        self.library_root = Path(library_root).expanduser().resolve(strict=False)
        assigned_user_root = self.library_root / user_id
        self.user_root = assigned_user_root.resolve(strict=False)
        if self.user_root != assigned_user_root:
            raise ValueError("Library owner folder must not be a symlink or alias.")
        self._require_contained(self.user_root, self.library_root)

    def resolve(self, profile: OutputProfile) -> Path:
        profile = OutputProfile.model_validate(profile.model_dump())
        directory = self.user_root
        if profile.base_path:
            directory /= profile.base_path
        if profile.organize_by == "playlist":
            directory /= "by-playlist"
        elif profile.organize_by == "uploader":
            directory /= "by-uploader"
        if profile.subdir:
            directory /= profile.subdir

        resolved_directory = directory.resolve(strict=False)
        self._require_contained(resolved_directory, self.user_root)
        return resolved_directory / profile.template

    @staticmethod
    def sanitize_profile(value: dict[str, Any] | None, *, organize_by: str = "downloads") -> OutputProfile:
        raw = value if isinstance(value, dict) else {}
        safe_organize_by = raw.get("organize_by") if raw.get("organize_by") in {"downloads", "playlist", "uploader"} else organize_by
        safe: dict[str, Any] = {"organize_by": safe_organize_by}
        for field in ("base_path", "subdir", "template"):
            if field not in raw:
                continue
            try:
                validated = OutputProfile(**{field: raw[field], "organize_by": safe_organize_by})
            except ValueError:
                continue
            safe[field] = getattr(validated, field)
        return OutputProfile(**safe)

    @staticmethod
    def _require_contained(candidate: Path, root: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Output must stay inside the user's Library folder.") from exc
