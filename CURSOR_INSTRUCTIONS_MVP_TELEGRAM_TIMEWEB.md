# CURSOR_INSTRUCTIONS.md

## Закрытый Telegram-бот — эксперт по ферментации и hospitality

**Цель:** создать стартовую production-ready MVP-версию обычного Telegram-бота, добавленного в одну закрытую платную группу и развёрнутого на Timeweb Cloud App Platform.

**Стек:** Python 3.12, FastAPI, aiogram 3.x, PostgreSQL, SQLAlchemy 2, asyncpg, Alembic, httpx, Pydantic 2, pydantic-settings, tenacity, structlog, pytest, Ruff, Docker.

---

# 1. Роль Cursor

Работай как senior Python backend-разработчик и архитектор.

Перед кодом:

1. Изучи существующий репозиторий.
2. Создай `docs/IMPLEMENTATION_PLAN.md`.
3. Создай `docs/ARCHITECTURE.md`.
4. Покажи будущую структуру файлов.
5. Зафиксируй риски и принятые решения.
6. Реализуй проект по этапам.
7. После каждого этапа запускай `ruff check .`, `ruff format --check .` и `pytest`.
8. Не переходи дальше с падающими тестами.
9. Не храни реальные токены, пароли, chat ID и agent ID в Git.
10. Не задавай вопросы о значениях, которые можно вынести в `.env`.

Не создавай весь проект одним неконтролируемым изменением.

---

# 2. Назначение продукта

Бот является цифровым консультантом конкретного эксперта по ферментации.

Он отвечает на вопросы о:

- ферментации;
- микробиологии и биохимии продуктов;
- баре и напитках;
- коктейлях;
- ингредиентах;
- пищевой безопасности;
- ресторанной и hospitality-индустрии;
- сервисе;
- обучении и управлении баром.

Главная ценность — ответы на основании авторской базы:

- курсов;
- лекций;
- рецептур;
- технологических карт;
- проверенных практических материалов.

Дополнительно основной агент может использовать интернет-поиск.

---

# 3. Telegram-сценарий

Guest Mode не используется.

Бот добавляется обычным участником в одну закрытую платную группу.

Пользователь вызывает его:

```text
@bot_username Как температура влияет на молочнокислую ферментацию?
```

Также поддерживаются:

```text
/ask Как температура влияет на ферментацию?
/ask@bot_username Как температура влияет на ферментацию?
```

И реплай на предыдущий ответ бота:

```text
А если температура будет 30 °C?
```

Бот отвечает реплаем в тот же чат и ту же forum topic, если используются темы.

---

# 4. Настройка Privacy Mode

Для гарантированной обработки обычного текстового `@bot_username` в BotFather отключить **Group Privacy Mode**.

Из-за этого backend будет получать все сообщения группы. Поэтому обязательны правила:

1. Сообщения без обращения к боту немедленно игнорируются.
2. Они не сохраняются.
3. Их текст не логируется.
4. Они не передаются ни одному агенту.
5. Для них не создаются пользователи и сессии.
6. Они не вызывают PostgreSQL-запросы, кроме минимальной обработки Telegram update.
7. Они не вызывают AI и не расходуют токены.

Если требуется сохранить Privacy Mode, допустимый альтернативный UX — только `/ask@bot` и реплаи. MVP по умолчанию строится под обычное `@упоминание`, поэтому Privacy Mode выключен.

Бот не должен быть администратором без отдельной необходимости.

---

# 5. Ограничение одним чатом

Разрешённый чат задаётся:

```dotenv
ALLOWED_CHAT_ID=-1000000000000
```

Проверять `chat_id` до:

- AI;
- создания сессии;
- сохранения вопроса;
- обращения к базе знаний;
- интернет-поиска.

Если бот добавлен в другой групповой чат:

- обработать update `my_chat_member`;
- вызвать `leaveChat`;
- не вызывать AI;
- не сохранять переписку.

Личные сообщения обычных пользователей не должны вызывать AI. Статический ответ:

```text
Ассистент работает внутри закрытого профессионального сообщества.
```

Исключение — `ADMIN_USER_IDS`.

---

# 6. Платный доступ

Платежи не входят в MVP.

Tribute или другой внешний сервис управляет членством в закрытой группе.

