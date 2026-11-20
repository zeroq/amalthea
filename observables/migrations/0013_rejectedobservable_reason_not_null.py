from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("observables", "0012_alter_rejectedobservable_normalized_data"),
    ]

    operations = [
        migrations.RunSQL(
            sql="UPDATE rejected_observables SET reason = '' WHERE reason IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="rejectedobservable",
            name="reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("duplicate", "Duplicate"),
                    ("irrelevant", "Irrelevant"),
                    ("false_positive", "False Positive"),
                    ("incomplete", "Incomplete"),
                    ("too_noisy", "Too Noisy"),
                    ("other", "Other"),
                ],
                default="",
                max_length=32,
            ),
            preserve_default=False,
        ),
    ]
