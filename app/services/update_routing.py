"""Only explicit, non-AI actions get the reserved service-command capacity."""

from app.bot.keyboards import MENU_BUTTONS

SERVICE_COMMANDS = frozenset(
    {
        "start",
        "help",
        "new",
        "quick",
        "deep",
        "mode",
        "level",
        "balance",
        "privacy",
        "remember",
        "profile",
        "forget",
        "admin_pending",
        "admin_allow",
        "admin_reject",
        "admin_user",
        "admin_grant_credits",
        "admin_block_user",
        "admin_unblock_user",
        "admin_block_session",
        "admin_unblock_session",
        "admin_cost_today",
        "admin_latency",
        "admin_kill_switch_on",
        "admin_kill_switch_off",
    }
)


def update_lane(payload: dict) -> str:
    message = payload.get("message") or {}
    text = message.get("text") or ""
    if text in MENU_BUTTONS:
        return "service"
    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
    if command.startswith("/") and command[1:] in SERVICE_COMMANDS:
        return "service"
    callback = (payload.get("callback_query") or {}).get("data") or ""
    if callback.startswith(("level:", "forget:", "feedback:")) or "my_chat_member" in payload:
        return "service"
    return "question"
