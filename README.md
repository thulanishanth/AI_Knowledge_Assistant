# AI Knowledge Assistant

An AI-powered FastAPI application that translates natural language questions into reliable answers using retrieval, validation, and SQL-aware orchestration.

## Features

- Natural-language chat interface served from `/`
- REST API endpoint for chat requests: `POST /api/chat/`
- Confidence scoring returned with each answer
- SQL validation and execution pipeline for data-backed queries
- Configurable environment via `.env`
- Request-level structured logging with request IDs

## Project Structure

```text
app/
  api/            # FastAPI route handlers
  db/             # MySQL access and schema helpers
  services/       # LLM, retrieval, SQL, intent, formatting logic
  utils/          # Logging and shared helpers
  data/           # Prompt/reference text files
scripts/
  run_lint.ps1    # Lint entrypoint
```

## Prerequisites

- Python 3.10+
- MySQL (for SQL-backed assistant flows)
- `pip`

## Quick Start

1. Create and activate a virtual environment.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Configure environment variables in `.env`.

4. Start the API server.

```powershell
uvicorn app.main:app --reload
```

5. Open `http://127.0.0.1:8000`.

## Configuration

Supported environment variables (with defaults where defined in code):

- `DB_HOST` (`localhost`)
- `DB_PORT` (`3306`)
- `DB_USER` (`root`)
- `DB_PASSWORD` (empty)
- `DB_NAME` (`hotel_db`)
- `DB_TABLE` (`hotel_reservations`)
- `HF_API_KEY` (empty)
- `HF_MODEL` (`katanemo/Arch-Router-1.5B`)
- `CLOUD_API_KEY` (empty)
- `MAX_QUERY_RESULTS` (`10`)
- `LOG_LEVEL` (`INFO`)
- `LOG_FILE` (empty; logs to console when unset)

## API Usage

### Chat Endpoint

- Method: `POST`
- Path: `/api/chat/`
- Content-Type: `application/json`

Request body:

```json
{
  "question": "How many confirmed bookings do we have?"
}
```

Response body:

```json
{
  "answer": "...",
  "confidence": 0.92
}
```

## Linting

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_lint.ps1
```

## Development Notes

- Keep secrets in `.env`; do not commit credentials.
- Run lint checks before pushing changes.
- Review database connectivity and schema settings before local testing.