# PLAN 2026-11-06 — Alert observables from source mappings

**Status: VERIFIED — all 28 ACs met (2026-11-06).** Independent verification:
[`VERIFY-2026-11-06-alert-observables.md`](../verification/VERIFY-2026-11-06-alert-observables.md).
Security review: [`SECURITY-2026-11-06-alert-observables.md`](../reviews/SECURITY-2026-11-06-alert-observables.md)
(PASS; one pre-existing Medium residual risk on raw values in mapping warnings). This plan is the P6 candidate recorded in
`PLAN-2026-10-16-source-field-mapping.md` (“observables from the same config”), expanded into a
complete alert/case workflow.

Related: `AGENTS.md` §2 Module A (ingestion) and Module C (observables) · `alerts/models.py`
(`AlertObservable`) · `cases/models.py` (`CaseObservable`) · `observables/extractor.py` ·
`observables/suggestions.py` · `ingest/mapping.py` · `ingest/editor.py` · `ingest/pipeline.py` ·
`ingest/remap.py` · `alerts/escalation.py` · `ui/views/alerts.py` · `ui/views/ingestion_sources.py`

---

## 1. Summary

Alerts already have an `AlertObservable` join model, but the ingestion mapping cannot populate it.
Observables are only attached to an alert by the JSON API (`POST /api/v1/alert/{alertId}/observable`)
or by accepting a case suggestion, and the alert page currently renders them in a card inside
**General** rather than as a first-class section.

This plan adds one end-to-end path:

1. A source mapping can name **observable rules** — a type and one or more JSONPaths.
2. Ingest extracts matching scalar values, normalizes them through the existing observable vocabulary,
   and links them to the alert as `AlertObservable` rows.
3. The alert detail page gets a dedicated **Observables** tab listing those artifacts and the visible
   cases they appear in.
4. Merging an alert into an existing case — and importing one into a new case — copies the alert’s
   observables into `CaseObservable` links, firing the same `observable.created` dispatch a manual
   case link would fire, exactly once per new case link.

No schema migration is expected. The existing join models, uniqueness constraints, normalization
rules and automation dispatch semantics are reused.

---

## 2. Goals

1. Add a validated `observables` section to a feed’s `mapping_config`.
2. Extract **all** scalar matches from each configured path, not only the first.
3. Normalize and globally dedupe values through `observables.extractor.resolve_observable`.
4. Never fail an ingest because an observable rule is wrong: warn, skip, and preserve the alert.
5. Expose observables on the alert detail page as a tab with an honest empty state.
6. Copy alert observables to the case on merge and import, idempotently.
7. Make an existing “Apply to unlinked alerts” pass carry observable extraction too.
8. Keep source mapping feedback auditable and testable: rule errors at save time, extraction
   tolerances recorded on the alert.

## 3. Non-goals

- Replacing the existing **case suggestion** system. Text/payload extraction for case review remains
  unchanged.
- Auto-escalating alerts or auto-creating cases from observables.
- New observable types, enrichment providers, or playbook actions.
- Re-hashing, merging, or deleting existing `Observable` rows.
- Adding per-link tags or per-source observable metadata beyond what `AlertObservable` already has.
- Rewriting `raw_payload`. The original remains byte-preserved.
- A new alert-observable API surface beyond what already exists, unless §14 Q6 chooses to add a read
  endpoint for parity.
- Migrating historical alerts automatically on deploy. Re-extraction is an explicit remap action.

---

## 4. Current state

| Area | Current behaviour | Gap |
|---|---|---|
| `AlertObservable` | Existing join table with `UNIQUE(alert, observable)` and `added_by` | Not populated by ingestion |
| `IngestionSource.mapping_config` | Legacy `{target: path}` or v2 `{version, fields, value_maps}` | No `observables` schema |
| `ingest.mapping.apply_mapping` | Fills alert scalar fields only; pure | No observable draft |
| `ingest.pipeline.store_ingested_alert` | Creates/dedupes the alert; warnings in JSONB | Never touches `AlertObservable` |
| `ingest.remap.remap_alerts` | Rewrites scalar fields on unlinked open alerts | Does not re-extract observables |
| `alerts.escalation` | Creates suggestions and a timeline event | Does not copy existing alert observables |
| Alert detail UI | Observables card lives in the General tab, right column | Not a tab; no empty state |
| Mapping editor | Table owns `fields`, JSON owns everything else; preserves unknown keys | No observable rules UI |

