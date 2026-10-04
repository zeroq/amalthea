import json
from pathlib import Path

import pytest


@pytest.mark.django_db
def test_load_case_fixture():
    path = Path(__file__).parent.parent / "fixtures" / "thehive" / "case_example.json"
    data = json.loads(path.read_text())
    assert data["_id"] == "~98112"
    assert data["title"] == "Suspicious Ransomware Activity"
    assert data["severity"] == 3
    assert data["status"] == "InProgress"
