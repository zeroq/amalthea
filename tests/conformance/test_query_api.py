"""Phase 8 conformance — `POST /api/v1/query` (AC8.1-AC8.4) and the P8-1 timeline envelope.

Covers, in order:

* **AC8.1** the plan's documented example query: bare array, `X-Total` = the pre-page count,
  severity-only, newest-first, every record is the T1 `case_json` surface minus the excluded field;
* **AC8.2** `from`/`to` paging over a fixed 25-case seed — disjoint, complete, ordered, stable
  across two runs (`_id` tie-break) — plus the `_between` timestamp tiles that the paging
  semantics depend on (`_from` inclusive, `_to` exclusive);
* **AC8.3** one positive test per implemented operator/step, and parametrized 400s for every
  unimplemented 5.8.0 operator and step **naming the token** in the `BadRequest` message — never
  a 200, never a silently empty array;
* **AC8.4** request bodies *built by thehive4py 2.1.0's own query builders* (the same objects
  `case.find()` / `alert.find()` / `observable.find()` / `case.count()` / `query.run()` POST,
  minus the live HTTP hop), the accepted-and-ignored `?name=`, the trailing-slash twin, the
  `includeFields`-wins projection, the unauthenticated refusal, and a query-count bound so the
  no-N+1 promise (plan G5) cannot regress;
* **P8-1** the timeline `GET` envelope — `{"events": [...]}` with ms `date`, mapped `kind`,
  `Case`/`Alert` entity references, `details` metadata and `endDate: null` — while the internal
  `timeline_event_json` ledger shape (Phase 7 WS/UI) stays untouched.

The seed helpers use `get_or_create` on every dictionary row the tests depend on, so nothing here
assumes the seed migrations ran (match the repo's test culture: `test_mvp_loop.py`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone
from thehive4py.query import filters as hive_filters
from thehive4py.query import page as hive_page
from thehive4py.query import sort as hive_sort

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseObservable, CaseStatus, TimelineEvent
from core.serializers import case_json
from identity.models import Organisation, User
from observables.models import Observable, ObservableType

QUERY_URL = "/api/v1/query"

# ---------------------------------------------------------------------------
# fixtures & seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def analyst(db: None) -> User:
    org = Organisation.objects.create(name="Test Org")
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="analyst@example.net",
        org=org,
        is_active=True,
    )


@pytest.fixture
def api(analyst: User) -> object:
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def anonymous_api() -> object:
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture(autouse=True)
def _reset_throttle_cache() -> None:
    # DRF's UserRateThrottle (1000/min) keys into the process-wide LocMemCache; without a reset
    # between tests, a long suite run trips 429s that have nothing to do with the assertion at
    # hand (the webhook-hardening tests grew the same pattern for the anonymous throttle).
    cache.clear()


def _case(title: str, severity: int = 2, **kwargs: Any) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, severity=severity, status=status, **kwargs)


def _alert(title: str, *, ref: str, severity: int = 2, case: Case | None = None) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=ref,
        title=title,
        severity=severity,
        status=status,
        case=case,
    )


def _observable(data: str, type_name: str = "ip") -> Observable:
    otype = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(data_type=otype, data=data, normalized_data=data)


def _link(case: Case, observable: Observable) -> CaseObservable:
    return CaseObservable.objects.create(case=case, observable=observable)


def _stamp(case: Case, when: Any) -> None:
    """`created_at` is `auto_now_add`; `update()` is the repo's way to control it in tests."""
    Case.objects.filter(pk=case.pk).update(created_at=when)


def _ms(when: Any) -> int:
    return int(when.timestamp() * 1000)


def _post(api: object, steps: list[dict[str, Any]], **envelope: object) -> Any:
    body: dict[str, Any] = {"query": steps}
    body.update(envelope)
    return api.post(QUERY_URL, body, format="json")


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["_id"] for item in items]


