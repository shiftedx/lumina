"""Paired read-only experiment for the profiled transcript candidate join.

Compares the current production candidate-driven predicate with the former
unwrapped visibility predicate on the same synthetic database.  It executes
captured route SQL directly, so it excludes HTTP, ORM hydration, and ranking;
the service tests qualify restricted-member semantics separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True, help='raw.json from audit_search_profile on the same source revision')
    args = parser.parse_args()
    os.environ['LUMINA_DATA_DIR'] = str(args.data_dir.resolve())
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.services.library_search import build_match
    from app.services.semantic_discovery import DEFAULT_INTENT_LEXICON, _content_tokens

    profile = json.loads(args.profile.read_text())
    result = {'scope': 'Read-only SQL execute+fetch microbenchmark on a disposable synthetic household; paired alternating order, warm cache; excludes HTTP/ORM/ranking. Exact ordered result equality checked every trial. The current captured route statement is compared with the preceding unwrapped predicate; restricted-member semantics are qualified by service regressions.',
              'sqlite': sqlite3.sqlite_version, 'samples_per_variant': 20, 'p95_method': 'nearest rank ceil(0.95*N)', 'cases': []}
    with sqlite3.connect((args.data_dir / 'app.db').resolve().as_uri()+'?mode=ro', uri=True) as db:
        users = db.execute('SELECT id,role FROM users ORDER BY username').fetchall()
        for user_index, (owner, role) in enumerate(users):
            for group in profile['queries']:
                current = next(row['sql'] for row in group['last_sql'] if 'bm25(transcript_fts)' in row['sql'])
                if role != 'admin':
                    current = current.replace(' OR li.user_id IS NULL', '')
                legacy = current.replace("coalesce(li.visibility, '') = 'shared'", "li.visibility = 'shared'").replace("coalesce(li.user_id, '') = ?", 'li.user_id = ?')
                if legacy == current:
                    raise RuntimeError('profile did not capture the candidate-driven production predicate')
                match = build_match([group['query'], *DEFAULT_INTENT_LEXICON.expand(_content_tokens(group['query']))])
                bindings = (match, 500, owner, 200)
                queries = {'legacy_unwrapped': legacy, 'current_candidate_driven': current}
                expected = db.execute(current, bindings).fetchall()
                samples = {name: [] for name in queries}
                plans = {name: list(db.execute('EXPLAIN QUERY PLAN '+sql, bindings)) for name,sql in queries.items()}
                for trial in range(20):
                    for name in (("legacy_unwrapped", "current_candidate_driven") if trial % 2 == 0
                                 else ("current_candidate_driven", "legacy_unwrapped")):
                        started,cpu = time.perf_counter(),time.process_time()
                        actual = db.execute(queries[name], bindings).fetchall()
                        samples[name].append({'wall_ms': (time.perf_counter()-started)*1000, 'process_cpu_ms': (time.process_time()-cpu)*1000})
                        if actual != expected:
                            raise RuntimeError('Query variant changed ordered visible results')
                summaries = {}
                for name, rows in samples.items():
                    values = sorted(row['wall_ms'] for row in rows)
                    summaries[name] = {'median_ms': statistics.median(values), 'p95_ms': values[math.ceil(.95*len(values))-1],
                                      'median_cpu_ms': statistics.median(row['process_cpu_ms'] for row in rows)}
                result['cases'].append({'user_index': user_index, 'role': role, 'query': group['query'], 'fts_match': match,
                    'rows': len(expected), 'ordered_rows_sha256': hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
                    'equal': True, 'summaries': summaries, 'plans': plans, 'samples': samples})
                args.out.parent.mkdir(parents=True,exist_ok=True)
                args.out.write_text(json.dumps(result,indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
