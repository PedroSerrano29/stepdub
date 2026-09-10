# Roadmap

The order is deliberate: each version has to end with something that works on its own,
and each one should be usable by someone who never reads this file.

## v0.1 — Web layer ✅

- [x] Versioned IR (`SCHEMA_VERSION = 1`) with ranked candidate selectors
- [x] Append-only JSONL recordings, flushed per event
- [x] Five pure transformation passes
- [x] Playwright/Python generator, deterministic, golden-file tested
- [x] Secrets never captured, enforced by an invariant in the data model
- [x] Web recorder: injected JS + Playwright binding, download and new-tab handling
- [x] CLI: `record`, `list`, `show`, `gen`, `purge`, `where`
- [x] Test suite that runs with no browser installed
- [x] End-to-end verification of the recorder against a real browser, driven from a
      local test page (`tests/pages/login.html`), skipped when Playwright is absent
- [ ] CI green on GitHub Actions
- [ ] Published on PyPI (name `encore-recorder` not yet claimed)

## v0.2 — Parameters and ergonomics

- [ ] `encore edit <name>`: drop, reorder and annotate steps without regenerating
- [ ] Parameter inference: offer likely candidates instead of requiring `--param 6=x`
- [ ] Secrets read from `keyring` as an alternative to environment variables
- [ ] `--resilient` generator flag: emit the full fallback cascade (see ADR-004)
- [ ] `encore run <name>`: replay straight from the IR, no code generation
- [ ] Better failure messages when a selector no longer matches

## v0.3 — Windows desktop

- [ ] UI Automation recorder (`pywinauto`), writing into the same IR
- [ ] `pywinauto` code generator
- [ ] Window and control identity in the IR (probably `SCHEMA_VERSION = 2`)
- [ ] One recording spanning browser and desktop steps

## v0.4 — Native application APIs

- [ ] Recognise that the focused window is Excel and promote actions to `win32com`
- [ ] The same for Outlook (repetitive email is the most requested use case)
- [ ] SAP GUI Scripting, if a test environment can be arranged

## v0.5 — Review interface

- [ ] Review the recording and pick parameters visually before generating
- [ ] Optional per-step screenshots, opt-in (see PRIVACY.md)

## Beyond

- macOS and Linux (the accessibility layer is completely different on each)
- A shared library of recordings for common sites
- Scheduling, so a generated script runs on a timer

---

## Open findings

Unresolved items from ongoing review. Each with a date and a `file:line` pointer.

- [ ] 2026-09-10 — `src/encore/recorders/web.py:188` the recording loop only watches the
      first page. If the user closes that tab but keeps working in a second one opened
      from it, the loop stops while the context is still alive. Needs to track the
      context rather than one page.
- [ ] 2026-09-10 — `src/encore/recorders/injected.js:200` `uniqueText` runs a full
      `querySelectorAll` on every click. Fine for normal pages, potentially slow on very
      large DOMs. Measure before optimising.
- [ ] 2026-09-10 — no recorder covers `contenteditable` beyond a plain `fill`; rich text
      editors will produce partial recordings.
- [ ] 2026-09-10 — running `tests/test_e2e_web.py` leaves a `Task was destroyed but it
      is pending` message on stderr after the suite passes. It comes from Playwright's
      own connection teardown when `start()`/`stop()` runs several times in one
      process, not from encore. Cosmetic, but it looks like a failure to a newcomer:
      either find the right teardown order or run the e2e file in its own process.
