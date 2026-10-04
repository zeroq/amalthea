from django.urls import re_path

from . import views

urlpatterns = [
    re_path(r"^.*$", views.not_found, name="compat-catchall"),
]
