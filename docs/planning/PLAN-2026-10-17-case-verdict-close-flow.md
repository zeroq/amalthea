# PLAN-2026-10-17 — Case verdict + close flow (TODO §12.8)

**Status:** APPROVED (user answered all five design questions: `1a, 2a+yes, 3a, 4both, 5b`)
**Scope:** TODO.md §12.8 (the last large item in the 12.6–12.9 batch)
**Implements:** `AGENTS.md` §2 Module B (incident lifecycle), §2 Module D (orchestration gateway — audit + realtime), §3 schema.

---

## 1. Summary

Case Details → General gains three things it does not have today:

1. **Inline metadata edits** — Severity, Scope, Type, Classification, Phase, and the new Verdict each get an edit icon that opens a `<dialog>` (no native prompts, per AGENTS.md §6), pre-populated from the Taxonomy vocabularies, posted to a single service-layer-validated endpoint.
2. **A Tags row** above the case description — always visible, with per-tag removal and an "Add tag" dialog that selects from existing tags or creates-on-assign.
3. **A verdict-enforced close flow** — a case cannot enter the Closed stage without (a) a non-`undefined` verdict and (b) a mandatory Markdown closing comment, both persisted to the ledger. Closing the case propagates: every linked Alert is closed and inherits the case's verdict, with one `AuditLog` row and one timeline event recording the whole operation.

Along the way two pre-existing defects are fixed, because the new flow would otherwise be built on broken ground (user decision **Q4both**):

- `apply_case_update` loses `closed_date` (and never sets `end_date`) because they are not in `save(update_fields=...)`.
- `apply_case_update` writes `target_type="case"` while the case Audit tab queries `target_type="Case"` — so *no* classification audit row has ever been visible on the case it describes.

---

## 2. Goals

| # | Goal |
|---|------|
| G1 | `verdict` on `Case` (default `undefined`) and `Alert` (default `undefined`), DB-CHECK constrained, exposed in `case_json`/`alert_json`/Query DSL. |
| G2 | A **Verdict** card on the Taxonomy page alongside the other classification vocabularies. |
| G3 | Single service-layer rule: **a case may not enter Closed without a closing verdict and a closing comment** — enforced in `apply_case_update`, so UI, API `PATCH /case/{id}`, `PATCH /case/_bulk`, and case-create-to-Closed are all blocked identically. |
| G4 | Inline edit icons for Severity / Scope / Type / Classification / Phase / Verdict on the General tab, served by one `<dialog>`-driven endpoint. |
| G5 | Tags row above the description with remove + Add Tag dialog (select existing, create-on-assign). |
| G6 | Closing a case closes **all** linked alerts (including `Imported` ones), copies the case's verdict onto each, and writes `AuditLog` + timeline events. |
| G7 | `closed_date` is persisted on close, cleared on reopen, and `end_date` defaults to `closed_date`. |

---

## 3. Non-goals

- No verdict UI on the Alert detail page beyond the field being present in the payload/`alert_json` (G1 only). Alert verdict is *displayed* where alerts are listed in the case's alert strip; no independent alert-close modal.
- No verdict field on `CaseTemplate` (precedent: `data_classification` is also absent there — `test_enum_contracts.py` CHOICE_FIELDS has no `case_template.data_classification`).
- No org-scoping of `Tag` (it is global by design; `create-on-assign` therefore mints org-visible tags — recorded as a known caveat, not a regression).
- No change to the automation/`CaseStatusChanged` trigger contract, and no new playbook dispatch on close (propagation is synchronous in-request for correctness; Celery would risk an alert still being open when the timeline renders).
- No `PATCH /case/_bulk` refactor — only the close rule is added to it. It still bypasses `apply_case_update` for ordinary fields.
- No new management command for the seeded `Closed` `AlertStatus` (data migration only).

---

## 4. Decisions (locked with user)

