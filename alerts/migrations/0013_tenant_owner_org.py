# ADR-004: an Alert must belong to an Organisation. owner_org moves from nullable SET_NULL to a
# mandatory PROTECT FK. Kept in the alerts app so a seed rollback stays inside its own app (H3-2).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Organisation = apps.get_model("identity", "Organisation")
    Alert = apps.get_model("alerts", "Alert")

    name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    org = Organisation.objects.filter(name=name).first() or Organisation.objects.first()
    if org is not None:
        Alert.objects.filter(owner_org__isnull=True).update(owner_org=org)


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0012_add_per_link_tags_to_observable_links"),
        ("identity", "0006_create_default_org"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="alert",
            name="owner_org",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="alerts",
                to="identity.organisation",
            ),
        ),
    ]
