"""Part C wire contract: Pydantic (app/schemas.py) and TypeScript (types.ts) agree (live and YouTube plans, contracts §3)."""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from app import schemas

TS = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts").read_text()
BLOCK = re.compile(r"^export interface (\w+)(?: extends (\w+))? \{\n(.*?)\n\}", re.M | re.S)
NEW_MODELS = [
    schemas.RemoteProgressAnnotation, schemas.ChannelLiveResponse, schemas.ChannelHeaderResponse, schemas.ChannelPageResponse,
    schemas.ChannelResolveRequest, schemas.ChannelResolveResponse, schemas.LibraryChannelResponse,
]


def ts_fields(name: str) -> set[str]:
    blocks = {interface: (parent, body) for interface, parent, body in BLOCK.findall(TS)}
    assert name in blocks, f"types.ts lacks `export interface {name}`"
    parent, body = blocks[name]
    own = set(re.findall(r"^  (\w+)\??:", body, re.M))
    return own | (ts_fields(parent) if parent else set())


@pytest.mark.parametrize("model", NEW_MODELS, ids=lambda model: model.__name__)
def test_every_part_c_model_matches_its_typescript_interface(model: type[BaseModel]) -> None:
    assert set(model.model_fields) == ts_fields(model.__name__)


def test_remote_entries_carry_the_member_markers_on_both_sides() -> None:
    assert {"saved_item_id", "progress"} <= set(schemas.YouTubeSearchResult.model_fields) & ts_fields("YouTubeSearchResult")
    preview = {"channel_id", "channel_url", "view_count", "saved_item_id", "progress"}
    assert preview <= set(schemas.PreviewEntry.model_fields) & ts_fields("PreviewEntry")


def test_the_channel_tab_union_matches() -> None:
    match = re.search(r"^export type ChannelTab =(.*?);$", TS, re.M | re.S)
    assert match, "types.ts lacks `export type ChannelTab`"
    assert set(re.findall(r"'([^']*)'", match[1])) == set(typing.get_args(schemas.ChannelTab))


def test_a_channel_page_and_a_library_channel_validate() -> None:
    page = schemas.ChannelPageResponse.model_validate({
        "channel": {"id": "UCabcdefghijklmnopqrstuv", "name": "Harbor Films", "url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"},
        "tab": "streams", "fetched_at": "2026-09-29T20:42:00Z",
    })
    assert page.entries == [] and page.restricted is False and page.channel.tabs == [] and page.channel.verified is False
    with pytest.raises(ValueError):
        schemas.ChannelPageResponse.model_validate({**page.model_dump(), "tab": "live"})
    with pytest.raises(ValueError):
        schemas.ChannelResolveRequest.model_validate({"url": "https://www.youtube.com/@x", "extra": 1})
    entry = schemas.YouTubeSearchResult(id="v", progress={"position_seconds": 12.5, "completed": False})
    assert entry.progress == schemas.RemoteProgressAnnotation(position_seconds=12.5, duration_seconds=None, completed=False)
