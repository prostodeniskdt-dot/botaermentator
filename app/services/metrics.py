"""Small structured stage timings; never record request contents."""

from contextlib import contextmanager
from time import perf_counter

from app.logging import get_logger

logger = get_logger(__name__)


@contextmanager
def stage(name: str, **fields):
    started = perf_counter()
    success = False
    try:
        yield
        success = True
    finally:
        logger.info(
            "stage_timing",
            stage=name,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            success=success,
            **fields,
        )
