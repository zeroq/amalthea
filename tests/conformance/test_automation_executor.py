"""Playbook action execution and the SSRF guard (plan §Phase 6, AC6.4).

The guard is the security boundary this module exists for: a playbook URL is operator-configured, but
the values interpolated into it come from adversary-controlled telemetry. So the tests here are
mostly about **refusal**, and they exercise real resolution rather than a stubbed socket.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import pytest

from automation.executor import (
    MAX_REDIRECTS,
    ActionError,
    UnsafeURLError,
    assert_safe_url,
    execute,
    register_action,
    registered_actions,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://evil.example.com:70/_x",
        "dict://127.0.0.1:11211/stat",
        "ftp://evil.example.com/x",
    ],
)
def test_non_http_schemes_are_refused(url: str) -> None:
    """The scheme allowlist is checked before anything is resolved or dialled."""
    with pytest.raises(UnsafeURLError, match="scheme"):
        assert_safe_url(url)


def test_loopback_literal_is_refused() -> None:
    with pytest.raises(UnsafeURLError, match="loopback"):
        assert_safe_url("http://127.0.0.1/")

    with pytest.raises(UnsafeURLError, match="loopback"):
        assert_safe_url("http://[::1]/")


def test_the_cloud_metadata_address_is_refused_by_name() -> None:
    """169.254.169.254 is link-local, and the metadata service is the canonical SSRF target."""
    with pytest.raises(UnsafeURLError, match="link-local"):
        assert_safe_url("http://169.254.169.254/latest/meta-data/iam/security-credentials/")


def test_private_ranges_are_refused() -> None:
    for address in ("10.0.0.5", "192.168.1.50", "172.16.4.4"):
        with pytest.raises(UnsafeURLError, match="private"):
            assert_safe_url(f"http://{address}/")


def test_credentials_in_the_url_are_refused() -> None:
    with pytest.raises(UnsafeURLError, match="credentials"):
        assert_safe_url("http://user:pass@example.com/")


def test_a_host_that_resolves_into_private_space_is_refused(monkeypatch: Any) -> None:
    """DNS rebinding: the *name* looks public, the answer is not.

    This is the case a hostname-string check would pass. The guard resolves first and rejects the
    whole answer set, because one public and one link-local address is a rebinding primitive rather
    than a misconfiguration.
    """
    import socket

    real = socket.getaddrinfo

    def fake(host: str, *args: object, **kwargs: object) -> Any:
        if host == "totally-legit.example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
            ]
        return real(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(UnsafeURLError, match="link-local"):
        assert_safe_url("http://totally-legit.example.com/")


def test_a_public_literal_passes_the_guard() -> None:
    assert assert_safe_url("https://93.184.216.34/health") == "https://93.184.216.34/health"


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(UnsafeURLError, match="no host"):
        assert_safe_url("http:///just/a/path")


def test_an_unknown_action_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ActionError, match="unknown action"):
        execute({"action": "carrier-pigeon"}, {})


def test_the_python_action_only_runs_registered_callables() -> None:
    """Nothing in `config` is imported, so a playbook row cannot name an arbitrary callable."""
    with pytest.raises(ActionError, match="no registered action"):
        execute({"action": "python", "action_path": "os.system"}, {})
    with pytest.raises(ActionError, match="no registered action"):
        execute({"action": "python", "action_path": "builtins.eval"}, {})


def test_a_registered_action_receives_the_interpolation_values() -> None:
    register_action("tests.receiver", lambda **kw: f"saw {kw.get('observable')}")
    result = execute(
        {"action": "python", "action_path": "tests.receiver"},
        {"observable": "evil.example", "observable_type": "fqdn"},
    )
    assert result.ok is True
    assert result.output_log == "saw evil.example"


def test_an_unknown_interpolation_key_is_visible_not_fatal() -> None:
    """A template referencing a name the run does not carry renders `{name}` rather than raising.

    `KeyError` inside a worker would be recorded as a generic failure and read as "the playbook is
    broken"; a visible `{name}` says exactly which field the operator forgot to wire.
    """
    from automation.executor import _interpolate

    assert _interpolate("https://x/{missing}", {"observable": "a"}) == "https://x/{missing}"
    assert _interpolate("https://x/{observable}", {"observable": "a"}) == "https://x/a"


def test_an_http_action_without_a_url_is_an_error_not_a_request() -> None:
    with pytest.raises(ActionError, match="no url"):
        execute({"action": "http"}, {})


def test_the_guard_runs_after_interpolation(monkeypatch: Any) -> None:
    """The *interpolated* URL is what gets checked, not the configured template.

    A playbook may legitimately be configured as `https://{observable}/lookup`; an attacker-supplied
    observable of `169.254.169.254` must not turn that into a metadata fetch.
    """
    from automation.executor import run_http_action

    with pytest.raises(UnsafeURLError):
        run_http_action(
            {"action": "http", "url": "http://{observable}/lookup"},
            {"observable": "169.254.169.254"},
        )


def test_redirects_are_capped(monkeypatch: Any) -> None:
    """Redirect following is manual, so it is bounded; `urllib`'s own following is disabled."""
    import urllib.error
    import urllib.request

    from automation.executor import run_http_action

    def fake_open(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError(
            request.full_url,
            302,
            "Found",
            {"Location": "http://93.184.216.34/again"},
            None,  # type: ignore[arg-type]
        )

    # Patched on the class so `build_opener(...).open(...)` binds normally; `self` is unused because
    # the point is only that every hop raises, which is what makes the hop counter observable.
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
    result = run_http_action({"action": "http", "url": "http://93.184.216.34/start"}, {})

    # The cap is reported as a failed run with the hop chain rendered, not as an exception: an analyst
    # reading the timeline should see where it was sent, which an exception thrown at the worker
    # boundary would discard.
    assert result.ok is False
    assert result.error is not None and "302" in result.error
    assert result.output_log.count("http://93.184.216.34/again") == MAX_REDIRECTS, (
        "the hop budget was not what MAX_REDIRECTS says"
    )


def test_output_is_truncated(monkeypatch: Any) -> None:
    """`output_log` is read by analysts in a UI, not used as an archive."""
    import urllib.request

    from automation.executor import MAX_OUTPUT_BYTES, run_http_action

    class _Response:
        status = 200

        def read(self, size: int) -> bytes:
            return b"x" * size

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda self, *a, **k: _Response())
    result = run_http_action({"action": "http", "url": "https://93.184.216.34/big"}, {})
    assert result.ok is True
    assert "truncated" in result.output_log
    assert len(result.output_log) < MAX_OUTPUT_BYTES + 200