| ID | Question | Decision |
|----|----------|----------|
| **D1 (1a)** | Where is the close rule enforced? | **Service layer.** `apply_case_update` is the choke point; `ui.case_set_status`, API `PATCH`, `_bulk`, and create-to-Closed all call it. One rule, no bypass. API-first. |
| **D2 (2a+yes)** | How does propagation close alerts? | **Seed a new `Closed` `AlertStatus`** (stage `Closed`, order `5`) in a data migration. Prefer it; fall back to the lowest-`order` `stage="Closed"` status. `Imported` alerts also close. |
| **D3 (3a)** | Verdict token spelling? | **House-style snake_case tokens** — `undefined` / `true_positive` / `false_positive`, labels `Undefined` / `True Positive` / `False Positive`. The literal §12.8 spelling ("Undefined"/"True Positive") is the **label**, not the token. **Deviation recorded in `docs/spec/deviations.md`.** |
| **D4 (4both)** | Fix the two pre-existing defects now? | **Yes, both**, as prerequisites: (a) `closed_date`/`end_date` persistence, (b) `target_type` casing so the new audit rows are actually visible. |
| **D5 (5b)** | Add Tag dialog behaviour? | **Select existing + create-on-assign.** Matches every existing `get_or_create` attach path (`cases/views.py:1110,1162`). Known caveat: `Tag` is global, so minted tags are visible org-wide. |

---

## 5. Architecture

### 5.1 Stack

Unchanged: Django 5.2 + DRF + Channels (ASGI/Daphne), Celery/Redis, PostgreSQL (dev: sqlite), Django templates + HTMX + Tailwind v4, `<dialog>` modals, strict CSP (`script-src 'self'`, `style-src 'self'`, no inline scripts/styles — ADR-003).

### 5.2 Why a service-layer rule (D1)

`apply_case_update` (`cases/update.py:195-246`) is already the audited, permission-checked, org-scoped writer for case metadata. Putting the close rule there means:

- The UI path (`ui.case_set_status`, `ui/views.py:934-965`) gets it for free.
- The API path (`PATCH /api/v1/case/{id}`) gets it for free.
- The create path (`cases/create.py::create_case`) gets it for free.
- The bulk path (`cases/views.py:1881-1934`) is the *only* exception — it does not use the applier — so the rule is **extracted into a helper** `enforce_close_rule()` and called from both call sites.

The rule is expressed as a normal field-validation failure (`CaseUpdateError("ValidationError", msg, fields)`) so the applier's existing error handling turns it into a 4xx on the API and a message + redirect in the UI. No new error channel.

### 5.3 Propagation is synchronous (G6)

Closing alerts inside the same transaction keeps the timeline coherent — an analyst who closes a case must never see a still-open linked alert in the next render. The alert `UPDATE` is done with `QuerySet.update()` (no per-row signals), then one case-level timeline event + one `AuditLog` row summarise the operation. Realtime comes from the existing `append_timeline_event` → `publish_case_event` on-commit path (`cases/ledger.py:39-85`), group `case_{id}`.

---

## 6. Data model

### 6.1 `core/enums.py`

```python
#: Case/alert verdict (PLAN-2026-10-17 §12.8(3), decision D3). Tokens, not labels — house
#: style (Q-D3/precedent: classification fields store `unset`, not "Unset"). Deviation from
#: the literal §12.8 spelling is recorded in docs/spec/deviations.md (V-1).
CASE_VERDICT_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("undefined", "Undefined"),
    ("true_positive", "True Positive"),
    ("false_positive", "False Positive"),
)
CASE_VERDICT_VALUES: Final[tuple[str, ...]] = tuple(v for v, _ in CASE_VERDICT_CHOICES)
#: The verdicts a case must hold *before* it may enter the Closed stage (decision D1).
CASE_CLOSING_VERDICTS: Final[tuple[str, ...]] = ("true_positive", "false_positive")
```

### 6.2 `cases/models.py::Case`

```python
verdict = models.CharField(max_length=32, choices=list(CASE_VERDICT_CHOICES), default="undefined")
```

with `Meta.constraints` gaining `in_values("case_verdict_valid", "verdict", CASE_VERDICT_VALUES)`.

### 6.3 `alerts/models.py::Alert`

```python
verdict = models.CharField(max_length=32, choices=list(CASE_VERDICT_CHOICES), default="undefined")
```

with `in_values("alert_verdict_valid", "verdict", CASE_VERDICT_VALUES)`.

### 6.4 Migrations

| File | Contents |
|------|----------|
| `cases/migrations/00XX_case_verdict.py` | AddField `Case.verdict` + AddConstraint `case_verdict_valid`. |
| `alerts/migrations/00XX_alert_verdict.py` | AddField `Alert.verdict` + AddConstraint `alert_verdict_valid`. |
| `alerts/migrations/00XX_seed_closed_alert_status.py` | `get_or_create(value="Closed", defaults={"stage": "Closed", "order": 5})`. Idempotent. (Existing seeded statuses are `New`(1), `Triaged`(2), `Dismissed`(3), `Imported`(4) — `alerts/migrations/0003_seed.py`.) |