Правило MVP:

> Наличие пользователя в разрешённой группе является пропуском к боту.

Не реализовывать API Tribute, Telegram Stars, Mini App и собственную подписку.

---

# 7. Архитектура

```text
Telegram webhook
        |
        v
FastAPI + aiogram
        |
        v
Request Gate
  - webhook secret
  - dedup update_id
  - allowed chat
  - mention / command / reply
  - blocked user/session
  - rate limits
  - duplicate
  - budget
        |
        v
Agent 1 — Industry Filter
        |
        | off-topic -> static rejection
        v
Agent 2 — Context Relation
  only for reply to bot
        |
        v
Agent 3 — Main Expert
  - Timeweb knowledge base
  - large model
  - web search
        |
        v
Safe Telegram formatter
        |
        v
Reply to user
```

Telegram не подключать напрямую к встроенной интеграции AI-агента Timeweb. Telegram работает только через наш backend.

---

# 8. Три агента Timeweb

Создать три отдельных AI-агента в панели Timeweb Cloud.

## Agent 1 — Industry Filter

Дешёвая быстрая модель без базы знаний и веб-поиска.

Определяет:

- относится ли вопрос к hospitality;
- категорию;
- prompt injection;
- попытку выгрузки авторских материалов;
- мусорный запрос;
- нормализованный вопрос.

Строгий JSON:

```json
{
  "allowed": true,
  "category": "fermentation",
  "confidence": 0.97,
  "reason_code": "hospitality_related",
  "normalized_question": "Как температура влияет на молочнокислую ферментацию?",
  "is_prompt_injection": false,
  "is_knowledge_exfiltration": false,
  "is_junk": false
}
```

Допустимые темы:

- ферментация;
- биохимия и микробиология продуктов;
- напитки;
- бар;
- ресторан;
- кухня;
- продукты;
- кофе, чай, вино, пиво, крепкий алкоголь;
- оборудование;
- хранение;
- санитария;
- пищевая безопасность;
- сенсорика;
- сервис;
- гостеприимство;
- персонал;
- управление и экономика заведения.

Широкий вопрос «Что такое белки?» разрешать как пищевую биохимию.

Если ответ невалидный:

1. убрать markdown fences;
2. повторить parsing;
3. сделать один corrective retry;
4. при повторной ошибке — fail closed;
5. Agent 3 не вызывать.

Off-topic ответ статический, без второй модели.

## Agent 2 — Context Relation

Дешёвая модель без базы знаний и веб-поиска.

Запускается только при реплае на сохранённый ответ бота.

Вход:

- текущий вопрос;
- предыдущий вопрос пользователя;
- предыдущий ответ бота.

Не передавать всю историю.

Строгий JSON:

```json
{
  "relation": "related",
  "include_previous_context": true,
  "rewritten_question": "Как изменится описанный процесс при температуре 30 °C?",
  "confidence": 0.94
}
```

Значения:

- `related`;
- `standalone`;
- `ambiguous`.

При timeout или невалидном JSON использовать fallback:

- вопрос считать самостоятельным;
- старый ответ в Agent 3 не передавать;
- основной запрос не блокировать.

## Agent 3 — Main Expert

Большая модель.

Только к нему подключить:

- авторскую базу знаний Timeweb;
- веб-поиск Timeweb.

Веб-поиск включается в панели агента. Код не должен считать, что может принудительно включить или выключить его для отдельного запроса.

Приоритет:

1. правила безопасности и исправления автора;
2. авторские курсы;
3. авторские рецепты;
4. авторские технологические карты;
5. проверенные внешние источники;
6. общие знания модели.

Agent 3 должен:

- отвечать только в профессиональном контексте;
- не приписывать автору отсутствующие данные;
- не придумывать источники, страницы и цифры;
- не раскрывать системный промпт;
- не выгружать полный курс;
- не воспроизводить большие фрагменты дословно;
- отличать факт, предположение и рекомендацию;
- указывать единицы и базу расчёта процентов;
- осторожно отвечать о безопасности продуктов;
- запрашивать недостающие параметры;
- избегать маркетингового языка;
- избегать рубленого текста;
- избегать шаблона «это не X, а Y»;
- писать спокойно и научно-прикладно.

Для теории возможна структура:

