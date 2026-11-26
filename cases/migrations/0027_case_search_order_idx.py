from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0026_case_upper_trgm"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="case",
            index=models.Index(
                fields=["owner_org", "-severity", "-start_date", "-id"],
                name="case_search_order_idx",
            ),
        ),
    ]