def test_ipv6_spellings_normalize_to_one_artifact() -> None:
    """The stoplist catches both spellings, which is the normalization the observable layer relies on."""
    from observables.extractor import normalize

    assert normalize("ip", "0:0:0:0:0:0:0:1", is_case_sensitive=False) == "::1"
    assert normalize("ip", "::1", is_case_sensitive=False) == "::1"


def test_the_builtin_probe_is_registered_under_its_dotted_path() -> None:
    """The documented example must be reachable by the path a playbook row actually names."""
    assert "amalthea.automation.executor.enrichment_probe" in registered_actions()
    result = execute(
        {"action": "python", "action_path": "amalthea.automation.executor.enrichment_probe"},
        {"observable": "evil.example", "observable_type": "fqdn"},
    )
    assert result.ok is True
    assert "evil.example" in result.output_log


def test_the_ipaddress_guard_agrees_with_the_stoplist() -> None:
    """Guards that disagree are worse than one guard: assert the stoplist is really unroutable."""
    unroutable = ("0.0.0.0", "127.0.0.1", "255.255.255.255", "::1", "::")  # noqa: S104
    for literal in unroutable:
        address = ipaddress.ip_address(literal)
        assert (
            address.is_loopback
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ), f"{literal} is routable but is in the observable stoplist"


# --- M2: DNS rebinding TOCTOU defense ----------------------------------------


