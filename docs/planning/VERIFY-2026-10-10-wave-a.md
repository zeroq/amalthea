# Verification Report: Wave A remediation (B1 / B2 / B3 / docs / mypy)

- **Plan of record:** `docs/planning/VERIFY-2026-10-10-t2-and-phase12.md` → §"Wave A remediation log (2026-10-10)"
- **Verified:** 2026-10-10 07:57 UTC
- **Tree:** uncommitted working tree on top of `70327d7` (17 modified + 3 untracked files)
- **Method:** read-only. Every verdict cites `file:line`; gate re-run and full suite re-run independently below.

## Verdict table

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | **B1** — 3 missing UI routes added, dup `ui-case-export` gone, delete does a local row+storage delete; smoke test non-vacuous | **MET** | `ui/urls.py:32-36` (`ui-case-tag-toggle`), `:46-50` (`ui-case-attachment-upload`), `:51-55` (`ui-case-attachment-delete`); exactly one `ui-case-export` remains (`ui/urls.py:30`, was 3×); `ui/views.py:1188-1206` deletes row then `default_storage.delete(path)` — no delegation to the DRF `@api_view`; test non-vacuous (see below) |
| 2 | **B2** — CSP real, single definition, legacy gone, test non-vacuous + passes | **MET** | `amalthea/settings/base.py:29` (`"csp"` in `INSTALLED_APPS`), `:46` (`csp.middleware.CSPMiddleware`), `:65` (`CONTENT_SECURITY_POLICY`, single definition); `prod.py` inherits via `from .base import *` (`prod.py:9`) and has **no** `CSP_*` (`rg '^CSP_' amalthea/settings/prod.py` → none); `AMALTHEA_CSP` gone from `.env.example`; `django-csp` 4.0 installed; test at `test_prod_settings.py:177` **passes** and is non-vacuous (see below) |
| 3 | **B3** — `_bulk` applies the shared guard + 409 pre-flight unless `?force=true`; single-item path unchanged; test exercises a 2-case observable | **MET** | `cases/views.py:877` `_cross_case_error`, `:893` `_observable_cross_case_guard`, `:944/:950` single-item DELETE/PATCH both route through it, `:1821` `_bulk_cross_case_guard`, `:1850-1865` `observable_bulk_update` pre-flights before `bulk_patch`; `test_observable_bulk_guard.py:45` (409 + affected cases), `:70` (force → 200), `:95` (single-case needs no force), `:114` (mixed batch aborts before writing) |
| 4 | **Mypy gate** — pinned `mypy` green; fixes type-only / behaviour-preserving | **MET** | `.venv/bin/mypy` (no path args) → `Success: no issues found in 100 source files`; at `HEAD` (via `git worktree`) the same command → **`Found 8 errors in 3 files`** exactly matching the log (`realtime/views.py:13`, `identity/ratelimit.py:22,23,36,44,78,78`, `core/serializers.py:311`); fixes reviewed (see notes) |
| 5 | **Docs hygiene** — TODO dedup + priority table, COMPLETED dedup + truthful count, plan header "partially shipped", spec Evidence refreshed | **DEVIATED** (mostly met) | `TODO.md:117,118` — one `6.6`, one `6.7` (**fixed**); `TODO.md:19-23` priority table rewritten (**fixed**); `COMPLETED.md:850` — the 3 duplicate realtime headings/stubs gone (**fixed**); `COMPLETED.md:918` "Known Pre-existing Failure (**1**…)" (**truthful**); `COMPLETED.md:906` "1003 / 3 / 1" (**matches my run**); plan header `PLAN-2026-10-09-usability-orchestration.md:4` "Partially shipped" (**fixed**); `api.md:356-366` + `data-model.md:241-250` Evidence refreshed and every count I sampled is correct. **BUT** two `TODO.md` contradictions the same finding listed are still open — see Deviations D1/D2 |
| 6 | `ui/views.py::case_merge` is orphaned/broken (flagged, deliberately not fixed) | **MET (confirmed)** | `ui/views.py:1120` `from alerts.escalation import merge_cases` — `alerts/escalation.py` defines no `merge_cases` (only `merge_alert_into_case`, `:122`); `:1131` renders `ui/case_merge.html` — no such file (`ls ui/templates/ui | rg merge` → empty); `:1117` `@require_GET` with a POST branch at `:1133-1146`; `rg 'ui-case-merge' ui/templates tests` → **0 hits** |

**Score: 5 MET · 1 DEVIATED · 0 NOT MET.**

## Gate output (independently run, exactly as `Makefile:32-40`)

```
── ruff check            All checks passed!
── ruff format           (silent, exit 0)
── mypy (no path args)   Success: no issues found in 100 source files
── django check          System check identified no issues (0 silenced).
── migrations in sync    No changes detected
── css compile check     ✓ Tailwind input compiles (compile check only — this does NOT detect drift)
── pytest                1 failed, 1003 passed, 3 skipped in 50.64s
make: *** [check] Error 1        <- only because of the pre-existing SQLite failure below
```

