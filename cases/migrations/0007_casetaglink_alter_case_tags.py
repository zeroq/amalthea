"""REVIEW-2026-10-04 **L-2** — prune the redundant index on the `case` side of the join.

`Case.tags` had no explicit `through`, so Django synthesised the join model. That model
gives the left FK (`case_id`) its own single-column index, which the table's own
`UNIQUE (case_id, tag_id)` already covers as a left prefix — the same redundancy REVIEW M1
removed from seven other tables. It survived here only because an implicit model has no
declaration to edit.

Declaring `through=CaseTagLink` is what makes the pruning expressible, but Django's
autodetector cannot generate the migration for it: `AlterField` on an M2M raises
"you cannot add or remove through= on M2M fields", and `CreateModel` would emit
`CREATE TABLE case_record_tags` for a table that already exists in every deployed
database. So this is hand-written with `SeparateDatabaseAndState`:

* **state** — `CreateModel` for the now-declared join model, plus the M2M pointing at it.
  This is byte-for-byte what the autodetector wanted to record; only the *database* half
  differs.
* **database** — a single `AlterField` on `CaseTagLink.case` to `db_index=False`, which
  rebuilds `case_record_tags` without the redundant index and keeps `UNIQUE (case, tag)`
  and the `tag_id` index (the reverse lookup is *not* prefix-covered) intact.

`SeparateDatabaseAndState` applies the state operations before the database ones, so
`CaseTagLink` is resolvable when the `AlterField` runs. Reversing restores `db_index=True`,
which recreates the index — so this migration is as reversible as a generated one.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0006_restore_custom_field_fk_indexes'),
    ]

    operations = [
        # Step 1 — record the now-declared join model in project state only. The table and
        # its indexes already exist in every deployed database, so this must emit no DDL:
        # `CreateModel` would issue CREATE TABLE. `case` is recorded with `db_index=True`
        # because that is what the database actually has *at this point* — see step 2.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='CaseTagLink',
                    fields=[
                        (
                            'id',
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name='ID',
                            ),
                        ),
                        (
                            'case',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='tag_links',
                                to='cases.case',
                            ),
                        ),
                        (
                            'tag',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='case_links',
                                to='cases.tag',
                            ),
                        ),
                    ],
                    options={
                        'db_table': 'case_record_tags',
                        'unique_together': {('case', 'tag')},
                    },
                ),
                migrations.AlterField(
                    model_name='case',
                    name='tags',
                    field=models.ManyToManyField(
                        blank=True,
                        related_name='cases',
                        through='cases.CaseTagLink',
                        to='cases.tag',
                    ),
                ),
            ],
        ),
        # Step 2 — the real database change: drop the redundant `case_id` index. Kept as a
        # separate operation because `SeparateDatabaseAndState` does not apply
        # `state_operations` to the `from_state` its `database_operations` are handed, so an
        # `AlterField` naming `casetaglink` cannot resolve the model unless a *prior*
        # operation has already put it into project state.
        migrations.AlterField(
            model_name='casetaglink',
            name='case',
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='tag_links',
                to='cases.case',
            ),
        ),
    ]