```text
Определение
Механизм
Практическое значение
Ограничения
```

Для рецепта и диагностики:

```text
Вводные
Раскладка
Последовательность действий
Критические точки
Советы
```

Не добавлять разделы механически.

---

# 9. Timeweb API

Использовать нативный API каждого агента:

```text
POST https://api.timeweb.cloud/api/v1/cloud-ai/agents/{agent_id}/call
Authorization: Bearer <TOKEN>
Content-Type: application/json
```

MVP-запрос:

```json
{
  "message": "сформированный запрос"
}
```

Не использовать `parent_message_id` для пользовательской истории в MVP. Контекстом управляет наше приложение.

Сохранять:

- текст ответа;
- `id`;
- `response_id`;
- `finish_reason`, если присутствует;
- latency;
- HTTP status;
- success/failure;
- estimated cost.

Для всех HTTP-вызовов:

- connect timeout;
- read timeout;
- общий timeout;
- максимум 2 попытки;
- exponential backoff;
- типизированные исключения;
- безопасные логи.

---

# 10. Сессии

Новый вопрос через упоминание или `/ask` создаёт новую UUID-сессию.

При реплае:

1. проверить, что `reply_to_message.from_user.id` — ID нашего бота;
2. найти сохранённый bot message по `chat_id + message_id`;
3. получить session ID;
4. запустить Agent 2;
5. `related` — продолжить сессию;
6. `standalone` — создать новую;
7. `ambiguous` — передать только минимальный контекст.

Хранить `message_thread_id`.

Не смешивать:

- разных пользователей;
- разные topics;
- параллельные ветки.

Agent 3 получает максимум:

- текущий вопрос;
- предыдущий вопрос;
- один предыдущий ответ — только после подтверждения Agent 2.

---

# 11. Request Gate

До Agent 1 выполнить:

1. проверить webhook secret;
2. дедуплицировать `update_id`;
3. проверить `from_user`;
4. отклонить сообщения от ботов;
5. проверить `chat_id`;
6. определить упоминание, `/ask` или reply;
7. проверить блокировку пользователя;
8. проверить блокировку сессии;
9. проверить лимит в минуту;
10. проверить лимит в час;
11. проверить лимит в сутки;
12. проверить concurrent request;
13. удалить обращение к боту;
14. проверить пустой вопрос;
15. проверить длину;
16. проверить точный дубликат;
17. проверить kill switch;
18. проверить дневной бюджет.

Только затем Agent 1.

Упоминание определять через Telegram `MessageEntity`, а не простым `if username in text`.

Поддержать:

```text
@bot вопрос
вопрос @bot
/ask вопрос
/ask@bot вопрос
```

---

# 12. Лимиты

```dotenv
USER_REQUESTS_PER_MINUTE=6
USER_REQUESTS_PER_HOUR=60
USER_REQUESTS_PER_DAY=1000
MAX_CONCURRENT_REQUESTS_PER_USER=1
MAX_QUERY_LENGTH=4000
DUPLICATE_WINDOW_SECONDS=60

SESSION_JUNK_SCORE_PAUSE=10
SESSION_JUNK_SCORE_BLOCK=25
USER_JUNK_SCORE_BLOCK=50

DAILY_AI_BUDGET_RUB=5000
AI_PROCESSING_ENABLED=true
```

1000 в сутки — абсолютный потолок. Минутный и часовой лимиты должны останавливать спам раньше.

Junk score:

- пустой запрос `+1`;
- мусор `+1`;
- off-topic `+1`;
- duplicate `+2`;
- prompt injection `+3`;
- попытка выгрузки базы `+3`;
- автоматический спам `+5`.

Пороги должны настраиваться без изменения кода.

---

# 13. PostgreSQL

Telegram ID хранить как `BIGINT`.

Создать таблицы:

## `telegram_users`

- UUID id;
- telegram_user_id unique;
- username;
- first_name;
- last_name;
- is_blocked;
- blocked_at;
- block_reason;
- created_at;
- updated_at;
- last_seen_at.

## `chat_sessions`

- UUID id;
- chat_id;
- telegram_user_id;
- message_thread_id;
- root_user_message_id;
- last_bot_message_id;
- status;
- junk_score;
- blocked_at;
- block_reason;
- created_at;
- updated_at.

