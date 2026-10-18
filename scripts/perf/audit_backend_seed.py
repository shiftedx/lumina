"""Create isolated synthetic input for ``audit_backend.py``.

This deliberately composes the established household seed rather than creating a
second, subtly different performance database.  The extra files are small,
valid fixtures for the audit's import and book setup steps; no real media,
providers, credentials, or household paths are used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import subprocess
import sys
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERF = Path(__file__).resolve().parent


def _wav(path: Path) -> None:
    """Write a short PCM WAV without needing ffmpeg."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(struct.pack("<" + "h" * 8_000, *([0] * 8_000)))


def _epub(path: Path) -> None:
    """Write one minimal, valid EPUB with a chapter and a tiny cover surrogate."""
    container = '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
 <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    package = '''<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="book" xmlns="http://www.idpf.org/2007/opf">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book">audit-book</dc:identifier><dc:title>Audit Book</dc:title><dc:language>en</dc:language></metadata>
 <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest>
 <spine><itemref idref="chapter"/></spine>
</package>'''
    chapter = "<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><h1>Audit chapter</h1><p>synthetic fixture</p></body></html>"
    nav = "<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><nav epub:type=\"toc\" xmlns:epub=\"http://www.idpf.org/2007/ops\"><ol><li><a href=\"chapter.xhtml\">Audit chapter</a></li></ol></nav></body></html>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
        archive.writestr("OEBPS/nav.xhtml", nav)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--import-files", type=int, default=32, choices=(32, 256))
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        parser.error(f"{data_dir} must be empty")

    # The shared seed's human-facing output includes its fixture password.
    # Capture it rather than letting an audit command or CI log print even a
    # synthetic credential; the audit only emits count/fixture facts below.
    seeded = subprocess.run([sys.executable, str(PERF / "seed_household.py"), str(data_dir)], cwd=ROOT,
                            check=False, capture_output=True, text=True)
    if seeded.returncode:
        raise RuntimeError(f"household seed failed ({seeded.returncode}); see its local setup independently")
    with sqlite3.connect(data_dir / "app.db") as db:
        root = db.execute("SELECT id, path FROM storage_roots WHERE mode = 'external' ORDER BY id LIMIT 1").fetchone()
        tracks = db.execute("SELECT id FROM library_items WHERE kind = 'track' AND visibility = 'shared' AND status = 'available' ORDER BY id LIMIT 5000").fetchall()
    if root is None or len(tracks) < 2:
        raise RuntimeError("household seed did not provide an external root and two shared tracks")
    root_id, root_path = root
    imports = Path(root_path) / "audit-import"
    imports.mkdir(parents=True, exist_ok=False)
    for number in range(args.import_files):
        _wav(imports / f"audit-{number:04d}.wav")
    fixtures = data_dir / "audit-fixtures"
    fixtures.mkdir(mode=0o700)
    _epub(fixtures / "audit-book.epub")
    selectors = {
        "admin": "perfadmin",
        "members": ["alex", "blair", "casey", "devon", "emery"],
        "external_root_id": root_id,
        "track_ids": [row[0] for row in tracks],
        "book_upload": str(fixtures / "audit-book.epub"),
        "import_file_count": args.import_files,
    }
    (data_dir / "audit-selectors.json").write_text(json.dumps(selectors, indent=2) + "\n", encoding="utf-8")
    print(f"audit seed ready: {data_dir} ({args.import_files} WAV files; one EPUB fixture)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
