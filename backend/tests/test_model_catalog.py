"""The read-only catalog is the only source of model URLs, names and checksums."""
from __future__ import annotations

import json
import re

import pytest

from app.config import settings
from app.services import model_catalog
from app.services.model_catalog import CatalogError, parse
from model_support import REV, default_pair, install, use_catalog

PINNED = re.compile(r"^https://huggingface\.co/[\w.-]+/[\w.-]+/resolve/[0-9a-f]{40}/[\w.-]+$")


def test_shipped_catalog_pins_every_file() -> None:
    catalog = model_catalog.catalog()
    assert [model.id for model in catalog.models] == [
        "granite-embedding-97m-multilingual-r2-q8", "nomic-embed-text-v1.5-q8",
        "faster-whisper-small-int8", "faster-whisper-large-v3-turbo-int8",
    ]
    assert catalog.default_for("search").id == "granite-embedding-97m-multilingual-r2-q8"
    assert catalog.default_for("speech").id == "faster-whisper-small-int8"
    assert catalog.allows_host("huggingface.co") and catalog.allows_host("us.aws.cdn.hf.co")
    assert not catalog.allows_host("example.com") and not catalog.allows_host("hf.co.example.com")
    for model in catalog.models:
        assert model.licence in {"Apache-2.0", "MIT"} and model.ram_bytes > 0
        assert all(PINNED.fullmatch(file.url) for file in model.files), model.id
    granite = catalog.get("granite-embedding-97m-multilingual-r2-q8")
    assert granite.size_bytes == 115_061_088
    assert granite.files[0].sha256 == "25155b89638e501ac33495fa278d551d7545e1e2f62722a499bba1f064c080f2"
    assert granite.engine["dimensions"] == 384
    nomic = catalog.get("nomic-embed-text-v1.5-q8")
    assert (nomic.engine["query_prefix"], nomic.engine["document_prefix"]) == ("search_query: ", "search_document: ")
    small = catalog.get("faster-whisper-small-int8")
    assert small.size_bytes == 486_212_372
    assert [file.name for file in small.files] == ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    turbo = catalog.get("faster-whisper-large-v3-turbo-int8")
    assert turbo.size_bytes == 1_621_665_983 and "preprocessor_config.json" in {file.name for file in turbo.files}


def test_choice_ids_and_the_active_model(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    catalog = use_catalog(monkeypatch, tmp_path, default_pair())
    search, speech = catalog.default_for("search"), catalog.default_for("speech")
    assert search.choice_id == "local:test-search"
    assert model_catalog.from_choice_id("local:test-search") is search
    assert model_catalog.from_choice_id("test-search") is None  # an external model name never resolves locally
    assert model_catalog.from_choice_id("local:nope") is None
    assert model_catalog.active("search", None) is search
    assert model_catalog.active("search", "no-such-model") is search
    assert model_catalog.active("search", speech.id) is search  # a speech id never becomes the search model


def _valid() -> dict:
    return {"hosts": ["127.0.0.1"], "cdn_host_suffixes": [], "models": default_pair()}


def _first_file(data: dict) -> dict:
    return data["models"][0]["files"][0]


BENDS = {
    "traversal file name": lambda d: _first_file(d).update(name="../escape.bin"),
    "hidden file name": lambda d: _first_file(d).update(name=".ready"),
    "part file name": lambda d: _first_file(d).update(name="weights.part"),
    "unpinned revision": lambda d: _first_file(d).update(url=_first_file(d)["url"].replace(REV, "main")),
    "query string": lambda d: _first_file(d).update(url=_first_file(d)["url"] + "?download=1"),
    "foreign host": lambda d: _first_file(d).update(url=f"https://example.com/org/repo/resolve/{REV}/w.bin"),
    "plain http off loopback": lambda d: (
        d.update(hosts=["127.0.0.1", "huggingface.co"]), _first_file(d).update(url=f"http://huggingface.co/org/repo/resolve/{REV}/w.bin"),
    ),
    "bad sha256": lambda d: _first_file(d).update(sha256="abc"),
    "zero size": lambda d: _first_file(d).update(size=0),
    "string size": lambda d: _first_file(d).update(size="12"),
    "bad id": lambda d: d["models"][0].update(id="Bad/Id"),
    "unknown role": lambda d: d["models"][0].update(role="chat"),
    "duplicate id": lambda d: d["models"][1].update(id=d["models"][0]["id"]),
    "two search defaults": lambda d: d["models"].append(dict(d["models"][0], id="second")),
    "no speech default": lambda d: d["models"][1].update(default=False),
    "search without dimensions": lambda d: d["models"][0].update(engine={}),
    "missing licence": lambda d: d["models"][0].pop("licence"),
    "suffix without a dot": lambda d: d.update(cdn_host_suffixes=["hf.co"]),
}


@pytest.mark.parametrize("name", sorted(BENDS))
def test_malformed_catalogs_are_refused(name: str) -> None:
    data = _valid()
    BENDS[name](data)
    with pytest.raises(CatalogError):
        parse(data)


def test_installed_only_with_a_matching_ready_marker(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    speech = use_catalog(monkeypatch, tmp_path, default_pair()).default_for("speech")
    directory = model_catalog.model_dir(speech)
    assert directory == settings.data_dir / "models" / "test-speech"
    assert not model_catalog.is_installed(speech)
    install(speech)
    assert model_catalog.is_installed(speech)
    (directory / speech.files[0].name).write_bytes(b"short")  # truncated after install
    assert not model_catalog.is_installed(speech)
    install(speech)
    (directory / model_catalog.READY_MARKER).write_text(json.dumps({file.name: "0" * 64 for file in speech.files}))
    assert not model_catalog.is_installed(speech)  # a marker from an older catalog: download again
    (directory / model_catalog.READY_MARKER).write_text("not json")
    assert not model_catalog.is_installed(speech)


def test_the_smoke_catalog_is_valid_tiny_and_pinned() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "models_smoke_catalog.json"
    catalog = parse(json.loads(path.read_text()))
    assert sum(model.size_bytes for model in catalog.models) < 100_000_000  # CI-friendly
    assert catalog.default_for("search").engine == {"dimensions": 384, "pooling": "mean", "query_prefix": "", "document_prefix": ""}
    assert catalog.default_for("speech").engine == {"language": "en"}
    assert all(PINNED.fullmatch(file.url) for model in catalog.models for file in model.files)
