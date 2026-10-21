"""Discovery and loading of playbook scripts from the filesystem folder (ADR-011).

All playbook scripts live as individual `.py` files in `PLAYBOOK_SCRIPTS_DIR`
(default: `automation/scripts/`). Each script declares module-level metadata and a
`run(**values) -> str` entry point. The loader discovers, validates, and caches the
runnable set per process; `action_path` in a playbook config names a script *name*
(never a dotted module path), and `load_script` resolves it to the on-disk file.

Security invariants preserved:
- Nothing from a `Playbook` row is imported or evaluated. The row names a script;
  the loader imports a file from a known folder using an explicit path from the scan.
- The folder is deploy-only: the app never writes to `PLAYBOOK_SCRIPTS_DIR` and
  never exposes an upload for it.
- Resolution is strict: the name must match a discovered script exactly; no path
  traversal, no `importlib.import_module` of a DB-supplied string.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from automation.executor import ActionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScriptInfo:
    """Metadata for a discovered script.

    Attributes:
        name: The script's identity (from `NAME` attribute or filename stem).
        path: Absolute path to the script file.
        description: Human-readable description from `DESCRIPTION` attribute.
        trigger_hint: Advisory trigger hint from `TRIGGER_HINT` attribute.
        run: The callable entry point (only present when `load_error` is None).
        load_error: Error message if the script failed to load; None if loaded OK.
    """

    name: str
    path: Path
    description: str
    trigger_hint: str
    run: Callable[..., str] | None
    load_error: str | None


# Module-level cache for per-process discovery
_scripts_cache: dict[str, ScriptInfo] | None = None
_cache_lock = threading.Lock()
_inserted_modules: list[str] = []


def _get_scripts_dir() -> Path:
    """Resolve the scripts directory from settings, with a sensible default."""
    base = getattr(settings, "PLAYBOOK_SCRIPTS_DIR", None)
    if base:
        return Path(base).resolve()
    return (settings.BASE_DIR / "automation" / "scripts").resolve()


def _is_inside_scripts_dir(path: Path, scripts_dir: Path) -> bool:
    """Verify `path` is inside `scripts_dir` (defence against symlink/relative escapes)."""
    try:
        path.resolve().relative_to(scripts_dir.resolve())
        return True
    except ValueError:
        return False


def _load_module_from_path(name: str, path: Path) -> object:
    """Load a module from an explicit file path using importlib."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Insert into sys.modules so relative imports work within the script
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _discover_once() -> dict[str, ScriptInfo]:
    """Perform the actual filesystem scan and module loading (no cache)."""
    global _inserted_modules
    scripts_dir = _get_scripts_dir()
    if not scripts_dir.is_dir():
        logger.warning("Playbook scripts directory does not exist: %s", scripts_dir)
        return {}

    candidates: list[Path] = []
    for entry in scripts_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix != ".py":
            continue
        stem = entry.stem
        if stem.startswith("_"):
            continue
        candidates.append(entry)

    # Deterministic order: sort by filename so first-wins is predictable
    candidates.sort(key=lambda p: p.name)

    discovered: dict[str, ScriptInfo] = {}
    seen_names: dict[str, Path] = {}

    for path in candidates:
        stem = path.stem
        module_name = f"amalthea_playbook_script_{stem}"

        # Verify path is inside the scripts dir (symlink/escape guard)
        if not _is_inside_scripts_dir(path, scripts_dir):
            logger.warning("Script path escapes scripts directory, skipping: %s", path)
            info = ScriptInfo(
                name=stem,
                path=path,
                description="",
                trigger_hint="",
                run=None,
                load_error="path escapes scripts directory",
            )
            discovered[stem] = info
            continue

        try:
            module = _load_module_from_path(module_name, path)
            # Track inserted module for cleanup on refresh
            if module_name not in _inserted_modules:
                _inserted_modules.append(module_name)

            # Require a callable `run`
            run_fn = getattr(module, "run", None)
            if not callable(run_fn):
                raise AttributeError("module has no callable `run`")

            # Identity: NAME attribute if present, else filename stem
            script_name = getattr(module, "NAME", stem)
            description = getattr(module, "DESCRIPTION", "")
            trigger_hint = getattr(module, "TRIGGER_HINT", "")

            # Duplicate identity check (deterministic first-wins)
            if script_name in seen_names:
                first_path = seen_names[script_name]
                logger.warning(
                    "Duplicate script identity %r: %s (first) vs %s (skipped)",
                    script_name,
                    first_path,
                    path,
                )
                # Record the duplicate with load_error but DO NOT overwrite the first
                # The first (valid) entry remains in `discovered`
                duplicate_info = ScriptInfo(
                    name=script_name,
                    path=path,
                    description=description,
                    trigger_hint=trigger_hint,
                    run=None,
                    load_error=f"duplicate identity (first: {first_path.name})",
                )
                # Use a unique key for the duplicate so it's reported but doesn't overwrite
                duplicate_key = f"{script_name}__duplicate__{path.stem}"
                discovered[duplicate_key] = duplicate_info
                continue

            seen_names[script_name] = path
            info = ScriptInfo(
                name=script_name,
                path=path,
                description=description,
                trigger_hint=trigger_hint,
                run=run_fn,
                load_error=None,
            )
            discovered[script_name] = info

        except Exception as exc:
            logger.exception("Failed to load playbook script %s: %s", path, exc)
            info = ScriptInfo(
                name=stem,
                path=path,
                description="",
                trigger_hint="",
                run=None,
                load_error=f"{type(exc).__name__}: {exc}",
            )
            discovered[stem] = info

    return discovered


def discover_scripts(*, refresh: bool = False) -> dict[str, ScriptInfo]:
    """Return the discovered scripts, cached per process.

    Args:
        refresh: If True, bypass the cache and re-scan the filesystem.

    Returns:
        Dict mapping script name -> ScriptInfo. Only scripts with `load_error is None`
        are runnable; others are reported for diagnostics.
    """
    global _scripts_cache, _inserted_modules
    with _cache_lock:
        if _scripts_cache is None or refresh:
            # Clean up previously inserted script modules on refresh (S-02)
            if refresh and _inserted_modules:
                for mod_name in _inserted_modules:
                    sys.modules.pop(mod_name, None)
                _inserted_modules = []
            _scripts_cache = _discover_once()
        return _scripts_cache


def load_script(name: str) -> Callable[..., str]:
    """Load a script by name for execution.

    Args:
        name: The script name (from `NAME` attribute or filename stem).

    Returns:
        The script's `run` callable.

    Raises:
        ActionError: If the script is not discovered, has a load error, or has no `run`.
    """
    scripts = discover_scripts()
    info = scripts.get(name)
    if info is None:
        known = sorted(scripts.keys())
        raise ActionError(f"no discovered script at {name!r}; known: {known}")
    if info.load_error is not None:
        raise ActionError(f"script {name!r} failed to load: {info.load_error}")
    if info.run is None:
        raise ActionError(f"script {name!r} has no callable `run`")
    return info.run


def script_names() -> list[str]:
    """Return sorted list of discovered script names (only loadable ones)."""
    scripts = discover_scripts()
    return sorted(name for name, info in scripts.items() if info.load_error is None)


def reset_cache() -> None:
    """Clear the discovery cache (for tests only)."""
    global _scripts_cache, _inserted_modules
    with _cache_lock:
        for mod_name in _inserted_modules:
            sys.modules.pop(mod_name, None)
        _inserted_modules = []
        _scripts_cache = None
