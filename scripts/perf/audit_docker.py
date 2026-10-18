"""Run a disposable Linux benchmark container and retain cgroup evidence.

The JSON spec supplies image, mounts, environment and container_command arrays.
With host_command and readiness_url, container_command is a fixture server and
the host command is its browser client. Otherwise it is a finite backend audit.
Wrap this script with audit_run.py on the host, inside the performance lock.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from compare_servers import DockerSampler

ROOT = Path(__file__).resolve().parents[2]


def checked(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, timeout=30).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    command = spec["container_command"]
    if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
        parser.error("container_command must be a nonempty argv array")
    name = f"lumina-perf-audit-{uuid4().hex[:12]}"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.out.with_suffix(".container.log")
    client_log_path = args.out.with_suffix(".client.log")
    cpus, memory = spec.get("cpus", 4), spec.get("memory", "4g")
    user = spec.get("user", "1000:1000")
    create = ["docker", "create", "--name", name, "--no-healthcheck", "--cpus", str(cpus), "--memory", memory, "--user", user,
              "--network", spec.get("network", "none"), "--entrypoint", "python"]
    for mount in spec.get("mounts", []):
        source = str(Path(mount["source"]).resolve())
        option = f"type=bind,src={source},dst={mount['target']}"
        if mount.get("readonly", False):
            option += ",readonly"
        create += ["--mount", option]
    for port in spec.get("ports", []):
        create += ["--publish", f"127.0.0.1:{int(port['host'])}:{int(port['container'])}"]
    for key, value in spec.get("environment", {}).items():
        create += ["--env", f"{key}={value}"]
    create += [spec["image"], "-c", "import time; time.sleep(86400)"]
    metadata = {"image": spec["image"], "cpus_limit": cpus, "memory_limit": memory,
                "container_name": name, "runtime_user": user,
                "scope": "container cgroup includes fixture preparation, drivers and server; browser remains on the host",
                "container_log": str(log_path), "client_log": str(client_log_path), "errors": []}
    server = None
    status = 1
    created = False
    started = time.monotonic()
    try:
        socket_path = Path.home() / ".docker/run/docker.sock"
        if socket_path.exists():
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket_path)), timeout=3, trust_env=False) as client:
                response = client.get("http://docker/_ping")
                if response.status_code != 200:
                    raise RuntimeError(f"Local Docker API unavailable (HTTP {response.status_code}); workload not started")
        metadata["image_identity"] = json.loads(checked(["docker", "image", "inspect", spec["image"],
            "--format", '{{json .Id}}']))
        checked(create)
        created = True
        checked(["docker", "start", name])
        metadata["runtime"] = json.loads(checked(["docker", "exec", name, "python", "-c",
            "import json,platform,sqlite3; print(json.dumps({'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),'sqlite':sqlite3.sqlite_version}))"]))
        with DockerSampler(name) as sampler, log_path.open("w") as server_log:
            if spec.get("host_command"):
                server = subprocess.Popen(["docker", "exec", name, *command], stdout=server_log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + spec.get("readiness_timeout_seconds", 600)
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError("fixture server exited before readiness; see container log")
                    try:
                        if httpx.get(spec["readiness_url"], timeout=1, trust_env=False).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("fixture server did not become ready")
                with client_log_path.open("w") as client_log:
                    client = subprocess.run(spec["host_command"], cwd=ROOT, stdout=client_log, stderr=subprocess.STDOUT)
                status = client.returncode
            else:
                workload = subprocess.run(["docker", "exec", name, *command], stdout=server_log, stderr=subprocess.STDOUT)
                status = workload.returncode
        metadata["resources"] = sampler.summary()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, httpx.HTTPError) as error:
        metadata["errors"].append(str(error))
    finally:
        if created:
            try:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False, timeout=15)
            except subprocess.TimeoutExpired:
                metadata["errors"].append("Owned-container cleanup timed out; container_name retained for retry")
        if server:
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.terminate()
        metadata.update({"exit_code": status, "wall_seconds": round(time.monotonic() - started, 3)})
        args.out.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Docker audit exit {status}; evidence {args.out}", flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
