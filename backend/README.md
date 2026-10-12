# Lumina Backend

FastAPI backend that wraps `yt_dlp.YoutubeDL`, persists job and library state in SQLite, and emits structured server-sent events for the UI.

## Development

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8765
```

The database is stored under `backend/.data/app.db` by default.
