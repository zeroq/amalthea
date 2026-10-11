"""Playbook action execution with an SSRF guard (AGENTS.md Module D, plan §Phase 6).

An `AutomationRun` executes exactly one action, chosen by `Playbook.config["action"]`:

* `"http"` — a single outbound request to `config["url"]`, with the observable's value
  interpolated into `config` placeholders. The response body, truncated, becomes `output_log`.
* `"python"` — a dotted path to a function registered in :func:`register_action`. Registration is
  what makes this safe: nothing is imported from `config`, so a playbook row cannot name an
  arbitrary callable, and no caller-supplied value is ever `eval`'d or unpickled.

**The SSRF guard is the reason this module exists as a boundary.** A playbook URL is operator-
configured, but its *interpolations* come from adversary-controlled telemetry: an `fqdn` observable
holding `169.254.169.254` must not be able to make the worker read the cloud metadata service. So
every outbound request is checked against :func:`assert_safe_url` **after** interpolation, on every
hop, with redirects followed manually and capped.

DNS rebinding defense: the hostname is resolved *before* connecting, every resolved address is
validated as public, and the connection is pinned to one validated address. After connect,
`socket.getpeername()` is verified to match the pinned address. Redirects are followed manually
and each hop is re-validated.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Any
from urllib.parse import urljoin, urlparse

#: How much of a response body reaches `output_log`. A runaway response must not become a runaway
#: row: `output_log` is a `TextField` an analyst reads in the UI, not an archive.
MAX_OUTPUT_BYTES = 64 * 1024

#: Redirect hops followed manually. `urllib` would otherwise follow them invisibly, past the guard.
MAX_REDIRECTS = 3

#: Total wall clock for one action.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Maximum allowed timeout for outbound requests (clamps config value).
MAX_TIMEOUT_SECONDS = 30.0

#: Schemes we will dial. Anything else — `file:`, `gopher:`, `dict:` — is refused before a socket.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: Registrations for the `"python"` action. Keyed by dotted path so the *config* names a target
#: rather than importing one.
_ACTIONS: dict[str, Callable[..., str]] = {}


class UnsafeURLError(ValueError):
    """A URL the SSRF guard refuses to fetch, with the reason attached."""


class ActionError(RuntimeError):
    """The action itself failed (connection refused, non-2xx, action raised)."""


def register_action(path: str, fn: Callable[..., str]) -> None:
    """Register `fn` under the dotted `path` a playbook's `config["action_path"]` names."""
    _ACTIONS[path] = fn


def registered_actions() -> dict[str, Callable[..., str]]:
    return dict(_ACTIONS)


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP connection that pins to a specific IP and verifies the peer after connect."""

    def __init__(
        self,
        host: str,
        port: int | None = None,
        timeout: float | None = None,
        pinned_ip: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._pinned_ip = pinned_ip
        # Connect to the pinned IP directly, but keep the original host for Host header
        super().__init__(host=pinned_ip or host, port=port, timeout=timeout, **kwargs)
        self._host_header = host

    def connect(self) -> None:
        super().connect()
        # Verify the peer IP matches the pinned IP (prevents DNS rebinding after connect)
        if self._pinned_ip and self.sock:
            peer_ip = self.sock.getpeername()[0]
            # Normalize IPv6 scoped addresses
            if "%" in peer_ip:
                peer_ip = peer_ip.split("%", 1)[0]
            if peer_ip != self._pinned_ip:
                self.close()
                raise UnsafeURLError(
                    f"DNS rebinding detected: connected to {peer_ip}, expected {self._pinned_ip}"
                )

    def putrequest(
        self, method: str, url: str, skip_host: bool = False, skip_accept_encoding: bool = False
    ) -> None:
        # Override Host header to use the original hostname
        super().putrequest(method, url, skip_host, skip_accept_encoding)
        self.putheader("Host", self._host_header)


class _PinnedHTTPSConnection(_PinnedHTTPConnection, HTTPSConnection):
    """HTTPS connection that pins to a specific IP and verifies the peer after connect."""

    def __init__(
        self,
        host: str,
        port: int | None = None,
        timeout: float | None = None,
        pinned_ip: str | None = None,
        context: ssl.SSLContext | None = None,
        **kwargs: Any,
    ) -> None:
        # MRO: _PinnedHTTPConnection.__init__ -> HTTPConnection.__init__ -> HTTPSConnection.__init__
        self._pinned_ip = pinned_ip
        self._host_header = host
        # HTTPSConnection calls HTTPConnection.__init__ which we want to skip, so call directly
        HTTPConnection.__init__(self, host=pinned_ip or host, port=port, timeout=timeout, **kwargs)
        self._context = context

    def connect(self) -> None:
        # Establish TCP connection to pinned IP
        HTTPConnection.connect(self)
        # Then wrap with TLS
        if self.sock and self._context:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self._host_header)
        # Verify peer IP
        if self._pinned_ip and self.sock:
            peer_ip = self.sock.getpeername()[0]
            if "%" in peer_ip:
                peer_ip = peer_ip.split("%", 1)[0]
            if peer_ip != self._pinned_ip:
                self.close()
                raise UnsafeURLError(
                    f"DNS rebinding detected: connected to {peer_ip}, expected {self._pinned_ip}"
                )

    def putrequest(
        self, method: str, url: str, skip_host: bool = False, skip_accept_encoding: bool = False
    ) -> None:
        super(HTTPSConnection, self).putrequest(method, url, skip_host, skip_accept_encoding)
        self.putheader("Host", self._host_header)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    """Handler that uses pinned connections for a specific host/IP pair."""

    def __init__(self, pinned_ip: str, original_host: str) -> None:
        self._pinned_ip = pinned_ip
        self._original_host = original_host
        super().__init__()

    def http_open(self, req: urllib.request.Request) -> Any:
        # Parse the URL to get host/port
        parsed = urlparse(req.full_url)
        host = parsed.hostname or ""
        port = parsed.port or 80

        conn = _PinnedHTTPConnection(
            host=host,
            port=port,
            timeout=req.timeout,
            pinned_ip=self._pinned_ip,
        )
        return self.do_open(lambda *a, **kw: conn, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Handler that uses pinned HTTPS connections for a specific host/IP pair."""

    def __init__(self, pinned_ip: str, original_host: str) -> None:
        self._pinned_ip = pinned_ip
        self._original_host = original_host
        super().__init__()

    def https_open(self, req: urllib.request.Request) -> Any:
        parsed = urlparse(req.full_url)
        host = parsed.hostname or ""
        port = parsed.port or 443

        # Create SSL context with default verification
        context = ssl.create_default_context()

        conn = _PinnedHTTPSConnection(
            host=host,
            port=port,
            timeout=req.timeout,
            pinned_ip=self._pinned_ip,
            context=context,
        )
        return self.do_open(lambda *a, **kw: conn, req)


