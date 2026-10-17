"""Seed the `Closed` alert status (Plan-2026-10-17 §12.8(5), decision D2).

Before this migration the only `stage="Closed"` status was `Dismissed`, which means "this
alert was reviewed and rejected". Closing a case is a different thing: the alert is resolved
*because its case closed*, and every linked alert — including `Imported` ones — must leave
triage on that transition. Overloading `Dismissed` for both would make the triage queue lie
about why an alert stopped being actionable, so closure gets its own status.

`get_or_create` keeps the migration idempotent and safe to re-run on a database that already
has a `Closed` row for some other reason.
"""

from django.db import migrations


def seed_closed_alert_status(apps, schema_editor):
    AlertStatus = apps.get_model('alerts', 'AlertStatus')
    AlertStatus.objects.get_or_create(
        value='Closed',
        defaults={
            'stage': 'Closed',
            'order': 5,
            'description': 'Resolved because the linked case closed.',
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0015_alert_verdict'),
    ]

    operations = [
        migrations.RunPython(seed_closed_alert_status, migrations.RunPython.noop),
    ]
