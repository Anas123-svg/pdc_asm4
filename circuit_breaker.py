
import time
import threading
import logging
from enum import Enum
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit is OPEN."""
    pass


class CircuitBreaker:

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        expected_exception: type = Exception,
        name: str = "CircuitBreaker",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    #  Public API                                                       

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._get_state()

    def call(self, func: Callable, *args, **kwargs) -> Any:

        with self._lock:
            current_state = self._get_state()

            if current_state == CircuitState.OPEN:
                logger.warning("[%s] Circuit OPEN — fast-fail, no call made.", self.name)
                raise CircuitBreakerOpenError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Retry after {self._seconds_until_retry():.1f}s."
                )

            if current_state == CircuitState.HALF_OPEN:
                logger.info("[%s] Circuit HALF_OPEN — sending probe request.", self.name)

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as exc:
            self._on_failure()
            raise

    #  Internal helpers                                                

    def _get_state(self) -> CircuitState:
        """Transition OPEN → HALF_OPEN if the recovery timeout has elapsed."""
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and (time.monotonic() - self._last_failure_time) >= self.recovery_timeout
        ):
            logger.info("[%s] Recovery timeout elapsed → HALF_OPEN.", self.name)
            self._state = CircuitState.HALF_OPEN
        return self._state

    def _on_success(self):
        with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
                logger.info("[%s] Success — resetting to CLOSED.", self.name)
                self._failure_count = 0
                self._state = CircuitState.CLOSED

    def _on_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            logger.warning(
                "[%s] Failure #%d (threshold=%d).",
                self.name, self._failure_count, self.failure_threshold,
            )
            if self._failure_count >= self.failure_threshold:
                logger.error("[%s] Threshold reached → OPEN.", self.name)
                self._state = CircuitState.OPEN

    def _seconds_until_retry(self) -> float:
        if self._last_failure_time is None:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def __repr__(self):
        return (
            f"<CircuitBreaker name={self.name!r} state={self._state.value} "
            f"failures={self._failure_count}/{self.failure_threshold}>"
        )