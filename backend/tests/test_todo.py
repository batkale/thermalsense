"""
The shared to-do list behind /to-do-list (data/todo_store.py).

What has to hold: it starts as the repo's seed, a save needs the admin key, two
people editing at once cannot erase each other's change, a wrong delete can be
undone from history, and the file lives where config.TODO_PATH says — which
conftest points at tmp_path, so no test touches the team's real list.
"""

import json

import pytest
from fastapi.testclient import TestClient

import config
import main
from data import todo_store

KEY = "test-admin-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", KEY)
    return TestClient(main.app)


def _put(client, version, items, key=KEY):
    headers = {"X-Admin-Token": key} if key else {}
    return client.put("/todo", json={"version": version, "items": items}, headers=headers)


def test_the_seed_is_a_valid_list_with_all_three_sections():
    seed = json.loads(todo_store.SEED_PATH.read_text(encoding="utf-8"))
    items = todo_store.clean(seed["items"])
    assert len(items) == len(seed["items"])
    assert {it["section"] for it in items} == set(todo_store.SECTIONS)
    assert len({it["id"] for it in items}) == len(items)


def test_before_any_save_the_list_is_the_seed_at_version_zero(client):
    res = client.get("/todo")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == 0
    seed = json.loads(todo_store.SEED_PATH.read_text(encoding="utf-8"))
    assert [it["id"] for it in body["items"]] == [it["id"] for it in seed["items"]]
    assert res.headers["cache-control"] == "no-store"
    assert "noindex" in res.headers["x-robots-tag"]
    assert not config.TODO_PATH.exists()                  # reading never writes


def test_a_save_needs_the_admin_key(client):
    items = client.get("/todo").json()["items"]
    assert _put(client, 0, items, key=None).status_code == 401
    assert _put(client, 0, items, key="wrong").status_code == 401
    assert client.get("/todo/auth", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/todo/auth", headers={"X-Admin-Token": KEY}).status_code == 200
    assert not config.TODO_PATH.exists()


def test_a_save_bumps_the_version_and_persists(client):
    items = client.get("/todo").json()["items"]
    items[0] = {**items[0], "section": "done", "done_at": "2026-11-21"}
    res = _put(client, 0, items)
    assert res.status_code == 200
    assert res.json()["version"] == 1
    on_disk = json.loads(config.TODO_PATH.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1 and on_disk["items"][0]["section"] == "done"
    assert client.get("/todo").json()["items"][0]["done_at"] == "2026-11-21"


def test_a_save_based_on_an_old_version_is_refused(client):
    items = client.get("/todo").json()["items"]
    assert _put(client, 0, items[:3]).status_code == 200            # one person saves
    res = _put(client, 0, items[:5])                                # the other, from version 0
    assert res.status_code == 409
    assert len(client.get("/todo").json()["items"]) == 3            # the first save stands


def test_bad_items_are_refused_and_nothing_is_written(client):
    good = {"id": "x1", "section": "todo", "title": "a task"}
    for bad in ([{**good, "section": "someday"}],
                [{**good, "title": "   "}],
                [good, {**good}],                                   # the same id twice
                [{**good, "id": "no spaces allowed"}],
                [{**good, "note": "n" * 5000}],
                "not a list"):
        assert _put(client, 0, bad).status_code == 422
    assert _put(client, "0", [good]).status_code == 422               # version must be an int
    assert not config.TODO_PATH.exists()


def test_unknown_keys_are_dropped_and_missing_ids_are_given(client):
    res = _put(client, 0, [{"section": "research", "title": " a question ", "colour": "red"}])
    item = res.json()["items"][0]
    assert item["title"] == "a question"
    assert "colour" not in item
    assert item["id"]


def test_every_replaced_version_is_kept_and_history_is_bounded(client, monkeypatch):
    monkeypatch.setattr(todo_store, "HISTORY_KEEP", 3)
    item = {"id": "a", "section": "todo", "title": "v"}
    for v in range(6):
        assert _put(client, v, [{**item, "title": f"v{v}"}]).status_code == 200
    hist = sorted((config.TODO_PATH.parent / "todo_history").glob("todo-v*.json"))
    assert [p.name for p in hist] == ["todo-v000003.json", "todo-v000004.json", "todo-v000005.json"]
    assert json.loads(hist[-1].read_text(encoding="utf-8"))["items"][0]["title"] == "v4"


def test_the_page_is_not_linked_from_the_public_pages():
    from pathlib import Path
    front = Path(__file__).resolve().parents[2] / "frontend"
    page = (front / "public" / "to-do-list.html").read_text(encoding="utf-8")
    assert 'name="robots" content="noindex, nofollow"' in page
    for public in [front / "index.html", front / "about.html", *(front / "src").rglob("*.js*")]:
        assert "to-do-list" not in public.read_text(encoding="utf-8"), public
