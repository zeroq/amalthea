# Implementation Brief — Apply observable rules to existing unlinked alerts

**Plan:** `docs/planning/PLAN-2026-11-09-source-observable-apply.md` (approved 2026-11-09, decisions A /
same-set / keep / exact / separate / no field side effects)
**Status:** Ready for implementation
**No migrations. No API route.**

## Shared contract

The backend owns these context keys; the frontend renders only these names:

```python
observable_apply_pending: ObservableApplyPreview | None
observable_apply_rows: list[dict]  # ordered: {"type", "found", "new_links", "existing_links"}
observable_apply_confirm_fields: dict | None
# {"form": "observables", "intent": "apply_confirmed", "pending_config": "<json>"}
observable_apply_confirm_disabled: bool
```

`ObservableApplyPreview` fields:

```python
candidates: int
by_type: dict[str, ObservableTypePreview]  # found/new_links/existing_links
alerts_with_values: int
unknown_types: tuple[str, ...]
truncated_alerts: int
over_cap: bool
```

Routing: `form=observables` + `intent=apply` previews; `intent=apply_confirmed` saves + extracts.
Plain `form=observables` (no intent) stays the existing immediate save.

## Backend tasks (`django-backend`)

1. **`observables/alert_sync.py`**
   - Add pure `candidate_link_key(obs_type, raw_value)` sharing `normalize` +
     `canonical_value_under`; use it in `sync_alert_observables`.
   - Extend `AlertObservableSyncResult` with per-type created/existing counts (default empty) without
     changing existing call sites.
2. **`ingest/observable_apply.py`**
   - Dataclasses above.
   - `preview_observable_rules(source, config, *, limit=MAX_REMAP_ALERTS)` using `candidate_alerts`.
     Read-only: no `get_or_create`, no saves, no audit. Track per-alert seen keys so two raw values that
     normalize identically count one new + one existing. Count unknown types, truncated extractions,
     alerts with values, over-cap.
   - `apply_observable_rules(source, config, *, actor=None, limit=MAX_REMAP_ALERTS)` using
     `sync_alert_observables` per candidate; aggregate counts/per-type; no field writes and no
     `remap_alerts`.
3. **`ui/views/ingestion_sources.py`**
   - Route observable intents before the existing save fallback.
   - `_confirm_observable_apply`: validate with `build_observable_config`; on error use
     `_reject_observables`; otherwise render `source_detail.html` with preview context and hidden
     `pending_config`. Writes nothing.
   - `_apply_observable_rules`: re-read/re-validate `pending_config` with `parse_json_config`; reject
     if changed keys include anything except `observables`; refuse over cap; then one
     `transaction.atomic()` around `_persist_mapping` (only if config changed),
     `apply_observable_rules`, and the new audit row. Redirect with flash counts.
   - Add the four contract keys to `_source_detail_context`.
4. **`identity/audit.py`**
   - `record_ingestion_source_observables_applied` with action
     `ingestion_source.observables_applied`; details = changed keys + aggregate counts + per-type
     counts. No raw values/payloads.
5. **Tests**
   - `tests/conformance/test_observable_apply.py`: preview/apply exactness, idempotency, append-only,
     candidate exclusion, unknown types, truncation, cap, no field/mapping-record side effects.
   - Extend `tests/conformance/test_source_observable_editor.py`: apply writes nothing; confirm saves +
     extracts + audits; invalid/tampered config, over-cap, non-admin, CSRF; immediate Save unchanged.

## Frontend tasks (`django-frontend`)

1. **`ui/templates/ui/source_detail.html`**
   - Observable card control row: keep `Save observable rules`; add
     `<button class="btn" type="submit" name="intent" value="apply">Apply to unlinked alerts…</button>`.
   - Add the modal below the mapping modal:
     `{% if observable_apply_pending %}` → include `_modal.html` with
     `modal_id="observable-apply-modal"`, `body_template="ui/_source_observable_apply_body.html"`,
     title `Apply these observable rules to existing alerts?`, confirm `Save & extract`,
     `form_action` = source detail URL, `extra_fields=observable_apply_confirm_fields`,
     `confirm_disabled=observable_apply_confirm_disabled`, `autoopen=True`, `size="md"`.
2. **New `ui/templates/ui/_source_observable_apply_body.html`**
   - State candidates / alerts-with-values; per-type table from `observable_apply_rows`
     (Type, Values found, New links, Already linked).
   - Show no-candidate text, over-cap refusal, unknown types, truncated alerts, and the invariants:
     field values untouched; linked/closed/dismissed alerts untouched; removing a rule does not remove
     existing links.
   - No `|safe`, no inline JS/CSS; reuse existing classes.
3. **Tests**
   - New `tests/conformance/test_source_observable_apply_ui.py`: button present once, modal id /
     autoopen / hidden fields, table rows, zero-candidate + over-cap copy, no inline script/style.

## Acceptance criteria

- `AC-SOA-1..9` in the plan.
- Immediate `Save observable rules` behavior is unchanged.
- Preview POST writes nothing (config, alerts, links, audit).
- Confirm is atomic, append-only, idempotent, and reports exact counts.
- Only same-feed unlinked `New`/`InProgress` alerts are touched.
- No field values or field `mapping` records are rewritten.
- Unknown types / truncation / cap are surfaced without partial writes.
- Audit is `ingestion_source.observables_applied` with counts only.

## Verification

Run focused tests, then `make check-fast`; hand off to `verifier` with the plan + this brief. Record any
deviation in the plan document.
