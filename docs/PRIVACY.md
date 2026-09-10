# Privacy

This document exists because encore technically has the same capability as a keylogger.
Pretending otherwise would be dishonest. Here is exactly what the tool does, what it
does not do, and what it will never do.

## What is captured

During a recording, and **only** during a recording:

- clicks, with the selectors of the element you clicked;
- what you typed into ordinary text fields;
- dropdown selections and checkbox states;
- the `Enter`, `Tab`, `Escape` and arrow keys (no others);
- URLs visited and names of downloaded files;
- the visible text of elements (capped at 80 characters), so the generated code can use
  readable names.

## What is never captured

- **Password field values.** Detection happens inside the page, at the moment of typing
  (`input[type=password]`, `autocomplete="current-password"` and `"new-password"`). The
  value does not reach the file — but it also does not reach the process: it is not
  recorded and then filtered. It comes out as a reference to an environment variable.
- **An input's `value` used as descriptive text.** A password field has no visible text,
  but it does have a `value` — and that was an obvious side channel by which a password
  would end up inside a selector name. It is closed deliberately.
- **Screenshots.** v0.1 takes none. When they exist they will be explicit opt-in,
  because an image of the screen can contain everything else that was open next to it.
- **Keystrokes outside a recording.** There is no resident process. When `record`
  finishes, nothing is left running.

Two independent layers enforce the password rule: `injected.js` does not send the
value, and the data model **refuses to exist** with a value on an event marked as a
secret (`src/encore/ir.py`, `Event.__post_init__`). If a future recorder tries, it
raises instead of writing to disk.

## Where it lives

Everything under `~/.encore/recordings/<name>/`, two files per recording: `meta.json`
and `events.jsonl`.

```bash
encore where        # prints the exact path on this machine
encore purge --all  # deletes everything
```

You can move it with the `ENCORE_HOME` environment variable.

## Network

encore makes no network requests at all. No telemetry, no version checks, no usage
statistics, no error reporting. The browser Playwright opens goes where you send it and
nowhere else.

## What the tool refuses to do

These are not missing features. They are decisions:

- **Silent or hidden mode.** While recording, there is a visible indicator on the page.
- **Starting automatically with the system.** Does not exist, will not exist.
- **Disguising the process.** The process is named what it is.
- **Background capture not started by the person at the keyboard.**

A request for any of these is declined, and the reason is recorded in
[DECISIONS.md](DECISIONS.md). This is the line between an automation tool and spyware.

## Your responsibility

encore runs on your machine, with your permissions, and records what you tell it to
record. Two things stay with you:

1. **Recordings are sensitive data.** An `events.jsonl` can contain names, customer
   numbers, internal references — everything you typed while recording. Do not commit
   them. This project's `.gitignore` already ignores `.encore/`, but that protects this
   repository, not yours.
2. **If you automate an organisation's systems, in the EU that may be processing of
   personal data**, and the responsibility sits with whoever records, not with the
   tool. Check the terms of use of the site or application you are automating.

## Reporting a privacy problem

If you find a path where sensitive data escapes — a field type that should be detected
as a secret and is not, for example — open an issue. It is the highest-priority bug
category in this project, above any feature.
