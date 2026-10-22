# Plan — Observable extraction suggestions, provenance & manual acknowledgement

**Date:** 2026-10-21  
**Status:** APPROVED — ready for implementation handoff  
**Owner:** Planner/Tech Lead  
**Scope:** Make observable extraction controlled/safe: add provenance, filtering, suggestion staging, manual accept/reject before linking to case (and before firing playbook triggers).

## 1. Problem statement (from user)
- "Currently the observable extraction feels uncontrolled."
- "Hashes are extracted but the analyst has no clue where they belong to and urls which lead to the detection tool (e.g. crowdstrike website)."
- "We need to have on the one hand more enrichment to the observables when extracting, some filtering method, and maybe each needs a manual acknowledgement before being added as observable to the case."

## 2. Current behavior (verified)
- `observables/extractor.py`: `json_leaves()` walks **only string values** (keys ignored), regex `PATTERNS` in order `mail,url,ip,file,hash,fqdn`, non-overlapping spans, per-type normalization, `_STOPLIST`, global dedup by `(owner_org, data_type, normalized_data)`, dispatch via `dispatch_observable_linked` **only on new Observable or new CaseObservable link**. `extract_into_case` is called **unconditionally** on case create (`cases/create.py`, `ui/views.py`) and during alert escalation (`alerts/escalation.py`).
- `AlertObservable` exists (`alerts/models.py`) with per-link tags/added_by; `CaseObservable` links observables to cases.
- Triggers fire on `observable.created` when link created (service-driven, not post_save).
- Provenance: none today — regex runs over flattened blob, no JSON path/field attribution.

Evidence: walk of CrowdStrike alert showed many hex GUIDs (agent/config/composite/poly IDs) and the Falcon portal URL extracted as `url` with no way to tell source field.

## 3. Goals
- **G1 Provenance:** Every extracted finding must carry source context (alert id + JSON path + maybe surrounding key) so "hash extracted from `sha256`" vs "hash extracted from `agent_id`" is visible.
- **G2 Filtering:** More aggressive, source/feed-aware filtering to drop noise (e.g., CrowdStrike portal URLs matching `falcon.*.crowdstrike.com/activity*`, known platform ID fields like `agent_id`, `composite_id`, `poly_id`, `config_id_*` where not useful) without globally breaking IOCs.
- **G3 Enrichment:** Enrich observables on/after suggestion (async via Celery) — e.g. first-seen/seen-in counts, link to source alerts/cases; reserve external API calls for later/opt-in.
- **G4 Manual acknowledgement:** Suggestions created on extraction; **do not** create `CaseObservable` or fire playbook triggers until analyst accepts. Re-extract must be idempotent (no duplicate suggestions). **Triggers fire only on acceptance.**
- **G5 Preserve invariants:** Global dedup of `Observable` rows, org scoping (ADR-004), tenant isolation, no auto-overwrite of existing shared evidence, non-overlapping extraction, conservative typing.

## 4. Decisions (from user)
1. Staging/default for **all extractions** (case create, escalation, etc.)
2. If an analyst **rejects** a suggestion, **re-extraction of the same alert must re-suggest it** (no permanent learning/hiding on reject)
3. **Triggers fire only on acceptance** (move dispatch from extraction/link creation to acceptance)
4. Suggestions shown as a **collapsible card in Observables, below** the observables list
5. **Bulk accept/reject** from day 1
6. **More aggressive filtering** (start aggressive for known noise; be careful not to filter must-keep types)
7. Suggestions attached to **alert** (and visible in context of alert/case)
8. **Manual add via `add_observable` remains immediate** (no staging)

## 5. Architecture / design

### 5.1 Provenance in extractor
- **Walker change:** Add `json_paths_and_values(payload)` returning `list[(path, value)]` where `path` uses dot notation (e.g. `device.external_ip`, `sha256`, `falcon_host_link`). Keep backward-compatible `json_leaves()` if needed.
- **Finding model:** Extend `observables/extractor.py:Finding` with `source_paths: list[str] = []`, `source_alert_id: str|None = None`, `source_context: dict = {}`.
- **Extraction:** `find_observables` accepts optional path context; when building findings, record which paths contributed. `extract_into_case` should accept/pass provenance + alert context.

