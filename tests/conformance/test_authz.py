import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_bad_api_key_returns_401():
    client = APIClient(HTTP_AUTHORIZATION="Bearer invalidkey123456789012")
    resp = client.get("/api/v1/nonexistent")
    assert resp.status_code == 401
    assert resp.json()["type"] == "AuthenticationError"
