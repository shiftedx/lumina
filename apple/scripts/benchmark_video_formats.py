#!/usr/bin/env python3
"""Synthetic FLAC video route qualification using the production decision and FFmpeg command.

Run with backend/.venv/bin/python from the repository root. A fresh output directory is
required. Native playback binary is compiled from benchmark_video_playback.swift.
Loopback server has byte ranges; no household media or credentials are used.
"""
import argparse
from dataclasses import replace
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import select
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.services.local_playback_sessions import ffmpeg_command
from app.services.media_probe import normalize, run_ffprobe
from app.services.playback_decision import caps_from_device_profile, decide

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--client-mkv', action='store_true', help='Compare server remux with native on-device MKV demux')
parser.add_argument('--bytes-per-second', type=int, default=0, help='Optional per-response pacing for startup qualification')
parser.add_argument('--seconds', type=int, default=12)
parser.add_argument('--runs', type=int, default=3)
parser.add_argument('--simulator', help='Run the native binary inside this booted simulator')
args = parser.parse_args()
if args.runs < 1 or args.seconds < 12 or args.bytes_per_second < 0:
    parser.error('runs must be positive, seconds at least 12, and pacing nonnegative')
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=False)
binary = args.binary.resolve(strict=True)
ffmpeg, ffprobe = (shutil.which(tool) for tool in ('ffmpeg', 'ffprobe'))
if not ffmpeg or not ffprobe:
    parser.error('ffmpeg and ffprobe are required')
# Read the actual advertised native audio contract; Swift's request test guards serialization.
source = Path(__file__).resolve().parents[1] / 'Core/Sources/LuminaCore/LuminaClient.swift'
audio = re.search(r'"AudioCodec": "([^"]+)"', source.read_text()).group(1)
caps = caps_from_device_profile({'DirectPlayProfiles': [{'Type': 'Video',
    'Container': 'mp4,m4v,mov,mkv' if args.client_mkv else 'mp4,m4v,mov', 'VideoCodec': 'h264,hevc', 'AudioCodec': audio}],
    'CodecProfiles': [{'Type': 'Video', 'Codec': 'hevc', 'Conditions': [
        {'Property': 'VideoBitDepth', 'Condition': 'LessThanEqual', 'Value': '10'}]}, {'Type': 'VideoAudio', 'Codec': 'flac', 'Conditions': [
        {'Property': 'AudioBitDepth', 'Condition': 'LessThanEqual', 'Value': '24'}]}]})
old = replace(caps, audio=caps.audio - {'flac'}, direct=tuple(
    replace(profile, audio=profile.audio - {'flac'}) for profile in caps.direct))

if args.client_mkv:
    old = replace(caps, direct=tuple(replace(profile, containers=profile.containers - {'mkv', 'matroska'}) for profile in caps.direct))
byte_counts = {}
count_lock = threading.Lock()


class Media(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = (root / unquote(urlsplit(self.path).path).lstrip('/')).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', header)
            if not match:
                self.send_error(416)
                return
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, end)
        if start > end:
            self.send_error(416)
            return
        self.send_response(206 if header else 200)
        mime = 'application/vnd.apple.mpegurl' if path.suffix == '.m3u8' else 'video/mp4'
        self.send_header('Content-Type', mime)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(end - start + 1))
        if header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with path.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if args.bytes_per_second:
                        time.sleep(len(chunk) / args.bytes_per_second)
                    self.wfile.write(chunk)
                    with count_lock:
                        key = str(path.relative_to(root))
                        byte_counts[key] = byte_counts.get(key, 0) + len(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *arguments):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Media)
