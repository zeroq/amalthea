# TaskNote model: a per-task append-only log (plan §5.1, Q2 = B2).
# Each note is also mirrored into the case ledger as kind="task-note" (Module B live ledger).

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0015_tenant_owner_org"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskNote",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("message", models.TextField()),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="task_notes",
                        to="identity.user",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notes",
                        to="cases.task",
                    ),
                ),
            ],
            options={
                "db_table": "task_note",
            },
        ),
        migrations.AddIndex(
            model_name="tasknote",
            index=models.Index(
                fields=["task", "-created_at", "-id"], name="task_note_task_created_idx"
            ),
        ),
    ]
