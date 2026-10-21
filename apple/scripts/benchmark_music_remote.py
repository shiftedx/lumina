#!/usr/bin/env python3
"""Compare remote native startup using synthetic files and a shared per-run byte rate.

Compile both revisions with benchmark_music_startup.swift from this checkout.
Generate the fixtures using the commands in apple/docs/performance-review.md.
"""
import argparse
import http.server
import json
from pathlib import Path
import statistics
import select
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('before', type=Path)
parser.add_argument('after', type=Path)
parser.add_argument('fixtures', type=Path)
parser.add_argument('--runs', type=int, default=5)
parser.add_argument('--transfer-seconds', type=float, default=8)
parser.add_argument('--latency-ms', type=float, default=100)
args = parser.parse_args()
if args.runs < 1 or args.transfer_seconds <= 0 or args.latency_ms < 0:
    parser.error('runs/transfer-seconds must be positive; latency-ms must be nonnegative')
scenarios = [
    ('wav', 'pcm_s16le', 'audio/wav'),
    ('flac', 'flac', 'audio/flac'),
    ('mp3', 'mp3', 'audio/mpeg'),
    ('aac.m4a', 'aac', 'audio/mp4'),
    ('alac.m4a', 'alac', 'audio/mp4'),
]
files = {f'noise-120.{suffix}': ((args.fixtures / f'noise-120.{suffix}').read_bytes(), mime)
    for suffix, _, mime in scenarios}
binaries = {'before': args.before.resolve(strict=True), 'after': args.after.resolve(strict=True)}
lock = threading.Lock()
stats = {}


class Media(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = urlsplit(self.path).path.strip('/').split('/')
        if len(parts) != 2 or parts[1] not in files:
            self.send_error(404)
            return
        run, filename = parts
        content, mime = files[filename]
        with lock:
            if run not in stats:
                self.send_error(404)
                return
            stats[run]['requests'] += 1
        time.sleep(args.latency_ms / 1000)
        start, end = 0, len(content) - 1
        header = self.headers.get('Range')
        if header:
            first, last = header.removeprefix('bytes=').split('-')
            start, end = int(first), min(int(last) if last else end, end)
        if start > end:
            self.send_error(416)
            return
        self.send_response(206 if header else 200)
        self.send_header('Content-Type', mime)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(end - start + 1))
        if header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(content)}')
        self.end_headers()
        rate = len(content) / args.transfer_seconds
        try:
            for offset in range(start, end + 1, 16384):
                chunk = content[offset:min(offset + 16384, end + 1)]
                with lock:
                    due = max(time.monotonic(), stats[run]['next_send'])
                    stats[run]['next_send'] = due + len(chunk) / rate
                time.sleep(max(0, due - time.monotonic()))
                self.wfile.write(chunk)
                self.wfile.flush()
                with lock:
                    stats[run]['bytes'] += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *arguments):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Media)
threading.Thread(target=server.serve_forever, daemon=True).start()
rows = []
try:
    for suffix, codec, _ in scenarios:
        filename = f'noise-120.{suffix}'
        size = len(files[filename][0])
        for iteration in range(args.runs):
            order = ('before', 'after') if iteration % 2 == 0 else ('after', 'before')
            for variant in order:
                run = uuid.uuid4().hex
                with lock:
                    stats[run] = {'bytes': 0, 'requests': 0, 'next_send': 0.0}
                url = f'http://127.0.0.1:{server.server_port}/{run}/{filename}'
                process = subprocess.Popen([str(binaries[variant]), url, '120', '48000', '1', '0', codec],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    # Native harness emits this immediately at confirmation, before its 2 s playback window.
                    if not select.select([process.stdout], [], [], 45)[0]:
                        raise TimeoutError('Native workload did not confirm playback within 45 seconds')
                    marker = process.stdout.readline()
                    if not marker.startswith('STARTUP '):
                        raise RuntimeError('Native workload did not emit startup confirmation')
                    with lock:
                        startup_bytes = stats[run]['bytes']
                    remaining, errors = process.communicate(timeout=45)
                    if process.returncode:
                        raise RuntimeError(f'Native workload failed ({process.returncode}): {errors[-1000:]}')
                    value = json.loads(remaining.strip().splitlines()[-1])
                    expected = 'decoded' if variant == 'before' else 'progressive'
                    if value['mode'] != expected or (variant == 'after' and (startup_bytes >= size or not value.get('seek_resume_verified'))):
                        raise RuntimeError('Workload did not use the expected route/start before whole delivery')
                    value.update(scenario=suffix, variant=variant, iteration=iteration,
                        input_bytes=size, bytes_sent_at_confirmation=startup_bytes,
                        transfer_seconds=args.transfer_seconds, response_latency_ms=args.latency_ms)
                    rows.append(value)
                    print(json.dumps(value), flush=True)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
        summary = {variant: {field: statistics.median(row[field] for row in rows
            if row['scenario'] == suffix and row['variant'] == variant)
            for field in ('startup_ms', 'bytes_sent_at_confirmation', 'peak_footprint_bytes', 'workload_cpu_ms')}
            for variant in ('before', 'after')}
        print('SUMMARY ' + json.dumps({suffix: summary}), flush=True)
finally:
    server.shutdown()
