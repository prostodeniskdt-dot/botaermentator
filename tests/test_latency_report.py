from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import User

from app.bot.routers.admin import admin_latency
from app.services.update_routing import update_lane


async def test_latency_command_denies_non_admin_before_accessing_database(settings):
    message = SimpleNamespace(
        from_user=User(id=7, is_bot=False, first_name="Test"), answer=AsyncMock()
    )
    await admin_latency(message, settings)
    message.answer.assert_not_awaited()


def test_latency_command_has_reserved_service_capacity():
    assert update_lane({"message": {"text": "/admin_latency@bot"}}) == "service"


async def test_invalid_json_retry_does_not_double_count_first_call_time(settings):
    import uuid

    from app.agents.industry_filter import IndustryFilterAgent
    from app.agents.timeweb_client import TimewebCallResult, TimewebTimeoutError

    client = AsyncMock()
    client.call_agent.side_effect = [
        TimewebCallResult(
            message="not json",
            response_id=None,
            finish_reason="stop",
            http_status=200,
            latency_ms=1,
            raw={},
        ),
        TimewebTimeoutError("synthetic", error_code="timeout"),
    ]
    repo = AsyncMock()
    assert (
        await IndustryFilterAgent(settings, client).evaluate(
            repo, question="q", session_id=uuid.uuid4(), question_id=uuid.uuid4()
        )
        is None
    )
    first, second = [args.args[0] for args in repo.record_usage_event.await_args_list]
    assert second.started_at >= first.finished_at
    assert not second.success
