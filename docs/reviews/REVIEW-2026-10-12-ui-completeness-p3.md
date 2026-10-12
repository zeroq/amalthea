# Security Review — UI completeness wave, Plan P3 (Procedures/TTP UI, A5)

**Date:** 2026-10-12
**Auditor:** security-auditor (read-only on product code; this report is the only artefact written)
**Scope:** the uncommitted P3 surfaces — `ui/views.py::case_procedure_create` and
`ui/views.py::case_procedures_create` (single + bulk procedure attach), the new case-detail
“Procedures” card (`ui/templates/ui/case_detail.html` + the new `ui/templates/ui/_procedure_entry.html`),
the route de-duplication in `ui/urls.py`, and the CSS in `ui/static/ui/app.css`. Cross-cut checks against
`identity/scoping.py::resolve_case`, `cases/procedures.py::create_procedure`,
`compat/procedures.py::parse_procedure`, `cases/views.py::_resolve_ttp_value`, and the P5/D3 Markdown
boundary (`ui/markdown.py`, `ui_tags.markdown`) because the new card renders a description through it.
**Plan / ADRs:** `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P3 + §9 (Security);
`docs/decisions/ADR-003-frontend-assets.md`, `docs/decisions/ADR-005-markdown-rendering.md`.
**Baseline:** working tree vs `8f38292` (8 tracked files changed, +256/−29; 3 untracked files: the new
partial + two conformance suites, 750 lines total). Everything reviewed is uncommitted on `main`.
**Method:** `git diff` review of every changed/added file; traced `resolve_case(write=True)` and the
write-share predicate; adversarial reading of the bulk item loop for bounds/amplification; verified every
`|markdown` call site is element content, not an attribute; catalogued flash-message interpolation;
confirmed CSP `script-src/style-src 'self'` and scanned templates for inline assets; ran the shipped
conformance suites; secret scan; `pip-audit`.
**Gate observed:** `.venv/bin/pytest tests/conformance -q` → **1517 passed / 3 skipped**.
`.venv/bin/pytest tests/conformance/test_ui_procedures.py tests/conformance/test_ui_css_tokens.py tests/conformance/test_ui_inline_assets.py -q` → **157 passed**.
`scripts/secret-scan.sh --files` over all 11 changed/untracked paths → **clean (11/11)**.
`pip-audit -r requirements/base.txt` → **No known vulnerabilities found**.

---

## Verdict

**CLEAR FOR SIGN-OFF WITH ONE POLICY ITEM AND THREE LOW-SEVERITY FOLLOW-UPS.**

No Critical and no High finding. The two new mutation views are correctly object-scoped: both call
`resolve_case(request.user, case_id, write=True)` (`ui/views.py:2055,2105`), so a cross-org caller gets the
uniform 404 (`resolve_case` returns `None`), a read-only *share* is refused, and a write-share is allowed —
exactly the §9 write-share-vs-read-share contract, and the predicate is already unit-tested in
`test_tenant_scoping.py` (`TestResolveCase.test_shared_read_resolves_for_get` /
`test_shared_write_resolves_for_mutate`, `:215-239`). `@login_required` sits **outside** `@require_POST`
on both views, so an anonymous request redirects to login and an authenticated `GET` is 405; CSRF is
enforced by `CsrfViewMiddleware` with no exemption. The one new rich-content sink
(`{{ procedure.description|markdown }}`, `_procedure_entry.html:16`) is element content under the
ADR-005 `nh3` allow-list, and every `|markdown` call site in the tree was re-confirmed to be element
content — never an attribute — so the literal `"` markdown-it leaves behind cannot break out. The new
template and CSS introduce no inline script/style/handler (CSP stays green) and no external `url()`/`@import`.
`case_procedures_create` reuses `parse_procedure`/`create_procedure` and a per-item savepoint; no raw SQL
was added.

The residual risk is the *same* systemic `read-only` role gap surfaced by P5 (M1), now widened by two more
point-and-click mutation surfaces, plus an audit-trail gap and a defence-in-depth bound on the bulk loop.
None is cross-tenant, XSS, CSRF, injection or secret exposure.

