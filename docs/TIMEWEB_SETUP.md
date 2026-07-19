# Timeweb Cloud deployment

## 1. PostgreSQL

Create a managed PostgreSQL instance in Timeweb Cloud and copy the connection string into `DATABASE_URL`.

## 2. AI agents

Create three Cloud AI agents:

| Agent | Model | Knowledge base | Web search |
|-------|-------|----------------|------------|
| Agent 1 — Industry Filter | cheap/fast | no | no |
| Agent 2 — Context Relation | cheap/fast | no | no |
| Agent 3 — Main Expert | large | yes | yes |

Upload author materials to the Agent 3 knowledge base (text-layer PDFs or Markdown).

## 3. API keys

Create separate API tokens for each agent and set:

- `TIMEWEB_AGENT_1_ID`, `TIMEWEB_AGENT_1_TOKEN`
- `TIMEWEB_AGENT_2_ID`, `TIMEWEB_AGENT_2_TOKEN`
- `TIMEWEB_AGENT_3_ID`, `TIMEWEB_AGENT_3_TOKEN`

## 4. App Platform

1. Connect the Git repository.
2. Choose Dockerfile deploy.
3. Set environment variables from `.env.example`.
4. Configure health check path `/health`.
5. Deploy and wait until the container is healthy.

## 5. Database migrations

Run once after deploy:

```bash
alembic upgrade head
```

The Docker entrypoint (`scripts/start.sh`) runs this automatically on startup.

## 6. Telegram webhook

1. Obtain the public HTTPS URL of the app.
2. Set `TELEGRAM_WEBHOOK_URL` (host only is OK — path is normalized).
3. Set `TELEGRAM_WEBHOOK_SECRET`.
4. Run:

```bash
python scripts/set_webhook.py
```

## 7. Smoke tests

- `GET /health` → 200
- `GET /ready` → 200 with `"database": true`
- Mention bot in allowed group → reply
- Message without mention → ignored
- Wrong group → bot leaves chat