## `user_questions`

- UUID id;
- session_id;
- telegram_update_id;
- telegram_message_id;
- reply_to_message_id;
- raw_question;
- normalized_question;
- category;
- filter_allowed;
- context_relation;
- status;
- created_at.

Сохранять только явные обращения к боту.

## `bot_responses`

- UUID id;
- session_id;
- question_id;
- telegram_message_id;
- response_text;
- timeweb_response_id;
- status;
- created_at.

## `processed_updates`

- telegram_update_id unique;
- processed_at;
- result.

## `ai_usage_events`

- UUID id;
- session_id;
- question_id;
- agent_type;
- agent_id;
- response_id;
- started_at;
- finished_at;
- latency_ms;
- http_status;
- success;
- input_tokens nullable;
- output_tokens nullable;
- actual_cost_rub nullable;
- estimated_cost_rub;
- error_code nullable.

## `rate_limit_counters`

- scope_type;
- scope_id;
- window_type;
- window_start;
- request_count;
- updated_at.

Unique:

```text
scope_type + scope_id + window_type + window_start
```

## `block_events`

- UUID id;
- target_type;
- target_id;
- action;
- reason;
- admin_telegram_user_id;
- created_at.

Все изменения схемы — только через Alembic.

---

# 14. Учёт стоимости

Если API возвращает usage — сохранить фактические токены и стоимость.

Если нет — использовать оценку:

```dotenv
AGENT_1_ESTIMATED_COST_RUB=0.10
AGENT_2_ESTIMATED_COST_RUB=0.10
AGENT_3_ESTIMATED_COST_RUB=1.30
WEB_SEARCH_ESTIMATED_COST_RUB=0.49
```

Не записывать стоимость веб-поиска как фактическую, если API не сообщил, что поиск запускался.

В статистике разделять actual и estimated.

---

# 15. Структура проекта

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
Dockerfile
pyproject.toml
alembic.ini
.env.example
README.md
```

Бизнес-логика не должна находиться в Telegram handlers.

---

# 16. Переменные окружения

Создать `.env.example`:

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
APP_HOST=0.0.0.0
APP_PORT=8080

TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_URL=
TELEGRAM_WEBHOOK_PATH=/telegram/webhook
TELEGRAM_WEBHOOK_SECRET=
ALLOWED_CHAT_ID=
ADMIN_USER_IDS=

DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database

TIMEWEB_API_BASE_URL=https://api.timeweb.cloud
TIMEWEB_AGENT_1_ID=
TIMEWEB_AGENT_1_TOKEN=
TIMEWEB_AGENT_2_ID=
TIMEWEB_AGENT_2_TOKEN=
TIMEWEB_AGENT_3_ID=
TIMEWEB_AGENT_3_TOKEN=

TIMEWEB_CONNECT_TIMEOUT_SECONDS=5
TIMEWEB_READ_TIMEOUT_SECONDS=120
TIMEWEB_MAX_ATTEMPTS=2

USER_REQUESTS_PER_MINUTE=6
USER_REQUESTS_PER_HOUR=60
USER_REQUESTS_PER_DAY=1000
MAX_CONCURRENT_REQUESTS_PER_USER=1
MAX_QUERY_LENGTH=4000
DUPLICATE_WINDOW_SECONDS=60

SESSION_JUNK_SCORE_PAUSE=10
SESSION_JUNK_SCORE_BLOCK=25
USER_JUNK_SCORE_BLOCK=50

DAILY_AI_BUDGET_RUB=5000
AI_PROCESSING_ENABLED=true

AGENT_1_ESTIMATED_COST_RUB=0.10
AGENT_2_ESTIMATED_COST_RUB=0.10
AGENT_3_ESTIMATED_COST_RUB=1.30
WEB_SEARCH_ESTIMATED_COST_RUB=0.49

STORE_USER_QUESTIONS=true
STORE_BOT_RESPONSES=true
```

В production отсутствие критических переменных должно останавливать запуск понятной ошибкой.

---

# 17. Telegram webhook

Endpoints:

```text
GET  /health
GET  /ready
POST /telegram/webhook
```

`/health`:

- не зависит от PostgreSQL и AI;
- возвращает 200, если процесс жив.

