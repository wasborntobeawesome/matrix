"""Machine-readable output for the Video Studio AI queue.

The studio runs the AI scripts as subprocesses and reads lines that start with
  @@PROGRESS {"stage": ..., "done": k, "total": n, ...}
  @@RESULT {...}
Everything else on stdout/stderr is shown as the job's log tail. The warm worker (worker.py)
adds @@READY / @@STATUS / @@ERROR lines.
"""
import json
import sys
import threading

_LOCK = threading.Lock()   # the warm worker writes from two threads


def emit(kind: str, **data) -> None:
    # leading newline: never glued to a half-written log line (tqdm, library prints)
    line = f"\n@@{kind} " + json.dumps(data, ensure_ascii=False) + "\n"
    with _LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def progress(stage: str, done: int = 0, total: int = 0, **extra) -> None:
    emit("PROGRESS", stage=stage, done=done, total=total, **extra)


def result(**data) -> None:
    emit("RESULT", **data)


def vram_stats() -> dict:
    import torch
    return {"peak_vram_allocated_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "peak_vram_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2)}
