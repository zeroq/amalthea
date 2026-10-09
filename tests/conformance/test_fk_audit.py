"""REVIEW-2026-10-03 **H1** / round-2 **C-2** — foreign-key audit.

The previous version of this test read `Case._meta.get_fields()` and accepted a
`related_name` of `None` as long as `get_accessor_name()` returned anything — which
Django always does, defaulting to `<model>_set`. The assertion could therefore not fail.

Round 2 then found the replacement's "every FK declares an explicit `on_delete`"
assertion was *also* vacuous, and this time measured it: with `Alert.case` mutated from
`SET_NULL` to `CASCADE` — a change that makes deleting a Case silently destroy every Alert
escalated into it — all three `on_delete` assertions still passed.

Two facts, one of which corrects the review's own explanation:

* The stated mechanism is wrong for this Django. On 5.2.17
  `ForeignKey.deconstruct()` **always** emits `on_delete`, CASCADE included (verified:
  `Task.case` deconstructs to `{'related_name': 'tasks', 'on_delete': CASCADE, 'to': ...}`).
* That makes the helper *unconditionally* true. `"on_delete" in field.deconstruct()[3]`
  passes for every field that exists, so the audit proved nothing about any field.

The fix is not a better predicate; it is to stop guessing and **declare the policy**. A
`PROTECT`-only subset check cannot catch `SET_NULL` → `CASCADE`, because the mutated field
simply leaves the subset. `EXPECTED_ON_DELETE` below names the intended behaviour of *every*
foreign key in the schema, so both directions are load-bearing:

* a value that disagrees with the table fails (`SET_NULL` → `CASCADE`, `CASCADE` → `SET_NULL`);
* a field missing from the table fails (a new FK with no reviewed policy);
* a table entry with no field fails (a policy that silently stopped applying).

The table is derived from PLAN §6.1 and ADR-002 §D5:

* **PROTECT** on vocabulary a live row points at — `Case.status`, `Alert.status`,
  `Observable.data_type`, `AutomationRun.playbook`. Deleting the vocabulary row would
  orphan evidence or silently re-type it.
* **SET_NULL** where the referencing row is *longer-lived* than the target: an `Alert`
  outlives the Case it was escalated into (TheHive's unlink-on-delete), a `Case` and an
  `Alert` outlive their `assignee` and their `Organisation`, a `TimelineEvent` outlives its
  actor, an `AutomationRun` outlives the case and observable that triggered it, and a
  credential's `ApiKey` is *not* in this class — see below.
* **CASCADE** only on genuinely case-owned children (`Task`, `TimelineEvent`,
  `CaseObservable`, `CaseCustomFieldValue`) and on `ApiKey.user`, where a token that
  outlived its owner would be a credential nobody can revoke.

`test_delete_semantics_*` then proves the policy *behaviourally*, because a table of strings
that nobody deletes anything against is documentation, not a test.

Review items: H1 (vacuous test), L3 (on_delete policy), C-2 (audit blind to CASCADE),
M5 (reverse-direction indexes).
"""

from __future__ import annotations

import pytest
from django.db import connection, models
from django.db.models.deletion import ProtectedError

from ._schema import foreign_keys, own_models

#: `(table, columns)` pairs that must exist as real constraints in the database.
EXPECTED_FK_COLUMNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "case_record": (("status_id",), ("assignee_id",), ("owner_org_id",)),
    # REVIEW M2: `source_id` was renamed to `ingestion_source_id`; the old column name is
    # asserted absent in test_phase3_schema.py.
    "alert": (
        ("status_id",),
        ("assignee_id",),
        ("owner_org_id",),
        ("case_id",),
        ("ingestion_source_id",),
    ),
    "observable": (("data_type_id",),),
    "task": (("case_id",), ("assignee_id",)),
    "automation_run": (
        ("case_id",),
        ("alert_id",),
        ("playbook_id",),
        ("triggered_by_observable_id",),
    ),
    "case_observable": (("case_id",), ("observable_id",), ("added_by_id",)),
    "alert_observable": (("alert_id",), ("observable_id",), ("added_by_id",)),
    "timeline_event": (("case_id",), ("actor_id",)),
    "attachment": (("case_id",), ("created_by_id",), ("updated_by_id",)),
    "case_custom_field_value": (("case_id",), ("custom_field_id",)),
    "alert_custom_field_value": (("alert_id",), ("custom_field_id",)),
    "identity_apikey": (("user_id",),),
    "identity_user": (("org_id",),),
    "case_status": (),
    "custom_field": (),
}