`/ready`:

- проверяет PostgreSQL;
- проверяет конфигурацию;
- не выполняет платный AI-запрос.

Webhook:

- проверить `X-Telegram-Bot-Api-Secret-Token`;
- при ошибке вернуть 403;
- использовать `allowed_updates=["message", "my_chat_member"]`;
- обеспечить дедупликацию `update_id`.

Development — long polling и отдельный test bot.

Production — только webhook.

---

# 18. Форматирование Telegram

Использовать обычный HTML.

Не доверять HTML модели. Экранировать динамический текст.

Разбивать длинный ответ на части около 3800–4000 символов.

Первая часть — reply на вопрос.

Последующие — в тот же чат и `message_thread_id`.

Не разрезать HTML entity и code block.

`typing` отправлять только после прохождения бесплатного Request Gate.

---

# 19. Ошибки

Agent 1 error:

- retry один раз;
- fail closed;
- Agent 3 не запускать;
- статическая ошибка.

Agent 2 error:

- retry один раз;
- fallback standalone;
- Agent 3 разрешён.

Agent 3 error:

- retry один раз;
- статический ответ;
- successful request не засчитывать.

PostgreSQL error:

- `/health` остаётся 200;
- `/ready` становится non-2xx;
- AI не запускать без дедупликации и лимитов.

Статические сообщения не генерировать AI.

---

# 20. Админ-команды

Доступны только в private chat и только `ADMIN_USER_IDS`:

```text
/admin_status
/admin_cost_today
/admin_block_user <telegram_user_id> <reason>
/admin_unblock_user <telegram_user_id>
/admin_block_session <session_uuid> <reason>
/admin_unblock_session <session_uuid>
/admin_session <session_uuid>
/admin_kill_switch_on
/admin_kill_switch_off
```

Также создать CLI scripts для блокировки и разблокировки.

Не показывать токены и секреты.

---

# 21. Логи и приватность

В production не логировать:

- токены;
- пароли;
- DATABASE_URL;
- системные промпты;
- обычную переписку группы;
- полный вопрос;
- полный ответ AI.

Логировать:

- update_id;
- chat_id;
- user_id;
- session_id;
- agent_type;
- category;
- reason_code;
- latency;
- status;
- estimated cost;
- error code;
- длину текста.

В PostgreSQL сохранять текст только явных обращений к боту.

---

# 22. База знаний

База создаётся вручную в панели Timeweb.

Требования:

- PDF должен иметь текстовый слой;
- проверить выделение и копирование текста;
- OCR выполнить до загрузки;
- изображения внутри PDF не считать источником текста;
- критические материалы лучше перевести в Markdown;
- крупные документы разбить по темам;
- версии документов маркировать;
- конфликтующие версии не загружать без пояснения.

Пример:

```yaml
---
document_id: FERMENT-COURSE-01-03
title: Молочнокислая ферментация
author: AUTHOR_NAME
version: 1.0
updated_at: 2026-07-01
source_priority: author_primary
---
```

База подключается только к Agent 3.

---

# 23. Docker и Timeweb

Dockerfile:

- Python 3.12 slim;
- non-root user;
- порт 8080;
- `EXPOSE 8080`;
- uvicorn;
- healthcheck;
- без секретов.

Ориентир:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .
RUN chown -R app:app /app
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Создать `docs/TIMEWEB_SETUP.md`:

1. создать PostgreSQL;
2. создать три AI-агента;
3. выбрать дешёвые модели для Agent 1 и Agent 2;
4. выбрать большую модель для Agent 3;
5. подключить базу знаний к Agent 3;
6. включить веб-поиск у Agent 3;
7. создать отдельные API keys;
8. подключить Git repository к App Platform;
9. выбрать Dockerfile deploy;
10. установить env;
11. настроить `/health`;
12. применить `alembic upgrade head`;
13. получить HTTPS URL;
14. установить Telegram webhook;
15. выполнить smoke tests.

---

# 24. Webhook scripts

`scripts/set_webhook.py`:

- загрузить settings;
- вызвать `setWebhook`;
- передать URL;
- передать secret token;
- передать `["message", "my_chat_member"]`;
- поддержать `--drop-pending-updates`;
- не печатать token.

`scripts/delete_webhook.py`:

