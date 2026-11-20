from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("observables", "0013_rejectedobservable_reason_not_null"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rejectedobservable",
            name="alert",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=models.SET_NULL,
                related_name="rejected_observables",
                to="alerts.alert",
            ),
        ),
        migrations.AlterField(
            model_name="rejectedobservable",
            name="owner_org",
            field=models.ForeignKey(
                db_index=False,
                on_delete=models.PROTECT,
                related_name="rejected_observables",
                to="identity.organisation",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="rejectedobservable",
            name="uniq_rejected_alert",
        ),
        migrations.AddConstraint(
            model_name="rejectedobservable",
            constraint=models.UniqueConstraint(
                condition=models.Q(("alert__isnull", False)),
                fields=("alert", "owner_org", "data_type", "normalized_data"),
                name="uniq_rejected_alert",
            ),
        ),
        migrations.AddConstraint(
            model_name="rejectedobservable",
            constraint=models.CheckConstraint(
                condition=models.Q(("reason", ""))
                | models.Q(
                    (
                        "reason__in",
                        (
                            "duplicate",
                            "irrelevant",
                            "false_positive",
                            "incomplete",
                            "too_noisy",
                            "other",
                        ),
                    )
                ),
                name="rejected_observable_reason_valid",
            ),
        ),
    ]
