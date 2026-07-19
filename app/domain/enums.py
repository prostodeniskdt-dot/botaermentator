"""Domain enumerations."""

from enum import StrEnum


class AgentType(StrEnum):
    INDUSTRY_FILTER = "industry_filter"
    CONTEXT_RELATION = "context_relation"
    MAIN_EXPERT = "main_expert"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"


class QuestionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ANSWERED = "answered"
    REJECTED = "rejected"
    FAILED = "failed"


class ContextRelation(StrEnum):
    RELATED = "related"
    STANDALONE = "standalone"
    AMBIGUOUS = "ambiguous"


class RateLimitWindow(StrEnum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


class BlockTargetType(StrEnum):
    USER = "user"
    SESSION = "session"


class BlockAction(StrEnum):
    BLOCK = "block"
    UNBLOCK = "unblock"
