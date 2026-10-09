"""Merge several cases into one (`POST /api/v1/case/_merge/{ids}`, T2 P4).

The wire contract is TheHive's: `POST /api/v1/case/_merge/{id1,id2,...}` and the response is the
resulting case. This module owns the rule; `cases/views.py` owns only the HTTP edge.

The rule is deterministic and replay-safe:

* the **first** case in the list is the target; every later case is folded into it;
* children move by re-parenting the row (alerts, tasks, ledger entries, comments, pages,
  attachments, custom-field values), so their ids survive the merge;
* a value the target already holds is not duplicated: observables are globally deduped already
  (Module C), so a target that already links the same artifact drops the incoming link rather
  than failing the whole merge on `uniq_case_observable`; a share or custom-field value the
  target already carries is likewise kept once;
* each merged-in case writes exactly one **provenance** ledger entry on the target *before* it is
  deleted, so the target's timeline can always answer "where did these artifacts come from?";
* a source already absorbed by an earlier call simply is not in the list — the view resolves ids
  best-effort, so replaying the same request is a no-op that still returns the target
  (AC6.1-P4-b).

The module is deliberately **not** part of the wire boundary (`TODO 2.3`): it reads model
objects and internal snake_case fields, never TheHive field-name literals.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db import transaction

from alerts.models import Alert
from cases.ledger import append_timeline_event
from cases.models import (
    Attachment,
    Case,
    CaseCustomFieldValue,
    Comment,
    Page,
    Share,
    Task,
    TimelineEvent,
)
from identity.models import User


@transaction.atomic
def merge_cases(cases: Sequence[Case], *, actor: User | None) -> Case:
    """Fold every case after the first into the first; return the target.

    Idempotent in the sense that matters on the wire: merging a *single* case (the replay shape,
    where every source has already been absorbed and deleted) is a no-op. The caller decides which
    ids resolved; this function trusts the list.
    """
    target = cases[0]
    for source in cases[1:]:
        if source.pk != target.pk:
            _absorb(target, source, actor=actor)
    return target


def _absorb(target: Case, source: Case, *, actor: User | None) -> None:
    """Move every child of `source` onto `target`, write provenance, delete `source`."""
    Alert.objects.filter(case=source).update(case=target)
    Task.objects.filter(case=source).update(case=target)
    Comment.objects.filter(case=source).update(case=target)
    Page.objects.filter(case=source).update(case=target)
    Attachment.objects.filter(case=source).update(case=target)
    TimelineEvent.objects.filter(case=source).update(case=target)

    # Observables: the target keeps one link per artifact. `get_or_create` on the target link
    # keeps the first `added_by`/`created_at` the target already had; the source link then dies
    # with the source case.
    for link in source.case_observables.all():
        target.case_observables.get_or_create(
            observable_id=link.observable_id,
            defaults={"added_by_id": link.added_by_id},
        )

    # Shares: a share is (case, organisation), so a target that already grants the same org keeps
    # its own row rather than tripping `uniq_share_case_org`.
    for share in source.shares.all():
        Share.objects.get_or_create(
            case=target,
            organisation_id=share.organisation_id,
            defaults={"permissions": share.permissions, "created_by_id": share.created_by_id},
        )

    # Custom-field values: first value wins, same reasoning as observables.
    for value in source.custom_field_values.all():
        CaseCustomFieldValue.objects.get_or_create(
            case=target,
            custom_field_id=value.custom_field_id,
            defaults={"value": value.value, "order": value.order},
        )

    append_timeline_event(
        target,
        title=f"Merged case #{source.number}: {source.title}",
        kind="case-merged",
        actor=actor,
        metadata={
            "merged_case_id": str(source.id),
            "merged_case_number": source.number,
            "merged_case_title": source.title,
        },
    )
    source.delete()


__all__ = ["merge_cases"]
