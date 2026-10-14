"""Fresh-process ABBA comparison of a baseline backend and Python hot-path changes.

Usage: python scripts/perf/python_hotpaths.py DATABASE BASELINE_BACKEND --output FILE
Measures services, not HTTP/network/video decoding. No runtime dependency added.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def worker(args):  # noqa: ANN001, ANN201
    sys.path.insert(0, str(args.backend))
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.models import User
    from app.services.jellyfin import ItemsQuery, JellyfinMapper
    from app.services.semantic_discovery import search_ids
    engine = create_engine(f"sqlite:///{args.database}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    queries = ('amber', 'bridge', 'cedar', 'harbor', 'island', 'river', 'garden', 'north')

    def request(kind, n):  # noqa: ANN001, ANN202
        with sessions() as db:
            user = db.scalar(select(User).where(User.username == 'perfadmin'))
            if kind == 'search':
                return [asdict(ref) for ref in search_ids(db, user, queries[n % len(queries)], types=None, limit=200)]
            query = ItemsQuery(recursive=True, includeitemtypes=['Movie'], limit=60, fields=['MediaSources'])
            return JellyfinMapper(db, user, query.csv('fields')).items(query)

    results = {}
    for kind in ('page', 'search'):
        expected = [request(kind, n) for n in range(len(queries))]
        samples = []
        start_cpu = time.process_time()
        for n in range(args.samples):
            start = time.perf_counter_ns()
            actual = request(kind, n)
            samples.append((time.perf_counter_ns() - start) / 1e6)
            assert actual == expected[n % len(queries)]
        cpu = (time.process_time() - start_cpu) * 1000 / args.samples
        ordered = sorted(samples)
        results[kind] = {'p50_ms': statistics.median(samples), 'p95_ms': ordered[int(len(ordered) * .95) - 1],
                         'cpu_ms_per_request': cpu, 'raw_ms': samples,
                         'response_sha256': hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()}
    rss = (int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE') if sys.platform == 'linux'
           else int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(os.getpid())], text=True)) * 1024)
    print(json.dumps({'service': results, 'rss_mib': rss / 1024**2, 'python': sys.version.split()[0]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('--candidate', type=Path, default=Path(__file__).resolve().parents[2] / 'backend')
    parser.add_argument('--samples', type=int, default=300)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--backend', type=Path)
    args = parser.parse_args()
    if args.backend:
        worker(args)
        return
    blocks = []
    for name in ('baseline', 'candidate', 'candidate', 'baseline', 'candidate', 'baseline', 'baseline', 'candidate'):
        command = [sys.executable, str(Path(__file__).resolve()), str(args.database), str(args.baseline),
                   '--backend', str(getattr(args, name)), '--samples', str(args.samples)]
        result = json.loads(subprocess.check_output(command, text=True))
        blocks.append({'revision': name, **result})
        for kind in ('page', 'search'):
            assert result['service'][kind]['response_sha256'] == blocks[0]['service'][kind]['response_sha256'], kind
    output = json.dumps({'benchmark': 'python_hotpaths_034', 'samples_per_block': args.samples, 'blocks': blocks}, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    else:
        print(output)


if __name__ == '__main__':
    main()
