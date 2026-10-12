"""On-device model catalog.

``backend/app/model_catalog.json`` is read-only data shipped in the image and the ONLY source of model
URLs, file names and checksums; nothing here is ever built from request input. Every file URL is pinned
to an exact upstream revision. A model's files live in ``<data_dir>/models/<id>/``, and it counts as
installed only while its ``.ready`` marker (written by model_downloads after sha256 verification) lists
exactly this catalog's files and checksums, so a catalog upgrade makes the model download again.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from app.config import settings

CATALOG_PATH = Path(__file__).resolve().parents[1] / "model_catalog.json"
READY_MARKER = ".ready"
WANTED_MARKER = ".wanted"
LOCAL_PREFIX = "local:"
ROLES = ("search", "speech")
# The ai_features_disabled keys whose work uses each role's model.
FEATURES: dict[str, tuple[str, ...]] = {"search": ("semantic_search",), "speech": ("subtitles_from_speech", "sync")}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PINNED_PATH = re.compile(r"^/[^/]+/[^/]+/resolve/[0-9a-f]{40}/[^/]+$")

Role = Literal["search", "speech"]


class CatalogError(ValueError):
    """The catalog is malformed; Lumina refuses all of it rather than guess."""


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True, eq=False)
class CatalogModel:
    id: str
    role: Role
    default: bool
    name: str
    description: str
    licence: str
    ram_bytes: int
    files: tuple[ModelFile, ...]
    engine: dict[str, Any] = field(default_factory=dict)  # search: dimensions, pooling, query/document prefix; speech: language

    @property
    def size_bytes(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def choice_id(self) -> str:
        """The id local vectors (SearchEmbedding.model_id) and speech jobs (AsrJob.model_id) carry."""
        return LOCAL_PREFIX + self.id


@dataclass(frozen=True)
class Catalog:
    hosts: frozenset[str]
    cdn_host_suffixes: tuple[str, ...]
    models: tuple[CatalogModel, ...]

    def get(self, model_id: str) -> CatalogModel | None:
        return next((model for model in self.models if model.id == model_id), None)

    def default_for(self, role: str) -> CatalogModel:
        return next(model for model in self.models if model.role == role and model.default)

    def allows_host(self, host: str) -> bool:
        """A catalog host, or one of its CDN hosts (the only redirect targets a download follows)."""
        host = host.lower()
        return host in self.hosts or any(host.endswith(suffix) for suffix in self.cdn_host_suffixes)


def _file(raw: dict, hosts: frozenset[str]) -> ModelFile:
    file = ModelFile(name=raw["name"], url=raw["url"], sha256=raw["sha256"], size=raw["size"])
    if not isinstance(file.name, str) or not FILE_PATTERN.fullmatch(file.name) or file.name.endswith(".part"):
        raise CatalogError("Model file names must be plain names")
    if not isinstance(file.sha256, str) or not SHA256_PATTERN.fullmatch(file.sha256):
        raise CatalogError("Model files need a sha256")
    if type(file.size) is not int or file.size <= 0:
        raise CatalogError("Model files need a size in bytes")
    url = urlsplit(file.url)
    host = (url.hostname or "").lower()
    secure = url.scheme == "https" or (url.scheme == "http" and host == "127.0.0.1")  # plain http only for the test fake
    if host not in hosts or not secure or url.query or url.username or not PINNED_PATH.fullmatch(url.path):
        raise CatalogError("Model file URLs must be revision-pinned https URLs on a catalog host")
    return file


def _model(raw: dict, hosts: frozenset[str]) -> CatalogModel:
    model = CatalogModel(
        id=raw["id"], role=raw["role"], default=raw.get("default", False) is True, name=raw["name"],
        description=raw["description"], licence=raw["licence"], ram_bytes=raw["ram_bytes"],
        files=tuple(_file(entry, hosts) for entry in raw["files"]), engine=dict(raw.get("engine") or {}),
    )
    if not isinstance(model.id, str) or not ID_PATTERN.fullmatch(model.id):
        raise CatalogError("Model ids must be short lowercase names")
    if model.role not in ROLES:
        raise CatalogError("A model's role is search or speech")
    if not model.files or len({file.name for file in model.files}) != len(model.files):
        raise CatalogError("A model needs distinct files")
    if type(model.ram_bytes) is not int or model.ram_bytes <= 0:
        raise CatalogError("A model needs a RAM estimate in bytes")
    if model.role == "search" and (type(model.engine.get("dimensions")) is not int or len(model.files) != 1):
        raise CatalogError("A search model is one GGUF file with known dimensions")
    return model


def parse(data: dict) -> Catalog:
    try:
        hosts = frozenset(host.lower() for host in data["hosts"])
        suffixes = tuple(suffix.lower() for suffix in data["cdn_host_suffixes"])
        models = tuple(_model(entry, hosts) for entry in data["models"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise CatalogError("The model catalog is malformed") from exc
    if any(not suffix.startswith(".") for suffix in suffixes):
        raise CatalogError("CDN host suffixes start with a dot")
    if len({model.id for model in models}) != len(models):
        raise CatalogError("Model ids must be unique")
    for role in ROLES:
        if sum(model.default for model in models if model.role == role) != 1:
            raise CatalogError(f"The catalog needs exactly one default {role} model")
    return Catalog(hosts=hosts, cdn_host_suffixes=suffixes, models=models)


@cache
def _load(path: Path) -> Catalog:
    return parse(json.loads(path.read_text(encoding="utf-8")))


def catalog() -> Catalog:
    """The catalog at ``CATALOG_PATH`` (parsed once per path; tests point the path at their own file)."""
    return _load(CATALOG_PATH)


def active(role: str, configured: str | None) -> CatalogModel:
    """The admin's chosen model for ``role`` (``AppSettings.local_*_model``), else the catalog default."""
    model = catalog().get(configured or "")
    return model if model is not None and model.role == role else catalog().default_for(role)


def from_choice_id(choice_id: str) -> CatalogModel | None:
    """The catalog model behind a ``local:`` model id; None for an external model name."""
    return catalog().get(choice_id[len(LOCAL_PREFIX):]) if choice_id.startswith(LOCAL_PREFIX) else None


def models_root() -> Path:
    return settings.data_dir / "models"


def model_dir(model: CatalogModel) -> Path:
    return models_root() / model.id


def is_installed(model: CatalogModel) -> bool:
    """Every catalog file is in place at its size, and the ready marker lists exactly these checksums."""
    directory = model_dir(model)
    try:
        marker = json.loads((directory / READY_MARKER).read_text(encoding="utf-8"))
        sizes_match = all((directory / file.name).stat().st_size == file.size for file in model.files)
    except (OSError, ValueError):
        return False
    return sizes_match and marker == {file.name: file.sha256 for file in model.files}