### 6.5 Contract surfaces

- `core/serializers.py::case_json` (~`:266-293`) — add `"verdict": case.verdict`.
- `core/serializers.py::alert_json` (~`:296-314`) — add `"verdict": alert.verdict`.
- `query/engine.py` `_case_fields()` (`:139-142`) and `_alert_fields()` — add a `verdict` string-column spec.
- `docs/spec/data-model.md:33-50`, `docs/spec/api.md`, `docs/spec/query-dsl.md` — document both fields.
- `compat/` is **deliberately untouched** (existing non-parity decision Q-D3: classification fields are not mirrored).

---

## 7. Service layer

### 7.1 `enforce_close_rule` — the one rule (G3, D1)

```python
def enforce_close_rule(case: Case, payload: Mapping[str, Any], *, previous_stage: str) -> None:
    """A case may not enter the Closed stage without a recorded verdict and a closing comment.

    Called from apply_case_update (the choke point) and from case_bulk_update (which does
    not use the applier). previous_stage is the case's stage *before* the payload is applied.
    """
```

Behaviour:

- Returns immediately if `previous_stage == "Closed"` (idempotent re-close) or the new stage is not `Closed`.
- Reads `verdict` from the payload if present, otherwise `case.verdict`. If it is not in `CASE_CLOSING_VERDICTS`, raise `CaseUpdateError("ValidationError", "Closing a case requires a verdict.", {"verdict": [...]})`.
- Reads `comment` from the payload. If it strips to empty, raise `CaseUpdateError("ValidationError", "Closing a case requires a closing comment.", {"comment": [...]})`.

### 7.2 `apply_case_update` changes

1. Add `"verdict"` to `CASE_SCALAR_FIELDS` (`cases/update.py:35-47`) and `"verdict"` to `_CASE_ENUM_FIELDS` so it validates against `CASE_VERDICT_CHOICES` like every other classification field.
2. Snapshot `previous_stage = case.status.stage` **before** `_set_case_fields`.
3. Call `enforce_close_rule(case, payload, previous_stage=previous_stage)` after `_set_case_fields`, before `save`.
4. Fix `closed_date`/`end_date` persistence (G7, D4a):

```python
closing = previous_stage != "Closed" and case.status.stage == "Closed"
reopening = previous_stage == "Closed" and case.status.stage != "Closed"
if closing:
    case.stamp_closed_date()  # sets closed_date if still None
    if case.end_date is None:
        case.end_date = case.closed_date
elif reopening:
    case.closed_date = None  # stamp_closed_date() never clears it
if closing or reopening:
    fields += ["closed_date", "end_date"]
```

5. Extend the audited-field set: add `severity` (timeline kind `severity-changed`, audit action `case.severity`) and `verdict` (kind `verdict-changed`, audit action `case.verdict`) alongside the existing four classification fields.
6. **Fix `target_type` casing (D4b):** `_emit_classification_changes` writes `target_type="case"`; the case Audit tab (`ui/views.py:828`) queries `target_type="Case"`. Change the writer to `"Case"` and update the one test that asserts the lowercase value (`tests/conformance/test_case_classification.py:433`).
7. Emit a `tags-changed` timeline event when `tags_touched` is set (cheap, keeps the ledger complete).

### 7.3 Alert status resolution + propagation (G6, D2)

```python
def resolve_closed_alert_status() -> AlertStatus:
    """The AlertStatus that means 'closed' (decision D2).

    Prefer the seeded `Closed` status; fall back to the lowest-`order` status with
    stage="Closed"; auto-create one as a last resort so a fresh DB can never strand a close.
    """
```

```python
def propagate_close_to_alerts(case: Case, verdict: str, *, actor: User | None) -> int:
    """Close every alert linked to `case` and carry the case's verdict onto each.

    Uses QuerySet.update() — no per-row signals. Emits one case-level AuditLog row and one
    timeline event summarising the operation. Returns the number of alerts closed.
    """
```

`propagate_close_to_alerts` is called from `apply_case_update` immediately after a successful close save, **inside the same transaction**, so a failure rolls back both the case close and the alert updates.

