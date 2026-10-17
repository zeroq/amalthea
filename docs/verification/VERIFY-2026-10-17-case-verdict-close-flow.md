# Verification Report: Case verdict, inline edits, tags row and enforced close flow (§12.8)

Plan: `docs/planning/PLAN-2026-10-17-case-verdict-close-flow.md`

**Verified 2026-10-17 at commit `79793f5`** (`79793f56a9ec0e87a86154afbbf11d917ec211c3`).

This is an **independent re-run** performed by `qa-tester` standing in for the `verifier` agent,
which was unavailable at the time (503 outage). It **supersedes** the provisional planner-authored
report at commit `2476edb` (report file added in `39c591e`). The old report's "Pass, 18/18 Met"
verdict was explicitly provisional and non-independent; every judgement below was re-derived from
source and tests, and the old report's evidence was treated as untrusted.

Result: **18/18 ACs Met, 0 Not Met, 0 Deviated at the AC level.** One new deviation from plan prose
and several citation drifts were found (see below); none invalidate an AC.

---

## Summary

| Category | Count |
|----------|-------|
| Total ACs | **18** |
| Met | **18** |
| Not Met | 0 |
| Deviated (AC-level) | 0 |
| New deviations from plan prose (not recorded in `deviations.md`) | **1** (D-N1, low impact) |
| Citation drifts found in the superseded report / code comments | **3** |

### Test / gate evidence (re-run at `79793f5`)

| Command | Result |
|---------|--------|
| `pytest -q tests/conformance/test_case_verdict_close.py tests/conformance/test_ui_case_verdict.py` | **49 passed** (22 + 27) in 6.5s |
| `pytest -q` (full gate) | **2460 passed, 3 skipped, 0 failed** |
| `pytest -q tests/conformance/test_case_classification_ui.py tests/conformance/test_seed_migrations.py` | **36 passed** |
| `pytest -q tests/conformance/test_ui_inline_assets.py` | **312 passed** |
| `pytest -q tests/conformance/test_t1_surface.py` | **56 passed** |
| `python manage.py makemigrations --check --dry-run` | **No changes detected** (exit 0) |
| `ruff check .` | **All checks passed!** |
| `ruff format --check .` | **316 files already formatted** |

`make css` / `make css-check` **cannot run on this host** (`scripts/tailwindcss: Bad CPU type in
executable` — the binary is `Mach-O 64-bit executable arm64`). This is an environment limitation,
not a product failure; see O1.

---

## Criterion-by-Criterion Evaluation

The plan does not enumerate ACs, so the superseded report's 18-AC decomposition is retained for
comparability. Each AC was regraded strictly against source + assertions; citations below were
re-read from the current tree (not copied from the old report).

