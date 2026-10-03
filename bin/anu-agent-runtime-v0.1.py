#!/usr/bin/env python3

from dataclasses import dataclass
from enum import Enum


class AgentState(str, Enum):
    NEW = "NEW"
    READY = "READY"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    WAITING = "WAITING"
    RETRY = "RETRY"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS = {
    AgentState.NEW: {
        AgentState.READY,
        AgentState.FAILED,
    },
    AgentState.READY: {
        AgentState.PLANNING,
        AgentState.FAILED,
    },
    AgentState.PLANNING: {
        AgentState.EXECUTING,
        AgentState.FAILED,
    },
    AgentState.EXECUTING: {
        AgentState.OBSERVING,
        AgentState.FAILED,
    },
    AgentState.OBSERVING: {
        AgentState.COMPLETE,
        AgentState.WAITING,
        AgentState.RETRY,
        AgentState.FAILED,
    },
    AgentState.WAITING: set(),
    AgentState.RETRY: {
        AgentState.EXECUTING,
        AgentState.FAILED,
    },
    AgentState.COMPLETE: set(),
    AgentState.FAILED: set(),
}


@dataclass(frozen=True)
class AgentTransition:
    agent_id: str
    from_state: AgentState
    to_state: AgentState
    reason: str | None = None


class AgentStateMachine:
    """Deterministic state machine for the ANU agent lifecycle."""

    def __init__(self, agent_id):
        if not isinstance(agent_id, str):
            raise TypeError("agent_id must be a non-empty string")

        if not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")

        self.agent_id = agent_id
        self._state = AgentState.NEW
        self._history = []

    @property
    def state(self):
        return self._state

    @property
    def history(self):
        return tuple(self._history)

    @property
    def is_terminal(self):
        return self._state in {
            AgentState.COMPLETE,
            AgentState.FAILED,
        }

    def can_transition(self, target_state):
        target = self._coerce_state(target_state)
        return target in _ALLOWED_TRANSITIONS[self._state]

    def transition(self, target_state, reason=None):
        target = self._coerce_state(target_state)

        if target not in _ALLOWED_TRANSITIONS[self._state]:
            raise ValueError(
                f"invalid agent transition: "
                f"{self._state.value} -> {target.value}"
            )

        if reason is not None:
            if not isinstance(reason, str):
                raise TypeError("reason must be a string or None")

            reason = reason.strip()

            if not reason:
                raise ValueError("reason must not be empty")

        previous = self._state
        self._state = target

        transition = AgentTransition(
            agent_id=self.agent_id,
            from_state=previous,
            to_state=target,
            reason=reason,
        )

        self._history.append(transition)

        return transition

    def snapshot(self):
        return {
            "agent_id": self.agent_id,
            "state": self._state.value,
            "terminal": self.is_terminal,
            "history_length": len(self._history),
        }

    @staticmethod
    def _coerce_state(value):
        if isinstance(value, AgentState):
            return value

        if isinstance(value, str):
            try:
                return AgentState(value)
            except ValueError as exc:
                raise ValueError(
                    f"unknown agent state: {value!r}"
                ) from exc

        raise TypeError(
            "target_state must be an AgentState or state string"
        )


if __name__ == "__main__":
    raise SystemExit(
        "anu-agent-runtime-v0.1.py is a library module; "
        "invoke AgentStateMachine explicitly"
    )
