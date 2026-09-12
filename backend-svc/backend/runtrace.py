"""Lightweight per-assessment pipeline tracer.

Records each stage of an assessment (start -> ok / error / skipped) with a short
detail string and elapsed milliseconds, so the UI can show an analyst exactly how
far a run got and where it stopped. Purely in-memory (one object per run); nothing
is persisted here.
"""
import time
from contextlib import contextmanager


class RunTracker:
    def __init__(self):
        self.steps = []  # list of {key,label,file,func,status,detail,ms}

    @contextmanager
    def step(self, key, label, file, func):
        rec = {"key": key, "label": label, "file": file, "func": func,
               "status": "running", "detail": "", "ms": None}
        self.steps.append(rec)
        t = time.perf_counter()
        try:
            yield rec
            if rec["status"] == "running":
                rec["status"] = "ok"
        except Exception as e:  # record where it stopped, then re-raise
            rec["status"] = "error"
            rec["detail"] = f"{type(e).__name__}: {e}"[:400]
            raise
        finally:
            rec["ms"] = round((time.perf_counter() - t) * 1000)

    def skip(self, key, label, file, func, detail=""):
        self.steps.append({"key": key, "label": label, "file": file, "func": func,
                           "status": "skipped", "detail": detail, "ms": None})


class _Null(RunTracker):
    """No-op tracker used when analyze() is called without tracing."""
    @contextmanager
    def step(self, *a, **k):
        yield {}

    def skip(self, *a, **k):
        pass


NULL = _Null()
