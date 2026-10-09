from __future__ import annotations

import pytest

from cases.models import CaseStatus


@pytest.mark.django_db
def test_b_probe_seed_status_present() -> None:
    assert CaseStatus.objects.filter(value="New").exists(), "seeded New status is GONE"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_z_delete_the_seed_row() -> None:
    # Ensure the seed data exists (other tests may have deleted it)
    CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    assert CaseStatus.objects.filter(value="New").exists()
    CaseStatus.objects.filter(value="New").delete()
    assert not CaseStatus.objects.filter(value="New").exists()
