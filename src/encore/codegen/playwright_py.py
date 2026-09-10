"""Python + Playwright code generator, driven by the IR.

Two rules, and they pull against each other:

1. The generated file has to be maintainable by hand. The test is simple: can someone
   delete encore from the machine and keep editing that Python? If not, the generator
   failed. So the chosen selector goes inline and readable.
2. That selector will break when the page changes. So when the best candidate is
   already a weak one, the alternatives go in a comment above the line - whoever has
   to fix it gets somewhere to start.

Output is deterministic: same IR, same file, byte for byte. No dates and no absolute
paths in the header - that is what makes golden-file testing possible.
"""

from __future__ import annotations

import json
import keyword
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..ir import Action, Event, Selector, SelectorKind, Target, split_role
from ..session import Recording

# Below this score the chosen selector is weak and the alternatives are worth showing.
WEAK_SELECTOR_SCORE = 80

# How many alternatives to show, and the line width to stay inside.
MAX_FALLBACKS_SHOWN = 2
MAX_LINE = 100

_INDENT = "    "

# Names the generated file uses itself: a locator variable must not collide with them.
_RESERVED = frozenset(
    {
        "START_URL",
        "Page",
        "Path",
        "browser",
        "destination",
        "download",
        "download_dir",
        "download_info",
        "downloaded",
        "expect",
        "main",
        "os",
        "p",
        "page",
        "path",
        "sync_playwright",
    }
)

_NON_IDENT = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class GenOptions:
    """Code generation options."""

    function_name: str = "run"
    # event id -> parameter name in the generated function
    params: Mapping[int, str] = field(default_factory=dict)
    include_main: bool = True


def _lit(value: str) -> str:
    """A Python string literal, with non-ASCII intact and escapes correct."""
    return json.dumps(value, ensure_ascii=False)


def _locator_call(sel: Selector) -> str:
    """The Playwright call that finds the element, without the object it starts from."""
    if sel.kind is SelectorKind.TESTID:
        return f"get_by_test_id({_lit(sel.value)})"
    if sel.kind is SelectorKind.ID:
        return f"locator({_lit('#' + sel.value)})"
    if sel.kind is SelectorKind.ROLE:
        role, name = split_role(sel.value)
        if name:
            return f"get_by_role({_lit(role)}, name={_lit(name)})"
        return f"get_by_role({_lit(role)})"
    if sel.kind is SelectorKind.LABEL:
        return f"get_by_label({_lit(sel.value)})"
    if sel.kind is SelectorKind.PLACEHOLDER:
        return f"get_by_placeholder({_lit(sel.value)})"
    if sel.kind is SelectorKind.TEXT:
        return f"get_by_text({_lit(sel.value)}, exact=True)"
    if sel.kind is SelectorKind.XPATH:
        return f"locator({_lit('xpath=' + sel.value)})"
    # CSS and COORDS land here; COORDS is handled before reaching this point
    return f"locator({_lit(sel.value)})"


def _fallback_comment(target: Target, depth: int = 1) -> str | None:
    """Alternative selectors, only when the chosen one is weak.

    For a `data-testid` there is no point cluttering the file; for a text selector
    there is - it is the first place anyone will look the day it breaks.
    """
    if target.best.score >= WEAK_SELECTOR_SCORE or not target.fallbacks:
        return None
    prefix = "# if this breaks, try: "
    budget = MAX_LINE - len(_INDENT) * depth - len(prefix)
    parts: list[str] = []
    for sel in target.fallbacks[:MAX_FALLBACKS_SHOWN]:
        candidate = f"{sel.kind.value}={sel.value}"
        width = len(" | ".join([*parts, candidate]))
        if parts and width > budget:
            break
        parts.append(candidate)
    if not parts:
        return None
    return prefix[2:] + " | ".join(parts)


