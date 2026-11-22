# Implementation Brief — Alert detail Similar tab

**Plan:** `TODO.md` §24 — Alert detail — Similar tab
**Decisions:** Q24-1…Q24-6 answered 2026-11-21; see `TODO.md §24.10`.
**Handoff to:** `django-backend` (similarity helper, view, route), `django-frontend` (tab/panel/table templates),
`qa-tester` (conformance suite), then `verifier` / `security-auditor`.

## Decisions applied

- Similarity channels: adapted SSDEEP + normalized-title gate **and** shared stored observables.
- No `correlation_key` signal and no new persisted score/model.
- Candidates: every `Alert` with `status.stage == "Closed"`, regardless of verdict value.
- Ordering: `updated_at` desc, then `date` desc, then `pk` desc. No `closed_at` field or migration.
- Result cap: 20; `similar_total` is the bounded union total before the final slice.
- UI: Similar tab after Observables, before Raw data, with a count badge.
- Table columns: Title, Verdict, Closed, Similarity.

## Tasks

### 1. `django-backend` — similarity helper in `cases/correlation.py`

- Add:

```python
SIMILAR_CLOSED_ALERTS_LIMIT = 20


@dataclass(frozen=True)
class SimilarAlertMatch:
    alert: Alert
    similarity: int | None  # fuzzy score 0-100, or None when only observables matched
    shared_observable_count: int  # 0 when only fuzzy matched


def alerts_similar_to_alert(
    source: Alert,
    *,
    user: User,
    limit: int = SIMILAR_CLOSED_ALERTS_LIMIT,
) -> tuple[list[SimilarAlertMatch], int]: ...
```

- Reuse existing constants/settings from `alerts_fuzzy_similar_to_case()`:
  - `MAX_FUZZY_COMPARISONS`
  - `MAX_CORRELATION_OBS`
  - `ALERT_FUZZY_SIMILARITY_THRESHOLD`
  - `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD`
  - `ALERT_FUZZY_TITLE_GATE_ENABLED`
- Base scope for both channels:
  - `status__stage="Closed"`, exclude `source.pk`.
  - `org_q("owner_org", user)` **and** `owner_org_id=source.owner_org_id`.
  - `select_related("status", "owner_org")`.
  - `order_by("-updated_at", "-date", "-pk")`.
- Fuzzy channel:
  - Skip if source `payload_ssdeep` empty.
  - Cap candidates at `MAX_FUZZY_COMPARISONS` before scoring.
  - Require candidate non-empty `payload_ssdeep`.
  - `ppdeep.compare(candidate.payload_ssdeep, source.payload_ssdeep)`.
  - Title gate uses `normalize_title_tokens()` + `jaccard_similarity()` from `alerts/title_similarity.py`, with the
    same pairwise semantics as the case Similar helper.
  - Import/comparison failures degrade to score `0`, matching existing behaviour.
- Observable channel:
  - Read source `AlertObservable` rows, excluding `observable__ignore_similarity=True`, cap at
    `MAX_CORRELATION_OBS`.
  - Query closed alerts through `AlertObservable` sharing those observable IDs, excluding
    `observable__ignore_similarity=True`.
  - Annotate `Count("observable_id", distinct=True)` as `shared_observable_count`.
  - Cap candidate alert rows at `MAX_FUZZY_COMPARISONS` after applying base ordering.
- Union:
  - Merge by `alert.id`; dual matches retain max fuzzy score and shared observable count.
  - `total_matching = len(union)` before final slice.
  - Return `(sorted(union.values(), key=base_order)[:limit], total_matching)`.

**Acceptance criteria**

- AC-B1: Closed-only, source excluded, caller-org and source-org scoped; superAdmin cannot compare cross-tenant.
- AC-B2: Fuzzy channel uses existing thresholds/title gate and bounded comparisons.
- AC-B3: Observable channel uses stored `AlertObservable`, excludes `ignore_similarity=True`, and is bounded.
- AC-B4: Dual matches preserve both signals; observable-only matches have `similarity=None`.
- AC-B5: Result ordering is `-updated_at`, `-date`, `-pk`; final cap is 20; total is pre-slice union size.
- AC-B6: Empty source signals return `([], 0)` without raising.

### 2. `django-backend` — alert detail context, fragment view, route

Files:

- `ui/views/alerts.py`
- `ui/urls.py`
- `ui/views/__init__.py`

Work:

- In `alert_detail()`:
  - Import/call `alerts_similar_to_alert(alert, user=request.user)`.
  - Add tab dict after `observables`: `{"slug": "similar", "label": "Similar", "count": similar_total}`.
  - Keep `general` default and invalid-tab fallback unchanged.
- Add `alert_similar_fragment(request, alert_id)`:
  - `@login_required`, `@require_GET`, viewer-allowed read-only.
  - `resolve_alert(request.user, alert_id, write=False)`; unknown/cross-org → 404.
  - Call helper with `limit=SIMILAR_CLOSED_ALERTS_LIMIT`.
  - Convert `SimilarAlertMatch` rows to simple template row dicts:
    - `alert`
    - `verdict_label = taxonomy_read.label(taxonomy_read.CATEGORY_CASE_VERDICT, alert.verdict or "")`, with a
      human fallback for empty tokens.
    - `closed_at = alert.updated_at`
    - `similarity_display`: `NN%`, `N shared obs`, or `NN% · N shared obs`.
  - Context: `alert`, `similar_alerts` (display rows), `similar_total`.
