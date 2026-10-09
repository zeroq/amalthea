"""Production settings must fail closed at import time (plan §T1, AC1.1—AC1.3; deviation F9).

`amalthea.settings.prod` raises `ImproperlyConfigured` *while the module is being imported*, so
each case runs in a fresh subprocess: raising inside the pytest process would poison
`sys.modules` for every later test, while a child's exit code and stderr make the refusal
directly observable.

The child environment starts from `os.environ` with the vars under test removed, then applies
per-case overrides. `base.py` runs `load_dotenv(BASE_DIR / ".env", override=False)`, so a var we
export in the child wins over any repo `.env` — but a var we *remove* would be re-filled from a
developer's local `.env`, which `_no_local_env_shadowing` refuses up front (the repository ships
only `.env.example`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_FILE = REPO_ROOT / ".env"

STRONG_KEY = "x" * 64
PLACEHOLDER_KEY = "dev-only-insecure-change-me"

# Import the settings module and, on success, print the hardening flags space-separated so the
# parent can assert on them without importing `prod` itself (which would raise, not print).
PROBE = (
    "import amalthea.settings.prod as s; "
    "print(s.SECRET_KEY[:1], s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, "
    "s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, s.SECURE_HSTS_INCLUDE_SUBDOMAINS, "
    "s.SECURE_HSTS_PRELOAD, s.SECURE_CONTENT_TYPE_NOSNIFF, s.SECURE_REFERRER_POLICY, "
    "s.X_FRAME_OPTIONS)"
)


@pytest.fixture(autouse=True)
def _no_local_env_shadowing() -> None:
    """A developer's local `.env` must not feed the vars the negative cases remove.

    `load_dotenv(..., override=False)` means an env var we *export* wins, but a var we *remove*
    would be re-filled from `BASE_DIR/.env`, turning "missing" into "present" and making the
    fail-closed cases untestable. Fail loudly with the cause named instead.
    """
    defined = set(dotenv_values(LOCAL_ENV_FILE))
    leaked = defined & {"DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS"}
    assert not leaked, (
        f"{LOCAL_ENV_FILE} defines {sorted(leaked)}; the fail-closed cases need those vars "
        "absent from the child environment, which a local .env would undo."
    )


def _import_prod(**overrides: str | None) -> subprocess.CompletedProcess[str]:
    """Import `amalthea.settings.prod` in a child process under a controlled environment.

    An override of `None` (the default for both vars under test) leaves that var unset; any
    other value is exported, which — because `base.py` uses `load_dotenv(..., override=False)` —
    also shadows a value from a repo `.env`.
    """
    env = dict(os.environ)
    env.pop("DJANGO_SECRET_KEY", None)
    env.pop("DJANGO_ALLOWED_HOSTS", None)
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    # Fixed literal argv (`sys.executable` + the PROBE constant); nothing in the command line
    # is derived from input, so the S603 "untrusted input" heuristic does not apply.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _assert_refused(result: subprocess.CompletedProcess[str], named: str) -> None:
    """Assert the child refused to import *and* that the refusal names the offending var."""
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"settings.prod imported but should have refused:\n{output}"
    assert named in output, f"the refusal did not mention {named}:\n{output}"


# --- AC1: DJANGO_SECRET_KEY ----------------------------------------------------------


def test_a_missing_secret_key_is_refused() -> None:
    """AC1 — no env var at all: base's public placeholder must never reach production."""
    _assert_refused(_import_prod(), "DJANGO_SECRET_KEY")


def test_the_dev_placeholder_secret_key_is_refused() -> None:
    """AC1 — the sentinel `base.py` defaults to is rejected even when set deliberately."""
    _assert_refused(_import_prod(DJANGO_SECRET_KEY=PLACEHOLDER_KEY), "DJANGO_SECRET_KEY")


def test_a_49_character_secret_key_is_refused() -> None:
    """AC1 — one character below the 50-character floor still fails closed."""
    _assert_refused(_import_prod(DJANGO_SECRET_KEY="x" * 49), "DJANGO_SECRET_KEY")


def test_a_strong_secret_key_with_a_host_imports() -> None:
    """AC1 — a 64-character key plus a host is the accepted configuration."""
    result = _import_prod(DJANGO_SECRET_KEY=STRONG_KEY, DJANGO_ALLOWED_HOSTS="amalthea.example.com")

    assert result.returncode == 0, result.stderr


# --- AC1: DJANGO_ALLOWED_HOSTS -------------------------------------------------------


@pytest.mark.parametrize(
    "hosts",
    [None, "", "   ", ","],
    ids=["unset", "empty", "whitespace-only", "commas-only"],
)
def test_allowed_hosts_without_a_single_host_is_refused(hosts: str | None) -> None:
    """AC1 — a strong key is not enough: prod must know which hosts it serves.

    Every input here filters down to an empty `ALLOWED_HOSTS` list: an unset var (base's
    default is empty in prod), the empty string, a whitespace-only string, and a string whose
    entries are all empty.
    """
    _assert_refused(
        _import_prod(DJANGO_SECRET_KEY=STRONG_KEY, DJANGO_ALLOWED_HOSTS=hosts),
        "DJANGO_ALLOWED_HOSTS",
    )


# --- AC2: hardening flags ------------------------------------------------------------


def test_hardening_flags_are_forced_in_production() -> None:
    """AC2 — cookies, redirects, HSTS, MIME-sniffing, referrer and framing are locked down."""
    result = _import_prod(DJANGO_SECRET_KEY=STRONG_KEY, DJANGO_ALLOWED_HOSTS="amalthea.example.com")

    assert result.returncode == 0, result.stderr
    (
        key_first_char,
        session_cookie_secure,
        csrf_cookie_secure,
        secure_ssl_redirect,
        hsts_seconds,
        hsts_include_subdomains,
        hsts_preload,
        content_type_nosniff,
        referrer_policy,
        x_frame_options,
    ) = result.stdout.split()

    assert key_first_char == "x", "the configured SECRET_KEY was not the one imported"
    assert session_cookie_secure == "True", "SESSION_COOKIE_SECURE is not forced"
    assert csrf_cookie_secure == "True", "CSRF_COOKIE_SECURE is not forced"
    assert secure_ssl_redirect == "True", "SECURE_SSL_REDIRECT is not forced"
    assert hsts_seconds == "31536000", "SECURE_HSTS_SECONDS is not one year"
    assert hsts_include_subdomains == "True", "SECURE_HSTS_INCLUDE_SUBDOMAINS is not forced"
    assert hsts_preload == "True", "SECURE_HSTS_PRELOAD is not forced"
    assert content_type_nosniff == "True", "SECURE_CONTENT_TYPE_NOSNIFF is not forced"
    assert referrer_policy == "same-origin", "SECURE_REFERRER_POLICY is not same-origin"
    assert x_frame_options == "DENY", "X_FRAME_OPTIONS is not DENY"