def _assert_bad_request(resp: Any, token: str) -> None:
    assert resp.status_code == 400, resp.content
    payload = resp.json()
    assert payload["type"] == "BadRequest"
    assert token in payload["message"]


# ---------------------------------------------------------------------------
# AC8.1 — the documented example
# ---------------------------------------------------------------------------


def test_ac8_1_documented_example_returns_bare_array_with_x_total(api: object) -> None:
    base = timezone.now()
    for i in range(12):
        case = _case(f"sev3-{i:02d}", severity=3)
        _stamp(case, base + timedelta(minutes=i))
    for i in range(3):
        _case(f"sev2-{i}", severity=2)

    body: dict[str, Any] = {
        "query": [
            {"_name": "listCase"},
            {"_name": "filter", "_eq": {"_field": "severity", "_value": 3}},
            {"_name": "sort", "_fields": [{"_createdAt": "desc"}]},
            {"_name": "page", "from": 0, "to": 10, "extraData": ["total"]},
        ],
        "excludeFields": ["description"],
    }
    resp = api.post(QUERY_URL, body, format="json")
    assert resp.status_code == 200, resp.content

    data = resp.json()
    assert isinstance(data, list)  # a bare array, not an envelope
    assert len(data) == 10  # the page, not the whole set
    assert resp["X-Total"] == "12"  # pre-page count — spans all pages, not just this slice

    newest = Case.objects.filter(severity=3).order_by("-created_at", "-id").first()
    assert newest is not None
    assert data[0]["_id"] == str(newest.id)  # newest first
    assert [item["severity"] for item in data] == [3] * 10

    # The query path renders through the T1 serializer: same surface, minus the exclusion.
    expected_keys = set(case_json(newest)) - {"description"}
    for item in data:
        assert set(item.keys()) == expected_keys
        assert "description" not in item


# ---------------------------------------------------------------------------
# AC8.2 — from/to paging and the _between tiles underneath it
# ---------------------------------------------------------------------------


def test_ac8_2_paging_is_disjoint_complete_ordered_and_stable(api: object) -> None:
    base = timezone.now()
    for i in range(25):
        case = _case(f"page-{i:02d}")
        _stamp(case, base + timedelta(minutes=i))
    expected = [str(c.id) for c in Case.objects.order_by("created_at", "id")]

    def page(start: int, end: int) -> list[str]:
        resp = _post(
            api,
            [
                {"_name": "listCase"},
                {"_name": "sort", "_fields": [{"_createdAt": "asc"}]},
                {"_name": "page", "from": start, "to": end, "extraData": ["total"]},
            ],
        )
        assert resp.status_code == 200, resp.content
        assert resp["X-Total"] == "25"
        return _ids(resp.json())

    first = page(0, 10)
    second = page(10, 20)
    assert first == expected[0:10]  # ordered
    assert second == expected[10:20]  # ordered
    assert set(first).isdisjoint(second)  # disjoint
    assert set(first) | set(second) == set(expected[0:20])  # complete
    # Deterministic across runs — the `_id` tie-break, not hash order.
    assert page(0, 10) == first
    assert page(10, 20) == second


def test_ac8_2_between_tiles_are_disjoint_and_complete(api: object) -> None:
    base = timezone.now()
    for i in range(20):
        case = _case(f"tile-{i:02d}")
        _stamp(case, base + timedelta(minutes=i))
    expected = [str(c.id) for c in Case.objects.order_by("created_at", "id")]
    lo, mid, hi = _ms(base), _ms(base + timedelta(minutes=10)), _ms(base + timedelta(minutes=20))

    def tile(start_ms: int, end_ms: int) -> list[str]:
        resp = _post(
            api,
            [
                {"_name": "listCase"},
                {
                    "_name": "filter",
                    "_between": {"_field": "_createdAt", "_from": start_ms, "_to": end_ms},
                },
                {"_name": "sort", "_fields": [{"_createdAt": "asc"}]},
            ],
        )
        assert resp.status_code == 200, resp.content
        return _ids(resp.json())

    left, right = tile(lo, mid), tile(mid, hi)
    assert left == expected[0:10]  # _from is inclusive
    assert right == expected[10:20]  # _to is exclusive — the boundary row moves on to the next tile
    assert set(left).isdisjoint(right)
    assert set(left) | set(right) == set(expected[0:20])


