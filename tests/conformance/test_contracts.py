import pytest


@pytest.mark.django_db
def test_time_parsing_contract():
    from compat import time

    dt = time.parse_timestamp(1745539200000)
    assert time.to_epoch_ms(dt) == 1745539200000
    dt = time.parse_timestamp("2026-10-03T00:00:00Z")
    assert time.to_epoch_ms(dt) > 0
    assert time.parse_timestamp(None) is None


@pytest.mark.django_db
def test_severity_contract():
    from compat import enums

    assert enums.SEVERITY_LABELS[1] == "Low"
    assert enums.SEVERITY_LABELS[4] == "Critical"
