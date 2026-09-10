"""Reading and writing recordings on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from encore import session
from encore.ir import Action, Event, Selector, SelectorKind, Target

FIXTURES = Path(__file__).parent / "fixtures"


def an_event(id_: int = 1) -> Event:
    return Event(
        id=id_,
        ts=1.5,
        action=Action.CLICK,
        target=Target(candidates=(Selector(SelectorKind.TESTID, "btn"),)),
    )


class TestSlug:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("Login Example", "login-example"),
            ("  MIXED case  and  spaces  ", "mixed-case-and-spaces"),
            ("café", "caf"),
            ("///", "recording"),
            ("", "recording"),
        ],
    )
    def test_slugify(self, given, expected):
        assert session.slugify(given) == expected

    def test_unique_slug_avoids_collisions(self, tmp_path):
        (tmp_path / "login").mkdir()
        assert session.unique_slug("Login", root=tmp_path) == "login-2"


class TestHome:
    def test_the_env_var_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(session.ENV_HOME, str(tmp_path))
        assert session.home() == tmp_path

    def test_default_is_under_the_user_home(self, monkeypatch):
        monkeypatch.delenv(session.ENV_HOME, raising=False)
        assert session.home() == Path.home() / ".encore"


class TestWritingAndReading:
    def test_the_writer_creates_meta_and_events(self, tmp_path):
        meta = session.RecordingMeta.new("Test", "test", "https://example.com")
        with session.RecordingWriter(meta, root=tmp_path) as w:
            w.append(an_event(1))
            w.append(an_event(2))
            assert w.count == 2

        folder = tmp_path / "test"
        written = json.loads((folder / session.META_FILE).read_text(encoding="utf-8"))
        assert written["slug"] == "test"
        assert len((folder / session.EVENTS_FILE).read_text(encoding="utf-8").splitlines()) == 2

    def test_the_writer_flushes_every_event(self, tmp_path):
        """If the process dies halfway, what was captured stays readable."""
        meta = session.RecordingMeta.new("Test", "test")
        with session.RecordingWriter(meta, root=tmp_path) as w:
            w.append(an_event(1))
            on_disk = (tmp_path / "test" / session.EVENTS_FILE).read_text(encoding="utf-8")
        assert on_disk.strip()

    def test_appending_outside_the_with_block_raises(self, tmp_path):
        w = session.RecordingWriter(session.RecordingMeta.new("T", "t"), root=tmp_path)
        with pytest.raises(session.RecordingError, match="outside its `with`"):
            w.append(an_event())

    def test_dumps_loads_roundtrip(self):
        events = (an_event(1), an_event(2))
        assert session.loads(session.dumps(events)) == events

    def test_an_invalid_line_says_which_one(self):
        with pytest.raises(session.RecordingError, match="line 2"):
            session.loads('{"id":1,"ts":0,"action":"press","value":"Enter"}\n{"junk":1}\n')

    def test_blank_lines_are_skipped(self):
        assert session.loads("\n\n") == ()


class TestLoadingTheFixture:
    def test_load_reads_meta_and_events(self):
        rec = session.load("login_search", root=FIXTURES)
        assert rec.meta.start_url == "https://example.com/login"
        assert len(rec.events) == 12

    def test_secrets_lists_the_required_variables(self):
        rec = session.load("login_search", root=FIXTURES)
        assert rec.secrets == ("password",)

    def test_a_missing_recording_says_where_it_looked(self):
        with pytest.raises(session.RecordingError, match="not found"):
            session.load("does-not-exist", root=FIXTURES)

    def test_a_future_schema_is_refused(self, tmp_path):
        folder = tmp_path / "future"
        folder.mkdir()
        (folder / session.META_FILE).write_text(
            json.dumps({"slug": "future", "schema": 999}), encoding="utf-8"
        )
        with pytest.raises(session.RecordingError, match="upgrade encore"):
            session.load("future", root=tmp_path)


class TestListingAndDeleting:
    def test_iter_recordings_skips_folders_without_meta(self, tmp_path):
        (tmp_path / "junk").mkdir()
        with session.RecordingWriter(session.RecordingMeta.new("A", "a"), root=tmp_path) as w:
            w.append(an_event())
        assert [m.slug for m in session.iter_recordings(tmp_path)] == ["a"]

    def test_count_events(self, tmp_path):
        with session.RecordingWriter(session.RecordingMeta.new("A", "a"), root=tmp_path) as w:
            w.append(an_event(1))
            w.append(an_event(2))
        assert session.count_events("a", root=tmp_path) == 2
        assert session.count_events("does-not-exist", root=tmp_path) == 0

    def test_purge_one(self, tmp_path):
        with session.RecordingWriter(session.RecordingMeta.new("A", "a"), root=tmp_path) as w:
            w.append(an_event())
        assert session.purge("a", root=tmp_path) == ["a"]
        assert not (tmp_path / "a").exists()

    def test_purge_all(self, tmp_path):
        for slug in ("a", "b"):
            with session.RecordingWriter(session.RecordingMeta.new(slug, slug), root=tmp_path) as w:
                w.append(an_event())
        assert sorted(session.purge(None, root=tmp_path)) == ["a", "b"]
        assert list(session.iter_recordings(tmp_path)) == []
