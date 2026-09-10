"""The transformation pipeline between a raw recording and the code generator."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..ir import Event
from .passes import (
    coalesce_typing,
    drop_focus_clicks,
    insert_waits,
    normalize_secret_refs,
    renumber,
)

Pass = Callable[[Sequence[Event]], tuple[Event, ...]]

# The order matters and is not arbitrary:
#   1. drop focus clicks before merging typing, otherwise a click sits in the middle of
#      a group and blocks the merge;
#   2. merge typing before inserting waits, so pauses are measured between real steps
#      rather than between keystrokes;
#   3. normalize secrets after the merge, so refs that are about to die are not
#      normalized;
#   4. always renumber last, because everything before it inserts and removes events.
DEFAULT_PIPELINE: tuple[Pass, ...] = (
    drop_focus_clicks,
    coalesce_typing,
    insert_waits,
    normalize_secret_refs,
    renumber,
)

__all__ = [
    "DEFAULT_PIPELINE",
    "Pass",
    "coalesce_typing",
    "drop_focus_clicks",
    "insert_waits",
    "normalize_secret_refs",
    "renumber",
    "run_pipeline",
]


def run_pipeline(
    events: Sequence[Event], pipeline: Sequence[Pass] | None = None
) -> tuple[Event, ...]:
    """Apply the passes in order. Does not mutate the input sequence."""
    current: tuple[Event, ...] = tuple(events)
    for step in pipeline if pipeline is not None else DEFAULT_PIPELINE:
        current = step(current)
    return current
