"""Management command to backfill SSDEEP hashes for existing alerts.

This command computes and stores the `payload_ssdeep` field for alerts that
don't have it populated yet. It is idempotent, batched, and supports dry-run
mode for safe execution.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from alerts.hashing import compute_payload_ssdeep
from alerts.models import Alert
from identity.models import Organisation


class Command(BaseCommand):
    """Backfill SSDEEP fuzzy hashes for alert payloads."""

    help = "Compute and store payload_ssdeep for alerts missing the hash."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of alerts to process per batch (default: 500)",
        )
        parser.add_argument(
            "--org",
            type=str,
            help="Organisation name or UUID to limit backfill to a single org",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute hashes but do not write to database",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recompute hashes for all alerts (including those already populated)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = options["batch_size"]
        org_spec = options["org"]
        dry_run = options["dry_run"]
        force = options["force"]

        if batch_size <= 0:
            raise CommandError("--batch-size must be a positive integer")

        # Build queryset
        qs = Alert.objects.all()

        if org_spec:
            try:
                org = Organisation.objects.get(name=org_spec)
            except Organisation.DoesNotExist:
                try:
                    org = Organisation.objects.get(pk=org_spec)
                except (Organisation.DoesNotExist, ValueError) as exc:
                    raise CommandError(f"Organisation not found: {org_spec}") from exc
            qs = qs.filter(owner_org=org)

        if not force:
            qs = qs.filter(payload_ssdeep="")

        # Order by PK for consistent iteration
        qs = qs.order_by("pk")

        total = qs.count()
        self.stdout.write(f"Total alerts to process: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be written"))

        processed = 0
        updated = 0
        skipped = 0

        # Use iterator to avoid loading all objects into memory
        for alert in qs.iterator(chunk_size=batch_size):
            processed += 1

            # Compute hash
            summary = alert.summary or ""
            payload = alert.raw_payload or {}
            new_hash = compute_payload_ssdeep(summary, payload)

            if new_hash:
                if alert.payload_ssdeep != new_hash:
                    alert.payload_ssdeep = new_hash
                    if not dry_run:
                        alert.save(update_fields=["payload_ssdeep", "updated_at"])
                    updated += 1
                else:
                    skipped += 1
            else:
                # Hash could not be computed (empty, oversize, exception)
                skipped += 1

            # Progress reporting
            if processed % batch_size == 0 or processed == total:
                self.stdout.write(
                    f"  Processed {processed}/{total} — updated: {updated}, skipped: {skipped}"
                )

        # Bulk update would be more efficient but we need to compute per-row
        # For large datasets, we could collect and bulk_update, but individual
        # saves with update_fields are acceptable for this one-time backfill.

        self.stdout.write(self.style.SUCCESS("Backfill complete."))
        self.stdout.write(f"  Processed: {processed}")
        self.stdout.write(f"  Updated:   {updated}")
        self.stdout.write(f"  Skipped:   {skipped}")