---

## Findings (severity-ranked)

| ID | Sev | Area | File:line | Introduced by this batch? |
|----|-----|------|-----------|---------------------------|
| M1 | **MEDIUM** | No role gate: a `read-only` user in the owning org (or a write-share org) can attach procedures | `ui/views.py:2044-2046, 2093-2095`; `identity/scoping.py:133-141` | No (pre-existing systemic — carried from P5 M1; two new surfaces) |
| L1 | LOW | Procedure attach writes no `AuditLog` and no case-ledger event | `ui/views.py:2081-2090, 2182-2196`; `cases/procedures.py:34-43` | Yes (P3 mutation) |
| L2 | LOW | Bulk view has no app-level item cap; relies on Django `DATA_UPLOAD_MAX_NUMBER_FIELDS` and is O(n²) in list-style mode | `ui/views.py:2114-2148` | Yes (P3 bulk view) |
| L3 | LOW | “Attach procedure” form is rendered to read-share viewers who then get a 404; bulk CSRF not explicitly tested | `ui/templates/ui/case_detail.html:128`; `tests/conformance/test_ui_procedures.py` | Yes (P3) |
| I1 | Info | Pre-existing `messages.error(..., str(exc))` / reflected-API-body flashes (not this batch) | `ui/views.py:367,985,1167,1228,1579,1959,1990` | No |
| I2 | Info | No rate limiting on the new mutation views (consistent with P2) | `ui/views.py:2044,2093` | Yes (P3), accepted parity |
| I3 | Info | `ttps=TTP.objects.order_by("name")` exposes the global ATT&CK vocabulary to any case viewer | `ui/views.py:441`; `cases/models.py:429-449` | Yes (P3) — intended |
| I4 | Info | Test-count drift: batch docs say 20 cases, suite collects 21 | `COMPLETED.md`; `docs/spec/deviations.md`; `tests/conformance/test_ui_procedures.py` | Yes (P3) — docs only |

---

### M1 — MEDIUM: the two new mutation views carry no role gate, so a `read-only` user can attach procedures

**Where.** Both views are gated only by `@login_required` + `@require_POST`
(`ui/views.py:2044-2045`, `:2093-2094`) and authorise with
`resolve_case(request.user, case_id, write=True)` (`:2055`, `:2105`). `resolve_case` grants an **owner-org
member** full write access regardless of role (`identity/scoping.py:133-135`) and a shared org access when
`permissions.write` is true (`:137-141`). There is no `role` predicate anywhere in the call chain — unlike
the playbook views, which deny `read-only` with a 404.

**Repro (by inspection; the class is confirmed for P2/P5).** As a user with `role="read-only"` in the org
that owns the case, `POST /ui/cases/<number>/procedure` with a valid `patternId` →
`ui/views.py:2055` resolves the case, `parse_procedure` succeeds, `create_procedure` inserts the row, and the
view returns 302. The same holds for `/ui/cases/<number>/procedures`. The owning UI can be reached because
`case_detail` renders the form unconditionally (`case_detail.html:128`).

**Impact.** A principal explicitly labelled “read-only” can silently alter the case's forensic record
(attach arbitrary ATT&CK techniques/TTPs). This is a *privilege/intent* mismatch, not a tenant-isolation
break: org scoping still holds, there is no cross-tenant read/write, and the blast radius is smaller than
P5 M1 (procedures are append-only in the UI — no delete path — and cannot destroy observables). It does
contradict the role name and the playbook precedent, and each wave adds surface.

**Recommendation.** Decide the `read-only` policy once, then enforce it in one place — as recommended in
P5 M1 and in the `REVIEW-2026-10-11-p2-ui-mutations.md` re-audit. Either:
- add an `IsAnalystOrAbove`-equivalent check to the four P2 views **and** these two P3 views (uniform 404
  for `read-only`), plus the matching DRF permission class; or
- document in `docs/spec/identity-auth.md` that `read-only` is not a mutation boundary, and stop treating
  the name as a control.

Do not special-case P3; this is a cross-cutting policy decision.

---

### L1 — LOW: attaching a procedure is a case mutation with no audit trail