| AC | Description | Status | Evidence (re-verified) |
|----|-------------|--------|------------------------|
| **AC-1** | `Case.verdict` defaults to `undefined`, DB-CHECK constrained | **Met** | `cases/models.py:131` field (`default="undefined"`), `:173` `in_values("case_verdict_valid", "verdict", CASE_VERDICT_VALUES)`; migration `cases/migrations/0018_case_verdict.py:16-23`. Tests `test_case_verdict_close.py::test_case_verdict_defaults_to_undefined` |
| **AC-2** | `Alert.verdict` defaults to `undefined`, DB-CHECK constrained | **Met** | `alerts/models.py:93` field, `:158` `in_values("alert_verdict_valid", ...)`; migration `alerts/migrations/0015_alert_verdict.py:20-28`. Test `::test_alert_verdict_defaults_to_undefined` |
| **AC-3** | `case_json` / `alert_json` expose `verdict` | **Met** | `core/serializers.py:75` (`alert_json`), `:294` (`case_json`). Test `::test_case_json_and_alert_json_expose_verdict` |
| **AC-4** | Query DSL exposes `verdict` on case **and** alert | **Met** | `query/engine.py:118` **case** dict, `:145` **alert** dict (both `_FieldSpec("verdict")`). *(The superseded report swapped these two lines — see Citation drift.)* |
| **AC-5** | Taxonomy page renders a **Verdict** card (code + label, mono code cell) | **Met** | `ui/templates/ui/taxonomy.html:162-168` (`<caption class="sr-only">Case verdict</caption>`, `<td class="mono">{{ val }}</td>`); context `ui/views.py:3388` (`verdict_choices=CASE_VERDICT_CHOICES`), `:3380-3389`. Tests `test_ui_case_verdict.py::test_taxonomy_renders_the_verdict_card`, `::test_taxonomy_verdict_card_uses_the_mono_code_cell` |
| **AC-6** | Close refused without a *closing* verdict (service layer) | **Met** | `cases/update.py:199` `enforce_close_rule`, `:216-217` (`verdict not in CASE_CLOSING_VERDICTS` → `CaseUpdateError("ValidationError", ...)`). Tests `::test_service_layer_close_rule_raises`, `test_case_verdict_close.py::test_api_close_without_verdict_is_refused`, `::test_api_close_with_undefined_verdict_is_refused` |
| **AC-7** | Close refused without a non-blank comment | **Met** | `cases/update.py:216-217` region — comment blank-check in `enforce_close_rule`. Tests `test_case_verdict_close.py::test_api_close_without_comment_is_refused`, `::test_api_close_with_blank_comment_is_refused` |
| **AC-8** | Close rule enforced on every write path (single PATCH, `_bulk`, create-to-Closed, UI) | **Met** | `cases/update.py:315` (applier call site; applier at `:294`), `cases/views.py:1910` (bulk), `cases/create.py:149` (create-to-Closed), `ui/views.py:1056` (`case_set_status` → applier), `:1068` (`case_close`). Tests `::test_bulk_close_without_verdict_is_refused`, `::test_create_in_closed_stage_without_verdict_is_refused`, `test_ui_case_verdict.py::test_the_plain_status_endpoint_refuses_a_bare_closed`. **Functional subset Met; the plan's "dialog re-rendered open" failure affordance is not implemented — see D-N1.** |
| **AC-9** | Six inline-editable fields; whitelist; Classification admin-only; unknown field → 404 | **Met** | `ui/views.py:131-133` `CASE_INLINE_EDIT_FIELDS`; descriptor list `:974-1021` (`"admin_only": True` only at `:1005`); `:1114-1115` unknown field `raise Http404()`. Markup `ui/templates/ui/_tab_general.html:18,44` with `can_mutate`/`can_classify` gating. Tests `::test_the_general_tab_renders_edit_icons_for_every_inline_field`, `::test_an_admin_sees_the_classification_edit_icon`, `::test_field_edit_rejects_an_unknown_field_with_a_404`, `::test_field_edit_sets_a_field_through_the_shared_applier`, `::test_field_edit_can_set_the_verdict_before_close` |
| **AC-10** | Tags row always renders; per-tag removal; Add Tag (select existing + create-on-assign) | **Met** | `_tab_general.html:82-105` (`.case-tags` always rendered, `.tag-list`, remove control, Add Tag button); `ui/views.py:1129-1150` `case_tag_add` merges `tags` + `new_tags` → `addTags`. Tests `::test_the_general_tab_always_renders_the_tags_row`, `::test_the_tags_row_lists_tags_with_a_remove_control`, `::test_tag_add_attaches_an_existing_tag`, `::test_tag_add_creates_on_assign`, `::test_tag_remove_detaches_a_tag`, `::test_tag_add_emits_a_tags_changed_event` |
| **AC-11** | Closing closes **all** linked alerts (incl. `Imported`) and copies the verdict, synchronously | **Met** | `cases/update.py:251` `propagate_close_to_alerts`, `:266-271` selects every alert by `case=` (no stage filter) and `.update(status=closed_status, verdict=verdict)`; called inside the same transaction. Tests `test_case_verdict_close.py::test_close_propagates_to_all_linked_alerts`, `test_ui_case_verdict.py::test_closing_a_case_closes_every_linked_alert_including_imported` |
| **AC-12** | One `AuditLog` + one timeline event per propagation; prefers seeded `Closed` alert status | **Met** | `cases/update.py:273-289` (one `alerts-closed` timeline event + one `AuditLog` `action="case.alerts_closed"`; `target_type="Case"` at `:285`); `:237-248` `resolve_closed_alert_status` prefers `value="Closed"`. Seed `alerts/migrations/0016_seed_closed_alert_status.py:18-21`. Tests `test_case_verdict_close.py::test_close_records_one_audit_row_and_one_timeline_event`, `::test_close_prefers_seeded_closed_alert_status`, `test_ui_case_verdict.py::test_closing_records_one_case_level_audit_row` |
| **AC-13** | `closed_date` persisted on close, cleared on reopen, `end_date` defaults to `closed_date` (D4a) | **Met** | `cases/update.py:323-338` — `case.stamp_closed_date()`, `end_date = closed_date` when unset, `closed_date = None` on reopen, and `fields += ["closed_date", "end_date"]` so `update_fields` persists them. Tests `test_case_verdict_close.py::test_api_close_with_verdict_and_comment_succeeds_and_stamps_dates`, `::test_reopening_clears_closed_date` |
| **AC-14** | Tracked-change `AuditLog` rows use `target_type="Case"` so the Audit tab shows them (D4b) | **Met** | `cases/update.py:438` writes `target_type="Case"` (comment `:431` explains the old lowercase bug). Tests `::test_classification_audit_row_uses_case_target_type`, `::test_verdict_change_writes_case_typed_audit_row`; `test_case_classification.py:431` updated to `target_type="Case"` |
| **AC-15** | New UI routes are `@not_viewer @login_required @require_POST`, org-scoped via `resolve_case(..., write=True)`; `case_set_status` delegates to the applier | **Met** | `ui/urls.py:24,25,26,45,46`; decorators `ui/views.py:1024-1026,1065-1067,1098-1100,1126-1128,1153-1155`; each calls `resolve_case(request.user, case_id, write=True)` and `raise Http404()` on miss (`:1037-1039,1076-1078,1110-1112,1136-1138,1158-1160`); `case_set_status` posts through `apply_case_update` (`:1056`). Tests `::test_field_edit_is_404_for_an_unknown_case`, `::test_field_edit_is_404_across_organisations`, `::test_a_viewer_is_denied_*`, `test_case_verdict_close.py::test_viewer_cannot_close` |
| **AC-16** | Plain status select omits `Closed`; Close button shown while open; close dialog offers only closing verdicts | **Met** | `_tab_general.html:55` (`{% if stage != "Closed" %}`), `:64-66` (Close case button gated on `stage != "Closed"`), `:168` (dialog); `_case_close_form.html:10-12` iterates `closing_verdict_choices` (context `ui/views.py:935-937`). Tests `::test_the_plain_status_select_offers_no_closed_option`, `::test_the_general_tab_renders_a_close_button_when_the_case_is_open`, `::test_a_closed_case_offers_no_close_button`, `::test_the_close_dialog_only_offers_the_closing_verdicts`. Status dot: `.tl-status-changed:before` is **live** at `ui/static/ui/app.css:609-611` |
| **AC-17** | Server-side enforcement (raw API close without verdict refused); dialogs are `<dialog>`, no inline styles | **Met** | Close refused at service layer (see AC-6/AC-8); dialogs rendered through `ui/_modal.html` + `modal.js`; new CSS classes added to `app.css`'s component section (`ui/static/ui/app.css:520-612`), not inline. `test_ui_inline_assets.py` **312 passed** |
| **AC-18** | Tests, enum-contract entries and docs land with the code | **Met** | `test_case_verdict_close.py` (22), `test_ui_case_verdict.py` (27); `test_enum_contracts.py:58` `("alert","verdict")→"alert_verdict_valid"`, `:69` `("case_record","verdict")→"case_verdict_valid"`, asserted `:326,335`; `MANAGED_CONSTRAINTS` entries; docs `docs/spec/deviations.md` (V-1/V-2/V-3), `docs/spec/data-model.md`, `docs/spec/api.md`, `docs/spec/query-dsl.md` |

