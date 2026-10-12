#!/usr/bin/env python3
"""Run backend tests with the bootstrapped virtual environment."""

import subprocess

from bootstrap import REPOSITORY, virtualenv_python


def main() -> None:
    backend_python = virtualenv_python(REPOSITORY / "backend" / ".venv")
    if not backend_python.is_file():
        raise SystemExit("backend dependencies are missing; run make bootstrap first")
    subprocess.run(
        [str(backend_python), "-m", "pytest", "-q"],
        cwd=REPOSITORY / "backend",
        check=True,
    )


if __name__ == "__main__":
    main()