---

## 8. API contracts

| Endpoint | Change |
|----------|--------|
| `PATCH /api/v1/case/{id}` | Accepts `verdict`, `comment`. Closes → 200 with updated `case_json`. Closes without verdict/comment → 400 `{"verdict": [...]}` / `{"comment": [...]}`. |
| `PATCH /api/v1/case/_bulk` | Same rule applied via `enforce_close_rule` before any field is written; a rejected case is skipped with its error, the rest of the batch proceeds. |
| `POST /api/v1/case/` | Create-to-Closed requires `verdict` + `comment`; otherwise 400. |
| `case_json` | Now includes `verdict`. |
| `alert_json` | Now includes `verdict`. |

No new REST resource is added; the close verb is expressed through the existing status transition.

---

## 9. UI / UX

### 9.1 `ui/templates/ui/_tab_general.html`

- **Meta grid, second `<dl class="meta meta-col">`** (`:14-21`): each of Severity / Scope / Type / Classification / Phase gains an edit icon (`<button class="meta-edit" data-modal-open="case-field-<slug>">`) with an accessible name (`aria-label="Edit severity"`), `aria-hidden="true"` on the glyph, and `@can_mutate` gating. A new **Verdict** row is appended after Phase (display-only value + edit icon).
- **Tags row**: the existing Tags card (currently hidden when empty, below Pages) is replaced by a tags row **above the description `<div class="body md">`** in the first card. It always renders; with no tags it shows an empty-state hint. Each tag is a pill with a remove `<button>` (✕, accessible name); a single "Add tag" button opens the Add Tag dialog. Gated on `@can_mutate`.
- **Status control**: `Closed` is removed from the plain `case-actions` status `<select>` (`:24-33`), which now only moves between `New` and `InProgress`. A dedicated **"Close case"** button opens the close dialog. Reopening stays on the plain control.

### 9.2 Dialogs (all reuse `ui/_modal.html` + `modal.js`, never native prompts — AGENTS.md §6)

| Dialog | Trigger | Body | Posts to |
|--------|---------|------|----------|
| `case-field-<slug>` (6×) | edit icon | `<select>` of the field's vocabulary, current value selected | `ui-case-field-edit` |
| `case-close` | "Close case" button | verdict `<select>` (only `CASE_CLOSING_VERDICTS`) + Markdown `<textarea>` for the closing comment | `ui-case-close` |
| `case-tag-add` | "Add tag" button | checkbox list of existing tags + a create-on-assign text input | `ui-case-tag-add` |

Each is a real `<form method="post">` so the no-JS fallback is a plain POST (progressive enhancement per AGENTS.md §6).

### 9.3 New UI endpoints (`ui/urls.py` + `ui/views.py`, all `@login_required @not_viewer @require_POST`, all org-scoped via `resolve_case(..., write=True)`)

| Name | Params | Behaviour |
|------|--------|-----------|
| `ui-case-close` | `verdict`, `comment` | Delegates to `apply_case_update` with the case's current Closed-stage status value. Failure → message + redirect back to General with the dialog re-rendered open. |
| `ui-case-field-edit` | `field`, `value` | Validates `field` against the allowed inline set, delegates to `apply_case_update`. Unknown field → 404, never 500. |
| `ui-case-tag-add` | `tags` (multi), `new_tags` (text) | Delegates to `apply_case_update` with `addTags`. |
| `ui-case-tag-remove` | `tag` | Delegates to `apply_case_update` with `removeTags`. |

`ui.case_set_status` (`ui/views.py:934-965`) is reworked to delegate to `apply_case_update` rather than writing `status`/`closed_date`/`end_date` directly (D1). It keeps its existing `stage` form field, but the persistence and the close rule now live in the applier; the timeline/WebSocket kind therefore becomes the applier's `status-changed`, not `status-change` (see deviations **V-3** and §15).

### 9.4 Taxonomy page (G2)

- `ui/views.py::taxonomy` (`:3140-3171`) adds `verdict_choices=CASE_VERDICT_CHOICES` to the context.
- `ui/templates/ui/taxonomy.html` renders a fifth card **"Verdict"** with Code + Label columns, styled exactly like the existing four code cards (including the `mono` class on the code-valued cell, per §12.6).

---

## 10. Phases

