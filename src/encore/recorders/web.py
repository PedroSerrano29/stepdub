"""The web (DOM) recorder, built on Playwright.

It has two halves, and it matters that they are two:

* `injected.js` runs INSIDE the page, because that is the only place where the live
  element exists at the moment of the click - so that is where selectors are built;
* this module runs in Python, receives those events over a binding, and handles what
  only exists on this side: downloads, navigations, new tabs.

Playwright is only needed here. The rest of encore (IR, transforms, codegen) reads and
writes recordings with no browser installed.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..ir import Action, Event, Selector, Target
from ..session import RecordingMeta, RecordingWriter, recordings_dir, unique_slug

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import BrowserContext, Page

INJECTED_JS = Path(__file__).with_name("injected.js")

BINDING = "__encore_emit"

# How often to pump Playwright's event loop while recording.
_TICK_MS = 200

_INSTALL_HINT = (
    "the web recorder needs Playwright:\n"
    '    pip install "encore-recorder[web]"\n'
    "    playwright install chromium"
)


class RecorderError(RuntimeError):
    """A recorder failure, with a message that tells the user what to do."""


def _load_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise RecorderError(_INSTALL_HINT) from err
    return sync_playwright


def target_from_payload(data: dict[str, Any]) -> Target | None:
    """Build a Target from what the page sent, dropping empty candidates."""
    raw = data.get("target")
    if not raw or not raw.get("candidates"):
        return None
    candidates = tuple(
        Selector(kind=c["kind"], value=c["value"], score=int(c.get("score", -1)))
        for c in raw["candidates"]
        if c.get("value")
    )
    if not candidates:
        return None
    return Target(
        candidates=candidates,
        tag=raw.get("tag", ""),
        text_preview=raw.get("text_preview", ""),
        frame_url=raw.get("frame_url", ""),
    )


def event_from_payload(data: dict[str, Any], event_id: int, ts: float) -> Event:
    """Convert a payload from the page into an IR Event.

    A secret's value is dropped here too, not only in `injected.js`. The `Event`
    invariant would already raise - but real defence is layered, and a recording
    aborted halfway by an exception is worse than a dropped value.
    """
    is_secret = bool(data.get("is_secret"))
    value = None if is_secret else data.get("value")
    return Event(
        id=event_id,
        ts=ts,
        action=Action(data["action"]),
        layer="dom",
        target=target_from_payload(data),
        value=value,
        is_secret=is_secret,
        secret_ref=data.get("secret_ref") if is_secret else None,
        context=dict(data.get("context", {})),
    )


class _Session:
    """State of a recording in progress."""

    def __init__(self, writer: RecordingWriter) -> None:
        self.writer = writer
        self.start = time.monotonic()
        self.next_id = 1
        self.stop = threading.Event()
        self.warnings: list[str] = []

    def ts(self) -> float:
        return time.monotonic() - self.start

    def record(self, data: dict[str, Any]) -> None:
        try:
            ev = event_from_payload(data, self.next_id, self.ts())
        except (ValueError, KeyError) as err:
            # One odd event must not kill the whole recording
            self.warnings.append(f"event skipped: {err}")
            return
        self.writer.append(ev)
        self.next_id += 1

    def record_navigate(self, url: str) -> None:
        self.record({"action": "navigate", "value": url})

    def record_download(self, filename: str) -> None:
        self.record({"action": "download", "value": filename})


def _wire_page(page: Page, sess: _Session) -> None:
    """Hook up the events that only exist on the Python side."""
    page.on("download", lambda d: sess.record_download(d.suggested_filename))


def _stop_on_enter(sess: _Session) -> None:
    """Read stdin on a thread, so Playwright's loop is never blocked."""

    def wait() -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.readline()
        sess.stop.set()

    threading.Thread(target=wait, daemon=True).start()


def record(
    url: str,
    name: str | None = None,
    *,
    root: Path | None = None,
    browser: str = "chromium",
    headless: bool = False,
) -> Path:
    """Record a browser session and return the recording's folder.

    Runs until the user presses Enter in the terminal or closes the browser.
    """
    sync_playwright = _load_playwright()
    parent = root or recordings_dir()
    slug = unique_slug(name or url, root=parent)
    meta = RecordingMeta.new(name or url, slug, start_url=url)
    js = INJECTED_JS.read_text(encoding="utf-8")

    with RecordingWriter(meta, root=parent) as writer:
        sess = _Session(writer)
        with sync_playwright() as p:
            engine = getattr(p, browser, None)
            if engine is None:
                raise RecorderError(f"unknown browser: {browser}")
            launched = engine.launch(headless=headless)
            context: BrowserContext = launched.new_context(accept_downloads=True)

            # The binding has to exist before the script that calls it.
            context.expose_binding(BINDING, lambda _source, data: sess.record(data))
            # add_init_script, not evaluate: it must survive every navigation and
            # reach every frame.
            context.add_init_script(js)
            context.on("page", lambda pg: _wire_page(pg, sess))

            page = context.new_page()
            _wire_page(page, sess)

            sess.record_navigate(url)
            page.goto(url)

            print(f"recording '{slug}' - go through the steps in the browser.")
            print("press Enter here (or close the browser) to finish.")
            _stop_on_enter(sess)

            try:
                while not sess.stop.is_set() and not page.is_closed():
                    # wait_for_timeout pumps the event loop: this is what lets the
                    # bindings arrive while we wait
                    page.wait_for_timeout(_TICK_MS)
            except KeyboardInterrupt:
                pass
            except Exception as err:  # browser closed by hand, or page gone
                sess.warnings.append(str(err))
            finally:
                with contextlib.suppress(Exception):
                    launched.close()

        for warning in sess.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"{writer.count} events recorded in {writer.path}")

    return (parent / slug).resolve()
