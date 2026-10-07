import pytest
from rest_framework.test import APIClient

from ._t1_seed import _case


@pytest.mark.django_db
def test_time_parsing_contract():
    from compat import time

    dt = time.parse_timestamp(1745539200000)
    assert time.to_epoch_ms(dt) == 1745539200000
    dt = time.parse_timestamp("2026-10-03T00:00:00Z")
    assert time.to_epoch_ms(dt) > 0
    assert time.parse_timestamp(None) is None


@pytest.mark.parametrize(
    "hostile",
    [float("inf"), float("nan"), 1e18],
    ids=["infinity", "nan", "huge-ms"],
)
def test_parse_timestamp_refuses_hostile_floats(hostile: float) -> None:
    """`fromtimestamp` raises on these (auditor F5); the parser degrades them to `None`.

    `None` is the value every caller already turns into the clean 400 "invalid timestamp"
    path, so a hostile magnitude lands exactly where a malformed ISO string lands — never a
    `OverflowError`/`ValueError` escaping as a 500. (`float("nan") < 0` is `False`, so it
    reaches the `fromtimestamp` branch like the others do.)
    """
    from compat import time

    assert time.parse_timestamp(hostile) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("raw_date", "label"),
    [
        ("1e18", "huge-ms"),
        ('"Infinity"', "infinity-string"),
        ('"NaN"', "nan-string"),
    ],
)
def test_an_endpoint_meets_a_hostile_timestamp_with_a_400_not_a_500(
    raw_date: str, label: str, api: APIClient
) -> None:
    """The same values through `POST .../customEvent` — the endpoint contract, not just the unit.

    Sent as a raw body because DRF's `STRICT_JSON` renderer refuses to *encode* `Infinity`/
    `NaN` — and its parser refuses to *decode* bare `Infinity`/`NaN` tokens (its own clean
    400, before any view code). The values that do reach `parse_timestamp` on the wire are
    finite-but-outrageous numbers (`1e18`) and the *strings* `"Infinity"`/`"NaN"`, whose
    `float()` fallback lands on the exact `fromtimestamp` explosions FIX-4 tamed.
    """
    case = _case("Hostile timestamp case")
    response = api.post(
        f"/api/v1/case/{case.id}/customEvent",
        f'{{"title": "Bad clock", "date": {raw_date}}}',
        content_type="application/json",
    )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["type"] == "BadRequest"
    assert body["fields"] == {"date": ["invalid timestamp"]}, label


@pytest.mark.django_db
def test_severity_contract():
    from compat import enums

    assert enums.SEVERITY_LABELS[1] == "Low"
    assert enums.SEVERITY_LABELS[4] == "Critical"
