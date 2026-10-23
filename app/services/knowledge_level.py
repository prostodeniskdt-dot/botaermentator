"""Optional explanation level, independent of answer depth."""

from enum import StrEnum


class KnowledgeLevel(StrEnum):
    DEFAULT = "default"
    BEGINNER = "beginner"
    ADVANCED = "advanced"
    PROFESSIONAL = "professional"


LEVEL_LABELS = {
    KnowledgeLevel.DEFAULT: "Универсальный",
    KnowledgeLevel.BEGINNER: "Начинающий",
    KnowledgeLevel.ADVANCED: "Продвинутый",
    KnowledgeLevel.PROFESSIONAL: "Профессионал",
}
LEVEL_INSTRUCTIONS = {
    KnowledgeLevel.DEFAULT: "",
    KnowledgeLevel.BEGINNER: (
        "Explain simply; define essential terms and explain what to do and why."
    ),
    KnowledgeLevel.ADVANCED: "Skip basic definitions; explain process parameters and mechanisms.",
    KnowledgeLevel.PROFESSIONAL: (
        "Use technically dense explanations, production constraints, control parameters, "
        "diagnostics and technological alternatives."
    ),
}


def knowledge_level(profile: dict | None) -> KnowledgeLevel:
    try:
        return KnowledgeLevel((profile or {}).get("knowledge_level", "default"))
    except (ValueError, TypeError):
        return KnowledgeLevel.DEFAULT
