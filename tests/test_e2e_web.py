"""End-to-end: a real browser, a real page, a real recording, real generated code.

Everything else in the suite runs without a browser. This file is the exception, and
it is skipped when Playwright is not installed, which is also how CI runs. It exists
because the recorder is the one part of stepdub that cannot be proven correct by
reasoning about pure functions: either the injected JS really produces good selectors
in a live DOM, or it does not.

Run it with:  pytest tests/test_e2e_web.py
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="needs: pip install playwright && playwright install")

from playwright.sync_api import expect, sync_playwright

from stepdub import session
from stepdub.codegen import GenOptions, generate
from stepdub.ir import Action, SelectorKind, page_of
from stepdub.recorders.web import WebRecorder
from stepdub.session import Recording, RecordingMeta, RecordingWriter
from stepdub.transforms import run_pipeline

PAGE = (Path(__file__).parent / "pages" / "login.html").resolve().as_uri()

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Recording:
    """Drive the test page once, and let every assertion read the same recording."""
    root = tmp_path_factory.mktemp("recordings")
    meta = RecordingMeta.new("e2e", "e2e", start_url=PAGE)

    with RecordingWriter(meta, root=root) as writer, WebRecorder(writer, headless=True) as rec:
        rec.open(PAGE)
        page = rec.page

        page.get_by_label("Username").click()
        page.get_by_label("Username").fill("pedro")
        page.get_by_label("Password").fill(PASSWORD)
        page.locator("#btn-signin").click()

        # The page reveals the app after 2.4s, so this wait is a real pause the
        # recorder should turn into a semantic wait.
        page.get_by_placeholder("Search fleet").wait_for(state="visible", timeout=10_000)

        page.get_by_placeholder("Search fleet").fill("van")
        page.locator("#cat").select_option("vans")
        page.locator("#ember1234").check()

        with page.expect_download() as info:
            page.get_by_text("Export CSV", exact=True).click()
        assert info.value.suggested_filename == "results.csv"

        rec.pump(600)

    return session.load("e2e", root=root)


class TestTheRecordingHappened:
    def test_events_were_captured(self, recorded):
        assert len(recorded.events) > 5

    def test_it_starts_with_the_navigation(self, recorded):
        assert recorded.events[0].action is Action.NAVIGATE
        assert recorded.events[0].value == PAGE

    def test_the_expected_actions_are_all_present(self, recorded):
        actions = {e.action for e in recorded.events}
        assert {Action.NAVIGATE, Action.CLICK, Action.FILL} <= actions
        assert Action.DOWNLOAD in actions
        assert Action.CHECK in actions

    def test_the_download_is_recorded_once(self, recorded):
        """The context's page event fires for the first page too; wiring it twice doubled this."""
        assert [e.action for e in recorded.events].count(Action.DOWNLOAD) == 1

    def test_no_warnings_were_produced(self, recorded):
        """A warning here means the injected JS sent something the IR refused."""
        assert all(e.action is not Action.SUBMIT for e in recorded.events)


class TestThePasswordNeverLeftTheBrowser:
    """The test this whole project has to pass."""

    def test_the_password_is_not_in_any_event(self, recorded):
        assert PASSWORD not in session.dumps(recorded.events)

    def test_the_password_field_was_recorded_as_a_secret(self, recorded):
        secrets = [e for e in recorded.events if e.is_secret]
        assert secrets, "the password field was not detected"
        assert all(e.value is None for e in secrets)
        assert secrets[0].secret_ref

    def test_the_password_is_not_in_any_selector_or_preview(self, recorded):
        """The side channel: an input's value used as an accessible name."""
        for ev in recorded.events:
            if ev.target is None:
                continue
            assert PASSWORD not in ev.target.text_preview
            for sel in ev.target.candidates:
                assert PASSWORD not in sel.value

    def test_the_password_is_not_in_the_generated_code(self, recorded):
        cleaned = replace(recorded, events=run_pipeline(recorded.events))
        assert PASSWORD not in generate(cleaned)


class TestSelectorQuality:
    def test_a_labelled_field_is_found_by_its_label_or_role(self, recorded):
        fills = [e for e in recorded.events if e.action is Action.FILL and e.value == "pedro"]
        assert fills
        kinds = {c.kind for c in fills[0].target.candidates}
        assert kinds & {SelectorKind.LABEL, SelectorKind.ROLE, SelectorKind.ID}

    def test_a_machine_looking_id_is_scored_down(self, recorded):
        """#ember1234 must not win over a real label."""
        checks = [e for e in recorded.events if e.action is Action.CHECK]
        assert checks
        ids = [c for c in checks[0].target.candidates if c.kind is SelectorKind.ID]
        assert ids, "the id was dropped entirely"
        assert ids[0].score <= 10

    def test_duplicated_link_text_is_not_offered_as_a_selector(self, recorded):
        """The page has two "Details" links, so "Details" is an ambiguity."""
        for ev in recorded.events:
            if ev.target is None:
                continue
            texts = [c.value for c in ev.target.candidates if c.kind is SelectorKind.TEXT]
            assert "Details" not in texts

    def test_unique_link_text_is_offered(self, recorded):
        clicks = [
            e
            for e in recorded.events
            if e.target is not None and e.target.text_preview == "Export CSV"
        ]
        assert clicks
        texts = [c.value for c in clicks[0].target.candidates if c.kind is SelectorKind.TEXT]
        assert texts == ["Export CSV"]

    def test_every_target_has_at_least_one_candidate(self, recorded):
        for ev in recorded.events:
            if ev.target is not None:
                assert ev.target.candidates


