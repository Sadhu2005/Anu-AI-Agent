#!/usr/bin/env python3
"""ANU GitHub v0.1 (V0.8.6) — approval-gated read-only GitHub API.

```text
repo      — GET /repos/{owner}/{repo}
contents  — GET /repos/{owner}/{repo}/contents/{path}
issue     — GET /repos/{owner}/{repo}/issues/{number}
```

Findings persist to the ``research`` table. No write / push / PR create.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
CONFIG_CANDIDATES = (
    BASE / "config" / "anu-github.yaml",
    _REPO / "config" / "anu-github.yaml",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_config(path=None):
    if path is not None:
        candidates = [Path(path)]
    else:
        candidates = list(CONFIG_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            root = data.get("github") if isinstance(data, dict) else None
            if not isinstance(root, dict):
                raise ValueError(f"invalid github config: {candidate}")
            return root, candidate
    raise FileNotFoundError("anu-github.yaml not found")


def open_db(db_path=None):
    path = Path(db_path) if db_path is not None else DB_FILE
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def resolve_token(cfg: dict) -> str:
    file_env = str(cfg.get("token_file_env") or "ANU_GITHUB_TOKEN_FILE")
    key_file = os.environ.get(file_env)
    if key_file:
        path = Path(key_file)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    env_name = str(cfg.get("token_env") or "ANU_GITHUB_TOKEN")
    return (os.environ.get(env_name) or "").strip()


def _require_owner(owner: str, cfg: dict) -> str:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a non-empty string")
    owner = owner.strip()
    allow = {o.lower() for o in (cfg.get("allow_owners") or [])}
    if owner.lower() not in allow:
        raise PermissionError(f"owner not allow-listed: {owner}")
    return owner


def _require_repo(repo: str) -> str:
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo must be a non-empty string")
    repo = repo.strip()
    if "/" in repo or repo.startswith("."):
        raise ValueError("invalid repo name")
    return repo


def api_get(cfg: dict, path: str) -> dict:
    if not cfg.get("enabled", True):
        raise RuntimeError("github is disabled in config")
    base = (cfg.get("api_base") or "https://api.github.com").rstrip("/")
    if cfg.get("https_only", True) and not base.startswith("https://"):
        raise PermissionError("only https api_base is allowed")
    host = urllib.parse.urlparse(base).hostname or ""
    allow_hosts = {h.lower() for h in (cfg.get("allow_hosts") or [])}
    if host.lower() not in allow_hosts:
        raise PermissionError(f"host not allow-listed: {host}")

    url = f"{base}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": cfg.get("user_agent") or "ANU-GitHub/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = resolve_token(cfg)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = float(cfg.get("timeout_seconds") or 30)
    max_bytes = int(cfg.get("max_bytes") or 262144)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub HTTP {exc.code}: {detail[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"unreachable: {exc.reason}") from exc

    truncated = len(raw) > max_bytes
    body = raw[:max_bytes].decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON GitHub response: {body[:120]}") from exc
    return {
        "ok": True,
        "url": url,
        "http_status": status,
        "truncated": truncated,
        "bytes": min(len(raw), max_bytes),
        "payload": payload,
        "token_used": bool(token),
    }


def persist_research(db, *, task_id, url, title, summary):
    cur = db.execute(
        """
        INSERT INTO research (task_id, url, title, summary, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, url, title, summary, now_iso()),
    )
    db.commit()
    return int(cur.lastrowid)


def run_repo(cfg, *, owner, repo, db=None, task_id=None):
    owner = _require_owner(owner, cfg)
    repo = _require_repo(repo)
    path = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
    raw = api_get(cfg, path)
    payload = raw["payload"]
    title = f"{owner}/{repo}"
    summary = json.dumps(
        {
            "full_name": payload.get("full_name"),
            "description": payload.get("description"),
            "default_branch": payload.get("default_branch"),
            "private": payload.get("private"),
            "html_url": payload.get("html_url"),
            "stargazers_count": payload.get("stargazers_count"),
            "open_issues_count": payload.get("open_issues_count"),
            "language": payload.get("language"),
        },
        sort_keys=True,
    )
    research_id = None
    if db is not None:
        research_id = persist_research(
            db,
            task_id=task_id,
            url=payload.get("html_url") or raw["url"],
            title=title,
            summary=summary[:2000],
        )
    return {
        "ok": True,
        "action": "repo",
        "owner": owner,
        "repo": repo,
        "title": title,
        "summary": summary[:2000],
        "url": payload.get("html_url") or raw["url"],
        "http_status": raw["http_status"],
        "research_id": research_id,
        "token_used": raw["token_used"],
        "data": json.loads(summary),
    }


