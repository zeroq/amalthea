"""Drop the redundant observable owner-org FK index.

`owner_org` is already the left prefix of `obs_org_created_idx`, and the Postgres
index audit (`test_no_index_is_left_prefix_covered_by_a_wider_one`) correctly flags
the standalone FK index as write amplification. Model state already declares
`db_index=False`; this migration removes the historical database index left behind
by earlier migrations.
"""

from django.db import migrations

INDEX_NAME = "observable_owner_org_id_0f867805"


class Migration(migrations.Migration):
    dependencies = [
        ("observables", "0015_observable_data_upper_trgm"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"DROP INDEX IF EXISTS {INDEX_NAME}",
            reverse_sql=(
                f"CREATE INDEX {INDEX_NAME} ON observable (owner_org_id)"
            ),
        ),
    ]
