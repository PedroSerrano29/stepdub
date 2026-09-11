# stepdub

[![CI](https://github.com/PedroSerrano29/stepdub/actions/workflows/ci.yml/badge.svg)](https://github.com/PedroSerrano29/stepdub/actions/workflows/ci.yml)

Record what you do on your computer and get **readable Python that does it again**.

It is the idea behind Excel's macro recorder — hit record, do the work, walk away with
code — but outside Excel: the browser first, desktop and native applications next. And
what comes out is not a proprietary format. It is a Python file you can read, edit,
commit, and keep maintaining long after you uninstall stepdub.

```bash
stepdub record https://example.com/login --name weekly-report
# ... sign in, search, download the file ...
stepdub gen weekly-report --param 6=term
```

Out comes this:

```python
def run(page: Page, *, term: str, download_dir: Path = Path("downloads")) -> list[Path]:
    """Replay the steps recorded in 'weekly-report'."""
    downloaded: list[Path] = []
    download_dir.mkdir(parents=True, exist_ok=True)

    page.goto(START_URL)
    page.get_by_label("Username").fill("pedro")
    # value comes from STEPDUB_PASSWORD, it was never recorded
    page.get_by_label("Password").fill(os.environ["STEPDUB_PASSWORD"])
    page.locator("#btn-signin").click()

    search_fleet = page.get_by_role("searchbox", name="Search fleet")
    expect(search_fleet).to_be_visible()
    search_fleet.fill(term)
    page.keyboard.press("Enter")

    # if this breaks, try: css=div.toolbar > a:nth-child(2)
    export_csv = page.get_by_text("Export CSV", exact=True)
    expect(export_csv).to_be_visible()
    with page.expect_download() as download_info:
        export_csv.click()
    download = download_info.value
    destination = download_dir / download.suggested_filename
    download.save_as(destination)
    downloaded.append(destination)

    return downloaded
```

Four things in that output are the whole project:

1. **The password is not in it.** It was detected at capture time and never entered the
   recording file. It comes out as an environment variable.
2. **There is not a single `time.sleep()`.** The pauses you made while recording became
   waits for the elements. Recorded timing works on your machine and breaks on everyone
   else's.
3. **The search term is a parameter.** It stopped being a macro and became a function
   you can loop over 500 rows of a CSV.
4. **When the chosen selector is fragile, the alternatives sit in a comment** — the
   first place you will look the day the page changes.

## Install

```bash
pip install "stepdub[web]"
playwright install chromium
```

## Usage

| Command | What it does |
|---|---|
| `stepdub record <url>` | opens the browser and records until you press Enter |
| `stepdub list` | your recordings |
| `stepdub show <name>` | the steps, cleaned up, with the ids you can parameterise |
| `stepdub gen <name>` | writes the Python file |
| `stepdub where` | where the data is stored |
| `stepdub purge --all` | deletes everything |

The normal loop is `record` → `show` (to see which steps survived and pick your
parameters) → `gen`.

## Secrets and privacy

A tool that records what you do on your computer owes you a straight answer about
this, so it all lives in [docs/PRIVACY.md](https://github.com/PedroSerrano29/stepdub/blob/main/docs/PRIVACY.md). The short version:

- **Zero network.** stepdub sends nothing anywhere. No telemetry, no "anonymous stats",
  no automatic crash reports.
- **Password fields are never recorded.** Detection happens at the point of capture,
  not in a filter downstream — the value never enters the process.
- **Everything is local**, under `~/.stepdub`, and `stepdub purge --all` removes it.
- **While recording, a visible indicator sits on the page.** There is no silent mode
  and no autostart, and there will not be — see [docs/DECISIONS.md](https://github.com/PedroSerrano29/stepdub/blob/main/docs/DECISIONS.md).

## How this differs from what already exists

Worth being straight about: for the pure web case,
[`playwright codegen`](https://playwright.dev/python/docs/codegen) is already excellent
and you should know it exists. stepdub diverges on four points:

| | `playwright codegen` | Power Automate / UiPath | stepdub |
|---|---|---|---|
| Web | ✅ | ✅ | ✅ |
| Desktop and native apps | ❌ | ✅ | planned (v0.3+) |
| Output is Python you can maintain | ✅ | ❌ (own format) | ✅ |
| Parameterise the recording | ❌ | ✅ | ✅ |
| Recording is reusable to regenerate code | ❌ | — | ✅ (versioned IR) |

That last row is the bet. The recording is stored as data — a versioned intermediate
representation — separate from the code generator. That is what will let the same
recording emit a different target later, and what will let one recording mix browser
and desktop steps.

## What it does not do yet

It is v0.1, so: web layer only, only tested on Windows, no GUI, no shadow DOM support,
frames are resolved by URL, and in a rich-text editor the text is recorded but its
formatting (bold, lists, links) is not. The [roadmap](https://github.com/PedroSerrano29/stepdub/blob/main/docs/ROADMAP.md) has the planned order.

## How it works inside

```
RECORDERS ──▶ EVENT LOG (IR) ──▶ TRANSFORMS ──▶ CODEGEN
(frontends)    versioned JSONL    pure passes    (backends)
```

The detail, and why code is never generated straight from captured events, is in
[docs/ARCHITECTURE.md](https://github.com/PedroSerrano29/stepdub/blob/main/docs/ARCHITECTURE.md).

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,web]"
.venv/Scripts/python -m pytest
```

The core (`ir`, `transforms`, `codegen`) has no dependencies: the tests run with no
browser installed. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT.
