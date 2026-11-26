"""Reproducible Postgres benchmark for auto-bundle candidate selection.

TODO §12.3-fu1 / PLAN-2026-11-26-auto-ingest-bundling.md P6 deferred the index
decision to measurement because SQLite cannot answer a planner question. This
command seeds deterministic alerts, cases and observable links, then measures
the exact candidate SQL shapes used by ``cases.correlation`` and captures
``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` plans through ``core.db_benchmark``.

It refuses non-Postgres engines and writes JSON only under ``/tmp``.

Example::

    docker compose up -d postgres
    DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \\
      .venv/bin/python manage.py benchmark_auto_bundle_candidates --seed \\
      --repeat 10 --output /tmp/auto-bundle-candidates.json
"""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count, Max
from django.utils import timezone

from alerts.models import Alert, AlertObservable, AlertStatus
from cases.correlation import MAX_CORRELATION_OBS
from cases.models import Case, CaseObservable, CaseStatus, CorrelationRule
from core.db_benchmark import measure_page
from identity.models import Organisation
from observables.models import Observable, ObservableType
from ui.pagination import PICKER_PAGE_SIZE

BENCH_KEY = "benchmark:auto-bundle-shared-key"
BENCH_OBSERVABLE = "benchmark:auto-bundle-shared-observable"
BENCH_NOISE_OBSERVABLE = "benchmark:auto-bundle-noise-observable"
HOT_KEY_EVERY = 50
TMP_ROOT = Path(os.sep) / "tmp"


