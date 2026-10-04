"""Seed the standard case statuses.

REVIEW-2026-10-03 **M10**: the reverse used to delete by value
(`.filter(value__in=[...])`). ADR-002 §D4 makes statuses user-extensible precisely so a
SOC can add `Contained` or `Escalated` without a schema migration — so a rollback would
have destroyed an analyst-created status that merely shared a name (`Closed`) with a
seeded one. The reverse is now a declared no-op. Schema rollback must never delete
domain data; if the seeded rows genuinely must go, that is a manual, reviewed DELETE.
"""

from django.db import migrations

SEEDED_CASE_STATUSES = (
    ('New', 'New', 1),
    ('InProgress', 'InProgress', 2),
    ('Contained', 'InProgress', 3),
    ('Closed', 'Closed', 4),
)


def seed_case_status(apps, schema_editor):
    CaseStatus = apps.get_model('cases', 'CaseStatus')
    for value, stage, order in SEEDED_CASE_STATUSES:
        CaseStatus.objects.get_or_create(value=value, defaults={'stage': stage, 'order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(seed_case_status, migrations.RunPython.noop),
    ]