#: **The declared `on_delete` policy for every foreign key in Amalthea's schema.**
#:
#: `(app_label, model name, field name) -> on_delete callable`, compared by identity
#: against `field.remote_field.on_delete`. Read that attribute directly and never
#: `deconstruct()` — see the module docstring for why the `deconstruct()` route cannot work.
EXPECTED_ON_DELETE: dict[tuple[str, str, str], object] = {
    # ---- Case -------------------------------------------------------------------------
    # PLAN §6.1 gives `severity` a 1..4 domain and `status` a vocabulary row; a case whose
    # status row vanished could not be rendered, queued or closed.
    ("cases", "Case", "status"): models.PROTECT,
    # An analyst leaving the organisation must not delete the cases they worked.
    ("cases", "Case", "assignee"): models.SET_NULL,
    ("cases", "Case", "owner_org"): models.SET_NULL,
    # ---- Task: AGENTS.md §3 "Case 1 —— 0..* Task" ---------------------------------------
    # A task with a NULL case is not a lower-cardinality relationship, it is an orphan.
    ("cases", "Task", "case"): models.CASCADE,
    ("cases", "Task", "assignee"): models.SET_NULL,
    # ---- Timeline: the collaborative ledger (AGENTS.md Module B) ------------------------
    ("cases", "TimelineEvent", "case"): models.CASCADE,
    ("cases", "TimelineEvent", "actor"): models.SET_NULL,
    # ---- Custom-field values are owned by the row that carries them --------------------
    ("cases", "CaseCustomFieldValue", "case"): models.CASCADE,
    ("cases", "CaseCustomFieldValue", "custom_field"): models.CASCADE,
    # ---- Link tables have no meaning once either endpoint is gone ---------------------
    ("cases", "CaseObservable", "case"): models.CASCADE,
    ("cases", "CaseObservable", "observable"): models.CASCADE,
    ("cases", "CaseObservable", "added_by"): models.SET_NULL,
    # ---- T2 P2 collaboration: comments, pages, shares ----------------------------------
    # A comment/page is a case- or alert-owned child: deleting the parent must delete it, or it
    # becomes an orphan no view can reach. The author columns are audit, not ownership: they
    # outlive a departing analyst, so SET_NULL.
    ("cases", "Comment", "case"): models.CASCADE,
    ("cases", "Comment", "alert"): models.CASCADE,
    ("cases", "Comment", "created_by"): models.SET_NULL,
    ("cases", "Comment", "updated_by"): models.SET_NULL,
    ("cases", "Page", "case"): models.CASCADE,
    ("cases", "Page", "created_by"): models.SET_NULL,
    ("cases", "Page", "updated_by"): models.SET_NULL,
    # A share *is* the grant of one case to one organisation, so it has no meaning once either
    # endpoint is gone; CASCADE in both directions. Deleting an org revokes its grants.
    ("cases", "Share", "case"): models.CASCADE,
    ("cases", "Share", "organisation"): models.CASCADE,
    ("cases", "Share", "created_by"): models.SET_NULL,
    # ---- T2 P3 attachments -------------------------------------------------------------
    # A blob is case-owned: deleting the case must delete the row (the view deletes the blob). The
    # author columns are audit, so they outlive a departing analyst.
    ("cases", "Attachment", "case"): models.CASCADE,
    ("cases", "Attachment", "created_by"): models.SET_NULL,
    ("cases", "Attachment", "updated_by"): models.SET_NULL,
    # ---- Alert ------------------------------------------------------------------------
    ("alerts", "Alert", "status"): models.PROTECT,
    # **TheHive's unlink-on-delete** (ADR-002): deleting a case must not destroy the alerts
    # that produced it, they go back to the triage queue.
    ("alerts", "Alert", "case"): models.SET_NULL,
    ("alerts", "Alert", "assignee"): models.SET_NULL,
    ("alerts", "Alert", "owner_org"): models.SET_NULL,
    # Retiring a feed configuration must not rewrite history by deleting what it received.
    ("alerts", "Alert", "ingestion_source"): models.SET_NULL,
    ("alerts", "AlertCustomFieldValue", "alert"): models.CASCADE,
    ("alerts", "AlertCustomFieldValue", "custom_field"): models.CASCADE,
    ("alerts", "AlertObservable", "alert"): models.CASCADE,
    ("alerts", "AlertObservable", "observable"): models.CASCADE,
    ("alerts", "AlertObservable", "added_by"): models.SET_NULL,
    # ---- Observable: ADR-002 §D5, globally deduplicated, never case-owned ---------------
    # PROTECT: deleting the type would silently re-type every artifact filed under it.
    ("observables", "Observable", "data_type"): models.PROTECT,
    # ---- AutomationRun: the Module D ledger -------------------------------------------
    ("automation", "AutomationRun", "playbook"): models.PROTECT,
    # A finished (or failed) run is audit evidence; it outlives the case that triggered it.
    ("automation", "AutomationRun", "case"): models.SET_NULL,
    ("automation", "AutomationRun", "alert"): models.SET_NULL,
    ("automation", "AutomationRun", "triggered_by_observable"): models.SET_NULL,
    # ---- Identity ---------------------------------------------------------------------
    ("identity", "User", "org"): models.SET_NULL,
    # CASCADE, deliberately not SET_NULL: a bearer token whose owner no longer exists is a
    # credential nobody can revoke.
    ("identity", "ApiKey", "user"): models.CASCADE,
    # ---- M2M join rows (declared explicitly; see REVIEW L-2) ----------------------------
    # These exist only to carry the M2M edge, so CASCADE is the only correct answer in every
    # direction: the row has no independent meaning. Deleting the parent must delete the edge,
    # and deleting the Tag must delete every edge pointing at it. PROTECT here would make a Tag
    # undeletable while any case still referenced it; SET_NULL is impossible (the columns are
    # NOT NULL), which is exactly why these FKs are named explicitly instead of relying on the
    # implicit M2M table.
    ("cases", "CaseTagLink", "case"): models.CASCADE,
    ("cases", "CaseTagLink", "tag"): models.CASCADE,
    ("alerts", "AlertTagLink", "alert"): models.CASCADE,
    ("alerts", "AlertTagLink", "tag"): models.CASCADE,
    ("observables", "ObservableTagLink", "observable"): models.CASCADE,
    ("observables", "ObservableTagLink", "tag"): models.CASCADE,
}


