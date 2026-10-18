#!/usr/bin/env python3

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any


def _validate_id(value, field_name):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a non-empty string")

    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

    return value.strip()


def _validate_text(value, field_name):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a non-empty string")

    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")

    return value.strip()


class _FrozenMapping(tuple):
    """Tagged immutable mapping: thaw only when this type is present (ADR-014).

    A bare tuple of ``(key, value)`` pairs must not be guessed to be a
    mapping — that corrupted empty lists and list-of-pairs (review F1).
    """


def _freeze(value):
    """Convert JSON-compatible data into immutable deterministic data."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, _FrozenMapping):
        return value

    if isinstance(value, Mapping):
        items = []

        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")

            items.append(
                (
                    key,
                    _freeze(item),
                )
            )

        items.sort(key=lambda pair: pair[0])
        return _FrozenMapping(items)

    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)

    raise TypeError(
        "data must contain only JSON-compatible values"
    )


def _thaw(value):
    """Convert immutable internal data back into JSON-compatible data."""

    if isinstance(value, _FrozenMapping):
        return {
            key: _thaw(item)
            for key, item in value
        }

    if isinstance(value, tuple):
        return [_thaw(item) for item in value]

    return value


def _freeze_mapping(value, field_name):
    if value is None:
        return _FrozenMapping()

    if not isinstance(value, Mapping):
        raise TypeError(
            f"{field_name} must be a mapping or None"
        )

    frozen = _freeze(value)

    if not isinstance(frozen, _FrozenMapping):
        raise TypeError(
            f"{field_name} must be a mapping"
        )

    return frozen


@dataclass(frozen=True)
class AgentGoal:
    goal_id: str
    description: str
    priority: int = 0
    metadata: object = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "goal_id",
            _validate_id(self.goal_id, "goal_id"),
        )

        object.__setattr__(
            self,
            "description",
            _validate_text(
                self.description,
                "description",
            ),
        )

        if isinstance(self.priority, bool) or not isinstance(
            self.priority,
            int,
        ):
            raise TypeError("priority must be an integer")

        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(
                self.metadata,
                "metadata",
            ),
        )

    def to_dict(self):
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "priority": self.priority,
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True)
class AgentAction:
    action_id: str
    goal_id: str
    kind: str
    description: str
    parameters: object = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "action_id",
            _validate_id(
                self.action_id,
                "action_id",
            ),
        )

        object.__setattr__(
            self,
            "goal_id",
            _validate_id(
                self.goal_id,
                "goal_id",
            ),
        )

        object.__setattr__(
            self,
            "kind",
            _validate_text(
                self.kind,
                "kind",
            ),
        )

        object.__setattr__(
            self,
            "description",
            _validate_text(
                self.description,
                "description",
            ),
        )

        object.__setattr__(
            self,
            "parameters",
            _freeze_mapping(
                self.parameters,
                "parameters",
            ),
        )

    def to_dict(self):
        return {
            "action_id": self.action_id,
            "goal_id": self.goal_id,
            "kind": self.kind,
            "description": self.description,
            "parameters": _thaw(self.parameters),
        }


@dataclass(frozen=True)
class AgentPlan:
    plan_id: str
    goal_id: str
    actions: tuple[AgentAction, ...]

    def __post_init__(self):
        object.__setattr__(
            self,
            "plan_id",
            _validate_id(
                self.plan_id,
                "plan_id",
            ),
        )

        object.__setattr__(
            self,
            "goal_id",
            _validate_id(
                self.goal_id,
                "goal_id",
            ),
        )

        if not isinstance(self.actions, (list, tuple)):
            raise TypeError(
                "actions must be a list or tuple"
            )

        actions = tuple(self.actions)

        seen = set()

        for action in actions:
            if not isinstance(action, AgentAction):
                raise TypeError(
                    "plan actions must be AgentAction instances"
                )

            if action.goal_id != self.goal_id:
                raise ValueError(
                    f"action {action.action_id!r} belongs to "
                    f"goal {action.goal_id!r}, expected "
                    f"{self.goal_id!r}"
                )

            if action.action_id in seen:
                raise ValueError(
                    f"duplicate action_id: {action.action_id!r}"
                )

            seen.add(action.action_id)

        object.__setattr__(
            self,
            "actions",
            actions,
        )

    def to_dict(self):
        return {
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "actions": [
                action.to_dict()
                for action in self.actions
            ],
        }


@dataclass(frozen=True)
class AgentResult:
    action_id: str
    success: bool
    status: str
    message: str
    data: object = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "action_id",
            _validate_id(
                self.action_id,
                "action_id",
            ),
        )

        if not isinstance(self.success, bool):
            raise TypeError(
                "success must be a boolean"
            )

        object.__setattr__(
            self,
            "status",
            _validate_text(
                self.status,
                "status",
            ),
        )

        object.__setattr__(
            self,
            "message",
            _validate_text(
                self.message,
                "message",
            ),
        )

        object.__setattr__(
            self,
            "data",
            _freeze_mapping(
                self.data,
                "data",
            ),
        )

    def to_dict(self):
        return {
            "action_id": self.action_id,
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "data": _thaw(self.data),
        }


if __name__ == "__main__":
    raise SystemExit(
        "anu-agent-model-v0.1.py is a library module; "
        "construct AgentGoal, AgentAction, AgentPlan, "
        "and AgentResult explicitly"
    )
