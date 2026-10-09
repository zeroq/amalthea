"""Seed one working example playbook (plan §6.1, T2.5).

"Working" means *runnable*, not *fire-and-forget on every install*: the seed is bound to
`observable.created` and uses the registered no-network action
`amalthea.automation.executor.enrichment_probe`, and it ships **inactive** so a
freshly migrated deployment never runs automation nobody configured. An analyst activates
it in the authoring UI (one click) — or runs it manually, which does not require
activation — and the offline MVP loop is the same loop the seed is built on,
`tests/conformance/test_mvp_loop_automation.py`.

The reverse is not the vocabulary seeds' no-op: this row is an *example*, not a
first-class vocabulary, so rolling it back legitimately removes it. It deletes **only** a
row that is still byte-for-byte the seed (name + trigger + config + description +
inactivity); an analyst who renamed, reconfigured or activated it keeps their work
(REVIEW M10: seeding must never destroy a row it did not create).
"""

from django.db import migrations

#: The seeded playbook. Kept in one place so the test that pins it cannot drift from it.
SEED_PLAYBOOK_NAME = 'enrich-observable'
SEED_TRIGGER_EVENT = 'observable.created'
SEED_DESCRIPTION = (
    'Example playbook: enrich a newly created observable with the registered no-network '
    'enrichment probe. Ships inactive so a fresh install never runs automation nobody '
    'configured; activate it (or run it manually) to see the feedback loop.'
)
SEED_IS_ACTIVE = False
SEED_CONFIG = {
    'action': 'python',
    'action_path': 'amalthea.automation.executor.enrichment_probe',
}


def seed_playbook(apps, schema_editor):
    Playbook = apps.get_model('automation', 'Playbook')
    Playbook.objects.get_or_create(
        name=SEED_PLAYBOOK_NAME,
        defaults={
            'description': SEED_DESCRIPTION,
            'trigger_event': SEED_TRIGGER_EVENT,
            'is_active': SEED_IS_ACTIVE,
            'config': SEED_CONFIG,
        },
    )


def unseed_playbook(apps, schema_editor):
    """Reverse: delete the seeded row only if it is still exactly the seed (M10)."""
    Playbook = apps.get_model('automation', 'Playbook')
    playbook = Playbook.objects.filter(name=SEED_PLAYBOOK_NAME).first()
    if playbook is None:
        return
    unchanged = (
        playbook.trigger_event == SEED_TRIGGER_EVENT
        and playbook.is_active == SEED_IS_ACTIVE
        and playbook.description == SEED_DESCRIPTION
        and playbook.config == SEED_CONFIG
    )
    if unchanged:
        playbook.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('automation', '0004_automation_run_triggered_by'),
    ]

    operations = [
        migrations.RunPython(seed_playbook, unseed_playbook),
    ]