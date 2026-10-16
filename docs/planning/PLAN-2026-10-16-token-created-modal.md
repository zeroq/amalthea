# PLAN 2026-10-16 — Show-once ingestion token as a modal (not an unstyled page)

**Status:** Ready for implementation
**Owner:** planner
**Related:** [PLAN-2026-10-15-token-only-webhook-routing](./PLAN-2026-10-15-token-only-webhook-routing.md),
[PLAN-2026-10-15-settings-sources-ingestion-tokens](./PLAN-2026-10-15-settings-sources-ingestion-tokens.md),
`ui/templates/ui/_modal.html`, `ui/static/ui/modal.js`, `ui/static/ui/modal.css`

## 1. Summary

Minting a token renders `ui/templates/ui/_settings_token_created.html` as a **top-level response**.
That partial has no `{% extends %}`, so the browser receives no shell and no stylesheets — the secret
lands on a bare, unstyled page. On top of that, the show-once markup's own classes have no CSS at all.

Fix: on POST success, re-render the **whole Sources page** (`ui/settings_sources.html`) with the
show-once content inside an autoopened `_modal.html` dialog — the same shape `case_create` already
uses when it re-renders `case_list.html` with `open_new_case=True` — and give the markup real styles.

## 2. Goals

- Clicking **Create** shows the plaintext token in a styled modal on top of the Sources list, with a
  Copy control, exactly as the rest of the UI presents dialogs.
- The secret is shown exactly once, in one render, and is never persisted anywhere (no session, no
  URL, no log).
- No change to create/revoke semantics, permissions, audit rows or the webhook contract.

## 3. Non-goals

- No `open`-attribute / JS-off visibility work (decision Q1 = **accept JS-only**, option b). A JS-off
  operator sees the Sources list and the new token row; the secret is unrecoverable by design and the
  dialog copy already says so ("If you lose it, revoke this token and create a new one").
- No fix for the refresh-reposts problem (decision Q5 = **leave + TODO**; the plaintext must never
  transit the session or cache).
- No `modal.css` token-guard extension and no fix for its existing undefined `--color-*-warn` tokens
  (decision Q6 = **later**).
- No copy of the body content beyond styling — warning box, token block and endpoint/header hints stay
  as they are (decision Q7).

## 4. Decisions (2026-10-16)

| # | Question | Decision |
|---|----------|----------|
| Q1 | Visible without JS? | **No** — `data-autoopen` only (option b); no server-side `open` attribute, no `modal.js` upgrade path |
| Q2 | Esc/backdrop-resistant? | **Yes** — dialog carries `data-modal-static`; `modal.js` is extended so Esc also respects it |
| Q3 | Footer | **Single Close**: an anchor to `/settings/sources` (works with no JS) marked `data-modal-close` (closes in place with JS). Needs `show_cancel=False` + `close_url` on `_modal.html` and `preventDefault()` for anchors in `modal.js` |
| Q4 | CSS home | **`modal.css`** (hand-written, next to the other modal content styles) |
| Q5 | Refresh re-post | **Leave**, record in `TODO.md` |
| Q6 | `modal.css` guard/token fix | **Later** |
| Q7 | Cosmetics | Content unchanged; dialog `size="lg"` (560px) so the 43-char secret + Copy button fit |

## 5. Implementation

### 5.1 Backend — `ui/views.py`

1. Extract the list context out of `settings_sources` (`ui/views.py:3905`) into a helper, e.g.
   `_settings_sources_context(request) -> dict`, returning `tokens`, `show_org_column`, `sources`,
   `organisations`, `is_superadmin`, `webhook_path`. `settings_sources` keeps rendering the same
   template with the same values (byte-identical GET output).
2. `settings_token_create` (`ui/views.py:3950`): replace the final
   `render(request, "ui/_settings_token_created.html", …)` with a render of `ui/settings_sources.html`
   using the shared context **plus**:
   - `token_created=True` — gates the dialog include
   - `token_plaintext=plaintext` — read by the body partial (name is load-bearing: pinned by
     `test_show_once_modal_names_the_concrete_webhook_endpoint` and `_plaintext_from`)
   - `close_url` is a template constant (`{% url 'ui-settings-sources' %}`), not server context.
3. Keep the 200 (no redirect), keep the audit + prefix-retry logic untouched. `token_plaintext` must
   appear in **no other** render path.

### 5.2 Template — `ui/templates/ui/settings_sources.html`

After the existing create-modal include (`:79-81`), add:

```django
{% if token_created %}
  {% include "ui/_modal.html" with modal_id="token-created-modal" title="Ingestion token created"
     body_template="ui/_settings_token_created.html" confirm_text="Close" confirm_class="btn-primary"
     show_cancel=False close_url=settings_sources_url size="lg" autoopen=token_created modal_static=True %}
{% endif %}
```

`_settings_token_created.html` keeps its body markup (warning, `#token-plaintext`, Copy button,
endpoint/header hints); only its trailing comment is updated to point at the new include.

### 5.3 `ui/templates/ui/_modal.html`

Two new optional params, both defaulting to today's behaviour so every existing caller keeps
byte-identical output:

- `show_cancel` (default `True`) — when falsy, the Cancel button is not rendered.
- `close_url` (optional) — in the no-`form_action` branch, when set, render
  `<a class="btn {{ confirm_class }}" href="{{ close_url }}" data-modal-close="{{ modal_id }}">…</a>`
  instead of the inert `data-modal-confirm` button.
- `modal_static` (optional) — when truthy, add `data-modal-static` to the `<dialog>`.

### 5.4 `ui/static/ui/modal.js`

