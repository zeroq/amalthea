"""Management command to list discovered playbook scripts and validate playbook configs.

This command:
1. Lists all discovered scripts with their metadata and load errors.
2. Reports any playbooks whose `action_path` is not among the discovered scripts.
3. Exits with non-zero status if any candidate script has a `load_error`
   (so CI catches broken scripts).
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from automation.models import Playbook
from automation.scripts_loader import ScriptInfo, discover_scripts, script_names


class Command(BaseCommand):
    help = "List discovered playbook scripts and validate playbook action_path references."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--format",
            choices=["table", "json"],
            default="table",
            help="Output format (default: table)",
        )
        parser.add_argument(
            "--check-playbooks",
            action="store_true",
            help="Also check playbooks for unknown action_path references",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        fmt: str = options["format"]
        check_playbooks: bool = options["check_playbooks"]

        scripts: dict[str, ScriptInfo] = discover_scripts()
        runnable_names: list[str] = script_names()

        # Exit code: 1 if any script has load_error, 2 if playbook references unknown script
        has_load_errors = False
        has_unknown_references = False

        if fmt == "json":
            output: dict[str, Any] = {
                "scripts": [],
                "runnable_count": len(runnable_names),
                "total_discovered": len(scripts),
            }
            for _name, info in sorted(scripts.items()):
                script_data: dict[str, Any] = {
                    "name": info.name,
                    "path": str(info.path),
                    "description": info.description,
                    "trigger_hint": info.trigger_hint,
                    "load_error": info.load_error,
                    "runnable": info.load_error is None,
                }
                output["scripts"].append(script_data)
                if info.load_error is not None:
                    has_load_errors = True

            if check_playbooks:
                output["playbooks"] = []
                for pb in Playbook.objects.all().order_by("name"):
                    action_path = pb.config.get("action_path") if pb.config else None
                    pb_data: dict[str, Any] = {
                        "name": pb.name,
                        "action_path": action_path,
                        "valid": action_path in runnable_names if action_path else True,
                    }
                    output["playbooks"].append(pb_data)
                    if action_path and action_path not in runnable_names:
                        has_unknown_references = True

            self.stdout.write(json.dumps(output, indent=2))

        else:
            # Table format
            self.stdout.write(self.style.NOTICE("Discovered playbook scripts:"))
            self.stdout.write("")

            # Header
            self.stdout.write(f"{'NAME':<30} {'PATH':<50} {'STATUS':<12} {'ERROR'}")
            self.stdout.write("-" * 120)

            for name, info in sorted(scripts.items()):
                status = (
                    self.style.SUCCESS("OK")
                    if info.load_error is None
                    else self.style.ERROR("LOAD ERROR")
                )
                error_msg = info.load_error or ""
                path_str = str(info.path)
                if len(path_str) > 48:
                    path_str = "..." + path_str[-45:]

                self.stdout.write(f"{name:<30} {path_str:<50} {status:<12} {error_msg}")
                if info.load_error is not None:
                    has_load_errors = True

            self.stdout.write("")
            self.stdout.write(f"Runnable: {len(runnable_names)} / Total discovered: {len(scripts)}")

            if check_playbooks:
                self.stdout.write("")
                self.stdout.write(self.style.NOTICE("Playbook action_path validation:"))
                self.stdout.write("")

                for pb in Playbook.objects.all().order_by("name"):
                    action_path = pb.config.get("action_path") if pb.config else None
                    if action_path:
                        if action_path in runnable_names:
                            status = self.style.SUCCESS("OK")
                        else:
                            status = self.style.ERROR("UNKNOWN SCRIPT")
                            has_unknown_references = True
                        self.stdout.write(f"  {pb.name:<30} {action_path:<30} {status}")
                    else:
                        self.stdout.write(
                            f"  {pb.name:<30} {'(no action_path)':<30} {self.style.WARNING('N/A')}"
                        )

        # Determine exit code
        if has_load_errors:
            self.stderr.write(self.style.ERROR("ERROR: One or more scripts failed to load"))
            raise CommandError("Script load errors detected")
        if has_unknown_references:
            self.stderr.write(
                self.style.ERROR("ERROR: One or more playbooks reference unknown scripts")
            )
            raise CommandError("Unknown script references in playbooks")

        self.stdout.write(self.style.SUCCESS("All checks passed"))
