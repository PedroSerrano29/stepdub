"""The CLI is the product's surface: errors must be messages, not stack traces."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stepdub.cli import _parse_params, _summary, _when, main
from stepdub.ir import Action, Event, Selector, SelectorKind, Target

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """STEPDUB_HOME pointed at tmp, with the test recording already in place."""
    monkeypatch.setenv("STEPDUB_HOME", str(tmp_path))
    shutil.copytree(FIXTURES / "login_search", tmp_path / "recordings" / "login_search")
    return tmp_path


class TestParseParams:
    def test_a_valid_pair(self):
        assert _parse_params(["6=term"]) == {6: "term"}

    def test_several_pairs(self):
        assert _parse_params(["1=a", "2=b"]) == {1: "a", 2: "b"}

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="expects ID=name"):
            _parse_params(["term"])

    def test_a_non_numeric_id_raises(self):
        with pytest.raises(ValueError, match="expects ID=name"):
            _parse_params(["six=term"])

    def test_an_empty_list(self):
        assert _parse_params(None) == {}


class TestList:
    def test_with_no_recordings_it_suggests_the_first_command(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("STEPDUB_HOME", str(tmp_path))
        assert main(["list"]) == 0
        assert "stepdub record" in capsys.readouterr().out

    def test_it_lists_the_recording_with_its_event_count(self, home, capsys):
        assert main(["list"]) == 0
        out = capsys.readouterr().out
        assert "login_search" in out
        assert "12" in out

    def test_the_url_column_is_not_pushed_out_by_a_long_timestamp(self, home, capsys):
        assert main(["list"]) == 0
        header, row = capsys.readouterr().out.splitlines()[:2]
        assert header.index("url") == row.index("https://example.com/login")


class TestShow:
    def test_it_shows_the_clean_version_and_the_raw_count(self, home, capsys):
        assert main(["show", "login_search"]) == 0
        out = capsys.readouterr().out
        assert "10 events (cleaned by the pipeline)" in out
        assert "the raw recording had 12" in out

    def test_raw_shows_all_twelve(self, home, capsys):
        assert main(["show", "login_search", "--raw"]) == 0
        assert "12 events (raw)" in capsys.readouterr().out

    def test_it_suggests_the_possible_parameters(self, home, capsys):
        assert main(["show", "login_search"]) == 0
        assert "--param 6=<name>" in capsys.readouterr().out

    def test_it_does_not_offer_the_start_url_or_a_keypress_as_parameters(self, home, capsys):
        """Both carry a value, neither is something anyone wants as a function argument."""
        assert main(["show", "login_search"]) == 0
        out = capsys.readouterr().out
        assert "--param 1=" not in out
        assert "--param 7=" not in out
        assert "'Enter'" not in out.split("values you can turn into parameters:")[1]

    def test_it_announces_the_environment_variable(self, home, capsys):
        assert main(["show", "login_search"]) == 0
        assert "STEPDUB_PASSWORD" in capsys.readouterr().out

    def test_the_secret_shows_up_marked_and_without_a_value(self, home, capsys):
        assert main(["show", "login_search"]) == 0
        assert "<secret: STEPDUB_PASSWORD>" in capsys.readouterr().out

    def test_a_missing_recording_gives_a_readable_error(self, home, capsys):
        assert main(["show", "does-not-exist"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("stepdub: ")
        assert "Traceback" not in err


class TestGen:
    def test_it_writes_a_file_named_after_the_recording(self, home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["gen", "login_search"]) == 0
        assert (tmp_path / "login_search.py").exists()
        assert "wrote" in capsys.readouterr().out

    def test_stdout_writes_no_file(self, home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["gen", "login_search", "--stdout"]) == 0
        assert not (tmp_path / "login_search.py").exists()
        assert "def run(page" in capsys.readouterr().out

    def test_a_param_becomes_a_function_argument(self, home, capsys):
        assert main(["gen", "login_search", "--stdout", "--param", "6=term"]) == 0
        assert "term: str" in capsys.readouterr().out

    def test_a_malformed_param_gives_a_readable_error(self, home, capsys):
        assert main(["gen", "login_search", "--stdout", "--param", "term"]) == 1
        assert "expects ID=name" in capsys.readouterr().err

    def test_it_does_not_overwrite_without_force(self, home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "login_search.py").write_text("# my code", encoding="utf-8")
        assert main(["gen", "login_search"]) == 1
        assert "--force" in capsys.readouterr().err
        assert (tmp_path / "login_search.py").read_text(encoding="utf-8") == "# my code"

    def test_force_overwrites(self, home, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "login_search.py").write_text("# my code", encoding="utf-8")
        assert main(["gen", "login_search", "--force"]) == 0
        assert "def run(page" in (tmp_path / "login_search.py").read_text(encoding="utf-8")

    def test_it_says_which_variables_to_set(self, home, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["gen", "login_search"]) == 0
        assert "STEPDUB_PASSWORD" in capsys.readouterr().out

    def test_no_main_generates_the_function_only(self, home, capsys):
        assert main(["gen", "login_search", "--stdout", "--no-main"]) == 0
        assert "def main()" not in capsys.readouterr().out


class TestPurge:
    def test_without_a_target_it_asks_for_one(self, home, capsys):
        assert main(["purge"]) == 1
        assert "--all" in capsys.readouterr().err

    def test_it_deletes_one(self, home, capsys):
        assert main(["purge", "login_search"]) == 0
        assert "deleted login_search" in capsys.readouterr().out
        assert not (home / "recordings" / "login_search").exists()

    def test_it_deletes_all(self, home, capsys):
        assert main(["purge", "--all"]) == 0
        assert "deleted" in capsys.readouterr().out

    def test_deleting_a_missing_one_gives_a_readable_error(self, home, capsys):
        assert main(["purge", "does-not-exist"]) == 1
        assert "not found" in capsys.readouterr().err


class TestSummary:
    def test_a_step_on_a_later_page_names_the_page(self):
        target = Target(candidates=(Selector(SelectorKind.ID, "note"),))
        ev = Event(id=4, ts=0.0, action=Action.FILL, target=target, value="hi", context={"page": 2})
        assert "[page 2] id=note" in _summary(ev)

    def test_a_popup_says_which_page_opened_it(self):
        ev = Event(id=3, ts=0.0, action=Action.POPUP, context={"page": 2, "opener": 1})
        assert "opened from page 1" in _summary(ev)

    def test_the_first_page_is_not_labelled(self):
        ev = Event(id=1, ts=0.0, action=Action.PRESS, value="Enter")
        assert "[page" not in _summary(ev)


class TestWhen:
    def test_an_iso_timestamp_is_trimmed_to_fit_a_column(self):
        assert _when("2026-01-01T09:30:00+00:00") == "2026-01-01 09:30"

    def test_an_empty_timestamp_survives(self):
        assert _when("") == ""


class TestWhere:
    def test_it_says_where_the_data_is_and_that_nothing_leaves(self, home, capsys):
        assert main(["where"]) == 0
        out = capsys.readouterr().out
        assert str(home) in out
        assert "nothing leaves this machine" in out
        assert "purge --all" in out


class TestParser:
    def test_no_command_exits_with_an_error(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "stepdub" in capsys.readouterr().out
