# PLAN 2026-10-16 — Per-source field mapping (JSON → Alert)

**Status: APPROVED 2026-10-16 — P1, P2, P3, P7 and P7.1 IMPLEMENTED 2026-10-16, P5 IMPLEMENTED
2026-10-17** (records in §8 P1, §8 P2, §8 P3, §8 P5, §8 P7, §8 P7.1); P4 (sample preview) is the next
phase, and P3's
`ingest/editor.py` is what P4's dialog reuses. P7 is the §11 Q7 retro-mapping item, requested directly and
landed out of order (its ACs are 17–23; P4's 15/16 stay reserved, and P7.1's report-of-the-report ACs are
24–26). All eight questions answered (§11 Decisions); the Q5 consequence is recorded in §6.1 and
implemented as **ADR-009**. Written by the planner agent; the `django-backend` / `django-frontend` /
`verifier` subagents were unavailable (504/400), so the planner implemented P1–P3 and P7 directly.

Related: `AGENTS.md` §2 Module A · `docs/decisions/ADR-008-webhook-routing.md` (the token resolves the
source) · `docs/planning/PLAN-2026-10-15-token-only-webhook-routing.md` (§6 deviation: "`mapping_config`
still has no editor") · `docs/planning/PLAN-2026-10-03-foundations-mvp.md:61` (the original intent).

---

## 1. Summary

Amalthea accepts arbitrary JSON at one endpoint and then **keeps almost none of it**. The mapping
language is an undocumented `{field: "$.path"}` dict with no transforms, no editor, and two ways it
loses the alert's meaning:

- a vendor field whose value is a *severity name* (`"Critical"`) cannot become the internal 1–4 int, so
  the alert takes the default;
- title/description/summary/date/tags are either unmapped or have no rule vocabulary at all.

Measured on the dev system (2026-10-16) — `crowdstrike-edr` has `mapping_config = {}`:

```
crowdstrike-edr  alerts=10  titles: "Alert" ×10  severities: 2 (Medium) ×10
warnings on every alert: "severity_defaulted: no severity mapping; defaulted to 2"

raw keys available (per alert): name, display_name, description, severity(=90),
severity_name(="Critical"), timestamp, created_timestamp, id, type(="ldt"),
mitre_attack[…], tactic, technique, confidence, host_names, user_name, sha256, device{…}
```

The payload knows this is a *10/10 Critical* credential-theft detection on `BRAPPF58RPPW$`. The alert
says `Medium`, title `Alert`. This plan makes that mapping a first-class, per-source, UI-manageable
object.

## 2. Goals

1. **A defined config language** for a source: which JSON paths fill `title`, `severity`, `date`,
   `description`, `summary`, `type`, `source_ref`, `tlp`, `pap`, `tags`, `correlation_key` — with
   `severity` derived **by name** and by score.
2. **Pure, testable engine** (`ingest/mapping.py`), no DB, no HTTP: `apply_mapping(config, payload) →
   AlertDraft(+ warnings)`. Owned by the ingest app, called by the webhook view.
3. **The mapping is editable in the UI** per source, validated on save (unknown target field, unknown
   transform, non-compiling JSONPath) — with the current rules visible as a table.
4. **Nothing silently defaults.** Where a value could not be derived, the alert keeps today's
   tolerance (ADR-002 §D11) but records *which field, which paths, and why* in `ingestion_warnings`.
5. Keep working: a legacy `{"title": "$.title"}` config, an empty config, and a source with no rule for
   a field (AC4.3 "an unconfigured source ingests without error, never a 500").

## 3. Non-goals

- **Observable extraction** (`sha256`, `user_name`, `host_names` → `Observable`). The same config
  language should later carry it (§8 P6), but Module C is not in this plan.
- A *second* tenancy model. Q5 makes the source itself org-owned (§6.1); a shared-forge scope above
  that is not in this wave.
- Natural-language/auto-mapping ("sniff the vendor from the payload"). The preview (P4) renders what
  the rules do; it does not suggest rules.
- Changing what the API can *read* (`case_json`, `alert_json`) — wire shape untouched.
- Re-writing `raw_payload`. The original is always preserved (AGENTS.md Module A).

## 4. Current state (evidence, so the diff is honest)

| Where | What it does today | Gap |
|---|---|---|
| `ingest/mapping.py:20` | `extract_by_jsonpath(path, payload)` — first match, `None` on a bad path | No schema, no transforms, no validation of the *config* |
| `ingest/mapping.py:40` | `extract_correlation_key(config, payload)` | Tries 7 spellings of one key (`correlation_key`, `correlationKey`, …) — a symptom of having no schema |
| `ingest/views.py:182` | `_mapped(payload, mapping, *keys)` — JSONPath, then `payload[key]` | Lives in the **HTTP** layer; per-field key lists are hardcoded (`title`, `summary`, `message`…) |
| `ingest/views.py:205` | `_coerce_severity` — `int(value)` in 1..4 else `None` | `"Critical"` → `None` → defaulted. This is the user's exact case |
| `ingest/pipeline.py:33` | `DEFAULT_SEVERITY = 2`, hardcoded | **`IngestionSource.default_severity` is never read** — a per-source default exists and is ignored |
| `ui/templates/ui/sources_list.html:41` | prints `key=value` pairs from `mapping_config` | Read-only, truncated, no editor anywhere (`ui/urls.py:174,188` expose only the *list*) |
| `ingest/models.py:18` | `mapping_config = JSONField(default=dict)` | Shape is documented in prose only ("the JSON-path mapping table") |

Existing tests pin the legacy flat shape as fixtures
(`test_webhook_hardening.py:39`, `test_mvp_loop.py:75`, `test_ui_loop.py:60`,
`test_correlation_key_derivation.py:26`) — that is why §5 keeps it working.

## 5. Design

### 5.1 Config language (v2)

A JSON object on the source. `fields` maps a **target** the engine knows to a **rule**; anything not
listed falls back to §5.4.

```json
{
  "version": 2,
  "fields": {
    "title":       {"paths": ["$.name", "$.description"], "transform": "str", "max_length": 200},
    "severity":    {"paths": ["$.severity_name"], "transform": "severity_name", "default": 2},
    "date":        {"paths": ["$.timestamp"], "transform": "datetime", "timezone": "UTC"},
    "description": {"paths": ["$.description"], "transform": "str"},
    "summary":     {"paths": ["$.objective", "$.tactic"], "transform": "str"},
    "type":        {"paths": ["$.type"], "transform": "str", "default": "ods"},
    "source_ref":  {"paths": ["$.id"], "transform": "str"}
  },
  "value_maps": {"severity": {"Benign": 1, "Suspicious": 2, "Malicious": 4}},
  "on_missing": "warn"
}
```

Rules:

- **`paths`** — ordered candidates; first non-`None` **and non-empty** match wins. Supports the
  arbitrary JSONPath AGENTS.md promises (`$.a.b`, `$['a b']`, `$.x[*].y`, `$..y`) via jsonpath-ng.
  Existing shared string spellings (`"title": "$.x"`) are normalized to `{"paths": ["$.x"]}` (§5.5).
- **`transform`** — one name from a **closed set** (§5.2). Unknown transform = form error at save
  time, never an ingest-time surprise.
- **`default`** — per-rule. Precedence: match → `default` → field-specific source default
  (`default_severity` for `severity`) → model default + warning. Tolerated alias, not a feature:
  `severity_default` is read as `default` by `_normalize` (`raw.get("severity_default",
  raw.get("default"))`), but the editor writes `default` only and no other producer exists.
- **`max_length`** — applied to `str`; a mapping must not be able to write an unbounded title
  (`Alert.title` is 500, today the view truncates at 200).
- **`value_maps`** — operator-supplied name→int entries, merged *over* the built-in vocabulary.
  Edited in the **advanced JSON view** (Q3), not as a form row.
- **`on_missing`** — `warn` (the only shipped behaviour, Q4). The key is reserved so a later
  `"reject"` does not have to be invented; nothing reads it as `reject` today.
- **`timezone` is not implemented** — it appears in the `date` example above, but the engine never reads
  the key; `_as_datetime` treats a naive, timezone-less timestamp as UTC (`ingest/mapping.py`).
- **`correlation_key` is deliberately not a v2 field** (Q8). `extract_correlation_key` keeps its
  current spelling list (`correlation_key`, `correlationKey`, `…`) so the existing behaviour is
  untouched; folding it into `fields` is a follow-up, not a silent extension.

### 5.2 Transforms (closed set)

| name | input | output | why |
|---|---|---|---|
| `str` | any | string, whitespace-collapsed, truncated to `max_length` | the default for text fields |
| `int` | int/`"3"` | `int` | `type`/`pattern_id` |
| `severity_name` | `"Critical"`, `"High"`, `"Informational"` | 1–4 | **the user's ask**; case-insensitive |
| `severity_score` | `90`, `"90"` | 1–4 by bands | CrowdStrike emits BOTH a 0–100 score and a name; explicit opt-in keeps it honest |
| `severity_int` | `1`–`4` | 1–4 | passthrough with range check |
| `tlp` / `pap` | `"Amber"`/`2` | 0–4 / 0–3 | the same name problem for TLP/PAP |
| `datetime` | RFC3339/ISO8601, epoch s/ms | aware `datetime` | `§$.crawled_timestamp` is `2026-…Z`; never crash on a weird value |
| `list` | scalar, array, array-of-objects path | `list[str]` | `$.mitre_attack[*].technique_id` → `["T1003"]` |
| `list_join` | list | string | tags where the model takes a scalar |
| `strip` / `lower` | string | string | vendor whitespace/casing |

**Implemented set — drift note, recorded 2026-10-17.** The table above is design intent, not the
contract; `ingest/mapping.py::TRANSFORMS` is the source of truth. The shipped closed set is exactly its
11 keys: `str`, `str_lower`, `int`, `severity_name`, `severity_score`, `severity_int`, `datetime`,
`list`, `json`, `tlp`, `pap`. `strip`, `lower` and `list_join` from the table above were **never
implemented**: `str_lower` is the shipped `lower`/`strip` job, and the shipped set additionally contains
`json` (absent from the table above); there is **no** `list_join` — use `list` where the model takes a
list.

Every transform is **total**: bad input returns `None` (+ a warning), never raises — the same posture
`extract_by_jsonpath` already documents ("a typo in operator-authored data should produce a warning,
not a 500").

### 5.3 Severity vocabulary (`severity_name`)

Built-in, case-insensitive, trimmed; merged with the source's `value_maps.severity`:

| → 1 Low | → 2 Medium | → 3 High | → 4 Critical |
|---|---|---|---|
| unknown, informational, info, negligible, low, minor, benign | medium, moderate, suspicious | high, severe, major, malicious | critical, urgent, emergency, catastrophic |

Unknown string ⇒ `default` + a warning naming the value (`"Informational"` already maps; a vendor
typo does not). `severity_score` bands are configurable per rule with these defaults:
`<20 → 1`, `20–49 → 2`, `50–79 → 3`, `≥80 → 4` (CrowdStrike's 60/70/90 land at High/High/Critical).

### 5.3a Where the mapping runs

```
webhook view  →  authenticate (token)  →  size/depth/type guards  →  apply_mapping(source, payload)
                                                                    →  AlertDraft + warnings
              →  store_ingested_alert(..., draft)                   (idempotent replay key unchanged)
```

`apply_mapping` is pure: no ORM, no settings, no logging of the payload. The draft is a frozen
dataclass, so a test can assert the draft for a stored CrowdStrike alert's `raw_payload` **without
touching HTTP** — that is §7's main contract test.

## 6. Data model / migrations

| Change | Why |
|---|---|
| none to `Alert` | every target already exists on the model |
| `IngestionSource.mapping_config` stays a `JSONField` | shape is validated in Python (a `CheckConstraint` cannot validate JSONPath) |
| `IngestionSource.mapping_version`? | **No** — `version` lives inside the config; a separate column is a second source of truth |
| optional `IngestionSource.mapping_updated_at/_by` | audit trail for "who made crowdstrike-edr map severity_name"; or reuse `AuditLog` |
| **`IngestionSource.organisation` FK (Q5, §6.1)** | the source stops being fleet-global, so its mapping is org-specific and an `admin` may edit it |

`default_severity` finally gets used (§5.1) — that fixes the ignored-column bug above.

### 6.1 Q5 — the source becomes org-owned

Answer 5 reverses ADR-008 §4 ("`IngestionSource` survives as a reusable mapping profile", *global*).
Consequence: the mapping is org-specific **because the object that holds it is**.

```
before                              after
IngestionSource  global              IngestionSource.organisation  FK, required
  slug unique                          unique(organisation, slug)
  mapping_config shared by all orgs    mapping_config belongs to that org
IngestionToken(source, org)          IngestionToken(source, org) — same org, enforced
```

- **Migration** (`ingest/000x_source_org.py` + `identity` follow-up): add `organisation`, then set it
  per source from the org that holds its tokens. Dev/prod evidence: all three dev sources have exactly
  one token org (`Default`), so the rule is unambiguous; a source with **no** token falls back to the
  default org (`identity.Organisation` default is already created by migration `identity.0006`); a
  source with tokens in **two** orgs cannot be assigned — the migration raises rather than guesses, and
  an operator splits it by hand (documented, not silent).
- **Consistency**: `IngestionToken.organisation` must equal `source.organisation`. A cross-table
  `CheckConstraint` is impossible, so it is enforced in `IngestionToken.create_token`/`save` +
  a conformance test, and the migration's output is asserted.
- **Slot definition**: `Organisation.default`-style lookup already exists (§ `identity`); reuse it.
- **ADR-009** ("`IngestionSource` is org-owned") supersedes ADR-008 §4 on this point, with the
  trade-off stated: two tenants can no longer share one profile, and the same vendor mapping saved per
  org is duplication by design — that is what "admin edits only his org" requires.
- **Not** chosen (recorded): a per-org `SourceMappingOverride` table. It keeps a shared default, but it
  has to answer "which two configs apply, in what order" on every ingest, for a product with no second
  tenant using a shared profile today.

## 7. UI

**P2/P3 — a source page.** `Settings → Sources` gets the row action it lacks today:
`GET/POST /settings/sources/<uuid>/` (`ui-settings-source-detail`), gated by role **and** org (§6.1):
`admin` for own-org sources, `superAdmin` for any, everyone else never. It renders:

1. **Identity** — slug, name, org, `default_severity`, `correlation_enabled`, alert count, tokens.
2. **Mapping table** — one row per known target: *target* (label), *paths* (textarea, one per line or
   comma-separated), *transform* (`<select>` of §5.2), *default*, *required*. Server-rendered, no
   inline JS (ADR-003/CSP), progressive enhancement: a plain form POST saves.
3. **Validation on save** — per-field errors via Django `Form`: unknown target, unknown transform,
   path that does not compile with jsonpath-ng, `max_length` not an int, severity band inverted.
   **Nothing is saved if any rule is invalid** (the config is one object).
4. **Stored JSON** in a `<details>` (read-only, copyable) — *and* the only place `value_maps` is
   edited, per Q3. One storage object; the table and the JSON are two views of it, never two writers.
5. **Preview** (§8 P4) — "Test this payload" with a paste box and a **"use the newest alert"** button
   that fills it from the source's latest stored `raw_payload` (Q2).
6. **Empty state** — "no rules ⇒ literal keys only", with the preview as the way to write the first
   rule from a real payload.

`sources_list.html`'s Mapping column stops printing raw `key=value` pairs and instead shows
`7 rules · severity←severity_name` (count + the severity rule, which is what an analyst checks),
with the row linking to the page.

## 8. Phases

Each phase is independently shippable and has ACs. P1 is required by all; P2 (org gate) before P3
(the page it guards); P4's dialog lives on the P3 page.

### P1 — Engine + schema (`django-backend`) — **IMPLEMENTED 2026-10-16**
`ingest/mapping.py` rewritten around `apply_mapping(source_or_config, payload) -> AlertDraft`;
`ingest/views.py` calls it; the pipeline takes the draft; `default_severity` becomes live; warnings
gain structure.

**Implementation record.** The `django-backend` subagent returned 504 on every attempt, so the planner
implemented P1 directly. Files: `ingest/mapping.py` (`AlertDraft`, `FieldWarning`, `TARGETS`,
`LEGACY_KEYS`, the transform table, `_normalize`, `apply_mapping`, `validate_config`), `ingest/views.py`
(one `apply_mapping` call replaces `_mapped` + `_coerce_severity`; both helpers deleted),
`ingest/pipeline.py` (`description`/`summary` in the create `defaults`, a `default_severity` parameter,
`ingestion_warnings["mapping"]`). Evidence: `tests/conformance/test_mapping_engine.py` (184 cases:
AC1 via a cursor tripwire, AC2 the synonym table + score bands, AC3 the real CrowdStrike payload both
on the engine and through `/api/v1/alerts/webhook/`, AC4/AC5 legacy shapes + 132 malformed-config ×
non-object-payload combinations, AC6 `default_severity`, AC7 the structured warnings); the five
pre-existing flat-`mapping_config` fixture files pass unchanged; full suite 2181 passed / 3 skipped;
`ruff` clean. Two decisions beyond the plan, both in `docs/spec/deviations.md` (MAP-2, MAP-4): a
compiled-JSONPath cache (`jsonpath_ng.parse` measured at 1.8 ms, and the engine walks up to ~20 paths
per webhook ⇒ now ~1 µs), and per-target `not_found` warnings when a configured rule matched nothing
(the old reader was silent). `validate_config` landed here rather than in P3 (it is pure; P3 consumes
it unchanged).

AC:
1. `apply_mapping` is pure (no ORM/HTTP/settings import) and returns a frozen dataclass + warnings.
2. `severity_name` handles all four dev payload values (`Critical`→4, `High`→3) and every synonym of
   §5.3; a bogus name ⇒ rule default + warning, never an exception.
3. A v2 config maps the **real stored** `crowdstrike-edr` payload to title `HiveCredTheft`,
   severity 4, date from `$.timestamp`, type `ldt`, `source_ref` `ind:2f1c9a` — asserted on the engine
   and through `/api/v1/alerts/webhook/`. **Corrected during implementation:** the original AC also
   listed `tags ["T1000…"]`, but tags left v1 in §11 Q8, so there is no `tags` target.
4. Legacy flat config (`{"title": "$.title", "severity": "$.severity"}`) behaves exactly as today —
   all five existing fixture files still pass.
5. Empty config: no 500, literal-key fallback only (AC4.3 preserved).
6. Severity falls back to the **source's** `default_severity`, not a module constant.
7. Warnings name field + paths (`{"field": "severity", "paths": [...], "reason": "unknown_value",
   "value": "Critical-ish"}`) while `ingestion_warnings["warnings"]` keeps its existing string list
   for compatibility with `test_ui_case_create.py:425`.

### P2 — Source-page shell + org gate (`django-frontend` + `django-backend`)
**IMPLEMENTED 2026-10-16.** The route/§7 page, plus the §6.1 migration and the `IngestionToken.organisation ==
IngestionSource.organisation` invariant. This is the phase that touches tenancy, so it lands with
ADR-009 and the two conformance tests below.

**Record.** Files: `ingest/models.py` (`organisation` FK PROTECT, `unique(organisation, slug)`),
`ingest/migrations/0003_ingestion_source_organisation.py` (**new**; add-null → backfill → not-null →
constraint → drop-slug-unique; applied to the dev DB — all three feeds resolved to `Default`, 0 mismatched
tokens), `identity/models.py` (`_source_org_id`, the `save()`/`create_token()` invariant),
`identity/scoping.py` (`resolve_ingestion_source`), `identity/audit.py` (`target_org` now the feed's org),
`identity/services.py` (`organisation_blockers["ingestionSources"]`), `ui/views.py`
(`settings_source_detail`, org-filtered `_settings_sources_context`/`sources_list`, org-scoped
`settings_token_create`, `_org_blockers_text` + "ingestion source"), `ui/urls.py`
(`ui-settings-source-detail`), `ui/templates/ui/source_detail.html` (**new**) and `sources_list.html`
(row link + rule summary). Tests: `tests/conformance/test_settings_source_mapping.py` (**new**, 16 cases —
AC7 driven through `MigrationExecutor` because pytest-django has already applied the migration; AC8/AC9/AC10
+ PROTECT/blocker), plus fixture updates across 15 conformance files (`organisation=` is now required),
`test_fk_audit.py` (+1 FK, 60 → 61), `test_schema_mutation_guards.py` (mutation 14 moved from
`slug unique=True` to `detach_constraint("ingestion_source_org_slug_uniq")`), `test_org_user_management.py`
(+`blockers["ingestionSources"]`). Docs: **ADR-009** (new; supersedes ADR-008 §4), ADR-008 header +
decision 4 amended, `data-model.md`, `api.md` blocker list, `identity-auth.md`.
Evidence: full suite green (see §8 P5 for the final run), `ruff` clean, `make css-check` clean.

AC:
7. Migration assigns every existing source the org of its tokens and asserts the post-state; a fixture
   with two token orgs makes the migration raise (not guess).
8. An `admin` of org A gets 404 (not 403-then-404 depending on order) for a source of org B; a
   `superAdmin` gets both; a `viewer`/analyst never reaches the page.
9. `IngestionToken` creation for a source of another org is refused with a clear error — one test per
   path (UI form, `create_token`, and the model), because a cross-table constraint cannot express it.
10. Duplicate slugs are now allowed *across* orgs and still refused *within* one.

### P3 — Mapping editor + validation (`django-frontend` + `django-backend`) — **IMPLEMENTED 2026-10-16**
The form table §7.2, the read-only JSON §7.4, save-time validation §7.3, audit.

**Record.** Files: `ingest/editor.py` (**new** — the pure half: `parse_paths` (newline-only, MAP-6),
`build_table_config`, `parse_json_config`, `editor_rows`, `submitted_rows`, `canonical_form`,
`is_noop_save`, `changed_keys`, `EditorResult`, `TARGET_LABELS`/`TRANSFORM_HELP`), `ingest/mapping.py`
(`config_shape` added so the page can say *which* shape it is showing; `validate_config`'s shape branch
aligned with `_normalize`'s — dev PM-adjacent fix, see the deviations wave record),
`identity/models.py` (`AuditLog.details`, **MAP-7**) +
`identity/migrations/0011_auditlog_details.py` (**new**), `identity/audit.py`
(`record_ingestion_source_updated`), `ui/views.py` (`settings_source_detail` is now
`@require_http_methods(["GET","POST"])` + `_source_detail_context` / `_source_mapping_rows` /
`_save_source_mapping`), `ui/templates/ui/source_detail.html` (rewritten: Identity card, the rules table,
a Tokens card, the advanced-JSON card, and the legacy-conversion warning).

**The four promises the editor keeps** (`ingest/editor.py`'s docstring is the contract):
(1) the table owns `version`/`fields` only — `value_maps`, `correlation_key`, `bands`, `max_length` and
unknown keys are preserved verbatim; (2) paths are newline-separated only (MAP-6); (3) **an empty row is
a deletion**, which is the only delete control the table has, and a preserved `bands` may not resurrect
it; (4) a conversion is announced, never silent (the legacy notice).

**Implementation record.** `ingest/editor.py` (392→~460 lines, pure: an import-level guard test asserts
it never imports Django, plus a cursor tripwire); `identity/migrations/0011_auditlog_details.py`
applied; `ui/views.py::settings_source_detail` is now GET+POST with `_source_detail_context` /
`_source_mapping_rows` / `_save_source_mapping` (PRG on success, **200 echoing the input** on a
rejection, `Transaction.atomic` + `update_fields=["mapping_config", "updated_at"]`, and
`record_ingestion_source_updated(...)` with `changed_keys`); `ui/templates/ui/source_detail.html`
rewritten (Identity card, the rule table with per-row errors and literal-key hints, the `POST`-with-
`{target}` rows, the transform help `<details>`, the Tokens card unchanged, and the advanced-JSON card
which renders **every** error message — a rule-level JSON failure must be visible, not silently
attributed to a card the operator is not looking at). No new CSS class (the page reuses `card`,
`data-table`, `field-error`, `hint`, `case-actions`, `msg msg-error`; `make css-check` clean).

**Deviations recorded:** **MAP-5** (no Django `Form` — hand-parsed POST, as everywhere else in the
repo), **MAP-6** (newline-only paths), **MAP-7** (new `AuditLog.details`), **MAP-8** (no `required`
column), all in `docs/spec/deviations.md`.

Evidence: `tests/conformance/test_mapping_editor.py` (**new**, 66 pure cases — round-trip/no-op,
preservation, MAP-6, deletions, defaults, the one-schema-authority rejections, the JSON box,
`changed_keys`, the preview rows, and the target/transform coverage invariants);
`tests/conformance/test_settings_source_mapping.py` **16 → 34 cases** (AC11 write + audit with
`details.changed == ["fields.severity.default", "fields.title.paths", "fields.title.transform"]` and
`"$.name" not in str(log.details)`; AC11's echo-on-error and the legacy warning; AC12 no-op on the
rendered page **and** on a `transform: "str"` config, plus "a real edit is not swallowed"; AC13 the JSON
box (verbatim store, `{}` clears, empty box refused, invalid JSON keeps the text, a rule-level failure
visible on the card); AC14 the analyst/foreign-admin 404 and the cross-org superAdmin write audited on
both organisations). Full suite **2288 passed / 3 skipped** (115 s), `ruff` clean, `make css-check`
clean, `makemigrations --check` clean.

AC:
11. `GET` renders the form for a permitted actor; a POST with a bad path/transform keeps the
    operator's input and shows a per-field error; **nothing** is persisted on error (assert the row).
12. Saving an unchanged form is idempotent (no audit row, no `updated_at` churn).
13. An accepted save writes v2 and audit (`ingest.source.updated`, target id, changed keys).
14. No inline JS/CSS (CSP), CSRF on the form, and the page works with JS off.

### P4 — Sample preview ("Test this payload", Q2)
A `<dialog>` on the source page: paste JSON **or "use the newest alert"** (the source's most recent
`Alert.raw_payload`, server-side) → POST → `apply_mapping` → renders the resulting draft field-by-field
with the warnings, and `stored: no`. No DB write, no logging of the sample.

AC:
15. A valid sample renders title/severity/date/type/source_ref + every warning; alert count, source
    config and `updated_at` are unchanged afterwards.
16. The sample is never written to `raw_payload`, the session, or a log line; a body over
    `WEBHOOK_MAX_BODY_SIZE` → 413; a viewer gets the redirect/flash, not the draft.

### P5 — Health & docs — **IMPLEMENTED 2026-10-17**
`docs/spec/api.md` mapping section, `docs/spec/data-model.md` shape, `sources_list` summary,
a `mapping_warnings` view on the source page, `TODO.md` gets retro-mapping (Q7) and the deferred
`canonical_payload`/`_verify_secret` dead-code observation. `ingest/admin.py` stays empty.

**Implemented 2026-10-17.** P5 shipped **no acceptance criteria of its own** — the AC numbers stay where
§8 P4/P7/P7.1 put them, and P5 reuses none of them (P4's 15/16 remain reserved), so the record below is a
deliverable list rather than a checklist. The health view named above lands as the **"Mapping health"
card**. `ingest/remap.py` gains the read-only health reader: `HEALTH_SCAN_LIMIT: Final[int] = 2000` (a
bound for a *page render* nobody asked for, deliberately smaller than `MAX_REMAP_ALERTS`, which bounds a
*write* the operator did ask for); the frozen `MappingHealth` (`total` — the re-map's own candidate
count, `scanned`, `affected` — alerts whose stored record has at least one unresolved field, `unresolved`
— `target -> reason -> alerts`, plus the derived `complete`/`clean`); `summarise_mapping_records(records)`
(a pure function over stored `Alert.ingestion_warnings` dicts; reads **only** the structured `"mapping"`
list, never the append-only `"warnings"` event log, MAP-2b; counts each alert once per `(field, reason)`;
and counts only reasons in `ingest.mapping.UNRESOLVED_REASONS`, so a benign `truncated` entry is not a
defect); and `mapping_health(source, *, limit=HEALTH_SCAN_LIMIT)` (read-only, bounded, and built on
`candidate_alerts(source)` — the same predicate the re-map uses, so the card cannot promise a repair the
button would refuse, MAP-9). `ui/views.py::_source_detail_context` passes `mapping_health` to the
template, and `ui/templates/ui/source_detail.html` gains a **"Mapping health" card between "Mapping
rules" and "Tokens"**: a clean state, or "N of M unlinked open alerts have a rule that produced no value"
plus a field/reason/count table that reuses the P7.1 `reason_label` filter while keeping the stable
reason token visible, a pointer to "Apply to unlinked alerts…", and an explicit note when the scan
stopped at the limit.

**What P5 did not do.** No P4 preview endpoint — the card reads the records *stored* on the alerts rather
than re-running the rules, so the confirmation dialog remains the only thing that previews a fresh pass
(a rule fixed but not yet re-applied still shows here, which is the point: it is the evidence that a pass
is worth running). `ingest/admin.py` deliberately stays empty (0 lines): the feed and its mapping are
managed through the Settings UI, not Django admin.

**The doc half of P5** (its other deliverable set, landed in parallel in the same change set): the
`docs/spec/api.md` mapping section, the `docs/spec/data-model.md` shape, the `sources_list` summary, and
`TODO.md` gaining the Q7 retro-mapping entry plus the deferred `canonical_payload`/`_verify_secret`
dead-code observation. §13's structured `mapping`/`warnings` split is already recorded as **MAP-2b** in
`docs/spec/deviations.md`, so P5 adds no deviation entry of its own.

**Correction (2026-10-17).** Of the "deferred `canonical_payload`/`_verify_secret` dead-code
observation" named above, only half holds: `ingest/views.py:105::_verify_secret` has no callers and is
dead (the live verifier is `identity/models.py:156`, called at `:309`), while `canonical_payload` is not
in `ingest/views.py` at all — it lives at `ingest/references.py:44` and is live (the synthetic
`source_ref` path via `payload_digest`, and `tests/conformance/test_ingest_pipeline.py:52`), so it must
not be deleted. The cleanup is recorded at `TODO.md` §6.18, and landed 2026-10-17 (with the
module-docstring bullet that described the receiver's long-retired legacy plaintext fallback).

Evidence: `tests/conformance/test_mapping_remap.py` gains `TestTheMappingHealth` (7 cases) and
`test_settings_source_mapping.py` gains `TestTheMappingHealthCard` (5 cases); collected counts after the
change are `test_mapping_remap.py` 32, `test_settings_source_mapping.py` 53, `test_mapping_engine.py`
184 and `test_mapping_editor.py` 66. `make check` green at **2352 passed / 3 skipped** (up from 2340;
ruff, ruff format, pinned-scope mypy, `manage.py check`, migrations in sync and the css compile check all
clean). The P5 code lands in one maintainer commit together with the doc updates (recorded without a
hash).

### P6 (candidate, not planned here) — observables from the same config
`"observables": [{"type": "sha256", "paths": ["$.sha256"]}, …]` feeding Module C. Explicitly out of §3
so it cannot creep into P1.

### P7 — Apply a mapping to existing alerts (Q7, user request 2026-10-16) — **IMPLEMENTED 2026-10-16**

§11 Q7 deferred retro-mapping to `TODO.md`; the user asked for it directly, so it lands out of order
(before P4/P5) as one more control on the source page — **"Apply to unlinked alerts…"** beside "Save
mapping". P4's AC15/AC16 stay reserved; this phase owns **AC17–AC23**.

**The candidate set is one predicate, not a filter each caller remembers to apply.** An alert is
re-mapped iff it belongs to *this feed* (`ingestion_source=source`) **and** is unlinked
(`case__isnull=True`) **and** is open (`status__stage__in=("New", "InProgress")` — the `New`/`Triaged`
status rows; `Dismissed` is `stage="Closed"` and `Imported` is `stage="Imported"`). Why those three:

- `ingestion_source`, not the wire `source` string: the FK is *our* record of which configured feed
  accepted the payload (the `Alert` docstring's "treat a mismatch as data"), and two feeds may both send
  `source: "crowdstrike"`. An alert that arrived over the TheHive-compatible API has no feed and is never
  a candidate.
- **unlinked**, per the request: a case file is human-authored evidence, and rewriting an alert *inside* a
  case changes what an investigator has already read. An escalated alert keeps the values it was escalated
  with — the alert row is preserved by design (`alerts/escalation.py`).
- **open**: `New`/`Triaged` are the stages where a wrong severity still misleads triage. Dismissed and
  imported alerts are terminal decisions, not mapping errors.

**Written: `title`, `description`, `summary`, `severity`, `date`** — the mapping's display/ordering
outputs, produced by the same `apply_mapping(config, payload, default_severity=source.default_severity)`
call the receiver makes, with the same `str(… or "Alert")[:200]` title clamp, so a re-mapped alert equals
what ingest would produce for that payload today. **Deliberately not written** (**MAP-9**): `type` and
`source_ref` — they are the dedupe key with `source`/`owner_org`, so rewriting them can collide with an
existing row and moves the replay key under a stored alert; `correlation_key` — not a v2 field at all
(§11 Q8); and every human/triage field (`status`, `case`, `tags`, `assignee`, `flag`, `follow`, `tlp`,
`pap`, observables, custom fields).

**Nothing is written unless it changed.** Each draft is compared field-by-field and only rows with a real
difference are saved (`update_fields=[…changed, "updated_at"]`); the result carries
`candidates`/`updated`/`unchanged`, so re-applying the same mapping twice is a no-op the second time — the
AC12 discipline, and the reason the flash names three numbers.

**Two steps, server-confirmed, nothing written on the first.** `intent=apply` validates the submitted
config and re-renders the page with the confirmation `<dialog>` (autoopen, per the `token-created`
precedent) carrying the exact counts and the field list; it writes **nothing**. `intent=apply_confirmed`
carries the validated config in a hidden `pending_config` field, re-validates it, then saves the mapping
(audit `ingestion_source.updated`, only when it changed) and re-maps **inside one transaction** (audit
`ingestion_source.mapping_applied`). A rejection at either step is the existing 200-that-echoes-the-input;
success is PRG. The dialog body is server-rendered (`ui/_source_remap_confirm_body.html`) and reuses
`_modal.html` — no new CSS class (§ modal blind spot).

**Cap:** `MAX_REMAP_ALERTS = 5000` per request, following the `MAX_BULK_PROCEDURES` idiom in `ui/views.py`.
Over the cap the dialog still opens but its confirm control is disabled and says why, and **nothing is
saved**: a "save & apply" that silently drops the apply half is worse than a refusal. A feed beyond that
needs a background task, which needs a status surface this wave does not have.

**Audit** — `ingestion_source.mapping_applied` with
`details={"changed": [...], "alerts": {"candidates": n, "updated": m, "unchanged": k}}`: counts, never
values (MAP-7). **Warnings** — the alert's structured `ingestion_warnings["mapping"]` is *refreshed* (it
describes the fields, so a stale one is a lie) while one `remapped:` line is *appended* to
`ingestion_warnings["warnings"]`, whose ingest-time lines (`severity_defaulted`, ref synthesis,
`status_created`) are history and are never rewritten. Without this, `alert_detail.html` would keep
showing "severity_defaulted … defaulted to 2" under a severity of 4.

AC:
17. Candidate set: exactly this feed's unlinked `New`/`Triaged` alerts are re-mapped; a linked alert, a
    dismissed alert, an imported alert, another feed's alert and another org's alert are all untouched.
18. Fields: `title`/`description`/`summary`/`severity`/`date` take the new mapping's values (equal to what
    ingest would produce, title clamped to 200); `type`, `source_ref`, `correlation_key`, `status`, `case`,
    tags, observables and custom fields are unchanged.
19. Idempotence: applying the same mapping again reports every candidate `unchanged` and writes nothing
    (`updated_at` and the audit log untouched).
20. Confirmation gate: `intent=apply` writes nothing at all (config, alerts, `updated_at`, audit) and
    renders the dialog with the exact counts and the exact field list; `intent=apply_confirmed` performs
    it; a config rejected at either step echoes the input and writes nothing.
21. Permissions: a viewer/analyst reaches neither the button nor the POST; a foreign-org admin gets the
    uniform 404; a superAdmin's cross-org apply is audited with both `actor_org` and `target_org`.
22. Audit + warnings: `ingestion_source.mapping_applied` records the three counts and the changed config
    keys; `ingestion_source.updated` appears only when the config actually changed; the structured
    `mapping` warnings are refreshed and one `remapped:` line is appended without losing the ingest-time
    lines.
23. Boundaries: over `MAX_REMAP_ALERTS` the dialog refuses and nothing is saved; with zero candidates the
    mapping is still saved and the flash says that nothing was re-mapped.

**Implemented 2026-10-16** — `ingest/remap.py` (candidate predicate, `remap_alerts`, the field set,
warnings refresh), `identity/audit.py::record_ingestion_source_mapping_applied`,
`ui/views.py::_source_mapping_post`/`_confirm_mapping_apply`/`_apply_mapping_to_alerts` (plus the
`_submitted_mapping`/`_reject_mapping`/`_persist_mapping`/`_mapping_saved_message` refactor shared with
`_save_source_mapping`), the extra button on both cards and
`ui/templates/ui/_source_remap_confirm_body.html`. Deviations **MAP-9**, **MAP-10** and (the cap)
**MAP-11** are recorded in `docs/spec/deviations.md`; the audit action and the `ingestion_warnings` split
are in `docs/spec/identity-auth.md` and `docs/spec/data-model.md`. Evidence:
`tests/conformance/test_mapping_remap.py` (14 cases), `test_settings_source_mapping.py`
(`TestApplyingTheMappingToExistingAlerts`, 10 cases) — full suite 2317 passed / 3 skipped,
`make check` green.


### P7.1 — Re-map feedback: "I added a rule and nothing changed" (user report 2026-10-16) — **IMPLEMENTED 2026-10-16**

Reported against a live feed: a `severity` rule reading `$.severity` (the vendor's 0-100 score) with the
engine's default `str` transform *runs*, produces `"70"`, is refused as `bad_type`, and falls back to the
feed's `default_severity` — the value it already had. The pass was correct and changed nothing, the flash
said "0 updated, 1 unchanged", and the alert page still showed the ingest-time `severity_defaulted` line.
Three feedback defects, all of them "the UI could not distinguish a working rule from an unreadable one":

1. The **dialog** described the candidate *set* (`summarise_candidates`) and the field list, never what
   the rules would actually produce — so the operator could only accept or cancel blind, and the outcome
   of a broken rule was indistinguishable from the outcome of a correct one.
2. The **flash and audit row** had no way to say "a rule produced no value": `updated`/`unchanged` are the
   only outcomes counted, and both are identical whether the value was read or defaulted.
3. The **alert/case pages** rendered only `ingestion_warnings["warnings"]` — the append-only log — while
   the *live* `ingestion_warnings["mapping"]` record (the one that says which rule failed, on which path,
   and what value it saw) was rendered by no template at all. The log's `severity_defaulted` line was
   therefore the only visible evidence, and it survives any fix (it was true when written).

Two further things surfaced from the same report and are fixed with it: a pass that changes no *field* but
replaces a stale `mapping` record now reports that write (`refreshed`, **MAP-12**), and the dialog now
states which rules a re-map will **never** apply (`type`/`source_ref`, **MAP-9**) — the other reason an
operator sees "I added a rule and nothing changed". Read from the *candidate* config, since the dialog
exists for rules that are not saved yet.

AC:
24. Preview: `intent=apply` renders a per-target dry run from a read of the real candidates (how many rows
    the rule would change, and how many it produced nothing for, by reason) and writes nothing — no alert
    field, no `updated_at`, no `ingestion_warnings`; the numbers come from the same decision function the
    write path uses, so a rerun's counts match what it then writes.
25. Unreadable rules: a pass whose outcome is "no field changed" names every target whose rule produced no
    value and the reason, in the flash message; the audit row records the refreshed-count as well.
26. Never-applied rules: the confirmation dialog names any `type`/`source_ref` rule in the candidate config
    as one that only applies to newly ingested alerts, before the operator confirms.

**Implemented 2026-10-16** — `ingest/mapping.py` (`UNRESOLVED_REASONS`, `REASON_LABELS`),
`ingest/remap.py` (`RemapResult.refreshed`/`unresolved`/`touched`, `preview_alerts` + `RemapPreview`,
`_evaluate` shared by both passes, `_refresh_record`/`_record_is_current`), `ui/views.py`
(`_unresolved_lines`, the flash/audit half, `apply_ignored_rules` from the candidate config),
`ui/templatetags/ui_tags.py` (`reason_label`, `is_unresolved`), `ui/templates/ui/_mapping_resolution.html`
+ its two includes, and the extended `_source_remap_confirm_body.html`. Deviation **MAP-12** recorded in
`docs/spec/deviations.md`. Evidence: `test_mapping_remap.py` (25 cases), `test_settings_source_mapping.py`
(48 cases), `test_ui_loop.py` (28 cases), `test_ui_markdown.py` (31, incl. `MappingResolutionRenderingTests`)
— full suite 2340 passed / 3 skipped, `make check` green.

**Not fixed, deliberately:** the rule itself. A `$.severity` path against a 0-100 score needs
`severity_name` (the vendor's `severity_name` field) or `severity_score` (the numeric bands) — that is a
config edit for the operator, in the editor this phase now gives feedback in, not a code change. Naming
the failure is this wave's job; choosing the rule is theirs.


## 9. Security

- **Path validation** happens at save time so an operator cannot ship a config that only fails when
  alerting matters. jsonpath-ng compiles in the form clean.
- DoS: `paths` per field (≤8) and fields per source (≤20), path string ≤200 chars; size/depth guards
  stay where they are (pre-parse, `WEBHOOK_MAX_*`). The engine is a pure function over an already
  depth-checked document.
- **Injection**: transforms only produce ints/strings/datetimes/aware strings; a mapping cannot write
  into a template or a shell — but a mapping *can* pull a 4 KB description into a title, hence
  `max_length` on `str` (P1) and the form's upper bound.
- **Tenancy (Q5)**: the mapping lives on an org-owned source and org-owned rows are the only ones an
  `admin` can reach, so "admin edits his org's mapping" is a scope guard, not a naming convention —
  and forgetting it is a cross-tenant write. Hence AC8/AC9 are conformance tests, not code review.
- **Preview** (Q2) takes operator-supplied JSON: auth-gated, size-capped, never persisted, never
  logged, and it must not echo the sample back into a page section that survives reload.
- Secrets: no config value is secret. A severity name is not a credential.

## 10. Testing

| Kind | What |
|---|---|
| Unit (`tests/test_mapping.py`) | every transform's happy path + wrong type; the §5.3 table in full; `severity_score` bands; `paths` first-match/empty-string skip; defaults precedence; legacy normalization; malformed config → warnings, no raise |
| Contract (`tests/conformance/`) | one test per AC of P1; a **replay of the real dev payload shape** (embedded, no dev DB) asserting the draft; `Symptoms` of a stale rule (`ingestion_warnings`) |
| Compatibility | run the five fixture files unchanged (`test_webhook_hardening`, `test_mvp_loop`, `test_mvp_loop_automation`, `test_ui_loop`, `test_correlation_key_derivation`) |
| UI (`tests/conformance/test_settings_source_mapping.py`) | P2 ACs 8–12, P3 13–14 |
| CSS/token guard | any new class must use tokens that exist; extend nothing blind (§ modal blind spot) |

## 11. Decisions (answers, 2026-10-16)

| # | Question | Decision |
|---|---|---|
| 1 | Editor shape | **Form table** *plus* the read-only JSON rendered on the page |
| 2 | Preview / dry-run | **In this wave** (P3), **plus** a "use the newest alert" button as the sample source |
| 3 | Severity | **`severity_score`** with bands (CrowdStrike's numeric `severity`), and `value_maps` is **editable in the advanced JSON view** rather than a separate form widget |
| 4 | Unmappable value | **Keep the alert, record the warning** — no rejection path at ingest |
| 5 | Permissions | Not the "superAdmin-only global profile" option: **the source itself becomes org-owned**, so `admin` edits its own org's sources and `superAdmin` any — see §6.1 |
| 6 | Legacy configs | Keep the flat `{field: "$.path"}` read path forever; the UI writes v2 |
| 7 | Retro-mapping | Not in this wave — record in `TODO.md` |
| 8 | v1 targets | `title`, `description`, `summary`, `severity`, `date`, `type`, `source_ref` — **`tlp`, `pap`, `tags`, `correlation_key` are out** (the last stays on today's legacy path, see below) |

Q8 fallout: `tlp`/`pap`/`tags` are dropped from the target list for this wave. `correlation_key` is
**not** a v2 field at all — it keeps its current `extract_correlation_key` spelling list until someone
needs it, so §5.1's example loses that key. The §5.2 transforms `tlp`/`pap`/`list`/`list_join` stay in
the closed set but are **not reachable from a v1 config**; only `str`, `int`, `severity_name`,
`severity_score`, `severity_int`, `datetime` are used by the shipped targets. (Keeping them costs one
table row each and means P5 does not have to reopen the schema.)


## 12. Risks

| Risk | Mitigation |
|---|---|
| A wrong rule silently mislabels alerts (severity is triage order) | Preview (P3) + `ingestion_warnings` on every defaulted field + the source page showing the last warnings |
| Replacing today's `_mapped` behaviour breaks 5 fixture files | P1 AC4 and AC5; normalize legacy on read |
| Vendor vocabulary drift ("Suspicious" → new vendor word) | warning names the offending value; `value_maps` lets an operator fix it without a deploy |
| `mapping_config` grows into an untyped dumping ground | closed set of targets/transforms; form errors; ADR-009 (§6.1) + the schema is documented in `docs/spec/api.md` (P5) |
| Preview endpoint becomes a PII sink | no persistence, no logging, size cap, admin-only (AC14) |
| Deep/wide JSONPath on attacker input | unchanged pre-parse guards; `paths` count/length caps |

## 13. Deviations to record when implemented

Recorded in `docs/spec/deviations.md`, § "Wave 2026-10-16 — the per-source field mapping engine (P1)"
(ids MAP-1…MAP-4):

- `ingestion_warnings` gains a structured `mapping` list (existing string list kept) — done.
- `IngestionSource.default_severity` starts being honoured (it was ignored) — a behaviour change for
  any source that set it — done.
- The legacy flat config keeps working but is no longer what the UI writes — the *working* half is done
  (P1); the "no longer what the UI writes" half is owed by P3.
- Also recorded, beyond the plan: `severity_defaulted:` now names the paths it tried; a rule that
  matched nothing warns where the old reader was silent; an empty string is no longer an answer; a
  legacy severity that cannot be coerced no longer ends the candidate search; a compiled-JSONPath cache
  (1.8 ms → ~1 µs per path).
