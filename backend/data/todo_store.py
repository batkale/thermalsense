"""
The team's shared to-do list: what is done, what is to do, what is to research.

It is read by the page at /to-do-list (frontend/public/to-do-list.html), which
is linked from nowhere and marked noindex, so the audience of the site never
sees it. Anyone with the address can read it; changing it takes the admin key,
the same X-Admin-Token that guards /train and /seed.

The list is one JSON file on the data volume (config.TODO_PATH), so edits made
on the page survive every deploy. While that file does not exist the list is
the repo's todo_seed.json, at version 0; the first save writes the file, and
from then on the seed is never read again. Change the list on the page, not
in the seed.

Every save names the version it was based on. If the file has moved on since
(two people editing at once), the save is refused with 409 and the page
reloads, so one person's change never silently erases the other's. The
version a save replaces is kept in todo_history/ beside the file, the newest
HISTORY_KEEP of them, which is the undo for a wrong delete.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

import config

SEED_PATH = Path(__file__).resolve().parent.parent / "todo_seed.json"
SECTIONS = ("todo", "research", "done")
MAX_ITEMS = 500
HISTORY_KEEP = 30
_LIMITS = {"id": 40, "title": 300, "note": 4000, "who": 60, "added": 40, "done_at": 40}
_OPTIONAL = ("note", "who", "added", "done_at")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}

_lock = threading.Lock()


class Conflict(Exception):
    """The list changed since the version the save was based on."""

    def __init__(self, current: int):
        super().__init__(f"the list is at version {current}")
        self.current = current


def clean(items) -> list[dict]:
    """Validated copies of the items, unknown keys dropped. Raises ValueError."""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} items")
    out, seen = [], set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"item {i} is not an object")
        section = it.get("section")
        if section not in SECTIONS:
            raise ValueError(f"item {i}: section must be one of {', '.join(SECTIONS)}")
        title = it.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"item {i}: a title is required")
        title = title.strip()
        if len(title) > _LIMITS["title"]:
            raise ValueError(f"item {i}: title longer than {_LIMITS['title']} characters")
        item_id = it.get("id") or uuid.uuid4().hex[:12]
        if not isinstance(item_id, str) or len(item_id) > _LIMITS["id"] or not _ID_RE.match(item_id):
            raise ValueError(f"item {i}: id must be letters, digits, '-' or '_'")
        if item_id in seen:
            raise ValueError(f"item {i}: id {item_id!r} is used twice")
        seen.add(item_id)
        row = {"id": item_id, "section": section, "title": title}
        for key in _OPTIONAL:
            value = it.get(key)
            if value is None or value == "":
                continue
            if not isinstance(value, str):
                raise ValueError(f"item {i}: {key} must be text")
            value = value.strip()
            if len(value) > _LIMITS[key]:
                raise ValueError(f"item {i}: {key} longer than {_LIMITS[key]} characters")
            if value:
                row[key] = value
        out.append(row)
    return out


def _path() -> Path:
    return Path(config.TODO_PATH)


def load() -> dict:
    """The current list: the saved file, or the repo's seed as version 0."""
    path = _path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return {"version": 0, "updated_at": seed.get("updated_at"), "items": clean(seed["items"])}


def _keep_history(path: Path, version: int) -> None:
    hist = path.parent / "todo_history"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"todo-v{version:06d}.json").write_bytes(path.read_bytes())
    old = sorted(hist.glob("todo-v*.json"))
    for stale in old[:-HISTORY_KEEP]:
        stale.unlink(missing_ok=True)


def save(items, version) -> dict:
    """Replace the list, if it is still at `version`. Raises ValueError or Conflict."""
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("version must be the integer the list was loaded at")
    items = clean(items)
    with _lock:
        current = load()
        if version != current["version"]:
            raise Conflict(current["version"])
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            _keep_history(path, current["version"])
        new = {
            "version": current["version"] + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": items,
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return new


def make_router(require_admin) -> APIRouter:
    """The /todo endpoints; writes go through the app's own admin check."""
    router = APIRouter()

    @router.get("/todo")
    def read_todo():
        return JSONResponse(load(), headers=_NO_STORE)

    @router.get("/todo/auth", dependencies=[Depends(require_admin)])
    def check_key():
        """Lets the page test a key before it offers editing."""
        return JSONResponse({"ok": True}, headers=_NO_STORE)

    @router.put("/todo", dependencies=[Depends(require_admin)])
    def write_todo(body: dict = Body(...)):
        try:
            saved = save(body.get("items"), body.get("version"))
        except Conflict as e:
            raise HTTPException(status_code=409, detail=f"changed meanwhile: {e}")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return JSONResponse(saved, headers=_NO_STORE)

    return router
