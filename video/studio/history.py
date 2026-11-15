"""Version history of a project's brief: projects/<id>/history/<n>.json + index.json.

Every save of project.json makes a snapshot of the brief. Rapid autosaves are coalesced: an "edit"
snapshot younger than COALESCE seconds is updated in place instead of adding a new one, so typing
for a minute gives one or two versions, not sixty. The newest MAX_SNAPSHOTS are kept. Restoring a
version saves it as a new snapshot ("restore"), so a restore can itself be undone.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_SNAPSHOTS = 100
COALESCE = 45.0
_lock = threading.RLock()
TOP_LABELS = {"title": "title", "product": "product name", "language": "main language", "languages": "languages",
              "format": "main format", "formats": "formats", "brand": "brand colours / logo", "voice": "voice",
              "voices": "voices", "captions": "captions", "music": "music", "cover": "cover", "presenter": "presenter",
              "template": "template", "template_options": "template options", "transition": "transition"}


def _dir(p) -> Path:
    return p.dir / "history"


def _index(p) -> list[dict]:
    try:
        return json.loads((_dir(p) / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_index(p, idx: list[dict]) -> None:
    d = _dir(p)
    tmp = d / "index.json.tmp"
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "index.json")


def _read(p, sid: str) -> dict | None:
    if not sid.isdigit():
        return None
    try:
        return json.loads((_dir(p) / f"{sid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(p, snap: dict) -> None:
    (_dir(p) / f"{snap['id']}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")


def _meta(snap: dict) -> dict:
    return {k: snap[k] for k in ("id", "created", "updated", "kind", "summary", "label", "scenes") if k in snap}


def record(p, old_brief: dict | None, new_brief: dict, kind: str = "edit", label: str | None = None) -> dict | None:
    """Snapshot `new_brief` after a save. `old_brief` is the brief before the save (used as the first
    snapshot of projects that have no history yet). Returns the snapshot meta, or None if nothing changed."""
    with _lock:
        d = _dir(p)
        d.mkdir(parents=True, exist_ok=True)
        idx = _index(p)
        now = round(time.time(), 3)
        if not idx and old_brief is not None and old_brief != new_brief and kind != "import":
            base = {"id": "00001", "created": now - 0.001, "updated": now - 0.001, "kind": "base",
                    "summary": "Version before history was recorded", "scenes": len(old_brief.get("scenes", [])),
                    "brief": old_brief}
            _write(p, base)
            idx.append(_meta(base))
        last = _read(p, idx[-1]["id"]) if idx else None
        if last and last.get("brief") == new_brief and kind == "edit":
            return None
        coalesce = (kind == "edit" and last is not None and last.get("kind") == "edit"
                    and now - last.get("created", 0) < COALESCE)
        if coalesce:
            prev = _read(p, idx[-2]["id"]) if len(idx) > 1 else None
            if prev and prev.get("brief") == new_brief:
                # the burst of edits ended where it started (e.g. a change and its undo): no new version
                (d / f"{last['id']}.json").unlink(missing_ok=True)
                idx.pop()
                _write_index(p, idx)
                return None
            last.update(brief=new_brief, updated=now, scenes=len(new_brief.get("scenes", [])),
                        summary=summarize(prev["brief"] if prev else None, new_brief))
            _write(p, last)
            idx[-1] = _meta(last)
            snap = last
        else:
            nid = f"{(int(idx[-1]['id']) + 1) if idx else 1:05d}"
            snap = {"id": nid, "created": now, "updated": now, "kind": kind, "scenes": len(new_brief.get("scenes", [])),
                    "summary": label or summarize(last["brief"] if last else None, new_brief), "brief": new_brief}
            if label:
                snap["label"] = label
                ch = summarize(last["brief"], new_brief) if last else f"{len(new_brief.get('scenes', []))} scenes"
                if ch and ch != "No changes":
                    snap["summary"] = f"{label} — {ch}"
            _write(p, snap)
            idx.append(_meta(snap))
        while len(idx) > MAX_SNAPSHOTS:
            old = idx.pop(0)
            (d / f"{old['id']}.json").unlink(missing_ok=True)
        _write_index(p, idx)
        return _meta(snap)


def listing(p) -> list[dict]:
    """Newest first, without the briefs."""
    return list(reversed(_index(p)))


def get(p, sid: str) -> dict | None:
    return _read(p, sid)


# ------------------------------------------------------------------ change summary
def _txt(v) -> str:
    if isinstance(v, dict):
        return " / ".join(f"{k}: {x}" for k, x in v.items())
    return str(v if v is not None else "")


def _fields(a: dict, b: dict) -> list[str]:
    out = []
    keys = [k for k in dict.fromkeys([*a.keys(), *b.keys()]) if k not in ("id",)]
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict) and k in ("say", "headline", "sub", "kicker", "callout",
                                                                 "button", "url", "stat"):
            langs = [lg for lg in dict.fromkeys([*va, *vb]) if va.get(lg) != vb.get(lg)]
            out.append(f"{k} ({', '.join(langs)})" if langs else k)
        elif isinstance(va, dict) and isinstance(vb, str) or isinstance(va, str) and isinstance(vb, dict):
            out.append(f"{k} (translations)")
        else:
            out.append(k)
    return out


def summarize(old: dict | None, new: dict) -> str:
    """A short, human summary of what changed between two briefs (shown in the History panel)."""
    if old is None:
        return f"Created ({len(new.get('scenes', []))} scenes)"
    parts = []
    for k, label in TOP_LABELS.items():
        if old.get(k) != new.get(k):
            if k in ("title", "product", "template", "language", "format") and isinstance(new.get(k), str):
                a, b = old.get(k), new.get(k)
                parts.append(f"{label}: {a or '—'} → {b}" if len(f'{a}{b}') < 50 else label)
            elif k == "transition":
                parts.append(f"transition: {_txt(old.get(k)) or 'default'} → {_txt(new.get(k)) or 'default'}")
            else:
                parts.append(label)
    os_ = {s.get("id"): s for s in old.get("scenes", [])}
    ns_ = {s.get("id"): s for s in new.get("scenes", [])}
    added = [i for i in ns_ if i not in os_]
    removed = [i for i in os_ if i not in ns_]
    if added:
        parts.append("added scene " + ", ".join(f"“{i}”" for i in added[:3]) + ("…" if len(added) > 3 else ""))
    if removed:
        parts.append("deleted scene " + ", ".join(f"“{i}”" for i in removed[:3]) + ("…" if len(removed) > 3 else ""))
    common_old = [i for i in os_ if i in ns_]
    common_new = [i for i in ns_ if i in os_]
    if common_old != common_new:
        parts.append("reordered scenes")
    changed = []
    for i in common_new:
        f = _fields(os_[i], ns_[i])
        if f:
            changed.append(f"“{i}”: {', '.join(f[:4])}{'…' if len(f) > 4 else ''}")
    if changed:
        parts.append("scene " + "; ".join(changed[:3]) + (f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""))
    return " · ".join(parts) if parts else "No changes"
