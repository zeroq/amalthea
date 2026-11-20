from django.contrib import admin

from .models import RejectedObservable


@admin.register(RejectedObservable)
class RejectedObservableAdmin(admin.ModelAdmin):  # type: ignore
    list_display = ("owner_org", "data_type", "alert", "reason", "is_promoted", "created_at")
    list_filter = ("owner_org", "data_type", "reason", "is_promoted")
    search_fields = ("normalized_data", "reason")
