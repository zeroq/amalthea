from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ingest.models import IngestionSource
from ingest.pipeline import store_ingested_alert

try:
    from rest_framework import status
except Exception:
    status = type(
        "S",
        (),
        {
            "HTTP_201_CREATED": 201,
            "HTTP_400_BAD_REQUEST": 400,
            "HTTP_401_UNAUTHORIZED": 401,
            "HTTP_403_FORBIDDEN": 403,
            "HTTP_413_REQUEST_ENTITY_TOO_LARGE": 413,
        },
    )()


_MAX_WEBHOOK_SIZE = getattr(settings, "WEBHOOK_MAX_BODY_SIZE", 5 * 1024 * 1024)  # 5MB default


@csrf_exempt
@require_POST
def webhook_alerts(request: HttpRequest, source_id: str) -> HttpResponse:
    """Webhook endpoint: POST /api/v1/alerts/webhook/<source_id>/

    - Lookup IngestionSource by slug
    - Check per-source secret if configured (401/403 on mismatch)
    - Enforce size cap before JSON parse (413 if too large)
    - Strict JSON only; return 400 with 'fields' on malformed/missing
    - Call store_ingested_alert and return 201 with alert data
    """
    # Size cap before parsing
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_WEBHOOK_SIZE:
                return JsonResponse(
                    {"message": "Payload too large", "type": "entityTooLarge", "code": 413},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except ValueError:
            pass

    raw_body = request.body
    if len(raw_body) > _MAX_WEBHOOK_SIZE:
        return JsonResponse(
            {"message": "Payload too large", "type": "entityTooLarge", "code": 413},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    # Parse JSON
    try:
        payload: Any = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (json.JSONDecodeError, UnicodeError) as exc:
        return JsonResponse(
            {
                "message": "Malformed JSON",
                "type": "badRequest",
                "code": 400,
                "fields": {"body": str(exc)},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Lookup source by slug
    try:
        source = get_object_or_404(IngestionSource, slug=source_id)
    except Exception:
        # Return 404-like as not found? But API shape - for now use generic
        return JsonResponse(
            {"message": "Not found", "type": "notFound", "code": 404},
            status=404,
        )

    # Check secret
    expected_secret = getattr(source, "webhook_secret_hash", "")
    provided_secret = (
        request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Api-Key")
        or request.POST.get("secret")
    )
    if expected_secret:
        if not provided_secret:
            return JsonResponse(
                {"message": "Unauthorized", "type": "unauthorized", "code": 401},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not constant_time_compare(str(provided_secret), str(expected_secret)):
            return JsonResponse(
                {"message": "Forbidden", "type": "forbidden", "code": 403},
                status=status.HTTP_403_FORBIDDEN,
            )

    # Extract basic fields from payload for mapping - pass through
    title = payload.get("title") or payload.get("summary") or payload.get("message") or "Alert"
    alert_type = payload.get("type") or payload.get("dataType") or payload.get("event") or "generic"
    severity = payload.get("severity") or payload.get("severityLevel")
    try:
        if severity is not None:
            severity = int(severity)
    except (ValueError, TypeError):
        severity = None
    mapped_source_ref = payload.get("sourceRef") or payload.get("source_ref") or payload.get("id")

    try:
        stored = store_ingested_alert(
            wire_source=payload.get("source") or source.slug,
            alert_type=str(alert_type),
            payload=payload,
            title=str(title)[:200],
            mapped_source_ref=mapped_source_ref,
            severity=severity,
            ingestion_source=source,
        )
    except ValidationError as exc:
        return JsonResponse(
            {
                "message": "Bad request",
                "type": "badRequest",
                "code": 400,
                "fields": exc.message_dict if hasattr(exc, "message_dict") else {"error": str(exc)},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        return JsonResponse(
            {"message": "Internal error", "type": "internalError", "code": 500},
            status=500,
        )

    alert = stored.alert
    response_data = {
        "_id": str(alert.id),
        "id": str(alert.id),
        "title": alert.title,
        "severity": alert.severity,
        "source": alert.source,
        "type": alert.type,
        "status": alert.status.value if alert.status else "New",
        "correlation_key": alert.correlation_key or "",
        "source_ref": alert.source_ref,
        "sourceRef": alert.source_ref,
        "date": alert.date.isoformat() if alert.date else None,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "ingestion_source": str(source.id) if source else None,
        "_type": "alert",
    }
    return JsonResponse(response_data, status=status.HTTP_201_CREATED)
