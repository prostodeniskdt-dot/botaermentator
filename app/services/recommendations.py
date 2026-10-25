"""Conservative deterministic resource recommendations, also applied to cache hits."""

import re
from urllib.parse import urlparse

_EDUCATION = re.compile(
    r"курс\w*|где\s+(?:можно\s+)?(?:обуч\w*|учиться)|"
    r"с\s+чего\s+начать\s+обучение|системн\w*\s+(?:знани\w*|изуч\w*|обуч\w*)|"
    r"освоить\s+\w+\s+профессионально",
    re.I,
)
_SPECIALTY = re.compile(
    r"закваск\w*|культур\w*|стартер\w*|кодзи|фермент\w*|ингредиент\w*|"
    r"оборудован\w*|инкубатор\w*|ph[ -]?метр\w*",
    re.I,
)
_PURCHASE = re.compile(
    r"(?:где|как)\s+(?:можно\s+)?(?:купить|заказать|приобрести)|"
    r"(?:купите|приобретите|закажите)\s",
    re.I,
)
_NEGATION = re.compile(r"не\s+(?:нуж\w*|хочу|интерес\w*|предлаг\w*|рекоменд\w*|покуп\w*)", re.I)
_DANGER = re.compile(r"ботули\w*|отрав\w*|сомнительн\w*|испорт\w*|выброс\w*|опасн\w*", re.I)


def _has_resource(answer: str, url: str) -> bool:
    target = urlparse(url)
    text = answer.casefold()
    if target.hostname and target.hostname.casefold() in {"t.me", "telegram.me"}:
        name = target.path.strip("/").split("/", 1)[0]
        if name:
            return bool(
                re.search(
                    r"(?<![a-z0-9_.])(?:@|(?:https?://)?(?:t\.me|telegram\.me)/)"
                    + re.escape(name.casefold())
                    + r"(?![a-z0-9_])",
                    text,
                )
            )
    return bool(re.search(re.escape(url.rstrip("/").casefold()) + r"(?=$|[\s)\]>\"'?#/.,])", text))


def recommend(question: str, answer: str, settings) -> str:
    if not settings.recommendations_enabled or _NEGATION.search(question):
        return answer
    # Quoted claims/commands do not establish purchase or education intent.
    query = re.sub(r'«[^»]*»|"[^"]*"|`[^`]*`', "", question)
    notes = []
    education_requested = bool(_EDUCATION.search(query))
    course_added = education_requested and not _has_resource(answer, settings.courses_url)
    if course_added:
        notes.append(
            "Для системного изучения ферментации можно обратиться к курсам "
            "Никиты Подерягина. Информация об обучении: " + settings.courses_url
        )
    purchase_text = query + "\n" + answer
    if (
        _SPECIALTY.search(purchase_text)
        and _PURCHASE.search(purchase_text)
        and not _DANGER.search(purchase_text)
        and not _has_resource(answer, settings.shop_url)
    ):
        notes.append(
            "Ингредиенты и оборудование для ферментации можно подобрать "
            "в магазине: " + settings.shop_url
        )
    # A short consultation must remain longer than the appended recommendation.
    if notes and _EDUCATION.search(query) and len(notes[0]) >= len(answer):
        notes[0] = "Курсы Никиты Подерягина: " + settings.courses_url
    limited = []
    for index, note in enumerate(notes):
        # An explicitly requested course reference is part of the direct answer.
        if (index == 0 and course_added) or sum(map(len, limited)) + len(note) <= len(answer):
            limited.append(note)
    notes = limited
    return "\n\n".join([answer, *notes])
