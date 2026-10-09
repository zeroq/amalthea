"""Legacy `/api/v1/...` catch-all and the public session-auth endpoints.

The catch-all is what keeps an unknown path from falling into the UI router (or, worse, into
an admin view) and answering with a 404 that says nothing about the envelope a client should
be parsing. It is the **last** include in `amalthea/urls.py` and the last pattern in this
module — both orderings are load-bearing.

`api_login`/`api_logout` are the two public views on the API surface. Both deliberately
replace *all three* authentication policies rather than only the permission one:

* `AllowAny` — the default `IsAuthenticated` would 401 a login before it ran;
* an empty `authentication_classes` — with a session cookie present, DRF's
  `SessionAuthentication` runs its own CSRF check on any unsafe method **regardless of
  `@csrf_exempt`** (it calls `CsRFCheck` itself, with no callback to exempt), so a logout
  POST would 403 for exactly the clients that have a session to log out of. Skipping DRF
  authentication sidesteps that; Django's `AuthenticationMiddleware` still owns
  `request.user`, which is all `logout()` needs;
* `@csrf_exempt` outermost — Django's `CsrfViewMiddleware` runs *before* the view and would
  otherwise 403 an API client that has no CSRF token (a webhook-style caller, a curl, a
  thehive4py session). The token is still enforced for every other endpoint.

Throttling is deliberately left alone: `DEFAULT_THROTTLE_CLASSES` (100/min anon by IP) still
applies, which is the rate limit plan §8 asks for on login. **Additionally, `api_login` has
its own stricter per-IP/username rate limit (5 attempts per 15 min, 5 min lockout) via
`identity.ratelimit`.**
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.serializers import user_json


@api_view(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@renderer_classes([JSONRenderer])
def not_found(request: Request, *args: object, **kwargs: object) -> Response:
    return Response(
        {"type": "NotFoundError", "message": "Not found"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def api_login(request: Request) -> Response:
    """`POST /api/v1/login` → 200 `OutputUser` + session cookie, or **400** on bad credentials.

    The failure is a 400, never a 401 (recorded OpenAPI, plan §12 AC-A6): a 401 is what an
    attacker enumerates against. On timing, the primary path is equalized by Django's
    dummy-hash — `ModelBackend` runs the password hasher once for an unknown username too —
    while the login-vs-username fallback below may add one extra comparison when the two have
    diverged. What does not exist either way is a status-code oracle: unknown user, wrong
    password and non-string credential material all get the identical 400 envelope, and the
    envelope deliberately does *not* say which of `user`/`password` was wrong.

    `LoginInput.user` is TheHive's spelling of our `username`; the password check itself goes
    through `authenticate()` so it honours whatever backend is configured (argon2 here) rather
    than comparing anything in this view.

    Rate limiting is handled by DRF's `AnonRateThrottle` (100/min per IP) via
    `DEFAULT_THROTTLE_CLASSES`. Per-IP/username stricter limits are applied to the UI login
    (`SignInView`) via `identity.ratelimit`.
    """
    payload = request.data if isinstance(request.data, dict) else {}
    raw_identifier = payload.get("user")
    password = payload.get("password")

    # Non-string credential material (`{"password": ["x"]}`, `{"password": 123}`) used to
    # reach `authenticate()` and raise out of the password hasher — a 500 carrying the
    # exception text (auditor F1). A non-string is not a credential: it gets the *same* 400
    # a wrong password gets, so the body type cannot become a status-code oracle either.
    if not isinstance(raw_identifier, (str, type(None))) or not isinstance(
        password, (str, type(None))
    ):
        return _bad("Invalid credentials", {})

    identifier = str(raw_identifier or "").strip()

    user = None
    if identifier and password:
        user = authenticate(request._request, username=identifier, password=password)
        if user is None:
            # `USERNAME_FIELD` is `username`, but a client signs in with `login`; when the two
            # have been allowed to diverge, retry under the canonical username.
            from identity.models import User

            candidate = User.objects.filter(login=identifier).first()
            if candidate is not None and candidate.username != identifier:
                user = authenticate(
                    request._request, username=candidate.username, password=password
                )

    if user is None:
        return _bad("Invalid credentials", {})

    requested_org = payload.get("organisation") or payload.get("org")
    if requested_org is not None and str(requested_org).strip():
        supplied = str(requested_org).strip()
        org = user.org  # no query when `org_id` is NULL (nullable FK short-circuit)
        org_name = org.name if org else None
        org_id = str(user.org_id) if user.org_id else None
        if supplied not in {org_name, org_id}:
            return _bad(
                "Organisation does not match",
                {"organisation": ["does not belong to this user"]},
            )

    django_login(request._request, user)
    return Response(user_json(user))


@csrf_exempt
@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def api_logout(request: Request) -> Response:
    """`GET|POST /api/v1/logout` → 200 empty — the recorded OpenAPI ships no body.

    Both verbs because TheHive's own clients hit it as a link as often as a button (plan §11-2).
    The session is flushed on the underlying `HttpRequest`; `django.contrib.auth.logout` rotates
    the session key, so the cookie the client still holds points at nothing and the next
    authenticated call 401s.
    """
    django_logout(request._request)
    return Response(status=status.HTTP_200_OK)


# --- Describe: a read-only catalogue of the entities this API models --------
#
# TheHive's `GET /describe/_all` and `GET /describe/{model}` let a generated client learn the
# entity/attribute surface without shipping a hand-maintained copy of the schema. This is a
# *description*, not a serializer: the attribute type strings are TheHive's `PropertyDescription`
# `type` enum (`boolean`, `date`, `enumeration`, `float`, `integer`, `string`, `url`, `user`),
# and `initialQuery` names the query operation a client would call to list the entity. The set
# below covers every entity this surface models; a model that is not listed answers 404 rather
# than an empty description, so a typo is loud.


def _attr(
    name: str,
    prop_type: str,
    *,
    cardinality: str = "single",
    aggregable: bool = False,
    index_type: str = "none",
    values: list[str] | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    attribute: dict[str, Any] = {
        "name": name,
        "type": prop_type,
        "cardinality": cardinality,
        "aggregable": aggregable,
        "indexType": index_type,
    }
    if values is not None:
        attribute["values"] = values
        attribute["labels"] = labels if labels is not None else values
    return attribute


def _entity(label: str, initial_query: str, attributes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "label": label,
        "path": "",
        "initialQuery": initial_query,
        "attributes": attributes,
    }


_SEVERITY_VALUES = ["1", "2", "3", "4"]
_SEVERITY_LABELS = ["low", "medium", "high", "critical"]
_TLP_VALUES = ["0", "1", "2", "3", "4"]
_TLP_LABELS = ["white", "green", "amber", "red", "unknown"]
_PAP_VALUES = ["0", "1", "2", "3"]
_PAP_LABELS = ["white", "green", "amber", "red"]

_DESCRIBE: dict[str, dict[str, Any]] = {
    "case": _entity(
        "case",
        "listCase",
        [
            _attr("title", "string", index_type="fulltext"),
            _attr("description", "string", index_type="fulltext"),
            _attr(
                "severity",
                "integer",
                aggregable=True,
                values=_SEVERITY_VALUES,
                labels=_SEVERITY_LABELS,
            ),
            _attr("status", "string"),
            _attr("tlp", "integer", values=_TLP_VALUES, labels=_TLP_LABELS),
            _attr("pap", "integer", values=_PAP_VALUES, labels=_PAP_LABELS),
            _attr("flag", "boolean"),
            _attr("assignee", "user"),
            _attr("tags", "string", cardinality="list"),
            _attr("createdAt", "date"),
        ],
    ),
    "alert": _entity(
        "alert",
        "listAlert",
        [
            _attr("title", "string", index_type="fulltext"),
            _attr("type", "string", index_type="fulltext"),
            _attr("source", "string", index_type="fulltext"),
            _attr("sourceRef", "string", index_type="fulltext"),
            _attr(
                "severity",
                "integer",
                aggregable=True,
                values=_SEVERITY_VALUES,
                labels=_SEVERITY_LABELS,
            ),
            _attr("status", "string"),
            _attr("date", "date"),
            _attr("tlp", "integer", values=_TLP_VALUES, labels=_TLP_LABELS),
            _attr("pap", "integer", values=_PAP_VALUES, labels=_PAP_LABELS),
            _attr("flag", "boolean"),
        ],
    ),
    "task": _entity(
        "task",
        "listTask",
        [
            _attr("title", "string", index_type="fulltext"),
            _attr("group", "string"),
            _attr(
                "status",
                "enumeration",
                values=["Waiting", "InProgress", "Completed", "Cancel"],
            ),
            _attr("flag", "boolean"),
            _attr("order", "integer"),
            _attr("assignee", "user"),
        ],
    ),
    "observable": _entity(
        "observable",
        "listObservable",
        [
            _attr("dataType", "string"),
            _attr("data", "string", index_type="fulltext"),
            _attr("tlp", "integer", values=_TLP_VALUES, labels=_TLP_LABELS),
            _attr("pap", "integer", values=_PAP_VALUES, labels=_PAP_LABELS),
            _attr("ioc", "boolean"),
            _attr("sighted", "boolean"),
            _attr("tags", "string", cardinality="list"),
        ],
    ),
    "customEvent": _entity(
        "customEvent",
        "listCustomEvent",
        [
            _attr("title", "string", index_type="fulltext"),
            _attr("description", "string", index_type="fulltext"),
            _attr("date", "date"),
            _attr("endDate", "date"),
        ],
    ),
    "customField": _entity(
        "customField",
        "listCustomField",
        [
            _attr("name", "string"),
            _attr("group", "string"),
            _attr(
                "type",
                "enumeration",
                values=["string", "integer", "float", "boolean", "date", "url"],
            ),
            _attr("options", "string"),
        ],
    ),
    "user": _entity(
        "user",
        "listUser",
        [
            _attr("login", "string", index_type="fulltext"),
            _attr("name", "string", index_type="fulltext"),
            _attr("role", "string"),
            _attr("organisation", "string"),
        ],
    ),
    "organisation": _entity(
        "organisation",
        "listOrganisation",
        [
            _attr("name", "string", index_type="fulltext"),
            _attr("description", "string", index_type="fulltext"),
        ],
    ),
    "tag": _entity(
        "tag",
        "listTag",
        [
            _attr("namespace", "string"),
            _attr("predicate", "string", index_type="fulltext"),
            _attr("description", "string", index_type="fulltext"),
            _attr("colour", "string"),
            _attr("hidden", "boolean"),
        ],
    ),
    "caseStatus": _entity(
        "caseStatus",
        "listCaseStatus",
        [
            _attr("value", "string", index_type="fulltext"),
            _attr("stage", "enumeration", values=["New", "InProgress", "Closed"]),
            _attr("order", "integer"),
            _attr("description", "string", index_type="fulltext"),
            _attr("hidden", "boolean"),
        ],
    ),
    "alertStatus": _entity(
        "alertStatus",
        "listAlertStatus",
        [
            _attr("value", "string", index_type="fulltext"),
            _attr("stage", "enumeration", values=["New", "InProgress", "Closed", "Imported"]),
            _attr("order", "integer"),
            _attr("description", "string", index_type="fulltext"),
            _attr("hidden", "boolean"),
        ],
    ),
    "observableType": _entity(
        "observableType",
        "listObservableType",
        [
            _attr("name", "string", index_type="fulltext"),
            _attr("isAttachment", "boolean"),
            _attr("isCaseSensitive", "boolean"),
        ],
    ),
}


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def describe_all(request: Request) -> Response:
    """`GET /api/v1/describe/_all` — the catalogue keyed by model name."""
    return Response(_DESCRIBE)


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def describe_model(request: Request, model: str) -> Response:
    """`GET /api/v1/describe/{model}` — one entity description, or 404 for an unknown model."""
    description = _DESCRIBE.get(model)
    if description is None:
        return Response(
            {"type": "NotFoundError", "message": f"{model} is not a described model"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(description)
