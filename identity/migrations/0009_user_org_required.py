"""ADR-007 / ADR-004 §36: make User.org NOT NULL with PROTECT; serialize UserManager.

ADR-004 §36 promised a backfill + NOT NULL step that was never written. This migration
delivers it: every existing org-less user is assigned to the default organisation
(named by AMALTHEA_DEFAULT_ORG_NAME, default "Default"), then the FK is tightened
to non-null PROTECT. The default org is guaranteed to exist by 0006_create_default_org.

Additionally, because AbstractUser's default manager (UserManager) sets
use_in_migrations = True, the custom UserManager is also serialized here via
AlterModelManagers so that historical migrations use the correct manager class.

Dependencies are identity-only (0008) — this migration never apps.get_model another app.
"""

from django.conf import settings
from django.db import migrations, models

import identity.models


def backfill_user_org(apps, schema_editor):
    """Assign every org-less user to the default organisation."""
    Organisation = apps.get_model("identity", "Organisation")
    User = apps.get_model("identity", "User")

    default_org_name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    org, _ = Organisation.objects.get_or_create(name=default_org_name)

    User.objects.filter(org__isnull=True).update(org=org)
    # The count is asserted in a test (T1-b AC); migrations have no logger so we
    # do not print here.


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0008_role_viewer_rename"),
    ]

    operations = [
        migrations.RunPython(backfill_user_org, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="org",
            field=models.ForeignKey(
                "identity.Organisation",
                on_delete=models.PROTECT,
                related_name="users",
            ),
        ),
        migrations.AlterModelManagers(
            name="user",
            managers=[
                ("objects", identity.models.UserManager()),
            ],
        ),
    ]
