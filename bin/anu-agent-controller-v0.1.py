#!/usr/bin/env python3

import dataclasses
import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

STATE_MODULE = BASE / "bin" / "anu-agent-runtime-v0.1.py"
MODEL_MODULE = BASE / "bin" / "anu-agent-model-v0.1.py"


def _load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load module: {path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_state_module():
    return _load_module(
        STATE_MODULE,
        "anu_agent_runtime_v0_1_controller",
    )


def _load_model_module():
    return _load_module(
        MODEL_MODULE,
        "anu_agent_model_v0_1_controller",
    )


def _is_model_contract_instance(value, expected_type):
    """
    Accept ANU model contract objects loaded from the canonical model
    source, even when a regression harness loads that source under a
    different Python module name.

    The custom loaders used by ANU execute modules under distinct
    module names, so exact Python class identity cannot be required.
    """
    if isinstance(value, expected_type):
        return True

    actual_type = type(value)

    if actual_type.__name__ != expected_type.__name__:
        return False

    if not actual_type.__module__.startswith(
        "anu_agent_model_v0_1"
    ):
        return False

    if not dataclasses.is_dataclass(value):
        return False

    try:
        expected_fields = tuple(
            field.name
            for field in dataclasses.fields(expected_type)
        )

        actual_fields = tuple(
            field.name
            for field in dataclasses.fields(value)
        )
    except TypeError:
        return False

    return expected_fields == actual_fields




class AgentControllerError(RuntimeError):
    """Raised when the controller receives an invalid agent contract."""


class AgentController:
    """Coordinate the deterministic ANU agent lifecycle.

    The controller owns lifecycle orchestration only.

    Planning and execution are supplied as callbacks:

        planner_fn(goal) -> AgentPlan
        executor_fn(action) -> AgentResult

    The controller does not contain LLM logic, tool implementation,
    scheduler logic, or direct database operations.
    """

    def __init__(
        self,
        agent_id,
        goal,
        planner_fn,
        executor_fn,
        max_retries=0,
    ):
        state = _load_state_module()
        model = _load_model_module()

        if not _is_model_contract_instance(
            goal,
            model.AgentGoal,
        ):
            raise TypeError(
                "goal must be an AgentGoal instance"
            )

        if not callable(planner_fn):
            raise TypeError(
                "planner_fn must be callable"
            )

        if not callable(executor_fn):
            raise TypeError(
                "executor_fn must be callable"
            )

        if isinstance(max_retries, bool) or not isinstance(
            max_retries,
            int,
        ):
            raise TypeError(
                "max_retries must be a non-negative integer"
            )

        if max_retries < 0:
            raise ValueError(
                "max_retries must be a non-negative integer"
            )

        self.agent_id = agent_id
        self.goal = goal
        self.planner_fn = planner_fn
        self.executor_fn = executor_fn
        self.max_retries = max_retries

        self.state_machine = state.AgentStateMachine(
            agent_id
        )

        self.plan = None
        self._results = []
        self._attempts = 0
        self._retry_count = 0
        self._has_run = False

    @property
    def state(self):
        return self.state_machine.state

    @property
    def results(self):
        return tuple(self._results)

    @property
    def attempts(self):
        return self._attempts

    @property
    def retry_count(self):
        return self._retry_count

    @property
    def history(self):
        return self.state_machine.history

    @property
    def is_terminal(self):
        return self.state_machine.is_terminal

    def run(self):
        if self._has_run:
            raise RuntimeError(
                "agent controller can only run once"
            )

        self._has_run = True

        self.state_machine.transition(
            "READY",
            reason="agent initialized",
        )

        self.state_machine.transition(
            "PLANNING",
            reason="goal ready for planning",
        )

        try:
            plan = self.planner_fn(self.goal)
            self._validate_plan(plan)
            self.plan = plan
        except Exception as exc:
            self.state_machine.transition(
                "FAILED",
                reason=f"planning failed: {exc}",
            )
            raise

        self.state_machine.transition(
            "EXECUTING",
            reason="plan accepted",
        )

        while True:
            self._attempts += 1
            attempt_results = []

            try:
                for action in self.plan.actions:
                    result = self.executor_fn(action)
                    self._validate_result(
                        action,
                        result,
                    )

                    attempt_results.append(result)
                    self._results.append(result)

            except Exception as exc:
                self.state_machine.transition(
                    "FAILED",
                    reason=f"execution failed: {exc}",
                )
                raise

            self.state_machine.transition(
                "OBSERVING",
                reason="execution results collected",
            )

            all_successful = all(
                result.success
                for result in attempt_results
            )

            if all_successful:
                self.state_machine.transition(
                    "COMPLETE",
                    reason="all actions completed successfully",
                )
                return self.report()

            if self._retry_count < self.max_retries:
                self._retry_count += 1

                self.state_machine.transition(
                    "RETRY",
                    reason=(
                        f"execution attempt failed; "
                        f"retry {self._retry_count} "
                        f"of {self.max_retries}"
                    ),
                )

                self.state_machine.transition(
                    "EXECUTING",
                    reason="retry execution",
                )

                continue

            self.state_machine.transition(
                "FAILED",
                reason="maximum retries exhausted",
            )

            return self.report()

    def report(self):
        return {
            "agent_id": self.agent_id,
            "goal_id": self.goal.goal_id,
            "state": self.state.value,
            "terminal": self.is_terminal,
            "attempts": self._attempts,
            "retry_count": self._retry_count,
            "plan_id": (
                self.plan.plan_id
                if self.plan is not None
                else None
            ),
            "result_count": len(self._results),
        }

    def _validate_plan(self, plan):
        model = _load_model_module()

        if not _is_model_contract_instance(
            plan,
            model.AgentPlan,
        ):
            raise AgentControllerError(
                "planner_fn must return AgentPlan"
            )

        if plan.goal_id != self.goal.goal_id:
            raise AgentControllerError(
                f"plan goal_id {plan.goal_id!r} does not "
                f"match controller goal {self.goal.goal_id!r}"
            )

    def _validate_result(self, action, result):
        model = _load_model_module()

        if not _is_model_contract_instance(
            result,
            model.AgentResult,
        ):
            raise AgentControllerError(
                "executor_fn must return AgentResult"
            )

        if result.action_id != action.action_id:
            raise AgentControllerError(
                f"result action_id {result.action_id!r} does not "
                f"match action {action.action_id!r}"
            )


if __name__ == "__main__":
    raise SystemExit(
        "anu-agent-controller-v0.1.py is a library module; "
        "construct AgentController explicitly"
    )