- удалить webhook перед local polling.

---

# 25. Обязательные тесты

## Telegram gate

1. Обычное сообщение без упоминания.
2. Упоминание в разрешённой группе.
3. `/ask`.
4. Реплай на бота.
5. Реплай на пользователя.
6. Чужая группа.
7. Private chat.
8. Admin private command.
9. Сообщение от бота.
10. Пустой вопрос.
11. Длинный вопрос.
12. Duplicate.
13. Duplicate update ID.

Для сообщения без упоминания проверить:

- нет session;
- нет сохранённого question;
- Agent 1/2/3 не вызваны;
- нет ответа.

Для чужого чата проверить:

- Agent 1/2/3 не вызваны;
- usage event не создан;
- при добавлении вызван `leaveChat`.

## Agent 1

- allowed;
- off-topic;
- injection;
- exfiltration;
- junk;
- markdown fences;
- invalid JSON;
- retry;
- fail closed.

## Agent 2

- related;
- standalone;
- ambiguous;
- invalid JSON;
- timeout;
- fallback standalone.

## Agent 3

- success;
- timeout;
- 4xx;
- 5xx;
- empty;
- long response;
- unsafe HTML;
- retry.

## Sessions

- new mention = new session;
- related reply = same session;
- standalone reply = new session;
- users do not mix;
- topics do not mix;
- blocked session does not call AI.

## Limits

- minute;
- hour;
- 1000/day;
- concurrent;
- duplicate;
- junk score;
- daily budget;
- kill switch.

## Webhook

- valid secret;
- invalid secret;
- missing secret;
- idempotent update.

Все внешние APIs мокировать через `respx` или `httpx.MockTransport`.

---

# 26. Этапы реализации

## Этап 1

- implementation plan;
- architecture;
- project skeleton;
- config;
- FastAPI;
- `/health`;
- `/ready`;
- Docker;
- base tests.

Без Telegram, DB и AI.

## Этап 2

- PostgreSQL;
- models;
- repositories;
- Alembic;
- integration tests.

## Этап 3

- aiogram;
- polling;
- webhook;
- secret;
- allowed chat;
- mention parser;
- `/ask`;
- reply;
- private response;
- auto-leave;
- fake question service.

## Этап 4

- Timeweb client;
- typed schemas;
- timeout;
- retry;
- mocks.

## Этап 5

- Agent 1;
- JSON validation;
- fail closed.

## Этап 6

- Agent 2;
- relation;
- fallback.

## Этап 7

- Agent 3;
- formatting;
- splitting.

## Этап 8

- full orchestrator;
- sessions;
- usage;
- responses.

## Этап 9

- limits;
- duplicates;
- blocks;
- junk score;
- budget;
- admin;
- kill switch.

## Этап 10

- deployment docs;
- webhook scripts;
- operations;
- full tests;
- smoke checklist.

После каждого этапа:

```bash
ruff check .
ruff format --check .
pytest
```

---

# 27. Definition of Done

MVP готов, когда:

- Docker image собирается;
- приложение слушает 8080;
- `/health` = 200;
- `/ready` проверяет DB;
- webhook защищён secret;
- обычные сообщения группы игнорируются;
- `@bot вопрос` работает;
- `/ask вопрос` работает;
- reply работает;
- чужой чат не вызывает AI;
- бот выходит из чужой группы;
- private chat не вызывает AI;
- Agent 1 фильтрует;
- Agent 2 проверяет связь;
- Agent 3 работает с Timeweb knowledge base;
- сессии сохраняются;
- update idempotent;
- лимиты работают;
- блокировки работают;
- budget и kill switch работают;
- cost сохраняется;
- длинные ответы делятся;
- HTML безопасен;
- секретов нет в Git;
- тесты проходят;
- README содержит local run;
- docs содержат Timeweb deploy.

---

# 28. Первый ответ Cursor

Не начинай сразу писать весь проект.

Сначала выдай:

1. понимание задачи;
2. анализ репозитория;
3. архитектуру;
4. список файлов;
5. план этапов;
6. риски;
7. спорные решения.

Затем создай:

- `docs/IMPLEMENTATION_PLAN.md`;
- `docs/ARCHITECTURE.md`.

После этого начинай Этап 1.
