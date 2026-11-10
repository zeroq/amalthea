# PLAN — Apply observable rules to existing unlinked alerts

**Date:** 2026-11-09
**Status:** Approved 2026-11-09 — Q1–Q6 answered (A, same set, keep, exact, separate, yes)
**Area:** Ingestion source detail → Observable extraction
**Related:** `PLAN-2026-10-16` P7 / P7.1 (mapping re-apply), `ingest/remap.py`, `ingest/observable_rules.py`

---

## 1. Summary

Saving a feed's observable rules currently writes `mapping_config` immediately. Existing alerts do
not gain observables until someone uses the separate **Apply to unlinked alerts…** path (which happens
to re-sync observables as part of a field re-map), re-sends the payload, or the alert is replayed.

The requested change makes the observable-rule save a two-step, previewed bulk operation, mirroring the
existing mapping-apply confirmation: validate the candidate rules, show what they would extract across
the feed's existing **unlinked open** alerts, then save + extract only on confirmation.

No new queue or Celery task: the candidate set is already bounded by `MAX_REMAP_ALERTS` (5,000) and the
existing modal path is synchronous.

---

## 2. Goals

- Clicking the observable save trigger validates the submitted rules and **writes nothing**.
- A confirmation modal states:
  - how many alerts are eligible;
  - what each observable type would extract;
  - how many links would be new vs already present;
  - what is deliberately left alone (field values, linked/closed alerts, existing links);
  - cap / truncation / unknown-type warnings.
- Confirming saves the observable rules and extracts observables in **one transaction**.
- Extraction is append-only and idempotent: re-running adds no duplicate links and never removes
  evidence when a rule is deleted.
- Reuse the existing modal, CSRF, admin gate, and audit conventions.

## 3. Non-goals

- No change to the JSONPath engine, normalization, or observable vocabulary.
- No removal of `AlertObservable` links when a rule is removed.
- No backfill of case-linked / dismissed / imported alerts.
- No API endpoint; this is the source-detail UI.
- No re-evaluation of field mapping values as part of an observable-only apply.
- No background task for >5,000 candidates (refuse and explain, as mapping apply does).

---

## 4. Current behaviour (verified)

- `ui/views/ingestion_sources.py::_source_mapping_post` routes `form=observables` directly to
  `_save_observable_rules`; `intent` is ignored for this form.
- `_save_observable_rules` validates via `build_observable_config`, writes `_persist_mapping`, and
  redirects. No alert/observable write.
- `ingest/remap.py::remap_alerts` already calls `sync_alert_observables(alert, config=config)` for every
  candidate, so the **Apply to unlinked alerts…** field path incidentally extracts observables.
- `preview_alerts` previews only field changes; it does not preview observable extraction.
- Candidate set: `candidate_alerts(source)` = same feed, `case is NULL`, status stage in
  `New` / `InProgress`.
- `sync_alert_observables` is append-only and returns `created`, `existing`, `skipped_unknown_type`,
  `truncated`, `warnings`.
- Existing modal pattern: `source_detail.html` includes `ui/_modal.html` +
  `_source_remap_confirm_body.html`, carrying the candidate config in a hidden `pending_config` and
  re-validating it on confirm.

---

## 5. Proposed UX

### Q1 — trigger (locked: A)

**A. Keep Save immediate + add a second button.**
`Save observable rules` stays as today; add `Apply to unlinked alerts…` beside it, identical to the
mapping card. Only the second button opens the modal.

The rest of this plan assumes **A**: the Save path is unchanged and must keep writing immediately.

### Modal content (locked A)

Title: **Apply these observable rules to existing alerts?**

- "This saves the observable rules and extracts them on **N unlinked open alerts** from `feed-slug`."
- If zero candidates: "There are no unlinked open alerts to extract from." Applying still saves the
  rules and adds no links.
- If over cap: same refusal wording as mapping; confirm disabled; neither rules nor links written.
- Table (when candidates exist):

  | Type | Values found | New links | Already linked |
  |------|--------------|-----------|----------------|
  | mail | 12 | 9 | 3 |
  | ip   | … | … | … |

- Warnings:
  - unknown observable types: skipped, named;
  - extraction truncation / per-payload candidate cap;
  - linked/closed/dismissed alerts are never touched;
  - removing a rule does **not** remove links already extracted (evidence is append-only);
  - field values and mapping records are not changed by this pass.
- Actions: `Save & extract` (primary), `Cancel`. The immediate `Save observable rules` button remains
  the way to save without applying.
- Success flash: "Observable rules saved — N type(s). Extracted X new link(s) on Y alert(s); Z already
  present."
- Reuse `ui/_modal.html`; new body `ui/_source_observable_apply_body.html`; no inline JS/CSS.

---

## 6. Architecture

### 6.1 Preview service

Add `ingest/observable_apply.py` (or an `ingest/remap.py` sibling section) with read-only dataclasses:

