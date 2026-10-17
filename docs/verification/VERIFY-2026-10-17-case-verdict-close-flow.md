# Verification Report: Case verdict, inline edits, tags row and enforced close flow (§12.8)

Plan: `docs/planning/PLAN-2026-10-17-case-verdict-close-flow.md` (Goals G1–G7, §7 service layer, §8 API, §9 UI, §11 Security, §12 Testing)

Verified: 2026-10-17, at commit `2476edb` (as merged in `b5e3d55`, on top of `origin/main` `4e86844`)
Status: **Pass** — 18/18 ACs Met

> **Provenance / independence caveat (read first).** This verification was performed **inline by the
> planning agent**, not by the `verifier` subagent, because the subagent service was unavailable
> during the session (repeated HTTP 503 `Upstream error from Nvidia: Service temporarily
> overloaded`, both on first call and on resume — the same outage that forced the two upstream
> reports to be planner-authored). The method is the verifier's method (read each AC, read the
> cited test *assertions*, read the implementation, cross-check the plan's claims against the
> code), but **it is not independent** — the same session that wrote the code graded it.
> **Action:** re-run `verifier` against this plan when the service recovers and re-issue this file;
> until then treat "Pass" as provisional. No `security-auditor` pass has been run on §12.8 either
> (see Follow-ups).

---

## Summary

| Category | Count |
|----------|-------|
| Total ACs | **18** |
| Met | **18** |
| Not Met | 0 |
| Deviated | 0 |

Test evidence: `tests/conformance/test_case_verdict_close.py` **22 passed** and
`tests/conformance/test_ui_case_verdict.py` **27 passed** (49 collected, both green), run against
the merged tree. Supporting slices, all green post-merge: `test_ui_loop.py` +
`test_settings_ingestion_tokens.py` + `test_realtime.py` + `test_ui_case_verdict.py` = **120 passed**;
`test_case_verdict_close.py` + `test_case_classification.py` + `test_case_classification_hardening.py` +
`test_enum_contracts.py` + `test_viewer_readonly_ui.py` + `test_ui_p2_wiring.py` + `test_ui_urls_smoke.py`
= **233 passed**; `test_tenant_scoping.py` = **92 passed**. Static gate green post-merge: `ruff check .`
clean, `ruff format --check .` **314 files already formatted**, `mypy` **109 files**, `manage.py check`
0 issues, `makemigrations --check --dry-run` no changes, `secret-scan --tracked` clean (496 files).
The full `make check` (whole suite) is run on request, not on every edit — see the plan's §12 note.
`make css-check` cannot run on this host (`scripts/tailwindcss: Bad CPU type in executable`).

What shipped: a DB-constrained `verdict` on `Case` and `Alert` (snake_case tokens, `undefined`
default) exposed through `case_json`/`alert_json` and the Query DSL; a Taxonomy **Verdict** card; a
single service-layer close rule (`enforce_close_rule`) that blocks a case entering **Closed** without
a closing verdict **and** a non-blank comment, wired through every write path (single `PATCH`,
`_bulk`, create-to-Closed, and the UI); six inline-edit `<dialog>`s on the General tab; an
always-visible Tags row with add/remove; synchronous close-propagation that closes **every** linked
alert (including `Imported`) and copies the verdict, with one `AuditLog` row + one timeline event;
and the two pre-existing defects fixed on the way (`closed_date`/`end_date` persistence;
`AuditLog.target_type` casing).

---