def declared_on_delete(field: models.ForeignKey) -> object:
    """The field's `on_delete`, read from the only place it can be read unambiguously.

    `deconstruct()` is deliberately not used. On Django 5.2.17 it always emits `on_delete`,
    CASCADE included, so `"on_delete" in field.deconstruct()[3]` is a tautology for every
    field that exists — including one whose author wrote no `on_delete=` at all, because
    Django then defaults it to CASCADE. `remote_field.on_delete` is the resolved value with
    no information loss, and `EXPECTED_ON_DELETE` is what makes reading it meaningful.
    """
    return field.remote_field.on_delete


def fk_inventory() -> dict[tuple[str, str, str], object]:
    """`{(app_label, model name, field name): on_delete}` for every FK Amalthea owns."""
    return {
        (model._meta.app_label, model.__name__, field.name): declared_on_delete(field)
        for _, model in own_models()
        for field in model._meta.fields
        if field.many_to_one
    }


@pytest.mark.django_db
def test_fk_audit_covers_the_whole_model_inventory() -> None:
    """Guards the guard: the audit must not quietly shrink to nothing.

    If an app were dropped from `_schema.OWN_APPS`, every assertion below would keep
    passing while auditing fewer models, so the inventory is pinned against the policy
    table itself — both directions, so neither the audit nor the table can be trimmed.
    """
    actual = fk_inventory()
    assert set(actual) == set(EXPECTED_ON_DELETE), (
        "the set of foreign keys changed. Every FK needs an entry in EXPECTED_ON_DELETE: "
        f"undeclared={sorted(set(actual) - set(EXPECTED_ON_DELETE))}, "
        f"stale={sorted(set(EXPECTED_ON_DELETE) - set(actual))}"
    )
    # 45 = 29 pre-L-2, plus the six `*TagLink` join FKs declared explicitly for **L-2**, plus the
    # ten T2 P2 collaboration FKs (Comment x4, Page x3, Share x3), plus the three T2 P3 attachment
    # FKs (Attachment x3). Kept as a literal on purpose: it is a tripwire for a model silently
    # dropping out of `OWN_APPS`, and `EXPECTED_ON_DELETE` already pins the exact set, so a bump
    # here is always a real change.
    assert len(actual) == 48, f"expected the full FK set, found only {len(actual)}"