### 5.2 Suggestion staging model
Create `ObservableSuggestion` (migration):

```python
class ObservableSuggestion(UUIDModel, TimeStampedModel):
    owner_org = FK(Organisation, ..., on_delete=CASCADE)
    data_type = FK(ObservableType, ..., on_delete=PROTECT)
    normalized_data = CharField(max_length=512)
    raw_data = TextField()
    status = CharField(
        max_length=16,
        choices=[
            ("proposed", "Proposed"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("ignored", "Ignored"),
        ],
        default="proposed",
    )
    source_alert = FK(Alert, null=True, blank=True, on_delete=SET_NULL, related_name="suggestions")
    source_paths = JSONField(default=list)
    reason = TextField(blank=True)
    reviewed_by = FK(User, null=True, blank=True, on_delete=SET_NULL)
    reviewed_at = DateTimeField(null=True, blank=True)
    case = FK(Case, null=True, blank=True, on_delete=CASCADE, related_name="suggestions")
    observable = FK(Observable, null=True, blank=True, on_delete=SET_NULL)
    enrichment_data = JSONField(default=dict)
    filtered_out = BooleanField(default=False)
    filter_reason = TextField(blank=True)
```

**Constraints/indexes:**
- Unique constraint to prevent duplicates on re-extract: `UniqueConstraint(fields=['owner_org','data_type','normalized_data','case','source_alert'], name='uniq_suggestion_per_target_alert')`
- Indexes for listing by case/alert/status/org

**Behavior:**
- On extraction: create suggestions as `proposed` (or track filtered). **Do not** create `CaseObservable`. **Do not** dispatch playbooks.
- On accept: get/create global `Observable` (respecting existing dedup), create `CaseObservable` if missing, set `suggestion.observable`, mark `accepted/reviewed_by/at`, **dispatch `observable.created` only if link created**.
- On reject/ignore: mark reviewed; **do not** create filter rules that permanently suppress (per Q2). Re-extraction must be allowed to re-suggest.
- Suggestions tied to alert (`source_alert`) and to case where applicable.

### 5.3 Filtering (more aggressive)
- **Field denylist by pattern:** Default rules to suppress known noise from CrowdStrike (and generally): `agent_id`, `agent_scan_id`, `aggregate_id`, `composite_id`, `poly_id`, `cid`, `config_id_*`, `scan_id`, `event_id` (when clearly platform IDs), etc. Configurable.
- **URL filtering:** Aggressively filter detection-portal/tool URLs (e.g. `falcon.*.crowdstrike.com/activity*`, similar). These can go to a "Filtered out" list in UI for visibility/debugging.
- **Type+context:** hash from platform-ID field → filtered by default (but show in filtered section). Hash from `sha256`/`md5`/`filename` context → prefer keep.
- **Hook:** `observables/filtering.py::should_keep_finding(finding, context) -> (keep: bool, reason: str|None, category: str|None)`. Apply during extraction; filtered findings still recorded as suggestions with `filtered_out=True` + reason (so analysts can see and override if needed).
- **Tunable:** settings for aggression level, org overrides later if needed.

### 5.4 Enrichment
- **Async via Celery:** `observables/tasks.py` task `enrich_observable(observable_id)` or `enrich_suggestion(suggestion_id)`. Called on accept (and optionally on suggestion creation for preview).
- **Internal first:** first-seen, seen-in cases/alerts count, related observables.
- **Storage:** `Observable.enrichment_data` (JSONB) exists; suggestions can show preview in `enrichment_data`.
- **Non-blocking.**

### 5.5 Manual acknowledgement flow
1. **Extract** (case create/escalation/all extractions) → provenance + filtering → create/update `ObservableSuggestion` (proposed; may be filtered_out). **No CaseObservable, no dispatch.**
2. **Review UI** (collapsible card in Observables tab, **below** the observables list) → show suggestions with type/value/provenance ("extracted from alert X, field `sha256`"), status, enrichment, filtered-out expandable section. Support **bulk accept/reject**.
3. **Accept** → resolve/create global observable, create `CaseObservable` if missing, mark suggestion accepted + link, **dispatch `observable.created` only now** if new link/observable created.
4. **Reject/Ignore** → mark reviewed. **No permanent filter learning** (Q2) — re-extraction can re-suggest.
5. **Suggestions attached to alert** — visible in alert context and case context as appropriate.

