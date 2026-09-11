"""The stepdub intermediate representation (IR).

A recording is a sequence of `Event`. Recorders write into this; transforms and
code generators read only from this. No external dependencies, on purpose: reading,
transforming and generating code works with no browser installed at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1

MAX_TEXT_PREVIEW = 80

# Separator inside a ROLE selector value: "button|Sign in"
ROLE_SEP = "|"


class SelectorKind(StrEnum):
    """Ways to find an element, most robust first."""

    TESTID = "testid"
    ID = "id"
    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    CSS = "css"
    XPATH = "xpath"
    COORDS = "coords"


# Default robustness per kind (0-100). A recorder may lower the score of a specific
# candidate, e.g. an id that looks machine-generated.
DEFAULT_SCORE: dict[SelectorKind, int] = {
    SelectorKind.TESTID: 100,
    SelectorKind.ID: 90,
    SelectorKind.ROLE: 80,
    SelectorKind.LABEL: 75,
    SelectorKind.PLACEHOLDER: 65,
    SelectorKind.TEXT: 55,
    SelectorKind.CSS: 30,
    SelectorKind.XPATH: 20,
    SelectorKind.COORDS: 5,
}


class Action(StrEnum):
    """What the user did."""

    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    SELECT = "select"
    CHECK = "check"
    SUBMIT = "submit"
    DOWNLOAD = "download"
    WAIT = "wait"
    # A page opened by another one; context["opener"] is the number of that page
    POPUP = "popup"


# Actions that make no sense without an on-screen target.
ACTIONS_REQUIRING_TARGET = frozenset(
    {Action.CLICK, Action.FILL, Action.SELECT, Action.CHECK, Action.SUBMIT, Action.WAIT}
)

# Actions whose `value` is meaningful. For the rest, code generators ignore it.
ACTIONS_WITH_VALUE = frozenset(
    {Action.NAVIGATE, Action.FILL, Action.PRESS, Action.SELECT, Action.CHECK}
)

# Actions whose value is worth offering as a function parameter. Deliberately narrower
# than ACTIONS_WITH_VALUE: the start URL and a keypress carry values too, but nobody
# wants "Enter" as a parameter of their function.
ACTIONS_PARAMETERIZABLE = frozenset({Action.FILL, Action.SELECT})


def split_role(value: str) -> tuple[str, str]:
    """Split a ROLE selector value into (role, accessible name)."""
    role, _, name = value.partition(ROLE_SEP)
    return role, name


def page_of(event: Event) -> int:
    """The page an event happened in. Pages are numbered as they appear, from 1."""
    return int(event.context.get("page", 1))


@dataclass(frozen=True, slots=True)
class Selector:
    """One concrete way to find an element."""

    kind: SelectorKind
    value: str
    score: int = -1

    def __post_init__(self) -> None:
        # SelectorKind(...) is idempotent: it accepts the member or the string from JSON
        object.__setattr__(self, "kind", SelectorKind(self.kind))
        if not self.value:
            raise ValueError("Selector.value must not be empty")
        if self.score < 0:
            object.__setattr__(self, "score", DEFAULT_SCORE[self.kind])
        if not 0 <= self.score <= 100:
            raise ValueError(f"Selector.score out of range 0-100: {self.score}")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": self.value, "score": self.score}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Selector:
        return cls(
            kind=SelectorKind(data["kind"]),
            value=data["value"],
            score=int(data.get("score", -1)),
        )


@dataclass(frozen=True, slots=True)
class Target:
    """The element an action happened on, with every candidate selector.

    A single selector is never stored on its own: replay tries the best one and falls
    back to the next. Candidates are always re-sorted by score here, so code
    generation is deterministic regardless of the order the recorder produced them in.
    """

    candidates: tuple[Selector, ...]
    tag: str = ""
    text_preview: str = ""
    frame_url: str = ""

    def __post_init__(self) -> None:
        cands = tuple(self.candidates)
        if not cands:
            raise ValueError("Target needs at least one Selector")
        # sorted is stable: equal scores keep capture order
        object.__setattr__(self, "candidates", tuple(sorted(cands, key=lambda s: -s.score)))
        if len(self.text_preview) > MAX_TEXT_PREVIEW:
            object.__setattr__(self, "text_preview", self.text_preview[:MAX_TEXT_PREVIEW])

    @property
    def best(self) -> Selector:
        return self.candidates[0]

    @property
    def fallbacks(self) -> tuple[Selector, ...]:
        return self.candidates[1:]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"candidates": [c.to_dict() for c in self.candidates]}
        if self.tag:
            data["tag"] = self.tag
        if self.text_preview:
            data["text_preview"] = self.text_preview
        if self.frame_url:
            data["frame_url"] = self.frame_url
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Target:
        return cls(
            candidates=tuple(Selector.from_dict(c) for c in data["candidates"]),
            tag=data.get("tag", ""),
            text_preview=data.get("text_preview", ""),
            frame_url=data.get("frame_url", ""),
        )


@dataclass(frozen=True, slots=True)
class Event:
    """One step of a recording.

    The project's central invariant: an event marked as a secret NEVER carries the
    value. This is not a convention, it is enforced here with an exception. If any
    recorder ever tries to store a password, it raises instead of writing to disk.
    """

    id: int
    ts: float
    action: Action
    layer: str = "dom"
    target: Target | None = None
    value: str | None = None
    is_secret: bool = False
    secret_ref: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", Action(self.action))
        if self.id < 0:
            raise ValueError(f"Event.id must not be negative: {self.id}")
        if self.ts < 0:
            raise ValueError(f"Event.ts must not be negative: {self.ts}")

        # The invariant that protects the user
        if self.is_secret:
            if self.value is not None:
                raise ValueError(
                    f"event {self.id}: is_secret=True requires value=None "
                    "(a secret never enters the IR)"
                )
            if not self.secret_ref:
                raise ValueError(
                    f"event {self.id}: is_secret=True needs secret_ref so the "
                    "generated code knows which environment variable to read"
                )
        elif self.secret_ref:
            raise ValueError(f"event {self.id}: secret_ref without is_secret=True")

        if self.action in ACTIONS_REQUIRING_TARGET and self.target is None:
            raise ValueError(f"event {self.id}: action {self.action.value} needs a target")
        if self.action is Action.NAVIGATE and not self.value:
            raise ValueError(f"event {self.id}: navigate needs the URL in value")

    @property
    def needs_value(self) -> bool:
        return self.action in ACTIONS_WITH_VALUE

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "ts": round(self.ts, 3),
            "action": self.action.value,
            "layer": self.layer,
        }
        if self.target is not None:
            data["target"] = self.target.to_dict()
        if self.value is not None:
            data["value"] = self.value
        if self.is_secret:
            data["is_secret"] = True
            data["secret_ref"] = self.secret_ref
        if self.context:
            data["context"] = self.context
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        target = data.get("target")
        return cls(
            id=int(data["id"]),
            ts=float(data["ts"]),
            action=Action(data["action"]),
            layer=data.get("layer", "dom"),
            target=Target.from_dict(target) if target else None,
            value=data.get("value"),
            is_secret=bool(data.get("is_secret", False)),
            secret_ref=data.get("secret_ref"),
            context=dict(data.get("context", {})),
        )
