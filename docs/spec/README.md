# Amalthea — Implemented-System Specification

Status: **derived from code** · Verified 2026-10-07 (Phase 11 complete) · Owner: planner

This directory is the reference specification of the **implemented** Amalthea platform — not a
statement of the original plan's intent. Where the plan and the code disagree, the code and this
spec win, and the disagreement is recorded in [`deviations.md`](./deviations.md).

## Reading guide

| File | Covers |
|---|---|
| [`data-model.md`](./data-model.md) | Every table, field, FK/`on_delete`, constraint, index |
| [`api.md`](./api.md) | REST surface under `/api/v1/` (plus `healthz`/`readyz`), bodies, responses, errors |
| [`realtime.md`](./realtime.md) | WebSocket protocol: `/ws/case/<id>/`, sync frames, ledger events |
| [`automation.md`](./automation.md) | Domain events, playbook triggers, dispatch/idempotency, executor safety |
| [`query-dsl.md`](./query-dsl.md) | `POST /api/v1/query` — steps, operators, field whitelists, paging |
| [`identity-auth.md`](./identity-auth.md) | Users, organisations, API keys/scopes, DRF config, per-env settings |
| [`deviations.md`](./deviations.md) | Register of every deviation from AGENTS.md §2/§3 and the plan, with evidence |

## Conformance statement (drift policy)

The spec is pinned by the conformance suite in `tests/conformance/` and by the contract tests in
`tests/` (`test_t1_surface.py`, `test_authz.py`, `test_realtime.py`, `test_automation_*`,
`test_thehive_fixtures.py`, `test_ledger_keyset.py`, …). A code change that breaks any identifier or
behaviour quoted here **must** update the spec in the same wave; a spec change that claims a new
identifier **must** be backed by a test that pins it. This mirrors the repo norm that a gate that
names a command is only a gate if the command is pinned (R11 / TODO §1.18).

Machine-readable contract: none by design — the wire rendering lives in `core/serializers.py` and the
error envelope in `compat/errors.py`; TheHive-compatibility decisions live in
`docs/decisions/ADR-002-thehive-api-compatibility.md`. If an OpenAPI build is ever wanted it must be
generated from code, not hand-written here (plan `PLAN-2026-10-07-docs-spec.md` §Non-goals).

## Evidence map

- Master plan: `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` (§7 wire, §12/§13 deviations)
- ADRs: `docs/decisions/ADR-001-backend-framework.md`, `docs/decisions/ADR-002-thehive-api-compatibility.md`
- Reviews/verifies: `docs/reviews/REVIEW-2026-10-0{3,4,5}-*.md`, `docs/planning/VERIFY-2026-10-07-phase{10,11}.md`
- Implementation plan for this directory: `docs/planning/PLAN-2026-10-07-docs-spec.md`