The last point matters: `ingest.editor._preserved()` already keeps every top-level key the table does
not manage, so adding `observables` will not be silently dropped by a normal “Save mapping” click.

---

## 5. Configuration schema

Add one optional top-level key to the v2 mapping config:

```json
{
  "version": 2,
  "fields": {
    "title": {"paths": ["$.name"], "transform": "str"}
  },
  "observables": [
    {
      "type": "mail",
      "paths": ["$.actor.email", "$.recipients[*].address"]
    },
    {
      "type": "ip",
      "paths": ["$.source.ip", "$.destination.ip"]
    },
    {
      "type": "hash",
      "paths": ["$.sha256", "$.md5"]
    }
  ]
}
```

Rules:

- `observables` is a list of rule objects, preserving order.
- Each rule has exactly the meaningful keys `type` (non-empty string, at most 100 chars) and `paths`
  (non-empty list of JSONPath strings, at most 8 paths, each at most 200 chars).
- Paths must start with `$` and compile with `jsonpath-ng`.
- The same type may appear in two rules; their extracted values are unioned.
- Unknown top-level keys are preserved as today.
- A legacy config has no `observables`; saving through the editor may convert it to v2 as today.
- Empty list or missing key means “no mapped alert observables”.
- Unknown observable type is **allowed by pure validation** because validation has no DB access. The
  ingest path skips it and records a warning. The UI selects from the actual `ObservableType`
  vocabulary, so an editor save cannot create one.

Recommended cap constants:

```text
MAX_OBSERVABLE_RULES       = 32
MAX_OBSERVABLE_PATHS       = 8 per rule
MAX_OBSERVABLE_CANDIDATES  = 200 per alert
MAX_OBSERVABLE_VALUE_CHARS = 2048 per value
```

These caps protect ingest from a hostile/large payload and from a rule that fans out across a large
JSON array. Truncation is recorded, not silent.

---

## 6. Extraction semantics

### 6.1 Pure parsing and extraction

Create `ingest/observable_rules.py`:

- `observable_rules(config) -> tuple[ObservableRule, ...]`: lenient reader, never raises.
- `validate_observables(config) -> list[str]`: strict schema errors for save time.
- `extract_observable_candidates(config, payload) -> ObservableExtraction`: returns
  `(type_name, raw_value, source_path)` candidates plus truncation metadata.

It imports a new `extract_all_by_jsonpath(path, payload)` helper from `ingest/mapping.py` so
jsonpath compilation stays in one place.

`extract_all_by_jsonpath` behaviour:

- compile once through the existing bounded cache;
- return every match value, not only `matches[0]`;
- flatten list/tuple values recursively;
- skip `None`, booleans and mapping/object values;
- stringify ints/floats;
- stop at the per-path/candidate cap.

`ingest.mapping.validate_config` calls `validate_observables` only for v2-shaped configs and reports
messages under an `observables…` prefix that the editor can assign to its own card.

### 6.2 Persistence service

Create `observables/alert_sync.py`:

- `sync_alert_observables(alert, *, config=None, actor=None) -> AlertObservableSyncResult`
  - default config: `alert.ingestion_source.mapping_config`; no source means no-op;
  - load `ObservableType` vocabulary once;
  - for each candidate, call `resolve_observable(type_name, raw, owner_org=alert.owner_org)`;
  - `AlertObservable.objects.get_or_create(alert, observable, defaults={"added_by": actor})`;
  - return `created`, `linked`, `skipped_unknown_type`, `truncated` counts and human warnings;
  - append-only: never delete an existing alert link.

- `copy_alert_observables_to_case(alert, case, *, actor=None) -> CaseObservableCopyResult`
  - iterate `AlertObservable.objects.filter(alert=alert).select_related("observable__data_type")`;
  - `CaseObservable.objects.get_or_create(case, observable, defaults={"added_by": ...})`;
  - dispatch `dispatch_observable_linked(observable, case.pk)` only when the case link was newly
    created (the dispatcher is already `transaction.on_commit` and idempotent);
  - return copied/already-present counts.

- `reconcile_suggestions_with_copied_observables(alert, case, *, actor=None) -> int`
  - find `ObservableSuggestion` rows for `(case, source_alert=alert)` whose normalized value matches
    a newly copied case observable;
  - mark them `accepted`, set `observable`, `reviewed_by`, `reviewed_at`;
  - do **not** dispatch here — the copy already did;
  - return the number reconciled.

All three helpers are transactional. `sync_alert_observables` should be safe to call twice; the unique
constraint is the idempotency boundary.

### 6.3 Ingest