class TestTheGeneratedCodeIsUsable:
    def test_it_is_valid_python(self, recorded):
        import ast

        cleaned = replace(recorded, events=run_pipeline(recorded.events))
        ast.parse(generate(cleaned, GenOptions(params={})))

    def test_the_long_pause_became_a_semantic_wait_not_a_sleep(self, recorded):
        cleaned = replace(recorded, events=run_pipeline(recorded.events))
        code = generate(cleaned)
        assert "sleep" not in code
        assert "to_be_visible()" in code

    def test_the_download_is_wrapped_in_expect_download(self, recorded):
        cleaned = replace(recorded, events=run_pipeline(recorded.events))
        assert "page.expect_download()" in generate(cleaned)

    def test_the_secret_becomes_an_environment_variable(self, recorded):
        cleaned = replace(recorded, events=run_pipeline(recorded.events))
        assert "os.environ[" in generate(cleaned)


class TestTheRecordingIndicator:
    def test_the_banner_is_injected_into_the_page(self, tmp_path):
        """No silent mode: while recording, the page says so."""
        meta = RecordingMeta.new("banner", "banner", start_url=PAGE)
        with RecordingWriter(meta, root=tmp_path) as w, WebRecorder(w, headless=True) as rec:
            rec.open(PAGE)
            rec.pump(200)
            banner = rec.page.locator("#__stepdub_banner")
            assert banner.count() == 1
            assert "recording" in banner.inner_text()

    def test_the_banner_survives_a_reload(self, tmp_path):
        """add_init_script, not evaluate: this is the difference."""
        meta = RecordingMeta.new("reload", "reload", start_url=PAGE)
        with RecordingWriter(meta, root=tmp_path) as w, WebRecorder(w, headless=True) as rec:
            rec.open(PAGE)
            rec.page.reload()
            rec.pump(200)
            assert rec.page.locator("#__stepdub_banner").count() == 1


class TestLifecycle:
    def test_using_the_page_outside_the_with_block_raises(self, tmp_path):
        from stepdub.recorders.web import RecorderError

        meta = RecordingMeta.new("x", "x")
        with RecordingWriter(meta, root=tmp_path) as w:
            rec = WebRecorder(w, headless=True)
            with pytest.raises(RecorderError, match="outside its `with`"):
                _ = rec.page

    def test_an_unknown_browser_fails_with_a_readable_message(self, tmp_path):
        from stepdub.recorders.web import RecorderError

        meta = RecordingMeta.new("x", "x")
        with RecordingWriter(meta, root=tmp_path) as w:  # noqa: SIM117
            with pytest.raises(RecorderError, match="unknown browser"):
                with WebRecorder(w, browser="netscape", headless=True):
                    pass


TABS = (Path(__file__).parent / "pages" / "tabs.html").resolve().as_uri()
REPORT = (Path(__file__).parent / "pages" / "report.html").resolve().as_uri()
EDITOR = (Path(__file__).parent / "pages" / "editor.html").resolve().as_uri()

# Longer than the 80 characters a text preview is capped at, on purpose.
COMMENT = (
    "Looks good to me, and the numbers on page two match the export we ran last week. Ship it."
)


@pytest.fixture(scope="module")
def tabs(tmp_path_factory) -> dict[str, object]:
    """Open a report in a new tab, close the first tab, and finish the work in the report."""
    root = tmp_path_factory.mktemp("tabs")
    meta = RecordingMeta.new("tabs", "tabs", start_url=TABS)

    with RecordingWriter(meta, root=root) as writer, WebRecorder(writer, headless=True) as rec:
        rec.open(TABS)
        with rec.page.expect_popup() as info:
            rec.page.get_by_text("Open report").click()
        report = info.value
        report.get_by_label("Note").fill("hello")
        banner = report.locator("#__stepdub_banner").count()

        rec.page.close()
        still_recording = rec.is_open()
        rec.pump(200)
        report.get_by_role("button", name="Save").click()
        rec.pump(400)

    return {
        "recording": session.load("tabs", root=root),
        "banner": banner,
        "still_recording": still_recording,
    }


