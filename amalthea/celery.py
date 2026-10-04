"""
Celery configuration for Amalthea.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amalthea.settings.dev")

app = Celery("amalthea")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
