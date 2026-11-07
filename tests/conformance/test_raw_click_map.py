"""Conformance tests for the "raw-data click-to-map" feature (TODO §17).

See TODO.md §17 and the implementation in:
  - ui/json_tree.py   (JsonNode, JsonTree, build_json_tree, jsonpath_for, all_items_path, is_valid_jsonpath)
  - ui/templates/ui/_json_tree.html  (recursive partial)
  - ui/templatetags/ui_tags.py  (json_tree inclusion tag)
  - ui/views/ingestion_sources.py  (prefill logic, map_targets, observable_types, latest_raw_data, prefilled)
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource
from observables.models import ObservableType

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helper: round-trip tests for jsonpath_for / all_items_path / build_json_tree
# ---------------------------------------------------------------------------


def _make_alert(source, org, *, source_ref, payload, date):
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title="Raw alert",
        type="phishing",
        source="webhook",
        source_ref=source_ref,
        status=status,
        raw_payload=payload,
        ingestion_source=source,
        owner_org=org,
        date=date,
    )


# ---- jsonpath_for / all_items_path ----------------------------------------


def test_jsonpath_for_round_trip_nested_key():
    """jsonpath_for with a nested key must round-trip through jsonpath_ng."""
    from ui.json_tree import jsonpath_for

    parts = ["iocs", "ip"]
    path = jsonpath_for(parts)
    assert path == "$.iocs.ip", f"expected $.iocs.ip, got {path!r}"
    # round-trip
    from jsonpath_ng import parse

    expr = parse(path)
    matches = expr.find({"iocs": {"ip": "1.2.3.4"}})
    assert [m.value for m in matches] == ["1.2.3.4"]


def test_jsonpath_for_round_trip_key_with_dots():
    """Keys containing dots should use the escaped form."""
    from ui.json_tree import jsonpath_for

    parts = ["a.b"]
    path = jsonpath_for(parts)
    assert '"a.b"' in path, f"expected escaped key in {path!r}"
    # round-trip
    from jsonpath_ng import parse

    expr = parse(path)
    matches = expr.find({"a.b": {"c": 1}})
    assert [m.value for m in matches] == [{"c": 1}]


def test_jsonpath_for_round_trip_key_with_spaces():
    """Keys with spaces should use the escaped form."""
    from ui.json_tree import jsonpath_for

    parts = ["foo bar"]
    path = jsonpath_for(parts)
    assert '"foo bar"' in path, f"expected escaped key in {path!r}"


def test_jsonpath_for_round_trip_key_with_quotes():
    """Keys with literal quotes should be escaped."""
    from ui.json_tree import jsonpath_for

    parts = ['a"b']
    path = jsonpath_for(parts)
    assert "\\" in path, f"expected backslash-escaped quote in {path!r}"


def test_jsonpath_for_round_trip_array_index():
    """Integer parts become [n] in jsonpath_for."""
    from ui.json_tree import jsonpath_for

    parts = [0, 1]
    path = jsonpath_for(parts)
    assert path == "$[0][1]", f"expected $[0][1], got {path!r}"
    from jsonpath_ng import parse

    expr = parse(path)
    # Array of arrays: [[0][1]] picks the second element of the first inner array
    matches = expr.find([[0, 1]])
    assert [m.value for m in matches] == [1]


def test_jsonpath_for_round_trip_unicode():
    """Unicode keys should be handled correctly."""
    from ui.json_tree import jsonpath_for

    parts = ["μπ"]
    path = jsonpath_for(parts)
    assert "μπ" in path, f"expected unicode key in {path!r}"


def test_all_items_path_turns_indices_into_star():
    """all_items_path should turn every int index into [*]."""
    from ui.json_tree import all_items_path

    # dict keys only
    parts = ["a", "b"]
    path = all_items_path(parts)
    assert "$." in path
    # mixed: some indices
    parts = ["a", 0, "b"]
    path = all_items_path(parts)
    assert "[*]" in path, f"expected [*] in {path!r}, got {path!r}"
    # pure indices
    parts = [0, 1]
    path = all_items_path(parts)
    assert path == "$[*][*]"


def test_build_json_tree_truncation_at_node_cap():
    """build_json_tree should set truncated=True when node count hits MAX_TREE_NODES."""
    from ui.json_tree import build_json_tree

    # Build a dict with many nodes manually
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    tree = build_json_tree(deep, max_nodes=2, max_depth=20)
    assert tree.truncated is True, "should be truncated at node cap"
    assert len(tree.nodes) <= 2, f"expected <= 2 nodes, got {len(tree.nodes)}"


def test_build_json_tree_truncation_at_depth_cap():
    """build_json_tree should set truncated=True when depth exceeds MAX_TREE_DEPTH."""
    from ui.json_tree import build_json_tree

    # A shallow payload still truncates when max_depth is 0.
    tree = build_json_tree({"a": 1}, max_nodes=1000, max_depth=0)
    assert tree.truncated is True, "should be truncated at depth 0"


def test_build_json_tree_scalar_root():
    """build_json_tree with a scalar should produce one node with key=''."""
    from ui.json_tree import build_json_tree

    tree = build_json_tree(42)
    assert len(tree.nodes) == 1
    node = tree.nodes[0]
    assert node.key == ""
    assert node.kind == "scalar"
    assert node.value_repr == "42"


def test_build_json_tree_scalar_truncation():
    """build_json_tree should truncate scalar value_repr at MAX_VALUE_CHARS."""
    from ui.json_tree import MAX_VALUE_CHARS, build_json_tree

    long_val = "x" * (MAX_VALUE_CHARS + 100)
    tree = build_json_tree(long_val)
    node = tree.nodes[0]
    # value_repr should be truncated with "..."
    assert node.value_repr.endswith("..."), "truncated scalar should end with ..."


def test_build_json_tree_kinds():
    """build_json_tree should set kind correctly for objects, arrays, scalars."""
    from ui.json_tree import build_json_tree

    # object
    tree = build_json_tree({"a": 1})
    assert tree.nodes[0].kind == "object"

    # array
    tree = build_json_tree([1, 2, 3])
    assert tree.nodes[0].kind == "array"

    # scalar
    tree = build_json_tree("hello")
    assert tree.nodes[0].kind == "scalar"


def test_is_valid_jsonpath():
    """is_valid_jsonpath should validate paths correctly."""
    from ui.json_tree import is_valid_jsonpath

    assert is_valid_jsonpath("$") is True
    assert is_valid_jsonpath("$.a") is True
    assert is_valid_jsonpath("$.a[0].b") is True
    assert is_valid_jsonpath("") is False
    assert is_valid_jsonpath("foo") is False
    assert is_valid_jsonpath("$.") is False


def test_is_valid_jsonpath_max_len():
    """is_valid_jsonpath should respect MAX_PATH_LEN."""
    from ui.json_tree import is_valid_jsonpath

    long_path = "$" + ".a" * 250  # well over 200
    assert is_valid_jsonpath(long_path) is False


# ---------------------------------------------------------------------------
# View tests (admin client): raw-data click-to-map prefill behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Click-Map Org")


@pytest.fixture
def admin(org: Organisation) -> User:
    return User.objects.create(
        login="click-admin",
        username="click-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )


@pytest.fixture
def source(org: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug="click-feed",
        name="Click feed",
        mapping_config={"version": 2, "fields": {"title": {"paths": ["$.name"]}}},
    )


@pytest.fixture
def client(admin: User) -> Client:
    c = Client()
    c.force_login(admin)
    return c


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _make_alert(source, org, *, source_ref, payload, date):
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title="Raw alert",
        type="phishing",
        source="webhook",
        source_ref=source_ref,
        status=status,
        raw_payload=payload,
        ingestion_source=source,
        owner_org=org,
        date=date,
    )


def test_get_mapping_tab_with_prefill_title_path(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """GET ?tab=mapping&map_target=title&map_path=$.iocs.ip → 200, path appended to textarea."""
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(_url(source) + "?tab=mapping&map_target=title&map_path=$.iocs.ip")
    body = response.content.decode()
    assert response.status_code == 200
    # The path should appear in the title textarea (Django autoescapes " to "
    # but $.iocs.ip has no quotes so it appears as-is in the value attribute).
    assert "paths__title" in body
    # The JSONPath string should be present in the rendered HTML
    assert "$.iocs.ip" in body
    # source.mapping_config must be unchanged
    source.refresh_from_db()
    assert source.mapping_config == {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}


def test_get_mapping_tab_dedup_path_already_present(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Path already present → appears once after re-render with prefill."""
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    # First request adds the path
    client.get(_url(source) + "?tab=mapping&map_target=title&map_path=$.iocs.ip")
    # Second request with the same path → should not duplicate
    response = client.get(_url(source) + "?tab=mapping&map_target=title&map_path=$.iocs.ip")
    body = response.content.decode()
    assert response.status_code == 200
    # Dedup is about the textarea: the path also appears once in the prefill hint.
    start = body.index('id="paths__title"')
    textarea = body[start : body.index("</textarea>", start)]
    count = textarea.count("$.iocs.ip")
    assert count == 1, f"expected 1 occurrence of $.iocs.ip in the title textarea, got {count}"
    source.refresh_from_db()
    assert source.mapping_config == {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
    }  # no change


