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


# --------------------------------------------------------------------------------------
# REVIEW-2026-10-04 round-2 **M-3** (TODO 1.19): `stage_from_alert_status` was untested.
#
# The function is the M12 contract: an alert's status must map onto a stage the seeded
# AlertStatus vocabulary actually contains. The historical bug was `"imported"` appearing in
# *two* branches — the Closed tuple and a later `Imported` branch — so the second was
# unreachable and `stage_from_alert_status("Imported")` returned a stage the schema had no
# row for. Nothing caught it: the suite never called this function.
#
# The table is therefore asserted **against the database**, not against a literal restated
# from the implementation. Restating the mapping here would be the round-2 C-1 defect again:
# both sides would move together.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_m3_every_alert_status_maps_to_a_seeded_stage() -> None:
    from alerts.models import AlertStatus
    from compat import enums

    seeded = {row.value: row.stage for row in AlertStatus.objects.all()}
    assert seeded, "the AlertStatus vocabulary must be seeded by this point"

    for status in sorted(seeded):
        mapped = enums.stage_from_alert_status(status)
        assert mapped in set(seeded.values()), (
            f"stage_from_alert_status({status!r}) -> {mapped!r}, which no seeded AlertStatus "
            f"row carries. The importer would build a case against a stage the schema "
            f"rejects. Seeded stages: {sorted(set(seeded.values()))}"
        )

    # The weaker membership check above is satisfied by any *legal* stage, so it cannot
    # see a status that lands on the wrong one. Where the vocabulary names a stage after
    # itself (`New`->New, `Imported`->Imported, `Closed`->Closed) that mapping is forced by
    # the seeded data alone, and it is exactly the M12 case.
    self_named = {v: s for v, s in seeded.items() if v == s}
    assert self_named, "the vocabulary should contain at least one self-named stage"
    for status, stage in sorted(self_named.items()):
        assert enums.stage_from_alert_status(status) == stage, (
            f"stage_from_alert_status({status!r}) -> {enums.stage_from_alert_status(status)!r}, "
            f"but the seeded AlertStatus row for {status!r} carries stage {stage!r}. A status "
            f"that is named after its own stage must map to it, or the importer silently "
            f"relabels an {status!r} alert."
        )


@pytest.mark.django_db
def test_m3_imported_maps_to_itself_and_is_not_closed() -> None:
    """The exact defect M12 reported, pinned in both letter and case."""
    from compat import enums

    assert enums.stage_from_alert_status("Imported") == "Imported"
    assert enums.stage_from_alert_status("imported") == "Imported"


@pytest.mark.django_db
def test_m3_the_mapping_is_case_insensitive_over_the_full_vocabulary() -> None:
    from alerts.models import AlertStatus
    from compat import enums

    for status in sorted(AlertStatus.objects.values_list("value", flat=True)):
        assert enums.stage_from_alert_status(status.lower()) == enums.stage_from_alert_status(
            status.upper()
        ), f"mapping is case-sensitive for {status!r}; wire payloads are not case-normalised"


@pytest.mark.django_db
def test_m3_unknown_statuses_degrade_to_new_rather_than_raising() -> None:
    from compat import enums

    for bogus in ("", "TotallyMadeUp", "  ", "Closed "):
        assert enums.stage_from_alert_status(bogus) == "New", (
            f"an unrecognised alert status {bogus!r} must degrade to New so an import can "
            f"still proceed; it must not raise or invent a stage."
        )