def run_contents(cfg, *, owner, repo, path, db=None, task_id=None):
    owner = _require_owner(owner, cfg)
    repo = _require_repo(repo)
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    rel = path.strip().lstrip("/")
    if ".." in rel.split("/"):
        raise PermissionError("path must not contain ..")
    api_path = (
        f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        f"/contents/{urllib.parse.quote(rel)}"
    )
    raw = api_get(cfg, api_path)
    payload = raw["payload"]
    if isinstance(payload, list):
        names = [item.get("name") for item in payload if isinstance(item, dict)]
        title = f"{owner}/{repo}:{rel}/"
        summary = json.dumps({"type": "dir", "entries": names[:50]}, sort_keys=True)
        html_url = f"https://github.com/{owner}/{repo}/tree/HEAD/{rel}"
        content_preview = None
    elif isinstance(payload, dict):
        title = f"{owner}/{repo}:{payload.get('path') or rel}"
        content_preview = None
        if payload.get("encoding") == "base64" and payload.get("content"):
            try:
                decoded = base64.b64decode(payload["content"]).decode(
                    "utf-8", errors="replace"
                )
                content_preview = decoded[:1500]
            except Exception:
                content_preview = None
        summary = json.dumps(
            {
                "type": payload.get("type"),
                "path": payload.get("path"),
                "sha": payload.get("sha"),
                "size": payload.get("size"),
                "html_url": payload.get("html_url"),
                "content_preview": content_preview,
            },
            sort_keys=True,
        )
        html_url = payload.get("html_url") or raw["url"]
    else:
        raise RuntimeError("unexpected contents payload")

    research_id = None
    if db is not None:
        research_id = persist_research(
            db,
            task_id=task_id,
            url=html_url,
            title=title,
            summary=summary[:2000],
        )
    return {
        "ok": True,
        "action": "contents",
        "owner": owner,
        "repo": repo,
        "path": rel,
        "title": title,
        "summary": summary[:2000],
        "url": html_url,
        "http_status": raw["http_status"],
        "research_id": research_id,
        "token_used": raw["token_used"],
        "content_preview": content_preview,
    }


def run_issue(cfg, *, owner, repo, number, db=None, task_id=None):
    owner = _require_owner(owner, cfg)
    repo = _require_repo(repo)
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("number must be a positive integer")
    api_path = (
        f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        f"/issues/{int(number)}"
    )
    raw = api_get(cfg, api_path)
    payload = raw["payload"]
    title = f"{owner}/{repo}#{number}: {payload.get('title') or ''}".strip()
    body = (payload.get("body") or "")[:1200]
    summary = json.dumps(
        {
            "number": payload.get("number"),
            "state": payload.get("state"),
            "title": payload.get("title"),
            "html_url": payload.get("html_url"),
            "user": (payload.get("user") or {}).get("login"),
            "body_preview": body,
        },
        sort_keys=True,
    )
    research_id = None
    if db is not None:
        research_id = persist_research(
            db,
            task_id=task_id,
            url=payload.get("html_url") or raw["url"],
            title=title[:200],
            summary=summary[:2000],
        )
    return {
        "ok": True,
        "action": "issue",
        "owner": owner,
        "repo": repo,
        "number": number,
        "title": title[:200],
        "summary": summary[:2000],
        "url": payload.get("html_url") or raw["url"],
        "http_status": raw["http_status"],
        "research_id": research_id,
        "token_used": raw["token_used"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU GitHub v0.1 (read-only)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--persist", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--owner", required=True)
    common.add_argument("--repo", required=True)
    common.add_argument("--task-id", type=int, default=None)

    sub.add_parser("repo", parents=[common])

    contents_p = sub.add_parser("contents", parents=[common])
    contents_p.add_argument("--path", required=True)

    issue_p = sub.add_parser("issue", parents=[common])
    issue_p.add_argument("--number", type=int, required=True)

    args = parser.parse_args(argv)
    cfg, cfg_path = load_config(args.config)
    db = None
    if args.persist or args.db:
        db = open_db(args.db)
    try:
        if args.cmd == "repo":
            out = run_repo(
                cfg,
                owner=args.owner,
                repo=args.repo,
                db=db,
                task_id=args.task_id,
            )
        elif args.cmd == "contents":
            out = run_contents(
                cfg,
                owner=args.owner,
                repo=args.repo,
                path=args.path,
                db=db,
                task_id=args.task_id,
            )
        else:
            out = run_issue(
                cfg,
                owner=args.owner,
                repo=args.repo,
                number=args.number,
                db=db,
                task_id=args.task_id,
            )
        out["config"] = str(cfg_path)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("ok") else 1
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "action": args.cmd},
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
