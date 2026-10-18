#!/usr/bin/env python3
"""F1: freeze/thaw must preserve empty lists and list-of-pairs (ADR-014)."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODEL = REPO / "bin" / "anu-agent-model-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("model_f1", MODEL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empty_list_stays_list():
    m = load()
    action = m.AgentAction(
        action_id="a1",
        goal_id="1",
        kind="TASK",
        description="d",
        parameters={"files": []},
    )
    assert action.to_dict()["parameters"] == {"files": []}
    print("PASS empty list stays list")


def test_list_of_pairs_stays_list():
    m = load()
    action = m.AgentAction(
        action_id="a1",
        goal_id="1",
        kind="TASK",
        description="d",
        parameters={"pairs": [["a", 1], ["b", 2]]},
    )
    assert action.to_dict()["parameters"] == {"pairs": [["a", 1], ["b", 2]]}
    print("PASS list-of-pairs stays list")


def test_nested_combinations():
    m = load()
    payload = {
        "files": [],
        "pairs": [["a", 1]],
        "nested": {"inner": [], "map": {"k": "v"}},
        "depends_on": [],
    }
    action = m.AgentAction(
        action_id="a1",
        goal_id="1",
        kind="TASK",
        description="d",
        parameters=payload,
    )
    got = action.to_dict()["parameters"]
    assert got["files"] == []
    assert got["pairs"] == [["a", 1]]
    assert got["depends_on"] == []
    assert got["nested"]["inner"] == []
    assert got["nested"]["map"] == {"k": "v"}

    plan = m.AgentPlan(
        plan_id="p1",
        goal_id="1",
        actions=(action,),
    )
    body = plan.to_dict()
    assert body["actions"][0]["parameters"]["files"] == []
    print("PASS nested combinations and plan round trip")


if __name__ == "__main__":
    print("===== ANU F1 FREEZE/THAW REGRESSION =====")
    test_empty_list_stays_list()
    test_list_of_pairs_stays_list()
    test_nested_combinations()
    print("F1_FREEZE_THAW_TEST_COMPLETE")
