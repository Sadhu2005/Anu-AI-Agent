#!/usr/bin/env python3
"""V0.7.4: hardened prompt + one retry on tool-echo / no-steps."""

import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-llm-planner-v0.1.py"


def load_planner():
    spec = importlib.util.spec_from_file_location("llm_planner_v074", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = REPO
    mod._gateway_call = None
    return mod


VALID = json.dumps({
    "steps": [
        {
            "role": "inspect",
            "description": "inspect",
            "operation": "python_test",
        }
    ]
})

TOOL_ECHO = json.dumps({
    "name": "propose_plan",
    "parameters": {
        "goal_description": "x",
        "project_key": "sandbox-test-project",
    },
})


def test_retry_after_tool_echo_yields_llm():
    p = load_planner()
    calls = []

    def fake(op, **fields):
        calls.append((op, fields.get("tool_choice"), fields.get("system", "")[:40]))
        if op == "health":
            return {"ok": True}
        # First chat: tool-echo; second: valid steps
        if len([c for c in calls if c[0] == "chat"]) == 1:
            return {"ok": True, "text": TOOL_ECHO}
        return {"ok": True, "text": VALID}

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {},
        {},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is True
    assert out["source"] == "llm"
    assert out["reason"] == "ok_retry"
    assert out["steps"][0]["role"] == "inspect"
    chat_calls = [c for c in calls if c[0] == "chat"]
    assert len(chat_calls) == 2
    assert all(c[1] == "none" for c in chat_calls)
    print("PASS retry after tool-echo -> llm")


def test_no_retry_on_disallowed_operation():
    p = load_planner()
    calls = []

    def fake(op, **fields):
        calls.append(op)
        if op == "health":
            return {"ok": True}
        return {
            "ok": True,
            "text": json.dumps({
                "steps": [
                    {
                        "role": "bad",
                        "description": "nope",
                        "operation": "shell_exec",
                    }
                ]
            }),
        }

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {},
        {},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is False
    assert "bad_operation" in out["reason"]
    assert calls.count("chat") == 1
    print("PASS no retry on disallowed operation")


def test_retry_still_fails_falls_through():
    p = load_planner()

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        return {"ok": True, "text": TOOL_ECHO}

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {},
        {},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is False
    assert "steps_not_list" in out["reason"]
    print("PASS retry still fails -> not ok")


def test_retry_when_plan_exceeds_step_cap():
    p = load_planner()
    prompts = []

    def five_steps():
        steps = [
            {
                "role": role,
                "description": role,
                "operation": "python_test",
            }
            for role in ("research", "design", "implement", "test", "verify")
        ]
        steps[1]["operation"] = "python_compile"
        return json.dumps({"steps": steps})

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        prompts.append(fields.get("prompt") or "")
        if len(prompts) == 1:
            return {"ok": True, "text": five_steps()}
        return {"ok": True, "text": VALID}

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "one ping module"},
        {},
        {},
        project_key="lab-ping-module",
        max_steps=4,
    )
    assert out["ok"] is True
    assert out["source"] == "llm"
    assert out["reason"] == "ok_retry"
    assert len(out["steps"]) == 1
    assert len(prompts) == 2
    assert "at most 4" in prompts[0]
    assert "at most 4" in prompts[1]
    print("PASS retry when plan exceeds step cap")


def test_system_prompt_forbids_tools():
    p = load_planner()
    assert "Do NOT call tools" in p.SYSTEM_PROMPT or "tool" in p.SYSTEM_PROMPT.lower()
    assert "JSON" in p.RETRY_SYSTEM_PROMPT
    print("PASS system prompts hardened")


if __name__ == "__main__":
    print("===== ANU V0.7.4 LLM PLANNER RETRY =====")
    test_retry_after_tool_echo_yields_llm()
    test_no_retry_on_disallowed_operation()
    test_retry_still_fails_falls_through()
    test_retry_when_plan_exceeds_step_cap()
    test_system_prompt_forbids_tools()
    print("LLM_PLANNER_RETRY_TEST_COMPLETE")
