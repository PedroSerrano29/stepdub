"""Command line interface.

Assumed UX principle: people using this see messages, not stack traces. Every
foreseeable error comes out as one line that says what to do next.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, session
from .codegen import GenOptions, generate
from .ir import ACTIONS_PARAMETERIZABLE, Event
from .session import RecordingError
from .transforms import run_pipeline


def _when(created_at: str) -> str:
    """Trim an ISO timestamp to something that fits a column: 2026-01-01 09:30."""
    return created_at[:16].replace("T", " ")


def _fail(msg: str) -> int:
    print(f"stepdub: {msg}", file=sys.stderr)
    return 1


def _parse_params(pairs: list[str] | None) -> dict[int, str]:
    """Turn --param 6=term into {6: "term"}."""
    out: dict[int, str] = {}
    for pair in pairs or []:
        id_text, _, name = pair.partition("=")
        if not name or not id_text.strip().isdigit():
            raise ValueError(f"--param expects ID=name, got {pair!r}")
        out[int(id_text)] = name.strip()
    return out


def _summary(ev: Event) -> str:
    """One readable line per event, for the `show` command."""
    target = ""
    if ev.target is not None:
        sel = ev.target.best
        target = f"{sel.kind.value}={sel.value}"
    if ev.is_secret:
        value = f"<secret: {ev.secret_ref}>"
    elif ev.value is not None:
        value = repr(ev.value)
    else:
        value = ""
    return f"{ev.id:>3}  {ev.action.value:<9} {target:<44} {value}"


# --- commands -----------------------------------------------------------------


def cmd_record(args: argparse.Namespace) -> int:
    from .recorders import RecorderError, record

    try:
        folder = record(
            args.url,
            args.name,
            browser=args.browser,
            headless=args.headless,
        )
    except RecorderError as err:
        return _fail(str(err))
    print(f"recorded in {folder}")
    print(f"next:  stepdub gen {folder.name}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    metas = list(session.iter_recordings())
    if not metas:
        print("no recordings yet. Start with:  stepdub record https://example.com")
        return 0
    print(f"{'slug':<28} {'events':>7}  {'when':<16}  url")
    for meta in metas:
        n = session.count_events(meta.slug)
        print(f"{meta.slug:<28} {n:>7}  {_when(meta.created_at):<16}  {meta.start_url}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        rec = session.load(args.name)
    except RecordingError as err:
        return _fail(str(err))

    events = rec.events if args.raw else run_pipeline(rec.events)
    label = "raw" if args.raw else "cleaned by the pipeline"
    print(f"{rec.meta.slug} - {len(events)} events ({label})")
    if not args.raw:
        print(f"the raw recording had {len(rec.events)}")
    print()
    for ev in events:
        print(_summary(ev))

    candidates = [ev for ev in events if ev.action in ACTIONS_PARAMETERIZABLE and ev.value]
    if candidates and not args.raw:
        print()
        print("values you can turn into parameters:")
        for ev in candidates:
            print(f"  --param {ev.id}=<name>   ({ev.value!r})")

    refs: list[str] = []
    for ev in events:
        if ev.secret_ref and ev.secret_ref not in refs:
            refs.append(ev.secret_ref)
    if refs:
        print()
        print("environment variables the generated code will need:")
        for ref in refs:
            print(f"  {ref}")
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    try:
        rec = session.load(args.name)
        params = _parse_params(args.param)
    except (RecordingError, ValueError) as err:
        return _fail(str(err))

    cleaned = replace(rec, events=run_pipeline(rec.events))
    code = generate(
        cleaned,
        GenOptions(
            function_name=args.function,
            params=params,
            include_main=not args.no_main,
        ),
    )

    if args.stdout:
        print(code, end="")
        return 0

    destination = Path(args.output) if args.output else Path(f"{rec.meta.slug}.py")
    if destination.exists() and not args.force:
        return _fail(f"{destination} already exists - use --force to overwrite")
    destination.write_text(code, encoding="utf-8", newline="\n")
    print(f"wrote {destination}")
    if cleaned.secrets:
        print("before running it, set:", ", ".join(cleaned.secrets))
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if not args.name and not args.all:
        return _fail("name a recording to delete, or use --all to delete every one")
    try:
        deleted = session.purge(None if args.all else args.name)
    except RecordingError as err:
        return _fail(str(err))
    if not deleted:
        print("nothing to delete")
        return 0
    for slug in deleted:
        print(f"deleted {slug}")
    return 0


def cmd_where(_args: argparse.Namespace) -> int:
    print(f"stepdub data:  {session.home()}")
    print(f"recordings:   {session.recordings_dir()}")
    print("nothing leaves this machine. To delete everything:  stepdub purge --all")
    print(f"to move it elsewhere, set {session.ENV_HOME}")
    return 0


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stepdub",
        description="Record what you do and get Python that does it again.",
    )
    p.add_argument("--version", action="version", version=f"stepdub {__version__}")
    subs = p.add_subparsers(dest="command", required=True)

    rec = subs.add_parser("record", help="record a browser session")
    rec.add_argument("url", help="page to start from")
    rec.add_argument("--name", help="name for the recording (defaults to the URL)")
    rec.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    rec.add_argument("--headless", action="store_true", help="no window (rarely useful)")
    rec.set_defaults(func=cmd_record)

    lst = subs.add_parser("list", help="list recordings")
    lst.set_defaults(func=cmd_list)

    show = subs.add_parser("show", help="inspect a recording's steps")
    show.add_argument("name")
    show.add_argument("--raw", action="store_true", help="skip the pipeline")
    show.set_defaults(func=cmd_show)

    gen = subs.add_parser("gen", help="generate Python from a recording")
    gen.add_argument("name")
    gen.add_argument("-o", "--output", help="output file")
    gen.add_argument("--stdout", action="store_true", help="print instead of writing")
    gen.add_argument(
        "--param",
        action="append",
        metavar="ID=NAME",
        help="turn the value of event ID into a parameter (see `stepdub show`)",
    )
    gen.add_argument("--function", default="run", help="name of the generated function")
    gen.add_argument("--no-main", action="store_true", help="function only, no main()")
    gen.add_argument("--force", action="store_true", help="overwrite the file if it exists")
    gen.set_defaults(func=cmd_gen)

    purge = subs.add_parser("purge", help="delete recordings from disk")
    purge.add_argument("name", nargs="?")
    purge.add_argument("--all", action="store_true")
    purge.set_defaults(func=cmd_purge)

    where = subs.add_parser("where", help="where stepdub stores its data")
    where.set_defaults(func=cmd_where)

    return p


def main(argv: list[str] | None = None) -> int:
    # Recorded values carry whatever characters the page had. On a console whose
    # encoding cannot represent them, printing must degrade, not crash - otherwise
    # `stepdub show > file.txt` dies on Windows over one accented character.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except KeyboardInterrupt:
        return 130
    return result


if __name__ == "__main__":
    raise SystemExit(main())
