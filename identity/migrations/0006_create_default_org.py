# ADR-004 (greenfield): guarantee a default Organisation exists so every owner_org backfill in
# cases/alerts/observables has a tenant to point at, and adopt any pre-existing legacy webhook
# secret as an org-scoped IngestionToken (Q6/T2-d). Depends only on identity's own schema, never
# on another app's migrations — that is what keeps a seed rollback inside its own app (H3-2).

from django.conf import settings
from django.db import migrations


def create_default_org(apps, schema_editor):
    Organisation = apps.get_model("identity", "Organisation")
    IngestionSource = apps.get_model("ingest", "IngestionSource")
    IngestionToken = apps.get_model("identity", "IngestionToken")

    name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
    org, _ = Organisation.objects.get_or_create(name=name)

    legacy = IngestionSource.objects.exclude(webhook_secret_hash__isnull=True).exclude(
        webhook_secret_hash=""
    )
    for source in legacy:
        IngestionToken.objects.get_or_create(
            source=source,
            organisation=org,
            defaults={
                "name": f"Migrated token for {source.slug}",
                "prefix": source.webhook_secret_hash[:12],
                "secret_hash": source.webhook_secret_hash,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0005_tenant_indexes"),
    ]

    operations = [
        migrations.RunPython(create_default_org, migrations.RunPython.noop),
    ]