# ---------------------------------------------------------------------------
# AC8.3 — one positive test per implemented operator / step
# ---------------------------------------------------------------------------


def test_operator_eq(api: object) -> None:
    names = ["A", "B", "C"]
    for name, severity in zip(names, (2, 2, 3), strict=True):
        _case(f"eq-{name}", severity=severity)

    resp = _post(
        api,
        [{"_name": "listCase"}, {"_name": "filter", "_eq": {"_field": "severity", "_value": 2}}],
    )
    assert resp.status_code == 200, resp.content
    assert {item["title"] for item in resp.json()} == {"eq-A", "eq-B"}


def test_operator_ne(api: object) -> None:
    for name, severity in zip(("A", "B", "C"), (2, 2, 3), strict=True):
        _case(f"ne-{name}", severity=severity)

    resp = _post(
        api,
        [{"_name": "listCase"}, {"_name": "filter", "_ne": {"_field": "severity", "_value": 2}}],
    )
    assert resp.status_code == 200, resp.content
    assert {item["title"] for item in resp.json()} == {"ne-C"}


def test_operator_range_gt_gte_lt_lte(api: object) -> None:
    for name, severity in zip(("A", "B", "C"), (1, 2, 3), strict=True):
        _case(f"range-{name}", severity=severity)

    def titles(steps: list[dict[str, Any]]) -> set[str]:
        resp = _post(api, [{"_name": "listCase"}, *steps])
        assert resp.status_code == 200, resp.content
        return {item["title"] for item in resp.json()}

    assert titles([{"_name": "filter", "_gt": {"_field": "severity", "_value": 2}}]) == {"range-C"}
    assert titles([{"_name": "filter", "_gte": {"_field": "severity", "_value": 2}}]) == {
        "range-B",
        "range-C",
    }
    assert titles([{"_name": "filter", "_lt": {"_field": "severity", "_value": 2}}]) == {"range-A"}
    assert titles([{"_name": "filter", "_lte": {"_field": "severity", "_value": 2}}]) == {
        "range-A",
        "range-B",
    }


def test_operator_between_on_an_integer_field(api: object) -> None:
    for name, severity in zip(("A", "B", "C", "D"), (1, 2, 3, 4), strict=True):
        _case(f"between-{name}", severity=severity)

    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", "_between": {"_field": "severity", "_from": 2, "_to": 4}},
        ],
    )
    assert resp.status_code == 200, resp.content
    # 4 is excluded: `_to` is exclusive on every field, not just datetimes.
    assert {item["title"] for item in resp.json()} == {"between-B", "between-C"}


def test_operator_in(api: object) -> None:
    for name, severity in zip(("A", "B", "C", "D"), (1, 2, 3, 4), strict=True):
        _case(f"in-{name}", severity=severity)

    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", "_in": {"_field": "severity", "_values": [1, 3]}},
        ],
    )
    assert resp.status_code == 200, resp.content
    assert {item["title"] for item in resp.json()} == {"in-A", "in-C"}


def test_operator_like_is_case_insensitive_substring(api: object) -> None:
    _case("invoice phish A")
    _case("invoice phish B")
    _case("unrelated alert")

    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", "_like": {"_field": "title", "_value": "PhIsH"}},
        ],
    )
    assert resp.status_code == 200, resp.content
    assert {item["title"] for item in resp.json()} == {"invoice phish A", "invoice phish B"}


