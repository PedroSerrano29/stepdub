"""The passes are pure functions - no browser, no disk, no network needed."""

from __future__ import annotations

from pathlib import Path

from stepdub import session
from stepdub.ir import Action, Event, Selector, SelectorKind, Target
from stepdub.transforms import (
    DEFAULT_PIPELINE,
    coalesce_typing,
    drop_focus_clicks,
    insert_waits,
    normalize_secret_refs,
    renumber,
    run_pipeline,
)

FIXTURES = Path(__file__).parent / "fixtures"


def field(name: str) -> Target:
    return Target(candidates=(Selector(SelectorKind.LABEL, name),), tag="input")


def button(name: str) -> Target:
    return Target(candidates=(Selector(SelectorKind.ROLE, f"button|{name}"),))


def ev(
    id_: int,
    ts: float,
    action: Action,
    target: Target | None = None,
    value: str | None = None,
    **kw: object,
) -> Event:
    return Event(id=id_, ts=ts, action=action, target=target, value=value, **kw)  # type: ignore[arg-type]


class TestDropFocusClicks:
    def test_a_click_before_typing_in_the_same_field_disappears(self):
        username = field("Username")
        given = [
            ev(1, 1.0, Action.CLICK, username),
            ev(2, 1.2, Action.FILL, username, "pedro"),
        ]
        assert [e.action for e in drop_focus_clicks(given)] == [Action.FILL]

    def test_a_click_on_a_different_field_stays(self):
        given = [
            ev(1, 1.0, Action.CLICK, field("Username")),
            ev(2, 1.2, Action.FILL, field("Password"), "x"),
        ]
        assert len(drop_focus_clicks(given)) == 2

    def test_a_click_long_before_the_typing_stays(self):
        username = field("Username")
        given = [
            ev(1, 1.0, Action.CLICK, username),
            ev(2, 9.0, Action.FILL, username, "pedro"),
        ]
        assert len(drop_focus_clicks(given)) == 2

    def test_a_click_on_a_button_is_never_dropped(self):
        given = [ev(1, 1.0, Action.CLICK, button("Sign in"))]
        assert drop_focus_clicks(given) == tuple(given)


class TestCoalesceTyping:
    def test_consecutive_keystrokes_become_one_fill_with_the_final_value(self):
        f = field("Username")
        given = [
            ev(1, 1.0, Action.FILL, f, "p"),
            ev(2, 1.1, Action.FILL, f, "pe"),
            ev(3, 1.2, Action.FILL, f, "pedro"),
        ]
        out = coalesce_typing(given)
        assert len(out) == 1
        assert out[0].value == "pedro"
        assert out[0].ts == 1.2

    def test_different_fields_are_not_merged(self):
        given = [
            ev(1, 1.0, Action.FILL, field("A"), "x"),
            ev(2, 1.1, Action.FILL, field("B"), "y"),
        ]
        assert len(coalesce_typing(given)) == 2

    def test_a_secret_contaminates_the_group_and_never_the_reverse(self):
        """If the field became a password field halfway, the result is still a secret."""
        f = field("Password")
        given = [
            ev(1, 1.0, Action.FILL, f, "vis"),
            ev(2, 1.1, Action.FILL, f, None, is_secret=True, secret_ref="password"),
        ]
        out = coalesce_typing(given)
        assert len(out) == 1
        assert out[0].is_secret is True
        assert out[0].value is None
        assert out[0].secret_ref == "password"

    def test_a_click_in_between_breaks_the_merge(self):
        f = field("Username")
        given = [
            ev(1, 1.0, Action.FILL, f, "p"),
            ev(2, 1.1, Action.CLICK, button("Ok")),
            ev(3, 1.2, Action.FILL, f, "pedro"),
        ]
        assert len(coalesce_typing(given)) == 3