In `store_ingested_alert`:

- keep the existing alert `get_or_create` and replay semantics;
- inside the same transaction, call `sync_alert_observables(alert, actor=None)` after the alert row is
  available (use the `ingestion_source` config, never a caller-supplied config);
- on a newly created alert, append any extraction warnings to
  `alert.ingestion_warnings["warnings"]` and save only that field;
- on a replay, extraction remains idempotent but warnings are not appended to the existing alert. The
  recommended default is to sync on replay too, so a payload re-sent after a mapping fix can repair
  missing alert observables.

If the alert has no `ingestion_source`, extraction is a no-op. Direct API-created alerts are unchanged.

### 6.4 Remap

Extend `remap_alerts` so each candidate alert gets `sync_alert_observables(alert, config=config)` in
the same transaction as the field writes. This makes “Apply to unlinked alerts…” repair both the
alert’s typed fields and its mapped observables. Add `observables_added` to `RemapResult` with a
default of `0` so existing constructors/tests remain valid; report it in the flash message when
non-zero.

`preview_alerts` does not need to count observable additions in the first wave. If the UI wants that,
it can be a follow-up; the write remains bounded by `MAX_REMAP_ALERTS` and the extraction caps.

Replay and remap never rewrite `raw_payload` or delete existing links.

---

## 7. Data model

No migration expected.

- `AlertObservable` already provides the alert link, unique per `(alert, observable)`.
- `Observable` already provides per-org global dedupe on `(owner_org, data_type, normalized_data)`.
- `CaseObservable` already provides the case link, unique per `(case, observable)`.
- `ObservableSuggestion` already supports `case`, `source_alert`, `observable`, `status`,
  `reviewed_by`, `reviewed_at`.

Provenance beyond the source path is not stored in this wave. Adding a `source_path` JSONB column to
`AlertObservable` would be a migration and is deliberately out of scope unless §14 Q7 requires it.

---

## 8. API

### 8.1 Existing surface

- `POST /api/v1/alert/{alertId}/observable` continues to attach a manual alert observable and return
  the full list. No behaviour change.
- `POST /api/v1/alert` and the webhook continue to return the alert. Alert JSON is unchanged.

### 8.2 Optional parity (decision Q6)

If desired, extend the same route to `GET /api/v1/alert/{alertId}/observable` so API clients can read
what the alert page shows. Recommended: defer unless a client actually needs it; the UI uses the ORM
and the existing POST returns the list as its response body.

### 8.3 Case response

When an alert is merged/imported, the existing case response already includes case observables via
`case_json`. Copying alert observables therefore makes them visible through the case API with no wire
change.

---

## 9. UI

### 9.1 Alert detail tabs

- Add `{"slug": "observables", "label": "Observables"}` to `ui/views/alerts.py::alert_detail.tabs`.
- Validate the `?tab=` value against all three tabs.
- Always render the tab (discoverability), with an honest empty state inside.
- Add an icon using an existing Font Awesome subset glyph, e.g. `fa-cube` or `fa-diagram-project`.
- Move the existing observables table out of the General tab and into
  `panel-observables`; keep the current columns: Type, Value, Appears in cases.
- Keep the count visible in the panel heading; optionally put the count in the tab label.
- Do not render an empty table. Show “No observables have been mapped for this alert.” and, when the
  alert has a feed, a hint pointing to the source mapping.

The General right column keeps triage, merge/escalate and tag controls. No content is lost.

### 9.2 Source mapping editor

Add an **Observable extraction** card to `ui/templates/ui/source_detail.html`, separate from the field
mapping table so one form writes one concern.

Recommended UI:

- one row per `ObservableType` in the vocabulary;
- a textarea named `observable_paths__<type_name>`, one JSONPath per line;
- help text: “Every matching value is added to the alert; leave empty for no rule”;
- “Save observable rules” submit button with `form=observables`;
- per-row errors echoed back from a rejected save;
- the existing advanced JSON card remains the complete-config view and can edit the same list.

Pure editor helpers go in `ingest/editor.py` or a small `ingest/observable_editor.py`:

- `observable_editor_rows(config, types, submitted=None, errors=None)`;
- `build_observable_config(existing, rows, types)` preserving `version`, `fields`, `value_maps`,
  correlation keys and all unknown top-level keys;
- `is_noop_save` reuse for byte-stable saves.

`ui/views/ingestion_sources.py`:

