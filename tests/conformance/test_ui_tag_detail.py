"""Tag detail renders linked entities in the shared table layout."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Tag

pytestmark = pytest.mark.django_db


def _table_headers(html: str, caption: str) -> list[str]:
    table = html.split(f'<caption class="sr-only">{caption}</caption>', 1)[1].split("</table>", 1)[
        0
    ]
    return re.findall(r'<th scope="col">([^<]+)</th>', table)


def test_tag_detail_renders_linked_cases_alerts_and_observables_as_tables(
    make_case,
    make_alert,
    make_observable,
    make_user,
) -> None:
    tag = Tag.objects.create(name="shared-table-tag")

    case = make_case(title="Tag detail case")
    case.tags.add(tag)

    alert = make_alert(
        title="Tag detail alert",
        type="phishing",
        source="email",
        source_ref="tag-detail-alert",
    )
    alert.tags.add(tag)

    observable = make_observable(data="203.0.113.77", data_type="ip")
    observable.tags.add(tag)

    client = Client()
    client.force_login(make_user(username="tag-detail-analyst", role="analyst"))
    response = client.get(reverse("ui-tag-detail", kwargs={"tag_id": tag.id}))
    html = response.content.decode()

    assert response.status_code == 200
    assert html.count('<table class="data-table">') == 3
    assert _table_headers(html, f"Linked cases for {tag.name}") == [
        "Severity",
        "Case",
        "Started",
        "State",
    ]
    assert _table_headers(html, f"Linked alerts for {tag.name}") == [
        "Severity",
        "Alert",
        "Seen",
        "State",
    ]
    assert "Tag detail case" in html
    assert "Tag detail alert" in html
    assert "203.0.113.77" in html
    assert reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) in html
    assert reverse("ui-observable-detail", kwargs={"observable_id": observable.id}) in html
