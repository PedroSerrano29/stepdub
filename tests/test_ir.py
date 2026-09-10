"""The IR must reject invalid events in the constructor, not later."""

from __future__ import annotations

import pytest

from stepdub.ir import (
    DEFAULT_SCORE,
    Action,
    Event,
    Selector,
    SelectorKind,
    Target,
    split_role,
)


def a_target(*cands: Selector) -> Target:
    return Target(candidates=cands or (Selector(SelectorKind.ID, "x"),))


class TestSelector:
    def test_default_score_comes_from_the_kind(self):
        assert Selector(SelectorKind.TESTID, "a").score == DEFAULT_SCORE[SelectorKind.TESTID]

    def test_a_recorder_can_lower_the_score(self):
        assert Selector(SelectorKind.ID, "ember1234", score=10).score == 10

    def test_empty_value_is_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            Selector(SelectorKind.ID, "")

    def test_out_of_range_score_is_rejected(self):
        with pytest.raises(ValueError, match="out of range 0-100"):
            Selector(SelectorKind.ID, "a", score=101)

    def test_kind_as_text_is_coerced(self):
        assert Selector("css", "div").kind is SelectorKind.CSS  # type: ignore[arg-type]


class TestTarget:
    def test_candidates_are_sorted_by_score(self):
        t = Target(
            candidates=(
                Selector(SelectorKind.CSS, "div"),
                Selector(SelectorKind.TESTID, "btn"),
                Selector(SelectorKind.ROLE, "button|Ok"),
            )
        )
        assert [c.kind for c in t.candidates] == [
            SelectorKind.TESTID,
            SelectorKind.ROLE,
            SelectorKind.CSS,
        ]
        assert t.best.kind is SelectorKind.TESTID
        assert len(t.fallbacks) == 2

    def test_sorting_is_stable_for_equal_scores(self):
        a = Selector(SelectorKind.CSS, "first")
        b = Selector(SelectorKind.CSS, "second")
        assert Target(candidates=(a, b)).candidates == (a, b)

    def test_target_without_candidates_is_rejected(self):
        with pytest.raises(ValueError, match="at least one Selector"):
            Target(candidates=())

    def test_text_preview_is_truncated(self):
        t = Target(candidates=(Selector(SelectorKind.ID, "x"),), text_preview="a" * 200)
        assert len(t.text_preview) == 80


class TestSecretInvariant:
    """The rule that protects whoever uses the tool."""

    def test_a_secret_carrying_a_value_raises(self):
        with pytest.raises(ValueError, match="never enters the IR"):
            Event(
                id=1,
                ts=0.0,
                action=Action.FILL,
                target=a_target(),
                value="p4ssw0rd",
                is_secret=True,
                secret_ref="STEPDUB_PASSWORD",
            )

    def test_a_secret_without_a_ref_raises(self):
        with pytest.raises(ValueError, match="needs secret_ref"):
            Event(id=1, ts=0.0, action=Action.FILL, target=a_target(), is_secret=True)

    def test_a_ref_without_the_secret_flag_raises(self):
        with pytest.raises(ValueError, match="secret_ref without is_secret"):
            Event(
                id=1,
                ts=0.0,
                action=Action.FILL,
                target=a_target(),
                value="hi",
                secret_ref="STEPDUB_X",
            )

    def test_a_valid_secret_is_accepted(self):
        ev = Event(
            id=1,
            ts=0.0,
            action=Action.FILL,
            target=a_target(),
            is_secret=True,
            secret_ref="STEPDUB_PASSWORD",
        )
        assert ev.value is None
        assert "p4ssw0rd" not in str(ev.to_dict())


class TestEvent:
    def test_an_action_needing_a_target_without_one_raises(self):
        with pytest.raises(ValueError, match="needs a target"):
            Event(id=1, ts=0.0, action=Action.CLICK)

    def test_navigate_without_a_url_raises(self):
        with pytest.raises(ValueError, match="navigate needs the URL"):
            Event(id=1, ts=0.0, action=Action.NAVIGATE)

    def test_press_without_a_target_is_valid(self):
        assert Event(id=1, ts=0.0, action=Action.PRESS, value="Enter").target is None

    def test_negative_ts_raises(self):
        with pytest.raises(ValueError, match="ts must not be negative"):
            Event(id=1, ts=-1.0, action=Action.PRESS, value="Enter")

    def test_roundtrip_preserves_everything(self):
        original = Event(
            id=7,
            ts=1.234,
            action=Action.CLICK,
            target=Target(
                candidates=(
                    Selector(SelectorKind.ROLE, "button|Sign in"),
                    Selector(SelectorKind.CSS, "form > button"),
                ),
                tag="button",
                text_preview="Sign in",
                frame_url="https://example.com/frame",
            ),
            context={"url": "https://example.com"},
        )
        assert Event.from_dict(original.to_dict()) == original

    def test_roundtrip_of_a_secret_invents_no_value(self):
        original = Event(
            id=2,
            ts=0.5,
            action=Action.FILL,
            target=a_target(),
            is_secret=True,
            secret_ref="STEPDUB_PASSWORD",
        )
        assert Event.from_dict(original.to_dict()) == original

    def test_non_ascii_values_survive_serialization(self):
        ev = Event(id=1, ts=0.0, action=Action.PRESS, value="café")
        assert Event.from_dict(ev.to_dict()).value == "café"


def test_split_role():
    assert split_role("button|Sign in") == ("button", "Sign in")
    assert split_role("searchbox") == ("searchbox", "")
