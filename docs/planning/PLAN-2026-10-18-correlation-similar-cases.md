# PLAN — Cross-case correlation & "Similar cases" (TODO §12.3) — APPROVED

**Status:** APPROVED 2026-10-18. §11 decisions and §12 answers settled.
**Scope of this wave:** **P1 + P2** (read-only Similar surface + correlation-ranked, audited bundle).
**Deferred:** **P3** (automatic ingest-time bundling) — decisions pre-settled in §5.3, not built now.
**Owner:** planner. **Executors:** `django-backend`, `django-frontend`, then `qa-tester`, `db`, `verifier`,
`security-auditor`.
**Source:** `TODO.md` §12.3 (AGENTS.md §2 Modules B + C), §10.7, Q-D4. **Supersedes** the Q-D4 "deferred"
decision in `PLAN-2026-10-14-case-classification-fields.md` §12.

---

## 1. Summary

The platform already stores the raw material for correlation and already ships most of the plumbing to act on
it; the findings below narrow this work substantially from the original TODO framing.

| Fact (verified) | Where |
|---|---|
| `Alert.correlation_key` (namespaced, e.g. `dest_ip:10.0.0.5`) is derived at ingest, stored and indexed — and **read by nothing** | `ingest/mapping.py:815` · `ingest/pipeline.py:104-111` · `alerts/models.py:104,133` |
| `Observable` is deduped per org on `(owner_org, data_type, data_hash)`; `CaseObservable` links cases; the fan-out index exists | `observables/models.py:96-99` · `cases/models.py:300,339` |
| The observable→cases pivot already renders ("Linked Cases (N)") — the case→cases pivot does not | `ui/views.py:2707` · `observable_detail.html:178` |
| `_tab_similar.html` is the "Similar cases" tab and is a `"not yet implemented"` placeholder | `ui/views.py:793` |
| `Observable.ignore_similarity` exists, is round-tripped by API + UI, and is **consumed by nothing** | `observables/models.py:109` |
| **A manual "Merge into an existing case" affordance already exists** — but its candidate list is "newest open cases", **not** correlation-ranked | `ui/views.py:510,464` · `alert_detail.html:95-110` |
| **Neither alert→case path writes an `AuditLog` row** — only a `TimelineEvent` (`alert-imported` / `alert-merged`), though ADR-002 §9 asks for both | `alerts/escalation.py:109,150` vs the idiom at `cases/update.py:280` |
| `find_observables()` is **pure** (no DB writes) — a dry-run extractor for ranking from a raw payload | `observables/extractor.py:256` |
| Ledger `kind` is a free-text `CharField(50)`; existing kinds are hyphenated (`alert-merged`, `case-created`) | `cases/models.py:252` |
| `identity/audit.py` holds per-domain audit recorders; case mutations currently call `AuditLog.objects.create` directly | `identity/audit.py` · `cases/update.py:280,433` |

**The work is therefore two things**, sharing one primitive:

1. **A reader** (Module C): rank the cases an actor may see by shared observational artifacts.
2. **A writer** (Module B): make the *existing* attach path correlation-ranked and audited —
   `correlation-bundle` in the ledger and `case.alert_bundled` in the audit log.

---

## 2. Goals

| # | Goal | Observable outcome |
|---|---|---|
| **G1** | **Similar cases (Module C).** A case's Similar tab lists other visible cases in the same org ranked by shared-observable overlap, each row naming the shared artifacts. `ignore_similarity` artifacts are excluded. | *"2 similar cases — 3 shared artifacts"* with links, or an honest empty state. |
| **G2** | **Honest truncation and honest emptiness.** Reuses the §12.1 list conventions (`RAIL_CAP`, count line, distinguishable empty states). | The panel never implies completeness when capped, and never looks broken when empty. |
| **G3** | **Correlation-ranked attach (Module B, manual).** The existing "Merge into an existing case" form ranks candidates by correlation and explains *why* each is suggested. | The analyst sees *"#142 — Phishing wave (2 shared artifacts: `evil.tld`, `1.2.3.4`)"*, not an alphabetical dump. |
| **G4** | **The attach is audited and reasoned.** Every alert→case attach writes exactly one ledger entry naming the reason and one `AuditLog` row — closing the ADR-002 §9 gap on this path. | `case.alert_bundled` appears in the audit trail; the ledger entry says why. |
| **G5** | **Honest `ignore_similarity`.** Such observables never drive similarity ranking and never drive candidate suggestion. | The control stops being decorative. |
| **G6** | **Tenant safety (ADR-004).** Every query is actor-scoped; counts cannot become an existence oracle. | No similarity, count or suggestion can reveal a case outside the actor's scope. |

