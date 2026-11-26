# Plan: Alert add-tags picker parity

**Date:** 2026-11-24
**Status:** APPROVED / IMPLEMENTED — defaults accepted 2026-11-24; targeted verification passed
**Owner:** planner
**Source:** user bug report 2026-11-24; `TODO.md` §§14.1, 14.4, 14.6, 14.9–14.11

---

## 1. Summary

The alert detail **Add tags** modal currently has no usable list of existing tags when the caller’s
organisation has no tag links. `ui/views/alerts.py::alert_detail` builds `available_tags` from tags
already linked to an alert/case/observable owned by `user_org_id`, so a tag that exists in the tag
catalog but has not yet been used in that organisation is invisible.

Case detail uses a simpler, deterministic approach: `ui/views/case_detail.py` takes the global
`Tag` catalog, excludes tags already on the case, orders by name, caps at `PICKER_PAGE_SIZE`, and
renders name-based checkboxes in `ui/templates/ui/_case_tag_add_form.html`. The alert modal should
use the same picker approach so an analyst can select an existing tag by name and attach it in the
same write as any newly typed tags.

Per the 2026-11-24 clarification and approval: **tag assignment is organisation-scoped through the
parent object** (`Alert`, `Case`, `Observable`), while the `Tag` catalog itself has no organisation
column. The approved default mirrors the case picker exactly: show the global catalog, excluding
tags already on the alert. See §12 for the closed decision gate.

---

## 2. Goals

- Make the alert detail **Existing tags** list appear and behave like the case detail picker.
- Allow an analyst to attach any listed existing tag to an alert they may write.
- Preserve create-on-assign for comma-separated new tag names.
- Remove the arbitrary UUID submission path (`existing_tag_ids`) so stale/forged IDs cannot be sent.
- Keep the change narrow: no schema migration, no REST API change, no UI redesign.

---

## 3. Non-goals

- No `Tag` schema change; no `owner_org` column on `Tag`; no data migration.
- No change to `POST /api/v1/alert/{id}/tag/add` or `/api/v1/alert/{id}/tag` contracts.
- No change to the case tag picker unless Q1 selects strict org-scoped visibility for both views.
- No audit/ledger/WebSocket parity for alert tag writes unless Q3 selects it now (`TODO.md` §14.6).
- No tag rename/delete administration, no bulk tag management, no client-side-generated options.

---

## 4. Architecture

### 4.1 Stack and references

- Backend: Django 5.x views, ORM, PostgreSQL — `ADR-001-backend-framework.md`.
- Frontend: server-rendered Django templates, HTMX-compatible progressive enhancement, Tailwind
  classes, native `<dialog>` modal — `ADR-003-frontend-assets.md`; `docs/spec/ui-style.md`.
- Tenant isolation: organisation ownership lives on `Alert`/`Case`/`Observable`, not on `Tag` —
  `ADR-004-tenant-isolation.md`.
- TheHive alignment: tags are string labels attached to first-class objects; the REST API keeps its
  existing tag names — `ADR-002-thehive-api-compatibility.md`.
- Viewer gating: `@not_viewer` remains on mutating tag routes — `ADR-006-role-semantics.md`.

### 4.2 Current-state comparison

