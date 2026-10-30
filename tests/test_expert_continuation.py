import asyncio
import copy
import uuid
from unittest.mock import AsyncMock

import pytest

from app.agents.expert_continuation import join_fragment
from app.agents.main_expert import MainExpertAgent
from app.agents.timeweb_client import TimewebCallResult, TimewebTimeoutError, _finish_reason
from app.config import Settings
from app.services.delivery_service import delivery_plan


def fragment(text, reason="stop", tokens=100):
    return TimewebCallResult(
        message=text,
        response_id=str(uuid.uuid4()),
        finish_reason=reason,
        http_status=200,
        latency_ms=12,
        raw={},
        output_tokens=tokens,
        actual_cost_rub=0.75,
    )


class CheckpointRepo:
    def __init__(self):
        self.state = {}
        self.events = []
        self.interrupt_after_fragment = False

    async def get_generation_state(self, _):
        return copy.deepcopy(self.state)

    async def save_generation_state(self, _, state):
        self.state = copy.deepcopy(state)

    async def before_external(self):
        progress = self.state.get("progress", {})
        if (
            self.interrupt_after_fragment
            and progress.get("fragments")
            and not progress["in_flight"]
        ):
            self.interrupt_after_fragment = False
            raise asyncio.CancelledError()

    async def ensure_processing_lease(self):
        pass

    async def record_usage_event(self, event):
        self.events.append(event)

    async def get_daily_usage_cost(self, _):
        return sum(e.actual_cost_rub or e.estimated_cost_rub for e in self.events)


async def answer(settings, repo, client, mode="deep"):
    return await MainExpertAgent(settings, client).answer(
        repo,
        question="Разберите технологию тестовой партии: 2% соли, 20 °C.",
        session_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        response_mode=mode,
        user_memory="Измерять pH; сомнительный продукт не пробовать.",
    )


def test_limits_are_distinct_and_configurable(monkeypatch):
    settings = Settings(_env_file=None)
    assert (settings.quick_max_output_tokens, settings.deep_max_output_tokens) == (600, 5000)
    assert settings.deep_max_continuations == 2
    monkeypatch.setenv("QUICK_MAX_OUTPUT_TOKENS", "700")
    monkeypatch.setenv("DEEP_MAX_OUTPUT_TOKENS", "6000")
    monkeypatch.setenv("DEEP_MAX_CONTINUATIONS", "1")
    settings = Settings(_env_file=None)
    assert (settings.quick_max_output_tokens, settings.deep_max_output_tokens) == (700, 6000)
    assert settings.deep_max_continuations == 1


@pytest.mark.parametrize("reason", ["stop", "length"])
async def test_quick_contract_is_concise_without_mechanical_word_cut_or_extra_calls(
    settings, reason
):
    text = "Нужны измерения температуры и pH. " * 16
    client = AsyncMock()
    client.call_agent.return_value = fragment(text, reason)
    result = await answer(settings, CheckpointRepo(), client, "quick")
    assert client.call_agent.await_count == 1
    request = client.call_agent.call_args.kwargs
    assert request["max_output_tokens"] == 600
    assert "60–100 Russian words when sufficient" in request["message"]
    assert "safety" in request["message"]
    assert result.text == text
    assert result.is_complete == (reason == "stop")


@pytest.mark.parametrize(
    "reason", ["length", "max_tokens", "max_output_tokens", "max_completion_tokens"]
)
async def test_confirmed_limit_continues_same_agent_and_keeps_parameters_and_safety(
    settings, reason
):
    safety = "Не пробуйте сомнительный продукт. Измеряйте pH."
    first = "Партия: 2% соли, 20 °C.\n\n" + safety
    client = AsyncMock()
    client.call_agent.side_effect = [
        fragment(first, reason, 5000),
        fragment(safety + "\n\nХранение: оцените условия контроля."),
    ]
    repo = CheckpointRepo()
    result = await answer(settings, repo, client)
    assert result.is_complete
    assert result.continuation_count == 1
    assert result.output_tokens == 5100
    assert result.text.count(safety) == 1
    assert "2% соли, 20 °C" in result.text
    first_request, second = [c.kwargs for c in client.call_agent.call_args_list]
    assert first_request["agent_id"] == second["agent_id"] == settings.timeweb_agent_3_id
    assert first_request["max_output_tokens"] == second["max_output_tokens"] == 5000
    assert "Preserve all established facts" in second["message"]
    assert safety in second["message"]
    assert "2% соли, 20 °C" in second["message"]
    assert len(repo.events) == 2
    assert sum(e.actual_cost_rub for e in repo.events) == 1.5


