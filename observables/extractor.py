"""Typed observable extraction, normalization and global dedupe (AGENTS.md Module C).

**Scope note.** This is Phase 5's `observables.extractor` reduced to the MVP loop: it turns free
text (and the JSON leaves of an alert's `raw_payload`) into typed `Observable` rows, normalizes
each value by its type's own rule, and links them to a case.

Two properties are load-bearing and are the reason this is a service rather than a regex call
site:

* **Global dedupe.** An `Observable` is a standalone entity, unique on `(data_type, data_hash)`.
  The same artifact appearing in four cases over six months is *one row* linked four times — that
  fan-out is what surfaces the persistent campaign. So extraction never creates a second row for
  a value already known; it reuses it and adds the link.
* **Normalization before hashing.** `Observable.data_hash` is derived from `normalized_data` by
  `DataHashField.pre_save`, so the normalizer here is what decides identity. It asks the type
  (`is_case_sensitive`, seeded in `observables/0002_seed`) rather than hardcoding a per-type rule,
  so an analyst-extensible vocabulary behaves consistently with its own definition.

Extraction is deliberately conservative: a value that cannot be typed confidently is left in the
text rather than guessed at, because a wrong `Observable` row is durable evidence that analysts
will later reason from.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction

from observables.hashing import canonical_value_under
from observables.models import Observable, ObservableType

if TYPE_CHECKING:  # pragma: no cover
    from cases.models import Case

#: Top-level domains the `fqdn` pattern will accept, longest-first so the alternation cannot match a
#: shorter label and leave a tail behind (regex alternation is first-match, not longest-match).
#: Includes RFC 2606 reserved names (`example`, `test`, `invalid`, `localhost`) because adversary
#: infrastructure documentation uses them, and a SOC's own internal suffixes (`.internal`, `.lan`,
#: `.corp`, `.local`) because Module C exists to correlate an internal pivot too.
_TLDS: tuple[str, ...] = (
    "com",
    "net",
    "org",
    "edu",
    "gov",
    "mil",
    "int",
    "io",
    "co",
    "ai",
    "app",
    "dev",
    "cloud",
    "online",
    "site",
    "xyz",
    "top",
    "info",
    "biz",
    "pro",
    "ru",
    "cn",
    "jp",
    "kr",
    "in",
    "br",
    "de",
    "fr",
    "nl",
    "uk",
    "it",
    "es",
    "pl",
    "se",
    "no",
    "fi",
    "dk",
    "cz",
    "at",
    "ch",
    "be",
    "ie",
    "pt",
    "gr",
    "ro",
    "hu",
    "ua",
    "tr",
    "za",
    "mx",
    "ar",
    "cl",
    "ca",
    "au",
    "nz",
    "sg",
    "hk",
    "tw",
    "th",
    "my",
    "id",
    "ph",
    "vn",
    "eu",
    "mil.co",
    "example",
    "test",
    "invalid",
    "localhost",
    "local",
    "internal",
    "lan",
    "corp",
    "home",
    "intranet",
)
_TLD_ALTERNATION = "|".join(re.escape(t) for t in sorted(_TLDS, key=len, reverse=True))

#: Type name → pattern, in **priority order**. Order is load-bearing: `mail` and `url` are tried
#: before `fqdn` because a hostname inside an address or a URL is not a bare-domain finding, and
#: the longest match claims the span so the narrower pattern cannot double-report it.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mail", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("url", re.compile(r"\b(?:https?|ftp)://[^\s<>\"'\)\]]+", re.IGNORECASE)),
    (
        "ip",
        re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ),
    # A file path: a drive letter, a UNC prefix, or a slash-bearing token with an extension.
    (
        "file",
        re.compile(
            r"(?:[A-Za-z]:\\[^\s<>\"'|?*]+|\\\\[^\s<>\"'|?*]+|/[^\s<>\"'|?*]*\.[A-Za-z0-9]{1,8})"
        ),
    ),
    # MD5 / SHA-1 / SHA-256 by length. `hash` is seeded case-insensitive, so AC5.4's "case hashes
    # normalize to lowercase" holds through the shared case rule rather than a special case here.
    ("hash", re.compile(r"\b[A-Fa-f0-9]{32}\b|\b[A-Fa-f0-9]{40}\b|\b[A-Fa-f0-9]{64}\b")),
    (
        "fqdn",
        # The TLD alternation is curated, not `[A-Za-z]{2,}`. A permissive tail turned every
        # dotted identifier in a payload into a "hostname" — a real capture had `event:
        # "mailbox.rule.created"` land in a case as an fqdn finding. Refusing to type a value we
        # cannot type is the conservative side of this module's bargain; the vocabulary is extensible
        # if a SOC needs a TLD that is not listed.
        re.compile(
            r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+(?:"
            + _TLD_ALTERNATION
            + r")\b",
            re.IGNORECASE,
        ),
    ),
)


#: Loopback / unspecified / broadcast literals are never an indicator of compromise. Bandit reads the
#: literals as bind-all addresses, which is exactly what they are *not* here — this is the list of
#: values that must never become an artifact — so the rule is suppressed with that reasoning rather
#: than by splitting the strings.
_STOPLIST: frozenset[str] = frozenset(
    {
        "0.0.0.0",  # noqa: S104
        "127.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "0:0:0:0:0:0:0:0",
    }
)


@dataclass(frozen=True)
class Finding:
    """One extracted artifact: its type, the value as written, and the normalized form."""

    type_name: str
    raw: str
    normalized: str

    def __str__(self) -> str:
        return f"{self.type_name}:{self.normalized}"


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalize(type_name: str, value: str, *, is_case_sensitive: bool) -> str:
    """The canonical stored form of `value` for a given observable type.

    Type-aware on purpose: an address is canonicalised through `ipaddress` so two spellings of one
    host (`::1` / `0:0:0:0:0:0:0:1`) are one artifact; a hostname loses any scheme, path, userinfo
    or trailing dot it arrived with; a URL's scheme and authority are lower-cased per RFC 3986 while
    its path is left alone. Everything then passes through `canonical_value_under` with the type's
    own `is_case_sensitive` — the *same* function `data_hash` is taken over, so normalization and
    identity cannot disagree.
    """
    text = value.strip().strip(".,;:'\"")
    if type_name == "ip" and _is_ip(text):
        return str(ipaddress.ip_address(text))
    if type_name in {"fqdn", "domain"}:
        host = text.split("://", 1)[-1].split("/", 1)[0].split("@", 1)[-1]
        return host.rstrip(".").lower()
    if type_name == "url":
        scheme, sep, rest = text.partition("://")
        if sep:
            return f"{scheme.lower()}://{rest}"
        return text
    return canonical_value_under(text, case_sensitive=is_case_sensitive)


def _walk(node: Any) -> list[str]:
    """Every string *value* of a JSON document, so `raw_payload` is searchable as flat text.

    Object keys are deliberately **not** included. A key is a field name chosen by the sender, not
    something observed about the adversary: harvesting `"user.email"` as a `fqdn` would manufacture
    an artifact out of our own vocabulary, and an artifact row is durable evidence an analyst will
    later reason from.
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_walk(value))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for item in node:
            out.extend(_walk(item))
        return out
    if isinstance(node, bool) or node is None:
        return []
    return [str(node)]


