"""Bounded photo inspection and sRGB WebP derivatives in a supervised child.

The web process never decodes untrusted megapixels.  This module's parent-side
helpers start a fresh Python child with address-space, CPU, output and wall-time
limits.  The child enables only the still-image formats Photos accepts, applies
EXIF orientation, converts embedded ICC profiles to sRGB, and writes metadata-
free derivatives into a private staging directory.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import resource
import shutil
import stat
import subprocess
import sys
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.persistence import read_regular_file
from app.services.parser_boundary import parser_command, parser_environment

WIDTHS = (400, 1200, 2400)
PIPELINE_VERSION = 1
MAX_PIXELS = 80_000_000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
TIMEOUT_SECONDS = 30
ADDRESS_SPACE_BYTES = 1536 * 1024 * 1024
CPU_SECONDS = 25
MAX_CONCURRENT_DECODERS = 2
ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/tiff", "image/heic", "image/heif", "image/avif"})
EXTENSIONS = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/tiff": "tiff",
    "image/heic": "heic", "image/heif": "heif", "image/avif": "avif",
}
_ADMISSION = threading.BoundedSemaphore(MAX_CONCURRENT_DECODERS)
CHILD_MESSAGE_BYTES = 64 * 1024


class PhotoDecodeError(ValueError):
    pass


def photos_root() -> Path:
    return settings.data_dir / "photos"


def original_path(storage_key: str) -> Path:
    root = photos_root().resolve(strict=False)
    path = root / storage_key
    # Keep the unresolved final path: resolve() would hide a malicious symlink
    # before the caller can reject it. Managed keys never contain dot segments.
    if not path.is_relative_to(root) or path.is_symlink():
        raise FileNotFoundError("Photo is unavailable.")
    return path


def source_key(content_hash: str, width: int) -> str:
    return hashlib.sha256(f"{content_hash}:photo:{PIPELINE_VERSION}:w{width}:autorotate:srgb:webp".encode()).hexdigest()


def derivative_path(key: str) -> Path:
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise FileNotFoundError("Photo rendition is unavailable.")
    return photos_root() / "derivatives" / key[:2] / f"{key}.webp"


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brands = {data[8:12], *(data[index:index + 4] for index in range(16, min(len(data), 128), 4))}
        if brands & {b"avif", b"avis"}:
            return "image/avif"
        if brands & {b"heic", b"heix", b"hevc", b"hevx"}:
            return "image/heic"
        if brands & {b"mif1", b"msf1"}:
            return "image/heif"
    return None


def _limits() -> None:
    # Some developer hosts expose a limit but reject changing it. The Linux
    # container accepts all three; wall time and pixel bounds remain mandatory
    # fallbacks elsewhere. A pre-exec exception must never abort the child.
    for kind, soft, hard in (
        (resource.RLIMIT_AS, ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_CPU, CPU_SECONDS, CPU_SECONDS + 1),
        (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES),
    ):
        try:
            _, current_hard = resource.getrlimit(kind)
            if current_hard != resource.RLIM_INFINITY:
                hard = min(hard, current_hard)
                soft = min(soft, hard)
            resource.setrlimit(kind, (soft, hard))
        except (OSError, ValueError):
            pass
    try:
        os.nice(19)
    except OSError:
        pass


def _run(mode: str, source: Path, target: Path | None = None) -> dict[str, Any]:
    package_root = Path(__file__).resolve().parents[2]
    source = source.absolute()
    try:
        if source.is_symlink() or not stat.S_ISREG(source.lstat().st_mode):
            raise PhotoDecodeError("The image could not be decoded safely.")
    except OSError as exc:
        raise PhotoDecodeError("The image could not be decoded safely.") from exc
    argv = [sys.executable, "-m", "app.services.photo_derivatives", mode, str(source)]
    if target is not None:
        argv.append(str(target.absolute()))
    try:
        with _ADMISSION, parser_command(argv, source, target) as command:
            process = subprocess.Popen(
                command, cwd=package_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=parser_environment(), close_fds=True,
            )
            assert process.stdout is not None
            output: list[bytes] = []
            ready = threading.Event()

            def drain() -> None:
                try:
                    output.append(process.stdout.read(CHILD_MESSAGE_BYTES + 1))
                finally:
                    ready.set()

            threading.Thread(target=drain, daemon=True, name="photo-derivative-output").start()
            try:
                deadline = time.monotonic() + TIMEOUT_SECONDS
                while process.poll() is None:
                    if ready.wait(0.05) and output and len(output[0]) > CHILD_MESSAGE_BYTES:
                        process.kill()
                        break
                    if time.monotonic() >= deadline:
                        process.kill()
                        break
                process.wait(timeout=5)
                if not ready.wait(1):
                    raise PhotoDecodeError("The image could not be decoded safely.")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
        raise PhotoDecodeError("Photo processing timed out or could not start.") from exc
    raw = output[0] if output else b""
    if process.returncode != 0 or len(raw) > CHILD_MESSAGE_BYTES:
        raise PhotoDecodeError("The image could not be decoded safely.")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhotoDecodeError("The image could not be decoded safely.") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PhotoDecodeError("The image could not be decoded safely.")
    return payload


def inspect(source: Path, content_type: str) -> dict[str, Any]:
    _require_content_type(content_type)
    return _validated_facts(_run("inspect", source), content_type)


def prepare(
    source: Path, content_type: str, content_hash: str,
) -> tuple[dict[str, Any], dict[int, tuple[str, int]]]:
    """Inspect and render an initial import with one bounded image decode."""
    _require_content_type(content_type)
    facts, products = _produce(source, content_hash, mode="prepare", content_type=content_type)
    assert facts is not None
    return facts, products


def render(source: Path, content_hash: str) -> dict[int, tuple[str, int]]:
    """Rebuild disposable derivatives for an already-cataloged photo."""
    _facts, products = _produce(source, content_hash, mode="render")
    return products


def _produce(
    source: Path, content_hash: str, *, mode: str, content_type: str | None = None,
) -> tuple[dict[str, Any] | None, dict[int, tuple[str, int]]]:
    if not _valid_hash(content_hash) or not hmac.compare_digest(_source_hash(source), content_hash):
        raise PhotoDecodeError("Photo content changed while it was being processed.")
    from app.services import photo_cache
    with photo_cache.reserve_set(content_hash) as reservation:
        staging = photos_root() / ".staging" / reservation.id
        staging.mkdir(mode=0o700, parents=True)
        try:
            payload = _run(mode, source, staging)
            if not hmac.compare_digest(_source_hash(source), content_hash):
                raise PhotoDecodeError("Photo content changed while it was being processed.")
            facts = _validated_facts(payload, content_type) if content_type is not None else None
            staged_products = _validated_products(payload, staging, content_hash)
            return facts, photo_cache.publish_set(reservation, staged_products)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or _text(value, limit) != value:
        raise PhotoDecodeError("The image metadata was invalid.")
    return value


def _require_content_type(content_type: str) -> None:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise PhotoDecodeError("Photos must be JPEG, PNG, WebP, TIFF, AVIF or HEIC/HEIF images.")


def _validated_facts(payload: dict[str, Any], content_type: str) -> dict[str, Any]:
    _require_content_type(content_type)
    reported_type = payload.get("content_type")
    # HEIC and generic HEIF use the same decoder and may report either brand.
    if not isinstance(reported_type, str) or (
        reported_type != content_type and {reported_type, content_type} != {"image/heic", "image/heif"}
    ):
        raise PhotoDecodeError("The image content does not match its container.")
    width, height = payload.get("width"), payload.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise PhotoDecodeError("The image dimensions are too large.")
    orientation = payload.get("orientation")
    if type(orientation) is not int or not 1 <= orientation <= 8:
        raise PhotoDecodeError("The image metadata was invalid.")
    captured_at = payload.get("captured_at")
    if captured_at is not None:
        try:
            parsed = datetime.fromisoformat(captured_at)
        except (TypeError, ValueError) as exc:
            raise PhotoDecodeError("The image metadata was invalid.") from exc
        if len(captured_at) != 19 or parsed.isoformat() != captured_at:
            raise PhotoDecodeError("The image metadata was invalid.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise PhotoDecodeError("The image metadata was invalid.")
    iso = metadata.get("iso")
    if iso is not None and (type(iso) is not int or iso < 0 or iso > 2_147_483_647):
        raise PhotoDecodeError("The image metadata was invalid.")
    embedded_icc = metadata.get("has_embedded_icc")
    if type(embedded_icc) is not bool:
        raise PhotoDecodeError("The image metadata was invalid.")
    return {
        "captured_at": captured_at,
        "captured_at_raw": _optional_text(payload.get("captured_at_raw"), 64),
        "camera_make": _optional_text(payload.get("camera_make"), 120),
        "camera_model": _optional_text(payload.get("camera_model"), 120),
        "orientation": orientation,
        "metadata": {
            "lens": _optional_text(metadata.get("lens"), 160),
            "iso": iso,
            "focal_length": _optional_text(metadata.get("focal_length"), 32),
            "aperture": _optional_text(metadata.get("aperture"), 32),
            "exposure_time": _optional_text(metadata.get("exposure_time"), 32),
            "has_embedded_icc": embedded_icc,
        },
        "content_type": reported_type,
        "width": width,
        "height": height,
    }


def _validated_products(
    payload: dict[str, Any], staging: Path, content_hash: str,
) -> dict[int, tuple[Path, str, int]]:
    products = payload.get("products")
    if not isinstance(products, dict) or set(products) != {str(width) for width in WIDTHS}:
        raise PhotoDecodeError("Photo derivatives were incomplete.")
    validated: dict[int, tuple[Path, str, int]] = {}
    for width in WIDTHS:
        staged = staging / f"{width}.webp"
        key = source_key(content_hash, width)
        try:
            descriptor = os.open(
                staged, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                header = handle.read(16)
                os.fsync(handle.fileno())
        except OSError as exc:
            raise PhotoDecodeError("Photo derivatives were incomplete.") from exc
        if (
            products.get(str(width)) != staged.name
            or not stat.S_ISREG(info.st_mode)
            or info.st_size <= 0
            or info.st_size > MAX_OUTPUT_BYTES
            or sniff(header) != "image/webp"
        ):
            raise PhotoDecodeError("Photo derivatives were incomplete.")
        validated[width] = (staged, key, info.st_size)
    return validated


def read(key: str) -> bytes:
    return read_regular_file(derivative_path(key), MAX_OUTPUT_BYTES)


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_SOURCE_BYTES:
            raise PhotoDecodeError("The image could not be decoded safely.")
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pillow() -> tuple[Any, Any, Any]:
    from PIL import Image, ImageCms, ImageOps
    from PIL.Image import DecompressionBombWarning

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", DecompressionBombWarning)
    return Image, ImageCms, ImageOps


def _opened(path: Path):  # noqa: ANN202 - Pillow's plugin-specific image subclasses
    Image, _ImageCms, _ImageOps = _pillow()
    image = Image.open(path, formats=("JPEG", "PNG", "WEBP", "TIFF", "AVIF", "HEIF"))
    image.seek(0)
    image.load()
    if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_PIXELS:
        raise ValueError("pixels")
    return image


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\x00", " ").split())[:limit]
    return cleaned or None


def _captured(raw: object) -> tuple[str | None, str | None]:
    value = _text(raw, 64)
    if value is None:
        return None, None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], pattern).isoformat(), value
        except ValueError:
            continue
    return None, value


def _facts(path: Path) -> tuple[Any, dict[str, Any]]:
    Image, _ImageCms, ImageOps = _pillow()
    image = _opened(path)
    exif = image.getexif()
    orientation = exif.get(274, 1)
    orientation = orientation if type(orientation) is int and 1 <= orientation <= 8 else 1
    captured_at, captured_raw = _captured(exif.get(36867) or exif.get(36868) or exif.get(306))
    upright = ImageOps.exif_transpose(image)
    content_type = Image.MIME.get(image.format)
    if image.format == "HEIF":
        with path.open("rb") as source:
            header = source.read(128)
        content_type = sniff(header)
    iso = exif.get(34855)
    iso = iso if type(iso) is int and 0 <= iso <= 2_147_483_647 else None
    metadata = {
        "captured_at": captured_at, "captured_at_raw": captured_raw,
        "camera_make": _text(exif.get(271), 120), "camera_model": _text(exif.get(272), 120),
        "orientation": orientation,
        "metadata": {
            "lens": _text(exif.get(42036), 160),
            "iso": iso,
            "focal_length": _text(str(exif.get(37386)), 32) if exif.get(37386) is not None else None,
            "aperture": _text(str(exif.get(33437)), 32) if exif.get(33437) is not None else None,
            "exposure_time": _text(str(exif.get(33434)), 32) if exif.get(33434) is not None else None,
            "has_embedded_icc": isinstance(image.info.get("icc_profile"), bytes),
        },
        "content_type": content_type, "width": upright.width, "height": upright.height,
    }
    return upright, metadata


def _srgb(image):  # noqa: ANN001,ANN202 - Pillow image in the child only
    Image, ImageCms, _ImageOps = _pillow()
    profile = image.info.get("icc_profile")
    if isinstance(profile, bytes) and profile:
        try:
            image = ImageCms.profileToProfile(image, io.BytesIO(profile), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):
            image = image.convert("RGB")
    elif image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    return image.convert("RGB")


def _child(mode: str, source: Path, target: Path | None) -> dict[str, Any]:
    image, facts = _facts(source)
    if mode == "inspect":
        return {"ok": True, **facts}
    if mode not in {"prepare", "render"} or target is None:
        raise ValueError("mode")
    image = _srgb(image)
    products: dict[str, str] = {}
    Image, _ImageCms, _ImageOps = _pillow()
    for width in WIDTHS:
        copy = image.copy()
        copy.thumbnail((width, width * 10), Image.Resampling.LANCZOS, reducing_gap=3.0)
        output = target / f"{width}.webp"
        copy.save(output, format="WEBP", quality=82, method=4, exif=b"", icc_profile=None)
        products[str(width)] = output.name
    payload = {"ok": True, "products": products}
    if mode == "prepare":
        payload.update(facts)
    return payload


if __name__ == "__main__":
    try:
        if os.name == "posix":
            _limits()
        command = sys.argv[1]
        source_path = Path(sys.argv[2])
        target_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
        print(json.dumps(_child(command, source_path, target_path), separators=(",", ":")))
    except Exception:
        print(json.dumps({"ok": False}))
        raise SystemExit(1)
