"""The frozen media-vault API contract: Pydantic (app/media_schemas.py) and TypeScript (types.ts) agree."""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app import media_schemas as contract
from app import schemas
from app.models import LibraryItem
from app.routers.admin_ai import AiConfigResponse, AiConfigUpdateRequest
from app.routers.admin_diagnostics import DiagnosticsResponse
from app.routers.local_playback import LocalPlaybackResponse
from app.services.library import LibraryService
from app.services.media_titles import FIELD_SOURCE_RANK

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
TS = (SRC / "types.ts").read_text() + (SRC / "api.ts").read_text()
BLOCK = re.compile(r"^export (?:interface (\w+)(?: extends (\w+))?|type (\w+) =) \{\n(.*?)\n\}", re.M | re.S)
MODELS = [
    v for k, v in vars(contract).items()
    if isinstance(v, type) and issubclass(v, BaseModel) and v.__module__ == contract.__name__ and not k.startswith("_")
]
LITERALS = {k: v for k, v in vars(contract).items() if typing.get_origin(v) is typing.Literal}


def _ts_fields() -> dict[str, set[str]]:
    blocks = {(iface or alias): (parent, set(re.findall(r"^  (\w+)\??:", body, re.M))) for iface, parent, alias, body in BLOCK.findall(TS)}

    def resolve(name: str) -> set[str]:
        parent, own = blocks[name]
        return own | (resolve(parent) if parent else set())

    return {name: resolve(name) for name in blocks}


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_contract_model_matches_its_typescript_interface(model: type[BaseModel]) -> None:
    ts = _ts_fields()
    assert model.__name__ in ts, f"types.ts lacks `export interface {model.__name__}`"
    assert set(model.model_fields) == ts[model.__name__]


@pytest.mark.parametrize("name", sorted(LITERALS))
def test_every_contract_literal_matches_its_typescript_union(name: str) -> None:
    match = re.search(rf"^export type {name} =(.*?);$", TS, re.M | re.S)
    assert match, f"types.ts lacks `export type {name}`"
    assert set(re.findall(r"'([^']*)'", match[1])) == set(typing.get_args(LITERALS[name]))


def test_existing_shapes_carry_the_new_fields_on_both_sides() -> None:
    ts = _ts_fields()
    extended = {
        "LibraryItem": (schemas.LibraryItemResponse, {"title_id", "extra_type", "progress"}),
        "PlaybackProgress": (schemas.PlaybackProgressResponse, {"title"}),
        "LocalSearchMatch": (schemas.LocalSearchMatchResponse, {"title_id", "start_ms", "media_title"}),
        "HouseholdCollection": (schemas.HouseholdCollectionResponse, {"rules", "titles"}),
        "LocalPlaybackOptions": (LocalPlaybackResponse, {"audio_tracks", "quality_heights", "loudness_gain_db", "free_video_slots"}),
        "AdminDiagnostics": (DiagnosticsResponse, {"playback", "artwork", "media_loading"}),
    }
    for ts_name, (model, fields) in extended.items():
        assert fields <= set(model.model_fields), model.__name__
        assert fields <= ts[ts_name], ts_name
    assert {"title", "moment"} <= set(typing.get_args(schemas.LocalSearchMatchResponse.model_fields["kind"].annotation))
    assert "rules" in schemas.HouseholdCollectionCreateRequest.model_fields
    assert set(typing.get_args(contract.FieldSource)) == set(FIELD_SOURCE_RANK)


def test_ai_features_disabled_carried_on_both_sides() -> None:
    """CONTRACT CHANGE: AppSettings.ai_features_disabled surfaces on the admin AI config models."""
    assert "ai_features_disabled" in AiConfigResponse.model_fields
    assert "ai_features_disabled" in AiConfigUpdateRequest.model_fields
    assert AiConfigResponse.model_fields["ai_features_disabled"].default_factory() == []
    ai_config_ts = re.search(r"export type AiConfig = \{(.*?)\};", TS, re.S)
    assert ai_config_ts and "ai_features_disabled" in ai_config_ts[1]
    # AiConfigUpdate derives from AiConfig via `Partial<Omit<AiConfig, ...>>`, so it
    # inherits the field automatically as long as the omit list doesn't name it.
    ai_config_update_ts = re.search(r"export type AiConfigUpdate = Partial<Omit<AiConfig, ((?:'\w+'\s*\|?\s*)+)>>", TS)
    assert ai_config_update_ts and "'ai_features_disabled'" not in ai_config_update_ts[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"ai_features_disabled": ["a" * 65]},  # over the 64-char key length
        {"ai_features_disabled": ["not/a-key"]},  # only [a-z0-9_], starting with a letter
        {"ai_features_disabled": ["Recap"]},  # lowercase only
        {"ai_features_disabled": [f"k{i}" for i in range(33)]},  # over the 32-entry cap
    ],
)
def test_ai_features_disabled_is_bounded(payload: dict) -> None:
    with pytest.raises(ValidationError):
        AiConfigUpdateRequest.model_validate(payload)


def test_ai_features_disabled_dedupes() -> None:
    parsed = AiConfigUpdateRequest.model_validate({"ai_features_disabled": ["recap", "asr", "recap"]})
    assert parsed.ai_features_disabled == ["recap", "asr"]


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (contract.EnrichmentBulkRequest, {"kinds": ["asr"]}),
        (contract.EnrichmentBulkRequest, {"title_id": "t", "root_id": "r", "kinds": ["asr"]}),
        (contract.EnrichmentBulkRequest, {"title_id": "t", "kinds": []}),
        (contract.PlaybackSessionRequest, {"subtitle": "s:../../etc/passwd"}),
        (contract.PlaybackSessionRequest, {"subtitle": "x:1"}),
        (contract.ProfanityPref, {"enabled": True, "words": ["a" * 41]}),
        (contract.MediaServerSettingsUpdate, {"metadata_language": "english"}),
        (contract.MediaServerSettingsUpdate, {"unknown": True}),
        (contract.MediaSegmentInput, {"type": "intro", "start_seconds": 30, "end_seconds": 10}),
        (contract.SmartCollectionRule, {"type": "movie", "conditions": [{"field": "path", "op": "is", "value": "/"}]}),
        (contract.SubtitleTranslateRequest, {"target_language": "Spanish"}),
    ],
)
def test_trust_boundary_bodies_are_rejected(model: type[BaseModel], payload: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_trust_boundary_bodies_accept_the_happy_path() -> None:
    contract.EnrichmentBulkRequest.model_validate({"root_id": "r", "kinds": ["segments", "captions"]})
    contract.PlaybackSessionRequest.model_validate({"subtitle": "t:0f8fad5b-d9cb-469f-a165-70867728950e", "audio_index": 1})
    contract.PlaybackSessionRequest.model_validate({"subtitle": "i:3", "max_height": 720})
    assert contract.ProfanityPref.model_validate({"enabled": True, "words": [" damn ", ""]}).words == ["damn"]


def test_library_items_serialize_their_title_link(db_factory) -> None:  # noqa: ANN001
    with db_factory() as session:
        item = LibraryItem(id="i1", title="Trailer", title_id="t1", extra_type="trailer", metadata_json={})
        session.add(item)
        session.commit()
        response = LibraryService(session).serialize(item)
    assert (response.title_id, response.extra_type) == ("t1", "trailer")