@pytest.mark.parametrize("reason", ["incomplete", "content_filter", "error", "failed"])
async def test_non_token_failure_is_never_a_continuation_trigger(settings, reason):
    client = AsyncMock()
    client.call_agent.return_value = fragment("Часть ответа.", reason)
    result = await answer(settings, CheckpointRepo(), client)
    assert not result.is_complete
    assert client.call_agent.await_count == 1


async def test_continuation_cap_and_repeated_paragraph_protection(settings):
    client = AsyncMock()
    client.call_agent.side_effect = [
        fragment("Первый этап.", "length"),
        fragment("Первый этап.\n\nВторой этап.", "length"),
        fragment("Второй этап.\n\nТретий этап.", "length"),
    ]
    repo = CheckpointRepo()
    result = await answer(settings, repo, client)
    assert not result.is_complete
    assert result.incomplete_reason == "continuation_limit"
    assert result.continuation_count == 2
    assert client.call_agent.await_count == 3
    assert result.text == "Первый этап.\n\nВторой этап.\n\nТретий этап."
    # A repeated invocation of the saved terminal operation never spends again.
    again = await answer(settings, repo, client)
    assert again.text == result.text
    assert client.call_agent.await_count == 3
    assert len(repo.events) == 3


async def test_no_progress_stops_even_before_configured_cap(settings):
    client = AsyncMock()
    client.call_agent.return_value = fragment("Не пробуйте сомнительный продукт.", "length")
    result = await answer(settings, CheckpointRepo(), client)
    assert not result.is_complete
    assert result.incomplete_reason == "no_progress"
    assert client.call_agent.await_count == 2


async def test_absent_finish_reason_is_not_reported_as_confirmed_completion(settings):
    client = AsyncMock()
    client.call_agent.return_value = fragment("Сохранённый текст.", None)
    result = await answer(settings, CheckpointRepo(), client)
    assert not result.is_complete
    assert result.incomplete_reason == "completion_unconfirmed"
    assert client.call_agent.await_count == 1


async def test_every_transport_requests_full_deep_budget_without_changing_timeouts(settings):
    from app.agents.timeweb_client import TimewebClient

    client = TimewebClient(settings)
    try:
        for mode, _, payload in client._request_variants("test", "question", 5000):
            key = (
                "max_tokens"
                if mode == "native"
                else (
                    "max_completion_tokens"
                    if mode == "chat_completions_no_reasoning"
                    else "max_output_tokens"
                )
            )
            assert payload[key] == 5000
        assert settings.timeweb_read_timeout_seconds == 120
        assert settings.timeweb_deadline_seconds == 150
    finally:
        await client.aclose()


async def test_diagnostics_exclude_question_text_and_token(settings):
    from structlog.testing import capture_logs

    client = AsyncMock()
    client.call_agent.return_value = fragment("Контроль процесса.")
    with capture_logs() as entries:
        await answer(settings, CheckpointRepo(), client)
    assert "Разберите технологию тестовой партии" not in str(entries)
    assert settings.timeweb_agent_3_token not in str(entries)
    summary = next(e for e in entries if e["event"] == "expert_answer_finished")
    assert summary["requested_output_tokens"] == 5000
    assert summary["continuation_count"] == 0
    assert summary["completed"] is True


