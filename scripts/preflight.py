#!/usr/bin/env python3
"""Fail clearly when the declared build toolchain is not active."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


# .tool-versions is the single toolchain pin (mise reads it; the Dockerfile base tags must match it).
PINS = dict(line.split() for line in (Path(__file__).resolve().parents[1] / ".tool-versions").read_text().splitlines() if line.strip())
EXPECTED_PYTHON = tuple(int(part) for part in PINS["python"].split("."))
EXPECTED_NODE = "v" + PINS["nodejs"]


def output(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    failures: list[str] = []
    if sys.version_info[:3] != EXPECTED_PYTHON:
        failures.append(f"Python {'.'.join(map(str, EXPECTED_PYTHON))} required; found {sys.version.split()[0]}")
    for tool in ("git", "make", "npm", "docker"):
        if shutil.which(tool) is None:
            failures.append(f"missing required tool: {tool}")
    if shutil.which("node") and output(["node", "--version"]) != EXPECTED_NODE:
        failures.append(f"Node {EXPECTED_NODE} required; found {output(['node', '--version'])}")
    if shutil.which("docker"):
        compose = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if compose.returncode != 0:
            failures.append("Docker Compose plugin is unavailable")
    if failures:
        raise SystemExit("Preflight failed:\n- " + "\n- ".join(failures))
    print(f"Preflight passed in {Path.cwd()}.")


if __name__ == "__main__":
    main()