def test_operator_has(api: object) -> None:
    for name in ("A", "B", "C"):
        _case(f"has-{name}")
    ended = Case.objects.get(title="has-B")
    Case.objects.filter(pk=ended.pk).update(end_date=timezone.now())

    resp = _post(api, [{"_name": "listCase"}, {"_name": "filter", "_has": "endDate"}])
    assert resp.status_code == 200, resp.content
    assert [item["title"] for item in resp.json()] == ["has-B"]


def test_operators_and_or_not(api: object) -> None:
    _case("phish alpha", severity=2)
    _case("phish beta", severity=3)
    _case("malware gamma", severity=3)

    def titles(steps: list[dict[str, Any]]) -> set[str]:
        resp = _post(api, [{"_name": "listCase"}, *steps])
        assert resp.status_code == 200, resp.content
        return {item["title"] for item in resp.json()}

    assert titles(
        [
            {
                "_name": "filter",
                "_and": [
                    {"_eq": {"_field": "severity", "_value": 3}},
                    {"_like": {"_field": "title", "_value": "phish"}},
                ],
            }
        ]
    ) == {"phish beta"}
    assert titles(
        [
            {
                "_name": "filter",
                "_or": [
                    {"_eq": {"_field": "severity", "_value": 2}},
                    {"_like": {"_field": "title", "_value": "malware"}},
                ],
            }
        ]
    ) == {"phish alpha", "malware gamma"}
    assert titles([{"_name": "filter", "_not": {"_eq": {"_field": "severity", "_value": 3}}}]) == {
        "phish alpha"
    }


def test_sort_tie_break_is_deterministic(api: object) -> None:
    # Identical created_at → equal sort keys → the `_id` tie-break must decide, deterministically.
    now = timezone.now()
    for name in ("A", "B", "C"):
        case = _case(f"tie-{name}")
        _stamp(case, now)

    resp = _post(
        api, [{"_name": "listCase"}, {"_name": "sort", "_fields": [{"_createdAt": "desc"}]}]
    )
    assert resp.status_code == 200, resp.content
    # The tie-break is ascending `_id` in every sort (engine contract, mirrors `_sort_rows`).
    expected = [str(c.id) for c in Case.objects.order_by("-created_at", "id")]
    assert _ids(resp.json()) == expected


def test_count_step_returns_a_bare_integer(api: object) -> None:
    for name, severity in zip(("A", "B", "C", "D", "E"), (3, 3, 3, 2, 2), strict=True):
        _case(f"count-{name}", severity=severity)

    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", "_eq": {"_field": "severity", "_value": 3}},
            {"_name": "count"},
        ],
    )
    assert resp.status_code == 200, resp.content
    value = resp.json()
    assert isinstance(value, int) and not isinstance(value, bool)
    assert value == 3


def test_get_case_by_tilde_id_plain_id_and_number(api: object) -> None:
    case = _case("Target case", severity=1)

    for identifier in (f"~{case.id}", str(case.id), str(case.number)):
        resp = _post(api, [{"_name": "getCase", "idOrName": identifier}])
        assert resp.status_code == 200, resp.content
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["_id"] == str(case.id)

    # A miss threads "zero cases" through every later step: an honest [], not a 400.
    resp = _post(api, [{"_name": "getCase", "idOrName": f"~{uuid4()}"}])
    assert resp.status_code == 200, resp.content
    assert resp.json() == []


