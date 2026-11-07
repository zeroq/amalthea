"""Ingestion source list and mapping editor views."""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from alerts.models import Alert
from identity.audit import maybe_record_cross_org_read
from identity.models import IngestionToken
from identity.scoping import (
    is_admin,
    org_q,
)
from ingest.models import IngestionSource
from ui.pagination import paginate

from .common import (
    _base_context,
    _require_admin_ui,
    not_viewer,
)

logger = logging.getLogger(__name__)


@login_required
@require_GET
def sources_list(request: HttpRequest) -> HttpResponse:
    """Configured feeds and how many alerts each has produced (org-scoped)."""
    from django.db.models import Exists, OuterRef

    user_org_id = getattr(request.user, "org_id", None)
    if user_org_id is None:
        alert_filter = Q(pk__in=[])
    else:
        alert_filter = Q(alerts__owner_org_id=user_org_id)

    # Whether a feed can receive alerts is not a boolean (§10a): a token is usable only while it is
    # neither revoked nor deactivated, and a deactivated one can come back. So three states:
    #   active — a usable token exists                    → "required"
    #   paused — no usable token, but one could resume     → "paused"
    #   none   — no token at all, or only revoked ones     → "none"
    # This replaces the legacy `webhook_secret_hash` check: the receiver authenticates via
    # `IngestionToken` rows, so "can this feed receive anything?" is a question about tokens.
    active_token_subquery = IngestionToken.objects.filter(
        source=OuterRef("pk"),
        revoked_at__isnull=True,
        deactivated_at__isnull=True,
    )
    paused_token_subquery = IngestionToken.objects.filter(
        source=OuterRef("pk"),
        revoked_at__isnull=True,
        deactivated_at__isnull=False,
    )

    from ingest.mapping import read_rules

    queryset = (
        # ADR-009: org-owned, so the list is scoped like every other tenant row (superAdmin: all).
        IngestionSource.objects.filter(org_q("organisation", request.user))
        .annotate(
            alert_count=Count("alerts", filter=alert_filter),
            has_active_token=Exists(active_token_subquery),
            has_paused_token=Exists(paused_token_subquery),
        )
        .order_by("slug")
    )

    # No filters on this list currently, so filtered_from is None
    page_obj = paginate(request, queryset)
    filtered_from = None

    sources = list(page_obj.object_list)
    for source in sources:
        # Display-only attributes: "7 rules · severity←severity_name" (§7). The count is the honest
        # "how much is configured", and the severity rule is the one an analyst checks first.
        rules = read_rules(source.mapping_config)
        source.rule_count = len(rules)  # type: ignore[attr-defined]
        severity_rule = rules.get("severity") or {}
        source.severity_rule = (
            severity_rule.get("transform")
            or (  # type: ignore[attr-defined]
                (severity_rule.get("paths") or [None])[0]
            )
        )

    return render(
        request,
        "ui/sources_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            sources=sources,
            # The detail page is admin-only, so a viewer/analyst gets plain text rather than a link
            # that would 404 (§7: "admin for own-org sources, everyone else never").
            can_open_source_page=is_admin(request.user),
        ),
    )


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def settings_source_detail(request: HttpRequest, source_id) -> HttpResponse:
    """One feed: identity, its editable rules, and the stored config (§7, P2 shell + P3 editor).

    P2 landed the read-only half — identity, rule summary, tokens, stored JSON — so the tenancy gate
    (role **and** org) was tested before a write path existed. P3 adds the two writers over the same
    object: the table (one row per target) and the advanced JSON box, which is the only place
    `value_maps` is edited (plan Q3). Neither writes anything else: every other top-level key survives
    a save verbatim (`ingest/editor.py` rule 1).

    Uniform 404 for "no such source" and "another org's source" (`resolve_ingestion_source`), and the
    cross-org *read* by a superAdmin is audited (ADR-004 Q5) — a mapping is a tenant's data, so
    peeking at another tenant's rules is a break-glass read, not a routine one. A superAdmin's
    cross-org *write* is not a separate action: the `ingestion_source.updated` row already carries both
    `actor_org` and `target_org`, so the crossover is visible where the change is recorded.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    from identity.scoping import resolve_ingestion_source

    # `write=True` on POST layers the viewer deny over the role gate above (ADR-006 Layer 1/2); the
    # page itself is already unreachable for a viewer, so this is depth, not the boundary.
    source = resolve_ingestion_source(request.user, source_id, write=request.method == "POST")
    if source is None:
        return render(request, "ui/404.html", status=404)

    if request.method == "POST":
        return _source_mapping_post(request, source)

    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=source,
        target_type="IngestionSource",
        target_id=source.id,
        target_org_field="organisation",
    )

    active_tab = request.GET.get("tab", "mapping")
    if active_tab not in ("mapping", "raw-data"):
        active_tab = "mapping"

    latest_alert: Alert | None = None
    latest_raw_payload: str | None = None
    if active_tab == "raw-data":
        latest_alert = (
            Alert.objects.filter(ingestion_source=source).order_by("-date", "-created_at").first()
        )
        if latest_alert is not None:
            latest_raw_payload = json.dumps(latest_alert.raw_payload, indent=2, sort_keys=True)

    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                active_tab=active_tab,
                latest_alert=latest_alert,
                latest_raw_payload=latest_raw_payload,
            ),
        ),
    )


def _source_detail_context(
    request: HttpRequest,
    source,
    *,
    submitted: dict[str, dict[str, str]] | None = None,
    errors: dict[str, tuple[str, ...]] | None = None,
    observable_submitted: dict[str, str] | None = None,
    observable_errors: dict[str, tuple[str, ...]] | None = None,
    failed_form: str = "",
    json_text: str | None = None,
    apply_pending: object | None = None,
    pending_config_json: str | None = None,
    active_tab: str = "mapping",
    latest_alert: Alert | None = None,
    latest_raw_payload: str | None = None,
) -> dict:
    """Everything the source page renders, for both the GET and a rejected POST.

    One builder for both, so a form that re-renders after an error cannot drift from the page the
    operator was looking at: `submitted` replaces the stored config in the table's rows and `errors`
    lands on the row that caused it (`ingest/editor.py`).

    `apply_pending` (a `RemapSummary`) is the confirmation dialog's half of the same page: it is set by
    the `intent=apply` POST, which is the only render in which the dialog exists at all — so a page
    reached by GET can never show a confirm control for a write nobody asked for.
    """
    from ingest.editor import TARGET_LABELS, editor_rows, transforms_for_editor
    from ingest.mapping import TARGETS, config_shape, read_rules
    from ingest.observable_editor import observable_editor_rows
    from ingest.remap import MAX_REMAP_ALERTS, REMAP_FIELDS, mapping_health
    from observables.models import ObservableType
    from ui.json_tree import is_valid_jsonpath

    errors = errors or {}
    observable_errors = observable_errors or {}
    observable_types = list(ObservableType.objects.order_by("name"))

    # Tokens rail (R surface) — cap with honest total (Q3/Q4, D1)
    from ui.pagination import RAIL_CAP

    tokens_qs = (
        source.tokens.select_related("organisation", "created_by")
        .defer("secret_hash")
        .order_by("-created_at")
    )
    tokens_total = tokens_qs.count()
    tokens = list(tokens_qs[:RAIL_CAP])

    erows = editor_rows(source.mapping_config, submitted=submitted, errors=errors)
    obs_rows = observable_editor_rows(
        source.mapping_config,
        observable_types,
        submitted=observable_submitted,
        errors=observable_errors,
    )

    # --- Raw-data click-to-map prefill (TODO §17) --------------------------------
    # A GET from the raw-data tab asks to add one JSONPath to a mapping row. It is a
    # *prefill* only: the path lands in the unsaved textarea, and the existing Save POST
    # remains the sole writer. Invalid input is ignored rather than raising.
    prefilled: dict[str, str] | None = None
    if active_tab == "mapping":
        map_target = request.GET.get("map_target", "")
        map_path = request.GET.get("map_path", "")
        map_observable_type = request.GET.get("map_observable_type", "")
        if is_valid_jsonpath(map_path):
            if map_target in TARGET_LABELS:
                prefilled = {"target": map_target, "observable_type": "", "path": map_path}
                _prefill_row(erows, "target", map_target, map_path)
            elif map_target == "observable" and map_observable_type in {
                ot.name for ot in observable_types
            }:
                prefilled = {
                    "target": "observable",
                    "observable_type": map_observable_type,
                    "path": map_path,
                }
                _prefill_row(obs_rows, "type", map_observable_type, map_path)

    return {
        "source": source,
        # `legacy` is the one shape the page must explain rather than silently upgrade (§7 rule 4).
        "config_shape": config_shape(source.mapping_config),
        "editor_rows": erows,
        "editor_errors": errors,
        # The whole-config messages of whichever form was submitted: `validate_config` has no row to
        # attach `value_maps` (or a JSON parse failure) to, so they are shown above it.
        "config_errors": errors.get("", ()),
        "observable_rows": obs_rows,
        "observable_errors": observable_errors,
        "observable_config_errors": observable_errors.get("", ()),
        "failed_form": failed_form,
        "mapping_json_text": (
            json_text
            if json_text is not None
            else json.dumps(source.mapping_config or {}, indent=2, sort_keys=True)
        ),
        "transforms": transforms_for_editor(),
        "rule_count": len(read_rules(source.mapping_config)),
        "tokens": tokens,
        "tokens_total": tokens_total,
        "alert_count": Alert.objects.filter(ingestion_source=source).count(),
        # --- P5: what the stored records say, before anyone opens nineteen alerts -------------------
        "mapping_health": mapping_health(source),
        "is_superadmin": request.user.role == "superAdmin",
        # --- P7: the "apply to existing alerts" confirmation dialog -------------------------------
        "apply_pending": apply_pending,
        "apply_confirm_fields": (
            {"intent": "apply_confirmed", "pending_config": pending_config_json or "{}"}
            if apply_pending is not None
            else None
        ),
        # Over the cap the dialog still opens — but its confirm control is disabled (server-side) and
        # nothing is written. A "save & apply" that quietly drops the apply half is the worse failure.
        "apply_confirm_disabled": bool(apply_pending is not None and apply_pending.over_cap),
        "apply_fields": [(target, TARGET_LABELS.get(target, target)) for target in REMAP_FIELDS],
        # Rules for targets a re-map never rewrites (`type`, `source_ref` — MAP-9). The dialog names them,
        # because "I added a ref mapping and nothing changed" is otherwise a puzzle: the rule is real, and
        # it only ever applies to alerts ingested *after* it was saved.
        #
        # Read from the **candidate** config, not the stored one: the dialog exists precisely for rules
        # that have not been saved yet, so a disclosure computed from the stored config would never fire
        # on the one render where it is needed.
        "apply_ignored_rules": [
            (target, TARGET_LABELS.get(target, target), tuple(rule.get("paths") or ()))
            for target, rule in read_rules(
                json.loads(pending_config_json) if pending_config_json else source.mapping_config
            ).items()
            if target not in REMAP_FIELDS
        ],
        "remap_cap": MAX_REMAP_ALERTS,
        # Source detail tabs (PLAN-2026-11-06-source-detail-raw-data).
        "active_tab": active_tab,
        "latest_alert": latest_alert,
        "latest_raw_payload": latest_raw_payload,
        # Raw-data click-to-map (TODO §17): the parsed payload feeds the JSON tree; the
        # raw `<pre>` above stays as the no-JS fallback.
        "latest_raw_data": latest_alert.raw_payload if latest_alert is not None else None,
        "map_targets": [(t, TARGET_LABELS[t]) for t in TARGETS],
        "observable_types": observable_types,
        "prefilled": prefilled,
    }


def _prefill_row(rows: list[dict], key: str, value: str, path: str) -> None:
    """Append *path* to the editor row whose ``key`` equals ``value`` (dedup, newline-separated)."""
    for row in rows:
        if row.get(key) == value:
            existing = [p.strip() for p in row.get("paths", "").splitlines() if p.strip()]
            if path not in existing:
                existing.append(path)
            row["paths"] = "\n".join(existing)
            return


def _source_mapping_rows(request: HttpRequest) -> dict[str, dict[str, str]]:
    """The table's submission as `{target: {paths, transform, default}}` (`ingest/editor.py`)."""
    from ingest.mapping import TARGETS

    return {
        target: {
            "paths": request.POST.get(f"paths__{target}", ""),
            "transform": request.POST.get(f"transform__{target}", ""),
            "default": request.POST.get(f"default__{target}", ""),
        }
        for target in TARGETS
    }


