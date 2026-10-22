# BRIEF — 2026-10-21 — Observable extraction suggestions, provenance & manual acknowledgement

**For:** `django-backend` (P1–P4), then `django-frontend` (P5–P6)  
**Plan:** `docs/planning/PLAN-2026-10-21-observable-extraction-suggestions.md` (APPROVED)  
**Verifier:** `verifier` (P8)

## Goals
Make extraction controlled: provenance (JSON paths), more aggressive feed-aware filtering, suggestions created on extraction with **no** CaseObservable link and **no** playbook triggers, manual accept/reject/bulk, **triggers fire only on acceptance**, suggestions attached to alert, re-extract must re-suggest rejected items, manual `add_observable` stays immediate.

## Current state (ground truth)
- `observables/extractor.py`: `json_leaves()` walks only values, `PATTERNS` mail/url/ip/file/hash/fqdn, non-overlapping, normalization, `_STOPLIST`, dispatch on new Observable or new CaseObservable link via `dispatch_observable_linked`. `extract_into_case` called unconditionally from `cases/create.py`, `ui/views.py`, `alerts/escalation.py`.
- `AlertObservable` (`alerts/models.py`), `CaseObservable` (`cases/models.py`), `Observable` has `enrichment_data`, global dedup by `(owner_org, data_type, normalized_data)`.
- `automation/registry.py` fires `observable.created`.

## Hard constraints
- Suggestions attached to **alert** and to **case** where applicable.
- **Do not** create `CaseObservable` or fire triggers on extraction/suggestion creation.
- **Triggers fire only on acceptance** (on new link/observable created at accept time).
- If rejected/ignored, **re-extraction of the same alert must re-suggest it** (no permanent learning/suppression).
- **Manual `add_observable` / `add_observable_with_status` remain immediate** (no staging).
- More aggressive filtering; show filtered items in UI “Filtered out” section (so analysts can see/override).
- Suggestions card is a **collapsible card in Observables tab, below** the observables list.
- **Bulk accept/reject** from day 1.
- Preserve org scoping (ADR-004), global dedup, non-overlapping extraction, conservative typing.
- No new CSS classes if possible; keep undefined-class backlog 42.
- `make check` green. Targeted pytest for new/changed tests.

## P1 – Provenance in extractor
1. Add `json_paths_and_values(payload: Any) -> list[tuple[str, Any]]` in `observables/extractor.py` (dot notation). Keep `json_leaves()` for compatibility.
2. Extend `Finding` with `source_paths: list[str] = []`, `source_alert_id: str | None = None`, `source_context: dict[str, Any] = {}`.
3. Update `find_observables` to accept/use path context (or compute) and attach provenance to findings.
4. Thread `source_alert_id` through extraction paths.

## P2 – Suggestion model + migration
1. Add `ObservableSuggestion` to `observables/models.py` with fields per plan: owner_org, data_type, normalized_data, raw_data, status (proposed/accepted/rejected/ignored), source_alert (FK Alert null/blank), source_paths (JSON), reason, reviewed_by, reviewed_at, case (FK Case null/blank), observable (FK Observable null/blank), enrichment_data, filtered_out, filter_reason.
2. Unique constraint: `UniqueConstraint(fields=['owner_org','data_type','normalized_data','case','source_alert'], name='uniq_suggestion_per_target_alert')`
3. Indexes for listing by case/alert/status/org.
4. Migration in `observables/migrations/` (create and apply via makemigrations).
5. Export from models if needed.

## P3 – Filtering (more aggressive)
1. Create `observables/filtering.py` with `should_keep_finding(finding, context) -> tuple[bool, str | None, str | None]`.
2. Defaults: block/flag CrowdStrike portal URLs matching `falcon.*.crowdstrike.com/activity*`, suppress hashes/values from known platform ID fields (`agent_id`, `agent_scan_id`, `aggregate_id`, `composite_id`, `poly_id`, `cid`, `config_id_*`, `scan_id`, `event_id` in noisy contexts), etc. Be aggressive but log reasons.
3. Wire into extraction; filtered findings become suggestions with `filtered_out=True`, `filter_reason` set (still visible).

## P4 – Staging extraction service
1. Create `observables/suggestions.py` with `create_suggestions_for_case(case, texts, source_alert=None, **kwargs)`.
2. Build findings with provenance, apply filters, create/update `ObservableSuggestion` rows as `proposed` (idempotent via unique constraint). **Never** create `CaseObservable`, **never** call `dispatch_observable_linked` here.
3. Switch **all** extraction call sites to use staging:
   - `cases/create.py` (unconditional extraction on create)
   - `ui/views.py` (case create)
   - `alerts/escalation.py` (alert escalation)
4. **Do not** change `add_observable` / `add_observable_with_status` (manual add stays immediate).
5. Suggestions attached to `source_alert` when present.

## Verification for P1–P4
- Extraction creates suggestions only (no CaseObservable rows created by extract path)
- No playbook triggers fired on extraction (dispatch only on accept)
- Provenance present on suggestions
- Filtered items recorded with reason
- Re-extraction of same alert+case doesn’t duplicate suggestions
- `make check` green

## Next (P5–P6, django-frontend after API stable)
- Accept/reject/bulk API + actions, dispatch on accept only
- Collapsible “Suggested observables” card below observables list in Observables tab
- Filtered-out section, provenance display
- Alert context visibility
