"""Exercise the shipped models through their real subprocess boundary, without private artwork."""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib


def test_bundled_models_analyze_a_raster_and_reject_non_images():
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    width, height = 320, 180
    image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\x80\x80" * width) * height)) + chunk(b"IEND", b""))
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    command = [sys.executable, "-m", "app.services.hero_analyzer"]
    result = subprocess.run(command, input=image, capture_output=True, env=environment, timeout=8, check=True)
    framing = json.loads(result.stdout)
    assert (framing["state"], framing["width"], framing["height"]) == ("ready", width, height)
    assert len(framing["regions"]) <= 16
    for region in framing["regions"]:
        assert set(region) == {"x", "y", "width", "height", "kind", "weight"}
        assert region["kind"] in ("face", "subject")
        assert 0 <= region["x"] <= region["x"] + region["width"] <= 1
        assert 0 <= region["y"] <= region["y"] + region["height"] <= 1
        assert 0 <= region["weight"] <= 1
    refused = subprocess.run(command, input=b"<svg></svg>", capture_output=True, env=environment, timeout=8)
    assert refused.returncode != 0
    assert refused.stdout == b""  # failures never masquerade as usable subject coordinates
