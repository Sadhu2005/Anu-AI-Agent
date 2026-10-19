#!/usr/bin/env python3
"""V0.8.5: read-only research fetch/search with allow-list + persist."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-research-v0.1.py"
CONFIG = REPO / "config" / "anu-research.yaml"


def load():
    spec = importlib.util.spec_from_file_location("research_v085", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "brain" / "anu-brain.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            url TEXT,
            title TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
        """
    )
    db.execute("INSERT INTO tasks (title) VALUES ('research-smoke')")
    db.commit()
    return db, db_path


class FakeResp:
    def __init__(self, body: bytes, status=200, content_type="text/html"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_blocks_non_allowlisted_and_http():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    try:
        m.validate_url("https://evil.example/x", cfg)
        raise AssertionError("non-allowlisted host should deny")
    except PermissionError as exc:
        assert "allow-listed" in str(exc)
    try:
        m.validate_url("http://docs.python.org/3/", cfg)
        raise AssertionError("http should deny when https_only")
    except PermissionError as exc:
        assert "https" in str(exc)
    print("PASS blocks non-allowlisted and http")


def test_fetch_persists_with_mock():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    db, _ = make_db()
    html = b"<html><title>Python Docs</title><body>Hello research</body></html>"

    def fake_urlopen(req, timeout=None):
        assert "docs.python.org" in req.full_url
        return FakeResp(html)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with mock.patch("socket.getaddrinfo", return_value=[
            (None, None, None, None, ("93.184.216.34", 0)),
        ]):
            out = m.run_fetch(
                cfg,
                url="https://docs.python.org/3/",
                db=db,
                task_id=1,
            )
    assert out["ok"] is True
    assert out["title"] == "Python Docs"
    assert "Hello research" in out["summary"]
    assert out["research_id"] == 1
    row = db.execute("SELECT url, title, summary FROM research WHERE id=1").fetchone()
    assert row["url"] == "https://docs.python.org/3/"
    assert row["title"] == "Python Docs"
    print("PASS fetch persists with mock")


def test_search_duckduckgo_mock():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    payload = {
        "Heading": "Raspberry Pi",
        "AbstractText": "A series of small single-board computers.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Raspberry_Pi",
        "RelatedTopics": [{"Text": "Single-board computer"}],
    }
    body = json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        assert "api.duckduckgo.com" in req.full_url
        return FakeResp(body, content_type="application/json")

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with mock.patch("socket.getaddrinfo", return_value=[
            (None, None, None, None, ("40.89.244.232", 0)),
        ]):
            out = m.run_search(cfg, query="Raspberry Pi", db=None, task_id=None)
    assert out["ok"] is True
    assert out["title"] == "Raspberry Pi"
    assert "single-board" in out["summary"].lower()
    assert out["query"] == "Raspberry Pi"
    print("PASS search duckduckgo mock")


def test_private_ip_blocked():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    with mock.patch("socket.getaddrinfo", return_value=[
        (None, None, None, None, ("127.0.0.1", 0)),
    ]):
        try:
            m.validate_url("https://docs.python.org/3/", cfg)
            raise AssertionError("loopback should block")
        except PermissionError as exc:
            assert "blocked" in str(exc)
    print("PASS private ip blocked")


def main():
    test_blocks_non_allowlisted_and_http()
    test_fetch_persists_with_mock()
    test_search_duckduckgo_mock()
    test_private_ip_blocked()
    print("ALL_PASS test_research_fetch_v0_8_5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
