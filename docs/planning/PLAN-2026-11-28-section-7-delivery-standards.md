# PLAN — Section 7: delivery standards and CSS artifact split closeout

Date: 2026-11-28
Closes: TODO §7.1, §7.2, §7.3, §7.4, §7.5
Status: Implemented and verified 2026-11-28; user-run full suite green. Implementation commit `d79ee587`.

## 1. Summary

TODO §7 contains four process rules and one engineering task. The engineering task (§7.4, Tailwind
source/artifact split) was already implemented by `PLAN-2026-10-19-ui-layout-standardization.md`
and later hardened with `source(none)`; the checkbox is stale. The remaining work is to make the
process rules binding in `AGENTS.md`, detail them in a spec document, add a non-vacuous conformance
guard, and close the TODO with evidence.

## 2. Goals

- **AC1 — R11 failure modes (§7.1):** every acceptance criterion must record its failure mode and a
  probe/mutation that demonstrates the criterion can fail. Passing tests without a demonstrated
  failure mode are not evidence.
- **AC2 — domain-expert gate (§7.2):** every phase — plan, implementation, verification, handoff —
  must record a domain-expert review decision, not only Phase 3.
- **AC3 — subagent report delivery (§7.3):** a subagent is not complete until it returns the report
  it promised in its final message; task id / “done” alone is not a report.
- **AC4 — baseline for failure claims (§7.5):** “pre-existing” / “unrelated” must be proven by
  reproducing on the base commit; otherwise classify as “unverified”. The full suite is the final
  gate; targeted subsets are diagnostics only.
- **AC5 — CSS split verification (§7.4):** confirm `app.src.css` is the sole Tailwind input,
  `app.css` is a generated artifact, `css-check` fails on drift, and automatic source detection is
  disabled with explicit `@source` declarations.
- **AC6 — durable documentation:** add a delivery-standards spec, link it from the spec README, and
  add a binding §7 in `AGENTS.md`.
- **AC7 — non-vacuous guard:** add `tests/conformance/test_delivery_standards.py` that fails when
  any required rule is removed from the docs or when the CSS split regresses, with synthetic
  negative controls proving the detector can fail.
- **AC8 — closeout:** check all §7 TODO items, add a COMPLETED entry, and write an independent-style
  verification report with failure-mode evidence for AC1–AC7.

## 3. Non-goals

- No application feature changes, schema/API changes, or changes to existing feature tests.
- No regeneration of `app.css` unless `make css-check` shows drift.
- No new subagent work; process rules are documented and pinned for future waves.
- No CI pipeline redesign; CI smoke remains diagnostic and the full suite remains the final gate.

## 4. Architecture / Process

- `AGENTS.md §7` is the binding short form for all agents.
- `docs/spec/delivery-standards.md` is the detailed procedure and template source.
- `tests/conformance/test_delivery_standards.py` is the mechanical guard for the written standard
  and the CSS pipeline invariants.
- `docs/verification/VERIFY-2026-11-28-section-7-delivery-standards.md` records evidence, including
  failure-mode demonstrations.

## 5. Data model / API / UI

None.

## 6. Phases

### Phase A — Process standards
| Task | Files | AC |
|---|---|---|
| A1 | `AGENTS.md` — add §7 Delivery workflow with the four rules | AC1–AC4 |
| A2 | `docs/spec/delivery-standards.md` — full rules, phase-gate template, failure-mode template, subagent report contract | AC1–AC4, AC6 |
| A3 | `docs/spec/README.md` — link the new standard | AC6 |

**Acceptance criteria:** the required phrases/behaviours are present; each rule has an evidence
requirement, not merely a statement of intent.

### Phase B — CSS closeout
| Task | Files | AC |
|---|---|---|
| B1 | Verify `Makefile`, `app.src.css`, `app.css` match the implemented split | AC5 |
| B2 | Pin the invariants in the new conformance guard | AC5, AC7 |

**Acceptance criteria:** `make css-check` passes; guard fails on a synthetic Makefile without
`app.src.css` / `css-check`; guard fails if `source(none)` or explicit `@source` declarations vanish.

### Phase C — Closeout
| Task | Files | AC |
|---|---|---|
| C1 | `tests/conformance/test_delivery_standards.py` | AC7 |
| C2 | `docs/verification/VERIFY-2026-11-28-section-7-delivery-standards.md` | AC8 |
| C3 | `TODO.md`, `COMPLETED.md` | AC8 |

**Acceptance criteria:** all §7 items checked; COMPLETED records commit pending; verification report
lists each AC as Met with failure-mode evidence.

## 7. Security

No code paths change. Documentation must not include secrets, tokens, or private endpoints beyond
public repo conventions. The guard reads repository text only.

## 8. Testing

- New: `tests/conformance/test_delivery_standards.py`.
- Existing: `tests/conformance/test_mutation_standard.py` (R11 mechanism),
  `tests/conformance/test_ui_css_tokens.py` (CSS tokens).
- Gates: targeted pytest, `make check`, `git diff --check`, secret scan on changed files, full suite
  by user after implementation.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Process docs become aspirational text | Pin required rules in a conformance guard with negative controls. |
| §7.4 is implemented but left stale again | Verify current files and explicitly check the TODO item with evidence. |
| Guard itself is vacuous | Mutation-check each detector against synthetic missing content. |
| Full-suite regression from doc/test additions is unlikely but possible | User runs full suite before commit/push; no application code changes. |

## 10. Open questions

None. §7.4 was already implemented; this wave verifies and closes rather than redesigns it.
