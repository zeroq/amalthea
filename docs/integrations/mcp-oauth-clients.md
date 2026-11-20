# MCP OAuth 2.1 External Clients

This document guides external (non-CrewAI) clients connecting to Amalthea as an OAuth 2.1
resource server. Amalthea validates JWT access tokens issued by an external identity provider
(IdP) and maps authenticated principals to organisations and service users through explicit
bindings.

## 1. Authentication

### 1.1 Authorization: Bearer header only

All requests to `/mcp*` must include an `Authorization: Bearer <token>` header.
No other authorization scheme is supported.

### 1.2 Required claims

JWT access tokens must satisfy the following claims:

| Claim | Requirement | Description |
|---|---|---|
| `iss` (issuer) | **Required** | Must exactly match `MCP_OAUTH_ISSUER` configured on the Amalthea server. No normalization or substring matching. |
| `aud` (audience) | **Required** | Must exactly equal `MCP_RESOURCE_URL` (= `MCP_BASE_URL` + `/mcp`). |
| `exp` (expiry) | **Required** | Must be in the future, within the configured `MCP_OAUTH_CLOCK_SKEW_SECONDS` (default 30s). |
| `sub` (subject) | **Required** | Non-empty string identifying the agent/service user. |
| `client_id` | **Required** | Non-empty string identifying the OAuth client. |
| `typ` (type) | **Required** | Must be `at+jwt` or `application/at+jwt`. |
| `alg` (algorithm) | **Required** | Must be in the `MCP_OAUTH_JWT_ALGS` allowlist (default `RS256`). Signature must validate against JWKS. |

### 1.3 Token scope handling

- MCP-relevant scopes are those beginning with `mcp:`.
- Unknown `mcp:*` scopes and reserved `mcp:write` / `mcp:admin` reject the token.
- Non-MCP scopes such as `openid` or `profile` are ignored (not rejected).
- Final granted scopes = token scopes ∩ binding `allowed_scopes` ∩ global allowed scopes.
- Viewer service users are capped at `mcp:read` only.

### 1.4 ID token rejection

ID tokens (even if signed by the same issuer) are always rejected. Only access tokens of type
`at+jwt` or `application/at+jwt` are accepted.

## 2. Binding resolution

Authentication resolves the token to a principal through the `McpOAuthBinding` model:

1. Extract `iss`, `client_id`, and `sub` from the JWT.
2. Look up an active `McpOAuthBinding` where `(issuer == iss) AND (client_id == client_id) AND (subject == sub)`.
3. The binding supplies:
   - `organisation_id` — the tenant this principal belongs to.
   - `user_id` — the effective service user ID.
   - `allowed_scopes` — the scopes granted to this binding.
4. The effective principal scopes = token scopes ∩ binding allowed_scopes ∩ global allowed scopes.
5. The `organisation` is **never** taken from the token or tool arguments. It comes only from the active binding.

If no matching active binding is found, the request is rejected with `401 invalid_token`.

## 3. OAuth modes

### 3.1 `oauth` mode

- Only OAuth access tokens are accepted.
- Static bearer credentials ( `McpCredential`) are **rejected** — they cannot authorize any request.
- The OAuth verifier is the sole authentication path.

### 3.2 `static` mode

- Only static bearer credentials (`McpCredential`) are accepted.
- OAuth tokens are rejected.
- The OAuth verifier is not consulted.

### 3.3 `both` mode

- Both OAuth access tokens and static bearer credentials are accepted.
- OAuth tokens are verified via the OAuth verifier.
- Static credentials are verified via the static verifier.
- OAuth use is audited as "legacy static use" in invocation records.
- The `WWW-Authenticate` 401 challenge includes resource metadata.

## 4. RFC 9728 Protected-Resource Metadata

Amalthea publishes metadata at `/.well-known/oauth-protected-resource/mcp` (RFC 9728).

```json
{
  "resource": "https://mcp.example.com/mcp",
  "authorization_servers": ["https://idp.example.com"],
  "scopes_supported": ["mcp:read", "mcp:propose", "mcp:summary"],
  "bearer_methods_supported": ["header"]
}
```

- `resource` exactly equals `MCP_RESOURCE_URL`.
- No fragment or query string in `resource`.
- Omit zero-valued arrays.
- Served only in `oauth` or `both` mode; returns `404` in `static` mode.

## 5. 401 Challenge

Unauthenticated or invalid requests to `/mcp*` return:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp", error="invalid_token"
```

- No token value, claim dump, or exception text in the challenge or response body.
- Static mode 401 does not include `resource_metadata`.
- `both` mode advertises OAuth metadata but logs legacy static-bearer use.

## 6. Token revocation

There is no Amalthea-managed token revocation cache. The external client must manage token
lifetimes (short-lived access tokens) and obtain fresh tokens as needed. If a binding is
disabled/revoked via the Amalthea UI, subsequent requests with tokens bound to that binding
will be rejected with `401 invalid_token`.

## 7. Generic OAuth client flow (pseudocode)

```python
import requests

# Step 1: Acquire access token from IdP (OIDC / RFC 8414)
token_response = requests.post(
    "https://idp.example.com/oauth/token",
    data={
        "grant_type": "client_credentials",
        "client_id": "my-client",
        "client_secret": "my-secret",
        "scope": "mcp:read",
    },
)
access_token = token_response["access_token"]

# Step 2: Connect to Amalthea MCP with bearer header
headers = {"Authorization": f"Bearer {access_token}"}
response = requests.get("https://amalthea.example.com/mcp", headers=headers)

if response.status_code == 401:
    # Token invalid — obtain a fresh one or check binding
    pass
```

## 8. Client requirements

- Must obtain OAuth 2.1 access tokens from an external IdP (OIDC / RFC 8414 compatible).
- Token must include `iss` matching `MCP_OAUTH_ISSUER` and `aud` equal to `MCP_RESOURCE_URL`.
- Must use header-only `Authorization: Bearer <token>` scheme.
- Must handle token expiry and refresh (short-lived access tokens).
- No client secrets are stored in Amalthea.
- No PKCE state is persisted in Amalthea; PKCE is the client/authorization-server concern.