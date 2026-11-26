"""Reproducible Postgres benchmark for Amalthea global search.

Measures the exact count/page SQL shapes used by ``query.search`` at realistic volume
and records ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` evidence.  Postgres-only by
design: SQLite cannot answer the planner question this benchmark exists to settle.

Example::

    DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \
      .venv/bin/python manage.py benchmark_global_search --seed \
      --output /tmp/global-search-benchmark.json
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from cases.numbering import ALLOCATED_MARKER, allocate_case_number, sync_case_number_sequence
from core.db_benchmark import measure_count, measure_page
from identity.models import Organisation, User
from observables.models import Observable, ObservableType
from query.search import (
    SEARCH_MAX_PAGE_SIZE,
    SEARCH_PAGE_SIZE,
    parse_query,
    queryset_for,
)

COMMON_TERM = "benchmark-hipsum"
FILLER_TERM = "benchmark-filler"
RARE_TERM = "rare-needle"
COMMON_EVERY = 100
RARE_EVERY = 1_000


class Command(BaseCommand):
    """Benchmark global-search COUNT/LIMIT shapes on Postgres."""

    help = "Benchmark global-search COUNT/LIMIT shapes on Postgres."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--count", type=int, default=100_000)
        parser.add_argument("--batch-size", type=int, default=2_000)
        parser.add_argument("--repeat", type=int, default=10)
        parser.add_argument("--threshold-ms", type=float, default=500.0)
        parser.add_argument("--org-name", default="global-search-benchmark-v2")
        parser.add_argument("--seed", action="store_true")
        parser.add_argument(
            "--output",
            type=Path,
            default=Path(tempfile.gettempdir()) / "global-search-benchmark.json",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        count = int(options["count"])
        batch_size = int(options["batch_size"])
        repeat = int(options["repeat"])
        threshold_ms = float(options["threshold_ms"])
        output = options["output"]

        if count <= 0 or batch_size <= 0 or repeat < 3 or threshold_ms <= 0:
            raise CommandError(
                "--count/--batch-size must be positive; --repeat >= 3; --threshold-ms > 0"
            )
        if connection.vendor != "postgresql":
            raise CommandError(
                "This benchmark is Postgres-only; run with "
                "DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg"
            )

        org = self._target_org(str(options["org_name"]))
        seeded: dict[str, int] = {}
        if options["seed"]:
            seeded = self._seed(org, count, batch_size)

        user = self._benchmark_user(org)
        number = (
            Case.objects.filter(owner_org=org, title__icontains=COMMON_TERM)
            .values_list("number", flat=True)
            .first()
        )
        if number is None:
            raise CommandError("no benchmark cases found; rerun with --seed")

        common_q = parse_query(COMMON_TERM)
        rare_q = parse_query(RARE_TERM)
        digit_q = parse_query(str(number))

        shapes: list[tuple[str, str, Any]] = [
            ("case_text_count", "count", queryset_for("case", common_q, user)),
            ("case_text_page", "page", queryset_for("case", common_q, user)),
            ("case_text_page_200", "page", queryset_for("case", common_q, user)),
            ("case_text_deep_page", "page", queryset_for("case", common_q, user)),
            ("case_rare_count", "count", queryset_for("case", rare_q, user)),
            ("case_rare_page", "page", queryset_for("case", rare_q, user)),
            ("case_digit_count", "count", queryset_for("case", digit_q, user)),
            ("case_digit_page", "page", queryset_for("case", digit_q, user)),
            ("alert_text_count", "count", queryset_for("alert", common_q, user)),
            ("alert_text_page", "page", queryset_for("alert", common_q, user)),
            ("alert_text_page_200", "page", queryset_for("alert", common_q, user)),
            ("alert_text_deep_page", "page", queryset_for("alert", common_q, user)),
            ("alert_rare_count", "count", queryset_for("alert", rare_q, user)),
            ("alert_rare_page", "page", queryset_for("alert", rare_q, user)),
            ("observable_text_count", "count", queryset_for("observable", common_q, user)),
            ("observable_text_page", "page", queryset_for("observable", common_q, user)),
            ("observable_rare_count", "count", queryset_for("observable", rare_q, user)),
            ("observable_rare_page", "page", queryset_for("observable", rare_q, user)),
        ]

        cases: list[dict[str, Any]] = []
        for name, kind, queryset in shapes:
            if kind == "count":
                cases.append(measure_count(name, queryset, repeat))
            else:
                page_size = SEARCH_MAX_PAGE_SIZE if name.endswith("_page_200") else SEARCH_PAGE_SIZE
                page_number = 10 if name.endswith("deep_page") else 1
                cases.append(measure_page(name, queryset, repeat, page_size, page_number))

        slow = [case["name"] for case in cases if float(case["p95_ms"]) > threshold_ms]
        report: dict[str, Any] = {
            "generated_at": timezone.now().isoformat(),
            "settings_module": os.getenv("DJANGO_SETTINGS_MODULE", "unknown"),
            "postgres_version": self._postgres_version(),
            "target_rows": count,
            "existing_rows": {
                "case": Case.objects.filter(owner_org=org).count(),
                "alert": Alert.objects.filter(owner_org=org).count(),
                "observable": Observable.objects.filter(owner_org=org).count(),
            },
            "seeded_rows": seeded,
            "seed_distribution": {
                "common_every": COMMON_EVERY,
                "rare_every": RARE_EVERY,
                "match_counts": {
                    "case": {
                        "common": Case.objects.filter(
                            owner_org=org, title__icontains=COMMON_TERM
                        ).count(),
                        "rare": Case.objects.filter(
                            owner_org=org, title__icontains=RARE_TERM
                        ).count(),
                    },
                    "alert": {
                        "common": Alert.objects.filter(
                            owner_org=org, title__icontains=COMMON_TERM
                        ).count(),
                        "rare": Alert.objects.filter(
                            owner_org=org, title__icontains=RARE_TERM
                        ).count(),
                    },
                    "observable": {
                        "common": Observable.objects.filter(
                            owner_org=org, data__icontains=COMMON_TERM
                        ).count(),
                        "rare": Observable.objects.filter(
                            owner_org=org, data__icontains=RARE_TERM
                        ).count(),
                    },
                },
            },
            "repeat": repeat,
            "threshold_ms": threshold_ms,
            "acceptance": {
                "decision": "index-candidate" if slow else "accept-no-index",
                "slow_cases": slow,
                "rule": "p95_ms <= threshold_ms for every measured case",
            },
            "cases": cases,
        }
        rendered = json.dumps(report, indent=2, sort_keys=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote benchmark JSON to {output}"))

    def _target_org(self, org_name: str) -> Organisation:
        org, _ = Organisation.objects.get_or_create(
            name=org_name, defaults={"description": "Benchmark rows for §6.16-fu1"}
        )
        return org

    def _benchmark_user(self, org: Organisation) -> User:
        user, created = User.objects.get_or_create(
            username="global-search-benchmark",
            defaults={"org": org, "role": "analyst", "is_active": True},
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        elif user.org_id != org.id:
            # The username is stable across benchmark runs; point it at the org named
            # by this invocation so --org-name v3 does not silently measure v1/v2 rows.
            user.org = org
            user.save(update_fields=["org"])
        return user

    def _seed(self, org: Organisation, target: int, batch_size: int) -> dict[str, int]:
        case_status, _ = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )
        alert_status, _ = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )
        observable_type, _ = ObservableType.objects.get_or_create(
            name="benchmark-observable",
            defaults={"is_case_sensitive": False, "is_attachment": False},
        )
        seeded = {
            "case": self._seed_cases(org, case_status, target, batch_size),
            "alert": self._seed_alerts(org, alert_status, target, batch_size),
            "observable": self._seed_observables(org, observable_type, target, batch_size),
        }
        self._analyze()
        return seeded

    def _seed_cases(
        self, org: Organisation, status: CaseStatus, target: int, batch_size: int
    ) -> int:
        existing = Case.objects.filter(owner_org=org).count()
        if existing >= target:
            return 0
        next_number = allocate_case_number()
        created = 0
        for start in range(existing, target, batch_size):
            batch = []
            for offset, i in enumerate(range(start, min(start + batch_size, target))):
                term = COMMON_TERM if i % COMMON_EVERY == 0 else FILLER_TERM
                marker = f"{RARE_TERM} " if i % RARE_EVERY == 0 else ""
                case = Case(
                    number=next_number + created + offset,
                    owner_org=org,
                    status=status,
                    title=f"{marker}{term} case title {i:08d}",
                    description=f"{marker}{term} case description {i:08d}",
                    summary=f"{marker}{term} case summary {i:08d}",
                )
                # Explicit benchmark numbers are already sequence-issued in one allocation;
                # avoid a setval round trip per row and sync once after the batches.
                setattr(case, ALLOCATED_MARKER, True)
                batch.append(case)
            Case.objects.bulk_create(batch, batch_size=batch_size)
            created += len(batch)
        if created:
            sync_case_number_sequence(next_number + created - 1)
        return created

    def _seed_alerts(
        self, org: Organisation, status: AlertStatus, target: int, batch_size: int
    ) -> int:
        existing = Alert.objects.filter(owner_org=org).count()
        if existing >= target:
            return 0
        created = 0
        for start in range(existing, target, batch_size):
            batch = []
            for i in range(start, min(start + batch_size, target)):
                term = COMMON_TERM if i % COMMON_EVERY == 0 else FILLER_TERM
                marker = f"{RARE_TERM} " if i % RARE_EVERY == 0 else ""
                batch.append(
                    Alert(
                        owner_org=org,
                        status=status,
                        type="benchmark",
                        source="benchmark",
                        source_ref=f"global-search-v2-{i:08d}",
                        title=f"{marker}{term} alert title {i:08d}",
                        description=f"{marker}{term} alert description {i:08d}",
                        summary=f"{marker}{term} alert summary {i:08d}",
                    )
                )
            Alert.objects.bulk_create(batch, batch_size=batch_size)
            created += len(batch)
        return created

    def _seed_observables(
        self, org: Organisation, observable_type: ObservableType, target: int, batch_size: int
    ) -> int:
        existing = Observable.objects.filter(owner_org=org).count()
        if existing >= target:
            return 0
        created = 0
        for start in range(existing, target, batch_size):
            batch = []
            for i in range(start, min(start + batch_size, target)):
                term = COMMON_TERM if i % COMMON_EVERY == 0 else FILLER_TERM
                marker = f"{RARE_TERM}-" if i % RARE_EVERY == 0 else ""
                value = f"{marker}{term}-{i:08d}.198.51.100.{i % 254}"
                batch.append(
                    Observable(
                        owner_org=org,
                        data_type=observable_type,
                        data=value,
                        normalized_data=value.casefold(),
                    )
                )
            Observable.objects.bulk_create(batch, batch_size=batch_size)
            created += len(batch)
        return created

    def _analyze(self) -> None:
        with connection.cursor() as cursor:
            for table in ("case_record", "alert", "observable"):
                cursor.execute(f"ANALYZE {table}")

    def _postgres_version(self) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            row = cursor.fetchone()
        return str(row[0]) if row else "unknown"