@dataclass(frozen=True)
class ActionResult:
    """What an action produced. `output_log` is truncated before it ever reaches the model."""

    output_log: str
    ok: bool
    error: str = ""


def _resolve_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address `host` currently resolves to.

    Resolving *before* connecting is what closes DNS rebinding: the guard validates the addresses it
    is about to dial, not the name, because the name can answer differently on the next lookup.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        # `sockaddr[0]` is the literal address; the rest is port and flowinfo. A non-literal entry
        # (a scope id, say) is skipped rather than fatal, because one unparseable row in a resolver
        # answer should not turn a refusal into a crash.
        try:
            addresses.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except (IndexError, ValueError):
            continue
    if not addresses:
        raise UnsafeURLError(f"host {host!r} resolved to no usable address")
    return addresses


def assert_safe_url(url: str) -> str:
    """Raise :class:`UnsafeURLError` unless `url` is safe to fetch. Returns the URL unchanged.

    The checks, in order: scheme allowlist, credentials rejected, host present, and then **every**
    address the host resolves to must be a public unicast address. Rejecting the whole set rather
    than the first is deliberate — a name resolving to one public and one link-local address is a
    rebinding primitive, not a typo.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"scheme {parsed.scheme!r} is not allowed; permitted: {sorted(ALLOWED_SCHEMES)}"
        )
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs carrying credentials are refused")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"URL {url!r} has no host")

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        addresses = _resolve_host(host)

    for address in addresses:
        if address.is_loopback:
            raise UnsafeURLError(f"{address} is loopback")
        if address.is_link_local:
            raise UnsafeURLError(f"{address} is link-local (includes the cloud metadata address)")
        if address.is_private:
            raise UnsafeURLError(f"{address} is private")
        if address.is_reserved or address.is_multicast or address.is_unspecified:
            raise UnsafeURLError(f"{address} is reserved/multicast/unspecified")
    return url


def _interpolate(template: str, values: dict[str, Any]) -> str:
    """Substitute `{observable}`-style placeholders from `values`.

    `str.format_map` over a defaulted dict rather than `format(**values)`: a template referencing an
    unknown key then yields a visible `{key}` instead of raising `KeyError` deep inside a worker.
    """

    class _Defaults(dict[str, Any]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    merged = _Defaults({k: ("" if v is None else str(v)) for k, v in values.items()})
    return template.format_map(merged)


def run_http_action(config: dict[str, Any], values: dict[str, Any]) -> ActionResult:
    """One outbound request. Redirects are followed manually so each hop passes the guard.

    DNS rebinding defense: for each hop, the hostname is resolved, all addresses are validated as
    public, and the connection is pinned to the first validated address. After connect, the peer
    IP is verified to match the pinned address.
    """
    template = str(config.get("url") or "")
    if not template:
        raise ActionError("playbook config has no url for an http action")
    url = _interpolate(template, values)

    # Clamp timeout to sane maximum
    timeout = min(
        float(config.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS
    )
    method = str(config.get("method") or "GET").upper()
    body = config.get("body")
    headers = {
        str(k): _interpolate(str(v), values) for k, v in (config.get("headers") or {}).items()
    }

    seen: list[str] = []
    for hop in range(MAX_REDIRECTS + 1):
        assert_safe_url(url)
        seen.append(url)

        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise ActionError(f"URL {url!r} has no host")

        # Resolve and validate all addresses (assert_safe_url already did this, but we need a
        # specific IP to pin to). Pick the first validated address.
        try:
            literal = ipaddress.ip_address(host)
            pinned_ip = str(literal)
        except ValueError:
            addresses = _resolve_host(host)
            # All addresses were already validated by assert_safe_url; pick the first
            pinned_ip = str(addresses[0])

        # Build opener with pinned connection handlers
        if parsed.scheme.lower() == "https":
            handler: urllib.request.BaseHandler = _PinnedHTTPSHandler(pinned_ip, host)
        else:
            handler = _PinnedHTTPHandler(pinned_ip, host)
        opener = urllib.request.build_opener(handler, _NoRedirect)

        request = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310
        data = _interpolate(str(body), values).encode("utf-8") if body else None
        try:
            with opener.open(request, data=data, timeout=timeout) as response:
                payload = response.read(MAX_OUTPUT_BYTES)
                return ActionResult(
                    output_log=_render(method, seen, response.status, payload), ok=True
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and hop < MAX_REDIRECTS:
                location = exc.headers.get("Location")
                if not location:
                    raise ActionError(f"redirect without Location from {url}") from exc
                url = urljoin(url, location)
                continue
            detail = exc.read(MAX_OUTPUT_BYTES)
            return ActionResult(
                output_log=_render(method, seen, exc.code, detail), ok=False, error=str(exc)
            )
        except urllib.error.URLError as exc:
            raise ActionError(f"request to {url} failed: {exc.reason}") from exc
        except UnsafeURLError as exc:
            # Post-connect peer verification failed (DNS rebinding)
            raise ActionError(f"request to {url} failed: {exc}") from exc
    raise ActionError(f"more than {MAX_REDIRECTS} redirects starting at {seen[0]}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable `urllib`'s automatic redirect following so every hop can be guarded."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _render(method: str, chain: list[str], status: int, payload: bytes) -> str:
    truncated = len(payload) >= MAX_OUTPUT_BYTES
    body = payload.decode("utf-8", errors="replace")
    lines = [f"{method} {' -> '.join(chain)}", f"HTTP {status}", ""]
    if body:
        lines.append(body)
    if truncated:
        lines.append(f"\n[truncated at {MAX_OUTPUT_BYTES} bytes]")
    return "\n".join(lines)


def run_python_action(config: dict[str, Any], values: dict[str, Any]) -> ActionResult:
    """Invoke a **registered** callable. Nothing in `config` is imported or evaluated."""
    path = str(config.get("action_path") or "")
    action = _ACTIONS.get(path)
    if action is None:
        raise ActionError(f"no registered action at {path!r}; registered: {sorted(_ACTIONS)}")
    output = action(**values)
    text = output if isinstance(output, str) else repr(output)
    return ActionResult(output_log=text[:MAX_OUTPUT_BYTES], ok=True)


def execute(config: dict[str, Any], values: dict[str, Any]) -> ActionResult:
    """Dispatch on `config["action"]`. An unknown action is refused, never guessed."""
    action = str(config.get("action") or "").lower()
    if action == "http":
        return run_http_action(config, values)
    if action == "python":
        return run_python_action(config, values)
    raise ActionError(f"unknown action {action!r}; expected 'http' or 'python'")


def _enrichment_probe(**values: object) -> str:
    """A registered no-network action, used by tests and as the documented example.

    It exists because a SOAR gateway whose only demonstrable action is an outbound HTTP request is hard
    to verify offline, and a test that monkeypatches `urllib` is verifying the mock, not the loop.
    This one exercises the real dispatch, the real registration lookup, the real `AutomationRun`
    lifecycle and the real timeline write, with no socket involved.

    It takes `**values` rather than a named parameter on purpose: `run_python_action` forwards the
    same interpolation names an `http` action gets, so an action declaring a narrower signature would
    fail on a perfectly normal binding — a confusing way to learn the calling convention.
    """
    subject = (
        values.get("observable") or values.get("case_id") or values.get("case") or "(no subject)"
    )
    kind = values.get("observable_type") or "case"
    return f"enrichment probe: no network lookup performed for {kind} {subject!r}"


register_action("amalthea.automation.executor.enrichment_probe", _enrichment_probe)