def test_list_any_returns_every_kind_and_filters_by_type_literal(api: object) -> None:
    case = _case("mixed-case", severity=2)
    alert = _alert("mixed-alert", ref="mixed-alert", severity=2)
    observable = _observable("192.0.2.55")
    _link(case, observable)

    resp = _post(api, [{"_name": "listAny"}])
    assert resp.status_code == 200, resp.content
    rows = resp.json()
    assert {row["_type"] for row in rows} == {"case", "alert", "observable"}
    assert len(rows) == 3

    only_alerts = _post(
        api,
        [
            {"_name": "listAny"},
            {"_name": "filter", "_eq": {"_field": "_type", "_value": "alert"}},
        ],
    )
    assert only_alerts.status_code == 200, only_alerts.content
    only_alerts_rows = only_alerts.json()
    assert len(only_alerts_rows) == 1
    assert only_alerts_rows[0]["_id"] == str(alert.id)

    case_and_observable = _post(
        api,
        [
            {"_name": "listAny"},
            {"_name": "filter", "_in": {"_field": "_type", "_values": ["case", "observable"]}},
        ],
    )
    assert case_and_observable.status_code == 200, case_and_observable.content
    assert {row["_type"] for row in case_and_observable.json()} == {"case", "observable"}


def test_list_any_drops_kinds_that_lack_the_filtered_field(api: object) -> None:
    case = _case("mixed-case", severity=2)
    _alert("mixed-alert", ref="mixed-alert", severity=2)
    _link(case, _observable("192.0.2.66"))

    # `severity` is a case/alert field; the observable row has no severity, so under `_eq` it
    # cannot match — the observable branch drops out instead of blowing up with a fake value.
    resp = _post(
        api,
        [
            {"_name": "listAny"},
            {"_name": "filter", "_eq": {"_field": "severity", "_value": 2}},
        ],
    )
    assert resp.status_code == 200, resp.content
    assert {row["_type"] for row in resp.json()} == {"case", "alert"}


# ---------------------------------------------------------------------------
# AC8.3 — unimplemented operators / steps / fields must be named 400s
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op",
    ["_startsWith", "_endsWith", "_match", "_contains", "_is", "_any"],
)
def test_unimplemented_filter_operators_are_named_400s(api: object, op: str) -> None:
    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", op: {"_field": "title", "_value": "x"}},
        ],
    )
    _assert_bad_request(resp, op)


@pytest.mark.parametrize(
    "name",
    ["getAny", "listTask", "label", "countCase", "getObservable", "listProcedure"],
)
def test_unimplemented_steps_are_named_400s(api: object, name: str) -> None:
    resp = _post(api, [{"_name": name}])
    _assert_bad_request(resp, name)


def test_unknown_field_is_a_named_400(api: object) -> None:
    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "filter", "_eq": {"_field": "noSuchField", "_value": 1}},
        ],
    )
    _assert_bad_request(resp, "noSuchField")


def test_count_must_be_the_final_step(api: object) -> None:
    resp = _post(
        api,
        [
            {"_name": "listCase"},
            {"_name": "count"},
            {"_name": "sort", "_fields": [{"_createdAt": "asc"}]},
        ],
    )
    _assert_bad_request(resp, "count")


def test_filter_without_a_list_step_is_a_400(api: object) -> None:
    resp = _post(api, [{"_name": "filter", "_eq": {"_field": "severity", "_value": 2}}])
    _assert_bad_request(resp, "filter")


def test_a_bare_array_body_is_rejected(api: object) -> None:
    resp = api.post(QUERY_URL, [{"_name": "listCase"}], format="json")
    _assert_bad_request(resp, "object")


def test_an_empty_query_array_is_rejected(api: object) -> None:
    resp = _post(api, [])
    _assert_bad_request(resp, "query")


def test_unknown_sort_field_and_direction_are_400s(api: object) -> None:
    resp = _post(api, [{"_name": "listCase"}, {"_name": "sort", "_fields": [{"noField": "asc"}]}])
    _assert_bad_request(resp, "noField")

    resp = _post(
        api, [{"_name": "listCase"}, {"_name": "sort", "_fields": [{"_createdAt": "sideways"}]}]
    )
    _assert_bad_request(resp, "sideways")


# ---------------------------------------------------------------------------
# AC8.4 — bodies built by thehive4py's own query builders, over the same Client
# ---------------------------------------------------------------------------


