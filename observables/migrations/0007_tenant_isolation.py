# ADR-004: an Observable becomes org-scoped. Adds the mandatory PROTECT owner_org FK, replaces the
# global unique (data_type, data_hash) with a per-tenant (owner_org, data_type, data_hash), and
# re-homes the data_type index (no longer a left prefix of the unique constraint). Kept in the
# observables app and depending only on identity's schema — never on cases — so the observables seed
# rollback stays inside observables (H3-2).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Organisation = apps.get_model("identity", "Organisation")
    Observable = apps.get_model("observables", "Observable")

    name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    org = Organisation.objects.filter(name=name).first() or Organisation.objects.first()
    if org is not None:
        Observable.objects.filter(owner_org__isnull=True).update(owner_org=org)


class Migration(migrations.Migration):

    dependencies = [
        ("observables", "0006_add_gin_indexes_json_fields"),
        ("identity", "0006_create_default_org"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="observable",
            name="uniq_obs_dtype_hash",
        ),
        migrations.AddField(
            model_name="observable",
            name="owner_org",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="observables",
                to="identity.organisation",
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="observable",
            name="owner_org",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="observables",
                to="identity.organisation",
            ),
        ),
        migrations.AlterField(
            model_name="observable",
            name="data_type",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="observables",
                to="observables.observabletype",
            ),
        ),
        migrations.AddIndex(
            model_name="observable",
            index=models.Index(fields=["owner_org", "-created_at"], name="obs_org_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="observable",
            constraint=models.UniqueConstraint(
                fields=("owner_org", "data_type", "data_hash"), name="uniq_obs_org_dtype_hash"
            ),
        ),
    ]
