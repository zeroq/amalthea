from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DomainEvent:
    pass


@dataclass
class AlertIngested(DomainEvent):
    alert_id: str
    source_id: str | None = None


@dataclass
class CaseStatusChanged(DomainEvent):
    case_id: str
    old_status: str
    new_status: str


@dataclass
class ObservableCreated(DomainEvent):
    observable_id: str
    case_id: str | None = None


@dataclass
class TaskCompleted(DomainEvent):
    task_id: str
    case_id: str | None = None
