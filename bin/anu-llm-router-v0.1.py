#!/usr/bin/env python3
"""ANU LLM Router v0.1 (V0.9.2) — multi-provider failover with shared brain memory.

Memory lives in ``llm_context``; switching providers never drops goal state.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
DEFAULT_CONFIG = BASE / "config" / "anu-llm-providers.yaml"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _module_path(name):
    for candidate in (BASE / "bin" / name, _HERE / name, _REPO / "bin" / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def _load(name, module_name):
    path = _module_path(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML required for anu-llm-providers.yaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def load_config(path: Path | None = None) -> dict:
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    if os.environ.get("ANU_LLM_PROVIDERS_CONFIG"):
        candidates.append(Path(os.environ["ANU_LLM_PROVIDERS_CONFIG"]))
    candidates.extend([
        BASE / "config" / "anu-llm-providers.yaml",
        _REPO / "config" / "anu-llm-providers.yaml",
    ])
    chosen = next((c for c in candidates if c.is_file()), None)
    if chosen is None:
        return {
            "enabled": False,
            "providers": [],
            "defaults": {},
            "privacy": {"forbidden_classes": ["SECRET"]},
            "cooldowns": {},
        }
    raw = _load_yaml(chosen)
    raw["_config_path"] = str(chosen)
    return raw


def db_connect(db_path=None):
    state = _load("anu-autonomy-state-v0.1.py", "anu_state_llm")
    db = state.db_connect(db_path if db_path is not None else DB_FILE)
    state.migrate(db)
    return db


def read_api_key(provider: dict) -> str | None:
    file_env = provider.get("api_key_file_env")
    if file_env and os.environ.get(file_env):
        path = Path(os.environ[file_env])
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
    env_name = provider.get("api_key_env") or "ANU_MODEL_API_KEY"
    val = os.environ.get(env_name)
    if val:
        return val.strip()
    # Optional bare file next to credentials (never logged).
    return None


def redact_text(text: str, patterns=None) -> str:
    cfg = load_config()
    pats = patterns or (cfg.get("privacy") or {}).get("redact_patterns") or []
    out = text or ""
    for pat in pats:
        try:
            out = re.sub(pat, "[REDACTED]", out)
        except re.error:
            continue
    return out


def privacy_allows(privacy_class: str, provider: dict, config=None) -> bool:
    cfg = config or load_config()
    privacy = cfg.get("privacy") or {}
    forbidden = set(privacy.get("forbidden_classes") or ["SECRET"])
    if privacy_class in forbidden and not provider.get("local"):
        return False
    return True


def classify_error(exc_or_code, body=None) -> str:
    if isinstance(exc_or_code, urllib.error.HTTPError):
        code = exc_or_code.code
        body = body or ""
        try:
            body = body or exc_or_code.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        low = (body or "").lower()
        if code == 429:
            return "rate_limit"
        if code in (401, 403):
            return "auth"
        if code == 402 or "quota" in low or "billing" in low:
            return "quota"
        if code >= 500:
            return "server_error"
        return "server_error"
    if isinstance(exc_or_code, urllib.error.URLError):
        return "connection"
    if isinstance(exc_or_code, TimeoutError):
        return "timeout"
    if isinstance(exc_or_code, int):
        if exc_or_code == 429:
            return "rate_limit"
        if exc_or_code in (401, 403):
            return "auth"
        if exc_or_code >= 500:
            return "server_error"
    msg = str(exc_or_code).lower()
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "refused" in msg or "name or service" in msg:
        return "connection"
    return "server_error"


def _cooldown_seconds(config, error_class, retry_after=None):
    if retry_after is not None:
        try:
            return max(1, int(retry_after))
        except (TypeError, ValueError):
            pass
    return int((config.get("cooldowns") or {}).get(error_class, 30))


def get_provider_health(db, name):
    row = db.execute(
        "SELECT * FROM provider_health WHERE provider = ?", (name,)
    ).fetchone()
    if row is None:
        return {
            "provider": name,
            "state": "unknown",
            "cooldown_until": None,
            "last_error": None,
            "error_class": None,
            "success_count": 0,
            "failure_count": 0,
        }
    return dict(row) if isinstance(row, sqlite3.Row) else {
        "provider": row[0],
        "state": row[1],
        "cooldown_until": row[2],
        "last_error": row[3],
        "error_class": row[4],
        "success_count": row[5],
        "failure_count": row[6],
    }


def set_provider_health(db, name, *, state, error=None, error_class=None, cooldown_until=None, success=False):
    now = now_iso()
    existing = get_provider_health(db, name)
    success_count = int(existing.get("success_count") or 0) + (1 if success else 0)
    failure_count = int(existing.get("failure_count") or 0) + (0 if success else 1)
    db.execute(
        """INSERT INTO provider_health
           (provider, state, cooldown_until, last_error, error_class,
            success_count, failure_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(provider) DO UPDATE SET
             state=excluded.state,
             cooldown_until=excluded.cooldown_until,
             last_error=excluded.last_error,
             error_class=excluded.error_class,
             success_count=excluded.success_count,
             failure_count=excluded.failure_count,
             updated_at=excluded.updated_at""",
        (
            name, state, cooldown_until,
            (str(error)[:400] if error else None),
            error_class, success_count, failure_count, now,
        ),
    )
    db.commit()


def provider_available(db, provider, config=None):
    if not provider.get("enabled", True):
        return False, "disabled"
    health = get_provider_health(db, provider["name"])
    until = health.get("cooldown_until")
    if until:
        try:
            ts = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < ts:
                return False, f"cooldown_until:{until}"
        except ValueError:
            pass
    # Daily limits
    usage = get_usage(db, provider["name"])
    if int(usage.get("requests") or 0) >= int(provider.get("daily_requests") or 10**9):
        return False, "daily_requests"
    if int(usage.get("tokens") or 0) >= int(provider.get("daily_tokens") or 10**12):
        return False, "daily_tokens"
    max_usd = float(provider.get("max_usd_per_day") or 0)
    if max_usd > 0 and float(usage.get("usd") or 0) >= max_usd:
        return False, "daily_usd"
    return True, "ok"


def get_usage(db, name, day=None):
    day = day or today_utc()
    row = db.execute(
        "SELECT provider, day, requests, tokens, usd FROM provider_usage WHERE provider=? AND day=?",
        (name, day),
    ).fetchone()
    if row is None:
        return {"provider": name, "day": day, "requests": 0, "tokens": 0, "usd": 0.0}
    return dict(row) if isinstance(row, sqlite3.Row) else {
        "provider": row[0], "day": row[1], "requests": row[2], "tokens": row[3], "usd": row[4],
    }


def bump_usage(db, name, *, tokens=0, usd=0.0):
    day = today_utc()
    db.execute(
        """INSERT INTO provider_usage(provider, day, requests, tokens, usd)
           VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(provider, day) DO UPDATE SET
             requests = requests + 1,
             tokens = tokens + excluded.tokens,
             usd = usd + excluded.usd""",
        (name, day, int(tokens), float(usd)),
    )
    db.commit()


def store_context(db, *, goal_id, task_id, role, content, summary=None, provider=None):
    db.execute(
        """INSERT INTO llm_context(goal_id, task_id, role, content, summary, provider, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (goal_id, task_id, role, content, summary, provider, now_iso()),
    )
    db.commit()


def list_context(db, goal_id, *, limit=20):
    rows = db.execute(
        """SELECT id, goal_id, task_id, role, content, summary, provider, created_at
           FROM llm_context WHERE goal_id = ? ORDER BY id DESC LIMIT ?""",
        (int(goal_id), int(limit)),
    ).fetchall()
    out = []
    for row in reversed(list(rows)):
        out.append(dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "goal_id": row[1], "task_id": row[2], "role": row[3],
            "content": row[4], "summary": row[5], "provider": row[6], "created_at": row[7],
        })
    return out


