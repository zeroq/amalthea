"""Severity filter parsing and toggle-URL building for the alerts list.

This module is deliberately self-contained and has no Django model or ORM dependencies
so it can be imported and unit-tested without a database.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, QueryDict

from core.enums import SEVERITY_CHOICES, SEVERITY_MAX, SEVERITY_MIN

SEVERITY_PARAM = "severity"


def parse_severity_filter(get: QueryDict) -> list[int]:
    """Return valid, de-duplicated severities in ascending order.

    Reads zero or more repeated ``severity`` query parameters, accepts only integer
    values in ``SEVERITY_MIN..SEVERITY_MAX``, ignores everything else, deduplicates,
    and returns the result sorted ascending.
    """
    raw_values = get.getlist(SEVERITY_PARAM, [])
    valid: set[int] = set()
    for raw in raw_values:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if SEVERITY_MIN <= val <= SEVERITY_MAX:
            valid.add(val)
    return sorted(valid)


def build_severity_filter_links(request: HttpRequest, selected: list[int]) -> list[dict[str, Any]]:
    """Return ordered ``[{value, label, selected, url}]`` toggle options.

    ``selected`` is the current list of selected severity values (already parsed/validated).

    For each of the four severities (in ``SEVERITY_CHOICES`` order), the returned dict
    contains:

    - ``value``: the integer severity value
    - ``label``: the human label from ``SEVERITY_CHOICES``
    - ``selected``: ``True`` if this severity is in the selected set
    - ``url``: a local path + query string representing the toggled state

    URL logic:

    - Start from ``request.GET`` (which already contains the current query params).
    - Flip the toggled set for this single severity: ``selected_set ^ {value}``.
    - Copy ``request.GET`` and use ``setlist``/``pop`` to replace the ``severity``
      parameter with the new toggled set, preserving all other params including repeats.
    - Remove the ``page`` parameter.
    - If the new toggled set is empty, remove the ``severity`` key entirely.
    - The URL is ``request.path`` + ``?`` + remaining query string (or empty if nothing
      left).  Never an external/redirect URL.
    """
    chosen = set(selected) if selected else set()
    result: list[dict[str, Any]] = []

    for value, label in SEVERITY_CHOICES:
        # Toggle: if currently selected, remove it; if not, add it.
        new_selected = chosen ^ {value}

        # Build the query string for the toggled state.
        get_copy = request.GET.copy()

        if new_selected:
            # Replace all severity values with the new set, preserving as repeated params.
            # Sort values for deterministic output (set iteration order is not guaranteed).
            get_copy.setlist(SEVERITY_PARAM, [str(v) for v in sorted(new_selected)])
        else:
            # Remove severity entirely when the set is empty.
            get_copy.pop(SEVERITY_PARAM, None)

        # Always remove page.
        get_copy.pop("page", None)

        # Build the local URL: path + query string.
        # If there are no query params left, just the path.
        query_string = get_copy.urlencode()
        if query_string:
            url = f"{request.path}?{query_string}"
        else:
            url = request.path

        result.append(
            {
                "value": value,
                "label": label,
                "selected": value in chosen,
                "url": url,
            }
        )

    return result