---

## Deviations already recorded in `docs/spec/deviations.md` (re-confirmed)

| ID | Deviation | Re-confirmed against code? |
|----|-----------|----------------------------|
| **V-1** | Verdict tokens are snake_case (`undefined`/`true_positive`/`false_positive`), labels human-readable | Yes — `core/enums.py:200-205` `CASE_VERDICT_CHOICES`/`CASE_VERDICT_VALUES`; `cases/models.py:131`; `alerts/models.py:93` |
| **V-2** | Closing a case closes/relabels linked alerts and seeds a `Closed` `AlertStatus` (widens the AGENTS.md §3 `Alert` sketch) | Yes — `cases/update.py:251-290`; `alerts/migrations/0016_seed_closed_alert_status.py` |
| **V-3** | General-tab status write emits timeline kind `status-changed`, not `status-change` | Yes — `cases/update.py:350` `kind="status-changed"`; `ui/views.py:1056` delegates to the applier; `app.css:609-611` styles `.tl-status-changed` and keeps legacy `.tl-status-change` |

## New deviation from plan prose (NOT recorded in `deviations.md`)

| ID | Deviation | Impact | Evidence |
|----|-----------|--------|----------|
| **D-N1** | Plan §9.3 (`PLAN-...:239`) specifies for `ui-case-close`: *"Failure → message + redirect back to General with the dialog re-rendered open."* The implementation flashes `messages.error(...)` and redirects to `?tab=general` (`ui/views.py:1090-1092`, `_case_detail_redirect` `:765-776`) but does **not** re-open the close dialog. There is also no test asserting the error is re-rendered: `test_ui_case_verdict.py::test_close_is_refused_without_a_verdict` asserts only that the case stays open and `closed_date is None` (`:370-377`), not the message/body. The plan's own test description (`PLAN-...:294`, "re-renders with an error") overstates the assertion. | **Low** — the enforcement is correct; only the convenience re-open is missing. No AC covers it. | `ui/views.py:1090-1092`; `test_ui_case_verdict.py:370-377` |

---