def update_goal_summary(db, goal_id, summary):
    store_context(
        db, goal_id=goal_id, task_id=None, role="summary",
        content=summary, summary=summary, provider="router",
    )


def build_messages(
    db,
    *,
    goal_id,
    user_prompt,
    system_prompt=None,
    task_id=None,
    privacy_class="INTERNAL",
    context_chars=24000,
    for_remote=False,
):
    """Provider-agnostic chat messages rebuilt from brain memory."""
    messages = []
    sys = system_prompt or (
        "You are ANU, an autonomous software engineer. "
        "Use the objective, checklist, and history. Do not invent secrets."
    )
    if for_remote:
        sys = redact_text(sys)
    messages.append({"role": "system", "content": sys})

    try:
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_llm2")
        obj = state.get_objective(db, goal_id) if goal_id else None
    except Exception:
        obj = None
    if obj:
        block = [
            f"Objective: {obj.get('objective') or obj.get('description')}",
            f"Criteria: {json.dumps(obj.get('acceptance_criteria') or [])}",
        ]
        for c in obj.get("checklist") or []:
            block.append(f"- [{c.get('status')}] {c.get('item')}")
        content = "\n".join(block)
        if for_remote:
            content = redact_text(content)
        messages.append({"role": "system", "content": content})

    history = list_context(db, goal_id, limit=30) if goal_id else []
    # Prefer latest summary
    summaries = [h for h in history if h.get("role") == "summary"]
    if summaries:
        s = summaries[-1].get("summary") or summaries[-1].get("content") or ""
        if for_remote:
            s = redact_text(s)
        messages.append({"role": "system", "content": f"Rolling summary:\n{s}"})

    budget = int(context_chars)
    used = sum(len(m["content"]) for m in messages)
    hist_msgs = []
    for h in history:
        if h.get("role") == "summary":
            continue
        role = h.get("role") if h.get("role") in ("user", "assistant", "system") else "user"
        content = h.get("content") or ""
        if for_remote:
            content = redact_text(content)
        if used + len(content) > budget:
            break
        hist_msgs.append({"role": role, "content": content})
        used += len(content)
    messages.extend(hist_msgs[-12:])

    prompt = user_prompt or ""
    if for_remote:
        prompt = redact_text(prompt)
    messages.append({"role": "user", "content": prompt})
    return messages


