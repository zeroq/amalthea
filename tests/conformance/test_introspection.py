"""REVIEW-2026-10-03 **H1** — schema conformance against the *live* database.

The previous version of this file asserted `len(tables) > 0`, which is true of an empty
database and therefore could not fail. These tests name the fields the plan, ADR-002 and
AGENTS.md require, and read them from the database so that a field which exists only in
`models.py` — or exists under a different column name — is caught.

Review item: H1 (vacuous schema tests).
"""

from __future__ import annotations

import pytest

from ._schema import columns, own_models

# REVIEW-2026-10-03 **H1** — the fields each model is contractually required to expose.
# `plan §6.1`, `ADR-002 §D2` (core schema) and AGENTS.md Module A/B/C/D.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "case_record": (
        # AGENTS.md Module B: a case is titled, described, graded, owned and dated.
        "number",
        "title",
        "description",
        "severity",
        "status_id",
        "owner_org_id",
        "assignee_id",
        "created_at",
        "start_date",
        "closed_date",
        "flag",
        "tlp",
        "pap",
        "ingestion_warnings",
    ),
    "alert": (
        # AGENTS.md Module A: the universal receiver keeps the raw payload intact.
        "title",
        "type",
        "source",
        "source_ref",
        "severity",
        "status_id",
        "date",
        "raw_payload",
        # Module B: an alert may be escalated into a case.
        "case_id",
        # REVIEW C4: the correlation key AC5.2's query actually filters on.
        "correlation_key",
        # REVIEW M2: the wire string and our FK must be separate columns.
        "ingestion_source_id",
    ),
    "observable": (
        # AGENTS.md Module C: observables are first-class rows, not free text.
        "data",
        "normalized_data",
        "data_hash",
        "data_type_id",
        "enrichment_data",
        "created_at",
        "ioc",
    ),
    "task": (
        "title",
        "status",
        # PLAN §6.1 names this `assignee` FK. AGENTS.md §3 says `assigned_to`; the plan
        # wins here, and it matches TheHive's own task payload. Reported as drift.
        "assignee_id",
        "case_id",
        "due_date",
        "flag",
        "mandatory",
    ),
    "automation_run": (
        # AGENTS.md Module D + REVIEW M14: FK to the playbook, immutable name snapshot.
        "playbook_id",
        "playbook_name",
        "status",
        "trigger_event",
        "started_at",
        "finished_at",
        "output_log",
        "error",
        "triggered_by_observable_id",
        "celery_task_id",
        "idempotency_key",
        "case_id",
    ),
    "identity_user": (
        # REVIEW H6: ADR-002 D3 requires a distinct login identifier.
        "id",
        "username",
        "login",
        "is_active",
    ),
    "identity_apikey": (
        # REVIEW M13: a prefix resolves to exactly one key.
        "prefix",
        "scope",
        "created_at",
        "updated_at",
    ),
    "ingestion_source": (
        "slug",
        "name",
        "default_severity",
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize(("table", "expected"), sorted(REQUIRED_COLUMNS.items()))
def test_required_columns_are_in_the_database(table: str, expected: tuple[str, ...]) -> None:
    present = columns(table)
    missing = [name for name in expected if name not in present]
    assert not missing, f"{table} is missing required column(s): {missing}"


@pytest.mark.django_db
def test_every_amalthea_model_has_a_table_in_the_database() -> None:
    """Every declared model has a real table. Guards the whole introspection suite.

    `own_models()` feeds `test_indexes.py`, `test_fk_audit.py` and `test_phase3_schema.py`.
    If a model existed only in `models.py` (or a table was never migrated), the audit
    would silently shrink instead of failing.
    """
    from ._schema import own_tables

    declared = {model._meta.db_table for _, model in own_models()}
    existing = own_tables()
    missing = sorted(declared - existing)
    assert not missing, f"model(s) declared without a table: {missing}"
    # And the reverse: no orphan tables that no model claims.
    orphans = sorted(existing - declared)
    assert not orphans, f"table(s) with no model: {orphans}"


@pytest.mark.django_db
def test_inventory_is_not_vacuous() -> None:
    """Guards the guard: the model inventory this module audits must be non-trivial.

    `own_models()` underpins every assertion in the conformance suite. If an app label
    were renamed or an app removed from `INSTALLED_APPS`, the suite would otherwise keep
    passing while auditing a handful of models, so pin the count.
    """
    models = own_models()
    assert len(models) >= 20, f"expected Amalthea's own model inventory, got {len(models)}"
    labels = {label for label, _ in models}
    assert {"alerts", "automation", "cases", "identity", "ingest", "observables"} <= labels