## Criterion-by-Criterion Evaluation

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC-1** | `Case.verdict` defaults to `undefined`, DB-CHECK constrained | **Met** | `cases/models.py:131` field (`default="undefined"`), `:173` `in_values("case_verdict_valid", "verdict", CASE_VERDICT_VALUES)`; migration `cases/migrations/0018_case_verdict.py:18,23` (`CheckConstraint Q(verdict__in=(...))`). Test `test_case_verdict_close.py::test_case_verdict_defaults_to_undefined` |
| **AC-2** | `Alert.verdict` defaults to `undefined`, DB-CHECK constrained | **Met** | `alerts/models.py:93` field, `:158` `in_values("alert_verdict_valid", ...)`; migration `alerts/migrations/0015_alert_verdict.py:20,25`. Test `::test_alert_verdict_defaults_to_undefined` |
| **AC-3** | `case_json` / `alert_json` expose `verdict` | **Met** | `core/serializers.py:294` (`case_json`), `:75` (`alert_json`). Test `::test_case_json_and_alert_json_expose_verdict` |
| **AC-4** | Query DSL exposes `verdict` on case **and** alert | **Met** | `query/engine.py:118` (alert `verdict` field spec), `:145` (case `verdict` spec) |
| **AC-5** | Taxonomy page renders a **Verdict** card (Code + Label, mono code cell) | **Met** | `ui/templates/ui/taxonomy.html:158-173` loops `verdict_choices` with `class="mono"` on the code cell; context from `ui/views.py::taxonomy`. Tests `test_ui_case_verdict.py::test_taxonomy_renders_the_verdict_card`, `::test_taxonomy_verdict_card_uses_the_mono_code_cell` |
| **AC-6** | Close refused without a *closing* verdict (service layer) | **Met** | `cases/update.py:215-219` — `verdict not in CASE_CLOSING_VERDICTS` → `CaseUpdateError("ValidationError", …, {"verdict": …})`. Tests `::test_service_layer_close_rule_raises`, `::test_api_close_without_verdict_is_refused`, `::test_api_close_with_undefined_verdict_is_refused` |
| **AC-7** | Close refused without a non-blank closing comment | **Met** | `cases/update.py:221-224` — `str(payload.get("comment") or "").strip()` empty → refuse. Tests `::test_api_close_without_comment_is_refused`, `::test_api_close_with_blank_comment_is_refused` |
| **AC-8** | Rule enforced identically in all four write paths | **Met** | `cases/update.py:315` (`apply_case_update` — the single `PATCH` choke point), `cases/views.py:1910` (`case_bulk_update`, `_bulk`), `cases/create.py:149` (create-to-Closed), `ui/views.py:1056,1089` (UI status + close). Tests `::test_bulk_close_without_verdict_is_refused`, `::test_create_in_closed_stage_without_verdict_is_refused`, `test_ui_case_verdict.py::test_the_plain_status_endpoint_refuses_a_bare_closed` |
| **AC-9** | Six inline-editable fields; whitelist; Classification admin-only; unknown field → 404 | **Met** | `ui/views.py:131-133` `CASE_INLINE_EDIT_FIELDS = frozenset({severity, scope, case_type, data_classification, phase, verdict})`; `:969-1021` descriptor list (`admin_only=True` only for `data_classification`); `:1114-1115` unknown field `raise Http404()`. Markup `ui/templates/ui/_tab_general.html:18-44` (`can_mutate` / `can_classify` gating). Tests `::test_the_general_tab_renders_edit_icons_for_every_inline_field`, `::test_an_admin_sees_the_classification_edit_icon`, `::test_field_edit_rejects_an_unknown_field_with_a_404`, `::test_field_edit_sets_a_field_through_the_shared_applier`, `::test_field_edit_can_set_the_verdict_before_close` |
| **AC-10** | Tags row always renders; per-tag removal; Add Tag (select existing **+** create-on-assign) | **Met** | `_tab_general.html:82-100` (`.case-tags` / `.tag-list`, remove control, Add Tag button, `can_mutate`-gated); `ui/views.py:1129-1150` (`case_tag_add` merges `tags` + `new_tags` → `addTags`). Tests `::test_the_general_tab_always_renders_the_tags_row`, `::test_the_tags_row_lists_tags_with_a_remove_control`, `::test_tag_add_attaches_an_existing_tag`, `::test_tag_add_creates_on_assign`, `::test_tag_remove_detaches_a_tag`, `::test_tag_add_emits_a_tags_changed_event` |
| **AC-11** | Closing closes **all** linked alerts (incl. `Imported`) and copies the verdict, synchronously | **Met** | `cases/update.py:266-271` — `Alert.objects.filter(case=case)` (no status exclusion) then `.update(status=closed_status, verdict=verdict)`; called inside the close transaction at `:377`. Tests `::test_close_propagates_to_all_linked_alerts`, `test_ui_case_verdict.py::test_closing_a_case_closes_every_linked_alert_including_imported` |
| **AC-12** | One `AuditLog` row + one timeline event summarise propagation; seeded `Closed` status preferred | **Met** | `cases/update.py:273-289` (one `append_timeline_event(kind="alerts-closed")` + one `AuditLog(action="case.alerts_closed", target_type="Case")`); `:227-248` `resolve_closed_alert_status` prefers the seeded `Closed`; seed `alerts/migrations/0016_seed_closed_alert_status.py:18-25` (`get_or_create(value="Closed", stage="Closed", order=5)`). Tests `::test_close_records_one_audit_row_and_one_timeline_event`, `::test_close_prefers_seeded_closed_alert_status` |
| **AC-13** | `closed_date` persisted on close, cleared on reopen, `end_date` defaults to `closed_date` (D4a) | **Met** | `cases/update.py:329-338` — `stamp_closed_date()`, `end_date = closed_date` when unset, `closed_date = None` on reopen, and `fields += ["closed_date", "end_date"]`. Tests `::test_api_close_with_verdict_and_comment_succeeds_and_stamps_dates`, `::test_reopening_clears_closed_date` |
| **AC-14** | Tracked-change `AuditLog` rows use `target_type="Case"` so the case Audit tab shows them (D4b) | **Met** | `cases/update.py:438` writes `target_type="Case"` (comment at `:431` explains the old lowercase bug). Tests `::test_classification_audit_row_uses_case_target_type` (asserts the `"Case"` row **and** asserts the lowercase `"case"` row is absent, `:391`), `::test_verdict_change_writes_case_typed_audit_row`; updated `test_case_classification.py:431-435` |
| **AC-15** | Four new UI routes are `@not_viewer @login_required @require_POST`, org-scoped via `resolve_case(..., write=True)`; `case_set_status` delegates to the applier | **Met** | `ui/urls.py:25,26,45,46`; decorators `ui/views.py:1024-1026, 1065-1067, 1098-1100, 1126-1128, 1153-1155`; each calls `resolve_case(request.user, case_id, write=True)` and raises `Http404` on miss (`:1037-1039, 1076-1078, 1110-1112, 1136-1138, 1158-1160`); `case_set_status` now posts through `apply_case_update` (`:1056`). Tests `::test_field_edit_is_404_for_an_unknown_case`, `::test_field_edit_is_404_across_organisations`, `::test_a_viewer_is_denied_every_new_endpoint` |
| **AC-16** | Plain status `<select>` omits **Closed**; a **Close case** button appears while open; the close dialog offers only closing verdicts | **Met** | `_tab_general.html:55` (`{% if stage != "Closed" %}`), `:64-66` (Close-case button gated on `stage != "Closed"`), `:168` close modal; `_case_close_form.html:10-12` loops `closing_verdict_choices`. Tests `::test_the_plain_status_select_offers_no_closed_option`, `::test_the_general_tab_renders_a_close_button_when_the_case_is_open`, `::test_a_closed_case_offers_no_close_button`, `::test_the_close_dialog_only_offers_the_closing_verdicts` |
| **AC-17** | Security: rule is server-side (raw API close without verdict → 400); no inline scripts/styles; modals via `<dialog>` (ADR-003) | **Met** | API path rejects with 400 — `::test_api_close_without_verdict_is_refused`; the UI close dialogs are `<dialog>` bodies rendered through `ui/_modal.html` + `modal.js` and the new CSS classes were added to `ui/static/ui/app.css`'s component section, not as inline styles (CSP `style-src 'self'`). Confirmed by the AUTHZ/`test_viewer_readonly_ui.py` sweep staying green |
| **AC-18** | Tests, enum-contract entries and docs land with the code | **Met** | `test_case_verdict_close.py` (22), `test_ui_case_verdict.py` (27); `test_enum_contracts.py` carries `("case_record","verdict")→"case_verdict_valid"` and `("alert","verdict")→"alert_verdict_valid"` (suite green); docs `docs/spec/deviations.md` (V-1/V-2/V-3), `docs/spec/data-model.md`, `docs/spec/api.md`, `docs/spec/query-dsl.md` |

