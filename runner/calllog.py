"""One terminal line per model call, plus per-model totals for the end-of-run summary.

    [chat ] deepseek-v4.1-flash   form answers            4.8s  in 3,912 / out 287 tok
    [jev  ] jev-1.13              form answers (6 q)      0.52s in 812 tok  $0.00003
Calls can run in parallel (cover letter + essays), so printing is serialised with a lock.
"""
import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_stats: dict[tuple[str, str], dict] = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "max": 0.0, "fails": 0})


def _short(model: str) -> str:
    return model.split("/")[-1]


def record(kind: str, model: str, purpose: str, seconds: float, detail: str = "", ok: bool = True):
    s = _stats[(kind, _short(model))]
    s["calls"] += 1
    s["seconds"] += seconds
    s["max"] = max(s["max"], seconds)
    if not ok:
        s["fails"] += 1
    secs = f"{seconds:.2f}s" if seconds < 10 else f"{seconds:.1f}s"
    line = f"      [{kind:<4}] {_short(model):<22} {purpose[:30]:<30} {secs:>7}  {detail}"
    with _lock:
        print(line.rstrip(), flush=True)


def note(msg: str):
    with _lock:
        print(f"      {msg}", flush=True)


class timer:
    """with timer() as t: ... ; t.seconds"""

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.seconds = time.time() - self.t0
        return False


def summary() -> str:
    if not _stats:
        return "No model calls."
    rows = ["Model time (sum of call durations; parallel calls overlap):",
            f"  {'kind':<5} {'model':<24} {'calls':>5} {'total':>8} {'avg':>7} {'slowest':>8} {'failed':>6}"]
    for (kind, model), s in sorted(_stats.items(), key=lambda kv: -kv[1]["seconds"]):
        avg = s["seconds"] / max(1, s["calls"])
        rows.append(f"  {kind:<5} {model:<24} {s['calls']:>5} {s['seconds']:>7.1f}s {avg:>6.2f}s {s['max']:>7.2f}s {s['fails']:>6}")
    return "\n".join(rows)