def _source_mapping_post(request: HttpRequest, source) -> HttpResponse:
    """Route one POST to the mapping page to the right half of it (P3 save, P7 apply).

    `intent` is the discriminator, and its default is the *old* behaviour: the Save buttons carry no
    `intent` at all, so every existing submission lands on the save path unchanged.
    """
    if request.POST.get("form") == "observables":
        return _save_observable_rules(request, source)
    intent = request.POST.get("intent") or "save"
    if intent == "apply":
        # Validates and *counts*, then opens the confirmation dialog. Writes nothing at all.
        return _confirm_mapping_apply(request, source)
    if intent == "apply_confirmed":
        return _apply_mapping_to_alerts(request, source)
    return _save_source_mapping(request, source)


def _submitted_mapping(request: HttpRequest, before: dict) -> tuple[str, dict | None, object]:
    """Read whichever form was posted into a validated `EditorResult`.

    `form=table|json` says which writer spoke; the table is rebuilt *from* the stored config so an
    unsubmitted row keeps its rule, and the JSON box is parsed as-is (§7.4).
    """
    from ingest.editor import build_table_config, parse_json_config

    which = "json" if request.POST.get("form") == "json" else "table"
    submitted = None if which == "json" else _source_mapping_rows(request)
    if which == "json":
        result = parse_json_config(request.POST.get("mapping_json"))
    else:
        result = build_table_config(before, submitted)
    return which, submitted, result


def _reject_mapping(
    request: HttpRequest,
    source,
    *,
    which: str,
    submitted: dict | None,
    result: object,
    json_text: str | None = None,
) -> HttpResponse:
    """The only rejection response: a 200 that re-renders the page *with the operator's input*.

    A redirect would discard what they typed, which is the entire value of per-row errors (AC11/AC20).
    """
    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                submitted=submitted,
                errors=result.errors,
                failed_form=which,
                json_text=json_text,
            ),
        ),
    )


def _observable_rows_from_post(request: HttpRequest) -> dict[str, str]:
    """The card's textareas, keyed by the current vocabulary name."""
    from observables.models import ObservableType

    return {
        name: request.POST.get(f"observable_paths__{name}", "")
        for name in ObservableType.objects.order_by("name").values_list("name", flat=True)
    }


def _reject_observables(
    request: HttpRequest,
    source,
    *,
    submitted: dict[str, str],
    result: object,
) -> HttpResponse:
    """Echo the observable card's input and per-row errors without writing anything."""
    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                observable_submitted=submitted,
                observable_errors=result.errors,
                failed_form="observables",
            ),
        ),
    )


def _save_observable_rules(request: HttpRequest, source) -> HttpResponse:
    """Save one edit of the top-level `observables` list, preserving every sibling key.

    This is a third writer over the same store, not a replacement for the table or JSON form. It owns
    exactly one key, so a field-mapping change and an observable-rule change cannot overwrite each
    other; the no-op path writes no row and no audit event, matching the other two writers (AC24).
    """
    from ingest.observable_editor import (
        build_observable_config,
        observable_save_is_noop,
    )
    from observables.models import ObservableType

    types = list(ObservableType.objects.order_by("name"))
    submitted = _observable_rows_from_post(request)
    before = source.mapping_config or {}
    result = build_observable_config(before, submitted, types)
    if not result.ok:
        return _reject_observables(request, source, submitted=submitted, result=result)

    if observable_save_is_noop(before, result.config):
        messages.info(request, "No changes to save.")
        return redirect("ui-settings-source-detail", source_id=source.id)

    with transaction.atomic():
        _persist_mapping(request, source, before, result.config)

    count = len(result.config.get("observables") or [])
    if count:
        messages.success(
            request,
            f"Observable rules saved — {count} type{'' if count == 1 else 's'}.",
        )
    else:
        messages.success(request, "Observable rules cleared.")
    return redirect("ui-settings-source-detail", source_id=source.id)


def _persist_mapping(request: HttpRequest, source, before: dict, config: dict) -> None:
    """Write the config and record the edit. The **caller** owns the transaction (P7 shares it)."""
    from identity.audit import record_ingestion_source_updated
    from ingest.editor import changed_keys

    source.mapping_config = config
    # `updated_at` is `auto_now`: its value is set on save, but an `update_fields` UPDATE writes only the
    # columns it names, so the timestamp has to be named (AC12 reads it).
    source.save(update_fields=["mapping_config", "updated_at"])
    record_ingestion_source_updated(
        request,
        actor=request.user,
        source=source,
        changed_keys=changed_keys(before, config),
    )


def _mapping_saved_message(request: HttpRequest, config: dict) -> None:
    rules = len(config.get("fields") or {}) if isinstance(config, dict) else 0
    if rules:
        messages.success(request, f"Mapping saved — {rules} rule{'' if rules == 1 else 's'}.")
    else:
        messages.success(request, "Mapping saved — no rules; payload literal keys are used.")


def _save_source_mapping(request: HttpRequest, source) -> HttpResponse:
    """Persist one edit of a feed's `mapping_config` (P3, AC11-AC14).

    Two writers, one object: the table and the advanced JSON box are separate `<form>`s, so a request
    carries exactly one of them (`form=table|json`) and the other half of the page keeps rendering the
    stored config — "one storage object, two views of it, never two writers" (plan §7.4).

    A rejected save is a **200 that echoes the input**; success is PRG; and an unchanged submission is a
    no-op: no row write, no `updated_at` bump, no audit (AC12).
    """

    from ingest.editor import is_noop_save

    before = source.mapping_config or {}
    which, submitted, result = _submitted_mapping(request, before)
    if not result.ok:
        return _reject_mapping(
            request,
            source,
            which=which,
            submitted=submitted,
            result=result,
            json_text=request.POST.get("mapping_json") if which == "json" else None,
        )

    if is_noop_save(before, result.config):
        messages.info(request, "No changes to save.")
        return redirect("ui-settings-source-detail", source_id=source.id)

    with transaction.atomic():
        _persist_mapping(request, source, before, result.config)

    _mapping_saved_message(request, result.config)
    return redirect("ui-settings-source-detail", source_id=source.id)


def _confirm_mapping_apply(request: HttpRequest, source) -> HttpResponse:
    """`intent=apply` — validate the submitted mapping, count the candidates, open the dialog (P7, AC20).

    The whole point is what this does **not** do: no config write, no alert write, no audit row. The
    operator asked to apply a mapping; they have not yet agreed to rewrite anything, so the page comes
    back with the exact numbers and the exact field list and the *confirm* control carries the validated
    config (a hidden `pending_config`) — not the table, which cannot be re-read across two requests.

    The numbers come from a **dry pass over the real alerts** (`preview_alerts`), not from the rule table:
    "19 alerts, these fields" is true and useless when a rule cannot read the payload, so each target is
    reported with what it resolves and what it would change (P7.1, AC24). Reading is the whole cost; the
    candidates are capped (`MAX_REMAP_ALERTS`), so this cannot walk an unbounded set.
    """
    from ingest.remap import preview_alerts

    before = source.mapping_config or {}
    which, submitted, result = _submitted_mapping(request, before)
    if not result.ok:
        return _reject_mapping(
            request,
            source,
            which=which,
            submitted=submitted,
            result=result,
            json_text=request.POST.get("mapping_json") if which == "json" else None,
        )

    return render(
        request,
        "ui/source_detail.html",
        _base_context(
            request,
            **_source_detail_context(
                request,
                source,
                # The page keeps rendering what is *stored* — this POST saved nothing — while the dialog
                # carries the candidate config. Two different objects, deliberately not conflated.
                apply_pending=preview_alerts(source, result.config),
                pending_config_json=json.dumps(result.config, sort_keys=True),
            ),
        ),
    )


