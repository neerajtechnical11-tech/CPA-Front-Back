"""Lightweight, dependency-free resilience helpers (pure stdlib).

Wrap a remote call with `resilience.call(name, fn)` to get:
  - Retry with exponential backoff + jitter on transient failures (429 / 5xx /
    timeout / connection errors).
  - A per-name Circuit Breaker that fails fast (raises CircuitOpen) when a
    dependency is repeatedly failing, then half-opens to probe recovery.

Happy path: fn() runs once and returns - no behavioural change. Timeouts are
enforced by the SDK clients (e.g. the Azure OpenAI client's `timeout=`), so a
hung call is turned into a transient error this layer can retry / trip on.

Tunable via env (sensible defaults):
  RESILIENCE_RETRY_ATTEMPTS (3), RESILIENCE_RETRY_BASE (1.0s),
  RESILIENCE_RETRY_CAP (20s), RESILIENCE_BREAKER_FAILS (5),
  RESILIENCE_BREAKER_RESET (30s).
"""
import os
import random
import threading
import time

ATTEMPTS = int(os.getenv("RESILIENCE_RETRY_ATTEMPTS", "3"))
BASE = float(os.getenv("RESILIENCE_RETRY_BASE", "1.0"))
CAP = float(os.getenv("RESILIENCE_RETRY_CAP", "20"))
FAIL_MAX = int(os.getenv("RESILIENCE_BREAKER_FAILS", "5"))
RESET = float(os.getenv("RESILIENCE_BREAKER_RESET", "30"))

_TRANSIENT_CODES = {408, 425, 429, 500, 502, 503, 504}


class CircuitOpen(Exception):
    """Raised when a circuit breaker is open (the dependency is deemed unhealthy)."""


def is_transient(exc: Exception) -> bool:
    """True for errors worth retrying: rate limits, timeouts, connection drops, 5xx."""
    if isinstance(exc, CircuitOpen):
        return False
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    if isinstance(code, int) and code in _TRANSIENT_CODES:
        return True
    name = type(exc).__name__.lower()
    return any(k in name for k in ("ratelimit", "timeout", "connection",
                                   "servicerequest", "serviceresponse", "apiconnection"))


class _Breaker:
    def __init__(self, name):
        self.name = name
        self.fails = 0
        self.opened_at = 0.0
        self.lock = threading.Lock()

    def before(self):
        """Raise CircuitOpen while open; allow a half-open trial after the reset window."""
        with self.lock:
            if self.opened_at:
                if time.monotonic() - self.opened_at >= RESET:
                    return  # half-open: let one trial through
                raise CircuitOpen(f"{self.name} circuit open")

    def ok(self):
        with self.lock:
            self.fails = 0
            self.opened_at = 0.0

    def bad(self):
        with self.lock:
            self.fails += 1
            if self.fails >= FAIL_MAX:
                self.opened_at = time.monotonic()


_breakers = {}
_reg_lock = threading.Lock()


def _breaker(name):
    with _reg_lock:
        b = _breakers.get(name)
        if b is None:
            b = _Breaker(name)
            _breakers[name] = b
        return b


def call(name, fn, *, attempts=None):
    """Run fn() under a circuit breaker + retry(backoff+jitter).

    - Transient errors (429/5xx/timeout/connection) are retried and count toward
      the breaker; a non-transient error (e.g. a 400 bad request) is raised
      immediately and does NOT trip the breaker.
    - When the breaker is open, fails fast with CircuitOpen (no waiting on a dead
      dependency).
    """
    attempts = attempts or ATTEMPTS
    br = _breaker(name)
    last = None
    for i in range(attempts):
        br.before()  # may raise CircuitOpen -> fail fast
        try:
            out = fn()
            br.ok()
            return out
        except CircuitOpen:
            raise
        except Exception as e:  # noqa: BLE001 - we re-raise below
            if not is_transient(e):
                raise  # e.g. a genuine bad request - don't retry, don't trip
            last = e
            br.bad()
            if i == attempts - 1:
                break
            delay = min(CAP, BASE * (2 ** i)) * (0.5 + random.random())  # backoff + jitter
            time.sleep(delay)
    raise last


def status() -> dict:
    """Current breaker states (for health / observability)."""
    with _reg_lock:
        return {n: ("open" if b.opened_at and (time.monotonic() - b.opened_at < RESET)
                    else "closed") for n, b in _breakers.items()}