### Phase B — Backend (agent: `django-backend`)
B1. `core/enums.py` verdict choices + `CASE_CLOSING_VERDICTS`.
B2. `Case.verdict` + `Alert.verdict` fields, constraints, migrations, `Closed` alert-status seed.
B3. `enforce_close_rule` + `apply_case_update` integration (scalar/enum field, stage snapshot, closed_date/end_date fix, severity + verdict audit, `target_type` → `"Case"`, `tags-changed` timeline event).
B4. `resolve_closed_alert_status` + `propagate_close_to_alerts`, wired into `apply_case_update`.
B5. `cases/create.py` create-to-Closed gate; `cases/views.py` `_bulk` gate.
B6. `case_json` / `alert_json` / `query/engine.py` verdict exposure.
B7. `docs/spec/deviations.md` (V-1: token-vs-literal verdict spelling), `docs/spec/data-model.md`, `api.md`, `query-dsl.md`.
B8. `tests/conformance/test_enum_contracts.py` entries; `tests/conformance/test_case_verdict_close.py`.

### Phase F — Frontend (agent: `django-frontend`)
F1. `ui/views.py` + `ui/urls.py`: `ui-case-close`, `ui-case-field-edit`, `ui-case-tag-add`, `ui-case-tag-remove`; rework `ui.case_set_status`.
F2. `_tab_general.html`: edit icons, Verdict row, Tags row above the description, Close-case button, `Closed` off the plain status select.
F3. Dialog body templates: `_case_field_form.html`, `_case_close_form.html`, `_case_tag_add_form.html`.
F4. Taxonomy: `taxonomy` view context + `taxonomy.html` Verdict card.
F5. `app.css` additions if needed (meta-edit button, tag pill) — hand-written utility classes only; `make css` is unavailable locally (arm64 binary).
F6. `tests/conformance/test_ui_case_verdict.py`.

**Dependency:** B1–B4 must land before F1–F3 compile and run green, because the UI delegates to `apply_case_update` with the new `verdict`/`comment`/`addTags` keys. B1–B4 and F4/F5/F6 can proceed in parallel; F1–F3 should start once B1–B4 are merged or at least present on the working branch.

---

## 11. Security

- Every new UI endpoint is `@login_required @not_viewer @require_POST`, org-scoped via `resolve_case(..., write=True)` — a cross-org case is a 404, never a 403.
- `ui-case-field-edit` whitelists its `field` parameter against a fixed set; an unknown value is a 404. It never reaches Django's `setattr`.
- The close rule is server-side. The dialog is a convenience; `PATCH /case/{id}` with `{"status": "Closed"}` and no verdict must still be a 400.
- No inline scripts/styles (ADR-003) — all dialogs go through `_modal.html` + `modal.js`.
- No new secrets, no `--no-verify`, secret scan run before every commit.
- `Tag` is global: `create-on-assign` mints an org-visible tag. This is pre-existing behaviour (every attach path already `get_or_create`s); it is not a regression, and it is documented as a caveat rather than fixed here.

---

## 12. Testing

| Test | Asserts |
|------|---------|
| `test_enum_contracts.py` | `("case_record", "verdict"): "case_verdict_valid"` and `("alert", "verdict"): "alert_verdict_valid"` in `CHOICE_FIELDS`; matching `GRADED_DOMAIN` entries refusing `("bogus", "")`; `test_h2_the_domain_table_covers_every_check_constraint` still passes. |
| `test_case_verdict_close.py` | Close blocked via UI/API/bulk without verdict or comment (no status change, no alert closed); close with verdict+comment succeeds and sets `closed_date` + `end_date`; reopen clears `closed_date`; linked alerts (including `Imported`) all close and inherit the verdict; one `AuditLog` + one timeline event for the propagation; audit rows use `target_type="Case"` and appear in the Audit-tab query; create-to-Closed blocked. |
| `test_ui_case_verdict.py` | Taxonomy renders the Verdict card; the General tab renders edit icons, the Verdict row, the Tags row, and no `Closed` option in the plain status select; `ui-case-field-edit` is 404 for an unknown field, 404 cross-org, denied for a viewer; `ui-case-close` with `verdict=undefined` re-renders with an error and leaves the case open. |
| `test_case_classification.py` | Updated where it asserts the old lowercase `target_type="case"`. |