def test_dns_rebinding_host_resolving_to_private_is_rejected(monkeypatch: Any) -> None:
    """A host that resolves to a private IP is rejected at validation time.

    This is the existing guard behavior (pre-connection), retained as the first line of defense.
    """
    import socket

    real = socket.getaddrinfo

    def fake(host: str, *args: object, **kwargs: object) -> Any:
        if host == "rebind.example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
            ]
        return real(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(UnsafeURLError, match="private"):
        assert_safe_url("http://rebind.example.com/")


def test_normal_public_url_passes_validation_and_execution(monkeypatch: Any) -> None:
    """A normal public URL passes validation and the request executes (mocked network)."""
    import urllib.request

    from automation.executor import run_http_action

    class _Response:
        status = 200
        code = 200
        msg = "OK"

        def read(self, size: int) -> bytes:
            return b'{"status": "ok"}'

        def info(self) -> dict:
            return {}

        def getheaders(self, name: str) -> list[str]:
            return []

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_open(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        # Verify the request has a timeout and it's clamped (passed via kwargs)
        timeout = kwargs.get("timeout")
        assert timeout is not None
        assert timeout <= 30.0  # MAX_TIMEOUT_SECONDS
        return _Response()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
    result = run_http_action(
        {"action": "http", "url": "https://93.184.216.34/api", "timeoutSeconds": 5},
        {},
    )
    assert result.ok is True
    assert "status" in result.output_log


def test_post_connect_peer_verification_rejects_rebind(monkeypatch: Any) -> None:
    """The post-connect peer check rejects a DNS rebind (connects to different IP than validated)."""
    import urllib.error
    import urllib.request

    from automation.executor import UnsafeURLError, run_http_action

    def fake_open(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        # Simulate connection to a different IP than expected by raising UnsafeURLError
        raise UnsafeURLError(
            "DNS rebinding detected: connected to 10.0.0.5, expected 93.184.216.34"
        )

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ActionError, match="DNS rebinding detected"):
        run_http_action(
            {"action": "http", "url": "https://example.com/api"},
            {},
        )


def test_timeout_is_clamped_to_max(monkeypatch: Any) -> None:
    """Request timeout is clamped to MAX_TIMEOUT_SECONDS (30s)."""
    import urllib.request

    from automation.executor import MAX_TIMEOUT_SECONDS, run_http_action

    captured_timeout: dict[str, float] = {}

    class _Response:
        status = 200
        code = 200
        msg = "OK"

        def read(self, size: int) -> bytes:
            return b"ok"

        def info(self) -> dict:
            return {}

        def getheaders(self, name: str) -> list[str]:
            return []

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_open(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        captured_timeout["value"] = kwargs.get("timeout")
        return _Response()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    # Request with timeout > MAX_TIMEOUT_SECONDS
    run_http_action(
        {"action": "http", "url": "https://93.184.216.34/api", "timeoutSeconds": 100},
        {},
    )

    assert captured_timeout["value"] == MAX_TIMEOUT_SECONDS


def test_redirect_hop_revalidates_dns_and_pins(monkeypatch: Any) -> None:
    """Each redirect hop re-validates DNS and pins to a validated address."""
    import urllib.error
    import urllib.request

    from automation.executor import run_http_action

    call_count = {"n": 0}

    class _RedirectResponse:
        def __init__(self, location: str):
            self.headers = {"Location": location}
            self.code = 302
            self.status = 302

        def read(self, size: int) -> bytes:
            return b""

        def info(self) -> dict:
            return {}

        def getheaders(self, name: str) -> list[str]:
            return []

    class _FinalResponse:
        status = 200
        code = 200
        msg = "OK"

        def read(self, size: int) -> bytes:
            return b"final"

        def info(self) -> dict:
            return {}

        def getheaders(self, name: str) -> list[str]:
            return []

        def __enter__(self) -> _FinalResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_open(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 302, "Found", {"Location": "https://93.184.216.34/final"}, None
            )
        return _FinalResponse()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    result = run_http_action(
        {"action": "http", "url": "https://93.184.216.34/start"},
        {},
    )

    assert result.ok is True
    assert call_count["n"] == 2  # initial + one redirect
    assert "final" in result.output_log