class TestInsertWaits:
    def test_a_long_pause_after_a_click_becomes_a_wait(self):
        result = field("Search")
        given = [
            ev(1, 0.0, Action.CLICK, button("Sign in")),
            ev(2, 5.0, Action.FILL, result, "x"),
        ]
        out = insert_waits(given)
        assert [e.action for e in out] == [Action.CLICK, Action.WAIT, Action.FILL]
        assert out[1].target == result

    def test_a_short_pause_produces_no_wait(self):
        given = [
            ev(1, 0.0, Action.CLICK, button("Sign in")),
            ev(2, 0.5, Action.FILL, field("A"), "x"),
        ]
        assert len(insert_waits(given)) == 2

    def test_no_sleep_is_ever_produced_and_the_pause_is_not_stored(self):
        """Recorded timing does not enter the code - the wait is for the element."""
        given = [
            ev(1, 0.0, Action.CLICK, button("Sign in")),
            ev(2, 30.0, Action.FILL, field("A"), "x"),
        ]
        wait = insert_waits(given)[1]
        assert wait.value is None
        assert wait.target is not None

    def test_pressing_enter_counts_as_something_worth_waiting_after(self):
        given = [
            ev(1, 0.0, Action.PRESS, None, "Enter"),
            ev(2, 6.0, Action.CLICK, button("Export")),
        ]
        assert [e.action for e in insert_waits(given)] == [
            Action.PRESS,
            Action.WAIT,
            Action.CLICK,
        ]


class TestNormalizeSecretRefs:
    def test_the_field_name_becomes_an_environment_variable(self):
        e = ev(1, 0.0, Action.FILL, field("P"), None, is_secret=True, secret_ref="user-password")
        assert normalize_secret_refs([e])[0].secret_ref == "STEPDUB_USER_PASSWORD"

    def test_the_prefix_is_not_doubled(self):
        e = ev(1, 0.0, Action.FILL, field("P"), None, is_secret=True, secret_ref="STEPDUB_PIN")
        assert normalize_secret_refs([e])[0].secret_ref == "STEPDUB_PIN"

    def test_ordinary_events_are_untouched(self):
        e = ev(1, 0.0, Action.FILL, field("A"), "visible")
        assert normalize_secret_refs([e])[0] is e


class TestRenumber:
    def test_ids_become_sequential_from_one(self):
        given = [
            ev(9, 0.0, Action.PRESS, None, "Enter"),
            ev(4, 1.0, Action.PRESS, None, "Tab"),
        ]
        assert [e.id for e in renumber(given)] == [1, 2]


class TestFullPipeline:
    def load(self) -> tuple[Event, ...]:
        return session.load("login_search", root=FIXTURES).events

    def test_the_raw_recording_has_the_expected_noise(self):
        assert len(self.load()) == 12

    def test_the_pipeline_produces_the_clean_sequence(self):
        out = run_pipeline(self.load())
        assert [e.action for e in out] == [
            Action.NAVIGATE,
            Action.FILL,  # username (3 keystrokes merged)
            Action.FILL,  # password (secret)
            Action.CLICK,  # sign in
            Action.WAIT,  # a 5s pause became a wait for the search box
            Action.FILL,  # search term
            Action.PRESS,  # Enter
            Action.WAIT,  # a 4s pause became a wait for the export link
            Action.CLICK,  # export CSV
            Action.DOWNLOAD,
        ]
        assert [e.id for e in out] == list(range(1, 11))

    def test_the_secret_survives_the_pipeline_without_a_value(self):
        secrets = [e for e in run_pipeline(self.load()) if e.is_secret]
        assert len(secrets) == 1
        assert secrets[0].value is None
        assert secrets[0].secret_ref == "STEPDUB_PASSWORD"

    def test_the_pipeline_does_not_mutate_its_input(self):
        given = list(self.load())
        copy = list(given)
        run_pipeline(given)
        assert given == copy

    def test_the_pipeline_is_idempotent_on_a_second_pass(self):
        once = run_pipeline(self.load())
        assert run_pipeline(once) == once

    def test_the_default_pipeline_has_the_documented_order(self):
        assert [p.__name__ for p in DEFAULT_PIPELINE] == [
            "drop_focus_clicks",
            "coalesce_typing",
            "insert_waits",
            "normalize_secret_refs",
            "renumber",
        ]
