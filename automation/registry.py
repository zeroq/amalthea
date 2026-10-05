"""Registered playbook bindings and the signals that start the loop.

`Playbook` is DB-backed (ADR-002 §D4), so bindings are **data**, not code: adding an action to a case
file does not need a migration or a deploy. :func:`playbooks_for` is the read side — resolving which
playbooks a trigger reaches, in a deterministic order — kept separate from `dispatcher` so resolving
a binding is testable without a queue.

## Why the receivers are shaped the way they are

* **`created` only, on `Observable` and `Alert`.** Re-saving an existing row is an analyst edit, and
  firing automation on every edit would re-run enrichment on every keystroke that touched a message
  field.
* **`pre_save` + `post_save` on `Case`.** A status *transition* is the event AGENTS.md names, and
  `post_save` alone cannot see one: by the time it runs, the database already holds the new stage, so
  comparing against the database would compare the new value with itself. The old stage is captured
  in `pre_save` and compared in `post_save`.
* **Dispatch after the link exists.** `Observable.save()` fires before `extract_into_case` writes the
  `CaseObservable` row, so the observable has no case at `post_save` time. Escalation therefore
  dispatches explicitly once the link is written, and this receiver covers observables an analyst adds
  by hand afterwards — which do already have their links.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from alerts.models import Alert
from automation.models import Playbook
from cases.models import Case
from observables.models import Observable

#: Attribute the previous stage is stashed on between `pre_save` and `post_save`.
_PREVIOUS_STAGE_ATTR = "_previous_stage"


def playbooks_for(event_name: str, *, only_active: bool = True) -> list[Playbook]:
    """The playbooks bound to `event_name`, in a deterministic order.

    Ordered by `name` rather than left to the database: two playbooks bound to the same trigger must
    fire in the same sequence on every replay, or the ledger's ordering is not reproducible.
    """
    queryset = Playbook.objects.filter(trigger_event=event_name)
    if only_active:
        queryset = queryset.filter(is_active=True)
    return list(queryset.order_by("name"))


@receiver(pre_save, sender=Case)
def _capture_previous_case_stage(sender: type[Case], instance: Case, **kwargs: Any) -> None:
    """Record the persisted stage before it is overwritten."""
    if instance._state.adding or not instance.status_id:
        setattr(instance, _PREVIOUS_STAGE_ATTR, None)
        return
    setattr(
        instance,
        _PREVIOUS_STAGE_ATTR,
        Case.objects.filter(pk=instance.pk).values_list("status__stage", flat=True).first(),
    )


@receiver(post_save, sender=Case)
def case_saved(sender: type[Case], instance: Case, created: bool, **kwargs: Any) -> None:
    """Fire `case.status_changed` on a real transition only."""
    if created or not instance.status_id:
        return
    previous = getattr(instance, _PREVIOUS_STAGE_ATTR, None)
    current = instance.status.stage
    if previous is None or previous == current:
        return
    from automation.dispatcher import dispatch
    from core.events import CaseStatusChanged

    dispatch(CaseStatusChanged(case_id=str(instance.pk), old_status=previous, new_status=current))


@receiver(post_save, sender=Observable)
def observable_saved(
    sender: type[Observable], instance: Observable, created: bool, **kwargs: Any
) -> None:
    """Fire `observable.created` for a new artifact that is already linked to a case."""
    if not created:
        return
    case_ids = [str(pk) for pk in instance.case_observables.values_list("case_id", flat=True)]
    if not case_ids:
        return
    from automation.dispatcher import dispatch
    from core.events import ObservableCreated

    for case_id in case_ids:
        dispatch(ObservableCreated(observable_id=str(instance.pk), case_id=case_id))


@receiver(post_save, sender=Alert)
def alert_saved(sender: type[Alert], instance: Alert, created: bool, **kwargs: Any) -> None:
    """Fire `alert.ingested` for a newly ingested alert."""
    if not created:
        return
    from automation.dispatcher import dispatch
    from core.events import AlertIngested

    dispatch(
        AlertIngested(
            alert_id=str(instance.pk),
            source_id=str(instance.ingestion_source_id) if instance.ingestion_source_id else None,
        )
    )


def dispatch_observable_linked(observable: Observable, case_id: UUID | str) -> None:
    """Fire `observable.created` once an observable has been linked to a case.

    Called by `observables.extractor.extract_into_case` *after* the `CaseObservable` row exists. The
    `post_save` receiver cannot cover this case (the observable predates the link), so escalation
    calls this explicitly — which is also why the loop fires once per link rather than once per
    observable when one artifact is added to several cases.
    """
    from automation.dispatcher import dispatch
    from core.events import ObservableCreated

    dispatch(ObservableCreated(observable_id=str(observable.pk), case_id=str(case_id)))
