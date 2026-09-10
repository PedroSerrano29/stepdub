"""The generator must be deterministic and must never leak a secret.

The golden file in `tests/golden/` is the safety net: any change in the generator
shows up as a readable diff instead of slipping through unnoticed.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from stepdub import session
from stepdub.codegen import GenOptions, generate
from stepdub.ir import Action, Event, Selector, SelectorKind, Target
from stepdub.session import Recording, RecordingMeta
from stepdub.transforms import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"

PARAMS = {6: "term"}


@pytest.fixture
def recording() -> Recording:
    rec = session.load("login_search", root=FIXTURES)
    return replace(rec, events=run_pipeline(rec.events))


def gen(rec: Recording, **kw: object) -> str:
    return generate(rec, GenOptions(**kw))  # type: ignore[arg-type]


def one_event_recording(*events: Event) -> Recording:
    return Recording(meta=RecordingMeta(name="x", slug="x"), events=events)


class TestGolden:
    def test_output_matches_the_golden_file(self, recording):
        expected = (GOLDEN / "login_search.py.expected").read_text(encoding="utf-8")
        assert gen(recording, params=PARAMS) == expected

    def test_output_is_deterministic(self, recording):
        assert gen(recording, params=PARAMS) == gen(recording, params=PARAMS)

    def test_the_header_has_no_date_and_no_absolute_path(self, recording):
        """If it did, the golden file would change on every run and stop being useful."""
        header = gen(recording, params=PARAMS).split('"""')[1]
        assert "2026" not in header
        assert "C:" not in header


class TestValidCode:
    def test_the_generated_file_is_valid_python(self, recording):
        ast.parse(gen(recording, params=PARAMS))

    def test_no_line_exceeds_100_characters(self, recording):
        code = gen(recording, params=PARAMS)
        assert [ln for ln in code.splitlines() if len(ln) > 100] == []

    def test_no_main_generates_the_function_only(self, recording):
        code = gen(recording, params=PARAMS, include_main=False)
        assert "def main()" not in code
        assert "sync_playwright" not in code

    def test_the_function_name_is_configurable(self, recording):
        assert "def download_fleet(" in gen(recording, function_name="download_fleet")


class TestSecrets:
    def test_a_secret_value_never_appears_in_the_code(self, recording):
        code = gen(recording, params=PARAMS)
        assert "p4ssw0rd" not in code
        assert 'os.environ["STEPDUB_PASSWORD"]' in code

    def test_the_header_states_which_variables_are_needed(self, recording):
        assert "STEPDUB_PASSWORD" in gen(recording, params=PARAMS).split('"""')[1]

    def test_without_secrets_os_is_not_imported(self):
        rec = one_event_recording(
            Event(id=1, ts=0.0, action=Action.NAVIGATE, value="https://example.com")
        )
        assert "import os" not in gen(rec)


class TestWaits:
    def test_time_sleep_is_never_generated(self, recording):
        code = gen(recording, params=PARAMS)
        assert "sleep" not in code
        assert "import time" not in code

    def test_the_pause_becomes_expect_to_be_visible(self, recording):
        assert "expect(search_fleet).to_be_visible()" in gen(recording, params=PARAMS)


class TestParameters:
    def test_a_recorded_value_becomes_a_function_parameter(self, recording):
        code = gen(recording, params=PARAMS)
        assert "term: str" in code
        assert ".fill(term)" in code

    def test_without_a_parameter_the_value_stays_literal(self, recording):
        code = gen(recording)
        assert '.fill("café tables")' in code
        assert "term: str" not in code

    def test_main_uses_the_recorded_value_as_an_example(self, recording):
        assert 'term="café tables"' in gen(recording, params=PARAMS)


class TestRepeatedLocators:
    def test_a_locator_used_twice_becomes_a_variable(self, recording):
        code = gen(recording, params=PARAMS)
        assert 'search_fleet = page.get_by_role("searchbox", name="Search fleet")' in code
        assert code.count('get_by_role("searchbox"') == 1

    def test_a_locator_used_once_stays_inline(self, recording):
        code = gen(recording, params=PARAMS)
        assert 'page.get_by_label("Username").fill("pedro")' in code

    def test_a_variable_never_collides_with_a_reserved_name(self):
        """A button labelled "page" must not generate a variable called `page`."""
        target = Target(candidates=(Selector(SelectorKind.TEXT, "page"),))
        rec = one_event_recording(
            Event(id=1, ts=0.0, action=Action.WAIT, target=target),
            Event(id=2, ts=1.0, action=Action.CLICK, target=target),
        )
        assert "page_2 = page.get_by_text" in gen(rec)

    def test_a_variable_never_collides_with_a_parameter_name(self):
        target = Target(candidates=(Selector(SelectorKind.TEXT, "term"),))
        rec = one_event_recording(
            Event(id=1, ts=0.0, action=Action.WAIT, target=target),
            Event(id=2, ts=1.0, action=Action.FILL, target=target, value="x"),
        )
        code = gen(rec, params={2: "term"})
        assert "term_2 = page.get_by_text" in code
        assert "term_2.fill(term)" in code


class TestFallbackHint:
    def test_a_weak_selector_gets_a_hint_with_alternatives(self, recording):
        assert "# if this breaks, try: css=div.toolbar" in gen(recording, params=PARAMS)

    def test_the_hint_appears_once_per_target(self, recording):
        code = gen(recording, params=PARAMS)
        assert code.count("if this breaks, try: css=div.toolbar") == 1

    def test_a_strong_selector_gets_no_hint(self, recording):
        lines = gen(recording, params=PARAMS).splitlines()
        i = next(i for i, ln in enumerate(lines) if "#btn-signin" in ln)
        assert "if this breaks" not in lines[i - 1]


class TestDownloads:
    def test_the_click_that_downloads_is_wrapped_in_the_download_wait(self, recording):
        code = gen(recording, params=PARAMS)
        assert "with page.expect_download() as download_info:" in code
        assert "download.save_as(destination)" in code
        assert "return downloaded" in code

    def test_without_downloads_path_is_not_imported(self):
        target = Target(candidates=(Selector(SelectorKind.TESTID, "ok"),))
        code = gen(one_event_recording(Event(id=1, ts=0.0, action=Action.CLICK, target=target)))
        assert "from pathlib import Path" not in code
        assert "-> None:" in code
        assert "return" not in code


class TestFrames:
    def test_an_element_inside_an_iframe_resolves_the_frame_first(self):
        target = Target(
            candidates=(Selector(SelectorKind.TESTID, "ok"),),
            frame_url="https://example.com/widget",
        )
        code = gen(one_event_recording(Event(id=1, ts=0.0, action=Action.CLICK, target=target)))
        assert 'frame_1 = page.frame(url="https://example.com/widget")' in code
        assert "if frame_1 is None:" in code
        assert 'frame_1.get_by_test_id("ok").click()' in code


class TestGracefulDegradation:
    def test_an_unsupported_action_stays_visible_in_the_code(self):
        target = Target(candidates=(Selector(SelectorKind.TESTID, "form"),))
        code = gen(one_event_recording(Event(id=1, ts=0.0, action=Action.SUBMIT, target=target)))
        assert "# step not supported by this generator: submit" in code
        assert ast.parse(code)

    def test_coordinates_generate_a_pixel_click(self):
        target = Target(candidates=(Selector(SelectorKind.COORDS, "847,312"),))
        code = gen(one_event_recording(Event(id=1, ts=0.0, action=Action.CLICK, target=target)))
        assert "page.mouse.click(847, 312)" in code
