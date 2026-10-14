# Verification Report: Viewer role enforcement (make `read-only` actually read-only)

Plan: `docs/planning/PLAN-2026-10-12-viewer-role-enforcement.md`
ADR: `docs/decisions/ADR-006-role-semantics.md`
Verified: 2026-10-12 15:12 UTC
Verifier: verification specialist (read-only)
Status: **Partial** — behaviour is implemented and the security guarantee holds; the plan's
declared test deliverables are incomplete (P5 AC1 / §9.4 / §9.5 / §9.7) and P6 bookkeeping is pending.

## Summary

- Total ACs evaluated: 16 (P1×3, P2×3, P3×4, P4×3, P5×3)
- Met: 14
- Not Met: 1 (P5 AC1)
- Partially met / Deviated: 1 (P2 AC3)
- §9 numbered items: 4 Met (1, 2, 3, 6), 3 Not Met/Partial (4, 5, 7)
- Independent test evidence: **1581 passed, 3 skipped** (full suite) · **40 passed** (targeted
  viewer suites) · `make check` **green** · `makemigrations --check --dry-run` → **No changes detected**

## Summary table

| Phase | AC | Status | Evidence |
|---|---|---|---|
| P1 | AC1 `is_viewer` truth table | **Met** | `tests/test_roles.py` (14 tests): true for `viewer`; false for analyst/admin/superAdmin (`:36`), anonymous/None (`:41`), non-`User` impostor (`:51`). `identity/scoping.py:48-63`. |
| P1 | AC2 no import cycle, mypy clean | **Met** | `identity/permissions.py:10-19` TYPE_CHECKING + function-local `from identity.scoping import is_viewer` (`:94`); `is_viewer` imports `core.enums` locally (`identity/scoping.py:57`). `make check` mypy: "Success: no issues found in 105 source files". |
| P1 | AC3 ADR-006 published | **Met** | `docs/decisions/ADR-006-role-semantics.md` (98 lines) exists (untracked). Status still "Proposed" — P6 finalisation pending. |
| P2 | AC1 viewer → `None` on write resolution | **Met** | `identity/scoping.py:143-144` (case), `:178-179` (alert), `:206-207` (observable); proven by `tests/test_roles.py:61-133`. |
| P2 | AC2 all `write=True` sites are mutations | **Met** | 45 `write=True` sites; every one is inside a mutating view (`ui/views.py` ×25, `alerts/views.py:334`, `automation/views.py:284`, `cases/views.py` ×17). See deviation D2 re: API alert/observable call sites. |
| P2 | AC3 reads unaffected | **Partial** | Read paths pinned by `test_viewer_readonly_ui.py:157`, `test_viewer_readonly_api.py:91`, `test_viewer_affordances.py`. Dedicated `tests/test_viewer_reads.py` (§9.4) absent; attachment-list GET, case-export GET and WS-subscribe handshake not asserted for a viewer. |
| P3 | AC1 tests 2+3 green | **Met** | `test_viewer_readonly_ui.py` (7 tests) + `test_viewer_readonly_api.py` (7 tests); 40 targeted tests pass. UI route enumeration yields **61** routes (>55); API enumeration **126** routes (>60). |
| P3 | AC2 login/logout/password-change reachable | **Met** | `test_viewer_readonly_ui.py:164-168`; allow-list `{login, logout, ui-password-change}` (`:27`). |
| P3 | AC3 webhook + `AllowAny` unaffected | **Met** | `compat/views.py:67-70,139-142` keep `AllowAny`; `test_viewer_readonly_api.py:149` proves the class passes anonymous through; ingest webhook is non-DRF (`ingest/urls.py` mounted at `api/v1/`), full suite green incl. `test_webhook_hardening.py`. |
| P3 | AC4 `POST /api/v1/query` → 403 | **Met** | `test_viewer_readonly_api.py:98-101` asserts 403; documented in plan §4.3/§6 and ADR-006 decision 8. Deviation record still pending P6. |
| P4 | AC1 viewer sees no mutation controls | **Met** | `tests/conformance/test_viewer_affordances.py` (8 tests) covers case/alert/observable detail + playbook/tag/template lists, each paired with an analyst positive control. Settings pages are gated (`@not_viewer`) → 404, so no controls render. |
| P4 | AC2 aria/labels, no CSP violations | **Met** | Controls are *removed* (`{% if can_mutate %}` … `{% endif %}`) rather than shown-disabled, so the `aria-hidden`/labeling rule is moot; `test_ui_inline_assets.py`, `test_ui_icons.py`, `test_ui_css_tokens.py` green in the full suite. |
| P4 | AC3 keyboard shortcuts hide mutations | **Met** | `ui/static/ui/keys.js` contains only navigation shortcuts (g/a/c/o/r/p/s/t/m/x/,///? — lines 19-31); `base.html:159-173` cheatsheet has none; the settings shortcut is `superAdmin`-guarded (`base.html:169`). No mutation shortcut exists to hide. |
| P5 | AC1 migration forward/reverse tested | **Not Met** | `identity/migrations/0008_role_viewer_rename.py` is correct-by-inspection (drop CHECK → RunPython → AlterField → re-add CHECK; reverse is symmetric) and `makemigrations --check` is clean, but **no test exercises forward/reverse**, and **no test asserts `role="viewer"` is accepted while `role="read-only"` is now rejected** (plan §9.7). |
| P5 | AC2 no `"read-only"` role literal remains | **Met** | Grep of code: only the share literal `core/serializers.py:415` (intentional), historical migration `identity/migrations/0004_*`, the new data migration `0008_*`, and comments/docstrings. No role comparison uses the old literal. |
| P5 | AC3 `share_json` unchanged | **Met** | `core/serializers.py:415` is not in `git diff`; share vocabulary untouched. |
| §9.1 | `tests/test_roles.py` | **Met** | Present, 14 tests. |
| §9.2 | UI conformance invariant | **Met** | `tests/conformance/test_viewer_readonly_ui.py`. |
| §9.3 | API conformance invariant | **Met** | `tests/conformance/test_viewer_readonly_api.py`. |
| §9.4 | `tests/test_viewer_reads.py` | **Not Met / Partial** | File absent. Read coverage folded into the three viewer suites; missing attachment-list, export, WS handshake. |
| §9.5 | `tests/conformance/test_viewer_precedence.py` | **Not Met / Partial** | File absent. Role-beats-write-share is covered (`tests/test_roles.py:74`); viewer-cannot-grant-share is only covered generically by the UI sweep (`ui-case-share-list`/`ui-case-share-revoke`). |
| §9.6 | Regression suites green | **Met** | Full suite 1581 passed / 3 skipped; `make check` green (ruff, format, mypy, django check, migrations in sync, css, pytest). |
| §9.7 | Migration forward/reverse + constraint authn test | **Not Met** | No test references migration `0008` or the rename. `test_enum_contracts.py:333` only proves the constraint rejects `invalid/superuser/""`, not the rename boundary. |

## Deviations

### D1 — P5 AC1 / §9.7: migration forward/reverse and constraint-boundary test missing (Not Met)
The migration itself is sound:
`identity/migrations/0008_role_viewer_rename.py:31-57` orders operations as
`RemoveConstraint → RunPython(rename_to_viewer, rename_to_read_only) → AlterField → AddConstraint`.
Because Django reverses operations in reverse order, the reverse correctly drops the new CHECK,
rewrites `viewer → read-only`, restores the old `AlterField`, then re-adds the old CHECK. No test
executes either direction, and nothing asserts that `role="viewer"` satisfies and
`role="read-only"` violates the regenerated `identity_user_role_valid`. Impact: the migration is
unverified against the constraint boundary; a typo in the value tuple would ship silently. This is
the one AC that is genuinely **Not Met**.

### D2 — P2 §4.2: API alert/observable mutation sites do not pass `write=True` (Deviation, Low)
The plan (§4.2) says to mirror the `write` flag onto `resolve_alert`/`resolve_observable` "then
pass `write=True` from their mutating call sites (UI + API)". Only the 5 UI call sites were updated
(`ui/views.py:392,419,1468` alerts; `:1697,1855` observables). The JSON API alert mutations resolve
without the flag — `alerts/views.py:331,345,457,472,621,646` call
`resolve_alert(...)`/`_resolve_alert_scoped(...)` (which delegates at `:450` with no `write`) — and
the observable API selects its own resolvers. **No security hole**: the global
`ViewerReadOnly` permission (Layer 2) refuses the unsafe verb before the view body runs, which is
exactly why the API sweep passes. The gap is only in defence-in-depth: Layer 1 is not exercised for
the API alert/observable paths. Recorded as a deviation from the plan's stated intent.

### D3 — Read over-block on gated GET+branch views (Deviation, Low)
`not_viewer` denies *all* methods, including GET, on the screens it decorates. For write-only
screens (`case_merge`, `case_apply_template`) this matches plan §4.2's deliberate consequence. But
`case_template_detail` (`ui/views.py:1231-1247`) is a **pure GET read view** ("View a case
template"), and the GET branch of `playbook_detail` (`:754-793`) and `tag_detail` (`:1367-1379`)
renders readable content; all three now return 404 to a viewer. This is broader than plan §7's
"Read routes unchanged (200)". The affordance tests were written to expect the hidden detail links
(`test_viewer_affordances.py:211,229,247`), so the behaviour is intentional but is a deviation from
§7 and is not yet recorded in `docs/spec/deviations.md`.

### D4 — P6 bookkeeping pending (expected)
`TODO.md:302` §11.1 is still unchecked; `COMPLETED.md` has no entry; `docs/spec/deviations.md` has
no viewer/role-rename entry (so the `user_json.role` change and the `POST /api/v1/query` denial are
not yet recorded); ADR-006 status is "Proposed" and the plan status is "Draft". P6 is sequenced
"after verification", so this is expected, not a defect.

## Adversarial checks (explicit findings)

1. **"Every mutating UI view is gated" — confirmed.** Cross-referencing `ui/urls.py` (172 lines)
   against the 38 `@not_viewer` applications in `ui/views.py`: every POST-capable route is gated
   (`alert_escalate/merge/status`, `case_set_status/comment/extract/task-*/edit/tag-toggle/
   apply-template/merge/share-list/share-revoke/attachment-upload/delete/procedure(s)-create/
   template-create/edit/delete`, `tag-create/detail/delete`, `observable-edit/delete`,
   `playbook-create/detail/edit/delete/run`, all six `settings_*`). Every remaining `ui-` route is a
   read view carrying `@require_GET` (so a viewer POST gets 405). No named route outside the
   `ui-`/allow-list set is mutating. The sweep's allow-list (`login`, `logout`,
   `ui-password-change`) is self-service auth only.
2. **API sweep enumerates the whole URLconf — confirmed.** `_iter_api_routes` walks every
   registered `URLPattern` whose accumulated path starts with `api/` (126 routes), which includes
   `compat/urls.py` (login/logout/describe/**catch-all**, mounted at `api/v1/`). The catch-all
   `compat-catchall` is swept, not hidden. The allow-list is `{api-login, api-logout}` (plus their
   `-slash` spellings via `removesuffix`); no other endpoint's name reduces to those, so the
   allow-list cannot hide a real mutation endpoint.
3. **No `DEFAULT_PERMISSION_CLASSES` bypass — confirmed.** The only `@permission_classes` override
   in non-test code is `compat/views.py:70,142` (`AllowAny` on login/logout, intentional). Every
   other DRF endpoint (`cases`, `alerts`, `observables`, `automation`, `identity`, `query`) is
   `@api_view` with no override, so `identity.permissions.ViewerReadOnly`
   (`amalthea/settings/base.py:161`) applies globally. `automation/views.py` invokes
   `IsAnalystOrAbove` *inside* the view body (`:96,182,265`), which composes with — never replaces —
   the global list. Non-DRF mutations are the UI (gated) and the token-authenticated ingest webhook
   (a viewer cannot present an `IngestionToken`).
4. **Rename clean — confirmed.** No remaining `"read-only"` **role** literal in code;
   `makemigrations --check --dry-run` reports "No changes detected"; `identity/models.py:9` derives
   `role` from `USER_ROLE_CHOICES`, and `core/enums.py:117-128` now contains `viewer` +
   `VIEWER_ROLE`. `identity/admin.py` uses the field's model choices (no literal), so no edit was
   required.

## Blockers

None to the security guarantee. The following prevent a clean "done":

- **P5 AC1 / §9.7** — migration forward/reverse + constraint-boundary tests not written.
- **§9.4** — `tests/test_viewer_reads.py` not written (read coverage partial).
- **§9.5** — `tests/conformance/test_viewer_precedence.py` not written (precedence/grant coverage
  partial).
- **P6** — documentation/status bookkeeping (TODO §11.1, COMPLETED.md, `docs/spec/deviations.md`,
  ADR/plan status) not done.

## Recommendations (non-binding)

1. Add a migration test using `MigrationExecutor` that migrates to `0006` and back, asserting a
   `read-only` row becomes `viewer` (forward) and reverts (reverse), plus a constraint test that
   `"viewer"` is accepted and `"read-only"` is rejected. This closes P5 AC1 / §9.7.
2. Either create `tests/test_viewer_reads.py` (attachment-list GET, case-export GET, WS-subscribe
   handshake) and `tests/conformance/test_viewer_precedence.py` (viewer-with-write-share → 404/403
   end-to-end; viewer → 404 on `case_share_list` POST), or amend the plan to fold them explicitly
   into the existing suites and note the coverage map.
3. Record D2 and D3 in `docs/spec/deviations.md`, and decide whether `case_template_detail` should
   be readable (it is a pure read view) — if not, state the "authoring-only screens" rule in the
   ADR so §7 is not contradicted.
4. Complete P6 (status flips, deviation rows for `user_json.role` and `POST /api/v1/query`).

## Round 2 — 2026-10-12

All three items from the re-check are addressed. The conformance tests now execute (not just reason about) the boundary behaviour.

### 1) P5 AC1 / §9.7 — `tests/conformance/test_viewer_role_rename.py`
- **Migration forward and back executed**: `test_the_rename_migration_rewrites_and_reverses_existing_rows` uses `MigrationExecutor(connection).migrate([BEFORE])`, creates a historical `User` with `role="read-only"` (using the state-at-BEFORE model), then migrates to `RENAME` and back to `BEFORE`. It asserts `_role_of("legacy-viewer") == "viewer"` after forward and `== "read-only"` after rollback. On exit it calls `_migrate(_leaves())` to restore the schema graph leaves. (lines 66–97)
- **Does not leak schema state**: a fresh `MigrationExecutor` is created per `_migrate` call (note in `_migrate`: "A fresh executor per call — a reused one replans from stale applied-state"), and the transactional test uses `transaction=True, serialized_rollback=True` with a `finally` to migrate back to leaves. No global state retained between tests.
- **Proves HEAD rejects `read-only`**: `test_the_database_rejects_the_old_role_value` updates the user row via ORM (`filter(pk=user.pk).update(role="read-only")`) and expects `IntegrityError` (line 58–60). `test_the_enum_agrees_with_the_database_at_head` asserts `VIEWER_ROLE == "viewer"`, `"viewer"` in `USER_ROLE_VALUES`, `"read-only"` not in `USER_ROLE_VALUES`, and the model field choices match `USER_ROLE_VALUES` (lines 40–48).
- **Verdict**: **Met**. The test is not a no-op; it exercises MigrationExecutor in both directions and proves the CHECK rejects `read-only` at HEAD.

### 2) §9.4 — `tests/conformance/test_viewer_reads.py`
- **Read inventory is real**: 
  - Page inventory parametrizes 14 routes including dashboard, lists (case/alert/observable/taxonomy/automation/playbook/sources/tags/templates), and detail pages (case/alert/observable) — each asserts 200 (lines 79–125).
  - Case export: `ui-case-export` returns 200 and body contains "Beaconing host" (lines 130–135).
  - Attachment list and download: `ui-case-attachment-list` returns 200 and includes filename; `ui-case-attachment-download` asserts 404 (blob absent on disk) for viewer and same for analyst parity (lines 137–166). The test explicitly notes the `<uuid:attachment_id>` boundary and `_as_uuid` regression (500) is prevented.
  - API GET collections: `/api/v1/case`, `/api/v1/case/`, `/api/v1/alert`, `/api/v1/alert/` return 200 with JSON content-type (lines 171–179).
  - API GET by id: case/alert/observable return 200 with expected content (lines 181–207).
  - WebSocket: `test_a_viewer_can_subscribe_to_the_case_room_and_sync` authenticates via session cookie, connects to `/ws/case/{case.id}/`, receives `connected=True`, sends `{"type":"sync"}`, receives `timeline` payload with expected events; uses `asgiref.sync.async_to_sync` + `ChannelsCommunicator` (lines 212–240). The consumer resolves visibility with `write=False` so viewer subscribes and replays.
- **Unasserted items noted**: The test focuses on the specified surfaces (page inventory, export, attachments list/download, API GETs, WS subscribe+sync). It does not explicitly assert the `ui-case-timeline` partial in isolation or every dashboard widget fragment. However, the case detail page (parametrized) renders timeline context, the WS sync replays timeline events, and the affordance/read-only suites already cover mutation affordances and the broader read set. These omissions are minor relative to the stated requirement ("every read page, case export, attachment list/download, the API GETs, and the WebSocket subscribe + `sync` replay") — the required items are all asserted.
- **Verdict**: **Met** (required read inventory is asserted). The minor partials (timeline partial in isolation, dashboard widget specifics) do not undermine the read guarantee.

### 3) §9.5 — `tests/conformance/test_viewer_precedence.py`
- **Role beats write-share proven**: `resolve_case` denies `write=True` for viewer even with explicit write share (`test_resolve_case_denies_write_for_a_viewer_even_with_a_write_share`: returns None on write, not None on read) (lines 70–81). End-to-end: viewer with write share gets 403 on `PATCH /api/v1/case/{case.id}` and title unchanged; UI `ui-case-status` POST returns 404; reads (API GET, UI detail GET) still succeed (lines 83–106).
- **Non-viewer control is non-vacuous**: `test_the_same_write_share_lets_a_non_viewer_write` shows analyst with same write share can PATCH and title changes (lines 108–119).
- **Share grant/revoke**: viewer with write share cannot grant share (`POST /api/v1/case/{case.id}/shares` returns 403, no third share created; UI `ui-case-share-list` POST returns 404, share count unchanged) (lines 124–144). Viewer cannot revoke the share that lets it read (`DELETE /api/v1/case/{case.id}/share/{share.id}` returns 403, share persists; UI `ui-case-share-revoke` POST returns 404, share persists) (lines 146–162).
- **Precedence across resolvers (deny before privileged branch)**: `test_the_viewer_deny_precedes_the_privileged_branch` parametrizes `case`, `alert`, and `observable` and monkeypatches `scoping._is_superadmin` to `True`. For each resolver it asserts read (write=False) resolves while write (write=True) is denied — proving the viewer deny executes first and blocks writes even when privilege is asserted (lines 167–211).
- **Verdict**: **Met**. Role-beats-write-share is proven for case body (resolve + API + UI), for share grant, and for share revoke; non-viewer control is present.

### 4) `ui/views.py::not_viewer` now logs resolver `url_name` + method (CWE-117 fix)
- The wrapper reads `match = getattr(request, "resolver_match", None)` and logs `request.method` and `match.url_name if match is not None else "?"`, plus `extra={"user_id": str(request.user.pk)}`. It does **not** log `request.path`, `request.get_full_path()`, or any user-controlled path/query segments. Both fields are server-controlled (method from request, url_name from resolver). This is free of user-controlled input in the log line (lines 106–137 in ui/views.py).
- **Verdict**: **Met**.

### 5) `cases/views.py::_as_uuid` fix
- `_as_uuid` returns `value` as-is if `isinstance(value, UUID)` (line 132–134). Otherwise tries `UUID(value)` in try/except catching `AttributeError, TypeError, ValueError` (lines 135–138). The comment explicitly documents that `<uuid:...>` converters can pass a UUID instance (so `UUID(some_uuid)` would raise `AttributeError`), and the fix prevents that. The intent "malformed → None → 404, never a 500" is preserved; callers check for None and return 404. No other caller was found that would silently swallow a bad value in an unsafe way — the helper’s contract is parse-or-None and callers treat None as not found/malformed.
- **Verdict**: **Met**.

### 6) `identity/scoping.py` viewer checks in resolvers
- `resolve_case`: viewer check `if write and is_viewer(user): return None` appears before superadmin branch and before org checks (line 143–145). Superadmin path is still allowed when not writing? (superadmin branch returns case regardless of role if not writing; if writing, viewer check already returned None). That means viewer role caps superadmin-who-is-also-viewer (consistent with precedence test case).
- `resolve_alert`: viewer check `if write and is_viewer(user): return None` appears before superadmin branch (line 178–180). No longer inside any privileged branch; applied first for `write=True`.
- `resolve_observable`: same — viewer check before superadmin (line 206–208).
- The `_is_superadmin` exception was removed from viewer checks in the sense that viewer denial is evaluated before superadmin for write operations (so viewer denies even if superadmin). This matches ADR-006 (role caps the person). No legitimate non-viewer is affected; superadmins who are not viewers retain access. The change is correct.
- **Verdict**: **Met**.

### Gate re-run (exact numbers)
- Full suite: `.venv/bin/python -m pytest -q` → **1616 passed, 3 skipped** (95.66s). (Skips unchanged: Postgres-only cases.)
- Targeted conformance trio: `.venv/bin/pytest tests/conformance/test_viewer_reads.py tests/conformance/test_viewer_precedence.py tests/conformance/test_viewer_role_rename.py -q` → **32 passed**.
- Implementer-reported `make check` = 1613 passed / 3 skipped matches observed (1616/3). All green.

## Round 3 — 2026-10-12

### 1) Precedence of viewer deny (moved to first statement)
- `identity/scoping.py::resolve_case`: viewer deny `if write and is_viewer(user): return None` is now the **first** statement in the function body (appears before the `_is_superadmin` branch). The check executes for all callers, including when `_is_superadmin(user)` would be true; in that case it short-circuits before any privileged resolution. (lines 121–164)
- `identity/scoping.py::resolve_alert`: viewer deny remains first for `write=True` (lines 166–195).
- `identity/scoping.py::resolve_observable`: viewer deny remains first for `write=True` (lines 197–239).
- The test `tests/conformance/test_viewer_precedence.py::test_the_viewer_deny_precedes_the_privileged_branch` parametrizes `["case", "alert", "observable"]` and uses `monkeypatch.setattr(scoping, "_is_superadmin", lambda user: True)`. For each resolver it asserts `resolve(..., write=False) is not None` and `resolve(..., write=True) is None`. The read returning a row proves the call reached the (patched) privileged branch; the `None` on write can only come from the viewer deny that executed first. This is non-vacuous and directly pins the ordering invariant (lines 167–211).

### 2) Correction to report text (replaced vacuous test)
The line citing the deleted test `test_a_viewer_flagged_as_superuser_is_still_denied_write` has been corrected. The replacement `test_the_viewer_deny_precedes_the_privileged_branch` proves ordering by forcing `_is_superadmin` to true (monkeypatch), so the viewer deny must run before the privileged branch to block writes while allowing reads. It covers case, alert, and observable.

### Gate (Round 3)
- `.venv/bin/python -m pytest -q` → **1617 passed, 3 skipped** (95.96s). The additional passing test is the new precedence test.
- `make check` (full gate): green (docs formatting applied). Observed: ruff/format/mypy/django/migrations/css/pytest all pass.


## Summary (Round 2)
- **Now fully met**: P1–P4, P5 AC2/AC3, §9.1–§9.3, §9.6, and all three new conformance tests (1–3) plus the production changes (4–6). P5 AC1 / §9.7 is now **Met** (migration forward/reverse + constraint boundary tested). §9.4 and §9.5 are now **Met** (explicit tests exist and assert required surfaces).
- **Anything still partial**: None blocking the security guarantee. Minor read-coverage notes in §9.4 (timeline partial in isolation, dashboard widget specifics) are non-blocking; required items are asserted.
- **New problems found**: None. The changes are correct and adversarially verified.

## Independent command transcript

```
$ .venv/bin/python -m pytest -q
1581 passed, 3 skipped in 94.09s (0:01:34)

$ .venv/bin/python -m pytest tests/test_roles.py \
    tests/conformance/test_viewer_readonly_ui.py \
    tests/conformance/test_viewer_readonly_api.py \
    tests/conformance/test_viewer_affordances.py -q
40 passed in 2.35s

$ make check
... ruff/format/mypy/django/migrations/css/pytest ...
✓ check passed        # mypy: Success, no issues found in 105 source files; pytest 1581 passed, 3 skipped

$ .venv/bin/python manage.py makemigrations --check --dry-run
No changes detected
```

The 3 skips are pre-existing Postgres-only tests (`test_indexes.py:421`,
`test_case_numbering.py:340,371`), unrelated to this change.