**Deviation note:** `make css` / `make css-check` cannot run locally (the repo's `scripts/tailwindcss` is a Mach-O arm64 binary and the shell is x86_64). CSS is verified by hand-written utility classes only; the full `pytest` suite is run on request, not on every edit.

---

## 13. Risks

| Risk | Mitigation |
|------|------------|
| The applier is called from many places; adding the close rule could break an existing caller that closes cases incidentally (e.g. a template, a bulk import). | `enforce_close_rule` is a no-op unless the stage is *actually entering* `Closed`; every close caller already intends to close. Grep for `stage == "Closed"` / `CaseStatus` writes before merging. |
| Seeding a `Closed` `AlertStatus` changes the alert list for existing deployments. | `get_or_create` is idempotent; existing `Dismissed` alerts are untouched — only new closes use `Closed`. |
| `QuerySet.update()` on alerts bypasses per-row signals. | Intentional (matches the existing `merge` helper's convention). The case-level timeline event + audit row carry the information the UI needs. |
| Two agents editing the same files. | Backend owns `cases/`, `core/`, `query/`, `alerts/`, `docs/spec/`, `tests/conformance/test_enum_contracts.py`, `test_case_verdict_close.py`. Frontend owns `ui/`, `ui/templates/`, `ui/static/`, `tests/conformance/test_ui_case_verdict.py`. `ui/views.py` is frontend-only. |
| `apply_case_update` is already long; bolting the close rule on makes it harder to read. | The rule is extracted into `enforce_close_rule`, and propagation into `propagate_close_to_alerts`, so the applier gains only a stage snapshot and two calls. |

---

## 14. Open questions

None blocking. Recorded for later:

- Should a verdict be settable on an *unclosed* case via the inline edit icon, or only at close time? **Resolved in favour of editable** — the applier supports it for free, and analysts may want to mark a case TP before containing it. Kept in scope as a sixth inline-edit field.
- Should `Tag` become org-scoped? **Out of scope** — noted as a future §12.x item; every attach path would need to change.

---

## 15. Implementation status (post-build, 2026-10-17)

**Phases B and F are implemented.** Verified locally:

- `ruff check .` clean; `ruff format --check .` clean (310 files) — this plan doc's Python code blocks are ruff-formatted so the doc itself passes the gate.
- `mypy` — "Success: no issues found in 109 source files".
- `manage.py check` — 0 issues; `makemigrations --check --dry-run` — no changes.
- `pytest` slices: `test_case_verdict_close.py` + `test_case_classification.py` + `test_case_classification_hardening.py` + `test_enum_contracts.py` = 183 passed; `test_ui_case_verdict.py` = 27 passed; `test_ui_loop.py` + `test_ui_p2_wiring.py` + `test_viewer_readonly_ui.py` + `test_viewer_affordances.py` + `test_viewer_precedence.py` = 90 passed; `test_realtime.py` = 14 passed; `test_tenant_scoping.py` = 105 passed; `test_ui_urls_smoke.py` + `test_ui_markdown.py` = 36 passed.

**Deviations from this plan, all recorded in `docs/spec/deviations.md`:**

- **V-1** — verdict tokens are snake_case, not the literal `Undefined` / `True Positive` / `False Positive` spelling (the labels are unchanged).
- **V-2** — closing a case closes and relabels linked alerts, and a `Closed` `AlertStatus` is seeded (widens the original `Alert` sketch in AGENTS.md §3).
- **V-3** — the General-tab status write now emits `status-changed`, not `status-change`, superseding §9.3 above. Cosmetic: the API mapped both to `"custom"`, and `app.css` now styles both dot selectors.

**Not done here (out of scope, unchanged):** `Tag` org-scoping (§9 caveat); the new tracked-change kinds (`classification-changed`, `severity-changed`, `verdict-changed`, `tags-changed`, `alerts-closed`) render the default grey timeline dot (only the status dot is keyed to a colour).

**Independent verification is pending** — no `verifier` / `security-auditor` pass has run (the subagent pool returned 503 throughout), so §12.8 stays `[~]` in TODO.md and out of `COMPLETED.md`.

**Open follow-up:** `make css` cannot run locally (arm64 `scripts/tailwindcss`), so the §F5 `app.css` additions (`.meta-edit`, `.case-tags`, `.case-tags-label`, `.tag-list`, `.field`, `.tl-status-changed:before`) were hand-written into the file's existing commented component section and are **not** re-minified into the compiled block; a maintainer on a supported platform should run `make css` before release.
