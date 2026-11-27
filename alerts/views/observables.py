"""Observable attachment endpoints for alerts."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import AlertObservable
from core.serializers import observable_json
from observables.extractor import resolve_observable
from observables.models import ObservableType

from ._common import _actor, _bad, _not_found_alert, _resolve_alert_scoped


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_observable_add(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/observable` — attach artifacts to the alert itself (201).

    Two decisions worth the docstring, both recorded as deviations:

    * **No automation dispatch (P10-b).** `dispatch_observable_linked` needs a case, and an
      alert-only observable has none: it is a *candidate* artifact TheHive records against the
      alert until an import/merge gives it a case to fire playbooks for. Inventing a case id
      here would run the case's playbooks against evidence no case has claimed yet.
    * **Create-time options only.** `tlp`/`pap`/`ioc`/`sighted`/`message` are written when the
      row is *born* and never rewrite an existing row: `Observable` is globally deduplicated
      (Module C), so patching a shared artifact from one alert's payload would mutate evidence
      three other cases are reading. An unchanged value is reported honestly by the response.

    The body follows `InputCreateObservable`: `data` is a string or an array of strings; both
    spellings produce the same array-shaped 201, which is what `OutputObservable[]` promises.
    """
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")

    payload = request.data if isinstance(request.data, dict) else {}
    type_name = str(payload.get("dataType") or "")
    if not ObservableType.objects.filter(name=type_name).exists():
        return _bad(
            f"unknown dataType {payload.get('dataType')!r}",
            {"dataType": ["not in the observable vocabulary"]},
        )

    raw = payload.get("data")
    if isinstance(raw, list):
        items: list[Any] = list(raw)
    elif raw is None:
        items = []
    else:
        items = [raw]
    if not items:
        return _bad("data is required", {"data": ["required"]})
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            return _bad(
                "data must be a string or an array of strings",
                {"data": ["must be a string or an array of strings"]},
            )

    defaults: dict[str, Any] = {}
    if payload.get("message") is not None:
        defaults["message"] = str(payload["message"])
    for key in ("tlp", "pap"):
        if key in payload:
            try:
                value = int(payload[key])
            except (TypeError, ValueError):
                value = -1
            if not 0 <= value <= 3:
                return _bad(
                    f"{key} must be an integer between 0 and 3",
                    {key: ["must be an integer between 0 and 3"]},
                )
            defaults[key] = value
    for key in ("ioc", "sighted"):
        if key in payload:
            defaults[key] = bool(payload[key])

    with transaction.atomic():
        for item in items:
            resolved = resolve_observable(
                type_name, str(item), owner_org=alert.owner_org, defaults=defaults
            )
            if resolved is None:  # pragma: no cover — vocabulary checked above
                return _bad(
                    f"unknown dataType {type_name!r}",
                    {"dataType": ["not in the observable vocabulary"]},
                )
            observable, _created = resolved
            AlertObservable.objects.get_or_create(
                alert=alert, observable=observable, defaults={"added_by": _actor(request)}
            )

    links = alert.alert_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links], status=status.HTTP_201_CREATED)