```python
@dataclass(frozen=True)
class ObservableTypePreview:
    found: int
    new_links: int
    existing_links: int


@dataclass(frozen=True)
class ObservableApplyPreview:
    candidates: int
    by_type: dict[str, ObservableTypePreview]
    alerts_with_values: int
    unknown_types: tuple[str, ...]
    truncated_alerts: int
    over_cap: bool
```

`preview_observable_rules(source, config, *, limit=MAX_REMAP_ALERTS)`:

1. Use `candidate_alerts(source)` — the single candidate predicate.
2. For each alert, run `extract_observable_candidates(config, alert.raw_payload)`.
3. For each candidate with a known `ObservableType`, compute the canonical key using the existing
   normalization helpers (no `get_or_create` in preview) and check the alert's existing
   `AlertObservable` links for that type/key.
4. Count `found`, `new_links`, `existing_links` per type; count alerts with at least one value.
5. Record unknown types, truncated extractions, and `over_cap` from `summarise_candidates`.
6. **No writes.** A read-only regression test asserts source, alerts, links, and audit are unchanged.

The canonical-key helper must be shared with `sync_alert_observables` so preview and apply cannot
disagree. Prefer a small pure helper in `observables/alert_sync.py`:
`candidate_link_key(obs_type, raw_value) -> tuple[type_id, normalized_data]`.

### 6.2 Apply service

Add `apply_observable_rules(source, config, *, actor=None, limit=MAX_REMAP_ALERTS) -> ObservableApplyResult`:

- Reuse `candidate_alerts`.
- For each alert call `sync_alert_observables(alert, config=config, actor=actor)`.
- Aggregate `created` / `existing` / `skipped_unknown_type` / truncation and per-type counts.
- Do **not** call `remap_alerts`: this pass must not rewrite `title`/`severity`/dates or refresh the
  field `mapping` record when only `observables` changed.
- Return enough detail for the flash + audit.

### 6.3 Routes / view

`_source_mapping_post`:

```python
if request.POST.get("form") == "observables":
    intent = request.POST.get("intent") or "save"
    if intent == "apply":
        return _confirm_observable_apply(request, source)
    if intent == "apply_confirmed":
        return _apply_observable_rules(request, source)
    return _save_observable_rules(request, source)
```

- `_confirm_observable_apply`: validate with `build_observable_config`; on error use
  `_reject_observables`; on success render the page with `observable_apply_pending=preview_...` and
  `pending_config=json.dumps(result.config)`. No writes.
- `_apply_observable_rules`: read `pending_config`, re-validate with `parse_json_config` (client cannot
  smuggle rules); if over cap refuse; otherwise `transaction.atomic()` → `_persist_mapping` (if changed)
  → `apply_observable_rules` → audit; redirect with flash.
- No-op config: if `observable_save_is_noop`, still perform extraction when the analyst confirmed
  `Save & extract`.
- Keep `_save_observable_rules` as the immediate Save path; it must not extract.
- Context additions in `_source_detail_context`: `observable_apply_pending`, `observable_apply_fields`,
  `observable_apply_confirm_fields`, `observable_apply_confirm_disabled`, `remap_cap`.

### 6.4 Audit

Add `identity/audit.py::record_ingestion_source_observables_applied` with action
`ingestion_source.observables_applied`:

```json
{"changed": ["observables"],
 "alerts": {"candidates": 19, "with_values": 17, "new_links": 31, "existing_links": 4},
 "types": {"mail": {"found": 20, "new": 18, "existing": 2}}}
```

The normal `ingestion_source.updated` row still records the config edit. Counts only; no raw observable
values or payloads.

---

## 7. UI / template details

- Add to `ui/templates/ui/source_detail.html`, next to the existing mapping modal:
  `{% if observable_apply_pending %}` include `_modal.html` with
  `body_template="ui/_source_observable_apply_body.html"`, title, confirm text `Save & extract`,
  hidden `pending_config`, `form=observables`, `intent=apply_confirmed`, autoopen.
- In the observable card:
  - default submit stays `Save observable rules` and remains immediate;
  - add `Apply to unlinked alerts…` (`name="intent" value="apply"`) beside it;
  - only `intent=apply` renders the modal; `Save` carries no intent.
- The standalone no-JS behavior is a normal POST → modal page; JS `modal.js` autoopens it.
- Build the modal body with a per-type table; use `get_item` for dict lookups; no `|safe`.
- Keep the existing observable card copy, but add "Changing rules does not remove existing links."

---

## 8. Security

- Existing admin gate on the source page remains; POSTs are CSRF-protected.
- Re-validate `pending_config` server-side; the hidden field is not trusted.
- Candidate predicate is fixed: same org/feed, unlinked, open only.
- Preview is read-only; cap prevents unbounded scans.
- No raw observable values in audit rows, flash messages, or modal counts.
- Unknown types skip with a count; no auto-creation of `ObservableType` rows.
- No secrets or payloads in new templates.

---