class Command(BaseCommand):
    """Seed and measure the two auto-bundle candidate query shapes."""

    help = "Benchmark auto-bundle candidate SQL/EXPLAIN plans on Postgres."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--alerts", type=int, default=50_000)
        parser.add_argument("--cases", type=int, default=1_000)
        parser.add_argument("--observables", type=int, default=1_000)
        parser.add_argument("--batch-size", type=int, default=2_000)
        parser.add_argument("--repeat", type=int, default=10)
        parser.add_argument("--threshold-ms", type=float, default=500.0)
        parser.add_argument("--org-name", default="auto-bundle-benchmark")
        parser.add_argument("--seed", action="store_true")
        parser.add_argument(
            "--output",
            type=Path,
            default=TMP_ROOT / "auto-bundle-candidate-benchmark.json",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        alerts_target = int(options["alerts"])
        cases_target = int(options["cases"])
        observables_target = int(options["observables"])
        batch_size = int(options["batch_size"])
        repeat = int(options["repeat"])
        threshold_ms = float(options["threshold_ms"])
        output: Path = options["output"]

        if alerts_target <= 0 or cases_target <= 0 or observables_target <= 0 or batch_size <= 0:
            raise CommandError("--alerts, --cases, --observables and --batch-size must be positive")
        if repeat < 3:
            raise CommandError("--repeat must be at least 3")
        if threshold_ms <= 0:
            raise CommandError("--threshold-ms must be positive")
        if connection.vendor != "postgresql":
            raise CommandError(
                "This benchmark is Postgres-only; run with "
                "DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg"
            )

        tmp_root = TMP_ROOT.resolve()
        if output.parent.resolve() != tmp_root or output.suffix != ".json":
            raise CommandError("Benchmark JSON may only be written under /tmp")

        org, status, case_status, rule = self._targets(str(options["org_name"]))
        seeded = (
            self._seed(
                org,
                status,
                case_status,
                alerts_target,
                cases_target,
                observables_target,
                batch_size,
            )
            if options["seed"]
            else {}
        )
        anchor = timezone.now()
        window_start = anchor - timedelta(minutes=rule.window_minutes)

        incoming = (
            Alert.objects.filter(owner_org=org, source_ref=f"{BENCH_KEY}:incoming")
            .order_by("-created_at")
            .first()
        )
        if incoming is None:
            raise CommandError("Benchmark incoming alert is missing; rerun with --seed")

        correlation_key_page = (
            Alert.objects.filter(
                owner_org_id=org.id,
                correlation_key=BENCH_KEY,
                date__gt=window_start,
                date__lte=anchor,
                case__isnull=False,
                case__owner_org_id=org.id,
                case__closed_date__isnull=True,
                case__status__stage__in=("New", "InProgress"),
            )
            .exclude(pk=incoming.pk)
            .select_related("case", "case__status")
            .order_by("-date", "-id")[:PICKER_PAGE_SIZE]
        )

        observable_ids_qs = AlertObservable.objects.filter(alert=incoming).values_list(
            "observable_id", flat=True
        )[:MAX_CORRELATION_OBS]
        # A separate QuerySet instance: measuring a cached one would issue no SQL, and
        # core.db_benchmark deliberately refuses to report a query that never hit the DB.
        observable_ids = list(
            AlertObservable.objects.filter(alert=incoming).values_list("observable_id", flat=True)[
                :MAX_CORRELATION_OBS
            ]
        )
        shared_observable_page = (
            CaseObservable.objects.filter(
                observable_id__in=observable_ids,
                observable__ignore_similarity=False,
                case__owner_org_id=org.id,
                case__closed_date__isnull=True,
                case__status__stage__in=("New", "InProgress"),
                created_at__gt=window_start,
                created_at__lte=anchor,
            )
            .values("case_id")
            .annotate(shared_count=Count("id"), latest_match=Max("created_at"))
            .order_by("-shared_count", "-latest_match", "case_id")[:PICKER_PAGE_SIZE]
        )

        measured: list[dict[str, Any]] = [
            measure_page(
                "correlation_key_candidates", correlation_key_page, repeat, PICKER_PAGE_SIZE, 1
            ),
            measure_page("alert_observable_ids", observable_ids_qs, repeat, MAX_CORRELATION_OBS, 1),
            measure_page(
                "shared_observable_candidates", shared_observable_page, repeat, PICKER_PAGE_SIZE, 1
            ),
        ]

        plan_checks = self._plan_checks(measured)
        slow = [case["name"] for case in measured if float(case["p95_ms"]) > threshold_ms]
        plan_failures = [name for name, passed in plan_checks.items() if not passed]
        report: dict[str, Any] = {
            "generated_at": timezone.now().isoformat(),
            "settings_module": os.getenv("DJANGO_SETTINGS_MODULE", "unknown"),
            "postgres_version": self._postgres_version(),
            "target_alerts": alerts_target,
            "target_cases": cases_target,
            "target_observables": observables_target,
            "hot_key_every": HOT_KEY_EVERY,
            "existing_alerts": Alert.objects.filter(
                owner_org=org,
                source="benchmark",
                source_ref__startswith=f"{BENCH_KEY}:",
            ).count(),
            "existing_hot_key_alerts": Alert.objects.filter(
                owner_org=org, correlation_key=BENCH_KEY
            ).count(),
            "existing_cases": Case.objects.filter(owner_org=org).count(),
            "seeded": seeded,
            "repeat": repeat,
            "threshold_ms": threshold_ms,
            "window_minutes": rule.window_minutes,
            "acceptance": {
                "decision": "index-candidate" if slow or plan_failures else "accept-no-index",
                "slow_cases": slow,
                "plan_failures": plan_failures,
                "rule": (
                    "p95_ms <= threshold_ms for every case and all indexed-seek plan checks pass; "
                    "accepts equivalent Postgres-selected indexes where the named index is not chosen"
                ),
            },
            "plan_checks": plan_checks,
            "cases": measured,
        }

        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote benchmark JSON to {output}"))

    def _targets(
        self, org_name: str
    ) -> tuple[Organisation, AlertStatus, CaseStatus, CorrelationRule]:
        org, _ = Organisation.objects.get_or_create(
            name=org_name, defaults={"description": "Benchmark rows for TODO §12.3-fu1"}
        )
        alert_status, _ = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )
        case_status, _ = CaseStatus.objects.get_or_create(
            value="InProgress", defaults={"stage": "InProgress", "order": 1}
        )
        rule, _ = CorrelationRule.objects.get_or_create(
            organisation=org,
            defaults={
                "enabled": True,
                "match_mode": "correlation_key",
                "window_minutes": 10,
            },
        )
        return org, alert_status, case_status, rule

    def _seed(
        self,
        org: Organisation,
        alert_status: AlertStatus,
        case_status: CaseStatus,
        alerts_target: int,
        cases_target: int,
        observables_target: int,
        batch_size: int,
    ) -> dict[str, int]:
        existing_alerts = Alert.objects.filter(
            owner_org=org,
            source="benchmark",
            source_ref__startswith=f"{BENCH_KEY}:",
        ).count()
        cases = list(Case.objects.filter(owner_org=org).order_by("number")[:cases_target])

        created_cases = 0
        if len(cases) < cases_target:
            case_batch = [
                Case(
                    owner_org=org,
                    status=case_status,
                    title=f"Benchmark case {i:06d}",
                )
                for i in range(len(cases), cases_target)
            ]
            Case.objects.bulk_create(case_batch, batch_size=batch_size)
            created_cases = len(case_batch)
            cases = list(Case.objects.filter(owner_org=org).order_by("number")[:cases_target])

        obs_type, _ = ObservableType.objects.get_or_create(
            name="benchmark-ip", defaults={"is_case_sensitive": False, "is_attachment": False}
        )
        observables = list(
            Observable.objects.filter(
                owner_org=org,
                data_type=obs_type,
                normalized_data__startswith=BENCH_OBSERVABLE,
            ).order_by("normalized_data")[:observables_target]
        )
        created_observables = 0
        if len(observables) < observables_target:
            obs_batch = []
            for i in range(len(observables), observables_target):
                value = f"{BENCH_OBSERVABLE}-{i:06d}"
                obs_batch.append(
                    Observable(
                        owner_org=org,
                        data_type=obs_type,
                        data=value,
                        normalized_data=value,
                        tlp=2,
                        pap=2,
                    )
                )
            Observable.objects.bulk_create(obs_batch, batch_size=batch_size, ignore_conflicts=True)
            created_observables = len(obs_batch)
            observables = list(
                Observable.objects.filter(
                    owner_org=org,
                    data_type=obs_type,
                    normalized_data__startswith=BENCH_OBSERVABLE,
                ).order_by("normalized_data")[:observables_target]
            )

        now = timezone.now()
        incoming, incoming_created = Alert.objects.get_or_create(
            owner_org=org,
            source="benchmark",
            type="benchmark",
            source_ref=f"{BENCH_KEY}:incoming",
            defaults={
                "status": alert_status,
                "title": "Benchmark incoming alert",
                "severity": 2,
                "date": now,
                "correlation_key": BENCH_KEY,
                "raw_payload": {},
            },
        )
        created_incoming = 1 if incoming_created else 0

        existing_case_links = CaseObservable.objects.filter(
            case__in=cases, observable__in=observables
        ).count()
        created_case_links = 0
        if existing_case_links < len(observables):
            case_link_batch = [
                CaseObservable(case=cases[i % len(cases)], observable=observable)
                for i, observable in enumerate(
                    observables[existing_case_links:], start=existing_case_links
                )
            ]
            CaseObservable.objects.bulk_create(
                case_link_batch, batch_size=batch_size, ignore_conflicts=True
            )
            created_case_links = len(case_link_batch)

        existing_alert_links = AlertObservable.objects.filter(
            alert=incoming, observable__in=observables
        ).count()
        created_alert_links = 0
        if existing_alert_links < len(observables):
            alert_link_batch = [
                AlertObservable(alert=incoming, observable=observable)
                for observable in observables[existing_alert_links:]
            ]
            AlertObservable.objects.bulk_create(
                alert_link_batch, batch_size=batch_size, ignore_conflicts=True
            )
            created_alert_links = len(alert_link_batch)

        created_alerts = 0
        if existing_alerts < alerts_target:
            for start in range(existing_alerts, alerts_target, batch_size):
                stop = min(start + batch_size, alerts_target)
                alert_batch = []
                for i in range(start, stop):
                    correlation_key = (
                        BENCH_KEY if i % HOT_KEY_EVERY == 0 else f"benchmark:cold:{i:06d}"
                    )
                    alert_batch.append(
                        Alert(
                            owner_org=org,
                            status=alert_status,
                            type="benchmark",
                            source="benchmark",
                            source_ref=f"{BENCH_KEY}:{i:06d}",
                            title=f"Benchmark alert {i:06d}",
                            severity=2,
                            date=now - timedelta(seconds=i % 600),
                            correlation_key=correlation_key,
                            case=cases[i % len(cases)],
                            raw_payload={},
                        )
                    )
                Alert.objects.bulk_create(alert_batch, batch_size=batch_size)
                created_alerts += len(alert_batch)
                self.stdout.write(
                    f"Seeded {created_alerts}/{alerts_target - existing_alerts} alerts", ending="\r"
                )
            self.stdout.write("")

        noise_observable, _ = Observable.objects.get_or_create(
            owner_org=org,
            data_type=obs_type,
            normalized_data=BENCH_NOISE_OBSERVABLE,
            defaults={"data": BENCH_NOISE_OBSERVABLE, "tlp": 2, "pap": 2},
        )
        benchmark_alert_ids = list(
            Alert.objects.filter(
                owner_org=org,
                source="benchmark",
                source_ref__startswith=f"{BENCH_KEY}:",
            )
            .exclude(pk=incoming.pk)
            .values_list("id", flat=True)
        )
        existing_noise_link_ids = set(
            AlertObservable.objects.filter(
                observable=noise_observable, alert_id__in=benchmark_alert_ids
            ).values_list("alert_id", flat=True)
        )
        noise_link_batch = [
            AlertObservable(alert_id=alert_id, observable=noise_observable)
            for alert_id in benchmark_alert_ids
            if alert_id not in existing_noise_link_ids
        ]
        if noise_link_batch:
            AlertObservable.objects.bulk_create(
                noise_link_batch, batch_size=batch_size, ignore_conflicts=True
            )

        with connection.cursor() as cursor:
            cursor.execute("ANALYZE alert")
            cursor.execute("ANALYZE case_record")
            cursor.execute("ANALYZE case_observable")
            cursor.execute("ANALYZE alert_observable")
            cursor.execute("ANALYZE observable")

        return {
            "cases": created_cases,
            "observables": created_observables,
            "case_observable_links": created_case_links,
            "alert_observable_links": created_alert_links,
            "noise_alert_observable_links": len(noise_link_batch),
            "alerts": created_alerts,
            "incoming_alerts": created_incoming,
        }

    def _plan_checks(self, measured: list[dict[str, Any]]) -> dict[str, bool]:
        plans = {case["name"]: case["plan"] for case in measured}
        corr_nodes = self._nodes(plans["correlation_key_candidates"])
        alert_nodes = self._nodes(plans["alert_observable_ids"])
        shared_nodes = self._nodes(plans["shared_observable_candidates"])
        return {
            "correlation_key_no_alert_seq_scan": not any(
                node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "alert"
                for node in corr_nodes
            ),
            "correlation_key_uses_alert_index_seek": any(
                node.get("Relation Name") == "alert" and node.get("Index Name") is not None
                for node in corr_nodes
            ),
            "shared_observable_uses_case_observable_index": any(
                node.get("Relation Name") == "case_observable"
                and node.get("Index Name") in {"caseobs_obs_case_idx", "uniq_case_observable"}
                for node in shared_nodes
            ),
            "shared_observable_uses_alert_observable_index": any(
                node.get("Index Name") in {"alertobs_obs_alert_idx", "uniq_alert_observable"}
                for node in alert_nodes
            ),
        }

    def _nodes(self, plan: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            found.append(value)
            for child in value.values():
                walk(child)

        walk(plan)
        return found

    def _postgres_version(self) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            row = cursor.fetchone()
        return str(row[0]) if row else "unknown"
