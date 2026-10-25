#!/usr/bin/env python3
"""Regenerate, synchronize, and validate the approved Lumina icon family.

The editable source of truth is assets/icons/source/icons.json. The bundled
assets/icons/source/build.py exporter remains the only geometry generator; this
wrapper mirrors its generated products into each application. Both commands
require only the Python standard library. Passing --render to check also uses
macOS Core Graphics to rasterize every SVG at the approved sizes.

Usage:
    python3 scripts/lumina_icons.py sync
    python3 scripts/lumina_icons.py check [--render]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib


REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "assets/icons"
NATIVE_CATALOG = PACKAGE / "native/LuminaMediaIcons.xcassets"
NATIVE_HELPER = PACKAGE / "native/LuminaIcon.swift"
WEB_HELPER = PACKAGE / "web/LuminaIcon.tsx"
WEB_SPRITE = PACKAGE / "web/lumina-media-sprite.svg"

PRODUCTION_CATALOG = REPO / "apple/Assets/LuminaMediaIcons.xcassets"
PRODUCTION_NATIVE_HELPER = REPO / "apple/App/Shared/LuminaIcon.swift"
PRODUCTION_WEB_HELPER = REPO / "frontend/src/ui/LuminaIcon.tsx"
PRODUCTION_WEB_SPRITE = REPO / "frontend/public/lumina-media-sprite.svg"
PRODUCTION_WEB_MODULE_SPRITE = REPO / "frontend/src/ui/lumina-media-sprite.svg"

EXPECTED_DESIGNS = 104
EXPECTED_SOLIDS = 40
EXPECTED_MASTERS = 144
EXPECTED_PREVIEWS = {
    "lumina-media-icons-overview.png": (1800, 1490),
    "lumina-media-icons-catalog-light.png": (1600, 3181),
    "lumina-media-icons-catalog-dark.png": (1600, 3181),
    "lumina-media-icons-solid-variants.png": (1600, 1430),
    "lumina-media-icons-size-check.png": (1680, 3701),
}
ALLOWED_SVG_ELEMENTS = {
    "svg", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "g"
}


class ContractError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ContractError(message)


def load_source(root: Path = PACKAGE) -> dict:
    return json.loads((root / "source/icons.json").read_text())


def native_name(item: dict) -> str:
    fallback = "Lumina" + "".join(
        part[:1].upper() + part[1:] for part in item["id"].split("-")
    )
    return item.get("nativeName", fallback)


def expected_generated_paths(data: dict) -> tuple[set[Path], set[Path]]:
    package_files = {
        Path("manifest.json"),
        Path("catalog.html"),
        Path("native/LuminaIcon.swift"),
        Path("native/LuminaMediaIcons.xcassets/Contents.json"),
        Path("web/LuminaIcon.tsx"),
        Path("web/lumina-media-sprite.svg"),
    }
    master_paths: set[Path] = set()
    for item in data["icons"]:
        slug = item["id"]
        outline = Path("svg") / f"{slug}.svg"
        package_files.add(outline)
        master_paths.add(outline)
        name = native_name(item)
        image_set = Path("native/LuminaMediaIcons.xcassets") / f"{name}.imageset"
        package_files.update({image_set / f"{name}.svg", image_set / "Contents.json"})
        if "solid" in item:
            folder = "selected" if item.get("legacySelected") else "solid"
            solid = Path("svg") / folder / f"{slug}.svg"
            package_files.add(solid)
            master_paths.add(solid)
            suffix = "Selected" if item.get("legacySelected") else "Solid"
            solid_name = name + suffix
            solid_set = Path("native/LuminaMediaIcons.xcassets") / f"{solid_name}.imageset"
            package_files.update({solid_set / f"{solid_name}.svg", solid_set / "Contents.json"})
    return package_files, master_paths


def run_builder(root: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(root / "source/build.py")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        fail(f"icon exporter failed:\n{result.stderr[-4000:]}")


def replace_tree(source: Path, destination: Path) -> None:
    if destination != PRODUCTION_CATALOG:
        fail(f"refusing to replace unexpected generated directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lumina-icon-sync-", dir=destination.parent) as temp:
        staged = Path(temp) / destination.name
        shutil.copytree(source, staged)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staged, destination)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sync() -> None:
    run_builder(PACKAGE)
    replace_tree(NATIVE_CATALOG, PRODUCTION_CATALOG)
    copy_file(NATIVE_HELPER, PRODUCTION_NATIVE_HELPER)
    copy_file(WEB_HELPER, PRODUCTION_WEB_HELPER)
    copy_file(WEB_SPRITE, PRODUCTION_WEB_SPRITE)
    copy_file(WEB_SPRITE, PRODUCTION_WEB_MODULE_SPRITE)
    data = load_source()
    solid_count = sum("solid" in item for item in data["icons"])
    print(
        f"Synchronized {len(data['icons'])} designs, {solid_count} solid alternates, "
        f"and {len(data['icons']) + solid_count} SVG masters."
    )


def files_under(root: Path, relative: Path) -> set[Path]:
    base = root / relative
    if not base.exists():
        return set()
    return {path.relative_to(root) for path in base.rglob("*") if path.is_file()}


def compare_file(left: Path, right: Path, label: str) -> None:
    if not left.is_file() or not right.is_file():
        fail(f"missing {label}: {left if not left.is_file() else right}")
    if left.read_bytes() != right.read_bytes():
        fail(f"out-of-date {label}: run `python3 scripts/lumina_icons.py sync`")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_svg(path: Path, *, native: bool) -> None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        fail(f"invalid SVG XML in {path}: {error}")
    if local_name(root.tag) != "svg" or root.attrib.get("viewBox") != "0 0 24 24":
        fail(f"wrong SVG canvas in {path}")
    if root.attrib.get("width") != "24" or root.attrib.get("height") != "24":
        fail(f"wrong SVG dimensions in {path}")
    if root.attrib.get("fill") != "none" or root.attrib.get("stroke-width") != "1.75":
        fail(f"wrong SVG paint contract in {path}")
    expected_stroke = "#000000" if native else "currentColor"
    if root.attrib.get("stroke") != expected_stroke:
        fail(f"wrong SVG foreground in {path}; expected {expected_stroke}")
    drawable_count = 0
    for node in root.iter():
        name = local_name(node.tag)
        if name not in ALLOWED_SVG_ELEMENTS:
            fail(f"unsupported SVG element {name} in {path}")
        if name not in {"svg", "g"}:
            drawable_count += 1
        for key, value in node.attrib.items():
            if (
                local_name(key).lower().startswith("on")
                or "url(" in value
                or "href" in local_name(key)
            ):
                fail(f"external or executable SVG content in {path}")
    if drawable_count == 0:
        fail(f"empty SVG artwork in {path}")


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        fail(f"invalid PNG output: {path}")
    return struct.unpack(">II", header[16:24])


def png_visible_pixel_count(path: Path) -> int:
    content = path.read_bytes()
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", content[16:29]
    )
    if (depth, color_type, compression, filtering, interlace) != (8, 6, 0, 0, 0):
        fail(f"unsupported rendered PNG format in {path}")
    position = 8
    compressed = bytearray()
    while position < len(content):
        length = struct.unpack(">I", content[position : position + 4])[0]
        chunk_type = content[position + 4 : position + 8]
        chunk = content[position + 8 : position + 8 + length]
        position += 12 + length
        if chunk_type == b"IDAT":
            compressed.extend(chunk)
        if chunk_type == b"IEND":
            break
    raw = zlib.decompress(compressed)
    stride = width * 4
    previous = bytearray(stride)
    offset = 0
    visible = 0

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        distances = (
            abs(estimate - left),
            abs(estimate - above),
            abs(estimate - upper_left),
        )
        return (left, above, upper_left)[distances.index(min(distances))]

    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type > 4:
            fail(f"invalid PNG scanline filter in {path}")
        for index, value in enumerate(row):
            left = row[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0
            predictors = (
                0,
                left,
                above,
                (left + above) // 2,
                paeth(left, above, upper_left),
            )
            row[index] = (value + predictors[filter_type]) & 0xFF
        visible += sum(alpha > 0 for alpha in row[3::4])
        previous = row
    return visible


def validate_rendering(master_paths: set[Path]) -> None:
    renderer = shutil.which("sips")
    if renderer is None:
        fail("--render requires the macOS Core Graphics image tool (`sips`)")
    with tempfile.TemporaryDirectory(prefix="lumina-icon-render-") as temp:
        temp_root = Path(temp)
        flat_inputs = temp_root / "input"
        flat_inputs.mkdir()
        flattened: list[Path] = []
        for index, relative in enumerate(sorted(master_paths)):
            target = flat_inputs / f"{index:03d}-{relative.stem}.svg"
            shutil.copy2(PACKAGE / relative, target)
            flattened.append(target)
        for size in (20, 24, 28, 96):
            output = temp_root / str(size)
            output.mkdir()
            result = subprocess.run(
                [
                    renderer,
                    "-s",
                    "format",
                    "png",
                    "--resampleHeightWidth",
                    str(size),
                    str(size),
                    *map(str, flattened),
                    "--out",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                fail(f"Core Graphics SVG rendering failed at {size}px:\n{result.stderr[-2000:]}")
            renders = sorted(output.glob("*.png"))
            if len(renders) != EXPECTED_MASTERS:
                fail(f"rendered {len(renders)} of {EXPECTED_MASTERS} SVGs at {size}px")
            for render in renders:
                if png_dimensions(render) != (size, size):
                    fail(f"wrong rendered dimensions for {render}")
                visible = png_visible_pixel_count(render)
                if visible == 0 or visible == size * size:
                    fail(f"empty artwork or opaque background in rendered SVG {render}")


def validate_source(data: dict) -> None:
    icons = data.get("icons", [])
    if len(icons) != EXPECTED_DESIGNS:
        fail(f"expected {EXPECTED_DESIGNS} designs, found {len(icons)}")
    solid_count = sum("solid" in item for item in icons)
    if solid_count != EXPECTED_SOLIDS:
        fail(f"expected {EXPECTED_SOLIDS} solid alternates, found {solid_count}")
    identifiers = [item.get("id") for item in icons]
    if len(set(identifiers)) != len(identifiers):
        fail("duplicate source icon identifiers")
    if any(
        not isinstance(identifier, str)
        or not re.fullmatch(r"[a-z][a-z0-9-]+", identifier)
        for identifier in identifiers
    ):
        fail("invalid source icon identifier")
    if data.get("viewBox") != "0 0 24 24" or data.get("strokeWidth") != 1.75:
        fail("source grid or structural stroke changed")
    if not data.get("categories") or any(
        item.get("category") not in data["categories"] for item in icons
    ):
        fail("source category contract is incomplete")
    generated_names: list[str] = []
    for item in icons:
        if not all(item.get(key) for key in ("label", "meaning", "outline")):
            fail(f"incomplete source metadata for {item.get('id')}")
        generated_names.append(native_name(item))
        if "solid" in item:
            suffix = "Selected" if item.get("legacySelected") else "Solid"
            generated_names.append(native_name(item) + suffix)
    if len(set(generated_names)) != EXPECTED_MASTERS:
        fail("duplicate generated native asset names")


def validate_generated_inventory(
    data: dict, package_files: set[Path], master_paths: set[Path]
) -> None:
    actual_svg = files_under(PACKAGE, Path("svg"))
    if actual_svg != master_paths:
        fail(
            "SVG master inventory drift: "
            f"missing={sorted(master_paths - actual_svg)}, "
            f"extra={sorted(actual_svg - master_paths)}"
        )
    expected_native = {
        path
        for path in package_files
        if path.parts[:2] == ("native", "LuminaMediaIcons.xcassets")
    }
    actual_native = files_under(PACKAGE, Path("native/LuminaMediaIcons.xcassets"))
    if actual_native != expected_native:
        fail("canonical native asset catalog inventory drift")
    expected_web = {
        Path("web/LuminaIcon.tsx"),
        Path("web/lumina-media-sprite.svg"),
    }
    if files_under(PACKAGE, Path("web")) != expected_web:
        fail("canonical web export inventory drift")
    for relative in sorted(master_paths):
        validate_svg(PACKAGE / relative, native=False)
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    if (
        manifest.get("designCount") != EXPECTED_DESIGNS
        or manifest.get("solidCount") != EXPECTED_SOLIDS
    ):
        fail("generated manifest counts do not match the approved contract")
    if len(manifest.get("icons", [])) != EXPECTED_DESIGNS:
        fail("generated manifest icon inventory is incomplete")
    sprite = ET.parse(WEB_SPRITE).getroot()
    symbol_ids = [
        node.attrib.get("id")
        for node in sprite.iter()
        if local_name(node.tag) == "symbol"
    ]
    expected_symbol_count = EXPECTED_MASTERS + sum(
        bool(item.get("legacySelected")) for item in data["icons"]
    )
    if len(symbol_ids) != expected_symbol_count or len(set(symbol_ids)) != expected_symbol_count:
        fail("web sprite symbol inventory is incomplete or duplicated")
    image_sets = sorted(NATIVE_CATALOG.glob("*.imageset"))
    if len(image_sets) != EXPECTED_MASTERS:
        fail(
            f"expected {EXPECTED_MASTERS} canonical native image sets, "
            f"found {len(image_sets)}"
        )
    for image_set in image_sets:
        contents = json.loads((image_set / "Contents.json").read_text())
        properties = contents.get("properties", {})
        if (
            properties.get("preserves-vector-representation") is not True
            or properties.get("template-rendering-intent") != "template"
        ):
            fail(f"invalid native vector properties in {image_set}")
        svg_files = list(image_set.glob("*.svg"))
        if (
            len(svg_files) != 1
            or contents.get("images", [{}])[0].get("filename") != svg_files[0].name
        ):
            fail(f"invalid native image set contents in {image_set}")
        validate_svg(svg_files[0], native=True)


def validate_reproducible(package_files: set[Path]) -> None:
    with tempfile.TemporaryDirectory(prefix="lumina-icon-build-") as temp:
        regenerated = Path(temp) / "icons"
        shutil.copytree(PACKAGE, regenerated)
        run_builder(regenerated)
        for relative in sorted(package_files):
            compare_file(
                PACKAGE / relative,
                regenerated / relative,
                f"canonical export {relative}",
            )


def validate_production() -> None:
    compare_file(NATIVE_HELPER, PRODUCTION_NATIVE_HELPER, "native SwiftUI helper")
    compare_file(WEB_HELPER, PRODUCTION_WEB_HELPER, "React helper")
    compare_file(WEB_SPRITE, PRODUCTION_WEB_SPRITE, "web sprite")
    compare_file(WEB_SPRITE, PRODUCTION_WEB_MODULE_SPRITE, "web module sprite")
    canonical_files = files_under(PACKAGE, Path("native/LuminaMediaIcons.xcassets"))
    production_files = files_under(
        REPO, Path("apple/Assets/LuminaMediaIcons.xcassets")
    )
    expected_production = {
        Path("apple/Assets/LuminaMediaIcons.xcassets")
        / path.relative_to("native/LuminaMediaIcons.xcassets")
        for path in canonical_files
    }
    if production_files != expected_production:
        fail("production native asset catalog inventory differs from the canonical catalog")
    for source_relative in canonical_files:
        production_relative = (
            Path("apple/Assets/LuminaMediaIcons.xcassets")
            / source_relative.relative_to("native/LuminaMediaIcons.xcassets")
        )
        compare_file(
            PACKAGE / source_relative,
            REPO / production_relative,
            f"production asset {production_relative.name}",
        )
    new_names = {path.stem for path in PRODUCTION_CATALOG.glob("*.imageset")}
    collisions: set[str] = set()
    for catalog in (REPO / "apple/Assets").glob("*.xcassets"):
        if catalog == PRODUCTION_CATALOG:
            continue
        collisions.update(
            new_names & {path.stem for path in catalog.rglob("*.imageset")}
        )
    if collisions:
        fail(f"native asset name collisions with existing catalogs: {sorted(collisions)}")


def validate_project_enrollment() -> None:
    project_yaml = (REPO / "apple/project.yml").read_text()
    catalog_path = "- path: Assets/LuminaMediaIcons.xcassets"
    if project_yaml.count(catalog_path) != 2:
        fail(
            "shared native icon catalog must be enrolled once in each "
            "project.yml application target"
        )
    project = (REPO / "apple/Lumina.xcodeproj/project.pbxproj").read_text()
    if project.count("LuminaMediaIcons.xcassets in Resources") != 4:
        fail(
            "generated Xcode project must contain two build files and two "
            "resource-phase references for the shared catalog"
        )
    if project.count("LuminaIcon.swift in Sources") != 4:
        fail("generated Xcode project must compile LuminaIcon.swift in both targets")
    controls = REPO / "apple/App/Shared/LuminaIconControls.swift"
    if controls.exists() and project.count("LuminaIconControls.swift in Sources") != 4:
        fail("generated Xcode project must compile LuminaIconControls.swift in both targets")


def validate_previews() -> None:
    for filename, dimensions in EXPECTED_PREVIEWS.items():
        path = PACKAGE / "preview" / filename
        if not path.is_file() or png_dimensions(path) != dimensions:
            fail(f"missing or invalid approved preview {filename}")


def check(render: bool) -> None:
    data = load_source()
    validate_source(data)
    package_files, master_paths = expected_generated_paths(data)
    validate_generated_inventory(data, package_files, master_paths)
    validate_reproducible(package_files)
    validate_production()
    validate_project_enrollment()
    validate_previews()
    if render:
        validate_rendering(master_paths)
    print(
        f"Validated {EXPECTED_DESIGNS} designs, {EXPECTED_SOLIDS} solid alternates, "
        f"{EXPECTED_MASTERS} canonical/native SVGs, and both application targets"
        + (" at 20/24/28/96px." if render else ".")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "sync", help="regenerate canonical exports and synchronize application outputs"
    )
    check_parser = subparsers.add_parser(
        "check",
        help="validate source, exports, synchronized outputs, and project enrollment",
    )
    check_parser.add_argument(
        "--render",
        action="store_true",
        help="also rasterize every master at 20, 24, 28, and 96 pixels",
    )
    args = parser.parse_args()
    try:
        if args.command == "sync":
            sync()
        else:
            check(args.render)
    except (ContractError, json.JSONDecodeError, OSError) as error:
        print(f"Lumina icon contract failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
