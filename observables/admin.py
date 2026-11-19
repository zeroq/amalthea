from django.contrib import admin

from .models import Observable, ObservableSuggestion, ObservableType, RejectedObservable


@admin.register(ObservableType)
class ObservableTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_attachment", "is_case_sensitive")
    search_fields = ("name",)


@admin.register(Observable)
class ObservableAdmin(admin.ModelAdmin):
    list_display = ("data_type", "owner_org", "pap", "tlp")
    list_filter = ("owner_org", "data_type")
    search_fields = ("data", "normalized_data")


@admin.register(ObservableSuggestion)
class ObservableSuggestionAdmin(admin.ModelAdmin):
    list_display = ("status", "owner_org", "data_type", "created_at")
    list_filter = ("status", "owner_org", "data_type")
    search_fields = ("normalized_data", "raw_data")


@admin.register(RejectedObservable)
class RejectedObservableAdmin(admin.ModelAdmin):
    list_display = ("owner_org", "data_type", "alert", "reason", "is_promoted", "created_at")
    list_filter = ("owner_org", "data_type", "reason", "is_promoted")
    search_fields = ("normalized_data", "reason")
