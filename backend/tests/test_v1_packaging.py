"""The Docker packaging stays browser-only, credential-free and pinned to one toolchain source."""
from pathlib import Path
import json
import os
import re
import subprocess

import pytest
import yaml

ROOT = Path(os.environ.get("LUMINA_PACKAGING_ROOT", Path(__file__).resolve().parents[2]))


def _pins() -> dict[str, str]:
    return dict(line.split() for line in (ROOT / ".tool-versions").read_text().splitlines() if line.strip())


def _release_workflow() -> tuple[dict, str]:
    raw = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    return yaml.safe_load(raw), raw


def _step(steps: list[dict], name: str) -> dict:
    return next(step for step in steps if step.get("name") == name)


def test_docker_base_images_match_the_single_toolchain_pin() -> None:
    pins = _pins()
    bases = re.findall(r"^FROM (\w+):([\d.]+)-", (ROOT / "Dockerfile").read_text(), re.MULTILINE)
    assert dict(bases) == {"node": pins["nodejs"], "python": pins["python"]}


def test_python_pin_reaches_pyproject_and_both_locks() -> None:
    major_minor = ".".join(_pins()["python"].split(".")[:2])
    assert f'requires-python = ">={major_minor}"' in (ROOT / "backend" / "pyproject.toml").read_text()
    for lock in ("requirements.lock", "requirements.runtime.lock"):
        assert f"--python-version {major_minor} " in (ROOT / "backend" / lock).read_text().splitlines()[1]


def test_packaging_carries_no_credential_or_legacy_state() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    for text in (compose, entrypoint, dockerfile):
        for legacy in ("SECRET_KEY", "storage.key", "lumina-secrets", "/shared", "cookies", "cryptography", "gosu"):
            assert legacy not in text
    assert "curl-cffi" not in (ROOT / "backend" / "requirements.runtime.lock").read_text()
    # Non-root runtime, healthcheck in the image, and init can forward SIGTERM across the UID drop.
    assert 'exec setpriv --reuid="$runtime_uid" --regid="$runtime_gid" "$groups"' in entrypoint
    assert "HEALTHCHECK" in dockerfile and "/api/health" in dockerfile
    assert "- KILL" in compose and "127.0.0.1:8765:8765" in compose
    # Shutdown joins model downloads and stops model servers one by one; Docker's 10 s default can SIGKILL that.
    assert "\n    stop_grace_period: 30s\n" in compose


def test_media_toolchain_is_jellyfin_ffmpeg_pinned_per_arch() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    stages = dict(re.findall(r"^FROM scratch AS media-debs-(\w+)\n(.*?)(?=^FROM )", dockerfile, re.M | re.S))
    assert set(stages) == {"amd64", "arm64"}
    for arch, body in stages.items():
        adds = re.findall(r"^ADD (.+)$", body, re.M)
        assert adds and all(re.fullmatch(r"--checksum=sha256:[0-9a-f]{64} https://github\.com/\S+\.deb /debs/", add) for add in adds)
        assert any("jellyfin-ffmpeg8_" in add and f"-trixie_{arch}.deb" in add for add in adds)
    assert "intel-opencl-icd_" in stages["amd64"] and "intel" not in stages["arm64"]
    assert "FROM media-debs-${TARGETARCH} AS media-debs" in dockerfile
    for tool in ("ffmpeg", "ffprobe"):
        assert f"ln -s /usr/lib/jellyfin-ffmpeg/{tool} /usr/local/bin/{tool}" in dockerfile
    assert "static-ffmpeg" not in dockerfile


def test_qsv_override_passes_the_render_device_and_group() -> None:
    qsv = (ROOT / "docker-compose.qsv.yml").read_text()
    assert "/dev/dri:/dev/dri" in qsv
    assert "LUMINA_RENDER_GID: ${LUMINA_RENDER_GID:?" in qsv


