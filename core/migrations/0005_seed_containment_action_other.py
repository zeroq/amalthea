# Add the free-text "Other" containment action.

from django.db import migrations


def seed_other_containment_action(apps, schema_editor):
    TaxonomyTerm = apps.get_model("core", "TaxonomyTerm")
    TaxonomyTerm.objects.get_or_create(
        category="containment_actions",
        code="other",
        defaults={
            "label": "Other",
            "order": 4,
            "protected": False,
            "is_closing": False,
            "hidden": False,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_seed_containment_actions"),
    ]

    operations = [
        migrations.RunPython(
            seed_other_containment_action,
            migrations.RunPython.noop,
        ),
    ]