class TestTabs:
    def test_recording_survives_closing_the_first_tab(self, tabs):
        assert tabs["still_recording"] is True

    def test_the_new_tab_shows_the_recording_indicator(self, tabs):
        """No silent mode in a second tab either."""
        assert tabs["banner"] == 1

    def test_every_step_is_recorded_on_the_page_it_happened_in(self, tabs):
        steps = [(e.action, page_of(e)) for e in tabs["recording"].events]
        assert steps == [
            (Action.NAVIGATE, 1),
            (Action.CLICK, 1),
            (Action.POPUP, 2),
            (Action.FILL, 2),
            (Action.CLICK, 2),
        ]

    def test_the_popup_knows_which_page_opened_it(self, tabs):
        popup = next(e for e in tabs["recording"].events if e.action is Action.POPUP)
        assert popup.context["opener"] == 1


@pytest.fixture(scope="module")
def manual_tab(tmp_path_factory) -> Recording:
    """A second tab opened the way Ctrl+T opens one: no page opened it."""
    root = tmp_path_factory.mktemp("manual")
    meta = RecordingMeta.new("manual", "manual", start_url=TABS)

    with RecordingWriter(meta, root=root) as writer, WebRecorder(writer, headless=True) as rec:
        rec.open(TABS)
        second = rec.page.context.new_page()
        second.goto(REPORT)
        second.get_by_label("Note").fill("by hand")
        rec.pump(400)

    return session.load("manual", root=root)


class TestTabOpenedByHand:
    def test_its_first_navigation_is_recorded_on_its_own_page(self, manual_tab):
        steps = [(e.action, page_of(e), e.value) for e in manual_tab.events]
        assert steps == [
            (Action.NAVIGATE, 1, TABS),
            (Action.NAVIGATE, 2, REPORT),
            (Action.FILL, 2, "by hand"),
        ]


@pytest.fixture(scope="module")
def editor(tmp_path_factory) -> Recording:
    """Type into a contenteditable element key by key, the way a person does, then post."""
    root = tmp_path_factory.mktemp("editor")
    meta = RecordingMeta.new("editor", "editor", start_url=EDITOR)

    with RecordingWriter(meta, root=root) as writer, WebRecorder(writer, headless=True) as rec:
        rec.open(EDITOR)
        box = rec.page.get_by_role("textbox", name="Comment")
        box.click()
        box.press_sequentially(COMMENT)
        rec.page.get_by_role("button", name="Post").click()
        rec.pump(400)

    return session.load("editor", root=root)


class TestContentEditable:
    def test_typing_becomes_one_fill_with_the_whole_text(self, editor):
        fills = [e for e in run_pipeline(editor.events) if e.action is Action.FILL]
        assert [e.value for e in fills] == [COMMENT]

    def test_the_editor_gets_the_same_robust_selectors_as_an_input(self, editor):
        fill = next(e for e in editor.events if e.action is Action.FILL)
        assert fill.target.best.kind is SelectorKind.ID
        assert "textbox|Comment" in [c.value for c in fill.target.candidates]


def generated(rec: Recording) -> dict[str, object]:
    """Generate the code for a recording and load it the way a module is loaded."""
    code = generate(replace(rec, events=run_pipeline(rec.events)), GenOptions(include_main=False))
    namespace: dict[str, object] = {}
    exec(compile(code, "generated.py", "exec"), namespace)
    return namespace


@pytest.fixture
def fresh_page():
    """A page set up the way the generated main() sets one up."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser.new_context().new_page()
        browser.close()


class TestTheGeneratedCodeRuns:
    """The strongest check there is: run what stepdub wrote against the same pages."""

    def test_the_login_recording_signs_in_and_downloads_the_file(
        self, recorded, fresh_page, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("STEPDUB_PASSWORD", PASSWORD)
        paths = generated(recorded)["run"](fresh_page, download_dir=tmp_path)
        assert [p.name for p in paths] == ["results.csv"]
        assert "AA-01-BB" in paths[0].read_text(encoding="utf-8")

    def test_the_tabs_recording_finishes_the_work_in_the_second_tab(self, tabs, fresh_page):
        generated(tabs["recording"])["run"](fresh_page)
        report = fresh_page.context.pages[-1]
        expect(report.locator("#status")).to_have_text("Saved: hello")

    def test_a_tab_opened_by_hand_is_opened_again_on_replay(self, manual_tab, fresh_page):
        generated(manual_tab)["run"](fresh_page)
        second = fresh_page.context.pages[-1]
        expect(second.locator("#note")).to_have_value("by hand")

    def test_text_typed_into_an_editor_is_typed_again_in_full(self, editor, fresh_page):
        generated(editor)["run"](fresh_page)
        expect(fresh_page.locator("#posted")).to_have_text(COMMENT)
