import pytest


@pytest.mark.django_db
def test_severity_roundtrip():
    from compat import enums

    assert enums.Severity.LOW.value == 1
    assert enums.SEVERITY_LABELS[3] == "High"


@pytest.mark.django_db
def test_tlp_pap():
    from compat import enums

    assert enums.TLP.RED.value == 3
    assert enums.PAP.AMBER.value == 2


@pytest.mark.django_db
def test_task_status():
    from compat import enums

    assert enums.TaskStatus.WAITING == "Waiting"
