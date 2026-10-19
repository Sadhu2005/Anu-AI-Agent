#!/usr/bin/env python3
"""ANU Research v0.1 (V0.8.5) — approval-gated read-only HTTPS fetch/search.

```text
fetch  — GET an allow-listed HTTPS URL
search — DuckDuckGo Instant Answer API (allow-listed host)
```

Findings persist to the ``research`` table. No browser automation.
Private / link-local destinations are blocked.
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
import re
import socket
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
    BASE / "config" / "anu-research.yaml",
    _REPO / "config" / "anu-research.yaml",
)

_TAG_RE = re.compile(r"<[^>]+>")


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
            root = data.get("research") if isinstance(data, dict) else None
            if not isinstance(root, dict):
                raise ValueError(f"invalid research config: {candidate}")
            return root, candidate
    raise FileNotFoundError("anu-research.yaml not found")


def open_db(db_path=None):
    path = Path(db_path) if db_path is not None else DB_FILE
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, cfg: dict) -> urllib.parse.ParseResult:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    parsed = urllib.parse.urlparse(url.strip())
    if cfg.get("https_only", True) and parsed.scheme != "https":
        raise PermissionError("only https URLs are allowed")
    if parsed.scheme not in ("https", "http"):
        raise PermissionError(f"unsupported URL scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("url missing host")
    allow = {h.lower() for h in (cfg.get("allow_hosts") or [])}
    if host not in allow:
        raise PermissionError(f"host not allow-listed: {host}")
    if cfg.get("block_private_networks", True):
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise PermissionError(f"DNS resolve failed: {host}") from exc
        for info in infos:
            addr = info[4][0]
            if _is_blocked_ip(addr):
                raise PermissionError(
                    f"blocked private/link-local destination: {host} -> {addr}"
                )
    return parsed


def http_get(url: str, cfg: dict) -> dict:
    validate_url(url, cfg)
    timeout = float(cfg.get("timeout_seconds") or 30)
    max_bytes = int(cfg.get("max_bytes") or 262144)
    headers = {
        "User-Agent": cfg.get("user_agent") or "ANU-Research/0.1",
        "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"unreachable: {exc.reason}") from exc

    truncated = len(raw) > max_bytes
    body = raw[:max_bytes].decode("utf-8", errors="replace")
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    if m:
        title = _strip_html(m.group(1))[:200]
    summary = _strip_html(body)[:2000]
    return {
        "ok": True,
        "url": url,
        "http_status": status,
        "content_type": ctype,
        "title": title,
        "summary": summary,
        "truncated": truncated,
        "bytes": min(len(raw), max_bytes),
    }


def search_duckduckgo(query: str, cfg: dict) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    search_cfg = cfg.get("search") or {}
    endpoint = (
        search_cfg.get("endpoint")
        or "https://api.duckduckgo.com/"
    )
    params = urllib.parse.urlencode(
        {
            "q": query.strip(),
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
    )
    url = f"{endpoint}?{params}"
    validate_url(url, cfg)
    timeout = float(cfg.get("timeout_seconds") or 30)
    max_bytes = int(cfg.get("max_bytes") or 262144)
    headers = {
        "User-Agent": cfg.get("user_agent") or "ANU-Research/0.1",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes).decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"unreachable: {exc.reason}") from exc

    payload = json.loads(raw)
    heading = payload.get("Heading") or query.strip()
    abstract = (payload.get("AbstractText") or "").strip()
    abstract_url = payload.get("AbstractURL") or url
    related = []
    for item in payload.get("RelatedTopics") or []:
        if isinstance(item, dict) and item.get("Text"):
            related.append(item["Text"][:200])
        elif isinstance(item, dict) and isinstance(item.get("Topics"), list):
            for sub in item["Topics"][:3]:
                if isinstance(sub, dict) and sub.get("Text"):
                    related.append(sub["Text"][:200])
        if len(related) >= 5:
            break
    parts = []
    if abstract:
        parts.append(abstract)
    if related:
        parts.append("Related: " + " | ".join(related[:5]))
    summary = (
        " ".join(parts)[:2000]
        or f"No abstract for query: {query.strip()}"
    )
    return {
        "ok": True,
        "url": abstract_url,
        "http_status": status,
        "content_type": "application/json",
        "title": heading[:200],
        "summary": summary,
        "truncated": False,
        "bytes": len(raw.encode("utf-8")),
        "query": query.strip(),
        "provider": search_cfg.get("provider") or "duckduckgo_instant",
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


def run_fetch(cfg, *, url, db=None, task_id=None):
    if not cfg.get("enabled", True):
        raise RuntimeError("research is disabled in config")
    result = http_get(url, cfg)
    research_id = None
    if db is not None:
        research_id = persist_research(
            db,
            task_id=task_id,
            url=result["url"],
            title=result.get("title"),
            summary=result.get("summary"),
        )
    result["research_id"] = research_id
    result["action"] = "fetch"
    return result


def run_search(cfg, *, query, db=None, task_id=None):
    if not cfg.get("enabled", True):
        raise RuntimeError("research is disabled in config")
    provider = ((cfg.get("search") or {}).get("provider") or "").strip()
    if provider != "duckduckgo_instant":
        raise RuntimeError(f"unsupported search provider: {provider!r}")
    result = search_duckduckgo(query, cfg)
    research_id = None
    if db is not None:
        research_id = persist_research(
            db,
            task_id=task_id,
            url=result["url"],
            title=result.get("title"),
            summary=result.get("summary"),
        )
    result["research_id"] = research_id
    result["action"] = "search"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Research v0.1")
    parser.add_argument("--config", default=None)
    parser.add_argument("--db", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch_p = sub.add_parser("fetch")
    fetch_p.add_argument("--url", required=True)
    fetch_p.add_argument("--task-id", type=int, default=None)
    fetch_p.add_argument("--persist", action="store_true")

    search_p = sub.add_parser("search")
    search_p.add_argument("--query", required=True)
    search_p.add_argument("--task-id", type=int, default=None)
    search_p.add_argument("--persist", action="store_true")

    args = parser.parse_args(argv)
    cfg, cfg_path = load_config(args.config)
    db = None
    if args.persist or args.db:
        db = open_db(args.db)
    try:
        if args.cmd == "fetch":
            out = run_fetch(
                cfg, url=args.url, db=db, task_id=args.task_id
            )
        else:
            out = run_search(
                cfg, query=args.query, db=db, task_id=args.task_id
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