## 3. Non-goals

| # | Non-goal | Why |
|---|---|---|
| **N1** | **Automatic ingest-time bundling (P3).** | Q1 = (b). Deferred with decisions pre-settled in §5.3. |
| **N2** | No new model, and no `CorrelationRule` table this wave. | The config has no consumer until P3; an unused model is the same inert-artifact problem as TODO item 1. |
| **N3** | No automatic case-to-case merge; never auto-attach into a **closed** case. | Silent merges are unacceptable; closed cases are evidence. |
| **N4** | No graph DB, ML scoring or clustering; no weighted/opaque score in v1. | An analyst must see *why* two cases are related. |
| **G/N N5** | No new API routes; no new model. UI + existing service path only. | ADR-002: don't invent incompatible surface; an additive API endpoint is a follow-up. |
| **N6** | No new bundle service duplicating `alerts/escalation.py`. The writer is the existing function, upgraded. | Two attach paths would drift; the trail must be identical for escalate and merge. |

---

## 4. Architecture & the shared primitive

One pure, org-scoped query builder, one audited mutation. `cases/correlation.py` (new module):

```python
@dataclass(frozen=True)
class SimilarCase:
    case: Case
    shared_count: int
    shared: list[tuple[str, str]]  # [(data_type, data), ...] — the *explanation*


def cases_sharing_findings(
    findings: Sequence[Finding],
    *,
    user: User,
    exclude_case: Case | None = None,
    limit: int = RAIL_CAP,
) -> tuple[list[SimilarCase], int]: ...
```

* **`findings`** are the join key. Both callers produce them from the same pure extractor:
  * **Module C** (case→cases): the case's own `CaseObservable` rows, excluding `ignore_similarity=True`.
  * **Module B** (alert→cases): `find_observables(json_leaves(alert.raw_payload), alert.title, …)`, a
    **dry-run with no writes**, plus the alert's `correlation_key`.
* **Scope:** candidates are restricted to `case_visible_q(user)` and the same `owner_org` (ADR-004) *before*
  counting, so both the rows and the `(N)` are post-authorization (§8 S1/S2).
* **Join:** one grouped query over `CaseObservable`, whose `caseobs_obs_case_idx` (`cases/models.py:339`, lead
  column `observable`) exists for precisely this fan-out.
