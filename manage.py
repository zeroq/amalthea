#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    """Run administrative tasks."""
    # REVIEW-2026-10-03 H2: this used to point at the (empty) `amalthea.settings`
    # package, so anything that touches the database — `migrate`, `sqlmigrate`,
    # `dbshell` — died with "settings.DATABASES is improperly configured". `check`
    # and `makemigrations --check` still passed, which is why AC3.1 looked green.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amalthea.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
