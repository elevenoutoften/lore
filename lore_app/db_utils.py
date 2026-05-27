from __future__ import annotations

import logging
import sqlite3
import time
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def retry_on_locked(
    max_retries: int = 3,
    delay: float = 0.1,
    backoff: float = 2.0,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Retry a function on sqlite3.OperationalError 'database is locked'."""

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            retries = 0
            current_delay = delay
            while True:
                try:
                    return fn(*args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower():
                        raise
                    retries += 1
                    if retries > max_retries:
                        raise
                    logger.warning(
                        "SQLite database locked, retrying (%d/%d): %s",
                        retries,
                        max_retries,
                        exc,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator
