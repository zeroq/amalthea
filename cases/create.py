"""Request-free case creation, shared by the API and the UI (P1).

A case comes into existence one of two ways: promoted from an alert
(`alerts.escalation.import_alert_to_case`) or created directly. `POST /api/v1/case` has always been
able to do the latter; the analyst UI now has a form for it (`ui-case-create`). Both call
`create_case` here, for the same reason the *update* surface was collapsed into
`cases/update.apply_case_update` (deviation **W616P2-3**): one definition of the field set, the
enum bounds, the org-scoped assignee lookup, the tag through-table, extraction and the ledger
entry, so the two surfaces cannot drift.

Two behaviours are deliberately *not* new, because clients already depend on them:

* **Extraction is unconditional.** On every create, observable *suggestions* are staged
  from the title and description — that is what `_create_case` did before this module existed,
  and what `import_alert_to_case` does for a promoted alert. A payload-level
  `extract`/`observables` pair only *adds* explicit strings to the corpus (the recorded wire
  contract), it does not gate the text scan.
* **An empty enum keeps the model default.** A `null`/`""` `severity`, `tlp` or `pap` is dropped
  rather than rejected (ADR-002 §D11 tolerance), while a non-integer or out-of-range value is
  refused with a 400 instead of reaching a `CHECK` constraint as a 500 (SEC-2026-10-11 L1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from django.utils import timezone

from alerts.escalation import resolve_case_status
from cases.ledger import append_timeline_event
from cases.models import Case
from cases.update import (
    _CASE_RANGE_FIELDS,
    CASE_SCALAR_FIELDS,
    CaseUpdateError,
    _apply_case_tags,
    _set_case_fields,
    enforce_close_rule,
)
from core import taxonomy_read
from identity.models import Organisation, User
from observables.suggestions import create_suggestions_for_case

#: Status a case is created in when the payload names none. The escalation default, so a manual
#: case and a promoted alert start in the same stage.
DEFAULT_CASE_STATUS = "InProgress"

_MISSING_ORG = "User must belong to an organisation to create cases"


def _as_flag(value: Any) -> bool:
    """A wire value → bool without letting a string reach a boolean column.

    `bool("false")` is `True` and Django raises `ValidationError` (a 500) for `"yes"`, so the two
    readings the wire actually carries are spelled out: a JSON boolean, or an HTML form's `"on"`.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _create_scalars(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The applier-shaped half of a create payload.

    `title` is handled by `create_case` (it is the required field and is normalised there), so it
    is absent from the result. Everything else is passed to `_set_case_fields`, which owns the
    enum bounds, the tolerant status read and the assignee lookup.
    """
    scalars: dict[str, Any] = {}
    for key in CASE_SCALAR_FIELDS:
        if key == "title" or key not in payload:
            continue
        value = payload[key]
        if key in _CASE_RANGE_FIELDS and value in (None, ""):
            # Absent/null/empty means "the model default" — the tolerance this endpoint has
            # always had — not a rejection.
            continue
        enum_cat = {
            "scope": taxonomy_read.CATEGORY_CASE_SCOPE,
            "case_type": taxonomy_read.CATEGORY_CASE_TYPE,
            "data_classification": taxonomy_read.CATEGORY_DATA_CLASSIFICATION,
            "phase": taxonomy_read.CATEGORY_CASE_PHASE,
            "verdict": taxonomy_read.CATEGORY_CASE_VERDICT,
        }.get(key)
        if enum_cat is not None and value in (None, ""):
            # Same tolerance for string enums: empty/null keeps the model default.
            continue
        if key in ("description", "summary"):
            value = str(value or "")  # NOT NULL columns: a JSON `null` is an empty body
        elif key == "flag":
            value = _as_flag(value)
        scalars[key] = value
    # status: a falsy value ("" or None) means "the default" — exactly the pre-refactor
    # behaviour (str(payload.get("status") or "InProgress")). Forwarding "" would reach
    # resolve_case_status(""), which get_or_creates a CaseStatus with stage="",
    # violating the case_status_stage_valid CHECK constraint → IntegrityError → 500.
    # assignee: an empty value legitimately means "unassigned", so forward unchanged.
    if "assignee" in payload:
        scalars["assignee"] = payload["assignee"]
    if "status" in payload:
        status_val = payload["status"]
        if status_val not in (None, ""):
            scalars["status"] = status_val
    return scalars


@transaction.atomic
def create_case(
    payload: Mapping[str, Any],
    *,
    actor: User | None,
    org: Organisation | None,
) -> tuple[Case, list[str]]:
    """Create a case from a wire payload; return `(case, warnings)`.

    The caller supplies the org rather than the service reading it off the request: the route's
    identity is a request concern, the invariant "a case always has an owner organisation"
    (ADR-004) is this function's. Returns the warnings `resolve_case_status` recorded so a caller
    can surface an auto-created status instead of swallowing it.

    One transaction: the row, its tag links, its extracted observables and the "Case created"
    ledger entry either all land or none do. A case that exists without the ledger entry that
    announced it makes the ledger a decoration.

    Raises `CaseUpdateError("AuthorizationError", …)` when the actor has no organisation (the
    API's recorded 403) and `CaseUpdateError("BadRequest", …)` for a missing title or an
    out-of-range enum (the recorded 400).
    """
    if org is None:
        raise CaseUpdateError("AuthorizationError", _MISSING_ORG, {})

    title = str(payload.get("title") or "").strip()
    if not title:
        raise CaseUpdateError("BadRequest", "title is required", {"title": ["required"]})

    warnings: list[str] = []
    case = Case(
        status=resolve_case_status(DEFAULT_CASE_STATUS, warnings=warnings),
        start_date=timezone.now(),
        owner_org=org,
    )
    scalars = _create_scalars(payload)
    scalars["title"] = title[:500]
    # The update surface's applier, not a second copy of it (deviation W616P2-3): severity/TLP/PAP
    # bounds, the tolerant `status` read and the org-scoped `assignee` resolution behave here
    # exactly as they do on a PATCH of the same fields.
    # A create that lands directly in Closed must carry a verdict and a closing comment too
    # (Plan-2026-10-17 §12.8(4), decision D1) — otherwise "POST a case already closed" would
    # sidestep the rule a PATCH obeys.
    previous_stage = case.status.stage
    _set_case_fields(case, scalars, warnings=warnings, actor=actor)
    # The rule reads `verdict` (present in both shapes) and `comment` (a create-payload key
    # `_create_scalars` deliberately does not copy), so it is handed the wire payload.
    enforce_close_rule(case, payload, previous_stage=previous_stage)
    if warnings:
        case.ingestion_warnings = {"warnings": warnings}
    # A create that lands directly in Closed gets the same closed_date/end_date pair the update
    # surface stamps (Case.save() stamps closed_date itself; end_date is set here for parity).
    if case.status.stage == "Closed" and case.end_date is None:
        case.end_date = case.closed_date or timezone.now()
    # Saved before the tag links: a `CaseTagLink` needs a pk (the same ordering
    # `apply_case_update` gets for free from the case it is handed).
    case.save()
    _apply_case_tags(case, payload)

    extra = (
        [str(value) for value in (payload.get("observables") or [])]
        if payload.get("extract") or payload.get("observables")
        else []
    )
    create_suggestions_for_case(case, case.title, case.description, *extra)

    append_timeline_event(
        case,
        title="Case created",
        description=case.description[:500],
        kind="case-created",
        actor=actor,
    )
    case.refresh_from_db()
    return case, warnings
