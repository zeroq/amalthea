"""Playbook configuration parsing helpers for the automation UI."""

from __future__ import annotations

from typing import Any

from automation.playbooks import HTTP_METHODS
from automation.scripts_loader import discover_scripts, script_names

from ..common import MAX_HEADERS


def _parse_playbook_config(post: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate playbook action config from POST data.

    Returns (config_dict, error_message). If error_message is not None, config_dict is None.
    Mirrors the validation logic from playbook_create/playbook_detail so a GET→POST round-trip
    produces an identical config (including header order).
    """
    from automation.playbooks import MAX_TIMEOUT_SECONDS

    action = (post.get("action") or "").lower()
    if action not in ("http", "python"):
        return None, "Action must be 'http' or 'python'."

    config: dict[str, Any] = {"action": action}

    if action == "http":
        url = (post.get("url") or "").strip()
        if not url:
            return None, "URL is required for HTTP actions."
        config["url"] = url

        method = (post.get("http_method") or "GET").upper()
        if method not in HTTP_METHODS:
            return None, "Invalid HTTP method."
        config["method"] = method

        timeout = post.get("timeout_seconds")
        if timeout:
            try:
                timeout_val = float(timeout)
                if timeout_val <= 0 or timeout_val > MAX_TIMEOUT_SECONDS:
                    raise ValueError
                config["timeoutSeconds"] = timeout_val
            except ValueError:
                return None, f"Timeout must be a positive number <= {MAX_TIMEOUT_SECONDS}."

        # Headers: preserve order by iterating header_key_<i> in index order
        headers = {}
        header_count = post.get("header_count")
        try:
            max_idx = int(header_count) if header_count else 4
        except (TypeError, ValueError):
            max_idx = 4
        # Bound the loop: header_count is attacker-controlled and drives a CPU-bound range(),
        # so an unbounded value is an authenticated DoS. Reject rather than silently clamp.
        if not 0 <= max_idx <= MAX_HEADERS:
            return None, f"Too many headers (max {MAX_HEADERS})."
        for i in range(max_idx):
            key_field = f"header_key_{i}"
            val_field = f"header_val_{i}"
            if post.get(key_field):
                headers[post[key_field]] = post.get(val_field, "")
        if headers:
            config["headers"] = headers

        body = post.get("http_body")
        if body:
            config["body"] = body

    else:  # python
        action_path = (post.get("action_path") or "").strip()
        if not action_path:
            return None, "Action path is required for Python actions."
        known_scripts = script_names()
        if action_path not in known_scripts:
            return (
                None,
                f"Unknown script. Discovered: {', '.join(sorted(known_scripts))}",
            )
        config["action_path"] = action_path

    return config, None


def _get_script_source(script_name: str) -> str | None:
    """Safely retrieve the source code of a discovered script for read-only display.

    Returns the source as a string, or None if the script is not found or has a load error.
    Never executes the script; uses inspect.getsource on the loaded module.
    """
    import inspect

    scripts = discover_scripts()
    info = scripts.get(script_name)
    if info is None or info.load_error is not None or info.run is None:
        return None
    try:
        return inspect.getsource(info.run)
    except (OSError, TypeError):
        # Could be a built-in function or source not available
        return None