- extend `_source_mapping_post` to route `form=observables` through a new save function;
- keep `form=table|json` behaviour unchanged;
- extend `_source_detail_context` with observable rows/types/errors;
- audit through the existing `record_ingestion_source_updated` with changed keys like
  `observables[0].paths` (the existing `changed_keys` helper is list-aware only as a whole value;
  either report `observables` or add a small list-aware diff);
- reject/echo on validation failure, no write on no-op.

### 9.3 Accessibility and style

- Use a real tab link, matching the existing `tabs.js` contract.
- Severity colours are irrelevant here; type labels stay text, not colour-only.
- Empty state is text, not a hidden panel.
- The mapping table and observable card use the existing `.card`, `.data-table`, `.hint`, `.field-error`
  patterns.
- No native `confirm()` / `alert()`; all destructive or applying actions keep the existing modal
  pattern.

---

## 10. Escalation copy

Both escalation paths are in scope by default:

### 10.1 `import_alert_to_case` (Escalate → new case)

After the case exists and the alert is linked:

1. `copy_alert_observables_to_case(alert, case, actor=actor)`.
2. `create_suggestions_for_case(...)` as today.
3. `reconcile_suggestions_with_copied_observables(alert, case, actor=actor)`.
4. Append the existing `alert-imported` timeline event; include
   `metadata["observables_copied"]` and `metadata["observables_already_present"]`.

If no alert observables exist, the timeline metadata records zero and behaviour is otherwise unchanged.

### 10.2 `merge_alert_into_case` (Merge → existing case)

After the alert link is established (or confirmed already present):

1. copy alert observables into the case;
2. create suggestions as today;
3. reconcile matching suggestions;
4. append `alert-merged` with the same observable counts.

The copy is a retry-safe convergence step: a second merge on the same alert/case creates no duplicate
`CaseObservable` rows and dispatches no second playbook run because the dispatcher’s
`observable.created` key is scoped to `(observable, case)`.

### 10.3 Dispatch semantics

Copying is not a silent data move. A newly created `CaseObservable` is exactly the event a manual
case observable add emits, so it dispatches `observable.created` on commit. Existing links do not
dispatch. This preserves Module D’s rule: the case drives automation, and re-linking the same artifact
to the same case is not a new event.

---

## 11. Security and safety

- **Authorization unchanged:** source mapping is admin/superAdmin-only; merge/import keep existing
  `resolve_alert(..., write=True)` and `resolve_case(..., write=True)` checks; viewers cannot POST.
- **Tenant isolation:** observables are resolved with `owner_org=alert.owner_org`; cases are already
  scoped; no cross-org link is possible.
- **No code execution:** JSONPath is evaluated by `jsonpath-ng`; no `eval`, no user Python.
- **Bounded work:** cap rules, paths, candidates and value length. A hostile payload cannot make one
  webhook create unbounded rows or enqueue unbounded playbooks.
- **No silent loss:** skipped unknown types and truncation are recorded in the alert’s ingestion
  warnings (on create); existing links are never deleted by a failed extraction.
- **Transactionality:** alert creation, alert-observable links, case creation/copy and timeline entries
  share the existing transaction boundaries. Automation dispatch remains `on_commit`.
- **No secret handling change:** no new credentials, endpoints or webhook behavior.

---

## 12. Testing strategy

Run only focused suites during development, then `make check`. Do not run the full suite unless asked.
Use `.venv/bin/pytest`.

### 12.1 Pure mapping tests (`tests/conformance/test_mapping_observables.py`)

- valid config parses and extracts all matches from a path;
- multiple paths union values;
- duplicate rule/type values dedupe at the service boundary;
- lists and arrays flatten; objects/null/bools skip; ints stringify;
- unknown type is a warning at persistence, not a crash;
- missing path yields no candidate and no crash;
- malformed config is ignored leniently by extraction;
- validation rejects non-list `observables`, missing `type`, empty `paths`, bad JSONPath, oversize
  rules/paths;
- validation accepts an empty/missing section.

### 12.2 Ingest tests (`tests/conformance/test_ingest_alert_observables.py`)

- webhook with a mapped source creates `AlertObservable` rows with the alert’s org;
- no mapping/no source creates none;
- replay is idempotent and does not duplicate links;
- the same value in two feeds/alerts reuses one global `Observable` per org;
- unknown type records a warning and still creates the alert;
- caps truncate and record a warning;
- unconfigured source still ingests (AC4.3 regression guard);
- raw payload is unchanged.

### 12.3 Remap tests

- applying a mapping to unlinked open alerts adds observables;
- linked/closed alerts remain untouched;
- rerun is idempotent;
- `observables_added` is reported;
- existing `RemapResult` assumptions still hold.

