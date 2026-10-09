# PLAN-2026-10-08 — Production secret hardening + repo-wide secret-hygiene guard

Date: 2026-10-08 · Status: **implemented** · Owner: planner
Tracking: TODO §1 (hygiene) · deviations register **F9** (was deferred) · master plan §13 Phase 13

## Summary

Two coupled changes, prompted by publishing `zeroq/amalthea`:

1. **Fix F9** — `amalthea/settings/prod.py` currently inherits `SECRET_KEY` from `base.py`
   (`os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-change-me")`) and never forces secure
   cookies. A production deploy that forgets `DJANGO_SECRET_KEY` silently runs on a **public
   placeholder** and downgrades cookie security. Make prod **fail closed**.
2. **Secret-hygiene guard** — make "no sensitive information is ever pushed" an enforced property,
   not a promise: a deterministic local secret scanner wired into the git hooks, a full-history
   scanner in CI, and a written policy (`SECURITY.md` + a one-line rule in `AGENTS.md`).

Both are small, but they touch the DoD (hooks + CI) and the project spec, so they get a plan and
an independent verifier pass.

## Goals
- G1 — `amalthea.settings.prod` refuses to import when `DJANGO_SECRET_KEY` is missing, the dev
  placeholder, or shorter than 50 chars; and refuses an empty `DJANGO_ALLOWED_HOSTS`.
- G2 — `prod.py` forces cookie/transport hardening (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  `SECURE_SSL_REDIRECT`, HSTS, `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS=DENY`).
- G3 — A committed, runnable secret scanner (`scripts/secret-scan.sh`) runs on **every** commit
  (staged files, even docs-only) and on **every** push (all tracked files); CI scans the **full
  history**.
- G4 — The "never commit secrets" policy is documented where contributors will see it:
  `SECURITY.md`, `README.md`, `AGENTS.md`, and the existing `.env.example` note.
- G5 — No regression: the existing gate stays green (`make check`, Postgres suite, coverage).

## Non-goals
- No credential storage / no interactive auth changes (user declined persistence).
- No third-party SaaS scanner dependency in the repo's Python closure; the CI history scan pins a
  GitHub Action by SHA, consistent with the repo's supply-chain stance.
- No change to `dev.py`/`test.py`/`test_pg.py` behaviour beyond what G1–G2 require.
- Not addressing **F2** (org-scoping / global observable mutation) — separate wave.

## Architecture / design

### 1. `amalthea/settings/prod.py` (fail-closed)
```python
import os
from django.core.exceptions import ImproperlyConfigured
from .base import *

DEBUG = False

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 50 or SECRET_KEY == "dev-only-insecure-change-me":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong, unique value (>=50 chars) in production. "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'."
    )

ALLOWED_HOSTS = [h for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list at least one host in production.")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# ... existing DATABASES / CHANNEL_LAYERS unchanged
```
Rationale for reading the env var *again* rather than validating base's `SECRET_KEY`: base's
fallback is what makes the current gap invisible; re-deriving makes prod's contract explicit and
keeps the sentinel string in one obvious place.

### 2. `tests/conformance/test_prod_settings.py`
Import the module in a subprocess (so a raised `ImproperlyConfigured` is observable and doesn't
poison the test process), asserting:
- missing / placeholder / short key ⇒ non-zero exit and the message names `DJANGO_SECRET_KEY`;
- empty `DJANGO_ALLOWED_HOSTS` ⇒ non-zero exit;
- strong key + a host ⇒ imports, and the hardening flags above are all `True`/expected values.

### 3. `scripts/secret-scan.sh`
- Modes: `--staged` (pre-commit; scans the **git index** via `git diff --cached` + `git grep --cached`),
  `--tracked` (pre-push; scans the **HEAD tree**, `git ls-tree -r HEAD` + `git grep HEAD`), `--history`
  (every blob in `git rev-list --all`, plus commit and annotated-tag **messages**), `--files` (manual,
  worktree paths). Scanning git objects rather than the worktree is deliberate: a staged-then-modified
  secret, or `git add -f .env && rm .env`, must still be caught.
