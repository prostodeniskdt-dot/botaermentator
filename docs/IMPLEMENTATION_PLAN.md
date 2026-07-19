# Implementation Plan — MVP Telegram + Timeweb

## Repository status (kickoff)

- Only `CURSOR_INSTRUCTIONS_MVP_TELEGRAM_TIMEWEB.md` existed.
- No application code, git history, Docker, or Python toolchain in the project yet.
- This plan drives staged delivery; do not land the full app in one change.

## Goal

Production-ready MVP: closed-group fermentation/hospitality expert bot on Timeweb App Platform (Python 3.12, FastAPI, aiogram 3, PostgreSQL, three Timeweb AI agents).

## Target tree

```text
app/
  main.py
  config.py
  logging.py
  api/
    health.py
    telegram_webhook.py
  bot/
    factory.py
    filters/
    routers/
      group_questions.py
      private_messages.py
      admin.py
      membership.py
    formatting/
  domain/
  services/
    request_gate.py
    question_service.py
    session_service.py
    rate_limit_service.py
    blocking_service.py
    usage_service.py
  agents/
    base.py
    timeweb_client.py
    industry_filter.py
    context_relation.py
    main_expert.py
    schemas.py
  db/
    base.py
    session.py
    models/
    repositories/
  prompts/
    agent_1_industry_filter.md
    agent_2_context_relation.md
    agent_3_main_expert.md

migrations/
scripts/
  set_webhook.py
  delete_webhook.py
  block_user.py
  unblock_user.py
  block_session.py
  unblock_session.py
tests/
docs/
  ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  TIMEWEB_SETUP.md          # stage 10
Dockerfile
pyproject.toml
alembic.ini
.env.example
README.md
```

## Stages

### Stage 1 — Skeleton (done)

Completed:

- `pyproject.toml` (FastAPI, uvicorn, pydantic-settings, structlog, ruff, pytest)
- `app/config.py` with production fail-fast for critical env
- structlog JSON logging
- FastAPI `GET /health`, `GET /ready` (config checks; DB placeholder until stage 2)
- Dockerfile (Python 3.12 slim, non-root, port 8080)
- `.env.example`, `.gitignore`, README
- Base tests: health, ready, settings
- Gate green: `ruff check .`, `ruff format --check .`, `pytest` (7 passed)

Out of scope for stage 1: Telegram, PostgreSQL, AI.

### Stage 2 — PostgreSQL

Models, repositories, Alembic, async session, `/ready` checks DB, integration tests.

### Stage 3 — Telegram surface

aiogram factory, polling (dev) + webhook (prod), secret, allowed chat, mention/`/ask`/reply parsers, private stub, auto-leave, fake question service.

### Stage 4 — Timeweb HTTP client

Typed client, timeouts, retries, mocks (`respx` / MockTransport).

### Stage 5 — Agent 1

Industry filter, JSON validation, corrective retry, fail closed.

### Stage 6 — Agent 2

Context relation + standalone fallback.

### Stage 7 — Agent 3

Main expert, safe HTML, message splitting.

### Stage 8 — Orchestrator

Sessions, usage events, persist Q/A, end-to-end reply path.

### Stage 9 — Hardening

Limits, duplicates, junk score, blocks, budget, admin commands, kill switch, CLI scripts.

### Stage 10 — Ops

`docs/TIMEWEB_SETUP.md`, webhook scripts, smoke checklist, full test suite.

## Risks

| Risk | Mitigation |
|------|------------|
| Privacy Mode off → all group traffic | Strict Request Gate; zero persistence/logging of non-addressed text |
| Timeweb JSON agents return markdown fences | Strip fences + one corrective retry; fail closed for Agent 1 |
| Agent cost overruns | Daily budget + estimated costs + kill switch |
| Duplicate Telegram deliveries | `processed_updates` + idempotent webhook |
| Wrong group / leaked bot | `ALLOWED_CHAT_ID` + `leaveChat` on foreign membership |
| Secrets in Git | `.env.example` only; fail startup if missing in production |
| Long Agent 3 latency | Read timeout 120s, typing after gate, static error on failure |
| HTML injection from model | Escape all dynamic text; controlled tags only |

## Contested / fixed decisions

1. **Privacy Mode off** — default MVP UX is `@mention`; alternative would be `/ask` + replies only.
2. **No Tribute/Stars API** — group membership is the pass.
3. **Three separate agents** — not one multi-tool agent; clearer cost and safety.
4. **Web search toggled only in Timeweb panel** — code never assumes per-request control.
5. **No `parent_message_id` for user history** — app owns context.
6. **`/ready` in stage 1** — returns config readiness; DB probe added in stage 2.
7. **Dev polling vs webhook** — polling locally; webhook only in production.

## Definition of Done (MVP)

Matches instruction §27: Docker build, `:8080`, health/ready, gated webhook, mention/`/ask`/reply, leave foreign chats, three agents, sessions, limits, budget, no secrets in Git, tests green, README + Timeweb docs.

## Quality gate (every stage)

```bash
ruff check .
ruff format --check .
pytest
```

Do not advance with failing tests.
