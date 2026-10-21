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
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db.models import Q

if TYPE_CHECKING:
    from alerts.models import Alert
    from cases.models import Case, TimelineEvent
    from identity.models import User
    from observables.extractor import Finding


# Hard cap on observable IDs fetched before the CaseObservable join (M2).
# Reuses existing cap constants — no new constant introduced.
# 5 MiB payload can hold thousands of small observables; 1000 bounds memory/CPU.
MAX_CORRELATION_OBS = 1000

# Hard cap on candidate unlinked alerts compared for fuzzy similarity (Phase B).
# Candidate set: unlinked + same-org + non-empty hash, ordered -date, sliced here.
# Comparison cost: len(case_hashes) * len(candidates) ppdeep.compare calls.
MAX_FUZZY_COMPARISONS = 500


@dataclass(frozen=True)
class SimilarCase:
    """A case that shares observable artifacts with a source case or alert."""

    case: Case
    shared_count: int
    shared: list[tuple[str, str]]  # ordered by data_type then data


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
        findings: The join key — extracted, normalized artifacts.
        user: The actor whose visibility scopes the query (ADR-004).
        exclude_case: Optional case to exclude from results (e.g., the source case).
        limit: Maximum rows to return (default: RAIL_CAP from ui.pagination).

    Returns:
        (top_limit_rows, total_matching) — total is computed BEFORE limit for honest truncation.

    Scope:
        Candidates restricted to `case_visible_q(user)` (includes own-org + shared cases).
        The redundant `owner_org` filter was removed (M4) — `case_visible_q` is the
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
    # Do NOT filter by owner_org here — shared cases link to observables
    # in the owning org, not the user's org.
    from cases.models import Case, CaseObservable
    from identity.scoping import case_visible_q
    from observables.models import Observable

    q_objects = Q()
    for type_name, normalized in finding_keys.keys():
        q_objects |= Q(data_type__name=type_name, normalized_data=normalized)

    # M2: Bound intermediate results — slice at DB level before fetching IDs
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

    Never raises on empty/garbage/malformed payload — degrades to plain open cases.
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
    # M4: NO redundant owner_org filter — case_visible_q includes shares.
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
        # No extractable signal — return plain open cases (newest first) up to limit
        return [
            SimilarCase(case=c, shared_count=0, shared=[])
            for c in base_qs.order_by("-start_date")[:limit]
        ]

    # Get observable PKs matching these findings across all orgs,
    # then restrict via CaseObservable join to visible cases (M4).
    # Do NOT filter by owner_org here — shared cases link to observables
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
        # No matching observables in any org — return plain open cases
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

    Signals: `payload_ssdeep` hashes of the case's attached alerts (non-empty).
    Candidates: unlinked alerts in the same org with non-empty `payload_ssdeep`.

    Scoring: for each candidate, compute the maximum `ppdeep.compare(candidate_hash, h)`
    over all case alert hashes. Keep candidates with score >= threshold.

    Returns:
        (top_limit_rows, total_matching) — total is computed BEFORE limit for honest truncation.
    """
    from ui.pagination import RAIL_CAP

    if limit is None:
        limit = RAIL_CAP

    # Resolve threshold (global default, overrideable per-call)
    threshold = (
        threshold
        if threshold is not None
        else getattr(settings, "ALERT_FUZZY_SIMILARITY_THRESHOLD", 80)
    )

    # 1. Collect case signal hashes from attached alerts
    from alerts.models import Alert
    from identity.scoping import org_q

    case_alerts = (
        Alert.objects.filter(case=case)
        .exclude(payload_ssdeep="")
        .values_list("payload_ssdeep", flat=True)[:MAX_CORRELATION_OBS]
    )

    case_hashes = [h for h in case_alerts if h]
    if not case_hashes:
        return [], 0

    # 2. Candidates: unlinked, same-org, non-empty hash, ordered by -date, capped
    candidates_qs = (
        Alert.objects.filter(case__isnull=True)
        .filter(org_q("owner_org", user))
        .exclude(payload_ssdeep="")
        .select_related("status", "owner_org")
        .order_by("-date")[:MAX_FUZZY_COMPARISONS]
    )

    # 3. Score each candidate: max ppdeep.compare against case hashes
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

            max_score = 0
            for case_hash in case_hashes:
                try:
                    score = ppdeep.compare(candidate_hash, case_hash)
                    if score > max_score:
                        max_score = score
                except (Exception, RecursionError, MemoryError):  # noqa: S112
                    # Treat comparison failures as score 0
                    continue

            if max_score >= threshold:
                similar_alerts.append(SimilarAlert(alert=alert, similarity=max_score))

    # 4. Rank: similarity desc, then -date
    similar_alerts.sort(key=lambda sa: (-sa.similarity, -sa.alert.date.timestamp()))

    total_matching = len(similar_alerts)
    return similar_alerts[:limit], total_matching


def record_alert_bundled(
    case: Case,
    alert: Alert,
    *,
    actor: User,
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
    from django.utils import timezone

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
    # linked the alert, and we're now recording the bundle — we must not record twice.)
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

    # Ledger entry
    event = TimelineEvent.objects.create(
        case=case,
        date=timezone.now(),
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
