"""Tag-name parsing shared by the case/alert/observable tag-link endpoints.

`InputCreateTag`'s `tags` is a string **or** an array of strings; both spellings are accepted and
normalised to a bounded list of names. The bound (`Tag.name`'s `max_length`) lives here because a
name longer than the column is a 500 on Postgres and a silent 201 on SQLite (auditor F4) — one
place, one answer.
"""

from __future__ import annotations

from typing import Any

MAX_TAG_NAME = 100


def tag_names_from_payload(payload: dict[str, Any]) -> list[str] | None:
    """Return the tag names in `payload["tags"]`, or `None` when none were supplied.

    `None` (rather than `[]`) is the "the caller said nothing" signal the views turn into a 400,
    so a body that omits `tags` cannot be mistaken for an intentional empty list. Whitespace-only
    entries are dropped; a name over the column limit is truncated rather than rejected.
    """
    raw = payload.get("tags")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    names = [str(item).strip()[:MAX_TAG_NAME] for item in raw]
    names = [name for name in names if name]
    return names or None
