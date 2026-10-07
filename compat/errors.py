from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def thehive_exception_handler(
    exc: Exception, context: dict[str, object] | None = None
) -> Response | None:
    response = exception_handler(exc, context)
    if response is None:
        # The traceback belongs to the operator, not the client: `str(exc)` used to be echoed
        # into the body, which turns any unhandled error into an information leak (auditor
        # F6). `logger.exception` keeps the detail server-side (it runs inside DRF's `except`
        # block, so the active exception is what gets logged); the wire gets a fixed envelope.
        logger.exception("Unhandled exception while serving %s", (context or {}).get("view"))
        return Response(
            {"type": "GenericError", "message": "Internal error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    res_status = response.status_code
    if res_status == status.HTTP_400_BAD_REQUEST:
        detail = response.data
        if isinstance(detail, dict):
            fields = detail
        else:
            fields = {"non_field_errors": detail}
        return Response(
            {"type": "BadRequest", "message": "Bad request", "fields": fields},
            status=res_status,
        )
    elif res_status == status.HTTP_401_UNAUTHORIZED:
        return Response(
            {"type": "AuthenticationError", "message": "Unauthorized"},
            status=res_status,
        )
    elif res_status == status.HTTP_403_FORBIDDEN:
        return Response(
            {"type": "AuthorizationError", "message": "Forbidden"},
            status=res_status,
        )
    elif res_status == status.HTTP_404_NOT_FOUND:
        return Response(
            {"type": "NotFoundError", "message": "Not found"},
            status=res_status,
        )
    elif res_status == status.HTTP_405_METHOD_NOT_ALLOWED:
        # DRF's `MethodNotAllowed` response carries `{"detail": ErrorDetail("Method "POST" not
        # allowed.")}`; the `else` branch would stringify that into the body and leak the
        # framework's internals (verifier V2). A fixed envelope, same shape as every other error.
        return Response(
            {"type": "BadRequest", "message": "Method not allowed"},
            status=res_status,
        )
    else:
        return Response(
            {"type": "GenericError", "message": str(response.data) if response.data else "Error"},
            status=res_status,
        )
