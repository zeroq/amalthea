"""ADR-006 decision 1 — the read-only role is renamed `read-only` -> `viewer`.

The role value is user-visible (API `user.role`, Django admin, the UI), and `read-only` named the
*permission* rather than the person who holds it. `viewer` is the noun the rest of the docs use.

Deliberately untouched: the TheHive *share* vocabulary. A share still reports
`profileName: "write" | "read-only"` (`core/serializers.py`) because that is a share ACL, not a
role — renaming it would break the TheHive-compatible surface. See ADR-006 §Decision 1.

Order matters: `identity_user_role_valid` is a CHECK over the allowed values, so rows may only be
rewritten *after* it is dropped and *before* it is re-added. Written by hand rather than as an
`AlterField`-only migration for that reason.
"""

from django.db import migrations, models


def rename_to_viewer(apps, schema_editor) -> None:
    apps.get_model("identity", "User").objects.filter(role="read-only").update(role="viewer")


def rename_to_read_only(apps, schema_editor) -> None:
    apps.get_model("identity", "User").objects.filter(role="viewer").update(role="read-only")


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0007_add_timestamps_to_organisation"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="identity_user_role_valid",
        ),
        migrations.RunPython(rename_to_viewer, rename_to_read_only),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("superAdmin", "Super Administrator"),
                    ("admin", "Organisation Administrator"),
                    ("analyst", "Analyst"),
                    ("viewer", "Viewer"),
                ],
                default="analyst",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(role__in=("superAdmin", "admin", "analyst", "viewer")),
                name="identity_user_role_valid",
            ),
        ),
    ]
