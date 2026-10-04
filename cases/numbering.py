"""Case number allocation and the field that guarantees it happens on every INSERT.

REVIEW-2026-10-03 **C1**: ``Case.save()`` used to run
``CREATE SEQUENCE IF NOT EXISTS case_number_seq`` on every auto-allocated case.
That statement is not valid SQLite (``OperationalError: near "SEQUENCE": syntax
error``), so *creating a case was broken*; the suite stayed green only because the
single test that builds a Case hardcoded ``number=1``, i.e. the auto-allocation path
had never executed. On Postgres the same code path would have been silently
non-monotonic under concurrency, because ``MAX(number)+1`` is not race-safe.

Two backends, two strategies:

* **Postgres** — a real sequence, created by the vendor-guarded ``RunPython`` in
  ``cases/migrations/0005_case_number_sequence.py``, read with ``nextval()``.
  ``nextval`` is atomic and gaps-free-per-transaction, which is what plan §6.1
  ("allocated by a Postgres sequence") requires.
* **SQLite** — ``MAX(number) + 1``. This is only safe because the dev/test SQLite
  database is a **single connection** held by the Django process: there is no second
  writer to race with. Two concurrent transactions *would* both read the same MAX and
  the second INSERT would fail the unique constraint on ``number`` (a loud
  IntegrityError, never a silently duplicated number). Production is Postgres.

Why the allocation lives on the *field* and not only in ``Model.save()``:
``bulk_create()`` never calls ``save()``, so a ``save()``-only guard can be bypassed
and would insert ``NULL`` into a NOT NULL unique column. ``Field.pre_save()`` is the
one hook every INSERT path goes through
(``django.db.models.sql.compiler.SQLInsertCompiler.pre_save_val`` calls it for both
``save()`` and ``bulk_create()``), so deriving the number there makes a null number
structurally impossible. ``Case.save()`` keeps its own explicit guard — it lets the
caller see the allocated number without a round trip, and it is what the reviewer
asked to keep (see the comment there).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Any, TypeVar

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, connections, models, router, transaction

#: The queryset mixin is generic in the model so `bulk_create` keeps its types.
_CaseT = TypeVar("_CaseT", bound=models.Model)

SEQUENCE_NAME = "case_number_seq"


def allocate_case_number(using: str | None = None) -> int:
    """Return the next unused ``Case.number``.

    Postgres: ``nextval()`` on ``case_number_seq`` (monotonic, concurrency-safe).
    SQLite: ``MAX(number) + 1`` (single-connection dev database — see module docstring).
    """
    connection = connections[using or DEFAULT_DB_ALIAS]
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT nextval('{SEQUENCE_NAME}')")
            row = cursor.fetchone()
        return int(row[0]) if row else 1

    # ORM aggregate instead of hand-written SQL: same result, and the table name comes
    # from Case.Meta rather than a string literal here.
    return _max_existing_number(using) + 1


def allocate_case_numbers(count: int, using: str | None = None) -> list[int]:
    """Allocate ``count`` consecutive unused numbers in one shot.

    Needed for `bulk_create()`. The field's `pre_save` cannot do this job: Django renders
    the parameter list for the whole batch *before* executing the INSERT, so every row's
    `pre_save` reads the same `MAX(number)` and the batch collapses onto one number —
    `UNIQUE constraint failed: case_record.number`. Allocating the batch up front, inside
    the caller's transaction, is the only place with the information needed to hand each
    row a distinct value.
    """
    if count < 0:
        raise ValueError(f"count must not be negative, got {count}")
    connection = connections[using or DEFAULT_DB_ALIAS]
    if connection.vendor == "postgresql":
        return [allocate_case_number(using) for _ in range(count)]
    first = _max_existing_number(using) + 1
    return list(range(first, first + count))


def _max_existing_number(using: str | None) -> int:
    case_model = apps.get_model("cases", "Case")
    highest = case_model.objects.using(using or DEFAULT_DB_ALIAS).aggregate(m=models.Max("number"))[
        "m"
    ]
    return int(highest or 0)


class AllocatingQuerySet(models.QuerySet[_CaseT]):
    """Adds `Case.number` allocation to `bulk_create()`.

    Objects that already carry a number keep it (TheHive's import path replays the
    original); the rest are numbered from one allocation for the whole batch.
    """

    def bulk_create(
        self,
        objs: Iterable[_CaseT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[_CaseT]:
        field = self.model._meta.get_field("number")
        attname = field.attname if isinstance(field, models.Field) else None
        pending = [obj for obj in objs if attname is None or getattr(obj, attname) is None]
        if pending:
            using = router.db_for_write(self.model, instance=pending[0])
            with transaction.atomic(using=using):
                for obj, number in zip(
                    pending, allocate_case_numbers(len(pending), using=using), strict=True
                ):
                    if attname is not None:
                        setattr(obj, attname, number)
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class AllocatedNumberField(models.IntegerField):
    """An ``IntegerField`` whose value is allocated by :func:`allocate_case_number`.

    Always NOT NULL and unique — the caller may pass an explicit number (TheHive's
    import path replays the original number), otherwise one is allocated on INSERT.
    """

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, _path, args, kwargs = super().deconstruct()
        # Pin the concrete path: migrations must not depend on this class ever moving.
        return name, "cases.numbering.AllocatedNumberField", args, kwargs

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        if getattr(model_instance, self.attname) is None:
            alias = router.db_for_write(model_instance.__class__, instance=model_instance)
            setattr(model_instance, self.attname, allocate_case_number(using=alias))
        return super().pre_save(model_instance, add)