- Pattern set: GitHub (`github_pat_`, `ghp_/gho_/ghu_/ghs_/ghr_`), AWS `AKIA…`, Slack `xox*`,
  Google `AIza…`, Stripe `sk_live_/rk_live_`, PEM `-----BEGIN … PRIVATE KEY-----`, and
  credential-bearing URLs (a URL that embeds a username and password before the host). Filename guard
  for `.env`, `.env.*`, `*.env`, `*.envrc`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519` (`.env.example`
  excepted).
- Binary content is scanned (`-a`/`--binary`); unparseable or truncated tool output **fails closed**
  (recorded as `content withheld`, exit ≥2), never a silent pass. The matched text is never echoed.
- Prefers `rg` for worktree mode, falls back to `grep -REn`.
- The allowlist is minimal and **per-pattern**: for the three known test-fixture paths only the
  URL pattern is suppressed (token patterns still fire everywhere); `.env.example` is exempt by path.
- Exit 1 with `file:line: <label>` findings and a "why this blocks" message; exit 0 clean; exit 2 for a
  usage/scanner error (the hooks treat ≥2 as a block too).

### 4. Hook + CI wiring
- `scripts/pre-commit.sh`: run `secret-scan.sh --staged` **before** the docs-only early-exit, so
  *no* kind of commit can introduce a secret.
- `scripts/pre-push.sh`: run `secret-scan.sh --tracked` before `make check`.
- `.github/workflows/ci.yml`: new `secrets` job — `actions/checkout` with `fetch-depth: 0` +
  `gitleaks/gitleaks-action` pinned by SHA (full-history scan). Keeps CI authoritative for history.
- `Makefile`: add `secrets` target (`./scripts/secret-scan.sh --tracked`) for manual runs.

### 5. Policy docs
- New `SECURITY.md`: reporting process + "Secret hygiene — never commit secrets" (generate keys
  with `secrets.token_urlsafe`, store in env/secret manager, `.env` is gitignored, rotate on
  exposure, the scanner runs on commit/push/CI and why not to `--no-verify`).
- `README.md`: short "Security" section linking `SECURITY.md` + noting the guard.
- `AGENTS.md`: one-line rule under §4 (or a new "Secret hygiene" bullet) making it a project law.

## Tasks & acceptance criteria

### T1 — `django-backend`: prod fail-closed (G1, G2)
Files: `amalthea/settings/prod.py`, `tests/conformance/test_prod_settings.py`.
- **AC1.1** `settings.prod` raises `ImproperlyConfigured` for missing, placeholder, or <50-char
  `DJANGO_SECRET_KEY`.
- **AC1.2** raises for empty `DJANGO_ALLOWED_HOSTS`.
- **AC1.3** a valid key + host imports and sets `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/
  `SECURE_SSL_REDIRECT`/`SECURE_HSTS_*`/`SECURE_CONTENT_TYPE_NOSNIFF`/`X_FRAME_OPTIONS`.
- **AC1.4** `test.py`/`test_pg.py`/`dev.py` unaffected; `make check` green.

### T2 — `security-auditor`: scanner + wiring + policy (G3, G4)
Files: `scripts/secret-scan.sh`, `scripts/pre-commit.sh`, `scripts/pre-push.sh`,
`.github/workflows/ci.yml`, `Makefile`, `SECURITY.md`, `README.md`, `AGENTS.md`.
- **AC2.1** `secret-scan.sh --staged` flags a staged file containing a `github_pat_…`; pre-commit
  invokes it even for docs-only commits.
- **AC2.2** `--tracked` and `--history` modes work; clean tree exits 0.
- **AC2.3** CI has a full-history secret scan (checkout `fetch-depth: 0`).
- **AC2.4** `SECURITY.md` documents the policy + reporting; README + AGENTS.md reference it.

### T3 — `planner`: records
Files: `docs/spec/deviations.md` (F9 → fixed), `docs/spec/identity-auth.md`,
`docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §13 (new Phase 13 P13-*),
`TODO.md`, `COMPLETED.md`.
- **AC3.1** F9 no longer listed as deferred; replaced with a "fixed in 2026-10-08 hardening" row.
- **AC3.2** master-plan §13 Phase 13 records the fix + guard; COMPLETED/TODO updated.

### T5 — `security-auditor`: harden after adversarial review
A second, independent security-auditor session reproduced real bypasses in the T2 scanner; all were
fixed and are now regression tests (`tests/unit/test_secret_scan.py`, 20 tests):
- **AC5.1** `--staged`/`--tracked` scan git **objects** (index / `HEAD` tree), not the worktree —
  closes "stage then edit" and `git add -f .env && rm .env`.
- **AC5.2** fail-closed parsing: an unparseable/truncated record (e.g. a `:` in the path) is a
  finding, never a silent pass; a scanner error exits ≥2 and the hooks block on it.
- **AC5.3** `--history` scans binary blobs (`-a`) **and** commit + annotated-tag messages.
- **AC5.4** CI workflow triggers on **every** branch/tag, not only `main`.
- **AC5.5** filename guard + `.gitignore` cover `*.env`, `*.env.*`, `*.envrc` (`.env.example` allowed).

### T4 — verification
- **AC4.1** `verifier` evaluates AC1–AC3 against the diff; `security-auditor` reviews the guard for
  bypasses/false-negatives (this produced the T5 hardening).
- **AC4.2** Full gate green on the final tree (**763 passed / 3 skipped**; Postgres/coverage gates as
  before).

## Security notes
- The scanner is defence-in-depth, not a guarantee; the CI history scan is the backstop for
  anything that slips a local hook (`--no-verify`, a fresh clone without hooks installed).
- `SECRET_KEY` length floor (50) matches Django's entropy expectations; the message tells operators
  exactly how to generate one.
- No secret values are committed by this plan; the scanner patterns themselves are not secrets.

## Risks
- **R1** HSTS/SSL-redirect in prod can lock out a plaintext-only deployment. Mitigation: documented
  in `SECURITY.md`; operators terminate TLS at the proxy and set `SECURE_PROXY_SSL_HEADER` if
  needed (not auto-set here to avoid trusting spoofable headers silently).
- **R2** False positives break commits. Mitigation: allowlist for known benign fixtures; patterns
  are high-specificity, not generic entropy.
- **R3** `gitleaks-action` is third-party. Mitigation: pin by SHA; local script is dependency-free.

## Open questions (resolved)
- Credential persistence: **no** (user decision).
- Repo visibility: stays **public** (user decision) — hence this hardening wave.