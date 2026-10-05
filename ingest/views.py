"""Webhook receiver for Module A — the schema-agnostic "Gossamer" feed.

`POST /api/v1/alerts/webhook/<source_id>/` accepts raw JSON from any tool, on the terms the plan set
(AC4.1—AC4.8). Four decisions here are load-bearing and each one exists because of a specific failure
mode, not because the code is shaped that way:

* **Size is checked before parsing, and twice.** `Content-Length` is checked first because it costs
  nothing and rejects the bulk of oversized traffic without materialising a body. The body is then
  re-checked because a client controls that header: lying about it (or using chunked encoding, where
  it is absent) must not get past the cap.
* **The body must be a JSON *object*.** `[1, 2]` and `"a string"` are valid JSON, but an alert has
  fields, and a `payload.get(...)` on them raises `AttributeError` — a 500 for what is really a bad
  request from an untrusted sender. The receiver refuses non-objects up front.
* **The secret is verified with a password hasher.** `webhook_secret_hash` stores a hash, so
  comparing the presented secret against the column with `constant_time_compare` would compare a
  plaintext against a digest and *never* match. `check_password` is the only correct comparison, and
  it is timing-safe. Legacy rows holding a plaintext secret are still accepted (via constant-time
  compare) so an operator can migrate without a coordinated flag day; the branch is commented because
  it is a deliberate, temporary affordance and not an oversight.
* **Depth and size limits are enforced before the mapping engine walks the document.** The mapping
  engine recurses through the raw payload, so an unbounded nesting depth is a stack-depth hazard
  coming from the network.

The receiver is `@csrf_exempt` because a webhook is a machine-to-machine call with no session and no
CSRF token; it is *not* unauthenticated — see the secret check.
"""

from __future__ import annotations

import json
import time
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ingest.models import IngestionSource
from ingest.pipeline import store_ingested_alert

_JSON_CONTENT_TYPES = (
    "application/json",
    "application/cloudevents+json",
    "application/problem+json",
)


def _error(
    code: int,
    message: str,
    *,
    type_: str,
    fields: dict[str, list[str]] | None = None,
) -> JsonResponse:
    """TheHive-shaped error envelope, so one bad webhook does not look like two error dialects."""
    body: dict[str, Any] = {"message": message, "type": type_, "code": code}
    if fields:
        body["fields"] = fields
    return JsonResponse(body, status=code)


def _too_large() -> JsonResponse:
    return _error(413, "Payload too large", type_="entityTooLarge")


def _depth_exceeds(value: Any, limit: int, depth: int = 1) -> bool:
    """Whether `value` nests deeper than `limit`.

    Iterative rather than recursive: this runs on attacker-supplied input, and a recursive walk would
    turn a deep-but-legal document into a `RecursionError` *inside the guard* — a 500 instead of the
    400 it is meant to produce.
    """
    stack: list[tuple[Any, int]] = [(value, depth)]
    while stack:
        node, level = stack.pop()
        if level > limit:
            return True
        if isinstance(node, dict):
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend((child, level + 1) for child in node)
    return False


def _secret_matches(provided: str, stored: str) -> bool:
    """Compare a presented webhook secret against what `webhook_secret_hash` holds.

    The column is named for a hash, so the primary path is `check_password`. A value that is not a
    recognisable hasher output predates this check and holds the secret verbatim; it is compared in
    constant time so those rows keep working until their owners re-hash them.
    """
    try:
        identify_hasher(stored)
    except ValueError:
        return constant_time_compare(provided.encode("utf-8"), stored.encode("utf-8"))
    return check_password(provided, stored)