def _http_chat(provider, messages, *, timeout=None) -> dict:
    base = str(provider.get("base_url") or "").rstrip("/")
    url = f"{base}/chat/completions"
    key = read_api_key(provider)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": provider.get("model"),
        "messages": messages,
        "temperature": float(provider.get("temperature", 0.2)),
        "max_tokens": int(provider.get("max_tokens") or 2048),
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    to = timeout or int(provider.get("timeout_seconds") or 120)
    with urllib.request.urlopen(req, timeout=to) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def extract_content(response: dict) -> str:
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def parse_json_content(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not m:
            raise
        return json.loads(m.group(0))


def probe_provider(provider, *, timeout=5) -> dict:
    """Cheap health probe (models list or tiny chat)."""
    base = str(provider.get("base_url") or "").rstrip("/")
    key = read_api_key(provider)
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = f"{base}/models"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status, "provider": provider["name"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "provider": provider["name"]}


def chat(
    db,
    *,
    messages=None,
    goal_id=None,
    task_id=None,
    user_prompt=None,
    system_prompt=None,
    privacy_class="INTERNAL",
    expect_json=False,
    config=None,
):
    """Try providers in order; persist memory independently of which succeeds."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        raise RuntimeError("llm router disabled")

    providers = [p for p in (cfg.get("providers") or []) if isinstance(p, dict)]
    if not providers:
        raise RuntimeError("no providers configured")

    defaults = cfg.get("defaults") or {}
    context_chars = int(defaults.get("context_chars") or 24000)

    last_error = None
    previous = None
    for provider in providers:
        name = provider.get("name") or "unnamed"
        ok, why = provider_available(db, provider, cfg)
        if not ok:
            continue
        if not privacy_allows(privacy_class, provider, cfg):
            last_error = f"privacy_block:{name}"
            continue

        for_remote = not bool(provider.get("local"))
        if messages is None:
            built = build_messages(
                db,
                goal_id=goal_id,
                user_prompt=user_prompt or "",
                system_prompt=system_prompt,
                task_id=task_id,
                privacy_class=privacy_class,
                context_chars=int(provider.get("context_chars") or context_chars),
                for_remote=for_remote,
            )
        else:
            built = messages
            if for_remote:
                built = [
                    {**m, "content": redact_text(m.get("content") or "")}
                    for m in built
                ]

        if previous and previous != name:
            try:
                state = _load("anu-autonomy-state-v0.1.py", "anu_state_llm3")
                state.emit_autonomy_event(
                    db, goal_id, "LLM_PROVIDER_SWITCH",
                    f"switch {previous} -> {name}",
                    {"from": previous, "to": name, "reason": last_error},
                )
            except Exception:
                pass

        try:
            resp = _http_chat(provider, built)
            content = extract_content(resp)
            if expect_json:
                parsed = parse_json_content(content)
            else:
                parsed = None
            # Persist memory (unredacted user_prompt when local memory)
            if goal_id is not None and user_prompt:
                store_context(
                    db, goal_id=goal_id, task_id=task_id, role="user",
                    content=user_prompt, provider=name,
                )
            if goal_id is not None:
                store_context(
                    db, goal_id=goal_id, task_id=task_id, role="assistant",
                    content=content, provider=name,
                )
                # Rolling summary (truncated)
                update_goal_summary(
                    db, goal_id,
                    f"Last provider={name}; response_len={len(content)}; "
                    f"at={now_iso()}",
                )
            usage = resp.get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)
            bump_usage(db, name, tokens=tokens, usd=0.0 if provider.get("zero_cost") else 0.0)
            set_provider_health(db, name, state="healthy", success=True, cooldown_until=None)
            return {
                "ok": True,
                "provider": name,
                "content": content,
                "json": parsed,
                "raw": resp,
            }
        except Exception as exc:
            err_class = classify_error(exc)
            retry_after = None
            if isinstance(exc, urllib.error.HTTPError):
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
            cool = _cooldown_seconds(cfg, err_class, retry_after)
            until = (datetime.now(timezone.utc) + timedelta(seconds=cool)).isoformat()
            set_provider_health(
                db, name, state="unhealthy", error=exc, error_class=err_class,
                cooldown_until=until, success=False,
            )
            last_error = f"{name}:{err_class}:{exc}"
            previous = name
            continue

    # All exhausted → signal pause
    try:
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_llm4")
        if goal_id is not None:
            state.set_control(
                db, "pause", scope="goal", goal_id=goal_id,
                reason="llm_unavailable", by_who="llm_router",
            )
            state.emit_autonomy_event(
                db, goal_id, "AUTONOMY_PAUSE",
                "all LLM providers exhausted",
                {"last_error": last_error},
            )
    except Exception:
        pass
    raise RuntimeError(f"llm_unavailable: {last_error}")


def reset_cooldown(db, provider_name):
    set_provider_health(db, provider_name, state="unknown", cooldown_until=None, success=True)
    return get_provider_health(db, provider_name)


def status(db, config=None):
    cfg = config or load_config()
    out = []
    for provider in cfg.get("providers") or []:
        name = provider.get("name")
        ok, why = provider_available(db, provider, cfg)
        out.append({
            "provider": name,
            "available": ok,
            "why": why,
            "health": get_provider_health(db, name),
            "usage_today": get_usage(db, name),
            "local": bool(provider.get("local")),
            "model": provider.get("model"),
        })
    return {"ok": True, "providers": out}


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU LLM Router v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    t = sub.add_parser("test")
    t.add_argument("--provider", required=True)
    r = sub.add_parser("reset-cooldown")
    r.add_argument("--provider", required=True)
    c = sub.add_parser("chat")
    c.add_argument("--goal-id", type=int, default=None)
    c.add_argument("--prompt", required=True)
    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config) if args.config else None)
    db = db_connect(args.db)
    try:
        if args.cmd == "status":
            print(json.dumps(status(db, cfg), indent=2, default=str))
        elif args.cmd == "test":
            providers = {p["name"]: p for p in cfg.get("providers") or []}
            if args.provider not in providers:
                print(json.dumps({"ok": False, "error": "unknown provider"}))
                return 1
            print(json.dumps(probe_provider(providers[args.provider]), indent=2))
        elif args.cmd == "reset-cooldown":
            print(json.dumps(reset_cooldown(db, args.provider), indent=2, default=str))
        elif args.cmd == "chat":
            print(json.dumps(
                chat(db, goal_id=args.goal_id, user_prompt=args.prompt, config=cfg),
                indent=2, default=str,
            ))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