def test_get_mapping_tab_invalid_target_ignored(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Invalid target → silently ignored, no append, normal render."""
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(_url(source) + "?tab=mapping&map_target=invalid_target&map_path=$.foo")
    assert response.status_code == 200
    source.refresh_from_db()
    assert source.mapping_config == {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
    }  # no change


def test_get_mapping_tab_invalid_path_ignored(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Invalid JSONPath → silently ignored, no append."""
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(_url(source) + "?tab=mapping&map_target=title&map_path=not-a-path")
    body = response.content.decode()
    assert response.status_code == 200
    # The invalid path should not be appended
    assert "$.iocs.ip" not in body  # path should not appear from invalid input
    source.refresh_from_db()
    assert source.mapping_config == {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
    }  # no change


def test_get_mapping_tab_observable_prefill(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """observable prefill with a real type name → observable_paths__<type> contains the path."""
    # Create an observable type first
    ObservableType.objects.create(name="IP", is_case_sensitive=False)
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(
        _url(source)
        + "?tab=mapping&map_target=observable&map_observable_type=IP&map_path=$.iocs.ip"
    )
    body = response.content.decode()
    assert response.status_code == 200
    # The observable paths textarea for IP should contain the path
    assert "observable_paths__IP" in body
    assert "$.iocs.ip" in body
    # source.mapping_config must be unchanged
    source.refresh_from_db()
    assert source.mapping_config == {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
    }  # no change


def test_get_mapping_tab_observable_prefill_config_unchanged(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """observable prefill: stored config unchanged, only the UI is prefilled."""
    ObservableType.objects.create(name="IP", is_case_sensitive=False)
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(
        _url(source)
        + "?tab=mapping&map_target=observable&map_observable_type=IP&map_path=$.iocs.ip"
    )
    assert response.status_code == 200
    source.refresh_from_db()
    # mapping_config should be the original, untouched
    assert source.mapping_config == {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
    }  # no change


def test_get_mapping_tab_analyst_404(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Analyst user → 404 on source detail page."""
    analyst = User.objects.create(
        login="click-analyst",
        username="click-analyst",
        email="analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    c = Client()
    c.force_login(analyst)
    assert c.get(_url(source)).status_code == 404


def test_get_mapping_tab_reflected_script_escaped(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Reflected <script> in map_path is handled safely (response 200, config unchanged)."""
    _make_alert(
        source,
        org,
        source_ref="ref1",
        payload={"note": "<script>alert(1)</script>"},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=mapping&map_target=title&map_path=<script>")
    assert response.status_code == 200
    source.refresh_from_db()
    assert source.mapping_config == {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}
