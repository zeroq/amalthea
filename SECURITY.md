# Security

Amalthea is a **public** repository (`github.com/zeroq/amalthea`). This document explains how to
report vulnerabilities and the project's non-negotiable rules for keeping credentials out of git.

## Reporting a vulnerability

Please do **not** open a public issue, pull request, or discussion for an exploitable finding.

- Prefer GitHub's private disclosure channel: **Security → Report a vulnerability** on the
  repository page (a private security advisory works as well).
- If neither is available, contact a maintainer directly and ask for a private channel.
- Include the affected component, reproduction steps, impact, and whether any real credential may
  have been exposed. We acknowledge receipt as soon as we can and coordinate a fix before any
  public disclosure.

## Secret hygiene — never commit secrets

**No API keys, tokens, passwords, private keys, or `.env` files may ever be committed** — not in
code, tests, fixtures, CI configuration, documentation, or commit messages. The repository is
public: anything in git history is world-readable within seconds of a push.

The rules:

- Real configuration lives in **environment variables**. Local development reads them from `.env`,
  which is gitignored. `.env.example` is the only committed dotenv file, and it holds placeholders
  only.
- Generate strong values with Python's standard library:

  ```bash
  python -c 'import secrets; print(secrets.token_urlsafe(64))'
  ```

- **If a secret ever lands in history: ROTATE it first** — assume it is compromised the moment the
  commit exists, regardless of whether it was pushed. Deleting the file is **not** enough: git
  keeps the blob in history, and history is what the world sees. After rotating, purge the history
  (for example with `git filter-repo`) and coordinate the rewrite with the other maintainers.
- Never use `git commit --no-verify` (or `git push --no-verify`) to skip the secret scan. It only
  disables *your local* hooks — it does not make the secret safe, it just silences the one warning
  that could still stop it before it ships. CI scans the full history anyway, so the build fails
  after the commit already exists instead of before it.

## Enforcement

`scripts/secret-scan.sh` is a deterministic, dependency-free scanner (git + bash, using ripgrep
with a `grep -REn` fallback). It uses a fixed, high-specificity pattern table — GitHub, AWS, Slack,
Google and Stripe token shapes, PEM private-key blocks, credential-bearing URLs, plus a filename
guard for dotenv and key files — and it runs on every surface where code moves:

Where it runs (it scans **git objects**, not the working tree, so a staged-then-edited secret or
`git add -f .env && rm .env` is still caught):

| Surface | Invocation | Scope |
| --- | --- | --- |
| Pre-commit hook | `secret-scan.sh --staged` | the git **index**, on every commit including docs-only ones |
| Pre-push hook | `secret-scan.sh --tracked` | the committed **`HEAD` tree** |
| CI `secrets` job | `secret-scan.sh --history` + gitleaks | every **blob, commit message and tag message** in history; runs on every branch and tag |

- The scan is fail-closed: a scanner/tool error (exit ≥2) also blocks the commit/push.
- Install the hooks once per clone: `make hooks`.
- Run the scan manually: `make secrets`, or
  `./scripts/secret-scan.sh --staged | --tracked | --history | --files …`.
- CI is the backstop: a fresh clone without hooks, or a push made with `--no-verify`, still hits the
  full-history scan on GitHub (on any branch — the workflow no longer triggers only on `main`).
- The allowlist is minimal and **per-pattern** (for three known test fixtures only the URL pattern is
  suppressed; token patterns fire everywhere). Treat any addition as a security-relevant change in
  review.

If the scan blocks you, the fix is always the same: remove the secret, **rotate** it at the
provider, and keep the replacement in an environment variable or a local, gitignored `.env`.

## Production configuration

`amalthea/settings/prod.py` **fails closed**: it refuses to import unless `DJANGO_SECRET_KEY` is
set to a strong, unique value (at least 50 characters) and `DJANGO_ALLOWED_HOSTS` is non-empty.
A deployment that forgets to configure the key stops with `ImproperlyConfigured` instead of
quietly running on the development placeholder.

- Generate the key with the `secrets.token_urlsafe` command above and inject it through your
  environment or secret manager — never write it to a file inside the repository.
- Production settings also force cookie and transport hardening (secure cookies, HSTS, SSL
  redirect, deny-by-default framing).
- `.env` is listed in `.gitignore`; the scanner additionally blocks dotenv and key-file *names*
  (`.env`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519`) whatever their content.
