# Architecture — Telegram Fermentation Expert Bot (MVP)

## Purpose

Allow-listed private and closed-group Telegram bot that answers hospitality/fermentation
questions using three Timeweb Cloud AI agents and an author knowledge base. Private access is
approved manually by administrators. Payments are out of scope.

## High-level flow

```text
Telegram update
      |
      v
FastAPI POST /telegram/webhook
  - X-Telegram-Bot-Api-Secret-Token
      |
      v
PostgreSQL update queue
      |
      v
aiogram dispatcher
      |
      v
Request Gate (cheap, no AI)
  1. dedup update_id
  2. reject bots / missing from_user
  3. allowed chat_id (else ignore or leaveChat)
  4. mention / /ask / reply-to-bot
  5. user/session blocks
  6. rate limits + concurrent
  7. strip bot address, empty/length checks
  8. duplicate question window
  9. kill switch + daily AI budget
      |
      v
Question orchestrator
      |
      +--> Agent 1 Industry Filter (cheap, JSON)
      |      off-topic / fail closed -> static reply
      |
      +--> Agent 2 Context Relation (reply only)
      |      related | standalone | ambiguous
      |      timeout/invalid -> standalone fallback
      |
      +--> Agent 3 Main Expert (KB + web search)
      |
      v
Transactional answer-delivery outbox
      |
      v
Rich Markdown / safe HTML formatter + split
      |
      v
Telegram reply (same chat / topic)
```

Telegram never talks to Timeweb AI agents directly. Our backend owns context, sessions, and gating.

## Runtime modes

| Mode | Transport | Notes |
|------|-----------|--------|
| Development | Long polling | Separate test bot; call `delete_webhook` first |
| Production | Webhook only | HTTPS App Platform URL + secret token |

## Components

### API layer (`app/api`)

- `GET /health` — process alive; no DB/AI.
- `GET /ready` — config + PostgreSQL; no paid AI calls.
- `POST /telegram/webhook` — secret check → aiogram feed.

### Bot layer (`app/bot`)

Thin routers: group questions, private messages, admin, membership (`my_chat_member`). Business logic lives in services, not handlers.

### Domain / services (`app/services`)

- `request_gate` — all pre-AI checks.
- `question_service` — orchestration.
- `session_service` — UUID sessions, topics, reply continuity.
- `rate_limit_service`, `blocking_service`, `usage_service`.

### Agents (`app/agents`)

Shared `TimewebClient` → `POST /api/v1/cloud-ai/agents/{id}/call` with timeouts, max 2 attempts, exponential backoff.

| Agent | Role | KB | Web search | On failure |
|-------|------|----|------------|------------|
| 1 Industry Filter | Allow/deny + normalize | No | No | Fail closed |
| 2 Context Relation | Reply continuity | No | No | Standalone fallback |
| 3 Main Expert | Final answer | Yes | Yes (panel) | Static error |

App-managed context: group replies use the previous Q/A after Agent 2. Private conversations
store the complete Q/A history in PostgreSQL. Agent 3 receives a compact working summary,
the newest clipped turns, and confirmed profile facts. Full previous answers are not
replayed as a rolling transcript.

### Persistence (`app/db`)

PostgreSQL via SQLAlchemy 2 + asyncpg. Schema changes only through Alembic.

Key tables: `telegram_users`, `chat_sessions`, `user_questions`, `bot_responses`,
`processed_updates`, `ai_usage_events`, `rate_limit_counters`, `block_events`,
`credit_accounts`, `credit_transactions`, `access_events`, `user_feedback`,
`telegram_update_jobs`, `telegram_delivery_jobs`, `system_settings`.

Telegram IDs stored as `BIGINT`. Persist text only for explicit bot invocations.

## Trust and privacy boundaries

1. Group Privacy Mode is off → backend receives all group messages.
2. Non-addressed messages: ignore immediately — no DB user/session, no logs of text, no AI.
3. Secrets never in Git or logs.
4. Production logs: ids, categories, latency, status, estimated cost — not full Q/A or prompts.

## Access model

- One `ALLOWED_CHAT_ID`.
- Wrong group: on `my_chat_member` join → `leaveChat`, no AI.
- Private chat: `/start` creates one pending access request.
- Administrators approve by immutable Telegram user ID.
- Pending/rejected/blocked users cannot call AI.
- Approved users receive a one-time configurable starting credit grant.
- Internal credits are reserved before processing and committed only for a successful answer.
- Paid membership managed externally (e.g. Tribute); bot does not integrate payments.

## Deployment shape

```text
Timeweb App Platform (Dockerfile → uvicorn :8080)
        |
        +--> Timeweb Managed PostgreSQL
        |
        +--> Timeweb Cloud AI (3 agents)
        |
Telegram webhook HTTPS → /telegram/webhook
```

## Decision log (MVP)

| Decision | Choice | Why |
|----------|--------|-----|
| Privacy Mode | Off | Enable `@mention` UX |
| Payments | Out of scope | Group membership = pass |
| Agent routing | Three separate Timeweb agents | Cost control + fail-closed filter |
| Context | App-owned, max 1 prior turn | Predictable cost/quality |
| Local transport | Polling | Simpler than tunnels |
| Prod transport | Webhook | Required by App Platform |
| HTML | Escape model output | Never trust model markup |
