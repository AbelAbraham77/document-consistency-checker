"""One concurrency budget shared by extraction, verification and embeddings.

This implementation coordinates threads in one process. A future Redis-backed
lease limiter can implement the same interface for multiple worker processes.
Acquire per HTTP attempt, not during retry backoff or cache hits.
"""

from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import ContextManager, Protocol

from app.config import Settings


class LLMLimiter(Protocol):
    def slot(self) -> ContextManager[None]: ...


class LLMLimitTimeout(TimeoutError):
    """No capacity became available within the configured queue timeout."""


class InProcessLLMLimiter:
    def __init__(self, concurrency: int, *, wait_timeout: float = 30):
        if type(concurrency) is not int or concurrency < 1 or wait_timeout <= 0:
            raise ValueError("LLM concurrency and queue timeout must be positive")
        self._slots = BoundedSemaphore(concurrency)
        self.wait_timeout = wait_timeout

    @contextmanager
    def slot(self):
        if not self._slots.acquire(timeout=self.wait_timeout):
            raise LLMLimitTimeout("LLM request capacity is temporarily unavailable")
        try:
            yield
        finally:
            self._slots.release()


_limiter_lock = Lock()
_default_limiter = None


def get_llm_limiter() -> LLMLimiter:
    # lru_cache alone can run the factory twice during simultaneous first calls.
    global _default_limiter
    with _limiter_lock:
        if _default_limiter is None:
            settings = Settings()
            _default_limiter = InProcessLLMLimiter(settings.llm_max_concurrency,
                                                  wait_timeout=settings.llm_queue_timeout_seconds)
        return _default_limiter