**Where.** `case_procedure_create` runs `create_procedure` inside `transaction.atomic()`
(`ui/views.py:2081-2082`) and the bulk path does the same per item (`:2186-2187`), but neither writes an
`AuditLog` row nor appends a case ledger event. `create_procedure` (`cases/procedures.py:34-43`) is a bare
`Procedure.objects.create`, and the API equivalents (`cases/views.py:2246-2281`) do not audit either.

**Context.** Plan §9's explicit audit list is “case edit, observable delete, token create/revoke, and
superAdmin cross-org reads” — procedure attach is **not** named, so this batch does not violate a stated
requirement and it matches API parity. It is nevertheless a case-linked forensic mutation that the UI now
makes point-and-click, and every analogous P2 UI mutation (`case_edit`, `observable_edit/delete`,
`alert_status`) writes an `AuditLog` in the same transaction (P2 L2 fix). The P2/P5 reviews treated the
“security-relevant action is always audited” invariant as a LOW-level concern.

**Recommendation.** Either emit an `AuditLog` (`action="procedure_attach"`, target the case, actor = user)
inside the same `transaction.atomic()` as the insert — copying the `observable_delete` pattern — and
optionally append a ledger event, or record the omission as an accepted deviation with rationale.

---

### L2 — LOW: the bulk item loop is bounded only by Django's field-count default (and is O(n²) in list mode)

