from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.forms import ModelForm
from django.http import HttpRequest

from .models import User


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
