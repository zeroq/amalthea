"""Seed the standard observable types.

REVIEW-2026-10-03 **H4**: `hash` was seeded `is_case_sensitive=True`, which inverts AC5.4
("case hashes normalize to lowercase; a case-sensitive type does not") and leaves no
correctly-seeded case-sensitive type to test against. Hashes are hex strings compared
case-insensitively everywhere; only a file path is genuinely case-sensitive, so
`is_case_sensitive` is now true for `file` alone.

REVIEW-2026-10-03 **M10**: the reverse used to delete by value
(`.filter(name__in=[...])`). ADR-002 §D4 makes observable types user-extensible, so a
rollback could destroy a type an analyst created that happens to share a name with a
seeded one. The reverse is now a declared no-op: schema rollback must never delete
domain data.
"""

from django.db import migrations

SEEDED_TYPES = ('ip', 'domain', 'fqdn', 'url', 'mail', 'file', 'hash', 'user', 'other')
CASE_SENSITIVE_TYPES = ('file',)


def seed_observable_types(apps, schema_editor):
    ObservableType = apps.get_model('observables', 'ObservableType')
    for name in SEEDED_TYPES:
        ObservableType.objects.get_or_create(
            name=name,
            defaults={
                'is_attachment': False,
                'is_case_sensitive': name in CASE_SENSITIVE_TYPES,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('observables', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_observable_types, migrations.RunPython.noop),
    ]
