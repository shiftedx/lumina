"""Measure the synchronous file bodies used by remote playback.

    backend/.venv/bin/python scripts/perf/media_stream_bench.py [--size-mib 64] [--count 7]

Starlette advances these iterators through its worker thread. The benchmark compares
the former 64 KiB reads with the configured media read size and prints JSON results.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import platform
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from starlette.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.media_response import MediaFileResponse  # noqa: E402
from app.services.remote_streaming import _FileRangeBody  # noqa: E402
from app.services.stream_cache import MEDIA_FILE_CHUNK_SIZE, _FileBody  # noqa: E402

LEGACY_CHUNK_SIZE = 64 * 1024


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


async def _consume(body) -> tuple[int, float]:  # noqa: ANN001
    response = StreamingResponse(body)
    total = 0
    started = time.perf_counter()
    first_chunk_ms = 0.0
    async for chunk in response.body_iterator:
        if total == 0:
            first_chunk_ms = (time.perf_counter() - started) * 1000
        total += len(chunk)
    return total, first_chunk_ms


async def _consume_file_response(path: Path, size: int, chunk_size: int) -> tuple[int, float]:
    response = MediaFileResponse(path)
    response.chunk_size = chunk_size
    total = 0
    started = time.perf_counter()
    first_chunk_ms = 0.0

    async def send(message: dict) -> None:
        nonlocal total, first_chunk_ms
        if message["type"] == "http.response.body":
            if total == 0:
                first_chunk_ms = (time.perf_counter() - started) * 1000
            total += len(message["body"])

    async def receive() -> dict:
        return {"type": "http.request"}

    scope = {"type": "http", "method": "GET", "headers": [(b"range", f"bytes=0-{size - 1}".encode())]}
    await response(scope, receive, send)
    return total, first_chunk_ms


def bench(path: Path, size: int, count: int) -> dict:
    input_digest = _digest(path)

    async def run(factory) -> list[dict[str, float]]:  # noqa: ANN001
        assert (await _consume(factory()))[0] == size
        samples = []
        for _ in range(count):
            wall_started, cpu_started = time.perf_counter(), time.process_time()
            total, first_chunk_ms = await _consume(factory())
            assert total == size
            samples.append({
                "wall_ms": (time.perf_counter() - wall_started) * 1000,
                "cpu_ms": (time.process_time() - cpu_started) * 1000,
                "first_chunk_ms": first_chunk_ms,
            })
        return samples

    results = {}
    bodies = {
        "cache": lambda chunk: _FileBody(path, size, chunk),
        "hls_range": lambda chunk: _FileRangeBody(path, 0, size - 1, chunk),
    }
    for body_name, factory in bodies.items():
        for chunk_name, chunk_size in (("legacy_64k", LEGACY_CHUNK_SIZE), ("configured", MEDIA_FILE_CHUNK_SIZE)):
            samples = asyncio.run(run(lambda factory=factory, chunk_size=chunk_size: factory(chunk_size)))
            median_ms = statistics.median(sample["wall_ms"] for sample in samples)
            results[f"{body_name}_{chunk_name}"] = {
                "chunk_kib": chunk_size // 1024,
                "median_ms": round(median_ms, 2),
                "median_cpu_ms": round(statistics.median(sample["cpu_ms"] for sample in samples), 2),
                "median_first_chunk_ms": round(statistics.median(sample["first_chunk_ms"] for sample in samples), 2),
                "mib_per_second": round((size / 1024**2) / (median_ms / 1000), 1),
                "samples": [{name: round(value, 2) for name, value in sample.items()} for sample in samples],
            }
    for chunk_name, chunk_size in (("legacy_64k", LEGACY_CHUNK_SIZE), ("configured", MEDIA_FILE_CHUNK_SIZE)):
        async def run_response(selected_chunk_size: int) -> list[dict[str, float]]:
            assert (await _consume_file_response(path, size, selected_chunk_size))[0] == size
            samples = []
            for _ in range(count):
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                total, first_chunk_ms = await _consume_file_response(path, size, selected_chunk_size)
                assert total == size
                samples.append({
                    "wall_ms": (time.perf_counter() - wall_started) * 1000,
                    "cpu_ms": (time.process_time() - cpu_started) * 1000,
                    "first_chunk_ms": first_chunk_ms,
                })
            return samples

        samples = asyncio.run(run_response(chunk_size))
        median_ms = statistics.median(sample["wall_ms"] for sample in samples)
        results[f"direct_range_{chunk_name}"] = {
            "chunk_kib": chunk_size // 1024,
            "median_ms": round(median_ms, 2),
            "median_cpu_ms": round(statistics.median(sample["cpu_ms"] for sample in samples), 2),
            "median_first_chunk_ms": round(statistics.median(sample["first_chunk_ms"] for sample in samples), 2),
            "mib_per_second": round((size / 1024**2) / (median_ms / 1000), 1),
            "samples": [{name: round(value, 2) for name, value in sample.items()} for sample in samples],
        }

    app = FastAPI()

    @app.get("/{chunk_kib}")
    async def media(chunk_kib: int):  # noqa: ANN202
        response = MediaFileResponse(path)
        response.chunk_size = chunk_kib * 1024
        return response

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(0.001)
    try:
        for chunk_name, chunk_size in (("legacy_64k", LEGACY_CHUNK_SIZE), ("configured", MEDIA_FILE_CHUNK_SIZE)):
            samples = []
            for _ in range(count + 1):  # first request warms the server and page cache
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                connection.request("GET", f"/{chunk_size // 1024}", headers={"Range": f"bytes=0-{size - 1}", "Connection": "close"})
                response = connection.getresponse()
                first = response.read(1)
                first_chunk_ms = (time.perf_counter() - wall_started) * 1000
                digest = hashlib.sha256(first)
                total = len(first)
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    total += len(chunk)
                sample = {
                    "wall_ms": (time.perf_counter() - wall_started) * 1000,
                    "cpu_ms": (time.process_time() - cpu_started) * 1000,
                    "first_byte_ms": first_chunk_ms,
                }
                connection.close()
                assert response.status == 206 and total == size and digest.hexdigest() == input_digest
                samples.append(sample)
            samples = samples[1:]
            median_ms = statistics.median(sample["wall_ms"] for sample in samples)
            results[f"http_direct_range_{chunk_name}"] = {
                "chunk_kib": chunk_size // 1024,
                "median_ms": round(median_ms, 2),
                "median_cpu_ms": round(statistics.median(sample["cpu_ms"] for sample in samples), 2),
                "median_first_byte_ms": round(statistics.median(sample["first_byte_ms"] for sample in samples), 2),
                "mib_per_second": round((size / 1024**2) / (median_ms / 1000), 1),
                "samples": [{name: round(value, 2) for name, value in sample.items()} for sample in samples],
            }
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    return {
        "size_bytes": size,
        "input_sha256": input_digest,
        "count": count,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark remote-playback file response bodies.")
    parser.add_argument("--size-mib", type=int, default=64)
    parser.add_argument("--count", type=int, default=7)
    args = parser.parse_args(argv)
    if args.size_mib <= 0 or args.count <= 0:
        parser.error("--size-mib and --count must be positive")
    with tempfile.TemporaryDirectory(prefix="lumina-media-stream-bench-") as work:
        path = Path(work) / "media.bin"
        with path.open("wb") as handle:
            handle.truncate(args.size_mib * 1024**2)
        print(json.dumps(bench(path, args.size_mib * 1024**2, args.count)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