def _apply_mapping_to_alerts(request: HttpRequest, source) -> HttpResponse:
    """`intent=apply_confirmed` — save the mapping and re-map the feed's unlinked open alerts (P7).

    One transaction for both halves (AC22): the rules that produced the new values and the values
    themselves commit together, so a feed can never end up stored one way and applied the other. The
    config arrives back from the client and is therefore **re-validated** here — the same check the save
    path runs, so the client cannot smuggle a config past the editor.

    The result is reported in full, because "unchanged" is ambiguous: a row can keep its values and still
    have needed a write (its `mapping` record was stale), and a *rule* can have produced nothing at all —
    which is the only honest explanation for an operator whose new severity rule changed no severity
    (P7.1, AC25).
    """

    from identity.audit import record_ingestion_source_mapping_applied
    from ingest.editor import changed_keys, is_noop_save, parse_json_config
    from ingest.remap import MAX_REMAP_ALERTS, remap_alerts, summarise_candidates

    pending = request.POST.get("pending_config")
    result = parse_json_config(pending)
    if not result.ok:
        # Tampered or hand-edited between the two steps: same treatment as any rejected submission.
        return _reject_mapping(
            request, source, which="json", submitted=None, result=result, json_text=pending
        )

    summary = summarise_candidates(source)
    if summary.over_cap:
        # Only reachable when the candidate count grew between rendering the dialog and confirming it
        # (the dialog disables its own confirm control over the cap). Refuse rather than write a partial
        # pass, and let the operator see the new count by pressing Apply again.
        messages.error(
            request,
            f"Not applied: this feed now has {summary.candidates} unlinked open alerts, over the "
            f"{MAX_REMAP_ALERTS}-per-request limit. Narrow the feed or use the API.",
        )
        return redirect("ui-settings-source-detail", source_id=source.id)

    before = source.mapping_config or {}
    with transaction.atomic():
        if not is_noop_save(before, result.config):
            _persist_mapping(request, source, before, result.config)
        counts = remap_alerts(source, result.config)
        record_ingestion_source_mapping_applied(
            request,
            actor=request.user,
            source=source,
            changed_keys=changed_keys(before, result.config),
            candidates=counts.candidates,
            updated=counts.updated,
            unchanged=counts.unchanged,
            refreshed=counts.refreshed,
        )

    _mapping_saved_message(request, result.config)
    if counts.candidates:
        messages.success(
            request,
            f"Re-applied to {counts.candidates} unlinked open alert"
            f"{'' if counts.candidates == 1 else 's'}: {counts.updated} updated, "
            f"{counts.unchanged} unchanged.",
        )
        if counts.touched:
            messages.info(request, f"Fields written: {', '.join(counts.touched)}.")
        if counts.refreshed:
            messages.info(
                request,
                f"Rule state refreshed on {counts.refreshed} alert"
                f"{'' if counts.refreshed == 1 else 's'} with no field values changed.",
            )
        if counts.observables_added:
            messages.info(
                request,
                f"Added {counts.observables_added} alert observable"
                f"{'' if counts.observables_added == 1 else 's'}.",
            )
        for line in _unresolved_lines(counts.unresolved, counts.candidates):
            messages.warning(request, line)
    else:
        messages.info(request, "No unlinked open alerts to re-map.")
    return redirect("ui-settings-source-detail", source_id=source.id)


def _unresolved_lines(unresolved: dict[str, dict[str, int]], candidates: int) -> list[str]:
    """One line per target whose rule produced no value, for the flash message (P7.1, AC25).

    The reason is named rather than counted, because it is the fix: `bad_type` on a 0-100 vendor score
    means "you need the `severity_score` transform", and that is not something a bare "0 updated" can
    ever say. Targets are listed in rule order and reasons alphabetically, so the message is stable
    enough to assert on.
    """
    lines = []
    for target in sorted(unresolved):
        reasons = unresolved[target]
        count = max(reasons.values())
        named = ", ".join(f"{reason}" for reason in sorted(reasons))
        lines.append(
            f"{target}: the mapping produced no value on {count} of {candidates} "
            f"alert{'' if candidates == 1 else 's'} ({named}) — those alerts keep what they have."
        )
    return lines