@pytest.mark.django_db
def test_every_fk_matches_the_declared_on_delete_policy() -> None:
    """**The C-2 fix.** Pin `on_delete` for every relation, not just the PROTECT subset.

    A `PROTECT`-only check cannot see a `SET_NULL` → `CASCADE` swap, because the mutated
    field simply leaves the subset the check looks at. Naming every field closes that hole:
    `Alert.case` and `Case.assignee` are CASCADE here and the test fails, and so does
    `Task.case` if it is weakened to SET_NULL and orphans tasks out of the AGENTS.md §3
    `Case 1 —— 0..* Task` relationship.
    """
    actual = fk_inventory()
    wrong = {
        key: (actual[key].__name__, EXPECTED_ON_DELETE[key].__name__)  # type: ignore[union-attr]
        for key in EXPECTED_ON_DELETE
        if key in actual and actual[key] is not EXPECTED_ON_DELETE[key]
    }
    assert not wrong, (
        "on_delete policy changed (actual, expected) — see the module docstring for why each "
        f"relation has the value it has: {wrong}"
    )


@pytest.mark.django_db
def test_every_fk_declares_a_related_name() -> None:
    offenders = [
        f"{model.__module__}.{model.__name__}.{field.name}"
        for _, model in own_models()
        for field in model._meta.fields
        if field.many_to_one and field.remote_field.related_name is None
    ]
    assert not offenders, (
        "FK(s) without an explicit related_name; a second FK to the same target would "
        f"shadow the reverse accessor: {offenders}"
    )


@pytest.mark.django_db
def test_no_cascade_is_reachable_from_a_longer_lived_row() -> None:
    """`CASCADE` is only ever correct where the child cannot outlive the parent.

    Enumerated explicitly rather than derived, because "can this child outlive its parent"
    is a judgement about the domain, not a fact the schema can supply. The entries
    below are the whole `CASCADE` surface; if one of them is deleted from the policy this
    test fails, so the review that authorised it cannot be un-done by accident.
    """
    cascades = {key for key, value in EXPECTED_ON_DELETE.items() if value is models.CASCADE}
    assert cascades == {
        ("cases", "Task", "case"),
        ("cases", "TimelineEvent", "case"),
        ("cases", "CaseCustomFieldValue", "case"),
        ("cases", "CaseCustomFieldValue", "custom_field"),
        ("cases", "CaseObservable", "case"),
        ("cases", "CaseObservable", "observable"),
        ("alerts", "AlertCustomFieldValue", "alert"),
        ("alerts", "AlertCustomFieldValue", "custom_field"),
        ("alerts", "AlertObservable", "alert"),
        ("alerts", "AlertObservable", "observable"),
        ("identity", "ApiKey", "user"),
        # M2M join rows (L-2): pure edges with no independent lifetime, so no parent can
        # outlive them in either direction. Same reasoning as Task/TimelineEvent, but listed
        # separately because these were introduced after the original CASCADE review.
        ("cases", "CaseTagLink", "case"),
        ("cases", "CaseTagLink", "tag"),
        ("alerts", "AlertTagLink", "alert"),
        ("alerts", "AlertTagLink", "tag"),
        ("observables", "ObservableTagLink", "observable"),
        ("observables", "ObservableTagLink", "tag"),
        # T2 P2 collaboration. A comment, page or share cannot outlive its case (or its alert,
        # for a comment); a share also dies with the organisation it points at, because the grant
        # names that organisation and has no meaning once it is gone.
        ("cases", "Comment", "case"),
        ("cases", "Comment", "alert"),
        ("cases", "Page", "case"),
        ("cases", "Share", "case"),
        ("cases", "Share", "organisation"),
        # T2 P3 attachments. A blob row is case-owned — the upload view deletes the file when the
        # row goes — so it cannot outlive the case it was filed against.
        ("cases", "Attachment", "case"),
    }, f"the CASCADE surface changed and needs a re-review: {sorted(cascades)}"


# ---------------------------------------------------------------------------------------
# Behaviour. EXPECTED_ON_DELETE is a table of intent; this half deletes rows.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def vocab() -> dict[str, object]:
    from alerts.models import AlertStatus
    from cases.models import CaseStatus, CustomField
    from observables.models import ObservableType

    return {
        "case_status": CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0],
        "alert_status": AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0],
        "custom_field": CustomField.objects.create(name="Impact", type="string"),
        "observable_type": ObservableType.objects.get_or_create(
            name="ip", defaults={"is_case_sensitive": False}
        )[0],
    }


