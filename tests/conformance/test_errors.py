import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_error_envelope_unauth():
    client = APIClient()
    resp = client.get("/api/v1/nonexistent")
    assert resp.status_code == 401
    body = resp.json()
    assert set(body.keys()) == {"type", "message"}
    assert body["type"] == "AuthenticationError"
