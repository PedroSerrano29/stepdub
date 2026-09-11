"""The web (DOM) recorder, built on Playwright.

It has two halves, and it matters that they are two:

* `injected.js` runs INSIDE the page, because that is the only place where the live
  element exists at the moment of the click - so that is where selectors are built;
* this module runs in Python, receives those events over a binding, and handles what
  only exists on this side: downloads, navigations, new tabs.

Pages are numbered in the order they appear, and every event carries the number of the
page it happened in (ADR-011).

`WebRecorder` is the engine and `record()` is the interactive wrapper around it. They
are separate on purpose: a recorder that only exists inside a "wait until the user
presses Enter" loop cannot be tested against a real browser, and this one is - see
`tests/test_e2e_web.py`.

Playwright is only needed here. The rest of stepdub (IR, transforms, codegen) reads and
writes recordings with no browser installed.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from ..ir import Action, Event, Selector, Target
from ..session import RecordingMeta, RecordingWriter, recordings_dir, unique_slug

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page

INJECTED_JS = Path(__file__).with_name("injected.js")

BINDING = "__stepdub_emit"

BROWSERS = ("chromium", "firefox", "webkit")

# How often to pump Playwright's event loop while recording.
_TICK_MS = 200

# A tab opened by hand starts here; none of these is a place to start a recording from.
_BLANK_SCHEMES = ("about:", "chrome:", "edge:")

_INSTALL_HINT = (
    "the web recorder needs Playwright:\n"
    '    pip install "stepdub[web]"\n'
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

    def record(self, data: dict[str, Any], page: int = 1) -> None:
        if page > 1:  # no number means the first page, so one-tab recordings stay lean
            data = {**data, "context": {**data.get("context", {}), "page": page}}
        try:
            ev = event_from_payload(data, self.next_id, self.ts())
        except (ValueError, KeyError) as err:
            # One odd event must not kill the whole recording
            self.warnings.append(f"event skipped: {err}")
            return
        self.writer.append(ev)
        self.next_id += 1

    def record_navigate(self, url: str, page: int = 1) -> None:
        self.record({"action": "navigate", "value": url}, page)

    def record_download(self, filename: str, page: int = 1) -> None:
        self.record({"action": "download", "value": filename}, page)

    def record_popup(self, page: int, opener: int) -> None:
        self.record({"action": "popup", "context": {"opener": opener}}, page)


class WebRecorder:
    """Drives a browser and writes what happens in it into a recording.

    Use it as a context manager. It owns the Playwright lifetime, so there is no
    interactive loop in here: the caller decides when to stop, which is what makes
    the recorder testable.
    """

    def __init__(
        self,
        writer: RecordingWriter,
        *,
        browser: str = "chromium",
        headless: bool = False,
    ) -> None:
        self.session = _Session(writer)
        self.browser_name = browser
        self.headless = headless
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        # Every page seen so far with its number, in order of appearance
        self._pages: list[tuple[Page, int]] = []
        self._navigated: set[int] = set()

    @property
    def page(self) -> Any:
        """The first page: where the recording starts."""
        if self._page is None:
            raise RecorderError("WebRecorder used outside its `with` block")
        return self._page

    @property
    def count(self) -> int:
        return self.session.writer.count

    @property
    def warnings(self) -> list[str]:
        return self.session.warnings

    def __enter__(self) -> WebRecorder:
        # Checked before Playwright starts: stopping it right after a start leaves a
        # pending task that complains on stderr.
        if self.browser_name not in BROWSERS:
            raise RecorderError(
                f"unknown browser: {self.browser_name} (choose from {', '.join(BROWSERS)})"
            )
        sync_playwright = _load_playwright()
        self._pw = sync_playwright().start()
        engine = getattr(self._pw, self.browser_name)

        self._browser = engine.launch(headless=self.headless)
        self._context = self._browser.new_context(accept_downloads=True)

        # The binding has to exist before the script that calls it.
        self._context.expose_binding(
            BINDING, lambda source, data: self.session.record(data, self._register(source["page"]))
        )
        # add_init_script, not evaluate: it must survive every navigation and reach
        # every frame of every page, including pages opened later.
        self._context.add_init_script(INJECTED_JS.read_text(encoding="utf-8"))
        # Fires for every page, the first one included; _register wires each one once.
        self._context.on("page", self._register)

        self._page = self._context.new_page()
        self._register(self._page)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._shutdown()

    def _shutdown(self) -> None:
        # Order matters: context, then browser, then Playwright itself. Closing the
        # browser first leaves Playwright's connection task pending and it complains
        # on stderr after the process is already done.
        for closeable in (self._context, self._browser):
            with contextlib.suppress(Exception):
                if closeable is not None:
                    closeable.close()
        with contextlib.suppress(Exception):
            if self._pw is not None:
                self._pw.stop()
        self._browser = self._context = self._page = self._pw = None
        self._pages = []
        self._navigated = set()

    def _register(self, pg: Page) -> int:
        """A page's number, given the first time it is seen. Wires the page up once."""
        for known, number in self._pages:
            if known == pg:
                return number
        number = len(self._pages) + 1
        self._pages.append((pg, number))
        pg.on("download", lambda d: self.session.record_download(d.suggested_filename, number))
        if number > 1:
            opener = pg.opener()
            if opener is not None:
                self.session.record_popup(number, self._register(opener))
            else:
                # opened by hand: no recorded step opened it, so its first URL counts
                pg.on("framenavigated", lambda frame: self._first_navigation(frame, number))
        return number

    def _first_navigation(self, frame: Any, number: int) -> None:
        """Where a tab opened by hand went first. Later navigations follow from clicks."""
        if number in self._navigated or frame.parent_frame is not None:
            return
        if not frame.url or frame.url.startswith(_BLANK_SCHEMES):
            return
        self._navigated.add(number)
        self.session.record_navigate(frame.url, number)

    def _live_page(self) -> Page | None:
        """The first page still open. Recording lasts as long as any tab does."""
        for pg, _ in self._pages:
            if not pg.is_closed():
                return pg
        return None

    def open(self, url: str) -> None:
        """Record the starting URL and go there."""
        self.session.record_navigate(url)
        self.page.goto(url)

    def pump(self, ms: int = _TICK_MS) -> None:
        """Let Playwright process messages, which is how queued events arrive."""
        live = self._live_page()
        if live is None:
            return
        try:
            live.wait_for_timeout(ms)
        except Exception:
            # the tab closed while we waited on it; the next pump picks another one
            if not live.is_closed():
                raise

    def is_open(self) -> bool:
        return self._live_page() is not None


