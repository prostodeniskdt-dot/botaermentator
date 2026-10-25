"""Render the exact approved texts into their operator document; never contact Timeweb."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "app" / "prompts" / "approved"
TARGET = ROOT / "docs" / "TIMEWEB_AGENT_PROMPTS.md"


def render() -> str:
    manifest = json.loads((PROMPTS / "manifest.json").read_text(encoding="utf-8"))
    parts = [
        "# Утверждённые промпты Timeweb AI\n",
        f"Версия набора: `{manifest['version']}`. Дата: {manifest['date']} (Москва).\n",
        "Источник — три полных текста пользователя, утверждённые в переписке. "
        "Копии сохранены без редакторских изменений, в UTF-8 с переводами строк LF. "
        "Этот документ генерируется из app/prompts/approved; вручную его не редактируют.\n",
        "Фактически установленные тексты панели, подключение KB, модели и внешний поиск "
        "не проверены: доступа к Timeweb нет. Настройки панели и production не менялись.\n",
        "## Распределение инструкций\n",
        "| Источник | Ответственность |\n| --- | --- |\n"
        "| Полные утверждённые тексты | Роль, область, поведение, знания, источники, "
        "безопасность |\n"
        "| app/prompts/*.md | Короткий протокол JSON и границы доверия данных |\n"
        "| Python | Вопрос, текущая тема, профиль, Quick/Deep, уровень, расширение, лимиты |\n"
        "| recommendations.py | Релевантные ссылки проекта для свежих и кэшированных ответов |\n",
        "## Безопасный выбор источника\n",
        "По умолчанию `TIMEWEB_PROMPT_SOURCE=local`: каждый запрос включает полный "
        "утверждённый текст и короткий протокол. Сокращённый протокол сам по себе не является "
        "заменой полного промпта. Отсутствующий или пустой обязательный файл "
        "останавливает запуск.\n",
        "`TIMEWEB_PROMPT_SOURCE=panel` отправляет только короткий протокол и динамические данные. "
        f"Этот режим требует `TIMEWEB_PANEL_PROMPT_SET_VERSION={manifest['version']}`. "
        "Указание версии — декларация оператора, а не автоматическая проверка панели. "
        "Перед включением оператор должен отдельно сверить все три текста и проверить применение "
        "инструкций, KB и поиска на используемых native/chat-completions/Responses API. "
        "Автоматического переключения источника или повтора генерации ради fallback нет.\n",
        "Версия кэша включает PROMPT_VERSION, версию набора и источник промптов, дополнительно "
        "к версии KB, режиму и уровню. При изменении текстов панели нужно обновить утверждённый "
        "набор и его версию; панель нельзя менять незаметно для приложения.\n",
        "## Проверка синхронизации\n",
        "`python scripts/sync_prompt_docs.py --check` проверяет документ и SHA-256 исходников. "
        "SHA-256 подтверждает целостность копий в репозитории, а не соответствие живой панели.\n",
    ]
    for name, digest in manifest["files"].items():
        raw = (PROMPTS / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"Approved prompt hash mismatch: {name}")
        parts.extend(
            [
                f"## {name}\n",
                f"SHA-256: `{digest}`\n",
                f"<!-- BEGIN {name} -->\n" + raw.decode("utf-8") + f"<!-- END {name} -->\n",
            ]
        )
    return "\n".join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != expected:
            raise SystemExit("Prompt document is out of sync; run scripts/sync_prompt_docs.py")
    else:
        TARGET.write_text(expected, encoding="utf-8")