**Where.** `ui/views.py:2114-2128` builds `item_indices` as the set of integer suffixes on `patternId_*`
keys; the loop then iterates `sorted(item_indices)` (`:2139`). Because it iterates the *set* of submitted
indices (not `range(max_index)`), a single `patternId_999999999` yields **one** iteration — there is no
`header_count`-style amplification (cf. P5 M2). The only bound on the set size is
`DATA_UPLOAD_MAX_NUMBER_FIELDS`, which this project does **not** override, so Django's default of **1000**
applies and the request body is additionally capped by `DATA_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB default).

`get_field` (`:2141-2148`) is O(1) for indexed fields, but each list-style lookup calls
`post.getlist(field_name)` (`:2145`) — O(n) — and it is called six times per item, so a list-style POST of
n repeated `patternId` values is O(6·n²). With n ≤ ~1000 the work is trivial (~6×10⁶ comparisons); measured
in-process a 1000-item list request is well under a tenth of a second. The `idx` value is interpolated into
flash messages (`:2170,2178,2193`), so a crafted index can render a large number in a message — cosmetic.

**Impact.** No currently exploitable DoS. The risk is that the only guard is an external framework default:
a deployment that raises or disables `DATA_UPLOAD_MAX_NUMBER_FIELDS` (a common move to permit large forms)
would expose an unbounded authenticated write loop.

**Recommendation (defence in depth).** Cap `len(item_indices)` explicitly (e.g. reject > 200 with a form
message, as P5 M2 did for `MAX_HEADERS`), and hoist the `post.getlist(field_name)` results out of the loop
to restore O(n). Add a regression test posting an over-cap batch and asserting the rejection.

---

### L3 — LOW: the attach form is shown to read-share viewers (then 404s), and the bulk CSRF path is untested

`case_detail` reads with `write=False` (`ui/views.py:419`), so a read-only *share* holder reaches the page
and sees the “Attach procedure / TTP” form (`case_detail.html:128`); a submit then 404s at
`resolve_case(..., write=True)`. No data is disclosed and access is correctly denied — it is an affordance
bug shared with the other unconditional forms on the page (case status, extract, task, upload). Relatedly,
`tests/conformance/test_ui_procedures.py` asserts CSRF for the **single** view (`test_procedure_create_requires_csrf`)
but not for the bulk view; the bulk view is CSRF-protected by middleware (no `csrf_exempt`), so this is a
coverage gap, not a control gap. Consider hiding the form unless the caller has write access (a `can_write`
context flag), and add a bulk CSRF assertion.

---

### Info / notes

- **I1 — pre-existing leaky flash messages.** `ui/views.py` still interpolates exception text into analyst
  messages in seven places outside this batch: `:367` and `:985` (`str(exc)` from `ValueError`),
  `:1167`/`:1228` (case-template parse `ValueError`), `:1579` (`CaseUpdateError.message`), `:1959`
  (merge `ValueError`), and `:1990` which reflects up to 200 bytes of an internal API error body. These are
  **not** introduced by P3 and are unchanged by it; the P3 code itself deliberately logs the exception and
  flashes a generic string (`:2083-2087`, `:2185-2201`). Worth a future sweep to the P3 pattern.
- **I2 — no rate limiting.** Neither new view throttles (consistent with the P2 mutations). Authenticated
  audit/ledger spam only; accepted for MVP.
- **I3 — global TTP vocabulary.** `ttps=TTP.objects.order_by("name")` (`ui/views.py:441`) is unfiltered, but
  `TTP` has no `owner_org` (`cases/models.py:429-449`) — it is shared reference data, and the API exposes
  the same list. Not a tenant leak.
- **I4 — documentation drift.** `COMPLETED.md` and `docs/spec/deviations.md` say the P3 suite has 20 cases;
  `pytest --collect-only` reports **21**. The plan §8 text says 21. Docs only; correct the counts.

---

## Cleared / tested attack classes

- **Cross-org tenant scoping — PASS.** Both views resolve via `resolve_case(..., write=True)` and return the
  uniform 404 `ui/404.html`; `test_ui_procedures.py::test_procedure_create_cross_org_returns_404` and
  `::test_procedures_bulk_create_cross_org_returns_404` confirm, with no row created. `resolve_case` is the
  single canonical helper (`identity/scoping.py:103-141`) and its read-share-vs-write-share behaviour is
  unit-tested (`test_tenant_scoping.py:215-239`).
- **Anonymous / method handling — PASS.** `@login_required` outside `@require_POST` ⇒ anonymous POST →
  login redirect (`::test_procedure_create_anonymous_redirects_to_login`), authenticated GET → 405
  (`::test_procedure_create_post_only_no_get`, `::test_procedures_bulk_create_post_only_no_get`).
- **CSRF — PASS.** `CsrfViewMiddleware` enabled (`amalthea/settings/base.py:51`); no `csrf_exempt`; every
  form emits `{% csrf_token %}`; the single-view test posts without a token → 403.
- **Stored XSS via procedure description — PASS.** `{{ procedure.description|markdown }}` is element
  content (`_procedure_entry.html:16`), rendered by `nh3` against an 18-tag allow-list with raw HTML
  escaped (`ui/markdown.py`). The test posts `<img onerror>`/`<script>` and asserts the payload survives
  only as `&lt;…&gt;` and that no `on*=` attribute exists in the response.
- **Every `|markdown` use is element content — PASS.** Three call sites exist —
  `_procedure_entry.html:16`, `_timeline_entry.html:6`, `case_detail.html:59` — all `{{ … }}` inside
  element text, none inside an attribute. Markdown-it's literal `"` output therefore cannot break out.
- **Other template sinks — PASS.** `pattern_id`, `pattern_name`, `tactic`, `occur_date`, `ttp.ttp_code` and
  `ttp.name` are plain `{{ }}` (autoescaped); `<select>` option values are UUIDs. No `|safe`.
- **CSP / inline assets — PASS.** The new partial and card contain no `<script>`, `style=`, `on*=`,
  `json_script`; `test_ui_inline_assets.py` stays green (157 passed). The CSS diff carries no `@import` and
  no `url()` (the only `http` reference is the Tailwind licence comment). Prod CSP is
  `script-src/style-src/font-src 'self'` with no `unsafe-inline` (`amalthea/settings/base.py:60-78`).
- **Injection — PASS.** All reads/writes go through the ORM; `_resolve_ttp_value` uses
  `TTP.objects.filter(pk=…)`/`.filter(name=…)` (`cases/views.py:2221-2228`), no raw SQL, no string
  interpolation into queries.