def record(
    url: str,
    name: str | None = None,
    *,
    root: Path | None = None,
    browser: str = "chromium",
    headless: bool = False,
) -> Path:
    """Record a browser session and return the recording's folder.

    Runs until the user presses Enter in the terminal or closes every tab.
    """
    parent = root or recordings_dir()
    slug = unique_slug(name or url, root=parent)
    meta = RecordingMeta.new(name or url, slug, start_url=url)

    with RecordingWriter(meta, root=parent) as writer:
        with WebRecorder(writer, browser=browser, headless=headless) as rec:
            rec.open(url)

            print(f"recording '{slug}' - go through the steps in the browser.")
            print("press Enter here (or close the browser) to finish.")
            _stop_on_enter(rec.session)

            try:
                while not rec.session.stop.is_set() and rec.is_open():
                    rec.pump()
            except KeyboardInterrupt:
                pass
            except Exception as err:  # the browser can go away mid-pump
                rec.warnings.append(str(err))
            warnings = list(rec.warnings)

        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(f"{writer.count} events recorded in {writer.path}")

    return (parent / slug).resolve()


def _stop_on_enter(sess: _Session) -> None:
    """Read stdin on a thread, so Playwright's loop is never blocked."""

    def wait() -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.readline()
        sess.stop.set()

    threading.Thread(target=wait, daemon=True).start()