@pytest.mark.django_db
def test_delete_semantics_deleting_a_case_unlinks_alerts_and_automation_runs(vocab) -> None:
    """`Alert.case` is SET_NULL (C-2): deleting a Case must not destroy its alerts.

    The mutation round 2 made and the suite accepted was `SET_NULL` → `CASCADE` here. With
    this test the same mutation destroys the alerts, and the assertion fails on the row
    count rather than on a metadata string.
    """
    from alerts.models import Alert
    from automation.models import AutomationRun, Playbook
    from cases.models import Case

    case = Case.objects.create(title="victim", status=vocab["case_status"])
    alert = Alert.objects.create(
        type="t", source="s", source_ref="r1", title="a", status=vocab["alert_status"], case=case
    )
    playbook = Playbook.objects.create(name="enrich", trigger_event="alert.created")
    run = AutomationRun.objects.create(
        case=case, playbook=playbook, playbook_name="enrich", idempotency_key="k1"
    )

    case.delete()

    alert.refresh_from_db()
    run.refresh_from_db()
    assert alert.pk is not None, "deleting a Case must NOT delete the Alerts escalated into it"
    assert alert.case_id is None, "the alert must be unlinked, not orphaned"
    assert run.pk is not None, "deleting a Case must NOT delete its AutomationRun ledger"
    assert run.case_id is None


@pytest.mark.django_db
def test_delete_semantics_deleting_a_case_cascades_to_tasks_and_the_ledger(vocab) -> None:
    """The other half of C-2: `Task.case` CASCADE → SET_NULL orphans tasks.

    AGENTS.md §3 declares `Case 1 —— 0..* Task`. A task with `case_id IS NULL` is not a
    lower-cardinality relationship, it is a row nobody can reach from the case ledger.
    """
    from cases.models import Case, Task, TimelineEvent

    case = Case.objects.create(title="victim", status=vocab["case_status"])
    task = Task.objects.create(case=case, title="contain")
    event = TimelineEvent.objects.create(case=case, title="analyst note", date=case.start_date)

    case.delete()

    assert not Task.objects.filter(pk=task.pk).exists(), "case-owned tasks must cascade"
    assert not TimelineEvent.objects.filter(pk=event.pk).exists(), "the ledger must cascade"


@pytest.mark.django_db
def test_delete_semantics_deleting_a_user_unlinks_their_work(vocab) -> None:
    """`Case.assignee` / `Alert.assignee` are SET_NULL — the opposite of C-2's mutation.

    Round 2 also flipped `Case.assignee` to CASCADE with a green suite, which would let one
    `DELETE FROM identity_user` remove an analyst's entire caseload.
    """
    from alerts.models import Alert
    from cases.models import Case
    from identity.models import ApiKey, User

    analyst = User.objects.create(username="ada")
    case = Case.objects.create(title="mine", status=vocab["case_status"], assignee=analyst)
    alert = Alert.objects.create(
        type="t",
        source="s",
        source_ref="r1",
        title="a",
        status=vocab["alert_status"],
        assignee=analyst,
    )
    key = ApiKey.objects.create(user=analyst, prefix="p1234567", key_hash="x")

    analyst.delete()

    case.refresh_from_db()
    alert.refresh_from_db()
    assert case.pk is not None and case.assignee_id is None
    assert alert.pk is not None and alert.assignee_id is None
    assert not ApiKey.objects.filter(pk=key.pk).exists(), (
        "a bearer token must not outlive its owner — nobody could revoke it"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "delete_target", ["case_status", "alert_status", "observable_type", "playbook"]
)
def test_delete_semantics_vocabulary_rows_are_protected(delete_target: str, vocab) -> None:
    """PROTECT where a live row points at the target.

    Without it, deleting one `ObservableType` row silently re-files every artifact of that
    type, and deleting a `Playbook` leaves runs pointing at a definition that no longer
    exists (REVIEW M14).
    """
    from automation.models import AutomationRun, Playbook
    from cases.models import Case
    from observables.models import Observable

    if delete_target == "playbook":
        playbook = Playbook.objects.create(name="enrich", trigger_event="alert.created")
        case = Case.objects.create(title="c", status=vocab["case_status"])
        AutomationRun.objects.create(
            case=case, playbook=playbook, playbook_name="enrich", idempotency_key="k2"
        )
        target: object = playbook
    elif delete_target == "case_status":
        case = Case.objects.create(title="c", status=vocab["case_status"])
        target = case.status
    elif delete_target == "alert_status":
        from alerts.models import Alert

        alert = Alert.objects.create(
            type="t", source="s", source_ref="r1", title="a", status=vocab["alert_status"]
        )
        target = alert.status
    else:
        Observable.objects.create(
            data_type=vocab["observable_type"], data="1.2.3.4", normalized_data="1.2.3.4"
        )
        target = vocab["observable_type"]

    with pytest.raises(ProtectedError):
        target.delete()  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_delete_semantics_deleting_a_custom_field_removes_only_its_values(vocab) -> None:
    """`custom_field` CASCADE: a definition owns its values, and nothing else."""
    from cases.models import Case, CaseCustomFieldValue

    case = Case.objects.create(title="c", status=vocab["case_status"])
    value = CaseCustomFieldValue.objects.create(
        case=case, custom_field=vocab["custom_field"], value="High"
    )

    vocab["custom_field"].delete()  # type: ignore[attr-defined]

    assert not CaseCustomFieldValue.objects.filter(pk=value.pk).exists()
    assert Case.objects.filter(pk=case.pk).exists(), "the case itself is not a child of the field"