threading.Thread(target=server.serve_forever, daemon=True).start()
rows = []
try:
    for name, video, channels, rate in [('avc-stereo', 'h264', 2, 48000),
                                      ('avc-51', 'h264', 6, 48000), ('hevc-71', 'hevc', 8, 96000)]:
        path = root / f'{name}.mp4'
        encoder = ['-c:v', 'libx264', '-preset', 'ultrafast'] if video == 'h264' else [
            '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
            '-x265-params', 'pools=1:frame-threads=1:log-level=error', '-tag:v', 'hvc1']
        tones = '|'.join(f'0.04*sin(2*PI*{440 + 110*i}*t)' for i in range(channels))
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
            f'testsrc2=size=1280x720:rate=24:duration={args.seconds}', '-f', 'lavfi', '-i',
            f'aevalsrc={tones}:s={rate}:d={args.seconds}', *encoder, '-g', '48', '-c:a', 'flac',
            '-sample_fmt', 's32', '-bits_per_raw_sample', '24', '-strict', 'experimental', '-movflags', '+faststart', str(path)],
            check=True, timeout=120)
        prefix = ['xcrun', 'simctl', 'spawn', args.simulator] if args.simulator else []
        subprocess.run([*prefix, str(binary), str(path)], check=True, capture_output=True, timeout=45)
        mkv = path.with_suffix('.mkv')
        subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-c', 'copy', str(mkv)], check=True, timeout=30)
        for source in ((mkv,) if args.client_mkv else (path, mkv)):
            facts = normalize(run_ffprobe(ffprobe, source), source.name)
            decisions = {'before': decide(old, facts), 'after': decide(caps, facts)}
            expected = 'direct' if args.client_mkv or source.suffix == '.mp4' else 'remux'
            assert decisions['before'].audio == ('copy' if args.client_mkv else 'encode') and decisions['after'].mode == expected
            assert decisions['after'].video == 'copy' and decisions['after'].audio == 'copy'
            for iteration in range(args.runs):
                for variant in (('before', 'after') if iteration % 2 == 0 else ('after', 'before')):
                    decision = decisions[variant]
                    row = dict(case=source.name, variant=variant, iteration=iteration, mode=decision.mode,
                        ffmpeg_cpu_ms=0, ffmpeg_wall_ms=0, ffmpeg_peak_rss_bytes=0)
                    target = source
                    if decision.mode != 'direct':
                        directory = root / f'{source.name}-{variant}-{iteration}'
                        directory.mkdir()
                        command = ffmpeg_command(ffmpeg, source, facts, directory, decision)
                        with tempfile.TemporaryFile() as errors:
                            started = time.monotonic()
                            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors)
                            _, status, usage = os.wait4(process.pid, 0)
                            process.returncode = os.waitstatus_to_exitcode(status)
                            errors.seek(0)
                            if process.returncode:
                                raise RuntimeError(errors.read().decode()[-2000:])
                        row.update(ffmpeg_wall_ms=(time.monotonic()-started)*1000,
                            ffmpeg_cpu_ms=(usage.ru_utime+usage.ru_stime)*1000,
                            ffmpeg_peak_rss_bytes=usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024))
                        target = directory / 'index.m3u8'
                        # The retained audio stream is really FLAC in the new remux, AAC in the old encode.
                        probe = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams',
                            '-of', 'json', str(target)], timeout=30))
                        audio_stream = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
                        assert audio_stream['codec_name'] == ('flac' if args.client_mkv or variant == 'after' else 'aac')
                        if variant == 'after':
                            assert audio_stream['channels'] == channels
                    url = f'http://127.0.0.1:{server.server_port}/{target.relative_to(root)}'
                    prefix = ['xcrun', 'simctl', 'spawn', args.simulator] if args.simulator else []
                    with count_lock:
                        byte_counts[str(source.relative_to(root))] = 0
                    mode = ['mkv'] if args.client_mkv and variant == 'after' else []
                    process = subprocess.Popen([*prefix, str(binary), url, *mode], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
                    try:
                        if not select.select([process.stdout], [], [], 45)[0] or process.stdout.readline().strip() != 'FIRST_FRAME':
                            raise RuntimeError('Native workload did not confirm a decoded first frame')
                        with count_lock:
                            row['source_bytes_at_first_frame'] = byte_counts.get(str(source.relative_to(root)), 0)
                        output, errors = process.communicate(timeout=45)
                        if process.returncode:
                            raise RuntimeError(f'Native qualification failed: {errors[:1500]}')
                        row.update(json.loads(output.strip().splitlines()[-1]))
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()
                    with count_lock:
                        row['source_bytes_sent'] = byte_counts.get(str(source.relative_to(root)), 0)
                    row['source_bytes'] = source.stat().st_size
                    rows.append(row)
                    print(json.dumps(row), flush=True)
            summary = {variant: {key: statistics.median(row[key] for row in rows
                if row['case'] == source.name and row['variant'] == variant) for key in (
                'ffmpeg_cpu_ms', 'ffmpeg_wall_ms', 'ffmpeg_peak_rss_bytes', 'first_frame_ms',
                'startup_plus_one_second_cpu_ms', 'seek_plus_200ms_ms', 'native_peak_rss_bytes')}
                for variant in ('before', 'after')}
            print('SUMMARY ' + json.dumps({source.name: summary}), flush=True)
finally:
    server.shutdown()