def _authenticate(request: HttpRequest, source: IngestionSource) -> HttpResponse | None:
    """Return an error response if the caller may not post to this source, else `None`."""
    stored = source.webhook_secret_hash or ""
    if not stored:
        return None
    presented = (
        request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Api-Key")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    if not presented:
        return _error(401, "Unauthorized", type_="unauthorized")
    if not _secret_matches(presented, stored):
        return _error(403, "Forbidden", type_="forbidden")
    return None


def _throttled(request: HttpRequest, source: IngestionSource) -> bool:
    """Whether this request exceeds the per-source or per-IP webhook rate limit.

    A webhook is unauthenticated from DRF's point of view, so `AnonRateThrottle` keys on `None` and
    would treat every sender as one caller. Keying on the source slug and the peer address separately
    means one noisy feed cannot exhaust the limit for every other feed, and one noisy network cannot
    exhaust it for every source.

    `cache.add` is the atomic test-and-set: a read-then-write would let a burst through under
    concurrency. Failure is treated as *not* throttled — a cache outage must not drop alerts.
    """
    ip = request.META.get("REMOTE_ADDR") or "unknown"
    window = int(time.time() // 60)
    for key, limit in (
        (f"wh:src:{source.slug}:{window}", settings.WEBHOOK_RATE_SOURCE_PER_MIN),
        (f"wh:ip:{ip}:{window}", settings.WEBHOOK_RATE_IP_PER_MIN),
    ):
        if limit <= 0:
            continue
        try:
            if not cache.add(key, 1, timeout=120):
                if cache.incr(key) > limit:
                    return True
        except ValueError:
            # The window expired between `add` and `incr`; the next request starts a fresh window.
            cache.set(key, 1, timeout=120)
        except Exception:
            return False
    return False


def _mapped(payload: dict[str, Any], mapping: dict[str, Any] | None, *keys: str) -> Any:
    """Read `payload` through `IngestionSource.mapping_config`, falling back to literal keys.

    Mapping first is the point of Module A: the operator configured a JSON-path, so honouring it is
    what makes a nested feed work. The literal fallbacks keep a source with no mapping working, which
    is also what the "schema-agnostic receiver" promise means in practice.
    """
    from ingest.mapping import extract_by_jsonpath

    if mapping:
        for key in keys:
            path = mapping.get(key)
            if isinstance(path, str) and path.strip():
                found = extract_by_jsonpath(path.strip(), payload)
                if found is not None:
                    return found
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _coerce_severity(value: Any, source: IngestionSource) -> int | None:
    """Severity must be an int in the model's 1—4 range, or `None` so the pipeline defaults it."""
    if value is None:
        return None
    try:
        severity = int(value)
    except (TypeError, ValueError):
        return None
    return severity if 1 <= severity <= 4 else None


@csrf_exempt
@require_POST
def webhook_alerts(request: HttpRequest, source_id: str) -> HttpResponse:
    """Accept one raw alert from a configured source (AC4.1—AC4.8)."""
    # Read through `settings` per request rather than caching in a module global: an operator who
    # changes `AMALTHEA_MAX_WEBHOOK_BYTES` (or a test that overrides it) must actually take effect,
    # and a value frozen at import time silently ignores both.
    max_size = settings.WEBHOOK_MAX_BODY_SIZE
    max_depth = settings.WEBHOOK_MAX_DEPTH

    declared = request.META.get("CONTENT_LENGTH")
    if declared is not None:
        try:
            if int(declared) > max_size:
                return _too_large()
        except ValueError:
            return _error(400, "Malformed Content-Length", type_="badRequest")

    content_type = request.content_type or ""
    if content_type and not any(content_type.startswith(t) for t in _JSON_CONTENT_TYPES):
        return _error(
            415,
            f"Unsupported Media Type: {content_type}",
            type_="unsupportedMediaType",
        )

    try:
        source = IngestionSource.objects.get(slug=source_id)
    except IngestionSource.DoesNotExist as exc:
        raise Http404(f"No ingestion source with slug {source_id!r}") from exc

    # Order matters: secret first, then throttle. Throttling first would let an anonymous flood
    # exhaust a *legitimate* source's budget and lock out its real feed — a denial of service against
    # ingestion that needs no credentials at all. The secret comparison is cheap, so checking it
    # ahead of the cache costs nothing and closes that hole.
    denied = _authenticate(request, source)
    if denied is not None:
        return denied

    if _throttled(request, source):
        return _error(429, "Webhook rate limit exceeded", type_="rateLimitExceeded")

    raw_body = request.body
    if len(raw_body) > max_size:
        return _too_large()

    try:
        payload: Any = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _error(400, "Malformed JSON", type_="badRequest", fields={"body": [str(exc)]})

    if not isinstance(payload, dict):
        return _error(
            400,
            "Payload must be a JSON object",
            type_="badRequest",
            fields={"body": [f"got {type(payload).__name__}"]},
        )

    if _depth_exceeds(payload, max_depth):
        return _error(
            400,
            f"Payload nests deeper than {max_depth} levels",
            type_="badRequest",
            fields={"body": ["nesting too deep"]},
        )

    mapping = source.mapping_config or {}
    title = _mapped(payload, mapping, "title", "summary", "message")
    severity = _coerce_severity(_mapped(payload, mapping, "severity", "severityLevel"), source)
    source_ref = _mapped(payload, mapping, "sourceRef", "source_ref", "id")
    alert_type = _mapped(payload, mapping, "type", "event", "alertType") or "generic"

    try:
        stored = store_ingested_alert(
            wire_source=str(payload.get("source") or source.slug),
            alert_type=str(alert_type)[:100],
            payload=payload,
            title=str(title or "Alert")[:200],
            mapped_source_ref=None if source_ref is None else str(source_ref),
            severity=severity if severity is not None else None,
            ingestion_source=source,
        )
    except ValidationError as exc:
        detail = getattr(exc, "message_dict", None) or {"nonFieldErrors": [str(exc)]}
        return _error(400, "Bad request", type_="badRequest", fields=detail)

    alert = stored.alert
    return JsonResponse(
        {
            "_id": str(alert.id),
            "id": str(alert.id),
            "_type": "alert",
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
            "ingestion_source": str(source.id),
            "created": stored.created,
            "warnings": stored.warnings,
        },
        # 201 when a new alert is stored, 200 when `payload` is recognised as a replay. The plan's
        # AC4.4 says a replay must not be an error; reporting it as created would overstate the
        # result, and a sender retrying under back-pressure needs to tell the two apart.
        status=201 if stored.created else 200,
    )
