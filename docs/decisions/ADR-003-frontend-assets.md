# ADR-003: Frontend Asset Delivery

**Status:** Accepted
**Date:** 2026-10-09
**Owner:** planner

## Context

Amalthea's AGENTS.md §1/§6 specifies a frontend stack of:
- Django templates (server-rendered)
- HTMX for partial interactivity
- Tailwind CSS for styling
- Font Awesome Free for icons
- Channels WebSockets for live updates
- Dark theme, keyboard-driven, Linear/Obsidian-inspired, TheHive-aligned

The repo previously shipped:
- Hand-written CSS (`ui/static/ui/app.css`)
- No HTMX library (attributes present but inert)
- No Font Awesome
- No Tailwind (CSS was hand-written)

## Decision

**All frontend assets are self-hosted (no CDN at runtime).**

1. **HTMX** — vendored as `ui/static/vendor/htmx/htmx.min.js` (pinned version, integrity optional)
2. **Tailwind CSS** — compiled at build time via standalone CLI binary (`scripts/tailwindcss`), compiled artifact committed to `ui/static/ui/app.css`. No Node.js toolchain required at runtime or in CI.
3. **Font Awesome Free** — subset of webfonts (`.woff2`) + minimal CSS subset committed under `ui/static/vendor/fontawesome/`. Only icons actually used are included.
4. **CSP** — strict `Content-Security-Policy` allowing only `'self'` for scripts/styles/fonts. No `unsafe-inline`, no `unsafe-eval`, no external origins.
4. **Build pipeline** — `make css` compiles Tailwind; `make css-check` verifies compilation succeeds; both run in CI. No Node.js in CI pipeline.

## Rationale

- **Offline-capable / air-gapped deployments** — no external network requests at runtime, compatible with air-gapped SOC environments.
- **CSP simplicity** — `'self'` only policy is trivial to reason about; no hash/nonces for inline scripts needed because we have zero inline scripts/styles.
- **Supply chain integrity** — pinned versions, committed artifacts, no runtime CDN fetches that could be compromised.
- **Deterministic builds** — `make css` produces identical output; `make css-check` in CI prevents drift.
- **License compliance** — Font Awesome Free (CC BY 4.0 icons / SIL OFL 1.1 fonts) requires attribution; self-hosting makes this trivial via `THIRD-PARTY.md`.
- **No Node.js dependency** — Tailwind standalone binary + `scripts/tailwindcss` keeps the toolchain minimal (single binary, no `package.json`, no `node_modules`).

## Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| CDN for HTMX/FA/Tailwind | Zero build step, always latest | Fails in air-gapped envs; CSP complexity; supply chain risk; runtime dependency on external infra |
| Full Node.js + npm + Tailwind | Full Tailwind features, ecosystem | Heavy toolchain; `node_modules` bloat; CI complexity; supply chain surface |
| Keep hand-written CSS | Zero build step | No utility classes; manual maintenance; no design token system; inconsistent with AGENTS.md spec |
| Tailwind v3 + Node.js | Mature ecosystem | Same Node.js cons; v4 standalone binary removes need |

## Consequences

- **Added files**: `scripts/tailwindcss` (binary), `ui/static/vendor/htmx/htmx.min.js`, `ui/static/vendor/fontawesome/webfonts/*.woff2`, `ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`, `tailwind.config.js`, `THIRD-PARTY.md`, `docs/decisions/ADR-003-frontend-assets.md`.
- **Modified files**: `ui/templates/ui/base.html` (asset includes), `Makefile` (`css`/`css-check` targets), `amalthea/settings/prod.py` (CSP), `ui/static/ui/app.css` (Tailwind source).
- **CI** runs `make css-check` as part of `make check`.
- **Developers** run `make css` after modifying `ui/static/ui/app.css` (Tailwind source).
- **No Node.js** in repo, CI, or production.

## Verification

- `make css` compiles cleanly
- `make css-check` passes in CI (`make check`)
- CSP headers present in prod (`scripts/secret-scan.sh --tracked` clean)
- `make check` passes (ruff, mypy, tests, migrations)
- Font Awesome license recorded in `THIRD-PARTY.md`

## References

- AGENTS.md §1, §6 (design spec)
- `docs/spec/README.md` (drift policy)
- `docs/planning/PLAN-2026-10-09-usability-orchestration.md` §4.1
- `THIRD-PARTY.md` (license register)