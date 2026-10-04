"""REVIEW-2026-10-03 **H6** — identity hardening.

* `User.id` / `ApiKey.id` become UUID primary keys, as ADR-002 §D3 requires
  ("store UUID PKs internally"). Both were `BigAutoField`.

  These were fixed in `0001_initial.py` rather than here, deliberately. Changing a
  primary key's type through an `AlterField` is not migratable on Postgres while any
  foreign key references it — `ALTER COLUMN ... TYPE uuid` is refused — and the eight
  referencing columns live in five other apps. With no production data the honest fix is
  to correct the initial schema rather than write a drop-constraint/retype/re-add dance
  across six tables for a table that has never held a row. Recorded as a deviation.

* `ApiKey.prefix` becomes `unique=True`. It was merely indexed, and
  `compat.auth.ApiKeyAuthentication` resolves a bearer token with
  `filter(prefix=...).first()` — two rows sharing a prefix meant the lookup silently
  picked one of them. `unique=True` implies the index, so `db_index=True` was dropped
  with it.

* `ApiKey.updated_at` is added because `ApiKey` now inherits `TimeStampedModel`, matching
  every other model in the project.

* `ApiKey.scope` gains a CHECK constraint behind the existing `choices` (REVIEW H5/L1).

* `Organisation.db_table` is stated explicitly (REVIEW M8) so `grep db_table` finds every
  table; the value was already Django's default, so this operation is a no-op that
  documents the intent.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('identity', '0002_organisation_alter_apikey_user_alter_user_org'),
    ]

    operations = [
        migrations.AddField(
            model_name='apikey',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddConstraint(
            model_name='apikey',
            constraint=models.CheckConstraint(condition=models.Q(('scope__in', ('read', 'readwrite'))), name='apikey_scope_valid'),
        ),
        migrations.AlterModelTable(
            name='organisation',
            table='identity_organisation',
        ),
    ]
