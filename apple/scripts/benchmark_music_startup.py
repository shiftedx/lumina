#!/usr/bin/env python3
"""Run the synthetic workloads documented in apple/docs/performance-review.md.

Uses only the Python standard library; serve generated fixtures on loopback only.
Each native binary prints one credential-free JSON measurement per fresh process.
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import statistics
import subprocess
import threading
import time

parser = argparse.ArgumentParser(description="Compare fresh-process native music startup and footprint")
parser.add_argument("before", type=Path)
parser.add_argument("after", type=Path)
parser.add_argument("fixtures", type=Path)
parser.add_argument("--runs", type=int, default=5)
args = parser.parse_args()
if args.runs < 1:
    parser.error("--runs must be positive")
root = args.fixtures.resolve(strict=True)
binaries = {"before": args.before.resolve(strict=True), "after": args.after.resolve(strict=True)}


class Media(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.1)
        super().do_GET()

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Media, directory=str(root)))
threading.Thread(target=server.serve_forever, daemon=True).start()
scenarios = [
    ('local-short', str(root / 'tone-15.flac'), 15, 48000, 2, 0),
    ('local-queue', str(root / 'tone-120.flac'), 120, 48000, 2, 0),
    ('local-crossfade', str(root / 'tone-120.flac'), 120, 48000, 2, 117),
    ('local-seek', str(root / 'tone-120.flac'), 120, 48000, 1, 60),
    ('local-high-rate', str(root / 'tone-96k.flac'), 60, 96000, 2, 0),
    ('http-queue', f'http://127.0.0.1:{server.server_port}/tone-120.flac', 120, 48000, 2, 0),
]
rows = []
try:
    for name, url, *arguments in scenarios:
        for iteration in range(args.runs):
            order = ('before', 'after') if iteration % 2 == 0 else ('after', 'before')
            for variant in order:
                result = subprocess.run([str(binaries[variant]), url, *map(str, arguments)],
                    check=True, capture_output=True, text=True, timeout=30)
                value = json.loads(result.stdout.strip().splitlines()[-1])
                if name == 'local-crossfade' and (value['active_index'] != 1 or not 0 < value['position_seconds'] < 1.5):
                    raise RuntimeError('The sample did not capture the active crossfade')
                value.update(scenario=name, variant=variant, iteration=iteration)
                rows.append(value)
                print(json.dumps(value), flush=True)
        summary = {}
        for variant in ('before', 'after'):
            subset = [row for row in rows if row['scenario'] == name and row['variant'] == variant]
            summary[variant] = {field: statistics.median(row[field] for row in subset)
                for field in ('startup_ms', 'peak_footprint_bytes', 'active_footprint_bytes', 'workload_cpu_ms')}
        print('SUMMARY ' + json.dumps({name: summary}), flush=True)
finally:
    server.shutdown()
