# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version
is below 1.0, the IR schema and the CLI may change between minor versions; the
`SCHEMA_VERSION` field keeps old recordings loadable or refuses them with a clear
message.

## [Unreleased]

## [0.1.0] — unreleased

First working version. Web layer only.

Named `stepdub`. The project was built as `encore` and renamed before release: that
name's CLI command already belongs to another tool and its PyPI name was taken. See
ADR-009 in `docs/DECISIONS.md`.

### Added

- Versioned intermediate representation (`SCHEMA_VERSION = 1`): `Selector`, `Target`,
  `Event`, with candidate selectors ranked by robustness.
- Append-only JSONL recordings under `~/.stepdub`, flushed per event, with a `meta.json`
  header per recording.
- Five pure transformation passes: `drop_focus_clicks`, `coalesce_typing`,
  `insert_waits`, `normalize_secret_refs`, `renumber`.
- Deterministic Playwright/Python code generator with parameterisation, download
  handling, iframe resolution and graceful degradation for unsupported steps.
- Web recorder: JS injected into every frame, Playwright binding, download handling,
  visible recording indicator.
- Work across tabs: pages are numbered as they appear, a tab opened from the page is
  recorded as a popup and replayed with `expect_popup()`, and a tab opened by hand
  becomes a new page. Recording continues while any tab is open, not only the first.
- CLI: `record`, `list`, `show`, `gen`, `purge`, `where`.
- End-to-end test suite driving a real browser against a local page, verifying selector
  quality, the recording indicator, and that a typed password reaches neither the
  recording nor the generated code. Skipped when Playwright is not installed.
- CI builds the wheel, installs it, and runs the whole suite against the installed
  package in a real browser, so what `pip install` delivers is what gets tested.
- Releases are built and published by CI through PyPI Trusted Publishing, after the full
  CI passes on the tagged commit.

### Security and privacy

- Password fields are detected at capture time and their values never enter the
  recording, enforced in two independent layers.
- No network calls of any kind. No telemetry.
- `stepdub where` and `stepdub purge --all` make storage inspectable and removable.
- The source distribution is built from an explicit allowlist, so a file that is only
  ignored locally can never ship in a release.
- Everything taken from a recording enters the generated code escaped, so an edited or
  crafted recording cannot inject code into it. Function and parameter names are
  checked before generation.
