from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@renderer_classes([JSONRenderer])
def not_found(request: Request, *args: object, **kwargs: object) -> Response:
    return Response(
        {"type": "NotFoundError", "message": "Not found"},
        status=status.HTTP_404_NOT_FOUND,
    )
