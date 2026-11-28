# Amalthea delivery standards

Status: **binding process specification** · Owner: planner · Closes TODO §7.1–§7.5.

This document is the detailed form of `AGENTS.md §7`. It applies to every implementation wave,
including small fixes, documentation-only changes, and subagent work. The conformance guard
`tests/conformance/test_delivery_standards.py` pins the rules below.

---

## 1. AC failure-mode evidence (§7.1 / R11)

**Rule:** A passing acceptance criterion is only evidence once its failure mode has been demonstrated.

A criterion is not “Met” because a test named after it passes. The verification record must show
that the test, guard, or observation can fail when the contract is broken. The mechanism already in
the repository is `tests/conformance/_mutation.py` and `tests/conformance/test_mutation_standard.py`;
use it for schema and detector guards, and use direct probes for behaviour that cannot be mutated
safely.

Every plan AC must include:

- **AC statement** — observable behaviour, not intent.
- **Failure mode** — what breaks if the AC is violated.
- **Evidence** — test name, command, or artefact that proves current behaviour.
- **Failure-mode demonstration** — mutation, temporary revert, synthetic bad input, or documented
  probe showing the evidence fails without the fix.

Verification reports must include a `Failure-mode demonstration` column or equivalent section. If
it is absent, the AC is `Unverified`, not `Met`.

Recommended wording in a verification report:

```markdown
### ACn: <statement>
- **Status:** Met
- **Evidence:** `<test file>::<test name>`; `<command>`
- **Failure-mode demonstration:** temporarily changed `<specific condition>`; the test failed with
  `<observed failure>`; restored and it passed.
```

---

## 2. Domain-expert gate (§7.2)

**Rule:** Every phase requires a domain-expert gate: plan, implementation, verification, and handoff.

The gate is not a rubber stamp. At each phase, record the reviewer (person or subagent role), the
questions asked, the decision, and any accepted residual risk. Phase 3 is not special; skipping the
gate in planning is the same violation as skipping it after verification.

Phase gate checklist:

| Phase | Questions that must be answerable |
|---|---|
| Plan | Does the AC match the IR/SOC workflow? What happens on alert/case/observable failure paths? Are tenant, audit, and timeline semantics preserved? |
| Implementation | Did the code follow the plan? Were deviations recorded before merge? Does the UI still make analyst sense (keyboard, dark theme, severity labels)? |
| Verification | Are failure modes demonstrated? Is the full suite green? Are skips/warnings explained rather than waved away? |
| Handoff/commit | Is the change reviewable? Are docs, TODO, COMPLETED, and verification records consistent? Are secrets absent? |

Record format:

```markdown
## Domain-expert gate

| Phase | Reviewer | Decision | Notes / residual risk |
|---|---|---|---|
| Plan | planner | Pass / Changes requested | ... |
| Implementation | ... | ... | ... |
| Verification | ... | ... | ... |
| Handoff | ... | ... | ... |
```

---

## 3. Subagent report contract (§7.3)

**Rule:** A subagent is not complete until it returns the report it promised in its final message.

A task id, a “done” acknowledgement, or a tool output stream is not a report. When delegating, the
planning agent must state the exact report required (verdict, evidence, file paths, commands,
deviations). When the subagent returns, the planning agent must check that the promised sections are
present. If the final message is missing or truncated, resume the same `task_id` and ask for the
report before marking the work complete.

Minimum final report:

```markdown
## Report
- Task: <what was asked>
- Status: Pass / Fail / Deviated
- Files changed or reviewed: <paths>
- Evidence: <commands, test counts, observations>
- Findings: <issues, severity, disposition>
- Deviations: <none or explicit list>
```

If the subagent service is unavailable, do not fabricate an independent verdict. Record the failure
in the verification report and say which agent role substituted.

---

## 4. Failure claims and test gates (§7.5)

**Rule:** A “pre-existing” / “unrelated” failure claim requires reproduction on the base commit; otherwise it is “unverified”. The full suite is the final gate; targeted subsets are diagnostics only.

Procedure when a test fails:

1. Identify the base commit (the parent of the change under review, or the branch point).
2. Reproduce the failure there using a clean worktree or checkout:
   `git worktree add /tmp/amalthea-base <base-sha>` then run the exact failing test command.
3. Record the command and output. If it does not fail on the base, classify as **introduced by this
   change** and fix it; never call it pre-existing.
4. If reproduction is impossible, classify as **unverified**, not unrelated.
5. Before closeout, run the full suite. Targeted suites may explain a regression but may not close
   the wave.

Record format:

```markdown
## Failure claim
- Test: <node id>
- Claim: pre-existing / unrelated / introduced / unverified
- Base commit: <sha>
- Reproduction command: <command>
- Result on base: <pass/fail with output excerpt>
- Disposition: <fixed / accepted / tracked>
```

---

## 5. Tailwind source/artifact split (§7.4)

The split is implemented; this section records the invariant so it cannot drift again.

- `ui/static/ui/app.src.css` is the **only** Tailwind input.
- `ui/static/ui/app.css` is a generated artifact; do not hand-edit it.
- `make css` regenerates `app.css` from `app.src.css`.
- `make css-check` compiles to a temp file and byte-compares; stale artifact is a hard failure.
- `app.src.css` uses `@import "tailwindcss" source(none)` plus explicit
  `@source "../../templates";` and `@source "./*.js";`. Automatic repo-wide detection is disabled
  so docs/tests cannot inject phantom utilities.
- If classes are authored outside those declared sources, update the `@source` list in the same
  change.

Workflow:

```bash
# edit ui/static/ui/app.src.css only
make css
make css-check
```

---

## 6. Non-vacuity of this standard

`tests/conformance/test_delivery_standards.py` checks:

- `AGENTS.md` contains the §7 heading and each binding rule.
- this document contains the four rule sections and the CSS split.
- `Makefile` uses `app.src.css` for both `css` and `css-check`, and `cmp` for drift.
- `app.src.css` declares `source(none)` and explicit `@source` entries.
- each detector fails against a synthetic document with the relevant rule removed.

A check that only searches for a keyword is not enough; the negative control must fail.