---

## Deviations (already recorded in `docs/spec/deviations.md`)

| ID | Deviation | Matches code? |
|----|-----------|---------------|
| **V-1** | Verdict tokens are snake_case (`undefined`/`true_positive`/`false_positive`), not the literal §12.8 spelling (labels unchanged) | Yes — `core/enums.py:200-205`, `cases/models.py:131`, `alerts/models.py:93` |
| **V-2** | Closing a case closes/relabels linked alerts and seeds a `Closed` `AlertStatus` (widens the AGENTS.md §3 `Alert` sketch) | Yes — `cases/update.py:251-290`, `alerts/migrations/0016_seed_closed_alert_status.py` |
| **V-3** | The General-tab status write emits timeline kind `status-changed`, not `status-change` | Yes — `ui/views.py:1056` delegates to the applier; `app.css` styles `.tl-status-changed` (legacy `.tl-status-change` kept for historical rows) |

No **new** deviations were found: the code matches the plan (as amended by V-1/V-2/V-3) on every AC above.

---

## Observations / Follow-ups (non-blocking, not ACs)

- **O1 — `make css` unavailable locally.** `scripts/tailwindcss` is the wrong CPU type for this host
  (`Bad CPU type in executable`), so the six new classes (`.meta-edit`, `.case-tags`,
  `.case-tags-label`, `.tag-list`, `.field`, `.tl-status-changed:before`) were hand-written into
  `ui/static/ui/app.css`'s commented component section and are **not** re-minified into the compiled
  block. A maintainer on a supported platform should run `make css` before release. *(Same note as
  the plan §15.)*
