# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,web]"   # .venv/bin/pip on macOS/Linux
playwright install chromium                  # only needed to record
.venv/Scripts/python -m pytest
```

## Before opening a pull request

```bash
ruff format .
ruff check .
mypy
pytest
```

CI runs the same four. `mypy` is strict on `src/stepdub`.

## Rules that are not negotiable

**1. The core has no dependencies.** `ir`, `session`, `transforms` and `codegen` import
only the standard library. Playwright belongs to the `web` extra and is imported inside
the recorder, never at module level. The test that matters: the suite passes with no
browser installed.

**2. A secret never gets a value.** If you add a recorder or a capture path, detect
secret fields at the point of capture and send `is_secret` with no value.
`Event.__post_init__` will refuse anything else — do not work around it. See
[docs/PRIVACY.md](docs/PRIVACY.md).

**3. No network calls.** Not for telemetry, not for version checks, not for crash
reports. A PR that adds an outbound request will be declined regardless of how useful
the data would be.

**4. No silent capture.** Anything that hides the fact that recording is happening, or
starts recording without the person at the keyboard doing so, is out of scope
permanently, not "not yet". See ADR in [docs/DECISIONS.md](docs/DECISIONS.md).

**5. Generated code must be maintainable by hand.** The test: could someone delete
stepdub and keep editing the output? No `sleep`, no unreadable selector soup, no
generated helper the reader has to decode.

## Adding a transformation pass

A pass is a pure function `Sequence[Event] -> tuple[Event, ...]`. It must not touch the
disk, must not mutate its input, and must be deterministic. Add it to
`transforms/passes.py`, wire it into `DEFAULT_PIPELINE` at the right position (the
ordering rationale is a comment on that tuple), and test it in isolation as well as
inside the full pipeline.

## Changing the generator

Any change to output shows up as a diff in `tests/golden/`. That is the point. Review
the diff, confirm it is what you meant, then update the golden file:

```bash
python -c "import sys; sys.path.insert(0,'src'); \
from pathlib import Path; from dataclasses import replace; \
from stepdub import session; from stepdub.transforms import run_pipeline; \
from stepdub.codegen import generate, GenOptions; \
rec = session.load('login_search', root=Path('tests/fixtures')); \
rec = replace(rec, events=run_pipeline(rec.events)); \
Path('tests/golden/login_search.py.expected').write_bytes( \
generate(rec, GenOptions(params={6: 'term'})).encode('utf-8'))"
```

Never update the golden file without reading the diff first.

## Changing the IR

Adding an optional field with a default is backwards compatible and needs no version
bump. Anything that changes the meaning of an existing field, or makes a field
required, bumps `SCHEMA_VERSION` and needs a note in
[docs/DECISIONS.md](docs/DECISIONS.md). Old recordings must keep loading.

## Reporting bugs

Include the `stepdub --version`, your OS, and — if the problem is in generated code —
the relevant lines of `stepdub show <name> --raw`. **Read that output before pasting it:
it may contain data from the site you were recording.**

A privacy hole (data escaping where it should not) is the highest-priority category in
this project. Say so in the title.
