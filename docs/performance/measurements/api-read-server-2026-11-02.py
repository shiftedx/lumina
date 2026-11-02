import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'scripts/perf'))
import serve
import uvicorn
uvicorn.run(serve.main.app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning',lifespan='off')