## 6. API / UI changes
- **API:** `GET /api/v1/cases/{id}/suggestions` (filter by status), `POST /api/v1/suggestions/{id}/accept`, `POST /api/v1/suggestions/{id}/reject`, `POST /api/v1/suggestions/bulk-accept`, `POST /api/v1/suggestions/bulk-reject`. Also consider alert-scoped listing.
- **Case detail Observables tab:** Add collapsible "Suggested observables" card **below** the existing observables list/table. Badge with count. Expand/collapse. Bulk selection + actions. "Show filtered out (N)" section.
- **Provenance display:** "Extracted from: [Alert title/id] → `source_paths`" with copyable values.
- **Keyboard accessible**, dark theme, no native dialogs.
- **Manual add unchanged:** `add_observable` remains immediate.

## 7. Phases with tasks & acceptance criteria

### Phase 0: Decisions & plan finalization — DONE
All questions answered; plan approved.

### Phase 1: Provenance in extractor (backend)
- [ ] Add `json_paths_and_values()` to `observables/extractor.py`
- [ ] Extend `Finding` with `source_paths`, `source_alert_id`, `source_context`
- [ ] Update `find_observables` to track/provide provenance
- [ ] Thread alert/case context through `extract_into_case`
- [ ] Keep backward compatibility where reasonable

**AC P1:** Findings carry provenance; existing behavior preserved in structure.

### Phase 2: Suggestion model + migration (backend)
- [ ] Add `ObservableSuggestion` to `observables/models.py`
- [ ] Create migration with indexes and unique constraint `uniq_suggestion_per_target_alert`
- [ ] Add to admin if useful (non-blocking)
- [ ] Export in models

**AC P2:** Migration applies cleanly; org-scoped; constraints correct.

### Phase 3: Filtering (more aggressive) (backend)
- [ ] Create `observables/filtering.py`
- [ ] Default rules for CrowdStrike portal URLs (`falcon.*.crowdstrike.com/activity*`) and platform ID fields/values
- [ ] Wire into extraction pipeline
- [ ] Record filtered_out + filter_reason in suggestions

**AC P3:** Aggressive defaults applied; filtered items visible in UI section; configurable.

### Phase 4: Staging extraction service (backend)
- [ ] Create `observables/suggestions.py` with `create_suggestions_for_case(case, texts, source_alert=None, **kwargs)`
- [ ] Switch **all** extraction points to staging: case create (`cases/create.py`, `ui/views.py`), alert escalation (`alerts/escalation.py`)
- [ ] Idempotent via unique constraint; no `CaseObservable` creation; **no dispatch on creation**
- [ ] Attach suggestions to alert and case
- [ ] Keep manual `add_observable` immediate (unchanged)

**AC P4:** New extractions create suggestions only; no links/triggers fired; re-extraction idempotent.

### Phase 5: Accept/reject API + actions (backend)
- [ ] API endpoints for list/accept/reject/bulk-accept/bulk-reject
- [ ] On accept: resolve/create observable, create `CaseObservable` if missing, mark accepted, **dispatch only on new link/observable**
- [ ] On reject/ignore: mark reviewed (no permanent suppression)
- [ ] Permissions enforce case/alert visibility + org scope
- [ ] Validation

**AC P5:** Accept triggers once; reject doesn't link/fire; bulk works.

### Phase 6: UI (frontend)
- [ ] Add collapsible "Suggested observables" card **below** observables list on case detail Observables tab
- [ ] Show provenance, type, value, status, enrichment
- [ ] "Show filtered out (N)" expandable
- [ ] Accept/reject single + **bulk** actions
- [ ] Keyboard accessible, dark theme
- [ ] Suggestions visible in alert context where appropriate

