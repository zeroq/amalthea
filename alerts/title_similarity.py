"""Title normalization and Jaccard similarity for the alert fuzzy title gate.

Public helpers:
- normalize_title_tokens(): NFKC + casefold, strip UUIDs/timestamps/hex-tokens, tokenize,
  drop numeric/empty tokens, return a frozenset.
- jaccard_similarity(): Jaccard percentage for pre-normalized token sets.
- title_similarity(): convenience wrapper that normalizes two titles first.
"""

from __future__ import annotations

import re
import unicodedata

#: Regex that keeps dots inside what look like domains or IP octets, then splits on
#: non-alphanumeric boundaries.  After NFKC + casefold, we replace hyphens with spaces
#: and run this pattern to extract tokens while preserving dots within e.g. "1.2.3.4" or
#: "sub.example.com".
_DOMAIN_SAFE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._]*")

#: Regex that matches UUID v4/v6 patterns (8-4-4-4-12 hex digits, separated by hyphens).
#: Matches standard UUID format embedded in text e.g. "123e4567-e89b-12d3-a456-426614174000".
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

#: Regex that matches ISO-8601-ish timestamps (anything that looks like an ISO date/time string).
#: We be conservative: sequences that look like YYYY-MM-DD or contain a T separator with
#: time components, optionally with timezone.
_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(?::\d{2})?"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
)

#: Regex that matches hex tokens of length >= 8 that contain a digit.  Candidates are
#: checked for a digit before being stripped, so words like "deadbeef" survive.
_HEX8_RE = re.compile(r"[a-fA-F0-9]{8,}")


def _remove_uuids(title: str) -> str:
    """Remove UUID v4/v6 patterns from the title string."""
    return _UUID_RE.sub(" ", title)


def _remove_iso_timestamps(title: str) -> str:
    """Remove ISO-8601-ish timestamp patterns from the title string."""
    return _ISO_TIMESTAMP_RE.sub(" ", title)


def _remove_hex_tokens(title: str) -> str:
    """Remove hex tokens of length >= 8 that contain a digit, replacing with space."""

    def _replace_hex(m: re.Match[str]) -> str:
        token = m.group(0)
        # Check if the token contains at least one digit
        if re.search(r"\d", token):
            return " "
        return token

    return _HEX8_RE.sub(_replace_hex, title)


def normalize_title_tokens(title: str) -> frozenset[str]:
    """Normalize a title into a frozenset of tokens for Jaccard similarity.

    Normalization steps (in order):
    1. Unicode NFKC normalize, then casefold().
    2. Remove UUIDs, ISO-8601 timestamps, and hex tokens of length >= 8 that contain a digit.
    3. Replace hyphens with spaces.
    4. Tokenize with a domain-safe pattern [a-z0-9][a-z0-9._]* that preserves dots inside
       domain/IP tokens.
    5. Drop empty and purely numeric tokens (so ALERT-123 and ALERT-99 normalize to {alert, ...}).
    6. Return a frozenset. Order is irrelevant; duplicate words do not inflate the score.

    Args:
        title: The raw alert title string.

    Returns:
        A frozenset of normalized tokens.
    """
    if not title:
        return frozenset()

    # 1. NFKC normalize + casefold
    normalized = unicodedata.normalize("NFKC", title).casefold()

    # 2. Remove UUIDs, ISO-8601 timestamps, and hex tokens >=8 with a digit
    normalized = _remove_uuids(normalized)
    normalized = _remove_iso_timestamps(normalized)
    normalized = _remove_hex_tokens(normalized)

    # 3. Replace hyphens with spaces (so "ALERT-123" -> "ALERT 123")
    normalized = normalized.replace("-", " ")

    # 4. Tokenize with domain-safe pattern
    raw_tokens = _DOMAIN_SAFE_TOKEN_RE.findall(normalized)

    # 5. Drop empty and purely numeric tokens
    filtered: list[str] = []
    for token in raw_tokens:
        # Drop tokens that are purely numeric (e.g. "123")
        if token.isdigit():
            continue
        # Drop empty tokens (shouldn't happen with the regex, but be safe)
        if not token.strip():
            continue
        filtered.append(token)

    return frozenset(filtered)


def jaccard_similarity(left_tokens: frozenset[str], right_tokens: frozenset[str]) -> int | None:
    """Jaccard percentage for two already-normalized token sets.

    Returns ``None`` when either set is empty so callers can apply the documented
    payload-only fallback.
    """
    if not left_tokens or not right_tokens:
        return None

    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens

    if not union:
        return None

    score = round(100 * len(intersection) / len(union))
    # Clamp to 0-100 (should always be in range, but be safe)
    return max(0, min(100, score))


def title_similarity(left: str, right: str) -> int | None:
    """Compute Jaccard similarity percentage between two title strings.

    Returns:
        - round(100 * |A intersect B| / |A union B|) if both normalized sets are non-empty.
        - None if either normalized set is empty (title gate falls back to payload-only).
    """
    return jaccard_similarity(normalize_title_tokens(left), normalize_title_tokens(right))
