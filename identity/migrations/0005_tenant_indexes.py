# ADR-004: the implicit FK indexes on AuditLog.actor/target_org and IngestionToken.source are
# redundant because each is the leading column of a composite index already declared on the model.
# Dropping them satisfies the index-audit guard (no index may be a left prefix of another).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0004_tenant_isolation_foundations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ingestiontoken",
            name="source",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tokens",
                to="ingest.ingestionsource",
            ),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="actor",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_logs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="target_org",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="identity.organisation",
            ),
        ),
    ]