Targeted modules (`test_ui_urls_smoke test_observable_bulk_guard test_prod_settings test_ui_loop
test_t1_surface test_realtime test_ledger_keyset test_t2_p2_surface test_t2_p4_surface
test_t2_p5_surface`): **143 passed, 0 failed.**

Everything in `make check` before `pytest` is green. The single failure is the documented
SQLite-only `test_zzz_probe.py::test_z_delete_the_seed_row`.

## Full-suite reproduction

```
$ DJANGO_SETTINGS_MODULE=amalthea.settings.test .venv/bin/python -m pytest -q
1 failed, 1003 passed, 3 skipped in 50.92s
FAILED tests/conformance/test_zzz_probe.py::test_z_delete_the_seed_row
  django.db.utils.OperationalError: no such table: case_record
```

- **Passes in isolation:** `.venv/bin/python -m pytest -q tests/conformance/test_zzz_probe.py` → **2 passed**. So it is full-suite *ordering* pollution, exactly as `COMPLETED.md:920-923` now says.
- `test_seed_migrations.py` → **21 passed** in isolation and did **not** fail in the full run (the old "3 pre-existing failures" count was indeed wrong).
- **Not caused by Wave A:** `git diff --stat HEAD -- tests/conformance/test_zzz_probe.py tests/conformance/test_seed_migrations.py` is empty (neither file touched), and the *same* failure is recorded at `70327d7` with a clean tree in `VERIFY-2026-10-10-t2-and-phase12.md:13-15`.

## Non-vacuity checks (adversarial)

### B1 smoke test — non-vacuous, with one scoping gap
- `test_ui_urls_smoke.py:81` scans template *source* for `{% url 'ui-…' %}` and asserts every name is in `get_resolver().reverse_dict`. It asserts the scan itself found names (`:83`), so it cannot pass empty. The three B1 names are all referenced literally: `case_tags.html:34`, `case_attachments.html:13`, `case_attachments.html:46`. **Remove any one route → this test fails.**
- `test_ui_urls_smoke.py:119` does a real `browser.get(url)` and asserts `200/302` (`:133`), with an explicit anti-vacuity list at `:138-139` requiring `ui-case-tags`, `ui-case-attachment-list`, `ui-case-detail` to have been exercised. Note: `{% url 'ui-case-tag-toggle' %}` sits inside `{% if case.tags.all %}` (`case_tags.html:31-40`), so a data-less GET would *not* exercise it — but tests `:145`, `:156`, `:166` `reverse()` the three new routes directly and POST them, asserting DB/storage effects (`:150,:153,:163,:183,:184`). No vacuity.
- **Gap (Low):** test 1 scans **templates only**. Two names referenced *only* from Python — `ui-case-apply-template` (`ui/views.py:863`) and `ui-case-merge` (`ui/views.py:1137,1146`) — are outside the reverse sweep. If `ui-case-apply-template` were deleted from `ui/urls.py`, nothing would catch it. Python `redirect/reverse` literals should be scanned too.

### B2 CSP test — non-vacuous
- `test_prod_settings.py:177` does `Client().get("/healthz")`, asserts `200`, then reads `response.headers["Content-Security-Policy"]` (**KeyError** if absent) and asserts `default-src 'self'` + `object-src 'none'`.
- I confirmed the header is really emitted: `Client().get('/healthz')` →
  `base-uri 'self'; default-src 'self'; font-src 'self'; form-action 'self'; connect-src 'self'; frame-ancestors 'none'; style-src 'self'; script-src 'self'; object-src 'none'; img-src 'self' data:`
- I confirmed failure mode: `override_settings(CONTENT_SECURITY_POLICY={})` → header **None** → test fails.
- Passing: `pytest tests/conformance/test_prod_settings.py::test_the_strict_csp_is_emitted_on_a_real_response` → **1 passed**.
- Pre-condition verified: at `HEAD` `csp.middleware.CSPMiddleware` was already in `MIDDLEWARE` but `csp` was **not** in `INSTALLED_APPS` and there was no `CONTENT_SECURITY_POLICY` — i.e. the middleware ran with an empty policy → no header. That is exactly the "no-op" B2 describes.

### B3 guard test — non-vacuous
- `test_observable_bulk_guard.py:52-53` links the *same* `Observable` to `case1` **and** `case2`; `:61` asserts 409; `:64` asserts both case numbers are listed; `:67` asserts `observable.message == ""` (row untouched). `:80-92` re-runs with `?force=true` → 200 + mutated. `:114-137` proves pre-flight (the *unshared* sibling is also unwritten).
- Single-item path still guarded: `cases/views.py:944` (DELETE) and `:950` (PATCH) both call `_observable_cross_case_guard`; pinned by `test_t1_surface.py:604` (delete), `:648` (patch), `:690` (single-case, no force) — all green in the targeted run.

## Deviations (Wave A claims vs tree)