def _run_entrypoint(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the real entrypoint with chown/stat/setpriv stubbed; the setpriv stub prints its arguments."""
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    for name, body in {"chown": "exit 0", "stat": 'echo "1000:1000"', "setpriv": 'echo "$@"'}.items():
        (stubs / name).write_text(f"#!/bin/sh\n{body}\n")
        (stubs / name).chmod(0o755)
    environ = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "LUMINA_DATA_DIR": str(tmp_path / "data"), **env}
    return subprocess.run(["sh", str(ROOT / "docker" / "entrypoint.sh")], env=environ, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(("env", "groups"), [({}, "--clear-groups"), ({"LUMINA_RENDER_GID": ""}, "--clear-groups"), ({"LUMINA_RENDER_GID": "109"}, "--groups=109")])
def test_entrypoint_drops_to_the_runtime_account_with_only_the_render_group(tmp_path: Path, env: dict, groups: str) -> None:
    result = _run_entrypoint(tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:3] == ["--reuid=1000", "--regid=1000", groups]


@pytest.mark.parametrize("render_gid", ["0", "video", "44;id", "-1", "01"])
def test_entrypoint_refuses_a_bad_render_group(tmp_path: Path, render_gid: str) -> None:
    result = _run_entrypoint(tmp_path, LUMINA_RENDER_GID=render_gid)
    assert result.returncode == 1 and "LUMINA_RENDER_GID" in result.stderr and result.stdout == ""


def test_model_runtimes_are_built_portable_from_pinned_sources() -> None:
    """llama-server from a pinned commit, never CPU-native; speech in its own hashed venv."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^ARG LLAMA_CPP_COMMIT=f805c57a2d0b7cc171e599303ce2040f6e1bfe15$", dockerfile, re.M)  # tag b11176
    assert 'test "$(git -C /src rev-parse HEAD)" = "$LLAMA_CPP_COMMIT"' in dockerfile
    for flag in ("-DGGML_NATIVE=OFF", "-DGGML_BACKEND_DL=ON", "-DGGML_CPU_ALL_VARIANTS=ON", "-DCMAKE_INSTALL_RPATH='$ORIGIN'"):
        assert flag in dockerfile
    assert "GGML_NATIVE=ON" not in dockerfile
    assert "--only-binary=:all: --require-hashes -r /tmp/lumina-asr.lock" in dockerfile
    for line in (
        "COPY --from=llama-build /opt/lumina-llama /opt/lumina-llama",
        "COPY --from=asr-venv /opt/lumina-asr /opt/lumina-asr",
        "COPY docker/lumina-asr/server.py /opt/lumina-asr/server.py",
        "/opt/lumina-llama/bin/llama-server --version",
        '/opt/lumina-asr/bin/python -c "import faster_whisper, ctranslate2"',
    ):
        assert line in dockerfile
    # Every Python stage uses the one pinned base, so the runtimes are built against the runtime image's C library.
    bases = set(re.findall(r"^FROM python:(\S+)", dockerfile, re.M))
    assert len(bases) == 1 and "@sha256:" in bases.pop()
    assert "faster-whisper" not in (ROOT / "backend" / "requirements.runtime.lock").read_text()  # Lumina's own deps unchanged


