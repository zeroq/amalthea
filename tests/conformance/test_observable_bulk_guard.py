"""AC-A5 / TODO 6.5 — `observable/_bulk` must enforce the cross-case force guard.

`PATCH /api/v1/observable/{id}` refuses to mutate an observable shared by more than one case
unless the caller repeats the request with `?force=true` (commit 239188b). The `_bulk` endpoint
called `_set_observable_fields` directly through `bulk_patch`, so the same mutation slipped
through the batch path with no 409 and no force acknowledgement. These tests pin the bulk surface
to the single-item rule:

* a shared observable without `?force=true` -> 409, body in the single-item shape, row untouched;
* the same request with `?force=true` -> applied, per-item 200;
* a single-case observable -> applied with no force at all;
* a batch mixing a shared and a single-case observable -> the whole batch aborts before writing,
  proving the guard is a pre-flight rather than a per-item check.

Non-vacuous: delete the `_bulk_cross_case_guard` pre-flight from `observable_bulk_update` and
`test_bulk_patch_on_shared_observable_without_force_is_refused` flips to 200 with the row mutated.
"""

from __future__ import annotations

from rest_framework.test import APIClient

from cases.models import Case, CaseObservable, CaseStatus
from identity.models import User
from observables.models import Observable, ObservableType


def _case(title: str, analyst: User) -> Case:
    return Case.objects.create(
        title=title,
        severity=2,
        status=CaseStatus.objects.get(value="New"),
        owner_org=analyst.org,
    )


def _observable(value: str) -> Observable:
    return Observable.objects.create(
        data_type=ObservableType.objects.get(name="ip"),
        data=value,
        normalized_data=value,
    )


def test_bulk_patch_on_shared_observable_without_force_is_refused(
    api: APIClient, analyst: User
) -> None:
    """A 2-case observable PATCHed via `_bulk` without force is a 409 and is not written."""
    case1 = _case("Bulk Shared One", analyst)
    case2 = _case("Bulk Shared Two", analyst)
    observable = _observable("10.20.30.40")
    CaseObservable.objects.create(case=case1, observable=observable)
    CaseObservable.objects.create(case=case2, observable=observable)

    response = api.patch(
        "/api/v1/observable/_bulk",
        {"ids": [str(observable.id)], "message": "must not apply"},
        format="json",
    )

    assert response.status_code == 409, response.content
    body = response.json()
    assert body["type"] == "CrossCaseMutationError"
    assert {c["case_number"] for c in body["affected_cases"]} == {case1.number, case2.number}

    observable.refresh_from_db()
    assert observable.message == "", "the shared observable was mutated despite the 409"


def test_bulk_patch_on_shared_observable_with_force_is_applied(
    api: APIClient, analyst: User
) -> None:
    """`?force=true` acknowledges the blast radius and the batch applies per-item."""
    case1 = _case("Bulk Forced One", analyst)
    case2 = _case("Bulk Forced Two", analyst)
    observable = _observable("10.20.30.41")
    CaseObservable.objects.create(case=case1, observable=observable)
    CaseObservable.objects.create(case=case2, observable=observable)

    response = api.patch(
        "/api/v1/observable/_bulk?force=true",
        {"ids": [str(observable.id)], "message": "forced bulk update"},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["updated"] == 1
    assert body["results"] == [{"id": str(observable.id), "status": 200}]

    observable.refresh_from_db()
    assert observable.message == "forced bulk update"


def test_bulk_patch_on_single_case_observable_needs_no_force(api: APIClient, analyst: User) -> None:
    """A single-case observable carries no blast radius, so the batch applies without force."""
    case = _case("Bulk Solo", analyst)
    observable = _observable("10.20.30.42")
    CaseObservable.objects.create(case=case, observable=observable)

    response = api.patch(
        "/api/v1/observable/_bulk",
        {"ids": [str(observable.id)], "message": "solo bulk update"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["updated"] == 1

    observable.refresh_from_db()
    assert observable.message == "solo bulk update"


def test_bulk_patch_aborts_atomically_when_any_target_is_shared(
    api: APIClient, analyst: User
) -> None:
    """The guard is a pre-flight: one shared id aborts the batch before any row is written."""
    shared_case1 = _case("Mixed Shared One", analyst)
    shared_case2 = _case("Mixed Shared Two", analyst)
    solo_case = _case("Mixed Solo", analyst)
    shared = _observable("10.20.30.43")
    solo = _observable("10.20.30.44")
    CaseObservable.objects.create(case=shared_case1, observable=shared)
    CaseObservable.objects.create(case=shared_case2, observable=shared)
    CaseObservable.objects.create(case=solo_case, observable=solo)

    response = api.patch(
        "/api/v1/observable/_bulk",
        {"ids": [str(solo.id), str(shared.id)], "message": "mixed batch"},
        format="json",
    )

    assert response.status_code == 409, response.content
    shared.refresh_from_db()
    solo.refresh_from_db()
    assert shared.message == "", "the shared observable was mutated"
    assert solo.message == "", "the unshared sibling was written before the guard aborted the batch"