@pytest.mark.django_db
def test_delete_semantics_deleting_an_observable_removes_links_but_keeps_runs(vocab) -> None:
    """Module C: the artifact is global, so both link tables go, but the run ledger stays."""
    from automation.models import AutomationRun, Playbook
    from cases.models import Case, CaseObservable
    from observables.models import Observable

    case = Case.objects.create(title="c", status=vocab["case_status"])
    observable = Observable.objects.create(
        data_type=vocab["observable_type"], data="9.9.9.9", normalized_data="9.9.9.9"
    )
    link = CaseObservable.objects.create(case=case, observable=observable)
    playbook = Playbook.objects.create(name="vt", trigger_event="observable.created")
    run = AutomationRun.objects.create(
        case=case,
        playbook=playbook,
        playbook_name="vt",
        triggered_by_observable=observable,
        idempotency_key="k3",
    )

    observable.delete()

    assert not CaseObservable.objects.filter(pk=link.pk).exists()
    run.refresh_from_db()
    assert run.pk is not None and run.triggered_by_observable_id is None


@pytest.mark.django_db
def test_delete_semantics_deleting_an_organisation_or_feed_unlinks(vocab) -> None:
    """`owner_org` and `ingestion_source` are SET_NULL in both directions."""
    from alerts.models import Alert
    from cases.models import Case
    from identity.models import Organisation, User
    from ingest.models import IngestionSource

    org = Organisation.objects.create(name="acme")
    member = User.objects.create(username="bob", org=org)
    case = Case.objects.create(title="c", status=vocab["case_status"], owner_org=org)
    source = IngestionSource.objects.create(slug="splunk", name="Splunk")
    alert = Alert.objects.create(
        type="t",
        source="s",
        source_ref="r1",
        title="a",
        status=vocab["alert_status"],
        owner_org=org,
        ingestion_source=source,
    )

    org.delete()
    source.delete()

    case.refresh_from_db()
    alert.refresh_from_db()
    member.refresh_from_db()
    assert case.pk is not None and case.owner_org_id is None
    assert alert.pk is not None and alert.ingestion_source_id is None
    assert member.pk is not None and member.org_id is None


@pytest.mark.django_db
@pytest.mark.parametrize(("table", "expected"), sorted(EXPECTED_FK_COLUMNS.items()))
def test_expected_fk_columns_exist_in_the_database(
    table: str, expected: tuple[tuple[str, ...], ...]
) -> None:
    present = foreign_keys(table)
    missing = [cols for cols in expected if cols not in present]
    assert not missing, f"{table} is missing foreign key(s): {missing}"


@pytest.mark.django_db
def test_database_fks_match_the_models() -> None:
    """Compare the database's FK constraints with `models.py`, table by table.

    The original test compared Django's in-memory model graph against itself, so a
    constraint that existed only in `models.py` — or only in the database — passed.
    """
    mismatches: list[str] = []
    for _, model in own_models():
        table = model._meta.db_table
        declared = {
            (f.column, f.target_field.model._meta.db_table)
            for f in model._meta.fields
            if f.many_to_one and f.column
        }
        actual = {(cols[0], target[0]) for cols, target in _fks_in_database(table)}
        if declared != actual:
            mismatches.append(f"{table}:\n    model={sorted(declared)}\n    db   ={sorted(actual)}")
    assert not mismatches, "FK mismatch between models.py and the database:\n" + "\n".join(
        mismatches
    )


def _fks_in_database(table: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    """`{(local columns, referenced columns)}` for every FK constraint on `table`."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {
        (tuple(spec["columns"]), tuple(spec["foreign_key"]))
        for spec in constraints.values()
        if spec.get("foreign_key")
    }
