#!/usr/bin/env python3
"""V0.7.3: coerce LM Studio / tool-call envelopes into plan steps."""

import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-llm-planner-v0.1.py"


def load_planner():
    spec = importlib.util.spec_from_file_location("llm_planner_v073", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = REPO
    mod._gateway_call = None
    return mod


STEPS = {
    "steps": [
        {
            "role": "inspect",
            "description": "inspect project",
            "operation": "python_test",
        },
        {
            "role": "verify",
            "description": "verify",
            "operation": "python_compile",
        },
    ]
}


def test_coerce_raw_steps_passthrough():
    p = load_planner()
    out = p.coerce_plan_object(dict(STEPS))
    assert out["steps"][0]["role"] == "inspect"
    print("PASS coerce raw steps passthrough")


def test_coerce_tool_parameters_with_steps():
    p = load_planner()
    envelope = {"name": "create_plan", "parameters": dict(STEPS)}
    out = p.coerce_plan_object(envelope)
    assert isinstance(out.get("steps"), list)
    assert len(out["steps"]) == 2
    print("PASS coerce tool parameters.steps")


def test_coerce_tool_parameters_plan_python_literal():
    p = load_planner()
    # Matches Pi LM Studio quirk: plan string with single-quoted literals.
    plan_str = (
        "{'steps': [{'role': 'inspect', 'description': 'inspect', "
        "'operation': 'python_test'}, {'role': 'verify', "
        "'description': 'verify', 'operation': 'python_compile'}]}"
    )
    envelope = {"name": "create_plan", "parameters": {"plan": plan_str}}
    out = p.coerce_plan_object(envelope)
    ok, steps, reason = p.validate_steps(out, max_steps=8)
    assert ok and reason == "ok"
    assert len(steps) == 2
    assert steps[0]["operation"] == "python_test"
    print("PASS coerce tool parameters.plan python-literal")


def test_coerce_tool_parameters_plan_json_string():
    p = load_planner()
    envelope = {
        "name": "create_plan",
        "parameters": {"plan": json.dumps(STEPS)},
    }
    out = p.coerce_plan_object(envelope)
    ok, steps, reason = p.validate_steps(out, max_steps=8)
    assert ok and len(steps) == 2
    print("PASS coerce tool parameters.plan JSON string")


def test_coerce_openai_tool_calls_arguments():
    p = load_planner()
    envelope = {
        "tool_calls": [
            {
                "function": {
                    "name": "create_plan",
                    "arguments": json.dumps(STEPS),
                }
            }
        ]
    }
    out = p.coerce_plan_object(envelope)
    ok, steps, reason = p.validate_steps(out, max_steps=8)
    assert ok and len(steps) == 2
    print("PASS coerce OpenAI tool_calls.arguments")


def test_propose_unwraps_tool_envelope():
    p = load_planner()
    plan_str = (
        "{'steps': [{'role': 'a', 'description': 'do a', "
        "'operation': 'python_test'}]}"
    )
    text = json.dumps({
        "name": "create_plan",
        "parameters": {"plan": plan_str},
    })

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        return {"ok": True, "text": text}

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {},
        {},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is True
    assert out["source"] == "llm"
    assert out["steps"][0]["role"] == "a"
    print("PASS propose_steps unwraps tool envelope -> llm")


def test_disallowed_still_rejected_after_unwrap():
    p = load_planner()
    envelope = {
        "name": "create_plan",
        "parameters": {
            "steps": [
                {
                    "role": "bad",
                    "description": "nope",
                    "operation": "shell_exec",
                }
            ]
        },
    }
    ok, steps, reason = p.validate_steps(envelope, max_steps=8)
    assert not ok and "bad_operation" in reason
    print("PASS disallowed op still rejected after unwrap")


if __name__ == "__main__":
    print("===== ANU V0.7.3 LLM PLANNER UNWRAP =====")
    test_coerce_raw_steps_passthrough()
    test_coerce_tool_parameters_with_steps()
    test_coerce_tool_parameters_plan_python_literal()
    test_coerce_tool_parameters_plan_json_string()
    test_coerce_openai_tool_calls_arguments()
    test_propose_unwraps_tool_envelope()
    test_disallowed_still_rejected_after_unwrap()
    print("LLM_PLANNER_UNWRAP_TEST_COMPLETE")