### 12.4 UI tests (`tests/conformance/test_alert_observables_tab.py`)

- `?tab=observables` shows the table and does not leak it into General;
- default `?tab=general` does not render the observables table;
- empty state is present when there are none;
- the tab is keyboard-reachable and follows the existing tabs contract;
- a viewer sees the tab but no mutation controls;
- tenant-visible case links only (reuse existing provenance test cases).

Update `tests/conformance/test_alert_observable_provenance_ui.py` to request `?tab=observables` in the
two alert-detail assertions.

### 12.5 Copy tests (`tests/conformance/test_alert_observable_case_copy.py`)

- merge copies alert observables to `CaseObservable`;
- import copies them too;
- copy is idempotent (same alert/case twice = one link);
- `observable.created` dispatch happens once per new case link, not on re-copy;
- copied links carry `added_by` (actor or the originating alert link’s adder);
- matching suggestions become accepted without duplicate dispatch;
- an alert with no observables keeps existing escalation behaviour;
- cross-org copy is impossible (scoped resolvers).

### 12.6 Source editor tests

Extend `tests/conformance/test_settings_source_mapping.py`:

- observable card renders one row per vocabulary type;
- saving paths stores the list shape verbatim;
- empty rows remove that type’s rule;
- table save preserves `observables` when using the field-mapping form;
- observable save preserves `fields`, `value_maps` and unknown keys;
- no-op save writes nothing / no audit;
- invalid JSONPath or unknown type rule (via JSON) is rejected with an echo;
- analyst/other-org cannot write.

---

## 13. Phases and acceptance criteria

### P1 — Pure schema + extraction

Files: `ingest/mapping.py`, new `ingest/observable_rules.py`, tests.

- [x] AC1: `validate_config` accepts a valid `observables` list and rejects malformed entries with
      readable path-specific errors.
- [x] AC2: extraction returns every scalar match for every configured path, in deterministic order.
- [x] AC3: extraction caps are enforced and reported as truncation metadata.
- [x] AC4: the pure module has no ORM/HTTP dependency, matching `apply_mapping`’s contract.

### P2 — Ingest persistence

Files: `observables/alert_sync.py`, `ingest/pipeline.py`, tests.

- [x] AC5: a mapped webhook creates `AlertObservable` links in the alert’s organisation.
- [x] AC6: replay does not duplicate links and remains idempotent.
- [x] AC7: unknown types are skipped with an ingestion warning; the alert is still created.
- [x] AC8: no source/no config/no rules means no observable links and no behaviour regression.
- [x] AC9: caps prevent an unbounded fan-out.

### P3 — Remap

Files: `ingest/remap.py`, `ui/views/ingestion_sources.py`, tests.

- [x] AC10: “Apply to unlinked alerts” also adds mapped alert observables.
- [x] AC11: linked and closed alerts are still never touched.
- [x] AC12: the result reports how many observables were added without breaking existing result fields.

### P4 — Alert detail tab

Files: `ui/views/alerts.py`, `ui/templates/ui/alert_detail.html`, `tests/conformance/test_ui_*`.

- [x] AC13: the alert page exposes General, Observables and Raw data tabs; `?tab=observables` is valid.
- [x] AC14: observables are listed only in the Observables tab; General keeps triage/meta/actions.
- [x] AC15: an empty state is shown when there are no observables.
- [x] AC16: keyboard/ARIA behavior matches the existing tab implementation; viewers stay read-only.

### P5 — Case copy

Files: `alerts/escalation.py`, `observables/alert_sync.py`, tests.

- [x] AC17: merge copies alert observables into `CaseObservable` links.
- [x] AC18: import/escalate copies them too.
- [x] AC19: copy is idempotent and dispatches `observable.created` once per new link.
- [x] AC20: matching suggestions are reconciled as accepted without duplicate dispatch.
- [x] AC21: timeline metadata records copied/already-present counts.

### P6 — Source editor

Files: `ingest/editor.py` (or `ingest/observable_editor.py`), `ui/views/ingestion_sources.py`,
`ui/templates/ui/source_detail.html`, tests.

- [x] AC22: the source page shows an editable observable-rules card populated from the vocabulary.
- [x] AC23: saving reusable observable paths stores the validated list and preserves unrelated config.
- [x] AC24: no-op saves write nothing; invalid saves re-render with the operator’s input and errors.
- [x] AC25: admin/superAdmin write gating and audit keys are preserved.

