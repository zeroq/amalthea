from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.forms import ModelForm
from django.http import HttpRequest

from .models import AuditLog, IngestionToken, Organisation, User


# django-stubs models `UserAdmin` as generic over the user model, but Django's runtime class is not
# subscriptable (`TypeError: type 'UserAdmin' is not subscriptable`), so the parameterisation cannot
# be written in the base-class position. Targeted ignore rather than loosening strict mode.
@admin.register(User)
class UserAdmin(DjangoUserAdmin):  # type: ignore[type-arg]
    list_display = ("username", "email", "login", "role", "org")
    fieldsets = (*(DjangoUserAdmin.fieldsets or ()), (None, {"fields": ("login", "role", "org")}))
    add_fieldsets = (
        *(DjangoUserAdmin.add_fieldsets or ()),
        (None, {"fields": ("login", "role", "org")}),
    )

    def get_form(
        self,
        request: HttpRequest,
        obj: Any = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm[Any]]:
        # `login` is derived from `username` in User.save(); requiring it in the admin
        # form would ask the operator for a value the server fills in anyway.
        # `change` is forwarded by keyword: the stubs resolve super() to a narrower overload, but
        # ModelAdmin.get_form consumes it by name, so behaviour is identical and the call type-checks.
        form = super().get_form(request, obj, change=change, **kwargs)
        form.base_fields["login"].required = False
        return form


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Organisation admin — list and search by name, show timestamps."""

    list_display = ("name", "created_at", "updated_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(IngestionToken)
class IngestionTokenAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Read-mostly view of per-org webhook credentials (ADR-004 Q6).

    The Argon2 `secret_hash` is never displayed (plan §7: excluded everywhere) and the
    `prefix` is read-only: the plaintext is shown once at creation and never recoverable,
    so there is nothing useful to edit here. Revoking a token is a security action, so
    `revoked_at` is the only field an operator is expected to touch.
    """

    list_display = ("name", "organisation", "source", "prefix", "revoked_at", "last_used_at")
    list_filter = ("organisation", "source")
    search_fields = ("name", "prefix")
    # `secret_hash` must be *excluded*, not merely read-only (F9): removing it from
    # `readonly_fields` alone would have made the Argon2 hash an editable widget. The
    # hash is non-null, so the add form (which omits read-only fields) can never be
    # valid anyway — tokens are minted by `IngestionToken.create_token`, not the admin.
    exclude = ("secret_hash",)
    readonly_fields = ("prefix", "last_used_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Append-only audit trail (ADR-004 Q5) — view only, never editable from the admin."""

    list_display = ("created_at", "actor", "action", "target_type", "target_id", "target_org")
    list_filter = ("action", "target_type")
    search_fields = ("target_id",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: AuditLog | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: AuditLog | None = None) -> bool:
        return False
