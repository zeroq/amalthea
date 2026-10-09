"""Authoring-time validation for `Playbook.config` (plan §6.1, T2.1).

The executor refuses a bad config when a run executes it (`ActionError` → a `Failed` run in the
ledger). That is the right answer for a playbook whose config rotted *after* it was saved, and the
wrong answer for one being written now: a rejected write is a 400 an analyst can fix, a rejected
run is a failed run nobody is watching. This module is the write-time mirror of
`automation.executor.execute`, and it deliberately **mirrors rather than centralises** — it
validates, it never invokes: nothing here is imported from `config`, the same rule the executor's
registration lookup follows.

`_meta` (see `automation.views.playbook_meta`) is built from the same constants, so the pickers
the UI renders cannot advertise a method or an action the validator would refuse.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from automation.executor import registered_actions

#: Actions `executor.execute` dispatches on. Kept next to the validator so `_meta`, the
#: validator and the executor's own branch on `config["action"]` fail loudly together.
ACTIONS: tuple[str, ...] = ("http", "python")

#: The methods an `http` config may name. The executor passes the string straight to `urllib`,
#: so an unvalidated method would be an arbitrary HTTP verb on an operator-configured host.
HTTP_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

#: Upper bound for `timeoutSeconds`. The executor's own default is 10s (`DEFAULT_TIMEOUT_SECONDS`);
#: a cap stops a playbook from parking a Celery worker on one request for an hour (plan §6.1:
#: "a positive number (capped)").
MAX_TIMEOUT_SECONDS: float = 30.0


def validate_config(config: object) -> dict[str, list[str]]:
    """Validate a `Playbook.config` body.

    Returns ``{}`` when the config is runnable, else a non-empty ``field -> [reasons]`` map in
    the shape `views._bad` renders (wire key names, so the caller does not translate). The
    function never raises: an authoring form that crashes on bad input is a worse bug than the
    bad input.
    """
    if not isinstance(config, Mapping):
        return {"config": ["must be an object"]}

    action = str(config.get("action") or "").lower()
    if not action:
        return {"action": ["required"]}
    if action == "http":
        return _validate_http(config)
    if action == "python":
        return _validate_python(config)
    return {"action": [f"unknown action {action!r}; expected one of {sorted(ACTIONS)}"]}


def _validate_http(config: Mapping[str, Any]) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}

    url = config.get("url")
    if not isinstance(url, str) or not url.strip():
        errors["url"] = ["required"]

    if "method" in config and config["method"] is not None:
        method = str(config["method"]).upper()
        if method not in HTTP_METHODS:
            errors["method"] = [f"must be one of {', '.join(HTTP_METHODS)}"]

    if "headers" in config and config["headers"] is not None:
        headers = config["headers"]
        if not isinstance(headers, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()
        ):
            errors["headers"] = ["must be an object of string keys and string values"]

    if "timeoutSeconds" in config and config["timeoutSeconds"] is not None:
        timeout = config["timeoutSeconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            errors["timeoutSeconds"] = ["must be a number"]
        elif timeout <= 0:
            errors["timeoutSeconds"] = ["must be greater than 0"]
        elif timeout > MAX_TIMEOUT_SECONDS:
            errors["timeoutSeconds"] = [f"must be at most {MAX_TIMEOUT_SECONDS}"]

    if "body" in config and config["body"] is not None and not isinstance(config["body"], str):
        errors["body"] = ["must be a string"]

    return errors


def _validate_python(config: Mapping[str, Any]) -> dict[str, list[str]]:
    path = config.get("action_path")
    if not isinstance(path, str) or not path.strip():
        return {"action_path": ["required"]}
    if path not in registered_actions():
        return {
            "action_path": [
                f"not a registered action path; registered: {sorted(registered_actions())}"
            ]
        }
    return {}
