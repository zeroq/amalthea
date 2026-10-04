"""`Alert.source_ref` resolution — the fix for REVIEW-2026-10-03 **C3**.

`Alert` carries an **unconditional** unique constraint on `(source, type, source_ref)`,
which is TheHive's documented de-duplication key and the correct shape. But
`source_ref` is `NOT NULL` with no default, and an arbitrary-payload webhook source is
under no obligation to carry a `sourceRef` — AGENTS.md Module A exists precisely to
accept payload shapes we have never seen. Before this module existed, such a source could
ingest **exactly one alert, ever**: the second payload collided with the first and the
request died with `IntegrityError: UNIQUE constraint failed: alert.source, alert.type,
alert.source_ref`. That makes AC4.3 ("an unconfigured source ingests without error, never
a 500") and AC4.4 ("replay is idempotent") structurally impossible, and it breaks the
product's front door.

So the pipeline must **always** populate a ref. When the mapping yields none we
synthesise one from the payload itself:

    "sha256:<hex digest of the canonicalised payload>"

That keeps both properties the constraint is there for:

* **Idempotent replay** — byte-identical payload → identical canonical form → identical
  digest → identical ref → the second ingest is recognised as a replay, not a new alert.
* **Distinct alerts** — two different payloads that both lack `sourceRef` hash
  differently, so they produce two alerts instead of colliding.

The substitution is never silent: it is returned as an ingestion warning (ADR-002 §D11,
"every tolerance is recorded on the entity") so an analyst can see that dedupe for this
feed is content-based rather than id-based.

Phase 4 wires this into the pipeline; it is deliberately a pure function plus a tiny
persistence helper so the contract can be tested before the webhook view exists.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Prefix of a synthesised ref, so a digest-derived ref is identifiable in the ledger.
SYNTHETIC_PREFIX = "sha256:"


def canonical_payload(payload: Any) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace, UTF-8 text.

    Two payloads that differ only in key order or spacing canonicalise identically, which
    is what makes replay detection depend on *content* rather than on the bytes the
    sender happened to serialise.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def resolve_source_ref(mapped_ref: str | None, payload: Any) -> tuple[str, str | None]:
    """Return ``(source_ref, warning)`` — exactly one of the two is ``None``.

    A mapped ref is passed through verbatim (TheHive's `sourceRef`). Otherwise a
    content-addressed ref is synthesised and a warning is returned for
    `ingestion_warnings`.
    """
    if mapped_ref:
        return mapped_ref, None
    return f"{SYNTHETIC_PREFIX}{payload_digest(payload)}", (
        "source_ref_missing: no sourceRef in payload or mapping config; synthesised "
        f"{SYNTHETIC_PREFIX}<digest of canonical payload>, so replay dedupe is "
        "content-based for this feed"
    )
