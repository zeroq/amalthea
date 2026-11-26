from django.contrib.postgres.indexes import OpClass
from django.db import migrations
from django.db.models import TextField
from django.db.models.functions import Cast, Upper

from core.indexes import PostgresExpressionGinIndex
from core.pg_indexes import ensure_trgm_extension_operation, gin_expression_index_operation


class Migration(migrations.Migration):

    dependencies = [
        ('observables', '0014_rejected_observable_conformance'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                ensure_trgm_extension_operation(),
                gin_expression_index_operation(
                    'observable',
                    'obs_data_upper_trgm_idx',
                    'UPPER("data"::text)',
                    'gin_trgm_ops',
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name='observable',
                    index=PostgresExpressionGinIndex(
                        OpClass(
                            Upper(Cast('data', TextField())), name='gin_trgm_ops'
                        ),
                        name='obs_data_upper_trgm_idx',
                    ),
                ),
            ],
        ),
    ]