| Concern | Case detail (target pattern) | Alert detail (current) |
| --- | --- | --- |
| Picker source | `Tag.objects.exclude(current ids)` | `Tag.objects.filter(links to `user_org_id`) |
| Ordering | `order_by("name")` | `order_by("name")` after `.distinct()` |
| Cap | `PICKER_PAGE_SIZE + 1` fetched; slice to `PICKER_PAGE_SIZE` | same |
| Current tags | excluded by case tag ids | excluded by alert tag ids |
| Checkbox | `name="tags" value="{{ tag.name }}"` | `name="existing_tag_ids" value="{{ tag.id }}"` |
| New tags field | `name="new_tags"` | `name="new_tag_names"` |
| Truncation hint | `Showing the first N tags; type to add others.` | `Showing the first N tags in this org.` |
| POST handling | names + `new_tags`, shared applier, ledger event | UUIDs + `new_tag_names`, direct `AlertTagLink.bulk_create` |

### 4.3 Recommended change

Mirror the case pattern in the alert view:

1. Replace the link-filtered picker queryset with a global, name-ordered `Tag` queryset excluding
   tags already attached to the alert.
2. Change the alert modal fields to `tags` (checkboxes carrying tag **names**) and `new_tags`
   (comma-separated new names).
3. Update `alert_tag_add` to collect and normalise names, resolve/create `Tag` rows by name, and link
   them to the already-resolved alert.
4. Update regression tests to the new field names and add picker parity coverage.

This also retires the `existing_tag_ids` attack surface in `TODO.md` §14.1 without adding a UUID
parser. `TODO.md` §14.4 becomes moot under the global catalog default; if Q1 chooses strict org
visibility, implement §14.4 by scoping to `alert.owner_org_id` and validating against that queryset.

---

## 5. Database schema

No schema changes.

- `cases.Tag`: global unique `name` (`max_length=100`), `colour`, `description`.
- `alerts.AlertTagLink`: unique `(alert, tag)`; organisation ownership is inherited from
  `alert.owner_org`.
- `cases.CaseTagLink` and `observables.ObservableTagLink` follow the same parent-owned pattern.

No backfill. No migration file. Existing links remain authoritative for which tags are attached to
which org-owned object.

---

## 6. API contracts

- **REST API:** unchanged. `POST /api/v1/alert/{id}/tag/add` and
  `POST|DELETE /api/v1/alert/{id}/tag` continue to accept tag names in `tags`.
- **UI POST:** `ui-alert-tag-add` changes from:
  - `existing_tag_ids` (repeated UUIDs) + `new_tag_names` (comma string)
  - to `tags` (repeated names) + `new_tags` (comma string), matching `ui-case-tag-add`.
- Response remains a redirect to `ui-alert-detail` with a Django message.
- Empty submission still redirects with `Please select or type at least one tag.`
- Non-existent names in `tags` are created on assignment, as in the case UI.
- `TODO.md` §14.3 idempotency decision is explicitly out of scope for this plan.

---

## 7. UI/UX

- Use the case modal body as the reference structure:
  - `<fieldset class="field"><legend>Existing tags</legend> … </fieldset>`.
  - `<div class="chips chips-scroll">` with one checkbox per tag.
  - Checkbox `name="tags" value="{{ tag.name }}"`; label text is `{{ tag.name }}`.
  - Field hint: `Showing the first {{ available_tags|length }} tags; type to add others.`
  - New tag input: `name="new_tags"` with a comma-separated placeholder.
- Change the alert card hint from “Select existing tags from your org …” to “Select existing tags
  or create new ones …” unless Q1 selects strict org-scoped visibility.
- Change the alert add-tags dialog `size` from `sm` to `md` to match case detail.
- Keep dark theme, semantic markup, visible labels, icon-only controls named, and no inline JS.
- Without HTMX/JS, the dialog remains a real POST form; with JS, existing `modal.js`/`modal.css`
  behaviour is reused.

---

## 8. Phases and acceptance criteria

### Phase 0 — decision gate (planner/user)

**Q1 — RESOLVED:** Use the global `Tag` catalog exactly as case detail does. No case picker change.
**Q2 — RESOLVED:** Switch UI POST fields to `tags` (names) + `new_tags`; remove `existing_tag_ids`
and `new_tag_names`.
**Q3 — RESOLVED:** Keep audit/ledger/WebSocket write parity (`TODO.md` §14.6) out of scope.

**AC0.1:** MET — user accepted all recommended defaults on 2026-11-24 (“go with defaults”).

### Phase 1 — picker parity

**Task 1.1 — `ui/views/alerts.py::alert_detail`**

- Replace the `Q(alert_links…) | Q(case_links…) | Q(observable_links…)` picker queryset with:
  `Tag.objects.exclude(id__in=existing_tag_ids).order_by("name")[: PICKER_PAGE_SIZE + 1]`.
- Keep the fetch-one-extra truncation pattern from §14.11; do not reintroduce `alert_tags_total`.

**AC1.1-a:** For an alert with no tags, `response.context["available_tags"]` contains global tags
ordered by name, capped at `PICKER_PAGE_SIZE`.
**AC1.1-b:** Tags already on the alert are absent from `available_tags`.
**AC1.1-c:** More than `PICKER_PAGE_SIZE` candidates sets `available_tags_truncated = True`.
**AC1.1-d:** `alert_tags_total` is absent from context.

**Task 1.2 — `ui/templates/ui/_alert_tag_add_modal_body.html` and `ui/templates/ui/alert_detail.html`**

- Mirror case form field names and hint; update card hint; set dialog size to `md`.

**AC1.2-a:** Rendered dialog contains `name="tags"` checkboxes with tag names as values.
**AC1.2-b:** Rendered dialog contains `name="new_tags"` and no `existing_tag_ids`/`new_tag_names`.
**AC1.2-c:** Fieldset/legend and truncation hint match case detail.
**AC1.2-d:** Dialog still uses `form="alert-tag-add-form"` and plain POST fallback.

### Phase 2 — POST handling

**Task 2.1 — `ui/views/alerts.py::alert_tag_add`**

- Collect `request.POST.getlist("tags")` and comma-split `request.POST.get("new_tags")`.
- Strip, truncate to `MAX_TAG_NAME`, dedupe with `dict.fromkeys`, reject empty submissions.
- Inside one transaction: resolve existing tags by name with `get_or_create`, skip ids already
  linked, `bulk_create` new `AlertTagLink` rows.
- Keep `@not_viewer`, `@login_required`, `@require_POST`, and `resolve_alert(..., write=True)`.

**AC2.1-a:** Selecting an existing tag by name attaches it to the alert.
**AC2.1-b:** Typing new comma-separated names creates and attaches each tag once.
**AC2.1-c:** Duplicate names in `tags`/`new_tags` produce one link.
**AC2.1-d:** Names longer than `MAX_TAG_NAME` are clamped before write.
**AC2.1-e:** Empty submission redirects with an error and creates no tags/links.
**AC2.1-f:** A viewer POST is rejected and no links are created.

**Task 2.2 — remove legacy UI fields**

- Remove `existing_tag_ids`/`new_tag_names` from the alert view/template and update tests.

**AC2.2-a:** `grep -R "existing_tag_ids\\|new_tag_names" ui/` returns no matches.
**AC2.2-b:** No REST API code is changed.

### Phase 3 — tests and verification

**Task 3.1 — `tests/conformance/test_alert_tag_add_regressions.py`**

- Update existing POST tests to `new_tags`.
- Add: alert picker lists global tags; excludes current tags; caps at `PICKER_PAGE_SIZE`; existing
  name POST attaches; legacy UUID field is ignored/rejected as empty.
- Keep API non-object body regression intact.

**AC3.1-a:** New tests fail against the current code and pass after the change.
**AC3.1-b:** Existing alert/tag regression tests remain green after field-name updates.

**Task 3.2 — verification**

Commands:

```bash
.venv/bin/ruff check ui/views/alerts.py tests/conformance/test_alert_tag_add_regressions.py
.venv/bin/ruff format --check ui/views/alerts.py tests/conformance/test_alert_tag_add_regressions.py
.venv/bin/pytest -q tests/conformance/test_alert_tag_add_regressions.py tests/conformance/test_ui_alert_detail_tags.py tests/conformance/test_ui_case_verdict.py -k "not slow"
bash scripts/secret-scan.sh --staged
bash scripts/secret-scan.sh --tracked
```

**AC3.2-a:** Commands pass.
**AC3.2-b:** Full pytest is not run unless explicitly requested.
**AC3.2-c:** `TODO.md` §§14.1 and 14.9–14.11 are updated only after implementation, with §14.1
marked resolved-by-field-change and §14.9 hint wording updated to case parity.

---

## 9. Security

- The write path still resolves the alert with `resolve_alert(request.user, alert_id, write=True)`;
  cross-org superAdmin writes remain governed by existing scoping rules.
- Name-based fields remove malformed-UUID 500s and arbitrary UUID attachments from §14.1.
- Tag names are clamped to `Tag.name.max_length`, deduped, and created in a transaction.
- Viewer mutation remains blocked by `@not_viewer`.
- The global catalog exposes tag **names** to any user who can open an alert, exactly as case detail
  already does. If tag names are considered tenant-sensitive, Q1 must choose strict org visibility
  and the case picker should be aligned in the same change.
- No secrets in code/tests/docs; rerun both secret scans.

---

## 10. Testing

- Primary: `tests/conformance/test_alert_tag_add_regressions.py`.
- Secondary: `tests/conformance/test_ui_alert_detail_tags.py`,
  `tests/conformance/test_ui_case_verdict.py` (case picker parity reference).
- Manual smoke: open an alert with at least one global tag not on the alert → Existing tags list
  appears; check one tag and type one new tag → submit → both appear in the alert Tags block.
- No-JS smoke: open the dialog without `modal.js`, submit with a normal POST → redirect and tags
  appear.

---

## 11. Risks

- **Ambiguity Q1:** global catalog vs strict org-scoped visibility. Do not implement strict scoping
  silently.
- **Case parity mismatch:** case picker is global today; strict org scoping on alerts only would
  break “same as cases”.
- **Cached forms:** browsers with an old open tab may still submit legacy field names; clean switch
  is acceptable because the form is server-rendered and internal.
- **Deferred §14.6:** alert tag writes still lack `AuditLog`, linked-case ledger entry, and
  WebSocket frame; the Audit tab remains incomplete for alert tags until Phase 2/Q3.
- **Cap:** tags after `PICKER_PAGE_SIZE` require typing the exact name; disclosed by the hint.

---

## 12. Decisions (closed)

1. **Q1 — org definition:** RESOLVED — show the global `Tag` catalog exactly like case detail.
   No strict org scoping and no case picker change.
2. **Q2 — field contract:** RESOLVED — switch UI POST fields to case-style `tags` + `new_tags`,
   with no legacy aliases.
3. **Q3 — write parity:** RESOLVED — keep §14.6 (audit/ledger/WebSocket) out of scope.

---

## 13. Decision log

- 2026-11-24: user clarified that tag assignment is per organisation and an alert may be assigned
  any existing tag of its organisation, “same as cases”. Because `Tag` is global and case detail
  currently renders the global catalog, Q1 remains open.
- Recommended defaults for handoff: Q1 = global catalog mirroring case detail; Q2 = switch to name
  fields; Q3 = defer §14.6.
- 2026-11-24: user accepted the recommended defaults (“go with defaults”). Decision gate closed;
  status set to APPROVED and implementation handoff authorised.
- 2026-11-24: implementation completed. Planner review caught and corrected a missing-alert redirect
  crash and two vacuous name-attachment tests before closeout. Targeted ruff/format, 51 pytest cases,
  and both secret scans are green. No plan AC deviation; the redundant duplicate API non-object test
  was merged into the existing parametrized regression (hence 51 cases). `TODO.md` §14.4 recorded as
  moot under the global-catalog decision.