- Route: `alerts/<uuid:alert_id>/similar` → `alert_similar_fragment`, name `ui-alert-similar`.
- Export the view from `ui/views/__init__.py` if alert views are re-exported there.

**Acceptance criteria**

- AC-B7: `?tab=similar` activates the tab; invalid tabs still fall back to `general`.
- AC-B8: Tab order is General, Observables, Similar, Raw data.
- AC-B9: Badge count equals `similar_total` from the bounded union.
- AC-B10: Fragment route is GET-only, login-required, viewer-readable, and returns 404 for unknown/cross-org IDs.
- AC-B11: Fragment performs no mutation and emits no audit row.

### 3. `django-frontend` — templates and markup

Files:

- `ui/templates/ui/alert_detail.html`
- `ui/templates/ui/_alert_similar_tab_fragment.html` (new)
- `ui/templates/ui/_alert_similar_alerts_card.html` (new)

Work:

- In `alert_detail.html`:
  - Add the Similar tab entry after Observables, before Raw data.
  - Use existing badge rendering when `tab.count is not None`.
  - Use `fa-diagram-project` icon, matching the case Similar tab; icon must be hidden from assistive tech.
  - Add `panel-similar` with `data-lazy-src="{% url 'ui-alert-similar' alert.id %}"` when inactive.
  - When active via `?tab=similar`, render the fragment inline and mark the panel loaded, following the existing
    case/lazy-tab contract.
- Fragment root:
  - Carry `data-similar-count="{{ similar_total }}"` and `data-similar-alerts` for `tabs.js` parity.
- Card table:
  - `.card`, `<h2>Similar alerts</h2>`.
  - `.table-wrap` + `<table class="data-table data-table-tight">`.
  - `<caption class="sr-only">Closed alerts similar to this alert</caption>`.
  - `scope="col"` headers: Title, Verdict, Closed, Similarity.
  - Title links to `{% url 'ui-alert-detail' row.alert.id %}`.
  - Verdict uses `row.verdict_label`; Closed uses `row.closed_at|date:"Y-m-d H:i"`; Similarity uses
    `row.similarity_display`.
  - Honest truncation line only when `similar_total > rows|length`.
  - Empty state when no matches.

**Acceptance criteria**

- AC-F1: Tab position, label, badge, and icon match decisions.
- AC-F2: Lazy panel contract works with `ui/static/ui/tabs.js`; no new JS.
- AC-F3: Table markup passes conformance guard (`.data-table`, sr-only caption, `scope="col"`).
- AC-F4: Title is the only required link; no mutation controls or native dialogs.
- AC-F5: Dark theme, visible text labels, and accessibility conventions are preserved.

### 4. `qa-tester` — tests

New file: `tests/conformance/test_alert_similar_tab.py`.

Required coverage:

- Helper:
  - closed-only; exclude source; same-org and superAdmin cross-org source scoping.
  - fuzzy threshold/title gate; empty SSDEEP.
  - shared observable match; `ignore_similarity=True` exclusion; observable-only match when SSDEEP empty.
  - dual-channel union; score/count preservation.
  - ordering by `updated_at`, not similarity.
  - 20-row cap and pre-slice honest total.
- Fragment/view:
  - login redirect; viewer read allowed; no write/audit.
  - unknown/cross-org 404.
  - rendered title link, verdict label, Closed, Similarity display variants, empty state.
  - tab position, badge count, `?tab=similar` activation, invalid-tab fallback, lazy `data-lazy-src`.
- UI guards:
  - table markup, icons/icon font, alert-detail tags, URL smoke, tenant scoping.

Rerun:

```bash
.venv/bin/pytest tests/conformance/test_alert_similar_tab.py \
  tests/conformance/test_alert_fuzzy_similarity.py \
  tests/conformance/test_ui_similar_tab_lazy.py \
  tests/conformance/test_alert_observables_tab.py \
  tests/conformance/test_ui_table_markup.py \
  tests/conformance/test_ui_icons.py \
  tests/conformance/test_ui_icon_font.py \
  tests/conformance/test_ui_alert_detail_tags.py \
  tests/conformance/test_ui_urls_smoke.py \
  tests/conformance/test_tenant_scoping.py -q
```

**Acceptance criteria**

- AC-Q1: All new and rerun targeted suites pass.
- AC-Q2: `make check-fast` / `make check` passes with pinned `.venv`.
- AC-Q3: Secret scan clean.
- AC-Q4: Full pytest is reserved for the final DoD gate after implementation; do not run it from this brief unless
  explicitly requested.

## Constraints

- Use `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`.
- No schema change, migration, new dependency, REST endpoint, Celery task, WebSocket event, or persisted score.
- Preserve tenant scoping and all existing similarity caps.
- Never write secrets into repo files, tests, docs, fixtures, logs, or audit reports.
- Do not modify `TODO.md` §24 except to record deviations before merge.
- Do not use `--no-verify`, destructive `git reset`, or `git checkout`.

## Definition of done

- AC-B1…B11, AC-F1…F5, and AC-Q1…Q4 pass.
- Verification report records any deviation from `TODO.md §24.10`.
- Security review confirms read-only access, tenant isolation, bounded CPU/DB work, and no raw payload exposure.
- Full pytest and secret scan are clean before merge.