def test_thehive4py_case_find_body(api: object) -> None:
    base = timezone.now()
    for i in range(12):
        case = _case(f"hive-{i:02d}", severity=3)
        _stamp(case, base + timedelta(minutes=i))
    for i in range(3):
        _case(f"hive-low-{i}", severity=2)

    # Exactly what `case.find(filters=..., sortby=..., paginate=...)` POSTs.
    query = [
        {"_name": "listCase"},
        {"_name": "filter", **hive_filters.Eq("severity", 3)},
        {"_name": "sort", **hive_sort.Desc("_createdAt")},
        {"_name": "page", **hive_page.Paginate(0, 10, extra_data=["total"])},
    ]
    resp = api.post(f"{QUERY_URL}?name=cases", {"query": query}, format="json")
    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert isinstance(data, list) and len(data) == 10
    assert resp["X-Total"] == "12"
    assert all(item["severity"] == 3 for item in data)


def test_thehive4py_alert_find_body(api: object) -> None:
    _alert("hive-alert-1", ref="hive-a-1", severity=1)
    _alert("hive-alert-2", ref="hive-a-2", severity=2)
    _alert("hive-alert-3", ref="hive-a-3", severity=2)

    query = [
        {"_name": "listAlert"},
        {"_name": "filter", **hive_filters.Eq("severity", 2)},
        {"_name": "sort", **hive_sort.Asc("title")},
    ]
    resp = api.post(f"{QUERY_URL}?name=alerts", {"query": query}, format="json")
    assert resp.status_code == 200, resp.content
    assert [row["title"] for row in resp.json()] == ["hive-alert-2", "hive-alert-3"]


def test_thehive4py_observable_find_body(api: object) -> None:
    case = _case("obs-carrier")
    _link(case, _observable("192.0.2.1"))
    _link(case, _observable("192.0.2.2"))

    query = [
        {"_name": "listObservable"},
        {"_name": "filter", **hive_filters.Eq("dataType", "ip")},
    ]
    resp = api.post(f"{QUERY_URL}?name=observables", {"query": query}, format="json")
    assert resp.status_code == 200, resp.content
    rows = resp.json()
    assert {row["dataType"] for row in rows} == {"ip"}
    assert {row["data"] for row in rows} == {"192.0.2.1", "192.0.2.2"}


