# TPT Miro Backend

FastAPI backend for **Timepiece Trading** — watch identify, inventory, virtual try-on, listing drafts.

## Run locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your keys
uvicorn main:app --reload --port 8001
```

Health check: `GET http://localhost:8001/api/health`

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `RAPID_API_KEY` | Yes | Google Lens via RapidAPI |
| `IMGBB_API_KEY` | Recommended | Image hosting for Lens |
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | For listing draft | AI listing copy |
| `API4AI_API_KEY` | Optional | Direct try-on API (falls back to RapidAPI) |

## Deploy (Render / Railway / Fly)

1. Connect this repo
2. Set build command: `pip install -r requirements.txt`
3. Set start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add env vars from `.env.example`
5. Note your public URL (e.g. `https://tptmirobackend.onrender.com`)

CORS is open (`*`) so the frontend can call this API from any domain.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health + feature flags |
| GET | `/api/inventory` | TPT inventory watches |
| POST | `/api/identify/full` | Lens identify + matches |
| POST | `/api/try-on` | Virtual wrist try-on |
| POST | `/api/listing-draft` | Dealer listing draft |