- **O2 — new timeline kinds render the default dot.** `classification-changed`, `severity-changed`,
  `verdict-changed`, `tags-changed` and `alerts-closed` rows use the neutral grey timeline dot; only
  the status dot is colour-keyed. Cosmetic, unchanged from the plan's stated scope.
- **O3 — `Tag` is global.** Create-on-assign mints an org-visible tag (pre-existing behaviour for
  every attach path; documented as a caveat in the plan §11, not fixed here).
- **O4 — `verdict` is deliberately absent from `CaseTemplate`** and from `compat/`, matching the
  existing non-parity decision for `data_classification` (plan §3, §6.5).
- **O5 — pre-existing unrelated test failure** (not caused by this work):
  `tests/conformance/test_t1_surface.py::test_the_101st_anonymous_login_from_one_client_is_throttled`
  fails in isolation on a clean tree too.

## Required follow-ups before this can be marked done

1. **Re-run `verifier`** against `PLAN-2026-10-17-case-verdict-close-flow.md` once the service is
   back, and re-issue this report (currently **provisional**, planner-authored).
2. **Run `security-auditor`** over the §12.8 surface — in particular `ui-case-field-edit`'s whitelist,
   the org-scoped `resolve_case(write=True)` chain, the close rule's multi-path enforcement, and the
   `QuerySet.update()` propagation (no per-row signals) — no such pass has run.
3. Run **`make css`** on a supported platform (O1) before release.
