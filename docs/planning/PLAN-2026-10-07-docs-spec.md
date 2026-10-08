# PLAN — docs/spec/: the implemented-system specification

Date: 2026-10-07 · Status: **implemented** · Owner: planner

## Summary

Create `docs/spec/` (TODO **§4.4** — currently a placeholder entry: "Intended home for the
data-model/API spec once it stabilises past the plan"). The system has stabilised through Phase 11
(P1–P11 shipped, T1 surface closed in P10, Conformance/Verify reports green), so the spec now
describes the **implemented** system — not the original plan's intent. The source of truth is the
code + the conformance suite (`tests/conformance/`); the spec is the human-readable, pointer-rich
map of that truth.

## Goals

- G1 — `docs/spec/` exists with an index (`README.md`) and per-surface files: data model, REST API,
  realtime/WebSocket protocol, automation/domain events, query DSL, identity/auth + settings.
- G2 — Every identifier (table, model, field, endpoint, method, status code, event kind, WS frame
  type) matches the code exactly. No invented surface, no plan-era names that the code no longer
  uses.
- G3 — Deviations from AGENTS.md §2/§3 and from the original plan (M3/M6/M9/M12/M14, P8-1,
  P10-2…P10-13 incl. the code-only P10-w, login 400-not-401, ISO-8601 vs epoch-ms, deferred F2/F9)
  are collected in one register inside the spec, each with its code reference and the plan/ADR that
  recorded it. **Correction (2026-10-08):** the original G3/AC3 text listed "A3/A5" and letter IDs
  "P10-b/c/d/w/x/y/z"; `A3`/`A5` are Phase-10 *task* IDs (`PLAN-2026-10-05-phase10-t1-closure.md`),
  not plan §13 deviations, and the letters are brief aliases of the numeric IDs. The register uses
  the canonical §13 numeric IDs with the alias noted where one exists.
- G4 — Spec files cross-reference the evidence trail (plan sections, ADRs, REVIEW/VERIFY reports,
  conformance tests) so a reader can jump from a claim to its binding test.
- G5 — TODO §4.4 closed with the file inventory; wave recorded in COMPLETED.md; links from
  README.md to `docs/spec/`.

## Non-goals

- **Not a tutorial / quickstart** (README.md already has one) — spec is reference material.
- **Not an OpenAPI/Swagger file** (G2 requires exactness, but the repo deliberately has no
  OpenAPI generator; the wire contract lives in `core/serializers.py` + conformance tests — see
  ADR-002). If a machine-readable spec is wanted later it must be generated from code, not written.
- **No API changes**, no new tests, no code edits. If a spec-vs-code mismatch surfaces, it is
  **recorded** in the register for a separate fix wave, not silently "fixed" by writing the spec to
  match the plan.
- **No version upgrades or config changes** (auth/settings docs describe current behaviour,
  including deferred hardening items).

## Architecture / structure

```
docs/spec/
├── README.md              # index, reading guide, conformance statement, evidence map
├── data-model.md          # entities per app: fields, FKs/on_delete, constraints, indexes
├── api.md                 # REST surface: paths, methods, bodies, responses, error envelope
├── realtime.md            # Channels: route, consumer protocol, ledger keyset, event kinds
├── automation.md          # domain events, triggers, dispatch/idempotency, executor safety
├── query-dsl.md           # query API: steps, operators, field whitelists, paging, paging
├── identity-auth.md       # users, org, ApiKey/scope auth, DRF config, settings per env
└── deviations.md          # register: AGENTS.md §2/§3 + plan deviations, each with evidence links
```

Source material: the explore-agent inventory (verified 2026-10-07, agent `ses_ee945ca0effeU9Sp0qu7RFqZBi`),
read fresh from `*/models.py`, `*/urls.py`, `*/views.py`, `core/serializers.py`, `compat/errors.py`,
`compat/time.py`, `query/engine.py`, `automation/*`, `realtime/*`, `amalthea/settings/*`.

## Content requirements per file (mandatory, checked by AC2)

1. **data-model.md** — one section per app; class docs quoted where they pin semantics (e.g. the
   `Alert.source` vs `ingestion_source` independence, `Case.save()` allocation + `stamp_closed_date`,
   `Observable.save()` rehash behaviour, `data_hash` recompute incl. `bulk_create`). Constraint and
   index names verbatim (`uniq_alert_source_type_ref`, `ar_pending_idx`, `timeline_case_date_idx`, …).
2. **api.md** — all `/api/v1/` routes with both slash spellings where they exist; method table; body
   fields and required-ness; 2xx/4xx codes incl. the error envelope (`thehive_exception_handler`);
   `healthz`/`readyz` (root, non-v1); `raw_payload` never inlined; `_id`/`id` dual exposure; ISO-8601
   serializers vs epoch-ms inputs (`customEvent`/alert `date`, timeline wire).
3. **realtime.md** — `ws/case/<case_id>/`, close codes 4401/4000, `sync` request frame, `timeline`
   response frame, `event` relay, `append_timeline_event` as the sole write path + on-commit
   publish, `events_after` keyset semantics.
4. **automation.md** — `TRIGGER_EVENTS`, the four `DomainEvent`s, signal wiring, `idempotency_key`
   formula, `_idempotent_subject`, `record_result` back-to-case, worker retry/terminal rules,
   executor safety (`assert_safe_url` allowlist + hop re-check, `MAX_OUTPUT_BYTES`,
   `_interpolation_values` never exposing `raw_payload`).
5. **query-dsl.md** — request/response contract, start steps, `filter`/`sort`/`page`/`count`,
   `_LEAF_OPS`/combinators, `_between` semantics, `_NO_MATCH`, `X-Total`, `getCase` miss → empty
   set, cross-kind `_sort_rows` tie-break.
6. **identity-auth.md** — `User`/`Organisation`/`ApiKey`, `ScopePermission` fail-closed read keys,
   login 400-not-401 rationale, DRF default classes, throttle rates, CSRF/cookie reality (no
   `SESSION_COOKIE_SECURE`/CORS today — the deferred prod-hardening item F9), settings table per env.
7. **deviations.md** — register table: deviation id (M*/A*/P*/P10-*), AGENTS.md/plan claim, code
   behaviour, evidence (file:line + conformance test + plan/ADR/VERIFY section).

## Phases, tasks, acceptance criteria

### Task 1 — skeleton + index
- [ ] T1.1 Create `docs/spec/` with `README.md` (index, evidence map, conformance statement) and the
      seven content stubs with headings only.
- **AC1** — `docs/spec/README.md` links to all seven files and to `tests/conformance/`,
  `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §7/§13, `ADR-002`; every target exists.

### Task 2 — write the content from the verified inventory
- [ ] T2.1 data-model.md
- [ ] T2.2 api.md
- [ ] T2.3 realtime.md
- [ ] T2.4 automation.md
- [ ] T2.5 query-dsl.md
- [ ] T2.6 identity-auth.md
- [ ] T2.7 deviations.md (register drawn from the inventory's deviation log + plan §13)
- **AC2** — every code identifier quoted in the spec (table names, field names, endpoint paths,
  event kinds, WS types, error codes, op names) exists verbatim in the source (spot-check via rg:
  ≥3 samples per file, 100% hit rate of sampled identifiers); zero plan-era identifiers that the code
  does not contain (e.g., no `case` table — it is `case_record`; no `Todo` task status —
  statuses are `Waiting/InProgress/Completed/Cancel`).
- **AC3** — deviations register contains ≥15 rows covering M3/M6/M9/M12/M14, P8-1, P10-2…P10-13
  (incl. the code-only P10-w) and the deferred F2/F9, plus the login-400 + timestamp-split items;
  each row carries a code pointer (file or `file:line`) and ≥1 evidence link. (`A3`/`A5` are Phase-10
  task IDs, not deviations — see the G3 correction note.)
- **AC4** — `data-model.md` constraint/index names, `api.md` route table, `realtime.md` protocol,
  `automation.md` trigger set, `query-dsl.md` operator set each match the source (sample-verified).

### Task 3 — evidence cross-links + record & close
- [ ] T3.1 Add per-file "Evidence" footers mapping content → tests/reviews/plans.
- [ ] T3.2 README.md gains a speculative "drift policy" note: the spec is pinned by the conformance
      suite; a code change that breaks a quoted identifier requires updating the spec in the same
      wave (per repo §1.18-adjacent truthfulness norm).
- [ ] T3.3 TODO §4.4 closed with inventory; COMPLETED.md wave entry; plan §13 Phase 12 record.
- **AC5** — TODO §4.4 shows the file list; COMPLETED.md has the entry; master plan §13 has the
  Phase 12 record with any spec-vs-code mismatches found (expected: none beyond recorded
  deviations, but the register is the evidence).

## Security / Testing / Risks

- Security: spec is documentation; no secrets, no reproduction of production-worthy payloads beyond
  what conformance fixtures already contain. Do **not** copy `.env.example` secrets or commit
  anything not already public in the repo.
- Testing: no code changes → `make check` must stay green trivially; the pre-commit hook is bypassed
  for docs-only commits by design (`pre-commit.sh` skips when no `.py/.toml/.yml/.yaml` staged), but
  AC2's rg spot-checks are the real gate here.
- Risks:
  | Risk | Mitigation |
  |---|---|
  | Spec drifts from code immediately after the wave | Conformance-suite pinning + drift policy in README (T3.2); identifiers sample-verified (AC2) |
  | Plan-era names leak in (case vs case_record, Todo vs Waiting) | AC2 explicitly samples these two; deviations.md records them |
  | Reader cannot find the binding test | Evidence footers (T3.1) link each surface to its conformance test |

## Open questions

None — surface was inventoried exhaustively; any ambiguity is resolved toward the code and recorded
in `deviations.md`.