def test_thehive4py_case_count_body(api: object) -> None:
    for name, severity in zip(("A", "B", "C", "D"), (2, 2, 2, 3), strict=True):
        _case(f"hive-count-{name}", severity=severity)

    # `case.count(filters=Eq("severity", 2))` → listCase + filter + count.
    query = [
        {"_name": "listCase"},
        {"_name": "filter", **hive_filters.Eq("severity", 2)},
        {"_name": "count"},
    ]
    resp = api.post(f"{QUERY_URL}?name=cases.count", {"query": query}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.json() == 3


def test_thehive4py_query_run_with_explicit_empty_exclusions(api: object) -> None:
    # `client.query.run(query, exclude_fields=[])` always sends `excludeFields: []` — which must
    # mean "exclude nothing", not "exclude everything".
    case = _case("full-surface", severity=3)
    query = [{"_name": "listCase"}, {"_name": "filter", **hive_filters.Eq("_id", str(case.id))}]
    resp = api.post(QUERY_URL, {"query": query, "excludeFields": []}, format="json")
    assert resp.status_code == 200, resp.content
    rows = resp.json()
    assert len(rows) == 1
    assert set(rows[0].keys()) == set(case_json(case))


def test_name_param_is_accepted_and_ignored(api: object) -> None:
    _case("ignored-name-param")
    resp = api.post(f"{QUERY_URL}?name=cases", {"query": [{"_name": "listCase"}]}, format="json")
    assert resp.status_code == 200, resp.content
    assert len(resp.json()) == 1


def test_trailing_slash_twin_route_works(api: object) -> None:
    _case("slash-twin")
    resp = api.post(f"{QUERY_URL}/", {"query": [{"_name": "listCase"}]}, format="json")
    assert resp.status_code == 200, resp.content
    assert len(resp.json()) == 1


def test_include_fields_wins_over_exclude_fields(api: object) -> None:
    _case("projection")
    resp = api.post(
        QUERY_URL,
        {
            "query": [{"_name": "listCase"}],
            "includeFields": ["title"],
            "excludeFields": ["title"],  # ignored: includeFields present
        },
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json() == [{"title": "projection"}]


def test_x_total_is_absent_unless_requested(api: object) -> None:
    for i in range(3):
        _case(f"no-total-{i}")
    resp = _post(api, [{"_name": "listCase"}, {"_name": "page", "from": 0, "to": 2}])
    assert resp.status_code == 200, resp.content
    assert resp.headers.get("X-Total") is None
    assert len(resp.json()) == 2


def test_unauthenticated_query_is_refused(anonymous_api: object) -> None:
    resp = anonymous_api.post(QUERY_URL, {"query": [{"_name": "listCase"}]}, format="json")
    assert resp.status_code in (401, 403), resp.content


def test_query_pages_without_n_plus_one(api: object, django_assert_max_num_queries: Any) -> None:
    for i in range(10):
        case = _case(f"perf-{i}")
        for j in range(2):
            _alert(f"perf-{i}-{j}", ref=f"perf-{i}-{j}", case=case)

    with django_assert_max_num_queries(4):
        # 1 count (X-Total) + 1 row fetch (select_related joins) + 1 prefetch of `alerts` for
        # `case_json`'s alertCount. Ten cases, two alerts each — still three queries.
        resp = _post(
            api,
            [{"_name": "listCase"}, {"_name": "page", "from": 0, "to": 10, "extraData": ["total"]}],
        )
    assert resp.status_code == 200, resp.content
    assert len(resp.json()) == 10
    assert {item["alertCount"] for item in resp.json()} == {2}


# ---------------------------------------------------------------------------
# P8-1 — the timeline GET envelope (5.8.0 OutputTimeline), internal shape untouched
# ---------------------------------------------------------------------------


def test_timeline_get_is_wrapped_with_mapped_kinds_and_entities(api: object) -> None:
    case = _case("timeline-carrier")
    alert = _alert("imported-alert", ref="tl-import")
    now = timezone.now()
    TimelineEvent.objects.create(case=case, kind="case-created", title="Case created", date=now)
    TimelineEvent.objects.create(
        case=case, kind="comment", title="A note", date=now, metadata={"body": "hello"}
    )
    TimelineEvent.objects.create(
        case=case,
        kind="alert-imported",
        title="Alert imported",
        date=now,
        metadata={"alert_id": str(alert.id)},
    )
    TimelineEvent.objects.create(case=case, kind="automation-run", title="Playbook ran", date=now)
    TimelineEvent.objects.create(case=case, kind="unknown-kind", title="Falls back", date=now)

    resp = api.get(f"/api/v1/case/{case.number}/timeline")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert set(body.keys()) == {"events"}

    by_kind = {event["kind"]: event for event in body["events"]}
    assert by_kind["case.created"]["entity"] == "Case"
    assert by_kind["case.created"]["entityId"] == f"~{case.id}"
    assert by_kind["case.created"]["endDate"] is None
    assert by_kind["log.created"]["details"] == {"body": "hello"}
    assert by_kind["alert.occurred"]["entity"] == "Alert"
    assert by_kind["alert.occurred"]["entityId"] == f"~{alert.id}"
    assert by_kind["custom"]["entity"] == "Case"  # automation-run and the unknown kind

    for event in body["events"]:
        assert isinstance(event["date"], int)  # ms epoch, not ISO-8601
        assert event["endDate"] is None
        assert isinstance(event["details"], dict)


def test_internal_timeline_ledger_shape_is_unchanged(api: object) -> None:
    case = _case("internal-shape")
    alert = _alert("imported-alert", ref="tl-internal")
    now = timezone.now()
    TimelineEvent.objects.create(
        case=case,
        kind="alert-imported",
        title="Alert imported",
        date=now,
        metadata={"alert_id": str(alert.id)},
    )

    detail = api.get(f"/api/v1/case/{case.number}").json()
    events = [event for event in detail["timeline"] if event["kind"] == "alert-imported"]
    assert len(events) == 1
    internal = events[0]
    # The internal ledger shape (Phase 7 sync protocol / UI) keeps its own vocabulary and keys.
    assert internal["_type"] == "event"
    assert internal["kind"] == "alert-imported"  # unmapped, unlike the API envelope
    assert internal["metadata"] == {"alert_id": str(alert.id)}
    assert set(internal.keys()) >= {
        "_id",
        "id",
        "date",
        "title",
        "description",
        "actor",
        "metadata",
    }


# ---------------------------------------------------------------------------
# T1 gap the AC8.4 live gate surfaced (2026-10-06): `POST /api/v1/alert` (create)
# and the merge response shape. The MVP loop created alerts by webhook, so the API
# create half of plan §7.2's `POST /api/v1/alert → InputCreateAlert` never existed;
# thehive4py's `alert.create` is what AC8.4 drives. Same file because the whole
# point is that the query surface and the entity surface speak one contract.
# ---------------------------------------------------------------------------


def test_alert_create_roundtrip_through_thehive4py_shape(api: object) -> None:
    """AC8.4's `client.alert.create(...)` must work: POST a 5.8.0 `InputCreateAlert`.

    The body is the exact shape thehive4py sends — ms-epoch `date`, wire `source`
    preserved verbatim (M3: never derived from an IngestionSource), required-field
    envelope errors matching the T1 `fields` convention.
    """
    response = api.post(
        "/api/v1/alert",
        {
            "type": "phishing",
            "source": "live-ac84-gate",
            "sourceRef": "ac84-create-roundtrip",
            "title": "Phishing roundtrip",
            "description": "Created through the API.",
            "severity": 3,
            "date": 1745539200000,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["_type"] == "alert"
    assert body["title"] == "Phishing roundtrip"
    assert body["severity"] == 3
    assert body["source"] == "live-ac84-gate"
    assert body["sourceRef"] == "ac84-create-roundtrip"
    assert body["date"]  # ms-epoch on the wire

    # And it is a real row the query surface can list back.
    listed = api.post(
        "/api/v1/query",
        {
            "query": [
                {"_name": "listAlert"},
                {
                    "_name": "filter",
                    "_eq": {"_field": "sourceRef", "_value": "ac84-create-roundtrip"},
                },
            ]
        },
        format="json",
    )
    assert listed.status_code == 200, listed.content
    assert [a["title"] for a in listed.json()] == ["Phishing roundtrip"]


def test_alert_create_requires_source_ref_and_titles(api: object) -> None:
    response = api.post("/api/v1/alert", {"type": "phishing", "source": "s"}, format="json")
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["type"] == "BadRequest"
    assert "title" in body["fields"] and "sourceRef" in body["fields"]


def test_merge_returns_the_case_not_the_alert(api: object) -> None:
    """5.8.0 returns `OutputCase` from `POST /alert/{id}/merge/{caseId}`.

    The pre-Phase-8 view returned the *alert* (`alert_json`), which no TheHive client
    would cope with; thehive4py annotates the call as `-> OutputCase`.
    """
    case = _case("merge-shape-case")
    alert = _alert("merge-shape-alert", ref="merge-shape", case=case)
    response = api.post(f"/api/v1/alert/{alert.id}/merge/{case.number}", {}, format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["_type"] == "case"
    assert body["_id"] == str(case.id)
    assert body["title"] == "merge-shape-case"