def test_image_carries_the_license_texts_of_copied_binaries_and_the_bundle() -> None:
    """Binaries copied without their package (llama.cpp, Node.js) bring their LICENSE; the web build writes its notices."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "cp /src/LICENSE /opt/lumina-llama/LICENSE" in dockerfile
    assert "COPY --from=frontend-build /usr/local/LICENSE /usr/local/share/doc/node/LICENSE" in dockerfile
    assert "node scripts/licenses.mjs" in json.loads((ROOT / "frontend" / "package.json").read_text())["scripts"]["build"]


def test_release_versions_and_default_public_image_stay_aligned() -> None:
    """A release tag must name the version users pull and the runtime reports."""
    app_version = re.search(r'^APP_VERSION = "([^"]+)"$', (ROOT / "backend" / "app" / "config.py").read_text(), re.M)
    assert app_version
    version = app_version.group(1)
    assert json.loads((ROOT / "frontend" / "package.json").read_text())["version"] == version
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())
    assert lock["version"] == version and lock["packages"][""]["version"] == version
    assert re.search(rf'^version = "{re.escape(version)}"$', (ROOT / "backend" / "pyproject.toml").read_text(), re.M)
    assert f'org.opencontainers.image.version="{version}"' in (ROOT / "Dockerfile").read_text()
    assert f"ghcr.io/shiftedx/lumina:{version}" in (ROOT / "docker-compose.yml").read_text()


def test_release_workflow_builds_native_arches_then_publishes_a_digest_manifest() -> None:
    workflow, raw = _release_workflow()
    jobs = workflow["jobs"]
    assert workflow[True]["push"]["tags"] == ["v*"]
    assert jobs["build"]["needs"] == "validate"
    assert jobs["publish-manifest"]["needs"] == ["validate", "build"]
    assert jobs["build"]["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    build = jobs["build"]
    assert build["runs-on"] == "${{ matrix.runner }}"
    build_step = next(step for step in build["steps"] if step.get("id") == "build")
    assert build_step["with"]["platforms"] == "linux/${{ matrix.arch }}"
    assert "push-by-digest=true" in build_step["with"]["outputs"]
    assert "name-canonical=true" in build_step["with"]["outputs"]
    assert build_step["with"]["build-args"].strip() == "LUMINA_SOURCE_REVISION=${{ github.sha }}"
    assert build_step["with"]["sbom"] is True and build_step["with"]["provenance"] == "mode=max"
    smoke = _step(build["steps"], "Smoke the native release image")["run"]
    for contract in ("docker run -d --rm", "linux/${{ matrix.arch }}", "org.opencontainers.image.revision", "org.opencontainers.image.version", "docker image inspect", "app.main:app", "--no-proxy-headers", 'open("/proc/1/status")', "api/health", "api/bootstrap/status", "needs_setup", "ffmpeg -version", "ffprobe -version", "llama-server --version", "faster_whisper, ctranslate2", "docker stop --time 30"):
        assert contract in smoke
    publish = _step(jobs["publish-manifest"]["steps"], "Publish and verify the multi-platform manifest")["run"]
    assert "docker buildx imagetools create" in publish and "docker buildx imagetools inspect" in publish
    assert 'arches == {"amd64", "arm64"}' in publish
    assert "entry.get(\"platform\"" in publish
    assert "ghcr.io/shiftedx/lumina" in raw


def test_release_workflow_has_minimal_permissions_and_immutable_action_pins() -> None:
    workflow, raw = _release_workflow()
    assert workflow["permissions"] == {"contents": "read"}
    for job_name in ("build", "publish-manifest"):
        assert workflow["jobs"][job_name]["permissions"] == {"contents": "read", "packages": "write"}
    assert "password: ${{ secrets.GITHUB_TOKEN }}" in raw
    assert "secrets." not in raw.replace("secrets.GITHUB_TOKEN", "")
    uses = re.findall(r"^\s*uses:\s*[^@\s]+@([^\s#]+)", raw, re.M)
    assert uses and all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in uses)


def test_release_tag_validation_accepts_only_the_checked_in_release_version(tmp_path: Path) -> None:
    workflow, _ = _release_workflow()
    script = workflow["jobs"]["validate"]["steps"][1]["run"]
    version = re.search(r'^APP_VERSION = "([^"]+)"$', (ROOT / "backend" / "app" / "config.py").read_text(), re.M).group(1)
    output = tmp_path / "output"
    valid = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], cwd=ROOT,
        env={**os.environ, "GITHUB_REF_NAME": f"v{version}", "GITHUB_OUTPUT": str(output)},
        capture_output=True, text=True, check=False,
    )
    assert valid.returncode == 0, valid.stderr
    assert output.read_text() == f"version={version}\n"
    invalid = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], cwd=ROOT,
        env={**os.environ, "GITHUB_REF_NAME": "v2.13", "GITHUB_OUTPUT": str(tmp_path / "invalid-output")},
        capture_output=True, text=True, check=False,
    )
    assert invalid.returncode == 1 and "vMAJOR.MINOR.PATCH" in invalid.stderr
