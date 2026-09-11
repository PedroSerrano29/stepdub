// Event capture inside the page.
//
// This file is injected into every frame, before any of the page's own scripts, and
// re-injected on every navigation. Inside the page is the only place where the live
// element exists at the moment of the click, which is why selectors are built here.
//
// Privacy rule, enforced here and not downstream: the value of a password field never
// enters the payload. Not as a value, not as an accessible name, not as a text
// preview. There is no filter on the Python side that can fix something which has
// already left this file.

(() => {
  if (window.__stepdub_installed) return;
  window.__stepdub_installed = true;

  const MAX_TEXT = 80;
  const MAX_TEXT_SELECTOR = 40;

  const SCORES = {
    testid: 100,
    id: 90,
    role: 80,
    label: 75,
    placeholder: 65,
    text: 55,
    css: 30,
  };

  const emit = (payload) => {
    try {
      if (window.__stepdub_emit) window.__stepdub_emit(payload);
    } catch (err) {
      // page closing, or the binding is not registered yet: losing one event beats
      // throwing inside the user's page
    }
  };

  // Visible recording indicator. This is not decoration: while this is capturing, it
  // has to be obvious on screen that it is capturing.
  const showBanner = () => {
    if (!document.body || document.getElementById("__stepdub_banner")) return;
    if (window !== window.top) return;
    const el = document.createElement("div");
    el.id = "__stepdub_banner";
    el.textContent = "stepdub ● recording";
    el.setAttribute(
      "style",
      [
        "position:fixed",
        "top:10px",
        "right:10px",
        "z-index:2147483647",
        "background:#b3261e",
        "color:#fff",
        "font:600 12px/1 system-ui,-apple-system,Segoe UI,sans-serif",
        "padding:7px 11px",
        "border-radius:6px",
        "pointer-events:none",
        "box-shadow:0 2px 10px rgba(0,0,0,.35)",
      ].join(";"),
    );
    document.body.appendChild(el);
  };

  const trim = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);

  // ids like "ember1234", ":r3:", "css-1x2y3z" are worthless tomorrow
  const looksGenerated = (v) =>
    !v || /\d{4,}|^(:|ember|react|radix|mui-|css-|jss|sc-)/i.test(v);

  const isSecret = (el) =>
    el.tagName === "INPUT" &&
    (el.type === "password" ||
      el.autocomplete === "current-password" ||
      el.autocomplete === "new-password");

  const labelText = (el) => {
    if (el.labels && el.labels.length) return trim(el.labels[0].innerText);
    if (el.id) {
      try {
        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (l) return trim(l.innerText);
      } catch (err) {
        /* invalid selector: fall through */
      }
    }
    const wrap = el.closest("label");
    return wrap ? trim(wrap.innerText) : "";
  };

  // An input's `value` is only used here when the value IS the visible label
  // (submit buttons), and never on a password field.
  const valueAsLabel = (el) => {
    if (isSecret(el)) return "";
    if (el.tagName !== "INPUT") return "";
    return el.type === "submit" || el.type === "button" ? trim(el.value) : "";
  };

  const accessibleName = (el) =>
    trim(
      el.getAttribute("aria-label") ||
        labelText(el) ||
        el.title ||
        el.innerText ||
        valueAsLabel(el),
    );

  const ROLE_BY_TAG = {
    a: "link",
    button: "button",
    select: "combobox",
    textarea: "textbox",
    summary: "button",
  };

  const ROLE_BY_INPUT = {
    checkbox: "checkbox",
    radio: "radio",
    search: "searchbox",
    submit: "button",
    button: "button",
    file: "button",
    email: "textbox",
    text: "textbox",
    tel: "textbox",
    url: "textbox",
    number: "spinbutton",
    password: "textbox",
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "input") return ROLE_BY_INPUT[el.type] || "textbox";
    if (/^h[1-6]$/.test(tag)) return "heading";
    return ROLE_BY_TAG[tag] || "";
  };

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 5; depth++) {
      if (node.id && !looksGenerated(node.id)) {
        parts.unshift(`#${CSS.escape(node.id)}`);
        break;
      }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (c) => c.tagName === node.tagName,
        );
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };

  // Text only works as a selector if it is unique on the page. If there are three
  // "Edit" links, recording "Edit" is recording an ambiguity.
  // Measured in Chromium: ~3 ms per click at 1,000 clickables, ~40 ms at 20,000.
  // textContent would be 3x faster but ignores CSS text-transform, so it can call a
  // duplicated text unique.
  const uniqueText = (el) => {
    const t = trim(el.innerText);
    if (!t || t.length > MAX_TEXT_SELECTOR) return "";
    const same = Array.from(
      document.querySelectorAll(
        'a,button,summary,[role="button"],[role="link"],[role="tab"]',
      ),
    ).filter((c) => trim(c.innerText) === t);
    return same.length === 1 ? t : "";
  };

  const candidates = (el) => {
    const out = [];
    const add = (kind, value, score) => {
      if (value) out.push({ kind, value, score: score === undefined ? SCORES[kind] : score });
    };
    add(
      "testid",
      el.getAttribute("data-testid") ||
        el.getAttribute("data-test-id") ||
        el.getAttribute("data-test"),
    );
    if (el.id) add("id", el.id, looksGenerated(el.id) ? 10 : SCORES.id);
    const role = roleOf(el);
    if (role) {
      const name = accessibleName(el);
      add("role", name ? `${role}|${name}` : role);
    }
    add("label", labelText(el));
    add("placeholder", trim(el.getAttribute("placeholder")));
    add("text", uniqueText(el));
    add("css", cssPath(el));
    return out;
  };

  const targetOf = (el) => ({
    candidates: candidates(el),
    tag: el.tagName.toLowerCase(),
    // innerText only: an input's `value` never passes through here
    text_preview: trim(el.innerText),
    frame_url: window === window.top ? "" : location.href,
  });

  const secretRefFor = (el) => trim(el.name || el.id || labelText(el)) || "password";

  const CLICKABLE =
    "a,button,summary,input,select,textarea,label,[role],[onclick],[tabindex]";

  const actionable = (el) => {
    if (!el || el.nodeType !== 1) return null;
    return el.closest(CLICKABLE) || el;
  };

  // --- listeners, always in the capture phase -------------------------------
  // Without { capture: true }, a page calling stopPropagation() blinds us.

  document.addEventListener(
    "click",
    (e) => {
      const el = actionable(e.target);
      if (!el || el.id === "__stepdub_banner") return;
      emit({ action: "click", target: targetOf(el), context: { url: location.href } });
    },
    true,
  );

  document.addEventListener(
    "input",
    (e) => {
      const el = e.target;
      if (!el || el.nodeType !== 1) return;
      const tag = el.tagName;
      if (tag !== "INPUT" && tag !== "TEXTAREA" && !el.isContentEditable) return;

      if (el.type === "checkbox" || el.type === "radio") {
        emit({
          action: "check",
          target: targetOf(el),
          value: el.checked ? "true" : "false",
          context: { url: location.href },
        });
        return;
      }

      if (isSecret(el)) {
        emit({
          action: "fill",
          target: targetOf(el),
          is_secret: true,
          secret_ref: secretRefFor(el),
          context: { url: location.href },
        });
        return;
      }

      emit({
        action: "fill",
        target: targetOf(el),
        // the whole text: trim() caps at 80 characters and is only meant for previews
        value: el.isContentEditable ? el.innerText : el.value,
        context: { url: location.href },
      });
    },
    true,
  );

  document.addEventListener(
    "change",
    (e) => {
      const el = e.target;
      if (!el || el.tagName !== "SELECT") return;
      emit({
        action: "select",
        target: targetOf(el),
        value: el.value,
        context: { url: location.href },
      });
    },
    true,
  );

  const KEYS = new Set(["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp"]);

  document.addEventListener(
    "keydown",
    (e) => {
      if (!KEYS.has(e.key)) return;
      emit({ action: "press", value: e.key, context: { url: location.href } });
    },
    true,
  );

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", showBanner, { once: true });
  } else {
    showBanner();
  }
})();
