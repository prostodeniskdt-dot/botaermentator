# Quick/Deep completion and recovery

## Architecture and minimal change

Previously Deep had a 2200-token limit, and both modes only appended a warning on `length` or
`incomplete`. The approved expert prompt already defined the modes. Answer persistence, credit
reservation/commit, durable webhook jobs, user-owner fences and per-part Telegram delivery already
existed. They are reused. No new queue, agent, model selection or deployment is introduced.

Migration `008_expert_checkpoint` adds one JSONB column, `user_questions.generation_state`, with an
empty default for existing questions. It holds the frozen expert request, original agent ID, budgets,
delivery context, saved fragments and an external-call marker. This is private working data in the
existing database, not technical logging. It is cleared on finalization or terminal recovery. Existing
profiles, dialogue, answers, credits and queue rows are preserved by the migration.

## Configuration

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `QUICK_MAX_OUTPUT_TOKENS` | 600 | Per-generation Quick budget |
| `DEEP_MAX_OUTPUT_TOKENS` | 5000 | Per-generation Deep budget, including continuations |
| `DEEP_MAX_CONTINUATIONS` | 2 | Additional Deep generations; 0 disables, maximum 4 |
| `DEEP_MODE_CREDITS` | 5 | One user Deep service, independent of technical continuations |
| `TIMEWEB_READ_TIMEOUT_SECONDS` | 120 | Unchanged HTTP read timeout in seconds |
| `TIMEWEB_DEADLINE_SECONDS` | 150 | Unchanged deadline per agent call in seconds |
| `PROMPT_VERSION` | 7 | Avoid reuse of answers cached with old runtime instructions |

Quick aims for 60–100 Russian words when sufficient, keeps necessary parameters and safety checks,
and is never mechanically cut by word count. Deep covers formulation, parameters, mechanism,
stages, control, risks, storage and diagnosis when relevant; it is not told to pad answers. The three
approved prompt files remain unchanged. Only application mode/continuation instructions change.

An existing explicit environment value, such as `DEEP_MAX_OUTPUT_TOKENS=2200` or `PROMPT_VERSION=6`,
overrides the new default. Update those values when deploying this change. Reliability across
restarts uses the normal configuration with PostgreSQL queues and `STORE_BOT_RESPONSES=true`.
The legacy no-storage direct-send mode does not have durable per-part delivery guarantees.

## Completion rules

Only explicit `length`, `max_tokens`, `max_completion_tokens` or `max_output_tokens` confirms a token
limit and triggers continuation. A generic `incomplete`, content filtering, a provider error, missing
completion reason or a timeout does not trigger it. `stop`/`completed` confirms completion. Unknown
completion is labeled incomplete rather than silently treated as a successful finished answer.

Each continuation goes to the frozen expert agent with the original task, selected context, safety
instructions and the saved draft including its ending. It asks for the remaining text without
repeating completed paragraphs or changing facts, units or process parameters. The join removes
exact repeated leading paragraphs and exact suffix/prefix overlap; it does not summarize or rewrite
the answer. A cut sentence may be repeated exactly to allow a safe overlap join. Semantic duplicates
with different wording are discouraged by the instruction rather than removed heuristically.

The loop ends on confirmed completion, the configured cap, no new text, a non-token failure, a
changed agent, or the existing daily provider budget. Budget is checked again before each technical
continuation. A partial answer has `UserQuestion.status=truncated`, a visible explanation and a notice
that it is not a finished technological instruction. Partial answers are not stored in the shared
answer cache or offered as a completed Quick answer for paid expansion.

## Credits, failures and restart

One ordinary Deep request reserves/charges 5 credits with the default settings. Technical calls do
not reserve or charge more user credits. Every call records its own `AIUsageEvent` with actual cost
when available and estimated cost otherwise. A paid expansion keeps its separately configured
existing price; its technical continuations also cost the user nothing extra.