1. Esc branch (~`:95`) must **not** close a dialog carrying `data-modal-static`.
2. In the `[data-modal-close]` click delegation (~`:61`), call `event.preventDefault()` when the closer
   is an `a[href]` so the anchor closes in place with JS and navigates without it.

`data-modal-static` is referenced nowhere else in the codebase today (only `modal.js:83`), so gating
Esc on it changes **no** existing dialog's behaviour.

### 5.5 `ui/static/ui/modal.css`

Style `.token-created`, `.token-warning`, `.token-display`, `.token-code-wrap`, `.token-hint` — amber
warning box (`--color-warn`), inset code block, `overflow-wrap: anywhere` on the secret (43 chars,
must not blow out the 560px dialog), Copy button inline with the token. **Only already-defined theme
tokens** may be used; `modal.css` is not covered by the `test_ui_css_tokens.py` guard, so a typo there
fails silently (exactly how `.blockers-warning` is broken today).

### 5.6 `TODO.md`

Add one item: the create POST result is re-sent on browser refresh and mints a second live token;
consider PRG with a one-shot in-memory hand-off, or make the create form idempotent per submission.

## 6. Acceptance Criteria

| AC | Criterion |
|----|-----------|
| AC1 | On success the response is a **full page** (shell + stylesheet links + Sources list), not a bare fragment |
| AC2 | The response contains `<dialog id="token-created-modal" … data-autoopen="1">` |
| AC3 | That dialog carries `data-modal-static`; the page's static dialog set is unchanged otherwise |
| AC4 | The dialog footer has exactly one action: an `a` to `/settings/sources` with `data-modal-close="token-created-modal"`, and no Cancel button |
| AC5 | `#token-plaintext` is unique and holds the secret; the secret appears in exactly one response and in no DB column or log — corrected 2026-10-16: it legitimately renders **3× inside that dialog** (the `<code id="token-plaintext">` plus the `X-Webhook-Secret:` and `Authorization: Bearer` hint lines), so the invariant is "one unique `#token-plaintext` element, all occurrences inside the show-once dialog", not "once on the page" |
| AC6 | `GET /settings/sources` is unchanged (no `token-created-modal`, no `token_plaintext`) |
| AC7 | GET/list markup for existing dialogs is unchanged (the three new `_modal.html` params are opt-in) — corrected 2026-10-16: asserted on rendered `<dialog …>` tag attributes, because "byte-identical" would also fail on harmless comment/whitespace changes |
| AC8 | Esc/backdrop do **not** dismiss this dialog; `modal.js` closes in place when the Close anchor is clicked |

## 7. Tests (`tests/conformance/test_settings_ingestion_tokens.py`)

- AC1/AC2/AC3/AC4/AC6 via rendered-body assertions (the `_dialog_tag` helper pattern from
  `test_ui_case_create.py:1246` is the house idiom).
- AC5 stays covered by `TestShowOnce`; add that the plaintext appears in exactly one response.
- AC7: assert an unrelated dialog's markup is unchanged / no `data-modal-static` anywhere on a normal
  listing render.
- AC8: textual pin of the two `modal.js` behaviours (precedent: `test_settings_ui.py:805` asserts on
  the served JS/HTML).
- CSS regression: assert the five `.token-*` classes exist in `modal.css` (would have caught this bug).

## 8. Risks

- **Secret leakage via the re-rendered page**: the Sources list is rendered again in the same response;
  it must not include `token_plaintext` anywhere but the dialog body. Covered by AC5.
- **Anchors inside dialogs**: `preventDefault()` must apply only to `[data-modal-close]` anchors, not to
  every link in a dialog.
- **`_modal.html` blast radius**: both new params must default to current behaviour (AC7).

## 9. Deviations to record (`docs/spec/deviations.md`)

1. `_modal.html` gains `show_cancel`, `close_url`, `modal_static` (all opt-in).
2. `modal.js` Esc now respects `data-modal-static` — previously backdrop clicks only.
3. `modal.js` prevents default for `[data-modal-close]` anchors.
4. Show-once response changed from a bare partial to a full-page render (the plan's whole point).
5. `modal.css` gains the five `.token-*` rules the old bare partial never had (it was an unstyled page).

## 10. Findings during implementation (2026-10-16)

Handoff note: the `django-backend` / `django-frontend` subagents returned HTTP 504 on every attempt
(five tries, including a minimal read-only probe), so §5.1–5.5 was implemented directly by the
planner agent and §7 tests were handed to `qa-tester`. Retry the code-writer agents for the
follow-ups in this section.

1. **A multi-line `{# … #}` comment is not a comment.** The first version of the
   `settings_sources.html` addition used Django's single-line-only `{# #}` syntax across four lines,
   so the block was emitted verbatim into every `GET /settings/sources` body — literal `{# Show-once
   dialog. … #}` text on a live Settings page, and a raw `data-modal-static` **substring** on a page
   where no dialog has the attribute. Caught by `qa-tester`, fixed by switching to
   `{% comment %}…{% endcomment %}`. Repo-wide grep: no other template has a multi-line `{# #}`.
   Worth a permanent guard (render the page, assert no `{#`/`#}` markers) — that is what AC7's
   raw-substring reading would have caught.
2. **AC5 as first written was wrong**, not the code: the body partial interpolates the secret three
   times (element + two header hints). The test asserts the true invariant; the AC table above is
   corrected.
3. `qa-tester` covered AC1–AC8 in 8 new tests. Not covered by any test: real browser behaviour
   (Esc/focus trap/`<dialog>`), and the `.token-*` CSS rules — `test_ui_css_tokens.py` still scans
   only `app.css`, so a typo'd custom property in `modal.css` fails silently (same blind spot that
   hides the pre-existing `.blockers-warning` bug at `modal.css:123`).
