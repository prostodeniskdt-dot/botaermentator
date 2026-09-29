# Fermentation Expert Telegram Bot (MVP)

Allow-listed private and closed-group Telegram consultant for fermentation and hospitality,
backed by three Timeweb Cloud AI agents.

## Stack

Python 3.12 · FastAPI · aiogram 3 · PostgreSQL · SQLAlchemy 2 · Timeweb Cloud AI · Docker

## Status

Production-oriented bot with:

- administrator-approved private access;
- persistent personal conversations and confirmed profile facts;
- concise and detailed answer modes;
- internal credits with manual grants (no payment integration);
- durable PostgreSQL queues for Telegram updates and answer delivery;
- group questions through `/ask`, `@mention`, or reply;
- AI usage, feedback, limits, blocking, and versioned FAQ cache.

See `docs/IMPLEMENTATION_PLAN.md`, `docs/ARCHITECTURE.md`, and
`docs/TIMEWEB_SETUP.md`.

## Local development

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
- Project directory: empty or `/`
- Health check path: `/health` (ignored if Dockerfile `HEALTHCHECK` is set — keep it)
- Port: **8080** (`PORT` / `APP_PORT` env supported)
- Start command: leave **empty** (uses `/start.sh` from Dockerfile)
- Do **not** use `main:app` or `botpodergun.main:app`

## Secrets

Never commit `.env`, tokens, chat IDs, or agent IDs. Use `.env.example` as a template only.

## Platform setup (your side)

Telegram BotFather, Timeweb agents/KB/Postgres/App Platform — see the kickoff plan and (later) `docs/TIMEWEB_SETUP.md`.

## Private access workflow

1. A user sends `/start`.
2. The bot records a pending request and tells the user to contact `@pprostodenis`.
3. An administrator receives the Telegram ID and runs `/admin_allow <id>`.
4. The user receives access and the configured starting credits.

User identity and authorization are based on immutable Telegram user ID, never username.
Payment providers, Telegram Stars, and package purchases are intentionally out of scope.