The external-call marker commits before the request. A successful fragment and its usage event
commit together before another generation. A retry reuses saved fragments and frozen limits. Final
answer, credit commit and delivery job remain one transaction. A finalization rollback can therefore
reuse the completed draft without another Timeweb call or charge. Pending queued generation is
excluded from orphan/notice cleanup until the queue has finished or exhausted recovery attempts.
The same recovery applies to paid expansions. Existing user-owner fences and queue leases apply.

If the process disappears while a request is marked in flight, the provider may already have billed
or completed it. There is no verified provider idempotency or result-retrieval mechanism here, so the
call is not blindly repeated. The bot records `outcome_unknown`, delivers the saved draft as incomplete
if available, or reports failure and releases the reservation if nothing was saved. A returned timeout
follows the same no-replay policy. Recovery occurs when the existing queue/user leases allow a new
worker to claim the job; these lease timings are unchanged.

The existing Telegram delivery records each part. A retry skips confirmed sent parts. Unknown send
outcomes are stopped for review instead of replayed; a non-idempotent external send cannot provide
both guaranteed arrival and unconditional retry without a duplicate risk. No partial draft is sent
before finalization. Telegram splitting retains HTML structure, word boundaries and UTF-16 limits.

## Provider limits and timeouts

[Timeweb's OpenAI-compatible documentation](https://timeweb.cloud/docs/ai-agents/api-usage/openai-compatible-api)
specifies `max_tokens` for Chat Completions and `max_completion_tokens` for GPT-5 models. This bot's
GPT-5-compatible transport uses `max_completion_tokens`; Responses variants use `max_output_tokens`.
Tests verify the full configured budget is sent unchanged through every existing transport.

[The native API documentation](https://timeweb.cloud/docs/ai-agents/api-usage/native-api) says native
calls apply Playground settings. Its documented parameters are `message` and `parent_message_id`;
it does not establish that the extra `max_tokens` field overrides the panel. The code retains that
field for compatibility and does not claim it is honored. Prefer the configured OpenAI transport for
per-request limits. [Available Playground parameters depend on the model](https://timeweb.cloud/docs/ai-agents/manage-agents/playground).
The public pages do not establish a universal output ceiling or verify that this particular agent
accepts 5000 tokens. No production credentials were available to test that fact or read panel limits.

Read/deadline values remain 120/150 seconds: there is no live measurement justifying an increase.
The deadline applies per generation, so a Deep answer can require up to three bounded agent calls
with the defaults, in addition to classification and delivery. Short mock-test durations say nothing
about live latency or generation speed. On a test agent, compare requested limit, returned output
tokens, finish reason and actual call duration for complete 5000-token-budget responses before
changing timeouts. Provider compatibility/quota errors do not silently lower the requested budget.

## Diagnostics and validation

`expert_generation`, `expert_fragment_finished` and `expert_answer_finished` report mode, requested
limit, returned output tokens when supplied, finish reason, continuation index/count, duration and
completion status. Total duration includes recovery downtime; per-call durations remain separate.
`expert_answer_persisted` reports completion, continuation count and planned Telegram part count.
Existing per-part delivery records track actual progress. The logs contain identifiers and metadata,
not prompts, full questions, drafts or tokens/secrets. Missing provider token counts remain unknown.

Validation includes unit/transport contracts plus isolated PostgreSQL tests for a 5-credit Deep
request, repeated updates, refunds, fragment checkpoints, restart during an unknown call, rollback
before finalization, expansion recovery, safe long-message splitting and delivery retries that skip
confirmed parts. Prompt tests retain food-safety instructions and exact numerical parameters in
continuation requests; synthetic answers test preservation without pretending to validate live model
quality. Run `ruff check .`, `ruff format --check .`, `python scripts/sync_prompt_docs.py --check` and
`pytest` with an isolated `TEST_DATABASE_URL` for database coverage.

No deployment has been performed. Before an approved deployment, stop old consumers, apply migration
008 and update explicit environment overrides; avoid old/new workers using the new schema together.
Exercise Quick, full Deep, forced truncation and failure on a test agent before production rollout.
