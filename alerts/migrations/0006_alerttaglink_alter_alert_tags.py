"""REVIEW-2026-10-04 **L-2** — prune the redundant index on the `alert` side of the join.

Same defect as `cases/migrations/0007_casetaglink_alter_case_tags.py`, same two-step shape.
`Alert.tags` had no explicit `through`, so Django's synthesised join model gave the left
FK (`alert_id`) a single-column index that the table's own `UNIQUE (alert, tag)` already
covers as a left prefix. Declaring the through model is what makes the pruning expressible;
the autodetector cannot generate it, because `AlterField` on an M2M refuses to add
`through=` and `CreateModel` would `CREATE TABLE` a table that already exists.

Step 1 records the join model in project state with no DDL. Step 2 is the one real change:
`AlterField` to `db_index=False`, which rebuilds `alert_tags` without the redundant index and
leaves the `UNIQUE (alert, tag)` composite and the `tag_id` index — the reverse lookup is
*not* prefix-covered — intact.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0005_restore_custom_field_fk_indexes'),
    ]

    operations = [
        # Step 1 — state only. `CreateModel` would emit CREATE TABLE for an existing table.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='AlertTagLink',
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
                            'alert',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='tag_links',
                                to='alerts.alert',
                            ),
                        ),
                        (
                            'tag',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='alert_links',
                                to='cases.tag',
                            ),
                        ),
                    ],
                    options={
                        'db_table': 'alert_tags',
                        'unique_together': {('alert', 'tag')},
                    },
                ),
                migrations.AlterField(
                    model_name='alert',
                    name='tags',
                    field=models.ManyToManyField(
                        blank=True,
                        related_name='alerts',
                        through='alerts.AlertTagLink',
                        to='cases.tag',
                    ),
                ),
            ],
        ),
        # Step 2 — the real change. A separate operation because
        # `SeparateDatabaseAndState` hands its `database_operations` a `from_state` that
        # does not yet contain the model, so an `AlterField` naming `AlertTagLink` cannot
        # resolve it until a prior operation has written it into project state.
        migrations.AlterField(
            model_name='alerttaglink',
            name='alert',
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='tag_links',
                to='alerts.alert',
            ),
        ),
    ]
