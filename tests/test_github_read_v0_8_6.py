#!/usr/bin/env python3
"""V0.8.6: read-only GitHub API with owner allow-list + persist."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-github-v0.1.py"
CONFIG = REPO / "config" / "anu-github.yaml"


def load():
    spec = importlib.util.spec_from_file_location("github_v086", MOD)
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute("INSERT INTO tasks (title) VALUES ('github-smoke')")
    db.commit()
    return db, db_path


class FakeResp:
    def __init__(self, body: bytes, status=200):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_blocks_non_allowlisted_owner():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    try:
        m._require_owner("octocat", cfg)
        raise AssertionError("non-allowlisted owner should deny")
    except PermissionError as exc:
        assert "allow-listed" in str(exc)
    print("PASS blocks non-allowlisted owner")


def test_repo_persists_with_mock():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    db, _ = make_db()
    payload = {
        "full_name": "Sadhu2005/Anu-AI-Agent",
        "description": "ANU agent",
        "default_branch": "main",
        "private": False,
        "html_url": "https://github.com/Sadhu2005/Anu-AI-Agent",
        "stargazers_count": 1,
        "open_issues_count": 0,
        "language": "Python",
    }
    body = json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        assert "api.github.com" in req.full_url
        assert "/repos/Sadhu2005/Anu-AI-Agent" in req.full_url
        return FakeResp(body)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        out = m.run_repo(
            cfg,
            owner="Sadhu2005",
            repo="Anu-AI-Agent",
            db=db,
            task_id=1,
        )
    assert out["ok"] is True
    assert out["research_id"] == 1
    assert out["data"]["full_name"] == "Sadhu2005/Anu-AI-Agent"
    row = db.execute("SELECT title, url FROM research WHERE id=1").fetchone()
    assert "Anu-AI-Agent" in row["title"]
    print("PASS repo persists with mock")


def test_contents_file_preview():
    m = load()
    cfg, _ = m.load_config(CONFIG)
    import base64

    text = "# ANU\nhello"
    payload = {
        "type": "file",
        "path": "README.md",
        "sha": "abc",
        "size": len(text),
        "html_url": "https://github.com/Sadhu2005/Anu-AI-Agent/blob/main/README.md",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }

    def fake_urlopen(req, timeout=None):
        return FakeResp(json.dumps(payload).encode("utf-8"))

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        out = m.run_contents(
            cfg,
            owner="Sadhu2005",
            repo="Anu-AI-Agent",
            path="README.md",
            db=None,
        )
    assert out["ok"] is True
    assert "hello" in (out.get("content_preview") or "")
    print("PASS contents file preview")


def main():
    test_blocks_non_allowlisted_owner()
    test_repo_persists_with_mock()
    test_contents_file_preview()
    print("ALL_PASS test_github_read_v0_8_6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
