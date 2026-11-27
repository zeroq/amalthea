"""Compatibility wrapper for the split correlation service."""

from __future__ import annotations

import sys
from types import ModuleType

from alerts.escalation import merge_alert_into_case

from . import _constants, bundling, similarity
from ._constants import (
    AUTO_BUNDLE_SOURCE,
    AUTO_BUNDLE_WINDOW_MAX_MINUTES,
    MAX_CORRELATION_OBS,
    MAX_FUZZY_COMPARISONS,
    SIMILAR_CLOSED_ALERTS_LIMIT,
)
from ._types import (
    AutoBundleDecision,
    SimilarAlert,
    SimilarAlertMatch,
    SimilarCase,
    _json_depth,
    _SimilarUnionEntry,
)
from .bundling import (
    _find_bundle_by_correlation_key,
    _find_bundle_by_shared_observable,
    auto_bundle_ingested_alert,
    find_auto_bundle_case,
    record_alert_bundled,
)
from .similarity import (
    alerts_fuzzy_similar_to_case,
    alerts_similar_to_alert,
    candidate_cases_for_alert,
    cases_sharing_findings,
)

__all__ = [
    "AUTO_BUNDLE_SOURCE",
    "AUTO_BUNDLE_WINDOW_MAX_MINUTES",
    "MAX_CORRELATION_OBS",
    "MAX_FUZZY_COMPARISONS",
    "SIMILAR_CLOSED_ALERTS_LIMIT",
    "AutoBundleDecision",
    "SimilarAlert",
    "SimilarAlertMatch",
    "SimilarCase",
    "_SimilarUnionEntry",
    "_find_bundle_by_correlation_key",
    "_find_bundle_by_shared_observable",
    "_json_depth",
    "alerts_fuzzy_similar_to_case",
    "alerts_similar_to_alert",
    "auto_bundle_ingested_alert",
    "candidate_cases_for_alert",
    "cases_sharing_findings",
    "find_auto_bundle_case",
    "merge_alert_into_case",
    "record_alert_bundled",
]

_PATCH_TARGETS: dict[str, tuple[object, ...]] = {
    "AUTO_BUNDLE_SOURCE": (_constants, bundling),
    "AUTO_BUNDLE_WINDOW_MAX_MINUTES": (_constants, bundling),
    "MAX_CORRELATION_OBS": (_constants, similarity, bundling),
    "MAX_FUZZY_COMPARISONS": (_constants, similarity),
    "SIMILAR_CLOSED_ALERTS_LIMIT": (_constants, similarity),
    "merge_alert_into_case": (bundling,),
    "record_alert_bundled": (bundling,),
}


class _PatchForwardingModule(ModuleType):
    """Keep historical `cases.correlation.*` monkeypatch targets effective."""

    def __setattr__(self, name: str, value: object) -> None:
        for module in _PATCH_TARGETS.get(name, ()):
            setattr(module, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _PatchForwardingModule
