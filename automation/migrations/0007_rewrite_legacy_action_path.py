"""Rewrite legacy `action_path` from dotted module path to bare script name (ADR-011).

The legacy seed playbook and any manually created playbooks stored the full dotted path
`amalthea.automation.executor.enrichment_probe` in `config["action_path"]`. This migration
rewrites those to the bare script name `enrichment_probe`.

The reverse operation maps `enrichment_probe` back to the legacy dotted path.
"""

from django.db import migrations

LEGACY_PATH = "amalthea.automation.executor.enrichment_probe"
NEW_NAME = "enrichment_probe"


def rewrite_action_path(apps, schema_editor):
    Playbook = apps.get_model("automation", "Playbook")
    # Only touch rows where config.action_path exactly matches the legacy string
    playbooks = Playbook.objects.filter(config__action_path=LEGACY_PATH)
    for pb in playbooks:
        config = dict(pb.config)
        config["action_path"] = NEW_NAME
        pb.config = config
        pb.save(update_fields=["config"])


def reverse_rewrite_action_path(apps, schema_editor):
    Playbook = apps.get_model("automation", "Playbook")
    # Reverse: map the bare name back to the legacy dotted path
    playbooks = Playbook.objects.filter(config__action_path=NEW_NAME)
    for pb in playbooks:
        config = dict(pb.config)
        config["action_path"] = LEGACY_PATH
        pb.config = config
        pb.save(update_fields=["config"])


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0006_seed_example_playbook"),
    ]

    operations = [
        migrations.RunPython(rewrite_action_path, reverse_rewrite_action_path),
    ]