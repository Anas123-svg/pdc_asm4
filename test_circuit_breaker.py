

import sys
import time
import threading
import asyncio
import unittest
from unittest.mock import patch, MagicMock

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState


ANSI_GREEN  = "\033[92m"
ANSI_RED    = "\033[91m"
ANSI_YELLOW = "\033[93m"
ANSI_BLUE   = "\033[94m"
ANSI_RESET  = "\033[0m"

def ok(msg):  print(f"{ANSI_GREEN}  ✓  {msg}{ANSI_RESET}")
def fail(msg):print(f"{ANSI_RED}  ✗  {msg}{ANSI_RESET}")
def info(msg):print(f"{ANSI_BLUE}  ─  {msg}{ANSI_RESET}")
def warn(msg):print(f"{ANSI_YELLOW}  ⚠  {msg}{ANSI_RESET}")


def make_breaker(threshold=3, timeout=2.0):
    return CircuitBreaker(
        failure_threshold=threshold,
        recovery_timeout=timeout,
        name="test-llm",
    )


def always_succeed():
    return "LLM answer"


def always_fail():
    raise ConnectionError("LLM timed out after 60 s")



class TestCircuitBreaker(unittest.TestCase):

    def test_healthy_request_succeeds(self):
        cb = make_breaker()
        result = cb.call(always_succeed)
        self.assertEqual(result, "LLM answer")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_failure_increments_counter(self):
        """Each failure increments the internal counter."""
        cb = make_breaker(threshold=5)
        for i in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(always_fail)
        self.assertEqual(cb._failure_count, 2)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_circuit_trips_to_open_after_threshold(self):
        """After *threshold* consecutive failures the circuit becomes OPEN."""
        cb = make_breaker(threshold=3)
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                cb.call(always_fail)
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_open_circuit_fast_fails_without_calling_llm(self):

        cb = make_breaker(threshold=3)
        # Trip the breaker
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                cb.call(always_fail)

        call_count = {"n": 0}

        def spy():
            call_count["n"] += 1
            return "should not reach here"

        start = time.monotonic()
        with self.assertRaises(CircuitBreakerOpenError):
            cb.call(spy)
        elapsed = time.monotonic() - start

        self.assertEqual(call_count["n"], 0, "Underlying function must NOT be called when OPEN")
        self.assertLess(elapsed, 1.0, "Fast-fail must complete in < 1 s (was blocking!)")

    def test_circuit_transitions_to_half_open_after_timeout(self):
        cb = make_breaker(threshold=2, timeout=0.5)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(always_fail)
        self.assertEqual(cb.state, CircuitState.OPEN)
        time.sleep(0.6)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes_circuit(self):
        cb = make_breaker(threshold=2, timeout=0.3)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                cb.call(always_fail)
        time.sleep(0.4)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        result = cb.call(always_succeed)
        self.assertEqual(result, "LLM answer")
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_thread_safety(self):
        cb = make_breaker(threshold=10, timeout=60)
        errors = []

        def worker():
            for _ in range(20):
                try:
                    cb.call(always_succeed)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(cb.state, CircuitState.CLOSED)



def run_before_after_demo():
    SEPARATOR = "=" * 60

    print(f"\n{SEPARATOR}")
    print("  StudySync – Circuit Breaker  BEFORE / AFTER  Demo")
    print(SEPARATOR)

    print(f"\n{ANSI_RED}━━━  BEFORE  (no circuit breaker)  ━━━{ANSI_RESET}")
    print("  Scenario: LLM API is down and hangs for 60 s per call.")
    print("  Three concurrent users hit /api/ask …\n")

    def naive_llm_call(user_id):
        """Simulates blocking call with no circuit breaker — hangs for 2s (we fake 60s)."""
        start = time.monotonic()
        info(f"User {user_id}: request starts …")
        time.sleep(2)   
        elapsed = time.monotonic() - start
        fail(f"User {user_id}: request TIMED OUT after {elapsed:.1f}s (simulates 60 s hang)")

    start_all = time.monotonic()
    threads = [threading.Thread(target=naive_llm_call, args=(i,)) for i in range(1, 4)]
    for t in threads: t.start()
    for t in threads: t.join()
    total = time.monotonic() - start_all
    fail(f"RESULT: All 3 users experienced a {total:.1f}s hang. Server is effectively dead.\n")

    print(f"{ANSI_GREEN}━━━  AFTER  (circuit breaker ENABLED)  ━━━{ANSI_RESET}")
    print("  Threshold = 3 failures, recovery timeout = 5 s\n")

    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=5.0, name="LLM-API")

    FALLBACK = "AI is temporarily unavailable. Please try again shortly."

    def protected_llm_call(user_id, force_fail=False):
        start = time.monotonic()
        try:
            result = cb.call(always_succeed if not force_fail else always_fail)
            elapsed = time.monotonic() - start
            ok(f"User {user_id}: ✓ Answer received in {elapsed*1000:.0f} ms  →  '{result}'")
        except CircuitBreakerOpenError:
            elapsed = time.monotonic() - start
            warn(f"User {user_id}: Circuit OPEN → instant fallback in {elapsed*1000:.0f} ms  →  '{FALLBACK}'")
        except Exception as exc:
            elapsed = time.monotonic() - start
            fail(f"User {user_id}: LLM error ({exc}) in {elapsed*1000:.0f} ms — failure recorded")

    info("Step 1 – Normal requests while LLM is healthy:")
    protected_llm_call(1, force_fail=False)
    protected_llm_call(2, force_fail=False)
    print()

    info("Step 2  LLM goes down; requests start failing:")
    for i in range(3, 6):
        protected_llm_call(i, force_fail=True)
    print()

    info(f"Step 3  Circuit is now {cb.state.value}. Next requests get instant fallback:")
    for i in range(6, 9):
        protected_llm_call(i, force_fail=True)  # irrelevant — breaker blocks them
    print()

    info(f"Step 4 Waiting {cb.recovery_timeout:.0f}s for recovery timeout …")
    time.sleep(cb.recovery_timeout + 0.1)
    info(f"Circuit state after wait: {cb.state.value}")
    print()

    info("Step 5  LLM is back. First probe closes the circuit:")
    protected_llm_call(9, force_fail=False)
    info(f"Circuit state now: {cb.state.value}")
    print()

    ok("RESULT: Only the first 3 failures were slow. All subsequent requests")
    ok("        got an instant fallback. Server remained responsive throughout.")
    print(f"\n{SEPARATOR}\n")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)   
    run_before_after_demo()

    print("Running unit tests …\n")
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestCircuitBreaker)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)