**AC P6:** Analysts can review/accept/reject/bulk; provenance visible.

### Phase 7: Enrichment scaffold (backend)
- [ ] Celery task in `observables/tasks.py` for internal enrichment
- [ ] Trigger on accept (and optionally suggestion creation)
- [ ] Display in UI

**AC P7:** Async enrichment works without blocking.

### Phase 8: Tests, docs, verification
- [ ] Conformance tests for staging flow, provenance, filtering, accept/reject/bulk, no-dispatch-on-extract, dispatch-on-accept
- [ ] Falsifiability (§7.5): "extraction does not create CaseObservable or fire triggers"; "accept fires trigger once"
- [ ] Update docs/specs
- [ ] `make check`, targeted tests
- [ ] Verifier pass

**AC P8:** All tests pass, gates green.

## 8. Risks
- **R1 Behavior change:** Tests expecting immediate observables/triggers (e.g. MVP loop) will need updates or careful adjustment.
- **R2 Trigger timing:** Moving dispatch is the biggest change — must be verified.
- **R3 Noise vs safety:** Aggressive filtering could hide IOCs; mitigated by showing filtered section + ability to accept manually? Or just visibility.
- **R4 Dedup complexity:** Suggestions + global observables + links need careful constraints.
- **R5 Re-suggest on reject:** Must ensure idempotency still holds and UI doesn't spam.

## 9. Files to touch
- `observables/extractor.py` - provenance
- `observables/filtering.py` (new)
- `observables/suggestions.py` (new)
- `observables/models.py` - ObservableSuggestion
- `observables/migrations/` - new migration
- `observables/tasks.py` (new or extend)
- `cases/create.py`, `alerts/escalation.py`, `ui/views.py` - switch to staging
- `cases/views.py`, `cases/urls.py`, `ui/urls.py`, `ui/templates/ui/*` - suggestions UI/API
- `automation/registry.py` - confirm dispatch still works on accept
- `tests/conformance/` - new tests

## 10. Handoff
Ready for implementation. Start with P1-P2 (provenance + model), then P3-P4 (filtering + staging), then P5-P6 (actions + UI), P7-P8.

## 11. Implementation deviations & post-review fixes (2026-10-21)

- **Accept semantics are one shared service.** `observables/suggestions.py::accept_suggestion`
  is now the single accept implementation used by both the JSON API and the UI. The first UI
  implementation called `add_observable_with_status()` (which already dispatches) and then
  dispatched a second time, firing automation twice for every newly created observable. The
  shared service uses `resolve_observable()` directly and dispatches exactly once. Covered by
  `tests/conformance/test_p6_suggestions_ui.py`.
- **UI bulk actions no longer fake a DRF request.** The bulk UI views built a `MockRequest` and
  passed it to an `@api_view` function, which raised
  `AssertionError: The 'request' argument must be an instance of 'django.http.HttpRequest'` — the
  bulk buttons were dead. They now call `bulk_review_suggestions()` directly, scoped to the case.
- **Modal body must load its filters.** `_suggestion_enrichment_modal_body.html` uses
  `|prettyjson` and was missing `{% load ui_tags %}`, raising `Invalid filter: 'prettyjson'`.
- **P7 (enrichment scaffolding) is not part of this implementation.** The UI renders
  `enrichment_data` when present, but no asynchronous enrichment task was added. P7/P8 remain open.
- **TheHive-compatible `POST /api/v1/case/{id}/observable` with `"extract": true` still extracts
  directly.** `cases/views.py` intentionally keeps immediate `extract_into_case()` semantics for
  wire compatibility (clients expect observables back from that call). It is the one extraction
  path that does not stage a suggestion. Recorded here as a deliberate deviation; revisit only
  with an API-compatibility decision.
- **Re-extraction now mirrors escalation.** `ui/views.py::case_extract_observables` runs one
  extraction pass per linked alert with that alert's `raw_payload` (instead of omitting
  `source_alert`), so it updates the existing suggestions rather than creating a parallel set.
  Rejected/ignored values are re-proposed by the service's `update_or_create` status reset.