def _identifier(raw: str, taken: set[str]) -> str:
    """A readable Python variable name derived from text on the page."""
    ascii_only = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    base = _NON_IDENT.sub("_", ascii_only.lower()).strip("_")
    base = "_".join(base.split("_")[:3])  # three words is plenty
    if not base or base[0].isdigit():
        base = f"target_{base}" if base else "target"
    if keyword.iskeyword(base) or keyword.issoftkeyword(base):
        base = f"{base}_"
    name = base
    n = 2
    while name in taken or name in _RESERVED:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def _target_label(target: Target) -> str:
    """The most descriptive text available for the target, to name a variable after."""
    for sel in target.candidates:
        if sel.kind is SelectorKind.ROLE:
            _, name = split_role(sel.value)
            if name:
                return name
        elif sel.kind in {
            SelectorKind.LABEL,
            SelectorKind.PLACEHOLDER,
            SelectorKind.TESTID,
            SelectorKind.TEXT,
        }:
            return sel.value
    return target.text_preview or target.tag or "target"


_LocatorKey = tuple[str, SelectorKind, str]


def _locator_key(target: Target) -> _LocatorKey:
    """A locator's identity: same frame, same chosen selector."""
    return (target.frame_url, target.best.kind, target.best.value)


class _Emitter:
    """Collects lines of code with indentation."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str = "", depth: int = 0) -> None:
        self.lines.append(f"{_INDENT * depth}{text}" if text else "")

    def blank(self) -> None:
        """A blank line, never producing two in a row."""
        if self.lines and self.lines[-1]:
            self.lines.append("")

    def render(self) -> str:
        while self.lines and not self.lines[-1]:
            self.lines.pop()
        return "\n".join(self.lines) + "\n"


class _Generator:
    def __init__(self, rec: Recording, options: GenOptions) -> None:
        self.rec = rec
        self.opt = options
        self.events = list(rec.events)
        self.frames: dict[str, str] = {}
        self.unsupported: list[str] = []

        # Variable names already spent in the generated file
        self.taken: set[str] = set(options.params.values())
        # A locator used more than once becomes a variable instead of a repeated call
        self.locator_vars: dict[_LocatorKey, str] = {}
        self.commented: set[_LocatorKey] = set()
        self.locator_uses: Counter[_LocatorKey] = Counter(
            _locator_key(e.target)
            for e in self.events
            if e.target is not None and e.target.best.kind is not SelectorKind.COORDS
        )

        self.has_downloads = any(e.action is Action.DOWNLOAD for e in self.events)
        self.has_waits = any(e.action is Action.WAIT for e in self.events)
        self.secrets = rec.secrets
        self.start_url = self._find_start_url()

    # --- preparation ----------------------------------------------------------

    def _find_start_url(self) -> str:
        for ev in self.events:
            if ev.action is Action.NAVIGATE and ev.value:
                return ev.value
        return self.rec.meta.start_url

    def _param_signature(self) -> list[str]:
        """Parameters of the generated function, in the order they appear."""
        out: list[str] = []
        for ev in self.events:
            name = self.opt.params.get(ev.id)
            if name and name not in out:
                out.append(name)
        return out

    def _param_examples(self) -> dict[str, str]:
        """The recorded value behind each parameter, to use as an example in `main()`."""
        out: dict[str, str] = {}
        for ev in self.events:
            name = self.opt.params.get(ev.id)
            if name and name not in out:
                out[name] = ev.value or ""
        return out

    def _base(self, target: Target, em: _Emitter, depth: int) -> str:
        """What the locator starts from: `page`, or the frame the element lives in."""
        if not target.frame_url:
            return "page"
        var = self.frames.get(target.frame_url)
        if var is None:
            var = f"frame_{len(self.frames) + 1}"
            self.frames[target.frame_url] = var
            em.line(f"{var} = page.frame(url={_lit(target.frame_url)})", depth)
            em.line(f"if {var} is None:", depth)
            em.line(f'raise RuntimeError("frame not found: {target.frame_url}")', depth + 1)
        return var

    def _locator(self, target: Target, em: _Emitter, depth: int) -> str:
        """The locator expression - or the variable name, if it is used more than once."""
        key = _locator_key(target)
        existing = self.locator_vars.get(key)
        if existing is not None:
            return existing
        expr = f"{self._base(target, em, depth)}.{_locator_call(target.best)}"
        if self.locator_uses[key] < 2:
            return expr
        var = _identifier(_target_label(target), self.taken)
        self.locator_vars[key] = var
        em.line(f"{var} = {expr}", depth)
        return var

    def _value_expr(self, ev: Event) -> str:
        """The value expression: a parameter, an environment variable, or a literal."""
        name = self.opt.params.get(ev.id)
        if name:
            return name
        if ev.is_secret and ev.secret_ref:
            return f"os.environ[{_lit(ev.secret_ref)}]"
        return _lit(ev.value or "")

    # --- body -----------------------------------------------------------------

    def _emit_action(self, ev: Event, em: _Emitter, depth: int, nxt: Event | None) -> None:
        if ev.action is Action.NAVIGATE:
            if ev.value == self.start_url:
                em.line("page.goto(START_URL)", depth)
            else:
                em.line(f"page.goto({_lit(ev.value or '')})", depth)
            return

        if ev.action is Action.PRESS and ev.target is None:
            em.line(f"page.keyboard.press({_lit(ev.value or 'Enter')})", depth)
            return

        if ev.action is Action.DOWNLOAD:
            # emitted together with the click that triggers it, see _emit_click
            return

        if ev.target is None:
            self.unsupported.append(f"{ev.action.value} with no target (event {ev.id})")
            em.line(f"# step not supported by this generator: {ev.action.value}", depth)
            return

        if ev.target.best.kind is SelectorKind.COORDS:
            x, _, y = ev.target.best.value.partition(",")
            em.line(f"page.mouse.click({x.strip()}, {y.strip()})", depth)
            return

        # The fallback hint appears once per target, not on every action against it
        key = _locator_key(ev.target)
        if key not in self.commented:
            self.commented.add(key)
            comment = _fallback_comment(ev.target, depth)
            if comment:
                em.line(f"# {comment}", depth)

        loc = self._locator(ev.target, em, depth)

        if ev.action is Action.WAIT:
            em.line(f"expect({loc}).to_be_visible()", depth)
        elif ev.action is Action.CLICK:
            self._emit_click(loc, em, depth, nxt)
        elif ev.action is Action.FILL:
            if ev.is_secret:
                em.line(f"# value comes from {ev.secret_ref}, it was never recorded", depth)
            em.line(f"{loc}.fill({self._value_expr(ev)})", depth)
        elif ev.action is Action.PRESS:
            em.line(f"{loc}.press({_lit(ev.value or 'Enter')})", depth)
        elif ev.action is Action.SELECT:
            em.line(f"{loc}.select_option({self._value_expr(ev)})", depth)
        elif ev.action is Action.CHECK:
            em.line(f"{loc}.{'check' if ev.value != 'false' else 'uncheck'}()", depth)
        else:
            self.unsupported.append(f"{ev.action.value} (event {ev.id})")
            em.line(f"# step not supported by this generator: {ev.action.value}", depth)

    def _emit_click(self, loc: str, em: _Emitter, depth: int, nxt: Event | None) -> None:
        """A click that triggers a download has to be wrapped in the download wait."""
        if nxt is not None and nxt.action is Action.DOWNLOAD:
            em.line("with page.expect_download() as download_info:", depth)
            em.line(f"{loc}.click()", depth + 1)
            em.line("download = download_info.value", depth)
            em.line("destination = download_dir / download.suggested_filename", depth)
            em.line("download.save_as(destination)", depth)
            em.line("downloaded.append(destination)", depth)
        else:
            em.line(f"{loc}.click()", depth)

    # --- file -----------------------------------------------------------------

    def _emit_header(self, em: _Emitter) -> None:
        name = self.rec.meta.name or self.rec.meta.slug
        em.line('"""' + f"{name} - generated by encore from a recording.")
        em.line()
        em.line(f"Regenerate:  encore gen {self.rec.meta.slug}")
        em.line("Edit by hand: go ahead. encore never reads this file back.")
        if self.secrets:
            em.line()
            em.line("Requires these environment variables:")
            for ref in self.secrets:
                em.line(f"  {ref}")
        em.line('"""')
        em.line()
        em.line("from __future__ import annotations")
        em.line()
        if self.secrets:
            em.line("import os")
        if self.has_downloads:
            em.line("from pathlib import Path")
        if self.secrets or self.has_downloads:
            em.line()
        imports = ["Page"]
        if self.has_waits:
            imports.append("expect")
        if self.opt.include_main:
            imports.append("sync_playwright")
        em.line(f"from playwright.sync_api import {', '.join(sorted(imports))}")
        em.line()
        if self.start_url:
            em.line(f"START_URL = {_lit(self.start_url)}")
        em.line()

    def _emit_run(self, em: _Emitter) -> None:
        params = self._param_signature()
        args = ["page: Page"]
        if params:
            args.append("*")
            args.extend(f"{p}: str" for p in params)
        if self.has_downloads:
            if "*" not in args:
                args.append("*")
            args.append('download_dir: Path = Path("downloads")')
        ret = "list[Path]" if self.has_downloads else "None"

        signature = f"def {self.opt.function_name}({', '.join(args)}) -> {ret}:"
        em.line()
        if len(signature) <= MAX_LINE:
            em.line(signature)
        else:
            em.line(f"def {self.opt.function_name}(")
            for arg in args:
                em.line(f"{arg}," if arg != "*" else "*,", 1)
            em.line(f") -> {ret}:")

        name = self.rec.meta.name or self.rec.meta.slug
        em.line(f'"""Replay the steps recorded in {name!r}."""', 1)
        if self.has_downloads:
            em.line("downloaded: list[Path] = []", 1)
            em.line("download_dir.mkdir(parents=True, exist_ok=True)", 1)
        em.line()

        for i, ev in enumerate(self.events):
            nxt = self.events[i + 1] if i + 1 < len(self.events) else None
            # a wait or a navigation marks the start of a new step in the process
            if i and ev.action in {Action.WAIT, Action.NAVIGATE}:
                em.blank()
            self._emit_action(ev, em, 1, nxt)

        if self.has_downloads:
            em.blank()
            em.line("return downloaded", 1)

    def _emit_main(self, em: _Emitter) -> None:
        examples = self._param_examples()
        call_args = ["page"]
        call_args.extend(f"{name}={_lit(value)}" for name, value in examples.items())

        em.line()
        em.line()
        em.line("def main() -> None:")
        em.line("with sync_playwright() as p:", 1)
        em.line("browser = p.chromium.launch(headless=False)", 2)
        em.line("page = browser.new_page()", 2)
        em.line("try:", 2)
        call = f"{self.opt.function_name}({', '.join(call_args)})"
        if self.has_downloads:
            em.line(f"for path in {call}:", 3)
            em.line("print(path)", 4)
        else:
            em.line(call, 3)
        em.line("finally:", 2)
        em.line("browser.close()", 3)
        em.line()
        em.line()
        em.line('if __name__ == "__main__":')
        em.line("main()", 1)

    def render(self) -> str:
        em = _Emitter()
        self._emit_header(em)
        self._emit_run(em)
        if self.opt.include_main:
            self._emit_main(em)
        return em.render()


def generate(rec: Recording, options: GenOptions | None = None) -> str:
    """Generate the Python module that replays the recording."""
    return _Generator(rec, options or GenOptions()).render()
