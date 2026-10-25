# Generated for Phase 2 taxonomy compilation

from django.db import migrations


def populate_terms(apps, schema_editor):
    TaxonomyTerm = apps.get_model('core', 'TaxonomyTerm')

    terms = [
        # case_scope
        ('case_scope', 'unset', 'Unset/Unknown', 0, True, False),
        ('case_scope', 'single_asset_user', 'Single Asset/User', 1, False, False),
        ('case_scope', 'multiple_assets_users', 'Multiple Assets/Users', 2, False, False),
        ('case_scope', 'single_department', 'Single Department', 3, False, False),
        ('case_scope', 'multiple_departments', 'Multiple Departments', 4, False, False),
        ('case_scope', 'complete_enterprise', 'Complete Enterprise', 5, False, False),
        ('case_scope', 'third_party_supplier', 'Third Party/Supplier', 6, False, False),
        # case_phase
        ('case_phase', 'unset', 'Unset/Unknown', 0, True, False),
        ('case_phase', 'initial_triage', 'Initial Triage', 1, False, False),
        ('case_phase', 'containment', 'Containment', 2, False, False),
        ('case_phase', 'analysis', 'Analysis', 3, False, False),
        ('case_phase', 'remediation', 'Remediation', 4, False, False),
        ('case_phase', 'cleanup', 'Cleanup', 5, False, False),
        ('case_phase', 'close', 'Close', 6, False, False),
        # case_type
        ('case_type', 'unset', 'Unset/Unknown', 0, True, False),
        ('case_type', 'malware', 'Malware', 1, False, False),
        ('case_type', 'phishing', 'Phishing', 2, False, False),
        ('case_type', 'password_leak', 'Password Leak', 3, False, False),
        ('case_type', 'ransomware', 'Ransomware', 4, False, False),
        ('case_type', 'bec', 'BEC', 5, False, False),
        # data_classification
        ('data_classification', 'unset', 'Unset/Unknown', 0, True, False),
        ('data_classification', 'no_data', 'No Data', 1, False, False),
        ('data_classification', 'public_data', 'Public Data', 2, False, False),
        ('data_classification', 'personal_data', 'Personal Data (PII)', 3, False, False),
        ('data_classification', 'sensitive_data', 'Sensitive Data (Credentials, IP)', 4, False, False),
        # case_verdict
        ('case_verdict', 'undefined', 'Undefined', 0, True, False),
        ('case_verdict', 'true_positive', 'True Positive', 1, True, True),
        ('case_verdict', 'false_positive', 'False Positive', 2, True, True),
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


def reverse(apps, schema_editor):
    TaxonomyTerm = apps.get_model('core', 'TaxonomyTerm')
    TaxonomyTerm.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(populate_terms, reverse),
    ]
