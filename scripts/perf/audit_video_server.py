"""Start the isolated synthetic-video server used by the opt-in audit suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: audit_video_server.py ROOT PORT")
    # This is deliberately a separate build function: it adds only the audit's
    # two-audio/text-subtitle fixture while retaining the playperf media matrix.
    sys.path.insert(0, str(REPO / "scripts"))
    import run_realstack_fixtures as realstack  # noqa: PLC0415
    from e2e import music_fixture  # noqa: PLC0415
    from perf.audit_video_fixtures import build_media  # noqa: PLC0415
    import uvicorn  # noqa: PLC0415

    realstack.build_media = build_media
    music_fixture.build_music = lambda root: Path(root).mkdir(parents=True, exist_ok=True)
    music_fixture.add_theme_music = lambda *_: None
    # The standard audit remains loopback-only. Docker's explicit audit mode
    # sets AUDIT_BIND_HOST=0.0.0.0 and publishes that port to localhost only.
    original_run = uvicorn.run
    def run_with_audit_bind(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["host"] = os.environ.get("AUDIT_BIND_HOST", "127.0.0.1")
        return original_run(*args, **kwargs)
    uvicorn.run = run_with_audit_bind
    realstack.main()


if __name__ == "__main__":
    main()
