#!/usr/bin/env python3
"""Install Lumina's locked backend and frontend dependencies."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


REPOSITORY = Path(__file__).resolve().parent.parent


def virtualenv_python(virtualenv: Path, platform: str = os.name) -> Path:
    if platform == "nt":
        return virtualenv / "Scripts" / "python.exe"
    return virtualenv / "bin" / "python"


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SystemExit(f"required tool is not available on PATH: {name}")
    return executable


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=REPOSITORY, check=True)


def locked_dependency_commands(
    *, backend_python: Path, npm: str
) -> list[list[str]]:
    return [
        [
            str(backend_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            "backend/requirements.lock",
        ],
        [npm, "--prefix", "frontend", "ci"],
    ]


def main() -> None:
    backend_virtualenv = REPOSITORY / "backend" / ".venv"
    backend_python = virtualenv_python(backend_virtualenv)
    if not backend_python.is_file():
        # mise's macOS Python build loads libpython relative to its real
        # executable. A copied venv launcher loses that relationship, while an
        # absolute symlink preserves it. Other platforms retain venv's default.
        venv.EnvBuilder(with_pip=True, symlinks=sys.platform == "darwin").create(backend_virtualenv)

    commands = locked_dependency_commands(
        backend_python=backend_python,
        npm=require_tool("npm"),
    )
    for command in commands:
        run(command)


if __name__ == "__main__":
    main()