- **Exception / info disclosure in the new code — PASS.** `parse_procedure` error pairs are static
  (`compat/procedures.py:33,39,45`), so `f"Invalid {field}: {message}"` cannot echo input; DB exceptions
  are logged with only `case_id`/`index` and a generic flash is shown (`ui/views.py:2084-2087,
  2188-2201`). No procedure description or PII is logged.
- **Bulk input handling — PASS (bounded today).** Index set is iterated, not `range(max)`; the field count
  is capped by Django's default 1000; body size by 2.5 MB. See L2 for the defence-in-depth note.
- **Secrets — PASS.** `scripts/secret-scan.sh --files` clean over all 11 changed/untracked paths; no new
  credentials, tokens, or `.env` content.
- **Route de-duplication — PASS.** The duplicate block removed from `ui/urls.py` left exactly one
  definition of each of `ui-case-procedure-create`, `ui-case-procedures-create`, `ui-case-template-list`
  and `ui-case-template-create`; both new routes remain registered and listed in `_UI_WRITE_ONLY_ROUTES`
  (`test_tenant_scoping.py:851-852`) for read-conformance. `test_ui_urls_smoke.py` is green.
- **Dependency advisories — PASS.** `pip-audit -r requirements/base.txt` → no known vulnerabilities.

---

## Summary of remediation priority

1. **M1** — decide and enforce (or explicitly document) the `read-only` role policy across P2/P3/API; one
   decision, one predicate.
2. **L2** — add an explicit item cap to `case_procedures_create` (and de-duplicate the `getlist` work).
3. **L1** — either audit procedure attach or record the omission as accepted.
4. **L3** — hide the attach form from read-share viewers; add a bulk CSRF test.
5. **I1** — optional sweep of the seven pre-existing exception-into-flash sites.

**Verdict: the P3 feature itself is sound — correctly scoped, CSRF-protected, CSP-clean, and XSS-safe.
No blocker for shipping; clear once M1 is decided (tracked from P5) and the LOW follow-ups are either fixed
or explicitly accepted.**

---

## Post-review resolutions (2026-10-12, orchestrator)

Recorded after the review above was written; the code changed in the same batch (uncommitted at review time).

| ID | Status | Resolution |
|----|--------|-----------|
| **M1** | **Accepted → TODO §11.1 + §11.4** | Policy decision, not a P3 defect: the same systemic `read-only` gap as P5 M1, now with two more surfaces. Enforcing it per-wave would fork the policy across three places; it is fixed once via a `requires_role("analyst")` predicate applied to both the UI and the compat API. Recorded with the two new line pointers. |
| **L1** | **Accepted → TODO §11.4** | Procedure attach audits nothing, single or bulk. The plan's §9 does not name procedures, and the compat API behaves identically, so this is an "audit or explicitly accept" decision — recorded, not silently ignored. |
| **L2** | **FIXED in this batch** | `MAX_BULK_PROCEDURES = 100` now rejects an oversized batch before any insert (with a flash), and the repeated-field `getlist` work is hoisted out of the item loop (O(n²) → O(n)). Test: `test_procedures_bulk_create_rejects_more_than_the_cap`. |
| **L3** | **Accepted → TODO §11.4** (CSRF half covered) | Hiding the form from read-share viewers needs a write-share *predicate*, and `identity/scoping.py` has none that does not query — a second `resolve_case(write=True)` per page render. It is the same visibility question as M1 and is decided with it. The bulk route's CSRF is covered by the existing `@login_required` + CSRF middleware plus the single-route `enforce_csrf_checks` test; an explicit bulk CSRF test is trivially added with the M1 work. |
| **I1** | **Recorded → TODO §11.4** | Seven pre-existing exception-into-flash sites listed for a single mechanical sweep; the P3 code already logs and flashes generically. |
| **I2 / I3** | **Accepted** | Rate-limit parity with P2 (tracked with the §S items); the global TTP vocabulary is shared reference data by design. |
| **I4** | **FIXED** | Doc counts corrected to 22 cases / 1538 passed (`COMPLETED.md`, `docs/spec/deviations.md`, plan P3 status). |

**Post-resolution gate:** `make check` → **1538 passed / 3 skipped** (ruff + format + mypy 105 files +
migrations + css-clean + full pytest).
