"""REVIEW-2026-10-03 **C1** — create the Postgres sequence that allocates `Case.number`.

Plan §6.1/§6.3 require `Case.number` to come from a Postgres sequence. The original
attempt to do this from `Case.save()` with
``CREATE SEQUENCE IF NOT EXISTS case_number_seq`` was wrong twice over: the statement is
not valid SQLite (every auto-allocated case raised
``OperationalError: near "SEQUENCE": syntax error``), and DDL executed from a request path
is not a migration. It lives here now, guarded on the vendor:

* **Postgres** — create the sequence and advance it past any rows that already exist, so a
  migrated database cannot hand out a number that is already taken.
* **SQLite** — no-op. `cases/numbering.py` falls back to `MAX(number) + 1`, which is safe
  on a single-connection dev database and fails loudly (unique violation) rather than
  silently if that ever stops being true.

`sqlmigrate` cannot render `RunPython` operations, which is why the seeded row sets get
their own assertion test (`test_seed_migrations.py`, REVIEW M11).
"""

from django.db import migrations

SEQUENCE_NAME = "case_number_seq"


def create_case_number_sequence(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE_NAME} AS integer START 1")
    Case = apps.get_model("cases", "Case")
    highest = Case.objects.order_by("-number").values_list("number", flat=True).first()
    if highest:
        schema_editor.execute(f"SELECT setval('{SEQUENCE_NAME}', {int(highest)}, true)")


def drop_case_number_sequence(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE_NAME}")


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0004_case_hardening"),
    ]

    operations = [
        migrations.RunPython(create_case_number_sequence, drop_case_number_sequence),
    ]
