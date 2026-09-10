"""The web recorder's pure parts, testable with no browser installed.

The half of the recorder that converts page payloads into IR events is ordinary pure
code, and it is where a privacy mistake would land. It gets tested like everything
else. The half that drives Playwright is not covered here - that needs a browser.
"""

from __future__ import annotations

import pytest

from stepdub.ir import Action, SelectorKind
from stepdub.recorders import web
from stepdub.session import RecordingMeta, RecordingWriter


def payload(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "action": "click",
        "target": {
            "candidates": [
                {"kind": "role", "value": "button|Sign in", "score": 80},
                {"kind": "css", "value": "form > button", "score": 30},
            ],
            "tag": "button",
            "text_preview": "Sign in",
        },
        "context": {"url": "https://example.com"},
    }
    base.update(kw)
    return base


class TestNoBrowserNeeded:
    def test_importing_the_recorder_does_not_require_playwright(self):
        """The module is imported by the CLI on every run, including `stepdub list`."""
        assert web.BINDING == "__stepdub_emit"

    def test_a_missing_playwright_produces_installation_instructions(self, monkeypatch):
        def explode(*_a: object, **_k: object) -> None:
            raise ImportError("no playwright")

        monkeypatch.setattr(web, "_load_playwright", explode)
        with pytest.raises(ImportError):
            web._load_playwright()


class TestInjectedAsset:
    def test_the_javascript_ships_with_the_package(self):
        """A packaging mistake that drops this file would break recording silently."""
        assert web.INJECTED_JS.exists()

    def test_it_carries_the_privacy_guards(self):
        js = web.INJECTED_JS.read_text(encoding="utf-8")
        assert "isSecret" in js
        assert "__stepdub_installed" in js
        assert "capture" in js

    def test_the_recording_indicator_exists(self):
        assert "__stepdub_banner" in web.INJECTED_JS.read_text(encoding="utf-8")


class TestTargetFromPayload:
    def test_candidates_become_selectors_sorted_by_score(self):
        target = web.target_from_payload(payload())
        assert target is not None
        assert target.best.kind is SelectorKind.ROLE
        assert target.tag == "button"

    def test_no_target_key_gives_none(self):
        assert web.target_from_payload({"action": "press"}) is None

    def test_an_empty_candidate_list_gives_none(self):
        assert web.target_from_payload({"target": {"candidates": []}}) is None

    def test_candidates_with_empty_values_are_dropped(self):
        data = {"target": {"candidates": [{"kind": "css", "value": ""}]}}
        assert web.target_from_payload(data) is None

    def test_a_partially_empty_candidate_list_keeps_the_rest(self):
        data = {
            "target": {
                "candidates": [
                    {"kind": "label", "value": ""},
                    {"kind": "css", "value": "div"},
                ]
            }
        }
        target = web.target_from_payload(data)
        assert target is not None
        assert len(target.candidates) == 1


class TestEventFromPayload:
    def test_a_click_becomes_a_click_event(self):
        ev = web.event_from_payload(payload(), 3, 1.5)
        assert ev.id == 3
        assert ev.ts == 1.5
        assert ev.action is Action.CLICK
        assert ev.layer == "dom"

    def test_a_secret_payload_carrying_a_value_has_it_dropped(self):
        """Defence in depth: injected.js should never send this, and if it does, it dies here."""
        ev = web.event_from_payload(
            payload(action="fill", is_secret=True, secret_ref="password", value="p4ssw0rd"),
            1,
            0.0,
        )
        assert ev.is_secret is True
        assert ev.value is None
        assert "p4ssw0rd" not in str(ev.to_dict())

    def test_a_non_secret_fill_keeps_its_value(self):
        ev = web.event_from_payload(payload(action="fill", value="pedro"), 1, 0.0)
        assert ev.value == "pedro"
        assert ev.secret_ref is None

    def test_a_secret_ref_on_a_non_secret_payload_is_ignored(self):
        ev = web.event_from_payload(payload(action="fill", value="x", secret_ref="nope"), 1, 0.0)
        assert ev.secret_ref is None

    def test_press_needs_no_target(self):
        ev = web.event_from_payload({"action": "press", "value": "Enter"}, 1, 0.0)
        assert ev.target is None
        assert ev.value == "Enter"

    def test_an_unknown_action_raises(self):
        with pytest.raises(ValueError, match="not a valid Action"):
            web.event_from_payload({"action": "levitate"}, 1, 0.0)


class TestSessionRecording:
    def writer(self, tmp_path) -> RecordingWriter:
        return RecordingWriter(RecordingMeta.new("T", "t"), root=tmp_path)

    def test_events_are_numbered_in_arrival_order(self, tmp_path):
        with self.writer(tmp_path) as w:
            sess = web._Session(w)
            sess.record(payload())
            sess.record(payload())
            assert w.count == 2
            assert sess.next_id == 3

    def test_one_bad_event_does_not_kill_the_recording(self, tmp_path):
        with self.writer(tmp_path) as w:
            sess = web._Session(w)
            sess.record({"action": "levitate"})
            sess.record(payload())
            assert w.count == 1
            assert sess.warnings

    def test_a_secret_without_a_ref_is_skipped_with_a_warning(self, tmp_path):
        """The IR refuses it; the recorder must survive that refusal."""
        with self.writer(tmp_path) as w:
            sess = web._Session(w)
            sess.record(payload(action="fill", is_secret=True))
            assert w.count == 0
            assert "needs secret_ref" in sess.warnings[0]

    def test_navigate_and_download_helpers(self, tmp_path):
        with self.writer(tmp_path) as w:
            sess = web._Session(w)
            sess.record_navigate("https://example.com")
            sess.record_download("results.csv")
            assert w.count == 2

    def test_timestamps_are_relative_to_the_recording_start(self, tmp_path):
        with self.writer(tmp_path) as w:
            sess = web._Session(w)
            assert sess.ts() < 1.0