- **D1 — `TODO.md` "Next Recommended Items" table was not deduped.** `TODO.md:159-169` still has duplicate ranks (two `**5**` at 165/166, two `**6**` at 167/168), `6.3` three times (163, 166, 168) and `6.2` twice (164, 169). The Wave A log only says "deduped `TODO.md` §6 / refreshed priority table", so the claim as *written* is accurate — but the original finding's item 3 (this table) is unremediated. **Severity: Low.**
- **D2 — `TODO.md` still self-contradicts the plan status.** Trailing line `TODO.md:173`: *"All planned Phase 12 items … are **SHIPPED**"* directly contradicts `PLAN-2026-10-09-usability-orchestration.md:4` ("Partially shipped … 10 NOT MET"). `TODO.md:4` also still uses "§6.11" for three different things (keyboard cheatsheet, login rate-limiting, `SESSION_COOKIE_HTTPONLY`). **Severity: Low (doc), but it is the exact class of drift Wave A set out to remove.**
- **D3 — duplicate routes only partly removed.** `ui-case-export` went 3× → 1× (claim met), but `ui-case-template-list` (`ui/urls.py:77` and `:99`) and `ui-case-procedure-create`/`-s-` (`:66-75` and `:101-110`) are still declared twice. Harmless (the duplicates are byte-identical patterns), so no behaviour impact. **Severity: Low.**
- **D4 — stale file counts in the gate echo / summary.** `Makefile:35` prints "73 files", `COMPLETED.md:909` says "88 source files", `pyproject.toml:93` says "95" — mypy reports **100** (and `git ls-files` over the pinned scope, migrations excluded, is 100). **Severity: Low.**

## New findings (not in the Wave A log)

- **N1 (Medium) — the new UI delete lacks the org/share guard the API it replaced has.** `ui/views.py:1199` resolves the case with `_resolve_case` (`ui/views.py:65-74`, a bare `get_object_or_404(Case, …)`) and never calls `_case_access`; the API equivalent `cases/views.py:1752-1758` does `_case_access(..., write=True)`. Before Wave A this route answered the form's POST with **405** (inert); now it performs a real delete. So for *this* endpoint Wave A converted a no-op into an unscoped destructive action. It is **the same class already flagged as S1/S2** (`rg -c '_case_access' ui/views.py` → **0** across all 17 `_resolve_case` call sites, e.g. `case_comment`/`case_set_status` mutate the same way), so it is not a new hole in the UI's overall posture — but `ui/views.py` must be in Wave B's `_case_access` sweep, and it should be listed explicitly. Not a Wave A blocker (B1's brief was "route the existing view"; the guard question is Wave B).
- **N2 (Low) — 6 UI routes have no entry point anywhere.** `ui-case-export`, `ui-case-bulk`, `ui-taxonomy`, `ui-case-procedure-create`, `ui-case-procedures-create`, `ui-case-attachment-detail` are defined (`ui/urls.py`) but referenced by **zero** templates and zero `redirect/reverse` calls (`rg` over `*.html` + `*.py` → no hits). They are reachable only by typing the URL. Pre-existing, out of Wave A scope; still undercuts AC6.12-P4-a ("reachable in UI").
- **N3 (Low) — smoke-test coverage of Python-only routes.** See the B1 gap above: `ui-case-apply-template` / `ui-case-merge` are not reverse-checked.

## Positive: the adversarial sweep came back clean

I enumerated every literal `ui-*` name referenced in **any** `*.html` (34) or **any** Python
`redirect(...)`/`reverse(...)` (30) in the repo (union = 38) and resolved each against the live
resolver: **referenced but undefined → `[]`**. There is **no other `NoReverseMatch` time bomb**
like the three B1 ones. (6 defined-but-unreferenced routes = N2 above.)

## Gate-fix review (type-only?)

- `realtime/views.py:13` — added `: HttpRequest` annotation. Pure annotation. ✔
- `core/serializers.py:309-321` — `timeline_events` → `timeline_items` and the slice folded into one
  expression. `rg 'timeline_events' core/serializers.py` → **0 leftovers**, so no stale reference
  shadows the rename; `list(events_after(...)[:limit+1])`, `has_more`, cursor and payload are
  semantically identical to the diff's before-side. ✔
- `identity/ratelimit.py:22-23` — `str(...)` around `request.META.get(...)` (always `str` at
  runtime). `:36,44` — `int(cache.get(key, 0))`; values are only ever written as ints by
  `_increment_attempts`, so `int()` is an identity. `:78` — `int(cast("Any", cache).ttl(key))`;
  `cast` is erased at runtime and the `except AttributeError` fallback for `LocMemCache`
  (`:80-83`) is untouched. Only theoretical edge: a backend whose `ttl()` returns a non-int would
  now raise `TypeError` instead of returning `None` — no such backend in the pinned stack.
  ✔ behaviour-preserving.

## Blockers

**None for Wave A.**

## Recommendation

**Wave A: READY TO COMMIT — yes.** Gate is green for lint/types/check/migrations/css; the full
suite's only failure is the pre-existing, order-dependent SQLite probe that passes in isolation and
was already failing at `70327d7`. Non-blocking follow-ups: fold N1 (`ui/views.py` → `_case_access`)
explicitly into Wave B's S1/S2 scope; D1/D2 (the two remaining `TODO.md` contradictions) in the next
docs pass; D3/D4 cosmetic.
