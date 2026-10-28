# Response speed and Telegram layout

## Findings and limits

The earlier webhook could await the entire question handler when durable ingestion was disabled.
The receipt also waited for context and topic classification. Consequently, a late receipt did not
mean the main expert was slow. Follow-up classification was sequential, and one delivery worker
handled answers from all chats.

The supplied Telegram answer demonstrates excessive gaps and a word split across message parts.
It is not a timing log. Local rendering of that sample took about 10 ms both before and after the
fix: formatting explains the layout defect, not a minute of waiting. The old output contained 75
runs of three or more newlines; the corrected output contained none. The private sample is not
included in the repository; regression tests use synthetic examples.

There is no measured production result yet and no five-second response guarantee. No production
credentials were available for a live request or deployment. The selected model and Timeweb panel
settings are unchanged. Timeouts have not been shortened to turn long requests into quick errors.

## Current processing

- With `DATABASE_URL` configured, the webhook commits the update to PostgreSQL and returns before
  AI processing, even if an old environment has `DURABLE_UPDATE_QUEUE_ENABLED=false`. Only isolated
  development without a database can use the synchronous path.
- After local access/content checks and durable question creation, the bot sends the neutral
  receipt “Сообщение получил, проверяю вопрос.” before AI classification. This acknowledges receipt;
  topic approval still precedes expert generation. A rejected topic can therefore receive a receipt
  followed by a refusal. Notice cleanup, restart recovery and duplicate protection remain active.
- With previous Q/A and no potential cached answer, context and topic classification run in
  parallel. Each uses its own database session with the same processing-owner fence. The filter
  receives the original question and a follow-up indicator, without a potentially sanitizing rewrite.
  The expert runs only after both checks and all protection flags pass. All three agents remain.
- Credits are reserved before the parallel calls and released on rejection or failure. A possible
  cached answer keeps the context-first path so it can still be served without credits. Personal
  context and rejected filter results cannot enter the shared-cache path.
- Two delivery workers serve different chats independently. PostgreSQL claims preserve order within
  one chat, including backoff and stale recovery. Service commands retain reserved worker capacity.
- Quick requests a complete concise answer, normally 60–100 words when sufficient. Deep retains
  its detailed scope. Required safety information is preserved; output limits stay 600/2200 tokens.

## API and configuration

`TIMEWEB_PREFERRED_TRANSPORT=openai` now tries Chat Completions with `reasoning_effort=none`, then
Responses with `reasoning.effort=none`, before native and plain Responses fallbacks. Successful
transports are cached, avoiding repeated negotiation. Compatibility fallback remains bounded;
authentication/quota failures and unknown timeout outcomes are not replayed through another API.
`native` explicitly selects the previous priority. The code does not switch the agent's model.

Reasoning fields are requests, not proof that a particular model honors them. Native `/call` uses
panel settings; successful native/plain fallback logs `timeweb_panel_reasoning_default`. Actual
returned model, transport and attempt count are persisted alongside AI timings. JSON-correction
retries record their own duration rather than counting the preceding request twice.

Relevant Timeweb documentation:

- [Native API and Playground settings](https://timeweb.cloud/docs/ai-agents/api-usage/native-api)
- [OpenAI-compatible API](https://timeweb.cloud/docs/ai-agents/api-usage/openai-compatible-api)
- [Models and reasoning settings](https://timeweb.cloud/docs/ai-agents/pricing/models)

Defaults in `.env.example` include `PARALLEL_CLASSIFIERS_ENABLED=true`,
`DELIVERY_QUEUE_CONCURRENCY=2`, `UPDATE_QUEUE_CONCURRENCY=2`, `RICH_MESSAGES_ENABLED=false` and
`PROMPT_VERSION=6`. Existing explicit environment values override ordinary defaults; update them
when adopting the new configuration. Durable ingestion with a database is mandatory as described
above. Account for all processes when sizing database and provider capacity. No schema change is
required beyond the existing migrations.

## Telegram formatting

Standard sanitized HTML is the default. Paragraph gaps and list markers are normalized, Markdown
fences explicitly labeled as Markdown are unwrapped, and code whitespace is preserved. Splitting
keeps ordinary words intact, balances tags and respects UTF-16 limits. Only a token longer than an
entire message needs splitting. Optional Rich delivery remains available; its fallback renders HTML
instead of exposing raw Markdown, and it is selected only when the fallback fits one message.

## Verify after deployment

Deploy the updated `main` with the existing credentials and migrations. Send a fresh Quick question,
a contextual follow-up and a Deep question. Check receipt timing, answer delivery, list spacing and
word boundaries. Then use `/admin_latency` from an administrator account.

The command reports only that administrator's last three requests: queue/local-check time, receipt
registration, individual AI-call durations, returned model/transport, completion of delivery and time
after the last AI call. Parallel stage durations must not be added together. Receipt registration is
a persisted timestamp after the send returns, not Telegram display telemetry. Delivery totals include
local persistence and cleanup. The report does not expose question text, tokens or agent IDs.

Automated coverage includes PostgreSQL transactions, credit refunds, early receipt before blocked
classification, parallel classifier sessions, cached answers with zero balance, concurrent delivery
claims and chat ordering, transport fallback and failures, private admin diagnostics, code whitespace,
HTML safety, list layout and UTF-16 message boundaries. Live timing is still required to assess the
five-second goal.
