"""IR transformation passes.

Every pass is a pure function `Sequence[Event] -> tuple[Event, ...]`: it touches no
disk, needs no browser, and never mutates its input. That is why they can be tested
without opening anything.

What comes out of a recorder is noisy on purpose. Typing "hi" fires two input events,
and clicking a field before typing in it is not a step of the process - it is the user
putting the cursor where they want it. Cleaning that up is these passes' job, not the
recorder's: the recorder captures everything it sees, so information that turns out to
be useful tomorrow is not lost today.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..ir import Action, Event

# A click followed by typing in the same field within this window is just the cursor.
FOCUS_CLICK_GAP = 1.5

# A pause longer than this is assumed to be the user waiting for the page.
WAIT_THRESHOLD = 2.0

# Actions after which the page usually changes, so waiting is worth it.
# PRESS counts: Enter inside a search box submits the form.
_CAUSES_PAGE_CHANGE = frozenset(
    {Action.NAVIGATE, Action.CLICK, Action.PRESS, Action.SUBMIT, Action.DOWNLOAD}
)

_TYPING_ACTIONS = frozenset({Action.FILL, Action.SELECT, Action.CHECK})

_SECRET_REF_INVALID = re.compile(r"[^A-Z0-9_]+")


def _same_target(a: Event, b: Event) -> bool:
    """Same element, for the purposes of noise removal."""
    if a.target is None or b.target is None:
        return False
    if a.target.frame_url != b.target.frame_url:
        return False
    return (a.target.best.kind, a.target.best.value) == (b.target.best.kind, b.target.best.value)


def drop_focus_clicks(events: Sequence[Event]) -> tuple[Event, ...]:
    """Drop the click that only places the cursor in a field before typing."""
    out: list[Event] = []
    for i, ev in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        if (
            ev.action is Action.CLICK
            and nxt is not None
            and nxt.action in _TYPING_ACTIONS
            and _same_target(ev, nxt)
            and nxt.ts - ev.ts <= FOCUS_CLICK_GAP
        ):
            continue
        out.append(ev)
    return tuple(out)


def coalesce_typing(events: Sequence[Event]) -> tuple[Event, ...]:
    """Merge consecutive keystrokes in one field into a single `fill` with the final value.

    If any event in the group is a secret, the result is a secret - never the other way
    around. A field that became a password field halfway through typing stays treated
    as a password.
    """
    out: list[Event] = []
    for ev in events:
        prev = out[-1] if out else None
        if (
            prev is not None
            and ev.action is Action.FILL
            and prev.action is Action.FILL
            and _same_target(prev, ev)
        ):
            if prev.is_secret or ev.is_secret:
                out[-1] = replace(
                    prev,
                    value=None,
                    is_secret=True,
                    secret_ref=prev.secret_ref or ev.secret_ref,
                    ts=ev.ts,
                )
            else:
                out[-1] = replace(prev, value=ev.value, ts=ev.ts)
            continue
        out.append(ev)
    return tuple(out)


def insert_waits(events: Sequence[Event], threshold: float = WAIT_THRESHOLD) -> tuple[Event, ...]:
    """Turn long pauses into semantic waits.

    Recorded timing must never become `time.sleep()` in the final code: it works on the
    machine that recorded it and breaks on everyone else's network. A long pause before
    acting on an element means "I was waiting for this to show up" - and that is written
    as a wait for the element itself.
    """
    out: list[Event] = []
    for i, ev in enumerate(events):
        prev = events[i - 1] if i else None
        if (
            prev is not None
            and ev.target is not None
            and ev.action is not Action.WAIT
            and prev.action in _CAUSES_PAGE_CHANGE
            and ev.ts - prev.ts > threshold
        ):
            out.append(
                Event(
                    id=0,
                    ts=prev.ts + threshold,
                    action=Action.WAIT,
                    layer=ev.layer,
                    target=ev.target,
                    context={"reason": "long pause while recording"},
                )
            )
        out.append(ev)
    return tuple(out)


def normalize_secret_refs(events: Sequence[Event]) -> tuple[Event, ...]:
    """Normalize the environment variable names used for secrets.

    Takes whatever the recorder could infer (the field name, its id, its label) and
    turns it into a valid, predictable name: `ENCORE_PASSWORD`, `ENCORE_PIN`.
    """
    out: list[Event] = []
    for ev in events:
        if not ev.is_secret or not ev.secret_ref:
            out.append(ev)
            continue
        ref = _SECRET_REF_INVALID.sub("_", ev.secret_ref.upper()).strip("_")
        if not ref:
            ref = f"SECRET_{ev.id}"
        if not ref.startswith("ENCORE_"):
            ref = f"ENCORE_{ref}"
        out.append(replace(ev, secret_ref=ref) if ref != ev.secret_ref else ev)
    return tuple(out)


def renumber(events: Sequence[Event]) -> tuple[Event, ...]:
    """Reassign sequential ids from 1, after insertions and removals."""
    return tuple(replace(ev, id=i) for i, ev in enumerate(events, start=1))
