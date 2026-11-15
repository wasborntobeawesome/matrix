"""Shared-folder inbox: video/inbox/<any-name>/brief.json + asset files -> new project.

Polled every few seconds. A folder is imported once its files have not changed
for STABLE_SECONDS (so half-copied folders are not picked up), then moved to
video/inbox/_imported/<name>-<timestamp>/ with an IMPORTED.txt naming the project.
On failure ERROR.txt is written into the folder; it is retried when files change.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from brief import BriefError
from store import StoreError, import_project, kind_of

VIDEO = Path(__file__).resolve().parent.parent
INBOX = VIDEO / "inbox"
POLL_SECONDS = 3
STABLE_SECONDS = 2

log: list[dict] = []  # recent events for GET /api/inbox


def _event(**kw):
    log.insert(0, {"time": time.time(), **kw})
    del log[50:]


def _files(d: Path) -> list[Path]:
    out = [f for f in d.iterdir() if f.is_file()]
    sub = d / "assets"
    if sub.is_dir():
        out += [f for f in sub.iterdir() if f.is_file()]
    return out


def scan_once() -> list[str]:
    INBOX.mkdir(parents=True, exist_ok=True)
    imported = []
    for d in sorted(INBOX.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        try:
            files = _files(d)
        except OSError:
            continue
        brief_f = d / "brief.json"
        if not brief_f.exists():
            continue
        err = d / "ERROR.txt"
        content = [f for f in files if f.name not in ("ERROR.txt", "IMPORTED.txt")]
        newest = max((f.stat().st_mtime for f in content), default=0)
        if time.time() - newest < STABLE_SECONDS:
            continue  # still being written
        if err.exists() and err.stat().st_mtime >= newest:
            continue  # failed before and nothing changed since
        try:
            pid, warnings = _import(d, content)
        except (BriefError, StoreError, ValueError, OSError) as e:
            err.write_text(f"Import failed {datetime.now():%Y-%m-%d %H:%M:%S}\n\n{e}\n", encoding="utf-8")
            _event(folder=d.name, ok=False, error=str(e))
            continue
        dest = INBOX / "_imported" / f"{d.name}-{datetime.now():%Y%m%d-%H%M%S}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        err.unlink(missing_ok=True)
        (d / "IMPORTED.txt").write_text(f"project: {pid}\nwarnings:\n" + "\n".join(f"- {w}" for w in warnings) + "\n",
                                        encoding="utf-8")
        shutil.move(str(d), str(dest))
        _event(folder=d.name, ok=True, project=pid, warnings=warnings)
        imported.append(pid)
    return imported


def _import(d: Path, files: list[Path]) -> tuple[str, list[str]]:
    try:
        raw = json.loads((d / "brief.json").read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise BriefError([f"brief.json is not valid JSON: {e}"]) from None
    with tempfile.TemporaryDirectory(prefix="studio-inbox-") as tmp:
        staged = []
        for f in files:
            if f.name == "brief.json" or not kind_of(f.name):
                continue
            t = Path(tmp) / f"{len(staged)}-{f.name}"
            shutil.copy2(f, t)  # copy: the inbox folder is kept (moved to _imported)
            staged.append((t, f.name))
        p, warnings = import_project(raw, staged, "inbox")
    return p.id, warnings


def start() -> None:
    def loop():
        while True:
            try:
                scan_once()
            except Exception as e:  # never let the watcher die
                _event(folder="*", ok=False, error=f"watcher: {e}")
            time.sleep(POLL_SECONDS)
    threading.Thread(target=loop, name="inbox", daemon=True).start()
