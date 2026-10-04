"""Seed the standard alert statuses.

REVIEW-2026-10-03 **M10**: the reverse used to delete by value; see
`cases/migrations/0003_seed.py` for why that is now a no-op.

REVIEW-2026-10-03 **M12**: the `Imported` status is seeded with `stage="Imported"`, which
ADR-002 §D4 lists as a legal alert stage. `compat.enums.stage_from_alert_status` used to
be structurally unable to emit it (it was swallowed by the `Closed` tuple and then
shadowed by an unreachable branch), so the schema and the compat mapper disagreed.
"""

from django.db import migrations

SEEDED_ALERT_STATUSES = (
    ('New', 'New', 1),
    ('Triaged', 'InProgress', 2),
    ('Dismissed', 'Closed', 3),
    ('Imported', 'Imported', 4),
)


def seed_alert_status(apps, schema_editor):
    AlertStatus = apps.get_model('alerts', 'AlertStatus')
    for value, stage, order in SEEDED_ALERT_STATUSES:
        AlertStatus.objects.get_or_create(
            value=value,
            defaults={'stage': stage, 'order': order, 'description': ''},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(seed_alert_status, migrations.RunPython.noop),
    ]
