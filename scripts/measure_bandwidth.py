#!/usr/bin/env python3
"""Measure aggregate HTTP delivery through the deployed Lumina/proxy path.

Example (the target must be a protected, authenticated media URL):
  scripts/measure_bandwidth.py --url https://lumina.example.test/media --header 'Cookie: session=…' --parallel 2
"""
from __future__ import annotations

import argparse
import concurrent.futures
import time
import urllib.request


def download(url: str, headers: dict[str, str]) -> tuple[int, float]:
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - operator supplies the target URL.
        total = 0
        while chunk := response.read(64 * 1024):
            total += len(chunk)
    return total, time.monotonic() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="authenticated Lumina URL through its supported proxy")
    parser.add_argument("--header", action="append", default=[], help="repeatable HTTP header, such as 'Cookie: name=value'")
    parser.add_argument("--parallel", type=int, default=2)
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error("--parallel must be positive")
    headers: dict[str, str] = {}
    for header in args.header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            parser.error("--header must have the form 'Name: value'")
        headers[name.strip()] = value.strip()

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda _: download(args.url, headers), range(args.parallel)))
    elapsed = time.monotonic() - started
    total = sum(size for size, _ in results)
    print(
        f"parallel={args.parallel} bytes={total} duration_seconds={elapsed:.3f} "
        f"aggregate_bps={(total * 8 / elapsed):.0f} "
        f"per_request_seconds={[round(duration, 3) for _, duration in results]}"
    )


if __name__ == "__main__":
    main()