## 9. Phases, tasks, acceptance criteria

### P1 — Preview service + tests
- `ingest/observable_apply.py` dataclasses + `preview_observable_rules`.
- Shared canonical-key helper in `observables/alert_sync.py`.
- **AC-SOA-1:** preview uses exactly `candidate_alerts`; linked/closed/dismissed alerts are excluded.
- **AC-SOA-2:** preview returns per-type `found/new/existing`, alerts-with-values, unknown types,
  truncation, and over-cap; writes nothing.

### P2 — Apply service + tests
- `apply_observable_rules`; aggregate counts; append-only via `sync_alert_observables`.
- **AC-SOA-3:** confirm adds only missing links; a second run adds 0; no links removed when a rule is
  deleted.
- **AC-SOA-4:** no field values, dates, status, assignment, case links, or field `mapping` records are
  changed by an observable-only apply.

### P3 — View/routing/modal
- `intent=apply` / `apply_confirmed` for `form=observables`; validation, cap refusal, atomic save+apply,
  audit, flash; modal body/template; card buttons.
- **AC-SOA-5:** first POST writes nothing (config, links, audit unchanged) and renders the modal.
- **AC-SOA-6:** confirm re-validates `pending_config`; invalid/over-cap writes nothing; success
  redirects with counts.
- **AC-SOA-7:** cancel/non-admin/CSRF/invalid row behavior matches existing observable-save safety.
- **AC-SOA-8:** audit row `ingestion_source.observables_applied` carries counts only.

### P4 — Verification
- Backend + UI tests, focused suite, `make check-fast`, independent verifier.
- **AC-SOA-9:** no regressions in existing source-mapping, remap, observable-editor, ingestion tests.

---

## 10. Testing matrix

- Empty feed: modal says no candidates; `Save & extract` succeeds with 0 links; immediate Save remains
  a normal save.
- One alert, one rule, no link: preview 1/1/0; apply creates 1; second apply 0 new.
- Same observable value on two alerts: two links, one `Observable` row; preview counts two new links.
- Existing link: preview `existing_links=1`, apply adds 0.
- Rule removal: existing links survive.
- Linked alert / Dismissed / Imported / another feed / another org: never touched.
- Unknown type in JSON box: skipped with count, no `ObservableType` row created.
- Truncated extraction / >5k candidates: warning; over-cap confirm disabled and server refuses.
- Invalid JSONPath / non-admin / missing CSRF: no writes.
- Audit contents: changed keys + counts, no values.
- Template: modal id, autoopen, `form=observables`, `intent=apply_confirmed`, table labels, no `|safe`.

---

## 11. Risks / trade-offs

- **Extra preview cost:** up to 5,000 alerts × JSONPath extraction + link lookups. Bounded and
  synchronous like the mapping preview; if it is too slow in practice, the follow-up is a Celery task
  (out of scope).
- **Preview/apply drift:** prevented by the shared canonical-key helper and a test comparing preview
  counts with the subsequent apply result.
- **Modal fatigue:** B makes every save a dialog; if that is too heavy, choose A (second button).
- **Visibility of removed rules:** links are retained by design; the UI must say so or analysts will
  expect deletion.
- **No field remap:** an operator who changed both fields and observables must use the mapping apply
  path for fields; this plan intentionally does not duplicate it.

---

## 12. Decisions locked (2026-11-09)

1. **Trigger/UX — A:** Save stays immediate; add `Apply to unlinked alerts…`.
2. **Scope:** same `candidate_alerts` as mapping — same feed, unlinked, `New`/`InProgress`.
3. **Rule removal:** keep existing alert observables; extraction is append-only.
4. **Preview detail:** exact per-type `found` / `new` / `already linked`, capped.
5. **Audit:** separate `ingestion_source.observables_applied` action.
6. **Field side effects:** none; use `sync_alert_observables`, not `remap_alerts`.

---

## 13. Handoff

After Q1–Q6 are answered, produce an Implementation Brief for `django-backend` / `django-frontend` with
the chosen UX, exact file paths, AC-SOA-1..9, and the test commands. Any deviation from this plan must be
recorded in this document.

---

## 14. Implementation notes (2026-11-09)

- **Canonical key uses the type name, not `type_id`.** `candidate_link_key` returns
  `(obs_type.name, normalized_data)` so it can be compared directly with
  `AlertObservable.values_list("observable__data_type__name", "observable__normalized_data")` without a
  second lookup. Names are unique on `ObservableType`; identity is equivalent.
- **Empty-config bootstrap is allowed on confirm.** `build_observable_config` adds the current
  `{version, fields: {}}` shape when the first observable rule is saved into `{}`. The confirm path
  accepts that shape-only addition, but refuses any non-empty `fields`/`value_maps` difference.
- **Tests are split into `test_source_observable_apply.py` (routing/preview/confirm) and
  `test_source_observable_apply_ui.py` (rendered modal)**, rather than extending the existing editor
  test with unrelated cases.
