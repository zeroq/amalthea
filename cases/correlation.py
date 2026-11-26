"""Cross-case correlation primitives (P1 + P2).

This module provides the shared correlation reader and the audited writer used by
the Similar cases tab (Module C) and the alert→case attach path (Module B).

No new models, no migrations. Uses existing indexes:
- `caseobs_obs_case_idx` (`CaseObservable.observable`, `CaseObservable.case`) for the fan-out
- `alert_corrkey_date_idx` (`Alert.correlation_key`, `Alert.date`) for the correlation-key seek
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone

from alerts.escalation import merge_alert_into_case
from alerts.models import Alert, AlertObservable
from alerts.title_similarity import jaccard_similarity, normalize_title_tokens
from cases.models import Case, CaseObservable, CorrelationRule
from ingest.models import IngestionSource

if TYPE_CHECKING:
    from cases.models import TimelineEvent
    from identity.models import User
    from observables.extractor import Finding


# Hard cap on observable IDs fetched before the CaseObservable join (M2).
# Reuses existing cap constants - no new constant introduced.
# 5 MiB payload can hold thousands of small observables; 1000 bounds memory/CPU.
MAX_CORRELATION_OBS = 1000

# Hard cap on candidate unlinked alerts compared for fuzzy similarity (Phase B).
# Candidate set: unlinked + same-org + non-empty hash, ordered -date, sliced here.
# Comparison cost: len(case_hashes) * len(candidates) ppdeep.compare calls.
MAX_FUZZY_COMPARISONS = 500


# --- Auto-bundle service constants ---

AUTO_BUNDLE_SOURCE = "ingest:auto-bundle"
AUTO_BUNDLE_WINDOW_MAX_MINUTES = 60


@dataclass(frozen=True)
class AutoBundleDecision:
    """Explainable result of an auto-bundle attempt.

    Invariants:
      - Exactly one of ``status ∈ {"bundled", "skipped", "failed"}`` is set.
      - ``reason`` is a short, non-secret, human-readable explanation.
      - ``mode`` is the match mode used: "correlation_key" or "shared_observable".
      - ``case_id`` is set only when ``status == "bundled"``.
      - ``window_minutes`` is the rule's window, or ``None`` if skipped before evaluation.
      - ``signal`` describes the matching artefact (key name or observable type/value).
    """

    status: str  # "bundled" | "skipped" | "failed"
    reason: str
    mode: str = ""
    case_id: UUID | None = None
    window_minutes: int | None = None
    signal: str = ""


# Maximum number of similar alerts to return from the helper.
SIMILAR_CLOSED_ALERTS_LIMIT = 20


@dataclass(frozen=True)
class SimilarCase:
    """A case that shares observable artifacts with a source case or alert."""

    case: Case
    shared_count: int
    shared: list[tuple[str, str]]  # ordered by data_type then data


@dataclass(frozen=True)
class SimilarAlertMatch:
    """An alert matched via the similar-alerts helper."""

    alert: Alert
    similarity: int | None  # fuzzy score 0-100, or None when only observables matched
    shared_observable_count: int  # 0 when only fuzzy matched


class _SimilarUnionEntry(TypedDict):
    alert: Alert
    similarity: int | None
    shared_observable_count: int


@dataclass(frozen=True)
class SimilarAlert:
    """An unlinked alert that is fuzzily similar to this case's attached alerts.

    The exact-overlap fields (shared_count, shared) were retired in Phase B
    (PLAN-2026-10-20-alert-fuzzy-similarity.md §3.4). Only the fuzzy
    similarity score is retained.
    """

    alert: Alert
    similarity: int  # 0-100, maximum ppdeep.compare score against case's alerts


def _json_depth(obj: object, current: int = 0) -> int:
    """Iterative depth check to avoid RecursionError (M3)."""
    max_depth = 0
    stack = [(obj, current)]
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if isinstance(node, dict):
            for v in node.values():
                stack.append((v, depth + 1))
        elif isinstance(node, (list, tuple)):
            for v in node:
                stack.append((v, depth + 1))
    return max_depth


def cases_sharing_findings(
    findings: Sequence[Finding],
    *,
    user: User,
    exclude_case: Case | None = None,
    limit: int | None = None,
) -> tuple[list[SimilarCase], int]:
    """
    Rank visible cases in the same org by shared-observable overlap.

    Args:
        findings: The join key - extracted, normalized artifacts.
        user: The actor whose visibility scopes the query (ADR-004).
        exclude_case: Optional case to exclude from results (e.g., the source case).
        limit: Maximum rows to return (default: RAIL_CAP from ui.pagination).

    Returns:
        (top_limit_rows, total_matching) - total is computed BEFORE limit for honest truncation.

    Scope:
        Candidates restricted to `case_visible_q(user)` (includes own-org + shared cases).
        The redundant `owner_org` filter was removed (M4) - `case_visible_q` is the
        single source of truth for visibility and already encodes same-org + shares.

    Join:
        One grouped query over `CaseObservable` using the `observable`-leading index.
    """
    from ui.pagination import RAIL_CAP

    if limit is None:
        limit = RAIL_CAP

    if not findings:
        return [], 0

    # Build a lookup of (data_type, normalized_value) -> Finding
    # Findings from find_observables already have normalized form
    finding_keys: dict[tuple[str, str], Finding] = {}
    for f in findings:
        finding_keys[(f.type_name, f.normalized)] = f

    if not finding_keys:
        return [], 0

    # Get observable PKs matching these findings across all orgs,
    # then restrict via CaseObservable join to visible cases (M4).
    # Do NOT filter by owner_org here - shared cases link to observables
    # in the owning org, not the user's org.
    from cases.models import Case, CaseObservable
    from identity.scoping import case_visible_q
    from observables.models import Observable

    q_objects = Q()
    for type_name, normalized in finding_keys.keys():
        q_objects |= Q(data_type__name=type_name, normalized_data=normalized)

    # M2: Bound intermediate results - slice at DB level before fetching IDs
    matching_obs_ids = list(
        Observable.objects.filter(q_objects).values_list("id", flat=True)[:MAX_CORRELATION_OBS]
    )

    if not matching_obs_ids:
        return [], 0

    # Query CaseObservable using the observable-leading index
    # Actor-scoped via case_visible_q (includes shares). NO redundant owner_org filter (M4).
    base_qs = Case.objects.filter(case_visible_q(user))

    if exclude_case is not None:
        base_qs = base_qs.exclude(pk=exclude_case.pk)

    # Join through CaseObservable, filter by matching observable IDs,
    # exclude observables with ignore_similarity=True
    case_obs = (
        CaseObservable.objects.filter(case__in=base_qs)
        .filter(observable_id__in=matching_obs_ids)
        .exclude(observable__ignore_similarity=True)
        .select_related("observable__data_type", "case")
        .order_by("case_id")
    )

    # Group by case in Python (single query, then aggregate)
    case_to_obs: dict[UUID, list[CaseObservable]] = defaultdict(list)
    for co in case_obs:
        case_to_obs[co.case_id].append(co)

    if not case_to_obs:
        return [], 0

    # Build SimilarCase objects
    similar_cases: list[SimilarCase] = []
    for _case_id, obs_list in case_to_obs.items():
        case = obs_list[0].case
        shared_list = [
            (co.observable.data_type.name, co.observable.normalized_data) for co in obs_list
        ]
        # Order by data_type then data for stable explanation
        shared_list.sort(key=lambda x: (x[0], x[1]))
        similar_cases.append(
            SimilarCase(
                case=case,
                shared_count=len(shared_list),
                shared=shared_list,
            )
        )

    # Rank: shared_count desc, then case.number asc
    similar_cases.sort(key=lambda sc: (-sc.shared_count, sc.case.number))

    total_matching = len(similar_cases)
    return similar_cases[:limit], total_matching


def candidate_cases_for_alert(
    alert: Alert,
    *,
    user: User,
    limit: int | None = None,
) -> list[SimilarCase]:
    """
    Correlation-ranked candidates for attaching an alert to an existing case.

    Uses a DRY-RUN extraction over the alert's payload + title, plus the alert's
    correlation_key. Returns OPEN cases only, ranked by shared artifacts.

    Never raises on empty/garbage/malformed payload - degrades to plain open cases.
    """
    from cases.models import Case, CaseObservable
    from identity.scoping import case_visible_q
    from observables.extractor import find_observables, json_leaves
    from observables.models import Observable, ObservableType
    from ui.pagination import PICKER_PAGE_SIZE

    if limit is None:
        limit = PICKER_PAGE_SIZE

    user_org_id = getattr(user, "org_id", None)
    if user_org_id is None:
        return []

    # Base: open cases visible to the actor in the same org (via case_visible_q)
    # M4: NO redundant owner_org filter - case_visible_q includes shares.
    base_qs = Case.objects.filter(case_visible_q(user)).filter(closed_date__isnull=True)

    # 1) Dry-run extraction from alert payload + title (never raises)
    findings: list[Finding] = []
    try:
        # M3: Re-apply ingest bounds before processing attacker-controlled payload
        payload = alert.raw_payload or {}
        payload_size = len(str(payload))  # approximate; ingest uses JSON dumps
        max_body = getattr(settings, "WEBHOOK_MAX_BODY_SIZE", 5 * 1024 * 1024)
        max_depth = getattr(settings, "WEBHOOK_MAX_DEPTH", 30)

        if payload_size <= max_body and _json_depth(payload) <= max_depth:
            vocabulary = {t.name: t for t in ObservableType.objects.all()}
            texts = [alert.title or "", json_leaves(payload)]
            findings = find_observables(*texts, types=vocabulary)
        else:
            findings = []
    except (Exception, RecursionError, MemoryError):
        # M3: Catch RecursionError (BaseException) and MemoryError, not just Exception
        findings = []

    # 2) Add correlation_key as a synthetic finding if present
    corr_key_finding = None
    if alert.correlation_key:
        # correlation_key format: "namespace:value" e.g. "dest_ip:10.0.0.5"
        # We treat it as a special type for matching against observables
        # that were extracted with the same key logic
        try:
            ns, val = alert.correlation_key.split(":", 1)
            # Map namespace to observable type name if possible
            type_map = {
                "dest_ip": "ip",
                "src_ip": "ip",
                "ip": "ip",
                "domain": "fqdn",
                "url": "url",
                "hash": "hash",
                "email": "mail",
                "user": "user",
            }
            obs_type_name = type_map.get(ns, ns)
            # Create a synthetic finding
            from observables.extractor import Finding

            corr_key_finding = Finding(
                type_name=obs_type_name,
                raw=val,
                normalized=val,
            )
        except (ValueError, AttributeError):
            pass

    all_findings = list(findings)
    if corr_key_finding:
        all_findings.append(corr_key_finding)

    if not all_findings:
        # No extractable signal - return plain open cases (newest first) up to limit
        return [
            SimilarCase(case=c, shared_count=0, shared=[])
            for c in base_qs.order_by("-start_date")[:limit]
        ]

    # Get observable PKs matching these findings across all orgs,
    # then restrict via CaseObservable join to visible cases (M4).
    # Do NOT filter by owner_org here - shared cases link to observables
    # in the owning org, not the user's org.
    finding_keys = {(f.type_name, f.normalized) for f in all_findings}

    q_objects = Q()
    for type_name, normalized in finding_keys:
        q_objects |= Q(data_type__name=type_name, normalized_data=normalized)

    # M2: Bound intermediate results at DB level
    matching_obs_ids = list(
        Observable.objects.filter(q_objects).values_list("id", flat=True)[:MAX_CORRELATION_OBS]
    )

    if not matching_obs_ids:
        # No matching observables in any org - return plain open cases
        return [
            SimilarCase(case=c, shared_count=0, shared=[])
            for c in base_qs.order_by("-start_date")[:limit]
        ]

    # Join through CaseObservable, exclude ignore_similarity
    # Actor-scoped via case_visible_q (includes shares). NO redundant owner_org filter (M4).
    case_obs = (
        CaseObservable.objects.filter(case__in=base_qs)
        .filter(observable_id__in=matching_obs_ids)
        .exclude(observable__ignore_similarity=True)
        .select_related("observable__data_type", "case")
        .order_by("case_id")
    )

    # Group by case
    case_to_obs: dict[UUID, list[CaseObservable]] = defaultdict(list)
    for co in case_obs:
        case_to_obs[co.case_id].append(co)

    similar_cases: list[SimilarCase] = []
    for _case_id, obs_list in case_to_obs.items():
        case = obs_list[0].case
        shared_list = [
            (co.observable.data_type.name, co.observable.normalized_data) for co in obs_list
        ]
        shared_list.sort(key=lambda x: (x[0], x[1]))
        similar_cases.append(
            SimilarCase(
                case=case,
                shared_count=len(shared_list),
                shared=shared_list,
            )
        )

    # Rank: shared_count desc, then case.number asc
    similar_cases.sort(key=lambda sc: (-sc.shared_count, sc.case.number))

    # If no correlation matches, fall back to plain open cases
    if not similar_cases:
        return [
            SimilarCase(case=c, shared_count=0, shared=[])
            for c in base_qs.order_by("-start_date")[:limit]
        ]

    return similar_cases[:limit]


def alerts_fuzzy_similar_to_case(
    case: Case, *, user: User, threshold: int | None = None, limit: int | None = None
) -> tuple[list[SimilarAlert], int]:
    """
    Rank UNLINKED, same-org alerts by SSDEEP fuzzy similarity to `case`'s attached alerts.

    Signals: `payload_ssdeep` hashes and titles of the case's attached alerts (non-empty).
    Candidates: unlinked alerts in the same org with non-empty `payload_ssdeep`.

    Scoring: for each candidate, compute the maximum `ppdeep.compare(candidate_hash, h)`
    over all case alert hashes. Keep candidates where at least one case alert passes
    both the payload threshold and the title-similarity gate.

    The title gate is on by default (`ALERT_FUZZY_TITLE_GATE_ENABLED=True`) and can be
    disabled via that setting or the `AMALTHEA_ALERT_FUZZY_TITLE_GATE=false` env var.
    When disabled, behavior is exactly the current payload-only path.

    Returns:
        (top_limit_rows, total_matching) - total is computed BEFORE limit for honest truncation.
    """
    from ui.pagination import RAIL_CAP

    if limit is None:
        limit = RAIL_CAP

    # Resolve thresholds (global defaults, overrideable per-call)
    payload_threshold = (
        threshold
        if threshold is not None
        else getattr(settings, "ALERT_FUZZY_SIMILARITY_THRESHOLD", 60)
    )
    title_threshold = getattr(settings, "ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD", 70)
    title_gate_enabled = getattr(settings, "ALERT_FUZZY_TITLE_GATE_ENABLED", True)

    # 1. Collect case signal hashes AND titles from attached alerts
    from alerts.models import Alert
    from identity.scoping import org_q

    case_alerts = (
        Alert.objects.filter(case=case)
        .exclude(payload_ssdeep="")
        .values_list("payload_ssdeep", "title")[:MAX_CORRELATION_OBS]
    )

    # Precompute title tokens once per case signal.
    case_signals: list[tuple[str, frozenset[str]]] = []
    for payload_ssdeep, title in case_alerts:
        if payload_ssdeep:
            case_signals.append((payload_ssdeep, normalize_title_tokens(title or "")))

    if not case_signals:
        return [], 0

    # 2. Candidates: unlinked, same-org, non-empty hash, ordered by -date, capped
    candidates_qs = (
        Alert.objects.filter(case__isnull=True)
        .filter(org_q("owner_org", user))
        .exclude(payload_ssdeep="")
        .select_related("status", "owner_org")
        .order_by("-date")[:MAX_FUZZY_COMPARISONS]
    )

    # 3. Score each candidate: max ppdeep.compare against case hashes,
    #    with title-gate qualification
    try:
        import ppdeep  # type: ignore[import-untyped]
    except ImportError:
        ppdeep = None

    similar_alerts: list[SimilarAlert] = []
    if ppdeep is not None:
        for alert in candidates_qs:
            candidate_hash = alert.payload_ssdeep
            if not candidate_hash:
                continue

            candidate_tokens = (
                normalize_title_tokens(alert.title) if title_gate_enabled else frozenset()
            )
            max_score = 0
            max_qualifying_score = 0

            for case_hash, case_tokens in case_signals:
                try:
                    score = ppdeep.compare(candidate_hash, case_hash)
                except (Exception, RecursionError, MemoryError):
                    # Treat comparison failures as score 0
                    score = 0

                if score > max_score:
                    max_score = score

                if not title_gate_enabled:
                    continue
                # Only evaluate the title gate after this pair passes the payload
                # threshold, and only against this same case signal.
                if score < payload_threshold:
                    continue
                title_score = jaccard_similarity(candidate_tokens, case_tokens)
                if title_score is not None and title_score < title_threshold:
                    continue
                if score > max_qualifying_score:
                    max_qualifying_score = score

            qualifying_score = max_qualifying_score if title_gate_enabled else max_score
            if qualifying_score >= payload_threshold:
                similar_alerts.append(SimilarAlert(alert=alert, similarity=qualifying_score))

    # 4. Rank: similarity desc, then -date
    similar_alerts.sort(key=lambda sa: (-sa.similarity, -sa.alert.date.timestamp()))

    total_matching = len(similar_alerts)
    return similar_alerts[:limit], total_matching


def record_alert_bundled(
    case: Case,
    alert: Alert,
    *,
    actor: User | None = None,
    reason: str,
    source: str,
) -> TimelineEvent:
    """
    Record an alert→case bundle in the ledger and audit log.

    Writes exactly ONE TimelineEvent (kind="correlation-bundle") and ONE AuditLog
    row (action="case.alert_bundled"). Caller owns the transaction.

    Refuses:
    - Closed case
    - Cross-org (alert.owner_org != case.owner_org)
    - Viewer actor
    - Duplicate bundle (idempotence): if a correlation-bundle event for this
      alert+case already exists, refuse to prevent double-recording on replay.

    Details carries only non-secret data: counts, correlation key, source refs.
    """
    from django.core.exceptions import PermissionDenied

    from cases.ledger import append_timeline_event
    from cases.models import TimelineEvent
    from identity.models import AuditLog
    from identity.scoping import is_viewer

    # Refuse closed case
    if case.status and case.status.stage == "Closed":
        raise PermissionDenied("Cannot bundle alert into a closed case")

    # Refuse cross-org
    if alert.owner_org_id != case.owner_org_id:
        raise PermissionDenied("Cross-org alert bundle refused")

    # Refuse viewer actor
    if actor and is_viewer(actor):
        raise PermissionDenied("Viewer role cannot bundle alerts")

    # Idempotence: check if a correlation-bundle event for this alert+case already exists
    # (This handles the case where import_alert_to_case / merge_alert_into_case already
    # linked the alert, and we're now recording the bundle - we must not record twice.)
    existing_bundle = TimelineEvent.objects.filter(
        case=case,
        kind="correlation-bundle",
        metadata__alert_id=str(alert.id),
    ).exists()
    if existing_bundle:
        raise PermissionDenied("Alert already bundled into this case")

    # Build non-secret details
    details = {
        "alert_id": str(alert.id),
        "alert_source": alert.source,
        "alert_type": alert.type,
        "alert_source_ref": alert.source_ref,
        "correlation_key": alert.correlation_key,
        "reason": reason,
        "source": source,  # "escalate" or "merge"
    }

    # Ledger entry via the single write path, so the bundle reaches live clients in the case
    # room and the choke-point scan in tests/conformance/test_realtime.py stays honest.
    event = append_timeline_event(
        case,
        title=f"Alert bundled: {alert.title}",
        kind="correlation-bundle",
        description=f"Reason: {reason}. Source: {alert.source}, type {alert.type}, ref {alert.source_ref}",
        actor=actor,
        metadata={"alert_id": str(alert.id), "reason": reason, "source": source},
    )

    # Audit log entry (idiom from cases/update.py:280-289)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None) if actor else None,
        target_org=case.owner_org,
        action="case.alert_bundled",
        target_type="Case",
        target_id=str(case.id),
        ip=None,
        details=details,
    )

    return event


def alerts_similar_to_alert(
    source: Alert,
    *,
    user: User,
    limit: int = SIMILAR_CLOSED_ALERTS_LIMIT,
) -> tuple[list[SimilarAlertMatch], int]:
    """
    Return up to `limit` closed alerts similar to `source`, scoped to the caller's
    organisation and the source's organisation.

    Two similarity channels are fused via union by alert.id:

    - Fuzzy (SSDEEP + title gate) channel: when source has a non-empty
      ``payload_ssdeep``, candidates are same-org closed alerts with non-empty
      ssdeep, scored with ``ppdeep.compare`` and a Jaccard title gate.
    - Observable channel: when source has AlertObservables, closed alerts that
      share those observables (excluding ``observable__ignore_similarity=True``)
      get a ``shared_observable_count`` annotation; ``similarity`` is ``None``
      for observable-only matches.

    Returns:
        (rows, total_matching) - total is computed BEFORE the final slice for
        honest truncation reporting.

    Scope (both channels):
        - ``status__stage="Closed"``, exclude ``source.pk``.
        - ``org_q("owner_org", user)`` AND ``owner_org_id=source.owner_org_id``.
        - ``select_related("status", "owner_org")``.
        - ``order_by("-updated_at", "-date", "-pk")``.

    Fuzzy channel:
        - Skip if source ``payload_ssdeep`` is empty.
        - Cap candidates at ``MAX_FUZZY_COMPARISONS`` before scoring.
        - Require candidate non-empty ``payload_ssdeep``.
        - ``ppdeep.compare(candidate.payload_ssdeep, source.payload_ssdeep)``.
        - Title gate: ``normalize_title_tokens()`` + ``jaccard_similarity()``
          from ``alerts.title_similarity``, same pairwise semantics as the case
          Similar helper.
        - Import/comparison failures degrade to score ``0``.

    Observable channel:
        - Read source ``AlertObservable`` rows, excluding
          ``observable__ignore_similarity=True``, cap at ``MAX_CORRELATION_OBS``.
        - Query closed alerts through shared observable IDs, excluding
          ``observable__ignore_similarity=True``.
        - Annotate ``Count("observable_id", distinct=True)`` as
          ``shared_observable_count``.
        - Cap candidate alert rows at ``MAX_FUZZY_COMPARISONS`` after applying
          base ordering.

    Union:
        - Merge by ``alert.id``; dual matches retain max fuzzy score and shared
          observable count.
        - ``total_matching = len(union)`` before final slice.
        - Return ``(sorted(union.values(), key=base_order)[:limit], total_matching)``.
    """
    from django.db.models import Count

    from alerts.models import Alert
    from alerts.title_similarity import jaccard_similarity, normalize_title_tokens
    from identity.scoping import org_q

    try:
        import ppdeep
    except ImportError:
        ppdeep = None

    # -------------------------------------------------------------------------
    # Base scope: closed-only, same org as source + caller, exclude source
    # -------------------------------------------------------------------------
    base_qs = (
        Alert.objects.filter(status__stage="Closed")
        .exclude(pk=source.pk)
        .filter(org_q("owner_org", user))
        .filter(owner_org_id=source.owner_org_id)
        .select_related("status", "owner_org")
        .order_by("-updated_at", "-date", "-pk")
    )

    # -------------------------------------------------------------------------
    # Fuzzy channel (SSDEEP + title gate)
    # -------------------------------------------------------------------------
    payload_threshold = getattr(settings, "ALERT_FUZZY_SIMILARITY_THRESHOLD", 60)
    title_threshold = getattr(settings, "ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD", 70)
    title_gate_enabled = getattr(settings, "ALERT_FUZZY_TITLE_GATE_ENABLED", True)

    fuzzy_results: list[tuple[Alert, int | None]] = []
    if not source.payload_ssdeep:
        # No SSDEEP hash on source → fuzzy channel yields no matches
        pass
    else:
        # Collect candidate alerts: closed, same-org, non-empty ssdeep, capped
        candidates_qs = base_qs.exclude(payload_ssdeep="")[:MAX_FUZZY_COMPARISONS]

        # Pre-compute source tokens for title gate
        source_tokens = normalize_title_tokens(source.title or "")

        # Score each candidate
        for candidate in candidates_qs:
            try:
                score = ppdeep.compare(candidate.payload_ssdeep, source.payload_ssdeep)
            except (Exception, RecursionError, MemoryError):
                score = 0

            # Title gate: Jaccard on normalized tokens
            candidate_tokens = normalize_title_tokens(candidate.title or "")
            title_score = None
            if candidate_tokens and source_tokens:
                try:
                    title_score = jaccard_similarity(candidate_tokens, source_tokens)
                except Exception:
                    title_score = None

            # Apply title gate + payload threshold qualification
            qualifying = True
            if title_gate_enabled and title_score is not None:
                if score < payload_threshold or title_score < title_threshold:
                    qualifying = False
            elif title_gate_enabled and score < payload_threshold:
                qualifying = False

            if qualifying and score >= payload_threshold:
                fuzzy_results.append((candidate, score))

    # -------------------------------------------------------------------------
    # Observable channel (shared stored observables)
    # -------------------------------------------------------------------------
    from alerts.models import AlertObservable

    # Read source AlertObservable rows, excluding observable__ignore_similarity=True
    source_obs = (
        AlertObservable.objects.filter(alert=source)
        .exclude(observable__ignore_similarity=True)
        .values_list("observable_id", flat=True)
        .distinct()[:MAX_CORRELATION_OBS]
    )

    obs_results: list[tuple[Alert, int | None, int]] = []  # (alert, similarity, shared_count)
    if source_obs:
        # Query closed alerts through shared observable IDs, excluding ignore_similarity
        obs_alerts = (
            Alert.objects.filter(status__stage="Closed")
            .filter(
                alert_observables__observable_id__in=source_obs,
                alert_observables__observable__ignore_similarity=False,
            )
            .filter(org_q("owner_org", user))
            .filter(owner_org_id=source.owner_org_id)
            .exclude(pk=source.pk)
            .annotate(
                shared_observable_count=Count("alert_observables__observable_id", distinct=True),
            )
            .select_related("status", "owner_org")
            .order_by("-updated_at", "-date", "-pk")[:MAX_FUZZY_COMPARISONS]
        )

        for obs_alert in obs_alerts:
            # Observable-only match: similarity is None
            obs_results.append((obs_alert, None, obs_alert.shared_observable_count or 0))

    # -------------------------------------------------------------------------
    # Union by alert.id, preserve both signals
    # -------------------------------------------------------------------------
    # Build a dict keyed by alert.pk, keeping the best signal per alert
    union_by_id: dict[UUID, _SimilarUnionEntry] = {}

    # Add fuzzy results first
    for fuzzy_alert, score in fuzzy_results:
        aid = fuzzy_alert.pk
        if aid not in union_by_id:
            union_by_id[aid] = {
                "alert": fuzzy_alert,
                "similarity": score,
                "shared_observable_count": 0,
            }

    # Add observable results; dual matches keep max fuzzy score and observable count
    for candidate_alert, _similarity, shared_count in obs_results:
        aid = candidate_alert.pk
        if aid in union_by_id:
            # Dual match: keep the existing fuzzy similarity, update obs count if larger
            existing = union_by_id[aid]
            if shared_count > existing["shared_observable_count"]:
                existing["shared_observable_count"] = shared_count
            # Keep existing similarity (fuzzy score is preserved)
        else:
            # Observable-only match: similarity is None
            union_by_id[aid] = {
                "alert": candidate_alert,
                "similarity": None,
                "shared_observable_count": shared_count,
            }

    # -------------------------------------------------------------------------
    # Sort by base order and slice
    # -------------------------------------------------------------------------
    # Build order index from base_qs
    order_pks = base_qs.values_list("pk", flat=True)
    order_index = {pk: i for i, pk in enumerate(order_pks)}

    sorted_union = sorted(
        union_by_id.values(), key=lambda item: order_index.get(item["alert"].pk, 999999)
    )

    total_matching = len(union_by_id)  # pre-slice union size

    result: list[SimilarAlertMatch] = []
    for item in sorted_union[:limit]:
        result.append(
            SimilarAlertMatch(
                alert=item["alert"],
                similarity=item["similarity"],
                shared_observable_count=item["shared_observable_count"],
            )
        )

    return result, total_matching


def find_auto_bundle_case(
    alert: Alert,
    *,
    rule: CorrelationRule,
    org_id: UUID,
) -> Case | None:
    """Determine whether an alert should be auto-bundled into an existing case.

    Returns the target Case if a match is found, or None if bundling should be skipped.
    The caller (auto_bundle_ingested_alert) is responsible for all pre-query skips
    (replay, already_linked, tenant mismatch, rule disabled, source disabled)
    and for wrapping the final re-check + merge in ``transaction.atomic()``.
    """
    # Correlation-key mode (default)
    if rule.match_mode == "correlation_key":
        return _find_bundle_by_correlation_key(alert, rule)

    # Shared-observable mode
    if rule.match_mode == "shared_observable":
        return _find_bundle_by_shared_observable(alert, rule)

    # Unknown mode - should not happen thanks to the DB constraint
    return None


def _find_bundle_by_correlation_key(
    alert: Alert,
    rule: CorrelationRule,
) -> Case | None:
    """Correlation-key mode: drive from Alert with exact filters/order/cap from plan §6.2."""
    from datetime import timedelta

    anchor = timezone.now()
    window_start = anchor - timedelta(minutes=rule.window_minutes)

    if not alert.correlation_key:
        return None

    from ui.pagination import PICKER_PAGE_SIZE

    candidate_alerts = (
        Alert.objects.filter(
            owner_org_id=rule.organisation_id,
            correlation_key=alert.correlation_key,
            date__gt=window_start,
            date__lte=anchor,
            case__isnull=False,
            case__owner_org_id=rule.organisation_id,
            case__closed_date__isnull=True,
            case__status__stage__in=("New", "InProgress"),
        )
        .exclude(pk=alert.pk)
        .select_related("case", "case__status")
        .order_by("-date", "-id")[:PICKER_PAGE_SIZE]
    )

    # Distinct case_id pick
    matched_case_id: UUID | None = None
    for ca in candidate_alerts:
        if ca.case_id is not None and ca.case_id != alert.pk:
            matched_case_id = ca.case_id
            break

    if matched_case_id is None:
        return None

    # Re-fetch the case
    try:
        case = Case.objects.get(pk=matched_case_id)
    except Case.DoesNotExist:
        return None

    # Defensive re-checks (caller does the final re-check + merge)
    if case.closed_date is not None:
        return None
    if case.owner_org_id != rule.organisation_id:
        return None
    if case.status is None or case.status.stage not in ("New", "InProgress"):
        return None

    return case


def _find_bundle_by_shared_observable(
    alert: Alert,
    rule: CorrelationRule,
) -> Case | None:
    """Shared-observable mode: use AlertObservable links and CaseObservable as in plan §6.3."""
    from django.db.models import Count, Max

    from ui.pagination import PICKER_PAGE_SIZE

    window = timedelta(minutes=rule.window_minutes)
    anchor = timezone.now()
    window_start = anchor - window

    # Fetch at most MAX_CORRELATION_OBS observable IDs from this alert
    observable_ids = list(
        AlertObservable.objects.filter(alert=alert).values_list("observable_id", flat=True)[
            :MAX_CORRELATION_OBS
        ]
    )

    if not observable_ids:
        return None

    matches = (
        CaseObservable.objects.filter(
            observable_id__in=observable_ids,
            observable__ignore_similarity=False,
            case__owner_org_id=rule.organisation_id,
            case__closed_date__isnull=True,
            case__status__stage__in=("New", "InProgress"),
            created_at__gt=window_start,
            created_at__lte=anchor,
        )
        .values("case_id")
        .annotate(shared_count=Count("id"), latest_match=Max("created_at"))
        .order_by("-shared_count", "-latest_match", "case_id")[:PICKER_PAGE_SIZE]
    )

    if not matches:
        return None

    # Pick the first case_id
    first_match = matches[0]
    matched_case_id: UUID = first_match["case_id"]

    # Re-fetch the case
    try:
        case = Case.objects.get(pk=matched_case_id)
    except Case.DoesNotExist:
        return None

    # Defensive re-checks (caller does the final re-check + merge)
    if case.closed_date is not None:
        return None
    if case.owner_org_id != rule.organisation_id:
        return None
    if case.status is None or case.status.stage not in ("New", "InProgress"):
        return None

    return case


def auto_bundle_ingested_alert(
    alert: Alert,
    *,
    source: IngestionSource,
    org_id: UUID,
    created: bool,
) -> AutoBundleDecision:
    """Evaluate the CorrelationRule and auto-bundle the alert into a case if eligible.

    Pre-condition: Caller has already performed these skips (see plan §6.1 #1-6):
      1. created=False → replay
      2. alert.case_id already set → already_linked
      3. alert.owner_org_id != org_id → tenant_mismatch
      4. No CorrelationRule for org_id → rule_disabled
      5. source.correlation_enabled=False → source_correlation_disabled
      6. window_minutes clamped to 1..60; PICKER_PAGE_SIZE as cap

    One ``transaction.atomic()`` encloses the final re-check + merge + record.
    Actor is None. Known exceptions (PermissionDenied, ValidationError) are caught
    and returned as skipped/failed; unexpected exceptions are logged and return failed.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Load the CorrelationRule for this org; missing/row means disabled
    rule = CorrelationRule.objects.filter(organisation_id=org_id).order_by("-created_at").first()

    # ── Pre-existing skip checks (caller's responsibility, guarded here) ──
    if not created:
        return AutoBundleDecision(
            status="skipped",
            reason="replay",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if alert.case_id is not None:
        return AutoBundleDecision(
            status="skipped",
            reason="already_linked",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if alert.owner_org_id != org_id:
        return AutoBundleDecision(
            status="skipped",
            reason="tenant_mismatch",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    if rule is None or not rule.enabled:
        return AutoBundleDecision(
            status="skipped",
            reason="rule_disabled",
            mode=rule.match_mode if rule else "",
            window_minutes=getattr(rule, "window_minutes", None) if rule else None,
        )

    # Source-level kill switch
    if not source.correlation_enabled:
        return AutoBundleDecision(
            status="skipped",
            reason="source_correlation_disabled",
            mode=rule.match_mode,
            window_minutes=rule.window_minutes,
        )

    # Clamp window_minutes to 1..60 and use PICKER_PAGE_SIZE as candidate cap.
    # The in-memory copy is deliberate: an out-of-range DB value is never persisted.
    effective_window = max(1, min(60, rule.window_minutes))
    effective_rule = rule
    effective_rule.window_minutes = effective_window

    if rule.match_mode == "correlation_key" and not alert.correlation_key:
        return AutoBundleDecision(
            status="skipped",
            reason="no_correlation_key",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )

    alert_observables: list[tuple[str, str]] = []
    if rule.match_mode == "shared_observable":
        from alerts.models import AlertObservable

        alert_observables = list(
            AlertObservable.objects.filter(alert=alert).values_list(
                "observable__data_type__name", "observable__normalized_data"
            )[:5]
        )
        if not alert_observables:
            return AutoBundleDecision(
                status="skipped",
                reason="no_observables",
                mode=rule.match_mode,
                window_minutes=effective_window,
            )

    # Choose a case using the selected mode
    case = find_auto_bundle_case(alert, rule=effective_rule, org_id=org_id)

    if case is None:
        if rule.match_mode == "correlation_key" and alert.correlation_key:
            signal = f"correlation_key='{alert.correlation_key}'"
        elif rule.match_mode == "shared_observable" and alert_observables:
            obs_str = ", ".join(f"{dt}:{val}" for dt, val in alert_observables)
            signal = f"shared observable '{obs_str}'"
        else:
            signal = ""

        return AutoBundleDecision(
            status="skipped",
            reason="no_open_candidate",
            mode=rule.match_mode,
            window_minutes=effective_window,
            signal=signal,
        )

    # ── Inside one transaction.atomic(): re-fetch, re-check, merge, record ──
    from django.db import transaction

    try:
        with transaction.atomic():
            # Re-fetch both rows under lock so a concurrent merge cannot slip through.
            locked_case = Case.objects.select_for_update().get(pk=case.pk)
            fresh_alert = Alert.objects.select_for_update().get(pk=alert.pk)

            if locked_case.closed_date is not None:
                return AutoBundleDecision(
                    status="skipped",
                    reason="closed_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if locked_case.owner_org_id != org_id:
                return AutoBundleDecision(
                    status="skipped",
                    reason="cross_org_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if locked_case.status is None or locked_case.status.stage not in (
                "New",
                "InProgress",
            ):
                return AutoBundleDecision(
                    status="skipped",
                    reason="closed_target_recheck",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if fresh_alert.case_id is not None:
                return AutoBundleDecision(
                    status="skipped",
                    reason="already_linked",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )
            if fresh_alert.owner_org_id != org_id:
                return AutoBundleDecision(
                    status="skipped",
                    reason="tenant_mismatch",
                    mode=rule.match_mode,
                    window_minutes=effective_window,
                )

            merge_alert_into_case(fresh_alert, locked_case, actor=None)

            if rule.match_mode == "correlation_key" and fresh_alert.correlation_key:
                reason_str = (
                    f"correlation_key='{fresh_alert.correlation_key}' "
                    f"within {effective_window} minutes"
                )
                signal_str = reason_str
            elif rule.match_mode == "shared_observable":
                from cases.models import CaseObservable

                obs_list = list(
                    CaseObservable.objects.filter(case=locked_case).values_list(
                        "observable__data_type__name", "observable__normalized_data"
                    )[:3]
                )
                if obs_list:
                    obs_str = ", ".join(f"{dt}:{val}" for dt, val in obs_list)
                    shared_count = (
                        CaseObservable.objects.filter(case=locked_case)
                        .filter(observable__ignore_similarity=False)
                        .count()
                    )
                    reason_str = (
                        f"shared observable '{obs_str}' within {effective_window} minutes "
                        f"(count={shared_count})"
                    )
                else:
                    reason_str = f"shared observable within {effective_window} minutes"
                signal_str = reason_str
            else:
                reason_str = ""
                signal_str = ""

            record_alert_bundled(
                locked_case,
                fresh_alert,
                actor=None,
                reason=reason_str,
                source=AUTO_BUNDLE_SOURCE,
            )

            return AutoBundleDecision(
                status="bundled",
                reason=reason_str,
                mode=rule.match_mode,
                case_id=locked_case.id,
                window_minutes=effective_window,
                signal=signal_str,
            )

    except PermissionDenied:
        # Known refusal - non-fatal to ingest; return skipped without raising
        return AutoBundleDecision(
            status="skipped",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
    except ValidationError:
        # Known validation error - non-fatal to ingest
        return AutoBundleDecision(
            status="skipped",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
    except Exception:
        # Unexpected exception - log, return failed; alert remains stored
        logger.exception("Unexpected error during auto-bundle evaluation")
        return AutoBundleDecision(
            status="failed",
            reason="bundle_failed",
            mode=rule.match_mode,
            window_minutes=effective_window,
        )
