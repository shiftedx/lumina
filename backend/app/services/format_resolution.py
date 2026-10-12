from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import FormatResolutionState, FormatSelection


# H.264 + AAC over HTTP(S) (the download gate refuses YouTube HLS); the trailing fallbacks keep sources
# without H.264 downloadable, and `_is_editable` then records that honestly.
EDITABLE_SELECTOR = (
    "bv[vcodec^=avc1][protocol^=http]+ba[acodec^=mp4a][protocol^=http]"
    "/b[vcodec^=avc1][acodec^=mp4a][protocol^=http]/bv+ba/b"
)
NOT_EDITABLE_REASON = "Not editable: the source has no H.264 + AAC version, so Lumina saved the best available instead."


@dataclass(frozen=True)
class FormatResolutionPlan:
    selector: str
    state: FormatResolutionState


@dataclass(frozen=True)
class AcquisitionResult:
    info: dict[str, Any]
    format_resolution: FormatResolutionState


class FormatResolutionPolicy:
    """Resolve one acquisition choice into a selector and safe observable state."""

    @classmethod
    def resolve(
        cls,
        selection: FormatSelection,
        *,
        info: dict[str, Any] | None = None,
        previous: FormatResolutionState | dict[str, Any] | None = None,
    ) -> FormatResolutionPlan:
        requested_selector = cls._requested_selector(selection)
        previous_state = cls._state(previous)
        compatible_previous = (
            previous_state if previous_state and previous_state.requested_selector == requested_selector else None
        )
        explicit_custom = bool(selection.custom_format)
        selector = requested_selector
        if compatible_previous and compatible_previous.selected_format_id and not explicit_custom:
            selector = f"{compatible_previous.selected_format_id}/{requested_selector}"

        selected_format_id = cls._selected_format_id(info) if info is not None else None
        fallback_reason = compatible_previous.fallback_reason if compatible_previous and selected_format_id is None else None
        if (
            not explicit_custom
            and compatible_previous
            and compatible_previous.selected_format_id
            and selected_format_id
            and selected_format_id != compatible_previous.selected_format_id
        ):
            fallback_reason = (
                f"Source formats changed after preview; Lumina used {selected_format_id} instead of "
                f"{compatible_previous.selected_format_id} while preserving the {selection.preset} selection policy."
            )
        if selection.preset == "best_editable" and not explicit_custom and info and not cls._is_editable(info):
            fallback_reason = NOT_EDITABLE_REASON

        return FormatResolutionPlan(
            selector=selector,
            state=FormatResolutionState(
                requested_selector=requested_selector,
                selected_format_id=selected_format_id
                or (compatible_previous.selected_format_id if compatible_previous else None),
                fallback_reason=fallback_reason,
            ),
        )

    @classmethod
    def _requested_selector(cls, selection: FormatSelection) -> str:
        if selection.custom_format:
            return selection.custom_format
        container = selection.output_container if selection.output_container in {"mp4", "webm", "mkv"} else "mp4"
        match selection.preset:
            case "best":
                return cls._best_selector(container)
            case "best_1080p":
                return cls._best_1080p_selector(container)
            case "best_editable":
                return EDITABLE_SELECTOR
            case "audio_only":
                return "bestaudio/best"
            case "source":
                return cls._source_selector(container)
            case _:
                return cls._best_selector(container)

    @staticmethod
    def _state(value: FormatResolutionState | dict[str, Any] | None) -> FormatResolutionState | None:
        if value is None:
            return None
        if isinstance(value, FormatResolutionState):
            return value
        return FormatResolutionState.model_validate(value)

    @staticmethod
    def _is_editable(info: dict[str, Any]) -> bool:
        # A playlist's top level carries no codecs; only judge what yt-dlp actually reports.
        vcodec, acodec = str(info.get("vcodec") or ""), str(info.get("acodec") or "")
        if not vcodec and not acodec:
            return True
        return vcodec.startswith("avc1") and acodec.startswith("mp4a")

    @staticmethod
    def _selected_format_id(info: dict[str, Any] | None) -> str | None:
        if not info:
            return None
        requested_ids = FormatResolutionPolicy._requested_format_ids(info)
        if requested_ids:
            return "+".join(dict.fromkeys(requested_ids))
        format_id = info.get("format_id")
        if format_id:
            return str(format_id)
        formats = info.get("formats")
        if not isinstance(formats, list):
            return None
        playable_ids = []
        for candidate in formats:
            if not isinstance(candidate, dict) or not candidate.get("format_id"):
                continue
            candidate_id = str(candidate["format_id"])
            extension = str(candidate.get("ext") or "").lower()
            protocol = str(candidate.get("protocol") or "").lower()
            has_media = str(candidate.get("vcodec") or "none").lower() != "none" or str(
                candidate.get("acodec") or "none"
            ).lower() != "none"
            if has_media and not candidate_id.lower().startswith("sb") and extension != "mhtml" and "mhtml" not in protocol:
                playable_ids.append(candidate_id)
        return playable_ids[0] if len(playable_ids) == 1 else None

    @staticmethod
    def _requested_format_ids(info: dict[str, Any]) -> list[str]:
        for key in ("requested_formats", "requested_downloads", "entries"):
            requested = info.get(key)
            if not isinstance(requested, list):
                continue
            format_ids: list[str] = []
            for candidate in requested:
                if not isinstance(candidate, dict):
                    continue
                format_ids.extend(FormatResolutionPolicy._requested_format_ids(candidate))
            if format_ids:
                return format_ids
        candidate_id = info.get("format_id")
        return [str(candidate_id)] if candidate_id else []

    @staticmethod
    def _best_selector(container: str) -> str:
        if container == "webm":
            return "bestvideo[ext=webm][vcodec!=none]+bestaudio[ext=webm][acodec!=none]/best[ext=webm]/bestvideo+bestaudio/best"
        if container == "mkv":
            return "bestvideo*+bestaudio/best"
        return "bestvideo[ext=mp4][vcodec!=none]+bestaudio[ext=m4a][acodec!=none]/best[ext=mp4]/bestvideo+bestaudio/best"

    @staticmethod
    def _best_1080p_selector(container: str) -> str:
        if container == "webm":
            return "bestvideo[height<=1080][ext=webm][vcodec!=none]+bestaudio[ext=webm][acodec!=none]/best[height<=1080][ext=webm]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        if container == "mkv":
            return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        return "bestvideo[height<=1080][ext=mp4][vcodec!=none]+bestaudio[ext=m4a][acodec!=none]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"

    @staticmethod
    def _source_selector(container: str) -> str:
        if container == "webm":
            return "bestvideo[ext=webm][vcodec!=none]+bestaudio[ext=webm][acodec!=none]/best[ext=webm]/best"
        if container == "mkv":
            return "bestvideo*+bestaudio/best"
        return "bestvideo[ext=mp4][vcodec!=none]+bestaudio[ext=m4a][acodec!=none]/best[ext=mp4]/best"