### P7 — Verification

- [x] AC26: focused tests and `make check` pass.
- [x] AC27: verifier confirms AC1–AC25 against the plan; deviations recorded in this doc and
      `docs/spec/deviations.md`.
- [x] AC28: security review confirms no new injection, tenant or authorization regression.

---

## 14. Open questions (decisions needed before TODO/implementation)

Recommended defaults are in **bold**.

1. **Alert extraction mode** — **link immediately as `AlertObservable`**, or stage as suggestions?
   Direct linking matches “extract observables for an alert directly” and the existing alert-observable
   API. It does not create a `CaseObservable` or trigger automation until escalation.
2. **Copy scope** — **both merge and import/escalate**, or only merge as literally requested?
   Import is the same promotion path; omitting it would make the two case-creation buttons disagree.
3. **Source editor scope** — **add an observable-rules card** plus the existing advanced JSON, or ship
   JSON-only first? The card is the usable path for non-JSON operators.
4. **Replay/remap extraction** — **re-extract on remap and on replay**, or only on first ingest?
   Re-extraction is idempotent and repairs alerts ingested before the mapping was configured.
5. **Suggestion reconciliation** — **mark matching suggestions accepted when copying**, or leave them
   in the review queue? Reconciling avoids showing a proposal for an artifact already on the case.
6. **API read parity** — **defer** a `GET /alert/{id}/observable` endpoint unless a client needs it.
7. **Provenance storage** — **defer** a per-link `source_path` column; the first wave needs no
   migration. If required, it becomes a separate migration-backed phase.
8. **Automation on copy** — **dispatch `observable.created` for newly copied case links** (consistent
   with manual adds and accepting suggestions), or copy silently? Dispatching can enqueue playbooks for
   every mapped observable on a large alert; the candidate cap is the safety valve.

### Decisions (2026-11-06)

All eight questions are resolved; the bold defaults above are accepted. Confirmed refinements:

1. Link mapped observables **directly** as `AlertObservable` rows; do not stage mapped values as
   suggestions.
2. Copy alert observables on **both** merge and import/escalate.
3. Ship the **observable-rules editor card** alongside the advanced JSON view.
4. Re-extract on **both replay and remap**.
5. Mark matching **proposed** suggestions `accepted` when copying; do not override an analyst's
   `rejected`/`ignored` decision.
6. Defer a `GET /api/v1/alert/{alertId}/observable` endpoint. TODO §15.7 records it for a later client
   request.
7. Defer per-link `source_path` provenance. The implementation will not add a migration. TODO §15.8
   records that the trade-off needs a dedicated explanation/decision before a future wave.
8. Dispatch `observable.created` for each newly created `CaseObservable` link, through the existing
   `dispatch_observable_linked` (on commit), never on an existing link.

---

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A noisy rule creates hundreds of observables per alert | Per-alert candidate cap + warning + dedupe |
| Copying triggers too many playbooks | `on_commit` + idempotency per `(observable, case)` + cap |
| Alert UI duplicate queues (accepted case links + proposed suggestions) | Reconcile matching suggestions during copy |
| Remap becomes slower | `sync_alert_observables` uses one vocabulary query and one link query per alert, bounded by cap |
| Unknown type rules saved via advanced JSON silently do nothing | Ingest warning recorded and visible on the alert |
| Field-mapping table save deletes observable config | `_preserved()` already protects unknown top-level keys; add regression test |
| Two forms fight over `mapping_config` | Each form writes only its ownership; JSON remains the whole-config view |
| Alert page tab change breaks existing provenance tests | Update the two tests to `?tab=observables`; keep the same rows/links |
| Large payload/extraction DoS | Existing webhook size/depth guards + extraction caps |

---

## 16. TODO entry to add after decisions

A proper `TODO.md` entry will be added only after §14 is answered. Proposed shape:

- heading with date/plan link and status `[~]`;
- P1–P7 tasks, each with file paths and AC references;
- explicit “Do not commit unless requested” note;
- source line: this plan + the P6 candidate in `PLAN-2026-10-16-source-field-mapping.md`;
- acceptance criteria summarized as the 28 ACs above.

---

## 17. Definition of done

- A mapped payload produces alert observables end to end, with no schema migration.
- The alert page has a working Observables tab and an honest empty state.
- Merge and import copy alert observables to the case, idempotently, with one dispatch per new link.
- Source operators can configure observable rules from the UI, with validation and no config loss.
- Focused tests and `make check` pass.
- Verification and security review are recorded; deviations are documented.
