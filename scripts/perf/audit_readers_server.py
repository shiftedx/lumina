"""Isolated real-backend launcher for the non-video reader and music audit."""
from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import audit_readers_fixtures
import run_realstack_fixtures as realstack


def build_media(media: Path) -> None:
    # The onboarding scan mounts ROOT/media. Keep every generated track beneath it.
    audit_readers_fixtures.build(media.parent)


def main() -> None:
    root, port = Path(sys.argv[1]).resolve(), sys.argv[2]
    import test_safety

    test_safety.check_root_isolation(root)
    shutil.rmtree(root, ignore_errors=True)
    (root / "data").mkdir(parents=True)
    build_media(root / "media")
    os.environ.update(
        LUMINA_DATA_DIR=str(root / "data"),
        LUMINA_PORT=port,
        LUMINA_STORAGE_MOUNT_PARENTS=str(root / "media"),
        LUMINA_ARTWORK_PASS="off",
    )
    test_safety.LoopbackOnlyGuard().apply(realstack.offline_setattr)
    sys.path.insert(0, str(REPO / "backend"))
    from app.schemas import YouTubeSearchResponse
    from app.services.yt_dlp_service import YtDlpService
    YtDlpService.youtube_search = lambda self, query, limit=10: YouTubeSearchResponse(query=query.strip(), items=[])
    if os.environ.get("AUDIT_READERS_UNPROBED") == "1":
        # Reproduce a newly scanned file before the background probe reaches it.
        from app.services.probe_warming import probe_warming
        probe_warming.start = lambda: None
    import uvicorn
    uvicorn.run("app.main:app", host=os.environ.get("AUDIT_BIND_HOST", "127.0.0.1"), port=int(port), log_level="warning", timeout_keep_alive=30)


if __name__ == "__main__":
    main()
