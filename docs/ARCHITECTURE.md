# Architecture

## The problem, and why it is not one problem

Excel's macro recorder works for a reason that is easy to miss: VBA lives *inside*
Excel. Every user action passes through the application's own object model (`Range`,
`Worksheet`, `Workbook`), and the recorder just writes down the command that was
already happening internally. It does not see clicks — it intercepts semantic commands.

On a computer in general there is no such single object model. "Click the login button
in Chrome" and "type into a cell in SAP" do not travel down the same pipe. So recording
steps on a PC is not one problem — it is four, at four levels of fidelity:

| Layer | What you capture | How | Robustness |
|---|---|---|---|
| 1. Raw input | `click at (847, 312)` | `pynput` / `pyautogui` | Terrible: breaks on resolution, window position, timing |
| 2. Accessibility | `Button "Save" in Window "Excel"` | UI Automation, `pywinauto` | Good |
| 3. DOM | `<button id="login">` | Playwright + CDP + injected JS | Very good |
| 4. Native API | `ws.Range("A1").Value = 5` | `win32com`, SAP GUI Scripting | Best |

**encore's bet:** do not pick a layer. Record several at once into a single event
stream, and pick the highest-fidelity representation available for each action at code
generation time. The user clicked pixels; what comes out is
`page.get_by_role("button", name="Login").click()`.

That is only possible if capture is separated from generation. Hence the central piece.

## The design

```
┌───────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐
│   RECORDERS   │──▶│  EVENT LOG   │──▶│ TRANSFORMS  │──▶│   CODEGEN    │
│  (frontends)  │   │     (IR)     │   │  (passes)   │   │  (backends)  │
├───────────────┤   ├──────────────┤   ├─────────────┤   ├──────────────┤
│ web (CDP) ✅  │   │ versioned    │   │ noise       │   │ playwright ✅│
│ windows (UIA) │   │ JSONL,       │   │ typing      │   │ pywinauto    │
│ excel (COM)   │   │ append-only, │   │ waits       │   │ win32com     │
│ outlook (COM) │   │ timestamped  │   │ secrets     │   │              │
└───────────────┘   └──────────────┘   └─────────────┘   └──────────────┘
     capture           the contract      pure funcs         emission
```

This is compiler design: frontend, intermediate representation, passes, backend. Not as
an analogy — it is literally the same structure, for the same reasons.

## Why the IR exists

The most common mistake in projects like this is generating code straight from captured
events. Five reasons not to:

1. **Decoupling.** The desktop recorder lands in v0.3 without touching the web
   generator.
2. **Testability.** Every pass is `Sequence[Event] -> tuple[Event, ...]`, pure. The
   tests run in under a second with no browser at all.
3. **Reproducibility.** The recording stays on disk. A better generator, six months
   later, regenerates code from the same recording.
4. **Editability.** There is a place to intervene between "what happened" and "what
   code comes out" — which is what makes parameterisation possible.
5. **Versioning.** `SCHEMA_VERSION` lets the format evolve without breaking old
   recordings. A recording from a newer schema is refused with a clear message instead
   of failing strangely halfway through.

## The types, and the invariant that matters

In `src/encore/ir.py`:

- **`Selector`** — one way to find an element (`kind`, `value`, `score` 0-100).
- **`Target`** — the element, with **every** candidate ordered by robustness. A single
  selector is never stored alone. The sorting happens in `__post_init__`, which
  guarantees the generator is deterministic no matter what order the recorder produced
  candidates in.
- **`Event`** — one step: `id`, `ts`, `action`, `target`, `value`, `is_secret`,
  `secret_ref`, `context`.

The project's central invariant lives in `Event.__post_init__`: **an event marked as a
secret cannot carry a value.** Not a convention, not a code-review rule — an exception
in the constructor. If a future recorder tries to store a password, the process raises
instead of writing the value to disk.

## Selector ranking

`DEFAULT_SCORE`, most robust to most fragile:

```
testid 100 · id 90 · role 80 · label 75 · placeholder 65 · text 55 · css 30 · xpath 20 · coords 5
```

A recorder can lower an individual candidate's score: an id that looks machine-generated
(`ember1234`, `css-1x2y3z`, `:r3:`) scores 10, not 90. A text selector is only emitted
when the text is **unique** on the page — recording "Edit" when there are three "Edit"
links is recording an ambiguity.

## The passes, and why the order is not arbitrary

In `src/encore/transforms/`:

1. `drop_focus_clicks` — clicking a field before typing in it is not a step of the
   process, it is the cursor being placed.
2. `coalesce_typing` — typing "pedro" fires five events; one `fill` with the final
   value comes out. If any event in the group is a secret, the result is a secret —
   never the other way round.
3. `insert_waits` — long pauses become waits **for the element**, never `sleep`.
4. `normalize_secret_refs` — the field's name becomes `ENCORE_PASSWORD`.
5. `renumber` — sequential ids, because the passes above insert and remove events.

The order is forced: clean clicks before merging typing (a click in the middle blocks
the merge); merge typing before measuring pauses (otherwise you measure gaps between
keystrokes); always renumber last.

The recorder captures **everything**, noise included. Cleaning up is the passes' job,
not the recorder's: information discarded at capture time never comes back, and a pass
written tomorrow may want it.

## The generator, and the tension that defines it

`src/encore/codegen/playwright_py.py` resolves two requirements that contradict each
other:

- **Readability** (the file must be maintainable by hand after encore is gone) wants
  the good selector, inline, and nothing else.
- **Resilience** wants the fallback cascade in the code.

The resolution: the chosen selector goes inline; the alternatives go in a comment
**only when the chosen one is already weak** (score < 80). For a `data-testid` there is
no point cluttering the file; for a text selector there is. A locator used more than
once becomes a variable instead of a repeated expression.

Output is deterministic — same IR, same file, byte for byte — which is what makes
golden-file testing work. That is why the header carries no dates and no absolute
paths.

## Known limits in v0.1

- DOM layer only. The other three are on the roadmap.
- Frames are resolved by URL (`page.frame(url=...)`). Nested frames, or several frames
  sharing a URL, will need a richer field in the IR — which is exactly what
  `SCHEMA_VERSION` is for.
- Shadow DOM is not traversed.
- `NAVIGATE` is only recorded for the initial URL. Navigations caused by clicks are a
  consequence, not a step: recording them would generate code that undoes the click it
  just made.
- `SUBMIT` exists in the IR but is neither emitted by the recorder nor supported by the
  generator. If one shows up, it comes out as a visible comment rather than wrong code.