## Citation drift found (accuracy of the superseded report / code comments)

1. **Superseded report AC-4 swapped the Query-DSL lines.** It cited `query/engine.py:118` as the
   *alert* spec and `:145` as the *case* spec. Actual: `:118` is the last entry of `_case_fields()`
   (case); `:145` is inside `_alert_fields()` (alert). AC-4 itself remains **Met**.
2. **Dead test reference in `alerts/migrations/0015_alert_verdict.py:13`.** The comment (added by
   `79793f5` when the `('cases', '0017_case_classification')` edge was removed) cites a guard
   `tests/conformance/test_seed_migrations.py::test_rollback_to_seed_stays_in_app` — **no such test
   exists** (`rg` over `tests/` returns no match). The `test_seed_migrations.py` suite is green (11
   test functions, none with that name), so the intent is covered elsewhere; the reference is stale.
3. **Superseded report O1 is factually wrong about the CSS.** It described the new component classes
   as hand-written "into `app.css`'s **commented** component section". The section is *not*
   commented: the banner comment closes at `app.css:515`, and `.meta-edit` (`:520`), `.case-tags`
   (`:544`), `.tag-list` (`:569`), `.field` (`:584`) and `.tl-status-changed:before`
   (`:609-611`) are live rules in the served file. The classes therefore take effect at runtime; see
   O1 for the residual build-reproducibility caveat.

Additional note on `79793f5`: `tests/conformance/test_case_classification_ui.py::test_general_tab_shows_human_labels`
now matches with `re.search(rf"<dt>{label}</dt>\s*<dd>\s*{re.escape(value)}", body)` — i.e. it pins
the human label as the first content of the `<dd>` but no longer requires the value to be immediately
adjacent to `</dd>`. That is a legitimate relaxation (each `<dd>` now also contains a `.meta-edit`
button); it still substantively asserts human labels. It does not weaken any §12.8 AC.

---

## Observations / follow-ups (re-assessed)

- **O1 — `make css` cannot run on this host; classes are live but the build is unverified.**
  `scripts/tailwindcss` is an arm64 binary and `make css` compiles
  `-i ui/static/ui/app.css -o ui/static/ui/app.css` (input and output are the same file; `css-check`
  compiles to `/dev/null`). The new classes **are present and active** in the served
  `ui/static/ui/app.css` (correcting the superseded report). Residual risk: it has not been proven
  that a regeneration on a supported platform preserves the hand-written component block. A
  maintainer should run `make css` (and inspect the diff) before release.
- **O2 — new timeline kinds render the default dot.** `classification-changed`, `severity-changed`,
  `verdict-changed`, `tags-changed` and `alerts-closed` keep the neutral grey dot; only
  `status-changed` is colour-keyed (`app.css:609-611`). Cosmetic; matches the plan's stated scope.
- **O3 — `Tag` is global.** Create-on-assign mints an org-visible tag (pre-existing attach-path
  behaviour; documented as a plan §11 caveat, not changed here).
- **O4 — `verdict` deliberately absent from `CaseTemplate` / `compat/`**, matching the existing
  non-parity decision for `data_classification` (plan §3, §6.5).
- **O5 — the superseded report's "pre-existing unrelated test failure" does NOT reproduce.**
  `test_t1_surface.py::test_the_101st_anonymous_login_from_one_client_is_throttled` passes in
  isolation (×3) and in the full suite at `79793f5`. The old report's claim of a pre-existing
  failure is not reproducible and should be treated as withdrawn.

## Required follow-ups

1. ~~Re-run `verifier` against the plan and re-issue this report.**~~ **Discharged** — this report
   is the re-issue (independent re-run by `qa-tester`; `verifier` was down with a 503).
2. **`security-auditor` pass on §12.8** — still outstanding. Focus: the `ui-case-field-edit`
   whitelist, org scoping via `resolve_case(..., write=True)`, the close rule reachable from every
   write path, and the `QuerySet.update()` propagation (no per-row signals).
3. **Run `make css` on a supported platform** and confirm the hand-written component block survives;
   inspect the diff before release (O1).
4. **Resolve D-N1** — either implement the §9.3 "dialog re-rendered open" failure affordance (and
   add a test asserting the error is re-rendered) **or** amend the plan prose to match the shipped
   `message + redirect` behaviour.
5. ~~Fix the dead test reference in `alerts/migrations/0015_alert_verdict.py:13`.~~ **Discharged** —
   the comment now points at the real guard,
   `test_seed_migrations.py::test_h3_2_rolling_back_a_seed_unapplies_nothing_in_another_app`
   (amended with this report).

---

*Verified 2026-10-17 at `79793f5` by `qa-tester` (for the unavailable `verifier`). Supersedes the
provisional planner-authored report at `2476edb`.*