async def test_technical_continuations_respect_existing_daily_provider_budget(settings):
    settings.daily_ai_budget_rub = 0.5
    client = AsyncMock()
    client.call_agent.return_value = fragment("Сохранённый фрагмент.", "length")
    result = await answer(settings, CheckpointRepo(), client)
    assert result.incomplete_reason == "budget_limit"
    assert not result.is_complete
    assert client.call_agent.await_count == 1


@pytest.mark.parametrize("has_fragment", [True, False])
async def test_provider_outage_keeps_partial_or_returns_failure_without_replay(
    settings, has_fragment
):
    client = AsyncMock()
    calls = [fragment("Не пробуйте сомнительный продукт.", "length")] if has_fragment else []
    client.call_agent.side_effect = calls + [TimewebTimeoutError("unknown")]
    repo = CheckpointRepo()
    result = await answer(settings, repo, client)
    assert len(repo.events) == 1 + int(has_fragment)
    assert not repo.events[-1].success
    if has_fragment:
        assert not result.is_complete
        assert result.incomplete_reason == "provider_error"
        assert "Не пробуйте" in result.text
    else:
        assert result is None


async def test_restart_resumes_durable_fragment_without_repeating_initial_call(settings):
    repo = CheckpointRepo()
    repo.interrupt_after_fragment = True
    first = AsyncMock()
    first.call_agent.return_value = fragment("Рецептура: 2% соли, 20 °C.", "length")
    with pytest.raises(asyncio.CancelledError):
        await answer(settings, repo, first)
    restarted = AsyncMock()
    restarted.call_agent.return_value = fragment("Контроль: измеряйте pH.")
    result = await answer(settings, repo, restarted)
    assert result.is_complete
    assert restarted.call_agent.await_count == 1
    assert result.text.startswith("Рецептура: 2% соли, 20 °C.")
    assert len(repo.events) == 2


async def test_restart_during_unknown_external_call_does_not_replay(settings):
    client = AsyncMock()
    client.call_agent.side_effect = [
        fragment("Сохранённый фрагмент.", "length"),
        asyncio.CancelledError(),
    ]
    repo = CheckpointRepo()
    with pytest.raises(asyncio.CancelledError):
        await answer(settings, repo, client)
    assert repo.state["progress"]["in_flight"]
    restarted = AsyncMock()
    result = await answer(settings, repo, restarted)
    assert result.text == "Сохранённый фрагмент."
    assert not result.is_complete
    assert result.incomplete_reason == "outcome_unknown"
    restarted.call_agent.assert_not_called()
    assert repo.events[-1].error_code == "outcome_unknown"


async def test_large_deep_answer_keeps_structure_and_safe_telegram_parts(settings):
    text = "\n\n".join(
        f"**Этап {n}**\n\nИзмеряйте pH. Не пробуйте сомнительный продукт." for n in range(180)
    )
    client = AsyncMock()
    client.call_agent.return_value = fragment(text, tokens=4900)
    result = await answer(settings, CheckpointRepo(), client)
    assert result.text == text
    assert result.is_complete
    parts = delivery_plan(result.text, rich_enabled=False)
    assert len(parts) > 2
    assert all(len(p.encode("utf-16-le")) // 2 <= 3800 for p, _ in parts)
    assert "".join(p for p, _ in parts).count("Не пробуйте сомнительный продукт.") == 180


def test_exact_overlap_completes_cut_sentence_without_rewriting_safety():
    original = "Шаг 1.\n\nНе определяйте безопасность по запаху, вкусу или цве"
    continuation = "Не определяйте безопасность по запаху, вкусу или цвету. Измеряйте pH."
    assert join_fragment(original, continuation) == original + "ту. Измеряйте pH."


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"status": "incomplete"}, "incomplete"),
        (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            "max_output_tokens",
        ),
        (
            {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}},
            "content_filter",
        ),
        ({"status": "incomplete", "incomplete_details": []}, "incomplete"),
    ],
)
def test_only_explicit_token_reason_confirms_token_limit(data, expected):
    assert _finish_reason(data) == expected
