"""Langfuse observability - traces every 'task' the app runs so an operator can
watch, in the Langfuse dashboard:

  1. Tool Call        - a span (doc parse, AI Search fetch, email send, Jira POST)
  2. Model Call       - a generation (Azure OpenAI embed / chat) with token usage
  3. Decision Branch  - an event (scoring thresholds, gap routing, chat routing)
  4. Outcome          - the task's output / status
  5. Cost per task    - derived by Langfuse from each generation's token usage

Graceful no-op when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are unset (mirrors
Cosmos / SMTP / Entra): the context managers and helpers still work, they just
record nothing. langfuse itself is imported lazily so the import is free when off.
"""
import os
import socket
import urllib.parse
from contextlib import contextmanager, nullcontext

_client = None
_state = None  # None = unchecked, False = disabled, True = enabled


def _reachable(host: str, timeout: float = 2.0) -> bool:
    """Quick TCP check so a DOWN Langfuse server disables tracing instead of
    hanging every task on export/flush timeouts."""
    try:
        u = urllib.parse.urlparse(host)
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True
    except Exception:
        return False


def _lf():
    """Return the Langfuse client, or None when tracing is disabled/misconfigured.

    Tracing is a safe no-op when the keys are unset OR the Langfuse host is
    unreachable - so a stopped Langfuse server can't hang assessments.
    """
    global _client, _state
    if _state is None:
        pub, sec = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
        host = (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")
                or "http://localhost:3000").strip().strip('"')
        if pub and sec and _reachable(host):
            try:
                from langfuse import Langfuse
                _client = Langfuse(public_key=pub, secret_key=sec, host=host)
                _state = True
            except Exception:
                _client, _state = None, False
        else:
            if pub and sec:
                print(f"[tracing] Langfuse host {host} unreachable - tracing disabled for this run.")
            _state = False
    return _client if _state else None


def is_enabled() -> bool:
    return _lf() is not None


class _Null:
    """Stands in for a Langfuse span/generation when tracing is off."""
    def update(self, **k):
        pass

    def score(self, *a, **k):
        pass

    def create_event(self, **k):
        pass


_NULL = _Null()


@contextmanager
def task(name, *, input=None, metadata=None, as_type="chain",
         user_id=None, session_id=None, tags=None):
    """Top-level trace = one user-facing task (assessment, chat, routing, ticket).

    Best-practice trace attributes (Langfuse v4 `propagate_attributes`):
      - user_id     - who ran it (enables per-user filtering + cost attribution)
      - session_id  - groups related traces; we pass the assessment correlation id
                      so an assessment and its follow-on chat / routing / ticket
                      traces replay together in the Sessions view
      - tags        - per-feature filtering on the dashboard
      - as_type     - pass "agent" for the analyst-chat and gap-routing agents so
                      each shows as its own node in the Langfuse Agent Graph

    Flushes on exit so the task shows up promptly in the dashboard (Streamlit is a
    long-lived process, so we don't rely on interpreter-shutdown flushing).
    """
    lf = _lf()
    if lf is None:
        yield _NULL
        return
    # Propagate identity / session / feature attributes to the trace and children.
    kw = {k: v for k, v in (("user_id", user_id),
                            ("session_id", session_id),
                            ("tags", tags)) if v}
    if kw:
        from langfuse import propagate_attributes
        attrs = propagate_attributes(**kw)
    else:
        attrs = nullcontext()
    with attrs, lf.start_as_current_observation(
        name=name, as_type=as_type, input=input, metadata=metadata
    ) as root:
        try:
            yield root
        finally:
            try:
                lf.flush()
            except Exception:
                pass


@contextmanager
def step(name, as_type="tool", *, input=None, metadata=None):
    """A nested span under the current task - a Tool Call by default."""
    lf = _lf()
    if lf is None:
        yield _NULL
        return
    with lf.start_as_current_observation(
        name=name, as_type=as_type, input=input, metadata=metadata
    ) as sp:
        yield sp


@contextmanager
def model_call(name, *, model=None, input=None, model_parameters=None):
    """A Model Call (generation). The caller updates output + usage_details on the
    yielded object so Langfuse can attribute token cost to the enclosing task."""
    lf = _lf()
    if lf is None:
        yield _NULL
        return
    with lf.start_as_current_observation(
        name=name, as_type="generation", model=model,
        input=input, model_parameters=model_parameters,
    ) as gen:
        yield gen


def decision(name, chosen, *, options=None, metadata=None):
    """Record a Decision Branch as an event on the current task/span."""
    lf = _lf()
    if lf is None:
        return
    try:
        md = dict(metadata or {})
        md["chosen"] = chosen
        if options:
            md["options"] = options
        lf.create_event(name=name, metadata=md)
    except Exception:
        pass


def usage_from_openai(resp):
    """Map an OpenAI usage object to Langfuse usage_details (input/output/total)."""
    u = getattr(resp, "usage", None)
    if not u:
        return None
    out = {}
    for src, dst in (("prompt_tokens", "input"),
                     ("completion_tokens", "output"),
                     ("total_tokens", "total")):
        v = getattr(u, src, None)
        if v is not None:
            out[dst] = int(v)
    return out or None
