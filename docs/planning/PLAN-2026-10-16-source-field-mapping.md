# PLAN 2026-10-16 — Per-source field mapping (JSON → Alert)

**Status: APPROVED 2026-10-16 — P1, P2 and P3 IMPLEMENTED 2026-10-16** (records in §8 P1, §8 P2,
§8 P3); P4 (sample preview) and P5 (health & docs) are the next phases, and P3's `ingest/editor.py` is
what P4's dialog reuses. All eight questions answered (§11 Decisions); the Q5 consequence is recorded in
§6.1 and implemented as **ADR-009**. Written by the planner agent; the `django-backend` /
`django-frontend` / `verifier` subagents were unavailable (504/400), so the planner implemented
P1–P3 directly.

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
  (`default_severity` for `severity`) → model default + warning.
- **`max_length`** — applied to `str`; a mapping must not be able to write an unbounded title
  (`Alert.title` is 500, today the view truncates at 200).
- **`value_maps`** — operator-supplied name→int entries, merged *over* the built-in vocabulary.
  Edited in the **advanced JSON view** (Q3), not as a form row.
- **`on_missing`** — `warn` (the only shipped behaviour, Q4). The key is reserved so a later
  `"reject"` does not have to be invented; nothing reads it as `reject` today.
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

### P5 — Health & docs
`docs/spec/api.md` mapping section, `docs/spec/data-model.md` shape, `sources_list` summary,
a `mapping_warnings` view on the source page, `TODO.md` gets retro-mapping (Q7) and the deferred
`canonical_payload`/`_verify_secret` dead-code observation. `ingest/admin.py` stays empty.

### P6 (candidate, not planned here) — observables from the same config
`"observables": [{"type": "sha256", "paths": ["$.sha256"]}, …]` feeding Module C. Explicitly out of §3
so it cannot creep into P1.

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
