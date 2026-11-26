from django.contrib.postgres.indexes import OpClass
from django.db import migrations
from django.db.models import TextField
from django.db.models.functions import Cast, Upper

from core.indexes import PostgresExpressionGinIndex
from core.pg_indexes import ensure_trgm_extension_operation, gin_expression_index_operation


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0025_add_status_label"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                ensure_trgm_extension_operation(),
                gin_expression_index_operation(
                    "case_record",
                    "case_title_upper_trgm_idx",
                    'UPPER("title"::text)',
                    "gin_trgm_ops",
                ),
                gin_expression_index_operation(
                    "case_record",
                    "case_desc_upper_trgm_idx",
                    'UPPER("description"::text)',
                    "gin_trgm_ops",
                ),
                gin_expression_index_operation(
                    "case_record",
                    "case_summary_upper_trgm_idx",
                    'UPPER("summary"::text)',
                    "gin_trgm_ops",
                ),
            ],
            state_operations=[
                    migrations.AddIndex(
                        model_name="case",
                        index=PostgresExpressionGinIndex(
                        OpClass(Upper(Cast("title", TextField())), name="gin_trgm_ops"),
                        name="case_title_upper_trgm_idx",
                    ),
                ),
                    migrations.AddIndex(
                        model_name="case",
                        index=PostgresExpressionGinIndex(
                        OpClass(Upper(Cast("description", TextField())), name="gin_trgm_ops"),
                        name="case_desc_upper_trgm_idx",
                    ),
                ),
                    migrations.AddIndex(
                        model_name="case",
                        index=PostgresExpressionGinIndex(
                        OpClass(Upper(Cast("summary", TextField())), name="gin_trgm_ops"),
                        name="case_summary_upper_trgm_idx",
                    ),
                ),
            ],
        ),
    ]
