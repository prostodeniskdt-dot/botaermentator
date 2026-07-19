# Fermentation Expert Telegram Bot (MVP)

Closed-group Telegram consultant for fermentation and hospitality, backed by three Timeweb Cloud AI agents.

## Stack

Python 3.12 · FastAPI · aiogram 3 · PostgreSQL · SQLAlchemy 2 · Timeweb Cloud AI · Docker

## Status

Stage 1 scaffold: FastAPI app with `/health` and `/ready`, configuration, Docker, base tests.

See `docs/IMPLEMENTATION_PLAN.md` and `docs/ARCHITECTURE.md`.

## Local development (Stage 1)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
copy .env.example .env   # or: cp .env.example .env

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Checks:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready

ruff check .
ruff format --check .
pytest
```

## Docker

```bash
docker build -t botpodergun .
docker run --rm -p 8080:8080 -e APP_ENV=development botpodergun
```

## Timeweb App Platform

- Deploy type: **Dockerfile**
- Health check path: `/health`
- Start command (if the panel overrides Docker CMD):  
  `uvicorn app.main:app --host 0.0.0.0 --port 8080`  
  **Not** `botpodergun.main:app` — Python-пакет приложения называется `app`.
- Leave the start command empty to use the Dockerfile `CMD`/`ENTRYPOINT`.

## Secrets

Never commit `.env`, tokens, chat IDs, or agent IDs. Use `.env.example` as a template only.

## Platform setup (your side)

Telegram BotFather, Timeweb agents/KB/Postgres/App Platform — see the kickoff plan and (later) `docs/TIMEWEB_SETUP.md`.
