"""Reading and writing recordings on disk.

A recording is a folder with two files:

    <STEPDUB_HOME>/recordings/<slug>/
        meta.json      -> name, start URL, schema version, when it was recorded
        events.jsonl   -> one event per line, append-only

The format is append-only on purpose: if the browser or the process dies halfway
through a long recording, whatever was already captured stays readable.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from . import __version__
from .ir import SCHEMA_VERSION, Event

ENV_HOME = "STEPDUB_HOME"
EVENTS_FILE = "events.jsonl"
META_FILE = "meta.json"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class RecordingError(Exception):
    """Failure while reading or writing a recording."""


def home() -> Path:
    """The stepdub data root. Everything the tool stores lives here and nowhere else."""
    raw = os.environ.get(ENV_HOME)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".stepdub"


def recordings_dir() -> Path:
    return home() / "recordings"


def slugify(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug or "recording"


def unique_slug(name: str, root: Path | None = None) -> str:
    """A free slug inside `root`, with a numeric suffix if one is already taken."""
    base = slugify(name)
    parent = root or recordings_dir()
    slug = base
    n = 2
    while (parent / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


@dataclass(frozen=True, slots=True)
class RecordingMeta:
    """A recording's header. Holds no captured data."""

    name: str
    slug: str
    start_url: str = ""
    schema: int = SCHEMA_VERSION
    stepdub_version: str = __version__
    created_at: str = ""
    system: str = ""

    @classmethod
    def new(cls, name: str, slug: str, start_url: str = "") -> RecordingMeta:
        return cls(
            name=name,
            slug=slug,
            start_url=start_url,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            system=platform.system(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "start_url": self.start_url,
            "schema": self.schema,
            "stepdub_version": self.stepdub_version,
            "created_at": self.created_at,
            "system": self.system,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordingMeta:
        return cls(
            name=data.get("name", data.get("slug", "")),
            slug=data["slug"],
            start_url=data.get("start_url", ""),
            schema=int(data.get("schema", SCHEMA_VERSION)),
            stepdub_version=data.get("stepdub_version", ""),
            created_at=data.get("created_at", ""),
            system=data.get("system", ""),
        )


@dataclass(frozen=True, slots=True)
class Recording:
    """A recording loaded into memory."""

    meta: RecordingMeta
    events: tuple[Event, ...]

    @property
    def secrets(self) -> tuple[str, ...]:
        """Environment variable names the generated code will need, in first-use order."""
        seen: list[str] = []
        for ev in self.events:
            if ev.secret_ref and ev.secret_ref not in seen:
                seen.append(ev.secret_ref)
        return tuple(seen)


class RecordingWriter:
    """Writes a recording line by line, flushing after every event."""

    def __init__(self, meta: RecordingMeta, root: Path | None = None) -> None:
        self.meta = meta
        self.path = (root or recordings_dir()) / meta.slug
        self._fh: Any = None
        self._count = 0

    def __enter__(self) -> RecordingWriter:
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / META_FILE).write_text(
            json.dumps(self.meta.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._fh = (self.path / EVENTS_FILE).open("a", encoding="utf-8")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def count(self) -> int:
        return self._count

    def append(self, event: Event) -> None:
        if self._fh is None:
            raise RecordingError("RecordingWriter used outside its `with` block")
        self._fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        self._count += 1


def dumps(events: Iterable[Event]) -> str:
    """Serialize events to JSONL (also used by the tests)."""
    return "".join(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n" for ev in events)


def loads(text: str) -> tuple[Event, ...]:
    """Read events from JSONL text, skipping blank lines."""
    out: list[Event] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            out.append(Event.from_dict(json.loads(stripped)))
        except (ValueError, KeyError) as err:
            raise RecordingError(f"line {lineno} is invalid: {err}") from err
    return tuple(out)


def resolve(name: str, root: Path | None = None) -> Path:
    """Locate a recording by name or slug."""
    parent = root or recordings_dir()
    direct = parent / name
    if (direct / META_FILE).exists():
        return direct
    slug = parent / slugify(name)
    if (slug / META_FILE).exists():
        return slug
    raise RecordingError(f"recording '{name}' not found in {parent}")


def load(name: str, root: Path | None = None) -> Recording:
    path = resolve(name, root)
    meta = RecordingMeta.from_dict(json.loads((path / META_FILE).read_text(encoding="utf-8")))
    if meta.schema > SCHEMA_VERSION:
        raise RecordingError(
            f"recording '{meta.slug}' uses schema {meta.schema}, this stepdub reads at "
            f"most {SCHEMA_VERSION} - please upgrade stepdub"
        )
    events_path = path / EVENTS_FILE
    text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
    return Recording(meta=meta, events=loads(text))


def iter_recordings(root: Path | None = None) -> Iterator[RecordingMeta]:
    parent = root or recordings_dir()
    if not parent.exists():
        return
    for path in sorted(parent.iterdir()):
        meta_path = path / META_FILE
        if not meta_path.exists():
            continue
        try:
            yield RecordingMeta.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except (ValueError, KeyError):
            continue


def count_events(slug: str, root: Path | None = None) -> int:
    path = (root or recordings_dir()) / slug / EVENTS_FILE
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def purge(name: str | None = None, root: Path | None = None) -> list[str]:
    """Delete one recording, or all of them when `name` is None. Returns deleted slugs."""
    parent = root or recordings_dir()
    if name is None:
        slugs = [m.slug for m in iter_recordings(parent)]
        for slug in slugs:
            shutil.rmtree(parent / slug, ignore_errors=True)
        return slugs
    path = resolve(name, parent)
    shutil.rmtree(path, ignore_errors=True)
    return [path.name]
