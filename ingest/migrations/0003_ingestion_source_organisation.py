"""ADR-009: `IngestionSource` becomes org-owned.

Reverses ADR-008 §4 ("`IngestionSource` is fleet-global configuration"). The source now carries the
mapping rules, and rules are a tenant's opinion, so the object holding them belongs to a tenant:
`organisation` FK (PROTECT — an ownable feed blocks its org's deletion, reported by
`organisation_blockers`) and `slug` unique *per organisation* rather than globally.

Three things are load-bearing here:

* **`null=True` → backfill → `null=False`.** Not for style: the column has to exist before the data
  can be written, and the NOT NULL has to be asserted by the database rather than trusted from the
  data migration.
* **The backfill derives the org from the source's tokens, and refuses to guess.** A source with two
  token orgs is not assignable — one of the two would silently start posting into the other's tenant.
  Raising leaves a broken deploy to a human, which is the correct failure mode for a tenancy question
  (the operator splits the feed by hand). Zero tokens falls back to the default organisation, which
  `identity.0006` guarantees exists (and created legacy tokens for, which is why this migration
  depends on it rather than on any later identity migration).
* **`UniqueConstraint(organisation, slug)` replaces a global `unique=True` on `slug`.** Two tenants may
  each configure a `crowdstrike-edr` with their own rules; neither may configure two in one org.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_organisation(apps, schema_editor):
    """Assign every source the organisation of its tokens, or fail loudly."""
    IngestionSource = apps.get_model("ingest", "IngestionSource")
    IngestionToken = apps.get_model("identity", "IngestionToken")
    Organisation = apps.get_model("identity", "Organisation")

    default_org_name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")

    for source in IngestionSource.objects.filter(organisation__isnull=True):
        org_ids = list(
            IngestionToken.objects.filter(source=source)
            .values_list("organisation_id", flat=True)
            .distinct()
        )
        if len(org_ids) > 1:
            names = sorted(
                Organisation.objects.filter(pk__in=org_ids).values_list("name", flat=True)
            )
            raise RuntimeError(
                f"IngestionSource {source.slug!r} ({source.pk}) has tokens in "
                f"{len(org_ids)} organisations ({', '.join(names)}), so ADR-009 cannot decide which "
                "one owns it. Split the feed (one source per organisation, keeping one org's tokens "
                "on each) and re-run the migration."
            )

        if org_ids:
            source.organisation_id = org_ids[0]
        else:
            # No tokens, nothing to infer from: the default org is the documented fallback. It is
            # created by identity.0006 (a dependency below); get_or_create keeps this correct even
            # against a database where that migration was faked.
            source.organisation_id = Organisation.objects.get_or_create(name=default_org_name)[0].pk
        source.save(update_fields=["organisation"])

    orphans = IngestionSource.objects.filter(organisation__isnull=True).count()
    if orphans:
        # Unreachable by construction — the NOT NULL arrives in the next operation either way, so
        # this only makes the cause legible instead of a bare IntegrityError.
        raise RuntimeError(f"{orphans} ingestion source(s) still have no organisation")


def noop_reverse(apps, schema_editor):
    """Reverse is the schema reverse below; nothing to undo in the data."""


class Migration(migrations.Migration):
    dependencies = [
        ("ingest", "0002_ingestion_source_hardening"),
        # Not identity's head: what this migration reads is the default organisation and the legacy
        # tokens `identity.0006` creates, so that is the migration it follows (per ADR-009 and the
        # §6.1 plan). Later identity migrations neither create nor move a token's org.
        ("identity", "0006_create_default_org"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestionsource",
            name="organisation",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ingestion_sources",
                to="identity.organisation",
            ),
        ),
        migrations.RunPython(backfill_organisation, noop_reverse),
        migrations.AlterField(
            model_name="ingestionsource",
            name="organisation",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ingestion_sources",
                to="identity.organisation",
            ),
        ),
        migrations.AddConstraint(
            model_name="ingestionsource",
            constraint=models.UniqueConstraint(
                fields=("organisation", "slug"), name="ingestion_source_org_slug_uniq"
            ),
        ),
        migrations.AlterField(
            model_name="ingestionsource",
            name="slug",
            field=models.CharField(max_length=100),
        ),
    ]