* **Ranking (Q2a):** `shared_count` desc, then `case.number` asc — stable and explainable. No weights in v1.
* **Return:** `(top_limit_rows, total_matching)` so the caller can render "showing top N of M" honestly (G2).
* **Closure policy (Q7a):** closed cases are shown, labelled with their status, and excluded only from
  *suggestion for attachment* (G3's form filters to open), never from the Module C list.

`Alert.correlation_key` is the second, indexed signal for Module B: `alert_corrkey_date_idx`
(`alerts/models.py:133`) makes "which open cases already hold an alert with this key" a single indexed lookup.

### 4.1 The audited mutation

```python
def record_alert_bundled(
    case: Case, alert: Alert, *, actor: User, reason: str, source: str
) -> TimelineEvent: ...
```

Written into the **existing** paths so escalate and merge cannot drift (N6):

* **Ledger:** `TimelineEvent(kind="correlation-bundle")` — hyphenated to match every existing kind
  (`alert-merged`, `case-created`, `automation-run`) — naming `reason` and the alert source ref.
* **Audit:** `AuditLog.objects.create(actor=…, actor_org=getattr(actor, "org", None), target_org=case.owner_org,
  action="case.alert_bundled", target_type="Case", target_id=str(case.id), ip=None, details={…})` —
  byte-for-byte the idiom at `cases/update.py:280-289`; `details` is non-secret only
  (`AuditLog.details` is documented "**Never** put a secret here").
* **Transaction:** called inside the caller's existing `transaction.atomic()` (`ui/views.py:489,518`), so the
  link, the observables, the ledger entry and the audit row land together or not at all.
* **Idempotence:** `Alert.case` is the marker; an already-attached alert is refused, never re-linked, so a
  replayed webhook or a double-submit cannot duplicate the trail.

---

## 5. Scope

### 5.1 In this wave

* **P1 — Module C.** `cases/correlation.py::cases_sharing_findings` + `similar_cases(case, *, user)` wrapper;
  `case_detail` passes `similar_cases`/`similar_total`; `_tab_similar.html` renders it.
* **P2 — Module B, manual + audited.** Rank the alert's merge candidates by correlation; wire
  `record_alert_bundled` into `alert_escalate` **and** `alert_merge` so both write ledger + audit.

### 4.2 / 5.3 Deferred: automatic bundling (P3) — decisions pre-settled

Not built this wave (Q1b). Recorded so it is ready to pick up, and so the trigger is not re-litigated:

* **Q3c** — a rule matches on `correlation_key` **or** shared observable, configurable, defaulting to
  `correlation_key` within the window.
* **Q4a** — the rule lives in a minimal per-org `CorrelationRule` (`enabled=False` default,
  `window_minutes=10`, capped), **created only when P3 is implemented** — not now (see N5/§6).
* **Q5a** — evaluate synchronously in the ingest request, behind `enabled=False`, with a bounded window;
  the candidate lookup is the `alert_corrkey_date_idx` seek.
* **AGENTS.md's example** — "identical destination IP within a 10-minute window" — is exactly this rule.
* Prerequisites when it starts: §12.1-fu1's Postgres measurement (index confirmation for the join), and a
  bound so one webhook cannot attach to an unbounded candidate set.

---

## 5.x Data model / migrations

**No migration and no new model this wave.** `TimelineEvent.kind` (free text) and `AuditLog.action` (free
text) admit the new values without schema change — the design intent stated in `AuditLog.details`'s
help_text ("a new audited action needs no migration"). The `db` agent still **confirms** (does not assume)
that `similar_cases` and the candidate lookup use `caseobs_obs_case_idx` and `alert_corrkey_date_idx` and
reports any index it believes is missing; that reasoning is recorded in §13.

---

## 6. Interfaces (frozen)

**`cases/correlation.py`** (new)

| Symbol | Signature | Contract |
|---|---|---|
| `SimilarCase` | frozen dataclass — `case`, `shared_count`, `shared: list[tuple[str, str]]` | `shared` is the rendered explanation; ordered by `data_type` then `data` |
| `cases_sharing_findings` | `(findings, *, user, exclude_case=None, limit=RAIL_CAP) -> tuple[list[SimilarCase], int]` | actor-scoped; `ignore_similarity` excluded; `shared_count` desc then `case.number` asc; second element is `total_matching` **before** `limit` |
| `candidate_cases_for_alert` | `(alert, *, user, limit=PICKER_PAGE_SIZE) -> list[SimilarCase]` | **open** cases only; ranked as above; never raises on an empty/garbage payload |
| `record_alert_bundled` | `(case, alert, *, actor, reason, source) -> TimelineEvent` | writes exactly one ledger entry + one audit row; caller owns the transaction |

**Constants:** reuse `ui.pagination.RAIL_CAP`; **do not** introduce a second cap (the §12.1 D1 drift).

**View/context contract (`ui/views.py`)**

| View | New context | Behaviour |
|---|---|---|
| `case_detail` | `similar_cases: list[SimilarCase]`, `similar_total: int` | under the existing `_tab_similar.html` include; no extra query when the tab is not rendered |
| `alert_detail` | `merge_candidates` becomes correlation-ranked (`list[SimilarCase]`) | must keep a working plain-open-case fallback so an alert with no extractable findings still offers a target |

**Templates:** `_tab_similar.html` (ranked rows, explanation, `_list_summary.html` for truncation, distinct
empty states), `alert_detail.html:95-110` (candidate labels gain "— N shared artifacts: …").

---

## 7. UI/UX

Dark theme, server-rendered, no new JS, keyboard-operable, `<dialog>` (never `confirm()`) for any
confirmation. Severity/status always carry a text label, never colour alone. Icons from Font Awesome Free,
decorative ones `aria-hidden`, icon-only controls named.

* **Similar tab:** ranked rows → `#number — title`, status, shared count, and the shared artifacts as
  monospace chips. Two distinct empty states: *"No other case shares artifacts with this one"* vs
  *"This case has no artifacts yet"*.
* **Attach form:** candidates become `#N — title (2 shared artifacts: evil.tld, 1.2.3.4)`; correlation
  matches come first and are labelled; unrelated open cases remain reachable (never silently removed from the
  list — an analyst may know better than the heuristic).
* **Settings:** untouched this wave (no rule to configure yet).

---

## 8. Security

| # | Requirement |
|---|---|
| **S1** | Both the rows and the `(N)` are computed **after** `case_visible_q(user)` + same-org scoping, so similarity is not an oracle. |
| **S2** | A test must prove that an actor with no access to a case cannot see it in the list, in a count, or as a candidate — including by crafting an alert payload that shares the artifact. |
| **S3** | `record_alert_bundled` refuses closed cases, cross-org pairs and viewer actors; refusals are typed and uniform-404 for out-of-scope ids; a failure after the link rolls back link + ledger + audit together. |
| **S4** | `details` in the audit row carries no secret and no full raw payload (only counts, the correlation key and source refs). |
| **S5** | `find_observables` runs on attacker-controlled payloads: the dry-run path must keep the existing per-payload bounds (size/depth limits from the ingest path) and must not raise on malformed input. |

---

## 9. Testing

`qa-tester` owns T1–T7; `db` owns T-DB1.

| # | Case |
|---|---|
| **T1** | Ranking: two cases sharing two artifacts outrank two sharing one; ties break by `case.number`; no-overlap cases are absent. |
| **T2** | `ignore_similarity` observables are excluded from both the count and the rendered list. |
| **T3** | Visibility (S1/S2): cross-org fixture sharing an identical artifact value → neither org sees the other, not even a count; viewer sees the list but cannot attach. |
| **T4** | Attach: exactly one ledger entry (`correlation-bundle`) + one `AuditLog` row (`case.alert_bundled`) per attach; a mid-path failure rolls all three back. |
| **T5** | Idempotence: a second attach of the same alert is a typed no-op, with no second ledger/audit row. |
| **T6** | Degraded input: an alert with an empty/garbage payload still renders the form and still offers plain open cases (S5); a case with only ignored observables gets the correct empty state. |
| **T7** | No API surface changed (N5): existing routes' shapes are untouched. |
| **T-DB1** | Index plan: `similar_cases` + candidate lookup use `caseobs_obs_case_idx` / `alert_corrkey_date_idx`; reasoning recorded. |

Existing suites that must stay green: all of `tests/conformance/`, plus the §12.1 pagination suites (this work
reuses `_list_summary.html` and `RAIL_CAP`).

---

## 10. Phases & Acceptance Criteria

### P1 — Module C: the correlation reader + Similar tab

| Task | Acceptance Criteria |
|---|---|
| **P1.1** `cases/correlation.py`: `SimilarCase`, `cases_sharing_findings` | **AC-P1.1** Actor-scoped + same-org before counting; `ignore_similarity` excluded; `shared_count` desc then `case.number` asc; returns `(rows, total_matching)` with `total_matching` pre-`limit`. |
| **P1.2** `ui/views.py::case_detail` passes `similar_cases`/`similar_total` | **AC-P1.2** No new model; no second cap constant (`RAIL_CAP` reused); the tab's data is not queried when not rendered. |
| **P1.3** `_tab_similar.html` renders ranked rows + both empty states | **AC-P1.3** Shared artifacts named per row; truncation uses `_list_summary.html`; keyboard-reachable; no colour-only status. |
| **P1.4** Tests T1–T3 + the empty-state half of T6 | **AC-P1.4** All pass; T3 proves S1/S2 with a cross-org fixture. |

### P2 — Module B: correlation-ranked, audited attach

| Task | Acceptance Criteria |
|---|---|
| **P2.1** `candidate_cases_for_alert` (open cases, correlation-ranked, plain fallback) | **AC-P2.1** Never raises on malformed payload; an alert with no findings still yields the open-case list; correlation matches ranked first and labelled. |
| **P2.2** `record_alert_bundled` (ledger `correlation-bundle` + audit `case.alert_bundled`, same-org/role/closed refusals) | **AC-P2.2** Exactly one of each per attach; all-or-nothing with the link; refusals typed; uniform 404 out-of-scope. |
| **P2.3** Wire into `alert_escalate` and `alert_merge` (**both**, so the trail cannot drift) | **AC-P2.3** Both paths now emit ledger + audit; the existing `alert-imported`/`alert-merged` kinds and all current tests keep working (no behaviour removed). |
| **P2.4** `alert_detail.html` candidate labels + confirm step | **AC-P2.4** Label shows the shared count/artifacts; works with no JS; states the consequence before the click. |
| **P2.5** Tests T4, T5, T7 + the fallback half of T6 | **AC-P2.5** All pass. |

### P3 — DEFERRED (automatic bundling) — see §5.x. Not in this wave.

### P4 — Verification
`qa-tester` → `db` (T-DB1) → `verifier` (AC grading → `docs/verification/`) → `security-auditor` (S1–S5 →
`docs/reviews/`). Then `make check`; commit and push **only on explicit user approval**.

---

## 11. Decisions (settled)

| # | Decision | Rationale |
|---|---|---|
| **Q1 = b** | This wave = Similar tab **+** audited, correlation-ranked manual attach. Automatic ingest-time bundling deferred. | The tab is the visible Module C capability; the attach upgrade is small because the merge path already exists. Auto-bundling mutates without a click and deserves its own plan. |
| **Q2 = a** | Rank by raw shared-artifact count, ties by `case.number`; show the shared artifacts. No weights. | Explainability over cleverness — the analyst can see *why*. |
| **Q3 = c** *(deferred)* | Rule matches on `correlation_key` or shared observable, configurable, default `correlation_key`. | Pre-settled for P3. |
| **Q4 = a** *(deferred)* | Per-org `CorrelationRule`, `enabled=False`, `window_minutes=10` capped. **Not built now.** | Multi-tenant config (ADR-004); building it before its consumer would create the inert-artifact problem of TODO item 1. |
| **Q5 = a** *(deferred)* | Synchronous, bounded, behind `enabled=False`. | Ingest stays fast by default; the lookup is an indexed seek. |
| **Q6 = a** | Per-alert affordance only — an upgrade of the **existing** merge form. No new UI surface. | The affordance already exists; the gap is ranking + audit, not a new control. |
| **Q7 = a** | Closed cases appear in the Similar list, labelled; excluded only from attachment. | AGENTS.md Module C's whole point is spotting a pattern across months of closed cases. |
| **Q8 = b** | Ledger kind **`correlation-bundle`**; audit action **`case.alert_bundled`**. | Normalized from `correlation_bundle` to the hyphenated `kind` convention (`alert-merged`, `case-created`) and the dotted `action` convention (`case.alerts_closed`). |
| **D1** | **The missing `AuditLog` row on `alert_escalate`/`alert_merge` is in scope** (finding from recon: both write only a `TimelineEvent`). | ADR-002 §9 wants ledger **+** audit on this exact path; ~10 lines using the existing idiom. **Expansion — flag if unwanted.** |
| **D2** | `identity/audit.py` gets **no** new named recorder; use the direct `AuditLog.objects.create` idiom of `cases/update.py`. | `audit.py`'s recorders are cross-org/admin-scoped; a case-owned attachment is not one of those. |

---

## 12. Open questions

None blocking. The two items carried forward from the previous session are **outside** this plan and still
await a decision: §12.1-fu1 (needs Postgres), §12.8-fu2 (security review of the close-flow — the pool is
working again), and TODO item 1 (inert migration edges).

---

## 13. Deviations & decisions during execution

Recorded 2026-10-18 after the P1+P2 implementation landed. Verified by the planner, not taken on trust.

| # | Deviation / decision | Reason | Status |
|---|---|---|---|
| **D3** | `_tab_similar.html` does **not** `{% include %}` `ui/_list_summary.html`; it replicates the pattern (`<p class="list-summary dim">Showing N of M…`). | The partial is **pagination-shaped**: it iterates `page_obj.paginator.count` / `start_index` / `end_index`, and the Similar tab is a **capped rail with no `page_obj`** — exactly like the §12.1 case-detail rails. Same visual contract, different mechanism. Recorded against AC-P1.3's literal wording ("truncation uses `_list_summary.html`"). | Accepted |
| **D4** | `PICKER_PAGE_SIZE = 200` was **added to `ui/pagination.py`** (it was not there before) and is imported by `ui/views.py`. | §6 required reuse rather than a second constant. Verified: **exactly one definition repo-wide** (`grep` in `ui/` returns only `ui/pagination.py:67`), so no drift was introduced. | Accepted |
| **D5** | ~127 lines of new component CSS added to `ui/static/ui/app.css` (`.similar-*`, `.shared-chip*`) despite §7's "add no new CSS unless essential". | No existing class covered a ranked row with artifact chips; the added rules use **theme tokens only** (`--color-bg-raised`, `--color-line`, `--radius-sm`, `--font-mono`) and include a `max-width: 640px` stack. | Accepted |
| **D6** | Severity rendered as raw `sev-{{ case.severity }}` (matching `observable_detail.html:201-202`), not the `|sev_class` filter. | Verified correct: `Case.severity` is `SmallIntegerField` and `app.css` defines `sev-1`…`sev-4`/`sev-unknown`. **`|sev_class` returns `high`/`critical`, i.e. `sev-high`/`sev-critical`, which are NOT defined in `app.css`** — a **pre-existing** defect at `_tab_alerts.html:25` (and `sev_label` is fine). Out of scope here; recorded as a new TODO item. | Accepted + **new finding F1** |
| **D7** | Idempotence (T5) is implemented by checking for an existing bundled ledger entry, rather than by treating `Alert.case` as the marker as §4.1 proposed. | Functionally equivalent for the attach path, but **not verified by the planner**. The verifier must confirm it cannot double-write and that it also covers the `alert.case_id == case.id` case. | **Open — verification required** |
| **D8** | `ui/views.py::case_detail` computes `similar_cases`/`similar_total` **only when `active_tab == "similar"`**. | Satisfies AC-P1.2 ("no extra query when the tab is not rendered"). | Accepted |
| **M1** | `alert_escalate` atomicity: the outer `with transaction.atomic():` in `ui/views.py:492-507` already wraps both `import_alert_to_case` + `record_alert_bundled`; nested `@transaction.atomic` on `import_alert_to_case` uses savepoints so a failure in `record_alert_bundled` rolls back the link + observables + `alert-imported` ledger entry together. Test `test_failure_rolls_back_link_ledger_audit` confirms all-or-nothing. | Reviewer misread the code — the atomic block was already correct. | Accepted (no code change) |
| **M2** | Unbounded intermediate query results: added `MAX_CORRELATION_OBS = 1000` constant and applied `[:MAX_CORRELATION_OBS]` slice at DB level on `Observable` query in both `cases_sharing_findings` and `candidate_cases_for_alert` (`cases/correlation.py:112-115, 273-276`). Reuses existing cap philosophy, no new cap constant. | Prevents DoS via crafted payload with thousands of observables. | Fixed |
| **M3** | Dry-run path ingest bounds: `candidate_cases_for_alert` now (a) checks `WEBHOOK_MAX_BODY_SIZE` (5 MiB) and `WEBHOOK_MAX_DEPTH` (30) before processing `alert.raw_payload`, (b) uses iterative `_json_depth` to avoid `RecursionError`, (c) catches `(Exception, RecursionError, MemoryError)` not just `Exception` (`cases/correlation.py:206-219`). | Plan S5 requires existing bounds to hold on this path. | Fixed |
| **M4** | Removed redundant `owner_org_id` filter in both correlation functions — rely on `case_visible_q(user)` alone (includes own-org + shares). Updated docstrings to clarify. Added test `test_shared_case_visible_in_similarity` proving shared cases appear in similarity results. | Plan §4 wording was ambiguous; ADR-004 sharing model requires shared cases to be visible in correlation. | Fixed |

**New finding (F1), out of scope for this plan:** `ui_tags.sev_class` ↔ `app.css` mismatch unstyles severity wherever the filter is used (`_tab_alerts.html:25`). Fix candidates: add `sev-low/medium/high/critical` to `app.css`, or make `sev_class` return the numeric suffix. Needs its own TODO row — **not** folded into this wave.

---

## 14. References

* `TODO.md` §12.3, §10.7, §12.1 (list conventions), §12.4 (deferred bulk UI).
* AGENTS.md §2 Modules B + C, §3, §5, §6.
* ADR-002 (TheHive compatibility + audit), ADR-004 (tenant isolation), ADR-006 (roles).
* Precedents: `alerts/escalation.py`, `cases/update.py:280` (audit idiom), `observables/extractor.py:256`
  (pure extractor), `PLAN-2026-10-18-ui-pagination.md` (`_list_summary.html`, `RAIL_CAP`, empty states).
