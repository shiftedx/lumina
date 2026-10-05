"""Stand-in ffmpeg for gallery T1 tests. Stdlib only; run through a tiny sh wrapper.

FAKE_FFMPEG_LOG names a JSON-lines file; every run that reads stdin appends {"argv": [...], "stdin": <bytes read>}.
FAKE_FFMPEG_MODE: ok (default) | fail | hang | oversize | no_webp.
Outputs are the argv entries inside a ``/.staging/`` directory: the 8 x 8 ``.rgb`` frame is 63 grey pixels and one
red one, ``preview30`` is 700 bytes (over the 600-byte cap), other previews 300 bytes, renditions 64 bytes.
"""
import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
mode = os.environ.get("FAKE_FFMPEG_MODE", "ok")
if "-encoders" in argv:
    print(" V....D mjpeg                MJPEG (Motion JPEG)")
    if mode != "no_webp":
        print(" V....D libwebp              libwebp WebP image (codec webp)")
    sys.exit(0)
stdin = sys.stdin.buffer.read()
log = os.environ.get("FAKE_FFMPEG_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "stdin": len(stdin)}) + "\n")
if mode == "fail":
    sys.exit(1)
if mode == "hang":
    time.sleep(30)
    sys.exit(0)
PIXELS = bytes([128, 128, 128]) * 63 + bytes([200, 40, 40])
for index, token in enumerate(token for token in argv if "/.staging/" in token):
    path = Path(token)
    if path.suffix == ".rgb":
        data = PIXELS
    elif path.stem == "preview30":
        data = b"p" * 700
    elif path.stem.startswith("preview"):
        data = b"p" * 300
    else:
        data = b"r" * (4 * 1024 * 1024 + 1 if mode == "oversize" and index == 0 else 64)
    path.write_bytes(data)