def json_leaves(payload: Any) -> str:
    """Flatten a JSON document to newline-joined text, for extraction over `raw_payload`."""
    return "\n".join(_walk(payload))


def find_observables(*texts: str, types: dict[str, ObservableType]) -> list[Finding]:
    """Extract typed findings from free text and JSON leaves.

    `types` is the vocabulary (`{name: ObservableType}`); a pattern whose type is absent from it is
    skipped, so extending the vocabulary is what enables a new detector. The longest-priority match
    claims its span and any later, narrower pattern overlapping it is dropped — that is how an
    address inside a URL does not also become a `fqdn`.
    """
    haystack = "\n".join(t for t in texts if t)
    if not haystack.strip() or not types:
        return []

    found: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    consumed: list[tuple[int, int]] = []

    for type_name, pattern in PATTERNS:
        obs_type = types.get(type_name)
        if obs_type is None:
            continue
        case_sensitive = obs_type.is_case_sensitive
        for match in pattern.finditer(haystack):
            start, end = match.span()
            if any(not (end <= s or start >= e) for s, e in consumed):
                continue
            raw = match.group(0)
            if type_name == "ip" and not _is_ip(raw):
                continue
            normalized = normalize(type_name, raw, is_case_sensitive=case_sensitive)
            if not normalized or normalized.lower() in _STOPLIST:
                continue
            consumed.append((start, end))
            identity = normalized if case_sensitive else normalized.casefold()
            key = (type_name, identity)
            if key in seen:
                continue
            seen.add(key)
            found.append(Finding(type_name=type_name, raw=raw, normalized=normalized))

    return found


@transaction.atomic
def extract_into_case(case: Case, *texts: str) -> list[Observable]:
    """Extract from `texts`, dedupe globally, and link every finding to `case`.

    Re-running the extractor over the same text is idempotent: the second pass resolves the
    existing rows and adds nothing, because both the `Observable` and the `CaseObservable` link are
    backed by unique constraints.
    """
    from cases.models import CaseObservable

    vocabulary = {t.name: t for t in ObservableType.objects.all()}
    linked: list[Observable] = []
    for finding in find_observables(*texts, types=vocabulary):
        obs_type = vocabulary[finding.type_name]
        canonical = canonical_value_under(
            finding.normalized, case_sensitive=obs_type.is_case_sensitive
        )
        observable, _created = Observable.objects.get_or_create(
            data_type=obs_type,
            normalized_data=canonical,
            defaults={"data": finding.raw},
        )
        CaseObservable.objects.get_or_create(case=case, observable=observable, defaults={})
        linked.append(observable)
    return linked


@transaction.atomic
def add_observable(case: Case, type_name: str, value: str) -> Observable | None:
    """Add one analyst-supplied artifact to a case, typed and normalized.

    Returns `None` when `type_name` is not in the vocabulary: an unknown type is refused rather than
    silently coerced to `other`, because a mislabelled artifact is worse than a missing one.
    """
    from cases.models import CaseObservable

    obs_type = ObservableType.objects.filter(name=type_name).first()
    if obs_type is None:
        return None
    canonical = canonical_value_under(
        normalize(type_name, value, is_case_sensitive=obs_type.is_case_sensitive),
        case_sensitive=obs_type.is_case_sensitive,
    )
    observable, _created = Observable.objects.get_or_create(
        data_type=obs_type, normalized_data=canonical, defaults={"data": value.strip()}
    )
    CaseObservable.objects.get_or_create(case=case, observable=observable, defaults={})
    return observable
