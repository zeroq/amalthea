# Seed containment action terms into the TaxonomyTerm vocabulary.

from django.db import migrations


def seed_containment_actions(apps, schema_editor):
    TaxonomyTerm = apps.get_model('core', 'TaxonomyTerm')

    terms = [
        ('containment_actions', 'password_reset', 'Password reset', 0, False, False),
        ('containment_actions', 'process_killed', 'Process Killed', 1, False, False),
        (
            'containment_actions',
            'asset_network_contained',
            'Asset network contained',
            2,
            False,
            False,
        ),
        ('containment_actions', 'user_disabled', 'User disabled', 3, False, False),
    ]

    for category, code, label, order, protected, is_closing in terms:
        TaxonomyTerm.objects.get_or_create(
            category=category,
            code=code,
            defaults={
                'label': label,
                'order': order,
                'protected': protected,
                'is_closing': is_closing,
                'hidden': False,
            },
        )


def reverse_seed_containment_actions(apps, schema_editor):
    TaxonomyTerm = apps.get_model('core', 'TaxonomyTerm')
    TaxonomyTerm.objects.filter(category='containment_actions').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_alter_taxonomyterm_category'),
    ]

    operations = [
        migrations.RunPython(seed_containment_actions, reverse_seed_containment_actions),
    ]
