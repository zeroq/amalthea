from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite

from identity.admin import IngestionTokenAdmin
from identity.models import IngestionToken


@pytest.mark.django_db
def test_ingestion_token_admin_shows_paused_state():
    admin = IngestionTokenAdmin(IngestionToken, AdminSite())
    assert "deactivated_at" in admin.list_display
    assert "deactivated_at" in admin.list_filter
    assert "deactivated_at" in admin.readonly_fields
