"""Profile the measured local-search hotspot on disposable household fixtures.

Calls the ordinary route function directly: excludes HTTP/auth/threadpool cost.
No model weights are installed by this audit; the effective mode is retained.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lumina-search-profile-') as temporary:
        data = Path(temporary) / 'data'
        subprocess.run([sys.executable, str(ROOT / 'scripts/perf/seed_household.py'), str(data)], stdout=subprocess.DEVNULL, check=True)
        os.environ['LUMINA_DATA_DIR'] = str(data)
        sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts')]
        import test_safety
        test_safety.LoopbackOnlyGuard().apply(lambda target, name, value: setattr(target, name, value))
        from sqlalchemy import event
        from app import main as app_main
        from app.db import SessionLocal, engine
        from app.models import User

        queries = []
        @event.listens_for(engine, 'before_cursor_execute')
        def before(connection, cursor, statement, parameters, context, executemany):
            context._audit_started = time.perf_counter()
        @event.listens_for(engine, 'after_cursor_execute')
        def after(connection, cursor, statement, parameters, context, executemany):
            queries.append({'sql': statement, 'execute_ms': (time.perf_counter()-context._audit_started)*1000})

        result = {'scope': 'direct synchronous search route with cProfile instrumentation overhead; 20k household seed; excludes HTTP/auth; SQL execute excludes fetching and ORM hydration; first call per query does not flush OS or earlier application caches', 'queries': []}
        with SessionLocal() as db:
            user = db.query(User).filter_by(username='perfadmin').one()
            profiler = cProfile.Profile()
            for query in ('river', 'river forest', 'zzznomatch'):
                trials = []
                for number in range(21):
                    queries.clear()
                    started, cpu = time.perf_counter(), time.process_time()
                    profiler.enable()
                    response = app_main.search_library(q=query, limit=12, scope=None, current_user=user, db=db)
                    profiler.disable()
                    trials.append({'phase': 'first' if number == 0 else 'repeat', 'wall_ms': (time.perf_counter()-started)*1000, 'cpu_ms': (time.process_time()-cpu)*1000, 'matches': len(response.matches), 'mode': response.mode, 'sql_count': len(queries), 'sql_execute_ms': sum(row['execute_ms'] for row in queries)})
                result['queries'].append({'query': query, 'trials': trials, 'last_sql': list(queries)})
        text = io.StringIO()
        pstats.Stats(profiler, stream=text).strip_dirs().sort_stats('cumulative').print_stats(45)
        (args.out / 'cumulative.txt').write_text(text.getvalue())
        (args.out / 'raw.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
