"""Reproducible Postgres benchmark for the observable-list COUNT follow-up.

TODO "12.1-fu1 / PLAN-2026-10-18 Q7 deferred because SQLite cannot answer the
question. This command seeds deterministic observables, measures the exact count
and page query shapes used by ``ui.views.observables.observable_list``, and emits
JSON containing timings plus ``EXPLAIN (ANALYZE, BUFFERS)`` plans.

It refuses to run on non-Postgres engines: the whole point is to measure the
planner against a realistic JSON/table scan.

The benchmark logic has been refactored to share ``core.db_benchmark`` helpers
(_count_case, _page_case, _timings, _explain, _node_types) so that the global
search benchmark can use the same measurement primitives.  The JSON output
schema is identical to the pre-refactor version.

Example::

    docker compose up -d postgres
    DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg \\
      .venv/bin/python manage.py benchmark_observable_count --seed \\
      --output /tmp/observable-count-benchmark.json
"""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from core.db_benchmark import measure_count, measure_page
from identity.models import Organisation
from observables.models import Observable, ObservableType
from ui.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

COMMON_MARKER = "benchmark-event"
RARE_MARKER = "rare-needle"


class Command(BaseCommand):
    """Seed and measure the observable-list filtered COUNT workload."""

    help = "Benchmark observable_list COUNT/EXPLAIN plans on Postgres."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--count", type=int, default=100_000)
        parser.add_argument("--batch-size", type=int, default=2_000)
        parser.add_argument("--repeat", type=int, default=10)
        parser.add_argument("--threshold-ms", type=float, default=500.0)
        parser.add_argument("--org-name", default="observable-count-benchmark")
        parser.add_argument("--type-name", default="benchmark-ip")
        parser.add_argument("--seed", action="store_true")
        parser.add_argument("--output", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        count = int(options["count"])
        batch_size = int(options["batch_size"])
        repeat = int(options["repeat"])
        threshold_ms = float(options["threshold_ms"])
        output = options["output"]

        if count <= 0:
            raise CommandError("--count must be a positive integer")
        if batch_size <= 0:
            raise CommandError("--batch-size must be a positive integer")
        if repeat < 3:
            raise CommandError("--repeat must be at least 3")
        if threshold_ms <= 0:
            raise CommandError("--threshold-ms must be positive")
        if connection.vendor != "postgresql":
            raise CommandError(
                "This benchmark is Postgres-only; run with "
                "DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg"
            )

        org, otype = self._targets(str(options["org_name"]), str(options["type_name"]))
        seeded = 0
        if options["seed"]:
            seeded = self._seed(org, otype, count, batch_size)

        base = (
            Observable.objects.select_related("data_type", "owner_org")
            .prefetch_related("case_observables__case")
            .filter(owner_org=org)
        )
        common = base.filter(data__icontains=COMMON_MARKER)
        rare = base.filter(data__icontains=RARE_MARKER)
        by_type = base.filter(data_type=otype)
        common_by_type = common.filter(data_type=otype)

        cases: list[dict[str, Any]] = [
            measure_count("unfiltered_count", base, repeat),
            measure_count("q_common_count", common, repeat),
            measure_count("q_rare_count", rare, repeat),
            measure_count("type_count", by_type, repeat),
            measure_count("q_common_and_type_count", common_by_type, repeat),
            measure_page("q_common_page", common, repeat, DEFAULT_PAGE_SIZE, 1),
            measure_page("q_rare_page", rare, repeat, DEFAULT_PAGE_SIZE, 1),
            measure_page("q_common_page_200", common, repeat, MAX_PAGE_SIZE, 1),
            measure_page("q_common_deep_page", common, repeat, DEFAULT_PAGE_SIZE, 100),
        ]

        slow = [case["name"] for case in cases if float(case["p95_ms"]) > threshold_ms]
        report: dict[str, Any] = {
            "generated_at": timezone.now().isoformat(),
            "settings_module": os.getenv("DJANGO_SETTINGS_MODULE", "unknown"),
            "postgres_version": self._postgres_version(),
            "target_rows": count,
            "existing_rows": base.count(),
            "seeded_rows": seeded,
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
        if output:
            output.write_text(rendered + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote benchmark JSON to {output}"))
        else:
            self.stdout.write(rendered)

    def _targets(self, org_name: str, type_name: str) -> tuple[Organisation, ObservableType]:
        org, _ = Organisation.objects.get_or_create(
            name=org_name, defaults={"description": "Benchmark rows for TODO §12.1-fu1"}
        )
        otype, _ = ObservableType.objects.get_or_create(
            name=type_name,
            defaults={"is_case_sensitive": False, "is_attachment": False},
        )
        return org, otype

    def _seed(self, org: Organisation, otype: ObservableType, target: int, batch_size: int) -> int:
        existing = Observable.objects.filter(
            owner_org=org,
            data_type=otype,
            data__contains=COMMON_MARKER,
        ).count()
        if existing >= target:
            self._analyze()
            self.stdout.write(f"Benchmark rows already present: {existing}; skipping seed.")
            return 0

        created = 0
        for start in range(existing, target, batch_size):
            stop = min(start + batch_size, target)
            batch = []
            for i in range(start, stop):
                value = f"{COMMON_MARKER}-{i:06d}.198.51.100.{i % 254}"
                if i % 1_000 == 0:
                    value = f"{RARE_MARKER}-{i:06d}.{value}"
                batch.append(
                    Observable(
                        owner_org=org,
                        data_type=otype,
                        data=value,
                        normalized_data=value.casefold(),
                        tlp=2,
                        pap=2,
                    )
                )
            Observable.objects.bulk_create(batch, batch_size=batch_size)
            created += len(batch)
            self.stdout.write(f"Seeded {created}/{target - existing} rows", ending="\r")
        self.stdout.write("")
        self._analyze()
        return created

    def _analyze(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE observable")

    def _postgres_version(self) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            row = cursor.fetchone()
        return str(row[0]) if row else "unknown"
