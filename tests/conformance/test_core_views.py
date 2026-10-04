import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_readyz_fails_when_no_services():
    client = APIClient()
    resp = client.get("/readyz")
    # Redis not running in test env - should fail
    assert resp.status_code == 503
