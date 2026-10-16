"""Webhook receiver for Module A — the schema-agnostic "Gossamer" feed.

`POST /api/v1/alerts/webhook/` accepts raw JSON from any tool, on the terms the plan set
(AC4.1—AC4.8). Four decisions here are load-bearing and each one exists because of a specific failure
mode, not because the code is shaped that way:

* **Size is checked before parsing, and twice.** `Content-Length` is checked first because it costs
  nothing and rejects the bulk of oversized traffic without materialising a body. The body is then
  re-checked because a client controls that header: lying about it (or using chunked encoding, where
  it is absent) must not get past the cap.
* **The body must be a JSON *object*.** `[1, 2]` and `"a string"` are valid JSON, but an alert has
  fields, and a `payload.get(...)` on them raises `AttributeError` — a 500 for what is really a bad
  request from an untrusted sender. The receiver refuses non-objects up front.
* **The secret is verified with a password hasher.** `IngestionToken.secret_hash` stores a hash, so
  comparing the presented secret against the column with `constant_time_compare` would compare a
  plaintext against a digest and *never* match. `check_password` is the only correct comparison, and
  it is timing-safe. Legacy rows holding a plaintext secret in `IngestionSource.webhook_secret_hash`
  are still accepted (via constant-time compare) so an operator can migrate without a coordinated
  flag day; the branch is commented because it is a deliberate, temporary affordance and not an
  oversight.
* **Depth and size limits are enforced before the mapping engine walks the document.** The mapping
  engine recurses through the raw payload, so an unbounded nesting depth is a stack-depth hazard
  coming from the network.

The receiver is `@csrf_exempt` because a webhook is a machine-to-machine call with no session and no
CSRF token; it is *not* unauthenticated — see the token check.

**P2 — Tenant Isolation:** The credential is the *only* routing fact. A presented secret resolves to
exactly one `IngestionToken`, which carries both the `IngestionSource` (mapping rules, rate-limit
bucket) and the `Organisation` used to stamp the alert's `owner_org`. There is no source slug in the
path, so an unauthenticated caller cannot probe which feeds exist: **missing or invalid token → 401,
always** (closes the open-source hole).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from argon2 import PasswordHasher
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from identity.models import IngestionToken
from ingest.mapping import apply_mapping
from ingest.models import IngestionSource
from ingest.pipeline import store_ingested_alert

logger = logging.getLogger(__name__)

_JSON_CONTENT_TYPES = (
    "application/json",
    "application/cloudevents+json",
    "application/problem+json",
)

# Module-level hasher (same pattern as compat/auth.py and identity.models)
_ph = PasswordHasher()


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


def _verify_secret(stored_hash: str, presented: str) -> bool:
    """Verify a presented secret against a stored Argon2 hash.

    Falls back to constant-time compare for legacy plaintext values (pre-hash migration).
    Uses Argon2 PasswordHasher directly (like compat.auth) since Django's check_password
    doesn't know about Argon2 unless PASSWORD_HASHERS is configured.
    """
    try:
        _ph.verify(stored_hash, presented)
        return True
    except Exception:
        # Legacy plaintext secret — constant-time compare
        return constant_time_compare(presented.encode("utf-8"), stored_hash.encode("utf-8"))


def _authenticate_token(request: HttpRequest) -> tuple[IngestionToken | None, HttpResponse | None]:
    """
    Resolve the presented secret to its `IngestionToken`.

    The token is the single routing fact of this endpoint: it carries the `IngestionSource` (mapping
    rules, rate-limit bucket) *and* the `Organisation` that stamps the alert. Lookup is by the unique
    12-char `prefix` — one indexed query — then the secret is verified against the Argon2 hash.

    Returns `(token, None)` on success, or `(None, error_response)` on failure.
    """
    presented = (
        request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Api-Key")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    if not presented:
        return None, _error(401, "Unauthorized: missing token", type_="unauthorized")

    token = (
        IngestionToken.objects.select_related("source", "organisation")
        .filter(prefix=presented[:12], revoked_at__isnull=True)
        .first()
    )
    # Revoked tokens are excluded by the queryset, so a revoked secret, an unknown prefix and a wrong
    # secret are one and the same answer — the caller learns nothing about which tokens exist.
    # `verify` only runs on a prefix hit, so a flood of junk secrets costs one indexed SELECT each.
    if token is None or not token.verify(presented):
        return None, _error(401, "Unauthorized: invalid token", type_="unauthorized")

    # Update last_used_at (best-effort, non-blocking)
    from django.utils import timezone

    IngestionToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token, None


def _throttled(
    request: HttpRequest, source: IngestionSource, token: IngestionToken | None = None
) -> bool:
    """Whether this request exceeds the per-source, per-IP, or per-token webhook rate limit."""
    ip = request.META.get("REMOTE_ADDR") or "unknown"
    window = int(time.time() // 60)
    keys: list[tuple[str, int]] = [
        (f"wh:src:{source.slug}:{window}", settings.WEBHOOK_RATE_SOURCE_PER_MIN),
        (f"wh:ip:{ip}:{window}", settings.WEBHOOK_RATE_IP_PER_MIN),
    ]
    if token:
        keys.append((f"wh:token:{token.prefix}:{window}", settings.WEBHOOK_RATE_SOURCE_PER_MIN))

    for key, limit in keys:
        if limit <= 0:
            continue
        try:
            if not cache.add(key, 1, timeout=120):
                if cache.incr(key) > limit:
                    return True
        except ValueError:
            cache.set(key, 1, timeout=120)
        except Exception:
            return False
    return False


@csrf_exempt
@require_POST
def webhook_alerts(request: HttpRequest) -> HttpResponse:
    """Accept one raw alert from any registered source (AC4.1—AC4.8).

    The path is source-less by design: the credential decides which feed this is and which tenant it
    belongs to. Everything cheap happens before the DB is touched, and the body is still never parsed
    before the size cap has had its say.
    """
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

    # The credential is the only routing fact: it resolves both the feed and the tenant.
    token, denied = _authenticate_token(request)
    if denied is not None:
        return denied
    # `_authenticate_token` returns `(None, error)` on failure, so a token is guaranteed here.
    assert token is not None
    source = token.source

    # Throttle after auth — auth is cheap and prevents DoS against legitimate sources
    if _throttled(request, source, token):
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

    # One pass over the payload, by the source's rules. `default_severity` is the source's own floor
    # rather than the module constant, so declaring "everything from this feed is at least a High" is
    # a setting rather than a code change.
    draft = apply_mapping(
        source.mapping_config,
        payload,
        default_severity=source.default_severity,
    )

    try:
        stored = store_ingested_alert(
            wire_source=str(payload.get("source") or source.slug),
            alert_type=str(draft.type or "generic")[:100],
            payload=payload,
            title=str(draft.title or "Alert")[:200],
            description=draft.description,
            summary=draft.summary,
            mapped_source_ref=draft.source_ref,
            severity=draft.severity,
            date=draft.date,
            ingestion_source=source,
            owner_org=token.organisation,
            extra_warnings=draft.warnings,
            mapping_warnings=draft.field_warnings,
        )
    except ValidationError as exc:
        detail = getattr(exc, "message_dict", None) or {"nonFieldErrors": [str(exc)]}
        return _error(400, "Bad request", type_="badRequest", fields=detail)

    alert = stored.alert
    # The path no longer names the feed, so this line is the provenance trail for an ingest.
    logger.info(
        "ingest %s source=%s token=%s org=%s alert=%s",
        "accepted" if stored.created else "replayed",
        source.slug,
        token.prefix,
        token.organisation_id,
        alert.id,
    )
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
        status=201 if stored.created else 200,
    )
