from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def thehive_exception_handler(exc: Exception, context: dict | None = None) -> Response | None:
    response = exception_handler(exc, context)
    if response is None:
        return Response(
            {"type": "GenericError", "message": str(exc) if str(exc) else "Internal error"},
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
    else:
        return Response(
            {"type": "GenericError", "message": str(response.data) if response.data else "Error"},
            status=res_status,
        )
