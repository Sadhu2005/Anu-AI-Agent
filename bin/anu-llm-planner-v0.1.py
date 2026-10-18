#!/usr/bin/env python3
"""ANU LLM Planner v0.1 (V0.7.2) — optional gateway plan proposal.

Model output is untrusted: parse → validate → only existing operations
(``python_test`` / ``python_compile``). Gateway down or invalid JSON →
template planner fallback (``improve_sandbox``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DB_FILE = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent

ALLOWED_OPERATIONS = frozenset({"python_test", "python_compile"})
DEFAULT_FALLBACK_TEMPLATE = "improve_sandbox"
DEFAULT_MAX_STEPS = 8

SYSTEM_PROMPT = (
    "You are ANU's planning assistant. Reply with JSON only, no markdown. "
    "Schema: {\"steps\":[{\"role\":\"short_id\",\"description\":\"one line\","
    "\"operation\":\"python_test\"|\"python_compile\"}]}. "
    "1–8 steps. Only those two operations. No shell, paths, or commands."
)

# Tests may inject a fake gateway caller: _gateway_call(op, **fields) -> dict
_gateway_call = None


def _module_path(name):
    live = BASE / "bin" / name
    if live.is_file():
        return live
    return _HERE / name


def _load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "BASE"):
        module.BASE = BASE
    return module


def _load_template():
    return _load_module(
        _module_path("anu-template-planner-v0.1.py"),
        "anu_template_planner_v072_llm",
    )


def _load_client():
    return _load_module(
        _module_path("anu-model-client-v0.1.py"),
        "anu_model_client_v072_llm",
    )


def _load_goal_service():
    return _load_module(
        _module_path("anu-goal-service-v0.1.py"),
        "anu_goal_service_v072_llm",
    )


def _load_plan_service():
    return _load_module(
        _module_path("anu-goal-plan-service-v0.1.py"),
        "anu_goal_plan_service_v072_llm",
    )


def _load_task_graph():
    return _load_module(
        _module_path("anu-task-graph-v0.1.py"),
        "anu_task_graph_v072_llm",
    )


def _load_execution_spec():
    return _load_module(
        _module_path("anu-task-execution-spec-v0.1.py"),
        "anu_task_execution_spec_v072_llm",
    )


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def _config_candidates():
    return (
        BASE / "config" / "anu-model-gateway.yaml",
        _HERE.parent / "config" / "anu-model-gateway.yaml",
    )


def load_planner_config(config_path=None):
    """Return planner block with defaults."""
    defaults = {
        "mode": "llm_optional",
        "fallback_template": DEFAULT_FALLBACK_TEMPLATE,
        "max_steps": DEFAULT_MAX_STEPS,
        "timeout_seconds": 60,
    }
    path = Path(config_path) if config_path else None
    candidates = [path] if path else list(_config_candidates())
    data = {}
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            break
        except Exception:
            data = {}
    block = dict(defaults)
    raw = data.get("planner") if isinstance(data, dict) else None
    if isinstance(raw, dict):
        if raw.get("mode") in ("llm_optional", "template_only"):
            block["mode"] = raw["mode"]
        if isinstance(raw.get("fallback_template"), str) and raw["fallback_template"].strip():
            block["fallback_template"] = raw["fallback_template"].strip()
        try:
            ms = int(raw.get("max_steps", block["max_steps"]))
            if ms >= 1:
                block["max_steps"] = min(ms, 16)
        except (TypeError, ValueError):
            pass
        try:
            ts = float(raw.get("timeout_seconds", block["timeout_seconds"]))
            if ts > 0:
                block["timeout_seconds"] = ts
        except (TypeError, ValueError):
            pass
    return block


def call_gateway(op: str, *, timeout: float | None = None, **fields) -> dict:
    if _gateway_call is not None:
        return _gateway_call(op, timeout=timeout, **fields)
    client = _load_client()
    kwargs = dict(fields)
    if timeout is not None:
        kwargs["timeout"] = timeout
    return client.call_gateway(op, **kwargs)


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_json_object(text: str):
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def validate_steps(raw, *, max_steps=DEFAULT_MAX_STEPS):
    """Validate LLM step list. Returns (ok, steps_or_none, reason)."""
    if not isinstance(raw, dict):
        return False, None, "root_not_object"
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return False, None, "steps_not_list"
    if len(steps) < 1:
        return False, None, "empty_steps"
    if len(steps) > max_steps:
        return False, None, f"too_many_steps:{len(steps)}>{max_steps}"

    cleaned = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, None, f"step_{index}_not_object"
        role = step.get("role")
        description = step.get("description")
        operation = step.get("operation")
        if not isinstance(role, str) or not role.strip():
            return False, None, f"step_{index}_bad_role"
        if not isinstance(description, str) or not description.strip():
            return False, None, f"step_{index}_bad_description"
        if not isinstance(operation, str) or operation not in ALLOWED_OPERATIONS:
            return False, None, f"step_{index}_bad_operation:{operation!r}"
        cleaned.append({
            "role": role.strip()[:64],
            "description": description.strip()[:500],
            "operation": operation,
            "kind": "TASK",
        })
    return True, cleaned, "ok"


def _build_user_prompt(goal, project_key, project_ctx, memory):
    goal_desc = ""
    if isinstance(goal, dict):
        goal_desc = str(goal.get("description") or "")
    payload = {
        "goal_description": goal_desc[:1000],
        "project_key": project_key,
        "file_count": (project_ctx or {}).get("file_count"),
        "has_tests": (project_ctx or {}).get("has_tests"),
        "known_problem_count": (project_ctx or {}).get("known_problem_count"),
        "memory_hints": list((memory or {}).get("hints") or [])[:5],
        "repeated_failure": bool((memory or {}).get("repeated_failure")),
    }
    return (
        "Propose a linear plan for this ANU goal. "
        "Use only python_test or python_compile.\n"
        + json.dumps(payload, indent=2)
    )


def propose_steps(
    goal,
    project_ctx,
    memory,
    *,
    project_key=None,
    max_steps=DEFAULT_MAX_STEPS,
    timeout_seconds=60,
):
    """Ask the gateway for steps. Never raises for provider failures."""
    try:
        health = call_gateway("health", timeout=min(30.0, float(timeout_seconds)))
    except Exception as exc:
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": f"health_error:{type(exc).__name__}:{exc}",
        }
    if not isinstance(health, dict) or not health.get("ok"):
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": f"health_not_ok:{health!r}"[:200],
        }

    key = project_key or (project_ctx or {}).get("project_key") or "unknown"
    prompt = _build_user_prompt(goal, key, project_ctx, memory)
    try:
        chat = call_gateway(
            "chat",
            timeout=float(timeout_seconds),
            prompt=prompt,
            system=SYSTEM_PROMPT,
        )
    except Exception as exc:
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": f"chat_error:{type(exc).__name__}:{exc}",
        }
    if not isinstance(chat, dict) or not chat.get("ok"):
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": f"chat_not_ok:{chat!r}"[:200],
        }

    text = chat.get("text")
    if not isinstance(text, str) or not text.strip():
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": "empty_text",
        }

    parsed = _extract_json_object(text)
    if parsed is None:
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": "json_parse_failed",
        }

    ok, steps, reason = validate_steps(parsed, max_steps=max_steps)
    if not ok:
        return {
            "ok": False,
            "source": "none",
            "steps": None,
            "reason": reason,
        }
    return {
        "ok": True,
        "source": "llm",
        "steps": steps,
        "reason": "ok",
    }


def build_plan_dict_from_steps(goal_id, steps, plan_id=None):
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 1:
        raise ValueError("goal_id must be a positive integer")
    if not steps:
        raise ValueError("steps must be non-empty")

    goal_key = str(goal_id)
    stamp = uuid.uuid4().hex[:10]
    plan_id = plan_id or f"plan-llm-{stamp}"
    actions = []
    for step in steps:
        operation = step["operation"]
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported operation {operation!r}")
        role = step["role"]
        actions.append({
            "action_id": f"{role}-{stamp}",
            "goal_id": goal_key,
            "kind": step.get("kind") or "TASK",
            "description": step["description"],
            "parameters": {
                "template": "llm",
                "planner": "llm",
                "role": role,
                "operation": operation,
            },
        })
    return {
        "plan_id": plan_id,
        "goal_id": goal_key,
        "actions": actions,
    }


def _expand_llm_plan(
    db,
    goal_id,
    project,
    steps,
    *,
    changes_file,
    plan_id,
    project_ctx,
    memory,
    tpl,
):
    """Persist an LLM-validated plan using the same task/spec/deps pattern."""
    goals = _load_goal_service()
    plans = _load_plan_service()
    graph = _load_task_graph()
    specs = _load_execution_spec()

    plans.migrate(db)
    if hasattr(specs, "migrate"):
        specs.migrate(db)

    plan_dict = build_plan_dict_from_steps(goal_id, steps, plan_id=plan_id)

    for action in plan_dict["actions"]:
        action["parameters"]["project_registered"] = True
        action["parameters"]["project_file_count"] = project_ctx.get("file_count")
        action["parameters"]["project_has_tests"] = project_ctx.get("has_tests")
        action["parameters"]["project_known_problems"] = project_ctx.get(
            "known_problem_count"
        )
        if project_ctx.get("top_level"):
            action["parameters"]["project_top_level"] = list(
                project_ctx["top_level"]
            )[:20]
        if memory.get("hints"):
            action["parameters"]["memory_hints"] = list(memory["hints"])
            action["parameters"]["memory_repeated_failure"] = bool(
                memory.get("repeated_failure")
            )
            if memory.get("failure_classes"):
                action["parameters"]["memory_failure_classes"] = list(
                    memory["failure_classes"]
                )

    plan_record = plans.save_plan(db, goal_id, plan_dict, status="active")
    created = []
    previous_task_id = None
    base_priority = 50

    for index, action in enumerate(plan_dict["actions"]):
        operation = action["parameters"]["operation"]
        role = action["parameters"]["role"]
        task_key = f"GOAL-{goal_id}-{role}-{uuid.uuid4().hex[:8].upper()}"
        title = f"llm:{role}"
        task_id = tpl._create_task_row(
            db,
            goal_id=goal_id,
            title=title,
            description=action["description"],
            priority=base_priority - index,
            task_key=task_key,
        )
        specs.save_execution_descriptor(
            db,
            task_id,
            project=project,
            changes_file=changes_file,
            operation=operation,
        )
        if previous_task_id is not None:
            graph.add_dependency(db, task_id, previous_task_id)

        created.append({
            "task_id": task_id,
            "task_key": task_key,
            "action_id": action["action_id"],
            "role": role,
            "operation": operation,
            "depends_on_task_id": previous_task_id,
        })
        previous_task_id = task_id

    goals.update_goal_status(
        db,
        goal_id,
        "active",
        reason=f"llm plan expanded to {len(created)} tasks",
    )

    return {
        "goal_id": goal_id,
        "template": "llm",
        "source": "llm",
        "project": project,
        "changes_file": changes_file,
        "plan": plan_record,
        "tasks": created,
        "memory": memory,
        "project_ctx": project_ctx,
    }


def expand_goal(
    db,
    goal_id,
    project,
    template_name=DEFAULT_FALLBACK_TEMPLATE,
    changes_file="changes.json",
    plan_id=None,
    *,
    planner_config=None,
):
    """Expand a goal via LLM when configured/healthy; else template fallback."""
    db = _require_db(db)
    cfg = planner_config or load_planner_config()
    fallback = cfg.get("fallback_template") or DEFAULT_FALLBACK_TEMPLATE
    # Callers may still pass template_name as the preferred fallback.
    if template_name and template_name != "llm":
        fallback = template_name

    tpl = _load_template()

    if cfg.get("mode") == "template_only":
        result = tpl.expand_goal(
            db,
            goal_id,
            project=project,
            template_name=fallback,
            changes_file=changes_file,
            plan_id=plan_id,
        )
        result["source"] = "template_only"
        return result

    if not isinstance(project, str) or not project.strip():
        raise ValueError("project must be a non-empty string")
    if not isinstance(changes_file, str) or not changes_file.strip():
        raise ValueError("changes_file must be a non-empty string")

    project = project.strip()
    changes_file = changes_file.strip()

    goals = _load_goal_service()
    goal = goals.get_goal(db, goal_id)
    if goal is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    project_ctx = tpl._project_context_for_planning(db, project)
    memory = tpl._memory_hints_for_project(db, project)

    proposal = propose_steps(
        goal,
        project_ctx,
        memory,
        project_key=project,
        max_steps=int(cfg.get("max_steps") or DEFAULT_MAX_STEPS),
        timeout_seconds=float(cfg.get("timeout_seconds") or 60),
    )

    if proposal.get("ok") and proposal.get("steps"):
        try:
            return _expand_llm_plan(
                db,
                goal_id,
                project,
                proposal["steps"],
                changes_file=changes_file,
                plan_id=plan_id,
                project_ctx=project_ctx,
                memory=memory,
                tpl=tpl,
            )
        except Exception as exc:
            # Persistence failure → fall through to template.
            proposal = {
                "ok": False,
                "reason": f"llm_persist_error:{type(exc).__name__}:{exc}",
            }

    result = tpl.expand_goal(
        db,
        goal_id,
        project=project,
        template_name=fallback,
        changes_file=changes_file,
        plan_id=plan_id,
    )
    result["source"] = "template_fallback"
    result["fallback_reason"] = proposal.get("reason")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU LLM Planner v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument(
        "--config",
        default=None,
        help="Path to anu-model-gateway.yaml (planner block)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config")

    expand_p = sub.add_parser("expand")
    expand_p.add_argument("--goal-id", type=int, required=True)
    expand_p.add_argument("--project", required=True)
    expand_p.add_argument("--template", default=DEFAULT_FALLBACK_TEMPLATE)
    expand_p.add_argument("--changes-file", default="changes.json")
    expand_p.add_argument("--plan-id", default=None)

    propose_p = sub.add_parser("propose")
    propose_p.add_argument("--goal-id", type=int, required=True)
    propose_p.add_argument("--project", required=True)

    args = parser.parse_args(argv)
    cfg = load_planner_config(args.config)

    if args.cmd == "config":
        print(json.dumps({"ok": True, "planner": cfg}, indent=2))
        return 0

    db = sqlite3.connect(str(Path(args.db)))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        if args.cmd == "expand":
            result = expand_goal(
                db,
                args.goal_id,
                project=args.project,
                template_name=args.template,
                changes_file=args.changes_file,
                plan_id=args.plan_id,
                planner_config=cfg,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2, default=str))
            return 0
        if args.cmd == "propose":
            tpl = _load_template()
            goals = _load_goal_service()
            goal = goals.get_goal(db, args.goal_id)
            if goal is None:
                print(json.dumps({"ok": False, "error": "goal not found"}))
                return 1
            project_ctx = tpl._project_context_for_planning(db, args.project)
            memory = tpl._memory_hints_for_project(db, args.project)
            proposal = propose_steps(
                goal,
                project_ctx,
                memory,
                project_key=args.project,
                max_steps=int(cfg.get("max_steps") or DEFAULT_MAX_STEPS),
                timeout_seconds=float(cfg.get("timeout_seconds") or 60),
            )
            print(json.dumps({"ok": True, "proposal": proposal}, indent=2))
            return 0 if proposal.get("ok") else 1
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
