# Timeweb Cloud deployment

## 1. PostgreSQL

Create a managed PostgreSQL instance in Timeweb Cloud and copy the connection string into `DATABASE_URL`.

If authentication fails with `InvalidPasswordError`, the password likely contains special characters that break the URL. Either:

- URL-encode the password (e.g. `@` → `%40`), or
- set `DATABASE_PASSWORD` to the raw password from the Timeweb panel (keep user/host/db in `DATABASE_URL`).

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

Required private-access settings:

```dotenv
ADMIN_USER_IDS=123456789
PRIVATE_ACCESS_ENABLED=true
ACCESS_CONTACT_USERNAME=pprostodenis
STARTING_CREDITS=5
QUICK_MODE_CREDITS=1
DEEP_MODE_CREDITS=5
DURABLE_UPDATE_QUEUE_ENABLED=true
```

`ADMIN_USER_IDS` cannot be empty in production while private access is enabled.
Use Telegram numeric user IDs; usernames are display-only.

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
- Unknown private user sends `/start` → pending-access message
- Administrator receives the request and runs `/admin_allow <telegram_user_id>`
- Approved user receives the starting balance and can ask a private question
- `/quick`, `/deep`, `/new`, `/remember`, `/profile`, `/forget`, and `/balance` work
- Restart during processing → queued update or answer delivery resumes

## 8. BotFather

Set the public commands:

```text
start - Начать работу
help - Помощь
new - Начать новую тему
quick - Краткий режим
deep - Подробный режим
mode - Текущий режим
balance - Баланс кредитов
remember - Сохранить подтвержденный факт
profile - Показать подтвержденные факты
forget - Удалить рабочую память
privacy - Использование данных
```

Keep the bot token only in Timeweb secrets. If group `@mention` behavior is retained,
configure Group Privacy consistently with the group scenario and test it before production.

## 9. Administrator commands

- `/admin_pending`
- `/admin_allow <telegram_user_id>`
- `/admin_reject <telegram_user_id>`
- `/admin_user <telegram_user_id>`
- `/admin_grant_credits <telegram_user_id> <amount> [reason]`
- `/admin_block_user <telegram_user_id> <reason>`
- `/admin_unblock_user <telegram_user_id>`
- `/admin_cost_today`
- `/admin_kill_switch_on` / `/admin_kill_switch_off`

Credits are internal accounting units. There is no payment or Telegram Stars integration.
