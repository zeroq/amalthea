from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "login", "role", "org")
    fieldsets = (*DjangoUserAdmin.fieldsets, (None, {"fields": ("login", "role", "org")}))
    add_fieldsets = (*DjangoUserAdmin.add_fieldsets, (None, {"fields": ("login", "role", "org")}))

    def get_form(self, request, obj=None, **kwargs):
        # `login` is derived from `username` in User.save(); requiring it in the admin
        # form would ask the operator for a value the server fills in anyway.
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["login"].required = False
        return form
