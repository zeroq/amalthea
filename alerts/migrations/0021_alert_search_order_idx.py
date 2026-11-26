from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0020_alert_upper_trgm"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="alert",
            index=models.Index(
                fields=["owner_org", "-severity", "-date", "-id"],
                name="alert_search_order_idx",
            ),
        ),
    ]
